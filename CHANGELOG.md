# Changelog

## Unreleased (0.5 lab, v0.5-lab integration)

Every item is opt-in; 0.4.0 entry-point output is unchanged.

- `ctxprune mcp-wrap -- <server cmd>` (T1.8): stdio MCP proxy applying the lossless rules to `tools/call` text results.
- `ctxprune mcp-gateway`, `ctxprune.wrap_openai` / `wrap_anthropic` / `LiteLLMCallback`, `search_saved`, `read_skeleton` /
  `read_symbols` (MCP tools listed only with `CTXPRUNE_MCP_EXTRA_TOOLS=1`). See docs/lab/ and research/ result files for
  the numbers and what is unmeasured.

## 0.4.1 (2026-11-19)

One-line agent install. No change to compression, the 0.2 / 0.3 API or the lossless output.

- `ctxprune wrap claude|opencode|hermes|pi|codex|--all` installs the lossless `safe` integration and prints the diff of
  the agent's config files; `ctxprune unwrap AGENT|--all` removes it. Same code, manifest and backups as `install` /
  `uninstall`, which are unchanged.
- `uvx ctxprune wrap claude` works: from a temporary uvx environment it first runs `uv tool install ctxprune==VERSION`
  (pipx if there is no uv), then runs `wrap` from the permanent copy. `ctxprune install` still refuses to run from uvx.
- The Claude Code marketplace plugin (`riceharvest/ctxprune`) is version 0.4.1: its uvx fallback pin is widened to
  `ctxprune>=0.3,<0.5` (was `<0.4`), so plugin users without a local install run the 0.4 lossless hook. On the smoke
  payloads (grep output, repeated lines, JSON rows, small outputs) the hook's bytes are identical to 0.3.2's, both
  from the 0.4.1 wheel and through uvx.
- The Pi, OpenCode and Hermes per-output scripts now hold changed first-render pins in their context store (the
  residual T2.1 rule the Codex proxy already had): a pin that changed a result's bytes is never evicted. No output
  change; the 0.4.0 note that these stores "do not call the pin hold yet" is superseded.
- Docs: cross-turn dedup is described as decided by model **per request** (what the code already did).
- Re-checked against a pristine 0.3.2 install with the release probes (`probe_api.py`, `probe_cli.sh`): the 0.2 / 0.3
  API, the CLI (file, pipe, dash, bare stdin), `--stats` and `serve` `POST /compress` are byte-identical; only the
  version string differs. `install` / `uninstall --scope project|local` against a real 0.2.1 install: all probe
  checks pass. The one deliberate 0.4 behaviour change (`ctxprune mcp` `read_pruned` `protect="auto"`) is unchanged
  from 0.4.0 and listed there.

## 0.4.0 (2026-11-19)

0.4 is the lossy release: a second token-pruning engine and new entry points for `prune()`. The lossless `safe`
stack that the Claude Code hook, the Hermes / Pi / OpenCode / dsh plugins, the MCP server and the Codex proxy use is
unchanged in its output (two safety fixes below). The 0.2 / 0.3 API returns the same bytes as 0.3.2.

### Headline (held-out, with its limits)

- **ctxprune 0.4 in query mode is level with LLMLingua-2-large on held-out LongBench. It does not beat it.** 270 items
  never used to tune 0.4 (30 per task, 9 English tasks), Gemma-4-31B reader, same token budget as LLMLingua-2-large:
  **+2.36 [-0.70, +5.51]** 9-task macro, +0.15 [-2.76, +3.42] without `passage_retrieval_en`. The question-aware ctxprune
  mode is compared with a question-blind LLMLingua-2, and no comparison with LongLLMLingua was run.
- **21 of the 270 held-out items share a source document with a dev-set item** (18 of 30 `qmsum`, 2 `multifieldqa_en`,
  1 `qasper`; no item is identical). Without them (n=249): +1.93 [-1.14, +5.27] macro9, -0.32 [-3.42, +2.72] macro8.
