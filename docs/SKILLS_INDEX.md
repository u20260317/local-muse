# Skill 库索引（对应 docs/skills/ 目录）

本目录是 local-muse 的任务 Skill 库：每个 `.md` 一个 Skill，文件头 front-matter
只写 `title / description / enabled` 三项（与 Agent Skills 通行格式对齐：
name 由文件名承担、description 决定何时被选用），正文是给策划 Agent 的工作流模板。
面板「Skill 管理」页可在线查看/新增/编辑/删除，保存即生效。

| Skill 文件 | 用途 | 何时选用 |
|---|---|---|
| `blessing_video_hit_v2.md` | 爆款祝福短视频：温暖治愈系选题、画面与镜头提示词 | 做祝福/治愈类日常短视频时（默认技能） |
| `storyboard_short_video.md` | 分镜短视频：封面镜 + 景别交替 + 逐镜旁白，专为 compose 合成优化 | 做通用题材、想要成片节奏与字幕配音齐整时 |
| `web_research.md` | 网络调研型：先采集资料再出纪实感内容 | 选题需要事实素材、数据或新闻背景时 |
| `auto_optimize_prompt.md` | 提示词自优化：按历史复盘自动改写画面描述 | 连续几轮画面质量不稳定、想自动收敛风格时 |

新增 Skill 的约定：
1. 文件名只用字母、数字、下划线、连字符（与 skill_loader 的白名单一致）。
2. description 写清「做什么 + 何时用」，这是被正确选用的关键。
3. 正文必须写明安全边界：不输出任何账号、凭证、密钥类信息。
4. 涉及合成参数（镜数、旁白长度、景别）时，与 docs/COMPOSE.md 的口径保持一致。
