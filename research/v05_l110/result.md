# L1.10 diagnostic delta + git-diff triage: R0/R1 screen

Date: 2026-11-19  
Branch: `ab/t_0483f732`  
Decision: **STOP / gate not demonstrated; do not promote to implementation.**

## Pre-registered question and gate

For consecutive `tsc`, `mypy`, and `ruff` runs, can diagnostics be keyed by `(file, severity, rule, message)` while ignoring line/column, emitted as `NEW`/`RESOLVED` plus an unchanged count, and ordered with changed files first, achieving at least 50% output-token savings on turns 2+ with zero missed new errors?

The stated gate is >=50% savings on turn 2+ and 0 missed new errors. Measure savings against raw diagnostics at the same turn; a miss is any newly appearing distinct diagnostic absent from the delta. Distribution seam if later supported: existing ctxprune agent hooks, rewriting checker tool outputs in the same channel, with full originals saved locally and referenced in the compact output.

## Cheapest screen and data audit

Inspected existing repository and lab data for diagnostic traces and 10-step edit/compile sequences. Candidate code/data areas included `research/v04_code/lab/dev_fix`, `data/everyday`, local benchmark outputs, and saved private tool-output/transcript JSONL. No reusable checker diagnostic sequence was found. `research/v04_code/lab/dev_fix/lcc_diag.txt` is a tail-retention probe on source code, not compiler diagnostics; CI task directories contain source/tests but no captured run-by-run diagnostics.

Therefore no empirical token savings or missed-error rate can be computed on the required corpus. No model/GPU or CPU-intensive work was justified; the audit was file search only. Result: the kill gate is **not passed**. This is a data-availability stop, not evidence that the approach cannot work.

## Correctness risks to resolve in a future screen

- The proposed key drops line and column, but may also collapse two simultaneous occurrences with the same file, severity, rule, and message. Preserve multiplicity (multiset counts) or report this as a known loss; otherwise a genuinely new second occurrence can be missed.
- Rule extraction and file normalization differ across tsc, mypy, ruff, cargo, and eslint. Unknown/unparseable diagnostics need passthrough to avoid missed errors.
- Checker identity and run continuity must be explicit: comparing different checker invocations/configurations could falsely label diagnostics `RESOLVED`.
- Git diff prioritization should only reorder output; it must not filter diagnostics. Untracked and renamed files need coverage.
- Local saved originals plus a retrievable reference are required for the output to be recoverable and auditable.

## Evidence needed to reopen

Capture 10 consecutive runs each for `tsc`, `mypy`, and `ruff` in an edit/compile loop, including unchanged line shifts, one newly introduced diagnostic, one resolved diagnostic, duplicate same-key diagnostics, malformed/unknown formats, and touched/untouched files. Freeze the raw files and their hashes, run a deterministic parser/delta, count tokens using the same estimator for raw and delta, and report per-turn savings and every miss. Promote only if turns 2+ meet >=50% savings and the delta has zero missed new errors; then test reader repair performance before making user-facing claims.
