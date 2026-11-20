"""流水线阶段插件注册表（借鉴 DSH 的插件管理 tool-plugin-manager）。

task_loop 的七个阶段全部注册为插件：每个插件声明 name/title/progress/permissions/
parallel_group，执行顺序与启停由 config 的 pipeline 段控制（disabled / order），
加新阶段只需注册一个插件，不再改流水线主干。权限是硬约束的一部分：
声明 vault:issue 的插件只有内置 browser 在白名单内，其余一律拒绝注册。
"""

# ============================================================
# 【核心模块】决策引擎 · 阶段插件注册表
# 作用：管理流水线阶段插件的注册、排序、启停与权限校验；task_loop 只按注册表执行，不再写死阶段顺序
# 定位与接口：见 docs/PLUGIN_GUIDE.md；模块地图：见 docs/MODULES.md
# ============================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from core.logger import get_logger

LOG = get_logger("pipeline")

# 允许签发 Vault 会话凭证的插件白名单（与 docs/security.md 的边界一致）
VAULT_ISSUE_ALLOWED = {"browser"}


@dataclass
class StagePlugin:
    """一个流水线阶段插件。run(ctx) 执行阶段逻辑，返回要记入 result["stages"] 的标签（None 表示不记录）。"""

    name: str
    title: str
    progress: int
    permissions: frozenset[str]
    run: Callable[[dict], Awaitable[str | None]]
    parallel_group: str = ""       # 同组相邻插件会被 asyncio.gather 并行执行
    skippable: bool = True

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "progress": self.progress,
                "permissions": sorted(self.permissions),
                "parallel_group": self.parallel_group, "skippable": self.skippable}


_REGISTRY: dict[str, StagePlugin] = {}


def register(plugin: StagePlugin) -> StagePlugin:
    """注册插件；权限越界直接抛错，宁可启动失败也不许静默放行。"""
    if "vault:issue" in plugin.permissions and plugin.name not in VAULT_ISSUE_ALLOWED:
        raise ValueError(f"插件 {plugin.name} 无权声明 vault:issue（白名单：{sorted(VAULT_ISSUE_ALLOWED)}）")
    _REGISTRY[plugin.name] = plugin
    return plugin


def build_pipeline(config) -> list[StagePlugin]:
    """按 config.pipeline 的 disabled/order 组装本次执行的插件序列。"""
    cfg = {}
    try:
        cfg = config.section("pipeline") or {}
    except Exception:
        cfg = {}
    disabled = set(cfg.get("disabled") or [])
    order = list(cfg.get("order") or [])
    plugins = [p for name, p in _REGISTRY.items() if name not in disabled]
    if disabled:
        LOG.info("流水线插件已禁用：%s", sorted(disabled & set(_REGISTRY)))
    if order:
        rank = {name: i for i, name in enumerate(order)}
        plugins.sort(key=lambda p: rank.get(p.name, len(rank)))
    return plugins


def describe_all(config=None) -> list[dict]:
    """面板/文档用：全部已注册插件及当前是否启用。"""
    disabled = set()
    if config is not None:
        try:
            disabled = set((config.section("pipeline") or {}).get("disabled") or [])
        except Exception:
            disabled = set()
    return [{**p.describe(), "enabled": p.name not in disabled} for p in _REGISTRY.values()]
