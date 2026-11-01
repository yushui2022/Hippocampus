# Hippocampus · 海马

> A memory kernel that forgets like a human: important things blur slowly,
> trivia fades, recall strengthens what it touches — and a half-remembered
> thing never comes back verbatim.

Most memory libraries optimise for **recall**. This one optimises for
**forgetting correctly** — because for a companion, a support desk, or any
long-running assistant, remembering every detail verbatim is not "smart",
it is uncanny.

```
L0  verbatim      "3月2日她说数学考砸了，哭了很久"
L1  summary       "三月她数学考砸过，情绪很低"
L2  impression    "她数学不太好"
L3  residue       "隐约记得上个月那阵子你状态不太一样，细节有点模糊。"
L4  archived      not volunteered, still reachable by keyword
```

---

## Status

**v0.2.0 — v2 core.** Runnable, tested, benchmarked. Not production ready.

| | |
|---|---|
| Kernel (`remember` / `recall` / `tick` / `consolidate` / `forget`) | ✅ |
| FSRS-shaped plasticity + importance-modulated decay | ✅ |
| Five-level graded forgetting + fidelity floor | ✅ |
| Rehearsal merge (re-telling strengthens, does not duplicate) | ✅ |
| Mention cooldown + proactive/vs-passive recall | ✅ |
| Read-only `peek()` (never strengthens, never logs) | ✅ |
| Hybrid retrieval: BM25 + vectors + RRF + MMR | ✅ |
| Episode builder (turns → episodes) | ✅ |
| Fine-grained facts with TTL / supersede / source | ✅ |
| Read-only inspection + backup | ✅ |
| Real embedding provider (bge-m3 via OpenAI-compatible API) | ✅ adapter |
| Graph memory | ❌ later |
| Async write / HTTP service | ❌ later |

### What changed in v2

- **`fidelity` is separate from `level`.** Recall can improve accessibility;
  it can never restore detail that graded forgetting discarded.
- **`tick()` is day-based.** A 90-day catch-up no longer ages memories as if
  the user had talked every day.
- **`recall(reinforce=False)` is the default**, and `peek()` never writes.
- **`mark_mentioned()` is separate from recall** — remembering is not saying.
- **Facts only come from user utterances**, with a mandatory source.
- **Stability is capped** (`Profile.max_stability`), so nothing becomes
  permanently unforgettable.
- **Hybrid retrieval** replaced raw token-hit counting.
- **Episodes** prevent 283 turns from becoming 283 separate traces.

---

## Quickstart

```bash
pip install -e .
python -m unittest discover -s tests -p 'test_*.py'
python -m eval.replay_30d
```

```python
from datetime import datetime, timedelta, timezone
from hippocampus import Event, Hippocampus, HashingEmbedder

T0 = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)
hippo = Hippocampus(
    scope="user_42",
    profile="companion",
    path="memory.db",
    embedder=HashingEmbedder(),   # replace with OpenAIEmbedder for real semantics
)

hippo.remember(Event("我数学考砸了，哭了好久", at=T0))
hippo.remember(Event("我不吃香菜", at=T0))

# one day at a time, with the day marked active
for day in range(1, 31):
    hippo.tick(
        T0 + timedelta(days=day),
        active=True,
        active_days={T0.date() + timedelta(days=day - 1), T0.date() + timedelta(days=day)},
    )

for hit in hippo.recall("最近有点学不动了", budget=5):
    print("L{}  {:.2f}  {}".format(hit.level, hit.score, hit.text))

for fact in hippo.facts():
    print("fact:", fact.kind, fact.predicate, fact.obj)
```

### Real semantics (bge-m3)

Local, no API key, no text leaving the machine:

```python
from hippocampus import LocalEmbedder

embedder = LocalEmbedder()   # http://127.0.0.1:11434/v1, model=bge-m3
```

Cloud (any OpenAI-compatible endpoint):

```python
from hippocampus import OpenAIEmbedder

embedder = OpenAIEmbedder(
    api_key="sk-...",
    model="BAAI/bge-m3",
    base_url="https://api.siliconflow.cn/v1",
    dim=1024,
)
```

Setup: `ollama pull bge-m3 && ollama serve`, then see
[`docs/LOCAL_EMBEDDING.md`](docs/LOCAL_EMBEDDING.md). The core has **no**
required third-party dependency; NumPy is used automatically when present.

---

## Architecture

```
                 ┌──────────────────────────────────────┐
   caller ──────▶│ kernel.py  remember / recall / tick  │
                 └───────────────┬──────────────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
   scoring.py               decay.py                  models.py
   importance / rank        R / S / fidelity           types + render
        │                        │                         │
        └────────────┬───────────┴────────────┬────────────┘
                     ▼                        ▼
              retrieval/                  facts.py
              BM25 · vectors · RRF · MMR   extract / TTL / supersede
                     │                        │
                     └──────────┬─────────────┘
                                ▼
                             store.py
                  SQLite · tokens · vectors · facts · meta
```

### Retrieval

```
query ─┬─ BM25 top-50 ─┐
       └─ ANN top-50 ──┴─▶ RRF ─▶ Hippocampus rerank
                               (relevance / R / I / recency /
                                emotion / relationship / cooldown)
                             ─▶ MMR ─▶ budget + char cap
```

- BM25 keeps names, places and numbers exact.
- Vectors recover paraphrases ("学不动了" → "数学考砸了").
- RRF fuses rank lists without calibrating scores.
- MMR stops six near-duplicate memories from filling the prompt.
- The char cap (default 2000) keeps prompts bounded.

### Forgetting

```
R(t, S) = (1 + 19/81 · t / S) ^ -0.5          t = active_age_days
S'      = min(S_max, S · (1 + 2 · (1-R) · (0.5+I)))
```

