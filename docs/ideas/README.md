# Token-saving ideas for ctxprune

A long list of hypotheses for saving more tokens, filtered to what fits ctxprune's scope. **None of it has been
built into ctxprune or measured on real captures.** Treat every item as something to test, not a result. The 222 lossless
(`L`) ideas and the 125 shortlisted rule (`R`) ideas additionally have a runnable gate (reference prototype, fixtures, oracle):
see [`gate/`](gate/README.md). It shows an idea keeps what it promises on synthetic input; it does not show real savings, and
it found that many ideas save less than their finders claimed.

## Scope filter

An idea is kept only if it works:

1. **in all harnesses**: no dependence on one agent's hooks, plugins, config or memory files, or its private log
   format. It may use anything every harness sends to the model API (messages, tool calls and results, system prompt,
   tool schemas);
2. **with all models**: no dependence on one vendor's model, tokenizer or API feature (cache breakpoints, thinking
   tokens, a runtime token-count API);
3. **with cloud models used through an API** (Claude/GPT style) **while running locally**: no runtime cloud call, no
   API key, no GPU, no control over the model server.

## Files

| File | What it is |
|---|---|
| [`shortlist.md`](shortlist.md) | The 159 in-scope ideas the finders rated medium or large. Start here. |
| [`ideas.md`](ideas.md) | All 1,121 kept ideas by category, with a test for each. ★ marks the shortlist. |
| [`dropped.md`](dropped.md) | The 342 ideas filtered out (harness-specific 268, off-scope 37, model-specific 33, cloud 4), with reasons. |
| [`ideas.jsonl`](ideas.jsonl) | Source of truth for all 1,463 ideas. |
| [`build.py`](build.py) | `python docs/ideas/build.py` regenerates the three markdown files from `ideas.jsonl`. |
| [`gate/`](gate/README.md) | Runnable tests for the 222 lossless and 125 shortlisted rule ideas: `python docs/ideas/gate/run.py [id ...]`. Each idea's `Run:` line in `ideas.md` shows its command. |

`ideas.jsonl` records for gated ideas also carry `check`, `check_kind` and `check_loses`, written by
`python docs/ideas/gate/run.py --sync` (do not hand-edit).

Status values in `ideas.jsonl`: `scope` (877), `eval` (218: measurement, benchmark and safety tooling), `conditional`
(26: need the tool call rewritten before it runs, so they only work where something can intercept calls), `dropped`
(342). Ids #1-#117 are the original hand-written seed list; #118-#1403 came from the generation loop; #1404-#1463 are 60 lossless
ideas added later together with their gate cases.

## How it was made, and how far to trust it

- 117 seed ideas, then 14 rounds of 9 independent LLM idea-finders (each with a different lens and sub-focus), merged
  and deduplicated by an LLM after every round. The loop stopped at its round cap while still finding ~80-100 new
  ideas per round, so the list is not exhausted. Duplicate detection was a word-overlap check plus the merge agent;
  near-duplicates worded differently may remain.
- The scope filter was a single LLM pass per idea against the rules above, with no second opinion. A few calls are
  judgment calls (MCP-only ideas are kept and flagged `MCP`; call-rewriting ideas are `conditional`).
- #1404-#1463 (round 15) were written by six themed agents, each also writing the idea's gate case; they were screened against
  all 1,403 earlier ideas with a TF-IDF cosine check plus a manual read of every title.
- Text like "Measured" or "Verified here" is a quick check an idea-finder ran while writing; it was not reproduced.
  "Reportedly" means from memory or web search and unchecked. The size estimates are guesses.
