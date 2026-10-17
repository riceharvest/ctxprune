"""Lossless stage 0 for the ctxprune v2 PostToolUse hook (PLAN.md §3.0).

Deterministic text rules that run before any model. Every kept content line stays byte-exact; the only
information dropped is SGR/OSC escapes (colours, titles; never in file views), \\r-overwritten progress redraws and
exact duplicate runs (which are counted). A rule is applied only if it saves chars AND approximate tokens.
Pure function of (text, command): no disk, cwd, env or clock, so identical input gives identical bytes
(prompt cache stays valid).

  compress(text, command=None) -> (new_text, stats)
  expand(text) -> text with grep grouping undone (exact inverse of the grouping rule)
  decode(text) -> lossless2 + grouping + marker escaping undone (escape_markers / unescape_markers: H2)

CLI: python3 lossless.py [--command "<bash command>"] [--stats] [--expand] < in > out
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys

GROUP_MARK = "[cp: grouped by file]"                   # short `[cp` markers (tokenizer study 2026-10-17 §4)
TRUNC_MARK = "[cp: truncated JSON]"
GROUP_MARKS = (GROUP_MARK, "[ctxprune: hits grouped by file]")                  # older outputs still decode
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


def _per_line(text: str, fn) -> str:
    if "[" not in text:
        return text
    parts = _LINE_SEP.split(text)
    for k in range(0, len(parts), 2):   # even indices are lines, odd ones the separators
        if "[" in parts[k]:
            parts[k] = fn(parts[k])
    return "".join(parts)


def escape_markers(text: str) -> str:
    """Neutralize marker-shaped lines already in tool output (reversible: unescape_markers)."""
    return _per_line(text, lambda ln: _MARKER_LIKE.sub(r"\1\\\2\3", ln, count=1))


def unescape_markers(text: str) -> str:
    return _per_line(text, lambda ln: _ESCAPED.sub(r"\1\2", ln, count=1))


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
    and posixpath elsewhere, so `C:\\bin\\cat` was a viewer on one OS only (determinism audit 2026-10-17)."""
    return re.split(r"[\\/]", tok)[-1]


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
    buf: list[str] = []
    for seg in core.split("\r"):
        erase = bool(ERASE_LINE.search(seg))
        s = list(strip_ansi(seg))
        buf = s if erase else s + buf[len(s):]
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
        if n >= 3 and len(tag) + 1 < (len(lines[i]) + 1) * n:  # only when it saves chars
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


def _open_at_end(s: str) -> bool:
    """True if s is a JSON prefix cut off inside an open container (no bracket mismatch, never closed)."""
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
        elif ch in "}]":
            if not stack or stack.pop() != ("{" if ch == "}" else "["):
                return False
            if not stack:
                return False  # top level closed: failure is not a cut-off
    return bool(stack)


def _compact(s: str) -> str:
    """Drop JSON whitespace outside strings; every token stays byte-exact (numbers, escapes, dup keys)."""
    return re.sub(r'("(?:[^"\\]|\\.)*")|[ \t\n\r]+', lambda m: m.group(1) or "", s)


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


def json_rules(text: str) -> tuple[str, str | None]:
    """Returns (text, rule fired or None). Rule is one of json_unescape / json_compact / json_truncated."""
    s = text.strip()
    if not s or s[0] not in '{["':
        return text, None
    nl = "\n" if text.endswith("\n") else ""
    try:
        obj, dup = json.loads(s, object_pairs_hook=_pairs), False
    except _DupKey:
        obj, dup = None, True
    except json.JSONDecodeError:
        if s[0] in "{[" and s.rsplit("\n", 1)[-1] not in TRUNC_MARKS and _open_at_end(s):
            return text + ("" if nl else "\n") + TRUNC_MARK + nl, "json_truncated"
        return text, None
    except (ValueError, RecursionError):
        return text, None
    if not dup:
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


