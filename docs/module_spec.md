# 模块接口规范（module_spec）

本文与代码一一对应，是新增技能、扩展 API、二次开发时的接口契约。
所有路径以项目根为基准且一律用相对写法；代码与脚本中没有任何平台相关的硬编码绝对路径，
运行期由 `core/paths.py` 基于 `__file__` 动态解析，Windows / Linux 通用。
`dict` 返回值均为可 JSON 序列化结构。

---

## 1. 数据契约 `core/models.py`

```python
class TaskPriority(IntEnum):
    HIGH_TEST = 0; MANUAL = 1; SCHEDULED = 2

class TaskStatus(str, Enum):
    QUEUED="queued"; RUNNING="running"; RETRYING="retrying"
    SUCCEEDED="succeeded"; FAILED="failed"; TIMEOUT="timeout"
    CANCELLED="cancelled"; PAUSED="paused"

@dataclass TaskSpec:
    task_id: str; priority: TaskPriority; source: str; enqueue_mode: str
    skill: str; instruction: str; created_at: float; seq: int = 0; dry_run: bool = False
    # classmethod create(priority, source, enqueue_mode, skill, instruction, dry_run=False) -> TaskSpec
    # to_dict() -> dict

@dataclass TaskRuntime:
    spec: TaskSpec; status: TaskStatus = QUEUED; stage: str = "init"
    progress: int = 0; started_at: float = 0.0; finished_at: float = 0.0
    pid: int | None = None; timeout_s: int = 600; retry: int = 0
    handle: object | None = None; cancel_event: object | None = None
    simulated: bool = False; provider: str = ""; message: str = ""; result: dict = {}
    # report(stage, progress, message="") -> None
    # snapshot() -> dict（面板展示用）

@dataclass ProviderConfig:
    name, kind, base_url, path, style, model, api_key, enabled,
    weight, priority, rpm, max_concurrency, timeout_s, doc, quota_note, risk_note
```

---

## 2. `core/` 共享基础层

### `core/paths.py`

| 函数 / 常量 | 入参 | 返回 | 说明 |
| --- | --- | --- | --- |
| `ROOT` / `DIRS` / `DB_PATH` / `CONFIG_PATH` / `LOG_PATH` / `PID_PATH` | — | `Path` | 由 `__file__` 动态解析，全部指向项目内目录，绝不指向系统浏览器；`DIRS["browserr_bin"]` 是项目内 Browserr 二进制目录 |
| `ensure_dirs()` | — | `None` | 幂等创建全部运行期目录（含 `browserr_bin/`） |
| `setup_browserr_env()` | — | `Path` | 在**任何** `import browserr` 之前把 `BROWSERR_HOME` 指向项目内 `browserr_bin/`；主进程、自检与浏览器子进程均调用，路径动态解析、跨平台 |
| `rel(path)` | `Path` | `str` | 把项目内路径显示为相对项目根的 `/` 分隔形式，用于日志与自检输出 |
| `safe_id(task_id)` | `str` | `str` | 收敛为文件名安全字符，防路径穿越 |
| `session_dir(task_id)` | `str` | `Path` | `browser_profile/session_<safe_id>/`，独立 Browserr 用户数据 |
| `output_dir(task_id)` | `str` | `Path` | `output/<safe_id>/` |
| `list_sessions()` | — | `list[dict]` | `{name, task_id, path, size_mb}`，`path` 为相对项目根的形式 |
| `reset_browser_env()` | — | `int` | 删除 `session_*` 目录数，保留根目录 |
| `subprocess_options()` | — | `dict` | Windows `CREATE_NEW_PROCESS_GROUP`；POSIX `start_new_session` |
| `apply_worker_memory_limit(mb)` | `int` | `None` | 仅 POSIX 生效，子进程内调 `RLIMIT_AS` |
| `kill_process_tree(pid)` | `int` | `bool` | Windows `taskkill /T /F`；POSIX `killpg` |

### `core/config.py::ConfigManager`

