"""统一日志：contextvars 注入 task_id / stage、敏感信息脱敏、内存环形缓冲。

面板的实时日志流直接读内存缓冲，避免高频查询 SQLite 拖慢 L430。
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
import time
from collections import deque
from contextvars import ContextVar
from pathlib import Path

_TASK_ID: ContextVar[str] = ContextVar("lm_task_id", default="-")
_STAGE: ContextVar[str] = ContextVar("lm_stage", default="-")

# 密钥/Cookie/Token 脱敏：只做「值」替换，不打印任何认证头
_REDACTORS = [
    (re.compile(r"(?i)(api[_\-]?key|token|secret|password|cookie|authorization|bearer)(\s*[:=]\s*)([^\s,;\"']+)"),
     r"\1\2***REDACTED***"),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{6,}|ghp_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{6,})\b"),
     "***REDACTED***"),
    (re.compile(r"(?i)([?&]key=)[^&\s]+"), r"\1***REDACTED***"),
]


def redact(text: str) -> str:
    """对可能携带凭证的文本做脱敏，用于日志与面板展示。"""
    out = str(text)
    for pattern, replacement in _REDACTORS:
        out = pattern.sub(replacement, out)
    return out


def bind(task_id: str = "-", stage: str = "-") -> None:
    """绑定当前协程/线程的日志上下文。asyncio.to_thread 会自动复制 contextvars。"""
    _TASK_ID.set(str(task_id or "-"))
    _STAGE.set(str(stage or "-"))


def set_stage(stage: str) -> None:
    _STAGE.set(str(stage or "-"))


def current_task_id() -> str:
    return _TASK_ID.get()


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = redact(_TASK_ID.get())
        record.stage = redact(_STAGE.get())
        record.msg = redact(str(record.msg))
        return True


class RingHandler(logging.Handler):
    """固定容量环形缓冲；供 /api/logs 实时拉取。"""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._db = None
        self.addFilter(_ContextFilter())

    def attach_db(self, db) -> None:
        """仅把 WARNING 以上写入数据库，作为可检索的历史索引。"""
        self._db = db

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "task_id": getattr(record, "task_id", "-"),
                "stage": getattr(record, "stage", "-"),
                "message": record.getMessage(),
            }
            with self._lock:
                self._buffer.append(entry)
            if self._db is not None and record.levelno >= logging.WARNING:
                self._db.add_log(entry["ts"], entry["level"], entry["task_id"],
                                 entry["stage"], entry["message"])
        except Exception:
            pass  # 日志系统绝不反过来影响业务

    def snapshot(self, limit: int = 300, task_id: str | None = None,
                 level: str | None = None, since: float = 0.0) -> list[dict]:
        with self._lock:
            items = list(self._buffer)
        if task_id:
            items = [x for x in items if x["task_id"] == task_id]
        if level and level != "ALL":
            items = [x for x in items if x["level"] == level]
        if since:
            items = [x for x in items if x["ts"] > since]
        return items[-max(1, int(limit)):]


_RING: RingHandler | None = None


def setup_logging(log_path: Path) -> RingHandler:
    """初始化根日志；重复调用安全（会替换本系统自己装的 handler）。"""
    global _RING
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        if getattr(handler, "_lm_handler", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | task=%(task_id)s | %(stage)s | %(message)s",
        datefmt="%H:%M:%S")
    ctx_filter = _ContextFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(ctx_filter)
    stream._lm_handler = True
    root.addHandler(stream)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        file_handler._lm_handler = True
        root.addHandler(file_handler)
    except OSError:
        pass

    _RING = RingHandler()
    _RING.setFormatter(formatter)
    _RING._lm_handler = True
    root.addHandler(_RING)
    return _RING


def get_ring() -> RingHandler | None:
    return _RING


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def snapshot(**kwargs) -> list[dict]:
    return _RING.snapshot(**kwargs) if _RING else []
