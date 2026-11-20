#!/usr/bin/env bash
# 仅下载 Playwright 内置独立 Chromium，绝不接管系统 Chrome / Edge / Firefox
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[错误] 未找到 .venv，请先执行：bash scripts/setup.sh"
  exit 1
fi

echo "下载 Playwright 内置独立 Chromium 到项目内 browser_bin/ ..."
# 相对项目根，由脚本位置动态定位；各平台自行解析，不写死绝对路径
export PLAYWRIGHT_BROWSERS_PATH="browser_bin"
"$PY" -m playwright install chromium
echo "[完成] 独立 Chromium 就绪（browser_bin/），系统浏览器未被触碰。"
