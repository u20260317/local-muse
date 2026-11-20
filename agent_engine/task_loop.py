"""单任务流水线编排（每个 task_id 独立执行，互不共享状态）。

v1.1 起流水线改为插件驱动：七个阶段全部注册到 pipeline 注册表，本文件只负责
组装 ctx、按注册表顺序执行（含并行组）、收尾销毁凭证。阶段的启停/排序由
config.pipeline 控制，加新阶段见 docs/PLUGIN_GUIDE.md。
业务 I/O 全部在独立子进程中完成；重试与超时由 Scheduler 统一负责。
"""

# ============================================================
# 【核心模块】决策引擎 · 单任务流水线编排
# 作用：串起 plan→browser→image→video→upload→persist→reflect 七个阶段插件；凭证只在本任务内签发、结束即销毁
# 定位与接口：见 docs/PLUGIN_GUIDE.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio
import time

from core import paths
from core.logger import get_logger, bind, set_stage
from agent_engine import pipeline, planner
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


# ---------- 阶段插件（签名统一：async run(ctx) -> 要记入 stages 的标签或 None） ----------

async def _stage_plan(ctx: dict) -> str:
    services, runtime, spec = ctx["services"], ctx["runtime"], ctx["spec"]
    _report(services, runtime, "plan", 10, "加载技能与历史复盘，生成任务计划")
    plan = await planner.build_plan(services, spec)
    ctx["plan"] = plan
    ctx["result"]["plan"] = {
        "topic": plan.get("topic", ""), "style": plan.get("style", ""),
        "source": plan.get("source", ""), "provider": plan.get("provider", ""),
        "prompt_preview": plan.get("prompt", "")[:200],
    }
    runtime.provider = plan.get("provider", "")
    return "plan"


async def _stage_browser(ctx: dict) -> str:
    services, runtime, spec = ctx["services"], ctx["runtime"], ctx["spec"]
    plan = ctx["plan"]
    browser_cfg = ctx["browser_cfg"]
    if not (plan.get("needs_browser") and browser_cfg.get("enabled", True)):
        return "browser(skipped)"
    task_id = spec.task_id
    if spec.dry_run:
        credential = {"session_id": "dry-run", "cookies": [], "headers": {}, "storage": {}}
    else:
        cards = [c["name"] for c in services["vault"].list_cards() if c.get("enabled", True)]
        credential = services["vault"].issue_session_credential(
            task_id, cards, services["engine_token"])
        ctx["credential_issued"] = True
    _report(services, runtime, "browser", 25, "启动独立 Browserr 子进程")
    browser_payload = {
        "task_id": task_id,
        "dry_run": spec.dry_run,
        "headless": bool(browser_cfg.get("headless", True)),
        "timeout_seconds": ctx["timeout_s"],
        "navigation_timeout_ms": int(browser_cfg.get("navigation_timeout_ms", 30000)),
        "memory_limit_mb": int(ctx["sched_cfg"].get("subprocess_memory_limit_mb", 512)),
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
        "browser", browser_payload, ctx["timeout_s"] + 15, task_id,
        on_pid=_pid_hook(services, runtime, task_id))
    if not browser_out.get("ok"):
        raise RuntimeError(f"浏览器采集失败：{browser_out.get('error', '未知错误')[:160]}")
    ctx["result"]["materials"] = browser_out.get("materials", [])
    ctx["result"]["browser_session"] = browser_out.get("session", "")
    return "browser"


async def _stage_image(ctx: dict) -> str:
    services, runtime, spec = ctx["services"], ctx["runtime"], ctx["spec"]
    _report(services, runtime, "image", 50, "从 API 池挑选文生图源并下载校验")
    image = await image_gen.generate(services, spec.task_id, ctx["plan"]["prompt"], spec.dry_run,
                                     on_pid=_pid_hook(services, runtime, spec.task_id))
    ctx["image"] = image
    ctx["result"]["assets"].append(image)
    if image.get("simulated"):
        runtime.simulated = True
    runtime.provider = image.get("provider") or runtime.provider
    return "image"


async def _stage_video(ctx: dict) -> str:
    services, runtime, spec = ctx["services"], ctx["runtime"], ctx["spec"]
    _report(services, runtime, "video", 72, "调用视频生成源（支持参考图输入）")
    image = ctx.get("image") or {}
    video = await video_gen.generate(
        services, spec.task_id, ctx["plan"]["prompt"],
        reference_image=image.get("path"), dry_run=spec.dry_run,
        on_pid=_pid_hook(services, runtime, spec.task_id))
    ctx["result"]["assets"].append(video)
    if video.get("simulated"):
        runtime.simulated = True
    runtime.provider = video.get("provider") or runtime.provider
    return "video"


