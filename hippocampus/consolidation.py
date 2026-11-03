"""Sleep consolidation: turn a day's buffer into long-term memory.

Online, ``Brain.observe`` only inserts a staging row. Offline, this module
replays a batch of them and decides, once per group:

* is this worth keeping (salience >= threshold), or is it the kind of small
  thing a person forgets?
* what is the gist, who was involved, what topics does it touch?
* what facts and person-notes follow from it?
* which situation did it happen in (source), so it can be said only there?

One LLM call per batch when available. Everything has a deterministic rule
fallback, because a memory system that stops working when the model returns
bad JSON is not a memory system.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .entities import add_note, add_topic
from .facts import RuleFactExtractor
from .models import Engram, Entity, Event, StagingItem, Summary
from .profiles import ContextPolicy, Profile
from .scoring import detect_emotion
from .store import Store
from .tokens import first_clause, jaccard, tokenize

_CONSEQUENCE = re.compile(
    "明天|后天|下周|下个月|明年|面试|考试|搬家|住院|手术|分手|结婚|生日|"
    "辞职|入职|毕业|怀孕|去世|决定|打算|计划|约定|答应|"
    "喜欢|讨厌|过敏|不吃|不喝|害怕|担心"
)
_EXPLICIT = re.compile("记住|别忘了|记得|提醒我|很重要|一定要|说好了")
_SELF_REF = ("我", "咱", "咱俩")
_OTHER_REF = ("你", "您")

_PROMPT = (
    "你是一个记忆巩固器。下面是一批编号的对话片段，请把它们整理成事件。\n"
    "只输出 JSON，不要解释。格式：\n"
    '{"events": [{"turns": [1,2], "gist": "...", "people": ["p1"],'
    ' "emotion": "...", "topics": ["..."],'
    ' "salience": {"emotion":0-1,"self":0-1,"consequence":0-1,'
    '"novelty":0-1,"explicit":0-1},'
    ' "facts": [{"about":"p1","kind":"preference|plan|fact|relation",'
    '"text":"...","turns":[1]}],'
    ' "notes": [{"about":"p1","text":"...","turns":[1]}]}]}\n'
    "规则：\n"
    "1. turns 必须来自本批编号。\n"
    "2. facts 的 turns 至少一条是用户说的话，否则不要输出该事实。\n"
    "3. about 必须是本批出现过的参与者编号。\n"
    "4. 只记稳定的偏好、计划、关系；一次性的情绪不要写成事实。\n"
    "5. gist 用第一人称，像自己回想，不要列清单。\n"
    "6. 没有值得记的就输出 {\"events\": []}。"
)


@dataclass
class EventGroup:
    items: List[StagingItem]
    gist: str = ""
    people: List[str] = field(default_factory=list)
    emotion: str = ""
    topics: List[str] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Dict[str, Any]] = field(default_factory=list)
    salience_parts: Dict[str, float] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class ConsolidationReport:
    promoted: int = 0
    dropped: int = 0
    engrams: List[str] = field(default_factory=list)
    facts: int = 0
    notes: int = 0
    errors: int = 0
    llm: bool = False


class Consolidator:
    def __init__(
        self,
        store: Store,
        scope: str,
        profile: Profile,
        *,
        writer: Any,
        summarizer: Optional[Any] = None,
    ) -> None:
        self.store = store
        self.scope = scope
        self.profile = profile
        self.writer = writer  # Hippocampus: needs remember_episode()
        self.summarizer = summarizer
        self.extractor = RuleFactExtractor()
        self.errors = 0

    # -- triggering ----------------------------------------------------------
    def should_sleep(
        self,
        items: Sequence[StagingItem],
        now: datetime,
        policy: ContextPolicy,
        *,
        force: bool = False,
    ) -> bool:
        if not items:
            return False
        if force:
            return True
        oldest = min(i.at for i in items)
        newest = max(i.at for i in items)
        if (now - newest) >= timedelta(minutes=policy.sleep_idle_minutes):
            return True
        if (now - oldest) >= timedelta(hours=policy.sleep_max_hold_hours):
            return True
        pressure = sum(self.quick_salience(i) for i in items)
        return pressure >= policy.sleep_pressure

    def quick_salience(self, item: StagingItem) -> float:
        text = item.text or ""
        score = 0.0
        if item.addressed:
            score += 0.5
        if detect_emotion(text):
            score += 0.3
        if len(text) >= 12:
            score += 0.2
        if _EXPLICIT.search(text):
            score += 0.4
        if _CONSEQUENCE.search(text):
            score += 0.2
        return score

    # -- main ----------------------------------------------------------------
    def run(
        self,
        now: datetime,
        *,
        context_id: Optional[str] = None,
        force: bool = False,
    ) -> ConsolidationReport:
        report = ConsolidationReport()
        contexts = (
            [context_id]
            if context_id
            else self.store.staging_contexts(self.scope, "pending")
        )
        for ctx in contexts:
            items = self.store.list_staging(
                self.scope,
                context_id=ctx,
                statuses=["pending"],
                limit=self.profile.sleep_batch,
            )
            if not items:
                continue
            policy = self._policy(items)
            if not self.should_sleep(items, now, policy, force=force):
                continue
            self._consolidate(items, now, policy, report)
        return report

    def _policy(self, items: Sequence[StagingItem]) -> ContextPolicy:
        kind = items[-1].context_kind
        size = items[-1].context_size
        return self.profile.policy(kind, size)

    def _consolidate(
        self,
        items: Sequence[StagingItem],
        now: datetime,
        policy: ContextPolicy,
        report: ConsolidationReport,
    ) -> None:
        groups = None
        if self._can_llm():
            groups = self._llm_groups(list(items))
            report.llm = groups is not None
        if not groups:
            groups = self._rule_groups(list(items))

        for group in groups:
            if not group.items:
                continue
            score = group.score or self.score(group)
            group.score = score
            ids = [i.staging_id for i in group.items]
            if score < policy.promote_threshold:
                self.store.set_staging_status(
                    ids, "dropped", salience=score
                )
                report.dropped += len(ids)
                continue

            events = [
                Event(
                    text=i.text,
                    actor=i.actor,
                    at=i.at,
                    meta={"refs": list(i.refs)},
                )
                for i in group.items
            ]
            source = group.items[0].source()
            source["speakers"] = list(
                dict.fromkeys(i.speaker_id for i in group.items)
            )
            before_facts = self.store.count_facts(self.scope)
            engram = self.writer.remember_episode(
                events,
                importance=score,
                summarize=False,
                meta={
                    "source": source,
                    "salience": score,
                    "buffer_ids": ids,
                    "participants": list(
                        dict.fromkeys(
                            i.speaker_id for i in group.items if i.actor == "user"
                        )
                    ),
                },
                summary=Summary(
                    gist=group.gist,
                    topics=group.topics,
                    emotion=group.emotion,
                    importance=score,
                )
                if group.gist
                else None,
            )
            if engram is None:
                continue
            self.store.set_staging_status(
                ids, "promoted", engram_id=engram.engram_id, salience=score
            )
            report.promoted += len(ids)
            report.engrams.append(engram.engram_id)
            report.facts += max(
                0, self.store.count_facts(self.scope) - before_facts
            )
            report.notes += self._update_cards(group, engram, score, now)
        return None

    # -- scoring -------------------------------------------------------------
    def score(self, group: EventGroup) -> float:
        text = " ".join(i.text for i in group.items)
        tokens = tokenize(text)
        parts = dict(group.salience_parts or {})
        parts.setdefault("emotion", 1.0 if detect_emotion(text) else 0.0)
        parts.setdefault(
            "self",
            1.0
            if any(w in text for w in _SELF_REF)
            else (0.5 if any(w in text for w in _OTHER_REF) else 0.2),
        )
        parts.setdefault(
            "consequence", 1.0 if _CONSEQUENCE.search(text) else 0.0
        )
        parts.setdefault("explicit", 1.0 if _EXPLICIT.search(text) else 0.0)
        if "novelty" not in parts:
            try:
                similarity = self.store.max_similarity(self.scope, tokens)
            except Exception:
                similarity = 0.0
            parts["novelty"] = max(0.0, 1.0 - similarity)

        addressed = sum(1 for i in group.items if i.addressed) / max(
            1, len(group.items)
        )
        similar = self.store.count_similar(self.scope, tokens, threshold=0.2)
        repetition = min(1.0, similar / max(1, self.profile.repetition_window))

        familiarity = 0.0
        for speaker in {i.speaker_id for i in group.items}:
            entity = self.store.get_entity(self.scope, speaker)
            if entity is not None:
                familiarity = max(familiarity, entity.familiarity)

        fact_bonus = 0.0
        for item in group.items:
            if item.actor != "user":
                continue
            if self.extractor.extract(
                Engram(raw_text=item.text, meta={"actor": "user"})
            ):
                fact_bonus = 0.12
                break

        value = (
            0.22 * _clamp(parts.get("emotion", 0.0))
            + 0.16 * _clamp(parts.get("self", 0.0))
            + 0.26 * _clamp(parts.get("consequence", 0.0))
            + 0.10 * _clamp(parts.get("novelty", 0.0))
            + 0.16 * _clamp(parts.get("explicit", 0.0))
            + 0.10 * repetition
            + 0.10 * addressed
            + 0.06 * familiarity
            + fact_bonus
        )
        return _clamp(value)

    # -- rule fallback -------------------------------------------------------
    def _rule_groups(self, items: List[StagingItem]) -> List[EventGroup]:
        if not items:
            return []
        groups: List[List[StagingItem]] = [[items[0]]]
        for item in items[1:]:
            previous = groups[-1][-1]
            gap = (item.at - previous.at).total_seconds()
            overlap = jaccard(previous.tokens, item.tokens)
            if gap > 30 * 60 or (overlap < 0.15 and len(previous.text) >= 4):
                groups.append([item])
            else:
                groups[-1].append(item)
        out: List[EventGroup] = []
        for group_items in groups:
            text = "\n".join(
                ("我：" if i.actor == "assistant" else "") + i.text
                for i in group_items
            )
            out.append(
                EventGroup(
                    items=group_items,
                    gist=first_clause(text, max_chars=60),
                    emotion=detect_emotion(text),
                )
            )
        return out

    # -- LLM path ------------------------------------------------------------
    def _can_llm(self) -> bool:
        return self.summarizer is not None and (
            hasattr(self.summarizer, "chat") or callable(self.summarizer)
        )

    def _chat(self, messages: List[dict]) -> str:
        if hasattr(self.summarizer, "chat"):
            return str(self.summarizer.chat(messages) or "")
        return str(self.summarizer(messages) or "")

    def _llm_groups(self, items: List[StagingItem]) -> Optional[List[EventGroup]]:
        people: Dict[str, str] = {}
        lines: List[str] = []
        for index, item in enumerate(items, 1):
            if item.actor == "user":
                key = people.setdefault(item.speaker_id, "p{}".format(len(people) + 1))
                name = "{} ({})".format(item.speaker_name or item.speaker_id, key)
            else:
                name = "我"
            lines.append("[{}] {}: {}".format(index, name, item.text))

        messages = [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ]
        for _ in range(2):
            try:
                raw = self._chat(messages)
                data = _extract_json(raw)
                groups = self._validate(data, items, people)
                if groups is not None:
                    return groups
            except Exception:
                pass
            messages.append(
                {
                    "role": "user",
                    "content": "上次输出不是合法 JSON 或结构不对。只输出 JSON，不要解释。",
                }
            )
        self.errors += 1
        return None

    def _validate(
        self,
        data: Any,
        items: List[StagingItem],
        people: Dict[str, str],
    ) -> Optional[List[EventGroup]]:
        if not isinstance(data, dict) or not isinstance(data.get("events"), list):
            return None
        reverse = {v: k for k, v in people.items()}
        out: List[EventGroup] = []
        for event in data["events"]:
            if not isinstance(event, dict):
                continue
            turns = [
                int(t)
                for t in event.get("turns") or []
                if isinstance(t, (int, str)) and str(t).isdigit()
            ]
            turns = [t for t in turns if 1 <= t <= len(items)]
            if not turns:
                continue
            group_items = [items[t - 1] for t in dict.fromkeys(turns)]
            salience = event.get("salience")
            parts: Dict[str, float] = {}
            if isinstance(salience, dict):
                for key in ("emotion", "self", "consequence", "novelty", "explicit"):
                    try:
                        parts[key] = _clamp(float(salience.get(key, 0.0)))
                    except (TypeError, ValueError):
                        parts[key] = 0.0

            facts: List[Dict[str, Any]] = []
            for fact in event.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                speaker = reverse.get(str(fact.get("about") or ""))
                text = str(fact.get("text") or "").strip()[:80]
                if not speaker or not text:
                    continue
                fact_turns = [
                    int(t)
                    for t in fact.get("turns") or []
                    if str(t).isdigit() and 1 <= int(t) <= len(items)
                ]
                # Invariant 14: an assistant line can never be the source.
                if not any(
                    items[t - 1].actor == "user" and items[t - 1].speaker_id == speaker
                    for t in fact_turns
                ):
                    continue
                facts.append(
                    {
                        "about": speaker,
                        "kind": str(fact.get("kind") or "fact"),
                        "text": text,
                        "turns": fact_turns,
                    }
                )

            notes: List[Dict[str, Any]] = []
            for note in event.get("notes") or []:
                if not isinstance(note, dict):
                    continue
                speaker = reverse.get(str(note.get("about") or ""))
                text = str(note.get("text") or "").strip()[:80]
                if not speaker or not text:
                    continue
                note_turns = [
                    int(t)
                    for t in note.get("turns") or []
                    if str(t).isdigit() and 1 <= int(t) <= len(items)
                ]
                if not any(
                    items[t - 1].actor == "user" for t in note_turns
                ):
                    continue
                notes.append({"about": speaker, "text": text})

            out.append(
                EventGroup(
                    items=group_items,
                    gist=str(event.get("gist") or "").strip()[:200],
                    people=[
                        reverse.get(str(p))
                        for p in event.get("people") or []
                        if reverse.get(str(p))
                    ],
                    emotion=str(event.get("emotion") or "")[:20],
                    topics=[
                        str(t)[:24]
                        for t in event.get("topics") or []
                        if str(t).strip()
                    ][:6],
                    facts=facts,
                    notes=notes,
                    salience_parts=parts,
                )
            )
        return out

    # -- cards ---------------------------------------------------------------
    def _update_cards(
        self,
        group: EventGroup,
        engram: Engram,
        score: float,
        now: datetime,
    ) -> int:
        added = 0
        speakers = {
            i.speaker_id: i.speaker_name
            for i in group.items
            if i.actor == "user" and i.speaker_id
        }
        for speaker, name in speakers.items():
            entity = self.store.get_entity(self.scope, speaker) or Entity(
                scope=self.scope,
                entity_id=speaker,
                kind="participant",
                name=name or speaker,
            )
            for note in group.notes:
                if note.get("about") == speaker:
                    if add_note(
                        entity.card,
                        note["text"],
                        evidence=engram.engram_id,
                        at=now,
                        salience=score,
                    ):
                        added += 1
            for fact in group.facts:
                if fact.get("about") == speaker and fact.get("kind") in (
                    "preference",
                    "relation",
                ):
                    if add_note(
                        entity.card,
                        fact["text"],
                        evidence=engram.engram_id,
                        at=now,
                        salience=score,
                    ):
                        added += 1
            if not group.notes and not group.facts:
                # Rule fallback: stable preferences still reach the card.
                for item in group.items:
                    if item.speaker_id != speaker or item.actor != "user":
                        continue
                    for candidate in self.extractor.extract(
                        Engram(raw_text=item.text, meta={"actor": "user"})
                    ):
                        if candidate.kind in ("preference", "relation"):
                            if add_note(
                                entity.card,
                                candidate.text,
                                evidence=engram.engram_id,
                                at=now,
                                salience=score,
                            ):
                                added += 1
            entity.last_seen = now
            entity.interactions += 1
            self.store.upsert_entity(entity)

        context = self.store.get_entity(self.scope, group.items[0].context_id) or Entity(
            scope=self.scope,
            entity_id=group.items[0].context_id,
            kind="context",
            name=group.items[0].context_id,
        )
        for topic in group.topics:
            add_topic(context.card, topic, at=now)
        context.interactions += 1
        context.last_seen = now
        self.store.upsert_entity(context)
        return added


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    return json.loads(text[start : end + 1])


def _clamp(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
