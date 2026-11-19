"""Atom segmentation: the unit the compressor keeps or drops.

An atom is either a "word" that glues identifier-ish characters together
(paths, URLs, versions, IPs, snake_case, UUIDs) or a single punctuation
character. Each atom remembers the whitespace that preceded it, so
``detok(atoms, keep)`` rebuilds kept text exactly as it appeared in the
source, without the "0. 21" / "ord _ 8f3a" artifacts LLMLingua-2 produces.
"""

from __future__ import annotations

import math
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
_HEX_STRICT_RE = re.compile(r"^(?=.*\d)[0-9a-fA-F]{7,}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CAMEL_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2}[a-z]")
_NAME_PREFIX_RE = re.compile(r"^(?:Mc|Mac|De|Di|La|Le|Van|Von|O')[A-Z][a-z]+$")
_MIXED_ALNUM_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._-]{4,}$")
_DECADE_RE = re.compile(r"^\d{4}s$")
_ABBR_DOT_RE = re.compile(r"^(?:[A-Za-z]\.){1,4}[A-Za-z]?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T_]\d{2}:\d{2}(?::\d{2})?)?")
_SEMVER_RE = re.compile(r"^[vV]?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.]+)?$")
_URL_EMAIL_PATH_RE = re.compile(r"^(?:https?://|[\w.+-]+@[\w-]+\.[\w.]+|/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+)$")
_CLI_FLAG_RE = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9_-]*$")
_SPLIT_ID_RE = re.compile(r"[A-Za-z]+|\d+|[^\w\s]|_", re.UNICODE)
_JSON_KV_LINE_RE = re.compile(r'^\s*"[^"\n]{1,60}"\s*:')
_CODE_KW_RE = re.compile(
    r"^\s*(?:def |class |import |from \S+ import |return |public |private |protected |"
    r"function |const |let |var |package |#include\b|func |fn |struct |interface )"
)
_PROSE_FUNC = frozenset(
    "the and of to in is was that for with as on are be this it by from we you they have had not but or at an were "
    "which their been has would will can so if about who when there all more also one two he she his her its our my"
    .split()
)


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


def detok(atoms: list[Atom], keep: list[bool], collapse_ws: bool = True, preserve_para: bool = True) -> str:
    """Rebuild text from kept atoms.

    Kept atoms keep their own leading whitespace. When an atom's predecessor
    was dropped, the dropped run's whitespace is collapsed to the "largest"
    whitespace seen in that run (a paragraph break ``\\n\\n`` wins over ``\\n``,
    which wins over a space), so paragraph and line structure survive deletions.
    """
    out: list[str] = []
    pending = ""
    for a, k in zip(atoms, keep):
        if k:
            ws = a.ws
            if pending and collapse_ws:
                ws = _merge_ws(pending, a.ws, preserve_para=preserve_para)
            if not out:
                ws = ws if not collapse_ws else ""
            out.append(ws + a.text)
            pending = ""
        else:
            pending = _merge_ws(pending, a.ws, preserve_para=preserve_para) if pending else (a.ws or " ")
    return "".join(out)


_WS_MERGED = ("", " ", "\n", "\n\n")


def _ws_class(ws: str) -> int:
    """0 empty, 1 blank without newline, 2 one newline, 3 paragraph break (>= 2 newlines)."""
    if not ws:
        return 0
    n = ws.count("\n")
    return 3 if n >= 2 else (2 if n else 1)


def prepare_detok(atoms: list[Atom]) -> tuple[list[str], list[str], list[int]]:
    """Per-atom static data for ``detok_prepared``: (ws + text, text, whitespace class)."""
    return (
        [a.ws + a.text for a in atoms],
        [a.text for a in atoms],
        [_ws_class(a.ws) for a in atoms],
    )


