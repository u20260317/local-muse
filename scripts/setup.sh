#!/usr/bin/env bash
# Local-Muse 首次初始化（Linux / macOS）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "============================================"
echo " Local-Muse 首次初始化（不使用 Docker / 容器）"
echo " 只下载项目内 Browserr（Servo）引擎，不触碰系统浏览器"
echo " API 只用本机 zen-gate（http://127.0.0.1:8787/v1），远程源默认关闭"
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

echo "[3/4] 下载 Browserr 官方 release 到项目内 browserr_bin/ ..."
"$PY" scripts/install_browserr.py

echo "[4/4] 环境自检 ..."
"$PY" main.py --setup-check

echo
echo "[完成] 初始化结束。下一步：bash scripts/start.sh"
echo "        本地 API：先启动 zen-gate，再把它的 mainKey 填入 config.json"
echo "        的 zen_gate_local.api_key，或设置环境变量 ZEN_GATE_API_KEY。"
