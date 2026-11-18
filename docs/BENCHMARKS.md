# Benchmarks

Every number in the README comes from this page. For each one I give the script, the code it ran against, and the
file the number is in.

**Runtime.** Unless noted, numbers are for the ctxprune runtime at research-repo `main` **12c7b79** (2026-10-18).
This packaging branch is rebased onto that commit, and its wheel vendors `scripts/` unchanged. The re-runs used a
clean `git archive 12c7b79` export, not a working tree.

**Traceability.**
- The study scripts and tables are committed in the research repo (mostly at 3a73482), and I cite those commits.
- Two files are still untracked there, so I give their sha256 prefix instead:
  - `results/lprobe-ship-live/table.md`;
  - `results/consolidated/matrix-everyday-dsh.md`.
- The numbers I re-ran for this release are committed here under [`bench/`](bench/).

Tokens are o200k (tiktoken) unless noted. "Qwen" means the Qwen3.6 tokenizer.

## 1. Savings on typical workloads

**Question.** What share of the tool-output tokens the model sees does the `safe` preset remove, for different kinds
of users?

**Data.** 2,225 public agent trajectories with 28,174 tool outputs: 8 Hugging Face datasets plus freshly generated
Playwright MCP and GitHub REST samples. They are fetched at pinned dataset commits, and only aggregates are kept.
The list, licenses and commits are in the research repo's `results/typical/DATASETS.md` @ 3a73482. Two of the sources contain real Claude Code sessions:
- trace-commons: 27 sessions;
- Terminal-Bench `claude-code` trials: 28.

**Method.** The study script is `results/typical/measure.py` @ 3a73482.
- Every trajectory is replayed in order. Each tool output goes through `ctxprune_core.process` with
  `Settings(model=False)`, the same call the hooks make.
- **Hook scope** is what the Claude Code PostToolUse hook reaches. Outputs that Claude Code spills to a file are left
  out of both the numerator and the denominator, because the model sees only a preview of them (see §2 for those).
  Non-Claude-Code traces are mapped onto Claude Code's tools: MCP tools become `mcp__*`, shell tools become Bash, and
  file views become Read.
- **Per-output checks:**
  - byte-exact round trip through `lossless.decode`;
  - or, for `json_compact` and `json_unescape`, identical parsed values.
- **CIs:** trajectory bootstrap, 200 resamples, o200k only.
- **User mixes** are weighted averages of workload groups. The weights are my assumptions (README table). In the mixes,
  "coding" is the real Claude Code sessions.

**Variants run for 0.3.0.** Two copies of `measure.py`, with the diffs committed:
- [`bench/measure_variant_powershell.diff`](bench/measure_variant_powershell.diff) adds `PowerShell` to the hook scope,
  because the 0.3.0 matcher is `Bash|PowerShell|Grep|mcp__.*`. The study script predates that matcher.
  - **Modelling assumption:** PowerShell outputs that Claude Code persisted (per the trace), or that are over 30k
    chars, count as spilled, like Bash. The shipped hook keeps PowerShell out of the spill path because its inline cap
    is unverified. In this data, 2 of 519 PowerShell outputs (1,923 tokens) were left out this way, both persisted by
    Claude Code in the trace; none was over 30k chars.
- [`bench/measure_variant_dedup.diff`](bench/measure_variant_dedup.diff) is the PowerShell variant with
  `Settings(model=False, dedup=True)`. That is what a `claude-opus-*` session gets by default, and what
  `CTXPRUNE_DEDUP=1` gives any model.
- In both copies, `SCRIPTS` points at the clean 12c7b79 export.

Commands. The venv is the study's own: tiktoken, tokenizers, pyarrow.

```sh
cd results/typical   # (the variants: a copy with SCRIPTS pointed at the repo's scripts/)
~/.cache/ctxprune-typical/venv/bin/python -I measure.py --workers 4 --out results.json --recs recs.jsonl
~/.cache/ctxprune-typical/venv/bin/python -I measure.py --summary recs.jsonl summary.json
python3 docs/bench/real_cc_split.py recs.jsonl      # Windows / non-Windows split of the real CC sessions
```

### Results: 0.3.0 `safe`, hook scope

**Sources:**
- dedup off: [`bench/typical_safe_summary.json`](bench/typical_safe_summary.json);
- dedup on: [`bench/typical_dedup_summary.json`](bench/typical_dedup_summary.json).

