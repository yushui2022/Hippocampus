#!/bin/bash
cd "$(dirname "$0")"
echo "http://127.0.0.1:6210/web/"
python3 -m http.server 6210
