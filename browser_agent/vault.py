"""加密凭证保险库（Vault）：自定义连接器 + 结构化字段 + 强制加密落盘。

命名说明：这里是 **Vault（凭证保险库）**，不是 wallet（钱包）。
它保存的是第三方站点的登录/调用凭证，与任何货币、链上资产无关。

安全边界：
1. 本模块与 media_generator 完全解耦；生成模块永不 import 本模块、永不读取原始凭证。
2. 只有「决策引擎」持有进程内随机 engine_token 才能签发会话级临时凭证；
   令牌不落盘、不进日志，签发结果只存在内存。
3. 落盘一律使用 Fernet 认证加密；cryptography 为必需依赖，缺失时拒绝启动，
   绝不降级为明文。旧的明文 cards.json 会在首次读取时自动升级为加密格式。
4. 面板只拿到脱敏摘要（字段是否配置、长度），永远看不到凭证正文。
5. 凭证只经 stdin 传入浏览器子进程，仅注入隔离 Browserr 会话。
"""

# ============================================================
# 【核心模块】安全模块 · 加密凭证保险库 Vault
# 作用：Fernet 加密落盘；只有决策引擎凭 engine_token 能签发会话级临时凭证；面板只见脱敏摘要
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from core import paths

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# 自定义连接器类型 → 结构化字段定义。面板按此动态渲染表单，后端按此校验与注入。
CONNECTORS: dict[str, dict] = {
    "api_key": {
        "label": "API 密钥",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True},
            {"key": "header_name", "label": "请求头名", "type": "text",
             "default": "Authorization", "required": False},
            {"key": "prefix", "label": "值前缀", "type": "text",
             "default": "Bearer", "required": False},
        ],
    },
    "username_password": {
        "label": "用户名 / 密码",
        "fields": [
            {"key": "username", "label": "用户名", "type": "text", "required": True},
            {"key": "password", "label": "密码", "type": "password", "required": True},
            {"key": "login_url", "label": "登录页 URL", "type": "text",
             "default": "", "required": False},
            {"key": "username_selector", "label": "用户名选择器", "type": "text",
             "default": "input[name=username]", "required": False},
            {"key": "password_selector", "label": "密码选择器", "type": "text",
             "default": "input[name=password]", "required": False},
            {"key": "submit_selector", "label": "提交按钮选择器", "type": "text",
             "default": "button[type=submit]", "required": False},
        ],
    },
    "cookie": {
        "label": "Cookie",
        "fields": [
            {"key": "cookies", "label": "Cookie（k=v; … 或 JSON）",
             "type": "textarea", "required": True},
            {"key": "domain", "label": "作用域域名", "type": "text",
             "default": "example.com", "required": False},
        ],
    },
    "bearer_token": {
        "label": "Bearer Token",
        "fields": [
            {"key": "token", "label": "Token", "type": "password", "required": True},
            {"key": "header_name", "label": "请求头名", "type": "text",
             "default": "Authorization", "required": False},
        ],
    },
    "http_header": {
        "label": "自定义请求头",
        "fields": [
            {"key": "name", "label": "请求头名", "type": "text", "required": True},
            {"key": "value", "label": "请求头值", "type": "password", "required": True},
        ],
    },
    "local_storage": {
        "label": "LocalStorage",
        "fields": [
            {"key": "key", "label": "键名", "type": "text", "required": True},
            {"key": "value", "label": "值", "type": "password", "required": True},
        ],
    },
}

# 旧版 wallet 卡片类型 → 新连接器类型
LEGACY_KIND_MAP = {
    "cookie": "cookie", "token": "bearer_token", "header": "http_header",
    "storage": "local_storage", "login": "cookie",
}


