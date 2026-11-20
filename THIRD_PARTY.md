# 第三方项目致谢与许可（THIRD_PARTY）

local-muse 的浏览器与本地 API 能力建立在以下开源项目之上，特此致谢。
本项目**不打包、不再分发**它们的代码或二进制：Browserr 在安装期由用户机器从其
官方 Release 下载（scripts/install_browserr.py，带 sha256 校验）；zen-gate 由用户
自行部署在本机，local-muse 仅通过其 OpenAI 兼容 API 调用。

| 项目 | 作者 / 来源 | 许可 | 在本项目中的角色 |
|---|---|---|---|
| [Browserr](https://github.com/Omodaka9375/browserr) | Omodaka9375 | Apache-2.0 | 默认浏览器引擎：独立子进程运行，负责页面采集/截图（tools/driver.py SDK 协议对接） |
| [Servo](https://servo.org/) | Mozilla / Servo 项目 | MPL-2.0 | Browserr 的底层渲染引擎（经由 Browserr 使用，未直接链接） |
| [zen-gate](https://github.com/LAGcomcom/zen-gate) | LAGcomcom | MIT | 本地 API 网关：唯一的默认文本源 zen_gate_local（127.0.0.1:8787） |
| Flask / requests / schedule / psutil | 各自社区 | BSD / Apache-2.0 等 | Web 面板、HTTP 客户端、定时与硬件采样（见 requirements.txt） |

补充说明：
- zen-gate 的 LICENSE 中注明其 lane 协议部分源自 dsh-our-free-model（zouyuxuan122，MIT），一并致谢。
- 若上游项目的许可或链接变更，以其官方仓库为准；发现署名遗漏请提 Issue，本项目会及时补正。
- 本项目的插件化 / 记忆 / 目标续行设计借鉴了 DeepSeek Harness 的全局插件理念（仅设计思路，未使用其代码）。

## v1.2 设计参考（仅思路，未使用代码）

| 项目 | 许可 | 借鉴点 |
|---|---|---|
| [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)（harry0703） | MIT | 「脚本→素材→配音→字幕→配乐→ffmpeg 合成」的一体化成片管线与多级降级做法；其 docs/skill 的 Agent 手册形态 |
| [anthropics/skills](https://github.com/anthropics/skills)（Anthropic） | 见其仓库 | Agent Skills 的 SKILL.md 通行格式（front-matter + 渐进披露），本项目 docs/skill/SKILL.md 与 Skill 库格式与之对齐 |
| [dsh-media-desk](https://github.com/woniuniuniu/dsh-media-desk)（woniuniuniu） | 见其仓库 | 「一句话灵感 → 脚本 / 封面 / 配图 / 分镜」的自媒体工作流拆解，映射为本项目的分镜表结构 |
| [Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter)（elementalsouls） | 见其仓库 | 大型 Skill 束的分类与索引组织方式（本项目 docs/SKILLS_INDEX.md） |
| [magpie](https://github.com/yetone/magpie)（yetone） | 见其仓库 | 「所有模型收拢到一个网关」的产品形态，与本项目 API 池/zen-gate 的定位互相印证 |
| [Lightpanda](https://github.com/lightpanda-io/browser)（lightpanda-io） | 见其仓库 | 为 AI 场景设计的轻量无头浏览器，已列入浏览器引擎的后续评估清单（当前仍用 Browserr） |

## v1.2 新增运行依赖

| 组件 | 用途 | 说明 |
|---|---|---|
| edge-tts | 成片配音 | 微软在线 TTS 的 Python 客户端（免费、免 Key）；仅在合成时联网调用，失败自动降级 |
| imageio-ffmpeg | ffmpeg 二进制来源 | 系统未装 ffmpeg 时提供静态二进制，三个安装包均内置，保证开箱即有合成能力 |
| pystray / Pillow | 系统托盘 | 托盘图标与菜单（可选依赖，缺失时自动回退普通服务模式） |