def detok_prepared(pre: tuple[list[str], list[str], list[int]], keep: list[bool]) -> str:
    """``detok(atoms, keep)`` (collapse_ws and preserve_para on) from ``prepare_detok`` data, byte-identical."""
    pc, tx, cl = pre
    out: list[str] = []
    append = out.append
    merged = _WS_MERGED
    pend = -1
    for i, k in enumerate(keep):
        if k:
            if pend >= 0:
                if out:
                    c = cl[i]
                    append(merged[c if c > pend else pend] + tx[i])
                else:
                    append(tx[i])
                pend = -1
            else:
                append(pc[i] if out else tx[i])
        else:
            c = cl[i]
            if pend < 0:
                pend = c if c > 1 else 1
            elif c > pend:
                pend = c
    return "".join(out)


def _merge_ws(a: str, b: str, preserve_para: bool = True) -> str:
    if preserve_para and (a.count("\n") >= 2 or b.count("\n") >= 2):
        return "\n\n"
    if "\n" in a or "\n" in b:
        return "\n"
    return " " if (a or b) else ""


def is_protected(atom: str) -> bool:
    """Atoms an agent is likely to need verbatim later (IDs, numbers, paths)."""
    if not atom:
        return False
    if atom.isascii() and atom.isalpha():
        if atom.islower():
            return False
        if len(atom) >= 3 and atom.isupper() and atom not in _CAPS_WORDS:
            return True
        return bool(_CAMEL_RE.search(atom))
    if not any(c.isalnum() for c in atom):
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


def is_strict_id(atom: str) -> bool:
    """Strict technical identifier: flags, paths, UUIDs, ISO dates, semver, hex hashes, symbols."""
    if not atom:
        return False
    if atom.isascii() and atom.isalpha():
        if atom.islower() or len(atom) < 4:
            return False
        return bool(_CAMEL_RE.search(atom) and not _NAME_PREFIX_RE.match(atom))
    if not any(c.isalnum() for c in atom):
        return False
    core = atom.lstrip("-/~.$@#")
    if atom != core:
        return True
    if _UUID_RE.match(atom) or _ISO_DATE_RE.match(atom) or _SEMVER_RE.match(atom):
        return True
    if _HEX_STRICT_RE.match(core):
        return True
    if any(c in core for c in "_/@="):
        return True
    if ":" in core and not core.endswith(":"):
        return True
    if "." in core and not core.endswith(".") and not _ABBR_DOT_RE.match(core):
        return True
    if len(core) >= 4 and _CAMEL_RE.search(core) and not _NAME_PREFIX_RE.match(core):
        return True
    return bool(_MIXED_ALNUM_RE.match(core) and not _DECADE_RE.match(core))


def is_prose_artifact_id(atom: str) -> bool:
    """Unmistakable technical artifact inside natural prose (URLs, emails, paths, UUIDs, hashes, snake_case, CLI flags)."""
    if not atom or len(atom) < 4:
        return False
    if atom.isascii() and atom.isalpha():
        return False
    if _URL_EMAIL_PATH_RE.match(atom) or _CLI_FLAG_RE.match(atom):
        return True
    if _UUID_RE.match(atom) or _ISO_DATE_RE.match(atom) or _SEMVER_RE.match(atom):
        return True
    if len(atom) >= 8 and _HEX_STRICT_RE.match(atom):
        return True
    if atom.count("/") >= 2 and not atom.endswith("/"):
        return True
    return "_" in atom and not atom.startswith("_") and not atom.endswith("_") and any(c.islower() for c in atom)


def line_spans(atoms: list[Atom]) -> list[tuple[int, int]]:
    """Return [start_idx, end_idx) atom ranges per line."""
    spans: list[tuple[int, int]] = []
    start = 0
    for i, a in enumerate(atoms):
        if i > start and "\n" in a.ws:
            spans.append((start, i))
            start = i
    if start < len(atoms):
        spans.append((start, len(atoms)))
    return spans


