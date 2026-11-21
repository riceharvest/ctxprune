"""Online Drain-style log-template miner (opt-in, lossy display digest, stdlib only).

For >50-line non-code logs: lines are clustered into templates with a Drain prefix tree
(token count -> first token -> similarity match). Repetitive bulk is replaced by
``[template xN | var ranges]``; first/last occurrence, rare templates (<2% share), alarm
lines (WARN/ERROR/FATAL/Traceback, non-2xx/3xx HTTP status) and numeric outliers stay verbatim.
Use only when the caller keeps the raw log.
"""
from __future__ import annotations

import re
from statistics import median

W = "<*>"
_ALARM_KW = re.compile(  # digit-free: evaluated once per distinct masked line
    r"\b(?:WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL|SEVERE|PANIC|EMERG|ALERT|FAIL(?:ED|URE)?"
    r"|Traceback|Exception|panic:|Caused by|segfault|OOM|Killed|denied|refused|timed? ?out)\b",
    re.I,
)
_ALARM_NUM = re.compile(  # needs the digits: HTTP 4xx/5xx, exit codes
    r"[\"\]] [45]\d\d\b|\b(?:[Ss]tatus|STATUS|[Cc]ode|CODE|HTTP/\d(?:\.\d)?)[=: \"]+[45]\d\d\b|\b[Ee]xit(?:ed)?[ =:]+[1-9]",
)
_FLT = re.compile(r"[-+]?\d+(?:\.\d+)?")
_NUM = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:ms|s|us|MB|KB|B|%)?$")




def _is_alarm(line: str) -> bool:
    return bool(_ALARM_KW.search(line) or _ALARM_NUM.search(line))


_RUN = re.compile(r"\d+")
_DIG = re.compile(r"\S*\d\S*")


class _Drain:
    def __init__(self, sim: float = 0.5, max_children: int = 100):
        self.sim, self.maxc = sim, max_children
        self.tree: dict = {}
        self.clusters: list[list] = []  # [template(list), ids(list)]
        self.seen: dict[str, list] = {}  # exact masked-line fast path

    def add(self, i: int, ml: str) -> None:
        hit = self.seen.get(ml)
        if hit is not None:
            hit[1].append(i)
            return
        m = ml.split()
        bucket = self.tree.setdefault(len(m), {})
        key = m[0] if m and (m[0] != W) else W
        if key not in bucket and len(bucket) >= self.maxc:
            key = W
        cands = bucket.setdefault(key, [])
        best, bs = None, -1.0
        for c in cands:
            t = c[0]
            s = sum(a == b for a, b in zip(t, m)) / len(m) if m else 1.0
            if s > bs:
                best, bs = c, s
        if best is not None and bs >= self.sim:
            best[0][:] = [a if a == b else W for a, b in zip(best[0], m)]
            best[1].append(i)
            self.seen[ml] = best
            return
        c = [m, [i]]
        self.seen[ml] = c
        cands.append(c)
        self.clusters.append(c)


def compact_logs(text: str, *, min_lines: int = 51, rare_share: float = 0.02) -> str:
    lines = text.splitlines()
    n = len(lines)
    if n < min_lines:
        return text
    toks = [l.split() for l in lines]
    d = _Drain()
    masked = _DIG.sub(W, text).splitlines()  # whole-text pass: \S never spans a newline
    if len(masked) != n:  # exotic line separators (\x0b, \x1c, ...): per-line fallback
        masked = [_DIG.sub(W, l) for l in lines]
    for i, ml in enumerate(masked):
        if toks[i]:
            d.add(i, ml)
    keep = {i for i, t in enumerate(toks) if not t}
    byml: dict[str, list] = {}
    for i, ml in enumerate(_RUN.sub("0", text).splitlines() if len(masked) == n else lines):
        byml.setdefault(ml, []).append(i)
    for ml, ids in byml.items():  # keyword alarms: once per distinct masked line
        if _ALARM_KW.search(ml):
            keep.update(ids)
    keep.update(i for i, l in enumerate(lines) if _ALARM_NUM.search(l))
    note: dict[int, str] = {}
    for tmpl, ids in d.clusters:
        if len(ids) < 3 or len(ids) / n < rare_share:
            keep.update(ids)
            continue
        keep.add(ids[0]); keep.add(ids[-1])
        ranges = []
        for p, t in enumerate(tmpl):
            if t != W:
                continue
            vals = [toks[j][p] for j in ids]
            if not all(_NUM.match(v) for v in vals):
                continue
            f = [float(_FLT.match(v).group()) for v in vals]
            m = median(f)
            if m:
                keep.update(j for j, x in zip(ids, f) if x > 8 * abs(m) or x < m / 8)
            lo, hi = min(range(len(f)), key=f.__getitem__), max(range(len(f)), key=f.__getitem__)
            if len(ranges) < 3 and f[lo] != f[hi]:
                ranges.append(f"#{p}={vals[lo]}..{vals[hi]}")
        note[ids[0]] = f"[{' '.join(tmpl)} x{len(ids)}" + (f" | {', '.join(ranges)}" if ranges else "") + "]"
    out = []
    for i, l in enumerate(lines):
        if i in note:
            out.append(note[i])
        if i in keep:
            out.append(l)
    res = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return res if len(res) < len(text) else text
