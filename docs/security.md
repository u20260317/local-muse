# 安全设计文档（security）

## 1. 威胁模型与保护对象

| 保护对象 | 主要威胁 | 防护手段 |
| --- | --- | --- |
| 用户日常浏览器账号 / Cookie / 历史 | Agent 误接管系统浏览器、读取本机配置 | 只用 Browserr（Servo）引擎 + 项目内独立用户数据目录 |
| vault 登录凭证 | 被生成模块读取、混入 prompt、外泄到免费 API | 模块解耦 + engine_token 鉴权 + 会话隔离 + stdin 注入 |
| API Key | 硬编码、日志泄露、面板回显、进程列表泄露 | 仅 `config.json`；脱敏视图；stdin 传参；日志过滤器 |
| 本机可用性（L430） | 多任务并发打满 CPU/内存、请求风暴被封 | 全局并发上限、单源配额、令牌桶、硬件降级、超时强杀 |
| 面板 | 公网暴露、XSS、路径穿越、误杀进程 | 仅回环、输出转义、文件名白名单、PID 命令行校验 |
| 产物完整性 | 半成品/损坏文件被当作成品 | `.part` 原子落盘 + 魔数/大小校验 |

## 2. vault 凭证隔离机制

### 2.1 存储

- 独立目录 `vault/`，POSIX 下目录权限 `0700`、文件 `0600`；Windows 依赖账户权限（如实说明边界）。
- **强制加密**：`cryptography` 是必需依赖，缺失时 `Vault()` 直接拒绝启动，绝不再降级为明文。
  `cards.json` 固定为 `{"v": 2, "encrypted": true, "data": <Fernet 密文>}`，Fernet 提供认证加密
  （篡改会被拒绝）；密钥文件 `vault/.key` 权限 `0600`。
- 旧版明文 `wallet/cards.json`（`encrypted: false`）在启动时自动迁移、升级为加密并删除旧明文文件；
  磁盘上不会再留下可读的凭证正文。
- 边界如实说明：密钥与密文都在同一项目目录，能防「直接读文件 / 误提交」，
  但防不住已获得本机同用户权限的进程；需要更强保护时应改为口令派生密钥或系统钥匙串。
- `vault/`（含 `.key`）、`browser_profile/`、`output/`、`logs/`、`db/*.db` 均在 `.gitignore` 中。
- 会话与消息记忆存于 `db/task.db` 的 `conversations` / `messages` 表；只存用户指令与任务回执，
  **不存任何凭证正文**，`conversation_context()` 也只取 `role/content`。

### 2.2 访问控制

```
Vault._engine_token = secrets.token_urlsafe(32)   # 仅主进程内存
issue_session_credential(task_id, card_names, engine_token, ttl_s)
    if not hmac.compare_digest(engine_token, self._engine_token):
        raise PermissionError("只有决策引擎可以申请临时凭证")
```

- `engine_token` 由 `Scheduler` 构造时取出，放进 `_services["engine_token"]`，
  只有 `agent_engine`（决策引擎）能拿到；`media_generator` 全链路**不 import vault**。
- 面板注入的是 `Vault.panel_view()` 返回的 `VaultPanelView` **受限视图**，
  只暴露 `list_cards / add_card / delete_card / toggle_card / sessions_snapshot`；
  视图对象没有 `engine_token()` 与 `issue_session_credential()`，面板代码因此无法申请临时凭证，
  消除了「持有 vault 引用即可越权签发」的潜在路径。
- 面板拿到的只有 `list_cards()` 的脱敏摘要（名称、类型、长度描述），**永不返回凭证正文**。
- 新增凭证的 POST 响应只回显名称与「内容隐藏」，输入框提交后立即清空。

### 2.3 会话级临时凭证

| 属性 | 说明 |
| --- | --- |
| 粒度 | 按 `task_id` 一个会话，`session_id` 随机，`expires_at` 默认 300s |
| 内容 | `cookies[] / headers{} / storage{} / form_login[]`，只存在内存 `Vault._sessions` |
| 传递 | 经 **stdin** JSON 传入独立浏览器子进程，不出现在命令行 / 环境变量 / 进程列表 |
| 注入 | `context.add_cookies()` / `set_extra_http_headers()` / `add_init_script()` 仅作用于隔离上下文 |
| 销毁 | 任务完成、失败、超时、被取消时 `vault.revoke(task_id)` 立即清空 |
| 并发隔离 | 不同 task_id 的会话字典互不引用；一个会话被撤销不影响其他任务 |

