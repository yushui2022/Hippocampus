# v3 status

Snapshot: 2026-11-03, branch `v3.0`, version `0.9.0`.

This maps the v3 execution plan (`执行-海马记忆系统 v3（Claude 版）`) to what
actually exists in the repository. v2 is untouched and still works; v3 is a
layer above it (`Brain`), not a rewrite.

## Done — P1

| # | Task | Where | Test |
|---|---|---|---|
| 1 | schema v6: `staging`, `entities` | `hippocampus/store.py` | `test_schema_v6_is_idempotent` |
| 2 | sensory gate | `hippocampus/sensory.py` | `test_filler_and_noise_are_dropped`, `test_addressed_is_always_kept` |
| 3 | `Utterance` + `Brain.observe` (attention, familiarity, staging) | `hippocampus/brain.py` | `test_observe_never_calls_llm`, `test_familiarity_rises_even_when_text_is_dropped` |
| 4 | sleep consolidation (trigger, split, salience, promote/drop, facts, cards, LLM validation + rule fallback) | `hippocampus/consolidation.py` | `test_big_event_is_promoted_and_small_is_dropped`, `test_llm_bad_json_falls_back_to_rules`, `test_assistant_lines_never_create_facts_or_notes` |
| 5 | source rules + `recall(where, boost)` | `hippocampus/source.py`, `hippocampus/kernel.py` | `test_speakability_matrix`, `test_private_memory_not_recalled_in_group` |
| 6 | L1 staging recall | `Brain._buffer_hits` | `test_dropped_buffer_visible_then_gone_after_ttl` |
| 7 | SelfModel waterline + evidence gate + no `user_facts` generation | `hippocampus/self_model.py` | `test_reflection_does_not_loop` |
| 8 | NapCat adapter uses `Brain` | `integrations/napcat/` | 14 adapter tests |
| 9 | `eval/forgetting_v3.py` | `eval/` | measured below |

## Measured (rule fallback, no API key)

```
metric             value  target
big_recall@30d       1.0  >= 0.9
small_recall@1d      1.0  >= 0.6
small_recall@7d      0.0  <= 0.2
noise_stored           0  == 0
privacy_leak         0.0  == 0
PASS
```

Promoted 11, dropped 6, 11 engrams, 3 entity cards over 35 simulated days.

`false_memory` is not measured here: it needs an LLM judge, so the rule
benchmark reports `None` rather than a fake zero.

## Deliberate deviation from the spec text

Section 7.3 says L1 recall covers `pending` + `promoted`. The benchmark
targets (`small_recall@1d >= 0.6`, `small_recall@7d <= 0.2`) only hold if a
**dropped** row stays readable until the 72-hour TTL, then disappears — that
is also the more human behaviour ("I remember you said that yesterday, but
not last week"). Implementation: L1 reads every staging row inside the TTL;
TTL expiry deletes the row. Invariant 13 is unchanged.

Section 7.1 says "addressed is always kept". In a DM every line is technically
addressed, which would make filler filtering useless in DMs. Adapters should
set `addressed=True` only for an explicit call (name, @, reply-to), not for
"this is a private chat"; `Brain` already gives dyads full attention. The QQ
adapter follows that rule.

## Not done — P2 / P3

- L3 life lines (open / closed threads)
- merging similar memories into regularities
- A-Mem-style retroactive promotion when a small thing is mentioned again
- `memory_search` as a model tool
- proactive mention driven by open threads (still probabilistic)
- feedback: used memory reinforced, corrected memory revised
- S6 emotional carry-over
- real-log holdout + human blind test
- live stage (browser, dafeiyu-live)

## Operational notes

- One brain per bot: `self:taozi`.
- Contexts: `qq:dm:<uid>`, `qq:group:<gid>`.
- New memories go to the `self:taozi` scope. Legacy `qq:*` / `napcat:*` rows
  stay in the same SQLite file and are simply not read by `Brain`.
- `MEMORY_DB` unchanged (`/data/hippocampus/memory.db`); schema migration to v6
  is in place and idempotent.
