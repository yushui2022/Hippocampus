from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from hippocampus import (
    Event,
    FactCandidate,
    HashingEmbedder,
    Hippocampus,
    Store,
    snapshot,
)
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
        e = h.remember(Event(raw, at=at(0)), importance=0.6)
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
        e = h.remember(Event(raw, at=at(0)), importance=0.6)
        tick_days(h, 120)
        engram = h.store.get(e.engram_id)
        self.assertIn(engram.level, (1, 2))
        self.assertNotEqual(engram.render(now=at(120)), raw)
        self.assertLessEqual(len(engram.render(now=at(120))), len(raw))

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
        h.feed_episode(events[0], max_turns=2)
        h.feed_episode(events[1], max_turns=2)
        h.feed_episode(events[2], max_turns=2)
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
        self.assertEqual(store.schema_version(), 4)
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
