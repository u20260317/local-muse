# 项目设计文档（DESIGN）

> 读者：接手维护的人或 AI。读完本文 + DECISION_ENGINE.md + MODULES.md，应能在 30 分钟内
> 定位任何问题、独立完成一次小迭代。本文所有描述以当前代码为准（2026-11-20，Browserr + zen-gate 版）。

## 1. 项目定位

Local-Muse 是一个跑在老硬件（如 ThinkPad L430）上的**本地自治 Agent 系统**：
调度器 7×24 接收任务（面板手动 / 定时 / 对话），决策引擎为每条任务生成计划，
可选地用隔离浏览器采集素材，再调用文本/图片/视频 API 产出内容，最后独立复盘并把经验写回，
供下一轮计划参考。本版本两条硬约束：

1. **浏览器只用 Browserr**（Servo 引擎，项目内 `browserr_bin/`），绝不接管系统浏览器。
2. **API 只用本地**：唯一启用源是本机 zen-gate（`http://127.0.0.1:8787/v1`）；
   其余远程源在 config.json 中全部 `enabled=false`，代码不会调用它们。

## 2. 总体架构

```
┌────────────┐  HTTP(127.0.0.1:8765)   ┌──────────────────────────────┐
│ web_panel  │ ──────────────────────▶ │ main.py  serve()             │
│ Flask 面板 │ ◀── bridge(线程桥) ──── │  └─ asyncio 事件循环(独立线程) │
└────────────┘                         │      └─ Scheduler 调度中枢    │
                                       └──────┬───────────────────────┘
              ┌───────────────────┬────────────┼──────────────┬─────────────────┐
              ▼                   ▼            ▼              ▼                 ▼
        agent_engine/       media_gen/   browser_agent/    core/           docs/skills/
        决策引擎             生成线       浏览器线          基础层           Skill 库
        planner 计划/反思    api_pool     runner(唯一启动点) config/db      *.md
        task_loop 流水线     image/video  worker 子进程     hardware/log
        task_queue 调度      worker 子进程 sandbox 隔离     models/paths
                             downloader   vault 凭证保险库
```

分层原则：**上层只依赖下层契约（core/models.py），业务 I/O 全部下沉到子进程**。
主进程（调度器所在进程）只做决策与协调，永不直接发 HTTP 生成请求、永不直接启动浏览器。

## 3. 一条任务的生命周期（时序）

```
submit(TaskSpec) → PriorityQueue 入队（insert 模式负优先级插队）
  → _dispatch_loop 出队（并发 < max_concurrency 且硬件未降级）
  → _run_task：asyncio.wait_for(task_loop.execute, timeout=task_timeout_seconds)
      1. plan    planner.build_plan：Skill + 历史复盘 + 会话上下文 → LLM 计划 | 模板降级
      2. browser （plan.needs_browser 时）vault 签发会话凭证 → sandbox 子进程 → Browserr 采集
      3. image   api_pool.pick 选源 → media 子进程请求/下载/校验
      4. video   同上，image 产物可作 first_frame_image
      5. upload  默认关闭，占位跳过
      6. persist result_json 落 tasks 表
      7. reflect planner.reflect：评分 + 建议 → prompt_history（下一轮 plan 读取）
  → 成功 SUCCEEDED；异常按 retry_times 指数退避重试，最终 FAILED/TIMEOUT
  → 任意路径结束：vault.revoke(task_id) + 杀该任务子进程树 + 释放并发名额
```

状态机（core/models.TaskStatus）：queued → running → (retrying → running)* →
succeeded | failed | timeout | cancelled。paused 仅为硬件降级展示态，不落库为真实流转。

## 4. 决策引擎设计（详见 DECISION_ENGINE.md）

- **计划**：LLM 优先、模板兜底。LLM 输出必须是 JSON（prompt/style/topic/steps/
  needs_browser/reference_image），解析失败或调用异常一律降级模板，任务不中断。
