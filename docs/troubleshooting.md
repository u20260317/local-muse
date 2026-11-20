# L430 故障排查（troubleshooting）

适用环境：ThinkPad L430 / 老旧 Windows 或 Linux / Python 3.10+（本项目在 Python 3.14.7 环境编写）。
先运行 `python main.py --setup-check` 与 `scripts/dry_run_test.*`，用输出定位问题层级。

---

## 1. 快速定位

| 现象 | 优先检查 |
| --- | --- |
| 面板打不开 | 端口占用、启动日志、是否被防火墙拦截回环 |
| 任务一直排队不执行 | `degraded` 降级状态、全局并发上限、调度器是否启动 |
| 任务全部失败 | `/api/logs` 中对应 task_id 的 ERROR、子进程 stderr |
| 浏览器任务失败 | Playwright 内置 Chromium 是否安装、会话目录权限 |
| 生成全是「模拟」 | 是否勾选自检模式 / 服务商无 Key 或被禁用 |
| 整机卡顿 | CPU、内存、并发数、Chromium 实例数 |

---

## 2. 并发卡死（新增重点）

**症状**：面板有响应但任务长期停在 `running`，进度不动；CPU 占用高或内存接近满载。

排查顺序：

1. 看底部状态条：`CPU / 内存 / 活跃 / 排队 / 降级`。若显示「硬件降级」，说明已自动暂停派发新任务，属**预期行为**，等待现有任务完成或资源回落。
2. 打开「实时日志」，按 task_id 过滤，确认最后停在哪个阶段（`plan / browser / image / video / reflect`）。
3. 若停在 `browser`：多半是页面导航卡住，检查 `browser.timeout_seconds` 与 `navigation_timeout_ms`，可下调到 30s / 15000ms。
4. 若停在 `video`：视频 API 是分钟级长轮询，检查 `scheduler.task_timeout_seconds` 与该源 `timeout_s`。
5. 在面板点「单独终止」该 task_id；程序会取消句柄、强杀该任务进程树、销毁其凭证会话，**不会杀主程序或其他任务**。
6. 仍不恢复时，用 `scripts/stop.*` 停止服务并重启（启动会重新加载队列状态，历史任务仍在 DB 中）。

**预防**：

- `max_concurrency` 从 2 降到 1；`browser.headless=true`。
- 缩短 `task_timeout_seconds`（如 300）与 `timeout_seconds`（如 40）。
- `sample_interval_seconds=1`、`cpu_threshold=75`、`memory_threshold=75`，让降级更早介入。
- 避免同时提交超过 5 条并行任务（队列会排队，但瞬时内存仍受全局并发限制）。

---

## 3. 并发超限 / 任务不派发

**症状**：提交成功但一直 `queued`，活跃数为 0 或已满。

| 原因 | 判断 | 处理 |
| --- | --- | --- |
| 全局并发已满 | `active_count == max_concurrency` | 等待任务完成，或调大 `max_concurrency`（L430 不建议 > 2） |
| 硬件降级 | 顶部显示「硬件降级中」 | 关闭占资源的程序；或调高阈值（不推荐） |
| 调度器未启动 | `/api/status` 返回 `error`，日志有「调度器尚未就绪」 | 确认用 `main.py` 启动（不是只 import 模块）；等待 15s 启动超时窗口 |
| 单源配额耗尽 | 日志出现 `acquire` 等待、该源 `inflight == max_concurrency` | 属正常限流；调低 `rpm` 或错开任务 |
| 每日上限 | 日志「今日定时任务已达上限」 | 调大 `daily_task_limit` 或手动触发（手动任务不受上限） |

优先级说明：`insert` 插队只影响**排队顺序**，不会突破全局并发上限或硬件降级门禁。

> 例外：`main.py --dry-run-test` 与 `scripts/dry_run_test.*` 会临时调用 `set_hardware_gate(False)`
> 关闭硬件降级门禁——自检不启动真实 Chromium、不联网，若本机内存本就偏高（如 93%），
> 开启门禁会让排队任务一直等不到派发。**正常启动服务时该门禁始终开启**。

