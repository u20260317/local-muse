"""Token 计量与上下文压缩（借鉴 DSH 的 token-meter / 上下文压缩插件）。

本地小模型（zen-gate 免费车道）上下文有限，planner 喂历史前必须先计量、超预算先压缩，
而不是靠散落各处的硬截断。本模块只做确定性规则处理，不额外调用 LLM，离线可跑。
"""

# ============================================================
# 【核心模块】基础层 · Token 计量与压缩
# 作用：启发式估算 token（中文约 1 字/token、英文约 4 字/token）；按预算压缩历史文本（保新弃旧、旧条目截断摘要）
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import threading


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af"


def estimate_tokens(text: str) -> int:
    """启发式估算：CJK 字符约 1 token/字，其余约 4 字符/token。仅用于预算控制，不作计费依据。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) + 4 for m in messages)


def compress_texts(texts: list[str], budget_tokens: int, keep_head_chars: int = 60) -> list[str]:
    """把文本列表压进 token 预算：从最新（列表尾部）往前保全文，旧条目超预算时截断为摘要。

    顺序保持不变；被截断的条目尾部带「…（已压缩）」标记，方便日志与调试时识别。
    """
    if budget_tokens <= 0 or not texts:
        return list(texts)
    total = sum(estimate_tokens(t) for t in texts)
    if total <= budget_tokens:
        return list(texts)
    out: list[str] = [""] * len(texts)
    remaining = budget_tokens
    # 先保最新条目的全文
    for i in range(len(texts) - 1, -1, -1):
        cost = estimate_tokens(texts[i])
        if cost <= remaining:
            out[i] = texts[i]
            remaining -= cost
        else:
            out[i] = ""  # 占位，下一轮统一截断填充
    # 旧条目用剩余预算截断填充（按最终字符串的真实估算计价，保证总量不超预算）
    for i, text in enumerate(texts):
        if out[i] == "" and text:
            candidate = text[:keep_head_chars] + "…（已压缩）"
            cost = estimate_tokens(candidate)
            if remaining >= cost:
                out[i] = candidate
                remaining -= cost
            elif remaining >= estimate_tokens("（已压缩省略）"):
                out[i] = "（已压缩省略）"
                remaining -= estimate_tokens("（已压缩省略）")
            else:
                out[i] = ""
    return out


class TokenLedger:
    """按 provider 累计估算 token（线程安全），供面板与选源策略读取。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._in: dict[str, int] = {}
        self._out: dict[str, int] = {}

    def add(self, provider: str, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self._in[provider] = self._in.get(provider, 0) + int(tokens_in)
            self._out[provider] = self._out.get(provider, 0) + int(tokens_out)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            names = set(self._in) | set(self._out)
            return {n: {"tokens_in_est": self._in.get(n, 0),
                        "tokens_out_est": self._out.get(n, 0)} for n in names}
