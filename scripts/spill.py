#!/usr/bin/env python3
"""Compressed spill files (reach lever A', research/reach/README.md): when Claude Code (2.1.302) spills a big tool
result to a file and shows the model only a preview, make the FILE smaller, exactly. Never inline (lever A was net
negative), never lossy, never a dedup marker (a file can outlive the output a marker points to).

Three cases, all strict (anything unexpected -> leave Claude Code's behaviour alone):
  bash      Bash > 30,000 chars. CC already wrote the full output (persistedOutputPath) and the hook sees a 30k
            stdout preview. The hook returns updatedToolOutput with the pointer fields dropped and stdout = the
            compressed full text; CC re-persists the hook's version (tool-results/<tool_use_id>.txt) and builds its
            preview from it, so the preview is a byte prefix of the new file (line numbers match). Only when the
            compressed text stays > BASH_CAP x MARGIN (else CC would show it inline), stderr is empty, the call was not
            interrupted and the file starts with the stdout preview.
  mcp       MCP result > 50,000 chars (sum of its text blocks, JS .length) and under CC's token cap: the hook sees the
            full result and CC persists the hook's version after it (a list of blocks -> JSON file, a string -> text
            file). The largest text block is compressed; the result must stay > MCP_CAP x MARGIN. A result shaped
            {"content": [...]} or holding images is left alone (persistence of those shapes is not verified).
  mcp_big   MCP result over CC's token cap (~25k tokens): CC saved it BEFORE the hook and the hook only sees
            "Error: result (N characters across M lines) exceeds maximum allowed tokens. Output has been saved to
            PATH.\\nFormat: Plain text\\n...". The path is parsed from that wording; only `Plain text`, no persist-limit
            note, a regular single-link file in a tool-results/ dir whose UTF-16 length is N and line count M. The file
            is rewritten in place (temp file in the same dir, fsync, original mode, os.replace; skipped if the file
            changed meanwhile) and the counts in the message are updated through updatedMCPToolOutput. A JSON file
            that is no longer JSON gets one NOT_JSON line after `Format:` (no preview here, and CC's advice names jq).
Exactness: lossless.compress() is used only when lossless.decode() gives the original back byte for byte (so the
lossy-by-design stage-0 rules cr / ansi / dup and json_unescape never reach a file). Pretty-printed JSON: json_compact
(+ e.g. jsonrows) is kept when lossless.decode() and then re-indenting (reindent(), 2 / 4 spaces or a tab, the
JSON.stringify layout) give the original bytes back. For text files (bash, mcp string, mcp_big) the longest line may
not grow past max(original, LINE_CAP): Read chunks by lines, so a compacted one-line JSON is never written (rows are).
Off: CTXPRUNE_SPILL=0 (or the global CTXPRUNE_OFF=1). handle(ev) -> hook output dict | PASS (a spill case left alone:
the caller must not touch it either) | None (not a spill case: the normal pipeline runs).
"""

from __future__ import annotations

import os
import re
import stat

BASH_CAP = 30_000           # Bash tool maxResultSizeChars (2.1.302): above it the result is shown as a preview
MCP_CAP = 50_000            # CC's persistence threshold ceiling for MCP results (UD = 50000 in the bundle)
MARGIN = 1.05               # the compressed result must stay this far above the cap (so it still spills)
MAX_FILE_BYTES = 8_000_000
LINE_CAP = 2000             # a text file's lines may grow up to this (Read shows lines of this size whole)
PTR_FIELDS = ("persistedOutputPath", "persistedOutputSize")
PASS = "pass"
_BIG = re.compile(r"\AError: result \(([\d,]+) characters across ([\d,]+) lines\) exceeds maximum allowed tokens\. "
                  r"Output has been saved to (/[^\n]+?\.txt)\.\nFormat: Plain text\n")
_NOTE = "Note: the output exceeded the persist byte limit"


def enabled() -> bool:
    return (os.environ.get("CTXPRUNE_SPILL", "1").strip().lower() not in ("0", "false", "no", "off")
            and os.environ.get("CTXPRUNE_OFF", "") not in ("1", "true", "yes"))


