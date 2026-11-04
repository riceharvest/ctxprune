"""Structured-data ideas: JSON, config files, tables, HTML code blocks, base64 fields.

#31 #32 #35 #128 #129 #130 #145 #316 (JSON), #36 (YAML/TOML/INI comments), #37 #38 #407 #408 #412 (tables),
#228 (hex runs), #323 (highlighter HTML), #325 (base64 content fields).

Conventions used throughout:
  * JSON encoders work on lexemes (a tiny tokenizer, `lex_parse` / `lex_dump`), never on json.loads/dumps output, so number
    spelling (1E5, 1.0, big ints), duplicate keys and key order survive.  The oracles do the opposite and are independent of
    that tokenizer: they use json.loads with object_pairs_hook / parse_float / parse_int / parse_constant that keep the
    lexeme, so "equal" means equal including duplicate keys, order and number spelling.
  * Encoders are total (any exception means "leave the input alone"), and never return something longer than the input.
  * In-band signals start with the sigil "§".  Text that itself starts with "§" is escaped with "§raw\\n" (so the escape path is
    allowed to grow, and those encoders set idempotent=False), and encoders whose body uses bare "§" tokens refuse input that
    contains "§" anywhere.
"""
from __future__ import annotations

import base64
import configparser
import hashlib
import json
import os
import random
import re
import tempfile
import tomllib
from html.parser import HTMLParser

from harness import Fixture, case

SIG = "§"
RAW = "§raw\n"


def guarded(text, fn):
    """Total + self-gating wrapper: any failure, or a result that is not strictly shorter, hands the input back."""
    try:
        out = fn(text)
    except Exception:  # noqa: BLE001  (RecursionError, ValueError, IndexError ...: unknown shape means leave it alone)
        return text
    return out if isinstance(out, str) and len(out) < len(text) else text


def make_codec(hdr, enc, dec):
    """(encode, decode) pair for an in-band header `hdr`; text that starts with the sigil is escaped with RAW."""

    def encode(text):
        if text.startswith(SIG):
            return RAW + text
        return guarded(text, enc)

    def decode(text):
        if text.startswith(RAW):
            return text[len(RAW):]
        if text.startswith(hdr):
            return dec(text)
        return text

    return encode, decode


# ---------------------------------------------------------------------------------------------------------------
# JSON toolkit: lexeme level (used by encoders) and parse level with kept lexemes (used by oracles)

_JSTR = r'"(?:[^"\\]|\\.)*"'
_JSTR_RE = re.compile(_JSTR)
_STRIP = re.compile(rf"({_JSTR})|[ \t\r\n]+")
_TOKEN = re.compile(rf'[ \t\r\n]*(?:({_JSTR})|([{{}}\[\]:,])|([^\s"{{}}\[\]:,]+))')
_HOOKS = dict(object_pairs_hook=lambda p: ("O", p), parse_float=lambda s: ("N", s), parse_int=lambda s: ("N", s),
              parse_constant=lambda s: ("N", s))


def jtree(text):
    """Strict parse that keeps number lexemes, duplicate keys and key order (raises on invalid JSON)."""
    return json.loads(text, **_HOOKS)


