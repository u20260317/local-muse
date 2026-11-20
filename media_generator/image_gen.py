"""文生图调度：主进程负责选源与限流，子进程负责发请求/下载/校验。

本模块不 import vault，也不接触任何登录凭证；prompt 中不含凭证信息。
"""
from __future__ import annotations

from browser_agent import sandbox_wrapper
from core.logger import get_logger

LOG = get_logger("image_gen")


def _register_pid(services: dict, task_id: str, out: dict) -> None:
    """登记子进程 PID，供面板展示资源占用与单独终止。"""
    pid = out.get("pid")
    if pid:
        try:
            services["register_pid"](task_id, int(pid))
        except Exception:
            pass


async def _run_generation(services: dict, kind: str, task_id: str, prompt: str,
                          dry_run: bool = False, reference_image: str | None = None,
                          on_pid=None) -> dict:
    """image / video 共用的生成实现：选源 → 配额 → 子进程 → 记录消耗。"""
    pool = services["pool"]
    provider = None if dry_run else await pool.pick(kind, exclude=set())

    if provider is None:
        # 无可用免费源或显式自检：仍走完整子进程链路，但产物明确标注「模拟」
        payload = {
            "kind": kind, "dry_run": True, "task_id": task_id,
            "prompt": prompt[:500], "provider": "dry_run",
            "simulated_reason": "无可用免费 API 源，或显式 dry_run 自检",
            "memory_limit_mb": int(services["config"].section("scheduler")
                                   .get("subprocess_memory_limit_mb", 512)),
        }
        out = await sandbox_wrapper.run_worker("media", payload, 60, task_id, on_pid=on_pid)
        if not out.get("ok"):
            raise RuntimeError(f"{kind} 自检子进程失败：{out.get('error', '')[:160]}")
        _register_pid(services, task_id, out)
        pool.note_usage("dry_run", kind, task_id, "simulated", 0,
                        "模拟产物，未调用公网 API")
        LOG.info("task=%s %s 使用模拟产物（未调用真实 API）", task_id, kind)
        return {
            "kind": kind, "path": out.get("path", ""), "provider": "dry_run",
            "simulated": True, "verify": out.get("verify", {}),
            "pid": out.get("pid"), "note": out.get("note", "模拟产物，不代表真实生成"),
        }

    conf = services["config"].provider(provider) or {}
    await pool.acquire(provider)
    try:
        request = pool.build_generation_request(kind, provider, prompt, task_id,
                                                reference_image=reference_image)
        timeout_s = int(conf.get("timeout_s", 120)) + 20
        out = await sandbox_wrapper.run_worker("media", request, timeout_s, task_id,
                                               on_pid=on_pid)
        if not out.get("ok"):
            if out.get("status") == 429:
                await pool.report_429(provider, 30)
            pool.note_usage(provider, kind, task_id, "error", 0,
                            str(out.get("error", ""))[:120])
            raise RuntimeError(f"{provider} 生成失败：{str(out.get('error', ''))[:160]}")
        _register_pid(services, task_id, out)
        pool.note_usage(provider, kind, task_id, "ok", 0,
                        str(out.get("path", ""))[:100])
        LOG.info("task=%s %s 生成完成 provider=%s path=%s",
                 task_id, kind, provider, str(out.get("path", ""))[-60:])
        return {
            "kind": kind, "path": out.get("path", ""), "provider": provider,
            "simulated": False, "verify": out.get("verify", {}),
            "pid": out.get("pid"), "url": out.get("url", ""),
        }
    finally:
        await pool.release(provider)


async def generate(services: dict, task_id: str, prompt: str,
                   dry_run: bool = False, reference_image: str | None = None,
                   on_pid=None) -> dict:
    """生成一张图片，返回 {kind, path, provider, simulated, verify, pid}。"""
    return await _run_generation(services, "image", task_id, prompt,
                                 dry_run=dry_run, reference_image=reference_image,
                                 on_pid=on_pid)
