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
    if MARK in text:
        return text  # the marker key already occurs: refuse, so the marker stays unambiguous
    return ser(jtree(text))


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
    try:
        a = jtree(x)
    except Exception:  # noqa: BLE001
        return e == x
    if MARK in x:
        return e == x
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
    claim="Redundant string escapes (\\uXXXX for printable characters, \\/) are written as the characters, and a string value that is itself a JSON object or array is inlined as structure inside {\"§json\": ...}; every value, number spelling, duplicate key and key order is preserved.",
    loses="the exact text of an inlined JSON string (its whitespace and escape spelling; only its parsed value survives), whitespace between tokens, and the spelling of \\uXXXX / \\/ escapes; \\r\\n escapes stay escaped because a raw newline cannot live inside a JSON string",
    encode=lambda t: guarded(t, _norm_escapes),
    equivalent=oracle128,
    fixtures=[
        Fixture("MCP text wrapper around an issue (Go escapes)", MCP_WRAP, True),
        Fixture("webhook payload string (PHP slashes)", WEBHOOK, True),
        Fixture("JSON inside JSON inside JSON", DOUBLE, True),
        Fixture("no escapes, minified", compact({"a": "plain", "b": ["x", "y"], "n": 1.0}), False),
        Fixture("strings that only look like JSON", compact({"a": "{not json}", "b": "[1, 2", "c": "123", "d": "true", "e": " {\"k\": 1}"}), False),
        Fixture("not JSON", "plain log line with \\u003c in it\n", False),
        Fixture("the marker key already present", pretty({"§json": {"a": 1}, "s": "{\"a\": 1}"}), None),
        Fixture("lone surrogate, U+2028, NUL", '{"a": "\\ud800", "b": "x\\u2028y", "c": "\\u0000", "d": "\\ud83d\\ude00 \\u00e9"}', None),
        Fixture("inlined JSON keeps duplicate keys and number lexemes", pretty({"s": '{"a": 1, "a": 2, "n": 1E5, "m": 1.0}'}), None),
        Fixture("embedded JSON string with leading space is left as a string", pretty({"s": ' {"a": 1}', "t": '{"b":"\\u003c"}'}), None),
    ],
    min_gain=0.15,
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
        Fixture("constant key at different positions", "\n".join(compact({"a": i, "k": 1} if i % 2 else {"k": 1, "a": i}) for i in range(12)) + "\n", False),
        Fixture("duplicate key inside a record", "\n".join(['{"k":1,"k":2,"a":%d}' % i for i in range(10)]) + "\n", None),
        Fixture("text that starts like the header", "§hoist nd [[0,\"a\",1]]\n{\"b\":1}\n", None),
        Fixture("constants that need escaping", "\n".join(compact({"svc": "a\"b\\c ü", "n": i, "z": 1.0}) for i in range(10)) + "\n", True),
    ],
    min_gain=0.1,
    idempotent=False,
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
    if KV in text or not is_json(text):
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


def dec316(text):
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
    encode=lambda t: guarded(t, _kv_enc),
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
        Fixture("non-string key and key equal to the marker", compact({"A": [{"Key": 1, "Value": "x"}, {"Key": 2, "Value": "y"}], "B": [{"Key": "§kv", "Value": "x"}, {"Key": "z", "Value": "y"}]}), None),
        Fixture("keys spelled differently but equal", compact({"T": [{"Key": "a", "Value": 1}, {"Key": "\\u0061", "Value": 2}]}).replace("\\\\u", "\\u"), None),
    ],
    min_gain=0.15,
    idempotent=False,
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
        cache, reader = EntityCache(state, window=4, epochs=epochs), Reader(window=4)
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
        o4 = call("jira", {"items": [gh_issue(0)]}, expect_ref=False)  # same bytes, other server: never a ref into github's output
        assert o4.startswith("§call ")
        changed = dict(gh_issue(2), milestone=dict(MILESTONE, open_issues=8))  # one changed field makes it a new object
        o5 = call("github", changed)
        assert '"open_issues":8' in o5 and '"open_issues":7' not in o5, "a changed milestone is sent in full, not as a ref to the old one"
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

# @@END@@
