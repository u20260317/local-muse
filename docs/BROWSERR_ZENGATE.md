# Browserr + 本地 zen-gate 迁移说明（2026-11-20 实测）

## 改了什么

1. **浏览器：Chromium/Playwright → Browserr（Servo）**
   - `browser_agent/browser_runner.py` 重写：唯一启动点改为 Browserr 官方
     release 的 `engine` + 纯标准库 SDK `driver.py`，接口契约
     （job dict → result dict、goto/fill/click/extract/screenshot/wait、
     form_login）与旧版保持不变，上层 `task_loop` 无需改协议。
   - 引擎固定在项目内 `browserr_bin/`（不入库），由
     `scripts/install_browserr.py` 从 GitHub 官方 release 下载并
     **强制 sha256 校验**（digest 缺失/不符拒绝安装）；默认版本 0.9.2，
     可用 `BROWSERR_VERSION` 覆盖。
   - `core/paths.py` 新增 `setup_browserr_env()` / `browserr_ready()`，
     旧 `setup_playwright_env()` 保留为兼容别名；`requirements.txt`
     删除 playwright。
   - 会话目录不变：`browser_profile/session_<task_id>/`，现作为
     Browserr 的 `profile_dir`（Servo profile）。
   - Linux 无头运行需要系统 EGL：`sudo apt install libegl1 libgl1 libgbm1 libgl1-mesa-dri`。

2. **API：只留本地，其余全关**
   - `config.json` 新增并启用唯一文本源 `zen_gate_local`：
     `http://127.0.0.1:8787/v1`（OpenAI 兼容，style=openai_chat），
     priority=100；原 10 个远程源（OpenRouter/MiniMax/DeepSeek/Gemini/
     通义/HF/万相 等）全部 `enabled=false`。
   - zen-gate 的 mainKey **不写进仓库**：`zen_gate_local.api_key` 留空，
     运行时由 `core/config.py` 从环境变量 `ZEN_GATE_API_KEY` 注入；
     也可以启动后在面板里填本机 key。
   - `main.py --setup-check` 新增两项检查：Browserr 引擎就绪、
     「API 源仅本地」（有远程源启用会 WARN）。
   - zen-gate 只提供文本模型：图片/视频在无本地源时按原设计降级为
     **模拟产物并明确标注**，不会偷偷调用已关闭的远程源。

3. **实测发现的坑（已修复）**
   - 旧的子进程 `RLIMIT_AS 512MB` 会直接杀死 Browserr 引擎：Servo 的
     虚拟地址空间需求实测 >4GB（512/2048/4096MB 均无应答，8192MB 可启动，
     实际 RSS 仅数百 MB）。`browser_worker.py` 因此不再对浏览器子进程
     设 RLIMIT_AS，内存改靠全局并发上限 + 硬件阈值（与 Windows 原策略一致）。
   - 截图文件名旧逻辑用 `safe_id()` 会把 `research.png` 的点去掉变成
     `researchpng`，已修复为保留扩展名。

## 实测结果（2026-11-20，本机沙箱）

| 项 | 结果 |
|---|---|
| zen-gate 无头构建（Go 1.26，`-tags headless`） | ✅ 15MB 二进制，`/healthz` 返回 ok |
| zen-gate `GET /v1/models`（带 mainKey） | ✅ 5 个免费模型：mimo-v2.6-flash-free、deepseek-v4.1-flash-free、nemotron-3.5-lightning-free、nemotron-3.5-super-vl-free、beam-free |
| zen-gate chat 调用 | ⚠️ 网关与鉴权正常，但上游免费车道按出口 IP 限流，5 个模型均返回 FreeUsageLimitError（rate_limit）；这是上游额度，不是接入故障，换出口/等额度恢复即可 |
| `scripts/install_browserr.py` | ✅ 下载 44MB、sha256 校验通过、解压就绪 |
| `main.py --setup-check` | ✅ 9/9 通过（含 Browserr 就绪、API 仅本地） |
| `main.py --dry-run-test`（3 条） | ✅ 3/3 成功 |
| 真实浏览器任务（worker 直跑） | ✅ goto example.com + extract + screenshot 全通，截图 PNG 1200×760 |
| 真实端到端任务（web_research，非 dry_run） | ✅ succeeded：plan 阶段真实调用 zen_gate_local（遭上游限流后按设计降级模板）→ browser 阶段 Browserr 真实采集 example.com → image/video 无本地源走模拟 → 落库完成 |
| 面板启动 | ✅ `http://127.0.0.1:8765/` 200；`/api/sources` 仅 zen_gate_local 为 enabled+available |

## 已知差异（Browserr/Servo 边界）

- 自定义请求头（headers 型 Vault 凭证）无 context 级注入口：runner 会
  写一条 material 说明并跳过，不冒充已注入。
- Cookie/localStorage 改为 goto 落地后注入（HttpOnly Cookie 无法写入）。
- Servo 对重型 SPA/DRM/WebRTC 的兼容性不如 Chromium，见 Browserr 官方说明。

## 排障：page never became ready / no matching event

浏览器引擎能启动、navigate 能发出，但页面永不 ready、eval 报
`TimeoutError: no matching event; recent: []` 时，优先怀疑**图形库缺失**而不是代码：
Servo 渲染依赖 libEGL/libGL/libgbm，系统重装或运行环境更换后可能丢失。
自查：`ldconfig -p | grep libEGL.so.1` 无输出即缺失；
修复：`apt-get install -y libegl1 libgl1 libgbm1 libgl1-mesa-dri` 后重启服务再测。
（2026-11-20 实测：沙箱 VM 更换后该库丢失导致真实采集连续失败，补装后 3.7 秒 ready 恢复正常。）