Both use the keys `hook_o200k_plain` and `hook_qwen_plain`, with the fields `mixes_real_cc_coding` and
`ci95_real_cc`.

| | dedup off (every model except `claude-opus-*`) | 95% CI | Qwen | dedup on (Opus) | 95% CI | Qwen |
|---|--:|--:|--:|--:|--:|--:|
| Mix 1, coder with few MCP servers | 2.64% | 1.47–4.08 | 2.38% | 4.58% | 3.02–6.13 | 4.11% |
| Mix 2, coder plus MCP | 3.69% | 2.95–4.59 | 3.36% | 7.55% | 6.56–8.58 | 7.14% |
| Mix 3, MCP-heavy assistant | 8.73% | 8.16–9.28 | 9.05% | 12.04% | 11.42–12.60 | 12.43% |
| Real Claude Code sessions (55), % of all tool tokens | 2.86% | 1.37–4.67 | 2.39% | 5.00% | 3.21–6.98 | 4.28% |
| Real Claude Code sessions, % of the tokens the hook reaches | 8.87% | 3.59–15.37 | 7.95% | 15.48% | 9.64–22.41 | 14.23% |

- **What the dedup column is.** The same sessions with cross-turn dedup on. The traces don't record the model; the
  column is what an Opus user would save on this content.
- **Comprehension.** Reading the references is a separate question, and Opus passed it (§4).

**Where the Claude Code number comes from.** I split the 55 sessions by whether they have a PowerShell call (session
bootstrap, 2,000 resamples). Source: [`bench/real_cc_split.txt`](bench/real_cc_split.txt).

| | sessions | tool tokens | dedup off | 95% CI | dedup on (Opus) | 95% CI |
|---|--:|--:|--:|--:|--:|--:|
| no PowerShell call (28 Terminal-Bench trials, 9 trace-commons) | 37 | 0.41M | 0.29% | 0.04–0.62 | 1.76% | 0.66–3.38 |
| PowerShell calls (Windows; all trace-commons) | 18 | 0.87M | 4.08% | 1.98–6.88 | 6.53% | 4.23–9.86 |

- The Windows sessions hold 68% of the pooled coding tokens, so the mixes above mostly reflect Windows coding.
- Platform is inferred from PowerShell use; the traces don't record the OS.
- Before the long-context fix round (grep grouping, `paths`, `numbered` and flat `jsonrows` opt-in) the non-PowerShell
  sessions saved 0.98% [0.33, 1.60] / 2.43% [1.08, 4.21] (dedup off / on), the pooled 3.36%.
- In the earlier run at 106bcea, the pooled number was 3.35%. Of that, PowerShell outputs gave 2.86 points and ANSI
  colour removal gave 2.32 points by rule. At 12c7b79 the pooled number is 3.36%.
- Read is 58% of these sessions' tool tokens, and ctxprune does not touch it.

**Per workload group** (dataset-macro mean, % of group tokens, o200k):

| group | dedup off | dedup on |
|---|--:|--:|
| coding (all sources, incl. benchmark harnesses) | 3.10 | 9.72 |
| mcp_multi (MCP-Atlas, Toucan) | 14.65 | 15.35 |
| mcp_browser (Playwright) | 9.51 | 21.67 |
| mcp_filesystem | 7.37 | 8.41 |
| mcp_saas (Notion) | 6.23 | 7.58 |
| assistant_api (AppWorld, τ², Hermes) | 4.98 | 7.21 |
| web_research (BrowseComp) | 3.39 | 3.48 |
| mcp_github | 0.12 | 2.28 |
| mcp_db (Postgres) | 0.00 | 0.52 |

Per-dataset, per-rule and per-format tables:
- [`bench/typical_safe_results.json`](bench/typical_safe_results.json);
- [`bench/typical_dedup_results.json`](bench/typical_dedup_results.json).

**Checks** (hook scope, outputs the pipeline changed):

| check | dedup off | dedup on | failures |
|---|--:|--:|--:|
| byte-exact (`lossless.decode`; with dedup on, also `dedup.expand` on every deduped output) | 1,205 | 2,960 | 0 |
| identical parsed JSON values (`json_compact`, `json_unescape`) | 5,463 | 5,463 | 0 |
| a rule combination the script does not verify end to end (mostly `json_compact` + `jsonrows`) | 1,254 | 1,254 | – |
| ANSI removed (render-preserving by design) | 194 | 194 | – |

