"""Episode building: turn a stream of messages into memorable episodes.

Per-message writes make a 283-turn conversation 283 traces, each decaying on
its own. A companion remembers *episodes*: a burst of turns about one thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from .models import Event


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
    ) -> None:
        self.max_turns = max(1, int(max_turns))
        self.gap = timedelta(minutes=max(0.0, float(gap_minutes)))
        self.split_by_day = bool(split_by_day)
        self._events: List[Event] = []

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
        self._events.append(event)
        if len(self._events) >= self.max_turns:
            closed.append(self._close())
        return [episode for episode in closed if episode.events]

    def flush(self, now: Optional[datetime] = None) -> List[Episode]:
        if not self._events:
            return []
        return [self._close()]

    def _close(self) -> Episode:
        events = self._events
        self._events = []
        scene = events[0].scene if events else ""
        return Episode(events=events, scene=scene)

    @property
    def pending(self) -> int:
        return len(self._events)
