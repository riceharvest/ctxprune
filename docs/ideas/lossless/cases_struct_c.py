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
import datetime
import functools
import io
import json
import re
import tokenize
import unicodedata
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
        Fixture("siblings with different structure", _dumps({"users": {"a": {"id": 1}, "b": {"id": 2, "x": 1}, "c": {"id": 3}}}), False),
        Fixture("pretty JSON with nothing foldable", _PRETTY_NOFOLD, False),
        Fixture("two siblings only", _dumps({"paths": {"/a": {"get": {"x": 1}}, "/b": {"get": {"x": 2}}}}), False),
        Fixture("not JSON", "paths:\n  /a:\n    get: 1\n", False),
        Fixture("number and bool types differ across siblings", _dumps({"m": {k: {"unit": "ms", "retries": 3, "mode": "fast", "v": v, "w": w} for k, (v, w) in zip("abcdef", [(1, 0), (1.0, False), (True, 0.0), (1, 0), (1.0, False), (True, -0.0)])}}), None),
        Fixture("one sibling in the middle has an extra key", _dumps({"m": {k: ({"unit": "ms", "retries": 3, "mode": "fast", "id": i} | ({"debug": True} if k == "c" else {})) for i, k in enumerate("abcdefg")}}), None),
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
    "A \\[link text\\](http://x.test/y) is not a link, and neither is !\\[an image\\](http://x.test/i.png). Nor is \\![an image](http://x.test/j.png) an image: the bang is escaped.\n\n"
    "```\nthe\\_code\\_block keeps \\_ and \\. as typed\n```\n\n"
    "    indented\\_code stays too\n")
_ESCAPES_ONLY_MEANINGFUL = (
    "# Title\n\n\\*not bold\\* and \\_not italic\\_ and \\_\\_init\\_\\_\n1\\. not a list\n\\- not a bullet\n\\# not a heading\n\n"
    "A \\[link text\\](http://x.test/y) and !\\[img\\](u) and \\![img](u) and \\[a\\]\\[b\\] and `code\\_here`.\n\n"
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




# ===============================================================================================================
# #987 Drop optional spaces in Python dict/list/call reprs. Equivalent: per line, the Python token stream is the same
# (so every string literal is byte-identical) and ast.literal_eval agrees where the line is a literal.

_SRC_LINE = re.compile(r"^\s*(?:\d+[\t:|→]\s*)?(?:def |class |import |from \S+ import |return\b|elif |else:|try:|except\b|finally:|with |for \w+ in |while |async |@\w|if .*:\s*$)")


def _looks_like_source(text):
    return sum(1 for ln in text.split("\n") if _SRC_LINE.match(ln)) >= 3


_PAIRS = {")": "(", "]": "[", "}": "{"}


def _skip_string(line, i):
    c, n, j = line[i], len(line), i + 1
    while j < n and line[j] != c:
        j += 2 if line[j] == "\\" else 1
    return j + 1 if j < n else None


def _groups(line):
    """Spans (callee name included) of depth-0 bracket groups outside string literals; None if the line is not balanced."""
    spans, stack, i, n, start = [], [], 0, len(line), 0
    while i < n:
        c = line[i]
        if c in "'\"":
            i = _skip_string(line, i)
            if i is None:
                return None
            continue
        if c == "#":
            return None
        if c in "([{":
            if not stack:
                start = i
                while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
                    start -= 1
            stack.append(c)
        elif c in ")]}":
            if not stack or stack.pop() != _PAIRS[c]:
                return None
            if not stack:
                spans.append((start, i + 1))
        i += 1
    return None if stack else spans


def _squeeze(group):
    out, i, n = [], 0, len(group)
    while i < n:
        c = group[i]
        if c in "'\"":
            j = _skip_string(group, i)
            out.append(group[i:j])
            i = j
            continue
        out.append(c)
        i += 1
        if c in ",:":
            while i < n and group[i] == " ":
                i += 1
    return "".join(out)


def _is_expression(src):
    try:
        compile(src, "<repr>", "eval")
        return True
    except Exception:  # noqa: BLE001
        return False


def _tidy_line(line):
    if ("," not in line and ":" not in line) or "'''" in line or '"""' in line:
        return line
    spans = _groups(line)
    if not spans:
        return line
    try:
        _py_tokens(line)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return line
    out, last = [], 0
    for a, b in spans:
        out.append(line[last:a])
        grp = line[a:b]
        out.append(_squeeze(grp) if _is_expression(grp) else grp)  # only real Python expressions, never prose in parentheses
        last = b
    out.append(line[last:])
    return "".join(out)


@_total
def encode_repr(text):
    if _looks_like_source(text):
        return text
    return "\n".join(_tidy_line(ln) for ln in text.split("\n"))


def _py_tokens(line):
    toks = []
    for t in tokenize.generate_tokens(io.StringIO(line.strip() + "\n").readline):
        if t.type not in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT):
            toks.append((t.type, t.string))
    return toks


def _literal(line):
    import ast

    try:
        return repr(ast.literal_eval(line.strip()))
    except Exception:  # noqa: BLE001
        return None


def _equiv_repr(a, b):
    la, lb = a.split("\n"), b.split("\n")
    if len(la) != len(lb):
        return False
    for x, y in zip(la, lb):
        if x == y:
            continue
        try:
            if _py_tokens(x) != _py_tokens(y):
                return False
        except (tokenize.TokenError, SyntaxError, IndentationError):
            return False  # a line the tokenizer cannot read must come back unchanged
        if _literal(x) != _literal(y):
            return False
        if x[:len(x) - len(x.lstrip())] != y[:len(y) - len(y.lstrip())]:
            return False
    return True


_REPRS = (
    "{'name': 'build-agent', 'version': (2, 4, 1), 'tags': ['ci', 'linux', 'x86_64'], 'owner': None, 'retries': {'max': 5, 'backoff': 1.5}}\n"
    "[{'id': 1, 'path': '/srv/a, b', 'ok': True}, {'id': 2, 'path': 'C:\\\\tmp\\\\x', 'ok': False}, {'id': 3, 'path': \"it's: fine, really\", 'ok': True}]\n"
    "INFO:root:loaded config {'a': 1, 'b': [1, 2, 3, 4, 5, 6, 7, 8], 'c': 'x: y, z'} in 0.4s\n"
    "Namespace(input='data.csv', output=None, workers=4, verbose=False, tags=['a', 'b'])\n"
    "E       assert {'a': 1, 'b': 2, 'c': [3, 4]} == {'a': 1, 'b': 2, 'c': [3, 5]}\n"
    "Retrying request(url='http://x.test/a?b=1,2', timeout=(3.0, 10.0), headers={'Accept': 'text/html', 'X-Id': 'k: v'}) after 2s\n"
    "array([[1, 2, 3],\n       [4, 5, 6]])\n"
    "b'\\x00\\x01, \\x02: \\x03' and f'{x:, }' and r'\\d+, \\w+' and 'unicode, é: ü'\n")
_PY_SOURCE = (
    "import os\n\n\ndef settings(env):\n    base = {'mode': 'dev', 'paths': ['/a', '/b'], 'n': 3}\n    if env == 'prod':\n        base['mode'] = 'prod'\n"
    "    return base\n\n\nclass Cfg:\n    default = {'a': 1, 'b': 2}\n")
case(
    987,
    claim="Inside balanced brackets of a single line, the spaces after ',' and ':' that sit outside string literals are removed; the per-line Python token stream (so every string literal byte) and ast.literal_eval value are unchanged, and text that looks like a Python source file is left alone.",
    loses="optional spaces after , and : inside brackets (cosmetic; a reader that wants PEP 8 spacing must re-add it)",
    encode=encode_repr,
    equivalent=_equiv_repr,
    fixtures=lambda: [
        Fixture("repr lines from prints, logs, pytest and argparse", _REPRS, True),
        Fixture("python source file (the Read tool)", _PY_SOURCE, False),
        Fixture("prose with commas and colons", "Note: the build, which ran twice, failed: see the log (attached, 3 pages).\nTime: 12:30, done\n", False),
        Fixture("already compact", "{'a':1,'b':[1,2,3]}\n", False),
        Fixture("triple quotes, comments, unbalanced and unterminated", "x = {'a': 1, 'b': '''p, q''' }\n{'a': 1, 'b': 2}  # note: a, b\n{'a': 1, 'b': [2, 3}\nprint('it's, ok', {'k': 1})\n", None),
        Fixture("tabs, CRLF, trailing space and odd tokens", "{'a':\t1, 'b': 2}\r\n[1,  2,   3 ]  \n(a, *b, **c)\nlambda: (x, y)\n{1: 2, 3: 4}\n", None),
    ],
    min_gain=0.07,
)


# ===============================================================================================================
# #988 Canonicalise printf-style scientific notation. Equivalent under exact value; the printed lexeme and trailing
# mantissa zeros are lost (that is the point), digits are never rounded.

_SCI = re.compile(r"(?<![\w.])(?<![\w.][+-])([+-]?)(\d+)(?:\.(\d+))?[eE]([+-])(\d{2,})(?![\w.])")


def _canon_sci(m):
    sign, ip, fp, esign, ed = m.groups()
    fp = fp or ""
    digits = (ip + fp).lstrip("0")
    exp10 = int(esign + ed) - len(fp)
    if not digits:
        new = sign + "0"  # zero keeps its sign
    else:
        stripped = digits.rstrip("0")
        exp10 += len(digits) - len(stripped)
        d = stripped
        sci = d[0] + ("." + d[1:] if len(d) > 1 else "") + "e" + str(exp10 + len(d) - 1)
        plain = None
        if abs(exp10) <= 30 and len(d) + max(0, exp10) <= 40:
            if exp10 >= 0:
                plain = d + "0" * exp10
            elif len(d) + exp10 > 0:
                plain = d[:len(d) + exp10] + "." + d[len(d) + exp10:]
            else:
                plain = "0." + "0" * (-exp10 - len(d)) + d
        new = sign + (plain if plain is not None and len(plain) <= len(sci) else sci)
    return new if len(new) < len(m.group(0)) else m.group(0)


@_total
def encode_sci(text):
    return _SCI.sub(_canon_sci, text)


