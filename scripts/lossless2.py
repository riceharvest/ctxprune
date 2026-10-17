#!/usr/bin/env python3
"""Lossless stage 0, v2 (user 2026-10-17: "lossless should find as many lossless things as possible"). Run-local,
invertible transforms; every transform is applied to an output only if its decoder reproduces that output EXACTLY
(decode(encode(x)) == x is checked per rule against the ORIGINAL text, not assumed), so a misdetection can only cost
savings, never bytes. Output is never longer than the input, and a pure function of (text, rules): deterministic.

Line rules (each touches only maximal runs it recognizes; all other lines stay byte-exact):
  a11y      a ```yaml block whose every line is a `- item` YAML list line and that holds `[ref=...]` refs (Playwright
            MCP accessibility snapshot) -> the `- ` after each indent dropped and ` [cursor=pointer]` shortened to ` ↗`
            (only when the block has no `↗` of its own), under one header line inside the block:
            `[cp: each line is a "- " YAML list item, dash omitted; ↗ = [cursor=pointer]]`. Refs stay byte-exact
            (agents pass them back to click). `[cp: ` + space: dedup does not take it for a fence. Runs first, so the
            other rules (blockref) see the stripped lines; decoded last.
  hits      runs of >= 3 consecutive grep-style lines `PATH:N:text` / `PATH-N-text` (grep -n/-rn, rg -n, context lines)
            from one file -> a `PATH` header line + `N:text` / `N-text` lines. A run needs at least one `:N:` line
            (`dir/name-2026-10-17.md` alone is a file name, not "line 10").
  paths     runs of >= 4 consecutive bare paths (no spaces) in one directory -> `[cp:dir]`, `DIR/` + ` NAME` lines
  numbered  >= 12 lines of a padded line-number gutter (`cat -n`, `nl`, `     1→`, `00001| `) -> `[cp:numbered w=6]`
            (` zero` for zero padding) + the numbers unpadded
  table     >= 12 rows of a left-aligned table (ps, docker ps, kubectl get, df) -> `[cp:cols 0 15 30]` + fields
            single-spaced; columns re-padded on decode
  prefix    (opt-in: CTXPRUNE_PREFIX=1) >= 3 consecutive lines sharing a prefix that ends in a delimiter (`: `, ` | `, `| `, `] `, tab), e.g.
            `docker compose logs` (`web-1  | `) -> `[cp:each line starts with "web-1  | "]` + the rest of each line
  diffhdr   git diff / show: `--- a/X` + `+++ b/Y` right after `diff --git a/X b/Y` (they repeat it) are dropped; the
            output's first line says so: `[cp:diff: ---/+++ lines omitted where they repeat diff --git]`
  blockref  (replaces dupref; opt-in: CTXPRUNE_BLOCKREF=1, see FLAG_RULES) a run of lines that repeats an earlier run of THIS output, as displayed ->
            `[cp:= same as line N]` / `[cp:= same as lines A–B]` (back-to-back repeats: `..., repeated K times]`;
            >= 40 chars and the token gate: break-even on o200k/Qwen/DeepSeek/Gemma). N / A–B count the lines as shown
            (the encoded text), so the target can be found by counting. Targets and references are plain lines outside
            every fence; diff/show output gets no blockref (agents copy those lines into edits). Applied last.
Whole-output rules (exclusive: when one fires, no line rule runs):
  jsonrows  a JSON document (exactly re-serializable by json.dumps) holding arrays of >= 3 records (objects whose
            keys follow one common order; a record may lack keys) -> the document with each array replaced by
            `[cp:rows #N]`, then per array a header `[cp:rows #N TSV path, line(json), text; grouped by path]` and one
            tab-separated row per record. String columns (present in every record, no tab/newline) show the strings
            raw; `(json)` columns hold compact JSON literals, and an EMPTY `(json)` cell means the key is absent in that
            record. Opt-in (jsonrows_ext, CTXPRUNE_JSONROWS_EXT=1): a sub-object present in every record is flattened
            one level into `key.sub` columns, and keys that are not plain words are written as JSON strings
            (`fields."Page Name"`); without it such sub-objects stay `(json)` cells and such arrays stay JSON. A repeating first column is grouped like
            `rg --heading` (its value on its own line, then the rows without it). Outermost arrays first; each array
            only if its block costs fewer estimated tokens. Exact bytes (key order, numbers, spacing) or nothing.
  sffiles   Hermes search_files files-mode {"files": [...], ...} (>= 3 paths) -> the JSON with the list replaced by
            `[cp:files below, one per line]`, then the paths one per line (grouped by the `paths` rule). Not on an
            object with an error / success / status key. Result is no longer JSON (callers that json.loads it, e.g.
            Hermes' execute_code RPC, must not get it: the Hermes plugin keeps JSON for calls without a tool_call_id).
  hread     (opt-in, not in TEXT_RULES) Hermes read_file JSON {"content": "     1|...", ...} -> the JSON with content
            replaced by `[cp:content below]`, then the content raw with the `numbered` rule. NOTE: the Hermes plugin's
            read_file path already does the same through view_safe.wrapper_unescape (unescape + numbered, siblings in a
            header line); switching the plugin to hread is not a one-line change, so it is not wired there.
Each changed run is fenced by a marker line naming its decoder, so the model (and decode()) know the rule. Every marker
starts with `[cp` (fences end with `[/cp]`); the older `[ctxprune:file]` ... `[/ctxprune]` forms still decode.

API: encode(text, rules=TEXT_RULES) -> (text, stats) ; decode(text) -> original
     VIEW_RULES / TEXT_RULES / JSON_RULES: what lossless.compress passes for file views / other text / JSON documents
CLI: --self-test | --measure CORPUS.jsonl [LIMIT]
"""

from __future__ import annotations

import json
import re
import sys

HIT = re.compile(r"^((?:[\w.@~+-]|/(?!/))*[\w.@~+-][^\s:]*?)([:-])(\d+)\2(.*)$")   # reference only: see _hit_match
PATHLINE = re.compile(r"^(?:\./|~/|/)?(?:[\w.@+-]+/)+[\w.@+-]+/?$")
# Markers (tokenizer study 2026-10-17 §4): one shared prefix `[cp` / `[/cp]`, so the H2 escaper and the note need one
# pattern. The older `[ctxprune...]` forms still decode (outputs written by earlier versions), each with its own end.
HB, PB, END = "[cp:file]", "[cp:dir]", "[/cp]"
HB_OLD, PB_OLD, END_OLD = "[ctxprune:file]", "[ctxprune:dir]", "[/ctxprune]"
END_CP = END
ENDS = (END_OLD, END)
NB, NB_OLD = "[cp:cat-n]", "[ctxprune:cat-n]"
NUMB_RE = re.compile(r"^\[cp:numbered w=(\d{1,3})( zero)?\]$")   # padded line-number gutter, unpadded
NUMB_OLD = re.compile(r"^\[ctxprune:numbered w=(\d{1,3}) pad=(space|zero)\]$")
NUMLINE = re.compile(r"^( *)(\d+)(\t|→|\||: |:)(.*)$")
TB_RE = re.compile(r"^\[cp:cols ((?:\d{1,5} )+\d{1,5})\]$")   # aligned table: fields single-spaced, columns re-padded
TB_OLD = re.compile(r"^\[ctxprune:table cols=((?:\d{1,5},)+\d{1,5})\]$")
NUMB_MIN = TABLE_MIN = 12   # rows: the tokenizer study's break-even (fence cost vs 1-2 tokens saved per line)
PFX_RE = re.compile(r'^\[cp:each line starts with (".*")\]$')
BR_RE = re.compile(r"^\[cp:= same as lines? (\d{1,7})(?:–(\d{1,7}))?(?:, repeated (\d{1,6}) times)?\]$")   # blockref
DUPREF = re.compile(r"^\[ctxprune:= line (\d{1,7}): .*…\]$")   # legacy dupref: output line N (1-based, DECODED)
DUPREF_MIN = 60   # constant: output bytes must not depend on the environment (red team #8)
BR_MIN_CHARS = 40   # blockref: a referenced run carries at least this many chars (+ the token gate)
BR_MARGIN = 4       # blockref token gate: estimated run tokens >= marker tokens + this
CATN = re.compile(r"^( *)(\d+)\t(.*)$")
DH_NOTE = "[cp:diff: ---/+++ lines omitted where they repeat diff --git]"
DG = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
DIFFY = re.compile(r"^(diff --git |@@ -\d+(,\d+)? \+\d+(,\d+)? @@)", re.M)
LEGEND_HEAD = "[ctxprune lossless: "
MARKERS = {HB, PB, END, NB, HB_OLD, PB_OLD, END_OLD, NB_OLD}
# a line that is (or, after a reversible backslash escape, was) marker-shaped; same family as lossless.MARKER_PREFIX
MARKERISH = re.compile(r"^[​-‏⁠﻿]*\\*\[(?:/?(?i:ctxprune)|/?(?i:cp)(?=[:\]= ])|…)")
_OWN = re.compile(r"^\[(?:/?ctxprune|/?cp(?=[:\]= ]))")        # an unescaped marker line ctxprune would write
_BREAKS = re.compile(r"[\r\x0b\x0c\x1c-\x1e\x85  ]")   # splitlines() boundaries other than \n


