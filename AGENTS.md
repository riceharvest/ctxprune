# Setting up ctxprune (for coding agents)

ctxprune has two parts. Pick the one that matches what the user asked for.

- **Agent hooks (lossless):** rewrites big tool outputs in Claude Code, Hermes, Pi, OpenCode, dsh and Codex into a
  shorter form that keeps every value. Standard library only, no model, no network.
- **Token pruning (lossy; the 0.2 API plus the 0.4 query-aware engine):** deletes low-value words from text (prompts, retrieved documents, logs) with a
  140M model that runs on CPU and downloads once from Hugging Face (143 MB, `~/.cache/huggingface`).

Nothing leaves the machine in either part; there is no telemetry.

## Agent hooks

```bash
uv tool install ctxprune        # or: pipx install ctxprune (not uvx: install writes the interpreter path)
ctxprune install --all          # every supported agent found; or: ctxprune install claude-code
ctxprune doctor                 # checks each integration with a test call
```

- Claude Code only, one repo: `ctxprune install claude-code --scope project` (`.claude/settings.json`) or
  `--scope local` (`.claude/settings.local.json`). Remove with `ctxprune uninstall claude-code --scope <same scope>`.
- No pip: `/plugin marketplace add riceharvest/ctxprune`, then `/plugin install ctxprune-safe@ctxprune`. Use either
  the plugin or `ctxprune install claude-code`, not both.
- Takes effect in new sessions. Remove everything with `ctxprune uninstall --all` (original configs are backed up).
- Off switch: `CTXPRUNE_OFF=1` in the agent's environment or inside one command.
- `ctxprune stats` shows what it saved. Expect a few percent of tool-output tokens on coding work and up to ~9% on
  MCP-heavy work (README, "How much it saves").

## Token pruning

```bash
ctxprune input.txt --keep 0.5 --stats
some_command 2>&1 | ctxprune --keep 0.33 --protect   # --protect: never drop IDs, numbers, paths
```

```python
import ctxprune
ctxprune.prune(text, keep=0.5, protect=False)
ctxprune.prune(text, keep=0.33, protect="auto", query="the question")   # 0.4: query-aware; compress_many(docs, query=) for RAG
```

**LangChain / LlamaIndex**: `from ctxprune.integrations import CtxpruneDocumentCompressor` (LangChain document
compressor) or `CtxpruneNodePostprocessor` (LlamaIndex node postprocessor); both take `rate` and `force_protected`.

Things to tell the user about token pruning:
- 0.4 query mode on held-out LongBench is level with LLMLingua-2-large (+2.36 [-0.70, +5.51]), not better; see docs/BENCHMARKS.md section 9.
- It is lossy: at 50% kept, a reader LLM answered 68.8% of questions on our agent-text set vs 91.8% uncompressed.
- It is weaker on source code and non-English prose; do not use it on files that will be edited.
