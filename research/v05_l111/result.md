# L1.11 — Jupyter `.ipynb` → percent-format cells (`#322`) + notebook git-diff unescape (`#880`)

- **Date:** 2026-11-19
- **Task / Branch:** `t_2d397ec9` / `ab/t_2d397ec9`
- **Rung:** R1 (CPU screen on 31 real local `.ipynb` files and 20 real `.ipynb` git diffs; 0 GPU-h, $0)
- **Verdict:** **PASS (opt-in CLI + Python API; default `safe` and 0.2/0.3 `prune()` byte-identical)**

---

## 1. Hypothesis & Pre-Registered Kill Gate

- **Hypothesis:** A deterministic `.ipynb` percent-format renderer (`#322`) that emits `# %%` cells verbatim, coalesces and caps stream/plain-text outputs (`max_output_lines=12` head 8 + tail 4, `max_output_chars=2000`), and stubs binary/HTML/widget payloads (`# [<mime> <size> elided]`), paired with a `.ipynb` `git diff` unescaper (`#880`) that decodes JSON string element lines, stubs blobs, and folds `execution_count` / execution-timestamp hunks, reduces `o200k_base` tokens by **≥ 70%** on output-heavy notebooks while preserving **100%** of code cells byte-for-byte.
- **Kill gate (`V0.4_EXPERIMENTS.md` L1.11):**
  1. `>= 70%` `o200k_base` token reduction on output-heavy notebooks (both pooled and 95% bootstrap CI lower bound on macro-mean);
  2. `100%` code cell fidelity (`extract_code_cells(render_notebook(nb)) == extract_code_cells(nb)` byte-for-byte across all cells);
  3. `0` regressions on existing fidelity gates (`python docs/ideas/gate/run.py 322 880`) and packaging/unit suites.

---

## 2. Prior-Work & Existing-Trace Audit (R0)

1. **Fidelity gates on disk (`docs/ideas/gate/cases_rules_structured_json.py`):**
   - `#322` (`render_notebook`, `R` rule): passes 6/6 fixtures and 282/282 mutations (`-88%` chars, `-84%` proxy tokens).
   - `#880` (`decode_ipynb_diff`, `R` rule): passes 7/7 fixtures and 329/329 mutations (`-76%` chars, `-71%` proxy tokens).
   - Both are tagged `[R]` (loss-aware deterministic digests, not invertible `[L]` rules), so they must not silently run inside `lossless.compress()`'s `safe` byte-invertible stage.
2. **Trace frequency in existing coding-agent logs (`~/ctxprune-v2`):**
   - `swe_rebench.jsonl`: 2 real `.ipynb` file reads (`drvinceknight/nbchkr` `tests/nbs/test.ipynb`, `lightkurve/lightkurve`), 0 `.ipynb` `git diff` hunks.
   - `kwai.jsonl`, `swe_smith.jsonl`, `raw-claude-opus-5-5.jsonl`: 0 `.ipynb` JSON tool outputs (`nbformat` mentions are Python source files importing `nbformat`).
3. **Real notebook & diff corpus on local disk (`n=31` notebooks, `n=20` diffs):**
   - 31 distinct valid `.ipynb` files across local repositories (`Matcha-TTS/synthesis.ipynb`, `vscode-eslint/playgrounds/notebooks/notebook.ipynb`, `flax/core/flax_functional_engine.ipynb`, 3 executed + 5 unexecuted CUTLASS notebooks in `sycl-tla`, 8 executed `CuTeDSL` notebooks in `sycl-tla@fd6cfe1e`, 7 GitLab `ipynbdiff/spec/testdata` notebooks with PNG/SVG/HTML/error outputs, 3 `x-algorithm/clip` notebooks, `Notebooks intro.ipynb`, `V4Lab_30M_Reproduce.ipynb`).
   - 20 real `.ipynb` `git diff` outputs: 12 commits modifying `.ipynb` files in `sycl-tla`, 2 GitLab `ipynbdiff` test pairs (`from->to`, `hide_images->text_png_output`), 3 unexecuted-to-executed notebook diffs (`sycl-tla/examples/python/deprecated` vs `python/docs/externals`), and 3 captured notebook re-run diffs (`GIT_DIFF_A/B/C`).

---

