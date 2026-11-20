# 更新日志（CHANGELOG）

本项目遵循语义化版本；每个版本都写清「新增 / 优化 / 修复 / 已知限制」，
方便任何人（或 AI）快速判断要不要升级、升级会动到什么。

## v1.2.0（开发中）

### 新增
- **成片合成阶段 compose**：分镜静图/片段经 ffmpeg 合成为可发布成片——
  zoompan 推拉运镜、xfade 转场、ASS 字幕烧录、逐镜配音对齐、可选 BGM 混音。
  参考 MoneyPrinterTurbo 的合成管线与 AI-Director 类项目的分镜拼接思路重新实现，
  工序与降级矩阵见 docs/COMPOSE.md。
- **分镜计划**：plan 阶段产出分镜表（首镜封面镜、景别交替、每镜一句 ≤28 字旁白），
  image 阶段改为逐镜出图；新增任务 Skill `storyboard_short_video` 专门驱动分镜生产。
- **配音（TTS）**：edge-tts 逐镜合成旁白（免费、免 Key），实测时长对齐镜头与字幕；
  不可用时自动降级无配音。新增 media_generator/tts.py。
- **系统托盘**：`--tray` 托盘模式（仿 zen-gate）：打开控制台 / 实时状态 /
  复制面板地址 / 打开输出目录 / 重启 / 退出；无桌面环境自动回退普通模式。
- **Agent 操作手册**：docs/skill/SKILL.md（按 Agent Skills 通行格式编写，
  供其他 AI Agent 安装、运维与调用本系统）；Skill 库索引 docs/SKILLS_INDEX.md。
- **万相出图/出视频接线**：DashScope 异步视频协议（dashscope_video）打通，
  wanxiang_free（图）与 wanxiang_video（视频）共用一个百炼 Key 即可启用真实生成。

### 优化
- 视频阶段定位调整：文生视频从「成片来源」改为「可选动态镜头来源」，
  真实片段自动插入成片第二镜；成片质量不再受单个 t2v 片段的长度与额度限制。
- 合成多级降级：xfade→concat、字幕→无字幕、合成失败→跳过保素材，
  任何一层故障都不判任务失败。
- Skill 库格式与 Agent Skills 标准对齐（name 由文件名承担、description 决定选用），
  并补齐每个 Skill 的「何时选用」说明。

### 已知限制
- edge-tts 依赖联网访问微软 TTS 服务，离线环境只有字幕无配音。
- Windows 便携包靠 imageio-ffmpeg 自带二进制合成（无需另装 ffmpeg）；
  系统装了更新版 ffmpeg 时优先用系统的。
- 托盘在 Linux 桌面需要系统指示器组件（AppIndicator），缺失时自动回退普通模式。

## v1.1.0（2026-11-20，agent-loop）

### 新增
- 阶段插件化：七个阶段全部迁为可注册插件（pipeline 段可启停/排序），
  新增 docs/PLUGIN_GUIDE.md 手把手教写新阶段。
- Agent Loop 目标续行：每日自动续跑（轮数上限 + 防堆叠三闸）。
- Token 计量与上下文压缩：planner 历史/会话按预算「保新弃旧」。
- SQLite 长期记忆：经验/失败指纹跨任务召回，面板新增「记忆库」页。
- 面板智能状态卡（续行/Token/插件/记忆）与 /api/pipeline、/api/memory 接口。

### 优化
- 快照 1 秒投影缓存，面板轮询开销显著下降；API 池改线程级连接池复用。

## v1.0.0（2026-11-20，browserr-zengate）

- 浏览器引擎由 Chromium/Playwright 换为 Browserr（Servo），子进程隔离运行。
- API 源收敛为仅本地 zen-gate（其余源默认关闭），Key 走环境变量/本地配置。
- 全模块按「【核心模块】」中文横幅标注，补齐设计/部署/AI 维护文档。

## 存档

- original-chromium-v1.0.0：最初的 Chromium + 远程源聚合版，仅作对照存档。
