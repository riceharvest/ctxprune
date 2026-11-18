"""Fidelity gate for the lossless ([L]) and shortlisted rule ([R]) ideas in docs/ideas: one test per idea, plus coverage and harness checks.

The cases live in docs/ideas/gate/cases_*.py; `python docs/ideas/gate/run.py` runs the same thing from a shell.
"""
import json
import os
import sys

import pytest

GATE = os.path.join(os.path.dirname(__file__), "..", "docs", "ideas", "gate")
sys.path.insert(0, os.path.abspath(GATE))
import harness  # noqa: E402

harness.discover()


def test_every_case_module_imports():
    assert not harness.LOAD_ERRORS, harness.LOAD_ERRORS


def test_gate_rejects_planted_defects():
    assert harness.selftest() == []


def test_oracles_notice_corrupted_encodings():
    """Corrupt each transform's output (replace/drop a character, truncate): the case's own decode or oracle must
    reject it, otherwise the case would pass for a wrong encoder too."""
    blind = {}
    for i, c in sorted(harness.REGISTRY.items()):
        hard = {k: v for k, v in harness.oracle_check(c).items() if k in harness.MUST_NOTICE}
        if hard:
            blind[i] = hard
    assert not blind, f"cases whose oracle misses corrupted encodings: {blind}"


def test_every_gated_idea_has_a_case():
    missing, extra = harness.coverage()
    assert not missing, f"gated ideas without a case: {missing}"
    assert not extra, f"cases for ids that are not kept ideas: {extra}"


def test_ideas_jsonl_records_each_check():
    """ideas.jsonl carries the claim of every case (python docs/ideas/gate/run.py --sync writes it)."""
    path = os.path.join(GATE, "..", "ideas.jsonl")
    for r in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip()):
        c = harness.REGISTRY.get(r["id"]) if r["status"] != "dropped" else None
        if c is None:
            assert "check" not in r, f"#{r['id']} has a check but no case"
        else:
            assert (r.get("check"), r.get("check_kind"), r.get("check_loses", "")) == (c.claim, c.kind, c.loses), \
                f"#{r['id']}: ideas.jsonl is stale, run python docs/ideas/gate/run.py --sync"


@pytest.mark.parametrize("idea", sorted(harness.REGISTRY))
def test_lossless_idea(idea):
    r = harness.run_case(harness.REGISTRY[idea])
    assert r.status in ("pass", "manual"), f"#{idea} ({r.kind}):\n" + "\n".join(r.errors[:5])