_NUMBER = re.compile(r"(?<![\w.])(?<![\w.][+-])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
_PARTS = re.compile(r"[+-]?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?")


def _digits_exp(tok):
    """(coefficient digits without leading/trailing zeros, power of ten) with huge exponents kept as Python ints."""
    ip, fp, e = _PARTS.fullmatch(tok).groups()
    fp = fp or ""
    digits = (ip + fp).lstrip("0")
    exp10 = int(e or 0) - len(fp)
    stripped = digits.rstrip("0")
    return stripped, (exp10 + len(digits) - len(stripped) if stripped else 0)


def _same_number(x, y):
    if x == y:
        return True
    if x.startswith("-") != y.startswith("-"):
        return False  # the sign of zero is information too
    dx, dy = _digits_exp(x), _digits_exp(y)
    if dx != dy:
        return False
    try:  # where it is cheap, also confirm with exact rational arithmetic (never float)
        if abs(dx[1]) < 3000:
            return Fraction(Decimal(x)) == Fraction(Decimal(y))
    except InvalidOperation:
        return False
    return True


def _equiv_sci(a, b):
    pa, pb = _NUMBER.split(a), _NUMBER.split(b)
    na, nb = [m.group(0) for m in _NUMBER.finditer(a)], [m.group(0) for m in _NUMBER.finditer(b)]
    return pa == pb and len(na) == len(nb) and all(_same_number(x, y) for x, y in zip(na, nb))


_SOLVER = "".join(
    f"iter {i:3d}  residual= {r}  step= {s}  obj= {o}  cond= {c}\n"
    for i, (r, s, o, c) in enumerate([
        ("1.234560e+02", "-9.870000e-05", "0.000000e+00", "1.000000e+10"), ("5.000000e-01", "2.500000e+00", "-0.000000e+00", "3.141593e+00"),
        ("1.000000000000000055511151231257827e-01", "1.200000e+02", "6.022000e+23", "-1.602177e-19"), ("9.999999e+01", "1.000000e+00", "1.000000e+05", "1.5E+02"),
        ("4.200000e+01", "1.100000e-03", "7.000000e+00", "2.000000e+01")] * 3))
_SCI_NEG = ("version 1.2.3e+10 and hash deadbeef1e+05 and 0x1e+05 and id a1.5e+02b\nunits 1.5e+03ms, 2e5, 3.0e-5, 1e+5, 12.50, 007\n"
            "1e5 and 1.5e-3 are already short; so are 1e+5 (one exponent digit)\n")
case(
    988,
    claim="printf-style numbers like 1.234560e+02 (mantissa, signed exponent of 2+ digits) are rewritten to the shorter of the plain decimal (123.456) and the unpadded scientific form (9.87e-5) using digit-string arithmetic, never float; every digit is kept, zero keeps its sign, and identifier-, hex- and unit-adjacent tokens are left alone.",
    loses="the original spelling and the trailing zeros of the mantissa (1.230000e+00 and 1.23 compare equal: printed precision is not preserved), the + and leading zeros of the exponent, and the case of E",
    encode=encode_sci,
    equivalent=_equiv_sci,
    fixtures=lambda: [
        Fixture("solver log with padded mantissas, zeros, 34-digit value", _SOLVER, True),
        Fixture("look-alikes that must stay (hex, identifiers, versions, units)", _SCI_NEG, False),
        Fixture("plain numbers", "total 12.50 items, 007, 1e5, 3.0e-5, -0.0\n", False),
        Fixture("exponent edge cases", "1e+99999999999999999999 2.5e-400 -0.000e+00 +7.0e+01 000123.4500e-02 9e+30 9e+31 1.0e+40\n", None),
        Fixture("zero and sign", "0.000000e+00 -0.000000e+00 +0.0e+00 0e+00\n", None),
    ],
    min_gain=0.15,
)


# ===============================================================================================================
# #1081 Lossless HTML attribute minifier. Equivalent: html.parser sees the same tag / attribute / data sequence once
# boolean attributes, spec defaults and empty class/style are normalised (the oracle owns that table of HTML facts).

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BOOL = {"allowfullscreen", "async", "autofocus", "autoplay", "checked", "controls", "default", "defer", "disabled", "formnovalidate",
         "hidden", "inert", "ismap", "itemscope", "loop", "multiple", "muted", "nomodule", "novalidate", "open", "playsinline",
         "readonly", "required", "reversed", "selected"}
_DEFAULTS = {("script", "type"): ("text/javascript",), ("style", "type"): ("text/css",), ("form", "method"): ("get",)}
_RAWTEXT = {"script", "style", "textarea", "title"}
_ATTR_SRC = r"""[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?"""
_TAG = re.compile(r"<([A-Za-z][A-Za-z0-9:-]*)((?:\s+" + _ATTR_SRC + r")*)\s*(/?)>")
_ATTR = re.compile(r"""\s+([^\s"'<>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")
_UNQUOTABLE = re.compile(r"[A-Za-z0-9_.:/#?%~+@-]+")


def _rewrite_tag(m):
    name, attr_src, slash = m.group(1), m.group(2), m.group(3)
    lname = name.lower()
    found = list(_ATTR.finditer(attr_src))
    if sum(len(a.group(0)) for a in found) != len(attr_src):
        return m.group(0)
    new_slash = "" if lname in _VOID else slash
    parts = []
    for a in found:
        an, av = a.group(1), a.group(2)
        la = an.lower()
        quoted = av is not None and av[0] in "\"'"
        v = av[1:-1] if quoted else av
        if av is not None and (lname, la) in _DEFAULTS and v == _DEFAULTS[(lname, la)][0]:
            continue  # the attribute's default value
        if av is not None and la in ("class", "style") and v == "":
            continue  # empty class / style equals none
        if av is not None and la in _BOOL and (v == "" or v.lower() == la):
            parts.append(an)
        elif quoted and not new_slash and _UNQUOTABLE.fullmatch(v) and not v.endswith("/"):
            parts.append(f"{an}={v}")
        elif av is None:
            parts.append(an)
        else:
            parts.append(f"{an}={av}")
    out = f"<{name}" + "".join(" " + p for p in parts) + (" />" if new_slash else ">")
    return out if len(out) < len(m.group(0)) else m.group(0)


@_total
def encode_html(text):
    out, i, n = [], 0, len(text)
    while True:
        j = text.find("<", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        i = j
        if text.startswith("<!--", i) or text.startswith("<![CDATA[", i):
            end = "-->" if text.startswith("<!--", i) else "]]>"
            k = text.find(end, i + 4)
            k = n if k < 0 else k + len(end)
            out.append(text[i:k])
            i = k
            continue
        m = _TAG.match(text, i)
        if not m:
            if text[i + 1:i + 2].isalpha():
                out.append(text[i:])  # a start tag we cannot parse may swallow what follows (html.parser is tolerant): stop here
                break
            out.append("<")
            i += 1
            continue
        out.append(_rewrite_tag(m))
        i = m.end()
        lname = m.group(1).lower()
        if lname in _RAWTEXT and not m.group(3):
            close = re.compile(r"</" + lname + r"(?=[\s/>])", re.I).search(text, i)
            k = close.start() if close else n
            out.append(text[i:k])  # raw text is never rewritten
            i = k
    return "".join(out)


# the oracle's own copy of the HTML facts (WHATWG): it must not share the encoder's tables
_O_BOOL = frozenset("allowfullscreen async autofocus autoplay checked controls default defer disabled formnovalidate hidden inert ismap "
                    "itemscope loop multiple muted nomodule novalidate open playsinline readonly required reversed selected".split())
_O_DEFAULTS = {("script", "type"): "text/javascript", ("style", "type"): "text/css", ("form", "method"): "get"}
_O_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class _Events(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ev, self.buf = [], []

    def _flush(self):
        if self.buf:
            self.ev.append(("data", "".join(self.buf)))
            self.buf.clear()

    @staticmethod
    def _norm(tag, attrs):
        out = []
        for k, v in attrs:
            if k in _O_BOOL and (v is None or v == "" or v.lower() == k):
                v = None  # present is present, however it is spelled
            if k in ("class", "style") and v == "":
                continue
            if v is not None and _O_DEFAULTS.get((tag, k)) == v.lower():
                continue
            out.append((k, v))
        return tuple(out)

    def handle_starttag(self, tag, attrs):
        self._flush()
        self.ev.append(("start", tag, self._norm(tag, attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _O_VOID:
            self.ev.append(("end", tag))

    def handle_endtag(self, tag):
        self._flush()
        self.ev.append(("end", tag))

    def handle_data(self, data):
        self.buf.append(data)

    def handle_comment(self, data):
        self._flush()
        self.ev.append(("comment", data))

    def handle_decl(self, decl):
        self._flush()
        self.ev.append(("decl", decl))

    def handle_pi(self, data):
        self._flush()
        self.ev.append(("pi", data))

    def unknown_decl(self, data):
        self._flush()
        self.ev.append(("unknown", data))


def _html_events(text):
    p = _Events()
    p.feed(text)
    p.close()
    p._flush()
    return p.ev


def _equiv_html(a, b):
    if a == b:
        return True
    try:
        return _html_events(a) == _html_events(b)
    except Exception:  # noqa: BLE001
        return False


_FORM = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8" />\n  <link rel="stylesheet" href="/static/app.css" type="text/css" />\n'
    '  <script type="text/javascript" src="/static/app.js"></script>\n  <title>Sign up &amp; go</title>\n</head>\n<body class="" id="top">\n'
    '  <form method="get" action="/signup" class="form form-wide" novalidate="novalidate" style="">\n'
    '    <label for="email">Email</label>\n    <input type="text" id="email" name="email" value="" required="required" autofocus="autofocus" />\n'
    '    <input type="checkbox" name="tos" checked="checked" disabled="disabled">\n    <select name="plan" multiple="multiple">\n'
    '      <option value="free" selected="selected">Free</option>\n      <option value="pro">Pro &mdash; $9</option>\n    </select>\n'
    '    <img src="/logo.png" alt="" width="32" height="32" />\n    <br />\n    <button type="submit" class="btn btn-primary" disabled="disabled">Go</button>\n'
    '  </form>\n  <a href="https://x.test/a?b=1&amp;c=2" title="a > b">more</a>\n  <p data-note=\'single quoted\'>text</p>\n</body>\n</html>\n')
_MIN_HTML = '<!DOCTYPE html>\n<html lang=en>\n<head>\n<meta charset=utf-8>\n</head>\n<body>\n<form action=/signup>\n<input name=email required>\n</form>\n</body>\n</html>\n'
_TRICKY = (
    '<script>var s = "<a href=\\"x\\" class=\\"y\\" disabled=\\"disabled\\" />";</script>\n<style>a[href="x"] { color: red }</style>\n'
    '<textarea><input disabled="disabled" /></textarea>\n<!-- <br /> <input checked="checked"> -->\n<![CDATA[ <br /> ]]>\n'
    '<svg><path d="M0 0" /><circle r="1" /></svg>\n<DIV CLASS="A" ID="b"/>\n<a href="x/" id = "y">k</a>\n<p class="a" class="b" hidden="false">dup</p>\n'
    '<input value="a>b" title=\'q"r\' disabled = "DISABLED">\n<a href=x/ >\n<br class=a/>\n<p\nclass="multi"\n  id="line">\n<b "weird" =x>\n<a href="x"title="y">\n')
case(
    1081,
    claim="Start tags are rewritten with boolean attributes bare (disabled=\"disabled\" to disabled), simple quoted values unquoted, default attributes (script type=text/javascript, style type=text/css, form method=get) and empty class/style dropped and the / of void elements removed; comments, CDATA, script, style, textarea and title content, any tag the pattern does not recognise and everything after such a tag are untouched, and html.parser yields the same event sequence.",
    loses="attribute quote style, whitespace inside tags, the trailing / on void tags, and attributes equal to their HTML default or empty class/style",
    encode=encode_html,
    equivalent=_equiv_html,
    fixtures=lambda: [
        Fixture("form page with booleans, defaults, quotes and void slashes", _FORM, True),
        Fixture("already minimal HTML", _MIN_HTML, False),
        Fixture("empty alt and value are meaningful", '<img src=a.png alt="">\n<input name=q value="">\n<a href="">x</a>\n', False),
        Fixture("no markup", "plain text with a < b and c > d\n", False),
        Fixture("raw text, comments, odd and malformed tags", _TRICKY, None),
    ],
    min_gain=0.1,
)


# ===============================================================================================================
# #1167 Decode JSON arrays of byte values to text. Roundtrip: only canonical compact arrays under "MESSAGE" or
# {"type":"Buffer","data":...} whose bytes are strict UTF-8 text; the encoded string carries a sigil so decode knows.

_BYTES = r"\[((?:0|[1-9]\d{0,2})(?:,(?:0|[1-9]\d{0,2})){7,})\]"
_MSG_ARR = re.compile(r'("MESSAGE":)' + _BYTES)
_BUF_ARR = re.compile(r'(\{"type":"Buffer","data":)' + _BYTES)
_MSG_STR = re.compile(r'("MESSAGE":)"§((?:[^"\\]|\\.)*)"')
_BUF_STR = re.compile(r'(\{"type":"Buffer","data":)"§((?:[^"\\]|\\.)*)"')


def _bytes_to_lexeme(nums):
    vals = nums.split(",")
    if any(int(v) > 255 for v in vals):
        return None
    try:
        text = bytes(int(v) for v in vals).decode("utf-8")
    except UnicodeDecodeError:
        return None
    bad = [c for c in text if (not c.isprintable()) and c not in "\n\t\r\x1b"]
    if bad or sum(c in "\r\x1b" for c in text) > len(text) // 4:
        return None  # not text-like: binary data stays as numbers
    lex = json.dumps("§" + text, ensure_ascii=False)
    return lex if len(lex) < len(nums) + 2 else None


def _fold_bytes(text):
    def sub(m):
        lex = _bytes_to_lexeme(m.group(2))
        return m.group(0) if lex is None else m.group(1) + lex

    body = _BUF_ARR.sub(sub, _MSG_ARR.sub(sub, text))
    return None if body == text else body


def _unfold_bytes(body):
    def sub(m):
        text = json.loads('"' + m.group(2) + '"')
        return m.group(1) + "[" + ",".join(str(b) for b in text.encode("utf-8")) + "]"

    return _BUF_STR.sub(sub, _MSG_STR.sub(sub, body))


@_failopen
def encode_bytes(text):
    return _esc_encode(text, "§b\n", _fold_bytes, strict_inner=True)


def decode_bytes(text):
    return _esc_decode(text, "§b\n", _unfold_bytes)


def _arr(s):
    return "[" + ",".join(str(b) for b in s.encode("utf-8")) + "]"


_JOURNAL = "".join(
    json.dumps({"__REALTIME_TIMESTAMP": str(1700000000000000 + i), "_SYSTEMD_UNIT": "app.service", "PRIORITY": "3"}, separators=(",", ":"))[:-1]
    + ',"MESSAGE":' + _arr(msg) + "}\n"
    for i, msg in enumerate(["\x1b[31mERROR\x1b[0m connection to db-1 refused: retrying in 5s",
                             "\x1b[1;33mWARN\x1b[0m cache cold, rebuilding index (3 of 12 shards)",
                             "café ☃ unicode message — fine"]))
_JOURNAL += '{"PRIORITY":"6","MESSAGE":"plain string message stays","_PID":"41"}\n{"PRIORITY":"3","MESSAGE":[255,254,0,1,2,3,4,5,6,7]}\n'
_BUFFER = json.dumps({"type": "Buffer", "data": list("hello from a node buffer, two lines\nsecond line".encode())}, separators=(",", ":"))
_BUFFER_RESULT = '{"ok":true,"payload":' + _BUFFER + ',"size":52}\n'
case(
    1167,
    claim="A canonical compact array of 8+ byte values under \"MESSAGE\" or {\"type\":\"Buffer\",\"data\":...} that is strict UTF-8 text becomes a sigil-prefixed JSON string; decode regenerates the exact array, so the original bytes come back. Arrays elsewhere, invalid UTF-8, binary-looking data and non-canonical spellings stay as numbers.",
    encode=encode_bytes,
    decode=decode_bytes,
    fixtures=lambda: [
        Fixture("journalctl -o json with coloured MESSAGE arrays", _JOURNAL, True),
        Fixture("Node Buffer.toJSON", _BUFFER_RESULT, True),
        Fixture("ASCII-valid arrays under other keys", '{"values":[72,101,108,108,111,32,119,111,114,108,100],"MESSAGES":[72,101,108,108,111,32,119,111,114,108,100]}\n{"type":"Blob","data":[72,101,108,108,111,32,119,111,114,108,100]}\n{"data":[72,101,108,108,111,32,119,111,114,108,100]}\n', False),
        Fixture("invalid UTF-8 and binary MESSAGE", '{"MESSAGE":[255,254,0,1,2,3,4,5,6,7]}\n{"MESSAGE":[0,1,2,3,4,5,6,7,8,9,10,11]}\n', False),
        Fixture("pretty-printed or non-canonical arrays", '{"MESSAGE": [72, 101, 108, 108, 111, 32, 119, 111, 114, 108, 100]}\n{"MESSAGE":[072,101,108,108,111,32,119,111,114,108,100]}\n', False),
        Fixture("short array", '{"MESSAGE":[72,101,108,108,111]}\n', False),
        Fixture("sigil already present", '{"MESSAGE":"§hello"}\n' + _JOURNAL, None),
        Fixture("text that starts like encoder output", '§b\n{"MESSAGE":"§x"}\n', None),
        Fixture("quotes, backslashes and control bytes inside the text", '{"MESSAGE":' + _arr('say "hi" \\ path\\n\ttab\x1b[0m end') + "}\n", None),
    ],
    min_gain=0.3,
    idempotent=False,
)


# ===============================================================================================================
# #1169 Delta and run-length encode metric value lists. Roundtrip on canonical compact numeric arrays.

_ITEM = r'(?:-?(?:0|[1-9]\d*)(?:\.\d+)?|"-?(?:0|[1-9]\d*)(?:\.\d+)?")'
_SERIES = re.compile(r"\[(" + _ITEM + "(?:," + _ITEM + r"){7,})\]")
_SERIES_ENC = re.compile(r"§([drD])\[([^\]\[]*)\]")


def _runs(tokens_, fmt=lambda t: t):
    out, i = [], 0
    while i < len(tokens_):
        j = i
        while j < len(tokens_) and tokens_[j] == tokens_[i]:
            j += 1
        out += [f"{fmt(tokens_[i])}*{j - i}"] if j - i >= 3 else [fmt(tokens_[i])] * (j - i)
        i = j
    return out


def _fmt_delta(d):
    return f"{'+' if d >= 0 else '-'}{abs(d)}"


def _series_encode(m):
    body = m.group(1)
    items = body.split(",")
    quoted = items[0][0] == '"'
    if any((it[0] == '"') != quoted for it in items):
        return m.group(0)
    best = "§r[" + ",".join(_runs(items)) + "]"
    raw = [it.strip('"') for it in items]
    if all(re.fullmatch(r"-?(?:0|[1-9]\d*)", r) for r in raw) and all(str(int(r)) == r for r in raw):
        vals = [int(r) for r in raw]
        deltas = [b - a for a, b in zip(vals, vals[1:])]
        cand = f"§{'D' if quoted else 'd'}[" + ",".join([str(vals[0])] + _runs(deltas, _fmt_delta)) + "]"
        if len(cand) < len(best):
            best = cand
    return best if len(best) < len(m.group(0)) else m.group(0)


def _series_decode(m):
    mode, body = m.group(1), m.group(2)
    items = body.split(",")
    if mode == "r":
        out = []
        for it in items:
            tok, star, n = it.partition("*")
            out += [tok] * (int(n) if star else 1)
        return "[" + ",".join(out) + "]"
    v, out = int(items[0]), [items[0]]
    for it in items[1:]:
        d, star, n = it.partition("*")
        for _ in range(int(n) if star else 1):
            v += int(d)
            out.append(str(v))
    return "[" + ",".join(f'"{x}"' if mode == "D" else x for x in out) + "]"


def _fold_series(text):
    body = _SERIES.sub(_series_encode, text)
    return None if body == text else body


@_failopen
def encode_series(text):
    return _esc_encode(text, "§dr\n", _fold_series, strict_inner=True)


def decode_series(text):
    return _esc_decode(text, "§dr\n", lambda body: _SERIES_ENC.sub(_series_decode, body))


def _prom_like():
    counter, total = [], 1_204_000
    for i in range(60):
        total += [3, 7, 4, 12, 5][i % 5] + (i // 17)
        counter.append(total)
    plateau = [0] * 20 + [100] * 25 + [250] * 15
    ts = [1700000000 + 15 * i for i in range(60)]
    quoted = [str(v) for v in counter[:30]]
    return json.dumps({"status": "success", "timestamps": ts, "http_requests_total": counter, "queue_depth": plateau,
                       "as_strings": quoted, "ratio": [0.5] * 10 + [0.25] * 10}, separators=(",", ":")) + "\n"


case(
    1169,
    claim="Canonical compact numeric arrays of 8+ items (bare or quoted) are rewritten as first value plus signed deltas with 'x*N' runs, or as raw tokens with runs, whichever is shorter; decode rebuilds the exact array text, including quotes and decimals.",
    encode=encode_series,
    decode=decode_series,
    fixtures=lambda: [
        Fixture("counter series, timestamps, plateau and quoted values", _prom_like(), True),
        Fixture("noisy small integers", '{"v":[3,1,4,1,5,9,2,6,5,3,5,8,9,7,9,3]}\n', False),
        Fixture("short arrays and strings", '{"v":[1,2,3],"s":["a","b","c","d","e","f","g","h"]}\n', False),
        Fixture("non-canonical numbers", '{"v":[1,2,3,4,5,6,7,8.0,9],"w":[01,2,3,4,5,6,7,8],"x":[1e5,1e5,1e5,1e5,1e5,1e5,1e5,1e5]}\n', False),
        Fixture("negative, zero and big values", '{"v":[-5,-5,-5,-5,0,0,0,0,9007199254740993,9007199254740994,-0,-1]}\n{"w":[-3,-2,-1,0,1,2,3,4,5]}\n', None),
        Fixture("mixed quoting and decimals", '{"v":["1",2,"3",4,5,6,7,8],"d":[0.5,0.5,0.5,0.5,1.25,1.25,1.25,1.25,-0.0,-0.0]}\n', None),
        Fixture("text that starts like encoder output", '§dr\n§d[1,+1*7]\n', None),
        Fixture("sigil in the middle", _prom_like() + "§d[1]\n", None),
    ],
    min_gain=0.5,
    idempotent=False,
)


# ===============================================================================================================
# #1234 docker compose config: re-shorten long-form ports, volumes and depends_on. Equivalent: a YAML-subset parser
# plus a normaliser that expands every short form to its long form (the reverse direction of the encoder) gives the
# same model for both texts.

_PORT_KEYS = {"mode", "host_ip", "target", "published", "protocol"}


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _item_fields(lines):
    """Parse one list item ('      - k: v', then '        k: v'; deeper lines belong to the previous key) into
    {key: [value, nested_lines]}; None when the item is not plain key/value text."""
    fields, key = {}, None
    for n, ln in enumerate(lines):
        if n == 0:
            if not ln.startswith("      - "):
                return None
            body, nested = ln[8:], False
        else:
            ind = len(ln) - len(ln.lstrip(" "))
            if ind < 8:
                return None
            body, nested = ln.strip(), ind > 8
        if not nested:
            m = re.fullmatch(r"([a-z_]+):(?: (.*))?", body)
            if not m or m.group(1) in fields:
                return None
            key = m.group(1)
            fields[key] = [m.group(2) or "", []]
        elif key is not None and fields[key][0] == "":
            fields[key][1].append(body)  # only a key with an empty value ('bind:') may own nested lines
        else:
            return None
    return fields


def _short_port(f):
    if not set(f) <= _PORT_KEYS or any(f[k][1] for k in f) or "target" not in f:
        return None
    if f.get("mode", ["ingress"])[0] != "ingress" or f.get("protocol", ["tcp"])[0] not in ("tcp", "udp"):
        return None
    tgt = _scalar(f["target"][0])
    pub = _scalar(f["published"][0]) if "published" in f else None
    host = _scalar(f["host_ip"][0]) if "host_ip" in f else None
    rng = r"\d+(?:-\d+)?"
    if not re.fullmatch(rng, tgt) or (pub is not None and not re.fullmatch(rng, pub)):
        return None
    if host is not None and (pub is None or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host)):
        return None
    proto = "/udp" if f.get("protocol", ["tcp"])[0] == "udp" else ""
    return '"' + (f"{host}:" if host else "") + (f"{pub}:" if pub else "") + tgt + proto + '"'


def _short_volume(f):
    kind = f.get("type", [""])[0]
    allowed = {"type", "source", "target", "read_only", kind}
    if kind not in ("bind", "volume") or not set(f) <= allowed or "source" not in f or "target" not in f:
        return None
    sub = f.get(kind)
    if sub is not None:
        want = ("create_host_path: true",) if kind == "bind" else ()
        if tuple(sub[1]) != want or sub[0] not in ("", "{}") or (kind == "bind" and sub[0] != "") or (kind == "volume" and sub[0] != "{}"):
            return None
    if any(f[k][1] for k in f if k != kind):
        return None
    ro = f.get("read_only", ["false"])[0]
    if ro not in ("true", "false"):
        return None
    src, tgt = f["source"][0], f["target"][0]
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", src) or not re.fullmatch(r"/[A-Za-z0-9_./-]*", tgt):
        return None
    if (kind == "bind") != src.startswith("/") or (kind == "volume" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", src)):
        return None
    return f"{src}:{tgt}" + (":ro" if ro == "true" else "")


def _items(block):
    """Split a service-level list block into items (list of line lists), or None."""
    items = []
    for ln in block:
        if ln.startswith("      - "):
            items.append([ln])
        elif items and ln.startswith("        "):
            items[-1].append(ln)
        else:
            return None
    return items


def _clean_yaml(lines):
    """Compose config prints strictly nested 2-space YAML: no tabs, CRs, blanks or comments, and an indent only ever
    grows by 2 after a line that opens a block ('key:' or a '- ' item), and dedents land on an open level."""
    levels, prev = [0], None
    for ln in lines:
        if not ln.strip() or "\t" in ln or "\r" in ln or ln.lstrip().startswith("#"):
            return False
        ind, body = len(ln) - len(ln.lstrip(" ")), ln.strip()
        if prev is not None:
            if ind > levels[-1]:
                if ind != levels[-1] + 2 or not re.fullmatch(r"(?:- )?[^\s:][^:]*:", prev):
                    return False
                levels.append(ind)
            else:
                while levels and ind < levels[-1]:
                    levels.pop()
                if not levels or ind != levels[-1]:
                    return False
        elif ind:
            return False
        if body.startswith("- ") and re.match(r"- [\w.-]+:(?: |$)", body):
            levels.append(ind + 2)  # keys of a '- k: v' item continue two columns in
        prev = body
    return True


@_total
def encode_compose(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
        tail = "\n"
    else:
        tail = ""
    if not _clean_yaml(lines):
        return text
    out, i, top = [], 0, ""
    while i < len(lines):
        ln = lines[i]
        if re.match(r"\S", ln):
            top = ln.split(":")[0]
        m = re.fullmatch(r"    (ports|volumes|depends_on):", ln) if top == "services" else None
        if not m:
            out.append(ln)
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].startswith("     "):
            j += 1
        block, key = lines[i + 1:j], m.group(1)
        out.append(ln)
        if key == "depends_on":
            names, ok = [], bool(block)
            k = 0
            while ok and k < len(block):
                nm = re.fullmatch(r"      ([\w.-]+):", block[k])
                body = block[k + 1:k + 3]
                if not nm or body not in (["        condition: service_started", "        required: true"], ["        condition: service_started"]):
                    ok = False
                    break
                names.append(nm.group(1))
                k += 1 + len(body)
            out += [f"      - {n}" for n in names] if ok else block
        else:
            items = _items(block)
            conv = _short_port if key == "ports" else _short_volume
            if items is None:
                out += block
            else:
                for item in items:
                    f = _item_fields(item)
                    short = conv(f) if f else None
                    out += [f"      - {short}"] if short else item
        i = j
    return "\n".join(out) + tail


# ---- oracle: tiny YAML subset parser + model normaliser ---------------------------------------------------------

def _yaml_lines(text):
    return [(len(ln) - len(ln.lstrip(" ")), ln.strip()) for ln in text.split("\n") if ln.strip() and not ln.strip().startswith("#")]


def _yaml_value(v):
    if v == "{}":
        return {}
    if v == "[]":
        return []
    if v in ("null", "~"):
        return None
    return _scalar(v)


def _yaml_block(ls, i, ind):
    if ls[i][1].startswith("- ") or ls[i][1] == "-":
        res = []
        while i < len(ls) and ls[i][0] == ind and (ls[i][1].startswith("- ") or ls[i][1] == "-"):
            rest = ls[i][1][2:].strip()
            if re.match(r"[\w.-]+:(?: |$)", rest):
                ls[i] = (ind + 2, rest)
                item, i = _yaml_block(ls, i, ind + 2)
                res.append(item)
            else:
                res.append(_yaml_value(rest) if rest else None)
                i += 1
        return res, i
    res = {}
    while i < len(ls) and ls[i][0] == ind and not ls[i][1].startswith("- "):
        m = re.fullmatch(r"([^\s:][^:]*?):(?: +(.*))?", ls[i][1])
        if not m:
            res[ls[i][1]] = None
            i += 1
            continue
        key, val = m.group(1), m.group(2)
        if val:
            res[key] = _yaml_value(val)
            i += 1
        elif i + 1 < len(ls) and (ls[i + 1][0] > ind or (ls[i + 1][0] == ind and ls[i + 1][1].startswith("- "))):
            res[key], i = _yaml_block(ls, i + 1, ls[i + 1][0])
        else:
            res[key] = None
            i += 1
    return res, i


def _yaml_parse(text):
    ls, i, chunks = _yaml_lines(text), 0, []
    while i < len(ls):
        node, j = _yaml_block(ls, i, ls[i][0])
        chunks.append(node)
        i = max(j, i + 1)
    return chunks


def _norm_port(p):
    if isinstance(p, str):
        proto = "tcp"
        if "/" in p:
            p, proto = p.rsplit("/", 1)
        parts = p.split(":")
        d = {"target": parts[-1], "protocol": proto, "mode": "ingress"}
        if len(parts) >= 2:
            d["published"] = parts[-2]
        if len(parts) == 3:
            d["host_ip"] = parts[0]
        return d
    if isinstance(p, dict):
        d = {k: (str(v) if v is not None else None) for k, v in p.items()}
        d.setdefault("mode", "ingress")
        d.setdefault("protocol", "tcp")
        return d
    return p


def _norm_volume(v):
    if isinstance(v, str):
        parts = v.split(":")
        if len(parts) in (2, 3):
            return {"type": "bind" if parts[0][:1] in "/.~" else "volume", "source": parts[0], "target": parts[1],
                    **({"read_only": "true"} if len(parts) == 3 and parts[2] == "ro" else {})}
        return v
    if isinstance(v, dict):
        d = {k: x for k, x in v.items()}
        if d.get("read_only") == "false":
            del d["read_only"]
        if d.get("bind") == {"create_host_path": "true"} or d.get("bind") == {"create_host_path": True}:
            del d["bind"]
        if d.get("volume") == {}:
            del d["volume"]
        return d
    return v


def _norm_dep(d):
    if isinstance(d, list):
        return {n: {"condition": "service_started", "required": "true"} for n in d}
    if isinstance(d, dict):
        return {n: {**{"condition": "service_started", "required": "true"}, **(v if isinstance(v, dict) else {})} for n, v in d.items()}
    return d


def _norm_compose(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            v = _norm_compose(v)
            if k == "ports" and isinstance(v, list):
                v = [_norm_port(p) for p in v]
            elif k == "volumes" and isinstance(v, list):
                v = [_norm_volume(x) for x in v]
            elif k == "depends_on":
                v = _norm_dep(v)
            out[k] = v
        return out
    if isinstance(node, list):
        return [_norm_compose(x) for x in node]
    return node


def _equiv_compose(a, b):
    return a == b or _norm_compose(_yaml_parse(a)) == _norm_compose(_yaml_parse(b))


_COMPOSE = """name: shop
services:
  api:
    build:
      context: /home/dev/shop/api
      dockerfile: Dockerfile
    depends_on:
      cache:
        condition: service_started
        required: true
      db:
        condition: service_healthy
        required: true
    environment:
      DATABASE_URL: postgres://shop:shop@db:5432/shop
    networks:
      default: null
    ports:
      - mode: ingress
        target: 8080
        published: "8080"
        protocol: tcp
      - mode: ingress
        host_ip: 127.0.0.1
        target: 9090
        published: "9090"
        protocol: tcp
      - mode: ingress
        target: 5353
        published: "5353"
        protocol: udp
      - mode: host
        target: 443
        published: "443"
        protocol: tcp
    volumes:
      - type: bind
        source: /home/dev/shop/api/src
        target: /app/src
        bind:
          create_host_path: true
      - type: bind
        source: /home/dev/shop/certs
        target: /certs
        read_only: true
        bind:
          create_host_path: true
      - type: bind
        source: /var/run/docker.sock
        target: /var/run/docker.sock
        bind:
          propagation: rshared
          create_host_path: true
      - type: volume
        source: api_cache
        target: /var/cache/api
        volume: {}
      - type: volume
        source: api_logs
        target: /var/log/api
        volume:
          nocopy: true
      - type: tmpfs
        target: /tmp
  db:
    image: postgres:16
    networks:
      default: null
    ports:
      - mode: ingress
        target: 5432
        published: "5432"
        protocol: tcp
    volumes:
      - type: volume
        source: db_data
        target: /var/lib/postgresql/data
        volume: {}
  cache:
    depends_on:
      db:
        condition: service_started
        required: true
    image: redis:7
    labels:
      note: "volumes: not a list"
    ports:
      - mode: ingress
        target: 6379
        published: "6379"
        protocol: tcp
networks:
  default:
    name: shop_default
volumes:
  api_cache:
    name: shop_api_cache
  db_data:
    name: shop_db_data
"""
_COMPOSE_SHORT = """name: app
services:
  web:
    image: nginx
    ports:
      - "80:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
    depends_on:
      - db
"""
case(
    1234,
    claim="In docker compose config output, long-form ports (mode ingress, protocol tcp/udp, optional IPv4 host_ip), bind and named-volume mounts (default bind/volume options only, optional read_only) and depends_on entries with the default condition become short syntax; any item with a non-default sub-field (mode host, propagation, nocopy, tmpfs, condition service_healthy) stays in long form, and the resolved model is the same.",
    loses="long-form defaults that short syntax implies: mode: ingress, protocol: tcp, create_host_path: true, an empty volume: {}, condition: service_started and required: true",
    encode=encode_compose,
    equivalent=_equiv_compose,
    fixtures=lambda: [
        Fixture("compose config with ports, volumes and depends_on", _COMPOSE, True),
        Fixture("already short syntax", _COMPOSE_SHORT, False),
        Fixture("only non-default long forms", "services:\n  a:\n    ports:\n      - mode: host\n        target: 80\n        published: \"80\"\n        protocol: tcp\n    depends_on:\n      b:\n        condition: service_healthy\n        required: true\n", False),
        Fixture("not compose", "ports:\n  - 80\nvolumes: 3\n", False),
        Fixture("CRLF compose", _COMPOSE.replace("\n", "\r\n"), None),
        Fixture("single-quoted and bare published values", _COMPOSE.replace('published: "8080"', "published: '8080'").replace('published: "9090"', "published: 9090"), None),
        Fixture("tab-indented line", _COMPOSE.replace("  db:\n    image", "  db:\n\timage"), None),
    ],
    min_gain=0.25,
    mutations=30,
)


# ===============================================================================================================
# #1245 Drop redundant xmlns redeclarations. Equivalent: the expat-level infoset is identical (expanded names, attributes,
# the in-scope namespace map of every element, text, comments, PIs). ElementTree.canonicalize is not used as the oracle:
# its C14N 2.0 writer mis-prints xmlns="" and merges two prefixes bound to one URI. The encoder is a start-tag scanner
# with a namespace scope stack, applied only to well-formed XML.

_XTOK = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|</[^\s>]+\s*>"
    r"|<([^\s/>!?]+)((?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)(\s*)(/?)>", re.S)
_XATTR = re.compile(r"""\s+([^\s=/>]+)\s*=\s*("[^"]*"|'[^']*')""")


@_total
def encode_xmlns(text):
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return text
    ET.fromstring(text)  # only well-formed documents
    out, stack, scope, pos, changed = [], [], {}, 0, False
    for m in _XTOK.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        if m.group(1) is None:
            if m.group(0).startswith("</"):
                scope = stack.pop()
            out.append(m.group(0))
            continue
        name, attrs, ws, slash = m.group(1), m.group(2), m.group(3), m.group(4)
        local = dict(scope)
        kept = []
        for a in _XATTR.finditer(attrs):
            an, av = a.group(1), a.group(2)[1:-1]
            if an == "xmlns" or an.startswith("xmlns:"):
                prefix = "" if an == "xmlns" else an[6:]
                if av and local.get(prefix) == av:
                    changed = True
                    continue  # redeclares the binding already in scope (xmlns="" undeclarations are always kept)
                local[prefix] = av
            kept.append(a.group(0))
        out.append(f"<{name}{''.join(kept)}{ws}{slash}>")
        if not slash:
            stack.append(scope)
            scope = local
    out.append(text[pos:])
    return "".join(out) if changed else text


def _xml_infoset(text):
    """Expat-level infoset: per element its expanded name, attributes and the full in-scope namespace map, plus
    text, tail, comments and processing instructions in document order."""
    scope, undo, seq = {}, [], []
    root = None
    for ev, obj in ET.iterparse(io.StringIO(text), events=("start-ns", "end-ns", "start", "comment", "pi")):
        if ev == "start-ns":
            undo.append((obj[0], scope.get(obj[0])))
            scope[obj[0]] = obj[1]
        elif ev == "end-ns":
            prefix, old = undo.pop()
            if old is None:
                scope.pop(prefix, None)
            else:
                scope[prefix] = old
        elif ev == "start":
            root = obj if root is None else root
            ns = {k: v for k, v in scope.items() if not (k == "" and v == "")}  # xmlns="" equals 'no default namespace'
            seq.append(("start", obj.tag, tuple(sorted(obj.attrib.items())), tuple(sorted(ns.items()))))
        else:
            seq.append((ev, obj.text if ev == "comment" else obj))
    return seq, [(e.tag, e.text, e.tail) for e in root.iter()]


def _equiv_xml(a, b):
    if a == b:
        return True
    try:
        return _xml_infoset(a) == _xml_infoset(b)
    except ET.ParseError:
        return False


def _soap(n=10):
    orders = "".join(
        f'  <Order xmlns="urn:shop:orders" id="{i}">\n    <Customer xmlns="urn:shop:orders"><Name xmlns="urn:shop:orders">C{i}</Name></Customer>\n'
        f'    <Total xmlns="urn:shop:orders" currency="EUR">{i * 10}.50</Total>\n    <Note xmlns="urn:other" xmlns:o="urn:shop:orders">hi</Note>\n  </Order>\n'
        for i in range(n))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">\n<s:Body xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">\n'
            '<Orders xmlns="urn:shop:orders" xmlns:x="urn:x">\n' + orders + "</Orders>\n</s:Body>\n</s:Envelope>\n")
