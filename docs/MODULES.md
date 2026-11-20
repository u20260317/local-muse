# 模块地图（MODULES）

> 全项目每个 Python 模块一行账：职责 / 对外接口 / 依赖 / 禁区。
> 源码里每个文件头部都有对应的「【核心模块】」中文横幅，与本表一一对应；
> 拆改某个模块时，先看本表确认它的契约，再动手。

## 决策引擎层 agent_engine/

| 模块 | 职责 | 对外接口 | 依赖 | 禁区 |
|---|---|---|---|---|
| task_queue.py | 调度中枢 | `Scheduler.start/stop/submit/cancel/snapshot`、`register_pid/unregister_pid` | core.models、skill_loader、task_loop、schedule | 主循环不许 await 业务；并发/超时只从 config 读 |
| planner.py | 计划与反思 | `build_plan(services, spec)`、`reflect(services, runtime, plan, result, succeeded)` | services 内的 db/pool/skill_loader | 计划阶段不许抛错阻断任务；prompt 不许含凭证 |
| task_loop.py | 单任务流水线 | `execute(runtime, services)` | planner、sandbox_wrapper、image_gen、video_gen、paths | 凭证签发/revoke 链不许删；阶段必须经 _report 上报 |
| skill_loader.py | Skill 库读写 | `SkillLoader.get/list_skills/save/delete/enabled_names/default_skill` | core.paths | 文件名必须过 NAME_RE + resolve 父目录校验；内置技能不可删 |

## 生成线 media_generator/

| 模块 | 职责 | 对外接口 | 依赖 | 禁区 |
|---|---|---|---|---|
| api_pool.py | 选源/限流/熔断/请求构造 | `ApiPool.pick/acquire/release/report_429/call_text/build_generation_request/snapshot` | core.logger、requests（线程内） | Key 不许进日志；HTTP 不许在主循环同步执行 |
| image_gen.py | 文生图调度 | `generate(services, task_id, prompt, dry_run, reference_image, on_pid)` | sandbox_wrapper、pool | 不许 import vault；无源必须走模拟并标注 |
| video_gen.py | 文生/图生视频调度 | `generate(...)`（复用 image_gen._run_generation） | image_gen | 不要另写一套选源逻辑 |
| media_worker.py | 生成子进程入口 | stdin JSON → stdout `LM_RESULT_JSON:` | downloader | 只回一行结果；其余输出去 stderr |
| downloader.py | 下载与校验 | `fetch/save_bytes/save_base64/verify/make_simulated_asset` | 标准库 | 必须 .part 原子改名 + 魔数校验；模拟产物必须明示 |

## 浏览器线 browser_agent/

| 模块 | 职责 | 对外接口 | 依赖 | 禁区 |
|---|---|---|---|---|
| browser_runner.py | **唯一启动浏览器处** | `run_browser_job(job) -> result` | browserr_bin/driver.py（动态载入） | 不许指定系统浏览器通道；headers 凭证无注入口时必须写 material 说明，不许假装注入 |
| browser_worker.py | 浏览器子进程入口 | stdin job → stdout `LM_RESULT_JSON:` | browser_runner、paths | **不许设 RLIMIT_AS**（Servo 需 >4GB 虚拟地址，512MB 必死） |
| sandbox_wrapper.py | 子进程隔离包装 | `run_worker(kind, payload, timeout_s, task_id, on_pid)` | asyncio 子进程 | 参数只走 stdin；超时必须杀整棵进程树 |
| vault.py | 加密凭证保险库 | `Vault.issue_session_credential/revoke/list_cards/engine_token`、`VaultPanelView` | cryptography(Fernet) | 禁明文落盘/降级；只有决策引擎可签发；面板只见脱敏摘要 |

### 浏览器 job 契约（runner 的输入输出，改字段先改这里）

输入 job dict：`task_id, dry_run, headless, timeout_seconds, navigation_timeout_ms,
user_data_dir, extra_args, credentials{cookies,headers,storage,form_login}, actions[]`。
action 类型：`goto{url} / fill{selector,value} / click{selector} / extract{selector} /
screenshot{name} / wait{ms}`。
输出 result dict：`ok, simulated, engine:"browserr", session, materials[], screenshots[], error?`。
materials 条目：`{type:"url"|"text"|"note", source, value}`。

## 基础层 core/

| 模块 | 职责 | 对外接口 | 禁区 |
|---|---|---|---|
| models.py | 全项目数据契约 | TaskSpec/TaskRuntime/TaskStatus/TaskPriority/ProviderConfig | 改字段必须全局搜引用并同步 db 落库 |
| config.py | 配置管理 | `ConfigManager.section/provider/providers/reload_if_changed`、脱敏视图 | api_key 不许出现在任何返回值给面板的字段里（只给 has_key） |
| db.py | SQLite 数据层 | TaskDB（tasks/prompt_history/api_usage/log_index/conversations/messages） | 短事务；按 task_id 隔离；不要在调度主循环里跑慢查询 |
| hardware.py | 硬件采样降级 | `HardwareMonitor.update()/snapshot()` | 阈值带滞回（recovery_margin），不要改成单点阈值 |
| logger.py | 日志与脱敏 | `get_logger/bind/set_stage/redact/snapshot` | 新增日志先想脱敏：Key/Cookie/凭证一律走 redact |
| paths.py | 路径与引擎定位 | `DIRS/session_dir/output_dir/setup_browserr_env/browserr_ready/kill_process_tree` | 运行期目录只许在项目内；不要拼用户主目录路径 |

## 面板层 web_panel/ 与入口

| 模块 | 职责 | 对外接口 | 禁区 |
|---|---|---|---|
| app.py | Flask 面板与 JSON API | `create_app(ctx)`；页面 /, /chat, /config, /skills, /sources, /logs；API /api/* | 只绑 127.0.0.1；响应不许带凭证正文；/api/sessions 同时返回 browserr 与 chromium 两个键（前端兼容） |
| bridge.py | 同步→异步桥 | `SchedulerBridge.submit/cancel/snapshot` | 不许跨线程直接碰 asyncio 对象 |
| main.py | 入口 | `--setup-check` / `--dry-run-test [--dry-run-count N]` / 默认 serve；`build_context()` 组装 ctx | 新全局对象先进 build_context，不要在模块顶层实例化 |

## 文档与脚本

| 路径 | 内容 |
|---|---|
| docs/skills/*.md | Skill 库（运行时数据，面板可编辑）：blessing_video_hit_v2、web_research、auto_optimize_prompt |
| scripts/install_browserr.py | 下载 Browserr 官方 release 到 browserr_bin/（强制 sha256 校验，BROWSERR_VERSION 可覆盖版本） |
| scripts/setup.sh/.bat | 首次初始化：venv→依赖→装 Browserr→setup-check |
| scripts/start.* / stop.* | 启停（stop 会校验 PID 命令行，只停本项目） |
| scripts/reset_browser.* | 二次确认后清空 browser_profile/session_* |
