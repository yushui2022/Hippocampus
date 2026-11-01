# Changelog

## v0.3.0 — 2026-11-01

Local embeddings, measured calibration, and model-change support.

### Added

- `LocalEmbedder`: Ollama bge-m3 at `http://127.0.0.1:11434/v1` (1024d).
- `Hippocampus.rebuild_vectors()` and `python -m hippocampus.rebuild` CLI
  (`--provider local|openai|hashing`, `--dry-run`, batch progress).
- `Store.clear_vectors`, `distinct_scopes`, `vector_dim`.
- `docs/LOCAL_EMBEDDING.md`, `examples/local_embedding.py`,
  `make local-demo`, `make local-eval`, `make rebuild-local`.

### Changed

- `importance_gain` is a Profile field, default **14** (was 11). On the
  283-turn replay, `recall@6` moved 0.733 → 0.933 (token) and 1.000 with
  local bge-m3.
- An explicit re-telling now recovers **one level** of fidelity: it is new
  evidence, not recall. Recall itself still cannot restore detail.
- Cosine merge uses `embedding_merge_threshold=0.95`; token merge stays 0.85.
- Companion retrieval weights moved to relevance 0.50 / R 0.15 / I 0.15 /
  recency 0.05 / relationship 0.10 / emotion 0.05.

### Measured (local bge-m3, Apple Silicon)

| Metric | Token | bge-m3 |
| --- | --- | --- |
| `recall@6 (clear)` | 0.933 | 1.000 |
| `fog_missing` | 0.000 | 0.000 |
| `over_recall` | 0.100 | 0.100 |
| `para_recall@6` | 0.600 (hashing) | 1.000 |
| recall p50 | 0.4 ms | 18.6 ms |

39 unittest cases.

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

- `rebuild_vectors()` and `python -m hippocampus.rebuild` for model changes.
- Companion importance calibration: `importance_gain = 14`.
- One-level fidelity recovery on an explicit re-telling (new evidence, not recall).
- Stricter cosine merge threshold (`embedding_merge_threshold = 0.95`).
- Hybrid retrieval: BM25 + vector search + RRF + MMR + character budget.
- `HashingEmbedder` (zero-dependency, deterministic), `LocalEmbedder`
  (Ollama bge-m3) and `OpenAIEmbedder` (any OpenAI-compatible endpoint).
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
- 39 unittest cases covering the invariants.

### Changed

- `recall()` accepts `max_chars`, `spontaneous`, `kinds`.
- `tick()` accepts `active_days` and returns `TickReport`.
- `Store` API extended (`match_stats`, vectors, facts, meta, `consistency_report`).
- Companion retrieval weights moved toward relevance (0.50) after hybrid
  candidates were measured to be crowded out by salient-but-unrelated memories.
- README rewritten for v2; old pytest-style `tests/test_kernel.py` replaced by
  `tests/test_v2.py` plus a shallow `tests/test_legacy_api.py`.

### Known gaps

- Real bge-m3 numbers require an API key; offline baselines are 0.733 (token)
  and 0.800 (hashing) on `eval.replay_30d`.
- Graph memory, async writes and the observatory UI are not implemented.
- Pure-Python vector search is fine up to a few thousand memories; install
  NumPy (or add FAISS) for 10k+.