_XML_SCOPES = ('<a xmlns="u" xmlns:p="1"><b xmlns="v" xmlns:p="2"><c xmlns="v" xmlns:p="2"/><d xmlns:p="1"/></b><e xmlns="u" xmlns:p="1"/>'
               '<f xmlns=""/><g xmlns="" xmlns:q=\'3\'><h xmlns:q="3"/></g></a>')
case(
    1245,
    claim="An xmlns or xmlns:prefix declaration whose non-empty value equals the binding already in scope is removed from the start tag (the scope stack is rebuilt per element); every other byte, including comments, CDATA, text and attribute layout, is kept, and the parsed infoset (element and attribute names, in-scope namespace map of every element, text, comments) of both texts is identical.",
    loses="redundant namespace declarations only (they are not part of the infoset); documents with a DOCTYPE or that are not well-formed are left untouched",
    encode=encode_xmlns,
    equivalent=_equiv_xml,
    fixtures=lambda: [
        Fixture("SOAP-style response, 10 orders with redeclared default xmlns", _soap(), True),
        Fixture("scope changes: rebinding, undeclaring, siblings", _XML_SCOPES, None),
        Fixture("declaration after a closed sibling that rebound the namespace", '<a xmlns="u"><b xmlns="v"><x/></b><c xmlns="v"><d xmlns="v"/></c><e xmlns="u"/></a>', None),
        Fixture("every declaration is needed", '<a xmlns="u"><b xmlns="v"><c xmlns="u"/></b></a>', False),
        Fixture("not XML", "xmlns=\"u\" is just text here\n", False),
        Fixture("comments and CDATA that mention xmlns", '<a xmlns="u"><!-- <b xmlns="u"/> --><![CDATA[<c xmlns="u"/>]]><d xmlns="v"/></a>', False),
        Fixture("DOCTYPE document", '<!DOCTYPE a [<!ENTITY e "x">]><a xmlns="u"><b xmlns="u">&e;</b></a>', None),
    ],
    min_gain=0.25,
    mutations=30,
)


