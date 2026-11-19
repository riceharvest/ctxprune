# L4.1 in-process SDK wrappers (ctxprune.wrap_openai / wrap_anthropic / LiteLLMCallback)
Verdict: PASS on prefix stability; PARTIAL on latency gate.
- Cache-prefix stability: 100% (pinned per tool_call_id / tool_use_id / call_id; 100 repeat requests byte-identical, even when the
  caller's later content differs; inputs never mutated; stream=True and unknown shapes pass through untouched). tests/test_sdk.py, 5 pass.
- Overhead: pinned (cache-hit) path << 1 ms per request. A NEW tool result pays one lossless render once: ~7 ms warm, ~25 ms cold
  for a 7 KB pretty JSON. So "<1 ms" holds for steady state, not for the first sight of a large output.
  Follow-up: render in a thread / only when len > threshold, or accept the one-time cost.
- Safe-mode render = scripts/lossless.compress (JSON minify etc.: values round-trip via decode, bytes not identical).
- Fix vs draft: `_rt.lossless` import silently no-op'd in source checkouts; now uses glue.runtime_dir().
- Not done: real SDK mock-transport (httpx) tests, `<ctxprune>` span support, OpenRouter (use wrap_openai with base_url).
Distribution: app/agent builders on openai/anthropic SDKs via `pip install ctxprune`, README snippet; LiteLLM users via litellm.callbacks.
