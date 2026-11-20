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
