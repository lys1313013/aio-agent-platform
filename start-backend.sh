#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/backend"
mkdir -p logs

# nohup 脱离终端后台运行、日志落盘:终端关闭不会再拖死服务
# (历史上服务子进程被孤儿化后仍持有 8100 端口,往死掉的 pty 写日志阻塞,
#  表现为"能连上但无响应"的假死)
nohup uv run aio-api >>logs/backend.log 2>&1 &
echo "后端已启动 pid=$!"
echo "正在跟踪日志(Ctrl+C 只退出日志,服务继续运行;停止服务用 ./stop-backend.sh)"
echo "---"
sleep 1
tail -f logs/backend.log
