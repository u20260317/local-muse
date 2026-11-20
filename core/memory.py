"""SQLite 长期记忆模块（借鉴 AI IDE 的记忆模块，如 CodeBuddy Memory）。

为什么需要它：只靠上下文窗口，任务每次都从零开始——同类失败反复重试、历史经验
只能翻最近几条 prompt_history。本模块在同一个 SQLite 库里维护一张 memories 表，
把经验（lesson）、失败（failure）、事实（fact）按 (kind, scope, key) 沉淀下来，
planner 计划前按相关性召回，reflect 与失败处理负责写入，形成跨任务、跨重启的记忆。

检索不用向量库：本地小模型场景下用「词项命中 + 权重 + 新近度」的规则打分足够，
零额外依赖、离线可跑；后续要换 embedding 检索只改 recall 内部实现，接口不变。
"""

# ============================================================
# 【核心模块】基础层 · SQLite 长期记忆
# 作用：memories 表的写入/召回/失败计数/统计；planner 召回经验与失败教训，reflect 与调度失败处理写入
# 定位与接口：见 docs/MEMORY_MODULE.md；模块地图：见 docs/MODULES.md
# ============================================================
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

from core import paths
from core.logger import get_logger

LOG = get_logger("memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(kind, scope, key)
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(kind, scope, updated_at);
"""

KINDS = ("lesson", "failure", "fact", "preference")


def _terms(text: str) -> list[str]:
    """把查询切成词项：英文/数字按词，中文按二元组（bigram），用于规则打分召回。"""
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9_]{2,}", text)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    grams: list[str] = []
    for seg in cjk:
        grams.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        if len(seg) == 1:
            grams.append(seg)
    return words + grams


class MemoryStore:
    """长期记忆存取层；与 TaskDB 共用同一个 SQLite 文件，各自维护连接（WAL 模式可并发）。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path else paths.DB_PATH
        self._lock = threading.RLock()
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---------- 写入 ----------
    def remember(self, kind: str, scope: str, key: str, content: str,
                 weight: float = 1.0) -> int:
        """记一条记忆；同 (kind, scope, key) 已存在时更新内容并提升权重（经验越用越牢）。"""
        kind = kind if kind in KINDS else "fact"
        scope = (scope or "global")[:80]
        key = re.sub(r"\s+", " ", (key or "").strip().lower())[:120]
        content = (content or "").strip()[:2000]
        if not key or not content:
            return 0
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO memories(kind, scope, key, content, weight, hits, created_at, updated_at)
                   VALUES(?,?,?,?,?,0,?,?)
                   ON CONFLICT(kind, scope, key) DO UPDATE SET
                     content=excluded.content, updated_at=excluded.updated_at,
                     weight=memories.weight + 0.5""",
                (kind, scope, key, content, float(weight), now, now))
            row = conn.execute("SELECT id FROM memories WHERE kind=? AND scope=? AND key=?",
                               (kind, scope, key)).fetchone()
        return int(row["id"]) if row else 0

    def record_failure(self, scope: str, stage: str, error: str) -> int:
        """记一次失败：指纹 = stage + 错误类型；重复出现只累加 hits，不新增行。

        返回该指纹累计出现次数，调用方可据此提示「这个坑已经踩过 N 次」。
        """
        err_type = (error or "unknown").split(":", 1)[0].strip()[:60] or "unknown"
        key = f"{stage}:{err_type}".lower()
        scope = (scope or "global")[:80]
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO memories(kind, scope, key, content, weight, hits, created_at, updated_at)
                   VALUES('failure',?,?,?,?,1,?,?)
                   ON CONFLICT(kind, scope, key) DO UPDATE SET
                     content=excluded.content, updated_at=excluded.updated_at,
                     hits=memories.hits + 1""",
                (scope, key, (error or "")[:300], 1.0, now, now))
            row = conn.execute("SELECT hits FROM memories WHERE kind='failure' AND scope=? AND key=?",
                               (scope, key)).fetchone()
        count = int(row["hits"]) if row else 1
        if count >= 3:
            LOG.warning("记忆提示：失败指纹 %s（scope=%s）已累计 %s 次，先查根因再重试", key, scope, count)
        return count

    # ---------- 召回 ----------
    def recall(self, query: str = "", kind: str | None = None,
               scope: str | None = None, limit: int = 8) -> list[dict]:
        """按相关性召回：词项命中数 ×2 + weight + 新近度加权；无 query 时按权重/更新时间取。"""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list = []
        if kind:
            sql += " AND kind=?"; params.append(kind)
        if scope:
            sql += " AND (scope=? OR scope='global')"; params.append(scope)
        sql += " ORDER BY updated_at DESC LIMIT 300"
        with self._lock, self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        terms = _terms(query)
        now = time.time()
        scored = []
        for row in rows:
            hay = (row["key"] + " " + row["content"]).lower()
            hits_score = sum(2.0 for t in set(terms) if t in hay)
            recency = max(0.0, 1.0 - (now - row["updated_at"]) / (30 * 86400))
            score = hits_score + float(row["weight"]) * 0.5 + recency
            if terms and hits_score == 0 and row["scope"] != (scope or ""):
                continue
            scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], -x[1]["updated_at"]))
        picked = [r for _s, r in scored[:max(1, int(limit))]]
        if picked:
            ids = [r["id"] for r in picked]
            with self._lock, self._connect() as conn:
                conn.execute(f"UPDATE memories SET hits=hits+1 WHERE id IN ({','.join('?' * len(ids))})", ids)
        return picked

    # ---------- 管理 / 统计 ----------
    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM memories"
        params: list = []
        if kind:
            sql += " WHERE kind=?"; params.append(kind)
        sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(int(limit))
        with self._lock, self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def delete(self, memory_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id=?", (int(memory_id),))
            return cur.rowcount > 0

    def stats(self) -> dict:
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            by_kind = {r["kind"]: r["c"] for r in conn.execute(
                "SELECT kind, COUNT(*) AS c FROM memories GROUP BY kind").fetchall()}
        return {"total": int(total), "by_kind": by_kind}
