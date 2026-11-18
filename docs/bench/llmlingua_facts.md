# Token pruning: which facts survive? (ctxprune-small vs LLMLingua-2)

**Question.** After token pruning, how many of the things a reader needs verbatim are still there? That means IDs and
hashes, paths, versions, URLs, dates, emails and numbers of two or more digits.

**Data.** 120 held-out chunks (test split, 400–6,000 chars), 30 per type, from the v2 corpus:
- agent tool output: SWE-agent bash output (Kwai SWE-smith) and Terminal-Bench observations;
- MCP results: Toucan MCP tool calls;
- chat: oasst2;
- multilingual web prose: FineWeb-2.

**Method.** [`llmlingua_facts.py`](llmlingua_facts.py), measured 2026-11-18.
- LLMLingua-2 runs through stock `llmlingua` 0.2.2 (`compress_prompt([text], rate=r, force_tokens=["\n"],
  drop_consecutive=True)`) with Microsoft's base (mBERT) and large (XLM-R) checkpoints.
- ctxprune is `ctxprune.prune(text, keep=r)` with and without `protect=True`.
- A fact counts as kept if it appears verbatim in the output. Tokens are o200k.
- `items` picks the chunks with a fixed seed; `score` prints the table.

**Results** (facts kept / tokens kept):

| type | rate | LLMLingua-2 base | LLMLingua-2 large | ctxprune | ctxprune `protect=True` |
|---|--:|---|---|---|---|
| agent tool output (240 facts) | 0.5 | 72.9% / 58.9% | 94.6% / 52.9% | 45.8% / 52.1% | **99.6%** / 55.9% |
| MCP results (245) | 0.5 | 71.8% / 61.4% | 75.9% / 49.3% | 66.1% / 52.4% | **95.1%** / 54.9% |
| chat (27) | 0.5 | 92.6% / 48.5% | 96.3% / 49.7% | 77.8% / 50.4% | **100%** / 50.5% |
| multilingual web prose (93) | 0.5 | 81.7% / 55.3% | 91.4% / 51.2% | 89.2% / 53.2% | **98.9%** / 53.4% |
| agent tool output | 0.8 | 79.2% / 94.7% | 100% / 79.8% | – | 100% / 82.2% |
| MCP results | 0.8 | 72.2% / 97.9% | 91.8% / 78.1% | – | 98.4% / 81.1% |

**How to read it.**
- **Use `protect=True` on tool output, logs and anything with identifiers.** Without it, ctxprune drops more facts
  than LLMLingua-2 on agent output (45.8% kept). It never splits or re-spaces them, but it does delete them.
- With `protect=True`, ctxprune keeps more facts than LLMLingua-2-base everywhere, often with fewer tokens. Against
  LLMLingua-2-large the gap on agent tool output is small (99.6% vs 94.6%) and ctxprune keeps about 3 points more
  tokens.
- `rate` is not a hard budget for every tool: LLMLingua-2-base at 0.8 kept 95–98% of tokens on tool output.
- Facts kept is not reading accuracy. On QA from the compressed text, LLMLingua-2-large is better than ctxprune on
  non-English web prose (see the [model card](https://huggingface.co/darioooooo0o/ctxprune-small)). The chat row has
  only 27 facts.
