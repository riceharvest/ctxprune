#!/usr/bin/env python3
"""Registry of texts a native ctxprune integration already handed to a harness (live canary 2026-10-17: hook + proxy
stacked made the proxy escape the hook's own markers, so the model saw `\\[ctxprune: …` as if the tool had printed it).

The native side (Claude Code hooks, ctxprune_compress.run for Pi / OpenCode / dsh / Hermes, the Hermes view-safe
path) calls record(text) on every rewritten output; ctxprune_proxy calls seen(text) on each tool result and forwards a
recorded one untouched. A sha256 per text, one empty file per hash under <state>/emitted/: no token cost, nothing in
the text, and tool output can't forge an entry (it would need write access to the state dir; checked live: the text
Claude Code puts in tool_result is byte-identical to the hook's updatedToolOutput). Both sides must share the state
dir (CTXPRUNE_STATE, default ~/.cache/ctxprune). Fails open: any error -> record does nothing, seen() is False (the
proxy then processes the text again, which is still lossless, only with escaped markers).
Files older than KEEP_DAYS are pruned now and then.
"""

from __future__ import annotations

import hashlib
import os
import random
import time

KEEP_DAYS = 14


def _dir(state: str | None = None) -> str:
    return os.path.join(state or os.environ.get("CTXPRUNE_STATE") or os.path.expanduser("~/.cache/ctxprune"),
                        "emitted")


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def record(text: str, state: str | None = None) -> None:
    try:
        if not isinstance(text, str) or not text:
            return
        d = _dir(state)
        os.makedirs(d, mode=0o700, exist_ok=True)
        p = os.path.join(d, _key(text))
        if not os.path.exists(p):
            with open(p, "a"):
                pass
        if random.random() < 0.01:
            prune(state)
    except Exception:
        pass


def seen(text: str, state: str | None = None) -> bool:
    try:
        return isinstance(text, str) and bool(text) and os.path.exists(os.path.join(_dir(state), _key(text)))
    except Exception:
        return False


def prune(state: str | None = None, days: float = KEEP_DAYS) -> None:
    try:
        d, cut = _dir(state), time.time() - days * 86400
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.getmtime(p) < cut:
                os.remove(p)
    except Exception:
        pass


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        assert not seen("a\nb", td)
        record("a\nb", td)
        assert seen("a\nb", td) and not seen("a\nb\n", td)
        prune(td, days=-1)
        assert not seen("a\nb", td)
    print("emitted self-test OK")
