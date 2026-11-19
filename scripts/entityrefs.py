"""Opt-in cross-call MCP JSON entity refs (T1.10b / idea 145). OFF unless the caller builds an EntityRefs.

A nested JSON object (>= MIN_CHARS compact chars, >= 2 keys) that already appeared, byte for byte, in an EARLIER tool
output of the same session is replaced by `{"$ref":"#K/json/pointer"}`: output #K, JSON pointer (RFC 6901) of the
first place it appeared. The first output that uses a ref carries a one-line legend. Lossless: expand() rebuilds the
original text byte for byte from the session state; an output that does not round-trip exactly (pretty-printed, odd
whitespace, duplicate keys, a `$ref` key already in the data) is never rewritten (fail open).

Why a pointer into the earlier output and not an id: the first occurrence is never annotated (no cost when nothing
repeats). Why it resolves: only objects seen in an UNREPLACED region are registered, and every ancestor of such an
object is unreplaced too, so the pointer is valid in the text the model saw.

  s = EntityRefs(tok=count_tokens)    # tok(str) -> int; a ref is used only when it is shorter in tokens
  new, info = s.process(text)         # text should already be json_compact'ed; counts as output #n
  s.skip()                            # an output that was replaced by something else (exact-dup marker): counts as #n
  expand(new, s_before) -> original   # s_before: a state snapshot taken before process() (copy.deepcopy)
"""
from __future__ import annotations

import json
import re

MIN_CHARS = 40   # fixed on dev (github 1-22, notion 1-28): 40 >= 60 > 100 > 200
MAX_AGE = 100
MAX_CHARS = 50000          # longer outputs are spilled by Claude Code: not touched
LEGEND = ('[ctxprune: {"$ref":"#N/path"} = the JSON object at JSON-pointer /path inside tool output #N above, '
          'verbatim]')
REF_RE = re.compile(r"^#(\d+)((?:/[^/]*)*)$")


class _Dup(Exception):
    pass


def _pairs(items):
    d = {}
    for k, v in items:
        if k in d:
            raise _Dup
        d[k] = v
    return d


def _dumps(o) -> str:
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def _esc(k: str) -> str:
    return k.replace("~", "~0").replace("/", "~1")


def _unesc(k: str) -> str:
    return k.replace("~1", "/").replace("~0", "~")


