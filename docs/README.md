> **本版（Browserr + 本地 zen-gate）优先读**：DEPLOY.md（部署）→ DESIGN.md（设计）→ DECISION_ENGINE.md（决策引擎）→ MODULES.md（模块地图）→ AI_GUIDE.md（AI 维护指南）→ BROWSERR_ZENGATE.md（迁移与实测）。本文及 system_design/module_spec 等为原版文档，个别 Chromium 字样以新文档为准。

# Local-Muse 本地自治 Agent 系统

面向 ThinkPad L430 等老旧设备的**纯 Python 本地自治 Agent 系统**：Agent 自主决策、多条任务并发调度、
独立 Browserr 浏览器自动化、图文视频生成、敏感凭证安全隔离，全部通过仅本机可访问的 Flask Web 面板管理。

- **不使用 Docker，不使用任何容器虚拟化**：隔离只靠 Python `subprocess` 子进程 + 超时 + 独立目录。
- **浏览器隔离铁则**：只启动由 `setup` 脚本下载到项目内 `browserr_bin/` 的 Browserr（Servo）引擎；
  绝不接管、读取、唤醒本机 Chrome / Edge / Firefox，Cookie 与缓存只存在项目内 `browser_profile/`。
- **跨平台路径铁则**：全项目零硬编码绝对路径。运行期目录由 `core/paths.py` 基于 `__file__` 动态解析；
  一键脚本按自身位置定位项目根；安装脚本用相对路径 `BROWSERR_HOME=browserr_bin`，
  运行时由 `setup_browserr_env()` 统一注入解析结果，Windows / Linux 通用。
- **凭证隔离铁则**：`vault/` 独立凭证容器与生成模块完全解耦；只有决策引擎能申请会话级临时凭证，
  凭证只经 stdin 注入隔离 Browserr，绝不进入绘画 / 视频 prompt 或 API 请求体。
- **无公网暴露**：面板只监听 `127.0.0.1`。
- **API 密钥**：统一放 `config.json`，代码零硬编码。

---

## 一、四大板块

| 板块 | 目录 | 职责 |
| --- | --- | --- |
| 1 决策引擎 + 并发调度中心 | `agent_engine/` | 优先级队列、并发分发、思考链、Skill 提示词、失败复盘、prompt 自迭代、工作流编排 |
| 2 独立 Browserr + Vault | `browser_agent/` | 独立浏览器会话、会话级凭证注入、轻量子进程隔离、超时强杀 |
| 3 图文视频 API 聚合 | `media_generator/` | 本地源优先（唯一启用 `zen_gate_local`）+ 同档加权轮询、单服务商并发锁、令牌桶限速、429 冷却、下载与完整性校验 |
| 4 Flask 本机面板 | `web_panel/` | 对话工作台（三栏）、实时并发看板、全局配置、Skill 管理、API 源管理、Vault 连接器、实时日志、产物预览 |

另有 `core/` 共享基础层（配置 / SQLite 会话记忆 / 日志 / 硬件采样 / 数据契约），被四大板块单向依赖；
`scripts/` 为跨平台一键启动与运维脚本。

---

## 二、目录结构

```
local_muse/
├── main.py                  # 入口：调度 loop 线程 + Flask 主线程 + CLI
├── config.json              # 全局配置（含 API Key 空占位）、并发上限、硬件阈值
├── requirements.txt         # flask / browserr / requests / schedule / cryptography
├── .gitignore               # 忽略 browserr_bin/ vault/ browser_profile/ output/ logs/ db/*.db .venv/
├── core/                    # paths / config / models / db（含会话记忆表）/ logger / hardware
├── agent_engine/            # task_queue / planner / skill_loader / task_loop
├── browser_agent/           # vault / browser_runner / sandbox_wrapper / browser_worker
│   └── browser_profile/README.md   # 占位说明（真实数据在项目根级 browser_profile/）
├── media_generator/         # api_pool / image_gen / video_gen / downloader / media_worker
├── web_panel/               # app.py / bridge.py / templates（含 chat.html）/ static
├── scripts/                 # .bat / .sh / .ps1 一键脚本 + README.md
├── browserr_bin/             # 仅本项目使用的 Browserr 引擎 二进制（不入库）
├── browser_profile/         # 唯一 Browserr 用户数据根：session_<task_id>/（不入库）
├── vault/                   # Vault 加密凭证保险库：cards.json（Fernet）+ .key（不入库）
├── output/                  # 产物，按 task_id 分目录
├── logs/                    # 运行日志与 PID 文件
├── db/task.db               # SQLite（WAL）：tasks / prompt_history / api_usage / log_index / conversations / messages
└── docs/                    # 本套工程文档 + skills/
```

