#!/usr/bin/env python3
"""Real Claude Code sessions (trace-commons + Terminal-Bench claude-code), hook scope, split into sessions with a
PowerShell call (Windows) and without. Saved % of all tool-output tokens the model sees whole (spills left out),
with a session bootstrap (2,000 resamples, seed 1). Input: the per-output records measure.py --recs writes.

    python3 real_cc_split.py recs.jsonl
"""
import collections
import json
import random
import sys

by = collections.defaultdict(lambda: [0, 0])
win = set()
for line in open(sys.argv[1]):
    r = json.loads(line)
    if not (r["ds"] == "trace_commons" or (r["ds"] == "terminalbench" and r["sub"] == "claude-code")) or r.get("spill"):
        continue
    k = (r["ds"], r["tid"])
    if r["tool"] == "PowerShell":
        win.add(k)
    h = r.get("hook")
    by[k][0] += r["t0"][0]
    by[k][1] += (r["t0"][0] - h["t"][0]) if h else 0


def pct(keys):
    return 100 * sum(by[k][1] for k in keys) / sum(by[k][0] for k in keys)


rng = random.Random(1)
for name, ks in (("non-windows", [k for k in by if k not in win]), ("windows", [k for k in by if k in win])):
    bs = sorted(pct([rng.choice(ks) for _ in ks]) for _ in range(2000))
    print(f"{name}: {len(ks)} sessions, {sum(by[k][0] for k in ks)} o200k tokens, saved {pct(ks):.2f}% "
          f"[{bs[50]:.2f}, {bs[1949]:.2f}]")
