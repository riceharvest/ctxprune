#!/usr/bin/env python3
"""View-safe lossless for JSON-wrapped file views (Hermes `read_file`, 18.1% of Hermes prompt tokens; reach study
2026-10-17: +1.48% of all Hermes prompt tokens saved). Code bytes stay exact:

  {"content": "     9|code\\n...", "total_lines": 120, ...}
    -> [ctxprune: unescaped JSON string field "content"; rest (field shown as null): {"content":null,"total_lines":120,...}]
       [cp:numbered w=6]   (only when the gutter is padded: Hermes' older format)
       9|code
       ...
       [/cp]

Only two rules: the json_unescape step of lossless.json_rules (sibling keys such as exit_code /
total_count survive in the header, which Hermes' compaction stubs regex-read) and the lossless2 `numbered` gutter rule.
Never dupref / hits / paths / dup on a view (an agent copies those lines into edits). Results that report a failure
(`error` set, `success: false`, `is_binary`) are left alone.

wrapper_unescape(text, rules)  -> new text or None (unchanged)
hermes_failure_text(text)      -> True when Hermes' generic _detect_tool_failure fallback would call this string a
                                  failure ('"error"' / '"failed"' in the first 500 chars, or a leading "Error").
                                  Unescaping code can create such a match ("error" in quotes was \\"error\\" before),
                                  and Hermes classifies the result AFTER transform_tool_result, so callers check it.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

VIEW_RULES = ("numbered",)


def _failed(obj: dict) -> bool:
    return bool(obj.get("error")) or obj.get("success") is False or bool(obj.get("is_binary")) \
        or obj.get("status") in ("error", "failed")


def unwrap(text: str, require_exit0: bool = False):
    """JSON object whose biggest string field holds multi-line text -> (header line, that text unescaped, failed).
    The header ("[ctxprune: unescaped JSON string field …; rest (field shown as null): {…}]") keeps the sibling keys.
    None when not that shape (or require_exit0 and exit_code != 0)."""
    try:
        if not isinstance(text, str) or not text.lstrip().startswith("{"):
            return None
        import lossless
        s = text.strip()
        obj = json.loads(s, object_pairs_hook=lossless._pairs)   # duplicate keys raise: not invertible, skip
        if not isinstance(obj, dict):
            return None
        if require_exit0 and obj.get("exit_code") != 0:
            return None
        new = lossless._unescape(obj, s)   # json_rules' unescape step, without its "shorter on its own" test:
        if new is None:                    # the caller judges the size after the body rules
            return None
        head, sep, body = new.partition("\n")
        if not sep or not head.startswith("[ctxprune: unescaped JSON string"):
            return None
        return head, body, _failed(obj)
    except Exception:
        return None


def wrapper_unescape(text: str, rules: tuple[str, ...] = VIEW_RULES, require_exit0: bool = False) -> str | None:
    """unwrap() + lossless2 `rules` on the text, for a successful result; None when not that shape, a failure, or not
    shorter. Marker-shaped lines in the text are escaped (audit H2) unless the text is one a ctxprune integration
    already produced (emitted.py: e.g. Hermes terminal output the plugin compressed before Hermes wrapped it)."""
    try:
        w = unwrap(text, require_exit0)
        if w is None or w[2]:
            return None
        head, body, _ = w
        from emitted import seen
        if not seen(body):
            from lossless import escape_markers
            body = escape_markers(body)
        if rules:
            from lossless2 import encode
            body, _st = encode(body, tuple(rules))
        out = head + "\n" + body
        return out if len(out) < len(text) else None
    except Exception:
        return None


def hermes_failure_text(text: str) -> bool:
    if not isinstance(text, str):
        return False
    head = text[:500].lower()
    return '"error"' in head or '"failed"' in head or text.startswith("Error")


def _self_test() -> None:
    from lossless import expand  # noqa: F401  (import check)
    from lossless2 import decode
    code = [f"def f{i}():\n    return {i}" for i in range(30)]
    lines = "\n".join(code).split("\n")
    padded = "\n".join(f"{i + 1:6d}|{ln}" for i, ln in enumerate(lines))
    raw = json.dumps({"content": padded, "total_lines": len(lines), "file_size": 999, "truncated": False})
    out = wrapper_unescape(raw)
    assert out and out.startswith('[ctxprune: unescaped JSON string field "content"; rest') and len(out) < len(raw)
    assert '"total_lines":60' in out.split("\n")[0], out.split("\n")[0]
    assert "[cp:numbered w=6]" in out
    assert decode(out.split("\n", 1)[1]) == padded                      # code bytes exact
    compact = "\n".join(f'{i + 1}|    log("step {i}", key="v")' for i in range(200))   # current Hermes gutter:
                                                                          # unescape only (quotes + newlines saved)
    out2 = wrapper_unescape(json.dumps({"content": compact, "total_lines": 60}))
    assert out2 and out2.split("\n", 1)[1] == compact
    assert wrapper_unescape(json.dumps({"content": compact, "error": "File not found: x"})) is None
    assert wrapper_unescape(json.dumps({"content": compact, "success": False})) is None
    term = json.dumps({"output": compact, "exit_code": 0, "error": None})
    assert '"exit_code":0' in wrapper_unescape(term, (), require_exit0=True)
    assert wrapper_unescape(json.dumps({"output": compact, "exit_code": 1}), (), require_exit0=True) is None
    assert hermes_failure_text('[ctxprune: x]\nraise ValueError("error")') and not hermes_failure_text(raw)
    print("view_safe self-test OK")


if __name__ == "__main__":
    _self_test()
