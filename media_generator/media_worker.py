"""生成任务独立子进程 CLI 入口。

stdin：一次性的请求描述（含 API Key，仅内存传递，不落盘、不进命令行、不进日志）
stdout：仅一行 LM_RESULT_JSON:{...}
职责：发 HTTP 请求 → 解析 URL/base64/异步任务 → 下载 → 完整性校验。
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import paths                         # noqa: E402
from core.logger import redact                 # noqa: E402
from media_generator import downloader         # noqa: E402

RESULT_PREFIX = "LM_RESULT_JSON:"
URL_KEYS = ("image_url", "video_url", "download_url", "output_url", "file_url",
            "url", "urls", "link", "image", "video")
RESOURCE_HINT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm",
                 "download", "/file/", "oss", "cdn")
B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


def _find_url(obj, depth: int = 0, key_hint: bool = False):
    """在响应里找可下载的资源 URL，避免把普通字符串误当产物。"""
    if depth > 7:
        return None
    if isinstance(obj, str):
        text = obj.strip()
        if len(text) > 2000 or not text.startswith(("http://", "https://")):
            return None
        if key_hint or any(hint in text.lower() for hint in RESOURCE_HINT):
            return text
        return None
    if isinstance(obj, list):
        for item in obj:
            found = _find_url(item, depth + 1, key_hint)
            if found:
                return found
    if isinstance(obj, dict):
        for key in URL_KEYS:
            if key in obj:
                found = _find_url(obj[key], depth + 1, True)
                if found:
                    return found
        for value in obj.values():
            found = _find_url(value, depth + 1, key_hint)
            if found:
                return found
    return None


def _find_base64(obj, depth: int = 0):
    if depth > 7:
        return None
    if isinstance(obj, str):
        text = obj.strip()
        if 200 < len(text) < 20_000_000 and not text.startswith("http") and B64_RE.fullmatch(text):
            return text
        return None
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_base64(value, depth + 1)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _find_base64(item, depth + 1)
            if found:
                return found
    return None


def _find_task_id(obj, depth: int = 0):
    if depth > 7:
        return None
    if isinstance(obj, dict):
        for key in ("task_id", "request_id", "job_id", "id"):
            value = obj.get(key)
            if isinstance(value, (str, int)) and 0 < len(str(value)) < 120:
                return str(value)
        for value in obj.values():
            found = _find_task_id(value, depth + 1)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _find_task_id(item, depth + 1)
            if found:
                return found
    return None


def _find_status(obj, depth: int = 0) -> str:
    if depth > 6 or not isinstance(obj, dict):
        return ""
    for key in ("task_status", "status", "state"):
        if isinstance(obj.get(key), str):
            return obj[key].lower()
    for value in obj.values():
        if isinstance(value, (dict, list)):
            found = _find_status(value, depth + 1)
            if found:
                return found
    return ""


def _dest(task_id: str, kind: str, url: str = "") -> Path:
    ext = ".mp4" if kind == "video" else ".png"
    lowered = (url or "").lower()
    for candidate in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"):
        if candidate in lowered:
            ext = candidate
            break
    return paths.output_dir(task_id) / f"{kind}_{int(time.time() * 1000)}{ext}"


def _poll(payload: dict, task_id_value: str, dest: Path) -> dict:
    """异步生成任务轮询（DashScope / MiniMax 等返回 task_id 的接口）。"""
    import requests
    template = payload.get("poll_url", "")
    if not template:
        return {"ok": False, "error": "响应返回 task_id，但该服务商未配置轮询地址"}
    interval = max(2, int(payload.get("poll_interval_s", 5)))
    max_polls = max(1, int(payload.get("max_polls", 24)))
    timeout = max(10, int(payload.get("timeout_s", 60)))
    headers = payload.get("poll_headers", {}) or {}
    for _ in range(max_polls):
        time.sleep(interval)
        try:
            response = requests.get(template.replace("{task_id}", task_id_value),
                                    headers=headers, timeout=timeout)
            if response.status_code == 429:
                return {"ok": False, "status": 429, "error": "轮询触发 429 限流"}
            data = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"轮询失败：{redact(str(exc))[:160]}"}
        url = _find_url(data)
        if url:
            return downloader.fetch(url, _dest(payload["task_id"], payload["kind"], url), timeout)
        encoded = _find_base64(data)
        if encoded:
            return downloader.save_base64(encoded, dest)
        status = _find_status(data)
        if status in ("failed", "error", "canceled", "cancelled"):
            return {"ok": False, "error": f"异步任务失败，状态={status}"}
    return {"ok": False, "error": "异步任务轮询超时，未取得成品"}


def handle(payload: dict) -> dict:
    task_id = paths.safe_id(str(payload.get("task_id", "")))
    kind = str(payload.get("kind", "image"))
    if payload.get("dry_run"):
        return downloader.make_simulated_asset(
            task_id, kind, str(payload.get("prompt", "")))

    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "未安装 requests，请运行 pip install -r requirements.txt"}

    timeout = max(10, int(payload.get("timeout_s", 60)))
    dest = _dest(task_id, kind)
    try:
        response = requests.post(payload["url"], headers=payload.get("headers", {}),
                                 json=payload.get("body", {}), timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"请求失败：{redact(str(exc))[:200]}"}
    if response.status_code == 429:
        return {"ok": False, "status": 429,
                "error": "服务商返回 429，已交给 API 池冷却处理"}
    if response.status_code >= 400:
        return {"ok": False, "error": f"服务商 HTTP {response.status_code}"}

    content_type = response.headers.get("Content-Type", "").lower()
    if content_type.startswith(("image/", "video/")):
        return downloader.save_bytes(response.content, dest)
    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "error": "服务商响应非 JSON，且不是可直接保存的二进制产物"}

    url = _find_url(data)
    if url:
        return downloader.fetch(url, _dest(task_id, kind, url), timeout)
    encoded = _find_base64(data)
    if encoded:
        return downloader.save_base64(encoded, dest)
    task_id_value = _find_task_id(data)
    if task_id_value:
        return _poll(payload, task_id_value, dest)
    return {"ok": False, "error": "服务商响应中未找到 URL / base64 / 异步任务编号"}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception as exc:
        sys.stdout.write(RESULT_PREFIX + json.dumps(
            {"ok": False, "error": f"参数解析失败：{exc}"}, ensure_ascii=False) + "\n")
        return 2
    paths.apply_worker_memory_limit(int(payload.get("memory_limit_mb", 0)))
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = handle(payload)
        except Exception as exc:
            result = {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:300]}
    if isinstance(result, dict):
        result.setdefault("simulated", False)
        result.setdefault("kind", payload.get("kind", ""))
        result.setdefault("provider", payload.get("provider", ""))
    sys.stdout.write(RESULT_PREFIX + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
