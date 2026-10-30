# Hippocampus · 海马

> A memory kernel that forgets like a human: important things blur slowly,
> trivia fades, and recall strengthens what it touches.

Most memory libraries optimise for **recall**. This one optimises for **forgetting
correctly** — because for a companion, support bot or idol, remembering every
detail verbatim is not "smart", it is uncanny.

```
L0  verbatim      "3月2日她说数学考砸了，哭了很久"
L1  summary       "三月她数学考砸过，情绪很低"
L2  impression    "她数学不太好"
L3  residue       "隐约记得上个月那阵子你状态不太一样，细节有点模糊。"
L4  archived      not volunteered, still reachable by keyword
```

---

## Status

**v0.0.1 — P0 kernel.** Runnable, tested, benchmarked. Not production ready.

| | |
|---|---|
| Kernel (remember / recall / tick / consolidate / forget) | ✅ |
| FSRS-shaped plasticity + importance-modulated decay | ✅ |
| Five-level graded forgetting with an importance floor | ✅ |
| Rehearsal merge (re-telling strengthens, does not duplicate) | ✅ |
| Mention cooldown (anti-repetition) | ✅ |
| 30-day replay benchmark | ✅ |
| Semantic distillation (`涟漪` with an LLM) | ❌ v1 |
| Embedding retrieval | ❌ v1 (bigram index for now) |
| Async / HTTP service | ❌ v1 |

## Install

```bash
git clone git@github.com:yushui2022/Hippocampus.git
cd Hippocampus
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

No runtime dependencies. Python 3.9+.

## Quickstart

```python
from datetime import datetime, timedelta, timezone
from hippocampus import Hippocampus, Event

hippo = Hippocampus(scope="user_42", profile="companion", path="memory.db")

hippo.remember(Event(text="我数学考砸了，哭了好久", at=datetime(2026, 3, 2, tzinfo=timezone.utc)))

hits = hippo.recall("最近有点学不动了")
for hit in hits:
    print(hit.level, hit.text)

hippo.tick(now=datetime(2026, 4, 1, tzinfo=timezone.utc))  # advance the clock
```

### The one thing to get right

Do **not** paste memory text into a prompt flatly. Use `level` to pick the tone:

```python
for hit in hits:
    if hit.level <= 1:
        prompt += f"你记得：{hit.text}\n"
    else:
        prompt += f"你隐约记得：{hit.text}\n"
```

This single branch is what separates "companion" from "database".

## API

| Method | Notes |
|---|---|
| `remember(event)` | Returns the `Engram`. Near-identical text is treated as rehearsal. |
| `recall(query, budget=6)` | Returns `RecallHit` with `score`, `level`, `refs`. Reinforces by default. |
| `tick(now, active=True)` | Advances the clock. Idempotent and replayable. **First call only sets the baseline.** |
| `consolidate()` | Nightly pass: settle levels, fill gists. |
| `forget(id, hard=True)` | `hard=True` deletes rows, tokens and logs. |

### Why `tick()` instead of reading the wall clock

Virtual time gives replayable benchmarks and deterministic tests:

```python
for day in range(30):
    hippo.tick(now=start + timedelta(days=day))
```

### Why active time, not wall time

```python
hippo.tick(now=..., active=False)  # idle: ages at 5% speed
```

If someone disappears for three months, she should not forget them completely.
That is a product requirement, not an optimisation.

## Profiles

The kernel never branches on a domain. Domains are profiles.

| Profile | Levels | Tuned for |
|---|---|---|
| `companion` | 5 | emotional residue matters |
| `support` | 3 | facts + repeated complaints |
| `education` | 4 | novelty, no emotional residue |
| `knowledge` | 2 | almost no forgetting |

```python
Hippocampus(profile="support")
```

## Benchmark

```bash
python -m eval.replay_30d
python -m eval.replay_30d --days 30 --seed 7 --budget 6 --json out.json
```

Synthetic 30-day chat log (283 turns, 50 planted facts) in three groups:
15 must be recalled, 15 should blur, 20 must not resurface. No API key needed.

Baseline (`--seed 7`, `companion`):

| Metric | Value | Target |
|---|---|---|
| `recall@6` (clarity ≤ L1) | **0.733** | ≥ 0.85 |
| `fog_accuracy` (blurred, not lost) | **1.000** | ≥ 0.60 |
| `fog_missing` | 0.067 | lower better |
| `over_recall` (uncanny valley) | **0.000** | ≤ 0.15 |
| recall latency p50 | ~0.1 ms | — |

**This is a retrieval-level benchmark, not answer accuracy.** And v0 misses its
recall target by 12 points on purpose: the parameters are fitted to a hand-written
set, and chasing 0.85 there would be overfitting. The next real step is an LLM
importance scorer plus a holdout from real logs.

The interesting line is `over_recall = 0.000` with `fog_accuracy = 1.000`:
trivia stops being volunteered while the mid-importance memories stay reachable
in a visibly degraded form. That is the whole thesis of the project.

## Design decisions

- **FSRS shape, not `exp(-t/τ)`.** Exponential decay can only say "older is
  fainter". FSRS also says "the harder a recall was, the stronger it gets".
  Calibration is shifted (`S_MIN = 0.02` days) because a chat agent sees a
  memory once, not in review sessions.
- **Importance sets a floor, not just a rate.** `I ≥ 0.8` can blur to L2 but
  never to "it didn't happen".
- **Recall sharpens one level, never fully restores.** No perfect reconstructions.
- **Rehearsal merges.** Re-telling the same event strengthens one trace instead
  of creating five copies that trivially survive.
- **Cooldown penalty.** Without it, an agent mentions the same memory three
  times in twenty minutes and the illusion dies.
- **No domain words in `hippocampus/`.** If you need `if profile == "idol"`,
  the abstraction is wrong.

## Layout

```
hippocampus/
  models.py    Event, Engram, RecallHit, level rendering
  decay.py     FSRS-shaped retrievability, reinforcement, floors
  scoring.py   importance features, retrieval ranking
  tokens.py    CJK bigrams + latin words
  store.py     SQLite persistence
  kernel.py    the five public methods
  profiles.py  domain configuration
eval/
  synth.py       30-day synthetic log + ground truth
  replay_30d.py  benchmark CLI
tests/
  test_kernel.py 14 tests, no network
```

## Roadmap

| | |
|---|---|
| P0 ✅ | kernel + benchmark |
| P1 | LLM importance scorer, semantic distillation, embedding retrieval |
| P2 | three profiles pass the benchmark; forgetting benchmark published |
| P3 | IM adapter, HTTP service |
| P4 | live-stream adapter (danmaku, avatars) |

## Naming

A nod to the hippocampus: pattern separation (dentate gyrus), pattern completion
(CA3), emotional weighting (amygdala), and sleep replay (sharp-wave ripples) all
have direct counterparts here. Not a metaphor — a functional mapping.

Not to be confused with [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG),
which is a RAG framework.

## License

MIT
