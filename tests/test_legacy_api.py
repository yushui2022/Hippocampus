"""The v0 surface still works after the v2 rewrite.

These are deliberately shallow: behaviour is covered by ``test_v2.py``.  They
exist so old integrations fail loudly if a public name disappears.
"""

from __future__ import annotations

import unittest

from hippocampus import Event, Hippocampus
from hippocampus import decay


class TestLegacySurface(unittest.TestCase):
    def test_kernel_methods_exist(self):
        h = Hippocampus(scope="legacy", path=":memory:")
        for name in ("remember", "recall", "tick", "consolidate", "forget"):
            self.assertTrue(callable(getattr(h, name)))

    def test_old_constructor_still_works(self):
        h = Hippocampus("legacy", "companion", ":memory:")
        e = h.remember(Event("今天喝了杯奶茶"))
        self.assertIsNotNone(e.engram_id)
        self.assertTrue(h.recall("奶茶"))

    def test_decay_helpers_are_public(self):
        self.assertGreater(decay.retrievability(0, 10), 0.99)
        self.assertGreaterEqual(decay.initial_stability(0.5), 0.02)

    def test_profile_lookup(self):
        from hippocampus import get_profile

        self.assertEqual(get_profile("companion").name, "companion")


if __name__ == "__main__":
    unittest.main()
