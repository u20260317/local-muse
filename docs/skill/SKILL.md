---
name: local-muse
description: 本地自治短视频 Agent 系统的操作手册（供 AI Agent 阅读执行）。当用户要求安装、启动、配置或运维 local-muse，或要求它生产一条短视频时使用本 Skill：系统在本地以流水线方式完成计划→分镜出图→配音→ffmpeg 合成成片，全程只监听 127.0.0.1。
---

# local-muse · Agent 操作手册

## 这是什么

local-muse 是一套跑在用户本机的自治 Agent 系统：给它一个选题，它会自己出计划、
拆分镜、调文生图、配音、合成，产出一条可发布的竖屏短视频。它只用本地 API 网关
（zen-gate）与用户自备的图片/视频源 Key，不上传用户数据，面板只绑 127.0.0.1。

## 流水线（八个阶段，全部插件化，可在 config 的 pipeline 段启停/排序）

plan（分镜计划）→ browser（可选采集）→ image（逐镜出图）→ video（可选文生视频）
→ compose（ffmpeg 成片合成）→ upload（可选）→ persist（落库）→ reflect（复盘记忆）

## 安装与启动

1. Windows：解压便携包后双击 `local-muse.exe`（默认托盘模式，无控制台窗口）。
2. Linux：装 .deb 后运行 `local-muse`；或直接运行 AppImage。
3. 源码：`pip install -r requirements.txt && python main.py [--tray] [--port 8765]`
4. 打开面板 http://127.0.0.1:8765 ；`python main.py --setup-check` 做环境自检。

## 关键配置（config.json）

- `providers`：各生成源（enabled + api_key 才会被选用）。出图/出视频推荐百炼万相
  （wanxiang_free / wanxiang_video，共用一个 DashScope Key）。
- `compose`：成片参数（宽高/fps/单镜时长/转场/配音音色/BGM 路径），见 docs/COMPOSE.md。
- `hardware.memory_threshold`：内存高于阈值时暂停派发任务（老机器保护），不是故障。
- `agent_loop`：目标续行（每日自动续跑的轮数上限与总目标）。

## 生产一条视频

在面板「对话工作台」输入选题并发送，或等待每日定时/目标续行自动生产。
产物在 `output/<task_id>/`：分镜图、旁白 mp3、final.mp4（成片）与合成中间件目录。

## 排障速查

- 任务一直 queued：多半是硬件门禁（内存超阈值），腾出内存或调高阈值后自动恢复。
- 成片没有配音：edge-tts 需要联网访问微软 TTS 服务；失败自动降级无配音，字幕照常。
- 成片是模拟产物：对应生成源未启用或没填 Key，系统如实标注 simulated，不冒充真实。
- 合成被跳过：result 里 compose.skipped 带原因（多为缺 ffmpeg）；装 ffmpeg 或
  imageio-ffmpeg 后重试。合成失败不影响原始素材保留。

## 安全边界

- 不读取、不输出任何 API Key / 凭证原文；Key 只填 config.json 或经安全录入保存。
- 浏览器采集只走 Browserr 子进程；凭证只经 Vault 签发会话级临时凭证。