def _has_ref_key(o) -> bool:
    if isinstance(o, dict):
        return "$ref" in o or any(_has_ref_key(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_ref_key(v) for v in o)
    return False


def _parse(text: str):
    """(obj, trailing_newline) when text is exactly the compact serialization of a JSON object/array, else None."""
    s = text[:-1] if text.endswith("\n") else text
    if not s or s[0] not in "{[" or len(s) > MAX_CHARS:
        return None
    try:
        obj = json.loads(s, object_pairs_hook=_pairs)
    except (_Dup, ValueError, RecursionError):
        return None
    try:
        if _dumps(obj) != s or _has_ref_key(obj):
            return None
    except RecursionError:
        return None
    return obj, len(s) != len(text)


def _lookup(obj, ptr: str):
    for seg in ptr.split("/")[1:]:
        seg = _unesc(seg)
        obj = obj[int(seg)] if isinstance(obj, list) else obj[seg]
    return obj


class EntityRefs:
    def __init__(self, min_chars: int = MIN_CHARS, tok=None, max_age: int = MAX_AGE, legend: bool = True):
        self.min_chars, self.max_age, self.legend = min_chars, max_age, legend
        self.tok = tok or (lambda s: max(1, len(s) // 3))
        self.n = 0
        self.seen: dict[str, tuple[int, str]] = {}   # canonical object text -> (output #, pointer)
        self.trees: dict[int, object] = {}           # output # -> original parsed tree (for expand)
        self.legend_out = False

    def skip(self) -> None:
        self.n += 1

    def process(self, text: str, refs: bool = True) -> tuple[str, dict]:
        """refs=False: register only (control arms). Returns (text, info{refs, legend, saved_tok})."""
        self.n += 1
        k = self.n
        info = {"refs": 0, "legend": False}
        try:
            p = _parse(text)
            if p is None:
                return text, info
            obj, nl = p
            new_seen: dict[str, tuple[int, str]] = {}

            def rec(o, ptr):
                if isinstance(o, dict):
                    s = _dumps(o)
                    if len(o) >= 2 and len(s) >= self.min_chars:
                        hit = self.seen.get(s)
                        if hit is not None and ptr and hit[0] >= k - self.max_age and refs:   # root never replaced
                            r = _dumps({"$ref": f"#{hit[0]}{hit[1]}"})
                            if self.tok(r) < self.tok(s):
                                info["refs"] += 1
                                info.setdefault("items", []).append((ptr, hit[0], hit[1], o))
                                return {"$ref": f"#{hit[0]}{hit[1]}"}
                        new_seen.setdefault(s, (k, ptr))
                    return {a: rec(v, f"{ptr}/{_esc(a)}") for a, v in o.items()}
                if isinstance(o, list):
                    return [rec(v, f"{ptr}/{i}") for i, v in enumerate(o)]
                return o

            out = rec(obj, "")
            for s, v in new_seen.items():
                self.seen[s] = v      # newest occurrence wins: stays within max_age as long as it keeps recurring
            self.trees[k] = obj
            self.trees.pop(k - self.max_age - 1, None)
            if not info["refs"]:
                return text, info
            new = _dumps(out) + ("\n" if nl else "")
            if self.legend and not self.legend_out:
                new = LEGEND + "\n" + new
                self.legend_out, info["legend"] = True, True
            return new, info
        except Exception:  # noqa: BLE001 - fail open
            return text, {"refs": 0, "legend": False, "error": True}


def expand(rendered: str, st: "EntityRefs") -> str:
    """Inverse of EntityRefs.process given the state the output was processed against. Never raises: returns the
    input unchanged when it holds no refs."""
    try:
        t = rendered
        if t.startswith(LEGEND + "\n"):
            t = t[len(LEGEND) + 1:]
        elif not t.startswith(("{", "[")):
            return rendered
        nl = t.endswith("\n")
        obj = json.loads(t[:-1] if nl else t, object_pairs_hook=_pairs)

        def back(o):
            if isinstance(o, dict):
                if len(o) == 1 and isinstance(o.get("$ref"), str):
                    m = REF_RE.match(o["$ref"])
                    if m:
                        return _lookup(st.trees[int(m.group(1))], m.group(2))
                return {a: back(v) for a, v in o.items()}
            if isinstance(o, list):
                return [back(v) for v in o]
            return o

        return _dumps(back(obj)) + ("\n" if nl else "")
    except Exception:  # noqa: BLE001
        return rendered


def _self_test() -> None:
    import copy
    ent = {"id": 1, "login": "octocat", "html_url": "https://github.com/octocat", "type": "User", "site_admin": False}
    a = _dumps({"items": [{"n": 1, "user": ent}, {"n": 2, "user": dict(ent, id=2)}]})
    b = _dumps({"item": {"n": 3, "user": ent, "title": "x~y/z"}, "sp/ec~": dict(ent, id=2)})
    s = EntityRefs(min_chars=40)
    outs, snaps = [], []
    for t in (a, b, "plain text", b + "\n", a):
        snaps.append(copy.deepcopy(s))
        outs.append(s.process(t)[0])
    assert outs[0] == a and outs[2] == "plain text"
    assert outs[1].startswith(LEGEND + "\n") and '"$ref":"#1/items/0/user"' in outs[1], outs[1]
    assert '"$ref":"#1/items/1/user"' in outs[1]
    assert LEGEND not in outs[3] and outs[3].endswith("\n") and len(outs[3]) < len(b)
    for orig, new, snap in zip((a, b, "plain text", b + "\n", a), outs, snaps):
        assert expand(new, snap) == orig, (orig, new)
    assert EntityRefs().process('{"a": 1,\n "b": 2}')[0] == '{"a": 1,\n "b": 2}'   # not compact: untouched
    assert EntityRefs().process(_dumps({"$ref": "x", "b": 1}))[1]["refs"] == 0
    print("entityrefs self-test OK")


if __name__ == "__main__":
    _self_test()
