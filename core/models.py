"""跨模块数据契约。

所有模块只依赖这里的 dataclass / Enum，避免接口签名漂移。
"""

# ============================================================
# 【核心模块】基础层 · 跨模块数据契约
# 作用：TaskSpec/TaskRuntime/TaskStatus/TaskPriority/ProviderConfig 唯一定义处，其它模块只依赖这里，避免签名漂移
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import asdict, dataclass, field


class TaskPriority(enum.IntEnum):
    """数值越小优先级越高；配合 seq 保证同优先级 FIFO。"""

    HIGH_TEST = 0   # 高优先级测试任务
    MANUAL = 1      # 面板手动触发任务
    SCHEDULED = 2   # 定时常规任务


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PAUSED = "paused"   # 硬件降级时仅用于展示，队列中的任务仍保持 QUEUED


@dataclass
class ProviderConfig:
    """单个 API 服务商配置（密钥只从 config.json 读取，代码零硬编码）。"""

    name: str
    kind: str = "text"                 # text / image / video
    base_url: str = ""
    path: str = ""
    style: str = "openai_chat"        # 适配器风格
    model: str = ""
    api_key: str = ""                 # 空占位，由用户在面板填写
    enabled: bool = False
    weight: int = 1
    priority: int = 0                 # 选源优先级：数值越大越优先，同档内按 weight 轮询
    rpm: int = 6                      # 每分钟请求数上限
    max_concurrency: int = 1          # 单服务商并发配额
    timeout_s: int = 60
    doc: str = ""
    quota_note: str = "免费额度以官方最新公告为准"
    risk_note: str = "需自行申请 Key 并确认免费额度"

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderConfig":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskSpec:
    """一条任务指令；调度主循环只分发，不等待其执行完成。"""

    task_id: str
    priority: TaskPriority
    source: str                       # scheduled / manual / test
    enqueue_mode: str                 # insert 插队 / queue 排队
    skill: str
    instruction: str
    created_at: float
    seq: int = 0                      # 由 Scheduler 统一分配
    dry_run: bool = False             # 显式自检模式，产物标注「模拟」
    conversation_id: str = ""         # 归属会话，用于 SQLite 持久化对话记忆

    @classmethod
    def create(cls, priority: TaskPriority, source: str, enqueue_mode: str,
               skill: str, instruction: str, dry_run: bool = False,
               conversation_id: str = "") -> "TaskSpec":
        return cls(
            task_id=uuid.uuid4().hex[:16],
            priority=priority,
            source=source,
            enqueue_mode=enqueue_mode,
            skill=skill,
            instruction=instruction,
            created_at=time.time(),
            dry_run=dry_run,
            conversation_id=conversation_id,
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["priority"] = int(self.priority)
        return data


@dataclass
class TaskRuntime:
    """单任务运行态；每个 task_id 一份，彼此互不影响。"""

    spec: TaskSpec
    status: TaskStatus = TaskStatus.QUEUED
    stage: str = "init"
    progress: int = 0                 # 0-100
    started_at: float = 0.0
    finished_at: float = 0.0
    pid: int | None = None            # 当前业务子进程 PID，供面板展示与单独终止
    timeout_s: int = 600
    retry: int = 0
    handle: object | None = None      # asyncio.Task
    cancel_event: object | None = None
    simulated: bool = False
    provider: str = ""
    message: str = ""
    result: dict = field(default_factory=dict)

    def report(self, stage: str, progress: int, message: str = "") -> None:
        """更新阶段进度（由 task_loop 调用，持久化由 Scheduler 的回调完成）。"""
        self.stage = stage
        self.progress = max(0, min(100, int(progress)))
        if message:
            self.message = message

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "task_id": self.spec.task_id,
            "priority": int(self.spec.priority),
            "source": self.spec.source,
            "skill": self.spec.skill,
            "instruction": self.spec.instruction[:120],
            "enqueue_mode": self.spec.enqueue_mode,
            "dry_run": self.spec.dry_run,
            "status": self.status.value,
            "stage": self.stage,
            "progress": self.progress,
            "retry": self.retry,
            "started_at": self.started_at,
            "elapsed_s": round(now - self.started_at, 1) if self.started_at else 0.0,
            "pid": self.pid,
            "timeout_s": self.timeout_s,
            "simulated": self.simulated,
            "provider": self.provider,
            "message": self.message,
        }
