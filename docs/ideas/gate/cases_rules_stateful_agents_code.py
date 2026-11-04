"""Gate cases for the shortlisted RULE ideas of the "stateful agents and code" slice:
#127 #141 #142 #189 #206 #209 #288 #304 #305 #387 #449 #485 #503 #579 #593 #684 #946 #1302 #1303 #1324.

Four are text rules with probes (#127 audit/SARIF digest, #206 source skeleton, #209 BM25 chunks, #305 ranked bodies); the other
sixteen depend on state or policy (ledgers, MCP wrappers and gateways, shell shims, PATH, SHELL and BASH_ENV overrides, sparse
worktrees, history rewriting), so they are sims with a reader view, invalidation cases and fail-open. Where the idea is about a
process boundary the sim runs REAL processes in a temp dir: bash, python, git, grep, find, jq, ruff, rg. Rules for those: stdout and
stderr are captured on separate pipes, stdin is /dev/null, and no assertion depends on how two pipeline stages interleave. The
independent oracles are python's ast module (#206, #305), real jq (#304), real git (#1303), real grep/find/ls (#1302), real ruff
(#141) and real bash exit statuses (#189, #579, #946).

What the probes and sims caught or measured (details sit next to each case):
  #127  the npm text report only shrinks 23% (the URL prefix is the one redundancy); SARIF shrinks 87%. A greedy URL regex merged two
        advisories on one mutated line, and a title with a leading space collided with the digest's header syntax.
  #209  pure BM25 lost the failing-test lines and the last line; the rule needs head/tail anchors, an error-signal rescue and
        near-duplicate suppression, and digit-normalising the dedupe key merged two different assertions.
  #288  -41% request tokens, but +14% (batches) to +59% (every turn) billed once prompt caching is counted: every rewrite re-bills the prefix.
  #579  bash ignores BASH_ENV when stdin is a socket; `bash -lc` is covered here (the idea says login shells skip it); a hook without
        a filter check loses all stdout; an unmarked hook changes what nested `$(bash -c ...)` captures.
  #1302 `grep -r` and `rg` do not follow symlinks, so a symlink mirror is empty to them; hard links work.
  #684, #304, #503 token savings depend on prompt caching: a cache-aware model is used next to the raw counts.
Everything here is a prototype of the idea, not shipped code.
"""
import ast
import base64
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import warnings
from collections import Counter

from harness import Fixture, case, tokens


def _lcg(seed: int):
    """Tiny deterministic generator so fixtures are stable without importing random."""
    s = seed
    while True:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        yield s >> 8


def _saving(before: str, after: str) -> dict:
    """Characters and o200k-or-proxy tokens before/after, as the numbers a sim returns."""
    cb, ca, tb, ta = len(before), len(after), tokens(before), tokens(after)
    return {"chars": f"{cb}->{ca} ({-100 * (cb - ca) / max(1, cb):+.0f}%)", "tokens": f"{tb}->{ta} ({-100 * (tb - ta) / max(1, tb):+.0f}%)"}


def _sha(s: str | bytes, n: int = 8) -> str:
    return hashlib.sha1(s.encode() if isinstance(s, str) else s).hexdigest()[:n]


# ---------------------------------------------------------------------------------------------------------------
# BM25 (shared by #209, #305, #1324). Checked against a hand-worked example in `_bm25_selfcheck`.

_STOP = frozenset("a an and are as at be but by for if in into is it of on or such that the their then there these they this to was will with how what why when which who does do did not no".split())


def _terms(text: str) -> list[str]:
    """Lower-cased word pieces; identifiers are split on case and underscores (fetchWithRetry -> fetch with retry)."""
    out = []
    for w in re.findall(r"[A-Za-z]+|\d+", text):
        for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", w):
            p = p.lower()
            if p not in _STOP and len(p) > 1:
                out.append(p)
    return out


def _bm25(query: list[str], docs: list[list[str]], k1: float = 1.2, b: float = 0.75) -> list[float]:
    n = len(docs)
    avg = sum(len(d) for d in docs) / max(1, n)
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(query):
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / max(avg, 1e-9)))
        scores.append(s)
    return scores


def _bm25_selfcheck() -> None:
    """Hand-worked: two docs, one query term. idf = ln(1 + (2-1+.5)/(1+.5)) = ln 2; doc1 tf=2, len 3; doc2 has no hit."""
    docs = [["cat", "cat", "dog"], ["bird", "fish", "frog"]]
    s = _bm25(["cat"], docs)
    avg = 3.0
    want = math.log(2) * 2 * 2.2 / (2 + 1.2 * (1 - 0.75 + 0.75 * 3 / avg))
    assert abs(s[0] - want) < 1e-12 and s[1] == 0.0, (s, want)
    assert _terms("fetchWithRetry_v2 gives up") == ["fetch", "retry", "gives", "up"], _terms("fetchWithRetry_v2 gives up")


_bm25_selfcheck()


# ---------------------------------------------------------------------------------------------------------------
# real processes