class Vault:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else paths.DIRS["vault"]
        self.root.mkdir(parents=True, exist_ok=True)
        self._file = self.root / "cards.json"
        self._key_file = self.root / ".key"
        self._lock = threading.RLock()
        self._engine_token = secrets.token_urlsafe(32)   # 仅主进程内存持有
        self._sessions: dict[str, dict] = {}             # task_id -> 临时凭证
        try:
            from cryptography.fernet import Fernet  # 必需依赖，不再可选
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            raise RuntimeError(
                "vault 需要 cryptography 才能加密凭证，请执行 "
                "pip install -r requirements.txt 后重启") from exc
        self._Fernet = Fernet
        if not paths.IS_WINDOWS:
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                pass
        self._migrate_legacy_wallet()
        if not self._file.exists():
            self._save_raw([])

    # ---------- 强制加密落盘 ----------
    def _cipher(self):
        if not self._key_file.exists():
            self._key_file.write_bytes(self._Fernet.generate_key())
            if not paths.IS_WINDOWS:
                try:
                    os.chmod(self._key_file, 0o600)
                except OSError:
                    pass
        return self._Fernet(self._key_file.read_bytes())

    def _save_raw(self, cards: list[dict]) -> None:
        """只写加密格式；密钥文件与 cards.json 均在 vault/ 内，从不写明文。"""
        payload = json.dumps(cards, ensure_ascii=False).encode("utf-8")
        blob = {"v": 2, "encrypted": True,
                "data": self._cipher().encrypt(payload).decode("ascii")}
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._file)
        if not paths.IS_WINDOWS:
            try:
                os.chmod(self._file, 0o600)
            except OSError:
                pass

    def _load_raw(self) -> list[dict]:
        if not self._file.exists():
            return []
        blob = json.loads(self._file.read_text(encoding="utf-8"))
        if blob.get("encrypted"):
            raw = self._cipher().decrypt(blob["data"].encode("ascii"))
            cards = json.loads(raw.decode("utf-8"))
        else:
            # 旧版明文：读入后立即升级为加密，绝不继续留在磁盘上
            cards = json.loads(base64.b64decode(blob["data"]).decode("utf-8"))
            if not isinstance(cards, list):
                cards = []
            self._normalize(cards)
            self._save_raw(cards)
            return cards
        if not isinstance(cards, list):
            return []
        self._normalize(cards)
        return cards

    @staticmethod
    def _legacy_fields(kind: str, secret: str) -> dict:
        """把旧版单一 secret 字符串拆成结构化字段。"""
        if kind == "cookie":
            return {"cookies": secret, "domain": "example.com"}
        if kind == "bearer_token":
            return {"token": secret, "header_name": "Authorization"}
        if kind == "http_header":
            name, _, value = secret.partition(":")
            return {"name": name.strip() or "Authorization", "value": value.strip() or secret}
        if kind == "local_storage":
            return {"key": "credential", "value": secret}
        return {"api_key": secret, "header_name": "Authorization", "prefix": "Bearer"}

    def _normalize(self, cards: list[dict]) -> None:
        for card in cards:
            kind = str(card.get("kind", ""))
            if kind in LEGACY_KIND_MAP:
                card["kind"] = LEGACY_KIND_MAP[kind]
            if "fields" not in card:
                card["fields"] = self._legacy_fields(
                    str(card.get("kind", "cookie")), str(card.pop("secret", "")))

    def _migrate_legacy_wallet(self) -> None:
        """把旧 wallet/cards.json 迁到 vault/，升级为加密后删除旧明文文件。"""
        legacy = self.root.parent / "wallet"
        if not legacy.exists() or legacy == self.root:
            return
        old_file, old_key = legacy / "cards.json", legacy / ".key"
        if old_file.exists() and not self._file.exists():
            shutil.copy2(old_file, self._file)
        if old_key.exists() and not self._key_file.exists():
            shutil.copy2(old_key, self._key_file)
        if not self._file.exists():
            return
        self._load_raw()  # encrypted=False 时立即升级为加密，确保凭证不再以明文留存
        for path in (old_file, old_key):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        try:
            legacy.rmdir()  # 仅当目录已空时移除，其它用户文件一律保留
        except OSError:
            pass

    # ---------- 面板可见的脱敏操作 ----------
    def connector_schema(self) -> dict:
        """供面板动态渲染「自定义连接器」表单，不含任何凭证值。"""
        return {name: {"label": spec["label"], "fields": spec["fields"]}
                for name, spec in CONNECTORS.items()}

    def list_cards(self) -> list[dict]:
        """只返回脱敏摘要，绝不返回凭证正文。"""
        with self._lock:
            cards = self._load_raw()
        result = []
        for card in cards:
            kind = str(card.get("kind", "cookie"))
            connector = CONNECTORS.get(kind, CONNECTORS["api_key"])
            fields = card.get("fields") or {}
            parts = []
            for field in connector["fields"]:
                value = str(fields.get(field["key"], "") or "")
                if not value or value == str(field.get("default", "")):
                    continue
                if field["type"] == "password":
                    parts.append(f"{field['label']}: 已配置（{len(value)} 字符，隐藏）")
                else:
                    parts.append(f"{field['label']}: {value[:40]}")
            result.append({
                "name": card.get("name", ""), "kind": kind,
                "kind_label": connector["label"],
                "note": str(card.get("note", ""))[:80],
                "enabled": bool(card.get("enabled", True)),
                "updated_at": float(card.get("updated_at", 0)),
                "masked": "；".join(parts) or "已配置（内容隐藏）",
                "encrypted": True,
            })
        return result

    def add_card(self, name: str, kind: str, fields: dict | None = None,
                 note: str = "", secret: str = "") -> dict:
        """新增/覆盖连接器卡片；按类型校验结构化字段，写入后立即返回脱敏摘要。"""
        if not NAME_RE.fullmatch(str(name or "")):
            raise ValueError("连接器名只允许字母、数字、下划线、连字符（1-64 位）")
        if kind not in CONNECTORS:
            raise ValueError(f"未知连接器类型，可用：{', '.join(CONNECTORS)}")
        values = dict(fields or {})
        if not values and secret:
            values = self._legacy_fields(kind, str(secret))
        spec = CONNECTORS[kind]["fields"]
        clean: dict[str, str] = {}
        for field in spec:
            value = str(values.get(field["key"], field.get("default", "")) or "").strip()
            if field.get("required") and not value:
                raise ValueError(f"缺少必填字段：{field['label']}")
            clean[field["key"]] = value[:2000]
        with self._lock:
            cards = [c for c in self._load_raw() if c.get("name") != name]
            cards.append({"name": name, "kind": kind, "fields": clean,
                          "note": str(note)[:120], "enabled": True,
                          "updated_at": time.time()})
            self._save_raw(cards)
        return {"name": name, "kind": kind,
                "kind_label": CONNECTORS[kind]["label"],
                "masked": "已配置（加密保存，内容隐藏）", "encrypted": True}

    def delete_card(self, name: str) -> bool:
        with self._lock:
            cards = self._load_raw()
            kept = [c for c in cards if c.get("name") != name]
            if len(kept) == len(cards):
                return False
            self._save_raw(kept)
        return True

    def toggle_card(self, name: str, enabled: bool) -> bool:
        with self._lock:
            cards = self._load_raw()
            changed = False
            for card in cards:
                if card.get("name") == name:
                    card["enabled"] = bool(enabled)
                    changed = True
            if changed:
                self._save_raw(cards)
        return changed

    # ---------- 仅决策引擎可调用的临时凭证签发 ----------
    def engine_token(self) -> str:
        """仅由 Scheduler 构造时调用一次；不向面板暴露。"""
        return self._engine_token

    def panel_view(self) -> "VaultPanelView":
        """返回面板专用受限视图，不暴露令牌与签发接口。"""
        return VaultPanelView(self)

    def issue_session_credential(self, task_id: str, card_names: list[str],
                                 engine_token: str, ttl_s: int = 300) -> dict:
        """签发仅属于该 task_id 的临时凭证；校验 engine_token 防止被其它板块调用。"""
        if not hmac.compare_digest(str(engine_token or ""), self._engine_token):
            raise PermissionError("只有决策引擎可以申请临时凭证")
        with self._lock:
            cards = [c for c in self._load_raw()
                     if c.get("name") in set(card_names) and c.get("enabled", True)]
            session = {
                "session_id": secrets.token_urlsafe(12),
                "task_id": task_id,
                "expires_at": time.time() + max(30, int(ttl_s)),
                "cookies": [], "headers": {}, "storage": {}, "form_login": [],
            }
            for card in cards:
                self._merge_card(session, card)
            self._sessions[task_id] = session
        return session

    @staticmethod
    def _merge_card(session: dict, card: dict) -> None:
        """把结构化字段按连接器类型拆到 cookies / headers / storage / form_login。"""
        kind = str(card.get("kind", "cookie"))
        fields = card.get("fields") or {}

        def put_header(name: str, value: str) -> None:
            if name and value:
                session["headers"][name.strip()] = value.strip()

        if kind == "api_key":
            prefix = str(fields.get("prefix", "")).strip()
            value = str(fields.get("api_key", "")).strip()
            put_header(str(fields.get("header_name") or "Authorization"),
                       f"{prefix} {value}".strip() if prefix else value)
        elif kind == "bearer_token":
            put_header(str(fields.get("header_name") or "Authorization"),
                       f"Bearer {str(fields.get('token', '')).strip()}".strip())
        elif kind == "http_header":
            put_header(str(fields.get("name", "")), str(fields.get("value", "")))
        elif kind == "local_storage":
            key = str(fields.get("key", "")).strip()
            if key:
                session["storage"][key] = str(fields.get("value", ""))
        elif kind == "username_password":
            session["form_login"].append({
                "url": str(fields.get("login_url", "")),
                "username": str(fields.get("username", "")),
                "password": str(fields.get("password", "")),
                "username_selector": str(fields.get("username_selector")
                                         or "input[name=username]"),
                "password_selector": str(fields.get("password_selector")
                                         or "input[name=password]"),
                "submit_selector": str(fields.get("submit_selector")
                                       or "button[type=submit]"),
            })
        elif kind == "cookie":
            domain = str(fields.get("domain") or "example.com")
            raw = str(fields.get("cookies", ""))
            parsed = None
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                pass
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    session["cookies"].append({"name": str(key), "value": str(value),
                                               "domain": domain, "path": "/"})
                return
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("name"):
                        entry = dict(item)
                        entry.setdefault("domain", domain)
                        entry.setdefault("path", "/")
                        session["cookies"].append(entry)
                return
            for pair in raw.split(";"):
                if "=" in pair:
                    key, _, value = pair.strip().partition("=")
                    session["cookies"].append({"name": key.strip(),
                                               "value": value.strip(),
                                               "domain": domain, "path": "/"})

    def revoke(self, task_id: str) -> None:
        """任务结束/被取消时立即销毁该会话的临时凭证。"""
        with self._lock:
            session = self._sessions.pop(task_id, None)
        if session:
            for key in ("cookies", "form_login"):
                session[key] = []
            for key in ("headers", "storage"):
                session[key] = {}

    def sessions_snapshot(self) -> list[dict]:
        """面板只看到会话编号与过期时间，看不到凭证内容。"""
        with self._lock:
            now = time.time()
            return [{
                "task_id": tid,
                "session_id": s["session_id"],
                "expires_in_s": max(0, int(s["expires_at"] - now)),
                "cookie_count": len(s["cookies"]),
                "header_count": len(s["headers"]),
            } for tid, s in self._sessions.items() if s["expires_at"] > now]


class VaultPanelView:
    """面板专用受限视图：只暴露连接器管理，不暴露 engine_token 与签发接口。"""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault

    def connector_schema(self) -> dict:
        return self._vault.connector_schema()

    def list_cards(self) -> list[dict]:
        return self._vault.list_cards()

    def add_card(self, name: str, kind: str, fields: dict | None = None,
                 note: str = "", secret: str = "") -> dict:
        return self._vault.add_card(name, kind, fields, note, secret)

    def delete_card(self, name: str) -> bool:
        return self._vault.delete_card(name)

    def toggle_card(self, name: str, enabled: bool) -> bool:
        return self._vault.toggle_card(name, enabled)

    def sessions_snapshot(self) -> list[dict]:
        return self._vault.sessions_snapshot()