Growth: 0.02% (o200k) and 0.13% (Qwen) of reached outputs came out longer in tokens than they went in. The field is
`hook_grow_pct_*` in `typical_safe_results.json`.

### History of the headline

| runtime | M1 | M2 | M3 | note |
|---|--:|--:|--:|---|
| pre-release, dedup on for everyone, no PowerShell reach | 1.91 | 4.34 | 7.78 | `results/typical/README.md` @ 3a73482 |
| 106bcea, dedup off for everyone, + PowerShell | 2.95 | 3.85 | 9.42 | the previous draft of these docs |
| 12c7b79, dedup by model (off / Opus) | 3.07 / 5.00 | 3.97 / 7.84 | 9.68 / 13.06 | the 0.3.0 draft before the long-context probe |
| **0.3.0, long-context fix round** (off / Opus) | **2.64 / 4.58** | **3.69 / 7.55** | **8.73 / 12.04** | this page: grep grouping, `paths`, `numbered`, flat `jsonrows` opt-in |

## 2. Spill files and Read trim (Claude Code reach levers)

**Spill files** (`spill.py`, on by default). When Claude Code saves a big output to a file, ctxprune writes that file
losslessly.
- Source: [`bench/spill_gain_12c7b79.json`](bench/spill_gain_12c7b79.json), policy `Ap`. Re-run for this release
  with `scripts/spill_gain.py` against the clean 12c7b79 export:
  `~/.cache/ctxprune-typical/venv/bin/python -I scripts/spill_gain.py --out …`.
- A run at 106bcea gave the same numbers.
- The committed `results/reach-live/spill_gain.json` (@ d141f03) predates the over-cap-JSON-as-rows change (5bab7ac)
  and the jsonrows/a11y defaults (abbec19). It gave +0.17 / +0.25 / +0.24 at r = 0.25.
- Added on top of §1, in points of all tool tokens. r = the fraction of the spilled file the agent later reads, which
  is assumed, not measured.

| r | M1 | M2 | M3 |
|--:|--:|--:|--:|
| 0.25 | +0.05 [0.04, 0.06] | +0.58 [0.37, 0.72] | +0.67 [0.46, 0.86] |
| 1.0 | +0.06 [0.04, 0.06] | +0.96 [0.69, 1.16] | +1.29 [0.88, 1.65] |

- The data has 290 spilled outputs.
- **The gate.** Stage 0 is used in a file only when `lossless.decode` gives back the original bytes. For JSON, it
  also counts as a match if re-indenting gives back the original bytes.
  - 163 outputs passed.
  - 23 were lossy-by-design, 14 hit the line-length cap and 4 failed (not shorter, or no exact decode). All of these
    keep the original file.
  - 86 had nothing to change.
- Real Claude Code traces have almost no spills: 3 in trace-commons, none from a hooked tool. All the value above is
  extrapolated from MCP benchmark traces.
- **Live check:** a Claude Code 2.1.302 run against a scripted stub model. All 15 spill checks passed (preview is a
  byte prefix, the file round-trips, the counts are updated). Source: `results/reach-live/evidence.json` @ 5bab7ac.

**Read trim** (opt-in, `CTXPRUNE_READ_TRIM=1`).
- Source: `results/reach-live/read_trim_replay.json` @ d141f03 (script `scripts/read_trim_replay.py`), replayed through
  the real hook path on 27 trace-commons sessions (1.24M tool tokens, 841 Reads).
- **Saved 1.14% [0.33, 2.34]** of all tool tokens.
- **Edit risk:** for 18 of 972 Edits (1.85%), the trim would have hidden the most recent literal copy of the Edit's
  `old_string`. For 1 of them no copy would be left in context. The baseline Edit failure rate in these sessions is
  15/972.
- The A/B on Edit failures has not been run.

## 3. Latency

**When these were measured.** These are the quiet-machine numbers at 106bcea.
- The re-run at 12c7b79 ran while other jobs held the machine at a load average of ~9. It came out slower across the
  board, including the SessionStart note hook, whose code didn't change: 20.7 → 31.0 ms p50. So it's not a clean
  measurement, and I don't quote it.
- 12c7b79 adds a once-per-session dedup decision and the first-render store to the hook path. Their cost hasn't been
  measured cleanly yet.

