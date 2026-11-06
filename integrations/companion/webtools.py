"""Minimal web tools for the livestream shell: search + fetch a page.

No API keys. Bing HTML first, DuckDuckGo lite as a fallback, then a plain
text extraction of the target page.
"""

from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import quote_plus, urlparse

import aiohttp

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}


def plain(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def site_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


async def search(session: aiohttp.ClientSession, query: str, limit: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(8, int(limit)))
    out: list[dict] = []

    url = "https://www.bing.com/search?q={}&setlang=zh-Hans&cc=cn".format(quote_plus(query))
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as resp:
            data = (await resp.read())[:400000].decode("utf-8", "ignore") if resp.status < 400 else ""
    except Exception:
        data = ""

    for block in re.findall(r'<li class="b_algo".*?</li>', data, re.S | re.I):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S | re.I)
        if not m:
            continue
        href = html.unescape(m.group(1))
        title = plain(m.group(2))
        sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S | re.I)
        snippet = plain(sm.group(1)) if sm else ""
        if href and title:
            out.append({"title": title, "url": href, "snippet": snippet[:220]})
        if len(out) >= limit:
            break

    if not out:
        try:
            ddg = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
            async with session.get(ddg, headers=HEADERS, timeout=15) as resp:
                data = (await resp.read())[:300000].decode("utf-8", "ignore")
            links = re.findall(r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', data, re.S | re.I)
            snippets = re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', data, re.S | re.I)
            for i, (href, title) in enumerate(links[:limit]):
                out.append({
                    "title": plain(title),
                    "url": html.unescape(href),
                    "snippet": plain(snippets[i])[:220] if i < len(snippets) else "",
                })
        except Exception:
            pass

    return out


async def fetch_page(session: aiohttp.ClientSession, url: str, limit: int = 1800) -> dict:
    """Return {title, url, site, text} for a page, or an empty dict."""
    if not re.match(r"^https?://", url or ""):
        return {}
    try:
        async with session.get(url, headers=HEADERS, timeout=20) as resp:
            if resp.status >= 400:
                return {}
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return {}
            raw = (await resp.read())[:600000].decode("utf-8", "ignore")
    except Exception:
        return {}

    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    title = plain(title_m.group(1)) if title_m else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>", "\n", body)
    text = plain(body)
    if not text:
        return {}
    return {
        "title": title,
        "url": url,
        "site": site_of(url),
        "text": text[:limit],
    }


def brief(results: list[dict], pages: Optional[list[dict]] = None) -> str:
    """Compact material to hand back to the model."""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append("{}. {}\n{}\n{}".format(i, r["title"], r["url"], r["snippet"]))
    for p in pages or []:
        parts.append("【页面正文】{} ({})\n{}".format(p.get("title") or "", p.get("site") or "", p.get("text", "")))
    return "\n\n".join(parts)[:4000]
