# T2.4 New integrations & async plugin polish

Verdict: shipped (code items); live checks and distribution PRs NOT done.

Done
- Cline SDK `afterTool` plugin: scripts/integrations/cline/ctxprune.mjs (lossless `safe`, async execFile, fail-open + marker escape).
- Copilot CLI `postToolUse` hook: scripts/copilot_hook.py (returns full `modifiedResult` object, skips view/edit/non-success).
- Pi / OpenCode / dsh plugins: execFileSync -> async execFile (stdin written via child.stdin.end; opencode dedupOn/off now async).
- `cline`, `copilot` added to PRESET_HARNESS (lossless only) in the 3 ctxprune_core.py copies.
- Tests: tests/test_new_integrations.py (copilot stdin contract, cline via node with fake afterTool ctx) + tests/test_integrations.py: 8 passed, 2 skipped.
- `node --check` ok on dsh/opencode/cline.

Not done / unverified
- Live Codex CLI through ctxprune_proxy.py: not run (needs model quota/login; no unattended run). Still unverified live.
- Cline and Copilot payload shapes follow docs only (no recorded live payload). Pi .ts not run under a real Pi.
- Distribution PRs (Hermes catalog, Claude Code plugin directory, npm pi-ctxprune/opencode-ctxprune, leanctx/llmlingua-2-js) not opened: outward-facing, left for overseer after 0.4.0.

Distribution plan (METHODOLOGY 13)
- Cline users: `cline plugin install` of the .mjs; Copilot CLI users: postToolUse hook entry pointing at copilot_hook.py;
  both via `ctxprune install` seam (installer wiring not added here). Pi/OpenCode users: npm packages from existing plugin files.