**Shipped Claude Code hook entry** (106bcea wheel).
- The command `ctxprune install` writes is `python …/ctxprune/plugin_cli.py hook claude-code`.
- It was replayed on 6 real hook events from the live canary, 50 times each, with a fresh process per call.
- **p50 39.1 ms, p95 47.1 ms, max 82.0 ms** (n = 300).
- Script: [`bench/bench_shipped.py`](bench/bench_shipped.py). Result:
  [`bench/latency_shipped_106bcea.json`](bench/latency_shipped_106bcea.json).

**Runtime scripts** (`results/canary/bench_hooks.py`, 50 repeats). Result:
[`bench/latency_scripts_106bcea.json`](bench/latency_scripts_106bcea.json).

| path | calls | p50 ms | p95 ms |
|---|--:|--:|--:|
| Claude Code `lossless_hook.py` (canary events) | 300 | 44.5 | 53.1 |
| `ctxprune_compress.py` as a process (the Pi / OpenCode / dsh path) | 200 | 59.8 | 68.7 |
| in-process `run()` (the Hermes plugin path) | 200 | 2.6 | 8.2 |

**Proxy.** In the live canary at 680b560, the proxy added 1.6–3.6 ms p50 and 4.6–8.3 ms p95 per request
(`results/canary/README.md` @ 1979e27). It was not re-run.

- Machine: Linux desktop, CPython 3.14.6.
- Not measured on macOS or Windows.

## 4. Comprehension probes

**Question.** Does a model still answer correctly from the rewritten text?

**Method.**
- Each rule gets 100 to 150 items. An item is a real tool output from a public repo or public MCP data, with a question
  that has one exact answer (a line number, a path, a count, a value).
- Every item is asked on the raw output and on the encoded output.
- Δ = encoded − raw accuracy, with a paired bootstrap 95% CI.

**Readers:**
- **Claude Opus 5.5:** `claude -p` on a subscription (no API billing), isolated: no tools, CLAUDE.md, memory, skills,
  hooks or plugins. Adaptive thinking at the CLI default effort; with and without the marker note; n = 100 per rule.
- **On vLLM:** Qwen3.8-27B INT4, Qwen3.6-35B-A3B NVFP4 and Gemma 4 31B (QAT W4A16), with thinking on, off, and
  "+note" (a one-line system note explaining the markers); n = 150 per rule, 100 in the ship-bytes run.

**Sources.**
- Scripts: `scripts/lossless_probe.py` and `scripts/lprobe_dedup.py`.
- Tables in the research repo:
  - `results/lprobe-claude/table.md` @ acb0033 (Opus, default rules);
  - `results/lprobe-claude/dedupfmt/table.md` @ c3a2d13 (Opus, dedup formats);
  - `results/lprobe-live`, `lprobe2-live`, `lprobe3-live`, `lprobe-gemma-live` and `lprobe-dedup-live` @ 3a73482;
  - `results/lprobe-ship-live/table.md` (untracked, sha256 `fc4232e82423`): the open readers on the exact bytes the
    runtime ships, including `json_ascii` and Windows paths.

**Rules on by default** (Δ accuracy in points; a range covers the modes tested):

| rule | Claude Opus 5.5 | Qwen3.8 | Qwen3.6 | Gemma 4 | notes |
|---|---|---|---|---|---|
| hits | 0.0 | 0.0 | −2.0 to +2.0 | 0.0 | |
| paths (`/` lists) | 0.0 | 0.0 | 0.0 | 0.0 | costs open thinking readers extra completion tokens, E = +38 (Qwen3.8) and +149 (Qwen3.6) per decode; off for local endpoints (§5) |
| numbered (probed as `catn`) | 0.0 to +1.0 | +0.7 to +2.0 | −1.0 to +2.0 | 0.0 | |
| jsonrows (flat) | 0.0 | −1.0 to 0.0 | −2.0 to +0.7 | 0.0 | |
| jsonrows (MCP results) | −2.0 [−5.0, 0.0]; −3.0 [−7.0, 0.0] with note | 0.0 to +1.0 | 0.0 to +1.0 on the shipped bytes | +1.3 | the earlier run, generated with the extended form, had Qwen3.6 with thinking at −5.3 [−9.3, −1.3], with the misses on `key.sub` columns; that form is opt-in now |
| a11y (Playwright) | 0.0 to +1.0 | 0.0 to +1.0 | 0.0 to +2.0 | −0.7 to +0.7 | |
| **json_ascii** | not probed | **+13 to +20** | **+11 to +14** | not probed | raw `\uXXXX` JSON was read correctly only 54–60% of the time; e.g. Qwen3.8 with thinking went from 60 to 76% |

