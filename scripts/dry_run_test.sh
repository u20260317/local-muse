#!/usr/bin/env bash
# Local-Muse 并行自检：不需要任何 API Key，产物明确标注「模拟」
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[错误] 未找到 .venv，请先执行：bash scripts/setup.sh"
  exit 1
fi

echo "提交 3 条不同优先级的并行自检任务（不访问公网、不启动面板）..."
exec "$PY" main.py --dry-run-test --dry-run-count 3 "$@"
