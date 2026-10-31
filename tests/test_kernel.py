from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hippocampus import Event, Hippocampus
from hippocampus import decay

T0 = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)


def at(days: float = 0.0, hours: float = 0.0) -> datetime:
    return T0 + timedelta(days=days, hours=hours)


def make(profile: str = "companion") -> Hippocampus:
    return Hippocampus(scope="test", profile=profile, path=":memory:")


def test_remember_and_recall() -> None:
    hippo = make()
    hippo.remember(Event(text="我数学考砸了，哭了好久", at=at()))
    hits = hippo.recall("数学", now=at(0, 1))
    assert hits, "expected at least one hit"
    assert "数学" in hits[0].text
    assert hits[0].level == 0


def test_retelling_merges_instead_of_duplicating() -> None:
    hippo = make()
    first = hippo.remember(Event(text="我养了十年的狗今天走了", at=at()))
    second = hippo.remember(Event(text="我养了十年的狗今天走了", at=at(1)))
    assert first.engram_id == second.engram_id
    assert hippo.count() == 1
    assert second.meta.get("mentions") == 2


def test_similar_but_distinct_events_stay_separate() -> None:
    hippo = make()
    a = hippo.remember(Event(text="我今天去了杭州西湖", at=at()))
    b = hippo.remember(Event(text="我今天去了南京玄武湖", at=at(1)))
    assert a.engram_id != b.engram_id
    assert hippo.count() == 2


def test_tick_is_idempotent() -> None:
    hippo = make()
    hippo.remember(Event(text="我爸妈离婚了", at=at()))
    hippo.tick(now=at(1))
    engram = hippo.store.list("test")[0]
    aged_once = engram.active_age_days
    hippo.tick(now=at(1))
    assert hippo.store.list("test")[0].active_age_days == aged_once


def test_tick_degrades_low_importance_memory() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="咖啡凉了", at=at()), importance=0.05)
    hippo.tick(now=at(1))
    hippo.tick(now=at(40))
    hippo.consolidate(now=at(40))
    assert hippo.store.get(engram.engram_id).level >= 3


def test_high_importance_never_fully_forgets() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="奶奶住院了，我很怕", at=at()), importance=0.95)
    for day in range(1, 400, 20):
        hippo.tick(now=at(day))
    hippo.consolidate(now=at(400))
    stored = hippo.store.get(engram.engram_id)
    assert stored.level <= 2, "important memories must keep a floor"
    assert stored.level >= 1, "but they are allowed to blur"


def test_idle_time_decays_slower_than_active_time() -> None:
    active = make()
    idle = make()
    active.remember(Event(text="我在学吉他", at=at()))
    idle.remember(Event(text="我在学吉他", at=at()))

    # The first tick only establishes the baseline clock.
    active.tick(now=at(0))
    idle.tick(now=at(0))
    active.tick(now=at(10), active=True)
    idle.tick(now=at(10), active=False)

    a = active.store.list("test")[0].active_age_days
    i = idle.store.list("test")[0].active_age_days
    assert i < a
    assert i == a * active.profile.idle_ratio or abs(i - a * 0.05) < 0.2


def test_recall_strengthens_stability() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="我被裁员了，存款只够三个月", at=at()))
    before = hippo.store.get(engram.engram_id).stability
    hippo.tick(now=at(0))
    hippo.tick(now=at(2))
    hippo.recall("裁员", now=at(3))
    after = hippo.store.get(engram.engram_id).stability
    assert after > before
    assert hippo.store.get(engram.engram_id).recall_count == 1


def test_mention_cooldown_penalises_repeats() -> None:
    hippo = make()
    hippo.remember(Event(text="我搬去成都了", at=at()))
    first = hippo.recall("成都", now=at(1))
    assert first
    second = hippo.recall("成都", now=at(1, 1), reinforce=False)
    fresh = hippo.recall("成都", now=at(2), reinforce=False)
    assert second[0].score < fresh[0].score


def test_forget_hard_deletes_everything() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="这是我的密码", at=at()))
    assert hippo.forget(engram.engram_id, hard=True)
    assert hippo.store.get(engram.engram_id) is None
    assert hippo.recall("密码") == []
    assert not hippo.forget(engram.engram_id)


def test_soft_forget_keeps_an_impression() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="我说过我想学画画", at=at()))
    hippo.forget(engram.engram_id, hard=False)
    stored = hippo.store.get(engram.engram_id)
    assert stored is not None
    assert stored.level == hippo.max_level
    assert stored.raw_text == ""


def test_render_never_returns_verbatim_when_foggy() -> None:
    hippo = make()
    engram = hippo.remember(Event(text="我确诊焦虑症了，医生让我开始吃药", at=at()))
    engram.level = 3
    rendered = engram.render(now=at(90))
    assert "确诊" not in rendered
    assert "吃药" not in rendered
    assert "记不清" in rendered or "隐约" in rendered


def test_levels_are_profile_specific() -> None:
    support = make("support")
    assert support.max_level == 2
    companion = make("companion")
    assert companion.max_level == 4


def test_decay_curve_monotonic() -> None:
    prev = 1.0
    for day in range(0, 100, 5):
        r = decay.retrievability(day, 2.0)
        assert r <= prev
        prev = r


def test_spontaneous_recall_hides_wounds_and_fog() -> None:
    hippo = make()
    hippo.remember(Event(text="我确诊焦虑症了，医生让我吃药", at=at()), importance=0.9)
    hippo.remember(Event(text="我拿到offer了，开心到睡不着", at=at()), importance=0.9)

    # She can answer when asked...
    asked = hippo.recall("焦虑症", now=at(30), reinforce=False)
    assert asked, "a direct question must reach even a painful memory"

    # ...but will not bring the painful one up by herself.
    unbidden = hippo.recall("焦虑症", now=at(30), reinforce=False, spontaneous=True)
    assert all("确诊" not in hit.text for hit in unbidden)


def test_intimacy_unlocks_spontaneous_mention() -> None:
    hippo = make()
    hippo.remember(Event(text="我爸妈离婚了，我不知道跟谁", at=at()), importance=0.9)
    hippo.set_relationship(intimacy=0.9)
    hits = hippo.recall("爸妈", now=at(30), reinforce=False, spontaneous=True)
    assert any("离婚" in hit.text for hit in hits)
