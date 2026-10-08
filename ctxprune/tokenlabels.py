"""Map atom-level keep labels onto subword tokens, and token scores back to atoms.

Shared by training and inference so both see the exact same alignment.
Label 1 means keep, matching LLMLingua-2 (it reads softmax(logits)[..., 1]).
"""

from __future__ import annotations

from .atoms import Atom

IGNORE = -100


def atom_spans(atoms: list[Atom]) -> tuple[str, list[tuple[int, int]]]:
    """Full text plus the [start, end) char span of each atom's non-space text."""
    parts, spans, pos = [], [], 0
    for a in atoms:
        pos += len(a.ws)
        spans.append((pos, pos + len(a.text)))
        pos += len(a.text)
        parts.append(a.ws + a.text)
    return "".join(parts), spans


def char_to_atom(spans: list[tuple[int, int]], n_chars: int) -> list[int]:
    owner = [-1] * n_chars
    for i, (s, e) in enumerate(spans):
        for c in range(s, e):
            owner[c] = i
    return owner


def token_atoms(offsets: list[tuple[int, int]], owner: list[int]) -> list[int]:
    """Atom index for each token: the atom of its first non-space char, else -1
    (special tokens and whitespace-only tokens)."""
    out = []
    for s, e in offsets:
        a = -1
        for c in range(s, min(e, len(owner))):
            if owner[c] >= 0:
                a = owner[c]
                break
        out.append(a)
    return out


def token_atom_sets(offsets: list[tuple[int, int]], owner: list[int]) -> list[list[int]]:
    """Every atom each token touches (a token like "):" can span two atoms)."""
    out = []
    for s, e in offsets:
        seen: list[int] = []
        for c in range(s, min(e, len(owner))):
            a = owner[c]
            if a >= 0 and (not seen or seen[-1] != a):
                seen.append(a)
        out.append(seen)
    return out


def token_labels(tok_atom: list[int], keep: list[bool]) -> list[int]:
    return [IGNORE if a < 0 else int(keep[a]) for a in tok_atom]


def special_wrap(tok) -> tuple[list[int], list[int]]:
    """Token ids the tokenizer puts before and after content ([CLS]/[SEP], <bos>/<eos>, ...)."""
    plain = tok("hello world", add_special_tokens=False)["input_ids"]
    full = tok("hello world")["input_ids"]
    for i in range(len(full) - len(plain) + 1):
        if full[i:i + len(plain)] == plain:
            return full[:i], full[i + len(plain):]
    raise ValueError("could not locate content inside special tokens")
