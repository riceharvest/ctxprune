"""Runnable fidelity cases for ten new lossless cloud / Kubernetes / IaC ideas (#1414 #1415 #1416 #1417 #1418 #1419
#1420 #1421 #1422 #1423). See harness.py for what a pass means.

Conventions used below:
  * every encoder is fail-open (anything that raises hands the input back untouched) and self-gating (the input comes
    back unless the result is strictly shorter);
  * in-band markers follow one protocol: text that already contains something the decoder would read as a marker, or
    that starts with the raw escape "§raw\\n", is returned as "§raw\\n" + text (so decode can tell it from an encoded
    text); that escape grows the text by design, so those cases set idempotent=False;
  * roundtrip cases are byte exact; the equivalent ones (#1416 #1417 #1421) have an oracle that parses both sides with
    a different algorithm and a `loses=` that names exactly what is not preserved;
  * fixtures marked "captured" are real output of Docker Engine 29.9.2 / Compose v5.6.1 / buildx 0.38.1 run in this
    container (a daemon with a locally imported image); everything else is "reconstructed from memory" and says so.
"""
import functools
import json
import re

from harness import Fixture, case

SIGIL = "§"
RAW = "§raw\n"


def _failopen(fn):
    """Fail open and self-gate: hand the input back if anything raises or the result is not strictly shorter,
    except the raw escape (a deliberate growth) which is passed through."""

    @functools.wraps(fn)
    def wrapper(text):
        try:
            out = fn(text)
        except Exception:  # noqa: BLE001
            return text
        if not isinstance(out, str):
            return text
        if out.startswith(RAW) and out[len(RAW):] == text:
            return out
        return out if len(out) < len(text) else text

    return wrapper


def _pad_table(rows, gap=3):
    """kubectl / docker style table: every column but the last padded to its widest cell plus `gap` spaces."""
    widths = [max(len(r[i]) for r in rows) + gap for i in range(len(rows[0]) - 1)]
    return "\n".join("".join(c.ljust(w) for c, w in zip(r, widths)) + r[-1] for r in rows) + "\n"


# ===============================================================================================================
# #1414 terraform plan/apply -json: rebuild the 7-field resource object from its address. Roundtrip, header legend.
# Terraform's machine-readable UI stream prints, in every planned_change, apply_*, refresh_* line, a resource object
# {addr, module, resource, implied_provider, resource_type, resource_name, resource_key} of which only addr is new.

_STR = r'"(?:[^"\\]|\\.)*"'
_RES_OBJ = re.compile(
    r'"resource":(\{"addr":(' + _STR + r'),"module":' + _STR + r',"resource":' + _STR + r',"implied_provider":' + _STR
    + r',"resource_type":' + _STR + r',"resource_name":' + _STR + r',"resource_key":(?:null|-?\d+|' + _STR + r')\})')
_MARK = re.compile(r'"resource":"@((?:[^"\\]|\\.)*)"')
_IDX = r'\[(?:\d+|"[^"\\]*")\]'
_MODSEG = re.compile(r'module\.[A-Za-z0-9_-]+(?:' + _IDX + r')?\.')
_RESPART = re.compile(r'(?:data\.)?([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z0-9_-]+)(' + _IDX + r')?')
_TF_HDR = '§tfjson "resource":"@ADDR" stands for the 7-field resource object derived from address ADDR\n'


def _jstr(s):
    return json.dumps(s, ensure_ascii=False)


def _tf_resource_text(addr):
    """The resource object Terraform prints for `addr`, or None when addr is not plain module.x.type.name[key]."""
    rest, mods = addr, []
    while (m := _MODSEG.match(rest)):
        mods.append(m.group(0)[:-1])
        rest = rest[m.end():]
    m = _RESPART.fullmatch(rest)
    if not m:
        return None
    typ, name, idx = m.group(1), m.group(2), m.group(3)
    key = "null" if idx is None else idx[1:-1]
    return ('{"addr":' + _jstr(addr) + ',"module":' + _jstr(".".join(mods)) + ',"resource":' + _jstr(rest)
            + ',"implied_provider":' + _jstr(typ.split("_", 1)[0]) + ',"resource_type":' + _jstr(typ)
            + ',"resource_name":' + _jstr(name) + ',"resource_key":' + key + '}')


@_failopen
def tfjson_encode(text):
    if text.startswith(SIGIL):
        return RAW + text
    if SIGIL in text or '"resource":"@' in text:
        return text
    n = 0

    def sub(m):
        nonlocal n
        addr = json.loads(m.group(2))
        if _tf_resource_text(addr) != m.group(1):
            return m.group(0)  # not derivable from the address: keep the object verbatim
        n += 1
        return '"resource":"@' + m.group(2)[1:-1] + '"'

    body = _RES_OBJ.sub(sub, text)
    return _TF_HDR + body if n else text


def tfjson_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(_TF_HDR):
        return text
    return _MARK.sub(lambda m: '"resource":' + _tf_resource_text(json.loads('"' + m.group(1) + '"')), text[len(_TF_HDR):])


def _tf_obj(addr):
    return _tf_resource_text(addr)


def _tf_line(msg, ts, payload, typ):
    """One line of `terraform apply -json`: keys of the envelope sorted as Go's encoder prints them."""
    return '{"@level":"info","@message":' + _jstr(msg) + ',"@module":"terraform.ui","@timestamp":"' + ts + '",' + payload + ',"type":"' + typ + '"}'


def _tf_stream():  # reconstructed from memory of Terraform's machine-readable UI (docs: "JSON Output Format")
    res = [("aws_vpc.main", "vpc-0a1b2c3d4e5f60718"), ('aws_subnet.private[0]', "subnet-0a11b22c33d44e551"),
           ('aws_subnet.private[1]', "subnet-0b22c33d44e55f662"), ('aws_subnet.private[2]', "subnet-0c33d44e55f66a773"),
           ("aws_security_group.web", "sg-0d44e55f66a77b884"), ('aws_instance.web["blue"]', "i-0e55f66a77b88c995"),
           ("module.db.aws_db_instance.this", "app-db"), ("module.db.module.parameters.aws_db_parameter_group.this", "app-db-pg")]
    out = [_tf_line("Terraform 1.9.8", "2025-03-11T10:15:00.101010+01:00", '"terraform":"1.9.8"', "version").replace('"type":"version"}', '"type":"version","ui":"1.2"}')]
    out.append(_tf_line("data.aws_ami.ubuntu: Refreshing...", "2025-03-11T10:15:01.202020+01:00",
                        '"hook":{"resource":' + _tf_obj("data.aws_ami.ubuntu") + ',"action":"read"}', "refresh_start"))
    out.append(_tf_line("data.aws_ami.ubuntu: Refresh complete [id=ami-0f1e2d3c4b5a69788]", "2025-03-11T10:15:02.303030+01:00",
                        '"hook":{"resource":' + _tf_obj("data.aws_ami.ubuntu") + ',"action":"read","id_key":"id","id_value":"ami-0f1e2d3c4b5a69788"}', "refresh_complete"))
    for i, (a, _) in enumerate(res):
        out.append(_tf_line(f"{a}: Plan to create", f"2025-03-11T10:15:03.{i:02d}0000+01:00", '"change":{"resource":' + _tf_obj(a) + ',"action":"create"}', "planned_change"))
    out.append(_tf_line("Plan: 8 to add, 0 to change, 0 to destroy.", "2025-03-11T10:15:03.900000+01:00",
                        '"changes":{"add":8,"change":0,"import":0,"remove":0,"operation":"plan"}', "change_summary"))
    for i, (a, rid) in enumerate(res):
        h = '"hook":{"resource":' + _tf_obj(a)
        out.append(_tf_line(f"{a}: Creating...", f"2025-03-11T10:15:04.{i:02d}0000+01:00", h + ',"action":"create"}', "apply_start"))
        if i >= 5:
            out.append(_tf_line(f"{a}: Still creating... [10s elapsed]", f"2025-03-11T10:15:14.{i:02d}0000+01:00", h + ',"action":"create","elapsed_seconds":10}', "apply_progress"))
        out.append(_tf_line(f"{a}: Creation complete after {3 + i * 4}s [id={rid}]", f"2025-03-11T10:15:20.{i:02d}0000+01:00",
                            h + ',"action":"create","id_key":"id","id_value":' + _jstr(rid) + f',"elapsed_seconds":{3 + i * 4}}}', "apply_complete"))
    out.append(_tf_line("Apply complete! Resources: 8 added, 0 changed, 0 destroyed.", "2025-03-11T10:16:00.000000+01:00",
                        '"changes":{"add":8,"change":0,"import":0,"remove":0,"operation":"apply"}', "change_summary"))
    out.append(_tf_line("Outputs: 1", "2025-03-11T10:16:00.100000+01:00", '"outputs":{"vpc_id":{"sensitive":false,"type":"string","value":"vpc-0a1b2c3d4e5f60718"}}', "outputs"))
    return "\n".join(out) + "\n"


_TF_STREAM = _tf_stream()
_TF_NO_RES = "\n".join(l for l in _TF_STREAM.split("\n") if '"resource"' not in l) + "\n"
_TF_ODD = "\n".join(l.replace('"implied_provider":"aws"', '"implied_provider":"amazon"', 1) if i == 3 else l for i, l in enumerate(_TF_STREAM.split("\n")))  # first planned_change breaks the grammar

case(
    1414,
    claim="In terraform plan/apply -json lines, each resource object {addr,module,resource,implied_provider,resource_type,resource_name,resource_key} that follows from its addr (module path, data.type.name, [index] or [\"key\"]) becomes \"resource\":\"@addr\" under a one-line legend, and decode rebuilds it byte for byte; an object that does not follow the grammar, and any text with a marker-like \"resource\":\"@, stays verbatim.",
    encode=tfjson_encode,
    decode=tfjson_decode,
    fixtures=lambda: [
        Fixture("apply -json stream, 8 resources incl. count, for_each, nested modules, data source", _TF_STREAM, True),
        Fixture("stream with no resource objects (version, summary, outputs)", _TF_NO_RES, False),
        Fixture("human-readable plan text, not JSON", "Terraform will perform the following actions:\n\n  # aws_instance.web will be created\n  + resource \"aws_instance\" \"web\" {\n      + ami = \"ami-123\"\n    }\n\nPlan: 1 to add, 0 to change, 0 to destroy.\n", False),
        Fixture("one object whose implied_provider breaks the grammar stays verbatim", _TF_ODD, None),
        Fixture("input that starts like encoder output", _TF_HDR + '{"a":1}\n', None),
        Fixture("marker-like text already in the input", _TF_STREAM + '{"x":{"resource":"@aws_vpc.main"}}\n', None),
        Fixture("sigil in the middle of a message", _TF_STREAM.replace("Plan to create", "Plan § to create", 1), None),
        Fixture("CRLF stream", _TF_STREAM.replace("\n", "\r\n"), None),
    ],
    min_gain=0.25,
    idempotent=False,  # re-encoding encoded text must escape it (it starts with the sigil), so it grows by design
)


