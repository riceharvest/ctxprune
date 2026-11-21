# T1.10 / idea 145: cross-call MCP JSON entity cache

## UPDATE 2026-11-19 (author): real-trace screen PASSES the savings gate; comprehension probe still pending

The earlier no-go below missed the real ordered MCP sessions in `~/.cache/ctxprune-typical/raw/mcpmark` (Claude Sonnet-4/Opus-4.1 runs with real github-mcp-server and Notion MCP outputs, one `messages.json` per session, tool outputs in order).
Screen: `docs/ideas/results/145/screen_cross_call_entities.py` (CPU only, ~1 min, o200k tokens). Per session, every nested JSON object (compact, key-sorted, >=60 chars, >=2 keys) that already appeared in an EARLIER call is replaced by a ~8-token reference. Conservative filters: root object never replaced, exact-duplicate outputs skipped (existing dedup covers them), outputs >50k chars excluded (Claude Code spills them), legend cost not counted beyond the 8-token ref.

| set | sessions | tokens (outputs <=50k) | additional saved | median/session | p90 | sessions >=5% |
|---|--:|--:|--:|--:|--:|--:|
| MCPMark github | 45 | 702,693 | **15.35%** | 3.35% | 39.6% | 21/45 |
| MCPMark notion | 56 | 1,302,462 | **26.18%** | 24.3% | 36.7% | 55/56 |

Savings gate (>=5% on multi-call MCP sessions): **PASS** on aggregate (and on the median Notion session; github median is 3.4%, mean driven by list-heavy sessions). Caveats: upper bound until the real ref format+legend are implemented and measured; overlap with `json_compact`/rows rules not yet netted out (run through `process()` with `DedupSession` before claiming); Toucan not usable (independent calls). Not yet run: reader comprehension probe (needs a batch job; do before default-on). Distribution: MCP PostToolUse hook seam (`mcp__*`) of the safe preset with per-server session state, fail-open, shipped via the existing `ctxprune install` / plugin channels. Next steps: (1) implement as opt-in rule over `DedupSession`, re-measure net savings with real encoding; (2) 16k-56k reference-resolution probe on the GPU.

## Original decision (superseded by the update above)

**No-go for implementation on this screen; retain as unproven, not as a measured negative.** The required R1 evidence is a real ordered multi-call MCP session containing repeated entities. No GitHub or Linear MCP session corpus was present in the inspected project data, and the available Toucan file contains independent examples rather than calls from one session. Therefore the ≥5% multi-call savings gate and the reader comprehension gate cannot be evaluated. The synthetic fidelity simulation is encouraging but is not a substitute for either gate.

## Hypothesis and gates

Hashing nested objects across calls in one MCP server session can remove at least 5% of total session output tokens while a reader resolves references correctly. Proceed only after real Toucan and GitHub/Linear multi-call sessions demonstrate ≥5% additional savings and the comprehension probe passes.

## R1 screen and evidence

- Runtime: 6.5 seconds wall; 0 GPU minutes; CPU lock honored.
- Gate result: synthetic simulation **pass**; `bytes_original=31298`, `bytes_sent=13021`, `saved_pct_whole_session=58.4`, `saved_pct_first_three_calls=60.6`.
- This simulation includes a synthetic GitHub page-1/page-2/issue-detail trajectory and exact reader reconstruction. It does not measure actual session token savings or model comprehension.
- The runner also printed an unrelated discovery error for `cases_alias_model_cache.py` (`ModuleNotFoundError: ctxprune`); idea 145 itself passed.
- Corpus inspection: the private Toucan trace corpus consists of independent examples, not ordered calls with server/session identifiers. A targeted search of the local trace corpus found no GitHub/Linear/Toucan MCP call-session corpus. No external MCP server was contacted.
- Paths: `docs/ideas/gate/cases_struct_a.py` (SHA-256 `e9c5bc64db0437454a5241c8e1699c540004e758cf7970875f4d6c4f03fa3d3d`); `docs/ideas/gate/run.py` (SHA-256 `7fc1f950211e0ce450664782eebc7486b24080f364e48f8cf9631ebf6fd3eb71`); corpus in the lab tree-v2/internal lab notes`.
- Compute: 0 GPU, $0. The output above is a synthetic proxy; do not claim real-world savings from it.

## Next evidence needed

Provide ordered multi-call GitHub and Linear MCP sessions plus the corresponding Toucan trajectories, then count repeated nested object tokens across calls. If the aggregate remains below 5%, stop. If it clears 5%, run the registered reference-resolution comprehension probe before considering default enablement. Any eventual integration belongs at the existing MCP output boundary/hook seam, with per-server session state, fail-open behavior, and the existing client/plugin distribution channels.

## Claim allowed

The existing synthetic gate passes exact reconstruction and reports 58.4% byte savings on its constructed trajectory. No claim about actual MCP sessions, token savings, or model comprehension is supported yet.