### 2.4 生成链路的硬边界

- `media_generator/*` 不 import `browser_agent.vault`，也不读取 `vault/` 目录。
- 生成请求的 `body` 只含 `model / prompt / aspect_ratio / first_frame_image` 等字段；
  `prompt` 由 planner 模板生成，模板明确写「不要包含任何账号或凭证信息」。
- vault 凭证不会作为 HTTP Header 发给任何图像 / 视频 / 文本 API；
  发给 API 的只有 `config.json` 里该服务商自己的 `api_key`。
- `dry_run` 自检不签发任何真实凭证，避免自检链路接触敏感数据。

## 3. 浏览器隐私隔离

| 规则 | 实现 |
| --- | --- |
| 二进制隔离 | 只使用 `scripts/install_browserr.* 下载 Browserr 官方 release` 下载到项目内 `browserr_bin/` 的独立 Browserr（不写用户全局 Browserr 缓存，不碰系统浏览器） |
| 路径隔离 | 安装脚本用相对项目根的 `BROWSERR_HOME=browserr_bin`；运行时由 `core/paths.py::setup_browserr_env()` 在 import Browserr 前注入由 `__file__` 解析的目录，不含硬编码绝对路径 |
| 配置隔离 | `Driver(profile_dir=browser_profile/session_<task_id>)` |
| 会话隔离 | 每条任务独立目录，Cookie / 缓存 / localStorage 天然不共享 |
| 明确禁止 | 不指定系统浏览器通道、不指定外部可执行路径、不连接已有浏览器进程、不读取系统浏览器用户目录 |
| 重置边界 | 面板「一键重置」与 `reset_browser.*` 只删项目内 `session_*`，且需二次确认 / 无活跃任务 |
| 有头模式 | 有头 Browserr 仍是独立实例与独立配置目录，不共享本机浏览器数据 |

`browser_agent/browser_runner.py` 是**全项目唯一**启动浏览器的位置，便于审计与静态检查。

## 4. 进程隔离与超时

- 每条浏览器任务、每次生成调用都启动独立 Python 子进程；主进程不直接执行 Browserr 或下载逻辑。
- 子进程创建参数：Windows `CREATE_NEW_PROCESS_GROUP`，POSIX `start_new_session=True`；
  超时用 `taskkill /T /F` 或 `killpg` 杀整棵进程树。
- Linux 子进程内 `apply_worker_memory_limit()` 设置 `RLIMIT_AS`；
  **Windows 无等价强隔离**，靠全局并发上限 + 硬件阈值降级 + Browserr 参数控制，文档如实标注。
- 子进程崩溃、非零退出、stdout 无结果都返回结构化错误，单任务失败不拖垮主程序。
- 退出时 `Scheduler.stop()` 取消全部 `asyncio.Task` 并清理所有已登记 PID。

## 5. 日志与面板脱敏

- `core.logger.redact()` 对 `api_key / token / secret / password / cookie / authorization / bearer`、
  `sk-*`、`ghp_*`、`github_pat_*`、`?key=` 做值替换。
- 日志只输出阶段、provider、task_id 与短摘要，**不输出 prompt 全文、响应大 payload、认证头**。
- 子进程回传的异常文本也经 `redact()` 处理（可能含带 key 的 URL）。
- 面板所有动态内容在 `app.js` 用 `esc()` 转义后渲染；技能预览用 `textContent`，不执行嵌入内容。
- 产物访问 `/output/<task_id>/<filename>` 用 `safe_id` + `secure_filename` 防路径穿越。

## 6. API 风控规避策略

| 风险 | 策略 |
| --- | --- |
| 单源并发过高 | 每服务商独立 `asyncio.Semaphore(max_concurrency)`，默认 1 |
| 短时请求风暴 | 令牌桶按 `rpm` 限速，`acquire()` 在业务子任务内异步等待 |
| 429 封禁 | `report_429()` 冷却该源，`pick()` 自动绕过；任务按 task_id 退避重试最多 2 次 |
| 免费额度耗尽 | 面板显示消耗日志；无可用源时产出明确标注的模拟产物，不伪造计费成功 |
| 长任务挂死 | HTTP 超时 + 子进程超时 + 单任务总超时三层 |
| 敏感数据外发 | prompt 模板禁止凭证；vault 与生成模块解耦；不向免费 API 发送登录凭证 |

