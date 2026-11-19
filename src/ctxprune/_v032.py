"""The 0.3.2 selection path, frozen byte-for-byte (verbatim from the v0.3.2 tag: `Compressor.compress`, `detok`).

The 0.2/0.3 API (`prune(text, keep)`, `Compressor().compress(text, rate)`, `ctxprune FILE --keep`, `serve`
POST /compress, the LangChain / LlamaIndex classes) keeps producing exactly the bytes it produced in 0.3.2 unless a
caller asks for something new (`protect="auto"|"ids"`, `query=`, `mode=`, `unit="tokens_est"|"o200k"|...`,
`clean_web=`). `Compressor.compress` routes here for the old call shapes (see `Compressor._wants_v04`).
Do not "improve" this file: it is a compatibility contract (research/v04_code/lab/API_DIFF.md).
"""

from __future__ import annotations

import math

from .atoms import is_protected

STRUCTURE = set('{}[]",:')


def _merge_ws(a: str, b: str) -> str:
    if "\n" in a or "\n" in b:
        return "\n"
    return " " if (a or b) else ""


def detok(atoms, keep, collapse_ws: bool = True) -> str:
    out: list[str] = []
    pending = ""
    for a, k in zip(atoms, keep):
        if k:
            ws = a.ws
            if pending and collapse_ws:
                ws = _merge_ws(pending, a.ws)
            if not out:
                ws = ws if not collapse_ws else ""
            out.append(ws + a.text)
            pending = ""
        else:
            pending = _merge_ws(pending, a.ws) if pending else (a.ws or " ")
    return "".join(out)


def compress(comp, text, rate=0.5, threshold=None, force_protected=False, keep_structure=False, unit="chars",
             measure=None) -> dict:
    atoms, scores, tok_counts = comp.atom_scores(text)
    if callable(unit):
        counts = list(unit([(" " if a.ws else "") + a.text for a in atoms]))
    elif unit == "chars":
        counts = [len(a.text) + (1 if a.ws else 0) for a in atoms]
    elif unit == "tokens":
        counts = tok_counts
    else:
        raise ValueError(f"unit must be 'chars' or 'tokens', not {unit!r}")
    forced = [
        bool(a.text) and ((force_protected and is_protected(a.text)) or (keep_structure and a.text in STRUCTURE))
        for a in atoms
    ]
    if threshold is not None:
        keep = [f or s >= threshold for f, s in zip(forced, scores)]
    elif measure is not None and rate is not None:
        order = [i for i in sorted(range(len(atoms)), key=lambda i: -scores[i])
                 if atoms[i].text and not forced[i]]
        target = rate * measure(text)

        def with_top(k: int) -> list[bool]:
            kk = list(forced)
            for i in order[:k]:
                kk[i] = True
            return kk

        lo, hi = 0, len(order)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if measure(detok(atoms, with_top(mid))) <= target:
                lo = mid
            else:
                hi = mid - 1
        keep = with_top(lo)
    else:
        total = sum(counts)
        budget = math.ceil((rate if rate is not None else 1.0) * total)
        keep = list(forced)
        used = sum(c for c, f in zip(counts, forced) if f)
        for i in sorted(range(len(atoms)), key=lambda i: -scores[i]):
            if used >= budget:
                break
            if not keep[i] and atoms[i].text:
                keep[i] = True
                used += counts[i]
    out = detok(atoms, keep)
    return {"text": out, "kept": sum(c for c, k in zip(counts, keep) if k), "total": sum(counts)}
