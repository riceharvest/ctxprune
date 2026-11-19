# L1.10b: diagnostic delta re-screen on real Claude Code transcripts

Date: 2026-11-19. Branch `ab/t_ae0dc4e9` (from `ab/t_0483f732`, prior result in `research/v05_l110/result.md`). Not merged into v0.4-lab or master.
Decision: **FAIL / negative (data-availability stop, pre-registered)**: held-out has n_pairs = 4 < 30, so the gate cannot be evaluated.

## What was run
- Corpus: `~/.claude/projects/**/*.jsonl` (426 files). A grep prefilter for tsc/mypy/ruff/`Found N errors` gave 170 files. Only `Bash` tool_use/tool_result pairs whose command mentions tsc/mypy/ruff/other checkers were parsed. ~/.codex/sessions and ~/.agentboard/runs were NOT scanned (not done).
- Pair definition (pre-registered): consecutive results of the same checker kind AND identical normalized command in one transcript, previous run having >=1 parsed diagnostic. Split by result timestamp: dev < 2026-11-01, held-out >= 2026-11-01.
- Encoder (`diag_delta.py`): diagnostics keyed by (file, severity, rule, message) as a multiset, line/column ignored. Output: NEW (full original lines), RESOLVED (keys), UNCHANGED count, plus non-diagnostic lines absent from the previous run. Decoder reconstructs the multiset from previous run + encoded text; "missed" = diagnostics in the current run not recovered. Line numbers of UNCHANGED diagnostics are dropped by design (recoverable from the previous run only up to shift).
- Tokens: o200k (tiktoken). Baseline lossless = `lossless.compress` through `ctxprune.glue` on the current output.
- Command: `PYTHONPATH=src ~/smallest-ai-lab/bin/lab-cpu -- python -I research/v05_l110b/diag_delta.py --split {dev|held} [--relax] --out data/pairs_<split>[relax].csv --files-list <list>`. Held-out was run once per config, no tuning on it. The encoder was not changed after seeing any numbers.
- Tests: `test_diag_delta.py` (3 hand-written synthetic cases, pass).

## Counts and numbers (median o200k reduction vs raw; bootstrap 95% CI over pairs)
| set | n pairs | sessions | tools | lossless baseline | delta |  missed |
|---|---|---|---|---|---|---|
| dev strict | 6 | 4 | tsc 6 | 0.0% | 88.6% [45.6, 97.2] | 0 |
| **held-out strict (gate set)** | **4** | 2 | ruff 3, mypy 1 | 0.0% | 94.4% [89.0, 97.6] | 0 |
| dev relaxed (kind only) | 37 | 20 | tsc 37 | 0.0% | -46.2% raw delta; 0.0% with raw fallback | 0 |
| held relaxed (kind only) | 62 | 34 | tsc 44, ruff 13, mypy 5 | 0.0% | -110.1% raw delta; 0.0% with raw fallback | 0 |

Relaxed sets are a sensitivity check only: they mostly pair outputs of different commands/clean runs (only 14 of 62 held pairs have a current diagnostic; there delta median -14.7% [-60.1, 89.0], with fallback 0.0% [0.0, 46.5]), so they are not valid repeat-pairs and do not count toward the gate. The strict n=4 CI is far too wide to claim anything (MDE at n=30 is ~10 points; here n=4). Round trip: 0 missed, 0 spurious diagnostics, unchanged counts correct on every pair in every set. The existing lossless path gave 0% on all pairs.

## Verdict against the kill gate
Gate (held-out median >= 50%, CI lower >= 40%, 0 missed) needs n_pairs >= 30; held-out strict n = 4, so per the protocol this stops as a negative result. The 4 strict held-out pairs happen to show 94.4% with 0 missed, and dev strict (n=6) 88.6%, but that is anecdotal. Real agent sessions rarely re-run the identical checker command with diagnostics in both runs (11 strict pairs total across 170 files). Do not make a user-facing claim.

## Reopen / next
- Scan ~/.codex/sessions and ~/.agentboard/runs (not done here) and new sessions as they accumulate; reopen when held-out strict n >= 30.
- The relaxed numbers show a real risk: when the diagnostic set changes completely, the raw delta is larger than the output, so a shipped encoder needs the raw fallback (included in `delta_fb_tok`).

## Privacy
Only per-pair aggregates are committed (`data/*.csv`: tool, hashed session id, turn index, token counts, counts). No transcript text, paths or project names; test fixtures are synthetic.
