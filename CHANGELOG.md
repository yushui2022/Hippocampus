# Changelog

## v0.6.0 — 2026-11-01

Latency work.

### Added

- `docs/PERFORMANCE.md` with measured tables and scaling estimates.
- `eval.bench --embedder local` for the Ollama path.
- Query embedding cache (`Profile.query_cache_size = 128`).
- Batch candidate hydration (`Store.get_many`).
- BM25 postings index: score only documents containing query tokens.
- NumPy vector matrix cached per scope and row-normalised at build time.

### Measured

| N | Retrieval | recall p50 |
| ---: | --- | ---: |
| 1 000 | BM25 | 4.4 ms |
| 1 000 | hashing 256d | 7.6 ms |
| 1 000 | local bge-m3, distinct | 20.4 ms |

The 1 000-memory bge path went from 96.5 ms to 20.4 ms after the matrix cache,
batch hydration and postings index (5×). 46 tests.

## v0.5.0 — 2026-11-01

The v2 architecture is complete. Schema v5.

### Added

- Topic-level mention budget: `topic_cooldown_hours=24`,
  `topic_repeat_limit=1`; `recall_log.topic` records what was surfaced.
- Shadow vector rebuild: `engram_vectors_shadow` plus an atomic swap;
  `rebuild_vectors(shadow=True)` and `--no-shadow` on the CLI.
- Read-only observatory: `python -m hippocampus.observatory --db ...`
  with `/api/snapshot`, `/api/engrams`, `/api/facts` and a plain HTML page.
- Opt-in graph memory: `Profile.enable_graph=True` writes
  `(subject, predicate, object)` edges from user facts and adds a graph route
  to the candidate union.
- Blind A/B harness: `python -m eval.blind_test` (interactive) and
  `--auto` for smoke tests.
- `docs/ARCHITECTURE.md` with the module map and paths.

### Changed

- Migration target is now schema v5 (`recall_log.topic`, graph table,
  shadow vector table).
- `stats()` exposes graph edge count.
- `ops` exports `list_engrams` / `list_facts` for the observatory.

46 unittest cases.

## v0.4.0 — 2026-11-01

Non-blocking writes.

### Added

- `indexer.py`: background embedding worker (`AsyncIndexer`) with a bounded
  queue, result draining on the main thread, error capture and flush-on-close.
- `Hippocampus(async_index=True)`; defaults to on for file-backed stores when
  an embedder is configured, off for `:memory:`.
- `stats()["index_pending"]` / `["index_dropped"]`.

### Changed

- Async mode skips the synchronous query embedding for merge detection:
  merging falls back to token overlap. Retrieval still embeds the query.
- `close()` flushes pending vectors before closing SQLite.
- Stale results are ignored when the memory was deleted or corrected while the
  worker was running.

Measured with local bge-m3: five writes 78 ms → 1.7 ms; vectors flush on close.
41 unittest cases.

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
