#!/usr/bin/env bash
# Local-Muse 首次初始化（Linux / macOS）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "============================================"
echo " Local-Muse 首次初始化（不使用 Docker / 容器）"
echo " 只下载 Playwright 内置独立 Chromium，不触碰系统浏览器"
echo "============================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.10+"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi
PY=".venv/bin/python"

echo "[2/4] 安装 Python 依赖 ..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt

echo "[3/4] 下载 Playwright 内置独立 Chromium 到项目内 browser_bin/ ..."
# 相对项目根，由脚本位置动态定位；各平台自行解析，不写死绝对路径
export PLAYWRIGHT_BROWSERS_PATH="browser_bin"
"$PY" -m playwright install chromium

echo "[4/4] 环境自检 ..."
"$PY" main.py --setup-check

echo
echo "[完成] 初始化结束。下一步：bash scripts/start.sh"
echo "        API 密钥请在 config.json 或 Web 面板中填写。"