# ===============================================================================================================
# #1248 Column-oriented dict-of-dicts (pandas to_json / to_dict) to a table. Roundtrip: a TSV of raw lexemes with an
# index column; decode rebuilds the exact compact JSON (or the exact Python repr).

_JS = r'"(?:[^"\\\x00-\x1f]|\\.)*"'
_JV = rf"(?:{_JS}|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|null|true|false)"
_PS = r"""(?:'(?:[^'\\\t\n]|\\.)*'|"(?:[^"\\\t\n]|\\.)*")"""
_PV = rf"(?:{_PS}|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?|-?nan|-?inf|None|True|False)"
_PK = rf"(?:{_PS}|-?\d+)"


def _parse_frame(text, py):
    s = text[:-1] if text.endswith("\n") else text
    key = re.compile(_PK if py else _JS)
    val = re.compile(_PV if py else _JV)
    kv, sep = (": ", ", ") if py else (":", ",")
    pos, cols, idx, cells = 0, [], None, []
    if not s.startswith("{"):
        return None
    pos = 1
    while True:
        m = key.match(s, pos)
        if not m or s[m.end():m.end() + len(kv) + 1] != kv + "{":
            return None
        cols.append(m.group(0))
        pos = m.end() + len(kv) + 1
        keys, vals = [], []
        while True:
            mk = key.match(s, pos)
            if not mk or s[mk.end():mk.end() + len(kv)] != kv:
                return None
            mv = val.match(s, mk.end() + len(kv))
            if not mv:
                return None
            keys.append(mk.group(0))
            vals.append(mv.group(0))
            pos = mv.end()
            if s.startswith(sep, pos):
                pos += len(sep)
                continue
            if s.startswith("}", pos):
                pos += 1
                break
            return None
        if idx is None:
            idx = keys
        elif keys != idx:
            return None
        cells.append(vals)
        if s.startswith(sep, pos):
            pos += len(sep)
            continue
        if s.startswith("}", pos) and pos + 1 == len(s):
            break
        return None
    if len(set(cols)) != len(cols) or len(set(idx)) != len(idx):
        return None
    return cols, idx, cells