def u16(s: str) -> int:
    """JavaScript .length (UTF-16 code units): what CC's thresholds and counts measure."""
    return len(s) + sum(1 for c in s if ord(c) > 0xFFFF) if not s.isascii() else len(s)


def max_line(s: str) -> int:
    return max((u16(x) for x in s.split("\n")), default=0)


def reindent(compact: str, ind: str) -> str:
    """JSON.stringify(JSON.parse(compact), null, ind) at the token level (numbers / escapes / key order kept as is)."""
    out, depth, i, n = [], 0, 0, len(compact)
    while i < n:
        c = compact[i]
        if c == '"':
            j = i + 1
            while compact[j] != '"':
                j += 2 if compact[j] == "\\" else 1
            out.append(compact[i:j + 1])
            i = j + 1
            continue
        if c in "{[":
            close = "}" if c == "{" else "]"
            if i + 1 < n and compact[i + 1] == close:
                out.append(c + close)
                i += 2
                continue
            depth += 1
            out.append(c + "\n" + ind * depth)
        elif c in "}]":
            depth -= 1
            out.append("\n" + ind * depth + c)
        elif c == ",":
            out.append(",\n" + ind * depth)
        elif c == ":":
            out.append(": ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _json_exact(text: str, compact: str) -> bool:
    core, nl = (compact[:-1], "\n") if compact.endswith("\n") else (compact, "")
    try:
        return any(reindent(core, ind) + nl == text for ind in ("  ", "    ", "\t"))
    except IndexError:
        return False


def exact(text: str, command: str = "", json_ok: bool = True, text_file: bool = True) -> str | None:
    """The stage-0 text if it is shorter and gives `text` back byte for byte (lossless.decode; after json_compact,
    lossless.decode then reindent), else None. text_file: the longest line may not grow past max(original, LINE_CAP)."""
    from lossless import compress, decode
    new, st = compress(text, command)
    if len(new) >= len(text):
        return None
    if "json_compact" in (st.get("rules") or []):
        ok = json_ok and _json_exact(text, decode(new))
    else:
        ok = decode(new) == text
    if not ok or (text_file and max_line(new) > max(max_line(text), LINE_CAP)):
        return None
    return new


def _read_exact(path: str) -> str | None:
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as f:
            return f.read().decode("utf-8")    # strict: a file that isn't clean UTF-8 is not ours to rewrite
    except (OSError, UnicodeDecodeError):
        return None


def _out(**kw) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", **kw}}


def bash(ev: dict):
    resp = ev.get("tool_response")
    if not isinstance(resp, dict) or not resp.get("persistedOutputPath"):
        return None
    out = resp.get("stdout")
    if (resp.get("isImage") or resp.get("interrupted") or resp.get("stderr") or not isinstance(out, str) or not out
            or not isinstance(resp["persistedOutputPath"], str)):
        return PASS
    text = _read_exact(resp["persistedOutputPath"])
    if text is None or not text.startswith(out) or u16(text) <= BASH_CAP:
        return PASS
    new = exact(text, str((ev.get("tool_input") or {}).get("command") or ""))
    if new is None or u16(new) <= BASH_CAP * MARGIN:
        return PASS
    upd = {k: v for k, v in resp.items() if k not in PTR_FIELDS}
    upd["stdout"] = new
    return _out(updatedToolOutput=upd)


def mcp(ev: dict):
    resp = ev.get("tool_response")
    if isinstance(resp, str):
        if resp.startswith("Error: result ("):
            return mcp_big(ev, resp)
        if u16(resp) <= MCP_CAP:
            return None
        new = exact(resp, str(ev.get("tool_name") or ""))
        if new is None or u16(new) <= MCP_CAP * MARGIN:
            return PASS
        return _out(updatedMCPToolOutput=new)
    if isinstance(resp, dict) and isinstance(resp.get("content"), list):
        blocks = resp["content"]
        total = sum(u16(b["text"]) for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))
        return PASS if total > MCP_CAP else None
    if not isinstance(resp, list):
        return None
    if any(not isinstance(b, dict) or b.get("type") != "text" or not isinstance(b.get("text"), str) for b in resp):
        return None    # image / document blocks: CC never persists those (and other shapes: not ours)
    sizes = [u16(b["text"]) for b in resp]
    total = sum(sizes)
    if total <= MCP_CAP:
        return None
    k = max(range(len(resp)), key=lambda i: sizes[i])
    new = exact(resp[k]["text"], str(ev.get("tool_name") or ""), text_file=False)
    if new is None or total - sizes[k] + u16(new) <= MCP_CAP * MARGIN:
        return PASS
    return _out(updatedMCPToolOutput=[dict(b, text=new) if i == k else b for i, b in enumerate(resp)])


