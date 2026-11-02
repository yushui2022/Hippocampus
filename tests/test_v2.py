from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from hippocampus import (
    Event,
    FactCandidate,
    HashingEmbedder,
    Hippocampus,
    Store,
    Summary,
    snapshot,
)
from hippocampus.summarizer import LLMSummarizer, SummaryError, parse_summary
from hippocampus.decay import S_MAX

T0 = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)


def at(days: float = 0.0, hours: float = 0.0) -> datetime:
    return T0 + timedelta(days=days, hours=hours)


def tick_days(h: Hippocampus, days: int, active: bool = True) -> None:
    """Advance the clock one real day at a time (with the day marked active)."""
    h.tick(at(0))
    for day in range(1, days + 1):
        h.tick(
            at(day),
            active=active,
            active_days={at(day - 1).date(), at(day).date()},
        )


def make(profile: str = "companion", embedder=None) -> Hippocampus:
    return Hippocampus(
        scope="test",
        profile=profile,
        path=":memory:",
        embedder=embedder,
    )


class FakeSummarizer:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self.persona = None

    def summarize(self, events, persona=None):
        self.calls += 1
        self.persona = persona
        if self.delay:
            time.sleep(self.delay)
        return Summary(
            gist="三月那次数学考砸，我哭了好久，觉得自己特别没用。",
            key_facts=["数学考砸", "哭了很久"],
            topics=["数学", "情绪"],
            sentiment="negative",
            importance=0.8,
            emotion="难过",
        )


class SlowEmbedder:
    """Deterministic embedder with a configurable delay."""

    model = "slow-test"
    dim = 4

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay

    def embed(self, texts):
        time.sleep(self.delay)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class SynonymEmbedder:
    """Tiny deterministic 'semantic' embedder for pipeline tests."""

    model = "synonym-test"
    dim = 8
    CLUSTERS = [
        ("学不动", "数学", "考砸", "成绩", "考试"),
        ("猫", "团子", "宠物"),
        ("海", "海边", "旅行"),
    ]

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for index, words in enumerate(self.CLUSTERS):
                if any(word in text for word in words):
                    vec[index] = 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


# --------------------------------------------------------------------------- #
# v0 behaviour must not regress
# --------------------------------------------------------------------------- #
class TestBasics(unittest.TestCase):
    def test_remember_and_recall(self):
        h = make()
        e = h.remember(Event("我数学考砸了，哭了好久", at=at(0)))
        hits = h.recall("数学考砸了", now=at(0))
        self.assertTrue(any(hit.engram.engram_id == e.engram_id for hit in hits))

    def test_retelling_merges(self):
        h = make()
        h.remember(Event("我数学考砸了，哭了好久", at=at(0)))
        h.remember(Event("我数学考砸了，哭了好久", at=at(1)))
        self.assertEqual(h.count(), 1)
        e = h.store.list("test")[0]
        self.assertEqual(e.meta.get("mentions"), 2)

    def test_similar_but_distinct_events_stay_separate(self):
        h = make()
        h.remember(Event("我去了杭州西湖", at=at(0)))
        h.remember(Event("我去了南京玄武湖", at=at(1)))
        self.assertEqual(h.count(), 2)

    def test_high_importance_has_floor(self):
        h = make()
        e = h.remember(Event("我确诊了焦虑症，这件事对我很重要", at=at(0)), importance=0.95)
        h.tick(at(0))
        h.tick(at(365), active=True)
        self.assertLessEqual(h.store.get(e.engram_id).level, 2)

    def test_forget_hard_and_soft(self):
        h = make()
        e1 = h.remember(Event("我喜欢猫", at=at(0)))
        e2 = h.remember(Event("我养了一只狗叫豆豆", at=at(1)))
        self.assertTrue(h.forget(e1.engram_id, hard=False))
        soft = h.store.get(e1.engram_id)
        self.assertEqual(soft.raw_text, "")
        self.assertTrue(h.forget(e2.engram_id, hard=True))
        self.assertIsNone(h.store.get(e2.engram_id))

    def test_tick_is_idempotent(self):
        h = make()
        e = h.remember(Event("今天喝了奶茶", at=at(0)), importance=0.4)
        h.tick(at(0))
        h.tick(at(10))
        first = h.store.get(e.engram_id).active_age_days
        h.tick(at(10))
        self.assertAlmostEqual(first, h.store.get(e.engram_id).active_age_days, places=9)