def is_technical_text(atoms: list[Atom], raw_text: str | None = None) -> bool:
    """Fast heuristic distinguishing code, JSON, CLI/MCP tool logs & KV records from natural prose."""
    words = [a.text for a in atoms[:1200] if a.text]
    mw = max(1, len(words))

    if raw_text:
        s = raw_text.lstrip()
        if (s.startswith("{") and ":" in s[:250]) or (s.startswith("[") and ('"' in s[:150] or "{" in s[:150])):
            return True
        head_lines = raw_text[:4000].splitlines()
        if head_lines:
            if sum(1 for ln in head_lines if _JSON_KV_LINE_RE.match(ln)) >= 3:
                return True
            if sum(1 for ln in head_lines if _CODE_KW_RE.match(ln)) >= 3:
                return True
            if sum(1 for ln in head_lines[:10] if ln.strip().startswith("<") and ">" in ln) >= 2:
                return True

    func_ratio = sum(1 for w in words if w.lower() in _PROSE_FUNC) / mw
    if func_ratio >= 0.22:
        return False

    code_punct = 0
    lim = min(len(atoms), 1200)
    for idx in range(lim):
        t = atoms[idx].text
        if t in (";", "=", "==", "!=", "<=", ">=", "->", "=>"):
            code_punct += 1
        elif t in ("{", "}"):
            if t == "{" and idx + 2 < len(atoms) and atoms[idx + 2].text == "}" and atoms[idx + 1].text.isalpha():
                continue
            if t == "}" and idx >= 2 and atoms[idx - 2].text == "{" and atoms[idx - 1].text.isalpha():
                continue
            code_punct += 1

    prot_cnt = sum(1 for w in words if is_protected(w))
    art_cnt = sum(1 for w in words if is_prose_artifact_id(w) or (len(w) >= 3 and w.startswith(("/", "--", "$"))))

    lines = line_spans(atoms[:lim])
    kv_lines = 0
    _KV_SCALARS = ("true", "false", "null", "none", "ok", "failed", "error")
    for ls, le in lines:
        line_chars = sum(len(atoms[j].text) + len(atoms[j].ws) for j in range(ls, le))
        if le > ls + 2 and line_chars <= 110 and le - ls <= 14:
            t0, t1 = atoms[ls].text, atoms[ls + 1].text
            if t1 == ":" and t0.replace("_", "").replace("-", "").isalnum():
                rest_has_val = any(
                    any(c.isdigit() for c in atoms[j].text) or atoms[j].text.lower() in _KV_SCALARS
                    for j in range(ls + 2, le)
                )
                if t0.islower() or "_" in t0 or "-" in t0 or rest_has_val:
                    kv_lines += 1
        elif le > ls + 1 and line_chars <= 110 and "=" in atoms[ls].text:
            kv_lines += 1

    if code_punct / mw >= 0.03 or art_cnt / mw >= 0.03:
        return True
    if prot_cnt / mw >= 0.11 and func_ratio < 0.18:
        return True
    return len(lines) >= 4 and kv_lines >= 3 and (kv_lines / len(lines)) >= 0.20


def est_atom_tokens(text: str, ws: str = "") -> float:
    """Zero-dependency LLM subword token cost estimator across prose, code, JSON, and CJK."""
    if not text:
        return 0.0
    extra_ws = 0.7 if "\n" in ws else 0.0
    o = ord(text[0])
    if len(text) == 1:
        if not text.isalnum():
            return 0.85 + extra_ws
        if 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xAC00 <= o <= 0xD7AF:
            return 0.90 + extra_ws
        return 1.0 + extra_ws
    if text.isascii():
        if text.isalpha():
            n = len(text)
            return (1.0 if n <= 6 else 1.0 + (n - 6) / 5.0) + extra_ws
        if text.isdigit():
            return math.ceil(len(text) / 3.0) + extra_ws
        cost = 0.0
        for part in _SPLIT_ID_RE.findall(text):
            if part.isalpha():
                cost += 1.0 if len(part) <= 6 else 1.0 + (len(part) - 6) / 5.0
            elif part.isdigit():
                cost += math.ceil(len(part) / 3.0)
            else:
                cost += 0.75
        return max(1.0, cost) + extra_ws
    if any(ord(c) > 0x036F for c in text):
        return max(1.0, len(text) / 3.2) + extra_ws
    return max(1.0, len(text) / 4.2) + extra_ws


def norm(atom: str) -> str:
    return atom.casefold()
