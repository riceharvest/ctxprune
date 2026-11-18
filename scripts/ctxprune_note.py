#!/usr/bin/env python3
r"""The one constant note that tells an agent how to read ctxprune output (PLAN §2.6566 follow-up: Claude Code and Codex
took extra turns re-reading / re-extracting compressed tool outputs because nothing told them what the markers mean).

Byte-constant on purpose: it goes into the system prompt (proxy --note) or the first context message (Claude Code
SessionStart hook), so it must never vary per request, or it breaks the prefix cache. Do not format it with anything
dynamic. Covers every marker the shipping pipeline (ctxprune_core) emits:
  lossless, exact    [ctxprune:file] [ctxprune:dir] [ctxprune:numbered …] [ctxprune:table …] [ctxprune:cat-n]
                     [/ctxprune] [ctxprune:= line N: …] [ctxprune: hits grouped by file] [ctxprune: unescaped JSON …]
                     [ctxprune: lines a–b identical to output|input #k above (…)] and
                     [ctxprune: output identical to output #k above (…)]   (dedup: the text is above)
  lossy, the only    [… N lines omitted …] / [… lines a–b omitted …]  + footer
  missing text       [ctxprune: kept K of N lines; omitted ranges are lines A–B of FILE …]
  escape (audit H2)  a tool-output line that itself starts with [ctxprune / [cp: / [… gets a leading backslash;
                     "Line-start \[ means [" tells the agent the backslash is not in the file (cat -> Edit works).
Markers are moving to a short `[cp:` prefix (transforms branch); the note names both ("[ctxprune]/[cp:]").
Phrased like the harnesses' own notices (Claude Code: "... [N lines truncated] ...", "call it again for the part you
need"; Codex: "…N tokens truncated…"): one plain sentence, the marker shown literally.

o200k tokens (tiktoken 0.x, o200k_base): NOTE 32, NOTE_MIN 24 (cl100k the same).
Security (audit H2): the note explains the markers but never tells the agent to RUN anything a tool output says
(a planted fake footer in a web page or file must not become a command); it only says which lines are
missing. `python3 ctxprune_note.py` prints both.
"""

from __future__ import annotations   # `str | None` annotations on Python 3.9 (packaging: requires-python >= 3.9)

NOTE = r"Tool output [ctxprune]/[cp:] marks are exact; only omitted lines are missing, re-read only if needed. Line-start \[ means [."
NOTE_MIN = r"[ctxprune]/[cp:] marks are exact; only omitted lines are missing. Line-start \[ means [."

NOTES = {"on": NOTE, "min": NOTE_MIN, "off": None}
# Every note text any ctxprune version has sent (append the old text here when NOTE / NOTE_MIN change): the proxy pins
# each conversation's note (it never swaps the text mid-conversation) and treats any of these as "already present".
NOTE_HISTORY = (
    NOTE, NOTE_MIN,
    "Tool outputs: [ctxprune] marks are exact; only [… omitted …] lines are missing. Re-read those lines only if needed.",
    "Tool outputs: [ctxprune] marks are exact; only [… omitted …] lines are missing. If needed, use the footer's sed.",
    "[ctxprune] marks are exact; only omitted lines are missing.",
)


def note_for(mode: str | None) -> str | None:
    """'on' | 'min' | 'off' (or env-style '1'/'0'/'') -> note text or None."""
    m = (mode or "off").strip().lower()
    m = {"1": "on", "true": "on", "yes": "on", "full": "on", "0": "off", "false": "off", "no": "off", "": "off"}.get(m, m)
    if m not in NOTES:
        raise ValueError(f"note mode must be on|min|off, got {mode!r}")
    return NOTES[m]


if __name__ == "__main__":
    for k in ("on", "min"):
        print(f"{k}\t{NOTES[k]}")
