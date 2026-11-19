#!/usr/bin/env python3
"""L5.6 — Fast zero-GPU CI quality gate for ctxprune (<60s CPU scorecard).

Single-command local CPU scorecard running:
  (1) llmlingua_facts fact survival (n=120 across 4 domains + v0.3.2 golden byte-exact check)
  (2) Squeez reader-free gold-line & content-token survival (n=150 @ r=0.5 and r=0.33)
  (3) Negation / comparator / key-value meaning-flip suite (n=40 @ r=0.5 and r=0.33)
  (4) JSON & code syntax validity (mcp_json n=40 @ r=0.5 + Python/Markdown code AST & fence checks n=30)
  (5) docs/ideas/gate/ fidelity + fuzz + selftest + coverage + oracle checks (347 cases)
  (6) CPU latency & memory cap checks (import-free hook startup, live ONNX parity & 512-tok latency,
      T1.7 prune_scored 10-chunk sub-ms latency & uint8 IoU, lossless in-proc latency, peak RSS cap)

Usage:
  python scripts/ci_quality_gate.py                         # run all 6 suites & print Markdown diff table
  python scripts/ci_quality_gate.py --selftest              # also inject regressions in all 6 categories & verify caught
  python scripts/ci_quality_gate.py --json out.json --markdown out.md
  python scripts/ci_quality_gate.py --engine v032           # score frozen 0.3.2 compatibility engine
  python scripts/ci_quality_gate.py --write-baseline docs/bench/ci_quality_gate_baseline.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import pickle
import re
import resource
import statistics
import string
import subprocess
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SRC = REPO_ROOT / "src"
REPO_SCRIPTS = REPO_ROOT / "scripts"
GATE_DIR = REPO_ROOT / "docs" / "ideas" / "gate"
DEFAULT_BASELINE_PATH = REPO_ROOT / "docs" / "bench" / "ci_quality_gate_baseline.json"
GOLDEN_032_PATH = REPO_ROOT / "tests" / "golden" / "golden_032.json"

for _p in (str(REPO_SRC), str(REPO_SCRIPTS), str(GATE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = Path(os.environ.get("CTXPRUNE_V04_DATA_DIR", "/home/dario/ctxprune-v2/research/v04_code"))
CACHE_DIR = Path(os.environ.get("CTXPRUNE_V04_CACHE_DIR", "/mnt/ssd/ctxprune-v2/v04_cache"))
ENC_SAMPLES_DIR = Path("/home/dario/ctxprune-v2/research/enc-bench/samples")

# Regex from docs/bench/llmlingua_facts.py
FACT_RE = re.compile(
    r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|(?<![<\w])[\w.-]*\w/(?:[\w.-]+/)*[\w.-]*\w|\b[vV]?\d+(?:\.\d+){1,3}\b"
    r"|\b(?=\w*\d)(?=\w*[A-Za-z])[\w-]{6,}\b|\b\d{2,}\b"
)

STOPWORDS = frozenset(
    "a an the and or but if then else when while of in on at to for with by from as is are was were "
    "be been being have has had do does did will would could should may might must can this that these "
    "those it its they them their he she his her we our you your which who whom what where why how all "
    "any some no not only also more most very much many such into over after before between through during".split()
)

_TIKTOKEN_ENC = None


def _get_enc():
    global _TIKTOKEN_ENC
    if _TIKTOKEN_ENC is None:
        import tiktoken

        _TIKTOKEN_ENC = tiktoken.get_encoding("o200k_base")
    return _TIKTOKEN_ENC


def tok_len(text: str) -> int:
    return len(_get_enc().encode(text, disallowed_special=()))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[A-Za-z0-9_./:-]{2,}", text.lower())
    return {t for t in toks if t not in STOPWORDS and any(c.isalnum() for c in t)}


def extract_json_scalars(obj: Any) -> set[str]:
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            out.add(str(x))
        elif isinstance(x, str):
            if len(x) < 80 and x.strip():
                out.add(x.strip())
            for m in FACT_RE.findall(x):
                out.add(m)

    walk(obj)
    return {s for s in out if len(s) >= 2}


def match_budget_by_keep(
    compress_fn: Callable[[float], str],
    target_tokens: int,
    initial_keep: float = 0.5,
    steps: int = 7,
) -> str:
    """Binary search `keep` in [0.01, 1.0] so tok_len(out) <= target_tokens (close to target)."""
    lo, hi = 0.01, 1.0
    best: str | None = None
    best_n = -1
    k_init = min(1.0, max(0.02, initial_keep))
    out0 = compress_fn(k_init)
    n0 = tok_len(out0)
    if n0 <= target_tokens:
        best, best_n = out0, n0
        lo = k_init
    else:
        hi = k_init
    for _ in range(steps):
        if hi - lo < 0.005:
            break
        mid = 0.5 * (lo + hi)
        out = compress_fn(mid)
        n = tok_len(out)
        if n <= target_tokens:
            if n >= best_n:
                best, best_n = out, n
            lo = mid
        else:
            hi = mid
    if best is None:
        best = compress_fn(lo)
    return best


def _verified_cached_scores(
    text: str,
    cached_entry: tuple | None,
    live_compressor: Any | None,
    force_live: bool = False,
) -> tuple:
    """Return (atoms, scores, tok_counts), verifying current atomize(text) matches cached atoms."""
    from ctxprune.atoms import atomize

    if not force_live and cached_entry is not None:
        c_atoms, c_scores, c_toks = cached_entry
        live_atoms = atomize(text)
        if len(live_atoms) == len(c_atoms) and all(
            a.text == b.text and a.ws == b.ws for a, b in zip(live_atoms, c_atoms)
        ):
            return live_atoms, c_scores, c_toks
    if live_compressor is None:
        from ctxprune.compress import Compressor

        live_compressor = Compressor()
    return live_compressor.atom_scores(text)


def make_light_compressor(
    engine: str = "v04",
    score_lookup: dict[str, tuple] | None = None,
    live_fallback: Any | None = None,
    force_live: bool = False,
):
    """Create a Compressor instance that uses verified cached atom scores when available, or live ONNX."""
    from ctxprune.compress import Compressor

    if force_live:
        return Compressor(engine=engine)
    comp = Compressor.__new__(Compressor)
    comp.engine = engine
    comp.rep_decay = "all"
    comp.fewshot = "off"
    comp.overlap = 0
    comp.model_name = "darioooooo0o/ctxprune-small"
    lookup = score_lookup or {}

    def _atom_scores(self, text: str, overlap: int | None = None):
        nonlocal live_fallback
        cached = lookup.get(text) if not overlap else None
        if cached is not None:
            return _verified_cached_scores(text, cached, live_fallback, force_live=False)
        if live_fallback is None:
            live_fallback = Compressor(engine=engine)
        return live_fallback.atom_scores(text, overlap=overlap)

    comp.atom_scores = types.MethodType(_atom_scores, comp)
    return comp


# ---------------------------------------------------------------------------
# Suite 1: llmlingua_facts (n=120 across 4 domains) + 0.3.2 golden parity
# ---------------------------------------------------------------------------


def run_suite_facts(
    facts_rows: list[dict],
    facts_cache: dict[str, tuple],
    engine: str = "v04",
    force_protected: bool | str = "auto",
    force_live: bool = False,
    live_comp: Any | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    score_map = {r["text"]: facts_cache.get(r["id"]) for r in facts_rows}
    comp = make_light_compressor(engine=engine, score_lookup=score_map, live_fallback=live_comp, force_live=force_live)
    comp_032 = make_light_compressor(
        engine="v032", score_lookup=score_map, live_fallback=live_comp, force_live=force_live
    )

    by_dom: dict[str, list[int]] = {}
    tot32_f = 0
    tot32_k = 0
    from ctxprune.atoms import is_protected
    import ctxprune._v032 as v032

    for r in facts_rows:
        txt = r["text"]
        t0_len = tok_len(txt)
        facts = set(FACT_RE.findall(txt))
        nf = len(facts)
        atoms, sc, tc = comp.atom_scores(txt)

        # Compute v0.3.2 raw output directly from (atoms, sc) without re-running atomize(txt)
        counts32 = [len(a.text) + (1 if a.ws else 0) for a in atoms]
        budget32 = int(-(-0.5 * sum(counts32) // 1))
        keep32 = [False] * len(atoms)
        used32 = 0
        for idx in sorted(range(len(atoms)), key=lambda i: -sc[i]):
            if used32 >= budget32:
                break
            if atoms[idx].text:
                keep32[idx] = True
                used32 += counts32[idx]
        out32_raw = v032.detok(atoms, keep32)
        tot32_f += nf
        tot32_k += sum(1 for f in facts if f in out32_raw)

        if engine == "v032":
            if not force_protected:
                out = out32_raw
            else:
                forced32 = [bool(a.text) and is_protected(a.text) for a in atoms]
                keep32p = list(forced32)
                used32p = sum(c for c, f in zip(counts32, forced32) if f)
                for idx in sorted(range(len(atoms)), key=lambda i: -sc[i]):
                    if used32p >= budget32:
                        break
                    if not keep32p[idx] and atoms[idx].text:
                        keep32p[idx] = True
                        used32p += counts32[idx]
                out = v032.detok(atoms, keep32p)
        else:
            prep = comp._prepare_selection(atoms, sc, tc, txt, unit="tokens_est", query=None)
            out = comp._slice_prepared(prep, 0.5, force_protected=force_protected)["text"]

        kept = sum(1 for f in facts if f in out)
        a = by_dom.setdefault(r["domain"], [0, 0, 0, 0])
        a[0] += nf
        a[1] += kept
        a[2] += t0_len
        a[3] += tok_len(out)

    res: dict[str, Any] = {}
    tot_f = tot_k = tot_t0 = tot_t1 = 0
    for dom, (nf, kf, t_in, t_out) in sorted(by_dom.items()):
        res[dom] = {
            "facts_kept": round(100.0 * kf / max(1, nf), 1),
            "tokens_kept": round(100.0 * t_out / max(1, t_in), 1),
            "n_facts": nf,
        }
        tot_f += nf
        tot_k += kf
        tot_t0 += t_in
        tot_t1 += t_out
    res["_macro"] = {
        "facts_kept": round(100.0 * tot_k / max(1, tot_f), 1),
        "tokens_kept": round(100.0 * tot_t1 / max(1, tot_t0), 1),
        "n_facts": tot_f,
        "n_items": len(facts_rows),
    }

    res["v032_compat_macro_facts_kept"] = round(100.0 * tot32_k / max(1, tot32_f), 1)
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return res


# ---------------------------------------------------------------------------
# Suite 2: Squeez reader-free gold-line & token recall (n=150 @ r=0.5, 0.33)
# ---------------------------------------------------------------------------

_SQ_WORKER_COMP = None


def _squeez_worker_batch(args: tuple) -> list[tuple[str, float, float, int, int, float, float, int]]:
    """Worker helper evaluating a batch of Squeez items at r=0.5 and r=0.33."""
    global _SQ_WORKER_COMP
    batch, engine, force_protected, use_query = args
    if _SQ_WORKER_COMP is None or getattr(_SQ_WORKER_COMP, "engine", None) != engine:
        from ctxprune.compress import Compressor

        _SQ_WORKER_COMP = Compressor.__new__(Compressor)
        _SQ_WORKER_COMP.engine = engine
        _SQ_WORKER_COMP.rep_decay = "all"
        _SQ_WORKER_COMP.fewshot = "off"
        _SQ_WORKER_COMP.overlap = 0

    from ctxprune.atoms import atomize
    import ctxprune._v032 as v032

    comp = _SQ_WORKER_COMP
    out_rows = []
    for item, cached in batch:
        txt = item["text"]
        t0 = tok_len(txt)
        c_atoms, c_scores, c_toks = cached
        live_atoms = atomize(txt)
        atoms = live_atoms if len(live_atoms) == len(c_atoms) else c_atoms
        gold_text = "\n".join(item["gold_lines"])
        gtoks = content_tokens(gold_text)
        g_len = max(1, len(gtoks))
        n_glines = max(1, len(item["gold_lines"]))

        if engine == "v032":
            counts = [len(a.text) + (1 if a.ws else 0) for a in atoms]
            from ctxprune.atoms import is_protected

            prot = bool(force_protected)
            forced = [bool(a.text) and (prot and is_protected(a.text)) for a in atoms]
            order = sorted(range(len(atoms)), key=lambda i: -c_scores[i])
            tot_c = sum(counts)

            def _slice_fn(kp: float) -> str:
                budget = int(-(-kp * tot_c // 1))
                keep = list(forced)
                used = sum(c for c, f in zip(counts, forced) if f)
                for idx in order:
                    if used >= budget:
                        break
                    if not keep[idx] and atoms[idx].text:
                        keep[idx] = True
                        used += counts[idx]
                return v032.detok(atoms, keep)

        else:
            prep = comp._prepare_selection(
                atoms, c_scores, c_toks, txt, unit="tokens_est", query=item["query"] if use_query else None
            )

            def _slice_fn(kp: float) -> str:
                return comp._slice_prepared(prep, kp, force_protected=force_protected)["text"]

        res_rates = {}
        for rate in (0.5, 0.33):
            target = max(16, int(rate * t0))
            out = match_budget_by_keep(_slice_fn, target, rate)
            t1 = tok_len(out)
            otoks = content_tokens(out)
            tok_rec = len(gtoks & otoks) / g_len
            out_lines = {ln.strip() for ln in out.splitlines()}
            line_rec = sum(1 for g in item["gold_lines"] if g in out_lines or g in out) / n_glines
            res_rates[rate] = (tok_rec, line_rec, t1)

        tr50, lr50, t1_50 = res_rates[0.5]
        tr33, lr33, t1_33 = res_rates[0.33]
        out_rows.append((item["id"], tr50, lr50, t0, t1_50, tr33, lr33, t1_33))
    return out_rows


def aggregate_squeez_rows(rows: list[tuple]) -> dict[str, Any]:
    n = max(1, len(rows))
    t0_sum = max(1, sum(r[3] for r in rows))
    return {
        "r0.5": {
            "tok_recall": round(100.0 * sum(r[1] for r in rows) / n, 1),
            "line_recall": round(100.0 * sum(r[2] for r in rows) / n, 1),
            "tok_ratio": round(100.0 * sum(r[4] for r in rows) / t0_sum, 1),
            "n": len(rows),
        },
        "r0.33": {
            "tok_recall": round(100.0 * sum(r[5] for r in rows) / n, 1),
            "line_recall": round(100.0 * sum(r[6] for r in rows) / n, 1),
            "tok_ratio": round(100.0 * sum(r[7] for r in rows) / t0_sum, 1),
            "n": len(rows),
        },
    }


def run_suite_squeez(
    sq_rows: list[dict],
    sq_cache: dict[str, tuple],
    engine: str = "v04",
    force_protected: bool | str = "auto",
    use_query: bool = True,
    executor: ProcessPoolExecutor | None = None,
    workers: int = 4,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    pairs = [(r, sq_cache[r["id"]]) for r in sq_rows]
    if executor is not None and workers > 1 and len(pairs) >= 16:
        chunk_sz = max(8, (len(pairs) + workers - 1) // workers)
        batches = [
            (pairs[i : i + chunk_sz], engine, force_protected, use_query)
            for i in range(0, len(pairs), chunk_sz)
        ]
        raw_out = []
        for b_res in executor.map(_squeez_worker_batch, batches):
            raw_out.extend(b_res)
    else:
        raw_out = _squeez_worker_batch((pairs, engine, force_protected, use_query))

    res = aggregate_squeez_rows(raw_out)
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return res


# ---------------------------------------------------------------------------
# Suite 3: Negation / comparator / key-value meaning-flip suite (n=40)
# ---------------------------------------------------------------------------


def run_suite_negation(
    neg_rows: list[dict],
    neg_cache: dict[str, tuple],
    engine: str = "v04",
    force_protected: bool | str = "auto",
    force_live: bool = False,
    live_comp: Any | None = None,
    disable_neg_pin: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    score_map = {r["text"]: neg_cache.get(r["id"]) for r in neg_rows}
    comp = make_light_compressor(engine=engine, score_lookup=score_map, live_fallback=live_comp, force_live=force_live)

    preps = {}
    t0_lens = {}
    for r in neg_rows:
        txt = r["text"]
        t0_lens[r["id"]] = tok_len(txt)
        if engine != "v032":
            atoms, sc, tc = comp.atom_scores(txt)
            prep = comp._prepare_selection(atoms, sc, tc, txt, unit="tokens_est", query=None)
            if disable_neg_pin:
                prep = dict(prep, neg_plan=[])
            preps[r["id"]] = prep

    res: dict[str, Any] = {}
    for rate in (0.5, 0.33):
        item_pass = 0
        anch_tot = 0
        anch_kept = 0
        t0_all = t1_all = 0
        for r in neg_rows:
            rid = r["id"]
            t_in = t0_lens[rid]
            target = max(16, int(rate * t_in))
            if engine == "v032":
                out = match_budget_by_keep(
                    lambda kp, txt=r["text"]: comp.compress(txt, rate=kp, force_protected=bool(force_protected))["text"],
                    target,
                    rate,
                )
            else:
                prep = preps[rid]
                out = match_budget_by_keep(
                    lambda kp, p=prep: comp._slice_prepared(p, kp, force_protected=force_protected)["text"],
                    target,
                    rate,
                )
            t0_all += t_in
            t1_all += tok_len(out)
            out_low = out.lower()
            hits = [a.lower() in out_low for a in r["anchors"]]
            anch_tot += len(hits)
            anch_kept += sum(hits)
            if all(hits):
                item_pass += 1
        res[f"r{rate}"] = {
            "all_anchors_pct": round(100.0 * item_pass / max(1, len(neg_rows)), 1),
            "anchor_recall_pct": round(100.0 * anch_kept / max(1, anch_tot), 1),
            "tok_ratio_pct": round(100.0 * t1_all / max(1, t0_all), 1),
            "n": len(neg_rows),
        }
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return res


# ---------------------------------------------------------------------------
# Suite 4: JSON & Code syntax validity
# ---------------------------------------------------------------------------


def _build_code_and_markdown_fixtures() -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Return (python_files, markdown_code_docs) for deterministic syntax & breadcrumb checks."""
    py_candidates = [
        REPO_SRC / "ctxprune" / "__init__.py",
        REPO_SRC / "ctxprune" / "atoms.py",
        REPO_SRC / "ctxprune" / "chunking.py",
        REPO_SRC / "ctxprune" / "compat.py",
        REPO_SRC / "ctxprune" / "idcheck.py",
        REPO_SRC / "ctxprune" / "integrations.py",
        REPO_SRC / "ctxprune" / "legacy_cli.py",
        REPO_SRC / "ctxprune" / "mcp_server.py",
        REPO_SRC / "ctxprune" / "model.py",
        REPO_SRC / "ctxprune" / "plugin_cli.py",
        REPO_SRC / "ctxprune" / "serve.py",
        REPO_SRC / "ctxprune" / "stats.py",
        REPO_SRC / "ctxprune" / "tokenlabels.py",
        REPO_SRC / "ctxprune" / "usage.py",
        REPO_SRC / "ctxprune" / "_v032.py",
        REPO_SCRIPTS / "ctxprune_note.py",
        REPO_SCRIPTS / "emitted.py",
        REPO_SCRIPTS / "hook_budget.py",
        REPO_SCRIPTS / "line_encode.py",
        REPO_SCRIPTS / "read_trim.py",
    ]
    py_fixtures: list[tuple[str, str]] = []
    for p in py_candidates:
        if p.exists():
            py_fixtures.append((p.name, p.read_text(encoding="utf-8")))

    md_fixtures: list[dict[str, Any]] = []
    for idx in range(10):
        heading_h1 = f"# Architecture RFC {idx + 100}"
        heading_h2 = f"## Section {idx + 1}.2 — Worker Configuration"
        py_block = (
            f"def compute_quota_{idx}(items: list[int], scale: float = 1.{idx}) -> dict[str, float]:\n"
            f"    total = sum(x for x in items if x >= {idx})\n"
            f"    return {{'id': 'job_{idx}_9941a', 'scaled': round(total * scale, 3)}}\n"
        )
        json_block = json.dumps(
            {
                "service": f"ingestor-{idx}",
                "port": 8080 + idx,
                "retryable": False,
                "endpoint": f"https://api.example.com/v1/nodes/{idx}?utm_source=test&id={idx}",
            },
            indent=2,
        )
        filler = (
            "Routine quarterly planning observations covering regional staffing schedules and general office notes. "
            * 5
        )
        doc = (
            f"{heading_h1}\n\n{filler}\n\n{heading_h2}\n\n"
            f"Critical invariant for endpoint `/srv/app/worker_{idx}.py` with token `5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b{idx:02d}`:\n\n"
            f"```python\n{py_block}```\n\n"
            f"```json\n{json_block}\n```\n\n"
            f"| Metric | Value |\n| --- | :---: |\n| latency_ms | {10 + idx} |\n"
        )
        md_fixtures.append(
            {
                "id": f"md-code-{idx}",
                "text": doc,
                "py_block": py_block,
                "h1": heading_h1,
                "h2": heading_h2,
                "canary_id": f"5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b{idx:02d}",
            }
        )
    return py_fixtures, md_fixtures


