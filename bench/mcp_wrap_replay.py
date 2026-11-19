#!/usr/bin/env python3
"""Offline replay for `ctxprune mcp-wrap` (T1.8): every MCP tool output of the public traces (results/typical raw
samples) goes through the wrapper's Transformer as a tools/call response, STATELESS (no cross-call dedup).
Reports o200k token savings per dataset + the guardrail counts (round-trip/parse failures, schema breaks).

  ~/.cache/ctxprune-typical/venv/bin/python -I bench/mcp_wrap_replay.py --typical ~/ctxprune-v2/results/typical [--n 400]
"""
import argparse
import json
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--typical", required=True)
ap.add_argument("--n", type=int, default=400)
ap.add_argument("--only", nargs="*", default=["mcpmark", "mcp_atlas", "toucan", "playwright_tc"])
a = ap.parse_args()
sys.path.insert(0, a.typical)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(a.typical).parent.parent / "scripts"))
import measure  # noqa: E402
import tiktoken  # noqa: E402

from ctxprune.mcp_wrap import Transformer  # noqa: E402

enc = tiktoken.get_encoding("o200k_base")
tok = lambda s: len(enc.encode(s, disallowed_special=()))  # noqa: E731
ITER = {"mcpmark": measure.it_mcpmark, "mcp_atlas": measure.it_mcp_atlas, "toucan": measure.it_toucan,
        "playwright_tc": measure.it_playwright_tc}
tf = Transformer()
tot = {}
for name in a.only:
    for tr in ITER[name](a.n):
        sub = tr.get("sub") or ""
        key = f"{name}/{sub}" if sub else name
        t = tot.setdefault(key, dict(outs=0, before=0, after=0, changed=0, skipped_read=0, fail=0, grow=0))
        for ev in tr["ev"]:
            if ev[0] != "out":
                continue
            _, tool, server, cmd, blocks, err, pers = ev
            for b in blocks:
                if not isinstance(b, str) or not b:
                    continue
                t["outs"] += 1
                tb = tok(b)
                t["before"] += tb
                tf.pending.clear()
                tf.pending["1"] = tool
                line = (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": b}],
                                                                           "isError": bool(err)}}) + "\n").encode()
                out = tf.on_response(line)
                try:
                    m = json.loads(out)
                    nb = m["result"]["content"][0]["text"]
                    assert set(m) == {"jsonrpc", "id", "result"} and len(m["result"]["content"]) == 1
                except Exception:
                    t["fail"] += 1
                    nb = b
                ta = tok(nb)
                if nb != b:
                    t["changed"] += 1
                    t["grow"] += ta > tb
                t["after"] += ta
print(f"{'dataset':26} {'outs':>6} {'tok before':>11} {'saved%':>7} {'changed%':>8} {'grew':>5} {'fail':>4}")
B = A = 0
for k, t in sorted(tot.items()):
    B += t["before"]
    A += t["after"]
    print(f"{k:26} {t['outs']:6} {t['before']:11} {100 * (1 - t['after'] / max(1, t['before'])):7.2f} "
          f"{100 * t['changed'] / max(1, t['outs']):8.1f} {t['grow']:5} {t['fail']:4}")
print(f"{'ALL':26} {'':6} {B:11} {100 * (1 - A / max(1, B)):7.2f}")