| 方法 | 入参 | 返回 | 说明 |
| --- | --- | --- | --- |
| `__init__(path=None)` | `Path` | — | 自动 `load()` |
| `load()` / `get()` | — | `dict` | 缺省合并 + ProviderConfig 规范化；`get()` 返回深拷贝 |
| `bootstrap()` | — | `None` | 首次运行生成 `config.json` 骨架 |
| `section(name)` | `str` | `dict` | 读取一个配置分组 |
| `providers(kind=None)` | `str` | `list[dict]` | 按类型过滤服务商 |
| `provider(name)` | `str` | `dict \| None` | 单个服务商 |
| `masked()` | — | `dict` | **脱敏视图**：`api_key` → `******` / 空，附 `has_key` |
| `save(new_data, preserve_secrets=True)` | `dict` | `dict` | 原子写（`*.tmp` + `os.replace`）；`******`/空 Key 保持原值 |
| `reload_if_changed()` | — | `bool` | mtime/size 变化则热更新 |

### `core/db.py::TaskDB`

| 方法 | 入参 | 返回 | 说明 |
| --- | --- | --- | --- |
| `init_schema()` | — | `None` | 建 `tasks` / `prompt_history` / `api_usage` / `log_index` + 索引 |
| `upsert_task(record)` | `dict`（字段白名单） | `None` | `ON CONFLICT(task_id) DO UPDATE`，短事务 |
| `update_task(task_id, **fields)` | — | `None` | 局部更新，自动序列化 `result_json` |
| `get_task(task_id)` / `list_tasks(limit, status)` | — | `dict` / `list[dict]` | 任务查询 |
| `count_today(source=None)` | `str` | `int` | 每日任务上限判定 |
| `stats()` | — | `dict` | 各状态计数 |
| `add_prompt_history(task_id, skill, prompt, reflection, score)` | — | `int` | 复盘记录 id |
| `recent_prompts(skill=None, limit=8)` | — | `list[dict]` | 同技能历史 |
| `add_api_usage(...)` / `list_api_usage(limit)` | — | — / `list[dict]` | 额度消耗日志 |
| `add_log(...)` / `list_logs(...)` | — | — / `list[dict]` | WARNING 以上日志索引 |

连接参数：`sqlite3.connect(..., timeout=10, check_same_thread=False)` + `journal_mode=WAL` +
`busy_timeout=5000` + `synchronous=NORMAL`，全部操作带 `RLock`。

### `core/logger.py`

| 函数 | 说明 |
| --- | --- |
| `redact(text)` | 对 `api_key/token/secret/password/cookie/authorization/bearer`、`sk-*`、`?key=` 做脱敏 |
| `bind(task_id, stage)` / `set_stage(stage)` / `current_task_id()` | `contextvars` 注入，`asyncio.to_thread` 自动复制上下文 |
| `setup_logging(log_path) -> RingHandler` | 控制台 + 滚动文件 + 内存环形缓冲（上限 2000） |
| `RingHandler.attach_db(db)` | WARNING 以上写 `log_index` |
| `snapshot(limit, task_id, level, since) -> list[dict]` | 面板实时日志流 |
| `get_logger(name)` | 标准 `logging.Logger` |

日志格式：`时间 | 级别 | task=<task_id> | <stage> | 已脱敏消息`。

### `core/hardware.py::HardwareMonitor`

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `sample()` | `dict` | `{cpu_percent, memory_percent, memory_used_mb, memory_total_mb, platform, sampled_at}` |
| `update()` | `dict` | 采样 + 滞回降级判定，附 `degraded / degraded_changed / reason / 阈值` |
| `degraded()` | `bool` | 当前是否降级 |
| `snapshot()` | `dict` | 最近一次采样 |

优先 `psutil`（可选）；否则 Linux `/proc/stat` `/proc/meminfo`，Windows `GetSystemTimes` / `GlobalMemoryStatusEx`。

---

## 3. 板块 1 决策引擎

### `agent_engine/task_queue.py::Scheduler`

