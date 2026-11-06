"""Force the OBS browser source to reload the stage page.

OBS's embedded Chromium caches the HTML, so after a frontend change the preview
can keep showing an old build. This re-sets the source URL with a cache-busting
timestamp.

    python refresh_obs.py            # 刷新到最新
    python refresh_obs.py --monitor  # 同时打开音频监听（抖音虚拟摄像头那条链路要）
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import websocket

CONFIG = Path.home() / "Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
SOURCE = "大肥鱼画面"
PAGE = "http://127.0.0.1:6210/stream/stream.html?audio=1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())
    ws = websocket.create_connection("ws://127.0.0.1:4455", timeout=20)
    hello = json.loads(ws.recv())["d"]["authentication"]
    pw = cfg["server_password"]
    secret = base64.b64encode(hashlib.sha256((pw + hello["salt"]).encode()).digest()).decode()
    resp = base64.b64encode(hashlib.sha256((secret + hello["challenge"]).encode()).digest()).decode()
    ws.send(json.dumps({"op": 1, "d": {"rpcVersion": 1, "authentication": resp}}))
    json.loads(ws.recv())

    req_id = 0

    def req(kind: str, data: dict) -> dict:
        nonlocal req_id
        req_id += 1
        rid = f"r{req_id}"
        ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                return msg["d"]

    url = f"{PAGE}&v={int(time.time())}"
    print("url     ", req("SetInputSettings", {
        "inputName": SOURCE, "overlay": True, "inputSettings": {"url": url},
    })["requestStatus"])

    mode = "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT" if args.monitor else "OBS_MONITORING_TYPE_NONE"
    print("monitor ", req("SetInputAudioMonitorType", {
        "inputName": SOURCE, "monitorType": mode,
    })["requestStatus"])
    ws.close()


if __name__ == "__main__":
    main()
