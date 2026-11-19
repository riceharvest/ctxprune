# Windows path lists: coverage hole closed (card t_edbc761c)

**Verdict: closed for bare path lists; git-status and MSBuild-error shapes stay 0% (same as their Linux twins).**

Metric (pre-registered by the card): o200k token saving of `lossless.compress` on Windows fixtures, target >= 20%, Linux reference not lower.
Command: `cd /tmp && uv run --no-project --with tiktoken python <repo>/tests/win_paths/measure.py` (output: measure_output.txt).

| arm | before (paths_win off, the shipped state) | after (paths on, Windows follows paths) |
|---|--:|--:|
| Windows fixtures, 8 shapes, 12,493 tokens | 0.0% | **51.7%** |
| Linux fixtures, 4 shapes, 5,271 tokens | 43.9% | 43.9% (unchanged) |

Per shape after: dir /s /b (spaces in names) 73.3, Get-ChildItem FullName 74.1, UNC 73.7, mixed `\`/`/` 70.3;
0.0: git status, git status -s, MSBuild error list, Get-ChildItem table (no bare path per line; Linux compiler-error and
`git diff --name-only` shapes are also 0.0, so no parity gap; a new line-decoration rule is out of scope).

What changed: (1) `lossless.paths_win_on` follows `paths` (same opt-in/hosted rules) except models in `PATHS_WIN_DENY`
(qwen3.6: the 2026-10-18 probe, -11 pt reader accuracy); CTXPRUNE_PATHS_WIN=0|1 still wins. (2) `lossless2._pathlike(win)`
accepts single interior spaces in names on lines with a `\` (Program Files, Contoso Billing). `paths` itself stays opt-in as before.
Safety: every fixture round-trips byte-exact (CRLF), no line lost, odd-space/forged-marker/slash-prose cases stay verbatim.
Caveat: the 51.7% is on synthetic real-shaped fixtures (seeded generator), token savings not reader accuracy; no new reader probe was run.