# --------------------------------------------------------------------------- #
# P0 invariants
# --------------------------------------------------------------------------- #
class TestInvariants(unittest.TestCase):
    def test_stability_cap(self):
        h = make()
        e = h.remember(Event("我确诊了焦虑症，这件事对我很重要", at=at(0)), importance=1.0)
        h.tick(at(0))
        for day in (20, 40, 60, 90, 150, 300):
            h.tick(at(day))
            h.recall("焦虑症", now=at(day), reinforce=True)
            self.assertLessEqual(h.store.get(e.engram_id).stability, S_MAX + 1e-9)

    def test_tick_catchup_uses_idle(self):
        h = make()
        e = h.remember(Event("今天喝了杯奶茶", at=at(0)), importance=0.3)
        h.tick(at(0))
        h.tick(at(90), active=False)  # a single catch-up after 90 silent days
        engram = h.store.get(e.engram_id)
        self.assertAlmostEqual(engram.active_age_days, 90 * 0.05, places=3)
        self.assertLess(engram.level, 3)

    def test_fidelity_monotonic(self):
        h = make()
        e = h.remember(Event("我数学考砸了，哭了好久，觉得自己特别没用", at=at(0)), importance=0.5)
        h.tick(at(0))
        last = 0
        for day in range(1, 200, 5):
            h.tick(at(day))
            current = h.store.get(e.engram_id).fidelity
            self.assertGreaterEqual(current, last)
            last = current

    def test_l1_recall_does_not_restore_raw(self):
        h = make()
        raw = "我数学考砸了，哭了好久，觉得自己特别没用"
        e = h.remember(Event(raw, at=at(0)), importance=0.45)
        tick_days(h, 120)
        before = h.store.get(e.engram_id)
        self.assertEqual(before.fidelity, 1)
        h.recall("数学考砸了", now=at(120), reinforce=True)
        after = h.store.get(e.engram_id)
        self.assertGreaterEqual(after.level, after.fidelity)
        self.assertNotEqual(after.render(now=at(120)), raw)

    def test_peek_is_read_only(self):
        h = make()
        h.remember(Event("我喜欢猫", at=at(0)))
        before = h.store.consistency_report()
        stats_before = h.store.recall_stats("test")
        h.peek("猫", now=at(0))
        self.assertEqual(before, h.store.consistency_report())
        self.assertEqual(stats_before, h.store.recall_stats("test"))

    def test_default_recall_does_not_reinforce(self):
        h = make()
        e = h.remember(Event("我养了一只猫叫团子", at=at(0)))
        before = h.store.get(e.engram_id).stability
        h.recall("猫", now=at(0))
        self.assertEqual(before, h.store.get(e.engram_id).stability)

    def test_mention_is_separate_from_recall(self):
        h = make()
        e = h.remember(Event("我养了一只猫叫团子", at=at(0)))
        h.recall("猫", now=at(0), reinforce=True)
        self.assertIsNone(h.store.get(e.engram_id).last_mention_at)
        h.mark_mentioned([e.engram_id], now=at(0))
        self.assertIsNotNone(h.store.get(e.engram_id).last_mention_at)
        h.tick(at(0))
        h.tick(at(0, hours=1))
        hi = h.recall("猫", now=at(0, hours=1), spontaneous=True)
        self.assertTrue(all(hit.engram.engram_id != e.engram_id for hit in hi))

    def test_gist_source_is_honest(self):
        h = make()
        raw = "我数学考砸了，哭了好久，觉得自己特别没用"
        e = h.remember(Event(raw, at=at(0)), importance=0.45)
        tick_days(h, 120)
        engram = h.store.get(e.engram_id)
        self.assertIn(engram.level, (1, 2))
        self.assertNotEqual(engram.render(now=at(120)), raw)
        self.assertLessEqual(len(engram.render(now=at(120))), len(raw))

    def test_retelling_recovers_one_level(self):
        h = make()
        text = "我搬去成都了，这边一直下雨，很潮湿"
        e = h.remember(Event(text, at=at(0)), importance=0.25)
        tick_days(h, 120)
        before = h.store.get(e.engram_id)
        self.assertGreater(before.level, 0)
        h.remember(Event(text, at=at(120)))
        after = h.store.get(e.engram_id)
        self.assertEqual(after.fidelity, max(0, before.fidelity - 1))
        self.assertLessEqual(after.level, before.level)

    def test_importance_can_be_forced_down(self):
        h = make()
        e = h.remember(Event("我喜欢猫", at=at(0)), importance=0.9)
        h.set_importance(e.engram_id, 0.2)
        self.assertEqual(h.store.get(e.engram_id).importance, 0.9)
        h.set_importance(e.engram_id, 0.2, force=True)
        self.assertAlmostEqual(h.store.get(e.engram_id).importance, 0.2)

    def test_tokens_do_not_grow_on_retelling(self):
        h = make()
        text = "我数学考砸了，哭了好久"
        h.remember(Event(text, at=at(0)))
        size = len(h.store.get(h.store.list("test")[0].engram_id).tokens)
        for day in range(1, 10):
            h.remember(Event(text, at=at(day)))
        self.assertEqual(len(h.store.list("test")[0].tokens), size)

    def test_delete_cascades(self):
        h = make(embedder=HashingEmbedder())
        e = h.remember(Event("我不吃香菜", at=at(0)))
        h.recall("香菜", now=at(0), reinforce=True)
        h.mark_mentioned([e.engram_id], now=at(0))
        h.forget(e.engram_id, hard=True)
        report = h.store.consistency_report()
        self.assertEqual(report["engrams"], 0)
        self.assertEqual(report["vectors"], 0)
        self.assertEqual(report["facts"], 0)
        self.assertEqual(h.store.recall_stats("test")["total"], 0)


