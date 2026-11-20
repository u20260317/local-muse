<div align="center">

# 🧠 Local-Muse

### 本地自治 Agent 系统 · 决策引擎驱动 · 为老硬件而生

[![Latest Release](https://img.shields.io/github/v/release/u20260317/local-muse?style=for-the-badge&color=6f42c1)](https://github.com/u20260317/local-muse/releases)
[![Stars](https://img.shields.io/github/stars/u20260317/local-muse?style=for-the-badge&color=yellow)](https://github.com/u20260317/local-muse/stargazers)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](#快速开始)
[![Engine](https://img.shields.io/badge/Browser-Browserr%20·%20Servo-orange?style=for-the-badge)](#核心特色)
[![API](https://img.shields.io/badge/API-本地%20zen--gate%20only-brightgreen?style=for-the-badge)](#核心特色)

**调度器 7×24 接单 → 决策引擎出计划 → 隔离浏览器采集 → 文/图/视频生成 → 独立复盘自我迭代**
**全程本地运行，不接管系统浏览器，不调用任何远程 API 站点**

[🚀 快速开始](#快速开始) · [📐 项目设计](docs/DESIGN.md) · [🧭 决策引擎](docs/DECISION_ENGINE.md) · [🧩 模块地图](docs/MODULES.md) · [🛠 部署手册](docs/DEPLOY.md) · [🤖 AI 维护指南](docs/AI_GUIDE.md)

</div>

---

## 核心特色

| | |
|---|---|
| 🧭 **决策引擎** | LLM 优先出计划、模板兜底永不中断；任务后独立复盘打分，经验写回供下一轮迭代 |
| ⚙️ **并发调度中枢** | 优先级队列 + 只分发不等待的主循环；单任务独立超时/重试/取消，硬件超阈值自动暂停派发 |
| 🌐 **Browserr 浏览器** | Servo 引擎替代 Chromium：项目内独立二进制（sha256 校验安装）、每任务独立 profile 与子进程 |
| 🔌 **API 只用本地** | 唯一启用源是本机 zen-gate（OpenAI 兼容）；priority 选源 + 令牌桶限速 + 429 冷却熔断 |
| 🔐 **凭证保险库 Vault** | Fernet 加密落盘；只有决策引擎能签发会话级临时凭证，任务结束立即销毁 |
| 🧩 **全程模块化** | 22 个模块统一「【核心模块】」中文横幅标注，职责/接口/禁区全在 [模块地图](docs/MODULES.md)，拆改像搭积木 |
| 📚 **手把手文档** | 设计、决策引擎、部署、AI 维护四件套，任何人或 AI 都能 30 分钟上手维护迭代 |
| 💻 **老硬件友好** | 为 ThinkPad L430 级别机器设计：子进程隔离、内存门禁、模拟产物明确标注不造假 |

## 架构一览

```
面板(Flask, 127.0.0.1:8765) ──bridge──▶ Scheduler 调度中枢
                                          ├─ agent_engine/  决策引擎：计划 · 流水线 · 调度 · Skill 库
                                          ├─ media_generator/ 生成线：API 池选源限流 · 子进程请求/下载/校验
                                          ├─ browser_agent/  浏览器线：Browserr 唯一启动点 · 子进程隔离 · Vault
                                          └─ core/           基础层：配置 · SQLite · 硬件采样 · 日志脱敏 · 数据契约
一条任务：plan → browser(可选) → image → video → upload(可选) → persist → reflect
```

## 快速开始

```bash
bash scripts/setup.sh                       # venv + 依赖 + 装 Browserr 引擎 + 环境自检
# 先启动本机 zen-gate（见 docs/DEPLOY.md 第 4 节），再注入它的 mainKey：
export ZEN_GATE_API_KEY=<zen-gate mainKey>
.venv/bin/python main.py                    # 面板 http://127.0.0.1:8765
.venv/bin/python main.py --setup-check      # 环境自检（实测 9/9 通过）
.venv/bin/python main.py --dry-run-test     # 全模拟冒烟（实测 3/3 通过）
```

> Linux 无头运行需 EGL：`sudo apt install libegl1 libgl1 libgbm1 libgl1-mesa-dri`
> 完整手把手步骤（装引擎、起 zen-gate、注 key、三级冒烟、故障表）见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/DEPLOY.md](docs/DEPLOY.md) | 手把手部署手册：每步带验证命令与故障表 |
| [docs/DESIGN.md](docs/DESIGN.md) | 项目设计：架构分层、任务生命周期、子进程隔离、数据与安全边界 |
| [docs/DECISION_ENGINE.md](docs/DECISION_ENGINE.md) | 决策引擎详解：计划生成、调度、选源熔断、复盘自迭代 |
| [docs/MODULES.md](docs/MODULES.md) | 模块地图：每个模块的职责 / 接口 / 依赖 / 禁区 |
| [docs/AI_GUIDE.md](docs/AI_GUIDE.md) | 给接手 AI 的维护指南：10 条不变量、改动配方、验证清单 |
| [docs/BROWSERR_ZENGATE.md](docs/BROWSERR_ZENGATE.md) | Browserr + zen-gate 迁移说明与全量实测结果 |

## 版本

| 版本 | 内容 | 入口 |
|---|---|---|
| **browserr-zengate-v1.0.0**（当前主线） | Browserr（Servo）+ 本地 zen-gate + 全模块化文档 | [Release](https://github.com/u20260317/local-muse/releases/tag/browserr-zengate-v1.0.0) |
| original-chromium-v1.0.0（存档） | 最初的 Chromium / Playwright + 远程源聚合版，仅存档对照 | [Release](https://github.com/u20260317/local-muse/releases/tag/original-chromium-v1.0.0) |

## 实测状态（2026-11-20）

- ✅ setup-check 9/9 · dry-run 3/3 · 真实端到端任务 succeeded（Browserr 真实采集 + 截图落盘）
- ✅ zen-gate 网关 / 鉴权 / 模型列表正常；chat 偶遇上游免费车道按出口 IP 限流时，决策引擎自动降级模板计划，任务不中断
- ⚠️ 已知差异：Browserr 无自定义请求头注入口（代码如实标注不冒充）；HttpOnly Cookie 无法经 JS 注入；图片/视频暂无本地源时走明确标注的模拟产物
