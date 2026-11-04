"""Push comments into the stream bridge.

    python feed.py "你好呀"                 # one comment
    python feed.py --user 小明 "你好呀"      # with a nickname
    python feed.py --stdin                  # read lines from stdin, exit on EOF

For a live room, point any danmaku listener at POST http://127.0.0.1:6220/comment
with {"user": ..., "text": ...}. This repo does not ship a platform scraper.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

# 抖音抓弹幕的控制台格式：【聊天msg】昵称：内容
# 抓取库实际格式：【聊天msg】[123456]昵称: 内容
DOUYIN_RE = re.compile(
    r"^[【\[]?\s*(?:聊天|评论|弹幕|chat)[^】\]]*[】\]]\s*(?:\[[^\]]*\])?\s*([^：:]{1,24})[：:]\s*(.+)$",
    re.IGNORECASE,
)
# 【进场msg】/【礼物msg】/【点赞msg】 这些不进对话
NOISE_RE = re.compile(r"^[【\[]?\s*(?:进场|礼物|点赞|关注|分享|在线人数|统计)[^】\]]*[】\]]")


def post(base: str, user: str, text: str) -> None:
    body = json.dumps({"user": user, "text": text}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/comment", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print(res.read().decode())
    except Exception as error:  # noqa: BLE001
        print("failed:", error, file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*", help="comment text")
    ap.add_argument("--user", default="观众")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--bridge", default="http://127.0.0.1:6220")
    args = ap.parse_args()

    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if NOISE_RE.match(line):
                continue
            match = DOUYIN_RE.match(line)
            if match:
                post(args.bridge, match.group(1).strip(), match.group(2).strip())
            else:
                post(args.bridge, args.user, line)
        return

    text = " ".join(args.text).strip()
    if not text:
        ap.error("give a comment or use --stdin")
    post(args.bridge, args.user, text)


if __name__ == "__main__":
    main()
