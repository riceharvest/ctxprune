"""Structured-data ideas, batch B: #420 #519 #520 #522 #525 #526 #528 #534 #619 #639 #766 #778 #792 #879 #881 #882 #895.

Everything here is a reference prototype: stdlib only, written to be tested, not shipped. Conventions used below:
  * encoders are total and self-gating (they hand the input back unless the result is strictly shorter);
  * where the encoding is signalled in-band (a `§...` header or sigil) the escape path is explicit and the case sets
    idempotent=False, because re-encoding an encoded text must escape it and so grows it by design;
  * oracles parse (json, csv, xml, plistlib, a small YAML-subset loader) instead of re-running the encoder's logic.
"""
import base64
import copy
import csv
import io
import json
import plistlib
import random
import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from datetime import datetime

from harness import Fixture, case

try:  # optional second opinion for the two YAML cases; the stdlib-only loader below is the primary oracle
    import yaml as _pyyaml
except Exception:  # noqa: BLE001
    _pyyaml = None


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


# ===============================================================================================================
# #420 Regular-interval time series: Prometheus/Loki matrices as start, step, flat values. Byte-exact roundtrip.
# `"values":[[T,"v"],...]` -> `"values":"~T@D:v v v;T:v"`. Runs of >=3 equally spaced points become `start@step:values`,
# every other point stays an explicit `T:v`. Timestamps must be canonical decimals so the decoder can rebuild the
# exact text; anything else (pretty-printed JSON, 1700000000.50, odd values) is left alone.

_TS_HDR, _TS_RAW, _TS_MARK = "§ts\n", "§raw\n", '"values":"~'
_TS_ARR = re.compile(r'"values":\[(\[[^\[\]]*\](?:,\[[^\[\]]*\])*)\]')
_TS_PT = re.compile(r'(\d{1,18}(?:\.\d{1,9})?),"([^"\\\s;:@~]+)"')


def _dfmt(d: Decimal) -> str:
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _ts_compact(m: re.Match) -> str:
    pts = []
    for part in m.group(1)[1:-1].split("],["):
        p = _TS_PT.fullmatch(part)
        if not p or _dfmt(Decimal(p.group(1))) != p.group(1):
            return m.group(0)
        pts.append((Decimal(p.group(1)), p.group(2)))
    segs, i, n = [], 0, len(pts)
    while i < n:
        j = i + 1
        if j < n and pts[j][0] > pts[i][0]:
            step = pts[j][0] - pts[i][0]
            while j + 1 < n and pts[j + 1][0] - pts[j][0] == step:
                j += 1
            if j - i + 1 >= 3:
                segs.append(f"{_dfmt(pts[i][0])}@{_dfmt(step)}:" + " ".join(v for _, v in pts[i:j + 1]))
                i = j + 1
                continue
        segs.append(f"{_dfmt(pts[i][0])}:{pts[i][1]}")
        i += 1
    new = '"values":"~' + ";".join(segs) + '"'
    return new if len(new) < len(m.group(0)) else m.group(0)


def ts_encode(x: str) -> str:
    esc = _TS_RAW + x if x.startswith((_TS_HDR, _TS_RAW)) else x
    if _TS_MARK in x:
        return esc  # the original already contains the compact marker: it could not be told apart on decode
    out = _TS_ARR.sub(_ts_compact, x)
    if out == x:
        return esc
    out = _TS_HDR + out
    return out if len(out) < len(x) else esc


def _ts_expand(m: re.Match) -> str:
    pts = []
    for seg in m.group(1).split(";"):
        head, _, vals = seg.partition(":")
        start, _, step = head.partition("@")
        t = Decimal(start)
        for k, v in enumerate(vals.split(" ")):
            pts.append(f'[{_dfmt(t + (Decimal(step) * k if step else 0))},"{v}"]')
    return '"values":[' + ",".join(pts) + "]"


def ts_decode(e: str) -> str:
    if e.startswith(_TS_RAW):
        return e[len(_TS_RAW):]
    if not e.startswith(_TS_HDR):
        return e
    return re.sub(r'"values":"~([^"]*)"', _ts_expand, e[len(_TS_HDR):])


def _prom(series: int, points: int, step: str = "15", start: str = "1700000000", gap_at=(), jitter=False, seed=1) -> str:
    r, res = _rng(seed), []
    for s in range(series):
        t, vals = Decimal(start), []
        for k in range(points):
            if k not in gap_at:
                tt = t + (r.randrange(0, 3) if jitter else 0)
                vals.append(f'[{_dfmt(tt)},"{r.choice(["0.91", "0.93", "0.95", "1", "0.5", "12.25", "0.125"])}"]')
            t += Decimal(step)
        res.append('{"metric":{"__name__":"node_cpu_seconds_total","instance":"10.0.%d.7:9100","mode":"idle"},"values":[%s]}' % (s, ",".join(vals)))
    return '{"status":"success","data":{"resultType":"matrix","result":[' + ",".join(res) + "]}}"


case(
    420,
    claim="Runs of 3+ equally spaced `[timestamp,\"value\"]` pairs in a Prometheus/Loki matrix become `start@step:values`; the decoder restores the exact original bytes, irregular points stay explicit.",
    encode=ts_encode,
    decode=ts_decode,
    fixtures=[
        Fixture("3 series x 30 regular points", _prom(3, 30) + "\n", True),
        Fixture("fractional timestamps, step 0.5", _prom(2, 24, step="0.5", start="1700000000.25"), True),
        Fixture("one missing scrape (gap) splits a run", _prom(2, 30, gap_at=(11,)), True),
        Fixture("instant vector (key is value, not values)", '{"status":"success","data":{"resultType":"vector","result":[{"metric":{"job":"api"},"value":[1700000000,"1"]}]}}\n', False),
        Fixture("pretty-printed matrix is not touched", '{\n  "values": [\n    [1700000000, "1"],\n    [1700000015, "2"],\n    [1700000030, "3"]\n  ]\n}\n', False),
        Fixture("non-canonical timestamps and odd values stay", '{"values":[[1700000000.50,"1"],[1700000001.50,"2"],[1700000002.50,"3"],[1700000003,"a b"]]}', False),
        Fixture("input that already contains the compact marker", _prom(1, 20) + '\n{"values":"~1700000000@15:1 2 3"}', None),
        Fixture("input that starts like encoder output", "§ts\n" + _prom(1, 20), None),
        Fixture("input that starts with the raw escape", "§raw\n" + _prom(1, 20), None),
        Fixture("NaN, Inf, duplicate and decreasing timestamps", '{"values":[[10,"NaN"],[20,"+Inf"],[30,"-Inf"],[30,"1"],[20,"2"],[10,"3"],[10,"1.5e+06"]]}', None),
    ],
    min_gain=0.3,
    idempotent=False,  # encoded text starts with the header, so re-encoding must escape it (grows) by design
)

# ===============================================================================================================
# #519 Lexeme-preserving JSON minifier for multi-document or truncated JSON. Equivalent: only whitespace outside strings
# goes. Number text, escapes, duplicate keys and a string cut off mid-way are untouched because nothing is parsed into values.

_WS = " \t\n\r"
_BARE_OK = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|NaN|-?Infinity")
_BARE_PREFIX = re.compile(r"-?\d*(?:\.\d*)?(?:[eE][+-]?\d*)?|t(?:r(?:ue?)?)?|f(?:a(?:l(?:se?)?)?)?|n(?:u(?:ll?)?)?")


def _is_bare(c: str) -> bool:
    return c not in _WS and c not in '"[]{}:,'


def json_stream_minify(text: str) -> str:
    out: list[str] = []
    last = ""  # last emitted character
    i, n, depth = 0, len(text), 0
    while i < n:
        c = text[i]
        if c in _WS:
            j, nl = i, False
            while j < n and text[j] in _WS:
                nl = nl or text[j] == "\n"
                j += 1
            if last and depth == 0 and nl:
                out.append("\n")
                last = "\n"
            elif last and j < n and _is_bare(last) and _is_bare(text[j]):
                out.append(" ")  # `1 2` must not fuse into `12`
                last = " "
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            tok = text[i:min(j + 1, n)]  # a string cut off by truncation runs to the end, bytes untouched
            out.append(tok)
            last = tok[-1]
            i += len(tok)
        elif _is_bare(c) and c not in "[]{}:,":
            j = i
            while j < n and _is_bare(text[j]):
                j += 1
            tok = text[i:j]
            if not _BARE_OK.fullmatch(tok) and not (j == n and _BARE_PREFIX.fullmatch(tok)):
                return text  # not JSON (python repr, yaml, prose, ansi): do not touch
            out.append(tok)
            last = tok[-1]
            i = j
        else:
            depth = depth + 1 if c in "[{" else max(0, depth - 1) if c in "]}" else depth
            out.append(c)
            last = c
            i += 1
    res = "".join(out)
    return res if len(res) < n else text


_LEX = re.compile(r'"(?:[^"\\]|\\.)*(?:"|\\?\Z)|[^ \t\n\r"\[\]{}:,]+|[\[\]{}:,]', re.S)
_STR_OR_WS = re.compile(r'"(?:[^"\\]|\\.)*(?:"|\\?\Z)|[ \t\n\r]+', re.S)


def _docs(t: str):
    dec = json.JSONDecoder(parse_float=str, parse_int=str, parse_constant=str, object_pairs_hook=list)
    i, out = 0, []
    while True:
        while i < len(t) and t[i] in _WS:
            i += 1
        if i >= len(t):
            return out
        try:
            v, i = dec.raw_decode(t, i)
        except ValueError:
            return None
        out.append(v)


def _json_lexical(a: str) -> bool:
    toks = _LEX.findall(a)
    for k, t in enumerate(toks):
        if t[0] in '"[]{}:,':
            continue
        if not _BARE_OK.fullmatch(t) and not (k == len(toks) - 1 and a.rstrip(_WS).endswith(t) and _BARE_PREFIX.fullmatch(t)):
            return False
    return True


def _json_stream_equiv(a: str, b: str) -> bool:
    if a == b:
        return True  # declining is always safe; shrinking fixtures prove the encoder does not always decline
    if not _json_lexical(a):
        return False  # only JSON-looking text may be rewritten
    if _LEX.findall(a) != _LEX.findall(b):
        return False  # every string, number and structural lexeme, verbatim and in order (so `1 2` did not become `12`)
    for m in _STR_OR_WS.finditer(b):
        if m.group()[0] != '"' and m.group() not in (" ", "\n"):
            return False  # nothing but a single separator may remain outside strings
    da, db = _docs(a), _docs(b)
    return (da is None) == (db is None) and (da is None or da == db)  # real parser agrees, lexemes and duplicate keys included


def _pretty_docs(n: int) -> str:
    r = _rng(3)
    docs = []
    for i in range(n):
        d = {"id": 10_000_000_000_000_000_000 + i, "name": f"item {i}  (spaced)", "tags": [r.choice(["a", "b", "c d"]) for _ in range(3)],
             "owner": {"login": f"user{i}", "site_admin": i % 3 == 0}, "note": None}
        docs.append(json.dumps(d, indent=2))
    return "\n".join(docs) + "\n"


_LEXEMES = '{\n  "a": 1E5,\n  "a": 100000.0,\n  "big": 12345678901234567890,\n  "neg": -0,\n  "s": "tab\\there \\"quoted\\" \\\\ \\u00e9  two  spaces",\n  "nested": [ 1.50, 0.10 , true ,null ]\n}\n'
_TRUNC = json.dumps([{"number": i, "title": f"Fix the flaky test number {i}", "user": {"login": "octocat", "id": 1000 + i}, "labels": [{"name": "bug", "color": "d73a4a"}]} for i in range(12)], indent=2)

