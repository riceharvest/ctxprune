"""Compressors for the usage eval. Each method: fn(texts, rate) -> list[str]. rate = fraction kept (chars ~ tokens)."""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

if str(Path.home() / "ctxprune") not in sys.path:   # appended: never shadow the host process's modules (audit M4)
    sys.path.append(str(Path.home() / "ctxprune"))

IDENT = re.compile(r"[A-Za-z_$][\w$]*(?:[./:-][\w$]+)+|\b\w*\d\w*\b|\b[a-z]+[A-Z]\w*\b|\b\w+_\w+\b")
STRONG = re.compile(r"\b(def|class|function|fn|func|interface|struct|enum|impl|export|async|module)\b|"
                    r"(Error|Exception|Traceback|FAIL|FAILED|ERROR|error:|warning:|assert)|^\s*(@|#include|import |from )")
COMMENT = re.compile(r"^\s*(#|//|/\*|\*|\"\"\"|''')")


def _marker(n: int) -> str:
    return f"[… {n} line omitted …]" if n == 1 else f"[… {n} lines omitted …]"


_FENCE_OPEN = ("[ctxprune:file]", "[ctxprune:dir]", "[ctxprune:cat-n]", "[cp:file]", "[cp:dir]", "[cp:cat-n]")
_FENCE_END = ("[/ctxprune]", "[/cp]")
_OWN_MARK = ("[ctxprune", "[/ctxprune]", "[cp:", "[/cp]")   # marker lines lossless writes (input lines are escaped)
# other fenced blocks whose lines only make sense with their opener: prefix (`[cp:each line starts with ...]`),
# JSON rows (`[cp:rows #N ...]`, grouped: a value line heads its rows), numbered / table (`[cp:numbered`, `[cp:cols`)
_FENCE_OPEN_RE = re.compile(r"^\[(?:cp:(?:each line starts with |rows #|numbered |cols )|ctxprune:(?:numbered |table ))")


def _is_open(ln: str) -> bool:
    return ln in _FENCE_OPEN or bool(_FENCE_OPEN_RE.match(ln))


def _member(opener: str, ln: str) -> bool:
    """A line that belongs to the nearest header line above it (False: it IS a header line, e.g. `PATH` / `DIR/`)."""
    if opener.startswith("[cp:rows #"):
        return "\t" in ln or "; grouped by " not in opener
    if opener in ("[ctxprune:file]", "[cp:file]", "[ctxprune:dir]", "[cp:dir]"):
        return bool(re.match(r"^(\d+[:-]| )", ln)) or ln == "--"
    return True


def protect_structure(lines: list[str], keep: list[bool]) -> list[bool]:
    """Lossless structure must survive lossy selection (runtime audit 2026-10-17: random selection orphaned `N:text`
    lines from their file header in 389/400 outputs): every ctxprune marker line (`[ctxprune...]`, `[cp:...]`, v1
    group marker) is kept, and a kept line inside a fenced block keeps that block's fences and its nearest header
    line (the `PATH` / `DIR/` / grouped JSON-rows value line the kept line belongs to). Adds lines (may exceed the
    budget slightly)."""
    keep = list(keep)
    n = len(lines)
    block_start, header = None, None
    owner = [None] * n                      # (fence_open_idx, header_idx) for lines inside blocks
    for i, ln in enumerate(lines):
        if block_start is None and _is_open(ln):
            block_start, header = i, None
            continue
        if ln in _FENCE_END:
            if block_start is not None:
                owner[i] = (block_start, None)
            block_start, header = None, None
            continue
        if block_start is not None:
            if not _member(lines[block_start], ln):
                header = i
            owner[i] = (block_start, header)
    ends = {}
    cur = None
    for i in range(n - 1, -1, -1):
        if lines[i] in _FENCE_END:
            cur = i
        elif _is_open(lines[i]):
            ends[i] = cur
            cur = None
    for i, ln in enumerate(lines):
        if ln.startswith(_OWN_MARK) and not _is_open(ln) and ln not in _FENCE_END:
            keep[i] = True                    # dedup / blockref / v1 grouping markers
    for i in range(n):
        if keep[i] and owner[i] is not None:
            fo, hd = owner[i]
            keep[fo] = True
            if ends.get(fo) is not None:
                keep[ends[fo]] = True
            if hd is not None:
                keep[hd] = True
    return keep


def _keep_lines(lines: list[str], keep: list[bool]) -> str:
    if any(l.startswith(_OWN_MARK) for l in lines):
        keep = protect_structure(lines, keep)
    out, gap = [], 0
    for ln, k in zip(lines, keep):
        if k:
            if gap:
                out.append(_marker(gap))
                gap = 0
            out.append(ln)
        else:
            gap += 1
    if gap:
        out.append(_marker(gap))
    return "\n".join(out)


