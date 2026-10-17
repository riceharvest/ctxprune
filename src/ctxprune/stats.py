"""`ctxprune stats`: what the local counts-only log says ctxprune saved.

The log (glue.stats_path(); one line per tool output: time, harness, tool name, chars before/after/deduped, stage)
never holds tool output or commands. Tokens are estimated as chars / 4; the dollar figure is tokens x the input price
you pass (--price, USD per million input tokens; the default is a placeholder, not your model's price). It counts each
output once, when it enters the context; later turns usually re-read it from the prompt cache at a fraction of that
price, and every turn that re-reads it saves again, so treat the figure as a rough order of magnitude.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from . import glue

DEFAULT_PRICE = 3.0   # USD / million input tokens: a placeholder, pass --price for your model
CHARS_PER_TOKEN = 4.0


def read(path: str, since: float = 0.0) -> list:
    rows = []
    for p in (path + ".1", path):
        try:
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("ts", 0) >= since:
                        rows.append(r)
        except OSError:
            continue
    return rows


def summarize(rows: list) -> dict:
    tot = {"calls": 0, "changed": 0, "before": 0, "after": 0, "dedup": 0, "off": 0}
    by_h: dict = {}
    by_t: dict = {}
    tot["request_dedup"] = 0
    for r in rows:
        if r.get("s") == "context":   # plugins' per-request dedup: savings only, not a new tool output
            tot["request_dedup"] += int(r.get("d") or 0)
            continue
        b, a = int(r.get("b") or 0), int(r.get("a") or 0)
        tot["calls"] += 1
        if r.get("s") == "off":
            tot["off"] += 1
        tot["before"] += b
        tot["after"] += a
        tot["dedup"] += int(r.get("d") or 0)
        if a < b:
            tot["changed"] += 1
        for key, d in ((r.get("h") or "?", by_h), (r.get("t") or "?", by_t)):
            x = d.setdefault(key, [0, 0])
            x[0] += 1
            x[1] += b - a
    tot["saved"] = tot["before"] - tot["after"] + tot["request_dedup"]
    tot["by_harness"], tot["by_tool"] = by_h, by_t
    return tot


def _proxy_stats(port: int) -> dict | None:
    import urllib.request
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(f"http://127.0.0.1:{port}/ctxprune/stats", timeout=1.5) as r:
            d = json.load(r)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="ctxprune stats")
    ap.add_argument("--days", type=float, default=0, help="only the last N days (default: everything)")
    ap.add_argument("--price", type=float, default=float(os.environ.get("CTXPRUNE_PRICE_PER_MTOK") or DEFAULT_PRICE),
                    help=f"USD per million input tokens for the estimate (default {DEFAULT_PRICE}, a placeholder)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path = glue.stats_path()
    since = time.time() - a.days * 86400 if a.days else 0.0
    tot = summarize(read(path, since))
    tokens = tot["saved"] / CHARS_PER_TOKEN
    tot["tokens_saved_est"], tot["usd_est"], tot["price_per_mtok"] = round(tokens), round(tokens / 1e6 * a.price,
                                                                                         4), a.price
    proxy = _proxy_stats(glue.settings()["proxy_port"])
    if proxy is not None:
        tot["proxy"] = proxy
    if a.json:
        print(json.dumps(tot, indent=2, sort_keys=True))
        return 0
    if not tot["calls"] and proxy is None:
        print(f"no ctxprune calls logged yet ({path}).\nInstall an integration (ctxprune install --all) and use your "
              "agent; this log keeps only counts, never tool output.")
        return 0
    span = f"last {a.days:g} days" if a.days else "all time"
    pct = 100.0 * (tot["before"] - tot["after"]) / tot["before"] if tot["before"] else 0.0
    print(f"ctxprune stats ({span}, {path})")
    print(f"  tool outputs seen     {tot['calls']:,}  (made smaller: {tot['changed']:,}; skipped by CTXPRUNE_OFF: "
          f"{tot['off']:,})")
    print(f"  chars in -> out       {tot['before']:,} -> {tot['after']:,}  (-{pct:.1f}%; dedup part "
          f"{tot['dedup']:,})")
    if tot["request_dedup"]:
        print(f"  per-request dedup     {tot['request_dedup']:,} chars (Hermes / Pi / OpenCode plugins, per LLM call)")
    print(f"  tokens saved (est.)   ~{tokens:,.0f}  (chars / {CHARS_PER_TOKEN:g})")
    print(f"  input cost saved      ~${tokens / 1e6 * a.price:,.2f}  at ${a.price:g} per million input tokens "
          "(each output counted once; pass --price for your model)")
    if tot["by_harness"]:
        print("  by harness            " + ", ".join(f"{h} {n:,} calls / {s / CHARS_PER_TOKEN:,.0f} tok"
                                                   for h, (n, s) in sorted(tot["by_harness"].items())))
    top = sorted(tot["by_tool"].items(), key=lambda kv: -kv[1][1])[:5]
    if top and any(s for _, (_, s) in top):
        print("  top tools             " + ", ".join(f"{t} {s / CHARS_PER_TOKEN:,.0f} tok" for t, (_, s) in top if s))
    if proxy is not None:
        print(f"  proxy (running)       {json.dumps(proxy, sort_keys=True)[:300]}")
    return 0
