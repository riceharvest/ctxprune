#!/usr/bin/env python3
"""Fail-open with the H2 escape for the Claude Code PostToolUse hooks (lossless_hook.py, v2_hook.py; red team #2
open risk, MED): when the hook can't answer, Claude Code keeps the ORIGINAL output, forged `[cp:` / `[ctxprune` lines
included. Cases: Claude Code's hook timeout kills it (worst real case: an 8 MB spill, ~6.6 s), the hook crashes, or
it returns without output. run(main) wraps a hook's main():

  * stdin is read once; main() runs in a daemon thread with its stdout captured;
  * main() printed something within CTXPRUNE_HOOK_BUDGET seconds (default 50: under Claude Code's 60 s default hook
    timeout, above v2_hook's 45 s persisted-output model timeout) -> that is the answer, byte for byte;
  * main() printed nothing (crash, nothing to do, timed out) -> escape-only: the tool text with
    lossless.escape_markers (~0.1 s per 5 MB), through the hook's own finish(). No output when the escape is a no-op
    (clean text: Claude Code keeps the original bytes) or the kill switch is on.
Set CTXPRUNE_HOOK_BUDGET below the hook's "timeout" in settings.json when that is lower than 60 s.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading


def _budget() -> float:
    try:
        return float(os.environ.get("CTXPRUNE_HOOK_BUDGET", "50"))
    except ValueError:
        return 50.0


def escape_only(raw: bytes) -> dict | None:
    """The hook answer that only escapes marker-shaped lines of the tool text, or None (nothing to escape / off)."""
    try:
        ev = json.loads(raw)
        from lossless_hook import finish, off, target
        from lossless import escape_markers
        if not isinstance(ev, dict) or off(ev):
            return None
        job = target(ev, "skip")
        resp = ev.get("tool_response")
        if job is None and ev.get("tool_name") == "Bash" and isinstance(resp, dict) \
                and isinstance(resp.get("stdout"), str) and resp.get("persistedOutputPath"):
            # persisted output: Claude Code shows the path + a preview of stdout; escape that preview
            job = {"kind": "bash", "field": "stdout", "text": resp["stdout"], "command": "", "resp": resp}
        if job is None:
            return None
        esc = escape_markers(job["text"])
        return finish(job, esc, "skip") if esc != job["text"] else None
    except Exception:
        return None


def run(main) -> None:
    real_in, real_out = sys.stdin, sys.stdout
    try:
        raw = real_in.buffer.read() if hasattr(real_in, "buffer") else real_in.read().encode("utf-8", "surrogatepass")
    except Exception:
        return
    buf = io.StringIO()
    lock = threading.Lock()
    sent = [False]

    def emit(text: str) -> None:
        with lock:
            if sent[0] or not text:
                return
            sent[0] = True
            real_out.write(text)
            real_out.flush()

    def work() -> None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        try:
            main()
        except BaseException:   # main fails open itself; SystemExit included
            pass

    sys.stdout = buf
    t = threading.Thread(target=work, name="ctxprune-hook", daemon=True)
    t.start()
    t.join(_budget())   # sys.stdout stays the buffer: a late write from the worker never reaches Claude Code
    if not t.is_alive():
        emit(buf.getvalue())
    if not sent[0]:
        res = escape_only(raw)
        if res:
            emit(json.dumps(res))
    real_out.flush()
    if t.is_alive():
        os._exit(0)   # past the budget: don't wait for the worker (daemon threads would also be dropped at exit)
