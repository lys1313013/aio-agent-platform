#!/usr/bin/env bash
# 停止后端:按端口和命令行特征清理整个进程树
# (reload 模式下子进程由看门狗跟随父进程退出,这里再做兜底)

# 按端口杀监听进程
pid=$(lsof -tnP -iTCP:8100 -sTCP:LISTEN 2>/dev/null || true)
[ -n "$pid" ] && kill $pid 2>/dev/null || true

# 杀 uv run / aio-api 包装进程
pkill -f "aio-api$" 2>/dev/null || true
pkill -f "uv run aio-api" 2>/dev/null || true

sleep 1
if pgrep -f "aio-api" >/dev/null 2>&1; then
  pkill -9 -f "aio-api" 2>/dev/null || true
fi
echo "后端已停止"