# --------------------------------------------------------------------------- #
# P1: retrieval, episodes
# --------------------------------------------------------------------------- #
class TestRetrieval(unittest.TestCase):
    def test_paraphrase_with_embedder(self):
        h = make(embedder=SynonymEmbedder())
        e = h.remember(Event("我数学考砸了，哭了好久", at=at(0)))
        hits = h.recall("最近学不动了", now=at(0))
        self.assertTrue(any(hit.engram.engram_id == e.engram_id for hit in hits))

    def test_without_embedder_paraphrase_is_missed(self):
        h = make()
        h.remember(Event("我数学考砸了，哭了好久", at=at(0)))
        hits = h.recall("最近状态很差", now=at(0))
        self.assertFalse(any("考砸" in hit.text for hit in hits))

    def test_char_budget(self):
        h = make()
        for i in range(6):
            h.remember(Event("第{}条：".format(i) + "很长的一段话" * 30, at=at(i)), importance=0.6)
        hits = h.recall("很长", budget=6, max_chars=80, now=at(6))
        self.assertTrue(hits)
        self.assertLessEqual(sum(len(hit.text) for hit in hits), 80 + 20)

    def test_episode_segmenter(self):
        h = make()
        events = [
            Event("我去了海边，风很大", at=at(0, hours=0)),
            Event("我吃了一顿火锅", at=at(0, hours=1)),
            Event("我看了一部电影", at=at(0, hours=2)),
        ]
        h.feed_episode(events[0], max_turns=2, topic_split=False)
        h.feed_episode(events[1], max_turns=2, topic_split=False)
        h.feed_episode(events[2], max_turns=2, topic_split=False)
        self.assertEqual(h.count(), 1)
        h.flush_episode()
        self.assertEqual(h.count(), 2)

    def test_migration_from_v0(self):
        path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE engrams (
                engram_id TEXT PRIMARY KEY, scope TEXT, kind TEXT, raw_text TEXT,
                gist TEXT, emotion TEXT, level INTEGER, importance REAL,
                stability REAL, difficulty REAL, active_age_days REAL,
                recall_count INTEGER, created_at TEXT, last_recall_at TEXT,
                last_mention_at TEXT, refs TEXT, meta TEXT
            );
            CREATE TABLE engram_tokens (engram_id TEXT, token TEXT);
            CREATE TABLE scope_state (scope TEXT PRIMARY KEY, profile TEXT, state TEXT, updated_at TEXT);
            CREATE TABLE recall_log (id INTEGER PRIMARY KEY, scope TEXT, engram_id TEXT, query TEXT, at TEXT);
            """
        )
        conn.execute(
            "INSERT INTO engrams VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old1", "s", "episodic", "旧记忆", "", "", 2, 0.5, 1.0, 5.0, 3.0, 0, at(0).isoformat(), None, None, "[]", "{}"),
        )
        conn.commit()
        conn.close()
        store = Store(path)
        self.assertEqual(store.schema_version(), 5)
        old = store.get("old1")
        self.assertEqual(old.fidelity, 2)
        self.assertEqual(old.gist_source, "none")
        store.close()
        os.unlink(path)

    def test_inspect_is_read_only(self):
        path = tempfile.mktemp(suffix=".db")
        h = Hippocampus(scope="s", path=path)
        h.remember(Event("我喜欢猫", at=at(0)))
        h.close()
        data = snapshot(path)
        self.assertTrue(data["read_only"])
        self.assertEqual(data["counts"]["engrams"], 1)
        os.unlink(path)


# --------------------------------------------------------------------------- #
# P2: facts, salience
# --------------------------------------------------------------------------- #
class TestFacts(unittest.TestCase):
    def test_preference_extraction_keeps_negation(self):
        h = make()
        h.remember(Event("我不吃香菜", at=at(0)))
        facts = h.facts(now=at(0))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].predicate, "不吃")
        self.assertEqual(facts[0].obj, "香菜")

    def test_assistant_text_never_creates_facts(self):
        h = make()
        h.remember(Event("我不吃香菜", actor="assistant", at=at(0)))
        self.assertEqual(h.facts(now=at(0)), [])

    def test_new_value_supersedes_old(self):
        h = make()
        h.remember(Event("我喜欢猫", at=at(0)))
        h.remember(Event("我喜欢狗", at=at(1)))
        active = h.facts(now=at(1))
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].obj, "狗")
        all_facts = h.facts(now=at(1), include_inactive=True)
        statuses = sorted(f.status for f in all_facts)
        self.assertEqual(statuses, ["active", "superseded"])

    def test_plan_expires(self):
        h = make()
        h.add_fact(
            FactCandidate(
                kind="plan",
                subject="主人",
                predicate="明天",
                obj="交材料",
                text="我明天交材料",
                confidence=0.8,
            ),
            now=at(0),
        )
        self.assertEqual(len(h.facts(now=at(0))), 1)
        h.tick(at(0))
        h.consolidate(now=at(3))
        self.assertEqual(len(h.facts(now=at(3))), 0)

    def test_salience_suppresses_negative_and_respects_budget(self):
        h = make()
        h.remember(Event("我今天特别开心，拿到了offer", at=at(0)), importance=0.8)
        h.remember(Event("我很难过，哭了一整天", at=at(0)), importance=0.8)
        h.tick(at(0))
        h.tick(at(1), active=True)
        hits = h.salient(now=at(1))
        self.assertTrue(all("难过" not in hit.text for hit in hits))
        if hits:
            h.mark_mentioned([hit.engram.engram_id for hit in hits], now=at(1))
        for hit in hits:
            h.mark_mentioned([hit.engram.engram_id], now=at(1))
        # Budget is per day; after exhausting it, nothing is volunteered.
        if len(hits) >= h.profile.mention_budget_per_day:
            self.assertEqual(h.salient(now=at(1)), [])


if __name__ == "__main__":
    unittest.main()


class TestTimeAndOps(unittest.TestCase):
    def test_daily_tick_matches_single_catchup(self):
        daily = make()
        once = make()
        for h in (daily, once):
            h.remember(Event("我数学考砸了，哭了好久", at=at(0)), importance=0.6)
            h.tick(at(0))
        for day in range(1, 31):
            daily.tick(at(day), active_days={at(day - 1).date(), at(day).date()})
        once.tick(at(30), active_days={at(day).date() for day in range(0, 31)})
        a = daily.store.list("test")[0]
        b = once.store.list("test")[0]
        self.assertAlmostEqual(a.active_age_days, b.active_age_days, places=6)
        self.assertEqual(a.level, b.level)
        self.assertEqual(a.fidelity, b.fidelity)

    def test_correct_reindexes_text(self):
        h = make()
        e = h.remember(Event("我喜欢猫", at=at(0)))
        h.correct(e.engram_id, text="我不吃香菜")
        self.assertEqual(h.store.get(e.engram_id).raw_text, "我不吃香菜")
        self.assertTrue(any("香菜" in hit.text for hit in h.recall("香菜", now=at(0))))

    def test_consolidate_fills_extractive_gist(self):
        h = make()
        e = h.remember(Event("我数学考砸了，哭了好久，觉得自己特别没用", at=at(0)), importance=0.5)
        tick_days(h, 120)
        engram = h.store.get(e.engram_id)
        self.assertEqual(engram.gist, "")
        h.consolidate(now=at(120))
        engram = h.store.get(e.engram_id)
        self.assertEqual(engram.gist_source, "extractive")
        self.assertTrue(engram.gist)

    def test_backup_and_restore(self):
        src = tempfile.mktemp(suffix=".db")
        bak = tempfile.mktemp(suffix=".db")
        h = Hippocampus(scope="s", path=src)
        h.remember(Event("我喜欢猫", at=at(0)))
        h.store.backup(bak)
        h.close()
        restored = Store(bak)
        self.assertEqual(restored.count("s"), 1)
        restored.close()
        for path in (src, bak):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_budget_zero(self):
        h = make()
        h.remember(Event("我喜欢猫", at=at(0)))
        self.assertEqual(h.recall("猫", budget=0), [])
        self.assertEqual(h.salient(budget=0), [])


class TestAsyncIndex(unittest.TestCase):
    def test_remember_does_not_block_and_close_flushes(self):
        path = tempfile.mktemp(suffix=".db")
        h = Hippocampus(
            scope="s",
            path=path,
            embedder=SlowEmbedder(delay=0.08),
            async_index=True,
        )
        started = time.perf_counter()
        h.remember(Event("我数学考砸了，哭了好久", at=at(0)))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05)  # embedding happens in the background
        h.close()

        store = Store(path)
        self.assertEqual(store.vector_count("s"), 1)
        store.close()
        os.unlink(path)

    def test_async_recall_drains_finished_vectors(self):
        path = tempfile.mktemp(suffix=".db")
        h = Hippocampus(
            scope="s",
            path=path,
            embedder=SlowEmbedder(delay=0.01),
            async_index=True,
        )
        e = h.remember(Event("我养了一只猫叫团子", at=at(0)))
        deadline = time.time() + 2
        while h.store.vector_count("s") == 0 and time.time() < deadline:
            h._drain_index()
            time.sleep(0.01)
        self.assertEqual(h.store.vector_count("s"), 1)
        self.assertTrue(h.recall("猫", now=at(0)))
        h.close()
        os.unlink(path)


class TestRebuild(unittest.TestCase):
    def test_rebuild_after_model_change(self):
        path = tempfile.mktemp(suffix=".db")
        old = Hippocampus(
            scope="s", path=path, embedder=HashingEmbedder(dim=8), async_index=False
        )
        ids = [
            old.remember(Event("我数学考砸了，哭了好久", at=at(0))).engram_id,
            old.remember(Event("我养了一只猫叫团子", at=at(1))).engram_id,
            old.remember(Event("我不吃香菜", at=at(2))).engram_id,
        ]
        self.assertEqual(old.store.vector_dim(ids[0]), 8)
        old.close()

        new = Hippocampus(
            scope="s", path=path, embedder=HashingEmbedder(dim=16), async_index=False
        )
        self.assertTrue(new._state.get("needs_vector_rebuild"))
        report = new.rebuild_vectors(batch_size=2)
        self.assertEqual(report["vectors"], 3)
        self.assertEqual(new.store.vector_count("s"), 3)
        self.assertEqual(new.store.vector_dim(ids[0]), 16)
        self.assertFalse(new._state.get("needs_vector_rebuild"))
        self.assertTrue(new.recall("数学考砸了", now=at(2)))
        new.close()
        os.unlink(path)


class TestTopicBudget(unittest.TestCase):
    def test_same_topic_is_not_volunteered_twice(self):
        h = make()
        cat = h.remember(
            Event("我养了一只猫叫团子", at=at(0), meta={"topics": ["猫"]}),
            importance=0.8,
        )
        work = h.remember(
            Event("我拿到 offer 了，下周入职", at=at(0), meta={"topics": ["工作"]}),
            importance=0.8,
        )
        h.tick(at(0))
        h.tick(at(1), active=True)
        h.mark_mentioned([cat.engram_id], now=at(1))
        hits = h.salient(now=at(1))
        ids = [hit.engram.engram_id for hit in hits]
        self.assertNotIn(cat.engram_id, ids)
        self.assertIn(work.engram_id, ids)
        # After the topic cooldown, it may surface again.
        later = h.salient(now=at(1) + timedelta(hours=25))
        self.assertIn(cat.engram_id, [hit.engram.engram_id for hit in later])


class TestGraph(unittest.TestCase):
    def test_graph_edges_are_optional(self):
        off = make()
        off.remember(Event("我养了一只猫叫团子", at=at(0)))
        self.assertEqual(off.store.count_edges("test"), 0)

        import dataclasses
        from hippocampus import get_profile

        profile = dataclasses.replace(get_profile("companion"), enable_graph=True)
        on = Hippocampus(scope="test", profile=profile, path=":memory:")
        e = on.remember(Event("我养了一只猫叫团子", at=at(0)))
        self.assertGreaterEqual(on.store.count_edges("test"), 1)
        ids = on.store.graph_candidates("test", {"猫"}, on.max_level)
        self.assertIn(e.engram_id, ids)


class TestShadowRebuild(unittest.TestCase):
    def test_failed_shadow_rebuild_keeps_old_vectors(self):
        path = tempfile.mktemp(suffix=".db")
        old = Hippocampus(
            scope="s", path=path, embedder=HashingEmbedder(dim=8), async_index=False
        )
        ids = [
            old.remember(Event("我数学考砸了，哭了好久", at=at(0))).engram_id,
            old.remember(Event("我养了一只猫叫团子", at=at(1))).engram_id,
        ]
        old.close()

        class BrokenEmbedder:
            model = "broken"
            dim = 8

            def embed(self, texts):
                raise RuntimeError("provider down")

        new = Hippocampus(
            scope="s", path=path, embedder=BrokenEmbedder(), async_index=False
        )
        with self.assertRaises(RuntimeError):
            new.rebuild_vectors()
        self.assertEqual(new.store.vector_count("s"), 2)
        self.assertEqual(new.store.vector_dim(ids[0]), 8)
        new.close()
        os.unlink(path)


class TestObservatory(unittest.TestCase):
    def test_read_only_http_surface(self):
        import json
        import threading
        import urllib.request

        from hippocampus.observatory import create_server

        path = tempfile.mktemp(suffix=".db")
        h = Hippocampus(scope="s", path=path)
        h.remember(Event("我不吃香菜", at=at(0)))
        h.close()

        server = create_server(path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        try:
            with urllib.request.urlopen(base + "/api/snapshot", timeout=5) as resp:
                data = json.load(resp)
            self.assertTrue(data["read_only"])
            self.assertEqual(data["counts"]["engrams"], 1)
            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                page = resp.read().decode("utf-8")
            self.assertIn("只读", page)
        finally:
            server.shutdown()
            server.server_close()
            os.unlink(path)


class TestBlindHarness(unittest.TestCase):
    def test_vote_counting(self):
        from eval.blind_test import run

        pairs = [
            {
                "id": "remember:x",
                "group": "remember",
                "query": "你还记得吗",
                "a": "模糊版本",
                "b": "原文",
                "a_first": True,
            }
        ]
        self.assertEqual(run(pairs, lambda _p: "A")["plastic_win_rate"], 1.0)
        self.assertEqual(run(pairs, lambda _p: "skip")["plastic_win_rate"], 0.0)


class TestSummarizer(unittest.TestCase):
    def test_parse_summary_handles_fences_and_clamps(self):
        raw = """```json
        {"gist":"我很难过","key_facts":["数学考砸"],"topics":["数学"],
         "sentiment":"negative","importance":1.7,"emotion":"难过"}
        ```"""
        summary = parse_summary(raw)
        self.assertEqual(summary.gist, "我很难过")
        self.assertEqual(summary.importance, 1.0)
        self.assertEqual(summary.topics, ["数学"])
        with self.assertRaises(SummaryError):
            parse_summary("not json")
        with self.assertRaises(SummaryError):
            parse_summary('{"topics": []}')

    def test_llm_summarizer_uses_transport(self):
        captured = []

        def transport(messages):
            captured.append(messages)
            return '{"gist":"我记得那次考试","key_facts":["考试"],"topics":["考试"],"sentiment":"negative","importance":0.7,"emotion":"难过"}'

        client = LLMSummarizer(api_key="test", transport=transport)
        summary = client.summarize(
            [Event("我数学考砸了", at=at(0))], persona="温柔、嘴硬的鲸鱼娘"
        )
        self.assertEqual(summary.gist, "我记得那次考试")
        self.assertIn("第一人称", captured[0][0]["content"])
        self.assertIn("鲸鱼娘", captured[0][0]["content"])
        self.assertIn("数学考砸", captured[0][1]["content"])

    def test_failed_summarizer_falls_back_to_extractive(self):
        class Broken:
            def summarize(self, events, persona=None):
                raise RuntimeError("api down")

        h = Hippocampus(
            scope="s", path=":memory:", summarizer=Broken(), async_summary=False
        )
        e = h.remember_episode([Event("我数学考砸了，哭了好久", at=at(0))], summarize=True)
        self.assertEqual(e.gist_source, "extractive")
        self.assertTrue(e.gist)

    def test_sync_summarizer_applies_first_person_gist(self):
        fake = FakeSummarizer()
        h = Hippocampus(
            scope="s",
            path=":memory:",
            summarizer=fake,
            async_summary=False,
            persona="温柔、嘴硬的鲸鱼娘",
        )
        e = h.remember_episode(
            [Event("我数学考砸了，哭了好久", at=at(0))], summarize=True
        )
        stored = h.store.get(e.engram_id)
        self.assertEqual(stored.gist_source, "llm")
        self.assertIn("数学考砸", stored.gist)
        self.assertEqual(stored.meta["topics"], ["数学", "情绪"])
        self.assertEqual(fake.persona, "温柔、嘴硬的鲸鱼娘")

    def test_episode_records_fact_keys(self):
        h = make()
        e = h.remember_episode(
            [
                Event("我数学考砸了", at=at(0), meta={"fact": "数学"}),
                Event("嗯嗯", actor="assistant", at=at(0, hours=0.02)),
            ]
        )
        self.assertIn("数学", e.meta["facts"])

    def test_async_summarizer_flushes_on_close(self):
        path = tempfile.mktemp(suffix=".db")
        fake = FakeSummarizer(delay=0.05)
        h = Hippocampus(
            scope="s", path=path, summarizer=fake, async_summary=True
        )
        e = h.remember_episode(
            [Event("我数学考砸了，哭了好久", at=at(0))], summarize=True
        )
        self.assertEqual(h.store.get(e.engram_id).gist_source, "extractive")
        h.close()
        store = Store(path)
        self.assertEqual(store.get(e.engram_id).gist_source, "llm")
        store.close()
        os.unlink(path)


class TestDialogueFormat(unittest.TestCase):
    def test_sender_name_is_used(self):
        from hippocampus.summarizer import format_dialogue

        text = format_dialogue(
            [
                Event("我数学考砸了", at=at(0), meta={"sender_name": "阿澈"}),
                Event("别难过", actor="assistant", at=at(0, hours=0.02)),
            ]
        )
        self.assertIn("阿澈: 我数学考砸了", text)
        self.assertIn("我: 别难过", text)
        self.assertNotIn("对方", text)


class TestTopicSplit(unittest.TestCase):
    def test_unrelated_messages_become_separate_episodes(self):
        h = make()
        h.feed_episode(Event("我数学考砸了，哭了好久", at=at(0)), max_turns=10)
        h.feed_episode(Event("我拿到那家公司的offer了，开心到睡不着", at=at(0, hours=1)), max_turns=10)
        h.flush_episode()
        self.assertEqual(h.count(), 2)

    def test_related_messages_stay_together(self):
        h = make()
        h.feed_episode(Event("我数学考砸了，哭了好久", at=at(0)), max_turns=10)
        h.feed_episode(Event("数学一直学不好，我很焦虑", at=at(0, hours=1)), max_turns=10)
        h.flush_episode()
        self.assertEqual(h.count(), 1)

    def test_short_filler_does_not_split(self):
        h = make()
        h.feed_episode(Event("我数学考砸了，哭了好久", at=at(0)), max_turns=10)
        h.feed_episode(Event("嗯嗯", at=at(0, hours=1)), max_turns=10)
        h.flush_episode()
        self.assertEqual(h.count(), 1)

    def test_topic_split_can_be_disabled(self):
        h = make()
        h.feed_episode(Event("我数学考砸了，哭了好久", at=at(0)), max_turns=10, topic_split=False)
        h.feed_episode(Event("我拿到那家公司的offer了，开心到睡不着", at=at(0, hours=1)), max_turns=10)
        h.flush_episode()
        self.assertEqual(h.count(), 1)


class TestSelfModel(unittest.TestCase):
    def test_merge_render_and_persist(self):
        from hippocampus.self_model import SelfModel, parse_reflection

        store = Store(":memory:")
        model = SelfModel(store, "s")
        model.merge(
            {
                "traits": ["嘴硬心软", "爱吐槽"],
                "user_facts": ["阿澈最近在找工作"],
                "inside_jokes": ["牛来"],
                "relationship": ["会互相拆台"],
                "style_notes": ["句子很短"],
            }
        )
        rendered = model.render()
        self.assertIn("嘴硬心软", rendered)
        self.assertIn("牛来", rendered)

        loaded = SelfModel(store, "s")
        self.assertIn("阿澈最近在找工作", loaded.render())

        data = parse_reflection(
            '```json\n{"traits":["更会怼人了"],"user_facts":["阿澈怕黑"]}\n```'
        )
        self.assertEqual(data["traits"], ["更会怼人了"])
        loaded.merge(data)
        self.assertIn("更会怼人了", loaded.render())
        store.close()

    def test_pending_tracks_reflected_engrams(self):
        from hippocampus.self_model import SelfModel

        store = Store(":memory:")
        h = Hippocampus(scope="s", store=store)
        e1 = h.remember(Event("我数学考砸了", at=at(0)))
        e2 = h.remember(Event("我养了一只猫", at=at(1)))
        model = SelfModel(store, "s")
        self.assertEqual(len(model.pending(store.list("s"))), 2)
        model.mark_reflected([e1])
        pending = model.pending(store.list("s"))
        self.assertEqual([e.engram_id for e in pending], [e2.engram_id])
        store.close()
