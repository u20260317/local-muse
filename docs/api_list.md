# 免费 API 清单（api_list）

> **重要**：免费额度、模型名、端点路径与限速策略由各服务商自行调整，本文按编写时的公开资料整理，
> **实际使用前请以官方文档为准**，并在 `config.json` 中核对 `base_url` / `path` / `model` / `rpm`。
> 所有 Key 为空占位，未填写的源自动禁用，不会发起任何请求。
>
> **首选免费源**：`openrouter_free`（`priority=10`）使用 OpenRouter 带 `:free` 后缀的免费模型，
> 只覆盖**文本**链路（决策 / 复盘 / 计划）；OpenRouter 不提供文生图 / 文生视频，
> 图像与视频仍需第 3、4 节里的其它源。免费模型**仍然需要 API Key**，
> 在面板「全局配置 → API Key 管理」填 `openrouter_free` 后保存即可（`/sources` 页只管开关与配额）。
> 免费模型列表会变动，可在 <https://openrouter.ai/models> 挑当前带 `:free` 的模型，
> 修改 `config.json` 中该源的 `model` 字段。

## 1. 通用配置字段

`config.json` 的 `providers[]` 每项字段：

| 字段 | 含义 |
| --- | --- |
| `name` / `kind` | 唯一标识；`text` / `image` / `video` |
| `style` | 适配器风格（见第 5 节） |
| `base_url` / `path` | 端点地址与路径，最终 URL = `base_url + path` |
| `model` | 模型名 |
| `api_key` | **空占位**；在面板或 config.json 填写，代码零硬编码 |
| `enabled` / `weight` | 启用开关 / 同优先级档内的加权轮询权重 |
| `priority` | 选源优先级（默认 `0`，数值越大越优先）；只有最高档源全部不可用或 429 冷却时才降级到下一档 |
| `rpm` / `max_concurrency` | 每分钟请求数（令牌桶）/ 单服务商并发配额（信号量） |
| `timeout_s` | 单次请求 / 任务超时 |
| `doc` / `quota_note` / `risk_note` | 官方文档、额度说明、风险提示 |

可用判定：`enabled && api_key 非空 && base_url 非空 && 不在 429 冷却期`。

## 2. 文本模型（kind = text）

| 服务商 `name` | 端点 | 模型 / style | 免费额度（以官方为准） | 限速建议 | 官方文档 | 风险提示 |
| --- | --- | --- | --- | --- | --- | --- |
| **`openrouter_free`（首选）** | `https://openrouter.ai/api/v1` + `/chat/completions` | `google/gemma-4-31b-it:free` / `openai_chat` | `:free` 模型走免费层，有 RPM / 每日请求上限 | `rpm=10`，并发 1 | <https://openrouter.ai/docs/api-reference/chat> | **免费模型仍需 API Key**；免费模型列表常变动、可能下线；免费层数据可能用于模型改进，勿传敏感信息 |
| `minimax_text` | `https://api.minimax.chat` + `/v1/text/chatcompletion_v2` | `MiniMax-Text-01` / `minimax_chat` | 新用户赠送 / 活动额度 | `rpm=6`，并发 1 | <https://platform.minimaxi.com/document/ChatCompletion%20v2> | 额度与模型名可能调整；确认是否计费后再开启 |
| `deepseek_text` | `https://api.deepseek.com` + `/chat/completions` | `deepseek-chat` / `openai_chat` | 当期赠送余额，非永久免费 | `rpm=10`，并发 1 | <https://api-docs.deepseek.com/> | 余额耗尽后计费；并发过高易 429 |
| `gemini_free` | `https://generativelanguage.googleapis.com` + `/v1beta/models/gemini-2.0-flash:generateContent` | `gemini-2.0-flash` / `gemini` | 免费层按 RPM / RPD 限制，随地区与模型变化 | `rpm=10`，并发 1 | <https://ai.google.dev/gemini-api/docs> | 国内网络可能不可达；免费层数据可能用于模型改进，**勿传敏感信息** |
| `qwen_free` | `https://dashscope.aliyuncs.com` + `/compatible-mode/v1/chat/completions` | `qwen-plus` / `openai_chat` | 新用户部分模型百万级 Token 免费额度，有有效期 | `rpm=6`，并发 1 | <https://help.aliyun.com/zh/model-studio/developer-reference/compatibility-of-openai-with-dashscope> | 额度有效期与模型范围需确认 |

