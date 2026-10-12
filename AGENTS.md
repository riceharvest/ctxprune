# Setting up ctxprune (for coding agents)

ctxprune deletes low-value words from text before an LLM reads it. It runs locally on CPU, needs no API key,
and downloads a 143 MB int8 model from Hugging Face on first use (`~/.cache/huggingface`).

## Pick the setup that matches what the user asked for

**Claude Code: compress large tool outputs automatically**

```bash
pip install ctxprune            # or: uv tool install ctxprune
ctxprune install claude-code    # writes a PostToolUse hook to ~/.claude/settings.json; pre-downloads the model
```

Use `--scope project` (`.claude/settings.json`) or `--scope local` (`.claude/settings.local.json`) to limit it to
one repo. Remove with `ctxprune uninstall claude-code --scope <same scope>`. Takes effect in new sessions.

Check it works: in a new session, have Claude run
`python3 -c "print('the build step finished with a warning about the cache directory being slow ' * 120)"`;
Claude should see a note starting "ctxprune compressed this Bash output". (Output that is nearly all numbers or IDs,
like `seq 1 3000`, is left alone because those are always kept.) Originals are saved in `~/.cache/ctxprune/outputs/`.

Tuning (environment of the Claude Code process): `CTXPRUNE_RATE=0.5` (fraction kept), `CTXPRUNE_MIN_CHARS=6000`.
Optional speed-up: keep `ctxprune serve` running (localhost:8719); the hook then skips the ~2 s model load.

Saved originals in `~/.cache/ctxprune/outputs/` are raw tool output (may contain secrets) and are never deleted
automatically; tell the user, and clear with `rm -rf ~/.cache/ctxprune/outputs`. The plugin install path does not
pre-download the model: run `python -c "import ctxprune; ctxprune.prune('x')"` once so early outputs get compressed.

**Shell / scripts**

```bash
ctxprune input.txt --keep 0.5 --stats
some_command 2>&1 | ctxprune --keep 0.33 --protect   # --protect: never drop IDs, numbers, paths
```

**Python**

```python
import ctxprune
ctxprune.prune(text, keep=0.5, protect=False)
```

**LangChain / LlamaIndex**: `from ctxprune.integrations import CtxpruneDocumentCompressor` (LangChain document
compressor) or `CtxpruneNodePostprocessor` (LlamaIndex node postprocessor); both take `rate` and `force_protected`.

## Things to tell the user

- It is lossy: at 50% kept, a reader LLM answered 68.8% of questions on our agent-text set vs 91.8% uncompressed.
- It is weaker on source code and non-English prose; do not use it on files that will be edited.
- Nothing leaves the machine; there is no telemetry.
