#!/usr/bin/env python3
"""Read boundary trim (reach lever B, research/reach/README.md). Opt-in: CTXPRUNE_READ_TRIM=1 (off by default until
an Edit-failure A/B has run; boundary trim elides the most recent literal copy of an Edit's old_string for ~2.4% of
Edits in trace-commons).

A Read that re-views lines the model was already shown verbatim, still in context and byte-identical now, drops the
already-shown PREFIX and/or SUFFIX of the view (each a run of >= MIN_RUN lines), moves `startLine` so Claude Code's
numbering stays exact (it rebuilds every number from startLine + index, so nothing is ever removed mid-view), and says
so out of band through the hook's additionalContext:
    [ctxprune: lines 1–100 of this Read are unchanged since shown above]
"Shown" = the latest numbered view of the same path (`Read <file_path>` label) among the outputs dedup still counts
as in context (dedup.HookSession: the transcript caught up, its age cap MAX_AGE, compaction and the prompt-shrink
guard). Lines are compared byte for byte at the same line number against that latest view, so any edit since (or a
line shift) stops the run. At least one real line is always kept (an empty Read renders as an empty-file warning).
What the model saw of a trimmed Read is registered in dedup's side file, so a later trim never leans on lines that
were trimmed away. Views are parsed from the rendered text in both formats: `N\\t` (2.1.x) and the padded `   N→`.

Claude Code wiring: the PostToolUse matcher must include `Read` (e.g. "Bash|Grep|Read|mcp__.*"); lossless_hook.py and
v2_hook.py call hook() for Read and do nothing else with it.
"""

from __future__ import annotations

import os
import re

MIN_RUN = 5
MIN_GAIN = 40          # approx. tokens a trim must save beyond its note
NUM = re.compile(r"^\s*(\d+)(?:→|\t)(.*)$", re.S)
NOTE = "[ctxprune: {ranges} of this Read {verb} unchanged since shown above]"
_TRUE = ("1", "true", "yes", "on")


def enabled() -> bool:
    return (os.environ.get("CTXPRUNE_READ_TRIM", "").strip().lower() in _TRUE
            and os.environ.get("CTXPRUNE_OFF", "") not in ("1", "true", "yes"))


def parse_view(lines: list[str]) -> tuple[int, list[str]] | None:
    """(start line, line texts) of a rendered Read view: consecutive numbered rows from the top; anything after the
    numbering breaks (a system reminder, a truncation note) is ignored."""
    out: list[str] = []
    start = 0
    for row in lines:
        m = NUM.match(row)
        if not m:
            break
        n = int(m.group(1))
        if not out:
            start = n
        elif n != start + len(out):
            break
        out.append(m.group(2))
    return (start, out) if out else None


def render(start: int, lines: list[str]) -> str:
    return "\n".join(f"{start + i}\t{ln}" for i, ln in enumerate(lines))


def label_of(path: str) -> str:
    return f"Read {path}"


def shown_lines(sess, path: str) -> dict[int, str]:
    """line number -> text of that line in the latest view of `path` that dedup still counts as in context."""
    lab = label_of(path)
    if len(lab) > 200:    # labels are cut at 200 chars: two long paths could collide
        return {}
    res: dict[int, str] = {}
    for idx, (label, lines) in enumerate(sess.outputs):
        if label != lab or not sess._live(0, sess.offset + idx + 1):
            continue
        pv = parse_view(lines)
        if pv:
            for i, ln in enumerate(pv[1]):
                res[pv[0] + i] = ln
    return res


def _tok(s: str) -> int:
    return len(s) // 4 + 1


def plan(start: int, lines: list[str], shown: dict[int, str], min_run: int = MIN_RUN,
         min_gain: int = MIN_GAIN) -> tuple[int, int] | None:
    """(p, q): drop the first p and the last q lines; None = keep the view whole."""
    m = len(lines)
    p = 0
    while p < m and shown.get(start + p) == lines[p]:
        p += 1
    if p == m:            # all of it was shown: keep the last line
        p = m - 1
    q = 0
    while q < m - p - 1 and shown.get(start + m - 1 - q) == lines[m - 1 - q]:
        q += 1
    p = p if p >= min_run else 0
    q = q if q >= min_run else 0
    if not p and not q:
        return None
    gain = _tok(render(start, lines[:p])) * bool(p) + _tok(render(start + m - q, lines[m - q:])) * bool(q)
    if gain < min_gain + _tok(note(start, m, p, q)):
        return None
    return p, q


def note(start: int, m: int, p: int, q: int) -> str:
    rs = []
    if p:
        rs.append(f"{start}–{start + p - 1}")
    if q:
        rs.append(f"{start + m - q}–{start + m - 1}")
    return NOTE.format(ranges="lines " + " and ".join(rs), verb="are")


def trim(resp: dict, shown: dict[int, str]):
    """-> (updated tool_response, note, rendered text the model will see) or None."""
    fl = resp.get("file") if isinstance(resp, dict) and resp.get("type") == "text" else None
    if not isinstance(fl, dict) or not isinstance(fl.get("content"), str) or not fl["content"]:
        return None
    start = fl.get("startLine", 1)
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return None
    lines = fl["content"].split("\n")
    pq = plan(start, lines, shown)
    if pq is None:
        return None
    p, q = pq
    kept = lines[p:len(lines) - q]
    nf = dict(fl, content="\n".join(kept), startLine=start + p, numLines=len(kept))
    return dict(resp, file=nf), note(start, len(lines), p, q), render(start + p, kept)


def hook(ev: dict, state_dir: str) -> dict | None:
    """PostToolUse for Read: the hook output, or None (keep the original). Never raises."""
    try:
        ti = ev.get("tool_input") or {}
        path = ti.get("file_path") if isinstance(ti, dict) else None
        resp = ev.get("tool_response")
        if not isinstance(path, str) or not path or not isinstance(resp, dict) or resp.get("type") != "text":
            return None
        from dedup import call_label, hook_session
        hs = hook_session(ev, state_dir)
        sess = hs.load() if hs is not None else None
        if sess is None:
            return None
        if call_label("Read", ti) != label_of(path):   # labels must match what catch_up registers
            return None
        got = trim(resp, shown_lines(sess, path))
        if got is None:
            return None
        upd, msg, seen = got
        hs.register(seen, label_of(path))
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": upd,
                                       "additionalContext": msg}}
    except Exception:
        return None
