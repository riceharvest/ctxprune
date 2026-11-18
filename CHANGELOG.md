# Changelog

## 0.3.0 (2026-11-18)

0.3.0 is a rewrite of the agent integrations. 0.2.1's Claude Code hook deleted words with a token model. 0.3.0's hooks
by default rewrite tool outputs into a shorter form that keeps the content, and they run inside more agents. The
model-based line selection for agents is an opt-in preset that's still experimental. The 0.2 token-pruning API is
unchanged (see "Kept from 0.2.1").

### New
- **The `safe` preset is the default.**
  - Aligned tables are single-spaced, and redundant `---`/`+++` diff headers are dropped.
  - JSON record lists with nested values (typical MCP results) become rows. `\uXXXX`-escaped JSON is shown with its
    characters (`json_ascii`). Playwright MCP snapshots lose the YAML dashes.
  - BOM and CRLF text is handled exactly. Windows `\` path lists stay raw.
  - Runs of identical lines are counted. Colour codes and progress redraws are removed.
  - Standard library only, with no model and no network.
  - [README](README.md#what-is-exact-and-what-isnt) says which of these are byte-exact and which aren't.
- **Integrations:**
  - Claude Code: a hook for Bash, PowerShell, Grep and MCP tools.
  - Hermes, Pi, OpenCode and dsh: plugins.
  - Codex: a local proxy.
  - `ctxprune install --all`, `uninstall`, `upgrade`, `doctor`, `stats`.
- **`ctxprune audit`:** for Claude Code, where the bill goes (system prompt, MCP listings, skills, CLAUDE.md, hooks,
  thinking, tool calls, tool results) and which static context you could drop; for Codex, Pi, OpenCode, Hermes and
  dsh, the static-overhead checks that apply (session prefix, instruction files, MCP servers and their use, skills,
  plugins, prompt-cache share, provider caching rules), each marked verified or unverified. Local and advice-only:
  numbers and names, never content, no config changes.
- **`ctxprune stats`** shows one row per agent plus a total, with the share of input ctxprune removed where the
  agent keeps usage records locally, the subscription reading of it (usage-limit headroom), and the dollar figure
  at API prices. `ctxprune doctor` ends with a per-agent status summary.
  `ctxprune install` names `stats` and `audit` when it's done. `--weekly-summary` (opt-in) adds a once-a-week line at
  session start that only you see.
- **`ctxprune-safe` Claude Code plugin:** self-contained, with no pip needed. There's also a self-contained Hermes
  plugin build (`ctxprune bundle`).
- **Spill files (Claude Code):** when Claude Code saves a big output to a file and shows only a preview, the file is
  rewritten losslessly. `CTXPRUNE_SPILL=0` turns it off.
- **Long-context probe.** Every default rule was re-checked with the output inside a 16k–56k-token agent transcript
  (Qwen3.6, the shipped bytes, tool schemas sent). Four rules that passed the short probes did worse there and are
  opt-in now. On public Linux Claude Code sessions they were most of what the default saved without dedup (0.98% of
  tool tokens before, 0.29% now); on MCP-heavy mixes they were under 1 point:
  - grep grouping (`CTXPRUNE_HITS=1`): without thinking, hits filed under the wrong file, −3.7 [−9.7, +3.2] points
    (with thinking it helped, +8.3; the bar was both modes);
  - `paths` directory grouping, with Hermes `sffiles` (`CTXPRUNE_PATHS=1`, or `CTXPRUNE_PATHS_MODELS=<hosted model
    ids>`): files under the wrong directory, −8.3 [−20.9, −0.5] points, −11 at 56k;
  - flat-record `jsonrows`, e.g. search hits or a git log as JSON (`CTXPRUNE_JSONROWS_FLAT=1`): the reader re-ran the
    search instead of answering, +7.4 [+3.7, +12.0] points of re-fetches (+12.0 with thinking);
  - `numbered`, the unpadded `cat -n` gutter (`CTXPRUNE_NUMBERED=1`): +5.1 [+0.9, +10.2] points of re-fetches.
- **Local / self-hosted endpoints:** no `jsonrows` (thinking models spent +139 to +407 tokens per lookup on nested
  records, against ~176 prompt tokens saved).
- **Cross-turn dedup by model.** It is on for `claude-opus-*` served by Anthropic and off for everything else, decided
  once per session. `CTXPRUNE_DEDUP=0|1` overrides it.
  - Claude Opus 5.5 passed the dedup comprehension probe (−0.2 [−1.8, +1.3] points).
  - The open models failed it (Qwen3.8: −7 to −21 points).
- **First-render pins.** The proxy and the plugins' request-time dedup keep serving the bytes they first sent for each
  result, so an upgrade or a settings change never re-renders history the provider has cached. The pins live in a
  bounded sqlite store.
- **Same bytes on every OS and Python.** CI checked Windows, macOS and Ubuntu × Python 3.9–3.13.
- **Opt-in flags:** `CTXPRUNE_BLOCKREF`, `CTXPRUNE_PREFIX`, `CTXPRUNE_JSONROWS_EXT`, `CTXPRUNE_READ_TRIM`, and the four
  above. Each is off
  because a comprehension probe on open models found a cost; see the README.
- **Lossy presets:** `balanced` and `aggressive` (experimental; the model weights are not published yet).
  - They keep a recovery file for every dropped range and leave file views whole.
  - A refetch guard in the Claude Code hook and the proxy falls back to `safe` for a while when the agent re-fetches
    what was dropped.
  - Codex, dsh, OpenCode and Pi run `balanced` as `safe` unless `CTXPRUNE_FORCE_LOSSY=1`.
- **Security fixes** from a red team and a runtime audit, listed in [docs/SAFETY.md](docs/SAFETY.md):
  - forged-marker escaping;
  - private state files;
  - bounded parsing;
  - a kill switch in every integration, including the proxy.

### Changed
- **License: 0BSD** (0.2.1 was Apache-2.0).
  - The optional line-scoring model weights are not 0BSD and are not part of the package. They are a fine-tune of
    mmBERT-small (MIT) trained on data that includes CC-BY-4.0 sets (SWE-rebench-openhands, Nemotron-SWE).
  - The weights are not published yet; their license and attributions will be on the Hugging Face model card
    when they are. See
    [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
- **The Claude Code hook** now also matches `PowerShell` and `Grep`. Its timeout went from 60 s to 30 s, because the
  default no longer loads a model.
- **Cross-turn dedup** was on for every model in pre-release builds of 0.3.0. It is now on for Claude Opus only
  (see above).
- **Hermes plugin:** each call has a time budget (`CTXPRUNE_BUDGET`, default 2 s). After that, Hermes keeps the
  original output.
- **Release gate:** the PyPI publish job (trusted publishing, self-hosted Linux runner) runs only after the tests, the
  install e2e and identical determinism digests on Python 3.9–3.13 pass on the tagged commit. macOS and Windows were
  last checked in CI at runtime a4bd87e (all 15 cells identical); they are not part of this release's gate.

### Kept from 0.2.1 (same behaviour, same outputs)
- **The Python API:** `ctxprune.prune()`, `Compressor`, `DEFAULT_MODEL`, loaded lazily so the hooks never import them.
- **The LangChain and LlamaIndex integrations:** `ctxprune.integrations.CtxpruneDocumentCompressor` and
  `CtxpruneNodePostprocessor`, and the `langchain`, `llamaindex`, `torch` and `onnx` extras.
- **`ctxprune FILE` / `some_command | ctxprune`** with `--keep` / `--protect` / `--keep-structure` / `--stats`.
- **The 0.2.1 `ctxprune serve`** (HTTP `POST 127.0.0.1:8719/compress`). With `--model-dir DIR`, `ctxprune serve` is
  the 0.3 line-model server for the lossy presets instead.
- **`ctxprune install|uninstall claude-code --scope user|project|local`.** project/local write the project's
  `.claude/settings.json` / `settings.local.json`, as in 0.2.1, and stay out of the user-level install manifest.
- **The dependencies** (onnxruntime, tokenizers, numpy, huggingface_hub), so a fresh `pip install ctxprune` still
  gives a working `prune()`. The hooks and plugins don't import them.
- Checked against 0.2.1 on the same inputs: `prune()` (16 cases), `Compressor`, both integrations, the four CLI forms,
  `--stats` and `serve` give byte-identical output.

### Removed
- **`CTXPRUNE_RATE`** in the Claude Code hook. The hook is lossless by default now; `CTXPRUNE_KEEP` sets the keep
  ratio of the lossy presets. `CTXPRUNE_MIN_CHARS` now applies only to them.

## Migrating from 0.2.1

**If you used `ctxprune install claude-code`:**
- Run `uv tool upgrade ctxprune` (or `pipx upgrade ctxprune` / `pip install -U ctxprune`), then
  `ctxprune install claude-code`.
- The 0.2.1 hook calls `ctxprune hook claude-code`, which keeps working after the upgrade, but its matcher stays
  `Bash|mcp__.*` until you re-run `install`. Re-running replaces the old ctxprune hook entry with the new one.
- After that, `ctxprune upgrade` handles future upgrades, including rewriting the integrations.

**If you used the 0.2.1 Claude Code plugin** (`riceharvest/ctxprune`, `claude-code-plugin/`):
- Its hook script runs `ctxprune hook claude-code` if `ctxprune` is on your PATH, else
  `uvx --from "ctxprune>=0.2.1" ctxprune hook claude-code`.
- Once 0.3.0 is on PyPI, that `uvx` range resolves to 0.3.0. **So plugin users without a local install move from the
  0.2.1 lossy model to the 0.3.0 `safe` hook automatically**, with no action on their side.
- What they get is lossless, with no model download, but the old plugin's matcher doesn't include `Grep` or
  `PowerShell`, so those stay unhooked until you update the plugin.
- If you have 0.2.1 installed on your PATH, the plugin keeps running 0.2.1 until you upgrade it.
- This release updates `claude-code-plugin/` itself (matcher `Bash|PowerShell|Grep|mcp__.*`, `uvx` pinned to
  `ctxprune>=0.3,<0.4`). Run `/plugin marketplace update ctxprune` to get it. The no-pip alternative is
  `/plugin install ctxprune-safe@ctxprune`.

**If you relied on the 0.2.1 lossy compression:**
- 0.3.0's default saves fewer tokens than 0.2.1 at `--keep 0.5`, but it doesn't drop content.
- The lossy replacement is `--preset balanced` with `ctxprune[model]` and `ctxprune serve --model-dir DIR`. Its
  weights are not published yet.
- In my agent-session measurements, `balanced` did not lower cost reliably on most harnesses
  ([docs/BENCHMARKS.md](docs/BENCHMARKS.md#6-presets-the-lossy-balanced-preset)).

**If you used the Python API, the file/pipe CLI, `ctxprune serve` or the LangChain/LlamaIndex classes:** nothing to
do. They work as in 0.2.1, with the same model and the same outputs.

**Kill switch:** `CTXPRUNE_OFF=1` works as before, in the environment or inside one command.

## 0.2.1

Token-level pruning model (mmBERT-small fine-tune) for files, pipes, Python, LangChain/LlamaIndex and a Claude Code
hook/plugin. Apache-2.0.
