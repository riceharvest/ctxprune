# L5.2 — Untrusted Unicode and prompt-injection hygiene

## Hypothesis and decision

A narrow deterministic sanitizer can remove the registered invisible carriers without changing supported multilingual shaping. The screen passed; the sanitizer remains opt-in until a web/MCP boundary opts into it.

## Screen and result

- Attack fixtures: 137/137 Unicode tag-plane codepoints (U+E0000–U+E007F), 5/5 bidi embedding/override controls (U+202A–U+202E), 4/4 bidi isolates (U+2066–U+2069), and one multiline HTML comment payload removed.
- Multilingual fixture: exact string equality for ZWJ family emoji, Hindi/Devanagari, Arabic with combining vowel marks, and Chinese; 0 changes.
- Metadata reports removed invisible-control count and removed comment count separately.
- Command: `python3 -m unittest scripts.test_lossless.DupGate`
- Result: 5 tests passed (including the existing duplicate-rule tests); `git diff --check` passed.
- Scope limitation: synthetic deterministic fixtures only; no captured web/MCP corpus or reader probe was run. This is the R1 cheap CPU screen, not a broad empirical attack-coverage claim.

## Implementation and distribution

Added `sanitize_untrusted(text) -> (text, metadata)` to `scripts/lossless.py`. Its intended distribution seam is the existing Python lossless stage used by hook integrations; a web/MCP adapter should call it only on identified untrusted external content and propagate the metadata as `[ctxprune: stripped N invisible control chars]`. It is opt-in at present; no current MCP wrapper is wired to it.

The sanitizer deliberately does not use the broader marker-escaping `INVISIBLE` class. It preserves ZWJ/ZWNJ, Arabic marks, variation selectors, and other shaping characters. It strips the specified tag block, bidi embeddings/overrides/isolate controls, and HTML comments. Other invisible characters are outside this experiment's attack set.

## Verdict

**Pass the stated kill gate on the exhaustive registered codepoint fixture and multilingual smoke fixture; continue as an opt-in API.** This does not establish that all prompt-injection attacks are neutralized. No model, network, GPU, or external service used.