# ---------------------------------------------------------------- token estimate (no tokenizer dependency)

_EST = re.compile(r" ?[A-Za-z]+|\d{1,3}|\n+|(.)\1{3,}|[^\sA-Za-z\d]{1,2}|\s+|[^\x00-\x7f]")   # newline, then indent


def _est(s: str) -> int:
    """Rough BPE token count (o200k-like: words with a leading space, 1-3 digit groups, punctuation pairs, whitespace
    runs, repeated-char runs ~16 per token). Calibrated on public tool output: sum within ~0.95-1.2x of o200k / Qwen /
    Gemma. Used only to keep rules from firing where fewer chars cost more tokens (separator lines, short tables)."""
    n = 0
    for m in _EST.finditer(s):
        g = m.group(0)
        if m.group(1):
            n += 1 + len(g) // 16
        elif g[-1].isalpha():
            n += 1 + (len(g.strip()) - 1) // 7
        else:
            n += 1
    return n


def _est_lines(lines: list[str]) -> int:
    return _est("\n".join(lines)) + 1


# ---------------------------------------------------------------- hits

_W = re.compile(r"[\w.@~+-]")
_APREFIX = re.compile(r"(?:[\w.@~+-]|/(?!/))*")
_STOP = re.compile(r"[\s:]")
_SPLIT = re.compile(r"(?=([:-])(\d+)\1)")
_DIGITS = re.compile(r"\d+")


def _hit_match(ln: str):
    """Linear-time equivalent of HIT.match(ln).groups() (HIT backtracks O(n^2) on long lines without a hit: 2.7 s at
    16k chars, 10.7 s at 32k; red team #2). Same answer: the regex tries the greedy star from its longest length
    down, so the path ends at the first split point after the LAST word char s that both lies in the
    [\\w.@~+-]/single-slash prefix and precedes some split point before the first whitespace/colon."""
    m_len = _APREFIX.match(ln).end()
    if m_len == 0:
        return None
    st = _STOP.search(ln)
    f = st.start() if st else len(ln)
    cands = []
    for m in _SPLIT.finditer(ln, 1):
        if m.start() > f:
            break
        cands.append(m.start())
    if not cands:
        return None
    s = min(m_len, cands[-1]) - 1
    while s >= 0 and not _W.match(ln, s):
        s -= 1
    if s < 0:
        return None
    q = next(c for c in cands if c >= s + 1)
    d = _DIGITS.match(ln, q + 1)
    return ln[:q], ln[q], d.group(0), ln[d.end() + 1:]


def _hit(ln: str):
    g = _hit_match(ln)
    if not g:
        return None
    if "/" not in g[0] and "." not in g[0]:      # bare words like `error-12-x` are not file hits
        return None
    return g


def _enc_hits(lines: list[str]) -> list[str]:
    out, i, n = [], 0, len(lines)
    while i < n:
        h = _hit(lines[i])
        if h is None:
            out.append(lines[i]); i += 1; continue
        j = i
        while j < n and (_hit(lines[j]) is not None or lines[j] == "--"):
            j += 1
        while j > i and lines[j - 1] == "--":
            j -= 1
        run = lines[i:j]
        parsed = [None if x == "--" else _hit(x) for x in run]
        if sum(1 for x in parsed if x) < 3 or not any(x and x[1] == ":" for x in parsed):
            out.extend(run); i = j; continue      # no `:N:` line: date-named paths, not hits (red team readability)
        block, cur = [HB], None
        for ln, h in zip(run, parsed):
            if h is None:
                block.append("--"); cur = None; continue
            p, sep, num, rest = h
            if p != cur:
                block.append(p); cur = p
            block.append(f"{num}{sep}{rest}")
        block.append(END)
        if sum(map(len, block)) + len(block) < sum(map(len, run)) + len(run) - 8:
            out.extend(block)
        else:
            out.extend(run)
        i = j
    return out


# ---------------------------------------------------------------- paths

def _dir(p: str) -> tuple[str, str] | None:
    q = p[:-1] if p.endswith("/") else p
    k = q.rfind("/")
    if k <= 0:
        return None
    return p[:k + 1], p[k + 1:]


def _enc_paths(lines: list[str]) -> list[str]:
    out, i, n = [], 0, len(lines)
    while i < n:
        if not PATHLINE.match(lines[i]) or _dir(lines[i]) is None:
            out.append(lines[i]); i += 1; continue
        j = i
        while j < n and PATHLINE.match(lines[j]) and _dir(lines[j]) is not None:
            j += 1
        run = lines[i:j]
        if len(run) < 4:
            out.extend(run); i = j; continue
        block, cur = [PB], None
        for ln in run:
            d, name = _dir(ln)
            if d != cur:
                block.append(d); cur = d
            block.append(" " + name)
        block.append(END)
        if sum(map(len, block)) + len(block) < sum(map(len, run)) + len(run) - 8 \
                and _est_lines(block) < _est_lines(run):          # token gate: fewer chars can still cost more tokens
            out.extend(block)
        else:
            out.extend(run)
        i = j
    return out


# ---------------------------------------------------------------- line-number gutters

def _enc_catn(lines: list[str]) -> list[str]:
    """Runs of >= 5 `cat -n` lines (number right-aligned to width 6, then a tab) -> numbers unpadded (decode only now)."""
    out, i, n = [], 0, len(lines)

    def ok(ln):
        m = CATN.match(ln)
        return m is not None and len(m.group(1)) + len(m.group(2)) == 6
    while i < n:
        if not ok(lines[i]):
            out.append(lines[i]); i += 1; continue
        j = i
        while j < n and ok(lines[j]):
            j += 1
        if j - i >= 5:
            out.append(NB)
            out.extend(f"{CATN.match(x).group(2)} {CATN.match(x).group(3)}" for x in lines[i:j])   # space: cheaper than tab
            out.append(END)
        else:
            out.extend(lines[i:j])
        i = j
    return out


def _enc_numbered(lines: list[str]) -> list[str]:
    """Runs of >= 5 lines with a fixed-width, padded line-number gutter (`cat -n`/`nl -ba` "     1\t", Claude Code
    Read "     1→", zero-padded "00001| ") -> numbers without padding; the fence records width + pad character, so the
    decoder re-pads exactly. Code text after the separator is untouched (safe for edits)."""
    out, i, n = [], 0, len(lines)

    def parse(ln):
        m = NUMLINE.match(ln)
        if not m:
            return None
        sp, digits = m.group(1), m.group(2)
        if sp and digits[0] != "0":
            return "space", len(sp) + len(digits), digits, m.group(3), m.group(4)
        if not sp and len(digits) > 1 and digits[0] == "0":
            return "zero", len(digits), digits.lstrip("0") or "0", m.group(3), m.group(4)
        return None

    while i < n:
        p = parse(lines[i])
        if p is None:
            out.append(lines[i]); i += 1; continue
        kind, width = p[0], p[1]
        j, block = i, []
        while j < n:
            q = parse(lines[j])
            if q is None or q[0] != kind or q[1] != width:
                break
            block.append(f"{q[2]}{q[3]}{q[4]}")
            j += 1
        fence = f"[cp:numbered w={width}{' zero' if kind == 'zero' else ''}]"
        if len(block) >= NUMB_MIN and sum(map(len, lines[i:j])) - sum(map(len, block)) - len(fence) - len(END) >= 40:
            out.append(fence)
            out.extend(block)
            out.append(END)
        else:
            out.extend(lines[i:j])
        i = j
    return out


# ---------------------------------------------------------------- tables

def _cols(header: str) -> list[int] | None:
    """Field start positions of an aligned header: position 0 (if non-space) + every non-space after >= 2 spaces."""
    if not header or header[0] == " ":
        return None
    cols = [0] + [m.end() for m in re.finditer(r"(?<=\S) {2,}(?=\S)", header)]
    return cols if len(cols) >= 3 else None


