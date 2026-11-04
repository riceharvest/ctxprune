"""Runnable fidelity cases for the structured-data lossless ideas (#896 #897 #898 #900 #985 #987 #988 #1081 #1167
#1169 #1234 #1245 #1248 #1249 #1250 #1255 #1326 #1365). See harness.py for what a pass means.

Conventions used below:
  * every encoder is wrapped by `_total` (or `_failopen` where an escape path may legitimately grow the text): if
    anything inside raises, the input comes back untouched, and `_total` also returns the input unless the result is shorter;
  * in-band signalling uses the sigil "§": text that already starts with it is escaped with "§raw\\n", and ideas whose
    markers could be confused with real text leave any input that contains the sigil alone (see `_esc_encode`);
  * roundtrip cases are byte exact; the rest are `equivalent` with an oracle that parses both sides with a different
    algorithm (json.loads, xml canonicalisation, html.parser, tokenize, a YAML subset parser, a CommonMark subset
    renderer, exact rational arithmetic) and a `loses=` that names exactly what is not preserved.
"""
import csv  # noqa: F401  (kept for fixtures that build CSV text)
import datetime
import functools
import io
import itertools  # noqa: F401
import json
import re
import tokenize
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from html.parser import HTMLParser

from harness import Fixture, case, tokens

SIGIL = "§"
RAW = "§raw\n"


def _failopen(fn):
    """Fail open: an encoder that raises hands the input back untouched."""

    @functools.wraps(fn)
    def wrapper(text):
        try:
            out = fn(text)
        except Exception:  # noqa: BLE001
            return text
        return out if isinstance(out, str) else text

    return wrapper


def _total(fn):
    """Fail open and self-gate: hand the input back unless the result is strictly shorter."""

    @functools.wraps(fn)
    def wrapper(text):
        try:
            out = fn(text)
        except Exception:  # noqa: BLE001
            return text
        return out if isinstance(out, str) and len(out) < len(text) else text

    return wrapper


def _esc_encode(text, hdr, fold, strict_inner=False):
    """In-band header protocol. `fold(text) -> body | None`. Text that starts with the sigil is escaped (so decode can
    tell it from an encoded text); with strict_inner, text containing the sigil anywhere is left alone because the
    encoded markers could be confused with it."""
    if text.startswith(SIGIL):
        return RAW + text
    if strict_inner and SIGIL in text:
        return text
    try:
        body = fold(text)
    except Exception:  # noqa: BLE001
        body = None
    if body is None:
        return text
    out = hdr + body
    return out if len(out) < len(text) else text


def _esc_decode(text, hdr, unfold):
    if text.startswith(RAW):
        return text[len(RAW):]
    if text.startswith(hdr):
        return unfold(text[len(hdr):])
    return text


def _strict_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key")
        d[k] = v
    return d


def _no_const(_name):
    raise ValueError("NaN/Infinity is not JSON")


def _finite(s):
    f = float(s)
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError("non-finite number")
    return f


def _load_strict(text):
    """json.loads that refuses duplicate keys, NaN/Infinity and overflowing floats (all of which re-serialise differently)."""
    return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_const, parse_float=_finite)


