# Later integrations: Cline and Copilot CLI (design notes, not built)

Both can replace a tool result while keeping it a success, so both can run the `safe` pipeline as it is. Neither
can see what is still in context, so like the Pi/OpenCode/dsh plugins they get the lossless rules only, no
cross-turn dedup. Facts below were checked against the upstream repos and docs on 2026-10-17. Re-check the API
shapes against the current release before building.

## Cline (SDK runtime plugin, `afterTool`)

- **Hook.** An SDK plugin's `afterTool` gets `AgentAfterToolResult`. Its `result` replaces the tool result that
  the model sees (`sdk/packages/shared/src/agent.ts`). The CLI, VS Code and JetBrains share these plugins.
  Cline's file hooks (`PostToolUse`) can only observe or append, so they are no use here.
- **Shape.** Write a single-file TS plugin in the same style as `scripts/integrations/opencode/ctxprune.js`:
  - Skip the file read/edit/write tools and non-text results.
  - Join the text parts and call `CTXPRUNE_PYTHON CTXPRUNE_CLI --command=… --tool=… --harness cline`.
  - Return the shorter text with the original result object's other fields copied. Copilot's #3361 shows that
    returning a bare string can be silently ignored.
- **Install.** `ctxprune install cline` would copy the file with the same two-line env header the other JS
  plugins get, then print `cline plugin install <path>`. Cline has no plugin registry; `cline plugin install`
  also takes a GitHub URL or an npm package.
- **Preset.** Add `"cline": {"lossy": False}` to `ctxprune_core.PRESET_HARNESS`, as for Pi/OpenCode: there is no
  session state and so no refetch guard.
- **Open questions.**
  - Does `afterTool` run before or after Cline's own truncation?
  - Is the plugin call async? A blocking `execFileSync` is fine for safe mode (60–90 ms) but should become
    `execFile` (audit P1 11).
  - What argument names does the shell tool use (`command`?), so we can build the stand-in command for grep
    grouping?
- **Test.** Use a doctor canary like the OpenCode one: load the plugin in node with a fake `afterTool` context.
  Then do one live run with Cline CLI against a mock OpenAI-compatible endpoint, the same way
  `tests/packaging/cc_live.py` does it.

## Copilot CLI (`postToolUse` hook, `modifiedResult`)

- **Hook.** In a plugin's `hooks.json`, a `postToolUse` command hook can return `modifiedResult` with
  `resultType: "success"` (docs.github.com/en/copilot/reference/hooks-configuration). This shape differs from
  Claude Code's `updatedToolOutput`. Known bug [#3361](https://github.com/github/copilot-cli/issues/3361): a
  plain string is ignored and the full result object has to be returned.
- **Shape.** Add a Python hook entry next to `lossless_hook.py`, for example `ctxprune hook copilot`. The steps:
  1. Read the hook JSON from stdin.
  2. Pick the text field of the shell/grep result.
  3. Run `ctxprune_core.process` (safe: lossless, no dedup unless the payload carries a transcript path we can
     replay).
  4. Print the full result object with that field replaced, plus `resultType: "success"`.

  It reuses glue.boot() (kill switch, stats) like the Claude Code hook. Don't fork `lossless_hook.target()`:
  write a separate `target_copilot()` for Copilot's payload, built from a recorded fixture.
- **Distribution.** Copilot CLI also reads `.claude-plugin/` marketplaces, so the `ctxprune-safe` bundle can
  carry a second hooks file. Check whether Copilot reads `hooks/hooks.json` from a Claude-format plugin or
  needs its own manifest. If it needs its own, `ctxprune bundle copilot` writes it.
  awesome-copilot takes external plugins through its issue form (ref + full SHA).
- **Open questions.**
  - Record a real `postToolUse` payload for the shell tool, a grep and an MCP tool (needed as fixtures; audit
    P1 14).
  - Does Copilot schema-check the modified result per tool, as Claude Code does?
  - Is there a minimum Copilot CLI version for `modifiedResult`? `doctor` should check it the way it checks
    Claude Code ≥ 2.1.121.
- **Test.** Use a stdin-contract test on the recorded fixtures, then one live `copilot -p` run against a mock
  endpoint if its auth allows a dummy key (otherwise fixtures only, stated as such).
