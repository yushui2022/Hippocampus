"""SQLite persistence for Hippocampus v2.

One file holds engrams, their token index, vectors, facts, scope state and an
audit log. WAL + a busy timeout keep a reader (dashboard, backup) from fighting
the writer.

The schema is versioned in ``meta.schema_version`` and migrations are
idempotent: opening an old v0 database adds the v2 columns in place.
"""

from __future__ import annotations

import json
import sqlite3
from array import array
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Engram, Fact, new_id
from .tokens import jaccard

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS engrams (
    engram_id       TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'episodic',
    raw_text        TEXT NOT NULL,
    gist            TEXT DEFAULT '',
    gist_source     TEXT NOT NULL DEFAULT 'none',
    emotion         TEXT DEFAULT '',
    level           INTEGER NOT NULL DEFAULT 0,
    fidelity        INTEGER NOT NULL DEFAULT 0,
    importance      REAL NOT NULL DEFAULT 0.5,
    stability       REAL NOT NULL DEFAULT 1.0,
    difficulty      REAL NOT NULL DEFAULT 5.0,
    active_age_days REAL NOT NULL DEFAULT 0.0,
    recall_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    last_recall_at  TEXT,
    last_mention_at TEXT,
    last_active_at  TEXT,
    corrected_at    TEXT,
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
CREATE TABLE IF NOT EXISTS recall_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    engram_id   TEXT NOT NULL,
    query       TEXT,
    topic       TEXT,
    spontaneous INTEGER NOT NULL DEFAULT 0,
    surfaced    INTEGER NOT NULL DEFAULT 0,
    level       INTEGER,
    score       REAL,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engram_vectors (
    engram_id TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS engram_vectors_shadow (
    engram_id TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id       TEXT PRIMARY KEY,
    scope         TEXT NOT NULL,
    subject       TEXT NOT NULL,
    predicate     TEXT NOT NULL,
    object        TEXT NOT NULL,
    source_engram TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    fact_id       TEXT PRIMARY KEY,
    scope         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT '',
    predicate     TEXT NOT NULL DEFAULT '',
    obj           TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0.7,
    importance    REAL NOT NULL DEFAULT 0.5,
    status        TEXT NOT NULL DEFAULT 'active',
    ttl_days      REAL NOT NULL DEFAULT 180,
    expires_at    TEXT,
    valid_from    TEXT,
    valid_to      TEXT,
    source_engram TEXT NOT NULL,
    source_refs   TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engrams_scope ON engrams(scope, created_at);
CREATE INDEX IF NOT EXISTS idx_engrams_level ON engrams(scope, level);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON engram_tokens(token);
CREATE INDEX IF NOT EXISTS idx_recall_scope ON recall_log(scope, at);
CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope, status, kind);
CREATE INDEX IF NOT EXISTS idx_facts_exp ON facts(expires_at);
CREATE INDEX IF NOT EXISTS idx_graph_subject ON graph_edges(scope, subject);
CREATE INDEX IF NOT EXISTS idx_graph_object ON graph_edges(scope, object);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_unique
    ON graph_edges(scope, subject, predicate, object, source_engram);
CREATE INDEX IF NOT EXISTS idx_vec_model ON engram_vectors(model);
"""

_ENGRAM_COLUMNS_V2 = {
    "gist_source": "TEXT NOT NULL DEFAULT 'none'",
    "fidelity": "INTEGER NOT NULL DEFAULT 0",
    "last_active_at": "TEXT",
    "corrected_at": "TEXT",
}

_RECALL_COLUMNS_V2 = {
    "topic": "TEXT",
    "spontaneous": "INTEGER NOT NULL DEFAULT 0",
    "surfaced": "INTEGER NOT NULL DEFAULT 0",
    "level": "INTEGER",
    "score": "REAL",
}


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self.version = 0  # bumped on writes; retrieval caches key off it
        self._conn = sqlite3.connect(path, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    # -- schema --------------------------------------------------------------
    def _migrate(self) -> None:
        _ensure_columns(self._conn, "engrams", _ENGRAM_COLUMNS_V2)
        _ensure_columns(self._conn, "recall_log", _RECALL_COLUMNS_V2)
        # Backfill v0 rows: level was the only clearness signal.
        self._conn.execute(
            "UPDATE engrams SET fidelity = level WHERE fidelity < level"
        )
        self._conn.execute(
            "UPDATE engrams SET gist_source = CASE "
            "WHEN gist IS NULL OR gist = '' THEN 'none' ELSE 'extractive' END "
            "WHERE gist_source = 'none'"
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        try:
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _bump(self) -> None:
        self.version += 1

    # -- engram writes -------------------------------------------------------
    def insert(self, engram: Engram) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO engrams (
                    engram_id, scope, kind, raw_text, gist, gist_source, emotion,
                    level, fidelity, importance, stability, difficulty,
                    active_age_days, recall_count, created_at, last_recall_at,
                    last_mention_at, last_active_at, corrected_at, refs, meta
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                self._row(engram),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO engram_tokens (engram_id, token) VALUES (?,?)",
                [(engram.engram_id, t) for t in engram.tokens],
            )
        self._bump()

    def update(self, engram: Engram) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE engrams SET
                    kind=?, raw_text=?, gist=?, gist_source=?, emotion=?,
                    level=?, fidelity=?, importance=?, stability=?, difficulty=?,
                    active_age_days=?, recall_count=?, last_recall_at=?,
                    last_mention_at=?, last_active_at=?, corrected_at=?,
                    refs=?, meta=?
                   WHERE engram_id=?""",
                (
                    engram.kind,
                    engram.raw_text,
                    engram.gist,
                    engram.gist_source,
                    engram.emotion,
                    engram.level,
                    engram.fidelity,
                    engram.importance,
                    engram.stability,
                    engram.difficulty,
                    engram.active_age_days,
                    engram.recall_count,
                    _dt(engram.last_recall_at),
                    _dt(engram.last_mention_at),
                    _dt(engram.last_active_at),
                    _dt(engram.corrected_at),
                    json.dumps(engram.refs, ensure_ascii=False),
                    json.dumps(engram.meta, ensure_ascii=False),
                    engram.engram_id,
                ),
            )
        self._bump()

    def update_many(self, engrams: Iterable[Engram]) -> None:
        rows = [
            (
                e.kind,
                e.raw_text,
                e.gist,
                e.gist_source,
                e.emotion,
                e.level,
                e.fidelity,
                e.importance,
                e.stability,
                e.difficulty,
                e.active_age_days,
                e.recall_count,
                _dt(e.last_recall_at),
                _dt(e.last_mention_at),
                _dt(e.last_active_at),
                _dt(e.corrected_at),
                json.dumps(e.refs, ensure_ascii=False),
                json.dumps(e.meta, ensure_ascii=False),
                e.engram_id,
            )
            for e in engrams
        ]
        if not rows:
            return
        with self._conn:
            self._conn.executemany(
                """UPDATE engrams SET
                    kind=?, raw_text=?, gist=?, gist_source=?, emotion=?,
                    level=?, fidelity=?, importance=?, stability=?, difficulty=?,
                    active_age_days=?, recall_count=?, last_recall_at=?,
                    last_mention_at=?, last_active_at=?, corrected_at=?,
                    refs=?, meta=?
                   WHERE engram_id=?""",
                rows,
            )
        self._bump()

    def add_tokens(self, engram_id: str, tokens: Iterable[str]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO engram_tokens (engram_id, token) VALUES (?,?)",
                [(engram_id, t) for t in tokens],
            )
        self._bump()

    def replace_tokens(self, engram_id: str, tokens: Iterable[str]) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM engram_tokens WHERE engram_id=?", (engram_id,)
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO engram_tokens (engram_id, token) VALUES (?,?)",
                [(engram_id, t) for t in tokens],
            )
        self._bump()

    def delete(self, engram_id: str) -> None:
        """Hard delete: row, tokens, vectors, facts and audit log all go."""
        with self._conn:
            self._conn.execute("DELETE FROM engrams WHERE engram_id=?", (engram_id,))
            self._conn.execute(
                "DELETE FROM engram_tokens WHERE engram_id=?", (engram_id,)
            )
            self._conn.execute(
                "DELETE FROM engram_vectors WHERE engram_id=?", (engram_id,)
            )
            self._conn.execute(
                "DELETE FROM recall_log WHERE engram_id=?", (engram_id,)
            )
            self._conn.execute(
                "DELETE FROM facts WHERE source_engram=?", (engram_id,)
            )
            self._conn.execute(
                "DELETE FROM graph_edges WHERE source_engram=?", (engram_id,)
            )
        self._bump()

    def delete_facts_for_engram(self, engram_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM facts WHERE source_engram=?", (engram_id,)
            )
            self._conn.execute(
                "DELETE FROM graph_edges WHERE source_engram=?", (engram_id,)
            )

    def mark_mentioned(self, engram_ids: Iterable[str], at: datetime) -> None:
        ids = list(engram_ids)
        if not ids:
            return
        with self._conn:
            self._conn.executemany(
                "UPDATE engrams SET last_mention_at=? WHERE engram_id=?",
                [(_dt(at), i) for i in ids],
            )

    # -- engram reads --------------------------------------------------------
    def get(self, engram_id: str) -> Optional[Engram]:
        row = self._conn.execute(
            "SELECT * FROM engrams WHERE engram_id=?", (engram_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def get_many(self, engram_ids: Iterable[str]) -> Dict[str, Engram]:
        ids = [i for i in dict.fromkeys(engram_ids) if i]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            "SELECT * FROM engrams WHERE engram_id IN ({})".format(placeholders),
            ids,
        ).fetchall()
        return {e.engram_id: e for e in self._hydrate(rows)}

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

    def active_ids(self, scope: str, max_level: int) -> List[str]:
        rows = self._conn.execute(
            "SELECT engram_id FROM engrams WHERE scope=? AND level < ?",
            (scope, max_level),
        ).fetchall()
        return [r["engram_id"] for r in rows]

    def token_docs(self, scope: str, max_level: int) -> List[Tuple[str, Set[str]]]:
        rows = self._conn.execute(
            """SELECT t.engram_id, t.token
               FROM engram_tokens t JOIN engrams e ON e.engram_id=t.engram_id
               WHERE e.scope=? AND e.level < ?""",
            (scope, max_level),
        ).fetchall()
        grouped: Dict[str, Set[str]] = {}
        for row in rows:
            grouped.setdefault(row["engram_id"], set()).add(row["token"])
        return list(grouped.items())

    def recent(self, scope: str, n: int = 300) -> List[Engram]:
        rows = self._conn.execute(
            "SELECT * FROM engrams WHERE scope=? ORDER BY created_at DESC LIMIT ?",
            (scope, n),
        ).fetchall()
        return self._hydrate(rows)

    def best_match(
        self, scope: str, tokens: Set[str], n: int = 300
    ) -> Tuple[Optional[Engram], float]:
        best: Optional[Engram] = None
        best_sim = 0.0
        for e in self.recent(scope, n):
            sim = jaccard(tokens, e.tokens)
            if sim > best_sim:
                best, best_sim = e, sim
        return best, best_sim

    def count_similar(
        self, scope: str, tokens: Set[str], threshold: float = 0.35, n: int = 300
    ) -> int:
        return self.match_stats(scope, tokens, threshold, n)[2]

    def match_stats(
        self,
        scope: str,
        tokens: Set[str],
        threshold: float = 0.35,
        n: int = 300,
    ) -> Tuple[Optional[Engram], float, int]:
        """Best match, best similarity and similar count in one pass."""
        best: Optional[Engram] = None
        best_sim = 0.0
        similar = 0
        for engram in self.recent(scope, n):
            sim = jaccard(tokens, engram.tokens)
            if sim > best_sim:
                best, best_sim = engram, sim
            if sim >= threshold:
                similar += 1
        return best, best_sim, similar

    def max_similarity(self, scope: str, tokens: Set[str], n: int = 300) -> float:
        return self.best_match(scope, tokens, n)[1]

    # -- vectors -------------------------------------------------------------
    def upsert_vector(
        self, engram_id: str, model: str, dim: int, vec: Sequence[float]
    ) -> None:
        blob = array("f", [float(x) for x in vec]).tobytes()
        with self._conn:
            self._conn.execute(
                """INSERT INTO engram_vectors (engram_id, model, dim, vec)
                   VALUES (?,?,?,?)
                   ON CONFLICT(engram_id) DO UPDATE SET
                     model=excluded.model, dim=excluded.dim, vec=excluded.vec""",
                (engram_id, model, int(dim), blob),
            )

    def begin_shadow_vectors(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM engram_vectors_shadow")

    def upsert_shadow_vector(
        self, engram_id: str, model: str, dim: int, vec: Sequence[float]
    ) -> None:
        blob = array("f", [float(x) for x in vec]).tobytes()
        with self._conn:
            self._conn.execute(
                """INSERT INTO engram_vectors_shadow (engram_id, model, dim, vec)
                   VALUES (?,?,?,?)
                   ON CONFLICT(engram_id) DO UPDATE SET
                     model=excluded.model, dim=excluded.dim, vec=excluded.vec""",
                (engram_id, model, int(dim), blob),
            )

    def commit_shadow_vectors(self) -> int:
        with self._conn:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM engram_vectors_shadow"
            ).fetchone()
            count = int(row[0])
            self._conn.execute("DELETE FROM engram_vectors")
            self._conn.execute(
                "INSERT INTO engram_vectors (engram_id, model, dim, vec) "
                "SELECT engram_id, model, dim, vec FROM engram_vectors_shadow"
            )
            self._conn.execute("DELETE FROM engram_vectors_shadow")
        self._bump()
        return count

    def drop_shadow_vectors(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM engram_vectors_shadow")

    def get_vector(self, engram_id: str) -> Optional[List[float]]:
        row = self._conn.execute(
            "SELECT dim, vec FROM engram_vectors WHERE engram_id=?", (engram_id,)
        ).fetchone()
        if row is None:
            return None
        return _blob_to_vec(row["vec"], int(row["dim"]))

    def vectors_for_scope(self, scope: str) -> Dict[str, List[float]]:
        rows = self._conn.execute(
            """SELECT v.engram_id, v.dim, v.vec
               FROM engram_vectors v JOIN engrams e ON e.engram_id=v.engram_id
               WHERE e.scope=?""",
            (scope,),
        ).fetchall()
        return {r["engram_id"]: _blob_to_vec(r["vec"], int(r["dim"])) for r in rows}

    def delete_vector(self, engram_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM engram_vectors WHERE engram_id=?", (engram_id,)
            )

    def clear_vectors(self, scope: Optional[str] = None) -> int:
        with self._conn:
            if scope:
                cursor = self._conn.execute(
                    "DELETE FROM engram_vectors WHERE engram_id IN "
                    "(SELECT engram_id FROM engrams WHERE scope=?)",
                    (scope,),
                )
            else:
                cursor = self._conn.execute("DELETE FROM engram_vectors")
        self._bump()
        return int(cursor.rowcount or 0)

    def vector_dim(self, engram_id: str) -> int:
        row = self._conn.execute(
            "SELECT dim FROM engram_vectors WHERE engram_id=?", (engram_id,)
        ).fetchone()
        return int(row["dim"]) if row else 0

    def distinct_scopes(self) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT scope FROM engrams ORDER BY scope"
        ).fetchall()
        return [row["scope"] for row in rows]

    def vector_count(self, scope: Optional[str] = None) -> int:
        if scope is None:
            row = self._conn.execute("SELECT COUNT(*) FROM engram_vectors").fetchone()
        else:
            row = self._conn.execute(
                """SELECT COUNT(*) FROM engram_vectors v
                   JOIN engrams e ON e.engram_id=v.engram_id WHERE e.scope=?""",
                (scope,),
            ).fetchone()
        return int(row[0])

    # -- graph ---------------------------------------------------------------
    def insert_edge(
        self,
        scope: str,
        subject: str,
        predicate: str,
        obj: str,
        source_engram: str,
        at: datetime,
    ) -> None:
        if not subject.strip() or not obj.strip():
            return
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO graph_edges
                   (edge_id, scope, subject, predicate, object, source_engram, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (new_id("edge"), scope, subject, predicate, obj, source_engram, _dt(at)),
            )

    def graph_candidates(
        self, scope: str, terms: Iterable[str], max_level: int, limit: int = 30
    ) -> List[str]:
        cleaned = [t for t in terms if t]
        if not cleaned:
            return []
        clauses = []
        params: List[object] = [scope, max_level]
        for term in cleaned[:12]:
            clauses.append("(g.subject LIKE ? OR g.object LIKE ?)")
            params.append("%" + term + "%")
            params.append("%" + term + "%")
        params.append(limit)
        sql = """
            SELECT DISTINCT g.source_engram
            FROM graph_edges g JOIN engrams e ON e.engram_id = g.source_engram
            WHERE g.scope = ? AND e.level < ? AND ({})
            LIMIT ?
        """.format(" OR ".join(clauses))
        return [r["source_engram"] for r in self._conn.execute(sql, params).fetchall()]

    def count_edges(self, scope: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE scope=?", (scope,)
        ).fetchone()
        return int(row[0])

    # -- facts ---------------------------------------------------------------
    def insert_fact(self, fact: Fact) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO facts (
                    fact_id, scope, kind, subject, predicate, obj, text,
                    confidence, importance, status, ttl_days, expires_at,
                    valid_from, valid_to, source_engram, source_refs,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fact.fact_id,
                    fact.scope,
                    fact.kind,
                    fact.subject,
                    fact.predicate,
                    fact.obj,
                    fact.text,
                    fact.confidence,
                    fact.importance,
                    fact.status,
                    fact.ttl_days,
                    _dt(fact.expires_at),
                    _dt(fact.valid_from),
                    _dt(fact.valid_to),
                    fact.source_engram,
                    json.dumps(fact.source_refs, ensure_ascii=False),
                    _dt(fact.created_at),
                    _dt(fact.updated_at),
                ),
            )

    def list_facts(
        self,
        scope: str,
        now: Optional[datetime] = None,
        kinds: Optional[Sequence[str]] = None,
        include_inactive: bool = False,
    ) -> List[Fact]:
        sql = "SELECT * FROM facts WHERE scope=?"
        params: List[object] = [scope]
        if not include_inactive:
            sql += " AND status='active'"
            if now is not None:
                sql += " AND (expires_at IS NULL OR expires_at > ?)"
                params.append(_dt(now))
        if kinds:
            sql += " AND kind IN ({})".format(",".join("?" for _ in kinds))
            params.extend(kinds)
        sql += " ORDER BY created_at DESC"
        return [_fact_from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def find_conflicting_facts(
        self, scope: str, kind: str, subject: str, predicate: str, exclude_id: str = ""
    ) -> List[Fact]:
        rows = self._conn.execute(
            """SELECT * FROM facts
               WHERE scope=? AND kind=? AND subject=? AND predicate=?
                 AND status='active' AND fact_id != ?""",
            (scope, kind, subject, predicate, exclude_id),
        ).fetchall()
        return [_fact_from_row(r) for r in rows]

    def supersede_fact(self, fact_id: str, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE facts SET status='superseded', valid_to=?, updated_at=? "
                "WHERE fact_id=?",
                (_dt(at), _dt(at), fact_id),
            )

    def expire_facts(self, now: datetime) -> int:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE facts SET status='expired', updated_at=? "
                "WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (_dt(now), _dt(now)),
            )
        return int(cur.rowcount or 0)

    def count_facts(self, scope: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE scope=? AND status='active'", (scope,)
        ).fetchone()
        return int(row[0])

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
        with self._conn:
            self._conn.execute(
                """INSERT INTO scope_state (scope, profile, state, updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(scope) DO UPDATE SET
                     profile=excluded.profile, state=excluded.state,
                     updated_at=excluded.updated_at""",
                (
                    scope,
                    profile,
                    json.dumps(state, ensure_ascii=False),
                    _dt(datetime.now(timezone.utc)),
                ),
            )

    # -- audit ---------------------------------------------------------------
    def log_recall(
        self,
        scope: str,
        engram_id: str,
        query: str,
        at: datetime,
        *,
        topic: str = "",
        spontaneous: bool = False,
        surfaced: bool = False,
        level: Optional[int] = None,
        score: Optional[float] = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO recall_log
                   (scope, engram_id, query, topic, spontaneous, surfaced, level, score, at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    scope,
                    engram_id,
                    query,
                    topic,
                    int(bool(spontaneous)),
                    int(bool(surfaced)),
                    level,
                    score,
                    _dt(at),
                ),
            )

    def recall_stats(self, scope: str, since: Optional[datetime] = None) -> Dict[str, int]:
        sql = (
            "SELECT COUNT(*) AS n, "
            "SUM(spontaneous) AS spontaneous, SUM(surfaced) AS surfaced "
            "FROM recall_log WHERE scope=?"
        )
        params: List[object] = [scope]
        if since is not None:
            sql += " AND at >= ?"
            params.append(_dt(since))
        row = self._conn.execute(sql, params).fetchone()
        return {
            "total": int(row["n"] or 0),
            "spontaneous": int(row["spontaneous"] or 0),
            "surfaced": int(row["surfaced"] or 0),
        }

    def topic_mentioned_since(
        self, scope: str, topic: str, since: datetime
    ) -> int:
        if not topic:
            return 0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM recall_log "
            "WHERE scope=? AND surfaced=1 AND topic=? AND at >= ?",
            (scope, topic, _dt(since)),
        ).fetchone()
        return int(row[0] or 0)

    def mentioned_today(self, scope: str, at: datetime) -> int:
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT engram_id) FROM recall_log "
            "WHERE scope=? AND surfaced=1 AND at >= ?",
            (scope, _dt(start)),
        ).fetchone()
        return int(row[0] or 0)

    # -- meta ----------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # -- maintenance ---------------------------------------------------------
    def consistency_report(self) -> Dict[str, int]:
        engrams = int(self._conn.execute("SELECT COUNT(*) FROM engrams").fetchone()[0])
        with_tokens = int(
            self._conn.execute(
                "SELECT COUNT(DISTINCT engram_id) FROM engram_tokens"
            ).fetchone()[0]
        )
        vectors = int(
            self._conn.execute("SELECT COUNT(*) FROM engram_vectors").fetchone()[0]
        )
        facts = int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        edges = int(self._conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0])
        return {
            "engrams": engrams,
            "with_tokens": with_tokens,
            "missing_tokens": max(0, engrams - with_tokens),
            "vectors": vectors,
            "facts": facts,
            "edges": edges,
        }

    def backup(self, destination: str) -> None:
        self._conn.commit()
        target = sqlite3.connect(destination)
        try:
            self._conn.backup(target)
            target.commit()
        finally:
            target.close()

    # -- internals -----------------------------------------------------------
    @staticmethod
    def _row(engram: Engram) -> Tuple[object, ...]:
        return (
            engram.engram_id,
            engram.scope,
            engram.kind,
            engram.raw_text,
            engram.gist,
            engram.gist_source,
            engram.emotion,
            engram.level,
            engram.fidelity,
            engram.importance,
            engram.stability,
            engram.difficulty,
            engram.active_age_days,
            engram.recall_count,
            _dt(engram.created_at),
            _dt(engram.last_recall_at),
            _dt(engram.last_mention_at),
            _dt(engram.last_active_at),
            _dt(engram.corrected_at),
            json.dumps(engram.refs, ensure_ascii=False),
            json.dumps(engram.meta, ensure_ascii=False),
        )

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
                    gist_source=row["gist_source"] or "none",
                    emotion=row["emotion"] or "",
                    level=int(row["level"]),
                    fidelity=int(row["fidelity"] or 0),
                    importance=float(row["importance"]),
                    stability=float(row["stability"]),
                    difficulty=float(row["difficulty"]),
                    active_age_days=float(row["active_age_days"]),
                    recall_count=int(row["recall_count"]),
                    created_at=_parse(row["created_at"]) or datetime.now(timezone.utc),
                    last_recall_at=_parse(row["last_recall_at"]),
                    last_mention_at=_parse(row["last_mention_at"]),
                    last_active_at=_parse(row["last_active_at"]),
                    corrected_at=_parse(row["corrected_at"]),
                    refs=json.loads(row["refs"] or "[]"),
                    tokens=token_map.get(row["engram_id"], set()),
                    meta=json.loads(row["meta"] or "{}"),
                )
            )
        return out


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {
        r["name"] for r in conn.execute("PRAGMA table_info({})".format(table)).fetchall()
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, name, ddl))


def _blob_to_vec(blob: bytes, dim: int) -> List[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)[:dim]


def _fact_from_row(row: sqlite3.Row) -> Fact:
    return Fact(
        fact_id=row["fact_id"],
        scope=row["scope"],
        kind=row["kind"],
        subject=row["subject"],
        predicate=row["predicate"],
        obj=row["obj"],
        text=row["text"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        status=row["status"],
        ttl_days=float(row["ttl_days"]),
        expires_at=_parse(row["expires_at"]),
        valid_from=_parse(row["valid_from"]),
        valid_to=_parse(row["valid_to"]),
        source_engram=row["source_engram"],
        source_refs=json.loads(row["source_refs"] or "[]"),
        created_at=_parse(row["created_at"]) or datetime.now(timezone.utc),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