def _fold_frame(text):
    for py in (False, True):
        parsed = _parse_frame(text, py)
        if parsed:
            break
    else:
        return None
    cols, idx, cells = parsed
    strip = (lambda k: k) if py else (lambda k: k[1:-1])
    nl = " nl" if text.endswith("\n") else ""
    head = "\t".join([""] + [strip(c) for c in cols])
    rows = ["\t".join([strip(k)] + [cells[c][r] for c in range(len(cols))]) for r, k in enumerate(idx)]
    return f"{'py' if py else 'json'}{nl}\n" + head + "".join("\n" + r for r in rows)


def _unfold_frame(body):
    sig, _, rest = body.partition("\n")
    py, nl = sig.startswith("py"), sig.endswith(" nl")
    lines = [ln.split("\t") for ln in rest.split("\n")]
    cols, rows = lines[0][1:], lines[1:]
    q = (lambda k: k) if py else (lambda k: f'"{k}"')
    kv, sep = (": ", ", ") if py else (":", ",")
    cs = sep.join(f"{q(c)}{kv}{{" + sep.join(f"{q(r[0])}{kv}{r[1 + ci]}" for r in rows) + "}" for ci, c in enumerate(cols))
    return "{" + cs + "}" + ("\n" if nl else "")


@_failopen
def encode_frame(text):
    return _esc_encode(text, "§frame ", _fold_frame)


