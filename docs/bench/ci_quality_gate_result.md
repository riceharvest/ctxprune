# Result: L5.6 — Fast Zero-GPU CI Quality Gate (`scripts/ci_quality_gate.py`)

- **Task:** `t_fbefc4c3` (`L5.6`, `V0.4_EXPERIMENTS.md`)
- **Date:** 2026-11-19
- **Branch:** `ab/t_fbefc4c3` (branched from `v0.4-lab` at `a692ec3785276718f28709d104b4c23792c42ec6`)
- **Hypothesis:** A single zero-GPU CPU command (`python scripts/ci_quality_gate.py`) executing all 6 quality and runtime categories — (1) `llmlingua_facts` fact survival (`n=120`), (2) `Squeez` reader-free gold-line & token recall (`n=150` at `r=0.5` and `r=0.33`), (3) negation/comparator/KV meaning-flip suite (`n=40` at `r=0.5` and `r=0.33`), (4) MCP JSON (`n=40`) & Python/Markdown code syntax validity (`n=30`), (5) `docs/ideas/gate/` fidelity + fuzz + selftest + coverage + oracle checks (`347` cases, `23,221` mutants), and (6) CPU latency, live ONNX parity, golden `0.3.2` byte-exact compatibility (`7/7`), and peak RSS memory cap checks — completes in `< 60.0 s` on CPU, reproduces the frozen `0.3.2` baseline (`0.0` deterministic drift), and detects `100%` (`6/6`) of planted regressions across all 6 categories.
- **Verdict vs Kill Gate:** **PASS.**
  - **Runtime gate (`< 60.0 s` on CPU):** **PASS** — **`32.25 s`** for the full 6-suite scorecard (`34.28 s` including `--selftest` across all 6 categories; `31.06 s` for `--engine v032 --verify-v032-baseline`).
  - **Intentional regression gate (catches regressions in every category 1–6):** **PASS** — **`6/6` (`100%`)** planted regressions caught by `--selftest` and `13/13` unit tests in `tests/test_ci_quality_gate.py` (`0.08 s`).
  - **Frozen baseline parity (`v0.3.2` and `v0.4-lab`):** **PASS** — `30/30` gates pass on `v0.4` (`0.0` drift vs `v0.4` reference); `--engine v032 --verify-v032-baseline` reproduces `v0.3.2` raw baseline with `0.0` drift on all deterministic metrics (`62.1%` facts macro, `73.5% / 42.2%` Squeez @ 0.5, `37.5%` negation @ 0.5, `0.0%` raw JSON valid, `7/7` golden `0.3.2` outputs byte-identical).

---

## 1. Per-Category / Per-Metric Scorecard vs Frozen Baselines (Dev-Set / Frozen Corpora)

*Note: All numbers below are evaluated on the frozen local corpora (`facts_items.jsonl` `n=120`, `squeez_sample.jsonl` `n=150`, `negation_suite.json` `n=40`, `mcp_json_items.jsonl` `n=40`, `docs/ideas/gate/` `347` cases, `tests/golden/golden_032.json` `7` cases). Per `METHODOLOGY.md` §4, numbers on corpora used during v0.4 development are labelled dev-set.*