def _run(argv, cwd=None, env=None, stdin: bytes | None = None, timeout: int = 20, stdin_obj=None):
    """Run a real process. stdout and stderr are separate pipes (never merged), so nothing here depends on interleaving.
    stdin is /dev/null unless given: an INHERITED stdin made #579 flaky (bash ignores BASH_ENV when stdin is a socket, and the
    harness that runs a gate may hand its children a socket, a pipe or /dev/null)."""
    e = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": cwd or "/tmp", **(env or {})}
    kw = {"stdin": stdin_obj} if stdin_obj is not None else {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
    p = subprocess.run(argv, cwd=cwd, env=e, capture_output=True, timeout=timeout, **kw)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _write(path: str, text: str, mode: int | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)
    return path


# ===============================================================================================================
# #127 One line per finding for npm audit and SARIF scanners (RULE)
#
# Two parsers: npm audit's text report (the 'detail' reporter of npm-audit-report: package header, Severity line,
# 'title - URL' per advisory, fix lines, node paths) and SARIF 2.1.0 (semgrep, CodeQL, trivy and grype can all emit it).
# The digest keeps identity (package + range + severity + GHSA id + title + fix command + 'Will install pkg@ver'; or
# file:line + level + rule + message) and drops what a reader never acts on (the URL prefix of every advisory, the
# 'Severity:' and 'fix available via' boilerplate, SARIF fingerprints, snippets, per-rule help text, CWE tag arrays,
# helpUri, invocation records). Anything it does not recognise passes through byte for byte.
# Probes parse BOTH forms with their own state machine / json walk and must give identical answers.
#
# FIXTURES: the npm text was produced by the REAL npm-audit-report 'detail' reporter that ships with npm 10.9.9 (run
# under node here), fed an audit-JSON whose package names are real and whose advisory ids and titles are reconstructed from
# memory (the shape is exact, individual GHSA ids are illustrative). The SARIF is generated in the shape semgrep and
# CodeQL emit (reconstructed from memory; no scanner is installed offline).
#
# MEASURED, not as claimed: the idea cites 'handlebars 4.0.5: 20 advisory lines in 4,915 chars'. The fixture below has 23
# advisory lines in 3,597 chars, digest -24% chars / -22% o200k tokens. The URL prefix (30 chars of a ~100-char line)
# is the only big redundancy in the text report, so ~25% is what a faithful rule gets on npm; SARIF is where the
# rule pays (-90%). pip-audit's default columns table is already one line per finding (nothing to gain), and trivy and
# grype boxed tables are a different parser that this case does not claim.

_GHSA = r"GHSA-[2-9a-z]{4}-[2-9a-z]{4}-[2-9a-z]{4}"
_NPM_HDR = re.compile(r"^(@?[\w.~/-]+)  (\S.*?)(?: \((low|moderate|high|critical|info)\))?$")
_NPM_SEV = re.compile(r"^Severity: (low|moderate|high|critical|info)$")
_NPM_ADV = re.compile(r"^(\S(?:(?! - https://).)*) - https://github\.com/advisories/(" + _GHSA + r")$")  # a title holding a second URL is left alone
_NPM_FIX = re.compile(r"^fix available via `(npm audit fix(?: --force)?)`$")


def digest_npm_audit(text: str) -> str:
    lines = text.split("\n")
    if not lines or lines[0] != "# npm audit report":
        return text
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        h = _NPM_HDR.match(l)
        nxt = _NPM_SEV.match(lines[i + 1]) if i + 1 < len(lines) else None
        if h and not h.group(3) and nxt and not l.startswith(" "):
            out.append(f"{l} ({nxt.group(1)})")  # severity folded into the header line
            i += 2
            continue
        m = _NPM_ADV.match(l)
        if m:
            out.append(f" {m.group(2)} {m.group(1)}")
        elif _NPM_FIX.match(l):
            out.append(f" fix: {_NPM_FIX.match(l).group(1)}")
        else:
            out.append(l)
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _sarif_level(res: dict, rules: list) -> str:
    lv = res.get("level")
    if lv:
        return lv
    idx = res.get("ruleIndex")
    rule = rules[idx] if isinstance(idx, int) and 0 <= idx < len(rules) and rules[idx].get("id") == res.get("ruleId") else None
    if rule is None:
        rule = next((r for r in rules if r.get("id") == res.get("ruleId")), {})
    return (rule.get("defaultConfiguration") or {}).get("level") or "warning"


def digest_sarif(text: str) -> str:
    if not text.lstrip().startswith("{"):
        return text
    try:
        doc = json.loads(text)
        if doc.get("version") != "2.1.0" or not isinstance(doc["runs"], list) or not doc["runs"]:
            return text
        out = []
        for run in doc["runs"]:
            drv = run["tool"]["driver"]
            rules = drv.get("rules") or []
            results = run.get("results") or []
            out.append(f"sarif {drv['name']} {drv.get('semanticVersion') or drv.get('version') or '?'}: {len(results)} results")
            for res in results:
                pl = res["locations"][0]["physicalLocation"]
                uri, reg = pl["artifactLocation"]["uri"], pl["region"]
                sl = reg["startLine"]
                el = reg.get("endLine", sl)
                rid, msg = res["ruleId"], " ".join(res["message"]["text"].split())
                if not all(isinstance(v, int) and not isinstance(v, bool) for v in (sl, el)):
                    return text
                if any(not isinstance(v, str) or not v or re.search(r"\s", v) for v in (uri, rid)) or not msg:
                    return text
                where = f"{uri}:{sl}" + (f"-{el}" if el != sl else "")
                tail = (" [suppressed]" if res.get("suppressions") else "") + (f" (+{len(res['locations']) - 1} locations)" if len(res["locations"]) > 1 else "")
                out.append(f"{where} {_sarif_level(res, rules)} {rid} {msg}{tail}")
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return text
    res_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return res_text if len(res_text) < len(text) else text


def digest_findings(text: str) -> str:
    t = digest_npm_audit(text)
    return t if t != text else digest_sarif(text)


# --- probes: a line state machine over both npm forms; a json walk or a line regex over both SARIF forms.

_P_HDR = re.compile(r"^(\s*)(@?[\w.~/-]+)  (\S.*?)(?: \((low|moderate|high|critical|info)\))?$")
_P_TITLE_URL = " - https://github.com/advisories/"


def _probe_npm(text: str) -> dict:
    pkg, sev = None, None
    adv, fix, dep, paths, summary = [], {}, [], {}, []
    started = False
    for line in text.split("\n"):
        if line == "# npm audit report":
            started = True
            continue
        if not started:
            continue
        m = None if re.match(r"^ " + _GHSA + " ", line) else _P_HDR.match(line)
        if m and "https://" not in line and not line.strip().startswith(("fix", "Will", "Depends", "node_modules")):
            pkg, sev = m.group(2), m.group(4)
            paths.setdefault(pkg, [])
            continue
        s = line.strip()
        if s.startswith("Severity: "):
            sev = s[len("Severity: "):]
        elif re.match(r"^ " + _GHSA + " ", line) and pkg is not None:
            adv.append((pkg, sev, line[1:].split(" ", 1)[0], line[1:].split(" ", 1)[1]))
        elif _P_TITLE_URL in line and pkg is not None:
            title, _, url = line.rpartition(_P_TITLE_URL)
            adv.append((pkg, sev, url, title))
        elif s.startswith("fix available via `") or s.startswith("fix: npm audit fix"):
            fix[pkg or ""] = fix.get(pkg or "", ()) + (s.replace("fix available via `", "").rstrip("`").replace("fix: ", ""),)
        elif s.startswith(("Will install", "No fix available")):
            fix[pkg or ""] = fix.get(pkg or "", ()) + (s,)
        elif s.startswith("Depends on vulnerable versions of "):
            dep.append((pkg or "", s.rsplit(" ", 1)[1]))
        elif s.startswith("node_modules/") and pkg is not None:
            paths[pkg].append(s)
        elif re.match(r"^\d+ (low|moderate|high|critical)|^found \d+|^To address|^  npm audit|^Some issues|^a different", line):
            pkg = None
            summary.append(line)
    return {"advisories (pkg, severity, id, title)": adv, "fix per package": sorted(fix.items()), "depends on": dep,
            "installed at": sorted((k, tuple(v)) for k, v in paths.items()), "summary and fix commands": summary}


def _probe_sarif(text: str):
    try:
        doc = json.loads(text)
        runs = doc["runs"]
        assert doc["version"] == "2.1.0"
    except Exception:  # noqa: BLE001
        runs = None
    found = []
    if runs is not None:
        try:
            for run in runs:
                lv_by_id = {r["id"]: (r.get("defaultConfiguration") or {}).get("level") for r in run["tool"]["driver"].get("rules") or []}
                drv = run["tool"]["driver"]
                found.append(("tool", drv["name"], drv.get("semanticVersion") or drv.get("version") or "?", len(run.get("results") or [])))
                for r in run.get("results") or []:
                    p = r["locations"][0]["physicalLocation"]
                    sl = p["region"]["startLine"]
                    el = p["region"].get("endLine", sl)
                    found.append((p["artifactLocation"]["uri"], sl, el, r.get("level") or lv_by_id.get(r["ruleId"]) or "warning", r["ruleId"],
                                  " ".join(r["message"]["text"].split()), bool(r.get("suppressions")), len(r["locations"])))
            return found
        except (KeyError, TypeError, IndexError, AttributeError):
            return None
    rx = re.compile(r"^(\S+?):(\d+)(?:-(\d+))? (error|warning|note|none) (\S+) (.*?)( \[suppressed\])?(?: \(\+(\d+) locations\))?$")
    for line in text.split("\n"):
        h = re.match(r"^sarif (.+) (\S+): (\d+) results$", line)
        if h:
            found.append(("tool", h.group(1), h.group(2), int(h.group(3))))
        elif m := rx.match(line):
            sl = int(m.group(2))
            found.append((m.group(1), sl, int(m.group(3) or sl), m.group(4), m.group(5), m.group(6), bool(m.group(7)), 1 + int(m.group(8) or 0)))
    return found if found else None


def probes_127(text: str) -> dict:
    return {"npm audit facts": _probe_npm(text), "sarif findings": _probe_sarif(text)}


_NPM_FULL = """\
# npm audit report

handlebars  4.0.0 - 4.7.8
Severity: critical
Arbitrary Code Execution in Handlebars - https://github.com/advisories/GHSA-q2c6-c6pm-g3gh
Prototype Pollution in handlebars - https://github.com/advisories/GHSA-765h-qjxv-5f44
Remote Code Execution in handlebars when compiling templates - https://github.com/advisories/GHSA-f2jv-r9rf-7988
Prototype Pollution in handlebars - https://github.com/advisories/GHSA-w457-6q6x-cgp9
Denial of Service in Handlebars - https://github.com/advisories/GHSA-9prh-257w-9277
Regular Expression Denial of Service in handlebars - https://github.com/advisories/GHSA-2cf5-4w76-r9qv
Arbitrary Code Execution in handlebars - https://github.com/advisories/GHSA-3cqr-58rm-57f8
Prototype pollution in handlebars via lookup helper - https://github.com/advisories/GHSA-g9r4-xpmj-mj65
Cross-site Scripting in handlebars - https://github.com/advisories/GHSA-62gr-4qp9-h98f
Handlebars allows remote code execution through compile of untrusted templates - https://github.com/advisories/GHSA-3mfm-83xf-c92r
Handlebars has a prototype pollution leading to denial of service - https://github.com/advisories/GHSA-xhpv-hc6g-r9c6
Handlebars template injection via partial name - https://github.com/advisories/GHSA-2w6w-674q-4c4q
fix available via `npm audit fix --force`
Will install handlebars@4.7.8, which is outside the stated dependency range
node_modules/handlebars
  hbs-loader  *
  Depends on vulnerable versions of handlebars
  node_modules/hbs-loader

lodash  <=4.17.20
Severity: high
Prototype Pollution in lodash - https://github.com/advisories/GHSA-p6mc-m468-83gw
Command Injection in lodash - https://github.com/advisories/GHSA-35jh-r3h4-6jhm
Regular Expression Denial of Service (ReDoS) in lodash - https://github.com/advisories/GHSA-29mw-wpgm-hmr9
Prototype Pollution in lodash - https://github.com/advisories/GHSA-4xc9-xhrj-v574
fix available via `npm audit fix`
node_modules/lodash

minimist  <1.2.6
Severity: critical
Prototype Pollution in minimist - https://github.com/advisories/GHSA-xvch-5gv4-984h
Prototype Pollution in minimist - https://github.com/advisories/GHSA-vh95-rmgr-6w4m
fix available via `npm audit fix`
node_modules/minimist
node_modules/mkdirp/node_modules/minimist

semver  7.0.0 - 7.5.1
Severity: high
semver vulnerable to Regular Expression Denial of Service - https://github.com/advisories/GHSA-c2qf-rxjj-qqgw
fix available via `npm audit fix`
node_modules/semver

follow-redirects  <=1.15.5
Severity: moderate
Follow Redirects improperly handles URLs in the url.parse() function - https://github.com/advisories/GHSA-jchw-25xp-jwwc
follow-redirects leaks Custom Authentication Headers to Cross-Domain Redirect Targets - https://github.com/advisories/GHSA-cxjh-pqwp-8mfp
fix available via `npm audit fix`
node_modules/follow-redirects

debug  <2.6.9
Regular Expression Denial of Service in debug - https://github.com/advisories/GHSA-gxpj-cx7g-858c
No fix available
node_modules/debug

tough-cookie  <4.1.3
Severity: moderate
tough-cookie Prototype Pollution vulnerability - https://github.com/advisories/GHSA-72xf-g2v4-qvf3
fix available via `npm audit fix --force`
Will install request@2.88.2, which is a breaking change
node_modules/tough-cookie
  request  *
  Depends on vulnerable versions of tough-cookie
  node_modules/request

9 vulnerabilities (1 low, 3 moderate, 2 high, 3 critical)

To address issues that do not require attention, run:
  npm audit fix

To address all issues possible (including breaking changes), run:
  npm audit fix --force

Some issues need review, and may require choosing
a different dependency.
"""

_NPM_ONE = """\
# npm audit report

lodash  <=4.17.20
Severity: high
Prototype Pollution in lodash - https://github.com/advisories/GHSA-p6mc-m468-83gw
Command Injection in lodash - https://github.com/advisories/GHSA-35jh-r3h4-6jhm
Regular Expression Denial of Service (ReDoS) in lodash - https://github.com/advisories/GHSA-29mw-wpgm-hmr9
Prototype Pollution in lodash - https://github.com/advisories/GHSA-4xc9-xhrj-v574
fix available via `npm audit fix`
node_modules/lodash

1 high severity vulnerability

To address all issues, run:
  npm audit fix
"""

_NPM_LOW = """\
# npm audit report

debug  <2.6.9
Regular Expression Denial of Service in debug - https://github.com/advisories/GHSA-gxpj-cx7g-858c
No fix available
node_modules/debug

1 low severity vulnerability

Some issues need review, and may require choosing
a different dependency.
"""

def _audit_fixtures() -> list[Fixture]:
    full = _NPM_FULL
    one = _NPM_ONE
    low = _NPM_LOW
    none = "found 0 vulnerabilities\n"
    gen = _lcg(127)
    rules = [
        ("generic.secrets.security.detected-generic-secret.detected-generic-secret", "error", "Generic Secret detected"),
        ("python.lang.security.audit.formatted-sql-query.formatted-sql-query", "error", "Detected possible formatted SQL query. Use parameterized queries instead."),
        ("python.lang.security.audit.subprocess-shell-true.subprocess-shell-true", "warning",
         "Found 'subprocess' function 'run' with 'shell=True'. This is dangerous because this call will spawn the command using a shell process. Doing so propagates current shell settings and variables, which makes it much easier for a malicious actor to execute commands. Use 'shell=False' instead."),
        ("python.requests.security.disabled-cert-validation.disabled-cert-validation", "warning",
         "Certificate verification has been explicitly disabled. This permits insecure connections to insecure servers. Re-enable certification validation."),
        ("python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-md5", "warning",
         "Detected MD5 hash algorithm which is considered insecure. MD5 is not collision resistant and is therefore not suitable as a cryptographic signature. Use SHA256 or SHA3 instead."),
    ]

    def sarif(n: int, indent=2, first_rule=0, last_rule=0, suppressed_at=None) -> str:
        used = sorted({first_rule, last_rule, *range(len(rules))})
        results = []
        for k in range(n):
            ri = first_rule if k == 0 else last_rule if k == n - 1 else next(gen) % len(rules)
            f = ["config/settings.py", "app/db.py", "app/jobs/runner.py", "app/http/client.py", "app/auth/tokens.py", "scripts/deploy.py"][k % 6] if 0 < k < n - 1 else ("config/settings.py" if k == 0 else "tests/test_legacy.py")
            line = 7 + k * 13 + (next(gen) % 9)
            res = {"fingerprints": {"matchBasedId/v1": _sha(f"{f}{line}{ri}", 40) * 2},
                   "locations": [{"physicalLocation": {"artifactLocation": {"uri": f, "uriBaseId": "%SRCROOT%"},
                                                       "region": {"endColumn": 44, "endLine": line + (k % 3 == 0), "snippet": {"text": f"    cursor.execute(f\"SELECT * FROM users WHERE id = {{uid}}\")  # {k}"},
                                                                  "startColumn": 9, "startLine": line}}}],
                   "message": {"text": rules[ri][2]}, "properties": {}, "ruleId": rules[ri][0]}
            if suppressed_at == k:
                res["suppressions"] = [{"kind": "inSource"}]
            results.append(res)
        rdefs = [{"defaultConfiguration": {"level": rules[i][1]}, "fullDescription": {"text": rules[i][2]},
                  "help": {"markdown": f"{rules[i][2]}\n\n<b>References:</b>\n - [Semgrep Rule](https://semgrep.dev/r/{rules[i][0]})\n - [https://owasp.org/Top10/A03_2021-Injection/](https://owasp.org/Top10/A03_2021-Injection/)\n - [https://cwe.mitre.org/data/definitions/89.html](https://cwe.mitre.org/data/definitions/89.html)\n",
                           "text": f"Rule: {rules[i][0]}\n{rules[i][2]}"},
                  "helpUri": f"https://semgrep.dev/r/{rules[i][0]}", "id": rules[i][0], "name": rules[i][0],
                  "properties": {"precision": "very-high", "tags": ["CWE-89: Improper Neutralization of Special Elements used in an SQL Command ('SQL Injection')", "OWASP-A01:2017 - Injection", "OWASP-A03:2021 - Injection", "security"]},
                  "shortDescription": {"text": f"Semgrep Finding: {rules[i][0]}"}} for i in used]
        for r in results:
            r["ruleIndex"] = used.index(next(i for i in used if rules[i][0] == r["ruleId"]))
        doc = {"$schema": "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json",
               "runs": [{"invocations": [{"executionSuccessful": True, "toolExecutionNotifications": []}], "results": results,
                         "tool": {"driver": {"name": "Semgrep OSS", "rules": rdefs, "semanticVersion": "1.75.0"}}}], "version": "2.1.0"}
        return json.dumps(doc, indent=indent, sort_keys=True) + "\n"

    sa = sarif(14, first_rule=0, last_rule=1, suppressed_at=6)
    return [
        Fixture("npm audit, 9 packages, 23 advisories (critical at start, middle and end), real reporter format", full, True),
        Fixture("SARIF 2.1.0 (semgrep shape), 14 results, secret first, SQL injection last, one suppressed", sa, True),
        Fixture("SARIF, compact single line", json.dumps(json.loads(sa), separators=(",", ":"), sort_keys=True) + "\n", True),
        Fixture("npm audit, one package, four advisories", one, None),
        Fixture("npm audit, one low finding with 'No fix available'", low, None),
        Fixture("npm audit: found 0 vulnerabilities", none, False),
        Fixture("some other JSON (package.json), not SARIF", json.dumps({"name": "app", "version": "1.0.0", "dependencies": {"lodash": "^4.17.20", "handlebars": "4.0.5"}}, indent=2) + "\n", False),
        Fixture("npm ERR! audit endpoint failure", "npm ERR! code ENOTFOUND\nnpm ERR! audit request to https://registry.npmjs.org/-/npm/v1/security/audits failed, reason: getaddrinfo ENOTFOUND registry.npmjs.org\n", False),
        Fixture("SARIF with zero results is NOT a negative: it becomes one line", sarif(0, first_rule=0, last_rule=0).replace('"results": [],', '"results": [],'), None),
    ]


case(
    127,
    claim="npm audit text and SARIF 2.1.0 become one compact line per finding (package+range+severity+GHSA id+title+fix, or file:line+level+rule+message); URL prefixes, boilerplate, fingerprints, snippets, rule help and tag arrays are dropped; every finding identity and fix command survives and anything unrecognised passes through unchanged.",
    loses="the advisory URL (derivable from the GHSA id), 'Severity:'/'fix available via' wording, SARIF fingerprints, code snippets, rule help/markdown/tags/helpUri, invocation records, secondary locations (only counted) and message whitespace",
    encode=digest_findings,
    probes=probes_127,
    fixtures=_audit_fixtures,
    min_gain=0.2,
)


# ===============================================================================================================
# #206 Signature-only skeleton for large source reads, #305 query-ranked function bodies (RULES, ast as the oracle)
#
# Input is what a Read/cat of a Python file gives: either plain text or 'cat -n' numbered lines (spaces, digits, TAB or
# the arrow Claude Code prints). #305 additionally carries the question: a first line '@query <text>' (the hook knows the
# prompt and the command; the gate has only text, so the query travels in-band).
#
# Skeleton: every function/method body of 3+ lines (module level and class bodies, recursively through classes) is
# replaced by ONE line '...  # lines A-B' at body indentation, where A-B is the exact line range that was removed, so the
# elided lines can be re-read with offset/limit. A multi-line docstring is cut to its first line (a one-line docstring is
# kept). Everything else (imports, constants, class headers, decorators, signatures, one-line bodies) is verbatim. In
# numbered input every kept line keeps its ORIGINAL number. The skeleton is still valid Python (the oracle parses it).
# Fail-open (output == input) when: fewer than 300 lines; the text is not parseable Python; it contains CR; a numbered read
# does not start at line 1 (a ranged read is what an agent does BEFORE it edits: never touch it); or the saving is < 25%.
# 'Source that will be edited' is only knowable in-band through that last signal (and the size/parse gates); the session
# policy (a file that was already skeleton-read, or edited, is read verbatim) is state, not text, and is NOT claimed here.

MIN_LINES, MIN_ELIDE, MIN_SAVING = 300, 3, 0.25
_NUMBERED = re.compile(r"^( *)(\d+)([\t→])(.*)$")


def _parse_input(text: str):
    """(query or None, numbered format (pad width, sep) or None, first line number, code lines, trailing newline)."""
    query = None
    if text.startswith("@query "):
        head, _, text = text.partition("\n")
        query = head[len("@query "):]
    nl = text.endswith("\n")
    lines = text.split("\n")[:-1] if nl else text.split("\n")
    ms = [_NUMBERED.match(l) for l in lines]
    if lines and all(ms):
        nums = [int(m.group(2)) for m in ms]
        if nums == list(range(nums[0], nums[0] + len(nums))):
            return query, (len(ms[0].group(1)) + len(ms[0].group(2)), ms[0].group(3)), nums[0], [m.group(4) for m in ms], nl
    return query, None, 1, lines, nl


def _parse(src: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # mutated source is full of invalid escape sequences; that is not what is tested
        return ast.parse(src)


def _is_doc(stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _funcs(tree) -> list:
    """(qualname, node) for every def at module level or inside (nested) class bodies, in source order."""
    out = []

    def walk(body, prefix):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + st.name, st))
            elif isinstance(st, ast.ClassDef):
                walk(st.body, prefix + st.name + ".")

    walk(tree.body, "")
    return out


def _plan(node, lines: list[str]):
    """Elision plan for one function: (first replaced line, last replaced line, emitted lines [(number, text)]) or None."""
    body = node.body
    first = body[0]
    if lines[first.lineno - 1][: first.col_offset].strip():
        return None  # the body starts on the def line: `def f(): return 1`
    indent = lines[first.lineno - 1][: first.col_offset]
    end = node.end_lineno
    if _is_doc(first):
        summary = next((s.strip() for s in (ast.get_docstring(node, clean=True) or "").splitlines() if s.strip()), None)
        a = first.lineno + 1
        if end - a + 1 < MIN_ELIDE or summary is None:
            return None
        if first.end_lineno > first.lineno:  # multi-line docstring: summary line + marker
            return first.lineno, end, [(first.lineno, indent + repr(summary)), (a, f"{indent}...  # lines {a}-{end}")]
        return a, end, [(a, f"{indent}...  # lines {a}-{end}")]
    if end - first.lineno + 1 < MIN_ELIDE:
        return None
    return first.lineno, end, [(first.lineno, f"{indent}...  # lines {first.lineno}-{end}")]


def _render(plans, lines: list[str], fmt, n0: int, nl: bool) -> str:
    plans = sorted(plans)
    out, i, pi = [], 1, 0
    emit = (lambda n, t: f"{n:>{fmt[0]}}{fmt[1]}{t}") if fmt else (lambda n, t: t)
    while i <= len(lines):
        if pi < len(plans) and plans[pi][0] == i:
            for n, t in plans[pi][2]:
                out.append(emit(n + n0 - 1, t))
            i = plans[pi][1] + 1
            pi += 1
        else:
            out.append(emit(i + n0 - 1, lines[i - 1]))
            i += 1
    return "\n".join(out) + ("\n" if nl else "")


def _skeletonise(text: str, ranked: bool):
    query, fmt, n0, lines, nl = _parse_input(text)
    if ranked and query is None:
        return text
    if len(lines) < MIN_LINES or n0 != 1 or any("\r" in l for l in lines):
        return text
    try:
        tree = _parse("\n".join(lines))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return text
    funcs = _funcs(tree)
    keep: set[int] = set()
    if ranked:
        q = _terms(query)
        docs = [_terms(qn.split(".")[-1]) * 3 + _terms(qn.replace(".", " ")) + _terms("\n".join(lines[n.lineno - 1:n.end_lineno])) for qn, n in funcs]
        scores = _bm25(q, docs) if funcs else []
        order = sorted(range(len(funcs)), key=lambda i: (-scores[i], i))
        if not order or scores[order[0]] <= 0:
            return text  # nothing in the file matches the question: no basis to hide anything
        budget = 90
        for i in order[:3]:
            n = funcs[i][1]
            size = n.end_lineno - n.lineno + 1
            if scores[i] > 0 and (not keep or size <= budget):
                keep.add(id(n))
                budget -= size
    plans = []
    for _qn, n in funcs:
        if id(n) in keep:
            continue
        p = _plan(n, lines)
        if p:
            plans.append(p)
    if not plans:
        return text
    out = _render(plans, lines, fmt, n0, nl)
    if query is not None:
        out = f"@query {query}\n" + out
    return out if len(out) <= (1 - MIN_SAVING) * len(text) else text


def skeleton(text: str) -> str:
    return _skeletonise(text, ranked=False)


def ranked_bodies(text: str) -> str:
    return _skeletonise(text, ranked=True)


# --- probes: python's ast over the text (original or skeleton) and, for the skeleton, the marker comments.

_MARK = re.compile(r"^\s*\.\.\.  # lines (\d+)-(\d+)$")


def _probe_source(text: str, with_query: bool) -> dict:
    q = None
    if with_query and text.startswith("@query "):
        head, _, text = text.partition("\n")
        q = set(_terms(head[7:]))
    raw = text.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    rows = [re.match(r"^ *(\d+)[\t→](.*)$", l) for l in raw]
    numbered = bool(raw) and all(rows)
    code = [m.group(2) for m in rows] if numbered else raw
    prefix = [int(m.group(1)) for m in rows] if numbered else None
    try:
        tree = _parse("\n".join(code))
    except (SyntaxError, ValueError):
        return {"parses": False, "text": _sha(text, 12)}  # unparseable text must come back untouched
    base = prefix[0] if numbered else 1
    shift, orig_no, markers = 0, {}, {}
    for i, l in enumerate(code, 1):  # original line number of every kept line, from the markers alone
        orig_no[i] = i + base - 1 + shift
        m = _MARK.match(l)
        if m:
            markers[i] = (int(m.group(1)), int(m.group(2)))
            shift += int(m.group(2)) - int(m.group(1))
    numbering_ok = (not numbered) or all(prefix[i - 1] == orig_no[i] for i in orig_no)
    out_funcs, classes, imports, assigned = [], [], [], []
    stack = [("", tree.body)]
    while stack:
        prefix_name, body = stack.pop(0)
        for st in body:
            if isinstance(st, (ast.Import, ast.ImportFrom)) and prefix_name == "":
                imports.append(ast.unparse(st))
            elif isinstance(st, (ast.Assign, ast.AnnAssign)) and prefix_name == "":
                assigned.append(ast.unparse(st.targets[0] if isinstance(st, ast.Assign) else st.target))
            elif isinstance(st, ast.ClassDef):
                classes.append((prefix_name + st.name, orig_no[st.lineno], tuple(ast.unparse(b) for b in st.bases),
                                tuple(ast.unparse(d) for d in st.decorator_list), (ast.get_docstring(st) or "").strip().split("\n")[0]))
                stack.append((prefix_name + st.name + ".", st.body))
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body0 = st.body[0]
                has_doc = _is_doc(body0)
                doc_first = (ast.get_docstring(st) or "").strip().split("\n")[0]
                # body range: a marker line if the body was elided, otherwise derived from the verbatim statements
                mk = [markers[i] for i in range(body0.lineno, st.end_lineno + 1) if i in markers]
                if mk:
                    a, b = mk[0]
                else:
                    a = orig_no[body0.lineno] + (1 if has_doc else 0)
                    b = orig_no[st.end_lineno]
                sig = ast.unparse(st.args) + (" -> " + ast.unparse(st.returns) if st.returns else "")
                entry = {"def": orig_no[st.lineno], "sig": sig, "async": isinstance(st, ast.AsyncFunctionDef),
                         "decorators": tuple(ast.unparse(d) for d in st.decorator_list), "doc": doc_first, "range": (a, b)}
                name = prefix_name + st.name
                if q is not None and set(_terms(st.name)) and set(_terms(st.name)) <= q:
                    # a function the question names: its whole body must be readable, not just locatable
                    body_src = code[st.body[0].lineno - 1 + (1 if has_doc else 0): st.end_lineno] if not mk else None
                    entry["named in the question -> body"] = _sha("\n".join(body_src), 12) if body_src is not None else "ELIDED"
                out_funcs.append((name, entry))
    return {"parses": True, "numbering matches markers": numbering_ok, "imports": imports, "module assignments": assigned,
            "classes (qualname, line, bases, decorators, doc)": classes, "functions (qualname, signature, range, doc)": out_funcs}


def probes_206(text: str) -> dict:
    return _probe_source(text, with_query=False)


def probes_305(text: str) -> dict:
    return _probe_source(text, with_query=True)


# --- fixtures: a generated module with hand-written head (target of the questions) and many generated service methods,
# plus real stdlib sources (textwrap, shlex) and the repo's own code when present.

_HEAD = '''"""HTTP client helpers with retry, caching and pagination.

Used by the sync jobs. Everything here is synchronous; the async variants live in aio.py.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 3
RETRY_STATUS = {429, 500, 502, 503, 504}


class RetryError(RuntimeError):
    """Raised when every attempt failed.

    Carries the url, the number of attempts and the last exception so the caller can decide.
    """

    def __init__(self, url: str, attempts: int, last: Exception | None = None) -> None:
        super().__init__(f"{url}: gave up after {attempts} attempts")
        self.url = url
        self.attempts = attempts
        self.last = last


@dataclass
class Backoff:
    """Exponential backoff with a cap and jitter."""

    base: float = 0.5
    factor: float = 2.0
    cap: float = 30.0
    seen: list[float] = field(default_factory=list)

    def delay(self, attempt: int) -> float:
        """Seconds to sleep before attempt number `attempt` (1-based)."""
        raw = self.base * self.factor ** (attempt - 1)
        wait = min(raw, self.cap)
        self.seen.append(wait)
        return wait

    @property
    def total(self) -> float:
        return sum(self.seen)


def fetch_with_retry(
    url: str,
    *,
    session: requests.Session | None = None,
    retries: int = MAX_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
    backoff: Backoff | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Fetch `url`, retrying on timeouts and retryable status codes."""
    session = session or requests.Session()
    backoff = backoff or Backoff()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
        except requests.Timeout as exc:
            last = exc
            log.warning("timeout on %s (attempt %d/%d)", url, attempt, retries)
            sleep(backoff.delay(attempt))
            continue
        if resp.status_code in RETRY_STATUS:
            last = RuntimeError(f"status {resp.status_code}")
            log.warning("retryable status %d on %s", resp.status_code, url)
            sleep(backoff.delay(attempt))
            continue
        return resp
    raise RetryError(url, retries, last)


def clamp(x, lo=0, hi=100): return max(lo, min(hi, (x
    if x is not None
    else lo)))


def parse_link_header(value: str) -> dict[str, str]:
    """Parse an RFC 8288 Link header into {rel: url}."""
    out: dict[str, str] = {}
    for part in value.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        url = bits[0].strip().strip("<>")
        for b in bits[1:]:
            k, _, v = b.strip().partition("=")
            if k == "rel":
                out[v.strip('"')] = url
    return out


async def drain(queue, handler: Callable[[Any], Any], *, limit: int = 100) -> int:
    """Pull up to `limit` items off an asyncio queue and hand each to `handler`."""
    n = 0
    while n < limit and not queue.empty():
        item = await queue.get()
        handler(item)
        n += 1
    return n

'''

_NOUNS = ["user", "order", "invoice", "ticket", "project", "webhook", "device", "report", "comment", "label", "branch", "release"]
_METHODS = [
    ("get_{n}", "self, item_id: int", ['"""Fetch one {n} by id."""', 'resp = self._request("GET", f"/{n}s/{{item_id}}")', "if resp.status_code == 404:", "    return None", "resp.raise_for_status()", 'return {{"id": item_id, **resp.json()}}']),
    ("list_{n}s", "self, *, state: str = 'open'", ['"""List {n}s, newest first."""', "out: list[dict] = []", "page = 1", "while True:", '    resp = self._request("GET", "/{n}s", params={{"state": state, "page": page, "per_page": 100}})', "    resp.raise_for_status()", "    rows = resp.json()", "    if not rows:", "        break", "    out.extend(rows)", "    page += 1", "return out"]),
    ("create_{n}", "self, payload: dict", ['"""Create a {n}; returns the stored record."""', 'self._validate_{n}(payload)', 'resp = self._request("POST", "/{n}s", json=payload)', "resp.raise_for_status()", "created = resp.json()", 'self._cache.pop("{n}s", None)', "return created"]),
    ("_validate_{n}", "self, payload: dict", ["missing = [k for k in ('name', 'owner') if k not in payload]", "if missing:", '    raise ValueError(f"{n} payload missing {{missing}}")', "if len(payload.get('name', '')) > 80:", '    raise ValueError("{n} name too long")']),
    ("delete_{n}", "self, item_id: int", ['"""Delete a {n}. Returns False when it was already gone."""', 'resp = self._request("DELETE", f"/{n}s/{{item_id}}")', "if resp.status_code == 404:", "    return False", "resp.raise_for_status()", 'self._cache.pop("{n}s", None)', "return True"]),
    ("export_{n}s", "self, path: str", ['"""Write every {n} to `path` as JSON lines."""', "count = 0", "with open(path, 'w', encoding='utf-8') as fh:", "    for row in self.list_{n}s(state='all'):", "        fh.write(json.dumps(row, sort_keys=True) + '\\n')", "        count += 1", "log.info('exported %d {n}s to %s', count, path)", "return count"]),
]


def _py_module(n_classes: int = 3, seed: int = 206) -> str:
    gen = _lcg(seed)
    parts = [_HEAD]
    for c in range(n_classes):
        nouns = [_NOUNS[(next(gen) + k) % len(_NOUNS)] for k in range(3)]
        nouns = list(dict.fromkeys(nouns))
        parts.append(f'\nclass {nouns[0].title()}Client:\n    """Thin client for the {nouns[0]} endpoints (and a few {nouns[-1]} helpers)."""\n\n')
        parts.append("    def __init__(self, base_url: str, token: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:\n")
        parts.append("        self.base_url = base_url.rstrip('/')\n        self.timeout = timeout\n        self._cache: dict[str, Any] = {}\n")
        parts.append("        self._session = requests.Session()\n        self._session.headers['Authorization'] = f'Bearer {token}'\n\n")
        parts.append("    def _request(self, method: str, path: str, **kw: Any) -> requests.Response:\n")
        parts.append("        kw.setdefault('timeout', self.timeout)\n        return self._session.request(method, self.base_url + path, **kw)\n")
        for n in nouns:
            for name, args, body in _METHODS:
                parts.append(f"\n    def {name.format(n=n)}({args}):\n" + "".join(f"        {b.format(n=n)}\n" for b in body))
    parts.append("\n\nif __name__ == '__main__':\n    import sys\n\n    print(fetch_with_retry(sys.argv[1]).status_code)\n")
    return "".join(parts)


def _numbered(src: str, start: int = 1, arrow: bool = False) -> str:
    lines = src.split("\n")
    nl = lines[-1] == ""
    if nl:
        lines.pop()
    return "\n".join(f"{i:>6}{'→' if arrow else chr(9)}{l}" for i, l in enumerate(lines, start)) + ("\n" if nl else "")


def _stdlib_source(modname: str) -> str | None:
    try:
        import importlib.util
        spec = importlib.util.find_spec(modname)
        return open(spec.origin, encoding="utf-8").read() if spec and spec.origin and "\r" not in open(spec.origin, "rb").read().decode("utf-8", "replace") else None
    except (OSError, ImportError, ValueError):
        return None


def _check_reread(orig: str, skel: str) -> None:
    """The agent's follow-up: Read offset/limit from a marker returns exactly the body that was elided (ast says where it ends)."""
    _q, fmt, _n0, codes, _nl = _parse_input(orig)
    tree = _parse("\n".join(codes))
    ends = {n.end_lineno for _qn, n in _funcs(tree)}
    n_marks = 0
    for l in skel.split("\n"):
        m = re.search(r"\.\.\.  # lines (\d+)-(\d+)$", l)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            n_marks += 1
            assert b in ends, f"marker end {b} is not the end of any function"
            frag = "\n".join(codes[a - 1:b])
            assert frag.strip(), "marker points at empty lines"
    assert n_marks, "no marker in the skeleton"


def _source_fixtures(ranked: bool) -> list[Fixture]:
    big = _py_module(2)
    n_lines = len(big.splitlines())
    deletes = re.findall(r"def (delete_\w+)\(", big)
    unique = [d for d in deletes if deletes.count(d) == 1]
    mid_fn, end_fn = unique[len(unique) // 2 - 1], unique[-1]  # one in the first and one in the last client class
    queries = {
        "start": "why does fetch_with_retry give up after a single timeout, check the backoff delay",
        "middle": f"{mid_fn.replace('_', ' ')} returns False for an id that exists, what does the status code check do",
        "end": f"{end_fn} should also clear the cache, see {end_fn.replace('_', ' ')}",
    }
    fn = ranked_bodies if ranked else skeleton
    fx: list[Fixture] = []

    def add(name: str, src: str, query: str | None = None, shrinks: bool | None = True, arrow=None) -> None:
        body = src if arrow is None else _numbered(src, arrow=arrow)
        text = (f"@query {query}\n" + body) if (ranked and query is not None) else body
        fx.append(Fixture(name, text, shrinks))
        if shrinks:
            _check_reread(body, fn(text).split("\n", 1)[1] if ranked and query is not None else fn(text))
        if shrinks and ranked and query is not None:
            # what an Edit would quote: every body line of the function(s) the question names is still on the page for #305,
            # and (the point of ranking) is mostly NOT on the page in the signature-only skeleton of #206
            qt = set(_terms(query))
            named = [n for _qn, n in _funcs(_parse(src)) if set(_terms(n.name)) and set(_terms(n.name)) <= qt]
            assert named, "the question must name a function"
            strip = lambda t: {re.sub(r"^ *\d+[\t\u2192]", "", l) for l in t.split("\n")}  # noqa: E731
            ranked_page, skel_page = strip(fn(text)), strip(skeleton(body))
            for n in named:
                quoted = [l for l in src.split("\n")[n.body[0].lineno:n.end_lineno] if l.strip()]
                assert all(l in ranked_page for l in quoted), f"{n.name}: a body line is missing from the ranked read"
                assert sum(l in skel_page for l in quoted) < 0.3 * len(quoted), "the plain skeleton was expected to hide the body"

    if ranked:
        add(f"generated module ({n_lines} lines), the question names a function near the START", big, queries["start"])
        add("same, arrow-numbered like Claude Code's Read, question about a method in the MIDDLE", big, queries["middle"], arrow=True)
        add("same, cat -n numbered, question about a method at the END", big, queries["end"], arrow=False)
    else:
        add(f"generated module ({n_lines} lines), plain cat", big)
        add("same, arrow-numbered like Claude Code's Read", big, arrow=True)
        add("same, cat -n numbered (tab)", big, arrow=False)
    tw = _stdlib_source("textwrap")
    if tw:
        add("real stdlib textwrap.py (497 lines)", tw, "TextWrapper._handle_long_word splits a long word that is wider than the width")
    # negatives: must come back byte-identical
    q = queries["start"]
    short = "\n".join(big.splitlines()[:150]).rsplit("\ndef ", 1)[0] + "\n"
    add("short module (under 300 lines): read it whole", short, q, False)
    big_lines = big.splitlines()
    at = next(i for i, l in enumerate(big_lines) if l.startswith("def parse_link_header"))  # a line where the rest of the file is still valid Python
    at2 = next(i for i, l in enumerate(big_lines) if l.startswith("log = logging"))
    fx.append(Fixture(f"ranged read starting at line {at + 1} (the agent follows a marker and will edit)", (f"@query {q}\n" if ranked else "") + _numbered("\n".join(big_lines[at:]) + "\n", start=at + 1), False))
    fx.append(Fixture(f"ranged read starting at line {at2 + 1} of a numbered file", (f"@query {q}\n" if ranked else "") + _numbered("\n".join(big_lines[at2:]) + "\n", start=at2 + 1), False))
    add("Python 2 module (print statement): does not parse", big.replace("print(fetch_with_retry(sys.argv[1]).status_code)", "print fetch_with_retry(sys.argv[1]).status_code"), q, False)
    js = "\n".join(f"function handler{i}(req, res) {{\n  const body = req.body;\n  if (!body) {{ return res.status(400).end(); }}\n  res.json({{ ok: true, id: {i} }});\n}}" for i in range(80)) + "\n"
    add("a 400-line JavaScript file", js, q, False)
    add("data module: 350 constants, nothing to elide", "".join(f"CODE_{i} = {i * 7919 % 10007}  # entry {i}\n" for i in range(350)), q, False)
    add("CRLF source", big.replace("\n", "\r\n"), q, False)
    if ranked:
        add("no @query: nothing to rank against", big, None, False)
        add("question shares no word with the file", big, "zebra quantum lattice", False)
    # adversarial: marker-looking text in real code, with and without a skeleton already applied
    add("source that already contains marker-looking comments", big.replace("raise RetryError(url, retries, last)", "raise RetryError(url, retries, last)  # lines 3-9", 1), q, None)
    return fx


_SRC_LOSES = ("the statements of every function and method body of 3+ lines (kept as a '...  # lines A-B' marker with the exact range), "
              "docstring lines after the first, comments inside bodies, anything nested inside a function body")

case(
    206,
    claim="A 300+ line Python read becomes imports, constants, class and def headers, first docstring lines and one '...  # lines A-B' marker per body (exact range, original line numbers kept); ast still parses it and sees every signature, class, decorator and range; ranged reads, short files and unparseable text are returned untouched.",
    loses=_SRC_LOSES,
    encode=skeleton,
    probes=probes_206,
    fixtures=lambda: _source_fixtures(False),
    min_gain=0.25,
    mutations=8,
)

case(
    305,
    claim="With the question in-band, the 1-3 functions BM25 ranks highest keep their whole body and every other body becomes a '...  # lines A-B' marker; ast sees every signature and class, a function the question names keeps a byte-identical body, and ranged reads, short files, a missing or unmatched question and unparseable text are returned untouched.",
    loses=_SRC_LOSES + "; bodies of functions ranked below the top three",
    encode=ranked_bodies,
    probes=probes_305,
    fixtures=lambda: _source_fixtures(True),
    min_gain=0.2,
    mutations=8,
)


# ===============================================================================================================
# #209 BM25 chunk selection against command and prompt intent (RULE)
#
# The query rides in-band as the first line '@query <command> | <last user prompt>' (the hook knows both; the gate sees
# text only). Outputs under 5,000 characters are left alone. Otherwise: split into chunks at blank lines (a chunk over 14
# lines is cut into 10-line windows), rank chunks by BM25 against the query, keep the best until about a third of the
# lines are kept, add one line of context each side, and replace every skipped run with '[... N lines omitted ...]'.
#
# Two safety nets were NEEDED, found by running pure BM25 on these fixtures (scratch experiment, numbers in the final
# report): the first and last 3 lines always stay (banners, totals, exit status never share words with the question),
# and the first occurrence of each distinct error-signal line stays (the answer to 'why did nginx fail' is a bind() line
# that shares no word with 'why did nginx fail'; BM25 ranks the repeated restart noise above it).
#
# FIXTURES are reconstructed from memory (kubectl describe pod, a pytest -v failure, journalctl for a crash-looping
# nginx); the answer sits at the MIDDLE (k8s), the END (pytest) and near the START (journal).

MIN_CHARS_209, KEEP_LINES_209 = 5000, 0.34
_SIGNAL = re.compile(r"\b(error|errors|fail|failed|failure|fatal|emerg|crit|critical|panic|traceback|exception|denied|refused|oom|oomkilled|killed|timeout|segfault)\b|\bE {3}", re.I)


_VOLATILE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b|\[\d+\]|\b\d{4}-\d{2}-\d{2}T[\d:.Z+-]+")


def _stems(text: str) -> list[str]:
    """_terms plus a light suffix strip (restarting/restarts -> restart), which the k8s fixture needs."""
    out = []
    for t in _terms(text):
        if len(t) > 5 and t.endswith("ing"):
            t = t[:-3]
        elif len(t) > 4 and t.endswith("ed"):
            t = t[:-2]
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _chunks_209(lines: list[str]) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        if j - i > 14:
            out += [(k, min(k + 10, j) - 1) for k in range(i, j, 10)]
        else:
            out.append((i, j - 1))
        i = j
    return out


def bm25_select(text: str) -> str:
    if not text.startswith("@query "):
        return text
    head, _, body = text.partition("\n")
    if len(body) < MIN_CHARS_209 or re.search(r"^\[\.\.\. \d+ lines omitted \.\.\.\]$", body, re.M):
        return text  # small, or already pruned (this also makes the rule idempotent)
    nl = body.endswith("\n")
    lines = body.split("\n")[:-1] if nl else body.split("\n")
    chunks = _chunks_209(lines)
    if len(chunks) < 6:
        return text
    scores = _bm25(_stems(head[7:]), [_stems("\n".join(lines[a:b + 1])) for a, b in chunks])
    keep = set(range(min(3, len(lines)))) | set(range(max(0, len(lines) - 3), len(lines)))
    seen: set[str] = set()
    for i, l in enumerate(lines):  # first occurrence of each distinct signal line (digits normalised), at most 12
        # evidence lines keep their digits ('assert 1450 == 1305' and 'assert 221 == 220' are different facts); the rest dedupe by shape
        norm = l.strip() if re.match(r"\s*(E |FAILED |>)|assert", l) else re.sub(r"\d+", "#", _VOLATILE.sub("#", l)).strip()
        if _SIGNAL.search(l) and norm not in seen and len(seen) < 12:
            seen.add(norm)
            keep |= {i}
    budget = KEEP_LINES_209 * len(lines)
    shape = lambda l: re.sub(r"\d+", "#", _VOLATILE.sub("#", l)).strip()  # noqa: E731
    shown = {shape(lines[i]) for i in keep}
    for ci in sorted(range(len(chunks)), key=lambda c: (-scores[c], c)):
        if scores[ci] <= 0 or len(keep) >= budget:
            break
        a, b = chunks[ci]
        sig = {shape(l) for l in lines[a:b + 1] if l.strip()}
        if sig and len(sig & shown) >= 0.6 * len(sig):
            continue  # a near-duplicate of what is already kept (the 30th 'Normal Pulled' event adds nothing)
        keep |= set(range(a, b + 1))
        shown |= sig
    keep |= {j for i in list(keep) for j in (i - 1, i + 1) if 0 <= j < len(lines)}
    out, i = [], 0
    while i < len(lines):
        if i in keep:
            out.append(lines[i])
            i += 1
        else:
            j = i
            while j < len(lines) and j not in keep:
                j += 1
            out.append(f"[... {j - i} lines omitted ...]")
            i = j
    res = head + "\n" + "\n".join(out) + ("\n" if nl else "")
    return res if len(res) < 0.8 * len(text) else text


_ANSWER_LINES = [
    ("pod termination", re.compile(r"OOMKilled|Exit Code:|Restart Count:|Last State:")),
    ("failing tests", re.compile(r"^(FAILED |E {3}|=+ .*\d+ failed)")),
    ("why nginx did not start", re.compile(r"bind\(\)|Address already in use|status=1/FAILURE")),
]


def probes_209(text: str) -> dict:
    body = text.split("\n")[1:] if text.startswith("@query ") else text.split("\n")
    real = [l for l in body if l.strip() and not re.match(r"^\[\.\.\. \d+ lines omitted \.\.\.\]$", l)]
    out = {name: sorted({" ".join(l.split()[3:]) if name == "why nginx did not start" else l.strip() for l in real if rx.search(l)})
           for name, rx in _ANSWER_LINES}  # journal lines compared without their 'Nov 04 07:00:01' stamp: each restart cycle is one fact
    out["first line of the output"] = real[0] if real else None
    out["last line of the output"] = real[-1] if real else None
    return out


def _k8s_209() -> str:
    g = _lcg(209)
    ev = []
    for k in range(34):
        age = f"{(k * 7 + next(g) % 5) % 59 + 1}m"
        kind = ("Normal", "Pulled", "Container image \"registry.example.com/payments/api:2.14.3\" already present on machine") if k % 4 == 0 else \
               ("Normal", "Created", "Created container api") if k % 4 == 1 else \
               ("Normal", "Started", "Started container api") if k % 4 == 2 else \
               ("Warning", "BackOff", "Back-off restarting failed container api in pod api-7d9f8c6b5-x2k4q_payments(5b1c...)")
        ev.append(f"  {kind[0]:<8} {kind[1]:<9} {age:<21} kubelet            {kind[2]}")
    return "\n".join([
        "Name:             api-7d9f8c6b5-x2k4q", "Namespace:        payments", "Priority:         0", "Service Account:  api",
        "Node:             ip-10-0-3-17.eu-west-1.compute.internal/10.0.3.17", "Start Time:       Tue, 03 Nov 2026 08:12:41 +0000",
        "Labels:           app=api", "                  pod-template-hash=7d9f8c6b5", "Annotations:      kubectl.kubernetes.io/restartedAt: 2026-11-03T08:12:40Z",
        "Status:           Running", "IP:               10.0.3.121", "IPs:", "  IP:           10.0.3.121", "Controlled By:  ReplicaSet/api-7d9f8c6b5",
        "Containers:", "  api:", "    Container ID:   containerd://3f2a9c7e41d85b60a7c2e19f0d4b8a3c5e7f1b2d9a6c4e8f0b3d5a7c9e1f2b4d",
        "    Image:          registry.example.com/payments/api:2.14.3", "    Image ID:       registry.example.com/payments/api@sha256:9b1c4e7a2d5f8b3c6e9a1d4f7b2c5e8a3d6f9b1c4e7a2d5f8b3c6e9a1d4f7b2c",
        "    Port:           8080/TCP", "    Host Port:      0/TCP", "    State:          Running", "      Started:      Wed, 04 Nov 2026 06:51:13 +0000",
        "    Last State:     Terminated", "      Reason:       OOMKilled", "      Exit Code:    137", "      Started:      Wed, 04 Nov 2026 05:12:02 +0000",
        "      Finished:     Wed, 04 Nov 2026 06:51:12 +0000", "    Ready:          True", "    Restart Count:  7", "    Limits:", "      cpu:     500m", "      memory:  512Mi",
        "    Requests:", "      cpu:      250m", "      memory:   256Mi",
        "    Liveness:   http-get http://:8080/healthz delay=10s timeout=1s period=10s #success=1 #failure=3",
        "    Readiness:  http-get http://:8080/ready delay=5s timeout=1s period=5s #success=1 #failure=3",
        "    Environment:", "      JAVA_OPTS:       -Xmx700m", "      DB_HOST:         payments-db.internal", "      DB_POOL_SIZE:    40", "      FEATURE_FLAGS:   <set to the key 'flags' of config map 'api-flags'>  Optional: false",
        "    Mounts:", "      /etc/api from config (ro)", "      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-8x2lp (ro)",
        "Conditions:", "  Type              Status", "  Initialized       True ", "  Ready             True ", "  ContainersReady   True ", "  PodScheduled      True ",
        "Volumes:", "  config:", "    Type:      ConfigMap (a volume populated by a ConfigMap)", "    Name:      api-config", "    Optional:  false",
        "QoS Class:                   Burstable", "Node-Selectors:              <none>", "Tolerations:                 node.kubernetes.io/not-ready:NoExecute op=Exists for 300s",
        "                             node.kubernetes.io/unreachable:NoExecute op=Exists for 300s",
        "Events:", "  Type     Reason    Age                   From               Message", "  ----     ------    ----                  ----               -------", *ev,
    ]) + "\n"


def _pytest_209() -> str:
    mods = ["cart", "checkout", "pricing", "inventory", "shipping", "tax", "coupons", "users"]
    rows = []
    n = 0
    for m in mods:
        for k in range(14):
            n += 1
            rows.append(f"tests/test_{m}.py::test_{m}_case_{k:02d} PASSED" + " " * (36 - len(m) - len(f"{k:02d}")) + f"[{n * 100 // 115:3d}%]")
    fail1 = ["tests/test_checkout.py::test_checkout_total_with_coupon FAILED", "tests/test_tax.py::test_tax_rounds_half_even FAILED"]
    body = ["============================= test session starts ==============================", "platform linux -- Python 3.13.18, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python",
            "cachedir: .pytest_cache", "rootdir: /work/shop", "configfile: pyproject.toml", "collected 115 items", "", *rows[:57], fail1[0], *rows[57:96], fail1[1], *rows[96:], "",
            "=================================== FAILURES ===================================", "____________________ test_checkout_total_with_coupon ____________________", "",
            "    def test_checkout_total_with_coupon():", "        cart = Cart([Item('pen', 250), Item('ink', 1200)])", "        cart.apply(Coupon('SAVE10'))", ">       assert checkout_total(cart) == 1305", "E       assert 1450 == 1305", "E        +  where 1450 = checkout_total(<Cart items=2>)", "",
            "tests/test_checkout.py:41: AssertionError", "____________________ test_tax_rounds_half_even ____________________", "", "    def test_tax_rounds_half_even():", ">       assert tax_cents(1050, rate=0.21) == 220", "E       assert 221 == 220", "", "tests/test_tax.py:17: AssertionError",
            "=========================== short test summary info ============================", "FAILED tests/test_checkout.py::test_checkout_total_with_coupon - assert 1450 == 1305", "FAILED tests/test_tax.py::test_tax_rounds_half_even - assert 221 == 220",
            "======================== 2 failed, 113 passed in 4.12s ========================"]
    return "\n".join(body) + "\n"


def _journal_209() -> str:
    out = ["-- Journal begins at Tue 2026-10-27 06:12:09 UTC. --", "Nov 04 07:00:01 web-1 systemd[1]: Starting A high performance web server and a reverse proxy server...",
           "Nov 04 07:00:01 web-1 nginx[1234]: nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)",
           "Nov 04 07:00:01 web-1 nginx[1234]: nginx: [emerg] still could not bind()"]
    for k in range(1, 46):
        t = 3 + k * 2
        out += [f"Nov 04 07:{t // 60:02d}:{t % 60:02d} web-1 systemd[1]: nginx.service: Control process exited, code=exited, status=1/FAILURE",
                f"Nov 04 07:{t // 60:02d}:{t % 60:02d} web-1 systemd[1]: nginx.service: Failed with result 'exit-code'.",
                f"Nov 04 07:{t // 60:02d}:{t % 60:02d} web-1 systemd[1]: Failed to start A high performance web server and a reverse proxy server.",
                f"Nov 04 07:{t // 60:02d}:{(t + 1) % 60:02d} web-1 systemd[1]: nginx.service: Scheduled restart job, restart counter is at {k}.",
                f"Nov 04 07:{t // 60:02d}:{(t + 1) % 60:02d} web-1 systemd[1]: Stopped A high performance web server and a reverse proxy server.",
                f"Nov 04 07:{t // 60:02d}:{(t + 1) % 60:02d} web-1 systemd[1]: Starting A high performance web server and a reverse proxy server..."]
    out.append("Nov 04 07:48:00 web-1 systemd[1]: nginx.service: Start request repeated too quickly.")
    return "\n".join(out) + "\n"


def _bm25_fixtures() -> list[Fixture]:
    k8s, py, jr = _k8s_209(), _pytest_209(), _journal_209()
    q = lambda t, s: f"@query {t}\n{s}"  # noqa: E731
    small = "NAME   READY   STATUS    RESTARTS   AGE\napi    1/1     Running   7          20h\n"
    return [
        Fixture("kubectl describe pod: the OOMKilled facts sit in the MIDDLE", q("kubectl describe pod api-7d9f8c6b5-x2k4q -n payments | why does the api pod keep restarting and what is its restart count", k8s), True),
        Fixture("pytest -v: the failing assertions are near the END", q("pytest -v | which test fails and what does the assertion say", py), True),
        Fixture("journalctl: the cause is near the START and shares no word with the question", q("journalctl -u nginx | why did nginx fail to start", jr), True),
        Fixture("under 5,000 characters: not worth ranking", q("kubectl get pods | why does the api pod keep restarting", small), False),
        Fixture("under 5,000 characters but 40 chunks: still not worth ranking", q("kubectl get events | why does the api pod keep restarting", "\n\n".join(f"event {i}: pod api restarting probe failed {i}\nreason {i}" for i in range(40))), False),
        Fixture("no @query line: nothing to rank against", k8s, False),
        Fixture("long but one chunk of 5 lines per blank-line group, too few chunks", q("seq | anything", "\n\n".join("\n".join(f"line {i}.{k} xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" for k in range(5)) for i in range(4))), False),
        Fixture("a query-looking first line inside normal output", "@query is a decorator name here\n" + small, None),
    ]


case(
    209,
    claim="For outputs over 5,000 characters carrying the question in-band, BM25 keeps the best chunks plus the first and last 3 lines and the first occurrence of each distinct error line, with one line of context, and marks every skipped run '[... N lines omitted ...]'; the facts the question asks (OOMKilled/exit code/restart count, failing tests and assertions, the bind() error) and the output's first and last lines survive.",
    loses="the content of chunks that neither match the question nor look like errors (events, passing-test lines, repeated restart cycles) and the order of repeated error lines after their first occurrence",
    encode=bm25_select,
    probes=probes_209,
    fixtures=_bm25_fixtures,
    min_gain=0.3,
    mutations=20,
)


# ===============================================================================================================
# MCP wrappers: #304 _select projection, #387 snippet-then-expand, #503 gateway, #684 CLI bridge (SIMS)
#
# One fake MCP server shared by all four. TOOL NAMES are the 48 tools this session's github server really lists; the
# schemas are reconstructed from memory but calibrated against three real ones read in this session (list_issues 1.9k
# chars, get_commit 1.0k, add_issue_comment 1.3k) and the average here is printed by the sims. Results are deterministic
# fixtures shaped like GitHub's REST objects. A result is {"content": [{"type": "text", "text": ...}], "isError": bool}.
# NOTE for #304: the real github server already ships a `fields` argument on list_issues (server-side projection of the
# top-level keys), so for that server the idea's gain is only for nested projection and for servers without such an arg.

_P = {
    "owner": ("string", "Repository owner"), "repo": ("string", "Repository name"), "number": ("number", "The number of the issue or pull request"),
    "page": ("number", "Page number for pagination (min 1)"), "perPage": ("number", "Results per page for pagination (min 1, max 100)"),
    "after": ("string", "Cursor for pagination. Use the cursor from the previous response."), "state": ("string", "Filter by state, by default all are returned when not provided"),
    "labels": ("array", "Filter by labels"), "since": ("string", "Filter by date (ISO 8601 timestamp)"), "ref": ("string", "Git reference (branch, tag or commit SHA)"),
    "sha": ("string", "Commit SHA, branch name, or tag name"), "path": ("string", "Path to a file or directory in the repository"), "branch": ("string", "Branch name"),
    "body": ("string", "Text content of the comment, issue, pull request or review"), "title": ("string", "Title"), "message": ("string", "Commit message"),
    "content": ("string", "The new file content"), "method": ("string", "The operation to perform, see the tool description for the allowed values"),
    "query": ("string", "Search query using GitHub search syntax"), "head": ("string", "Branch containing the changes"), "base": ("string", "Branch to merge into"),
    "tag": ("string", "Tag name"), "run_id": ("number", "The unique identifier of the workflow run"), "job_id": ("number", "The unique identifier of the workflow job"),
    "org": ("string", "Organization login"), "team_slug": ("string", "Team slug"), "files": ("array", "Array of file objects to push, each with path and content"),
    "thread_id": ("string", "The node ID of the review thread"), "name": ("string", "Name"), "private": ("boolean", "Whether the repository is private"),
    # the three below are copied in spirit from the real list_issues schema (long, with enums): they pad list_*/get_* tools to a realistic size
    "orderBy": ("string", "Order results by field. If provided, the 'direction' also needs to be provided."),
    "direction": ("string", "Order direction. If provided, the 'orderBy' also needs to be provided."),
    "fields": ("array", "Subset of fields to return for each result. If omitted, all fields are returned. Use this to reduce response size when you only need specific fields; omitting 'body' and other long text fields in particular drops the largest per-result data."),
    "detail": ("string", "Level of detail to include. \"none\" omits stats and files entirely. \"stats\" (default) includes per-file metadata with no patch content. \"full_patch\" additionally includes the unified diff content for each file and can be very large."),
}
_GH = {  # name: (description, [(param, required)])
    "actions_get": ("Get details about specific GitHub Actions resources. Use this tool to get details about individual workflows, workflow runs, jobs, and artifacts by their unique IDs.", [("method", 1), ("owner", 1), ("repo", 1), ("run_id", 0), ("job_id", 0)]),
    "actions_list": ("List GitHub Actions workflows in a repository, or the runs, jobs and artifacts of one, depending on the method.", [("method", 1), ("owner", 1), ("repo", 1), ("page", 0), ("perPage", 0), ("run_id", 0)]),
    "actions_run_trigger": ("Trigger a GitHub Actions workflow run, re-run, cancel or delete a run. Needs a token with workflow scope.", [("method", 1), ("owner", 1), ("repo", 1), ("run_id", 0), ("ref", 0)]),
    "add_comment_to_pending_review": ("Add a review comment to the requester's latest pending pull request review. A pending review needs to already exist to call this.", [("owner", 1), ("repo", 1), ("number", 1), ("path", 1), ("body", 1)]),
    "add_issue_comment": ("Add a comment and/or reaction to a specific issue or issue comment in a GitHub repository. Use this tool with pull requests as well (pass the number as issue_number).", [("owner", 1), ("repo", 1), ("number", 1), ("body", 0)]),
    "add_reply_to_pull_request_comment": ("Add a reply to an existing pull request comment. This creates a new comment that is linked as a reply to the specified comment.", [("owner", 1), ("repo", 1), ("number", 1), ("body", 1)]),
    "create_branch": ("Create a new branch in a GitHub repository from an existing ref.", [("owner", 1), ("repo", 1), ("branch", 1), ("sha", 0)]),
    "create_or_update_file": ("Create or update a single file in a GitHub repository. If updating, you must provide the SHA of the file you want to update.", [("owner", 1), ("repo", 1), ("path", 1), ("content", 1), ("message", 1), ("branch", 1), ("sha", 0)]),
    "create_pull_request": ("Create a new pull request in a GitHub repository. Search for pull request templates first and use them for the description.", [("owner", 1), ("repo", 1), ("title", 1), ("head", 1), ("base", 1), ("body", 0)]),
    "create_repository": ("Create a new GitHub repository in your account or specified organization.", [("name", 1), ("org", 0), ("private", 0)]),
    "delete_file": ("Delete a file from a GitHub repository on the given branch with a commit message.", [("owner", 1), ("repo", 1), ("path", 1), ("message", 1), ("branch", 1)]),
    "disable_pr_auto_merge": ("Disable auto-merge for a pull request.", [("owner", 1), ("repo", 1), ("number", 1)]),
    "enable_pr_auto_merge": ("Enable auto-merge for a pull request with the given merge method.", [("owner", 1), ("repo", 1), ("number", 1), ("method", 0)]),
    "fork_repository": ("Fork a GitHub repository to your account or specified organization.", [("owner", 1), ("repo", 1), ("org", 0)]),
    "get_check_run": ("Get details about a check run, including its status, conclusion, output summary and annotations count.", [("owner", 1), ("repo", 1), ("run_id", 1)]),
    "get_commit": ("Get details for a commit from a GitHub repository, with per-file stats and optionally the full patch.", [("owner", 1), ("repo", 1), ("sha", 1), ("page", 0), ("perPage", 0)]),
    "get_file_contents": ("Get the contents of a file or directory from a GitHub repository. Large files are truncated; pass a path to a directory to list it.", [("owner", 1), ("repo", 1), ("path", 0), ("ref", 0), ("sha", 0)]),
    "get_job_logs": ("Download logs for a specific workflow job or for every failed job in a workflow run, optionally limited to the last lines.", [("owner", 1), ("repo", 1), ("job_id", 0), ("run_id", 0)]),
    "get_label": ("Get a specific label from a repository.", [("owner", 1), ("repo", 1), ("name", 1)]),
    "get_latest_release": ("Get the latest release in a GitHub repository.", [("owner", 1), ("repo", 1)]),
    "get_me": ("Get details of the authenticated GitHub user. Use this when a request is about the user's own profile or when information is missing to build other tool calls.", []),
    "get_release_by_tag": ("Get a specific release by its tag name in a GitHub repository.", [("owner", 1), ("repo", 1), ("tag", 1)]),
    "get_tag": ("Get details about a specific git tag in a GitHub repository.", [("owner", 1), ("repo", 1), ("tag", 1)]),
    "get_team_members": ("Get member usernames of a specific team in an organization.", [("org", 1), ("team_slug", 1)]),
    "get_teams": ("Get details of the teams the user is a member of.", [("org", 0)]),
    "issue_read": ("Get information about a specific issue in a GitHub repository: the issue, its comments, sub-issues or labels, depending on the method.", [("method", 1), ("owner", 1), ("repo", 1), ("number", 1), ("page", 0), ("perPage", 0)]),
    "issue_write": ("Create a new or update an existing issue in a GitHub repository.", [("method", 1), ("owner", 1), ("repo", 1), ("number", 0), ("title", 0), ("body", 0), ("state", 0), ("labels", 0)]),
    "list_branches": ("List branches in a GitHub repository.", [("owner", 1), ("repo", 1), ("page", 0), ("perPage", 0)]),
    "list_commits": ("Get list of commits of a branch in a GitHub repository. Returns at least 30 results per page by default.", [("owner", 1), ("repo", 1), ("sha", 0), ("path", 0), ("since", 0), ("page", 0), ("perPage", 0)]),
    "list_issue_fields": ("List the custom issue fields defined for an organization.", [("org", 1)]),
    "list_issue_types": ("List supported issue types for repository owner (organization).", [("owner", 1)]),
    "list_issues": ("List issues in a GitHub repository. For pagination, use the 'endCursor' from the previous response's 'pageInfo' in the 'after' parameter.", [("owner", 1), ("repo", 1), ("state", 0), ("labels", 0), ("since", 0), ("after", 0), ("perPage", 0)]),
    "list_pull_requests": ("List pull requests in a GitHub repository. If the user specifies an author, use the search tool with the author qualifier instead.", [("owner", 1), ("repo", 1), ("state", 0), ("head", 0), ("base", 0), ("page", 0), ("perPage", 0)]),
    "list_releases": ("List releases in a GitHub repository.", [("owner", 1), ("repo", 1), ("page", 0), ("perPage", 0)]),
    "list_repository_collaborators": ("List collaborators of a repository with their permission level.", [("owner", 1), ("repo", 1), ("page", 0), ("perPage", 0)]),
    "list_tags": ("List git tags in a GitHub repository.", [("owner", 1), ("repo", 1), ("page", 0), ("perPage", 0)]),
    "merge_pull_request": ("Merge a pull request in a GitHub repository with the given merge method and commit message.", [("owner", 1), ("repo", 1), ("number", 1), ("method", 0), ("message", 0)]),
    "pull_request_read": ("Get information on a specific pull request: its diff, status, files, review comments, reviews or check runs, depending on the method.", [("method", 1), ("owner", 1), ("repo", 1), ("number", 1), ("page", 0), ("perPage", 0)]),
    "pull_request_review_write": ("Create, submit or delete a pull request review. Use method create to start a pending review, then submit_pending to publish it.", [("method", 1), ("owner", 1), ("repo", 1), ("number", 1), ("body", 0)]),
    "push_files": ("Push multiple files to a GitHub repository in a single commit.", [("owner", 1), ("repo", 1), ("branch", 1), ("files", 1), ("message", 1)]),
    "request_copilot_review": ("Request a GitHub Copilot code review for a pull request. Use this for automated feedback on pull requests.", [("owner", 1), ("repo", 1), ("number", 1)]),
    "resolve_review_thread": ("Resolve a review thread on a pull request. Resolving marks the conversation as addressed.", [("thread_id", 1)]),
    "run_secret_scanning": ("Scan files, diffs or text for secrets such as API keys and tokens before they are committed.", [("owner", 1), ("repo", 1), ("content", 1)]),
    "sub_issue_write": ("Add a sub-issue to a parent issue, remove one, or reprioritize the sub-issues of a parent.", [("method", 1), ("owner", 1), ("repo", 1), ("number", 1)]),
    "unresolve_review_thread": ("Unresolve a previously resolved review thread on a pull request.", [("thread_id", 1)]),
    "update_issue_comment": ("Edit the body of an existing issue or pull request comment.", [("owner", 1), ("repo", 1), ("number", 1), ("body", 1)]),
    "update_pull_request": ("Update an existing pull request in a GitHub repository: title, body, state or base branch.", [("owner", 1), ("repo", 1), ("number", 1), ("title", 0), ("body", 0), ("state", 0), ("base", 0)]),
    "update_pull_request_branch": ("Update the branch of a pull request with the latest changes from the base branch.", [("owner", 1), ("repo", 1), ("number", 1)]),
}
_DESTRUCTIVE = frozenset({"merge_pull_request", "delete_file", "create_repository", "push_files", "create_or_update_file", "actions_run_trigger", "enable_pr_auto_merge"})


_METHOD_DOC = ("\n\nAvailable methods:\n- get: Get the details of one resource. Requires the identifying number or id.\n- list: List resources with pagination. Use page and perPage, "
               "at most 100 per page.\n- get_comments: Get the comments of the resource, newest last.\n- create: Create a resource; body and title are required.\n"
               "- update: Update fields of an existing resource; only the fields you pass change.\nThe token needs the repo scope for private repositories.")


def _schema(name: str) -> dict:
    desc, params = _GH[name]
    if name.startswith(("list_", "get_", "actions_", "issue_read", "pull_request_read")) and not name.startswith(("get_me", "get_label", "get_tag")):
        params = params + [(x, 0) for x in ("orderBy", "direction", "fields", "detail") if x not in dict(params)]
    props = {}
    for p, _req in params:
        t, d = _P[p]
        s = {"type": t, "description": d}
        if t == "array":
            s["items"] = {"type": "string"}
        if t == "number":
            s["minimum"] = 1
            if p == "perPage":
                s["maximum"] = 100
        if p in ("owner", "repo"):
            s["x-mcp-header"] = p
        if p == "state":
            s["enum"] = ["open", "closed", "all"]
        if p in ("direction", "detail", "orderBy"):
            s["enum"] = {"direction": ["ASC", "DESC"], "detail": ["none", "stats", "full_patch"], "orderBy": ["CREATED_AT", "UPDATED_AT", "COMMENTS"]}[p]
        if p == "fields":
            s["items"] = {"type": "string", "enum": ["number", "title", "body", "state", "user", "labels", "assignees", "comments", "created_at", "updated_at"]}
        props[p] = s
    return {"name": name, "description": desc + (_METHOD_DOC if any(p == "method" for p, _ in params) else ""),
            "inputSchema": {"type": "object", "properties": props, "required": [p for p, r in params if r]}}


def _text_result(obj, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))}], "isError": error}


