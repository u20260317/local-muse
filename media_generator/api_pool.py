"""多免费 API 池：加权轮询 + 单服务商信号量 + 令牌桶限速 + 429 冷却熔断。

- 每个服务商独立最大并发配额与每分钟请求数，防止并发打爆触发 429
- 所有真实 HTTP 请求都在子进程或线程池中执行，绝不阻塞调度主循环
- API Key 只在进程内从 config.json 读取，经 stdin 传给子进程，绝不落日志
"""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from collections import deque
from pathlib import Path

from core.logger import get_logger

LOG = get_logger("api_pool")


def _http_json(url: str, headers: dict, body: dict, timeout: int) -> dict:
    """同步 HTTP POST（只在 to_thread / 子进程中调用）。"""
    import requests
    response = requests.post(url, headers=headers, json=body, timeout=max(5, int(timeout)))
    try:
        data = response.json()
    except ValueError:
        data = {"text": response.text[:500]}
    return {"status": response.status_code, "data": data, "headers": dict(response.headers)}


def _extract_text(data: dict) -> str:
    """从各家文本模型响应里提取正文，失败返回空串。"""
    try:
        if "candidates" in data:  # Gemini
            return data["candidates"][0]["content"]["parts"][0]["text"]
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            if message.get("content"):
                return str(message["content"])
        for key in ("reply", "output", "text", "result"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
    except (KeyError, IndexError, TypeError):
        pass
    return ""


class ApiPool:
    def __init__(self, config, db=None) -> None:
        self.config = config
        self.db = db
        self._lock = threading.RLock()
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._token_locks: dict[str, asyncio.Lock] = {}
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}
        self._inflight: dict[str, int] = {}
        self._rr: dict[str, int] = {}
        self._usage: deque[dict] = deque(maxlen=300)
        for provider in config.providers():
            self._semaphores[provider["name"]] = asyncio.Semaphore(
                max(1, int(provider.get("max_concurrency", 1))))

    # ---------- 可用性 / 选源 ----------
    def available(self, name: str) -> bool:
        provider = self.config.provider(name)
        return bool(provider and provider.get("enabled") and provider.get("api_key")
                    and provider.get("base_url"))

    async def pick(self, kind: str, exclude: set[str]) -> str | None:
        """按 priority 严格优先，同档内加权轮询；跳过禁用、缺 Key、冷却中的源。

        priority 越大越优先；只有最高档的源全部不可用或处于 429 冷却时，
        才自动降级到下一档。未配置 priority 的源默认为 0，保持原有加权轮询行为。
        """
        now = time.time()
        candidates: list[tuple[str, int, int]] = []
        for provider in self.config.providers(kind):
            name = provider["name"]
            if name in exclude or not self.available(name):
                continue
            if now < self._cooldown.get(name, 0):
                continue
            candidates.append((name, max(1, int(provider.get("weight", 1))),
                               int(provider.get("priority", 0))))
        if not candidates:
            return None
        top = max(prio for _, _, prio in candidates)
        expanded = [name for name, weight, prio in candidates
                    if prio == top for _ in range(weight)]
        with self._lock:
            index = self._rr.get(kind, 0)
            self._rr[kind] = index + 1
        return expanded[index % len(expanded)]

    # ---------- 并发 / 限速 ----------
    async def acquire(self, provider: str) -> None:
        """先拿服务商并发配额，再按令牌桶等待每分钟限额。"""
        conf = self.config.provider(provider) or {}
        capacity = max(1, int(conf.get("max_concurrency", 1)))
        semaphore = self._semaphores.setdefault(provider, asyncio.Semaphore(capacity))
        await semaphore.acquire()
        rpm = max(1, int(conf.get("rpm", 6)))
        rate = rpm / 60.0
        token_lock = self._token_locks.setdefault(provider, asyncio.Lock())
        while True:
            async with token_lock:
                now = time.monotonic()
                last = self._last_refill.get(provider, now)
                tokens = min(float(rpm), self._tokens.get(provider, float(rpm)) + (now - last) * rate)
                if tokens >= 1:
                    self._tokens[provider] = tokens - 1
                    self._last_refill[provider] = now
                    with self._lock:
                        self._inflight[provider] = self._inflight.get(provider, 0) + 1
                    return
                self._tokens[provider] = tokens
                self._last_refill[provider] = now
                wait_s = min(2.0, max(0.05, (1 - tokens) / rate))
            await asyncio.sleep(wait_s)

    async def release(self, provider: str) -> None:
        semaphore = self._semaphores.get(provider)
        if semaphore:
            try:
                semaphore.release()
            except ValueError:
                pass
        with self._lock:
            self._inflight[provider] = max(0, self._inflight.get(provider, 1) - 1)

    async def report_429(self, provider: str, retry_after: int = 30) -> None:
        """触发风控：该源进入冷却，自动被 pick 跳过。"""
        cooldown = max(5, int(retry_after or 30))
        with self._lock:
            self._cooldown[provider] = time.time() + cooldown
        self.note_usage(provider, "-", "-", "429", 0, f"冷却 {cooldown}s")
        LOG.warning("API 源 %s 触发 429，冷却 %ss，后续请求自动绕过", provider, cooldown)

    def note_usage(self, provider: str, kind: str, task_id: str,
                   status: str, latency_ms: int = 0, note: str = "") -> None:
        entry = {"provider": provider, "kind": kind, "task_id": task_id,
                 "status": status, "latency_ms": int(latency_ms),
                 "note": note[:150], "created_at": time.time()}
        with self._lock:
            self._usage.append(entry)
        if self.db:
            try:
                self.db.add_api_usage(provider, kind, task_id, status, latency_ms, note)
            except Exception:
                pass

    # ---------- 请求构造 ----------
    def build_text_request(self, provider: dict, messages: list[dict]) -> dict:
        key = provider.get("api_key", "")
        url = provider["base_url"].rstrip("/") + provider.get("path", "")
        style = provider.get("style", "openai_chat")
        headers = {"Content-Type": "application/json"}
        if style == "gemini":
            url = f"{url}?key={key}"
            body = {"contents": [{"parts": [{"text": "\n".join(
                str(m.get("content", "")) for m in messages)}]}]}
        elif style == "minimax_chat":
            headers["Authorization"] = f"Bearer {key}"
            body = {"model": provider.get("model", ""), "messages": messages}
        else:  # openai_chat / 兼容模式
            headers["Authorization"] = f"Bearer {key}"
            body = {"model": provider.get("model", ""), "messages": messages,
                    "temperature": 0.7, "max_tokens": 800}
        return {"url": url, "headers": headers, "body": body,
                "timeout": int(provider.get("timeout_s", 60))}

    async def call_text(self, provider: str, messages: list[dict], task_id: str) -> str:
        """文本模型调用（决策/复盘用），遵守该源并发与限速配额。"""
        conf = self.config.provider(provider)
        if not conf or not self.available(provider):
            raise RuntimeError(f"API 源 {provider} 未启用或缺少 Key")
        await self.acquire(provider)
        started = time.monotonic()
        try:
            req = self.build_text_request(conf, messages)
            response = await asyncio.to_thread(
                _http_json, req["url"], req["headers"], req["body"], req["timeout"])
            if response["status"] == 429:
                await self.report_429(provider, 30)
                raise RuntimeError(f"{provider} 返回 429，已冷却")
            if response["status"] >= 400:
                raise RuntimeError(f"{provider} HTTP {response['status']}")
            text = _extract_text(response.get("data", {}))
            if not text:
                raise RuntimeError(f"{provider} 响应中未找到文本内容")
            self.note_usage(provider, "text", task_id, "ok",
                            int((time.monotonic() - started) * 1000))
            return text
        except Exception:
            self.note_usage(provider, "text", task_id, "error",
                            int((time.monotonic() - started) * 1000))
            raise
        finally:
            await self.release(provider)

    def build_generation_request(self, kind: str, provider_name: str, prompt: str,
                                 task_id: str, reference_image: str | None = None) -> dict:
        """构造子进程可执行的生成请求；API Key 仅在此处进入内存 payload。"""
        provider = self.config.provider(provider_name)
        if not provider:
            raise ValueError(f"未知 API 源：{provider_name}")
        key = provider["api_key"]
        base = provider["base_url"].rstrip("/")
        url = base + provider.get("path", "")
        style = provider.get("style", "generic_image")
        headers = {"Content-Type": "application/json"}
        poll_url = ""
        if style == "hf_inference":
            headers["Authorization"] = f"Bearer {key}"
            body = {"inputs": prompt[:1000]}
        elif style == "dashscope_image":
            headers["Authorization"] = f"Bearer {key}"
            headers["X-DashScope-Async"] = "enable"
            body = {"model": provider.get("model", ""),
                    "input": {"prompt": prompt[:1000]},
                    "parameters": {"size": "720*1280", "n": 1}}
            poll_url = f"{base}/api/v1/tasks/{{task_id}}"
        elif style == "minimax_video":
            headers["Authorization"] = f"Bearer {key}"
            body = {"model": provider.get("model", ""), "prompt": prompt[:1000]}
            poll_url = f"{base}/v1/query/video_generation?task_id={{task_id}}"
        elif style == "gemini":
            url = f"{url}?key={key}"
            body = {"contents": [{"parts": [{"text": prompt[:1000]}]}]}
            poll_url = ""
        else:
            headers["Authorization"] = f"Bearer {key}"
            body = {"model": provider.get("model", ""), "prompt": prompt[:1000],
                    "aspect_ratio": "9:16", "response_format": "url"}
            poll_url = f"{url}?task_id={{task_id}}"

        # 参考图：本地文件转 data URL 后随请求发送；URL 形式直接透传
        if reference_image:
            if str(reference_image).startswith(("http://", "https://")):
                body["first_frame_image"] = reference_image
            else:
                path = Path(reference_image)
                if path.exists() and path.is_file():
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    body["first_frame_image"] = f"data:image/png;base64,{encoded}"

        return {
            "ok": True, "kind": kind, "provider": provider_name, "style": style,
            "task_id": task_id, "dry_run": False,
            "url": url, "headers": headers, "body": body,
            "poll_url": poll_url, "poll_headers": {"Authorization": f"Bearer {key}"},
            "poll_interval_s": 5, "max_polls": 24,
            "timeout_s": int(provider.get("timeout_s", 120)),
            "prompt_preview": prompt[:100],
            "memory_limit_mb": int(self.config.section("scheduler")
                                   .get("subprocess_memory_limit_mb", 512)),
        }

    # ---------- 面板快照 ----------
    def snapshot(self) -> dict[str, dict]:
        now = time.time()
        result = {}
        for provider in self.config.providers():
            name = provider["name"]
            with self._lock:
                inflight = self._inflight.get(name, 0)
                cooldown = max(0, int(self._cooldown.get(name, 0) - now))
                used = sum(1 for u in self._usage if u["provider"] == name)
            result[name] = {
                "name": name, "kind": provider.get("kind", ""),
                "enabled": bool(provider.get("enabled")),
                "available": self.available(name),
                "has_key": bool(provider.get("api_key")),
                "weight": int(provider.get("weight", 1)),
                "priority": int(provider.get("priority", 0)),
                "rpm": int(provider.get("rpm", 6)),
                "max_concurrency": int(provider.get("max_concurrency", 1)),
                "inflight": inflight, "cooldown_s": cooldown, "calls": used,
                "doc": provider.get("doc", ""),
                "quota_note": provider.get("quota_note", ""),
                "risk_note": provider.get("risk_note", ""),
            }
        return result

    def usage_snapshot(self, limit: int = 60) -> list[dict]:
        with self._lock:
            return list(self._usage)[-limit:][::-1]
