"""Lossless stage 0 for the ctxprune v2 PostToolUse hook (PLAN.md §3.0).

Deterministic text rules that run before any model. Every kept content line stays byte-exact; the only
information dropped is SGR/OSC escapes (colours, titles; never in file views), \\r-overwritten progress redraws and
exact duplicate runs (which are counted). A rule is applied only if it saves chars AND approximate tokens;
json_unescape and grep_group also only if the whole output is estimated to shrink in tokens (token_delta, S1 gate).
Pure function of (text, command): no disk, cwd, env or clock, so identical input gives identical bytes
(prompt cache stays valid).

  compress(text, command=None) -> (new_text, stats)
  expand(text) -> text with grep grouping undone (exact inverse of the grouping rule)
  decode(text) -> lossless2 + grouping + marker escaping undone (escape_markers / unescape_markers: H2)

CLI: python3 lossless.py [--command "<bash command>"] [--stats] [--expand] < in > out
"""

from __future__ import annotations

import json
import os
import re as _re
import shlex
import sys


class _Lazy:
    """A pattern compiled on first use (hook / plugin startup: ~7 ms of regex compiling at import, canary 2026-10-18).
    The compiled object's methods are cached on the instance after the first lookup, so hot loops pay nothing extra."""

    def __init__(self, pattern, flags=0):
        self._p, self._f = pattern, flags

    def __getattr__(self, name):
        rx = self.__dict__.get("_rx")
        if rx is None:
            rx = self.__dict__["_rx"] = _re.compile(self._p, self._f)
        v = getattr(rx, name)
        if callable(v):
            self.__dict__[name] = v
        return v


class _LazyRe:
    """`re` for this module: re.compile(...) returns a _Lazy pattern; everything else is the real `re` (cached)."""

    @staticmethod
    def compile(pattern, flags=0):
        return _Lazy(pattern, flags)

    def __getattr__(self, name):
        v = getattr(_re, name)
        self.__dict__[name] = v
        return v


re = _LazyRe()

GROUP_MARK = "[cp: grouped by file]"                   # short `[cp` markers (tokenizer study 2026-10-17 §4)
TRUNC_MARK = "[cp: truncated JSON]"
# candidate layout (0.3.0 fix round): ripgrep's own `--heading` shape, a blank line between file blocks, so a reader
# sees the grep output it knows as complete (long-context think probe: the plain layout drew more re-greps). Only for
# text without empty lines; expand() drops the blank separators after this mark. GROUP_LAYOUT picks the one compress uses.
GROUP_MARK_RG = "[cp: grouped by file, as rg --heading]"
GROUP_MARKS = (GROUP_MARK, "[ctxprune: hits grouped by file]", GROUP_MARK_RG)    # older outputs still decode
GROUP_LAYOUT = "plain"
# Probe knobs (winback 2026-10-19, results/winback): GROUP_REPEAT repeats the file header every N lines (0 = once per run),
# GROUP_MIN_HITS is the smallest file run grouped, GROUP_MAX_FILES caps the distinct files per output (0 = no cap),
# GROUP_ON forces grouping on/off (None = the CTXPRUNE_HITS flag). Every redesign failed the long-context probe
# (lprobe-long3: header every 10 lines −5.3, runs >= 5 −7.6; lprobe-long4: single-file outputs only −5.1 pt right,
# Qwen3.6 no-think): without the per-line path the reader names the wrong file. So grouping stays opt-in and these
# defaults leave group_hits exactly as before.
GROUP_REPEAT = 0
GROUP_MIN_HITS = 2
GROUP_ON = None
# GROUP_MAX_FILES: group only outputs whose hit lines name at most this many distinct files (0 = no cap). Counted
# loosely over every `prefix:N:` / `prefix-N-` line (anything hit-shaped counts as one more file: fail closed).
GROUP_MAX_FILES = 0
_LOOSE_HIT = re.compile(r"^(.+?)([:-])\d+\2")
TRUNC_MARKS = (TRUNC_MARK, "[ctxprune: output looks truncated (JSON does not parse)]")

SGR_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;:]*m")   # colours / titles: no layout effect
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
ERASE_LINE = re.compile(r"\x1b\[[02]?K")
SPLIT_AT = re.compile(r"(?=([:-])(\d+)\1)")  # every (overlapping) `:N:` / `-N-` split point
HIT = re.compile(r"(\d+)([:-])")  # grouped hit line: LINE + sep
VIEWERS = {"cat", "head", "tail", "less", "more", "bat", "batcat", "sed", "nl", "tac", "view",
           "get-content", "gc", "type"}   # + PowerShell (Claude Code's PowerShell tool on Windows); compared lowercased
WRAPPERS = {"sudo", "env", "command", "builtin", "exec", "time", "nice", "nohup", "stdbuf"}
CTRL_OPS = {"|", "||", "&&", ";", "&", "|&", ";;", "(", ")", "\n"}
PIPE_OPS = {"|", "|&"}
MAX_REST = 1000  # max chars of sibling JSON kept in the unescape marker line

# H2 (security audit 2026-10-17): tool output must not be able to forge ctxprune markers. Every line that already
# looks like one (MARKER_PREFIX at its start, optionally after invisible chars: INVISIBLE) gets one more leading backslash;
# lines that start with backslashes + such a prefix too, so the escape is a bijection and unescape_markers undoes it
# exactly. "Line" = every splitlines() boundary (\r, \f, U+2028, ...), since the line model and recover split there.
# Every marker-shaped line the model sees is therefore one ctxprune wrote, and decoders only ever see those.
# MARKER_PREFIX is shared (note, decoders, tests): `[ctxprune` / `[/ctxprune` (any case), the short forms `[cp` /
# `[/cp` followed by `:` `]` `=` or a space (so `[cpu]` / `[cpp]` config sections stay untouched), and `[…` (gaps).
MARKER_PREFIX = r"\[(?:/?(?i:ctxprune)|/?(?i:cp)(?=[:\]= ])|…)"
# INVISIBLE: Unicode Default_Ignorable_Code_Point (zero-width chars, bidi marks / embeddings / isolates, soft hyphen,
# CGJ, variation selectors, tags). A reader sees `<U+2067>[cp:file]` as `[cp:file]`; RTL text carries these routinely
# (robustness audit 2026-10-17: only U+200B-200F / 2060 / FEFF were covered). Same class in lossless2.INVISIBLE and in
# the plugins' escapedOnly(). Visible lookalikes (fullwidth `［cp:`, NBSP / space indent) are not escaped: they look
# different, and every decoder matches ASCII `[cp` / `[ctxprune` exactly.
INVISIBLE = (r"\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164"
             r"\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff")
MARKER_LIKE = re.compile(r"^[" + INVISIBLE + r"]*" + MARKER_PREFIX)   # is this line marker-shaped?
_LINE_SEP = re.compile(r"(\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029])")
_MARKER_LIKE = re.compile(r"^([" + INVISIBLE + r"]*)(\\*)(" + MARKER_PREFIX + ")")
_ESCAPED = re.compile(r"^([" + INVISIBLE + r"]*)\\(\\*" + MARKER_PREFIX + ")")
_ESCAPED2 = re.compile(r"^([" + INVISIBLE + r"]*)\\\\(\\*" + MARKER_PREFIX + ")")
# U+0085 / U+2028 / U+2029 may stand raw inside a JSON string (control chars may not), so a marker after one can sit
# inside a string: there the escape adds TWO backslashes (`\\[` is a valid JSON escape, `\[` is not; red team 2), and
# the JSON stays valid (an even run of backslashes stays even). Same in the plugins' escapeMarkers / escapedOnly.
_IN_STRING_SEPS = ("\x85", "\u2028", "\u2029")


def _per_line(text: str, fn, fn2=None) -> str:
    """fn on every line; fn2 (if given) instead on lines after a separator that is legal inside a JSON string."""
    if "[" not in text:
        return text
    parts = _LINE_SEP.split(text)
    for k in range(0, len(parts), 2):   # even indices are lines, odd ones the separators
        if "[" in parts[k]:
            parts[k] = (fn2 if fn2 is not None and k and parts[k - 1] in _IN_STRING_SEPS else fn)(parts[k])
    return "".join(parts)