- **选源**：priority 严格优先（zen_gate_local=100 独占最高档），同档 weight 加权轮询；
  每源信号量并发 + 令牌桶 rpm 限速 + 429 冷却 30s 熔断。
- **反思**：纯规则评分（成功且无重试=1.0 / 成功=0.5 / 失败=0.0）+ 规则建议，
  写入 prompt_history，形成「计划→执行→复盘→再计划」的自迭代闭环。
- **凭证**：只有决策引擎持有 engine_token 并签发会话级临时凭证；生成线永不接触 vault。

## 5. 子进程隔离模型

| 子进程 | 入口 | 输入 | 输出 | 内存策略 |
|---|---|---|---|---|
| 浏览器 | browser_agent/browser_worker.py | stdin 一次性 JSON job | stdout 一行 `LM_RESULT_JSON:` | **不设 RLIMIT_AS**（Servo 虚拟地址 >4GB，设了必死；靠并发+硬件门禁控 RSS） |
| 生成 | media_generator/media_worker.py | stdin 请求（含 Key，仅内存） | 同上 | scheduler.subprocess_memory_limit_mb（默认 512） |

铁则：参数与密钥只走 stdin，绝不进命令行/日志/落盘文件；超时由 sandbox_wrapper 强杀整棵进程树。

## 6. 数据设计（SQLite，core/db.py，WAL 模式）

| 表 | 用途 | 关键字段 |
|---|---|---|
| tasks | 任务主记录（每 task_id 一行） | status/stage/progress/retry/result_json/provider/conversation_id |
| prompt_history | 复盘与经验沉淀 | task_id/skill/prompt/reflection/score |
| api_usage | API 消耗与 429 记录 | provider/kind/task_id/status/latency_ms |
| log_index | 日志索引（正文在内存环形缓冲+文件） | ts/level/task_id/stage |
| conversations / messages | 面板对话工作台的会话记忆 | conv_id/role/content |

## 7. 配置设计（config.json + core/config.py）

分段：server / scheduler / hardware / browser / upload / providers[]。
ConfigManager 负责缺省合并、类型校验、原子写、脱敏视图（面板只见 has_key 等摘要）、
文件变更热更新（硬件采样循环里调用 reload_if_changed）。
**密钥规则**：只存 config.json 或环境变量；zen_gate_local 的 api_key 留空时自动读
`ZEN_GATE_API_KEY`。仓库内的 config.json 必须保持所有 api_key 为空占位。

## 8. 安全边界（不可妥协，改代码前先读 docs/security.md）

1. 面板只监听 127.0.0.1，不开放外网。
2. 浏览器只用项目内 Browserr，profile 在 browser_profile/session_<task_id>/，不用完不清理凭证会话不算结束——结束必须 revoke。
3. Vault 落盘必须 Fernet 加密，cryptography 缺失时拒绝启动，禁止明文降级。
4. 日志必须过 core/logger.redact 脱敏；prompt 中禁止出现任何凭证信息。
5. Skill 名白名单正则 + 路径穿越校验（skill_loader._path），面板可写 Skill 但写不出 docs/skills/。

## 9. 已知限制与取舍

- Browserr/Servo 对重型 SPA、DRM、WebRTC 兼容性弱于 Chromium；headers 型凭证无注入口
  （runner 会写入一条 material 说明，不冒充已注入）；HttpOnly Cookie 无法经 JS 注入。
- zen-gate 免费车道按出口 IP 限流，chat 可能返回限流错误：planner 会降级模板，任务仍成功，
  但计划质量下降——这是预期行为，不是 bug（排查顺序见 DEPLOY.md 故障表）。
- 图片/视频暂无本地源：无可用源时走「模拟产物」并明确标注 simulated=true，禁止伪装真实生成。
- 硬件门禁：CPU/内存超阈值只暂停派发新任务，不杀已跑任务；自检模式可 set_hardware_gate(False)。