| Category | Metric | `v0.3.2` Frozen (raw / prot) | `v0.4` Frozen Ref | Current (`v0.4`) | Δ vs `v0.3.2` Raw | Δ vs `v0.4` Ref | Gate | Status |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| **1. `llmlingua_facts`** (`n=120`, 605 facts) | `facts_macro_pct` | 62.1% (97.7% prot) | 96.2% | **96.2%** | **+34.1** | 0.0 | `>= 95.0%` | PASS |
| 1. `llmlingua_facts` | `facts_agent_pct` (`n=240` facts) | 45.8% (99.6% prot) | 99.2% | **99.2%** | **+53.4** | 0.0 | `>= 95.0%` | PASS |
| 1. `llmlingua_facts` | `facts_mcp_pct` (`n=245` facts) | 66.1% (95.1% prot) | 95.1% | **95.1%** | **+29.0** | 0.0 | `>= 94.0%` | PASS |
| 1. `llmlingua_facts` | `facts_v032_compat_pct` | 62.1% | 62.1% | **62.1%** | 0.0 | 0.0 | `== 62.1%` | PASS |
| **2. `Squeez` Recall** (`n=150`, `o200k`-matched) | `squeez_r05_tok_pct` | 73.5% (53.6% prot) | 87.6% | **87.6%** | **+14.1** | 0.0 | `>= 85.0%` | PASS |
| 2. `Squeez` Recall | `squeez_r05_line_pct` | 42.2% (13.4% prot) | 62.2% | **62.2%** | **+20.0** | 0.0 | `>= 58.0%` | PASS |
| 2. `Squeez` Recall | `squeez_r033_tok_pct` | 60.4% (42.5% prot) | 79.1% | **79.1%** | **+18.7** | 0.0 | `>= 76.0%` | PASS |
| 2. `Squeez` Recall | `squeez_r033_line_pct` | 26.6% (2.4% prot) | 46.7% | **46.7%** | **+20.1** | 0.0 | `>= 43.0%` | PASS |
| **3. Negation Suite** (`n=40`, `o200k`-matched) | `neg_r05_all_anchors_pct` | 37.5% (37.5% prot) | 82.5% | **82.5%** | **+45.0** | 0.0 | `>= 80.0%` | PASS |
| 3. Negation Suite | `neg_r05_anchor_recall_pct` | 68.0% (68.8% prot) | 92.0% | **92.0%** | **+24.0** | 0.0 | `>= 88.0%` | PASS |
| 3. Negation Suite | `neg_r033_all_anchors_pct` | 27.5% (27.5% prot) | 70.0% | **70.0%** | **+42.5** | 0.0 | `>= 65.0%` | PASS |
| **4. JSON/Code Validity** (`n=40` JSON + `30` code) | `json_valid_pct` | 0.0% | 100.0% | **100.0%** | **+100.0** | 0.0 | `== 100.0%` | PASS |
| 4. JSON/Code Validity | `json_scalar_recall_pct` | 50.0% | 95.8% | **95.8%** | **+45.8** | 0.0 | `>= 94.0%` | PASS |
| 4. JSON/Code Validity | `json_tok_ratio_pct` (vs `89.0%` compact) | 53.5% | 59.5% | **59.5%** | +6.0 (-29.5 vs compact) | 0.0 | `<= 65.0%` | PASS |
| 4. JSON/Code Validity | `code_ast_valid_pct` (`n=30`) | 100.0% | 100.0% | **100.0%** | 0.0 | 0.0 | `== 100.0%` | PASS |
| 4. JSON/Code Validity | `md_breadcrumb_pct` (`n=10`) | 0.0% | 100.0% | **100.0%** | **+100.0** | 0.0 | `== 100.0%` | PASS |
| **5. `docs/ideas/gate/`** (`347` cases, `23,221` mutants) | `gate_failed_cases` (`332` pass, `15` manual) | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| 5. `docs/ideas/gate/` | `gate_selftest_problems` | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| 5. `docs/ideas/gate/` | `gate_missing_coverage` | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| 5. `docs/ideas/gate/` | `gate_blind_oracles` | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| **6. Latency & Memory** | `eager_heavy_imports` (`import ctxprune`) | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| 6. Latency & Memory | `golden_032_pass` (`tests/golden/golden_032.json`) | 7/7 | 7/7 | **7/7** | 0.0 | 0.0 | `== 7/7` | PASS |
| 6. Latency & Memory | `onnx_parity_diffs` (live ONNX vs frozen cache) | 0 | 0 | **0** | 0.0 | 0.0 | `== 0` | PASS |
| 6. Latency & Memory | `onnx_128_med_ms` (live ONNX CPU inference) | 213.9 ms | 213.9 ms | **205.6 ms** | -8.3 | -8.3 | `<= 300.0 ms` | PASS |
| 6. Latency & Memory | `t17_atom_iou_pct` (`ScoredText` uint8 vs fp32) | 99.33% | 99.33% | **99.33%** | 0.0 | 0.0 | `>= 99.0%` | PASS |
| 6. Latency & Memory | `t17_noq_med_ms_per_10` (`prune_scored` 10 chunks) | 0.891 ms | 0.891 ms | **0.490 ms** | -0.401 | -0.401 | `< 1.000 ms` | PASS |
| 6. Latency & Memory | `t17_q_med_ms_per_10` (`prune_scored` with query) | 3.99 ms | 3.99 ms | **3.75 ms** | -0.239 | -0.239 | `<= 10.0 ms` | PASS |
| 6. Latency & Memory | `lossless_inproc_med_ms` (`safe` preset, `n=20`) | 0.120 ms | 0.120 ms | **0.107 ms** | -0.013 | -0.013 | `<= 5.0 ms` | PASS |
| 6. Latency & Memory | `peak_rss_mb` (`VmHWM`) | 908.3 MB | 908.3 MB | **786.4 MB** | -121.9 | -121.9 | `<= 1024.0 MB` | PASS |
| 6. Latency & Memory | `total_wall_s` | 47.64 s | 47.64 s | **32.25 s** | -15.4 | -15.4 | `< 60.0 s` | PASS |

