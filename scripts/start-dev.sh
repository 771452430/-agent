#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$BACKEND_DIR/data"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"

BACKEND_LOG="$LOG_DIR/uvicorn-dev.log"
FRONTEND_LOG="$LOG_DIR/next-dev.log"
BACKEND_PID_FILE="$LOG_DIR/uvicorn-dev.pid"
FRONTEND_PID_FILE="$LOG_DIR/next-dev.pid"

mkdir -p "$LOG_DIR"

kill_listener() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill $pids 2>/dev/null || true
    sleep 1
  fi
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local attempts="${3:-30}"
  local sleep_seconds="${4:-1}"

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name 已启动：$url"
      return 0
    fi
    sleep "$sleep_seconds"
  done

  echo "$name 启动超时，请查看日志："
  echo "  $BACKEND_LOG"
  echo "  $FRONTEND_LOG"
  return 1
}

kill_listener "$BACKEND_PORT"
kill_listener "$FRONTEND_PORT"

echo "启动后端..."
nohup env PYTHONPATH="$BACKEND_DIR" \
  "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app \
  --app-dir "$BACKEND_DIR" \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT" \
  >"$BACKEND_LOG" 2>&1 </dev/null &
echo $! >"$BACKEND_PID_FILE"

echo "启动前端..."
nohup bash -lc "
  cd "$FRONTEND_DIR"
  npm run dev -- --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT"
" >"$FRONTEND_LOG" 2>&1 </dev/null &
echo $! >"$FRONTEND_PID_FILE"

wait_for_url "http://$BACKEND_HOST:$BACKEND_PORT/health" "后端"
wait_for_url "http://$FRONTEND_HOST:$FRONTEND_PORT/jira-solution-search" "前端"

echo
echo "启动完成："
echo "  Jira 方案检索 Agent: http://$FRONTEND_HOST:$FRONTEND_PORT/jira-solution-search"
echo "  Jira 工单 Agent: http://$FRONTEND_HOST:$FRONTEND_PORT/jira-duplicates"
echo "  后端健康检查: http://$BACKEND_HOST:$BACKEND_PORT/health"
echo
echo "日志文件："
echo "  $BACKEND_LOG"
echo "  $FRONTEND_LOG"