def escape_markers(text: str) -> str:
    """Neutralize marker-shaped lines already in tool output (reversible: unescape_markers)."""
    return _per_line(text, lambda ln: _MARKER_LIKE.sub(r"\1\\\2\3", ln, count=1),
                     lambda ln: _MARKER_LIKE.sub(r"\1\\\\\2\3", ln, count=1))


def unescape_markers(text: str) -> str:
    return _per_line(text, lambda ln: _ESCAPED.sub(r"\1\2", ln, count=1), lambda ln: _ESCAPED2.sub(r"\1\2", ln, count=1))


# Deliberately narrower than INVISIBLE: sanitizer input is untrusted prose, and broad
# Default_Ignorable removal corrupts Arabic marks, emoji variation selectors and joiners.
_UNTRUSTED_CONTROLS = _re.compile(r"[\U000E0000-\U000E007F\u202A-\u202E\u2066-\u2069]")
_HTML_COMMENT = _re.compile(r"<!--.*?-->", _re.S)


def sanitize_untrusted(text: str) -> tuple[str, dict]:
    """Strip invisible injection carriers from untrusted web/MCP text.

    This is opt-in at the integration boundary. ZWJ/ZWNJ, Arabic marks, variation
    selectors, and all other script shaping characters are preserved.
    """
    stripped_controls = len(_UNTRUSTED_CONTROLS.findall(text))
    stripped_comments = len(_HTML_COMMENT.findall(text))
    result = _HTML_COMMENT.sub("", _UNTRUSTED_CONTROLS.sub("", text))
    return result, {"invisible_controls": stripped_controls, "html_comments": stripped_comments}


# ---------------------------------------------------------------- command parsing

def _tokens(command: str | None) -> list[str]:
    if not command:
        return []
    try:
        lx = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)   # \n separates commands
        lx.whitespace_split = True
        return list(lx)
    except ValueError:
        return command.split()


def _base(tok: str) -> str:
    """Program name of a command token, the same on every OS: os.path.basename is ntpath on Windows (splits on `\\`)
    and posixpath elsewhere, so `C:\\bin\\cat` was a viewer on one OS only (determinism audit 2026-10-17). A Windows
    `.exe` suffix is dropped (`cat.exe`, `type.exe`, `gc.exe`, `git.exe`: red team 2)."""
    name = re.split(r"[\\/]", tok)[-1]
    return name[:-4] if name[-4:].lower() == ".exe" else name


def _pipelines(toks: list[str]) -> list[list[str]]:
    """Program names per pipeline: list operators (&&, ||, ;, &, newline) split pipelines, | splits stages."""
    pipes: list[list[str]] = [[]]
    start, after_wrapper = True, False
    for tok in toks:
        if tok in CTRL_OPS:
            if tok not in PIPE_OPS and pipes[-1]:
                pipes.append([])
            start, after_wrapper = True, False
            continue
        if not start:
            continue
        if re.match(r"^[A-Za-z_]\w*=", tok) or (after_wrapper and tok.startswith("-")):
            continue
        name = _base(tok)
        if name in WRAPPERS:
            after_wrapper = True
            continue
        pipes[-1].append(name)
        start, after_wrapper = False, False
    return [p for p in pipes if p]


# ---------------------------------------------------------------- noise

def strip_ansi(text: str, pat=ANSI) -> str:
    for _ in range(4):   # removing one sequence can expose another; bounded (nested input was quadratic)
        new = pat.sub("", text)
        if new == text:
            break
        text = new
    return text


def strip_sgr(text: str) -> str:
    """Only colour (SGR) and OSC (title / hyperlink) codes: cursor moves and erases are left as they are (dropping
    them unapplied changes what the terminal showed; red team #9)."""
    return strip_ansi(text, SGR_OSC) if "\x1b" in text else text


def _overlay(core: str) -> str:
    """Each segment overwrites the start of the line. In place: `s + buf[len(s):]` copied the whole buffer per
    segment, O(segments x line length) (red team 2: a 1 MB line, one long segment then 80k `1%` redraws, took 184 s)."""
    buf: list[str] = []
    for seg in core.split("\r"):
        erase = bool(ERASE_LINE.search(seg))
        s = strip_ansi(seg)
        if erase or len(s) >= len(buf):
            buf = list(s)
        else:
            buf[:len(s)] = s
    return "".join(buf)


PROGRESS = re.compile(r"\d+(\.\d+)?%|\(?\d+/\d+\)?|[█▏▎▍▌▋▊▉]|--:--:--")


def _progress_like(core: str) -> bool:
    """CR-separated text that is not a redraw (CR-only line endings, HTTP bodies) would be overlaid into a mix of
    fragments; only redraws are collapsed: an erase-line code, a progress counter, or segments sharing a 4-char
    prefix with the previous one (red team #4)."""
    if ERASE_LINE.search(core) or PROGRESS.search(core):
        return True
    segs = [strip_ansi(x) for x in core.split("\r") if x]
    return all(b[:4] == a[:4] for a, b in zip(segs, segs[1:]))


def collapse_cr(text: str) -> str:
    """Keep the final terminal state of each \\r-overwritten line. \\r\\n (and a trailing \\r) stay as is."""
    if "\r" not in text:
        return text
    out = []
    for ln in text.split("\n"):
        core, tail = (ln[:-1], "\r") if ln.endswith("\r") else (ln, "")
        if "\r" in core and _progress_like(core):
            core = _overlay(core)
        out.append(core + tail)
    return "\n".join(out)


def _dup_once(lines: list[str]) -> tuple[list[str], int]:
    out, runs, i = [], 0, 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        n = j - i + 1
        # a CRLF line keeps its \r last, so a second pass still sees one line (red team #5)
        tag = f"{lines[i][:-1]}  [× {n}]\r" if lines[i].endswith("\r") else f"{lines[i]}  [× {n}]"
        # only when it saves clearly more than the tag costs: a tag is ~4-5 tokens, so short repeated lines
        # ("True" x 3 -> "True  [× 3]") grew in tokens (wave C q36 2026-10-18, S1)
        if n >= 3 and len(lines[i].strip()) >= 8 and (len(lines[i]) + 1) * (n - 1) >= len(tag) - len(lines[i]) + 16:
            out.append(tag)
            runs += 1
        else:
            out.extend(lines[i:j + 1])
        i = j + 1
    return out, runs


DUP_TAG = re.compile(r"  \[× \d+\]\r?$")


