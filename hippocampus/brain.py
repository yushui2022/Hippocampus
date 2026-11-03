"""Brain: one self, many situations.

``Brain`` is the v3 layer above the v2 kernel. The kernel still owns graded
forgetting; this layer owns *what enters memory*, *where it came from*, *who
was there*, and *what may be said now*.

Pipeline (v3 spec 5.1)::

    observe  -> sensory gate -> attention -> staging (no LLM)
    maintain -> sleep / promote / cards / tick / consolidate / reflect
    context  -> recall -> source filter -> render

Invariants enforced here:

* ``observe`` never calls an LLM;
* buffer rows disappear after the TTL;
* assistant lines never create facts, cards or self-model entries;
* private memories are never recalled in front of outsiders;
* ``context`` is read-only;
* ``maintain`` is idempotent for the same clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .consolidation import ConsolidationReport, Consolidator
from .entities import decayed_familiarity, render_context, render_person
from .kernel import Hippocampus
from .models import Engram, Entity, Event, RecallHit, StagingItem, Utterance
from .profiles import Profile, get_profile
from .self_model import SelfModel, parse_reflection, reflection_prompt
from .sensory import SensoryGate, content_chars, duplicate_key, normalize, recently_said
from .source import boost as source_boost
from .source import speakable
from .store import Store
from .tokens import tokenize


@dataclass
class ObserveResult:
    kept: bool
    reason: str
    staging_id: str = ""
    attention: bool = False


@dataclass
class BrainContext:
    memories: List[RecallHit] = field(default_factory=list)
    buffer: List[RecallHit] = field(default_factory=list)
    people: Dict[str, str] = field(default_factory=dict)
    context_card: str = ""
    blocked: int = 0

    def render(self, *, max_chars: int = 1200) -> str:
        blocks: List[str] = []
        if self.memories:
            blocks.append(
                "<长期记忆>\n"
                + "\n".join("- " + hit.text for hit in self.memories)
                + "\n</长期记忆>"
            )
        if self.buffer:
            blocks.append(
                "<最近听到的>\n"
                + "\n".join("- " + hit.text for hit in self.buffer)
                + "\n</最近听到的>"
            )
        if self.people:
            blocks.append(
                "<你认识的人>\n"
                + "\n".join(self.people.values())
                + "\n</你认识的人>"
            )
        if self.context_card:
            blocks.append("<这个情境>\n" + self.context_card + "\n</这个情境>")
        text = "\n".join(blocks)
        return text[:max_chars]


class Brain:
    def __init__(
        self,
        scope: str,
        *,
        store: Optional[Store] = None,
        path: str = ":memory:",
        profile: Any = "companion",
        summarizer: Optional[Any] = None,
        embedder: Optional[Any] = None,
        persona: str = "",
        clock: Optional[Any] = None,
        async_index: Optional[bool] = None,
        async_summary: Optional[bool] = None,
    ) -> None:
        self.scope = scope
        self.profile: Profile = (
            get_profile(profile) if isinstance(profile, str) else profile
        )
        self.store = store or Store(path)
        self._owns_store = store is None
        self.summarizer = summarizer
        self.hippo = Hippocampus(
            scope=scope,
            store=self.store,
            profile=self.profile,
            summarizer=summarizer,
            embedder=embedder,
            persona=persona,
            clock=clock,
            async_index=async_index,
            async_summary=async_summary,
        )
        self.gate = SensoryGate(min_chars=self.profile.sensory_min_chars)
        self.consolidator = Consolidator(
            self.store,
            scope,
            self.profile,
            writer=self.hippo,
            summarizer=summarizer,
        )
        self._recent: Dict[str, tuple] = {}
        self._active_days = set()
        self._last_maintain: Optional[datetime] = None

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self.hippo.close()
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "Brain":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- online: perceive ----------------------------------------------------
    def observe(self, utterance: Utterance) -> ObserveResult:
        at = utterance.resolved_at()
        text = normalize(utterance.text)
        utterance = Utterance(
            text=text,
            context_id=utterance.context_id,
            context_kind=utterance.context_kind,
            context_size=utterance.context_size,
            speaker_id=utterance.speaker_id,
            speaker_name=utterance.speaker_name,
            actor=utterance.actor,
            addressed=utterance.addressed,
            witnesses=list(utterance.witnesses),
            visibility=utterance.visibility,
            at=at,
            refs=list(utterance.refs),
            kind=utterance.kind,
            meta=dict(utterance.meta),
        )
        self._active_days.add(at.date())

        # Familiarity updates even when the line is discarded: you remember a
        # face without remembering what they said.
        self._touch(utterance)

        decision = self.gate.check(text, addressed=utterance.addressed)
        if not decision.keep:
            return ObserveResult(False, decision.reason)
        if not self._attended(utterance):
            return ObserveResult(False, "attention")

        key = duplicate_key(text)
        previous = self._recent.get(utterance.speaker_id)
        if previous and previous[0] == key and recently_said(
            text, previous[1], at, minutes=10.0
        ):
            return ObserveResult(False, "repeat")
        self._recent[utterance.speaker_id] = (key, at)
        if len(self._recent) > 5000:
            self._recent = dict(list(self._recent.items())[-2500:])

        item = StagingItem(
            scope=self.scope,
            context_id=utterance.context_id,
            context_kind=utterance.context_kind,
            context_size=utterance.context_size,
            speaker_id=utterance.speaker_id,
            speaker_name=utterance.speaker_name,
            actor=utterance.actor,
            text=text,
            addressed=utterance.addressed,
            witnesses=list(dict.fromkeys(utterance.witnesses or [utterance.speaker_id])),
            visibility=utterance.resolved_visibility(),
            refs=list(utterance.refs),
            tokens=tokenize(text),
            at=at,
        )
        self.store.insert_staging(item)
        return ObserveResult(True, "staged", item.staging_id, True)

    def mark_replied(self, context_id: str, speaker_id: str) -> None:
        """A reply is attention: it makes the speaker slightly more familiar."""
        entity = self.store.get_entity(self.scope, speaker_id)
        if entity is None:
            return
        entity.familiarity = min(1.0, entity.familiarity + 0.05)
        self.store.upsert_entity(entity)

    def _touch(self, utterance: Utterance) -> None:
        at = utterance.resolved_at()
        policy = self.profile.policy(
            utterance.context_kind, utterance.context_size
        )
        entity = self.store.get_entity(self.scope, utterance.speaker_id)
        if entity is None:
            entity = Entity(
                scope=self.scope,
                entity_id=utterance.speaker_id,
                kind="participant",
                name=utterance.speaker_name or utterance.speaker_id,
                first_seen=at,
            )
        entity.familiarity = decayed_familiarity(
            entity.familiarity,
            entity.last_seen or entity.first_seen,
            at,
            policy.familiarity_half_life_days,
        )
        gain = 0.02
        if utterance.addressed:
            gain += 0.08
        if utterance.actor == "assistant":
            gain = 0.0
        entity.familiarity = min(1.0, entity.familiarity + gain)
        entity.interactions += 1
        entity.last_seen = at
        if utterance.speaker_name:
            entity.name = utterance.speaker_name
        if utterance.context_id not in entity.contexts:
            entity.contexts.append(utterance.context_id)
            entity.contexts = entity.contexts[-200:]
        self.store.upsert_entity(entity)

        context = self.store.get_entity(self.scope, utterance.context_id)
        if context is None:
            context = Entity(
                scope=self.scope,
                entity_id=utterance.context_id,
                kind="context",
                name=utterance.context_id,
                first_seen=at,
            )
        context.interactions += 1
        context.last_seen = at
        if utterance.speaker_id and utterance.speaker_id not in context.contexts:
            context.contexts.append(utterance.speaker_id)
            context.contexts = context.contexts[-500:]
        self.store.upsert_entity(context)

    def _attended(self, utterance: Utterance) -> bool:
        if utterance.context_kind != "group":
            return True
        if utterance.addressed:
            return True
        policy = self.profile.policy("group", utterance.context_size)
        entity = self.store.get_entity(self.scope, utterance.speaker_id)
        if entity is not None and entity.familiarity >= policy.attention_familiar:
            return True
        if utterance.context_size < self.profile.crowd_size:
            return True
        return (
            len(content_chars(utterance.text))
            >= policy.attention_min_chars_crowd
        )

    # -- output: recall + source filter --------------------------------------
    def recall(
        self,
        query: str,
        *,
        context_id: str = "",
        present: Optional[Iterable[str]] = None,
        budget: int = 3,
        now: Optional[datetime] = None,
    ) -> BrainContext:
        at = now or self.hippo.now()
        present = [p for p in dict.fromkeys(present or []) if p]
        members = self._members(context_id)

        raw = self.hippo.recall(query, budget=max(1, budget) * 3, now=at)
        allowed: List[RecallHit] = []
        blocked = 0
        for hit in raw:
            if speakable(
                hit.engram.meta.get("source") or {},
                context_id=context_id,
                present=present,
                members=members,
            ):
                hit.score += source_boost(
                    hit.engram.meta.get("source") or {},
                    context_id=context_id,
                    present=present,
                )
                allowed.append(hit)
            else:
                blocked += 1
        allowed.sort(key=lambda h: h.score, reverse=True)
        memories = allowed[: max(1, budget)]

        buffer_hits = self._buffer_hits(
            query, context_id, present, members, at, budget=budget
        )
        promoted_ids = {h.engram.engram_id for h in memories}
        buffer_hits = [
            h
            for h in buffer_hits
            if h.signals.get("promoted_id") not in promoted_ids
        ]

        people: Dict[str, str] = {}
        policy = self.profile.policy()
        for participant in present:
            entity = self.store.get_entity(self.scope, participant)
            if entity is None:
                continue
            rendered = render_person(
                entity, min_evidence=policy.card_min_evidence
            )
            if rendered:
                people[participant] = rendered

        context_card = ""
        if context_id:
            entity = self.store.get_entity(self.scope, context_id)
            if entity is not None:
                context_card = render_context(
                    entity, min_evidence=policy.card_min_evidence
                )

        return BrainContext(
            memories=memories,
            buffer=buffer_hits,
            people=people,
            context_card=context_card,
            blocked=blocked,
        )

    def context(
        self,
        query: str,
        *,
        context_id: str = "",
        present: Optional[Iterable[str]] = None,
        budget: int = 3,
        now: Optional[datetime] = None,
    ) -> BrainContext:
        """Read-only: recall may not reinforce or write anything."""
        return self.recall(
            query,
            context_id=context_id,
            present=present,
            budget=budget,
            now=now,
        )

    def _buffer_hits(
        self,
        query: str,
        context_id: str,
        present: Sequence[str],
        members: Sequence[str],
        now: datetime,
        *,
        budget: int = 3,
    ) -> List[RecallHit]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        since = now - timedelta(hours=self.profile.buffer_ttl_hours)
        items = self.store.list_staging(self.scope, since=since)
        scored: List[RecallHit] = []
        for item in items:
            if item.actor != "user":
                continue
            if not speakable(
                item.source(),
                context_id=context_id,
                present=present,
                members=members,
            ):
                continue
            overlap = len(query_tokens & item.tokens) / max(
                1, len(query_tokens)
            )
            if overlap <= 0.15:
                continue
            scored.append(
                RecallHit(
                    engram=item.as_engram(),
                    score=overlap * 0.9,
                    text=item.text,
                    signals={
                        "buffer": 1.0,
                        "overlap": overlap,
                        "promoted_id": item.engram_id or "",
                    },
                )
            )
        scored.sort(key=lambda h: (h.score, h.engram.created_at), reverse=True)
        return scored[: max(1, budget)]

    def _members(self, context_id: str) -> List[str]:
        if not context_id:
            return []
        entity = self.store.get_entity(self.scope, context_id)
        if entity is None or entity.kind != "context":
            return []
        return list(entity.contexts)

    # -- offline: sleep, tick, reflect ---------------------------------------
    def sleep(
        self,
        now: Optional[datetime] = None,
        *,
        context_id: Optional[str] = None,
        force: bool = True,
    ) -> ConsolidationReport:
        at = now or self.hippo.now()
        return self.consolidator.run(at, context_id=context_id, force=force)

    def maintain(
        self, now: Optional[datetime] = None, *, force: bool = False
    ) -> Dict[str, Any]:
        at = now or self.hippo.now()
        if self._last_maintain is not None and at <= self._last_maintain:
            return {"skipped": True, "reason": "clock", "at": at.isoformat()}

        purged = self.store.purge_staging(
            self.scope, at - timedelta(hours=self.profile.buffer_ttl_hours)
        )
        report = self.consolidator.run(at, force=force)
        tick = self.hippo.tick(at, active_days=set(self._active_days))
        consolidated = self.hippo.consolidate(at)
        reflected = self.reflect(now=at)

        self._active_days.clear()
        self._last_maintain = at
        return {
            "at": at.isoformat(),
            "purged": purged,
            "promoted": report.promoted,
            "dropped": report.dropped,
            "engrams": report.engrams,
            "facts": report.facts,
            "notes": report.notes,
            "llm": report.llm,
            "sleep_errors": self.consolidator.errors,
            "aged": tick.aged,
            "consolidated": consolidated,
            "reflected": reflected,
        }

    def reflect(self, now: Optional[datetime] = None, *, force: bool = False) -> bool:
        if self.summarizer is None:
            return False
        at = now or self.hippo.now()
        model = SelfModel(self.store, self.scope)
        pending = [
            e
            for e in model.pending(self.store.list(self.scope))
            if e.actor == "user" and e.raw_text.strip()
        ]
        if not pending:
            return False
        policy = self.profile.policy()
        pressure = sum(e.importance for e in pending)
        last_raw = model.state.get("reflected_at")
        hours = 1e9
        if last_raw:
            try:
                last = datetime.fromisoformat(str(last_raw))
                hours = (at - last).total_seconds() / 3600.0
            except ValueError:
                hours = 1e9
        if not force and (
            pressure < policy.reflect_pressure
            or hours < self.profile.reflect_min_hours
        ):
            return False
        try:
            raw = self._chat(reflection_prompt(model, pending))
            model.merge(parse_reflection(raw))
        except Exception:
            self.consolidator.errors += 1
            return False
        model.mark_reflected(pending)
        return True

    def _chat(self, messages: List[dict]) -> str:
        if hasattr(self.summarizer, "chat"):
            return str(self.summarizer.chat(messages) or "")
        return str(self.summarizer(messages) or "")

    # -- introspection -------------------------------------------------------
    def person(self, entity_id: str) -> Optional[Entity]:
        return self.store.get_entity(self.scope, entity_id)

    def people(self) -> List[Entity]:
        return self.store.list_entities(self.scope, "participant")

    def self_model(self) -> SelfModel:
        return SelfModel(self.store, self.scope)

    def stats(self) -> Dict[str, Any]:
        base = self.hippo.stats()
        base.update(
            {
                "buffer_pending": self.store.count_staging(self.scope, "pending"),
                "buffer_promoted": self.store.count_staging(self.scope, "promoted"),
                "buffer_dropped": self.store.count_staging(self.scope, "dropped"),
                "entities": len(self.store.list_entities(self.scope)),
                "sleep_errors": self.consolidator.errors,
                "last_maintain": self._last_maintain.isoformat()
                if self._last_maintain
                else None,
            }
        )
        return base
