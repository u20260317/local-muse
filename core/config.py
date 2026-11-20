"""全局配置管理：缺省合并、类型校验、原子写、脱敏视图、变更热更新。

所有 API 密钥只存在于 config.json，由本模块在进程内读取；面板只拿脱敏视图。
"""

# ============================================================
# 【核心模块】基础层 · 配置管理
# 作用：config.json 缺省合并/类型校验/原子写/脱敏视图/热更新；zen_gate_local 的 key 可由环境变量 ZEN_GATE_API_KEY 注入
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

from core import paths
from core.models import ProviderConfig

# 字段级缺省值；providers 由 config.json 提供，代码不内置任何地址或密钥
DEFAULT_CONFIG: dict = {
    "version": 1,
    "server": {"host": "127.0.0.1", "port": 8765, "use_reloader": False},
    "scheduler": {
        "daily_task_limit": 10,
        "max_concurrency": 2,
        "default_api_max_concurrency": 1,
        "interval_seconds": 300,
        "schedule_times": ["09:00", "18:00"],
        "retry_times": 2,
        "task_timeout_seconds": 600,
        "manual_enqueue_mode": "queue",
        "subprocess_memory_limit_mb": 512,
    },
    "hardware": {
        "cpu_threshold": 85.0,
        "memory_threshold": 85.0,
        "sample_interval_seconds": 2,
        "recovery_margin": 10.0,
    },
    "browser": {
        "enabled": True,
        "engine": "browserr",
        "headless": True,
        "timeout_seconds": 60,
        "navigation_timeout_ms": 30000,
        "per_session_profile": True,
        "extra_args": [],
    },
    "upload": {"enabled": False, "repo": "", "target": ""},
    # v1.2 成片合成（见 docs/COMPOSE.md）：分镜→运镜/转场/字幕/配音/BGM 的 ffmpeg 参数
    "compose": {
        "enabled": True, "width": 1080, "height": 1920, "fps": 30,
        "scene_seconds": 4.0, "transition_seconds": 0.5,
        "voice_enabled": True, "voice": "zh-CN-XiaoxiaoNeural", "bgm_path": "",
    },
    # v1.1 Agent Loop 相关配置（见 docs/PLUGIN_GUIDE.md / docs/MEMORY_MODULE.md）
    "pipeline": {"disabled": [], "order": []},
    "planner": {"history_token_budget": 600, "conversation_token_budget": 400},
    "agent_loop": {
        "enabled": True,
        "goal": "持续产出高质量短视频内容，并根据复盘持续优化",
        "max_rounds_per_day": 6,
        "dry_run": False,
    },
    "memory": {"enabled": True},
    "providers": [],
}

SECRET_MASK = "******"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，列表以 override 为准。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ConfigManager:
    """线程安全的配置容器；保存采用临时文件 + os.replace 原子替换。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else paths.CONFIG_PATH
        self._lock = threading.RLock()
        self._data: dict = {}
        self._stamp: tuple[int, int] | None = None
        self.load()

    # ---------- 读取 ----------
    def load(self) -> dict:
        raw: dict = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
        with self._lock:
            self._data = _deep_merge(DEFAULT_CONFIG, raw)
            self._data["providers"] = [
                ProviderConfig.from_dict(p).to_dict()
                for p in (self._data.get("providers") or [])
                if isinstance(p, dict) and p.get("name")
            ]
            self._stamp = self._stat()
        return self.get()

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def bootstrap(self) -> None:
        """首次运行生成 config.json（仅骨架，密钥为空占位）。"""
        if not self.path.exists():
            self.save(self.get(), preserve_secrets=False)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

    def providers(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            items = copy.deepcopy(self._data.get("providers", []))
        # 本地 zen-gate 源：config.json 里 api_key 留空时，从环境变量注入，
        # 这样本机 mainKey 永远不需要写进仓库文件。
        for item in items:
            if item.get("name") == "zen_gate_local" and not item.get("api_key"):
                env_key = os.environ.get("ZEN_GATE_API_KEY", "")
                if env_key:
                    item["api_key"] = env_key
        return [p for p in items if kind is None or p.get("kind") == kind]

    def provider(self, name: str) -> dict | None:
        for p in self.providers():
            if p.get("name") == name:
                return p
        return None

    def masked(self) -> dict:
        """供面板展示：密钥只显示是否已配置，绝不输出正文。"""
        data = self.get()
        for p in data.get("providers", []):
            p["api_key"] = SECRET_MASK if p.get("api_key") else ""
            p["has_key"] = bool(p.get("api_key") == SECRET_MASK)
        return data

    # ---------- 写入 ----------
    def save(self, new_data: dict, preserve_secrets: bool = True) -> dict:
        """保存配置。面板回传的 ****** 或空密钥表示「保持原值不变」。"""
        with self._lock:
            merged = _deep_merge(self._data, new_data or {})
            if preserve_secrets:
                old = {p.get("name"): p.get("api_key", "")
                       for p in self._data.get("providers", [])}
                for p in merged.get("providers", []):
                    key = p.get("api_key", "")
                    if (not key or key == SECRET_MASK) and old.get(p.get("name")):
                        p["api_key"] = old[p["name"]]
            merged["providers"] = [
                ProviderConfig.from_dict(p).to_dict()
                for p in merged.get("providers", [])
                if isinstance(p, dict) and p.get("name")
            ]
            self._validate(merged)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self._data = merged
            self._stamp = self._stat()
            return self.get()

    @staticmethod
    def _validate(data: dict) -> None:
        """轻量类型/范围校验，防止面板误填把系统搞崩。"""
        sched = data.get("scheduler", {})
        if int(sched.get("max_concurrency", 2)) < 1:
            raise ValueError("全局最大并发数至少为 1")
        if int(sched.get("daily_task_limit", 10)) < 0:
            raise ValueError("每日任务数不能为负")
        if int(sched.get("retry_times", 2)) not in range(0, 6):
            raise ValueError("重试次数需在 0-5 之间")
        for field_name in ("cpu_threshold", "memory_threshold"):
            value = float(data.get("hardware", {}).get(field_name, 85))
            if not 1 <= value <= 100:
                raise ValueError(f"硬件阈值 {field_name} 需在 1-100 之间")
        server = data.get("server", {})
        if server.get("host") not in ("127.0.0.1", "localhost", "::1"):
            server["host"] = "127.0.0.1"  # 强制本机回环，杜绝公网暴露

    def reload_if_changed(self) -> bool:
        """外部编辑 config.json 后热更新，无需重启调度器。"""
        if self._stamp == self._stat():
            return False
        self.load()
        return True
