"""Tests for entityrefs.py (opt-in cross-call entity refs). Run: python3 -I -m unittest scripts/test_entityrefs.py"""
from __future__ import annotations

import copy
import json
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import entityrefs as E  # noqa: E402


def _rand(rng, depth=0):
    if depth > 3 or rng.random() < .3:
        return rng.choice([1, 2.5, "x/y~z", None, True, "é "])
    if rng.random() < .5:
        return [_rand(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {rng.choice("abcdef") + rng.choice(["", "/", "~"]): _rand(rng, depth + 1) for _ in range(rng.randint(0, 4))}


class T(unittest.TestCase):
    def test_self_test(self):
        E._self_test()

    def test_random_roundtrip_and_default_untouched(self):
        rng = random.Random(7)
        pool = [_rand(rng) for _ in range(12)]
        s = E.EntityRefs(min_chars=10)
        fired = 0
        for _ in range(300):
            o = {"r": [rng.choice(pool) for _ in range(3)], "z": rng.choice(pool)}
            t = E._dumps(o) + rng.choice(["", "\n"])
            snap = copy.deepcopy(s)
            new, info = s.process(t)
            self.assertEqual(E.expand(new, snap), t)
            fired += bool(info["refs"])
        self.assertGreater(fired, 20)

    def test_non_json_and_pretty_pass_through(self):
        s = E.EntityRefs()
        for t in ("hello", '{"a": 1,\n "b": 2}', "", "[1,2", '{"a":1,"a":2}', '{"$ref":"#1","b":1}'):
            self.assertEqual(s.process(t), (t, {"refs": 0, "legend": False}))

    def test_no_ref_when_not_shorter(self):
        e = {"a": 1, "b": 2}
        s = E.EntityRefs(min_chars=5, tok=lambda x: len(x))
        s.process(E._dumps({"x": e, "y": 1}))
        self.assertEqual(s.process(E._dumps({"x": e, "y": 1}))[1]["refs"], 0)   # ref is longer than the object

    def test_expand_ignores_plain_text(self):
        self.assertEqual(E.expand("no json here", E.EntityRefs()), "no json here")


if __name__ == "__main__":
    unittest.main()
