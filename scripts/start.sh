#!/usr/bin/env bash
# 后台启动 charvar Web 管理端（默认 http://127.0.0.1:8710）
set -euo pipefail
cd "$(dirname "$0")/.."

export CHARVAR_WEB_PORT="${CHARVAR_WEB_PORT:-8710}"
LOG="/tmp/charvar-web-${CHARVAR_WEB_PORT}.log"

if lsof -a -i ":${CHARVAR_WEB_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 ${CHARVAR_WEB_PORT} 已被占用" >&2
  exit 1
fi

nohup python3 -m web.app >"$LOG" 2>&1 &
for _ in $(seq 1 20); do
  if curl -s --noproxy '*' "http://127.0.0.1:${CHARVAR_WEB_PORT}/api/version" >/dev/null 2>&1; then
    echo "charvar 已启动：http://127.0.0.1:${CHARVAR_WEB_PORT}（日志：${LOG}）"
    exit 0
  fi
  sleep 0.3
done
echo "启动失败，日志：${LOG}" >&2
tail -n 20 "$LOG" >&2
exit 1