```python
Scheduler(config, db, hardware, pool, vault)

async start(enable_schedule=True) -> None
async stop() -> None
async submit(spec: TaskSpec) -> str                 # 非阻塞，立即返回 task_id
async cancel(task_id: str) -> bool                  # 只终止该任务
def set_hardware_gate(enabled: bool) -> None        # 自检模式临时关闭硬件降级门禁
def active_snapshot() -> list[dict]
def queue_snapshot() -> list[dict]
def queue_size() -> int
def degraded() -> bool
def snapshot() -> dict                             # 面板 /api/status 数据源
def register_pid(task_id, pid) -> None
def unregister_pid(task_id, pid=None) -> None
```

`snapshot()` 返回：`active / queued / active_count / queue_size / max_concurrency / degraded /
hardware / stats / today / daily_limit / providers`。

**并发接口约定**

- 调度主循环 `_dispatch_loop()` 只做 `get_nowait()` + `create_task()`，绝不 `await` 业务子任务。
- `active` 用 `dict + RLock` 而非纯 Semaphore，便于零查询快照与单独取消。
- `_services` 是传给业务流水线的一组依赖（见第 7 节），在 `Scheduler.__init__` 构造，全任务共享但无可变跨任务状态。

### `agent_engine/planner.py`

| 函数 | 入参 | 返回 | 说明 |
| --- | --- | --- | --- |
| `build_plan(services, spec)` | `dict, TaskSpec` | `dict` | `{prompt, style, topic, steps, needs_browser, reference_image, source, provider}`；文本模型失败降级模板；dry_run 标 `source="dry_run"` |
| `reflect(services, runtime, plan, result, succeeded=True)` | — | `dict` | 写 `prompt_history`，返回 `{task_id, skill, topic, style, score, reflection, next_prompt_hint}`；`succeeded` 由调用方显式传入，因为 `execute()` 返回前 `runtime.status` 仍是 RUNNING |
| `_template_plan(spec, skill, history)` | — | `dict` | 确定性模板 |
| `_extract_json(text)` | `str` | `dict \| None` | 从模型输出提取 JSON |

约定：技能名含 `research` 时模板 `needs_browser=True`（对应 `web_research.md`）。

### `agent_engine/skill_loader.py::SkillLoader`

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `list_skills()` | `list[dict]` | `{name, title, description, enabled, updated_at, chars, path}` |
| `get(name)` | `dict` | 附 `meta / body / raw` |
| `save(name, content)` | `dict` | 原子写，限 200KB |
| `delete(name)` | `bool` | 三个内置技能禁止删除 |
| `default_skill()` / `enabled_names()` | `str` / `list[str]` | 定时任务轮选用 |

文件名白名单 `^[A-Za-z0-9_\-]{1,64}$`，并校验 `resolve()` 仍在 `docs/skills/` 内。

### `agent_engine/task_loop.py`

```python
async execute(runtime: TaskRuntime, services: dict) -> dict
```

返回 `result`：`{task_id, simulated, started_at, finished_at, assets[], materials[], stages[],
plan{}, browser_session, upload{}, reflection{}}`。

阶段与进度：`plan 10 → browser 25（可跳过）→ image 50 → video 72 → upload 86（可跳过）
→ persist 92 → reflect 97 → done 100`。异常直接上抛，由 Scheduler 统一重试。

辅助：`_report(services, runtime, stage, progress, message)`、`_pid_hook(services, runtime, task_id)`。

---

## 4. 板块 2 浏览器与凭证

### `browser_agent/vault.py::Vault`

**这是 Vault（凭证保险库），不是 wallet（钱包）**：只存第三方站点的登录 / 调用凭证，与货币无关。

