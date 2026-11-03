#!/bin/bash
set -e
cd "$(dirname "$0")/.."
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
if [ -f tts/tts.pid ] && kill -0 "$(cat tts/tts.pid)" 2>/dev/null; then
  echo "tts already running: $(cat tts/tts.pid)"
  exit 0
fi
nohup tts/.venv/bin/python tts/server.py > tts/tts.log 2>&1 &
echo $! > tts/tts.pid
echo "tts started: $(cat tts/tts.pid)  http://127.0.0.1:6211/health"