case(
    519,
    claim="Whitespace outside JSON strings is removed from concatenated, `jq '.[]'` or truncated JSON by a string-aware scanner; every string, number (1E5, 20-digit ints), duplicate key and a mid-string cut stay byte-identical, and non-JSON text is returned untouched.",
    loses="indentation and spacing outside strings; a newline run between top-level documents becomes one newline; CRLF becomes LF there",
    encode=json_stream_minify,
    equivalent=_json_stream_equiv,
    fixtures=[
        Fixture("ten pretty documents from jq '.[]'", _pretty_docs(10), True),
        Fixture("head -c truncated pretty array (cut mid-string)", _TRUNC[:900], True),
        Fixture("truncated right after a backslash inside a string", _TRUNC[: _TRUNC.index("Fix the flaky test number 5") + 8] + "\\", True),
        Fixture("lexemes: 1E5, duplicate keys, 20-digit ints, escapes", _LEXEMES * 2, True),
        Fixture("already minified NDJSON", '{"a":1,"b":[1,2]}\n{"c":"x y"}\n', False),
        Fixture("python repr with double spaces is not JSON", "{'name':  'a  b', 'n': 1,  'ok': True}\n", False),
        Fixture("indented prose", "Build failed:\n    step 2 exited with 1\n    see log\n", False),
        Fixture("bare scalars one per line", "1\n2\ntrue\nnull\n-3.5e2\n", False),
        Fixture("adjacent bare tokens must not fuse", "1  2   3 \n[1 , 2]  true   false  \n   -0   1E5\n", None),
        Fixture("string cut off with an odd number of backslashes", '{"a" : "x\\\\\\', None),
        Fixture("unicode and raw newline inside a string", '{ "k" : "é  \n  ü" ,  "z": [ ] }', None),
    ],
    min_gain=0.15,
)

# ===============================================================================================================
# #520 Partition mixed-kind JSON arrays by key list; one columnar table per group of 3+, singletons inline. Exact roundtrip
# for arrays that are already in canonical compact form; anything else (pretty JSON, 1E5, duplicate keys) is declined.

_MIX, _MIX_RAW = '{"@mixed":1', "§raw\n"
_LAB = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _cj(v, ascii_: bool = False) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=ascii_)


def mix_encode(x: str) -> str:
    esc = _MIX_RAW + x if x.startswith((_MIX, _MIX_RAW)) else x
    tail = "\n" if x.endswith("\n") else ""
    body = x[: len(x) - len(tail)]
    if not body.startswith("["):
        return esc
    try:
        arr = json.loads(body)
    except Exception:  # noqa: BLE001
        return esc
    ascii_ = next((a for a in (False, True) if _cj(arr, a) == body), None)
    if ascii_ is None:
        return esc  # not canonical compact JSON (whitespace, 1E5, duplicate keys, escapes): cannot restore the bytes
    groups: dict[tuple, list] = {}
    for row in arr:
        if isinstance(row, dict) and row:
            groups.setdefault(tuple(row), []).append(row)
    big = [k for k, v in groups.items() if len(v) >= 3][: len(_LAB)]
    label = {k: _LAB[i] for i, k in enumerate(big)}
    order = "".join(label.get(tuple(r), "-") if isinstance(r, dict) and r else "-" for r in arr)
    one = [r for r, ch in zip(arr, order) if ch == "-"]
    if not big:
        return esc
    obj = {"@mixed": 1, "cols": {label[k]: list(k) for k in big}, "rows": {label[k]: [list(r.values()) for r in groups[k]] for k in big},
           "one": one, "order": order}
    if ascii_:
        obj["ascii"] = 1
    out = _cj(obj, ascii_) + tail
    return out if len(out) < len(x) else esc


def mix_decode(e: str) -> str:
    if e.startswith(_MIX_RAW):
        return e[len(_MIX_RAW):]
    tail = "\n" if e.endswith("\n") else ""
    body = e[: len(e) - len(tail)]
    if not body.startswith(_MIX):
        return e
    d = json.loads(body)
    rows = {k: iter(v) for k, v in d["rows"].items()}
    one = iter(d["one"])
    arr = [next(one) if ch == "-" else dict(zip(d["cols"][ch], next(rows[ch]))) for ch in d["order"]]
    return _cj(arr, bool(d.get("ascii"))) + tail


def _timeline(n: int, seed: int = 5) -> str:
    r, ev = _rng(seed), []
    for i in range(n):
        k = r.choice(["commented", "commented", "labeled", "labeled", "assigned", "cross-referenced", "committed"])
        base = {"id": 1_000_000 + i * 17, "event": k}
        if k == "commented":
            ev.append({**base, "actor": {"login": r.choice(["octocat", "hubot"]), "id": 583231}, "created_at": f"2025-03-0{1 + i % 9}T10:{i:02d}:00Z", "body": "LGTM, thanks for the quick fix"})
        elif k == "labeled":
            ev.append({**base, "actor": {"login": "octocat", "id": 583231}, "created_at": f"2025-03-0{1 + i % 9}T11:{i:02d}:00Z", "label": {"name": r.choice(["bug", "p1", "needs-repro"]), "color": "d73a4a"}})
        elif k == "assigned":
            ev.append({**base, "actor": {"login": "hubot", "id": 1}, "created_at": f"2025-03-0{1 + i % 9}T12:{i:02d}:00Z", "assignee": {"login": "octocat"}})
        elif k == "cross-referenced":
            ev.append({**base, "created_at": f"2025-03-0{1 + i % 9}T13:{i:02d}:00Z", "source": {"type": "issue", "number": 100 + i}})
        else:
            ev.append({"sha": f"{r.getrandbits(80):020x}", "event": k, "message": "wip", "author": {"name": "Octo Cat", "date": "2025-03-01T09:00:00Z"}})
    ev.insert(7, {"event": "renamed", "rename": {"from": "old title", "to": "new title"}})
    return _cj(ev, False) + "\n"


case(
    520,
    claim="A compact JSON array of mixed-shape objects is regrouped by exact key list: groups of 3+ rows become one header plus value rows, singletons stay inline, and an order string restores every row in place byte for byte.",
    encode=mix_encode,
    decode=mix_decode,
    fixtures=[
        Fixture("issue timeline, 5 shapes interleaved", _timeline(36), True),
        Fixture("longer timeline without trailing newline", _timeline(60, seed=9).rstrip("\n"), True),
        Fixture("ascii-escaped unicode in rows", _cj([{"id": i, "kind": "a", "name": "café " + str(i)} for i in range(8)] + [{"x": 1}], True), True),
        Fixture("every shape appears at most twice", _cj([{"a": 1}, {"b": 2}, {"a": 3}, {"b": 4}, {"c": 5}]), False),
        Fixture("pretty-printed array is declined", json.dumps([{"a": i, "b": 2} for i in range(6)], indent=2) + "\n", False),
        Fixture("object, not array", '{"a":1,"b":[1,2,3]}', False),
        Fixture("1E5 and duplicate keys cannot be restored", '[{"a":1E5,"b":2},{"a":2,"b":3},{"a":3,"b":4},{"a":4,"b":5}]', False),
        Fixture("input that looks like encoder output", '{"@mixed":1,"cols":{"A":["a"]},"rows":{"A":[[1]]},"one":[],"order":"A"}', None),
        Fixture("input that starts with the raw escape", "§raw\n" + _timeline(12), None),
        Fixture("nested rows, null, NaN, empty object and key order", '[{"a":1,"b":{"c":[1,{"d":null}]}},{"a":2,"b":{}},{"a":NaN,"b":[]},{"b":1,"a":2},{"b":2,"a":3},{"b":3,"a":4},{},{},{}]', None),
    ],
    min_gain=0.15,
    idempotent=False,
)

# ===============================================================================================================
# #522 NDJSON app logs as logfmt. Byte-exact roundtrip per line for compact JSON objects: `{"level":30,"req":{"id":"a"}}`
# becomes `level=30 req.id=a`. Types survive (a string "1" stays quoted), number text is kept as written, nested objects use
# dotted keys, arrays stay inline JSON. A line is converted only if the decoder reproduces it exactly, so dotted real keys,
# duplicate keys, \u escapes and float spellings that cannot be reproduced stay JSON. Not done: hoisting `time LEVEL msg`
# to the front (that reorders keys, which a byte-exact inverse would have to remember).

_L_NUM = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_L_SEG = r"[A-Za-z_][A-Za-z0-9_-]*"
_L_KEY = re.compile(rf"{_L_SEG}(?:\.{_L_SEG})*")
_L_SAFE = re.compile(r"[A-Za-z0-9_./:@+,%#~-]+")
_L_BARE = re.compile(r'[^\s"=\[\]{}]+')
_L_HDR, _L_RAW, _L_ESC = "§lf\n", "§raw\n", "§!"


class _Num(str):
    """A JSON number kept as its source text (1E5, -0, 20-digit ints)."""


class _Obj(list):
    """A JSON object as an ordered list of (key, value) pairs, duplicates included."""


_LDEC = json.JSONDecoder(parse_float=_Num, parse_int=_Num, parse_constant=_Num, object_pairs_hook=_Obj)


def _lser(v) -> str:
    if isinstance(v, _Obj):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _lser(x) for k, x in v) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_lser(x) for x in v) + "]"
    if isinstance(v, _Num):
        return str.__str__(v)
    if v is True or v is False or v is None:
        return {True: "true", False: "false", None: "null"}[v]
    return json.dumps(v, ensure_ascii=False)


def _lleaf(v) -> str:
    if isinstance(v, _Obj):
        return "{}"
    if isinstance(v, list):
        return _lser(v) if v else "[]"
    if isinstance(v, _Num):
        if not _L_NUM.fullmatch(v):
            raise ValueError("number spelling")
        return str.__str__(v)
    if v is True or v is False or v is None:
        return _lser(v)
    if _L_SAFE.fullmatch(v) and not _L_NUM.fullmatch(v) and v not in ("true", "false", "null"):
        return v
    return json.dumps(v, ensure_ascii=False)


def _lflat(pairs, pre: str, out: list) -> None:
    for k, v in pairs:
        if not re.fullmatch(_L_SEG, k):
            raise ValueError("key")
        if isinstance(v, _Obj) and v:
            _lflat(v, pre + k + ".", out)
        else:
            out.append((pre + k, _lleaf(v)))


def _lparse(s: str):
    pairs, i = [], 0
    while True:
        m = _L_KEY.match(s, i)
        if not m or s[m.end():m.end() + 1] != "=":
            return None
        i = m.end() + 1
        c = s[i:i + 1]
        try:
            if c == '"' or c == "[":
                v, j = _LDEC.raw_decode(s, i)
                if (c == '"') != isinstance(v, str):
                    return None
            elif s.startswith("{}", i):
                v, j = _Obj(), i + 2
            else:
                b = _L_BARE.match(s, i)
                if not b:
                    return None
                t, j = b.group(), b.end()
                v = _Num(t) if _L_NUM.fullmatch(t) else {"true": True, "false": False, "null": None}.get(t, t) if t in ("true", "false", "null") else t
        except ValueError:
            return None
        pairs.append((m.group(), v))
        i = j
        if i == len(s):
            return pairs
        if s[i] != " ":
            return None
        i += 1


def _lunflat(pairs) -> _Obj:
    root = _Obj()
    for key, v in pairs:
        parts, cur = key.split("."), root
        for p in parts[:-1]:
            nxt = next((x for k, x in cur if k == p), None)
            if nxt is None:
                nxt = _Obj()
                cur.append((p, nxt))
            elif not isinstance(nxt, _Obj):
                raise ValueError("leaf and object share a key")
            cur = nxt
        cur.append((parts[-1], v))
    return root


def _line_dec(line: str):
    if line.startswith(("{", "§")):
        return None
    pairs = _lparse(line)
    if pairs is None:
        return None
    try:
        return _lser(_lunflat(pairs))
    except ValueError:
        return None


