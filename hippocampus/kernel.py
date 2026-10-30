"""The kernel.

Public surface is deliberately tiny:

    remember(event)      -> Engram
    recall(query)        -> [RecallHit]
    tick(now)            -> None      (idempotent, replayable)
    consolidate()        -> dict
    forget(id, hard)     -> bool

Nothing in this file may mention a product concept (idol, danmaku, ticket).
If you need that, add a Profile or an adapter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from . import decay
from .models import Engram, Event, RecallHit, new_id, utcnow
from .profiles import Profile, get_profile
from .scoring import (
    RetrievalSignals,
    detect_emotion,
    recency_factor,
    retrieval_score,
    score_importance,
    similarity,
)
from .store import Store
from .tokens import first_clause, tokenize


class Hippocampus:
    def __init__(
        self,
        scope: str = "default",
        profile: str = "companion",
        path: str = ":memory:",
        *,
        store: Optional[Store] = None,
        llm: Optional[Callable[[str], Any]] = None,
        summarizer: Optional[Callable[[str], str]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.scope = scope
        self.profile: Profile = get_profile(profile) if isinstance(profile, str) else profile
        self.store = store or Store(path)
        self._owns_store = store is None
        self._llm = llm
        self._summarizer = summarizer
        self._clock = clock or utcnow

        state = self.store.get_state(scope)
        state.setdefault("profile", self.profile.name)
        state.setdefault("relationship", {})
        self._state = state

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "Hippocampus":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def max_level(self) -> int:
        return self.profile.max_level

    def now(self) -> datetime:
        return _aware(self._clock())

    # -- 1. remember ---------------------------------------------------------
    def remember(
        self,
        event: Event,
        *,
        importance: Optional[float] = None,
        kind: str = "episodic",
    ) -> Engram:
        at = _aware(event.resolved_at())
        tokens = tokenize(event.text)

        novelty = 1.0 - self.store.max_similarity(self.scope, tokens)
        existing, sim = self.store.best_match(self.scope, tokens)
        is_retelling = (
            existing is not None
            and sim >= self.profile.merge_threshold
            and existing.meta.get("actor", "user") == event.actor
            and existing.kind == kind
        )

        # Repeats of the same trace are collapsed by `_rehearse`, so counting
        # similar rows alone under-counts rehearsal. Use the trace's own tally.
        if is_retelling:
            repeat_count = int(existing.meta.get("mentions", 1)) + 1
        else:
            repeat_count = self.store.count_similar(self.scope, tokens) + 1
        repetition = min(1.0, repeat_count / 4.0)

        llm_score = None
        if self._llm is not None:
            llm_score = _safe_float(self._llm(event.text))

        report = score_importance(
            event.text,
            tokens,
            self.profile.importance_weights,
            self.profile.weight_sum(),
            novelty=novelty,
            repetition=repetition,
            llm_score=llm_score,
        )
        score = report.value if importance is None else _clamp(importance)

        # Re-telling vs. new event: near-identical text is rehearsal, so it
        # strengthens the existing trace instead of creating a duplicate.
        if is_retelling:
            return self._rehearse(existing, event, at, score, report, tokens)

        engram = Engram(
            engram_id=new_id(),
            scope=self.scope,
            kind=kind,
            raw_text=event.text,
            gist=self._gist(event.text),
            emotion=detect_emotion(event.text),
            level=0,
            importance=score,
            stability=decay.initial_stability(score),
            difficulty=5.0,
            active_age_days=0.0,
            created_at=at,
            refs=list(event.meta.get("refs", [])),
            tokens=tokens,
            meta={
                "actor": event.actor,
                "scene": event.scene,
                "features": report.features,
                **{k: v for k, v in event.meta.items() if k != "refs"},
            },
        )
        self.store.insert(engram)
        return engram

    # -- 2. recall -----------------------------------------------------------
    def recall(
        self,
        query: str = "",
        *,
        budget: int = 6,
        kinds: Optional[Iterable[str]] = None,
        now: Optional[datetime] = None,
        reinforce: bool = True,
    ) -> List[RecallHit]:
        at = _aware(now) if now else self.now()
        query_tokens = tokenize(query)

        candidate_ids = self.store.candidates(
            self.scope, sorted(query_tokens), self.max_level, limit=50
        )
        if not candidate_ids:
            return []

        wanted = set(kinds) if kinds else None
        query_emotion = detect_emotion(query)

        scored: List[RecallHit] = []
        for engram in (self.store.get(i) for i in candidate_ids):
            if engram is None:
                continue
            if wanted and engram.kind not in wanted:
                continue

            r = decay.retrievability(engram.active_age_days, engram.stability)
            signals = RetrievalSignals(
                similarity=similarity(query_tokens, engram.tokens),
                retrievability=r,
                importance=engram.importance,
                recency=recency_factor(engram.created_at, at),
                relationship=self._relationship_boost(engram),
                emotion=1.0 if (query_emotion and engram.emotion == query_emotion) else 0.0,
                cooldown=self._cooldown(engram, at),
            )
            score = retrieval_score(signals, self.profile.retrieval_weights)
            scored.append(
                RecallHit(engram=engram, score=score, text=engram.render(now=at))
            )

        scored.sort(key=lambda h: h.score, reverse=True)
        hits = scored[: max(0, budget)]

        if reinforce:
            for hit in hits:
                self._reinforce(hit.engram, at)
            for hit in hits:
                self.store.update(hit.engram)
                self.store.log_recall(self.scope, hit.engram.engram_id, query, at)

        return hits

    # -- 3. time -------------------------------------------------------------
    def tick(self, now: Optional[datetime] = None, *, active: bool = True) -> None:
        """Advance the clock. Idempotent for a given `now`."""
        at = _aware(now) if now else self.now()
        last = _parse_state_time(self._state.get("last_tick"))

        if last is None:
            self._state["last_tick"] = at.isoformat()
            self.store.set_state(self.scope, self.profile.name, self._state)
            return

        delta_days = max(0.0, (at - last).total_seconds() / 86400.0)
        if delta_days <= 0:
            return

        factor = 1.0 if active else self.profile.idle_ratio
        aged: List[Engram] = []
        for engram in self.store.list(self.scope):
            # A trace cannot be older than its own existence, otherwise a
            # memory created on day 29 would be aged a full day on day 29.
            cap = max(
                0.0, (at - _aware(engram.created_at)).total_seconds() / 86400.0
            ) * factor
            engram.active_age_days = min(
                engram.active_age_days + delta_days * factor, cap
            )
            r = decay.retrievability(engram.active_age_days, engram.stability)
            target = decay.target_level(r, engram.importance, self.max_level)
            # Forgetting is one-way on tick: a memory does not un-forget itself
            # just because it has been left alone.
            engram.level = max(engram.level, target)
            if engram.level >= 1 and not engram.gist:
                engram.gist = self._gist(engram.raw_text)
            aged.append(engram)

        self.store.update_many(aged)
        self._state["last_tick"] = at.isoformat()
        self.store.set_state(self.scope, self.profile.name, self._state)

    # -- 4. consolidate ("ripple") -------------------------------------------
    def consolidate(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Nightly pass: settle levels, fill gists, record the shape of memory.

        Semantic distillation is intentionally left to an LLM hook in v1 --
        faking it with templates produced garbage facts, so we don't.
        """
        at = _aware(now) if now else self.now()
        changed = 0
        levels: Dict[int, int] = {}

        for engram in self.store.list(self.scope):
            r = decay.retrievability(engram.active_age_days, engram.stability)
            target = decay.target_level(r, engram.importance, self.max_level)
            if target > engram.level:
                engram.level = target
                changed += 1
            if engram.level >= 1 and not engram.gist:
                engram.gist = self._gist(engram.raw_text)
                changed += 1
            levels[engram.level] = levels.get(engram.level, 0) + 1
            self.store.update(engram)

        self._state["last_consolidated"] = at.isoformat()
        self.store.set_state(self.scope, self.profile.name, self._state)
        return {
            "scope": self.scope,
            "changed": changed,
            "levels": dict(sorted(levels.items())),
            "total": sum(levels.values()),
        }

    # -- 5. forget -----------------------------------------------------------
    def forget(self, engram_id: str, *, hard: bool = True) -> bool:
        """Hard delete is the only thing that satisfies 'please forget this'."""
        if self.store.get(engram_id) is None:
            return False
        if hard:
            self.store.delete(engram_id)
        else:
            engram = self.store.get(engram_id)  # type: ignore[assignment]
            engram.level = self.max_level
            engram.raw_text = ""
            engram.gist = ""
            self.store.update(engram)
        return True

    # -- helpers -------------------------------------------------------------
    def relationship(self) -> Dict[str, Any]:
        return dict(self._state.get("relationship", {}))

    def set_relationship(self, **kwargs: Any) -> Dict[str, Any]:
        rel = dict(self._state.get("relationship", {}))
        rel.update(kwargs)
        self._state["relationship"] = rel
        self.store.set_state(self.scope, self.profile.name, self._state)
        return rel

    def count(self) -> int:
        return self.store.count(self.scope, self.max_level)

    def stats(self) -> Dict[str, Any]:
        engrams = self.store.list(self.scope)
        buckets: Dict[int, int] = {}
        for e in engrams:
            buckets[e.level] = buckets.get(e.level, 0) + 1
        return {
            "scope": self.scope,
            "profile": self.profile.name,
            "total": len(engrams),
            "levels": dict(sorted(buckets.items())),
            "mean_importance": round(
                sum(e.importance for e in engrams) / len(engrams), 3
            )
            if engrams
            else 0.0,
        }

    def _rehearse(
        self,
        engram: Engram,
        event: Event,
        at: datetime,
        score: float,
        report: Any,
        tokens: Set[str],
    ) -> Engram:
        """Same event told again: strengthen, do not duplicate."""
        r = decay.retrievability(engram.active_age_days, engram.stability)
        engram.importance = max(engram.importance, score)
        engram.stability = decay.reinforce(engram.stability, r, engram.importance)
        engram.active_age_days = 0.0
        engram.last_mention_at = at
        if len(event.text) > len(engram.raw_text):
            engram.raw_text = event.text
        if not engram.gist:
            engram.gist = self._gist(engram.raw_text)
        for ref in event.meta.get("refs", []):
            if ref not in engram.refs:
                engram.refs.append(ref)
        engram.tokens |= tokens
        engram.meta["mentions"] = int(engram.meta.get("mentions", 1)) + 1
        engram.meta["features"] = report.features
        engram.meta.update({k: v for k, v in event.meta.items() if k != "refs"})
        self.store.update(engram)
        self.store.add_tokens(engram.engram_id, tokens)
        return engram

    def _reinforce(self, engram: Engram, at: datetime) -> None:
        r = decay.retrievability(engram.active_age_days, engram.stability)
        engram.stability = decay.reinforce(engram.stability, r, engram.importance)
        engram.active_age_days = 0.0
        engram.recall_count += 1
        engram.last_recall_at = at
        engram.last_mention_at = at
        # Recall sharpens one level, never fully restores in a single hit.
        engram.level = decay.level_down(engram.level, 1)

    def _cooldown(self, engram: Engram, at: datetime) -> float:
        if not engram.last_mention_at:
            return 0.0
        hours = (at - _aware(engram.last_mention_at)).total_seconds() / 3600.0
        return 1.0 if hours < self.profile.cooldown_hours else 0.0

    def _relationship_boost(self, engram: Engram) -> float:
        if not self.profile.enable_relationship:
            return 0.0
        rel = self._state.get("relationship", {}) or {}
        if not rel:
            return 0.0
        intimacy = float(rel.get("intimacy", 0.0) or 0.0)
        # Emotional memories surface more easily in close relationships.
        return min(1.0, intimacy * (0.5 + engram.importance))

    def _gist(self, text: str) -> str:
        if self._summarizer is not None:
            try:
                return str(self._summarizer(text))
            except Exception:
                pass
        return first_clause(text)


# -- small utilities ---------------------------------------------------------
def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return _clamp(float(value))
    except (TypeError, ValueError):
        return None


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_state_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None