---

## 2. Controls & Intentional Regression Self-Test (`--selftest`)

To prove the gate is non-vacuous and catches regressions in every category, `--selftest` plants 6 distinct defects and asserts each causes the corresponding category gates to fail (`6/6 CAUGHT` in `2.03 s` additional time):

| Category | Planted Defect | Failed Gates Triggered | Verdict |
|---|---|---|---|
| **1. `llmlingua_facts`** | Disable ID protection on technical text (`force_protected=False`, macro drops `96.2% -> 62.1%`) | `facts_macro_pct`, `facts_agent_pct`, `facts_mcp_pct` | **CAUGHT** |
| **2. `Squeez` Recall** | Disable `query=` lexical boost on Squeez (`r=0.5 tok_recall` drops `87.6% -> 64.6%`) | `squeez_r05_tok_pct`, `squeez_r05_line_pct`, `squeez_r033_tok_pct`, `squeez_r033_line_pct` | **CAUGHT** |
| **3. Negation Suite** | Disable `_pin_negation_anchors` (`r=0.5 all_anchors_pct` drops `82.5% -> 50.0%`) | `neg_r05_all_anchors_pct`, `neg_r05_anchor_recall_pct`, `neg_r033_all_anchors_pct` | **CAUGHT** |
| **4. JSON/Code Validity** | Bypass JSON-values-only routing (`json_valid_pct` drops `100% -> 0%`) + corrupt fenced Python AST (`code_ast_valid_pct` drops `100% -> 96.7%`) | `json_valid_pct`, `json_scalar_recall_pct`, `code_ast_valid_pct` | **CAUGHT** |
| **5. `docs/ideas/gate/`** | Plant a lossy transform claiming byte-exact roundtrip in `harness._run_case` | `gate_failed_cases` | **CAUGHT** |
| **6. Latency & Memory** | Simulate eager heavy import at startup (`eager_heavy_imports=2`) + `T1.7` latency spike (`2.500 ms > 1.000 ms`) + RSS cap breach (`2048 MB > 1024 MB`) | `eager_heavy_imports`, `t17_noq_med_ms_per_10`, `peak_rss_mb` | **CAUGHT** |

**Cache-invalidation control:** `_verified_cached_scores()` always runs the current `ctxprune.atoms.atomize(text)` on every item and compares the live atom sequence `(atom.text, atom.ws)` against cached atoms before reusing cached ONNX keep-probabilities, falling back to live `Compressor.atom_scores(text)` on any atomizer change. Suite 6 additionally runs live ONNX inference on `facts` items and verifies `onnx_parity_diffs == 0` (`onnx_max_score_diff == 0.0`) against `facts_scores.pkl`.

---

