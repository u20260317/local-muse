"""并发调度中枢（板块 1 核心）。

设计铁则：
- 【调度主循环】只做出队、并发判定、create_task 分发与状态管理，绝不 await 业务子任务；
  因此调度器随时能接收新指令，不需要等上一条任务跑完。
- 【业务子任务】在独立 asyncio.Task 中运行，每任务独立超时、独立重试、独立子进程、独立取消句柄。
- 硬件超阈值时只暂停「从队列取新任务」，已在跑的任务不受影响，资源回落自动恢复。
"""

# ============================================================
# 【核心模块】调度中枢 · 并发分发与重试
# 作用：PriorityQueue + 只分发不等待的主循环；独立超时/重试/取消、硬件超阈值暂停派发、定时任务注入
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio
import itertools
import threading
import time

import schedule

from core import paths
from core.logger import get_logger, bind
from core.models import TaskPriority, TaskRuntime, TaskSpec, TaskStatus
from agent_engine.skill_loader import SkillLoader
from agent_engine import task_loop

LOG = get_logger("scheduler")


class Scheduler:
    def __init__(self, config, db, hardware, pool, vault) -> None:
        self.config = config
        self.db = db
        self.hardware = hardware
        self.pool = pool
        self.vault = vault
        self.skill_loader = SkillLoader()

        self.queue: asyncio.PriorityQueue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.active: dict[str, TaskRuntime] = {}          # 运行中的任务
        self._queued: dict[str, tuple[int, int, TaskSpec]] = {}
        self._cancelled: set[str] = set()
        self._handles: dict[str, asyncio.Task] = {}
        self._pids: dict[str, set[int]] = {}              # task_id -> 子进程 PID 集合
        self._bg_tasks: list[asyncio.Task] = []
        self._stop_event: asyncio.Event | None = None
        self._seq = itertools.count()
        self._sched_index = 0
        self._degraded = False
        self._hardware_gate = True                        # 自检模式可临时关闭
        self._lock = threading.RLock()                    # 保护跨线程读取的快照
        self._services = {
            "config": config, "db": db, "pool": pool, "vault": vault,
            "hardware": hardware, "skill_loader": self.skill_loader,
            "engine_token": vault.engine_token(),       # 仅决策引擎持有
            "register_pid": self.register_pid,
            "unregister_pid": self.unregister_pid,
            "on_report": self._persist,
        }

    # ---------- 生命周期 ----------
    def set_hardware_gate(self, enabled: bool) -> None:
        """自检模式（不启动真实 Browserr、不联网）可临时关闭硬件降级门禁。

        正常运行时保持开启：CPU/内存超阈值即暂停派发新任务，保护 L430。
        """
        self._hardware_gate = bool(enabled)
        if not self._hardware_gate:
            self._degraded = False

    async def start(self, enable_schedule: bool = True) -> None:
        bind("-", "scheduler")
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.PriorityQueue()
        self._stop_event = asyncio.Event()
        schedule.clear()
        self._bg_tasks = [
            asyncio.create_task(self._dispatch_loop(), name="lm-dispatch"),
            asyncio.create_task(self._hardware_loop(), name="lm-hardware"),
        ]
        if enable_schedule:
            self._bg_tasks.append(asyncio.create_task(self._schedule_loop(), name="lm-schedule"))
        LOG.info("调度器启动：全局并发上限=%s 每日任务数=%s 定时间隔=%ss",
                 self._max_concurrency(),
                 self.config.section("scheduler").get("daily_task_limit", 10),
                 self.config.section("scheduler").get("interval_seconds", 300))

    async def stop(self) -> None:
        """停止调度：取消后台循环与全部业务任务，清理所有子进程树。"""
        if self._stop_event:
            self._stop_event.set()
        for task in self._bg_tasks:
            task.cancel()
        for task_id, handle in list(self._handles.items()):
            if not handle.done():
                handle.cancel()
        for task_id in list(self._pids):
            self._kill_pids(task_id)
        schedule.clear()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        LOG.info("调度器已停止，全部业务子进程已清理")

    # ---------- 提交 / 取消 ----------
    async def submit(self, spec: TaskSpec) -> str:
        """非阻塞入队，立即返回 task_id；插队模式使用负优先级排到队首。"""
        spec.seq = next(self._seq)
        effective = -1 if spec.enqueue_mode == "insert" else int(spec.priority)
        with self._lock:
            self._queued[spec.task_id] = (effective, spec.seq, spec)
        assert self.queue is not None
        await self.queue.put((effective, spec.seq, spec))
        self.db.upsert_task({
            "task_id": spec.task_id, "priority": int(spec.priority),
            "source": spec.source, "skill": spec.skill, "instruction": spec.instruction,
            "enqueue_mode": spec.enqueue_mode, "status": TaskStatus.QUEUED.value,
            "stage": "queued", "progress": 0, "dry_run": spec.dry_run,
            "created_at": spec.created_at, "result_json": "{}", "error": "",
            "conversation_id": spec.conversation_id,
        })
        LOG.info("任务入队 task=%s 优先级=%s 来源=%s 模式=%s dry_run=%s",
                 spec.task_id, spec.priority.name, spec.source, spec.enqueue_mode, spec.dry_run)
        return spec.task_id

    async def cancel(self, task_id: str) -> bool:
        """只终止指定 task_id：取消句柄 + 杀该任务的进程树 + 释放凭证会话，不影响其它并发任务。"""
        with self._lock:
            known = task_id in self.active or task_id in self._queued
            if not known:
                return False
            self._cancelled.add(task_id)
            self._queued.pop(task_id, None)
            runtime = self.active.get(task_id)
            handle = self._handles.get(task_id)
        self._kill_pids(task_id)
        self.vault.revoke(task_id)
        if runtime:
            runtime.status = TaskStatus.CANCELLED
            runtime.message = "由面板单独终止"
            self._persist(runtime)
        if handle and not handle.done():
            handle.cancel()
        self.db.update_task(task_id, status=TaskStatus.CANCELLED.value,
                            error="由面板单独终止", finished_at=time.time())
        LOG.warning("任务已单独终止 task=%s（其它并行任务不受影响）", task_id)
        return True

    # ---------- 子进程登记 ----------
    def register_pid(self, task_id: str, pid: int) -> None:
        with self._lock:
            self._pids.setdefault(task_id, set()).add(int(pid))

    def unregister_pid(self, task_id: str, pid: int | None = None) -> None:
        with self._lock:
            if pid is None:
                self._pids.pop(task_id, None)
            else:
                self._pids.get(task_id, set()).discard(int(pid))

    def _kill_pids(self, task_id: str) -> None:
        with self._lock:
            pids = set(self._pids.pop(task_id, set()))
        for pid in pids:
            if paths.kill_process_tree(pid):
                LOG.warning("已终止 task=%s 的子进程树 pid=%s", task_id, pid)

    # ---------- 调度主循环 ----------
    async def _dispatch_loop(self) -> None:
        """只分发不等待：有空位就出队并 create_task，然后立刻进入下一轮。"""
        assert self._stop_event is not None and self.queue is not None
        while not self._stop_event.is_set():
            try:
                if self._degraded or len(self.active) >= self._max_concurrency():
                    await asyncio.sleep(0.25)
                    continue
                try:
                    _priority, _seq, spec = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.25)
                    continue
                if spec.task_id in self._cancelled:
                    self._cancelled.discard(spec.task_id)
                    continue
                with self._lock:
                    self._queued.pop(spec.task_id, None)
                    runtime = TaskRuntime(
                        spec=spec, status=TaskStatus.RUNNING, stage="dispatch",
                        progress=5, started_at=time.time(),
                        timeout_s=self._timeout_seconds())
                    self.active[spec.task_id] = runtime
                self._persist(runtime)
                handle = asyncio.create_task(self._run_task(runtime),
                                             name=f"lm-task-{spec.task_id}")
                with self._lock:
                    self._handles[spec.task_id] = handle
                handle.add_done_callback(
                    lambda done, tid=spec.task_id: self._on_task_done(tid, done))
                LOG.info("任务分发 task=%s 当前并发=%s/%s", spec.task_id,
                         len(self.active), self._max_concurrency())
            except asyncio.CancelledError:
                break
            except Exception as exc:
                LOG.error("调度主循环异常（已自愈继续运行）：%s", exc)
                await asyncio.sleep(0.5)

    async def _run_task(self, runtime: TaskRuntime) -> None:
        """业务子任务：独立超时、独立重试、最终状态独立落库。"""
        bind(runtime.spec.task_id, "plan")
        retry_times = int(self.config.section("scheduler").get("retry_times", 2))
        for attempt in range(retry_times + 1):
            runtime.retry = attempt
            try:
                result = await asyncio.wait_for(
                    task_loop.execute(runtime, self._services), timeout=runtime.timeout_s)
                runtime.status = TaskStatus.SUCCEEDED
                runtime.progress = 100
                runtime.stage = "done"
                runtime.finished_at = time.time()
                runtime.result = result
                runtime.simulated = bool(result.get("simulated"))
                self._persist(runtime, result_json=result)
                LOG.info("任务完成 task=%s 耗时=%.1fs 重试=%s 模拟=%s",
                         runtime.spec.task_id, time.time() - runtime.started_at,
                         attempt, runtime.simulated)
                return
            except asyncio.CancelledError:
                runtime.status = TaskStatus.CANCELLED
                runtime.message = "任务被取消"
                runtime.finished_at = time.time()
                self._kill_pids(runtime.spec.task_id)
                self._persist(runtime)
                return
            except asyncio.TimeoutError as exc:
                self._handle_failure(runtime, attempt, retry_times,
                                     TaskStatus.TIMEOUT, f"任务超时（{runtime.timeout_s}s）：{exc}")
            except Exception as exc:
                self._handle_failure(runtime, attempt, retry_times,
                                     TaskStatus.FAILED, f"{type(exc).__name__}: {exc}")
            if attempt < retry_times:
                await asyncio.sleep(min(2 ** attempt, 5))
        runtime.finished_at = time.time()
        self._persist(runtime)
        LOG.error("任务最终失败 task=%s 状态=%s 原因=%s",
                  runtime.spec.task_id, runtime.status.value, runtime.message[:160])

    def _handle_failure(self, runtime: TaskRuntime, attempt: int,
                        retry_times: int, final_status: TaskStatus, message: str) -> None:
        runtime.status = TaskStatus.RETRYING if attempt < retry_times else final_status
        runtime.message = message[:300]
        runtime.stage = "retry" if attempt < retry_times else final_status.value
        self._kill_pids(runtime.spec.task_id)
        self._persist(runtime)
        LOG.warning("任务异常 task=%s 第 %s/%s 次：%s",
                    runtime.spec.task_id, attempt + 1, retry_times + 1, message[:160])

    def _on_task_done(self, task_id: str, handle: asyncio.Task) -> None:
        """释放并发名额：只清理该 task_id 的句柄、子进程与凭证会话。"""
        with self._lock:
            runtime = self.active.pop(task_id, None)
            self._handles.pop(task_id, None)
            self._pids.pop(task_id, None)
            self._cancelled.discard(task_id)
        self.vault.revoke(task_id)
        if runtime:
            runtime.finished_at = runtime.finished_at or time.time()
            self._persist(runtime)
        try:
            error = handle.exception()
            if error:
                LOG.error("子任务句柄异常 task=%s：%s", task_id, error)
        except asyncio.CancelledError:
            pass
        LOG.info("并发名额已释放 task=%s 剩余活跃=%s", task_id, len(self.active))

    # ---------- 硬件降级 / 定时触发 ----------
    async def _hardware_loop(self) -> None:
        assert self._stop_event is not None
        interval = max(1.0, float(self.config.section("hardware").get("sample_interval_seconds", 2)))
        while not self._stop_event.is_set():
            try:
                data = await asyncio.to_thread(self.hardware.update)
                # 门禁关闭时仍采样，但不阻塞自检任务派发，也不打印误导性的降级提示
                self._degraded = bool(data.get("degraded")) and self._hardware_gate
                if data.get("degraded") and not self._hardware_gate:
                    data["degraded_changed"] = False
                self.config.reload_if_changed()   # 面板改配置后热更新
                if data.get("degraded_changed"):
                    if self._degraded:
                        LOG.warning("硬件保护：%s", data.get("reason"))
                    else:
                        LOG.info("硬件保护：%s", data.get("reason"))
            except Exception as exc:
                LOG.warning("硬件采样失败（不影响调度）：%s", exc)
            await asyncio.sleep(interval)

    async def _schedule_loop(self) -> None:
        assert self._stop_event is not None
        interval = max(30, int(self.config.section("scheduler").get("interval_seconds", 300)))
        schedule.every(interval).seconds.do(self._on_schedule_tick)
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(schedule.run_pending)
            except Exception as exc:
                LOG.warning("定时检查失败：%s", exc)
            await asyncio.sleep(1)

    def _on_schedule_tick(self) -> None:
        """schedule 回调运行在线程中，用线程安全方式把任务提交回调度 loop。"""
        if self.loop and not self._stop_event.is_set():
            asyncio.run_coroutine_threadsafe(self._enqueue_scheduled(), self.loop)

    async def _enqueue_scheduled(self) -> None:
        limit = int(self.config.section("scheduler").get("daily_task_limit", 10))
        if self.db.count_today("scheduled") >= limit:
            LOG.info("今日定时任务已达上限 %s，跳过本轮", limit)
            return
        names = self.skill_loader.enabled_names() or [self.skill_loader.default_skill()]
        skill = names[self._sched_index % len(names)] if names else ""
        self._sched_index += 1
        spec = TaskSpec.create(
            priority=TaskPriority.SCHEDULED, source="scheduled", enqueue_mode="queue",
            skill=skill, instruction=f"定时常规任务 · {time.strftime('%Y-%m-%d %H:%M')} · 自动选题")
        await self.submit(spec)

    # ---------- 快照 / 持久化 ----------
    def _max_concurrency(self) -> int:
        return max(1, int(self.config.section("scheduler").get("max_concurrency", 2)))

    def _timeout_seconds(self) -> int:
        return max(30, int(self.config.section("scheduler").get("task_timeout_seconds", 600)))

    def _persist(self, runtime: TaskRuntime, **extra) -> None:
        spec = runtime.spec
        record = {
            "task_id": spec.task_id, "priority": int(spec.priority), "source": spec.source,
            "skill": spec.skill, "instruction": spec.instruction,
            "enqueue_mode": spec.enqueue_mode, "status": runtime.status.value,
            "stage": runtime.stage, "progress": runtime.progress, "retry": runtime.retry,
            "dry_run": spec.dry_run, "simulated": runtime.simulated,
            "provider": runtime.provider, "created_at": spec.created_at,
            "started_at": runtime.started_at, "finished_at": runtime.finished_at,
            "error": runtime.message if runtime.status in
                     (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED, TaskStatus.RETRYING) else "",
            "pid": runtime.pid,
        }
        record.update(extra)
        try:
            self.db.upsert_task(record)
        except Exception as exc:
            LOG.error("任务落库失败 task=%s：%s", spec.task_id, exc)

    def active_snapshot(self) -> list[dict]:
        with self._lock:
            return [r.snapshot() for r in self.active.values()]

    def queue_snapshot(self) -> list[dict]:
        with self._lock:
            items = sorted(self._queued.values(), key=lambda x: (x[0], x[1]))
        return [{
            "task_id": spec.task_id, "priority": int(spec.priority),
            "effective_priority": priority, "source": spec.source, "skill": spec.skill,
            "instruction": spec.instruction[:100], "dry_run": spec.dry_run,
            "created_at": spec.created_at,
        } for priority, _seq, spec in items]

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queued)

    def degraded(self) -> bool:
        return bool(self._degraded)

    def snapshot(self) -> dict:
        return {
            "active": self.active_snapshot(),
            "queued": self.queue_snapshot(),
            "active_count": len(self.active),
            "queue_size": self.queue_size(),
            "max_concurrency": self._max_concurrency(),
            "degraded": self.degraded(),
            "hardware": self.hardware.snapshot(),
            "stats": self.db.stats(),
            "today": self.db.count_today(),
            "daily_limit": int(self.config.section("scheduler").get("daily_task_limit", 10)),
            "providers": self.pool.snapshot(),
        }
