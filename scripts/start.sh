#!/usr/bin/env bash
# 后台启动 charvar Web 管理端（默认 http://127.0.0.1:8710）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
export CHARVAR_WEB_PORT="${CHARVAR_WEB_PORT:-8710}"
LOG="/tmp/charvar-web-${CHARVAR_WEB_PORT}.log"
SCREEN_NAME="charvar-${CHARVAR_WEB_PORT}"
PID_FILE="${ROOT}/.charvar-web-${CHARVAR_WEB_PORT}.pid"

# 顺带装全局 CLI 与 charvar-skill（幂等）。失败不阻断 web 启动 ——
# CLI 是附属能力，不能因为它把服务拖垮。
bash "${ROOT}/scripts/install_cli.sh" || echo "警告：CLI 安装失败，继续启动 web" >&2

if lsof -a -nP -iTCP:"${CHARVAR_WEB_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 ${CHARVAR_WEB_PORT} 已被占用" >&2
  exit 1
fi

# detached screen：让服务在调用方 shell / 终端包装进程退出后仍然稳定运行
screen -dmS "$SCREEN_NAME" /bin/zsh -lc "exec python3 -m web.app >'$LOG' 2>&1"

for _ in $(seq 1 20); do
  if curl -s --noproxy '*' "http://127.0.0.1:${CHARVAR_WEB_PORT}/api/version" >/dev/null 2>&1; then
    listener_pid="$(lsof -t -a -nP -iTCP:"${CHARVAR_WEB_PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
    if [ -n "$listener_pid" ]; then
      printf '%s\n' "$listener_pid" > "$PID_FILE"
    fi
    echo "charvar 已启动：http://127.0.0.1:${CHARVAR_WEB_PORT}（screen：${SCREEN_NAME}）"
    exit 0
  fi
  sleep 0.3
done

echo "启动失败，日志：${LOG}" >&2
tail -n 20 "$LOG" 2>/dev/null >&2 || true
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
