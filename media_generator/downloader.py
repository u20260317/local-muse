"""产物下载与完整性校验（同步函数，运行在 media_worker 子进程内）。

流式写 .part 后原子改名，避免半成品被面板预览；校验大小与文件魔数。
dry_run 只生成明确标注的模拟产物，绝不伪装成真实 API 生成结果。
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import zlib
from pathlib import Path

from core import paths


def _detect(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF8"):
        return "gif"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm/mkv"
    return "unknown"


def verify(path: str | Path) -> dict:
    """返回文件完整性信息：存在、大小、魔数类型、是否通过。"""
    target = Path(path)
    if not target.exists():
        return {"exists": False, "size": 0, "kind": "missing", "verified": False}
    size = target.stat().st_size
    with target.open("rb") as handle:
        head = handle.read(32)
    kind = _detect(head)
    if target.suffix.lower() == ".json":
        # 模拟清单是元数据文件，单独标注，不冒充图片/视频
        return {"exists": True, "size": size, "kind": "manifest", "verified": size > 0}
    verified = size > 0 and kind != "unknown"
    return {"exists": True, "size": size, "kind": kind, "verified": verified}


def save_bytes(data: bytes, dest: str | Path) -> dict:
    """把内存字节原子落盘并校验。"""
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    part.write_bytes(data)
    os.replace(part, target)
    return {"ok": True, "path": str(target), "simulated": False, "verify": verify(target)}


def save_base64(text: str, dest: str | Path) -> dict:
    """保存 API 返回的 base64 内容（容忍 data URL 前缀）。"""
    payload = str(text or "")
    if "," in payload[:80] and payload.strip().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:
        return {"ok": False, "error": f"base64 解码失败：{exc}"}
    if not raw:
        return {"ok": False, "error": "base64 内容为空"}
    return save_bytes(raw, dest)


def fetch(url: str, dest: str | Path, timeout: int = 60) -> dict:
    """流式下载远端产物，原子落盘后做魔数/大小校验。"""
    import requests
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=max(5, int(timeout))) as response:
            if response.status_code == 429:
                return {"ok": False, "status": 429, "error": "下载触发 429 限流"}
            if response.status_code >= 400:
                return {"ok": False, "error": f"下载失败 HTTP {response.status_code}"}
            with part.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
        if not part.exists() or part.stat().st_size == 0:
            part.unlink(missing_ok=True)
            return {"ok": False, "error": "下载内容为空"}
        os.replace(part, target)
        check = verify(target)
        if not check["verified"]:
            return {"ok": False, "error": f"产物完整性校验未通过：{check['kind']}",
                    "verify": check, "path": str(target)}
        return {"ok": True, "path": str(target), "url": url,
                "simulated": False, "verify": check}
    except Exception as exc:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": f"下载异常：{str(exc)[:160]}"}


def _png_1x1() -> bytes:
    """构造最小合法 PNG（仅用于 dry_run 自检占位，不代表生成效果）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def make_simulated_asset(task_id: str, kind: str, prompt: str = "") -> dict:
    """生成明确标注的模拟产物：图片为占位 PNG；视频只写清单，不伪造视频文件。"""
    out_dir = paths.output_dir(task_id)
    stamp = int(time.time())
    if kind == "image":
        target = out_dir / f"simulated_image_{stamp}.png"
        save_bytes(_png_1x1(), target)
        note = "1x1 占位 PNG，仅用于验证下载与校验链路"
    else:
        target = out_dir / f"simulated_video_{stamp}.json"
        target.write_text(json.dumps({
            "simulated": True, "kind": "video", "task_id": task_id,
            "prompt_preview": prompt[:120], "created_at": stamp,
            "note": "未调用任何视频 API；此处仅记录占位元数据，不伪造视频文件",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        note = "视频占位清单（JSON），未伪造 mp4 文件"

    manifest = out_dir / "dry_run_manifest.json"
    history = []
    if manifest.exists():
        try:
            history = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history = []
    history.append({"task_id": task_id, "kind": kind, "path": str(target),
                    "created_at": stamp, "note": note})
    manifest.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(target), "kind": kind,
            "simulated": True, "verify": verify(target), "note": note}
