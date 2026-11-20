#!/usr/bin/env bash
# 重置项目内独立 Browserr 会话（带二次确认，绝不触碰系统浏览器）
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "将删除 browser_profile/session_*（仅项目内独立 Browserr 会话）"
echo "本机日常 Chrome / Edge / Firefox 数据不会被读取或删除。"
read -r -p "输入 Y 继续：" ans
if [ "$ans" != "Y" ] && [ "$ans" != "y" ]; then
  echo "已取消。"
  exit 0
fi

mkdir -p browser_profile
find browser_profile -maxdepth 1 -type d -name 'session_*' -exec rm -rf {} +
echo "[完成] 独立浏览器环境已重置。"
