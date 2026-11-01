# Architecture

Hippocampus is a **library**, not a service and not a bot. It owns the shape of
memory; the caller owns generation, persona and transport.

```
┌──────────────────────────────────────────────────────────────┐
│ caller (companion app / bot adapter / CLI)                   │
│   remember · recall · salient · tick · correct · forget      │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ kernel.py                                                    │
│   policy: budgets, spontaneous filters, reinforcement        │
└───────┬─────────────────┬──────────────────┬─────────────────┘
        ▼                 ▼                  ▼
   decay.py          retrieval/           facts.py
   R · S · fidelity  BM25 · vectors       extract / TTL /
   levels · floors   RRF · MMR            supersede
        │                 │                  │
        └────────────┬────┴──────────────────┘
                     ▼
                  store.py
     SQLite: engrams · tokens · vectors · facts · graph · audit
```

## Design rules

1. **The kernel is scenario-free.** No product words, no channel words.
   Behaviour that varies by scenario lives in `profiles.py`.
2. **State changes are explicit.** `tick()` advances time; `recall()` does not
   unless `reinforce=True`; looking at memory never changes it.
3. **Detail is monotonic under forgetting.** `fidelity` only grows; recall can
   improve accessibility (`level`) but never restores lost detail. An explicit
   user re-telling is new evidence and may recover one level.
4. **Facts come from the user.** Assistant output can be remembered as an
   episode but never produces a fact. This blocks self-pollution loops.
5. **Degrade, don't fail.** No embedder → BM25. No summarizer → extractive
   gist. Vector provider down → token retrieval plus `needs_vector_rebuild`.
6. **Everything is inspectable.** Every fact has a source; every recall is
   auditable; the observatory is read-only.

## Write path

```
Event
  → tokenize
  → importance (rules + optional LLM)
  → retelling? ── yes → rehearse: S↑, age=0, fidelity-1, refs merge
  │                no
  ▼
Engram(level=0, fidelity=0, I, S0)
  → insert + tokens
  → async vector submit (optional)
  → fact extraction (user only) → facts + graph edges
```

Episode mode buffers turns and calls the same path with a joined text and a
summarizer-generated gist.

## Recall path

```
query
  ├─ BM25 top-50
  ├─ ANN top-50 (optional)
  └─ graph candidates (optional flag)
        ↓
      RRF (k=60)
        ↓
  Hippocampus rerank:
     0.50 relevance + 0.15 R + 0.15 I
   + 0.05 recency + 0.10 relationship + 0.05 emotion
   − 0.25 × cooldown
        ↓
      MMR (λ=0.7) → budget + char cap
        ↓
   render(level) → caller prompt
```

`render()` is where "like a human" is decided:

| level | output |
| --- | --- |
| 0 | raw text |
| 1 | LLM gist, or extractive first clause |
| 2 | shorter gist / truncation |
| 3 | vague period + emotion, no entities |
| 4 | not retrieved |

## Proactive path

```
salient()
  → level ≤ 1
  → negative memories need intimacy ≥ 0.7
  → global daily budget (3)
  → topic cooldown (24h, 1 per topic)
  → score I^0.6 × R^0.4 × exp(-age/60)
```

`mark_mentioned()` records what was actually said; `recall()` never does.

## Lifecycle

```
 tick(now, active_days)
   → split into calendar days
   → active day: factor 1.0; missing day: idle_ratio
   → active_age += factor (capped at natural age)
   → fidelity = max(fidelity, target_level(R, I))
   → level    = max(level, fidelity, target_level)

 nightly consolidate()
   → settle levels / fill gists
   → expire facts
```

`Profile.max_stability` caps plasticity so even important memories can blur.

## Storage

| Table | Purpose |
| --- | --- |
| `engrams` | trace + level/fidelity/importance/stability/active_age |
| `engram_tokens` | BM25 source |
| `engram_vectors` | persisted embeddings |
| `engram_vectors_shadow` | side table for zero-downtime rebuild |
| `facts` | typed, sourced, bi-temporal facts |
| `graph_edges` | subject/predicate/object (opt-in) |
| `recall_log` | audit: spontaneous, surfaced, topic, level, score |
| `scope_state` | profile, relationship, last tick |
| `meta` | schema version, embedder fingerprint |

WAL + `busy_timeout=5000`; writes go through one connection. The async indexer
never touches SQLite: it computes vectors, and the main thread drains them.

## Extension points

| Interface | Default | Swap for |
| --- | --- | --- |
| `Embedder` | `HashingEmbedder` / `LocalEmbedder` | bge-m3, OpenAI, local model |
| `Summarizer` | none (extractive) | LLM gist/topics |
| `FactExtractor` | `RuleFactExtractor` | LLM extractor with schema validation |
| `Store` | SQLite | Postgres/pgvector adapter |
| `Profile` | companion/support/education/knowledge | a new scenario |

## Non-goals

- Generation, persona, tone (caller)
- IM/voice/vision transport (adapter)
- Multi-agent shared memory
- A web app (the observatory is read-only JSON/HTML)