def _lines(s: str) -> int:
    """CC's line count in the over-token-cap message (live 2.1.302: a trailing newline does not start a line)."""
    return s.count("\n") + (0 if s.endswith("\n") else 1)


def _fmt(n: int) -> str:
    return f"{n:,}"


def rewrite(path: str, old: str, new: str) -> bool:
    """Replace `path` (a regular, single-link file holding exactly `old`) with `new`, atomically, same mode."""
    d, base = os.path.split(path)
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return False
        tmp = os.path.join(d, f".{base}.ctxprune-{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            data = new.encode("utf-8")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fchmod(f.fileno(), stat.S_IMODE(st.st_mode))
                os.fsync(f.fileno())
            now = os.lstat(path)   # changed since we read it? (another writer) -> leave it
            if (now.st_ino, now.st_size, now.st_mtime_ns) != (st.st_ino, st.st_size, st.st_mtime_ns) \
                    or _read_exact(path) != old:
                raise OSError("changed")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def mcp_big(ev: dict, msg: str):
    m = _BIG.match(msg)
    if not m:
        return None   # not CC's wording (JSON formats, another version): the normal path handles the message
    if _NOTE in msg:
        return PASS
    n_chars, n_lines, path = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")), m.group(3)
    if not os.path.isabs(path) or os.path.basename(os.path.dirname(path)) != "tool-results":
        return PASS
    text = _read_exact(path)
    if text is None or u16(text) != n_chars or _lines(text) != n_lines:
        return PASS
    new = exact(text, str(ev.get("tool_name") or ""))
    if new is None or _lines(new) < 2 or not rewrite(path, text, new):
        return PASS
    head = m.group(0)
    new_head = head.replace(f"({m.group(1)} characters across {m.group(2)} lines)",
                            f"({_fmt(u16(new))} characters across {_fmt(_lines(new))} lines)", 1)
    if _is_json(text) and not _is_json(new):   # no preview here, and CC's advice names jq: say it is not JSON now
        new_head += NOT_JSON + "\n"
    return _out(updatedMCPToolOutput=new_head + msg[len(head):])


NOT_JSON = ("[ctxprune: the saved file holds this output in an exact lossless text form (its [cp:…] lines say how to "
            "read it); it is no longer JSON, so read it with Read or grep, not jq]")


def _is_json(s: str) -> bool:
    t = s.strip()
    if not t or t[0] not in "{[":
        return False
    import json
    try:
        json.loads(t)
        return True
    except (ValueError, RecursionError):
        return False


def handle(ev: dict, bash_ok: bool = True):
    """Hook entry: a hook output dict, PASS, or None (see the module doc). Never raises."""
    try:
        if not enabled():
            return None
        name = str(ev.get("tool_name") or "")
        if name == "Bash":   # not PowerShell: its inline cap is not verified (an output under it would go inline)
            return bash(ev) if bash_ok else None
        if name.startswith("mcp__"):
            from lossless_hook import MCP_SKIP
            if MCP_SKIP.search(name.rsplit("__", 1)[-1]):   # file read/write tools: never touched (as the hook)
                return None
            return mcp(ev)
    except Exception:   # fail open: an error inside a spill case leaves it alone
        return PASS
    return None