## 7. 并发场景下的凭证隔离规则（重点）

1. **一任务一会话**：`issue_session_credential(task_id, ...)`，会话字典以 task_id 为键。
2. **不共享可变对象**：`TaskRuntime` 与 `result` 每任务独立；`services` 只读共享，业务任务不得写入。
3. **stdin 单播**：凭证只发给该任务自己的浏览器子进程，不广播、不写共享文件。
4. **撤销局部化**：`revoke(task_id)` 只清该会话；`cancel(task_id)` 只杀该任务的 PID 集合。
5. **失败不串扰**：一个任务超时被杀，其凭证会话立即销毁，其他任务的会话继续有效至各自结束。
6. **DB 隔离**：`tasks` 以 task_id 主键 upsert；`prompt_history` / `api_usage` 都带 task_id，
   复盘只读本任务与同技能历史。
7. **日志可追溯**：每条日志带 task_id，出现凭证相关告警时可精确定位到单个会话。

## 8. 面板与脚本安全边界

- Flask 只绑定 `127.0.0.1`；`main.py` 对任何非回环 `--host` 强制改写；`use_reloader=False`。
- 不做端口映射 / 内网穿透；面板不提供任何「对外分享」入口。
- `stop.*` 读取 `logs/local_muse.pid` 后**先校验进程命令行含 `main.py`**，不匹配则拒绝终止，避免 PID 复用误杀。
- `reset_browser.*` 二次确认；面板接口在有活跃任务时返回 409 拒绝重置。
- 脚本内零密钥、零绝对路径、零容器；只调用项目内 `main.py` 与 pip/browserr 安装命令。
- 安装脚本用相对项目根 `BROWSERR_HOME=browserr_bin` 把 Browserr 固定到项目内；
  脚本一律先 `cd "$(dirname ...)/.."` 或 `cd /d "%~dp0.."` 定位项目根，不写盘符路径，
  因此把项目整体拷到 Linux 后无需改任何路径。
- 配置保存走原子写与字段白名单；`server.host` 非法值被强制修正。

## 9. 已知边界（不承诺项）

| 边界 | 说明 |
| --- | --- |
| 轻量沙盒 ≠ 强安全边界 | 子进程与主进程同用户权限；不是容器、不是虚拟机，无法防御同主机恶意代码 |
| Windows 内存上限 | 没有 `RLIMIT_AS`，不能承诺单进程硬性内存隔离 |
| 凭证落盘 | 未安装 `cryptography` 时为受限权限明文；请自行控制主机账户权限 |
| 免费 API 数据 | 第三方免费层可能保留请求内容；切勿提交任何敏感信息 |
| 面板认证 | 面板只监听回环、无登录鉴权；假设本机单用户可信环境 |
| 网络代理 | 代理配置由用户自行处理；程序不自动读取或记录代理凭据 |

## 10. 安全自查清单

- [ ] 全仓库搜索 `channel=` / `executable_path` / `connect_over_cdp` 无命中（或仅出现在说明文字中）
- [ ] `media_generator/` 不出现 `import vault` 或读取 `vault/` 目录的代码
- [ ] `config.json` 的 `api_key` 为空占位；填写真实 Key 后未提交到公开仓库
- [ ] 日志与面板响应中无密钥正文（脱敏过滤器生效）
- [ ] Flask 监听地址为 `127.0.0.1`，未做端口映射
- [ ] `browser_profile/` 下只有 `session_*` 目录，无系统浏览器路径
- [ ] Browserr 二进制位于项目内 `browserr_bin/`，未被用户全局 Browserr 缓存接管
- [ ] 搜索全部 `.py` / `.bat` / `.sh` / `.ps1`，无任何平台相关绝对路径；`BROWSERR_HOME` 安装用相对路径、运行时动态解析
- [ ] 退出后 `vault` 会话计数为 0、无残留 Python 子进程
