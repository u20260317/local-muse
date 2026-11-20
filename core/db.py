"""SQLite 访问层：WAL + 短事务 + 按 task_id 隔离记录。

每条任务独占一行主记录，另有 prompt 历史、API 消耗、日志索引三张附属表；
一个任务写失败不会影响其他并发任务。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

TASK_COLUMNS = {
    "task_id", "priority", "source", "skill", "instruction", "enqueue_mode",
    "status", "stage", "progress", "retry", "dry_run", "simulated", "provider",
    "created_at", "started_at", "finished_at", "error", "result_json", "pid",
    "conversation_id",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    priority     INTEGER NOT NULL DEFAULT 2,
    source       TEXT NOT NULL DEFAULT 'manual',
    skill        TEXT NOT NULL DEFAULT '',
    instruction  TEXT NOT NULL DEFAULT '',
    enqueue_mode TEXT NOT NULL DEFAULT 'queue',
    status       TEXT NOT NULL DEFAULT 'queued',
    stage        TEXT NOT NULL DEFAULT 'init',
    progress     INTEGER NOT NULL DEFAULT 0,
    retry        INTEGER NOT NULL DEFAULT 0,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    simulated    INTEGER NOT NULL DEFAULT 0,
    provider     TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    started_at   REAL NOT NULL DEFAULT 0,
    finished_at  REAL NOT NULL DEFAULT 0,
    error        TEXT NOT NULL DEFAULT '',
    result_json  TEXT NOT NULL DEFAULT '{}',
    pid          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

CREATE TABLE IF NOT EXISTS prompt_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    skill       TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    reflection  TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_task ON prompt_history(task_id);

CREATE TABLE IF NOT EXISTS api_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_created ON api_usage(created_at DESC);

CREATE TABLE IF NOT EXISTS log_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    level       TEXT NOT NULL,
    task_id     TEXT NOT NULL DEFAULT '-',
    stage       TEXT NOT NULL DEFAULT '-',
    message     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_task ON log_index(task_id, id DESC);

-- 对话会话与消息：SQLite 持久化记忆，重启后仍可恢复上下文
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '新会话',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'user',
    content          TEXT NOT NULL DEFAULT '',
    task_id          TEXT NOT NULL DEFAULT '',
    meta_json        TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
"""


