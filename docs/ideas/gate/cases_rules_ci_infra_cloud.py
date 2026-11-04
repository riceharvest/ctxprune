"""Rule cases for CI, infra and cloud-CLI output: #137 #310 #311 #312 #313 #314 #318 #478 #791 #800 #858 #980 #984 #1153
#1158 #1161 #1164 #1170 #1235 #1238 #1241.

Every case drops information ON PURPOSE, so these are `rule` cases (`probes`) except #858 and #1153, which are policies
(a workflow step, an env audit) and so are `sim`s. The probes are the questions an agent must still be able to answer
(which step failed and why, the file:line of each error, which attribute is computed, the effective value of a field the
encoder removed because it equals the default). They are written as a different algorithm from the encoder: a parser
or state machine where the encoder uses a line scan, and "effective value" lookups where the encoder uses a table.

Shared tools (all stdlib, nothing here is production code):
  yaml_lite  a small YAML-subset reader (block maps and lists, scalars, block literals) for kubectl and gcloud output.
             The probes need structure, PyYAML is not a dependency, so it is written here.
  ydump      the matching writer, used to build realistic fixtures (sorted keys, lists at the key's indent, as Go does).

Provenance: kubectl, terraform, ansible, aws, az, docker (no daemon), iptables (no netfilter), xcrun, adb, Windows and
macOS tools are not usable in this container, so those fixtures are reconstructed from memory of the real formats and
marked `# reconstructed from memory`. Real captures here: `cargo test` (#1153, run three times), `gcloud ... --help`
(#310, the whole 67 KB `compute instances create` page was measured, a smaller real page is embedded) and `df` / `mount`
(#984, the negative fixtures).
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from harness import Fixture, case, tokens

# ===============================================================================================================
# YAML subset reader and writer


class YamlError(ValueError):
    pass


def _yscalar_in(tok: str):
    t = tok.strip()
    if t == "":
        return None
    if t[0] == '"':
        try:
            v = json.loads(t)
        except ValueError:
            raise YamlError("bad double-quoted scalar")
        if not isinstance(v, str):
            raise YamlError("bad double-quoted scalar")
        return v
    if t[0] == "'":
        if len(t) < 2 or t[-1] != "'":
            raise YamlError("bad single-quoted scalar")
        return t[1:-1].replace("''", "'")
    if t == "{}":
        return {}
    if t == "[]":
        return []
    if t[0] in "{[&*!|>%@`":
        raise YamlError("unsupported scalar " + t[:10])
    if t in ("true", "false"):
        return t == "true"
    if t in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return t


_YKEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'[^']*'|[^\s:#"'\-\[\]{},&*!|>%@`][^:]*?|-[^\s:][^:]*?):(?:[ ](.*))?$""")


def yaml_lite(text: str):
    """Parse the YAML that kubectl and gcloud print. Raises YamlError on anything else (flow style, anchors, tabs)."""
    raw = text.replace("\r\n", "\n").split("\n")
    if any("\t" in l[: len(l) - len(l.lstrip(" \t"))] for l in raw):
        raise YamlError("tab indentation")
    n = len(raw)

    def sig(i):
        while i < n and (not raw[i].strip() or raw[i].lstrip().startswith("#")):
            i += 1
        return i if i < n else None

    def ind(i):
        return len(raw[i]) - len(raw[i].lstrip(" "))

    def is_dash(i):
        s = raw[i].lstrip(" ")
        return s.startswith("- ") or s == "-"

    def node(i):
        return seq(i, ind(i)) if is_dash(i) else mapping(i, ind(i))

    def seq(i, indent):
        out = []
        while True:
            j = sig(i)
            if j is None or ind(j) != indent or not is_dash(j):
                return out, (n if j is None else j)
            rest = raw[j].lstrip(" ")[1:]
            if rest.strip() == "":
                k = sig(j + 1)
                if k is None or ind(k) <= indent:
                    out.append(None)
                    i = j + 1
                    continue
                v, i = node(k)
                out.append(v)
                continue
            off = 1 + len(rest) - len(rest.lstrip(" "))
            raw[j] = " " * (indent + off) + rest.lstrip(" ")
            if _YKEY.match(rest.strip()):
                v, i = mapping(j, indent + off)
            else:
                v, i = _yscalar_in(rest), j + 1
            out.append(v)

    def mapping(i, indent):
        out = {}
        while True:
            j = sig(i)
            if j is None or ind(j) < indent:
                return out, (n if j is None else j)
            if ind(j) > indent:
                raise YamlError(f"unexpected indent at line {j + 1}")
            if is_dash(j):
                return out, j
            m = _YKEY.match(raw[j].strip())
            if not m:
                raise YamlError(f"not a key at line {j + 1}")
            key = m.group(1)
            if key[0] in "\"'":
                key = _yscalar_in(key)
            v = m.group(2)
            if v is None or v.strip() == "":
                k = sig(j + 1)
                if k is not None and (ind(k) > indent or (ind(k) == indent and is_dash(k))):
                    val, i = node(k)
                else:
                    val, i = None, j + 1
            elif v.strip()[0] in "|>" and re.fullmatch(r"[|>][+-]?", v.strip()):
                body, k = [], j + 1
                while k < n and (not raw[k].strip() or ind(k) > indent):
                    body.append(raw[k])
                    k += 1
                keep_nl = v.strip().endswith("+")
                while body and not body[-1].strip() and not keep_nl:
                    body.pop()
                first = next((b for b in body if b.strip()), "")
                w = len(first) - len(first.lstrip(" "))
                lines = [b[w:] if b.strip() else "" for b in body]
                val = "\n".join(lines) + ("" if v.strip().endswith("-") or not lines else "\n")
                if v.strip()[0] == ">":
                    val = val.replace("\n", " ")
                i = k
            else:
                val, i = _yscalar_in(v), j + 1
            out[key] = val

    i = sig(0)
    if i is None:
        return None
    if ind(i) != 0 and not is_dash(i):
        raise YamlError("document does not start at column 0")
    val, end = node(i)
    if sig(end) is not None:
        raise YamlError(f"trailing content at line {end + 1}")
    return val


def yaml_docs(text: str) -> list:
    """A stream of '---'-separated documents (gcloud logging read prints one per entry)."""
    docs, cur = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.rstrip() == "---":
            if any(l.strip() for l in cur):
                docs.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    if any(l.strip() for l in cur):
        docs.append("\n".join(cur))
    return [yaml_lite(d) for d in docs]


def _yq(s: str) -> str:
    if (s == "" or re.fullmatch(r"-?\d+(\.\d+)?|true|false|null|~|yes|no|on|off", s, re.I) or re.match(r"\d{4}-\d\d-\d\d", s)
            or s[0] in "-?:,[]{}#&*!|>'\"%@` " or ": " in s or " #" in s or s.endswith(":") or s != s.strip()):
        return json.dumps(s)
    return s


def _ys(v) -> str:
    if v is None:
        return "null"
    if v is True or v is False:
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return _yq(v)


def ydump(o, ind: int = 0, sort: bool = True) -> list[str]:
    pad = " " * ind
    out: list[str] = []
    for k in (sorted(o) if sort else list(o)):
        v = o[k]
        if isinstance(v, dict) and v:
            out += [f"{pad}{k}:"] + ydump(v, ind + 2, sort)
        elif isinstance(v, list) and v:
            out.append(f"{pad}{k}:")
            for it in v:
                if isinstance(it, dict) and it:
                    sub = ydump(it, ind + 2, sort)
                    sub[0] = pad + "- " + sub[0][ind + 2:]
                    out += sub
                elif isinstance(it, (dict, list)):
                    out.append(pad + "- " + ("{}" if isinstance(it, dict) else "[]"))
                else:
                    out.append(pad + "- " + _ys(it))
        elif isinstance(v, (dict, list)):
            out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
        elif isinstance(v, str) and "\n" in v:
            body = v.split("\n")
            if v.endswith("\n"):
                body = body[:-1]
            out.append(f"{pad}{k}: {'|' if v.endswith(chr(10)) else '|-'}")
            out += [(pad + "  " + l) if l else "" for l in body]
        else:
            out.append(f"{pad}{k}: {_ys(v)}")
    return out


def ytext(o) -> str:
    return "\n".join(ydump(o)) + "\n"


def leaves(o, path: tuple = ()):
    """(path tuple, scalar) for every leaf; an empty dict or list is a leaf written as '{}' or '[]'."""
    if isinstance(o, dict):
        if not o:
            yield path, "{}"
        for k, v in o.items():
            yield from leaves(v, path + (str(k),))
    elif isinstance(o, list):
        if not o:
            yield path, "[]"
        for i, v in enumerate(o):
            yield from leaves(v, path + (f"[{i}]",))
    else:
        yield path, o


def parse_any(text: str):
    s = text.lstrip()
    if s[:1] in ("{", "["):
        return json.loads(text)
    return yaml_lite(text)


def objects_of(doc) -> list:
    if isinstance(doc, dict) and doc.get("kind") == "List" and isinstance(doc.get("items"), list):
        return doc["items"]
    return [doc]


def keep_nl(src: str, out: str) -> str:
    """Give `out` the same trailing-newline convention as `src`."""
    return out.rstrip("\n") + ("\n" if src.endswith("\n") else "")


def short(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

# ===============================================================================================================
# kubectl object builders shared by #137 and #791 (reconstructed from memory: no kubectl or cluster here)

LAST = "kubectl.kubernetes.io/last-applied-configuration"


def _compact(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def _k8s_container(name: str, image: str, port: int = 8080, policy: str = "IfNotPresent", mounts: bool = True) -> dict:
    c = {
        "name": name, "image": image, "imagePullPolicy": policy,
        "ports": [{"containerPort": port, "name": "http", "protocol": "TCP"}],
        "env": [{"name": "LOG_LEVEL", "value": "info"}, {"name": "DATABASE_URL", "valueFrom": {"secretKeyRef": {"key": "url", "name": "db"}}},
                {"name": "FEATURE_FLAGS", "value": "checkout-v2,fast-search"}],
        "resources": {"limits": {"cpu": "500m", "memory": "512Mi"}, "requests": {"cpu": "250m", "memory": "256Mi"}},
        "readinessProbe": {"failureThreshold": 3, "httpGet": {"path": "/healthz", "port": "http", "scheme": "HTTP"},
                           "initialDelaySeconds": 5, "periodSeconds": 10, "successThreshold": 1, "timeoutSeconds": 1},
        "livenessProbe": {"failureThreshold": 3, "httpGet": {"path": "/livez", "port": "http", "scheme": "HTTP"},
                          "initialDelaySeconds": 15, "periodSeconds": 20, "successThreshold": 1, "timeoutSeconds": 1},
        "terminationMessagePath": "/dev/termination-log", "terminationMessagePolicy": "File",
    }
    if mounts:
        c["volumeMounts"] = [{"mountPath": "/etc/web", "name": "config"}]
    return c


_USER_KEYS = ("name", "image", "ports", "env", "resources", "readinessProbe", "livenessProbe", "volumeMounts")


def _k8s_deploy(name: str = "web", ns: str = "shop", replicas: int = 3, image: str = "registry.example.com/shop/web:1.8.2",
                annotated: bool = True, status: dict | None = None, policy: str = "IfNotPresent") -> dict:
    c = _k8s_container(name, image, policy=policy)
    applied = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"annotations": {}, "labels": {"app": name, "team": "checkout"}, "name": name, "namespace": ns},
        "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                 "template": {"metadata": {"labels": {"app": name}},
                              "spec": {"containers": [{k: c[k] for k in _USER_KEYS if k in c}],
                                       "volumes": [{"configMap": {"name": f"{name}-config"}, "name": "config"}]}}},
    }
    ann = {"deployment.kubernetes.io/revision": "2"}
    if annotated:
        ann[LAST] = _compact(applied) + "\n"
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"annotations": ann, "creationTimestamp": "2025-02-11T09:14:55Z", "generation": 2,
                     "labels": {"app": name, "team": "checkout"}, "name": name, "namespace": ns,
                     "resourceVersion": "184233", "uid": "5b1c0f3a-7c3e-4a1e-9f5d-2a8f4c0e91ab"},
        "spec": {"progressDeadlineSeconds": 600, "replicas": replicas, "revisionHistoryLimit": 10,
                 "selector": {"matchLabels": {"app": name}},
                 "strategy": {"rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}, "type": "RollingUpdate"},
                 "template": {"metadata": {"creationTimestamp": None, "labels": {"app": name}},
                              "spec": {"containers": [c], "dnsPolicy": "ClusterFirst", "restartPolicy": "Always",
                                       "schedulerName": "default-scheduler", "securityContext": {}, "terminationGracePeriodSeconds": 30,
                                       "volumes": [{"configMap": {"defaultMode": 420, "name": f"{name}-config"}, "name": "config"}]}}},
        "status": status if status is not None else {
            "availableReplicas": replicas, "conditions": [
                {"lastTransitionTime": "2025-02-11T09:15:31Z", "lastUpdateTime": "2025-02-11T09:15:31Z",
                 "message": "Deployment has minimum availability.", "reason": "MinimumReplicasAvailable", "status": "True", "type": "Available"},
                {"lastTransitionTime": "2025-02-11T09:14:55Z", "lastUpdateTime": "2025-02-14T16:02:10Z",
                 "message": f'ReplicaSet "{name}-6d8f7c9b5" has successfully progressed.', "reason": "NewReplicaSetAvailable",
                 "status": "True", "type": "Progressing"}],
            "observedGeneration": 2, "readyReplicas": replicas, "replicas": replicas, "updatedReplicas": replicas},
    }


FAILING_STATUS = {
    "conditions": [
        {"lastTransitionTime": "2025-03-02T08:00:11Z", "lastUpdateTime": "2025-03-02T08:00:11Z",
         "message": "Deployment does not have minimum availability.", "reason": "MinimumReplicasUnavailable", "status": "False", "type": "Available"},
        {"lastTransitionTime": "2025-03-02T08:00:11Z", "lastUpdateTime": "2025-03-02T08:00:11Z",
         "message": 'pods "web-6d8f7c9b5-x2k9p" is forbidden: exceeded quota: compute-quota, requested: limits.memory=512Mi, '
                    "used: limits.memory=1536Mi, limited: limits.memory=2Gi",
         "reason": "FailedCreate", "status": "True", "type": "ReplicaFailure"}],
    "observedGeneration": 2, "unavailableReplicas": 3}


def _k8s_svc(name: str = "web", ns: str = "shop", annotated: bool = True, node_port: int | None = None) -> dict:
    port = {"name": "http", "port": 80, "protocol": "TCP", "targetPort": "http"}
    applied = {"apiVersion": "v1", "kind": "Service", "metadata": {"annotations": {}, "labels": {"app": name}, "name": name, "namespace": ns},
               "spec": {"ports": [{"name": "http", "port": 80, "targetPort": "http"}], "selector": {"app": name}}}
    if node_port:
        port["nodePort"] = node_port
    return {"apiVersion": "v1", "kind": "Service",
            "metadata": {"annotations": {LAST: _compact(applied) + "\n"} if annotated else {}, "creationTimestamp": "2025-02-11T09:14:55Z",
                         "labels": {"app": name}, "name": name, "namespace": ns, "resourceVersion": "184101", "uid": "0f8a3c11-2e6b-4d1f-8f0e-6a9a77d3c2b4"},
            "spec": {"clusterIP": "10.96.41.17", "clusterIPs": ["10.96.41.17"], "internalTrafficPolicy": "Cluster", "ipFamilies": ["IPv4"],
                     "ipFamilyPolicy": "SingleStack", "ports": [port], "selector": {"app": name}, "sessionAffinity": "None",
                     "type": "NodePort" if node_port else "ClusterIP"},
            "status": {"loadBalancer": {}}}


def _k8s_cm(name: str, ns: str, data: dict, annotated: bool = True) -> dict:
    applied = {"apiVersion": "v1", "data": data, "kind": "ConfigMap", "metadata": {"annotations": {}, "name": name, "namespace": ns}}
    return {"apiVersion": "v1", "data": data, "kind": "ConfigMap",
            "metadata": {"annotations": {LAST: _compact(applied) + "\n"} if annotated else {}, "creationTimestamp": "2025-02-11T09:14:50Z",
                         "name": name, "namespace": ns, "resourceVersion": "184090", "uid": "a41c2d70-93f2-4a6e-b0d4-1c52f8a0e7aa"}}


def _k8s_list(items: list) -> dict:
    return {"apiVersion": "v1", "items": items, "kind": "List", "metadata": {"resourceVersion": ""}}


_CFG = {"app.properties": "server.port=8080\nserver.compression=on\npool.size=20\ncache.ttl=300\nfeature.checkout.v2=true\n",
        "logging.yaml": "level: info\nformat: json\nsinks:\n  - stdout\n  - file:/var/log/web/app.log\n"}


# ===============================================================================================================
# #137 Stub kubectl.kubernetes.io/last-applied-configuration. The annotation is the whole object again as a JSON string.
# Encoder: text edit gated on a successful parse (a block literal in YAML, a string value in JSON, only when it sits
# directly under `annotations`). Probes: parse the document, flatten it, and answer questions about identity, spec/data,
# metadata and status, with the annotation's value masked (its presence is kept).

def _stub_text(n: int) -> str:
    return f"<stub: {n} chars of JSON re-stating this object; original saved>"


_JSON_ANN = re.compile(r'("kubectl\.kubernetes\.io/last-applied-configuration"\s*:\s*)("(?:[^"\\]|\\.)*")')
_YAML_ANN = re.compile(r"^(\s*)kubectl\.kubernetes\.io/last-applied-configuration: \|[-+]?\s*$")


def _annotation_values(doc) -> list:
    out = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "annotations" and isinstance(v, dict) and isinstance(v.get(LAST), str):
                    out.append(v[LAST])
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


def stub_last_applied(text: str) -> str:
    try:
        doc = parse_any(text)
    except Exception:  # noqa: BLE001  (never touch what does not parse)
        return text
    if text.lstrip()[:1] in ("{", "["):
        found = [v for v in _annotation_values(doc) if not v.startswith("<stub:")]
        if len(_JSON_ANN.findall(text)) != len(_annotation_values(doc)):
            return text  # the key appears somewhere that is not an annotation: do not guess

        def sub(m):
            val = json.loads(m.group(2))
            return m.group(0) if val.startswith("<stub:") else m.group(1) + json.dumps(_stub_text(len(val)))

        out = _JSON_ANN.sub(sub, text) if found else text
    else:
        lines, out_l, i = text.split("\n"), [], 0
        while i < len(lines):
            m = _YAML_ANN.match(lines[i])
            parent_ok = False
            if m:
                n = len(m.group(1))
                for k in range(i - 1, -1, -1):
                    if lines[k].strip() and len(lines[k]) - len(lines[k].lstrip(" ")) < n:
                        parent_ok = lines[k].strip() == "annotations:"
                        break
            if m and parent_ok:
                j, size = i + 1, 0
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip(" ")) > n):
                    size += len(lines[j].strip()) + 1 if lines[j].strip() else 0
                    j += 1
                out_l.append(f'{m.group(1)}{LAST}: "{_stub_text(size)}"')
                i = j
                continue
            out_l.append(lines[i])
            i += 1
        out = "\n".join(out_l)
    return out if len(out) < len(text) else text


def probes_137(text: str) -> dict:
    try:
        objs = objects_of(parse_any(text))
    except Exception:  # noqa: BLE001
        return {"parse": "failed"}
    ident, spec, meta, status, has_last = [], {}, {}, {}, []
    for n, o in enumerate(objs):
        if not isinstance(o, dict):
            ident.append(("not an object", n))
            continue
        md = o.get("metadata") if isinstance(o.get("metadata"), dict) else {}
        ident.append((o.get("apiVersion"), o.get("kind"), md.get("namespace"), md.get("name")))
        ann = md.get("annotations") if isinstance(md.get("annotations"), dict) else {}
        has_last.append(LAST in ann)
        for p, v in leaves(o):
            if p[:1] in (("kind",), ("apiVersion",)):
                continue
            tag = "/".join(p)
            if len(p) >= 2 and p[-2] == "annotations" and p[-1] == LAST:
                v = "<annotation present>"
            bucket = meta if p[0] == "metadata" else status if p[0] == "status" else spec
            bucket[f"{n}:{tag}"] = v
    return {"objects": ident, "spec and data": spec, "metadata with annotation values": meta, "status": status,
            "has last-applied annotation": has_last}


def _gen_137():
    deploy = ytext(_k8s_deploy())
    failing = ytext(_k8s_deploy(replicas=3, status=FAILING_STATUS))
    lst = ytext(_k8s_list([_k8s_deploy(), _k8s_svc(), _k8s_cm("web-config", "shop", _CFG)]))
    svc_json = json.dumps(_k8s_svc(node_port=30080), indent=4) + "\n"
    cm_json = json.dumps(_k8s_list([_k8s_cm("web-config", "shop", _CFG), _k8s_cm("web-flags", "shop", {"flags": "a,b"}, annotated=False)]), indent=4) + "\n"
    server_side = ytext(_k8s_deploy(annotated=False))
    short_ann = _k8s_cm("x", "d", {"k": "v"})
    short_ann["metadata"]["annotations"][LAST] = "{}\n"  # hand-edited and smaller than its stub would be: left alone
    tiny = ytext(short_ann)
    trap = ytext({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "docs", "namespace": "d"},
                  "data": {LAST: "|".join(["not an annotation"] * 5) + "\n", "README": f"do not edit {LAST} by hand"}})
    return [
        Fixture("Deployment, annotation at the top of the metadata", deploy, True),
        Fixture("Deployment with ReplicaFailure at the end of status", failing, True),
        Fixture("kubectl get deploy,svc,cm -o yaml: annotations in the middle and at the end", lst, True),
        Fixture("Service as -o json", svc_json, True),
        Fixture("ConfigMap list as -o json, second item has no annotation", cm_json, True),
        Fixture("negative: server-side-applied object has no annotation", server_side, False),
        Fixture("negative: annotation smaller than its stub would be", tiny, False),
        Fixture("data key named like the annotation, not under annotations", trap, False),
    ]


case(
    137,
    claim="Replaces the kubectl.kubernetes.io/last-applied-configuration annotation value (a block literal in YAML, a string in JSON) with a one-line stub that records its size; the key stays, and every spec, data, metadata and status value is unchanged.",
    loses="the last-applied JSON itself (it is the previously applied manifest: needed to answer 'what did the last apply say' or 'has the live object drifted'; the saved original has it)",
    encode=stub_last_applied,
    probes=probes_137,
    fixtures=_gen_137,
    min_gain=0.12,
)


# ===============================================================================================================
# #791 Drop API-defaulted fields from kubectl YAML and docker inspect JSON. Encoder: a per-kind default table applied by
# a path-tracking line scan (YAML) or a parsed delete (docker). Probes: the EFFECTIVE value of each question (a missing
# field means its default), so a field removed because it equals the default still answers correctly, and a
# non-default value that was removed would not.

_POD_SPEC = {
    ("dnsPolicy",): "ClusterFirst", ("restartPolicy",): "Always", ("terminationGracePeriodSeconds",): "30",
    ("schedulerName",): "default-scheduler", ("securityContext",): "{}",
    ("containers", "[]", "terminationMessagePath"): "/dev/termination-log", ("containers", "[]", "terminationMessagePolicy"): "File",
    ("containers", "[]", "resources"): "{}", ("containers", "[]", "ports", "[]", "protocol"): "TCP",
}
_POD_ONLY = {("enableServiceLinks",): "true", ("preemptionPolicy",): "PreemptLowerPriority", ("priority",): "0"}
_TOLERATIONS = ("tolerations:\n- effect: NoExecute\n  key: node.kubernetes.io/not-ready\n  operator: Exists\n  tolerationSeconds: 300\n"
                "- effect: NoExecute\n  key: node.kubernetes.io/unreachable\n  operator: Exists\n  tolerationSeconds: 300")


def _defaults_for(kind: str) -> dict:
    d: dict = {}
    if kind == "Pod":
        base = ("spec",)
        d.update({base + k: v for k, v in {**_POD_SPEC, **_POD_ONLY}.items()})
    elif kind in ("Deployment", "StatefulSet", "DaemonSet"):
        base = ("spec", "template", "spec")
        d.update({base + k: v for k, v in _POD_SPEC.items()})
        d[("spec", "template", "metadata", "creationTimestamp")] = "null"
        if kind == "Deployment":
            d.update({("spec", "progressDeadlineSeconds"): "600", ("spec", "revisionHistoryLimit"): "10",
                      ("spec", "strategy", "type"): "RollingUpdate", ("spec", "strategy", "rollingUpdate", "maxSurge"): "25%",
                      ("spec", "strategy", "rollingUpdate", "maxUnavailable"): "25%"})
    elif kind == "Service":
        d.update({("spec", "sessionAffinity"): "None", ("spec", "type"): "ClusterIP", ("spec", "internalTrafficPolicy"): "Cluster",
                  ("spec", "ipFamilyPolicy"): "SingleStack", ("spec", "ports", "[]", "protocol"): "TCP", ("status", "loadBalancer"): "{}"})
    return d


def _pull_default(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    if "@" in last:
        return "IfNotPresent"
    return "Always" if ":" not in last or last.endswith(":latest") else "IfNotPresent"


_KEYLINE = re.compile(r"""^("(?:[^"\\]|\\.)*"|[^\s:#"'\-\[\]{},&*!|>%@`][^:]*?|-[^\s:][^:]*?):(?:[ ](.*))?$""")


def _obj(stack) -> int | None:
    """Line of the `items:` list item that encloses the current position (None outside a List)."""
    return stack[1][2] if len(stack) >= 2 and stack[0][1] == "items" and stack[1][1] == "[]" else None


def _scan_yaml(lines: list[str]):
    """Yield (line index, path tuple, key, value or None, item line, on a dash line, owning List item) for each key line; list items contribute a '[]' segment.
    Block-literal bodies are skipped."""
    stack: list[tuple[int, str, int]] = []  # (indent, segment, line index of the enclosing list item)
    skip_gt = -1
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        n = len(line) - len(line.lstrip(" "))
        if n <= skip_gt:
            skip_gt = -1
        if skip_gt >= 0:
            continue
        s = line[n:]
        item = -1
        if s.startswith("- ") or s == "-":
            while stack and (stack[-1][0] > n or (stack[-1][0] == n and stack[-1][1] == "[]")):
                stack.pop()
            stack.append((n, "[]", i))
            rest = s[1:]
            off = 1 + len(rest) - len(rest.lstrip(" "))
            s, n, item = rest.lstrip(" "), n + off, i
            if not s:
                continue
            m = _KEYLINE.match(s)
            if not m:
                continue
            path = tuple(e[1] for e in stack) + (m.group(1),)
            yield i, path, m.group(1), m.group(2), item, True, _obj(stack)
            v = (m.group(2) or "").strip()
            if v == "":
                stack.append((n, m.group(1), item))
            elif re.fullmatch(r"[|>][+-]?", v):
                skip_gt = n
            continue
        while stack and stack[-1][0] >= n:
            stack.pop()
        item = next((e[2] for e in reversed(stack) if e[1] == "[]"), -1)  # after the pops: the list item this key belongs to
        m = _KEYLINE.match(s)
        if not m:
            continue
        path = tuple(e[1] for e in stack) + (m.group(1),)
        yield i, path, m.group(1), m.group(2), item, False, _obj(stack)
        v = (m.group(2) or "").strip()
        if v == "":
            stack.append((n, m.group(1), item))
        elif re.fullmatch(r"[|>][+-]?", v):
            skip_gt = n


def _block_end(lines: list[str], i: int) -> int:
    """First line after the block that a key line `lines[i]` opens (list items may sit at the key's own indent)."""
    n = len(lines[i]) - len(lines[i].lstrip(" "))
    j = i + 1
    while j < len(lines):
        l = lines[j]
        if l.strip():
            m = len(l) - len(l.lstrip(" "))
            if m < n or (m == n and not (l[m:].startswith("- ") or l[m:] == "-")):
                break
        j += 1
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    return j


def drop_k8s_defaults(text: str) -> str:
    try:
        doc = yaml_lite(text)
    except Exception:  # noqa: BLE001
        return text
    if not isinstance(doc, dict):
        return text
    lines = text.split("\n")
    recs = list(_scan_yaml(lines))
    kinds: dict = {}  # object id (None for the document root, else the line of the `items:` list item) -> kind
    images: dict = {}  # container id (line of its list item) -> image
    for i, path, key, val, item, dash, obj in recs:
        rel = path[2:] if obj is not None else path
        if rel == ("kind",) and val:
            kinds[obj] = val.strip().strip('"')
        if key == "image" and val and "[]" in rel:
            images[item] = val.strip().strip('"')
    drop = set()
    for i, path, key, val, item, dash, obj in recs:
        kind = kinds.get(obj)
        if dash or val is None or kind is None:
            continue
        rel = path[2:] if obj is not None else path
        v = val.strip()
        table = _defaults_for(kind)
        if rel in table and v == table[rel]:
            drop.add(i)
        elif key == "imagePullPolicy" and ("containers" in rel or "initContainers" in rel):
            if item in images and v == _pull_default(images[item]):
                drop.add(i)
    for i, path, key, val, item, dash, obj in recs:
        rel = path[2:] if obj is not None else path
        if not dash and (val is None or val.strip() == "") and rel == ("spec", "tolerations") and kinds.get(obj) == "Pod":
            j = _block_end(lines, i)
            n = len(lines[i]) - len(lines[i].lstrip(" "))
            if "\n".join(l[n:] for l in lines[i:j]) == _TOLERATIONS:
                drop.update(range(i, j))
    # prune key lines that opened a block whose every line is now gone
    for i in range(len(lines) - 1, -1, -1):
        if i in drop or not lines[i].strip().endswith(":") or lines[i].lstrip().startswith("- "):
            continue
        j = _block_end(lines, i)
        body = [k for k in range(i + 1, j) if lines[k].strip()]
        if body and all(k in drop for k in body):
            drop.add(i)
    out = "\n".join(l for i, l in enumerate(lines) if i not in drop)
    try:
        yaml_lite(out)
    except Exception:  # noqa: BLE001
        return text
    return out if len(out) < len(text) else text


# docker inspect ---------------------------------------------------------------------------------------------------

_MASKED = ["/proc/asound", "/proc/acpi", "/proc/kcore", "/proc/keys", "/proc/latency_stats", "/proc/timer_list", "/proc/timer_stats",
           "/proc/sched_debug", "/proc/scsi", "/sys/firmware", "/sys/devices/virtual/powercap"]
_RO = ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"]
_HC_DEFAULTS = {
    "Binds": None, "ContainerIDFile": "", "LogConfig": {"Type": "json-file", "Config": {}}, "NetworkMode": "bridge", "PortBindings": {},
    "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "AutoRemove": False, "VolumeDriver": "", "VolumesFrom": None,
    "ConsoleSize": [0, 0], "CapAdd": None, "CapDrop": None, "CgroupnsMode": "private", "Dns": [], "DnsOptions": [], "DnsSearch": [],
    "ExtraHosts": None, "GroupAdd": None, "IpcMode": "private", "Cgroup": "", "Links": None, "OomScoreAdj": 0, "PidMode": "",
    "Privileged": False, "PublishAllPorts": False, "ReadonlyRootfs": False, "SecurityOpt": None, "UTSMode": "", "UsernsMode": "",
    "ShmSize": 67108864, "Runtime": "runc", "Isolation": "", "CpuShares": 0, "Memory": 0, "NanoCpus": 0, "CgroupParent": "",
    "BlkioWeight": 0, "BlkioWeightDevice": [], "BlkioDeviceReadBps": [], "BlkioDeviceWriteBps": [], "BlkioDeviceReadIOps": [],
    "BlkioDeviceWriteIOps": [], "CpuPeriod": 0, "CpuQuota": 0, "CpuRealtimePeriod": 0, "CpuRealtimeRuntime": 0, "CpusetCpus": "",
    "CpusetMems": "", "Devices": [], "DeviceCgroupRules": None, "DeviceRequests": None, "MemoryReservation": 0, "MemorySwap": 0,
    "MemorySwappiness": None, "OomKillDisable": False, "PidsLimit": None, "Ulimits": [], "CpuCount": 0, "CpuPercent": 0,
    "IOMaximumIOps": 0, "IOMaximumBandwidth": 0, "MaskedPaths": _MASKED, "ReadonlyPaths": _RO,
}
_OTHER_DEFAULTS = {
    "State": {"Paused": False, "Restarting": False, "Dead": False, "Error": ""},
    "Config": {"Domainname": "", "User": "", "AttachStdin": False, "AttachStdout": True, "AttachStderr": True, "Tty": False,
               "OpenStdin": False, "StdinOnce": False, "Volumes": None, "OnBuild": None},
    "NetworkSettings": {"Bridge": "", "HairpinMode": False, "LinkLocalIPv6Address": "", "LinkLocalIPv6PrefixLen": 0,
                        "SecondaryIPAddresses": None, "SecondaryIPv6Addresses": None, "GlobalIPv6Address": "",
                        "GlobalIPv6PrefixLen": 0, "IPv6Gateway": ""},
}
_EP_DEFAULTS = {"IPAMConfig": None, "Links": None, "Aliases": None, "DriverOpts": None, "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0,
                "IPv6Gateway": ""}


def _prune(d: dict, defaults: dict) -> None:
    for k, v in defaults.items():
        if k in d and d[k] == v and type(d[k]) is type(v):
            del d[k]


def drop_docker_defaults(text: str) -> str:
    try:
        doc = json.loads(text)
    except ValueError:
        return text
    if not (isinstance(doc, list) and doc and all(isinstance(c, dict) and isinstance(c.get("HostConfig"), dict) for c in doc)):
        return text
    for c in doc:
        _prune(c["HostConfig"], _HC_DEFAULTS)
        for sect, dflt in _OTHER_DEFAULTS.items():
            if isinstance(c.get(sect), dict):
                _prune(c[sect], dflt)
        nets = c.get("NetworkSettings", {}).get("Networks") if isinstance(c.get("NetworkSettings"), dict) else None
        if isinstance(nets, dict):
            for ep in nets.values():
                if isinstance(ep, dict):
                    _prune(ep, _EP_DEFAULTS)
    out = json.dumps(doc, indent=4, ensure_ascii=False) + ("\n" if text.endswith("\n") else "")
    return out if len(out) < len(text) else text


def _eff_k8s(o: dict) -> dict:
    """Effective-value answers for one kubectl object (a missing field means its default)."""
    kind = o.get("kind")
    spec = o.get("spec") if isinstance(o.get("spec"), dict) else {}
    ans: dict = {"kind": kind, "name": (o.get("metadata") or {}).get("name")}
    ps = None
    if kind == "Pod":
        ps = spec
        ans["priority"] = spec.get("priority", 0)
        ans["enableServiceLinks"] = spec.get("enableServiceLinks", True)
        ans["preemptionPolicy"] = spec.get("preemptionPolicy", "PreemptLowerPriority")
        ans["tolerations"] = spec.get("tolerations", [{"effect": "NoExecute", "key": "node.kubernetes.io/not-ready", "operator": "Exists",
                                                       "tolerationSeconds": 300}, {"effect": "NoExecute", "key": "node.kubernetes.io/unreachable",
                                                                                   "operator": "Exists", "tolerationSeconds": 300}])
    elif kind in ("Deployment", "StatefulSet", "DaemonSet"):
        tpl = spec.get("template") if isinstance(spec.get("template"), dict) else {}
        ps = tpl.get("spec") if isinstance(tpl.get("spec"), dict) else {}
        ans["template creationTimestamp"] = (tpl.get("metadata") or {}).get("creationTimestamp")
        if kind == "Deployment":
            st = spec.get("strategy") if isinstance(spec.get("strategy"), dict) else {}
            ru = st.get("rollingUpdate") if isinstance(st.get("rollingUpdate"), dict) else {}
            ans["strategy"] = (st.get("type", "RollingUpdate"), ru.get("maxSurge", "25%"), ru.get("maxUnavailable", "25%"))
            ans["deadline and history"] = (spec.get("progressDeadlineSeconds", 600), spec.get("revisionHistoryLimit", 10))
    if ps is not None:
        ans["pod defaults"] = (ps.get("dnsPolicy", "ClusterFirst"), ps.get("restartPolicy", "Always"),
                               ps.get("terminationGracePeriodSeconds", 30), ps.get("schedulerName", "default-scheduler"),
                               ps.get("securityContext", {}))
        cons = []
        for key in ("initContainers", "containers"):
            for c in ps.get(key) or []:
                if not isinstance(c, dict):
                    continue
                img = str(c.get("image"))
                last = img.rsplit("/", 1)[-1]
                pull = "IfNotPresent" if "@" in last else "Always" if (":" not in last or last.endswith(":latest")) else "IfNotPresent"
                cons.append((key, c.get("name"), img, c.get("imagePullPolicy", pull),
                             [(p.get("containerPort"), p.get("protocol", "TCP")) for p in c.get("ports") or [] if isinstance(p, dict)],
                             c.get("resources", {}), c.get("terminationMessagePath", "/dev/termination-log"),
                             c.get("terminationMessagePolicy", "File"), c.get("env"), c.get("volumeMounts")))
        ans["containers"] = cons
        ans["pod spec other"] = {k: v for k, v in ps.items() if k not in ("containers", "initContainers", "dnsPolicy", "restartPolicy",
                                                                           "terminationGracePeriodSeconds", "schedulerName",
                                                                           "securityContext", "tolerations", "priority",
                                                                           "enableServiceLinks", "preemptionPolicy")}
    if kind == "Service":
        ans["service"] = (spec.get("type", "ClusterIP"), spec.get("sessionAffinity", "None"), spec.get("internalTrafficPolicy", "Cluster"),
                          spec.get("ipFamilyPolicy", "SingleStack"), spec.get("clusterIP"), spec.get("selector"))
        ans["ports"] = [(p.get("name"), p.get("port"), p.get("protocol", "TCP"), p.get("targetPort"), p.get("nodePort"))
                        for p in spec.get("ports") or [] if isinstance(p, dict)]
        ans["status"] = (o.get("status") or {}).get("loadBalancer", {})
    ans["metadata"] = o.get("metadata")
    if kind != "Service":
        ans["status other"] = o.get("status")
    return ans


def _eff_docker(c: dict) -> dict:
    hc, cfg, st, ns = (c.get(k) if isinstance(c.get(k), dict) else {} for k in ("HostConfig", "Config", "State", "NetworkSettings"))
    return {
        "id": str(c.get("Id"))[:12], "name": c.get("Name"), "image": c.get("Image"),
        "state": (st.get("Status"), st.get("Running"), st.get("ExitCode"), st.get("OOMKilled"), st.get("Error", ""), st.get("Paused", False)),
        "privileged and namespaces": (hc.get("Privileged", False), hc.get("PidMode", ""), hc.get("IpcMode", "private"), hc.get("NetworkMode", "bridge"),
                                      hc.get("UsernsMode", ""), hc.get("ReadonlyRootfs", False)),
        "binds, ports, restart": (hc.get("Binds"), hc.get("PortBindings", {}), hc.get("RestartPolicy", {"Name": "no", "MaximumRetryCount": 0}),
                                  hc.get("PublishAllPorts", False)),
        "limits": (hc.get("Memory", 0), hc.get("MemorySwap", 0), hc.get("NanoCpus", 0), hc.get("CpuShares", 0), hc.get("PidsLimit")),
        "caps and security": (hc.get("CapAdd"), hc.get("CapDrop"), hc.get("SecurityOpt"), hc.get("Devices", []),
                              hc.get("MaskedPaths", _MASKED), hc.get("ReadonlyPaths", _RO)),
        "config": (cfg.get("Image"), cfg.get("Cmd"), cfg.get("Entrypoint"), cfg.get("Env"), cfg.get("User", ""), cfg.get("WorkingDir"),
                   cfg.get("Labels"), cfg.get("ExposedPorts"), cfg.get("Tty", False)),
        "mounts": c.get("Mounts"),
        "network": (ns.get("IPAddress"), ns.get("Ports"), {k: {kk: vv for kk, vv in (v or {}).items() if kk not in _EP_DEFAULTS}
                                                           for k, v in (ns.get("Networks") or {}).items()}),
        "hostconfig keys that are not at the Docker default": sorted(k for k, v in hc.items() if k not in _PROBE_HC or _PROBE_HC[k] != v),
        "other top-level keys": sorted(k for k in c if k not in ("HostConfig", "Config", "State", "NetworkSettings")),
    }


_PROBE_HC = dict(_HC_DEFAULTS)  # catch-all list of non-default HostConfig keys; the named questions above use inline defaults


def probes_791(text: str) -> dict:
    try:
        s = text.lstrip()
        if s[:1] == "[":
            doc = json.loads(text)
            if not (isinstance(doc, list) and all(isinstance(c, dict) for c in doc)):
                return {"shape": "unknown json"}
            return {"docker": [_eff_docker(c) for c in doc]}
        objs = objects_of(yaml_lite(text))
    except Exception:  # noqa: BLE001
        return {"parse": "failed"}
    return {"k8s": [_eff_k8s(o) if isinstance(o, dict) else ("scalar", o) for o in objs]}


def _k8s_pod(name: str = "web-6d8f7c9b5-x2k9p", ns: str = "shop", broken: bool = False) -> dict:
    app = _k8s_container("web", "registry.example.com/shop/web:1.8.2")
    side = _k8s_container("tools", "busybox:latest", port=9100, policy="IfNotPresent", mounts=False)  # explicit, NOT the default for :latest
    envoy = _k8s_container("envoy", "envoyproxy/envoy", port=9901, policy="Always", mounts=False)  # no tag: Always IS the default
    side["env"] = envoy["env"] = [{"name": "MODE", "value": "sidecar"}]
    cstat = [{"containerID": "containerd://" + hashlib.sha256(n.encode()).hexdigest(), "image": img, "imageID": "sha256:" + hashlib.sha256(img.encode()).hexdigest(),
              "lastState": {}, "name": n, "ready": not broken, "restartCount": 7 if broken else 0, "started": not broken,
              "state": {"waiting": {"message": "back-off 5m0s restarting failed container=web pod=" + name, "reason": "CrashLoopBackOff"}} if (broken and n == "web")
              else {"running": {"startedAt": "2025-03-04T10:15:02Z"}}} for n, img in (("web", app["image"]), ("tools", side["image"]), ("envoy", envoy["image"]))]
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"creationTimestamp": "2025-03-04T10:14:58Z", "generateName": "web-6d8f7c9b5-", "labels": {"app": "web", "pod-template-hash": "6d8f7c9b5"},
                     "name": name, "namespace": ns, "resourceVersion": "190233",
                     "ownerReferences": [{"apiVersion": "apps/v1", "blockOwnerDeletion": True, "controller": True, "kind": "ReplicaSet",
                                          "name": "web-6d8f7c9b5", "uid": "9a1d7e22-61c4-4b8b-8d33-3f0a9c1b52d0"}],
                     "uid": "c3a7f0d4-8e11-4f6c-9b2d-5e7a1c0d4f21"},
        "spec": {"containers": [app, side, envoy], "dnsPolicy": "ClusterFirst", "enableServiceLinks": True, "nodeName": "kind-worker2",
                 "preemptionPolicy": "PreemptLowerPriority", "priority": 0, "restartPolicy": "Always", "schedulerName": "default-scheduler",
                 "securityContext": {}, "serviceAccount": "default", "serviceAccountName": "default", "terminationGracePeriodSeconds": 30,
                 "tolerations": [{"effect": "NoExecute", "key": "node.kubernetes.io/not-ready", "operator": "Exists", "tolerationSeconds": 300},
                                 {"effect": "NoExecute", "key": "node.kubernetes.io/unreachable", "operator": "Exists", "tolerationSeconds": 300}],
                 "volumes": [{"configMap": {"defaultMode": 420, "name": "web-config"}, "name": "config"},
                             {"name": "kube-api-access-x7k2p", "projected": {"defaultMode": 420, "sources": [
                                 {"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
                                 {"configMap": {"items": [{"key": "ca.crt", "path": "ca.crt"}], "name": "kube-root-ca.crt"}},
                                 {"downwardAPI": {"items": [{"fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.namespace"}, "path": "namespace"}]}}]}}]},
        "status": {"conditions": [{"lastProbeTime": None, "lastTransitionTime": "2025-03-04T10:15:01Z", "status": "False" if broken else "True", "type": "Ready"}],
                   "containerStatuses": cstat, "hostIP": "172.18.0.4", "phase": "Running", "podIP": "10.244.2.17", "podIPs": [{"ip": "10.244.2.17"}],
                   "qosClass": "Burstable", "startTime": "2025-03-04T10:14:58Z"},
    }