def headtail(texts, rate):
    res = []
    for t in texts:
        lines = t.splitlines()
        budget = rate * len(t)
        keep = [False] * len(lines)
        used, i, j = 0, 0, len(lines) - 1
        while i <= j and used < budget:  # alternate 2 head : 1 tail
            for idx in (i, i + 1, j):
                if i <= idx <= j and not keep[idx] and used < budget:
                    keep[idx] = True
                    used += len(lines[idx]) + 1
            i, j = i + 2, j - 1
        res.append(_keep_lines(lines, keep))
    return res


def lines_heur(texts, rate):
    """Rule-based line selection (what regex/RTK-style filters do): dedup, drop blanks/comments, rank by signal."""
    res = []
    for t in texts:
        lines = t.splitlines()
        seen: dict[str, int] = {}
        scores = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            key = re.sub(r"\d+", "0", s)
            dup = seen.get(key, 0)
            seen[key] = dup + 1
            if not s:
                sc = -5.0
            else:
                sc = len(IDENT.findall(ln)) * 1.0 + (3.0 if STRONG.search(ln) else 0.0)
                sc -= 2.0 if COMMENT.match(ln) else 0.0
                sc -= 3.0 * min(dup, 3)
                sc += 1.0 if i < 3 or i >= len(lines) - 3 else 0.0
            scores.append(sc)
        budget = rate * len(t)
        order = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
        keep = [False] * len(lines)
        used = 0
        for i in order:
            if used + len(lines[i]) + 1 > budget and used > 0:
                continue
            keep[i] = True
            used += len(lines[i]) + 1
        res.append(_keep_lines(lines, keep))
    return res


def _line_scores_heur(lines):
    seen: dict[str, int] = {}
    scores = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        key = re.sub(r"\d+", "0", s)
        dup = seen.get(key, 0)
        seen[key] = dup + 1
        if not s:
            sc = -5.0
        else:
            sc = len(IDENT.findall(ln)) * 1.0 + (3.0 if STRONG.search(ln) else 0.0)
            sc -= 2.0 if COMMENT.match(ln) else 0.0
            sc -= 3.0 * min(dup, 3)
            sc += 1.0 if i < 3 or i >= len(lines) - 3 else 0.0
        scores.append(sc)
    return scores


# speed3 D6: error lines a hard-keep rule protects (with their frame lines). Over-matches in code listings
# (`except ValueError:`, `assert x`), which is what the D6 ablation measures.
ERR_LINE = re.compile(r"Traceback \(most recent call last\)|Error:|error(\[[A-Z]?\d+\])?:|\bFAILED\b|\bassert\b|"
                      r"\bpanic(ked)?\b|Exception\b|^E {3}|(?i:\bexit (code|status)\b\W{0,3}[1-9]\d*)|"
                      r"<returncode>\s*[1-9]\d*")
# terminal exception lines (`ValueError: x`, `E   assert ...`): Python prints the innermost frame BEFORE them
ERR_LAST = re.compile(r"^\s*([\w.]*(Error|Exception)\w*\b:?|E {3})")


def error_lines(lines, after: int = 3, before: int = 2) -> set:
    """Indices of error lines (ERR_LINE) + up to `after` following non-blank lines (stops at a blank line), + up to
    `before` preceding non-blank lines for terminal exception lines (ERR_LAST). speed3 D6 hard-keep set."""
    keep: set = set()
    for i, ln in enumerate(lines):
        if not ERR_LINE.search(ln):
            continue
        keep.add(i)
        for j in range(i + 1, min(len(lines), i + 1 + after)):
            if not lines[j].strip():
                break
            keep.add(j)
        if ERR_LAST.match(ln):
            for j in range(i - 1, max(-1, i - 1 - before), -1):
                if not lines[j].strip():
                    break
                keep.add(j)
    return keep


def _select(lines, scores, budget_chars, round_to: float = 0.0, force=None):
    """Keep the highest-scoring lines (ties -> lower index first) until the char budget is used.
    speed3 (defaults = unchanged): round_to > 0 ranks by round(score / round_to) (D5: a score jitter below the bin size
    can't reorder lines, except at bin edges); force = line indices ranked before all others (D6 hard-keep: they still
    count against the budget, so the output stays within budget and monotone in it)."""
    if round_to:
        q = [math.floor(s / round_to + 0.5) for s in scores]
        order = sorted(range(len(lines)), key=lambda i: (-q[i], i))
    else:
        order = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
    if force:
        order = [i for i in order if i in force] + [i for i in order if i not in force]
    keep = [False] * len(lines)
    used = 0
    for i in order:
        if used + len(lines[i]) + 1 > budget_chars and used > 0:
            continue
        keep[i] = True
        used += len(lines[i]) + 1
    return _keep_lines(lines, keep)


