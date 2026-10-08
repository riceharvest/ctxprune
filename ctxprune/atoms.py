"""Atom segmentation: the unit the compressor keeps or drops.

An atom is either a "word" that glues identifier-ish characters together
(paths, URLs, versions, IPs, snake_case, UUIDs) or a single punctuation
character. Each atom remembers the whitespace that preceded it, so
``detok(atoms, keep)`` rebuilds kept text exactly as it appeared in the
source, without the "0. 21" / "ord _ 8f3a" artifacts LLMLingua-2 produces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Scripts written without spaces (Han, kana) get one atom per character, so
# a deletion inside a sentence is still a deletion and not a "rewrite".
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_W = rf"[^\W{_CJK}]"  # word char that is not CJK
# A word may start with one path/flag sigil, then word chars, with
# identifier punctuation allowed only *between* word chars.
_WORD = rf"(?:--?|[/~.$@#])?{_W}(?:(?:{_W}|[.:/@%+#~=?&'\-])*{_W})?"
_ATOM_RE = re.compile(rf"(\s*)({_WORD}|[{_CJK}]|[^\s])", re.UNICODE)

# All-caps English that is prose, not an identifier (license headers, shouting).
_CAPS_WORDS = set(
    "THE AND FOR ANY NOT BUT ARE WAS YOU ALL WITH FROM THIS THAT WITHOUT WARRANTIES WARRANTY SOFTWARE KIND "
    "IMPLIED INCLUDING LIMITED COPYRIGHT OTHER LIABILITY USE BASIS CONDITIONS SUCH EVENT SHALL HOLDERS "
    "CONTRIBUTORS DAMAGES PURPOSE PARTICULAR FITNESS MERCHANTABILITY EXPRESS OUT CONNECTION ARISING "
    "WHETHER CONTRACT TORT OTHERWISE PROVIDED LICENSE NOTICE NOTE IMPORTANT".split()
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,}$")
_CAMEL_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2}[a-z]")


@dataclass(frozen=True)
class Atom:
    ws: str  # whitespace preceding the atom in the source
    text: str


def atomize(text: str) -> list[Atom]:
    atoms: list[Atom] = []
    pos = 0
    for m in _ATOM_RE.finditer(text):
        if m.start() != pos:  # unreachable by construction; guard anyway
            raise ValueError(f"atomizer skipped {text[pos:m.start()]!r}")
        atoms.append(Atom(m.group(1), m.group(2)))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        raise ValueError(f"atomizer left non-space tail {tail!r}")
    if tail:  # trailing whitespace rides on a sentinel-free empty atom
        atoms.append(Atom(tail, ""))
    return atoms


def detok(atoms: list[Atom], keep: list[bool], collapse_ws: bool = True) -> str:
    """Rebuild text from kept atoms.

    Kept atoms keep their own leading whitespace. When an atom's predecessor
    was dropped, the dropped run's whitespace is collapsed to the "largest"
    whitespace seen in that run (a newline wins over a space), so line
    structure survives deletions.
    """
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


def _merge_ws(a: str, b: str) -> str:
    if "\n" in a or "\n" in b:
        return "\n"
    return " " if (a or b) else ""


def is_protected(atom: str) -> bool:
    """Atoms an agent is likely to need verbatim later (IDs, numbers, paths)."""
    if not atom or not any(c.isalnum() for c in atom):
        return False
    if any(c.isdigit() for c in atom):
        return True
    core = atom.lstrip("-/~.$@#")
    if any(c in core for c in "_/.:@="):
        return True
    if _HEX_RE.match(core):
        return True
    if len(core) >= 3 and core.isupper() and core not in _CAPS_WORDS:
        return True
    if _CAMEL_RE.search(core):
        return True
    return atom != core  # flags like --format, paths like /tmp


def norm(atom: str) -> str:
    return atom.casefold()
