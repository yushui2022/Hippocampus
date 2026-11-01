"""Read-only inspection.

Looking at memory must never change memory. This module opens SQLite with
``mode=ro`` + ``query_only`` and never touches the kernel's write path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def snapshot(path: str, scope: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    """Return a small, read-only view of a database."""
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        meta = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM meta").fetchall()
        }
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        counts: Dict[str, int] = {}
        for table in ("engrams", "engram_tokens", "engram_vectors", "facts", "recall_log"):
            if table in tables:
                counts[table] = int(
                    conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
                )

        sql = (
            "SELECT engram_id, scope, kind, level, fidelity, importance, "
            "stability, (raw_text != '') AS has_text, created_at FROM engrams"
        )
        params: List[Any] = []
        if scope:
            sql += " WHERE scope=?"
            params.append(scope)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        samples = [dict(row) for row in rows]

        fact_sql = "SELECT fact_id, kind, status, confidence, text FROM facts"
        fact_params: List[Any] = []
        if scope:
            fact_sql += " WHERE scope=?"
            fact_params.append(scope)
        fact_sql += " ORDER BY created_at DESC LIMIT ?"
        fact_params.append(limit)
        facts = [dict(row) for row in conn.execute(fact_sql, fact_params).fetchall()]

        return {
            "schema_version": meta.get("schema_version", ""),
            "meta": meta,
            "counts": counts,
            "samples": samples,
            "facts": facts,
            "read_only": True,
        }
    finally:
        conn.close()
