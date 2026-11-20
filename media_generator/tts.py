"""配音合成：edge-tts（微软在线 TTS，免费、免 Key）逐镜生成旁白 mp3。

设计原则（借鉴 MoneyPrinterTurbo 的可降级流水线）：TTS 只是成片的一层，
失败或未安装时静默降级为「无配音」，绝不让整个任务失败；时长用 ffprobe 实测，
供合成阶段对齐镜头与字幕时间轴。
"""

# ============================================================
# 【核心模块】媒体生成 · 配音（TTS）
# 作用：edge-tts 逐镜合成旁白 mp3 + ffprobe 实测时长；失败降级无配音，不阻断流水线
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from core.logger import get_logger

LOG = get_logger("tts")

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


async def synthesize_scene(narration: str, out_path: Path,
                           voice: str = DEFAULT_VOICE) -> Path | None:
    """把一句旁白合成 mp3；任何失败返回 None（调用方按无配音处理）。"""
    text = (narration or "").strip()
    if not text:
        return None
    try:
        import edge_tts
    except Exception:
        LOG.info("edge-tts 未安装，本镜无配音")
        return None
    try:
        comm = edge_tts.Communicate(text, voice or DEFAULT_VOICE)
        await asyncio.wait_for(comm.save(str(out_path)), timeout=30)
        if out_path.exists() and out_path.stat().st_size > 1024:
            return out_path
        LOG.warning("配音产物异常小，视为失败：%s", out_path)
    except Exception as exc:
        LOG.warning("配音失败（降级无配音）：%s", str(exc)[:120])
    return None


def audio_duration(path: Path) -> float:
    """ffprobe 实测音频时长（秒）；不可用时返回 0。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not Path(path).exists():
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15)
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0
