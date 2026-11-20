"""成片合成：分镜静图/片段 + ffmpeg 合成为可发布短视频。

工序（参考 MoneyPrinterTurbo 的合成管线与 AI-Director 的分镜拼接）：
  逐镜制片（静图 zoompan 推拉运镜 / 视频片段归一化）→ xfade 转场拼接
  → 旁白音轨按镜对齐（无配音自动补静音）→ BGM 低音量混音
  → ASS 字幕按时间轴烧录 → H.264 + AAC 成片（faststart）。
任何一层失败都有降级：xfade 不可用退回 concat、字幕滤镜不可用退回无字幕，
合成整体失败由调用方（compose 阶段）降级跳过，不拖垮任务。
"""

# ============================================================
# 【核心模块】媒体生成 · 成片合成（ffmpeg）
# 作用：分镜素材 → 运镜/转场/字幕/配音/BGM 一条龙合成竖屏成片；多级降级保底
# 定位与接口：见 docs/MODULES.md、docs/COMPOSE.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from core.logger import get_logger

LOG = get_logger("compose")


def find_ffmpeg() -> str | None:
    """优先系统 ffmpeg，其次 imageio-ffmpeg 自带二进制（安装包靠它免装 ffmpeg）。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


async def _run(cmd: list[str], timeout: int = 300, cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"ffmpeg 超时（{timeout}s）：{cmd[1] if len(cmd) > 1 else cmd[0]}")
    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"ffmpeg 失败 rc={proc.returncode}：{tail}")


def _font_name() -> str:
    if sys.platform == "win32":
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_ass(slots: list[dict], width: int, height: int) -> str:
    """按镜头时间槽生成 ASS 字幕（PlayRes 与成片一致，字号/描边按竖屏调校）。"""
    font_size = max(36, height // 36)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{_font_name()},{font_size},&H00FFFFFF,&H00FFFFFF,&H00202020,"
        f"&H64000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{height // 13},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for slot in slots:
        text = (slot.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        safe = text.replace("{", "｛").replace("}", "｝")
        lines.append(
            f"Dialogue: 0,{_ass_time(slot['start'])},{_ass_time(slot['end'])},"
            f"Default,,0,0,0,,{safe}")
    return header + "\n".join(lines) + "\n"


async def compose(scenes: list[dict], out_path: Path, cfg: dict) -> dict:
    """把分镜合成成片。scenes: [{image|video, narration, audio, duration}]。

    返回 {path, duration, scenes, voice, bgm, subtitles, ffmpeg}；失败抛 RuntimeError。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg（系统未装且 imageio-ffmpeg 不可用）")
    width = int(cfg.get("width", 1080))
    height = int(cfg.get("height", 1920))
    fps = int(cfg.get("fps", 30))
    fade = float(cfg.get("transition_seconds", 0.5))
    default_dur = float(cfg.get("scene_seconds", 4.0))
    scenes = [s for s in scenes if s.get("image") or s.get("video")]
    if not scenes:
        raise RuntimeError("没有可合成的分镜素材")

    durations = []
    for scene in scenes:
        dur = float(scene.get("duration") or 0) or default_dur
        durations.append(min(15.0, max(2.5, dur)))
    total = sum(durations) - fade * (len(scenes) - 1)

    workdir = out_path.parent / (out_path.stem + "_parts")
    workdir.mkdir(parents=True, exist_ok=True)

    # ---- 第一遍：逐镜制片（纯视频） ----
    clips: list[Path] = []
    for idx, (scene, dur) in enumerate(zip(scenes, durations)):
        clip = workdir / f"clip{idx}.mp4"
        frames = max(1, round(dur * fps))
        if scene.get("video"):
            vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},fps={fps},format=yuv420p")
            cmd = [ffmpeg, "-y", "-stream_loop", "-1", "-i", str(scene["video"]),
                   "-vf", vf, "-frames:v", str(frames), "-an",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(clip)]
        else:
            zoom_in = idx % 2 == 0
            zexpr = (f"1+0.15*on/{frames}" if zoom_in
                     else f"1.15-0.15*on/{frames}")
            vf = (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                  f"crop={width * 2}:{height * 2},"
                  f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d={frames}:s={width}x{height}:fps={fps},format=yuv420p")
            cmd = [ffmpeg, "-y", "-i", str(scene["image"]),
                   "-vf", vf, "-frames:v", str(frames),
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(clip)]
        await _run(cmd, timeout=180)
        clips.append(clip)

    # ---- 第二遍：拼接（xfade 转场，失败退回 concat） ----
    silent = workdir / "silent.mp4"
    if len(clips) == 1:
        shutil.copyfile(clips[0], silent)
    else:
        inputs: list[str] = []
        for clip in clips:
            inputs += ["-i", str(clip)]
        filters = []
        current = "0:v"
        merged_len = durations[0]
        for k in range(1, len(clips)):
            offset = max(0.0, merged_len - fade)
            label = f"x{k}"
            filters.append(
                f"[{current}][{k}:v]xfade=transition=fade:duration={fade}:"
                f"offset={offset:.3f}[{label}]")
            current = label
            merged_len += durations[k] - fade
        try:
            await _run([ffmpeg, "-y", *inputs, "-filter_complex", ";".join(filters),
                        "-map", f"[{current}]", "-c:v", "libx264", "-preset",
                        "veryfast", "-crf", "20", str(silent)], timeout=300)
        except RuntimeError as exc:
            LOG.warning("xfade 拼接失败，退回 concat：%s", str(exc)[:120])
            list_file = workdir / "concat.txt"
            list_file.write_text(
                "".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
            await _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i",
                        list_file.name, "-c", "copy", str(silent)],
                       timeout=300, cwd=workdir)

    # ---- 时间槽（字幕与配音共用同一条时间轴） ----
    slots = []
    cursor = 0.0
    for idx, dur in enumerate(durations):
        slot_len = dur - (fade if idx < len(durations) - 1 else 0.0)
        slots.append({"start": cursor, "end": cursor + slot_len,
                      "text": scenes[idx].get("narration", "")})
        cursor += slot_len

    # ---- 第三遍：配音轨（逐镜对齐，缺失补静音） ----
    segments: list[Path] = []
    any_voice = False
    for idx, (scene, slot) in enumerate(zip(scenes, slots)):
        seg = workdir / f"voice{idx}.wav"
        slot_len = slot["end"] - slot["start"]
        audio = scene.get("audio")
        if audio and Path(audio).exists():
            any_voice = True
            await _run([ffmpeg, "-y", "-i", str(audio), "-af", "apad",
                        "-t", f"{slot_len:.3f}", "-ac", "2", "-ar", "44100",
                        str(seg)], timeout=120)
        else:
            await _run([ffmpeg, "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=44100:cl=stereo", "-t", f"{slot_len:.3f}",
                        str(seg)], timeout=60)
        segments.append(seg)
    voice_track = workdir / "voice.wav"
    if len(segments) == 1:
        shutil.copyfile(segments[0], voice_track)
    else:
        inputs = []
        for seg in segments:
            inputs += ["-i", str(seg)]
        concat_filter = "".join(f"[{i}:a]" for i in range(len(segments))) + \
            f"concat=n={len(segments)}:v=0:a=1[a]"
        await _run([ffmpeg, "-y", *inputs, "-filter_complex", concat_filter,
                    "-map", "[a]", str(voice_track)], timeout=180)

    # ---- 终遍：字幕烧录 + BGM 混音 + 封装 ----
    bgm_path = str(cfg.get("bgm_path") or "")
    if bgm_path and not Path(bgm_path).is_absolute():
        # 相对路径按应用根目录解析（终遍 ffmpeg 的 cwd 在合成工作目录内）
        bgm_path = str(Path(__file__).resolve().parent.parent / bgm_path)
    has_bgm = bool(bgm_path and Path(bgm_path).exists())
    ass_text = _build_ass(slots, width, height)
    has_text = "Dialogue:" in ass_text
    if has_text:
        (workdir / "subs.ass").write_text(ass_text, encoding="utf-8")

    async def _final(with_subs: bool) -> None:
        cmd = [ffmpeg, "-y", "-i", "silent.mp4", "-i", "voice.wav"]
        if has_bgm:
            cmd += ["-stream_loop", "-1", "-i", bgm_path]
        filters = []
        if with_subs:
            filters.append("[0:v]ass=subs.ass[v]")
            video_label = "[v]"
        else:
            video_label = "0:v"
        if has_bgm:
            filters.append("[1:a][2:a]amix=inputs=2:duration=first:"
                           "weights=1 0.16,dynaudnorm[a]")
            audio_label = "[a]"
        else:
            audio_label = "1:a"
        if filters:
            cmd += ["-filter_complex", ";".join(filters)]
        cmd += ["-map", video_label, "-map", audio_label,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-t", f"{total:.3f}", str(out_path)]
        # silent.mp4 / voice.wav / subs.ass 以相对名在 workdir 内引用，产物写绝对路径
        await _run(cmd, timeout=600, cwd=workdir)

    subtitles_done = has_text
    try:
        await _final(with_subs=has_text)
    except RuntimeError as exc:
        if has_text:
            LOG.warning("字幕烧录失败，退回无字幕成片：%s", str(exc)[:120])
            subtitles_done = False
            await _final(with_subs=False)
        else:
            raise
    LOG.info("合成完成：%s（%s 镜，%.1fs，配音=%s，字幕=%s，BGM=%s）",
             out_path, len(scenes), total, any_voice, subtitles_done, has_bgm)
    return {"path": str(out_path), "duration": round(total, 2),
            "scenes": len(scenes), "voice": any_voice, "bgm": has_bgm,
            "subtitles": subtitles_done, "ffmpeg": ffmpeg}
