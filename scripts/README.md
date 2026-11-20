# scripts/ 一键启动与运维脚本

本目录是 Local-Muse 的外围入口层，只负责「环境准备 → 调用 `main.py`」，不重复实现任何业务逻辑。
全部脚本零密钥、零绝对路径、零容器；不接管本机 Chrome / Edge / Firefox。

## 一、推荐执行顺序（首次）

| 步骤 | Windows | Linux / macOS | 作用 |
| --- | --- | --- | --- |
| 1 | 双击 `setup.bat` | `bash scripts/setup.sh` | 建 `.venv`、装 `requirements.txt`、把 Chromium 下载到项目内 `browser_bin/`、跑环境自检 |
| 2 | 编辑 `config.json` 填 API Key（也可稍后在面板填） | 同左 | 密钥只落 `config.json` |
| 3 | 双击 `start.bat` 或 `powershell -ExecutionPolicy Bypass -File scripts\start.ps1` | `bash scripts/start.sh` | 启动调度器 + 面板，访问 `http://127.0.0.1:8765` |
| 4 | `dry_run_test.bat` | `bash scripts/dry_run_test.sh` | 无需 Key 的并行链路自检 |

> Linux 首次使用建议先执行一次：`chmod +x scripts/*.sh`，之后可直接 `./scripts/start.sh`。
> Windows 的 `.bat` 全部使用 ASCII 文本 + `chcp 65001`，避免中文编码导致批处理解析失败；
> 需要中文提示时使用 `start.ps1`。

## 二、脚本清单

| 脚本 | 说明 |
| --- | --- |
| `setup.bat` / `setup.sh` | 首次初始化：虚拟环境、依赖、**仅**把 Playwright 内置独立 Chromium 下载到项目内 `browser_bin/`、执行 `main.py --setup-check` |
| `start.bat` / `start.sh` / `start.ps1` | 一键启动；复用 `.venv`，校验核心依赖，打印本机面板地址。附加参数会透传给 `main.py`（如 `--port 9000`） |
| `dry_run_test.bat` / `dry_run_test.sh` | 调用 `main.py --dry-run-test --dry-run-count 3`，提交 3 条不同优先级的并行自检任务，产物明确标注「模拟」，不访问公网 |
| `install_chromium.bat` / `install_chromium.sh` | 单独把 Chromium 安装/修复到项目内 `browser_bin/`（内部设置 `PLAYWRIGHT_BROWSERS_PATH=browser_bin`），用于浏览器二进制缺失或升级 |
| `reset_browser.bat` / `reset_browser.sh` | 二次确认后删除 `browser_profile/session_*`，重建独立浏览器环境；有运行中的任务时面板接口会拒绝 |
| `stop.bat` / `stop.sh` | 读取 `logs/local_muse.pid`，**先校验进程命令行包含 `main.py`**，再终止，避免误杀其它进程 |

## 三、`main.py` 提供的 CLI（供脚本调用）

```
python main.py                               # 启动调度器 + 面板（仅 127.0.0.1）
python main.py --setup-check                 # 环境自检，返回退出码
python main.py --dry-run-test                # 并行自检，默认 3 条
python main.py --dry-run-test --dry-run-count 5
python main.py --host 127.0.0.1 --port 8765 # 非回环 host 会被强制改写为 127.0.0.1
```

### 路径与跨平台约定（重要）

- **零硬编码绝对路径**：运行期目录全部由 `core/paths.py` 基于 `__file__` 动态解析；
  脚本用 `cd /d "%~dp0.."`（Windows）或 `cd "$(dirname "${BASH_SOURCE[0]}")/.."`（Linux）定位项目根。
  项目文件里不存在任何只能在 Windows 上用的盘符绝对路径。
- Playwright 内置 Chromium 二进制固定装到**项目内 `browser_bin/`**：安装脚本设置
  `PLAYWRIGHT_BROWSERS_PATH=browser_bin`（相对项目根）；程序运行时由
  `core/paths.py::setup_playwright_env()` 在任何 `import playwright` 之前统一注入
  由 `__file__` 解析出的目录，主进程、自检命令与浏览器子进程保持一致。
- `browser_bin/`（二进制）与 `browser_profile/`（用户数据 / Cookie）职责分离，两者都不入库。
- 手动安装时，先进入项目根再执行：
  `PLAYWRIGHT_BROWSERS_PATH=browser_bin python -m playwright install chromium`
  （Windows PowerShell：`$env:PLAYWRIGHT_BROWSERS_PATH="browser_bin"` 后执行；
  CMD：`set PLAYWRIGHT_BROWSERS_PATH=browser_bin` 后执行）。

## 四、L430 使用建议

- 首次 `setup` 需要把 Chromium（约 100–200MB）下载到项目内 `browser_bin/`，建议在代理/网络稳定时执行，可重复运行不会重复安装。
- 启动后先在面板「全局配置」保持 `max_concurrency=2`、`单服务商并发上限=1`；若内存紧张改为 `1`。
- 用 `dry_run_test` 验证链路，不消耗任何免费额度；确认无误后再填 Key 关闭「自检模式」。
- 面板只监听回环地址，不要用任何内网穿透/端口映射工具把它暴露到公网。

## 五、常见失败

| 现象 | 处理 |
| --- | --- |
| `[ERROR] .venv not found` | 先运行 `setup.bat` / `setup.sh` |
| `playwright install chromium` 下载失败 | 检查代理后重跑 `install_chromium.*`；确认 `browser_bin/` 可写、`PLAYWRIGHT_BROWSERS_PATH` 指向项目内目录；不要指向系统浏览器 |
| 下载长时间停在 0% | 官方 CDN 会跳转到部分网络不可达的存储域名。可先设置 `PLAYWRIGHT_DOWNLOAD_HOST` 指向可访问镜像再重跑安装脚本，例如 PowerShell：`$env:PLAYWRIGHT_DOWNLOAD_HOST="https://cdn.npmmirror.com/binaries/playwright"`；Bash：`export PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`。脚本不覆盖该变量，未设置时仍走官方源；安装目录始终由 `PLAYWRIGHT_BROWSERS_PATH=browser_bin` 锁定在项目内 |
| 面板打不开 | 确认端口未被占用、防火墙未拦截回环地址；用 `--port` 换端口 |
| 任务一直是「模拟」 | 说明未配置可用 API Key 或勾选了「自检模式」，在面板/配置中确认 |
| 停止脚本拒绝终止 | PID 可能被系统复用，脚本为安全起见拒绝；直接关闭启动窗口（Ctrl+C）即可 |

## 六、安全边界

- 脚本内不含任何 API Key、Token、Cookie；密钥只在 `config.json` 与加密的 `vault/` 容器中。
- 脚本与代码均不含硬编码绝对路径；所有目录按脚本位置 / `__file__` 动态解析，Windows 与 Linux 通用。
- 所有脚本只调用本机 Python 与项目内 `main.py`，不启动 Docker、不安装系统级服务。
- `stop` 与 `reset_browser` 都限定项目路径/二次确认，不误删用户数据。
