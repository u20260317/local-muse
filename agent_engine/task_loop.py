"""单任务流水线编排（每个 task_id 独立执行，互不共享状态）。

plan → 浏览器采集(可选) → 文生图 → 文生视频/图生视频 → 上传(可选) → 落库 → 独立复盘
业务 I/O 全部在独立子进程中完成；重试与超时由 Scheduler 统一负责。
"""
from __future__ import annotations

import time

from core import paths
from core.logger import get_logger, bind, set_stage
from agent_engine import planner
from browser_agent import sandbox_wrapper
from media_generator import image_gen, video_gen

LOG = get_logger("task_loop")


def _report(services: dict, runtime, stage: str, progress: int, message: str = "") -> None:
    """上报阶段进度：运行时状态 + SQLite + 带 task_id 的日志，三处同步。"""
    runtime.report(stage, progress, message)
    set_stage(stage)
    services["on_report"](runtime)
    LOG.info("阶段推进 [%s] progress=%s%% %s", stage, progress, message)


def _pid_hook(services: dict, runtime, task_id: str):
    """子进程启动回调：登记 PID 以便面板显示资源占用与单独终止。"""
    def hook(pid: int) -> None:
        runtime.pid = int(pid)
        services["register_pid"](task_id, int(pid))
        services["on_report"](runtime)
    return hook


async def execute(runtime, services: dict) -> dict:
    """执行一条任务的完整工作流；异常向上抛给 Scheduler 做重试。"""
    spec = runtime.spec
    task_id = spec.task_id
    bind(task_id, "plan")
    config = services["config"]
    browser_cfg = config.section("browser")
    sched_cfg = config.section("scheduler")
    timeout_s = int(browser_cfg.get("timeout_seconds", 60))
    result: dict = {
        "task_id": task_id, "simulated": spec.dry_run,
        "started_at": time.time(), "assets": [], "materials": [], "stages": [],
    }
    credential_issued = False

    try:
        # 1) 决策：加载 Skill、参考历史、生成/迭代 prompt
        _report(services, runtime, "plan", 10, "加载技能与历史复盘，生成任务计划")
        plan = await planner.build_plan(services, spec)
        result["plan"] = {
            "topic": plan.get("topic", ""), "style": plan.get("style", ""),
            "source": plan.get("source", ""), "provider": plan.get("provider", ""),
            "prompt_preview": plan.get("prompt", "")[:200],
        }
        result["stages"].append("plan")
        runtime.provider = plan.get("provider", "")

        # 2) 浏览器采集：独立 Chromium 子进程 + 会话级临时凭证（仅注入隔离实例）
        if plan.get("needs_browser") and browser_cfg.get("enabled", True):
            if spec.dry_run:
                credential = {"session_id": "dry-run", "cookies": [], "headers": {}, "storage": {}}
            else:
                cards = [c["name"] for c in services["vault"].list_cards() if c.get("enabled", True)]
                credential = services["vault"].issue_session_credential(
                    task_id, cards, services["engine_token"])
                credential_issued = True
            _report(services, runtime, "browser", 25, "启动独立 Playwright Chromium 子进程")
            browser_payload = {
                "task_id": task_id,
                "dry_run": spec.dry_run,
                "headless": bool(browser_cfg.get("headless", True)),
                "timeout_seconds": timeout_s,
                "navigation_timeout_ms": int(browser_cfg.get("navigation_timeout_ms", 30000)),
                "memory_limit_mb": int(sched_cfg.get("subprocess_memory_limit_mb", 512)),
                "user_data_dir": str(paths.session_dir(task_id)),
                "extra_args": list(browser_cfg.get("extra_args", [])),
                "credentials": credential,
                "actions": [
                    {"type": "goto", "url": "https://example.com"},
                    {"type": "extract", "selector": "body"},
                    {"type": "screenshot", "name": "research.png"},
                ],
            }
            browser_out = await sandbox_wrapper.run_worker(
                "browser", browser_payload, timeout_s + 15, task_id,
                on_pid=_pid_hook(services, runtime, task_id))
            if not browser_out.get("ok"):
                raise RuntimeError(f"浏览器采集失败：{browser_out.get('error', '未知错误')[:160]}")
            result["materials"] = browser_out.get("materials", [])
            result["browser_session"] = browser_out.get("session", "")
            result["stages"].append("browser")
        else:
            result["stages"].append("browser(skipped)")

        # 3) 文生图：主进程限流选源，子进程发请求与下载校验
        _report(services, runtime, "image", 50, "从 API 池挑选文生图源并下载校验")
        image = await image_gen.generate(services, task_id, plan["prompt"], spec.dry_run,
                                         on_pid=_pid_hook(services, runtime, task_id))
        result["assets"].append(image)
        result["stages"].append("image")
        if image.get("simulated"):
            runtime.simulated = True
        runtime.provider = image.get("provider") or runtime.provider

        # 4) 文生/图生视频：参考图来自上一步产物
        _report(services, runtime, "video", 72, "调用视频生成源（支持参考图输入）")
        video = await video_gen.generate(
            services, task_id, plan["prompt"],
            reference_image=image.get("path"), dry_run=spec.dry_run,
            on_pid=_pid_hook(services, runtime, task_id))
        result["assets"].append(video)
        result["stages"].append("video")
        if video.get("simulated"):
            runtime.simulated = True
        runtime.provider = video.get("provider") or runtime.provider

        # 5) 可选上传：默认关闭，只由决策引擎控制
        upload_cfg = config.section("upload")
        if upload_cfg.get("enabled"):
            _report(services, runtime, "upload", 86, "上传适配器未配置，跳过")
        result["upload"] = {"skipped": True,
                            "reason": "默认关闭" if not upload_cfg.get("enabled") else "未配置上传适配器"}
        result["stages"].append("upload(skipped)")

        # 6) 按 task_id 独立落库
        _report(services, runtime, "persist", 92, "写入任务结果与产物索引")
        services["db"].update_task(task_id, result_json=result)

        # 7) 独立复盘：prompt / 风格 / 选题迭代，不与其他任务共享
        _report(services, runtime, "reflect", 97, "读取历史结果，生成下一轮优化建议")
        result["reflection"] = await planner.reflect(services, runtime, plan, result,
                                                     succeeded=True)
        result["stages"].append("reflect")

        _report(services, runtime, "done", 100, "全部阶段完成")
        result["finished_at"] = time.time()
        result["simulated"] = bool(runtime.simulated)
        return result
    except Exception as exc:
        LOG.error("任务流水线中断 task=%s stage=%s：%s", task_id, runtime.stage, str(exc)[:200])
        raise
    finally:
        # 凭证会话随任务结束立即销毁；任何情况下都不留给下一个任务
        if credential_issued:
            services["vault"].revoke(task_id)
        services["unregister_pid"](task_id, runtime.pid)