- `active_age` advances only inside `tick()`, and idle days use
  `Profile.idle_ratio` (companion: `0.05`).
- `fidelity` is monotonic: detail only disappears.
- `level >= fidelity` always holds; recall can lower `level` at most to
  `fidelity`.
- Importance floors: `I ≥ 0.8` never goes below L2; `I ≥ 0.5` never below L3.

### Facts

Only `Event(actor="user")` produces facts. Assistant output never does.
Each fact carries:

- `kind`: `fact | preference | plan | relation`
- typed TTL (`plan 2d`, `preference 60d`, `relation 90d`, `fact 180d`)
- `valid_from` / `valid_to`
- `source_engram` and `source_refs`

A new value for the same `(subject, predicate)` supersedes the old one; the
history stays queryable with `include_inactive=True`.

### Proactive speech

`salient()` answers "what is worth bringing up?" without a query:

- only L0/L1 (clear memories are volunteered; fuzzy ones are not)
- negative memories are suppressed unless `intimacy ≥ 0.7`
- mention cooldown is a hard filter
- a daily budget (`mention_budget_per_day`, default 3)

`mark_mentioned()` records what was actually said. `recall()` does not.

---

## API

| Method | Writes? | Purpose |
| --- | --- | --- |
| `remember(event)` | ✅ | store one event |
| `remember_episode(events)` | ✅ | store a whole episode |
| `feed_episode(event)` | ✅ | buffer turns, write closed episodes |
| `recall(query, ...)` | optional | retrieve; `reinforce=False` by default |
| `peek(query)` | ❌ | read-only retrieval |
| `salient()` | ❌ | proactive candidates |
| `tick(now, active_days=...)` | ✅ | advance time |
| `consolidate()` | ✅ | settle levels / gists / facts |
| `correct(id, ...)` | ✅ | fix text / importance / refs |
| `set_importance(id, v, force=True)` | ✅ | explicit override |
| `mark_mentioned(ids)` | ✅ | record what was said |
| `forget(id, hard=...)` | ✅ | soft or hard delete |
| `facts(...)` | ❌ | active facts |
| `stats()` | ❌ | levels, fidelity, stability, facts |

Full signatures live in `hippocampus/kernel.py`.

---

## Evaluation

```bash
python -m eval.replay_30d                 # token baseline (no embedder)
python -m eval.replay_30d --embedder hashing
python -m eval.paraphrase --embedder lexicon
python -m eval.paraphrase --embedder openai   # HIPPO_EMBED_KEY=...
python -m eval.bench --n 500
python -m eval.false_memory
```

Current numbers on the 283-turn synthetic replay (seed 7, budget 6):

| Embedder | recall@6 | fog_accuracy | fog_missing | over_recall |
| --- | --- | --- | --- | --- |
| none (token baseline) | 0.933 | 1.000 | 0.000 | 0.100 |
| hashing (token vectors) | 1.000 | 1.000 | 0.267 | 0.000 |
| **local bge-m3 (Ollama)** | 1.000 | 1.000 | 0.000 | 0.100 |

`para_recall@6`: 0.600 with hashing, **1.000 with local bge-m3** (10/10).

bge-m3's clear win is paraphrase recall (0.600 → 1.000) and fuzzy recall
(`fog_missing` 0.267 → 0.000). The `remember` gain comes from importance
calibration (`importance_gain = 14`) plus one-level recovery on an explicit
re-telling. See `docs/LOCAL_EMBEDDING.md` for the full table and caveats.

> The benchmark is retrieval-level, not answer accuracy. A separate blind test
> (plastic memory vs verbatim memory) is the real acceptance test.

---

## Invariants

1. `level >= fidelity` always.
2. `fidelity` never decreases under forgetting or recall (an explicit re-telling is new evidence and may recover one level).
3. `stability <= Profile.max_stability`.
4. `tick(now)` is idempotent for the same `now`.
5. Unknown time spans default to idle, never to active.
6. `peek()` and `salient()` never write.
7. `recall()` does not set `last_mention_at`.
8. `recall(reinforce=False)` does not change `stability`.
9. Every fact has a `source_engram`.
10. Assistant text never creates a fact.
11. Hard delete removes engram, tokens, vectors, facts and audit rows.

All of these are covered by `tests/test_v2.py`.

---

## Migration

Opening a v0 database automatically adds `fidelity`, `gist_source`,
`last_active_at`, `corrected_at`, the vector/facts tables and the v2 audit
columns. `fidelity` is backfilled from `level`, and `gist_source` is set to
`extractive` when a gist exists.

```
meta.schema_version  0 -> 4
```

Back up before upgrading:

```bash
cp memory.db memory.db.bak
python -c "from hippocampus import Store; Store('memory.db').close()"
```

After changing the embedding model, rebuild vectors:

```bash
python -m hippocampus.rebuild --db memory.db --provider local
```

---

## Layout

```
hippocampus/
  kernel.py          public API
  models.py          Event / Engram / Fact / RecallHit / render
  decay.py           R / S / fidelity / floors
  scoring.py         importance + retrieval signals
  store.py           SQLite, migrations, vectors, facts
  tokens.py          CJK bigrams, BM25 helper
  episode.py         window segmenter
  facts.py           extractor + TTL builder
  retrieval/         embeddings · BM25 · vector index · RRF/MMR
  ops/               read-only snapshot · backup
eval/                replay · paraphrase · bench
tests/               invariants and behaviour
```

---

## Roadmap

- **P1.5** — real bge-m3 baseline, candidate union tuning, rerank provider.
- **P2** — graph memory (feature-flagged), read-only observatory UI, shadow
  index rebuild, mention budget per topic.
- **P3** — IM adapter, async write queue, blind test harness.

See the design notes in `docs/` for the full plan.

---

## License

MIT.
