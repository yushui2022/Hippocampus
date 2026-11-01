"""Read-only observatory.

    python -m hippocampus.observatory --db memory.db --port 8765

A tiny stdlib HTTP server that exposes engrams, facts and counts. It opens the
database with ``mode=ro`` + ``query_only`` on every request and never calls the
kernel's write path, so browsing cannot strengthen or change a memory.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .ops.inspect import list_engrams, list_facts, snapshot


def _page(db_path: str, scope: str) -> str:
    data = snapshot(db_path, scope or None, limit=50)
    engrams = list_engrams(db_path, scope or None, limit=100)
    facts = list_facts(db_path, scope or None, limit=100)
    counts = data.get("counts", {})

    def row_engram(item: Dict[str, Any]) -> str:
        return (
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td></tr>".format(
                html.escape(str(item.get("level"))),
                html.escape(str(item.get("fidelity"))),
                html.escape(str(item.get("importance"))),
                html.escape(str(item.get("kind", ""))),
                html.escape(str(item.get("scope", ""))),
                html.escape(str(item.get("text", ""))),
            )
        )

    def row_fact(item: Dict[str, Any]) -> str:
        return (
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(item.get("kind", ""))),
                html.escape(str(item.get("status", ""))),
                html.escape(str(item.get("subject", ""))),
                html.escape(str(item.get("predicate", ""))),
                html.escape(str(item.get("obj", ""))),
            )
        )

    return """<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>Hippocampus · 只读观测台</title>
<style>
body{{font-family:-apple-system,system-ui,"PingFang SC",sans-serif;margin:2rem;color:#1c2430;background:#f7f8fa}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem}}
.badge{{display:inline-block;background:#e6f4ea;color:#1e6b3a;border-radius:6px;padding:2px 8px;font-size:.8rem}}
table{{border-collapse:collapse;width:100%;background:#fff;margin-top:.6rem;font-size:.86rem}}
th,td{{border-bottom:1px solid #e6e8ec;padding:.45rem .6rem;text-align:left;vertical-align:top}}
th{{background:#f0f2f5;font-weight:600}}
code{{background:#eef0f3;padding:1px 4px;border-radius:4px}}
.muted{{color:#667085;font-size:.85rem}}
</style></head>
<body>
<h1>Hippocampus <span class="badge">只读</span></h1>
<p class="muted">schema {schema} · scope <code>{scope}</code> · engrams {engrams} · facts {facts} · vectors {vectors}</p>
<p class="muted">这个页面只读。浏览、刷新、调用下面的 API 都不会改变记忆。</p>
<p><code>/api/snapshot</code> · <code>/api/engrams</code> · <code>/api/facts</code> · <code>/health</code></p>

<h2>记忆（按创建时间倒序）</h2>
<table><thead><tr><th>level</th><th>fidelity</th><th>importance</th><th>kind</th><th>scope</th><th>text</th></tr></thead>
<tbody>{rows}</tbody></table>

<h2>事实</h2>
<table><thead><tr><th>kind</th><th>status</th><th>subject</th><th>predicate</th><th>object</th></tr></thead>
<tbody>{fact_rows}</tbody></table>
</body></html>""".format(
        schema=html.escape(str(data.get("schema_version", ""))),
        scope=html.escape(scope or "*"),
        engrams=counts.get("engrams", 0),
        facts=counts.get("facts", 0),
        vectors=counts.get("engram_vectors", 0),
        rows="".join(row_engram(item) for item in engrams),
        fact_rows="".join(row_fact(item) for item in facts),
    )


def make_handler(db_path: str, scope: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            target = query.get("scope", [scope])[0]
            try:
                if parsed.path == "/health":
                    self._json({"ok": True})
                elif parsed.path == "/api/snapshot":
                    limit = int(query.get("limit", ["20"])[0])
                    self._json(snapshot(db_path, target or None, limit=limit))
                elif parsed.path == "/api/engrams":
                    limit = int(query.get("limit", ["100"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    self._json(list_engrams(db_path, target or None, limit, offset))
                elif parsed.path == "/api/facts":
                    limit = int(query.get("limit", ["100"])[0])
                    self._json(list_facts(db_path, target or None, limit))
                elif parsed.path in ("/", "/index.html"):
                    body = _page(db_path, target).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json({"error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001 - surface the message
                self._json({"error": str(exc)}, status=500)

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    return Handler


def create_server(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    scope: str = "",
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(db_path, scope))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hippocampus read-only observatory")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scope", default="")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "warning: binding {} exposes memory contents; prefer 127.0.0.1".format(
                args.host
            ),
            file=sys.stderr,
        )
    server = create_server(args.db, args.host, args.port, args.scope)
    print(
        "observatory: http://{}:{}/  (read-only, Ctrl+C to stop)".format(
            args.host, server.server_address[1]
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
