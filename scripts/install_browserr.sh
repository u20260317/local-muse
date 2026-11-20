#!/usr/bin/env bash
# 下载 Browserr 官方 release 到项目内 browserr_bin/，绝不接管系统浏览器
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "${PY:-}" ]; then
  echo "[错误] 未找到 Python，请先执行：bash scripts/setup.sh"
  exit 1
fi

echo "下载 Browserr（Servo）引擎到项目内 browserr_bin/ ..."
"$PY" scripts/install_browserr.py
echo "[完成] Browserr 就绪（browserr_bin/），系统浏览器未被触碰。"