> 路径说明：需求中「项目内 `browser_profile/`」统一为**项目根级**目录；
> `browser_agent/browser_profile/` 只保留一个 README 占位说明，避免两处真实数据目录导致 Cookie 分散。
> `browserr_bin/`（Browserr 二进制）与 `browser_profile/`（用户数据）职责分离，都在项目根级且均不入库。
> 目录树里所有路径都是**相对项目根**的写法；代码与脚本中没有任何平台相关的硬编码绝对路径，
> 运行期由 `core/paths.py` 动态解析，Windows / Linux 通用。

---

## 三、分步启动教程

### 方式 A：一键脚本（推荐）

**Windows**

```bat
:: 1) 首次初始化（创建 .venv、安装依赖、下载独立 Browserr、环境自检）
scripts\setup.bat

:: 2) 启动（双击亦可）
scripts\start.bat
:: 或 PowerShell 版：powershell -ExecutionPolicy Bypass -File scripts\start.ps1

:: 3) 无需 API Key 的并行链路自检
scripts\dry_run_test.bat
```

**Linux / macOS**

```bash
chmod +x scripts/*.sh
bash scripts/setup.sh
bash scripts/start.sh
bash scripts/dry_run_test.sh
```

启动后浏览器打开 `http://127.0.0.1:8765`（端口以 `config.json` 的 `server.port` 为准）。

### 方式 B：手动分步

```bash
# 1. 建议 Python 3.10+（本项目在 Python 3.14.7 环境编写）
python --version

# 2. 创建虚拟环境
python -m venv .venv
# Windows: .venv\Scripts\python.exe    Linux: .venv/bin/python

# 3. 安装依赖（尽量精简，无额外重型框架）
.venv/bin/python -m pip install -r requirements.txt
# Windows 用：.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. 只把 Browserr（Servo）引擎下载到项目内 browserr_bin/（不碰系统浏览器，强制 sha256 校验）
.venv/bin/python scripts/install_browserr.py
# 等价于：bash scripts/install_browserr.sh（Windows：scripts\install_browserr.bat）

# 5. 环境自检
.venv/bin/python main.py --setup-check

# 6. 本地 API（唯一启用源 zen_gate_local）：先启动本机 zen-gate，再二选一填它的 mainKey：
#    环境变量 ZEN_GATE_API_KEY（推荐，key 不落文件），或 config.json 的
#    providers[zen_gate_local].api_key；其余远程源已全部 enabled=false，不会被调用

# 7. 启动调度器 + 面板
.venv/bin/python main.py

# 8. 打开面板 → 「并发看板」→ 提交并行任务；或命令行跑自检
.venv/bin/python main.py --dry-run-test --dry-run-count 3
```

此时 Browserr 落在**项目根级 `browserr_bin/`**，与系统日常浏览器完全无关；
程序由 `core/paths.py::setup_browserr_env()` 定位该目录（`BROWSERR_HOME`/`BROWSERR_ENGINE`），
`browser_runner.py` 从 `browserr_bin/driver.py` 载入官方 SDK，
再通过 `Driver(profile_dir=browser_profile/session_<task_id>)` 启动。

---

## 四、scripts/ 脚本速查

| 脚本 | 作用 |
| --- | --- |
| `setup.bat` / `setup.sh` | 首次初始化：venv + 依赖 + 把独立 Browserr 装到 `browserr_bin/` + `--setup-check` |
| `start.bat` / `start.sh` / `start.ps1` | 一键启动，参数透传给 `main.py` |
| `dry_run_test.bat` / `.sh` | 并行自检（不联网、不需要 Key、产物标注「模拟」） |
| `install_browserr.bat` / `.sh` | 单独安装 / 修复 `browserr_bin/` 内的独立 Browserr |
| `reset_browser.bat` / `.sh` | 二次确认后清空 `browser_profile/session_*` |
| `stop.bat` / `.sh` | 校验 PID 命令行后仅停止本项目服务 |

详见 `scripts/README.md`。

---

## 五、config.json 关键字段