| 方法 | 入参 | 返回 | 说明 |
| --- | --- | --- | --- |
| `connector_schema()` | — | `dict` | 6 类自定义连接器的字段定义（无凭证值），供面板动态渲染表单 |
| `list_cards()` | — | `list[dict]` | 仅脱敏摘要 `{name, kind, kind_label, note, enabled, updated_at, masked, encrypted}` |
| `add_card(name, kind, fields, note="", secret="")` | `dict` | `dict` | 按连接器 schema 校验结构化字段，加密写入后返回脱敏结果 |
| `delete_card(name)` / `toggle_card(name, enabled)` | — | `bool` | 删除 / 启停 |
| `engine_token()` | — | `str` | **仅决策引擎持有**的进程内随机令牌 |
| `issue_session_credential(task_id, card_names, engine_token, ttl_s=300)` | — | `dict` | 校验令牌后签发 `{session_id, task_id, expires_at, cookies[], headers{}, storage{}, form_login[]}` |
| `revoke(task_id)` | — | `None` | 立即销毁该会话凭证 |
| `sessions_snapshot()` | — | `list[dict]` | 仅会话编号与计数，无凭证正文 |
| `panel_view()` | — | `VaultPanelView` | 面板专用受限视图 |

`VaultPanelView` 只暴露 `connector_schema / list_cards / add_card / delete_card / toggle_card /
sessions_snapshot`，**不含** `engine_token()` 与 `issue_session_credential()`；`main.py` 注入面板的是该视图，
`Scheduler` 持有的才是真实 `Vault`，确保只有决策引擎能签发临时凭证。

连接器类型（`CONNECTORS`）：`api_key`（API 密钥 + 请求头名 + 前缀）、`username_password`
（用户名 / 密码 / 登录页 URL / 三个选择器）、`cookie`、`bearer_token`、`http_header`、`local_storage`。
旧类型 `cookie/token/header/storage/login` 在加载时自动映射并拆成结构化字段。

**加密铁则**：`cryptography` 为必需依赖，缺失时 `Vault()` 直接拒绝启动，绝不降级为明文；
`cards.json` 固定为 `{"v": 2, "encrypted": true, "data": <Fernet 密文>}`，密钥文件 `vault/.key`
在 Linux 下权限 `0600`。旧的明文 `wallet/cards.json` 会在启动时迁移到 `vault/`、升级为加密，
并删除旧明文文件；`form_login` 只在浏览器子进程内用于 `page.fill/click`，不落盘、不进日志。

### `browser_agent/sandbox_wrapper.py`

```python
async run_worker(kind: str, payload: dict, timeout_s: int,
                 task_id: str, on_pid=None) -> dict
```

- `kind`：`"browser"` → `browser_agent/browser_worker.py`；`"media"` → `media_generator/media_worker.py`。
- payload 经 **stdin** JSON 传入；stdout 只解析最后一行 `LM_RESULT_JSON:{...}`。
- 返回 `{ok, error?, timeout?, exit_code, pid, ...}`；超时强杀进程树。**永不抛异常**（除 `CancelledError`）。

### `browser_agent/browser_runner.py`

```python
run_browser_job(job: dict) -> dict
```

`job`：`{task_id, dry_run, headless, timeout_seconds, navigation_timeout_ms, memory_limit_mb,
user_data_dir, extra_args, credentials, actions[]}`。

`actions[].type` 支持 `goto / fill / click / extract / screenshot / wait`。
返回 `{ok, simulated, session, materials[], screenshots[], error?}`。

**隔离铁则**：二进制只来自项目内 `browserr_bin/`（由 `paths.setup_browserr_env()` 在任何
`import browserr` 之前注入 `BROWSERR_HOME`）；只调用
`Driver(profile_dir=browser_profile/session_<task_id>)`；
不指定系统浏览器通道、不指定外部可执行路径、不连接已有浏览器进程。

### `browser_agent/browser_worker.py`

CLI：`python browser_agent/browser_worker.py`，stdin 一次性 JSON，stdout 一行结果；
`contextlib.redirect_stdout(sys.stderr)` 保证 stdout 不被第三方输出污染。

---

## 5. 板块 3 图文视频生成

### `media_generator/api_pool.py::ApiPool`

```python
ApiPool(config, db=None)

def available(name) -> bool
async pick(kind: str, exclude: set[str]) -> str | None
async acquire(provider: str) -> None
async release(provider: str) -> None
async report_429(provider: str, retry_after: int = 30) -> None
def note_usage(provider, kind, task_id, status, latency_ms=0, note="") -> None
def build_text_request(provider: dict, messages: list[dict]) -> dict
async call_text(provider: str, messages: list[dict], task_id: str) -> str
def build_generation_request(kind, provider_name, prompt, task_id,
                             reference_image=None) -> dict
def snapshot() -> dict[str, dict]
def usage_snapshot(limit=60) -> list[dict]
```

