#!/usr/bin/env python3
"""Claude Code PostToolUse hook for ctxprune v2: lossless stage 0, then (for big outputs) the v2 line model via
v2_server.py. Returns the full tool object with one text field replaced (built-in tools ignore bare strings).

Tools (shapes and rules: lossless_hook.py): Bash `stdout`; Grep tool `content` in content mode (line-level, kept lines
byte-exact, gap markers); never Read/Edit/Write. With --persisted keep|drop, a Bash output Claude Code persisted to a
file (>30k) is read in full from persistedOutputPath and compressed to <= ~24k chars (model keep = min(--keep,
24k/len)); if the server is down or the result stays over the cap, nothing is printed.

settings.json:
  {"hooks": {"PostToolUse": [{"matcher": "Bash|PowerShell|Grep|mcp__.*", "hooks": [{"type": "command",
     "command": "python3 /abs/scripts/v2_hook.py [--preset balanced] [--keep R] [--min-chars N] [--port 8765]
                 [--persisted skip|keep|drop]"}]}]}}
  Without --preset / CTXPRUNE_PRESET the hook runs the `safe` preset (lossless only, like lossless_hook.py; cross-turn
  dedup is opt-in: CTXPRUNE_DEDUP=1 or --dedup on).
Before all that (lossless_hook.spill_or_read): a Read goes only to the opt-in boundary trim (read_trim.py,
CTXPRUNE_READ_TRIM=1; the matcher must then include Read), and a result Claude Code spills to a file goes to spill.py
(exact lossless file, never inline / lossy / dedup; with --persisted keep|drop the explicit lossy inline path keeps
persisted Bash outputs).
Intent = the agent's last assistant text (last 1500 chars, from transcript_path) + "\n" + the command (500 chars; for
Grep a stand-in `rg ... -- <pattern> <path>`), the same shape as build_usage_eval.py's intent.
Fails open: any error or server problem -> stage-0 result or the original.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)
DEDUP_DIR = os.environ.get("CTXPRUNE_STATE", os.path.expanduser("~/.cache/ctxprune")) + "/dedup"


def last_assistant_text(path: str | None, max_lines: int = 400) -> str:
    """Last assistant text block in a Claude Code transcript JSONL (reads only the tail)."""
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 2_000_000))
        tail = f.read().decode("utf-8", "replace").splitlines()[-max_lines:]
    for line in reversed(tail):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") or {}
        if rec.get("type") != "assistant" and msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"] \
            if isinstance(content, list) else [content] if isinstance(content, str) else []
        text = "\n".join(t for t in texts if t).strip()
        if text:
            return text
    return ""


def v2_compress(text: str, intent: str, keep: float, port: int, timeout: float) -> str | None:
    """POST to v2_server.py (never via http_proxy: ctxprune_core.post_compress, audit H1); None on any problem (the
    caller keeps the lossless result). No circuit breaker here (the proxy has its own)."""
    try:
        from ctxprune_core import post_compress
        return post_compress(port, {"text": text, "intent": intent, "keep": keep}, timeout)["text"]
    except Exception:
        return None


def main() -> None:
    # Settings: CTXPRUNE_PRESET (safe | balanced | aggressive, default safe; or --preset) resolved for claude-code
    # (ctxprune_core.PRESETS / PRESET_HARNESS); a flag given here wins over the preset, an absent one never clobbers it.
    # --recoverable on|off: save the full text, every gap gets its exact range + the file (recover.py)
    # --dedup on|off: cross-turn dedup (PLAN §2.656 E2) against earlier outputs / inputs in context
    # --persisted skip|keep|drop: Bash output Claude Code persisted to a file (>30k), see lossless_hook.py
    # Refetch guard (lossy presets): a re-run of a lossy output's command, or a read of its recovery file, within 3
    # tool calls switches this session to safe for CTXPRUNE_GUARD_TURNS (10) calls; state = the dedup session state.
    from ctxprune_core import PRESETS
    spec = {"keep": (float, None, None), "min_chars": (int, None, None), "port": (int, None, None),
            "timeout": (float, None, None), "recoverable": (str, "on", ("on", "off")),
            "dedup": (str, "", ("", "on", "off")), "persisted": (str, None, ("skip", "keep", "drop")),
            "preset": (str, None, tuple(PRESETS))}
    try:
        from lossless_hook import _NS, parse_args
        a = _NS(parse_args(sys.argv[1:], spec))
        ev = json.load(sys.stdin)
        if ev.get("tool_name") == "Read":   # only the opt-in boundary trim (read_trim.py) ever touches a Read
            from lossless_hook import spill_or_read
            spill_or_read(ev, False)
            return
        from ctxprune_core import Settings, process
        from lossless_hook import PERSIST_TARGET, finish, persisted_footer, rendered, target
        st = Settings.from_env(preset=a.preset, keep=a.keep, min_chars=a.min_chars, port=a.port, timeout=a.timeout,
                               recoverable=a.recoverable == "on", harness="claude-code")
        if a.dedup:   # default: the preset / CTXPRUNE_DEDUP (off unless opted in)
            st.dedup = a.dedup == "on"
        persisted = (a.persisted or st.persisted) if st.model else "skip"   # model off: can't fit inline
        from lossless_hook import spill_or_read
        if spill_or_read(ev, persisted == "skip"):   # Read trim (opt-in) / exact spill files; --persisted keep|drop
            return                                   # (explicit lossy inline of a persisted Bash output) wins there
        job = target(ev, persisted)
        if not job:
            return
        sess = None
        if st.dedup or st.model:   # the session state also carries the refetch guard
            from dedup import hook_session
            sess = hook_session(ev, DEDUP_DIR)
        desc = str((ev.get("tool_input") or {}).get("description") or "")[:300]
        text = job["text"]
        keep = None
        if job["kind"] == "mcp":   # MCP results: lossless + dedup only
            st.model = False
        if job["kind"] == "bash_persisted":   # fit Claude Code's inline cap: model keep = min(keep, room / len)
            room = PERSIST_TARGET - len(persisted_footer(job))
            st.min_chars, st.timeout, st.harness = 0, max(st.timeout, 45), "cc-persisted"
            keep = min(st.keep, room / max(1, len(text)))
        guard, intent = None, ""
        if st.model and len(text) >= st.min_chars:   # transcript tail / guard state only when the model could run
            intent = (last_assistant_text(ev.get("transcript_path"))[-1500:] + "\n" + job["command"][:500]
                      + ("\n" + desc if desc else "")).strip()
            if sess is not None and st.guard_turns > 0:
                guard = sess.guard()
        new, rec = process(text, job["command"], tool=ev.get("tool_name") or "", intent=intent, settings=st,
                           session=sess if st.dedup else None, keep_override=keep, guard=guard)
        if job["kind"] == "bash_persisted" and rec.get("v2") not in ("ok", "not_smaller") and len(new) > PERSIST_TARGET:
            return   # a persisted output can't be shown inline without the model
        res = finish(job, new, persisted)
        if res:
            if sess is not None:   # what the model will see, for later dedup against it (+ the guard's lossy record)
                sess.register(rendered(job, res), job["command"][:200], lossy=rec.get("lossy"))
            json.dump(res, sys.stdout)
    except (Exception, SystemExit):
        return


if __name__ == "__main__":
    os.umask(0o077)   # own process: every state file it creates is private (audit M2)
    main()
