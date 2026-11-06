"""Bilibili 直播弹幕 → 大肥鱼弹幕桥。

只依赖 blivedm（拉流/协议）+ aiohttp。B 站现在给 danmuInfo 加了 WBI 签名、
并且认证包必须带登录账号的 uid，官方旧版 blivedm 直连会拿到 -352 / 被立刻断开，
所以这里补了两块：WBI 签名、uid 探测。

    python danmaku.py 14308771
    BILI_COOKIE="SESSDATA=..." python danmaku.py 14308771

Cookie 三种来源，优先级从高到低：
    1. 环境变量 BILI_COOKIE
    2. 同目录 cookie.txt（内容形如 SESSDATA=xxx）
    3. 无（只能连上极少数不需要风控的接口，通常拿不到弹幕）

只转发聊天和醒目留言，礼物/进场/榜单全部丢弃。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import aiohttp
from blivedm import client as bclient
from blivedm.handlers import BaseHandler

BRIDGE = os.environ.get("STREAM_BRIDGE", "http://127.0.0.1:6220/comment")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

# B 站 WBI 混淆表
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def load_cookie() -> str:
    env = os.environ.get("BILI_COOKIE", "").strip()
    if env:
        return env
    path = Path(__file__).with_name("cookie.txt")
    return path.read_text().strip() if path.exists() else ""


async def wbi_keys(session: aiohttp.ClientSession) -> tuple[str, str]:
    async with session.get("https://api.bilibili.com/x/web-interface/nav", timeout=15) as res:
        data = await res.json()
    wbi = (data.get("data") or {}).get("wbi_img") or {}
    img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    return img, sub


def mixin_key(img: str, sub: str) -> str:
    raw = img + sub
    return "".join(raw[i] for i in MIXIN_TAB)[:32]


def sign(params: dict, mixin: str) -> str:
    params = dict(params)
    params["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(params.items()))


class SignedClient(bclient.BLiveClient):
    """补上 WBI 签名，并用可用的 get_info 接口取房间信息。"""

    async def _init_room_id_and_owner(self):  # noqa: N802
        try:
            async with self._session.get(
                "https://api.live.bilibili.com/room/v1/Room/get_info",
                params={"room_id": self._tmp_room_id}, timeout=15, ssl=self._ssl,
            ) as res:
                data = await res.json()
            if data.get("code") != 0:
                print("room init failed:", data.get("message"), file=sys.stderr)
                return False
            info = data["data"]
            self._room_id = info["room_id"]
            self._room_short_id = info.get("short_id") or 0
            self._room_owner_uid = info.get("uid") or 0
            return True
        except Exception as error:  # noqa: BLE001
            print("room init error:", error, file=sys.stderr)
            return False

    async def _init_host_server(self):  # noqa: N802
        try:
            img, sub = await wbi_keys(self._session)
            query = sign({"id": self._room_id, "type": 0}, mixin_key(img, sub))
            async with self._session.get(
                "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?" + query,
                timeout=15, ssl=self._ssl,
            ) as res:
                data = await res.json()
            if data.get("code") != 0:
                print("danmu info failed:", data.get("message"), file=sys.stderr)
                return False
            return self._parse_danmaku_server_conf(data["data"])
        except Exception as error:  # noqa: BLE001
            print("danmu info error:", error, file=sys.stderr)
            return False


class ChatHandler(BaseHandler):
    def _on_danmaku(self, client, message):  # noqa: N802
        uid = getattr(getattr(message, "user_info", None), "uid", "")
        name = getattr(getattr(message, "user_info", None), "uname", "") or uid or "观众"
        text = (message.content or "").strip()
        if text:
            print(f"{name}: {text}", flush=True)
            post(str(name), text)

    def _on_super_chat(self, client, message):  # noqa: N802
        name = getattr(getattr(message, "user_info", None), "uname", None) or "观众"
        text = (message.message or "").strip()
        if text:
            print(f"[SC] {name}: {text}", flush=True)
            post(str(name), text)


def post(user: str, text: str) -> None:
    body = json.dumps({"user": user, "text": text}).encode()
    req = urllib.request.Request(BRIDGE, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            res.read()
    except Exception as error:  # noqa: BLE001
        print("bridge post failed:", error, file=sys.stderr)


async def run(room_id: int) -> None:
    cookie = load_cookie()
    session = aiohttp.ClientSession(headers={
        "User-Agent": UA,
        "Referer": "https://live.bilibili.com/",
        **({"Cookie": cookie} if cookie else {}),
    })
    if not cookie:
        print("warning: 没有 cookie，弹幕接口大概率返回 -352", file=sys.stderr)

    # 认证包必须带登录账号的 uid，否则服务端会立刻断开
    uid = 0
    try:
        async with session.get("https://api.bilibili.com/x/web-interface/nav", timeout=15) as res:
            nav = await res.json()
        uid = int((nav.get("data") or {}).get("mid") or 0)
        if not (nav.get("data") or {}).get("isLogin"):
            print("warning: cookie 未登录", file=sys.stderr)
    except Exception as error:  # noqa: BLE001
        print("nav failed:", error, file=sys.stderr)

    print(f"connecting room {room_id} -> {BRIDGE}", flush=True)
    blive = SignedClient(room_id, uid=uid, session=session)
    blive.add_handler(ChatHandler())
    await blive.init_room()
    blive.start()
    try:
        await blive.join()
    finally:
        await blive.stop_and_close()
        await session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python danmaku.py <room_id>", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(run(int(sys.argv[1])))
