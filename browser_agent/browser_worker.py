"""浏览器任务独立子进程 CLI 入口。

stdin：一次性 JSON（含会话级临时凭证，绝不写命令行、绝不落盘）
stdout：仅一行 LM_RESULT_JSON:{...}；其余输出全部重定向到 stderr
"""

# ============================================================
# 【核心模块】浏览器子进程入口
# 作用：stdin 收一次性 JSON job（含临时凭证）→ 调 runner → stdout 只回一行 LM_RESULT_JSON；不设 RLIMIT_AS（会杀 Servo）
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import paths                      # noqa: E402
from core.logger import redact              # noqa: E402

paths.setup_browserr_env()                   # 子进程同样锁定项目内 Browserr 引擎
from browser_agent.browser_runner import run_browser_job  # noqa: E402

RESULT_PREFIX = "LM_RESULT_JSON:"


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        job = json.loads(raw)
    except Exception as exc:
        sys.stdout.write(RESULT_PREFIX + json.dumps(
            {"ok": False, "error": f"参数解析失败：{exc}"}, ensure_ascii=False) + "\n")
        return 2

    # Browserr/Servo 不适用 RLIMIT_AS：实测其虚拟地址空间需求 >4GB（8192MB 才可启动，
    # 512/2048/4096MB 均导致引擎无应答），而实际 RSS 只有数百 MB。Chromium 时代的
    # 512MB 地址空间上限会直接杀死引擎，故浏览器子进程不设此限，内存改靠全局并发
    # 上限 + 硬件阈值降级控制（与 Windows 原本无 RLIMIT_AS 的策略一致）。
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = run_browser_job(job)
        except Exception as exc:
            result = {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:300]}
    sys.stdout.write(RESULT_PREFIX + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
