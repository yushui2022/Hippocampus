"""The kernel.

Public surface (v2):

    remember(event)              -> Engram
    remember_episode(events)     -> Engram
    recall(query)                -> [RecallHit]
    peek(query)                  -> [RecallHit]   (never writes)
    salient()                    -> [RecallHit]   (proactive, no query)
    tick(now)                    -> TickReport
    consolidate()                -> dict
    correct / set_importance / mark_mentioned / forget

Nothing in this file may mention a product concept. If you need one, add a
Profile or an adapter.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import decay
from .episode import Episode, WindowSegmenter
from .indexer import AsyncIndexer
from .facts import (
    FactCandidate,
    FactExtractor,
    NullFactExtractor,
    RuleFactExtractor,
    build_fact,
)
from .models import Engram, Event, Fact, RecallHit, Summary, TickReport, new_id, utcnow
from .profiles import Profile, get_profile
from .retrieval import BM25Index, Embedder, VectorIndex, cosine, mmr, normalize, rrf
from .scoring import (
    NEGATIVE_EMOTIONS,
    RetrievalSignals,
    detect_emotion,
    recency_factor,
    retrieval_score,
    score_importance,
    similarity,
)
from .store import Store
from .tokens import first_clause, jaccard, tokenize


class Hippocampus:
    def __init__(
        self,
        scope: str = "default",
        profile: str = "companion",
        path: str = ":memory:",
        *,
        store: Optional[Store] = None,
        llm: Optional[Callable[[str], Any]] = None,
        summarizer: Optional[Any] = None,
        clock: Optional[Callable[[], datetime]] = None,
        embedder: Optional[Embedder] = None,
        fact_extractor: Optional[FactExtractor] = None,
        auto_index: bool = True,
        auto_facts: bool = True,
        async_index: Optional[bool] = None,
    ) -> None:
        self.scope = scope
        self.profile: Profile = get_profile(profile) if isinstance(profile, str) else profile
        self.store = store or Store(path)
        self._owns_store = store is None
        self._llm = llm
        self._summarizer = summarizer
        self._clock = clock or utcnow
        self._embedder = embedder
        self._auto_index = bool(auto_index and embedder is not None)
        self._auto_facts = bool(auto_facts)
        self._fact_extractor = (
            fact_extractor if fact_extractor is not None else RuleFactExtractor()
        )

        self._vectors = (
            VectorIndex(self.store, embedder.model, embedder.dim)
            if embedder is not None
            else None
        )
        self._bm25 = BM25Index(self.store)
        self._segmenters: Dict[str, WindowSegmenter] = {}
        if async_index is None:
            # In-memory databases stay synchronous so tests and evals are
            # deterministic; file-backed stores get non-blocking writes.
            async_index = embedder is not None and str(self.store.path) != ":memory:"
        self._async_index = bool(async_index and embedder is not None)
        self._indexer: Optional[AsyncIndexer] = None
        if self._async_index and self._vectors is not None:
            self._indexer = AsyncIndexer(self._embedder, self._apply_async_vector)

        state = self.store.get_state(scope)
        state.setdefault("profile", self.profile.name)
        state.setdefault("relationship", {})
        self._state = state

        if self._vectors is not None:
            fingerprint = self._vectors.fingerprint()
            stored = self.store.get_meta("embedder_fingerprint")
            if stored and stored != fingerprint:
                self._state["needs_vector_rebuild"] = True
            else:
                self.store.set_meta("embedder_fingerprint", fingerprint)

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        if self._indexer is not None:
            self._indexer.close()
            self._drain_index()
            self._indexer = None
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

    # -- 1. write ------------------------------------------------------------
    def remember(
        self,
        event: Event,
        *,
        importance: Optional[float] = None,
        kind: str = "episodic",
        summarize: bool = False,
    ) -> Engram:
        at = _aware(event.resolved_at())
        text = (event.text or "").strip()
        tokens = tokenize(text)

        query_vec = self._embed_query(text)
        recent = self.store.recent(self.scope, 300)
        existing, sim, similar_count = _match_recent(recent, tokens)
        if query_vec is not None and self._vectors is not None:
            recent_ids = {e.engram_id for e in recent}
            vec_hits = self._vectors.search(
                self.scope, query_vec, k=1, allowed=recent_ids
            )
            if vec_hits and vec_hits[0][1] > sim:
                candidate = self.store.get(vec_hits[0][0])
                if candidate is not None:
                    existing, sim = candidate, vec_hits[0][1]
        novelty = 1.0 - sim
        threshold = (
            self.profile.embedding_merge_threshold
            if query_vec is not None
            else self.profile.merge_threshold
        )
        is_retelling = (
            existing is not None
            and sim >= threshold
            and existing.meta.get("actor", "user") == event.actor
            and existing.kind == kind
        )

        if is_retelling:
            repeat_count = int(existing.meta.get("mentions", 1)) + 1
        else:
            repeat_count = similar_count + 1
        repetition = min(1.0, repeat_count / 4.0)

        llm_score = None
        if self._llm is not None:
            llm_score = _safe_float(self._llm(text))

        report = score_importance(
            text,
            tokens,
            self.profile.importance_weights,
            self.profile.weight_sum(),
            novelty=novelty,
            repetition=repetition,
            llm_score=llm_score,
        )
        score = report.value if importance is None else _clamp(importance)

        if is_retelling and existing is not None:
            return self._rehearse(existing, event, at, score, report, tokens, query_vec)

        engram = Engram(
            engram_id=new_id(),
            scope=self.scope,
            kind=kind,
            raw_text=text,
            gist="",
            gist_source="none",
            emotion=detect_emotion(text),
            level=0,
            fidelity=0,
            importance=score,
            stability=decay.initial_stability(
                score, self.profile.max_stability, self.profile.importance_gain
            ),
            difficulty=5.0,
            active_age_days=0.0,
            created_at=at,
            last_active_at=at,
            refs=list(event.meta.get("refs", [])),
            tokens=tokens,
            meta={
                "actor": event.actor,
                "scene": event.scene,
                "features": report.features,
                **{k: v for k, v in event.meta.items() if k != "refs"},
            },
        )

        if summarize and self._summarizer is not None:
            self._apply_summary(engram, [event])

        self.store.insert(engram)
        self._index(engram, query_vec)
        if self._auto_facts:
            self._ingest_facts(engram, [event], at)
        return engram

    def remember_episode(
        self,
        events: Sequence[Event],
        *,
        kind: str = "episodic",
        summarize: bool = True,
        importance: Optional[float] = None,
    ) -> Optional[Engram]:
        events = [e for e in events if (e.text or "").strip()]
        if not events:
            return None
        at = _aware(events[-1].resolved_at())
        text = "\n".join(
            ("我：" if e.actor == "assistant" else "") + e.text.strip() for e in events
        )
        tokens = tokenize(text)
        query_vec = self._embed_query(text)
        recent = self.store.recent(self.scope, 300)
        existing, sim, similar_count = _match_recent(recent, tokens)
        if query_vec is not None and self._vectors is not None:
            recent_ids = {e.engram_id for e in recent}
            vec_hits = self._vectors.search(
                self.scope, query_vec, k=1, allowed=recent_ids
            )
            if vec_hits and vec_hits[0][1] > sim:
                candidate = self.store.get(vec_hits[0][0])
                if candidate is not None:
                    existing, sim = candidate, vec_hits[0][1]
        novelty = 1.0 - sim
        threshold = (
            self.profile.embedding_merge_threshold
            if query_vec is not None
            else self.profile.merge_threshold
        )
        is_retelling = (
            existing is not None
            and sim >= threshold
            and existing.meta.get("actor", "user") == "user"
            and existing.kind == kind
        )

        if importance is not None:
            score = _clamp(importance)
        else:
            report = score_importance(
                text,
                tokens,
                self.profile.importance_weights,
                self.profile.weight_sum(),
                novelty=novelty,
                repetition=min(1.0, (similar_count + 1) / 4.0),
                llm_score=_safe_float(self._llm(text)) if self._llm else None,
            )
            score = report.value

        if is_retelling and existing is not None:
            return self._rehearse(existing, _event_from_episode(events), at, score, None, tokens, query_vec)

        refs: List[str] = []
        for event in events:
            refs.extend(event.meta.get("refs", []))
        engram = Engram(
            scope=self.scope,
            kind=kind,
            raw_text=text,
            gist=first_clause(text, max_chars=60),
            gist_source="extractive",
            emotion=_dominant_emotion(events),
            level=0,
            fidelity=0,
            importance=score,
            stability=decay.initial_stability(
                score, self.profile.max_stability, self.profile.importance_gain
            ),
            created_at=_aware(events[0].resolved_at()),
            last_active_at=at,
            refs=refs,
            tokens=tokens,
            meta={
                "actor": "user",
                "scene": events[0].scene,
                "turns": len(events),
                "span": [_dt(events[0].resolved_at()), _dt(at)],
            },
        )
        if summarize and self._summarizer is not None:
            self._apply_summary(engram, list(events))

        self.store.insert(engram)
        self._index(engram, query_vec)
        if self._auto_facts:
            self._ingest_facts(engram, list(events), at)
        return engram

    def feed_episode(self, event: Event, *, max_turns: int = 10, gap_minutes: float = 180.0) -> List[Engram]:
        """Convenience helper: buffer events and write closed episodes."""
        segmenter = self._segmenters.get(self.scope)
        if segmenter is None:
            segmenter = WindowSegmenter(max_turns=max_turns, gap_minutes=gap_minutes)
            self._segmenters[self.scope] = segmenter
        out: List[Engram] = []
        for episode in segmenter.feed(event):
            engram = self.remember_episode(episode.events)
            if engram is not None:
                out.append(engram)
        return out

    def flush_episode(self) -> List[Engram]:
        segmenter = self._segmenters.get(self.scope)
        if segmenter is None:
            return []
        out: List[Engram] = []
        for episode in segmenter.flush():
            engram = self.remember_episode(episode.events)
            if engram is not None:
                out.append(engram)
        return out

    # -- 2. read -------------------------------------------------------------
    def recall(
        self,
        query: str = "",
        *,
        budget: int = 6,
        max_chars: Optional[int] = None,
        kinds: Optional[Iterable[str]] = None,
        now: Optional[datetime] = None,
        reinforce: bool = False,
        spontaneous: bool = False,
    ) -> List[RecallHit]:
        at = _aware(now) if now else self.now()
        if budget <= 0:
            return []
        self._drain_index()
        query_tokens = tokenize(query)
        if not query_tokens:
            # Proactive recall has no query: use salience instead of token match.
            return self.salient(budget=budget, now=at) if spontaneous else []

        max_chars = self.profile.max_chars if max_chars is None else max_chars
        candidates, relevance = self._candidates(query, query_tokens, at)
        if not candidates:
            return []

        wanted = set(kinds) if kinds else None
        query_emotion = detect_emotion(query)

        scored: List[Tuple[str, float, Engram, Dict[str, float]]] = []
        for engram_id, rel in relevance.items():
            engram = self.store.get(engram_id)
            if engram is None or engram.level >= self.max_level:
                continue
            if wanted and engram.kind not in wanted:
                continue
            if spontaneous and not self._may_speak(engram, at):
                continue

            r = decay.retrievability(engram.active_age_days, engram.stability)
            signals = {
                "similarity": rel,
                "retrievability": r,
                "importance": engram.importance,
                "recency": recency_factor(engram.created_at, at),
                "relationship": self._relationship_boost(engram),
                "emotion": 1.0 if (query_emotion and engram.emotion == query_emotion) else 0.0,
                "cooldown": self._cooldown(engram, at),
            }
            score = retrieval_score(
                RetrievalSignals(
                    similarity=signals["similarity"],
                    retrievability=signals["retrievability"],
                    importance=signals["importance"],
                    recency=signals["recency"],
                    relationship=signals["relationship"],
                    emotion=signals["emotion"],
                    cooldown=signals["cooldown"],
                ),
                self.profile.retrieval_weights,
            )
            scored.append((engram_id, score, engram, signals))

        if not scored:
            return []

        by_id = {item[0]: item for item in scored}
        candidate_scores = [(item[0], item[1]) for item in scored]
        pick_count = max(1, budget)
        selected = mmr(
            candidate_scores,
            lambda a, b: jaccard(by_id[a][2].tokens, by_id[b][2].tokens),
            pick_count,
            self.profile.mmr_lambda,
        )

        hits: List[RecallHit] = []
        used_chars = 0
        for engram_id in selected:
            _, score, engram, signals = by_id[engram_id]
            text = engram.render(now=at)
            if len(text) > self.profile.max_item_chars:
                text = text[: self.profile.max_item_chars] + "…"
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining]
            used_chars += len(text)
            hits.append(RecallHit(engram=engram, score=score, text=text, signals=signals))

        if reinforce:
            for hit in hits:
                self._reinforce(hit.engram, at)
                self.store.update(hit.engram)
                self.store.log_recall(
                    self.scope,
                    hit.engram.engram_id,
                    query,
                    at,
                    spontaneous=spontaneous,
                    level=hit.level,
                    score=hit.score,
                )
        return hits

    def peek(
        self, query: str = "", *, budget: int = 6, now: Optional[datetime] = None
    ) -> List[RecallHit]:
        """Read-only recall: no strengthening, no audit row."""
        return self.recall(query, budget=budget, now=now, reinforce=False)

    def salient(
        self, *, budget: int = 3, now: Optional[datetime] = None
    ) -> List[RecallHit]:
        """Proactive selection with no query: what is worth bringing up?"""
        at = _aware(now) if now else self.now()
        if budget <= 0:
            return []
        remaining = self.profile.mention_budget_per_day - self.store.mentioned_today(
            self.scope, at
        )
        if remaining <= 0:
            return []
        budget = min(budget, remaining)

        scored: List[Tuple[str, float, Engram]] = []
        for engram in self.store.list(self.scope):
            if engram.level >= self.max_level or engram.level > 1:
                continue
            if not self._may_speak(engram, at):
                continue
            r = decay.retrievability(engram.active_age_days, engram.stability)
            age = engram.age_days(at)
            salience = (
                max(0.01, engram.importance) ** 0.6
                * max(0.01, r) ** 0.4
                * pow(2.718281828459045, -age / 60.0)
            )
            scored.append((engram.engram_id, salience, engram))

        scored.sort(key=lambda item: item[1], reverse=True)
        hits: List[RecallHit] = []
        for engram_id, score, engram in scored[:budget]:
            hits.append(
                RecallHit(
                    engram=engram,
                    score=score,
                    text=engram.render(now=at)[: self.profile.max_item_chars],
                )
            )
        return hits

    # -- 3. time -------------------------------------------------------------
    def tick(
        self,
        now: Optional[datetime] = None,
        *,
        active: bool = False,
        active_days: Optional[Set[date]] = None,
    ) -> TickReport:
        """Advance the clock, one calendar day at a time.

        Missing days default to ``idle_ratio``. A single ``tick`` after a long
        outage must never age memories as if the user had been talking daily.
        """
        at = _aware(now) if now else self.now()
        last = _parse_state_time(self._state.get("last_tick"))
        if last is None:
            self._state["last_tick"] = at.isoformat()
            self.store.set_state(self.scope, self.profile.name, self._state)
            return TickReport(scope=self.scope)

        delta_days = (at - last).total_seconds() / 86400.0
        if delta_days <= 0:
            return TickReport(scope=self.scope)

        engrams = self.store.list(self.scope)
        if not engrams:
            self._state["last_tick"] = at.isoformat()
            self.store.set_state(self.scope, self.profile.name, self._state)
            return TickReport(scope=self.scope)

        weights = _day_weights(last, at)
        analytic = len(weights) > 366
        active_set = {d for d in (active_days or set())}

        level_changes = 0
        fidelity_changes = 0
        for engram in engrams:
            if analytic:
                factor = self.profile.idle_ratio
                old_l, old_f = decay.tick_engram(engram, max(0.0, delta_days) * factor, at, self.profile)
                level_changes += int(old_l != engram.level)
                fidelity_changes += int(old_f != engram.fidelity)
                continue
            for day, fraction in weights:
                if day in active_set:
                    factor = 1.0
                elif day == at.date() and not active_set:
                    factor = 1.0 if active else self.profile.idle_ratio
                else:
                    factor = self.profile.idle_ratio
                old_l, old_f = decay.tick_engram(engram, fraction * factor, at, self.profile)
                level_changes += int(old_l != engram.level)
                fidelity_changes += int(old_f != engram.fidelity)

        self.store.update_many(engrams)
        self._state["last_tick"] = at.isoformat()
        self.store.set_state(self.scope, self.profile.name, self._state)
        return TickReport(
            days=len(weights),
            factor_active=1.0 if active else self.profile.idle_ratio,
            aged=len(engrams),
            level_changes=level_changes,
            fidelity_changes=fidelity_changes,
            scope=self.scope,
        )

    def consolidate(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        at = _aware(now) if now else self.now()
        changed = 0
        levels: Dict[int, int] = {}
        engrams = self.store.list(self.scope)
        for engram in engrams:
            r = decay.retrievability(engram.active_age_days, engram.stability)
            target = decay.target_level(
                r, engram.importance, self.max_level, self.profile.level_thresholds
            )
            if target > engram.fidelity:
                engram.fidelity = target
            if engram.fidelity > engram.level:
                engram.level = engram.fidelity
            if target > engram.level:
                engram.level = target
            if engram.level >= 1 and not engram.gist:
                if self._summarizer is not None:
                    self._apply_summary(engram, [Event(text=engram.raw_text, at=engram.created_at)])
                    changed += 1
                else:
                    engram.gist = first_clause(engram.raw_text, max_chars=48)
                    engram.gist_source = "extractive"
                    changed += 1
            levels[engram.level] = levels.get(engram.level, 0) + 1
            self.store.update(engram)

        expired = self.store.expire_facts(at)
        self._state["last_consolidated"] = at.isoformat()
        self.store.set_state(self.scope, self.profile.name, self._state)
        return {
            "scope": self.scope,
            "changed": changed,
            "levels": dict(sorted(levels.items())),
            "total": sum(levels.values()),
            "facts_expired": expired,
        }

    # -- 4. correction -------------------------------------------------------
    def correct(
        self,
        engram_id: str,
        *,
        text: Optional[str] = None,
        importance: Optional[float] = None,
        refs: Optional[List[str]] = None,
    ) -> Engram:
        engram = self.store.get(engram_id)
        if engram is None:
            raise KeyError(engram_id)
        if text is not None:
            engram.raw_text = text
            engram.tokens = tokenize(text)
            self.store.replace_tokens(engram_id, engram.tokens)
            if self._summarizer is not None:
                self._apply_summary(engram, [Event(text=text, at=engram.created_at)])
            else:
                engram.gist = first_clause(text, max_chars=48)
                engram.gist_source = "extractive"
            self._index(engram, None)
        if importance is not None:
            engram.importance = _clamp(importance)
        if refs is not None:
            engram.refs = list(refs)
        engram.corrected_at = self.now()
        self.store.update(engram)
        self._bm25.invalidate()
        return engram

    def set_importance(self, engram_id: str, value: float, *, force: bool = False) -> Engram:
        engram = self.store.get(engram_id)
        if engram is None:
            raise KeyError(engram_id)
        value = _clamp(value)
        # Importance normally only rises; correction is the explicit override.
        engram.importance = value if force else max(engram.importance, value)
        self.store.update(engram)
        return engram

    def mark_mentioned(
        self, engram_ids: Iterable[str], now: Optional[datetime] = None
    ) -> None:
        ids = list(engram_ids)
        if not ids:
            return
        at = _aware(now) if now else self.now()
        self.store.mark_mentioned(ids, at)
        for engram_id in ids:
            engram = self.store.get(engram_id)
            if engram is not None:
                self.store.log_recall(
                    self.scope,
                    engram_id,
                    "",
                    at,
                    surfaced=True,
                    level=engram.level,
                )

    def forget(self, engram_id: str, *, hard: bool = True) -> bool:
        engram = self.store.get(engram_id)
        if engram is None:
            return False
        if hard:
            if self._vectors is not None:
                self._vectors.delete(engram_id)
            self.store.delete(engram_id)
        else:
            engram.level = self.max_level
            engram.fidelity = self.max_level
            engram.raw_text = ""
            engram.gist = ""
            engram.gist_source = "none"
            engram.tokens = set()
            self.store.update(engram)
            self.store.replace_tokens(engram_id, [])
            self.store.delete_facts_for_engram(engram_id)
            if self._vectors is not None:
                self._vectors.delete(engram_id)
        self._bm25.invalidate()
        return True

    # -- facts ---------------------------------------------------------------
    def facts(
        self,
        *,
        kinds: Optional[Iterable[str]] = None,
        now: Optional[datetime] = None,
        include_inactive: bool = False,
    ) -> List[Fact]:
        return self.store.list_facts(
            self.scope,
            now=_aware(now) if now else self.now(),
            kinds=list(kinds) if kinds else None,
            include_inactive=include_inactive,
        )

    def add_fact(
        self,
        candidate: FactCandidate,
        *,
        source_engram: str = "manual",
        now: Optional[datetime] = None,
    ) -> Optional[Fact]:
        fact = build_fact(
            candidate,
            scope=self.scope,
            source_engram=source_engram,
            source_refs=[],
            now=_aware(now) if now else self.now(),
        )
        if fact is None:
            return None
        self._insert_fact_with_conflicts(fact)
        return fact

    # -- index maintenance ---------------------------------------------------
    def rebuild_vectors(
        self,
        *,
        scope: Optional[str] = None,
        batch_size: int = 16,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Drop and recompute vectors for a scope.

        Required after changing the embedding model: vectors from different
        models are not comparable even when the dimension matches.
        """
        if self._embedder is None or self._vectors is None:
            raise ValueError("rebuild_vectors needs an embedder")
        if self._indexer is not None:
            self._indexer.flush()
            self._drain_index()
        target = scope or self.scope
        all_engrams = self.store.list(target)
        engrams = [e for e in all_engrams if e.raw_text.strip()]
        total = len(engrams)
        self.store.clear_vectors(target)
        self._vectors.invalidate(target)

        done = 0
        step = max(1, int(batch_size))
        for start in range(0, total, step):
            batch = engrams[start : start + step]
            vectors = self._embedder.embed([e.raw_text for e in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("embedder returned {} vectors for {} texts".format(
                    len(vectors), len(batch)
                ))
            for engram, vector in zip(batch, vectors):
                self._vectors.upsert(target, engram.engram_id, vector)
            done += len(batch)
            if progress is not None:
                progress(done, total)

        fingerprint = self._vectors.fingerprint()
        self.store.set_meta("embedder_fingerprint", fingerprint)
        if target == self.scope:
            self._state.pop("needs_vector_rebuild", None)
            self.store.set_state(target, self.profile.name, self._state)
        return {
            "scope": target,
            "vectors": done,
            "skipped_empty": len(all_engrams) - total,
            "model": self._embedder.model,
            "dim": self._embedder.dim,
            "fingerprint": fingerprint,
        }

    # -- introspection -------------------------------------------------------
    def count(self) -> int:
        return self.store.count(self.scope, self.max_level)

    def relationship(self) -> Dict[str, Any]:
        return dict(self._state.get("relationship", {}))

    def set_relationship(self, **kwargs: Any) -> Dict[str, Any]:
        rel = dict(self._state.get("relationship", {}))
        rel.update(kwargs)
        self._state["relationship"] = rel
        self.store.set_state(self.scope, self.profile.name, self._state)
        return rel

    def stats(self) -> Dict[str, Any]:
        engrams = self.store.list(self.scope)
        buckets: Dict[int, int] = {}
        fidelity: Dict[int, int] = {}
        for engram in engrams:
            buckets[engram.level] = buckets.get(engram.level, 0) + 1
            fidelity[engram.fidelity] = fidelity.get(engram.fidelity, 0) + 1
        stability = [e.stability for e in engrams]
        return {
            "scope": self.scope,
            "profile": self.profile.name,
            "total": len(engrams),
            "levels": dict(sorted(buckets.items())),
            "fidelity": dict(sorted(fidelity.items())),
            "mean_importance": round(
                sum(e.importance for e in engrams) / len(engrams), 3
            )
            if engrams
            else 0.0,
            "mean_stability": round(sum(stability) / len(stability), 3)
            if stability
            else 0.0,
            "facts": self.store.count_facts(self.scope),
            "recall": self.store.recall_stats(self.scope),
            "index_pending": self._indexer.pending if self._indexer else 0,
            "index_dropped": self._indexer.dropped if self._indexer else 0,
            "schema_version": self.store.schema_version(),
        }

    # -- internals -----------------------------------------------------------
    def _candidates(
        self, query: str, query_tokens: Set[str], at: datetime
    ) -> Tuple[List[str], Dict[str, float]]:
        limit = max(1, self.profile.candidate_limit)
        active = set(self.store.active_ids(self.scope, self.max_level))
        rankings: List[List[str]] = []

        bm = self._bm25.search(self.scope, query_tokens, self.max_level, limit=limit)
        if bm:
            rankings.append([doc_id for doc_id, _ in bm])

        if (
            self._vectors is not None
            and self._embedder is not None
            and query.strip()
        ):
            vec = self._embedder.embed([query])[0]
            ann = self._vectors.search(self.scope, vec, k=limit, allowed=active)
            if ann:
                rankings.append([doc_id for doc_id, _ in ann])

        fused = rrf(rankings, k=60)
        if not fused:
            return [], {}
        fused = {doc_id: score for doc_id, score in fused.items() if doc_id in active}
        relevance = normalize(fused)
        return list(fused.keys()), relevance

    def _embed_query(self, text: str) -> Optional[List[float]]:
        # With a background indexer we deliberately skip the synchronous query
        # embedding for merge detection. Merging falls back to token overlap;
        # retrieval still embeds the query because it cannot be deferred.
        if self._indexer is not None:
            return None
        if self._vectors is None or self._embedder is None or not text.strip():
            return None
        return self._embedder.embed([text])[0]

    def _index(self, engram: Engram, vec: Optional[List[float]] = None) -> None:
        if not self._auto_index or self._vectors is None or self._embedder is None:
            return
        if self._indexer is not None:
            if not self._indexer.submit(self.scope, engram.engram_id, engram.raw_text):
                self._state["needs_vector_rebuild"] = True
                self.store.set_state(self.scope, self.profile.name, self._state)
            return
        if vec is None:
            vec = self._embedder.embed([engram.raw_text])[0]
        self._vectors.upsert(self.scope, engram.engram_id, vec)

    def _apply_async_vector(
        self, scope: str, engram_id: str, text: str, vector: List[float]
    ) -> None:
        engram = self.store.get(engram_id)
        # The memory may have been deleted or corrected while the worker ran.
        if engram is None or engram.raw_text != text:
            return
        if self._vectors is not None:
            self._vectors.upsert(scope, engram_id, vector)

    def _drain_index(self) -> int:
        if self._indexer is None:
            return 0
        written = self._indexer.drain()
        errors = self._indexer.take_errors()
        if errors:
            self._state["needs_vector_rebuild"] = True
            self.store.set_state(self.scope, self.profile.name, self._state)
        return written

    def _rehearse(
        self,
        engram: Engram,
        event: Event,
        at: datetime,
        score: float,
        report: Any,
        tokens: Set[str],
        query_vec: Optional[List[float]],
    ) -> Engram:
        r = decay.retrievability(engram.active_age_days, engram.stability)
        engram.importance = max(engram.importance, score)
        engram.stability = decay.reinforce(
            engram.stability, r, engram.importance, self.profile.max_stability
        )
        engram.active_age_days = 0.0
        engram.last_active_at = at
        # A re-telling is new evidence, not recall: the user supplied the
        # detail again, so the trace may recover one level of fidelity.
        if engram.fidelity > 0:
            engram.fidelity -= 1
        engram.level = max(engram.fidelity, engram.level - 1)
        if len(event.text) > len(engram.raw_text):
            engram.raw_text = event.text
            engram.tokens = tokenize(engram.raw_text)
            self.store.replace_tokens(engram.engram_id, engram.tokens)
        for ref in event.meta.get("refs", []):
            if ref not in engram.refs:
                engram.refs.append(ref)
        engram.meta["mentions"] = int(engram.meta.get("mentions", 1)) + 1
        if report is not None:
            engram.meta["features"] = report.features
        engram.meta.update({k: v for k, v in event.meta.items() if k != "refs"})
        self.store.update(engram)
        self.store.add_tokens(engram.engram_id, tokens)
        self._index(engram, query_vec)
        return engram

    def _reinforce(self, engram: Engram, at: datetime) -> None:
        r = decay.retrievability(engram.active_age_days, engram.stability)
        engram.stability = decay.reinforce(
            engram.stability, r, engram.importance, self.profile.max_stability
        )
        engram.active_age_days = 0.0
        engram.recall_count += 1
        engram.last_recall_at = at
        # Accessibility only: never below fidelity, never restores lost detail.
        engram.level = decay.level_down(engram.level, engram.fidelity, 1)

    def _cooldown(self, engram: Engram, at: datetime) -> float:
        if not engram.last_mention_at:
            return 0.0
        hours = (at - _aware(engram.last_mention_at)).total_seconds() / 3600.0
        return 1.0 if hours < self.profile.cooldown_hours else 0.0

    def _may_speak(self, engram: Engram, at: datetime) -> bool:
        if engram.level > 1:
            return False
        if engram.emotion in NEGATIVE_EMOTIONS and self._intimacy() < self.profile.negative_intimacy_threshold:
            return False
        return self._cooldown(engram, at) <= 0

    def _intimacy(self) -> float:
        rel = self._state.get("relationship", {}) or {}
        try:
            return float(rel.get("intimacy", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _relationship_boost(self, engram: Engram) -> float:
        if not self.profile.enable_relationship:
            return 0.0
        rel = self._state.get("relationship", {}) or {}
        if not rel:
            return 0.0
        intimacy = self._intimacy()
        return min(1.0, intimacy * (0.5 + engram.importance))

    def _apply_summary(self, engram: Engram, events: Sequence[Event]) -> None:
        if self._summarizer is None:
            return
        try:
            if hasattr(self._summarizer, "summarize"):
                summary = self._summarizer.summarize(list(events), persona=None)
            else:
                summary = self._summarizer(list(events))
        except Exception:
            return
        if isinstance(summary, Summary):
            if summary.gist:
                engram.gist = summary.gist
                engram.gist_source = "llm"
            if summary.emotion:
                engram.emotion = summary.emotion
            engram.importance = max(engram.importance, _clamp(summary.importance))
        elif isinstance(summary, str) and summary.strip():
            engram.gist = summary.strip()
            engram.gist_source = "llm"

    def _ingest_facts(
        self, engram: Engram, events: Sequence[Event], at: datetime
    ) -> None:
        if self._fact_extractor is None:
            return
        candidates: List[FactCandidate] = []
        for event in events:
            if event.actor != "user":
                continue
            synthetic = Engram(raw_text=event.text, meta={"actor": "user"})
            try:
                candidates.extend(self._fact_extractor.extract(synthetic))
            except Exception:
                continue
        for candidate in candidates:
            fact = build_fact(
                candidate,
                scope=self.scope,
                source_engram=engram.engram_id,
                source_refs=engram.refs,
                now=at,
            )
            if fact is not None:
                self._insert_fact_with_conflicts(fact)

    def _insert_fact_with_conflicts(self, fact: Fact) -> None:
        conflicts = self.store.find_conflicting_facts(
            self.scope, fact.kind, fact.subject, fact.predicate, fact.fact_id
        )
        for old in conflicts:
            # Same value is a duplicate; a different value supersedes the old one.
            if old.obj.strip().casefold() == fact.obj.strip().casefold():
                return
            self.store.supersede_fact(old.fact_id, fact.created_at)
        self.store.insert_fact(fact)


# -- helpers -----------------------------------------------------------------
def _match_recent(
    recent: Sequence[Engram], tokens: Set[str], threshold: float = 0.35
) -> Tuple[Optional[Engram], float, int]:
    best: Optional[Engram] = None
    best_sim = 0.0
    similar = 0
    for engram in recent:
        score = jaccard(tokens, engram.tokens)
        if score > best_sim:
            best, best_sim = engram, score
        if score >= threshold:
            similar += 1
    return best, best_sim, similar


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


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


def _day_weights(start: datetime, end: datetime) -> List[Tuple[date, float]]:
    """Split an interval into (calendar day, fraction-of-a-day) pieces."""
    out: List[Tuple[date, float]] = []
    cursor = start
    while cursor < end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1),
            dtime.min,
            tzinfo=cursor.tzinfo,
        )
        step = min(end, next_midnight)
        fraction = (step - cursor).total_seconds() / 86400.0
        if fraction > 0:
            out.append((cursor.date(), fraction))
        cursor = step
    return out


def _dt(value: Optional[datetime]) -> str:
    return value.isoformat() if value else ""


def _event_from_episode(events: Sequence[Event]) -> Event:
    return Event(
        text="\n".join(e.text for e in events),
        actor=events[0].actor if events else "user",
        at=events[-1].resolved_at() if events else None,
        scene=events[0].scene if events else "",
        meta={"refs": [r for e in events for r in e.meta.get("refs", [])]},
    )


def _dominant_emotion(events: Sequence[Event]) -> str:
    counts: Dict[str, int] = {}
    for event in events:
        emotion = detect_emotion(event.text)
        if emotion:
            counts[emotion] = counts.get(emotion, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]