# ===============================================================================================================
# #1415 docker ps / compose ps / docker port: IPv4 and IPv6 published-port twins -> one {0.0.0.0,[::]} entry.

_PORT = r'(?P<hp>\d+(?:-\d+)?)->(?P<cp>\d+(?:-\d+)?/(?:tcp|udp|sctp))'
_TWIN = re.compile(r'(?<![\w.])0\.0\.0\.0:' + _PORT + r', (?P<v6>\[::\]|::):(?P=hp)->(?P=cp)(?!\w)')
_PAIR = re.compile(r'^(?P<c>\d+(?:-\d+)?/(?:tcp|udp|sctp)) -> 0\.0\.0\.0:(?P<h>\d+(?:-\d+)?)\n(?P=c) -> (?P<v6>\[::\]|::):(?P=h)$', re.M)
_UN_TWIN = re.compile(r'\{0\.0\.0\.0,(?P<v6>\[::\]|::)\}:' + _PORT)
_UN_PAIR = re.compile(r'^(?P<c>\d+(?:-\d+)?/(?:tcp|udp|sctp)) -> \{0\.0\.0\.0,(?P<v6>\[::\]|::)\}:(?P<h>\d+(?:-\d+)?)$', re.M)


@_failopen
def ports_encode(text):
    if text.startswith(RAW) or "{0.0.0.0," in text:
        return RAW + text
    out = _TWIN.sub(lambda m: "{0.0.0.0," + m["v6"] + "}:" + m["hp"] + "->" + m["cp"], text)
    return _PAIR.sub(lambda m: m["c"] + " -> {0.0.0.0," + m["v6"] + "}:" + m["h"], out)


def ports_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    out = _UN_PAIR.sub(lambda m: m["c"] + " -> 0.0.0.0:" + m["h"] + "\n" + m["c"] + " -> " + m["v6"] + ":" + m["h"], text)
    return _UN_TWIN.sub(lambda m: "0.0.0.0:" + m["hp"] + "->" + m["cp"] + ", " + m["v6"] + ":" + m["hp"] + "->" + m["cp"], out)


def _ps_rows(v6):
    def p(*pairs):
        return ", ".join(f"0.0.0.0:{h}->{c}" + (f", {v6}:{h}->{c}" if v6 else "") for h, c in pairs)
    return [["CONTAINER ID", "IMAGE", "COMMAND", "CREATED", "STATUS", "PORTS", "NAMES"],
            ["3f1a2b3c4d5e", "nginx:1.27", '"/docker-entrypoint.…"', "2 hours ago", "Up 2 hours", p(("80", "80/tcp"), ("443", "443/tcp")), "edge"],
            ["9a8b7c6d5e4f", "postgres:16", '"docker-entrypoint.s…"', "2 hours ago", "Up 2 hours (healthy)", "127.0.0.1:5432->5432/tcp", "db"],
            ["1b2c3d4e5f6a", "redis:7", '"docker-entrypoint.s…"', "2 hours ago", "Up 2 hours", "6379/tcp", "cache"],
            ["7e6d5c4b3a29", "acme/api:2025.03", '"gunicorn app:app"', "37 minutes ago", "Up 37 minutes", p(("8000", "8000/tcp")), "api"],
            ["5c4b3a291807", "acme/worker:2025.03", '"celery -A app worker"', "37 minutes ago", "Up 37 minutes", "", "worker"],
            ["0f9e8d7c6b5a", "acme/ftp:latest", '"/start.sh"', "3 days ago", "Up 3 days", p(("49153-49155", "8000-8002/tcp"), ("21", "21/tcp")), "ftp"],
            ["c3d4e5f6a7b8", "axllent/mailpit:v1.21", '"/mailpit"', "2 hours ago", "Up 2 hours (healthy)", p(("1025", "1025/tcp"), ("8025", "8025/tcp")), "mail"],
            ["d4e5f6a7b8c9", "minio/minio:RELEASE.2025-02-28", '"/usr/bin/docker-ent…"', "2 hours ago", "Up 2 hours", p(("9000", "9000/tcp"), ("9001", "9001/tcp")), "s3"]]


_PS_V6 = _pad_table(_ps_rows("[::]"))  # reconstructed from memory: Docker 28/29 prints the IPv6 twin as [::]
_PS_V6_OLD = _pad_table(_ps_rows("::"))  # reconstructed from memory: Docker 20.10 - 27 prints it as :::
# _PS_REAL and _PORT_REAL are captured text, defined at the end of the module
_PORT_V6 = "80/tcp -> 0.0.0.0:8080\n80/tcp -> [::]:8080\n443/tcp -> 0.0.0.0:8443\n443/tcp -> [::]:8443\n9000-9002/udp -> 0.0.0.0:49160\n"  # reconstructed
_COMPOSE_PS_ROWS = [["NAME", "IMAGE", "COMMAND", "SERVICE", "CREATED", "STATUS", "PORTS"]] + [
    [f"shop-{s}-1", im, f'"{c}"', s, "2 minutes ago", st, po] for s, im, c, st, po in [
        ("web", "nginx:1.27", "/docker-entrypoint.…", "Up 2 minutes", "0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp"),
        ("api", "acme/api:2025.03", "gunicorn app:app", "Up 2 minutes", "0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp"),
        ("db", "postgres:16", "docker-entrypoint.s…", "Up 2 minutes (healthy)", "127.0.0.1:5432->5432/tcp"),
        ("cache", "redis:7", "docker-entrypoint.s…", "Up 2 minutes", "0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp"),
        ("worker", "acme/api:2025.03", "celery -A app worker", "Up 2 minutes", "")]]
_COMPOSE_PS = _pad_table(_COMPOSE_PS_ROWS)  # shape of the captured table (see _PS_COMPOSE), with [::] twins as Compose 2.30+ prints them


case(
    1415,
    claim="In docker ps, docker compose ps and docker port output, a published port listed on both 0.0.0.0 and :: ([::] or the older :::) with the same host and container port and protocol (ranges included) is written once as {0.0.0.0,[::]}:host->container/proto; decode expands it back byte for byte, and a lone IPv4, a specific host IP, a mismatched pair or any input already containing {0.0.0.0, stays verbatim or escaped.",
    encode=ports_encode,
    decode=ports_decode,
    fixtures=lambda: [
        Fixture("docker ps -a, Docker 28/29 [::] twins, ranges, loopback and unpublished ports", _PS_V6, True),
        Fixture("docker ps -a, Docker 20.10-27 ::: twins", _PS_V6_OLD, True),
        Fixture("docker port with twin lines", _PORT_V6, True),
        Fixture("docker compose ps with twins", _COMPOSE_PS, True),
        Fixture("captured compose ps with a twin added to its one published port", _PS_COMPOSE.replace("0.0.0.0:8080->80/tcp", "0.0.0.0:8080->80/tcp, [::]:8080->80/tcp"), None),
        Fixture("captured compose ps as printed (no twin on this host)", _PS_COMPOSE, False),
        Fixture("captured docker ps on a host without IPv6: no twin to fold", _PS_REAL, False),
        Fixture("captured docker port: IPv4 only", _PORT_REAL, False),
        Fixture("IPv6 twin with a different host port is not a twin", "9a8b7c6d5e4f   x   0.0.0.0:80->80/tcp, [::]:8080->80/tcp   a\n", False),
        Fixture("input that already contains the marker form", _PS_V6 + "note: {0.0.0.0,[::]}:80->80/tcp\n", None),
        Fixture("input that starts with the raw escape", RAW + _PS_V6, None),
        Fixture("CRLF docker port", _PORT_V6.replace("\n", "\r\n"), None),
    ],
    min_gain=0.05,
    idempotent=False,  # re-encoding encoded text must escape it (it contains the marker), so it grows by design
)


# ===============================================================================================================
# #1416 docker compose up/down/start/stop progress: one state-chain line per resource. Equivalent: the relative order
# of lines of DIFFERENT resources inside a run is not kept (each resource's own state order is).

_LIFE = re.compile(r'^ (Network|Container|Volume|Image) (\S+) ([A-Z][a-z]+) ?$')
_CHAIN = re.compile(r'^ (Network|Container|Volume|Image) (\S+) [A-Z][a-z]+(?:>[A-Z][a-z]+)+$')


@_failopen
def compose_chain(text):
    lines = text.split("\n")
    if any(_CHAIN.match(l) for l in lines):
        return text  # the output already contains chain-looking lines: do not make the result ambiguous
    out, i, changed = [], 0, False
    while i < len(lines):
        if not _LIFE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and _LIFE.match(lines[j]):
            j += 1
        run = lines[i:j]
        chains: dict[tuple, list] = {}
        for l in run:
            m = _LIFE.match(l)
            chains.setdefault((m[1], m[2]), []).append(m[3])
        if len(chains) < len(run):
            out += [f" {k[0]} {k[1]} {'>'.join(v)}" for k, v in chains.items()]
            changed = True
        else:
            out += run
        i = j
    return "\n".join(out) if changed else text


_ANY = re.compile(r'^ (Network|Container|Volume|Image) (\S+) ([A-Z][a-z]+(?:>[A-Z][a-z]+)*) ?$')


def _compose_view(text):
    """Independent view: the text as segments; a run of lifecycle lines is {resource: [states in order]} plus the order
    in which resources first appear; every other line is kept verbatim."""
    segs, run = [], None
    for l in text.split("\n"):
        m = _ANY.match(l)
        if m:
            if run is None:
                run = {}
                segs.append(("run", run))
            run.setdefault((m[1], m[2]), []).extend(m[3].split(">"))
        else:
            run = None
            segs.append(("line", l))
    return [(k, list(v.items()) if k == "run" else v) for k, v in segs]


def _equiv_compose_chain(a, b):
    return _compose_view(a) == _compose_view(b)


