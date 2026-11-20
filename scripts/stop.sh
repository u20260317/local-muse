#!/usr/bin/env bash
# 仅停止本项目启动的 Local-Muse 服务（校验 cmdline 含 main.py，避免误杀）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PID_FILE="logs/local_muse.pid"
if [ ! -f "$PID_FILE" ]; then
  echo "[提示] 未找到 PID 文件，服务可能未运行。"
  exit 0
fi
LM_PID=$(cat "$PID_FILE")
if [ -z "$LM_PID" ]; then
  rm -f "$PID_FILE"
  echo "[提示] PID 文件为空，已清理。"
  exit 0
fi

if [ -r "/proc/$LM_PID/cmdline" ]; then
  CMDLINE=$(tr '\0' ' ' < "/proc/$LM_PID/cmdline")
  case "$CMDLINE" in
    *main.py*) ;;
    *) echo "[错误] PID $LM_PID 不属于 Local-Muse，拒绝终止。"; exit 1 ;;
  esac
  echo "正在停止 Local-Muse (PID $LM_PID) ..."
  kill -TERM "$LM_PID"
  for _ in {1..10}; do
    [ -r "/proc/$LM_PID/cmdline" ] || break
    sleep 0.5
  done
  if [ -r "/proc/$LM_PID/cmdline" ]; then
    echo "进程未响应 TERM，发送 KILL ..."
    kill -KILL "$LM_PID"
  fi
else
  echo "[提示] 进程已退出，清理 PID 文件。"
fi
rm -f "$PID_FILE"
echo "[完成] 已停止。"