- **Not shown:** a zero-model BM25 selector at the same budget is level with the query mode (+0.71 [-1.77, +3.40]), so the
  neural model's added value over lexical query matching on LongBench is not shown. Query-blind mode is level on 9 tasks
  and **worse** without `passage_retrieval_en` (-3.73 [-6.52, -1.14]; `hotpotqa` -15.8 [-28.4, -4.8]). The 9-task lead
  comes from `passage_retrieval_en` (+20.0) and `multifieldqa_en` (+8.5); `hotpotqa` (-6.1) and `qasper` (-2.7) are lower.
- The review of this result (a different model family, from raw files) is **WEAKER**: the numbers reproduce, and the
  wording above is what it allowed. The pre-registered claim "beats LLMLingua-2-large" failed (CI lower bound below 0),
  so it is not made. On a 90-item subset the query mode is +8.08 [+2.13, +14.14] over LLMLingua-2-base, but that is a
  query-aware method against a query-blind one and the 8-task interval includes 0 (+5.34 [-0.20, +11.20]).
- Dev-set only (the 0.4 code was tuned on these 256 items): query mode +5.3 [+2.0, +8.5] over LLMLingua-2-large, which
  did **not** replicate held-out. The dev set also shows **losses**: `triviaqa` 83.8 vs 90.4, `lcc` 8.7 vs 14.6 (0.3.2),
  `passage_retrieval_en` is a lexical shortcut. All of it is in [docs/BENCHMARKS.md §9](docs/BENCHMARKS.md#9-longbench-rematch-against-llmlingua-2).

### What is new

- **`ctxprune.prune(text, keep, protect="auto", query=..., mode=..., unit=..., clean_web=...)`**: the v0.4 engine, selected
  whenever a call uses something 0.3.2 did not have. `protect="auto"` keeps IDs, numbers and paths in technical text and
  prunes prose; `protect="ids"` keeps identifiers only; `protect=True` is still the strictest.
- **Query-aware pruning** (`query=`) spends the budget on the parts of the text that match the question.
- **`compress_many(docs, keep, query=, scores=)`**: retrieved chunks under one shared budget with overlap removed.
  RAG QA (Gemma-4-31B, n=30, dev-set): F1 **75.3 vs 72.5** for flat 0.3.2 pruning, at slightly fewer tokens.
- **`score()` / `prune_scored()`**: score a text once, slice it per query without running the model
  (0.30 ms per 10 chunks; 1.05 ms before the anchor plan was cached, same output).
- **`mode="json"`** shrinks JSON values only (`mode="auto"` tries the lossless rules first, then JSON values); on 40 MCP-style results the output was valid JSON every time
  (95.8% scalar recall at 0.5 kept; 0.3.2 gave 0.0% valid).
- Smaller engine changes: negation anchors, intra-document repetition decay, paragraph and header preservation,
  `clean_web=True`, a lexical two-stage cascade (`fast=True`, opt-in) and overlapping windows (`overlap=`, opt-in,
  off by default; it did not clear its gate).
- `ctxprune.compat.PromptCompressor`: the LLMLingua-style class on the new engine.
- Opt-in `Compressor(rep_decay="prose"|..., fewshot="pin"|"noquery"|"both")`, off by default (output unchanged when
  unset). Dev-set only: triviaqa 92.67 (was 83.78) and lcc 14.69 (was 8.69; one item decides it, and on the 30-item
  extension 11.17 vs 6.97 with the interval including 0; 0.3.2 scores 18.00). They stay off by default because of
  that, and `lcc` is still a known regression of the 0.4 engine on source code.
- Measured on the dev set or offline proxies, none of it held-out (BENCHMARKS §10): 96.2% of facts kept on tool output
  and prose with `protect="auto"` (0.3.2 default 62.1%, but **0.3.2 with `protect=True` already keeps 97.7%**, so that
  gain is the `protect` setting, not the engine). **Negation probe, n=40: `polarity_safe` is 80.0% for both 0.3.2 and 0.4:
  no improvement.**

### Compatibility

- **The 0.2 / 0.3 API returns the same bytes as 0.3.2.** `prune(text, keep[, protect])`,
  `Compressor().compress(text, rate=..., force_protected=bool, keep_structure=..., unit="chars"|"tokens"|callable,
  threshold=..., measure=...)`, `ctxprune FILE --keep [--protect]`, `ctxprune serve` `POST /compress` without the new
  fields, and `CtxpruneDocumentCompressor` / `CtxpruneNodePostprocessor` with their defaults run the frozen 0.3.2 path
  (`ctxprune/_v032.py`). Checked against a pristine 0.3.2 install with the release probes (`probe_api.py`,
  `probe_cli.sh`) and 132 further call shapes; golden 0.3.2 outputs are in `tests/golden/`.
- The v0.4 engine runs when a call asks for something 0.3.2 did not have: `protect="auto"|"ids"`, `query=`, `mode=`,
  `clean_web=`, `fast=`, `unit="tokens_est"|"o200k"|"cl100k"`, `Compressor(engine="v04")`, or a constructor option that
  did not exist before (`rep_decay`, `fewshot`, `overlap`); there an unset `protect` means `"auto"`. `compress_many`,
  `score`, `prune_scored`, `ctxprune.compat.PromptCompressor` and `ctxprune mcp` always use it.
  LangChain / LlamaIndex classes: `force_protected="auto"` turns on query-aware, shared-budget pruning.
- `Compressor(engine="auto"|"v04"|"v032")` pins an engine. `prune(..., protect=None, unit=None)` and
  `Compressor.compress(force_protected=None, unit=None)` now default to "unset" (was `False` / `"chars"`); an unset value
  keeps the 0.3.2 meaning unless a new option is used.
- **One deliberate behaviour change: `ctxprune mcp` `read_pruned` now defaults to `protect="auto"` (it was `true` in
  0.3.2)** and runs the v0.4 engine. Pass `protect=true` for the 0.3.2 behaviour.
- Other differences that are not output: new constructor keywords and signatures, the legacy CLI `--help` text, and ONNX
  threads default to `min(cpu, 4)`.

### Lossless `safe` stack

- Output unchanged: the lossless suite, the hook / plugin / MCP smoke tests and the Codex-proxy tests pass on this release
  (release gate, run on the tagged tree).
- **Safety (two residual holes closed).** The proxy now clears its per-request dedup state at the start of every request:
  a request that failed during setup (and went out unchanged, fail-open) could leave its Opus dedup session in a
  thread-local, so the next request on the same keep-alive connection (for example a Sonnet request) got dedup
  references. For Anthropic-format traffic the render store also never evicts a pin that changed a result's bytes (not
  by age, not over the size cap, not at open): losing one makes a resumed conversation's history differ from what was
  sent, which is a 400 on the Claude 5.x thinking models. Docs now say what the code already did: cross-turn dedup is
  decided per request by the current model. The Hermes, Pi and OpenCode context stores do not call the pin hold yet.

## 0.3.2 (2026-11-18)

- **`ctxprune mcp`**: a stdio MCP server for clients without tool-output hooks (Claude Desktop, Cursor, Cline, ...).
  `read_compact` reads a file through the lossless `safe` pipeline; `read_pruned` token-prunes it with the 0.2 model,
  identifiers kept by default. Stdlib JSON-RPC, no MCP SDK. Listed as `io.github.riceharvest/ctxprune` in the MCP
  Registry (`server.json`).
- README / model card: use `protect=True` for token pruning of tool output and logs. Measured on 120 held-out chunks
  against LLMLingua-2 in [docs/bench/llmlingua_facts.md](docs/bench/llmlingua_facts.md).
- Note: `ctxprune mcp` is now a subcommand, so a 0.2-style `ctxprune mcp` on a file literally named `mcp` needs
  `ctxprune ./mcp`.

## 0.3.1 (2026-11-18)

Docs only; the code and every output are the same as 0.3.0.
- README: a Credits section. The GPU runs behind this release's benchmarks ran on CloudRift RTX 5090s through their
  AI Builder Grant.
- docs/BENCHMARKS.md: says which sections ran on CloudRift GPUs and which ran locally.

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