def _line_enc(line: str):
    if not line.startswith("{"):
        return None
    try:
        obj, end = _LDEC.raw_decode(line)
    except (ValueError, RecursionError):
        return None
    if end != len(line) or not isinstance(obj, _Obj) or not obj:
        return None
    out: list = []
    try:
        _lflat(obj, "", out)
    except ValueError:
        return None
    lf = " ".join(f"{k}={v}" for k, v in out)
    return lf if _line_dec(lf) == line else None  # convert only what the decoder reproduces exactly


def logfmt_encode(x: str) -> str:
    esc = _L_RAW + x if x.startswith((_L_HDR, _L_RAW)) else x
    lines = x.split("\n")
    conv = [_line_enc(l) for l in lines]
    if all(c is None for c in conv):
        return esc
    out = []
    for l, c in zip(lines, conv):
        if c is not None:
            out.append(c)
        elif l.startswith("§") or _line_dec(l) is not None:
            out.append(_L_ESC + l)  # a raw line the decoder would otherwise take for logfmt
        else:
            out.append(l)
    e = _L_HDR + "\n".join(out)
    return e if len(e) < len(x) else esc


def logfmt_decode(e: str) -> str:
    if e.startswith(_L_RAW):
        return e[len(_L_RAW):]
    if not e.startswith(_L_HDR):
        return e
    out = []
    for l in e[len(_L_HDR):].split("\n"):
        if l.startswith(_L_ESC):
            out.append(l[len(_L_ESC):])
        else:
            d = _line_dec(l)
            out.append(l if d is None else d)
    return "\n".join(out)


def _pino(n: int, seed: int = 11) -> str:
    r, lines = _rng(seed), []
    for i in range(n):
        d = {"level": r.choice([30, 30, 30, 40, 50]), "time": 1700000000000 + i * 137, "pid": 4182, "hostname": "api-7d9f",
             "req": {"id": f"req-{r.randrange(10**5):05d}", "method": r.choice(["GET", "POST"]), "url": r.choice(["/v1/items", "/v1/items?page=2", "/healthz"]),
                     "headers": {"host": "api.local", "user-agent": "curl/8.4.0"}},
             "res": {"statusCode": r.choice([200, 200, 201, 404, 500])}, "responseTime": r.randrange(3, 400), "msg": r.choice(["request completed", "request errored", "cache miss"])}
        lines.append(_cj(d))
    return "\n".join(lines) + "\n"


def _zap(n: int) -> str:
    return "".join(_cj({"level": "info", "ts": 1700000000.123 + i, "caller": "server/handler.go:88", "msg": "served", "path": f"/v1/jobs/{i}", "status": 200, "tags": ["a", "b c"], "ok": True, "err": None}) + "\n" for i in range(n))


case(
    522,
    claim="Compact JSON object lines become logfmt (`key=value`, dotted nested keys, quotes only when needed, string-vs-number types kept) and decode back to the exact original line; lines the decoder cannot reproduce stay JSON.",
    encode=logfmt_encode,
    decode=logfmt_decode,
    fixtures=[
        Fixture("20 pino request lines", _pino(20), True),
        Fixture("zap lines with floats, arrays, bool, null", _zap(14), True),
        Fixture("JSON lines mixed with plain text lines", _pino(6) + "starting worker pool\n" + _pino(5, seed=2), True),
        Fixture("plain text and already-logfmt lines", "level=info msg=started port=8080\nERROR something broke\n", False),
        Fixture("pretty-printed JSON is not NDJSON", json.dumps({"level": 30, "msg": "x", "req": {"id": 1}}, indent=2) + "\n", False),
        Fixture("objects with whitespace after separators", '{"level": 30, "msg": "x y"}\n{ "level": 40 }\n', False),
        Fixture("input that starts like encoder output", "§lf\nlevel=1 msg=x\n" + _pino(4), None),
        Fixture("input that starts with the raw escape", "§raw\n" + _pino(4), None),
        Fixture("logfmt-looking and sigil lines beside JSON lines", _pino(3) + "level=info msg=hi\n§!weird\n§lf\n" + _pino(3), None),
        Fixture("tricky values", '{"a.b":1,"c":{"d":1}}\n{"a":1,"a":{"b":2}}\n{"s":"1","t":"true","u":"","v":"null","w":"x=y","x":"é","y":"\\u00e9"}\n{"n":1E5,"m":-0,"k":12345678901234567890,"z":NaN}\n{"e":{},"f":[],"g":[{"h":1},"i j"],"q":"say \\"hi\\""}\n{"a":{"x":1},"b":2,"a":{"y":3}}\n{}\n{"k":"v"}\r\n', None),
    ],
    min_gain=0.07,
    idempotent=False,
)

# ===============================================================================================================
# #525 Strip indentation whitespace from pretty-printed XML. Equivalent: whitespace-only text nodes are dropped only in
# element-only content (at least one child element, no other text), never under xml:space="preserve", never in CDATA,
# comments or processing instructions, never in mixed content or a leaf element. Oracle: ElementTree trees, compared
# node by node with a rule for which whitespace may disappear, plus byte equality of every comment/CDATA/PI.

