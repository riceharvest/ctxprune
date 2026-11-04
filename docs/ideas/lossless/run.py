"""Run the fidelity gate for the lossless ([L]) ideas.

    python docs/ideas/lossless/run.py              # every case
    python docs/ideas/lossless/run.py 9 10 392     # just these idea ids
    python docs/ideas/lossless/run.py -v 9         # per-fixture detail and every failure
    python docs/ideas/lossless/run.py --module cases_logs -v   # only that file's cases (others may be unfinished)
    python docs/ideas/lossless/run.py --list       # id, kind, claim
    python docs/ideas/lossless/run.py --coverage   # exit 1 if a kept [L] idea has no case
    python docs/ideas/lossless/run.py --selftest   # prove the gate rejects planted defects
    python docs/ideas/lossless/run.py --json       # machine-readable results
    python docs/ideas/lossless/run.py --sync       # write each case's claim into ideas.jsonl, rebuild the markdown

Exit status is 1 on any failure (or, with --coverage / --selftest, any gap). See harness.py for what a pass means.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402

JSONL = os.path.join(os.path.dirname(HERE), "ideas.jsonl")


def sync() -> int:
    """Copy each case's kind, claim and loss into its ideas.jsonl record, then rebuild the markdown."""
    rows = [json.loads(l) for l in open(JSONL, encoding="utf-8") if l.strip()]
    n = 0
    for r in rows:
        c = harness.REGISTRY.get(r["id"])
        for k in ("check", "check_kind", "check_loses"):
            r.pop(k, None)
        if c and r["tag"] == "L" and r["status"] != "dropped":
            r["check"], r["check_kind"] = c.claim, c.kind
            if c.loses:
                r["check_loses"] = c.loses
            n += 1
    with open(JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "build.py")], check=True)
    print(f"synced {n} checks into ideas.jsonl")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--module", action="append", metavar="NAME", help="load only cases_NAME.py (repeatable); default: all")
    a = ap.parse_args()

    if a.selftest:
        problems = harness.selftest()
        print("\n".join(problems) or "selftest ok: every planted defect was rejected, the good transform passed")
        return 1 if problems else 0
    harness.discover(a.module)
    for fn, tb in harness.LOAD_ERRORS.items():
        print(f"LOAD ERROR in {fn}:\n{tb}", file=sys.stderr)
    rc_load = 1 if harness.LOAD_ERRORS else 0
    if a.coverage:
        missing, extra = harness.coverage()
        print(f"{len(harness.REGISTRY)} cases; missing a case: {missing or 'none'}; case without a kept [L] idea: {extra or 'none'}")
        return 1 if missing or extra or rc_load else 0
    if a.sync:
        if rc_load:
            return 1
        return sync()
    ids = a.ids or sorted(harness.REGISTRY)
    if a.module and not a.ids and not ids:
        print("no cases registered by the selected module(s)", file=sys.stderr)
        return 1
    unknown = [i for i in ids if i not in harness.REGISTRY]
    if unknown:
        print(f"no case for idea(s) {unknown}; --coverage lists the gaps", file=sys.stderr)
        return 2
    titles = {r["id"]: r["title"] for r in harness.lossless_ideas()}
    if a.list:
        for i in ids:
            c = harness.REGISTRY[i]
            print(f"#{i:<5} {c.kind:<10} {titles.get(i, '')[:60]:<60} | {c.claim}")
        return 0

    results = [harness.run_case(harness.REGISTRY[i]) for i in ids]
    if a.json:
        print(json.dumps([r.__dict__ for r in results], indent=1))
    else:
        print(f"{'idea':<6} {'kind':<10} {'status':<7} {'fix':>3} {'mut':>5} {'chars':>7} {'tokens':>7}  title")
        for r in results:
            gain = f"{-r.char_gain:+.0%}" if r.char_gain else "-"
            tok = f"{-r.tok_gain:+.0%}" if r.char_gain else "-"
            print(f"#{r.id:<5} {r.kind:<10} {r.status:<7} {r.fixtures:>3} {r.mutants:>5} {gain:>7} {tok:>7}  {titles.get(r.id, '')[:50]}")
            if r.status == "fail":
                for e in r.errors[: (None if a.verbose else 2)]:
                    print(f"        FAIL {e}")
            elif a.verbose:
                for d in r.detail:
                    print(f"        {d}")
        ok = sum(r.status == "pass" for r in results)
        bad = sum(r.status == "fail" for r in results)
        man = sum(r.status == "manual" for r in results)
        print(f"\n{ok} pass, {bad} fail, {man} manual of {len(results)}; gain columns are mean size reduction on the "
              f"shrinking fixtures (tokens: {harness.TOKENIZER})")
    return 1 if rc_load or any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
