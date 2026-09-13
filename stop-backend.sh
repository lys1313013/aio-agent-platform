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

# reload 子进程的命令行只有 spawn_main,不会匹配 aio-api。
# SIGTERM 未完成退出时,按实际监听端口兜底清理,再确认端口释放。
for attempt in 1 2 3 4 5; do
  remaining=$(lsof -tnP -iTCP:8100 -sTCP:LISTEN 2>/dev/null || true)
  if [ -z "$remaining" ]; then
    echo "后端已停止"
    exit 0
  fi
  kill -9 $remaining 2>/dev/null || true
  sleep 1
done
if lsof -tnP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "停止失败:8100 端口仍被占用,请检查残留进程" >&2
  exit 1
fi
echo "后端已停止"