---

## 4. 多 Chromium 实例资源占用

**症状**：内存飙升、风扇狂转、`browser_profile/` 下出现多个 `session_*` 目录。

- 单个独立 Chromium 实例约 150–300MB；默认 `max_concurrency=2` 即最多两个并行浏览器任务。
- 只有技能名含 `research` 的计划会把 `needs_browser` 置为 true；用 `blessing_video_hit_v2` /
  `auto_optimize_prompt` 可减少浏览器实例。
- 在「API 源 / 会话」页查看每个会话目录占用（MB），用「清空缓存 / 一键重置环境」清理（需二次确认，且无活跃任务）。
- 命令行等价：`scripts/reset_browser.bat` / `bash scripts/reset_browser.sh`。
- 长期运行建议定期重置，避免会话缓存无限增长。
- 注意：重置只删项目内 `session_*`，**不会**影响本机 Chrome / Edge / Firefox。

---

## 5. 429 限流与免费额度

**症状**：日志出现「服务商返回 429」「API 源 xxx 触发 429，冷却 Ns」，任务进入 `RETRYING`。

处理：

1. 在「API 源」页看冷却倒计时，等待自动恢复；`pick()` 会自动绕过冷却中的源。
2. 降低该源 `rpm` 与 `max_concurrency`（建议 `rpm=2~6`、并发 1）。
3. 降低 `max_concurrency` 全局值，避免多任务同时打同一家。
4. 检查免费额度是否耗尽：登录服务商控制台确认余额与用量上限；免费额度不是永久承诺。
5. 重试 2 次仍失败会按 `FAILED` 落库，可在面板查看 `error` 字段。

**不要**通过调高 `rpm` 来「提速」：L430 的瓶颈是内存与网络，触发风控反而更慢。

---

## 6. Playwright / 独立 Chromium 问题

| 报错 | 处理 |
| --- | --- |
| `playwright not installed` / `模块不存在` | `.venv/bin/python -m pip install -r requirements.txt`（Windows 用 `.venv\Scripts\python.exe`） |
| `Executable doesn't exist ... chromium-...` | 重跑 `scripts/install_chromium.*`；它会在项目根设置 `PLAYWRIGHT_BROWSERS_PATH=browser_bin` 并下载到项目内 `browser_bin/`。手动安装时务必先 `cd` 到项目根再执行，否则相对路径会指向别处 |
| 下载超时 / 证书错误 | 检查代理与网络后重试；确认 `browser_bin/` 可写；只下载 Playwright 自带二进制，**不要**改用系统 Chrome |
| 进度长时间停在 0% | 官方 CDN 会 302 到 `storage.googleapis.com`，部分网络直连超时。设置 `PLAYWRIGHT_DOWNLOAD_HOST` 指向可访问镜像后重跑 `install_chromium.*`；脚本不覆盖该变量，未设置时走官方源，安装目录仍锁定在项目内 `browser_bin/` |
| `Target page/context closed` | 页面崩溃或超时被杀；缩短导航超时、降低并发、确认内存充足 |
| 有头模式不显示窗口 | 检查 `browser.headless` 是否为 `false`；无桌面环境（Linux 服务器）只能用无头 |
| 项目移到另一台机器后浏览器报错 | 自带 `browser_bin/` 可整目录拷贝；若缺失只需重跑安装脚本，不要写死任何盘符路径 |

环境自检命令：`python main.py --setup-check`，会以**相对项目根**的形式打印内置 Chromium 可执行文件与是否存在（不展示平台相关的绝对路径）。

---

## 7. Python 3.14 / Windows 兼容

