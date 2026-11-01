# Changelog

## v0.2.0 — 2026-11-01

The v2 core: fix the invariants first, then add retrieval, episodes and facts.

### Fixed (breaking where noted)

- **`fidelity` vs `level`**: detail loss is now monotonic. Recall can improve
  accessibility but cannot restore lost detail. (Breaking: `level` no longer
  drops below `fidelity`.)
- **`tick()` catch-up**: advances one calendar day at a time; missing days use
  `idle_ratio`. A single 90-day catch-up no longer ages memories by 90 active
  days. (Breaking: `active=True` affects only the current day.)
- **`stability` cap**: `decay.reinforce()` now respects `Profile.max_stability`.
  Previously `S_MAX` only applied to initial stability.
- **recall/mention separation**: `recall()` no longer writes
  `last_mention_at`; use `mark_mentioned()`.
- **`recall()` defaults to `reinforce=False`**; `peek()` never writes.
  (Breaking: callers that relied on implicit strengthening must pass
  `reinforce=True`.)
- **gist provenance**: `gist_source` (`none | extractive | llm`). Without a
  summarizer, L1/L2 render an extractive truncation, never the full original.
- **tokens on retelling**: rebuilt from the current `raw_text` instead of an
  unbounded union.
- **soft forget** clears tokens/vectors and derived facts.
- **WAL + busy_timeout + foreign keys** on every connection.

### Added

- Hybrid retrieval: BM25 + vector search + RRF + MMR + character budget.
- `HashingEmbedder` (zero-dependency, deterministic) and `OpenAIEmbedder`
  (bge-m3 / any OpenAI-compatible endpoint).
- `VectorIndex` with optional NumPy acceleration; vectors persisted in SQLite.
- `Episode` / `WindowSegmenter` and `remember_episode()` / `feed_episode()`.
- Facts table with typed TTL, `valid_from/valid_to`, supersede semantics and
  a mandatory `source_engram`.
- `RuleFactExtractor` (preferences, identity, location, plans) that only reads
  user-actor events.
- `salient()` proactive recall with clarity/emotion/cooldown filters and a
  daily mention budget.
- `correct()`, `set_importance(force=...)`, `mark_mentioned()`.
- `ops.snapshot()` read-only inspection and `ops.backup_database()`.
- Schema versioning and idempotent migration from v0 (`schema_version` 0 → 4).
- `eval.bench`, `eval.paraphrase`, `eval.false_memory`, `--embedder` option on `eval.replay_30d`.
- 37 unittest cases covering the invariants.

### Changed

- `recall()` accepts `max_chars`, `spontaneous`, `kinds`.
- `tick()` accepts `active_days` and returns `TickReport`.
- `Store` API extended (`match_stats`, vectors, facts, meta, `consistency_report`).
- README rewritten for v2; old pytest-style `tests/test_kernel.py` replaced by
  `tests/test_v2.py` plus a shallow `tests/test_legacy_api.py`.

### Known gaps

- Real bge-m3 numbers require an API key; offline baselines are 0.733 (token)
  and 0.800 (hashing) on `eval.replay_30d`.
- Graph memory, async writes and the observatory UI are not implemented.
- Pure-Python vector search is fine up to a few thousand memories; install
  NumPy (or add FAISS) for 10k+.
