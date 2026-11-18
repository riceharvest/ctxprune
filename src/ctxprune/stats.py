"""`ctxprune stats`: what the local counts-only log says ctxprune saved.

The log (glue.stats_path(); one line per tool output: time, harness, tool name, chars before/after/deduped, stage)
never holds tool output or commands. Tokens are estimated as chars / 4; the dollar figure is tokens x the input price
you pass (--price, USD per million input tokens; the default is a placeholder, not your model's price). It counts each
output once, when it enters the context; later turns usually re-read it from the prompt cache at a fraction of that
price, and every turn that re-reads it saves again, so treat the figure as a rough order of magnitude.

Share of input: for Claude Code, the token counts Claude Code itself records in its transcripts
(~/.claude/projects/**/*.jsonl, `usage` of each API response; read-only, numbers only) give the input tokens that
newly entered the context over the same period (fresh input + cache writes). ctxprune's share is
saved / (that + saved): the part of what would have been sent that it removed. Content read back from the prompt
cache on later turns shrinks by roughly the same share. On a subscription (Pro / Max) nothing is billed per token,
so the same share shows up as usage-limit headroom on the input side instead of dollars.
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


def cc_projects_dir() -> str:
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"),
                        "projects")


def _ts(v) -> float:
    if not isinstance(v, str):
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def cc_input(since: float, root: str | None = None) -> dict | None:
    """Input tokens Claude Code recorded (assistant `usage`, one per API response id) since `since`: fresh, cache
    writes, cache reads. Numbers only; None when there are no transcripts."""
    root = root or cc_projects_dir()
    if not os.path.isdir(root):
        return None
    tot = {"requests": 0, "fresh": 0, "cache_write": 0, "cache_read": 0}
    seen = set()
    for d, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(d, fn)
            try:
                if os.path.getmtime(p) < since:
                    continue
                f = open(p, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with f:
                for ln in f:
                    if '"usage"' not in ln or '"assistant"' not in ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    m = r.get("message") if isinstance(r, dict) else None
                    u = m.get("usage") if isinstance(m, dict) else None
                    if not isinstance(u, dict) or r.get("type") != "assistant" or _ts(r.get("timestamp")) < since:
                        continue
                    key = m.get("id") or r.get("requestId") or r.get("uuid")
                    if key in seen:
                        continue
                    seen.add(key)
                    tot["requests"] += 1
                    for k, f_ in (("fresh", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                                  ("cache_read", "cache_read_input_tokens")):
                        try:
                            tot[k] += int(u.get(f_) or 0)
                        except (TypeError, ValueError):
                            pass
    return tot if tot["requests"] else None


def share(saved_tokens: float, cc: dict | None) -> float | None:
    """% of the input that would have entered the context (fresh + cache writes) that ctxprune removed."""
    if not cc:
        return None
    new = cc["fresh"] + cc["cache_write"]
    return 100.0 * saved_tokens / (new + saved_tokens) if new + saved_tokens > 0 else None


def weekly_line(days: float = 7.0) -> str | None:
    """One line for the opt-in SessionStart summary (shown to the user, never to the model)."""
    since = time.time() - days * 86400
    tot = summarize(read(glue.stats_path(), since))
    if not tot["saved"]:
        return None
    tokens = tot["saved"] / CHARS_PER_TOKEN
    sh = per_harness(read(glue.stats_path(), since), since)["total"].get("share_pct")
    return (f"ctxprune, last {days:g} days: ~{tokens:,.0f} tokens saved over {tot['calls']:,} tool outputs"
            + (f" (~{sh:.1f}% of input)" if sh is not None else "") + ". Details: ctxprune stats")


def per_harness(rows: list, since: float, transcripts: bool = True) -> dict:
    """One entry per harness seen in the log (plus `total`): calls, tokens saved (chars / 4, request-time dedup
    included) and, where the harness keeps usage locally, the share of its input ctxprune removed."""
    from . import usage
    saved: dict = {}
    calls: dict = {}
    first: dict = {}
    for r in rows:
        h = r.get("h") or "?"
        b, a_, d = int(r.get("b") or 0), int(r.get("a") or 0), int(r.get("d") or 0)
        saved[h] = saved.get(h, 0) + (d if r.get("s") == "context" else b - a_)
        if r.get("s") != "context":
            calls[h] = calls.get(h, 0) + 1
        first[h] = min(first.get(h, r.get("ts", 0)), r.get("ts", 0))
    out: dict = {}
    tot_saved = tot_new = 0.0
    any_share = False
    for h in sorted(saved):
        tok = saved[h] / CHARS_PER_TOKEN
        e = {"calls": calls.get(h, 0), "tokens_saved": round(tok)}
        reader = usage.READERS.get(h)
        if h in usage.NOT_READ:
            e["from"] = usage.NOT_READ[h]
        elif reader is None:
            e["from"] = "counts log only"
        elif not transcripts:
            e["from"] = "counts log only (--no-transcripts)"
        else:
            try:
                u = reader(max(since, first.get(h, since)))
            except Exception:  # noqa: BLE001
                u = None
            if u is None:
                e["from"] = "no local usage records found"
            else:
                e["share_pct"] = round(usage.share(tok, u), 2) if usage.share(tok, u) is not None else None
                e["new_input_tokens"] = round(usage.new_input_tokens(u))
                e["requests"] = u["requests"]
                e["from"] = f"{u['requests']:,} requests, {u['basis'].split(':')[0]}"
                tot_saved += tok
                tot_new += usage.new_input_tokens(u)
                any_share = True
        out[h] = e
    out["total"] = {"calls": sum(calls.values()), "tokens_saved": round(sum(saved.values()) / CHARS_PER_TOKEN),
                    "share_pct": round(100.0 * tot_saved / (tot_new + tot_saved), 2)
                    if any_share and tot_new + tot_saved > 0 else None,
                    "from": "harnesses with local usage records" if any_share else ""}
    return out


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="ctxprune stats")
    ap.add_argument("--days", type=float, default=0, help="only the last N days (default: everything)")
    ap.add_argument("--price", type=float, default=float(os.environ.get("CTXPRUNE_PRICE_PER_MTOK") or DEFAULT_PRICE),
                    help=f"USD per million input tokens for the estimate (default {DEFAULT_PRICE}, a placeholder)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-transcripts", action="store_true",
                    help="don't read the agents' own local usage records (skips the share-of-input estimate)")
    a = ap.parse_args(argv)
    path = glue.stats_path()
    since = time.time() - a.days * 86400 if a.days else 0.0
    rows = read(path, since)
    tot = summarize(rows)
    tokens = tot["saved"] / CHARS_PER_TOKEN
    tot["tokens_saved_est"], tot["usd_est"], tot["price_per_mtok"] = round(tokens), round(tokens / 1e6 * a.price,
                                                                                         4), a.price
    per = per_harness(rows, since, transcripts=not a.no_transcripts)
    tot["harnesses"] = per
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
    print(f"  at API prices         ~${tokens / 1e6 * a.price:,.2f} input cost saved at ${a.price:g} per million "
          "input tokens (each output counted once; pass --price for your model)")
    rows_h = [(h, v) for h, v in per.items() if h != "total"]
    if rows_h:
        print("\n  per harness           calls   tokens saved   share of input (est.)   from")
        for h, v in rows_h + [("total", per["total"])]:
            sh_ = f"~{v['share_pct']:.1f}%" if v.get("share_pct") is not None else "-"
            ts_ = f"~{v['tokens_saved']:,.0f}"
            print(f"  {h:<20} {v['calls']:>6,}   {ts_:>12}   {sh_:>21}   {v.get('from', '')}")
        t = per["total"]
        if t.get("share_pct") is not None:
            print(f"\n  on a subscription     ~{t['share_pct']:.1f}% more input-side usage headroom where you pay by plan "
                  "(not per token; limits also count output)")
        print("  share of input        saved / (what newly entered the context + saved), from each agent's own "
              "local records (--no-transcripts skips them)")
    top = sorted(tot["by_tool"].items(), key=lambda kv: -kv[1][1])[:5]
    if top and any(s for _, (_, s) in top):
        print("  top tools             " + ", ".join(f"{t} {s / CHARS_PER_TOKEN:,.0f} tok" for t, (_, s) in top if s))
    if proxy is not None:
        print(f"  proxy (running)       {json.dumps(proxy, sort_keys=True)[:300]}")
    return 0