| 分组 | 字段 | 默认 | 说明 |
| --- | --- | --- | --- |
| `server` | `host` / `port` | `127.0.0.1` / `8765` | 非回环 host 会被强制改写，杜绝公网暴露 |
| `scheduler` | `daily_task_limit` | `10` | 每日定时常规任务数量上限 |
| | `max_concurrency` | `2` | **全局最大并发数**（L430 建议 1–2） |
| | `default_api_max_concurrency` | `1` | 新服务商默认并发配额（面板可逐个改） |
| | `interval_seconds` | `300` | 定时触发间隔 |
| | `retry_times` | `2` | 失败自动重试次数 |
| | `task_timeout_seconds` | `600` | 单任务总超时，超时强杀子进程树 |
| | `manual_enqueue_mode` | `queue` | 手动任务默认 `queue` 排队 / `insert` 插队 |
| | `subprocess_memory_limit_mb` | `512` | Linux 子进程地址空间上限；Windows 依赖并发与阈值降级 |
| `hardware` | `cpu_threshold` / `memory_threshold` | `85` / `85` | 超阈值暂停派发新任务 |
| | `sample_interval_seconds` | `2` | 采样周期 |
| | `recovery_margin` | `10` | 滞回，防临界抖动 |
| `browser` | `enabled` / `headless` | `true` / `true` | 自动化开关、无头/有头 |
| | `timeout_seconds` / `navigation_timeout_ms` | `60` / `30000` | 浏览器任务与页面导航超时 |
| | `per_session_profile` | `true` | 每任务独立用户数据目录 |
| `upload` | `enabled` | `false` | 可选上传，默认关闭 |
| `providers[]` | `name/kind/style/base_url/path/model/api_key/enabled/priority/weight/rpm/max_concurrency` | — | 每个免费源的端点、密钥、优先级、轮询权重、限速与并发配额；`zen_gate_local` 默认 `priority=100` 为唯一启用源，其余远程源已禁用 |

面板「全局配置」保存后原子写入并热更新（端口变更需重启）；面板只显示密钥是否已配置，绝不回显正文。

---

## 六、L430 并发优化参数（推荐）

| 项 | 推荐值 | 理由 |
| --- | --- | --- |
| 全局最大并发 `max_concurrency` | **2**（内存 < 4GB 时改 **1**） | 单个 Browserr 实例约 150–300MB，2 条已接近老旧设备舒适上限 |
| 单服务商并发上限 | **1** | 避免同服务商并发触发 429 |
| 文本 / 图像 / 视频 RPM | 6 / 3 / 2 | 令牌桶限速，宁慢勿封 |
| 每日任务数 | **10**（可下调至 3–5） | 减少长时间占用与免费额度消耗 |
| 定时执行间隔 | `300` 秒以上 | 避免密集入队 |
| 浏览器无头模式 | `true` | 有头模式更耗资源 |
| 浏览器任务超时 | `60` 秒 | 卡住即杀，不拖垮整机 |
| 单任务总超时 | `600` 秒 | 视频长轮询兜底 |
| 重试次数 | `2` | 硬性要求；退避 1s / 2s |
| CPU / 内存警戒线 | `85%` / `85%`，滞回 `10%` | 超限只暂停派发新任务，不杀运行中任务 |
| 子进程内存上限 | `512` MB（Linux） | Windows 无强隔离，靠并发与阈值降级 |
| `extra_args` | `--disable-dev-shm-usage --no-first-run --disable-extensions` | 减少共享内存与扩展开销 |

若出现整机卡顿：先把 `max_concurrency` 降到 1，再把采样间隔调小到 1 秒、内存警戒线降到 75%。
详见 `troubleshooting.md`。

---

## 七、面板功能

1. **并发任务看板**（首页）：批量提交并行任务、优先级/插队选择、运行中任务进度与 PID、单独终止指定 task_id、队列与历史、产物预览。
2. **全局配置**：并发上限、每日任务数、重试、浏览器无头/有头与超时、硬件阈值、API Key、vault 凭证卡片。
3. **Skill 管理**：查看 / 新增 / 编辑 / 删除 `docs/skills/*.md`。
4. **API 源与浏览器会话**：启停、权重、并发配额、当前并发数、冷却倒计时、额度消耗日志、并行 Browserr 会话列表、一键重置环境。
5. **实时日志与产物**：按 task_id / 级别过滤，日志带任务编号，图片 / 视频 / dry_run 清单预览。

