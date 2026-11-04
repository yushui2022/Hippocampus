#!/usr/bin/env bash
# Start the stream stack: static page (6210) + danmaku bridge (6220).
# The brain (6200) and TTS (6211) are separate services that should already be up.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STREAM="$ROOT/stream"
LOG="/tmp/taozi-stream"

mkdir -p "$LOG"

if ! lsof -nP -iTCP:6210 -sTCP:LISTEN >/dev/null 2>&1; then
  (cd "$ROOT" && nohup python3 -m http.server 6210 >"$LOG/web.log" 2>&1 &)
  echo "web      :6210 started"
else
  echo "web      :6210 already running"
fi

if ! lsof -nP -iTCP:6220 -sTCP:LISTEN >/dev/null 2>&1; then
  (cd "$STREAM" && nohup ./.venv/bin/python bridge.py >"$LOG/bridge.log" 2>&1 &)
  echo "bridge   :6220 started"
else
  echo "bridge   :6220 already running"
fi

sleep 2
echo
echo "画面   http://127.0.0.1:6210/stream/stream.html"
echo "场控   http://127.0.0.1:6210/stream/control.html"
echo "日志   $LOG"
echo
echo "检查："
curl -s --max-time 3 http://127.0.0.1:6200/health | head -c 200; echo
curl -s --max-time 3 http://127.0.0.1:6220/health | head -c 200; echo