case(
    1416,
    claim="In docker compose up/down/start/stop progress (' Container shop-db-1 Creating ' lines), the lines of a run are regrouped into one line per resource with its states in order joined by '>'; every other line and each resource's own sequence of states is unchanged.",
    loses="the relative order of lines belonging to different resources inside one run (which container reported Created before which)",
    encode=compose_chain,
    equivalent=_equiv_compose_chain,
    fixtures=lambda: [
        Fixture("captured compose up -d: 4 services, healthcheck, duplicated network lines", _UP, True),
        Fixture("captured compose down", _DOWN, True),
        Fixture("up output between other text", "Pulling shop...\n" + _UP + "[+] Running 5/5\n" + _DOWN + "done\n", True),
        Fixture("compose ps table (no lifecycle lines)", _COMPOSE_PS, False),
        Fixture("one line per resource, nothing to merge", " Container a Created \n Container b Created \n Network n Created \n", False),
        Fixture("output that already contains chain-looking lines", _UP + " Container shop-db-1 Creating>Created\n", None),
        Fixture("lines without the trailing space", _UP.replace(" \n", "\n"), None),
        Fixture("CRLF compose up", _UP.replace("\n", "\r\n"), None),
    ],
    min_gain=0.25,
)


# ===============================================================================================================
# #1417 CloudFormation JSON (cdk synth, aws cloudformation get-template): Fn::Join with an empty delimiter whose parts
# are literals, Ref and Fn::GetAtt becomes the equivalent Fn::Sub string. Equivalent: the oracle expands every
# string-only Fn::Sub on BOTH sides back to the Join form, so a hand-written Sub and the Join it abbreviates compare equal.

def _strict_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key")
        d[k] = v
    return d


def _no_const(_name):
    raise ValueError("NaN/Infinity is not JSON")


def _load_strict(text):
    return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_no_const)


_CF_REF = re.compile(r"AWS::[A-Za-z]+|[A-Za-z0-9]+")
_CF_LOGICAL = re.compile(r"[A-Za-z0-9]+")
_CF_ATTR = re.compile(r"[A-Za-z0-9._-]+")


def _join_to_sub(arg):
    """Value of an Fn::Join -> the Fn::Sub string, or None when the Join is not made of simple parts."""
    if not (isinstance(arg, list) and len(arg) == 2 and arg[0] == "" and isinstance(arg[1], list) and arg[1]):
        return None
    out, prev_literal = [], False
    for p in arg[1]:
        if isinstance(p, str):
            if not p or "${" in p or prev_literal:
                return None  # empty or adjacent literals would not survive Sub -> Join, ${ would be read as a reference
            prev_literal = True
            out.append(p)
        elif isinstance(p, dict) and len(p) == 1:
            prev_literal = False
            (k, v), = p.items()
            if k == "Ref" and isinstance(v, str) and _CF_REF.fullmatch(v):
                out.append("${" + v + "}")
            elif (k == "Fn::GetAtt" and isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)
                  and _CF_LOGICAL.fullmatch(v[0]) and _CF_ATTR.fullmatch(v[1])):
                out.append("${" + v[0] + "." + v[1] + "}")
            else:
                return None
        else:
            return None
    return "".join(out)


def _cf_walk(node):
    if isinstance(node, dict):
        if len(node) == 1 and "Fn::Join" in node:
            s = _join_to_sub(node["Fn::Join"])
            if s is not None:
                return {"Fn::Sub": s}
        return {k: _cf_walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_cf_walk(x) for x in node]
    return node


def _json_style(body, obj):
    """dumps() keywords that reproduce `body` exactly (indent n or compact), or None: then the layout is not canonical
    and the text is left alone rather than re-laid-out."""
    cands = []
    if "\n" in body:
        m = re.search(r"\n( +)\S", body)
        cands.append({"indent": len(m.group(1)) if m else 2})
    else:
        cands.append({"separators": (",", ":")})
    for kw in cands:
        if json.dumps(obj, ensure_ascii=False, allow_nan=False, **kw) == body:
            return kw
    return None


@_failopen
def cfn_encode(text):
    body = text.rstrip("\n")
    nl = text[len(body):]
    obj = _load_strict(body)
    if not isinstance(obj, (dict, list)):
        return text
    kw = _json_style(body, obj)
    if kw is None:
        return text
    new = _cf_walk(obj)
    if new == obj:
        return text
    return json.dumps(new, ensure_ascii=False, allow_nan=False, **kw) + nl


def _expand_sub(s):
    """Fn::Sub string -> list of Join parts (independent tokenizer: split on ${...}), or None if not expandable."""
    if "${!" in s:
        return None
    pieces = re.split(r"\$\{([^}]*)\}", s)
    parts = []
    for i, p in enumerate(pieces):
        if i % 2 == 0:
            if p:
                parts.append(p)
        elif re.fullmatch(r"AWS::[A-Za-z]+", p) or re.fullmatch(r"[A-Za-z0-9]+", p):
            parts.append({"Ref": p})
        elif re.fullmatch(r"[A-Za-z0-9]+\.[A-Za-z0-9._-]+", p):
            logical, _, attr = p.partition(".")
            parts.append({"Fn::GetAtt": [logical, attr]})
        else:
            return None
    return parts or None


def _cf_norm(node):
    if isinstance(node, dict):
        if len(node) == 1 and isinstance(node.get("Fn::Sub"), str):
            parts = _expand_sub(node["Fn::Sub"])
            if parts is not None:
                return {"Fn::Join": ["", parts]}
        return {k: _cf_norm(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_cf_norm(x) for x in node]
    return node


def _equiv_cfn(a, b):
    if a == b:
        return True
    try:
        return (json.dumps(_cf_norm(_load_strict(a))) == json.dumps(_cf_norm(_load_strict(b)))  # key order is compared
                and a.endswith("\n") == b.endswith("\n"))
    except ValueError:
        return False


def _ref(x):
    return {"Ref": x}


def _att(a, b):
    return {"Fn::GetAtt": [a, b]}


def _join(*parts):
    return {"Fn::Join": ["", list(parts)]}


_CFN = {  # shaped like `cdk synth --json` output for a bucket + function + role (reconstructed from memory)
    "Resources": {
        "SiteBucketE2B0B2A1": {
            "Type": "AWS::S3::Bucket",
            "Properties": {"BucketName": _join("site-", _ref("AWS::AccountId"), "-", _ref("AWS::Region"))},
            "UpdateReplacePolicy": "Retain",
            "DeletionPolicy": "Retain",
            "Metadata": {"aws:cdk:path": "SiteStack/SiteBucket/Resource"},
        },
        "ApiFnServiceRole3F1A9C27": {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "AssumeRolePolicyDocument": {"Statement": [{"Action": "sts:AssumeRole", "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}}], "Version": "2012-10-17"},
                "ManagedPolicyArns": [_join("arn:", _ref("AWS::Partition"), ":iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")],
            },
            "Metadata": {"aws:cdk:path": "SiteStack/ApiFn/ServiceRole/Resource"},
        },
        "ApiFnServiceRoleDefaultPolicy6A0B1C2D": {
            "Type": "AWS::IAM::Policy",
            "Properties": {
                "PolicyDocument": {
                    "Statement": [
                        {"Action": ["s3:GetObject*", "s3:GetBucket*", "s3:List*"], "Effect": "Allow",
                         "Resource": [_att("SiteBucketE2B0B2A1", "Arn"), _join(_att("SiteBucketE2B0B2A1", "Arn"), "/*")]},
                        {"Action": "logs:PutLogEvents", "Effect": "Allow",
                         "Resource": _join("arn:", _ref("AWS::Partition"), ":logs:", _ref("AWS::Region"), ":", _ref("AWS::AccountId"), ":log-group:/aws/lambda/", _ref("ApiFn1B2C3D4E"), ":*")},
                    ],
                    "Version": "2012-10-17",
                },
                "PolicyName": "ApiFnServiceRoleDefaultPolicy6A0B1C2D",
                "Roles": [_ref("ApiFnServiceRole3F1A9C27")],
            },
        },
        "ApiFn1B2C3D4E": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "Code": {"S3Bucket": {"Fn::Sub": "cdk-hnb659fds-assets-${AWS::AccountId}-${AWS::Region}"}, "S3Key": "9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c2b1a0.zip"},
                "Role": _att("ApiFnServiceRole3F1A9C27", "Arn"),
                "Environment": {"Variables": {
                    "BUCKET": _ref("SiteBucketE2B0B2A1"),
                    "BUCKET_URL": _join("https://", _att("SiteBucketE2B0B2A1", "RegionalDomainName"), "/index.html"),
                    "TABLE_ARNS": {"Fn::Join": [",", [_att("TableA", "Arn"), _att("TableB", "Arn")]]},
                    "TABLE_ARNS2": _join(_att("TableA", "Arn"), ",", _att("TableB", "Arn")),
                    "PICK": _join("x-", {"Fn::Select": [0, {"Fn::GetAZs": ""}]}),
                    "TEMPLATED": _join("literal ${", _ref("X"), "}"),
                    "ADJACENT": {"Fn::Join": ["", ["a", "b", _ref("Y")]]},
                }},
                "Handler": "index.handler",
                "Runtime": "nodejs20.x",
            },
            "DependsOn": ["ApiFnServiceRoleDefaultPolicy6A0B1C2D", "ApiFnServiceRole3F1A9C27"],
            "Metadata": {"aws:cdk:path": "SiteStack/ApiFn/Resource"},
        },
    },
    "Outputs": {"SiteUrl": {"Value": _join("https://", _att("SiteBucketE2B0B2A1", "DomainName"), "/")}},
    "Parameters": {"BootstrapVersion": {"Type": "AWS::SSM::Parameter::Value<String>", "Default": "/cdk-bootstrap/hnb659fds/version"}},
}
_CFN_TEXT = json.dumps(_CFN, indent=2, ensure_ascii=False) + "\n"
_CFN_PLAIN = {"Resources": {"Q": {"Type": "AWS::SQS::Queue", "Properties": {"QueueName": "orders", "Tags": [{"Key": "a", "Value": {"Ref": "AWS::Region"}}]}}}}
_CFN_PLAIN_TEXT = json.dumps(_CFN_PLAIN, indent=2) + "\n"
_CFN_YAML = "Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: !Join ['', [site-, !Ref 'AWS::Region']]\n"
_CFN_SUB_ONLY = {"Outputs": {"A": {"Value": {"Fn::Sub": "arn:${AWS::Partition}:s3:::${Bucket}/*"}}, "B": {"Value": {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, ":s3:::", {"Ref": "Bucket"}, "/*"]]}}}}