def _dumps(v):
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _canon(v):
    """Order- and type-sensitive canonical text of a parsed JSON value (1, 1.0 and true differ; key order matters)."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


# ===============================================================================================================
# #896 Fold Relay Connection / Edge boilerplate types in GraphQL SDL. Roundtrip: in-place markers, legend in header.

_CONN = "type {n}Connection {{\n  edges: [{n}Edge]\n  nodes: [{n}]\n  pageInfo: PageInfo!\n  totalCount: Int!\n}}"
_EDGE = "type {n}Edge {{\n  cursor: String!\n  node: {n}\n}}"
_CONN_RE = re.compile(r"^type (\w+)Connection \{\n  edges: \[\1Edge\]\n  nodes: \[\1\]\n  pageInfo: PageInfo!\n  totalCount: Int!\n\}$", re.M)
_EDGE_RE = re.compile(r"^type (\w+)Edge \{\n  cursor: String!\n  node: \1\n\}$", re.M)
_RELAY_HDR = ("§relay boilerplate folded: '§C X' = type XConnection{edges:[XEdge] nodes:[X] pageInfo:PageInfo! totalCount:Int!}, "
              "'§E X' = type XEdge{cursor:String! node:X}; every other type is verbatim\n")


def _fold_relay(text):
    body = _CONN_RE.sub(lambda m: f"§C {m[1]}", text)
    body = _EDGE_RE.sub(lambda m: f"§E {m[1]}", body)
    return None if body == text else body


def _unfold_relay(body):
    body = re.sub(r"^§C (\w+)$", lambda m: _CONN.format(n=m[1]), body, flags=re.M)
    return re.sub(r"^§E (\w+)$", lambda m: _EDGE.format(n=m[1]), body, flags=re.M)


@_failopen
def encode_relay(text):
    return _esc_encode(text, _RELAY_HDR, _fold_relay, strict_inner=True)


def decode_relay(text):
    return _esc_decode(text, _RELAY_HDR, _unfold_relay)


def _sdl_relay():
    names = ["Repository", "Issue", "PullRequest", "User", "Organization", "Commit", "Release", "Label", "Milestone", "Team"]
    parts = ["schema {\n  query: Query\n  mutation: Mutation\n}\n", "interface Node {\n  id: ID!\n}\n"]
    for n in names:
        parts.append(f"type {n} implements Node {{\n  id: ID!\n  name: String\n  createdAt: DateTime!\n}}\n")
        parts.append(_CONN.format(n=n) + "\n")
        parts.append(_EDGE.format(n=n) + "\n")
    # deviations stay verbatim: extra field, non-null edges, a description, a different nodes type
    parts.append(_CONN.format(n="Gist").replace("totalCount: Int!", "totalCount: Int!\n  totalSize: Int") + "\n")
    parts.append(_CONN.format(n="Project").replace("[ProjectEdge]", "[ProjectEdge!]!") + "\n")
    parts.append(_EDGE.format(n="Gist").replace("node: Gist", "node: Gist!") + "\n")
    parts.append('"""A comment"""\n' + _EDGE.format(n="Comment") + "\n")
    parts.append("enum OrderDirection {\n  ASC\n  DESC\n}\n")
    return "\n".join(parts)


_SDL_PLAIN = "type Query {\n  me: User\n}\n\ntype User {\n  id: ID!\n  login: String!\n}\n\ntype PageInfo {\n  hasNextPage: Boolean!\n}\n"
_SDL_ONE = _sdl_relay()
case(
    896,
    claim="Relay XConnection{edges,nodes,pageInfo,totalCount} and XEdge{cursor,node} blocks that match the template character for character become one-line markers under a one-line legend; any deviating block, other type and every other byte stays verbatim, and decode restores the exact original.",
    encode=encode_relay,
    decode=decode_relay,
    fixtures=lambda: [
        Fixture("relay schema with 10 template twins and 4 deviations", _SDL_ONE, True),
        Fixture("schema without connection types", _SDL_PLAIN, False),
        Fixture("CRLF schema is not matched", _SDL_ONE.replace("\n", "\r\n"), False),
        Fixture("only deviating blocks", _CONN.format(n="A").replace("Int!", "Int") + "\n" + _EDGE.format(n="A").replace("String!", "String") + "\n", False),
        Fixture("text that already contains a marker line", "§C Repository\n" + _SDL_ONE, None),
        Fixture("text that starts like encoder output", _RELAY_HDR + _SDL_ONE, None),
        Fixture("sigil in a description", _SDL_ONE + '\n"""costs 5§"""\ntype Price {\n  a: Int\n}\n', None),
    ],
    min_gain=0.3,
    idempotent=False,  # encoded text starts with the sigil, so re-encoding must escape it (grows by design)
)


# ===============================================================================================================
# #897 Sparse wide CSV/TSV: header once, then col=value for filled cells only. Roundtrip on raw cell text.

def _read_cell(s, i, delim):
    """End of the raw cell starting at i: stops at an unquoted delimiter or newline. None if a quote never closes."""
    n = len(s)
    j = i
    if j < n and s[j] == '"':
        j += 1
        while True:
            k = s.find('"', j)
            if k < 0:
                return None
            if k + 1 < n and s[k + 1] == '"':
                j = k + 2
                continue
            j = k + 1
            break
    while j < n and s[j] != delim and s[j] != "\n":
        j += 1
    return j


def _parse_rows(text, delim):
    rows, i, n = [], 0, len(text)
    while i < n:
        row = []
        while True:
            j = _read_cell(text, i, delim)
            if j is None:
                return None
            row.append(text[i:j])
            if j < n and text[j] == delim:
                i = j + 1
                continue
            i = j + 1 if j < n else j
            break
        rows.append(row)
    return rows


def _fold_sparse(text):
    if "\r" in text or not text:
        return None
    head = text.split("\n", 1)[0]
    delim = "\t" if head.count("\t") >= head.count(",") and "\t" in head else ","
    rows = _parse_rows(text, delim)
    if not rows or len(rows) < 3:
        return None
    names, data = rows[0], rows[1:]
    ncol = len(names)
    if ncol < 4 or any(len(r) != ncol for r in rows):
        return None
    if len(set(names)) != ncol or any((not nm) or "=" in nm or '"' in nm or "\n" in nm or nm != nm.strip() for nm in names):
        return None
    cells = ncol * len(data)
    empty = sum(1 for r in data for c in r if c == "")
    if empty < 0.7 * cells:
        return None
    nonl = "" if text.endswith("\n") else " nonl"
    kind = "tsv" if delim == "\t" else "csv"
    lines = [delim.join(f"{names[k]}={c}" for k, c in enumerate(r) if c != "") for r in data]
    return f"{kind}{nonl}\n" + delim.join(names) + "\n" + "".join(ln + "\n" for ln in lines)


@_failopen
def encode_sparse(text):
    return _esc_encode(text, "§sparse ", _fold_sparse)


def _unfold_sparse(body):
    sig, _, rest = body.partition("\n")
    delim = "\t" if sig.startswith("tsv") else ","
    nonl = sig.endswith(" nonl")
    head, _, rest = rest.partition("\n")
    names = head.split(delim)
    pos = {nm: k for k, nm in enumerate(names)}
    out, i, n = [], 0, len(rest)
    while i < n:
        cells = [""] * len(names)
        while rest[i] != "\n":
            eq = rest.index("=", i)
            j = _read_cell(rest, eq + 1, delim)
            cells[pos[rest[i:eq]]] = rest[eq + 1:j]
            i = j + 1 if rest[j] == delim else j
        i += 1
        out.append(delim.join(cells))
    text = delim.join(names) + "\n" + "".join(r + "\n" for r in out)
    return text[:-1] if nonl else text


def decode_sparse(text):
    return _esc_decode(text, "§sparse ", _unfold_sparse)


def _feature_csv(delim=",", final_nl=True):
    cols = [f"f{k}" for k in range(34)] + ["label", "notes", "split", "weight", "id", "group"]
    rows = [delim.join(cols)]
    for r in range(24):
        c = [""] * len(cols)
        c[r % 30] = str(r % 7 + 1)
        c[(r * 7 + 3) % 30] = f"0.{r + 1}"
        c[34] = "pos" if r % 3 else "neg"
        if r % 8 == 0:
            c[35] = '"first, second ""quoted""\nthird line"' if delim == "," else '"a ""b"" c"'
        if r % 5 == 0:
            c[36] = "train"
        c[38] = str(1000 + r)
        rows.append(delim.join(c))
    return "\n".join(rows) + ("\n" if final_nl else "")


_DENSE = "a,b,c,d\n" + "".join(f"{i},{i * 2},{i * 3},{i * 4}\n" for i in range(12))
case(
    897,
    claim="A CSV/TSV with at least 70% empty cells becomes its header once plus one line of name=rawcell pairs per row (filled cells only, row order kept); decode restores the exact bytes, including quoted cells with commas, quotes and newlines and a missing final newline.",
    encode=encode_sparse,
    decode=decode_sparse,
    fixtures=lambda: [
        Fixture("40-column feature matrix, ~3 filled per row", _feature_csv(), True),
        Fixture("TSV, no final newline", _feature_csv("\t", False), True),
        Fixture("dense CSV", _DENSE, False),
        Fixture("CRLF CSV", _feature_csv().replace("\n", "\r\n"), False),
        Fixture("ragged rows", "a,b,c,d,e\n1,,,,\n,2,,\n,,,,3\n", False),
        Fixture("header with = in a name", _feature_csv().replace("f3,", "f=3,", 1), None),
        Fixture("empty-row edge cases", "a,b,c,d,e\n,,,,\n,,,,x\n,,,,\n,,,,", None),
        Fixture("text that starts like encoder output", "§sparse csv\na,b\n1=2\n", None),
        Fixture("sigil in a cell", _feature_csv().replace(",pos", ",po§s", 1), None),
    ],
    min_gain=0.2,
    idempotent=False,
)


# ===============================================================================================================
# #898 Skeleton-plus-slots fold for near-identical sibling JSON subtrees. Equivalent: parsed value, key order and
# number types are preserved; whitespace and number lexemes are not.

def _sig(v):
    if isinstance(v, dict):
        return ("d", tuple((k, _sig(x)) for k, x in v.items()))
    if isinstance(v, list):
        return ("l", tuple(_sig(x) for x in v))
    return "L"


def _leaf_same(a, b):
    return type(a) is type(b) and _canon(a) == _canon(b)


def _make_run(members, keyed):
    """members: list of values sharing one skeleton. Returns the folded run object or None when it would not be shorter."""
    cols = []

    def walk(vs):
        v0 = vs[0]
        if isinstance(v0, dict):
            return {k: walk([v[k] for v in vs]) for k in v0}
        if isinstance(v0, list):
            return [walk([v[i] for v in vs]) for i in range(len(v0))]
        if all(_leaf_same(v0, v) for v in vs[1:]):
            return v0
        cols.append(vs)
        return f"§{len(cols) - 1}"

    skel = walk(members)
    run = {"s": skel, "v": [[c[m] for c in cols] for m in range(len(members))]}
    return run


def _fold_value(v, min_run=3):
    changed = False
    if isinstance(v, dict):
        items = list(v.items())
        out, i, fid = {}, 0, 0
        sigs = [_sig(x) if isinstance(x, (dict, list)) else None for _, x in items]
        while i < len(items):
            if sigs[i] is not None:
                j = i
                while j < len(items) and sigs[j] == sigs[i]:
                    j += 1
                if j - i >= min_run:
                    run = _make_run([x for _, x in items[i:j]], True)
                    run["k"] = [k for k, _ in items[i:j]]
                    if len(_dumps(run)) < len(_dumps(dict(items[i:j]))):
                        out[f"§fold{fid}"] = run
                        fid += 1
                        changed = True
                        i = j
                        continue
            nx, ch = _fold_value(items[i][1], min_run)
            out[items[i][0]] = nx
            changed |= ch
            i += 1
        return out, changed
    if isinstance(v, list):
        out, i = [], 0
        sigs = [_sig(x) if isinstance(x, (dict, list)) else None for x in v]
        while i < len(v):
            if sigs[i] is not None:
                j = i
                while j < len(v) and sigs[j] == sigs[i]:
                    j += 1
                if j - i >= min_run:
                    run = _make_run(v[i:j], False)
                    if len(_dumps(run)) < len(_dumps(v[i:j])):
                        out.append({"§fold": run})
                        changed = True
                        i = j
                        continue
            nx, ch = _fold_value(v[i], min_run)
            out.append(nx)
            changed |= ch
            i += 1
        return out, changed
    return v, False


@_total
def encode_skeleton(text):
    if SIGIL in text:
        return text
    v = _load_strict(text)
    new, changed = _fold_value(v)
    return _dumps(new) if changed else text


def _unfold_oracle(v):
    """Independent expander: instantiate the skeleton by deep copy, then patch every slot placeholder."""
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict) and list(x) == ["§fold"]:
                out.extend(_instances(x["§fold"]))
            else:
                out.append(_unfold_oracle(x))
        return out
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k.startswith("§fold"):
                out.update(zip(x["k"], _instances(x)))
            else:
                out[k] = _unfold_oracle(x)
        return out
    return v


def _instances(run):
    def patch(node, vals):
        if isinstance(node, str) and node.startswith("§"):
            return vals[int(node[1:])]
        if isinstance(node, dict):
            return {k: patch(x, vals) for k, x in node.items()}
        if isinstance(node, list):
            return [patch(x, vals) for x in node]
        return node

    return [patch(json.loads(json.dumps(run["s"])), vals) for vals in run["v"]]


def _equiv_skeleton(a, b):
    if a == b:
        return True
    try:
        before = _canon(_load_strict(a))
    except Exception:  # noqa: BLE001
        return False  # an input we cannot parse must come back untouched, which a == b above already covered
    try:
        return before == _canon(_unfold_oracle(json.loads(b)))
    except Exception:  # noqa: BLE001
        return False


def _openapi(n=25):
    paths = {}
    for i in range(n):
        res = f"res{i}"
        paths[f"/{res}"] = {
            "options": {
                "summary": "CORS support",
                "consumes": ["application/json"],
                "produces": ["application/json"],
                "responses": {"200": {"description": "200 response", "headers": {
                    "Access-Control-Allow-Origin": {"type": "string"},
                    "Access-Control-Allow-Methods": {"type": "string"},
                    "Access-Control-Allow-Headers": {"type": "string"}}}},
                "x-amazon-apigateway-integration": {
                    "type": "mock", "passthroughBehavior": "when_no_match", "requestTemplates": {"application/json": "{\"statusCode\": 200}"},
                    "responses": {"default": {"statusCode": "200", "responseParameters": {
                        "method.response.header.Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
                        "method.response.header.Access-Control-Allow-Headers": "'Content-Type,Authorization'",
                        "method.response.header.Access-Control-Allow-Origin": "'*'"}}}},
                "operationId": f"options{res.title()}",
                "x-order": i * 1.0 if i % 4 == 0 else i,
            }
        }
    return {"openapi": "3.0.1", "info": {"title": "api", "version": "1"}, "paths": paths}


_K8S_ITEMS = {"kind": "List", "items": [
    {"kind": "Pod", "metadata": {"name": f"web-{i}", "namespace": "prod", "labels": {"app": "web", "tier": "front"}},
     "spec": {"containers": [{"name": "app", "image": "web:1.4", "ports": [{"containerPort": 8080}]}], "restartPolicy": "Always"},
     "status": {"phase": "Running" if i != 2 else "Pending", "podIP": f"10.0.0.{i + 4}"}} for i in range(6)]}
_PRETTY_NOFOLD = json.dumps({"users": {"a": {"id": 1, "name": "x"}, "b": {"id": 2, "tags": ["p"]}, "c": {"email": "e", "id": 3}}}, indent=2)
case(
    898,
    claim="Runs of 3+ consecutive sibling subtrees with the same key structure are replaced by one skeleton plus per-sibling slot values (dict runs keep their keys); expanding the folded JSON gives back the same parsed value, key order and number types.",
    loses="JSON whitespace, number spelling (1e2 vs 100.0, 1.50 vs 1.5) and string escape form; inputs with duplicate keys, NaN/Infinity or the sigil are left untouched",
    encode=encode_skeleton,
    equivalent=_equiv_skeleton,
    fixtures=lambda: [
        Fixture("API Gateway CORS paths (25)", _dumps(_openapi()), True),
        Fixture("k8s items list", _dumps(_K8S_ITEMS), True),
        Fixture("siblings with different structure", _dumps({"users": {"a": {"id": 1}, "b": {"id": 2, "x": 1}, "c": {"name": "z"}}}), False),
        Fixture("pretty JSON with nothing foldable", _PRETTY_NOFOLD, False),
        Fixture("two siblings only", _dumps({"paths": {"/a": {"get": {"x": 1}}, "/b": {"get": {"x": 2}}}}), False),
        Fixture("not JSON", "paths:\n  /a:\n    get: 1\n", False),
        Fixture("number and bool types differ across siblings", _dumps({"m": {k: {"unit": "ms", "retries": 3, "mode": "fast", "v": v, "w": w} for k, (v, w) in zip("abcdef", [(1, True), (1.0, 1), (-0.0, False), (1, True), (0.0, 0), (True, 1.0)])}}), None),
        Fixture("duplicate keys", '{"a":{"x":1},"a":{"x":2},"b":{"x":3},"c":{"x":4}}', None),
        Fixture("sigil in a value", _dumps({"m": {"a": {"v": "§1"}, "b": {"v": "x"}, "c": {"v": "y"}}}), None),
        Fixture("looks like a folded document", '{"§fold0":{"s":{"v":"§0"},"v":[[1],[2],[3]],"k":["a","b","c"]}}', None),
    ],
    min_gain=0.5,
    mutations=30,
)


# ===============================================================================================================
# #900 Lift escaped multi-line JSON strings into raw side blocks. Roundtrip, restricted to strings whose lexeme is the
# canonical json.dumps(ensure_ascii=False) form, so unlifting reproduces the exact bytes.

_JSTRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_AFTER = re.compile(r"\s*(.)", re.S)


def _is_json_text(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        pass
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False
    try:
        for ln in lines:
            json.loads(ln)
    except ValueError:
        return False
    return True


def _fold_lift(text):
    if not _is_json_text(text):
        return None
    pieces, blocks, last = [], [], 0
    for m in _JSTRING.finditer(text):
        lex = m.group(0)
        if lex.count("\\n") < 3:
            continue
        nxt = _AFTER.match(text, m.end())
        if nxt and nxt.group(1) == ":":
            continue  # an object key
        val = json.loads(lex)
        if val.count("\n") < 3 or json.dumps(val, ensure_ascii=False) != lex:
            continue  # only exactly re-creatable lexemes are lifted
        pieces.append(text[last:m.start()])
        pieces.append(f'"§{len(blocks)}"')
        blocks.append(val)
        last = m.end()
    if not blocks:
        return None
    pieces.append(text[last:])
    head = "".join(pieces)
    return f"{len(head)}\n" + head + "".join(f"\n§{k} {len(b)}\n{b}" for k, b in enumerate(blocks))


def _unfold_lift(body):
    n, _, rest = body.partition("\n")
    head, pos, blocks = rest[:int(n)], int(n), []
    while pos < len(rest):
        eol = rest.index("\n", pos + 1)
        k, size = rest[pos + 1:eol].split(" ")
        k, size = int(k[1:]), int(size)
        blocks.append(rest[eol + 1:eol + 1 + size])
        pos = eol + 1 + size
    return re.sub(r'"§(\d+)"', lambda m: json.dumps(blocks[int(m[1])], ensure_ascii=False), head)


@_failopen
def encode_lift(text):
    return _esc_encode(text, "§lift ", _fold_lift, strict_inner=True)


def decode_lift(text):
    return _esc_decode(text, "§lift ", _unfold_lift)


_PY_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "/srv/app/handlers/orders.py", line 88, in create_order\n    total = price_of(item["sku"], qty=item["qty"])\n'
    '  File "/srv/app/pricing.py", line 41, in price_of\n    return TABLE[sku] * qty\n'
    "KeyError: 'SKU-0042'\n\nDuring handling of the above exception, another exception occurred:\n\n"
    'Traceback (most recent call last):\n  File "/srv/app/main.py", line 12, in <module>\n    raise HTTPError(\"bad sku: %r\" % sku)\n'
    "HTTPError: bad sku: 'SKU-0042'\n")
_DIFF_BODY = ('diff --git a/cfg.py b/cfg.py\n--- a/cfg.py\n+++ b/cfg.py\n@@ -1,6 +1,7 @@\n import os\n \n-NAME = "old"\n+NAME = "new"\n'
              '+LABEL = "a\\tb"\n DEBUG = os.environ.get("DEBUG", "0")\n PATHS = {"home": "C:\\\\Users\\\\x", "tmp": "/tmp"}\n')
_PKG = json.dumps({"name": "web", "version": "1.2.0", "scripts": {"build": "tsc -p .", "test": "jest --ci"},
                   "dependencies": {"express": "^4.18.2", "pino": "^8.0.0", "zod": "^3.22.0"}, "license": "MIT"}, indent=2)
_PYSRC = ('import os\n\nCONFIG = {"home": "/srv/app", "mode": os.environ.get("MODE", "dev")}\n\n\ndef greet(name):\n'
          '    print("hello, " + name + "!")\n    return {"name": name, "ok": True}\n\n\nif __name__ == "__main__":\n    greet("world")\n')
_FILE_RESULT = json.dumps({"path": "/srv/app/package.json", "size": len(_PKG), "content": _PKG,
                           "other": {"path": "/srv/app/main.py", "content": _PYSRC}}, ensure_ascii=False)
_SENTRY = json.dumps({"event_id": "9f2c", "level": "error", "platform": "python",
                      "exception": {"values": [{"type": "KeyError", "value": "'SKU-0042'", "stacktrace_text": _PY_TRACE}]},
                      "request": {"url": "https://x.test/orders", "method": "POST"}, "extra": {"patch": _DIFF_BODY}}, ensure_ascii=False)
_PINO = "\n".join(json.dumps({"level": 50, "time": 1700000000000 + i, "pid": 41, "msg": "unhandled",
                              "err": {"type": "TypeError", "message": "x is not a function",
                                      "stack": "TypeError: x is not a function\n    at handler (/app/src/h.js:10:5)\n    at next (/app/node_modules/express/lib/router/route.js:144:13)\n    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)\n    at /app/src/h.js:22:3"}},
                             ensure_ascii=False) for i in range(3)) + "\n"
case(
    900,
    claim="A JSON string value with 3+ newline escapes whose lexeme is exactly its canonical encoding is replaced by a placeholder and printed raw (real newlines, no escapes) in a length-framed block after the JSON; decode restores the exact bytes. Keys, non-canonical lexemes (\\u00e9, \\/) and short strings stay in place.",
    encode=encode_lift,
    decode=decode_lift,
    fixtures=lambda: [
        Fixture("file-read result: package.json and a Python file inside JSON strings", _FILE_RESULT, True),
        Fixture("Sentry-style event with traceback and diff strings (gain only a few %)", _SENTRY, None),
        Fixture("pino NDJSON with node stacks (gain is tiny, mainly enables line rules)", _PINO, None),
        Fixture("JSON with no multi-line strings", json.dumps({"a": "one\ntwo", "b": [1, 2, 3], "c": "x"}), False),
        Fixture("not JSON", _PY_TRACE, False),
        Fixture("python ensure_ascii escapes stay", json.dumps({"s": "caf\u00e9\nb\nc\nd\n"}), False),
        Fixture("key containing newlines is not lifted", json.dumps({"a\nb\nc\nd\n": 1}), False),
        Fixture("string that already looks like a placeholder", json.dumps({"x": "§0", "t": "a\nb\nc\nd\n"}), None),
        Fixture("text that starts like encoder output", '§lift 2\n{}', None),
        Fixture("JSON-valid text with raw CR, quotes and backslashes", json.dumps({"t": 'q"\\\r\n\n\n\n'}), None),
    ],
    min_gain=0.06,
    idempotent=False,
)


# ===============================================================================================================
# #985 Unescape over-escaped Markdown punctuation from HTML converters. Equivalent: a CommonMark-subset renderer
# (blocks, code spans, emphasis with flanking rules, inline links, images, reference links) gives the same output
# for both texts. The encoder is deliberately conservative: it only drops a backslash where the character is inert.

_ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_BLOCK_PFX = r"(?:\s|>|[-+*](?=\s)|\d{1,9}[.)](?=\s))*"
_AT_START = re.compile(_BLOCK_PFX)
_AT_DIGITS = re.compile(_BLOCK_PFX + r"\d{1,9}")
_PAIR = re.compile(r"\\\[([^\[\]\\`]{2,}?)\\\]")


def _code_spans(line):
    """Code spans on one line by the CommonMark rule, or None when some backtick run has no partner on the line."""
    spans, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\" and i + 1 < n and line[i + 1] in _ASCII_PUNCT:
            i += 2
            continue
        if c == "`":
            j = i
            while j < n and line[j] == "`":
                j += 1
            run, k = j - i, j
            while True:
                k = line.find("`", k)
                if k < 0:
                    return None
                e = k
                while e < n and line[e] == "`":
                    e += 1
                if e - k == run:
                    spans.append((i, e))
                    i = e
                    break
                k = e
            continue
        i += 1
    return spans


def _in_urlish_word(line, i):
    a = i
    while a > 0 and not line[a - 1].isspace():
        a -= 1
    b = i
    while b < len(line) and not line[b].isspace():
        b += 1
    w = line[a:b]
    return "://" in w or "www." in w or "@" in w


def _may_unescape(line, i, d, no_bracket, no_defs):
    before = line[:i]
    if d == "_":
        return 0 < i and line[i - 1].isalnum() and i + 2 < len(line) and line[i + 2].isalnum()
    if d == ".":
        return not _AT_DIGITS.fullmatch(before)
    if d == "!":
        return line[i + 2:i + 3] != "[" and line[i + 2:i + 4] != "\\["
    if d in "()":
        return no_bracket and (d == "(" or not _AT_DIGITS.fullmatch(before))
    if d == "-":
        return not _AT_START.fullmatch(before)
    return d in "?;,"  # no meaning in CommonMark core


def _unescape_line(line, spans, no_bracket, no_defs):
    out, i, n = [], 0, len(line)
    span_end = dict(spans)
    while i < n:
        if i in span_end:
            out.append(line[i:span_end[i]])
            i = span_end[i]
            continue
        c = line[i]
        if c != "\\" or i + 1 >= n or line[i + 1] not in _ASCII_PUNCT:
            out.append(c)
            i += 1
            continue
        d = line[i + 1]
        if d == "[" and no_defs and not _in_urlish_word(line, i):
            m = _PAIR.match(line, i)
            if m and line[m.end():m.end() + 1] not in ("(", "[", ":") and line[m.end():m.end() + 2] != "\\(":
                out.append("[" + m.group(1) + "]")
                i = m.end()
                continue
        if d in "_.!()-?;," and _may_unescape(line, i, d, no_bracket, no_defs) and not _in_urlish_word(line, i):
            out.append(d)
        else:
            out.append("\\" + d)
        i += 2
    return "".join(out)


@_total
def encode_unescape_md(text):
    if "\\" not in text:
        return text
    lines = text.split("\n")
    out = list(lines)
    no_defs = "]:" not in text
    chunk, fence = [], None

    def flush():
        if not chunk:
            return
        body = [lines[k] for k in chunk]
        joined = "\n".join(body)
        if re.search(r"<[A-Za-z/!?]", joined):
            chunk.clear()
            return
        spans = [_code_spans(ln) for ln in body]
        if any(s is None for s in spans):
            chunk.clear()
            return
        no_bracket = "]" not in joined
        for k, ln, sp in zip(chunk, body, spans):
            if ln.startswith(("    ", "\t")) or "|" in ln:
                continue
            out[k] = _unescape_line(ln, sp, no_bracket, no_defs)
        chunk.clear()

    for idx, ln in enumerate(lines):
        fm = re.match(r" {0,3}(`{3,}|~{3,})", ln)
        if fence:
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence):
                fence = None
            continue
        if fm:
            flush()
            fence = fm.group(1)
            continue
        if not ln.strip():
            flush()
            continue
        chunk.append(idx)
    flush()
    return "\n".join(out)


# ---- oracle: CommonMark-subset renderer (independent of the encoder: it models what the characters mean) -------

import string  # noqa: E402
import unicodedata  # noqa: E402

_AUTOLINK = re.compile(r"<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[^\s<>@]+@[^\s<>]+)>")
_HTMLTAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_UNESC = re.compile(r"\\([!-/:-@\[-`{-~])")


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _is_punct(c):
    return c in _ASCII_PUNCT or unicodedata.category(c)[0] in "PS"


def _label(s):
    return " ".join(s.lower().split())


def _link_tail(s, k):
    """Parse '(dest "title")' starting at s[k] == '('. Returns (end, dest, title) or None."""
    n = len(s)
    j = k + 1
    while j < n and s[j] in " \t\n":
        j += 1
    if j < n and s[j] == "<":
        e = j + 1
        while e < n and s[e] not in ">\n":
            e += 2 if s[e] == "\\" else 1
        if e >= n or s[e] != ">":
            return None
        dest, j = s[j + 1:e], e + 1
    else:
        start, depth = j, 0
        while j < n:
            ch = s[j]
            if ch == "\\" and j + 1 < n and s[j + 1] in _ASCII_PUNCT:
                j += 2
                continue
            if ch in " \t\n":
                break
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        if depth:
            return None
        dest = s[start:j]
    while j < n and s[j] in " \t\n":
        j += 1
    title = ""
    if j < n and s[j] in "\"'(":
        close = ")" if s[j] == "(" else s[j]
        e = j + 1
        while e < n and s[e] != close:
            e += 2 if s[e] == "\\" else 1
        if e >= n:
            return None
        title, j = s[j + 1:e], e + 1
        while j < n and s[j] in " \t\n":
            j += 1
    if j < n and s[j] == ")":
        return j + 1, _UNESC.sub(r"\1", dest), title
    return None


def _emphasis(items):
    delims = [d for d in items if isinstance(d, dict) and "ch" in d]
    for ci, cl in enumerate(delims):
        while cl["n"] > 0 and cl["close"]:
            oi = None
            for k in range(ci - 1, -1, -1):
                op = delims[k]
                if op["ch"] == cl["ch"] and op["open"] and op["n"] > 0:
                    if (op["close"] or cl["open"]) and (op["orig"] + cl["orig"]) % 3 == 0 and not (op["orig"] % 3 == 0 and cl["orig"] % 3 == 0):
                        continue
                    oi = k
                    break
            if oi is None:
                break
            op = delims[oi]
            use = 2 if op["n"] >= 2 and cl["n"] >= 2 else 1
            tag = "strong" if use == 2 else "em"
            op["post"].append(f"<{tag}>")
            cl["pre"].append(f"</{tag}>")
            op["n"] -= use
            cl["n"] -= use
            for d in delims[oi + 1:ci]:
                d["open"] = d["close"] = False
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
        elif "ch" in it:
            out.append("".join(it["pre"]) + it["ch"] * it["n"] + "".join(reversed(it["post"])))
        else:
            out.append(it["lit"])
    return "".join(out)


def _inline(s, defs):
    toks, brackets, i, n = [], [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in _ASCII_PUNCT:
            toks.append(_esc(s[i + 1]))
            i += 2
        elif c == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            run, k, found = j - i, j, None
            while True:
                k = s.find("`", k)
                if k < 0:
                    break
                e = k
                while e < n and s[e] == "`":
                    e += 1
                if e - k == run:
                    found = (k, e)
                    break
                k = e
            if found:
                body = s[j:found[0]].replace("\n", " ")
                if len(body) > 2 and body[0] == " " == body[-1] and body.strip():
                    body = body[1:-1]
                toks.append(f"<code>{_esc(body)}</code>")
                i = found[1]
            else:
                toks.append("`" * run)
                i = j
        elif c == "<" and (m := (_AUTOLINK.match(s, i) or _HTMLTAG.match(s, i))):
            toks.append(m.group(0))
            i = m.end()
        elif c == "[" or (c == "!" and s[i + 1:i + 2] == "["):
            img = c == "!"
            brackets.append(len(toks))
            toks.append({"lit": "![" if img else "[", "img": img, "after": i + (2 if img else 1), "live": True})
            i += 2 if img else 1
        elif c == "]" and brackets:
            o = brackets.pop()
            op = toks[o]
            label_raw = s[op["after"]:i]
            link = None
            if op["live"]:
                if s[i + 1:i + 2] == "(":
                    t = _link_tail(s, i + 1)
                    if t:
                        link = t
                if link is None:
                    m = re.match(r"\[([^\[\]]*)\]", s[i + 1:])
                    if m and _label(m.group(1) or label_raw) in defs:
                        link = (i + 1 + m.end(), "ref:" + _label(m.group(1) or label_raw), "")
                    elif not m and _label(label_raw) in defs:
                        link = (i + 1, "ref:" + _label(label_raw), "")
            if link:
                inner = _emphasis(toks[o + 1:])
                del toks[o:]
                toks.append(f"<{'img' if op['img'] else 'a'} href=\"{_esc(link[1])}\" title=\"{_esc(link[2])}\">{inner}</{'img' if op['img'] else 'a'}>")
                i = link[0]
                if not op["img"]:
                    for k in brackets:
                        if not toks[k]["img"]:
                            toks[k]["live"] = False
            else:
                toks.append("]")
                i += 1
        elif c in "*_":
            j = i
            while j < n and s[j] == c:
                j += 1
            before = s[i - 1] if i else " "
            after = s[j] if j < n else " "
            left = not after.isspace() and (not _is_punct(after) or before.isspace() or _is_punct(before))
            right = not before.isspace() and (not _is_punct(before) or after.isspace() or _is_punct(after))
            if c == "*":
                can_open, can_close = left, right
            else:
                can_open = left and (not right or _is_punct(before))
                can_close = right and (not left or _is_punct(after))
            toks.append({"ch": c, "n": j - i, "orig": j - i, "open": can_open, "close": can_close, "pre": [], "post": []})
            i = j
        else:
            toks.append(_esc(c))
            i += 1
    return _emphasis(toks)


def _render_md(text):
    lines = text.split("\n")
    defs = {_label(m.group(1)) for ln in lines if (m := re.match(r"^[ >]{0,6}\[([^\]\n]+)\]:\s*\S", ln))}
    blocks, para, para_pfx, fence = [], [], (), None

    def flush():
        if para:
            blocks.append(f"P{list(para_pfx)}:" + _inline("\n".join(para), defs))
            para.clear()

    for ln in lines:
        if fence:
            if re.match(r"^ {0,3}" + re.escape(fence[0]) + "{%d,}\\s*$" % len(fence), ln):
                fence = None
            else:
                blocks.append("CODE:" + ln)
            continue
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", ln)
        if m:
            flush()
            fence = m.group(1)
            blocks.append("FENCE")
            continue
        if not ln.strip():
            flush()
            continue
        s, pfx = ln.lstrip(" \t"), []
        while True:
            if re.match(r"^([-*_])(?:[ \t]*\1){2,}[ \t]*$", s):
                flush()
                blocks.append(f"HR{pfx}")
                break
            if para and not pfx and re.match(r"^(=+|-+)[ \t]*$", s):
                blocks.append(f"SETEXT{s[0]}:" + _inline("\n".join(para), defs))
                para.clear()
                break
            m = re.match(r"^(#{1,6})(?:[ \t]+|$)(.*?)(?:[ \t]+#+)?[ \t]*$", s)
            if m:
                flush()
                blocks.append(f"H{len(m.group(1))}{pfx}:" + _inline(m.group(2), defs))
                break
            m = re.match(r">[ \t]?", s)
            if m:
                pfx.append("bq")
                s = s[m.end():].lstrip(" \t")
                continue
            m = re.match(r"([-+*]|\d{1,9}[.)])(?:[ \t]+|$)", s)
            if m:
                flush()
                pfx.append("li" + (m.group(1) if m.group(1) in "-+*" else "o" + m.group(1)[-1]))
                s = s[m.end():]
                if not s.strip():
                    blocks.append(f"EMPTYLI{pfx}")
                    break
                continue
            if para and tuple(pfx) != para_pfx:
                flush()
            para.append(s)
            para_pfx = tuple(pfx)
            break
    flush()
    return "\n".join(blocks)


def _equiv_md(a, b):
    return a == b or _render_md(a) == _render_md(b)


_CONVERTED = (
    "# Install\\_guide\n\n"
    "Call `load\\_config()` first, then run `my\\_cli --dry\\_run` (inside code, nothing changes).\n\n"
    "The helper read\\_user\\_profile\\_data returns user\\_id and display\\_name\\. See section 3\\.2 for details\\!\n"
    "Set the max\\_retry\\_count option (default 3\\) and the request\\_timeout\\_ms option\\.\n\n"
    "- Use get\\_all\\_items\\(\\) to list items\\.\n- Mr\\. Smith owns the build\\_cache\\_dir folder\\.\n- A well\\-known tmp\\-dir setting\\.\n\n"
    "Configure the worker\\_pool\\_size, queue\\_max\\_depth and idle\\_timeout\\_seconds settings\\. The default\\_region\\_name is us\\-east\\-1\\.\n"
    "Set auth\\_token\\_ttl and refresh\\_token\\_ttl\\. Then restart the job\\_runner\\_service (see the run\\_book\\_page\\)\\.\n\n"
    "The retry\\_policy\\_name field accepts exponential\\_backoff or fixed\\_delay\\. Use max\\_attempts\\_per\\_call and base\\_delay\\_ms\\.\n\n"
    "Fields: user\\_id, org\\_id, repo\\_id, created\\_at, updated\\_at, closed\\_at, merged\\_at, head\\_sha, base\\_sha, merge\\_commit\\_sha\\.\n"
    "Flags: is\\_draft, is\\_locked, is\\_merged, has\\_conflicts, auto\\_merge\\_enabled, allow\\_squash\\_merge\\.\n\n"
    "Wait\\.\\.\\. what\\? Version 2\\.4\\.1 is out\\; the old\\_api\\_v1 and the old\\_api\\_v2 are gone\\!\n\n"
    "See \\[note\\] and \\[draft\\] for context, plus www.example.com/a\\_b and https://x.test/p/a\\_b\\_c\\.\n\n"
    "These stay escaped because the backslash matters:\n\n"
    "\\*not bold\\* and \\_not italic\\_ and \\_\\_init\\_\\_ and \\# no heading\n"
    "1\\. not a list\n"
    "\\- not a bullet\n\n"
    "A \\[link text\\](http://x.test/y) is not a link, and neither is !\\[an image\\](http://x.test/i.png).\n\n"
    "```\nthe\\_code\\_block keeps \\_ and \\. as typed\n```\n\n"
    "    indented\\_code stays too\n")
_ESCAPES_ONLY_MEANINGFUL = (
    "# Title\n\n\\*not bold\\* and \\_not italic\\_ and \\_\\_init\\_\\_\n1\\. not a list\n\\- not a bullet\n\\# not a heading\n\n"
    "A \\[link text\\](http://x.test/y) and !\\[img\\](u) and \\[a\\]\\[b\\] and `code\\_here`.\n\n"
    "- 1\\. nested ordered marker\n- 2\\) another\n\n```\nfenced\\_code \\. \\_\n```\n")
case(
    985,
    claim="Backslashes before _ . ! ( ) - [ ] ? ; , are dropped only where the character is inert (intraword underscore, period not after line-start digits, ! not before [, parens only when the paragraph has no ], hyphen not at block start, \\[word\\] only when the document has no reference definitions); code spans, fenced and indented code, table rows, HTML, URLs and every other escape are untouched, so a CommonMark-subset render is identical before and after.",
    loses="the backslash bytes themselves, in positions where the escaped character already renders as plain text",
    encode=encode_unescape_md,
    equivalent=_equiv_md,
    fixtures=lambda: [
        Fixture("turndown-style page with inert and meaningful escapes", _CONVERTED, True),
        Fixture("only meaningful escapes", _ESCAPES_ONLY_MEANINGFUL, False),
        Fixture("plain markdown without backslashes", "# T\n\nsnake_case and a.b (c) [d](e)\n", False),
        Fixture("escapes only inside code", "`a\\_b`\n\n```\nc\\_d\n```\n", False),
        Fixture("escaped backslash before underscore", "C:\\\\_temp and a\\\\\\_b and trailing\\\\\n", None),
        Fixture("reference definition makes \\[docs\\] meaningful", "See \\[docs\\] and [docs] here.\n\n[docs]: https://x.test/d\n", None),
        Fixture("table rows and html", "| a\\_b | c |\n|---|---|\n| d\\_e | \\. |\n\n<span>snake\\_case</span>\n", None),
        Fixture("hard line break and unmatched backtick", "line one\\\nline\\_two ` open\nnext\\_line `x\\_y`\n", None),
        Fixture("link destination and image with escapes", "[a\\_b](c\\_d.md) and ![x\\_y](z\\_w.png) and [k\\(1\\)](u\\(2\\))\n", None),
    ],
    min_gain=0.05,
    mutations=30,
)