def collapse_dups(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    if any(DUP_TAG.search(x) for x in lines):   # already tagged somewhere: a reader could not tell them apart (#6)
        return text, 0
    tail = []
    if len(lines) > 1 and lines[-1] == "":  # trailing newline is not a line
        lines, tail = lines[:-1], [""]
    total = 0
    while True:  # fixpoint, so compress is idempotent
        lines, runs = _dup_once(lines)
        if not runs:
            break
        total += runs
    return "\n".join(lines + tail), total


# ---------------------------------------------------------------- JSON

class _DupKey(ValueError):
    pass


def _pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _DupKey(k)
        d[k] = v
    return d


JSON_DEPTH = 200   # = lossless2.JSON_DEPTH: deeper JSON is not JSON to any rule (red team 2: RecursionError escaped
                   # compress at depth ~1000; json.loads' own limit is the C stack, which differs between Python builds)


def _too_deep(o, limit: int = JSON_DEPTH) -> bool:
    """= lossless2._too_deep (iterative)."""
    stack = [(o, 1)]
    while stack:
        x, d = stack.pop()
        if d > limit:
            return True
        vals = x.values() if isinstance(x, dict) else x
        stack.extend((v, d + 1) for v in vals if isinstance(v, (dict, list)))
    return False


def _deep_text(s: str, limit: int = JSON_DEPTH) -> bool:
    """JSON-ish text nests brackets (outside strings) deeper than `limit`."""
    depth, in_str, esc = 0, False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
            if depth > limit:
                return True
        elif ch in "}]":
            depth -= 1
    return False


def _open_at_end(s: str) -> bool:
    """True if s is a JSON prefix cut off inside an open container (no bracket mismatch, never closed, at most
    JSON_DEPTH deep)."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
            if len(stack) > JSON_DEPTH:
                return False
        elif ch in "}]":
            if not stack or stack.pop() != ("{" if ch == "}" else "["):
                return False
            if not stack:
                return False  # top level closed: failure is not a cut-off
    return bool(stack)


_POSS = "+" if sys.version_info >= (3, 11) else ""
_JSTR_WS = re.compile(r'("[^"\\]*' + _POSS + r'(?:\\.[^"\\]*' + _POSS + r')*' + _POSS + r'")|[ \t\n\r]+')
# ^ a JSON string unrolled (+ possessive on 3.11+): `"(?:[^"\\]|\\.)*"` kept a backtracking record per char, so a pretty
#   JSON with a 5 MB string cost 669 MB (red team 2). Same strings: compact() runs on valid JSON only.


def _compact(s: str) -> str:
    """Drop JSON whitespace outside strings; every token stays byte-exact (numbers, escapes, dup keys)."""
    return _JSTR_WS.sub(lambda m: m.group(1) or "", s)


def _largest_str(obj, path=()):
    best = (-1, None, None)
    if isinstance(obj, str):
        return (len(obj), path, obj)
    items = obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else ()
    for k, v in items:
        cand = _largest_str(v, path + (k,))
        if cand[0] > best[0]:
            best = cand
    return best


def _path_label(path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else p)
    return out


def _replace_leaf(obj, path):
    if not path:
        return None
    head, rest = path[0], path[1:]
    if isinstance(obj, dict):
        return {k: (_replace_leaf(v, rest) if k == head else v) for k, v in obj.items()}
    return [(_replace_leaf(v, rest) if i == head else v) for i, v in enumerate(obj)]


def _unescape(obj, s: str) -> str | None:
    if isinstance(obj, (dict, list)) and _too_deep(obj):   # _largest_str / _replace_leaf recurse
        return None
    n, path, leaf = _largest_str(obj)
    if leaf is None or "\n" not in leaf or re.search("[\ud800-\udfff]", leaf):
        return None
    if len(json.dumps(leaf, ensure_ascii=False)) < 0.5 * len(s):
        return None
    if not path:
        return "[ctxprune: unescaped JSON string]\n" + leaf
    rest = _replace_leaf(obj, path)
    label = _path_label(path)
    if len(path) == 1 and isinstance(obj, dict) and len(obj) == 1:  # the field was the whole object
        return f"[ctxprune: unescaped JSON string field {json.dumps(label, ensure_ascii=False)}]\n" + leaf
    rest_s = json.dumps(rest, separators=(",", ":"), ensure_ascii=False)
    if len(rest_s) > MAX_REST:
        return None
    return (f"[ctxprune: unescaped JSON string field {json.dumps(label, ensure_ascii=False)}; "
            f"rest (field shown as null): {rest_s}]\n" + leaf)


def json_rules(text: str, unescape: bool = True) -> tuple[str, str | None]:
    """Returns (text, rule fired or None). Rule is one of json_unescape / json_compact / json_truncated.
    unescape=False: as if json_unescape never applied (compress() re-runs this way when its token gate says no)."""
    s = text.strip()
    if not s or s[0] not in '{["':
        return text, None
    nl = "\n" if text.endswith("\n") else ""
    try:
        obj, dup = json.loads(s, object_pairs_hook=_pairs), False
    except _DupKey:
        obj, dup = None, True
    except (json.JSONDecodeError, RecursionError):   # RecursionError: deeper than this build's C stack (any depth
        # beyond JSON_DEPTH gets the same answer on every build: _open_at_end / _too_deep / _deep_text refuse it)
        if s[0] in "{[" and s.rsplit("\n", 1)[-1] not in TRUNC_MARKS and _open_at_end(s):
            return text + ("" if nl else "\n") + TRUNC_MARK + nl, "json_truncated"
        return text, None
    except ValueError:
        return text, None
    if (obj is None and _deep_text(s)) or (isinstance(obj, (dict, list)) and _too_deep(obj)):
        return text, None
    if not dup and unescape:
        new = _unescape(obj, s)
        if new is not None and len(new) + len(nl) < len(text):
            return new + nl, "json_unescape"
    if "\n" in s and s[0] in "{[":  # pretty-printed (raw newlines can only be whitespace in valid JSON)
        new = _compact(s) + nl
        if len(new) < len(text):
            return new, "json_compact"
    return text, None


# ---------------------------------------------------------------- grep / rg grouping

def _plausible(p: str) -> bool:
    return (bool(p) and not re.search(r"\s", p) and ("/" in p or "." in p or "\\" in p)
            and not any(ord(c) < 32 for c in p) and not re.match(r"\d+:", p))


MAX_SPLITS = 64   # split points per line before the first whitespace (real outputs: at most 46, red team 2 corpus)


def _parse_hits(lines: list[str], toks: set[str]) -> list[str | None]:
    """Per line: the path prefix if exactly one split point qualifies, else None. A prefix qualifies if it is a
    command token, or _plausible and a split prefix of at least 2 lines.
    Bounded: prefixes are kept as (line, offset) and counted by (length, hash) with an exact comparison, never stored
    (red team 2: 400 lines of `a.b:1:1:…` stored ~2k prefixes of ~2 KB each, 1.6 MB -> 843 MB and 62 s); a line with
    more than MAX_SPLITS split points is not a hit line (red team #1 capped the prefix length at PATH_MAX)."""
    maxtok = max(map(len, toks), default=0)
    offs: list[list[int]] = []
    groups: dict[tuple[int, int], list[list[int]]] = {}   # (k, hash(prefix)) -> [[representative line, count], ...]

    def entry(k: int, p: str):
        for e in groups.get((k, hash(p)), ()):
            if lines[e[0]].startswith(p):   # same length k: equal prefixes
                return e
        return None

    for i, ln in enumerate(lines):
        o: list[int] = []
        ws = re.search(r"\s", ln)
        # a prefix qualifies only if it is a command token or _plausible (no whitespace): later split points can't
        lim = max(maxtok, min(ws.start() if ws else len(ln), 4096))   # 4096 = PATH_MAX
        for m in SPLIT_AT.finditer(ln):
            if m.start() > lim:
                break
            if m.start() > 0:
                o.append(m.start())
                if len(o) > MAX_SPLITS:
                    break
        if len(o) > MAX_SPLITS:
            o = []
        offs.append(o)
        for k in o:                          # distinct offsets: distinct prefixes of this line
            p = ln[:k]
            e = entry(k, p)
            if e is not None:
                e[1] += 1
            else:
                groups.setdefault((k, hash(p)), []).append([i, 1])
    out: list[str | None] = []
    for ln, o in zip(lines, offs):
        first, nq = None, 0
        for k in o:
            p = ln[:k]
            if p in toks or (_plausible(p) and entry(k, p)[1] >= 2):
                nq += 1
                if nq > 1:
                    break
                first = p
        out.append(first if nq == 1 else None)
    return out


def expand(text: str, limit: int | None = None) -> str:
    """Exact inverse of the grouping rule. Identity on any text without the GROUP_MARK line. limit: raise
    lossless2.DecodeBudget instead of building more than that many chars (each hit line gets its path back)."""
    lines = text.split("\n")
    mark = next((m for m in GROUP_MARKS if m in lines), None)
    if mark is None:
        return text
    k = lines.index(mark)
    out, cur = lines[:k], None
    body = lines[k + 1:]
    if mark == GROUP_MARK_RG:   # blank separators are ours (the layout is only used on text without empty lines)
        body = [ln for j, ln in enumerate(body) if ln != "" or j == len(body) - 1]
    for i, ln in enumerate(body):
        if cur is not None:
            if ln == "--":
                out.append(ln)
                continue
            m = HIT.match(ln)
            if m:
                if limit is not None:
                    limit -= len(cur) + len(ln) + 2
                    if limit < 0:
                        from lossless2 import DecodeBudget
                        raise DecodeBudget
                out.append(cur + m.group(2) + ln)
                continue
            cur = None
        if ln != "--" and i + 1 < len(body) and HIT.match(body[i + 1]):
            cur = ln
            continue
        out.append(ln)
    return "\n".join(out)


def group_hits(text: str, command: str | None = None, layout: str | None = None, min_hits: int | None = None,
               repeat: int | None = None) -> tuple[str, int]:
    """layout: "plain" (GROUP_MARK, header + `N:text` lines) or "rg" (GROUP_MARK_RG, a blank line between blocks);
    None = GROUP_LAYOUT. min_hits / repeat: None = GROUP_MIN_HITS / GROUP_REPEAT (a repeated header is the same path
    line again; expand() reads it as a new run of the same file, so decode is unchanged)."""
    min_hits = max(2, GROUP_MIN_HITS if min_hits is None else min_hits)
    repeat = GROUP_REPEAT if repeat is None else repeat
    if GROUP_MAX_FILES:
        seen = set()
        for ln in text.split("\n"):
            m = _LOOSE_HIT.match(ln)
            if m:
                seen.add(m.group(1))
                if len(seen) > GROUP_MAX_FILES:
                    return text, 0
    lines = text.split("\n")
    if any(m in lines for m in GROUP_MARKS):
        return text, 0
    rg = (layout or GROUP_LAYOUT) == "rg"
    if rg and "" in lines[:-1]:
        return text, 0              # the blank separators must be unambiguous
    paths = _parse_hits(lines, set(_tokens(command)))
    if sum(p is not None for p in paths) < 2:
        return text, 0
    out, groups, i, n = [GROUP_MARK_RG if rg else GROUP_MARK], 0, 0, len(lines)
    rest = n - 1 if lines[-1] == "" else n   # lines after this index are only the trailing newline
    while i < n:
        p = paths[i]
        if p is None:
            out.append(lines[i])
            i += 1
            continue
        j, last, hits = i, i, 0
        while j < n and (paths[j] == p or (lines[j] == "--" and paths[j] is None)):
            if paths[j] == p:
                last, hits = j, hits + 1
            j += 1
        if hits >= min_hits:
            if rg and len(out) > 1 and out[-1] != "":
                out.append("")
            out.append(p)
            since, prev = 0, ""
            for ln in lines[i:last + 1]:
                if ln == "--":
                    out.append(ln)
                    prev = ln
                    continue
                # repeat the header once `repeat` lines have passed, at a block start (after `--`) or a match line
                # (`N:`), so a -C context block is not split
                if repeat and since >= repeat and (prev == "--" or ln[len(p)] == ":"):
                    out.append(p)
                    since = 0
                out.append(ln[len(p) + 1:])
                since += 1
                prev = ln
            if rg and last + 1 < rest:
                out.append("")
            groups += 1
        else:
            out.extend(lines[i:last + 1])
        i = last + 1
    new = "\n".join(out)
    if not groups or len(new) >= len(text) or expand(new) != text:
        return text, 0
    return new, groups


# ---------------------------------------------------------------- pipeline

def _json_shaped(text: str) -> bool:
    s = text.strip()
    if not s or s[0] not in "{[":
        return False
    if s.rsplit("\n", 1)[-1] in TRUNC_MARKS or _open_at_end(s):
        return True
    try:
        o = json.loads(s)
    except (ValueError, RecursionError):
        return False
    return not (isinstance(o, (dict, list)) and _too_deep(o))


GIT_OPT_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
SHELLS = {"bash", "sh", "zsh", "dash"}


def _git_view(rest: list[str]) -> bool:
    """git show / diff / blame / stash show / log -p: the output carries file content."""
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in GIT_OPT_ARG else 1
    if i >= len(rest):
        return False
    sub, more = rest[i], rest[i + 1:]
    stage = more[:next((k for k, t in enumerate(more) if t in CTRL_OPS), len(more))]
    return sub in ("show", "diff", "blame") or (sub == "stash" and stage[:1] == ["show"]) or \
        (sub == "log" and any(t in ("-p", "-u", "--patch") for t in stage))


def _first_stage_view(toks: list[str]) -> bool:
    """A viewer (or a content-showing git command) anywhere in the first stage of a pipeline: `nice -n 10 cat`,
    `sudo -u x cat`, `timeout 5 cat`, `ssh h cat`, `for f in *; do cat $f`, `bash -c "cat x"` (red team #3)."""
    stage_start = True
    for k, tok in enumerate(toks):
        if tok in PIPE_OPS:
            stage_start = False
            continue
        if tok in CTRL_OPS:
            stage_start = True
            continue
        if not stage_start:
            continue
        words = tok.split()
        if words and (_base(words[0]).lower() in VIEWERS or _base(tok).lower() in VIEWERS):
            return True
        if _base(tok) == "git" and _git_view(toks[k + 1:]):
            return True
    return False


_FILE_TOOLS = {"jq": {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2, "--indent": 1, "-L": 1},
               "gojq": {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2, "--indent": 1, "-L": 1},
               "yq": {"--arg": 2, "--indent": 1, "-I": 1}, "awk": {"-F": 1, "-v": 1}, "gawk": {"-F": 1, "-v": 1},
               "mawk": {"-F": 1, "-v": 1}, "nawk": {"-F": 1, "-v": 1}}
_PY_READ = re.compile(r"\.read\(\)|\.read_text\(|\.readlines\(\)")   # raw file text (json.load(open(..)) is no view)


def _tool_view(stage: list[str]) -> bool:
    """A pipeline's first stage that prints a FILE through another program, so an agent may Edit from its lines (red
    team 2: 150 real outputs got json_compact / jsonrows): `jq . x.json`, `jq '.a' x.json`, `awk 'NR<40' f.ts`,
    `awk '/^def/,/^$/' f.py`, `python3 -c "print(open('f').read())"`. A filter over stdin (`curl … | jq .`) is no
    view, and neither is a python -c that does not read a file's raw text."""
    for k, tok in enumerate(stage):
        name = _base(tok).lower()
        if re.match(r"^[A-Za-z_]\w*=", tok) or name in WRAPPERS:
            continue
        rest = stage[k + 1:]
        if re.match(r"^python[0-9.]*$", name):
            return "-c" in rest[:-1] and bool(_PY_READ.search(rest[rest.index("-c") + 1]))
        opt = _FILE_TOOLS.get(name)
        if opt is None:
            continue
        pos, i, prog_file = [], 0, False
        while i < len(rest):
            t = rest[i]
            if t == "--":
                pos += rest[i + 1:]
                break
            if t in ("--args", "--jsonargs"):          # jq: the remaining positionals are values, not files
                break
            if t == "-f" or t == "--from-file":
                prog_file, i = True, i + 2
                continue
            if t.startswith("-") and len(t) > 1:
                i += 1 + opt.get(t, 0)
                continue
            pos.append(t)
            i += 1
        return len(pos) >= (1 if prog_file else 2)   # the program / filter text, then at least one file operand
    return False


def _first_stages(toks: list[str]) -> list[list[str]]:
    stages, cur, first = [], [], True
    for t in toks + [";"]:
        if t in CTRL_OPS:
            if first and cur:
                stages.append(cur)
            first, cur = t not in PIPE_OPS, []
            continue
        cur.append(t)
    return stages


def view_kind(command: str | None) -> tuple[bool, bool]:
    """(view_first, view_any): output is file bytes (a pipeline starts with / first stage holds a viewer) | some
    viewer anywhere. The one detector for compress and ctxprune_core.is_view."""
    toks = _tokens(command)
    pipes = _pipelines(toks)
    first = any(p[0].lower() in VIEWERS for p in pipes) or _first_stage_view(toks) or \
        any(_tool_view(st) for st in _first_stages(toks))
    if not first and len(toks) >= 3 and _base(toks[0]) in SHELLS and toks[1] in ("-c", "-lc", "-ic", "-lic"):
        first = view_kind(toks[2])[0]   # `bash -lc "<script>"` as one string (Codex shell calls)
    return first, first or any(prog.lower() in VIEWERS for p in pipes for prog in p)


def is_view(command: str | None) -> bool:
    return view_kind(command)[0]


_TOK = re.compile(r"\\(?:u[0-9a-fA-F]{4}|.)| ?[^\W\d_]+| ?\d{1,3}|\s+|[^\w\s\\]+|_+")


def approx_tokens(s: str) -> int:
    """Cheap, deterministic o200k-like token estimate (no tiktoken in the hook's python, no BPE download in a hook;
    the same bytes on every machine): a word = 1 (+1 per 10 more letters), up to 3 digits = 1, a whitespace run = 1,
    an escape (\\n, \\", \\u00e9) = 1, a punctuation run = 1 per 4 chars. Only used to compare two encodings of the same content (red team #10)."""
    n = 0
    for m in _TOK.finditer(s):
        g = m.group()
        c = g[0] if g[0] != " " or len(g) == 1 else g[1]
        if c == "\\":            # a JSON/C escape (\n, \", \u00e9) is one token, like the char it stands for
            n += 1
        elif c.isalpha():
            n += 1 + (len(g) - 1) // 10
        elif c in "_" or not (c.isdigit() or c.isspace()):
            n += 1 + (len(g) - 1) // 4
        else:
            n += 1
    return n


def _better(new: str, old: str) -> bool:
    """A rule's output is used only if it is shorter in chars and not longer in (approximate) tokens."""
    return len(new) < len(old) and approx_tokens(new) <= approx_tokens(old)


# ---------------------------------------------------------------- token-delta estimate (Wave C S1 gate)
# json_unescape and grep_group are kept only if the WHOLE output is estimated to shrink in tokens on both an o200k-
# and a Qwen-style tokenizer, with a margin. approx_tokens can't do that: a 2% error on a 7k-token output swamps a
# +17 delta (red team 2: its gate lost 2,244 tokens of savings and still let outputs grow). This one estimates the
# DIFFERENCE: both texts are split with (close copies of) the tokenizers' own pre-tokenizer regexes, and only the
# pre-tokens that differ in count are costed. BPE runs per pre-token, so the shared ones cancel exactly; with exact
# per-pre-token costs this gives the real delta on 99.5% of the calibration pairs. Costs: _TD_EXACT holds the real
# ones for our own marker words and the ~500 punct / symbol runs that differ most often (`\",\"`, `",\n`, ` ✓`); the
# rest come from a small model (fitted on half the sessions of red team 2's 4,954 real json_unescape / 2,777 grep_group
# outputs, Hermes terminal envelopes and results/typical; o200k_base and Qwen3.8): `\n`+word = the word + 1, punct +
# newline merged on o200k but not on Qwen, rare symbols per UTF-8 byte.
_TD_UP = "A-Z\u00c0-\u00d6\u00d8-\u00de\u0391-\u03a9\u0400-\u042f"
_TD_C = r"(?:'[sStTmMdD]|'[rR][eE]|'[vV][eE]|'[lL][lL])"
_TD_ESC = r"(?<=\\)[nrtbfu](?=[^\W\d_])|"   # `...\` + `nfoo`: the escape letter on its own, so `foo` pairs with the raw one
_TD_PRE = (   # o200k_base, Qwen2/3 (\p{L} ~ [^\W\d_], \p{N} ~ \d; possessive = no per-char backtracking state)
    re.compile(_TD_ESC + r"(?:[^\r\n\w]|_)?(?:[" + _TD_UP + r"]*[^\W\d_" + _TD_UP + r"]+|[" + _TD_UP + r"]+[^\W\d_" +
               _TD_UP + r"]*)" + _TD_C + r"?|\d{1,3}| ?(?:[^\s\w]|_)+" + _POSS + r"[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+"),
    re.compile(_TD_ESC + _TD_C + r"|(?:[^\r\n\w]|_)?[^\W\d_]+|\d| ?(?:[^\s\w]|_)+" + _POSS +
               r"[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"))
_TD_WS = re.compile(r"\s+\Z")
_TD_DIG = re.compile(r"\d+\Z")
_TD_LET = re.compile(r"((?:[^\r\n\w]|_)?)([^\W\d_]+)((?:'\w+)?)\Z")
_TD_CYR = re.compile("[\u0400-\u04ff]")
_TD_CJK = re.compile("[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")
_TD_EXACT = {   # pre-token -> (o200k, Qwen3.8) tokens: our own markers + the most frequent punct / symbol runs
    **dict.fromkeys((
        ' "', ' """', ' "#', ' "$', ' ","', ' "-', ' "--', ' "---', ' ".', ' "./', ' "/', ' ">', ' "@', ' "\\',
        ' "^', ' "_"', ' \'"', " '\\", " '\\\\", ' (', ' ("', ' (\\', ' -', ' JSON', ' [', ' ["', ' [],', ' [{"',
        ' \\', ' \\"', ' \\"$', ' \\\\', ' a', ' as', ' b', ' by', ' characters', ' diff', ' ending', ' escapes',
        ' field', ' file', ' grouped', ' in', ' line', ' lines', ' null', ' omitted', ' one', ' repeat', ' rest',
        ' shown', ' string', ' they', ' w', ' where', ' {"', ' {\\', ' {}\\', ' |\\', ' }\\', ' ·', ' ×', ' –', ' —',
        ' ›', ' €', ' →', ' │', ' ○', ' ✓', '!\\', '"', '"\'', '")', '").', '",', '","', '"/></', '":', '":"',
        '":["', '":{"', '";', '">', '"><', '">{', '"\\', '"]', '"],', '"{', '"}', '"},', '#', '%', '%"', '%\\',
        "'\\", '("', '("--', '("/', '()\\', '(\\', '(\\"', ')', ')"', ')",', ').\\', ')/', '):', ');\\', ')\\',
        '+\\', ',', ',"', ',\\', ',\\"', '-', '-\\', '.', '."', '.",', '.","', '...\\', '.\\', '.\\"', '/', '/(',
        '/.', '/[', '/\\', ':', ':\\', ':\\\\', ';\\', '=', '="', '="#', '="-', '="/', '=\\', '=\\"', '=\\"#', '>',
        '>\\', '?"', '?\\', '@', 'XXXX', '[', '["', '[/', '\\"', '\\")', '\\",', '\\",\\', '\\",\\"', '\\":',
        '\\":\\"', '\\":{\\"', '\\">', '\\"><', '\\">\\', '\\"\\', '\\"]', '\\/', '\\\\', '\\\\/', '\\n', ']', ']",',
        ']/', ']\\', '`\\', 'content', 'cp', 'error', 'exit', 'field', 'git', 'null', 'output', 'r', 'u', 'x', '{"',
        '{\\', '{\\"', '|', '|\\', '}', '}\\', '€', '│', '✓',
    ), (1, 1)),
    **dict.fromkeys((
        ' "\n', ' """\n', ' (\n', ' ({\n', ' )\n', ' );\n', ' --\n', ' ...\n', ' />\n', ' =\n', ' >\n', ' TSV',
        ' [\n', ' []\n', ' [],\n', ' [];\n', ' \\\n', ' ]\n', ' ],\n', ' ^\n', ' {\n', ' {}\n', ' {},\n', ' |\n',
        ' |\n\n', ' }\n', ' })\n', ' }),\n', ' });\n', ' },\n', ' };\n', ' }}>\n', '!\n', '"\n', '")\n', '"))\n',
        '",\n', '":\n', '";\n', '">\n', '"]\n', '"],\n', '"]}\n', '"}\n', '%\n', "'\n", "')\n", "'),\n", "');\n",
        "',\n", "'.\n", "':\n", "';\n", "']\n", "'],\n", "'}\n", '(\n', '()\n', '())\n', '());\n', '(),\n', '():\n',
        '();\n', '({\n', ')\n', ')\n\n', ')\n\n\n', ')"\n', '))\n', '));\n', '),\n', ').\n', ').\n\n', '):\n',
        ');\n', '**\n', '+\n', ',\n', '-\n', '---\n', '---\n\n', '.\n', '.\n\n', '.\n\n\n', '."\n', '."""\n',
        '.",\n', ".',\n", '...\n', '/\n', "/',\n", ':\n', ':\n\n', ':file', ';\n', '=\n', '>\n', ">'.\n", '>;\n',
        '?\n', ']\n', '])\n', ']);\n', '],\n', ']:\n', '];\n', '__\n', '`\n', '`\n\n', '`)\n', '`.\n', '`.\n\n',
        '{\n', '|\n', '}\n', '}")\n', '}>\n', '}]\n', '…\n', '├',
    ), (1, 2)),
    **dict.fromkeys((
        ' ))}\n', ' }],\n', '":{\n', '[];\n', '|}\n',
    ), (1, 3)),
    **dict.fromkeys((
        '\x1b[', ' \'\\"', ' ...\\', ' />\\', ' [\\', '"/><', '":"/', '":[{"', '"],"', ')","', '):\\', '<"', '=\\"/',
        '\\_',
    ), (2, 1)),
    **dict.fromkeys((
        ' "---\\', ' "€', ' (\\"', ' ({\\', ' ))}\\', ' );\\', ' )\\', ' --\\', ' =\\', ' >\\', ' @@\n', ' @@\\',
        ' ["/', ' [\\"', ' [],\\', ' [];\\', ' []\\', ' [{\\"', ' \\"#', ' \\"-', ' \\"--', ' \\".', ' \\"./',
        ' \\"/', ' \\"@', ' \\"\\', ' \\"\\\\', ' \\"^', ' \\"€', ' \\\\\\', ' \\\\\\"', ' ],\\', ' ]\\', ' ^\\',
        ' unescaped', ' {\\"', ' {},\\', ' }),\\', ' });\\', ' })\\', ' },\\', ' };\\', ' }}>\\', ' │\n', ' │\\',
        ' ❯', '":[\n', '":["/', '":[],"', '">,', '"}],', '%\r', '%\\"', '%]\\', '%|\\', "'),\\", "');\\", "')\\",
        "',\\", "'.\\", "':\\", "';\\", "'],\\", "']\\", "'}\\", '());\\', '())\\', '(),\\', '():\\', '();\\',
        '(\\"--', '(\\"/', '({\\', ')\r', ')"/></', ')"><', '));\\', '))\\', '),\\', ')\\"', ')\\"><', ')\\"\\',
        ')\\\\', '**.\n', '**.\\', '**\\', '---\\', ".',\\", '.\\",', '.\\"\\', '.\\\\', '/","', "/',\\", ':**\n',
        ':**\\', ':cols', ':diff', ':dir', ':numbered', ':rows', '<\\"', '=\\"-', ">'.\\", '>;\\', '?\\"', '[\\"',
        '[];\\', '[cp', '\\"\'', '\\").', '\\")\\', '\\"/><', '\\"/></', '\\":\\', '\\":\\"/', '\\":{\\', '\\";',
        '\\";\\', '\\">,', '\\">{', '\\"\\\\', '\\"],', '\\"]\\', '\\"]}\\', '\\"{', '\\"}",', '\\"},', '\\"}\\',
        '\\"}],', '\\\\\\"', '\\\\_', ']);\\', '])\\', '],\\', ']:\\', '];\\', '__\\', '`)\\', '`.\\', '```\n',
        '```\n\n', '```\\', '|",', '|{"', '|{\\"', '|}\\', '}>\\',
    ), (2, 2)),
    **dict.fromkeys((
        '"\'.\n', '"}]\n', '%]\n', '|---\n', "|>'\n", "|>')\n", '|});\n', '|};\n',
    ), (2, 3)),
    **dict.fromkeys((
        ' \\",\\"', ' \\"\\"\\"', ' \\"\\"\\"\\', ' \\"_\\"', ' {\\\\\\"', ' }],\\', ')\\"/></', '.\\",\\',
        '.\\"\\"\\"\\', '=\\\\\\"', '\\"\'.\\', '\\"))\\', '\\":[\\', '\\":[\\"', '\\":[{\\"', '\\"],\\', '|---\\',
        "|>'\\", '|```\n', '|```\\', '|});\\', '|};\\', '}\\")\\',
    ), (3, 3)),
    **dict.fromkeys((
        "|>')\\",
    ), (4, 3)),
    **dict.fromkeys((
        '[ctxprune', '\\\\\\",\\\\\\"', '\\\\\\":\\\\\\"', '\\\\\\"}\\",',
    ), (4, 4)),
    **dict.fromkeys((
        ']⎯\n\n', ']⎯\\',
    ), (5, 5)),
    **dict.fromkeys((
        ' ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
    ), (9, 9)),
    **dict.fromkeys((
        '⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[',
    ), (73, 73)),
}
_TD_ONE = frozenset((   # other punct runs (newlines stripped) that are one token on both tokenizers
    ' "', ' "$', ' "_"', ' \'"', ' (', ' (\\', ' />', ' [', ' \\', ' \\"', ' \\"$', ' ],', ' {', ' {"', ' {\\', ' |',
    ' |\\', ' }', ' });', ' },', ' };', ' }\\', '"', '"\'', '")', '",', '","', '":', '":"', '";', '">', '"}', '%',
    '%\\', "'", "',", "'.", "';", "'\\", '(', '("', '()', '();', '()\\', '(\\', '(\\"', ')', '),', '):', ');', ');\\',
    ')\\', ',', ',"', ',\\', ',\\"', '-', '.', '."', '.",', '.\\', '/', '/\\', ':', ':\\', ';', ';\\', '=', '="',
    '=\\"', '>', '>\\', '\\"', '\\",', '\\",\\', '\\",\\"', '\\":', '\\":\\"', '\\">\\', '\\"\\', '\\\\', ']', ']:',
    ']\\', '`', '`\\', '```', '{"', '{\\"', '|', '|\\', '}', '}\\', '}]'))
_TD_KEYS = ("ws ws_nl4 ws_long dig lo lo_x cap cap_x up up_x mix mix_x cyr cyr_ch cjk cjk_ch na na_ch pre_sp pre_sp_x "
            "pre_p bs bs1 suf p1 p1bs p p_len p_bs p_q p_sp na2 na3 box na4 na_run p_nl1 p_nl").split()
_TD_THETA = tuple(dict(zip(_TD_KEYS, v)) for v in (   # o200k_base, Qwen3.8 (least squares on the pre-tokens not in
    (1.0, .55, 0., 1.0, 1.02, .24, 1.37, .21, 1.47, .72, 1.99, .21, .95, .25, 1.2, .7, 1.36, .21, .07, -.04,  # _TD_EXACT)
     .26, 1.05, 1.0, 0., .99, 1.76, 1.2, .09, .49, .03, -.05, .37, 2.19, .08, 1.73, 1.5, .17, .12),
    (1.0, .55, 0., 1.0, 1.01, .27, 1.16, .26, 1.44, .7, 2.26, .13, 1.02, .22, 1.6, .46, 1.48, .17, .08, -.02,
     .26, 1.05, 1.0, 0., 1.07, 1.64, 1.12, .1, .5, .02, .03, .33, 2.2, .13, 2.58, 1.37, .92, 1.14)))
# kept only if max over the two tokenizers of (est. delta + b*sqrt(differing pre-tokens not priced exactly)) <
# -(a + c*such non-ASCII symbol pre-tokens): the margins at which no calibration output grows on either tokenizer and
# the fewest non-growing ones are turned down (the commit messages have the curves)
S1_MARGIN = {"json_unescape": (2.0, 0.7, 0.25), "grep_group": (1.0, 0.3, 0.0), "dup": (1.0, 0.3, 0.0)}
S1_RULES = tuple(S1_MARGIN)


def _td_word(w: str, th: dict) -> float:
    if w.isascii():
        n = len(w)
        c = "lo" if w.islower() else "up" if n > 1 and w.isupper() else \
            "cap" if n == 1 or (w[0].isupper() and w[1:].islower()) else "mix"
        return th[c] + th[c + "_x"] * max(0, n - 6)
    n = len(w)
    s = "cyr" if 2 * len(_TD_CYR.findall(w)) >= n else "cjk" if 2 * len(_TD_CJK.findall(w)) >= n else "na"
    return th[s] + th[s + "_ch"] * (n - 1)


def _td_cost(p: str, th: dict) -> float:
    """Estimated tokens of one pre-token not in _TD_EXACT."""
    if _TD_WS.match(p):
        nl = p.count("\n") + p.count("\r")
        return th["ws"] + th["ws_nl4"] * max(0, nl - 3) + th["ws_long"] * max(0, len(p) - nl - 16)
    if _TD_DIG.match(p):
        return th["dig"]
    m = _TD_LET.match(p)
    if m:
        pre, w, suf = m.groups()
        if pre == "\\":           # `\n`, `\t`, `\u` + the word after the escape letter: priced as that word + 1
            c = th["bs1"] if len(w) == 1 else th["bs"] + _td_word(w[1:], th)
        else:
            c = _td_word(w, th)
            if pre == " ":
                c += th["pre_sp"] + th["pre_sp_x"] * max(0, len(w) - 6)
            elif pre:
                c += th["pre_p"]
        return c + (th["suf"] if suf else 0.0)
    core = p.rstrip("\r\n")
    nl = len(core) < len(p)
    if core in _TD_ONE:
        return th["p1"] + th["p_nl1"] * nl
    if core[-1:] == "\\" and core[:-1] in _TD_ONE:
        return th["p1bs"] + th["p_nl1"] * nl
    c2 = core.lstrip(" ")
    asc = 0
    c = th["p"] + th["p_bs"] * c2.count("\\") + th["p_q"] * c2.count('"') + th["p_sp"] * (len(c2) < len(core)) + \
        th["p_nl"] * nl
    prev = ""
    for ch in c2:
        if ch.isascii():
            asc += 1
        else:
            b = len(ch.encode("utf-8", "surrogatepass"))
            box = "\u2500" <= ch <= "\u259f"     # box drawing: long runs merge
            c += th["na2" if b == 2 else "box" if box else "na3" if b == 3 else "na4"]
            if box and ch != prev:
                c += th["na_run"]
            prev = ch
    return c + th["p_len"] * max(0, asc - 1)


def token_delta(old: str, new: str) -> tuple[tuple[float, int], tuple[float, int], int]:
    """((o200k est. tokens(new) - tokens(old), differing pre-tokens not priced exactly), (the same for Qwen), such
    non-ASCII symbol pre-tokens). Pure Python, deterministic (a fixed-order sum over a dict built in text order)."""
    from collections import Counter
    res, na = [], 0
    for i, (rx, th) in enumerate(zip(_TD_PRE, _TD_THETA)):
        c = Counter(rx.findall(new))
        c.subtract(rx.findall(old))
        d, n, a = 0.0, 0, 0
        for p, v in c.items():
            if v:
                x = _TD_EXACT.get(p)
                if x is not None:
                    d += v * x[i]
                    continue
                d += v * _td_cost(p, th)
                n += abs(v)
                if not p.isascii() and not p.strip("\n ").isalpha():
                    a += abs(v)
        res.append((d, n))
        na = max(na, a)
    return res[0], res[1], na


def tokens_pay(old: str, new: str, rule: str = "json_unescape") -> bool:
    """True when `new` is fewer tokens than `old` on both tokenizers by more than the rule's margin (S1_MARGIN)."""
    a, b, c = S1_MARGIN[rule]
    (do, no), (dq, nq), na = token_delta(old, new)
    return max(do + b * no ** 0.5, dq + b * nq ** 0.5) < -(a + c * na)


def grep_group_on() -> bool:
    """grep_group is opt-in with the lossless2 `hits` rule (CTXPRUNE_HITS=1): at 16-56k context a reader filed hits
    under the wrong file header (lprobe-long2, lossless2.FLAG_RULES). GROUP_ON forces it (probe item builders)."""
    if GROUP_ON is not None:
        return GROUP_ON
    return os.environ.get("CTXPRUNE_HITS", "") in ("1", "true", "yes")


def opt_in_rules() -> tuple[str, ...]:
    """lossless2 rules that are off by default (lossless2.FLAG_RULES: blockref, prefix, jsonrows_ext; the
    comprehension probes failed them) and switched on by their env flag (=1). Hook and proxy must
    see the same flags."""
    from lossless2 import FLAG_RULES
    return tuple(r for r, env in FLAG_RULES.items() if os.environ.get(env, "") in ("1", "true", "yes"))


# `paths` (dir-grouped path lists) costs self-hosted thinking readers +38 (Qwen3.8) / +149 (Qwen3.6) completion tokens
# per decode at a ~43% reuse rate (results/netcost, 2026-10-17): it pays on hosted API models, loses latency locally.
# The proxy / Hermes / Pi / OpenCode integrations pass the model's base URL or provider (`target`), fixed per session.
LOCAL_HOST = re.compile(r"^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|::1|\[::1\]|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|"
                        r"172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}|169\.254(\.\d{1,3}){2}|\[?f[cd][0-9a-f]{2}:[0-9a-f:]*\]?|"
                        r"[\w.-]+\.(local|lan|internal|home\.arpa))$", re.I)
LOCAL_PROVIDER = re.compile(r"(?i)(vllm|llama[ ._-]?cpp|ollama|lm[ ._-]?studio|sglang|llamafile|localai|koboldcpp|"
                            r"text-generation-inference|\btgi\b|\blocal\b|self[._-]?hosted)")


def local_target(target: str | None) -> bool:
    """A model endpoint on this machine / private network, or a self-hosted server's provider name."""
    t = (target or "").strip()
    if not t:
        return False
    from urllib.parse import urlsplit
    for part in t.split():
        try:
            host = urlsplit(part if "://" in part else "//" + part).hostname or ""
        except ValueError:
            host = ""
        if host and LOCAL_HOST.match(host):
            return True
    return bool(LOCAL_PROVIDER.search(t))


# `paths` (directory grouping, and sffiles, which needs it) is OFF by default for every model (0.3.0 fix round,
# 2026-10-18). Long-context probe (results/rented/lprobe-long): Qwen3.6 no-think -8.3 [-20.9, -0.5] pts, -11 at 56k and
# at 90% depth (right file name, wrong `./dir/` header); the short 1-6k probes were at 0 on every reader, so they do
# not predict it. No model has long-context paths data (Claude only short, at ceiling), and a Claude Code session is
# past 16k before its first tool output, so a size gate that is safe would never fire. What it gives up (results/
# typical): 0.18% of coding tokens, 0.49% assistant_api, 0.99% mcp_filesystem. Opt in: CTXPRUNE_PATHS=1 (every
# hosted and local target), or CTXPRUNE_PATHS_MODELS=claude-,gpt-6 (allow-listed hosted models only; prefix entries end
# in `-`). A model is added here only after a long-context probe passes on it.
PATHS_MODELS: tuple[str, ...] = ()


def paths_models() -> tuple[str, ...]:
    """PATHS_MODELS plus CTXPRUNE_PATHS_MODELS (comma list), read per call."""
    extra = tuple(m.strip() for m in os.environ.get("CTXPRUNE_PATHS_MODELS", "").split(",") if m.strip())
    return PATHS_MODELS + extra


def paths_on(target: str | None = None) -> bool:
    """CTXPRUNE_PATHS=0|1 wins; else on only for a hosted target whose model (a token of `target`: endpoint + model
    id, ctxprune_core.lossless_target) is allow-listed (paths_models(); empty by default). Local endpoints, other /
    unknown models: off. Deterministic in (env, target)."""
    v = os.environ.get("CTXPRUNE_PATHS", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    ms = paths_models()
    return bool(ms) and not local_target(target) and any(model_listed(t, ms) for t in (target or "").lower().split())


# `\`-separated (Windows) path lists: only for probed hosted models (ship-bytes probe 2026-10-18: Qwen3.6 no-think
# -11 pt on them). Model ids as they appear in the target string (provider / base URL / model); empty = off everywhere.
PATHS_WIN_MODELS: tuple[str, ...] = ()


def paths_win_on(target: str | None = None) -> bool:
    """CTXPRUNE_PATHS_WIN=0|1 wins; else only with `paths` on, a hosted target and an allow-listed model in it."""
    v = os.environ.get("CTXPRUNE_PATHS_WIN", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return bool(PATHS_WIN_MODELS) and paths_on(target) and not local_target(target) and \
        any(model_listed(t, PATHS_WIN_MODELS) for t in (target or "").lower().split())


def model_listed(token: str, models) -> bool:
    """A model id token (`claude-opus-5-5`, `anthropic/claude-opus-5.5`, `us.anthropic.claude-opus-…:0`) is in an
    allow-list: an entry ending in `-` is a prefix (`claude-opus-`); else an exact id or that id + `-`/`@`/`:` suffix."""
    tok = (token or "").lower()
    cands = {tok} | {tok[i + 1:] for i, c in enumerate(tok) if c in "/:."}
    for m in models:
        m = m.lower()
        for x in cands:
            if x == m or (m.endswith("-") and x.startswith(m)) or x.startswith((m + "-", m + "@", m + ":")):
                return True
    return False


def compress(text: str, command: str | None = None, *, paths: bool | None = None,
             paths_win: bool | None = None, rows: bool = True) -> tuple[str, dict]:
    """paths: the lossless2 `paths` rule (None = paths_on(): the env override, else allow-listed models only).
    rows: the jsonrows rule (False for local / self-hosted targets: nested record cells cost thinking readers +139 to
    +407 completion tokens per lookup for ~176 prompt tokens saved; lossless2._rows_block). A leading BOM (UTF-16 /
    UTF-8-with-BOM tool output, PowerShell) is set aside and put back, so JSON and first-line rules still see the text;
    decode() does the same."""
    if text.startswith("\ufeff"):
        out, st = _compress(text[1:], command, paths, paths_win, rows=rows)
        st["chars_before"], st["chars_after"] = len(text), len(out) + 1
        return "\ufeff" + out, st
    return _compress(text, command, paths, paths_win, rows=rows)


_UNESC_HEAD = re.compile(r"^\[ctxprune: unescaped JSON string(?: field .*)?\]$")


def own_encoding(text: str) -> bool:
    """Text that carries ctxprune's own structure (a pass of compress already ran): the unescaped-JSON header on line 1,
    a grouping mark, a truncation mark as the last line, or any lossless2 marker (lossless2._marked)."""
    if "[" not in text:
        return False
    lines = text.split("\n")
    if _UNESC_HEAD.match(lines[0]) or any(m in lines for m in GROUP_MARKS) or \
            (lines[-1] or (lines[-2] if len(lines) > 1 else "")) in TRUNC_MARKS:
        return True
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.append(_here)
    from lossless2 import _marked
    return _marked(text)


def _compress(text: str, command: str | None, paths: bool | None, paths_win: bool | None = None,
              skip: frozenset = frozenset(), rows: bool = True) -> tuple[str, dict]:
    """skip: S1_RULES the token gate already turned down for this text (the whole pass is re-run without them).
    A stacked second pass (hook + proxy without the shared emitted.py registry, red team 2) gets only the H2 escape:
    re-running cr / ansi / dup / json_unescape / sffiles on our own output was lossy. The escape keeps it exact
    (decode() of the second pass gives the first pass's text back, and decode() of that the original), and text that
    merely imitates our markers is escaped like any forged marker."""
    if not skip and own_encoding(text):
        t = escape_markers(text)
        return t, {"chars_before": len(text), "chars_after": len(t), "rules": ["esc"] if t != text else [],
                   "second_pass": True}
    view_first, view_any = view_kind(command)
    rules: list[str] = []
    counts: dict[str, int] = {}

    t = text
    if not view_first:  # before the SGR strip: the overlay needs erase-line codes
        t = collapse_cr(t)
        if t != text:
            rules.append("cr")
        u = strip_sgr(t)   # never on views: ESC bytes in a file are content
        if u != t:
            rules.append("ansi")
            t = u
    u = escape_markers(t)   # H2: after the noise steps (they can expose a new line start), before any rule
    if u != t:
        rules.append("esc")
        t = u
    if not view_any:
        new, rule = json_rules(t, unescape="json_unescape" not in skip)
        if rule:
            rules.append(rule)
            t = new
            if rule == "json_unescape":  # same noise steps on the decoded body
                u = collapse_cr(t)
                if u != t and "cr" not in rules:
                    rules.append("cr")
                v = strip_sgr(u)
                if v != u and "ansi" not in rules:
                    rules.append("ansi")
                head, nl0, body = v.partition("\n")   # line 1 is our own header; the decoded body is tool data
                body2 = escape_markers(body)
                if body2 != body and "esc" not in rules:
                    rules.append("esc")
                t = head + nl0 + body2
    if not view_first and not _json_shaped(t):  # JSON bodies (valid, compacted, cut off) stay as is
        u, runs = collapse_dups(t) if "dup" not in skip else (t, 0)
        if runs and _better(u, t):
            t = u
            rules.append("dup")
            counts["dup_runs"] = runs
        u, groups = group_hits(t, command) if "grep_group" not in skip and grep_group_on() else (t, 0)
        if groups and _better(u, t):
            t = u
            rules.append("grep_group")
            counts["grep_groups"] = groups
    # lossless2: run-local invertible transforms, each kept only if it decodes back exactly (file views: only the
    # cat -n padding rule, so code lines an agent copies into an edit are never replaced by references)
    # (JSON documents: only jsonrows, and never on a view)
    json_doc = _json_shaped(t)
    if not (json_doc and view_any):
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:   # -I drops the script dir; append once (never shadow the host's modules)
            sys.path.append(_here)
        from lossless2 import FLAG_RULES, JSON_RULES, TEXT_RULES, VIEW_RULES, encode as enc2
        on = opt_in_rules()
        if paths is None:
            paths = paths_on()
        if paths_win is None:
            paths_win = paths_win_on()
        keep = lambda r: (r not in FLAG_RULES or r in on) and (paths or r not in ("paths", "paths_win")) and \
            (paths_win or r != "paths_win") and (rows or not r.startswith(("jsonrows", "jsondefs")))
        text_rules = tuple(r for r in TEXT_RULES if keep(r))
        json_rules_ = tuple(r for r in JSON_RULES if keep(r))
        rs = json_rules_ if json_doc else tuple(r for r in VIEW_RULES if keep(r)) if view_first else text_rules
        t2, st2 = enc2(t, rs)              # token gate per rule inside encode (fewer chars, not more est. tokens)
        if st2["rules"]:
            rules += [f"l2_{r}" for r in st2["rules"]]
            t = t2
    # Wave C S1: json_unescape / grep_group may not leave the whole output longer in tokens (escapes like \\n are
    # 2 chars but often 1 token, a group header can cost more than the prefixes it saves). Judged on the final text, so
    # an unescape that only pays through the rules it unlocks on the body (dup, grouping, lossless2) is kept.
    gate = [r for r in S1_RULES if r in rules]
    if gate and not tokens_pay(text, t, gate[0]):
        return _compress(text, command, paths, paths_win, skip | {gate[0]}, rows)
    stats = {"chars_before": len(text), "chars_after": len(t), "rules": rules, **counts}
    if skip:
        stats["s1_gated"] = sorted(skip)
    return t, stats


def _drop_trunc_mark(text: str) -> str:
    """json_truncated only appends TRUNC_MARK as the last line (a forged one would be escaped), so it is undone by
    removing it: decode(compress(x)) == x for cut-off JSON, and a stacked second pass (hook + proxy) that escapes the
    first mark and appends a fresh one decodes back to the first pass's text."""
    if text.endswith("\n" + TRUNC_MARK + "\n"):
        return text[:-len(TRUNC_MARK) - 1]
    if text.endswith("\n" + TRUNC_MARK):
        return text[:-len(TRUNC_MARK) - 1]
    return text


def decode(text: str) -> str:
    """Inverse of the reversible part of compress (lossless2 transforms, grep grouping, marker escaping): what the
    tool printed after the noise/JSON/dup steps. Decoders only ever act on markers ctxprune wrote, never on marker-shaped
    tool text (that is escaped). Budget: an output past lossless2.decode_limit(len(text)) (4x the input, at least 64M
    chars) is never built; the input comes back unchanged (red team 2: crafted encodings reached 2.7 GB)."""
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.append(_here)
    from lossless2 import DecodeBudget, decode as dec2, decode_limit
    if text.startswith("\ufeff"):                 # set aside by compress()
        return "\ufeff" + decode(text[1:])
    raw, text = text, _drop_trunc_mark(text)
    try:   # global expansion budget: a crafted encoding returns the input unchanged instead of eating the memory
        return unescape_markers(expand(dec2(text, strict=True), limit=decode_limit(len(text))))
    except DecodeBudget:
        return raw
    except (ValueError, IndexError, OverflowError, MemoryError):   # malformed marker (never one we wrote): as is
        return unescape_markers(text)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--command", default=None, help="the Bash command that produced stdin")
    ap.add_argument("--stats", action="store_true", help="print stats JSON to stderr")
    ap.add_argument("--expand", action="store_true", help="undo grouping, lossless2 and marker escaping (decode)")
    a = ap.parse_args()
    text = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
    if a.expand:
        out, stats = decode(text), None
    else:
        out, stats = compress(text, a.command)
    sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    if a.stats and stats is not None:
        print(json.dumps(stats, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
