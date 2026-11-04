#!/usr/bin/env bash
# Stop the stream stack started by start.sh (the brain and TTS are left alone).
set -uo pipefail

for port in 6220 6210; do
  pids="$(lsof -ti tcp:$port 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -9 2>/dev/null || true
    echo "stopped :$port"
  else
    echo ":$port not running"
  fi
done
