#!/usr/bin/env python3
"""GitHub Copilot CLI `postToolUse` hook: lossless `safe` pipeline on shell / grep / MCP output (design:
docs/later-integrations.md).

stdin: {toolName, toolArgs, toolResult: {resultType, textResultForLlm}, ...} (toolArgs may be a JSON string).
stdout: nothing (keep the result) or {"modifiedResult": <the full toolResult with textResultForLlm replaced>, with
        resultType "success"}. The whole object is returned, every other key copied: copilot-cli #3361 shows a bare
        string is silently ignored.
Skipped: file view/edit/create tools, non-success results, text under CTXPRUNE_MIN_CHARS. CTXPRUNE_OFF=1: off.
Lossless only (no session state: no dedup, no lossy). Fails open: any error -> no output.
UNVERIFIED LIVE: the payload shape follows the docs and is tested on synthetic fixtures only (no recorded payload).
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

SKIP = re.compile(r"^(view|edit|create|str_replace_editor|str_replace_based_edit_tool|write|read|apply_patch)$", re.I)


def _args(a) -> dict:
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            return {}
    return a if isinstance(a, dict) else {}


def transform(payload: dict, compress=None) -> dict | None:
    """The `modifiedResult` object, or None to keep the result. `compress(text, command, tool) -> str`."""
    tool = str(payload.get("toolName") or "")
    res = payload.get("toolResult")
    if not tool or SKIP.search(tool) or not isinstance(res, dict) or res.get("resultType") != "success":
        return None
    text = res.get("textResultForLlm")
    if not isinstance(text, str) or len(text) < int(os.environ.get("CTXPRUNE_MIN_CHARS", "0") or 0):
        return None
    a = _args(payload.get("toolArgs"))
    command = a.get("command") if isinstance(a.get("command"), str) else f"{tool} {json.dumps(a)[:500]}"
    if compress is None:
        import ctxprune_compress

        def compress(t, c, tl):
            return ctxprune_compress.run(t, c, None, tl, None, None, None, None, True, "copilot", None, None)[0]
    out = compress(text, command, tool)
    if not isinstance(out, str) or out == text:
        return None
    return {**res, "textResultForLlm": out, "resultType": "success"}


def main() -> int:
    try:
        if os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes"):
            return 0
        mod = transform(json.loads(sys.stdin.buffer.read().decode("utf-8", "surrogateescape")))
        if mod is not None:
            sys.stdout.write(json.dumps({"modifiedResult": mod}, ensure_ascii=False))
    except BaseException:  # noqa: BLE001 - fail open
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
