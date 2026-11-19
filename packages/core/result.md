# L1.6 native package screen

Date: 2026-11-19

## Decision and distribution

The native package seam is `@ctxprune/core` for JavaScript/TypeScript integrations: Pi, OpenCode, dsh, Cline, Vercel AI SDK, and LangChain.js can import it directly instead of spawning the Python hook. `compress` / `decode` are dependency-free; Node ONNX inference is an optional `onnxruntime-node` peer. This screen does not establish it as a drop-in replacement.

## Cheapest screen and gates

The existing Python determinism harness was run as the baseline: `python tests/packaging/determinism.py --out /tmp/ctxprune-py-fixtures.json`. It produced 103 output cases with digest `d651a3d5eda31279f046a4a208828c9ffd5ea389cf7943ceaa5bf3525772b7bf` and no reported failures.

An in-process Node smoke/latency screen ran with Node v22.22.2 using `node --experimental-strip-types`. Marker escape/decode round-trip passed. Across 7 batches of 10,000 calls on a 2,100-character ordinary text input, median was 0.000181 ms/call and the slowest batch (reported as the sampled p95 upper bound) was 0.001096 ms/call. This measures only the implemented JS safe path; it is not comparable to Python fixture parity or an ONNX end-to-end request.

**Kill gate: failed / not demonstrated.** The current package implements only forged-marker escaping and JSON whitespace compaction; the Python safe pipeline additionally contains command-aware JSON handling, ANSI/CR treatment, hit grouping, deduplication, numbered views, paths, tables, and other lossless2 transforms. Consequently it cannot produce byte-identical output for the full 103-case suite. No ONNX model artifact/tokenizer was available for the Node adapter, so the `<5 ms p95` gate for ONNX inference is also not measured. Do not advertise native parity or route existing integrations through this package yet.

## Artifacts

- `packages/core/src/index.ts`: TS API and lazy ONNX Runtime Node adapter (expects pre-tokenized IDs).
- `packages/core/package.json`, `tsconfig.json`, `README.md`.
- Python baseline fixture report: `/tmp/ctxprune-py-fixtures.json` (outside the repository).

## Claim allowed

The current code is a native, dependency-free starter seam with a measured sub-5-ms marker/JSON subset. It does not meet L1.6's full determinism or ONNX latency kill gate and is not a release-ready replacement for the Python integration path.
