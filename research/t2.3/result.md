# T2.3 — Passive local harm canary: screen result

**Verdict: no-go for the 0.5 implementation on the available 30-day screen.** The requested ≈0 false-positive gate cannot be measured with the existing local evidence. Do not enable an auto-disable rule from these counters.

## Screen and evidence

The cheapest screen was an audit of the existing local stats schema and cached 30-day replay outputs; no compute, model, or network was used.

- `ctxprune stats` reads a counts-only log: timestamp, harness, tool name, before/after/dedup character counts, and stage. It intentionally contains no output text or command arguments.
- Rewrite records have no stable event/session identifier that can be joined to subsequent tool calls.
- The available 30-day replay artifact (`results/lossless-each/real_logs_30d.json`) is aggregated by transform. It has no per-event pairing, false-positive labels, or counts for repeated commands, missing-path reads, Edit `old_string` misses, or tool errors.
- The existing `research/signal` replay corpus measures next-action behavior for lossy prompt compression. Its rows are not observations of lossless output rewrites followed by actual tool failures, so using it would answer a different question.

Therefore the number of eligible rewritten outputs, matched unrewritten controls, and false positives is **not measurable (n/a)** from these artifacts. Treating absent events as zero would be an invalid pass. The ≈0 false-positive gate is not met, so implementation and automatic rule disabling stop here.

## What would make a future screen measurable

A new opt-in local event stream needs to link a rewritten output to the following three tool calls while recording only minimized signals: event/session IDs, tool kind, a keyed command fingerprint, path-read outcome category, Edit miss category, and generic tool-error category. It should not persist raw commands, paths, output text, or `old_string`. A replay dataset must provide matched rewritten/unrewritten sequences and adjudicated labels before any threshold or auto-disable behavior is added.

## Distribution

If a later card passes the screen, expose aggregate counts through the existing `ctxprune stats` CLI and its local JSON output. That is the existing user-facing seam; keep the event data local and opt-in. No external collection channel is needed or appropriate for this canary.
