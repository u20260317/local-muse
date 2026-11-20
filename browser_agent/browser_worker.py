"""浏览器任务独立子进程 CLI 入口。

stdin：一次性 JSON（含会话级临时凭证，绝不写命令行、绝不落盘）
stdout：仅一行 LM_RESULT_JSON:{...}；其余输出全部重定向到 stderr
"""
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

paths.setup_playwright_env()                 # 子进程同样锁定项目内独立 Chromium
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

    paths.apply_worker_memory_limit(int(job.get("memory_limit_mb", 0)))
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
