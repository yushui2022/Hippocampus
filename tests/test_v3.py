"""v3 invariants and behaviour.

These tests are the acceptance criteria from the v3 spec (section 10):
12 observe never calls an LLM
13 dropped buffer leaves no trace after TTL
14 assistant turns never create facts / notes / self entries
15 private memory is not recalled in front of outsiders
16 context() is read-only
17 reflection does not loop
18 maintain(now) is idempotent
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from hippocampus import Brain, Store, Utterance
from hippocampus.entities import add_note, render_person
from hippocampus.models import Entity
from hippocampus.self_model import SelfModel
from hippocampus.sensory import SensoryGate
from hippocampus.source import speakable

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def at(hours: float = 0.0, days: float = 0.0) -> datetime:
    return T0 + timedelta(hours=hours, days=days)


def make_brain(**kwargs) -> Brain:
    return Brain("s", store=Store(":memory:"), **kwargs)


def utterance(text: str, **kwargs) -> Utterance:
    values = dict(
        context_id="c:dm",
        context_kind="dyad",
        context_size=2,
        speaker_id="p:1",
        speaker_name="小王",
        addressed=True,
        at=T0,
    )
    values.update(kwargs)
    return Utterance(text=text, **values)


class TestSensoryGate(unittest.TestCase):
    def test_filler_and_noise_are_dropped(self):
        gate = SensoryGate()
        for text in ("嗯", "666", "哈哈哈哈", "。。。", "[表情]", "  "):
            self.assertFalse(gate.check(text).keep, text)

    def test_addressed_is_always_kept(self):
        gate = SensoryGate()
        self.assertTrue(gate.check("嗯", addressed=True).keep)

    def test_normal_text_kept(self):
        gate = SensoryGate()
        self.assertTrue(gate.check("我今天面试完了").keep)


class TestObserve(unittest.TestCase):
    def test_observe_never_calls_llm(self):
        calls = []

        class Exploding:
            def chat(self, messages):
                calls.append(messages)
                raise AssertionError("observe must not call the LLM")

        b = make_brain(summarizer=Exploding())
        result = b.observe(utterance("我明天要去面试，有点紧张"))
        self.assertTrue(result.kept)
        self.assertEqual(calls, [])
        self.assertEqual(b.store.count_staging("s", "pending"), 1)
        b.close()

    def test_familiarity_rises_even_when_text_is_dropped(self):
        b = make_brain()
        for _ in range(5):
            b.observe(utterance("嗯", addressed=False, at=at(hours=len(b._recent))))
        entity = b.store.get_entity("s", "p:1")
        self.assertGreater(entity.familiarity, 0.0)
        self.assertEqual(b.store.count_staging("s"), 0)
        b.close()

    def test_open_group_hears_everyone_but_crowd_filters_short_lines(self):
        b = make_brain()
        small = b.observe(
            utterance(
                "今天中午吃什么好呢",
                context_id="c:small",
                context_kind="group",
                context_size=5,
                speaker_id="p:2",
                addressed=False,
            )
        )
        self.assertTrue(small.kept)
        crowd = b.observe(
            utterance(
                "今天",
                context_id="c:big",
                context_kind="group",
                context_size=100,
                speaker_id="p:3",
                addressed=False,
            )
        )
        self.assertFalse(crowd.kept)
        self.assertEqual(crowd.reason, "attention")
        b.close()


class TestConsolidation(unittest.TestCase):
    def test_big_event_is_promoted_and_small_is_dropped(self):
        b = make_brain()
        b.observe(utterance("我明天要去面试，有点紧张"))
        b.observe(
            utterance(
                "我今天吃了牛肉面",
                context_id="c:group",
                context_kind="group",
                context_size=5,
                addressed=False,
                at=at(hours=0.2),
            )
        )
        report = b.maintain(at(hours=2))
        self.assertGreaterEqual(report["promoted"], 1)
        texts = [e.raw_text for e in b.store.list("s")]
        self.assertTrue(any("面试" in t for t in texts))
        self.assertFalse(any("牛肉面" in t for t in texts))
        self.assertEqual(b.store.count_staging("s", "dropped"), 1)
        b.close()

    def test_llm_bad_json_falls_back_to_rules(self):
        class Bad:
            def chat(self, messages):
                return "抱歉，我无法输出 JSON"

        b = make_brain(summarizer=Bad())
        b.observe(utterance("我明天要去面试，有点紧张"))
        report = b.maintain(at(hours=2))
        self.assertEqual(b.consolidator.errors, 1)
        self.assertGreaterEqual(report["promoted"], 1)
        self.assertEqual(len(b.store.list("s")), 1)
        b.close()

    def test_assistant_lines_never_create_facts_or_notes(self):
        payload = {
            "events": [
                {
                    "turns": [1, 2],
                    "gist": "他说有点累，我说了句安慰的话",
                    "people": ["p1"],
                    "emotion": "累",
                    "topics": ["工作"],
                    "salience": {
                        "emotion": 0.2,
                        "self": 0.2,
                        "consequence": 0.9,
                        "novelty": 0.5,
                        "explicit": 0.0,
                    },
                    "facts": [
                        {
                            "about": "p1",
                            "kind": "preference",
                            "text": "怕黑",
                            "turns": [2],
                        }
                    ],
                    "notes": [{"about": "p1", "text": "怕黑", "turns": [2]}],
                }
            ]
        }

        class Fake:
            def chat(self, messages):
                import json

                return json.dumps(payload, ensure_ascii=False)

        b = make_brain(summarizer=Fake())
        b.observe(utterance("我今天有点累"))
        b.observe(utterance("怕黑就开灯", actor="assistant", at=at(hours=0.1)))
        b.maintain(at(hours=2))
        self.assertEqual(b.store.count_facts("s"), 0)
        entity = b.store.get_entity("s", "p:1")
        self.assertEqual(entity.card.get("notes") or [], [])
        b.close()

    def test_card_note_needs_two_evidence(self):
        class Fake:
            def chat(self, messages):
                import json

                payload = {
                    "events": [
                        {
                            "turns": [1],
                            "gist": "聊到口味",
                            "people": ["p1"],
                            "salience": {
                                "emotion": 0.2,
                                "self": 0.5,
                                "consequence": 0.9,
                                "novelty": 0.5,
                                "explicit": 0.0,
                            },
                            "notes": [
                                {"about": "p1", "text": "喜欢吃辣", "turns": [1]}
                            ],
                        }
                    ]
                }
                return json.dumps(payload, ensure_ascii=False)

        b = make_brain(summarizer=Fake())
        b.observe(utterance("我特别喜欢吃辣"))
        b.sleep(at(hours=1), force=True)
        entity = b.store.get_entity("s", "p:1")
        self.assertEqual(render_person(entity, min_evidence=2), "")

        b.observe(utterance("我真的喜欢吃辣", at=at(hours=2)))
        b.sleep(at(hours=3), force=True)
        entity = b.store.get_entity("s", "p:1")
        self.assertIn("喜欢吃辣", render_person(entity, min_evidence=2))
        b.close()

    def test_card_helpers(self):
        entity = Entity(scope="s", entity_id="p:1", kind="participant", name="小王")
        self.assertTrue(add_note(entity.card, "怕黑", evidence="e1", at=T0))
        self.assertFalse(add_note(entity.card, "怕黑", evidence="e2", at=T0))
        self.assertEqual(entity.card["notes"][0]["count"], 2)
        self.assertIn("怕黑", render_person(entity, min_evidence=2))


class TestSourceRules(unittest.TestCase):
    def test_speakability_matrix(self):
        private = {
            "context_id": "c:dm",
            "visibility": "private",
            "speakers": ["p:1"],
            "witnesses": ["p:1"],
        }
        self.assertTrue(speakable(private, context_id="c:dm", present=["p:1"]))
        self.assertTrue(speakable(private, context_id="c:dm", present=["p:9"]))
        self.assertFalse(speakable(private, context_id="c:g", present=["p:2"]))

        shared = {
            "context_id": "c:g1",
            "visibility": "shared",
            "speakers": ["p:1"],
            "witnesses": ["p:1"],
        }
        self.assertTrue(
            speakable(shared, context_id="c:g2", present=["p:1"])
        )
        self.assertFalse(
            speakable(shared, context_id="c:g2", present=["p:5"])
        )

    def test_private_memory_not_recalled_in_group(self):
        b = make_brain()
        b.observe(utterance("我告诉你一个秘密，我其实怕黑"))
        b.maintain(at(hours=2))
        self.assertEqual(len(b.store.list("s")), 1)

        dm = b.recall("秘密", context_id="c:dm", present=["p:1"], now=at(hours=3))
        self.assertTrue(any("秘密" in h.text for h in dm.memories))

        group = b.recall(
            "秘密", context_id="c:group", present=["p:2"], now=at(hours=3)
        )
        self.assertFalse(any("秘密" in h.text for h in group.memories))
        self.assertEqual(group.blocked, 1)
        b.close()


class TestBuffer(unittest.TestCase):
    def test_dropped_buffer_visible_then_gone_after_ttl(self):
        b = make_brain()
        b.observe(
            utterance(
                "我今天吃了牛肉面",
                context_id="c:group",
                context_kind="group",
                context_size=5,
                addressed=False,
            )
        )
        b.maintain(at(hours=2))
        self.assertEqual(b.store.count_staging("s", "dropped"), 1)

        near = b.recall(
            "牛肉面", context_id="c:group", present=["p:1"], now=at(hours=3)
        )
        self.assertTrue(any("牛肉面" in h.text for h in near.buffer))

        b.maintain(at(days=5))
        self.assertEqual(b.store.count_staging("s"), 0)
        far = b.recall(
            "牛肉面", context_id="c:group", present=["p:1"], now=at(days=5)
        )
        self.assertEqual(far.buffer, [])
        b.close()


class TestReadOnlyAndIdempotence(unittest.TestCase):
    def test_context_is_read_only(self):
        b = make_brain()
        b.observe(utterance("我明天要去面试，有点紧张"))
        b.maintain(at(hours=2))
        before = (
            b.store.version,
            b.store.count("s"),
            b.store.count_staging("s"),
            b.store.count_facts("s"),
        )
        bundle = b.context(
            "面试", context_id="c:dm", present=["p:1"], now=at(hours=3)
        )
        self.assertTrue(bundle.memories)
        after = (
            b.store.version,
            b.store.count("s"),
            b.store.count_staging("s"),
            b.store.count_facts("s"),
        )
        self.assertEqual(before, after)
        self.assertEqual(b.store.recall_stats("s")["total"], 0)
        b.close()

    def test_maintain_is_idempotent(self):
        b = make_brain()
        b.observe(utterance("我明天要去面试，有点紧张"))
        first = b.maintain(at(hours=2))
        second = b.maintain(at(hours=2))
        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))
        b.close()

    def test_reflection_does_not_loop(self):
        calls = []

        class Fake:
            def chat(self, messages):
                calls.append(1)
                return '{"traits": ["嘴硬心软"]}'

        b = make_brain(summarizer=Fake())
        b.observe(utterance("我明天要去面试，有点紧张"))
        b.maintain(at(hours=2))
        self.assertTrue(b.reflect(force=True))
        before = len(calls)
        self.assertFalse(b.reflect())
        self.assertEqual(len(calls), before)

        model = SelfModel(b.store, "s")
        self.assertEqual(model.state["evidence"]["traits"]["嘴硬心软"], 1)
        self.assertTrue(model.render(min_evidence=1))
        self.assertEqual(model.render(min_evidence=2), "")
        b.close()

    def test_schema_v6_is_idempotent(self):
        path = tempfile.mktemp(suffix=".db")
        store = Store(path)
        self.assertEqual(store.schema_version(), 6)
        store.close()
        again = Store(path)
        self.assertEqual(again.schema_version(), 6)
        again.close()
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
