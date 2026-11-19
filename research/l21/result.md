# L2.1 result: source skeleton and symbol reader

## Result

Implemented two opt-in, read-only tools in `ctxprune mcp`: `read_skeleton` and `read_symbols` (`#206`, `#305`). Python symbols use the standard-library AST and report decorator-inclusive, 1-based inclusive extents. Other languages use a conservative declaration-line outline. `read_symbols` ranks signatures and one-line docstrings against a query using a small BM25-style lexical score, then returns the selected source ranges verbatim. No model, dependency, network call, or edit operation is involved.

## Cheapest screen

Ran a CPU screen over all 9 `src/ctxprune/*.py` files longer than 300 lines in this checkout via `~/smallest-ai-lab/bin/lab-cpu`. Aggregate whitespace-word count fell from 24,496 to 3,628 (**85.19% fewer whitespace-separated words** (whitespace-word proxy; NOT tokens, no tokenizer was run), a proxy rather than tokenizer-measured tokens). Per-file reductions ranged from 75.5% to 92.0%. Every emitted Python symbol range was in bounds and included its declared name (9/9 files; 228 declarations). A focused query screen selected `parse_data` for “parse input records” and returned its complete L2-L4 source verbatim.

This passes the cheap structural/savings screen, but is not the specified 50-task SWE-bench comprehension evaluation: no held-out task lookup set or local reader probe was run. The 60% figure is therefore a CPU word-count proxy, not a validated token or agent-task result. The exact range property is guaranteed for Python AST extents in this screen; regex declaration-line ranges for non-Python languages need separate fixtures before claiming cross-language accuracy.

## Distribution plan

Users are coding agents configured with ctxprune's existing stdio MCP server. They discover the tools through the existing `ctxprune mcp` `tools/list` seam after upgrading the package from the existing PyPI/uv install channel. `read_skeleton` is for surveying source; `read_symbols` is for query-focused navigation. Both are opt-in MCP calls and leave existing hook `Read` behavior untouched.

## Verification

- CPU screen command used `lab-cpu`; result above.
- Direct MCP `call_tool`/range checks and source-query expansion were exercised during implementation.
- No reader GPU job was run; comprehension remains unverified.