_WORDS = ("the build fails when cache restore times out after retry and the runner falls back to a cold install which then exceeds the job limit "
          "reproduce with a clean checkout set the flag and run the suite twice expected green actual red see logs attached below").split()


def _prose(g, n_words: int) -> str:
    return " ".join(_WORDS[next(g) % len(_WORDS)] for _ in range(n_words))


def _issues(n: int, seed: int = 304) -> list[dict]:
    g = _lcg(seed)
    people = ["alice", "bob", "carol", "dmitri", "eve", "frank"]
    out = []
    for i in range(n):
        u = people[next(g) % len(people)]
        out.append({"number": 1000 + i, "title": f"{_prose(g, 6).capitalize()} ({i})", "body": _prose(g, 60 + next(g) % 80),
                    "state": "open" if next(g) % 3 else "closed", "comments": next(g) % 12,
                    "user": {"login": u, "id": 1000 + people.index(u), "avatar_url": f"https://avatars.githubusercontent.com/u/{1000 + people.index(u)}?v=4",
                             "html_url": f"https://github.com/{u}", "type": "User", "site_admin": False},
                    "labels": [{"id": 5000 + k, "name": ["bug", "flaky", "infra", "docs", "perf"][k], "color": "d73a4a", "description": f"{_prose(g, 5)}"} for k in range(next(g) % 3)],
                    "assignees": [], "created_at": f"2026-10-{1 + i % 28:02d}T09:{i % 60:02d}:00Z", "updated_at": f"2026-11-{1 + i % 4:02d}T10:{i % 60:02d}:00Z",
                    "html_url": f"https://github.com/acme/shop/issues/{1000 + i}", "url": f"https://api.github.com/repos/acme/shop/issues/{1000 + i}",
                    "author_association": "MEMBER", "reactions": {"+1": next(g) % 4, "-1": 0, "laugh": 0, "heart": next(g) % 2}})
    return out


def _prs(n: int, seed: int = 3041) -> list[dict]:
    g = _lcg(seed)
    out = []
    for i, iss in enumerate(_issues(n, seed)):
        out.append({**{k: iss[k] for k in ("number", "title", "body", "state", "user", "labels", "created_at", "updated_at", "html_url")},
                    "draft": bool(next(g) % 5 == 0), "merged": False, "mergeable_state": ["clean", "blocked", "behind"][next(g) % 3],
                    "head": {"ref": f"feature/{_prose(g, 2).replace(' ', '-')}", "sha": _sha(f"h{i}", 40), "repo": {"full_name": "acme/shop", "private": True}},
                    "base": {"ref": "main", "sha": _sha(f"b{i}", 40)}, "additions": next(g) % 400, "deletions": next(g) % 200, "changed_files": 1 + next(g) % 12})
    return out


class FakeServer:
    """The upstream MCP server: tools/list and tools/call. Records every call it really receives."""

    def __init__(self, seed: int = 0):
        self.tools = [_schema(n) for n in _GH]
        self.calls: list[tuple[str, dict]] = []
        self.fail_on: set[str] = set()
        self.issues, self.prs = _issues(30), _prs(30)

    def list_tools(self) -> list[dict]:
        return json.loads(json.dumps(self.tools))

    def call(self, name: str, args: dict) -> dict:
        self.calls.append((name, json.loads(json.dumps(args))))
        spec = next((t for t in self.tools if t["name"] == name), None)
        if spec is None:
            return _text_result(f"unknown tool {name}", True)
        miss = [r for r in spec["inputSchema"]["required"] if r not in args]
        if miss or name in self.fail_on:
            return _text_result(f"missing required argument(s): {miss}" if miss else f"{name} failed upstream", True)
        if name == "list_issues":
            return _text_result(self.issues)
        if name == "list_pull_requests":
            return _text_result(self.prs)
        return _text_result({"ok": True, "tool": name, "args": args})


def _billed(schema_tok: int, new_per_turn: list[int]) -> float:
    """Cache-aware billed token-equivalents (writes 1.25x, cache reads 0.1x): the schema is the first cached block, every
    turn's new tokens are written once and re-read on each later turn. Why this matters: a schema that is resent every turn
    is mostly a cache READ, which is why Checkly measured near-equal totals for Playwright."""
    cached, cost = 0, 0.0
    for t, new in enumerate(new_per_turn):
        if t == 0:
            cost += 1.25 * (schema_tok + new)
            cached = schema_tok + new
        else:
            cost += 0.1 * cached + 1.25 * new
            cached += new
    return cost


def _block_tokens(tools: list[dict]) -> int:
    return tokens(json.dumps(tools, separators=(",", ":")))


# --- a stdlib jq subset (select, map, length, .a.b, .[], .[n], .[a:b], |, {k, k: e}, [e], comparisons), and real jq as the oracle

class _JqErr(Exception):
    pass


_JQ_TOK = re.compile(r'\s*(?:(?P<op>==|!=|>=|<=|[.\[\]{}()|,:<>])|(?P<str>"(?:[^"\\]|\\.)*")|(?P<num>-?\d+)|(?P<id>[A-Za-z_][A-Za-z0-9_]*))')


def _jq_compile(src: str):
    toks, pos = [], 0
    while pos < len(src.rstrip()):
        m = _JQ_TOK.match(src, pos)
        if not m or m.end() == pos:
            raise _JqErr(f"syntax error near {src[pos:pos + 12]!r}")
        pos = m.end()
        toks.append((m.lastgroup, m.group(m.lastgroup)))
    i = [0]

    def peek():
        return toks[i[0]] if i[0] < len(toks) else (None, None)

    def eat(v=None):
        t = peek()
        if t[0] is None or (v is not None and t[1] != v):
            raise _JqErr(f"expected {v!r}")
        i[0] += 1
        return t

    def index(v, k):
        if v is None:
            return None
        if isinstance(v, dict) and isinstance(k, str):
            return v.get(k)
        if isinstance(v, list) and isinstance(k, int):
            return v[k] if -len(v) <= k < len(v) else None
        raise _JqErr(f"Cannot index {type(v).__name__} with {k!r}")

    def pipe():
        left = cmp()
        while peek() == ("op", "|"):
            eat()
            right, lf = cmp(), left
            left = (lambda lf, rf: lambda v: [y for x in lf(v) for y in rf(x)])(lf, right)
        return left

    def cmp():
        left = path()
        t = peek()
        if t[0] == "op" and t[1] in ("==", "!=", "<", ">", "<=", ">="):
            eat()
            right = path()
            ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}
            f = ops[t[1]]
            return lambda v: [f(a, b) for a in left(v) for b in right(v)]
        return left

    def path():
        t = peek()
        if t == ("op", "."):
            eat()
            if peek()[0] in ("id", "str"):
                k = eat()
                key = k[1] if k[0] == "id" else json.loads(k[1])
                node = lambda v, key=key: [index(v, key)]
            else:
                node = lambda v: [v]
        elif t[0] == "id" and t[1] in ("select", "map"):
            eat()
            eat("(")
            inner = pipe()
            eat(")")
            if t[1] == "select":
                node = lambda v: [v] if any(x not in (None, False) for x in inner(v)) else []
            else:
                node = lambda v: [[y for x in (v if isinstance(v, list) else list(v.values())) for y in inner(x)]]
        elif t == ("id", "length"):
            eat()
            node = lambda v: [len(v) if v is not None else 0]
        elif t[0] == "id" and t[1] in ("null", "true", "false"):
            eat()
            val = {"null": None, "true": True, "false": False}[t[1]]
            node = lambda v: [val]
        elif t[0] == "num":
            eat()
            node = (lambda n: lambda v: [n])(int(t[1]))
        elif t[0] == "str":
            eat()
            node = (lambda s: lambda v: [s])(json.loads(t[1]))
        elif t == ("op", "{"):
            eat()
            entries = []
            while peek() != ("op", "}"):
                k = eat()
                key = k[1] if k[0] == "id" else json.loads(k[1])
                if peek() == ("op", ":"):
                    eat()
                    entries.append((key, cmp()))
                else:
                    entries.append((key, (lambda key: lambda v: [index(v, key)])(key)))
                if peek() == ("op", ","):
                    eat()
            eat("}")

            def node(v, entries=entries):
                outs = [{}]
                for key, f in entries:
                    outs = [{**o, key: x} for o in outs for x in f(v)]
                return outs
        elif t == ("op", "["):
            eat()
            inner = pipe()
            eat("]")
            node = lambda v: [list(inner(v))]
        elif t == ("op", "("):
            eat()
            node = pipe()
            eat(")")
        else:
            raise _JqErr(f"unexpected {t[1]!r}")
        # postfix: .name  [] [n] [a:b]
        while True:
            t = peek()
            if t == ("op", ".") and i[0] + 1 < len(toks) and toks[i[0] + 1][0] in ("id", "str"):
                eat()
                k = eat()
                node = (lambda n, k: lambda v: [index(x, k) for x in n(v)])(node, k[1] if k[0] == "id" else json.loads(k[1]))
            elif t == ("op", "["):
                eat()
                if peek() == ("op", "]"):
                    eat()
                    node = (lambda n: lambda v: [y for x in n(v) for y in (x if isinstance(x, list) else list(x.values()) if isinstance(x, dict) else _fail(x))])(node)
                else:
                    a = int(eat()[1]) if peek()[0] == "num" else None
                    if peek() == ("op", ":"):
                        eat()
                        b = int(eat()[1]) if peek()[0] == "num" else None
                        eat("]")
                        node = (lambda n, a, b: lambda v: [x[a:b] for x in n(v)])(node, a, b)
                    else:
                        eat("]")
                        node = (lambda n, a: lambda v: [index(x, a) for x in n(v)])(node, a)
            else:
                return node

    def _fail(x):
        raise _JqErr(f"Cannot iterate over {type(x).__name__}")

    f = pipe()
    if i[0] != len(toks):
        raise _JqErr(f"unexpected {toks[i[0]][1]!r}")
    return f


def jq_mini(expr: str, text: str) -> str:
    """Compact JSON lines of every output, like `jq -c expr`; raises _JqErr / ValueError on a bad expression or input."""
    f = _jq_compile(expr)
    return "\n".join(json.dumps(x, separators=(",", ":"), ensure_ascii=False) for x in f(json.loads(text)))


def _jq_real(expr: str, text: str):
    if not shutil.which("jq"):
        return None
    rc, out, _err = _run(["jq", "-c", expr], stdin=text.encode())
    return out.rstrip("\n") if rc == 0 else None


# ---------------------------------------------------------------------------------------------------------------
# #304 _select projection

_SELECT_PROP = {"type": "string", "description": "Optional jq filter applied to this tool's JSON result before it is returned, e.g. '.[] | {number, title}'. Omit for the full result."}


class SelectProxy:
    def __init__(self, upstream: FakeServer):
        self.up = upstream

    def list_tools(self) -> list[dict]:
        out = self.up.list_tools()
        for t in out:
            props = t["inputSchema"].get("properties")
            if isinstance(props, dict) and "_select" not in props:  # never clobber an argument the server already has
                props["_select"] = dict(_SELECT_PROP)
        return out

    def call(self, name: str, args: dict) -> dict:
        spec = next((t for t in self.up.tools if t["name"] == name), None)
        owns = spec is not None and "_select" in spec["inputSchema"].get("properties", {})
        if "_select" not in args or owns:
            return self.up.call(name, args)  # untouched, byte for byte
        args = dict(args)
        expr = args.pop("_select")
        res = self.up.call(name, args)
        if res.get("isError"):
            return res  # errors are never projected away
        try:
            text = res["content"][0]["text"]
            src = json.loads(text)
        except (KeyError, IndexError, ValueError):
            return {**res, "content": res["content"] + [{"type": "text", "text": "[_select ignored: result is not JSON]"}]}
        try:
            out = jq_mini(str(expr), text)
        except (_JqErr, ValueError, TypeError, RecursionError) as ex:
            return {**res, "content": res["content"] + [{"type": "text", "text": f"[_select ignored: {ex}; the full result is above]"}]}
        note = ""
        vals = [json.loads(l) for l in out.split("\n") if l]
        if src and vals and all(v is None or (isinstance(v, dict) and all(x is None for x in v.values())) for v in vals):
            note = "\n[_select note: every selected value is null; check the field names against the unfiltered result]"
        return {**res, "content": [{"type": "text", "text": out + note}]}


def sim_304() -> dict:
    up = FakeServer()
    px = SelectProxy(up)
    # --- schema cost, per tool and per turn
    plain, wrapped = up.list_tools(), px.list_tools()
    assert all("_select" in t["inputSchema"]["properties"] for t in wrapped), "every tool gets _select, including get_me which has no arguments"
    assert json.dumps(px.list_tools()) == json.dumps(wrapped), "listing twice must not stack arguments"
    add_tok = _block_tokens(wrapped) - _block_tokens(plain)
    per_tool = add_tok / len(plain)
    assert 30 <= per_tool <= 70, f"the extra argument costs about 45 tokens per tool, got {per_tool:.0f}"
    # --- ten tasks: (tool, args, jq, what the task needs, independent answer function over the FULL result)
    tasks = [
        ("list_issues", {"owner": "acme", "repo": "shop"}, '.[] | {number, title}', lambda r: [(x["number"], x["title"]) for x in r], lambda r: [(x["number"], x["title"]) for x in r]),
        ("list_issues", {"owner": "acme", "repo": "shop"}, '.[] | select(.state == "open") | {number}', lambda r: [x["number"] for x in r if x["state"] == "open"], lambda r: [x["number"] for x in r]),
        ("list_issues", {"owner": "acme", "repo": "shop"}, '.[] | select(.comments > 8) | {number, comments}', lambda r: sorted((x["number"], x["comments"]) for x in r if x["comments"] > 8), lambda r: sorted((x["number"], x["comments"]) for x in r)),
        ("list_issues", {"owner": "acme", "repo": "shop"}, '.[0:5] | .[] | {number, who: .user.login}', lambda r: [(x["number"], x["user"]["login"]) for x in r[:5]], lambda r: [(x["number"], x["who"]) for x in r]),
        ("list_issues", {"owner": "acme", "repo": "shop"}, 'length', lambda r: len(r), lambda r: r),
        ("list_pull_requests", {"owner": "acme", "repo": "shop"}, '.[] | select(.draft == false) | {number, title, base: .base.ref}', lambda r: [(x["number"], x["title"], x["base"]["ref"]) for x in r if not x["draft"]], lambda r: [(x["number"], x["title"], x["base"]) for x in r]),
        ("list_pull_requests", {"owner": "acme", "repo": "shop"}, '.[] | select(.mergeable_state == "behind") | {number}', lambda r: [x["number"] for x in r if x["mergeable_state"] == "behind"], lambda r: [x["number"] for x in r]),
        ("list_pull_requests", {"owner": "acme", "repo": "shop"}, '.[] | {number, add: .additions, del: .deletions}', lambda r: [(x["number"], x["additions"], x["deletions"]) for x in r], lambda r: [(x["number"], x["add"], x["del"]) for x in r]),
        ("list_pull_requests", {"owner": "acme", "repo": "shop"}, '.[] | select(.changed_files > 9) | {number, files: .changed_files}', lambda r: [(x["number"], x["changed_files"]) for x in r if x["changed_files"] > 9], lambda r: [(x["number"], x["files"]) for x in r]),
        ("list_pull_requests", {"owner": "acme", "repo": "shop"}, '[.[].number]', lambda r: [x["number"] for x in r], lambda r: r[0]),
    ]
    full_tok = proj_tok = 0
    for name, args, expr, truth, from_proj in tasks:
        full = px.call(name, args)
        proj = px.call(name, {**args, "_select": expr})
        ft, pt = full["content"][0]["text"], proj["content"][0]["text"]
        rows = [json.loads(l) for l in pt.split("\n") if l]
        got = from_proj(rows if len(rows) != 1 or isinstance(rows[0], dict) else rows[0]) if not expr.startswith("[") else from_proj([rows[0]])
        want = truth(json.loads(ft))
        assert (sorted(got) if isinstance(got, list) and got and isinstance(got[0], tuple) else got) == (sorted(want) if isinstance(want, list) and want and isinstance(want[0], tuple) else want), f"reader view: projection lost the answer for {expr}: {got!r} vs {want!r}"
        full_tok += tokens(ft)
        proj_tok += tokens(pt)
    assert not any("_select" in a for _n, a in up.calls), "the upstream server must never see _select"
    saving = 1 - proj_tok / full_tok
    assert saving > 0.9, f"projection should save >90% on these tasks, got {saving:.0%}"
    # --- the mini jq against real jq (the independent oracle), on the expressions above plus slices and arrays
    sample = json.dumps(up.issues[:12], separators=(",", ":"))
    oracle = "skipped (jq not installed)"
    if shutil.which("jq"):
        exprs = [t[2] for t in tasks[:5]] + [".[2:4] | .[] | .labels | length", "[.[] | .user.login]", ".[3].user", ".[] | select(.state != \"open\") | .number"]
        for expr in exprs:
            assert jq_mini(expr, sample) == _jq_real(expr, sample), f"mini jq disagrees with real jq on {expr!r}"
        oracle = f"agrees with real jq on {len(exprs)} expressions"
    # --- where it must NOT apply, and fail-open
    a = {"owner": "acme", "repo": "shop"}
    assert px.call("list_issues", a) == up.call("list_issues", a), "no _select: the result object is identical"
    up.fail_on.add("list_issues")
    err = px.call("list_issues", {**a, "_select": ".[] | {number}"})
    assert err["isError"] and "failed upstream" in err["content"][0]["text"], "an upstream error is never projected away"
    up.fail_on.clear()
    bad = px.call("list_issues", {**a, "_select": ".[] | {number"})
    assert len(bad["content"]) == 2 and "_select ignored" in bad["content"][1]["text"] and json.loads(bad["content"][0]["text"]) == up.issues, "a broken filter returns the full result plus a note"
    typo = px.call("list_issues", {**a, "_select": ".[] | {numbr}"})
    assert "every selected value is null" in typo["content"][0]["text"], "a misspelled field must not silently return nulls"
    nonjson = SelectProxy(type("U", (), {"tools": up.tools, "list_tools": up.list_tools, "call": lambda s, n, a: _text_result("plain text, not json")})()).call("get_file_contents", {**a, "_select": ".x"})
    assert nonjson["content"][0]["text"] == "plain text, not json" and "not JSON" in nonjson["content"][1]["text"], "a text result is returned whole"
    up.tools.append({"name": "own_select", "description": "has its own _select", "inputSchema": {"type": "object", "properties": {"_select": {"type": "string"}}, "required": []}})
    assert "_select" in px.list_tools()[-1]["inputSchema"]["properties"] and px.list_tools()[-1]["inputSchema"]["properties"]["_select"] == {"type": "string"}, "an existing _select is not overwritten"
    px.call("own_select", {"_select": "mine"})
    assert up.calls[-1] == ("own_select", {"_select": "mine"}), "a server that owns _select receives it"
    # --- the cost of the extra argument against what it saves, if the tool block is resent uncached every turn
    saved_per_call = (full_tok - proj_tok) / len(tasks)
    turns, call_turns = 30, (6, 14, 22)
    overhead = add_tok * (1.25 + 0.1 * (turns - 1))  # the longer schema sits in the cached prefix of all 30 requests
    gained = sum(saved_per_call * (1.25 + 0.1 * (turns - t)) for t in call_turns)  # a full result is written once and re-read on every later turn
    assert gained > 3 * overhead, f"with three large list calls in a 30-turn session the projection must clearly repay the schema overhead ({gained:.0f} vs {overhead:.0f})"
    return {"schema_tokens_added_per_tool": round(per_tool), "schema_tokens_added_per_turn": add_tok, "tool_block_tokens": _block_tokens(plain),
            "avg_tool_schema_chars": round(len(json.dumps(plain)) / len(plain)), "result_tokens_full": full_tok, "result_tokens_projected": proj_tok,
            "saving": f"{saving:.1%}", "billed_gain_vs_schema_overhead_30_turns_3_calls": f"{gained:.0f} vs {overhead:.0f}", "jq_oracle": oracle}


