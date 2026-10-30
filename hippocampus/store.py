"""SQLite persistence.

v0 keeps everything in one file: engrams, a token index for retrieval, and a
per-scope state blob. No FTS5, because its default tokenizers handle CJK
badly and a bigram table is both portable and predictable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Engram
from .tokens import jaccard

SCHEMA = """
CREATE TABLE IF NOT EXISTS engrams (
    engram_id       TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'episodic',
    raw_text        TEXT NOT NULL,
    gist            TEXT DEFAULT '',
    emotion         TEXT DEFAULT '',
    level           INTEGER NOT NULL DEFAULT 0,
    importance      REAL NOT NULL DEFAULT 0.5,
    stability       REAL NOT NULL DEFAULT 1.0,
    difficulty      REAL NOT NULL DEFAULT 5.0,
    active_age_days REAL NOT NULL DEFAULT 0.0,
    recall_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    last_recall_at  TEXT,
    last_mention_at TEXT,
    refs            TEXT NOT NULL DEFAULT '[]',
    meta            TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS engram_tokens (
    engram_id TEXT NOT NULL,
    token     TEXT NOT NULL,
    PRIMARY KEY (engram_id, token)
);
CREATE TABLE IF NOT EXISTS scope_state (
    scope      TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_engrams_scope ON engrams(scope, created_at);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON engram_tokens(token);
CREATE TABLE IF NOT EXISTS recall_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    engram_id  TEXT NOT NULL,
    query      TEXT,
    at         TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes --------------------------------------------------------------
    def insert(self, engram: Engram) -> None:
        self._conn.execute(
            """INSERT INTO engrams (
                engram_id, scope, kind, raw_text, gist, emotion, level, importance,
                stability, difficulty, active_age_days, recall_count, created_at,
                last_recall_at, last_mention_at, refs, meta
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                engram.engram_id,
                engram.scope,
                engram.kind,
                engram.raw_text,
                engram.gist,
                engram.emotion,
                engram.level,
                engram.importance,
                engram.stability,
                engram.difficulty,
                engram.active_age_days,
                engram.recall_count,
                _dt(engram.created_at),
                _dt(engram.last_recall_at),
                _dt(engram.last_mention_at),
                json.dumps(engram.refs, ensure_ascii=False),
                json.dumps(engram.meta, ensure_ascii=False),
            ),
        )
        self._conn.executemany(
            "INSERT OR IGNORE INTO engram_tokens (engram_id, token) VALUES (?,?)",
            [(engram.engram_id, t) for t in engram.tokens],
        )
        self._conn.commit()

    def update(self, engram: Engram) -> None:
        self._conn.execute(
            """UPDATE engrams SET
                kind=?, raw_text=?, gist=?, emotion=?, level=?, importance=?,
                stability=?, difficulty=?, active_age_days=?, recall_count=?,
                last_recall_at=?, last_mention_at=?, refs=?, meta=?
               WHERE engram_id=?""",
            (
                engram.kind,
                engram.raw_text,
                engram.gist,
                engram.emotion,
                engram.level,
                engram.importance,
                engram.stability,
                engram.difficulty,
                engram.active_age_days,
                engram.recall_count,
                _dt(engram.last_recall_at),
                _dt(engram.last_mention_at),
                json.dumps(engram.refs, ensure_ascii=False),
                json.dumps(engram.meta, ensure_ascii=False),
                engram.engram_id,
            ),
        )
        self._conn.commit()

    def update_many(self, engrams: Iterable[Engram]) -> None:
        """Batch update in a single transaction (tick() calls this a lot)."""
        rows = [
            (
                e.kind,
                e.raw_text,
                e.gist,
                e.emotion,
                e.level,
                e.importance,
                e.stability,
                e.difficulty,
                e.active_age_days,
                e.recall_count,
                _dt(e.last_recall_at),
                _dt(e.last_mention_at),
                json.dumps(e.refs, ensure_ascii=False),
                json.dumps(e.meta, ensure_ascii=False),
                e.engram_id,
            )
            for e in engrams
        ]
        if not rows:
            return
        self._conn.executemany(
            """UPDATE engrams SET
                kind=?, raw_text=?, gist=?, emotion=?, level=?, importance=?,
                stability=?, difficulty=?, active_age_days=?, recall_count=?,
                last_recall_at=?, last_mention_at=?, refs=?, meta=?
               WHERE engram_id=?""",
            rows,
        )
        self._conn.commit()

    def add_tokens(self, engram_id: str, tokens: Iterable[str]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO engram_tokens (engram_id, token) VALUES (?,?)",
            [(engram_id, t) for t in tokens],
        )
        self._conn.commit()

    def delete(self, engram_id: str) -> None:
        """Hard delete: row, tokens and recall log all go."""
        self._conn.execute("DELETE FROM engrams WHERE engram_id=?", (engram_id,))
        self._conn.execute("DELETE FROM engram_tokens WHERE engram_id=?", (engram_id,))
        self._conn.execute("DELETE FROM recall_log WHERE engram_id=?", (engram_id,))
        self._conn.commit()

    # -- reads ---------------------------------------------------------------
    def get(self, engram_id: str) -> Optional[Engram]:
        row = self._conn.execute(
            "SELECT * FROM engrams WHERE engram_id=?", (engram_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def list(self, scope: str, limit: Optional[int] = None) -> List[Engram]:
        sql = "SELECT * FROM engrams WHERE scope=? ORDER BY created_at"
        params: List[object] = [scope]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self._hydrate(self._conn.execute(sql, params).fetchall())

    def count(self, scope: str, max_level: Optional[int] = None) -> int:
        sql = "SELECT COUNT(*) FROM engrams WHERE scope=?"
        params: List[object] = [scope]
        if max_level is not None:
            sql += " AND level < ?"
            params.append(max_level)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def candidates(
        self, scope: str, tokens: Sequence[str], max_level: int, limit: int = 50
    ) -> List[str]:
        if not tokens:
            return []
        placeholders = ",".join("?" for _ in tokens)
        sql = """
            SELECT e.engram_id, COUNT(*) AS hits
            FROM engram_tokens t
            JOIN engrams e ON e.engram_id = t.engram_id
            WHERE e.scope = ? AND e.level < ? AND t.token IN ({})
            GROUP BY e.engram_id
            ORDER BY hits DESC
            LIMIT ?
        """.format(placeholders)
        params: List[object] = [scope, max_level] + list(tokens) + [limit]
        return [r["engram_id"] for r in self._conn.execute(sql, params).fetchall()]

    def recent(self, scope: str, n: int = 300) -> List[Engram]:
        rows = self._conn.execute(
            "SELECT * FROM engrams WHERE scope=? ORDER BY created_at DESC LIMIT ?",
            (scope, n),
        ).fetchall()
        return self._hydrate(rows)

    def max_similarity(self, scope: str, tokens: Set[str], n: int = 300) -> float:
        best = 0.0
        for e in self.recent(scope, n):
            best = max(best, jaccard(tokens, e.tokens))
        return best

    def count_similar(self, scope: str, tokens: Set[str], threshold: float = 0.35, n: int = 300) -> int:
        return sum(1 for e in self.recent(scope, n) if jaccard(tokens, e.tokens) >= threshold)

    def best_match(self, scope: str, tokens: Set[str], n: int = 300) -> Tuple[Optional[Engram], float]:
        """Most similar recent engram -- used to detect a re-telling."""
        best: Optional[Engram] = None
        best_sim = 0.0
        for e in self.recent(scope, n):
            sim = jaccard(tokens, e.tokens)
            if sim > best_sim:
                best, best_sim = e, sim
        return best, best_sim

    # -- scope state ---------------------------------------------------------
    def get_state(self, scope: str) -> Dict[str, object]:
        row = self._conn.execute(
            "SELECT state FROM scope_state WHERE scope=?", (scope,)
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["state"])
        except (TypeError, ValueError):
            return {}

    def set_state(self, scope: str, profile: str, state: Dict[str, object]) -> None:
        self._conn.execute(
            """INSERT INTO scope_state (scope, profile, state, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(scope) DO UPDATE SET
                 profile=excluded.profile, state=excluded.state, updated_at=excluded.updated_at""",
            (scope, profile, json.dumps(state, ensure_ascii=False), _dt(datetime.utcnow())),
        )
        self._conn.commit()

    # -- audit ---------------------------------------------------------------
    def log_recall(self, scope: str, engram_id: str, query: str, at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO recall_log (scope, engram_id, query, at) VALUES (?,?,?,?)",
            (scope, engram_id, query, _dt(at)),
        )
        self._conn.commit()

    # -- internals -----------------------------------------------------------
    def _hydrate(self, rows: Iterable[sqlite3.Row]) -> List[Engram]:
        rows = list(rows)
        if not rows:
            return []
        ids = [r["engram_id"] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        token_map: Dict[str, Set[str]] = {i: set() for i in ids}
        for tr in self._conn.execute(
            "SELECT engram_id, token FROM engram_tokens WHERE engram_id IN ({})".format(
                placeholders
            ),
            ids,
        ):
            token_map[tr["engram_id"]].add(tr["token"])

        out: List[Engram] = []
        for row in rows:
            out.append(
                Engram(
                    engram_id=row["engram_id"],
                    scope=row["scope"],
                    kind=row["kind"],
                    raw_text=row["raw_text"],
                    gist=row["gist"] or "",
                    emotion=row["emotion"] or "",
                    level=int(row["level"]),
                    importance=float(row["importance"]),
                    stability=float(row["stability"]),
                    difficulty=float(row["difficulty"]),
                    active_age_days=float(row["active_age_days"]),
                    recall_count=int(row["recall_count"]),
                    created_at=_parse(row["created_at"]) or datetime.utcnow(),
                    last_recall_at=_parse(row["last_recall_at"]),
                    last_mention_at=_parse(row["last_mention_at"]),
                    refs=json.loads(row["refs"] or "[]"),
                    tokens=token_map.get(row["engram_id"], set()),
                    meta=json.loads(row["meta"] or "{}"),
                )
            )
        return out


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