def _parse_hits(lines: list[str], toks: set[str]) -> list[str | None]:
    """Per line: the path prefix if exactly one split point qualifies, else None."""
    cands = []
    count: dict[str, int] = {}
    maxtok = max(map(len, toks), default=0)
    for ln in lines:
        cs = []
        ws = re.search(r"\s", ln)
        # a prefix qualifies only if it is a command token or _plausible (no whitespace): later split points can't,
        # and storing every prefix of a long line is O(splits x length) memory (red team #1: a 2.4 MB output > 3.5 GB)
        lim = max(maxtok, min(ws.start() if ws else len(ln), 4096))   # 4096 = PATH_MAX
        for m in SPLIT_AT.finditer(ln):
            if m.start() > lim:
                break
            if m.start() > 0:
                cs.append(ln[:m.start()])
        cands.append(cs)
        for p in set(cs):
            count[p] = count.get(p, 0) + 1
    out = []
    for cs in cands:
        q = [p for p in cs if p in toks or (_plausible(p) and count[p] >= 2)]
        out.append(q[0] if len(q) == 1 else None)
    return out


def expand(text: str) -> str:
    """Exact inverse of the grouping rule. Identity on any text without the GROUP_MARK line."""
    lines = text.split("\n")
    mark = next((m for m in GROUP_MARKS if m in lines), None)
    if mark is None:
        return text
    k = lines.index(mark)
    out, cur = lines[:k], None
    body = lines[k + 1:]
    for i, ln in enumerate(body):
        if cur is not None:
            if ln == "--":
                out.append(ln)
                continue
            m = HIT.match(ln)
            if m:
                out.append(cur + m.group(2) + ln)
                continue
            cur = None
        if ln != "--" and i + 1 < len(body) and HIT.match(body[i + 1]):
            cur = ln
            continue
        out.append(ln)
    return "\n".join(out)


def group_hits(text: str, command: str | None = None) -> tuple[str, int]:
    lines = text.split("\n")
    if any(m in lines for m in GROUP_MARKS):
        return text, 0
    paths = _parse_hits(lines, set(_tokens(command)))
    if sum(p is not None for p in paths) < 2:
        return text, 0
    out, groups, i, n = [GROUP_MARK], 0, 0, len(lines)
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
        if hits >= 2:
            out.append(p)
            out.extend(ln if ln == "--" else ln[len(p) + 1:] for ln in lines[i:last + 1])
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
        json.loads(s)
        return True
    except (ValueError, RecursionError):
        return False


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


def view_kind(command: str | None) -> tuple[bool, bool]:
    """(view_first, view_any): output is file bytes (a pipeline starts with / first stage holds a viewer) | some
    viewer anywhere. The one detector for compress and ctxprune_core.is_view."""
    toks = _tokens(command)
    pipes = _pipelines(toks)
    first = any(p[0].lower() in VIEWERS for p in pipes) or _first_stage_view(toks)
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


def paths_on(target: str | None = None) -> bool:
    """CTXPRUNE_PATHS=0|1 wins; else on unless the target is local / self-hosted. Deterministic in (env, target)."""
    v = os.environ.get("CTXPRUNE_PATHS", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return not local_target(target)


def compress(text: str, command: str | None = None, *, paths: bool | None = None) -> tuple[str, dict]:
    """paths: the lossless2 `paths` rule (None = paths_on(): the env override, else on)."""
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
        new, rule = json_rules(t)
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
        u, runs = collapse_dups(t)
        if runs and _better(u, t):
            t = u
            rules.append("dup")
            counts["dup_runs"] = runs
        u, groups = group_hits(t, command)
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
        text_rules = tuple(r for r in TEXT_RULES if (r not in FLAG_RULES or r in on) and (paths or r != "paths"))
        json_rules_ = tuple(r for r in JSON_RULES if (paths or r != "paths") and (r not in FLAG_RULES or r in on))
        rs = json_rules_ if json_doc else VIEW_RULES if view_first else text_rules
        t2, st2 = enc2(t, rs)              # token gate per rule inside encode (fewer chars, not more est. tokens)
        if st2["rules"]:
            rules += [f"l2_{r}" for r in st2["rules"]]
            t = t2
    stats = {"chars_before": len(text), "chars_after": len(t), "rules": rules, **counts}
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
    tool text (that is escaped)."""
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.append(_here)
    from lossless2 import decode as dec2
    text = _drop_trunc_mark(text)
    try:
        return unescape_markers(expand(dec2(text)))
    except (ValueError, IndexError, OverflowError, MemoryError):   # malformed marker (never one we wrote): as is
        return unescape_markers(text)


def main() -> int:
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