`pick()` 先按 `priority` 取最高档的可用源，再在档内按 `weight` 加权轮询；首选源缺 Key /
被禁用 / 429 冷却时自动降级到下一档。

`snapshot()` 每项：`{name, kind, enabled, available, has_key, weight, priority, rpm, max_concurrency,
inflight, cooldown_s, calls, doc, quota_note, risk_note}`。

`build_generation_request` 返回：`{kind, provider, style, task_id, dry_run:False, url, headers, body,
poll_url, poll_headers, poll_interval_s, max_polls, timeout_s, prompt_preview, memory_limit_mb}`。
`memory_limit_mb` 取自 `scheduler.subprocess_memory_limit_mb`，供子进程设置 POSIX 地址空间上限。
本地参考图读成 `data:image/png;base64,...` 填入 `body.first_frame_image`。

### `media_generator/image_gen.py` / `video_gen.py`

```python
async image_gen.generate(services, task_id, prompt, dry_run=False,
                         reference_image=None, on_pid=None) -> dict
async video_gen.generate(services, task_id, prompt, reference_image=None,
                         dry_run=False, on_pid=None) -> dict
```

共用 `_run_generation(services, kind, task_id, prompt, dry_run, reference_image, on_pid)`。
返回 `{kind, path, provider, simulated, verify, pid, url?, note?}`；
无可用源或 `dry_run` 时 `provider="dry_run"`、`simulated=True`，产物明确标注模拟。

### `media_generator/downloader.py`（同步，子进程内调用）

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `verify(path)` | `dict` | `{exists, size, kind, verified}`；魔数识别 PNG/JPEG/GIF/WebP/MP4/WebM |
| `save_bytes(data, dest)` | `dict` | `.part` 原子落盘 + 校验 |
| `save_base64(text, dest)` | `dict` | 容忍 `data:` 前缀 |
| `fetch(url, dest, timeout=60)` | `dict` | 流式下载 + 429 判定 + 完整性校验 |
| `make_simulated_asset(task_id, kind, prompt)` | `dict` | 图片占位 PNG / 视频只写 JSON 清单，并更新 `dry_run_manifest.json` |

### `media_generator/media_worker.py`

CLI：`python media_generator/media_worker.py`。`handle(payload)` 处理三种响应形态：
URL → 下载、base64 → 保存、`task_id` → `_poll()` 异步轮询；返回统一附加
`{ok, kind, provider, simulated, path?, verify?, error?, status?}`。

---

## 6. 板块 4 Web 面板

### `web_panel/bridge.py::SchedulerBridge`

```python
SchedulerBridge(scheduler)
submit(spec) -> str; cancel(task_id) -> bool
active_snapshot() / queue_snapshot() / snapshot() / degraded()
```

内部用 `asyncio.run_coroutine_threadsafe(coro, scheduler.loop).result(timeout)`；调度器未就绪抛 `RuntimeError`。

### `web_panel/app.py::create_app(ctx) -> Flask`

`ctx` 键：`config / db / ring / hardware / pool / vault / scheduler / bridge / version / host / port / app`。

| 页面路由 | 模板 |
| --- | --- |
| `GET /` | `dashboard.html` |
| `GET /config` | `config.html` |
| `GET /skills` | `skills.html` |
| `GET /sources` | `sources.html` |
| `GET /logs` | `logs.html` |

