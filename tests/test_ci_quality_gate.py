"""Unit and integration tests for scripts/ci_quality_gate.py (L5.6)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT / "src"), str(REPO_ROOT / "scripts"), str(REPO_ROOT / "docs" / "ideas" / "gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ci_quality_gate as cqg  # noqa: E402


def test_baseline_json_schema_and_values():
    assert cqg.DEFAULT_BASELINE_PATH.exists(), "docs/bench/ci_quality_gate_baseline.json must exist"
    base = json.loads(cqg.DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    for key in ("schema_version", "git_commit", "hashes", "gates", "v032_raw", "v032_protect", "v032_raw_flat", "v04_ref", "v04_ref_flat"):
        assert key in base, f"missing {key} in baseline JSON"

    # Frozen v0.3.2 baseline facts & JSON validity
    assert base["v032_raw_flat"]["facts_macro_pct"] == 62.1
    assert base["v032_raw_flat"]["facts_agent_pct"] == 45.8
    assert base["v032_raw_flat"]["json_valid_pct"] == 0.0
    assert base["v032_raw_flat"]["neg_r05_all_anchors_pct"] == 37.5

    # Frozen v0.4 reference passes every gate
    fake_report = copy.deepcopy(base["v04_ref"])
    fake_report["engine"] = "v04"
    fake_report["total_wall_s"] = base["v04_ref_flat"]["total_wall_s"]
    passed, rows = cqg.evaluate_gates(fake_report, base["gates"])
    failed = [r for r in rows if not r["passed"]]
    assert passed, f"v04_ref in baseline JSON failed gates: {failed}"


@pytest.mark.parametrize(
    "category_prefix,patch_fn",
    [
        ("1. llmlingua_facts", lambda r: r["facts"]["_macro"].__setitem__("facts_kept", 62.1)),
        ("2. Squeez Recall", lambda r: r["squeez"]["r0.5"].__setitem__("tok_recall", 73.5)),
        ("3. Negation Suite", lambda r: r["negation"]["r0.5"].__setitem__("all_anchors_pct", 37.5)),
        ("4. JSON/Code Validity", lambda r: r["json_code_validity"].__setitem__("json_valid_pct", 90.0)),
        ("4. JSON/Code Validity", lambda r: r["json_code_validity"].__setitem__("code_ast_valid_pct", 95.0)),
        ("5. docs/ideas/gate", lambda r: r["ideas_gate"].__setitem__("failed", 1)),
        ("5. docs/ideas/gate", lambda r: r["ideas_gate"].__setitem__("blind_oracles", 1)),
        ("6. Latency & Memory", lambda r: r["latency_memory"].__setitem__("eager_heavy_imports", 1)),
        ("6. Latency & Memory", lambda r: r["latency_memory"].__setitem__("t17_noq_med_ms_per_10", 1.5)),
        ("6. Latency & Memory", lambda r: r["latency_memory"].__setitem__("peak_rss_mb", 1500.0)),
        ("6. Latency & Memory", lambda r: r.__setitem__("total_wall_s", 65.0)),
    ],
)
def test_gate_catches_planted_regressions_in_every_category(category_prefix, patch_fn):
    base = json.loads(cqg.DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    rep = copy.deepcopy(base["v04_ref"])
    rep["engine"] = "v04"
    rep["total_wall_s"] = 25.0
    patch_fn(rep)
    passed, rows = cqg.evaluate_gates(rep, base["gates"])
    assert not passed
    cat_failures = [r for r in rows if not r["passed"] and r["category"] == category_prefix]
    assert cat_failures, f"expected failure in {category_prefix}, got {[r for r in rows if not r['passed']]}"


def test_markdown_scorecard_formatting():
    base = json.loads(cqg.DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    rep = copy.deepcopy(base["v04_ref"])
    rep["engine"] = "v04"
    rep["total_wall_s"] = base["v04_ref_flat"]["total_wall_s"]
    passed, rows = cqg.evaluate_gates(rep, base["gates"])
    rep["passed"] = passed
    md = cqg.format_markdown_scorecard(rep, rows, baseline=base)
    assert "# ctxprune Zero-GPU CI Quality Gate Scorecard" in md
    assert "| Category | Metric | `v0.3.2` Frozen | `v0.4` Frozen Ref | Current |" in md
    assert "`facts_macro_pct`" in md
    assert "+34.1" in md
    assert "**PASS**" in md
