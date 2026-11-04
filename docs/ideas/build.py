"""Rebuild ideas.md, shortlist.md and dropped.md from ideas.jsonl.

    python docs/ideas/build.py

ideas.jsonl is the source of truth: edit a record's status/est/text there and re-run.
status: scope | eval | conditional | dropped.
check, check_kind, check_loses: written by `python docs/ideas/lossless/run.py --sync` from the registered fidelity-gate
case of each [L] idea; rendered below as a "Run:" line. Do not hand-edit them.
"""
import collections
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "ideas.jsonl"), encoding="utf-8") if l.strip()]
MCP = re.compile(r"\bmcp\b|tool schema|tools/list|toolsearch|tool definitions", re.I)
TAGS = {"L": 0, "R": 1, "M": 2, "X": 3}
LEGEND = (
    "Tags: **L** lossless, **R** deterministic rule that drops information, **M** model-based, **X** measurement/infra. "
    "`MCP` = applies to MCP tool traffic only. `busts-cache` = rewrites already-sent messages, so provider prefix caches miss "
    "from that point. Every estimate is an unverified hypothesis; 'Measured'/'Verified here' means a finder ran a quick check "
    "that was not reproduced. `Run:` = a runnable fidelity gate (docs/ideas/lossless): it checks that the idea can be "
    "lossless on synthetic fixtures and their mutations; it does not measure real-world savings (the `Test:` line does)."
)


def starred(r):
    return r["status"] == "scope" and r["est"] in ("medium", "large") and r["round"] > 0


def item(r, star=True):
    flags = [r["status"]] if r["status"] in ("eval", "conditional") else []
    if MCP.search(r["title"] + " " + r["detail"]):
        flags.append("MCP")
    if r["cache"] == "busts":
        flags.append("busts-cache")
    fl = " `" + ", ".join(flags) + "`" if flags else ""
    est = f" _{r['est']}_" if r["est"] != "unknown" else ""
    line = f"- **#{r['id']}**{' ★' if star and starred(r) else ''} [{r['tag']}]{est}{fl} **{r['title']}** — {r['detail']}"
    if r["test"]:
        line += f"\n  - Test: {r['test']}"
    if r.get("check"):
        line += f"\n  - Run: `python docs/ideas/lossless/run.py {r['id']}` ({r['check_kind']}): {r['check']}"
        if r.get("check_loses"):
            line += f" Not preserved: {r['check_loses']}."
    return line


def by_cat(rs):
    cats = collections.OrderedDict()
    for r in rs:
        cats.setdefault(r["cat"], []).append(r)
    return cats


kept = [r for r in rows if r["status"] != "dropped"]
cnt = collections.Counter(r["status"] for r in kept)
nstar = sum(starred(r) for r in kept)
dropped = [r for r in rows if r["status"] == "dropped"]

out = [
    "# ctxprune: token-saving ideas (scoped master list)", "",
    f"{len(kept)} ideas, one list. Scope: works in all harnesses, with all models, against cloud API models, running locally. "
    f"Status: **scope** {cnt['scope']} (in scope), **eval** {cnt['eval']} (measurement/benchmark/safety tooling), "
    f"**conditional** {cnt['conditional']} (needs the tool call rewritten before it runs, so not universal). "
    f"★ = the finders rated it medium or large ({nstar} in-scope ideas). {len(dropped)} further ideas were dropped as harness-, "
    "model- or cloud-specific or off-scope; see dropped.md.", "", LEGEND, "", "## Contents", "",
]
cats = by_cat(kept)
out += [f"- {c} ({len(v)})" for c, v in cats.items()] + [""]
for c, v in cats.items():
    out += [f"## {c}", ""] + [item(r) for r in v] + [""]
open(os.path.join(HERE, "ideas.md"), "w", encoding="utf-8").write("\n".join(out))

sl = sorted([r for r in rows if starred(r)], key=lambda r: (r["est"] != "large", TAGS[r["tag"]], r["id"]))
out = [f"# Shortlist: {len(sl)} in-scope ideas rated medium or large", "",
       "Large first, then medium; lossless (L) before rules (R) before model (M) before measurement (X). "
       "Seed items #1-#117 are not included here.", "", LEGEND, ""]
open(os.path.join(HERE, "shortlist.md"), "w", encoding="utf-8").write("\n".join(out + [item(r, star=False) for r in sl]))

dc = collections.OrderedDict()
for r in dropped:
    dc.setdefault(r["verdict"], []).append(r)
out = [f"# Dropped by the scope filter ({len(dropped)})", ""]
for v, rs in dc.items():
    out += [f"## {v} ({len(rs)})", ""] + [f"- #{r['id']} {r['title']} — {r['because']}" for r in rs] + [""]
open(os.path.join(HERE, "dropped.md"), "w", encoding="utf-8").write("\n".join(out))
print(f"ideas.md {len(kept)} | shortlist.md {len(sl)} | dropped.md {len(dropped)}")
