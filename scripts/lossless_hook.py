#!/usr/bin/env python3
"""Claude Code PostToolUse hook: apply the lossless stage 0 (lossless.py) to Bash / PowerShell output and Grep-tool
content.
Also holds the tool-shape helpers that v2_hook.py imports (one code path for both hooks).

stdin: the hook JSON ({tool_name, tool_input, tool_response, transcript_path, ...}).
stdout: nothing (keep the original) or {"hookSpecificOutput": {"hookEventName": "PostToolUse",
        "updatedToolOutput": <the full tool object with one text field replaced>}}. Built-in tools ignore a bare string
        or a value that doesn't match the tool's output schema, so the whole object is returned, every other key copied
        (Claude Code >= 2.1.121).

Reached tools (PLAN §2.65 D3/D4):
  Bash  {stdout, stderr, interrupted, isImage, ...}: `stdout` replaced.
  PowerShell  (Claude Code on Windows; input {command, description}, same response shape as Bash): handled as Bash.
        Its output is CRLF + SGR colour codes: colours go (ansi rule), CRLF stays byte-exact (lossless2 encodes the
        LF form and puts the CRs back). Get-Content / gc / type count as file views.
  Grep  {mode, numFiles, filenames, content, numLines, ...} (schema read from the 2.1.302 bundle): only mode=="content"
        with a string `content`; `content` replaced line-level (kept lines byte-exact), every other field kept as is
        (numLines is not rendered to the model). files_with_matches / count modes pass through.
  mcp__<server>__<tool>: tool_response a string, a list of content blocks or {"content": [blocks]}; the largest text
        block is compressed (lossless + dedup only, never the model) and the same shape returned via
        updatedMCPToolOutput. MCP file read/write/edit tools (MCP_SKIP, like the proxy's SKIP_TOOLS) are skipped.
  Read: only the opt-in boundary trim (read_trim.py, CTXPRUNE_READ_TRIM=1, matcher must include Read): an already
        shown, unchanged prefix/suffix is dropped and startLine moved, so Claude Code's numbering stays exact.
  Edit / Write and everything else: never touched.
Spills (spill.py, default on, CTXPRUNE_SPILL=0 = off): a Bash result > 30k chars, an MCP result > 50k chars, or an
  MCP result over the token cap that Claude Code saved to a file gets an exactly decodable lossless file (Bash / MCP:
  via the hook's output, CC persists it; over the token cap: the saved file rewritten in place). Never inline, never
  lossy, no dedup markers; anything unexpected leaves the result alone (it is then not compressed at all).
Kill switch (as in v0.2.1): CTXPRUNE_OFF=1 in the environment disables both hooks; a Bash command containing
  `CTXPRUNE_OFF=1` passes through verbatim.
Bash outputs Claude Code persisted to a file (> ~30k chars; tool_response has persistedOutputPath + persistedOutputSize
  and only a ~30 KB stdout preview), --persisted:
    skip (default until the live hooktest says which variant shows inline): pass through
    keep (variant A): read the full file, compress, stdout = result, pointer fields kept
    drop (variant B): same, pointer fields removed (in 2.1.302 a set persistedOutputPath makes Claude Code render
                      path + ~2 KB preview of stdout instead of stdout itself)
  The result must be <= PERSIST_CAP chars, else nothing is printed (an inline 50k stdout would be worse than the
  default). Lossless alone rarely gets there (e.g. `cat` output is left byte-exact); v2_hook.py adds the model.

settings.json:
  {"hooks": {"PostToolUse": [{"matcher": "Bash|PowerShell|Grep|mcp__.*", "hooks": [{"type": "command",
     "command": "python3 /abs/scripts/lossless_hook.py [--persisted drop]"}]}]}}
Fails open: any error -> no output -> Claude Code keeps the original.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

DEDUP_DIR = os.environ.get("CTXPRUNE_STATE", os.path.expanduser("~/.cache/ctxprune")) + "/dedup"
PERSIST_TARGET = 24_000      # chars the persisted-output compression aims for (incl. the footer)
PERSIST_CAP = 25_000         # hard cap for a persisted replacement (Claude Code persists Bash output > ~30k)
MAX_PERSISTED_BYTES = 8_000_000
PTR_FIELDS = ("persistedOutputPath", "persistedOutputSize")
SHELL_TOOLS = ("Bash", "PowerShell")   # same {command} input and {stdout, stderr, ...} response
MCP_SKIP = re.compile(r"(?i)(^|[_.:/-])(read|read_file|read_text_file|read_multiple_files|readfile|get_file_contents|"
                      r"view|view_file|open_file|edit|edit_file|multiedit|multi_edit|write|write_file|create_file|"
                      r"create_or_update_file|push_files|patch|apply_patch|str_replace_editor|fs_read|fs_write|"
                      r"git_show|git_diff|git_diff_staged|git_diff_unstaged)$")


def off(ev: dict | None = None) -> bool:
    """Kill switch: CTXPRUNE_OFF=1 in the environment, or in the Bash command itself."""
    if os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes"):
        return True
    ti = (ev or {}).get("tool_input") or {}
    return isinstance(ti, dict) and "CTXPRUNE_OFF=1" in str(ti.get("command") or "")


def _mcp_text(resp) -> tuple[list, str] | None:
    """(path to the largest text, text) inside an MCP tool_response, or None."""
    if isinstance(resp, str):
        return ([], resp) if resp else None
    base: list = []
    blocks = resp
    if isinstance(resp, dict):
        if not isinstance(resp.get("content"), list):
            return None
        base, blocks = ["content"], resp["content"]
    if not isinstance(blocks, list):
        return None
    best = None
    for k, b in enumerate(blocks):
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"]:
            if best is None or len(b["text"]) > len(best[1]):
                best = (base + [k, "text"], b["text"])
    return best


def _set(obj, path: list, value):
    if not path:
        return value
    head = path[0]
    if isinstance(obj, dict):
        return {k: (_set(v, path[1:], value) if k == head else v) for k, v in obj.items()}
    return [(_set(v, path[1:], value) if i == head else v) for i, v in enumerate(obj)]


def parse_args(argv: list[str], spec: dict) -> dict:
    """--name value / --name=value for the few hook options (argparse costs ~17 ms per hook process in 3.14: it
    imports _colorize). spec: {name: (type, default, choices | None)}. Raises ValueError on anything unexpected."""
    out = {k: v[1] for k, v in spec.items()}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("--"):
            raise ValueError(a)
        name, eq, val = a[2:].partition("=")
        if not eq:
            i += 1
            if i >= len(argv):
                raise ValueError(a)
            val = argv[i]
        key = name.replace("-", "_")
        if key not in spec:
            raise ValueError(a)
        typ, _, choices = spec[key]
        v = typ(val)
        if choices and v not in choices:
            raise ValueError(a)
        out[key] = v
        i += 1
    return out


class _NS:
    def __init__(self, d: dict):
        self.__dict__.update(d)


def grep_command(ti: dict) -> str:
    """A stand-in rg command line for a Grep tool call: lossless.py takes path tokens from it (so single-path hits
    still group) and v2 uses it as the intent's tool call. Starts with `rg`, so the viewer rule never fires."""
    parts = ["rg"]
    for flag, key in (("-i", "-i"), ("-n", "-n")):
        if ti.get(key):
            parts.append(flag)
    for k in ("-A", "-B", "-C", "context"):
        if ti.get(k) not in (None, "", 0):
            parts += ["-C" if k == "context" else k, str(ti[k])]
    if ti.get("glob"):
        parts += ["--glob", str(ti["glob"])]
    if ti.get("type"):
        parts += ["--type", str(ti["type"])]
    parts += ["--", str(ti.get("pattern") or "")]
    if ti.get("path"):
        parts.append(str(ti["path"]))
    return " ".join(shlex.quote(p) for p in parts)


