#!/usr/bin/env bash
# Local-Muse 一键启动（Linux / macOS）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[错误] 未找到 .venv，请先执行：bash scripts/setup.sh"
  exit 1
fi
if ! "$PY" -c "import flask, requests, schedule" >/dev/null 2>&1; then
  echo "[错误] 依赖缺失，请先执行：bash scripts/setup.sh"
  exit 1
fi

echo "Local-Muse 启动中 ... 面板地址：http://127.0.0.1:8765（默认，端口见 config.json）"
echo "仅监听回环地址；按 Ctrl+C 停止，停止时会自动清理全部子进程。"
exec "$PY" main.py "$@"