case(
    1417,
    claim="In pretty or minified CloudFormation JSON, an Fn::Join with an empty delimiter whose parts are non-empty, non-adjacent literals, Ref and two-part Fn::GetAtt becomes {\"Fn::Sub\": \"arn:${AWS::Partition}:...${Bucket.Arn}\"}; Joins with other delimiters, Fn::Select or nested parts, adjacent literals or a literal containing ${ stay as they are, and a Sub and the Join it abbreviates compare equal.",
    loses="nothing in the CloudFormation value; JSON layout is rebuilt with the same indent (non-canonical layouts are left untouched), and an Fn::Join and the Fn::Sub string that spells the same value are treated as the same",
    encode=cfn_encode,
    equivalent=_equiv_cfn,
    fixtures=lambda: [
        Fixture("cdk synth style template, 2-space JSON", _CFN_TEXT, True),
        Fixture("same template minified", json.dumps(_CFN, separators=(",", ":"), ensure_ascii=False) + "\n", True),
        Fixture("template without Join", _CFN_PLAIN_TEXT, False),
        Fixture("YAML template with !Join", _CFN_YAML, False),
        Fixture("JSON that is not canonical (tabs)", _CFN_TEXT.replace("  ", "\t"), None),
        Fixture("hand-written Sub next to an equal Join", json.dumps(_CFN_SUB_ONLY, indent=2) + "\n", None),
        Fixture("duplicate key makes the layout ambiguous", '{\n  "A": {"Fn::Join": ["", ["a", {"Ref": "B"}]]},\n  "A": 1\n}\n', None),
    ],
    min_gain=0.05,
)


# ===============================================================================================================
# #1418 aws s3 sync/cp/mv and gcloud storage cp: hoist the two roots into a header and print only the relative path
# (it appears on both sides of every line). Roundtrip.

_VERB = r"(?:upload: |download: |copy: |move: |Copying )"
_SYNC_LINE = re.compile(r"^(?P<pre>(?:[^\r\n]*\r)*)(?P<verb>" + _VERB + r")(?P<src>\S.*?) to (?P<dst>\S.*?)(?P<tail> *)$")
_SYNC_FOLDED = re.compile(r"^(?P<pre>(?:[^\r\n]*\r)*)(?P<verb>" + _VERB + r")(?P<rel>[^\r\n ](?:[^\r\n]*?[^\r\n ])?)(?P<tail> *)$")
_SYNC_HDR = "§sync\t"


def _common_suffix(a, b):
    x, y = a.split("/"), b.split("/")
    k = 0
    while k < min(len(x), len(y)) and x[-1 - k] == y[-1 - k]:
        k += 1
    return "/".join(x[len(x) - k:]) if k else ""


@_failopen
def sync_encode(text):
    if text.startswith(RAW) or text.startswith(_SYNC_HDR):
        return RAW + text
    lines = text.split("\n")
    if any((g := _SYNC_FOLDED.match(l)) and " to " not in g["rel"] for l in lines):
        return text  # a line the decoder would read as folded already exists
    pairs: dict[tuple, int] = {}
    for l in lines:
        m = _SYNC_LINE.match(l)
        if m:
            rel = _common_suffix(m["src"], m["dst"])
            if rel and "\t" not in m["src"] + m["dst"]:
                key = (m["src"][: len(m["src"]) - len(rel)], m["dst"][: len(m["dst"]) - len(rel)])
                pairs[key] = pairs.get(key, 0) + 1
    if not pairs:
        return text
    (sr, dr), n = max(pairs.items(), key=lambda kv: (kv[1], kv[0]))
    if n < 2:
        return text
    out, folded = [], 0
    for l in lines:
        m = _SYNC_LINE.match(l)
        if m and m["src"].startswith(sr) and m["dst"].startswith(dr):
            rel = m["src"][len(sr):]
            cand = m["pre"] + m["verb"] + rel + m["tail"]
            g = _SYNC_FOLDED.match(cand)
            if (rel and rel == m["dst"][len(dr):] and " to " not in rel and "\t" not in rel and g
                    and (g["pre"], g["verb"], g["rel"], g["tail"]) == (m["pre"], m["verb"], rel, m["tail"])):
                out.append(cand)
                folded += 1
                continue
        out.append(l)
    return _SYNC_HDR + sr + "\t" + dr + "\n" + "\n".join(out) if folded else text


def sync_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(_SYNC_HDR):
        return text
    head, _, body = text.partition("\n")
    _, sr, dr = head.split("\t")
    out = []
    for l in body.split("\n"):
        m = _SYNC_FOLDED.match(l)
        if m and " to " not in m["rel"]:
            out.append(m["pre"] + m["verb"] + sr + m["rel"] + " to " + dr + m["rel"] + m["tail"])
        else:
            out.append(l)
    return "\n".join(out)


_SITE = ["index.html", "favicon.ico", "robots.txt", "assets/index-DiwrgTda.js", "assets/vendor-5Fq2Zr9k.js", "assets/index-B3x9Qm1a.css",
         "assets/logo-5fa3c2d1.svg", "assets/fonts/inter-latin-400.woff2", "assets/fonts/inter-latin-700.woff2", "img/hero.webp", "img/team/ada.jpg", "img/team/linus.jpg"]
_S3_UP = "".join(f"upload: dist/{f} to s3://acme-web-prod/{f}\n" for f in _SITE)  # reconstructed from memory
_S3_UP_CR = "".join(f"Completed {i * 37.5 + 12.4:.1f} KiB/{len(_SITE) * 37.5 + 12.4:.1f} KiB ({i * 41.2 + 9.1:.1f} KiB/s) with {len(_SITE) - i} file(s) remaining\rupload: ./{f} to s3://acme-web-prod/site/{f}{' ' * 24}\n"
                    for i, f in enumerate(_SITE))  # reconstructed: the CLI redraws one progress line and overwrites it with the result
_S3_DOWN = "".join(f"download: s3://acme-data/exports/2025/03/{f} to exports/2025/03/{f}\n" for f in ["orders-0001.csv", "orders-0002.csv", "orders-0003.csv", "customers.parquet", "manifest.json"])
_GCS = "".join(f"Copying file://dist/{f} to gs://acme-web-prod/{f}\n  Completed files {i + 1}/{len(_SITE)} | {(i + 1) * 12.5:.1f}KiB/{len(_SITE) * 12.5:.1f}KiB\n" for i, f in enumerate(_SITE))  # reconstructed
_S3_MIXED = _S3_UP + "upload: dist/other/a.txt to s3://acme-web-prod/stage/b.txt\nupload failed: dist/big.iso to s3://acme-web-prod/big.iso An error occurred (AccessDenied) when calling the PutObject operation\ndelete: s3://acme-web-prod/old.js\n"

case(
    1418,
    claim="In aws s3 sync/cp/mv and gcloud storage cp output, the source and destination roots shared by the lines are written once in a header, and every 'verb: SRC to DST' line whose two paths end in the same relative path is reduced to 'verb: REL' (CR progress frames and trailing padding kept); lines with a different relative path, failures, delete lines and any text a decoder could misread stay verbatim.",
    encode=sync_encode,
    decode=sync_decode,
    fixtures=lambda: [
        Fixture("aws s3 sync dist s3://bucket: 12 uploads", _S3_UP, True),
        Fixture("aws s3 sync with CR progress frames and padding", _S3_UP_CR, True),
        Fixture("aws s3 sync from S3 down to a local directory", _S3_DOWN, True),
        Fixture("gcloud storage cp -r with progress lines", _GCS, True),
        Fixture("mixed: one line with a different name, a failure, a delete", _S3_MIXED, True),
        Fixture("a single copy: nothing to share", "upload: ./a.txt to s3://bucket/a.txt\n", False),
        Fixture("no sync lines", "Completed 1.0 KiB/1.0 KiB (2.1 KiB/s) with 0 file(s) remaining\nfatal error: Unable to locate credentials\n", False),
        Fixture("input that starts like the header", "§sync\tdist/\ts3://b/\nupload: a to b\n", None),
        Fixture("a file whose name contains ' to '", _S3_UP + "upload: dist/notes to self.txt to s3://acme-web-prod/notes to self.txt\n", None),
        Fixture("a line the decoder would read as folded", _S3_UP + "Copying file://dist/robots.txt [Content-Type=text/plain]...\n", None),
        Fixture("a harmless line starting with Copying", _S3_UP + "Copying files to the new server\n", None),
        Fixture("tab in a path", _S3_UP + "upload: dist/a\tb to s3://acme-web-prod/a\tb\n", None),
    ],
    min_gain=0.2,
    idempotent=False,  # the header makes re-encoding escape the text
)


# ===============================================================================================================
# #1419 Terraform diagnostics: the box of ╷ │ ╵ glyphs. Strip the rail from every inner line; the opener becomes '╷:'
# so a decoder knows to put the rails back. Roundtrip.

_RAIL = "│ "


@_failopen
def tfbox_encode(text):
    if text.startswith(RAW) or "\n╷:\n" in "\n" + text + "\n":
        return RAW + text
    lines = text.split("\n")
    out, i, changed = [], 0, False
    while i < len(lines):
        if lines[i] != "╷":
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j] not in ("╵", "╷"):
            j += 1
        inner = lines[i + 1:j]
        if j < len(lines) and lines[j] == "╵" and all(l.startswith(_RAIL) and l[len(_RAIL):] != "╵" for l in inner):
            out += ["╷:"] + [l[len(_RAIL):] for l in inner] + ["╵"]
            changed = True
            i = j + 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) if changed else text


def tfbox_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    out, inside = [], False
    for l in text.split("\n"):
        if inside:
            if l == "╵":
                inside = False
                out.append(l)
            else:
                out.append(_RAIL + l)
        elif l == "╷:":
            inside = True
            out.append("╷")
        else:
            out.append(l)
    return "\n".join(out)


def _box(*lines):
    """A Terraform diagnostic box; the empty inner lines are '│ ' (rail and one space), as Terraform prints them."""
    return "╷\n" + "".join(_RAIL + l + "\n" for l in lines) + "╵\n"


_BOX_MISSING = _box("Error: Missing required argument", "", '  on main.tf line 12, in resource "aws_instance" "web":', '  12: resource "aws_instance" "web" {', "",
                    'The argument "ami" is required, but no definition was found.')
_BOX_DEPRECATED = _box("Warning: Argument is deprecated", "", "  with aws_s3_bucket.assets,", '  on s3.tf line 8, in resource "aws_s3_bucket" "assets":', '   8:   acl = "private"', "",
                       "acl is deprecated. Use the aws_s3_bucket_acl resource instead.", "", "(and 3 more similar warnings elsewhere)")
_BOX_DENIED = _box("Error: creating EC2 Instance: operation error EC2: RunInstances, https response error StatusCode: 403, RequestID: 6c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f, api error UnauthorizedOperation: You are not authorized to perform this operation. User: arn:aws:iam::123456789012:user/dev is not authorized to perform: ec2:RunInstances on resource: arn:aws:ec2:eu-west-1:123456789012:instance/* because no identity-based policy allows the ec2:RunInstances action",
                   "", "  with aws_instance.web,", '  on main.tf line 21, in resource "aws_instance" "web":', '  21: resource "aws_instance" "web" {', "")
