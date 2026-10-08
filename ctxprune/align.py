"""Turn a teacher's deletion-only compression into per-atom keep labels.

The teacher is asked to only delete atoms, so its output should be an
ordered subsequence of the source atoms. We find the longest common
subsequence (exact match first, casefold second) and label matched source
atoms as kept. Teacher atoms that match nothing are "variations" (the
teacher rewrote or invented text); a high variation rate means the sample
is untrustworthy and gets filtered.
"""

from __future__ import annotations

from dataclasses import dataclass

from .atoms import Atom, atomize, is_protected, norm


@dataclass
class Alignment:
    keep: list[bool]
    n_src: int
    n_comp: int
    n_matched: int
    variation_rate: float  # teacher atoms with no source match
    comp_rate: float  # kept / source, in atoms
    protected_total: int
    protected_kept: int


def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []
    # dp[i][j] = LCS length of a[i:], b[j:]; rows built bottom-up.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai, row, nxt = a[i], dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                row[j] = nxt[j + 1] + 1
            else:
                x, y = nxt[j], row[j + 1]
                row[j] = x if x >= y else y
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def align(src_atoms: list[Atom], comp_text: str) -> Alignment:
    src = [a.text for a in src_atoms]
    comp = [a.text for a in atomize(comp_text) if a.text]
    keep = [False] * len(src)
    matched_comp = [False] * len(comp)

    # Pass 1: exact. Pass 2: casefold over what is left, in the gaps between
    # pass-1 anchors so order is preserved.
    pairs = _lcs_pairs(src, comp)
    for i, j in pairs:
        keep[i] = True
        matched_comp[j] = True
    anchors = [(-1, -1)] + pairs + [(len(src), len(comp))]
    for (i0, j0), (i1, j1) in zip(anchors, anchors[1:]):
        si = [i for i in range(i0 + 1, i1) if not keep[i]]
        cj = [j for j in range(j0 + 1, j1) if not matched_comp[j]]
        if not si or not cj:
            continue
        sub = _lcs_pairs([norm(src[i]) for i in si], [norm(comp[j]) for j in cj])
        for a, b in sub:
            keep[si[a]] = True
            matched_comp[cj[b]] = True

    real = [i for i, t in enumerate(src) if t]
    n_src = len(real)
    n_matched = sum(matched_comp)
    prot = [i for i in real if is_protected(src[i])]
    return Alignment(
        keep=keep,
        n_src=n_src,
        n_comp=len(comp),
        n_matched=n_matched,
        variation_rate=(1 - n_matched / len(comp)) if comp else 0.0,
        comp_rate=(sum(keep[i] for i in real) / n_src) if n_src else 0.0,
        protected_total=len(prot),
        protected_kept=sum(keep[i] for i in prot),
    )
