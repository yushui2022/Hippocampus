"""向 B 站申请第三方推流地址（走直播姬的 pc_link 通道）。

背景：B 站从 2025-05-26 起要求第三方开播账号粉丝数 ≥ 5000，低于这个数
网页上不会显示推流地址。官方直播姬客户端走的是 `platform=pc_link`，
这个脚本用同一个接口替 OBS 申请地址——和直播姬本身做的事一样，只是没有
Windows 客户端。是否使用请自行判断平台风险。

用法：
    python go_live.py --cookie cookie.txt
    python go_live.py --cookie cookie.txt --json
    python go_live.py --cookie cookie.txt --stop

cookie.txt 至少包含两项（浏览器 F12 → Application → Cookies → bilibili.com）：
    SESSDATA=...; bili_jct=...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.live.bilibili.com"
ROOM_ID = "14308771"
AREA_ID = "372"                      # 知识 → 自习室
APP_KEY = "aae92bc66f3edfab"
APP_SEC = "af125a0d5279fd576c1b4418a3e8276d"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


def load_cookie(path: str) -> str:
    text = Path(path).read_text("utf-8").strip()
    keys = {kv.split("=", 1)[0].strip() for kv in text.split(";") if "=" in kv}
    missing = {"SESSDATA", "bili_jct"} - keys
    if missing:
        raise SystemExit(f"cookie 缺少: {', '.join(sorted(missing))}")
    return text


def get_json(url: str, data: bytes | None = None, cookie: str = "") -> dict:
    headers = {
        "User-Agent": UA,
        "Origin": API,
        "Referer": "https://link.bilibili.com/p/center/index",
    }
    if cookie:
        headers["Cookie"] = cookie
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode())


def livehime_version() -> tuple[str, str]:
    params = {
        "system_version": "2",
        "ts": str(int(time.time() * 1000)),
        "appkey": APP_KEY,
    }
    query = urllib.parse.urlencode(sorted(params.items()))
    sign = hashlib.md5((query + APP_SEC).encode()).hexdigest()
    data = get_json(f"{API}/xlive/app-blink/v1/liveVersionInfo/getHomePageLiveVersion"
                    f"?{query}&sign={sign}")
    body = data.get("data") or {}
    return str(body.get("curr_version") or ""), str(body.get("build") or "")


def csrf(cookie: str) -> str:
    for part in cookie.split(";"):
        if part.strip().startswith("bili_jct="):
            return part.split("=", 1)[1].strip()
    return ""


def start(cookie: str) -> dict:
    # 和 BLiveWeb 一致：appkey + 排序后的 query 做 md5 签名，ts 用秒
    token = csrf(cookie)
    payload = {
        "access_key": "",
        "appkey": APP_KEY,
        "area_v2": AREA_ID,
        "build": "9343",
        "csrf": token,
        "csrf_token": token,
        "platform": "pc_link",
        "room_id": ROOM_ID,
        "ts": str(int(time.time())),
    }
    query = urllib.parse.urlencode(sorted(payload.items()))
    sign = hashlib.md5((query + APP_SEC).encode()).hexdigest()
    data = (query + "&sign=" + sign).encode()
    return get_json(f"{API}/room/v1/Room/startLive", data=data, cookie=cookie)


def stop(cookie: str) -> dict:
    payload = {
        "room_id": ROOM_ID,
        "platform": "pc_link",
        "csrf": csrf(cookie),
        "csrf_token": csrf(cookie),
    }
    data = urllib.parse.urlencode(payload).encode()
    return get_json(f"{API}/room/v1/Room/stopLive", data=data, cookie=cookie)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", default="cookie.txt")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()

    cookie = load_cookie(args.cookie)
    result = stop(cookie) if args.stop else start(cookie)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return

    if result.get("code") != 0:
        print("失败:", result.get("code"), result.get("message"))
        return

    data = result.get("data") or {}
    if args.stop:
        print("已结束直播")
        return
    rtmp = data.get("rtmp") or {}
    print("推流地址:", rtmp.get("addr"))
    print("串流密钥:", rtmp.get("code"))
    print("\nOBS → 设置 → 直播 → 服务：自定义，填上面两行。")


if __name__ == "__main__":
    main()
