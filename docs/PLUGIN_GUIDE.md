# 插件开发指南（PLUGIN_GUIDE）— v1.1 阶段插件化

> 借鉴 DeepSeek Harness 的全局插件体系（tool-plugin-manager 等），local-muse 的流水线
> 阶段在 v1.1 起全部插件化：阶段 = 插件，主干只负责按注册表执行。本文是加/改阶段的唯一规范。

## 1. 插件长什么样

定义在 `agent_engine/pipeline.py` 的 `StagePlugin`：

| 字段 | 含义 |
|---|---|
| name | 唯一名（小写英文），config 与日志都用它 |
| title | 中文标题，面板展示 |
| progress | 阶段进度锚点（0-100），只用于上报 |
| permissions | 权限声明集合，见第 3 节 |
| run | `async def run(ctx) -> str | None`：执行逻辑，返回记入 result["stages"] 的标签，None 表示不记录 |
| parallel_group | 同组相邻插件用 asyncio.gather 并行执行（组内插件必须无数据依赖） |
| skippable | 是否允许被 config 禁用 |

ctx 是全流水线共享的上下文字典，插件之间只许通过它传数据：
`services / runtime / spec / result / plan / image / credential_issued / browser_cfg / sched_cfg / timeout_s`。
需要新字段先在本表登记，再在 execute() 的 ctx 初始化处加默认值。

## 2. 内置插件一览（注册顺序 = 默认执行顺序）

| 插件 | 权限 | 并行组 | 说明 |
|---|---|---|---|
| plan | llm:text, db:read, skill:read, memory:read | - | planner.build_plan |
| browser | vault:issue, subprocess:browser, network:web | - | 唯一可签发凭证的插件 |
| image | api:image, subprocess:media | - | 与 browser 无数据依赖，但同为重型子进程，默认串行（见第 6 节） |
| video | api:video, subprocess:media | - | 依赖 image 产物，必须串行在 image 之后 |
| upload | network:upload | - | 默认跳过（占位） |
| persist | db:write | - | 落库，不记 stages 标签 |
| reflect | db:write, db:read, memory:write | - | 复盘 + 写长期记忆 |

## 3. 权限模型（硬约束）

- 权限是声明 + 校验，不是注释：`register()` 时若插件声明 `vault:issue` 而名字不在
  白名单 `VAULT_ISSUE_ALLOWED = {"browser"}`，直接抛 ValueError 拒绝注册。
- 新插件按最小权限声明：只读 DB 用 db:read，要联网要写清 network:*，要起子进程写 subprocess:*。
- 插件不许绕过 services 直接 import vault / config 单例；依赖一律从 ctx["services"] 取。

## 4. 加一个新阶段（五步）

1. 在 task_loop.py（或新模块）写 `async def _stage_xxx(ctx) -> str:`，逻辑里用 `_report()` 上报进度。
2. `pipeline.register(StagePlugin(name="xxx", title="…", progress=…, permissions=frozenset({...}), run=_stage_xxx))`。
3. 若与相邻阶段无数据依赖且想并行，给同一个 parallel_group；有依赖的绝不许同组。
4. 更新本文第 2 节表格与 docs/MODULES.md。
5. 跑 AI_GUIDE.md 的验证清单，并单独验证：config 里把新插件加进 pipeline.disabled 后流水线仍能跑完。

## 5. 配置控制（config.json → pipeline 段）

```json
"pipeline": {
  "disabled": ["upload"],
  "order": ["plan", "browser", "image", "video", "upload", "persist", "reflect"]
}
```

- disabled：按 name 禁用插件（reflect/persist 不建议禁，禁了就没有复盘与落库）。
- order：给出时按此顺序重排，未列出的插件排在末尾（保持注册相对顺序）。
- 面板 `/api/pipeline` 可查看全部插件及当前启停状态；看板「智能状态」卡有摘要。

## 6. 并行组的安全边界

**重型子进程阶段默认串行**：browser（Servo 引擎）与 image（媒体 worker）都是吃内存的大户，
在老硬件上同时跑容易触发硬件门禁与资源争抢，默认串行是保守策略；parallel_group 优先用于
轻量阶段（纯内存/纯 API 调用、无子进程）。确需并行重型阶段，先在目标机压测再开。
（注：v1.1 测试期出现过一次 `TimeoutError: no matching event`，事后定位真因是测试沙箱
VM 更换后 libEGL 图形库缺失、与并行无关，排查方法见 docs/BROWSERR_ZENGATE.md。）

gather 并行时组内任一插件抛错，异常会向 Scheduler 传播走重试；其余插件可能已产生
副作用（如已下载图片），重试时以 result 重新组装为准，插件实现要能接受重复执行
（幂等或覆盖写）。browser 与 image 并行时 runtime.pid 会被后完成者覆盖，仅影响面板展示，不影响进程树清理（PID 集合在 Scheduler 统一登记）。
