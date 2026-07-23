#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/frontend"

if command -v pnpm &>/dev/null; then
  pnpm dev
else
  npm run dev
fi