底部状态条常驻显示 CPU、内存、活跃并发、排队数、降级状态与监听地址。

---

## 八、自检模式（dry_run）

未配置任何 API Key 时也能验证完整链路：提交任务时勾选「自检模式」，或运行 `--dry-run-test`。

- 依然会执行：优先级入队 → 并发分发 → 决策与 Skill → **独立浏览器子进程**（自检时不联网/不启动 Browserr）
  → 生成子进程 → 下载与完整性校验 → SQLite 按 task_id 落库 → 独立复盘。
- 图片产物为 1×1 占位 PNG，视频只写 `simulated_video.json` 清单，**不伪造 mp4**。
- 任务记录 `simulated = true`，日志与面板均标注「模拟」，绝不冒充真实生成成功。

---

## 九、文档索引

| 文档 | 内容 |
| --- | --- |
| `README.md` | 本文件：说明、启动教程、脚本、配置、L430 参数 |
| `system_design.md` | 总体架构、四大模块职责、数据流、状态流转、并发调度模型、约束清单 |
| `module_spec.md` | 每个模块的接口、入参出参、返回值、并发接口定义、CLI 契约 |
| `workflow.md` | 全链路工作流、文字版时序、并发任务流转、失败与降级路径 |
| `security.md` | vault 凭证隔离、进程隔离、隐私保护、API 风控、并发安全约束 |
| `api_list.md` | 全部免费 API 的地址、额度、限速、调用文档与风险提示 |
| `troubleshooting.md` | L430 常见故障：并发卡死、并发超限、多 Browserr 资源占用、429、脚本启动失败 |
| `skills/*.md` | 三套内置 Agent 技能与 System Prompt |

---

## 十、安全提醒

- 填写真实 API Key 后不要提交 `config.json` 到公开仓库；`.gitignore` 已忽略运行期数据、`browserr_bin/` 与 `vault/`。
- 不要把面板通过内网穿透、端口映射暴露到公网；程序已强制只绑定回环地址。
- `browser_profile/` 里的会话 Cookie 属于隔离 Browserr，清理用面板「一键重置」或 `reset_browser.*` 脚本；
  `browserr_bin/` 只放二进制，可整目录删除后用 `install_browserr.*` 重装，不要手工指向系统浏览器。
- `vault/.key` 是 Fernet 密钥，与 `cards.json` 同目录；不要提交、不要分享整个 `vault/` 目录。

---

## 十一、对话工作台与 Vault

首页 `/` 是三栏对话工作台，原并发看板移到 `/board`：

| 区域 | 能力 |
| --- | --- |
| 左侧会话 | 新建 / 切换 / 重命名 / 删除会话；列表来自 SQLite，重启后不丢 |
| 中间对话 | 用户指令与 Agent 回执气泡；下方可选技能、优先级、并发条数、自检模式 |
| 右侧当前执行 | 本会话任务的 `stage` / `progress` 进度条、状态，以及最近阶段日志 |
| 栏收起 | 顶部「◧ 会话 / ◨ 执行」或各栏 `‹` / `›` 按钮可分别收起左右栏，中间自动占满；状态存 `localStorage`，刷新后保持 |

**SQLite 持久化记忆**：`db/task.db` 新增 `conversations` 与 `messages` 表，`tasks` 增加
`conversation_id` 列（老库启动时自动 `ALTER TABLE` 迁移）。`TaskDB` 提供
`create_conversation / list_conversations / add_message / list_messages /
conversation_context / list_tasks_by_conversation`；`conversation_context()` 只取 `role/content`，
不含任何凭证。`planner.build_plan()` 会把当前会话最近 8 条消息作为「会话上下文」
注入文本模型调用，因此切换会话不会串上下文；`dry_run` 不调用模型，仅使用复盘历史。

**Vault 自定义连接器**：`vault/` 是凭证保险库，不是钱包。配置页「Vault 自定义连接器」
按类型动态渲染结构化字段：API 密钥、用户名 / 密码、Cookie、Bearer Token、自定义请求头、LocalStorage。
全部内容以 Fernet 认证加密写入 `vault/cards.json`，面板只回显脱敏摘要；
`cryptography` 为必需依赖（已写入 `requirements.txt`），缺失时拒绝启动。
旧版 `wallet/cards.json` 明文会在启动时自动迁移、加密并删除旧明文文件。