def is_json(text):
    try:
        jtree(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def strip_ws(text):
    """Remove insignificant whitespace from text that is already known to be valid JSON."""
    return _STRIP.sub(lambda m: m.group(1) or "", text)


def lex_parse(text):
    """Valid JSON text -> ("o", [(key_lexeme, node)]) | ("a", [node]) | ("l", lexeme)."""
    toks = [m.group(1) or m.group(2) or m.group(3) for m in _TOKEN.finditer(text)]
    at = [0]

    def value():
        t = toks[at[0]]
        at[0] += 1
        if t == "{":
            pairs = []
            if toks[at[0]] == "}":
                at[0] += 1
                return ("o", pairs)
            while True:
                k = toks[at[0]]
                at[0] += 2  # key and ':'
                pairs.append((k, value()))
                sep = toks[at[0]]
                at[0] += 1
                if sep == "}":
                    return ("o", pairs)
        if t == "[":
            items = []
            if toks[at[0]] == "]":
                at[0] += 1
                return ("a", items)
            while True:
                items.append(value())
                sep = toks[at[0]]
                at[0] += 1
                if sep == "]":
                    return ("a", items)
        return ("l", t)

    return value()


def lex_dump(n):
    if n[0] == "l":
        return n[1]
    if n[0] == "a":
        return "[" + ",".join(lex_dump(c) for c in n[1]) + "]"
    return "{" + ",".join(k + ":" + lex_dump(v) for k, v in n[1]) + "}"


def value_end(s, i):
    """Index just past the JSON value that starts at s[i] (a minified quoted string, container or bare token)."""
    if s[i] == '"':
        return _JSTR_RE.match(s, i).end()
    if s[i] in "{[":
        depth, j = 0, i
        while True:
            c = s[j]
            if c == '"':
                j = _JSTR_RE.match(s, j).end()
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
            j += 1
            if depth == 0:
                return j
    j = i
    while j < len(s) and s[j] != ",":
        j += 1
    return j


def json_codec_oracle(decode):
    """Independent oracle for JSON-to-JSON-ish encoders: decode(e) must parse (lexemes, duplicate keys, key order kept) to
    exactly what x parses to; text that is not JSON must come back as it was."""

    def oracle(x, e):
        d = decode(e)
        try:
            a = jtree(x)
        except Exception:  # noqa: BLE001
            return d == x
        try:
            b = jtree(d)
        except Exception:  # noqa: BLE001
            return False
        return a == b

    return oracle


# ---------------------------------------------------------------------------------------------------------------
# sample data shared by several JSON fixtures

_LOGINS = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi"]
_TITLES = ["Crash when config file is empty", "Add retry to the upload step", "Docs: clarify install on Windows",
           "Flaky test test_cache_eviction", "Support Python 3.13", "Bump urllib3 to 2.2", "Typo in README",
           "Memory leak in the worker pool", "Deprecate the legacy flag", "CI is red on main"]


def gh_user(i):
    n = i % len(_LOGINS)
    return {"login": _LOGINS[n], "id": 4000 + 37 * n, "avatar_url": f"https://avatars.githubusercontent.com/u/{4000 + 37 * n}?v=4",
            "html_url": f"https://github.com/{_LOGINS[n]}", "type": "User", "site_admin": False}


_LABELS = [("bug", "d73a4a", "Something isn't working"), ("docs", "0075ca", "Improvements or additions to documentation"),
           ("ci", "fbca04", "Continuous integration"), ("good first issue", "7057ff", "Good for newcomers")]


def gh_label(i):
    name, color, desc = _LABELS[i % len(_LABELS)]
    return {"id": 2080000 + i % len(_LABELS), "name": name, "color": color, "default": i % len(_LABELS) == 0, "description": desc}


MILESTONE = {"id": 9001, "number": 3, "title": "v2.1", "state": "open", "open_issues": 7, "closed_issues": 21,
             "html_url": "https://github.com/acme/widgets/milestone/3"}


def gh_issue(i):
    return {"number": 100 + i, "title": _TITLES[i % len(_TITLES)], "state": "open" if i % 3 else "closed", "user": gh_user(i * 3),
            "labels": [gh_label(i), gh_label(i + 1)][: 1 + i % 2], "milestone": MILESTONE if i % 2 == 0 else None,
            "comments": (i * 7) % 11, "created_at": f"2024-05-{1 + i:02d}T10:{i:02d}:00Z"}


def pretty(o, indent=2):
    return json.dumps(o, indent=indent, ensure_ascii=False) + "\n"


def compact(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------------------------------------------
# #31 JSON minify, at lexeme level

def minify_json(text):
    return strip_ws(text) if is_json(text) else text


def same_json(x, e):
    """Independent oracle: both sides strict-parse to the same tree incl. number lexemes, duplicate keys and key order."""
    try:
        a = jtree(x)
    except Exception:  # noqa: BLE001
        return e == x  # not JSON: must be untouched
    try:
        b = jtree(e)
    except Exception:  # noqa: BLE001
        return False
    return a == b


TRAPS = """{
  "ids": [1E5, 1.0, -0, 0.10, 1e-7, 123456789012345678901234567890, 0.1e+2],
  "dup": { "a": 1, "a": 2, "z": null, "a": [ ] },
  "text": "  padded \\"quote\\" \\\\ back \\u00e9 \\ud83d\\ude00 \\/ ",
  "empty": { },
  "tail": [ true , false , null ]
}
"""
BIG_INT = "[\n  1" + "0" * 5000 + ",\n  2\n]\n"  # beyond json.loads' int digit limit if a parser converted it

case(
    31,
    claim="All whitespace outside strings is removed from a complete JSON document; number spellings (1E5, 1.0, big ints), duplicate keys, key order and every string byte are untouched, and anything that is not valid JSON is returned as is.",
    loses="indentation, newlines and spaces between tokens (including the trailing newline)",
    encode=lambda t: guarded(t, minify_json),
    equivalent=same_json,
    fixtures=[
        Fixture("pretty MCP issue list", pretty({"total_count": 6, "items": [gh_issue(i) for i in range(6)]}), True),
        Fixture("lexeme traps: 1E5, 1.0, dup keys, escapes", TRAPS, True),
        Fixture("tab-indented CRLF", pretty({"a": [1, 2, {"b": "x y"}]}, indent="\t").replace("\n", "\r\n"), True),
        Fixture("already minified", compact({"a": [1.0, "x y", {"b": None}]}), False),
        Fixture("trailing comma is not JSON", '{\n  "a": 1,\n}\n', False),
        Fixture("truncated JSON", pretty({"a": [1, 2, 3], "b": {"c": "d"}})[:-12], False),
        Fixture("two documents (NDJSON) are not one JSON", '{"a": 1}\n{"a": 2}\n', False),
        Fixture("python repr", "{'a': 1, 'b': [1, 2]}\n", False),
        Fixture("5000-digit integer", BIG_INT, None),
        Fixture("NaN and Infinity", "[\n  NaN,\n  Infinity,\n  -Infinity\n]\n", None),
        Fixture("BOM before the document", "\ufeff" + pretty({"a": 1}), None),
        Fixture("non-JSON whitespace between tokens", '[1,\u00a02,\x0c3]\n', None),
        Fixture("deep nesting", "[" * 3000 + "]" * 3000, None),
        Fixture("top-level string with spaces", '  "  keep   these  "  \n', None),
    ],
    min_gain=0.15,
)

# ---------------------------------------------------------------------------------------------------------------
# #32 Columnar encoding for uniform arrays of objects

_BARE = re.compile(r"[A-Za-z](?:[A-Za-z0-9_.@/: +-]*[A-Za-z0-9_.@/:+-])?\Z")
_LITS = {"true", "false", "null", "NaN", "Infinity"}
COLS = "§cols "


def _cell(node):
    if node[0] == "l":
        t = node[1]
        if t[0] == '"' and _BARE.match(t[1:-1]) and t[1:-1] not in _LITS:
            return t[1:-1]
        return t
    return lex_dump(node)


def _cols_enc(text):
    if not is_json(text):
        return text
    root = lex_parse(text)
    if root[0] != "a" or len(root[1]) < 3 or any(r[0] != "o" for r in root[1]):
        return text
    schema: list[str] = []
    for _, pairs in root[1]:
        keys = [k for k, _v in pairs]
        if len(set(keys)) != len(keys):
            return text  # duplicate keys inside one object: no column can hold both
        at = 0
        for n, k in enumerate(keys):
            if k in schema:
                idx = schema.index(k)
                if idx < at:
                    return text  # key order differs from the schema: a column list cannot carry per-row order
                at = idx + 1
            else:  # a new key goes just before the next key this object shares with the schema, else at the end
                nxt = next((schema.index(kk) for kk in keys[n + 1:] if kk in schema), None)
                pos = max(at, len(schema) if nxt is None else nxt)
                schema.insert(pos, k)
                at = pos + 1
    if len(schema) < 2:
        return text
    rows = []
    for _, pairs in root[1]:
        d = dict(pairs)
        rows.append(",".join(_cell(d[k]) if k in d else "" for k in schema))
    return COLS + ",".join(schema) + "\n" + "\n".join(rows)


def _cols_dec(text):
    head, _, body = text.partition("\n")
    schema = _JSTR_RE.findall(head[len(COLS):])
    objs = []
    for line in body.split("\n"):
        i, pairs = 0, []
        for n, k in enumerate(schema):
            if i < len(line) and line[i] not in ",":
                j = value_end(line, i)
                tok = line[i:j]
                if tok[0].isalpha() and tok not in _LITS:  # a bare word is a string; true/false/null/NaN/Infinity are literals
                    tok = '"' + tok + '"'
                pairs.append(k + ":" + tok)
                i = j
            if n < len(schema) - 1:
                assert line[i] == ",", "malformed row"
                i += 1
        objs.append("{" + ",".join(pairs) + "}")
    return "[" + ",".join(objs) + "]"


enc32, dec32 = make_codec(COLS, _cols_enc, _cols_dec)


def pr_list(n, optional=True):
    out = []
    for i in range(n):
        o = {"number": 400 + i, "title": _TITLES[i % len(_TITLES)], "state": ["OPEN", "MERGED", "CLOSED"][i % 3],
             "author": {"login": _LOGINS[i % len(_LOGINS)]}, "headRefName": f"feature/{_LOGINS[i % 8]}-{i}", "isDraft": i % 4 == 0,
             "additions": i * 13 % 90, "updatedAt": f"2024-06-{1 + i:02d}T08:{i:02d}:11Z"}
        if optional and i % 3 == 1:
            o["milestone"] = {"title": "v2.1"}  # optional key: absent on most rows
        if optional and i % 5 == 2:
            o["mergedAt"] = None
        out.append(o)
    return out


def slack_msgs(n):
    return [{"type": "message", "user": f"U{1000 + i % 5}", "text": f"deploy {i} finished, see https://ci.example.com/run/{i}",
             "ts": f"1700000{i:03d}.000{i:03d}", **({"thread_ts": f"1700000{i - 1:03d}.000100"} if i % 4 == 3 else {})} for i in range(n)]


TRICKY_ROWS = [
    {"id": 1, "name": "plain words", "v": 1.0, "w": "123", "x": "true", "y": "", "z": None},
    {"id": 2, "name": "with, comma", "v": 1e5 if False else 100000, "w": "null", "x": "a\"quote", "y": "line\nbreak", "z": [1, [2, "]"]]},
    {"id": 3, "name": "ünïcode ✓", "v": -0.0, "w": "Infinity", "x": "{\"k\": [1,2]}", "y": "tab\there", "z": {"a": {"b": "}"}}},
    {"id": 4, "name": "§cols not a header", "w": "NaN", "x": " lead", "z": False},
]

case(
    32,
    claim="An array of at least three objects becomes one header line of keys plus one comma-separated line per object; decoding restores each object's keys in their original order, absent keys (distinct from null and \"\"), nested values and number lexemes.",
    loses="whitespace between JSON tokens (the decoded array is minified)",
    encode=enc32,
    equivalent=json_codec_oracle(dec32),
    fixtures=[
        Fixture("gh pr list --json (pretty, optional keys)", pretty(pr_list(14)), True),
        Fixture("gh pr list --json (minified)", compact(pr_list(14)), True),
        Fixture("slack history, one optional key", pretty(slack_msgs(12)), True),
        Fixture("two objects only", compact(pr_list(2)), False),
        Fixture("key order differs in one object", compact([{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"b": 6, "a": 5}] * 3), False),
        Fixture("duplicate key inside an object", '[{"a":1,"a":2},{"a":3,"b":1},{"a":4,"b":2},{"a":5,"b":3}]', False),
        Fixture("array of scalars", compact(list(range(40))), False),
        Fixture("object, not array", compact({"items": pr_list(6)}), False),
        Fixture("looks like strings vs literals vs absent", compact(TRICKY_ROWS * 3), True),
        Fixture("number lexemes", '[{"n":1E5,"m":1.0},{"n":-0,"m":0.10},{"n":123456789012345678901234567890,"m":1e-7},{"n":2,"m":3}]', None),
        Fixture("text that starts with the sigil", "§cols \"a\",\"b\"\n1,2\n3,4\n", None),
        Fixture("a string value equal to the header", compact([{"a": "§cols \"a\"", "b": 1}] * 4), None),
    ],
    min_gain=0.15,
    idempotent=False,  # re-encoding encoded text starts with the sigil, so it is escaped (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #35 Dedupe repeated nested objects within one output

DEFS = "§defs "


def _dedupe_enc(text):
    if SIG in text or not is_json(text):
        return text
    root = lex_parse(text)
    defs: list[str] = []

    def walk_texts(n, acc, top):
        """Collect dump text of every non-root object node; returns this node's dump text."""
        if n[0] == "l":
            return n[1]
        if n[0] == "a":
            t = "[" + ",".join(walk_texts(c, acc, False) for c in n[1]) + "]"
        else:
            t = "{" + ",".join(k + ":" + walk_texts(v, acc, False) for k, v in n[1]) + "}"
        if n[0] == "o" and not top:
            acc.append(t)
        return t

    def replace(n, target, token):
        if n[0] == "l":
            return n
        if n[0] == "o" and lex_dump(n) == target:
            return ("l", token)
        if n[0] == "a":
            return ("a", [replace(c, target, token) for c in n[1]])
        return ("o", [(k, replace(v, target, token)) for k, v in n[1]])

    for _ in range(9):
        acc: list[str] = []
        walk_texts(root, acc, True)
        counts: dict[str, int] = {}
        for t in acc:
            counts[t] = counts.get(t, 0) + 1
        best, gain = None, 0
        for t, c in counts.items():
            g = c * (len(t) - 2) - (len(t) + 4)  # each use costs 2 chars ("§k"); one definition line costs text + "§k=" + "\n"
            if c > 1 and g > gain:
                best, gain = t, g
        if best is None:
            break
        defs.append(best)
        root = replace(root, best, f"§{len(defs)}")
    if not defs:
        return text
    return DEFS + f"{len(defs)}\n" + "".join(f"§{i}={d}\n" for i, d in enumerate(defs, 1)) + lex_dump(root)


def _dedupe_dec(text):
    lines = text.split("\n")
    n = int(lines[0][len(DEFS):])
    defs = [ln.split("=", 1)[1] for ln in lines[1:1 + n]]
    body = "\n".join(lines[1 + n:])
    for k in range(n, 0, -1):  # later definitions may mention earlier tokens
        body = body.replace(f"§{k}", defs[k - 1])
    return body


enc35, dec35 = make_codec(DEFS, _dedupe_enc, _dedupe_dec)

_OWNER = {"login": "acme-bot", "id": 77001, "avatar_url": "https://avatars.githubusercontent.com/u/77001?v=4",
          "html_url": "https://github.com/acme-bot", "type": "Bot", "site_admin": False}
_REPO = {"id": 31337, "name": "widgets", "full_name": "acme/widgets", "private": False, "owner": _OWNER,
         "html_url": "https://github.com/acme/widgets", "default_branch": "main"}


def issues_with_shared_objects(n):
    return [{"number": 100 + i, "title": _TITLES[i % len(_TITLES)], "repository": _REPO, "user": gh_user(i % 3),
             "milestone": MILESTONE, "labels": [gh_label(0), gh_label(1)]} for i in range(n)]


case(
    35,
    claim="Object values that occur more than once in a JSON document are defined once in a legend and replaced by a §k token (nested repeats first); decoding substitutes them back, so the document parses to the same tree.",
    loses="whitespace between JSON tokens (output is minified); a repeated object must match byte for byte after minifying, so key order or number spelling differences keep copies separate",
    encode=enc35,
    equivalent=json_codec_oracle(dec35),
    fixtures=[
        Fixture("issues sharing user, repo, milestone, labels (pretty)", pretty(issues_with_shared_objects(14)), True),
        Fixture("the same list, minified", compact(issues_with_shared_objects(14)), True),
        Fixture("gh issue list with repeated users", pretty([gh_issue(i) for i in range(12)]), True),
        Fixture("all objects distinct", compact([{"id": i, "name": f"n{i}", "tags": [{"k": i}]} for i in range(10)]), False),
        Fixture("repeated objects too small to pay for a legend", compact([{"a": 1}, {"a": 1}, {"a": 1}]), False),
        Fixture("not JSON", "ok ok ok\n" * 8, False),
        Fixture("key order differs between copies", compact([{"u": {"a": 1, "b": "xxxxxxxxxxxxxxxxxxxx"}}, {"u": {"b": "xxxxxxxxxxxxxxxxxxxx", "a": 1}}]), False),
        Fixture("sigil inside a string value", compact([{"note": "§1 is not an alias", "u": _OWNER}] * 5), None),
        Fixture("text that starts like encoder output", '§defs 1\n§1={"a":1}\n[§1,§1]', None),
        Fixture("nested repeats and duplicate keys", compact([{"r": {"o": _OWNER, "o": _OWNER, "n": 1.0}, "o": _OWNER}] * 5), True),
    ],
    min_gain=0.3,
    idempotent=False,  # encoded text starts with the sigil, so re-encoding escapes it
)

# ---------------------------------------------------------------------------------------------------------------
# #128 Normalise JSON string escapes and inline nested JSON strings

MARK = "§json"


def ser_str(s):
    out = json.dumps(s, ensure_ascii=False)
    return re.sub("[\ud800-\udfff\u2028\u2029]", lambda m: "\\u%04x" % ord(m.group()), out)


def _container(s):
    """The parsed value of s if s is exactly a JSON object or array (no surrounding whitespace), else None."""
    if s[:1] not in ("{", "[") or s[-1:] not in ("}", "]"):
        return None
    try:
        v = jtree(s)
    except Exception:  # noqa: BLE001
        return None
    return v if isinstance(v, (list, tuple)) else None


def ser(v):
    if isinstance(v, tuple):
        if v[0] == "N":
            return v[1]
        return "{" + ",".join(ser_str(k) + ":" + ser(x) for k, x in v[1]) + "}"
    if isinstance(v, list):
        return "[" + ",".join(ser(x) for x in v) + "]"
    if isinstance(v, str):
        lit = ser_str(v)
        inner = _container(v)
        if inner is not None:
            wrapped = '{"' + MARK + '":' + ser(inner) + "}"
            if len(wrapped) < len(lit):
                return wrapped
        return lit
    return {True: "true", False: "false", None: "null"}[v]


def _norm_escapes(text):
    return ser(jtree(text))


def enc128(text):
    if text.startswith(SIG) or MARK in text:
        return RAW + text  # the marker (or an escape) is already in the input: escape it so the marker stays unambiguous
    return guarded(text, _norm_escapes)


def _norm128(v, encoded):
    if isinstance(v, tuple) and v[0] == "O":
        if encoded and len(v[1]) == 1 and v[1][0][0] == MARK:
            return ("J", _norm128(v[1][0][1], encoded))
        return ("O", [(k, _norm128(x, encoded)) for k, x in v[1]])
    if isinstance(v, list):
        return [_norm128(x, encoded) for x in v]
    if isinstance(v, str):
        inner = _container(v)
        return ("J", _norm128(inner, encoded)) if inner is not None else v
    return v


def oracle128(x, e):
    """x and e carry the same values; a string that is itself a JSON object/array may appear inline (wrapped in the marker)."""
    if e.startswith(RAW):
        return e[len(RAW):] == x and (x.startswith(SIG) or MARK in x)  # the escape is only legitimate where the marker is ambiguous
    try:
        a = jtree(x)
    except Exception:  # noqa: BLE001
        return e == x
    if MARK in x:
        return False
    try:
        b = jtree(e)
    except Exception:  # noqa: BLE001
        return False
    return _norm128(a, False) == _norm128(b, True)


def go_escape(s):
    """Go's encoding/json HTML-escapes < > & ; PHP escapes / ."""
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("/", "\\/")


_ISSUE = {"number": 42, "title": "Crash on <empty> config & restart", "body": "Steps:\r\n1. run `app --config a/b.toml`\r\n2. see <error>\r\n",
          "user": {"login": "alice", "id": 4000}, "labels": [{"name": "bug", "color": "d73a4a"}], "state": "open", "score": 1.50}
MCP_WRAP = pretty({"content": [{"type": "text", "text": go_escape(compact(_ISSUE))}], "isError": False})
WEBHOOK = pretty({"event": "push", "delivery": "9f1c", "payload": go_escape(json.dumps(
    {"ref": "refs/heads/main", "commits": [{"id": "a1b2c3", "message": "fix <b>bold</b> & more", "added": [], "modified": ["src/app.py"]}],
     "repository": {"full_name": "acme/widgets", "html_url": "https://github.com/acme/widgets"}}, indent=1))})
DOUBLE = pretty({"outer": json.dumps({"inner": json.dumps({"k": [1, 2, {"deep": "v"}], "n": 1.0})})})

case(
    128,
    claim="Redundant string escapes (\\uXXXX for printable characters, \\/) are written as the characters, and a string value that is itself a JSON object or array is inlined as structure inside {\"§json\": ...}; every value, number spelling, duplicate key and key order is preserved, and input that already uses the marker key is escaped with a \"§raw\" prefix.",
    loses="the exact text of an inlined JSON string (its whitespace and escape spelling; only its parsed value survives), whitespace between tokens, and the spelling of \\uXXXX / \\/ escapes; \\r\\n escapes stay escaped because a raw newline cannot live inside a JSON string",
    encode=enc128,
    equivalent=oracle128,
    fixtures=[
        Fixture("MCP text wrapper around an issue (Go escapes)", MCP_WRAP, True),
        Fixture("webhook payload string (PHP slashes)", WEBHOOK, True),
        Fixture("JSON inside JSON inside JSON", DOUBLE, True),
        Fixture("no escapes, minified", compact({"a": "plain", "b": ["x", "y"], "n": 1.0}), False),
        Fixture("strings that only look like JSON", compact({"a": "{not json}", "b": "[1, 2", "c": "123", "d": "true", "e": " {\"k\": 1}"}), False),
        Fixture("not JSON", "plain log line with \\u003c in it\n", False),
        Fixture("the marker key already present", pretty({"§json": {"a": 1}, "s": "{\"a\": 1}"}), None),
        Fixture("the marker only inside a string value", compact({"s": "see §json docs", "t": "{\"a\": 1}"}), None),
        Fixture("text starting with the sigil", "§json is a note\n", None),
        Fixture("lone surrogate, U+2028, NUL", '{"a": "\\ud800", "b": "x\\u2028y", "c": "\\u0000", "d": "\\ud83d\\ude00 \\u00e9"}', None),
        Fixture("inlined JSON keeps duplicate keys and number lexemes", pretty({"s": '{"a": 1, "a": 2, "n": 1E5, "m": 1.0}'}), None),
        Fixture("embedded JSON string with leading space is left as a string", pretty({"s": ' {"a": 1}', "t": '{"b":"\\u003c"}'}), None),
    ],
    min_gain=0.15,
    idempotent=False,  # encoded output contains the marker, so re-encoding escapes it (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #129 Minify pretty-printed JSON blocks embedded in mixed output

_DEC = json.JSONDecoder(**_HOOKS)


def _find_block(lines, i):
    """Last line index of the balanced {...}/[...] block that starts at column 0 of lines[i] and ends its line, else None."""
    depth = 0
    for j in range(i, min(len(lines), i + 4000)):
        ln, k = lines[j], 0
        while k < len(ln):
            c = ln[k]
            if c == '"':
                m = _JSTR_RE.match(ln, k)
                if not m:
                    return None  # JSON strings cannot span lines
                k = m.end()
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return (j, k) if not ln[k + 1:].strip(" \t\r") else None
                if depth < 0:
                    return None
            k += 1
    return None


def minify_blocks(text):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i][:1] in ("{", "["):
            end = _find_block(lines, i)
            if end is not None:
                j, k = end
                block = "\n".join(lines[i:j] + [lines[j][: k + 1]])
                if is_json(block):
                    out.append(strip_ws(block) + lines[j][k + 1:])
                    i = j + 1
                    continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def segments(text):
    """Independent oracle view (json raw_decode instead of bracket matching): [("T", text) | ("J", tree)]."""
    segs, buf, pos, n = [], [], 0, len(text)
    while pos < n:
        eol = text.find("\n", pos)
        eol = n if eol < 0 else eol
        if text[pos] in "{[":
            try:
                val, end = _DEC.raw_decode(text, pos)
            except Exception:  # noqa: BLE001
                val = end = None
            if end is not None:
                eol2 = text.find("\n", end)
                eol2 = n if eol2 < 0 else eol2
                if not text[end:eol2].strip(" \t\r"):
                    if buf:
                        segs.append(("T", "".join(buf)))
                        buf = []
                    segs.append(("J", val))
                    buf.append(text[end:eol2 + 1])
                    pos = eol2 + 1
                    continue
        buf.append(text[pos:eol + 1])
        pos = eol + 1
    if buf:
        segs.append(("T", "".join(buf)))
    return segs


AWS = ("$ aws ec2 describe-instances --instance-ids i-0abc123 --region eu-west-1\n"
       + pretty({"Reservations": [{"ReservationId": "r-0123", "Instances": [{"InstanceId": "i-0abc123", "InstanceType": "t3.micro",
                                                                              "State": {"Code": 16, "Name": "running"}, "Monitoring": {"State": "disabled"}}]}]}, 4)
       + "exit status 0\n")
CURL = ("* Connected to api.example.com (203.0.113.9) port 443\n> GET /v1/items/7 HTTP/2\n> Host: api.example.com\n>\n< HTTP/2 200\n"
        "< content-type: application/json\n<\n"
        + pretty({"id": 7, "name": "widget", "dims": {"w": 1.50, "h": 2.0}, "tags": ["a", "b"], "price": 1E2})
        + "* Connection #0 to host api.example.com left intact\n")
DEBUG = ("[2024-06-01 10:00:01] INFO starting worker\n[INFO] cfg follows\n" + pretty({"workers": 4, "queues": ["high", "low"], "dup": 1, "dup": 2})
         + "{'python': 'repr', 'is': 'not json'}\n{ not json }\n[section]\nkey = value\n"
         + pretty([{"id": 1, "ok": True}, {"id": 2, "ok": False}]) + "done\n")

case(
    129,
    claim="A balanced {...} or [...] block that starts at column 0 and ends its line, and that json.loads accepts, is minified in place; every other byte of the output (log lines, repr dicts, [section] headers, indented JSON) is untouched.",
    loses="whitespace between tokens inside the minified blocks",
    encode=lambda t: guarded(t, minify_blocks),
    equivalent=lambda x, e: segments(x) == segments(e),
    fixtures=[
        Fixture("aws cli between a command line and a status", AWS, True),
        Fixture("curl -v with a JSON body", CURL, True),
        Fixture("debug prints: two blocks and look-alikes", DEBUG, True),
        Fixture("logs with no JSON block", "[2024-06-01 10:00:01] INFO up\n{ not json }\n{'a': 1}\n[section]\nk = v\n[1] trailing text\n", False),
        Fixture("JSON that is already one line", "result:\n" + compact({"a": 1, "b": [1, 2]}) + "\nnext\n", False),
        Fixture("indented JSON is not at line start", "log: payload\n  {\n    \"a\": 1\n  }\ndone\n", False),
        Fixture("block followed by text on the closing line", "{\n  \"a\": 1\n} trailing\n", None),
        Fixture("block with duplicate keys, 1E5 and a string holding braces", 'x\n{\n  "a": 1,\n  "a": 2,\n  "n": 1E5,\n  "s": "}{ ]["\n}\ny\n', None),
        Fixture("CRLF output", AWS.replace("\n", "\r\n"), None),
        Fixture("block cut off by truncation", AWS[:-60], None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #130 Hoist constant key-value pairs out of JSON records

HOIST = "§hoist "


def _records(text):
    """("arr"|"nd"|"ndn", [object nodes]) or None."""
    if is_json(text):
        root = lex_parse(text)
        if root[0] == "a" and all(r[0] == "o" for r in root[1]):
            return "arr", root[1]
        return None
    lines = text.split("\n")
    mode = "nd"
    if lines and lines[-1] == "":
        lines.pop()
        mode = "ndn"
    if not lines or not all(ln.strip() and is_json(ln) and lex_parse(ln)[0] == "o" for ln in lines):
        return None
    return mode, [lex_parse(ln) for ln in lines]


def _hoist_enc(text):
    got = _records(text)
    if got is None:
        return text
    mode, recs = got
    if len(recs) < 5:
        return text
    modal: dict[str, dict[str, int]] = {}
    for _, pairs in recs:
        seen = set()
        for k, v in pairs:
            if k in seen:
                modal.setdefault(k, {})["\0dup"] = 1
            seen.add(k)
            modal.setdefault(k, {}).setdefault(lex_dump(v), 0)
            modal[k][lex_dump(v)] += 1
    hoisted: list[tuple[int, str, str]] = []
    for k, vals in modal.items():
        if "\0dup" in vals or sum(vals.values()) != len(recs):
            continue  # key missing from some record, or repeated inside one: hoisting would invent or merge data
        v, c = max(vals.items(), key=lambda kv: kv[1])
        if c < 0.9 * len(recs):
            continue
        idx = {[kk for kk, _vv in pairs].index(k) for _, pairs in recs for kk, vv in pairs if kk == k and lex_dump(vv) == v}
        if len(idx) != 1:
            continue  # position of the pair must be the same in every record that loses it
        if c * (len(k) + len(v) + 2) <= len(k) + len(v) + 12:
            continue
        hoisted.append((idx.pop(), k, v))
    if not hoisted:
        return text
    hoisted.sort()
    consts = {k: v for _p, k, v in hoisted}
    body = []
    for _, pairs in recs:
        body.append(lex_dump(("o", [(k, v) for k, v in pairs if not (k in consts and lex_dump(v) == consts[k])])))
    head = HOIST + mode + " [" + ",".join(f"[{p},{k},{v}]" for p, k, v in hoisted) + "]\n"
    return head + ("[" + ",".join(body) + "]" if mode == "arr" else "\n".join(body) + ("\n" if mode == "ndn" else ""))


def _hoist_dec(text):
    head, _, body = text.partition("\n")
    mode, _, spec = head[len(HOIST):].partition(" ")
    hoisted = []
    for ent in lex_parse(spec)[1]:
        p, k, v = ent[1]
        hoisted.append((int(p[1]), k[1], v))
    hoisted.sort(key=lambda h: h[0])

    def restore(obj_text):
        pairs = lex_parse(obj_text)[1]
        present = {k for k, _v in pairs}
        for p, k, v in hoisted:
            if k not in present:
                pairs.insert(p, (k, v))
        return lex_dump(("o", pairs))

    if mode == "arr":
        items = lex_parse(body)[1]
        return "[" + ",".join(restore(lex_dump(o)) for o in items) + "]"
    lines = body.split("\n")
    if mode == "ndn":
        lines.pop()
    return "\n".join(restore(ln) for ln in lines) + ("\n" if mode == "ndn" else "")


enc130, dec130 = make_codec(HOIST, _hoist_enc, _hoist_dec)


def _ndjson_records(text):
    """Oracle view: ("nd", per-line trees, ends with newline) or ("arr", tree) or None; independent of lex_parse."""
    try:
        return "arr", jtree(text)
    except Exception:  # noqa: BLE001
        pass
    lines = text.split("\n")
    nl = text.endswith("\n")
    if nl:
        lines.pop()
    try:
        return "nd", [jtree(ln) for ln in lines], nl
    except Exception:  # noqa: BLE001
        return None


def oracle130(x, e):
    d = dec130(e)
    return _ndjson_records(x) == _ndjson_records(d) if _ndjson_records(x) is not None else d == x


def pino_logs(n, lacking=None):
    out = []
    for i in range(n):
        rec = {"level": 30 if i % 7 else 50, "time": 1717000000000 + i * 137, "pid": 4711, "hostname": "api-7d9f8b6c5-abcde",
               "service": "checkout", "version": "1.14.2", "msg": f"handled request {i}", "reqId": f"req-{i:04d}"}
        if i == 3:
            rec["hostname"] = "api-7d9f8b6c5-zzzzz"  # exception stays inline
        if lacking is not None and i == lacking:
            del rec["version"]
        out.append(rec)
    return out


NDJSON = "\n".join(compact(r) for r in pino_logs(16)) + "\n"

case(
    130,
    claim="Keys that hold the same value in at least 90% of the records, sit at the same position and exist in every record are printed once in a header (position, key, value); records that differ keep the pair inline, and decoding re-inserts the pairs at their positions.",
    loses="whitespace between tokens (records are minified)",
    encode=enc130,
    equivalent=oracle130,
    fixtures=[
        Fixture("pino-style NDJSON with one exception", NDJSON, True),
        Fixture("array of records (gh list style)", pretty(pino_logs(14)), True),
        Fixture("NDJSON without trailing newline", NDJSON.rstrip("\n"), True),
        Fixture("every value differs", "\n".join(compact({"id": i, "msg": f"m{i}"}) for i in range(12)) + "\n", False),
        Fixture("fewer than five records", "\n".join(compact(r) for r in pino_logs(4)) + "\n", False),
        Fixture("a blank line inside NDJSON", "\n".join(compact(r) for r in pino_logs(8)).replace("\n", "\n\n", 1) + "\n", False),
        Fixture("a key missing from one record is not hoisted", "\n".join(compact(r) for r in pino_logs(16, lacking=5)) + "\n", True),
        Fixture("a key that is constant on only 60% of the records", "\n".join(compact({"id": i, "kind": "a" if i % 5 < 3 else "b", "msg": f"message number {i}"}) for i in range(20)) + "\n", False),
        Fixture("constant key at different positions", "\n".join(compact({"a": i, "k": 1} if i % 2 else {"k": 1, "a": i}) for i in range(12)) + "\n", False),
        Fixture("duplicate key inside a record", "\n".join(['{"k":1,"k":2,"a":%d}' % i for i in range(10)]) + "\n", None),
        Fixture("text that starts like the header", "§hoist nd [[0,\"a\",1]]\n{\"b\":1}\n", None),
        Fixture("constants that need escaping", "\n".join(compact({"svc": "a\"b\\c ü", "n": i, "z": 1.0}) for i in range(10)) + "\n", True),
    ],
    min_gain=0.1,
    idempotent=False,  # re-encoding encoded text starts with the sigil, so it is escaped (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #316 [{Key,Value}] pair lists as maps

KV = "§kv"
_PAIR_NAMES = {('"Key"', '"Value"'), ('"key"', '"value"'), ('"name"', '"value"'), ('"Name"', '"Value"')}


def _pairs_to_map(n):
    """Rewrite one array node if it is a clean list of two-key objects; returns the new node or n."""
    items = n[1]
    if not items or any(i[0] != "o" or len(i[1]) != 2 for i in items):
        return n
    names = {(i[1][0][0], i[1][1][0]) for i in items}
    if len(names) != 1 or next(iter(names)) not in _PAIR_NAMES:
        return n
    kn, vn = next(iter(names))
    keys = []
    for i in items:
        k = i[1][0][1]
        if k[0] != "l" or k[1][0] != '"':
            return n
        keys.append(json.loads(k[1]))
    if len(set(keys)) != len(keys) or KV in keys:
        return n  # duplicate keys cannot live in a map; the marker name must stay unique
    out = ("o", [('"' + KV + '"', ("l", json.dumps(json.loads(kn) + "," + json.loads(vn)))), *[(i[1][0][1][1], i[1][1][1]) for i in items]])
    return out if len(lex_dump(out)) < len(lex_dump(n)) else n


def _rewrite316(n):
    if n[0] == "l":
        return n
    if n[0] == "a":
        return _pairs_to_map(("a", [_rewrite316(c) for c in n[1]]))
    return ("o", [(k, _rewrite316(v)) for k, v in n[1]])


def _kv_enc(text):
    if not is_json(text):
        return text
    root = lex_parse(text)
    new = _rewrite316(root)
    return lex_dump(new) if lex_dump(new) != lex_dump(root) else text


def _unrewrite316(n):
    if n[0] == "l":
        return n
    if n[0] == "a":
        return ("a", [_unrewrite316(c) for c in n[1]])
    if n[1] and n[1][0][0] == '"' + KV + '"':
        kn, vn = json.loads(n[1][0][1][1]).split(",")
        return ("a", [("o", [(json.dumps(kn), ("l", k)), (json.dumps(vn), _unrewrite316(v))]) for k, v in n[1][1:]])
    return ("o", [(k, _unrewrite316(v)) for k, v in n[1]])


def enc316(text):
    if text.startswith(SIG) or KV in text:
        return RAW + text  # the marker is already in the input: escape it so a decoder cannot mistake it for ours
    return guarded(text, _kv_enc)


def dec316(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    return lex_dump(_unrewrite316(lex_parse(text))) if is_json(text) else text


def ec2_describe(n):
    out = []
    for i in range(n):
        out.append({"InstanceId": f"i-0abc{i:03d}", "InstanceType": "t3.micro", "State": {"Name": "running"},
                    "Tags": [{"Key": "Name", "Value": f"web-{i}"}, {"Key": "env", "Value": "prod"}, {"Key": "team", "Value": "payments"},
                             {"Key": "aws:cloudformation:stack-name", "Value": "shop-prod"}]})
    return {"Reservations": [{"Instances": out}]}


def k8s_pod():
    env_a = [{"name": "LOG_LEVEL", "value": "info"}, {"name": "PORT", "value": "8080"}, {"name": "DB_HOST", "value": "db.internal"}]
    env_b = [{"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "t", "key": "k"}}}, {"name": "PORT", "value": "9090"}]
    return {"kind": "Pod", "metadata": {"name": "api-0", "labels": {"app": "api"}},
            "spec": {"containers": [{"name": "api", "env": env_a}, {"name": "sidecar", "env": env_b}]}}


case(
    316,
    claim="A list of {Key,Value} (or key/value, name/value) objects with unique string keys becomes an object whose first member {\"§kv\":\"Key,Value\"} names the original fields; decoding rebuilds the identical list, and lists with duplicate keys, extra fields, mixed shapes or non-string keys are left as lists.",
    loses="whitespace between tokens (output is minified)",
    encode=enc316,
    equivalent=json_codec_oracle(dec316),
    fixtures=[
        Fixture("aws ec2 describe-instances (pretty)", pretty(ec2_describe(5), 4), True),
        Fixture("kubectl get pod -o json, env lists", pretty(k8s_pod()), True),
        Fixture("aws, minified", compact(ec2_describe(5)), True),
        Fixture("duplicate Key in one tag list", compact({"Tags": [{"Key": "a", "Value": "1"}, {"Key": "a", "Value": "2"}]}), False),
        Fixture("two-key objects with other names", compact({"rows": [{"id": 1, "label": "x"}, {"id": 2, "label": "y"}]}), False),
        Fixture("env list mixing value and valueFrom", compact({"env": [{"name": "A", "value": "1"}, {"name": "B", "valueFrom": {"x": 1}}]}), False),
        Fixture("tiny list does not pay for the marker", compact({"Tags": [{"Key": "a", "Value": "b"}]}), False),
        Fixture("value types, escapes, empty and nested lists", compact({"Tags": [{"Key": "n", "Value": 1.0}, {"Key": "q\"\\é", "Value": None},
                                                                                {"Key": "o", "Value": {"Tags": [{"Key": "in", "Value": "v"}, {"Key": "in2", "Value": "w"}]}},
                                                                                {"Key": "e", "Value": []}], "Empty": []}), None),
        Fixture("entries with different field order", compact({"Tags": [{"Key": "a", "Value": "1"}, {"Value": "2", "Key": "b"}, {"Key": "c", "Value": "3"}]}), None),
        Fixture("a real object whose first key is the marker", compact({"x": {"§kv": "Key,Value", "a": "b"}}), None),
        Fixture("non-string key and key equal to the marker", compact({"A": [{"Key": 1, "Value": "x"}, {"Key": 2, "Value": "y"}], "B": [{"Key": "§kv", "Value": "x"}, {"Key": "z", "Value": "y"}]}), None),
        Fixture("keys spelled differently but equal", compact({"T": [{"Key": "a", "Value": 1}, {"Key": "\\u0061", "Value": 2}]}).replace("\\\\u", "\\u"), None),
    ],
    min_gain=0.15,
    idempotent=False,  # encoded output contains the marker, so re-encoding escapes it (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #145 MCP JSON entity cache across calls (a stateful policy, so a simulation)


def _ptr_escape(key):
    return key.replace("~", "~0").replace("/", "~1")


class EntityCache:
    """Per-session cache for MCP JSON outputs. State lives in a file because each hook call is a new process.

    The first time an object (>= MIN bytes minified) is seen from a server it stays in full and its location is remembered.
    Later outputs replace an identical object (byte for byte, per server) with {"$see": "<call id>:<json pointer>"}. Every
    output the cache touched starts with "§call <epoch>.<n>" so a reader can find it. A ref is only used while the target,
    and every ref the target itself contains, is still inside the reader's window of visible outputs. Lost state starts a
    new epoch, so a call id is never reused."""

    MIN = 70

    def __init__(self, path, window=8, epochs=None):
        self.path, self.window = path, window
        self.epochs = epochs or (lambda: os.urandom(2).hex())

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
            assert isinstance(st["n"], int) and isinstance(st["seen"], dict) and isinstance(st["epoch"], str)
            return st
        except Exception:  # noqa: BLE001  missing or corrupt state: start a new epoch
            return {"epoch": self.epochs(), "n": 0, "seen": {}}

    def transform(self, server, text):
        try:
            return self._transform(server, text)
        except Exception:  # noqa: BLE001  fail open: the model gets the original
            return text

    def _transform(self, server, text):
        if "$see" in text or not is_json(text):
            return text  # not JSON, or it already uses our vocabulary: leave it alone and remember nothing
        root = lex_parse(text)
        if self._has_dup_keys(root):
            return text  # pointers by key would be ambiguous
        st = self._load()
        n = st["n"] + 1
        seen = st["seen"]
        floor = n - self.window  # entries that depend on calls <= floor are outside the reader's window
        touched = [False]

        def walk(node, ptr, top):
            """returns (new node, oldest call number this subtree depends on)"""
            if node[0] == "l":
                return node, n
            if node[0] == "a":
                kids = [walk(c, f"{ptr}/{i}", False) for i, c in enumerate(node[1])]
                return ("a", [k for k, _o in kids]), min([n] + [o for _k, o in kids])
            key = None
            if not top:
                d = lex_dump(node)
                if len(d) >= self.MIN:
                    key = server + "|" + hashlib.sha1(d.encode()).hexdigest()[:16]
                    ent = seen.get(key)
                    if ent and ent["oldest"] > floor:
                        touched[0] = True
                        return ("l", '{"$see":"%s.%d:%s"}' % (st["epoch"], ent["call"], ent["ptr"])), ent["oldest"]
            kids = [(k, *walk(v, ptr + "/" + _ptr_escape(json.loads(k)), False)) for k, v in node[1]]
            oldest = min([n] + [o for _k, _v, o in kids])
            if key is not None:
                seen[key] = {"call": n, "ptr": ptr, "oldest": oldest}
                touched[0] = True
            return ("o", [(k, v) for k, v, _o in kids]), oldest

        new, _ = walk(root, "", True)
        if not touched[0]:
            return text
        st["n"], st["seen"] = n, seen
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except OSError:
            return text  # cannot persist: the numbering would drift, so do not annotate
        return f"§call {st['epoch']}.{n}\n" + lex_dump(new)

    @staticmethod
    def _has_dup_keys(n):
        if n[0] == "l":
            return False
        if n[0] == "a":
            return any(EntityCache._has_dup_keys(c) for c in n[1])
        keys = [json.loads(k) for k, _v in n[1]]
        return len(set(keys)) != len(keys) or any(EntityCache._has_dup_keys(v) for _k, v in n[1])


class Reader:
    """What the model can see: the last `window` annotated outputs, and the ability to follow {"$see": ...} into them."""

    def __init__(self, window):
        self.window, self.calls, self.latest = window, {}, None

    def read(self, text):
        if not text.startswith("§call "):
            return jtree(text)
        head, _, body = text.partition("\n")
        cid = head[len("§call "):]
        epoch, _, n = cid.partition(".")
        self.latest = (epoch, int(n))
        tree = jtree(body)
        self.calls[cid] = tree
        return self._resolve(tree)

    def _resolve(self, v):
        if isinstance(v, tuple) and v[0] == "O":
            if len(v[1]) == 1 and v[1][0][0] == "$see":
                cid, _, ptr = v[1][0][1].partition(":")
                epoch, _, n = cid.partition(".")
                if cid not in self.calls or (epoch == self.latest[0] and int(n) <= self.latest[1] - self.window):
                    raise AssertionError(f"dangling ref to call {cid} (latest {self.latest}, window {self.window})")
                return self._resolve(self._follow(self.calls[cid], ptr))
            return ("O", [(k, self._resolve(x)) for k, x in v[1]])
        if isinstance(v, list):
            return [self._resolve(x) for x in v]
        return v

    @staticmethod
    def _follow(v, ptr):
        for part in ptr.split("/")[1:] if ptr else []:
            part = part.replace("~1", "/").replace("~0", "~")
            v = v[int(part)] if isinstance(v, list) else dict(v[1])[part]
        return v


def sim_entity_cache():
    counter = iter(range(1, 100))
    epochs = lambda: f"e{next(counter)}"  # noqa: E731  deterministic epoch ids
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "gh.json")
        cache, reader = EntityCache(state, window=8, epochs=epochs), Reader(window=8)
        tally = {"orig": 0, "sent": 0}

        def call(server, payload, cache=cache, reader=reader, expect_ref=None):
            text = payload if isinstance(payload, str) else pretty(payload, 1)
            out = cache.transform(server, text)
            tally["orig"] += len(text)
            tally["sent"] += len(out)
            assert reader.read(out) == jtree(text), "reader view differs from the original output"  # (b) nothing is lost
            if expect_ref is not None:
                assert ("$see" in out) == expect_ref, f"expected refs={expect_ref}: {out[:160]!r}"
            return out

        # (a) the saving: a list page, an overlapping second page, then a get of one issue --------------------
        page1 = {"items": [gh_issue(i) for i in range(8)]}
        page2 = {"items": [gh_issue(i) for i in range(4, 12)]}  # same users, labels and milestone, some same issues
        o1 = call("github", page1)
        o2 = call("github", page2, expect_ref=True)
        assert len(o2) < 0.5 * len(pretty(page2, 1)), "page two should be mostly refs into page one"
        detail = dict(gh_issue(2), body="Steps to reproduce ...", thread=[{"user": gh_user(6), "body": "me too"}])
        o3 = call("github", detail, expect_ref=True)
        base = sum(len(pretty(p, 1)) for p in (page1, page2, detail))
        sent = sum(len(o) for o in (o1, o2, o3))
        assert sent < 0.5 * base, f"three-call session should halve the bytes (sent {sent} of {base})"

        # (c) it must NOT apply -------------------------------------------------------------------------------
        changed = dict(gh_issue(2), milestone=dict(MILESTONE, open_issues=8))  # one changed field makes it a new object
        o4 = call("github", changed)
        assert '"open_issues":8' in o4 and '"open_issues":7' not in o4, "a changed milestone is sent in full, not as a ref to the old one"
        o5 = call("jira", {"items": [gh_issue(0)]}, expect_ref=False)  # same bytes, other server: never a ref into github's output
        assert o5.startswith("§call ")
        small = pretty({"items": [{"id": 1}, {"id": 1}, {"id": 1}]}, 1)  # below the size floor
        assert cache.transform("github", small) == small
        for odd in ("plain text, not json", '{"error": "rate limited"', '{"$see": "x", "pad": "' + "x" * 100 + '"}',
                    '{"a": ' + compact(gh_user(1)) + ', "a": ' + compact(gh_user(1)) + "}"):  # truncated, own vocabulary, duplicate keys
            assert cache.transform("github", odd) == odd, odd[:40]

        # the window: refs only while the target and everything it depends on is still visible --------------
        c2 = EntityCache(os.path.join(d, "w.json"), window=3, epochs=epochs)
        r2 = Reader(window=3)
        user, issue = gh_user(1), {"title": "x" * 20, "user": gh_user(1), "note": "n" * 40}
        outs = [c2.transform("s", compact({"u": user, "pad": "a" * 30})),  # call 1: user in full
                c2.transform("s", compact({"i": issue})),  # call 2: issue in full, its user is a ref to call 1
                c2.transform("s", compact({"filler": {"k": "v" * 90}})),  # call 3
                c2.transform("s", compact({"i": issue}))]  # call 4: window covers calls 2..4, but call 2 depends on call 1
        assert "$see" in outs[1] and "$see" not in outs[3], "a ref whose target itself points outside the window must not be used"
        for o in outs:
            r2.read(o)  # every output resolves with only the last three visible
        tight = Reader(window=1)  # the reader view is not vacuous: losing call 1 makes a stale ref fail
        tight.read(o1)
        try:
            tight.read(o3)
        except AssertionError:
            pass
        else:
            raise AssertionError("a reader that has lost call 1 must not be able to resolve refs into it")

        # (d) failure handling: fail open ----------------------------------------------------------------------
        with open(state, "w") as f:
            f.write("{corrupt")
        o6 = call("github", page1)  # corrupt state: a new epoch; refs may point only inside this output, never at the old epoch
        assert o6.split("\n")[0] != o1.split("\n")[0] and set(re.findall(r'"\$see":"([^.]+)\.', o6)) <= {o6.split()[1].split(".")[0]}, "a new epoch must not reuse old call ids"
        locked = EntityCache(os.path.join(d, "no", "such", "dir", "s.json"), epochs=epochs)  # cannot persist state
        txt = pretty(page1, 1)
        assert locked.transform("github", txt) == txt, "no state, no annotation"
        os.remove(state)  # session reset or context compaction: nothing may refer to the past
        o7 = call("github", page1)
        assert o7.split("\n")[0] not in (o1.split("\n")[0], o6.split("\n")[0])
        assert set(re.findall(r'"\$see":"([^.]+)\.', o7)) <= {o7.split()[1].split(".")[0]}
        o8 = call("github", page1, expect_ref=True)
        assert len(o8) < 0.15 * len(o7), "an identical repeat collapses to refs"
    return {"bytes_original": tally["orig"], "bytes_sent": tally["sent"], "saved_pct_whole_session": round(100 * (1 - tally["sent"] / tally["orig"]), 1),
            "saved_pct_first_three_calls": round(100 * (1 - sent / base), 1)}


case(
    145,
    claim="Within one session and server, a nested JSON object seen in an earlier output is replaced by {\"$see\":\"<call id>:<pointer>\"} while it and everything it points to are inside the reader's window; a reader that follows the refs recovers every output exactly, and changed objects, other servers, tiny objects, non-JSON, corrupt or unwritable state all leave the output in full.",
    sim=sim_entity_cache,
)

# ---------------------------------------------------------------------------------------------------------------
# #36 Strip comments and blank lines in YAML / TOML / INI

_FAIL = object()
_BLOCK = re.compile(r"[|>](?:[+-]?\d?|\d[+-])\Z")
_DQ = re.compile(r'"(?:[^"\\]|\\.)*"')
_SQ = re.compile(r"'(?:[^']|'')*'")
_YKEY = re.compile(r"([A-Za-z0-9_.-]+):(?: +(.*))?\Z")


def _indent(ln):
    return len(ln) - len(ln.lstrip(" "))


class YamlSubsetError(ValueError):
    pass


def _y_value(rest):
    """Scalar text of a value part with its comment removed; raises outside the supported subset."""
    s = rest.strip()
    if s[:1] in ('"', "'"):
        m = (_DQ if s[0] == '"' else _SQ).match(s)
        if not m:
            raise YamlSubsetError("unterminated or multi-line quoted scalar")
        tail = s[m.end():]
        if tail.strip() and not (tail[0] == " " and tail.strip().startswith("#")):
            raise YamlSubsetError("text after a quoted scalar")
        return s[: m.end()]
    m = re.search(r"(?:^|\s)#", s)
    return (s[: m.start()] if m else s).rstrip()


def yaml_load(text):
    """Loader for the YAML subset the fixtures use: block maps and sequences, plain/quoted scalars, | and > block scalars,
    comments. Anything else raises, so unsupported documents are never claimed safe. Scalars keep their quoting style."""
    if "\t" in text or "\r" in text:
        raise YamlSubsetError("tabs or CR")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    at = [0]

    def sig(i):
        while i < len(lines):
            st = lines[i].strip()
            if st and not st.startswith("#"):
                return i
            i += 1
        return None

    def scalar(rest):
        v = _y_value(rest)
        if v == "":
            return None
        return ("dq" if v[0] == '"' else "sq" if v[0] == "'" else "p", v)

    def block_scalar(header, parent):
        body, i = [], at[0]
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > parent):
            body.append(lines[i])
            i += 1
        at[0] = i
        nonblank = [ln for ln in body if ln.strip()]
        bi = _indent(nonblank[0]) if nonblank else 0
        out = []
        for ln in body:
            if ln.strip():
                if _indent(ln) < bi:
                    raise YamlSubsetError("under-indented block scalar line")
                out.append(ln[bi:])
            elif ln != "":
                raise YamlSubsetError("whitespace-only line inside a block scalar")
            else:
                out.append("")
        if "+" not in header:
            while out and out[-1] == "":
                out.pop()
        return ("B", header, tuple(out))

    def parse_block(ind):
        i = sig(at[0])
        if i is None:
            return None
        st = lines[i][ind:]
        return parse_seq(ind) if st.startswith("- ") or st == "-" else parse_map(ind)

    def nested(ind):
        j = sig(at[0])
        if j is not None and (_indent(lines[j]) > ind or (_indent(lines[j]) == ind and lines[j][ind:].startswith("- "))):
            return parse_block(_indent(lines[j]))
        return None

    def parse_map(ind):
        d = {}
        while True:
            i = sig(at[0])
            if i is None or _indent(lines[i]) < ind:
                return d
            if _indent(lines[i]) > ind:
                raise YamlSubsetError("unexpected indent")
            body = lines[i][ind:]
            if body.startswith("- ") or body == "-":
                return d
            m = _YKEY.match(body)
            if not m or m.group(1) in d:
                raise YamlSubsetError(f"not a key line: {body!r}")
            key, rest = m.group(1), _y_value(m.group(2) or "")
            at[0] = i + 1
            if rest == "":
                d[key] = nested(ind)
            elif _BLOCK.match(rest):
                d[key] = block_scalar(rest, ind)
            else:
                d[key] = scalar(rest)

    def parse_seq(ind):
        out = []
        while True:
            i = sig(at[0])
            if i is None or _indent(lines[i]) != ind or not (lines[i][ind:].startswith("- ") or lines[i][ind:] == "-"):
                if i is not None and _indent(lines[i]) > ind:
                    raise YamlSubsetError("unexpected indent")
                return out
            body = lines[i][ind + 1:].lstrip(" ")
            if body == "":
                at[0] = i + 1
                out.append(nested(ind))
            elif _YKEY.match(body):
                lines[i] = " " * (ind + 2) + body
                out.append(parse_map(ind + 2))
            else:
                at[0] = i + 1
                out.append(scalar(body))

    i = sig(0)
    if i is None:
        return None
    v = parse_block(_indent(lines[i]))
    if sig(at[0]) is not None:
        raise YamlSubsetError("trailing content")
    return v


def _toml_p(t):
    try:
        return repr(tomllib.loads(t))
    except Exception:  # noqa: BLE001
        return _FAIL


def _ini_p(t):
    try:
        cp = configparser.ConfigParser(interpolation=None, strict=False)  # default: '#' and ';' full-line comments only
        cp.optionxform = str
        cp.read_string(t)
        return repr(({s: dict(cp.items(s)) for s in cp.sections()}, dict(cp.defaults())))
    except Exception:  # noqa: BLE001
        return _FAIL


def _yaml_p(t):
    try:
        return repr(yaml_load(t))
    except Exception:  # noqa: BLE001
        return _FAIL


def _toml_scan(line, ml):
    """(index of a comment start or -1, multi-line string state at end of line) for one TOML line."""
    i, n, state = 0, len(line), ml
    while i < n:
        c = line[i]
        if state == '"""':
            if c == "\\":
                i += 2
            elif line.startswith('"""', i):
                while i < n and line[i] == '"':
                    i += 1
                state = None
            else:
                i += 1
        elif state == "'''":
            if line.startswith("'''", i):
                while i < n and line[i] == "'":
                    i += 1
                state = None
            else:
                i += 1
        elif c == "#":
            return i, state
        elif line.startswith('"""', i):
            state, i = '"""', i + 3
        elif line.startswith("'''", i):
            state, i = "'''", i + 3
        elif c == '"':
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
            i += 1
        elif c == "'":
            j = line.find("'", i + 1)
            i = n if j < 0 else j + 1
        else:
            i += 1
    return -1, state


def _y_inline(line):
    """(line without its trailing comment, block-scalar header or None) for one YAML line."""
    m = re.match(r"^(\s*(?:- +)*[A-Za-z0-9_.-]+:)((?: +.*)?)$", line) or re.match(r"^(\s*- +)(.*)$", line)
    if not m:
        return line, None
    head, value = m.group(1), m.group(2)
    s = value.lstrip(" ")
    lead = value[: len(value) - len(s)]
    if s[:1] in ('"', "'"):
        q = (_DQ if s[0] == '"' else _SQ).match(s)
        tail = q.end() if q else None
        if q and s[tail:].startswith(" ") and s[tail:].lstrip(" ").startswith("#"):
            value = lead + s[:tail]
    else:
        c = re.search(r"(?:^|\s)#", s)
        if c:
            value = (lead + s[: c.start()]).rstrip()
    v = value.strip()
    return head + value, (v if _BLOCK.match(v) else None)


def strip_config(text):
    if "\r" in text:
        return text
    toml, ini = _toml_p(text) is not _FAIL, _ini_p(text) is not _FAIL
    mode = "toml-ini" if toml and ini else "toml" if toml else "ini" if ini else "yaml" if _yaml_p(text) is not _FAIL else None
    if mode is None:
        return text
    had_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_nl:
        lines.pop()
    out: list[tuple[str, str, int]] = []  # (L line | B blank candidate | V verbatim, text, index of the original line)
    if mode in ("toml", "toml-ini"):
        ml = None
        for n, ln in enumerate(lines):
            start, (idx, ml) = ml, _toml_scan(ln, ml)
            if start is not None:  # inside a multi-line string: only a comment after the closing quotes may go
                out.append(("V", ln[:idx].rstrip() if idx >= 0 and mode == "toml" else ln, n))
            elif idx >= 0 and not ln[:idx].strip():
                continue  # full-line comment
            else:
                ln = ln[:idx].rstrip() if idx >= 0 and mode == "toml" else ln
                out.append(("B" if not ln.strip() else "L", ln, n))
    elif mode == "ini":
        for n, ln in enumerate(lines):
            if ln.strip().startswith(("#", ";")):
                continue
            out.append(("B" if not ln.strip() else "L", ln, n))
    else:
        parent, keep_tail, pending = None, False, []
        for n, ln in enumerate(lines):
            if parent is not None:
                if not ln.strip():
                    pending.append((ln, n))
                    continue
                if _indent(ln) > parent:
                    out.extend(("V", p, k) for p, k in pending)
                    out.append(("V", ln, n))
                    pending = []
                    continue
                out.extend(("V" if keep_tail else "B", p, k) for p, k in pending)  # trailing blanks after the block
                pending, parent = [], None
            if not ln.strip():
                out.append(("B", ln, n))
            elif ln.strip().startswith("#"):
                continue
            else:
                new, hdr = _y_inline(ln)
                out.append(("L", new, n))
                if hdr:
                    parent, keep_tail = _indent(ln), "+" in hdr
        out.extend(("V" if keep_tail and parent is not None else "B", p, k) for p, k in pending)
    res, i = [], 0
    while i < len(out):
        if out[i][0] != "B":
            res.append(out[i])
            i += 1
            continue
        j = i
        while j < len(out) and out[j][0] == "B":
            j += 1
        if j < len(out) and _indent(out[j][1]) > 0:  # next line is indented: it may continue a multi-line value
            res.extend(out[i:j])
        i = j
    # a final line without newline stays without one only if that very line survived; if it was dropped, the line
    # before it was newline-terminated in the original (this matters for |+ blocks, which count trailing newlines)
    end_nl = had_nl or (bool(res) and res[-1][2] != len(lines) - 1)
    return "\n".join(t for _k, t, _n in res) + ("\n" if end_nl and res else "")


def oracle36(x, e):
    """Every parser that accepts x (tomllib, configparser, the YAML-subset loader) must accept e and read the same thing."""
    if e == x:
        return True
    accepted = False
    for parse in (_toml_p, _ini_p, _yaml_p):
        a = parse(x)
        if a is _FAIL:
            continue
        accepted = True
        if parse(e) != a:
            return False
    return accepted


TOML_DOC = '''# Project configuration
# generated by hand, do not edit

[package]
name = "widgets"          # the published name
version = "2.1.0"
homepage = "https://example.com/widgets#readme"   # a fragment is part of the URL
tagline = "C# is not a comment # really"
pattern = 'a#b'           # literal string with a hash

description = """
Line one # inside a multi-line string
# this looks like a comment but is text

after a blank line
"""    # trailing comment after the closing quotes

keywords = [
    "a",      # first
    # a full-line comment inside the array
    "b",
]


[dependencies]
requests = ">=2.0"   # pinned loosely
click = "^8.1"

# optional extras
[extras]
yaml = { version = "6", optional = true }   # only for config loading
'''

INI_DOC = """; global settings
# also a comment style

[metadata]
name = widgets
url = https://example.com/page#section   ; the INI parser keeps this as part of the value
long_description =
    First line of the description

    third line after a blank line
    # a comment line inside a value is skipped by the parser

[options]
install_requires =
    requests
    # dev only
    click

; trailing comment
"""

AMBIGUOUS = """# build settings
[build]
target = "x86_64"   # TOML: a comment. INI: part of the value, so it must stay
opt = 3

# tests
[test]
retries = 2
"""

YAML_DOC = """# docker-compose for the demo stack
version: "3.8"      # compose file format

services:
  web:
    image: nginx:1.25   # pinned
    ports:
      - "8080:80"       # host:container
      - '9090:90'
    environment:
      - LOG_LEVEL=debug # a comment
      - COLOR="#ff0000"
      - HOME_URL=http://example.com/#top
    healthcheck:
      test: curl -f http://localhost:80/#health || exit 1
    command: |
      #!/bin/sh
      # start the server
      echo "starting"

      exec nginx -g 'daemon off;'
    labels:
      note: "a # b"
      other: 'it''s # fine'   # trailing

  db:
    image: postgres:16

# trailing section
volumes:
  data:
"""

case(
    36,
    claim="Full-line comments, trailing comments (TOML and YAML only) and blank lines are removed from TOML, INI and YAML (block maps and sequences, quoted and plain scalars, | and > block scalars; other YAML is left alone); a # inside a quoted string, a URL fragment, a multi-line string or a block scalar is never touched, and every parser that reads the original (tomllib, configparser, a YAML-subset loader) reads the result identically.",
    loses="comments, blank lines (before a line that starts at column 0) and spaces before a trailing comment; INI trailing comments are not comments to configparser, so they stay",
    encode=lambda t: guarded(t, strip_config),
    equivalent=oracle36,
    fixtures=[
        Fixture("TOML with strings, arrays and multi-line strings", TOML_DOC, True),
        Fixture("INI with ; and # comments and a multi-line value", INI_DOC, True),
        Fixture("valid as both TOML and INI: inline comments stay", AMBIGUOUS, True),
        Fixture("YAML compose file with a block scalar", YAML_DOC, True),
        Fixture("YAML without comments or blank lines", "a: 1\nb:\n  - x\n  - y\nc: \"#not a comment\"\n", False),
        Fixture("TOML without comments or blank lines", 'a = 1\n[t]\nb = "x # y"\n', False),
        Fixture("Python source", "# header\nimport os\n\n\ndef f(a):\n    # note\n    return a  # trailing\n", False),
        Fixture("Markdown with # headings", "# Title\n\nSome text.\n\n## Section\n\n- item\n", False),
        Fixture(".env file", "# secrets\nAPI_KEY=abc123   # not a comment in dotenv\n\nDEBUG=1\n", False),
        Fixture("shell script", "#!/bin/sh\n# run\n\necho hi # say hi\n", False),
        Fixture("YAML outside the supported subset (multi-line plain scalar)", "description: this is a long\n  text with # inside\n\n# real comment\nb: 1\n", False),
        Fixture("YAML with CRLF", YAML_DOC.replace("\n", "\r\n"), False),
        Fixture("keep-chomped block scalar and folded scalar", "a: |+\n  text # not a comment\n\n\n# comment\nb: >\n  folded # text\n\n  more\n\nc: 1  # x\n", None),
        Fixture("TOML ml strings, 4 closing quotes, literal ml", 'a = """x # y\n\nz""""  # c\nb = \'\'\'\n# lit\n\n\'\'\'\n\n# end\n', None),
        Fixture("INI with DEFAULT, indented comment, # inside a value", "[DEFAULT]\nk = v # kept\n\n  # indented comment\n[s]\nx = a;b\n\n\ny = 1\n", None),
        Fixture("comment-only file", "# only a comment\n\n; and another\n", None),
        Fixture("blank line before indented content stays", "a:\n\n  b: 1\n\n\nc: 2\n", None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #37 Fixed-width table padding to single separators (exact, via a geometry header)

TBL = "§tbl "


def _table_enc(text):
    if "\t" in text or "\r" in text:
        return text
    had_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_nl:
        lines.pop()
    if len(lines) < 3 or any(not ln or ln != ln.rstrip(" ") for ln in lines):
        return text
    width = max(len(ln) for ln in lines)
    gut = [all(len(ln) <= p or ln[p] == " " for ln in lines) for p in range(width)]
    regions, seps, p = [], [], 0
    while p < width:
        q = p
        while q < width and gut[q] == gut[p]:
            q += 1
        (seps if gut[p] else regions).append((p, q))
        p = q
    s0 = seps[0][1] if seps and seps[0][0] == 0 else 0
    if s0:
        seps = seps[1:]
    if len(regions) < 2 or len(seps) != len(regions) - 1:
        return text
    spec, cols = [str(s0)], []
    for j, (a, b) in enumerate(regions):
        cells = [ln[a:b] for ln in lines]
        w = b - a
        if all(c == c.strip(" ").ljust(w) or (c == c.strip(" ") and len(c) < w) for c in cells):
            al = "L"
        elif all(c == "" or c == c.strip(" ").rjust(w) for c in cells):
            al = "R"
        else:
            return text
        cols.append([c.strip(" ") for c in cells])
        spec.append(f"{w}{al}")
        if j < len(seps):
            spec.append(str(seps[j][1] - seps[j][0]))
    rows = []
    for r in range(len(lines)):
        fields = [col[r] for col in cols]
        while fields and fields[-1] == "":
            fields.pop()
        rows.append("\t".join(fields))
    out = TBL + " ".join(spec) + "\n" + "\n".join(rows) + ("\n" if had_nl else "")
    return out if _table_dec(out) == text else text  # exactness guard: anything the geometry cannot express stays as it was


def _table_dec(text):
    head, _, body = text.partition("\n")
    items = head[len(TBL):].split(" ")
    s0 = int(items[0])
    regs = [(int(t[:-1]), t[-1]) for t in items[1::2]]
    seps = [int(t) for t in items[2::2]]
    had_nl = body.endswith("\n")
    out = []
    for row in (body[:-1] if had_nl else body).split("\n"):
        fields = row.split("\t")
        parts = [" " * s0]
        for j, f in enumerate(fields):
            w, al = regs[j]
            last = j == len(fields) - 1
            parts.append(f.rjust(w) if al == "R" else f if last else f.ljust(w))
            if not last:
                parts.append(" " * seps[j])
        out.append("".join(parts))
    return "\n".join(out) + ("\n" if had_nl else "")


enc37, dec37 = make_codec(TBL, _table_enc, _table_dec)

def tabular(rows, gap=3):
    """kubectl / docker style: left-aligned columns, width = widest cell + gap, no trailing padding."""
    n = max(map(len, rows))
    w = [max(len(r[i]) if i < len(r) else 0 for r in rows) + gap for i in range(n)]
    return "".join("".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip(" ") + "\n" for r in rows)


_PODS = [("api-7d9f8b6c5-abcde", "1/1", "Running", "0", "3d4h"), ("api-7d9f8b6c5-fghij", "1/1", "Running", "0", "3d4h"),
         ("worker-5c6d7f8b9-xyz12", "0/1", "CrashLoopBackOff", "12 (3m ago)", "2h17m"), ("cron-nightly-28591440-qrstu", "0/1", "Completed", "0", "9h"),
         ("ingress-nginx-controller-6f8d9-k2", "1/1", "Running", "2 (1d ago)", "41d"), ("redis-0", "1/1", "Running", "0", "41d"),
         ("redis-1", "1/1", "Running", "0", "41d"), ("migrate-20240601-zk9fp", "0/1", "Error", "0", "5d"),
         ("web-6b7c8d9e5-mnop1", "1/1", "Running", "1 (2d ago)", "12d"), ("web-6b7c8d9e5-qrst2", "1/1", "Running", "0", "12d")]
KUBECTL = tabular([("NAME", "READY", "STATUS", "RESTARTS", "AGE"), *_PODS])
_CONT = [("a1b2c3d4e5f6", "nginx:1.25", '"/docker-entrypoint.…"', "2 hours ago", "Up 2 hours", "0.0.0.0:80->80/tcp", "web"),
         ("b2c3d4e5f6a1", "postgres:16", '"docker-entrypoint.s…"', "2 hours ago", "Up 2 hours", "5432/tcp", "db"),
         ("c3d4e5f6a1b2", "redis:7-alpine", '"docker-entrypoint.s…"', "3 days ago", "Exited (0) 3 days ago", "", "cache"),
         ("d4e5f6a1b2c3", "ghcr.io/acme/api:v2.1", '"/app/server --port …"', "About a minute ago", "Up About a minute (healthy)", "0.0.0.0:8080->8080/tcp", "api"),
         ("e5f6a1b2c3d4", "busybox", '"sleep 3600"', "5 minutes ago", "Exited (137) 2 minutes ago", "", "tmp")]
DOCKER_PS = tabular([("CONTAINER ID", "IMAGE", "COMMAND", "CREATED", "STATUS", "PORTS", "NAMES"), *_CONT])
_PS = [("root", 1, 0.0, 0.1, 168936, 11824, "?", "Ss", "Oct28", "0:03", "/sbin/init splash"), ("root", 2, 0.0, 0.0, 0, 0, "?", "S", "Oct28", "0:00", "[kthreadd]"),
       ("www-data", 1042, 1.2, 2.4, 812340, 98432, "?", "Sl", "09:12", "0:41", "nginx: worker process"),
       ("postgres", 2210, 10.5, 11.3, 2203112, 454012, "?", "Ss", "09:13", "3:02", "postgres: main checkpointer"),
       ("alice", 4711, 0.0, 0.1, 15432, 5120, "pts/0", "R+", "10:01", "0:00", "ps aux"), ("alice", 4690, 0.3, 0.2, 22100, 8192, "pts/0", "Ss", "10:00", "0:00", "-bash")]


def ps_aux(rows, vsz_w=7, rss_w=6):
    head = f"{'USER':<8} {'PID':>5} {'%CPU':>4} {'%MEM':>4} {'VSZ':>{vsz_w}} {'RSS':>{rss_w}} {'TTY':<8} {'STAT':<4} {'START':<5} {'TIME':>4} COMMAND"
    return "\n".join([head] + [f"{u:<8} {p:>5} {c:>4} {m:>4} {v:>{vsz_w}} {r:>{rss_w}} {t:<8} {st:<4} {s:<5} {tm:>4} {cmd}"
                              for u, p, c, m, v, r, t, st, s, tm, cmd in rows]) + "\n"


PS_AUX = ps_aux(_PS)
PS_AUX_OVERFLOW = ps_aux(_PS, vsz_w=6, rss_w=5)  # ps does not widen a column: big values push the rest of the row sideways
_FS = [("/dev/sda1", "50G", "32G", "16G", "67%", "/"), ("tmpfs", "3.9G", "0", "3.9G", "0%", "/dev/shm"), ("/dev/sdb1", "916G", "312G", "558G", "36%", "/mnt/data"),
       ("overlay", "50G", "32G", "16G", "67%", "/var/lib/docker/overlay2/abc123/merged"), ("/dev/sdc1", "2.0T", "1.1T", "0.9T", "55%", "/srv/backups"),
       ("tmpfs", "780M", "1.2M", "779M", "1%", "/run/user/1000")]
DF_H = "".join(f"{a:<14} {b:>5} {c:>5} {d:>5} {e:>4} {f}\n" for a, b, c, d, e, f in [("Filesystem", "Size", "Used", "Avail", "Use%", "Mounted on"), *_FS])
NOTES = tabular([("NAME", "STATUS", "NOTE")] + [(f"job-{i:02d}", st, nt) for i, (st, nt) in enumerate(
    [("ok", ""), ("failed", "disk full on node 3"), ("ok", ""), ("pending", "waiting for quota"), ("ok", ""), ("failed", "timeout after 30s"),
     ("ok", ""), ("ok", ""), ("pending", "queued"), ("ok", "")])])

case(
    37,
    claim="A fixed-width text table is stored as one geometry header (leading gap, per-column width and L/R alignment, gap widths) plus tab-separated trimmed cells; decoding re-pads every line to the identical bytes, and any table the geometry cannot express exactly is left untouched.",
    encode=enc37,
    decode=dec37,
    fixtures=[
        Fixture("kubectl get pods", KUBECTL, True),
        Fixture("docker ps with empty PORTS cells", DOCKER_PS, True),
        Fixture("ps aux with right-aligned numbers", PS_AUX, True),
        Fixture("ps aux where a number overflows its column", PS_AUX_OVERFLOW, False),
        Fixture("df -h", DF_H, True),
        Fixture("last column empty on some rows (small table: header costs as much as it saves)", NOTES, None),
        Fixture("prose", "The quick brown fox jumps over the lazy dog.\nA second line, a little longer than the first one.\nShort.\n", False),
        Fixture("single column", "alpha\nbeta\ngamma\ndelta\n", False),
        Fixture("table with a tab", KUBECTL.replace("Running", "Run\ting", 1), False),
        Fixture("blank line inside the table", KUBECTL.replace("\ncron", "\n\ncron"), False),
        Fixture("trailing spaces on a line", KUBECTL.replace("9h\n", "9h  \n"), False),
        Fixture("CRLF output", KUBECTL.replace("\n", "\r\n"), False),
        Fixture("table without a final newline", KUBECTL.rstrip("\n"), True),
        Fixture("header-looking text from the encoder", "§tbl 0 4L 1 3L\nab\tcd\nef\tgh\n", None),
        Fixture("sigil inside a cell, wide characters", KUBECTL.replace("Running", "Run§ing", 1).replace("fghij ", "日本語 ", 1), None),
        Fixture("every cell right-aligned, indented", "".join(f"  {a:>6}{b:>8}{c:>5}\n" for a, b, c in [("id", "size", "n"), ("1", "20480", "3"), ("22", "5", "17"), ("333", "1", "2")]), True),
    ],
    min_gain=0.1,
    idempotent=False,  # re-encoding encoded text starts with the sigil, so it is escaped (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #38 Markdown table padding and separator rows

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _md_cells(row):
    """Raw cells of a row that starts with '|' and ends with an unescaped '|' (trailing blanks allowed), else None."""
    cells, cur, i, n = [], [], 1, len(row)
    while i < n:
        c = row[i]
        if c == "\\" and i + 1 < n:
            cur.append(row[i:i + 2])
            i += 2
        elif c == "|":
            cells.append("".join(cur))
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    return None if "".join(cur).strip(" \t") else cells


def _md_delim(cell):
    return re.fullmatch(r":?-+:?", cell.strip(" \t")) is not None


def _md_align(cell):
    c = cell.strip(" \t")
    return (":" if c.startswith(":") else "") + "-" + (":" if c.endswith(":") else "")


def minimal_md_tables(text):
    if "\r" in text:
        return text
    lines = text.split("\n")
    out, i, fence = [], 0, None
    while i < len(lines):
        ln = lines[i]
        m = _FENCE.match(ln)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not ln.strip(" `~"):
                fence = None
            out.append(ln)
            i += 1
            continue
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            out.append(ln)
            i += 1
            continue
        head = _md_cells(ln) if ln.startswith("|") else None
        sep = _md_cells(lines[i + 1]) if head is not None and i + 1 < len(lines) and lines[i + 1].startswith("|") else None
        if head is None or sep is None or len(sep) != len(head) or not all(_md_delim(c) for c in sep):
            out.append(ln)
            i += 1
            continue
        j, body = i + 2, []
        while j < len(lines) and lines[j].startswith("|") and _md_cells(lines[j]) is not None:
            body.append(_md_cells(lines[j]))
            j += 1
        if any(len(r) != len(head) for r in body):
            out.extend(lines[i:j])  # ragged rows: GFM pads or truncates them, so do not touch the table
        else:
            def trim(c):
                c = c.strip(" \t")
                odd = (len(c) - len(c.rstrip("\\"))) % 2  # "a\\" + "|" would read as an escaped pipe
                return c + " " if odd else c

            fmt = lambda cells: "|" + "|".join(trim(c) for c in cells) + "|"  # noqa: E731
            out.append(fmt(head))
            out.append("|" + "|".join(_md_align(c) for c in sep) + "|")
            out.extend(fmt(r) for r in body)
        i = j
    return "\n".join(out)


def md_model(text):
    """Oracle view built differently (regex cell split, own fence tracking): [("line", s) | ("table", head, aligns, rows)]."""
    cellre = re.compile(r"((?:\\.|[^|\\])*)\|")

    def cells(row):
        if not row.startswith("|"):
            return None
        body = row[1:]
        got = cellre.findall(body)
        used = sum(len(c) + 1 for c in got)
        return [c.strip(" \t") for c in got] if not body[used:].strip(" \t") else None

    lines, model, k, fence = text.split("\n"), [], 0, ""
    while k < len(lines):
        ln = lines[k]
        f = re.match(r"^ {0,3}([`~])\1{2,}", ln)
        if fence:
            if f and ln.strip().startswith(fence) and set(ln.strip()) <= set(fence):
                fence = ""
            model.append(("line", ln))
            k += 1
        elif f:
            fence = re.match(r"^ {0,3}([`~]+)", ln).group(1)
            model.append(("line", ln))
            k += 1
        else:
            h = cells(ln)
            s = cells(lines[k + 1]) if h is not None and k + 1 < len(lines) else None
            if h is not None and s is not None and len(h) == len(s) and all(re.fullmatch(r":?-+:?", c) for c in s):
                rows, k = [], k + 2
                while k < len(lines) and cells(lines[k]) is not None:
                    rows.append(cells(lines[k]))
                    k += 1
                model.append(("table", h, [(c[0] == ":", c[-1] == ":") for c in s], rows))
            else:
                model.append(("line", ln))
                k += 1
    return model


PR_TABLE = """## Review status

| Check            | Result  | Duration | Notes                  |
|:-----------------|:-------:|---------:|------------------------|
| lint             | pass    |     12 s | none                   |
| unit tests       | pass    |  4 m 10s | 2 skipped              |
| integration      | fail    |  9 m 41s | see job 4411           |
| docs build       | pass    |     58 s |                        |

Merge when green.

| Owner  | Area      |
| ------ | --------- |
| alice  | backend   |
| bob    | frontend  |
"""
FENCED = PR_TABLE + "\nExample of the syntax:\n\n```markdown\n| a   | b   |\n|-----|-----|\n| 1   | 2   |\n```\n\n~~~\n| x   | y   |\n|-----|-----|\n~~~\n"

case(
    38,
    claim="GFM pipe tables lose their cell padding and become |a|b| rows with a minimal :-: / -: / :- delimiter row; cell text (trimmed), column count and alignment are kept, and fenced code, text with pipes that is not a table, tables without outer pipes and ragged tables are left as they were.",
    loses="whitespace around cell text and the number of dashes in the delimiter row",
    encode=lambda t: guarded(t, minimal_md_tables),
    equivalent=lambda x, e: md_model(x) == md_model(e),
    fixtures=[
        Fixture("padded status tables with alignment", PR_TABLE, True),
        Fixture("tables inside code fences stay verbatim", FENCED, True),
        Fixture("already minimal", "|a|b|\n|-|:-:|\n|1|2|\n", False),
        Fixture("shell pipelines and bars that are not tables", "cat a.log | grep ERR | wc -l\n| not a table, no delimiter row |\n| still not |\n", False),
        Fixture("table without outer pipes", "a   | b\n----|----\n1   | 2\n", False),
        Fixture("ragged body row", "| a   | b   |\n|-----|-----|\n| 1   | 2   | 3 |\n| 4   |\n", False),
        Fixture("header and delimiter disagree", "| a   | b   |\n|-----|-----|-----|\n| 1   | 2   |\n", False),
        Fixture("escaped pipes and code spans", "| cmd             | meaning        |\n|-----------------|----------------|\n| `a \\| b`        | pipe in cell   |\n| `x`  y          | two  spaces    |\n", None),
        Fixture("empty cells, unicode, trailing backslash", "| é  |    |  日本  |\n|----|----|-------|\n|    | a\\ | ✓     |\n", None),
        Fixture("CRLF text", PR_TABLE.replace("\n", "\r\n"), None),
        Fixture("table with trailing spaces and no final newline", "|  a  |  b  |   \n|:---|---:|\n|  1  |  2  |    ", None),
        Fixture("table directly followed by text", "| a   | b   |\n|-----|-----|\n| 1   | 2   |\nlazy continuation line\n", None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #228 Join byte-separated hex runs: hexdump -C, xxd, od -x, openssl, ssh-keygen

HEX = "§hex\n"
_HEXRUN = re.compile(
    r"(?<![0-9A-Fa-f])(?:(?P<sp>[0-9A-Fa-f]{2}(?: [0-9A-Fa-f]{2}){5,})|(?P<co>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){7,})"
    r"|(?P<wd>[0-9A-Fa-f]{4}(?: [0-9A-Fa-f]{4}){3,}))(?![0-9A-Fa-f])")
_JOINED = re.compile(r"§(:|w)?((?:[0-9A-Fa-f]{2})+)")


def _join_hex(text):
    if SIG in text:
        return text  # the joined runs are marked with the sigil, so a sigil already in the text would be ambiguous

    def rep(m):
        kind = m.lastgroup
        run = m.group(kind)
        toks = run.split(":" if kind == "co" else " ")
        if not re.search("[A-Fa-f]", run) and set(toks) != {"00"}:
            return run  # a run of plain decimal numbers (ids, counts) is not hex data
        return SIG + {"sp": "", "co": ":", "wd": "w"}[kind] + "".join(toks)

    body = _HEXRUN.sub(rep, text)
    return HEX + body if body != text else text


def _split_hex(text):
    def un(m):
        k, h = m.group(1), m.group(2)
        step = 4 if k == "w" else 2
        return (":" if k == ":" else " ").join(h[i:i + step] for i in range(0, len(h), step))

    return _JOINED.sub(un, text[len(HEX):])


enc228, dec228 = make_codec(HEX, _join_hex, _split_hex)


def _chunks(data, n):
    return [data[i:i + n] for i in range(0, len(data), n)]


def hexdump_c(data):
    out = []
    for off, ch in zip(range(0, len(data), 16), _chunks(data, 16)):
        a, b = " ".join(f"{x:02x}" for x in ch[:8]), " ".join(f"{x:02x}" for x in ch[8:])
        out.append(f"{off:08x}  {a:<23}  {b:<23}  |{''.join(chr(x) if 32 <= x < 127 else '.' for x in ch)}|")
    return "\n".join(out) + f"\n{len(data):08x}\n"


def xxd(data):
    out = []
    for off, ch in zip(range(0, len(data), 16), _chunks(data, 16)):
        g = " ".join(c.hex() for c in _chunks(ch, 2))
        out.append(f"{off:08x}: {g:<39}  {''.join(chr(x) if 32 <= x < 127 else '.' for x in ch)}")
    return "\n".join(out) + "\n"


def od_x(data):
    out = []
    for off, ch in zip(range(0, len(data), 16), _chunks(data, 16)):
        ch = ch + b"\0" * (len(ch) % 2)
        out.append(f"{off:07o} " + " ".join(f"{ch[i + 1]:02x}{ch[i]:02x}" for i in range(0, len(ch), 2)))
    return "\n".join(out) + f"\n{len(data):07o}\n"


def colon_block(data, indent, per=15):
    return "".join(" " * indent + ":".join(f"{x:02x}" for x in ch) + (":" if i + per < len(data) else "") + "\n"
                   for i, ch in zip(range(0, len(data), per), _chunks(data, per)))


_DATA = random.Random(228).randbytes(160)
OPENSSL = ("Certificate:\n    Data:\n        Version: 3 (0x2)\n        Serial Number:\n" + colon_block(_DATA[:11], 12)
           + "        Signature Algorithm: sha256WithRSAEncryption\n        Issuer: C = US, O = Example CA\n"
           "        Subject Public Key Info:\n            Public-Key: (2048 bit)\n            Modulus:\n" + colon_block(_DATA[11:131], 20)
           + "            Exponent: 65537 (0x10001)\n    Signature Algorithm: sha256WithRSAEncryption\n" + colon_block(_DATA[131:], 9))
SSH_MD5 = "256 MD5:" + ":".join(f"{x:02x}" for x in _DATA[:16]) + " alice@laptop (ED25519)\n"

case(
    228,
    claim="Runs of byte tokens separated by single spaces (hexdump -C, 2-digit), single colons (openssl, ssh-keygen -E md5) or 4-digit words (xxd, od -x) are joined into one marked run; decoding splits each run back at the recorded width and separator, so the original text is restored exactly.",
    encode=enc228,
    decode=dec228,
    fixtures=[
        Fixture("hexdump -C of 256 bytes", hexdump_c(random.Random(1).randbytes(256)), True),
        Fixture("xxd of 192 bytes", xxd(random.Random(2).randbytes(192)), True),
        Fixture("od -x of 128 bytes", od_x(random.Random(3).randbytes(128)), True),
        Fixture("openssl x509 -text", OPENSSL, True),
        Fixture("ssh-keygen -E md5 -lf", SSH_MD5 * 3, True),
        Fixture("prose, git sha, short hex lists", "commit 3f2a9c4e1b7d8a60f5c2e9b3a1d4c7e8f0a2b6c9\nthe be ad fa ce de words, and #ff00aa colours\nde ad be ef\n", False),
        Fixture("MAC, IPv6, UUID", "link/ether 00:1a:2b:3c:4d:5e\ninet6 2001:db8::ff00:42:8329\nid 550e8400-e29b-41d4-a716-446655440000\n", False),
        Fixture("decimal numbers that happen to be two digits", "ids: 10 20 30 40 50 60 70 80 90\ncounts: 11 12 13 14 15 16 17 18\n", False),
        Fixture("text with the sigil next to hex digits", "price 5§abcd and §:00ff and §w1234\n" + hexdump_c(random.Random(4).randbytes(48)), None),
        Fixture("text that starts like encoder output", "§hex\n§48656c6c6f20776f\nnot encoded\n", None),
        Fixture("upper and mixed case, run at EOF, glued neighbours", "DE AD BE EF 00 11 22 33\nzzaa bb cc dd ee ff gg\nAa Bb cC Dd eE Ff 1234\n0A 0B 0C 0D 0E 0F", None),
        Fixture("zero fill and a repeated-line star", "00000000  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00  |................|\n*\n00001000\n", None),
    ],
    min_gain=0.05,
    idempotent=False,  # re-encoding encoded text starts with the sigil, so it is escaped (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #323 Unwrap syntax-highlighter span soup in HTML code blocks

_ATTR = r"""(?:[^<>"']|"[^"<]*"|'[^'<]*')"""  # one attribute character, or a quoted value (which may hold '>' but never '<')
_TAG = re.compile(rf"</?[A-Za-z][A-Za-z0-9-]*(?:\s{_ATTR}*)?>")


def _open(name, cls=None):
    """Regex source of an opening <name ...> tag, optionally one whose class attribute lists the class token cls."""
    want = rf'(?:\s{_ATTR}*?)??\sclass="[^"<]*(?<![^\s"])(?:{cls})(?![^\s"])[^"<]*"' if cls else r""
    return rf"<{name}(?![A-Za-z0-9-]){want}(?:\s{_ATTR}*)?>"


_PRE = re.compile(rf"({_open('pre')})(.*?)(</pre\s*>)", re.S | re.I)
_CODE_WRAP = re.compile(rf"(\s*{_open('code')})(.*)(</code\s*>\s*)\Z", re.S | re.I)
_LINENO_TD = re.compile(rf"{_open('td', 'linenos|hljs-ln-numbers')}.*?</td\s*>", re.S | re.I)
_LINENO_SPAN = re.compile(rf"{_open('span', 'linenos?')}.*?</span\s*>", re.S | re.I)


def flatten_highlighted(text):
    text = _LINENO_TD.sub("", text)

    def fix(m):
        open_, inner, close = m.groups()
        inner = _LINENO_SPAN.sub("", inner)
        names = {re.match(r"</?([A-Za-z0-9-]+)", t.group()).group(1).lower() for t in _TAG.finditer(inner)}
        if not names or names - {"span", "code"}:
            return m.group(0)  # no markup to drop, or markup that carries meaning (links, images, breaks): keep the block as it is
        w = _CODE_WRAP.match(inner)
        pre, body, post = (w.group(1), w.group(2), w.group(3)) if w else ("", inner, "")
        flat = _TAG.sub("", body)
        if "<" in flat:
            return m.group(0)  # a '<' that is not one of the tags we removed: unknown markup, keep the block
        return open_ + pre + flat + post + close

    return _PRE.sub(fix, text)


class _CodeView(HTMLParser):
    """Oracle view: text of every <pre> element (entities decoded) and of everything outside <pre>, ignoring line-number
    cells and spans. Uses html.parser's own tokenizer, not the regexes the encoder uses."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.pres, self.outside, self.skip, self.closed_lineno_tds = 0, [], [], None, 0

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if self.skip:
            self.skip[1] += tag == self.skip[0]
        elif (tag == "td" and ("linenos" in classes or "hljs-ln-numbers" in classes)) or (tag == "span" and ("linenos" in classes or "lineno" in classes)):
            self.skip = [tag, 1]
        elif tag == "pre":
            if self.depth == 0:
                self.pres.append([])
            self.depth += 1

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] -= 1
                if self.skip[1] == 0:
                    self.closed_lineno_tds += tag == "td"
                    self.skip = None
        elif tag == "pre" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.skip:
            (self.pres[-1] if self.depth else self.outside).append(data)


def code_view(html_text):
    v = _CodeView()
    v.feed(html_text)
    v.close()
    return ["".join(p) for p in v.pres], "".join(v.outside), v.closed_lineno_tds


def oracle323(x, e):
    """Same <pre> texts and same text outside them (line-number cells ignored), and no complete line-number cell is left."""
    px, ox, _ = code_view(x)
    pe, oe, left = code_view(e)
    return px == pe and ox == oe and left == 0


_PYTOK = re.compile(r"""(?P<c>\#[^\n]*)|(?P<s>"[^"\n]*"|'[^'\n]*')|(?P<k>\b(?:def|return|import|from|for|in|if|else|class|raise|with|as|None|True|False)\b)"""
                    r"""|(?P<n>\b\d+(?:\.\d+)?\b)|(?P<i>[A-Za-z_]\w*)|(?P<o>[-+*/=<>!%&|.:,()\[\]{}]+)|(?P<w>[ \t]+)|(?P<nl>\n)|(?P<x>.)""")
_CLASSES = {
    "pygments": {"c": "c1", "s": "s2", "k": "k", "n": "mi", "i": "n", "o": "o", "x": "err"},
    "hljs": {"c": "hljs-comment", "s": "hljs-string", "k": "hljs-keyword", "n": "hljs-number", "o": "hljs-operator"},
    "prism": {"c": "token comment", "s": "token string", "k": "token keyword", "n": "token number", "o": "token operator", "i": "token function"},
}


def highlight(code, style):
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")  # noqa: E731
    colors = {"c": "#6A737D", "s": "#9ECBFF", "k": "#F97583", "n": "#79B8FF", "i": "#E1E4E8", "o": "#F97583", "w": "#E1E4E8", "x": "#E1E4E8"}
    out = []
    for m in _PYTOK.finditer(code):
        kind, tok = m.lastgroup, m.group()
        if style == "shiki" and kind != "nl":
            out.append(f'<span style="color:{colors[kind]}">{esc(tok)}</span>')
        elif style != "shiki" and kind in _CLASSES[style]:
            out.append(f'<span class="{_CLASSES[style][kind]}">{esc(tok)}</span>')
        else:
            out.append(esc(tok))
    return "".join(out)


SAMPLE = '''import json
from pathlib import Path


def load(path, strict=False):
    # read the config, fail on unknown keys when strict
    data = json.loads(Path(path).read_text())
    if strict and "extra" in data:
        raise ValueError("unknown key: extra <" + str(len(data)) + "> & more")
    return {"name": data.get("name", 'anon'), "retries": 3}
'''
INTRO = '<html><body><h1>Loading config</h1>\n<p>Call <code>load()</code> with a path; see <a href="/api#load">the API page</a>.</p>\n'
OUTRO = "\n<p>That is all &mdash; it returns a dict.</p>\n</body></html>\n"
PYG = (INTRO + '<div class="highlight"><pre><span></span>' + highlight(SAMPLE, "pygments") + "</pre></div>" + OUTRO)
HLJS = INTRO + '<pre><code class="hljs language-python">' + highlight(SAMPLE, "hljs") + "</code></pre>" + OUTRO
PRISM = INTRO + '<pre class="language-python"><code class="language-python">' + highlight(SAMPLE, "prism") + "</code></pre>" + OUTRO
SHIKI = (INTRO + '<pre class="shiki github-dark" style="background-color:#24292e;color:#e1e4e8" tabindex="0"><code>'
         + "\n".join(f'<span class="line">{highlight(ln, "shiki")}</span>' for ln in SAMPLE.rstrip("\n").split("\n")) + "</code></pre>" + OUTRO)
_NUMS = "\n".join(f'<span class="normal">{i:2d}</span>' for i in range(1, SAMPLE.count("\n") + 1))
PYG_TABLE = (INTRO + '<div class="highlight"><table class="highlighttable"><tr><td class="linenos"><div class="linenodiv"><pre>' + _NUMS
             + '</pre></div></td><td class="code"><div class="highlight"><pre><span></span>' + highlight(SAMPLE, "pygments")
             + "</pre></div>\n</td></tr></table></div>" + OUTRO)
PYG_INLINE_NUMS = (INTRO + '<div class="highlight"><pre><span></span>' + "".join(
    f'<span class="linenos">{i:2d} </span>{highlight(ln, "pygments")}\n' for i, ln in enumerate(SAMPLE.rstrip("\n").split("\n"), 1)) + "</pre></div>" + OUTRO)

case(
    323,
    claim="Inside <pre> blocks whose only markup is <span> and <code>, the tags are removed (entities left as written) and line-number cells and spans are dropped; paragraphs, links, other markup and blocks with links, images or breaks are untouched.",
    loses="the class and style attributes of the highlighter spans, and line numbers",
    encode=lambda t: guarded(t, flatten_highlighted),
    equivalent=oracle323,
    fixtures=[
        Fixture("Pygments", PYG, True),
        Fixture("highlight.js", HLJS, True),
        Fixture("Prism", PRISM, True),
        Fixture("Shiki with per-line spans", SHIKI, True),
        Fixture("Pygments table with a line-number column", PYG_TABLE, True),
        Fixture("Pygments with inline line numbers", PYG_INLINE_NUMS, True),
        Fixture("page without code", INTRO + "<p>Nothing to see &amp; nothing to flatten.</p>\n</body></html>\n", False),
        Fixture("pre with plain text only", "<pre>$ make test\nok 12 tests\n</pre>\n", False),
        Fixture("pre with a link", '<pre>see <a href="/docs/x">the docs</a> and <span class="k">def</span> f()\n</pre>\n', False),
        Fixture("pre with a line break tag", "<pre><span>a</span><br><span>b</span></pre>\n", False),
        Fixture("unclosed pre (truncated page)", PYG[: len(PYG) // 2], None),
        Fixture("raw < and a > inside an attribute", '<pre><span class="k">a</span> < <span title="x>y">b</span></pre>\n<pre><span title="a>b">x</span> y</pre>\n', None),
        Fixture("upper-case tags and entity soup", '<PRE><SPAN CLASS="k">&lt;div&gt;</SPAN> &amp;&amp; <SPAN CLASS="s">&quot;a&#x27;b&quot;</SPAN> &#8594; &nbsp;x</PRE>\n', None),
        Fixture("two blocks and text between", PYG + PRISM, True),
    ],
    min_gain=0.3,
)

# ---------------------------------------------------------------------------------------------------------------
# #325 Decode base64 text content fields in API JSON

B64ENC = "decoded-from-base64/"
_SECRET_NAME = re.compile(r"(^|/)(\.env(\..*)?|id_[a-z0-9]+|.*\.(pem|key|p12|pfx|jks|keystore)|\.npmrc|\.netrc|credentials|secrets?(\..*)?)$", re.I)
_SECRET_BODY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|^[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|API_?KEY|PASSWD)[A-Z0-9_]*\s*[=:]", re.M | re.I)


def _wrap60(b64, width, nl):
    if width == 0:
        return b64 + ("\n" if nl else "")
    return "\n".join(b64[i:i + width] for i in range(0, len(b64), width)) + ("\n" if nl else "")


def _b64_shape(val):
    """(width, trailing newline) if val is canonical base64 as GitHub writes it (fixed-width lines), else None."""
    nl = val.endswith("\n")
    lines = val[:-1].split("\n") if nl else val.split("\n")
    width = len(lines[0]) if len(lines) > 1 else 0
    return (width, nl) if lines and all(lines) else None


def _decode_obj(pairs):
    """New pairs for one object node, or None if it is not a decodable GitHub contents-style object."""
    keys = {}
    for k, v in pairs:
        keys.setdefault(json.loads(k), []).append(v)
    enc, content = keys.get("encoding", []), keys.get("content", [])
    if len(enc) != 1 or len(content) != 1 or enc[0] != ("l", '"base64"') or content[0][0] != "l" or content[0][1][:1] != '"':
        return None
    lex = content[0][1]
    val = json.loads(lex)
    shape = _b64_shape(val)
    if shape is None:
        return None
    width, nl = shape
    try:
        raw = base64.b64decode(val.replace("\n", ""), validate=True)
        text = raw.decode("utf-8")  # strict: invalid UTF-8 raises
    except ValueError:
        return None
    if _wrap60(base64.b64encode(raw).decode(), width, nl) != val or json.dumps(val) != lex:
        return None  # not byte-for-byte what the decoder will rebuild
    if "\x00" in text or re.search("[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        return None  # binary in disguise
    names = [json.loads(v[1]) for k, v in pairs if json.loads(k) in ("name", "path") and v[0] == "l" and v[1][:1] == '"']
    if any(_SECRET_NAME.search(n) for n in names) or _SECRET_BODY.search(text):
        return None  # secrets stay opaque
    return [(k, ("l", ser_str(text)) if json.loads(k) == "content" else ("l", json.dumps(f"{B64ENC}{width}/{int(nl)}")) if json.loads(k) == "encoding" else v) for k, v in pairs]


def _walk325(n, changed):
    if n[0] == "l":
        return n
    if n[0] == "a":
        return ("a", [_walk325(c, changed) for c in n[1]])
    if any(json.loads(k) == "kind" and v[0] == "l" and v[1] in ('"Secret"', '"SecretList"') for k, v in n[1]):
        return n  # a Kubernetes Secret (or list of them): nothing inside is touched
    new = _decode_obj(n[1])
    if new is not None:
        changed.append(1)
        return ("o", new)
    return ("o", [(k, _walk325(v, changed)) for k, v in n[1]])


def _b64_enc(text):
    if not is_json(text):
        return text
    changed: list[int] = []
    new = _walk325(lex_parse(text), changed)
    return lex_dump(new) if changed else text


def _unwalk325(n):
    if n[0] == "l":
        return n
    if n[0] == "a":
        return ("a", [_unwalk325(c) for c in n[1]])
    enc = [v for k, v in n[1] if json.loads(k) == "encoding"]
    if len(enc) == 1 and enc[0][0] == "l" and enc[0][1].startswith('"' + B64ENC):
        width, nl = json.loads(enc[0][1])[len(B64ENC):].split("/")
        out = []
        for k, v in n[1]:
            if json.loads(k) == "content":
                v = ("l", json.dumps(_wrap60(base64.b64encode(json.loads(v[1]).encode("utf-8")).decode(), int(width), nl == "1")))
            elif json.loads(k) == "encoding":
                v = ("l", '"base64"')
            out.append((k, v))
        return ("o", out)
    return ("o", [(k, _unwalk325(v)) for k, v in n[1]])


def enc325(text):
    if text.startswith(SIG) or B64ENC in text:
        return RAW + text  # the marker is already in the input: escape it so a decoder cannot mistake it for ours
    return guarded(text, _b64_enc)


def dec325(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    return lex_dump(_unwalk325(lex_parse(text))) if is_json(text) else text


_APP = '''"""Small CLI that prints a greeting."""
import argparse
import sys

GREETING = "Hello, {name}! — welcome"


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("name", nargs="?", default="world")
    p.add_argument("--shout", action="store_true", help="upper-case the greeting")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    msg = GREETING.format(name=args.name)
    print(msg.upper() if args.shout else msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def contents_api(path, raw, width=60, nl=True, **extra):
    b64 = base64.b64encode(raw).decode()
    return {"name": path.rsplit("/", 1)[-1], "path": path, "sha": "3d21ec53a331a6f037a91c368710b99387d012c1", "size": len(raw),
            "url": f"https://api.github.com/repos/acme/widgets/contents/{path}?ref=main", "type": "file",
            "content": _wrap60(b64, width, nl), "encoding": "base64", **extra}


GH_CONTENTS = pretty(contents_api("src/app.py", _APP.encode()))
GH_CONTENTS_MIN = compact(contents_api("src/app.py", _APP.encode()))
K8S_SECRET = pretty({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "db", "namespace": "prod"}, "type": "Opaque",
                     "data": {"password": base64.b64encode(b"s3cr3t-pass").decode(), "username": base64.b64encode(b"admin").decode(),
                              "tls.crt": base64.b64encode(b"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n").decode()}})
SECRET_WITH_BLOB = pretty({"kind": "Secret", "data": {"a": "YQ=="}, "content": base64.b64encode(b"hello world, plain text").decode(), "encoding": "base64"})

case(
    325,
    claim="In objects that have a \"content\" string and \"encoding\": \"base64\" (GitHub contents API), canonical base64 that decodes to valid UTF-8 without control characters or NULs is replaced by the text (JSON-escaped) and the encoding field records the line width and trailing newline; decoding rebuilds the identical base64, and Kubernetes Secrets, secret-looking files, binaries and non-canonical base64 are left alone.",
    loses="whitespace between JSON tokens (output is minified)",
    encode=enc325,
    equivalent=json_codec_oracle(dec325),
    fixtures=[
        Fixture("gh api contents of a Python file (pretty)", GH_CONTENTS, True),
        Fixture("same, minified", GH_CONTENTS_MIN, True),
        Fixture("two files in a list, one binary", pretty([contents_api("src/app.py", _APP.encode()), contents_api("img/logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(40)))]), True),
        Fixture("kubectl get secret -o json", K8S_SECRET, False),
        Fixture("contents-style blob inside a Secret", SECRET_WITH_BLOB, False),
        Fixture(".env file via the contents API", compact(contents_api(".env", b"API_KEY=abc123\nDEBUG=1\n" * 8)), False),
        Fixture("private key file via the contents API", compact(contents_api("deploy/id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----\n" + b"b3BlbnNzaC1rZXktdjEAAAAA" * 8 + b"\n-----END OPENSSH PRIVATE KEY-----\n")), False),
        Fixture("binary content (a few NUL bytes)", compact(contents_api("data.bin", bytes(range(0, 40)))), False),
        Fixture("mostly text with one NUL and a control character", compact(contents_api("notes.dat", ("plain text line number one\n" * 10).encode() + b"\x00\x01 tail\n")), False),
        Fixture("invalid UTF-8 (latin-1 text)", compact(contents_api("legacy.txt", ("café résumé naïve " * 12).encode("latin-1"))), False),
        Fixture("base64 that is not canonical (no padding, odd wrap)", compact({"content": base64.b64encode(b"hello world, plain text!!").decode().rstrip("=") + "\n", "encoding": "base64"}), False),
        Fixture("not JSON", "content: aGVsbG8=\nencoding: base64\n", False),
        Fixture("single-line base64, no trailing newline, CRLF and unicode text", compact(contents_api("notes.txt", ("line one\r\nline two — 日本語 \U0001f600\r\n" * 6).encode(), width=0, nl=False)), None),
        Fixture("76-column base64 (python base64.encodebytes)", compact(contents_api("a.txt", ("The quick brown fox jumps over the lazy dog. " * 12).encode(), width=76)), True),
        Fixture("empty file", compact(contents_api("empty.txt", b"")), None),
        Fixture("duplicate content key", '{"content":"aGVsbG8gd29ybGQ=\\n","content":"x","encoding":"base64"}', None),
        Fixture("the encoder's own marker in the input", compact({"content": "hello", "encoding": B64ENC + "60/1"}), None),
    ],
    min_gain=0.1,
    idempotent=False,  # encoded output contains the marker, so re-encoding escapes it (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #407 Hoist constant and default-index columns from CSV text tables

HOISTC = "§csvhoist "


def parse_csv(text):
    """RFC 4180 records as [(raw cells, line ending)], or None if the text is not clean CSV (stray quotes, unterminated)."""
    recs, i, n = [], 0, len(text)
    while i < n:
        cells = []
        while True:
            if i < n and text[i] == '"':
                j = i + 1
                while True:
                    k = text.find('"', j)
                    if k < 0:
                        return None
                    if text[k + 1:k + 2] == '"':
                        j = k + 2
                        continue
                    break
                end = k + 1
                if end < n and text[end] not in ",\r\n" or (text[end:end + 1] == "\r" and text[end + 1:end + 2] != "\n"):
                    return None
            else:
                end = i
                while end < n and text[end] != "," and text[end] != "\n" and not (text[end] == "\r" and text[end + 1:end + 2] == "\n"):
                    end += 1
            cells.append(text[i:end])
            i = end
            if i < n and text[i] == ",":
                i += 1
                continue
            break
        eol = "\r\n" if text.startswith("\r\n", i) else "\n" if text[i:i + 1] == "\n" else ""
        i += len(eol)
        recs.append((cells, eol))
    return recs


def _csv_text(recs):
    return "".join(",".join(c) + e for c, e in recs)


def _csv_enc(text):
    recs = parse_csv(text)
    if recs is None or len(recs) < 4 or _csv_text(recs) != text:
        return text
    width = len(recs[0][0])
    if width < 2 or any(len(c) != width for c, _e in recs):
        return text
    head, rows = recs[0][0], recs[1:]
    entries, drop = [], set()
    for j in range(width):
        col = [r[0][j] for r in rows]
        if head[j] in ("", "Unnamed: 0") and all(c == str(i) for i, c in enumerate(col)):
            entries.append([j, head[j], None])  # pandas' default RangeIndex written by to_csv
            drop.add(j)
        elif len(set(col)) == 1:
            entries.append([j, head[j], col[0]])
            drop.add(j)
    if not entries or len(drop) == width:
        return text
    kept = [(([c for j, c in enumerate(cells) if j not in drop]), eol) for cells, eol in recs]
    out = HOISTC + json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n" + _csv_text(kept)
    return out if _csv_dec(out) == text else text  # exactness guard


def _csv_dec(text):
    head, _, body = text.partition("\n")
    entries = json.loads(head[len(HOISTC):])
    recs = parse_csv(body)
    out = []
    for r, (cells, eol) in enumerate(recs):
        cells, full = list(cells), []
        for p in range(len(cells) + len(entries)):
            ent = next((e for e in entries if e[0] == p), None)
            if ent is None:
                full.append(cells.pop(0))
            else:
                full.append(ent[1] if r == 0 else str(r - 1) if ent[2] is None else ent[2])
        out.append((full, eol))
    return _csv_text(out)


enc407, dec407 = make_codec(HOISTC, _csv_enc, _csv_dec)


def df_csv(n, index=True, eol="\n"):
    cols = ["run_id", "model", "lr", "epochs", "region", "env", "schema", "loss", "acc", "note"]
    lines = [(["" if index else None] if index else []) + cols]
    rnd = random.Random(407)
    for i in range(n):
        row = [f"r{1000 + i * 7}", ["resnet", "vit", "bert"][i % 3], f"{rnd.choice([0.001, 0.003, 0.01])}", str(10 * (1 + i % 3)), "us-east-1", "prod", "v3",
               f"{rnd.random():.4f}", f"{rnd.random():.3f}", rnd.choice(["", "ok", '"warm, start"', '"say ""hi"""'])]
        lines.append(([str(i)] if index else []) + row)
    return "".join(",".join(r) + eol for r in lines)


case(
    407,
    claim="CSV columns whose cell text is identical in every row, and pandas' unnamed 0..N-1 index column, are listed once in a header line (position, raw header cell, raw value) and removed from the rows; decoding re-inserts them, restoring the original bytes including quoting and line endings.",
    encode=enc407,
    decode=dec407,
    fixtures=[
        Fixture("df.to_csv() with index and 3 constant columns", df_csv(16), True),
        Fixture("mlflow-style CSV without an index", df_csv(16, index=False), True),
        Fixture("CRLF CSV", df_csv(14, eol="\r\n"), True),
        Fixture("no constant column, no index", "a,b,c\n1,x,3\n2,y,4\n3,z,5\n4,w,6\n", False),
        Fixture("index column that does not start at 0", "".join(f"{i + 1},v{i},{i * 3}\n" for i in range(8)).join(["i,a,b\n", ""]), False),
        Fixture("quoted 0 is not the default index", ",a,b\n" + "".join(f'"{i}",x{i},y{i % 3}\n' for i in range(14)), False),
        Fixture("ragged rows", "a,b,c\n1,2\n3,4,5\n6,7,8\n9,9,9\n", False),
        Fixture("only two data rows", "a,b,c\n1,1,x\n1,1,y\n", False),
        Fixture("prose with commas", "Well, it works, mostly.\nHowever, not always, as seen.\nSo, test, test, test.\nOk, done, here, now.\n", False),
        Fixture("every column constant", "a,b\n1,2\n1,2\n1,2\n1,2\n", None),
        Fixture("empty-string constant column and trailing comma", "a,b,c,\n1,,x,\n2,,y,\n3,,z,\n4,,w,\n", None),
        Fixture("multi-line quoted cells, same value quoted two ways", 'k,v,s\n1,"a\nb",x\n2,"a\nb",x\n3,a,x\n4,"a\nb",x\n', None),
        Fixture("unterminated quote (truncated CSV)", df_csv(8)[:-9] + '"cut', None),
        Fixture("text that starts like encoder output", '§csvhoist [[0,"a","1"]]\nb\n1\n2\n3\n', None),
        Fixture("no final newline", df_csv(12).rstrip("\n"), True),
    ],
    min_gain=0.05,
    idempotent=False,  # re-encoding encoded text starts with the sigil, so it is escaped (grows) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #408 Strip column-uniform trailing zeros from printed floats

_FLOAT = re.compile(r"-?[0-9]+\.[0-9]+\Z")


def strip_uniform_zeros(text):
    lines = text.split("\n")
    cols: dict[int, list[tuple[int, int, str]]] = {}
    for li, ln in enumerate(lines):
        for m in re.finditer(r"\S+", ln):
            if _FLOAT.match(m.group()):
                cols.setdefault(m.end(), []).append((li, m.start(), m.group()))  # keyed by end offset: right-aligned columns
    edits: dict[int, list[tuple[int, int, str]]] = {}
    for end, items in cols.items():
        if len(items) < 3 or len({len(t.split(".")[1]) for _l, _s, t in items}) != 1 or len(items[0][2].split(".")[1]) < 2:
            continue  # need a clearly uniform fixed-decimals column, not a scatter of unrelated floats
        for li, s, t in items:
            new = t.rstrip("0").rstrip(".")
            if new != t:
                edits.setdefault(li, []).append((s, end, new))
    for li, es in edits.items():
        for s, e, new in sorted(es, reverse=True):
            lines[li] = lines[li][:s] + new + lines[li][e:]
    return "\n".join(lines)


def oracle408(x, e):
    """Same lines, same whitespace between tokens, and each differing token is a float with zeros cut off its end."""
    a, b = x.split("\n"), e.split("\n")
    if len(a) != len(b):
        return False
    for la, lb in zip(a, b):
        pa, pb = re.split(r"(\s+)", la), re.split(r"(\s+)", lb)
        if len(pa) != len(pb):
            return False
        for ta, tb in zip(pa, pb):
            if ta == tb:
                continue
            if not tb or not ta.startswith(tb) or not set(ta[len(tb):]) <= set("0."):
                return False
            try:
                fa, fb = float(ta), float(tb)
            except ValueError:
                return False
            if fa != fb or str(fa)[0] != str(fb)[0] and fa == 0:
                return False
    return True


def describe(cols, rows):
    """pandas DataFrame.describe() layout: index left-aligned, every cell of a column printed with the same decimals."""
    cells = [[f"{v:.6f}" for v in col] for col in cols]
    w = [max(len(c) for c in col) + 2 for col in cells]
    names = ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]
    out = [" " * 5 + "".join(n.rjust(w[i]) for i, n in enumerate(rows))]
    out += [f"{names[r]:<5}" + "".join(cells[i][r].rjust(w[i]) for i in range(len(cells))) for r in range(len(names))]
    return "\n".join(out) + "\n"


DESCRIBE = describe([[200.0, 0.512345, 0.288675, 0.0, 0.25, 0.5, 0.75, 1.0], [200.0, 15.5, 4.125, 1.0, 12.0, 15.5, 19.0, 25.0],
                     [200.0, -0.04, 1.0, -3.2, -0.7, -0.05, 0.62, 2.9], [200.0, 1000.0, 250.5, 500.0, 800.0, 1000.0, 1200.0, 1500.0]], ["a", "b", "c", "d"])
CLASSREP = """              precision    recall  f1-score   support

           0       0.50      1.00      0.67         1
           1       0.00      0.00      0.00         1
           2       1.00      0.50      0.67         2
           3       0.75      0.75      0.75         4

    accuracy                           0.62         8
   macro avg       0.56      0.56      0.52         8
weighted avg       0.66      0.62      0.60         8
"""
FRAME = """   price  qty  total
0  10.50    2  21.00
1   3.00   10  30.00
2  99.90    1  99.90
3   7.25    4  29.00
"""

case(
    408,
    claim="In a printed table, a right-aligned column whose cells all carry the same number of decimals (pandas describe(), sklearn reports, DataFrame prints) has trailing zeros cut from each cell; every cell still parses to the same float, and every other character, including the whitespace between tokens, is unchanged.",
    loses="the fixed decimal width of the column (200.000000 becomes 200, 0.50 becomes 0.5) and the vertical alignment of the table",
    encode=lambda t: guarded(t, strip_uniform_zeros),
    equivalent=oracle408,
    fixtures=[
        Fixture("pandas describe()", DESCRIBE, True),
        Fixture("sklearn classification_report (two decimals: only a few percent)", CLASSREP, None),
        Fixture("DataFrame print with 2-decimal columns", FRAME * 3, True),
        Fixture("integer table", "   a  b\n0  1  2\n1  3  4\n2  5  6\n3  7  8\n", False),
        Fixture("columns with mixed decimals", "   x\n0  1.5\n1  2.25\n2  3.125\n3  4.5\n", False),
        Fixture("prose with a few floats", "The run took 12.500 seconds, then 3.140 more.\nLoss fell to 0.100 and 0.250 later.\nDone at 99.000 percent.\n", False),
        Fixture("floats with no trailing zero", "  a     b\n0 0.51  0.12\n1 0.34  0.56\n2 0.78  0.91\n3 0.23  0.45\n", False),
        Fixture("exponents, NaN, inf, negative zero", "        a         b\nx   1.500000  nan\ny  -0.000000  1.5e+03\nz   2.000000  inf\nw   0.250000  -2.500000\n", None),
        Fixture("left-aligned version-like column is not right-aligned", "name   ver\nalpha  1.10\nbeta   22.10\ngamma  3.10\ndelta  4.10\n", None),
        Fixture("no final newline, CRLF", DESCRIBE.replace("\n", "\r\n").rstrip("\r\n"), True),
        Fixture("tokens with units are not floats", "  t\n0  1.50s\n1  2.50s\n2  3.00s\n3  4.00s\n", False),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #412 ASCII +---+ grid tables to TSV (Spark show(), mysql, beeline)

_BORDER = re.compile(r"\+(?:-+\+)+\Z")


def _grid_at(lines, i):
    """(number of lines, rows of cells) for a clean +---+ grid starting at lines[i], else None."""
    b = lines[i]
    if not _BORDER.match(b):
        return None
    cuts = [k for k, c in enumerate(b) if c == "+"]

    def cells(ln):
        if len(ln) != len(b) or any(ln[k] != "|" for k in cuts):
            return None
        return [ln[cuts[j] + 1:cuts[j + 1]] for j in range(len(cuts) - 1)]

    if i + 2 >= len(lines) or cells(lines[i + 1]) is None or lines[i + 2] != b:
        return None
    rows, k = [cells(lines[i + 1])], i + 3
    while k < len(lines) and cells(lines[k]) is not None:
        rows.append(cells(lines[k]))
        k += 1
    if k >= len(lines) or lines[k] != b:
        return None
    return k + 1 - i, rows


def grid_to_tsv(text):
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        g = _grid_at(lines, i)
        if g is None:
            out.append(lines[i])
            i += 1
            continue
        n, rows = g
        cells = [[c.strip(" ") for c in r] for r in rows]
        if any("\t" in c for r in cells for c in r):
            out.extend(lines[i:i + n])  # a tab inside a cell would be ambiguous in TSV: keep this grid as it is
        else:
            out.extend("\t".join(r) for r in cells)
        i += n
    return "\n".join(out)


def grid_oracle(x, e):
    """Independent oracle: find grids with one regex built from the border's column widths, then check that the encoded
    text holds, line for line, the same text and each grid as its trimmed cells joined by tabs."""
    xs, el = x.split("\n"), e.split("\n")
    at, pos, i = 0, 0, 0
    while i < len(xs):
        got = None
        if _BORDER.match(xs[i]):
            ws = [len(s) for s in xs[i][1:-1].split("+")]
            row = r"\|" + r"\|".join(rf"([^\n]{{{w}}})" for w in ws) + r"\|"
            border = re.escape(xs[i])
            pat = re.compile(rf"{border}\n{row}\n{border}\n(?:{row}\n)*{border}(?:\n|\Z)")
            m = pat.match("\n".join(xs[i:]) + "\n")
            if m:
                block = m.group(0).rstrip("\n").split("\n")
                rows = [[c.strip(" ") for c in re.fullmatch(row, ln).groups()] for ln in block if ln.startswith("|")]
                got = (len(block), rows)
        if got is None:
            if at >= len(el) or el[at] != xs[i]:
                return False
            at, i = at + 1, i + 1
            continue
        n, rows = got
        if any("\t" in c for r in rows for c in r):
            want = xs[i:i + n]
        else:
            want = ["\t".join(r) for r in rows]
        if el[at:at + len(want)] != want:
            return False
        at, i = at + len(want), i + n
    return at == len(el)


SPARK_ROWS = [("Alice", 30, "NY", 1200.5), ("Bob", 25, "SF", 980.0), ("Carolina", 41, "LA", 15000.25), ("Dmitri", 37, "NY", 40.0),
              ("Eve", 29, "SEA", 7.75), ("Frank", 52, "NY", 310.1), ("Grace", 33, "LA", 2200.0), ("Heidi", 45, "SF", 870.9)]
_w = [max(len(h), *(len(str(r[j])) for r in SPARK_ROWS)) for j, h in enumerate(("name", "age", "city", "balance"))]
_b = "+" + "+".join("-" * w for w in _w) + "+"
SPARK = "\n".join([_b, "|" + "|".join(h.rjust(w) for h, w in zip(("name", "age", "city", "balance"), _w)) + "|", _b,
                   *("|" + "|".join(str(c).rjust(w) for c, w in zip(r, _w)) + "|" for r in SPARK_ROWS), _b]) + "\nonly showing top 8 rows\n\n"
MYSQL_ROWS = [(1, "alice", "alice@example.com", "2024-05-01 10:00:00", None), (2, "bob smith", "bob@example.com", "2024-05-02 11:30:00", "a|b"),
              (3, "carol", "", "2024-05-03 09:15:00", "x  y"), (4, "dave", "dave@example.com", "2024-05-04 18:45:00", None), (5, "erin", "erin@example.com", "2024-05-05 07:05:00", "ok")]
_h = ("id", "name", "email", "created_at", "note")
_cells = [[("NULL" if c is None else str(c)) for c in r] for r in MYSQL_ROWS]
_mw = [max(len(h), *(len(r[j]) for r in _cells)) for j, h in enumerate(_h)]
_mb = "+" + "+".join("-" * (w + 2) for w in _mw) + "+"
_num = {0}
MYSQL = "mysql> select * from users;\n" + "\n".join([_mb, "| " + " | ".join(h.ljust(w) for h, w in zip(_h, _mw)) + " |", _mb,
                                                       *("| " + " | ".join((c.rjust(w) if j in _num else c.ljust(w)) for j, (c, w) in enumerate(zip(r, _mw))) + " |" for r in _cells), _mb]) + "\n5 rows in set (0.00 sec)\n"
TWO_GRIDS = "+---+---+\n| a | b |\n+---+---+\n| 1 | 2 |\n+---+---+\n+---+\n| c |\n+---+\n| 3 |\n| 4 |\n+---+\n"

case(
    412,
    claim="A +---+ border grid (header, rule, rows, closing rule) as printed by Spark show(), mysql and beeline becomes one tab-separated line per row with the space padding trimmed; the cells are unchanged, and a grid whose cells contain a tab, a grid that is not clean, and everything outside grids stay as they were.",
    loses="space padding and alignment inside cells (so leading or trailing spaces that were part of a value), and the border and rule lines",
    encode=lambda t: guarded(t, grid_to_tsv),
    equivalent=grid_oracle,
    fixtures=[
        Fixture("Spark show() with right-aligned cells", SPARK, True),
        Fixture("mysql client output with NULLs and a | inside a cell", MYSQL, True),
        Fixture("two grids back to back", TWO_GRIDS, True),
        Fixture("markdown table", "| a | b |\n|---|---|\n| 1 | 2 |\n", False),
        Fixture("box-drawing table", "┌───┬───┐\n│ a │ b │\n├───┼───┤\n│ 1 │ 2 │\n└───┴───┘\n", False),
        Fixture("ASCII art and diff lines", "+-----+\n| hi  |\n+-----+ x\n+++ b/file.py\n+x = 1\n", False),
        Fixture("grid without its closing rule (truncated)", SPARK.split("\nonly")[0].rsplit("\n", 1)[0] + "\n", False),
        Fixture("ragged row", "+---+---+\n| a | b |\n+---+---+\n| 1 | 2 | 3 |\n+---+---+\n", False),
        Fixture("CRLF output", SPARK.replace("\n", "\r\n"), False),
        Fixture("a cell holding a tab", "+-----+----+\n| a   | b  |\n+-----+----+\n| x\ty | z  |\n+-----+----+\n", None),
        Fixture("header only (empty result)", "+----+----+\n| id | nm |\n+----+----+\n+----+----+\n", None),
        Fixture("empty cells, wide characters", "+----+------+\n| a  | b    |\n+----+------+\n|    | 日本   |\n| 1  |      |\n+----+------+\n", None),
        Fixture("grid in the middle of other text", "before\n" + MYSQL + "after\n", True),
    ],
    min_gain=0.1,
)

# @@END@@