## 3. Commands to Reproduce, Files & SHA-256 Hashes

### Commands
```bash
# 1. Run full 6-category CPU quality gate + 6-category intentional regression self-test (~34s on CPU):
PATH=/home/dario/ctxprune-v2/venv-enc/bin:$PATH PYTHONPATH=src python scripts/ci_quality_gate.py --selftest

# 2. Verify frozen v0.3.2 compatibility engine reproduces v032_raw baseline with 0.0 deterministic drift (~31s):
PATH=/home/dario/ctxprune-v2/venv-enc/bin:$PATH PYTHONPATH=src python scripts/ci_quality_gate.py --engine v032 --verify-v032-baseline

# 3. Run fast pytest unit tests for the quality gate (~0.08s):
PATH=/home/dario/ctxprune-v2/venv-enc/bin:$PATH PYTHONPATH=src python -m pytest tests/test_ci_quality_gate.py -q
```

### Files & SHA-256
- Input corpora (frozen):
  - `/home/dario/ctxprune-v2/research/v04_code/facts_items.jsonl`: `d4e929275d342e26290332f59cecf58f3fecc5debde739ef43170fbb78ac5410`
  - `/home/dario/ctxprune-v2/research/v04_code/squeez_sample.jsonl`: `e28290cc13328882fcfe35cc108bd2be46bfdd2f8e48cf9f0f62d5d46e09be01`
  - `/home/dario/ctxprune-v2/research/v04_code/negation_suite.json`: `a1b6fc7379bb54a026abca27034cb947b6ec73fd7a0c155dd3ec445646f0081c`
  - `/home/dario/ctxprune-v2/research/v04_code/mcp_json_items.jsonl`: `7f8187b9e6ae83c4c4f0d87dd79b5daeb60f3e602bf4b644792ec898c014f703`
  - `tests/golden/golden_032.json`: `463218b5a7f6c96df7ef41838477d05f4f6448fd550dd3abbe48bd5d46e55873`
- Shipped files in branch `ab/t_fbefc4c3`:
  - `scripts/ci_quality_gate.py`
  - `docs/bench/ci_quality_gate_baseline.json`
  - `tests/test_ci_quality_gate.py`
  - `docs/bench/ci_quality_gate_result.md`

### Compute Used
- **CPU only:** ~6 minutes total across development and verification runs, **$0**, **0 GPU hours** (local CPU only).

### Claim Allowed
"On the frozen local evaluation corpora and `docs/ideas/gate/` suite (`347` cases), `scripts/ci_quality_gate.py` runs all 6 zero-GPU quality and runtime gates in `32.25 s` on CPU (`34.28 s` with `--selftest`), passes `30/30` gates on `v0.4-lab`, reproduces the frozen `v0.3.2` baseline with `0.0` drift on all deterministic metrics, and catches `6/6` planted regressions across all 6 categories. CPU-local dev-set regression gate; not a held-out reader benchmark claim."

---

## 4. Distribution Plan (`METHODOLOGY.md` §13)

- **Who uses it:**
  1. **ctxprune contributors & autonomous lab agents** screening Tiers 1–2 code-only experiments (`V0.4_EXPERIMENTS.md` / v0.5 branches) before committing or requesting GPU reader probes.
  2. **Maintainers & CI runners** validating pull requests and release branches (`pytest tests/test_ci_quality_gate.py` in fast CI; `python scripts/ci_quality_gate.py --selftest` in pre-merge / pre-release verification).
- **Existing seam / channel:**
  - Directly callable as `python scripts/ci_quality_gate.py` (matching existing `scripts/` layout alongside `docs/ideas/gate/run.py` and `docs/bench/llmlingua_facts.py`) and integrated into standard `pytest tests/` discovery via `tests/test_ci_quality_gate.py` (which runs in `0.08 s` with zero extra dependencies).
  - Uses the existing frozen baseline JSON (`docs/bench/ci_quality_gate_baseline.json`) and `tests/golden/golden_032.json` compatibility seam, requiring no new package dependencies or public API surface changes.
