#!/usr/bin/env bash
# 停止 charvar Web 管理端（只杀本仓库启动的监听进程）
cd "$(dirname "$0")/.."

PORT="${1:-${CHARVAR_WEB_PORT:-8710}}"
PIDS=$(pgrep -f "python3 -m web.app" | while read -r pid; do
  if lsof -a -i ":${PORT}" -p "$pid" >/dev/null 2>&1; then echo "$pid"; fi
done)
if [ -z "$PIDS" ]; then
  echo "端口 ${PORT} 上没有 charvar 进程"
  exit 0
fi
echo "$PIDS" | xargs kill
echo "已停止（端口 ${PORT}）"