| JSON 接口 | 方法 | 入参 | 返回要点 |
| --- | --- | --- | --- |
| `/api/status` | GET | — | 并发快照 + 硬件 + 统计 + 服务商状态 |
| `/api/tasks` | GET | — | `active / queued / history` |
| `/api/tasks/submit` | POST | `{count, priority, enqueue_mode, skill, instruction, dry_run}` | `{ok, task_ids, count}` |
| `/api/tasks/<id>/cancel` | POST | — | `{ok, task_id}` |
| `/api/logs` | GET | `since, task_id, level, limit` | `{ok, now, logs[]}` |
| `/api/config` | GET / POST | 配置分组 + `providers[]` | 脱敏配置 / 保存结果 |
| `/api/vault/connectors` | GET | — | 6 类连接器字段定义（无凭证值） |
| `/api/vault` | GET / POST | `{name, kind, fields{}, note}`（兼容 `secret`） | 脱敏卡片列表 / 新增结果 |
| `/api/vault/<name>` | DELETE | — | `{ok}` |
| `/api/vault/<name>/toggle` | POST | `{enabled}` | `{ok}` |
| `/api/wallet*` | — | 旧路径兼容别名 | 转发到 `/api/vault*`，兼容浏览器缓存中的旧前端 |
| `/api/conversations` | GET / POST | `{title}` | 会话列表 / 新建会话 id |
| `/api/conversations/<id>` | GET / POST | `{title}` / `{action: rename / archive / delete}` | 消息、任务与阶段进度 / 更新结果 |
| `/api/conversations/<id>/messages` | POST | `{content}` | 追加一条消息 |
| `/api/skills` | GET | — | `{ok, skills[]}` |
| `/api/skills/<name>` | GET / POST / DELETE | `{content}` | 技能详情 / 保存 / 删除 |
| `/api/sources` | GET | — | 源状态 + 消耗日志 |
| `/api/sources/<name>` | POST | `{enabled, weight, priority, rpm, max_concurrency, api_key?}` | 更新后的源列表 |
| `/api/sessions` | GET | — | Browserr 会话 + vault 会话 |
| `/api/sessions/reset` | POST | — | 有活跃任务时返回 409 |
| `/api/outputs` | GET | — | 最近 60 个产物（含预览 URL） |
| `/output/<task_id>/<filename>` | GET | — | 本地文件，`safe_id` + `secure_filename` 防穿越 |

统一错误：`{"ok": false, "error": "<脱敏信息>"}` + HTTP 4xx/5xx。

---

## 7. `services` 依赖契约（`Scheduler._services`）

| 键 | 类型 | 用途 |
| --- | --- | --- |
| `config` | `ConfigManager` | 读取配置 |
| `db` | `TaskDB` | 落库与历史 |
| `pool` | `ApiPool` | 选源与限流 |
| `vault` | `Vault` | 凭证容器（仅签发/撤销） |
| `hardware` | `HardwareMonitor` | 硬件采样 |
| `skill_loader` | `SkillLoader` | 技能读写 |
| `engine_token` | `str` | 仅决策引擎持有，签发凭证必需 |
| `register_pid` / `unregister_pid` | callable | 子进程 PID 登记 |
| `on_report` | callable | 阶段进度持久化 |

`services` 本身**不可被业务任务修改**，避免并发任务间状态串扰。

---

## 8. `main.py` CLI 契约

```
python main.py [--setup-check] [--dry-run-test] [--dry-run-count N]
               [--host 127.0.0.1] [--port 8765]
```

| 参数 | 说明 |
| --- | --- |
| `--setup-check` | 只读环境检查（依赖、目录、配置、DB、独立 Browserr），返回退出码 0/1 |
| `--dry-run-test` | 提交 N 条并行自检任务，等待完成并打印每个 task_id 状态，不启动面板 |
| `--host` | 非回环地址强制改写为 `127.0.0.1` |
| `--port` | 默认取 `config.server.port` |

退出：写 `logs/local_muse.pid`；信号/atexit 时取消全部子任务、清理子进程树、删除 PID 文件。

---

## 9. 子进程返回码约定

| 码 | 含义 |
| --- | --- |
| 0 | 成功（`ok=True`） |
| 1 | 业务失败（`ok=False`，附 `error`） |
| 2 | 参数解析失败 |
| 超时 | `sandbox_wrapper` 强杀进程树，返回 `{ok:False, timeout:True}` |
| 429 | `{ok:False, status:429}`，由 `ApiPool.report_429` 冷却该源 |
