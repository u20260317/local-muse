# 决策引擎详解（DECISION_ENGINE）

> 对应代码：`agent_engine/`（planner.py / task_loop.py / task_queue.py / skill_loader.py）。
> 这是本项目的核心：其它模块都是它的手脚。改决策逻辑前先读完本文。

## 1. 组成与职责

| 文件 | 角色 | 一句话 |
|---|---|---|
| task_queue.py `Scheduler` | 调度中枢 | 只分发不等待；并发、超时、重试、取消、硬件降级、定时注入全在这里 |
| planner.py | 大脑 | build_plan 出计划（LLM 优先/模板兜底）；reflect 出复盘并写回经验库 |
| task_loop.py `execute()` | 流水线 | 把一条任务按七阶段串起来，阶段进度三处同步（runtime/SQLite/日志） |
| skill_loader.py | 技能库 | docs/skills/*.md 的安全读写，是计划的「知识来源」之一 |
| core/models.py | 契约 | TaskSpec/TaskRuntime/TaskStatus/ProviderConfig，改字段先改这里并全局搜引用 |

services 字典（task_loop/planner 唯一入参，Scheduler._services 组装）：
`config, db, pool, vault, hardware, skill_loader, engine_token, register_pid, unregister_pid, on_report`。
新模块需要新依赖时，加进 services，不要用全局变量或互相 import 业务模块。

## 2. 计划生成（planner.build_plan）

输入：TaskSpec（skill/instruction/dry_run/conversation_id）。
步骤：
1. `skill_loader.get(spec.skill)` 取技能正文；技能不存在不报错，降级为 default 空技能。
2. `db.recent_prompts(skill, limit=5)` 取同技能历史复盘；`db.conversation_context(conv_id, limit=8)` 取会话上下文（只含角色/正文，永不含凭证）。
3. 非 dry_run 时 `pool.pick("text")` 选文本源（当前只有 zen_gate_local 可用）；
   system prompt 强制「只输出 JSON 且禁止输出凭证」；user prompt 拼选题+技能正文(截 800 字)+历史复盘+会话上下文。
4. `_extract_json` 用正则抠第一个 JSON 对象（容忍 ```json 包裹）；解析出且含 prompt 才采用，
   并与模板计划 merge（模板补缺省字段），标记 source=llm。
5. 任何异常（网络/限流/解析失败）→ 模板计划 source=template；dry_run 时 source=dry_run。
   **计划阶段永不抛错阻断任务**，这是刻意设计。

模板计划（_template_plan）：按技能名含 "research" 切换风格（纪实感 vs 温暖治愈）；
steps 固定 [browser, image, video, reflect]；needs_browser = 技能名含 "research"。

计划 dict 契约（下游只依赖这些键）：
`prompt, style, topic, steps, needs_browser, reference_image, source, provider`。
新增键可以，下游用 .get 读取；删键/改名必须同步 task_loop 与 reflect。

## 3. 流水线（task_loop.execute）七阶段

| 阶段 | progress | 做什么 | 失败语义 |
|---|---|---|---|
| plan | 10 | build_plan | 不失败（内部降级） |
| browser | 25 | needs_browser 才跑：vault 签发凭证→子进程 Browserr 采集（默认动作 goto example.com/extract body/screenshot research.png） | 子进程 ok=false → 抛错走重试 |
| image | 50 | image_gen.generate（选源在主进程，请求在子进程） | 无源→模拟产物（不算失败）；真实源失败→抛错 |
| video | 72 | video_gen.generate，image 产物作参考图 | 同上 |
| upload | 86 | 默认关闭直接 skipped | 不失败 |
| persist | 92 | update_task(result_json) | — |
| reflect | 97 | planner.reflect 写 prompt_history | 不失败 |

凭证生命周期：browser 阶段前 `vault.issue_session_credential(task_id, cards, engine_token)`；
finally 里只要签发过就 `vault.revoke(task_id)`；Scheduler 的 cancel 与 _on_task_done 也各兜底 revoke 一次。
改流水线时**不许动这条链**。

进度上报：`_report()` 同时更新 runtime、SQLite（on_report 回调）、日志 stage，三处缺一不可。

## 4. 调度（Scheduler）

- 队列：asyncio.PriorityQueue，元素 (effective_priority, seq, spec)；MANUAL=1/SCHEDULED=2/
  HIGH_TEST=0；enqueue_mode=insert 时 effective=-1 插队。
- 主循环 _dispatch_loop：每 250ms 检查「未降级且 active < max_concurrency」才出队，
  create_task 后立即下一轮——**主循环里出现 await 业务代码就是 bug**。
- 单任务 _run_task：wait_for 超时（task_timeout_seconds，默认 600，下限 30）；
  失败按 retry_times（默认 2）重试，退避 min(2^attempt, 5)s；每次失败先杀该任务进程树再重试。
- 取消 cancel(task_id)：杀进程树 + revoke 凭证 + 句柄 cancel + 落库 CANCELLED，不影响其它任务。
- 硬件循环 _hardware_loop：采样周期 hardware.sample_interval_seconds（默认 2s）；
  degraded 时暂停派发（不杀在跑任务）；同时负责 config 热更新检查。
- 定时循环 _schedule_loop：interval_seconds（默认 300）触发 _enqueue_scheduled：
  今日 scheduled 数达 daily_task_limit（默认 10）跳过；技能在 enabled 列表里轮转。

## 5. API 选源与熔断（media_generator/api_pool.py，决策引擎的资源层）

- available = enabled && api_key 非空 && base_url 非空（zen_gate_local 的 key 可来自环境变量注入）。
- pick：先过滤 exclude/不可用/冷却中，再取 priority 最高档，同档按 weight 展开加权轮询。
  **低档源永远不会在高档可用时被选**——加新源时 priority 填错会「永远选不中」或「永远抢占」，先想清楚。
- acquire/release：每源 Semaphore(max_concurrency) + 令牌桶（rpm/60 速率，容量 rpm）。
- report_429：冷却默认 30s（下限 5s），pick 自动跳过；zen-gate 上游限流返回 429 时即走此路，
  planner 随后降级模板——日志里看到「触发 429，冷却」+「降级模板」连着出现属正常链路。
- call_text：to_thread 执行同步 requests，主循环不阻塞；响应文本提取兼容 OpenAI/Gemini/通用字段。

## 6. 复盘与自迭代（planner.reflect）

- 评分：成功且 retry=0 → 1.0；成功 → 0.5；失败 → 0.0（succeeded 由调用方显式传入，不要读 runtime.status——reflect 执行时状态还没置 SUCCEEDED）。
- 建议规则：失败/模拟/重试/历史成功率<0.6 各对应一条固定建议，无命中则「链路稳定…」。
- 落库 add_prompt_history（prompt 截 2000 字）；下一轮 build_plan 会读到，形成闭环。
- 想升级为 LLM 复盘可以，但必须保留规则兜底与同样的落库字段。

## 7. 扩展决策引擎的正确姿势

- **加一个流水线阶段**：在 task_loop.execute 里按编号插入，_report 给新 progress，
  result["stages"] 追加阶段名，失败语义先想清（抛错重试还是降级跳过），并更新本文表格。
- **加一个 Skill**：在 docs/skills/ 新建 md（front-matter: title/description/enabled），
  技能名含 research 会自动触发浏览器阶段——这是约定，不要另加开关字段。
- **换/加文本源**：只改 config.json providers（见 DEPLOY.md 第 6 节），不要在 planner 里写死任何端点。
- **改重试/并发**：改 config.json scheduler 段，不要改代码默认值散落各处。

## 8. v1.1 Agent Loop 升级（插件化 / Token / 记忆 / 续行）

- **阶段插件化**：七阶段注册到 pipeline 注册表（agent_engine/pipeline.py），task_loop 只组装 ctx 并按表执行；注册表支持并行组，但 browser/image 均为重型子进程，老硬件上默认串行（保守策略，见 PLUGIN_GUIDE 第 6 节）。加阶段走 docs/PLUGIN_GUIDE.md，不要再往 execute() 里塞 if。
- **Token 计量与压缩**：core/token_meter.py 负责估算与预算压缩；planner 喂历史前先按 planner.history_token_budget / conversation_token_budget 压缩；api_pool.call_text 把每次调用的估算 token 记入 TokenLedger，面板 providers 快照带 tokens_in_est/tokens_out_est。
- **长期记忆**：core/memory.py（SQLite memories 表）；reflect 写 lesson、调度失败写 failure、plan 前召回，详见 docs/MEMORY_MODULE.md。
- **目标续行**：Scheduler._enqueue_agent_round 按 config.agent_loop 在每个调度 tick 检查：启用、未达 max_rounds_per_day、无在跑/排队的 agent_loop 任务，三闸全过才入队一轮，指令带上轮复盘摘要。
- **快照投影缓存**：Scheduler.snapshot() 1 秒 TTL 缓存，_persist 落库即失效；面板高频轮询不再全量现算。
- **连接池**：api_pool 的 HTTP 改为线程级 Session 复用（pool 16），_http_json 接口不变。
