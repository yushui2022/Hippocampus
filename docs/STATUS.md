# v2 status

Snapshot: 2026-11-01, branch `v2.0`.

This maps the execution plan (`执行-海马记忆系统 v2`) to what actually exists.

## Done

### P0 — invariants

- [x] baseline frozen (tag-ready commit)
- [x] schema versioning + idempotent migration (v0 → v4)
- [x] stability cap (`Profile.max_stability`)
- [x] day-based `tick()` with idle defaults and `active_days`
- [x] `fidelity` vs `level` split
- [x] `gist_source` (`none | extractive | llm`); L1/L2 differ
- [x] recall/mention separation + `mark_mentioned()`
- [x] `recall(reinforce=False)` default; `peek()` never writes
- [x] WAL + `busy_timeout` + foreign keys
- [x] `correct()` / `set_importance(force=True)`
- [x] token rebuild instead of unbounded union
- [x] invariant test suite
- [x] recall log v2 (`spontaneous`, `surfaced`, `level`, `score`)
- [x] CI workflow + `Makefile`

### P1 — retrieval and write pipeline

- [x] `Embedder` protocol; `HashingEmbedder`; `LocalEmbedder`; `OpenAIEmbedder`
- [x] `VectorIndex` with SQLite persistence and optional NumPy
- [x] `engram_vectors` + provider fingerprint
- [x] BM25 over the persisted token index
- [x] candidate union + RRF + Hippocampus rerank
- [x] MMR + character budget
- [x] `Episode` / `WindowSegmenter` / `remember_episode` / `feed_episode`
- [x] `Summarizer` hook + deterministic `Summary` type
- [x] cosine merge when an embedder is configured
- [x] `eval.paraphrase`, `eval.bench`
- [x] migration of old rows (backfill + optional re-embed)

### P2 — facts, behaviour, ops

- [x] facts table with typed TTL / status / `valid_from` / `valid_to`
- [x] `RuleFactExtractor` (user-only, negation-aware)
- [x] supersede semantics and expiry in `consolidate()`
- [x] `salient()` proactive selection (clarity / emotion / cooldown)
- [x] daily mention budget + `mark_mentioned()`
- [x] uncertainty-preserving render (L3 has no entities)
- [x] read-only `ops.snapshot()`
- [x] backup via SQLite online API
- [x] hard-delete cascade (engram / tokens / vectors / facts / audit)
- [x] `rebuild_vectors()` + `python -m hippocampus.rebuild` CLI
- [x] async embedding indexer (`async_index=True`, drain on recall/close)
- [x] importance/stability calibration (`importance_gain=14`, re-telling recovery)
- [x] `eval.false_memory`
- [x] topic-level mention budget (`topic_cooldown_hours=24`)
- [x] shadow (zero-downtime) vector rebuild
- [x] read-only observatory (`hippocampus.observatory`, HTTP + page)
- [x] graph memory behind `Profile.enable_graph` (opt-in)
- [x] LLM summarizer (`LLMSummarizer`, async, DeepSeek/Ollama, extractive fallback)
- [x] LLM-judged summarizer A/B (`eval.summarizer_ab`)
- [x] blind A/B harness (`eval.blind_test`)
- [x] README + `docs/INVARIANTS.md`

## Not done (next)

- [ ] IM adapter (`AstrBot` / `NapCat`)
- [ ] long-running HTTP API service / daemon
- [ ] real-log holdout set (current numbers are synthetic)
- [ ] multi-process write coordination and auth

## Current numbers

| Mode | recall@6 | fog | fog_missing | over_recall |
| --- | --- | --- | --- | --- |
| token baseline | 0.933 | 1.000 | 0.000 | 0.100 |
| hashing vectors | 1.000 | 1.000 | 0.267 | 0.000 |
| local bge-m3 (Ollama) | 1.000 | 1.000 | 0.000 | 0.100 |

`eval.paraphrase --embedder openai`: 1.000 on 10 bundled pairs with local
bge-m3 (0.600 with hashing). `recall@6` is a forgetting-calibration metric, not
a retrieval metric.

Latency with NumPy, 1 000 engrams: recall p50 4.4 ms (BM25) / 20.4 ms
(local bge-m3, distinct queries); writes ~0.3 ms with async indexing. See
`docs/PERFORMANCE.md`.

## Three next tasks (in order)

1. Run the interactive blind test with a human judge and record the result.
2. Add the AstrBot / NapCat adapter so the kernel can run in QQ.
3. Add a long-running HTTP API and a real-log holdout set, then re-run the
   summarizer on real conversations.