- 项目只用标准库 + `flask / playwright / requests / schedule`；若某依赖版本不支持 3.14，先升级该包。
- Windows 上 `asyncio` 默认 Proactor 事件循环，子进程通信依赖它；不要用第三方循环替换。
- Windows 没有 `resource.RLIMIT_AS`，`subprocess_memory_limit_mb` 不生效是正常的，靠并发与阈值降级控制内存。
- `.bat` 脚本全部 ASCII + `chcp 65001`；若中文显示为乱码，改用 `scripts/start.ps1` 或直接手动命令。
- 路径含空格时脚本用 `cd /d "%~dp0.."` / `cd "$(dirname ...)/.."`，不要自行改成裸相对路径。
- 项目内**没有**任何硬编码绝对路径：Windows 与 Linux 都按脚本位置 / `__file__` 动态解析项目根。
  若从别的工作目录手动执行 `python main.py`，浏览器目录仍由 `setup_playwright_env()` 正确解析；
  但手动执行 `playwright install chromium` 时，必须先进入项目根再设 `PLAYWRIGHT_BROWSERS_PATH=browser_bin`，
  否则相对目录会被解析到错误的当前目录。

---

## 8. 脚本启动失败

| 现象 | 处理 |
| --- | --- |
| `[ERROR] .venv not found` | 先跑 `scripts/setup.*` |
| `[ERROR] Python 3 not found` | 安装 Python 3.10+ 并加入 PATH；Windows 可用 `py -3` |
| `pip install` 失败 | 检查网络/代理、换 pip 源；重跑 setup（可重复执行） |
| 面板端口被占用 | `python main.py --port 9000`，或修改 `config.json` 的 `server.port` 后重启 |
| `stop` 提示 PID 不属于 Local-Muse | PID 被系统复用，脚本为安全起见拒绝；直接 Ctrl+C 关闭启动窗口 |
| `reset_browser` 被面板拒绝 (409) | 仍有运行中的任务，先等待或单独终止 |
| `.sh` 无执行权限 | `chmod +x scripts/*.sh` |

---

## 9. SQLite / 日志问题

- `database is locked`：已开启 WAL + `busy_timeout=5000`；若仍出现，检查是否有多个服务实例同时运行（用 `scripts/stop.*` 停掉多余的）。
- 任务记录不刷新：面板轮询 2 秒一次；手动刷新页面或检查 `/api/status` 返回。
- 日志里出现 `***REDACTED***`：脱敏过滤器生效，属预期，不代表凭证丢失。
- 日志文件过大：`logs/local_muse.log` 使用 `RotatingFileHandler`（2MB × 3），无需手动清理。
- 想清空历史任务：停止服务后删除 `db/task.db*`（会丢失全部复盘与会话记录，谨慎操作）。
- `RuntimeError: vault 需要 cryptography`：执行 `pip install -r requirements.txt` 后重启；
  Vault 不再接受明文降级，`--setup-check` 也会检查该依赖。
- 会话列表为空 / 消息不恢复：确认 `db/task.db` 未被删除；老库启动时会自动补
  `tasks.conversation_id` 列与 `conversations` / `messages` 表。
- 首页仍是旧的并发看板：代码已把 `/` 改为对话工作台，需**重启服务**并强制刷新浏览器缓存；
  原看板保留在 `/board`。

---

## 10. 产物问题

| 现象 | 处理 |
| --- | --- |
| 面板图片空白 | 已配置 `onerror` 兜底；检查 `output/<task_id>/` 文件是否存在、`verify.verified` 是否为 true |
| 产物是 1×1 PNG / `simulated_video.json` | 当前为 `dry_run` 自检或无可用 API 源，属预期；填 Key 并取消「自检模式」 |
| 下载文件损坏 | `downloader.verify()` 魔数校验会拒绝并触发重试；检查服务商返回是否为真实文件 URL |
| 参考图未生效 | 确认 `image` 阶段成功产出本地路径，`build_generation_request` 才会转 data URL 填入 `first_frame_image` |

---

## 11. 信息收集清单（求助时提供）

1. `python main.py --setup-check` 的完整输出。
2. `scripts/dry_run_test.*` 的退出码与每个 task_id 状态。
3. 面板 `/api/status` 的 JSON（可脱敏后提供）。
4. 「实时日志」中相关 task_id 的 ERROR / WARNING 行。
5. OS 版本、Python 版本、内存大小、`config.json` 中的 `max_concurrency` 与硬件阈值。

> 提供信息前请自行检查：日志与配置中不应包含任何 API Key、Cookie 或 Token 正文。
