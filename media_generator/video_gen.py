"""文生视频 / 图生视频调度。

支持把上一步文生图的本地产物作为参考图（first_frame_image）传给视频模型；
复用 image_gen 的生成实现，保证单服务商并发配额与 429 熔断逻辑完全一致。
"""

# ============================================================
# 【核心模块】生成调度 · 文生/图生视频
# 作用：复用 image_gen 的 _run_generation，可把上一步图片产物作为 first_frame_image 参考图
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

from media_generator.image_gen import _run_generation


async def generate(services: dict, task_id: str, prompt: str,
                   reference_image: str | None = None,
                   dry_run: bool = False, on_pid=None) -> dict:
    """生成视频；返回 {kind, path, provider, simulated, verify, pid}。

    dry_run 或无可用的免费视频源时，只写「模拟视频清单」，不伪造 mp4 文件。
    """
    return await _run_generation(
        services, "video", task_id, prompt,
        dry_run=dry_run, reference_image=reference_image, on_pid=on_pid)
