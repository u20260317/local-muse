# AI 维护指南（AI_GUIDE）— 给接手的 AI 看

> 你（AI）接手本项目时，先按第 1 节的顺序读文件，再动任何代码。
> 本项目的规矩是「契约优先、子进程隔离、失败降级」，违反任何一条的改动默认是错的。

## 1. 15 分钟上手路径（按序读，不要跳）

1. `docs/DESIGN.md` — 架构、时序、安全边界（10 分钟）
2. `docs/DECISION_ENGINE.md` — 决策引擎的计划/调度/选源/复盘（5 分钟）
3. `docs/MODULES.md` — 你要改的那个模块的契约行
4. 目标模块源码头部「【核心模块】」横幅 + 模块 docstring
5. `docs/BROWSERR_ZENGATE.md` — 本版（Browserr+本地 API）与原版的差异与实测结论

## 2. 不可破坏的不变量（INVARIANTS）

1. 浏览器只许在 `browser_agent/browser_runner.py` 启动，且只用项目内 Browserr。
2. 浏览器子进程**不许设 RLIMIT_AS**（Servo 虚拟地址 >4GB，实测 512/2048/4096MB 都会让引擎无应答）。
3. 主进程（Scheduler 所在进程）不许直接发 HTTP 生成请求、不许直接启动浏览器；业务 I/O 一律子进程（sandbox_wrapper.run_worker）或 to_thread。
4. 参数与密钥只走子进程 stdin；不进命令行、不进日志（日志先过 redact）、不进 prompt。
5. Vault 只许 Fernet 加密落盘；只有决策引擎可签发会话凭证；任务结束（含异常/取消）必须 revoke。
6. planner.build_plan 不许抛错阻断任务——外部调用失败必须降级模板计划。
7. 无可用生成源时走模拟产物且 simulated=true 明示，禁止伪装真实生成。
8. config.json 入库版本所有 api_key 必须为空；zen_gate_local 的 key 走 ZEN_GATE_API_KEY 注入。
9. 启用源只许本机回环（zen_gate_local）；不要擅自把远程 provider 改回 enabled=true。
10. 面板只绑 127.0.0.1；/api/sessions 保留 browserr+chromium 双键（前端兼容）。

## 3. 常见改动配方（照着做，别自由发挥）

**加一个 API 源**：只改 config.json providers（name/kind/base_url/path/style/model/priority/
weight/rpm/max_concurrency/timeout_s），style 需是 api_pool 已支持的
（openai_chat/minimax_chat/gemini/generic_image/hf_inference/dashscope_image/minimax_video/generic_video）；
新 style 才需要改 api_pool.build_*_request 与 media_worker 的解析。加完跑 DEPLOY.md 第 7 节三级冒烟。

**加一个 Skill**：docs/skills/ 下新建 `<name>.md`，front-matter 写 title/description/enabled；
name 只许 [A-Za-z0-9_-]{1,64}；名字含 research 会自动触发浏览器阶段。内置三个技能不可删（skill_loader 有保护）。

**给浏览器加一种 action**：browser_runner.run_browser_job 的动作分发里加分支 →
在 docs/MODULES.md 的 job 契约里补一行 → 用 browser_worker 直跑一个 job JSON 验证
（stdin 喂 job，stdout 应只有一行 LM_RESULT_JSON）。

**加一个流水线阶段**：task_loop.execute 按阶段编号插入，_report 上报新 progress，
result["stages"] 追加名字，先定失败语义（抛错重试 / 降级跳过），同步更新 DECISION_ENGINE.md 第 3 节表格。

**改并发/超时/重试**：只改 config.json 的 scheduler 段（max_concurrency/task_timeout_seconds/
retry_times/subprocess_memory_limit_mb），不要改代码里的默认值散落处；注意 subprocess_memory_limit_mb 只对生成子进程生效，浏览器子进程刻意不设。

**改数据表**：core/db.py 的 CREATE TABLE IF NOT EXISTS 区加迁移；models.py 改字段后全局搜
`runtime.`/`spec.` 引用；tasks 表落库字段在 Scheduler._persist 里统一收口。

**改记忆/Token 相关**：core/memory.py 与 core/token_meter.py 都是零业务依赖的基础层，
改 recall/压缩算法后必须单独跑一遍召回与压缩的断言脚本（见 PLUGIN_GUIDE 与 MEMORY_MODULE 的算法说明），
并确认 planner 在 services["memory"]=None 时仍能正常出计划（v1.0 退路）。

## 4. 验证清单（每次改动后按改动面勾选，全过才许说「完成」）

```bash
python3 -m py_compile main.py core/*.py agent_engine/*.py browser_agent/*.py media_generator/*.py web_panel/*.py
.venv/bin/python main.py --setup-check                       # 期望 9/9
.venv/bin/python main.py --dry-run-test --dry-run-count 3    # 期望 3/3
# 动了浏览器线再加：
echo '<job json>' | .venv/bin/python browser_agent/browser_worker.py   # 期望 ok=true 且截图落盘
# 动了面板再加：起服务后 curl 127.0.0.1:8765/ 与 /api/sources 各一次
```

报告结果时区分：真实跑通 / 模拟跑通（simulated）/ 上游限流降级——三者不许混着说。

## 5. 发布方式（本仓库的约定）

- **main 分支 = 原始版本，永不直接改**。改造以 GitHub Release 形式发布：
  用 Git Data API 在新 tree+commit 上打 tag（父提交指向 main 当前提交），再用该 tag 建 Release；
  Release 说明写清改动清单、实测结果、已知限制（模板见 BROWSERR_ZENGATE.md 的表格）。
- 提交信息格式：`backup:` / `feat:` / `fix:` / `docs:` 前缀 + 一句中文说明。
- 推送前自查：git 状态里不许出现 browserr_bin/、vault/、db/、output/、logs/、__pycache__、
  以及任何非空 api_key 的 config.json。

## 6. 排障速查（先看日志再动代码）

- 日志位置：logs/ + 面板 /logs；每行带 task_id/stage，先按 task_id 过滤单任务全程。
- 「429 冷却 + 降级模板」连环出现 = 上游限流，属预期链路（见 DEPLOY.md 故障表）。
- 「engine did not answer」= 先查 RLIMIT_AS 与 EGL 依赖，不要先怀疑 Browserr 本身：
  用 browserr_bin/driver.py 写 10 行脚本直连引擎验证（navigate example.com + screenshot）。
- 任务卡 queued = 看 Scheduler snapshot 的 degraded 与 active_count，不要重启。