- **Opus jsonrows (MCP):** both Opus CIs touch 0, so this counts as a pass, but it is the weakest default-rule result
  on Claude. Graded on the answer's last line instead (a right value after shown working counts), no Opus cell lost anything, and jsonrows (MCP) was 100 → 100 in both modes.
- **Windows `\` path lists** are not grouped (they stay raw). On the shipped bytes, Qwen3.6 without thinking lost
  11.0 [−18.0, −6.0] points on them; the other modes lost 0 to 2 points.
- **`table`, `diffhdr`, `sffiles`** were probed on the shipped bytes on the open readers only (lprobe-final030,
  n = 100 per rule and mode): 0.0 to +2.0 points, `sffiles` −1.0 [−3.0, 0.0] in two modes. Claude was not probed on them.
- **Not probed with any reader:** the `[× N]` repeat counts, `json_compact`, `json_unescape`, ANSI/CR removal.
- **Other Claude models** (Sonnet, Haiku) are not probed.

**Cross-turn dedup** (multi-turn items, the shipped format "v1"):

| reader | whole output | line runs | references to tool inputs | all |
|---|---|---|---|---|
| Claude Opus 5.5 (n = 150 each) | +1.3 [0.0, +3.3] | −3.3 [−7.3, 0.0] | +1.3 [−1.3, +4.0] | **−0.2 [−1.8, +1.3]** (n = 450) |
| Qwen3.8, thinking | −7.3 [−12.0, −3.3] | −18.0 [−25.3, −11.3] | −21.3 [−30.0, −12.7] | |
| Qwen3.8, +note | −6.0 | −18.0 | −27.3 | |

- With Opus the deduped prompts were 22% smaller (3,229 → 2,521 tokens on average), and completions grew from 96 to
  198 tokens.
- This is why dedup is on for `claude-opus-*` only (`ctxprune_core.DEDUP_MODELS`) and off for everything else.

**Opt-in or never shipped** (open readers; not run on Claude):

| rule | worst Δ (points) | status |
|---|---|---|
| blockref | −26.7 (Qwen3.8) to −85.3 (Gemma); count questions −72.7 to −100 | opt-in `CTXPRUNE_BLOCKREF=1` |
| prefix | −6.7 [−11.3, −2.7] (Qwen3.6 no-think); −2.7 (Qwen3.8 no-think); thinking 91 → 262 tokens | opt-in `CTXPRUNE_PREFIX=1` |
| dupref | −86.7 to −100 | encoder removed; old markers still decode |
| alias (`§A` directory stems) | −0.7 to −38.0 | probe-only, never shipped |
| gutter (Claude Code Read `N\t` numbers dropped) | −17.3 to −94.0 | probe-only, never shipped |

### Long context (the 0.3.0 fix round)

**Question.** The probes above put one output in a 1–6k-token prompt. Real sessions run 50k+ tokens. Does a rule
that passes short still pass deep in a long transcript?

**Method** (`scripts/lprobe_long.py`, `scripts/lossless_probe.py`; research repo `results/rented/lprobe-long2`).
- 24 base items per rule (the ship-bytes items), each placed at 10/50/90% depth of a 16k/32k/56k-token agent
  transcript of real shell outputs from other OSS repos (9 cells per item).
- Raw arm: every output raw. Encoded arm: every output as 0.3.0 emits it (`lossless.compress` with the real command),
  filler included. Hits items also got a second encoded arm, the ripgrep `--heading` layout.
- The tool schemas are sent, as in a real agent. A reply that re-runs a tool instead of answering is a **re-fetch**:
  one more turn in a live agent, counted on its own, not as wrong. Max 8,192 completion tokens.
- Reader: Qwen3.6-35B-A3B (NVFP4), with and without thinking. Δ = encoded − raw in points, 95% bootstrap CI
  clustered by base item; re-fetch pairs with a two-sided sign test.
- Decision rules were written down before the run (`runbook/job_lprobe_long2.sh`): any rule off if its "right" CI is
  wholly below 0 or its "wrong" CI wholly above 0 in either mode; grep grouping kept only at Δ right ≥ −2 with a
  re-fetch CI that includes 0 in both modes. Grep grouping and flat `jsonrows` (think: right −11.6 [−18.1, −5.6]) went
  off by those rules. `numbered` met neither of its pre-registered bars; it was turned off afterwards on its
  re-fetch result (13 vs 2 pairs without thinking), since a re-fetch is a whole extra turn and it saves little.

**Without thinking** (n = 216 pairs per rule; `paths` from the earlier run, lossless2 bytes):

| rule | Δ right | Δ wrong | re-fetch raw → enc | Δ re-fetch | decision |
|---|---|---|---|---|---|
| json_ascii | **+27.3** [+11.1, +44.0] | −26.4 [−43.5, −10.6] | 0 → 0 | 0 | on |
| jsonrows, nested MCP records | +1.4 [−1.9, +5.6] | −1.4 | 0 → 0 | 0 | on (hosted models) |
| a11y (Playwright) | +4.6 [−1.4, +13.4] | −5.1 | 0.9 → 1.4% | +0.5 | on |
| grep grouping | −3.7 [−9.7, +3.2] | +4.6 [−2.3, +11.1] | 3.2 → 2.3% | −0.9 | **opt-in**: below the −2 bar; hits filed under the wrong file (17 vs 10 pairs). The rg layout: also −3.7 |
| jsonrows, flat records | −5.1 [−12.0, +1.4] | −2.3 | 7.9 → 15.3% | **+7.4 [+3.7, +12.0]** (19 vs 3, p = 0.001) | **opt-in** |
| numbered (`catn`) | −4.2 [−9.7, +0.5] | −0.9 | 3.2 → 8.3% | **+5.1 [+0.9, +10.2]** (13 vs 2, p = 0.007) | **opt-in** |
| paths | −8.3 [−20.9, −0.5] (−11 at 56k) | | | | **opt-in** |

**With thinking** (n = 216 pairs per rule; 2.8% of replies hit the 8,192-token cap, against 7.8% at 4,096 tokens and
no tools in the first run; 34% of replies were re-fetches, so "right" rates are low on the re-runnable kinds):

| rule | Δ right | Δ wrong | re-fetch raw → enc | Δ re-fetch | Δ cut off |
|---|---|---|---|---|---|
| json_ascii | **+26.4** [+13.9, +39.8] | −13.9 [−25.5, −2.8] | 0 → 0 | 0 | −12.5 [−21.8, −4.6] |
| jsonrows, nested MCP records | +0.9 [0.0, +2.8] | 0.0 | 0 → 0 | 0 | −0.9 |
| a11y (Playwright) | −1.9 [−6.9, +3.2] | +3.2 [−1.9, +9.7] | 0.5 → 0.5% | 0 | −1.4 |
| grep grouping | **+8.3** [+3.7, +13.0] | +0.5 | 86.1 → 77.8% | **−8.3** [−13.0, −4.2] (9 vs 27) | −0.5 |
| grep grouping, rg layout | +4.2 [−0.9, +8.8] | +0.5 | 86.1 → 81.0% | −5.1 [−10.6, +0.5] | +0.5 |
| jsonrows, flat records | **−11.6** [−18.1, −5.6] | 0.0 | 36.1 → 48.1% | **+12.0** [+5.6, +18.1] (42 vs 16, p = 0.001) | −0.5 |
| numbered (`catn`) | −3.2 [−9.7, +2.8] | −1.4 | 55.1 → 57.9% | +2.8 [−3.2, +8.3] | +1.9 |

- **Grep grouping is the close call.** With thinking it helped (fewer re-greps, more right answers); without thinking
  it cost 3.7 points through wrong-file answers. The bar set before the run needed both modes, so it is opt-in.
  Claude was not run at long context; a Claude long-context probe could move it back to the default for Claude.
- **Flat-record `jsonrows`** failed in both modes, and `numbered` without thinking: re-fetches, i.e. extra turns.
- **`json_ascii`** is the largest gain in the whole probe set and holds at every length and depth.

## 5. Net-cost model: do rules still pay once decoding is counted?

**Question.** Some encodings make a thinking model spend extra completion tokens to decode them (for example, joining a
directory and a file name back together). Do those extra tokens eat the prompt savings?

**Method.**
- Script: `results/netcost/netcost.py` @ 3a73482. Table: `results/netcost/table.md` @ 3a73482.
- From the probe answers: S = prompt tokens saved per firing, and E = extra completion tokens per forced decode.
- From real transcripts: d = how often the next agent turn actually uses content the rule moved. Only counts are kept.
- **(a) Hosted at Opus 5.5 prices:** a rule pays if d < d* = S·22/(20E).
- **(b) Self-hosted latency with a prefix cache:** a rule pays if d < d* = S/(20E).

| rule | E (Qwen3.8 thinking) | (a) hosted | (b) self-hosted |
|---|--:|---|---|
| hits | −10 [−24, +2] | pays | pays |
| jsonrows | −2 [−7, +4] | pays | pays |
| catn / numbered | 0 [−13, +14] | pays | pays |
| paths | +38 [+30, +47] (Qwen3.6 +149) | pays | **loses**: d* 26% (Qwen3.6 7%) vs a real d of 44% [33, 56] |
| prefix | +171 [+143, +203] | thinking readers only | thinking readers only |

This was the first reason `paths` turned off for local targets. Since the long-context probe (§ long context) it is
off for every model unless allow-listed (`lossless.paths_on`, `CTXPRUNE_PATHS_MODELS`).

Caveats:
- Qwen tokens are priced at Opus rates. Claude's own thinking overhead was not measured.
- "Uses the content" is detected with a regex.

## 6. Presets: the lossy `balanced` preset

**Selector replay.** Source: `results/consolidated/balanced.md` @ 0b18309 (the final verdict).
- `balanced` uses the "anchored" selector.
- Against no compression, on outputs ≤ 30k chars with file views whole (Qwen3.6, 1,615 items):
  - excess refetch +4.0 [+2.8, +5.3] points;
  - excess divergence +2.7 [+0.8, +4.7] points;
  - net +554 [+505, +604] tokens per item.
- Qwen3.8 (1,582 items): excess refetch +6.9 [+5.4, +8.5], divergence +6.6 [+4.6, +8.6], net +497 [+449, +550]
  tokens per item.

**Agent-session cost with `balanced` vs off.** Ratio of total cost, 90% CI. Source: `results/consolidated/README.md`
@ b163f24 and the `matrix-*.md` files; the dsh everyday matrix is untracked.

| harness | everyday | general |
|---|---|---|
| Codex | +36.8% [+15.8, +60.6] | – |
| dsh | +0.3% (+14% prompt tokens, +1.9 turns) | −10.4% [−22.1, +2.6] |
| Pi | −9.8% [−29.5, +11.8] | +1.5% [−17.4, +23.6] |
| OpenCode | −16.1% [−26.9, −1.7] | −3.4% [−15.9, +12.8] |
| Claude Code | +5.5% | +4.8% (anchored) |
| Hermes | −18.2% (n = 13, stopped) | +9.2% [−1.5, +19.9] |

- This is why `safe` is the default, and why Codex, dsh, OpenCode and Pi run `balanced` as `safe` unless forced.
- These waves used local Qwen models, not Claude.
- The 0.3.0 model weights are not published, so none of this is reproducible by users yet.

## 7. Live harness canary

Each harness was run end to end in a throwaway HOME against a scripted stub model that logs every request. Source:
`results/canary/README.md` @ 1979e27 (run at 680b560).

**Versions:** Claude Code 2.1.302, Hermes 0.21.6, Pi 1.1.0, OpenCode 1.2.10. **Not installed, so not run:** Codex, dsh.

**Checked, and passed after the fixes listed in SAFETY.md:**
- the compressed text reaches the model;
- it decodes back to the original;
- file views arrive byte-identical;
- forged markers arrive escaped;
- `CTXPRUNE_OFF=1` disables the native integrations;
- crash, hang and dead-model-port all fail open.

**What this does and doesn't show.** It checks the plumbing and the wire bytes, not model behaviour.

**Packaging re-checks for this release** (wheel built from this branch at runtime 12c7b79):
- **Every vendored module imports** from the installed wheel (19 modules), and both self-contained bundles carry the
  stdlib set, including `render_store.py`.
- **`tests/packaging/e2e.py`: 80 checks, 0 failed.** It installs, re-installs, runs doctor and uninstalls every
  integration in a throwaway HOME. It also checks that request-time dedup stays off with no model, runs with
  `CTXPRUNE_DEDUP=1`, and that soft OFF keeps pinned bytes.
- **`tests/packaging/cc_live.py`**, with Claude Code 2.1.302 against a local mock API:
  - the `ctxprune install claude-code` route and the `ctxprune-safe` plugin both delivered the compressed grep output
    (5,345 → 3,270 chars);
  - `CTXPRUNE_OFF=1` delivered the raw output.
- **`uvx --from <wheel> ctxprune hook claude-code`** (the 0.2.1 plugin's fallback path) returned a rewritten output.
- **CI on `v2-ci`** (GitHub Actions run 39076169557, runtime a4bd87e): the same e2e test passed on Windows, macOS and
  Ubuntu × Python 3.9–3.13, and the determinism job found all 15 cells byte-identical.

**Not re-run:**
- The live harness canary (Hermes, Pi, OpenCode with a stub model) predates dedup-by-model, the render store, the
  Hermes time budget and the spill and PowerShell changes. Unit tests cover those in the research repo.
- The proxy kill switch is covered by `scripts/test_ctxprune_proxy.py::test_kill_switch_env_and_file`.

## 8. Compared with other compactors

**Question.** If a tool shrinks a tool output, does the agent still do the same next thing?

**Method.** Next-action replay (research repo `results/compare/harm.md` @ 22d5074, 2026-10-18).
- 400 frozen replay items: real held-out agent trajectories, cut right after a big tool output. Each tool rewrites
  that output at its documented default. The reader then picks the next action.
- Readers: Qwen3.6-35B-A3B and Qwen3.8-27B. No Claude reader.
- Metric: excess next-action divergence and re-fetch rate, measured only on the items each tool changed. Each arm is
  paired against a resample of the raw prompt (the noise floor). 95% bootstrap over items.
- Versions: headroom-ai 0.41.0, rtk 1.0.1, sqz 2.1.1, optscale-kompressor 1.0.2, llmlingua 0.2.2 (LLMLingua-2,
  applied at ctxprune's hook reach).

| tool | items changed | Qwen3.6 divergence | Qwen3.6 re-fetch | Qwen3.8 divergence | Qwen3.8 re-fetch |
|---|--:|---|--:|---|--:|
| ctxprune `safe` | 7 | n/a (too few) | 0 | n/a | 0 |
| headroom | 133 | +12.8 [+3.8, +21.8] | +3.8 | +21.8 [+13.5, +30.1] | +3.8 |
| ctxprune `balanced` (lossy, experimental) | 198 | +14.6 [+7.6, +22.7] | +7.6 | +32.8 [+25.8, +39.9] | +17.2 |
| kompressor `lossless` mode | 48 | +14.6 [−4.2, +31.2] | +14.6 | +37.5 [+22.9, +52.1] | +12.5 |
| rtk | 109 | +23.9 [+13.8, +33.0] | +5.5 | +35.8 [+26.6, +45.9] | +13.8 |
| sqz | 45 | +28.9 [+11.1, +46.7] | +15.6 | +33.3 [+15.6, +51.1] | +15.6 |
| LLMLingua-2 | 192 | +29.2 [+21.4, +37.5] | +18.8 | +42.7 [+34.9, +50.0] | +27.1 |
| kompressor | 351 | +33.6 [+27.6, +39.9] | +23.6 | +44.7 [+39.0, +50.7] | +32.2 |

How to read it:
- Every lossy tool changed the agent's next action materially, more so on Qwen3.8. headroom was the mildest, and
  milder than ctxprune's own `balanced` on Qwen3.8.
- `safe` changed only 7 of these 400 items (most were file views and code, which it leaves alone), so this shows that
  it does no harm where it fires, not that it saves much here.
- The same study measured savings at each tool's own reach on 30.6k public tool outputs (research repo
  `results/compare/tables.md`): lossy defaults removed far more, e.g. headroom 18.7 / 23.4 / 36.5% and LLMLingua-2
  18.3 / 33.4 / 58.3% on mixes 1 / 2 / 3. That run used an earlier ctxprune runtime (8ad8a8b), so its ctxprune row is
  not quoted; §1 has the 0.3.0 numbers.
- Replay is one step deep: a dropped detail that matters ten turns later isn't seen.

## Not quoted, and why

- **Hermes savings on my own sessions** (not published: private logs). They were measured on pinned pre-merge
  trees (1979e27 / 3859d9d) on private logs, and the proxy figure includes dedup. I didn't re-run them on 0.3.0.
- **Dollar savings.** These depend on the model price and on how often each output is re-read from cache. `ctxprune
  stats --price` gives a rough local estimate.
- **Claude models other than Opus 5.5** (Sonnet, Haiku): not probed, so they get dedup off.