def _split_fixed(ln: str, cols: list[int]) -> list[str] | None:
    """Split a left-aligned row at the column starts; None if the row doesn't fit the layout exactly."""
    out = []
    for j, c in enumerate(cols):
        end = cols[j + 1] if j + 1 < len(cols) else len(ln)
        if c > len(ln):
            return None
        chunk = ln[c:end]
        if j + 1 < len(cols):
            field = chunk.rstrip(" ")
            if not field or " " in field or (end < len(ln) and ln[end - 1] != " " and len(field) == end - c):
                return None   # a field that fills its slot completely (no separator) or contains a space: ambiguous
            if c > 0 and ln[c - 1] != " ":
                return None
        else:
            field = chunk
        out.append(field)
    return out


def _render_fixed(fields: list[str], cols: list[int]) -> str:
    s = ""
    for j, f in enumerate(fields):
        if j + 1 < len(cols):
            s += f.ljust(cols[j + 1] - cols[j])
        else:
            s += f
    return s


def _enc_table(lines: list[str]) -> list[str]:
    """Runs of >= 4 rows of a left-aligned table (header defines the columns; ps, docker ps, kubectl get, df, ...):
    padding replaced by single spaces (the tokenizer charges ~1 token per run of spaces, a single space is free).
    Only when no field but the last contains a space, so `split(' ', k-1)` decodes it exactly."""
    out, i, n = [], 0, len(lines)
    while i < n:
        cols = _cols(lines[i])
        if cols is None or _OWN.match(lines[i]):
            out.append(lines[i]); i += 1; continue
        rows, j = [], i
        while j < n:
            f = _split_fixed(lines[j], cols)
            if f is None or _render_fixed(f, cols) != lines[j]:
                break
            rows.append(f); j += 1
        if len(rows) >= TABLE_MIN:
            block = [f"[cp:cols {' '.join(map(str, cols))}]"] + [" ".join(f) for f in rows] + [END]
            if _est_lines(block) < _est_lines(lines[i:j]):        # token gate
                out.extend(block)
                i = j
                continue
        out.append(lines[i]); i += 1
    return out


# ---------------------------------------------------------------- fences (shared by prefix / blockref / decode)

def _is_opener(ln: str) -> bool:
    return ln in (HB, PB, NB, HB_OLD, PB_OLD, NB_OLD) or bool(NUMB_RE.match(ln) or TB_RE.match(ln) or PFX_RE.match(ln)
                                                           or NUMB_OLD.match(ln) or TB_OLD.match(ln))


def _fenced(lines: list[str]) -> list[bool]:
    """True for marker lines and every line inside a fence: prefix / blockref never touch those."""
    m, inside = [], False
    for ln in lines:
        if inside:
            m.append(True)
            if ln in ENDS:
                inside = False
            continue
        if _OWN.match(ln):
            m.append(True)
            inside = _is_opener(ln)
            continue
        m.append(False)
    return m


# ---------------------------------------------------------------- prefix

PFX_DELIMS = re.compile(r"(: | \| |\| |\] |\t)")


def _pfx(ln: str) -> str | None:
    m = PFX_DELIMS.search(ln)
    if not m or m.end() < 6 or m.end() >= len(ln):
        return None
    return ln[:m.end()]


def _enc_prefix(lines: list[str]) -> list[str]:
    fm = _fenced(lines)
    out, i, n = [], 0, len(lines)
    while i < n:
        p = None if fm[i] else _pfx(lines[i])
        if p is None:
            out.append(lines[i]); i += 1; continue
        j = i
        while j < n and not fm[j] and lines[j].startswith(p) and len(lines[j]) > len(p):
            j += 1
        run = lines[i:j]
        if len(run) >= 3:
            rest = [x[len(p):] for x in run]
            block = [f"[cp:each line starts with {json.dumps(p, ensure_ascii=False)}]"] + rest + [END_CP]
            if not any(MARKERISH.match(x) for x in rest) and _est_lines(block) < _est_lines(run):
                out.extend(block); i = j; continue      # (a stripped line that looks like a marker would forge one)
        out.extend(run)
        i = j
    return out


# ---------------------------------------------------------------- a11y (Playwright accessibility snapshots)

A11Y_HEAD = '[cp: each line is a "- " YAML list item, dash omitted; ↗ = [cursor=pointer]]'
A11Y_HEAD0 = '[cp: each line is a "- " YAML list item, dash omitted]'
A11Y_HEADS = (A11Y_HEAD, A11Y_HEAD0)
A11Y_ITEM = re.compile(r"^( *)- (\S.*)$")
A11Y_CUR, A11Y_ARROW = " [cursor=pointer]", " ↗"
A11Y_OPEN, A11Y_CLOSE = "```yaml", "```"


def _enc_a11y(lines: list[str]) -> list[str]:
    """A ```yaml block whose every line is a `- item` list line holding `[ref=...]` refs (Playwright MCP snapshot):
    the `- ` after the indent is dropped and ` [cursor=pointer]` becomes ` ↗` (only when the block has no `↗`), under
    a one-line `[cp: ...]` header inside the block. Refs and every other byte stay as they were; blank lines stay."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if lines[i] != A11Y_OPEN:
            out.append(lines[i]); i += 1; continue
        k = next((j for j in range(i + 1, n) if lines[j] == A11Y_CLOSE), None)
        if k is None:
            out.extend(lines[i:]); break
        body = lines[i + 1:k]
        ms = [A11Y_ITEM.match(x) for x in body]
        if any(ms) and all(m or not x.strip() for m, x in zip(ms, body)) and any("[ref=" in x for x in body):
            new = [m.group(1) + m.group(2) if m else x for m, x in zip(ms, body)]
            head = A11Y_HEAD0
            if not any("↗" in x for x in body) and any(A11Y_CUR in x for x in new):
                new = [x.replace(A11Y_CUR, A11Y_ARROW) for x in new]
                head = A11Y_HEAD
            if not any(MARKERISH.match(x) or x.startswith(A11Y_CLOSE) for x in new) \
                    and _est_lines([head] + new) < _est_lines(body):
                out += [lines[i], head] + new + [lines[k]]
                i = k + 1
                continue
        out.extend(lines[i:k + 1])
        i = k + 1
    return out


_A11Y_LINE = re.compile(r"^( *)(.*)$")


def _dec_a11y(lines: list[str]) -> list[str]:
    if not any(x in A11Y_HEADS for x in lines):
        return lines
    out, i, n = [], 0, len(lines)
    while i < n:
        if not (lines[i] == A11Y_OPEN and i + 1 < n and lines[i + 1] in A11Y_HEADS):
            out.append(lines[i]); i += 1; continue
        arrow = lines[i + 1] == A11Y_HEAD
        out.append(lines[i])
        i += 2
        while i < n and lines[i] != A11Y_CLOSE:
            x = lines[i]
            if x.strip():
                m = _A11Y_LINE.match(x)
                x = m.group(1) + "- " + m.group(2)
                if arrow:
                    x = x.replace(A11Y_ARROW, A11Y_CUR)
            out.append(x)
            i += 1
    return out


# ---------------------------------------------------------------- diff headers

def _enc_diffhdr(lines: list[str]) -> list[str]:
    if not any(ln.startswith("diff --git ") for ln in lines):
        return lines
    out, i, n, saved = [], 0, len(lines), []
    while i < n:
        m = DG.match(lines[i])
        out.append(lines[i]); i += 1
        if not m:
            continue
        while i < n and re.match(r"^(index |new file|deleted file|old mode|new mode|similarity|rename |copy |"
                                 r"dissimilarity)", lines[i]):
            out.append(lines[i]); i += 1
        if i + 2 < n and lines[i] == f"--- a/{m.group(1)}" and lines[i + 1] == f"+++ b/{m.group(2)}" \
                and lines[i + 2].startswith("@@ "):
            saved += lines[i:i + 2]
            i += 2
    if not saved or _est_lines(saved) <= _est(DH_NOTE) + 2:
        return lines
    return [DH_NOTE] + out


def _dec_diffhdr(lines: list[str]) -> list[str]:
    out, cur, seen_minus = [], None, False
    for ln in lines:
        m = DG.match(ln)
        if m:
            cur, seen_minus = m, False
        elif ln.startswith("--- ") and cur is not None:
            seen_minus = True
        elif ln.startswith("@@ ") and cur is not None:
            if not seen_minus:
                out.append(f"--- a/{cur.group(1)}")
                out.append(f"+++ b/{cur.group(2)}")
            cur = None
        out.append(ln)
    return out


# ---------------------------------------------------------------- blockref (replaces dupref)

def _br_marker(a: int, m: int) -> str:
    return f"[cp:= same as line {a}]" if m == 1 else f"[cp:= same as lines {a}–{a + m - 1}]"


def _cheap(ln: str) -> bool:
    """A line with no letter or digit (`=====`, `-----`, `}`, blank): a few tokens at most, and a reader's visual
    anchor. blockref never starts or ends a reference on one, and its chars don't count toward BR_MIN_CHARS."""
    return not any(c.isalnum() for c in ln)