文本响应用 `_extract_text()` 统一解析：优先 `choices[0].message.content`，兼容 `reply` / `output` / `text`，
Gemini 走 `candidates[0].content.parts[0].text`。

## 3. 文生图（kind = image）

| 服务商 `name` | 端点 | 模型 / style | 免费额度（以官方为准） | 限速建议 | 官方文档 | 风险提示 |
| --- | --- | --- | --- | --- | --- | --- |
| `minimax_image` | `https://api.minimax.chat` + `/v1/image_generation` | `image-01` / `generic_image` | 以官方赠送额度为准 | `rpm=4`，并发 1 | <https://platform.minimaxi.com/document/Image%20Generation> | 图像接口可能单独计费，开启前确认余额 |
| `sd_free` | `https://api-inference.huggingface.co` + `/models/stabilityai/stable-diffusion-xl-base-1.0` | SDXL / `hf_inference` | Inference Providers 每月少量免费额度，规则常变 | `rpm=3`，并发 1 | <https://huggingface.co/docs/inference-providers/index> | 冷启动可能 503；需同意模型许可；国内网络可能不稳；返回二进制图片 |
| `wanxiang_free` | `https://dashscope.aliyuncs.com` + `/api/v1/services/aigc/text2image/image-synthesis` | `wanx2.1-t2i-turbo` / `dashscope_image` | 部分万相模型有免费额度 / 有效期 | `rpm=2`，并发 1 | <https://help.aliyun.com/zh/model-studio/text-to-image> | **异步任务**，需 `X-DashScope-Async: enable`；超出免费额度会计费 |

## 4. 文生视频 / 图生视频（kind = video）

| 服务商 `name` | 端点 | 模型 / style | 免费额度（以官方为准） | 限速建议 | 官方文档 | 风险提示 |
| --- | --- | --- | --- | --- | --- | --- |
| `minimax_video` | `https://api.minimax.chat` + `/v1/video_generation` | `T2V-01` / `minimax_video` | 通常**无长期免费额度**，仅活动赠送 | `rpm=2`，并发 1 | <https://platform.minimaxi.com/document/Video%20Generation> | 费用高、任务耗时长（分钟级），务必确认余额；建议默认关闭 |
| `free_video_trial` | 空（占位） | `generic_video` | 需自行寻找当期提供免费试用的服务 | `rpm=2`，并发 1 | <https://huggingface.co/docs/inference-providers/index> | **示例占位**：`enabled=false` 且 `base_url` 为空时永不调用；切勿填写来路不明的服务 |

> 视频生成是本项目最消耗资源与额度的一环。L430 上建议先用自检模式验证链路，再单独开启一个视频源。

## 5. 适配器风格（style）

| style | 请求形态 | 响应处理 |
| --- | --- | --- |
| `openai_chat` | `Authorization: Bearer <key>` + `{model, messages}` | 取 `choices[0].message.content` |
| `minimax_chat` | `Authorization: Bearer <key>` + `{model, messages}` | 同上，兼容 `reply` |
| `gemini` | URL 追加 `?key=<key>` + `{contents:[{parts:[{text}]}]}` | 取 `candidates[0].content.parts[0].text` |
| `generic_image` | `Authorization: Bearer <key>` + `{model, prompt, aspect_ratio, response_format}` | 找 URL / base64 |
| `hf_inference` | `Authorization: Bearer <key>` + `{inputs: prompt}` | 响应 `Content-Type: image/*` 时直接保存二进制 |
| `dashscope_image` | `Authorization` + `X-DashScope-Async: enable` | 返回 `task_id` → 轮询 `/api/v1/tasks/{task_id}` |
| `minimax_video` | `Authorization: Bearer <key>` + `{model, prompt, first_frame_image?}` | 返回 `task_id` → 轮询 `/v1/query/video_generation?task_id=...` |
| `generic_video` | 通用 POST | 找 URL / base64 / task_id |