_BOX_UNSUPPORTED = _box("Error: Unsupported argument", "", '  on main.tf line 14, in resource "aws_instance" "web":', '  14:   instance_typ = "t3.micro"', "",
                        'An argument named "instance_typ" is not expected here. Did you mean "instance_type"?')
_BOX_UNDECLARED = _box("Error: Reference to undeclared resource", "", '  on outputs.tf line 3, in output "ip":', "   3:   value = aws_instance.app.public_ip", "",
                       'A managed resource "aws_instance" "app" has not been declared in the root module.')
_TF_ERR = _BOX_MISSING + _BOX_UNSUPPORTED + _BOX_DEPRECATED + _BOX_UNDECLARED  # reconstructed from memory of terraform validate diagnostics
_TF_LONG = _BOX_DENIED + _BOX_MISSING  # one very long summary line dilutes the saving
_TF_OK = "Success! The configuration is valid.\n\n"

case(
    1419,
    claim="In Terraform diagnostics, a box that opens with a '╷' line, closes with '╵' and has the '│ ' rail on every inner line is rewritten with the rails removed and the opener as '╷:'; decode puts the rails back byte for byte, and a box with an unrailed or doubled-up inner line is left as it is.",
    encode=tfbox_encode,
    decode=tfbox_decode,
    fixtures=lambda: [
        Fixture("terraform validate output: four boxes (errors and a deprecation warning)", _TF_ERR, True),
        Fixture("two plan warnings between plan text", "Plan: 1 to add, 0 to change, 0 to destroy.\n\n" + _BOX_DEPRECATED + "\n" + _BOX_DEPRECATED + "\nChanges to Outputs:\n", True),
        Fixture("valid configuration, no box", _TF_OK, False),
        Fixture("apply error with a 400-character summary line plus a short box", _TF_LONG, None),
        Fixture("box with an inner line missing the rail", "╷\n│ Error: x\nno rail here\n│ y\n╵\n", False),
        Fixture("inner line that would read as the closer after stripping", "╷\n│ Error: x\n│ ╵\n│ more\n╵\n", None),
        Fixture("unterminated box", "╷\n│ Error: x\n│ y\n", None),
        Fixture("nested opener before the closer", "╷\n│ a\n╷\n│ b\n╵\n", None),
        Fixture("input with a line that equals the folded opener", "╷:\n│ x\n╵\n", None),
        Fixture("CRLF boxes", _TF_ERR.replace("\n", "\r\n"), None),
    ],
    min_gain=0.05,
    idempotent=False,  # '╷:' lines in the output make re-encoding escape the text
)


# ===============================================================================================================
# #1420 docker build (BuildKit plain progress) failure summary: the failing RUN command is printed four more times and
# the last lines of its log once more. Replace the repeats with references; decode expands them exactly. Roundtrip.
#   '§N'                     the command text of step #N (raw, or Go-%q escaped inside process "/bin/sh -c ...")
#   '§last K lines of #N'    the last K streamed log lines of step #N without their '#N ' prefix

_BK_HDR = re.compile(r"^#(\d+) \[([^\]]*)\] RUN (.+)$")
_BK_ERR = re.compile(r'^#(\d+) ERROR: process "/bin/sh -c (.*)" did not complete successfully: exit code: (\d+)$')
_BK_STREAM = re.compile(r"^#(\d+) (\d+\.\d+ .*)$")
_BK_MARKER = re.compile(r"§(?:\d+|last \d+ lines of #\d+)")
_BK_DOCKERFILE = re.compile(r"^( *\d+ \| >>> RUN )(.*)$")


