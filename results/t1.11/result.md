# T1.11 high-yield lossless structured rules: screen on real traces (2026-11-19)

Verdict: NEGATIVE at the screen. No rule moves a real workload by >= 0.5%. No long-context probe run (not worth GPU).

Method (CPU, lab-cpu): gate prototypes (`docs/ideas/gate`, encode/decode/equivalent) run on 51,650 real tool outputs:
agent_samples (554), local_eval (1,710), 30d Claude Code transcripts (~49.4k results >200 chars). `screen.py`, `screen_cc.json`.
All 9 gate cases pass on their own fixtures; the issue is that real traces almost never contain these shapes.

| idea | fired / 51,650 | chars saved, % of all tool-output chars | round-trip/oracle fails |
|---|---|---|---|
| #1424 GitHub REST URLs | 0 | 0 | 0 |
| #325 base64 content | 0 | 0 | 0 |
| #520 mixed-shape arrays | 0 | 0 | 0 |
| #130 hoist constants | 0 | 0 | 0 |
| #519 lexeme minify (multi-doc/truncated JSON) | 156 | 0.023% (~25-30% on fired) | 0 |
| #412 ASCII grid -> TSV | 0 | 0 | 0 |
| #1326 Unicode box -> TSV | 9 | 0.005% (~30% on fired) | 0 |
| #323 HTML span soup | 0 | 0 | 0 |
| #1429 git status long -> short | 16 | 0.0007% (~1% on fired) | 9 of 16 fail the equivalence oracle on real data |

Hermes `patch_echo` strip: not re-measured; prior 31.4% on 64% of Hermes patch results (lossless-each/FINDINGS.md) stands and is the only
rule of the list with real yield; it is Hermes-specific and belongs to a separate card.

Kill gate: "0 failures AND passes long-context probe". #1429 fails (0 failures violated). Others pass the failure clause but fail the
screen threshold (>= 0.5% of workload, or >= 15% when fired AND fires non-trivially): #519 and #1326 are >= 15% when fired but fire on 0.3% / 0.02% of outputs,
i.e. ~0.02% of tokens. Consistent with the earlier finding that stage-0 lossless is ~0% on the user's own outputs.

Recommendation: do not ship any of these in `safe`. Optionally ship #519 as a no-risk, no-probe-needed micro-rule only if someone wants it
(it only touches JSON that json.loads rejects). Caveat: the traces are this user's coding/ops work; an API-heavy (GitHub REST/MCP) workload
would fire #1424/#325/#520/#130, but we have no such trace to test, and they remain unscreened there.

Distribution plan: none. Nothing is proposed for shipping, so no seam/channel and no user. If #519 were added it would go through the existing
lossless tool-output hook (the same seam as jsonrows/json_ascii) in Claude Code/Hermes/Pi/OpenCode/Codex hooks.