## 3. Results (`o200k_base` tokens via `tiktoken`, 5,000-draw paired bootstrap)

### 3A. Summary Table

| Slice | Items (`n`) | Code cells | Exact code cells | `o200k` tokens before | `o200k` tokens after | Pooled token drop | Macro-mean drop (95% bootstrap CI) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Output-heavy notebooks** (`outputs >= source`) | 12 | 364 | **364 / 364 (100.0%)** | 462,751 | 15,464 | **96.66%** | **80.39% `[74.57%, 86.22%]`** |
| Output-heavy excl. `synthesis.ipynb` outlier | 11 | 352 | **352 / 352 (100.0%)** | 60,599 | 13,276 | **78.09%** | **78.66% `[72.81%, 84.34%]`** |
| **All output-bearing notebooks** | 20 | 446 | **446 / 446 (100.0%)** | 511,897 | 43,855 | **91.43%** | **64.18% `[55.52%, 72.90%]`** |
| **All real `.ipynb` notebooks** (incl. 11 unexecuted) | 31 | 531 | **531 / 531 (100.0%)** | 542,132 | 60,882 | **88.77%** | **57.82% `[51.09%, 64.69%]`** |
| **Real `.ipynb` `git diff` outputs** (all) | 20 | — | 100% code hunks kept | 58,377 | 41,028 | **29.72%** | **29.26% `[19.46%, 40.49%]`** |
| `.ipynb` `git diff` re-run / image hunks (`GIT_DIFF_A/B/C`) | 3 | — | 100% code hunks kept | 8,775 | 1,207 | **86.25%** | **86.32% `[83.41%, 89.22%]`** |

### 3B. Per-Notebook Breakdown (`n=31` real `.ipynb` files, `o200k_base`)

| Notebook | Code cells | Output-heavy | `o200k` before | `o200k` after | Drop (%) | Exact code round-trip |
|---|---:|:---:|---:|---:|---:|:---:|
| `Matcha-TTS/synthesis.ipynb` | 12 | Yes | 402,152 | 2,188 | 99.5% | 12/12 |
| `vscode-eslint/notebook.ipynb` | 307 | Yes | 34,119 | 6,141 | 82.0% | 307/307 |
| `ipynbdiff/error_output/input.ipynb` | 1 | Yes | 522 | 30 | 94.3% | 1/1 |
| `ipynbdiff/ignore_html_output/input.ipynb` | 1 | Yes | 527 | 57 | 89.2% | 1/1 |
| `ipynbdiff/from.ipynb` | 6 | Yes | 1,433 | 237 | 83.5% | 6/6 |
| `ipynbdiff/to.ipynb` | 6 | Yes | 1,451 | 247 | 83.0% | 6/6 |
| `sycl-tla/externals/01_epilogue.ipynb` | 7 | Yes | 8,247 | 1,799 | 78.2% | 7/7 |
| `ipynbdiff/svg/input.ipynb` | 1 | Yes | 492 | 120 | 75.6% | 1/1 |
| `ipynbdiff/hide_images/input.ipynb` | 1 | Yes | 254 | 62 | 75.6% | 1/1 |
| `sycl-tla:CuTeDSL/cuda_graphs.ipynb` | 5 | Yes | 4,728 | 1,185 | 74.9% | 5/5 |
| `ipynbdiff/text_png_output/input.ipynb` | 1 | Yes | 269 | 87 | 67.7% | 1/1 |
| `sycl-tla/externals/00_basic_gemm.ipynb` | 11 | Yes | 8,557 | 3,311 | 61.3% | 11/11 |
| `sycl-tla/externals/02_pytorch_extension_grouped_gemm.ipynb` | 7 | No | 4,120 | 1,957 | 52.5% | 7/7 |
| `clip/create_gcs_language_image_dataset.ipynb` | 10 | No | 1,716 | 829 | 51.7% | 10/10 |
| `sycl-tla/deprecated/01_epilogue.ipynb` | 9 | No | 2,144 | 1,039 | 51.5% | 9/9 |
| `V4Lab_30M_Reproduce.ipynb` | 3 | No | 581 | 290 | 50.1% | 3/3 |
| `clip/media_embeddings_dataflow.ipynb` | 17 | No | 6,311 | 3,155 | 50.0% | 17/17 |
| `sycl-tla:CuTeDSL/hello_world.ipynb` | 4 | No | 1,433 | 720 | 49.8% | 4/4 |
| `sycl-tla:CuTeDSL/cute_layout_algebra.ipynb` | 15 | No | 12,124 | 6,205 | 48.8% | 15/15 |
| `sycl-tla/deprecated/00_basic_gemm.ipynb` | 15 | No | 4,775 | 2,699 | 43.5% | 15/15 |
| `sycl-tla:CuTeDSL/print.ipynb` | 12 | No | 4,494 | 2,561 | 43.0% | 12/12 |
| `clip/create_bq_language_image_dataset.ipynb` | 6 | No | 2,236 | 1,281 | 42.7% | 6/6 |
| `sycl-tla/deprecated/02_pytorch_extension_grouped_gemm.ipynb` | 8 | No | 3,037 | 1,754 | 42.2% | 8/8 |
| `sycl-tla/deprecated/04_epilogue_visitor.ipynb` | 7 | No | 2,623 | 1,523 | 41.9% | 7/7 |
| `flax/core/flax_functional_engine.ipynb` | 9 | No | 3,536 | 2,071 | 41.4% | 9/9 |
| `sycl-tla:CuTeDSL/tensorssa.ipynb` | 10 | No | 6,546 | 3,921 | 40.1% | 10/10 |
| `sycl-tla:CuTeDSL/data_types.ipynb` | 4 | No | 2,750 | 1,702 | 38.1% | 4/4 |
| `Notebooks intro.ipynb` | 1 | No | 1,652 | 1,042 | 36.9% | 1/1 |
| `sycl-tla:CuTeDSL/tensor.ipynb` | 8 | No | 4,526 | 2,932 | 35.2% | 8/8 |
| `sycl-tla:CuTeDSL/elementwise_add.ipynb` | 13 | No | 9,617 | 6,322 | 34.3% | 13/13 |
| `sycl-tla/deprecated/03_basic_conv2d.ipynb` | 14 | No | 5,160 | 3,415 | 33.8% | 14/14 |

