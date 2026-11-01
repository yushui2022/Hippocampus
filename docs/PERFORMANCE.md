# Performance

Measured on this machine: Apple Silicon, 64 GB RAM, Python 3.9.6, NumPy 2.0.2,
Ollama bge-m3 (1024d, warm), pure-Python SQLite.

## Memory layer latency

| N engrams | Retrieval | recall p50 | recall p95 | Notes |
| ---: | --- | ---: | ---: | --- |
| 55 | BM25 only | 0.4 ms | — | 30-day eval |
| 55 | bge-m3 | 16.3 ms | — | eval replay |
| 1 000 | BM25 only | 4.4 ms | 5.1 ms | |
| 1 000 | hashing vectors | 7.6 ms | 8.7 ms | 256d |
| 1 000 | bge-m3, distinct queries | **20.4 ms** | **30.9 ms** | 1024d |

`recall p50` includes the query embedding (about 11 ms warm), BM25, vector
search, rerank, MMR and SQLite reads. Identical queries hit the 128-entry query
cache and skip the embedding call.

Writes (1 000 engrams, bge-m3):

| Mode | Perceived write | Background cost |
| --- | ---: | ---: |
| synchronous | ~10 ms each | — |
| `async_index=True` | **~0.3 ms each** | ~10 ms per vector, worker |

`close()` / `recall()` drain finished vectors. A 30-day `tick` at 1 000
engrams takes ~60 ms (one-time; normally one day per call).

## What was optimized

| Change | Effect |
| --- | --- |
| NumPy matrix cache for vectors | 1 000-memory bge recall 96.5 → 20.4 ms |
| Batch hydration (`Store.get_many`) | one SQL query instead of ~100 |
| BM25 postings (score only matching docs) | O(N) → O(postings) |
| Query embedding cache | repeated queries skip Ollama |
| Async indexer | write path 78 ms → 1.7 ms for 5 writes |
| Normalised matrix at build time | no per-query row norms |

## End-to-end view

A chat turn is:

```
memory recall (5–30 ms) + LLM generation (300–3000 ms) + transport
```

Memory is usually **1–5%** of the user-visible latency. Optimising it further
has diminishing returns unless the LLM is local and fast.

## Summarizer (optional)

| Mode | Perceived write | Background |
| --- | ---: | ---: |
| synchronous | ~1.4 s (4 turns, DeepSeek) | — |
| `async_summary=True` | **~0.4 ms** | ~1.4 s per episode |

Summaries run at episode write / consolidation, not per message. They do not
affect recall latency; the gist is ready for the next recall that drains the
worker.

## Knobs

| Knob | Default | Effect |
| --- | --- | --- |
| `Profile.candidate_limit` | 50 | fewer candidates = faster rerank |
| `Profile.max_chars` | 2000 | prompt size, not CPU |
| `budget` | 6 | fewer final memories |
| `Profile.query_cache_size` | 128 | repeated-query hit rate |
| `Embedder` | bge-m3 | bge-small / 512d ≈ 2–3× faster |
| `eval.bench --embedder none` | — | 4.4 ms @1k, keyword only |

## Scaling

Vector search is linear in N (one dot product per memory). With NumPy:

- 1k ≈ 5 ms search + 11 ms embedding
- 10k ≈ 50 ms search + 11 ms embedding (estimate)
- 100k ≈ 500 ms → use FAISS

BM25 uses postings, so it scales with the number of matching documents rather
than the whole corpus. `tick` is O(N) per calendar day; 10k ≈ 0.6 s for a
30-day catch-up (estimated, one-time).

## Reproduce

```bash
python -m eval.bench --n 1000 --embedder none
python -m eval.bench --n 1000 --embedder hashing
python -m eval.bench --n 1000 --embedder local
```

For the 1 000-memory bge number, `eval.bench --embedder local` writes
synchronously; the 20.4 ms figure above comes from a file-backed store with
`async_index=True` and distinct queries.