`media_worker.py` 用统一的递归提取器处理三种返回形态：

1. `_find_url()`：资源类字段或带扩展名 / 下载特征的 http(s) 链接 → `downloader.fetch()`。
2. `_find_base64()`：长 base64 字符串 → `downloader.save_base64()`。
3. `_find_task_id()`：异步任务编号 → `_poll()` 按 `poll_url` 模板轮询，最多 `max_polls` 次。

**参考图输入**：`build_generation_request()` 把本地参考图读成 `data:image/png;base64,...` 放进
`first_frame_image`，实现图生视频；HTTP URL 形式的参考图直接透传。

## 6. 风控规避策略

| 机制 | 实现位置 | 行为 |
| --- | --- | --- |
| 单服务商并发锁 | `ApiPool.acquire/release` | 每源独立 `asyncio.Semaphore(max_concurrency)` |
| 令牌桶限速 | `ApiPool.acquire` | 按 `rpm/60` 补充令牌，不足则异步等待，不阻塞调度 loop |
| 优先级 + 加权轮询 | `ApiPool.pick` | 先按 `priority` 取最高档（`openrouter_free` 为 10），档内按 `weight` 展开候选；跳过禁用 / 缺 Key / 冷却中的源，最高档全不可用才降级 |
| 429 冷却 | `ApiPool.report_429` | 写入冷却截止时间（默认 ≥5s），`pick()` 自动绕过 |
| 请求超时 | `media_worker` / `sandbox_wrapper` | HTTP 超时 + 子进程总超时，超时强杀进程树 |
| 消耗日志 | `ApiPool.note_usage` → `api_usage` 表 | 面板可查看每源调用次数、状态、429 时间线 |
| 任务级重试 | `Scheduler._run_task` | 最多 2 次，退避 1s / 2s，失败按 task_id 入库 |

## 7. 下载与完整性校验

- 流式写 `*.part` → `os.replace()` 原子改名，避免半成品被面板预览。
- `verify()` 检查大小与魔数：PNG / JPEG / GIF / WebP / MP4(`ftyp`) / WebM；未知类型判定为不通过。
- `dry_run` 产物单独标注：图片为 1×1 占位 PNG，视频只写 `simulated_video.json` 清单，**不伪造 mp4**。

## 8. 新增一个服务商

1. 在 `config.json` 的 `providers[]` 加一项，填 `name / kind / style / base_url / path / model / rpm / max_concurrency`。
2. 在面板「API 源」页启用并填 Key（或直接在 config.json 填 `api_key`）。
3. 若响应形态特殊，在 `media_generator/media_worker.py` 的提取器或 `api_pool.build_generation_request()` 增加分支。
4. 在本文表格补充额度、限速、文档与风险提示。
5. 用「自检模式」确认选源与队列逻辑，再用一条真实低额度任务验证。

## 9. 风险提示汇总

- 免费额度不是永久承诺，可能产生费用；**务必在服务商控制台设置余额告警与用量上限**。
- 请勿把任何 Cookie、Token、登录凭证写进 prompt 或传给免费 API；免费层可能保留请求数据。
- 国内网络访问部分境外端点不稳定，失败会按 task_id 单独重试，不影响其他并发任务。
- 不要为「提速」调高 `rpm` 与 `max_concurrency`：L430 的瓶颈是内存与网络，触发 429 反而更慢。
- `free_video_trial` 是占位源，未配置 `base_url` 时永不调用，避免误连不可信服务。
