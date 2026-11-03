# Invariants

These are the rules the kernel must never break. Every one has a test in
`tests/test_v2.py`; a PR that weakens a test instead of fixing code should be
rejected.

| # | Invariant | Why | Test |
| --- | --- | --- | --- |
| 1 | `level >= fidelity` | A trace cannot claim more clarity than it stored | `test_l1_recall_does_not_restore_raw` |
| 2 | `fidelity` never decreases under forgetting or recall | Lost detail must not come back just because you looked | `test_fidelity_monotonic` |
| 3 | `stability <= max_stability` | Even important memories must be able to blur | `test_stability_cap` |
| 4 | `tick(now)` is idempotent | Replay and tests depend on it | `test_tick_is_idempotent` |
| 5 | Unknown time spans default to idle | A cron outage must not age the user | `test_tick_catchup_uses_idle` |
| 6 | `peek()` / `salient()` never write | Looking at memory must not change it | `test_peek_is_read_only` |
| 7 | `recall()` does not write `last_mention_at` | Remembering is not saying | `test_mention_is_separate_from_recall` |
| 8 | `recall(reinforce=False)` never changes `stability` | Measurement must not pollute state | `test_default_recall_does_not_reinforce` |
| 9 | Every fact has `source_engram` | Hallucinations must be traceable | `test_new_value_supersedes_old` |
| 10 | Assistant text never creates facts | Stops self-pollution feedback loops | `test_assistant_text_never_creates_facts` |
| 11 | Hard delete removes every derived row | Privacy must be real | `test_delete_cascades` |

## Notes on the tricky ones

### `fidelity` vs `level`

One deliberate exception: an explicit **re-telling** by the user is new
evidence, not recall. `_rehearse()` may lower `fidelity` by one level because
the detail was supplied again. `test_retelling_recovers_one_level` covers it.
Recall itself can never do this.

`level` answers "how clearly does this surface *now*". `fidelity` answers
"how much detail is still stored". They move in the same direction when
forgetting, but recall only moves `level`, never `fidelity`:

```python
# remember -> L0, fidelity 0
# 120 days -> L1, fidelity 1
# recall   -> L1, still fidelity 1 (NOT L0)
```

Without this split, one recall of a half-forgotten memory would return the raw
sentence, which breaks both S3 ("remember, but vaguely") and S5 ("admit
uncertainty").

### `tick` and idle time

The contract is *calendar-day based*:

```python
tick(now, active_days={...})
```

- A day in `active_days` ages by `1.0`.
- Any other day ages by `Profile.idle_ratio` (companion: `0.05`).
- `active=True` applies to the current day only; it does **not** retroactively
  mark the whole gap active.
- Gaps longer than 366 days are handled analytically to keep `tick` O(1) in
  the gap.

### Mention budget

`salient()` respects `Profile.mention_budget_per_day`. `mark_mentioned()` is
the only writer of "she actually said this"; `recall()` writes audit rows with
`surfaced=0`. This keeps "how often does she bring it up" measurable and
prevents the same memory from being volunteered three times in an hour.

### Facts and the actor boundary

`RuleFactExtractor` checks `engram.actor`, which is read from
`meta["actor"]`. `remember()` stores it from `Event.actor`. An adapter should
set `actor="assistant"` for model output; those events can be remembered but
never produce facts.

This is a deliberate answer to a real failure mode observed in production
memory plugins: a summarizer mis-writes a fact, the fact is recalled, and the
model then acts on its own mistake.

## Changing an invariant

Don't. Add a profile flag or a new API instead. If an invariant truly must
change, update this file, the test and the changelog in the same PR, and
explain the user-visible consequence.

---

# v3 invariants (12–18)

These sit above the v2 invariants. v2 still owns decay, fidelity and rendering;
v3 owns what enters memory, where it came from, and whether it may be spoken.

| # | Invariant | Test |
|---|---|---|
| 12 | `Brain.observe` never calls an LLM | `test_observe_never_calls_llm` |
| 13 | A dropped buffer row leaves no trace after the TTL | `test_dropped_buffer_visible_then_gone_after_ttl` |
| 14 | Assistant lines never create facts, card notes or self-model entries | `test_assistant_lines_never_create_facts_or_notes` |
| 15 | A private memory is never recalled in front of outsiders | `test_private_memory_not_recalled_in_group` |
| 16 | `context()` / `peek()` write nothing | `test_context_is_read_only` |
| 17 | Reflection does not call the LLM again with no new memories | `test_reflection_does_not_loop` |
| 18 | `maintain(now)` is idempotent for the same clock | `test_maintain_is_idempotent` |

### Why 12 matters

`observe` is the hot path: one message, one SQLite insert, no network. If the
gate or the staging write ever needs a model call, every message inherits the
model's latency and failure modes.

### Why 14 matters

An engam can contain assistant text (for context), but facts and notes are
derived only from `actor=user` turns. This is the fix for the classic failure:
the model writes a summary, the summary becomes a fact, and the model recites
its own mistake back as a memory.

### Why 15 matters

The v2 adapter enforced privacy with a prompt line ("don't reveal DMs"). v3
filters at recall: a private source's allowed listeners are its speakers and
witnesses, and the current audience must be a subset. The model never sees the
memory in the first place.

### Why 17 matters

The v2 self-model used `reflected_ids[:200]`. A busy week silently dropped old
ids, so "unreflected" memories could be reflected twice or never. v3 stores a
timestamp waterline (`reflected_until`) plus evidence counts, and reflection
only runs when the pressure and time thresholds are met.
