#!/bin/bash
cd "$(dirname "$0")/.."
if [ -f tts/tts.pid ]; then
  kill "$(cat tts/tts.pid)" 2>/dev/null || true
  rm -f tts/tts.pid
fi
pkill -f "tts/server.py" 2>/dev/null || true
echo "tts stopped"
