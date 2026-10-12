# ctxprune

**Cut big tool outputs, logs and documents in half before your LLM reads them, without mangling IDs.**
A 140M-parameter model deletes the least useful words; everything it keeps is passed on byte for byte.
Runs on CPU in about 0.1 s per 500 tokens. Apache-2.0.

![ctxprune on a failing pytest run: struck-through words are deleted, IDs and code stay intact](https://raw.githubusercontent.com/riceharvest/ctxprune/main/assets/before-after.png)

At 50% of tokens kept, a reader LLM answers **51.5%** of SQuAD v2 + HotpotQA questions exactly from ctxprune's
output vs **36.5%** from [headroom](https://github.com/headroomlabs-ai/headroom)'s Kompress-v2 (400 questions,
95% CI of the gap +10.5 to +19.5), and **68.8% vs 53.4%** for LLMLingua-2-large on our agent-text set.
**[Try it in your browser](https://huggingface.co/spaces/darioooooo0o/ctxprune)** (nothing is uploaded).

## Quick start

```bash
pip install ctxprune
ctxprune build.log --keep 0.5          # or: some_command | ctxprune --keep 0.33 --protect
```

```python
import ctxprune
short = ctxprune.prune(tool_output, keep=0.5)    # protect=True never drops IDs, numbers or paths
```

## Which one do I use?

| You want to... | Use | |
|---|---|---|
| Shrink big Bash/MCP outputs inside **Claude Code** | `ctxprune install claude-code` | or the plugin: `/plugin install ctxprune --marketplace riceharvest/ctxprune` |
| Compress a file or a pipe from the shell | `ctxprune FILE --keep 0.5` | `--protect`, `--stats`, reads stdin |
| Call it from Python | `ctxprune.prune(text, keep=0.5)` | `Compressor` for batching and options |
| Call it from another language / avoid the model load | `ctxprune serve` | `POST localhost:8787/compress {"text": ...}` |
| Compress retrieved documents in **LangChain** | `CtxpruneDocumentCompressor` | see below |
| Compress retrieved nodes in **LlamaIndex** | `CtxpruneNodePostprocessor` | see below |
| Use it inside **LLMLingua** | `PromptCompressor(model_name="darioooooo0o/ctxprune-small", use_llmlingua2=True)` | needs [LLMLingua#261](https://github.com/microsoft/LLMLingua/pull/261) |
| Run the raw model (ONNX / transformers / transformers.js) | [model card](https://huggingface.co/darioooooo0o/ctxprune-small) | label 1 = keep |

Agents setting this up for a user: see [AGENTS.md](https://github.com/riceharvest/ctxprune/blob/main/AGENTS.md).

## Claude Code

```bash
pip install ctxprune && ctxprune install claude-code     # undo: ctxprune uninstall claude-code
```

This adds a `PostToolUse` hook. Bash and MCP outputs over 6,000 characters are compressed to about half before
Claude reads them, with identifiers, numbers and paths always kept. Claude gets a one-line note saying so, and the
full output is saved under `~/.cache/ctxprune/outputs/` so it can grep the original if a detail is missing.
Commands that print files Claude may edit (`cat`, `head`, `sed -n`, `git diff/show`) and the Read/Grep tools are
never touched, and a command containing `CTXPRUNE_OFF=1` is passed through. If anything fails, Claude gets the
original output. Settings: `CTXPRUNE_RATE` (0.5), `CTXPRUNE_MIN_CHARS` (6000). Each compressed call loads the model
(~2 s); run `ctxprune serve` in the background and the hook uses it instead.

As a plugin, without touching settings files (needs `ctxprune` on PATH, or `uv` so it can run via `uvx`):
`/plugin install ctxprune --marketplace riceharvest/ctxprune`.

## LangChain and LlamaIndex

```python
from ctxprune.integrations import CtxpruneDocumentCompressor    # LangChain
retriever = ContextualCompressionRetriever(base_compressor=CtxpruneDocumentCompressor(rate=0.5),
                                           base_retriever=base_retriever)

from ctxprune.integrations import CtxpruneNodePostprocessor     # LlamaIndex
engine = index.as_query_engine(node_postprocessors=[CtxpruneNodePostprocessor(rate=0.5)])
```

## How good is it, honestly

- Compression loses information. On our agent set, QA accuracy goes from 91.8% uncompressed to 68.8% at 50% kept.
  Use it where the token savings are worth that, e.g. long logs, test output, search results, retrieved docs.
- Weak spots: source code (the teacher rarely deleted code, so the model keeps most of it) and non-English web prose,
  where LLMLingua-2-large still does better.
- Our 378-question set is from our own training distribution, so the neutral SQuAD/HotpotQA comparison above is the
  fairer number. Per-source tables and caveats: [model card](https://huggingface.co/darioooooo0o/ctxprune-small).

By [@imdariotoo](https://x.com/imdariotoo).

---

## Reproducing the model

Why: LLMLingua-2 (Microsoft, Mar 2024) was trained only on meeting transcripts
under CC BY-NC-SA labels. On agent tool outputs it loses or mangles about half
of all identifiers (UUIDs, IPs, paths, versions) and breaks JSON.

## Pipeline

| step | script | where | notes |
|---|---|---|---|
| corpus | `scripts/build_corpus.py` | CPU, ~2 min | ranged parquet reads from 8 permissive sources; split per repo/server/site |
| labels | `scripts/label.py` | teacher LLM (`scripts/teacher_up.sh`) | deletion-only compression, LCS-aligned to atoms; cached |
| train | `scripts/train.py` | any torch device (we used an Intel Arc Pro B70) or CPU | token classifier, label 1 = keep (LLMLingua-2 convention) |
| quick eval | `scripts/eval_quick.py` | CPU | identifiers lost, JSON validity, char ratio vs LLMLingua-2 |
| QA eval | `scripts/eval_qa.py questions/answer` | teacher LLM | can a reader still answer from the compressed text |

`scripts/run_v1.sh` runs corpus and labels; `scripts/after_label.sh` runs everything after them.

## Data sources (all permissive)

SWE-smith trajectories (MIT), SWE-rebench OpenHands trajectories (CC BY 4.0),
Toucan-1.5M MCP tool calls (Apache-2.0), github-code-clean filtered to
MIT/Apache/BSD/ISC files, FineWeb-Edu and FineWeb-2 (ODC-BY), oasst2
(Apache-2.0). Every chunk records its dataset, license and source reference.
Teacher: Qwen3.8-27B GPTQ (Apache-2.0 outputs).

## Training more after an eval

Nothing has to be redone:

1. **More data, same mix.** Rerun `build_corpus.py` into the same directory
   with a larger `--total`. Sampling is deterministic, and dataset revisions
   are pinned in `chunks/revisions.json`, so every source file becomes a
   superset of the old one. To weight a domain up, add `--mix toucan_mcp=0.3`.
2. **Label only the new chunks.** Rerun `label.py`. The teacher cache is keyed
   by (prompt version, model, text), so old chunks come back from disk.
3. **Continue training** from the previous model:
   `train.py --data <dir> --init-from runs/v1-mmbert-small/final --out runs/v2-...`
   (or `--resume` to continue an interrupted run from its checkpoint).
   `--data` accepts several directories, to mix label sets.
4. **Re-run evals.** Question and answer caches mean only new contexts cost LLM calls.

Changing the teacher prompt bumps `PROMPT_VERSION` in `ctxprune/teacher.py`, which
invalidates the cache on purpose.

## Tests

`uv run pytest`: atom round-trips, identifier atoms, CJK handling, LCS
alignment, chunk budgets, split stability and cache resume.