def run_suite_json_code(
    mcp_rows: list[dict],
    mcp_cache: dict[str, tuple],
    prose_cache: dict[str, tuple],
    engine: str = "v04",
    force_live: bool = False,
    live_comp: Any | None = None,
    corrupt_json_mode: bool = False,
    corrupt_code_ast: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    from ctxprune.atoms import atomize, is_technical_text
    from ctxprune.compress import clean_web_text
    import lossless

    score_map = {r["text"]: mcp_cache.get(r["id"]) for r in mcp_rows}
    score_map.update(prose_cache)
    comp = make_light_compressor(engine=engine, score_lookup=score_map, live_fallback=live_comp, force_live=force_live)

    valid = 0
    tot_sc = kept_sc = t0_all = t1_all = 0
    compact_t1_all = 0
    for r in mcp_rows:
        raw = r["text"]
        t_in = tok_len(raw)
        t0_all += t_in
        obj = json.loads(raw)
        compact_str = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        compact_t1_all += tok_len(compact_str)

        if engine == "v032" or corrupt_json_mode:
            out = comp.compress(raw, rate=0.5, force_protected=False, mode="prune")["text"]
        else:
            out = comp.compress(raw, rate=0.5, mode="auto")["text"]
        t1_all += tok_len(out)
        try:
            json.loads(out)
            valid += 1
        except Exception:
            pass
        scalars = extract_json_scalars(obj)
        tot_sc += len(scalars)
        kept_sc += sum(1 for s in scalars if s in out)

    py_fixtures, md_fixtures = _build_code_and_markdown_fixtures()
    py_valid = 0
    py_unchanged = 0
    tech_detect_ok = 0
    for idx, (_name, src) in enumerate(py_fixtures):
        ll_out, _ = lossless.compress(src)
        if corrupt_code_ast and idx == 0:
            ll_out = "def broken_syntax(:\n    pass\n"
        try:
            ast.parse(ll_out)
            py_valid += 1
        except SyntaxError:
            pass
        if ll_out == src:
            py_unchanged += 1
        if is_technical_text(atomize(src), src):
            tech_detect_ok += 1

    md_valid = 0
    md_breadcrumb_ok = 0
    fenced_py_re = re.compile(r"```python\n(.*?)```", re.DOTALL)
    fenced_json_re = re.compile(r"```json\n(.*?)```", re.DOTALL)
    for idx, item in enumerate(md_fixtures):
        cleaned = clean_web_text(item["text"])
        if corrupt_code_ast and idx == 0:
            cleaned = cleaned.replace("return {", "return {broken:")
        m_py = fenced_py_re.search(cleaned)
        m_js = fenced_json_re.search(cleaned)
        ok_syntax = False
        if m_py and m_js:
            try:
                ast.parse(m_py.group(1))
                json.loads(m_js.group(1))
                ok_syntax = "utm_source" not in cleaned
            except Exception:
                ok_syntax = False
        if ok_syntax:
            md_valid += 1

        # Also check L3.7 heading breadcrumb preservation under v0.4 pruning
        if engine == "v032":
            pruned_md = item["text"][: len(item["text"]) // 2]
        else:
            # Score deterministically using heuristic atom properties so zero ONNX cost for synthetic markdown check
            md_atoms = atomize(item["text"])
            synth_scores = [0.85 if item["canary_id"] in a.text else 0.45 for a in md_atoms]
            prep_md = comp._prepare_selection(md_atoms, synth_scores, None, item["text"], unit="tokens_est")
            pruned_md = comp._slice_prepared(prep_md, 0.45, force_protected="auto")["text"]
        if item["canary_id"] in pruned_md and item["h1"] in pruned_md and item["h2"] in pruned_md:
            md_breadcrumb_ok += 1

    total_code_items = len(py_fixtures) + len(md_fixtures)
    valid_code_items = py_valid + md_valid
    compact_ratio = round(100.0 * compact_t1_all / max(1, t0_all), 1)
    tok_ratio = round(100.0 * t1_all / max(1, t0_all), 1)

    return {
        "json_valid_pct": round(100.0 * valid / max(1, len(mcp_rows)), 1),
        "scalar_recall_pct": round(100.0 * kept_sc / max(1, tot_sc), 1),
        "tok_ratio_pct": tok_ratio,
        "compact_only_tok_ratio_pct": compact_ratio,
        "extra_savings_vs_compact_pts": round(compact_ratio - tok_ratio, 1),
        "n_json": len(mcp_rows),
        "code_ast_valid_pct": round(100.0 * valid_code_items / max(1, total_code_items), 1),
        "py_lossless_unchanged_pct": round(100.0 * py_unchanged / max(1, len(py_fixtures)), 1),
        "tech_detector_recall_pct": round(100.0 * tech_detect_ok / max(1, len(py_fixtures)), 1),
        "md_breadcrumb_pct": round(100.0 * md_breadcrumb_ok / max(1, len(md_fixtures)), 1),
        "n_code": total_code_items,
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }


# ---------------------------------------------------------------------------
# Suite 5: docs/ideas/gate/ fidelity + fuzz + selftest + coverage + oracles
# ---------------------------------------------------------------------------

_GATE_DISCOVERED = False


def _gate_worker_chunk(args: tuple[list[int], int, bool]) -> list[dict[str, Any]]:
    global _GATE_DISCOVERED
    cids, max_mutations, check_oracle = args
    os.environ["CTXPRUNE_IDEAS_TOKENIZER"] = "proxy"
    import harness

    if not _GATE_DISCOVERED:
        harness.discover()
        _GATE_DISCOVERED = True

    out = []
    for cid in cids:
        c = harness.REGISTRY[cid]
        old_m = c.mutations
        if max_mutations >= 0:
            c.mutations = min(old_m, max_mutations)
        try:
            r = harness._run_case(c)
            blind = {}
            if check_oracle and c.kind in ("roundtrip", "equivalent", "rule"):
                surv = harness.oracle_check(c)
                blind = {k: v for k, v in surv.items() if k in harness.MUST_NOTICE}
            out.append(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "status": r.status,
                    "fixtures": r.fixtures,
                    "mutants": r.mutants,
                    "errors": r.errors[:2],
                    "blind": blind,
                }
            )
        finally:
            c.mutations = old_m
    return out


def run_suite_ideas_gate(
    executor: ProcessPoolExecutor | None = None,
    workers: int = 4,
    full_gate: bool = False,
    plant_defect: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    old_tok_env = os.environ.get("CTXPRUNE_IDEAS_TOKENIZER")
    os.environ["CTXPRUNE_IDEAS_TOKENIZER"] = "proxy"
    try:
        import harness

        harness.discover()
        load_errors = len(harness.LOAD_ERRORS)
        selftest_problems = harness.selftest()
        missing, extra = harness.coverage()

        ids = sorted(harness.REGISTRY)
        max_mut = 40 if full_gate else 4
        # Always check oracles on a stratified sample of 15 cases (or all 347 in --full-gate)
        oracle_sample_ids = set(ids if full_gate else ids[:: max(1, len(ids) // 15)])

        if executor is not None and workers > 1 and not plant_defect:
            chunk_sz = max(8, (len(ids) + workers * 3 - 1) // (workers * 3))
            chunks = [
                (ids[i : i + chunk_sz], max_mut, any(x in oracle_sample_ids for x in ids[i : i + chunk_sz]))
                for i in range(0, len(ids), chunk_sz)
            ]
            case_results = []
            for ch_res in executor.map(_gate_worker_chunk, chunks):
                case_results.extend(ch_res)
        else:
            case_results = _gate_worker_chunk((ids, max_mut, True))

        if plant_defect:
            bad_case = harness.Case(
                id=-999,
                claim="planted broken transform",
                encode=lambda t: t.replace("ERROR", ""),
                decode=lambda t: t,
                fixtures=[
                    harness.Fixture("pos", "ERROR disk full on /dev/sda1\n" * 4, True),
                    harness.Fixture("neg", "ok\n", False),
                ],
                mutations=2,
            )
            r_bad = harness._run_case(bad_case)
            case_results.append(
                {
                    "id": r_bad.id,
                    "kind": r_bad.kind,
                    "status": r_bad.status,
                    "fixtures": r_bad.fixtures,
                    "mutants": r_bad.mutants,
                    "errors": r_bad.errors[:2],
                    "blind": {},
                }
            )

        passed = sum(1 for r in case_results if r["status"] == "pass")
        failed = sum(1 for r in case_results if r["status"] == "fail")
        manual = sum(1 for r in case_results if r["status"] == "manual")
        total_mutants = sum(r["mutants"] for r in case_results)
        blind_oracles = sum(1 for r in case_results if r["blind"])
        failed_ids = [r["id"] for r in case_results if r["status"] == "fail"]

        return {
            "total_cases": len(case_results),
            "passed": passed,
            "failed": failed,
            "manual": manual,
            "total_mutants": total_mutants,
            "load_errors": load_errors,
            "selftest_problems": len(selftest_problems),
            "missing_cases": len(missing),
            "extra_cases": len(extra),
            "blind_oracles": blind_oracles,
            "failed_ids": failed_ids,
            "elapsed_s": round(time.perf_counter() - t0, 3),
        }
    finally:
        if old_tok_env is None:
            os.environ.pop("CTXPRUNE_IDEAS_TOKENIZER", None)
        else:
            os.environ["CTXPRUNE_IDEAS_TOKENIZER"] = old_tok_env


# ---------------------------------------------------------------------------
# Suite 6: CPU latency, live ONNX parity, golden 0.3.2 & memory cap checks
# ---------------------------------------------------------------------------


def run_suite_latency_memory(
    facts_rows: list[dict],
    facts_cache: dict[str, tuple],
    plant_regression: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    from ctxprune.atoms import atomize
    from ctxprune.compress import Compressor, ScoredText
    import lossless

    # 6.1 Import-free startup check in a fresh Python interpreter
    probe_code = (
        "import sys, time; t0 = time.perf_counter(); import ctxprune; "
        "dt = (time.perf_counter() - t0) * 1000; "
        "heavy = ('onnxruntime', 'numpy', 'tokenizers', 'torch', 'transformers', 'tiktoken', 'ctxprune.compress', 'ctxprune.compat'); "
        "loaded = [m for m in heavy if m in sys.modules]; "
        "import json; print(json.dumps({'import_ms': round(dt, 2), 'eager_heavy': loaded}))"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_SRC))
    p = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True, env=env, check=True)
    imp_info = json.loads(p.stdout.strip())
    eager_heavy_count = len(imp_info["eager_heavy"])
    import_ms = float(imp_info["import_ms"])

    # 6.2 Golden 0.3.2 byte-exact compatibility check (7 entries in tests/golden/golden_032.json)
    golden = json.loads(GOLDEN_032_PATH.read_text(encoding="utf-8"))
    golden_texts = {
        "json": json.dumps(
            [
                {
                    "id": f"ord_8f3a2c{i:02d}",
                    "status": "failed",
                    "amount": 1999 + i,
                    "ip": f"10.0.{i}.7",
                    "path": f"/srv/app/v1.2.{i}/worker.py",
                    "msg": "Timeout while waiting for the payment provider to answer the capture request",
                }
                for i in range(12)
            ],
            indent=2,
        ),
        "log": "\n".join(
            f"2026-11-18T10:{i:02d}:03Z WARN worker-{i % 3} retrying job 4f3a-91{i} after connection reset "
            f"by peer (attempt {i}/5)"
            for i in range(40)
        )
        + "\nERROR job 4f3a-9139 failed permanently: database is locked\n",
        "prose": (
            "The quarterly planning meeting covered the migration of the billing service to the new cluster, the open "
            "questions about data retention for European customers, and a proposal to move the on-call rotation from "
            "weekly to bi-weekly shifts. Most attendees agreed that the migration should wait until the retention "
            "policy is settled, because moving the data twice would cost more than the delay. "
        )
        * 4,
    }
    comp_live = Compressor()
    assert 1 <= comp_live.threads <= 4

    golden_cache_path = CACHE_DIR / "golden_032_scores.pkl"
    golden_raw_cache = pickle.load(golden_cache_path.open("rb")) if golden_cache_path.exists() else {}
    golden_score_map = {golden_texts[k]: golden_raw_cache[k] for k in golden_texts if k in golden_raw_cache}
    comp_golden = make_light_compressor(engine="auto", score_lookup=golden_score_map, live_fallback=comp_live)

    golden_total = 0
    golden_pass = 0
    for key, want in golden.items():
        if key.startswith("prune/"):
            golden_total += 1
            _, name, keep_s, prot_s = key.split("/")
            got = comp_golden.compress(
                golden_texts[name], rate=float(keep_s), force_protected=(prot_s == "True")
            )["text"]
            if got == want:
                golden_pass += 1
        elif key == "Compressor.text":
            golden_total += 1
            got = comp_golden.compress(golden_texts["log"], rate=0.5, keep_structure=True)["text"]
            if got == want:
                golden_pass += 1

    # 6.3 Live ONNX parity vs frozen facts_scores.pkl cache on the 2 shortest facts items
    parity_diffs = 0
    max_score_diff = 0.0
    shortest_facts = sorted(facts_rows, key=lambda x: len(x["text"]))[:2]
    for r in shortest_facts:
        txt = r["text"]
        _, s_cap, _ = comp_live.atom_scores(txt)
        _, s_cache, _ = facts_cache[r["id"]]
        diff_c = max((abs(a - b) for a, b in zip(s_cap, s_cache)), default=0.0)
        max_score_diff = max(max_score_diff, diff_c)
        if diff_c > 1e-5:
            parity_diffs += 1

    # 6.4 128-token ONNX CPU inference latency (1 warmup + 3 timed runs)
    import numpy as np

    sample_long = "\n\n".join(r["text"] for r in shortest_facts)
    ids_128 = comp_live.tok(sample_long, add_special_tokens=False)["input_ids"][:128]
    arr_128 = np.array([ids_128], dtype=np.int64)
    feed_128 = {"input_ids": arr_128, "attention_mask": np.ones_like(arr_128)}
    comp_live.session.run(["logits"], feed_128)
    onnx_128_times = []
    for _ in range(3):
        ts = time.perf_counter()
        comp_live.session.run(["logits"], feed_128)
        onnx_128_times.append((time.perf_counter() - ts) * 1000.0)
    onnx_128_med_ms = round(statistics.median(onnx_128_times), 1)
    onnx_128_min_ms = round(min(onnx_128_times), 1)

    # 6.5 T1.7 pre-scored ingest (ScoredText) parity across all 120 facts items + 10-chunk query latency
    score_map = {r["text"]: facts_cache[r["id"]] for r in facts_rows}
    comp_cached = make_light_compressor(engine="v04", score_lookup=score_map, live_fallback=comp_live)
    scored_texts: list[ScoredText] = []
    atom_ious: list[float] = []
    byte_exact = 0
    total_atoms = 0
    total_u8_bytes = 0
    for r in facts_rows:
        txt = r["text"]
        direct = comp_cached.compress(txt, rate=0.5, force_protected="auto")["text"]
        scored = comp_cached.score(txt)
        via_scored = comp_cached.prune_scored(scored, rate=0.5, force_protected="auto")["text"]
        scored_texts.append(scored)
        d_atoms = {a.text for a in atomize(direct) if a.text}
        s_atoms = {a.text for a in atomize(via_scored) if a.text}
        atom_ious.append(len(d_atoms & s_atoms) / max(1, len(d_atoms | s_atoms)))
        byte_exact += int(direct == via_scored)
        total_atoms += len(atomize(txt))
        total_u8_bytes += len(scored.scores_u8)

    batch_10 = scored_texts[:10]
    for sc in batch_10:
        comp_cached.prune_scored(sc, rate=0.5, force_protected="auto")
    t17_noq_times = []
    for _ in range(100):
        ts = time.perf_counter()
        for sc in batch_10:
            comp_cached.prune_scored(sc, rate=0.5, force_protected="auto")
        t17_noq_times.append((time.perf_counter() - ts) * 1000.0)

    for sc in batch_10:
        comp_cached.prune_scored(sc, rate=0.5, force_protected="auto", query="worker error status latency")
    t17_q_times = []
    for _ in range(25):
        ts = time.perf_counter()
        for sc in batch_10:
            comp_cached.prune_scored(sc, rate=0.5, force_protected="auto", query="worker error status latency")
        t17_q_times.append((time.perf_counter() - ts) * 1000.0)

    # 6.6 Lossless safe in-process latency across 20 tool outputs
    ll_samples = [r["text"] for r in facts_rows[:20]]
    for s in ll_samples:
        lossless.compress(s)
    ll_times = []
    for s in ll_samples:
        ts = time.perf_counter()
        lossless.compress(s)
        ll_times.append((time.perf_counter() - ts) * 1000.0)

    # 6.7 Peak RSS memory cap check (prefer /proc/self/status VmHWM on Linux; fallback to ru_maxrss)
    peak_rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    try:
        for ln in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if ln.startswith("VmHWM:"):
                peak_rss_mb = round(int(ln.split(":", 1)[1].strip().split()[0]) / 1024.0, 1)
                break
    except Exception:
        pass
    bytes_per_atom = round(total_u8_bytes / max(1, total_atoms), 3)

    t17_noq_med = round(statistics.median(t17_noq_times), 3)
    if plant_regression:
        eager_heavy_count = 2
        t17_noq_med = 2.500
        peak_rss_mb = 2048.0

    return {
        "eager_heavy_imports": eager_heavy_count,
        "import_startup_ms": import_ms,
        "golden_032_pass": golden_pass,
        "golden_032_total": golden_total,
        "threads_default": comp_live.threads,
        "onnx_parity_diffs": parity_diffs,
        "onnx_max_score_diff": round(max_score_diff, 7),
        "onnx_128_med_ms": onnx_128_med_ms,
        "onnx_128_min_ms": onnx_128_min_ms,
        "t17_atom_iou_pct": round(100.0 * statistics.mean(atom_ious), 2),
        "t17_byte_exact": byte_exact,
        "t17_noq_med_ms_per_10": t17_noq_med,
        "t17_noq_mean_ms_per_10": round(statistics.mean(t17_noq_times), 3),
        "t17_q_med_ms_per_10": round(statistics.median(t17_q_times), 3),
        "lossless_inproc_med_ms": round(statistics.median(ll_times), 3),
        "lossless_p95_ms": round(sorted(ll_times)[int(0.95 * len(ll_times)) - 1], 3),
        "peak_rss_mb": peak_rss_mb,
        "scored_bytes_per_atom": bytes_per_atom,
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }


# ---------------------------------------------------------------------------
# Gate evaluation & Markdown diff table generation
# ---------------------------------------------------------------------------

DEFAULT_GATES: dict[str, dict[str, Any]] = {
    "facts_macro_pct": {"cat": "1. llmlingua_facts", "op": ">=", "val": 95.0, "unit": "%"},
    "facts_agent_pct": {"cat": "1. llmlingua_facts", "op": ">=", "val": 95.0, "unit": "%"},
    "facts_mcp_pct": {"cat": "1. llmlingua_facts", "op": ">=", "val": 94.0, "unit": "%"},
    "facts_v032_compat_pct": {"cat": "1. llmlingua_facts", "op": "==", "val": 62.1, "unit": "%"},
    "squeez_r05_tok_pct": {"cat": "2. Squeez Recall", "op": ">=", "val": 85.0, "unit": "%"},
    "squeez_r05_line_pct": {"cat": "2. Squeez Recall", "op": ">=", "val": 58.0, "unit": "%"},
    "squeez_r033_tok_pct": {"cat": "2. Squeez Recall", "op": ">=", "val": 76.0, "unit": "%"},
    "squeez_r033_line_pct": {"cat": "2. Squeez Recall", "op": ">=", "val": 43.0, "unit": "%"},
    "neg_r05_all_anchors_pct": {"cat": "3. Negation Suite", "op": ">=", "val": 80.0, "unit": "%"},
    "neg_r05_anchor_recall_pct": {"cat": "3. Negation Suite", "op": ">=", "val": 88.0, "unit": "%"},
    "neg_r033_all_anchors_pct": {"cat": "3. Negation Suite", "op": ">=", "val": 65.0, "unit": "%"},
    "json_valid_pct": {"cat": "4. JSON/Code Validity", "op": "==", "val": 100.0, "unit": "%"},
    "json_scalar_recall_pct": {"cat": "4. JSON/Code Validity", "op": ">=", "val": 94.0, "unit": "%"},
    "json_tok_ratio_pct": {"cat": "4. JSON/Code Validity", "op": "<=", "val": 65.0, "unit": "%"},
    "code_ast_valid_pct": {"cat": "4. JSON/Code Validity", "op": "==", "val": 100.0, "unit": "%"},
    "md_breadcrumb_pct": {"cat": "4. JSON/Code Validity", "op": "==", "val": 100.0, "unit": "%"},
    "gate_failed_cases": {"cat": "5. docs/ideas/gate", "op": "==", "val": 0, "unit": ""},
    "gate_selftest_problems": {"cat": "5. docs/ideas/gate", "op": "==", "val": 0, "unit": ""},
    "gate_missing_coverage": {"cat": "5. docs/ideas/gate", "op": "==", "val": 0, "unit": ""},
    "gate_blind_oracles": {"cat": "5. docs/ideas/gate", "op": "==", "val": 0, "unit": ""},
    "eager_heavy_imports": {"cat": "6. Latency & Memory", "op": "==", "val": 0, "unit": ""},
    "golden_032_pass": {"cat": "6. Latency & Memory", "op": "==", "val": 7, "unit": "/7"},
    "onnx_parity_diffs": {"cat": "6. Latency & Memory", "op": "==", "val": 0, "unit": ""},
    "onnx_128_med_ms": {"cat": "6. Latency & Memory", "op": "<=", "val": 300.0, "unit": " ms"},
    "t17_atom_iou_pct": {"cat": "6. Latency & Memory", "op": ">=", "val": 99.0, "unit": "%"},
    "t17_noq_med_ms_per_10": {"cat": "6. Latency & Memory", "op": "<", "val": 1.000, "unit": " ms"},
    "t17_q_med_ms_per_10": {"cat": "6. Latency & Memory", "op": "<=", "val": 10.0, "unit": " ms"},
    "lossless_inproc_med_ms": {"cat": "6. Latency & Memory", "op": "<=", "val": 5.0, "unit": " ms"},
    "peak_rss_mb": {"cat": "6. Latency & Memory", "op": "<=", "val": 1024.0, "unit": " MB"},
    "total_wall_s": {"cat": "6. Latency & Memory", "op": "<", "val": 60.0, "unit": " s"},
}


def extract_flat_metrics(report: dict[str, Any]) -> dict[str, float | int]:
    f = report["facts"]
    sq = report["squeez"]
    neg = report["negation"]
    jc = report["json_code_validity"]
    ig = report["ideas_gate"]
    lm = report["latency_memory"]
    return {
        "facts_macro_pct": f["_macro"]["facts_kept"],
        "facts_agent_pct": f["agent tool output"]["facts_kept"],
        "facts_mcp_pct": f["MCP result"]["facts_kept"],
        "facts_v032_compat_pct": f["v032_compat_macro_facts_kept"],
        "squeez_r05_tok_pct": sq["r0.5"]["tok_recall"],
        "squeez_r05_line_pct": sq["r0.5"]["line_recall"],
        "squeez_r033_tok_pct": sq["r0.33"]["tok_recall"],
        "squeez_r033_line_pct": sq["r0.33"]["line_recall"],
        "neg_r05_all_anchors_pct": neg["r0.5"]["all_anchors_pct"],
        "neg_r05_anchor_recall_pct": neg["r0.5"]["anchor_recall_pct"],
        "neg_r033_all_anchors_pct": neg["r0.33"]["all_anchors_pct"],
        "json_valid_pct": jc["json_valid_pct"],
        "json_scalar_recall_pct": jc["scalar_recall_pct"],
        "json_tok_ratio_pct": jc["tok_ratio_pct"],
        "code_ast_valid_pct": jc["code_ast_valid_pct"],
        "md_breadcrumb_pct": jc["md_breadcrumb_pct"],
        "gate_failed_cases": ig["failed"],
        "gate_selftest_problems": ig["selftest_problems"],
        "gate_missing_coverage": ig["missing_cases"] + ig["extra_cases"] + ig["load_errors"],
        "gate_blind_oracles": ig["blind_oracles"],
        "eager_heavy_imports": lm["eager_heavy_imports"],
        "golden_032_pass": lm["golden_032_pass"],
        "onnx_parity_diffs": lm["onnx_parity_diffs"],
        "onnx_128_med_ms": lm["onnx_128_med_ms"],
        "t17_atom_iou_pct": lm["t17_atom_iou_pct"],
        "t17_noq_med_ms_per_10": lm["t17_noq_med_ms_per_10"],
        "t17_q_med_ms_per_10": lm["t17_q_med_ms_per_10"],
        "lossless_inproc_med_ms": lm["lossless_inproc_med_ms"],
        "peak_rss_mb": lm["peak_rss_mb"],
        "total_wall_s": report["total_wall_s"],
    }


def check_op(val: float | int, op: str, target: float | int) -> bool:
    if op == ">=":
        return val >= target - 1e-9
    if op == "<=":
        return val <= target + 1e-9
    if op == "<":
        return val < target
    if op == ">":
        return val > target
    if op == "==":
        return abs(val - target) <= 1e-6
    raise ValueError(f"Unknown operator {op!r}")


def evaluate_gates(
    report: dict[str, Any],
    gates: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    gates = gates or DEFAULT_GATES
    flat = extract_flat_metrics(report)
    rows: list[dict[str, Any]] = []
    all_pass = True
    for key, spec in gates.items():
        cur = flat[key]
        ok = check_op(cur, spec["op"], spec["val"])
        if not ok:
            all_pass = False
        rows.append(
            {
                "key": key,
                "category": spec["cat"],
                "current": cur,
                "op": spec["op"],
                "threshold": spec["val"],
                "unit": spec["unit"],
                "passed": ok,
            }
        )
    return all_pass, rows


def format_markdown_scorecard(
    report: dict[str, Any],
    gate_rows: list[dict[str, Any]],
    baseline: dict[str, Any] | None = None,
) -> str:
    v032_flat: dict[str, Any] = {}
    v04_flat: dict[str, Any] = {}
    if baseline:
        if "v032_raw_flat" in baseline:
            v032_flat = baseline["v032_raw_flat"]
        if "v04_ref_flat" in baseline:
            v04_flat = baseline["v04_ref_flat"]

    def _fmt_val(v: Any, unit: str) -> str:
        if v is None or v == "—":
            return "—"
        if isinstance(v, float):
            if unit == " ms" and v < 2.0:
                return f"{v:.3f}{unit}"
            return f"{v:.1f}{unit}" if abs(v - round(v, 1)) < 1e-6 else f"{v:.2f}{unit}"
        return f"{v}{unit}"

    def _fmt_delta(cur: Any, ref: Any, unit: str) -> str:
        if ref is None or ref == "—" or not isinstance(cur, (int, float)) or not isinstance(ref, (int, float)):
            return "—"
        d = cur - ref
        if abs(d) < 1e-6:
            return "0.0"
        if unit == " ms" and abs(d) < 2.0:
            return f"{d:+.3f}"
        return f"{d:+.1f}"

    lines = [
        f"# ctxprune Zero-GPU CI Quality Gate Scorecard (`engine={report['engine']}`)",
        "",
        f"- **Overall Verdict:** **{'PASS' if report['passed'] else 'FAIL'}** "
        f"({sum(1 for r in gate_rows if r['passed'])}/{len(gate_rows)} gates passed in **{report['total_wall_s']:.2f} s**)",
        f"- **Suite Timings:** facts `{report['facts']['elapsed_s']:.2f}s` · "
        f"squeez `{report['squeez']['elapsed_s']:.2f}s` · "
        f"negation `{report['negation']['elapsed_s']:.2f}s` · "
        f"json/code `{report['json_code_validity']['elapsed_s']:.2f}s` · "
        f"ideas/gate `{report['ideas_gate']['elapsed_s']:.2f}s` ({report['ideas_gate']['total_cases']} cases, {report['ideas_gate']['total_mutants']} mutants) · "
        f"latency/mem `{report['latency_memory']['elapsed_s']:.2f}s`",
        "",
        "| Category | Metric | `v0.3.2` Frozen | `v0.4` Frozen Ref | Current | Δ vs `v0.3.2` | Δ vs `v0.4` Ref | Gate | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for r in gate_rows:
        k = r["key"]
        u = r["unit"]
        cur = r["current"]
        ref32 = v032_flat.get(k)
        ref04 = v04_flat.get(k)
        gate_str = f"`{r['op']} {_fmt_val(r['threshold'], u)}`"
        status_str = "PASS" if r["passed"] else "**FAIL**"
        lines.append(
            f"| {r['category']} | `{k}` | {_fmt_val(ref32, u)} | {_fmt_val(ref04, u)} | "
            f"**{_fmt_val(cur, u)}** | {_fmt_delta(cur, ref32, u)} | {_fmt_delta(cur, ref04, u)} | "
            f"{gate_str} | {status_str} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intentional regression self-test across all 6 categories
# ---------------------------------------------------------------------------


def run_intentional_regression_selftest(
    clean_report: dict[str, Any],
    facts_rows: list[dict],
    facts_cache: dict[str, tuple],
    sq_rows: list[dict],
    sq_cache: dict[str, tuple],
    neg_rows: list[dict],
    neg_cache: dict[str, tuple],
    mcp_rows: list[dict],
    mcp_cache: dict[str, tuple],
    prose_cache: dict[str, tuple],
) -> tuple[bool, list[dict[str, Any]]]:
    """Plant an intentional defect in each of the 6 categories and verify the gate catches 6/6."""
    t0 = time.perf_counter()
    checks: list[dict[str, Any]] = []

    # 1. Category 1 (llmlingua_facts): disable protection on technical text (simulating T1.2 regression -> 62.1% < 95.0%)
    bad_facts = run_suite_facts(facts_rows, facts_cache, engine="v032", force_protected=False)
    rep1 = dict(clean_report, facts=bad_facts)
    ok1, rows1 = evaluate_gates(rep1)
    failed1 = [r["key"] for r in rows1 if not r["passed"] and r["category"].startswith("1.")]
    checks.append(
        {
            "category": "1. llmlingua_facts",
            "planted": "force_protected=False on technical facts (macro 96.2% -> 62.1%)",
            "caught": (not ok1) and bool(failed1),
            "failed_gates": failed1,
        }
    )

    # 2. Category 2 (Squeez recall): drop query= boost (simulating T1.1 query regression -> 64.6% < 85.0%)
    sq_sub = sq_rows[:30]
    bad_sq = run_suite_squeez(sq_sub, sq_cache, engine="v04", force_protected="auto", use_query=False, workers=1)
    rep2 = dict(clean_report, squeez=bad_sq)
    ok2, rows2 = evaluate_gates(rep2)
    failed2 = [r["key"] for r in rows2 if not r["passed"] and r["category"].startswith("2.")]
    checks.append(
        {
            "category": "2. Squeez Recall",
            "planted": "disable query= lexical boost on Squeez (r=0.5 tok_recall -> <85%)",
            "caught": (not ok2) and bool(failed2),
            "failed_gates": failed2,
        }
    )

    # 3. Category 3 (Negation suite): disable _pin_negation_anchors (simulating L1.1 regression -> 50.0% < 80.0%)
    bad_neg = run_suite_negation(neg_rows, neg_cache, engine="v04", force_protected="auto", disable_neg_pin=True)
    rep3 = dict(clean_report, negation=bad_neg)
    ok3, rows3 = evaluate_gates(rep3)
    failed3 = [r["key"] for r in rows3 if not r["passed"] and r["category"].startswith("3.")]
    checks.append(
        {
            "category": "3. Negation Suite",
            "planted": "disable _pin_negation_anchors (r=0.5 all_anchors 82.5% -> 50.0%)",
            "caught": (not ok3) and bool(failed3),
            "failed_gates": failed3,
        }
    )

    # 4. Category 4 (JSON & Code syntax validity): bypass JSON-values-only routing + corrupt Python AST
    bad_jc = run_suite_json_code(
        mcp_rows[:10], mcp_cache, prose_cache, engine="v04", corrupt_json_mode=True, corrupt_code_ast=True
    )
    rep4 = dict(clean_report, json_code_validity=bad_jc)
    ok4, rows4 = evaluate_gates(rep4)
    failed4 = [r["key"] for r in rows4 if not r["passed"] and r["category"].startswith("4.")]
    checks.append(
        {
            "category": "4. JSON/Code Validity",
            "planted": "raw token pruning on JSON + broken Python AST in code block",
            "caught": (not ok4) and bool(failed4),
            "failed_gates": failed4,
        }
    )

    # 5. Category 5 (docs/ideas/gate): plant a defective lossy transform claiming roundtrip fidelity
    bad_ig = dict(clean_report["ideas_gate"], failed=1, failed_ids=[-999])
    # Also verify live harness._run_case actually fails on a planted broken Case
    import harness

    planted_case = harness.Case(
        id=-999,
        claim="planted defective roundtrip",
        encode=lambda t: t.replace("ERROR", ""),
        decode=lambda t: t,
        fixtures=[
            harness.Fixture("pos", "ERROR disk full on /dev/sda1\n" * 4, True),
            harness.Fixture("neg", "ok\n", False),
        ],
        mutations=2,
    )
    r_planted = harness._run_case(planted_case)
    bad_ig["failed"] = 1 if r_planted.status == "fail" else 0
    rep5 = dict(clean_report, ideas_gate=bad_ig)
    ok5, rows5 = evaluate_gates(rep5)
    failed5 = [r["key"] for r in rows5 if not r["passed"] and r["category"].startswith("5.")]
    checks.append(
        {
            "category": "5. docs/ideas/gate",
            "planted": "lossy transform claiming byte-exact roundtrip in harness._run_case",
            "caught": (not ok5) and bool(failed5) and (r_planted.status == "fail"),
            "failed_gates": failed5,
        }
    )

    # 6. Category 6 (CPU latency & memory cap): simulate eager heavy import + T1.7 latency spike + RSS cap breach
    bad_lm = dict(
        clean_report["latency_memory"],
        eager_heavy_imports=2,
        t17_noq_med_ms_per_10=2.500,
        peak_rss_mb=2048.0,
    )
    rep6 = dict(clean_report, latency_memory=bad_lm)
    ok6, rows6 = evaluate_gates(rep6)
    failed6 = [r["key"] for r in rows6 if not r["passed"] and r["category"].startswith("6.")]
    checks.append(
        {
            "category": "6. Latency & Memory",
            "planted": "eager heavy import + T1.7 latency > 1.0ms + peak RSS > 1024MB",
            "caught": (not ok6) and len(failed6) >= 3,
            "failed_gates": failed6,
        }
    )

    all_caught = all(c["caught"] for c in checks)
    elapsed = round(time.perf_counter() - t0, 3)
    for c in checks:
        c["selftest_elapsed_s"] = elapsed
    return all_caught, checks


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def load_corpora_and_caches(force_live: bool = False) -> dict[str, Any]:
    facts_path = DATA_DIR / "facts_items.jsonl"
    sq_path = DATA_DIR / "squeez_sample.jsonl"
    neg_path = DATA_DIR / "negation_suite.json"
    mcp_path = DATA_DIR / "mcp_json_items.jsonl"

    facts_rows = [json.loads(l) for l in facts_path.open(encoding="utf-8")]
    sq_rows = [json.loads(l) for l in sq_path.open(encoding="utf-8")]
    neg_rows = json.loads(neg_path.read_text(encoding="utf-8"))
    mcp_rows = [json.loads(l) for l in mcp_path.open(encoding="utf-8")]

    facts_cache = (
        pickle.load((CACHE_DIR / "facts_scores.pkl").open("rb"))
        if not force_live and (CACHE_DIR / "facts_scores.pkl").exists()
        else {}
    )
    sq_cache = (
        pickle.load((CACHE_DIR / "squeez_scores.pkl").open("rb"))
        if not force_live and (CACHE_DIR / "squeez_scores.pkl").exists()
        else {}
    )
    neg_cache = (
        pickle.load((CACHE_DIR / "negation_scores.pkl").open("rb"))
        if not force_live and (CACHE_DIR / "negation_scores.pkl").exists()
        else {}
    )
    mcp_cache = (
        pickle.load((CACHE_DIR / "mcp_json_scores.pkl").open("rb"))
        if not force_live and (CACHE_DIR / "mcp_json_scores.pkl").exists()
        else {}
    )
    prose_cache_path = CACHE_DIR / "mcp_json_prose_scores.pkl"
    prose_cache = (
        pickle.load(prose_cache_path.open("rb"))
        if not force_live and prose_cache_path.exists()
        else {}
    )

    return {
        "facts_rows": facts_rows,
        "sq_rows": sq_rows,
        "neg_rows": neg_rows,
        "mcp_rows": mcp_rows,
        "facts_cache": facts_cache,
        "sq_cache": sq_cache,
        "neg_cache": neg_cache,
        "mcp_cache": mcp_cache,
        "prose_cache": prose_cache,
        "hashes": {
            "facts_items.jsonl": sha256_file(facts_path),
            "squeez_sample.jsonl": sha256_file(sq_path),
            "negation_suite.json": sha256_file(neg_path),
            "mcp_json_items.jsonl": sha256_file(mcp_path),
            "golden_032.json": sha256_file(GOLDEN_032_PATH),
        },
    }


def run_quality_gate(
    engine: str = "v04",
    workers: int = 4,
    full_gate: bool = False,
    force_live: bool = False,
    baseline_path: Path | None = DEFAULT_BASELINE_PATH,
    run_selftest: bool = False,
) -> dict[str, Any]:
    t_start = time.perf_counter()
    data = load_corpora_and_caches(force_live=force_live)
    prot: bool | str = False if engine == "v032" else "auto"
    use_q = engine != "v032"

    # Phase 1: Run worker-pool suites (Squeez + docs/ideas/gate) & fast main-process suites (facts, negation, json/code)
    if workers > 1 and not force_live:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            sq_res = run_suite_squeez(
                data["sq_rows"],
                data["sq_cache"],
                engine=engine,
                force_protected=prot,
                use_query=use_q,
                executor=ex,
                workers=workers,
            )
            ig_res = run_suite_ideas_gate(executor=ex, workers=workers, full_gate=full_gate)
    else:
        sq_res = run_suite_squeez(
            data["sq_rows"],
            data["sq_cache"],
            engine=engine,
            force_protected=prot,
            use_query=use_q,
            executor=None,
            workers=1,
        )
        ig_res = run_suite_ideas_gate(executor=None, workers=1, full_gate=full_gate)

    facts_res = run_suite_facts(
        data["facts_rows"],
        data["facts_cache"],
        engine=engine,
        force_protected=prot,
        force_live=force_live,
    )
    neg_res = run_suite_negation(
        data["neg_rows"],
        data["neg_cache"],
        engine=engine,
        force_protected=prot,
        force_live=force_live,
    )
    jc_res = run_suite_json_code(
        data["mcp_rows"],
        data["mcp_cache"],
        data["prose_cache"],
        engine=engine,
        force_live=force_live,
    )

    # Phase 2: Uncontended single-process CPU latency, live ONNX parity, golden 0.3.2 & memory cap checks
    lm_res = run_suite_latency_memory(data["facts_rows"], data["facts_cache"])

    total_wall_s = round(time.perf_counter() - t_start, 2)
    report: dict[str, Any] = {
        "engine": engine,
        "total_wall_s": total_wall_s,
        "hashes": data["hashes"],
        "facts": facts_res,
        "squeez": sq_res,
        "negation": neg_res,
        "json_code_validity": jc_res,
        "ideas_gate": ig_res,
        "latency_memory": lm_res,
    }

    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path is not None and baseline_path.exists()
        else None
    )
    gates = baseline.get("gates", DEFAULT_GATES) if baseline else DEFAULT_GATES
    passed, gate_rows = evaluate_gates(report, gates)
    report["passed"] = passed
    report["gate_rows"] = gate_rows

    if run_selftest:
        st_ok, st_checks = run_intentional_regression_selftest(
            report,
            data["facts_rows"],
            data["facts_cache"],
            data["sq_rows"],
            data["sq_cache"],
            data["neg_rows"],
            data["neg_cache"],
            data["mcp_rows"],
            data["mcp_cache"],
            data["prose_cache"],
        )
        report["selftest_passed"] = st_ok
        report["selftest_checks"] = st_checks
        report["total_wall_with_selftest_s"] = round(time.perf_counter() - t_start, 2)

    report["markdown"] = format_markdown_scorecard(report, gate_rows, baseline=baseline)
    return report


def build_and_write_baseline(out_path: Path, workers: int = 4) -> dict[str, Any]:
    """Run both frozen v0.3.2 and current v0.4-lab reference to write docs/bench/ci_quality_gate_baseline.json."""
    data = load_corpora_and_caches(force_live=False)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        sq_032_raw = run_suite_squeez(
            data["sq_rows"], data["sq_cache"], engine="v032", force_protected=False, use_query=False, executor=ex, workers=workers
        )
        sq_032_prot = run_suite_squeez(
            data["sq_rows"], data["sq_cache"], engine="v032", force_protected=True, use_query=False, executor=ex, workers=workers
        )
    f_032_raw = run_suite_facts(data["facts_rows"], data["facts_cache"], engine="v032", force_protected=False)
    f_032_prot = run_suite_facts(data["facts_rows"], data["facts_cache"], engine="v032", force_protected=True)
    n_032_raw = run_suite_negation(data["neg_rows"], data["neg_cache"], engine="v032", force_protected=False)
    n_032_prot = run_suite_negation(data["neg_rows"], data["neg_cache"], engine="v032", force_protected=True)
    jc_032_raw = run_suite_json_code(
        data["mcp_rows"], data["mcp_cache"], data["prose_cache"], engine="v032"
    )

    v04_report = run_quality_gate(engine="v04", workers=workers, baseline_path=None)
    v032_dummy_report = {
        "engine": "v032",
        "total_wall_s": v04_report["total_wall_s"],
        "facts": f_032_raw,
        "squeez": sq_032_raw,
        "negation": n_032_raw,
        "json_code_validity": jc_032_raw,
        "ideas_gate": v04_report["ideas_gate"],
        "latency_memory": v04_report["latency_memory"],
    }

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()

    baseline_obj = {
        "schema_version": "1.0",
        "created_at": "2026-11-19",
        "git_commit": git_head,
        "hashes": data["hashes"],
        "gates": DEFAULT_GATES,
        "v032_raw": {
            "facts": f_032_raw,
            "squeez": sq_032_raw,
            "negation": n_032_raw,
            "json_code_validity": jc_032_raw,
        },
        "v032_protect": {
            "facts": f_032_prot,
            "squeez": sq_032_prot,
            "negation": n_032_prot,
        },
        "v032_raw_flat": extract_flat_metrics(v032_dummy_report),
        "v04_ref": {
            "facts": v04_report["facts"],
            "squeez": v04_report["squeez"],
            "negation": v04_report["negation"],
            "json_code_validity": v04_report["json_code_validity"],
            "ideas_gate": v04_report["ideas_gate"],
            "latency_memory": v04_report["latency_memory"],
        },
        "v04_ref_flat": extract_flat_metrics(v04_report),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(baseline_obj, indent=2) + "\n", encoding="utf-8")
    return baseline_obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=("v04", "v032"), default="v04", help="Compressor engine to score (default: v04)")
    ap.add_argument("--workers", type=int, default=4, help="Parallel worker processes for Squeez & ideas/gate")
    ap.add_argument("--full-gate", action="store_true", help="Run 40 mutations + full oracle sweep on all 347 cases")
    ap.add_argument("--live-onnx", action="store_true", help="Run live ONNX inference on every item instead of caches")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH, help="Frozen baseline JSON path")
    ap.add_argument("--write-baseline", type=Path, default=None, help="Compute and write frozen baseline JSON")
    ap.add_argument("--verify-v032-baseline", action="store_true", help="Assert engine=v032 matches frozen v032_raw")
    ap.add_argument("--selftest", action="store_true", help="Inject regressions in all 6 categories & verify caught")
    ap.add_argument("--json", type=Path, default=None, help="Write machine-readable JSON report to path")
    ap.add_argument("--markdown", type=Path, default=None, help="Write Markdown scorecard table to path")
    args = ap.parse_args(argv)

    if args.write_baseline is not None:
        b = build_and_write_baseline(args.write_baseline, workers=args.workers)
        print(f"Wrote frozen baseline JSON to {args.write_baseline} (commit {b['git_commit'][:10]})")
        return 0

    report = run_quality_gate(
        engine=args.engine,
        workers=args.workers,
        full_gate=args.full_gate,
        force_live=args.live_onnx,
        baseline_path=args.baseline,
        run_selftest=args.selftest,
    )

    print(report["markdown"])

    if args.selftest:
        print("\n## Intentional Regression Self-Test (`--selftest`)\n")
        print("| Category | Planted Regression | Failed Gates | Result |")
        print("|---|---|---|---|")
        for c in report["selftest_checks"]:
            fg = ", ".join(f"`{g}`" for g in c["failed_gates"]) or "none"
            st = "CAUGHT (PASS)" if c["caught"] else "**MISSED (FAIL)**"
            print(f"| {c['category']} | {c['planted']} | {fg} | {st} |")
        print(
            f"\nSelf-test summary: **{sum(1 for c in report['selftest_checks'] if c['caught'])}/"
            f"{len(report['selftest_checks'])}** planted regressions caught "
            f"(total runtime with selftest: **{report['total_wall_with_selftest_s']:.2f} s**)."
        )

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        out_json = {k: v for k, v in report.items() if k != "markdown"}
        args.json.write_text(json.dumps(out_json, indent=2) + "\n", encoding="utf-8")

    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        md_text = report["markdown"]
        if args.selftest:
            md_lines = [
                md_text,
                "",
                "## Intentional Regression Self-Test (`--selftest`)",
                "",
                "| Category | Planted Regression | Failed Gates | Result |",
                "|---|---|---|---|",
            ]
            for c in report["selftest_checks"]:
                fg = ", ".join(f"`{g}`" for g in c["failed_gates"]) or "none"
                st = "CAUGHT (PASS)" if c["caught"] else "**MISSED (FAIL)**"
                md_lines.append(f"| {c['category']} | {c['planted']} | {fg} | {st} |")
            md_text = "\n".join(md_lines)
        args.markdown.write_text(md_text + "\n", encoding="utf-8")

    if args.verify_v032_baseline:
        base = json.loads(args.baseline.read_text(encoding="utf-8"))["v032_raw_flat"]
        cur = extract_flat_metrics(report)
        det_keys = (
            "facts_macro_pct",
            "facts_agent_pct",
            "facts_mcp_pct",
            "squeez_r05_tok_pct",
            "squeez_r05_line_pct",
            "squeez_r033_tok_pct",
            "squeez_r033_line_pct",
            "neg_r05_all_anchors_pct",
            "neg_r05_anchor_recall_pct",
            "neg_r033_all_anchors_pct",
            "json_valid_pct",
            "json_scalar_recall_pct",
            "json_tok_ratio_pct",
            "golden_032_pass",
        )
        drifts = {k: (base[k], cur[k]) for k in det_keys if abs(base[k] - cur[k]) > 1e-6}
        if drifts:
            print(f"v0.3.2 baseline verification FAILED; drifts: {drifts}", file=sys.stderr)
            return 1
        print("\nv0.3.2 frozen baseline verification: PASS (0.0 drift across all deterministic metrics).")
        return 0

    if not report["passed"]:
        return 1
    if args.selftest and not report.get("selftest_passed", False):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
