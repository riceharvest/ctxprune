# Benchmarks

Every number in the README comes from this page. For each one I give the script, the code it ran against, and the
file the number is in.

**Compute.** The GPU runs here (the open-model comprehension and long-context probes in §4, the `balanced` replay and
agent-session runs in §6, and the competitor replay in §8) ran on rented RTX 5090s from
[CloudRift](https://www.cloudrift.ai), provided through their AI Builder Grant; §5 is computed from those probe
answers. §1–3 and §7 ran locally, and the Claude Opus probes ran through Claude. §9 and §10 (0.4) ran
locally too: the reader on a local Intel Arc B70, the compressors on CPU and a local RTX 3060, with no rented compute. §11 (0.5) used no rented compute; the CloudRift ledger has no rental after 2026-10-21.

**Runtime.** Unless noted, numbers are for the ctxprune runtime at research-repo `main` **12c7b79** (2026-10-18).
This packaging branch is rebased onto that commit, and its wheel vendors `scripts/` unchanged. The re-runs used a
clean `git archive 12c7b79` export, not a working tree.
The 0.4 numbers in §9 and §10 come from the 0.4 code at lab commit `707898a`; the reviewer rebuilt 18 held-out arm
items byte-exact on it and on the later release candidate.

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
  paired per item against a resample of the raw prompt (the noise floor). 95% bootstrap over trajectories.
- Versions: headroom-ai 0.41.0, rtk 1.0.1, sqz 2.1.1, optscale-kompressor 1.0.2, llmlingua 0.2.2 (LLMLingua-2 at keep
  0.33, applied at ctxprune's hook reach).
- Reproduce: `python3 results/compare/harm_score.py --raw-dir results/rented/replay-compete/replay` (research repo,
  harm_score.py @ a8e2c83).

| tool | items changed | Qwen3.6 divergence | Qwen3.6 re-fetch | Qwen3.8 divergence | Qwen3.8 re-fetch |
|---|--:|---|--:|---|--:|
| ctxprune `safe` | 7 | n/a (too few) | 0 | n/a | 0 |
| headroom | 133 | +9.0 [+0.0, +18.3] | +3.8 | +18.0 [+10.1, +26.3] | +3.8 |
| kompressor `lossless` mode | 48 | +0.0 [−16.7, +16.7] | +14.6 | +25.0 [+12.5, +37.5] | +12.5 |
| rtk | 109 | +18.3 [+8.6, +28.8] | +5.5 | +22.0 [+13.6, +31.4] | +13.8 |
| sqz | 45 | +13.3 [−4.4, +31.8] | +15.6 | +17.8 [+4.3, +32.6] | +15.6 |
| ctxprune `balanced` (lossy, experimental) | 198 | +7.1 [+0.0, +14.7] | +7.6 | +15.7 [+8.7, +22.8] | +17.2 |
| LLMLingua-2 | 192 | +10.4 [+1.6, +18.6] | +18.8 | +15.6 [+7.4, +23.3] | +27.1 |
| kompressor | 351 | +10.0 [+3.7, +16.3] | +23.6 | +12.5 [+6.7, +18.3] | +32.2 |

Rows are sorted by Qwen3.8 re-fetch. **Correction (2026-11-19):** an earlier version of this table showed much larger
divergence figures (e.g. LLMLingua-2 +42.7, kompressor +44.7 on Qwen3.8). They came from a paired script that was run
interactively and never committed, and the committed scorer does not reproduce them. They are replaced by the
committed scorer's numbers above (excess over the noise floor, per item, on the items each tool changed). Re-fetch
figures were unchanged.

How to read it:
- Divergence is small to moderate and noisy: on Qwen3.6 four of the seven intervals touch or include zero; on Qwen3.8
  every lossy tool's interval excludes zero (+12 to +25 points). The tools are not separable on divergence alone.
- The clearer signal is re-fetch (the agent has to go back for what the tool dropped). headroom was the mildest
  (+3.8 on both readers); kompressor (+23.6 / +32.2) and LLMLingua-2 (+18.8 / +27.1) were the heaviest. ctxprune
  `balanced` sits in between (+7.6 / +17.2). On divergence, headroom and `balanced` overlap on both readers, so
  "headroom does better than `balanced`" is not supported by this data.
- The kompressor "lossless" mode still raises re-fetch by 12 to 15 points and, on Qwen3.8, divergence by +25.
- `safe` changed only 7 of these 400 items (most were file views and code, which it leaves alone), so this shows that
  it does no harm where it fires, not that it saves much here.
- The same study measured savings at each tool's own reach on 30.6k public tool outputs (research repo
  `results/compare/tables.md`): lossy defaults removed far more, e.g. headroom 18.7 / 23.4 / 36.5% and LLMLingua-2
  18.3 / 33.4 / 58.3% on mixes 1 / 2 / 3. That run used an earlier ctxprune runtime (8ad8a8b), so its ctxprune row is
  not quoted; §1 has the 0.3.0 numbers.
- Replay is one step deep: a dropped detail that matters ten turns later isn't seen.

## 9. LongBench rematch against LLMLingua-2

LLMLingua-2's own benchmark, with its own prompts and scorer (`metrics.py`, first-line rule, max over answers), 9 English
LongBench tasks. Two separate sets, and they must not be mixed up:

- **Held-out (the headline, 9.1):** 270 items never used to build or tune ctxprune 0.4, frozen before use.
- **Dev-set (9.2):** 256 items the 0.4 code was tuned on. Every number there is dev-set.

Reader for both: Gemma-4-31B-it QAT q4_0 via llama.cpp, temperature 0, thinking off, picked from outside the Qwen and
DeepSeek families our model was trained from. The reader ran locally (Intel Arc B70), not on rented GPUs.

### 9.1 Held-out: level with LLMLingua-2-large

**ctxprune 0.4 in query mode is level with LLMLingua-2-large on held-out LongBench. It does not beat it.**

- Items: 30 per task from 9 tasks (270). The configuration (code commit, flags, budget rule, statistics) was written down
  and hashed before any ctxprune arm was built (`frozen_config.json` sha256 `32c11b71…966670ea`, committed in the
  research repo at `36b5ddb`). The held-out set was read once.
- **Disclosure:** 0 items overlap the dev set exactly, but 21 of the 270 share a source document with a dev item
  (18 of the 30 `qmsum` items, 2 `multifieldqa_en`, 1 `qasper`). Results without them are given below.
- Budget: ctxprune and the BM25 baseline are matched per item to the token count LLMLingua-2-large actually produced
  (o200k): mean 2,038 vs 2,051 tokens (0.993x), no item above 1.020x, no fallbacks. LLMLingua-2 ran natively
  (`target_token=2000`, `drop_consecutive=True`).
- **Query-aware vs query-blind.** `v04_query` is given the question (`query=`); LLMLingua-2 and `v04_agnostic` are not.
  `gov_report` and `lcc` have no question, so there `v04_query` equals `v04_agnostic`. `bm25_only` is a zero-model baseline:
  the same unit splitter, ranked by BM25 against the question, at the same budget.

Per-task score (LLMLingua metrics x100), 30 items per task:

| task | LLMLingua-2-large | v04 query-blind | **v04 query** | BM25 only |
|---|---:|---:|---:|---:|
| qasper | 51.9 | 49.0 | 49.2 | 47.1 |
| multifieldqa_en | 50.0 | 50.6 | 58.5 | 56.9 |
| hotpotqa | 54.9 | 39.1 | 48.8 | 47.3 |
| 2wikimqa | 58.8 | 48.9 | 59.9 | 53.6 |
| gov_report | 25.8 | 25.7 | 25.9 | 26.9 |
| qmsum | 22.1 | 20.5 | 22.1 | 23.8 |
| triviaqa | 88.4 | 89.1 | 89.1 | 85.7 |
| passage_retrieval_en | 80.0 | 98.3 | 100.0 | 96.7 |
| lcc | 10.5 | 9.7 | 10.1 | 19.2 |
| **macro, 9 tasks** | 49.15 | 47.87 | 51.51 | 50.80 |
| macro, 8 tasks (no `passage_retrieval_en`) | 45.29 | 41.56 | 45.45 | 45.06 |

Paired differences (2,000 bootstrap resamples within task, 95% CIs):

| comparison | macro, 9 tasks | macro, 8 tasks | reading |
|---|---|---|---|
| v04 query − LLMLingua-2-large | **+2.36 [−0.70, +5.51]** | +0.15 [−2.76, +3.42] | level with |
|... without the 21 dev-shared-document items (n=249) | +1.93 [−1.14, +5.27] | −0.32 [−3.42, +2.72] | level with |
| v04 query-blind − LLMLingua-2-large | −1.28 [−4.12, +1.55] | **−3.73 [−6.52, −1.14]** | level on 9 tasks, worse on 8 |
| v04 query − BM25 only | +0.71 [−1.77, +3.40] | +0.38 [−2.34, +3.33] | level with |
|... n=249 | +0.30 [−2.14, +2.93] | | level with |
| v04 query − v04 query-blind | **+3.64 [+1.06, +6.31]** | +3.88 [+0.99, +6.84] | seeing the question helps |
|... n=249 | +3.40 [+0.82, +6.04] | | |

What it says:

- The 9-task lead of the query mode over LLMLingua-2-large comes from two tasks: `passage_retrieval_en` (+20.0
  [+6.7, +33.3]; the question is matched to its paragraph, a lexical shortcut) and `multifieldqa_en` (+8.5 [+3.3, +14.7]).
  It is lower on `hotpotqa` (−6.1 [−17.0, +4.0]) and `qasper` (−2.7 [−8.7, +3.6]); `qmsum` and `lcc` are level
  (−0.0, −0.4). Without `passage_retrieval_en` the difference is +0.15.
- **The neural model's added value over lexical query matching is not shown:** a zero-model BM25 selector at the same
  budget is level with `v04 query` (+0.71 [−1.77, +3.40]).
- **Query-blind mode** (no question, the use case LLMLingua-2 is built for) is **worse** than LLMLingua-2-large without
  `passage_retrieval_en`: −3.73 [−6.52, −1.14]; on the four QA tasks −7.00 [−12.10, −2.29]; `hotpotqa` −15.8
  [−28.4, −4.8]. Its only large win is `passage_retrieval_en` (+18.3 [+5.0, +33.3]).
- Query-aware and query-blind are different problems. We did not run LongLLMLingua (the question-aware LLMLingua
  family), so **there is no claim here against it.**

Smaller arms (10 items per task, 90 items, chosen before the run; wide CIs): LLMLingua-2-base, the 0.3.2 engine and random
atom deletion at the same budget.

| comparison (n=90) | macro, 9 tasks | macro, 8 tasks |
|---|---|---|
| v04 query − random deletion | +12.90 [+6.82, +19.05] | +7.02 [+1.14, +13.23] |
| LLMLingua-2-large − random deletion | +10.49 [+3.08, +17.62] | +5.55 [−0.40, +11.97] |
| v04 query − LLMLingua-2-base | +8.08 [+2.13, +14.14] | +5.34 [−0.20, +11.20] |
| v04 query-blind − LLMLingua-2-base | +3.26 [−1.89, +8.78] | +0.55 [−3.94, +5.57] |
| v04 query − 0.3.2 engine | +3.50 [−2.09, +9.00] | −2.31 [−7.13, +2.49] |
| v04 query-blind − 0.3.2 engine | −1.32 [−7.24, +4.79] | −7.11 [−13.09, −1.56] |

The +8.08 against LLMLingua-2-base is a query-aware method against a query-blind one, on 90 items, and its 8-task CI
touches 0; it is not a "beats LLMLingua-2-base" result. Against the 0.3.2 engine the 0.4 query mode is level. Random
deletion scores far lower, so the test does detect information loss. Positive control: on a 30-item uncompressed
subset (3-4 items per task) the compressed arms read worse than the full text (v04 query −8.25 [−12.89, −3.95],
LLMLingua-2-large −5.39 [−10.94, +0.64]). Re-reading 60 rows with an empty cache gave 51 identical answers, with
mean per-arm score shifts of at most 0.11.

**Power.** The pre-registered detectable difference at n=270 was about 4.8 points; the hypothesised +2 sat below it, so a
true +2 would usually read as "level with". It did.

Deviations, all disclosed: the first uncompressed-baseline run was invalid (the llama.cpp Vulkan server crashed; the 29
error answers were removed from the cache and the arm was re-run); the reader ran ~7.3 h locally; mean tokens were
counted with o200k, not in Gemma tokens (the reviewer audited the reader's own token counts: 0.9817x for the 0.4
arms against LLMLingua-2-large, no truncation). Review: **WEAKER** (numbers and freeze order reproduce; the wording above is
what the reviewer allowed). Raw answers, scripts and the review are in the research repo (commit `493f658`,
`internal lab notes`).

### 9.2 Dev-set (the 0.4 code was tuned on these items)

The earlier rematch: 256 items (30 per task, 16 for `lcc`), the same reader and scorer, ctxprune matched per item to
LLMLingua-2-large's token count (plain 1.006x, `protect` 1.049x because forced IDs cannot be dropped). LLMLingua-2 ran
natively at `target_token=2000` (this page said "rate 0.33" before; that was wrong). **The 0.4 code was tuned on exactly
these items, so the 0.4 columns are dev-set results and are not evidence of a win.** The 0.3.2 columns were not tuned on
them.

| task | LLMLingua-2-base | LLMLingua-2-large | 0.3.2 | 0.3.2 `protect` | v04 query-blind | v04 query |
|---|---:|---:|---:|---:|---:|---:|
| qasper | 45.4 | 49.6 | 49.8 | 48.7 | 47.7 | 50.5 |
| multifieldqa_en | 41.3 | 42.5 | 42.7 | 38.1 | 40.8 | 58.4 |
| hotpotqa | 39.1 | 46.8 | 49.7 | 46.4 | 54.9 | 72.1 |
| 2wikimqa | 53.4 | 56.2 | 54.7 | 52.9 | 51.5 | 52.0 |
| gov_report | 25.3 | 25.7 | 24.8 | 24.4 | 24.6 | 25.0 |
| qmsum | 21.4 | 23.2 | 16.4 | 16.0 | 22.2 | 23.8 |
| triviaqa | 90.4 | 90.4 | 90.4 | 87.1 | 90.4 | 83.8 |
| passage_retrieval_en | 80.0 | 83.3 | 63.3 | 80.0 | 100.0 | 100.0 |
| lcc (n=16) | 8.6 | 9.0 | 14.6 | 14.2 | 8.7 | 8.7 |
| **macro, 9 tasks** | 45.0 | 47.4 | 45.2 | 45.3 | 49.0 | 52.7 |

Paired differences to LLMLingua-2-large (dev-set): 0.3.2 −2.3 [−6.0, +1.5] (level with LLMLingua-2-base, not better than
-large), 0.3.2 `protect` −2.1 [−6.2, +1.9], v04 query-blind +1.6 [−1.3, +4.6], v04 query +5.3 [+2.0, +8.5].
**The dev-set +5.3 did not replicate on held-out (+2.36 [−0.70, +5.51], 9.1).**

**Losses on the dev set, shown on purpose:**

- **`triviaqa`** (83.8 for v04 query vs 90.4): the whole gap is two items on which the reader answered "not mentioned in
  the passage"; neither 0.4 mode keeps the `Question:`/`Answer:` markers of the 24 few-shot examples.
- **`lcc`** (8.7 vs 14.6 for 0.3.2): one item (`lcc-3`, 83 → 0) accounts for the gap; the reader answers in prose on 14
  of 16 items in every arm. This is a known regression of the 0.4 engine on source code.
- **`passage_retrieval_en`** scores 100 in both 0.4 modes (0.3.2 without `protect`: 63.3). That is a paragraph-number
  match, a BM25-style shortcut, which is why 9.1 also gives the 8-task numbers.
- Without `passage_retrieval_en` the dev-set margin over LLMLingua-2-large is +3.84 [+0.35, +7.11].

Opt-in fixes (default off, so 0.4 output is unchanged unless you turn them on): `Compressor(fewshot="pin"|"noquery"|"both")`
and `Compressor(rep_decay="prose"|...)`. On the dev set only, with `fewshot="pin"` and `rep_decay="prose"`: triviaqa 92.67
(was 83.78; two items, CI [0.0, +20.0]), `lcc` 14.69 (was 8.69; one item; on the 30-item dev extension 11.17 vs 6.97,
CI includes 0, and 0.3.2 scores 18.00), 9-task macro 54.27 (was 52.69; +1.58 [+0.09, +3.56]). Because the lcc effect is not
robust and the registered gate as a whole was not met, they stay opt-in. The review of this experiment: **WEAKER** (numbers reproduce;
"recovers / level with", not "fixed"). Evidence: research repo `internal lab notes`.

## 10. Other 0.4 measurements (dev-set or offline proxy, not held-out, not independently reviewed)

Gemma-4-31B-it reader unless stated. These are screens used while building 0.4; none of them is a held-out result.

| probe | 0.3.2 | LLMLingua-2-large | v0.4 | note |
|---|---:|---:|---:|---|
| multi-document RAG, F1 (`compress_many`, n=30, shared 0.5 budget) | 72.5 (4,835 tokens) | | **75.3** (4,813) | +2.8 F1, n=30, dev-set |
| `llmlingua_facts`: share of 605 facts kept (n=120 chunks, `protect="auto"`) | 62.1% | 86.6% | **96.2%** | offline proxy; 0.3.2 with `protect=True` scores 97.7% |
| MCP JSON: valid JSON / scalar recall (n=40 at 0.5) | 0.0% / 50.0% | 0.0% / 71.4% | **100.0% / 95.8%** | JSON-values mode |
| negation probe (n=40): `polarity_safe` | 80.0% (F1 44.8) | 47.5% (F1 48.8) | **80.0%** (F1 49.3) | **no improvement** on polarity safety: 0.3.2 and 0.4 are equal |
| query-time slicing, 10 pre-scored chunks | | | 0.30 ms (1.05 ms before the cache) | cached anchor plan, byte-identical output; commit message numbers, CPU |

How to read it:

- The facts gain is mostly the `protect` flag, not the 0.4 engine: 0.3.2 with `protect=True` keeps 97.7% of the facts,
  `protect="auto"` 96.2%, and 0.3.2 without protection 62.1%. The 0.4 `auto` mode's advantage is that it prunes plain prose
  while keeping technical text intact; it is not a higher fact-retention score.
- On the negation probe 0.4 matches the 0.3.2 polarity-safe rate (80.0%) and does not improve it; both are far above
  LLMLingua-2-large (47.5%). n=40 is too few to see a small difference.
- The RAG result is n=30 on the dev set (+2.8 F1); an offline answer-hit proxy on the same 30 items gave 80.0% vs 73.3%.

## 11. 0.5 items (opt-in, mostly unmeasured)

Nothing here is held-out. Each row says what the number is, its n, and where it comes from. Rule for 0.5: a number is
quoted only if an independent review exists; otherwise it is labelled an author screen. The table of items and their
review cards is `docs/lab/v0.5-release-claims.md`; repo result files are under `docs/lab/` and `docs/ideas/results/`.

| item | number | scope | source |
|---|---|---|---|
| entity refs across calls (T1.10b) | notion 4.81% [3.42, 6.36], github 10.28% [3.94, 18.31] (median 0%) more o200k tokens, hosted default (jsonrows on) | validation split of MCPMark sessions, not held-out; notion n=28, github n=23 sessions | independent review (WEAKER), `docs/ideas/results/145/t110b_result.md` |
| entity refs, reader probe | **-24.3 points** [-30.8, -17.9] on questions about replaced objects (raw 61.7% -> 37.4%) | validation sessions, n=243 questions / 34 sessions, local Qwen2.5-1.5B reader only | same files; the reason there is no preset |
| log template miner (L3.2) | 73-85 ms per 5k lines vs a 15 ms gate: **not shipped as a hook rule** | CPU, 30 real + 30 synthetic logs, byte-identical output, 0 alarm lines lost | independent review (PASS) |
| `mcp-wrap` | 8.4% o200k tokens saved, 0 parse breaks | author offline replay, no live client, no review | `docs/lab/v0.5-release-claims.md` row 4 |
| `mcp-gateway` | static schema 92.5% smaller in **characters of JSON** (not tokens); 1-2 scripted discovery turns | author screen, no live client, no completion rate | `docs/lab/L4.3-result.md` |
| SDK wrappers | first render of a new 7 KB JSON costs 7-25 ms | author timing, httpx mock transport | `docs/lab/v0.5-integration-result.md` |
| `read_skeleton` / `read_symbols` | 85.19% fewer whitespace-separated words | proxy, 9 Python files, no reader eval | `docs/lab/v0.5-integration-result.md` |
| `ctxprune notebook` | 96.66% pooled | author screen, 12 output-heavy notebooks, no reader eval | `docs/lab/v0.5-release-claims.md` row 9 |
| `--redact-secrets` | 100% recall, 0 / 40,000 false positives | synthetic secrets (2,000), author screen | `docs/lab/v0.5-release-claims.md` row 10 |
| `jsondefs` (`CTXPRUNE_JSONDEFS=1`) | 46.3% vs 7.7% o200k | 4 Notion fixtures, author, no reader eval | `docs/lab/v0.5-release-claims.md` row 15 |
| `paths_win` (`CTXPRUNE_PATHS_WIN=1`) | 0% -> 51.7% | author fixtures, no reader eval | `docs/lab/v0.5-release-claims.md` row 16 |

Release checks (tests, not benchmarks; frozen commit 1fcedcc, release gates run): full suite 759 passed / 3 skipped;
smoke scripts 219 passed; default lossless output identical to 0.4.1 on 78 input files x 4,212 calls (digest
`60e015d8aec913b3` both trees); default `ctxprune mcp` tool list identical. This says nothing about inputs outside
those files.

Compression quality did not change in 0.5. The held-out LongBench result (§9.1) is still *level with* LLMLingua-2-large,
and the dev-set losses in §9.2 (`triviaqa`, `lcc`, `passage_retrieval_en`) still apply.

## Not quoted, and why

- **Hermes savings on my own sessions** (not published: private logs). They were measured on pinned pre-merge
  trees (1979e27 / 3859d9d) on private logs, and the proxy figure includes dedup. I didn't re-run them on 0.3.0.
- **Dollar savings.** These depend on the model price and on how often each output is re-read from cache. `ctxprune
  stats --price` gives a rough local estimate.
- **Claude models other than Opus 5.5** (Sonnet, Haiku): not probed, so they get dedup off.
