"""Episode building: turn a stream of messages into memorable episodes.

Per-message writes make a 283-turn conversation 283 traces, each decaying on
its own. A companion remembers *episodes*: a burst of turns about one thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from .models import Event
from .tokens import jaccard, tokenize


@dataclass
class Episode:
    events: List[Event] = field(default_factory=list)
    scene: str = ""

    @property
    def started_at(self) -> datetime:
        return self.events[0].resolved_at()

    @property
    def ended_at(self) -> datetime:
        return self.events[-1].resolved_at()

    @property
    def text(self) -> str:
        parts = []
        for event in self.events:
            prefix = "我：" if event.actor == "assistant" else ""
            parts.append(prefix + event.text.strip())
        return "\n".join(parts)

    @property
    def refs(self) -> List[str]:
        refs: List[str] = []
        for event in self.events:
            refs.extend(event.meta.get("refs", []))
        return refs

    def __len__(self) -> int:
        return len(self.events)


class WindowSegmenter:
    """Close an episode on turn count, idle gap, or a day boundary."""

    def __init__(
        self,
        max_turns: int = 10,
        gap_minutes: float = 180.0,
        split_by_day: bool = True,
        topic_split: bool = True,
        topic_threshold: float = 0.10,
        topic_min_chars: int = 6,
    ) -> None:
        self.max_turns = max(1, int(max_turns))
        self.gap = timedelta(minutes=max(0.0, float(gap_minutes)))
        self.split_by_day = bool(split_by_day)
        self.topic_split = bool(topic_split)
        self.topic_threshold = max(0.0, min(1.0, float(topic_threshold)))
        self.topic_min_chars = max(1, int(topic_min_chars))
        self._events: List[Event] = []
        self._user_texts: List[str] = []

    def feed(self, event: Event) -> List[Episode]:
        """Add one event; return any episodes it closes."""
        closed: List[Episode] = []
        if self._events:
            prev = self._events[-1].resolved_at()
            now = event.resolved_at()
            gap_too_big = (now - prev) > self.gap
            day_changed = self.split_by_day and now.date() != prev.date()
            if gap_too_big or day_changed:
                closed.append(self._close())
            elif self._topic_changed(event):
                closed.append(self._close())
        self._events.append(event)
        if event.actor == "user":
            self._user_texts.append((event.text or "").strip())
        if len(self._events) >= self.max_turns:
            closed.append(self._close())
        return [episode for episode in closed if episode.events]

    def flush(self, now: Optional[datetime] = None) -> List[Episode]:
        if not self._events:
            return []
        return [self._close()]

    def _topic_changed(self, event: Event) -> bool:
        """Cheap topic-change detector for consecutive user turns.

        Assistant replies stay with the user turn that triggered them. Short
        fillers ("嗯嗯", "在吗") never split. This is intentionally a *low*
        threshold: over-splitting is safer than one diary-style episode.
        """
        if not self.topic_split or event.actor != "user" or not self._events:
            return False
        text = (event.text or "").strip()
        if len(text) < self.topic_min_chars or len(self._user_texts) < 1:
            return False
        current = tokenize(text)
        if not current:
            return False
        similarity = max(
            jaccard(current, tokenize(previous))
            for previous in self._user_texts[-5:]
        )
        return similarity < self.topic_threshold

    def _close(self) -> Episode:
        events = self._events
        self._events = []
        self._user_texts = []
        scene = events[0].scene if events else ""
        return Episode(events=events, scene=scene)

    @property
    def pending(self) -> int:
        return len(self._events)