class TaskDB:
    """所有方法自行开/关连接，短事务且线程安全，适配 Flask 线程 + asyncio 线程并发。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            # 老库平滑迁移：tasks 补会话归属列，已有任务记录不丢失
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            if "conversation_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_conv "
                         "ON tasks(conversation_id, created_at DESC)")

    # ---------- 任务 ----------
    def upsert_task(self, record: dict) -> None:
        """按 task_id 插入或覆盖；只接受白名单字段，避免 SQL 注入。"""
        cols = {k: v for k, v in record.items() if k in TASK_COLUMNS}
        if "task_id" not in cols:
            raise ValueError("upsert_task 需要 task_id")
        for key in ("dry_run", "simulated"):
            if key in cols:
                cols[key] = int(bool(cols[key]))
        if "result_json" in cols and not isinstance(cols["result_json"], str):
            cols["result_json"] = json.dumps(cols["result_json"], ensure_ascii=False)
        names = list(cols)
        placeholders = ", ".join("?" for _ in names)
        updates = ", ".join(f"{n}=excluded.{n}" for n in names if n != "task_id")
        sql = (f"INSERT INTO tasks ({', '.join(names)}) VALUES ({placeholders}) "
               f"ON CONFLICT(task_id) DO UPDATE SET {updates}")
        with self._lock, self._connect() as conn:
            conn.execute(sql, [cols[n] for n in names])

    def update_task(self, task_id: str, **fields) -> None:
        cols = {k: v for k, v in fields.items() if k in TASK_COLUMNS and k != "task_id"}
        if not cols:
            return
        for key in ("dry_run", "simulated"):
            if key in cols:
                cols[key] = int(bool(cols[key]))
        if "result_json" in cols and not isinstance(cols["result_json"], str):
            cols["result_json"] = json.dumps(cols["result_json"], ensure_ascii=False)
        assignments = ", ".join(f"{n}=?" for n in cols)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE task_id=?",
                         [*cols.values(), task_id])

    def get_task(self, task_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, limit: int = 100, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks"
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_today(self, source: str | None = None) -> int:
        """统计当天已创建的任务数，用于每日任务数量上限。"""
        midnight = time.localtime()
        start = time.mktime((midnight.tm_year, midnight.tm_mon, midnight.tm_mday,
                             0, 0, 0, 0, 0, -1))
        sql = "SELECT COUNT(*) AS c FROM tasks WHERE created_at>=?"
        params: list = [start]
        if source:
            sql += " AND source=?"
            params.append(source)
        with self._lock, self._connect() as conn:
            return int(conn.execute(sql, params).fetchone()["c"])

    def stats(self) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    # ---------- prompt 历史 / 反思 ----------
    def add_prompt_history(self, task_id: str, skill: str, prompt: str,
                           reflection: str = "", score: float = 0.0) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO prompt_history(task_id,skill,prompt,reflection,score,created_at) "
                "VALUES (?,?,?,?,?,?)", (task_id, skill, prompt, reflection, score, time.time()))
            return int(cur.lastrowid)

    def recent_prompts(self, skill: str | None = None, limit: int = 8) -> list[dict]:
        sql = "SELECT * FROM prompt_history"
        params: list = []
        if skill:
            sql += " WHERE skill=?"
            params.append(skill)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- API 消耗 ----------
    def add_api_usage(self, provider: str, kind: str, task_id: str, status: str,
                      latency_ms: int = 0, note: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO api_usage(provider,kind,task_id,status,latency_ms,note,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (provider, kind, task_id, status, int(latency_ms), note[:300], time.time()))

    def list_api_usage(self, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM api_usage ORDER BY id DESC LIMIT ?",
                                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ---------- 日志索引 ----------
    def add_log(self, ts: float, level: str, task_id: str, stage: str, message: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO log_index(ts,level,task_id,stage,message) VALUES (?,?,?,?,?)",
                         (ts, level, task_id, stage, message[:500]))

    def list_logs(self, limit: int = 200, task_id: str | None = None,
                  level: str | None = None) -> list[dict]:
        sql = "SELECT * FROM log_index WHERE 1=1"
        params: list = []
        if task_id:
            sql += " AND task_id=?"
            params.append(task_id)
        if level:
            sql += " AND level=?"
            params.append(level)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- 会话记忆（SQLite 持久化，重启后可恢复） ----------
    def create_conversation(self, title: str = "新会话") -> str:
        conv_id = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations(id,title,created_at,updated_at) VALUES (?,?,?,?)",
                (conv_id, str(title)[:80] or "新会话", now, now))
        return conv_id

    def list_conversations(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT c.*, "
                "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count, "
                "(SELECT m.content FROM messages m WHERE m.conversation_id=c.id "
                " ORDER BY m.id DESC LIMIT 1) AS last_message "
                "FROM conversations c WHERE c.archived=0 "
                "ORDER BY c.updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=?",
                               (conv_id,)).fetchone()
        return dict(row) if row else None

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                               (str(title)[:80] or "新会话", time.time(), conv_id))
            return cur.rowcount > 0

    def archive_conversation(self, conv_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE conversations SET archived=1, updated_at=? WHERE id=?",
                               (time.time(), conv_id))
            return cur.rowcount > 0

    def delete_conversation(self, conv_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            cur = conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
            return cur.rowcount > 0

    def add_message(self, conv_id: str, role: str, content: str,
                    task_id: str = "", meta: dict | None = None) -> int:
        now = time.time()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,task_id,meta_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (conv_id, str(role)[:20], str(content)[:8000], task_id,
                 json.dumps(meta or {}, ensure_ascii=False), now))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
            return int(cur.lastrowid)

    def list_messages(self, conv_id: str, limit: int = 200) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, int(limit),)).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["meta"] = json.loads(item.pop("meta_json") or "{}")
            except (ValueError, TypeError):
                item["meta"] = {}
            result.append(item)
        return result

    def conversation_context(self, conv_id: str, limit: int = 12) -> list[dict]:
        """给决策引擎的短期上下文：仅 role/content，绝不含凭证或密钥。"""
        return [{"role": m["role"], "content": m["content"][:500]}
                for m in self.list_messages(conv_id, limit)
                if m["role"] in ("user", "assistant")]

    def list_tasks_by_conversation(self, conv_id: str, limit: int = 30) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conv_id, int(limit),)).fetchall()
        return [dict(r) for r in rows]