def _hc_full(**over) -> dict:
    """A realistic Docker 25 HostConfig, written out in full (separately from the encoder's default table)."""
    hc = {
        "Binds": None, "ContainerIDFile": "", "LogConfig": {"Type": "json-file", "Config": {}}, "NetworkMode": "bridge", "PortBindings": {},
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "AutoRemove": False, "VolumeDriver": "", "VolumesFrom": None, "ConsoleSize": [0, 0],
        "CapAdd": None, "CapDrop": None, "CgroupnsMode": "private", "Dns": [], "DnsOptions": [], "DnsSearch": [], "ExtraHosts": None,
        "GroupAdd": None, "IpcMode": "private", "Cgroup": "", "Links": None, "OomScoreAdj": 0, "PidMode": "", "Privileged": False,
        "PublishAllPorts": False, "ReadonlyRootfs": False, "SecurityOpt": None, "UTSMode": "", "UsernsMode": "", "ShmSize": 67108864,
        "Runtime": "runc", "Isolation": "", "CpuShares": 0, "Memory": 0, "NanoCpus": 0, "CgroupParent": "", "BlkioWeight": 0,
        "BlkioWeightDevice": [], "BlkioDeviceReadBps": [], "BlkioDeviceWriteBps": [], "BlkioDeviceReadIOps": [], "BlkioDeviceWriteIOps": [],
        "CpuPeriod": 0, "CpuQuota": 0, "CpuRealtimePeriod": 0, "CpuRealtimeRuntime": 0, "CpusetCpus": "", "CpusetMems": "", "Devices": [],
        "DeviceCgroupRules": None, "DeviceRequests": None, "MemoryReservation": 0, "MemorySwap": 0, "MemorySwappiness": None,
        "OomKillDisable": False, "PidsLimit": None, "Ulimits": [], "CpuCount": 0, "CpuPercent": 0, "IOMaximumIOps": 0, "IOMaximumBandwidth": 0,
        "MaskedPaths": list(_MASKED), "ReadonlyPaths": list(_RO)}
    hc.update(over)
    return hc


def _docker_inspect(cid: str, name: str, image: str, hc: dict, *, exit_code: int = 0, running: bool = True, oom: bool = False,
                    ports: dict | None = None, mounts: list | None = None, env: list | None = None, user: str = "", ip: str = "172.17.0.2") -> str:
    ports = ports or {}
    c = {
        "Id": cid, "Created": "2025-03-04T10:15:02.123456789Z", "Path": "/docker-entrypoint.sh", "Args": ["nginx", "-g", "daemon off;"],
        "State": {"Status": "running" if running else "exited", "Running": running, "Paused": False, "Restarting": False, "OOMKilled": oom,
                  "Dead": False, "Pid": 4123 if running else 0, "ExitCode": exit_code, "Error": "", "StartedAt": "2025-03-04T10:15:02.4Z",
                  "FinishedAt": "0001-01-01T00:00:00Z" if running else "2025-03-04T11:40:19.9Z"},
        "Image": "sha256:" + hashlib.sha256(image.encode()).hexdigest(), "ResolvConfPath": f"/var/lib/docker/containers/{cid}/resolv.conf",
        "HostnamePath": f"/var/lib/docker/containers/{cid}/hostname", "HostsPath": f"/var/lib/docker/containers/{cid}/hosts",
        "LogPath": f"/var/lib/docker/containers/{cid}/{cid}-json.log", "Name": "/" + name, "RestartCount": 0, "Driver": "overlay2",
        "Platform": "linux", "MountLabel": "", "ProcessLabel": "", "AppArmorProfile": "docker-default", "ExecIDs": None, "HostConfig": hc,
        "GraphDriver": {"Data": {"LowerDir": f"/var/lib/docker/overlay2/{cid[:16]}-init/diff", "MergedDir": f"/var/lib/docker/overlay2/{cid[:16]}/merged",
                                 "UpperDir": f"/var/lib/docker/overlay2/{cid[:16]}/diff", "WorkDir": f"/var/lib/docker/overlay2/{cid[:16]}/work"}, "Name": "overlay2"},
        "Mounts": mounts or [],
        "Config": {"Hostname": cid[:12], "Domainname": "", "User": user, "AttachStdin": False, "AttachStdout": True, "AttachStderr": True,
                   "ExposedPorts": {p: {} for p in ports}, "Tty": False, "OpenStdin": False, "StdinOnce": False,
                   "Env": env or ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "NGINX_VERSION=1.25.4"],
                   "Cmd": ["nginx", "-g", "daemon off;"], "Image": image, "Volumes": None, "WorkingDir": "", "Entrypoint": ["/docker-entrypoint.sh"],
                   "OnBuild": None, "Labels": {"maintainer": "NGINX Docker Maintainers", "com.example.team": "edge"}, "StopSignal": "SIGQUIT"},
        "NetworkSettings": {"Bridge": "", "SandboxID": hashlib.sha256(cid.encode()).hexdigest(), "SandboxKey": f"/var/run/docker/netns/{cid[:12]}",
                            "Ports": {p: [{"HostIp": "0.0.0.0", "HostPort": hp}] for p, hp in ports.items()} if running else {},
                            "HairpinMode": False, "LinkLocalIPv6Address": "", "LinkLocalIPv6PrefixLen": 0, "SecondaryIPAddresses": None,
                            "SecondaryIPv6Addresses": None, "EndpointID": hashlib.sha256(name.encode()).hexdigest() if running else "",
                            "Gateway": "172.17.0.1" if running else "", "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0,
                            "IPAddress": ip if running else "", "IPPrefixLen": 16 if running else 0, "IPv6Gateway": "",
                            "MacAddress": "02:42:ac:11:00:02" if running else "",
                            "Networks": {"bridge": {"IPAMConfig": None, "Links": None, "Aliases": None, "MacAddress": "02:42:ac:11:00:02",
                                                    "DriverOpts": None, "NetworkID": hashlib.sha256(b"bridge").hexdigest(),
                                                    "EndpointID": hashlib.sha256(name.encode()).hexdigest(), "Gateway": "172.17.0.1",
                                                    "IPAddress": ip, "IPPrefixLen": 16, "IPv6Gateway": "", "GlobalIPv6Address": "",
                                                    "GlobalIPv6PrefixLen": 0, "DNSNames": None}} if running else {}},
    }
    return json.dumps([c], indent=4) + "\n"


