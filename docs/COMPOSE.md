# 成片合成（compose）说明

> v1.2 新增。流水线第八个阶段之前的最后加工：把分镜素材合成为一条
> **可直接发布**的竖屏短视频——有运镜、有转场、有字幕、有配音、可混 BGM。
> 参考 MoneyPrinterTurbo 的合成管线与 AI-Director 类项目的分镜拼接思路，
> 按本项目的插件化/可降级原则重新实现（未使用其代码）。

## 流水线位置

```
plan（出分镜表）→ browser（可选）→ image（逐镜出图）→ video（可选文生视频）
→ compose（本阶段）→ upload（可选）→ persist → reflect
```

## 输入与产物

输入（由前面阶段备齐）：

| 输入 | 来源 | 说明 |
|---|---|---|
| 分镜表 scenes | plan 阶段 | 每镜 image_prompt（画面词）+ narration（一句旁白） |
| 分镜图 | image 阶段 | 按分镜逐镜生成，`ctx["images"]` |
| 视频片段（可选） | video 阶段 | 仅**真实**（非模拟）片段会被插入为动态镜头（第二镜） |
| 旁白音频 | compose 阶段内调 TTS | edge-tts 逐镜合成，ffprobe 实测时长 |

产物（`output/<task_id>/`）：

- `final.mp4` —— 成片（H.264 + AAC，faststart，默认 1080×1920 / 30fps）
- `voice_<n>.mp3` —— 逐镜旁白（便于单独试听/重配）
- `final_parts/` —— 合成中间件（逐镜片段、静音轨、字幕 subs.ass），排障时看这里

任务结果 `result.final_video` 记录镜数/时长/配音/字幕/BGM 实际情况；
素材列表里成片的 provider 标注为 `ffmpeg-compose`。

## 合成工序（media_generator/compose.py）

1. **逐镜制片**：静图用 zoompan 做推/拉运镜（偶数镜推近、奇数镜拉远，幅度 15%），
   先放大到 2 倍尺寸再运镜以减轻锯齿；视频片段做 scale+crop 归一化并循环补时长。
   单镜时长 = 旁白实测时长 + 0.3s；无旁白时用 `scene_seconds`（默认 4s），
   并夹在 2.5s–15s 之间。
2. **转场拼接**：xfade 淡化转场（默认 0.5s）；ffmpeg 不支持时自动退回 concat 直拼。
3. **配音轨**：逐镜音频按时间槽 apad/裁剪对齐后拼接；缺失的镜补等长静音，
   保证音画时间轴严格一致（字幕共用同一条时间轴）。
4. **字幕**：按时间槽生成 ASS（PlayRes 与成片一致），字体按平台自动选
   （Windows 微软雅黑 / macOS 苹方 / Linux Noto Sans CJK），白字描边、底部居中，
   在终遍用 libass 烧录进画面。
5. **BGM（可选）**：`compose.bgm_path` 指向音频文件时循环混入，音量权重 0.16
   并做 dynaudnorm 防爆音；不配则只有旁白轨。
6. **封装**：libx264（crf 20 / veryfast）+ AAC 128k，`+faststart` 便于边下边播。

## 降级矩阵（任何一层坏掉都不判任务失败）

| 故障 | 行为 |
|---|---|
| edge-tts 不可用/无网 | 该镜无配音，字幕照常，成片继续 |
| ffmpeg 无 xfade | 退回 concat 直拼（无转场） |
| libass 不可用 | 退回无字幕成片，result 里 subtitles=false |
| 完全没有 ffmpeg | compose 阶段整体跳过并记录原因，原始素材保留 |
| 分镜缺失（老计划） | 按单镜处理（整图 + 总提示词旁白为空） |

## 配置（config.json 的 compose 段）

| 键 | 默认 | 说明 |
|---|---|---|
| enabled | true | 关掉则跳过合成，流水线回到 v1.1 行为 |
| width / height | 1080 / 1920 | 成片尺寸（横屏改 1920×1080 即可） |
| fps | 30 | 帧率 |
| scene_seconds | 4.0 | 无旁白时单镜时长 |
| transition_seconds | 0.5 | 转场时长 |
| voice_enabled | true | 是否配音（dry_run 任务永不配音） |
| voice | zh-CN-XiaoxiaoNeural | edge-tts 音色，可换 zh-CN-YunyangNeural 等 |
| bgm_path | "" | BGM 文件路径（相对路径按应用根目录解析），空 = 不混 |

## ffmpeg 从哪来

优先系统 PATH 里的 ffmpeg；找不到时用 imageio-ffmpeg 包自带的二进制
（三个安装包都内置了它，所以装完包即有合成能力，无需另装 ffmpeg）。
版本要求：xfade 需要 ffmpeg ≥ 4.4，老版本自动走 concat 降级。

## 排障

- 成片时长不对：看 `final_parts/` 里逐镜 clip 的时长与 subs.ass 的时间槽是否一致。
- 字幕是方框：Linux 缺 CJK 字体，装 fonts-noto-cjk 后重试。
- 配音断断续续：旁白单句过长（>28 字）会被读得很赶，按 Skill 的分镜规则拆句。