def _enc_blockref(lines: list[str]) -> list[str]:
    """A run of lines repeating earlier DISPLAYED plain lines (outside fences, not references) -> one reference to
    their displayed line numbers. Greedy, longest match among the last 16 occurrences of the run's first line.
    Separator / punctuation-only lines are never replaced on their own: a run starts and ends on a line with a
    letter or digit, and must pass the token gate on what it replaces."""
    fm = _fenced(lines)
    out: list[str] = []
    plain: list[bool] = []
    pos: dict[str, list[int]] = {}
    i, n = 0, len(lines)
    while i < n:
        best = None
        if not fm[i] and not _cheap(lines[i]):
            for jd in pos.get(lines[i], ())[-16:]:
                m = 0
                while i + m < n and jd + m < len(out) and not fm[i + m] and plain[jd + m] and out[jd + m] == lines[i + m]:
                    m += 1
                while m and _cheap(lines[i + m - 1]):
                    m -= 1
                if best is None or m > best[1]:
                    best = (jd, m)
        if best and best[1]:
            jd, m = best
            run = lines[i:i + m]
            mk = _br_marker(jd + 1, m)
            if sum(len(x) for x in run if not _cheap(x)) + m >= BR_MIN_CHARS and \
                    _est_lines(run) >= _est(mk) + BR_MARGIN:
                prev = BR_RE.match(out[-1]) if out and not plain[-1] else None
                if prev and int(prev.group(1)) == jd + 1 and int(prev.group(2) or prev.group(1)) == jd + m:
                    out[-1] = mk[:-1] + f", repeated {int(prev.group(3) or 1) + 1} times]"   # back-to-back repeats
                else:
                    out.append(mk); plain.append(False)
                i += m
                continue
        if not fm[i]:
            pos.setdefault(lines[i], []).append(len(out))
        out.append(lines[i]); plain.append(not fm[i])
        i += 1
    return out


def _enc_dupref(lines: list[str]) -> list[str]:   # legacy name: callers asking for "dupref" get blockref
    return _enc_blockref(lines)


def _dec_blockref(lines: list[str]) -> list[str]:
    if not any(ln.startswith("[cp:= ") for ln in lines):
        return lines
    out = []
    for k, ln in enumerate(lines):
        m = BR_RE.match(ln)
        if m:
            a, b, c = int(m.group(1)), int(m.group(2) or m.group(1)), int(m.group(3) or 1)
            if 1 <= a <= b <= k and (b - a + 1) * c <= 1_000_000 and not any(BR_RE.match(x) for x in lines[a - 1:b]):
                out.extend(lines[a - 1:b] * c)
                continue
        out.append(ln)
    return out


# ---------------------------------------------------------------- jsonrows / hread (whole-output)

JCFG = [(False, None), (False, (",", ":")), (True, None), (True, (",", ":"))]   # (ensure_ascii, separators)
JLABEL = ["", "; compact", "; ascii", "; ascii compact"]
_NAME = r'(?:[\w-]+|"(?:[^"\\\n]|\\.)*")'       # a key as is when it is a word, else as a JSON string
_COL = re.compile(rf"({_NAME})(?:\.({_NAME}))?(\(json\))?")   # key | key.sub (one-level flattened) + (json)
_COLNC = rf"{_NAME}(?:\.{_NAME})?(?:\(json\))?"
ROWS_HDR = re.compile(rf"^\[cp:rows #(\d{{1,4}}) TSV ({_COLNC}(?:, {_COLNC})*)"
                      rf"(; grouped by {_NAME}(?:\.{_NAME})?)?(; ascii compact|; compact|; ascii)?\]$")
_SCELL = re.compile(r"[\t\n\r\x00-\x1f\x7f\x85  ]")


def _ser(o, k: int) -> str:
    ea, seps = JCFG[k]
    return json.dumps(o, ensure_ascii=ea, separators=seps)


def _jload(s: str):
    try:
        return json.loads(s)
    except (ValueError, RecursionError):
        return None


_WORD = re.compile(r"^[\w-]+$")


def _nm(key: str) -> str:
    return key if _WORD.match(key) else json.dumps(key, ensure_ascii=False)


def _un_nm(x: str) -> str:
    return json.loads(x) if x.startswith('"') else x


def _parse_cols(spec: str) -> list[tuple[str, str | None, str]] | None:
    """Header column list -> [(key, sub or None, "s" | "j")]."""
    out, i = [], 0
    while True:
        m = _COL.match(spec, i)
        if not m:
            return None
        out.append((_un_nm(m.group(1)), _un_nm(m.group(2)) if m.group(2) else None, "j" if m.group(3) else "s"))
        i = m.end()
        if i == len(spec):
            return out
        if not spec.startswith(", ", i):
            return None
        i += 2


def _union_keys(rows: list[dict]) -> list[str] | None:
    """Every key of the records in one order that each record's own key order follows (absent keys allowed); None
    when no such order exists (then a decoder could not rebuild the key order)."""
    order: list[str] = []
    pos: dict[str, int] = {}
    for r in rows:
        last = -1
        for key in r:
            if key not in pos:
                pos[key] = len(order)
                order.append(key)
        for key in r:
            if pos[key] <= last:
                return None
            last = pos[key]
    return order


def _rec_columns(arr: list, ext: bool = True) -> list[tuple[str, str | None]] | None:
    """Columns of an array of >= 3 records: (key, None) or (key, sub) for a one-level flattened sub-object; None when
    the array is not records. ext: a key is flattened when every record holds an object under it with the same keys,
    and keys that are not plain words are allowed (JSON-quoted in the header); without ext neither (lprobe3: both
    only ever appeared together in the items Qwen3.6-thinking failed)."""
    if len(arr) < 3 or not all(isinstance(x, dict) and x for x in arr):
        return None
    order = _union_keys(arr)
    if order is None or (not ext and not all(_WORD.match(key) for key in order)):
        return None
    cols: list[tuple[str, str | None]] = []
    for key in order:
        vals = [x.get(key, _ABSENT) for x in arr]
        if ext and all(isinstance(v, dict) for v in vals):     # present in every record: all sub cells empty = {}
            sub = _union_keys(vals)
            if sub:
                cols += [(key, q) for q in sub]
                continue
        cols.append((key, None))
    return cols


_ABSENT = object()


def _cell_values(arr: list, col: tuple[str, str | None]) -> list:
    key, sub = col
    return [x.get(key, _ABSENT) if sub is None else x[key].get(sub, _ABSENT) for x in arr]


def _rows_block(arr: list, n: int, k: int, ext: bool = True) -> list[str] | None:
    cols = _rec_columns(arr, ext)
    if cols is None:
        return None
    vals = [_cell_values(arr, c) for c in cols]
    # a string column shows its strings raw; it must be present in every record (an empty cell is the empty string)
    types = ["s" if all(isinstance(v, str) and not _SCELL.search(v) for v in vs) else "j" for vs in vals]
    rows = [[(v if t == "s" else "" if v is _ABSENT else _ser(v, k)) for vs, t in zip(vals, types) for v in [vs[r]]]
            for r in range(len(arr))]
    names = [_nm(key) if sub is None else f"{_nm(key)}.{_nm(sub)}" for key, sub in cols]
    group = len(cols) >= 3 and types[0] == "s" and len({r[0] for r in rows}) < 0.7 * len(rows)
    spec = ", ".join(nm + ("(json)" if t == "j" else "") for nm, t in zip(names, types))
    out = [f"[cp:rows #{n} TSV {spec}{'; grouped by ' + names[0] if group else ''}{JLABEL[k]}]"]
    cur = None
    for r in rows:
        if group:
            if r[0] != cur:
                out.append(r[0]); cur = r[0]
            out.append("\t".join(r[1:]))
        else:
            out.append("\t".join(r))
    out.append(END_CP)
    if _BREAKS.search(out[0]) or not ROWS_HDR.match(out[0]) or \
            any(MARKERISH.match(x) or _BREAKS.search(x) for x in out[1:-1]):
        return None                       # a cell that looks like a marker (H2) or splits a line: leave the array
    return out