async def _stage_upload(ctx: dict) -> str:
    upload_cfg = ctx["services"]["config"].section("upload")
    if upload_cfg.get("enabled"):
        _report(ctx["services"], ctx["runtime"], "upload", 86, "上传适配器未配置，跳过")
    ctx["result"]["upload"] = {"skipped": True,
                               "reason": "默认关闭" if not upload_cfg.get("enabled") else "未配置上传适配器"}
    return "upload(skipped)"


async def _stage_persist(ctx: dict) -> None:
    _report(ctx["services"], ctx["runtime"], "persist", 92, "写入任务结果与产物索引")
    ctx["services"]["db"].update_task(ctx["spec"].task_id, result_json=ctx["result"])
    return None


async def _stage_reflect(ctx: dict) -> str:
    services, runtime = ctx["services"], ctx["runtime"]
    _report(services, runtime, "reflect", 97, "读取历史结果，生成下一轮优化建议")
    ctx["result"]["reflection"] = await planner.reflect(
        services, runtime, ctx["plan"], ctx["result"], succeeded=True)
    return "reflect"


# 注册内置插件（顺序即默认执行顺序）。browser 与 image 虽无数据依赖，但两者都是
# 重型子进程（Servo 引擎 + 媒体 worker），在老硬件上默认串行是保守策略；
# 并行组机制保留给轻量阶段，确需并行重型阶段先在目标机压测，见 PLUGIN_GUIDE 第 6 节。
pipeline.register(pipeline.StagePlugin(
    name="plan", title="决策计划", progress=10,
    permissions=frozenset({"llm:text", "db:read", "skill:read", "memory:read"}), run=_stage_plan))
pipeline.register(pipeline.StagePlugin(
    name="browser", title="浏览器采集", progress=25,
    permissions=frozenset({"vault:issue", "subprocess:browser", "network:web"}),
    run=_stage_browser))
pipeline.register(pipeline.StagePlugin(
    name="image", title="文生图", progress=50,
    permissions=frozenset({"api:image", "subprocess:media"}),
    run=_stage_image))
pipeline.register(pipeline.StagePlugin(
    name="video", title="文生/图生视频", progress=72,
    permissions=frozenset({"api:video", "subprocess:media"}), run=_stage_video))
pipeline.register(pipeline.StagePlugin(
    name="upload", title="上传（可选）", progress=86,
    permissions=frozenset({"network:upload"}), run=_stage_upload))
pipeline.register(pipeline.StagePlugin(
    name="persist", title="落库", progress=92,
    permissions=frozenset({"db:write"}), run=_stage_persist))
pipeline.register(pipeline.StagePlugin(
    name="reflect", title="独立复盘", progress=97,
    permissions=frozenset({"db:write", "db:read", "memory:write"}), run=_stage_reflect))


async def execute(runtime, services: dict) -> dict:
    """执行一条任务的完整工作流；异常向上抛给 Scheduler 做重试。"""
    spec = runtime.spec
    task_id = spec.task_id
    bind(task_id, "plan")
    config = services["config"]
    browser_cfg = config.section("browser")
    ctx: dict = {
        "services": services, "runtime": runtime, "spec": spec,
        "plan": None, "image": None, "credential_issued": False,
        "browser_cfg": browser_cfg, "sched_cfg": config.section("scheduler"),
        "timeout_s": int(browser_cfg.get("timeout_seconds", 60)),
        "result": {
            "task_id": task_id, "simulated": spec.dry_run,
            "started_at": time.time(), "assets": [], "materials": [], "stages": [],
        },
    }
    result = ctx["result"]
    try:
        plugins = pipeline.build_pipeline(config)
        ctx["result"]["pipeline"] = [p.name for p in plugins]
        index = 0
        while index < len(plugins):
            group_name = plugins[index].parallel_group
            if group_name:
                group = []
                while index < len(plugins) and plugins[index].parallel_group == group_name:
                    group.append(plugins[index])
                    index += 1
                labels = await asyncio.gather(*(p.run(ctx) for p in group))
            else:
                labels = [await plugins[index].run(ctx)]
                index += 1
                group = plugins[index - 1:index]
            for label in labels:
                if label:
                    result["stages"].append(label)
        _report(services, runtime, "done", 100, "全部阶段完成")
        result["finished_at"] = time.time()
        result["simulated"] = bool(runtime.simulated)
        return result
    except Exception as exc:
        LOG.error("任务流水线中断 task=%s stage=%s：%s", task_id, runtime.stage, str(exc)[:200])
        raise
    finally:
        # 凭证会话随任务结束立即销毁；任何情况下都不留给下一个任务
        if ctx["credential_issued"]:
            services["vault"].revoke(task_id)
        services["unregister_pid"](task_id, runtime.pid)