_XTOK = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE(?:[^<>\[]|\[.*?\])*>|</[^>]*>|<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>|[^<]+|<", re.S)
_XML_WS = " \t\r\n"
_XSPACE = "{http://www.w3.org/XML/1998/namespace}space"
_X_SPACE_ATTR = re.compile(r"""\sxml:space\s*=\s*(["'])(preserve|default)\1""")


def _xblank(t: str) -> bool:
    return t.strip(_XML_WS) == ""


def xml_strip(x: str) -> str:
    try:
        ET.fromstring(x)  # precondition only: the text is well-formed XML
    except Exception:  # noqa: BLE001
        return x
    toks = _XTOK.findall(x)
    if "".join(toks) != x:
        return x
    drop: set[int] = set()
    stack: list[dict] = []
    for i, t in enumerate(toks):
        if not t.startswith("<"):
            if not stack:
                if _xblank(t):
                    drop.add(i)  # outside the root element
            elif _xblank(t):
                stack[-1]["ws"].append(i)
            else:
                stack[-1]["mixed"] = True
        elif t.startswith("<![CDATA["):
            if stack:
                stack[-1]["mixed"] = True
        elif t.startswith(("<!--", "<?", "<!DOCTYPE")):
            continue
        elif t.startswith("</"):
            f = stack.pop()
            if f["elem"] and not f["mixed"] and not f["preserve"]:
                drop.update(f["ws"])
        else:
            if stack:
                stack[-1]["elem"] = True
            if t.endswith("/>"):
                continue
            m = _X_SPACE_ATTR.search(t)
            pres = stack[-1]["preserve"] if stack else False
            if m:
                pres = m.group(2) == "preserve"
            stack.append({"ws": [], "mixed": False, "elem": False, "preserve": pres})
    out = "".join(t for i, t in enumerate(toks) if i not in drop)
    return out if len(out) < len(x) else x


def _xparse(x: str):
    p = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    p.feed(x)
    return p.close()


def _xsame(ea, eb, preserve: bool) -> bool:
    if ea.tag != eb.tag or ea.attrib != eb.attrib:
        return False
    if not isinstance(ea.tag, str):  # comment or processing instruction: exact text
        return ea.text == eb.text
    sp = ea.attrib.get(_XSPACE)
    preserve = True if sp == "preserve" else False if sp == "default" else preserve
    ka, kb = list(ea), list(eb)
    if len(ka) != len(kb):
        return False
    slots_a, slots_b = [ea.text] + [k.tail for k in ka], [eb.text] + [k.tail for k in kb]
    element_only = any(isinstance(k.tag, str) for k in ka) and all(t is None or _xblank(t) for t in slots_a)
    for ta, tb in zip(slots_a, slots_b):
        if (ta or "") == (tb or ""):
            continue
        if element_only and not preserve and _xblank(ta or "") and not tb:
            continue  # the one permitted change: indentation between elements disappears
        return False
    return all(_xsame(a, b, preserve) for a, b in zip(ka, kb))


_XLIT = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>", re.S)


def _xml_equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        ta = _xparse(a)
    except Exception:  # noqa: BLE001
        return False  # only well-formed XML may be rewritten
    try:
        tb = _xparse(b)
    except Exception:  # noqa: BLE001
        return False
    return (_xsame(ta, tb, False) and _XLIT.findall(a) == _XLIT.findall(b)  # comments, CDATA, PIs byte for byte
            and "".join(a.split()) == "".join(b.split()))  # no non-whitespace character moved or vanished


def _pom(n: int) -> str:
    deps = "".join(f"""        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-{name}</artifactId>
            <version>3.2.{i}</version>
            <scope>{'test' if i % 3 == 0 else 'compile'}</scope>
        </dependency>
""" for i, name in enumerate(["web", "data-jpa", "security", "actuator", "test", "validation", "cache", "mail"][:n]))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <!-- keep   this   comment   as it is -->
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <description>
        A  demo   with   inline <b>markup</b> and  text.
    </description>
    <properties>
        <java.version>17</java.version>
        <empty> </empty>
    </properties>
    <dependencies>
{deps}    </dependencies>
</project>
"""


_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />
    <application android:label="@string/app_name">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

_SOAP = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <GetPrice>
      <Item>Apple</Item>
      <Note xml:space="preserve">
    keep   this
       layout  <b/>  </Note>
      <Code><![CDATA[
   if (a < b) {   return 1; }
]]></Code>
      <Para>Hello <b>bold</b> <i>italic</i> world</Para>
      <Box xml:space="default">
        <Inner>x</Inner>
      </Box>
    </GetPrice>
  </soapenv:Body>
</soapenv:Envelope>
"""

case(
    525,
    claim="Whitespace-only text between sibling elements is dropped from well-formed XML; text nodes with content, mixed content, leaf elements, xml:space=\"preserve\" subtrees, CDATA, comments and PIs keep every byte.",
    loses="indentation and line breaks between elements (element-only content) and whitespace outside the root element",
    encode=xml_strip,
    equivalent=_xml_equiv,
    fixtures=[
        Fixture("pom.xml with 8 dependencies", _pom(8), True),
        Fixture("AndroidManifest.xml", _MANIFEST, True),
        Fixture("SOAP envelope with preserve, CDATA, mixed content", _SOAP, True),
        Fixture("compact XML", '<a><b>x</b><c y="1"/></a>', False),
        Fixture("whitespace only in mixed content, leaf and preserve", '<a>  <b>x</b>  y </a>', False),
        Fixture("HTML-ish text is not well-formed XML", "<p>a<br>b</p>\n  <p>  c  </p>\n", False),
        Fixture("JSON is not XML", '{\n  "a": [1, 2]\n}\n', False),
        Fixture("doctype, PI, attribute with '>' and newline inside a tag", '<?xml version="1.0"?>\n<!DOCTYPE note [\n  <!ELEMENT note (to)>\n]>\n<?php  echo 1;  ?>\n<note\n   id="a>b">\n  <to>  T  </to>\n  <!--  c  -->\n</note>\n', None),
        Fixture("char refs and namespaces", '<r xmlns:p="urn:x">\n  <p:a>&#32;</p:a>\n  <p:b>&#10;  </p:b>\n\n  <p:c xml:space="preserve">\n    <p:d>  </p:d>\n  </p:c>\n</r>\n', None),
        Fixture("non-breaking space is not XML whitespace", "<r>\n \u00a0 <a>x</a>\n</r>\n", None),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #526 Remove unneeded quotes from quote-all CSV. Equivalent: csv.reader yields identical rows and every line terminator
# is untouched. A scanner walks the text field by field and drops the quotes of a field only when its content has no
# comma, quote or line break, no edge whitespace, is not number-like, and is not the only (empty) field of a row.

_NUMLIKE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def csv_unquote(x: str) -> str:
    n, i, out = len(x), 0, []
    while i < n:
        fields = []  # (raw text, content, was quoted)
        while True:
            if i < n and x[i] == '"':
                j, buf = i + 1, []
                while True:
                    k = x.find('"', j)
                    if k < 0:
                        return x  # unterminated quote
                    buf.append(x[j:k])
                    if x[k + 1:k + 2] == '"':
                        buf.append('"')
                        j = k + 2
                    else:
                        j = k + 1
                        break
                if j < n and x[j] not in ",\r\n":
                    return x  # text after the closing quote: not the strict dialect we rewrite
                fields.append((x[i:j], "".join(buf), True))
                i = j
            else:
                j = i
                while j < n and x[j] not in ",\r\n":
                    j += 1
                fields.append((x[i:j], x[i:j], False))
                i = j
            if i < n and x[i] == ",":
                i += 1
                continue
            break
        term = ""
        if i < n:
            term = "\r\n" if x.startswith("\r\n", i) else x[i]
            i += len(term)
        parts = []
        for raw, c, quoted in fields:
            keep = (not quoted or any(ch in c for ch in ',"\r\n') or c != c.strip() or _NUMLIKE.fullmatch(c) is not None
                    or (c == "" and len(fields) == 1))
            parts.append(raw if keep else c)
        out.append(",".join(parts) + term)
    res = "".join(out)
    return res if len(res) < n else x


def _rows(t: str):
    return list(csv.reader(io.StringIO(t, newline="")))


def _csv_equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        ra = _rows(a)
    except csv.Error:
        return False
    try:
        rb = _rows(b)
    except csv.Error:
        return False
    brk = re.compile(r"\r\n|\r|\n")
    return ra == rb and brk.findall(a) == brk.findall(b)


def _quote_all(rows, term: str = "\n") -> str:
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator=term).writerows(rows)
    return buf.getvalue()


def _people(n: int, term: str = "\n") -> str:
    r = _rng(21)
    first = ["Ada", "Grace", "Linus", "Margaret", "Alan", "Edsger"]
    rows = [["id", "name", "email", "city", "zip", "balance", "status", "notes"]]
    for i in range(n):
        rows.append([str(1000 + i), f"{r.choice(first)} {r.choice(['Lovelace', 'Hopper', 'Torvalds'])}", f"user{i}@example.org", r.choice(["Amsterdam", "San Francisco", "New York, NY"]),
                     r.choice(["02134", "1011AB", "94105"]), f"{r.randrange(10000) / 100:.2f}", r.choice(["active", "inactive", "pending"]),
                     r.choice(["", "prefers \"phone\" calls", "line one\nline two", " padded ", "plain note"])])
    return _quote_all(rows, term)


case(
    526,
    claim="Quote-all CSV is re-emitted so only fields that need quotes (comma, quote, line break, edge whitespace, number-like text, a row's only empty field) keep them; csv.reader returns identical rows and line terminators are untouched.",
    loses="the quoted-vs-unquoted distinction, which csv.reader does not expose (including quoted empty strings)",
    encode=csv_unquote,
    equivalent=_csv_equiv,
    fixtures=[
        Fixture("pandas quoting=1 export, 30 rows", _people(30), True),
        Fixture("spreadsheet dump with CRLF", _people(20, "\r\n"), True),
        Fixture("quoted semicolon-delimited export", '"a";"b";"c"\n"1";"x y";"z"\n', False),
        Fixture("already minimal CSV", 'id,name,note\n1,Ada,"say ""hi"", ok"\n2,Bob,"two\nlines"\n3,,plain\n', False),
        Fixture("JSON array is not CSV", '["a","b"]\n', False),
        Fixture("only number-like fields are quoted", 'id,zip\n"1","02134"\n"2","1e5"\n"3","-.5"\n', False),
        Fixture("empty-field edge cases", '""\n"",""\n"a",""\n"",\n,"b"\n"a",\n""""\n"""x"\n', None),
        Fixture("bom, junk after quote, lone CR, trailing comma", '\ufeff"a","b"\r"c"d,"e"\r"f","g",\n', None),
        Fixture("unterminated quote at the end", '"a","b"\n"c","d\n', None),
        Fixture("quote in the middle of an unquoted field", 'a"b,"c"\n"d",e"f"\n', None),
    ],
    min_gain=0.05,
)

# ===============================================================================================================
# A small YAML-subset loader, the primary oracle for #528 and #792 (the stdlib has no YAML). It understands block
# mappings, block sequences (indented or not), `- key: v` items, flow sequences of plain scalars, quoted scalars, plain
# scalar typing, `key: &a` anchors with nested blocks, `key: *a` aliases, `---` documents (anchors are per document) and
# comments. Anything else (block scalars, multi-line scalars, tabs, duplicate keys) raises _YErr, and the encoders treat
# "the loader cannot read this" as "leave it alone". When PyYAML is installed it is consulted as a second opinion.


class _YErr(Exception):
    pass


_Y_INT = re.compile(r"[-+]?(?:0|[1-9]\d*)")
_Y_FLOAT = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")
_Y_KEY = re.compile(r"""("[^"]*"|'[^']*'|[A-Za-z0-9_./$@][^:]*?)\s*:(?:\s+(.*))?$""")


def _yscalar(t: str):
    if t in ("", "~", "null", "Null", "NULL"):
        return None
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if _Y_INT.fullmatch(t):
        return int(t)
    if _Y_FLOAT.fullmatch(t):
        return float(t)
    return t


def _yval(t: str, anchors: dict):
    t = t.strip()
    c = t[:1]
    if c == "*":
        if t[1:] not in anchors:
            raise _YErr("unknown alias")
        return copy.deepcopy(anchors[t[1:]])
    if c in ("|", ">", "&", "!", "%", "@", "`"):
        raise _YErr("unsupported scalar")
    if c == '"':
        try:
            v = json.loads(t)
        except ValueError:
            raise _YErr("double-quoted scalar") from None
        if not isinstance(v, str):
            raise _YErr("double-quoted scalar")
        return v
    if c == "'":
        inner = t[1:-1]
        if len(t) < 2 or not t.endswith("'") or "'" in inner.replace("''", ""):
            raise _YErr("single-quoted scalar")
        return inner.replace("''", "'")
    if c == "[":
        if not t.endswith("]"):
            raise _YErr("flow sequence")
        inner = t[1:-1].strip()
        if inner == "":
            return []
        if any(ch in inner for ch in "[]{}\"'&*!|>") or ": " in inner:
            raise _YErr("flow sequence content")
        parts = [p.strip() for p in inner.split(",")]
        if "" in parts:
            raise _YErr("empty flow item")
        return [_yscalar(p) for p in parts]
    if c == "{":
        if t == "{}":
            return {}
        raise _YErr("flow mapping")
    if t.startswith("- ") or ": " in t or t.endswith(":"):
        raise _YErr("plain scalar")
    return _yscalar(t)


def _ylines(raw_lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for raw in raw_lines:
        if "\t" in raw:
            raise _YErr("tab")
        s = raw.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        content = s.lstrip(" ")
        if " #" in content:
            if '"' in content or "'" in content:
                raise _YErr("comment next to quotes")
            content = content[: content.index(" #")].rstrip()
        out.append((len(s) - len(s.lstrip(" ")), content))
    return out


def _yblock(lines, i, indent, anchors):
    c = lines[i][1]
    return (_yseq if c == "-" or c.startswith("- ") else _ymap)(lines, i, indent, anchors)


def _yseq(lines, i, indent, anchors):
    out = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1] == "-" or lines[i][1].startswith("- ")):
        rest = lines[i][1][1:].lstrip(" ")
        col = indent + (len(lines[i][1]) - len(rest))
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                v, i = _yblock(lines, i + 1, lines[i + 1][0], anchors)
            else:
                v, i = None, i + 1
        elif rest.startswith("&"):
            raise _YErr("anchor on a sequence item")
        elif _Y_KEY.match(rest) or rest.startswith("- "):
            lines[i] = (col, rest)
            v, i = _yblock(lines, i, col, anchors)
        else:
            v = _yval(rest, anchors)
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                raise _YErr("continuation line")
        out.append(v)
    return out, i


def _ymap(lines, i, indent, anchors):
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        m = _Y_KEY.match(lines[i][1])
        if not m:
            raise _YErr("not a mapping line")
        key = _yval(m.group(1), anchors) if m.group(1)[0] in "\"'" else _yscalar(m.group(1))
        if key in out:
            raise _YErr("duplicate key")
        rest, anchor = (m.group(2) or "").strip(), None
        am = re.match(r"&([A-Za-z0-9_-]+)(?:\s+(.*))?$", rest)
        if am:
            anchor, rest = am.group(1), (am.group(2) or "").strip()
        if rest == "":
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and nxt[0] > indent:
                v, i = _yblock(lines, i + 1, nxt[0], anchors)
            elif nxt and nxt[0] == indent and (nxt[1] == "-" or nxt[1].startswith("- ")):
                v, i = _yseq(lines, i + 1, indent, anchors)
            else:
                v, i = None, i + 1
        else:
            v = _yval(rest, anchors)
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                raise _YErr("continuation line")
        if anchor:
            anchors[anchor] = v
        out[key] = v
    if i < len(lines) and lines[i][0] > indent:
        raise _YErr("inconsistent indentation")
    return out, i


def yload_docs(text: str) -> list:
    docs, cur = [], []
    for line in text.split("\n"):
        if line.rstrip() == "---":
            docs.append(cur)
            cur = []
        elif line.startswith(("--- ", "...", "%")):
            raise _YErr("directive or inline document marker")
        else:
            cur.append(line)
    docs.append(cur)
    out = []
    for d in docs:
        lines = _ylines(d)
        if not lines:
            out.append(None)
            continue
        v, i = _yblock(lines, 0, lines[0][0], {})  # anchors are scoped to one document
        if i != len(lines):
            raise _YErr("trailing lines")
        out.append(v)
    return out


def _yaml_equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        la, lb = yload_docs(a), yload_docs(b)
    except _YErr:
        return False  # the encoder may only rewrite what the loader can read, and the result must stay readable
    if la != lb:
        return False
    if _pyyaml is not None:  # second, independent opinion
        try:
            pa = list(_pyyaml.safe_load_all(a))
        except Exception:  # noqa: BLE001
            return True
        try:
            pb = list(_pyyaml.safe_load_all(b))
        except Exception:  # noqa: BLE001
            return False
        return pa == pb
    return True


def _yindent(l: str) -> int:
    return len(l) - len(l.lstrip(" "))


def _yblank(l: str) -> bool:
    return l.strip() == "" or l.lstrip().startswith("#")


# ===============================================================================================================
# #528 Flow-style YAML for scalar-only block sequences: `key:` + `- a` lines -> `key: [a, b]`. Equivalent: the loaded
# documents are equal. Only plain scalars qualify (no `: `, `#`, `,`, brackets, quotes, anchors, colons at all), no comment
# or blank line between items, no continuation lines, and the document must be readable by the loader (so block
# scalars, where `key:` lines are text, are never touched).

_YK = re.compile(r"^( *(?:- +)*)([A-Za-z_][\w.\-/]*):$")
_YI = re.compile(r"^( *)- +(\S.*?) *$")
_YDASH = re.compile(r"^( *)-(?: |$)")


def _flow_plain(s: str) -> bool:
    if not s or s[0] in "&*!|>'\"%@`[]{},#?:" or s == "-" or s.startswith("- "):
        return False
    return not any(c in s for c in ",[]{}:\t") and " #" not in s


def _yseq_at(lines: list[str], i: int, c: int):
    j, items, ind = i + 1, [], None
    while j < len(lines):
        m = _YI.match(lines[j])
        if not m:
            break
        d = len(m.group(1))
        if ind is None:
            if d < c:
                break
            ind = d
        if d != ind:
            break
        items.append(m.group(2))
        j += 1
    if not items or not all(_flow_plain(s) for s in items):
        return None
    k = j
    while k < len(lines) and _yblank(lines[k]):
        k += 1
    if k < len(lines):
        mk = _YDASH.match(lines[k])
        if mk and len(mk.group(1)) == ind:
            return None  # a comment or blank line sits between items
        if not _yblank(lines[j]) and _yindent(lines[j]) > ind:
            return None  # continuation line
    return items, j


def yaml_flow_seq(x: str) -> str:
    try:
        yload_docs(x)
    except _YErr:
        return x
    lines, out, i = x.split("\n"), [], 0
    while i < len(lines):
        m = _YK.match(lines[i])
        got = _yseq_at(lines, i, len(m.group(1))) if m else None
        if got:
            out.append(f"{m.group(1)}{m.group(2)}: [{', '.join(got[0])}]")
            i = got[1]
        else:
            out.append(lines[i])
            i += 1
    res = "\n".join(out)
    return res if len(res) < len(x) else x


_POD_YAML = """apiVersion: v1
kind: Pod
metadata:
  name: prometheus-0
  namespace: monitoring
spec:
  containers:
  - args:
    - --config.file=/etc/prometheus/prometheus.yml
    - --storage.tsdb.path=/prometheus
    - --storage.tsdb.retention.time=15d
    - --web.console.libraries=/usr/share/prometheus/console_libraries
    - --web.console.templates=/usr/share/prometheus/consoles
    - --web.enable-lifecycle
    - --log.level=info
    - --query.max-concurrency=20
    - --query.timeout=2m
    - -v
    command:
    - /bin/prometheus
    image: prom/prometheus:v2.45.0
    name: prometheus
    ports:
    - containerPort: 9090
      protocol: TCP
    volumeMounts:
    - mountPath: /etc/prometheus
      name: config
  nodeSelector:
    kubernetes.io/os: linux
  tolerations:
  - effect: NoSchedule
    key: node-role
status:
  conditions:
  - status: "True"
    type: Ready
  podIPs:
  - ip: 10.1.2.3
"""
_COMPOSE_NEG = """services:
  web:
    image: nginx:1.25
    ports:
    - "80:80"
    volumes:
    - ./data:/var/lib/data
    - ./conf:/etc/conf:ro
    command:
    - sh
    # keep this note
    - -c
    environment:
    - KEY=a b
    - 'QUOTED=1'
    cap_add:
    - NET_ADMIN, SYS_TIME
    entrypoint:
    - run   # trailing comment
    - now
"""

case(
    528,
    claim="Block sequences whose items are all plain scalars become `key: [a, b, c]`; sequences of mappings, quoted or colon-bearing items, comment-interleaved lists, continuation lines and anything inside block scalars are left alone, and the loaded document is unchanged.",
    loses="block layout and per-item line structure of the rewritten sequences",
    encode=yaml_flow_seq,
    equivalent=_yaml_equiv,
    fixtures=[
        Fixture("kubectl get pod -o yaml with 10 args", _POD_YAML, True),
        Fixture("same pod twice as a multi-document stream", _POD_YAML + "---\n" + _POD_YAML.replace("prometheus-0", "prometheus-1"), True),
        Fixture("docker-compose lists that must stay block", _COMPOSE_NEG, False),
        Fixture("sequence of mappings only", "items:\n- name: a\n  port: 1\n- name: b\n  port: 2\n", False),
        Fixture("JSON text", '{"args": ["a", "b"]}\n', False),
        Fixture("list inside a block scalar is text", "data:\n  script: |\n    run:\n    - a\n    - b\n  other:\n  - x\n  - y\n", None),
        Fixture("scalars that resolve to non-strings and odd items", "a:\n- null\n- ~\n- true\n- 1\n- 2.5\n- yes\n- -x\n- --long=1\n- a&b\n- 'q'\nb:\n  - one   \n  - two\nc:\n  -   spaced\n  - last\n", None),
        Fixture("multi-line plain item and nested lists", "a:\n- first\n  continued here\n- second\nb:\n- - x\n  - y\nc:\n- one\n\n- two\n", None),
        Fixture("indentless and nested key lines", "- name: x\n  args:\n  - a\n  - b\n  env:\n    list:\n    - c\n    - d\n", None),
    ],
    min_gain=0.05,
)

# ===============================================================================================================
# #792 YAML anchors for repeated mapping blocks within one document: `labels:` + identical nested lines (3+ times) ->
# `labels: &a1` once and `labels: *a1` after. Equivalent: loaded documents equal. Anchors never cross `---`; documents that
# already use anchors or aliases are skipped; a block must be free of comments and blank lines to qualify.

_YKO = re.compile(r"^( *)([A-Za-z0-9_./-]+):$")


def _anchor_doc(seg: list[str]) -> list[str]:
    if "&" in "\n".join(seg) or "*" in "\n".join(seg):
        return seg
    n, cands = len(seg), []
    for i, l in enumerate(seg):
        m = _YKO.match(l)
        if not m:
            continue
        ind, j, ok = len(m.group(1)), i + 1, True
        while j < n:
            if _yblank(seg[j]):
                k = j
                while k < n and _yblank(seg[k]):
                    k += 1
                ok = not (k < n and _yindent(seg[k]) > ind)
                break
            if _yindent(seg[j]) > ind:
                j += 1
            else:
                break
        if ok and j > i + 1:
            cands.append((i, j, m.group(2), m.group(1)))
    groups: dict[tuple, list] = {}
    for i, j, key, pad in cands:
        groups.setdefault((key, tuple(seg[t][len(pad):] for t in range(i + 1, j))), []).append((i, j, key, pad))
    order = sorted(groups.items(), key=lambda kv: -sum(len(seg[t]) + 1 for t in range(kv[1][0][0] + 1, kv[1][0][1])) * (len(kv[1]) - 1))
    claimed: set[int] = set()
    repl: dict[int, str] = {}
    drop: set[int] = set()
    count = 0
    for _sig, occs in order:
        free = [o for o in occs if not any(t in claimed for t in range(o[0], o[1]))]
        if len(free) < 3:
            continue
        count += 1
        for idx, (i, j, key, pad) in enumerate(free):
            claimed.update(range(i, j))
            repl[i] = f"{pad}{key}: {'&' if idx == 0 else '*'}a{count}"
            if idx:
                drop.update(range(i + 1, j))
    return [repl.get(t, l) for t, l in enumerate(seg) if t not in drop]


def yaml_anchors(x: str) -> str:
    try:
        yload_docs(x)
    except _YErr:
        return x
    lines = x.split("\n")
    bounds = [-1] + [i for i, l in enumerate(lines) if l.rstrip() == "---"] + [len(lines)]
    out: list[str] = []
    for a, b in zip(bounds, bounds[1:]):
        out.extend(_anchor_doc(lines[a + 1:b]))
        if b < len(lines):
            out.append(lines[b])
    res = "\n".join(out)
    return res if len(res) < len(x) else x


def _k8s_list(n: int, tail: str = "") -> str:
    labels = {
        "web": "      app: web\n      app.kubernetes.io/managed-by: Helm\n      pod-template-hash: 7d9f8b\n      tier: frontend\n      version: v1\n",
        "api": "      app: api\n      app.kubernetes.io/managed-by: Helm\n      pod-template-hash: 5c6d7e\n      tier: backend\n      version: v2\n",
    }
    ann = '      kubectl.kubernetes.io/restartedAt: "2025-03-01T10:00:00Z"\n      prometheus.io/port: "9102"\n      prometheus.io/scrape: "true"\n'
    items = ""
    for i in range(n):
        app = "web" if i % 3 == 0 else "api" if i % 3 == 1 else f"job-{i}"
        lab = labels.get(app, f"      app: {app}\n      batch: nightly-{i}\n")
        items += f"""- apiVersion: v1
  kind: Pod
  metadata:
    annotations:
{ann}    labels:
{lab}    name: {app}-{i:02d}
    namespace: prod
  spec:
    containers:
    - image: nginx:1.25
      name: {app}
"""
    return "apiVersion: v1\nitems:\n" + items + "kind: List\nmetadata:\n  resourceVersion: \"\"\n" + tail


def _doc_with(block: str, times: int, tag: str) -> str:
    return "".join(f"svc-{tag}{k}:\n  meta:\n    labels:\n{block}  name: n{k}\n" for k in range(times))


_BLK = "      app: web\n      tier: frontend\n      env: production\n      team: platform\n"
_BLK_IN = "    app: web\n    tier: frontend\n    env: production\n    team: platform\n"

case(
    792,
    claim="A mapping block that appears 3+ times under the same key in one YAML document is written once with an anchor and aliased afterwards; anchors never cross `---`, and the loaded documents are equal.",
    loses="the repeated text of the aliased blocks (the loaded value is identical)",
    encode=yaml_anchors,
    equivalent=_yaml_equiv,
    fixtures=[
        Fixture("kubectl get pods -o yaml List, 12 items", _k8s_list(12), True),
        Fixture("three documents each repeating a block 3 times", "---\n" + _doc_with(_BLK_IN, 3, "a") + "---\n" + _doc_with(_BLK_IN, 3, "b") + "---\n" + _doc_with(_BLK_IN, 3, "c"), True),
        Fixture("block seen only twice", _k8s_list(2), False),
        Fixture("one block per document: anchors cannot cross ---", "---\n" + "---\n".join(_doc_with(_BLK_IN, 1, t) for t in "abc"), False),
        Fixture("document already using an anchor and alias", "x: &a\n  k: 1\ny: *a\n" + _doc_with(_BLK_IN, 3, "q"), False),
        Fixture("JSON text", '{"labels": {"a": 1}}\n', False),
        Fixture("comment inside a repeated block, block scalar, tab", "a:\n  labels:\n    x: 1\n    # note\n    y: 2\nb:\n  labels:\n    x: 1\n    # note\n    y: 2\nc:\n  labels:\n    x: 1\n    # note\n    y: 2\nd:\n  labels:\n    s: |\n      text\n", None),
        Fixture("nested repeated blocks overlap", "".join(f"p{k}:\n  spec:\n    labels:\n      a: 1\n      b: 2\n    other: 3\n  q:\n    labels:\n      a: 1\n      b: 2\n" for k in range(4)), None),
        Fixture("different indentation depth, same relative block", "a:\n  labels:\n    x: 1\n    y: 2\nb:\n  c:\n    labels:\n      x: 1\n      y: 2\nd:\n  labels:\n    x: 1\n    y: 2\ne:\n  labels:\n    x: 1\n    z: 2\n", None),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #534 RFC 6902-style delta for re-fetched same-id JSON entities (sim). A ledger keyed by (server, tool, id) renders a
# re-fetch as a patch against the previous state. The reader view applies those patches, starting from a full copy that is
# still in the model's context, and must land on exactly the entity the server returned.

_D_MARK, _D_ESC = "[ctxprune delta vs call ", "[ctxprune raw]\n"


def _jcanon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ptr(path: str, k) -> str:
    return path + "/" + str(k).replace("~", "~0").replace("/", "~1")


def patch_diff(a, b, path: str = "") -> list[dict]:
    if isinstance(a, dict) and isinstance(b, dict):
        ops = [{"op": "remove", "path": _ptr(path, k)} for k in a if k not in b]
        for k, v in b.items():
            ops += patch_diff(a[k], v, _ptr(path, k)) if k in a else [{"op": "add", "path": _ptr(path, k), "value": v}]
        return ops
    if isinstance(a, list) and isinstance(b, list):
        if len(a) == len(b):
            return [op for i in range(len(a)) for op in patch_diff(a[i], b[i], _ptr(path, i))]
        if len(b) > len(a) and _jcanon(b[:len(a)]) == _jcanon(a):
            return [{"op": "add", "path": _ptr(path, i), "value": b[i]} for i in range(len(a), len(b))]
    return [] if _jcanon(a) == _jcanon(b) else [{"op": "replace", "path": path, "value": b}]


def patch_apply(doc, ops: list[dict]):
    doc = copy.deepcopy(doc)
    for op in ops:
        toks = [t.replace("~1", "/").replace("~0", "~") for t in op["path"].split("/")[1:]] if op["path"] else []
        if not toks:
            doc = copy.deepcopy(op["value"])
            continue
        parent = doc
        for t in toks[:-1]:
            parent = parent[int(t)] if isinstance(parent, list) else parent[t]
        last = toks[-1]
        if isinstance(parent, list):
            i = int(last)
            if op["op"] == "add":
                parent.insert(i, copy.deepcopy(op["value"]))
            elif op["op"] == "replace":
                parent[i] = copy.deepcopy(op["value"])
            else:
                del parent[i]
        elif op["op"] == "remove":
            del parent[last]
        else:
            parent[last] = copy.deepcopy(op["value"])
    return doc


class EntityLedger:
    """Per-session state kept by the hook: last rendered state per entity, which calls the model can still see."""

    def __init__(self, min_saving: float = 0.2):
        self.last: dict[tuple, tuple[int, object]] = {}
        self.base: dict[int, int | None] = {}  # call -> call it is a delta of (None: full copy)
        self.visible: set[int] = set()
        self.min_saving = min_saving

    def compacted(self, dropped: set[int]) -> None:
        self.visible -= dropped

    def _chain_visible(self, call: int) -> bool:
        while call is not None:
            if call not in self.visible:
                return False
            call = self.base[call]
        return True

    def render(self, server: str, tool: str, text: str, call: int) -> str:
        out = text
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict) or "id" not in obj:
                raise ValueError("not an entity")
            key, prev, base = (server, tool, _jcanon(obj["id"])), None, None
            prev = self.last.get(key)
            if prev is not None and self._chain_visible(prev[0]):
                ops = patch_diff(prev[1], obj)
                same = sum(1 for k in obj if k in prev[1] and _jcanon(obj[k]) == _jcanon(prev[1][k]))
                cand = f"{_D_MARK}{prev[0]} | {server} {tool}]" + "".join("\n" + json.dumps(o, separators=(",", ":"), ensure_ascii=False) for o in ops) + f"\n[{same} fields unchanged]"
                if len(cand) <= len(text) * (1 - self.min_saving):
                    out, base = cand, prev[0]
            self.last[key] = (call, obj)
            self.base[call] = base
        except Exception:  # noqa: BLE001  fail open: whatever went wrong, the model gets the original output
            out = text
            self.base[call] = None
        if out is text and text.startswith("[ctxprune"):
            out = _D_ESC + text  # tool output that looks like our marker must not be read as a delta
        self.visible.add(call)
        return out


def reader_entity(seen: dict[int, str], call: int):
    """What the model can reconstruct from the transcript alone (a KeyError means: it cannot)."""
    t = seen[call]
    if t.startswith(_D_ESC):
        return t[len(_D_ESC):]
    if not t.startswith(_D_MARK):
        return json.loads(t)
    head, *rest = t.split("\n")
    base = int(re.match(re.escape(_D_MARK) + r"(\d+) ", head).group(1))
    ops = [json.loads(l) for l in rest if l.startswith("{")]
    return patch_apply(reader_entity(seen, base), ops)


def _issue(**ch) -> dict:
    d = {"id": 1234, "number": 87, "title": "Flaky test in the scheduler", "state": "open", "locked": False, "comments": 3, "updated_at": "2025-03-01T10:00:00Z",
         "user": {"login": "octocat", "id": 583231, "type": "User", "site_admin": False},
         "labels": [{"id": 1, "name": "bug", "color": "d73a4a"}, {"id": 2, "name": "p1", "color": "b60205"}],
         "assignees": [], "milestone": None, "body": "Steps to reproduce:\n" + "1. run the suite twice and watch test_sched fail intermittently\n" * 12,
         "reactions": {"+1": 2, "-1": 0, "heart": 1}, "url": "https://api.github.com/repos/o/r/issues/87", "node_id": "I_kwDOA"}
    d.update(ch)
    return d


def sim_entity_delta() -> dict:
    L = EntityLedger()
    seen: dict[int, str] = {}
    truth: dict[int, dict] = {}
    sent = full = 0

    def fetch(server, tool, obj, call, raw=None):
        nonlocal sent, full
        text = raw if raw is not None else json.dumps(obj, indent=2)
        out = L.render(server, tool, text, call)
        seen[call], truth[call] = out, obj
        sent += len(out)
        full += len(text)
        return out, text

    states = [
        _issue(),                                                                    # 1 first fetch: full
        _issue(comments=4, updated_at="2025-03-01T10:05:00Z"),                       # 2 a comment arrived
        _issue(comments=4, updated_at="2025-03-02T08:00:00Z", state="closed",
               labels=[{"id": 1, "name": "bug", "color": "d73a4a"}, {"id": 2, "name": "p1", "color": "b60205"}, {"id": 3, "name": "fixed", "color": "0e8a16"}],
               assignees=[{"login": "hubot"}]),                                      # 3 closed, label appended, assignee added
        _issue(comments=4, updated_at="2025-03-02T08:00:00Z", state="closed",
               labels=[{"id": 1, "name": "bug", "color": "d73a4a"}, {"id": 2, "name": "p1", "color": "b60205"}, {"id": 3, "name": "fixed", "color": "0e8a16"}],
               assignees=[{"login": "hubot"}]),                                      # 4 nothing changed
        _issue(comments=5, updated_at="2025-03-03T08:00:00Z", state="open", milestone={"title": "v2"}, reactions={"+1": 3, "heart": 1},
               labels=[{"id": 1, "name": "bug", "color": "d73a4a"}]),                # 5 reopened, label removed (list shrinks), key removed, null -> object
    ]
    for n, st in enumerate(states, 1):
        fetch("gh", "issue_read", st, n)
    assert seen[1].startswith("{"), "the first fetch is the full object"
    for n in range(2, 6):
        assert seen[n].startswith(_D_MARK), f"call {n} should be a delta"
    assert seen[4].count("\n") == 1 and "fields unchanged" in seen[4], "an unchanged re-fetch is a one-line note"
    for n in range(1, 6):  # (b) reader view: the entity rebuilt from the transcript is exactly what the server returned
        assert _jcanon(reader_entity(seen, n)) == _jcanon(truth[n]), f"reader cannot rebuild call {n}"
    chain_sent, chain_full = sent, full
    assert chain_sent < 0.45 * chain_full, f"expected >55% saved on a 5-call poll, got {chain_sent}/{chain_full}"

    # (c) must NOT apply: another id, another tool, another server, a delta that is not smaller
    other = _issue(id=1235, number=88)
    o, t = fetch("gh", "issue_read", other, 6)
    assert o == t, "different id: full"
    o, t = fetch("gh", "pull_request_read", _issue(), 7)
    assert o == t, "same id, different tool: full"
    o, t = fetch("gh2", "issue_read", _issue(), 8)
    assert o == t, "same id, different server: full"
    rewrite = {"id": 1234, "title": "x" * 40, "state": "locked-down", "z": list(range(30)), "body": "completely different " * 30}
    o, t = fetch("gh", "issue_read", rewrite, 9)
    assert o == t and "ctxprune delta" not in o, "a patch that saves less than 20% is not worth it: full"
    o, t = fetch("gh", "issue_read", {**rewrite, "state": "open"}, 10)
    assert o.startswith(_D_MARK) and _jcanon(reader_entity(seen, 10)) == _jcanon({**rewrite, "state": "open"})
    # type changes and JSON-pointer escapes survive the round trip
    tricky1, tricky2 = {"id": 77, "a/b": 1, "c~d": True, "n": 1, "pad": "p" * 300}, {"id": 77, "a/b": 2, "c~d": 1, "n": 1.0, "pad": "p" * 300, "new": [1]}
    fetch("gh", "t", tricky1, 11)
    fetch("gh", "t", tricky2, 12)
    assert seen[12].startswith(_D_MARK) and _jcanon(reader_entity(seen, 12)) == _jcanon(tricky2), "True vs 1 and 1 vs 1.0 are different JSON"

    # compaction drops call 1 from the model's context: older deltas become unreadable, so the next fetch must be full
    for gone in (1,):
        del seen[gone]
    L.compacted({1})
    for n in (2, 3, 4, 5):
        try:
            reader_entity(seen, n)
            raise AssertionError("a delta whose base was compacted away should be unreadable")
        except KeyError:
            pass
    o, t = fetch("gh", "issue_read", _issue(comments=6), 13)
    assert o == t, "base compacted away: send the full object again"
    o, t = fetch("gh", "issue_read", _issue(comments=7), 14)
    assert o.startswith(_D_MARK) and _jcanon(reader_entity(seen, 14)) == _jcanon(_issue(comments=7)), "deltas resume on the new full copy"

    # (d) fail open
    for text in ["plain text output", "[1,2,3]", '{"no_id": 1}', "", "{broken json", '{"id": 5, "a": [1,'] + ["[ctxprune delta vs call 1 | x]\nnot really"]:
        out = L.render("gh", "t2", text, 100 + len(seen))
        seen[100 + len(seen)] = out
        assert out == text or (text.startswith("[ctxprune") and out == _D_ESC + text), f"pass-through failed for {text!r}"
    assert reader_entity({1: _D_ESC + "[ctxprune delta vs call 1 | x]\nnot really"}, 1) == "[ctxprune delta vs call 1 | x]\nnot really"
    boom = EntityLedger()
    boom.last = type("Broken", (dict,), {"get": lambda *a: (_ for _ in ()).throw(RuntimeError("store down"))})()
    txt = json.dumps(_issue())
    assert boom.render("gh", "issue_read", txt, 1) == txt and boom.render("gh", "issue_read", txt, 2) == txt, "a broken ledger fails open"
    return {"chars_full": full, "chars_sent": sent, "poll_chars_full": chain_full, "poll_chars_sent": chain_sent, "poll_saved_pct": round(100 * (1 - chain_sent / chain_full))}


case(
    534,
    claim="A re-fetch of the same (server, tool, id) JSON entity is rendered as RFC 6902-style ops plus a count of unchanged fields against the previous state; the model-side rebuild equals the server's object, and different ids, tools, servers, unprofitable patches or a compacted-away base get the full object.",
    sim=sim_entity_delta,
)

# ===============================================================================================================
# #639 Cross-call key factoring for repeated singleton MCP objects (sim). After a full object for (server, tool) is in the
# model's context, a later object with exactly the same key list is shown as `#same-keys call=N [values...]`. The reader
# view zips the values with the keys of call N; if call N was compacted away the next result is sent in full.

_K_MARK, _K_ESC = "#same-keys call=", "#raw\n"


def _no_dup_keys(pairs):
    d = dict(pairs)
    if len(d) != len(pairs):
        raise ValueError("duplicate keys")
    return d


class KeyLedger:
    def __init__(self):
        self.refs: dict[tuple, list[tuple[int, tuple]]] = {}
        self.visible: set[int] = set()

    def compacted(self, dropped: set[int]) -> None:
        self.visible -= dropped

    def render(self, server: str, tool: str, text: str, call: int) -> str:
        out = text
        try:
            obj = json.loads(text, object_pairs_hook=_no_dup_keys)  # duplicate keys anywhere: leave the text alone
            if not isinstance(obj, dict) or not obj:
                raise ValueError("not a singleton object")
            keys = tuple(obj)
            ref = next((c for c, k in self.refs.get((server, tool), []) if k == keys and c in self.visible), None)
            if ref is not None:
                cand = f"{_K_MARK}{ref} " + json.dumps(list(obj.values()), separators=(",", ":"), ensure_ascii=False)
                if len(cand) < len(text):
                    out = cand
            if out is text:
                self.refs.setdefault((server, tool), []).append((call, keys))
        except Exception:  # noqa: BLE001  fail open
            out = text
        if out is text and text.startswith(("#same-keys", "#raw")):
            out = _K_ESC + text
        self.visible.add(call)
        return out


def reader_object(seen: dict[int, str], call: int):
    t = seen[call]
    if t.startswith(_K_ESC):
        return t[len(_K_ESC):]
    if not t.startswith(_K_MARK):
        return json.loads(t)
    ref, _, vals = t[len(_K_MARK):].partition(" ")
    return dict(zip(json.loads(seen[int(ref)]), json.loads(vals)))  # KeyError if call `ref` is gone


def _label(i: int, **ch) -> dict:
    d = {"id": 9000 + i, "node_id": f"LA_kwDOAbc{i:04d}", "url": f"https://api.github.com/repos/o/r/labels/l{i}", "name": f"label-{i}",
         "color": "d73a4a", "default": i % 2 == 0, "description": "Something isn't working"}
    d.update(ch)
    return d


def sim_key_factoring() -> dict:
    L = KeyLedger()
    seen: dict[int, str] = {}
    truth: dict[int, object] = {}
    sent = full = 0

    def call(server, tool, obj, n, raw=None):
        nonlocal sent, full
        text = raw if raw is not None else json.dumps(obj)
        out = L.render(server, tool, text, n)
        try:
            truth[n] = json.loads(text)
        except ValueError:
            truth[n] = text
        seen[n] = out
        sent += len(out)
        full += len(text)
        return out, text

    for n in range(1, 13):  # twelve get_label results
        call("gh", "get_label", _label(n), n)
    assert seen[1].startswith("{") and all(seen[n].startswith(_K_MARK) for n in range(2, 13)), "call 1 full, calls 2+ values only"
    for n in range(1, 13):  # (b) reader QA: every call rebuilds to the identical key -> value mapping, key order included
        got = reader_object(seen, n)
        assert got == truth[n] and list(got) == list(truth[n]), f"call {n} not rebuilt"
    key_chars = sum(len(json.dumps(k)) + 2 for k in _label(1))
    assert sent < 0.75 * full, f"expected at least 25% saved on repeated singleton objects, got {sent}/{full}"
    labels_sent, labels_full = sent, full

    # (c) must NOT apply
    o, t = call("gh", "get_label", {k: v for k, v in _label(20).items() if k != "color"}, 20)
    assert o == t, "different key list: full"
    o, t = call("gh", "get_label", dict(reversed(list(_label(21).items()))), 21)
    assert o == t, "same keys in a different order: full"
    o, t = call("gh", "other_tool", _label(22), 22)
    assert o == t, "a different tool has its own references: full"
    o, t = call("gh2", "get_label", _label(23), 23)
    assert o == t, "a different server has its own references: full"
    o, t = call("gh", "get_label", None, 24, raw='{"id":1,"id":2,"name":"dup"}')
    assert o == t, "duplicate keys cannot be factored"
    o, t = call("gh", "list_labels", None, 25, raw=json.dumps([_label(1), _label(2)]))
    assert o == t, "arrays are not singleton objects"
    o, t = call("gh", "get_label", None, 26, raw="{}")
    assert o == t, "empty object"
    o, t = call("gh", "get_label", {"id": 1}, 27)  # a values-only form that is not shorter than the original is not used
    assert o == t
    nested = {**_label(30), "owner": {"login": "octocat", "id": 1}}
    call("gh", "get_label", nested, 31)
    o, _ = call("gh", "get_label", {**_label(32), "owner": {"login": "hubot", "id": 2}}, 32)
    assert o.startswith(_K_MARK) and '"login"' in o, "nested keys inside values are kept; only the top-level key list is factored"
    assert reader_object(seen, 32) == {**_label(32), "owner": {"login": "hubot", "id": 2}}
    # text that looks like our marker is escaped, and the reader gives it back untouched
    for txt in ["#same-keys call=1 [1,2]", "#raw\nhello"]:
        o, t = call("gh", "plain", None, 40 + len(txt), raw=txt)
        assert o == _K_ESC + txt and reader_object(seen, 40 + len(txt)) == txt

    # compaction drops call 1 (the reference): later results must carry the keys again
    base_calls = set(range(1, 13))
    for n in base_calls:
        seen.pop(n, None)
    L.compacted(base_calls)
    o, t = call("gh", "get_label", _label(50), 50)
    assert o == t, "reference compacted away: the full object again"
    o, t = call("gh", "get_label", _label(51), 51)
    assert o.startswith(f"{_K_MARK}50 ") and reader_object(seen, 51) == _label(51), "factoring resumes against the new reference"
    for n in (2, 3):
        try:
            reader_object({n: "#same-keys call=1 [1]"}, n)
            raise AssertionError("expected KeyError for a missing reference")
        except KeyError:
            pass

    # (d) fail open
    for txt in ["", "not json", '{"a": [1,', "42", "null"]:
        o, t = call("gh", "get_label", None, 60 + len(txt), raw=txt)
        assert o == t
    boom = KeyLedger()
    boom.refs = type("Broken", (dict,), {"get": lambda *a: (_ for _ in ()).throw(RuntimeError("store down"))})()
    txt = json.dumps(_label(1))
    assert boom.render("gh", "get_label", txt, 1) == txt and boom.render("gh", "get_label", txt, 2) == txt
    return {"label_chars_full": labels_full, "label_chars_sent": labels_sent, "key_name_chars_per_object": key_chars}


case(
    639,
    claim="Once a full singleton object for (server, tool) is visible, later objects with the identical key list are shown as `#same-keys call=N` plus a values array; the reader's rebuild equals the original key-to-value mapping (order included), and other key lists, tools, servers, duplicate keys, arrays, unprofitable cases and a compacted-away reference get the full object.",
    sim=sim_key_factoring,
)

# ===============================================================================================================
# #619 Decode Python bytes-repr \xNN UTF-8 escape runs. Roundtrip, inside `b'...'` / `b"..."` literals only: lowercase
# `\xNN` runs are decoded greedily to printable non-ASCII characters; stray or truncated bytes, ASCII escapes (\x00, \x1b),
# `\n`, and an escaped backslash followed by x.. stay as written. `§b\n` marks an encoded text (the decoder re-escapes every
# non-ASCII character inside a bytes literal), so a literal that already holds non-ASCII text is declined. A `\xc3\xa9` in a
# str repr (no b prefix) is mojibake, not UTF-8, and is never decoded.

_B_HDR, _B_RAW = "§b\n", "§raw\n"
_B_START = re.compile(r"(?<![A-Za-z0-9_])b(['\"])")
_B_RUN = re.compile(r"((?:\\x[0-9a-f]{2})+)|\\.", re.S)


def _b_literals(t: str):
    pos, n = 0, len(t)
    while True:
        m = _B_START.search(t, pos)
        if not m:
            return
        q, i = m.group(1), m.end()
        while i < n and t[i] != q and t[i] != "\n":
            i += 2 if t[i] == "\\" else 1
        if i < n and t[i] == q:
            yield m.end(), i
            pos = i + 1
        else:
            pos = m.end()


def _decode_run(run: str) -> str:
    bs = bytes(int(h, 16) for h in re.findall(r"\\x([0-9a-f]{2})", run))
    out, i = [], 0
    while i < len(bs):
        b = bs[i]
        n = 2 if 0xC2 <= b <= 0xDF else 3 if 0xE0 <= b <= 0xEF else 4 if 0xF0 <= b <= 0xF4 else 0
        ch = None
        if n and i + n <= len(bs):
            try:
                ch = bs[i:i + n].decode("utf-8")
            except UnicodeDecodeError:
                ch = None
        if ch is not None and ch.isprintable():
            out.append(ch)
            i += n
        else:
            out.append(f"\\x{b:02x}")
            i += 1
    return "".join(out)


def _b_rewrite(t: str, fn) -> str:
    out, last = [], 0
    for s, e in _b_literals(t):
        out += [t[last:s], fn(t[s:e])]
        last = e
    return "".join(out) + t[last:]


def bytes_repr_encode(x: str) -> str:
    esc = _B_RAW + x if x.startswith((_B_HDR, _B_RAW)) else x
    if any(ord(c) > 127 for s, e in _b_literals(x) for c in x[s:e]):
        return esc  # non-ASCII text inside a bytes literal is not a bytes repr; decode could not tell it from our output
    out = _b_rewrite(x, lambda c: _B_RUN.sub(lambda m: _decode_run(m.group(1)) if m.group(1) else m.group(0), c))
    if out == x:
        return esc
    out = _B_HDR + out
    return out if len(out) < len(x) else esc


def bytes_repr_decode(e: str) -> str:
    if e.startswith(_B_RAW):
        return e[len(_B_RAW):]
    if not e.startswith(_B_HDR):
        return e
    return _b_rewrite(e[len(_B_HDR):], lambda c: re.sub(r"[^\x00-\x7f]", lambda m: "".join(f"\\x{b:02x}" for b in m.group().encode("utf-8")), c))


_JP = "東京 Café Renée 日本語のテスト 🎉 naïve"
_CALLED = ("Traceback (most recent call last):\n  File \"deploy.py\", line 42, in <module>\n    subprocess.run(['curl', '-s', url], check=True, capture_output=True)\n"
           "subprocess.CalledProcessError: Command '['curl', '-s', 'http://api/city']' returned non-zero exit status 22.\n"
           "stdout: " + repr(('{"city":"東京","name":"Café Renée","note":"日本語のテスト 🎉"}\n').encode()) + "\nstderr: " + repr(b"curl: (22) The requested URL returned error: 404\n") + "\n")
_SOCK = "".join(f"recv({n}) -> {repr(_JP.encode()[i:i + n])}\n" for n, i in [(8, 0), (8, 8), (8, 16), (8, 24), (8, 32), (8, 40)])

case(
    619,
    claim="Inside Python bytes literals, runs of lowercase \\xNN escapes that form valid UTF-8 for printable characters are replaced by those characters (with a `§b` marker); the decoder re-escapes them to the exact original text; truncated or invalid bytes, ASCII escapes, \\n, str mojibake and literals that already contain non-ASCII text are never decoded.",
    encode=bytes_repr_encode,
    decode=bytes_repr_decode,
    fixtures=[
        Fixture("CalledProcessError with a CJK and accented JSON body", _CALLED, True),
        Fixture("socket recv chunks cut in the middle of characters", _SOCK, True),
        Fixture("double-quoted bytes with escaped quote and an escaped backslash", "print(b\"it\\'s \\xc3\\xa9 \\\\xc3\\\\xa9 \\xe6\\x97\\xa5\\n\")\n", True),
        Fixture("str mojibake repr is not UTF-8 bytes", "'\\xc3\\xa9' and '\\xe6\\x97\\xa5'\n", False),
        Fixture("ASCII-only bytes with control escapes", "b'hello\\n\\x00\\x1b[0m\\r\\t'\n", False),
        Fixture("invalid UTF-8 and C1 controls and NBSP stay escaped", "b'\\xff\\xfe\\x80' b'\\xc2\\x85' b'\\xc2\\xa0' b'\\xc3'\n", False),
        Fixture("uppercase hex is not Python's bytes repr", "b'\\xE6\\x97\\xA5'\n", False),
        Fixture("input that starts like encoder output", "§b\nb'\\xe6\\x97\\xa5'\n", None),
        Fixture("input that starts with the raw escape", "§raw\nb'\\xe6\\x97\\xa5'\n", None),
        Fixture("non-ASCII text already inside a bytes literal", "b'日本語 \\xe6\\x97\\xa5' and b'\\xe6\\x97\\xa5'\n", None),
        Fixture("b' that is not a literal start, unterminated literal", "verb'\\xe6\\x97\\xa5' b'\\xe6\\x97\\xa5 unterminated\nb'\\xe6\\x97\\xa5'\n", None),
        Fixture("4-byte emoji, truncated emoji, overlong encoding", "b'\\xf0\\x9f\\x8e\\x89' b'\\xf0\\x9f\\x8e' b'\\xc0\\xaf' b'\\xed\\xa0\\x80'\n", None),
    ],
    min_gain=0.2,
    idempotent=False,
)

# ===============================================================================================================
# #766 Header-once table for CSV-loader row documents. Byte-exact roundtrip. Retrieved documents are `column: value` lines
# separated by one blank line; a run of 2+ consecutive documents with the same ordered column list becomes
# `§t<TAB>col<TAB>col...` plus one tab-separated value line per document. Documents with tabs in them, lines without `: `,
# one column or a different column list are kept as they are. `§!` escapes a block that starts with the sigil.

_T_HDR, _T_ESC = "§t\t", "§!"


def _t_parse(block: str):
    cols, vals = [], []
    for l in block.split("\n"):
        k, sep, v = l.partition(": ")
        if not sep or not k or "\t" in l:
            return None
        cols.append(k)
        vals.append(v)
    return (cols, vals) if len(cols) >= 2 else None


def _t_esc(b: str) -> str:
    return _T_ESC + b if b.startswith("§") else b


def csvdocs_encode(x: str) -> str:
    tail = "\n" if x.endswith("\n") else ""
    blocks = x[: len(x) - len(tail)].split("\n\n")
    parsed = [_t_parse(b) for b in blocks]
    out, i = [], 0
    while i < len(blocks):
        p, j = parsed[i], i + 1
        if p:
            while j < len(blocks) and parsed[j] and parsed[j][0] == p[0]:
                j += 1
            if j - i >= 2:
                table = _T_HDR + "\t".join(p[0]) + "\n" + "\n".join("\t".join(parsed[t][1]) for t in range(i, j))
                if len(table) < len("\n\n".join(blocks[i:j])):
                    out.append(table)
                    i = j
                    continue
        out.append(_t_esc(blocks[i]))
        i += 1
    res = "\n\n".join(out) + tail
    return res if len(res) < len(x) else "\n\n".join(_t_esc(b) for b in blocks) + tail


def csvdocs_decode(e: str) -> str:
    tail = "\n" if e.endswith("\n") else ""
    out = []
    for b in e[: len(e) - len(tail)].split("\n\n"):
        if b.startswith(_T_ESC):
            out.append(b[len(_T_ESC):])
        elif b.startswith(_T_HDR):
            rows = b.split("\n")
            cols = rows[0][len(_T_HDR):].split("\t")
            out.append("\n\n".join("\n".join(f"{c}: {v}" for c, v in zip(cols, r.split("\t"))) for r in rows[1:]))
        else:
            out.append(b)
    return "\n\n".join(out) + tail


def _csv_docs(n: int, seed: int = 4) -> str:
    r = _rng(seed)
    cols = ["id", "first_name", "last_name", "email", "country", "signup_date", "plan", "monthly_spend"]
    docs = []
    for i in range(n):
        v = [str(100 + i), r.choice(["Ada", "Grace", "Linus", "Margaret"]), r.choice(["Lovelace", "Hopper", "Torvalds", "Hamilton"]), f"user{i}@example.org",
             r.choice(["NL", "US", "DE"]), f"2024-0{1 + i % 9}-1{i % 9}", r.choice(["free", "pro", "team"]), f"{r.randrange(5000) / 100:.2f}"]
        docs.append("\n".join(f"{c}: {x}" for c, x in zip(cols, v)))
    return "\n\n".join(docs) + "\n"


case(
    766,
    claim="Runs of 2+ consecutive `column: value` documents with the same ordered column list become one tab-separated header plus value rows, and decode restores the exact original text; documents with tabs, a different schema or non-`key: value` lines are left as they were.",
    encode=csvdocs_encode,
    decode=csvdocs_decode,
    fixtures=[
        Fixture("top-10 rows of an 8-column CSV", _csv_docs(10), True),
        Fixture("two schemas, a prose paragraph and a singleton between", _csv_docs(4) + "\n" + "Note: this one has other columns\nSource: manual\n\n" + _csv_docs(5, seed=6).replace("plan: ", "tier: ") + "\nA closing remark with no colon-space.\n", True),
        Fixture("every document has its own columns", "Title: A\nBody: x y\n\nHeading: B\nText: z\n\nName: C\nValue: 1\n", False),
        Fixture("documents containing tabs", "a: 1\tx\nb: 2\n\na: 3\tx\nb: 4\n\na: 5\tx\nb: 6\n", False),
        Fixture("one column per document", "a: 1\n\na: 2\n\na: 3\n", False),
        Fixture("prose with colons", "Error: disk full\nWarning: retrying\n\nStack: main -> run\n", False),
        Fixture("block starting like a table, and with the escape", "§t\ta\tb\n1\t2\n\n§!x: 1\ny: 2\n\n§plain: 1\nq: 2\n\nk: 1\nl: 2\n\nk: 3\nl: 4\n", None),
        Fixture("values with ': ', empty values, CR, a key containing ': '", "a: x: y\nb: \n\na: p: q\nb: \n\r\n\na: z\nb: 1\r\n\na: w\nb: 2\r\n\nk: l: m\nn: o\n\nk: p: r\nn: s\n", None),
        Fixture("extra blank lines and no final newline", "a: 1\nb: 2\n\n\n\na: 3\nb: 4\n\n\na: 5\nb: 6\n\na: 7\nb: 8", None),
    ],
    min_gain=0.2,
    idempotent=False,
)

# ===============================================================================================================
# #778 Ditto marks for run-repeated cells in text tables. Equivalent, at the level of cells: a left-aligned fixed-width
# table (kubectl, docker ps: columns start at the header words) is rewritten tab-separated with a `"` wherever a cell of 3+
# characters equals the cell above it. Both the padding and the repeated text go, so the loss is the alignment whitespace.
# Cells that start with `"` or a backslash get a backslash prefix. Oracle: expand the ditto marks and compare the cell matrix.

_DITTO_LEGEND = '[" = same as the row above; a leading " or \\ is prefixed with \\; tab-separated]\n'


def _fixed_table(x: str):
    tail = x.endswith("\n")
    lines = (x[:-1] if tail else x).split("\n")
    if len(lines) < 4 or "\t" in x or "\r" in x or any(not l.strip() for l in lines):
        return None
    starts = [m.start() for m in re.finditer(r"(?:^|(?<=  ))\S", lines[0])]
    if len(starts) < 3 or starts[0] != 0:
        return None
    rows = []
    for l in lines:
        if any(len(l) >= s and l[s - 1] != " " for s in starts[1:]):
            return None  # a column boundary falls inside a word: not a header-aligned table
        cells = [l[a:b].strip() for a, b in zip(starts, starts[1:] + [None])]
        if "".join(l.split()) != "".join("".join(c.split()) for c in cells):
            return None
        rows.append(cells)
    return rows, tail


def ditto_encode(x: str) -> str:
    t = _fixed_table(x)
    if t is None:
        return x
    rows, tail = t
    out = []
    for r, row in enumerate(rows):
        out.append("\t".join('"' if r >= 2 and c == rows[r - 1][j] and len(c) >= 3 else "\\" + c if c[:1] in ('"', "\\") else c for j, c in enumerate(row)))
    res = _DITTO_LEGEND + "\n".join(out) + ("\n" if tail else "")
    return res if len(res) < len(x) else x


def _ditto_equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    t = _fixed_table(a)
    if t is None:
        return False
    rows_a, tail = t
    if not b.startswith(_DITTO_LEGEND) or b.endswith("\n") != tail:
        return False
    rows_b: list[list[str]] = []
    for line in b[len(_DITTO_LEGEND): len(b) - tail].split("\n"):
        prev = rows_b[-1] if rows_b else None
        row = []
        for j, c in enumerate(line.split("\t")):
            if c == '"':
                if prev is None or j >= len(prev):
                    return False
                row.append(prev[j])
            else:
                row.append(c[1:] if c[:1] == "\\" else c)
        rows_b.append(row)
    return rows_b == rows_a and "".join(a.split()) == "".join("".join(c.split()) for r in rows_b for c in r)


def _kubectl_pods(n: int) -> str:
    r = _rng(8)
    ns = [("kube-system", 12), ("monitoring", 9), ("prod", 14), ("default", n)]
    rows = [["NAMESPACE", "NAME", "READY", "STATUS", "RESTARTS", "AGE"]]
    for name, k in ns:
        for i in range(k):
            if len(rows) > n:
                break
            rows.append([name, f"{r.choice(['coredns', 'etcd', 'prometheus', 'api', 'worker'])}-{r.randrange(16**5):05x}-{r.randrange(16**5):05x}", "1/1", r.choice(["Running", "Running", "Running", "Completed"]), "0", r.choice(["5d", "5d", "12d"])])
    w = [max(len(row[j]) for row in rows) + 3 for j in range(6)]
    return "".join("".join(c.ljust(w[j]) for j, c in enumerate(row)).rstrip() + "\n" for row in rows)


_DOCKER_PS = (
    "CONTAINER ID   IMAGE          COMMAND                  CREATED        STATUS        PORTS                  NAMES\n"
    '3f4e5d6c7b8a   nginx:1.25     "/docker-entrypoint.…"   2 hours ago    Up 2 hours    0.0.0.0:80->80/tcp     web-1\n'
    '4a5b6c7d8e9f   nginx:1.25     "/docker-entrypoint.…"   2 hours ago    Up 2 hours    0.0.0.0:81->80/tcp     web-2\n'
    '5b6c7d8e9f0a   redis:7        "docker-entrypoint.s…"   2 hours ago    Up 2 hours                           cache\n'
    '6c7d8e9f0a1b   redis:7        "docker-entrypoint.s…"   3 hours ago    Up 3 hours                           cache-2\n'
)

case(
    778,
    claim="A header-aligned text table (kubectl/docker style) becomes tab-separated with a `\"` for each 3+-character cell equal to the cell above; expanding the marks and unescaping gives exactly the original cell matrix, and non-table or ragged input is untouched.",
    loses="column alignment padding and trailing whitespace",
    encode=ditto_encode,
    equivalent=_ditto_equiv,
    fixtures=[
        Fixture("kubectl get pods -A, 48 rows", _kubectl_pods(48), True),
        Fixture("docker ps with quoted COMMAND cells and empty PORTS", _DOCKER_PS, True),
        Fixture("tiny table: the legend would cost more than it saves", "A  B  C\n1  2  3\n4  5  6\n7  8  9\n", False),
        Fixture("git log has no aligned columns", "e2830e2 docs: render a Run line\n990e62b docs: runnable harness\nc652b9f docs: add ideas list\nabcdef0 more\n", False),
        Fixture("right-aligned numbers break the column boundary", "USER     PID  COMMAND\nroot       1  init\nroot   12345  sshd\nroot     200  bash\nroot     300  cron\n", False),
        Fixture("table that already contains tabs", "A  B  C\n1\t2  3\n4  5  6\n7  8  9\n", False),
        Fixture("cells that look like the marks", 'NAME      NOTE      KIND\n"x"       "quoted"  \\raw\n"x"       "quoted"  \\raw\n"x"       "quoted"  \\raw\na         b         c\n"         "         "\n', None),
        Fixture("empty cells, short lines, trailing spaces", "NAME      NOTE      KIND\nalpha     same      \nalpha     same      \nbeta      same\nbeta      same      x   \n", None),
        Fixture("input that starts like encoder output", _DITTO_LEGEND + "NAME  NOTE  KIND\nalpha  same  x\nalpha  same  x\nbeta  same  x\n", None),
    ],
    min_gain=0.25,
)
