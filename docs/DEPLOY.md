# 部署手册（DEPLOY）— 手把手版

> 目标读者：第一次部署的人或 AI。照着做，每步都有验证命令；任何一步对不上，先停在那步看故障表，不要跳步。

## 0. 前置条件

- Python 3.10+（项目在 3.12/3.14 均实测）、磁盘 ≥1GB 空闲（Browserr 引擎约 160MB + 运行数据）
- 能访问 github.com（下载 Browserr release）与本机回环端口 8765（面板）、8787（zen-gate）
- 浏览器引擎二选一的系统依赖：Linux 需要 EGL 库（第 3 步），Windows/macOS 免

## 1. 拿代码

```bash
git clone https://github.com/u20260317/local-muse.git
cd local-muse
# 若使用 Release 版：下载 Release 的 Source code 压缩包解压即可，内容与对应 tag 一致
```

## 2. 一键初始化（推荐）

Linux/macOS：`bash scripts/setup.sh`
Windows：`scripts\setup.bat`

它依次做：建 .venv → pip 装 requirements（flask/requests/schedule/cryptography，
**没有 playwright**) → 跑 install_browserr.py 装引擎 → `main.py --setup-check`。
想手动分步就看第 3、4 步。

## 3. 手动装 Browserr 引擎

```bash
python scripts/install_browserr.py            # 默认版本 0.9.2
BROWSERR_VERSION=0.9.2 python scripts/install_browserr.py   # 指定版本
```

- 它从 GitHub 官方 release 下载对应平台包，**校验 sha256，不符拒绝安装**，解压到 `browserr_bin/`
  （含 `engine` 二进制与 `driver.py`）。browserr_bin/ 已被 .gitignore 排除，不要提交。
- Linux 报缺 `libEGL.so.1` 时：`sudo apt install libegl1 libgl1 libgbm1 libgl1-mesa-dri`，再重跑。
- 验证：`ls browserr_bin/` 应有 engine 与 driver.py。

## 4. 部署本地 zen-gate（唯一的 API 源）

```bash
git clone https://github.com/LAGcomcom/zen-gate.git
cd zen-gate
CGO_ENABLED=0 go build -tags headless -o zen-gate ./cmd/zen-gate   # 需要 Go 1.21+
ZEN_GATE_HOME=$HOME/.zen-gate ZEN_GATE_HEADLESS=1 ./zen-gate &     # 监听 127.0.0.1:8787
curl http://127.0.0.1:8787/healthz        # 期望 {"ok":true,"service":"zen-gate"}
```

首次启动会在 `$ZEN_GATE_HOME/config.json` 生成 mainKey（`ofm-` 开头）。
验证模型与鉴权：

```bash
KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('$ZEN_GATE_HOME/config.json')))['mainKey'])")
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8787/v1/models   # 期望返回 *-free 模型列表
```

## 5. 把 key 交给 local-muse（二选一，推荐环境变量）

```bash
export ZEN_GATE_API_KEY=<上一步的 mainKey>     # key 不落文件，config.json 里保持空
# 或：编辑 config.json，把 providers 里 zen_gate_local 的 api_key 填上（填了就不要提交 git！）
```

其余 10 个远程 provider 保持 `enabled=false` 不要动——本版「API 只搞本地」是硬约束，
setup-check 会对「启用了非回环源」给出警告。

## 6. 环境自检（必须 9/9）

```bash
.venv/bin/python main.py --setup-check
```

期望 9 项全 OK，含「Browserr 引擎（browserr_bin/）」与「API 源仅本地」。
任何 FAIL 先修那项，不要带病启动。

## 7. 冒烟测试（从轻到重，三级都要过）

```bash
.venv/bin/python main.py --dry-run-test --dry-run-count 3     # 一级：全模拟，期望 3/3 成功
ZEN_GATE_API_KEY=<key> .venv/bin/python main.py               # 二级：起服务，浏览器开 http://127.0.0.1:8765
```

三级（真实任务）：面板「并发看板」提交一条技能选 web_research 的任务，期望：
状态 succeeded；materials 里有 example.com 正文；output/<task_id>/research.png 存在；
plan 来源若显示 template 且日志有「429 冷却」字样，是 zen 上游限流（见故障表），不是部署失败。

## 8. 日常运维

- 启停：`bash scripts/start.sh` / `bash scripts/stop.sh`（stop 校验 PID，只停本项目）
- 配置热更新：改 config.json 保存即可，硬件采样循环会自动 reload，不用重启
- 清浏览器会话：`bash scripts/reset_browser.sh`（二次确认，只删 browser_profile/session_*）
- 数据都在项目内：db/task.db、output/、logs/、vault/（加密凭证，**不要拷给别人**）
- zen-gate 是独立进程，重启机器后要先起 zen-gate 再起 local-muse

## 9. 故障表

| 现象 | 原因 | 处理 |
|---|---|---|
| setup-check 报 Browserr 未就绪 | browserr_bin 缺失/不完整 | 重跑第 3 步；Linux 先装 EGL 库 |
| 浏览器任务报 engine did not answer / Connection refused | 多半是有人给浏览器子进程加了内存上限，或引擎缺 EGL 依赖 | 检查 browser_worker 不要设 RLIMIT_AS；Linux 装 libegl1/libgbm1 后重试 |
| plan 总是 template、日志有 429 冷却 | zen-gate 上游免费车道按出口 IP 限流 | 等额度恢复或换出口网络；网关本身用 /healthz 与 /v1/models 验证（这两个通就不是部署问题） |
| /api/sources 里 zen_gate_local 不 available | key 没注入 | 确认启动进程的环境里有 ZEN_GATE_API_KEY，或 config.json 已填 |
| 面板打不开 | 端口被占/进程没起 | 查 logs/ 最新日志；换端口 `.venv/bin/python main.py --port 8766`（只许回环地址） |
| 任务卡在 queued | 硬件降级中（日志有「硬件保护」）或并发已满 | 等资源回落自动恢复；不要重启，重启会丢队列态 |
| 截图文件名没有扩展名 | 旧版本 bug（safe_id 吃掉点号） | 本版已修复，确认跑的是本版代码 |

## 10. 升级与回滚

- 升级：拉新代码/新 Release → 重跑 setup.sh → setup-check → dry-run 三级冒烟。
- 回滚：main 分支永远是原始版本；Release tag 是改造版快照，互不覆盖，随时可切回 main 部署。
- config.json、db/、vault/ 是用户数据，升级时保留这三处不动。