def _go_quote_inner(s):
    """Go's %q body for printable ASCII: only the double quote and the backslash need escaping."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _bk_index(lines):
    hdr, stream = {}, {}
    for l in lines:
        m = _BK_HDR.match(l)
        if m:
            hdr.setdefault(m[1], (m[2], m[3]))
            continue
        m = _BK_STREAM.match(l)
        if m:
            stream.setdefault(m[1], []).append(m[2])
    return hdr, stream


@_failopen
def buildkit_encode(text):
    if text.startswith(RAW) or _BK_MARKER.search(text):
        return RAW + text
    lines = text.split("\n")
    hdr, stream = _bk_index(lines)
    out = list(lines)
    drop, touched = set(), set()
    for e, l in enumerate(lines):
        m = _BK_ERR.match(l)
        if not m or m[1] not in hdr:
            continue
        n, bracket_cmd = m[1], hdr[m[1]]
        bracket, cmd = bracket_cmd
        esc = _go_quote_inner(cmd)
        if m[2] != esc or len(cmd) < 8 or not all(0x20 <= ord(c) < 0x7F for c in cmd):
            continue
        out[e] = f'#{n} ERROR: process "/bin/sh -c §{n}" did not complete successfully: exit code: {m[3]}'
        touched.add(e)
        k = e + 1
        if k + 1 < len(lines) and lines[k] == "------" and lines[k + 1] == f" > [{bracket}] RUN {cmd}:":
            out[k + 1] = f" > [{bracket}] RUN §{n}:"
            touched.add(k + 1)
            t = k + 2
            while t < len(lines) and lines[t] != "------":
                t += 1
            tail = lines[k + 2:t]
            if t < len(lines) and len(tail) >= 2 and tail == stream[n][-len(tail):]:
                out[k + 2] = f"§last {len(tail)} lines of #{n}"
                touched.add(k + 2)
                drop.update(range(k + 3, t))
        for i in range(e + 1, len(lines)):
            li = lines[i]
            if i in drop or i in touched or li.startswith(" > ["):
                continue
            d = _BK_DOCKERFILE.match(li)
            if d and d[2] == cmd:
                out[i] = d[1] + f"§{n}"
                touched.add(i)
            elif li.startswith("ERROR: ") and f'process "/bin/sh -c {esc}"' in li:
                out[i] = li.replace(f'process "/bin/sh -c {esc}"', f'process "/bin/sh -c §{n}"')
                touched.add(i)
    return "\n".join(l for i, l in enumerate(out) if i not in drop)


def buildkit_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    lines = text.split("\n")
    hdr, stream = _bk_index(lines)
    out = []
    for l in lines:
        m = re.fullmatch(r"§last (\d+) lines of #(\d+)", l)
        if m:
            out += stream[m[2]][-int(m[1]):]
            continue
        l = re.sub(r'process "/bin/sh -c §(\d+)"', lambda g: 'process "/bin/sh -c ' + _go_quote_inner(hdr[g[1]][1]) + '"', l)
        out.append(re.sub(r"§(\d+)", lambda g: hdr[g[1]][1], l))
    return "\n".join(out)


case(
    1420,
    claim="In a failed docker build (BuildKit plain progress), the command of the failing RUN step is written once, in its '#N [i/m] RUN ...' header; its repeats in '#N ERROR: process ...', ' > [i/m] RUN ...:', the Dockerfile excerpt and the final 'ERROR: failed to build' line become §N, and the block of last log lines that duplicates the end of the step's streamed output becomes '§last K lines of #N'; decode expands both byte for byte, and any step that does not match exactly (escaping, tail, multi-line RUN excerpt) keeps its original text.",
    encode=buildkit_encode,
    decode=buildkit_decode,
    fixtures=lambda: [
        Fixture("captured failed build: 25 log lines, 10 repeated in the summary", _BK_FAIL, True),
        Fixture("captured failed build: multi-line RUN with quotes, 4 log lines", _BK_FAIL2, True),
        Fixture("captured successful build", _BK_OK, False),
        Fixture("failure of a non-RUN step: nothing repeated", "#7 [3/4] COPY missing.txt .\n#7 ERROR: failed to calculate checksum of ref: \"/missing.txt\": not found\n------\n > [3/4] COPY missing.txt .:\n------\nDockerfile:3\nERROR: failed to build: failed to solve: failed to compute cache key\n", False),
        Fixture("input that starts with the raw escape", RAW + _BK_FAIL, None),
        Fixture("input that already contains a marker", _BK_FAIL + "§3 and §last 2 lines of #8\n", None),
        Fixture("sigil elsewhere in the log", _BK_FAIL.replace("Collecting package-3==", "Collecting § package-3==", 1), None),
        Fixture("tail block that is not the end of the stream", _BK_FAIL.replace("0.163 Collecting package-20==1.20.0\n0.163 Collecting package-21", "0.163 Collecting package-20==1.20.0\n0.164 Collecting package-21", 1), None),
        Fixture("CRLF log", _BK_FAIL.replace("\n", "\r\n"), None),
    ],
    min_gain=0.15,
    idempotent=False,  # §N markers in the output make re-encoding escape the text
)


# ===============================================================================================================
# #1421 terraform providers schema -json: every attribute leaf {type, description, description_kind, optional, ...}
# becomes one string '§type flags|description'. Equivalent: parse both sides; oracle expands the strings back.

_LEAF_KEYS = {"type", "description", "description_kind", "deprecated", "required", "optional", "computed", "sensitive"}
_FLAGS = ("required", "optional", "computed", "sensitive", "deprecated")
_PRIMS = {"string", "number", "bool", "dynamic"}


def _type_text(t):
    if isinstance(t, str):
        return t if t in _PRIMS else None
    if isinstance(t, list) and len(t) == 2 and t[0] in ("list", "set", "map"):
        inner = _type_text(t[1])
        return None if inner is None else f"{t[0]}({inner})"
    return None


def _leaf_text(d):
    if not isinstance(d, dict) or "type" not in d or not set(d) <= _LEAF_KEYS:
        return None
    t = _type_text(d["type"])
    if t is None:
        return None
    flags = []
    for f in _FLAGS:
        if f in d:
            if d[f] is not True:
                return None
            flags.append(f)
    desc, kind = d.get("description"), d.get("description_kind")
    if desc is None:
        if kind is not None:
            return None
    elif not isinstance(desc, str) or kind not in ("plain", "markdown"):
        return None
    elif kind == "markdown":
        flags.append("md")
    s = SIGIL + t + ("".join(" " + f for f in flags))
    return s + ("|" + desc if desc is not None else "")


def _schema_walk(node):
    if isinstance(node, dict):
        s = _leaf_text(node)
        if s is not None:
            return s
        return {k: _schema_walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_schema_walk(x) for x in node]
    return node


@_failopen
def schema_encode(text):
    if SIGIL in text:
        return text
    body = text.rstrip("\n")
    nl = text[len(body):]
    obj = _load_strict(body)
    if not isinstance(obj, (dict, list)):
        return text
    if "\n" in body:
        m = re.search(r"\n( +)\S", body)
        kw = {"indent": len(m.group(1)) if m else 2}
    else:
        kw = {"separators": (",", ":")}
    new = _schema_walk(obj)
    if new == obj:
        return text
    return json.dumps(new, ensure_ascii=False, allow_nan=False, **kw) + nl


def _parse_type(s):
    m = re.fullmatch(r"(list|set|map)\((.*)\)", s)
    return [m.group(1), _parse_type(m.group(2))] if m else s


def _expand_leaf(s):
    head, sep, desc = s[1:].partition("|")
    words = head.split(" ")
    flags = words[1:]
    d = {"type": _parse_type(words[0])}
    if sep:
        d["description"] = desc
        d["description_kind"] = "markdown" if "md" in flags else "plain"
    for f in _FLAGS:
        if f in flags:
            d[f] = True
    return d


def _schema_norm(node):
    if isinstance(node, dict):
        if "type" in node and set(node) <= _LEAF_KEYS:  # an attribute leaf: its own key order is not significant
            return dict(sorted(node.items()))
        return {k: _schema_norm(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_schema_norm(x) for x in node]
    if isinstance(node, str) and node.startswith(SIGIL):
        return dict(sorted(_expand_leaf(node).items()))
    return node


def _equiv_schema(a, b):
    if a == b:
        return True
    try:
        return json.dumps(_schema_norm(_load_strict(a))) == json.dumps(_schema_norm(_load_strict(b)))
    except (ValueError, KeyError):
        return False


def _attr(typ, *flags, desc=None):
    d = {"type": typ}
    if desc is not None:
        d["description"] = desc
        d["description_kind"] = "plain"
    for f in flags:
        d[f] = True
    return d


_S, _B, _N = "string", "bool", "number"
_LS, _SS, _MS = ["list", "string"], ["set", "string"], ["map", "string"]
_AWS_INSTANCE = {  # shaped like `terraform providers schema -json` for aws_instance (reconstructed from memory)
    "ami": _attr(_S, "optional", "computed"), "arn": _attr(_S, "computed"),
    "associate_public_ip_address": _attr(_B, "optional", "computed"), "availability_zone": _attr(_S, "optional", "computed"),
    "cpu_core_count": _attr(_N, "optional", "computed"), "cpu_threads_per_core": _attr(_N, "optional", "computed"),
    "disable_api_stop": _attr(_B, "optional", "computed"), "disable_api_termination": _attr(_B, "optional", "computed"),
    "ebs_optimized": _attr(_B, "optional", "computed"), "get_password_data": _attr(_B, "optional"),
    "hibernation": _attr(_B, "optional"), "host_id": _attr(_S, "optional", "computed"),
    "iam_instance_profile": _attr(_S, "optional", "computed"), "id": _attr(_S, "optional", "computed"),
    "instance_initiated_shutdown_behavior": _attr(_S, "optional", "computed"), "instance_state": _attr(_S, "computed"),
    "instance_type": _attr(_S, "optional", "computed"), "ipv6_address_count": _attr(_N, "optional", "computed"),
    "ipv6_addresses": _attr(_LS, "optional", "computed"), "key_name": _attr(_S, "optional", "computed"),
    "monitoring": _attr(_B, "optional", "computed"), "outpost_arn": _attr(_S, "computed"),
    "password_data": _attr(_S, "computed"), "placement_group": _attr(_S, "optional", "computed"),
    "primary_network_interface_id": _attr(_S, "computed"), "private_dns": _attr(_S, "computed"),
    "private_ip": _attr(_S, "optional", "computed"), "public_dns": _attr(_S, "computed"), "public_ip": _attr(_S, "computed"),
    "secondary_private_ips": _attr(_SS, "optional", "computed"), "security_groups": _attr(_SS, "optional", "computed"),
    "source_dest_check": _attr(_B, "optional"), "subnet_id": _attr(_S, "optional", "computed"),
    "tags": _attr(_MS, "optional"), "tags_all": _attr(_MS, "optional", "computed"),
    "tenancy": _attr(_S, "optional", "computed"), "user_data": _attr(_S, "optional", "computed"),
    "user_data_base64": _attr(_S, "optional", "computed"), "user_data_replace_on_change": _attr(_B, "optional"),
    "volume_tags": _attr(_MS, "optional"), "vpc_security_group_ids": _attr(_SS, "optional", "computed"),
}
_BLOCKS = {
    "root_block_device": {"nesting_mode": "list", "block": {"attributes": {
        "delete_on_termination": _attr(_B, "optional"), "device_name": _attr(_S, "computed"), "encrypted": _attr(_B, "optional", "computed"),
        "iops": _attr(_N, "optional", "computed"), "kms_key_id": _attr(_S, "optional", "computed"), "tags": _attr(_MS, "optional"),
        "throughput": _attr(_N, "optional", "computed"), "volume_id": _attr(_S, "computed"), "volume_size": _attr(_N, "optional", "computed"),
        "volume_type": _attr(_S, "optional", "computed")}}, "max_items": 1},
    "metadata_options": {"nesting_mode": "list", "block": {"attributes": {
        "http_endpoint": _attr(_S, "optional", "computed"), "http_put_response_hop_limit": _attr(_N, "optional", "computed"),
        "http_tokens": _attr(_S, "optional", "computed"), "instance_metadata_tags": _attr(_S, "optional", "computed")}}, "max_items": 1},
    "network_interface": {"nesting_mode": "set", "block": {"attributes": {
        "delete_on_termination": _attr(_B, "optional"), "device_index": _attr(_N, "required"), "network_card_index": _attr(_N, "optional"),
        "network_interface_id": _attr(_S, "required")}}},
}
_PROVIDER_ATTRS = {
    "access_key": _attr(_S, "optional", desc="The access key for API operations. You can retrieve this from the 'Security & Credentials' section of the AWS console."),
    "region": _attr(_S, "optional", desc="The region where AWS operations will take place. Examples are us-east-1, us-west-2, etc."),
    "profile": _attr(_S, "optional", desc="The profile for API operations. If not set, the default profile created with `aws configure` will be used."),
    "max_retries": _attr(_N, "optional", desc="The maximum number of times an AWS API request is retried on retryable failure."),
    "insecure": _attr(_B, "optional", desc="Explicitly allow the provider to perform \"insecure\" SSL requests."),
    "secret_key": _attr(_S, "optional", desc="The secret key for API operations.") | {"sensitive": True},
    "default_tags": _attr(["list", ["map", "string"]], "optional", desc="Tags to apply across all resources handled by this provider."),
    "assume_role_duration": {"type": "string", "description": "Duration of the assumed role session, for example `1h`. See [the docs](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html).", "description_kind": "markdown", "optional": True},
}
_SCHEMA = {"format_version": "1.0", "provider_schemas": {"registry.terraform.io/hashicorp/aws": {
    "provider": {"version": 0, "block": {"attributes": _PROVIDER_ATTRS, "description_kind": "plain"}},
    "resource_schemas": {"aws_instance": {"version": 1, "block": {"attributes": _AWS_INSTANCE, "block_types": _BLOCKS, "description_kind": "plain"}}}}}}
_SCHEMA_OTHER = {"definitions": {"Point": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}}}, "format_version": "1.0"}
_SCHEMA_ODD = {"attrs": {"a": {"type": ["object", {"x": "string"}], "optional": True}, "b": {"type": "string", "optional": False},
                         "c": {"type": "string", "description": "d", "description_kind": "html"}, "d": {"type": "string", "nested": 1}}}

case(
    1421,
    claim="In terraform providers schema -json (compact or pretty), each attribute object made only of type (a primitive or list/set/map of one), description, description_kind plain/markdown and true flags (required, optional, computed, sensitive, deprecated) is rewritten as one string '§string optional computed|description'; blocks, nested types, object types, false flags and every other key stay as they are.",
    loses="JSON text layout (re-serialised with the same indent, or minified, and string escapes such as \\u003c written as the character) and the order of the flag keys inside a leaf; the parsed document is otherwise identical",
    encode=schema_encode,
    equivalent=_equiv_schema,
    fixtures=lambda: [
        Fixture("aws_instance schema, jq-style 2-space JSON", json.dumps(_SCHEMA, indent=2) + "\n", True),
        Fixture("same schema, Terraform's compact one-liner", json.dumps(_SCHEMA, separators=(",", ":")) + "\n", True),
        Fixture("JSON-schema style objects that are not attribute leaves", json.dumps(_SCHEMA_OTHER, indent=2) + "\n", False),
        Fixture("leaves that must stay (object type, false flag, odd description_kind, extra key)", json.dumps(_SCHEMA_ODD, indent=2) + "\n", None),
        Fixture("input that already contains the sigil", json.dumps(_SCHEMA, indent=2).replace('"id"', '"§id"', 1) + "\n", None),
        Fixture("duplicate key", '{"a": {"type": "string"}, "a": {"type": "number"}}', None),
        Fixture("not JSON", "provider[registry.terraform.io/hashicorp/aws] ~> 5.0\n", False),
    ],
    min_gain=0.25,
)


# ===============================================================================================================
# #1422 kubectl config get-contexts: NAME, CLUSTER and AUTHINFO are the same long string on EKS, GKE and kind. A cell
# equal to the cell on its left becomes a ditto mark; the spacing is kept. Roundtrip.

_CTX_HDR = re.compile(r"^CURRENT +NAME +CLUSTER +AUTHINFO +NAMESPACE *$")
_CTX_ROW = re.compile(r"^(?P<cur>\*?[ ]*)(?P<name>\S+)(?P<s1> +)(?P<cl>\S+)(?P<s2> +)(?P<au>\S+)(?P<rest>.*)$")
_DITTO = '"'


@_failopen
def contexts_encode(text):
    if text.startswith(RAW):
        return RAW + text
    lines = text.split("\n")
    if not lines or not _CTX_HDR.match(lines[0]):
        return text
    out, changed = [lines[0]], False
    for l in lines[1:]:
        m = _CTX_ROW.match(l)
        if not m:
            out.append(l)
            continue
        if _DITTO in (m["name"], m["cl"], m["au"]):
            return RAW + text
        cl = _DITTO if m["cl"] == m["name"] else m["cl"]
        au = _DITTO if m["au"] == m["cl"] else m["au"]
        out.append(m["cur"] + m["name"] + m["s1"] + cl + m["s2"] + au + m["rest"])
        changed = changed or (cl, au) != (m["cl"], m["au"])
    return "\n".join(out) if changed else text


def contexts_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    lines = text.split("\n")
    if not lines or not _CTX_HDR.match(lines[0]):
        return text
    out = [lines[0]]
    for l in lines[1:]:
        m = _CTX_ROW.match(l)
        if not m:
            out.append(l)
            continue
        cl = m["name"] if m["cl"] == _DITTO else m["cl"]
        au = cl if m["au"] == _DITTO else m["au"]
        out.append(m["cur"] + m["name"] + m["s1"] + cl + m["s2"] + au + m["rest"])
    return "\n".join(out)


def _contexts(rows):
    return _pad_table([["CURRENT", "NAME", "CLUSTER", "AUTHINFO", "NAMESPACE"]] + rows)


_EKS = "arn:aws:eks:us-east-1:123456789012:cluster/prod"
_EKS2 = "arn:aws:eks:eu-west-1:123456789012:cluster/staging"
_CTX_EKS = _contexts([["*", _EKS, _EKS, _EKS, "default"], ["", _EKS2, _EKS2, _EKS2, ""], ["", "gke_acme-prod_europe-west4_core", "gke_acme-prod_europe-west4_core", "gke_acme-prod_europe-west4_core", "payments"],
                      ["", "kind-dev", "kind-dev", "kind-dev", ""], ["", "minikube", "minikube", "minikube", "kube-system"], ["", "aks-ctx", "aks-cluster", "clusterUser_rg_aks-cluster", ""],
                      ["", "ctx-x", "cluster-y", "ctx-x", "team-a"], ["", "ctx-z", "cluster-z", "user-z", ""]])  # reconstructed from memory

case(
    1422,
    claim="In kubectl config get-contexts output, a CLUSTER cell equal to the NAME cell and an AUTHINFO cell equal to the CLUSTER cell are written as a ditto mark (\"), with all spacing kept; decode copies the cell on the left back byte for byte, and a table that already uses a literal \" cell, or any other text, is left alone or escaped.",
    encode=contexts_encode,
    decode=contexts_decode,
    fixtures=lambda: [
        Fixture("EKS, GKE, kind and minikube contexts, one AKS-style row with different cells", _CTX_EKS, True),
        Fixture("single EKS context", _contexts([["*", _EKS, _EKS, _EKS, ""]]), True),
        Fixture("all cells differ", _contexts([["*", "a", "b", "c", "d"], ["", "e", "f", "g", ""]]), False),
        Fixture("another table", "NAME   READY   STATUS\nweb    1/1     Running\n", False),
        Fixture("a cell that is a literal double quote", _contexts([["*", "a", '"', "c", ""], ["", "e", "e", "e", ""]]), None),
        Fixture("input that starts with the raw escape", RAW + _CTX_EKS, None),
        Fixture("rows with fewer cells", "CURRENT   NAME   CLUSTER   AUTHINFO   NAMESPACE\n*         only   two\n          x      x         x\n", None),
        Fixture("CRLF table", _CTX_EKS.replace("\n", "\r\n"), None),
    ],
    min_gain=0.1,
    idempotent=False,  # '"' cells make re-encoding escape the text
)


# ===============================================================================================================
# #1423 kubectl get pods --show-labels: labels derivable from the pod name or from a sibling label become short markers
# in place. ~h pod-template-hash (middle part of NAME), ~n statefulset.kubernetes.io/pod-name (= NAME), ~i
# apps.kubernetes.io/pod-index (= number after the last '-'), ~j job-name (= batch.kubernetes.io/job-name), ~u
# controller-uid (= batch.kubernetes.io/controller-uid). Roundtrip.

_PODS_HDR = re.compile(r"^(?P<ns>NAMESPACE +)?NAME +READY +STATUS +RESTARTS +AGE(?: +IP +NODE +NOMINATED NODE +READINESS GATES)? +LABELS$")
_PODS_ROW = {True: re.compile(r"^(?P<pre>\S+ +)(?P<name>\S+)(?P<mid>.* )(?P<labels>\S+)$"),
             False: re.compile(r"^(?P<pre>)(?P<name>\S+)(?P<mid>.* )(?P<labels>\S+)$")}
_MARKERS = {"~h", "~n", "~i", "~j", "~u"}


def _name_parts(name):
    segs = name.split("-")
    return (segs[-2] if len(segs) >= 3 else None), (segs[-1] if len(segs) >= 2 else None)


def _fold_labels(name, labels):
    parts = labels.split(",")
    h, idx = _name_parts(name)
    kv = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in parts if "=" in p}
    out = []
    for p in parts:
        k, _, v = p.partition("=")
        if k == "pod-template-hash" and h is not None and v == h:
            out.append("~h")
        elif k == "statefulset.kubernetes.io/pod-name" and v == name:
            out.append("~n")
        elif k == "apps.kubernetes.io/pod-index" and idx is not None and idx.isdigit() and v == idx:
            out.append("~i")
        elif k == "job-name" and kv.get("batch.kubernetes.io/job-name") == v and v:
            out.append("~j")
        elif k == "controller-uid" and kv.get("batch.kubernetes.io/controller-uid") == v and v:
            out.append("~u")
        else:
            out.append(p)
    return ",".join(out)


def _unfold_labels(name, labels):
    parts = labels.split(",")
    h, idx = _name_parts(name)
    kv = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in parts if "=" in p}
    rep = {"~h": f"pod-template-hash={h}", "~n": f"statefulset.kubernetes.io/pod-name={name}", "~i": f"apps.kubernetes.io/pod-index={idx}",
           "~j": f"job-name={kv.get('batch.kubernetes.io/job-name')}", "~u": f"controller-uid={kv.get('batch.kubernetes.io/controller-uid')}"}
    return ",".join(rep.get(p, p) for p in parts)


def _rows(text, fn):
    lines = text.split("\n")
    m = _PODS_HDR.match(lines[0]) if lines else None
    if not m:
        return None
    rx = _PODS_ROW[bool(m["ns"])]
    out = [lines[0]]
    for l in lines[1:]:
        r = rx.match(l)
        out.append(l if not r else r["pre"] + r["name"] + r["mid"] + fn(r["name"], r["labels"]))
    return "\n".join(out)


@_failopen
def labels_encode(text):
    if text.startswith(RAW):
        return RAW + text
    if any(p in _MARKERS for l in text.split("\n") for p in l.rsplit(" ", 1)[-1].split(",")):
        return RAW + text
    out = _rows(text, _fold_labels)
    return text if out is None else out


def labels_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    out = _rows(text, _unfold_labels)
    return text if out is None else out


def _pods(rows, ns=False):
    hdr = (["NAMESPACE"] if ns else []) + ["NAME", "READY", "STATUS", "RESTARTS", "AGE", "LABELS"]
    return _pad_table([hdr] + [([r[0]] if ns else []) + r[1:] for r in rows])


_PODROWS = [
    ["default", "web-5d8f7c6b9-4xk2p", "1/1", "Running", "0", "3d", "app=web,pod-template-hash=5d8f7c6b9,tier=frontend"],
    ["default", "web-5d8f7c6b9-9fjwq", "1/1", "Running", "0", "3d", "app=web,pod-template-hash=5d8f7c6b9,tier=frontend"],
    ["default", "web-5d8f7c6b9-vz7lh", "1/1", "Running", "2 (5h ago)", "3d", "app=web,pod-template-hash=5d8f7c6b9,tier=frontend"],
    ["default", "db-0", "1/1", "Running", "0", "12d", "app=db,apps.kubernetes.io/pod-index=0,controller-revision-hash=db-7d9f8c6b5,statefulset.kubernetes.io/pod-name=db-0"],
    ["default", "db-1", "1/1", "Running", "0", "12d", "app=db,apps.kubernetes.io/pod-index=1,controller-revision-hash=db-7d9f8c6b5,statefulset.kubernetes.io/pod-name=db-1"],
    ["default", "migrate-27381-xk4tq", "0/1", "Completed", "0", "6h", "batch.kubernetes.io/controller-uid=4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f,batch.kubernetes.io/job-name=migrate-27381,controller-uid=4f1c2d3e-5a6b-4c7d-8e9f-0a1b2c3d4e5f,job-name=migrate-27381"],
    ["kube-system", "coredns-787d4945fb-2m9rt", "1/1", "Running", "0", "30d", "k8s-app=kube-dns,pod-template-hash=787d4945fb"],
    ["kube-system", "etcd-cp1", "1/1", "Running", "0", "30d", "component=etcd,tier=control-plane"],
    ["default", "odd-5d8f7c6b9-aaaaa", "1/1", "Running", "0", "1d", "app=odd,pod-template-hash=ffffffff"],
    ["default", "cache-0", "1/1", "Running", "0", "1d", "apps.kubernetes.io/pod-index=3,statefulset.kubernetes.io/pod-name=cache-9"],
    ["default", "report-28-abcde", "0/1", "Completed", "0", "1d", "batch.kubernetes.io/job-name=report-29,job-name=report-28"],
    ["default", "nolabels-7c9d5f4b8-q2w3e", "1/1", "Running", "0", "1d", "<none>"],
]
_PODS_A = _pods(_PODROWS, ns=True)  # reconstructed from memory of kubectl get pods -A --show-labels
_PODS_NS = _pad_table([["NAME", "READY", "STATUS", "RESTARTS", "AGE", "LABELS"]] + [r[1:] for r in _PODROWS if r[0] == "default"])

case(
    1423,
    claim="In kubectl get pods --show-labels output, labels derivable from the pod name or a sibling label are replaced in place by ~h (pod-template-hash equal to the name's middle part), ~n (statefulset.kubernetes.io/pod-name equal to the name), ~i (apps.kubernetes.io/pod-index equal to the name's number), ~j and ~u (the legacy job-name and controller-uid beside their batch.kubernetes.io twins); a label whose value is not the derived one is kept, and decode restores the line byte for byte.",
    encode=labels_encode,
    decode=labels_decode,
    fixtures=lambda: [
        Fixture("get pods -A --show-labels: Deployment, StatefulSet, Job, DaemonSet-like and static pods", _PODS_A, True),
        Fixture("get pods --show-labels without the namespace column", _PODS_NS, True),
        Fixture("labels that are not derivable", _pods([["default", "api-6b7c8d9e0f-abcde", "1/1", "Running", "0", "1d", "app=api,pod-template-hash=zzz"], ["default", "x", "1/1", "Running", "0", "1d", "a=b"]], ns=True), False),
        Fixture("get pods without labels", "NAME   READY   STATUS    RESTARTS   AGE\nweb-5d8f7c6b9-4xk2p   1/1   Running   0   3d\n", False),
        Fixture("a label element that looks like a marker", _PODS_A + "default   p-1-2   1/1   Running   0   1d   ~h,a=b\n", None),
        Fixture("input that starts with the raw escape", RAW + _PODS_A, None),
        Fixture("CRLF rows", _PODS_A.replace("\n", "\r\n"), None),
    ],
    min_gain=0.1,
    idempotent=False,  # ~x markers in the output make re-encoding escape the text
)


# ===============================================================================================================
# Captured output (Docker Engine 29.9.2, Compose v5.6.1, buildx 0.38.1; a local daemon, an imported image, no network).
# Container ids and image digests are as printed. Defined last: the fixtures that use them are built lazily.
_UP = r''' Network shop_default Creating 
 Network shop_default Creating 
 Network shop_default Created 
 Network shop_default Created 
 Container shop-cache-1 Creating 
 Container shop-db-1 Creating 
 Container shop-cache-1 Created 
 Container shop-db-1 Created 
 Container shop-api-1 Creating 
 Container shop-api-1 Created 
 Container shop-worker-1 Creating 
 Container shop-worker-1 Created 
 Container shop-db-1 Starting 
 Container shop-cache-1 Starting 
 Container shop-db-1 Started 
 Container shop-cache-1 Started 
 Container shop-db-1 Waiting 
 Container shop-db-1 Healthy 
 Container shop-api-1 Starting 
 Container shop-api-1 Started 
 Container shop-worker-1 Starting 
 Container shop-worker-1 Started 
'''
_DOWN = r''' Container shop-worker-1 Stopping 
 Container shop-worker-1 Stopped 
 Container shop-worker-1 Removing 
 Container shop-worker-1 Removed 
 Container shop-api-1 Stopping 
 Container shop-api-1 Stopped 
 Container shop-api-1 Removing 
 Container shop-api-1 Removed 
 Container shop-db-1 Stopping 
 Container shop-cache-1 Stopping 
 Container shop-cache-1 Stopped 
 Container shop-cache-1 Removing 
 Container shop-cache-1 Removed 
 Container shop-db-1 Stopped 
 Container shop-db-1 Removing 
 Container shop-db-1 Removed 
 Network shop_default Removing 
 Network shop_default Removed 
'''
_BK_FAIL = r'''#0 building with "default" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 264B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/shimg:1
#2 DONE 0.0s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [internal] load build context
#4 transferring context: 37B done
#4 DONE 0.0s

#5 [1/4] FROM docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023
#5 resolve docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023 done
#5 extracting sha256:28111573e5ac9a5afcfe1981a4660106f31adfccefdb950bfc2fb529e85f5f65 0.0s done
#5 DONE 0.0s

#6 [2/4] WORKDIR /app
#6 DONE 0.2s

#7 [3/4] COPY app.txt .
#7 DONE 0.0s

#8 [4/4] RUN i=0; while [ $i -lt 25 ]; do echo "Collecting package-$i==1.$i.0"; i=$((i+1)); done; echo "ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9"; exit 1
#8 0.163 Collecting package-0==1.0.0
#8 0.163 Collecting package-1==1.1.0
#8 0.163 Collecting package-2==1.2.0
#8 0.163 Collecting package-3==1.3.0
#8 0.163 Collecting package-4==1.4.0
#8 0.163 Collecting package-5==1.5.0
#8 0.163 Collecting package-6==1.6.0
#8 0.163 Collecting package-7==1.7.0
#8 0.163 Collecting package-8==1.8.0
#8 0.163 Collecting package-9==1.9.0
#8 0.163 Collecting package-10==1.10.0
#8 0.163 Collecting package-11==1.11.0
#8 0.163 Collecting package-12==1.12.0
#8 0.163 Collecting package-13==1.13.0
#8 0.163 Collecting package-14==1.14.0
#8 0.163 Collecting package-15==1.15.0
#8 0.163 Collecting package-16==1.16.0
#8 0.163 Collecting package-17==1.17.0
#8 0.163 Collecting package-18==1.18.0
#8 0.163 Collecting package-19==1.19.0
#8 0.163 Collecting package-20==1.20.0
#8 0.163 Collecting package-21==1.21.0
#8 0.163 Collecting package-22==1.22.0
#8 0.163 Collecting package-23==1.23.0
#8 0.163 Collecting package-24==1.24.0
#8 0.163 ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9
#8 ERROR: process "/bin/sh -c i=0; while [ $i -lt 25 ]; do echo \"Collecting package-$i==1.$i.0\"; i=$((i+1)); done; echo \"ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9\"; exit 1" did not complete successfully: exit code: 1
------
 > [4/4] RUN i=0; while [ $i -lt 25 ]; do echo "Collecting package-$i==1.$i.0"; i=$((i+1)); done; echo "ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9"; exit 1:
0.163 Collecting package-16==1.16.0
0.163 Collecting package-17==1.17.0
0.163 Collecting package-18==1.18.0
0.163 Collecting package-19==1.19.0
0.163 Collecting package-20==1.20.0
0.163 Collecting package-21==1.21.0
0.163 Collecting package-22==1.22.0
0.163 Collecting package-23==1.23.0
0.163 Collecting package-24==1.24.0
0.163 ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9
------
Dockerfile:4
--------------------
   2 |     WORKDIR /app
   3 |     COPY app.txt .
   4 | >>> RUN i=0; while [ $i -lt 25 ]; do echo "Collecting package-$i==1.$i.0"; i=$((i+1)); done; echo "ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9"; exit 1
   5 |     
--------------------
ERROR: failed to build: failed to solve: process "/bin/sh -c i=0; while [ $i -lt 25 ]; do echo \"Collecting package-$i==1.$i.0\"; i=$((i+1)); done; echo \"ERROR: Could not find a version that satisfies the requirement missing-pkg==9.9\"; exit 1" did not complete successfully: exit code: 1
'''
_BK_FAIL2 = r'''#0 building with "default" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 349B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/shimg:1
#2 DONE 0.0s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [1/3] FROM docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023
#4 resolve docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023 0.0s done
#4 CACHED

#5 [2/3] WORKDIR /srv/app
#5 DONE 0.0s

#6 [3/3] RUN echo "step one" &&     echo "installing dependencies" &&     echo 'warning: deprecated flag --legacy' >&2 &&     test -f /srv/app/package.json || (echo "npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'" && exit 254)
#6 0.160 warning: deprecated flag --legacy
#6 0.160 step one
#6 0.160 installing dependencies
#6 0.160 npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'
#6 ERROR: process "/bin/sh -c echo \"step one\" &&     echo \"installing dependencies\" &&     echo 'warning: deprecated flag --legacy' >&2 &&     test -f /srv/app/package.json || (echo \"npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'\" && exit 254)" did not complete successfully: exit code: 254
------
 > [3/3] RUN echo "step one" &&     echo "installing dependencies" &&     echo 'warning: deprecated flag --legacy' >&2 &&     test -f /srv/app/package.json || (echo "npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'" && exit 254):
0.160 warning: deprecated flag --legacy
0.160 step one
0.160 installing dependencies
0.160 npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'
------
Dockerfile:4
--------------------
   3 |     WORKDIR /srv/app
   4 | >>> RUN echo "step one" && \
   5 | >>>     echo "installing dependencies" && \
   6 | >>>     echo 'warning: deprecated flag --legacy' >&2 && \
   7 | >>>     test -f /srv/app/package.json || (echo "npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'" && exit 254)
   8 |     
--------------------
ERROR: failed to build: failed to solve: process "/bin/sh -c echo \"step one\" &&     echo \"installing dependencies\" &&     echo 'warning: deprecated flag --legacy' >&2 &&     test -f /srv/app/package.json || (echo \"npm ERR! enoent ENOENT: no such file or directory, open '/srv/app/package.json'\" && exit 254)" did not complete successfully: exit code: 254
'''
_BK_OK = r'''#0 building with "default" instance using docker driver

#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 92B done
#1 DONE 0.0s

#2 [internal] load metadata for docker.io/library/shimg:1
#2 DONE 0.0s

#3 [internal] load .dockerignore
#3 transferring context: 2B done
#3 DONE 0.0s

#4 [1/4] FROM docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023
#4 resolve docker.io/library/shimg:1@sha256:b4fc3f906c99e8fab5b75931572e790906ca1712ed639b59ddaa4af8300b2023 done
#4 DONE 0.0s

#5 [2/4] WORKDIR /app
#5 CACHED

#6 [internal] load build context
#6 transferring context: 37B done
#6 DONE 0.0s

#7 [3/4] COPY app.txt .
#7 DONE 0.0s

#8 [4/4] RUN echo "ok"
#8 0.151 ok
#8 DONE 0.2s

#9 exporting to image
#9 exporting layers 0.1s done
#9 exporting manifest sha256:675721f8d39acfe1f337b2f08665a9c848dbce9f7887ed2de3c582b0b693d616 done
#9 exporting config sha256:725cb62c3c2a6b63ce8e5cc38587b7a172427d0dfbc2b1f4e2422714229e7b2b done
#9 exporting attestation manifest sha256:7a5e9fad6431984ff1b0dc7cccaea58ec6909367dc9e8b443751f44fb087be4d done
#9 exporting manifest list sha256:497f6474bd58026e49d3268b5ab9a1302d37ff3fe17f81271851f3810f3fce65 done
#9 naming to moby-dangling@sha256:497f6474bd58026e49d3268b5ab9a1302d37ff3fe17f81271851f3810f3fce65
#9 naming to moby-dangling@sha256:497f6474bd58026e49d3268b5ab9a1302d37ff3fe17f81271851f3810f3fce65 done
#9 unpacking to moby-dangling@sha256:497f6474bd58026e49d3268b5ab9a1302d37ff3fe17f81271851f3810f3fce65 0.0s done
#9 DONE 0.1s
'''
_PS_COMPOSE = r'''NAME            IMAGE       COMMAND            SERVICE   CREATED         STATUS                  PORTS
shop-api-1      sleeper:1   "/bin/sleep 600"   api       2 seconds ago   Up Less than a second   0.0.0.0:8080->80/tcp
shop-cache-1    sleeper:1   "/bin/sleep 600"   cache     2 seconds ago   Up 1 second             
shop-db-1       sleeper:1   "/bin/sleep 600"   db        2 seconds ago   Up 1 second (healthy)   
shop-worker-1   sleeper:1   "/bin/sleep 600"   worker    2 seconds ago   Up Less than a second   
'''
_PS_REAL = r'''CONTAINER ID   IMAGE       COMMAND            CREATED                  STATUS                  PORTS                                         NAMES
b0ee4b9ac9ec   sleeper:1   "/bin/sleep 600"   Less than a second ago   Up Less than a second   0.0.0.0:8080->80/tcp, 0.0.0.0:8443->443/tcp   web
'''
_PORT_REAL = r'''80/tcp -> 0.0.0.0:8080
443/tcp -> 0.0.0.0:8443
'''