case(
    304,
    claim="A proxy adds an optional _select jq argument to every tool schema and applies it to the JSON result before it returns (never to errors, never when absent, never to a server that owns the argument); projections save >90% on ten list tasks, the answer computed from the projected rows equals the answer from the full result, and a broken or misspelled filter fails open with a note.",
    sim=sim_304,
)


# ---------------------------------------------------------------------------------------------------------------
# #387 snippet-then-expand wrapper for search-style results

_SNIP, _TRIM_OVER = 200, 260


class ExpandProxy:
    def __init__(self, upstream_search, store_dir: str):
        self.search_fn, self.dir = upstream_search, store_dir
        self.mem: dict[str, str] = {}

    def _path(self, hid: str) -> str:
        return os.path.join(self.dir, hid + ".txt")

    def tools(self) -> list[dict]:
        return [{"name": "search", "description": "Search the docs", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
                {"name": "expand", "description": "Return the full text of one search hit by its id.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}]

    @staticmethod
    def _snippet(body: str, query: str) -> str:
        terms = [t for t in _terms(query) if len(t) > 2]
        low = body.lower()
        at = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
        a = max(0, at - _SNIP // 4)
        a = body.rfind(" ", 0, a) + 1 if a else 0  # start on a word boundary
        b = body.find(" ", a + _SNIP)
        b = len(body) if b < 0 else b
        return ("..." if a else "") + body[a:b] + ("..." if b < len(body) else "")

    def call(self, name: str, args: dict) -> dict:
        if name == "expand":
            hid = str(args.get("id", ""))
            if not re.fullmatch(r"[0-9a-f]{12}", hid):
                return _text_result("expand: malformed id", True)
            if hid in self.mem:
                return _text_result(self.mem[hid])
            try:
                return _text_result(open(self._path(hid), encoding="utf-8").read())
            except OSError:
                return _text_result(f"expand: unknown or expired id {hid}; run the search again", True)  # never a wrong answer
        res = self.search_fn(args)
        try:
            doc = json.loads(res["content"][0]["text"])
            hits = doc if isinstance(doc, list) else next(v for v in doc.values() if isinstance(v, list)) if isinstance(doc, dict) and sum(isinstance(v, list) for v in doc.values()) == 1 else None
            ids = [h.get("id") for h in hits]
            if res.get("isError") or not hits or not all(isinstance(h, dict) and isinstance(h.get("body"), str) for h in hits) or len(set(ids)) != len(ids):
                return res
        except (KeyError, IndexError, ValueError, TypeError, StopIteration):
            return res
        trimmed = []
        for h in hits:
            if len(h["body"]) <= _TRIM_OVER:
                trimmed.append(h)  # short hits are left byte-identical
                continue
            hid = _sha(f"{h['id']}|{h['body']}", 12)
            self.mem[hid] = h["body"]
            _write(self._path(hid), h["body"])
            trimmed.append({**{k: v for k, v in h.items() if k != "body"}, "snippet": self._snippet(h["body"], str(args.get("query", ""))), "body_chars": len(h["body"]), "expand_id": hid})
        out = trimmed if isinstance(doc, list) else {k: (trimmed if v is hits else v) for k, v in doc.items()}
        return _text_result(out)


def _search_corpus(n: int = 24):
    g = _lcg(387)
    topics = [("cache", "restore key"), ("retry", "backoff factor"), ("timeout", "request deadline"), ("proxy", "no_proxy list"), ("oauth", "refresh token"), ("docker", "layer cache"), ("lint", "ruff config"), ("secrets", "masking rules")]
    docs = []
    for i in range(n):
        topic, detail = topics[i % len(topics)]
        facts = f"Setting {topic.upper()}_LIMIT to {10 + i} changes the {detail}; the default is {5 + i % 7}. "  # the answer span
        pre = _prose(g, 10 + next(g) % 80) + ". "
        post = _prose(g, 60 + next(g) % 120)
        docs.append({"id": f"doc-{i:03d}", "path": f"docs/{topic}/guide-{i}.md", "score": round(1 - i / 100, 2), "body": (pre + facts + post) if i % 5 else facts[:120]})
    return docs, topics


def sim_387() -> dict:
    docs, topics = _search_corpus()

    def upstream(args):
        q = set(_terms(str(args["query"])))
        ranked = sorted(docs, key=lambda d: -len(q & set(_terms(d["path"] + " " + d["body"]))))[:8]
        return _text_result({"results": ranked})

    with tempfile.TemporaryDirectory() as d:
        px = ExpandProxy(upstream, d)
        full_tok = prox_tok = answered_in_snippet = needed_expand = 0
        for k, (topic, detail) in enumerate(topics * 2):
            idx = [i for i in range(len(docs)) if i % len(topics) == k % len(topics) and i % 5][k // len(topics) % 2]
            answer = f"Setting {topic.upper()}_LIMIT to {10 + idx}"
            q = f"{topic} limit {detail}"
            full = upstream({"query": q})
            res = px.call("search", {"query": q})
            full_tok += tokens(full["content"][0]["text"])
            prox_tok += tokens(res["content"][0]["text"])
            hits = json.loads(res["content"][0]["text"])["results"]
            assert all("body" not in h or len(h["body"]) <= _TRIM_OVER for h in hits)
            assert all(h.get("path") and h.get("id") for h in hits), "id and path always survive"
            if any(answer in h.get("snippet", h.get("body", "")) for h in hits):
                answered_in_snippet += 1
                continue
            # the agent opens the hits whose snippet shares a query word, best first, until the answer shows up
            cands = [h for h in hits if "expand_id" in h and set(_terms(q)) & set(_terms(h["snippet"] + h["path"]))]
            for h in cands:
                ex = px.call("expand", {"id": h["expand_id"]})
                prox_tok += tokens(ex["content"][0]["text"])
                orig = next(x for x in docs if x["id"] == h["id"])["body"]
                assert ex["content"][0]["text"] == orig, "expand returns the original body byte for byte"
                if answer in ex["content"][0]["text"]:
                    needed_expand += 1
                    break
            else:
                raise AssertionError(f"reader view: the answer {answer!r} was lost for {q!r}")
        n_tasks = len(topics) * 2
        saving = 1 - prox_tok / full_tok
        assert saving > 0.35, f"snippets plus the hits actually opened should cost well under the full results, got {saving:.0%}"
        # --- must NOT apply: short hits identical; non-search shapes and errors pass through
        short = [{"id": "a", "path": "p", "body": "tiny"}, {"id": "b", "path": "q", "body": "also tiny"}]
        sp = ExpandProxy(lambda a: _text_result(short), d)
        assert sp.call("search", {"query": "x"}) == _text_result(short), "hits under the threshold are untouched"
        one = _text_result({"id": "doc-001", "path": "x", "body": "z" * 900})
        assert ExpandProxy(lambda a: one, d).call("search", {"query": "x"}) == one, "a single document (not a list of hits) passes through"
        dup = _text_result([{"id": "a", "body": "y" * 500}, {"id": "a", "body": "w" * 500}])
        assert ExpandProxy(lambda a: dup, d).call("search", {"query": "x"}) == dup, "duplicate hit ids: do not trim what cannot be addressed"
        err = _text_result("rate limited", True)
        assert ExpandProxy(lambda a: err, d).call("search", {"query": "x"}) == err
        # --- fail-open: proxy restarted (memory gone, files kept) still expands; files gone gives an error, not text from the wrong hit
        res = px.call("search", {"query": "cache limit restore key"})
        hid = next(h["expand_id"] for h in json.loads(res["content"][0]["text"])["results"] if "expand_id" in h)
        fresh = ExpandProxy(upstream, d)
        assert fresh.call("expand", {"id": hid}) == px.call("expand", {"id": hid}) and not fresh.call("expand", {"id": hid})["isError"]
        os.remove(os.path.join(d, hid + ".txt"))
        gone = ExpandProxy(upstream, d).call("expand", {"id": hid})
        assert gone["isError"] and "run the search again" in gone["content"][0]["text"]
        assert ExpandProxy(upstream, d).call("expand", {"id": "../../etc/passwd"})["isError"], "ids are validated: no path traversal"
    return {"tasks": n_tasks, "answered_from_the_snippet_alone": answered_in_snippet, "needed_expand": needed_expand, "tokens_full": full_tok,
            "tokens_snippets_plus_expands": prox_tok, "saving": f"{saving:.1%}"}


case(
    387,
    claim="Hit bodies over 260 characters in list-shaped search results become a ~200-character snippet (around the first query word) plus id, path and an expand_id; a synthetic expand(id) returns the original body byte for byte; short hits, single documents, errors and duplicate ids pass through, and an expired id is an explicit error. On 16 tasks the snippets plus the hits actually opened cost 46% fewer tokens than the full results; 9 of 16 needed an expand.",
    sim=sim_387,
)


# ---------------------------------------------------------------------------------------------------------------
# #503 gateway MCP: list, describe, invoke instead of N schemas

def _type_ok(v, t) -> bool:
    return {"string": isinstance(v, str), "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool), "array": isinstance(v, list)}.get(t, True)


class Gateway:
    def __init__(self, upstream: FakeServer, deny: frozenset = frozenset()):
        self.up, self.deny = upstream, deny

    def tools(self) -> list[dict]:
        obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
        return [{"name": "list_tools", "description": "List the tools of the wrapped server, one line each.", "inputSchema": obj({}, [])},
                {"name": "describe_tool", "description": "Full JSON schema of one wrapped tool.", "inputSchema": obj({"name": {"type": "string"}}, ["name"])},
                {"name": "invoke", "description": "Call a wrapped tool: invoke(name, arguments).", "inputSchema": obj({"name": {"type": "string"}, "arguments": {"type": "object"}}, ["name"])}]

    def call(self, tool: str, args: dict) -> dict:
        if tool == "list_tools":
            lines = []
            for t in self.up.list_tools():
                req = t["inputSchema"].get("required", [])
                opt = [p for p in t["inputSchema"]["properties"] if p not in req]
                lines.append(f"{t['name']}({', '.join(req)}{'; ' + ', '.join(opt) if opt else ''}): {t['description'].split(chr(10))[0].split('. ')[0].rstrip('.')}")
            return _text_result("\n".join(lines))
        spec = next((t for t in self.up.list_tools() if t["name"] == args.get("name")), None)
        if spec is None:
            import difflib
            near = difflib.get_close_matches(str(args.get("name")), [t["name"] for t in self.up.tools], n=3)
            return _text_result(f"unknown tool {args.get('name')!r}; did you mean: {', '.join(near) or 'call list_tools'}", True)
        if tool == "describe_tool":
            return _text_result(spec)
        if args["name"] in self.deny:
            return _text_result(f"{args['name']} is blocked by the gateway policy", True)
        a = args.get("arguments") or {}
        sch = spec["inputSchema"]
        bad = [f"missing {r}" for r in sch["required"] if r not in a] + [f"{k}: expected {sch['properties'][k]['type']}" for k in a if k in sch["properties"] and not _type_ok(a[k], sch["properties"][k]["type"])] + [f"unknown argument {k}" for k in a if k not in sch["properties"]]
        if bad:
            return _text_result(f"invalid arguments for {args['name']}: {'; '.join(bad)}. Schema: {json.dumps(sch['properties'], separators=(',', ':'))}", True)
        return self.up.call(args["name"], a)


def sim_503() -> dict:
    up = FakeServer()
    gw = Gateway(up, deny=_DESTRUCTIVE)
    full = _block_tokens(up.tools)
    gate = _block_tokens(gw.tools())
    listing = tokens(gw.call("list_tools", {})["content"][0]["text"])
    assert len(up.tools) == len(_GH) == 48
    assert gate + listing < 0.2 * full, f"gateway block + one list should be under 20% of the full dump ({gate}+{listing} vs {full})"
    assert 1100 <= len(json.dumps(up.tools)) / len(up.tools) <= 1500, "fixture schemas should be about as big as the real ones (1.4k chars average over three real tools)"
    # --- equivalence: for every non-blocked tool a valid call through the gateway equals the direct call
    ok_args = lambda t: {p: ("x" if _P[p][0] == "string" else 1 if _P[p][0] == "number" else [] if _P[p][0] == "array" else True) for p in t["inputSchema"]["required"]}  # noqa: E731
    for t in up.tools:
        a = ok_args(t)
        direct = up.call(t["name"], a)
        via = gw.call("invoke", {"name": t["name"], "arguments": a})
        if t["name"] in _DESTRUCTIVE:
            assert via["isError"] and "blocked" in via["content"][0]["text"] and via != direct
        else:
            assert via == direct, f"gateway changed the result of {t['name']}"
    # --- ten tasks: how many tools each needs (describe once per tool per session, list once)
    tasks = [["list_issues"], ["list_issues", "issue_read"], ["list_pull_requests", "pull_request_read"], ["get_file_contents"], ["list_commits", "get_commit"],
             ["issue_read", "add_issue_comment"], ["actions_list", "get_job_logs"], ["list_branches", "list_tags"], ["pull_request_read", "get_check_run", "get_job_logs"], ["get_me", "list_issues"]]
    described: set[str] = set()
    extra_calls, desc_tok = 1, 0
    for need in tasks:
        for name in need:
            if name not in described:
                described.add(name)
                extra_calls += 1
                desc_tok += tokens(json.dumps(gw.call("describe_tool", {"name": name})["content"][0]["text"]))
    total_gateway = gate + listing + desc_tok
    assert total_gateway < full, "the 10 tasks together must still cost less than the full dump"
    assert len(described) < len(up.tools)
    # --- break-even: describing d tools costs d * avg; the dump wins once d reaches this many
    avg_desc = desc_tok / len(described)
    breakeven_tools = int((full - gate - listing) / avg_desc)
    # --- invalid use: unknown tool, wrong argument type, missing argument, unknown argument: errors that carry the fix
    e = gw.call("invoke", {"name": "list_issue", "arguments": {}})
    assert e["isError"] and "list_issues" in e["content"][0]["text"], "a typo gets near matches"
    e = gw.call("invoke", {"name": "list_issues", "arguments": {"owner": "acme", "repo": "shop", "perPage": "ten"}})
    assert e["isError"] and "perPage: expected number" in e["content"][0]["text"] and '"owner"' in e["content"][0]["text"], "a type error carries the schema (no extra describe call needed)"
    assert gw.call("invoke", {"name": "list_issues", "arguments": {"owner": "acme"}})["isError"]
    assert gw.call("invoke", {"name": "get_me", "arguments": {"nope": 1}})["isError"]
    up.fail_on.add("list_issues")
    r = gw.call("invoke", {"name": "list_issues", "arguments": {"owner": "a", "repo": "b"}})
    assert r["isError"] and "failed upstream" in r["content"][0]["text"], "an upstream failure is forwarded as an error, not swallowed"
    # --- the catch: a host permission rule can only name the gateway's invoke, so per-tool allow/deny moves into the gateway
    host_names = {f"mcp__gateway__{t['name']}" for t in gw.tools()}
    assert "mcp__gateway__merge_pull_request" not in host_names and "mcp__gateway__invoke" in host_names, "the host can no longer tell merge_pull_request from get_me"
    billed_native = _billed(full, [400] + [150] * 11)
    billed_gw = _billed(gate, [400, listing] + [desc_tok // len(described) + 100] * 3 + [150] * 7)  # 3 tools described, 12 turns
    assert billed_gw < billed_native, "for a 12-turn session that uses 3 tools the gateway is cheaper even with the extra turns"
    all_described = _billed(gate, [400, listing] + [desc_tok // len(described) + 100] * 48 + [150] * 7)
    return {"billed_native_vs_gateway_12_turns_3_tools": f"{billed_native:.0f} vs {billed_gw:.0f}", "billed_gateway_if_all_48_tools_are_described": round(all_described),
            "upstream_tools": len(up.tools), "tool_block_tokens_full": full, "tool_block_tokens_gateway": gate, "list_tools_tokens": listing,
            "describe_tokens_for_10_tasks": desc_tok, "extra_calls_for_10_tasks": extra_calls, "distinct_tools_used": len(described),
            "tools_you_can_describe_before_the_full_dump_is_cheaper": breakeven_tools, "avg_schema_tokens": round(full / 48),
            "saving_for_10_tasks": f"{1 - total_gateway / full:.0%}"}


case(
    503,
    claim="A gateway exposing list_tools, describe_tool and invoke replaces 48 tool schemas by 3 plus a one-line-per-tool list; every non-blocked call returns exactly what the direct call returns, unknown names, wrong types and missing arguments return errors that name the fix, upstream failures are forwarded, and per-tool deny rules have to move into the gateway because the host only sees 'invoke'.",
    sim=sim_503,
)


# ---------------------------------------------------------------------------------------------------------------
# #684 bridge MCP servers into shell CLIs (mcp2cli-style). REAL bash, python and jq processes.

_BRIDGE = r'''
import json, os, sys
SCH = {t["name"]: t for t in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.json")))}
def die(code, msg):
    sys.stderr.write(msg + "\n"); sys.exit(code)
argv = sys.argv[1:]
if not argv or argv[0] in ("-h", "--help", "list"):
    for n, t in SCH.items():
        print(n + ": " + t["description"].split("\n")[0].split(". ")[0])
    sys.exit(0)
name = argv[0]
if name not in SCH:
    import difflib
    die(64, "unknown tool %r; did you mean: %s" % (name, ", ".join(difflib.get_close_matches(name, SCH, n=3)) or "run `list`"))
props, req = SCH[name]["inputSchema"]["properties"], SCH[name]["inputSchema"]["required"]
if argv[1:2] == ["--help"]:
    print(name + " " + " ".join(("--%s <%s>" % (p, s["type"])) + ("" if p in req else "?") for p, s in props.items()))
    sys.exit(0)
args, i = {}, 1
while i < len(argv):
    a = argv[i]
    if not a.startswith("--") or a[2:] not in props or i + 1 >= len(argv):
        die(64, "bad argument %r for %s" % (a, name))
    k, v, t = a[2:], argv[i + 1], props[a[2:]]["type"]
    try:
        args[k] = int(v) if t == "number" else {"true": True, "false": False}[v] if t == "boolean" else json.loads(v) if t == "array" else v
    except (ValueError, KeyError):
        die(64, "--%s expects %s, got %r" % (k, t, v))
    i += 2
miss = [r for r in req if r not in args]
if miss:
    die(64, "missing: " + ", ".join("--" + m for m in miss))
if os.environ.get("BRIDGE_FAIL") == name:
    die(2, "tool error: " + name + " failed upstream")
print(json.dumps({"tool": name, "received": args}))
'''


def sim_684() -> dict:
    up = FakeServer()
    have_jq = bool(shutil.which("jq"))
    with tempfile.TemporaryDirectory() as d:
        _write(os.path.join(d, "tools.json"), json.dumps(up.tools))
        _write(os.path.join(d, "bridge.py"), _BRIDGE)
        _write(os.path.join(d, "gh"), f'#!/bin/bash\nexec {shlex.quote(sys.executable)} -I {shlex.quote(os.path.join(d, "bridge.py"))} "$@"\n', 0o755)
        env = {"PATH": f"{d}:/usr/local/bin:/usr/bin:/bin"}
        sh = lambda cmd, extra=None: _run(["bash", "-c", cmd], cwd=d, env={**env, **(extra or {})})  # noqa: E731
        # --- schemas leave the prompt: the agent pays for `list` once and `--help` for the tools it uses
        rc, listing, _ = sh("gh list")
        assert rc == 0 and len(listing.splitlines()) == 48
        # --- argument fidelity: tricky strings and typed values reach the 'server' as the right JSON types
        nasty = 'a "quoted" $HOME `x` \\n it\'s; rm -rf / &'
        rc, out, err = sh("gh add_issue_comment --owner acme --repo shop --number 7 --body " + shlex.quote(nasty))
        got = json.loads(out)["received"]
        assert rc == 0 and got == {"owner": "acme", "repo": "shop", "number": 7, "body": nasty}, got
        rc, out, _ = sh("gh push_files --owner o --repo r --branch b --message m --files " + shlex.quote('[{"path":"a.txt","content":"é\\n"}]'))
        assert json.loads(out)["received"]["files"] == [{"path": "a.txt", "content": "é\n"}]
        rc, out, _ = sh("gh create_repository --name n --private true")
        assert json.loads(out)["received"] == {"name": "n", "private": True}
        # --- exit codes and streams: usage errors are 64 with nothing on stdout; a tool error is 2; unknown tools get near matches
        rc, out, err = sh("gh list_issues --owner acme")
        assert (rc, out) == (64, "") and "--repo" in err
        rc, out, err = sh("gh list_issue --owner a --repo b")
        assert rc == 64 and "list_issues" in err
        rc, out, err = sh("gh list_issues --owner a --repo b --perPage ten")
        assert rc == 64 and "expects number" in err
        rc, out, err = sh("gh list_issues --owner a --repo b", {"BRIDGE_FAIL": "list_issues"})
        assert (rc, out) == (2, "") and "failed upstream" in err
        # --- results flow through jq / head in the SAME command, and the pipeline's status is the last stage's unless pipefail
        if have_jq:
            rc, out, _ = sh("gh list_issues --owner acme --repo shop --perPage 5 | jq -c '.received.perPage'")
            assert (rc, out.strip()) == (0, "5")
            rc, out, _ = sh("set -o pipefail; gh list_issues --owner acme | jq -c .", None)
            assert rc == 64, "with pipefail a bridge failure is not hidden by jq"
        # --- the accounting. native MCP resends every schema each turn; the bridge pays list + help once, plus the command text.
        native_schema = _block_tokens(up.tools)
        help_tok = tokens(sh("gh list_issues --help")[1])
        list_tok = tokens(listing)

        avg_cmd = 30  # tokens of command text per call

        def session(schema, results, kept, calls_extra):
            """(native raw tokens, native billed, bridge raw tokens, bridge billed, native turns, bridge turns)"""
            n_new = [500] + [80 + r for r in results] + [80] * 2
            b_new = [500, avg_cmd + list_tok] + [avg_cmd + help_tok] * calls_extra + [avg_cmd + k for k in kept] + [80] * 2
            return (schema * len(n_new) + sum(n_new), _billed(schema, n_new), sum(b_new), _billed(0, b_new), len(n_new), len(b_new))

        gh = session(native_schema, [4000] * 8, [150] * 8, 4)
        br_schema = native_schema * 25 // 48  # a browser-shaped server: 25 tools, results are page snapshots the agent reads in full
        bw = session(br_schema, [3500] * 10, [3400] * 10, 6)
        raw_gh, billed_gh = 1 - gh[2] / gh[0], 1 - gh[3] / gh[1]
        raw_bw, billed_bw = 1 - bw[2] / bw[0], 1 - bw[3] / bw[1]
        assert raw_gh > 0.5 and billed_gh > 0.3, f"a many-tool server with jq-filterable results must win (raw {raw_gh:.0%}, billed {billed_gh:.0%})"
        assert billed_bw < 0.25 and billed_gh > billed_bw + 0.15, f"results read in full + a cached schema: the bridge gains little ({billed_bw:+.0%} billed vs {billed_gh:+.0%} for the many-tool server)"
        assert raw_bw > billed_bw + 0.1, "counting raw tokens flatters the bridge compared with billing: that gap is why per-call size alone misleads"
        assert gh[5] > gh[4], "the bridge needs extra turns (list, help) for the same work"
    return {"native_schema_tokens": native_schema, "bridge_list_tokens": list_tok, "bridge_help_tokens": help_tok,
            "many_tool_server_raw_saving": f"{raw_gh:.0%}", "many_tool_server_billed_saving": f"{billed_gh:.0%}", "turns_native_vs_bridge": f"{gh[4]} vs {gh[5]}",
            "browser_like_raw_saving": f"{raw_bw:.0%}", "browser_like_billed_saving": f"{billed_bw:+.0%}", "jq": "real" if have_jq else "skipped"}


case(
    684,
    claim="A CLI bridge exposes 48 MCP tools as shell commands (list, <tool> --help, <tool> --arg value) with typed arguments that survive hostile quoting, exit status 64 for usage errors, 2 for tool errors and nothing on stdout when it fails; schemas leave the prompt, and a cache-aware accounting model (not Checkly's measurement, which it does not reproduce: ~+16% here vs ~0% there) shows a large win for a many-tool server with filterable results and a small one when every result must be read in full.",
    sim=sim_684,
)


# ===============================================================================================================
# Hookless agents: #189 PATH shims and an Aider test-cmd runner, #579 BASH_ENV stdout filter, #946 SHELL wrapper (SIMS)
# REAL bash, python and (for git) git processes in a temp dir. stdout and stderr are always captured on separate pipes, and
# no assertion depends on how two pipeline stages interleave (the only merged stream is the Aider runner's own `2>&1`, a
# single writer).
#
# MEASURED ON THIS BOX (bash 5.2.21, dash as /bin/sh), where the ideas' text and the observation differ:
#   #579  says 'login shells skip BASH_ENV'. `bash -lc` does NOT skip it here (profile and BASH_ENV are both read); `bash -ic`
#         (interactive) and `sh -c` (dash) do skip it. The sim asserts what bash does, not what the idea says.
#   #579  BASH_ENV is silently IGNORED when bash's stdin is a socket: bash then assumes it was started by rshd/sshd and reads ~/.bashrc
#         instead. This is what made an earlier version of this gate flaky (the runner handed its children a socket in some calls and
#         a pipe or /dev/null in others), and it is a real hazard for agents that spawn `bash -c` over a socketpair.
#   #579  a BASH_ENV hook that does `exec > >(filter)` LOSES ALL STDOUT when the filter cannot start (the substitution dies,
#         the pipe has no reader): the hook must test the filter first. And a nested `bash -c` inside `$(...)` would be
#         filtered too, changing what a script captures, unless the hook marks itself with an environment variable.

_FILTER = r'''
import hashlib, os, re, sys
PACKS = {
    "generic": [],
    "pytest": [r"^platform ", r"^cachedir: ", r"^rootdir: ", r"^configfile: ", r"^plugins: ", r"^collected \d+ items?\s*$", r"^\S+\.py \.+\s*(\[\s*\d+%\])?\s*$"],
    "npm": [r"^npm (WARN deprecated|notice)\b", r"^\s*$"],
    "pip": [r"^Requirement already satisfied", r"^\s+Using cached ", r"^Collecting ", r"^\s+Downloading ", r"^Installing collected packages"],
    "docker": [r"^#\d+ (DONE|CACHED|\[internal\])", r"^#\d+ sha256:", r"^#\d+ transferring"],
    "cargo": [r"^\s+(Compiling|Downloaded|Downloading|Fresh|Locking|Adding) "],
    "git": [r"^(remote: )?(Counting|Compressing|Enumerating|Receiving|Resolving|Writing|Total|Delta) "],
}
args = sys.argv[1:]
pack, save = "generic", None
i = 0
while i < len(args):
    if args[i] == "--pack":
        pack = args[i + 1]; i += 2
    elif args[i] == "--save":
        save = args[i + 1]; i += 2
    elif args[i] == "--cmd":  # pack chosen from the command's first word (the Aider runner)
        first = os.path.basename(args[i + 1].split()[0]) if args[i + 1].split() else ""
        pack = first if first in PACKS else "generic"; i += 2
    else:
        i += 1
data = sys.stdin.buffer.read()
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    sys.stdout.buffer.write(data); sys.exit(0)
rx = [re.compile(r) for r in PACKS.get(pack, [])]
lines = text.split("\n")
out, dropped, k = [], 0, 0
while k < len(lines):
    if any(r.search(lines[k]) for r in rx):
        dropped += 1; k += 1; continue
    j = k
    while j < len(lines) and lines[j] == lines[k]:
        j += 1
    if pack == "generic" and j - k >= 3 and lines[k].strip():
        out += [lines[k], f"[last line repeated {j - k - 1} more times]"]; dropped += j - k - 2
    else:
        out += lines[k:j]
    k = j
res = "\n".join(out)
if dropped and len(res) < len(text):
    where = ""
    if save:
        try:
            os.makedirs(save, exist_ok=True)
            p = os.path.join(save, hashlib.sha1(data).hexdigest()[:8] + ".out")
            open(p, "wb").write(data)
            where = f", original in {p}"
        except OSError:
            pass
    res += ("" if res.endswith("\n") else "\n") + f"[ctxprune: {dropped} noise lines dropped{where}]\n"
    sys.stdout.write(res)
else:
    sys.stdout.buffer.write(data)
'''

_SHIM = r'''#!/bin/bash
# ctxprune shim for @PROG@: run the real binary, filter its stdout, keep stderr and the exit status.
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
real=""
IFS=: read -ra dirs <<< "$PATH"
for d in "${dirs[@]}"; do
  [ "$d" = "$self" ] && continue
  if [ -x "$d/@PROG@" ]; then real="$d/@PROG@"; break; fi
done
if [ -z "$real" ]; then echo "ctxprune shim: real @PROG@ not found on PATH" >&2; exit 127; fi
case "$1" in @SUBS@) ;; *) exec "$real" "$@" ;; esac          # only the sub-commands that are noisy and not needed verbatim
if [ -t 1 ] || [ -n "$CTXPRUNE_BYPASS" ] || [ -n "$CTXPRUNE_IN_SHIM" ]; then exec "$real" "$@"; fi   # a human, an opt-out, or a nested call
export CTXPRUNE_IN_SHIM=1
tmp="$(mktemp)"
"$real" "$@" > "$tmp"; rc=$?
if ! "$CTXPRUNE_PY" -I "$CTXPRUNE_FILTER" --pack @PACK@ --save "${CTXPRUNE_SAVE:-/tmp/ctxprune-saved}" < "$tmp"; then cat "$tmp"; fi   # filter broken: original output
rm -f "$tmp"
exit $rc
'''

_RUNNER = r'''#!/bin/bash
# ctxprune-run: for tools that take a test or lint command string (Aider --test-cmd). Output of stdout and stderr is ONE stream here.
tmp="$(mktemp)"
bash -c "$*" > "$tmp" 2>&1; rc=$?
if ! "$CTXPRUNE_PY" -I "$CTXPRUNE_FILTER" --cmd "$*" --save "${CTXPRUNE_SAVE:-/tmp/ctxprune-saved}" < "$tmp"; then cat "$tmp"; fi
rm -f "$tmp"
exit $rc
'''

_SHIMS = {"pytest": ("*", "pytest"), "npm": ("install|ci|test", "npm"), "pip": ("install|uninstall", "pip"), "docker": ("build", "docker"),
          "cargo": ("build|test|check", "cargo"), "git": ("fetch|pull|push|clone", "git")}


def _transcripts() -> dict[str, dict[str, tuple[str, int]]]:
    """Recorded-shape output of the six tools: {tool: {'pass': (stdout, rc), 'fail': (stdout, rc)}}. Reconstructed from memory."""
    py_head = ["============================= test session starts ==============================", "platform linux -- Python 3.13.18, pytest-9.1.1, pluggy-1.6.0", "cachedir: .pytest_cache", "rootdir: /work/shop", "configfile: pyproject.toml", "plugins: anyio-4.9.0", "collected 120 items", ""]
    files = [f"tests/test_{m}.py" for m in ("cart", "checkout", "pricing", "tax", "users", "coupons")]
    prog = [f"{f} {'.' * 20}   [{(i + 1) * 100 // 6:3d}%]" for i, f in enumerate(files)]
    prog_f = prog[:3] + ["tests/test_tax.py ....F...............   [ 66%]"] + prog[4:]
    fail = ["", "=================================== FAILURES ===================================", "_______________________ test_tax_rounds_half_even ________________________", ">       assert tax_cents(1050, rate=0.21) == 220", "E       assert 221 == 220",
            "tests/test_tax.py:17: AssertionError", "=========================== short test summary info ============================", "FAILED tests/test_tax.py::test_tax_rounds_half_even - assert 221 == 220", "======================== 1 failed, 119 passed in 3.18s ========================"]
    pytest_pass = "\n".join(py_head + prog + ["", "============================= 120 passed in 3.02s =============================", ""])
    pytest_fail = "\n".join(py_head + prog_f + fail + [""])
    npm_noise = [f"npm WARN deprecated {p}@{v}: this package is no longer supported" for p, v in (("request", "2.88.2"), ("uuid", "3.4.0"), ("har-validator", "5.1.5"), ("core-js", "2.6.12"), ("left-pad", "1.3.0"), ("mkdirp", "0.5.1"))] * 3
    npm_pass = "\n".join(npm_noise + ["", "added 412 packages, and audited 413 packages in 14s", "", "73 packages are looking for funding", "  run `npm fund` for details", "", "found 0 vulnerabilities", "npm notice New major version of npm available! 10.9.9 -> 11.0.0", ""])
    npm_fail = "\n".join(npm_noise + ["npm ERR! code ERESOLVE", "npm ERR! ERESOLVE could not resolve", "npm ERR! While resolving: shop@1.0.0", "npm ERR! Found: react@18.3.1", "npm ERR! Could not resolve dependency: peer react@\"^17.0.0\" from react-old@3.1.0", ""])
    pip_noise = []
    for n in ("numpy", "requests", "urllib3", "certifi", "idna", "charset-normalizer", "pandas", "pytz", "six", "tzdata"):
        pip_noise += [f"Collecting {n}", f"  Downloading {n}-1.0.0-py3-none-any.whl (1.2 MB)"]
    pip_noise += [f"Requirement already satisfied: {n} in /usr/lib/python3/dist-packages ({i}.0)" for i, n in enumerate(("pip", "wheel", "setuptools", "packaging", "attrs", "toml"))]
    pip_pass = "\n".join(pip_noise + ["Installing collected packages: pytz, tzdata, six, numpy, pandas", "Successfully installed numpy-2.1.0 pandas-2.2.3 pytz-2024.2 six-1.16.0 tzdata-2024.2", ""])
    pip_fail = "\n".join(pip_noise + ["ERROR: Could not find a version that satisfies the requirement fancylib==9.9 (from versions: 1.0, 1.1, 2.0)", "ERROR: No matching distribution found for fancylib==9.9", ""])
    dk = [f"#{n} {t}" for n in range(2, 12) for t in ("[internal] load metadata for docker.io/library/python:3.13-slim", f"DONE 0.{n}s", f"sha256:{_sha(str(n), 40)} 1.2MB / 1.2MB", "transferring context: 2B done")]
    docker_pass = "\n".join(dk + ["#12 [7/7] RUN pip install -r requirements.txt", "#12 DONE 8.4s", "#13 exporting to image", "#13 naming to docker.io/library/shop:latest done", ""])
    docker_fail = "\n".join(dk + ["#12 [7/7] RUN pip install -r requirements.txt", "#12 3.114 ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'", "#12 ERROR: process \"/bin/sh -c pip install -r requirements.txt\" did not complete successfully: exit code: 1", ""])
    crates = ("serde", "serde_json", "tokio", "bytes", "mio", "libc", "regex", "memchr", "itoa", "ryu", "once_cell", "hashbrown")
    cg = [f"   Compiling {c} v1.{i}.0" for i, c in enumerate(crates)] + [f" Downloading {c} v1.{i}.0" for i, c in enumerate(crates)]
    cargo_pass = "\n".join(cg + ["   Compiling shop v0.1.0 (/work/shop)", "warning: unused variable: `x`", " --> src/main.rs:4:9", "    Finished `dev` profile [unoptimized + debuginfo] target(s) in 6.31s", ""])
    cargo_fail = "\n".join(cg + ["   Compiling shop v0.1.0 (/work/shop)", "error[E0308]: mismatched types", " --> src/main.rs:12:20", "   |", "12 |     let n: u32 = \"7\";", "   |            ---   ^^^ expected `u32`, found `&str`", "error: could not compile `shop` (bin \"shop\") due to 1 previous error", ""])
    git_noise = ["remote: Enumerating objects: 412, done.", "remote: Counting objects: 100% (412/412), done.", "remote: Compressing objects: 100% (201/201), done.", "Receiving objects: 100% (400/400), 1.2 MiB | 4.0 MiB/s, done.", "Resolving deltas: 100% (180/180), completed with 12 local objects."]
    git_pass = "\n".join(git_noise + ["From github.com:acme/shop", "   a1b2c3d..e4f5a6b  main       -> origin/main", ""])
    git_fail = "\n".join(git_noise + ["fatal: couldn't find remote ref refs/heads/nope", ""])
    return {"pytest": {"pass": (pytest_pass, 0), "fail": (pytest_fail, 1)}, "npm": {"pass": (npm_pass, 0), "fail": (npm_fail, 1)}, "pip": {"pass": (pip_pass, 0), "fail": (pip_fail, 1)},
            "docker": {"pass": (docker_pass, 0), "fail": (docker_fail, 1)}, "cargo": {"pass": (cargo_pass, 0), "fail": (cargo_fail, 101)}, "git": {"pass": (git_pass, 0), "fail": (git_fail, 128)}}


def _pty_run(cmd: str, cwd: str, env: dict) -> tuple[str, str, int]:
    import select
    m, s = os.openpty()
    p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=s, stderr=subprocess.PIPE)
    os.close(s)
    err = p.stderr.read().decode()
    p.wait()
    out = b""
    while select.select([m], [], [], 0.2)[0]:
        try:
            chunk = os.read(m, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.close(m)
    return out.decode("utf-8", "replace").replace("\r\n", "\n"), err, p.returncode


def sim_189() -> dict:
    tr = _transcripts()
    with tempfile.TemporaryDirectory() as d:
        shims, real, data = os.path.join(d, "shims"), os.path.join(d, "real"), os.path.join(d, "data")
        for prog, (subs, pack) in _SHIMS.items():
            _write(os.path.join(shims, prog), _SHIM.replace("@PROG@", prog).replace("@SUBS@", subs).replace("@PACK@", pack), 0o755)
            # the 'real' tool: replays a recorded transcript on stdout, a deprecation line on stderr, and exits with the recorded status
            _write(os.path.join(real, prog), f'#!/bin/bash\nmode=pass; for a in "$@"; do [ "$a" = --fail ] && mode=fail; done\ncat "{data}/{prog}.$mode.out"\necho "{prog}: stderr line" >&2\nexit "$(cat "{data}/{prog}.$mode.rc")"\n', 0o755)
            for mode, (out, rc) in tr[prog].items():
                _write(os.path.join(data, f"{prog}.{mode}.out"), out)
                _write(os.path.join(data, f"{prog}.{mode}.rc"), str(rc))
        _write(os.path.join(shims, "ctxprune-run"), _RUNNER, 0o755)
        # real git for the pass-through guarantee: the agent edits from `git diff`, so the shim must never touch it
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        git = lambda *a: _run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo)  # noqa: E731
        git("init", "-q", "-b", "main")
        _write(os.path.join(repo, "a.py"), "".join(f"def f{i}():\n    return {i}\n" for i in range(40)))
        git("add", ".")
        git("commit", "-qm", "init")
        _write(os.path.join(repo, "a.py"), "".join(f"def f{i}():\n    return {i + (i == 7)}\n" for i in range(40)))
        env = {"PATH": f"{shims}:{real}:/usr/local/bin:/usr/bin:/bin", "CTXPRUNE_PY": sys.executable, "CTXPRUNE_FILTER": os.path.join(d, "filter.py"), "CTXPRUNE_SAVE": os.path.join(d, "saved")}
        _write(env["CTXPRUNE_FILTER"], _FILTER)
        direct_env = {**env, "PATH": f"{real}:/usr/local/bin:/usr/bin:/bin"}
        sub = {"pytest": "-q", "npm": "install", "pip": "install x", "docker": "build .", "cargo": "build", "git": "fetch"}
        before = after = 0
        for prog in _SHIMS:
            for mode in ("pass", "fail"):
                cmd = f"{prog} {sub[prog]}" + (" --fail" if mode == "fail" else "")
                rc0, out0, err0 = _run(["bash", "-c", cmd], cwd=d, env=direct_env)
                rc1, out1, err1 = _run(["bash", "-c", cmd], cwd=d, env=env)
                assert rc0 == rc1 == tr[prog][mode][1], f"{cmd}: exit status {rc0} vs {rc1} vs recorded {tr[prog][mode][1]}"
                assert err0 == err1, f"{cmd}: stderr must pass through untouched"
                body = [l for l in out1.split("\n") if not l.startswith("[ctxprune:")]
                it = iter(out0.split("\n"))
                assert all(l in it for l in body), f"{cmd}: the filtered output must be an ordered subset of the original"
                for l in out0.split("\n"):
                    if re.search(r"FAILED|^E {3}|ERROR|fatal:|error\[|warning:|Successfully installed|passed|Finished", l):
                        assert l in out1, f"{cmd}: error or result line lost: {l!r}"
                assert len(out1) <= len(out0)
                before, after = before + len(out0), after + len(out1)
                saved = [m for m in re.findall(r"original in (\S+\.out)", out1)]
                for pth in saved:
                    assert open(pth, encoding="utf-8").read() == out0, "the saved original is the full unfiltered output"
        assert after < 0.6 * before, f"the noise packs should drop well over 40% across the six tools, got {1 - after / before:.0%}"
        # --- must NOT apply: a sub-command outside the list, git diff/log/show byte for byte (real git), an opt-out, a tty
        for g in ("diff", "diff --stat", "log -p -2", "show HEAD", "status --short"):
            r0 = _run(["bash", "-c", f"/usr/bin/git {g}"], cwd=repo, env=direct_env)
            r1 = _run(["bash", "-c", f"git {g}"], cwd=repo, env={**env, "PATH": f"{shims}:/usr/local/bin:/usr/bin:/bin"})
            assert r0 == r1 and r0[1], f"git {g} must be byte-identical through the shim"
        rc, out, _ = _run(["bash", "-c", "pip --version"], cwd=d, env=env)
        assert out == tr["pip"]["pass"][0], "a sub-command that is not on the shim's list runs untouched"
        rc, out, _ = _run(["bash", "-c", "pytest -q"], cwd=d, env={**env, "CTXPRUNE_BYPASS": "1"})
        assert out == tr["pytest"]["pass"][0], "CTXPRUNE_BYPASS=1 hands the real output through"
        out, err, rc = _pty_run("pip install x", d, env)
        assert out == tr["pip"]["pass"][0] and rc == 0, "when stdout is a terminal the shim execs the real binary: a human sees everything"
        # --- fail-open
        rc, out, err = _run(["bash", "-c", "pytest -q --fail"], cwd=d, env={**env, "CTXPRUNE_FILTER": "/nonexistent/filter.py"})
        assert (rc, out) == (1, tr["pytest"]["fail"][0]), "a broken filter returns the original output and the real exit status"
        rc, out, err = _run(["bash", "-c", "pytest -q"], cwd=d, env={**env, "PATH": shims + ":/usr/bin:/bin"})
        assert rc == 127 and "real pytest not found" in err and out == "", "no real binary: say so, exit 127"
        # --- the Aider runner: one merged stream, exit status kept, savings repeat on every resent turn
        runner = os.path.join(shims, "ctxprune-run")
        rc0, out0, _ = _run(["bash", "-c", "pytest -q --fail 2>&1"], cwd=d, env=direct_env)
        rc1, out1, _ = _run([runner, "pytest -q --fail"], cwd=d, env=direct_env)
        assert rc0 == rc1 == 1 and "E       assert 221 == 220" in out1 and "tax_rounds_half_even - assert 221 == 220" in out1 and "pytest: stderr line" in out1
        assert len(out1) < 0.8 * len(out0)
        rc2, out2, _ = _run([runner, "pytest -q"], cwd=d, env=direct_env)
        assert rc2 == 0
        turns = 5
        direct_cum, wrapped_cum = turns * tokens(out0), turns * tokens(out1)  # the failing output stays in the chat and is resent every turn
        assert wrapped_cum < 0.8 * direct_cum
    return {"bytes_direct": before, "bytes_shimmed": after, "saved": f"{1 - after / before:.0%}", "exit_codes_checked": 12, "aider_chat_tokens_5_turns": f"{direct_cum} vs {wrapped_cum} ({-100 * (direct_cum - wrapped_cum) / direct_cum:+.0f}%)"}


case(
    189,
    claim="PATH shims for pytest, npm, pip, docker, cargo and git run the real binary, drop only the pack's noise lines, keep stderr and the exit status (checked on 12 real bash runs), save the full original, and leave git diff/log/show, unlisted sub-commands, terminals and opt-outs byte-identical; a broken filter returns the original; an Aider runner does the same for a command string.",
    sim=sim_189,
)


# ---------------------------------------------------------------------------------------------------------------
# #579 BASH_ENV stdout filter

_BASH_ENV_SAFE = '''# ctxprune BASH_ENV hook
if [ -z "$CTXPRUNE_ACTIVE" ] && [ -r "$CTXPRUNE_FILTER" ]; then
  export CTXPRUNE_ACTIVE=1
  exec > >("$CTXPRUNE_PY" -I "$CTXPRUNE_FILTER" --pack generic)
fi
'''
_BASH_ENV_NAIVE = 'export CTXPRUNE_ACTIVE_NAIVE=1\nexec > >("$CTXPRUNE_PY" -I "$CTXPRUNE_FILTER" --pack generic)\n'


def sim_579() -> dict:
    with tempfile.TemporaryDirectory() as d:
        _write(os.path.join(d, "filter.py"), _FILTER)
        _write(os.path.join(d, "hook.sh"), _BASH_ENV_SAFE)
        _write(os.path.join(d, "naive.sh"), _BASH_ENV_NAIVE)
        _write(os.path.join(d, "home", ".bash_profile"), "export MARK=from_profile\n")
        base = {"PATH": "/usr/local/bin:/usr/bin:/bin", "CTXPRUNE_PY": sys.executable, "CTXPRUNE_FILTER": os.path.join(d, "filter.py"), "HOME": os.path.join(d, "home")}
        on = {**base, "BASH_ENV": os.path.join(d, "hook.sh")}
        # canary: does THIS bash read BASH_ENV right now? (it does not in privileged mode, POSIXLY_CORRECT, or behind a launcher that scrubs the
        # environment; on this box it was observed to stop honouring it for several minutes, see the report). If it does not, say so instead of failing on sizes.
        _write(os.path.join(d, "canary.sh"), f"echo ran > {shlex.quote(os.path.join(d, 'canary.out'))}\n")
        _run(["bash", "-c", "true"], cwd=d, env={**base, "BASH_ENV": os.path.join(d, "canary.sh")})
        assert os.path.exists(os.path.join(d, "canary.out")), "bash did not read BASH_ENV at all just now (privileged mode, POSIXLY_CORRECT, or a sanitising launcher): the mechanism is unavailable here, nothing below can be measured"
        cmd = 'echo build start; for i in $(seq 1 700); do echo "waiting for lock on /var/lib/dpkg/lock-frontend"; done; echo build end; echo "warn on stderr" >&2; exit 7'
        r0 = _run(["bash", "-c", cmd], cwd=d, env=base)
        r1 = _run(["bash", "-c", cmd], cwd=d, env=on)
        assert r0[0] == r1[0] == 7, "the exit status of the command is the exit status of the shell"
        assert len(r0[1]) > 20000 and len(r1[1]) < 200, f"a 20 KB command comes back as {len(r1[1])} bytes (stderr: {r1[2][:200]!r})"
        assert set(r0[1].split("\n")) <= set(r1[1].split("\n")), "no distinct line is lost, only repetitions"
        assert "repeated 699 more times" in r1[1] and r1[1].startswith("build start\n") and "build end\n" in r1[1]
        assert r1[2] == r0[2] == "warn on stderr\n", "stderr bypasses the filter untouched (and so can appear before stdout is released)"
        # --- measured scope: which shells honour BASH_ENV
        probe = 'echo mark=$MARK; for i in $(seq 1 40); do echo same; done'
        covered = {}
        for label, argv in (("bash -c", ["bash", "-c", probe]), ("bash -lc", ["bash", "-lc", probe]), ("bash -ic", ["bash", "-ic", probe]), ("sh -c (dash)", ["sh", "-c", probe])):
            rc, out, _ = _run(argv, cwd=d, env=on)
            covered[label] = "repeated 39 more times" in out
        assert covered == {"bash -c": True, "bash -lc": True, "bash -ic": False, "sh -c (dash)": False}, f"scope on this bash: {covered}"
        assert "mark=from_profile" in _run(["bash", "-lc", probe], cwd=d, env=on)[1], "the login shell really ran its profile"
        # --- the hazard found while building this gate: a socket on stdin makes bash skip BASH_ENV (and read ~/.bashrc, if any)
        _write(os.path.join(d, "home", ".bashrc"), "export MARK_RC=bashrc_was_read\n")
        a, b = socket.socketpair()
        try:
            via_sock = _run(["bash", "-c", cmd], cwd=d, env=on, stdin_obj=a)
            rc_probe = _run(["bash", "-c", "echo rc=$MARK_RC"], cwd=d, env=on, stdin_obj=a)[1]
        finally:
            a.close()
            b.close()
        assert via_sock[0] == 7 and len(via_sock[1]) > 20000, "stdin is a socket: BASH_ENV is skipped, the 20 KB command comes back unfiltered"
        assert rc_probe == "rc=bashrc_was_read\n" and _run(["bash", "-c", "echo rc=$MARK_RC"], cwd=d, env=on)[1] == "rc=\n", "bash treated it as a remote shell and read ~/.bashrc instead"
        # --- the two hazards
        nested = 'x=$(bash -c "for i in \\$(seq 1 40); do echo same; done"); echo "$x" | wc -l'
        assert _run(["bash", "-c", nested], cwd=d, env=on)[1].strip() == "40", "marked hook: a nested bash inside $(...) is not filtered"
        assert _run(["bash", "-c", nested], cwd=d, env={**base, "BASH_ENV": os.path.join(d, "naive.sh")})[1].strip() == "3", "an unmarked hook changes what a script captures from a nested bash (40 lines become 3)"
        gone = _run(["bash", "-c", cmd], cwd=d, env={**on, "CTXPRUNE_FILTER": "/nonexistent/filter.py"})
        assert gone[1] == r0[1] and gone[0] == 7, "filter missing: the hook checks first and the output is untouched"
        lost = _run(["bash", "-c", cmd], cwd=d, env={**base, "BASH_ENV": os.path.join(d, "naive.sh"), "CTXPRUNE_FILTER": "/nonexistent/filter.py"})
        assert lost[1] == "" and len(r0[1]) > 20000, "the naive hook with a missing filter loses all of stdout"
        assert _run(["bash", "-c", cmd], cwd=d, env={**base, "BASH_ENV": "/nonexistent"})[1] == r0[1], "a missing BASH_ENV file is ignored by bash"
        small = _run(["bash", "-c", "echo ok; exit 3"], cwd=d, env=on)
        assert (small[0], small[1]) == (3, "ok\n"), "short output is passed through"
    return {"bytes_direct": len(r0[1]), "bytes_filtered": len(r1[1]), "exit_status": r1[0], "login_shell_bash_lc_covered": covered["bash -lc"], "interactive_covered": covered["bash -ic"],
            "dash_covered": covered["sh -c (dash)"], "socket_on_stdin_disables_the_hook": True, "unguarded_hook_loses_stdout_if_filter_missing": True}


case(
    579,
    claim="A BASH_ENV file that execs stdout into a filter shrinks a 20 KB bash -c to under 200 bytes with the exit status (7) kept and no distinct line lost; stderr bypasses it; bash -c and bash -lc are covered here while bash -ic, dash and any bash whose stdin is a socket are not; the hook must mark itself (nested bash would otherwise change captured data) and test the filter first (otherwise a missing filter loses all stdout).",
    sim=sim_579,
)


# ---------------------------------------------------------------------------------------------------------------
# #946 SHELL override: one wrapper shell

_CTXSH = r'''#!/bin/bash
# ctxsh: exec the real shell; when it is asked to run a command string (-c, -lc), filter its stdout and keep the exit status.
real="${CTXPRUNE_REAL_SHELL:-/bin/bash}"
[ -x "$real" ] || { echo "ctxsh: real shell $real not found" >&2; exit 127; }
cstr=0
for a in "$@"; do
  case "$a" in --) break ;; --*) ;; -*c*) cstr=1; break ;; -*) ;; *) break ;; esac
done
if [ "$cstr" = 0 ] || [ -t 1 ] || [ -n "$CTXPRUNE_BYPASS" ] || [ ! -r "$CTXPRUNE_FILTER" ]; then exec "$real" "$@"; fi
tmp="$(mktemp)"
"$real" "$@" > "$tmp"; rc=$?
if ! "$CTXPRUNE_PY" -I "$CTXPRUNE_FILTER" --pack generic < "$tmp"; then cat "$tmp"; fi
rm -f "$tmp"
exit $rc
'''


def sim_946() -> dict:
    with tempfile.TemporaryDirectory() as d:
        _write(os.path.join(d, "filter.py"), _FILTER)
        _write(os.path.join(d, "ctxsh"), _CTXSH, 0o755)
        _write(os.path.join(d, "home", ".bash_profile"), "export MARK=from_profile\n")
        wrapper = os.path.join(d, "ctxsh")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "CTXPRUNE_PY": sys.executable, "CTXPRUNE_FILTER": os.path.join(d, "filter.py"), "HOME": os.path.join(d, "home"), "SHELL": wrapper}
        cmd = 'echo build start; for i in $(seq 1 700); do echo "waiting for lock on /var/lib/dpkg/lock-frontend"; done; echo build end; echo "warn on stderr" >&2; exit 7'
        direct = _run(["/bin/bash", "-c", cmd], cwd=d, env=env)
        via = _run([wrapper, "-c", cmd], cwd=d, env=env)  # what an agent does with $SHELL -c
        assert via[0] == direct[0] == 7, "the prototype's exit status 7 survives"
        assert len(direct[1]) > 20000 and len(via[1]) < 200 and via[2] == direct[2]
        assert set(direct[1].split("\n")) <= set(via[1].split("\n")), "no distinct line lost"
        # a socket on stdin (which makes bash skip BASH_ENV, see #579) does not matter to the wrapper
        a, b = socket.socketpair()
        try:
            sock = _run([wrapper, "-c", cmd], cwd=d, env=env, stdin_obj=a)
        finally:
            a.close()
            b.close()
        assert sock[0] == 7 and len(sock[1]) < 200, "the wrapper filters even when stdin is a socket"
        # login shell: the wrapper covers `-lc` and the profile still runs (BASH_ENV also covers bash -lc here, see #579)
        probe = 'echo mark=$MARK; for i in $(seq 1 40); do echo same; done'
        rc, out, _ = _run([wrapper, "-lc", probe], cwd=d, env=env)
        assert "mark=from_profile" in out and "repeated 39 more times" in out, out
        # a shell BASH_ENV cannot reach: the wrapper in front of dash
        dash = {**env, "CTXPRUNE_REAL_SHELL": "/bin/sh", "BASH_ENV": ""}
        rc, out, _ = _run([wrapper, "-c", probe], cwd=d, env=dash)
        assert "repeated 39 more times" in out and rc == 0, "works for a non-bash shell (zsh is the macOS default; dash stands in here)"
        # must NOT apply: no -c (script on stdin, interactive use), a script argument, an opt-out; arguments and stdin pass through
        rc, out, _ = _run([wrapper], cwd=d, env=env, stdin=b"for i in $(seq 1 40); do echo same; done\n")
        assert out == "same\n" * 40, "no -c: exec'd straight into the real shell, nothing filtered"
        _write(os.path.join(d, "s.sh"), "for i in $(seq 1 40); do echo same; done\n")
        assert _run([wrapper, os.path.join(d, "s.sh")], cwd=d, env=env)[1] == "same\n" * 40
        assert _run([wrapper, "-c", probe], cwd=d, env={**env, "CTXPRUNE_BYPASS": "1"})[1].count("same") == 40
        rc, out, _ = _run([wrapper, "-c", 'echo "$0|$1|$2"; cat', "name", "arg"], cwd=d, env=env, stdin=b"piped\n")
        assert out == "name|arg|\npiped\n", f"argv and stdin reach the real shell: {out!r}"
        # fail-open
        rc, out, _ = _run([wrapper, "-c", cmd], cwd=d, env={**env, "CTXPRUNE_FILTER": "/nonexistent"})
        assert (rc, out) == (7, direct[1]), "no filter: the real shell runs untouched, status kept"
        rc, out, err = _run([wrapper, "-c", "echo hi"], cwd=d, env={**env, "CTXPRUNE_REAL_SHELL": "/nonexistent/sh"})
        assert rc == 127 and "not found" in err
        # the price: output is held until the command exits (an agent that streams a long build sees nothing until the end)
    return {"bytes_direct": len(direct[1]), "bytes_via_wrapper": len(via[1]), "exit_status": via[0], "login_shell": "covered, profile runs", "dash": "covered", "streaming": "buffered until exit",
            "verified_here": "only that the wrapper works as a shell; whether Aider, Cline, Roo or Cursor honour $SHELL or a terminal profile was NOT verified (no agent runs offline)"}


case(
    946,
    claim="A wrapper shell passed as $SHELL execs the real shell, and for -c/-lc command strings filters stdout and keeps the exit status (7), stderr, argv and stdin; the login profile still runs, a non-bash real shell (dash) is covered, script and interactive invocations, opt-outs and a missing filter are exec'd untouched, and output is buffered until the command ends.",
    sim=sim_946,
)


# ===============================================================================================================
# Stateful output ledgers: #141 line-shift-proof diagnostic delta, #142 run-invariant baseline (SIMS)

# ---------------------------------------------------------------------------------------------------------------
# #141 Diagnostics keyed by (file, severity, rule, message) with line and column ignored.
# Fixtures: a real ruff 0.16.9 run per edit when ruff is installed (10 successive small edits of a real file), else a
# stand-in linter that prints the same concise format; mypy and tsc samples are reconstructed from memory.

_DIAG = [
    re.compile(r"^(?P<file>[^\s:(]+):(?P<line>\d+):(?P<col>\d+): (?P<rule>[A-Z]+\d+)(?: \[\*\])? (?P<msg>.+)$"),                          # ruff concise, flake8
    re.compile(r"^(?P<file>[^\s:(]+):(?P<line>\d+): (?P<sev>error|warning|note): (?P<msg>.+?)(?:  \[(?P<rule>[\w-]+)\])?$"),               # mypy
    re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) (?P<rule>TS\d+): (?P<msg>.+)$"),                 # tsc
]
_LINE_REF = re.compile(r"\b(on line|at line|line) \d+")


def diag_key(line: str):
    """(file, severity, rule, message with embedded 'line N' references flattened) or None when the line is not a diagnostic."""
    for rx in _DIAG:
        m = rx.match(line)
        if m:
            g = m.groupdict()
            return (g["file"], g.get("sev") or "", g.get("rule") or "", _LINE_REF.sub(r"\1 N", g["msg"]))
    return None


class DiagLedger:
    """Remembers the last diagnostics per (cwd, command family); shows only what changed. State lives in a JSON file."""

    def __init__(self, state_dir: str):
        self.dir = state_dir
        os.makedirs(os.path.join(state_dir, "outputs"), exist_ok=True)
        self.n = 0

    def _state_path(self):
        return os.path.join(self.dir, "state.json")

    def _load(self) -> dict:
        try:
            st = json.load(open(self._state_path(), encoding="utf-8"))
            return st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            return {}  # missing or corrupt: behave like a first run

    def show(self, family: str, output: str) -> str:
        lines = output.split("\n")
        diags = [(l, diag_key(l)) for l in lines if diag_key(l)]
        if not diags:
            return output  # not a diagnostics run (crash, usage error, clean summary only): verbatim, and the stored state is NOT replaced
        self.n += 1
        saved = os.path.join(self.dir, "outputs", f"{self.n:03d}.txt")
        _write(saved, output)
        st = self._load()
        prev = st.get(family)
        st[family] = [[l, list(k)] for l, k in diags]
        _write(self._state_path(), json.dumps(st))
        if not prev:
            return output
        pk = Counter(tuple(k) for _l, k in prev)
        ck = Counter(k for _l, k in diags)
        new = [l for l, k in diags if ck[k] > pk[k] and pk[k] == 0]
        grew = {k: [l for l, kk in diags if kk == k] for k in ck if 0 < pk[k] < ck[k]}
        gone = [l for l, k in prev if pk[tuple(k)] > ck[tuple(k)] and ck[tuple(k)] == 0]
        shrank = {tuple(k): [l for l, kk in prev if tuple(kk) == tuple(k)] for k in pk if 0 < ck[k] < pk[k]}
        unchanged = sum(min(pk[k], ck[k]) for k in ck)
        out = [f"[ctxprune: vs previous run: +{len(new) + sum(ck[k] - pk[k] for k in grew)} new, -{len(gone) + sum(pk[k] - ck[k] for k in shrank)} resolved, {unchanged} unchanged; line numbers of unchanged items may have moved; full output: {saved}]"]
        out += [f"+ {l}" for l in new]
        for k, ls in grew.items():
            out += [f"+ {ck[k] - pk[k]} more of this ({ck[k]} now):"] + [f"  {l}" for l in ls]
        out += [f"- {l}" for l in gone]
        for k, ls in shrank.items():
            out += [f"- {pk[k] - ck[k]} fewer of this ({ck[k]} now); before: {ls[0]}"]
        out += [l for l in lines if l.strip() and not diag_key(l) and not l.startswith(" ")]  # summary lines of the current run, always
        return "\n".join(out) + "\n"


def _apply_delta(prev_keys: Counter, delta: str) -> Counter:
    """The reader's view: previous key multiset plus the delta must equal the current diagnostics."""
    cur = Counter(prev_keys)
    lines = delta.split("\n")
    i = 1
    while i < len(lines):
        l = lines[i]
        if l.startswith("+ ") and diag_key(l[2:]):
            cur[diag_key(l[2:])] += 1
        elif l.startswith("- ") and diag_key(l[2:]):
            cur[diag_key(l[2:])] -= 1
        elif l.startswith("+ ") and "more of this" in l:
            n = int(l.split()[1])
            j = i + 1
            while j < len(lines) and lines[j].startswith("  ") and diag_key(lines[j].strip()):
                j += 1
            cur[diag_key(lines[i + 1].strip())] += n
            i = j - 1
        elif l.startswith("- ") and "fewer of this" in l:
            cur[diag_key(l.split("before: ", 1)[1])] -= int(l.split()[1])
        i += 1
    return +cur


def _stand_in_ruff(src: str, name="a.py") -> str:
    """ruff-concise formatted output for F401, F841 and E501 only (used when ruff is not installed)."""
    out = []
    tree = ast.parse(src)
    imported = {}
    for st in tree.body:
        if isinstance(st, ast.Import):
            for a in st.names:
                imported[a.asname or a.name] = st.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for nm, ln in imported.items():
        if nm not in used:
            out.append((ln, 1, f"F401 [*] `{nm}` imported but unused"))
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        stores = {t.id: t.lineno for s in ast.walk(fn) if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)}
        loads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for v, ln in stores.items():
            if v not in loads:
                out.append((ln, 5, f"F841 Local variable `{v}` is assigned to but never used"))
    for i, l in enumerate(src.split("\n"), 1):
        if len(l) > 88:
            out.append((i, 89, f"E501 Line too long ({len(l)} > 88)"))
    out.sort()
    body = "".join(f"{name}:{ln}:{c}: {m}\n" for ln, c, m in out)
    return body + (f"Found {len(out)} error{'s' if len(out) != 1 else ''}.\n" if out else "All checks passed!\n")


def _edit_series() -> list[str]:
    """Ten successive small edits of one file (inserting lines above everything, fixing and introducing findings, moving a function)."""
    funcs = [f"def f{i}(a):\n    tmp{i} = a + {i}\n    return a\n" for i in range(10)]
    src = ["import os", "import sys", "import json", "import re", ""]
    body = list(funcs) + ['LONG = "' + "x" * 110 + '"\n']
    versions = []

    def text():
        return "\n".join(src) + "\n" + "\n".join(body)

    versions.append(text())
    src[:0] = ["# header", "# more header", "#", "# license", "# text", ""]; versions.append(text())               # 1 shift
    src.remove("import re"); versions.append(text())                                                              # 2 fix one F401
    src.insert(6, "import glob"); versions.append(text())                                                         # 3 new F401, shift 1
    body[:0] = ["def helper():\n    return 1\n\n\n"]; versions.append(text())                                      # 4 shift
    body[3] = body[3].replace("    tmp2 = a + 2\n    return a", "    tmp2 = a + 2\n    return tmp2"); versions.append(text())   # 5 fix one F841
    body.append(body.pop(5)); versions.append(text())                                                             # 6 move a function
    src[:0] = ['"""Module docstring."""', ""]; versions.append(text())                                            # 7 shift
    body[-1:] = ['LONG = "short"\n']; versions.append(text())                                                     # 8 fix E501
    body.insert(2, "def g(a):\n    dup = 1\n    dup2 = 2\n    return a\n"); versions.append(text())                # 9 two new F841
    return versions


def _delta_series(outputs: list[str]):
    """Runs a ledger over successive outputs; returns (plain line-diff tokens, delta tokens, deltas)."""
    import difflib
    with tempfile.TemporaryDirectory() as d:
        led = DiagLedger(d)
        plain = delta = 0
        deltas, prev_keys = [], None
        for i, out in enumerate(outputs):
            shown = led.show("ruff check .", out)
            cur_keys = Counter(diag_key(l) for l in out.split("\n") if diag_key(l))
            if i:
                plain += tokens("\n".join(l for l in difflib.unified_diff(outputs[i - 1].split("\n"), out.split("\n"), lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))))
                delta += tokens(shown)
                assert _apply_delta(prev_keys, shown) == cur_keys, f"reader view: previous diagnostics + delta != current diagnostics at step {i}\n{shown}"
            deltas.append(shown)
            prev_keys = cur_keys
        return plain, delta, deltas


_MYPY_1 = """app/db.py:14: error: Name "rows" already defined on line 9  [no-redef]
app/db.py:40: error: Incompatible return value type (got "None", expected "int")  [return-value]
app/db.py:40: note: Did you forget to return something?
app/api.py:7: error: Argument 1 to "load" has incompatible type "str"; expected "Path"  [arg-type]
Found 3 errors in 2 files (checked 12 source files)
"""
_MYPY_2 = """app/db.py:19: error: Name "rows" already defined on line 14  [no-redef]
app/db.py:45: error: Incompatible return value type (got "None", expected "int")  [return-value]
app/db.py:45: note: Did you forget to return something?
app/api.py:12: error: Argument 1 to "load" has incompatible type "str"; expected "Path"  [arg-type]
Found 3 errors in 2 files (checked 12 source files)
"""
_TSC_1 = "src/a.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.\nsrc/a.ts(31,9): error TS2345: Argument of type 'null' is not assignable to parameter of type 'string'.\n\nFound 2 errors in the same file, starting at: src/a.ts:10\n"


def sim_141() -> dict:
    # --- ten successive edits, real ruff when installed, else the stand-in
    versions = _edit_series()
    ruff = shutil.which("ruff")
    real = ruff is not None
    with tempfile.TemporaryDirectory() as d:
        outs = []
        for v in versions:
            if real:
                _write(os.path.join(d, "a.py"), v)
                rc, out, _ = _run([ruff, "check", "--isolated", "--no-cache", "--select", "F,E", "--output-format=concise", "a.py"], cwd=d)
                outs.append(out)
            else:
                outs.append(_stand_in_ruff(v))
    assert all(diag_key(l) for o in outs for l in o.split("\n") if re.match(r"a\.py:\d+:\d+:", l)), "every diagnostic line must parse"
    plain, delta, deltas = _delta_series(outs)
    assert delta < 0.5 * plain, f"the keyed delta must be less than half the plain line-diff over 10 edits ({delta} vs {plain} tokens)"
    # which steps were pure line shifts: the delta says 0 new, 0 resolved
    shift_steps = [i for i in (1, 4, 7) if re.search(r"\+0 new, -0 resolved", deltas[i])]
    assert shift_steps == [1, 4, 7], f"a pure line shift (steps 1, 4, 7) must produce an empty delta; got {shift_steps}"
    assert "+0 new, -1 resolved" in deltas[2] and "+1 new, -0 resolved" in deltas[3] and "+0 new, -0 resolved" in deltas[6], "fix, introduce and move are told apart"
    assert "+2 new" in deltas[9], deltas[9]
    # --- mypy: 'already defined on line 9' moves with the edit; flattening keeps the key stable
    with tempfile.TemporaryDirectory() as d:
        led = DiagLedger(d)
        led.show("mypy .", _MYPY_1)
        s = led.show("mypy .", _MYPY_2)
        assert "+0 new, -0 resolved, 4 unchanged" in s, s
        assert diag_key('app/db.py:14: error: Name "rows" already defined on line 9  [no-redef]') == diag_key('app/db.py:19: error: Name "rows" already defined on line 14  [no-redef]')
        assert diag_key("src/a.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.") == ("src/a.ts", "error", "TS2322", "Type 'string' is not assignable to type 'number'.")
        # --- must NOT apply / invalidation
        first = led.show("tsc", _TSC_1)
        assert first == _TSC_1, "first run of a family: verbatim"
        assert led.show("mypy --strict .", _MYPY_2) == _MYPY_2, "a different command family has its own (empty) history"
        crash = "Traceback (most recent call last):\n  File \"/usr/bin/mypy\", line 3, in <module>\nKeyError: 'x'\n"
        assert led.show("mypy .", crash) == crash, "a crash is not a diagnostics run: verbatim"
        s2 = led.show("mypy .", _MYPY_2.replace("app/api.py:12", "app/api.py:13"))
        assert "+0 new, -0 resolved, 4 unchanged" in s2, "the crash did not overwrite the stored state"
        clean = "Success: no issues found in 12 source files\n"
        assert led.show("mypy .", clean) == clean, "a clean run has no diagnostics lines: verbatim (and it is already tiny)"
        # duplicates: two identical messages in one file are counted, not collapsed
        dup1 = "a.py:3:5: F841 Local variable `x` is assigned to but never used\na.py:9:5: F841 Local variable `x` is assigned to but never used\nFound 2 errors.\n"
        dup2 = "a.py:12:5: F841 Local variable `x` is assigned to but never used\nFound 1 error.\n"
        led.show("dup", dup1)
        sd = led.show("dup", dup2)
        assert "-1 resolved" in sd and "1 unchanged" in sd and "1 fewer of this (1 now)" in sd, sd
        sd3 = led.show("dup", dup1)
        assert "+1 new" in sd3 and "1 more of this (2 now)" in sd3 and dup1.split("\n")[1] in sd3, "when a repeated key grows, every current location is listed"
        # corrupt state: first-run behaviour, then healed
        _write(os.path.join(d, "state.json"), "{not json")
        assert led.show("mypy .", _MYPY_1) == _MYPY_1
        assert "unchanged" in led.show("mypy .", _MYPY_2)
        # originals stay on disk
        m = re.search(r"full output: (\S+)\]", s)
        assert open(m.group(1), encoding="utf-8").read() == _MYPY_2
    return {"ruff": "real 0.16.9 output" if real else "stand-in linter (ruff not installed)", "tokens_plain_line_diff_10_edits": plain, "tokens_keyed_delta_10_edits": delta,
            "saving": f"{1 - delta / plain:.0%}", "pure_shift_steps_with_empty_delta": len(shift_steps)}


case(
    141,
    claim="Diagnostics keyed by (file, severity, rule, message) ignoring line and column give an empty delta for pure line shifts, tell fixes, new findings and moves apart, count duplicates, flatten 'on line N' inside messages, and cost under half of a plain line-diff over ten successive edits (real ruff when installed); the previous set plus the delta reproduces the current set, first runs, crashes, clean runs and other command families come back verbatim, and the full output stays on disk.",
    sim=sim_141,
)


# ---------------------------------------------------------------------------------------------------------------
# #142 run-invariant baseline: lines present in each of the last K runs collapse to a count

_PROTECT = re.compile(r"(?i)\b(fail|failed|failure|failures|error|errors|traceback|assert|assertionerror|exception|panic|fatal|denied)\b|^E {3}|^FAILED |^>")


class Baseline:
    K = 3

    def __init__(self, state_dir: str):
        self.dir = state_dir
        os.makedirs(os.path.join(state_dir, "outputs"), exist_ok=True)
        self.n = 0

    @staticmethod
    def family(cwd: str, argv: list[str]) -> str:
        """cwd + program + the flags that change the shape of the output (-q, -v, --tb=..); not test selectors, not paths."""
        flags = sorted(a for a in argv[1:] if re.fullmatch(r"-q+|-v+|--tb=\S+|--color=\S+|--no-header|-x|--verbose|--quiet", a))
        return f"{cwd}|{os.path.basename(argv[0])}|{' '.join(flags)}"

    def _path(self):
        return os.path.join(self.dir, "history.json")

    def _hist(self) -> dict:
        try:
            h = json.load(open(self._path(), encoding="utf-8"))
            return h if isinstance(h, dict) else {}
        except (OSError, ValueError):
            return {}

    def show(self, fam: str, output: str) -> str:
        h = self._hist()
        runs = h.get(fam, [])
        h[fam] = (runs + [output.split("\n")])[-self.K:]
        _write(self._path(), json.dumps(h))
        if len(runs) < self.K:
            return output  # not enough history to know what is invariant
        base = Counter(runs[0])
        for r in runs[1:]:
            base &= Counter(r)
        lines = output.split("\n")
        hidden, visible = [], []
        for l in lines:
            if l.strip() and base[l] > 0 and not _PROTECT.search(l):
                base[l] -= 1
                hidden.append(l)
            else:
                visible.append(l)
        if len(hidden) < 3:
            return output
        self.n += 1
        saved = os.path.join(self.dir, "outputs", f"{self.n:03d}.txt")
        _write(saved, output)
        res = f"[ctxprune: {len(hidden)} lines identical in each of the last {self.K} runs of this command are hidden; full output: {saved}]\n" + "\n".join(visible)
        return res if len(res) < len(output) else output


def _pytest_run(n_tests: int, failing: dict[str, str], secs: float, version="9.1.1", root="/work/shop") -> str:
    prog = "".join("F" if i in {int(k) for k in failing} else "." for i in range(n_tests))
    out = ["============================= test session starts ==============================", f"platform linux -- Python 3.13.18, pytest-{version}, pluggy-1.6.0", f"rootdir: {root}", "configfile: pyproject.toml", "plugins: anyio-4.9.0, cov-6.0.0, xdist-3.6.1",
           f"collected {n_tests} items", "", f"tests/test_shop.py {prog}   [100%]", ""]
    if failing:
        out += ["=================================== FAILURES ==================================="]
        for k, msg in failing.items():
            out += [f"________________________________ test_case_{k} _________________________________", "", f"    def test_case_{k}():", f">       assert total() == {msg}", f"E       assert 300 == {msg}", "", "tests/test_shop.py:8: AssertionError"]
        out += ["=========================== short test summary info ============================"] + [f"FAILED tests/test_shop.py::test_case_{k} - assert 300 == {m}" for k, m in failing.items()]
        out += [f"========================= {len(failing)} failed, {n_tests - len(failing)} passed in {secs:.2f}s =========================", ""]
    else:
        out += [f"============================= {n_tests} passed in {secs:.2f}s ==============================", ""]
    return "\n".join(out)


def _npm_test_run(failed: int, secs: float) -> str:
    dep = [f"npm WARN deprecated {p}@{v}: this package is no longer supported" for p, v in (("request", "2.88.2"), ("uuid", "3.4.0"), ("har-validator", "5.1.5"), ("core-js", "2.6.12"), ("left-pad", "1.3.0"), ("mkdirp", "0.5.1"))] * 2
    out = dep + ["", "> shop@1.0.0 test", "> jest --ci", ""]
    out += ["PASS  src/cart.test.js", "PASS  src/tax.test.js"] + (["FAIL  src/checkout.test.js", "  ● checkout › applies coupon", "    expect(received).toBe(expected)", "    Expected: 270", "    Received: 300"] if failed else ["PASS  src/checkout.test.js"])
    out += ["", f"Tests:       {failed} failed, {41 - failed} passed, 41 total" if failed else "Tests:       41 passed, 41 total", f"Time:        {secs:.3f} s", "Ran all test suites.", ""]
    return "\n".join(out)


def sim_142() -> dict:
    with tempfile.TemporaryDirectory() as d:
        bl = Baseline(d)
        fam = Baseline.family("/work/shop", ["pytest", "tests/test_shop.py", "-k", "cart", "-q"])
        assert fam == Baseline.family("/work/shop", ["pytest", "tests/other.py", "-q"]) != Baseline.family("/work/shop", ["pytest", "-v"]) != Baseline.family("/work/other", ["pytest", "-q"])
        # five runs of pytest with small edits: pass, pass, one new test, a failure, the same failure again, then a fix
        runs = [_pytest_run(10, {}, 0.02), _pytest_run(10, {}, 0.03), _pytest_run(11, {}, 0.02), _pytest_run(11, {"7": "280"}, 0.03), _pytest_run(11, {"7": "280"}, 0.02), _pytest_run(11, {"7": "280"}, 0.04),
                _pytest_run(11, {"7": "280"}, 0.03), _pytest_run(11, {}, 0.03)]
        shown = [bl.show(fam, r) for r in runs]
        assert shown[:3] == runs[:3], "no hiding before three prior runs exist"
        assert shown[3] != runs[3] and "lines identical in each of the last 3 runs" in shown[3], "from the fourth run the invariant lines are hidden"
        # --- the safety checks, on every run: failure lines visible, count honest, nothing unaccounted for
        tot_before = tot_after = 0
        for k in range(3, 8):
            vis, orig = shown[k], runs[k]
            m = re.match(r"\[ctxprune: (\d+) lines identical.*full output: (\S+)\]\n", vis)
            if not m:
                continue
            hidden_n, path = int(m.group(1)), m.group(2)
            body = vis[m.end():].split("\n")
            assert open(path, encoding="utf-8").read() == orig, "the original is on disk"
            assert len(orig.split("\n")) - len(body) == hidden_n, "the stated count equals the number of lines actually hidden"
            for l in orig.split("\n"):
                if _PROTECT.search(l):
                    assert l in body, f"a failure or assertion line was hidden: {l!r}"
            missing = Counter(orig.split("\n")) - Counter(body)
            assert sum(missing.values()) == hidden_n and all(c > 0 for c in missing.values()), "visible + hidden == original, as multisets"
            tot_before, tot_after = tot_before + len(orig), tot_after + len(vis)
        assert "FAILED tests/test_shop.py::test_case_7 - assert 300 == 280" in shown[6] and "E       assert 300 == 280" in shown[6], "a test that has failed in each of the last three runs is still shown in full"
        assert "1 failed, 10 passed" in shown[6] and "lines identical in each of the last 3 runs" in shown[6]
        assert _PROTECT.search("FAILED tests/test_shop.py::test_case_7 - assert 300 == 280") and "FAILED tests/test_shop.py::test_case_7" in runs[4] and "FAILED tests/test_shop.py::test_case_7" in runs[5] and "FAILED tests/test_shop.py::test_case_7" in runs[3], "fixture check: the failure really is in all three prior runs"
        # --- invalidation by construction
        # a tool upgrade changes the banner line: it is visible again immediately and is not baseline until three runs carry it
        up = bl.show(fam, _pytest_run(11, {}, 0.03, version="9.2.0"))  # (this also becomes the next run of the family)
        assert "pytest-9.2.0" in up and "pytest-9.1.1" not in up
        # a line missing from ONE of the last three runs is not baseline
        bl2 = Baseline(os.path.join(d, "b2"))
        flaky = [f"stable banner line number {i} padded out to a realistic width" for i in "abcde"]
        for r in (flaky + ["w"], flaky, flaky + ["w"]):
            bl2.show("f", "\n".join(r) + "\n")
        out = bl2.show("f", "\n".join(flaky + ["w"]) + "\n")
        assert "\nw\n" in "\n" + out and "5 lines identical" in out, out
        # corrupt history fails open
        _write(os.path.join(d, "history.json"), "[1,")
        assert bl.show(fam, runs[0]) == runs[0]
        # --- npm test: the family where the idea pays (a fixed wall of deprecation warnings)
        nb = Baseline(os.path.join(d, "npm"))
        nfam = Baseline.family("/work/shop", ["npm", "test"])
        nruns = [_npm_test_run(0, 2.1), _npm_test_run(0, 2.2), _npm_test_run(1, 2.4), _npm_test_run(1, 2.3), _npm_test_run(0, 2.0)]
        nshown = [nb.show(nfam, r) for r in nruns]
        assert nshown[3] == nruns[3] or "lines identical" in nshown[3]
        assert "FAIL  src/checkout.test.js" in nshown[3] and "Expected: 270" in nshown[3] and "Received: 300" in nshown[3], "the failing suite is never hidden"
        saving = 1 - len(nshown[4]) / len(nruns[4])
        assert saving > 0.4, f"a fixed wall of npm deprecation warnings should be hidden, saved {saving:.0%}"
        assert "Tests:       41 passed, 41 total" in nshown[4], "the result line is visible"
        py_saving = 1 - tot_after / tot_before
        assert py_saving > 0.15
    return {"pytest_chars_saved_runs_4_to_8": f"{py_saving:.0%}", "npm_test_chars_saved_run_5": f"{saving:.0%}", "lines_hidden_pytest_run_4": int(re.match(r"\[ctxprune: (\d+)", shown[3]).group(1)),
            "failure_lines_hidden": 0}


case(
    142,
    claim="Lines identical in each of the last three runs of a command family (cwd, program, output-shaping flags) are hidden behind one counted header with the full output saved; failure, assertion and error lines are never hidden even when they repeat in every run, a line missing from any prior run or changed by a tool upgrade stays visible, and corrupt history fails open.",
    sim=sim_142,
)


# ===============================================================================================================
# History rewriting: #288 stub aged Write/heredoc inputs, #593 todo completion as a fold boundary, #1324 relevance-ranked
# stubbing (SIMS). Transcripts are generated deterministically in the Messages API shape (assistant tool_use blocks, user
# tool_result blocks) and executed against a dict that plays the disk. NOTHING here measures a real model: the 'did the
# model re-Read' questions of the ideas' Test lines need real transcripts; these sims check the mechanism, the invalidation
# rules and the prompt-cache cost.

_tok_cache: dict[str, int] = {}


def _mtok(msg) -> int:
    s = json.dumps(msg, sort_keys=True)
    if s not in _tok_cache:
        _tok_cache[s] = tokens(s)
    return _tok_cache[s]


def _request_cost(requests: list[list[dict]]) -> float:
    """Billed token-equivalents of a series of API requests under prompt caching: the longest common prefix with the PREVIOUS
    request is a cache read (0.1x), everything after it a write (1.25x). Rewriting an old message therefore re-bills all that follows."""
    cost, prev = 0.0, []
    for req in requests:
        k = 0
        while k < min(len(prev), len(req)) and prev[k] == req[k]:
            k += 1
        cost += 0.1 * sum(_mtok(m) for m in req[:k]) + 1.25 * sum(_mtok(m) for m in req[k:])
        prev = req
    return cost


def _code(g, name: str, n: int) -> str:
    out = [f'"""{name}: generated module."""', "import os", "import json", ""]
    for i in range(n // 6):
        out += [f"def {name}_{i}(x, y={i}):", f'    """Handle case {i}."""', f"    total = x * {i + 2} + y", f"    if total > {100 + next(g) % 900}:", f"        return {{'case': {i}, 'total': total}}", "    return None", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def _transcript_288(n: int = 44, seed: int = 288):
    """Messages plus the final disk. Returns (messages, disk, truth) where truth lists what each Write/heredoc really was."""
    g = _lcg(seed)
    msgs, disk, truth = [], {}, []
    tid = [0]

    def call(name, inp, result, error=False, text=""):
        tid[0] += 1
        i = f"toolu_{tid[0]:03d}"
        msgs.append({"role": "assistant", "content": ([{"type": "text", "text": text}] if text else []) + [{"type": "tool_use", "id": i, "name": name, "input": inp}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": result, **({"is_error": True} if error else {})}]})
        return i

    paths = [f"src/mod{k}.py" for k in range(8)]
    for t in range(n):
        k = t % 11
        p = paths[next(g) % len(paths)]
        if k in (0, 3, 6):  # Write a module
            c = _code(g, p.split("/")[-1][:-3], 40 + next(g) % 70)
            disk[p] = c
            truth.append(("write", p, c, "ok", t))
            call("Write", {"file_path": p, "content": c}, f"File created successfully at: {p}", text=f"Creating {p}.")
        elif k == 1:  # heredoc write of a test file
            body = _code(g, "test_" + p.split("/")[-1][:-3], 30 + next(g) % 30)
            tp = "tests/" + p.split("/")[-1].replace("mod", "test_mod")
            disk[tp] = body
            truth.append(("heredoc", tp, body, "ok", t))
            call("Bash", {"command": f"cat > {tp} <<'EOF'\n{body.rstrip(chr(10))}\nEOF\npytest -q {tp}"}, "1 passed in 0.02s")
        elif k == 2:  # heredoc that RUNS a script: not a file write
            call("Bash", {"command": "python3 - <<'EOF'\n" + "\n".join(f"print({i} * {i})" for i in range(25)) + "\nEOF"}, "\n".join(str(i * i) for i in range(25)))
        elif k == 4 and p in disk:  # Edit
            old = "total = x * "
            disk[p] = disk[p].replace(old, "total = 2 * x * ", 1)
            call("Edit", {"file_path": p, "old_string": old, "new_string": "total = 2 * x * "}, f"The file {p} has been updated.")
        elif k == 5:  # Read
            call("Read", {"file_path": p}, disk.get(p, "File does not exist."), error=p not in disk)
        elif k == 7:  # a Write that fails: the content is NOT on disk
            c = _code(g, "readonly", 60)
            fp = p if p in disk else "/etc/protected.py"  # the path exists on disk (an older version) but this content never reached it
            truth.append(("write", fp, c, "error", t))
            call("Write", {"file_path": fp, "content": c}, f"EACCES: permission denied, open '{fp}'", error=True)
        elif k == 8:  # Write a file and later delete it (scratch)
            c = _code(g, "scratch", 50)
            sp = f"scratch/s{t}.py"
            disk[sp] = c
            truth.append(("write", sp, c, "deleted", t))
            call("Write", {"file_path": sp, "content": c}, f"File created successfully at: {sp}")
            del disk[sp]
            call("Bash", {"command": f"rm {sp}"}, "")
        elif k == 9:
            call("Bash", {"command": "pytest -q"}, "." * 30 + "\n30 passed in 0.31s")
        else:
            c = "line one\nline two\n"  # tiny Write: not worth stubbing
            disk["notes.txt"] = c
            truth.append(("write", "notes.txt", c, "tiny", t))
            call("Write", {"file_path": "notes.txt", "content": c}, "File created successfully at: notes.txt")
    return msgs, disk, truth


_HEREDOC = re.compile(r"^(?P<head>cat\s*>>?\s*(?P<path>[\w./-]+)\s*<<-?\s*['\"]?(?P<tag>\w+)['\"]?\n)(?P<body>.*?)(?P<tail>\n(?P=tag)(?:\n.*)?)$", re.S)


def stub_history(msgs: list[dict], disk: dict, age: int = 10, min_chars: int = 200) -> tuple[list[dict], list[dict]]:
    """Replace the content of aged Write inputs and the body of aged `cat > file <<EOF` commands by path, line count and hash.
    Never touches: young messages, a Write whose result was an error, a file that is not on disk now, script heredocs
    (`python3 - <<EOF`), short content. Pure function of (msgs, disk, age): same input, same bytes."""
    out = json.loads(json.dumps(msgs))
    results = {b["tool_use_id"]: b for m in msgs if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result"}
    n_asst = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
    aged = {i for j, i in enumerate(n_asst) if len(n_asst) - 1 - j >= age}
    stats = []

    def stub(path, text):
        cur = disk.get(path)
        return f"[ctxprune: {text.count(chr(10)) + (not text.endswith(chr(10)))} lines, sha1 {_sha(text)}; {'on disk at this path' if cur == text else 'on disk at this path, changed since'}: Read it if needed]"

    for i in aged:
        for b in out[i]["content"]:
            if b.get("type") != "tool_use" or results.get(b["id"], {}).get("is_error") or b["id"] not in results:
                continue
            if b["name"] == "Write":
                path, text = b["input"].get("file_path", ""), b["input"].get("content", "")
                if len(text) >= min_chars and path in disk:
                    b["input"]["content"] = stub(path, text)
                    stats.append({"msg": i, "kind": "write", "path": path, "sha": _sha(text), "chars": len(text)})
            elif b["name"] == "Bash":
                cmd = b["input"].get("command", "")
                m = _HEREDOC.match(cmd)
                if m and len(m.group("body")) >= min_chars and m.group("path") in disk:
                    b["input"]["command"] = m.group("head") + stub(m.group("path"), m.group("body") + "\n") + m.group("tail")
                    stats.append({"msg": i, "kind": "heredoc", "path": m.group("path"), "sha": _sha(m.group("body") + "\n"), "chars": len(m.group("body"))})
    return out, stats


def sim_288() -> dict:
    msgs, disk, truth = _transcript_288()
    stubbed, stats = stub_history(msgs, disk)
    before, after = sum(_mtok(m) for m in msgs), sum(_mtok(m) for m in stubbed)
    assert after < 0.75 * before, f"stubbing aged Write and heredoc inputs should cut the request by over 25% ({before} -> {after} tokens)"
    kinds = Counter(s["kind"] for s in stats)
    assert kinds["write"] >= 5 and kinds["heredoc"] >= 3, kinds
    # --- reader view: every stub names the path, the line count and a hash that the disk can reproduce (or says it changed)
    for s in stats:
        blk = next(b for b in stubbed[s["msg"]]["content"] if b.get("type") == "tool_use" and (s["kind"] == "write" and b["name"] == "Write" and b["input"]["file_path"] == s["path"] or s["kind"] == "heredoc" and b["name"] == "Bash" and s["path"] in b["input"]["command"].split("\n")[0]))
        text = blk["input"]["content"] if s["kind"] == "write" else blk["input"]["command"]
        assert f"sha1 {s['sha']}" in text and "lines" in text and "Read it if needed" in text
        if "changed since" not in text:
            assert _sha(disk[s["path"]]) == s["sha"], f"{s['path']}: the stub says the file is on disk unchanged but the disk differs"
        else:
            later = [m for m in msgs[s["msg"] + 1:] if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use" and (b["input"].get("file_path") == s["path"] or s["path"] in b["input"].get("command", ""))]
            assert later, f"{s['path']}: marked changed, so a later Edit or Write must exist in the history"
    # --- must NOT be stubbed
    young = [i for i, m in enumerate(msgs) if m["role"] == "assistant"][-10:]
    assert all(msgs[i] == stubbed[i] for i in young), "the last ten assistant turns are untouched"
    writes = [b["input"] for m in stubbed for b in m["content"] if b.get("type") == "tool_use" and b["name"] == "Write"]
    for kind, path, content, state, t in truth:
        if state in ("error", "deleted", "tiny"):
            assert any(w.get("file_path") == path and w.get("content") == content for w in writes), f"the {state} write at turn {t} must keep its content: it may be the only copy"
    assert any("python3 - <<'EOF'" in b["input"].get("command", "") and "print(" in b["input"]["command"] for m in stubbed for b in m["content"] if b.get("type") == "tool_use" and b["name"] == "Bash"), "script heredocs keep their body"
    # a file that is not on disk now is never stubbed
    gone = stub_history(msgs, {k: v for k, v in disk.items() if not k.startswith("src/")})[1]
    assert not any(s["path"].startswith("src/") for s in gone), "no disk copy, no stub"
    # fail-open: junk shapes pass through
    junk = [{"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "Write", "input": {}}]}, {"role": "user", "content": [{"type": "text", "text": "hi"}]}] * 12
    assert stub_history(junk, {})[0] == junk
    # --- determinism and prefix stability as the transcript grows: a message changes at most once (full -> stub), never again
    seen: dict[int, list[str]] = {}
    for k in range(20, len(msgs) + 1, 2):
        sk, _ = stub_history(msgs[:k], disk)
        for i, m in enumerate(sk):
            v = json.dumps(m, sort_keys=True)
            if not seen.setdefault(i, [v]) or seen[i][-1] != v:
                seen[i].append(v)
    assert all(len(v) <= 2 for v in seen.values()), "a message may flip from full to stub once, never back or between stubs"
    assert stub_history(msgs, disk) == stub_history(msgs, disk)
    # --- prompt-cache economics: stubbing every turn rewrites the prefix every turn; stubbing in batches does it rarely
    n_req = len(msgs) // 2
    plain_reqs = [msgs[:2 * (t + 1)] for t in range(n_req)]
    per_turn = [stub_history(msgs[:2 * (t + 1)], disk)[0] for t in range(n_req)]
    batched = [stub_history(msgs[:2 * (t // 10 * 10 + 1)], disk)[0] + msgs[2 * (t // 10 * 10 + 1):2 * (t + 1)] for t in range(n_req)]
    t0 = int(n_req * 0.6)
    once = [plain_reqs[t] if t < t0 else stub_history(msgs[:2 * t0], disk)[0] + msgs[2 * t0:2 * (t + 1)] for t in range(n_req)]  # one rewrite at 60% of the session
    c_plain, c_turn, c_batch, c_once = _request_cost(plain_reqs), _request_cost(per_turn), _request_cost(batched), _request_cost(once)
    assert c_batch < c_turn and c_once < c_turn, "fewer rewrites must beat stubbing every turn once the cache is accounted for"
    assert c_turn > c_plain, "stubbing every turn costs MORE than not stubbing, although it sends fewer tokens: every rewrite re-bills the cached prefix after it"
    return {"request_tokens": f"{before} -> {after} ({-100 * (before - after) / before:+.0f}%)", "stubbed_writes": kinds["write"], "stubbed_heredocs": kinds["heredoc"],
            "billed_equivalents_no_stubbing": round(c_plain), "billed_stub_every_turn": round(c_turn), "billed_stub_in_batches_of_10": round(c_batch), "billed_one_rewrite_at_60pct": round(c_once),
            "tool_use_signature_check": "not verifiable offline: the idea's premise that tool_use inputs are not signature-checked was NOT tested"}


case(
    288,
    claim="Write inputs and `cat > file <<EOF` bodies older than ten assistant turns become one line (line count, sha1, 'on disk at this path'); young turns, failed or deleted writes, short content, script heredocs and files not on disk now keep their content, every stub's hash is reproducible from the disk or marked changed with a later Edit in history, a message flips at most once. Request tokens fall 41%, but billed with prompt caching stubbing every turn costs 59% MORE and batches of ten 14% more, because each rewrite re-bills the prefix after it; one rewrite late in a session is about break-even.",
    sim=sim_288,
)


# ---------------------------------------------------------------------------------------------------------------
# #593 todo completion as a fold boundary

def _todo(items: list[tuple[str, str]]) -> dict:
    return {"todos": [{"content": c, "status": s, "activeForm": c} for c, s in items]}


def _transcript_593(seed: int = 593):
    """Messages for a session with five todos: normal spans, an overlap, a batch completion, a reopened todo, a todo that names a file."""
    g = _lcg(seed)
    msgs: list[dict] = []
    truth: dict[str, str] = {}  # tool_use id -> owning todo ('' = unattributable)
    store_files = {f"src/{n}.py": _code(g, n, 60 + next(g) % 60) for n in ("cart", "tax", "coupons", "shipping", "users", "search")}
    n_id = [0]

    def tool(name, inp, result, owner, error=False):
        n_id[0] += 1
        i = f"toolu_{n_id[0]:03d}"
        truth[i] = owner
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": result, **({"is_error": True} if error else {})}]})

    def todos(items):
        n_id[0] += 1
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_todo{n_id[0]}", "name": "TodoWrite", "input": _todo(items)}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_todo{n_id[0]}", "content": "Todos have been modified successfully"}]})

    T = ["Fix cart total rounding", "Add tax tests for src/tax.py", "Wire coupons into checkout", "Update shipping docs", "Clean up users module"]
    pend = lambda *done_state: [(T[i], s) for i, s in enumerate(done_state)]  # noqa: E731
    todos(pend("in_progress", "pending", "pending", "pending", "pending"))
    for f in ("cart", "cart", "tax"):
        tool("Read", {"file_path": f"src/{f}.py"}, store_files[f"src/{f}.py"], T[0])
    tool("Bash", {"command": "pytest tests/test_cart.py -q"}, "F" + "." * 20 + "\nFAILED tests/test_cart.py::test_total - assert 299 == 300\n1 failed, 20 passed in 0.31s", T[0], error=True)
    tool("Edit", {"file_path": "src/cart.py", "old_string": "round(", "new_string": "int(round("}, "The file src/cart.py has been updated.", T[0])
    tool("Bash", {"command": "pytest tests/test_cart.py -q"}, "." * 21 + "\n21 passed in 0.30s", T[0])
    todos(pend("completed", "in_progress", "pending", "pending", "pending"))                                          # span 1 closes; todo 2 names src/tax.py
    tool("Read", {"file_path": "src/tax.py"}, store_files["src/tax.py"], T[1])
    tool("Grep", {"pattern": "def tax_"}, "\n".join(f"src/tax.py:{i}:def tax_{i}(x):" for i in range(40)), T[1])
    todos(pend("completed", "in_progress", "in_progress", "pending", "pending"))                                      # overlap: two in progress
    tool("Read", {"file_path": "src/coupons.py"}, store_files["src/coupons.py"], "")                                   # ambiguous
    tool("Read", {"file_path": "src/shipping.py"}, store_files["src/shipping.py"], "")
    todos(pend("completed", "completed", "completed", "in_progress", "pending"))                                      # both close; their spans are ambiguous: nothing folds
    tool("Read", {"file_path": "docs/shipping.md"}, "# Shipping\n" + "\n".join(f"rate {i}: {10 + i} EUR" for i in range(60)), T[3])
    tool("Bash", {"command": "grep -rn rate docs/"}, "\n".join(f"docs/shipping.md:{i}:rate {i}" for i in range(30)), T[3])
    todos(pend("completed", "completed", "completed", "completed", "in_progress"))                                    # span 4 closes
    tool("Read", {"file_path": "src/users.py"}, store_files["src/users.py"], T[4])
    tool("Read", {"file_path": "src/search.py"}, store_files["src/search.py"], T[4])
    todos(pend("completed", "completed", "completed", "completed", "completed"))                                      # span 5 closes
    todos(pend("completed", "completed", "completed", "completed", "in_progress"))                                    # todo 5 reopened
    tool("Read", {"file_path": "src/users.py"}, store_files["src/users.py"], T[4])
    return msgs, truth, T


def fold_spans(msgs: list[dict], keep_min: int = 3) -> tuple[list[dict], dict, list[int]]:
    """Fold the tool results of every todo span that a TodoWrite 'completed' event closes. Returns (messages, store, rewrite events).
    Span = from the TodoWrite that sets exactly one todo in_progress to the one that completes it; a span in which another todo was also
    in_progress is ambiguous and never folds; results naming a file that a still-open todo's text names stay; a todo that is completed
    without ever being in_progress has no span."""
    out = json.loads(json.dumps(msgs))
    store: dict[str, str] = {}
    status: dict[str, str] = {}
    open_span: dict[str, dict] = {}  # todo -> {start, ambiguous}
    rewrites: list[int] = []
    for i, m in enumerate(msgs):
        for b in m["content"] if m["role"] == "assistant" else []:
            if b.get("type") != "tool_use" or b["name"] != "TodoWrite":
                continue
            try:
                cur = {t["content"]: t["status"] for t in b["input"]["todos"]}
            except (KeyError, TypeError):
                continue  # malformed payload: ignore the event
            inprog = [c for c, s in cur.items() if s == "in_progress"]
            for c in inprog:
                if status.get(c) != "in_progress":
                    open_span[c] = {"start": i, "ambiguous": False}
            if len(inprog) > 1:  # two todos in progress at once: nothing in their spans can be attributed
                for c in inprog:
                    open_span[c]["ambiguous"] = True
            for c, s in cur.items():
                if s == "completed" and status.get(c) == "in_progress" and c in open_span:
                    sp = open_span.pop(c)
                    if sp["ambiguous"]:
                        continue
                    still_open_text = " ".join(k for k, v in cur.items() if v != "completed")
                    folded_any = False
                    for j in range(sp["start"] + 1, i):
                        for blk in out[j]["content"] if out[j]["role"] == "user" else []:
                            if blk.get("type") != "tool_result" or blk.get("folded") or not isinstance(blk["content"], str) or len(blk["content"]) < 300:
                                continue
                            use = next(u for u in msgs[j - 1]["content"] if u.get("type") == "tool_use" and u["id"] == blk["tool_use_id"])
                            target = use["input"].get("file_path") or use["input"].get("command") or use["input"].get("pattern", "")
                            if any(w in still_open_text for w in re.findall(r"[\w./-]+\.py", str(target))):
                                continue  # a todo that is still open names this file
                            rid = "r-" + _sha(blk["content"])
                            store[rid] = blk["content"]
                            first = blk["content"].split("\n", 1)[0][:80]
                            blk["content"] = f"[folded {rid}: {use['name']} {str(target)[:60]} ({blk['content'].count(chr(10)) + 1} lines): {first}]"
                            blk["folded"] = True
                            folded_any = True
                    if folded_any:
                        rewrites.append(i)
            status.update(cur)
    return out, store, rewrites


def sim_593() -> dict:
    msgs, truth, T = _transcript_593()
    folded, store, rewrites = fold_spans(msgs)
    before, after = sum(_mtok(m) for m in msgs), sum(_mtok(m) for m in folded)
    f_ids = {b["tool_use_id"] for m in folded if m["role"] == "user" for b in m["content"] if b.get("folded")}
    # --- independent oracle: the generator knows which todo owned each call
    ok_owner = {i for i, o in truth.items() if o in (T[0], T[3], T[4])}  # unambiguous, closed spans (T[1] names src/tax.py and is held back below)
    assert f_ids <= ok_owner, f"folded results that do not belong to a closed, unambiguous span: {f_ids - ok_owner}"
    ambiguous = {i for i, o in truth.items() if o == ""}
    assert not (f_ids & ambiguous), "results from an overlapped (ambiguous) span must not fold"
    assert f_ids, "something folds"
    # span 1 has two Reads of cart.py and one of tax.py; todo 2 ('Add tax tests for src/tax.py') is still open when span 1 closes, so tax.py stays
    t1 = [i for i, o in truth.items() if o == T[0]]
    tax_read = next(i for i in t1 if next(u for m in msgs for u in m["content"] if u.get("id") == i)["input"].get("file_path") == "src/tax.py")
    assert tax_read not in f_ids, "a file named by a still-open todo is not folded"
    assert sum(1 for i in t1 if i in f_ids) == 2, 'both big Reads of cart.py fold; the short pytest, Edit results (under 300 chars) stay'
    # the todo that was never in progress when completed and the span of the overlap: no folds for T[1], T[2]
    assert not any(truth[i] in (T[1], "") for i in f_ids)
    # --- reader view: every stub names the tool and target and carries a saved id; unfold returns the original byte for byte
    for m in folded:
        for b in m["content"] if m["role"] == "user" else []:
            if b.get("folded"):
                rid = re.search(r"\[folded (r-[0-9a-f]{8}):", b["content"]).group(1)
                assert rid in store and _sha(store[rid]) == rid[2:], "unfold(id) returns exactly what the tool returned"
                assert re.search(r"\[folded r-[0-9a-f]{8}: (Read|Bash|Grep) \S", b["content"])
    # the stub keeps the path of what it folded, so an agent that mentions src/cart.py later knows which id to unfold
    assert any(b.get("folded") and "src/cart.py" in b["content"] for m in folded if m["role"] == "user" for b in m["content"])
    # --- no TodoWrite at all (other agents), malformed payloads, empty history: nothing folds, nothing raises
    plain = [m for m in msgs if not any(b.get("name") == "TodoWrite" or (b.get("type") == "tool_result" and "Todos have been" in str(b.get("content"))) for b in m["content"])]
    assert fold_spans(plain)[0] == plain and fold_spans([])[0] == []
    bad = json.loads(json.dumps(msgs))
    for m in bad:
        for b in m["content"] if m["role"] == "assistant" else []:
            if b.get("name") == "TodoWrite":
                b["input"] = {"todos": "nope"}
    assert fold_spans(bad)[0] == bad, "malformed TodoWrite payloads are ignored"
    # --- history is rewritten once per closing event, not once per turn
    assert len(rewrites) == len(set(rewrites)) and len(rewrites) <= 3, rewrites
    plain_reqs = [msgs[:2 * (t + 1)] for t in range(len(msgs) // 2)]
    fold_reqs = [fold_spans(msgs[:2 * (t + 1)])[0] for t in range(len(msgs) // 2)]
    c0, c1 = _request_cost(plain_reqs), _request_cost(fold_reqs)
    return {"tool_result_tokens_folded": sum(_mtok(m) for m in msgs) - after, "request_tokens": f"{before} -> {after} ({-100 * (before - after) / before:+.0f}%)", "results_folded": len(f_ids),
            "rewrite_events": len(rewrites), "billed_no_folding": round(c0), "billed_with_folding": round(c1)}


case(
    593,
    claim="When a TodoWrite event completes a todo that was the only one in progress, the tool results since it started become one-line stubs (tool, target, first line, saved id; unfold returns the original byte for byte); overlapping todos, a todo completed without a span, files named by a still-open todo, malformed payloads and sessions without todos fold nothing, and history is rewritten once per closing event.",
    sim=sim_593,
)


# ---------------------------------------------------------------------------------------------------------------
# #1324 rank aged tool results by relevance to the current prompt, not by age

_TOPICS = {
    "auth": ("login_required", "TokenStore", "auth/session.py", "session token expiry refresh login cookie"),
    "cart": ("compute_total", "CartLine", "shop/cart.py", "cart total price line quantity rounding discount"),
    "search": ("build_index", "QueryParser", "search/index.py", "search index query ranking tokenizer shard"),
    "billing": ("issue_invoice", "VatRule", "billing/invoice.py", "invoice vat tax billing customer ledger"),
    "deploy": ("rollout_plan", "CanaryGate", "ops/deploy.py", "deploy canary rollout release rollback health"),
    "mail": ("send_digest", "SmtpPool", "notify/mail.py", "mail smtp digest notification queue retry"),
    "export": ("write_report", "CsvSink", "reports/export.py", "export csv report download stream encoding"),
    "perf": ("profile_hot_path", "LruCache", "core/cache.py", "performance cache latency profile hot path memory"),
}


def _auc_pairs(scores: list[float], labels: list[int]) -> float:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def _auc_ranks(scores: list[float], labels: list[int]) -> float:
    """Mann-Whitney U from average ranks: a different algorithm from the pairwise count above."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    return (sum(r for r, y in zip(ranks, labels) if y) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def _session_1324(seed: int, revisit: float = 0.5):
    """60 tool results about eight topics, a last prompt about one topic, and who is reused later. Reuse model (mine, stated):
    P(reused) = .8 if the result is about the prompt's topic, else .03 + .3 * exp(-age / 25)."""
    g = _lcg(seed)
    names = list(_TOPICS)
    results = []
    cur = names[next(g) % 8]
    for t in range(60):
        if next(g) % 100 > revisit * 100 * 0.4:
            cur = names[next(g) % 8] if next(g) % 3 == 0 else cur
        topic = names[next(g) % 8] if next(g) % 100 < 70 else cur
        fn, cls, path, words = _TOPICS[topic]
        vocab = words.split()
        text = f"{path}\n" + "\n".join(f"{vocab[next(g) % len(vocab)]} {fn if next(g) % 4 == 0 else 'value'} {vocab[next(g) % len(vocab)]} {cls if next(g) % 5 == 0 else 'node'}" for _ in range(30 + next(g) % 30))
        results.append({"turn": t, "topic": topic, "text": text, "path": path})
    last_topic = names[next(g) % 8]
    prompt = f"please look at the {_TOPICS[last_topic][3].split()[0]} problem again, especially {_TOPICS[last_topic][3].split()[1]} and {_TOPICS[last_topic][3].split()[2]}"
    labels = []
    for r in results:
        p = 0.8 if r["topic"] == last_topic else 0.03 + 0.3 * math.exp(-(59 - r["turn"]) / 25)  # recency helps a little for everything else
        labels.append(1 if next(g) % 1000 < p * 1000 else 0)
    return results, prompt, last_topic, labels


def sim_1324() -> dict:
    # --- the metric itself: two algorithms agree
    rng = _lcg(5)
    for _ in range(30):
        sc = [next(rng) % 7 for _ in range(25)]
        lb = [int(next(rng) % 3 == 0) for _ in range(25)]
        if 0 < sum(lb) < len(lb):
            assert abs(_auc_pairs(sc, lb) - _auc_ranks(sc, lb)) < 1e-12
    # --- AUC of age vs BM25-to-latest-prompt as reuse predictors, over 20 seeded sessions (aged = older than 8 turns)
    a_age, a_bm = [], []
    kept_bm = kept_age = reused_total = 0
    for seed in range(20):
        results, prompt, topic, labels = _session_1324(seed)
        aged = [i for i, r in enumerate(results) if r["turn"] < 52]
        sc_bm = _bm25(_terms(prompt), [_terms(results[i]["text"]) for i in aged])
        sc_age = [results[i]["turn"] for i in aged]  # younger (larger turn) = higher score
        lab = [labels[i] for i in aged]
        if 0 < sum(lab) < len(lab):
            a_age.append(_auc_ranks(sc_age, lab))
            a_bm.append(_auc_ranks(sc_bm, lab))
        # equal budget: keep 12 of the aged results by each score, count the reused ones kept
        k = 12
        top_bm = sorted(range(len(aged)), key=lambda j: (-sc_bm[j], j))[:k]
        top_age = sorted(range(len(aged)), key=lambda j: (-sc_age[j], j))[:k]
        kept_bm += sum(lab[j] for j in top_bm)
        kept_age += sum(lab[j] for j in top_age)
        reused_total += sum(lab)
    auc_age, auc_bm = sum(a_age) / len(a_age), sum(a_bm) / len(a_bm)
    assert auc_age > 0.5, f"the generator gives recency a small real effect, so age must be a better-than-chance predictor ({auc_age:.2f}); below 0.5 means the age score has the wrong sign"
    assert auc_bm > auc_age + 0.1, f"relevance must beat age on this reuse model ({auc_bm:.2f} vs {auc_age:.2f})"
    assert kept_bm > kept_age, "at an equal keep budget the relevance rule keeps more of the results that were reused"

    # --- the policy: which aged results to stub. Keep = youngest 8, top-K by BM25, any result naming a path the prompt names, errors.
    def to_stub(results, prompt, k=12, young=8):
        aged = [i for i, r in enumerate(results) if r["turn"] < len(results) - young]
        if not _terms(prompt):
            return sorted(aged, key=lambda i: results[i]["turn"])[: max(0, len(aged) - k)]  # no prompt to rank against: fall back to age
        sc = _bm25(_terms(prompt), [_terms(results[i]["text"]) for i in aged])
        keep = {aged[j] for j in sorted(range(len(aged)), key=lambda j: (-sc[j], j))[:k] if sc[j] > 0}
        named = {i for i in aged if results[i]["path"] in prompt}
        return [i for i in aged if i not in keep and i not in named and not results[i].get("error")]

    results, prompt, topic, labels = _session_1324(3)
    stubbed = to_stub(results, prompt)
    assert stubbed and len(stubbed) < len(results)
    # a result whose path the prompt names is never stubbed, however old and however low its BM25 score
    results[2]["path"] = "ops/deploy.py"
    forced = to_stub(results, prompt + " and also fix ops/deploy.py")
    assert 2 not in forced, "an explicitly named path pins the result"
    assert to_stub(results, "") == sorted(range(len(results) - 8), key=lambda i: results[i]["turn"])[: max(0, len(results) - 8 - 12)], "an empty prompt falls back to age"
    results[5]["error"] = True
    assert 5 not in to_stub(results, prompt), "results marked as errors are never stubbed"
    # --- the honest failure: a prompt that uses other words than the file ('the config' for settings.py) scores zero and the result is stubbed
    cfg = [{"turn": 0, "topic": "x", "text": "settings.py\nDEBUG = False\nSECRET_KEY = 'x'\nALLOWED_HOSTS = []", "path": "settings.py"}] + [{"turn": t, "topic": "y", "text": "unrelated output about caching and retries " * 3, "path": f"f{t}.py"} for t in range(1, 30)]
    assert 0 in to_stub(cfg, "why is the config ignored on startup", k=3), "vocabulary mismatch: BM25 does not see that 'config' means settings.py (the stub still names the path)"
    # --- determinism
    assert to_stub(results, prompt) == to_stub(results, prompt)
    return {"auc_age": round(auc_age, 2), "auc_bm25_to_latest_prompt": round(auc_bm, 2), "reused_results_kept_at_budget_12_bm25_vs_age": f"{kept_bm} vs {kept_age} of {reused_total}",
            "caveat": "synthetic reuse model written for this sim: shows the mechanism and the failure case, not the idea's real-transcript AUC"}


case(
    1324,
    claim="Aged tool results are ranked for stubbing by BM25 against the latest prompt instead of by age: on seeded synthetic sessions the AUC as a reuse predictor and the reused results kept at an equal budget both beat age (two independent AUC algorithms agree), a result whose path the prompt names, an error result and everything when the prompt is empty are handled by pin and fallback rules, and a vocabulary mismatch ('config' vs settings.py) is shown to be stubbed.",
    sim=sim_1324,
)


# ===============================================================================================================
# New tools instead of old habits: #449 replace_lines with a content-hash guard, #485 ci_failure (SIMS)

# ---------------------------------------------------------------------------------------------------------------
# #449 replace_lines(path, start, end, sha, new). REAL files in a temp dir.

class LineEditor:
    """The sha is of lines start-1..end+1 (the range plus ONE line of context each side) exactly as read, so an edit is rejected when
    the range or its neighbours changed, but a change elsewhere in the file is not a reason to refuse. Optional file_sha is the strict mode."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)

    def _resolve(self, path: str) -> str | None:
        full = os.path.realpath(os.path.join(self.root, path))
        return full if full == self.root or full.startswith(self.root + os.sep) else None

    @staticmethod
    def _lines(raw: bytes):
        try:
            return raw.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _window_sha(lines: list[str], a: int, b: int) -> str:
        return _sha("".join(lines[max(0, a - 2):min(len(lines), b + 1)]), 10)  # 1-based a..b inclusive, one line of context each side

    def read(self, path: str, start: int = 1, end: int | None = None) -> dict:
        full = self._resolve(path)
        raw = open(full, "rb").read()
        lines = self._lines(raw)
        end = len(lines) if end is None else min(end, len(lines))
        return {"text": "".join(f"{i}\t{l}" if l.endswith("\n") else f"{i}\t{l}\n" for i, l in enumerate(lines[start - 1:end], start)), "start": start, "end": end,
                "sha": self._window_sha(lines, start, end), "file_sha": _sha(raw, 10), "n_lines": len(lines)}

    def replace_lines(self, path: str, start: int, end: int, sha: str, new: str, file_sha: str | None = None) -> dict:
        full = self._resolve(path)
        if full is None or not os.path.isfile(full):
            return {"ok": False, "error": "path outside the project or not a file"}
        raw = open(full, "rb").read()
        lines = self._lines(raw)
        if lines is None:
            return {"ok": False, "error": "not valid UTF-8 text: use another tool"}
        if not (1 <= start <= end <= len(lines)):
            return {"ok": False, "error": f"range {start}-{end} is outside the file ({len(lines)} lines); read it again"}
        if file_sha is not None and _sha(raw, 10) != file_sha:
            return {"ok": False, "error": "file changed since you read it (strict mode)"}
        if self._window_sha(lines, start, end) != sha:
            k = end - start + 1
            hits = [s for s in range(1, len(lines) - k + 2) if self._window_sha(lines, s, s + k - 1) == sha]
            return {"ok": False, "error": f"stale: lines {start}-{end} (or their neighbours) changed since your read", "moved_to": hits[0] if len(hits) == 1 else None,
                    "current": "".join(f"{i}\t{l}" for i, l in enumerate(lines[max(0, start - 2):end + 1], max(1, start - 1)))}
        crlf = sum(l.endswith("\r\n") for l in lines) > len(lines) / 2
        eol = "\r\n" if crlf else "\n"
        parts = new.split("\n")[:-1] if new.endswith("\n") else new.split("\n")  # "a\n" is one line, "a\n\n" is a line and a blank line
        new_lines = [l.rstrip("\r") + eol for l in parts] if new != "" else []
        if end == len(lines) and not lines[-1].endswith("\n") and new_lines:
            new_lines[-1] = new_lines[-1].rstrip("\r\n")  # the range ended the file without a final newline: keep it that way
        out = "".join(lines[:start - 1] + new_lines + lines[end:])
        tmp = full + ".ctx-tmp"
        with open(tmp, "wb") as f:
            f.write(out.encode("utf-8"))
        os.chmod(tmp, os.stat(full).st_mode)
        os.replace(tmp, full)
        return {"ok": True, "new_range": [start, start + len(new_lines) - 1], "shift": len(new_lines) - (end - start + 1)}


def _module_200() -> str:
    g = _lcg(449)
    out = ['"""Order processing."""', "import json", ""]
    for i in range(22):
        out += [f"def handle_{i}(order, retries={i % 3}):", f'    """Process order kind {i}."""', "    if order is None:", "        return None", f"    total = sum(l['price'] * l['qty'] for l in order['lines']) + {i}",
                f"    if total > {1000 + next(g) % 500}:", "        order['flag'] = 'review'", "    for attempt in range(retries + 1):", "        pass", "    return total", ""]
    return "\n".join(out)


def sim_449() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ed = LineEditor(d)
        src = _module_200()
        path = "orders.py"
        _write(os.path.join(d, path), src)
        lines = src.split("\n")
        # --- 20 edit tasks of 1-25 lines, non-overlapping; the Edit tool needs an old_string that is unique in the file, so context is added until it is
        g = _lcg(4490)
        raw_tasks = []
        for k in range(20):
            size = 1 + next(g) % 25 if k % 3 else 1 + next(g) % 3
            a = 1 + next(g) % (len(lines) - size - 2)
            raw_tasks.append((a, a + size - 1))
        tasks, last_end = [], 0
        for a, b in sorted(set(raw_tasks)):
            if a > last_end + 1:
                tasks.append((a, b))
                last_end = b
        new_of = lambda a, orig: [f"    # edited {a}"] + [l.replace("return", "return  ", 1) if "return" in l else l for l in orig]  # noqa: E731
        edit_in = replace_in = old_tok = 0
        cur = src.split("\n")  # the file as the Edit tool leaves it, edits applied bottom to top
        for a, b in sorted(tasks, reverse=True):
            lo, hi = a, b
            whole = "\n".join(cur)
            while whole.count("\n".join(cur[lo - 1:hi])) != 1:  # grow the old_string with neighbouring lines until it is unique
                if lo > 1 and (a - lo) <= (hi - b):
                    lo -= 1
                elif hi < len(cur):
                    hi += 1
                else:
                    lo -= 1
            old_string = "\n".join(cur[lo - 1:hi])
            new_block = new_of(a, cur[a - 1:b])
            new_string = "\n".join(cur[lo - 1:a - 1] + new_block + cur[b:hi])
            cur[lo - 1:hi] = new_string.split("\n")
            edit_in += tokens(json.dumps({"file_path": path, "old_string": old_string, "new_string": new_string}))
            old_tok += tokens(old_string)
            replace_in += tokens(json.dumps({"path": path, "start": a, "end": b, "sha": "0123456789", "new": "\n".join(new_block)}))
        ref = "\n".join(cur)
        # the same edits through replace_lines on the real file, bottom to top, the sha from one Read each
        for a, b in sorted(tasks, reverse=True):
            r = ed.read(path, a, b)
            res = ed.replace_lines(path, a, b, r["sha"], "\n".join(new_of(a, src.split("\n")[a - 1:b])) + "\n")
            assert res["ok"], res
        assert open(os.path.join(d, path)).read() == ref, "replace_lines and Edit produce the same file"
        saving = 1 - replace_in / edit_in
        old_share = old_tok / edit_in
        assert saving > 0.3 and old_share > 0.35, f"dropping old_string must cut the edit input by over 30% (saved {saving:.0%}, old_string share {old_share:.0%})"
        # --- stale handling, on the pristine file
        _write(os.path.join(d, path), src)
        r = ed.read(path, 30, 34)
        # (a) the range itself changed: rejected, current text returned
        _write(os.path.join(d, path), src.replace(lines[31], lines[31] + "  # touched"))
        res = ed.replace_lines(path, 30, 34, r["sha"], "x = 1")
        assert not res["ok"] and "stale" in res["error"] and "# touched" in res["current"]
        assert open(os.path.join(d, path)).read() == src.replace(lines[31], lines[31] + "  # touched"), "a rejected edit leaves the file untouched"
        # (b) a line was inserted ABOVE: the old numbers now point elsewhere: rejected, and the tool says where the text moved
        _write(os.path.join(d, path), "# header line\n" + src)
        res = ed.replace_lines(path, 30, 34, r["sha"], "x = 1")
        assert not res["ok"] and res["moved_to"] == 31, res  # Edit would have succeeded here: the price of line addressing is a retry
        res2 = ed.replace_lines(path, 31, 35, r["sha"], "x = 1")
        assert res2["ok"], "retrying at the reported position works"
        # (c) a change far away from the range and its neighbours is NOT a reason to refuse
        _write(os.path.join(d, path), src.replace(lines[150], lines[150] + "  # far away"))
        assert ed.replace_lines(path, 30, 34, r["sha"], "x = 1")["ok"]
        # (d) the limit: a run of identical lines. After a shift the same sha can match a different spot; strict mode catches it
        _write(os.path.join(d, path), "a\n" + "pass\n" * 8 + "z\n")
        rr = ed.read(path, 4, 5)
        _write(os.path.join(d, path), "NEW\n" + "a\n" + "pass\n" * 8 + "z\n")
        wrong = ed.replace_lines(path, 4, 5, rr["sha"], "X")
        assert wrong["ok"], "inside a run of identical lines a shifted range still matches: the content guard cannot see the shift"
        _write(os.path.join(d, path), "a\n" + "pass\n" * 8 + "z\n")
        rr = ed.read(path, 4, 5)
        _write(os.path.join(d, path), "NEW\n" + "a\n" + "pass\n" * 8 + "z\n")
        assert not ed.replace_lines(path, 4, 5, rr["sha"], "X", file_sha=rr["file_sha"])["ok"], "strict mode (file_sha) refuses any change since the read"
        # --- file hygiene: CRLF, no final newline, delete lines, tabs and unicode, permissions, binary, path escape, bad ranges
        _write(os.path.join(d, "crlf.txt"), "one\r\ntwo\r\nthree\r\n")
        r = ed.read("crlf.txt", 2, 2)
        assert ed.replace_lines("crlf.txt", 2, 2, r["sha"], "TWO\nTWO-B")["ok"] and open(os.path.join(d, "crlf.txt"), "rb").read() == b"one\r\nTWO\r\nTWO-B\r\nthree\r\n"
        _write(os.path.join(d, "nonl.txt"), "a\nb\nlast")
        r = ed.read("nonl.txt", 3, 3)
        assert ed.replace_lines("nonl.txt", 3, 3, r["sha"], "LAST")["ok"] and open(os.path.join(d, "nonl.txt"), "rb").read() == b"a\nb\nLAST", "no final newline stays no final newline"
        _write(os.path.join(d, "u.txt"), "café\n\tindented\n→ arrow\n", 0o640)
        r = ed.read("u.txt", 2, 2)
        assert ed.replace_lines("u.txt", 2, 2, r["sha"], "")["ok"] and open(os.path.join(d, "u.txt"), encoding="utf-8").read() == "café\n→ arrow\n" and oct(os.stat(os.path.join(d, "u.txt")).st_mode)[-3:] == "640"
        open(os.path.join(d, "bin.dat"), "wb").write(b"\xff\xfe\x00\x01")
        assert "UTF-8" in ed.replace_lines("bin.dat", 1, 1, "x", "y")["error"]
        assert not ed.replace_lines("../outside.txt", 1, 1, "x", "y")["ok"] and not ed.replace_lines("nonl.txt", 5, 9, "x", "y")["ok"] and not ed.replace_lines("nonl.txt", 3, 2, "x", "y")["ok"]
        assert not [f for f in os.listdir(d) if f.endswith(".ctx-tmp")], "no temp file left behind"
    return {f"edit_input_tokens_{len(tasks)}_edits": edit_in, "replace_lines_input_tokens": replace_in, "saving": f"{saving:.0%}", "old_string_share_of_edit_input": f"{old_share:.0%}", "edits": len(tasks),
            "stale_reject_when_a_line_is_inserted_above": "yes (Edit would succeed): a retry costs a turn", "known_blind_spot": "runs of identical lines after a shift (strict mode closes it)"}


case(
    449,
    claim="replace_lines(path, start, end, sha, new) applies the same non-overlapping edits (12 of 20 drawn) as old_string/new_string to real files and cuts the edit input tokens by 38% (old_string was over a third of it); the sha covers the range plus one context line each side, so a changed range or a shift above is rejected with the current text and the new position, a far-away change is not, strict file_sha mode catches shifts inside runs of identical lines, and CRLF, a missing final newline, deletion, unicode, permissions, binary files and path escapes are handled.",
    sim=sim_449,
)


# ---------------------------------------------------------------------------------------------------------------
# #485 one ci_failure tool replacing four serial gh calls

def _job_log(job: str, steps: list[str], failing: int | None, cause: list[str], seed: int) -> str:
    """A GitHub Actions job log: timestamped lines, one ##[group] per step in step order, pip noise, then the step's output."""
    g = _lcg(seed)
    out = []
    t = [0]

    def ts():
        t[0] += 1 + next(g) % 7
        return f"2026-11-04T09:{t[0] // 6000 % 60:02d}:{t[0] // 100 % 60:02d}.{t[0] % 100:02d}34567Z "

    for i, s in enumerate(steps):
        out += [ts() + f"##[group]{s}", ts() + "shell: /usr/bin/bash -e {0}", ts() + "##[endgroup]"]  # the group holds the command echo; the output follows it
        if s.startswith("Run pip install"):
            out += [ts() + f"Collecting pkg{k}==1.{k}.0" for k in range(60)] + [ts() + f"  Downloading pkg{k}-1.{k}.0-py3-none-any.whl (1{k % 10}0 kB)" for k in range(60)] + [ts() + "Successfully installed " + " ".join(f"pkg{k}-1.{k}.0" for k in range(12))]
        elif s.startswith("Run pytest"):
            out += [ts() + f"tests/test_mod{k}.py {'.' * (20 + k)}   [{k * 4:3d}%]" for k in range(25)]
        elif s.startswith("Set up job"):
            out += [ts() + f"Runner Image Provisioner {k}" for k in range(20)]
        else:
            out += [ts() + f"{s} output line {k}" for k in range(8)]
        if failing == i:
            out += [ts() + l for l in cause] + [ts() + "##[error]Process completed with exit code 1."]
    return "\n".join(out) + "\n"


_STEPS = ["Set up job", "Run actions/checkout@v4", "Run actions/setup-python@v5", "Run pip install -r requirements.txt", "Run ruff check .", "Run pytest -x tests", "Run actions/upload-artifact@v4", "Post Run actions/checkout@v4", "Complete job"]
_CAUSE = ["=================================== FAILURES ===================================", "_______________________ test_tax_rounds_half_even ________________________", "", ">       assert tax_cents(1050, rate=0.21) == 220", "E       assert 221 == 220", "tests/test_tax.py:17: AssertionError",
          "=========================== short test summary info ============================", "FAILED tests/test_tax.py::test_tax_rounds_half_even - assert 221 == 220", "======================== 1 failed, 119 passed in 3.18s ========================"]


class FakeGh:
    """gh api backed by a canned run: 9 matrix jobs (3 OS x 3 Python), two failing in 'Run pytest' with the same cause, one in an unrelated step."""

    def __init__(self, scenario: str = "matrix"):
        self.calls: list[str] = []
        self.scenario = scenario
        self.jobs = []
        for os_ in ("ubuntu-latest", "windows-latest", "macos-latest"):
            for py in ("3.11", "3.12", "3.13"):
                failing = 5 if py == "3.12" and os_ != "ubuntu-latest" else None
                steps = [{"number": i + 1, "name": s, "conclusion": "failure" if failing == i else ("skipped" if failing is not None and i > failing and not s.startswith(("Post", "Complete")) else "success")} for i, s in enumerate(_STEPS)]
                cause = [l.replace("tests/", "tests\\") if os_ == "windows-latest" else l for l in _CAUSE]
                self.jobs.append({"id": 7000 + len(self.jobs), "name": f"test ({os_}, {py})", "conclusion": "failure" if failing is not None else "success", "steps": steps,
                                  "log": _job_log(f"{os_}{py}", _STEPS, failing, cause, 485 + len(self.jobs)),
                                  "ann": [{"path": "tests/test_tax.py", "start_line": 17, "annotation_level": "failure", "message": "assert 221 == 220"}] if failing is not None else []})
        if scenario == "runner-lost":
            self.jobs = [{**self.jobs[0], "conclusion": "failure", "steps": [{**s, "conclusion": "success"} for s in self.jobs[0]["steps"]]}]
        self.expired = scenario == "expired"

    def api(self, endpoint: str):
        self.calls.append(endpoint)
        if endpoint.endswith("/check-runs"):
            return {"check_runs": [{"id": k + 1, "name": j["name"], "conclusion": j["conclusion"], "details_url": f"https://github.com/acme/shop/actions/runs/9981/job/{j['id']}"} for k, j in enumerate(self.jobs)]}
        if endpoint.endswith("/jobs"):
            return {"jobs": [{k: v for k, v in j.items() if k not in ("log", "ann")} for j in self.jobs]}
        m = re.search(r"/jobs/(\d+)/logs$", endpoint)
        if m:
            if self.expired:
                raise RuntimeError("gh: HTTP 410: Logs have expired")
            return next(j for j in self.jobs if j["id"] == int(m.group(1)))["log"]
        if endpoint.endswith("/annotations"):
            jid = int(re.search(r"/check-runs/(\d+)/annotations", endpoint).group(1))
            j = self.jobs[jid - 1] if jid <= len(self.jobs) else None
            if self.scenario == "runner-lost":
                return [{"path": ".github", "start_line": 1, "annotation_level": "failure", "message": "The runner has received a shutdown signal."}]
            return j["ann"] if j else []
        raise RuntimeError(f"gh: unknown endpoint {endpoint}")


_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")


def ci_failure(gh: FakeGh, sha: str = "abc1234") -> str:
    try:
        checks = gh.api(f"repos/acme/shop/commits/{sha}/check-runs")["check_runs"]
    except Exception as ex:  # noqa: BLE001
        return f"ci_failure: could not list the check runs for {sha}: {ex}"
    bad = [c for c in checks if c["conclusion"] in ("failure", "timed_out", "cancelled")]
    pending = [c for c in checks if c["conclusion"] is None]
    if not bad:
        return f"no failed check for {sha}: {len(checks) - len(pending)} finished and green" + (f", {len(pending)} still running" if pending else "")
    run = re.search(r"/runs/(\d+)/", bad[0]["details_url"]).group(1)
    try:
        jobs = {j["id"]: j for j in gh.api(f"repos/acme/shop/actions/runs/{run}/jobs")["jobs"]}
    except Exception as ex:  # noqa: BLE001
        return f"ci_failure: {len(bad)} failed check(s) in run {run} ({', '.join(c['name'] for c in bad)}); could not read the jobs: {ex}"
    groups: dict[tuple, dict] = {}
    notes = []
    for c in bad:
        jid = int(re.search(r"/job/(\d+)$", c["details_url"]).group(1))
        job = jobs.get(jid)
        step = next((s for s in (job or {}).get("steps", []) if s["conclusion"] == "failure"), None)
        try:
            ann = gh.api(f"repos/acme/shop/check-runs/{c['id']}/annotations")
        except Exception:  # noqa: BLE001
            ann = []
        lines: list[str] = []
        if step is None:
            lines = [f"annotation: {a['message']}" for a in ann]
            key = ("no failing step", tuple(lines))
            notes.append(f"{c['name']}: no step failed (infrastructure failure?)")
        else:
            try:
                log = gh.api(f"repos/acme/shop/actions/jobs/{jid}/logs").split("\n")
                starts = [i for i, l in enumerate(log) if "##[group]" in l]
                if len(starts) != len(job["steps"]):
                    raise ValueError("log and steps do not line up")
                a = starts[step["number"] - 1]
                b = starts[step["number"]] if step["number"] < len(starts) else len(log)
                seg = [_TS.sub("", l).replace("\\", "/") for l in log[a:b]]
            except ValueError:  # the step cannot be located: the last 40 lines of the whole log hold the error
                seg = [_TS.sub("", l).replace("\\", "/") for l in log][-40:]
            except Exception as ex:  # noqa: BLE001
                seg = [f"(log unavailable: {ex})"]
            keep: set[int] = set()
            for i, l in enumerate(seg):
                if "##[error]" in l:
                    keep |= set(range(max(0, i - 12), i + 1))  # the generic 'exit code 1' line says nothing; its cause is just above
                elif re.search(r"FAILED|^E |Error|error:|Traceback", l):
                    keep |= {j for j in (i - 1, i, i + 1) if 0 <= j < len(seg)}
            noise = re.compile(r"##\[(end)?group\]|^shell: ")
            lines = [seg[i] for i in sorted(keep) if seg[i].strip() and not noise.search(seg[i])][-25:] or [l for l in seg[-5:] if not noise.search(l)]
            lines += [f"annotation: {a_['path']}:{a_['start_line']} {a_['message']}" for a_ in ann]
            key = (step["name"], tuple(lines))
        groups.setdefault(key, {"jobs": [], "step": step})["jobs"].append(c["name"])
    out = [f"CI failure for acme/shop@{sha}: run {run}, {len(bad)} of {len(checks)} jobs failed"]
    for (stepname, lines), g in groups.items():
        s = g["step"]
        out.append(f"failing step: #{s['number']} \"{s['name']}\"" if s else "failing step: none")
        out.append(f"affects: {', '.join(g['jobs'])}" + (" (same root cause)" if len(g["jobs"]) > 1 else ""))
        out += ["  " + l for l in lines]
    out += notes
    out.append(f"passed: {len(checks) - len(bad) - len(pending)}; full logs: gh run view {run} --log-failed")
    return "\n".join(out) + "\n"


def sim_485() -> dict:
    gh = FakeGh()
    digest = ci_failure(gh)
    # --- the four serial calls an agent makes without the tool: checks, run view (jobs), the failed log(s), and a grep over the log
    raw_checks = "\n".join(f"{c['name']}\t{'fail' if c['conclusion'] == 'failure' else 'pass'}\t1m{k}s\thttps://github.com/acme/shop/actions/runs/9981/job/{7000 + k}" for k, c in enumerate(gh.api("repos/acme/shop/commits/abc1234/check-runs")["check_runs"]))
    raw_jobs = json.dumps(gh.api("repos/acme/shop/actions/runs/9981/jobs"), indent=1)
    failed_logs = "".join(j["log"] for j in gh.jobs if j["conclusion"] == "failure")
    raw = raw_checks + raw_jobs + failed_logs
    assert tokens(digest) < 0.05 * tokens(raw), f"the digest must be a few percent of what the four calls print ({tokens(digest)} vs {tokens(raw)})"
    # --- reader view with an independent oracle: scan each FULL failing log for its error lines and look for them in the digest
    for j in gh.jobs:
        if j["conclusion"] != "failure":
            continue
        full = [_TS.sub("", l).replace("\\", "/") for l in j["log"].split("\n")]
        must = [l for l in full if l.startswith(("FAILED ", "E ", "##[error]"))]
        assert must and all(l in digest for l in must), f"{j['name']}: root-cause line missing from the digest: {[l for l in must if l not in digest]}"
    assert "failing step: #6" in digest and "Run pytest" in digest, "names the failing step"
    assert "test (windows-latest, 3.12), test (macos-latest, 3.12)" in digest and "same root cause" in digest, "both matrix legs, once"
    assert digest.count("FAILED tests/test_tax.py::test_tax_rounds_half_even") == 1, "the cause is printed once for the two legs"
    assert "tests/test_tax.py:17" in digest, "annotations are appended"
    assert "ubuntu" not in digest.split("passed:")[0].split("affects:")[1], "a passing leg is not blamed"
    # --- tool calls: 1 + 1 + (1 log + 1 annotation call per failing job) are API calls inside ONE tool call for the agent
    n_api = len(gh.calls)
    # --- must NOT apply / fail-open
    green = FakeGh()
    for j in green.jobs:
        j["conclusion"] = "success"
    assert "no failed check" in ci_failure(green) and "9 finished and green" in ci_failure(green)
    pend = FakeGh()
    for j in pend.jobs:
        j["conclusion"] = "success"
    pend.jobs[0]["conclusion"] = None
    assert "1 still running" in ci_failure(pend), "in-progress jobs are reported, not mistaken for green"
    exp = ci_failure(FakeGh("expired"))
    assert "log unavailable" in exp and "410" in exp and "annotation: tests/test_tax.py:17" in exp and "failing step: #6" in exp, "expired logs: the step and the annotations still tell the story"
    lost = ci_failure(FakeGh("runner-lost"))
    assert "no step failed" in lost and "shutdown signal" in lost, "an infrastructure failure has no failing step; the annotation is the cause"
    broken = FakeGh()
    broken.jobs[4]["log"] = broken.jobs[4]["log"].replace("##[group]Run ruff check .", "plain line")  # log and steps no longer line up
    mis = ci_failure(broken)
    assert "FAILED tests/test_tax.py::test_tax_rounds_half_even" in mis, "step not locatable in the log: fall back to the tail of the log"

    class Down(FakeGh):
        def api(self, endpoint):
            if "/jobs" in endpoint and "/logs" not in endpoint:
                raise RuntimeError("gh: HTTP 403: API rate limit exceeded")
            return super().api(endpoint)

    assert "could not read the jobs" in ci_failure(Down()) and "rate limit" in ci_failure(Down()) and "windows-latest" in ci_failure(Down()), "a failing call is named; what is known is still reported"
    # a second, different root cause in another job is listed separately
    two = FakeGh()
    two.jobs[2]["conclusion"] = "failure"
    two.jobs[2]["steps"][4]["conclusion"] = "failure"
    two.jobs[2]["log"] = _job_log("x", _STEPS, 4, ["src/app.py:3:1: F401 `os` imported but unused", "Found 1 error."], 99)
    two.jobs[2]["ann"] = [{"path": "src/app.py", "start_line": 3, "annotation_level": "failure", "message": "unused import"}]
    multi = ci_failure(two)
    assert multi.count("failing step:") == 2 and "F401" in multi and "FAILED tests/test_tax.py" in multi
    return {"tokens_four_calls": tokens(raw), "tokens_digest": tokens(digest), "saving": f"{1 - tokens(digest) / tokens(raw):.1%}", "agent_turns": "4 -> 1", "api_calls_inside_the_tool": n_api,
            "matrix_legs_reported_once": 2}


case(
    485,
    claim="One ci_failure call finds the failing check, job and step from steps[].conclusion, reads only that step's log slice, appends annotations and groups identical matrix legs, returning a digest under 5% of the tokens of checks, run view, failed logs and grep; every FAILED, E and ##[error] line of each full failing log (found by an independent scan) is in the digest, and green, in-progress, expired-log, infrastructure-failure, misaligned-log, rate-limited and multi-cause runs are each reported truthfully.",
    sim=sim_485,
)


# ===============================================================================================================
# What agents see on disk: #1302 digest mirror of a repo, #1303 git sparse-checkout worktree (SIMS, real processes)
# Real grep, find, ls, git, and (when present) rg run in temp dirs. Findings measured here, not from memory:
#   - `grep -r` (GNU 3.x) and `rg` do NOT follow symlinks met while recursing, so a mirror made of symlinks to the real source
#     is empty to them; hard links (or copies) work. The idea says 'symlinks plus digests': the symlink half does not survive grep -r.
#   - `git add` of a new path outside the sparse cone fails loudly (exit 1, a hint naming --sparse), it is not silently skipped.

def _git(cwd: str, *args: str):
    return _run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", "-c", "advice.updateSparsePath=true", *args], cwd=cwd, env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


def _lockfile(n: int = 260) -> str:
    g = _lcg(1302)
    pk = {"": {"name": "shop", "version": "1.0.0", "dependencies": {"lodash": "^4.17.21", "left-pad": "^1.3.0", "react": "^18.3.1"}}}
    names = ["lodash", "left-pad", "react", "scheduler", "loose-envify", "js-tokens"] + [f"pkg-{w}{k}" for k in range(n) for w in ("core", "util", "plugin")][: n - 6]
    for nm in names:
        v = f"{1 + next(g) % 9}.{next(g) % 20}.{next(g) % 30}" if nm not in ("lodash", "left-pad", "react") else {"lodash": "4.17.21", "left-pad": "1.3.0", "react": "18.3.1"}[nm]
        pk[f"node_modules/{nm}"] = {"version": v, "resolved": f"https://registry.npmjs.org/{nm}/-/{nm}-{v}.tgz", "integrity": "sha512-" + hashlib.sha512(f"{nm}{v}".encode()).hexdigest()[:86], "dev": next(g) % 3 == 0, "dependencies": {"loose-envify": "^1.1.0"}}
    return json.dumps({"name": "shop", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": pk}, indent=2) + "\n"


def _bundle() -> str:
    g = _lcg(13)
    parts = [f"function a{k}(e,t){{return e.x{next(g) % 99}+t*{next(g) % 999}||'{_sha(str(k), 6)}'}}" for k in range(900)]
    parts.insert(450, "var FLAGS={FEATURE_FLAG_BETA:!1,FEATURE_FLAG_DARK:!0};")
    return "!function(){" + "".join(parts) + "}();\n//# sourceMappingURL=app.min.js.map\n"


def _snap() -> str:
    return "// Jest Snapshot v1, https://goo.gl/fbAQLP\n\n" + "\n".join(f"exports[`<Cart /> renders state {k} 1`] = `\n<div className=\"cart\">\n" + "".join(f"  <li key=\"{j}\">item {j} x{k}</li>\n" for j in range(14)) + "</div>\n`;\n" for k in range(40))


def _users() -> str:
    return json.dumps([{"id": i, "name": f"user{i}", "email": f"user{i}@example.com", "plan": ["free", "pro"][i % 2], "created": f"2026-0{1 + i % 9}-1{i % 9}", "address": {"city": "Haarlem", "zip": f"20{i % 90 + 10}AB"}} for i in range(220)], indent=1) + "\n"


def _notebook() -> str:
    img = base64.b64encode(hashlib.sha512(b"x").digest() * 300).decode()
    cells = [{"cell_type": "code", "execution_count": k, "metadata": {}, "source": [f"df{k} = load({k})\n", f"df{k}.plot()\n"], "outputs": [{"output_type": "display_data", "data": {"image/png": img, "text/plain": ["<Figure size 640x480>"]}, "metadata": {}}]} for k in range(1, 5)]
    cells.insert(2, {"cell_type": "markdown", "metadata": {}, "source": ["## Findings\n", "Revenue grew 12%."]})
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}, indent=1) + "\n"


def _repo_1302(root: str) -> None:
    for k in range(10):
        _write(os.path.join(root, "src", f"mod{k}.py"), f'"""Module {k}."""\n\n\ndef total_{k}(items):\n    return sum(i * {k + 1} for i in items)\n\n\ndef label_{k}(x):\n    return f"mod{k}:{{x}}"\n' + "\n".join(f"# note {j}" for j in range(30)) + "\n")
    _write(os.path.join(root, "README.md"), "# shop\n\nSmall demo repo.\n")
    _write(os.path.join(root, "package-lock.json"), _lockfile())
    _write(os.path.join(root, "dist", "app.min.js"), _bundle())
    _write(os.path.join(root, "src", "__snapshots__", "cart.test.js.snap"), _snap())
    _write(os.path.join(root, "fixtures", "users.json"), _users())
    _write(os.path.join(root, "notebooks", "analysis.ipynb"), _notebook())


def _digest_of(rel: str, text: str) -> str | None:
    """A digest body, or None when the file stays real (source and small files)."""
    name = os.path.basename(rel)
    if len(text) < 8000:
        return None
    if name == "package-lock.json":
        pk = json.loads(text)["packages"]
        rows = sorted(f"{k[len('node_modules/'):]}@{v['version']}{' (dev)' if v.get('dev') else ''}" for k, v in pk.items() if k)
        return f"lockfileVersion {json.loads(text)['lockfileVersion']}, {len(rows)} packages; integrity hashes and resolved URLs dropped\n" + "\n".join(rows)
    if name.endswith(".min.js"):
        return f"{len(text)} bytes, {text.count(chr(10))} lines, {text.count('function')} functions; sourceMap: {re.findall(r'sourceMappingURL=(\S+)', text)[-1:]}\nstarts: {text[:120]!r}\nends: {text[-100:]!r}"
    if name.endswith(".snap"):
        names = re.findall(r"^exports\[`(.+?)`\]", text, re.M)
        return f"{len(names)} snapshots, {len(text)} bytes; names:\n" + "\n".join(names)
    if name.endswith(".ipynb"):
        nb = json.loads(text)
        out = []
        for i, c in enumerate(nb["cells"]):
            kinds = sorted({k for o in c.get("outputs", []) for k in o.get("data", {})})
            out.append(f"[{i}] {c['cell_type']}: {''.join(c['source'])[:120]!r}" + (f" -> {len(c['outputs'])} output(s) {kinds} dropped" if c.get("outputs") else ""))
        return "\n".join(out)
    if name.endswith(".json"):
        data = json.loads(text)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            sch = lambda d: {k: (sch(v) if isinstance(v, dict) else type(v).__name__) for k, v in d.items()}  # noqa: E731
            return f"array of {len(data)} objects, {len(text)} bytes; first record keys: {json.dumps(sch(data[0]))}\nfirst: {json.dumps(data[0])}"
    return None


class Mirror:
    """A mirror of a repo. Source and small files are HARD-linked (or symlinked, for the naive variant); big generated files become digest files."""

    def __init__(self, real: str, dest: str, link: str = "hard"):
        self.real, self.dest, self.link = real, dest, link
        self.digests: dict[str, str] = {}  # rel path -> sha1 of the real file the digest was made from

    def build(self) -> None:
        for dp, _dn, fns in os.walk(self.real):
            for fn in fns:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, self.real)
                self._one(rel, full)

    def _one(self, rel: str, full: str) -> None:
        out = os.path.join(self.dest, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.lexists(out):
            os.chmod(out, 0o644) if not os.path.islink(out) else None
            os.remove(out)
        raw = open(full, "rb").read()
        try:
            dig = _digest_of(rel, raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError):
            dig = None  # unparseable: stay real
        if dig is None:
            if self.link == "sym":
                os.symlink(full, out)
            else:
                try:
                    os.link(full, out)
                except OSError:
                    shutil.copy2(full, out)  # another filesystem: a read-only copy, and the header-less file would mislead, so mark it
            return
        self.digests[rel] = _sha(raw, 10)
        _write(out, f"# ctxprune DIGEST of {rel} ({len(raw)} bytes, sha1 {_sha(raw, 10)}); NOT the file. The real file: {full}\n{dig}\n", 0o444)

    def stale(self) -> list[str]:
        return sorted(rel for rel, h in self.digests.items() if not os.path.exists(os.path.join(self.real, rel)) or _sha(open(os.path.join(self.real, rel), "rb").read(), 10) != h)

    def refresh(self) -> list[str]:
        gone = self.stale()
        for rel in gone:
            full = os.path.join(self.real, rel)
            if os.path.exists(full):
                self._one(rel, full)
            else:
                os.chmod(os.path.join(self.dest, rel), 0o644)
                os.remove(os.path.join(self.dest, rel))
                self.digests.pop(rel)
        return gone


def _explore(root: str) -> dict[str, str]:
    """The scripted session: the same real commands in whichever tree."""
    cmds = {"ls -R": ["ls", "-R"], "find": ["find", ".", "-type", "f", "-not", "-path", "./.git*"], "cat lock": ["cat", "package-lock.json"], "cat bundle": ["cat", "dist/app.min.js"],
            "cat users": ["cat", "fixtures/users.json"], "cat src": ["cat", "src/mod3.py"], "grep lodash": ["grep", "-rn", "lodash", "."]}
    return {k: _run(argv, cwd=root)[1] for k, argv in cmds.items()}


def sim_1302() -> dict:
    with tempfile.TemporaryDirectory() as d:
        real, mir, sym = os.path.join(d, "real"), os.path.join(d, "mirror"), os.path.join(d, "symmirror")
        _repo_1302(real)
        m = Mirror(real, mir, "hard")
        m.build()
        Mirror(real, sym, "sym").build()
        full, dig = _explore(real), _explore(mir)
        t_full, t_dig = sum(tokens(v) for v in full.values()), sum(tokens(v) for v in dig.values())
        assert t_dig < 0.15 * t_full, f"the scripted exploration should cost under 15% of the real tree ({t_dig} vs {t_full} tokens)"
        assert dig["cat src"] == full["cat src"] and dig["ls -R"] == full["ls -R"] and dig["find"] == full["find"], "source files and the tree shape are the real thing"
        # --- five questions: four are answered the same from the mirror, one cannot be and says so
        q = {}
        q["lodash version pinned"] = ("lodash@4.17.21" in dig["cat lock"], json.loads(full["cat lock"])["packages"]["node_modules/lodash"]["version"] == "4.17.21")
        q["functions in src/mod3.py"] = (re.findall(r"^def (\w+)", dig["cat src"], re.M), re.findall(r"^def (\w+)", full["cat src"], re.M))
        snap_names = lambda t: sorted(re.findall(r"<Cart /> renders state \d+ 1", t))  # noqa: E731
        q["snapshot names"] = (snap_names(_run(["cat", "src/__snapshots__/cart.test.js.snap"], cwd=mir)[1]), snap_names(_run(["cat", "src/__snapshots__/cart.test.js.snap"], cwd=real)[1]))
        users_digest = _run(["cat", "fixtures/users.json"], cwd=mir)[1]
        q["users record keys"] = (sorted(json.loads(re.search(r"first record keys: (\{.*\})", users_digest).group(1))), sorted(json.load(open(os.path.join(real, "fixtures/users.json")))[0]))
        for k, (a, b) in q.items():
            assert a == b, f"question {k!r}: mirror {a!r} vs real {b!r}"
        flag_real = _run(["grep", "-rln", "FEATURE_FLAG_BETA", "."], cwd=real)[1]
        flag_mirror = _run(["grep", "-rln", "FEATURE_FLAG_BETA", "."], cwd=mir)[1]
        assert "dist/app.min.js" in flag_real and flag_mirror.strip() == "", "the fifth question (is a string inside the minified bundle?) cannot be answered from the digest: the mirror says 'not found'"
        listing = _run(["grep", "-rl", "ctxprune DIGEST", "."], cwd=mir)[1].split()
        n_digests = len(m.digests)
        assert sorted(listing) == sorted("./" + r for r in m.digests) and n_digests == 5, "every digest announces itself: the agent can list exactly which files it must open for real"
        head = open(os.path.join(mir, "dist/app.min.js")).readline()
        assert "NOT the file" in head and os.path.join(real, "dist/app.min.js") in head, "the header points at the real path"
        # --- links: grep -r does not follow symlinks, hard links work
        gnu = "GNU" in _run(["grep", "--version"])[1]
        hit_hard = _run(["grep", "-rn", "def total_3", "."], cwd=mir)[1]
        hit_sym = _run(["grep", "-rn", "def total_3", "."], cwd=sym)[1]
        assert "src/mod3.py" in hit_hard, "hard-linked source is found by grep -r"
        if gnu:
            assert hit_sym == "", "GNU grep -r skips symlinks found while recursing: a symlink mirror is invisible to it"
            assert "src/mod3.py" in _run(["grep", "-Rn", "def total_3", "."], cwd=sym)[1], "grep -R does follow them"
        rg_note = "rg not installed"
        if shutil.which("rg"):
            rg_note = f"rg finds {len(_run(['rg', '-l', 'def total_3', '.'], cwd=sym)[1].split())} files in the symlink mirror and {len(_run(['rg', '-l', 'def total_3', '.'], cwd=mir)[1].split())} in the hard-link mirror"
            assert "mod3.py" in _run(["rg", "-l", "def total_3", "."], cwd=mir)[1] and _run(["rg", "-l", "def total_3", "."], cwd=sym)[1] == ""
        # --- writes: an edit through the mirror reaches the real file (hard link kept); a digest is mode 0444 (root ignores mode bits: asserted, not enforced)
        with open(os.path.join(mir, "src/mod3.py"), "a") as f:
            f.write("# edited through the mirror\n")
        assert open(os.path.join(real, "src/mod3.py")).read().endswith("# edited through the mirror\n")
        assert oct(os.stat(os.path.join(mir, "dist/app.min.js")).st_mode)[-3:] == "444"
        if os.geteuid() != 0:
            try:
                open(os.path.join(mir, "dist/app.min.js"), "a").write("x")
                raise AssertionError("a digest must not be writable")
            except PermissionError:
                pass
        # --- staleness: the real lockfile changes (npm install); the digest is stale until refreshed
        assert m.stale() == []
        _write(os.path.join(real, "package-lock.json"), _lockfile().replace('"version": "4.17.21"', '"version": "4.17.22"', 1))
        assert m.stale() == ["package-lock.json"]
        assert m.refresh() == ["package-lock.json"] and m.stale() == []
        assert "lodash@4.17.22" in open(os.path.join(mir, "package-lock.json")).read() or "lodash@4.17.21" in open(os.path.join(mir, "package-lock.json")).read()
        # --- fail-open: an unparseable big JSON stays a real file; a file that is deleted from the real tree disappears from the mirror
        _write(os.path.join(real, "fixtures/broken.json"), "{" + "x" * 9000)
        m._one("fixtures/broken.json", os.path.join(real, "fixtures/broken.json"))
        assert open(os.path.join(mir, "fixtures/broken.json")).read().startswith("{x")
        os.remove(os.path.join(real, "dist/app.min.js"))
        assert m.refresh() == ["dist/app.min.js"] and not os.path.exists(os.path.join(mir, "dist/app.min.js"))
    return {"tokens_real_tree_session": t_full, "tokens_mirror_session": t_dig, "saving": f"{1 - t_dig / t_full:.0%}", "questions_answered_from_mirror": "4 of 5", "the_miss": "string inside the minified bundle",
            "symlink_mirror_vs_grep_-r": "invisible (GNU grep)" if gnu else "not checked (non-GNU grep)", "rg": rg_note, "digested_files": n_digests}


case(
    1302,
    claim="A mirror of the repo with hard-linked source and digest files for the lockfile, minified bundle, snapshot, big JSON fixture and notebook cuts a scripted ls/find/cat/grep session to under 15% of the tokens, answers four of five questions identically (lockfile version, functions, snapshot names, record keys) and misses the fifth (a string inside the bundle) while every digest announces itself with the real path; real grep -r cannot see a symlink mirror, edits through hard links reach the real file, digests are mode 0444, stale digests are detected and refreshed.",
    sim=sim_1302,
)


# ---------------------------------------------------------------------------------------------------------------
# #1303 git sparse-checkout worktree for agents

def _repo_1303(root: str) -> None:
    for k in range(10):
        _write(os.path.join(root, "src", f"mod{k}.py"), f"def handle_{k}(x):\n    return x + {k}\n" + "\n".join(f"# line {j}" for j in range(20)) + "\n")
    for k in range(5):
        _write(os.path.join(root, "tests", f"test_mod{k}.py"), f"from src.mod{k} import handle_{k}\n\n\ndef test_{k}():\n    assert handle_{k}(1) == {1 + k}\n")
    for k in range(4):
        _write(os.path.join(root, "docs", "current", f"guide{k}.md"), f"# Guide {k}\n\nHow to run {k}.\n")
    for k in range(40):
        _write(os.path.join(root, "docs", "archive", f"2019-{k:02d}.md"), f"# Archived note {k}\n" + "old text about handle_ functions\n" * 30)
    for p in ("left-pad", "lodash", "react"):
        for k in range(14):
            _write(os.path.join(root, "vendor", p, f"file{k}.js"), f"// {p} {k}\nmodule.exports = function handle_vendor_{k}() {{ return {k}; }};\n" * 8)
    for k in range(6):
        _write(os.path.join(root, "dist", f"chunk{k}.js"), "var a=" + "x" * 5000 + ";\n")
    for k in range(30):
        _write(os.path.join(root, "fixtures", f"case{k}.json"), json.dumps({"id": k, "payload": "p" * 800}) + "\n")
    _write(os.path.join(root, "README.md"), "# shop\n")
    _write(os.path.join(root, "package.json"), '{"name": "shop", "version": "1.0.0"}\n')


def sim_1303() -> dict:
    if not shutil.which("git"):
        raise AssertionError("git is required for this case")
    with tempfile.TemporaryDirectory() as d:
        main, wt = os.path.join(d, "repo"), os.path.join(d, "wt")
        os.makedirs(main)
        _repo_1303(main)
        assert _git(main, "init", "-q", "-b", "main")[0] == 0
        _git(main, "add", "-A")
        assert _git(main, "commit", "-qm", "init")[0] == 0
        n_tracked = len(_git(main, "ls-files")[1].split())
        assert _git(main, "worktree", "add", "-q", "--no-checkout", "-b", "agent/work", wt)[0] == 0
        rc, _out, err = _git(wt, "sparse-checkout", "set", "--cone", "src", "tests", "docs/current")
        assert rc == 0, err
        assert _git(wt, "checkout", "-q", "agent/work")[0] in (0, 1)
        rc, out, _ = _git(wt, "sparse-checkout", "list")
        assert sorted(out.split()) == ["docs/current", "src", "tests"]
        # --- identical exploration in both trees, real ls/find/grep
        cmds = {"ls -R": ["ls", "-R"], "find": ["find", ".", "-type", "f", "-not", "-path", "./.git*"], "grep handle_": ["grep", "-rn", "handle_", "--exclude-dir=.git", "."], "du": ["du", "-a", "."]}
        full = {k: _run(a, cwd=main)[1] for k, a in cmds.items()}
        sparse = {k: _run(a, cwd=wt)[1] for k, a in cmds.items()}
        t_full, t_sparse = sum(tokens(v) for v in full.values()), sum(tokens(v) for v in sparse.values())
        assert t_sparse < 0.4 * t_full, f"the exploration should cost under 40% in the sparse tree ({t_sparse} vs {t_full})"
        # --- the same answers for questions about the code that stays; and the miss for what was hidden
        in_src = lambda t: sorted(l for l in t.split("\n") if l.startswith("./src/") and "handle_" in l)  # noqa: E731
        assert in_src(full["grep handle_"]) == in_src(sparse["grep handle_"]) and in_src(sparse["grep handle_"]), "src answers are identical"
        assert "vendor/lodash" in full["grep handle_"] and "vendor/" not in sparse["grep handle_"], "grep for a vendored symbol finds nothing in the sparse tree: a silent miss"
        # --- the cone is not a wall: add what is needed, the files appear, and it can be narrowed again
        assert _git(wt, "sparse-checkout", "add", "vendor/lodash")[0] == 0 and os.path.exists(os.path.join(wt, "vendor/lodash/file0.js"))
        assert _git(wt, "sparse-checkout", "set", "--cone", "src", "tests", "docs/current")[0] == 0 and not os.path.exists(os.path.join(wt, "vendor"))
        # --- git itself stays truthful: status clean, new paths outside the cone are refused loudly
        assert _git(wt, "status", "--porcelain")[1] == ""
        _write(os.path.join(wt, "vendor/lodash/new.js"), "x\n")
        rc, out, err = _git(wt, "add", "vendor/lodash/new.js")
        assert rc != 0 and "sparse" in (out + err), "a path outside the cone is refused with a hint, not skipped"
        shutil.rmtree(os.path.join(wt, "vendor"))
        # --- commits still work normally and carry the whole tree
        with open(os.path.join(wt, "src/mod2.py"), "a") as f:
            f.write("# agent change\n")
        _write(os.path.join(wt, "src/new_mod.py"), "def fresh():\n    return 1\n")
        assert _git(wt, "add", "-A")[0] == 0
        assert _git(wt, "commit", "-qm", "agent work")[0] == 0
        tree = _git(wt, "ls-tree", "-r", "--name-only", "HEAD")[1].split()
        assert len(tree) == n_tracked + 1 and any(p.startswith("vendor/lodash/") for p in tree) and any(p.startswith("docs/archive/") for p in tree), "excluded files are still in the commit"
        changed = _git(wt, "diff", "--name-only", "HEAD~1", "HEAD")[1].split()
        assert sorted(changed) == ["src/mod2.py", "src/new_mod.py"], f"the commit contains only what the agent changed, not deletions of the hidden trees: {changed}"
        assert _git(wt, "status", "--porcelain")[1] == ""
        # --- and it merges back
        assert _git(main, "merge", "--ff-only", "-q", "agent/work")[0] == 0
        assert "# agent change" in open(os.path.join(main, "src/mod2.py")).read() and os.path.exists(os.path.join(main, "vendor/lodash/file0.js"))
        # --- must NOT apply: a task that needs the hidden tree is served by the full checkout, which is untouched
        assert "vendor/lodash" in _run(["grep", "-rn", "handle_vendor", "."], cwd=main)[1]
    return {"files_in_full_tree": len(full["find"].split()), "files_in_sparse_tree": len(sparse["find"].split()), "tokens_full": t_full, "tokens_sparse": t_sparse, "saving": f"{1 - t_sparse / t_full:.0%}",
            "silent_miss": "grep for a vendored symbol finds nothing in the sparse tree", "git_add_outside_cone": "refused with a sparse hint"}


case(
    1303,
    claim="A git worktree with a cone sparse-checkout of src, tests and docs/current cuts the same ls -R, find, grep and du exploration to under 40% of the tokens with identical answers for the code that stays; git status stays clean, new paths outside the cone are refused with a hint, a commit made there carries the whole tree and only the agent's changes, it fast-forwards into the main checkout, and a grep for a vendored symbol silently finds nothing (sparse-checkout add brings it back).",
    sim=sim_1303,
)
