# 长期记忆模块（MEMORY_MODULE）— SQLite 版

> 借鉴 AI IDE 的记忆模块（如 CodeBuddy Memory）：只靠上下文窗口，任务每次从零开始，
> 同类失败反复重试、经验用完即弃。本模块把记忆沉淀进 SQLite，跨任务、跨重启可用。
> 代码：`core/memory.py`（MemoryStore），与 TaskDB 共用同一个数据库文件（WAL 模式）。

## 1. 存什么：memories 表

| 字段 | 含义 |
|---|---|
| kind | lesson 经验 / failure 失败 / fact 事实 / preference 偏好 |
| scope | 作用域：技能名 / provider 名 / global |
| key | 归一化指纹或主题键，与 (kind, scope) 组成唯一约束，重复写只更新不新增 |
| content | 正文（≤2000 字） |
| weight | 权重：每次被更新 +0.5，召回打分用 |
| hits | 被召回次数（failure 类则为累计失败次数） |
| created_at / updated_at | 时间戳 |

## 2. 谁写、谁读（闭环）

- **写**：planner.reflect 每次复盘后把 (score + 建议) 写成 lesson（scope=技能名，key=选题）；
  Scheduler._handle_failure 每次失败把指纹写成 failure（key=`stage:错误类型`，只累加 hits）。
- **读**：planner.build_plan 在出计划前 recall 两类：同技能 lesson（3 条）+ failure（2 条），
  拼进 LLM 提示词的「长期记忆」段与模板计划的经验段——失败教训会以「避坑（累计失败 N 次）」出现。
- **管**：面板「记忆库」页可检索、看统计、删单条；`/api/memory?q=&kind=` 是同一接口。

## 3. 召回算法（规则打分，无向量库）

query 切词：英文/数字按词、中文按 bigram；在 (key + content) 里统计命中，
`score = 命中数×2 + weight×0.5 + 新近度(30 天内线性衰减)`；同 scope 或 global 优先。
零依赖、离线可跑；要升级 embedding 检索只换 recall() 内部，接口与调用方不动。

## 4. 与 prompt_history 的分工

prompt_history 是流水账（每任务一行，供统计与最近复盘）；memories 是提炼后的长期资产
（按主题去重、带权重、会被主动召回）。两者都写，不互相替代。

## 5. 配置与边界

- config.memory.enabled=false 时整体停用（build_context 不创建 MemoryStore，services["memory"]=None，
  所有调用方都已做 None 容错，行为退回 v1.0）。
- 记忆内容禁止写入任何凭证/Key（与全局日志脱敏规则一致）；面板删除是硬删除，不可恢复。
- 失败指纹累计 ≥3 次时日志会打 WARNING 提示先查根因——这是给运维看的信号，不是自动熔断；
  要不要停某技能由人决定，不要在代码里擅自加自动禁用。