def smooth_select(lines, scores, budget_chars, w: int = 2, decay: float = 0.8, fill: int = 3, mark: int = 26):
    """Line selection that avoids tiny holes (2026-10-17 everyday suite: `[… 1 lines omitted …]` holes inside mail
    records made the agent distrust the output and re-extract it, 5 extra calls). Scores are smoothed by a decayed
    max over +-w lines (contiguous picks), every newly opened gap pays `mark` chars of budget for its marker, and
    holes of <= `fill` lines are filled afterwards (up to 5% over budget)."""
    n = len(lines)
    if n == 0:
        return ""
    sm = list(scores)
    for i in range(n):
        for d in range(1, w + 1):
            f = decay ** d
            if i - d >= 0:
                sm[i] = max(sm[i], scores[i - d] * f)
            if i + d < n:
                sm[i] = max(sm[i], scores[i + d] * f)
    keep = [False] * n
    used = 0.0
    for i in sorted(range(n), key=lambda i: (-sm[i], i)):
        c = len(lines[i]) + 1
        opens = not ((i > 0 and keep[i - 1]) or (i + 1 < n and keep[i + 1]))
        extra = mark if opens else (-mark if (i > 0 and keep[i - 1]) and (i + 1 < n and keep[i + 1]) else 0)
        if used + c + extra > budget_chars and used > 0:
            continue
        keep[i] = True
        used += c + extra
    i = 0
    while i < n:
        if keep[i]:
            i += 1
            continue
        j = i
        while j < n and not keep[j]:
            j += 1
        if 0 < i and j < n and j - i <= fill:
            c = sum(len(lines[k]) + 1 for k in range(i, j))
            if used + c - mark <= budget_chars * 1.05:
                for k in range(i, j):
                    keep[k] = True
                used += c - mark
        i = j
    return _keep_lines(lines, keep)


def anchored_select(lines, scores, budget_chars, alpha: float = 0.3, head_share: float = 0.5, **kw):
    """smooth_select with `alpha` of the budget reserved for the output's head and tail (head_share of it at the
    top): on real Claude Code sessions head+tail alone keeps the copied lines best (local eval), the model finds the
    rest. Implemented as a score boost on the head/tail lines that fit the reserve, then smooth_select."""
    n = len(lines)
    if n == 0:
        return ""
    sc = list(scores)
    top = max(sc) if sc else 1.0
    res_h, res_t = alpha * budget_chars * head_share, alpha * budget_chars * (1 - head_share)
    used = 0.0
    for i in range(n):
        if used + len(lines[i]) + 1 > res_h:
            break
        sc[i] = top + 1.0
        used += len(lines[i]) + 1
    used = 0.0
    for i in range(n - 1, -1, -1):
        if used + len(lines[i]) + 1 > res_t or sc[i] > top:
            break
        sc[i] = top + 1.0
        used += len(lines[i]) + 1
    return smooth_select(lines, sc, budget_chars, **kw)


WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def lines_intent_bm25(texts, rate, items):
    """Heuristic line score + lexical overlap with the agent's intent (its last message + tool call), +-2 lines context."""
    import math
    res = []
    for t, it in zip(texts, items):
        lines = t.splitlines()
        q = {w.lower() for w in WORD.findall(it.get("intent", ""))}
        df: dict[str, int] = {}
        toks = []
        for ln in lines:
            ws = {w.lower() for w in WORD.findall(ln)}
            toks.append(ws)
            for w in ws:
                df[w] = df.get(w, 0) + 1
        n = max(1, len(lines))
        rel = [sum(math.log(1 + n / df[w]) for w in ws & q) for ws in toks]
        sm = [max(rel[max(0, i - 2): i + 3]) * 0.5 + rel[i] for i in range(len(lines))]
        base = _line_scores_heur(lines)
        res.append(_select(lines, [b + 2.0 * r for b, r in zip(base, sm)], rate * len(t)))
    return res


def make_scored(path, mix_heur=0.0):
    """Line selector from precomputed per-line scores (list per item, same order as the eval file)."""
    import json
    all_scores = json.load(open(path))

    def fn(texts, rate, items):
        res = []
        for t, it in zip(texts, items):
            lines = t.splitlines()
            sc = all_scores[it["_idx"]]
            if len(sc) != len(lines):
                sc = (sc + [0.0] * len(lines))[: len(lines)]
            if mix_heur:
                h = _line_scores_heur(lines)
                sc = [s + mix_heur * x / 10 for s, x in zip(sc, h)]
            res.append(_select(lines, sc, rate * len(t)))
        return res
    return fn


def register_scored(name, path, mix_heur=0.0):
    fn = make_scored(path, mix_heur)
    fn.__name__ = name
    METHODS[name] = fn
    CHEAP.add(name)
    NEEDS_ITEM.add(name)


def _ctxprune(protect):
    def fn(texts, rate):
        import ctxprune
        return [ctxprune.prune(t, keep=rate, protect=protect) for t in texts]
    return fn


def none(texts, rate):
    return list(texts)


METHODS = {"none": none, "headtail": headtail, "lines_heur": lines_heur,
           "ctxprune": _ctxprune(False), "ctxprune_protect": _ctxprune(True),
           "lines_intent_bm25": lines_intent_bm25}


CHEAP = {"headtail", "lines_heur", "lines_intent_bm25"}
NEEDS_ITEM = {"lines_intent_bm25"}


def get(name):
    return METHODS[name]