def read_persisted(path: str, preview: str | None) -> str | None:
    """Full output from Claude Code's persisted file, or None (missing, too big, or doesn't start like the preview)."""
    try:
        if not isinstance(path, str) or not os.path.isfile(path) or os.path.getsize(path) > MAX_PERSISTED_BYTES:
            return None
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    head = (preview or "").strip()[:200]
    if head and head not in text[:len(head) + 4096]:  # not the raw output we think it is (format changed?)
        return None
    return text or None


def target(ev: dict, persisted: str = "skip") -> dict | None:
    """What to compress: {kind: bash|grep|bash_persisted, field, text, command, resp} or None (leave untouched)."""
    name, resp, ti = ev.get("tool_name"), ev.get("tool_response"), ev.get("tool_input") or {}
    if off(ev) or not isinstance(ti, dict):
        return None
    if isinstance(name, str) and name.startswith("mcp__"):
        tool = name.rsplit("__", 1)[-1]
        if MCP_SKIP.search(tool):
            return None
        got = _mcp_text(resp)
        if got is None:
            return None
        return {"kind": "mcp", "field": got[0], "text": got[1], "command": name, "resp": resp}
    if not isinstance(resp, dict):
        return None
    if name == "Grep":
        c = resp.get("content")
        if resp.get("mode") != "content" or not isinstance(c, str) or not c:
            return None
        return {"kind": "grep", "field": "content", "text": c, "command": grep_command(ti), "resp": resp}
    if name not in SHELL_TOOLS or resp.get("isImage"):
        return None
    cmd = ti.get("command") or ""
    out = resp.get("stdout")
    if resp.get("persistedOutputPath"):
        if persisted not in ("keep", "drop"):
            return None
        text = read_persisted(resp["persistedOutputPath"], out if isinstance(out, str) else None)
        if text is None:
            return None
        return {"kind": "bash_persisted", "field": "stdout", "text": text, "command": cmd, "resp": resp}
    if not isinstance(out, str) or not out:
        return None
    return {"kind": "bash", "field": "stdout", "text": out, "command": cmd, "resp": resp}


