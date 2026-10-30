"""Hippocampus -- a memory kernel that forgets like a human.

    from hippocampus import Hippocampus, Event

    hippo = Hippocampus(scope="user_42", profile="companion")
    hippo.remember(Event(text="我数学考砸了，哭了好久"))
    hits = hippo.recall("最近有点学不动了")
"""

from __future__ import annotations

from .kernel import Hippocampus
from .models import Engram, Event, RecallHit
from .profiles import PRESETS, Profile, get_profile
from .store import Store

__version__ = "0.0.1"

__all__ = [
    "Hippocampus",
    "Event",
    "Engram",
    "RecallHit",
    "Profile",
    "PRESETS",
    "get_profile",
    "Store",
    "__version__",
]
