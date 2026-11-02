from __future__ import annotations

import html
import ipaddress
import re
import socket
from typing import Awaitable, Callable, Dict, List
from urllib.parse import quote_plus, urlparse

from .config import Config


def plain(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class WebTools:
    def __init__(
        self,
        cfg: Config,
        session_getter: Callable[[], Awaitable[object]],
    ):
        self.cfg = cfg
        self._session_getter = session_getter

    def schema(self) -> List[dict]:
        if not self.cfg.web_tools:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索实时网页信息。需要新闻、天气、链接、最新事实时使用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索词"},
                            "limit": {
                                "type": "integer",
                                "description": "最多几条，默认 5",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "打开一个 http/https 链接并读出正文。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "完整链接"}
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    async def run(self, name: str, args: Dict[str, object]) -> str:
        try:
            if name == "web_search":
                return await self.search(
                    str(args.get("query") or ""), int(args.get("limit") or 5)
                )
            if name == "web_fetch":
                return await self.fetch(str(args.get("url") or ""))
            return "未知工具：{}".format(name)
        except Exception as exc:  # noqa: BLE001
            return "工具执行失败：{}".format(exc)

    @staticmethod
    def safe_url(url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in ("localhost", "host.docker.internal"):
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True

    async def _headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }

    async def search(self, query: str, limit: int = 5) -> str:
        if not query.strip():
            return "搜索词为空。"
        limit = max(1, min(8, int(limit)))
        session = await self._session_getter()
        headers = await self._headers()
        url = "https://www.bing.com/search?q={}&setlang=zh-Hans&cc=cn".format(
            quote_plus(query)
        )
        data = ""
        try:
            async with session.get(url, headers=headers, timeout=20) as resp:
                if resp.status < 400:
                    data = (await resp.read())[:400000].decode("utf-8", "ignore")
        except Exception:
            pass

        results = []
        for block in re.findall(r'<li class="b_algo".*?</li>', data, re.S | re.I):
            m = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.S | re.I,
            )
            if not m:
                continue
            href = html.unescape(m.group(1))
            title = plain(m.group(2))
            sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
            snippet = plain(sm.group(1)) if sm else ""
            if href and title:
                results.append((title, href, snippet))
            if len(results) >= limit:
                break

        if not results:
            try:
                ddg = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
                async with session.get(ddg, headers=headers, timeout=20) as resp:
                    data = (await resp.read())[:300000].decode("utf-8", "ignore")
                links = re.findall(
                    r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    data,
                    re.S | re.I,
                )
                snippets = re.findall(
                    r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
                    data,
                    re.S | re.I,
                )
                for i, (href, title) in enumerate(links[:limit]):
                    snippet = plain(snippets[i]) if i < len(snippets) else ""
                    results.append((plain(title), html.unescape(href), snippet))
            except Exception:
                pass

        if not results:
            return "没有找到搜索结果。"
        return "\n\n".join(
            "{}. {}\n{}\n{}".format(i, title, href, snippet[:200])
            for i, (title, href, snippet) in enumerate(results, 1)
        )

    async def fetch(self, url: str) -> str:
        if not self.safe_url(url):
            return "这个地址不允许抓取。"
        session = await self._session_getter()
        headers = await self._headers()
        async with session.get(url, headers=headers, timeout=20) as resp:
            if resp.status >= 400:
                return "页面打开失败：HTTP {}".format(resp.status)
            data = await resp.read()
        text = plain(data[:800000].decode("utf-8", "ignore"))
        return text[:4000] if text else "页面没有可读正文。"