---

## 4. Distribution Plan (METHODOLOGY §12–13)

- **Who uses it:** Data scientists, ML engineers, and coding agents inspecting `.ipynb` files or reviewing noisy `.ipynb` git diffs where raw JSON arrays, execution timestamps, HTML tables, and base64 images blow out context windows.
- **Existing seams / channels:**
  1. **CLI subcommand:** `ctxprune notebook <path.ipynb>` renders a notebook to `# %%` percent format on stdout; `git diff | ctxprune notebook` decodes `.ipynb` diff hunks while passing all non-notebook file diffs through untouched.
  2. **Python SDK (lazy export in `ctxprune`):** `ctxprune.render_notebook()`, `ctxprune.unescape_notebook_diff()`, and `ctxprune.extract_code_cells()` (zero import overhead on `import ctxprune` via PEP 562 `__getattr__`).
  3. **Future hook/MCP seam (v0.5 integration):** Can be wired into `--preset digest` (`T1.12`) and `ctxprune mcp` `read_compact` on `.ipynb` paths once the v0.5 digest flag lands.

---

## 5. Reproduction Commands & Artifacts

- **Unit & gate tests:**
  - `PYTHONPATH=src python3 -m unittest tests/test_notebook.py tests/packaging/test_units.py`
  - `PYTHONPATH=src python3 docs/ideas/gate/run.py 322 880`
- **Files modified / added (with sha256):**
  - `src/ctxprune/notebook.py` (`484b9d0bd23c5be54bdb8601e00cb6dddb983c1401784689013c633b219aa7ab`)
  - `src/ctxprune/cli.py` (`8124405cd58d5d8e7de51a88edc428467095197134bd6d4a54f0ac5790cddf47`)
  - `src/ctxprune/__init__.py` (`708e7d2db047637e8f0b009bc6b6423b985ffded2596912cffaf9d39937ef95b`)
  - `tests/test_notebook.py` (`0d24905786bc2f9f569648119e08698ba551a52fc7a692402f316c32e4a17669`)
  - `research/v05_l111/result.md`
- **Compute used:** < 1 CPU minute, 0 GPU-h, $0.
