#!/usr/bin/env bash
# 停止 charvar Web 管理端；优先 PID 文件，再按端口 + 本仓库 cwd 安全回搜。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${1:-${CHARVAR_WEB_PORT:-8710}}"
SCREEN_NAME="charvar-${PORT}"
PID_FILE="${ROOT}/.charvar-web-${PORT}.pid"

is_charvar_process() {
  local pid="$1"
  local command cwd
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
  case "$cwd" in
    "$ROOT"|"$ROOT"/*) return 0 ;;
  esac
  case "$command" in
    *" -m web.app"*|*"web.app:app"*) return 0 ;;
  esac
  return 1
}

candidate_pids=()
if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && is_charvar_process "$pid"; then
    candidate_pids+=("$pid")
  fi
  rm -f "$PID_FILE"
fi

while IFS= read -r pid; do
  [ -n "$pid" ] || continue
  kill -0 "$pid" 2>/dev/null || continue
  is_charvar_process "$pid" || continue
  duplicate=0
  for existing in "${candidate_pids[@]:-}"; do
    if [ "${existing:-}" = "$pid" ]; then duplicate=1; fi
  done
  if [ "$duplicate" -eq 0 ]; then
    candidate_pids+=("$pid")
  fi
done < <(lsof -t -a -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)

if [ "${#candidate_pids[@]}" -eq 0 ]; then
  screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
  echo "端口 ${PORT} 上没有 charvar 进程"
  exit 0
fi

for pid in "${candidate_pids[@]}"; do
  kill "$pid" 2>/dev/null || true
done

for _ in $(seq 1 20); do
  alive=0
  for pid in "${candidate_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      alive=1
    fi
  done
  [ "$alive" -eq 0 ] && break
  sleep 0.1
done

for pid in "${candidate_pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
done
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
echo "已停止（端口 ${PORT}：${candidate_pids[*]}）"