def decode_frame(text):
    return _esc_decode(text, "§frame ", _unfold_frame)


def _frame_json(rows=40, trailing=""):
    cols = {"price": lambda i: json.dumps(i * 1.25 if i % 3 else float(i)), "name": lambda i: json.dumps(f"item \"{i}\" caf\u00e9/\t" if i % 7 == 0 else f"item-{i}", ensure_ascii=False),
            "stock": lambda i: "null" if i % 11 == 0 else str(i * 3)}
    body = ",".join(f'"{c}":{{' + ",".join(f'"{i}":{f(i) if c == "name" else f(i)}' for i in range(rows)) + "}" for c, f in cols.items())
    return "{" + body + "}" + trailing


def _frame_py(rows=30):
    return "{" + ", ".join(f"'{c}': {{" + ", ".join(f"{i}: {f(i)}" for i in range(rows)) + "}" for c, f in
                           {"a": lambda i: i * 2, "b": lambda i: repr(f"x{i}" if i % 5 else "it's"), "c": lambda i: "nan" if i % 9 == 0 else i / 4}.items()) + "}\n"


case(
    1248,
    claim="A pandas {column: {index: value}} document (compact JSON, or the Python repr with ', ' and ': ') in which every column has the same ordered index keys becomes a TSV with an index column and raw cell lexemes; decode rebuilds the exact original text, including a trailing newline, null, escaped strings and nan.",
    encode=encode_frame,
    decode=decode_frame,
    fixtures=lambda: [
        Fixture("df.to_json(), 3 columns x 40 rows", _frame_json(), True),
        Fixture("df.to_json() with trailing newline", _frame_json(20, "\n"), True),
        Fixture("df.to_dict() repr with int index keys", _frame_py(), True),
        Fixture("columns with different index keys", '{"a":{"0":1,"1":2},"b":{"0":1,"2":2}}', False),
        Fixture("row-oriented records", '[{"a":1,"b":2},{"a":3,"b":4}]', False),
        Fixture("nested values", '{"a":{"0":[1,2],"1":[3]},"b":{"0":1,"1":2}}', False),
        Fixture("pretty-printed", json.dumps(json.loads(_frame_json(3)), indent=2), False),
        Fixture("sigil inside string cells", '{"a":{"0":"5§","1":"§x"},"b":{"0":1,"1":2}}', None),
        Fixture("duplicate column names", '{"a":{"0":1,"1":2},"a":{"0":3,"1":4}}', None),
        Fixture("text that starts like encoder output", '§frame json\n\ta\n0\t1', None),
        Fixture("one-row, empty keys and escaped tabs", '{"":{"":"x\\ty"},"b c":{"":1}}', None),
    ],
    min_gain=0.25,
    idempotent=False,
)


# ===============================================================================================================
# #1249 Group consecutive dotted-key lines by shared prefix. Roundtrip: 'prefix: leaf=v leaf=v' lines for runs of 2+
# key=value lines whose values are simple; every other line is verbatim; invert the grouping to get the flat keys.

_KV = re.compile(r"([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\.([A-Za-z0-9_-]+)=(\S*)")


def _fold_dotted(text):
    lines = text.split("\n")
    out, i, grouped = [], 0, False
    while i < len(lines):
        m = _KV.fullmatch(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and (m2 := _KV.fullmatch(lines[j])) and m2.group(1) == m.group(1):
            j += 1
        if j - i >= 2:
            members = [_KV.fullmatch(ln) for ln in lines[i:j]]
            new = f"§{m.group(1)}: " + " ".join(f"{mm.group(2)}={mm.group(3)}" for mm in members)
            if len(new) < len("\n".join(lines[i:j])):
                out.append(new)
                grouped = True
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) if grouped else None


def _unfold_dotted(body):
    out = []
    for ln in body.split("\n"):
        if ln.startswith("§"):
            prefix, _, rest = ln[1:].partition(": ")
            out += [f"{prefix}.{kv}" for kv in rest.split(" ")]
        else:
            out.append(ln)
    return "\n".join(out)


@_failopen
def encode_dotted(text):
    return _esc_encode(text, "§g\n", _fold_dotted, strict_inner=True)


def decode_dotted(text):
    return _esc_decode(text, "§g\n", _unfold_dotted)


_GIT_CONFIG = (
    "core.repositoryformatversion=0\ncore.filemode=true\ncore.bare=false\ncore.logallrefupdates=true\ncore.editor=vim -c 'set paste'\n"
    "user.name=Ada Lovelace\nuser.email=ada@example.com\nuser.signingkey=ABCD1234\n"
    "remote.origin.url=git@github.com:acme/widgets.git\nremote.origin.fetch=+refs/heads/*:refs/remotes/origin/*\n"
    "branch.main.remote=origin\nbranch.main.merge=refs/heads/main\nbranch.dev.remote=origin\nbranch.dev.merge=refs/heads/dev\n"
    "alias.co=checkout\nalias.st=status -sb\nalias.lg=log --oneline --graph\ncommit.gpgsign=true\npull.rebase=true\n")
_SPRING = "".join(f"spring.datasource.{k}={v}\n" for k, v in [("url", "jdbc:postgresql://db:5432/app"), ("username", "app"), ("password", ""), ("driver-class-name", "org.postgresql.Driver"),
                                                              ("hikari.maximum-pool-size", "10"), ("hikari.minimum-idle", "2"), ("hikari.connection-timeout", "30000")]) \
    + "# comment line\nserver.port=8080\nserver.servlet.context-path=/api\nserver.compression.enabled=true\nlogging.level.root=INFO\nlogging.level.org.hibernate=WARN\n"
case(
    1249,
    claim="Runs of 2+ consecutive 'a.b.c=value' lines with the same prefix and whitespace-free values become one '§a.b: c=value d=value' line; lines with spaces in the value, comments and everything else stay verbatim in place, and decode re-expands to the exact original lines.",
    encode=encode_dotted,
    decode=decode_dotted,
    fixtures=lambda: [
        Fixture("git config --list", _GIT_CONFIG, True),
        Fixture("Spring application.properties / env", _SPRING, True),
        Fixture("every prefix appears once", "a.x=1\nb.y=2\nc.z=3\n", False),
        Fixture("values with spaces only", "alias.st=status -sb\nalias.lg=log --graph\n", False),
        Fixture("CRLF properties", _SPRING.replace("\n", "\r\n"), False),
        Fixture("not key=value", "plain text\n\nkey: value\nx = y\n", False),
        Fixture("empty values, = inside values, duplicate keys", "a.b=\na.c==x=\na.b=1\na.d=http://x/?q=1&r=2\n", None),
        Fixture("sigil inside a value", _GIT_CONFIG + "user.note=5§\n", None),
        Fixture("text that starts like encoder output", "§g\n§user: name=a email=b\nuser.x=1\n", None),
    ],
    min_gain=0.06,
    idempotent=False,
)


# ===============================================================================================================
# #1250 Canonicalise ISO-8601 timestamps. Equivalent under exact instant arithmetic; the zero fraction and the
# spelling of UTC are lost; offsets that are not +00:00 / +0000 / +00 are never touched.

_STAMP = re.compile(
    r"(?<![\w/\\.:-])(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|\+00(?::?00)?|[+-]\d{2}(?::?\d{2})?)?(?![\w/\\:-]|\.\w)")