def _enc_jsonrows(text: str, ext: bool = True) -> str:
    """JSON document -> outer document with each records array replaced by `[cp:rows #N]` + one TSV block per array.
    Records may lack keys (empty `(json)` cell = key absent), hold nested values (`(json)` cells, compact JSON), and
    one-level sub-objects with the same keys in every record become `key.sub` columns. Exact bytes or nothing: the
    document must re-serialize byte-identically and the decoder must give the text back."""
    body, nl = (text[:-1], "\n") if text.endswith("\n") else (text, "")
    if not body or body[0] not in "[{" or body != body.strip() or "\n" in body or "[cp:rows #" in body:
        return text
    o = _jload(body)
    if o is None:
        return text
    k = next((c for c in range(len(JCFG)) if _ser(o, c) == body), None)
    if k is None:
        return text
    blocks: list[list[str]] = []

    def walk(x):                          # replaces accepted arrays by placeholders, outermost first
        if isinstance(x, list):
            if len(blocks) < 9999:
                blk = _rows_block(x, len(blocks) + 1, k, ext)
                if blk is not None and _est("\n".join(blk)) + 6 < _est(_ser(x, k)):   # per-array token gate
                    blocks.append(blk)
                    return f"\x00ROWS{len(blocks)}\x00"
            for i, y in enumerate(x):
                x[i] = walk(y)
        elif isinstance(x, dict):
            for key in x:
                x[key] = walk(x[key])
        return x

    try:
        o = walk(o)
    except RecursionError:
        return text
    if not blocks:
        return text
    outer = _ser(o, k)
    for n in range(1, len(blocks) + 1):
        ser_ph = _ser(f"\x00ROWS{n}\x00", k)
        if outer.count(ser_ph) != 1:
            return text
        outer = outer.replace(ser_ph, f"[cp:rows #{n}]", 1)
    new = "\n".join([outer] + [ln for b in blocks for ln in b]) + nl
    if len(new) >= len(text) or _est(new) >= _est(text):
        return text
    try:
        ok = _dec_jsonrows(new) == text
    except Exception:  # noqa: BLE001
        ok = False
    return new if ok else text


def _dec_jsonrows(text: str) -> str | None:
    """None when text is not a jsonrows encoding."""
    body, nl = (text[:-1], "\n") if text.endswith("\n") else (text, "")
    lines = body.split("\n")
    if len(lines) < 3 or "[cp:rows #1]" not in lines[0] or not ROWS_HDR.match(lines[1]):
        return None
    outer, arrays, i, k = lines[0], {}, 1, 0
    while i < len(lines):
        m = ROWS_HDR.match(lines[i])
        if not m:
            return None
        n = int(m.group(1))
        cols = _parse_cols(m.group(2))
        if cols is None:
            return None
        group = bool(m.group(3))
        k = JLABEL.index(m.group(4) or "")
        i += 1
        arr, cur = [], None
        while i < len(lines) and lines[i] != END_CP:
            ln = lines[i]
            if group and "\t" not in ln:
                cur = ln; i += 1; continue
            cells = ([cur] if group else []) + ln.split("\t")
            if len(cells) != len(cols) or (group and cur is None):
                return None
            rec: dict = {}
            for (key, sub, t), c in zip(cols, cells):
                tgt = rec if sub is None else rec.setdefault(key, {})   # a flattened sub-object is always there
                if t == "j" and c == "":
                    continue                      # key absent in this record
                tgt[key if sub is None else sub] = c if t == "s" else json.loads(c)
            arr.append(rec)
            i += 1
        if i >= len(lines):
            return None
        i += 1
        arrays[n] = arr
    for n, arr in arrays.items():
        outer = outer.replace(f"[cp:rows #{n}]", _ser(arr, k), 1)
    return outer + nl


SF_RE = re.compile(r"\[cp:files below, one per line(; ascii compact|; compact|; ascii)?\]")
SF_KEYS_NO = ("error", "success", "message", "status", "user_summary", "exit_code")   # results a harness classifies


def _enc_sffiles(text: str, group: bool = True) -> str:
    """Hermes search_files files-mode JSON {"files": ["a/b.py", ...], ...} -> the JSON with the list replaced by
    `[cp:files below, one per line]`, then one path per line (the `paths` rule groups them by directory). Exact or
    nothing; never on an object carrying an error / status key (harness failure checks read those)."""
    body, nl = (text[:-1], "\n") if text.endswith("\n") else (text, "")
    if not body.startswith("{") or "\n" in body or "[cp:files below" in body:
        return text
    o = _jload(body)
    if not isinstance(o, dict) or any(key in o for key in SF_KEYS_NO):
        return text
    fs = o.get("files")
    if not isinstance(fs, list) or len(fs) < 3 or \
            any(not isinstance(f, str) or not f or _BREAKS.search(f) or "\n" in f or MARKERISH.match(f) for f in fs):
        return text
    k = next((c for c in range(len(JCFG)) if _ser(o, c) == body), None)
    if k is None:
        return text
    o2 = dict(o)
    o2["files"] = "\x00F\x00"
    outer = _ser(o2, k)
    if outer.count(_ser("\x00F\x00", k)) != 1:
        return text
    outer = outer.replace(_ser("\x00F\x00", k), f"[cp:files below, one per line{JLABEL[k]}]", 1)
    lst = encode("\n".join(fs), ("paths",))[0] if group else "\n".join(fs)   # group: the `paths` rule is on
    new = f"{outer}\n{lst}{nl}"
    return new if len(new) < len(text) and _est(new) < _est(text) and _dec_sffiles(new) == text else text


def _dec_sffiles(text: str) -> str | None:
    body, nl = (text[:-1], "\n") if text.endswith("\n") else (text, "")
    outer, sep, rest = body.partition("\n")
    m = SF_RE.search(outer)
    if not m or not sep or not outer.startswith("{"):
        return None
    k = JLABEL.index(m.group(1) or "")
    return outer.replace(m.group(0), _ser(decode(rest).split("\n"), k), 1) + nl


HREAD_RE = re.compile(r"\[cp:content below(; ascii compact|; compact|; ascii)?\]")


def _enc_hread(text: str) -> str:
    if "\n" in text or not text.startswith("{"):
        return text
    o = _jload(text)
    if not isinstance(o, dict) or not isinstance(o.get("content"), str):
        return text
    k = next((c for c in range(len(JCFG)) if _ser(o, c) == text), None)
    if k is None:
        return text
    o2 = dict(o)
    o2["content"] = "\x00C\x00"
    outer = _ser(o2, k)
    mk = f"[cp:content below{JLABEL[k]}]"
    if "[cp:" in outer or outer.count(_ser("\x00C\x00", k)) != 1:
        return text
    outer = outer.replace(_ser("\x00C\x00", k), mk, 1)
    c = o["content"]
    if any(MARKERISH.match(x) for x in c.split("\n")):
        return text
    body, _ = encode(c, ("numbered",))
    new = f"{outer}\n{body}"
    return new if len(new) < len(text) and _dec_hread(new) == text else text


def _dec_hread(text: str) -> str | None:
    outer, sep, body = text.partition("\n")
    m = HREAD_RE.search(outer)
    if not m or not sep or not outer.startswith("{"):
        return None
    k = JLABEL.index(m.group(1) or "")
    return outer.replace(m.group(0), _ser(decode(body), k), 1)


# ---------------------------------------------------------------- decode

