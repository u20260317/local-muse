# Local-Muse（Browserr + 本地 zen-gate 改造版）

本地自治 Agent 系统：调度器 + 决策引擎 + 隔离浏览器采集 + 文图视频生成 + 自我复盘。
本版与原版（main 分支）的区别只有两条主线，详见 docs/BROWSERR_ZENGATE.md：

1. **浏览器**：Chromium/Playwright → **Browserr**（Servo 引擎，项目内 `browserr_bin/`，官方 release + sha256 校验安装）
2. **API**：只启用**本地 zen-gate**（`http://127.0.0.1:8787/v1`），其余远程源全部关闭；图片/视频无本地源时走明确标注的模拟产物

> 本版以 GitHub Release 形式发布（tag 指向独立提交），**main 分支保持原始版本不变**，随时可回滚。

## 快速开始

```bash
bash scripts/setup.sh                       # venv + 依赖 + 装 Browserr + 自检
# 先启动本机 zen-gate（见 docs/DEPLOY.md 第 4 节），再注入它的 mainKey：
export ZEN_GATE_API_KEY=<zen-gate mainKey>
.venv/bin/python main.py                    # 面板 http://127.0.0.1:8765
.venv/bin/python main.py --setup-check      # 环境自检（期望 9/9）
.venv/bin/python main.py --dry-run-test     # 全模拟冒烟
```

Linux 无头运行需 EGL：`sudo apt install libegl1 libgl1 libgbm1 libgl1-mesa-dri`。

## 文档导航（按阅读顺序）

| 文档 | 内容 |
|---|---|
| docs/DEPLOY.md | 手把手部署手册：装引擎、起 zen-gate、注入 key、三级冒烟、故障表 |
| docs/DESIGN.md | 项目设计：架构分层、任务生命周期、子进程隔离、数据与安全边界 |
| docs/DECISION_ENGINE.md | 决策引擎详解：计划生成、调度、选源熔断、复盘自迭代 |
| docs/MODULES.md | 模块地图：每个模块的职责/接口/依赖/禁区，与源码【核心模块】横幅对应 |
| docs/AI_GUIDE.md | 给接手 AI 的维护指南：不变量、改动配方、验证清单、发布约定 |
| docs/BROWSERR_ZENGATE.md | 本版迁移说明与 2026-11-20 全量实测结果 |
| docs/README.md 等其余 docs | 原版文档（系统设计/模块规格/安全/工作流/排障/API 清单） |
