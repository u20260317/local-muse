"""轻量子进程隔离包装（不使用 Docker / 任何容器虚拟化）。

- 每条浏览器任务、每条生成任务各启动一个独立 Python 子进程
- 参数与临时凭证经 stdin 传入（不出现在命令行 / 进程列表 / 日志）
- stdout 只回传一行 JSON 结果；超时强杀整棵进程树
- 单任务崩溃只影响自己，主程序与其他并行任务继续运行
"""

# ============================================================
# 【核心模块】隔离层 · 子进程包装
# 作用：每类业务各起独立 Python 子进程，参数走 stdin 不走命令行；超时强杀整棵进程树
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from core import paths
from core.logger import get_logger, redact

LOG = get_logger("sandbox")
RESULT_PREFIX = "LM_RESULT_JSON:"

WORKERS: dict[str, Path] = {
    "browser": paths.ROOT / "browser_agent" / "browser_worker.py",
    "media": paths.ROOT / "media_generator" / "media_worker.py",
}


async def run_worker(kind: str, payload: dict, timeout_s: int, task_id: str,
                     on_pid=None) -> dict:
    """启动独立子进程并返回结构化结果；永不抛出异常，失败以 dict 形式返回。"""
    script = WORKERS.get(kind)
    if script is None or not script.exists():
        return {"ok": False, "error": f"未找到 {kind} 子进程入口：{script}"}

    env = os.environ.copy()
    env["PYTHONPATH"] = str(paths.ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env, cwd=str(paths.ROOT),
            **paths.subprocess_options(),
        )
        if on_pid:
            try:
                on_pid(proc.pid)
            except Exception:
                pass
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdout, stderr = await asyncio.wait_for(proc.communicate(data), timeout=max(5, timeout_s))
    except asyncio.TimeoutError:
        if proc:
            paths.kill_process_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
        LOG.warning("子进程超时被强杀 task=%s kind=%s timeout=%ss", task_id, kind, timeout_s)
        return {"ok": False, "error": f"子进程超时（{timeout_s}s）已被强杀", "timeout": True}
    except asyncio.CancelledError:
        if proc:
            paths.kill_process_tree(proc.pid)
        raise
    except Exception as exc:
        LOG.error("子进程启动失败 task=%s kind=%s：%s", task_id, kind, exc)
        return {"ok": False, "error": f"子进程启动失败：{redact(str(exc))[:200]}"}

    text = (stdout or b"").decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if not lines:
        err_text = redact((stderr or b"").decode("utf-8", errors="replace"))[-400:]
        return {"ok": False, "error": f"子进程未返回结果（exit={proc.returncode}）：{err_text}",
                "exit_code": proc.returncode}
    try:
        out = json.loads(lines[-1][len(RESULT_PREFIX):])
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"子进程结果解析失败：{exc}", "exit_code": proc.returncode}
    if not isinstance(out, dict):
        return {"ok": False, "error": "子进程返回格式非法"}
    out.setdefault("ok", False)
    out["exit_code"] = proc.returncode
    out["pid"] = proc.pid
    if proc.returncode not in (0, None) and out.get("ok"):
        out["ok"] = False
        out.setdefault("error", f"子进程退出码 {proc.returncode}")
    if not out.get("ok"):
        LOG.warning("子进程返回失败 task=%s kind=%s：%s", task_id, kind,
                    redact(str(out.get("error", "")))[:200])
    return out