def _dec_fences(lines: list[str]) -> list[str]:
    """Expand every fence; a `[cp:...]` fence ends at `[/cp]`, a legacy `[ctxprune:...]` one at `[/ctxprune]`."""
    out, i, n = [], 0, len(lines)

    def body(start, end):
        j = start
        while j < n and lines[j] != end:
            j += 1
        return lines[start:j], j + 1

    while i < n:
        ln = lines[i]
        if not ln.startswith("[") or len(ln) > 2000 or not _is_opener(ln):   # (no 1e9-wide re-padding either)
            out.append(ln); i += 1; continue
        end = END_OLD if ln.startswith("[ctxprune") else END
        blk, i = body(i + 1, end)
        if ln in (HB, HB_OLD):
            cur = None
            for x in blk:
                m = re.match(r"^(\d+)([:-])(.*)$", x)
                if x == "--":
                    out.append("--"); cur = None
                elif m and cur is not None:
                    out.append(f"{cur}{m.group(2)}{m.group(1)}{m.group(2)}{m.group(3)}")
                else:
                    cur = x
        elif ln in (PB, PB_OLD):
            ind, cur = (" " if ln == PB else "  "), ""
            for x in blk:
                if x.startswith(ind):
                    out.append(cur + x[len(ind):])
                else:
                    cur = x
        elif ln in (NB, NB_OLD):
            for x in blk:
                num, _, rest = x.partition(" ")
                out.append(num.rjust(6) + "\t" + rest)
        elif NUMB_RE.match(ln) or NUMB_OLD.match(ln):
            mm = NUMB_RE.match(ln)
            if mm:
                w, ch = int(mm.group(1)), ("0" if mm.group(2) else " ")
            else:
                mm = NUMB_OLD.match(ln)
                w, ch = int(mm.group(1)), (" " if mm.group(2) == "space" else "0")
            for x in blk:
                k = 0
                while k < len(x) and x[k].isdigit():
                    k += 1
                out.append(x[:k].rjust(w, ch) + x[k:])
        elif TB_RE.match(ln) or TB_OLD.match(ln):
            tb = TB_RE.match(ln)
            cols = [int(x) for x in (tb.group(1).split(" ") if tb else TB_OLD.match(ln).group(1).split(","))]
            for x in blk:
                out.append(_render_fixed(x.split(" ", len(cols) - 1), cols))
        else:                                                     # prefix
            p = _jload(PFX_RE.match(ln).group(1))
            if not isinstance(p, str):
                out.append(ln); out.extend(blk); out.append(end); continue
            for x in blk:
                out.append(p + x)
    return out


def _crlf(text: str) -> bool:
    """Every line break is \r\n (Windows / PowerShell output): the rules run on the LF form and the CRs go back."""
    return "\r\n" in text and text.count("\r") == text.count("\n") == text.count("\r\n")


def decode(text: str) -> str:
    if _crlf(text):
        return _decode_lf(text.replace("\r\n", "\n")).replace("\n", "\r\n")
    return _decode_lf(text)


def _decode_lf(text: str) -> str:
    head, _, rest = text.partition("\n")
    w = None
    try:                                   # decode never raises: a malformed encoding is returned as is
        if head.startswith("{") and "[cp:content below" in head:
            w = _dec_hread(text)
        elif head.startswith("{") and "[cp:files below" in head:
            w = _dec_sffiles(text)
        elif rest.startswith("[cp:rows #1 ") and "[cp:rows #1]" in head:
            w = _dec_jsonrows(text)
    except (ValueError, KeyError, IndexError, RecursionError, TypeError):
        w = None
    if w is not None:
        return w
    lines = text.split("\n")
    if lines and lines[0].startswith(LEGEND_HEAD) and lines[0].endswith("]"):
        lines = lines[1:]
    diff = bool(lines) and lines[0] == DH_NOTE
    if diff:
        lines = lines[1:]
    lines = _dec_blockref(lines)           # display-numbered: resolved before any fence is expanded
    out = _dec_fences(lines)
    for k, ln in enumerate(out):           # legacy dupref: numbered in DECODED lines, resolved after the fences
        m = DUPREF.match(ln)
        if m and 0 < int(m.group(1)) <= k:
            out[k] = out[int(m.group(1)) - 1]
    if diff:
        out = _dec_diffhdr(out)
    out = _dec_a11y(out)                   # encoded first, so decoded last
    return "\n".join(out)


# ---------------------------------------------------------------- encode

VIEW_RULES = ("numbered",)                                   # file views: code bytes stay exact
TEXT_RULES = ("a11y", "diffhdr", "hits", "paths", "numbered", "table", "prefix", "blockref")
FLAG_RULES = {"blockref": "CTXPRUNE_BLOCKREF", "prefix": "CTXPRUNE_PREFIX", "jsonrows_ext": "CTXPRUNE_JSONROWS_EXT"}
# ^ opt-in (off in lossless.compress unless the env flag is 1), comprehension probes 2026-10-17: blockref fails
#   count / line questions even with the note; prefix costs -2.7 pt without thinking and 2.9x thinking tokens;
#   jsonrows_ext (key.sub columns, quoted keys): Qwen3.6 thinking -7.3 pt on those items, thinking runs into the cap
#   (lprobe3). a11y (Playwright) passed lprobe3 on Qwen3.6 / Qwen3.8 / Gemma: on. dupref is never encoded (0% even
#   with the note); its markers still decode.
JSON_RULES = ("sffiles", "jsonrows", "jsonrows_ext", "paths")   # paths: sffiles groups its list by directory;
# jsonrows_ext: jsonrows flattens key.sub columns and allows non-word (quoted) keys
ALL_RULES = TEXT_RULES
LINE_ORDER = (("a11y", _enc_a11y), ("diffhdr", _enc_diffhdr), ("hits", _enc_hits), ("paths", _enc_paths), ("catn", _enc_catn),
              ("numbered", _enc_numbered), ("table", _enc_table), ("prefix", _enc_prefix), ("blockref", _enc_blockref))


def _marked(text: str) -> bool:
    if text.startswith(LEGEND_HEAD) or "[cp:rows #" in text or "[cp:content below" in text or \
            "[cp:files below" in text:
        return True
    if "[" not in text:
        return False
    # only lossless2's own markers: lossless.compress's (grouped hits, unescaped JSON, truncated) come first by design
    return any(ln in MARKERS or ln == DH_NOTE or ln in A11Y_HEADS or _is_opener(ln) or BR_RE.match(ln) or DUPREF.match(ln)
               for ln in text.split("\n") if ln.startswith("["))


def encode(text: str, rules: tuple[str, ...] = TEXT_RULES) -> tuple[str, dict]:
    if _crlf(text):                               # CRLF output: encode the LF form, CRLF back on every line
        e, stats = _encode_lf(text.replace("\r\n", "\n"), rules)
        new = e.replace("\n", "\r\n")
        if stats["rules"] and len(new) < len(text) and decode(new) == text:
            stats["before"], stats["after"] = len(text), len(new)
            return new, stats
        return text, {"rules": [], "before": len(text), "after": len(text)}
    return _encode_lf(text, rules)


def _encode_lf(text: str, rules: tuple[str, ...]) -> tuple[str, dict]:
    stats = {"rules": [], "before": len(text)}
    rules = tuple("blockref" if r == "dupref" else r for r in rules)
    if _marked(text):                             # never re-encode marked text
        stats["after"] = len(text)
        return text, stats
    cur = text
    for name, fn in (("hread", _enc_hread), ("sffiles", _enc_sffiles), ("jsonrows", _enc_jsonrows)):   # whole-output
        if name in rules:
            new = fn(text, "paths" in rules) if name == "sffiles" else \
                fn(text, "jsonrows_ext" in rules) if name == "jsonrows" else fn(text)
            if new != text and _est(new) <= _est(text) and decode(new) == text:
                stats["rules"], stats["after"] = [name], len(new)
                return new, stats
    diffy = bool(DIFFY.search(text))
    for name, fn in LINE_ORDER:
        if name not in rules or (name == "blockref" and diffy):
            continue
        new = "\n".join(fn(cur.split("\n")))
        # per rule: fewer chars AND not more (estimated) tokens, and an exact round trip to the ORIGINAL
        if new != cur and len(new) < len(cur) and _est(new) <= _est(cur) and decode(new) == text:
            stats["rules"].append(name)
            cur = new
    if len(cur) >= len(text):                     # never longer than the input
        cur, stats["rules"] = text, []
    if decode(cur) != text:                       # whole-pipeline guard
        cur, stats["rules"] = text, []
    stats["after"] = len(cur)
    return cur, stats


# ---------------------------------------------------------------- self-test / measure

def _rt(s: str, rules=TEXT_RULES, want=None) -> str:
    e, st = encode(s, rules)
    assert decode(e) == s, (st, e)
    assert len(e) <= len(s)
    assert encode(s, rules) == (e, st)            # deterministic
    if want is not None:
        assert want in st["rules"], (want, st, e)
    return e


