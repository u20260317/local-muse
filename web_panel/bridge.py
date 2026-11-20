"""Flask 同步线程 → asyncio 调度 loop 的线程安全桥。

不引入 Quart/FastAPI 等异步 Web 框架，用 run_coroutine_threadsafe
把提交/取消/快照请求投递到调度 loop，避免跨线程直接操作事件循环对象。
"""

# ============================================================
# 【核心模块】面板层 · 同步-异步桥
# 作用：用 run_coroutine_threadsafe 把 Flask 线程的提交/取消/快照投递到调度事件循环
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio


class SchedulerBridge:
    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self.scheduler, "loop", None)
        if loop is None or loop.is_closed():
            raise RuntimeError("调度器尚未就绪，请稍后重试")
        return loop

    def _call(self, coroutine, timeout: float = 5.0):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop())
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def submit(self, spec) -> str:
        return self._call(self.scheduler.submit(spec))

    def cancel(self, task_id: str) -> bool:
        return self._call(self.scheduler.cancel(task_id), timeout=8.0)

    def active_snapshot(self) -> list[dict]:
        return self.scheduler.active_snapshot()

    def queue_snapshot(self) -> list[dict]:
        return self.scheduler.queue_snapshot()

    def snapshot(self) -> dict:
        return self.scheduler.snapshot()

    def degraded(self) -> bool:
        return self.scheduler.degraded()