def _valid_stamp(head):
    try:
        datetime.datetime.strptime(head.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False


def _canon_stamp(m):
    head, frac, zone = m.group(1), m.group(2), m.group(3)
    if not _valid_stamp(head):
        return m.group(0)
    if frac is not None and frac.strip("0") == "":
        frac = None
    new = head + (f".{frac}" if frac else "") + ("Z" if zone in ("+00:00", "+0000", "+00") else (zone or ""))
    return new if len(new) < len(m.group(0)) else m.group(0)


@_total
def encode_stamps(text):
    return _STAMP.sub(_canon_stamp, text)


_ANY_STAMP = re.compile(r"(?<![\w/\\.:-])(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?(Z|[+-]\d{2}(?::?\d{2})?)?(?![\w/\\:-]|\.\w)")


def _instant(m):
    """(exact seconds since the epoch as a Fraction, or the naive wall-clock value; kind) for one stamp match."""
    y, mo, d, h, mi, s, frac, zone = m.groups()
    days = datetime.date(int(y), int(mo), int(d)).toordinal()
    sec = Fraction(days * 86400 + int(h) * 3600 + int(mi) * 60 + int(s)) + (Fraction(int(frac), 10 ** len(frac)) if frac else 0)
    if zone is None:
        return ("naive", sec)
    if zone == "Z":
        return ("aware", sec)
    sign = -1 if zone[0] == "-" else 1
    digits = zone[1:].replace(":", "")
    offset = sign * (int(digits[:2]) * 3600 + int(digits[2:] or 0) * 60)
    return ("aware", sec - offset)


def _equiv_stamps(a, b):
    ma, mb = list(_ANY_STAMP.finditer(a)), list(_ANY_STAMP.finditer(b))
    if _ANY_STAMP.sub("\x00", a) != _ANY_STAMP.sub("\x00", b) or len(ma) != len(mb):
        return False  # text around the stamps, or the number of stamps, changed
    for x, y in zip(ma, mb):
        if x.group(0) == y.group(0):
            continue
        try:
            if _instant(x) != _instant(y):
                return False
        except ValueError:
            return False
        if (x.group(8) or "").startswith("-") and x.group(8) != y.group(8):
            return False  # -00:00 means 'offset unknown' and a real offset must not be rewritten at all
    return True


_JIRA = "".join(
    json.dumps({"id": 10000 + i, "created": f"2024-03-{i % 28 + 1:02d}T09:{i % 60:02d}:17.000+0000", "updated": f"2024-04-{i % 28 + 1:02d}T11:{i % 60:02d}:05.000+0000",
                "resolved": None if i % 3 else f"2024-04-{i % 28 + 1:02d}T12:00:00.000+0100"}) + "\n" for i in range(8))
_JS_ISO = "[" + ",".join(f'"2024-05-{i % 28 + 1:02d}T{i % 24:02d}:30:00.000Z"' for i in range(12)) + "]\n"
_PY_ISO = "".join(f"event {i} at 2024-06-0{i % 9 + 1}T08:15:30.000000+00:00 until 2024-06-0{i % 9 + 1} 09:15:30.500000+00:00\n" for i in range(6))
_STAMP_KEEP = ("created 2024-03-05T12:34:56.123Z\nlocal 2024-03-05T12:34:56+02:00 and 2024-03-05T12:34:56-08:00 and 2024-03-05T12:34:56+0530\n"
               "unknown offset 2024-03-05T12:34:56-00:00 and -0000\nfile logs/2024-03-05T12:34:56.000Z/app.log and app-2024-03-05T12:34:56.000Z-1.txt\n"
               "bad 2024-13-45T25:61:61.000Z and 2024-02-30T10:00:00.000Z\n")
case(
    1250,
    claim="ISO-8601 timestamps with seconds lose an all-zero fraction (.000, .000000) and have +00:00, +0000 or +00 spelled Z; non-zero fractions are kept digit for digit, offsets other than UTC (+02:00, -08:00, +0530, -00:00) are never touched, and the exact instant of every timestamp is unchanged.",
    loses="the printed fractional-second precision when it is all zeros, and the +00:00 / +0000 spelling of UTC",
    encode=encode_stamps,
    equivalent=_equiv_stamps,
    fixtures=lambda: [
        Fixture("Jira JSON with +0000 and one +0100 stamp", _JIRA, True),
        Fixture("JS toISOString list", _JS_ISO, True),
        Fixture("Python isoformat with +00:00 and a space separator", _PY_ISO, True),
        Fixture("stamps that must stay", _STAMP_KEEP, False),
        Fixture("already canonical", "2024-03-05T12:34:56Z and 2024-03-05T12:34:56.5Z and 2024-03-05\n", False),
        Fixture("non-zero fractions with UTC offsets", "t=2024-03-05T12:34:56.120+00:00 u=2024-03-05T12:34:56.123456789+0000 v=2024-03-05T12:34:56.000+05:30\n", None),
        Fixture("naive local stamps and leap second", "2024-03-05T12:34:56.000000 2024-06-30T23:59:60.000Z\n", None),
    ],
    min_gain=0.1,
)


# ===============================================================================================================
# #1255 Minify JSON leaves but keep one record per line. Equivalent: the parsed value (types and key order) is the same.

def _lines_json(v):
    """The top-level container and the container-valued members of a top-level object put each child on its own line;
    everything deeper (the records) is compact."""
    if not isinstance(v, (dict, list)) or not v:
        return _dumps(v)
    op, cl = ("[", "]") if isinstance(v, list) else ("{", "}")
    if isinstance(v, list):
        kids = [_dumps(x) for x in v]
    else:
        kids = []
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x:
                a, b = ("[", "]") if isinstance(x, list) else ("{", "}")
                inner = [_dumps(y) for y in x] if isinstance(x, list) else [f"{_dumps(kk)}:{_dumps(y)}" for kk, y in x.items()]
                kids.append(f"{_dumps(k)}:{a}\n" + ",\n".join(inner) + f"\n{b}")
            else:
                kids.append(f"{_dumps(k)}:{_dumps(x)}")
    return op + "\n" + ",\n".join(kids) + "\n" + cl


@_total
def encode_fill(text):
    return _lines_json(_load_strict(text))


def _equiv_fill(a, b):
    if a == b:
        return True
    try:
        before = _canon(_load_strict(a))
    except Exception:  # noqa: BLE001
        return False
    try:
        return before == _canon(json.loads(b))
    except Exception:  # noqa: BLE001
        return False


def _records(n=15):
    return [{"id": i, "login": f"user{i}", "profile": {"name": f"User {i}", "tags": ["a", "b"], "score": i * 1.5}, "active": i % 2 == 0, "note": None} for i in range(n)]


case(
    1255,
    claim="A pretty-printed (or loosely spaced) JSON document is re-emitted with each child of the top-level container, and each child of a container-valued top-level member, on its own line and everything deeper compact; the parsed value, key order and number types are identical, and text that is not JSON, or for which the result would not be shorter (already compact, empty), is returned unchanged.",
    loses="JSON whitespace, number spelling (1e2 vs 100.0) and string escape form; inputs with duplicate keys or NaN/Infinity are left untouched",
    encode=encode_fill,
    equivalent=_equiv_fill,
    fixtures=lambda: [
        Fixture("indent=2 array of 15 records", json.dumps(_records(), indent=2), True),
        Fixture("indent=4 object with items array and meta", json.dumps({"total": 15, "items": _records(8), "meta": {"page": 1, "tags": ["x"]}}, indent=4), True),
        Fixture("one-line compact JSON", _dumps(_records(4)), False),
        Fixture("not JSON", "a: 1\nb: [2, 3]\n", False),
        Fixture("empty container and scalar", "[]", False),
        Fixture("a string scalar", '"just a string"', False),
        Fixture("numbers and escapes", '{\n "a": 1e2,\n "b": 1.0,\n "c": "\\u00e9\\/",\n "d": [true, 1, 1.0, -0.0, 10000000000000000000000]\n}', None),
        Fixture("duplicate keys and NaN", '{"a": 1, "a": 2}\n[NaN, 1]\n', None),
        Fixture("deeply nested", "[" * 150 + "1" + "]" * 150, None),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #1326 Unicode box tables (DuckDB duckbox, polars, Rich/Keras/Lightning) to TSV with dtypes folded into header cells.
# Equivalent: a regex-based re-parse of the box gives the same grid as the TSV.

_TOPS, _BOTS, _VERTS, _SEPS = set("┌┏╭╔"), set("└┗╰╚"), set("│┃┆║"), set("├╞┡╠╟")
_VERT_SPLIT = re.compile("[│┃┆║]")


def _parse_box(block):
    """Block lines (top border .. bottom border) -> (header cells, body rows) or None."""
    ncols, header, body, seen_sep = None, [], [], False
    for ln in block[1:-1]:
        if ln[:1] in _SEPS:
            seen_sep = True
            continue
        if len(ln) < 2 or ln[0] not in _VERTS or ln[-1] not in _VERTS:
            return None
        cells = [c.strip() for c in _VERT_SPLIT.split(ln[1:-1])]
        if "\t" in ln or (ncols is not None and len(cells) != ncols):
            return None
        ncols = len(cells)
        (body if seen_sep else header).append(cells)
    if not seen_sep or not header or ncols is None:
        return None
    if len(header) == 1:
        names = header[0]
    elif len(header) == 2:
        names = [f"{n}:{t}" for n, t in zip(*header)]
    elif len(header) == 3 and all(c == "---" for c in header[1]):
        names = [f"{n}:{t}" for n, t in zip(header[0], header[2])]
    else:
        return None
    return names, body


@_total
def encode_boxes(text):
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        if lines[i][:1] in _TOPS:
            j = i + 1
            while j < n and lines[j][:1] and lines[j][:1] not in _BOTS and (lines[j][:1] in _VERTS or lines[j][:1] in _SEPS):
                j += 1
            if j < n and lines[j][:1] in _BOTS:
                parsed = _parse_box(lines[i:j + 1])
                if parsed:
                    names, body = parsed
                    out += ["\t".join(names)] + ["\t".join(r) for r in body]
                    i = j + 1
                    continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


_BOX_RE = re.compile(r"^[┌┏╭╔][^\n]*(?:\n[│┃┆║├╞┡╠╟][^\n]*)*\n[└┗╰╚][^\n]*$", re.M)


def _equiv_boxes(a, b):
    if a == b:
        return True
    pos, bpos = 0, 0
    for m in _BOX_RE.finditer(a):
        between = a[pos:m.start()]
        if b[bpos:bpos + len(between)] != between:
            return False
        bpos += len(between)
        pos = m.end()
        rows = [ln for ln in m.group(0).split("\n")[1:-1] if ln[0] not in "├╞┡╠╟"]
        seps = [k for k, ln in enumerate(m.group(0).split("\n")[1:-1]) if ln[0] in "├╞┡╠╟"]
        grid = None
        if seps and all(len(r) >= 2 and r[0] in "│┃┆║" and r[-1] in "│┃┆║" and "\t" not in r for r in rows):
            cells = [[c.strip() for c in re.split("[│┃┆║]", r[1:-1])] for r in rows]
            nh = len([k for k in range(len(m.group(0).split("\n")[1:-1])) if k < seps[0]])
            if len({len(c) for c in cells}) == 1 and 1 <= nh <= 3 and nh <= len(cells):
                head, body = cells[:nh], cells[nh:]
                if nh == 1:
                    names = head[0]
                elif nh == 2:
                    names = [f"{x}:{y}" for x, y in zip(*head)]
                else:
                    names = [f"{x}:{y}" for x, y in zip(head[0], head[2])] if set(head[1]) == {"---"} else None
                if names is not None:
                    grid = "\n".join("\t".join(r) for r in [names] + body)
        for candidate in ([grid] if grid is not None else []) + [m.group(0)]:
            if b.startswith(candidate, bpos):
                bpos += len(candidate)
                break
        else:
            return False
    return b[bpos:] == a[pos:]


_DUCKDB = (
    "┌───────┬─────────┬──────────┬────────────┐\n│  id   │  name   │  amount  │    day     │\n│ int32 │ varchar │  double  │    date    │\n"
    "├───────┼─────────┼──────────┼────────────┤\n" + "".join(
        f"│ {i:5d} │ {('user_' + str(i)):<7} │ {(i * 12.5):8.1f} │ 2024-05-{i % 28 + 1:02d} │\n" if i % 4 else f"│ {i:5d} │ {'NULL':<7} │ {'NULL':>8} │ 2024-05-{i % 28 + 1:02d} │\n"
        for i in range(1, 21)).replace("user_", "user_").rstrip("\n") + "\n└───────┴─────────┴──────────┴────────────┘\n20 rows    4 columns\n")
_POLARS = ("shape: (4, 3)\n┌─────┬───────┬───────┐\n│ id  ┆ name  ┆ score │\n│ --- ┆ ---   ┆ ---   │\n│ i64 ┆ str   ┆ f64   │\n"
           "╞═════╪═══════╪═══════╡\n│ 1   ┆ alice ┆ 9.5   │\n│ 2   ┆ bob   ┆ null  │\n│ 3   ┆ carol ┆ 7.25  │\n│ 4   ┆ dave  ┆ 8.0   │\n└─────┴───────┴───────┘\n")
_KERAS = ("Model: \"sequential\"\n┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓\n┃ Layer (type)                    ┃ Output Shape           ┃       Param # ┃\n"
          "┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩\n│ dense (Dense)                   │ (None, 64)             │         1,088 │\n"
          "├─────────────────────────────────┼────────────────────────┼───────────────┤\n│ dense_1 (Dense)                 │ (None, 10)             │           650 │\n"
          "└─────────────────────────────────┴────────────────────────┴───────────────┘\n Total params: 1,738 (6.79 KB)\n")
_ASCII_GRID = "+----+-------+\n| id | name  |\n+----+-------+\n| 1  | alice |\n+----+-------+\n"
case(
    1326,
    claim="A Unicode box table (top border, header rows, a header separator, body rows, bottom border) becomes TSV with one header line (name, or name:dtype when a dtype row follows; polars' --- row dropped) and one line per body row, cells trimmed; ragged, separator-less or tab-containing tables and text around the table are left verbatim.",
    loses="cell padding and alignment (cells are trimmed, so a cell with intentional leading/trailing spaces changes), border characters, extra row separators, and column width",
    encode=encode_boxes,
    equivalent=_equiv_boxes,
    fixtures=lambda: [
        Fixture("DuckDB duckbox, 20 rows with NULLs", _DUCKDB, True),
        Fixture("polars frame with shape line", _POLARS, True),
        Fixture("Keras model summary (heavy header, row separator)", _KERAS, True),
        Fixture("ASCII grid is a different idea (#412)", _ASCII_GRID, False),
        Fixture("box without a header separator", "┌──┬──┐\n│ a│ b│\n└──┴──┘\n", False),
        Fixture("ragged box and CRLF box", "┌──┬──┐\n│ a│ b│\n├──┼──┤\n│ 1│\n└──┴──┘\n" + _POLARS.replace("\n", "\r\n"), False),
        Fixture("box characters used as decoration", "┌ note ┐\nthis is prose\n└ end ┘\n│ not a table │\n", False),
        Fixture("two tables and prose between", _POLARS + "between\n" + _DUCKDB, None),
        Fixture("table with a tab in a cell and an empty row", "┌──┬──┐\n│ a│ b│\n├──┼──┤\n│ x\ty│ 1│\n└──┴──┘\n┌──┬──┐\n│ a│ b│\n├──┼──┤\n│  │  │\n└──┴──┘\n", None),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #1365 Round-trip-verified synthesised encoders: the acceptance gate, as a sim.
#
# A coding model writes encode/decode for one tool from ~10 captures. The gate accepts the pair only if on every
# held-out capture decode(encode(x)) == x byte for byte, nothing raises, the output is deterministic and the tokens
# drop. The decoder is only a proof: at read time the model sees encode(x). A deployed encoder also re-verifies each
# live output (fail open), because the held-out set can miss a rare input.

def accept_pair(encode, decode, heldout, min_captures=5, min_token_drop=0.10):
    """Return (accepted, reason, stats). Fails closed: any exception or doubt rejects the pair."""
    if len(heldout) < min_captures:
        return False, f"only {len(heldout)} held-out captures (< {min_captures})", {}
    tok_before = tok_after = 0
    for k, x in enumerate(heldout):
        try:
            e = encode(x)
            if not isinstance(e, str) or e != encode(x):
                return False, f"capture {k}: encode is not a deterministic str", {}
            d = decode(e)
        except Exception as ex:  # noqa: BLE001
            return False, f"capture {k}: {type(ex).__name__}", {}
        if d != x:
            return False, f"capture {k}: round trip differs", {}
        tok_before += tokens(x)
        tok_after += tokens(e)
    drop = 1 - tok_after / tok_before
    if drop < min_token_drop:
        return False, f"tokens drop only {drop:.1%}", {"token_drop": drop}
    return True, "ok", {"token_drop": drop, "tokens_before": tok_before, "tokens_after": tok_after}


def deployed(encode, decode):
    """What runs per live tool call once a pair is accepted: use the encoding only if it verifies on this very input."""

    def run(x):
        try:
            e = encode(x)
            return e if len(e) < len(x) and decode(e) == x else x
        except Exception:  # noqa: BLE001
            return x

    return run


# a 'synthesised' pair for a hypothetical tool that returns compact JSON rows with a fixed key order
def _rows_encode(text):
    m = re.fullmatch(r'\[(\{.*\})\]\n?', text)
    if not m or not text.endswith("\n"):
        return text
    rows = m.group(1).split("},{")
    keys = re.findall(r'"([a-z_]+)":', rows[0].lstrip("{"))
    cells = []
    for r in rows:
        r = r.strip("{}")
        parts = re.split(r',"[a-z_]+":', "," + r)[1:]
        if len(parts) != len(keys):
            return text
        cells.append("\t".join(parts))
    out = "§rows " + ",".join(keys) + "\n" + "\n".join(cells) + "\n"
    return out if len(out) < len(text) else text


def _rows_decode(text):
    if not text.startswith("§rows "):
        return text
    head, _, body = text.partition("\n")
    keys = head[6:].split(",")
    rows = ["{" + ",".join(f'"{k}":{v}' for k, v in zip(keys, ln.split("\t"))) + "}" for ln in body.rstrip("\n").split("\n")]
    return "[" + ",".join(rows) + "]\n"


def _rows_encode_buggy(text):
    # looks right on typical captures but 'normalises' floats like 2.0 to 2, which mangles a rare value
    return _rows_encode(re.sub(r'(\d)\.0(?=[,}])', r"\1", text))


def _capture(seed, extra=None):
    rows = [{"id": 100 + seed * 10 + i, "name": f"svc-{seed}-{i}", "state": ["up", "down", "warn"][(seed + i) % 3], "load": round(0.05 + 0.1 * (i + seed), 2)}
            for i in range(16)]
    if extra:
        rows[3]["load"] = extra
    return json.dumps(rows, separators=(",", ":")) + "\n"


def sim_synthesised_gate():
    held = [_capture(s) for s in range(6)]
    ok, why, stats = accept_pair(_rows_encode, _rows_decode, held)
    assert ok, f"a correct pair must be accepted: {why}"
    assert stats["token_drop"] > 0.12, "accepted pair must really save tokens"
    saved = stats["tokens_before"] - stats["tokens_after"]

    # (c) subtly wrong pair: identical on every capture except one that contains a float spelled 2.0
    rare = held[:5] + [_capture(7, extra=2.0)]
    assert '"load":2.0' in rare[-1]
    ok_bad, why_bad, _ = accept_pair(_rows_encode_buggy, _rows_decode, rare)
    assert not ok_bad and "round trip" in why_bad, "the wrong pair must be rejected when held-out data contains the rare input"
    ok_plain, _, _ = accept_pair(_rows_encode_buggy, _rows_decode, held)
    assert ok_plain, "(documented limit) a held-out set without the rare input cannot tell the pairs apart"

    # (d) the per-call guard still protects live traffic when the gate was fooled
    live = deployed(_rows_encode_buggy, _rows_decode)
    x = _capture(9, extra=2.0)
    assert live(x) == x, "a mangled live output must fall back to the raw capture"
    assert live(held[0]) != held[0] and _rows_decode(live(held[0])) == held[0], "normal captures are still compressed"

    # other rejections: identity (no savings), raising decoder, nondeterminism, too few captures, non-str
    assert not accept_pair(lambda t: t, lambda t: t, held)[0]
    assert not accept_pair(_rows_encode, lambda t: 1 / 0, held)[0]
    flip = iter(range(10 ** 6))
    assert not accept_pair(lambda t: _rows_encode(t) + " " * (next(flip) % 2), lambda t: _rows_decode(t).rstrip(" "), held)[0]
    assert not accept_pair(_rows_encode, _rows_decode, held[:3])[0]
    assert not accept_pair(lambda t: None, _rows_decode, held)[0]
    return {"tokens_saved_on_heldout": saved, "token_drop": round(stats["token_drop"], 3), "wrong_pair_rejected_with_rare_input": True}


case(
    1365,
    claim="The acceptance gate takes an encode/decode pair and held-out captures and accepts it only if decode(encode(x)) equals x byte for byte on every capture (deterministic, no exceptions, enough captures) and tokens drop by at least 10%: it accepts a correct pair and rejects one that mangles a rare input (a float spelled 2.0) when that input is in the held-out set; a per-call round-trip check protects live traffic when the held-out set missed it.",
    sim=sim_synthesised_gate,
)