def _self_test() -> None:
    g = "\n".join([f"src/app/models/user.py:{i}:    user = User.get({i})" for i in range(1, 6)] +
                  ["--", "src/app/views.py-10-def f():", "src/app/views.py:11:    return User", "plain line",
                   "/home/u/proj/packages/sections/src/one.ts", "/home/u/proj/packages/sections/src/two.ts",
                   "/home/u/proj/packages/sections/src/three.ts", "/home/u/proj/packages/sections/src/four.ts",
                   "/home/u/proj/packages/billing/src/five.ts", "/home/u/proj/packages/billing/src/six.ts"])
    e, st = encode(g)
    assert decode(e) == g and st["rules"] == ["hits", "paths"] and len(e) < len(g), (st, e)
    assert encode(e)[0] == e                      # idempotent: marked text is left alone
    v1 = "[ctxprune: hits grouped by file]\n" + "\n".join(["x" * 50 + " repeated line", "y" * 30] * 3)
    assert "blockref" in encode(v1)[1]["rules"]  # lossless.compress's own markers come first: still encoded
    for s in ["", "no hits here\nat all", "a:1:b\nc:2:d\ne:3:f", "x/y.py:1:a", "[/cp]", "[cp:= same as line 1]"]:
        assert decode(encode(s)[0]) == s
    # hits: date-named paths alone are not hits; the linear matcher equals the regex
    dated = "\n".join(f"notes/daily/log-2026-10-{d:02d}.md" for d in range(10, 16))
    assert "hits" not in encode(dated, ("hits",))[1]["rules"]
    for ln in ["a/b.py:12:x", "a-1-b/c.py-3-y", "x" * 3000, "./p/q-2-r:7:z", "-1-", "a.b-12-", "~/x.c:4:"]:
        m = HIT.match(ln)
        assert (m.groups() if m else None) == _hit_match(ln), ln
    import time
    t0 = time.perf_counter(); _hit_match("ab" * 40000); assert time.perf_counter() - t0 < 0.5
    tbl = "\n".join(["CONTAINER ID   IMAGE          STATUS    NAMES", "a1b2c3d4e5f6   vllm:0.31      running   agent",
                     "b2c3d4e5f6a1   nginx:1.27     exited    web", "c3d4e5f6a1b2   redis:7        running   cache",
                     "d4e5f6a1b2c3   postgres:16    running   db"] +
                    [f"e{k:02d}f6a1b2c3d   worker:{k}       running   job{k}" for k in range(10)])
    tb2 = tbl.replace("CONTAINER ID", "CONTAINER_ID")
    e, st = encode(tb2)
    assert decode(e) == tb2 and "table" in st["rules"], (st, e)
    assert decode(encode(tbl)[0]) == tbl
    for kind in ("tab", "arrow", "zero"):
        txt = "\n".join(f"{k:>6}\tcode {k}" if kind == "tab" else (f"{k:>6}→code {k}" if kind == "arrow" else f"{k:05d}| code {k}")
                        for k in range(8, 60))
        e, st = encode(txt, ("numbered",))
        assert decode(e) == txt and st["rules"] == ["numbered"] and len(e) < len(txt), (kind, st, e)
    # legacy markers still decode (outputs written by earlier versions)
    assert decode("a\n[ctxprune:file]\nsrc/x.py\n3:foo\n4-bar\n[/ctxprune]") == "a\nsrc/x.py:3:foo\nsrc/x.py-4-bar"
    assert decode("[ctxprune:dir]\nsrc/a/\n  x.py\n  y.py\n[/ctxprune]") == "src/a/x.py\nsrc/a/y.py"
    assert decode("[ctxprune:numbered w=6 pad=space]\n9\tx\n10\ty\n[/ctxprune]") == "     9\tx\n    10\ty"
    assert decode("[ctxprune:numbered w=5 pad=zero]\n9| x\n[/ctxprune]") == "00009| x"
    assert decode("[ctxprune:table cols=0,6,12]\nab cd ef g\n[/ctxprune]") == "ab    cd    ef g"
    assert decode("[ctxprune:cat-n]\n7 x\n[/ctxprune]") == "     7\tx"
    assert decode("[cp:dir]\nsrc/a/\n x.py\n[/cp]\n[cp:numbered w=4 zero]\n9| x\n[/cp]") == "src/a/x.py\n0009| x"
    old = "first line that is long enough to be referenced by dupref later on ok\nmid\n" \
          "[ctxprune:= line 1: first line that is long…]"
    assert decode(old).split("\n")[2] == old.split("\n")[0]
    # blockref: display-numbered references, skipped on diffs, never inside fences
    blk = ["tests/test_core.py::test_a PASSED", "WARNING: datetime.utcnow() is deprecated and scheduled for removal",
           "  ts = datetime.utcnow()  # src/pkg/clock.py:41", "tests/test_core.py::test_b PASSED"]
    log = "\n".join(blk + ["tests/test_core.py::test_c PASSED"] + blk[1:3] + ["tests/test_core.py::test_d PASSED"]
                    + blk[1:3])
    e = _rt(log, want="blockref")
    assert "[cp:= same as lines 2–3]" in e.split("\n"), e
    lines_e = e.split("\n")
    for ln in lines_e:
        mm = BR_RE.match(ln)
        if mm:                                    # the target is visible at the displayed line numbers
            a = int(mm.group(1))
            assert lines_e[a - 1] == blk[1], (a, lines_e)
    rep = "\n".join(["poll 1", "status: building image layer 3 of 7", "progress: 41% (eta 2m)"] * 5)
    e = _rt(rep, want="blockref")
    assert e.split("\n")[3:] == ["[cp:= same as lines 1–3, repeated 4 times]"], e
    sep = "\n".join(["=" * 70, "section one", "=" * 70, "section two", "=" * 70])
    assert "blockref" not in encode(sep)[1]["rules"]          # token gate: separator lines are cheap
    sep2 = "\n".join((["-" * 80, "Mode   LastWriteTime   Length Name", "-" * 80, "a" * 30] * 3))
    e = encode(sep2)[0]
    el = e.split("\n")
    assert decode(e) == sep2 and el[:5] == sep2.split("\n")[:5] and el[5:] == ["[cp:= same as lines 2–4]", "-" * 80,
                                                                             "[cp:= same as lines 2–4]"], e
    diff = "\n".join(["diff --git a/src/x.py b/src/x.py", "index 1234567..89abcde 100644", "--- a/src/x.py",
                      "+++ b/src/x.py", "@@ -1,3 +1,3 @@", " def f():", "-    return 1", "+    return 2",
                      "diff --git a/src/long/path/module_name.py b/src/long/path/module_name.py", "index 1..2 100644",
                      "--- a/src/long/path/module_name.py", "+++ b/src/long/path/module_name.py", "@@ -9 +9 @@",
                      "-    x = compute_value_for(item, other_argument)", "+    x = compute_value_for(item, other_argument)  # same",
                      " unchanged context line long enough to be repeated somewhere below in the diff output",
                      " unchanged context line long enough to be repeated somewhere below in the diff output"])
    e = _rt(diff, want="diffhdr")
    assert e.startswith(DH_NOTE) and "--- a/src/x.py" not in e and "[cp:= " not in e, e
    # prefix
    logs = "\n".join(f"web-1     | GET /api/items/{k} 200 in {k * 3} ms" for k in range(12)) + "\nother"
    e = _rt(logs, want="prefix")
    assert e.startswith('[cp:each line starts with "web-1     | "]\nGET /api/items/0 200'), e
    forged = "\n".join(["proj-1  | x", "proj-1  | [cp:file]", "proj-1  | y", "proj-1  | z"])
    assert "prefix" not in encode(forged)[1]["rules"]          # a stripped line must never look like a marker
    # jsonrows: exact bytes or nothing
    recs = [{"path": f"./src/pkg/mod{k // 4}.py", "line": 10 + k, "content": f"def handler_{k}(request):"} for k in range(12)]
    for js in (json.dumps({"matches": recs, "total_count": 12}),
               json.dumps({"matches": recs, "total_count": 12}, separators=(",", ":")),
               json.dumps([{"name": "a", "size": 1.50, "ok": True}, {"name": "bb", "size": 2e3, "ok": None},
                           {"name": "ccc", "size": -0.0, "ok": False}]),
               json.dumps({"items": [{"id": k, "title": f"Ü title {k}"} for k in range(5)]}, ensure_ascii=True)):
        e = _rt(js, JSON_RULES, want="jsonrows")
        assert e.split("\n")[1].startswith("[cp:rows #1 TSV "), e
    e = _rt(json.dumps({"matches": recs}), JSON_RULES)
    assert "\n./src/pkg/mod0.py\n10\tdef handler_0(request):" in e and "; grouped by path]" in e, e
    # CRLF (PowerShell): rules run on the LF form, every CR comes back
    sec = lambda d: ["", f"    Directory: D:\\proj\\{d}", "", "Mode                 LastWriteTime         Length Name",
                     "----                 -------------         ------ ----"] + \
        [f"-a---          12-06-2026 06:{k:02d} AM          {k * 977:5d} file_{d}_{k}.ts" for k in range(14)]
    ps = "\r\n".join(sec("src") + sec("lib") + sec("lib"))
    for t in (ps, ps + "\r\n", ps + "\r\nlast\n", "a\r\r\nb\r\n" + ps):
        e, st = encode(t)
        assert decode(e) == t and len(e) <= len(t), (st, e[:300])
    e, st = encode(ps + "\r\n")
    assert st["rules"] and e.endswith("\r\n") and "\n" not in e.replace("\r\n", ""), st
    assert "\r\n[cp:cols " in e or "[cp:= same as lines" in e, e[:400]
    # a11y: Playwright MCP snapshot (yaml block of `- item` lines with refs)
    snap = ["### Page", "- Page URL: https://example.org/", "### Snapshot", "```yaml",
            '- generic [active] [ref=e1]:', '  - link "Jump to content" [ref=e2] [cursor=pointer]:',
            '    - /url: "#bodyContent"', "  - banner [ref=e4]:", "    - navigation \"Site\" [ref=e6]:"] + \
           [f'      - button "Item {k}" [ref=e{10 + k}] [cursor=pointer]' for k in range(40)] + ["", "```", "done"]
    sn = "\n".join(snap)
    e = _rt(sn, want="a11y")
    el = e.split("\n")
    assert el[:5] == snap[:4] + [A11Y_HEAD] and el[5] == "generic [active] [ref=e1]:", el
    assert '      button "Item 3" [ref=e13] ↗' in el and "- Page URL: https://example.org/" in el, el
    assert all(f"[ref=e{10 + k}]" in e for k in range(40))
    sn2 = sn.replace("Item 5", "Item ↗ 5")                     # an arrow in the block: dashes only
    e = _rt(sn2, want="a11y")
    assert A11Y_HEAD0 in e.split("\n") and "[cursor=pointer]" in e, e
    for odd in (sn.replace('[ref=', '[id='), sn.replace("    - /url", "    -/url"),
                sn.replace('- generic [active]', '- ```'), sn.replace('- generic [active]', '- [cp: x]')):
        e, st = encode(odd)
        assert decode(e) == odd and "a11y" not in st["rules"], (st, e)
    assert encode(A11Y_OPEN + "\n" + A11Y_HEAD + "\nx [ref=e1]\n```")[1]["rules"] == []   # marked: left alone
    # MCP JSON (Notion-like): nested values, keys absent in some records, a sub-object flattened to key.sub columns
    blocks = [{"object": "block", "id": f"0f1e2d3c-4b5a-81{k:02d}", "parent": {"type": "page_id", "page_id": "p-1"},
               "has_children": k == 2, "type": t, t: {"rich_text": [{"plain_text": f"text {k}"}]} if t != "divider" else {}}
              for k, t in enumerate(["heading_2", "paragraph", "divider", "paragraph", "to_do"])]
    nb = json.dumps({"object": "list", "results": blocks, "next_cursor": None}, separators=(",", ":"))
    e = _rt(nb, JSON_RULES, want="jsonrows")
    hdr = e.split("\n")[1]
    assert "parent.type, parent.page_id" in hdr and "heading_2(json)" in hdr and "\tblock_id\t" not in e, e
    assert "\t\t" in e, e                                      # an absent key is an empty (json) cell
    e2 = encode(nb, ("jsonrows",))[0]                          # without jsonrows_ext: sub-objects stay (json) cells
    assert decode(e2) == nb and "parent(json)" in e2.split("\n")[1] and "parent.type" not in e2, e2
    assert "jsonrows" not in encode(json.dumps([{"a b": k} for k in range(9)]), ("jsonrows",))[1]["rules"]
    for js in (json.dumps([{"a": "x", "b": {"c": 1, "d": "y"}}, {"a": "", "b": {"c": 2, "d": "z"}},
                           {"a": "w", "b": {"c": 3, "d": ""}}]),                     # "" in string columns
               json.dumps([{"a": 1}, {"b": 2}, {"a": 3, "b": 4}, {"c": [1, {"d": None}]}]),   # absent everywhere
               json.dumps({"x": [{"k.dot": 1, "a, b": {"c d": 1}}, {"k.dot": 2, "a, b": {}},
                                 {"k.dot": 3, "a, b": {"e]": 2}}]})):             # odd keys: JSON-quoted names
        assert decode(encode(js, JSON_RULES)[0]) == js, encode(js, JSON_RULES)
    air = json.dumps({"records": [{"id": f"rec{k:03d}", "fields": {"Page Name": f"Home {k}", "Visits": k * 7,
                                                                   **({"Notes": "x"} if k % 3 else {})}}
                                  for k in range(8)]})
    e = _rt(air, JSON_RULES, want="jsonrows")
    assert 'fields."Page Name", fields.Visits(json), fields.Notes(json)' in e, e
    assert "jsonrows" not in encode(json.dumps([{"b": 1, "a": 2}, {"a": 3, "b": 4}, {"a": 5}]), JSON_RULES)[1]["rules"]
    for bad in ('{"a": [{"x": 1e5}, {"x": 2}, {"x": 3}]}', '[{"a":"x"},{"a":"y"},{"a":"z"}]  ',
                '{"rows": [{"a": "[cp:file]"}, {"a": "b"}, {"a": "c"}, {"a": "d"}]}'):
        assert decode(encode(bad, JSON_RULES)[0]) == bad
    # sffiles: Hermes search_files files mode
    fl = [f"./src/pkg/sub/mod_{k}.py" for k in range(12)] + ["./tests/test_a.py", "./tests/test_b.py", "README.md"]
    for js in (json.dumps({"total_count": 15, "files": fl}, ensure_ascii=False),
               json.dumps({"files": fl, "total_count_is_lower_bound": True}, separators=(",", ":")) + "\n"):
        e = _rt(js, JSON_RULES, want="sffiles")
        assert "[cp:files below, one per line" in e.split("\n")[0] and "[cp:dir]" in e, e
    for js in (json.dumps({"files": fl, "error": None}), json.dumps({"files": fl[:2]}),
               json.dumps({"files": ["a\nb", "c", "d"]}), json.dumps({"files": ["[cp:dir]", "b", "c"]})):
        assert "sffiles" not in encode(js, JSON_RULES)[1]["rules"] and decode(encode(js, JSON_RULES)[0]) == js
    # hread (opt-in)
    code = "\n".join(f"{k:6d}|    value_{k} = compute({k})" for k in range(1, 40))
    hr = json.dumps({"content": code, "total_lines": 39, "file_size": 999, "truncated": False})
    e = _rt(hr, ("hread",), want="hread")
    assert e.startswith('{"content": [cp:content below], "total_lines": 39'), e[:80]
    assert "hread" not in encode(hr)[1]["rules"]               # off by default
    for junk in ("[cp:numbered w=999]\n1x\n[/cp]", "[cp:cols " + "9 " * 3000 + "9]\na\n[/cp]",
                 "a\n[cp:= same as lines 1–1, repeated 999999 times]", "[ctxprune:= line " + "9" * 5000 + ": x…]",
                 '{"a": [cp:rows #1]}\n[cp:rows #1 TSV a(json)]\n{bad\n[/cp]', '{"content": [cp:content below]}\n[/cp]',
                 "[cp:each line starts with \"x]\nq\n[/cp]", "[ctxprune:table cols=" + "1," * 900 + "1]\nab\n[/ctxprune]"):
        decode(junk)                              # never raises
    print("lossless2 self-test OK")


def _measure(path: str, limit: int = 0) -> None:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        T = lambda s: len(enc.encode(s, disallowed_special=()))
    except ImportError:
        T = lambda s: len(s) / 3.5
    tb = ta = 0
    fired: dict = {}
    bad = k = 0
    for k, line in enumerate(open(path)):
        if limit and k >= limit:
            break
        r = json.loads(line)
        t = r.get("text") if "text" in r else r.get("tool_output", "")
        s = t.strip()
        rules = JSON_RULES if s[:1] in "[{" else TEXT_RULES
        e, st = encode(t, rules)
        if decode(e) != t:
            bad += 1
        tb += T(t); ta += T(e)
        for rl in st["rules"]:
            fired[rl] = fired.get(rl, 0) + 1
    print(f"{k + 1 if not limit else min(limit, k + 1)} outputs: {tb:,.0f} -> {ta:,.0f} tokens "
          f"({100 * (tb - ta) / max(1, tb):.2f}% saved); round-trip failures: {bad}; fired: {fired}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--self-test"]:
        _self_test()
    elif sys.argv[1:2] == ["--measure"]:
        _measure(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
