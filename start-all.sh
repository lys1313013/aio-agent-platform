#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# 安装依赖
echo ">> 同步依赖..."
(cd "$ROOT/backend" && uv sync --all-extras)

# 启动后端（后台运行）
(cd "$ROOT/backend" && uv run aio-api) &
BACKEND_PID=$!

# 启动前端（后台运行）
(cd "$ROOT/frontend" && (pnpm dev 2>/dev/null || npm run dev)) &
FRONTEND_PID=$!

# 捕获退出信号，同时关闭两个进程
cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

wait
