#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/backend"
RELOAD=true uv run aio-api