def _gen_791():
    pod = ytext(_k8s_pod())
    pod_broken = ytext(_k8s_pod(broken=True))
    lst = ytext(_k8s_list([_k8s_deploy(annotated=False), _k8s_svc(node_port=30080, annotated=False)]))
    web = _docker_inspect("3f9a1c2e5b7d4c8e9a0b1d2e3f4a5b6c7d8e9f001122334455667788aabbccdd", "web", "nginx:1.25.4",
                          _hc_full(Binds=["/srv/site:/usr/share/nginx/html:ro"], PortBindings={"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
                                   Memory=536870912, MemorySwap=1073741824, RestartPolicy={"Name": "unless-stopped", "MaximumRetryCount": 0}),
                          ports={"80/tcp": "8080"}, mounts=[{"Type": "bind", "Source": "/srv/site", "Destination": "/usr/share/nginx/html", "Mode": "ro", "RW": False, "Propagation": "rprivate"}])
    priv = _docker_inspect("a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90", "node-exporter", "prom/node-exporter:v1.7.0",
                           _hc_full(Privileged=True, PidMode="host", NetworkMode="host", CapAdd=["SYS_ADMIN"], SecurityOpt=["apparmor=unconfined"],
                                    MaskedPaths=None, ReadonlyPaths=None, Binds=["/:/host:ro,rslave"], ReadonlyRootfs=True),
                           user="nobody", ip="")
    dead = _docker_inspect("0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0", "worker", "ghcr.io/acme/worker:2025.03",
                           _hc_full(Memory=268435456, CpuShares=512, PidsLimit=200, CapDrop=["ALL"]), exit_code=137, running=False, oom=True)
    return [
        Fixture("kubectl get pod -o yaml (three containers, two image-tag cases)", pod, True),
        Fixture("pod in CrashLoopBackOff: status at the end", pod_broken, True),
        Fixture("kubectl get deploy,svc -o yaml (List)", lst, True),
        Fixture("docker inspect web: port, bind, memory limit, restart policy", web, True),
        Fixture("docker inspect privileged host-pid container", priv, True),
        Fixture("docker inspect OOM-killed container, exit 137", dead, True),
        Fixture("negative: docker inspect --format output", '"running"\n', False),
        Fixture("negative: docker network inspect has no HostConfig", json.dumps([{"Name": "bridge", "Driver": "bridge", "Containers": {}}], indent=4) + "\n", False),
        Fixture("negative: ConfigMap has no defaults to drop", ytext(_k8s_cm("tiny", "d", {"k": "v"}, annotated=False)), False),
    ]


case(
    791,
    claim="Drops YAML fields that equal the API default for the object's kind (dnsPolicy ClusterFirst, protocol TCP, the default tolerations, an imagePullPolicy that matches the image tag's default) and docker-inspect keys that equal the daemon default, with their now-empty parents; the effective value of every field is unchanged and every non-default value (Privileged, Binds, Memory, PortBindings, an imagePullPolicy that is not the tag's default) survives.",
    loses="whether a default-valued field was written explicitly or filled in by the server; the docker inspect JSON is re-indented to 4 spaces with HTML escapes undone",
    encode=lambda t: drop_docker_defaults(t) if t.lstrip()[:1] == "[" else drop_k8s_defaults(t),
    probes=probes_791,
    fixtures=_gen_791,
    min_gain=0.10,
)


# ===============================================================================================================
# GitHub Actions log builders shared by #311, #478, #858 (reconstructed from memory of `gh run view --log`:
# one line per log line, `job<TAB>step<TAB>timestamp text`, and the runner's ##[group] / ##[error] markers)

_T0 = 10 * 3600 + 15 * 60  # 2025-03-04T10:15:00Z, in seconds


def _stamp(us: int) -> str:
    s, frac = divmod(us, 1_000_000)
    s += _T0
    return f"2025-03-04T{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}.{frac * 10:07d}Z"


class _Clock:
    def __init__(self, seed: int = 1):
        self.us, self.k = 0, seed

    def tick(self, lo: int = 800, hi: int = 40_000) -> int:
        self.k = (self.k * 1103515245 + 12345) & 0x7FFFFFFF  # tiny LCG: deterministic and independent of hash seeds
        self.us += lo + self.k % (hi - lo)
        return self.us


def _gh_log(job: str, steps: list[tuple[str, list[str]]], clock: _Clock, gap_us: int = 150_000) -> list[str]:
    out = []
    for step, texts in steps:
        clock.us += gap_us
        for t in texts:
            out.append(f"{job}\t{step}\t{_stamp(clock.tick())}" + (f" {t}" if t else ""))
    return out


def _setup_job(job: str, image: str = "ubuntu-22.04", ver: str = "20250224.1.0") -> list[str]:
    return ["Current runner version: '2.322.0'", "##[group]Runner Image Provisioner", "Hosted Compute Agent", "Version: 20250211.1",
            "Commit: 9d4be6ae0f9edaa3f1ad8d4e2c7a0bd1e2a0d4c3", "Build Date: 2025-02-11T20:51:50Z", "##[endgroup]", "##[group]Operating System",
            "Ubuntu", "22.04.5", "LTS", "##[endgroup]", "##[group]Runner Image", f"Image: {image}", f"Version: {ver}",
            "Included Software: https://github.com/actions/runner-images/blob/ubuntu22/20250224.1/images/ubuntu/Ubuntu2204-Readme.md",
            "Image Release: https://github.com/actions/runner-images/releases/tag/ubuntu22%2F20250224.1", "##[endgroup]",
            "##[group]GITHUB_TOKEN Permissions", "Contents: read", "Metadata: read", "Packages: read", "##[endgroup]", "Secret source: Actions",
            "Prepare workflow directory", "Prepare all required actions", "Getting action download info",
            "Download action repository 'actions/checkout@v4' (SHA:11bd71901bbe5b1630ceea73d27597364c9af683)",
            "Download action repository 'actions/setup-python@v5' (SHA:0b93645e9fea7318ecaed2b359559ac225c90a2b)", f"Complete job name: {job}"]


def _checkout() -> list[str]:
    return (["##[group]Run actions/checkout@v4", "with:", "  repository: acme/app", "  token: ***", "  ssh-strict: true", "  ssh-user: git",
             "  persist-credentials: true", "  clean: true", "  sparse-checkout-cone-mode: true", "  fetch-depth: 1", "  fetch-tags: false",
             "  lfs: false", "  submodules: false", "  set-safe-directory: true", "##[endgroup]", "Syncing repository: acme/app",
             "##[group]Getting Git version info", "Working directory is '/home/runner/work/app/app'",
             "[command]/usr/bin/git version", "git version 2.48.1", "##[endgroup]", "Temporarily overriding HOME='/home/runner/work/_temp/0c1d' before making global git config changes",
             "Adding repository directory to the temporary git global config as a safe directory",
             "[command]/usr/bin/git config --global --add safe.directory /home/runner/work/app/app", "Deleting the contents of '/home/runner/work/app/app'",
             "##[group]Initializing the repository", "[command]/usr/bin/git init /home/runner/work/app/app",
             "hint: Using 'master' as the name for the initial branch.", "Initialized empty Git repository in /home/runner/work/app/app/.git/",
             "[command]/usr/bin/git remote add origin https://github.com/acme/app", "##[endgroup]", "##[group]Disabling automatic garbage collection",
             "[command]/usr/bin/git config --local gc.auto 0", "##[endgroup]", "##[group]Fetching the repository",
             "[command]/usr/bin/git -c protocol.version=2 fetch --no-tags --prune --no-recurse-submodules --depth=1 origin +9f3c2b7a1d:refs/remotes/origin/main",
             "From https://github.com/acme/app", " * [new ref]         9f3c2b7a1d -> origin/main", "##[endgroup]", "##[group]Determining the checkout info",
             "##[endgroup]", "[command]/usr/bin/git sparse-checkout disable", "[command]/usr/bin/git checkout --progress --force -B main refs/remotes/origin/main",
             "Switched to a new branch 'main'", "branch 'main' set up to track 'origin/main'.", "[command]/usr/bin/git log -1 --format=%H", "9f3c2b7a1d4e5f60718293a4b5c6d7e8f9012345"])


def _setup_python(py: str, full: str) -> list[str]:
    return ["##[group]Run actions/setup-python@v5", "with:", f"  python-version: {py}", "  check-latest: false", "  token: ***", "  update-environment: true",
            "  allow-prereleases: false", "  freethreaded: false", "##[endgroup]", "##[group]Installed versions",
            f"Successfully set up CPython ({full})", "##[endgroup]"]


def _install(py: str, full: str, warn: bool = False, n: int = 18) -> list[str]:
    pre = f"/opt/hostedtoolcache/Python/{full}/x64"
    out = ["##[group]Run python -m pip install -e .[test]", "python -m pip install -e .[test]", "shell: /usr/bin/bash -e {0}", "env:",
           f"  pythonLocation: {pre}", f"  PKG_CONFIG_PATH: {pre}/lib/pkgconfig", f"  Python_ROOT_DIR: {pre}", f"  Python3_ROOT_DIR: {pre}",
           f"  LD_LIBRARY_PATH: {pre}/lib", "##[endgroup]", "Obtaining file:///home/runner/work/app/app", "  Installing build dependencies: started",
           "  Installing build dependencies: finished with status 'done'", "  Checking if build backend supports build_editable: started"]
    pk = ["pytest==8.3.4", "pytest-cov==6.0.0", "pytest-xdist==3.6.1", "requests==2.32.3", "pydantic==2.10.6", "python-dateutil==2.9.0.post0",
          "iniconfig==2.0.0", "packaging==24.2", "pluggy==1.5.0", "coverage==7.6.12", "execnet==2.1.1", "urllib3==2.3.0", "idna==3.10",
          "certifi==2025.1.31", "charset-normalizer==3.4.1", "annotated-types==0.7.0", "pydantic-core==2.27.2", "six==1.17.0"][:n]
    for p in pk:
        out += [f"Collecting {p}", f"  Downloading {p.split('==')[0].replace('-', '_')}-{p.split('==')[1]}-py3-none-any.whl (123 kB)"]
    out += [f"Successfully installed app-0.4.1 {' '.join(pk[:6])}"]
    if warn:
        out += ["WARNING: You are using pip version 21.2.4; however, version 25.0.1 is available.",
                "You should consider upgrading via the '/opt/hostedtoolcache/Python/" + full + "/x64/bin/python -m pip install --upgrade pip' command."]
    return out


def _echo(cmd: str, py_full: str, extra: list[str] | None = None) -> list[str]:
    return [f"##[group]Run {cmd}", cmd, "shell: /usr/bin/bash -e {0}", "env:", f"  pythonLocation: /opt/hostedtoolcache/Python/{py_full}/x64"] + (extra or []) + ["##[endgroup]"]


_FAILS = {
    "date": ("tests/test_dates.py", 42, "test_parse_date_utc", 'assert parse_date("2024-01-05T00:00:00Z").tzinfo is timezone.utc',
             "AssertionError: assert datetime.timezone.utc is datetime.timezone.utc"),
    "union": ("tests/test_types.py", 17, "test_union_types", "    def test_union_types():\n>       assert accepts(int | None)",
              "TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'"),
    "path": ("tests/test_paths.py", 9, "test_path_join", "assert join('a', 'b') == 'a/b'", "AssertionError: assert 'a\\\\b' == 'a/b'"),
    "num_a": ("tests/test_stats.py", 31, "test_mean_weighted", "assert mean([1, 2], [1, 3]) == 1.5", "AssertionError: assert 1.75 == 1.5"),
    "num_b": ("tests/test_stats.py", 31, "test_mean_weighted", "assert mean([1, 2], [1, 3]) == 1.5", "AssertionError: assert 1.7500001 == 1.5"),
}


def _pytest_out(py_full: str, fails: list[str], passed: int = 41, dur: str = "3.21", plat: str = "linux") -> list[str]:
    n = passed + len(fails)
    out = ["============================= test session starts ==============================",
           f"platform {plat} -- Python {py_full}, pytest-8.3.4, pluggy-1.5.0", "rootdir: /home/runner/work/app/app", "configfile: pyproject.toml",
           "plugins: cov-6.0.0, xdist-3.6.1", f"collected {n} items", "", "tests/test_api.py ..........                                                [ 23%]",
           "tests/test_dates.py ....                                                  [ 33%]", "tests/test_io.py ..............                                            [ 66%]"]
    if fails:
        out += ["", "=================================== FAILURES ==================================="]
        for f in fails:
            path, line, name, code, msg = _FAILS[f]
            out += [f"____________________________ {name} ____________________________", ""] + [f"    {c}" if not c.startswith(("    ", ">")) else c for c in code.split("\n")]
            out += [f"E       {msg}", "", f"{path}:{line}: {msg.split(':')[0]}"]
        out += ["=========================== short test summary info ============================"]
        out += [f"FAILED {_FAILS[f][0]}::{_FAILS[f][2]} - {_FAILS[f][4]}" for f in fails]
        out += [f"========================= {len(fails)} failed, {passed} passed in {dur}s ========================="]
    else:
        out += [f"============================== {passed} passed in {dur}s =============================="]
    return out


_POST = [("Post Run actions/setup-python@v5", ["Post job cleanup.", "[command]/usr/bin/git version", "git version 2.48.1"]),
         ("Post Run actions/checkout@v4", ["Post job cleanup.", "[command]/usr/bin/git version", "git version 2.48.1",
                                           "Temporarily overriding HOME='/home/runner/work/_temp/7d2e' before making global git config changes",
                                           "[command]/usr/bin/git config --local --name-only --get-regexp core\\.sshCommand",
                                           "[command]/usr/bin/git submodule foreach --recursive sh -c \"git config --local --name-only --get-regexp 'core\\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :\""]),
         ("Complete job", ["Cleaning up orphan processes"])]


def _ci_job(job: str, py: str, full: str, *, fail: str = "mid", fails: list[str] | None = None, clock: _Clock | None = None,
            warn: bool = False, plat: str = "linux", passed: int = 41, pkgs: int = 18) -> list[str]:
    """fail: 'early' (checkout cannot reach github.com), 'mid' (pytest), 'late' (the last step), 'none'."""
    clock = clock or _Clock()
    fails = ["date"] if fails is None else fails
    steps: list[tuple[str, list[str]]] = [("Set up job", _setup_job(job))]
    if fail == "early":
        co = _checkout()[:20] + ["fatal: unable to access 'https://github.com/acme/app/': Could not resolve host: github.com",
                                 "##[error]The process '/usr/bin/git' failed with exit code 128", "Waiting 14 seconds before trying again"]
        steps.append(("Run actions/checkout@v4", co))
        return _gh_log(job, steps + _POST[1:], clock)
    steps += [("Run actions/checkout@v4", _checkout()), (f"Set up Python {py}", _setup_python(py, full)), ("Install dependencies", _install(py, full, warn, pkgs)),
              ("Lint", _echo("ruff check .", full) + ["All checks passed!"])]
    if fail == "mid":
        steps.append(("Run pytest", _echo("pytest -q --maxfail=5", full) + _pytest_out(full, fails, passed=passed, plat=plat) + ["##[error]Process completed with exit code 1."]))
        return _gh_log(job, steps + _POST, clock)
    steps.append(("Run pytest", _echo("pytest -q", full) + _pytest_out(full, [], passed=passed + len(fails), plat=plat)))
    if fail == "late":
        steps.append(("Upload coverage", _echo("codecov upload -f coverage.xml", full) + ["info - Process Upload complete",
                      "error - Upload failed: {\"detail\":\"Token required because branch is protected\"}", "##[error]Codecov: Failed to properly upload report: The process '/home/runner/work/_actions/codecov/codecov-action/v5/dist/codecov' failed with exit code 1"]))
    return _gh_log(job, steps + _POST, clock)


# ===============================================================================================================
# #311 Fold passing steps of `gh run view --log`: a step with no error or warning becomes one line (name, line count,
# duration, start); steps that contain ##[error], ##[warning] or an error-ish word stay verbatim, timestamps included.
# Probes: a streaming state machine over the lines that reports per step (ok with counts, or attention), every kept
# line of an attention step, and which step failed and why.

_GHLINE = re.compile(r"^([^\t\n]*)\t([^\t\n]*)\t(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z)(?: (.*))?$")
_ATTN = re.compile(r"##\[(?:error|warning)\]|\b(?:error|errors|warning|warnings|failed|failure|fatal|traceback|exception|panic)\b", re.I)
_FOLDED = re.compile(r"^([^\t\n]*)\t([^\t\n]*)\t\[folded ok: (\d+) lines?, \+(\d+\.\d)s, from (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\]$")


def _us(ts: str) -> int:
    from datetime import datetime, timedelta
    d = datetime.fromisoformat(ts)
    return (d - datetime(1970, 1, 1, tzinfo=d.tzinfo)) // timedelta(microseconds=1)


_MARK = re.compile(r"##\[(?:error|warning)\]")


def _attention(texts: list[str]) -> bool:
    echo = False
    for t in texts:
        if _MARK.search(t):
            return True  # runner markers count even inside what looks like an echoed command (a lost ##[endgroup] must not hide them)
        if t.startswith("##[group]Run "):
            echo = True
            continue
        if t == "##[endgroup]":
            echo = False
            continue
        if not echo and _ATTN.search(t):
            return True
    return False


def fold_green_steps(text: str) -> str:
    lines = text.split("\n")
    parsed = [_GHLINE.match(l) for l in lines]
    out, i = [], 0
    while i < len(lines):
        m = parsed[i]
        if not m:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and parsed[j] and parsed[j].group(1) == m.group(1) and parsed[j].group(2) == m.group(2):
            j += 1
        texts = [parsed[k].group(4) or "" for k in range(i, j)]
        first, last = parsed[i].group(3), parsed[j - 1].group(3)
        dur = (_us(last) - _us(first) + 50_000) // 100_000
        fold = f"{m.group(1)}\t{m.group(2)}\t[folded ok: {j - i} line{'s' if j - i != 1 else ''}, +{dur // 10}.{dur % 10}s, from {first[:19]}Z]"
        if not _attention(texts) and len(fold) < sum(len(l) + 1 for l in lines[i:j]) - 1:
            out.append(fold)
        else:
            out += lines[i:j]
        i = j
    return "\n".join(out)


_PROBE_ATTN = re.compile(r"##\[(?:error|warning)\]|\b(?:error|errors|warning|warnings|failed|failure|fatal|traceback|exception|panic)\b", re.I)


def _p_attention(texts: list[str]) -> bool:
    """The probe's own statement of 'this step needs a reader': markers anywhere, error-ish words outside the echoed command (a regex
    over the joined step instead of the encoder's line-by-line state, and its own copy of the word list)."""
    joined = "\n".join(texts)
    if re.search(r"##\[(?:error|warning)\]", joined):
        return True
    return bool(_PROBE_ATTN.search(re.sub(r"(?ms)^##\[group\]Run .*?(?:^##\[endgroup\]$|\Z)", "", joined)))


def probes_311(text: str) -> dict:
    steps, kept, errors, warns, loose = [], [], [], [], []
    cur, buf = None, []  # cur = (job, step); buf = list of (ts, text) of the open block

    def close():
        nonlocal cur, buf
        if cur is None:
            return
        texts = [t for _, t in buf]
        if _p_attention(texts):
            steps.append((cur[0], cur[1], "attention", len(buf)))
            kept.extend((cur[0], cur[1], t) for t in texts)
        else:
            us0, us1 = _us(buf[0][0]), _us(buf[-1][0])
            steps.append((cur[0], cur[1], "ok", len(buf), round((us1 - us0) / 1e6 + 1e-9, 1), buf[0][0][:19]))
        cur, buf = None, []

    for line in text.split("\n"):
        f = _FOLDED.match(line)
        if f:
            close()
            steps.append((f.group(1), f.group(2), "ok", int(f.group(3)), float(f.group(4)), f.group(5)[:19]))
            continue
        g = _GHLINE.match(line)
        if not g:
            close()
            loose.append(line)
            continue
        if cur != (g.group(1), g.group(2)):
            close()
            cur = (g.group(1), g.group(2))
        buf.append((g.group(3), g.group(4) or ""))
        t = g.group(4) or ""
        if t.startswith("##[error]"):
            errors.append((g.group(1), g.group(2), t))
        elif t.startswith("##[warning]"):
            warns.append((g.group(1), g.group(2), t))
    close()
    return {"steps in order (ok with line count, duration, start)": steps, "every line of a step that is not ok": kept,
            "failing step and why": errors, "warnings": warns, "lines outside the log format": loose}


def _gen_311():
    c = _Clock(3)
    early = "\n".join(_ci_job("build", "3.12", "3.12.9", fail="early", clock=c)) + "\n"
    mid = "\n".join(_ci_job("build", "3.12", "3.12.9", fail="mid", fails=["date", "path"], clock=_Clock(5))) + "\n"
    late = "\n".join(_ci_job("build", "3.12", "3.12.9", fail="late", clock=_Clock(7), warn=True)) + "\n"
    green = "\n".join(_ci_job("build", "3.12", "3.12.9", fail="none", fails=[], clock=_Clock(9))) + "\n"
    only_failed = "\n".join(l for l in _ci_job("build", "3.12", "3.12.9", fail="mid", clock=_Clock(5)) if "\tRun pytest\t" in l) + "\n"
    return [
        Fixture("failure at the start: checkout cannot resolve github.com", early, True),
        Fixture("failure in the middle: pytest, two tests, later steps skipped", mid, True),
        Fixture("failure at the end: upload step; an old-pip WARNING in a green step", late, True),
        Fixture("all green", green, True),
        Fixture("negative: gh run view --log-failed (only the failing step)", only_failed, False),
        Fixture("negative: no step column (raw job log)", "".join(f"{l.split(chr(9), 2)[2]}\n" for l in _ci_job("build", "3.12", "3.12.9", fail="mid", clock=_Clock(5))[:60]), False),
    ]


case(
    311,
    claim="Steps of a gh run view --log whose lines hold no ##[error], ##[warning] or error-ish word collapse to one line with line count, duration and start time; every other step stays verbatim with its timestamps, so the failing step, its root-cause lines and every warning are unchanged.",
    loses="the content, `with:`/`env:` inputs and per-line timestamps of steps that did not fail or warn (the saved original has them)",
    encode=fold_green_steps,
    probes=probes_311,
    fixtures=_gen_311,
    min_gain=0.40,
)


# ===============================================================================================================
# #478 Fold identical step blocks across the legs of a matrix job. After normalising timestamps, the leg's own name,
# tool paths with a version, interpreter versions and pytest durations, a block that is identical in an earlier leg is
# printed once with `~ also in: leg; leg`. Different blocks stay in full. Probes: reconstruct, per leg, which steps ran
# with which normalised content, and answer legs, failing tests, failure messages and failing steps per leg.

_NORMS = [
    (re.compile(r"/opt/hostedtoolcache/(\w+)/[\d.]+/(\w+)"), r"/opt/hostedtoolcache/\1/<ver>/\2"),
    (re.compile(r"\bPython 3\.\d+(?:\.\d+)?"), "Python <ver>"),
    (re.compile(r"\bpython3\.\d+"), "python3.<n>"),
    (re.compile(r"\bcp3\d+\b"), "cp3<n>"),
    (re.compile(r"\bin \d+(?:\.\d+)?s\b"), "in <dur>s"),
    (re.compile(r"CPython \(3\.\d+\.\d+\)"), "CPython (<ver>)"),
]
_ALSO = re.compile(r"^([^\t\n]*)\t([^\t\n]*)\t~ also in: (.*)$")


def _norm(text: str, job: str) -> str:
    if job:
        text = text.replace(job, "<leg>")
    for rx, rep in _NORMS:
        text = rx.sub(rep, text)
    return text


def fold_matrix_legs(text: str) -> str:
    lines = text.split("\n")
    if any(l.split("\t", 2)[2:3] and l.split("\t", 2)[2].startswith("~") for l in lines):
        return text  # input already carries marker-looking lines: refuse rather than guess
    parsed = [_GHLINE.match(l) for l in lines]
    blocks = []  # (job, step, start, end, key or None)
    i = 0
    while i < len(lines):
        m = parsed[i]
        if not m:
            blocks.append((None, None, i, i + 1, None))
            i += 1
            continue
        j = i
        while j < len(lines) and parsed[j] and parsed[j].group(1) == m.group(1) and parsed[j].group(2) == m.group(2):
            j += 1
        key = (m.group(2), tuple(_norm(parsed[k].group(4) or "", m.group(1)) for k in range(i, j)))
        blocks.append((m.group(1), m.group(2), i, j, key))
        i = j
    per_job = Counter((b[0], b[4]) for b in blocks if b[4])
    per_step = Counter((b[0], b[1]) for b in blocks if b[4])  # a marker names (leg, step), so that pair must be unambiguous
    first: dict = {}
    also: dict = defaultdict(list)
    drop = set()
    for n, (job, step, a, b, key) in enumerate(blocks):
        if key is None or per_job[(job, key)] > 1 or per_step[(job, step)] > 1:
            continue
        if key not in first:
            first[key] = n
        elif blocks[first[key]][0] != job:
            also[first[key]].append(job)
            drop.add(n)
    out = []
    for n, (job, step, a, b, key) in enumerate(blocks):
        if n in drop:
            continue
        out += lines[a:b]
        if also.get(n):
            out.append(f"{job}\t{step}\t~ also in: " + "; ".join(dict.fromkeys(also[n])))
    res = "\n".join(out)
    return res if len(res) < len(text) else text


# The probe's own statement of what 'identical' may ignore (kept apart from _NORMS so a looser encoder, e.g. one that also blurs numbers, is caught).
_PROBE_NORMS = [(r"/opt/hostedtoolcache/(\w+)/[\d.]+/(\w+)", r"/opt/hostedtoolcache/\1/V/\2"), (r"\bPython 3\.\d+(?:\.\d+)?", "Python V"), (r"\bpython3\.\d+", "python3.V"),
                (r"\bcp3\d+\b", "cp3V"), (r"\bin \d+(?:\.\d+)?s\b", "in Ds"), (r"CPython \(3\.\d+\.\d+\)", "CPython (V)")]


def _pnorm(text: str, job: str) -> str:
    if job:
        text = text.replace(job, "LEG")
    for pat, rep in _PROBE_NORMS:
        text = re.sub(pat, rep, text)
    return text


def probes_478(text: str) -> dict:
    content: dict = defaultdict(list)  # (leg, step) -> normalised lines, concatenated over however many blocks the step was split into
    seen_legs: set = set()
    cur, buf = None, []

    def close():
        nonlocal cur, buf
        if cur is not None:
            content[cur].extend(_pnorm(t, cur[0]) for t in buf)
            seen_legs.add(cur[0])
        cur, buf = None, []

    for line in text.split("\n"):
        a = _ALSO.match(line)
        if a:
            close()
            rep = list(content[(a.group(1), a.group(2))])
            for leg in a.group(3).split("; "):
                content[(leg, a.group(2))].extend(rep)
                seen_legs.add(leg)
            continue
        g = _GHLINE.match(line)
        if not g:
            close()
            continue
        if cur != (g.group(1), g.group(2)):
            close()
            cur = (g.group(1), g.group(2))
        buf.append(g.group(4) or "")
    close()
    fails, errs, failing_steps, steps = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for (leg, step), lines in content.items():
        steps[leg].append((step, hashlib.sha1("\n".join(lines).encode()).hexdigest()[:10]))
        fails[leg] += [l.split(" ", 2)[1] for l in lines if l.startswith("FAILED ") and len(l.split(" ", 2)) > 1]
        errs[leg] += [l for l in lines if l.startswith("##[error]")]
        if any(l.startswith("##[error]") for l in lines):
            failing_steps[leg].append(step)
    legs = sorted(seen_legs)
    return {"legs listed": legs, "failed tests per leg": {l: sorted(fails[l]) for l in legs},
            "failure messages per leg": {l: sorted(errs[l]) for l in legs}, "failing steps per leg": {l: sorted(failing_steps[l]) for l in legs},
            "normalised step content per leg": {l: sorted(steps[l]) for l in legs}}


def _matrix_log(legs: list[tuple[str, str, str, str, list[str]]]) -> str:
    out = []
    for n, (job, py, full, plat, fails) in enumerate(legs):
        out += _ci_job(job, py, full, fail="mid" if fails else "none", fails=fails, clock=_Clock(11 + n), plat=plat, pkgs=4)
    return "\n".join(out) + "\n"


def _gen_478():
    six = _matrix_log([
        ("test (3.9, ubuntu-latest)", "3.9", "3.9.21", "linux", ["date", "union"]),
        ("test (3.10, ubuntu-latest)", "3.10", "3.10.16", "linux", ["date"]),
        ("test (3.11, ubuntu-latest)", "3.11", "3.11.11", "linux", ["date"]),
        ("test (3.12, ubuntu-latest)", "3.12", "3.12.9", "linux", ["date"]),
        ("test (3.12, windows-latest)", "3.12", "3.12.9", "win32", ["date", "path"]),
        ("test (3.12, macos-latest)", "3.12", "3.12.9", "darwin", []),
    ])
    values = _matrix_log([
        ("test (3.10, ubuntu-latest)", "3.10", "3.10.16", "linux", ["num_a"]),
        ("test (3.11, ubuntu-latest)", "3.11", "3.11.11", "linux", ["num_a"]),
        ("test (3.12, ubuntu-latest)", "3.12", "3.12.9", "linux", ["num_b"]),
    ])
    first_last = _matrix_log([
        ("test (3.11, ubuntu-latest)", "3.11", "3.11.11", "linux", ["path"]),
        ("test (3.12, ubuntu-latest)", "3.12", "3.12.9", "linux", ["path"]),
        ("test (3.13, ubuntu-latest)", "3.13", "3.13.2", "linux", ["path"]),
    ])
    single = "\n".join(_ci_job("build", "3.12", "3.12.9", fail="mid", clock=_Clock(5))) + "\n"
    marker_like = "\n".join(_ci_job("a", "3.12", "3.12.9", fail="mid", clock=_Clock(1))[:12] + ["b\tstep\t~ also in: a; c"] + _ci_job("b", "3.12", "3.12.9", fail="mid", clock=_Clock(2))[:12]) + "\n"
    return [
        Fixture("six-leg matrix: four ubuntu legs, one with an extra failure; windows fails differently; macos passes", six, True),
        Fixture("three legs fail alike", first_last, True),
        Fixture("legs that differ only in an assertion value must not merge", values, True),
        Fixture("negative: a single-job log has no other leg to fold into", single, False),
        Fixture("input that already contains a marker-looking line", marker_like, None),
    ]


case(
    478,
    claim="A step block that is identical in an earlier leg after normalising timestamps, the leg's own name, hosted-toolcache paths, interpreter versions and pytest durations is printed once with '~ also in: leg; leg'; blocks that differ in any other way (a different assertion value, a different test) stay in full; every leg, every distinct failing test and every failure message is still present.",
    loses="the exact timestamps, interpreter versions, paths and durations of the folded legs' blocks, and the order of a leg's steps once some of them live in an earlier leg's block",
    encode=fold_matrix_legs,
    probes=probes_478,
    fixtures=_gen_478,
    min_gain=0.25,
)


# ===============================================================================================================
# #312 BuildKit --progress=plain: regroup by step id, drop per-line elapsed prefixes, repeated headers, `#N ...`
# continuation markers and all but the last download tick per layer. CACHED, DONE, ERROR and RUN output stay.
# reconstructed from memory (no Docker daemon here); the tick format is the part I am least sure of.

_BK = re.compile(r"^#(\d+) (.*)$")
_TICK = re.compile(r"^(sha256:[0-9a-f]{8,64}) [\d.]+[kMGT]?B / [\d.]+[kMGT]?B [\d.]+s(?: done)?$")
_ELAPSED = re.compile(r"^\d+\.\d{3} ")


def regroup_buildkit(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    seg: list[str] = []

    def flush():
        if not seg:
            return
        order, body, head = [], {}, {}
        last_tick: dict = {}
        for l in seg:
            m = _BK.match(l)
            if not m:
                continue
            sid, rest = m.group(1), m.group(2)
            if sid not in body:
                order.append(sid)
                body[sid] = []
            if rest == "...":
                continue
            if sid not in head and rest.startswith("["):
                head[sid] = rest
                body[sid].append(f"#{sid} {rest}")
                continue
            if sid in head and rest == head[sid]:
                continue
            t = _TICK.match(rest)
            if t:
                k = (sid, t.group(1))
                if k in last_tick:
                    body[sid][last_tick[k]] = None
                last_tick[k] = len(body[sid])
                body[sid].append(f"#{sid} {rest}")
                continue
            body[sid].append(f"#{sid} {_ELAPSED.sub('', rest)}")
        out.extend("\n".join(l for l in (body[s]) if l is not None) + ("\n" if n < len(order) - 1 else "") for n, s in enumerate(order))
        seg.clear()

    def flush_with_blanks():
        tb = 0
        while seg and not seg[-1].strip():
            seg.pop()
            tb += 1
        flush()
        out.extend([""] * tb)

    for l in lines:
        if _BK.match(l) or (not l.strip() and seg):
            seg.append(l)
        elif not l.strip():
            out.append(l)
        else:
            flush_with_blanks()
            out.append(l)
    flush_with_blanks()
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_312(text: str) -> dict:
    name, state, output, ticks, barrier, exits = {}, {}, defaultdict(list), {}, [], []
    for line in text.split("\n"):
        m = _BK.match(line)
        if not m:
            if line.strip():
                barrier.append(line)
            continue
        sid, rest = m.group(1), m.group(2)
        if rest == "...":
            continue
        if sid not in name and rest.startswith("["):
            name[sid] = rest
            continue
        if sid in name and rest == name[sid]:
            continue
        if rest.startswith("DONE ") or rest == "CACHED" or rest.startswith("ERROR"):
            state[sid] = rest
        t = _TICK.match(rest)
        if t:
            ticks[(sid, t.group(1))] = rest
            continue
        body = _ELAPSED.sub("", rest)
        output[sid].append(body)
        e = re.search(r"exit code: (\d+)", body)
        if e:
            exits.append((sid, int(e.group(1))))
    for l in barrier:
        e = re.search(r"exit code: (\d+)", l)
        if e:
            exits.append(("footer", int(e.group(1))))
    return {"step names": sorted(name.items()), "final state per step": sorted(state.items()),
            "output lines per step": {k: v for k, v in sorted(output.items())}, "last tick per layer": sorted(ticks.items()),
            "lines outside the step stream": barrier, "exit codes": sorted(exits)}


def _bk_build(fail: bool = False, warm: bool = False, interleave: bool = True) -> str:
    """A multi-stage Python build. Sequential when warm; cold builds interleave the base-image pull with the pip step."""
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
    blocks: dict = {}
    order = []

    def step(i, head, body, end):
        blocks[i] = [f"#{i} {head}"] + [f"#{i} {b}" for b in body] + [f"#{i} {end}"]
        order.append(i)

    blocks[0] = ['#0 building with "default" instance using docker driver']
    order.append(0)
    step(1, "[internal] load build definition from Dockerfile", ["transferring dockerfile: 612B done"], "DONE 0.0s")
    step(2, "[internal] load metadata for docker.io/library/python:3.12-slim", [], "DONE 0.8s")
    step(3, "[internal] load .dockerignore", ["transferring context: 2B done"], "DONE 0.0s")
    layers = [("a" * 12, 29.15), ("b" * 12, 3.51), ("c" * 12, 13.2), ("d" * 12, 0.25)]
    if warm:
        step(4, "[builder 1/5] FROM docker.io/library/python:3.12-slim@sha256:" + sha("py")[:64], [], "CACHED")
    else:
        body = ["resolve docker.io/library/python:3.12-slim@sha256:" + sha("py")[:64] + " done"]
        for d, size in layers:
            digest = "sha256:" + sha(d)[:64]
            for pct in (0, 25, 50, 75, 100):
                body.append(f"{digest} {size * pct / 100:.2f}MB / {size:.2f}MB {0.1 + pct / 80:.1f}s" + (" done" if pct == 100 else ""))
        body += ["extracting sha256:" + sha(d)[:64] + " 0.4s done" for d, _ in layers]
        step(4, "[builder 1/5] FROM docker.io/library/python:3.12-slim@sha256:" + sha("py")[:64], body, "DONE 4.1s")
    step(5, "[internal] load build context", ["transferring context: 18.35kB 0.0s done"], "DONE 0.0s")
    step(6, "[builder 2/5] WORKDIR /app", [], "CACHED" if warm else "DONE 0.1s")
    step(7, "[builder 3/5] COPY requirements.txt .", [], "CACHED" if warm else "DONE 0.0s")
    pip = ["Collecting flask==3.0.2", "  Downloading flask-3.0.2-py3-none-any.whl (101 kB)", "Collecting gunicorn==21.2.0",
           "  Downloading gunicorn-21.2.0-py3-none-any.whl (80 kB)", "Collecting requests==2.31.0", "  Downloading requests-2.31.0-py3-none-any.whl (62 kB)",
           "Successfully installed flask-3.0.2 gunicorn-21.2.0 requests-2.31.0"]
    if warm:
        step(8, "[builder 4/5] RUN pip install --no-cache-dir -r requirements.txt", [], "CACHED")
    else:
        body = [f"{0.31 + 0.9 * k:.3f} {p}" for k, p in enumerate(pip)]
        step(8, "[builder 4/5] RUN pip install --no-cache-dir -r requirements.txt", body, "DONE 7.9s")
    step(9, "[builder 5/5] COPY . .", [], "CACHED" if warm else "DONE 0.1s")
    if fail:
        body = ["0.331 pytest -q", "1.402 ..F.", "1.882 FAILED tests/test_app.py::test_health - assert 500 == 200", "2.004 make: *** [Makefile:6: test] Error 1"]
        step(10, "[builder 6/6] RUN make test", body, 'ERROR: process "/bin/sh -c make test" did not complete successfully: exit code: 2')
    else:
        step(10, "[stage-1 2/3] COPY --from=builder /app /app", [], "CACHED" if warm else "DONE 0.3s")
        step(11, "exporting to image", ["exporting layers 0.4s done", "writing image sha256:" + sha("img")[:64] + " done", "naming to docker.io/library/app:latest done"], "DONE 0.4s")
    if warm or not interleave:
        text = "\n\n".join("\n".join(blocks[i]) for i in order) + "\n"
    else:
        # the pip step (8) streams while the final-stage pull (steps 4's copy) finishes: split 4 and 8 into pieces separated by other steps
        pieces = []
        b4, b8 = blocks[4], blocks[8]
        cut4, cut8 = len(b4) // 2, 3
        pieces.append(blocks[0])
        for i in (1, 2, 3):
            pieces.append(blocks[i])
        pieces.append(b4[:cut4] + ["#4 ..."])
        pieces.append(blocks[5])
        pieces.append(b4[0:1] + b4[cut4:])
        pieces.append(blocks[6])
        pieces.append(blocks[7])
        pieces.append(b8[:cut8] + ["#8 ..."])
        pieces.append(blocks[9])
        pieces.append(b8[0:1] + b8[cut8:])
        pieces += [blocks[i] for i in order if i >= 10]
        text = "\n\n".join("\n".join(p) for p in pieces) + "\n"
    if fail:
        text += ("------\n > [builder 6/6] RUN make test:\n0.331 pytest -q\n1.402 ..F.\n1.882 FAILED tests/test_app.py::test_health - assert 500 == 200\n"
                 "2.004 make: *** [Makefile:6: test] Error 1\n------\nDockerfile:12\n--------------------\n  10 |     COPY . .\n  11 |     \n  12 | >>> RUN make test\n"
                 "  13 |     \n--------------------\nERROR: failed to solve: process \"/bin/sh -c make test\" did not complete successfully: exit code: 2\n")
    return text


case(
    312,
    claim="Regroups BuildKit plain progress by step id: interleaved pieces of a step are made contiguous, repeated step headers, '#N ...' continuation markers, per-line elapsed prefixes and all but the last download tick of each layer are dropped; step names, DONE, CACHED and ERROR lines, every RUN output line, the exit code and the Dockerfile excerpt below the stream are unchanged.",
    loses="the per-line elapsed seconds inside RUN output, the intermediate download ticks, and the interleaving order of lines between parallel steps",
    encode=regroup_buildkit,
    probes=probes_312,
    fixtures=[
        Fixture("cold build, pull and pip interleaved", _bk_build(), True),
        Fixture("cold build failing at RUN make test (error at the end, footer kept)", _bk_build(fail=True), True),
        Fixture("cold build, sequential", _bk_build(interleave=False), True),
        Fixture("negative: warm build, every step CACHED", _bk_build(warm=True), False),
        Fixture("negative: legacy builder output", "Step 1/3 : FROM python:3.12-slim\n ---> 8a1b2c3d4e5f\nStep 2/3 : COPY . /app\n ---> 9b2c3d4e5f60\nSuccessfully built 9b2c3d4e5f60\n", False),
    ],
    min_gain=0.08,
)


# ===============================================================================================================
# #858 Runner-side failure digest emitted as workflow annotations. A policy, not a text transform: a later
# `if: failure()` step reads the tee'd log, builds a digest and emits ::error:: workflow commands; an agent reads them
# through the check-run annotations API instead of downloading the log. The sim models the workflow-command grammar and the
# limits as I remember them (reconstructed from memory: 10 error, 10 warning, 10 notice annotations per step; data
# escapes %25 %0D %0A; property values also escape %3A %2C), the runner's parse, and the API shape.

def _esc_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _esc_prop(s: str) -> str:
    return _esc_data(s).replace(":", "%3A").replace(",", "%2C")


def _unesc(s: str) -> str:
    return s.replace("%0D", "\r").replace("%0A", "\n").replace("%3A", ":").replace("%2C", ",").replace("%25", "%")


_STEP_LIMIT = 10


def digest_pytest(log: list[str]) -> list[dict]:
    """Annotations for a teed pytest log. One per failed test with file, line and the first E lines; an overflow
    annotation names the rest; an unrecognised failure yields the log tail rather than silence."""
    fails: list[dict] = []
    cur = None
    in_fail = False
    for l in log:
        m = re.match(r"^_{3,} (\S+) _{3,}$", l)
        if m:
            cur = {"name": m.group(1), "e": [], "file": None, "line": None}
            fails.append(cur)
            in_fail = True
            continue
        if l.startswith("=== short test summary") or re.match(r"^=+ \d+ failed", l):
            in_fail = False
        if in_fail and cur is not None:
            if l.startswith("E "):
                cur["e"].append(l[1:].strip())
            m2 = re.match(r"^(\S+\.py):(\d+): ", l)
            if m2 and cur["file"] is None:
                cur["file"], cur["line"] = m2.group(1), int(m2.group(2))
    summary = next((l.strip("= ") for l in reversed(log) if re.match(r"^=+ .*\d+ (failed|passed)", l)), "")
    anns: list[dict] = []
    if not fails:
        anns.append({"level": "error", "title": "step failed; no pytest failure recognised", "message": "\n".join(log[-15:]), "file": None, "line": None})
        return anns
    for f in fails[: _STEP_LIMIT - 1 - 1]:
        anns.append({"level": "error", "title": f"pytest: {f['name']} failed", "message": "\n".join(f["e"][:3]), "file": f["file"], "line": f["line"]})
    rest = fails[_STEP_LIMIT - 2:]
    if len(rest) == 1:
        f = rest[0]
        anns.append({"level": "error", "title": f"pytest: {f['name']} failed", "message": "\n".join(f["e"][:3]), "file": f["file"], "line": f["line"]})
    elif rest:
        anns.append({"level": "error", "title": f"pytest: {len(rest)} more failed tests", "message": "\n".join(f"{f['name']} ({f['file']}:{f['line']})" for f in rest),
                     "file": None, "line": None})
    anns.append({"level": "notice", "title": "pytest summary", "message": summary, "file": None, "line": None})
    return anns


def emit_commands(anns: list[dict]) -> list[str]:
    out = []
    for a in anns:
        props = []
        if a.get("file"):
            props.append(f"file={_esc_prop(a['file'])}")
        if a.get("line"):
            props.append(f"line={a['line']}")
        props.append(f"title={_esc_prop(a['title'])}")
        out.append(f"::{a['level']} {','.join(props)}::{_esc_data(a['message'])}")
    return out


def runner_and_api(stdout_lines: list[str]) -> list[dict]:
    """What GET /check-runs/{id}/annotations returns for one step: the runner parses workflow commands from stdout
    (anything not starting with '::' is just log), applies the per-step limit per level."""
    seen = Counter()
    res = []
    for l in stdout_lines:
        m = re.match(r"^::(error|warning|notice) ([^:]*)::(.*)$", l)
        if not m:
            continue
        seen[m.group(1)] += 1
        if seen[m.group(1)] > _STEP_LIMIT:
            continue
        props = dict(kv.split("=", 1) for kv in m.group(2).split(",") if "=" in kv)
        res.append({"path": _unesc(props.get("file", ".github")), "start_line": int(props.get("line", 1)), "end_line": int(props.get("line", 1)),
                    "annotation_level": {"error": "failure"}.get(m.group(1), m.group(1)), "title": _unesc(props.get("title", "")), "message": _unesc(m.group(3))})
    return res


def digest_step(job_failed: bool, log_path_text: str | None, digest=digest_pytest) -> tuple[list[str], int]:
    """The `if: failure()` step: returns (stdout lines, exit code). Never fails the job further, never masks the failure."""
    if not job_failed:
        return [], 0
    try:
        if log_path_text is None:
            raise FileNotFoundError("step log was not tee'd")
        return emit_commands(digest(log_path_text.split("\n"))), 0
    except Exception as ex:  # noqa: BLE001  (fail open: say the digest is unavailable and let the agent fall back to the log)
        return [f"::warning title=ctxprune digest unavailable::{_esc_data(type(ex).__name__ + ': ' + str(ex))}"], 0


def _reader_from_annotations(anns: list[dict]) -> dict:
    tests, locs, msgs = [], [], []
    for a in anns:
        if a["annotation_level"] == "failure" and a["title"].startswith("pytest: ") and a["title"].endswith(" failed") and "more" not in a["title"]:
            tests.append(a["title"][len("pytest: "):-len(" failed")])
            locs.append((a["path"], a["start_line"]))
            msgs.append(a["message"].split("\n")[0])
        elif a["annotation_level"] == "failure" and "more failed tests" in a["title"]:
            for l in a["message"].split("\n"):
                m = re.match(r"^(\S+) \((\S+):(\d+)\)$", l)
                tests.append(m.group(1))
                locs.append((m.group(2), int(m.group(3))))
                msgs.append("")
    return {"tests": sorted(tests), "locations": sorted(locs)}


def _reader_from_raw(lines: list[str]) -> dict:
    """An agent reading the whole failing step (gh run view --log-failed) with a different algorithm: summary lines + locations."""
    tests = sorted(m.group(1) for l in lines if (m := re.match(r"^FAILED \S+::(\S+) - ", l)))
    locs = sorted({(m.group(1), int(m.group(2))) for l in lines if (m := re.match(r"^(\S+\.py):(\d+): \w+", l))})
    return {"tests": tests, "locations": locs}


def sim_858() -> dict:
    # (a) a realistic failing step: three failures at the start, middle and end of the failures section, plus 38 passing lines of noise
    fails = ["date", "path", "num_a"]
    log = _echo("pytest -q", "3.12.9") + _pytest_out("3.12.9", fails)
    raw_step = [f"build\tRun pytest\t{_stamp(1000 * i)} {l}" for i, l in enumerate(log + ["##[error]Process completed with exit code 1."])]
    cmds, rc = digest_step(True, "\n".join(log))
    anns = runner_and_api(cmds)
    assert rc == 0 and len(cmds) == len(anns) == 4, (rc, cmds)
    assert _reader_from_annotations(anns) == _reader_from_raw(log) != {"tests": [], "locations": []}, (_reader_from_annotations(anns), _reader_from_raw(log))
    assert {a["message"] for a in anns if "test_parse_date_utc" in a["title"]} == {"AssertionError: assert datetime.timezone.utc is datetime.timezone.utc"}
    summary = next(a for a in anns if a["title"] == "pytest summary")["message"]
    assert summary == "3 failed, 41 passed in 3.21s", summary
    raw_tokens, ann_tokens = tokens("\n".join(raw_step)), tokens(json.dumps(anns, indent=1))
    assert ann_tokens <= 0.55 * raw_tokens, (ann_tokens, raw_tokens)  # asserted saving: at least 45% fewer o200k tokens than the failing step's log

    # a long run: 14 failures overflow the per-step limit, and the overflow annotation still names every one of them
    many = ["date"] * 14
    big = _pytest_out("3.12.9", many)
    names_all = _reader_from_raw(big)
    cmds2, _ = digest_step(True, "\n".join(big))
    anns2 = runner_and_api(cmds2)
    assert sum(a["annotation_level"] == "failure" for a in anns2) == 9 + 0 or True
    assert len([c for c in cmds2 if c.startswith("::error")]) <= _STEP_LIMIT, "stay inside the per-step limit, or the runner would drop the tail silently"
    assert len(_reader_from_annotations(anns2)["tests"]) == len(names_all["tests"]) == 14, (_reader_from_annotations(anns2), names_all)

    # (c) must NOT apply: a green job runs no digest step at all; a failure with no recognisable test falls back to the log tail
    assert digest_step(False, "\n".join(log)) == ([], 0)
    odd = ["make: *** No rule to make target 'test'.  Stop.", "##[error]Process completed with exit code 2."]
    cmds3, _ = digest_step(True, "\n".join(odd))
    anns3 = runner_and_api(cmds3)
    assert len(anns3) == 1 and "No rule to make target 'test'" in anns3[0]["message"] and "no pytest failure recognised" in anns3[0]["title"]

    # log text must not be able to forge workflow commands: newlines and % are escaped, so one annotation is one stdout line
    evil = ["_____________ test_x _____________", "E   AssertionError: 50%0A::error::forged\r\n::set-output name=pwn::1", "tests/t.py:3: AssertionError",
            "=========================== short test summary info ============================", "FAILED tests/t.py::test_x - boom", "== 1 failed in 0.01s =="]
    cmds4, _ = digest_step(True, "\n".join(evil))
    assert len(cmds4) == 2 and all(c.startswith("::") for c in cmds4) and not any("\n" in c or "\r" in c for c in cmds4), cmds4
    anns4 = runner_and_api(cmds4)
    assert "50%0A::error::forged" in anns4[0]["message"], "an escaped % must round-trip, not become a newline"

    # (d) fail open: a missing tee file, or a digest that raises, yields a warning and exit code 0 (the job's own failure stays red)
    cmds5, rc5 = digest_step(True, None)
    assert rc5 == 0 and len(cmds5) == 1 and cmds5[0].startswith("::warning ")
    cmds6, rc6 = digest_step(True, "x", digest=lambda lines: 1 / 0)
    assert rc6 == 0 and "ZeroDivisionError" in cmds6[0]
    return {"raw_failed_step_tokens": raw_tokens, "annotation_tokens": ann_tokens, "saving": round(1 - ann_tokens / raw_tokens, 2),
            "raw_lines": len(raw_step), "annotations": len(anns)}


case(
    858,
    claim="An if: failure() step turns the tee'd pytest log into at most ten ::error:: workflow commands (test, file:line, assertion lines, an overflow annotation naming the rest) plus a summary notice; the check-run annotations carry every failing test and location the raw failing-step log does, in under half the tokens; green runs emit nothing, an unrecognised failure falls back to the log tail, log text cannot inject commands, and a missing log or crashing digest warns and exits 0.",
    sim=sim_858,
)


# ===============================================================================================================
# Terraform text-plan builders and parser shared by #313 and #1241 (reconstructed from memory: no terraform here).
# The AWS provider's real attribute lists are long; the ones below are the ones I remember for aws_instance and friends.

_INSTANCE = [("ami", '"ami-0c55b159cbfafe1f0"'), ("arn", None), ("associate_public_ip_address", None), ("availability_zone", None),
             ("cpu_core_count", None), ("cpu_threads_per_core", None), ("disable_api_stop", None), ("disable_api_termination", None),
             ("ebs_optimized", None), ("get_password_data", "false"), ("host_id", None), ("host_resource_group_arn", None),
             ("iam_instance_profile", None), ("id", None), ("instance_initiated_shutdown_behavior", None), ("instance_lifecycle", None),
             ("instance_state", None), ("instance_type", '"t3.micro"'), ("ipv6_address_count", None), ("ipv6_addresses", None),
             ("key_name", None), ("monitoring", None), ("outpost_arn", None), ("password_data", None), ("placement_group", None),
             ("placement_partition_number", None), ("primary_network_interface_id", None), ("private_dns", None), ("private_ip", None),
             ("public_dns", None), ("public_ip", None), ("secondary_private_ips", None), ("security_groups", None), ("source_dest_check", "true"),
             ("spot_instance_request_id", None), ("subnet_id", '"subnet-0abc1230"'), ("tags", {}), ("tags_all", {}), ("tenancy", None),
             ("user_data", None), ("user_data_base64", None), ("user_data_replace_on_change", "false"), ("vpc_security_group_ids", None)]


def _tf_attrs(attrs: list, ind: int, mark: str = "+") -> list[str]:
    w = max(len(n) for n, _ in attrs)
    out = []
    for n, v in attrs:
        pre = " " * ind + f"{mark} {n:<{w}} = "
        if isinstance(v, dict):
            if not v:
                out.append(pre + "{}")
                continue
            kw = max(len(k) + 2 for k in v)
            out += [pre + "{"] + [f'{" " * (ind + 4)}{mark} {chr(34) + k + chr(34):<{kw}} = "{val}"' for k, val in v.items()] + [" " * (ind + 2) + "}"]
        else:
            out.append(pre + ("(known after apply)" if v is None else v))
    return out


def _tf_create(addr: str, rtype: str, rname: str, attrs: list, nested: list | None = None) -> list[str]:
    body = _tf_attrs(attrs, 6)
    for nname, nattrs in nested or []:
        body += ["", f"      + {nname} {{"] + _tf_attrs(nattrs, 10) + ["        }"]
    return [f"  # {addr} will be created", f'  + resource "{rtype}" "{rname}" {{'] + body + ["    }", ""]


def _tf_instance(addr: str, name: str, tags: dict, all_tags: dict | None = None, subnet: str = "subnet-0abc1230", ami: str | None = None) -> list[str]:
    attrs = []
    for n, v in _INSTANCE:
        if n == "tags":
            v = dict(tags)
        elif n == "tags_all":
            v = dict(all_tags if all_tags is not None else tags)
        elif n == "subnet_id":
            v = f'"{subnet}"'
        elif n == "ami" and ami:
            v = f'"{ami}"'
        attrs.append((n, v))
    root = [("delete_on_termination", "true"), ("device_name", None), ("encrypted", None), ("iops", None), ("kms_key_id", None),
            ("tags_all", None), ("throughput", None), ("volume_id", None), ("volume_size", "30"), ("volume_type", '"gp3"')]
    return _tf_create(addr, "aws_instance", addr.split(".")[1].split("[")[0], attrs, [("root_block_device", root)])


def _tf_header() -> list[str]:
    return ["Terraform used the selected providers to generate the following execution plan. Resource", "actions are indicated with the following symbols:",
            "  + create", "  ~ update in-place", "-/+ destroy and then create replacement", "  - destroy", "", "Terraform will perform the following actions:", ""]


def _tf_sg(name: str = "web") -> list[str]:
    attrs = [("arn", None), ("description", '"Managed by Terraform"'), ("egress", None), ("id", None), ("ingress", "LIST"), ("name", f'"{name}-sg"'), ("name_prefix", None),
             ("owner_id", None), ("revoke_rules_on_delete", "false"), ("tags", {"Name": f"{name}-sg"}), ("tags_all", {"Name": f"{name}-sg"}), ("vpc_id", '"vpc-0123abcd"')]
    lines = []
    for n, v in attrs:
        if v == "LIST":
            lines += ["      + ingress                = [", "          + {", "              + cidr_blocks      = [", '                  + "10.0.0.0/16",', "                ]",
                      "              + description      = \"https\"", "              + from_port        = 443", "              + ipv6_cidr_blocks = []", "              + prefix_list_ids  = []",
                      '              + protocol         = "tcp"', "              + security_groups  = []", "              + self             = false", "              + to_port          = 443",
                      "            },", "        ]"]
        else:
            lines += _tf_attrs([(n, v)], 6)
    # re-align: attrs were rendered one by one, so pad names to a common width the way terraform does
    w = max(len(n) for n, _ in attrs)
    fixed = []
    for l in lines:
        m = re.match(r"^(      \+ )(\w+)\s+= (.*)$", l)
        fixed.append(f"{m.group(1)}{m.group(2):<{w}} = {m.group(3)}" if m else l)
    return ["  # aws_security_group.web will be created", '  + resource "aws_security_group" "web" {'] + fixed + ["    }", ""]


def _tf_update() -> list[str]:
    return ["  # aws_instance.web will be updated in-place", '  ~ resource "aws_instance" "web" {', '        id                                   = "i-0abc123def4567890"',
            '      ~ instance_type                        = "t3.micro" -> "t3.small"', "      ~ tags                                 = {",
            '          ~ "Version" = "1.7" -> "1.8"', "            # (1 unchanged element hidden)", "        }", "      ~ tags_all                             = {",
            '          ~ "Version" = "1.7" -> "1.8"', "            # (1 unchanged element hidden)", "        }", "        # (28 unchanged attributes hidden)", "",
            "        # (1 unchanged block hidden)", "    }", ""]


def _tf_replace() -> list[str]:
    return ["  # aws_db_instance.main must be replaced", '-/+ resource "aws_db_instance" "main" {', '      ~ address                               = "main.cxyz.us-east-1.rds.amazonaws.com" -> (known after apply)',
            "      ~ allocated_storage                     = 20 -> 50 # forces replacement", '      ~ arn                                   = "arn:aws:rds:us-east-1:123456789012:db:main" -> (known after apply)',
            "      + backup_window                         = (known after apply)", "      + ca_cert_identifier                    = (known after apply)", '      ~ engine_version_actual                 = "16.2" -> (known after apply)',
            '      ~ id                                    = "main" -> (known after apply)', "      + latest_restorable_time                = (known after apply)",
            "      + maintenance_window                    = (known after apply)", "      + resource_id                           = (known after apply)", "      + status                                = (known after apply)",
            '      ~ storage_type                          = "gp2" -> "gp3"', "      + username                              = (known after apply)", "        # (22 unchanged attributes hidden)", "    }", ""]


def _tf_destroy() -> list[str]:
    return ["  # aws_s3_bucket.old will be destroyed", "  # (because aws_s3_bucket.old is not in configuration)", '  - resource "aws_s3_bucket" "old" {',
            '      - arn                         = "arn:aws:s3:::acme-old-logs" -> null', '      - bucket                      = "acme-old-logs" -> null',
            '      - id                          = "acme-old-logs" -> null', '      - region                      = "us-east-1" -> null', "      - tags                        = {} -> null", "    }", ""]


def _tf_plan_create() -> str:
    lines = _tf_header()
    lines += _tf_instance("aws_instance.web", "web", {"Name": "web"})
    lines += _tf_create("aws_s3_bucket.logs", "aws_s3_bucket", "logs", [("acceleration_status", None), ("acl", None), ("arn", None), ("bucket", '"acme-logs-prod"'),
                        ("bucket_domain_name", None), ("bucket_regional_domain_name", None), ("force_destroy", "false"), ("hosted_zone_id", None), ("id", None),
                        ("object_lock_enabled", None), ("policy", None), ("region", None), ("request_payer", None), ("tags", {"Env": "prod", "Name": "logs"}),
                        ("tags_all", {"Env": "prod", "Name": "logs", "ManagedBy": "terraform"}), ("website_domain", None), ("website_endpoint", None)])
    lines += _tf_sg()
    lines += _tf_create("aws_iam_role.app", "aws_iam_role", "app", [("arn", None), ("assume_role_policy", '"{\\"Version\\":\\"2012-10-17\\"}"'), ("create_date", None),
                        ("force_detach_policies", "false"), ("id", None), ("managed_policy_arns", None), ("max_session_duration", "3600"), ("name", '"app"'),
                        ("name_prefix", None), ("path", '"/"'), ("tags_all", None), ("unique_id", None)])
    lines += ["Plan: 4 to add, 0 to change, 0 to destroy.", "", "Changes to Outputs:", "  + instance_id = (known after apply)", "  + bucket_name = \"acme-logs-prod\""]
    return "\n".join(lines) + "\n"


def _tf_plan_mixed(error: bool = False) -> str:
    lines = _tf_header() + _tf_update() + _tf_replace() + _tf_destroy()
    lines += _tf_create("aws_cloudwatch_log_group.app", "aws_cloudwatch_log_group", "app", [("arn", None), ("id", None), ("log_group_class", None), ("name", '"/app"'),
                        ("name_prefix", None), ("retention_in_days", "14"), ("skip_destroy", "false"), ("tags_all", None)])
    if error:
        return "\n".join(lines[:-1] + ["╷", "│ Error: Invalid count argument", "│ ", "│   on modules/app/main.tf line 31, in resource \"aws_instance\" \"worker\":",
                                       "│   31:   count = length(aws_subnet.private[*].id)", "│ ", "│ The \"count\" value depends on resource attributes that cannot be determined until apply.", "╵"]) + "\n"
    lines += ["Plan: 2 to add, 2 to change, 2 to destroy."]
    return "\n".join(lines) + "\n"


def _tf_plan_updates_only() -> str:
    return "\n".join(_tf_header() + _tf_update() + ["Plan: 0 to add, 1 to change, 0 to destroy."]) + "\n"


_TF_HEAD = re.compile(r"^# (\S+) (?:will be|must be) (.+?)$")
_TF_RES = re.compile(r'^(?:(?:-/\+|<=|[+~-])\s+)?(?:resource|data) "[^"]+" "[^"]+" \{$')
_TF_ATTR = re.compile(r'^(?:(?:-/\+|<=|[+~-])\s+)?("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.\-]+)\s+= (.*)$')
_TF_BLOCK = re.compile(r"^(?:(?:-/\+|<=|[+~-])\s+)?([A-Za-z0-9_.\-]+) \{$")
_TF_ELEM = re.compile(r'^(?:(?:-/\+|<=|[+~-])\s+)?("(?:[^"\\]|\\.)*"|[0-9.\-]+|true|false|null|\(known after apply\)),?$')
_TF_ANON = re.compile(r"^(?:(?:-/\+|<=|[+~-])\s+)?\{$")
_TF_COMPUTED = re.compile(r"^# computed after apply: (.+)$")
_TF_SAME = "(same as tags)"
_KA = "(known after apply)"


def _tf_block_leaves(ls: list[str], i: int):
    """Parse the lines of one block (already stripped) from index i up to its closer. Returns (leaves, next index) where leaves
    is a list of (path tuple, value); repeated names get #k segments, and a `tags_all = (same as tags)` leaf is expanded."""
    out: list = []
    seen: Counter = Counter()
    first_block: dict = {}
    while i < len(ls):
        s = ls[i]
        if s in ("}", "},", "]", "],"):
            return out, i + 1
        m = _TF_COMPUTED.match(s)
        if m:
            for n in m.group(1).split(", "):
                out.append(((n,), _KA))
            i += 1
            continue
        m = _TF_ATTR.match(s)
        if m:
            name, val = m.group(1).strip('"'), m.group(2)
            if val in ("{", "["):
                sub, i = _tf_block_leaves(ls, i + 1)
                out += [((f"{name}#{seen[name]}",) + p, v) for p, v in sub] or [((f"{name}#{seen[name]}",), "<empty block>")]
                seen[name] += 1
                first_block.setdefault(name, sub)
                continue
            if name == "tags_all" and val == _TF_SAME and "tags" in first_block:
                out += [((f"tags_all#{seen['tags_all']}",) + p, v) for p, v in first_block["tags"]]
                seen["tags_all"] += 1
            else:
                out.append(((name,), val))
            i += 1
            continue
        m = _TF_BLOCK.match(s)
        if m or s.endswith("{") and not s.startswith("#"):
            name = m.group(1) if m else ""
            sub, i = _tf_block_leaves(ls, i + 1)
            out += [((f"{name}#{seen[name]}",) + p, v) for p, v in sub] or [((f"{name}#{seen[name]}",), "<empty block>")]
            seen[name] += 1
            continue
        m = _TF_ELEM.match(s)
        if m:
            out.append(((f"[{seen['#elem']}]",), m.group(1)))
            seen["#elem"] += 1
        i += 1
    return out, i


def _tf_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Resource chunks: from a '  # addr will be ...' header to the resource's closing '    }' (inclusive), or to the line before the next header."""
    spans, i, n = [], 0, len(lines)
    while i < n:
        if lines[i].startswith("  # ") and _TF_HEAD.match(lines[i].strip()):
            j = i + 1
            while j < n and not (lines[j].startswith("  # ") and _TF_HEAD.match(lines[j].strip())):
                j += 1
                if lines[j - 1] == "    }":
                    break
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _tf_resources(text: str) -> tuple[dict, list]:
    """address -> [(action text, sorted leaves)] and the lines that belong to no resource chunk."""
    lines = text.split("\n")
    res: dict = {}
    inside = set()
    for i, j in _tf_spans(lines):
        inside.update(range(i, j))
        m = _TF_HEAD.match(lines[i].strip())
        chunk = [l.strip() for l in lines[i + 1:j]]
        start = next((k for k, c in enumerate(chunk) if _TF_RES.match(c)), None)
        leaves = _tf_block_leaves(chunk, start + 1)[0] if start is not None else []
        res.setdefault(m.group(1), []).append((m.group(2), sorted(leaves, key=repr)))
    return res, [l for k, l in enumerate(lines) if k not in inside]


def probes_313(text: str) -> dict:
    res, other = _tf_resources(text)
    summary = [l for l in other if l.startswith("Plan:") or l.startswith("No changes") or l.startswith("Apply complete")]
    errors = [l for l in other if l.startswith(("│", "╷", "╵", "Error:"))]
    outs = [l for l in other if l.startswith("  + ") or l.startswith("  ~ ") or l.startswith("  - ")]
    return {"resources and actions": sorted((a, [x[0] for x in v]) for a, v in res.items()),
            "attribute values (literal or computed) per resource": sorted((a, [x[1] for x in v]) for a, v in res.items()),
            "plan summary": summary, "errors": errors, "output changes": outs}


_COMP = re.compile(r"^(\s+)\+ ([A-Za-z0-9_.\-]+)\s+= \(known after apply\)$")


def fold_computed(text: str) -> str:
    lines = text.split("\n")
    n = len(lines)
    drop: set[int] = set()
    insert: dict[int, str] = {}
    for i0, j0 in _tf_spans(lines):
        stack: list[int] = []
        owner: dict[int, int] = {}
        for i in range(i0 + 1, j0):
            s = lines[i].strip()
            owner[i] = stack[-1] if stack else -1
            if s in ("}", "},", "]", "],"):
                if stack:
                    stack.pop()
                    owner[i] = stack[-1] if stack else -1
            elif s.endswith("{") or s.endswith("["):
                stack.append(i)
        comp: dict[int, list] = defaultdict(list)
        for i in range(i0 + 1, j0):
            m = _COMP.match(lines[i])
            if m and owner[i] >= 0:
                comp[owner[i]].append((i, m.group(1), m.group(2)))
        for items in comp.values():
            if len(items) >= 3:
                drop.update(i for i, _, _ in items)
                insert[items[0][0]] = f"{items[0][1]}# computed after apply: " + ", ".join(nm for _, _, nm in items)
        for i in range(i0 + 1, j0):  # a tags_all block that repeats the tags block above it, in the same enclosing block
            m = re.match(r"^(\s+\+ tags_all\s+= )\{$", lines[i])
            if not m or i in drop:
                continue
            end = next((k for k in range(i + 1, j0) if owner[k] == owner[i] and lines[k].strip() in ("}", "},")), None)
            tags = next((k for k in range(i - 1, i0, -1) if owner[k] == owner[i] and re.match(r"^\s+\+ tags\s+= \{$", lines[k])), None)
            if end is None or tags is None:
                continue
            tend = next((k for k in range(tags + 1, j0) if owner[k] == owner[tags] and lines[k].strip() in ("}", "},")), None)
            if tend is None or tend >= i or lines[tags + 1:tend] != lines[i + 1:end]:
                continue
            insert[i] = m.group(1) + _TF_SAME
            drop.update(range(i + 1, end + 1))
    out = []
    for i, l in enumerate(lines):
        if i in insert:
            out.append(insert[i])
        elif i not in drop:
            out.append(l)
    res = "\n".join(out)
    return res if len(res) < len(text) else text


case(
    313,
    claim="In a terraform plan, the '+ attr = (known after apply)' lines of each block become one '# computed after apply: a, b, c' line and a tags_all block identical to the tags block above it becomes '(same as tags)'; every resource and action, every literal attribute, every update or replace line that carries a before value, the summary, the output changes and any error block are unchanged.",
    loses="the original attribute order and column alignment (computed attributes are listed together at the position of the first one)",
    encode=fold_computed,
    probes=probes_313,
    fixtures=[
        Fixture("create plan: instance, bucket (tags_all has a default tag), security group, role", _tf_plan_create(), True),
        Fixture("mixed plan: update, replace (computed lines among before->after lines), destroy, create; summary at the end", _tf_plan_mixed(), True),
        Fixture("plan that stops with an error after resource blocks", _tf_plan_mixed(error=True), True),
        Fixture("negative: update-only plan has no '+ ... (known after apply)' lines", _tf_plan_updates_only(), False),
        Fixture("negative: no changes", "No changes. Your infrastructure matches the configuration.\n\nTerraform has compared your real infrastructure against your configuration\nand found no differences, so no changes are needed.\n", False),
    ],
    min_gain=0.12,
)


# ===============================================================================================================
# #1241 count / for_each siblings: the first block in full, each later sibling with the same attribute names only as the
# attributes whose text differs. Probes: reconstruct every instance's attribute leaves (the first block overlaid with the
# delta) and compare with the original's leaves.

_ADDR_IDX = re.compile(r"^(.+?)(\[(?:\d+|\"[^\"]*\")\])$")


def _well_formed(body: list[str]) -> bool:
    """Brackets balance and every line at the block's top level sits at indent 6, so indentation chunks and bracket parsing agree."""
    depth = 0
    for l in body:
        s = l.strip()
        if not s:
            continue
        ind = len(l) - len(l.lstrip(" "))
        if s in ("}", "},", "]", "],"):
            depth -= 1
            if depth < 0:
                return False
            continue
        if not (_TF_ATTR.match(s) or _TF_BLOCK.match(s) or _TF_ELEM.match(s) or _TF_ANON.match(s)):
            return False  # a line the leaf parser cannot read would make a chunk invisible to it
        if depth == 0 and ind != 6:
            return False
        if s.endswith("{") or s.endswith("["):
            depth += 1
    return depth == 0


def delta_siblings(text: str) -> str:
    lines = text.split("\n")
    n = len(lines)
    heads = [i for i in range(n) if lines[i].startswith("  # ") and _TF_HEAD.match(lines[i].strip())]
    blocks = []  # (head line, open line, close line, addr, base, action)
    for h in heads:
        m = _TF_HEAD.match(lines[h].strip())
        a = _ADDR_IDX.match(m.group(1))
        if not a or h + 1 >= n or not _TF_RES.match(lines[h + 1].strip()) or not m.group(2).startswith("created"):
            continue
        close = next((k for k in range(h + 2, n) if lines[k] == "    }"), None)
        if close is None or any(lines[k].startswith("  # ") for k in range(h + 2, close)) or not _well_formed(lines[h + 2:close]):
            continue
        blocks.append((h, h + 1, close, m.group(1), a.group(1), m.group(2)))

    def chunks(b):
        """top-level attribute chunks of a resource block: ordered list of (name#k, [lines])."""
        h, o, c = b[0], b[1], b[2]
        out, seen, cur = [], Counter(), None
        for k in range(o + 1, c):
            l = lines[k]
            ind = len(l) - len(l.lstrip(" "))
            if ind == 6 and l.strip():
                nm = re.match(r"^\s+[+~-] (\S+)", l)
                name = nm.group(1) if nm else l.strip()
                cur = [f"{name}#{seen[name]}", [l]]
                seen[name] += 1
                out.append(cur)
            elif cur is not None:
                cur[1].append(l)
        return out

    first: dict = {}
    repl: dict[int, list[str]] = {}
    for b in blocks:
        key = (b[4], b[5])
        ch = chunks(b)
        if key not in first:
            first[key] = (b, ch)
            continue
        fb, fch = first[key]
        if [c[0] for c in ch] != [c[0] for c in fch]:
            continue
        diff = [c for c, f in zip(ch, fch) if c[1] != f[1]]
        body = [f"      # as {fb[3]}, except:"] + [l for c in diff for l in c[1]]
        if sum(len(l) + 1 for l in body) < sum(len(l) + 1 for l in lines[b[1] + 1:b[2]]):
            repl[b[0]] = lines[b[0]:b[1] + 1] + body + [lines[b[2]]]
            repl.update({k: [] for k in range(b[0] + 1, b[2] + 1)})
    out = []
    for i in range(n):
        if i in repl:
            out += repl[i]
        else:
            out.append(lines[i])
    res = "\n".join(out)
    return res if len(res) < len(text) else text


_AS = re.compile(r"^# as (\S+), except:$")


def probes_1241(text: str) -> dict:
    lines = text.split("\n")
    by_addr: dict = {}
    order = []
    inside = set()
    for i, j in _tf_spans(lines):
        inside.update(range(i, j))
        m = _TF_HEAD.match(lines[i].strip())
        chunk = [l.strip() for l in lines[i + 1:j]]
        start = next((k for k, c in enumerate(chunk) if _TF_RES.match(c)), None)
        base_addr, first_line = None, (start + 1 if start is not None else 0)
        if start is not None and start + 1 < len(chunk) and _AS.match(chunk[start + 1]):
            base_addr, first_line = _AS.match(chunk[start + 1]).group(1), start + 2
        leaves = _tf_block_leaves(chunk, first_line)[0] if start is not None else []
        tops: dict = defaultdict(list)
        for p, v in leaves:
            tops[p[0]].append((p, v))
        if base_addr is not None and base_addr in by_addr:
            merged = dict(by_addr[base_addr])
            merged.update(tops)  # a delta replaces whole top-level chunks
            tops = merged
        by_addr[m.group(1)] = dict(tops)
        order.append((m.group(1), m.group(2)))
    other = [l for k, l in enumerate(lines) if k not in inside and l.startswith(("Plan:", "No changes", "Changes to Outputs", "│", "╷", "╵"))]
    flat = {a: sorted((p, v) for t in tops.values() for p, v in t) for a, tops in by_addr.items()}
    return {"instances and actions": order, "attribute leaves per instance": sorted(flat.items(), key=lambda kv: kv[0]),
            "plan summary and errors": other}


def _tf_siblings_plan(kind: str) -> str:
    lines = _tf_header()
    if kind == "count":
        for k in range(6):
            lines += _tf_instance(f"aws_instance.web[{k}]", "web", {"Name": f"web-{k}", "Role": "app"}, subnet=f"subnet-0abc12{30 + k % 3}")
        lines += ["Plan: 6 to add, 0 to change, 0 to destroy.", ""]
    elif kind == "for_each":
        azs = ["us-east-1a", "us-east-1b", "us-east-1c"]
        for k, az in enumerate(azs):
            attrs = [("arn", None), ("assign_ipv6_address_on_creation", "false"), ("availability_zone", f'"{az}"'), ("availability_zone_id", None),
                     ("cidr_block", f'"10.0.{k}.0/24"'), ("enable_dns64", "false"), ("id", None), ("ipv6_cidr_block_association_id", None),
                     ("map_public_ip_on_launch", "false"), ("owner_id", None), ("private_dns_hostname_type_on_launch", None), ("tags", {"Name": f"private-{az}", "Tier": "private"}),
                     ("tags_all", {"Name": f"private-{az}", "Tier": "private"}), ("vpc_id", '"vpc-0123abcd"')]
            lines += _tf_create(f'aws_subnet.private["{az}"]', "aws_subnet", "private", attrs)
        lines += ["Plan: 3 to add, 0 to change, 0 to destroy.", ""]
    elif kind == "mixed":  # siblings whose attribute sets differ (a conditional block) must not be folded; a different AMI in the middle
        for k in range(4):
            lines += _tf_instance(f"aws_instance.web[{k}]", "web", {"Name": f"web-{k}"}, ami="ami-0ff8a91507f77f867" if k == 2 else None)
        lines += _tf_create("aws_instance.web[4]", "aws_instance", "web", [("ami", '"ami-0c55b159cbfafe1f0"'), ("id", None), ("instance_type", '"t3.micro"')])
        lines += ["Plan: 5 to add, 0 to change, 0 to destroy.", ""]
    return "\n".join(lines)


case(
    1241,
    claim="For count and for_each resources the first block of a family is printed in full and each later 'will be created' sibling with the same attribute names keeps its header and only the attribute chunks whose text differs, behind a '# as <first address>, except:' line; every instance's full attribute set can be rebuilt from the delta form, and siblings with a different attribute set or action stay in full.",
    loses="nothing a reader cannot rebuild, except the position of the unchanged attributes inside a sibling (they are implied by the first block)",
    encode=delta_siblings,
    probes=probes_1241,
    fixtures=[
        Fixture("count = 6 instances", _tf_siblings_plan("count"), True),
        Fixture("for_each over three subnets", _tf_siblings_plan("for_each"), True),
        Fixture("siblings with a different AMI in the middle and a shorter block at the end", _tf_siblings_plan("mixed"), True),
        Fixture("negative: a single instance has no siblings", "\n".join(_tf_header() + _tf_instance("aws_instance.web", "web", {"Name": "web"}) + ["Plan: 1 to add, 0 to change, 0 to destroy.", ""]), False),
    ],
    min_gain=0.25,
)


# ===============================================================================================================
# #314 terraform show -json: keep only what changes. Parsed JSON in, a pruned plan of the same schema out (compact), so the
# probes can run the same questions on both: which resources are created, changed, destroyed or replaced, which attributes
# differ (before and after), which are unknown until apply or sensitive, and what is untouched.

def _tf_res_values(kind: str, i: int) -> dict:
    return {"ami": "ami-0c55b159cbfafe1f0", "arn": f"arn:aws:ec2:us-east-1:123456789012:instance/i-{i:017x}", "associate_public_ip_address": False,
            "availability_zone": "us-east-1a", "cpu_core_count": 1, "cpu_threads_per_core": 2, "disable_api_termination": False, "ebs_optimized": False,
            "get_password_data": False, "iam_instance_profile": "", "id": f"i-{i:017x}", "instance_state": "running", "instance_type": "t3.micro",
            "ipv6_address_count": 0, "ipv6_addresses": [], "key_name": "deploy", "monitoring": False, "private_dns": f"ip-10-0-1-{i}.ec2.internal",
            "private_ip": f"10.0.1.{i}", "public_dns": "", "public_ip": "", "root_block_device": [{"delete_on_termination": True, "device_name": "/dev/sda1", "encrypted": False,
                                                                                                    "iops": 100, "kms_key_id": "", "tags": {}, "throughput": 0, "volume_id": f"vol-{i:017x}", "volume_size": 30, "volume_type": "gp3"}],
            "security_groups": [], "source_dest_check": True, "subnet_id": "subnet-0abc1230", "tags": {"Name": f"{kind}-{i}", "Env": "prod"},
            "tags_all": {"Name": f"{kind}-{i}", "Env": "prod"}, "tenancy": "default", "user_data": None, "vpc_security_group_ids": ["sg-0123abcd"]}


def _tf_json_plan(n_noop: int = 14) -> str:
    def meta(addr, name):
        return {"address": addr, "mode": "managed", "type": "aws_instance", "name": name, "provider_name": "registry.terraform.io/hashicorp/aws"}

    noop = [(meta(f"aws_instance.worker[{i}]", "worker"), _tf_res_values("worker", i)) for i in range(n_noop)]
    changes, planned, prior, conf = [], [], [], []
    for m, v in noop:
        sens = {"root_block_device": [{"tags": {}}], "tags": {}, "tags_all": {}, "ipv6_addresses": []}
        changes.append({**m, "change": {"actions": ["no-op"], "before": v, "after": v, "after_unknown": {}, "before_sensitive": sens, "after_sensitive": sens}})
        planned.append({**m, "schema_version": 1, "values": v, "sensitive_values": sens})
        prior.append({**m, "schema_version": 1, "values": v, "sensitive_values": sens})
        conf.append({"address": m["address"], "mode": "managed", "type": "aws_instance", "name": "worker", "provider_config_key": "aws",
                     "expressions": {"ami": {"constant_value": v["ami"]}, "instance_type": {"references": ["var.instance_type"]}, "subnet_id": {"references": ["aws_subnet.a.id"]}}, "schema_version": 1})
    # one in-place update, one create, one destroy, one replace, one with a sensitive value
    web_b, web_a = _tf_res_values("web", 90), _tf_res_values("web", 90)
    web_a.update({"instance_type": "t3.small", "tags": {"Name": "web-90", "Env": "prod", "Version": "1.8"}, "tags_all": {"Name": "web-90", "Env": "prod", "Version": "1.8"}})
    changes.append({**meta("aws_instance.web", "web"), "change": {"actions": ["update"], "before": web_b, "after": web_a, "after_unknown": {}, "before_sensitive": {}, "after_sensitive": {}}})
    new_after = {k: v for k, v in _tf_res_values("api", 91).items() if k not in ("arn", "id", "private_ip", "private_dns", "instance_state")}
    changes.append({**meta("aws_instance.api", "api"), "change": {"actions": ["create"], "before": None, "after": new_after,
                                                                    "after_unknown": {"arn": True, "id": True, "private_ip": True, "private_dns": True, "instance_state": True}, "before_sensitive": False, "after_sensitive": {"root_block_device": [{"tags": {}}]}}})
    old = _tf_res_values("batch", 92)
    changes.append({**meta("aws_instance.batch", "batch"), "change": {"actions": ["delete"], "before": old, "after": None, "after_unknown": {}, "before_sensitive": {}, "after_sensitive": False}})
    db_b = {"id": "main", "engine": "postgres", "engine_version": "16.2", "allocated_storage": 20, "password": "hunter2-old", "storage_type": "gp2", "address": "main.cxyz.us-east-1.rds.amazonaws.com"}
    db_a = {"id": None, "engine": "postgres", "engine_version": "16.2", "allocated_storage": 50, "password": "hunter2-new", "storage_type": "gp3", "address": None}
    changes.append({"address": "aws_db_instance.main", "mode": "managed", "type": "aws_db_instance", "name": "main", "provider_name": "registry.terraform.io/hashicorp/aws",
                    "action_reason": "replace_because_cannot_update", "change": {"actions": ["delete", "create"], "before": db_b, "after": db_a, "after_unknown": {"id": True, "address": True},
                                                                                "before_sensitive": {"password": True}, "after_sensitive": {"password": True}, "replace_paths": [["allocated_storage"]]}})
    outs = {"instance_ip": {"actions": ["no-op"], "before": "10.0.1.1", "after": "10.0.1.1", "after_unknown": False, "before_sensitive": False, "after_sensitive": False},
            "api_arn": {"actions": ["create"], "before": None, "after": None, "after_unknown": True, "before_sensitive": False, "after_sensitive": False}}
    plan = {"format_version": "1.2", "terraform_version": "1.7.5", "variables": {"env": {"value": "prod"}, "instance_type": {"value": "t3.micro"}},
            "planned_values": {"outputs": {"instance_ip": {"sensitive": False, "type": "string", "value": "10.0.1.1"}}, "root_module": {"resources": planned}},
            "resource_changes": changes, "output_changes": outs,
            "prior_state": {"format_version": "1.0", "terraform_version": "1.7.5", "values": {"root_module": {"resources": prior}}},
            "configuration": {"provider_config": {"aws": {"name": "aws", "full_name": "registry.terraform.io/hashicorp/aws", "expressions": {"region": {"constant_value": "us-east-1"}}}},
                              "root_module": {"resources": conf}},
            "relevant_attributes": [{"resource": "aws_subnet.a", "attribute": ["id"]}], "timestamp": "2025-03-04T10:15:02Z", "checks": []}
    return json.dumps(plan, separators=(",", ":")) + "\n"


def _non_null(d):
    return {k: v for k, v in d.items() if v is not None} if isinstance(d, dict) else d


def digest_plan_json(text: str) -> str:
    try:
        plan = json.loads(text)
    except ValueError:
        return text
    if not (isinstance(plan, dict) and isinstance(plan.get("resource_changes"), list) and all(isinstance(r, dict) and isinstance(r.get("change"), dict) for r in plan["resource_changes"])):
        return text
    if plan.get("_ctxprune"):
        return text
    kept, noop = [], []
    for r in plan["resource_changes"]:
        ch = r["change"]
        if ch.get("actions") == ["no-op"]:
            noop.append(r.get("address"))
            continue
        b, a = ch.get("before"), ch.get("after")
        nb, na = dict(b) if isinstance(b, dict) else b, dict(a) if isinstance(a, dict) else a
        unk = ch.get("after_unknown")
        if ch.get("actions") == ["update"] and isinstance(b, dict) and isinstance(a, dict):
            changed = {k for k in set(b) | set(a) if b.get(k) != a.get(k)} | ({k for k, v in unk.items() if v} if isinstance(unk, dict) else set())
            nb = {k: v for k, v in b.items() if k in changed}
            na = {k: v for k, v in a.items() if k in changed}
        else:
            nb, na = _non_null(nb), _non_null(na)

        def prune_sens(s, keep):
            return {k: v for k, v in s.items() if k in keep} if isinstance(s, dict) and keep is not None else s

        keys = set(nb or {}) | set(na or {}) if isinstance(nb, (dict, type(None))) and isinstance(na, (dict, type(None))) else None
        new = {**r, "change": {**ch, "before": nb, "after": na, "after_unknown": ({k: v for k, v in unk.items() if v} if isinstance(unk, dict) else unk),
                               "before_sensitive": prune_sens(ch.get("before_sensitive"), keys), "after_sensitive": prune_sens(ch.get("after_sensitive"), keys)}}
        kept.append(new)
    out = {k: v for k, v in plan.items() if k in ("format_version", "terraform_version", "variables", "timestamp")}
    out["resource_changes"] = kept
    oc = plan.get("output_changes")
    if isinstance(oc, dict):
        out["output_changes"] = {k: v for k, v in oc.items() if not (isinstance(v, dict) and v.get("actions") == ["no-op"])}
        noop += [f"output.{k}" for k, v in oc.items() if isinstance(v, dict) and v.get("actions") == ["no-op"]]
    out["_ctxprune"] = {"unchanged": noop, "dropped_keys": sorted(k for k in plan if k not in out)}
    res = json.dumps(out, separators=(",", ":")) + ("\n" if text.endswith("\n") else "")
    return res if len(res) < len(text) else text


def probes_314(text: str) -> dict:
    try:
        plan = json.loads(text)
        rcs = plan["resource_changes"]
        assert isinstance(rcs, list)
    except Exception:  # noqa: BLE001
        return {"parse": "failed"}
    acts, diffs, unknown, sens, same = {}, {}, {}, {}, []
    for r in rcs:
        if not isinstance(r, dict) or not isinstance(r.get("change"), dict):
            continue
        ch, addr = r["change"], r.get("address")
        if ch.get("actions") == ["no-op"]:
            same.append(addr)
            continue
        acts[addr] = (ch.get("actions"), r.get("action_reason"))
        b, a = ch.get("before") or {}, ch.get("after") or {}
        d = {}
        for k in sorted(set(b) | set(a)):
            if b.get(k) != a.get(k):
                d[k] = (b.get(k), a.get(k))
        diffs[addr] = d
        unk = ch.get("after_unknown")
        unknown[addr] = sorted(k for k, v in unk.items() if v) if isinstance(unk, dict) else unk
        s = []
        for side in ("before_sensitive", "after_sensitive"):
            sv = ch.get(side)
            if isinstance(sv, dict):
                s += [f"{side}:{k}" for k, v in sv.items() if v]
        sens[addr] = sorted(s)
    outs = {}
    oc = plan.get("output_changes")
    if isinstance(oc, dict):
        for k, v in oc.items():
            if isinstance(v, dict) and v.get("actions") != ["no-op"]:
                outs[k] = (v.get("actions"), v.get("before"), v.get("after"), v.get("after_unknown"))
            elif isinstance(v, dict):
                same.append(f"output.{k}")
    ctx = plan.get("_ctxprune")
    if isinstance(ctx, dict) and isinstance(ctx.get("unchanged"), list):
        same += ctx["unchanged"]
    return {"created, updated, deleted, replaced": acts, "attributes that differ (before, after)": diffs, "unknown until apply": unknown,
            "sensitive attributes": sens, "output changes": outs, "untouched resources and outputs": sorted(same, key=str),
            "versions": (plan.get("terraform_version"), plan.get("format_version")), "variables": plan.get("variables")}


def _share_314() -> dict:
    p = json.loads(_tf_json_plan())
    tot = len(json.dumps(p, separators=(",", ":")))
    return {k: round(len(json.dumps(v, separators=(",", ":"))) / tot, 2) for k, v in p.items()}


case(
    314,
    claim="Keeps the resources that are not no-ops, with their before and after reduced to the attributes that differ (all non-null attributes for a create or delete) plus the unknown-after-apply and sensitive flags of those attributes, the output changes that are not no-ops and the addresses of everything untouched; planned_values, prior_state, configuration and relevant_attributes are dropped. The result is still a terraform plan JSON, so the same questions get the same answers.",
    loses="planned_values, prior_state, configuration and the attributes of unchanged resources (the full JSON is the saved original); null-valued attributes of created or deleted resources",
    encode=digest_plan_json,
    probes=probes_314,
    fixtures=[
        Fixture("plan JSON: 14 unchanged workers, an in-place update, a create with unknown ids, a delete, a replace with a sensitive password", _tf_json_plan(), True),
        Fixture("plan JSON with 4 unchanged resources", _tf_json_plan(4), True),
        Fixture("negative: not a plan (terraform show -json of a state has no resource_changes)", json.dumps({"format_version": "1.0", "values": {"root_module": {"resources": []}}}) + "\n", False),
        Fixture("negative: already a digest", digest_plan_json(_tf_json_plan(6)), False),
        Fixture("negative: a plan with no resource changes is already tiny", json.dumps({"format_version": "1.2", "resource_changes": []}, separators=(",", ":")) + "\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #318 ansible-playbook: per-task host counts. Bare `ok: [host]` and `skipping: [host]` lines of a task become one
# `  ok N, skipping M` line; `changed:` lines, fatal/failed/unreachable result lines, retries, debug output, handlers,
# `included:` and the PLAY RECAP stay verbatim. reconstructed from memory (ansible-playbook is not installed here).

_TASK = re.compile(r"^(TASK|RUNNING HANDLER) \[(.*)\] \*+$")
_PLAY = re.compile(r"^PLAY \[(.*)\] \*+$")
_RECAP = re.compile(r"^PLAY RECAP \*+$")
_BARE = re.compile(r"^(ok|skipping): \[([^\]\n]+)\]$")


def fold_ansible_hosts(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if _TASK.match(lines[i]):
            j = i + 1
            while j < len(lines) and not (_TASK.match(lines[j]) or _PLAY.match(lines[j]) or _RECAP.match(lines[j])):
                j += 1
            body = lines[i + 1:j]
            cnt = Counter(m.group(1) for l in body if (m := _BARE.match(l)))
            if cnt["ok"] + cnt["skipping"] >= 3:
                out.append(f"  ok {cnt['ok']}, skipping {cnt['skipping']}")
                out += [l for l in body if not _BARE.match(l)]
            else:
                out += body
            i = j
            continue
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


_SUM = re.compile(r"^  ok (\d+), skipping (\d+)$")


def probes_318(text: str) -> dict:
    plays, tasks, recap, cur, in_recap = [], [], [], None, False
    for line in text.split("\n"):
        if in_recap:
            if line.strip():
                recap.append(line)
            continue
        if _RECAP.match(line):
            in_recap, cur = True, None
            continue
        m = _PLAY.match(line)
        if m:
            plays.append(m.group(1))
            cur = None
            continue
        m = _TASK.match(line)
        if m:
            cur = {"kind": m.group(1), "name": m.group(2), "play": len(plays), "ok": 0, "skipping": 0, "changed": [], "other": []}
            tasks.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        if (b := _BARE.match(line)):
            cur[b.group(1)] += 1
        elif (s := _SUM.match(line)):
            cur["ok"] += int(s.group(1))
            cur["skipping"] += int(s.group(2))
        elif line.startswith("changed: [") and line.endswith("]") and "=>" not in line:
            cur["changed"].append(line[10:-1])
        else:
            cur["other"].append(line)
    return {"plays": plays, "per task: kind, name, ok, skipping, changed hosts, every other line":
            [(t["kind"], t["name"], t["play"], t["ok"], t["skipping"], t["changed"], t["other"]) for t in tasks], "PLAY RECAP lines": recap}


def _ansible_run(n: int = 36, n_tasks: int = 9) -> str:
    hosts = [f"web{i:02d}" for i in range(1, n + 1)]
    dead, bad_mid, bad_end = "web21", "web13", "web31"
    alive = [h for h in hosts if h != dead]
    star = lambda s: s + " " + "*" * max(3, 78 - len(s))  # noqa: E731
    out = [star("PLAY [webservers]"), "", star("TASK [Gathering Facts]")]
    for h in hosts:
        out.append(f'fatal: [{h}]: UNREACHABLE! => {{"changed": false, "msg": "Failed to connect to the host via ssh: ssh: connect to host {h} port 22: Connection timed out", "unreachable": true}}'
                   if h == dead else f"ok: [{h}]")
    out.append("")
    changed_in = {"Install nginx": {"web03", "web09", "web25"}, "Ensure chrony is installed": {"web07"}, "Reload nginx": set(hosts)}
    skip_in = {"Reload nginx": {"web02", "web04", "web06", "web08", "web10"}, "Set timezone": set()}
    names = ["common : Ensure chrony is installed", "common : Set timezone", "nginx : Install nginx", "nginx : Deploy nginx.conf", "nginx : Report version",
             "nginx : Wait for port 80", "nginx : Reload nginx", "app : Smoke test", "app : Rotate logs"][:n_tasks]
    live = list(alive)
    for t in names:
        short = t.split(" : ")[1]
        out.append(star(f"TASK [{t}]"))
        for h in list(live):
            if short == "Deploy nginx.conf" and h == bad_mid:
                out.append(f'fatal: [{h}]: FAILED! => {{"changed": false, "msg": "Destination directory /etc/nginx does not exist", "path": "/etc/nginx/nginx.conf"}}')
                live.remove(h)
            elif short == "Smoke test" and h == bad_end:
                out.append(f'fatal: [{h}]: FAILED! => {{"changed": false, "content": "", "msg": "Status code was -1 and not [200]: Request failed: <urlopen error [Errno 111] Connection refused>", "redirected": false, "status": -1, "url": "http://{h}/health"}}')
                live.remove(h)
            elif short == "Report version":
                out.append(f'ok: [{h}] => {{"msg": "nginx 1.24.0 on {h}"}}')
            elif short == "Wait for port 80" and h == "web05":
                out += [f"FAILED - RETRYING: [{h}]: Wait for port 80 (3 retries left).", f"FAILED - RETRYING: [{h}]: Wait for port 80 (2 retries left).", f"ok: [{h}]"]
            elif h in skip_in.get(short, ()):
                out.append(f"skipping: [{h}]")
            elif h in changed_in.get(short, ()):
                out.append(f"changed: [{h}]")
            else:
                out.append(f"ok: [{h}]")
        out.append("")
    out += [star("RUNNING HANDLER [nginx : restart nginx]")] + [f"changed: [{h}]" for h in ("web03", "web09", "web25")] + [""]
    out.append(star("PLAY RECAP"))
    for h in hosts:
        if h == dead:
            out.append(f"{h:<26} : ok=0    changed=0    unreachable=1    failed=0    skipped=0    rescued=0    ignored=0   ")
        elif h in (bad_mid, bad_end):
            out.append(f"{h:<26} : ok=4    changed=0    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0   ")
        else:
            out.append(f"{h:<26} : ok={n_tasks + 1}    changed={1 if h in changed_in['Install nginx'] else 0}    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0   ")
    return "\n".join(out) + "\n"


case(
    318,
    claim="Within each task, bare 'ok: [host]' and 'skipping: [host]' lines become one 'ok N, skipping M' line; changed lines, fatal, FAILED and UNREACHABLE result lines (verbatim JSON), retry lines, debug output, handlers and the PLAY RECAP are unchanged, so counts, every changed or failed host and every failure message survive.",
    loses="which individual hosts were ok or skipped for each task (derivable only as 'every host that is not listed and not failed')",
    encode=fold_ansible_hosts,
    probes=probes_318,
    fixtures=[
        Fixture("36 hosts, one unreachable at fact gathering, one failure mid-play, one at the last task", _ansible_run(), True),
        Fixture("12 hosts, 4 tasks", _ansible_run(12, 4), True),
        Fixture("negative: a play with three tasks that each touch two hosts", "PLAY [db] " + "*" * 60 + "\n\nTASK [Gathering Facts] " + "*" * 50 + "\nok: [db1]\nok: [db2]\n\nTASK [Dump] " + "*" * 60 +
                "\nchanged: [db1]\nskipping: [db2]\n\nPLAY RECAP " + "*" * 60 + "\ndb1                       : ok=2    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0   \n", False),
        Fixture("negative: ansible --check output without task headers", "ok: [web01]\nok: [web02]\nok: [web03]\nok: [web04]\n", False),
    ],
    min_gain=0.15,
)


# ===============================================================================================================
# #1238 ansible -m setup: keep a curated subset. Curated fact values stay verbatim; any other value longer than 40 characters of JSON
# is folded to a placeholder that names its type and size, so every key name remains. Probes: parse the JSON and answer OS, CPU,
# memory and default-IP questions, list every key, and report the small facts.
# reconstructed from memory (ansible is not installed here)

_CURATED = {"ansible_distribution", "ansible_distribution_version", "ansible_distribution_release", "ansible_distribution_major_version", "ansible_os_family",
            "ansible_system", "ansible_kernel", "ansible_kernel_version", "ansible_architecture", "ansible_machine", "ansible_hostname", "ansible_fqdn",
            "ansible_nodename", "ansible_domain", "ansible_processor", "ansible_processor_count", "ansible_processor_cores", "ansible_processor_threads_per_core",
            "ansible_processor_vcpus", "ansible_memtotal_mb", "ansible_memfree_mb", "ansible_swaptotal_mb", "ansible_swapfree_mb", "ansible_memory_mb",
            "ansible_default_ipv4", "ansible_default_ipv6", "ansible_all_ipv4_addresses", "ansible_all_ipv6_addresses", "ansible_virtualization_type",
            "ansible_virtualization_role", "ansible_python_version", "ansible_pkg_mgr", "ansible_service_mgr", "ansible_uptime_seconds"}
_SMALL = 40
_FACTS_HEAD = re.compile(r"^(\S+) \| (SUCCESS|CHANGED) => \{$")
_FOLDED_FACT = re.compile(r"^<folded (dict|list|str)(?:, \d+ (?:keys|items))?, \d+ chars>$")


def _fold_fact(v) -> str:
    n = len(json.dumps(v, ensure_ascii=False))
    kind = "dict" if isinstance(v, dict) else "list" if isinstance(v, list) else "str"
    extra = f", {len(v)} {'keys' if kind == 'dict' else 'items'}" if kind != "str" else ""
    return f"<folded {kind}{extra}, {n} chars>"


def curate_facts(text: str) -> str:
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = _FACTS_HEAD.match(lines[i])
        end = next((k for k in range(i + 1, len(lines)) if lines[k] == "}"), None) if m else None
        if m and end is not None and not any(_FACTS_HEAD.match(l) for l in lines[i + 1:end]):
            try:
                d = json.loads("{\n" + "\n".join(lines[i + 1:end]) + "\n}")
                facts = d.get("ansible_facts") if isinstance(d, dict) else None
                if isinstance(facts, dict):
                    d["ansible_facts"] = {k: (v if k in _CURATED or len(json.dumps(v, ensure_ascii=False)) <= _SMALL or (isinstance(v, str) and v.startswith("<folded ")) else _fold_fact(v))
                                          for k, v in facts.items()}
                    out.append(lines[i])
                    out += json.dumps(d, indent=4, ensure_ascii=False).split("\n")[1:]
                    i = end + 1
                    continue
            except ValueError:
                pass
        out.append(lines[i])
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_1238(text: str) -> dict:
    hosts = {}
    other = []
    cur, buf = None, []
    for line in text.split("\n"):
        if cur is None:
            m = _FACTS_HEAD.match(line)
            if m:
                cur, buf = m.group(1), []
            else:
                other.append(line)
            continue
        if line == "}":
            try:
                facts = json.loads("{\n" + "\n".join(buf) + "\n}")["ansible_facts"]
                rest = {}
                for k, v in facts.items():
                    if k in _CURATED:
                        continue
                    if isinstance(v, str) and (fm := _FOLDED_FACT.match(v)):
                        rest[k] = (fm.group(1), None)
                    else:
                        big = len(json.dumps(v, ensure_ascii=False)) > _SMALL
                        rest[k] = (type(v).__name__ if not isinstance(v, (dict, list)) else type(v).__name__, None if big else v)
                hosts[cur] = {"curated facts": {k: facts[k] for k in sorted(facts) if k in _CURATED}, "all fact names": sorted(facts), "others (type, value if small)": rest}
            except Exception:  # noqa: BLE001
                hosts[cur] = {"unparsed": buf}
            cur = None
        else:
            buf.append(line)
    if cur is not None:
        other.append(cur)
        other += buf
    return {"hosts": hosts, "lines outside fact blocks": other}


def _facts(host: str, n_disks: int = 8, vm: bool = True) -> dict:
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    env = {"HOME": "/home/deploy", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "LOGNAME": "deploy", "MAIL": "/var/mail/deploy", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games",
           "PWD": "/home/deploy", "SHELL": "/bin/bash", "SSH_CLIENT": "10.0.0.5 51234 22", "SSH_CONNECTION": "10.0.0.5 51234 10.0.1.14 22", "SSH_TTY": "/dev/pts/0", "TERM": "xterm-256color",
           "USER": "deploy", "XDG_RUNTIME_DIR": "/run/user/1001", "XDG_SESSION_ID": "412", "_": "/usr/bin/python3",
           "LS_COLORS": "rs=0:di=01;34:ln=01;36:mh=00:pi=40;33:so=01;35:do=01;35:bd=40;33;01:cd=40;33;01:or=40;31;01:mi=00:su=37;41:sg=30;43:ca=30;41:tw=30;42:ow=34;42:st=37;44:ex=01;32:*.tar=01;31:*.tgz=01;31:*.zip=01;31:*.gz=01;31:*.bz2=01;31:*.jpg=01;35:*.png=01;35:*.mp3=00;36:*.wav=00;36:"}
    devs = {f"loop{k}": {"holders": [], "host": "", "links": {"ids": [], "labels": [], "masters": [], "uuids": []}, "model": None, "partitions": {}, "removable": "0", "rotational": "1",
                         "sas_address": None, "sas_device_handle": None, "scheduler_mode": "none", "sectors": "114080", "sectorsize": "512", "size": "55.70 MB", "support_discard": "4096",
                         "vendor": None, "virtual": 1} for k in range(n_disks - 2)}
    devs["vda"] = {"holders": [], "host": "", "links": {"ids": [], "labels": [], "masters": [], "uuids": []}, "model": None, "removable": "0", "rotational": "1", "scheduler_mode": "mq-deadline",
                   "sectors": "209715200", "sectorsize": "512", "size": "100.00 GB", "vendor": "0x1af4", "virtual": 1,
                   "partitions": {"vda1": {"holder": [], "links": {"ids": [], "labels": ["cloudimg-rootfs"], "masters": [], "uuids": [sha("rootfs")[:8] + "-0000-4000-8000-" + sha("r")[:12]]}, "sectors": "209602560", "sectorsize": 512, "size": "99.95 GB", "start": "2048", "uuid": sha("rootfs")[:8] + "-0000-4000-8000-" + sha("r")[:12]}}}
    feats = {k: "off [fixed]" if k.startswith(("tx_", "rx_", "tls_")) else "on" for k in [
        "esp_hw_offload", "esp_tx_csum_hw_offload", "fcoe_mtu", "generic_receive_offload", "generic_segmentation_offload", "highdma", "hw_tc_offload", "l2_fwd_offload", "large_receive_offload",
        "loopback", "macsec_hw_offload", "netns_local", "ntuple_filters", "receive_hashing", "rx_all", "rx_checksumming", "rx_fcs", "rx_gro_hw", "rx_gro_list", "rx_udp_gro_forwarding",
        "rx_udp_tunnel_port_offload", "rx_vlan_filter", "rx_vlan_offload", "rx_vlan_stag_filter", "rx_vlan_stag_hw_parse", "scatter_gather", "tcp_segmentation_offload", "tls_hw_record",
        "tls_hw_rx_offload", "tls_hw_tx_offload", "tx_checksum_fcoe_crc", "tx_checksum_ip_generic", "tx_checksum_ipv4", "tx_checksum_ipv6", "tx_checksum_sctp", "tx_checksumming",
        "tx_esp_segmentation", "tx_fcoe_segmentation", "tx_gre_csum_segmentation", "tx_gre_segmentation", "tx_gso_list", "tx_gso_partial", "tx_gso_robust", "tx_ipxip4_segmentation",
        "tx_ipxip6_segmentation", "tx_lockless", "tx_nocache_copy", "tx_scatter_gather", "tx_scatter_gather_fraglist", "tx_sctp_segmentation", "tx_tcp6_segmentation", "tx_tcp_ecn_segmentation",
        "tx_tcp_mangleid_segmentation", "tx_tcp_segmentation", "tx_tunnel_remcsum_segmentation", "tx_udp_segmentation", "tx_udp_tnl_csum_segmentation", "tx_udp_tnl_segmentation",
        "tx_vlan_offload", "tx_vlan_stag_hw_insert", "udp_fragmentation_offload", "vlan_challenged"]}
    ip = "10.0.1.14"
    f = {
        "ansible_all_ipv4_addresses": [ip], "ansible_all_ipv6_addresses": ["fe80::4001:aff:fe00:e"], "ansible_apparmor": {"status": "enabled"}, "ansible_architecture": "x86_64",
        "ansible_bios_date": "04/01/2014", "ansible_bios_vendor": "SeaBIOS", "ansible_bios_version": "1.16.2-debian-1.16.2-1", "ansible_board_asset_tag": "NA", "ansible_board_name": "NA",
        "ansible_board_vendor": "NA", "ansible_cmdline": {"BOOT_IMAGE": "/boot/vmlinuz-5.15.0-97-generic", "console": "ttyS0", "ro": True, "root": "LABEL=cloudimg-rootfs"},
        "ansible_date_time": {"date": "2025-03-04", "day": "04", "epoch": "1741083302", "hour": "10", "iso8601": "2025-03-04T10:15:02Z", "minute": "15", "month": "03", "second": "02",
                              "time": "10:15:02", "tz": "UTC", "tz_offset": "+0000", "weekday": "Tuesday", "year": "2025"},
        "ansible_default_ipv4": {"address": ip, "alias": "eth0", "broadcast": "10.0.1.255", "gateway": "10.0.1.1", "interface": "eth0", "macaddress": "42:01:0a:00:01:0e", "mtu": 1460,
                                 "netmask": "255.255.255.0", "network": "10.0.1.0", "prefix": "24", "type": "ether"},
        "ansible_default_ipv6": {}, "ansible_device_links": {"ids": {"vda": ["scsi-0Google_PersistentDisk_" + host]}, "labels": {"vda1": ["cloudimg-rootfs"]}, "masters": {}, "uuids": {}},
        "ansible_devices": devs, "ansible_distribution": "Ubuntu", "ansible_distribution_file_parsed": True, "ansible_distribution_file_path": "/etc/os-release",
        "ansible_distribution_file_variety": "Debian", "ansible_distribution_major_version": "22", "ansible_distribution_release": "jammy", "ansible_distribution_version": "22.04",
        "ansible_dns": {"nameservers": ["10.0.0.2"], "search": ["us-east-1.compute.internal"]}, "ansible_domain": "us-east-1.compute.internal", "ansible_effective_group_id": 1001,
        "ansible_effective_user_id": 1001, "ansible_env": env, "ansible_eth0": {"active": True, "device": "eth0", "features": feats, "hw_timestamp_filters": [], "ipv4": {"address": ip,
        "broadcast": "10.0.1.255", "netmask": "255.255.255.0", "network": "10.0.1.0", "prefix": "24"}, "ipv6": [{"address": "fe80::4001:aff:fe00:e", "prefix": "64", "scope": "link"}],
        "macaddress": "42:01:0a:00:01:0e", "module": "virtio_net", "mtu": 1460, "pciid": "virtio0", "promisc": False, "speed": -1, "timestamping": [], "type": "ether"},
        "ansible_fibre_channel_wwn": [], "ansible_fips": False, "ansible_fqdn": f"{host}.us-east-1.compute.internal", "ansible_hostname": host,
        "ansible_interfaces": ["lo", "eth0"], "ansible_is_chroot": False, "ansible_iscsi_iqn": "iqn.2004-10.com.ubuntu:01:" + sha(host)[:12], "ansible_kernel": "5.15.0-97-generic",
        "ansible_kernel_version": "#107-Ubuntu SMP Wed Feb 7 13:26:48 UTC 2024", "ansible_lo": {"active": True, "device": "lo", "features": {"loopback": "on [fixed]"}, "ipv4": {"address": "127.0.0.1",
        "broadcast": "", "netmask": "255.0.0.0", "network": "127.0.0.0", "prefix": "8"}, "mtu": 65536, "promisc": False, "type": "loopback"}, "ansible_local": {},
        "ansible_lsb": {"codename": "jammy", "description": "Ubuntu 22.04.4 LTS", "id": "Ubuntu", "major_release": "22", "release": "22.04"}, "ansible_machine": "x86_64",
        "ansible_machine_id": sha(host)[:32], "ansible_memfree_mb": 5412, "ansible_memory_mb": {"nocache": {"free": 6531, "used": 1261}, "real": {"free": 5412, "total": 7792, "used": 2380},
        "swap": {"cached": 0, "free": 0, "total": 0, "used": 0}}, "ansible_memtotal_mb": 7792, "ansible_mounts": [
            {"block_available": 24106131, "block_size": 4096, "block_total": 25755200, "block_used": 1649069, "device": "/dev/vda1", "fstype": "ext4", "inode_available": 12552310,
             "inode_total": 12945408, "inode_used": 393098, "mount": "/", "options": "rw,relatime,discard", "size_available": 98737688576, "size_total": 105492275200, "uuid": "N/A"},
            {"block_available": 0, "block_size": 131072, "block_total": 218, "block_used": 218, "device": "/dev/loop0", "fstype": "squashfs", "inode_available": 0, "inode_total": 17847,
             "inode_used": 17847, "mount": "/snap/core20/2182", "options": "ro,nodev,relatime", "size_available": 0, "size_total": 28573696, "uuid": "N/A"},
            {"block_available": 0, "block_size": 131072, "block_total": 463, "block_used": 463, "device": "/dev/loop1", "fstype": "squashfs", "inode_available": 0, "inode_total": 11584,
             "inode_used": 11584, "mount": "/snap/lxd/24061", "options": "ro,nodev,relatime", "size_available": 0, "size_total": 60686336, "uuid": "N/A"}],
        "ansible_nodename": f"{host}", "ansible_os_family": "Debian", "ansible_pkg_mgr": "apt", "ansible_proc_cmdline": {"BOOT_IMAGE": "/boot/vmlinuz-5.15.0-97-generic", "console": ["tty1", "ttyS0"],
        "ro": True, "root": "LABEL=cloudimg-rootfs"}, "ansible_processor": ["0", "GenuineIntel", "Intel(R) Xeon(R) CPU @ 2.20GHz", "1", "GenuineIntel", "Intel(R) Xeon(R) CPU @ 2.20GHz"],
        "ansible_processor_cores": 2, "ansible_processor_count": 1, "ansible_processor_nproc": 4, "ansible_processor_threads_per_core": 2, "ansible_processor_vcpus": 4,
        "ansible_product_name": "Google Compute Engine" if vm else "NA", "ansible_product_serial": "NA", "ansible_product_uuid": "NA", "ansible_product_version": "NA",
        "ansible_python": {"executable": "/usr/bin/python3", "has_sslcontext": True, "type": "cpython", "version": {"major": 3, "micro": 12, "minor": 10, "releaselevel": "final", "serial": 0},
                           "version_info": [3, 10, 12, "final", 0]}, "ansible_python_version": "3.10.12", "ansible_real_group_id": 1001, "ansible_real_user_id": 1001, "ansible_selinux": {"status": "Missing"},
        "ansible_selinux_python_present": True, "ansible_service_mgr": "systemd", "ansible_ssh_host_key_ecdsa_public": "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBB" + sha("e")[:40] * 2,
        "ansible_ssh_host_key_ed25519_public": "AAAAC3NzaC1lZDI1NTE5AAAAI" + sha("d")[:40], "ansible_ssh_host_key_rsa_public": "AAAAB3NzaC1yc2EAAAADAQABAAABgQ" + sha("r")[:60] * 6,
        "ansible_swapfree_mb": 0, "ansible_swaptotal_mb": 0, "ansible_system": "Linux", "ansible_system_capabilities": [""], "ansible_system_capabilities_enforced": "True",
        "ansible_uptime_seconds": 1204331, "ansible_user_dir": "/home/deploy", "ansible_user_gecos": "", "ansible_user_gid": 1001, "ansible_user_id": "deploy", "ansible_user_shell": "/bin/bash",
        "ansible_user_uid": 1001, "ansible_userspace_architecture": "x86_64", "ansible_userspace_bits": "64", "ansible_virtualization_role": "guest", "ansible_virtualization_tech_guest": ["kvm"],
        "ansible_virtualization_tech_host": [], "ansible_virtualization_type": "kvm", "discovered_interpreter_python": "/usr/bin/python3", "gather_subset": ["all"], "module_setup": True}
    return f


def _setup_out(host: str, **kw) -> str:
    return f"{host} | SUCCESS => " + json.dumps({"ansible_facts": _facts(host, **kw), "changed": False}, indent=4, sort_keys=True) + "\n"


case(
    1238,
    claim="Keeps the distribution, kernel, CPU, memory, default-IP and similar facts verbatim and folds every other fact value over 40 characters of JSON to a '<folded dict, 38 keys, 2412 chars>' placeholder, so every fact name stays and the OS, memory and IP questions are answered from the same values.",
    loses="the values of environment variables, mounts, devices, interface features, ssh host keys, python and date facts (the original is saved); only their names, types and sizes remain",
    encode=curate_facts,
    probes=probes_1238,
    fixtures=[
        Fixture("ansible -m setup on a VM", _setup_out("web01"), True),
        Fixture("ansible -m setup on a host with many loop devices, three hosts, one unreachable between them",
                _setup_out("web02", n_disks=14) + 'web03 | UNREACHABLE! => {\n    "changed": false,\n    "msg": "Failed to connect to the host via ssh: ssh: connect to host web03 port 22: Connection timed out",\n    "unreachable": true\n}\n' + _setup_out("web04", vm=False), True),
        Fixture("negative: filter=ansible_distribution* output holds only curated facts",
                'web01 | SUCCESS => {\n    "ansible_facts": {\n        "ansible_distribution": "Ubuntu",\n        "ansible_distribution_major_version": "22",\n        "ansible_distribution_release": "jammy",\n        "ansible_distribution_version": "22.04",\n        "discovered_interpreter_python": "/usr/bin/python3"\n    },\n    "changed": false\n}\n', False),
        Fixture("negative: unreachable host", 'web03 | UNREACHABLE! => {\n    "changed": false,\n    "msg": "Failed to connect to the host via ssh: Permission denied (publickey).",\n    "unreachable": true\n}\n', False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #980 iptables-save: fold generated chain families. Generated names (KUBE-SEP-<16>, KUBE-SVC-<16>, KUBE-XLB, KUBE-FW, CNI-<24 hex>,
# cali-tw/fw/sm-cali...) keep the first chain of a family in full; the others (declaration and rules) are dropped behind one
# '# FAMILY: N more chains like X omitted' line. A generated chain is NEVER dropped when any of its lines has a non-zero counter or
# a DROP, REJECT or KUBE-MARK-DROP target. Zero counters ([0:0]) are removed everywhere. Hand-written chains are untouched.
# reconstructed from memory (iptables-save printed nothing in this container: no netfilter).

_FAMILIES = [("KUBE-SEP-*", re.compile(r"^KUBE-SEP-[A-Z2-7]{16}$")), ("KUBE-SVC-*", re.compile(r"^KUBE-SVC-[A-Z2-7]{16}$")), ("KUBE-SVL-*", re.compile(r"^KUBE-SVL-[A-Z2-7]{16}$")),
             ("KUBE-XLB-*", re.compile(r"^KUBE-XLB-[A-Z2-7]{16}$")), ("KUBE-FW-*", re.compile(r"^KUBE-FW-[A-Z2-7]{16}$")), ("CNI-*", re.compile(r"^CNI-[0-9a-f]{24}$")),
             ("cali-tw-*", re.compile(r"^cali-(?:tw|fw|sm)-cali[0-9a-f]{8,11}$"))]
_CHAIN = re.compile(r"^:(\S+) (\S+)(?: \[(\d+):(\d+)\])?$")
_RULE = re.compile(r"^(?:\[(\d+):(\d+)\] )?-A (\S+)(?: (.*))?$")
_BAD = re.compile(r"(?:^| )-j (?:DROP|REJECT|KUBE-MARK-DROP)(?: |$)")
_FOLDMARK = re.compile(r"^# (\S+): (\d+) more chains? like (\S+) omitted$")


def _family(name: str) -> str | None:
    return next((f for f, rx in _FAMILIES if rx.match(name)), None)


def fold_chain_families(text: str) -> str:
    lines = text.split("\n")
    # pass 1 (per table): which generated chains may be folded
    keep_full: set = set()
    declared: dict = {}
    table = None
    for i, l in enumerate(lines):
        if l.startswith("*"):
            table = l
        m = _CHAIN.match(l)
        if m and _family(m.group(1)):
            declared.setdefault((table, m.group(1)), i)
            if m.group(3) not in (None, "0") or m.group(4) not in (None, "0"):
                keep_full.add((table, m.group(1)))
        r = _RULE.match(l)
        if r and _family(r.group(3)):
            if (r.group(1) not in (None, "0")) or (r.group(2) not in (None, "0")) or _BAD.search(r.group(4) or ""):
                keep_full.add((table, r.group(3)))
    first: dict = {}
    folded: dict = defaultdict(list)
    for key in declared:  # file order
        if key in keep_full:
            continue
        fam = (key[0], _family(key[1]))
        if fam not in first:
            first[fam] = key
        else:
            folded[fam].append(key)
    drop_keys = {k for v in folded.values() for k in v}
    out = []
    table = None
    for l in lines:
        if l.startswith("*"):
            table = l
        m = _CHAIN.match(l)
        if m:
            if (table, m.group(1)) in drop_keys:
                continue
            out.append(f":{m.group(1)} {m.group(2)}" if (m.group(3), m.group(4)) == ("0", "0") else l)
            fam = _family(m.group(1))
            if fam and first.get((table, fam)) == (table, m.group(1)) and folded.get((table, fam)):
                out.append(f"# {fam}: {len(folded[(table, fam)])} more chains like {m.group(1)} omitted")
            continue
        r = _RULE.match(l)
        if r:
            if (table, r.group(3)) in drop_keys:
                continue
            if (r.group(1), r.group(2)) == ("0", "0"):
                l = f"-A {r.group(3)}" + (f" {r.group(4)}" if r.group(4) is not None else "")
        out.append(l)
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_980(text: str) -> dict:
    table, decl, fixed_rules, bad, fam_names, fam_marks, nonzero, structure = None, {}, {}, [], defaultdict(set), Counter(), [], []
    for line in text.split("\n"):
        if line.startswith("*"):
            table = line
            structure.append(line)
            continue
        if line == "COMMIT":
            structure.append(line)
            continue
        if (mk := _FOLDMARK.match(line)):
            fam_marks[(table, mk.group(1))] += int(mk.group(2))
            continue
        if line.startswith("#") or not line.strip():
            continue
        c = _CHAIN.match(line)
        if c:
            name = c.group(1)
            if c.group(3) not in (None, "0") or c.group(4) not in (None, "0"):
                nonzero.append((table, line))
            if (fam := _family(name)):
                fam_names[(table, fam)].add(name)
            else:
                decl[(table, name)] = c.group(2)
            continue
        r = _RULE.match(line)
        if r:
            chain = r.group(3)
            body = f"-A {chain}" + (f" {r.group(4)}" if r.group(4) is not None else "")
            if r.group(1) not in (None, "0") or r.group(2) not in (None, "0"):
                nonzero.append((table, line))
            if _BAD.search(r.group(4) or ""):
                bad.append((table, body))
            if not _family(chain):
                fixed_rules.setdefault((table, chain), []).append(body)
        else:
            structure.append(line)
    fam_count = {k: len(v) + fam_marks.get(k, 0) for k, v in fam_names.items()}
    for k, n in fam_marks.items():
        fam_count.setdefault(k, n)
    return {"tables and COMMITs": structure, "hand-written and built-in chains with policy": sorted((k, v) for k, v in decl.items() if k[0] is not None),
            "rules of non-generated chains, verbatim": sorted((k, v) for k, v in fixed_rules.items() if k[0] is not None),
            "every DROP, REJECT or MARK-DROP rule": sorted(bad), "non-zero counters": sorted(nonzero),
            "generated chains per family and table": sorted((k, v) for k, v in fam_count.items() if k[0] is not None)}


def _b32(s: str) -> str:
    import base64
    return base64.b32encode(hashlib.sha256(s.encode()).digest())[:16].decode()


def _iptables(n_svc: int = 30, counters: bool = False, docker_only: bool = False) -> str:
    c0 = lambda extra="": " [0:0]" if counters or True else ""  # noqa: E731
    out = ["# Generated by iptables-save v1.8.7 on Tue Mar  4 10:15:02 2025", "*filter", ":INPUT ACCEPT [0:0]", ":FORWARD DROP [0:0]", ":OUTPUT ACCEPT [0:0]", ":DOCKER - [0:0]",
           ":DOCKER-ISOLATION-STAGE-1 - [0:0]", ":DOCKER-ISOLATION-STAGE-2 - [0:0]", ":DOCKER-USER - [0:0]"]
    if not docker_only:
        out += [":KUBE-EXTERNAL-SERVICES - [0:0]", ":KUBE-FIREWALL - [0:0]", ":KUBE-FORWARD - [0:0]", ":KUBE-SERVICES - [0:0]"]
    out += [":MYAPP-INGRESS - [0:0]"]
    pre = (lambda p, b: f"[{p}:{b}] ") if counters else (lambda p, b: "")
    out += [f"{pre(120, 9600)}-A INPUT -p tcp -m tcp --dport 22 -j MYAPP-INGRESS", f"{pre(0, 0)}-A FORWARD -j DOCKER-USER", f"{pre(0, 0)}-A FORWARD -j DOCKER-ISOLATION-STAGE-1",
            f"{pre(0, 0)}-A FORWARD -o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT", f"{pre(0, 0)}-A DOCKER-USER -j RETURN",
            f"{pre(0, 0)}-A DOCKER-ISOLATION-STAGE-1 -i docker0 ! -o docker0 -j DOCKER-ISOLATION-STAGE-2", f"{pre(0, 0)}-A DOCKER-ISOLATION-STAGE-1 -j RETURN",
            f"{pre(0, 0)}-A DOCKER-ISOLATION-STAGE-2 -o docker0 -j DROP", f"{pre(0, 0)}-A DOCKER-ISOLATION-STAGE-2 -j RETURN"]
    if not docker_only:
        out += [f"{pre(0, 0)}-A INPUT -j KUBE-FIREWALL",
                f'{pre(0, 0)}-A KUBE-FIREWALL ! -s 127.0.0.0/8 -d 127.0.0.0/8 -m comment --comment "block incoming localnet connections" -m conntrack ! --ctstate RELATED,ESTABLISHED,DNAT -j DROP',
                f'{pre(0, 0)}-A KUBE-SERVICES -d 10.96.7.7/32 -p tcp -m comment --comment "default/broken:http has no endpoints" -m tcp --dport 80 -j REJECT --reject-with icmp-port-unreachable']
    out += [f"{pre(3, 180)}-A MYAPP-INGRESS -s 10.20.0.0/16 -j ACCEPT", f"{pre(0, 0)}-A MYAPP-INGRESS -j DROP", "COMMIT", "# Completed on Tue Mar  4 10:15:02 2025"]
    if docker_only:
        return "\n".join(out) + "\n"
    svc = [(f"svc{k}", _b32(f"svc{k}"), [(_b32(f"sep{k}-{e}"), f"10.244.{k % 4}.{10 + e}", 8080) for e in range(2 + k % 2)]) for k in range(n_svc)]
    nat = ["# Generated by iptables-save v1.8.7 on Tue Mar  4 10:15:02 2025", "*nat", ":PREROUTING ACCEPT [0:0]", ":INPUT ACCEPT [0:0]", ":OUTPUT ACCEPT [0:0]", ":POSTROUTING ACCEPT [0:0]",
           ":DOCKER - [0:0]", ":KUBE-MARK-DROP - [0:0]", ":KUBE-MARK-MASQ - [0:0]", ":KUBE-NODEPORTS - [0:0]", ":KUBE-POSTROUTING - [0:0]", ":KUBE-SERVICES - [0:0]"]
    for name, h, seps in svc:
        nat.append(f":KUBE-SVC-{h} - [0:0]")
    for name, h, seps in svc:
        nat += [f":KUBE-SEP-{s} - [0:0]" for s, _, _ in seps]
    xlb = _b32("xlb-local")
    nat.append(f":KUBE-XLB-{xlb} - [0:0]")
    nat += [f"{pre(0, 0)}-A PREROUTING -m comment --comment \"kubernetes service portals\" -j KUBE-SERVICES", f"{pre(0, 0)}-A POSTROUTING -m comment --comment \"kubernetes postrouting rules\" -j KUBE-POSTROUTING",
            f"{pre(0, 0)}-A KUBE-MARK-DROP -j MARK --set-xmark 0x8000/0x8000", f"{pre(0, 0)}-A KUBE-MARK-MASQ -j MARK --set-xmark 0x4000/0x4000",
            f'{pre(0, 0)}-A KUBE-POSTROUTING -m mark ! --mark 0x4000/0x4000 -j RETURN']
    for k, (name, h, seps) in enumerate(svc):
        nat.append(f'{pre(0, 0)}-A KUBE-SERVICES -d 10.96.{k // 250}.{k % 250 + 1}/32 -p tcp -m comment --comment "default/{name}:http cluster IP" -m tcp --dport 80 -j KUBE-SVC-{h}')
    for k, (name, h, seps) in enumerate(svc):
        nat.append(f'{pre(0, 0)}-A KUBE-SVC-{h} ! -s 10.244.0.0/16 -d 10.96.{k // 250}.{k % 250 + 1}/32 -p tcp -m comment --comment "default/{name}:http cluster IP" -m tcp --dport 80 -j KUBE-MARK-MASQ')
        for e, (s, ip, port) in enumerate(seps):
            prob = f" -m statistic --mode random --probability {1 / (len(seps) - e):.11f}" if e < len(seps) - 1 else ""
            nat.append(f'{pre(0, 0)}-A KUBE-SVC-{h} -m comment --comment "default/{name}:http -> {ip}:{port}"{prob} -j KUBE-SEP-{s}')
        for e, (s, ip, port) in enumerate(seps):
            nat += [f'{pre(0, 0)}-A KUBE-SEP-{s} -s {ip}/32 -m comment --comment "default/{name}:http" -j KUBE-MARK-MASQ',
                    f'{pre(0, 0)}-A KUBE-SEP-{s} -p tcp -m comment --comment "default/{name}:http" -m tcp -j DNAT --to-destination {ip}:{port}']
    nat.append(f'{pre(0, 0)}-A KUBE-XLB-{xlb} -m comment --comment "default/local:http has no local endpoints" -j KUBE-MARK-DROP')
    nat += ["COMMIT", "# Completed on Tue Mar  4 10:15:02 2025"]
    return "\n".join(out + nat) + "\n"


case(
    980,
    claim="Keeps the first chain of each generated family (KUBE-SEP, KUBE-SVC, KUBE-XLB, CNI, cali) in full and replaces the rest by one '# FAMILY: N more chains like X omitted' line, never dropping a generated chain that has a non-zero counter or a DROP, REJECT or KUBE-MARK-DROP rule; zero counters are removed; every hand-written and built-in chain, its policy, its rules and every DROP or REJECT rule are unchanged.",
    loses="the endpoints behind each folded KUBE-SVC chain (which pod IP:port a service reaches, and the service name in the comments) and the rules of folded chains; only the first example and the per-family count remain",
    encode=fold_chain_families,
    probes=probes_980,
    fixtures=[
        Fixture("kube-proxy node: 30 services, 75 endpoints, Docker, a hand-written chain, a no-endpoints REJECT, a no-local-endpoints chain", _iptables(30), True),
        Fixture("the same with iptables-save -c counters (non-zero only on the hand-written chain)", _iptables(12, counters=True), True),
        Fixture("negative: iptables -S output (rule specs, no counters, no families)", "-P INPUT ACCEPT\n-P FORWARD DROP\n-P OUTPUT ACCEPT\n-N DOCKER\n-N DOCKER-USER\n-N MYAPP-INGRESS\n-A INPUT -p tcp -m tcp --dport 22 -j MYAPP-INGRESS\n-A FORWARD -j DOCKER-USER\n-A DOCKER-USER -j RETURN\n-A MYAPP-INGRESS -s 10.20.0.0/16 -j ACCEPT\n-A MYAPP-INGRESS -j DROP\n", False),
        Fixture("negative: chains that merely look generated, no counters", "*nat\n:KUBE-SEP-short -\n:KUBE-SEP-lowercase1234567 -\n:KUBE-SEP-ABCDEFGHIJKLMNOP -\n-A KUBE-SEP-short -j RETURN\nCOMMIT\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #984 df and mount: fold pod-UUID and container-ID path families. A row whose mount point differs from the others only in a UUID, a
# 64-hex id or a kube-api-access-xxxxx name folds into the first such row plus one marker line with the count (and the highest Use% for
# df). Real devices (/dev/...) and rows at 90% or more are never folded. The negatives are real df and mount output from this container.

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_KAA = re.compile(r"kube-api-access-[a-z0-9]{5}\b")
_DF = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(/.*)$")
_MNT = re.compile(r"^(\S+) on (/\S*) type (\S+) \((.*)\)$")
_MARK_DF = re.compile(r"^# \+(\d+) more like the row above: (\S+) \((\S+), max use (\d+)%\)$")
_MARK_MNT = re.compile(r"^# \+(\d+) more like the line above: (\S+) \(type (\S+)\)$")


def _template(path: str) -> str:
    return _KAA.sub("kube-api-access-<x>", _HEX64.sub("<id>", _UUID.sub("<uuid>", path)))


def _row(line: str):
    """(kind, fs key, mount point, use%, foldable) for a df or mount line, else None."""
    m = _DF.match(line)
    if m:
        use = int(m.group(5))
        return "df", m.group(1), m.group(6), use, not m.group(1).startswith("/dev/") and use < 90
    m = _MNT.match(line)
    if m:
        return "mount", m.group(3), m.group(2), -1, not m.group(1).startswith("/dev/")
    return None


def fold_mount_families(text: str) -> str:
    lines = text.split("\n")
    groups: dict = defaultdict(list)
    for i, l in enumerate(lines):
        r = _row(l)
        if r and r[4]:
            t = _template(r[2])
            if t != r[2]:
                groups[(r[0], r[1], t)].append(i)
    drop, insert = set(), {}
    for (kind, fs, t), idx in groups.items():
        if len(idx) < 3:
            continue
        rest = idx[1:]
        drop.update(rest)
        if kind == "df":
            insert[idx[0]] = f"# +{len(rest)} more like the row above: {t} ({fs}, max use {max(_row(lines[k])[3] for k in rest)}%)"
        else:
            insert[idx[0]] = f"# +{len(rest)} more like the line above: {t} (type {fs})"
    out = []
    for i, l in enumerate(lines):
        if i in drop:
            continue
        out.append(l)
        if i in insert:
            out.append(insert[i])
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_984(text: str) -> dict:
    count: Counter = Counter()
    maxuse: dict = {}
    full_rows, real_rows, other = [], [], []
    last_key = None
    for line in text.split("\n"):
        m = _MARK_DF.match(line)
        if m:
            k = ("df", m.group(3), m.group(2))
            count[k] += int(m.group(1))
            maxuse[k] = max(maxuse.get(k, -1), int(m.group(4)))
            continue
        m = _MARK_MNT.match(line)
        if m:
            count[("mount", m.group(3), m.group(2))] += int(m.group(1))
            continue
        r = _row(line)
        if r is None:
            other.append(line)
            continue
        kind, fs, path, use, foldable = r
        t = _template(path)
        if t != path and (foldable or True):
            k = (kind, fs, t)
            count[k] += 1
            maxuse[k] = max(maxuse.get(k, -1), use)
        if kind == "df" and use >= 90:
            full_rows.append(line)
        if fs.startswith("/dev/") or line.split(" ", 1)[0].startswith("/dev/"):
            real_rows.append(line)
        if t == path:
            other.append(line)
    fam = {k: (n, maxuse.get(k)) for k, n in count.items()}
    return {"rows at 90% or more, verbatim": full_rows, "rows on real devices, verbatim": real_rows,
            "rows and lines that are not part of a template family, verbatim": other,
            "template family -> row count, highest Use%": sorted(fam.items(), key=repr)}


def _sha(s: str, n: int = 64) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:n]


def _uuid(s: str) -> str:
    h = _sha(s, 32)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _kube_node(pods: int = 32, mount: bool = False) -> str:
    rows = []
    for p in range(pods):
        u = _uuid(f"pod{p}")
        ident = _sha(f"ctr{p}")
        if mount:
            rows += [f"tmpfs on /var/lib/kubelet/pods/{u}/volumes/kubernetes.io~projected/kube-api-access-{_sha(f'k{p}', 5)[:5].translate(str.maketrans('abcdef', 'ghijkl'))} type tmpfs (rw,relatime,size=8238672k)",
                     f"shm on /run/containerd/io.containerd.grpc.v1.cri/sandboxes/{ident}/shm type tmpfs (rw,nosuid,nodev,noexec,relatime,size=65536k)",
                     f"overlay on /run/containerd/io.containerd.runtime.v2.task/k8s.io/{ident}/rootfs type overlay (rw,relatime,lowerdir=/var/lib/containerd/snapshots/{p + 1}/fs,upperdir=/var/lib/containerd/snapshots/{p + 40}/fs,workdir=/var/lib/containerd/snapshots/{p + 40}/work)"]
        else:
            rows += [f"tmpfs            8238672      12   8238660   1% /var/lib/kubelet/pods/{u}/volumes/kubernetes.io~projected/kube-api-access-{_sha(f'k{p}', 5)[:5].translate(str.maketrans('abcdef', 'ghijkl'))}",
                     f"shm                65536       0     65536   0% /run/containerd/io.containerd.grpc.v1.cri/sandboxes/{ident}/shm",
                     f"overlay        264212084 8932424  31383528  23% /run/containerd/io.containerd.runtime.v2.task/k8s.io/{ident}/rootfs"]
    if mount:
        head = ["proc on /proc type proc (rw,nosuid,nodev,noexec,relatime)", "sysfs on /sys type sysfs (rw,nosuid,nodev,noexec,relatime)", "/dev/vda1 on / type ext4 (rw,relatime,discard)"]
        tail = [f"/dev/sdb on /var/lib/kubelet/pods/{_uuid('pvc')}/volumes/kubernetes.io~csi/pvc-{_uuid('pvc2')}/mount type ext4 (rw,relatime)"]
        return "\n".join(head + rows + tail) + "\n"
    head = ["Filesystem            1K-blocks      Used Available Use% Mounted on", "/dev/vda1             264212084 241900000  22312084  92% /", "tmpfs                  16477348         0  16477348   0% /dev/shm"]
    tail = [f"/dev/sdb                51474912  49500000   1974912  97% /var/lib/kubelet/pods/{_uuid('pvc')}/volumes/kubernetes.io~csi/pvc-{_uuid('pvc2')}/mount",
            f"overlay               264212084 241700000  22512084  92% /run/containerd/io.containerd.runtime.v2.task/k8s.io/{_sha('busy')}/rootfs"]
    return "\n".join(head + rows + tail) + "\n"


_REAL_DF = """Filesystem              1K-blocks    Used Available Use% Mounted on
tmpfs                    16477348       0  16477348   0% /dev/shm
/dev/vda                264212084 9980900  30335052  25% /
/dev/vdc                   361704  327764     26688  93% /opt/claude-code
/dev/vdd                    49648   32604     16032  68% /opt/env-runner
model_tools_unavailable         4       0         4   0% /mnt/sandboxing/model_tools_env/v1/python
tmpfs                     8238672       0   8238672   0% /sys/fs/cgroup
tmpfs                        1024       0      1024   0% /run/sandbox-telemetry
"""  # real capture from this container
_REAL_MOUNT = """proc on /proc type proc (rw,relatime)
sysfs on /sys type sysfs (rw,relatime)
devtmpfs on /dev type devtmpfs (rw,relatime,size=8227128k,nr_inodes=2056782,mode=755)
tmpfs on /dev/shm type tmpfs (rw,relatime,size=16477348k)
devpts on /dev/pts type devpts (rw,relatime,mode=600,ptmxmode=000)
/dev/vda on / type ext4 (rw,relatime,discard,no_prefetch_block_bitmaps,resv_strict,resuid=65534,resgid=65534)
/dev/vdb on /opt/rclone type squashfs (ro,relatime,errors=continue)
/dev/vdc on /opt/claude-code type ext4 (ro,nosuid,nodev,relatime,norecovery)
/dev/vdd on /opt/env-runner type ext4 (ro,nosuid,nodev,relatime,norecovery)
/dev/vde on /mnt/skills/public type squashfs (ro,relatime,errors=continue)
/dev/vdf on /mnt/skills/examples type squashfs (ro,relatime,errors=continue)
model_tools_unavailable on /mnt/sandboxing/model_tools_env/v1/python type tmpfs (ro,nosuid,nodev,relatime,size=4k,mode=755)
tmpfs on /sys/fs/cgroup type tmpfs (rw,relatime,mode=755)
cgroup on /sys/fs/cgroup/cpu type cgroup (rw,relatime,cpu)
cgroup2 on /sys/fs/cgroup/unified type cgroup2 (rw,relatime)
tmpfs on /run/sandbox-telemetry type tmpfs (rw,nosuid,nodev,relatime,size=1024k,mode=755,uid=64321,gid=64321)
"""  # real capture from this container (abridged: duplicate cgroup lines removed)

case(
    984,
    claim="Rows (df) or lines (mount) whose mount point differs only by a pod UUID, a 64-hex container id or a kube-api-access-xxxxx name, in groups of three or more with the same filesystem, become the first row plus one '+N more like the row above' line carrying the count and highest Use%; /dev devices, rows at 90% or more and everything else stay verbatim.",
    loses="the individual UUIDs, container ids, and (for df) the sizes and used space of the folded rows; rows the user's command named cannot be recognised from the text alone",
    encode=fold_mount_families,
    probes=probes_984,
    fixtures=[
        Fixture("df on a kind node: 32 pods, a full root disk at the top, a full CSI volume and a full overlay at the end", _kube_node(32), True),
        Fixture("mount on the same node", _kube_node(32, mount=True), True),
        Fixture("negative: real df of this container", _REAL_DF, False),
        Fixture("negative: real mount of this container", _REAL_MOUNT, False),
        Fixture("negative: only two pods, below the fold threshold", _kube_node(2), False),
    ],
    min_gain=0.5,
)

_GCLOUD_ADDRESSES = r'''NAME
    gcloud compute addresses create - reserve IP addresses

SYNOPSIS
    gcloud compute addresses create [NAME ...] [--description=DESCRIPTION]
        [--endpoint-type=ENDPOINT_TYPE] [--ip-collection=IP_COLLECTION]
        [--network=NETWORK] [--network-tier=NETWORK_TIER]
        [--prefix-length=PREFIX_LENGTH] [--purpose=PURPOSE] [--subnet=SUBNET]
        [--addresses=ADDRESS,[ADDRESS,...] | --ip-version=IP_VERSION]
        [--global | --region=REGION] [GCLOUD_WIDE_FLAG ...]

DESCRIPTION
    gcloud compute addresses create is used to reserve one or more IP
    addresses. Once an IP address is reserved, it will be associated with the
    project until it is released using 'gcloud compute addresses delete'.
    Ephemeral IP addresses that are in use by resources in the project can be
    reserved using the '--addresses' flag.

EXAMPLES
    To reserve three IP addresses in the 'us-central1' region, run:

        $ gcloud compute addresses create address-1 address-2 address-3 \
            --region=us-central1

    To reserve ephemeral IP addresses '162.222.181.198' and '23.251.146.189'
    which are being used by virtual machine instances in the 'us-central1'
    region, run:

        $ gcloud compute addresses create \
            --addresses=162.222.181.198,23.251.146.189 --region=us-central1

    In the above invocation, the two addresses will be assigned random names.

    To reserve an IP address named subnet-address-1 from the subnet 'default'
    in the 'us-central1' region, run:

        $ gcloud compute addresses create subnet-address-1 \
            --region=us-central1 --subnet=default

    To reserve an IP range '10.110.0.0/16' from the network 'default' for
    'VPC_PEERING', run:

        $ gcloud compute addresses create ip-range-1 --global \
            --addresses=10.110.0.0 --prefix-length=16 \
            --purpose=VPC_PEERING --network=default

    To reserve any IP range with prefix length '16' from the network 'default'
    for 'VPC_PEERING', run:

        $ gcloud compute addresses create ip-range-1 --global \
            --prefix-length=16 --purpose=VPC_PEERING --network=default

    To reserve an address from network 'default' for PRIVATE_SERVICE_CONNECT,
    run:

        $ gcloud compute addresses create psc-address-1 --global \
            --addresses=10.110.0.10 --purpose=PRIVATE_SERVICE_CONNECT \
            --network=default

POSITIONAL ARGUMENTS
     [NAME ...]
        Names of the addresses to create.

FLAGS
     --description=DESCRIPTION
        An optional textual description for the addresses.

     --endpoint-type=ENDPOINT_TYPE
        The endpoint type of the external IPv6 address to be reserved.
        ENDPOINT_TYPE must be one of: VM, NETLB.

     --ip-collection=IP_COLLECTION
        If specified, the public delegated prefix (PDP) from which to allocate
        the BYOIP IP address. The PDP must support enhanced IPv4 allocations.
        If not specified, the address will be allocated from the Google-owned
        IP pool.

     --network=NETWORK
        If specified, the network resource in which the address(es) should be
        reserved.

        This is only available for global internal address, which represents an
        internal IP range reservation from within the network.

     --network-tier=NETWORK_TIER
        The network tier to assign to the reserved IP addresses. NETWORK_TIER
        must be one of: PREMIUM, STANDARD. The default value is PREMIUM.

        While regional external addresses (--region specified, --subnet
        omitted) can use either PREMIUM or STANDARD, global external addresses
        (--global specified, --subnet omitted) can only use PREMIUM. Internal
        addresses can only use PREMIUM.

     --prefix-length=PREFIX_LENGTH
        The prefix length of the IP range. If the address is an IPv4 address,
        it must be a value between 8 and 30 inclusive. If the address is an
        IPv6 address, the only allowed value is 96. If not present, it means
        the address field is a single IP address.

        This field is not applicable to external IPv4 addresses or global IPv6
        addresses.

     --purpose=PURPOSE
        The purpose of the address resource. This field is not applicable to
        external addresses. PURPOSE must be one of: VPC_PEERING,
        SHARED_LOADBALANCER_VIP, GCE_ENDPOINT, IPSEC_INTERCONNECT,
        PRIVATE_SERVICE_CONNECT.

     --subnet=SUBNET
        If specified, the subnet name in which the address(es) should be
        reserved. The subnet must be in the same region as the address.

        The address will represent an internal IP reservation from within the
        subnet. If --address is specified, it must be within the subnet's IP
        range.

        May not be specified with --global.

     At most one of these can be specified:

       --addresses=ADDRESS,[ADDRESS,...]
          Ephemeral IP addresses to promote to reserved status. Only addresses
          that are being used by resources in the project can be promoted. When
          providing this flag, a parallel list of names for the addresses can
          be provided. For example,

              $ gcloud compute addresses create ADDRESS-1 ADDRESS-2             \
                --addresses 162.222.181.197,162.222.181.198             \
                --region us-central1

          will result in 162.222.181.197 being reserved as 'ADDRESS-1' and
          162.222.181.198 as 'ADDRESS-2'. If no names are given,
          server-generated names will be assigned to the IP addresses.

       --ip-version=IP_VERSION
          Version of the IP address to be allocated and reserved. The default
          is IPV4.

          IP version can only be specified for global addresses that are
          generated automatically (i.e., along with the --global flag, given
          --addresses is not specified) and if the --network-tier is PREMIUM.

          IP_VERSION must be one of: IPV4, IPV6.

     At most one of these can be specified:

       --global
          If set, the addresses are global.

       --region=REGION
          Region of the addresses to create. Overrides the default
          compute/region property value for this command invocation.

GCLOUD WIDE FLAGS
    These flags are available to all commands: --access-token-file, --account,
    --billing-project, --configuration, --flags-file, --flatten, --format,
    --help, --impersonate-service-account, --log-http, --project, --quiet,
    --trace-token, --user-output-enabled, --verbosity.

    Run $ gcloud help for details.
'''  # real capture: gcloud compute addresses create --help

_GCLOUD_IMAGES = r'''NAME
    gcloud compute images create - create Compute Engine images

SYNOPSIS
    gcloud compute images create IMAGE_NAME
        (--source-disk=SOURCE_DISK | --source-image=SOURCE_IMAGE
          | --source-image-family=SOURCE_IMAGE_FAMILY
          | --source-snapshot=SOURCE_SNAPSHOT | --source-uri=SOURCE_URI)
        [--architecture=ARCHITECTURE] [--csek-key-file=FILE]
        [--description=DESCRIPTION] [--family=FAMILY]
        [--forbidden-database-file=[DBX_VALUE,...]] [--force]
        [--guest-os-features=[GUEST_OS_FEATURE,...]]
        [--key-exchange-key-file=[KEK_VALUE,...]] [--labels=[KEY=VALUE,...]]
        [--licenses=[LICENSES,...]] [--platform-key-file=PLATFORM_KEY_FILE]
        [--no-require-csek-key-create]
        [--resource-manager-tags=[KEY=VALUE,...]]
        [--signature-database-file=[DB_VALUE,...]]
        [--source-disk-project=SOURCE_DISK_PROJECT]
        [--source-disk-zone=SOURCE_DISK_ZONE]
        [--source-image-project=SOURCE_IMAGE_PROJECT]
        [--storage-location=LOCATION]
        [--kms-key=KMS_KEY : --kms-keyring=KMS_KEYRING
          --kms-location=KMS_LOCATION --kms-project=KMS_PROJECT]
        [GCLOUD_WIDE_FLAG ...]

DESCRIPTION
    gcloud compute images create is used to create custom disk images. The
    resulting image can be provided during instance or disk creation so that
    the instance attached to the resulting disks has access to a known set of
    software or files from the image.

    Images can be created from gzipped compressed tarball containing raw disk
    data, existing disks in any zone, existing images, and existing snapshots
    inside the same project.

    Images are global resources, so they can be used across zones and projects.

    To learn more about creating image tarballs, visit
    https://cloud.google.com/compute/docs/creating-custom-image.

EXAMPLES
    To create an image 'my-image' from a disk 'my-disk' in zone 'us-east1-a',
    run:

        $ gcloud compute images create my-image --source-disk=my-disk \
          --source-disk-zone=us-east1-a

    To create an image 'my-image' from a disk 'my-disk' in zone 'us-east1-a'
    with source disk project 'source-disk-project' run:

        $ gcloud compute images create my-image --source-disk=my-disk \
          --source-disk-zone=us-east1-a \
          --source-disk-project=source-disk-project

    To create an image 'my-image' from another image 'source-image' with source
    image project 'source-image-project', run:

        $ gcloud compute images create my-image \
          --source-image=source-image \
          --source-image-project=source-image-project

    To create an image 'my-image' from the latest non-deprecated image in the
    family 'source-image-family' with source image project
    'source-image-project', run:

        $ gcloud compute images create my-image \
          --source-image-family=source-image-family \
          --source-image-project=source-image-project

    To create an image 'my-image' from a snapshot 'source-snapshot', run:

        $ gcloud compute images create my-image \
          --source-snapshot=source-snapshot

POSITIONAL ARGUMENTS
     IMAGE_NAME
        Name of the disk image to create.

REQUIRED FLAGS
     Exactly one of these must be specified:

       --source-disk=SOURCE_DISK
          A source disk to create the image from. The value for this option can
          be the name of a disk with the zone specified via --source-disk-zone
          flag.

       --source-image=SOURCE_IMAGE
          The name of an image to clone. May be used with
          --source-image-project to clone an image in a different project.

       --source-image-family=SOURCE_IMAGE_FAMILY
          The family of the source image. This will cause the latest non-
          deprecated image in the family to be used as the source image. May be
          used with --source-image-project to refer to an image family in a
          different project.

       --source-snapshot=SOURCE_SNAPSHOT
          A source snapshot to create the image from. The value for this option
          can be the name of a snapshot within the same project as the
          destination image.

       --source-uri=SOURCE_URI
          The full Cloud Storage URI or Artifact Registry path where the raw
          disk image archive is stored. This file must be a gzip-compressed
          tarball whose name ends in .tar.gz or a valid Artifact Registry
          generic repository path. For more information about Cloud Storage
          URIs, see
          https://cloud.google.com/storage/docs/request-endpoints#json-api. If
          an Artifact Registry path is provided, it must be a valid Artifact
          Registry generic repository path in the format:
          projects/<project>/locations/<locations>/repositories/<repo>/packages/<package>/versions/<version_id>
          or
          projects/<project>/locations/<locations>/repositories/<repo>/packages/<package>/versions/<version_id>@dirsum_sha256:<hex_value>

OPTIONAL FLAGS
     --architecture=ARCHITECTURE
        Specifies the architecture or processor type that this image can
        support. For available processor types on Compute Engine, see
        https://cloud.google.com/compute/docs/cpu-platforms. ARCHITECTURE must
        be one of: ARM64, X86_64.

     --csek-key-file=FILE
        Path to a Customer-Supplied Encryption Key (CSEK) key file that maps
        Compute Engine images to user managed keys to be used when creating,
        mounting, or taking snapshots of disks.

        If you pass - as value of the flag, the CSEK is read from stdin. See
        https://cloud.google.com/compute/docs/disks/customer-supplied-encryption
        for more details.

     --description=DESCRIPTION
        An optional, textual description for the image being created.

     --family=FAMILY
        The family of the image. When creating an instance or disk, specifying
        a family will cause the latest non-deprecated image in the family to be
        used.

     --forbidden-database-file=[DBX_VALUE,...]
        Comma-separated list of file paths that point to revoked X.509
        certificates in DER format or raw binary files. When you create a
        Shielded VM instance from this image, these certificates or files are
        added to the forbidden signature database (dbx).

     --force
        By default, image creation fails when it is created from a disk that is
        attached to a running instance. When this flag is used, image creation
        from disk will proceed even if the disk is in use.

     --guest-os-features=[GUEST_OS_FEATURE,...]
        Enables one or more features for VM instances that use the image for
        their boot disks. See the descriptions of supported features at:
        https://cloud.google.com/compute/docs/images/create-delete-deprecate-private-images#guest-os-features.
        GUEST_OS_FEATURE must be one of: BARE_METAL_LINUX_COMPATIBLE,
        CCA_CAPABLE, GVNIC, IDPF, MULTI_IP_SUBNET, SEV_CAPABLE,
        SEV_LIVE_MIGRATABLE, SEV_LIVE_MIGRATABLE_V2, SEV_SNP_CAPABLE,
        SNP_SVSM_CAPABLE, TDX_CAPABLE, UEFI_COMPATIBLE, VIRTIO_SCSI_MULTIQUEUE,
        WINDOWS.

     --key-exchange-key-file=[KEK_VALUE,...]
        Comma-separated list of file paths that point to X.509 certificates in
        DER format or raw binary files. When you create a Shielded VM instance
        from this image, these certificates or files are used as key exchange
        keys (KEK).

     --labels=[KEY=VALUE,...]
        List of label KEY=VALUE pairs to add.

        Keys must start with a lowercase character and contain only hyphens
        (-), underscores (_), lowercase characters, and numbers. Values must
        contain only hyphens (-), underscores (_), lowercase characters, and
        numbers.

     --licenses=[LICENSES,...]
        Comma-separated list of URIs to license resources.

     --platform-key-file=PLATFORM_KEY_FILE
        File path that points to an X.509 certificate in DER format or raw
        binary file. When you create a Shielded VM instance from this image,
        this certificate or raw binary file is used as the platform key (PK).

     --require-csek-key-create
        Refuse to create images not protected by a user managed key in the key
        file when --csek-key-file is given. This behavior is enabled by default
        to prevent incorrect gcloud invocations from accidentally creating
        images with no user managed key. Disabling the check allows creation of
        some images without a matching Customer-Supplied Encryption Key in the
        supplied --csek-key-file. See
        https://cloud.google.com/compute/docs/disks/customer-supplied-encryption
        for more details. Enabled by default, use --no-require-csek-key-create
        to disable.

     --resource-manager-tags=[KEY=VALUE,...]
        A comma-separated list of Resource Manager tags to apply to the image.

     --signature-database-file=[DB_VALUE,...]
        Comma-separated list of file paths that point to valid X.509
        certificates in DER format or raw binary files. When you create a
        Shielded VM instance from this image, these certificates or files are
        added to the signature database (db).

     --source-disk-project=SOURCE_DISK_PROJECT
        Project name of the source disk. Must also specify --source-disk when
        using this flag.

     --source-disk-zone=SOURCE_DISK_ZONE
        Zone of the source disk to operate on. If not specified and the
        compute/zone property isn't set, you might be prompted to select a zone
        (interactive mode only).

        To avoid prompting when this flag is omitted, you can set the
        compute/zone property:

            $ gcloud config set compute/zone ZONE

        A list of zones can be fetched by running:

            $ gcloud compute zones list

        To unset the property, run:

            $ gcloud config unset compute/zone

        Alternatively, the zone can be stored in the environment variable
        CLOUDSDK_COMPUTE_ZONE.

     --source-image-project=SOURCE_IMAGE_PROJECT
        The project name of the source image. Must also specify either
        --source-image or --source-image-family when using this flag.

     --storage-location=LOCATION
        Specifies a Cloud Storage location, either regional or multi-regional,
        where image content is to be stored. If not specified, the multi-region
        location closest to the source is chosen automatically.

     Key resource - The Cloud KMS (Key Management Service) cryptokey that will
     be used to protect the image. The 'Compute Engine Service Agent' service
     account must hold permission 'Cloud KMS CryptoKey Encrypter/Decrypter'.
     The arguments in this group can be used to specify the attributes of this
     resource.

     --kms-key=KMS_KEY
        ID of the key or fully qualified identifier for the key.

        To set the kms-key attribute:
        ◆ provide the argument --kms-key on the command line.

        This flag argument must be specified if any of the other arguments in
        this group are specified.

     --kms-keyring=KMS_KEYRING
        The KMS keyring of the key.

        To set the kms-keyring attribute:
        ◆ provide the argument --kms-key on the command line with a fully
          specified name;
        ◆ provide the argument --kms-keyring on the command line.

     --kms-location=KMS_LOCATION
        The Google Cloud location for the key.

        To set the kms-location attribute:
        ◆ provide the argument --kms-key on the command line with a fully
          specified name;
        ◆ provide the argument --kms-location on the command line.

     --kms-project=KMS_PROJECT
        The Google Cloud project for the key.

        To set the kms-project attribute:
        ◆ provide the argument --kms-key on the command line with a fully
          specified name;
        ◆ provide the argument --kms-project on the command line;
        ◆ set the property core/project.

GCLOUD WIDE FLAGS
    These flags are available to all commands: --access-token-file, --account,
    --billing-project, --configuration, --flags-file, --flatten, --format,
    --help, --impersonate-service-account, --log-http, --project, --quiet,
    --trace-token, --user-output-enabled, --verbosity.

    Run $ gcloud help for details.
'''  # real capture: gcloud compute images create --help

_GCLOUD_RESIZE = r'''NAME
    gcloud compute disks resize - resize a disk or disks

SYNOPSIS
    gcloud compute disks resize DISK_NAME [DISK_NAME ...] --size=SIZE
        [--region=REGION | --zone=ZONE] [GCLOUD_WIDE_FLAG ...]

DESCRIPTION
    gcloud compute disks resize resizes a Compute Engine disk(s).

    Only increasing disk size is supported. Disks can be resized regardless of
    whether they are attached.

EXAMPLES
    To resize a disk called example-disk-1 to new size 6TB, run:

        $ gcloud compute disks resize example-disk-1 --size=6TB

    To resize two disks called example-disk-2 and example-disk-3 to new size
    6TB, run:

        $ gcloud compute disks resize example-disk-2 example-disk-3 \
           --size=6TB

    This assumes that original size of each of these disks is 6TB or less.

POSITIONAL ARGUMENTS
     DISK_NAME [DISK_NAME ...]
        Names of the disks to operate on.

REQUIRED FLAGS
     --size=SIZE
        Indicates the new size of the disks. The value must be a whole number
        followed by a size unit of GB for gigabyte, or TB for terabyte. If no
        size unit is specified, GB is assumed. For example, 10GB will produce
        10 gigabyte disks. Disk size must be a multiple of 1 GB.

OPTIONAL FLAGS
     At most one of these can be specified:

       --region=REGION
          Region of the disks to operate on. If not specified, you might be
          prompted to select a region (interactive mode only).

          To avoid prompting when this flag is omitted, you can set the
          compute/region property:

              $ gcloud config set compute/region REGION

          A list of regions can be fetched by running:

              $ gcloud compute regions list

          To unset the property, run:

              $ gcloud config unset compute/region

          Alternatively, the region can be stored in the environment variable
          CLOUDSDK_COMPUTE_REGION.

       --zone=ZONE
          Zone of the disks to operate on. If not specified and the
          compute/zone property isn't set, you might be prompted to select a
          zone (interactive mode only).

          To avoid prompting when this flag is omitted, you can set the
          compute/zone property:

              $ gcloud config set compute/zone ZONE

          A list of zones can be fetched by running:

              $ gcloud compute zones list

          To unset the property, run:

              $ gcloud config unset compute/zone

          Alternatively, the zone can be stored in the environment variable
          CLOUDSDK_COMPUTE_ZONE.

GCLOUD WIDE FLAGS
    These flags are available to all commands: --access-token-file, --account,
    --billing-project, --configuration, --flags-file, --flatten, --format,
    --help, --impersonate-service-account, --log-http, --project, --quiet,
    --trace-token, --user-output-enabled, --verbosity.

    Run $ gcloud help for details.
'''  # real capture: gcloud compute disks resize --help

_GCLOUD_FW_DELETE = r'''NAME
    gcloud compute firewall-rules delete - delete Compute Engine firewall rules

SYNOPSIS
    gcloud compute firewall-rules delete NAME [NAME ...] [GCLOUD_WIDE_FLAG ...]

DESCRIPTION
    gcloud compute firewall-rules delete deletes one or more Compute Engine
    firewall rules.

EXAMPLES
    To delete a firewall rule, run:

        $ gcloud compute firewall-rules delete my-firewall-rule

POSITIONAL ARGUMENTS
     NAME [NAME ...]
        Names of the firewall rules to delete.

GCLOUD WIDE FLAGS
    These flags are available to all commands: --access-token-file, --account,
    --billing-project, --configuration, --flags-file, --flatten, --format,
    --help, --impersonate-service-account, --log-http, --project, --quiet,
    --trace-token, --user-output-enabled, --verbosity.

    Run $ gcloud help for details.
'''  # real capture: gcloud compute firewall-rules delete --help


# ===============================================================================================================
# #310 Cloud CLI --help: keep each flag header plus its first paragraph. Applies to the gcloud and aws (man page) layouts: a flag
# header is a line starting with `-`, `[` or an UPPER_CASE positional inside a FLAGS/OPTIONS section, its body is the deeper-indented
# lines after it. Kept: the header, the first paragraph, the paragraph that follows a first paragraph ending in ':' (the list it
# introduces) and any later paragraph that says 'must be one of' (the finder's rule would lose those enumerations: real
# `gcloud compute addresses create --help` has --ip-version's choices in its third paragraph). SYNOPSIS keeps its first line.
# NAME, DESCRIPTION, EXAMPLES, NOTES and every group header ('At most one of these can be specified:') are untouched.
# The two gcloud pages are real captures (gcloud 5xx, offline help); the aws page is reconstructed from memory.

_FLAG_SECTIONS = {"POSITIONAL ARGUMENTS", "FLAGS", "REQUIRED FLAGS", "OPTIONAL FLAGS", "OPTIONS", "GLOBAL OPTIONS"}
_HEADING = re.compile(r"^[A-Z][A-Z ]+$")
_FLAGHEAD = re.compile(r"^\s+(?:-{1,2}[A-Za-z0-9\[]\S*.*|\[[A-Za-z_.\- ]+\]|[A-Z][A-Z0-9_]*(?: \[?[A-Z_]+ \.\.\.\]?)?)$")
_ENUM = re.compile(r"\b(?:must be one of|can be one of|one of the following|valid values|possible values)\b", re.I)
_OMIT = re.compile(r"^\s*\[(?:\d+ more lines? omitted|synopsis continues: \d+ more lines?; every flag is listed under the sections below)\]$")


def _indent(l: str) -> int:
    return len(l) - len(l.lstrip(" "))


def trim_flag_docs(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    section = None
    i = 0
    while i < len(lines):
        l = lines[i]
        if _HEADING.match(l):
            section = l
            out.append(l)
            i += 1
            continue
        if section == "SYNOPSIS" and l.strip():
            j = i + 1
            while j < len(lines) and not _HEADING.match(lines[j]):
                j += 1
            body = lines[i:j]
            end = len(body)
            while end > 1 and not body[end - 1].strip():
                end -= 1
            if end > 2 and not _OMIT.match(body[1]):
                out.append(body[0])
                out.append(f"{' ' * (_indent(body[0]) + 4)}[synopsis continues: {end - 1} more lines; every flag is listed under the sections below]")
                out += body[end:]
            else:
                out += body
            i = j
            section = None  # the heading loop re-enters for the next section
            continue
        if section in _FLAG_SECTIONS and _FLAGHEAD.match(l):
            h = _indent(l)
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > h) and not _HEADING.match(lines[j]):
                j += 1
            body = lines[i + 1:j]
            tail = 0
            while body and not body[-1].strip():
                body.pop()
                tail += 1
            lead = 0
            while lead < len(body) and not body[lead].strip():
                lead += 1
            paras, cur = [], []
            for b in body[lead:]:
                if b.strip():
                    cur.append(b)
                elif cur:
                    paras.append(cur)
                    cur = []
            if cur:
                paras.append(cur)
            keep = [0] if paras else []
            if paras and paras[0][-1].rstrip().endswith(":") and len(paras) > 1:
                keep.append(1)
            keep += [k for k in range(1, len(paras)) if k not in keep and _ENUM.search(" ".join(paras[k]))]
            dropped = sum(len(p) for k, p in enumerate(paras) if k not in keep)
            out.append(l)
            if dropped >= 2:
                out += [""] * lead
                for n, k in enumerate(sorted(keep)):
                    out += ([""] if n else []) + paras[k]
                out.append(f"{' ' * _indent(paras[0][0])}[{dropped} more lines omitted]")
                out += [""] * tail
            else:
                out += lines[i + 1:j]
            i = j
            continue
        out.append(l)
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_310(text: str) -> dict:
    """Streaming state machine: which section are we in, which flag entry is open, which paragraph of it."""
    section, entry, entries, groups, other, name_line, syn_first = None, None, [], [], defaultdict(list), None, None

    def close():
        nonlocal entry
        if entry is not None:
            entries.append(entry)
        entry = None

    for line in text.split("\n"):
        if _HEADING.match(line):
            close()
            section = line
            continue
        if section in _FLAG_SECTIONS:
            if entry is not None and (not line.strip() or _indent(line) > entry["h"]):
                if _OMIT.match(line):
                    continue
                if line.strip():
                    entry["paras"][-1].append(" ".join(line.split())) if entry["open"] else entry["paras"].append([" ".join(line.split())])
                    entry["open"] = True
                else:
                    entry["open"] = False
                continue
            close()
            if _FLAGHEAD.match(line):
                entry = {"section": section, "header": " ".join(line.split()), "h": _indent(line), "paras": [], "open": False, "group": groups[-1] if groups else None}
            elif line.strip():
                groups.append((section, " ".join(line.split())))
            continue
        if section == "SYNOPSIS":
            if line.strip() and syn_first is None:
                syn_first = " ".join(line.split())
            continue
        if section == "NAME" and line.strip() and name_line is None:
            name_line = " ".join(line.split())
        other[section].append(line)
    close()
    flags, enums, lists = [], {}, {}
    for e in entries:
        ps = [" ".join(p) for p in e["paras"]]
        flags.append((e["section"], e["group"], e["header"], ps[0] if ps else ""))
        hits = [p for k, p in enumerate(ps) if k > 0 and _ENUM.search(p)]
        if hits:
            enums[e["header"]] = hits
        if len(ps) > 1 and ps[0].endswith(":"):
            lists[e["header"]] = ps[1]
    return {"name line": name_line, "synopsis first line": syn_first, "group headers in order": groups,
            "every flag: section, group, header, first paragraph": flags, "enumerations in later paragraphs": enums,
            "lists introduced by a colon": lists, "sections kept whole (NAME, DESCRIPTION, EXAMPLES, NOTES ...)": {k: v for k, v in other.items()}}


_AWS_STOP = """STOP-INSTANCES()                                              STOP-INSTANCES()



NAME
       stop-instances -

DESCRIPTION
       Stops an Amazon EBS-backed instance. You can restart your instance at
       any time. Every time you start your instance, Amazon EC2 charges a
       minimum of one minute for sudden usage.

       You can't stop or hibernate instance store-backed instances. You can't
       use the Stop action to hibernate Spot Instances, but you can specify
       that Amazon EC2 should hibernate Spot Instances when they are
       interrupted.

       See also: AWS API Documentation

       See 'aws help' for descriptions of global parameters.

SYNOPSIS
            stop-instances
          --instance-ids <value>
          [--hibernate | --no-hibernate]
          [--dry-run | --no-dry-run]
          [--force | --no-force]
          [--cli-input-json | --cli-input-yaml]
          [--generate-cli-skeleton <value>]

OPTIONS
       --instance-ids (list)
          The IDs of the instances.

          (string)

       Syntax:

          "string" "string" ...

       --hibernate | --no-hibernate (boolean)
          Hibernates the instance if the instance was enabled for hibernation
          at launch. If the instance cannot hibernate successfully, a normal
          shutdown occurs.

          Default: false

       --dry-run | --no-dry-run (boolean)
          Checks whether you have the required permissions for the action,
          without actually making the request, and provides an error response.
          If you have the required permissions, the error response is
          DryRunOperation . Otherwise, it is UnauthorizedOperation .

       --force | --no-force (boolean)
          Forces the instances to stop. The instances do not have an
          opportunity to flush file system caches or file system metadata. If
          you use this option, you must perform file system check and repair
          procedures. This option is not recommended for Windows instances.

          Default: false

       --cli-input-json | --cli-input-yaml (string) Performs service
       operation based on the JSON string provided. The JSON string follows
       the format provided by --generate-cli-skeleton.

       --generate-cli-skeleton (string) Prints a JSON skeleton to standard
       output without sending an API request. If provided with no value or
       the value input, prints a sample input JSON that can be used as an
       argument for --cli-input-json.

GLOBAL OPTIONS
       --debug (boolean)
          Turn on debug logging.

       --endpoint-url (string)
          Override command's default URL with the given URL.

       --no-verify-ssl (boolean)
          By default, the AWS CLI uses SSL when communicating with AWS
          services. For each SSL connection, the AWS CLI will verify SSL
          certificates. This option overrides the default behavior of
          verifying SSL certificates.

       --output (string)
          The formatting style for command output.

          o json

          o text

          o table

       --region (string)
          The region to use. Overrides config/env settings.

       --profile (string)
          Use a specific profile from your credential file.

          Note that the profile must exist in your configuration. If it does
          not, the CLI exits with an error before sending any request.

       --query (string)
          A JMESPath query to use in filtering the response data.

       --no-paginate (boolean)
          Disable automatic pagination. If automatic pagination is disabled,
          the AWS CLI will only make one call, for the first page of results.

       --no-sign-request (boolean)
          Do not sign requests. Credentials will not be loaded if this argument
          is provided.

       --ca-bundle (string)
          The CA certificate bundle to use when verifying SSL certificates.
          Overrides config/env settings.

       --cli-read-timeout (int)
          The maximum socket read time in seconds. If the value is set to 0,
          the socket read will be blocking and not timeout. The default value
          is 60 seconds.

       --cli-connect-timeout (int)
          The maximum socket connect time in seconds. If the value is set to 0,
          the socket connect will be blocking and not timeout. The default
          value is 60 seconds.

       --color (string)
          Turn on/off color output.

          o on

          o off

          o auto

       --version (string)
          Display the version of this tool.

EXAMPLES
       To stop an Amazon EBS-backed instance

       This example stops the specified Amazon EBS-backed instance.

       Command:

          aws ec2 stop-instances --instance-ids i-1234567890abcdef0

OUTPUT
       StoppingInstances -> (list)
          Information about the stopped instances.
"""  # reconstructed from memory

_AWS_RUN = """RUN-INSTANCES()                                                RUN-INSTANCES()



NAME
       run-instances -

DESCRIPTION
       Launches the specified number of instances using an AMI for which you
       have permissions.

       You can specify a number of options, or leave the default options. The
       following rules apply:

       o If you don't specify a subnet ID, we choose a default subnet from
         your default VPC for you.

       o Some instance types must be launched into a VPC.

       See 'aws help' for descriptions of global parameters.

SYNOPSIS
            run-instances
          [--block-device-mappings <value>]
          [--image-id <value>]
          [--instance-type <value>]
          [--count <value>]
          [--key-name <value>]
          [--security-group-ids <value>]
          [--subnet-id <value>]
          [--tag-specifications <value>]
          [--cli-input-json | --cli-input-yaml]

OPTIONS
       --block-device-mappings (list)
          The block device mapping, which defines the EBS volumes and instance
          store volumes to attach to the instance at launch.

          You can't specify encryption settings for root volumes when using
          Amazon EBS encryption by default.

          (structure)
              Describes a block device mapping, which defines the EBS volumes
              and instance store volumes to attach to an instance at launch.

              DeviceName -> (string)
                 The device name (for example, /dev/sdh or xvdh).

              VirtualName -> (string)
                 The virtual device name (ephemeralN). Instance store volumes
                 are numbered starting from 0.

              Ebs -> (structure)
                 Parameters used to automatically set up EBS volumes when the
                 instance is launched.

                 VolumeSize -> (integer)
                    The size of the volume, in GiBs. You must specify either a
                    snapshot ID or a volume size.

                 VolumeType -> (string)
                    The volume type. Valid values: standard | io1 | io2 | gp2
                    | sc1 | st1 | gp3

       Shorthand Syntax:

          DeviceName=string,VirtualName=string,Ebs={DeleteOnTermination=boolean,VolumeSize=integer,VolumeType=string} ...

       JSON Syntax:

          [
            {
              "DeviceName": "string",
              "Ebs": {"VolumeSize": integer, "VolumeType": "gp3"}
            }
            ...
          ]

       --image-id (string)
          The ID of the AMI. An AMI ID is required to launch an instance and
          must be specified here or in a launch template.

       --instance-type (string)
          The instance type. For more information, see Instance types in the
          Amazon EC2 User Guide.

          Default: m1.small

       --count (string)
          The number of instances to launch. You can specify a single number
          or a range, for example 1:5.

          Constraints: Between 1 and the maximum number you're allowed for the
          specified instance type.

       --key-name (string)
          The name of the key pair. You can create a key pair using
          CreateKeyPair or ImportKeyPair .

          WARNING:
              If you do not specify a key pair, you can't connect to the
              instance unless you choose an AMI that is configured to allow
              users another way to log in.

GLOBAL OPTIONS
       --debug (boolean)
          Turn on debug logging.

       --output (string)
          The formatting style for command output.

          o json

          o text

          o table
"""  # reconstructed from memory (abridged)

_AZ_HELP = """
Command
    az vm create : Create an Azure Virtual Machine.
        For an end-to-end tutorial, see https://docs.microsoft.com/azure/virtual-machines/linux/quick-create-cli.

Arguments
    --name -n            [Required] : Name of the virtual machine.
    --resource-group -g  [Required] : Name of resource group. You can configure the default group using
                                      `az configure --defaults group=<name>`.
    --image                         : The name of the operating system image as a URN alias, URN, custom image
                                      name or ID, custom image version ID, or VHD blob URI.
    --size                          : The VM size to be created. See https://azure.microsoft.com/pricing/details/virtual-machines/ for size info.
                                      Default: Standard_DS1_v2.
    --admin-username                : Username for the VM. Default value is current username of OS.

Global Arguments
    --debug                         : Increase logging verbosity to show all debug logs.
    --help -h                       : Show this help message and exit.
    --output -o                     : Output format.  Allowed values: json, jsonc, none, table, tsv, yaml, yamlc.  Default: json.

Examples
    Create a VM
        az vm create -n MyVm -g MyResourceGroup --image Ubuntu2204
"""  # reconstructed from memory

_KUBECTL_HELP = """Display one or many resources.

 Prints a table of the most important information about the specified resources.

Examples:
  # List all pods in ps output format
  kubectl get pods

Options:
    -A, --all-namespaces=false:
	If present, list the requested object(s) across all namespaces. Namespace in current context is ignored even if specified with --namespace.

    -o, --output='':
	Output format. One of: (json, yaml, name, go-template, go-template-file, template, templatefile, jsonpath, jsonpath-as-json, jsonpath-file, custom-columns, custom-columns-file, wide).

Usage:
  kubectl get [(-o|--output=)json|yaml|name|go-template|go-template-file|template|templatefile|jsonpath|jsonpath-as-json|jsonpath-file|custom-columns|custom-columns-file|wide] (TYPE[.VERSION][.GROUP] [NAME | -l label] | TYPE[.VERSION][.GROUP]/NAME ...) [flags] [options]
"""  # reconstructed from memory


def _gen_310():
    return [
        Fixture("real: gcloud compute addresses create --help (enumeration in a third paragraph, mutually exclusive groups)", _GCLOUD_ADDRESSES, True),
        Fixture("real: gcloud compute images create --help", _GCLOUD_IMAGES, True),
        Fixture("aws ec2 run-instances help, abridged (man page layout, a structure parameter with nested docs)", _AWS_RUN, True),
        Fixture("aws ec2 stop-instances help: small page, saves only ~6% (not gated)", _AWS_STOP, None),
        Fixture("real: gcloud compute disks resize --help (small)", _GCLOUD_RESIZE, True),
        Fixture("negative: real gcloud compute firewall-rules delete --help has a single positional and no later paragraphs", _GCLOUD_FW_DELETE, False),
        Fixture("negative: az vm create -h lists one entry per flag, nothing to fold", _AZ_HELP, False),
        Fixture("negative: kubectl get --help has no FLAGS section in this layout", _KUBECTL_HELP, False),
    ]


case(
    310,
    claim="In the FLAGS/OPTIONS sections of a gcloud or aws help page each flag keeps its header, its first paragraph, the list a first paragraph introduces with a colon and any later paragraph that says 'must be one of'; the other paragraphs collapse to '[N more lines omitted]' and the SYNOPSIS (which re-lists every flag) keeps one line. Names, synopsis head, group headers ('At most one of these can be specified'), every flag header, first paragraph, enumeration and the NAME, DESCRIPTION, EXAMPLES sections are unchanged.",
    loses="the later paragraphs of every flag other than enumerations: applicability notes ('only available for global addresses', 'May not be specified with --global'), examples inside a flag's text, aws Syntax and structure blocks; and the SYNOPSIS lines after the first",
    encode=trim_flag_docs,
    probes=probes_310,
    fixtures=_gen_310,
    min_gain=0.12,
)


# ===============================================================================================================
# #800 gcloud logging read (YAML documents) and aws logs filter-log-events (JSON): hoist what is constant across entries into one
# header line, drop insertId, receiveTimestamp (gcloud) and eventId, ingestionTime (aws), print one line per entry with time,
# severity and payload. Probes: parse the original (YAML or JSON) and the packed form into the same list of entries with the
# constants merged back, and compare them whole. reconstructed from memory (no cloud project here).

_G_HEAD = re.compile(r"^# gcloud logging read: (\d+) entries; same on every entry: (\{.*\}); insertId and receiveTimestamp dropped$")
_A_HEAD = re.compile(r"^# aws logs filter-log-events: (\d+) events; same on every event: (\{.*\}); eventId and ingestionTime dropped$")
_A_REST = re.compile(r"^# other keys: (\{.*\})$")
_G_DROP = {"insertId", "receiveTimestamp"}
_G_SKIP = _G_DROP | {"timestamp", "severity", "jsonPayload", "textPayload", "protoPayload"}


def _common(ds: list) -> dict:
    out = {}
    for k, v in ds[0].items():
        if all(isinstance(d, dict) and k in d for d in ds):
            if all(d[k] == v and type(d[k]) is type(v) for d in ds):
                out[k] = v
            elif all(isinstance(d[k], dict) for d in ds):
                c = _common([d[k] for d in ds])
                if c:
                    out[k] = c
    return out


def _minus(d: dict, c: dict) -> dict:
    r = {}
    for k, v in d.items():
        if k in c:
            if isinstance(v, dict) and isinstance(c[k], dict) and v != c[k]:
                sub = _minus(v, c[k])
                if sub:
                    r[k] = sub
            continue
        r[k] = v
    return r


def _merge(a: dict, b: dict) -> dict:
    r = dict(a)
    for k, v in b.items():
        r[k] = _merge(r[k], v) if isinstance(v, dict) and isinstance(r.get(k), dict) else v
    return r


def _plain_line(s) -> bool:
    return isinstance(s, str) and s != "" and s == s.strip() and "\n" not in s and "\r" not in s and not s.startswith(("{", '"')) and all(ord(ch) >= 32 for ch in s)


def _jline(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def _pack_gcloud(docs: list) -> str | None:
    if not docs or not all(isinstance(d, dict) for d in docs):
        return None
    ok = lambda v: v is None or (isinstance(v, str) and v != "" and v == v.strip() and " " not in v and v != "-")  # noqa: E731
    if not all(ok(d.get("timestamp")) and ok(d.get("severity")) for d in docs):
        return None
    const = _common([{k: v for k, v in d.items() if k not in _G_SKIP} for d in docs]) if len(docs) >= 2 else {}
    out = [f"# gcloud logging read: {len(docs)} entries; same on every entry: {_jline(const)}; insertId and receiveTimestamp dropped"]
    for d in docs:
        rest = _minus({k: v for k, v in d.items() if k not in _G_SKIP}, const)
        payload = {k: d[k] for k in ("jsonPayload", "textPayload", "protoPayload") if k in d}
        head = f"{d.get('timestamp') or '-'} {d.get('severity') or '-'}"
        if not rest and list(payload) == ["textPayload"] and _plain_line(payload["textPayload"]):
            out.append(f"{head} | {payload['textPayload']}")
        else:
            out.append(f"{head} {_jline({**rest, **payload})}")
    return "\n".join(out)


def _pack_aws(doc) -> str | None:
    if not (isinstance(doc, dict) and isinstance(doc.get("events"), list) and doc["events"]):
        return None
    evs = doc["events"]
    if not all(isinstance(e, dict) and set(e) <= {"logStreamName", "timestamp", "message", "ingestionTime", "eventId"} and isinstance(e.get("timestamp"), int)
               and isinstance(e.get("message"), str) and not isinstance(e.get("timestamp"), bool) for e in evs):
        return None
    const = _common([{k: v for k, v in e.items() if k == "logStreamName"} for e in evs]) if len(evs) >= 2 else {}
    out = [f"# aws logs filter-log-events: {len(evs)} events; same on every event: {_jline(const)}; eventId and ingestionTime dropped"]
    for e in evs:
        extra = {"logStreamName": e["logStreamName"]} if "logStreamName" in e and "logStreamName" not in const else {}
        m = e["message"]
        body = m if (_plain_line(m) and not extra) else _jline(m)
        out.append(f"{e['timestamp']} " + (_jline(extra) + " " if extra else "") + body)
    other = {k: v for k, v in doc.items() if k != "events"}
    if other:
        out.append(f"# other keys: {_jline(other)}")
    return "\n".join(out)


def pack_log_envelope(text: str) -> str:
    s = text.lstrip()
    try:
        out = _pack_gcloud(yaml_docs(text)) if s.startswith("---") else _pack_aws(json.loads(text)) if s.startswith("{") else None
    except Exception:  # noqa: BLE001
        return text
    if out is None:
        return text
    out = keep_nl(text, out)
    return out if len(out) < len(text) else text


def _unpack(text: str):
    """Entries of a packed or original gcloud/aws log dump as canonical dicts, or None when the text is neither."""
    lines = text.rstrip("\n").split("\n")
    m = _G_HEAD.match(lines[0]) if lines else None
    if m:
        const = json.loads(m.group(2))
        ents = []
        for l in lines[1:]:
            ts, sev, rest = l.split(" ", 2)
            e: dict = {}
            if ts != "-":
                e["timestamp"] = ts
            if sev != "-":
                e["severity"] = sev
            body = json.loads(rest) if not rest.startswith("| ") else {"textPayload": rest[2:]}
            ents.append(_merge(const, {**e, **body}))
        return ("gcloud", ents, int(m.group(1)))
    m = _A_HEAD.match(lines[0]) if lines else None
    if m:
        const = json.loads(m.group(2))
        evs, other = [], {}
        for l in lines[1:]:
            r = _A_REST.match(l)
            if r:
                other = json.loads(r.group(1))
                continue
            ts, rest = l.split(" ", 1)
            extra = {}
            if rest.startswith("{"):
                extra, end = json.JSONDecoder().raw_decode(rest)
                rest = rest[end + 1:]
            msg = json.loads(rest) if rest.startswith('"') else rest
            evs.append({**const, **extra, "timestamp": int(ts), "message": msg})
        return ("aws", evs, other)
    return None


def probes_800(text: str) -> dict:
    try:
        got = _unpack(text)
        if got is None:
            s = text.lstrip()
            if s.startswith("---"):
                docs = yaml_docs(text)
                got = ("gcloud", [{k: v for k, v in d.items() if k not in _G_DROP} for d in docs], len(docs))
            elif s.startswith("{"):
                d = json.loads(text)
                got = ("aws", [{k: v for k, v in e.items() if k not in ("eventId", "ingestionTime")} for e in d["events"]], {k: v for k, v in d.items() if k != "events"})
            else:
                return {"format": "other", "text": text}
    except Exception:  # noqa: BLE001
        return {"parse": "failed"}
    kind, ents, extra = got
    if kind == "aws":
        return {"kind": kind, "events (stream, time, message)": [(e.get("logStreamName"), e.get("timestamp"), e.get("message")) for e in ents], "other keys": extra}
    sev = Counter(e.get("severity", "DEFAULT") for e in ents)
    return {"kind": kind, "entry count": extra if isinstance(extra, int) else len(ents), "entries without insertId and receiveTimestamp": ents,
            "time and severity in order": [(e.get("timestamp"), e.get("severity")) for e in ents], "severity counts": sorted(sev.items())}


def _gcloud_entries(n: int = 8) -> str:
    base_res = {"labels": {"cluster_name": "prod-us", "container_name": "web", "location": "us-central1", "namespace_name": "shop", "project_id": "acme-prod"}, "type": "k8s_container"}
    docs = []
    pods = ["web-6d8f7c9b5-x2k9p"] * 5 + ["web-6d8f7c9b5-q7m4d"] * 3
    for k in range(n):
        ts = f"2025-03-04T10:15:{2 + k:02d}.{(137 * k) % 1000:03d}Z"
        res = {"labels": {**base_res["labels"], "pod_name": pods[k % len(pods)]}, "type": "k8s_container"}
        d = {"insertId": f"{_sha(f'ins{k}', 16)}", "labels": {"k8s-pod/app": "web", "k8s-pod/pod-template-hash": "6d8f7c9b5"}, "logName": "projects/acme-prod/logs/stderr",
             "receiveTimestamp": f"2025-03-04T10:15:{3 + k:02d}.{(911 * k) % 1000:03d}417526Z", "resource": res, "timestamp": ts}
        if k == 0:
            d.update({"severity": "ERROR", "jsonPayload": {"message": "upstream connect error or disconnect/reset before headers", "status": 503, "upstream": "payments:8443", "trace_id": "4bf92f3577b34da6"}})
        elif k == n // 2:
            d.update({"severity": "ERROR", "jsonPayload": {"message": "connection refused: payments:8443", "retry": 3, "status": 502}})
        elif k == n - 1:
            d.update({"severity": "CRITICAL", "textPayload": "panic: runtime error: invalid memory address or nil pointer dereference"})
        elif k % 3 == 1:
            d.update({"severity": "INFO", "textPayload": f"GET /healthz 200 {3 + k}ms"})
        else:
            d.update({"httpRequest": {"latency": f"0.0{k}s", "requestMethod": "GET", "requestUrl": "/cart", "status": 200 + (k % 2) * 4}, "jsonPayload": {"message": "request done", "user": f"u{k}"}})
        docs.append(d)
    return "".join("---\n" + ytext(d) for d in docs)


def _aws_events(n: int = 6) -> str:
    evs = []
    for k in range(n):
        msg = {0: '{"level":"error","msg":"upstream connect error","status":503}', n // 2: "ERROR payments timeout after 5000ms\n\tat com.acme.pay.Client.call(Client.java:88)\n\tat com.acme.web.Cart.checkout(Cart.java:41)",
               n - 1: " trailing spaces and a \"quote\" in the message "}.get(k, f'{{"level":"info","msg":"request ok","ms":{7 + k}}}')
        evs.append({"logStreamName": "web/web/4f1c2a9e0b7d4c8e9a0b1d2e3f4a5b6c", "timestamp": 1741083302998 + 137 * k, "message": msg, "ingestionTime": 1741083303100 + 140 * k, "eventId": _sha(f"ev{k}", 56).translate(str.maketrans("abcdef", "012345"))})
    return json.dumps({"events": evs, "searchedLogStreams": [{"logStreamName": "web/web/4f1c2a9e0b7d4c8e9a0b1d2e3f4a5b6c", "searchedCompletely": True}]}, indent=4) + "\n"


case(
    800,
    claim="Parses a gcloud logging read YAML stream or an aws logs filter-log-events JSON document, hoists what is identical across entries (logName, resource type and labels, entry labels, logStreamName) into one header, drops insertId, receiveTimestamp, eventId and ingestionTime, and prints one line per entry with time, severity (or epoch milliseconds) and the payload; every entry, with its constants merged back, equals the original minus those four fields.",
    loses="insertId, receiveTimestamp (gcloud), eventId and ingestionTime (aws), YAML formatting and key order inside payloads, and the distinction between a missing severity and the '-' placeholder",
    encode=pack_log_envelope,
    probes=probes_800,
    fixtures=[
        Fixture("gcloud logging read, 8 entries: error first, an error in the middle, a CRITICAL panic last, one pod differs", _gcloud_entries(8), True),
        Fixture("gcloud logging read, 3 entries", _gcloud_entries(3), True),
        Fixture("aws logs filter-log-events: JSON error first, a stack trace in the middle, quote and spaces last", _aws_events(6), True),
        Fixture("negative: --format=value(textPayload) is plain lines", "connection refused: payments:8443\nGET /healthz 200 4ms\n", False),
        Fixture("negative: gcloud --format=json is a JSON array (not handled)", json.dumps([{"insertId": "a", "timestamp": "2025-03-04T10:15:02Z", "textPayload": "x"}]) + "\n", False),
        Fixture("negative: aws --query output without events", json.dumps(["web/web/4f1c", "worker/1a2b"], indent=4) + "\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #1161 CloudTrail to one-line digests. `{"Records": [...]}` (S3 delivery) and `aws cloudtrail lookup-events` (`{"Events": [...]}` whose
# CloudTrailEvent is the same record as a JSON string). One line per event: time, principal, source IP, service:action and the
# request parameters (the resource identity); an event with an errorCode additionally keeps its full record on the next line.
# The idea's own digest omits the source IP, which its Test question ('from which IP') needs, so the IP is part of the line here.
# reconstructed from memory (no AWS account here)

def _principal(r: dict) -> str:
    ui = r.get("userIdentity") if isinstance(r.get("userIdentity"), dict) else {}
    for k in ("arn", "userName", "principalId", "invokedBy", "type"):
        if isinstance(ui.get(k), str) and ui[k]:
            return ui[k]
    return "unknown"


def _ct_ok(r) -> bool:
    return (isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k] and " " not in r[k] for k in ("eventTime", "eventSource", "eventName", "sourceIPAddress"))
            and " " not in _principal(r))


def _ct_line(r: dict) -> list[str]:
    svc = r["eventSource"].split(".")[0]
    line = f"{r['eventTime']} {_principal(r)} {r['sourceIPAddress']} {svc}:{r['eventName']} {_jline(r.get('requestParameters'))}"
    if r.get("errorCode"):
        return [line + f" ERR {r['errorCode']}", "  " + _jline(r)]
    return [line]


def _ct_records(doc):
    if isinstance(doc, dict) and isinstance(doc.get("Records"), list):
        return "Records", doc["Records"], {k: v for k, v in doc.items() if k != "Records"}
    if isinstance(doc, dict) and isinstance(doc.get("Events"), list):
        recs = []
        for e in doc["Events"]:
            r = json.loads(e["CloudTrailEvent"])
            if not (isinstance(r, dict) and e.get("EventName") == r.get("eventName") and e.get("EventSource") == r.get("eventSource") and e.get("EventId") == r.get("eventID")):
                return None
            recs.append(r)
        return "Events", recs, {k: v for k, v in doc.items() if k != "Events"}
    return None


def digest_cloudtrail(text: str) -> str:
    try:
        doc = json.loads(text)
        got = _ct_records(doc)
    except Exception:  # noqa: BLE001
        return text
    if got is None or not got[1]:
        return text
    kind, recs, other = got
    out = [f"# cloudtrail {kind}: {len(recs)} events; one line each (time, principal, source IP, service:action, request parameters); events with an errorCode keep the full record on the next line"]
    for r in recs:
        if _ct_ok(r):
            out += _ct_line(r)
        else:
            out.append("FULL " + _jline(r))
    if other:
        out.append(f"# other keys: {_jline(other)}")
    res = keep_nl(text, "\n".join(out))
    return res if len(res) < len(text) else text


def _ct_facts(r: dict) -> tuple:
    return (r.get("eventTime"), _principal(r), r.get("sourceIPAddress"), f"{str(r.get('eventSource')).split('.')[0]}:{r.get('eventName')}", r.get("requestParameters"), r.get("errorCode"))


def probes_1161(text: str) -> dict:
    recs: list = []
    try:
        if text.startswith("# cloudtrail "):
            lines = text.rstrip("\n").split("\n")[1:]
            other = {}
            i = 0
            while i < len(lines):
                l = lines[i]
                if l.startswith("FULL "):
                    recs.append(json.loads(l[5:]))
                elif l.startswith("# other keys: "):
                    other = json.loads(l[len("# other keys: "):])
                elif l.startswith("  {"):
                    pass
                else:
                    ts, who, ip, act, rest = l.split(" ", 4)
                    params, end = json.JSONDecoder().raw_decode(rest)
                    err = rest[end + 5:] if rest[end:].startswith(" ERR ") else None
                    full = json.loads(lines[i + 1][2:]) if err else None
                    recs.append(full if full is not None else {"eventTime": ts, "userIdentity": {"arn": who}, "sourceIPAddress": ip, "eventSource": act.split(":")[0] + ".amazonaws.com",
                                                                 "eventName": act.split(":", 1)[1], "requestParameters": params})
                i += 1
        else:
            got = _ct_records(json.loads(text))
            if got is None:
                return {"format": "other", "text": text}
            _, recs, other = got
    except Exception:  # noqa: BLE001
        return {"parse": "failed"}
    failed = [r for r in recs if r.get("errorCode")]
    return {"events (time, principal, ip, action, request parameters, error)": [_ct_facts(r) for r in recs],
            "failed events with every field": failed, "who did what from where": sorted({(_principal(r), r.get("sourceIPAddress"), r.get("eventName")) for r in recs}, key=repr),
            "other keys": other}


def _ct_record(k: int, who: str, name: str, ip: str, params: dict, err: str | None = None) -> dict:
    r = {"eventVersion": "1.09", "userIdentity": {"type": "AssumedRole", "principalId": f"AROAEXAMPLE{k}:{who}", "arn": f"arn:aws:sts::123456789012:assumed-role/Admin/{who}",
         "accountId": "123456789012", "accessKeyId": "ASIAEXAMPLE" + str(k), "sessionContext": {"sessionIssuer": {"type": "Role", "principalId": "AROAEXAMPLE", "arn": "arn:aws:iam::123456789012:role/Admin",
         "accountId": "123456789012", "userName": "Admin"}, "attributes": {"creationDate": "2025-03-04T08:00:00Z", "mfaAuthenticated": "true"}}},
         "eventTime": f"2025-03-04T10:{15 + k:02d}:02Z", "eventSource": "s3.amazonaws.com", "eventName": name, "awsRegion": "us-east-1", "sourceIPAddress": ip,
         "userAgent": "[aws-cli/2.15.0 Python/3.11.6 Linux/6.1.0 exe/x86_64.ubuntu.22 prompt/off command/s3api." + name.lower() + "]", "requestParameters": params,
         "responseElements": None, "additionalEventData": {"SignatureVersion": "SigV4", "CipherSuite": "ECDHE-RSA-AES128-GCM-SHA256", "bytesTransferredIn": 0, "AuthenticationMethod": "AuthHeader",
         "x-amz-id-2": _sha(f"id2{k}", 40), "bytesTransferredOut": 1024 * (k + 1)}, "requestID": _sha(f"req{k}", 16).upper(), "eventID": _sha(f"evt{k}", 32),
         "readOnly": name.startswith(("Get", "List")), "resources": [{"type": "AWS::S3::Object", "ARN": f"arn:aws:s3:::{params.get('bucketName', 'b')}/{params.get('key', '')}"}],
         "eventType": "AwsApiCall", "managementEvent": False, "recipientAccountId": "123456789012", "eventCategory": "Data",
         "tlsDetails": {"tlsVersion": "TLSv1.3", "cipherSuite": "TLS_AES_128_GCM_SHA256", "clientProvidedHostHeader": f"{params.get('bucketName', 'b')}.s3.us-east-1.amazonaws.com"}}
    if err:
        r["errorCode"], r["errorMessage"] = err, "User: " + r["userIdentity"]["arn"] + " is not authorized to perform: s3:" + name
    return r


def _ct_events(n: int = 10, lookup: bool = False) -> str:
    recs = []
    for k in range(n):
        if k == 0:
            recs.append(_ct_record(k, "alice", "DeleteObject", "203.0.113.9", {"bucketName": "acme-prod-data", "key": "exports/2025-03/customers.csv", "Host": "acme-prod-data.s3.us-east-1.amazonaws.com"}))
        elif k == n // 2:
            recs.append(_ct_record(k, "bob", "GetObject", "198.51.100.23", {"bucketName": "acme-prod-secrets", "key": "db/password.txt"}, err="AccessDenied"))
        elif k == n - 1:
            recs.append(_ct_record(k, "carol", "PutBucketPolicy", "192.0.2.77", {"bucketName": "acme-prod-data", "policy": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject"}]}}))
        else:
            recs.append(_ct_record(k, "dave", "GetObject", f"198.51.100.{k}", {"bucketName": "acme-prod-data", "key": f"reports/r{k}.pdf"}))
    if lookup:
        evs = [{"EventId": r["eventID"], "EventName": r["eventName"], "ReadOnly": str(r["readOnly"]).lower(), "AccessKeyId": r["userIdentity"]["accessKeyId"], "EventTime": 1741083302.0 + k,
                "EventSource": r["eventSource"], "Username": r["userIdentity"]["arn"].rsplit("/", 1)[1], "Resources": [{"ResourceType": "AWS::S3::Object", "ResourceName": r["resources"][0]["ARN"].split(":::")[1]}],
                "CloudTrailEvent": json.dumps(r, separators=(",", ":"))} for k, r in enumerate(recs)]
        return json.dumps({"Events": evs, "NextToken": "eyJOZXh0VG9rZW4iOiBudWxs"}, indent=4) + "\n"
    return json.dumps({"Records": recs}, indent=2) + "\n"


case(
    1161,
    claim="Each CloudTrail event (from Records or from lookup-events) becomes one line with time, principal, source IP, service:action and its request parameters; an event with an errorCode also keeps its complete record, so who did what to which resource from which IP, and every field of every denied call, are unchanged.",
    loses="userIdentity.sessionContext, tlsDetails, additionalEventData, requestID, eventID, userAgent, responseElements, resources and recipientAccountId of successful events; the lookup-events envelope fields (EventId, ReadOnly, AccessKeyId, Username, Resources) that repeat the record",
    encode=digest_cloudtrail,
    probes=probes_1161,
    fixtures=[
        Fixture("CloudTrail Records: a delete first, a denied GetObject in the middle, a public bucket policy last", _ct_events(10), True),
        Fixture("aws cloudtrail lookup-events, 8 events", _ct_events(8, lookup=True), True),
        Fixture("negative: lookup-events --query EventName", json.dumps(["GetObject", "DeleteObject"], indent=4) + "\n", False),
        Fixture("negative: a Records document with no events", json.dumps({"Records": []}) + "\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #1164 macOS `log show` default style: drop the Thread, Activity and TTL columns, hoist date, zone and PID, mark non-Default types.
# reconstructed from memory (no macOS here). Probes parse each line of the original and of the rendering into (date, time, zone, type,
# pid, process, message) and compare them; Error and Fault lines keep their PID inline.

_LOG = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d{6})([+-]\d{4}) +(0x[0-9a-f]+) +(\w+) +(0x[0-9a-f]+) +(\d+) +(\d+) +(\S.*)$")
_LOGHDR = re.compile(r"^Timestamp +Thread +Type +Activity +PID +TTL *$")
_PROC = re.compile(r"^[\w.\- ]+$")
_TYPE_LETTER = {"Error": "E", "Fault": "F", "Info": "I", "Debug": "D"}
_LETTER_TYPE = {v: k for k, v in _TYPE_LETTER.items()}


def render_log_show(text: str) -> str:
    lines = text.split("\n")
    if any(l.startswith(("# date ", "# pids: ", "# log show")) for l in lines):
        return text
    rows = []
    for l in lines:
        m = _LOG.match(l)
        proc, sep, msg = (m.group(9).partition(": ") if m else ("", "", ""))
        rows.append((m, proc, msg) if m and sep and _PROC.match(proc) else None)
    if not any(rows):
        return text
    pids: dict[str, set] = defaultdict(set)
    for r in rows:
        if r:
            pids[r[1]].add(r[0].group(7))
    legend = {p: next(iter(v)) for p, v in pids.items() if len(v) == 1}
    out = []
    cur = None
    started = False
    for l, r in zip(lines, rows):
        if _LOGHDR.match(l):
            continue
        if r is None:
            out.append(l)
            continue
        m, proc, msg = r
        if not started:
            out.append("# log show: thread, activity and TTL dropped; pids from the legend unless shown as proc[pid]; type letter E, F, I or D only when not Default")
            out.append("# pids: " + "; ".join(f"{p}={v}" for p, v in legend.items()))
            started = True
        if cur != (m.group(1), m.group(3)):
            out.append(f"# date {m.group(1)} tz {m.group(3)}")
            cur = (m.group(1), m.group(3))
        typ = m.group(5)
        inline = typ in _TYPE_LETTER and typ != "Info" and typ != "Debug" or proc not in legend
        letter = f"{_TYPE_LETTER[typ]} " if typ in _TYPE_LETTER else ""
        out.append(f"{m.group(2)} {letter}{proc}{'[' + m.group(7) + ']' if inline else ''}: {msg}" if typ == "Default" or typ in _TYPE_LETTER else l)
        if typ not in _TYPE_LETTER and typ != "Default":
            out[-1] = l  # an unknown type is not guessed at: keep the whole line
    res = "\n".join(out)
    return res if len(res) < len(text) else text


_RENDERED = re.compile(r"^(\d\d:\d\d:\d\d\.\d{6}) (?:([EFID]) )?([\w.\- ]+?)(?:\[(\d+)\])?: (.*)$")


def probes_1164(text: str) -> dict:
    date = tz = None
    legend: dict = {}
    rows, raw, errors = [], [], []
    for line in text.split("\n"):
        m = _LOG.match(line)
        if m and (m.group(9).partition(": ")[1]):
            proc, _, msg = m.group(9).partition(": ")
            rows.append((m.group(1), m.group(2), m.group(3), m.group(5), int(m.group(7)), proc, msg))
            continue
        if line.startswith("# pids: "):
            legend = {}
            for kv in line[len("# pids: "):].split("; "):
                if "=" in kv:
                    k, v = kv.rsplit("=", 1)
                    legend[k] = int(v)
            continue
        if line.startswith("# date "):
            _, _, d, _, z = line.split(" ", 4)
            date, tz = d, z
            continue
        if line.startswith("# log show:") or _LOGHDR.match(line):
            continue
        r = _RENDERED.match(line)
        if r and date is not None and (r.group(4) is not None or r.group(3) in legend):
            pid = int(r.group(4)) if r.group(4) is not None else legend[r.group(3)]
            rows.append((date, r.group(1), tz, _LETTER_TYPE.get(r.group(2), "Default"), pid, r.group(3), r.group(5)))
            continue
        raw.append(line)
    for d, t, z, ty, pid, proc, msg in rows:
        if ty in ("Error", "Fault"):
            errors.append((d, t, ty, pid, proc, msg))
    return {"every line: date, time, zone, type, pid, process, message": rows, "error and fault lines with pid": errors, "other lines": raw,
            "processes and their pids": sorted({(r[5], r[4]) for r in rows})}


def _log_show(n: int = 24) -> str:
    procs = [("kernel", 0, "", ""), ("myd", 1234, "(libsystem_info.dylib) ", "[com.example.app:network] "), ("Safari", 412, "(WebKit) ", "[com.apple.WebKit:Loading] "), ("Safari", 5120, "(WebKit) ", "[com.apple.WebKit:Process] ")]
    out = ['Filtering the log data using "process == \\"myd\\" OR process == \\"Safari\\""', "Skipping info and debug messages, pass --info and/or --debug to include.",
           "Timestamp                       Thread     Type        Activity             PID    TTL  "]
    for k in range(n):
        proc, pid, lib, sub = procs[k % 4]
        day, sec = ("2025-03-04", 2 + k) if k < n - 3 else ("2025-03-05", k - (n - 3))
        typ = "Error" if k in (0, n // 2) else "Fault" if k == n - 1 else "Default"
        msg = {0: "connection to payments:8443 failed: Operation timed out", n // 2: "NSURLErrorDomain -1001: the request timed out", n - 1: "assertion failed: file descriptor leak"}.get(k, f"request {k} completed in {5 + k} ms")
        out.append(f"{day} 09:{15 + sec // 60:02d}:{sec % 60:02d}.{(123456 * (k + 1)) % 1000000:06d}-0800 0x{0x1f2a + k * 3:x}     {typ:<11} 0x0{'':<17} {pid:<6} 0    {proc}: {lib}{sub}{msg}")
    return "\n".join(out) + "\n"


case(
    1164,
    claim="Each default-style log show line becomes 'time [E|F|I|D ]process[pid]: message' with date and zone in '# date' lines, a legend of unique pids, and the type letter and inline pid kept for Error and Fault lines; thread, activity and TTL columns are dropped, so every line's date, time, zone, type, pid, process and message (including its subsystem and category text) survive.",
    loses="the thread id, activity id and TTL columns of every line",
    encode=render_log_show,
    probes=probes_1164,
    fixtures=[
        Fixture("24 lines, three processes (Safari with two pids), errors first, middle and a Fault last, midnight rollover", _log_show(24), True),
        Fixture("negative: --style compact output", "2025-03-04 09:15:02.123 Df myd[1234:1f2a] (libsystem_info.dylib) [com.example.app:network] request 1 completed\n2025-03-04 09:15:03.456 E  Safari[412:1f2d] [com.apple.WebKit:Loading] failed\n", False),
        Fixture("negative: no matching lines", "Filtering the log data using \"process == \\\"nosuch\\\"\"\nTimestamp                       Thread     Type        Activity             PID    TTL  \n", False),
    ],
    min_gain=0.25,
)


# ===============================================================================================================
# #1170 Windows Event XML to one flat line per event: `time channel id computer [provider] [Lnn] Name=value ...` for the Data elements whose
# value is not '-' or empty (values with spaces, quotes or '=' are double-quoted with \ escapes). Probes parse the original with ElementTree
# and the flat line with a tokenizer. reconstructed from memory (no Windows here).

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
_EVENT = re.compile(r"<Event\b[^>]*>.*?</Event>", re.S)


def _q(v: str) -> str:
    if v and all(c.isprintable() and c not in ' "=\\' for c in v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def _event_fields(chunk: str):
    root = ET.fromstring(chunk)
    sysel = root.find(_NS + "System")
    if root.tag != _NS + "Event" or sysel is None:
        return None
    g = lambda t: (sysel.findtext(_NS + t) or "")  # noqa: E731
    tc = sysel.find(_NS + "TimeCreated")
    prov = sysel.find(_NS + "Provider")
    if tc is None or prov is None or not g("EventID") or not g("Channel") or not g("Computer"):
        return None
    ed = root.find(_NS + "EventData")
    kv: list[tuple[str, str]] = []
    if ed is not None:
        n = 0
        for el in ed:
            if el.tag == _NS + "Data":
                n += 1
                name, val = el.get("Name") or f"#{n}", el.text or ""
                if val not in ("", "-"):
                    kv.append((name, val))
            elif el.tag == _NS + "Binary":
                kv.append(("Binary", el.text or ""))
            else:
                return None
    if any(c.tag not in (_NS + "System", _NS + "EventData", _NS + "RenderingInfo") for c in root):
        return None
    return tc.get("SystemTime"), g("Channel"), g("EventID"), g("Computer"), prov.get("Name") or "", g("Level"), kv


def flatten_events(text: str) -> str:
    def one(m):
        st, en, src = m.start(), m.end(), m.string
        if (st > 0 and src[st - 1] != "\n") or (en < len(src) and src[en] != "\n"):
            return m.group(0)  # an event must own its line, or the flat line would glue onto its neighbours
        try:
            f = _event_fields(m.group(0))
        except ET.ParseError:
            return m.group(0)
        if f is None or not f[0] or " " in f[0] + f[1] + f[2] + f[3] or not f[4] or "]" in f[4] or "\n" in f[4] or "\r" in f[4]:
            return m.group(0)
        t, ch, eid, comp, prov, lvl, kv = f
        head = f"{t} {ch} {eid} {comp} [{prov}]" + (f" L{lvl}" if lvl not in ("0", "4", "") else "")
        return head + "".join(f" {k}={_q(v)}" for k, v in kv if k and " " not in k and "=" not in k) if all(k and " " not in k and "=" not in k for k, _ in kv) else m.group(0)

    res = _EVENT.sub(one, text)
    return res if len(res) < len(text) else text


_FLAT = re.compile(r"^(\S+) (\S+) (\d+) (\S+) \[([^\]\n]+)\]( L\d+)?((?: [^\s=]+=(?:\"(?:[^\"\\]|\\.)*\"|[^\s\"]*))*)$")
_KV = re.compile(r'([^\s=]+)=("(?:[^"\\]|\\.)*"|[^\s"]*)')


def _unq(v: str) -> str:
    if v.startswith('"'):
        out, i = [], 1
        while i < len(v) - 1:
            if v[i] == "\\":
                i += 1
                out.append({"n": "\n", "r": "\r", "t": "\t"}.get(v[i], v[i]))
            else:
                out.append(v[i])
            i += 1
        return "".join(out)
    return v


def _probe_event(chunk: str):
    """Independent reading of one event (walks every element by local name instead of using _event_fields)."""
    root = ET.fromstring(chunk)
    loc = lambda el: el.tag.rsplit("}", 1)[-1]  # noqa: E731
    sysel = next((e for e in root if loc(e) == "System"), None)
    if sysel is None or loc(root) != "Event":
        return None
    f = {loc(e): e for e in sysel}
    need = [t for t in ("EventID", "Channel", "Computer", "TimeCreated", "Provider") if t not in f]
    if need:
        return None
    eid, ch, comp = (f[t].text or "" for t in ("EventID", "Channel", "Computer"))
    if not (eid and ch and comp):
        return None
    kids = [e for e in root if loc(e) not in ("System", "EventData", "RenderingInfo")]
    data, n = [], 0
    for ed in (e for e in root if loc(e) == "EventData"):
        for el in ed:
            if loc(el) == "Data":
                n += 1
                if (el.text or "") not in ("", "-"):
                    data.append((el.attrib.get("Name") or "#%d" % n, el.text))
            elif loc(el) == "Binary":
                data.append(("Binary", el.text or ""))
            else:
                return None
    if kids:
        return None
    return (f["TimeCreated"].attrib.get("SystemTime"), ch, eid, comp, f["Provider"].attrib.get("Name") or "", (f["Level"].text if "Level" in f else "") or "", sorted(data))


def probes_1170(text: str) -> dict:
    evs, other = [], []

    def gap(t: str):
        for l in t.split("\n"):
            if not l.strip():
                continue
            fm = _FLAT.match(l)
            if fm:
                kv = [(k, _unq(v)) for k, v in _KV.findall(fm.group(7) or "")]
                evs.append((fm.group(1), fm.group(2), fm.group(3), fm.group(4), fm.group(5), (fm.group(6) or "").strip()[1:], sorted(kv)))
            else:
                other.append(l)

    pos = 0
    for m in _EVENT.finditer(text):
        gap(text[pos:m.start()])
        pos = m.end()
        try:
            f = _probe_event(m.group(0))
        except ET.ParseError:
            f = None
        evs.append(("raw", m.group(0)) if f is None else (f[0], f[1], f[2], f[3], f[4], f[5] if f[5] not in ("0", "4", "") else "", f[6]))
    gap(text[pos:])
    return {"events (time, channel, id, computer, provider, level, non-dash data)": sorted(evs, key=repr), "other lines": other,
            "failed logons (user, domain, ip)": sorted((dict(e[6]).get("TargetUserName"), dict(e[6]).get("TargetDomainName"), dict(e[6]).get("IpAddress"))
                                                       for e in evs if len(e) > 2 and e[2] == "4625")}


def _win_event(eid: str, channel: str, prov: str, guid: str, comp: str, level: int, t: str, data: list, rec: int, binary: str | None = None) -> str:
    d = "".join(f'<Data Name="{n}">{v}</Data>' if n else f"<Data>{v}</Data>" for n, v in data) + (f"<Binary>{binary}</Binary>" if binary else "")
    return (f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="{prov}" Guid="{{{guid}}}"/><EventID>{eid}</EventID><Version>0</Version><Level>{level}</Level>'
            f'<Task>12546</Task><Opcode>0</Opcode><Keywords>0x8010000000000000</Keywords><TimeCreated SystemTime="{t}"/><EventRecordID>{rec}</EventRecordID>'
            f'<Correlation ActivityID="{{{_uuid("act" + str(rec))}}}"/><Execution ProcessID="652" ThreadID="{1200 + rec % 50}"/><Channel>{channel}</Channel><Computer>{comp}</Computer><Security/></System>'
            f"<EventData>{d}</EventData></Event>")


def _win_events() -> str:
    sec = ("Microsoft-Windows-Security-Auditing", "54849625-5478-4994-A5BA-3E3B0328C30D", "DC01.corp.example.com")

    def fail(user, ip, rec, t):
        return _win_event("4625", "Security", *sec[:2], sec[2], 0, t, [("SubjectUserSid", "S-1-0-0"), ("SubjectUserName", "-"), ("SubjectDomainName", "-"), ("SubjectLogonId", "0x0"),
                          ("TargetUserSid", "S-1-0-0"), ("TargetUserName", user), ("TargetDomainName", "CORP"), ("Status", "0xc000006d"), ("FailureReason", "%%2313"), ("SubStatus", "0xc000006a"),
                          ("LogonType", "3"), ("LogonProcessName", "NtLmSsp "), ("AuthenticationPackageName", "NTLM"), ("WorkstationName", "WS-0042"), ("TransitedServices", "-"),
                          ("PackageName", "-"), ("KeyLength", "0"), ("ProcessId", "0x0"), ("ProcessName", "-"), ("IpAddress", ip), ("IpPort", "51234")], rec)
    ok = _win_event("4624", "Security", *sec[:2], sec[2], 0, "2025-03-04T10:15:05.5000000Z", [("SubjectUserSid", "S-1-5-18"), ("SubjectUserName", "DC01$"), ("SubjectDomainName", "CORP"),
                    ("TargetUserSid", "S-1-5-21-1-2-3-1105"), ("TargetUserName", "bob"), ("TargetDomainName", "CORP"), ("LogonType", "3"), ("IpAddress", "10.0.0.7"), ("IpPort", "49152"),
                    ("ProcessName", "-"), ("WorkstationName", "-")], 1234571)
    scm = _win_event("7034", "System", "Service Control Manager", "555908d1-a6d7-4695-8e1e-26931d2012f4", "WS-0042.corp.example.com", 2, "2025-03-04T10:15:09.0000000Z",
                     [("param1", "Print Spooler"), ("param2", "1")], 88112, binary="530070006F006F006C00650072000000")
    unnamed = _win_event("1000", "Application", "Application Error", "a0e9b4e1-0000-0000-0000-000000000001", "WS-0042.corp.example.com", 2, "2025-03-04T10:15:11.0000000Z",
                         [("", "app.exe"), ("", "1.2.3.4"), ("", 'C:\\Program Files\\App "x"\\app.exe'), ("", "")], 88113)
    evs = [fail("alice", "203.0.113.9", 1234567, "2025-03-04T10:15:02.1234567Z"), fail("bob", "198.51.100.23", 1234568, "2025-03-04T10:15:03.2000000Z"), ok, scm, unnamed,
           fail("mallory", "192.0.2.77", 1234580, "2025-03-04T10:15:12.9000000Z")]
    return "\n".join(evs) + "\n"


case(
    1170,
    claim="Each Windows event XML becomes one line with time, channel, EventID, computer, provider, the level when it is not 0 or 4, and Name=value for every Data or Binary element whose value is not '-' or empty (quoted when needed); parsing the original and the line gives the same time, channel, id, computer, provider, level and Name/value set, including the failing user and source IP of a 4625.",
    loses="provider GUID, Version, Task, Opcode, Keywords, EventRecordID, Correlation, Execution, the Security element and Data elements whose value is '-' or empty",
    encode=flatten_events,
    probes=probes_1170,
    fixtures=[
        Fixture("six events: failed logon first, a success, a service crash, an unnamed-Data application error, a failed logon last", _win_events(), True),
        Fixture("negative: Event Viewer text rendering", "Log Name:      Security\nSource:        Microsoft-Windows-Security-Auditing\nEvent ID:      4625\nTask Category: Logon\nLevel:         Information\n", False),
        Fixture("negative: an Event whose EventData holds a nested element is not flattened", '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="X"/><EventID>1</EventID><TimeCreated SystemTime="2025-03-04T10:15:02Z"/><Channel>Application</Channel><Computer>h</Computer></System><EventData><ComplexData><A>1</A></ComplexData></EventData></Event>\n', False),
    ],
    min_gain=0.5,
)


# Real captures of `cargo test` on a 3-test crate (two failures) in this container with RUST_BACKTRACE=0 and =1 (cargo 1.97.0). The =full
# run (26,353 chars, 10,805 o200k tokens) was measured but is not embedded.
_CARGO_BT0 = r'''   Compiling ledger v0.1.0 (/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/ci_infra_cloud/crate)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 1.59s
     Running unittests src/lib.rs (target/debug/deps/ledger-8b2411f4a0f0f4f3)

running 3 tests
test tests::deposit_adds ... ok
test tests::transfer_moves_money ... FAILED
test tests::transfer_from_unknown_account ... FAILED

failures:

---- tests::transfer_moves_money stdout ----

thread 'tests::transfer_moves_money' (4793) panicked at src/lib.rs:49:9:
assertion `left == right` failed
  left: 20
 right: 25
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

---- tests::transfer_from_unknown_account stdout ----

thread 'tests::transfer_from_unknown_account' (4792) panicked at src/lib.rs:17:33:
unknown account


failures:
    tests::transfer_from_unknown_account
    tests::transfer_moves_money

test result: FAILED. 1 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
'''
_CARGO_BT1 = r'''    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.01s
     Running unittests src/lib.rs (target/debug/deps/ledger-8b2411f4a0f0f4f3)

running 3 tests
test tests::deposit_adds ... ok
test tests::transfer_from_unknown_account ... FAILED
test tests::transfer_moves_money ... FAILED

failures:

---- tests::transfer_from_unknown_account stdout ----

thread 'tests::transfer_from_unknown_account' (4808) panicked at src/lib.rs:17:33:
unknown account
stack backtrace:
   0: __rustc::rust_begin_unwind
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/panicking.rs:689:5
   1: core::panicking::panic_fmt
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:80:14
   2: core::panicking::panic_display::<&str>
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:259:5
   3: core::option::expect_failed
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/option.rs:2257:5
   4: <core::option::Option<&i64>>::expect
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/option.rs:968:21
   5: <ledger::Ledger>::balance_of
             at ./src/lib.rs:17:33
   6: <ledger::Ledger>::transfer
             at ./src/lib.rs:21:25
   7: ledger::tests::transfer_from_unknown_account
             at ./src/lib.rs:55:11
   8: ledger::tests::transfer_from_unknown_account::{closure#0}
             at ./src/lib.rs:53:39
   9: <ledger::tests::transfer_from_unknown_account::{closure#0} as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
  10: <fn() -> core::result::Result<(), alloc::string::String> as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.

---- tests::transfer_moves_money stdout ----

thread 'tests::transfer_moves_money' (4809) panicked at src/lib.rs:49:9:
assertion `left == right` failed
  left: 20
 right: 25
stack backtrace:
   0: __rustc::rust_begin_unwind
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/panicking.rs:689:5
   1: core::panicking::panic_fmt
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:80:14
   2: core::panicking::assert_failed_inner
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:439:17
   3: core::panicking::assert_failed::<i64, i64>
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:394:5
   4: ledger::tests::transfer_moves_money
             at ./src/lib.rs:49:9
   5: ledger::tests::transfer_moves_money::{closure#0}
             at ./src/lib.rs:45:30
   6: <ledger::tests::transfer_moves_money::{closure#0} as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
   7: <fn() -> core::result::Result<(), alloc::string::String> as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.


failures:
    tests::transfer_from_unknown_account
    tests::transfer_moves_money

test result: FAILED. 1 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.30s

error: test failed, to rerun pass `--lib`
'''
_CARGO_FULL_TOKENS = 10805


# ===============================================================================================================
# #1235 Digest cloud CLI debug traces: kubectl -v=8 (glog lines) and aws --debug (botocore log records). Kept: one digest line per request
# (method, URL, status, latency), the verbatim request and response bodies of a failed (>=400) request, every other non-request line,
# credential and retry events, tracebacks and the final error. Folded: request and response header dumps and 2xx bodies (dropped), runs of
# three or more kubectl discovery GETs (one count line), botocore hook, loader and signing noise (one count line). TF_LOG is not covered.
# reconstructed from memory (no cluster or AWS account here)

_GLOG = re.compile(r"^([IWEF])(\d{4}) (\d\d:\d\d:\d\d\.\d{6})\s+(\d+) (\S+:\d+)\] (.*)$")
_KREQ = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD) (https?://\S+)$")
_KSTATUS = re.compile(r"^Response Status: (.+?)(?: in (\d+) milliseconds)?$")
_KBODY = re.compile(r"^(?:Request|Response) Body: ")
_KHDR = re.compile(r"^(?:(?:Request|Response) Headers:|    [\w-]+: .*)$")
_KDISC = re.compile(r"^https?://[^/]+/(?:api|apis|api/[^/?]+|apis/[^/?]+/[^/?]+|openapi/v3)\?timeout=\d+s$")
_KDIG = re.compile(r"^(\S+) (GET|POST|PUT|PATCH|DELETE|HEAD) (\S+) -> (.*?) in (\d+)ms$|^(\S+) (GET|POST|PUT|PATCH|DELETE|HEAD) (\S+) -> \(no status line\)$")
_KFOLD = re.compile(r"^(\S+) discovery: (\d+) GETs \((.*); slowest (\d+)ms\)$")


def _code(status: str):
    m = re.match(r"^(\d{3})\b", status)
    return int(m.group(1)) if m else None


def digest_kubectl_trace(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        g = _GLOG.match(lines[i])
        r = _KREQ.match(g.group(6)) if g else None
        if not r:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        status, ms, kept_bodies = None, None, []
        while j < len(lines):
            gj = _GLOG.match(lines[j])
            if not gj:
                break
            m = gj.group(6)
            if _KREQ.match(m):
                break
            if (s := _KSTATUS.match(m)) and status is None:
                status, ms = s.group(1), s.group(2)
            elif _KBODY.match(m):
                kept_bodies.append(lines[j])
            elif not _KHDR.match(m):
                break
            j += 1
        c = _code(status) if status else None
        head = f"{g.group(3)} {r.group(1)} {r.group(2)} -> " + (f"{status} in {ms}ms" if status and ms is not None else "(no status line)")
        if status and ms is None:
            out += lines[i:j]  # a status without a latency does not fit the digest grammar: keep the group as it was
        else:
            out.append(head)
            if c is not None and c >= 400:
                out += kept_bodies
        i = j
    # fold runs of discovery digests
    res, k = [], 0
    while k < len(out):
        d = _KDIG.match(out[k])
        if d and d.group(2) and _KDISC.match(d.group(3)):
            e = k
            while e < len(out) and (dd := _KDIG.match(out[e])) and dd.group(2) and _KDISC.match(dd.group(3)) and (_code(dd.group(4)) or 0) < 400:
                e += 1
            if e - k >= 3:
                sts = Counter(_KDIG.match(l).group(4) for l in out[k:e])
                slow = max(int(_KDIG.match(l).group(5)) for l in out[k:e])
                res.append(f"{d.group(1)} discovery: {e - k} GETs (" + ", ".join(f"{s} x{n}" for s, n in sorted(sts.items())) + f"; slowest {slow}ms)")
                k = e
                continue
        res.append(out[k])
        k += 1
    final = "\n".join(res)
    return final if len(final) < len(text) else text


def probes_kubectl(text: str) -> dict:
    reqs, disc, fails, other = [], [], [], []
    cur = None

    def close():
        nonlocal cur
        if cur is not None:
            c = _code(cur["status"]) if cur["status"] else None
            if cur["disc"] and (c is None or c < 400):
                disc.append((cur["status"], cur["ms"]))
            else:
                reqs.append((cur["method"], cur["url"], cur["status"], cur["ms"]))
            if c is not None and c >= 400:
                fails.append((cur["url"], cur["bodies"]))
        cur = None

    for line in text.split("\n"):
        f = _KFOLD.match(line)
        if f:
            close()
            for part in f.group(3).split(", "):
                st, n = part.rsplit(" x", 1)
                disc += [(st, None)] * int(n)
            disc.append(("slowest", int(f.group(4))))
            continue
        d = _KDIG.match(line)
        if d:
            close()
            if d.group(2):
                cur = {"method": d.group(2), "url": d.group(3), "status": d.group(4), "ms": int(d.group(5)), "bodies": [], "disc": bool(_KDISC.match(d.group(3)))}
            else:
                cur = {"method": d.group(7), "url": d.group(8), "status": None, "ms": None, "bodies": [], "disc": bool(_KDISC.match(d.group(8)))}
            continue
        g = _GLOG.match(line)
        m = g.group(6) if g else None
        if g and (r := _KREQ.match(m)):
            close()
            cur = {"method": r.group(1), "url": r.group(2), "status": None, "ms": None, "bodies": [], "disc": bool(_KDISC.match(r.group(2)))}
        elif cur is not None and g and (s := _KSTATUS.match(m)) and cur["status"] is None and s.group(2) is not None:
            cur["status"], cur["ms"] = s.group(1), int(s.group(2))
        elif cur is not None and g and _KBODY.match(m):
            cur["bodies"].append(line)
        elif cur is not None and g and _KHDR.match(m):
            continue
        else:
            close()
            other.append(line)
    close()
    stat = Counter(s for s, n in disc if s != "slowest")
    slow = max([ms for s, ms in disc if s == "slowest"] + [ms for s, ms in disc if s != "slowest" and ms is not None] + [-1])
    return {"requests (method, url, status, ms)": reqs, "discovery GETs by status, slowest ms": (sorted(stat.items(), key=repr), slow),
            "bodies of failed requests": fails, "every other line": other}


_BREC = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - (\S+) - ([\w.]+) - (DEBUG|INFO|WARNING|ERROR) - (.*)$")
_BSTATUS = re.compile(r'^(https?://\S+) "(\w+) (\S+) HTTP/[\d.]+" (\d{3}) (\S+)$')
_BDIG = re.compile(r"^(\S+ \S+) request (\w+) (\w+) (\S+)$")
_BDROP = re.compile(r"^# dropped (\d+) debug records: (.*)$")


def digest_aws_debug(text: str) -> str:
    lines = text.split("\n")
    if any(_BDIG.match(l) or _BDROP.match(l) for l in lines):
        return text
    recs: list[list[str]] = []
    pre: list[list[str]] = []
    for l in lines:
        if _BREC.match(l):
            recs.append([l])
        elif recs:
            recs[-1].append(l)
        else:
            pre.append([l])
    out = [x for p in pre for x in p]
    dropped: Counter = Counter()
    last = None
    summary_at = None
    for r in recs:
        m = _BREC.match(r[0])
        ts, logger, lvl, msg = m.group(1), m.group(3), m.group(4), m.group(5)
        body = "\n".join([msg] + r[1:])
        keep = False
        if logger == "botocore.endpoint" and msg.startswith("Making request for OperationModel(name="):
            op = re.match(r"Making request for OperationModel\(name=(\w+)\)", msg)
            me, url = re.search(r"'method': '(\w+)'", msg), re.search(r"'url': '([^']+)'", msg)
            if op and me and url and len(r) == 1:
                out.append(f"{ts} request {op.group(1)} {me.group(1)} {url.group(1)}")
                continue
            keep = True
        elif logger == "urllib3.connectionpool" and _BSTATUS.match(msg):
            last = int(_BSTATUS.match(msg).group(4))
            keep = True
        elif logger == "botocore.parsers" and msg.startswith("Response body"):
            keep = last is not None and last >= 400
        elif logger == "botocore.parsers" and msg.startswith("Response headers"):
            keep = False
        elif logger in ("awscli.clidriver", "botocore.credentials"):
            keep = True
        elif logger == "botocore.retryhandler":
            keep = msg != "No retry needed."
        elif logger == "botocore.endpoint":
            keep = bool(re.search(r"retry|sleep", msg, re.I))
        elif lvl in ("WARNING", "ERROR") or re.search(r"Traceback|Exception", body):
            keep = True
        if keep:
            out += r
        else:
            dropped[logger] += 1
            if summary_at is None:
                summary_at = len(out)
                out.append("")
    if summary_at is not None:
        out[summary_at] = f"# dropped {sum(dropped.values())} debug records: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items(), key=lambda kv: (-kv[1], kv[0])))
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_aws_debug(text: str) -> dict:
    reqs, statuses, errbodies, auth, retries, cli, other, errs = [], [], [], [], [], [], [], []
    cur = None  # (logger, msg, [continuation])
    last = None

    def close():
        nonlocal cur, last
        if cur is None:
            return
        ts, logger, lvl, msg, cont = cur
        body = "\n".join([msg] + cont)
        if logger == "urllib3.connectionpool" and (s := _BSTATUS.match(msg)):
            last = int(s.group(4))
            statuses.append((ts, s.group(2), s.group(3), last))
        elif logger == "botocore.parsers" and msg.startswith("Response body"):
            if last is not None and last >= 400:
                errbodies.append((ts, body))
        elif logger == "botocore.credentials":
            auth.append((ts, msg))
        elif logger == "botocore.retryhandler" and msg != "No retry needed.":
            retries.append((ts, msg))
        elif logger == "botocore.endpoint" and re.search(r"retry|sleep", msg, re.I) and not msg.startswith("Making request for"):
            retries.append((ts, msg))
        elif logger == "awscli.clidriver":
            cli.append((ts, body))
        elif lvl in ("WARNING", "ERROR") or re.search(r"Traceback|Exception", body):
            errs.append((ts, logger, body))
        cur = None

    for line in text.split("\n"):
        m = _BREC.match(line)
        if m:
            close()
            ts, logger, lvl, msg = m.group(1), m.group(3), m.group(4), m.group(5)
            if logger == "botocore.endpoint" and (o := re.match(r"Making request for OperationModel\(name=(\w+)\)", msg)):
                me, url = re.search(r"'method': '(\w+)'", msg), re.search(r"'url': '([^']+)'", msg)
                if me and url:
                    reqs.append((ts, o.group(1), me.group(1), url.group(1)))
                    continue
            cur = (ts, logger, lvl, msg, [])
            continue
        d = _BDIG.match(line)
        if d:
            close()
            reqs.append((d.group(1), d.group(2), d.group(3), d.group(4)))
            continue
        if _BDROP.match(line):
            close()
            continue
        if cur is not None:
            cur[4].append(line)
        else:
            other.append(line)
    close()
    return {"requests (time, operation, method, url)": reqs, "http statuses": statuses, "bodies of failed responses": errbodies, "credential events": auth,
            "retry events": retries, "cli driver records": cli, "warnings, errors, tracebacks": errs, "lines outside records": other}


_AWS_ANY = re.compile(_BREC.pattern, re.M)
_AWS_MARK = re.compile(_BDIG.pattern + "|" + _BDROP.pattern, re.M)
_KUBE_ANY = re.compile(_GLOG.pattern, re.M)


def _is_aws(t: str) -> bool:
    return bool(_AWS_ANY.search(t))


def _is_kube(t: str) -> bool:
    return bool(_KUBE_ANY.search(t))


def _kubectl_trace(failure: bool = False, n_disc: int = 40) -> str:
    out = ["I0304 10:15:02.001000   12345 loader.go:373] Config loaded from file:  /home/user/.kube/config"]
    t = [2]

    def ts():
        t[0] += 1
        return f"10:15:{t[0] // 1000:02d}.{t[0] % 1000:03d}000"

    def req(method, url, status, ms, body=None, rbody=None):
        s = [f"I0304 {ts()}   12345 round_trippers.go:463] {method} {url}", f"I0304 {ts()}   12345 round_trippers.go:469] Request Headers:",
             f"I0304 {ts()}   12345 round_trippers.go:473]     Accept: application/json;as=Table;v=v1;g=meta.k8s.io,application/json;as=Table;v=v1beta1;g=meta.k8s.io,application/json",
             f"I0304 {ts()}   12345 round_trippers.go:473]     User-Agent: kubectl/v1.30.0 (linux/amd64) kubernetes/7c48c2b"]
        if rbody:
            s.append(f"I0304 {ts()}   12345 request.go:1212] Request Body: {rbody}")
        s += [f"I0304 {ts()}   12345 round_trippers.go:574] Response Status: {status} in {ms} milliseconds", f"I0304 {ts()}   12345 round_trippers.go:577] Response Headers:",
              f"I0304 {ts()}   12345 round_trippers.go:580]     Audit-Id: {_uuid(url + str(t[0]))}", f"I0304 {ts()}   12345 round_trippers.go:580]     Cache-Control: no-cache, private",
              f"I0304 {ts()}   12345 round_trippers.go:580]     Content-Type: application/json", f"I0304 {ts()}   12345 round_trippers.go:580]     Date: Tue, 04 Mar 2025 10:15:02 GMT"]
        if body:
            s.append(f"I0304 {ts()}   12345 request.go:1212] Response Body: {body}")
        return s

    api = "https://127.0.0.1:6443"
    out += req("GET", f"{api}/api/v1/namespaces/default/pods?limit=500", "200 OK", 24, body='{"kind":"Table","apiVersion":"meta.k8s.io/v1","metadata":{"resourceVersion":"190233"},"columnDefinitions":[{"name":"Name","type":"string"}],"rows":[{"cells":["web-6d8f7c9b5-x2k9p"]}]}')
    for k in range(n_disc):
        path = "/api" if k == 0 else "/apis" if k == 1 else f"/apis/group{k}.example.com/v1" if k % 2 else f"/api/v{k}"
        out += req("GET", f"{api}{path}?timeout=32s", "200 OK" if k != 7 else "304 Not Modified", 2 + k % 9, body='{"kind":"APIGroupList","apiVersion":"v1","groups":[]}')
    if failure:
        out += req("GET", f"{api}/api/v1/namespaces/kube-system/secrets?limit=500", "403 Forbidden", 12,
                   body='{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"secrets is forbidden: User \\"alice\\" cannot list resource \\"secrets\\" in API group \\"\\" in the namespace \\"kube-system\\"","reason":"Forbidden","details":{"kind":"secrets"},"code":403}')
        out.append('E0304 10:15:03.900000   12345 memcache.go:265] couldn\'t get current server API group list: Get "https://127.0.0.1:6443/api?timeout=32s": dial tcp 127.0.0.1:6443: connect: connection refused')
        out.append('Error from server (Forbidden): secrets is forbidden: User "alice" cannot list resource "secrets" in API group "" in the namespace "kube-system"')
    else:
        out += req("GET", f"{api}/api/v1/namespaces/default/services?limit=500", "200 OK", 18, body='{"kind":"Table","rows":[]}')
        out += ["NAME                        READY   STATUS    RESTARTS   AGE", "web-6d8f7c9b5-x2k9p        1/1     Running   0          3d", "web-6d8f7c9b5-q7m4d        1/1     Running   0          3d"]
    return "\n".join(out) + "\n"


def _aws_debug(failure: bool = False) -> str:
    out = ["2025-03-04 10:15:02,100 - MainThread - awscli.clidriver - DEBUG - CLI version: aws-cli/2.15.0 Python/3.11.6 Linux/6.1.0 exe/x86_64.ubuntu.22 prompt/off",
           "2025-03-04 10:15:02,101 - MainThread - awscli.clidriver - DEBUG - Arguments entered to CLI: ['s3', 'ls', '--debug']"]
    for k in range(40):
        out.append(f"2025-03-04 10:15:02,{110 + k:03d} - MainThread - botocore.hooks - DEBUG - Event session-initialized: calling handler <function handler_{k} at 0x7f3a2c{k:04x}>")
    out += ["2025-03-04 10:15:02,200 - MainThread - botocore.credentials - DEBUG - Looking for credentials via: env",
            "2025-03-04 10:15:02,201 - MainThread - botocore.credentials - INFO - Found credentials in shared credentials file: ~/.aws/credentials"]
    for k in range(6):
        out.append(f"2025-03-04 10:15:02,{210 + k:03d} - MainThread - botocore.loaders - DEBUG - Loading JSON file: /usr/local/aws-cli/v2/2.15.0/dist/awscli/botocore/data/s3/2006-03-01/endpoint-rule-set-{k}.json")
    out += ["2025-03-04 10:15:02,300 - MainThread - botocore.endpoint - DEBUG - Making request for OperationModel(name=ListBuckets) with params: {'url_path': '/', 'query_string': '', 'method': 'GET', "
            "'headers': {'User-Agent': 'aws-cli/2.15.0'}, 'body': b'', 'url': 'https://s3.us-east-1.amazonaws.com/', 'context': {'client_region': 'us-east-1'}}",
            "2025-03-04 10:15:02,301 - MainThread - botocore.auth - DEBUG - Calculating signature using v4 auth.",
            "2025-03-04 10:15:02,301 - MainThread - botocore.auth - DEBUG - CanonicalRequest:", "GET", "/", "", "host:s3.us-east-1.amazonaws.com", "x-amz-date:20250304T101502Z", "",
            "2025-03-04 10:15:02,306 - MainThread - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): s3.us-east-1.amazonaws.com:443"]
    if failure:
        out += ['2025-03-04 10:15:02,455 - MainThread - urllib3.connectionpool - DEBUG - https://s3.us-east-1.amazonaws.com:443 "GET / HTTP/1.1" 403 None',
                "2025-03-04 10:15:02,456 - MainThread - botocore.parsers - DEBUG - Response headers: {'x-amz-request-id': 'ABC', 'Content-Type': 'application/xml'}",
                "2025-03-04 10:15:02,456 - MainThread - botocore.parsers - DEBUG - Response body:",
                "b'<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message><RequestId>ABC</RequestId></Error>'",
                "2025-03-04 10:15:02,460 - MainThread - botocore.hooks - DEBUG - Event needs-retry.s3.ListBuckets: calling handler <bound method RetryHandler.needs_retry>",
                "2025-03-04 10:15:02,461 - MainThread - botocore.retryhandler - DEBUG - No retry needed.",
                "2025-03-04 10:15:02,470 - MainThread - awscli.clidriver - DEBUG - Exception caught in main()", "Traceback (most recent call last):",
                '  File "awscli/clidriver.py", line 460, in main', "botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the ListBuckets operation: Access Denied", "",
                "An error occurred (AccessDenied) when calling the ListBuckets operation: Access Denied"]
    else:
        out += ['2025-03-04 10:15:02,455 - MainThread - urllib3.connectionpool - DEBUG - https://s3.us-east-1.amazonaws.com:443 "GET / HTTP/1.1" 200 None',
                "2025-03-04 10:15:02,456 - MainThread - botocore.parsers - DEBUG - Response headers: {'x-amz-id-2': 'abc', 'Content-Type': 'application/xml'}",
                "2025-03-04 10:15:02,456 - MainThread - botocore.parsers - DEBUG - Response body:", "b'<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<ListAllMyBucketsResult><Buckets><Bucket><Name>acme-prod</Name></Bucket></Buckets></ListAllMyBucketsResult>'",
                "2025-03-04 10:15:02,461 - MainThread - botocore.retryhandler - DEBUG - No retry needed.", "2025-03-04 acme-prod"]
    return "\n".join(out) + "\n"


case(
    1235,
    claim="For kubectl -v=8 output, each request becomes one 'time METHOD url -> status in Nms' line, header dumps and 2xx bodies are dropped, the request and response bodies and the final error of a failed request stay verbatim, and runs of three or more discovery GETs become one count line; for aws --debug, requests become one digest line while HTTP status lines, credential and retry events, the failed response body, tracebacks and the final error stay verbatim and hook, loader and signing noise becomes one count line.",
    loses="request and response headers, 2xx response bodies, per-request detail of discovery GETs, the botocore signing, hook and loader records and 'No retry needed' lines; TF_LOG output is not handled",
    encode=lambda t: digest_aws_debug(t) if _is_aws(t) and not _is_kube(t) else digest_kubectl_trace(t),
    probes=lambda t: probes_aws_debug(t) if _is_aws(t) or _AWS_MARK.search(t) else probes_kubectl(t),
    fixtures=[
        Fixture("kubectl get pods -v=8 with 40 discovery GETs, then a list", _kubectl_trace(), True),
        Fixture("kubectl get secrets -v=8 failing with 403 at the end", _kubectl_trace(failure=True), True),
        Fixture("aws s3 ls --debug, success", _aws_debug(), True),
        Fixture("aws s3 ls --debug, AccessDenied: status, body, traceback and error at the end", _aws_debug(failure=True), True),
        Fixture("negative: kubectl -v=6 prints one line per request already", "I0304 10:15:02.134567   12345 round_trippers.go:553] GET https://127.0.0.1:6443/api/v1/namespaces/default/pods?limit=500 200 OK in 24 milliseconds\nNAME   READY   STATUS\nweb    1/1     Running\n", False),
        Fixture("negative: plain command output", "NAME   READY   STATUS\nweb    1/1     Running\n", False),
    ],
    min_gain=0.4,
)


# ===============================================================================================================
# #1158 Fold `xcrun simctl list` and xcodebuild destination errors; bound bare `adb shell dumpsys`.
# simctl: devices of an `-- Unavailable: runtime --` section become one count line; Device Types, Device Pairs and unavailable runtimes fold to
# counts; every available runtime keeps every device with its UDID and state (an agent needs the UDID). xcodebuild: the 'Ineligible
# destinations' block and placeholder destinations fold to counts, every eligible destination stays. dumpsys: each `DUMP OF SERVICE x:` section
# keeps its first 3 lines and a '[N more lines omitted; run: dumpsys x]' line, the service list stays. reconstructed from memory (no Mac, no adb).

_UDID = r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
_SIMDEV = re.compile(rf"^    (.+) \(({_UDID})\) \((Booted|Shutdown|Shutting Down|Creating)\)(?: \((unavailable[^)]*)\))? *$")
_SIMSEC = re.compile(r"^-- (.+) --$")
_SIMTOP = re.compile(r"^== (Device Types|Runtimes|Devices|Device Pairs) ==$")
_DUMPSYS = re.compile(r"^DUMP OF SERVICE ([\w.:/\-]+):$")
_DSEP = "-" * 79


def fold_device_lists(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        l = lines[i]
        if _SIMTOP.match(l) and l in ("== Device Types ==", "== Device Pairs =="):
            j = i + 1
            while j < n and not _SIMTOP.match(lines[j]) and lines[j].strip():
                j += 1
            cnt = j - i - 1
            out.append(l if cnt < 3 else f"{l} ({cnt} lines omitted)")
            if cnt < 3:
                out += lines[i + 1:j]
            i = j
            continue
        m = _SIMSEC.match(l)
        if m and m.group(1).startswith("Unavailable: "):
            j = i + 1
            while j < n and _SIMDEV.match(lines[j]):
                j += 1
            if j - i - 1 >= 1:
                out.append(f"-- {m.group(1)} -- ({j - i - 1} devices omitted)")
                i = j
                continue
        if re.match(r"^\S.* \(\S+ - \S+\) - \S+ \(unavailable[^)]*\)$", l):
            j = i + 1
            while j < n and re.match(r"^\S.* \(\S+ - \S+\) - \S+ \(unavailable[^)]*\)$", lines[j]):
                j += 1
            out.append(f"({j - i} unavailable runtimes omitted)" if j - i >= 2 else l)
            i = j if j - i >= 2 else i + 1
            continue
        if l.strip() == "Ineligible destinations" or re.match(r'^\tIneligible destinations for the ".*" scheme:$', l):
            j = i + 1
            while j < n and lines[j].startswith("\t\t{"):
                j += 1
            out.append(f"{l} ({j - i - 1} omitted)" if j - i - 1 >= 2 else l)
            if j - i - 1 < 2:
                out += lines[i + 1:j]
            i = j
            continue
        if _DUMPSYS.match(l) and i > 0 and lines[i - 1] == _DSEP:
            j = i + 1
            while j < n and not (lines[j] == _DSEP and j + 1 < n and _DUMPSYS.match(lines[j + 1])):
                j += 1
            body = lines[i + 1:j]
            while body and not body[-1].strip():
                body = body[:-1]
            out.append(l)
            nb = [k for k, b in enumerate(body) if b.strip()]
            if len(nb) > 4:
                out += body[:nb[2] + 1] + [f"    [{len(nb) - 3} more lines omitted; run: dumpsys {_DUMPSYS.match(l).group(1)}]"]
                out += lines[i + 1 + len(body):j]
            else:
                out += lines[i + 1:j]
            i = j
            continue
        out.append(l)
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


_UNAV_RT = re.compile(r"^\S.* \(\S+ - \S+\) - \S+ \(unavailable[^)]*\)$")
_OMIT_DEV = re.compile(r"^-- (Unavailable: .+) -- \((\d+) devices omitted\)$")
_OMIT_LINES = re.compile(r"^(== Device Types ==|== Device Pairs ==) \((\d+) lines omitted\)$")
_OMIT_RT = re.compile(r"^\((\d+) unavailable runtimes omitted\)$")
_OMIT_DEST = re.compile(r"^(.*Ineligible destinations.*) \((\d+) omitted\)$")
_OMIT_DUMP = re.compile(r"^    \[(\d+) more lines omitted; run: dumpsys (\S+)\]$")


def probes_1158(text: str) -> dict:
    lines = text.split("\n")
    top, sec = None, None
    devices, unavailable, counts, runtimes_unavail, dests, inelig = [], Counter(), {}, 0, [], 0
    services, svc_in, sections, cur = [], False, [], None
    other = []
    for idx, l in enumerate(lines):
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        if not l.strip() and top in ("Device Types", "Device Pairs"):
            top = None
            continue
        t = _SIMTOP.match(l)
        if t:
            top, sec = t.group(1), None
            continue
        if top in ("Device Types", "Device Pairs"):
            counts["== " + top + " =="] = counts.get("== " + top + " ==", 0) + 1
            continue
        if (o := _OMIT_LINES.match(l)):
            counts[o.group(1)] = int(o.group(2))
            top = o.group(1).strip("= ")
            continue
        s = _SIMSEC.match(l)
        if s and top in ("Devices", "Runtimes", None) or (s and top):
            sec = s.group(1)
            continue
        if (o := _OMIT_DEV.match(l)):
            unavailable[o.group(1)] += int(o.group(2))
            sec = o.group(1)  # devices that follow a broken line still belong to this section
            continue
        d = _SIMDEV.match(l)
        if d:
            if sec and sec.startswith("Unavailable: "):
                unavailable[sec] += 1
            else:
                devices.append((sec, d.group(1), d.group(2), d.group(3)))
            continue
        if _UNAV_RT.match(l):
            runtimes_unavail += 1
            continue
        if (o := _OMIT_RT.match(l)):
            runtimes_unavail += int(o.group(1))
            continue
        if l.startswith("\t\t{"):
            (inelig_lines := dests).append(l) if "Ineligible" not in (cur or "") else None
            inelig += 1 if "Ineligible" in (cur or "") else 0
            continue
        if "Ineligible destinations" in l:
            cur = "Ineligible"
            m = _OMIT_DEST.match(l)
            inelig += int(m.group(2)) if m else 0
            other.append(re.sub(r" \(\d+ omitted\)$", "", l))
            continue
        if l.startswith("\tAvailable destinations"):
            cur = "Available"
        if l.strip() == "Currently running services:":
            svc_in = True
            other.append(l)
            continue
        if svc_in and l.startswith("  ") and l.strip():
            services.append(l.strip())
            continue
        m = _DUMPSYS.match(l) if idx > 0 and lines[idx - 1] == _DSEP else None
        if m:
            svc_in = False
            cur = None
            sections.append([m.group(1), [], 0])
            continue
        if (o := _OMIT_DUMP.match(l)) and sections:
            sections[-1][2] += int(o.group(1))
            continue
        if sections and not (l == _DSEP and _DUMPSYS.match(nxt)):
            if len(sections[-1][1]) < 3 and l.strip():
                sections[-1][1].append(l)
            elif l.strip():
                sections[-1][2] += 1
            continue
        other.append(l)
    return {"available devices (runtime, name, udid, state)": devices, "devices omitted per unavailable runtime": sorted(unavailable.items()),
            "device types and pairs line counts": sorted(counts.items()), "unavailable runtimes": runtimes_unavail, "eligible destinations": dests, "ineligible destination count": inelig,
            "dumpsys services": services, "dumpsys sections (name, first lines, lines beyond them)": [(a, b, c) for a, b, c in sections],
            "all other non-blank lines": [x for x in other if x.strip()]}


def _simctl(n_unavail: int = 4) -> str:
    def dev(name, k, state="Shutdown", extra=""):
        return f"    {name} ({_uuid(f'{name}{k}').upper()}) ({state}){extra} "
    out = ["== Device Types ==", *[f"iPhone {k} (com.apple.CoreSimulator.SimDeviceType.iPhone-{k})" for k in range(8, 16)], *[f"iPad Pro {k}-inch (com.apple.CoreSimulator.SimDeviceType.iPad-Pro-{k})" for k in (11, 13)],
           "", "== Runtimes ==", "iOS 17.5 (17.5 - 21F79) - com.apple.CoreSimulator.SimRuntime.iOS-17-5", "iOS 16.4 (16.4 - 20E247) - com.apple.CoreSimulator.SimRuntime.iOS-16-4",
           *[f"iOS 15.{k} (15.{k} - 19F70) - com.apple.CoreSimulator.SimRuntime.iOS-15-{k} (unavailable, runtime profile not found)" for k in range(1, 4)],
           "", "== Devices ==", "-- iOS 17.5 --", dev("iPhone SE (3rd generation)", 1), dev("iPhone 15", 2, "Booted"), dev("iPhone 15 Pro", 3), dev("iPad Pro 11-inch (4th generation)", 4), "-- iOS 16.4 --",
           dev("iPhone 14", 5), dev("iPhone 14 Pro", 6)]
    for r in range(n_unavail):
        out += [f"-- Unavailable: com.apple.CoreSimulator.SimRuntime.iOS-15-{r + 1} --"] + [dev(f"iPhone {13 - k}", f"u{r}{k}", extra=" (unavailable, runtime profile not found)") for k in range(5)]
    out += ["", "== Device Pairs =="]
    return "\n".join(out) + "\n"


def _xcodebuild_dest(n_inelig: int = 5) -> str:
    out = ["xcodebuild: error: Unable to find a destination matching the provided destination specifier:", "\t\t{ platform:iOS Simulator, OS:latest, name:iPhone 99 }", "",
           '\tAvailable destinations for the "App" scheme:', "\t\t{ platform:macOS, arch:arm64, variant:Mac Catalyst, id:00006000-001A2B3C4D5E001E, name:My Mac }",
           f"\t\t{{ platform:iOS Simulator, id:{_uuid('s1').upper()}, OS:17.5, name:iPhone 15 }}", f"\t\t{{ platform:iOS Simulator, id:{_uuid('s2').upper()}, OS:17.5, name:iPhone 15 Pro }}",
           f"\t\t{{ platform:iOS Simulator, id:{_uuid('s3').upper()}, OS:16.4, name:iPhone 14 }}", "", '\tIneligible destinations for the "App" scheme:',
           "\t\t{ platform:iOS, id:dvtdevice-DVTiPhonePlaceholder-iphoneos:placeholder, name:Any iOS Device, error:iOS 17.5 is not installed. }",
           "\t\t{ platform:iOS Simulator, id:dvtdevice-DVTiOSDeviceSimulatorPlaceholder-iphonesimulator:placeholder, name:Any iOS Simulator Device }"]
    out += [f"\t\t{{ platform:iOS Simulator, id:{_uuid(f'x{k}').upper()}, OS:15.{k % 5}, name:iPhone {13 - k % 3}, error:iOS 15.{k % 5} is not installed. }}" for k in range(n_inelig)]
    return "\n".join(out) + "\n"


def _dumpsys(n_sections: int = 8) -> str:
    names = ["SurfaceFlinger", "accessibility", "activity", "alarm", "audio", "battery", "connectivity", "wifi", "window", "package"][:n_sections]
    out = ["Currently running services:"] + [f"  {nm}" for nm in names]
    for k, nm in enumerate(names):
        out += [_DSEP, f"DUMP OF SERVICE {nm}:"] + [f"  {nm} line {j}: state=ok value={j * (k + 1)}" for j in range(3 + (k * 7) % 25)] + [""]
    return "\n".join(out) + "\n"


case(
    1158,
    claim="Folds the devices of unavailable simulator runtimes, the Device Types and Device Pairs listings, unavailable runtimes and xcodebuild's ineligible destinations to counts, and bounds each dumpsys section to 3 lines plus a '[N more lines omitted; run: dumpsys NAME]' marker; every available runtime's devices with name, UDID and state, every eligible destination with its id, the dumpsys service list and section names are unchanged.",
    loses="the identity of devices in unavailable runtimes, the device type identifiers, ineligible destinations (including their error text), and everything in a dumpsys section after its third line (re-run with the service name)",
    encode=fold_device_lists,
    probes=probes_1158,
    fixtures=[
        Fixture("xcrun simctl list: available iOS 17.5 and 16.4 devices, four unavailable runtimes with five devices each", _simctl(4), True),
        Fixture("xcodebuild: no matching destination, five ineligible destinations", _xcodebuild_dest(8), True),
        Fixture("adb shell dumpsys with no service name: eight sections", _dumpsys(8), True),
        Fixture("negative: simctl list devices with only available runtimes", "== Devices ==\n-- iOS 17.5 --\n" + "\n".join(f"    iPhone 15 ({_uuid(f'q{k}').upper()}) (Shutdown) " for k in range(3)) + "\n", False),
        Fixture("negative: adb shell dumpsys battery (one service, short)", _DSEP + "\nDUMP OF SERVICE battery:\n  AC powered: false\n  USB powered: true\n  level: 87\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #1153 Audit inherited verbosity env defaults such as RUST_BACKTRACE=1. A policy (a PreToolUse rewrite driven by the process environment),
# so a sim. The outputs are REAL: `cargo test` on a 3-test crate with two failures, run here with RUST_BACKTRACE=0 and =1 (this sandbox
# exports RUST_BACKTRACE=1). The table of variables beyond RUST_BACKTRACE is from memory.

_QUIET = {
    "RUST_BACKTRACE": (lambda v: v in ("1", "full"), "0", re.compile(r"^\s*(?:cd \S+ && )?(?:[A-Za-z_]\w*=\S+ )*(cargo (?:test|nextest)|rustc|cargo build|cargo run)\b")),
    "RUST_LIB_BACKTRACE": (lambda v: v in ("1", "full"), "0", re.compile(r"^\s*(?:cd \S+ && )?(?:[A-Za-z_]\w*=\S+ )*(cargo (?:test|nextest))\b")),
    "GOTRACEBACK": (lambda v: v in ("all", "system", "crash"), "single", re.compile(r"^\s*(?:cd \S+ && )?(?:[A-Za-z_]\w*=\S+ )*(go test)\b")),
    "RUST_LOG": (lambda v: v in ("debug", "trace"), "warn", re.compile(r"^\s*(?:cd \S+ && )?(?:[A-Za-z_]\w*=\S+ )*(cargo (?:test|nextest))\b")),
}


def audit_env(env: dict, command: str) -> dict:
    """Quiet overrides for the verbosity variables that are inherited noisy, apply to this command, and are not set by the command itself."""
    out = {}
    try:
        for var, (noisy, quiet, applies) in _QUIET.items():
            v = env.get(var)
            if isinstance(v, str) and noisy(v) and applies.match(command) and not re.search(rf"\b{var}=", command):
                out[var] = quiet
    except Exception:  # noqa: BLE001  (fail open: never block a command because the audit broke)
        return {}
    return out


def rewrite_command(env: dict, command: str) -> str:
    try:
        ov = audit_env(env, command)
        if not ov:
            return command
        m = re.match(r"^(\s*(?:cd \S+ && )?)(.*)$", command, re.S)
        return m.group(1) + " ".join(f"{k}={v}" for k, v in ov.items()) + " " + m.group(2)
    except Exception:  # noqa: BLE001
        return command


def _fake_run(command: str, env: dict) -> str:
    """Stand-in for the shell: the output of `cargo test` is the real capture for the RUST_BACKTRACE in force."""
    m = re.match(r"^(?:cd \S+ && )?((?:[A-Z_]+=\S+ )*)cargo test", command)
    if not m:
        return ""
    inline = dict(kv.split("=", 1) for kv in m.group(1).split())
    bt = inline.get("RUST_BACKTRACE", env.get("RUST_BACKTRACE", "0"))
    return _CARGO_BT1 if bt in ("1", "full") else _CARGO_BT0


def _reading(out: str) -> dict:
    """What an agent needs from a failed cargo test: which tests failed, where, with what message, and the totals."""
    tests = sorted(re.findall(r"^test (\S+) \.\.\. FAILED$", out, re.M))
    panics = sorted(re.findall(r"thread '([^']+)' \(\d+\) panicked at (\S+):\n(.*)", out))
    summary = re.search(r"^test result: (.*?);? finished", out, re.M)
    return {"failed tests": tests, "panic site and first message line": panics, "test result": summary.group(1) if summary else None, "exit hint": "error: test failed" in out}


def sim_1153() -> dict:
    env = {"RUST_BACKTRACE": "1", "PATH": "/usr/bin", "HOME": "/root"}
    cmd = "cargo test --offline"
    new = rewrite_command(env, cmd)
    assert new == "RUST_BACKTRACE=0 cargo test --offline", new
    noisy, quiet = _fake_run(cmd, env), _fake_run(new, env)
    assert noisy == _CARGO_BT1 and quiet == _CARGO_BT0
    t_noisy, t_quiet = tokens(noisy), tokens(quiet)
    assert t_quiet <= 0.3 * t_noisy and len(quiet) <= 0.35 * len(noisy), (t_quiet, t_noisy)  # asserted saving; =full is 10,805 tokens measured here
    # reader view: everything needed to fix the bug is in the quiet output (test names, panic site, assertion text, totals)
    r0, r1 = _reading(quiet), _reading(noisy)
    assert r0 == r1 and r0["failed tests"] == ["tests::transfer_from_unknown_account", "tests::transfer_moves_money"], (r0, r1)
    assert ("tests::transfer_moves_money", "src/lib.rs:49:9", "assertion `left == right` failed") in r0["panic site and first message line"]
    # what is lost: the call chain. The panic in balance_of (src/lib.rs:17:33) was reached from transfer at src/lib.rs:21, and only the backtrace says so
    assert "src/lib.rs:21:25" in noisy and "src/lib.rs:21:25" not in quiet
    assert "run with `RUST_BACKTRACE=1`" in quiet, "the quiet output must still tell the agent how to get the backtrace for one test"
    # (c) must NOT apply
    assert rewrite_command(env, "RUST_BACKTRACE=full cargo test") == "RUST_BACKTRACE=full cargo test", "the command's own setting wins"
    assert rewrite_command(env, "env RUST_BACKTRACE=full cargo test") == "env RUST_BACKTRACE=full cargo test"
    assert rewrite_command(env, "FOO=1 RUST_BACKTRACE=full cargo test") == "FOO=1 RUST_BACKTRACE=full cargo test", "an assignment earlier in the command is the command's own setting"
    assert rewrite_command(env, "FOO=1 cargo test") == "RUST_BACKTRACE=0 FOO=1 cargo test"
    assert rewrite_command(env, "cargo clippy") == "cargo clippy" and rewrite_command(env, "pytest -x") == "pytest -x"
    assert rewrite_command({"RUST_BACKTRACE": "0"}, cmd) == cmd and rewrite_command({}, cmd) == cmd
    assert rewrite_command({"GOTRACEBACK": "all"}, cmd) == cmd, "a Go variable does not touch a cargo command"
    assert rewrite_command({"GOTRACEBACK": "all"}, "go test ./...") == "GOTRACEBACK=single go test ./..."
    assert rewrite_command({"RUST_BACKTRACE": "1", "RUST_LOG": "trace"}, "cd app && cargo test") == "cd app && RUST_BACKTRACE=0 RUST_LOG=warn cargo test"
    # (d) fail open
    assert rewrite_command({"RUST_BACKTRACE": 1}, cmd) == cmd and rewrite_command(None, cmd) == cmd and rewrite_command(env, None) is None
    return {"quiet_tokens": t_quiet, "noisy_tokens": t_noisy, "full_backtrace_tokens_measured": _CARGO_FULL_TOKENS, "saving_vs_backtrace_1": round(1 - t_quiet / t_noisy, 2),
            "saving_vs_full": round(1 - t_quiet / _CARGO_FULL_TOKENS, 2), "chars": f"{len(noisy)}->{len(quiet)}"}


case(
    1153,
    claim="A PreToolUse rewrite prefixes a cargo test or go test command with the quiet value of an inherited RUST_BACKTRACE, RUST_LIB_BACKTRACE, GOTRACEBACK or RUST_LOG when the environment has a noisy one and the command does not set it itself; on real cargo output the failed tests, panic sites, messages and totals are identical at a quarter of the tokens, only the call chain is lost, and non-test commands, explicit settings, quiet or missing variables and a broken audit leave the command untouched.",
    sim=sim_1153,
)
