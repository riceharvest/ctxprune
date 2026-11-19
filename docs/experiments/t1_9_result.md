# T1.9 Stateful Playwright a11y-snapshot delta encoding (#349): KILLED at the screen

**Verdict:** negative. Screen on real traces gives 10.8% token savings, under the 20% kill gate. No reader probe run (GPU not spent).

**Data:** MCPMark `*__playwright` runs (50 runs, 671 snapshot outputs, ~6.2M tokens at 3.5 chars/token), CPU only.
Script: `docs/experiments/t1_9_a11y_delta_screen.py`.

**Method:** replay each run in order; per run, one tab (MCPMark has no tab id). Same-URL snapshot: emit changed lines + `removed refs:` list, x1.25 + 10 tokens for position pointers, capped at the full size. URL change: full snapshot. Identical repeat: `[unchanged]`.

**Numbers:**
- Delta saves 10.8% of snapshot tokens vs full. Exact-duplicate snapshots are 0% of these outputs, so delta is also +10.8% beyond dedup.
- 425 of 671 snapshots are on a new URL and fall back to full. Only 196 snapshot pairs are same-URL.
- On those 196 pairs: median 89% of lines unchanged, mean 75%. Refs are renumbered (>50% of shared lines change ref) in 24 pairs, which also forces full.
- The estimate is an upper bound: the delta format costs and the model-side replay risk are not charged beyond the 1.25x.

**Why it fails:** agents mostly navigate (new URL), so the delta never applies to ~63% of snapshots. Current `@playwright/mcp` (0.0.83) also returns the snapshot as a file link on navigate and has a server-side `--snapshot-mode incremental`, so the remaining opportunity shrinks further.

**Not tested:** playwright_tc, fresh gen_playwright, long-context comprehension probe (moot after the screen). Revisit only if a workload shows long same-page click/type sequences with stable refs.

**Distribution plan:** none, not shipping. Had it passed: Claude Code PostToolUse hook (`ctxprune install claude-code`), `mcp__playwright__browser_*` outputs, gated to models that pass the probe.