def persisted_footer(job: dict) -> str:
    return (f"\n[ctxprune: compressed from {len(job['text']):,} chars; the full output is in "
            f"{job['resp'].get('persistedOutputPath')}]")


def finish(job: dict, new: str, persisted: str = "skip") -> dict | None:
    """The hook's stdout object, or None to keep the original. The text the model will see is recorded in
    emitted.py's registry, so a ctxprune_proxy stacked behind this hook forwards it untouched (live canary)."""
    res = _finish(job, new, persisted)
    if res:
        try:
            from emitted import record
            record(rendered(job, res))
        except Exception:
            pass
    return res


def _finish(job: dict, new: str, persisted: str = "skip") -> dict | None:
    if job["kind"] == "mcp":
        if new == job["text"]:
            return None
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "updatedMCPToolOutput": _set(job["resp"], job["field"], new)}}
    if job["kind"] == "bash_persisted":
        new = new + persisted_footer(job)
        if len(new) > PERSIST_CAP:
            return None
    elif new == job["text"]:
        return None
    upd = dict(job["resp"])
    upd[job["field"]] = new
    if job["kind"] == "bash_persisted" and persisted == "drop":
        for k in PTR_FIELDS:
            upd.pop(k, None)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": upd}}


def rendered(job: dict, res: dict) -> str:
    """The text the model will see for this result (what dedup registers)."""
    h = res["hookSpecificOutput"]
    if job["kind"] == "mcp":
        cur = h["updatedMCPToolOutput"]
        for k in job["field"]:
            cur = cur[k]
        return cur
    return h["updatedToolOutput"][job["field"]]


def spill_or_read(ev: dict, bash_spill: bool) -> bool:
    """The two reach levers, before the normal pipeline. True = this call is handled (the hook output, if any, is
    printed): a Read (only ever touched by the opt-in boundary trim, read_trim.py) or a result Claude Code spills to
    a file (spill.py: the file gets the exact lossless text, or the result is left alone; never inline, never dedup)."""
    if ev.get("tool_name") == "Read":
        if not off(ev) and os.environ.get("CTXPRUNE_READ_TRIM", "").strip().lower() in ("1", "true", "yes", "on"):
            from read_trim import hook as read_hook
            res = read_hook(ev, DEDUP_DIR)
            if res:
                json.dump(res, sys.stdout)
        return True
    if off(ev):
        return False
    from spill import PASS, handle
    res = handle(ev, bash_ok=bash_spill)
    if res is None:
        return False
    if res is not PASS:
        json.dump(res, sys.stdout)
    return True


def main() -> None:
    try:
        a = _NS(parse_args(sys.argv[1:], {"persisted": (str, "drop", ("skip", "keep", "drop")),   # hooktest: B shows
                                          "dedup": (str, "", ("", "on", "off"))}))                # inline
        ev = json.load(sys.stdin)
        if spill_or_read(ev, True):
            return
        # model off here: a persisted output (> ~30k) never gets under the inline cap with lossless alone (red team:
        # 284/284 real ones stayed above 25k), so don't even read it
        job = target(ev, "skip")
        if not job:
            return
        from ctxprune_core import Settings, process
        st = Settings.from_env(model=False, harness="claude-code")   # dedup: off unless CTXPRUNE_DEDUP=1 / --dedup on
        if a.dedup:
            st.dedup = a.dedup == "on"
        sess = None
        if st.dedup:   # PLAN §2.656 E2: lossless, the referenced lines are still in context
            from dedup import hook_session
            sess = hook_session(ev, DEDUP_DIR)
        new, _rec = process(job["text"], job["command"], tool=ev.get("tool_name") or "", settings=st, session=sess)
        res = finish(job, new, a.persisted)
        if res:
            if st.dedup:
                from dedup import hook_register
                hook_register(ev, rendered(job, res), job["command"][:200], DEDUP_DIR)
            json.dump(res, sys.stdout)
    except (Exception, SystemExit):  # fail open (bad options included)
        return


if __name__ == "__main__":
    os.umask(0o077)   # own process: every state file it creates is private (audit M2)
    main()
