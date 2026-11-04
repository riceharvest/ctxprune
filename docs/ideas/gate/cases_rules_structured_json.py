"""Structured-JSON, XML and SVG rule ideas: #120 #122 #131 #132 #133 #135 #322 #324 #421 #424 #521 #606 #732 #801 #880 #886 #891
#1067 #1154 #1162 #1168.

Every case here is a RULE: the encoder deletes information on purpose, `loses=` says exactly what, and `probes` answer the
questions an agent must still be able to answer, from the original and from the encoding, with different code paths:

* the encoders walk the document they parse (a path-aware or recursive transform);
* the probes treat the original as data (generic walkers, a stdlib XML/HTML parser, a state machine) and read the encoding
  with a small parser of its own text format, so a bug in one side is not mirrored in the other;
* every fixture puts the interesting item at the start, in the middle and at the end, includes a failure variant, and each case
  carries a negative that must come back byte-identical (other tool, already compact, must-stay-verbatim input).

Conventions: an encoder is total (any exception returns its input), self-gating (returns the input unless strictly shorter), and
refuses (returns the input) on anything it cannot parse completely, so a truncated or mutated document is never half-rewritten.
Fixtures marked "captured" were produced by the real tool in this container (eslint 10.1.0, git 2.43); the rest are
"reconstructed from memory" and say so. Sizes are in characters; `harness.tokens` (o200k when tiktoken loads) is reported by the
runner next to them.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import html
import json
import math
import random
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict, defaultdict
from html.parser import HTMLParser

from harness import Fixture, case

_BAD = object()


def jload(text: str):
    """Strict json.loads; _BAD on any failure (including 4300-digit ints and deep nesting)."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return _BAD


def jc(obj) -> str:
    """Compact JSON, non-ASCII kept."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def jp(obj) -> str:
    """Two-space pretty JSON, non-ASCII kept (how most CLIs print it)."""
    return json.dumps(obj, indent=2, ensure_ascii=False)


def gate(text: str, out: str) -> str:
    return out if len(out) < len(text) else text


def total(fn):
    """Make an encoder total: any exception hands the input back."""

    def wrapped(text: str) -> str:
        try:
            return fn(text)
        except Exception:  # noqa: BLE001  (RecursionError and ValueError included)
            return text

    wrapped.__name__ = fn.__name__
    return wrapped


def rng(seed: int) -> random.Random:
    return random.Random(seed)


def hexid(n: int, width: int = 24, salt: str = "") -> str:
    return hashlib.sha1(f"{salt}{n}".encode()).hexdigest().upper()[:width]


def safe_probes(fn):
    """Probes that never raise: a document the probe cannot read answers {'probe error': ...} on both sides, so a bug shows as a diff."""

    def wrapped(text: str) -> dict:
        try:
            return fn(text)
        except Exception as ex:  # noqa: BLE001
            return {"probe error": type(ex).__name__}

    return wrapped
ESLINT_REAL = r'''[{"filePath":"/home/dev/app/src/a.js","messages":[{"ruleId":"prefer-const","severity":2,"message":"'data' is never reassigned. Use 'const' instead.","line":3,"column":7,"messageId":"useConst","endLine":3,"endColumn":11,"fix":{"range":[50,82],"text":"const data = fs.readFileSync(path)"}},{"ruleId":"semi","severity":1,"message":"Missing semicolon.","line":3,"column":35,"messageId":"missingSemi","endLine":4,"endColumn":1,"fix":{"range":[82,82],"text":";"}},{"ruleId":"eqeqeq","severity":1,"message":"Expected '===' and instead saw '=='.","line":4,"column":12,"messageId":"unexpected","endLine":4,"endColumn":14,"suggestions":[{"messageId":"replaceOperator","data":{"expectedOperator":"===","actualOperator":"=="},"fix":{"range":[94,96],"text":"==="},"desc":"Use '===' instead of '=='."}]},{"ruleId":"no-undef","severity":2,"message":"'console' is not defined.","line":5,"column":5,"messageId":"undef","endLine":5,"endColumn":12},{"ruleId":"no-console","severity":1,"message":"Unexpected console statement.","line":5,"column":5,"messageId":"unexpected","endLine":5,"endColumn":16,"suggestions":[{"fix":{"range":[109,136],"text":""},"messageId":"removeConsole","data":{"propertyName":"log"},"desc":"Remove the console.log()."}]}],"suppressedMessages":[],"errorCount":2,"fatalErrorCount":0,"warningCount":3,"fixableErrorCount":1,"fixableWarningCount":1,"source":"const fs = require(\"fs\");\nfunction load(path) {\n  let data = fs.readFileSync(path)\n  if (data == null) {\n    console.log(\"empty\", path);\n  }\n  return data;\n}\nmodule.exports = { load };\n","usedDeprecatedRules":[{"ruleId":"semi","replacedBy":["@stylistic/semi"],"info":{"message":"Formatting rules are being moved out of ESLint core.","url":"https://eslint.org/blog/2023/10/deprecating-formatting-rules/","deprecatedSince":"8.53.0","availableUntil":"11.0.0","replacedBy":[{"message":"ESLint Stylistic now maintains deprecated stylistic core rules.","url":"https://eslint.style/guide/migration","plugin":{"name":"@stylistic/eslint-plugin","url":"https://eslint.style"},"rule":{"name":"semi","url":"https://eslint.style/rules/semi"}}]}}]},{"filePath":"/home/dev/app/src/b.js","messages":[{"ruleId":"no-undef","severity":2,"message":"'taxRate' is not defined.","line":6,"column":16,"messageId":"undef","endLine":6,"endColumn":23}],"suppressedMessages":[],"errorCount":1,"fatalErrorCount":0,"warningCount":0,"fixableErrorCount":0,"fixableWarningCount":0,"source":"function total(items) {\n  var sum = 0;\n  for (var i = 0; i < items.length; i++) {\n    sum += items[i].price * items[i].qty;\n  }\n  return sum + taxRate;\n}\nmodule.exports = total;\n","usedDeprecatedRules":[{"ruleId":"semi","replacedBy":["@stylistic/semi"],"info":{"message":"Formatting rules are being moved out of ESLint core.","url":"https://eslint.org/blog/2023/10/deprecating-formatting-rules/","deprecatedSince":"8.53.0","availableUntil":"11.0.0","replacedBy":[{"message":"ESLint Stylistic now maintains deprecated stylistic core rules.","url":"https://eslint.style/guide/migration","plugin":{"name":"@stylistic/eslint-plugin","url":"https://eslint.style"},"rule":{"name":"semi","url":"https://eslint.style/rules/semi"}}]}}]},{"filePath":"/home/dev/app/src/c.js","messages":[{"ruleId":"no-unused-vars","severity":2,"message":"'unused' is assigned a value but never used.","line":1,"column":7,"messageId":"unusedVar","endLine":1,"endColumn":13,"suggestions":[{"messageId":"removeVar","data":{"varName":"unused"},"fix":{"range":[0,17],"text":""},"desc":"Remove unused variable 'unused'."}]},{"ruleId":"eqeqeq","severity":1,"message":"Expected '===' and instead saw '=='.","line":3,"column":7,"messageId":"unexpected","endLine":3,"endColumn":9,"suggestions":[{"messageId":"replaceOperator","data":{"expectedOperator":"===","actualOperator":"=="},"fix":{"range":[37,39],"text":"==="},"desc":"Use '===' instead of '=='."}]},{"ruleId":"no-undef","severity":2,"message":"'console' is not defined.","line":3,"column":15,"messageId":"undef","endLine":3,"endColumn":22},{"ruleId":"no-console","severity":1,"message":"Unexpected console statement.","line":3,"column":15,"messageId":"unexpected","endLine":3,"endColumn":28,"suggestions":[{"fix":{"range":[45,68],"text":""},"messageId":"removeConsole","data":{"propertyName":"error"},"desc":"Remove the console.error()."}]},{"ruleId":"no-unused-vars","severity":2,"message":"'f' is defined but never used.","line":4,"column":10,"messageId":"unusedVar","endLine":4,"endColumn":11,"suggestions":[{"messageId":"removeVar","data":{"varName":"f"},"fix":{"range":[71,97],"text":""},"desc":"Remove unused variable 'f'."}]},{"ruleId":"no-undef","severity":2,"message":"'y' is not defined.","line":4,"column":23,"messageId":"undef","endLine":4,"endColumn":24}],"suppressedMessages":[],"errorCount":4,"fatalErrorCount":0,"warningCount":2,"fixableErrorCount":0,"fixableWarningCount":0,"source":"const unused = 1;\nconst x = 2;\nif (x == 3) { console.error(\"three\"); }\nfunction f() { return y; }\n","usedDeprecatedRules":[{"ruleId":"semi","replacedBy":["@stylistic/semi"],"info":{"message":"Formatting rules are being moved out of ESLint core.","url":"https://eslint.org/blog/2023/10/deprecating-formatting-rules/","deprecatedSince":"8.53.0","availableUntil":"11.0.0","replacedBy":[{"message":"ESLint Stylistic now maintains deprecated stylistic core rules.","url":"https://eslint.style/guide/migration","plugin":{"name":"@stylistic/eslint-plugin","url":"https://eslint.style"},"rule":{"name":"semi","url":"https://eslint.style/rules/semi"}}]}}]},{"filePath":"/home/dev/app/src/clean.js","messages":[],"suppressedMessages":[],"errorCount":0,"fatalErrorCount":0,"warningCount":0,"fixableErrorCount":0,"fixableWarningCount":0,"usedDeprecatedRules":[{"ruleId":"semi","replacedBy":["@stylistic/semi"],"info":{"message":"Formatting rules are being moved out of ESLint core.","url":"https://eslint.org/blog/2023/10/deprecating-formatting-rules/","deprecatedSince":"8.53.0","availableUntil":"11.0.0","replacedBy":[{"message":"ESLint Stylistic now maintains deprecated stylistic core rules.","url":"https://eslint.style/guide/migration","plugin":{"name":"@stylistic/eslint-plugin","url":"https://eslint.style"},"rule":{"name":"semi","url":"https://eslint.style/rules/semi"}}]}}]},{"filePath":"/home/dev/app/src/eslint.config.js","messages":[],"suppressedMessages":[],"errorCount":0,"fatalErrorCount":0,"warningCount":0,"fixableErrorCount":0,"fixableWarningCount":0,"usedDeprecatedRules":[{"ruleId":"semi","replacedBy":["@stylistic/semi"],"info":{"message":"Formatting rules are being moved out of ESLint core.","url":"https://eslint.org/blog/2023/10/deprecating-formatting-rules/","deprecatedSince":"8.53.0","availableUntil":"11.0.0","replacedBy":[{"message":"ESLint Stylistic now maintains deprecated stylistic core rules.","url":"https://eslint.style/guide/migration","plugin":{"name":"@stylistic/eslint-plugin","url":"https://eslint.style"},"rule":{"name":"semi","url":"https://eslint.style/rules/semi"}}]}}]}]'''  # captured: eslint 10.1.0 -f json on four small files (paths shortened)


# ===============================================================================================================
# #120 Per-tool key pruning for CLI JSON reporters (eslint, jest, webpack, npm audit).
# The encoder recognises the tool by the shape of the document and removes a per-tool blocklist of keys at the paths where
# they are noise. Anything it does not recognise, and any document where nothing is removable, comes back untouched.

ESL_FILE_DROP = ("source", "usedDeprecatedRules")
ESL_MSG_DROP = ("messageId", "nodeType", "fix", "suggestions")
JEST_CASE_DROP = ("fullName", "failureDetails", "location", "invocations", "retryReasons", "numPassingAsserts")
JEST_SUITE_DROP = ("startTime", "endTime", "summary")
WP_MODULE_DROP = ("reasons", "issuerPath", "issuer", "issuerId", "identifier", "profile", "optional", "prefetched",
                  "cacheable", "built", "index", "index2", "depth", "usedExports", "providedExports", "optimizationBailout")
AUDIT_VULN_DROP = ("nodes", "effects")
AUDIT_VIA_DROP = ("cwe", "cvss", "source")


def _pop(d: dict, keys, only_if=None) -> int:
    n = 0
    for k in keys:
        if k in d and (only_if is None or only_if(k, d[k])):
            del d[k]
            n += 1
    return n


def _wp_modules(node, depth=0) -> int:
    """Strip module noise from every module dict, wherever stats nest modules (chunks, children, concatenated modules)."""
    n = 0
    if depth > 40:
        return 0
    if isinstance(node, dict):
        if "identifier" in node and "name" in node:
            n += _pop(node, WP_MODULE_DROP)
        for v in node.values():
            n += _wp_modules(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            n += _wp_modules(v, depth + 1)
    return n


@total
def prune_cli_json(text: str) -> str:
    o = jload(text)
    if o is _BAD:
        return text
    n = 0
    if isinstance(o, list) and o and all(isinstance(f, dict) and "filePath" in f and isinstance(f.get("messages"), list) for f in o):
        for f in o:  # eslint -f json
            n += _pop(f, ESL_FILE_DROP)
            n += _pop(f, ("suppressedMessages",), lambda k, v: v == [])
            for m in f["messages"]:
                if isinstance(m, dict):
                    n += _pop(m, ESL_MSG_DROP)
    elif isinstance(o, dict) and isinstance(o.get("testResults"), list) and "numTotalTests" in o:
        n += _pop(o, ("openHandles", "snapshot"), lambda k, v: v == [] or k == "snapshot")  # jest --json
        for s in o["testResults"]:
            if not isinstance(s, dict):
                continue
            cases = [a for a in s.get("assertionResults", []) if isinstance(a, dict)]
            for a in cases:
                n += _pop(a, JEST_CASE_DROP)
            n += _pop(s, JEST_SUITE_DROP)
            if any(a.get("failureMessages") for a in cases):  # the suite message repeats them; a suite that never ran keeps it
                n += _pop(s, ("message",))
    elif isinstance(o, dict) and isinstance(o.get("modules"), list) and "assets" in o:
        n += _wp_modules(o)  # webpack --json
    elif isinstance(o, dict) and "auditReportVersion" in o and isinstance(o.get("vulnerabilities"), dict):
        for v in o["vulnerabilities"].values():  # npm audit --json
            if isinstance(v, dict):
                n += _pop(v, AUDIT_VULN_DROP)
                for via in v.get("via", []):
                    if isinstance(via, dict):
                        n += _pop(via, AUDIT_VIA_DROP)
    if not n:
        return text
    return gate(text, jc(o))


def _walk_dicts(o, key):
    """All values stored under `key`, at any depth."""
    stack = [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k == key:
                    yield v
                stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)


def probes_120(text: str) -> dict:
    o = jload(text)
    if o is _BAD:
        return {"tool": "unparsable"}
    if isinstance(o, list) and o and all(isinstance(f, dict) and "filePath" in f for f in o):
        problems = [(f["filePath"], m.get("line"), m.get("column"), m.get("ruleId"), m.get("severity"), m.get("message"), bool(m.get("fatal")))
                    for f in o for m in f.get("messages", [])]
        counts = [(f["filePath"], f.get("errorCount"), f.get("warningCount"), f.get("fatalErrorCount")) for f in o]
        return {"tool": "eslint", "every problem": problems, "per-file counts": counts}
    if isinstance(o, dict) and "testResults" in o:
        tests, never_ran = [], []
        for s in o["testResults"]:
            ars = s.get("assertionResults", [])
            for a in ars:
                tests.append((s.get("name"), " ".join(a.get("ancestorTitles", []) + [a.get("title")]), a.get("status"), a.get("failureMessages")))
            if s.get("status") == "failed" and not any(a.get("failureMessages") for a in ars):
                never_ran.append((s.get("name"), s.get("message")))
        tops = {k: v for k, v in o.items() if k.startswith("num") or k == "success"}
        return {"tool": "jest", "test -> status, failure": tests, "suites that failed to run": never_ran, "totals": tops}
    if isinstance(o, dict) and "assets" in o and "modules" in o:
        mods = sorted((m.get("name"), m.get("size"), m.get("id"), m.get("failed"), m.get("errors"), m.get("warnings"))
                      for lst in _walk_dicts(o, "modules") if isinstance(lst, list) for m in lst if isinstance(m, dict))
        return {"tool": "webpack", "assets": [(a.get("name"), a.get("size")) for a in o["assets"]], "modules": mods,
                "errors": o.get("errors"), "warnings": o.get("warnings"), "hash": o.get("hash")}
    if isinstance(o, dict) and "vulnerabilities" in o:
        vs = {n: (v.get("severity"), v.get("range"), v.get("fixAvailable"), v.get("isDirect"),
                  sorted(x if isinstance(x, str) else (x.get("title"), x.get("url"), x.get("severity"), x.get("range")) for x in v.get("via", [])
                         if isinstance(x, str) or isinstance(x, dict)) if all(isinstance(x, (str, dict)) for x in v.get("via", [])) else None)
              for n, v in o["vulnerabilities"].items()}
        return {"tool": "npm audit", "vulnerabilities": vs, "totals": o.get("metadata", {}).get("vulnerabilities")}
    return {"tool": "other"}


def _eslint_big() -> str:
    base = json.loads(ESLINT_REAL)
    seeds = [f for f in base if f["messages"]]
    files = []
    dirs = ["api", "ui", "db", "auth", "cache", "jobs", "util"]
    for i in range(14):
        f = json.loads(json.dumps(seeds[i % len(seeds)]))
        f["filePath"] = f"/home/dev/app/src/{dirs[i % 7]}/mod{i:02d}.js"
        for m in f["messages"]:
            m["line"] += i
            if "endLine" in m:
                m["endLine"] += i
        f["source"] = f["source"] * 7  # real files are longer than the captured samples
        files.append(f)

    def add(f, rule, sev, msg, line, col, **extra):
        f["messages"].append({"ruleId": rule, "severity": sev, "message": msg, "line": line, "column": col, "nodeType": "CallExpression",
                              "messageId": "unexpected", "endLine": line, "endColumn": col + 5, **extra})

    add(files[0], "no-eval", 2, "eval can be harmful.", 41, 3)  # start
    add(files[7], "no-restricted-syntax", 2, "Do not use with statements.", 88, 5)  # middle
    files[13]["messages"] = [{"ruleId": None, "fatal": True, "severity": 2, "message": "Parsing error: Unexpected token }", "line": 9, "column": 1, "nodeType": None}]
    for f in files:
        f["errorCount"] = sum(m["severity"] == 2 for m in f["messages"])
        f["warningCount"] = sum(m["severity"] == 1 for m in f["messages"])
        f["fatalErrorCount"] = sum(bool(m.get("fatal")) for m in f["messages"])
    files[13]["source"] = files[13]["source"] + "}\n"  # end: the file that does not parse
    return jc(files)


ESLINT_BIG = _eslint_big()


def _jest_run() -> str:
    suites = []
    names = ["auth/login", "auth/session", "cart/totals", "cart/discount", "orders/create", "orders/refund", "search/index", "search/rank", "ui/Button"]
    for i, nm in enumerate(names):
        cases = []
        for j in range(5 + i % 3):
            fail = (i, j) in {(0, 1), (4, 2), (8, 0)}  # first, middle and last suites hold failures
            title = f"handles case {j} of {nm.split('/')[1]}"
            anc = ["describe " + nm.split("/")[0], nm.split("/")[1]]
            msg = [f"\u001b[31mError: expect(received).toBe(expected)\u001b[39m\n\nExpected: \u001b[32m{j + 2}\u001b[39m\nReceived: \u001b[31m{j + 1}\u001b[39m\n    at Object.<anonymous> (/home/dev/app/src/{nm}.test.js:{20 + j * 7}:31)"] if fail else []
            cases.append({"ancestorTitles": anc, "duration": 3 + j, "failureDetails": [{"matcherResult": {"actual": j + 1, "expected": j + 2, "message": "..", "name": "toBe", "pass": False}}] if fail else [],
                          "failureMessages": msg, "fullName": " ".join(anc + [title]), "invocations": 1, "location": None, "numPassingAsserts": 0 if fail else j + 1,
                          "retryReasons": [], "status": "failed" if fail else "passed", "title": title})
        s = {"assertionResults": cases, "endTime": 1730000001000 + i * 40, "message": "", "name": f"/home/dev/app/src/{nm}.test.js", "startTime": 1730000000900 + i * 40,
             "status": "passed", "summary": ""}
        if any(c["failureMessages"] for c in cases):
            s["status"] = "failed"
            s["message"] = "".join(f"  ● {c['fullName']}\n\n    {c['failureMessages'][0]}\n\n" for c in cases if c["failureMessages"])
        if nm == "search/index":  # middle: a suite that cannot even run keeps its only explanation in `message`
            s.update(assertionResults=[], status="failed", message="  ● Test suite failed to run\n\n    Cannot find module './tokenizer' from 'src/search/index.js'\n\n      at Resolver.resolveModule (node_modules/jest-resolve/build/resolver.js:324:11)")
        suites.append(s)
    tot = sum(len(s["assertionResults"]) for s in suites)
    nfail = sum(c["status"] == "failed" for s in suites for c in s["assertionResults"])
    return jc({"numFailedTestSuites": 4, "numFailedTests": nfail, "numPassedTestSuites": 5, "numPassedTests": tot - nfail, "numPendingTestSuites": 0, "numPendingTests": 0,
               "numRuntimeErrorTestSuites": 1, "numTodoTests": 0, "numTotalTestSuites": 9, "numTotalTests": tot,
               "openHandles": [], "snapshot": {"added": 0, "didUpdate": False, "failure": False, "filesAdded": 0, "filesRemoved": 0, "filesRemovedList": [], "filesUnmatched": 0,
                                                "filesUpdated": 0, "matched": 0, "total": 0, "unchecked": 0, "uncheckedKeysByFile": [], "unmatched": 0, "updated": 0},
               "startTime": 1730000000000, "success": False, "testResults": suites, "wasInterrupted": False})


def _webpack_stats() -> str:
    mods = []
    libs = ["react", "react-dom", "lodash", "axios", "redux", "scheduler", "object-assign", "prop-types"]
    for i in range(48):
        lib = libs[i % len(libs)]
        nm = f"./node_modules/{lib}/lib/m{i}.js" if i % 4 else f"./src/components/C{i}.jsx"
        iss = "./src/index.js" if i < 6 else f"./src/components/C{(i - 1) // 4 * 4}.jsx"
        ident = "/home/dev/app/" + nm[2:]
        mods.append({"id": i + 10, "identifier": ident, "name": nm, "index": i, "index2": i, "size": 400 + i * 37, "cacheable": True, "built": True, "optional": False,
                     "prefetched": False, "chunks": [0], "issuer": "/home/dev/app/" + iss[2:], "issuerId": 3, "issuerName": iss,
                     "issuerPath": [{"id": 0, "identifier": "/home/dev/app/src/index.js", "name": "./src/index.js", "profile": {"factory": 3, "building": 20}},
                                    {"id": 3, "identifier": "/home/dev/app/" + iss[2:], "name": iss, "profile": {"factory": 2, "building": 11}}],
                     "profile": {"factory": 2, "building": 9}, "failed": i == 47, "errors": 1 if i == 47 else 0, "warnings": 1 if i == 0 else 0, "assets": [],
                     "reasons": [{"moduleId": 3, "moduleIdentifier": "/home/dev/app/" + iss[2:], "module": iss, "moduleName": iss, "type": "cjs require", "explanation": "",
                                  "userRequest": lib, "loc": f"{i + 1}:0-{20 + i}"}, {"moduleId": 0, "moduleIdentifier": "/home/dev/app/src/index.js", "module": "./src/index.js",
                                                                                      "moduleName": "./src/index.js", "type": "harmony side effect evaluation", "explanation": "", "userRequest": lib, "loc": f"{i}:0-9"}],
                     "usedExports": True, "providedExports": None, "optimizationBailout": [], "depth": 1 + i % 3})
    return jc({"errors": ["./src/components/C47.jsx\nModule not found: Error: Can't resolve './missing' in '/home/dev/app/src/components'"],
               "warnings": ["./src/index.js\nexport 'default' (imported as 'legacy') was not found in './legacy'"], "assets": [{"name": "main.js", "size": 142870, "chunks": [0], "chunkNames": ["main"], "emitted": True},
                                                                                      {"name": "vendor.js", "size": 802113, "chunks": [1], "chunkNames": ["vendor"], "emitted": True}],
               "modules": mods, "hash": "5c0a1d6b9e2f4a7c8d3e", "version": "5.90.3", "time": 8213, "outputPath": "/home/dev/app/dist"})


def _npm_audit() -> str:
    vs = {}
    for i, (name, sev, rng_) in enumerate([("lodash", "high", "<4.17.21"), ("minimist", "critical", "<1.2.6"), ("axios", "moderate", "<0.21.2"), ("semver", "high", "<5.7.2"),
                                           ("tough-cookie", "moderate", "<4.1.3"), ("word-wrap", "moderate", "<1.2.4"), ("request", "moderate", "*"), ("ws", "high", "7.0.0 - 7.5.9"),
                                           ("node-fetch", "high", "<2.6.7")]):
        vs[name] = {"name": name, "severity": sev, "isDirect": i % 2 == 0,
                    "via": [{"source": 1090000 + i, "name": name, "dependency": name, "title": f"{name} advisory {i}: prototype pollution", "url": f"https://github.com/advisories/GHSA-{i:04d}-abcd-efgh",
                             "severity": sev, "cwe": ["CWE-1321", "CWE-20"], "cvss": {"score": 7.5 + i / 10, "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N"}, "range": rng_}],
                    "effects": [f"dep-{i}-a", f"dep-{i}-b"], "range": rng_, "nodes": [f"node_modules/{name}", f"node_modules/app/node_modules/{name}"],
                    "fixAvailable": {"name": name, "version": f"9.{i}.0", "isSemVerMajor": i % 3 == 0} if i % 4 else False}
    return jp({"auditReportVersion": 2, "vulnerabilities": vs, "metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 4, "high": 4, "critical": 1, "total": 9},
                                                                           "dependencies": {"prod": 412, "dev": 1033, "optional": 14, "peer": 0, "peerOptional": 0, "total": 1459}}})


ESLINT_PRUNED = prune_cli_json(ESLINT_BIG)
PACKAGE_JSON = jp({"name": "app", "version": "1.2.3", "scripts": {"build": "webpack", "lint": "eslint ."}, "dependencies": {"react": "^18.2.0", "lodash": "^4.17.21"}})

case(
    120,
    claim="Recognises eslint -f json, jest --json, webpack --json and npm audit --json by shape and removes per-tool noise keys (eslint source/usedDeprecatedRules/fix/suggestions/messageId/nodeType, jest fullName/failureDetails/location/duplicate suite message, webpack reasons/issuerPath/identifier, npm audit cwe/cvss/nodes); every problem with file:line:col:rule:message, every test with status and failure text, a suite that failed to run, all modules, assets, errors and totals are unchanged.",
    loses="eslint autofix text and suggestion ranges, the full source of each file, rule messageIds and deprecation notices; jest duplicate fullName, failureDetails matcher objects, per-suite timestamps and the aggregated suite message when per-test failures exist; webpack module reasons, issuer chains and profiling; npm audit CWE and CVSS vectors, install paths and the effects list; whitespace of pretty-printed input",
    encode=prune_cli_json,
    probes=safe_probes(probes_120),
    fixtures=lambda: [
        Fixture("eslint -f json captured (5 files)", ESLINT_REAL, True),
        Fixture("eslint, 14 files: no-eval first, restricted syntax in the middle, parse error last", ESLINT_BIG, True),
        Fixture("jest --json: failures in first, middle (suite cannot run) and last suite", _jest_run(), True),
        Fixture("webpack --json: 48 modules, a failed one last", _webpack_stats(), True),
        Fixture("npm audit --json, pretty printed", _npm_audit(), True),
        Fixture("eslint output already pruned", ESLINT_PRUNED, False),
        Fixture("package.json is not a reporter", PACKAGE_JSON, False),
        Fixture("eslint json with a 5000-digit number (json.loads refuses it)", ESLINT_REAL[:-1] + ',{"filePath":"x.js","messages":[],"big":' + "7" * 5000 + "}]", None),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #122 Extract the plaintext traceback from framework debug HTML pages (Django traceback_area, Werkzeug div.plain).
# Rule: the page is replaced by one header line plus the unescaped textarea. Probes read the original with html.parser and
# the encoding as text, then run the same small traceback state machine over both, so the extraction is checked, not shared.

TB_HEAD = "[ctxprune: "
_TEXTAREA = re.compile(r"<textarea\b([^>]*)>(.*?)</textarea>", re.S)


@total
def extract_traceback(text: str) -> str:
    if text.startswith(TB_HEAD) or "<textarea" not in text:
        return text
    for tag in ("script", "style", "textarea"):  # raw-text elements must open and close in turn, or a real HTML parser swallows the textarea
        depth = 0
        for m in re.finditer(r"<(/?)" + tag + r"\b", text):
            depth += -1 if m.group(1) else 1
            if depth not in (0, 1):
                return text
        if depth:
            return text
    kind, hits = "Django", [m for m in _TEXTAREA.finditer(text) if 'id="traceback_area"' in m.group(1)]
    if not hits:
        i = text.find('<div class="plain">')
        m = _TEXTAREA.search(text, i) if i >= 0 else None
        if m and "<div" in text[i + 5:m.start()] or m and "</div>" in text[i:m.start()]:
            m = None  # the textarea must sit directly inside the plain div
        kind, hits = "Werkzeug", [m] if m else []
    if len(hits) != 1 or "<" in hits[0].group(2):  # more than one candidate, or markup inside: not a page we understand
        return text
    tb = html.unescape(hits[0].group(2)).strip("\n")
    if not tb.strip():
        return text
    head = f"{TB_HEAD}{kind} debug page, {len(text):,} chars -> its plaintext traceback; request info, locals, settings and markup dropped]\n"
    return gate(text, head + tb + "\n")


class _TbFinder(HTMLParser):
    """Collects the text of the traceback textarea: id=traceback_area, or any textarea inside <div class="plain">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self._cur = None
        self._plain_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            if self._plain_depth or a.get("class") == "plain":
                self._plain_depth += 1
        elif tag == "textarea" and (a.get("id") == "traceback_area" or self._plain_depth):
            self._cur = []

    def handle_endtag(self, tag):
        if tag == "div" and self._plain_depth:
            self._plain_depth -= 1
        elif tag == "textarea" and self._cur is not None:
            self.found.append("".join(self._cur))
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur.append(data)


def _tb_text(text: str):
    if text.startswith(TB_HEAD):
        return text.split("\n", 1)[1] if "\n" in text else ""
    if "<textarea" not in text:
        return None
    p = _TbFinder()
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001
        return None
    return p.found[0].strip("\n") if len(p.found) == 1 else None


def probes_122(text: str) -> dict:
    tb = _tb_text(text)
    if tb is None:
        return {"traceback": None}
    frames, facts, last = [], {}, ""
    for line in tb.splitlines():
        m = re.match(r'\s*File "(.*)", line (\d+), in (.*)$', line)
        if m:
            frames.append((m.group(1), int(m.group(2)), m.group(3)))
        for key in ("Request Method", "Request URL", "Django Version", "Python Version", "Exception Type", "Exception Value"):
            if line.startswith(key + ":"):
                facts[key] = line.split(":", 1)[1].strip()
        if line.startswith("In template ") or line.startswith("Template error"):
            facts.setdefault("template", []).append(line.strip())
        if line.strip():
            last = line.strip()
    return {"frames (file, line, function)": frames, "innermost frame": frames[-1] if frames else None, "facts": facts,
            "last line": last, "chained exceptions": tb.count("During handling of the above exception") + tb.count("The above exception was the direct cause")}


_DJ_SETTINGS = ("ABSOLUTE_URL_OVERRIDES ADMINS ALLOWED_HOSTS APPEND_SLASH AUTHENTICATION_BACKENDS AUTH_PASSWORD_VALIDATORS AUTH_USER_MODEL CACHES CACHE_MIDDLEWARE_ALIAS "
                "CACHE_MIDDLEWARE_KEY_PREFIX CACHE_MIDDLEWARE_SECONDS CSRF_COOKIE_AGE CSRF_COOKIE_DOMAIN CSRF_COOKIE_HTTPONLY CSRF_COOKIE_NAME CSRF_COOKIE_PATH CSRF_COOKIE_SAMESITE "
                "CSRF_COOKIE_SECURE CSRF_FAILURE_VIEW CSRF_HEADER_NAME CSRF_TRUSTED_ORIGINS CSRF_USE_SESSIONS DATABASES DATABASE_ROUTERS DATA_UPLOAD_MAX_MEMORY_SIZE DATETIME_FORMAT "
                "DEBUG DEBUG_PROPAGATE_EXCEPTIONS DEFAULT_AUTO_FIELD DEFAULT_CHARSET DEFAULT_EXCEPTION_REPORTER DEFAULT_FILE_STORAGE DEFAULT_FROM_EMAIL DISALLOWED_USER_AGENTS EMAIL_BACKEND "
                "EMAIL_HOST EMAIL_HOST_PASSWORD EMAIL_HOST_USER EMAIL_PORT FILE_UPLOAD_HANDLERS FIRST_DAY_OF_WEEK FORM_RENDERER INSTALLED_APPS INTERNAL_IPS LANGUAGE_CODE LOGGING "
                "LOGIN_REDIRECT_URL LOGIN_URL MEDIA_ROOT MEDIA_URL MIDDLEWARE ROOT_URLCONF SECRET_KEY SECURE_HSTS_SECONDS SESSION_COOKIE_AGE SESSION_ENGINE SETTINGS_MODULE STATIC_ROOT "
                "STATIC_URL TEMPLATES TIME_ZONE USE_I18N USE_TZ WSGI_APPLICATION").split()
_DJ_META = ("COLORTERM CONTENT_LENGTH CONTENT_TYPE DJANGO_SETTINGS_MODULE GATEWAY_INTERFACE HOME HTTP_ACCEPT HTTP_ACCEPT_ENCODING HTTP_ACCEPT_LANGUAGE HTTP_CONNECTION HTTP_COOKIE HTTP_HOST "
            "HTTP_UPGRADE_INSECURE_REQUESTS HTTP_USER_AGENT LANG LOGNAME PATH PATH_INFO PWD QUERY_STRING REMOTE_ADDR REMOTE_HOST REQUEST_METHOD RUN_MAIN SCRIPT_NAME SERVER_NAME SERVER_PORT "
            "SERVER_PROTOCOL SERVER_SOFTWARE SHELL TERM TZ USER VIRTUAL_ENV wsgi.errors wsgi.input wsgi.multiprocess wsgi.multithread wsgi.run_once wsgi.url_scheme wsgi.version").split()
_DJ_CSS = "".join(f"#{n} {{ margin: {i % 7}px; padding: {i % 5}px {i % 11}px; border-bottom: 1px solid #{i % 10}{i % 10}{i % 10}; }}\n" for i, n in enumerate(
    ["summary", "traceback", "requestinfo", "explanation", "template-info", "browserTraceback", "pastebinTraceback"] * 14))
_DJ_JS = "function hideAll(elems) { for (var e = 0; e < elems.length; e++) { elems[e].style.display = 'none'; } }\n" * 18


def _esc(s: str) -> str:
    return html.escape(s, quote=True).replace("&#x27;", "&#x27;")


def _django_page(exc_type: str, exc_val: str, url: str, frames: list[tuple[str, int, str, str]], chained: str = "", template_err: str = "", pastebin_first: bool = False) -> str:
    def tb_text() -> str:
        parts = ["Environment:\n\n\nRequest Method: GET\nRequest URL: " + url, "\nDjango Version: 4.2.7\nPython Version: 3.11.6\nInstalled Applications:\n['django.contrib.admin',\n 'django.contrib.auth',\n 'orders']\nInstalled Middleware:\n['django.middleware.security.SecurityMiddleware',\n 'django.contrib.sessions.middleware.SessionMiddleware']\n\n"]
        if template_err:
            parts.append(template_err + "\n")
        parts.append("Traceback (most recent call last):\n")
        for f, ln, fn, code in frames:
            parts.append(f'  File "{f}", line {ln}, in {fn}\n    {code}\n')
        if chained:
            parts.append(f"\nDuring handling of the above exception ({chained}), another exception occurred:\n\n")
            f, ln, fn, code = frames[-1]
            parts.append(f'Traceback (most recent call last):\n  File "{f}", line {ln + 9}, in {fn}\n    raise\n')
        parts.append(f"\nException Type: {exc_type} at {url.split('8000')[1]}\nException Value: {exc_val}\n")
        return "".join(parts)

    frames_html = ""
    for k, (f, ln, fn, code) in enumerate(frames):
        pre = "".join(f'<li onclick="toggle(this)"><code>    line {ln - 3 + j} of context for {fn}()</code></li>' for j in range(3))
        post = "".join(f'<li onclick="toggle(this)"><code>    line {ln + 1 + j} of context after {fn}()</code></li>' for j in range(3))
        locs = "".join(f'<tr><td>{n}</td><td class="code"><pre>{_esc(v)}</pre></td></tr>' for n, v in
                       [("request", "<WSGIRequest: GET '" + url.split("8000")[1] + "'>"), ("self", f"<orders.views.View object at 0x7f{k:02d}a1b2c3d4>"), ("kwargs", "{}"), ("args", "()")])
        frames_html += (f'<li class="frame user"><code>{f}</code>, line {ln}, in {fn}<div class="context" id="c{k}"><ol start="{ln - 3}" class="pre-context" id="pre{k}">{pre}</ol>'
                        f'<ol start="{ln}" class="context-line"><li onclick="toggle(this)"><code>    {_esc(code)}</code> <span>&#8226;&#8226;&#8226;</span></li></ol>'
                        f'<ol start="{ln + 1}" class="post-context" id="post{k}">{post}</ol></div><details><summary>Local vars</summary><table class="vars" id="v{k}">'
                        f"<thead><tr><th>Variable</th><th>Value</th></tr></thead><tbody>{locs}</tbody></table></details></li>\n")
    pasteform = (f'<form action="https://dpaste.com/" name="pasteform" id="pasteform" method="post"><div id="pastebinTraceback" class="pastebin">'
                 f'<input type="hidden" name="language" value="PythonConsole"><input type="hidden" name="title" value="{exc_type} at {url.split("8000")[1]}">'
                 f'<input type="hidden" name="source" value="Django Dpaste Agent"><input type="hidden" name="poster" value="Django">'
                 f'<textarea name="content" id="traceback_area" cols="140" rows="25">{_esc(tb_text())}</textarea><br><br><input type="submit" value="Share this traceback on a public website"></div></form>')
    meta = "".join(f'<tr><td>{k}</td><td class="code"><pre>{_esc(repr("x" * (3 + i % 40)))}</pre></td></tr>' for i, k in enumerate(_DJ_META))
    sett = "".join(f'<tr><td>{k}</td><td class="code"><pre>{"&#x27;********************&#x27;" if "KEY" in k or "PASSWORD" in k else _esc(repr(["a", "b"] * (1 + i % 5)))}</pre></td></tr>' for i, k in enumerate(_DJ_SETTINGS))
    summary = (f'<div id="summary"><h1>{exc_type} at {url.split("8000")[1]}</h1><pre class="exception_value">{_esc(exc_val)}</pre><table class="meta"><tr><th>Request Method:</th><td>GET</td></tr>'
               f"<tr><th>Request URL:</th><td>{url}</td></tr><tr><th>Django Version:</th><td>4.2.7</td></tr><tr><th>Exception Type:</th><td>{exc_type}</td></tr></table></div>")
    page = (f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta http-equiv="content-type" content="text/html; charset=utf-8"><meta name="robots" content="NONE,NOARCHIVE">'
            f'<title>{exc_type} at {url.split("8000")[1]}</title>\n<style type="text/css">\n{_DJ_CSS}</style>\n<script>\n{_DJ_JS}</script>\n</head>\n<body>\n' + (pasteform if pastebin_first else "") + summary +
            f'<div id="traceback"><h2>Traceback <a href="#" onclick="return switchPastebinFriendly(this);">Switch to copy-and-paste view</a></h2><div id="browserTraceback"><ul class="traceback">\n{frames_html}</ul></div>' +
            ("" if pastebin_first else pasteform) + f'</div>\n<div id="requestinfo"><h2>Request information</h2><h3 id="meta-info">META</h3><table class="req"><thead><tr><th>Variable</th><th>Value</th></tr></thead><tbody>{meta}</tbody></table>'
            f'<h3 id="settings-info">Settings</h3><p>Using settings module <code>app.settings</code></p><table class="req"><tbody>{sett}</tbody></table></div>'
            '<div id="explanation"><p>You\'re seeing this error because you have <code>DEBUG = True</code> in your Django settings file. Change that to <code>False</code>, and Django will display a standard page.</p></div></body></html>\n')
    return page


_DJ_FRAMES = [("/usr/lib/python3.11/site-packages/django/core/handlers/exception.py", 55, "inner", "response = get_response(request)"),
              ("/usr/lib/python3.11/site-packages/django/core/handlers/base.py", 197, "_get_response", "response = wrapped_callback(request, *callback_args, **callback_kwargs)"),
              ("/srv/app/orders/views.py", 42, "orders_list", 'user_id = request.session["user_id"]'),
              ("/usr/lib/python3.11/site-packages/django/contrib/sessions/backends/base.py", 53, "__getitem__", "return self._session[key]")]


def _werkzeug_page() -> str:
    frames = [("/usr/lib/python3/site-packages/flask/app.py", 1455, "wsgi_app", "response = self.full_dispatch_request()"),
              ("/usr/lib/python3/site-packages/flask/app.py", 869, "full_dispatch_request", "rv = self.handle_user_exception(e)"),
              ("/srv/app/api.py", 31, "get_user", "return jsonify(USERS[request.args['id']])")]
    plain = "Traceback (most recent call last):\n" + "".join(f'  File "{f}", line {ln}, in {fn}\n    {code}\n' for f, ln, fn, code in frames) + "KeyError: '7'\n"
    fr = "".join(f'<li><div class="frame" id="frame-{140245 + i}"><h4>File <cite class="filename">"{f}"</cite>, line <em class="line">{ln}</em>, in <code class="function">{fn}</code></h4>'
                 f'<div class="source library"><pre class="line before"><span class="ws">    </span>{_esc(code)}</pre><pre class="line current"><span class="ws">    </span>{_esc(code)}</pre></div></div></li>\n' for i, (f, ln, fn, code) in enumerate(frames))
    return ('<!doctype html>\n<html lang=en>\n  <head>\n    <title>KeyError: \'7\'\n // Werkzeug Debugger</title>\n    <link rel="stylesheet" href="?__debugger__=yes&amp;cmd=resource&amp;f=style.css">\n'
            '    <script src="?__debugger__=yes&amp;cmd=resource&amp;f=debugger.js"></script>\n    <script>\n      var CONSOLE_MODE = false,\n          EVALEX = true,\n          EVALEX_TRUSTED = false,\n'
            '          SECRET = "tQ3x9ZpLw7aVdE2kRnHs";\n    </script>\n  </head>\n  <body style="background-color: #fff">\n    <div class="debugger">\n<h1>KeyError</h1>\n<div class="detail">\n'
            "  <p class=\"errormsg\">KeyError: '7'\n</p>\n</div>\n<h2 class=\"traceback\">Traceback <em>(most recent call last)</em></h2>\n<div class=\"traceback\">\n  <h3></h3>\n  <ul>" + fr +
            "</ul>\n  <blockquote>KeyError: '7'\n</blockquote>\n</div>\n\n<div class=\"plain\">\n    <p>\n      This is the Copy/Paste friendly version of the traceback.\n    </p>\n"
            f'    <textarea cols="50" rows="10" name="code" readonly>{_esc(plain)}</textarea>\n</div>\n<div class="explanation">\nThe debugger caught an exception in your WSGI application.  You can now\n'
            'look at the traceback which led to the error.  <span class="nojavascript">\nIf you enable JavaScript you can also use additional features such as code\nexecution (if the evalex feature is enabled), '
            'automatic pasting of the\nexceptions and much more.</span>\n</div>\n      <div class="footer">\n        Brought to you by <strong class="arthur">DON\'T PANIC</strong>, your\n'
            '        friendly Werkzeug powered traceback interpreter.\n      </div>\n    </div>\n\n    <div class="pin-prompt">\n      <div class="inner">\n        <h3>Console Locked</h3>\n      </div>\n    </div>\n  </body>\n</html>\n\n\n')


def _django_404() -> str:
    pats = "".join(f"<li>{i + 1}. admin/{p}/</li>" for i, p in enumerate(["login", "logout", "password_change", "password_change/done", "autocomplete", "jsi18n", "r/<int:content_type_id>/<path:object_id>"]))
    return ("<!DOCTYPE html><html lang=\"en\"><head><title>Page not found at /nope/</title><style>" + _DJ_CSS + "</style></head><body><div id=\"summary\"><h1>Page not found <small>(404)</small></h1>"
            "<table class=\"meta\"><tr><th>Request Method:</th><td>GET</td></tr><tr><th>Request URL:</th><td>http://localhost:8000/nope/</td></tr></table></div><div id=\"info\"><p>Using the URLconf defined in "
            "<code>app.urls</code>, Django tried these URL patterns, in this order:</p><ol>" + pats + "<li>orders/ [name='orders']</li></ol><p>The current path, <code>nope/</code>, didn't match any of these.</p></div></body></html>\n")


_DJ_500 = _django_page("KeyError", "'user_id'", "http://localhost:8000/orders/", _DJ_FRAMES)
_DJ_CHAIN = _django_page("TemplateDoesNotExist", "orders/list.html", "http://localhost:8000/orders/archive/", _DJ_FRAMES[:3] + [("/srv/app/orders/views.py", 77, "archive", 'return render(request, "orders/list.html", ctx)')],
                         chained="TemplateDoesNotExist: orders/list.html", template_err="Template loader postmortem\nDjango tried loading these templates, in this order:\n\nUsing engine django:\n    * django.template.loaders.app_directories.Loader: /srv/app/orders/templates/orders/list.html (Source does not exist)")
_DJ_TOP = _django_page("ValueError", "invalid literal for int() with base 10: 'abc'", "http://localhost:8000/orders/abc/", _DJ_FRAMES[2:], pastebin_first=True)
_DJ_TWO = _DJ_500.replace("</body>", '<textarea id="traceback_area">second</textarea></body>')

case(
    122,
    claim="A Django debug page (textarea#traceback_area) or Werkzeug page (textarea in div.plain) is replaced by one header line plus the unescaped plaintext traceback; every frame (file, line, function), the innermost frame, Exception Type/Value, request line, versions, chained-exception count and template-error lines are unchanged; other HTML (404 pages, pages with two candidates, markup in the textarea) is untouched.",
    loses="the page markup and CSS/JS, the per-frame source context and local variables table, the request META, GET/POST and cookies tables, the full settings table, and any exception text that appears only in the browser view",
    encode=extract_traceback,
    probes=safe_probes(probes_122),
    fixtures=lambda: [
        Fixture("Django 500 KeyError (textarea in the middle of the page)", _DJ_500, True),
        Fixture("Django chained TemplateDoesNotExist with template postmortem", _DJ_CHAIN, True),
        Fixture("Django page with the pastebin form first", _DJ_TOP, True),
        Fixture("Werkzeug/Flask page (textarea at the end)", _werkzeug_page(), True),
        Fixture("Django 404 debug page: URL patterns are the information", _django_404(), False),
        Fixture("Django DEBUG=False 500 page", "<!doctype html><html lang=\"en\"><head><title>Server Error (500)</title></head><body><h1>Server Error (500)</h1></body></html>\n", False),
        Fixture("two textareas claim to be the traceback", _DJ_TWO, None),
        Fixture("input that starts like the header", TB_HEAD + "Django debug page, 5 chars -> its plaintext traceback; x]\n<textarea id=\"traceback_area\">a</textarea>\n", None),
        Fixture("traceback quoting a textarea tag (escaped in the page)", _django_page("ValueError", "bad <textarea id=\"traceback_area\"> tag", "http://localhost:8000/x/", _DJ_FRAMES[2:3]), None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #131 Auto-mine derivable JSON fields instead of hand blocklists.
# The miner works on one array of >= 5 objects: a leaf that is identical in every row is hoisted ("const"), a string leaf that is
# a verified template of other leaves in the same or an enclosing object is replaced by the template ("tmpl"), and a field called
# node_id is dropped (opaque: neither constant nor derivable). Everything else stays in the rows.

PH_L, PH_R = "⟦", "⟧"  # placeholder brackets; an input containing them is refused
MIN_ROWS = 5


def _leaves(d: dict, prefix: str = ""):
    for k, v in d.items():
        p = prefix + k
        if isinstance(v, dict) and v:
            yield from _leaves(v, p + ".")
        else:
            yield p, v


def _dot_key(d) -> bool:
    return isinstance(d, dict) and any("." in k or _dot_key(v) for k, v in d.items())


def _find_rows(o):
    """(path, rows) for every array of >= MIN_ROWS objects at the root, or one or two object levels down."""
    out, stack = [], [("$", o, 0)]
    while stack:
        path, x, depth = stack.pop(0)
        if isinstance(x, list) and len(x) >= MIN_ROWS and all(isinstance(r, dict) for r in x):
            out.append((path, x))
        elif isinstance(x, dict) and depth < 2:
            stack.extend((path + "." + k, v, depth + 1) for k, v in x.items())
    return out


def _ancestors(p: str) -> list[str]:
    """Dict prefixes of leaf path p: 'a.b.c' -> ['', 'a.', 'a.b.']"""
    parts = p.split(".")[:-1]
    return [""] + [".".join(parts[:i + 1]) + "." for i in range(len(parts))]


def _template(p: str, lrs: list[dict], srcs: set[str]):
    first = lrs[0]
    parts: list = [first[p]]
    here = set(_ancestors(p))
    cands = [q for q in srcs if q != p and (q.rpartition(".")[0] + "." if "." in q else "") in here and isinstance(first[q], (str, int)) and not isinstance(first[q], bool)
             and "://" not in str(first[q]) and len(str(first[q])) >= (1 if isinstance(first[q], int) else 3)]
    for q in sorted(cands, key=lambda q: (-len(str(first[q])), q)):
        s = str(first[q])
        nxt = []
        for part in parts:
            if isinstance(part, tuple):
                nxt.append(part)
                continue
            bits = part.split(s)
            for i, b in enumerate(bits):
                if i:
                    nxt.append((q,))
                if b:
                    nxt.append(b)
        parts = nxt
    if not any(isinstance(x, tuple) for x in parts):
        return None
    for lr in lrs:
        try:
            if "".join(x if isinstance(x, str) else str(lr[x[0]]) for x in parts) != lr[p]:
                return None
        except KeyError:
            return None
    return parts


def _delete(row: dict, path: str) -> None:
    keys = path.split(".")
    chain = [row]
    for k in keys[:-1]:
        chain.append(chain[-1][k])
    del chain[-1][keys[-1]]
    for d, k in zip(reversed(chain[:-1]), reversed(keys[:-1])):  # drop dicts that became empty
        if not d[k]:
            del d[k]


@total
def mine_fields(text: str) -> str:
    if text.startswith("# ") or PH_L in text or PH_R in text:
        return text
    o = jload(text)
    if o is _BAD:
        return text
    head = []
    for path, rows in _find_rows(o):
        if any(_dot_key(r) for r in rows):
            continue  # a dot inside a key would make leaf paths ambiguous
        lrs = [dict(_leaves(r)) for r in rows]
        common = set(lrs[0])
        for lr in lrs[1:]:
            common &= set(lr)
        const = {p: lrs[0][p] for p in sorted(common) if all(jc(lr[p]) == jc(lrs[0][p]) for lr in lrs)}
        opaque = [p for p in sorted(common) if p.rpartition(".")[2] == "node_id" and p not in const]
        srcs = {p for p in common if p not in const and p not in opaque}
        tmpl = {}
        for p in sorted(common):
            if p in const or p in opaque or not all(isinstance(lr[p], str) for lr in lrs):
                continue
            t = _template(p, lrs, srcs)
            if t is not None:
                tmpl[p] = t
        changed = True
        while changed:  # a template may not lean on a field that is itself templated
            changed = False
            for p in list(tmpl):
                if any(isinstance(x, tuple) and x[0] in tmpl for x in tmpl[p]):
                    del tmpl[p]
                    changed = True
        if not (const or tmpl or opaque):
            continue
        head.append(f"# [{path}] {len(rows)} rows")
        head += [f"# const {p} {jc(v)}" for p, v in const.items()]
        head += [f"# tmpl {p} " + jc("".join(x if isinstance(x, str) else PH_L + x[0] + PH_R for x in t)) for p, t in tmpl.items()]
        head += [f"# drop {p}" for p in opaque]
        for r in rows:
            for p in list(const) + list(tmpl) + opaque:
                _delete(r, p)
    if not head:
        return text
    return gate(text, "\n".join(head) + "\n" + jc(o))


def _flat(row: dict) -> dict:
    return {p: v for p, v in _leaves(row) if p.rpartition(".")[2] != "node_id"}


def _arrays_of(o) -> dict:
    """path -> rows, found breadth-first over dict values only (root list, or up to two object levels down)."""
    out, queue = {}, [("$", o, 0)]
    while queue:
        path, x, d = queue.pop(0)
        if isinstance(x, list) and len(x) >= MIN_ROWS and all(isinstance(r, dict) for r in x):
            out[path] = x
        elif isinstance(x, dict) and d < 2:
            queue += [(f"{path}.{k}", v, d + 1) for k, v in x.items()]
    return out


def probes_131(text: str) -> dict:
    lines = text.split("\n")
    n = 0
    while n < len(lines) and lines[n].startswith("# "):
        n += 1
    blocks: dict = {}
    cur = None
    for line in lines[:n]:
        if m := re.match(r"# \[(.*)\] (\d+) rows$", line):
            cur = blocks[m.group(1)] = {"const": {}, "tmpl": {}}
        elif cur is None:
            continue
        elif m := re.match(r"# const (\S+) (.*)$", line):
            cur["const"][m.group(1)] = json.loads(m.group(2))
        elif m := re.match(r"# tmpl (\S+) (.*)$", line):
            cur["tmpl"][m.group(1)] = json.loads(m.group(2))
    o = jload("\n".join(lines[n:]))
    if o is _BAD:
        return {"rows": None}
    out = {}
    for path, rows in _arrays_of(o).items():
        b = blocks.get(path, {"const": {}, "tmpl": {}})
        flat_rows = []
        for r in rows:
            f = dict(_flat(r))
            f.update(b["const"])
            for p, t in b["tmpl"].items():
                f[p] = re.sub(PH_L + r"(.*?)" + PH_R, lambda m: str(f[m.group(1)]), t)
            flat_rows.append(f)
        out[path] = flat_rows
    return {"rows per array (every leaf except node_id)": out}


def _gh_issues(n: int, pull_at: int | None = None, seed: int = 7) -> list[dict]:
    r = rng(seed)
    users = [("octocat", 583231), ("mira-k", 1024877), ("devon-p", 4410022), ("tsato", 77120), ("lena-w", 9120334), ("bhavna", 5511002)]
    labels = [("bug", "d73a4a", "Something isn't working"), ("enhancement", "a2eeef", "New feature or request"), ("docs", "0075ca", "Improvements or additions to documentation"), ("good first issue", "7057ff", "Good for newcomers")]
    out = []
    for i in range(n):
        num = 1300 + i * 3 + r.randrange(3)
        login, uid = users[r.randrange(len(users))]
        base = f"https://api.github.com/repos/acme/widgets/issues/{num}"
        is_pr = pull_at == i
        usr = {"login": login, "id": uid, "node_id": f"MDQ6VXNlcj{uid}", "avatar_url": f"https://avatars.githubusercontent.com/u/{uid}?v=4", "gravatar_id": "", "url": f"https://api.github.com/users/{login}",
               "html_url": f"https://github.com/{login}", "followers_url": f"https://api.github.com/users/{login}/followers", "following_url": f"https://api.github.com/users/{login}/following{{/other_user}}",
               "gists_url": f"https://api.github.com/users/{login}/gists{{/gist_id}}", "starred_url": f"https://api.github.com/users/{login}/starred{{/owner}}{{/repo}}",
               "subscriptions_url": f"https://api.github.com/users/{login}/subscriptions", "organizations_url": f"https://api.github.com/users/{login}/orgs", "repos_url": f"https://api.github.com/users/{login}/repos",
               "events_url": f"https://api.github.com/users/{login}/events{{/privacy}}", "received_events_url": f"https://api.github.com/users/{login}/received_events", "type": "User", "site_admin": False}
        row = {"url": base, "repository_url": "https://api.github.com/repos/acme/widgets", "labels_url": base + "/labels{/name}", "comments_url": base + "/comments", "events_url": base + "/events",
               "html_url": f"https://github.com/acme/widgets/{'pull' if is_pr else 'issues'}/{num}", "id": 2000000000 + num * 17, "node_id": f"I_kwDOAB{num:06d}Zq", "number": num,
               "title": ["Crash when saving empty form", "Dark mode flickers on load", "Docs: add install section", "Support CSV export", "Timeout in sync job", "Wrong total in cart"][i % 6] + f" ({num})",
               "user": usr, "labels": [{"id": 100 + k, "node_id": f"LA_kwDO{k:04d}", "url": f"https://api.github.com/repos/acme/widgets/labels/{lb[0]}", "name": lb[0], "color": lb[1], "default": k == 0, "description": lb[2]}
                                       for k, lb in enumerate(labels) if (i + k) % 3 == 0], "state": "open", "locked": i == n - 1 and pull_at is None,
               "assignee": None, "assignees": [], "milestone": None, "comments": r.randrange(12), "created_at": f"2024-0{1 + i % 9}-1{i % 9}T09:{10 + i % 50}:00Z",
               "updated_at": f"2024-0{1 + i % 9}-2{i % 8}T17:{10 + i % 50}:00Z", "closed_at": None, "author_association": ["NONE", "CONTRIBUTOR", "MEMBER"][i % 3], "active_lock_reason": None,
               "body": ["Steps to reproduce:\n1. open the form\n2. press save", "It happens after a hard reload only.", "We should document the env vars.", "Customers keep asking for this."][i % 4],
               "reactions": {"url": base + "/reactions", "total_count": i % 4, "+1": i % 4, "-1": 0, "laugh": 0, "hooray": 0, "confused": 0, "heart": 0, "rocket": 0, "eyes": 0},
               "timeline_url": base + "/timeline", "performed_via_github_app": None, "state_reason": "reopened" if i == n // 2 and pull_at is None else None}
        if is_pr:
            row["pull_request"] = {"url": f"https://api.github.com/repos/acme/widgets/pulls/{num}", "html_url": f"https://github.com/acme/widgets/pull/{num}"}
        if i == 0:
            row["assignee"] = dict(usr)
        out.append(row)
    return out


_GH30 = jc(_gh_issues(30))
_GH_WITH_PR = jc(_gh_issues(30, pull_at=14))
_GH_WRAPPED = jc({"total_count": 120, "incomplete_results": False, "items": _gh_issues(24, seed=3)})
_NO_PATTERN = jc([{"id": i * 7919 % 1000, "name": "".join(chr(97 + (i * k) % 26) for k in range(5, 11)), "score": (i * 37) % 101 / 7} for i in range(12)])

case(
    131,
    claim="In a JSON array of >= 5 objects, leaves identical in every row are hoisted into '# const' lines, string leaves that verify as a template of other leaves (same or enclosing object) become one '# tmpl' line, and node_id is dropped; expanding the header restores every leaf of every row except node_id, and a field that breaks the pattern in even one row (a PR among issues, one locked row) stays in the rows.",
    loses="node_id values (opaque, neither constant nor derivable; dropped by name), the key order inside rows, and JSON whitespace; derived and constant fields are recoverable only by applying the printed header",
    encode=mine_fields,
    probes=safe_probes(probes_131),
    fixtures=lambda: [
        Fixture("30 GitHub issues (state_reason set in the middle, locked on the last)", _GH30, True),
        Fixture("same with a pull request in the middle: html_url stops being a template", _GH_WITH_PR, True),
        Fixture("search-style wrapper {total_count, items: [...]}", _GH_WRAPPED, True),
        Fixture("12 rows with nothing constant or derivable", _NO_PATTERN, False),
        Fixture("4 rows are too few to mine", jc(_gh_issues(4)), False),
        Fixture("text that is not JSON", "# const x 1\nNAME,ID\nwidget,7\n", False),
        Fixture("input containing the placeholder brackets", _GH30.replace('"Docs: add', '"⟦Docs: add'), None),
    ],
    min_gain=0.4,
)


# ===============================================================================================================
# #132 Sample long JSON arrays and cap depth, keeping outliers.
# An array of >= 100 elements (>= 90% objects) becomes {"$sampled": {total, keys, kept, stats}, "rows": [kept rows]}. Kept: the
# first and last 3 rows, every error row, every row more than 3 sigma from the mean in a numeric column, every row whose keys
# differ from the modal key set (or that is not an object). Inside kept rows, containers deeper than ROW_DEPTH become stubs.

MIN_ARRAY, EDGE, ROW_DEPTH = 100, 3, 6
ERR_RE = re.compile(r"(?i)error|fail|fatal|panic|exception|crashloop|oomkill|unhealthy|timeout|timed out")
ERRKEY_RE = re.compile(r"(?i)error|fail")


def _is_long(lst) -> bool:
    return isinstance(lst, list) and len(lst) >= MIN_ARRAY and sum(isinstance(r, dict) for r in lst) * 10 >= len(lst) * 9


def _is_err(v, depth=0, ctx=False) -> bool:
    if isinstance(v, dict):
        return depth <= ROW_DEPTH and any(_is_err(x, depth + 1, ctx or bool(ERRKEY_RE.search(k))) for k, x in v.items())
    if isinstance(v, list):
        return depth <= ROW_DEPTH and any(_is_err(x, depth + 1, ctx) for x in v)
    if isinstance(v, str):
        return bool(v.strip()) and (ctx or bool(ERR_RE.search(v)))
    return ctx and bool(v) and isinstance(v, (int, float))


def _num_leaves(row, depth=0, prefix=""):
    if isinstance(row, dict) and depth <= ROW_DEPTH:
        for k, v in row.items():
            if isinstance(v, dict):
                yield from _num_leaves(v, depth + 1, prefix + k + ".")
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                yield prefix + k, v


def _columns(rows) -> dict:
    cols: dict[str, dict[int, float]] = {}
    for i, r in enumerate(rows):
        for p, v in _num_leaves(r):
            cols.setdefault(p, {})[i] = v
    out = {}
    for p, vals in sorted(cols.items()):
        xs = list(vals.values())
        if len(xs) * 10 < len(rows) * 9 or len(set(xs)) < 3:
            continue
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        out[p] = {"mean": mean, "var": var, "min": min(xs), "max": max(xs), "vals": vals}
    return out


def _cap(v, depth=0):
    if isinstance(v, dict):
        return {k: _cap(x, depth + 1) for k, x in v.items()} if depth <= ROW_DEPTH else {"$keys": list(v)}
    if isinstance(v, list):
        return [_cap(x, depth + 1) for x in v] if depth <= ROW_DEPTH else {"$len": len(v)}
    return v


def _sample(lst: list):
    keysets = Counter(tuple(r) for r in lst if isinstance(r, dict))
    modal = list(keysets.most_common(1)[0][0])
    keep = set(range(EDGE)) | set(range(len(lst) - EDGE, len(lst)))
    for i, r in enumerate(lst):
        if not isinstance(r, dict) or set(r) != set(modal) or _is_err(r):
            keep.add(i)
    cols = _columns(lst)
    for c in cols.values():
        if c["var"] > 0:
            keep |= {i for i, x in c["vals"].items() if (x - c["mean"]) ** 2 >= 9 * c["var"]}
    kept = sorted(keep)
    stats = {p: [c["mean"], c["var"], c["min"], c["max"]] for p, c in cols.items()}
    return {"$sampled": {"total": len(lst), "keys": modal, "kept": kept, "stats": stats}, "rows": [_cap(lst[i]) for i in kept]}


def _sample_tree(node):
    if isinstance(node, dict):
        if "$sampled" in node:
            return node
        return {k: _sample_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return _sample(node) if _is_long(node) else [_sample_tree(x) for x in node]
    return node


@total
def sample_arrays(text: str) -> str:
    o = jload(text)
    if o is _BAD:
        return text
    return gate(text, jc(_sample_tree(o)))


def _canon(v, depth=0):
    """Probe-side capping: idempotent on stubs the encoder already wrote."""
    if isinstance(v, dict):
        if depth > ROW_DEPTH:
            return v if set(v) == {"$keys"} else {"$keys": list(v)}
        return {k: _canon(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_canon(x, depth + 1) for x in v] if depth <= ROW_DEPTH else {"$len": len(v)}
    return v


def _err_stack(row) -> bool:
    stack = [(row, 0, False)]
    while stack:
        v, d, ctx = stack.pop()
        if isinstance(v, dict) and d <= ROW_DEPTH:
            stack += [(x, d + 1, ctx or bool(ERRKEY_RE.search(k))) for k, x in v.items()]
        elif isinstance(v, list) and d <= ROW_DEPTH:
            stack += [(x, d + 1, ctx) for x in v]
        elif isinstance(v, str) and v.strip() and (ctx or ERR_RE.search(v)):
            return True
        elif ctx and isinstance(v, (int, float)) and v:
            return True
    return False


def _wrapper(x) -> bool:
    s = x.get("$sampled") if isinstance(x, dict) else None
    return (isinstance(s, dict) and isinstance(s.get("total"), int) and isinstance(s.get("keys"), list) and isinstance(s.get("kept"), list) and isinstance(s.get("stats"), dict)
            and isinstance(x.get("rows"), list) and len(x["rows"]) == len(s["kept"]) and all(isinstance(i, int) for i in s["kept"]))


def probes_132(text: str) -> dict:
    o = jload(text)
    if o is _BAD:
        return {"arrays": None}
    found, stack = {}, [("$", o)]
    while stack:
        path, x = stack.pop()
        if _wrapper(x):
            s = x["$sampled"]
            found[path] = (s["total"], s["keys"], dict(zip(s["kept"], x["rows"])), s["stats"])
        elif _is_long(x):
            modal = list(Counter(tuple(r) for r in x if isinstance(r, dict)).most_common(1)[0][0])
            found[path] = (len(x), modal, dict(enumerate(x)), None)
        elif isinstance(x, dict):
            stack += [(f"{path}.{k}", v) for k, v in x.items()]
        elif isinstance(x, list):
            stack += [(f"{path}[{i}]", v) for i, v in enumerate(x)]
    out = {}
    for path, (total, keys, rows, stats) in sorted(found.items()):
        if stats is None:  # an array the encoder left alone: compute what the header would hold
            ordered = [rows[i] for i in range(total)]
            stats = {p: [c["mean"], c["var"], c["min"], c["max"]] for p, c in _columns(ordered).items()}
        need = {}
        for i, r in rows.items():
            odd = not isinstance(r, dict) or set(r) != set(keys)
            flagged = {}
            for p, v in _num_leaves(r) if isinstance(r, dict) else []:
                if p in stats and stats[p][1] > 0 and (v - stats[p][0]) ** 2 >= 9 * stats[p][1]:
                    flagged[p] = v
            if i < EDGE or i >= total - EDGE or odd or _err_stack(r) or flagged:
                need[i] = {"row": _canon(r), "odd schema": odd, "error": _err_stack(r), "sigma outlier columns": flagged}
        out[path] = {"total rows": total, "schema": keys, "required rows": need, "column min/max": {p: (s[2], s[3]) for p, s in stats.items()}}
    return {"arrays": out}


def _pods(n: int = 240) -> str:
    r = rng(5)
    items = []
    bad = {0: "CrashLoopBackOff", 117: "OOMKilled", 239: "Error"}
    for i in range(n):
        name = f"api-{i:03d}-{r.randrange(16 ** 5):05x}"
        reason = bad.get(i)
        cs = {"name": "api", "ready": reason is None, "restartCount": 0 if reason is None else 14, "image": "registry.local/api:3.1.0", "imageID": "sha256:" + hexid(i, 64),
              "state": {"running": {"startedAt": "2025-02-01T10:00:00Z"}} if reason is None else {"waiting": {"reason": reason, "message": "back-off 5m0s restarting failed container=api"}} if reason == "CrashLoopBackOff"
              else {"terminated": {"reason": reason, "exitCode": 137 if reason == "OOMKilled" else 1, "finishedAt": "2025-02-01T10:05:00Z"}}}
        items.append({"metadata": {"name": name, "namespace": "prod", "uid": hexid(i, 32, "pod"), "labels": {"app": "api", "tier": "web", "pod-template-hash": "7d9f"},
                                   "creationTimestamp": "2025-02-01T09:00:00Z", "ownerReferences": [{"kind": "ReplicaSet", "name": "api-7d9f"}]},
                      "spec": {"nodeName": f"node-{i % 12}", "containers": [{"name": "api", "image": "registry.local/api:3.1.0", "resources": {"limits": {"memory": "512Mi", "cpu": "500m"}},
                                                                            "env": [{"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password", "optional": False}}}]}]},
                      "status": {"phase": "Running" if reason != "Error" else "Failed", "podIP": f"10.1.{i // 250}.{i % 250}", "hostIP": f"192.168.0.{i % 12 + 10}", "containerStatuses": [cs]}})
    return jc({"apiVersion": "v1", "kind": "List", "items": items, "metadata": {"resourceVersion": ""}})


def _svc_metrics() -> str:
    r = rng(11)
    rows = []
    for i in range(200):
        lat = round(r.gauss(120, 12), 1)
        if i in (5, 118, 190):  # numeric outliers early, in the middle and late
            lat = [4800.5, 9100.0, 3300.0][(5, 118, 190).index(i)]
        rows.append({"id": f"svc-{i:03d}", "region": ["eu", "us", "ap"][i % 3], "status": "ok", "latency_ms": lat, "rps": round(r.gauss(900, 90), 1), "error": None, "tags": {"team": "core", "tier": i % 3}})
    rows[60].update(status="degraded", error={"code": 503, "message": "upstream unavailable"})  # error rows with no error-looking string
    rows[199]["status"] = "failed"
    rows[130]["extra_debug_field"] = True  # a row with a different key set
    return jc({"generated": "2025-02-01T00:00:00Z", "services": rows})


def _plain_rows(n: int) -> str:
    r = rng(2)
    return jc([{"id": i, "name": f"item-{i}", "weight": round(r.uniform(1, 2), 3), "ok": True} for i in range(n)])


case(
    132,
    claim="Arrays of >= 100 mostly-object elements are sampled to the first and last 3 rows plus every error row, every row beyond 3 sigma in a numeric column and every row with a different key set, with the schema, total count and column mean/var/min/max kept in a $sampled header and containers deeper than 6 inside kept rows stubbed; the required rows are identical wherever they sit in the array.",
    loses="all unremarkable middle rows (their values and positions are known only as 'not kept'), containers deeper than six levels inside kept rows (key list or length only), anything inside an omitted row that is neither an error string nor a numeric outlier, and JSON whitespace; the depth cap is applied only inside sampled arrays",
    encode=sample_arrays,
    probes=safe_probes(probes_132),
    fixtures=lambda: [
        Fixture("kubectl get pods -o json: 240 pods, CrashLoopBackOff first, OOMKilled middle, Failed last", _pods(), True),
        Fixture("service metrics: sigma outliers at 5/118/190, 503 error at 60, odd row at 130", _svc_metrics(), True),
        Fixture("150 plain rows, nothing special: edges only", _plain_rows(150), True),
        Fixture("99 rows are below the threshold", _plain_rows(99), False),
        Fixture("long array of strings is not row data", jc(["x" * 20 + str(i) for i in range(300)]), False),
        Fixture("a document that already holds a $sampled header", jc({"$sampled": {"total": 500, "keys": [], "kept": [], "stats": {}}, "rows": []}), None),
        Fixture("non-finite numbers and big ints in the rows", jc([{"id": i, "v": 1e999 if i == 40 else i % 7} for i in range(120)]), None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #133 Elide long numeric arrays (embeddings, coordinates, histogram buckets) in JSON documents.
# Text-level, so everything outside the array stays byte-identical: a flat array of >= 32 numbers, or an array of >= 8 equal-width
# rows (width <= 8) holding >= 32 numbers, becomes one JSON string holding count, min, max and the first values.

FLAT_MIN, ROWS_MIN, ROW_WIDTH_MAX = 32, 8, 8
_NUM = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_WS = re.compile(r"\s*")
_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|\[')


def _numarray(t: str, i: int, depth: int = 0):
    """Parse the array at t[i]=='[' if it holds only numbers (or, at depth 0, only flat numeric arrays). -> (end, items) or None."""
    try:
        j = _WS.match(t, i + 1).end()
        items = []
        while True:
            if t[j] == "[" and depth == 0:
                sub = _numarray(t, j, 1)
                if sub is None:
                    return None
                j2, item = sub
            else:
                m = _NUM.match(t, j)
                if not m:
                    return None
                j2, item = m.end(), m.group(0)
            items.append(item)
            j = _WS.match(t, j2).end()
            if t[j] == ",":
                j = _WS.match(t, j + 1).end()
            elif t[j] == "]":
                return j + 1, items
            else:
                return None
    except IndexError:
        return None


def _kind(toks) -> str:
    return "floats" if any(re.search(r"[.eE]", x) for x in toks) else "ints"


def _stub(items) -> str | None:
    if all(isinstance(x, str) for x in items):
        if len(items) < FLAT_MIN:
            return None
        lo, hi = min(items, key=float), max(items, key=float)
        return f"[{len(items)} {_kind(items)}, min {lo}, max {hi}, first 4: {' '.join(items[:4])}]"
    if not all(isinstance(x, list) and x for x in items):
        return None
    widths = {len(x) for x in items}
    flat = [t for x in items for t in x]
    if len(items) < ROWS_MIN or len(widths) != 1 or len(flat) < FLAT_MIN or widths.pop() > ROW_WIDTH_MAX:
        return None
    w = len(items[0])
    cmin = ",".join(min((r[c] for r in items), key=float) for c in range(w))
    cmax = ",".join(max((r[c] for r in items), key=float) for c in range(w))
    first = ";".join(",".join(r) for r in items[:2])
    return f"[{len(items)} x {w} {_kind(flat)}, col min {cmin}, col max {cmax}, first 2 rows: {first}]"


@total
def elide_numeric_arrays(text: str) -> str:
    if jload(text) is _BAD:
        return text
    out, last, pos = [], 0, 0
    while True:
        m = _TOK.search(text, pos)
        if not m:
            break
        if m.group(0) == "[":
            r = _numarray(text, m.start())
            s = _stub(r[1]) if r else None
            if s:
                out += [text[last:m.start()], json.dumps(s)]
                last = pos = r[0]
                continue
        pos = m.end()
    out.append(text[last:])
    return gate(text, "".join(out))


class _Tok(str):
    """A JSON number kept as its source text, so strings and numbers stay distinguishable."""


class _Const(str):
    """NaN / Infinity literals: not plain numbers, so an array holding one is never elided."""


_STUB_FLAT = re.compile(r"\[(\d+) (floats|ints), min (\S+), max (\S+), first 4: (.*)\]\Z", re.S)
_STUB_ROWS = re.compile(r"\[(\d+) x (\d+) (floats|ints), col min (\S+), col max (\S+), first 2 rows: (.*)\]\Z", re.S)


def _elided_record(x):
    """Record for an array the encoder would elide, or for a string already in stub form; None otherwise."""
    if isinstance(x, str) and not isinstance(x, _Tok):
        if m := _STUB_FLAT.match(x):
            return ("flat", int(m.group(1)), m.group(2), m.group(3), m.group(4), tuple(m.group(5).split(" ")))
        if m := _STUB_ROWS.match(x):
            return ("rows", int(m.group(1)), int(m.group(2)), m.group(3), tuple(m.group(4).split(",")), tuple(m.group(5).split(",")), m.group(6))
        return None
    if not isinstance(x, list) or not x:
        return None
    if all(isinstance(v, _Tok) for v in x):
        if len(x) < FLAT_MIN:
            return None
        return ("flat", len(x), _kind(x), min(x, key=float), max(x, key=float), tuple(x[:4]))
    if all(isinstance(v, list) and v and all(isinstance(t, _Tok) for t in v) for v in x):
        w = len(x[0])
        if len(x) < ROWS_MIN or any(len(v) != w for v in x) or len(x) * w < FLAT_MIN or w > ROW_WIDTH_MAX:
            return None
        flat = [t for v in x for t in v]
        return ("rows", len(x), w, _kind(flat), tuple(min((v[c] for v in x), key=float) for c in range(w)), tuple(max((v[c] for v in x), key=float) for c in range(w)),
                ";".join(",".join(v) for v in x[:2]))
    return None


def probes_133(text: str) -> dict:
    try:
        o = json.loads(text, parse_int=_Tok, parse_float=_Tok, parse_constant=_Const)
    except Exception:  # noqa: BLE001
        return {"document": None}
    records, rest, stack = {}, {}, [("$", o)]

    def skeleton(path, x):
        rec = _elided_record(x)
        if rec is not None:
            records[path] = rec
            return "<elided>"
        if isinstance(x, dict):
            return {k: skeleton(f"{path}.{k}", v) for k, v in x.items()}
        if isinstance(x, list):
            return [skeleton(f"{path}[{i}]", v) for i, v in enumerate(x)]
        return ("num", str(x)) if isinstance(x, _Tok) else x

    return {"elided arrays (count, kind, min, max, first values)": records, "everything else": skeleton("$", o)}


def _embedding_response() -> str:
    r = rng(21)
    data = [{"object": "embedding", "index": i, "embedding": [round(r.gauss(0, 0.026), 17) for _ in range(1536)]} for i in range(3)]
    data[0]["bbox"] = [0, 0, 640, 480]  # short arrays stay as they are
    return jc({"object": "list", "data": data, "model": "text-embedding-3-small", "usage": {"prompt_tokens": 9, "total_tokens": 9}})


def _geojson() -> str:
    r = rng(8)
    ring = [[round(4.85 + r.random() * 0.1, 6), round(52.35 + r.random() * 0.06, 6)] for _ in range(200)]
    ring.append(ring[0])
    line = [[round(5.1 + i * 0.001, 6), round(52.0 + r.random() * 0.01, 6), round(r.uniform(-2, 40), 1)] for i in range(150)]
    return jp({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Amsterdam-Centrum", "area_km2": 8.04}, "geometry": {"type": "Polygon", "coordinates": [ring]}},
        {"type": "Feature", "properties": {"name": "Fietsroute 12"}, "geometry": {"type": "LineString", "coordinates": line}},
        {"type": "Feature", "properties": {"name": "Central Station"}, "geometry": {"type": "Point", "coordinates": [4.9004, 52.3791]}}]})


def _histogram() -> str:
    r = rng(3)
    return jc({"name": "http_request_duration_seconds", "buckets": [r.randrange(0, 9000) for _ in range(64)], "bounds": [round(0.005 * 1.25 ** i, 4) for i in range(63)],
               "short": list(range(31)), "sum": 1834.25, "count": 71234, "p99_series": [round(r.uniform(0.1, 0.9), 3) for _ in range(40)]})


case(
    133,
    claim="In a valid JSON document, a flat array of >= 32 numbers (or >= 8 equal-width numeric rows, width <= 8) is replaced in place by one string holding the count, min, max and first values (per-column for rows); every other byte, every short array and every non-numeric array is untouched.",
    loses="all numeric values of an elided array except its count, min, max and first 4 values (first 2 rows for coordinate lists), their order and the position of the extremes; numpy/torch text prints and invalid or truncated JSON are not handled",
    encode=elide_numeric_arrays,
    probes=safe_probes(probes_133),
    fixtures=lambda: [
        Fixture("embeddings API response: three 1536-float vectors, a 4-number bbox kept", _embedding_response(), True),
        Fixture("GeoJSON: 201-point polygon, 150-point 3D line, a point", _geojson(), True),
        Fixture("histogram buckets, bounds and a 40-point series; a 31-item array stays", _histogram(), True),
        Fixture("only short arrays", jc({"bbox": [1, 2, 3, 4], "ids": list(range(31)), "pairs": [[1, 2]] * 7}), False),
        Fixture("numpy print is not JSON", "array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.0, 3.1, 3.2, 3.3])\n", False),
        Fixture("string that looks like an array, mixed array, stub-like string, ragged rows", jc({"note": "[" + ", ".join(str(i) for i in range(40)) + "]", "mixed": list(range(30)) + ["x"] * 5 + list(range(5)),
                                                                                                  "already": "[40 floats, min 0.1, max 0.9, first 4: 0.1 0.2 0.3 0.4]", "ragged": [[1, 2], [1], [1, 2, 3]] * 12, "nan": [float("nan")] + list(range(40))}), None),
        Fixture("exactly 32 numbers with exponents and negative zero", jc({"v": [-0.0, 1e-7, 2.5e+3, -4E2] + list(range(28))}), None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #135 OpenAPI spec digest into an endpoint table (OpenAPI 3.x JSON; swagger.yaml needs a YAML parser and is out of scope).
# One line per operation ('METHOD /path [operationId] (name:in:type*, @body:T*) -> 200:Pet 404 | summary'), one line per component
# schema, and a header naming the shared error schema; examples, x- extensions, descriptions, tags, security and server variables go.

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_NAME_OK = re.compile(r"[\w.\-$]+\Z")


def _norm(s) -> str:
    return " ".join(str(s).split())


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def sig(s) -> str:
    if not isinstance(s, dict):
        return "any"
    if "$ref" in s:
        return _ref_name(str(s["$ref"]))
    if "enum" in s:
        return "enum(" + "|".join(v if isinstance(v, str) else json.dumps(v) for v in s["enum"]) + ")"
    for k in ("allOf", "oneOf", "anyOf"):
        if k in s:
            return k + "(" + ",".join(sig(x) for x in s[k]) + ")"
    t = s.get("type")
    if t == "array":
        return "[" + sig(s.get("items")) + "]"
    if t == "object" or "properties" in s:
        ap = s.get("additionalProperties")
        return f"map<{sig(ap)}>" if isinstance(ap, dict) and "properties" not in s else "object"
    if isinstance(t, str):
        return t + (":" + s["format"] if isinstance(s.get("format"), str) else "")
    return "any"


def _resolve(spec: dict, node):
    """One-level $ref resolution for components/parameters and components/responses."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/components/"):
        cur = spec
        for part in node["$ref"][2:].split("/"):
            cur = cur[part]
        return cur
    return node


def _content_sig(c) -> str | None:
    if not isinstance(c, dict) or not c:
        return None
    media = c.get("application/json") or next(iter(c.values()))
    return sig(media.get("schema")) if isinstance(media, dict) and "schema" in media else None


def _ops(spec: dict):
    for path, item in spec["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                yield method.upper(), path, item[method], item.get("parameters", [])


@total
def openapi_digest(text: str) -> str:
    spec = jload(text)
    if not (isinstance(spec, dict) and str(spec.get("openapi", "")).startswith("3.") and isinstance(spec.get("paths"), dict)):
        return text
    ops = []
    for method, path, op, shared in _ops(spec):
        params = []
        for p in [_resolve(spec, x) for x in list(shared) + list(op.get("parameters", []))]:
            if not _NAME_OK.match(str(p["name"])):
                return text
            params.append(f"{p['name']}:{p['in']}:{sig(p.get('schema'))}" + ("*" if p.get("required") else ""))
        rb = _resolve(spec, op.get("requestBody"))
        if isinstance(rb, dict):
            params.append(f"@body:{_content_sig(rb.get('content')) or 'any'}" + ("*" if rb.get("required") else ""))
        resp = []
        for status, r in op.get("responses", {}).items():
            r = _resolve(spec, r)
            resp.append((str(status), _content_sig(r.get("content")) if isinstance(r, dict) else None))
        oid = op.get("operationId", "-")
        if not (re.fullmatch(r"\S+", path) and re.fullmatch(r"[^\s\]]+", str(oid))):
            return text
        ops.append((method, path, oid, params, resp, _norm(op.get("summary", ""))))
    errs = Counter(s for *_a, resp, _s in ops for st, s in resp if s and (st.startswith(("4", "5")) or st == "default"))
    common = errs.most_common(1)[0][0] if errs and errs.most_common(1)[0][1] >= 3 else None
    lines = [f"# openapi {_norm(spec['openapi'])} | {_norm(spec.get('info', {}).get('title', ''))} {_norm(spec.get('info', {}).get('version', ''))}"]
    if common:
        lines.append(f"# responses >= 400 and default carry {common} unless a schema is shown")
    for method, path, oid, params, resp, summary in ops:
        r = " ".join(st if (s is None or (s == common and (st.startswith(("4", "5")) or st == "default"))) else f"{st}:{s}" for st, s in resp)
        lines.append(f"{method} {path} [{oid}] ({', '.join(params)}) -> {r}" + (f" | {summary}" if summary else ""))
    schemas = spec.get("components", {}).get("schemas", {})
    if schemas:
        lines.append("# schemas")
    for name, s in schemas.items():
        if not _NAME_OK.match(name) or not isinstance(s, dict) or ("properties" in s and any(k in s for k in ("allOf", "oneOf", "anyOf"))):
            return text
        if isinstance(s.get("properties"), dict) and s["properties"]:
            req = set(s.get("required", []))
            if not all(_NAME_OK.match(p) for p in s["properties"]):
                return text
            lines.append(f"{name} {{" + ", ".join(f"{p}{'*' if p in req else ''}:{sig(v)}" for p, v in s["properties"].items()) + "}")
        else:
            lines.append(f"{name} = {sig(s)}")
    return gate(text, "\n".join(lines) + "\n")


def _sig2(s) -> str:
    """Probe-side signature: same notation, written as a dispatch on the schema's keys."""
    if not isinstance(s, dict):
        return "any"
    handlers = [("$ref", lambda: str(s["$ref"]).split("/")[-1]), ("enum", lambda: "enum(" + "|".join(x if isinstance(x, str) else json.dumps(x) for x in s["enum"]) + ")")]
    handlers += [(k, (lambda k=k: f"{k}({','.join(_sig2(x) for x in s[k])})")) for k in ("allOf", "oneOf", "anyOf")]
    for key, fn in handlers:
        if key in s:
            return fn()
    if s.get("type") == "array":
        return f"[{_sig2(s.get('items'))}]"
    if s.get("type") == "object" or "properties" in s:
        ap = s.get("additionalProperties")
        return "map<" + _sig2(ap) + ">" if isinstance(ap, dict) and not s.get("properties") else "object"
    return "any" if not isinstance(s.get("type"), str) else s["type"] + (":" + s["format"] if isinstance(s.get("format"), str) else "")


def _split_top(s: str) -> list[str]:
    return [x for x in s.split(", ")] if s else []


def probes_135(text: str) -> dict:
    spec = jload(text)
    if isinstance(spec, dict) and str(spec.get("openapi", "")).startswith("3.") and isinstance(spec.get("paths"), dict):
        operations, errs = {}, Counter()
        raw = []
        for item_path, item in spec["paths"].items():
            for m in item:
                if m in HTTP_METHODS:
                    op = item[m]
                    resp = {}
                    for st, r in op.get("responses", {}).items():
                        r = _resolve(spec, r)
                        c = r.get("content") if isinstance(r, dict) else None
                        med = (c.get("application/json") or next(iter(c.values()))) if isinstance(c, dict) and c else None
                        resp[str(st)] = _sig2(med.get("schema")) if isinstance(med, dict) and "schema" in med else None
                        if resp[str(st)] and (str(st)[0] in "45" or st == "default"):
                            errs[resp[str(st)]] += 1
                    raw.append((m.upper(), item_path, op, item, resp))
        common = errs.most_common(1)[0][0] if errs and errs.most_common(1)[0][1] >= 3 else None
        for m, p, op, item, resp in raw:
            params = {}
            for x in list(item.get("parameters", [])) + list(op.get("parameters", [])):
                x = _resolve(spec, x)
                params[(x["name"], x["in"])] = (_sig2(x.get("schema")), bool(x.get("required")))
            rb = _resolve(spec, op.get("requestBody"))
            body = None
            if isinstance(rb, dict):
                c = rb.get("content")
                med = (c.get("application/json") or next(iter(c.values()))) if isinstance(c, dict) and c else None
                body = (_sig2(med.get("schema")) if isinstance(med, dict) and "schema" in med else "any", bool(rb.get("required")))
            operations[f"{m} {p}"] = {"id": op.get("operationId", "-"), "params": params, "body": body, "summary": _norm(op.get("summary", "")),
                                      "responses": resp}
        # the digest drops the shared error schema from the lines; the probe answers "what does 404 return" by filling it in on this side too
        schemas = {}
        for name, s in spec.get("components", {}).get("schemas", {}).items():
            if isinstance(s.get("properties"), dict) and s["properties"]:
                req = set(s.get("required", []))
                schemas[name] = {"props": {p: (_sig2(v), p in req) for p, v in s["properties"].items()}}
            else:
                schemas[name] = {"sig": _sig2(s)}
        return {"operations": operations, "schemas": schemas, "common error": common}
    # digest side
    operations, schemas, common, in_schemas = {}, {}, None, False
    for line in text.split("\n"):
        if m := re.match(r"# responses >= 400 and default carry (\S+) unless", line):
            common = m.group(1)
        elif line == "# schemas":
            in_schemas = True
        elif not in_schemas and (m := re.match(r"([A-Z]+) (\S+) \[([^\]]*)\] \((.*?)\) -> (.*?)(?: \| (.*))?$", line)):
            params, body = {}, None
            for piece in _split_top(m.group(4)):
                req = piece.endswith("*")
                piece = piece[:-1] if req else piece
                if piece.startswith("@body:"):
                    body = (piece[6:], req)
                else:
                    name, loc, s = piece.split(":", 2)
                    params[(name, loc)] = (s, req)
            resp = {}
            for r in m.group(5).split(" "):
                st, _, s = r.partition(":")
                resp[st] = s or (common if (st[0] in "45" or st == "default") and common else None)
            operations[f"{m.group(1)} {m.group(2)}"] = {"id": m.group(3), "params": params, "body": body, "summary": m.group(6) or "", "responses": resp}
        elif in_schemas and (m := re.match(r"(\S+) \{(.*)\}$", line)):
            props = {}
            for piece in _split_top(m.group(2)):
                pm = re.match(r"([^:*]+)(\*?):(.*)$", piece)
                props[pm.group(1)] = (pm.group(3), bool(pm.group(2)))
            schemas[m.group(1)] = {"props": props}
        elif in_schemas and (m := re.match(r"(\S+) = (.*)$", line)):
            schemas[m.group(1)] = {"sig": m.group(2)}
    if not operations and not schemas:
        return {"operations": None}
    return {"operations": operations, "schemas": schemas, "common error": common}


def _spec(resources: list[str], extra_first: bool = True) -> dict:
    err = {"$ref": "#/components/schemas/Error"}
    ok_json = lambda sch: {"application/json": {"schema": sch, "examples": {"sample": {"summary": "an example", "value": {"id": 1}}}}}  # noqa: E731
    e4 = lambda d: {"description": d, "content": {"application/json": {"schema": err, "example": {"code": 4, "message": d}}}}  # noqa: E731
    paths, schemas = {}, {"Error": {"type": "object", "required": ["code", "message"], "properties": {"code": {"type": "integer", "format": "int32", "description": "Application error code"}, "message": {"type": "string", "description": "Human readable message"}}}}
    for ri, res in enumerate(resources):
        T = res.capitalize()
        schemas[T] = {"type": "object", "required": ["name", "status"], "description": f"A {res}.", "xml": {"name": res}, "properties": {
            "id": {"type": "integer", "format": "int64", "example": 10 + ri}, "name": {"type": "string", "description": f"The {res} name", "example": f"my {res}"},
            "status": {"type": "string", "description": f"{T} status", "enum": ["available", "pending", "sold"]}, "tags": {"type": "array", "items": {"$ref": "#/components/schemas/Tag"}},
            "owner": {"$ref": "#/components/schemas/User"}, "created": {"type": "string", "format": "date-time"}}, "x-internal-table": f"t_{res}"}
        paths[f"/{res}"] = {"get": {"tags": [res], "summary": f"List {res}s", "description": f"List {res}s", "operationId": f"list{T}s", "x-codegen": {"pager": True},
                                    "parameters": [{"name": "limit", "in": "query", "description": "page size", "required": False, "schema": {"type": "integer", "format": "int32", "maximum": 100}},
                                                   {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["available", "pending", "sold"]}}],
                                    "responses": {"200": {"description": "ok", "content": ok_json({"type": "array", "items": {"$ref": f"#/components/schemas/{T}"}})}, "400": e4("Invalid parameters"), "default": e4("Unexpected error")}},
                            "post": {"tags": [res], "summary": f"Create a {res}", "operationId": f"create{T}", "security": [{"petstore_auth": ["write:pets"]}],
                                     "requestBody": {"description": f"{T} to create", "required": True, "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{T}"}}, "application/xml": {"schema": {"$ref": f"#/components/schemas/{T}"}}}},
                                     "responses": {"201": {"description": "created", "content": ok_json({"$ref": f"#/components/schemas/{T}"})}, "400": e4("Invalid input"), "422": e4("Validation exception")}}}
        paths[f"/{res}/{{{res}Id}}"] = {"parameters": [{"name": f"{res}Id", "in": "path", "description": f"ID of the {res}", "required": True, "schema": {"type": "integer", "format": "int64"}}],
                                        "get": {"tags": [res], "summary": f"Find {res} by ID", "description": f"Find {res} by ID", "operationId": f"get{T}ById", "responses": {"200": {"description": "ok", "content": ok_json({"$ref": f"#/components/schemas/{T}"})}, "404": e4(f"{T} not found"), "default": e4("Unexpected error")}},
                                        "delete": {"tags": [res], "summary": f"Delete a {res}", "operationId": f"delete{T}", "parameters": [{"name": "api_key", "in": "header", "required": False, "schema": {"type": "string"}}],
                                                   "responses": {"204": {"description": "deleted"}, "404": e4(f"{T} not found")}}}
    schemas["Tag"] = {"type": "object", "properties": {"id": {"type": "integer", "format": "int64"}, "name": {"type": "string"}}}
    schemas["User"] = {"type": "object", "properties": {"id": {"type": "integer", "format": "int64"}, "username": {"type": "string"}, "email": {"type": "string", "format": "email"}}}
    schemas["Inventory"] = {"type": "object", "additionalProperties": {"type": "integer", "format": "int32"}}
    paths["/inventory"] = {"get": {"summary": "Returns inventories by status", "operationId": "getInventory", "responses": {"200": {"description": "ok", "content": ok_json({"$ref": "#/components/schemas/Inventory"})}}}}
    return {"openapi": "3.0.2", "info": {"title": "Swagger Petstore - OpenAPI 3.0", "version": "1.0.11", "description": "This is a sample Pet Store Server based on the OpenAPI 3.0 specification. " * 3,
                                         "termsOfService": "http://swagger.io/terms/", "contact": {"email": "apiteam@swagger.io"}, "license": {"name": "Apache 2.0", "url": "http://www.apache.org/licenses/LICENSE-2.0.html"}},
            "externalDocs": {"description": "Find out more about Swagger", "url": "http://swagger.io"}, "servers": [{"url": "/api/v3"}], "tags": [{"name": r, "description": f"Everything about {r}s"} for r in resources],
            "paths": paths, "components": {"schemas": schemas, "securitySchemes": {"petstore_auth": {"type": "oauth2", "flows": {"implicit": {"authorizationUrl": "https://petstore3.swagger.io/oauth/authorize", "scopes": {"write:pets": "modify pets in your account", "read:pets": "read your pets"}}}}}}}


_OAS_PET = jp(_spec(["pet", "order", "user"]))
_OAS_BIG = jp(_spec(["pet", "order", "user", "invoice", "shipment", "customer", "product", "review", "coupon", "warehouse", "supplier", "ticket"]))
_SWAGGER2 = jc({"swagger": "2.0", "info": {"title": "Petstore", "version": "1.0.7"}, "basePath": "/v2", "paths": {"/pet/{petId}": {"get": {"summary": "Find pet by ID", "operationId": "getPetById", "parameters": [{"name": "petId", "in": "path", "required": True, "type": "integer", "format": "int64"}], "responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Pet"}}}}}}})
_OAS_BAD_NAME = jp({**_spec(["pet"]), "paths": {"/pet": {"get": {"operationId": "x y", "parameters": [{"name": "a b", "in": "query"}], "responses": {"200": {"description": "ok"}}}}}})

case(
    135,
    claim="An OpenAPI 3.x JSON spec becomes one line per operation (method, path, operationId, name:in:type* parameters, @body, status:schema responses, summary) plus one line per component schema (properties with * for required, enums, arrays, maps) and the shared error schema named once; every endpoint, parameter, required flag, response status and success schema, and every schema property and enum value is unchanged.",
    loses="examples, descriptions (summaries are kept), x- extensions, tags, security requirements and scheme definitions, servers and info beyond title/version, constraints (maximum, pattern, minLength), non-JSON media types beyond the first, a response schema that equals the shared error schema (it is implied by the header), and the structure of inline object schemas (shown as 'object'); specs with unusual names are left unchanged",
    encode=openapi_digest,
    probes=safe_probes(probes_135),
    fixtures=lambda: [
        Fixture("Petstore-style spec, 3 resources (first op at the start, user ops last)", _OAS_PET, True),
        Fixture("12-resource spec, 74 operations", _OAS_BIG, True),
        Fixture("Swagger 2.0 JSON is another dialect: left alone", _SWAGGER2, False),
        Fixture("spec with a whitespace operationId and parameter name is refused", _OAS_BAD_NAME, False),
        Fixture("not an OpenAPI document", jp({"name": "app", "paths": ["a", "b"]}), False),
        Fixture("a digest-looking input", "# openapi 3.0.0 | x 1\nGET /a [a] () -> 200\n", None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #322 Render .ipynb as percent-format cells, cap text outputs, stub images.
# '# %% <index> <type> [ec=N] [id=X]' per cell, sources verbatim (markdown and raw cells commented with '# '), outputs as '#|' header
# lines with '#:' content lines (text capped to the first 8 and last 4 lines when longer than 12) and blob stubs for images/html.

NB_CAP, NB_HEAD, NB_TAIL = 12, 8, 4
_SAFE = re.compile(r"[^\s]+\Z")


class _Refuse(Exception):
    pass


def _tok(x) -> str:
    return _norm(x).replace(" ", "_") or "-"


def _ev(x) -> str:
    return _norm(x)[:200].rstrip()


def _src_lines(c: dict) -> list[str]:
    src = c.get("source", "")
    src = "".join(src) if isinstance(src, list) else src
    if not isinstance(src, str):
        raise _Refuse
    return src.splitlines()


def _mimetext(v) -> str:
    v = "".join(v) if isinstance(v, list) else v
    if not isinstance(v, str):
        raise _Refuse
    return v


def _text_block(head: str, text: str) -> list[str]:
    ls = text.splitlines()
    shown = [f"#: {x}".rstrip() if x else "#:" for x in ls]
    if len(ls) > NB_CAP:
        shown = shown[:NB_HEAD] + [f"#~ {len(ls) - NB_HEAD - NB_TAIL} lines omitted"] + shown[-NB_TAIL:]
    return [f"#| {head} {len(ls)}"] + shown


@total
def render_notebook(text: str) -> str:
    nb = jload(text)
    if not (isinstance(nb, dict) and nb.get("nbformat") == 4 and isinstance(nb.get("cells"), list)):
        return text
    try:
        meta = nb.get("metadata", {})
        ks, li = meta.get("kernelspec", {}), meta.get("language_info", {})
        out = [f"# %% notebook nbformat=4.{_tok(nb.get('nbformat_minor', 0))} kernel={_tok(ks.get('name', '-'))} lang={_tok(li.get('name', '-'))} {_tok(li.get('version', '-'))}"]
        for i, c in enumerate(nb["cells"]):
            ct = c.get("cell_type")
            if ct not in ("code", "markdown", "raw"):
                raise _Refuse
            ec = c.get("execution_count") if ct == "code" else None
            cid = c.get("id")
            if not (ec is None or (isinstance(ec, int) and not isinstance(ec, bool))) or not (cid is None or (isinstance(cid, str) and _SAFE.match(cid))):
                raise _Refuse
            out.append(f"# %% {i} {ct}" + (f" ec={ec}" if ec is not None else "") + (f" id={cid}" if cid else ""))
            for ln in _src_lines(c):
                if ct == "code":
                    if ln.startswith(("# %%", "#|", "#:", "#~")):
                        raise _Refuse  # would be read back as structure
                    out.append(ln)
                else:
                    out.append(f"# {ln}" if ln else "#")
            for o in c.get("outputs", []) if ct == "code" else []:
                kind = o.get("output_type")
                if kind == "stream":
                    if not _SAFE.match(str(o.get("name", ""))):
                        raise _Refuse
                    out += _text_block(f"stream {o['name']}", _mimetext(o.get("text", "")))
                elif kind in ("execute_result", "display_data", "update_display_data"):
                    oec = o.get("execution_count")
                    if not (oec is None or (isinstance(oec, int) and not isinstance(oec, bool))):
                        raise _Refuse
                    for mime in sorted(o.get("data", {})):
                        if not _SAFE.match(mime):
                            raise _Refuse
                        v = _mimetext(o["data"][mime])
                        who = f"result {oec if oec is not None else '-'}" if kind == "execute_result" else "display"
                        if mime == "text/plain":
                            out += _text_block(f"{who} text/plain", v)
                        else:
                            out.append(f"#| blob {who} {mime} {len(v.rstrip(chr(10)))}")
                elif kind == "error":
                    if not _SAFE.match(str(o.get("ename", ""))):
                        raise _Refuse
                    out.append(f"#| error {o['ename']} {_ev(o.get('evalue', ''))}".rstrip())
                else:
                    raise _Refuse
    except (_Refuse, KeyError, TypeError, AttributeError):
        return text
    return gate(text, "\n".join(out) + "\n")


def _cap_lines(text: str) -> tuple:
    ls = text.splitlines()
    return (len(ls), tuple(ls if len(ls) <= NB_CAP else ls[:NB_HEAD] + ls[-NB_TAIL:]))


def probes_322(text: str) -> dict:
    nb = jload(text)
    if isinstance(nb, dict) and nb.get("nbformat") == 4 and isinstance(nb.get("cells"), list):
        cells = []
        for i, c in enumerate(nb["cells"]):
            outs = []
            for o in c.get("outputs", []) if c["cell_type"] == "code" else []:
                k = o["output_type"]
                if k == "stream":
                    outs.append(("stream", o["name"], _cap_lines(_mimetext(o.get("text", "")))))
                elif k == "error":
                    outs.append(("error", o["ename"], _ev(o.get("evalue", ""))))
                else:
                    who = "result" if k == "execute_result" else "display"
                    for mime in sorted(o.get("data", {})):
                        v = _mimetext(o["data"][mime])
                        outs.append((who, o.get("execution_count") if who == "result" else None, mime, _cap_lines(v) if mime == "text/plain" else len(v.rstrip("\n"))))
            src = _src_lines(c)
            cells.append({"index": i, "type": c["cell_type"], "ec": c.get("execution_count") if c["cell_type"] == "code" else None, "id": c.get("id"), "source": src, "outputs": outs})
        ks, li = nb.get("metadata", {}).get("kernelspec", {}), nb.get("metadata", {}).get("language_info", {})
        return {"notebook": (f"4.{_tok(nb.get('nbformat_minor', 0))}", _tok(ks.get("name", "-")), _tok(li.get("name", "-")), _tok(li.get("version", "-"))), "cells": cells}
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the encoding ends with a newline
    if not lines or not lines[0].startswith("# %% notebook "):
        return {"notebook": None}
    m = re.match(r"# %% notebook nbformat=(\S+) kernel=(\S+) lang=(\S+) (\S+)$", lines[0])
    cells, cur, outs, last = [], None, None, None
    for ln in lines[1:]:
        if h := re.match(r"# %% (\d+) (code|markdown|raw)(?: ec=(-?\d+))?(?: id=(\S+))?$", ln):
            cur = {"index": int(h.group(1)), "type": h.group(2), "ec": int(h.group(3)) if h.group(3) else None, "id": h.group(4), "source": [], "outputs": []}
            cells.append(cur)
            last = None
        elif cur is None:
            continue
        elif ln.startswith("#| "):
            f = ln[3:].split(" ")
            if f[0] == "stream":
                last = ["stream", f[1], int(f[2]), [], None]
            elif f[0] == "error":
                cur["outputs"].append(("error", f[1], " ".join(f[2:])))
                last = None
                continue
            elif f[0] == "blob":
                if f[1] == "result":
                    cur["outputs"].append(("result", None if f[2] == "-" else int(f[2]), f[3], int(f[4])))
                else:
                    cur["outputs"].append(("display", None, f[2], int(f[3])))
                last = None
                continue
            else:  # result <ec> text/plain <n> | display text/plain <n>
                last = [f[0], f[1] if f[0] == "result" else None, f[-2], int(f[-1]), [], None]
                if f[0] == "result":
                    last[1] = None if f[1] == "-" else int(f[1])
            cur["outputs"].append(last)
        elif ln.startswith("#~ ") and last is not None:
            continue
        elif ln.startswith("#:") and last is not None:
            last[-2 if last[0] != "stream" else 3].append(ln[3:] if ln.startswith("#: ") else "")
        elif cur["type"] != "code":
            cur["source"].append(ln[2:] if ln.startswith("# ") else "")
        else:
            cur["source"].append(ln)
    for c in cells:
        c["outputs"] = [(("stream", o[1], (o[2], tuple(o[3]))) if o[0] == "stream" else (o[0], o[1], o[2], (o[3], tuple(o[4])))) if isinstance(o, list) else o for o in c["outputs"]]
    return {"notebook": (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None, "cells": cells}


def _png_b64(seed: int, n: int) -> str:
    return base64.b64encode(bytes((i * 31 + seed) % 256 for i in range(n))).decode() + "\n"


def _src(s: str) -> list[str]:
    ls = s.split("\n")
    return [x + "\n" for x in ls[:-1]] + ([ls[-1]] if ls[-1] else [])


def _notebook() -> str:
    tq = "".join(f"\r  {p:3d}%|{'#' * (p // 5):<20}| {p}/100 [00:{p % 60:02d}<00:00, 12.5it/s]" for p in range(0, 101, 2))
    cells = [
        {"cell_type": "markdown", "id": "a1b2c3d4", "metadata": {}, "source": _src("# Churn analysis\nLoad the **customer** table and look at tenure.\n\n- source: `data/churn.csv`")},
        {"cell_type": "code", "execution_count": 1, "id": "e5f6a7b8", "metadata": {}, "outputs": [], "source": _src("import pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt")},
        {"cell_type": "code", "execution_count": 2, "id": "09c1d2e3", "metadata": {}, "outputs": [{"name": "stdout", "output_type": "stream", "text": _src("loaded 7043 rows\n" + "".join(f"column {i}: float64\n" for i in range(40)))}],
         "source": _src('df = pd.read_csv("data/churn.csv")\nprint(f"loaded {len(df)} rows")\nfor i, c in enumerate(df.columns):\n    print(f"column {i}: {df[c].dtype}")')},
        {"cell_type": "code", "execution_count": 3, "id": "f4a5b6c7", "metadata": {}, "outputs": [
            {"data": {"image/png": _png_b64(1, 30000), "text/plain": ["<Figure size 640x480 with 1 Axes>"]}, "metadata": {"needs_background": "light"}, "output_type": "display_data"}],
         "source": _src('df["tenure"].hist(bins=30)\nplt.title("Tenure (months)")\nplt.show()')},
        {"cell_type": "code", "execution_count": 4, "id": "d8e9f0a1", "metadata": {}, "outputs": [
            {"data": {"text/html": _src("<div><style>.dataframe tbody tr th { vertical-align: top; }</style><table class=\"dataframe\">" + "<tr><td>x</td></tr>" * 80 + "</table></div>"), "text/plain": ["   tenure  charges\n", "0      12     29.9\n", "1      34     56.9"]},
                     "execution_count": 4, "metadata": {}, "output_type": "execute_result"}], "source": _src("df.head(3)")},
        {"cell_type": "code", "execution_count": 5, "id": "11aa22bb", "metadata": {}, "outputs": [{"name": "stderr", "output_type": "stream", "text": [tq + "\n"]}], "source": _src("from tqdm import tqdm\nfor _ in tqdm(range(100)):\n    pass")},
        {"cell_type": "code", "execution_count": 6, "id": "33cc44dd", "metadata": {}, "outputs": [
            {"ename": "ValueError", "evalue": "could not convert string to float: ' '", "output_type": "error",
             "traceback": ["\u001b[0;31m---------------------------------------------------------------------------\u001b[0m", "\u001b[0;31mValueError\u001b[0m                                Traceback (most recent call last)",
                           "Cell \u001b[0;32mIn[6], line 2\u001b[0m\n\u001b[1;32m      1\u001b[0m x \u001b[38;5;241m=\u001b[39m df[\u001b[38;5;124m'\u001b[39m\u001b[38;5;124mTotalCharges\u001b[39m\u001b[38;5;124m'\u001b[39m]\n\u001b[0;32m----> 2\u001b[0m \u001b[43mx\u001b[49m\u001b[38;5;241m.\u001b[39mastype(\u001b[38;5;28mfloat\u001b[39m)\n",
                           "\u001b[0;31mValueError\u001b[0m: could not convert string to float: ' '"]}],
         "source": _src("x = df['TotalCharges']\nx.astype(float)")},
        {"cell_type": "code", "execution_count": 7, "id": "55ee66ff", "metadata": {}, "outputs": [{"data": {"image/png": _png_b64(5, 24000), "text/plain": ["<Figure size 800x600 with 1 Axes>"]}, "metadata": {}, "output_type": "display_data"}],
         "source": _src('df["TotalCharges"] = pd.to_numeric(df["TotalCharges"], errors="coerce")\ndf.boxplot("TotalCharges", by="Churn")')},
        {"cell_type": "code", "execution_count": 8, "id": "77aa88bb", "metadata": {}, "outputs": [{"data": {"text/plain": ["0.2654"]}, "execution_count": 8, "metadata": {}, "output_type": "execute_result"}], "source": _src('(df["Churn"] == "Yes").mean()')},
        {"cell_type": "markdown", "id": "99cc00dd", "metadata": {}, "source": _src("## Result\nChurn is **26.5%**.")},
        {"cell_type": "code", "execution_count": None, "id": "ab12cd34", "metadata": {}, "outputs": [], "source": []}]
    return jp({"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}, "language_info": {"codemirror_mode": {"name": "ipython", "version": 3}, "file_extension": ".py",
               "mimetype": "text/x-python", "name": "python", "nbconvert_exporter": "python", "pygments_lexer": "ipython3", "version": "3.11.7"}}, "nbformat": 4, "nbformat_minor": 5}) + "\n"


_NB = _notebook()
_NB_TINY = jp({"cells": [{"cell_type": "code", "execution_count": 1, "id": "c1", "metadata": {}, "outputs": [{"name": "stdout", "output_type": "stream", "text": ["hello\n"]}], "source": ["print('hello')"]}],
               "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python", "version": "3.12.1"}}, "nbformat": 4, "nbformat_minor": 5})
_NB_MARKER = json.dumps({**json.loads(_NB), "cells": json.loads(_NB)["cells"][:2] + [{"cell_type": "code", "execution_count": 9, "metadata": {}, "outputs": [], "source": ["# %% jupytext marker\n", "x = 1\n"]}]}, indent=1)
_NB_PIPE = json.dumps({**json.loads(_NB), "cells": [{"cell_type": "code", "execution_count": 1, "metadata": {}, "outputs": [], "source": ["#| not an output line\n", "y = 2\n"]}]}, indent=1)
_NB_V3 = jp({"nbformat": 3, "nbformat_minor": 0, "worksheets": [{"cells": [{"cell_type": "code", "input": ["1+1"], "outputs": [], "language": "python"}]}], "metadata": {}})

case(
    322,
    claim="A nbformat-4 notebook becomes '# %% <index> <type> ec=N id=X' cells with verbatim source (markdown commented), text outputs capped to 8+4 lines with the total count, image/html outputs stubbed to mime and size, errors reduced to ename and evalue; cell index and id, execution count, every source line, every stream/result text line that is shown, image count and sizes, and every error are unchanged.",
    loses="base64 image bytes and text/html bodies (size only), error tracebacks (ename and evalue only), the middle of text outputs longer than 12 lines, cell and notebook metadata other than kernel name and language version, whether a source ended with a newline, and JSON whitespace; notebooks with source lines starting '# %%', '#|', '#:' or '#~' are left unchanged",
    encode=render_notebook,
    probes=safe_probes(probes_322),
    fixtures=lambda: [
        Fixture("11-cell analysis notebook: 2 images, tqdm bar, DataFrame html, ValueError mid-way, result last", _NB, True),
        Fixture("one-cell notebook", _NB_TINY, True),
        Fixture("notebook with a jupytext '# %%' marker in a code cell", _NB_MARKER, False),
        Fixture("nbformat 3 worksheet notebook", _NB_V3, False),
        Fixture("package.json", PACKAGE_JSON, False),
        Fixture("a code line starting '#|'", _NB_PIPE, None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #324 Rich-text AST JSON to Markdown: Atlassian Document Format, Notion blocks, Slack rich_text duplicates.
# Inside any JSON document, an ADF doc object becomes the string "[md] <markdown>", a Notion block list (results of block objects)
# becomes one such string, and a Slack message's `blocks` is dropped when it renders to exactly the message's `text`. Node types the
# converter does not know pass through as ⟦compact json⟧, never silently dropped.

MD = "[md] "
UNKNOWN_L, UNKNOWN_R = "⟦", "⟧"
_TOKEN = re.compile(r"[^\W_]+")


def _toks(s) -> list[str]:
    return _TOKEN.findall(str(s))


class _Odd(Exception):
    """A value the converter cannot render safely; the whole document is then left alone."""


def _lead(line: str) -> str:
    return "\\" + line if line[:1] in "#>" or line.startswith("```") else line


def _unknown(n) -> str:
    return UNKNOWN_L + jc(n) + UNKNOWN_R


def _adf_text(n: dict) -> str:
    s = str(n.get("text", "")).replace("\r", " ").replace("\n", " ")
    marks = {m.get("type"): m for m in n.get("marks", []) if isinstance(m, dict)}
    if "code" in marks:
        s = f"`{s}`"
    if "strong" in marks:
        s = f"**{s}**"
    if "em" in marks:
        s = f"*{s}*"
    if "strike" in marks:
        s = f"~~{s}~~"
    if "link" in marks:
        href = str(marks["link"].get("attrs", {}).get("href", ""))
        if not href or re.search(r"[<>)\s]", href):
            raise _Odd
        s = f"[{s}](<{href}>)"
    return s


def _adf_inline(nodes) -> str:
    out = []
    for n in nodes:
        t = n.get("type")
        a = n.get("attrs", {}) if isinstance(n.get("attrs"), dict) else {}
        if t == "text":
            out.append(_adf_text(n))
        elif t == "hardBreak":
            out.append("\n")
        elif t == "mention" and (a.get("text") or a.get("id")):
            out.append(str(a.get("text") or a.get("id")).replace("\n", " "))
        elif t == "emoji" and (a.get("shortName") or a.get("text")):
            out.append(str(a.get("shortName") or a.get("text")))
        elif t == "inlineCard" and a.get("url") and not re.search(r"[<>\s]", str(a["url"])):
            out.append(f"<{a['url']}>")
        elif t == "status" and a.get("text"):
            out.append(f"[{str(a['text']).replace(chr(10), ' ')}]")
        else:
            out.append(_unknown(n))
    return "".join(out)


def _adf_blocks(nodes) -> list[str]:
    lines: list[str] = []
    for n in nodes:
        t, kids = n.get("type"), n.get("content", [])
        a = n.get("attrs", {}) if isinstance(n.get("attrs"), dict) else {}
        if t == "paragraph":
            lines += [_lead(x) for x in _adf_inline(kids).split("\n")] + [""]
        elif t == "heading" and isinstance(a.get("level"), int) and 1 <= a["level"] <= 6:
            lines += ["#" * a["level"] + " " + _adf_inline(kids).replace("\n", " "), ""]
        elif t in ("bulletList", "orderedList"):
            start = a.get("order", 1) if isinstance(a.get("order", 1), int) else 1
            for k, item in enumerate(kids):
                if item.get("type") != "listItem":
                    lines.append(_unknown(item))
                    continue
                body = _adf_blocks(item.get("content", []))
                while body and body[-1] == "":
                    body.pop()
                mark = "- " if t == "bulletList" else f"{start + k}. "
                lines += [(mark if j == 0 else ("  " if x else "")) + x for j, x in enumerate(body or [""])]
            lines.append("")
        elif t == "codeBlock":
            code = "".join(c.get("text", "") for c in kids if isinstance(c, dict)).split("\n")
            fence = "`" * max(3, max((len(m) for x in code for m in re.findall(r"`+", x)), default=0) + 1)
            lang = str(a.get("language", ""))
            if re.search(r"\s", lang):
                raise _Odd
            lines += [fence + lang] + code + [fence, ""]
        elif t == "blockquote":
            body = _adf_blocks(kids)
            while body and body[-1] == "":
                body.pop()
            lines += ["> " + x if x else ">" for x in body] + [""]
        elif t == "rule":
            lines += ["---", ""]
        elif t == "panel" and isinstance(a.get("panelType"), str) and not re.search(r"\s", a["panelType"]):
            body = _adf_blocks(kids)
            while body and body[-1] == "":
                body.pop()
            lines += [("> [!" + a["panelType"] + "] " if j == 0 else "> ") + x for j, x in enumerate(body or [""])] + [""]
        elif t == "table":
            for r, row in enumerate(kids):
                cells = [" ".join(_adf_blocks(c.get("content", []))).strip().replace("|", "\\|") for c in row.get("content", [])]
                lines.append("| " + " | ".join(cells) + " |")
                if r == 0:
                    lines.append("|" + " --- |" * len(cells))
            lines.append("")
        elif t in ("mediaSingle", "mediaGroup"):
            for m in kids:
                ma = m.get("attrs", {}) if isinstance(m.get("attrs"), dict) else {}
                lines.append(f"[media {ma.get('type', 'file')} {ma.get('id', '')}]".replace("\n", " "))
            lines.append("")
        else:
            lines += [_unknown(n), ""]
    return lines


def adf_to_md(doc: dict) -> str:
    lines = _adf_blocks(doc.get("content", []))
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


_NOTION_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do", "code", "quote", "divider", "callout")


def _notion_rich(rt) -> str:
    out = []
    for r in rt:
        s = str(r.get("plain_text", "")).replace("\r", " ").replace("\n", " ")
        an = r.get("annotations", {}) if isinstance(r.get("annotations"), dict) else {}
        if an.get("code"):
            s = f"`{s}`"
        if an.get("bold"):
            s = f"**{s}**"
        if an.get("italic"):
            s = f"*{s}*"
        if an.get("strikethrough"):
            s = f"~~{s}~~"
        href = r.get("href")
        if href:
            if not isinstance(href, str) or re.search(r"[<>)\s]", href):
                raise _Odd
            s = f"[{s}](<{href}>)"
        out.append(s)
    return "".join(out)


def notion_to_md(blocks: list) -> str:
    lines, num = [], 0
    for b in blocks:
        t = b.get("type")
        body = b.get(t) if isinstance(b.get(t), dict) else {}
        num = num + 1 if t == "numbered_list_item" else 0
        if t not in _NOTION_TYPES or not isinstance(body.get("rich_text", []), list):
            lines += [_unknown(b), ""]
            continue
        text = _notion_rich(body.get("rich_text", []))
        if t == "paragraph":
            lines += [_lead(x) for x in text.split("\n")] + [""]
        elif t.startswith("heading_"):
            lines += ["#" * int(t[-1]) + " " + text, ""]
        elif t == "bulleted_list_item":
            lines.append("- " + text)
        elif t == "numbered_list_item":
            lines.append(f"{num}. {text}")
        elif t == "to_do":
            lines.append(("- [x] " if body.get("checked") else "- [ ] ") + text)
        elif t == "quote":
            lines += ["> " + text, ""]
        elif t == "divider":
            lines += ["---", ""]
        elif t == "callout":
            lines += ["> " + (str((body.get("icon") or {}).get("emoji", "")) + " " if isinstance(body.get("icon"), dict) else "") + text, ""]
        elif t == "code":
            code = "".join(str(r.get("plain_text", "")) for r in body.get("rich_text", [])).split("\n")
            fence = "`" * max(3, max((len(m) for x in code for m in re.findall(r"`+", x)), default=0) + 1)
            lang = str(body.get("language", ""))
            if re.search(r"\s", lang):
                raise _Odd
            lines += [fence + lang] + code + [fence, ""]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _slack_text(el: dict) -> str:
    """Render a Slack rich_text tree the way Slack builds the message's mrkdwn `text`."""
    t = el.get("type")
    if t == "text":
        s = str(el.get("text", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        st = el.get("style", {}) if isinstance(el.get("style"), dict) else {}
        for key, ch in (("code", "`"), ("bold", "*"), ("italic", "_"), ("strike", "~")):
            if st.get(key):
                s = ch + s + ch
        return s
    if t == "user":
        return f"<@{el.get('user_id')}>"
    if t == "channel":
        return f"<#{el.get('channel_id')}>"
    if t == "emoji":
        return f":{el.get('name')}:"
    if t == "link":
        return f"<{el.get('url')}|{el.get('text')}>" if el.get("text") else f"<{el.get('url')}>"
    if t == "rich_text_section":
        return "".join(_slack_text(x) for x in el.get("elements", []))
    if t == "rich_text":
        return "\n".join(_slack_text(x) for x in el.get("elements", []))
    raise _Odd


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _convert(o):
    """Returns (new value, changed)."""
    if isinstance(o, dict):
        if o.get("type") == "doc" and isinstance(o.get("content"), list) and isinstance(o.get("version"), int):
            return MD + adf_to_md(o), True
        changed = False
        res = {}
        for k, v in o.items():
            if k == "results" and isinstance(v, list) and v and all(isinstance(b, dict) and b.get("object") == "block" for b in v):
                res[k], changed = MD + notion_to_md(v), True
            elif k == "blocks" and isinstance(v, list) and isinstance(o.get("text"), str) and v and all(isinstance(b, dict) and b.get("type") == "rich_text" for b in v):
                try:
                    same = _same_text(o["text"], "\n".join(_slack_text(b) for b in v))
                except _Odd:
                    same = False
                if same:
                    changed = True
                    continue
                res[k] = v
            else:
                nv, ch = _convert(v)
                res[k], changed = nv, changed or ch
        return res, changed
    if isinstance(o, list):
        pairs = [_convert(x) for x in o]
        return [p[0] for p in pairs], any(p[1] for p in pairs)
    return o, False


@total
def richtext_to_markdown(text: str) -> str:
    o = jload(text)
    if o is _BAD:
        return text
    try:
        new, changed = _convert(o)
    except (_Odd, AttributeError, TypeError):
        return text
    return gate(text, jc(new)) if changed else text


def _adf_tokens(n, out: list, facts: dict) -> None:
    """Probe-side walk of an ADF node: appends the word tokens a reader sees, in order, and records headings and links."""
    if isinstance(n, list):
        for x in n:
            _adf_tokens(x, out, facts)
        return
    t, a = n.get("type"), (n.get("attrs") if isinstance(n.get("attrs"), dict) else {})
    if t == "text":
        out += _toks(n.get("text", ""))
        for m in n.get("marks", []):
            if m.get("type") == "link":
                out += _toks(m["attrs"]["href"])
                facts["links"].append(" ".join(_toks(m["attrs"]["href"])))
        return
    known = {"doc", "paragraph", "heading", "bulletList", "orderedList", "listItem", "codeBlock", "blockquote", "rule", "hardBreak", "mention", "emoji", "inlineCard", "status", "panel", "table",
             "tableRow", "tableCell", "tableHeader", "mediaSingle", "mediaGroup", "media"}
    if t not in known or (t == "mention" and not (a.get("text") or a.get("id"))) or (t == "emoji" and not (a.get("shortName") or a.get("text"))) \
            or (t == "inlineCard" and not a.get("url")) or (t == "status" and not a.get("text")) or (t == "heading" and not (isinstance(a.get("level"), int) and 1 <= a["level"] <= 6)) \
            or (t == "panel" and not isinstance(a.get("panelType"), str)):
        out += _toks(jc(n))
        return
    if t == "mention":
        out += _toks(a.get("text") or a.get("id"))
    elif t == "emoji":
        out += _toks(a.get("shortName") or a.get("text"))
    elif t == "inlineCard":
        out += _toks(a["url"])
        facts["links"].append(" ".join(_toks(a["url"])))
    elif t == "status":
        out += _toks(a["text"])
    elif t == "codeBlock":
        out += _toks(a.get("language", "")) + _toks("".join(c.get("text", "") for c in n.get("content", [])))
    elif t == "panel":
        out += _toks(a["panelType"])
        _adf_tokens(n.get("content", []), out, facts)
    elif t in ("mediaSingle", "mediaGroup"):
        for m in n.get("content", []):
            ma = m.get("attrs", {}) if isinstance(m.get("attrs"), dict) else {}
            out += ["media"] + _toks(ma.get("type", "file")) + _toks(ma.get("id", ""))
    elif t == "orderedList":
        start = a.get("order", 1) if isinstance(a.get("order", 1), int) else 1
        for k, item in enumerate(n.get("content", [])):
            if item.get("type") == "listItem":
                out.append(str(start + k))
            _adf_tokens(item, out, facts)
    elif t == "heading":
        before = len(out)
        _adf_tokens(n.get("content", []), out, facts)
        facts["headings"].append((a["level"], tuple(out[before:])))
    else:
        _adf_tokens(n.get("content", []), out, facts)


def _notion_tokens(blocks: list, out: list, facts: dict) -> None:
    num = 0
    for b in blocks:
        t = b.get("type")
        body = b.get(t) if isinstance(b.get(t), dict) else {}
        num = num + 1 if t == "numbered_list_item" else 0
        if t not in _NOTION_TYPES or not isinstance(body.get("rich_text", []), list):
            out += _toks(jc(b))
            continue
        before = len(out)
        if t == "numbered_list_item":
            out.append(str(num))
        if t == "to_do" and body.get("checked"):
            out.append("x")
        if t == "code":
            out += _toks(body.get("language", "")) + _toks("".join(str(r.get("plain_text", "")) for r in body["rich_text"]))
            continue
        if t == "callout" and isinstance(body.get("icon"), dict):
            out += _toks(body["icon"].get("emoji", ""))
        for r in body.get("rich_text", []):
            out += _toks(r.get("plain_text", ""))
            if r.get("href"):
                out += _toks(r["href"])
                facts["links"].append(" ".join(_toks(r["href"])))
        if t.startswith("heading_"):
            facts["headings"].append((int(t[-1]), tuple(out[before:])))


def _md_facts(md: str) -> dict:
    out, facts, fence = [], {"links": [], "headings": []}, None
    for ln in md.split("\n"):
        if fence is None and (m := re.match(r"(`{3,})(\S*)$", ln)):
            fence = m.group(1)
        elif fence is not None and re.match(r"`{%d,}$" % len(fence), ln):
            fence = None
        elif fence is None and (m := re.match(r"(#{1,6}) (.*)$", ln)):
            facts["headings"].append((len(m.group(1)), tuple(_toks(m.group(2)))))
        if fence is None:
            facts["links"] += [" ".join(_toks(u)) for u in re.findall(r"\]\(<([^>]*)>\)", ln)]
            facts["links"] += [" ".join(_toks(u)) for u in re.findall(r"(?<![\]\w(])<(https?://[^>\s]*)>", ln)]
    out += _toks(md)
    facts["words"] = out
    return facts


def probes_324(text: str) -> dict:
    o = jload(text)
    if o is _BAD:
        return {"document": None}
    docs, slack = [], []
    stack = [("$", o)]
    while stack:
        path, x = stack.pop()
        if isinstance(x, dict):
            if x.get("type") == "doc" and isinstance(x.get("content"), list) and isinstance(x.get("version"), int):
                facts = {"links": [], "headings": []}
                words: list = []
                _adf_tokens(x, words, facts)
                docs.append((path, {**facts, "words": words}))
                continue
            for k, v in x.items():
                if k == "results" and isinstance(v, list) and v and all(isinstance(b, dict) and b.get("object") == "block" for b in v):
                    facts, words = {"links": [], "headings": []}, []
                    _notion_tokens(v, words, facts)
                    docs.append((f"{path}.{k}", {**facts, "words": words}))
                elif k == "results" and isinstance(v, str) and v.startswith(MD):
                    docs.append((f"{path}.{k}", _md_facts(v[len(MD):])))
                else:
                    stack.append((f"{path}.{k}", v))
            if isinstance(x.get("text"), str) and ("blocks" in x or x.get("type") == "message"):
                # what the message says about mentions and links: from the blocks when they are still there, from `text` otherwise
                if isinstance(x.get("blocks"), list):
                    src = "\n".join(_slack_text(b) for b in x["blocks"]) if all(isinstance(b, dict) and b.get("type") == "rich_text" for b in x["blocks"]) else x["text"]
                else:
                    src = x["text"]
                slack.append((path, x["text"], re.findall(r"<@(\w+)>", src), re.findall(r"<(https?://[^|>]+)", src), {k: v for k, v in x.items() if k != "blocks"}))
        elif isinstance(x, str) and x.startswith(MD):
            docs.append((path, _md_facts(x[len(MD):])))
        elif isinstance(x, list):
            stack += [(f"{path}[{i}]", v) for i, v in enumerate(x)]
    return {"rich-text documents (words, headings, links)": sorted(docs, key=lambda d: d[0]), "slack messages (text, mentions, links, other fields)": sorted(slack, key=lambda d: d[0])}


def _adf_doc(seed: int = 1) -> dict:
    t = lambda s, *marks: {"type": "text", "text": s, **({"marks": [{"type": m} if m != "link" else {"type": "link", "attrs": {"href": "https://wiki.acme.test/runbook/db-failover"}} for m in marks]} if marks else {})}  # noqa: E731
    p = lambda *kids: {"type": "paragraph", "content": list(kids)}  # noqa: E731
    li = lambda *kids: {"type": "listItem", "content": [p(*kids)]}  # noqa: E731
    return {"version": 1, "type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [t("Problem")]},
        p(t("Checkout returns "), t("HTTP 502", "code"), t(" for about "), t("3% of requests", "strong"), t(" since the 14:05 deploy. See "), t("the runbook", "link"), t("."), {"type": "hardBreak"}, t("Reported by "), {"type": "mention", "attrs": {"id": "5b10a2844c20165700ede21g", "text": "@Mira Kovacs", "accessLevel": ""}}, t(" "), {"type": "emoji", "attrs": {"shortName": ":fire:", "id": "1f525", "text": "\U0001f525"}}),
        {"type": "heading", "attrs": {"level": 3}, "content": [t("Steps to reproduce")]},
        {"type": "orderedList", "attrs": {"order": 1}, "content": [li(t("Add any item to the cart")), li(t("Press "), t("Pay now", "strong")), li(t("Observe the 502 after about "), t("30s", "em"))]},
        {"type": "bulletList", "content": [li(t("Region: eu-west-1")), {"type": "listItem", "content": [p(t("Affected nodes")), {"type": "bulletList", "content": [li(t("node-7")), li(t("node-12"))]}]}]},
        {"type": "codeBlock", "attrs": {"language": "bash"}, "content": [{"type": "text", "text": "kubectl logs deploy/checkout --since=10m | grep 502\n# expect: upstream connect error"}]},
        {"type": "panel", "attrs": {"panelType": "warning"}, "content": [p(t("Do not restart the pods before the heap dump is taken."))]},
        {"type": "blockquote", "content": [p(t("works on staging"))]},
        {"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default", "localId": "abc"}, "content": [
            {"type": "tableRow", "content": [{"type": "tableHeader", "attrs": {}, "content": [p(t("Node"))]}, {"type": "tableHeader", "attrs": {}, "content": [p(t("p99 ms"))]}]},
            {"type": "tableRow", "content": [{"type": "tableCell", "attrs": {}, "content": [p(t("node-7"))]}, {"type": "tableCell", "attrs": {}, "content": [p(t("4810"))]}]}]},
        {"type": "mediaSingle", "attrs": {"layout": "center"}, "content": [{"type": "media", "attrs": {"id": "a3f9-77c1", "type": "file", "collection": "", "width": 800, "height": 450}}]},
        {"type": "rule"},
        p(t("Status: "), {"type": "status", "attrs": {"text": "In progress", "color": "blue", "localId": "x1"}}, t(" tracked in "), {"type": "inlineCard", "attrs": {"url": "https://acme.atlassian.net/browse/OPS-4411"}}),
        {"type": "taskList", "attrs": {"localId": "t1"}, "content": [{"type": "taskItem", "attrs": {"localId": "t2", "state": "TODO"}, "content": [t("Write the post-mortem")]}]},
        p(t("Final note: rollback is safe to run at any time."))]}


def _jira_issue() -> str:
    comments = [{"id": str(10000 + i), "author": {"accountId": "5b10a2844c20165700ede21g", "displayName": "Mira Kovacs", "active": True, "avatarUrls": {"48x48": "https://avatar.test/48.png", "24x24": "https://avatar.test/24.png"}},
                 "body": {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Comment {i}: checked node-{i}, the 502s continue."}]}]}, "created": f"2025-03-0{1 + i}T10:00:00.000+0000"} for i in range(4)]
    return jp({"expand": "renderedFields,names", "id": "10042", "key": "OPS-4411", "fields": {"summary": "Checkout 502s", "status": {"name": "In Progress", "id": "3"}, "priority": {"name": "High", "id": "2"},
                                                                                       "description": _adf_doc(), "comment": {"comments": comments, "total": 4}, "labels": ["prod", "checkout"]}})


def _notion_blocks() -> str:
    rt = lambda s, **an: {"type": "text", "text": {"content": s, "link": {"url": an.pop("url")} if "url" in an else None}, "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default", **{k: v for k, v in an.items()}},  # noqa: E731
                          "plain_text": s, "href": None}
    def blk(t, i, **body):
        return {"object": "block", "id": hexid(i, 32, "nb"), "parent": {"type": "page_id", "page_id": hexid(0, 32, "pg")}, "created_time": "2025-03-01T10:00:00.000Z", "last_edited_time": "2025-03-02T11:30:00.000Z",
                "created_by": {"object": "user", "id": hexid(1, 32, "u")}, "last_edited_by": {"object": "user", "id": hexid(1, 32, "u")}, "has_children": False, "archived": False, "in_trash": False, "type": t, t: body}
    link = rt("runbook", url="https://wiki.acme.test/runbook")
    link["href"] = "https://wiki.acme.test/runbook"
    res = [blk("heading_1", 1, rich_text=[rt("Incident review")], is_toggleable=False, color="default"), blk("paragraph", 2, rich_text=[rt("See the "), link, rt(" and "), rt("rollback", code=True), rt(".")], color="default"),
           blk("numbered_list_item", 3, rich_text=[rt("Detect")], color="default"), blk("numbered_list_item", 4, rich_text=[rt("Mitigate")], color="default"), blk("to_do", 5, rich_text=[rt("Write post-mortem")], checked=True, color="default"),
           blk("callout", 6, rich_text=[rt("Customers were affected")], icon={"type": "emoji", "emoji": "⚠️"}, color="gray_background"), blk("code", 7, rich_text=[rt("kubectl rollout undo deploy/checkout")], language="shell", caption=[]),
           blk("child_page", 8, title="Sub page"), blk("paragraph", 9, rich_text=[rt("Last paragraph.")], color="default")]
    return jc({"object": "list", "results": res, "next_cursor": None, "has_more": False, "type": "block", "block": {}, "request_id": hexid(4, 32, "rq")})


def _slack_history() -> str:
    def msg(i, text, elements, **extra):
        return {"type": "message", "user": f"U{i:03d}ABCDE", "text": text, "ts": f"17120{i:05d}.000{i:03d}", "team": "T061EG9R6", "client_msg_id": hexid(i, 32, "cm"),
                "blocks": [{"type": "rich_text", "block_id": f"b{i}", "elements": [{"type": "rich_text_section", "elements": elements}]}], **extra}
    T = lambda s, **st: {"type": "text", "text": s, **({"style": st} if st else {})}  # noqa: E731
    ms = [msg(1, "Deploy done, see <https://ci.acme.test/run/88|run 88> :tada:", [T("Deploy done, see "), {"type": "link", "url": "https://ci.acme.test/run/88", "text": "run 88"}, T(" "), {"type": "emoji", "name": "tada"}]),
          msg(2, "<@U007ZED> can you check *the cache* & report?", [{"type": "user", "user_id": "U007ZED"}, T(" can you check "), T("the cache", bold=True), T(" & report?")]),
          msg(3, "pinned: rollback window is 15:00-16:00", [T("pinned: rollback window is 15:00-16:00")], thread_ts="1712000002.000002", reply_count=3),
          msg(4, "see attached", [T("see something else entirely")]),  # text and blocks disagree: the blocks must stay
          msg(5, "_all clear_", [T("all clear", italic=True)])]
    return jc({"ok": True, "messages": ms, "has_more": False, "pin_count": 1, "channel_actions_ts": None})


_PLAIN_JSON = jp({"summary": "no rich text here", "description": "plain string", "fields": {"priority": {"name": "High"}}, "blocks": [{"type": "section", "text": "not rich_text"}]})

case(
    324,
    claim="Inside any JSON document an ADF doc becomes '[md] ' Markdown (headings, lists with numbering, code fences with language, quotes, panels, tables, marks, links, mentions, emoji, cards, status, media), a Notion block list becomes Markdown, and a Slack message's blocks are dropped when they render to exactly its text; the word sequence, heading levels and texts, link targets, and Slack mentions and links are unchanged, and node types the converter does not know survive as compact JSON.",
    loses="ADF marks and attrs with no Markdown form (underline, text colour, sub/superscript, localId, layout, media size), Notion colours, annotations other than bold/italic/strike/code, block ids and timestamps, the structure of the JSON nodes (one Markdown string replaces it), Slack rich_text blocks that duplicate text, and JSON whitespace; documents with link targets containing spaces, angle brackets or parentheses are left unchanged",
    encode=richtext_to_markdown,
    probes=safe_probes(probes_324),
    fixtures=lambda: [
        Fixture("Jira issue: ADF description (all node kinds, a taskList it does not know) plus four ADF comments", _jira_issue(), True),
        Fixture("Notion block list (child_page is unknown)", _notion_blocks(), True),
        Fixture("Slack history: four duplicate blocks, one message whose blocks disagree with its text", _slack_history(), True),
        Fixture("JSON with no rich text; a 'blocks' key that is not rich_text", _PLAIN_JSON, False),
        Fixture("already converted", jc({"description": MD + "# Title\n\nbody"}), False),
        Fixture("ADF with a link target holding a space", jc({"d": {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {"href": "https://a b"}}]}]}]}}), False),
    ],
    min_gain=0.25,
)


# ===============================================================================================================
# #421 OTLP, Jaeger and Zipkin trace JSON to an indented span tree.
# Per trace one header line and one line per span, indented by depth: service TAB name TAB duration TAB start offset TAB status TAB
# attributes (compact JSON, wrappers unwrapped). Ids, scopes, kinds, events and resource attributes other than service.name are dropped.

TRACE_RE = re.compile(r"trace (\S*) \| (\d+) spans \| (\d+)\.(\d)ms$")
SPAN_RE = re.compile(r"( *)([^\t]*)\t([^\t]*)\t(\d+)\.(\d)ms\t\+(\d+)\.(\d)ms\t([^\t]*)\t(.*)$")


def _otlp_val(v) -> str:
    if isinstance(v, dict) and len(v) >= 1:
        k, x = next(iter(v.items()))
        if k in ("stringValue", "intValue", "doubleValue") and isinstance(x, (str, int, float)) and not isinstance(x, bool):
            return str(x)
        if k == "boolValue" and isinstance(x, bool):
            return "true" if x else "false"
    return jc(v)


def _attrs_otlp(lst) -> dict:
    return {str(a["key"]): _otlp_val(a.get("value")) for a in lst or []}


def _spans_of(doc) -> list[dict]:
    """Normalise OTLP / Jaeger / Zipkin documents to dicts: trace, id, parent, name, service, start (us), dur (us), error, msg, attrs."""
    out = []
    if isinstance(doc, dict) and "resourceSpans" in doc:
        for rs in doc["resourceSpans"]:
            ra = _attrs_otlp(rs.get("resource", {}).get("attributes"))
            for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                for sp in ss.get("spans", []):
                    st = sp.get("status") or {}
                    err = st.get("code") in (2, "STATUS_CODE_ERROR")
                    msg = str(st.get("message", ""))
                    if err and not msg:
                        for ev in sp.get("events", []):
                            if ev.get("name") == "exception":
                                ea = _attrs_otlp(ev.get("attributes"))
                                msg = f"{ea.get('exception.type', '')}: {ea.get('exception.message', '')}"
                    t0, t1 = int(sp["startTimeUnixNano"]), int(sp["endTimeUnixNano"])
                    out.append({"trace": sp["traceId"], "id": sp["spanId"], "parent": sp.get("parentSpanId") or None, "name": sp["name"], "service": ra.get("service.name", "?"),
                                "start": t0 // 1000, "dur": (t1 - t0) // 1000, "error": err, "msg": msg, "attrs": _attrs_otlp(sp.get("attributes"))})
    elif isinstance(doc, dict) and isinstance(doc.get("data"), list):
        for tr in doc["data"]:
            procs = tr.get("processes", {})
            for sp in tr["spans"]:
                tags = {str(t["key"]): t["value"] for t in sp.get("tags", [])}
                err = tags.get("error") in (True, "true", 1) or tags.get("otel.status_code") == "ERROR"
                refs = sp.get("references") or []
                out.append({"trace": sp["traceID"], "id": sp["spanID"], "parent": refs[0]["spanID"] if refs else None, "name": sp["operationName"],
                            "service": procs.get(sp.get("processID"), {}).get("serviceName", "?"), "start": int(sp["startTime"]), "dur": int(sp["duration"]), "error": bool(err),
                            "msg": str(tags.get("otel.status_description", "")), "attrs": {k: (v if isinstance(v, str) else jc(v)) for k, v in tags.items() if k not in ("error", "otel.status_description")}})
    elif isinstance(doc, list) and doc and all(isinstance(s, dict) and "traceId" in s and "id" in s for s in doc):
        for sp in doc:
            tags = sp.get("tags") or {}
            out.append({"trace": sp["traceId"], "id": sp["id"], "parent": sp.get("parentId") or None, "name": sp.get("name", ""), "service": (sp.get("localEndpoint") or {}).get("serviceName", "?"),
                        "start": int(sp["timestamp"]), "dur": int(sp["duration"]), "error": "error" in tags, "msg": str(tags.get("error", "")), "attrs": {str(k): str(v) for k, v in tags.items() if k != "error"}})
    return out


def _tenths(us: int) -> str:
    n = us // 100
    return f"{n // 10}.{n % 10}ms"


@total
def render_trace_tree(text: str) -> str:
    doc = jload(text)
    spans = _spans_of(doc) if doc is not _BAD else []
    if not spans:
        return text
    traces: dict[str, list[dict]] = {}
    for s in spans:
        traces.setdefault(str(s["trace"]), []).append(s)
    out = []
    for tid, sp in traces.items():
        ids = [s["id"] for s in sp]
        if len(set(ids)) != len(ids) or any(s["dur"] < 0 for s in sp):
            return text
        base = min(s["start"] for s in sp)
        window = max(s["start"] + s["dur"] for s in sp) - base
        by_id = {s["id"]: s for s in sp}
        kids: dict = defaultdict(list)
        roots = []
        for s in sp:
            (kids[s["parent"]] if s["parent"] in by_id and s["parent"] != s["id"] else roots).append(s)
        order = lambda l: sorted(l, key=lambda s: (s["start"], str(s["id"])))  # noqa: E731
        stack = [(s, 0) for s in reversed(order(roots))]
        lines, seen = [], 0
        while stack:
            s, depth = stack.pop()
            seen += 1
            status = "-" if not s["error"] else "ERROR" + (":" + _norm(s["msg"]) if _norm(s["msg"]) else "")
            if s["start"] < base:
                return text
            lines.append("  " * depth + "\t".join([_norm(s["service"]) or "-", _norm(s["name"]) or "-", _tenths(s["dur"]), "+" + _tenths(s["start"] - base), status,
                                                   jc({str(k): _norm(v) for k, v in s["attrs"].items()}) if s["attrs"] else "-"]))
            stack += [(c, depth + 1) for c in reversed(order(kids[s["id"]]))]
        if seen != len(sp):  # a parent cycle hides spans from the walk
            return text
        out.append(f"trace {_norm(tid)} | {len(sp)} spans | {_tenths(window)}")
        out += lines
    return gate(text, "\n".join(out) + "\n")


def _probe_spans(text: str):
    """Spans as (trace, depth, service, name, dur in 0.1ms, offset in 0.1ms, status, attrs) from the original or from the tree text."""
    doc = jload(text)
    if doc is not _BAD:
        sp = _spans_of(doc)
        if not sp:
            return None
        res = {}
        for t in sorted({str(s["trace"]) for s in sp}):
            mine = [s for s in sp if str(s["trace"]) == t]
            byid = {s["id"]: s for s in mine}
            base = min(s["start"] for s in mine)
            rows = []
            for s in mine:
                depth, cur, hops = 0, s, 0
                while cur["parent"] in byid and cur["parent"] != cur["id"] and hops <= len(mine):
                    cur, depth, hops = byid[cur["parent"]], depth + 1, hops + 1
                st = "-" if not s["error"] else "ERROR" + (":" + _norm(s["msg"]) if _norm(s["msg"]) else "")
                rows.append((depth, _norm(s["service"]) or "-", _norm(s["name"]) or "-", s["dur"] // 100, (s["start"] - base) // 100, st,
                             jc({str(k): _norm(v) for k, v in s["attrs"].items()}) if s["attrs"] else "-"))
            res[_norm(t)] = (len(mine), (max(s["start"] + s["dur"] for s in mine) - base) // 100, rows)
        return res
    res, cur = {}, None
    for ln in text.split("\n"):
        if m := TRACE_RE.match(ln):
            cur = res[m.group(1)] = (int(m.group(2)), int(m.group(3)) * 10 + int(m.group(4)), [])
        elif cur is not None and (m := SPAN_RE.match(ln)):
            cur[2].append((len(m.group(1)) // 2, m.group(2), m.group(3), int(m.group(4)) * 10 + int(m.group(5)), int(m.group(6)) * 10 + int(m.group(7)), m.group(8), m.group(9)))
    return res or None


def probes_421(text: str) -> dict:
    res = _probe_spans(text)
    if res is None:
        return {"traces": None}
    out = {"traces (id: span count, duration in 0.1ms)": {t: (n, w) for t, (n, w, _r) in res.items()}, "span tree rows": {t: sorted(r) for t, (_n, _w, r) in res.items()}}
    out["slowest span per trace"] = {t: max(sorted(r), key=lambda x: x[3])[1:4] for t, (_n, _w, r) in res.items()}
    out["errored spans"] = {t: sorted((x[1], x[2], x[5]) for x in r if x[5] != "-") for t, (_n, _w, r) in res.items()}
    return out


_TRACE_SPANS = [  # id, parent, service, name, start offset ms, dur ms, error msg or None, attrs
    ("a1", None, "gateway", "POST /checkout", 0.0, 412.3, None, {"http.method": "POST", "http.route": "/checkout", "http.status_code": 200}),
    ("b2", "a1", "cart", "GET /cart/{id}", 3.2, 18.4, "cache miss storm: redis timeout after 15ms", {"cache.hit": False, "cart.items": 7}),  # an early error
    ("c3", "b2", "cart", "redis GET", 4.0, 15.1, None, {"db.system": "redis", "db.statement": "GET cart:1234"}),
    ("d4", "a1", "orders", "create_order", 25.5, 301.0, None, {"order.total": 129.9, "tenant": "acme"}),
    ("e5", "d4", "orders", "INSERT orders", 30.1, 2.9, None, {"db.system": "postgresql", "db.statement": "INSERT INTO orders (...) VALUES (...)"}),
    ("f6", "d4", "payments", "POST /charge", 40.0, 275.8, None, {"http.status_code": 200, "peer.service": "stripe"}),  # the slowest child, in the middle
    ("g7", "f6", "payments", "stripe.charge", 45.2, 268.0, None, {"stripe.attempt": 2}),
    ("h8", "a1", "mailer", "send_receipt", 330.0, 80.1, "SMTP 451: mailbox busy", {"smtp.host": "mx1.acme.test"}),  # a late error
    ("i9", "h8", "mailer", "smtp send", 331.0, 79.0, None, {}),
]
_T0_NS = 1712345678123456789


def _otlp_trace() -> str:
    spans_by_service: dict[str, list] = {}
    for sid, par, svc, name, off, dur, err, at in _TRACE_SPANS:
        t0 = _T0_NS + int(off * 1_000_000)
        val = lambda v: {"boolValue": v} if isinstance(v, bool) else {"intValue": str(v)} if isinstance(v, int) else {"doubleValue": v} if isinstance(v, float) else {"stringValue": v}  # noqa: E731
        sp = {"traceId": "5b8efff798038103d269b633813fc60c", "spanId": hexid(ord(sid[0]), 16, "s").lower(), "parentSpanId": hexid(ord(par[0]), 16, "s").lower() if par else "", "traceState": "", "name": name, "kind": 2 if par is None else 3,
              "startTimeUnixNano": str(t0), "endTimeUnixNano": str(t0 + int(dur * 1_000_000)), "attributes": [{"key": k, "value": val(v)} for k, v in at.items()], "droppedAttributesCount": 0, "events": [], "droppedEventsCount": 0,
              "links": [], "droppedLinksCount": 0, "status": {"code": 2, "message": err} if err else {"code": 0}, "flags": 257}
        spans_by_service.setdefault(svc, []).append(sp)
    rs = [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": svc}}, {"key": "telemetry.sdk.language", "value": {"stringValue": "go"}}, {"key": "telemetry.sdk.version", "value": {"stringValue": "1.24.0"}},
                                       {"key": "host.name", "value": {"stringValue": f"{svc}-7d9f8-x2k"}}]}, "scopeSpans": [{"scope": {"name": "go.opentelemetry.io/otel/sdk", "version": "1.24.0"}, "spans": sps}], "schemaUrl": "https://opentelemetry.io/schemas/1.21.0"}
          for svc, sps in spans_by_service.items()]
    return jp({"resourceSpans": rs})


def _jaeger_trace() -> str:
    spans = []
    tid = "5b8efff798038103d269b633"
    for sid, par, svc, name, off, dur, err, at in _TRACE_SPANS:
        tags = [{"key": k, "type": "bool" if isinstance(v, bool) else "int64" if isinstance(v, int) else "float64" if isinstance(v, float) else "string", "value": v} for k, v in at.items()]
        if err:
            tags += [{"key": "error", "type": "bool", "value": True}, {"key": "otel.status_description", "type": "string", "value": err}]
        spans.append({"traceID": tid, "spanID": sid, "flags": 1, "operationName": name, "references": [{"refType": "CHILD_OF", "traceID": tid, "spanID": par}] if par else [], "startTime": 1712345678123456 + int(off * 1000),
                      "duration": int(dur * 1000), "tags": tags, "logs": [], "processID": "p-" + svc, "warnings": None})
    procs = {"p-" + svc: {"serviceName": svc, "tags": [{"key": "hostname", "type": "string", "value": svc + "-7d9f8"}, {"key": "ip", "type": "string", "value": "10.1.2.3"}]} for svc in {s[2] for s in _TRACE_SPANS}}
    return jp({"data": [{"traceID": tid, "spans": spans, "processes": dict(sorted(procs.items())), "warnings": None}], "total": 0, "limit": 0, "offset": 0, "errors": None})


def _zipkin_trace() -> str:
    out = []
    tid = "5b8efff798038103"
    for sid, par, svc, name, off, dur, err, at in _TRACE_SPANS:
        tags = {k: str(v) for k, v in at.items()}
        if err:
            tags["error"] = err
        out.append({"traceId": tid, "parentId": par, "id": sid, "kind": "SERVER" if par is None else "CLIENT", "name": name.lower(), "timestamp": 1712345678123456 + int(off * 1000), "duration": int(dur * 1000),
                    "localEndpoint": {"serviceName": svc, "ipv4": "10.1.2.3"}, "tags": tags})
        if par is None:
            out[-1].pop("parentId")
    return jp(out)


case(
    421,
    claim="OTLP, Jaeger and Zipkin trace JSON become 'trace <id> | N spans | window' plus one line per span, indented by depth, with service, name, duration and start offset in 0.1 ms, status with message, and unwrapped attributes; span count, tree shape, every duration and offset, the slowest span and every errored span with its message are unchanged.",
    loses="span and parent ids, span kind, scope and schema info, trace state and flags, span events and links (an exception event is folded into the status message of an errored OTLP span), resource attributes other than service.name, sub-0.1 ms precision, and JSON whitespace; traces whose spans repeat an id, run backwards or form a parent cycle are left unchanged",
    encode=render_trace_tree,
    probes=safe_probes(probes_421),
    fixtures=lambda: [
        Fixture("OTLP export: 9 spans, 4 services, errors early and late, slowest child in the middle", _otlp_trace(), True),
        Fixture("Jaeger all-in-one trace of the same 9 spans", _jaeger_trace(), True),
        Fixture("Zipkin v2 list of the same 9 spans", _zipkin_trace(), True),
        Fixture("package.json", PACKAGE_JSON, False),
        Fixture("a rendered tree", "trace abc | 1 spans | 1.0ms\nsvc\tname\t1.0ms\t+0.0ms\t-\t-\n", False),
        Fixture("two spans claiming the same id", jp([{"traceId": "t", "id": "x", "name": "a", "timestamp": 1000, "duration": 500, "localEndpoint": {"serviceName": "s"}}, {"traceId": "t", "id": "x", "name": "b", "timestamp": 1200, "duration": 100, "localEndpoint": {"serviceName": "s"}}]), None),
        Fixture("parent cycle", jp([{"traceId": "t", "id": "x", "parentId": "y", "name": "a", "timestamp": 1000, "duration": 500}, {"traceId": "t", "id": "y", "parentId": "x", "name": "b", "timestamp": 1200, "duration": 100}]), None),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #424 journalctl -o json: keep a field allowlist.
# One line per entry: 'ISO-timestamp unit[pid] pN message', the host hoisted into a header line when it is the same for every entry.
# Everything else (__CURSOR, _BOOT_ID, _MACHINE_ID, cgroup, capabilities, SELinux context, UID/GID, EXE, CMDLINE, SYSLOG_*, COREDUMP_*)
# is dropped. Refuses anything that is not one JSON object per line with a __REALTIME_TIMESTAMP.

JOURNAL_HEAD = re.compile(r"# journal host=(\S+) entries=(\d+)$")
JOURNAL_LINE = re.compile(r"(\S+) (\S*)\[(\d*)\] p([0-7-]) (.*)$")


def _esc_msg(m: str) -> str:
    return m.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def _message(e: dict) -> str:
    m = e.get("MESSAGE", "")
    if isinstance(m, list) and all(isinstance(x, int) for x in m):
        return f"<binary {len(m)} bytes>"
    if not isinstance(m, str):
        raise ValueError("odd MESSAGE")
    return _esc_msg(m)


def _unit(e: dict) -> str:
    u = e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or "-"
    return "_".join(str(u).split()) or "-"


@total
def journal_allowlist(text: str) -> str:
    rows = []
    for ln in text.split("\n"):
        if not ln.strip():
            continue
        e = jload(ln)
        if not isinstance(e, dict) or not str(e.get("__REALTIME_TIMESTAMP", "")).isdigit():
            return text
        rows.append(e)
    if not rows:
        return text
    hosts = {str(e.get("_HOSTNAME", "-")) for e in rows}
    host = "_".join(next(iter(hosts)).split()) or "-" if len(hosts) == 1 else "*"
    out = [f"# journal host={host} entries={len(rows)}"]
    for e in rows:
        us = int(e["__REALTIME_TIMESTAMP"])
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(us // 1_000_000)) + f".{us // 1000 % 1000:03d}Z"
        pid = e.get("_PID") or e.get("SYSLOG_PID") or ""
        pr = str(e.get("PRIORITY", "-"))
        u = _unit(e) + (f"@{'_'.join(str(e.get('_HOSTNAME', '-')).split()) or '-'}" if host == "*" else "")
        if not (str(pid).isdigit() or pid == "") or pr not in "01234567-" or len(pr) != 1:
            return text
        out.append(f"{ts} {u}[{pid}] p{pr} {_message(e)}")
    return gate(text, "\n".join(out) + "\n")


def probes_424(text: str) -> dict:
    entries, host = [], None
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if lines and (m := JOURNAL_HEAD.match(lines[0])):
        host = m.group(1)
        for ln in lines[1:]:
            lm = JOURNAL_LINE.match(ln)
            entries.append((lm.group(1), lm.group(2), lm.group(3), lm.group(4), lm.group(5)))
    else:
        objs = [jload(ln) for ln in lines]
        if not objs or any(not isinstance(o, dict) or not str(o.get("__REALTIME_TIMESTAMP", "")).isdigit() for o in objs):
            return {"journal": None}
        hs = {str(o.get("_HOSTNAME", "-")) for o in objs}
        host = ("_".join(next(iter(hs)).split()) or "-") if len(hs) == 1 else "*"
        for o in objs:
            us = int(o["__REALTIME_TIMESTAMP"])
            moment = datetime.datetime.fromtimestamp(us // 1_000_000, datetime.timezone.utc)
            ts = moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{us // 1000 % 1000:03d}Z"
            unit = _unit(o) + (f"@{'_'.join(str(o.get('_HOSTNAME', '-')).split()) or '-'}" if host == "*" else "")
            entries.append((ts, unit, str(o.get("_PID") or o.get("SYSLOG_PID") or ""), str(o.get("PRIORITY", "-")), _message(o)))
    counts = Counter(e[3] for e in entries)
    return {"host": host, "entries (time, unit, pid, priority, message)": entries, "entries per priority": sorted(counts.items()),
            "error messages (priority 0-3)": [(e[1], e[4]) for e in entries if e[3] in "0123"], "units seen": sorted({e[1] for e in entries})}


def _journal(n: int = 60, host: str = "web-01") -> str:
    r = rng(17)
    boot, machine = hexid(1, 32, "boot").lower(), hexid(2, 32, "mach").lower()
    t = 1709388191_482150
    out = []
    kinds = [("sshd.service", "sshd", "6", "Accepted publickey for deploy from 10.0.0.{} port {} ssh2: ED25519 SHA256:{}"), ("nginx.service", "nginx", "6", "10.0.0.{} - - \"GET /health HTTP/1.1\" 200 {} \"-\" \"kube-probe/1.28\" {}"),
             ("cron.service", "CRON", "6", "pam_unix(cron:session): session opened for user root(uid=0) by (uid={}) {} {}"), ("systemd-logind.service", "systemd-logind", "6", "Session {} logged out. Waiting for processes to exit. {} {}")]
    for i in range(n):
        t += r.randrange(200_000, 4_000_000)
        unit, ident, prio, tmpl = kinds[r.randrange(len(kinds))]
        msg = tmpl.format(r.randrange(1, 250), r.randrange(1024, 65000), hexid(i, 12))
        pid = str(300 + r.randrange(9000))
        ent = {"_TRANSPORT": "syslog", "PRIORITY": prio, "SYSLOG_FACILITY": "10", "SYSLOG_IDENTIFIER": ident, "SYSLOG_PID": pid, "_UID": "0", "_GID": "0", "_COMM": ident.lower()[:15], "_EXE": f"/usr/sbin/{ident.lower()}", "_CMDLINE": f"{ident.lower()}: worker process {pid}",
               "_CAP_EFFECTIVE": "1ffffffffff", "_SELINUX_CONTEXT": "unconfined\n", "_SYSTEMD_CGROUP": f"/system.slice/{unit}", "_SYSTEMD_UNIT": unit, "_SYSTEMD_SLICE": "system.slice", "_SYSTEMD_INVOCATION_ID": hexid(i % 5, 32, "inv").lower(),
               "_PID": pid, "MESSAGE": msg, "SYSLOG_TIMESTAMP": "Mar  2 14:03:11 ", "_SOURCE_REALTIME_TIMESTAMP": str(t - 35), "_BOOT_ID": boot, "_MACHINE_ID": machine, "_HOSTNAME": host, "__REALTIME_TIMESTAMP": str(t),
               "__MONOTONIC_TIMESTAMP": str(5_000_000_000 + i * 1_300_000), "__SEQNUM": str(900 + i), "__SEQNUM_ID": hexid(3, 32, "seq").lower(), "__CURSOR": f"s={hexid(4, 32, 'cur').lower()};i={900 + i:x};b={boot};m={5_000_000_000 + i:x};t={t:x};x=7d3f9a1c55e0b2a4"}
        out.append(ent)

    def special(pos: int, ent: dict) -> None:
        base = dict(out[pos])
        base.update(ent)
        out[pos] = base

    special(0, {"_SYSTEMD_UNIT": "app.service", "SYSLOG_IDENTIFIER": "systemd", "_COMM": "systemd", "PRIORITY": "3", "MESSAGE": "app.service: Failed with result 'exit-code'.", "_PID": "1", "SYSLOG_PID": "1"})  # an error at the start
    special(n // 2, {"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "kernel", "_COMM": "kernel", "PRIORITY": "3", "_SYSTEMD_UNIT": None, "_PID": None, "SYSLOG_PID": None, "_SOURCE_MONOTONIC_TIMESTAMP": "5100123456",
                     "MESSAGE": "Out of memory: Killed process 4412 (app) total-vm:2412344kB, anon-rss:1203300kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:3400kB oom_score_adj:0"})  # the OOM kill in the middle
    out[n // 2 + 1].update({"MESSAGE": [72, 101, 108, 108, 111, 255, 254], "PRIORITY": "6"})  # binary message
    special(n // 2 + 2, {"_SYSTEMD_UNIT": "systemd-coredump@0-4412-0.service", "PRIORITY": "2", "COREDUMP_PID": "4412", "COREDUMP_SIGNAL_NAME": "SIGSEGV", "COREDUMP_EXE": "/srv/app/bin/app",
                         "MESSAGE": "Process 4412 (app) of user 1000 dumped core.\n\nStack trace of thread 4412:\n#0  0x00007f3a in parse_header\n#1  0x00007f3b in main"})
    out[n // 2 + 3].pop("MESSAGE")  # an entry without a message
    special(n - 1, {"_SYSTEMD_UNIT": "nginx.service", "SYSLOG_IDENTIFIER": "nginx", "PRIORITY": "3", "MESSAGE": "connect() failed (111: Connection refused) while connecting to upstream, client: 10.0.0.9, upstream: \"http://127.0.0.1:8080/\"\\n"})  # an error at the end, with a backslash
    return "".join(jc({k: v for k, v in e.items() if v is not None}) + "\n" for e in out)


_JOURNAL = _journal()
_JOURNAL_MIXED = "".join(jc({**json.loads(l), "_HOSTNAME": ["web-01", "web-02"][i % 2]}) + "\n" for i, l in enumerate(_journal(24).splitlines()))
_DOCKER_LOG = "".join(jc({"log": f"request {i} ok\n", "stream": "stdout", "time": f"2025-03-02T14:03:{i:02d}.123456789Z"}) + "\n" for i in range(30))

case(
    424,
    claim="journalctl -o json lines become 'timestamp unit[pid] pN message' (host hoisted or suffixed to the unit when it varies, newlines escaped, binary messages stubbed); every entry's time to the millisecond, unit, pid, priority and message, the per-priority counts and the error messages are unchanged.",
    loses="__CURSOR, _BOOT_ID, _MACHINE_ID, _SYSTEMD_CGROUP/SLICE/INVOCATION_ID, _CAP_EFFECTIVE, _SELINUX_CONTEXT, _UID/_GID, _EXE, _CMDLINE, SYSLOG_FACILITY/TIMESTAMP, monotonic and source timestamps, COREDUMP_* and every other field, sub-millisecond time, and the bytes of binary messages; output that is not one JSON object per line is left unchanged",
    encode=journal_allowlist,
    probes=safe_probes(probes_424),
    fixtures=lambda: [
        Fixture("60 entries: failed unit first, OOM kill + binary + coredump + no-message entries in the middle, nginx error last", _JOURNAL, True),
        Fixture("24 entries from two hosts", _JOURNAL_MIXED, True),
        Fixture("docker json-file log lines are not journal entries", _DOCKER_LOG, False),
        Fixture("journalctl -o json-pretty", jp(json.loads(_journal(8).splitlines()[1])) + "\n", False),
        Fixture("no entries", "-- No entries --\n", False),
        Fixture("input that starts like the header", "# journal host=x entries=1\n2025-01-01T00:00:00.000Z a[1] p6 m\n", None),
    ],
    min_gain=0.6,
)


# ===============================================================================================================
# #521 Render GraphQL introspection, JSON Schema and CRDs as type signatures (descriptions dropped).
# GraphQL: 'KIND Name [implements A,B] [= X | Y]' blocks with '  field: [Issue!]!' lines, '    arg name: Type = default' lines and
# '  = ENUM_VALUE' lines; __ types dropped. JSON Schema and CRD openAPIV3Schema: 'schema <name>' then 'a.b[].c*: type' lines, with
# $ref targets named once ('def Name' blocks). Both reuse `sig` from #135 for JSON Schema types.

GQL_HEAD = re.compile(r"# graphql query=(\S+) mutation=(\S+) subscription=(\S+)$")


def _s(x) -> str:
    if not isinstance(x, str):
        raise ValueError("a name must be a string")
    return x


def _gql_sig(t) -> str:
    k = t.get("kind")
    if k == "NON_NULL":
        return _gql_sig(t["ofType"]) + "!"
    if k == "LIST":
        return "[" + _gql_sig(t["ofType"]) + "]"
    return _s(t["name"])


def _gql_default(d) -> str:
    if d is None:
        return ""
    if not isinstance(d, str) or "\n" in d:
        raise ValueError("odd default")
    return f" = {d}"


def _gql_lines(schema: dict) -> list[str]:
    root = lambda k: _s((schema.get(k) or {}).get("name") or "-")  # noqa: E731
    out = [f"# graphql query={root('queryType')} mutation={root('mutationType')} subscription={root('subscriptionType')}"]
    for t in schema["types"]:
        name, kind = _s(t["name"]), _s(t["kind"])
        if name.startswith("__"):
            continue
        head = f"{kind} {name}"
        if t.get("interfaces"):
            head += " implements " + ",".join(_s(i["name"]) for i in t["interfaces"])
        if kind == "UNION":
            head += " = " + " | ".join(_s(p["name"]) for p in t.get("possibleTypes") or [])
        out.append(head)
        for f in (t.get("fields") or []) + (t.get("inputFields") or []):
            out.append(f"  {_s(f['name'])}: {_gql_sig(f['type'])}" + (" @deprecated" if f.get("isDeprecated") else "") + _gql_default(f.get("defaultValue")))
            for a in f.get("args") or []:
                out.append(f"    arg {_s(a['name'])}: {_gql_sig(a['type'])}" + _gql_default(a.get("defaultValue")))
        out += [f"  = {_s(e['name'])}" + (" @deprecated" if e.get("isDeprecated") else "") for e in t.get("enumValues") or []]
    return out


def _schema_lines(schema: dict, label: str) -> list[str]:
    out = [f"schema {_norm(label) or '-'}"]

    def walk(s: dict, path: str, depth: int) -> None:
        req = set(s.get("required", [])) if isinstance(s.get("required"), list) else set()
        for name, sub in (s.get("properties") or {}).items():
            out.append(f"{path}{name}{'*' if name in req else ''}: {sig(sub)}")
            if depth < 12 and isinstance(sub, dict):
                if isinstance(sub.get("properties"), dict):
                    walk(sub, f"{path}{name}.", depth + 1)
                elif isinstance(sub.get("items"), dict) and isinstance(sub["items"].get("properties"), dict):
                    walk(sub["items"], f"{path}{name}[].", depth + 1)

    walk(schema, "", 0)
    for key in ("definitions", "$defs"):
        for dn, ds in (schema.get(key) or {}).items():
            out.append(f"def {dn}")
            if isinstance(ds, dict) and isinstance(ds.get("properties"), dict):
                walk(ds, "", 0)
            else:
                out.append(f": {sig(ds)}")
    return out


@total
def render_type_signatures(text: str) -> str:
    o = jload(text)
    if not isinstance(o, dict):
        return text
    sch = o.get("__schema") or (o.get("data") or {}).get("__schema") if isinstance(o.get("data", {}), dict) else o.get("__schema")
    if isinstance(sch, dict) and isinstance(sch.get("types"), list):
        lines = _gql_lines(sch)
        if not all(GQL_HEAD.match(x) or re.match(r"([A-Z_]+) (\S+)(?: implements (\S+))?(?: = (.*))?$|  (\S+): (\S+)( @deprecated)?(?: = (.*))?$|    arg (\S+): (\S+)(?: = (.*))?$|  = (\S+)( @deprecated)?$", x) for x in lines):
            return text
    elif o.get("kind") == "CustomResourceDefinition" and isinstance((o.get("spec") or {}).get("versions"), list):
        lines = []
        for v in o["spec"]["versions"]:
            s = ((v.get("schema") or {}).get("openAPIV3Schema"))
            if isinstance(s, dict):
                lines += _schema_lines(s, f"{(o['spec'].get('names') or {}).get('kind', '?')} {v.get('name', '?')}")
    elif isinstance(o.get("properties"), dict) and (o.get("type") == "object" or "$schema" in o):
        lines = _schema_lines(o, str(o.get("title") or o.get("$id") or "root"))
    else:
        return text
    if not lines or any("\n" in x for x in lines) or (lines[0].startswith("schema") and any(re.search(r"\s", x.split(": ", 1)[0]) and not x.startswith(("schema ", "def ")) for x in lines)):
        return text
    return gate(text, "\n".join(lines) + "\n")


def _tsig(t) -> str:
    """Probe-side GraphQL type signature: unwrap the ofType chain iteratively, then close the wrappers from the inside out."""
    wraps = []
    while t.get("kind") in ("NON_NULL", "LIST"):
        wraps.append(t["kind"])
        t = t["ofType"]
    s = t["name"]
    for w in reversed(wraps):
        s = s + "!" if w == "NON_NULL" else f"[{s}]"
    return s


def _probe_gql_original(sch: dict) -> dict:
    types = {}
    for t in sch["types"]:
        if t["name"].startswith("__"):
            continue
        members = {}
        for f in (t.get("fields") or []) + (t.get("inputFields") or []):
            members[f["name"]] = (_tsig(f["type"]), bool(f.get("isDeprecated")), f.get("defaultValue"), {a["name"]: (_tsig(a["type"]), a.get("defaultValue")) for a in f.get("args") or []})
        types[t["name"]] = {"kind": t["kind"], "implements": sorted(i["name"] for i in t.get("interfaces") or []), "members": members,
                            "enum": [(e["name"], bool(e.get("isDeprecated"))) for e in t.get("enumValues") or []], "union": sorted(p["name"] for p in t.get("possibleTypes") or []) if t["kind"] == "UNION" else []}
    r = lambda k: (sch.get(k) or {}).get("name") or "-"  # noqa: E731
    return {"roots": (r("queryType"), r("mutationType"), r("subscriptionType")), "types": types}


def _probe_gql_encoded(lines: list[str]) -> dict:
    types, cur, last = {}, None, None
    m = GQL_HEAD.match(lines[0])
    for ln in lines[1:]:
        if h := re.match(r"([A-Z_]+) (\S+)(?: implements (\S+))?(?: = (.*))?$", ln):
            cur = types[h.group(2)] = {"kind": h.group(1), "implements": sorted(h.group(3).split(",")) if h.group(3) else [], "members": {}, "enum": [],
                                       "union": sorted(h.group(4).split(" | ")) if h.group(1) == "UNION" and h.group(4) else []}
        elif cur is not None and (a := re.match(r"    arg (\S+): (\S+)(?: = (.*))?$", ln)):
            last[3][a.group(1)] = (a.group(2), a.group(3))
        elif cur is not None and (e := re.match(r"  = (\S+)( @deprecated)?$", ln)):
            cur["enum"].append((e.group(1), bool(e.group(2))))
        elif cur is not None and (f := re.match(r"  (\S+): (\S+)( @deprecated)?(?: = (.*))?$", ln)):
            last = cur["members"][f.group(1)] = (f.group(2), bool(f.group(3)), f.group(4), {})
    return {"roots": (m.group(1), m.group(2), m.group(3)), "types": types}


def _probe_schema_original(s: dict, label: str) -> tuple:
    props, stack = {}, [(s, "", 0)]
    while stack:
        node, prefix, depth = stack.pop()
        req = node.get("required") if isinstance(node.get("required"), list) else []
        for name, sub in (node.get("properties") or {}).items():
            props[prefix + name] = (_sig2(sub), name in req)
            if depth < 12 and isinstance(sub, dict):
                if isinstance(sub.get("properties"), dict):
                    stack.append((sub, f"{prefix}{name}.", depth + 1))
                elif isinstance(sub.get("items"), dict) and isinstance(sub["items"].get("properties"), dict):
                    stack.append((sub["items"], f"{prefix}{name}[].", depth + 1))
    return label, props


def probes_521(text: str) -> dict:
    lines = [x for x in text.split("\n") if x != ""] if text.startswith(("# graphql", "schema ")) else None
    if lines is not None and GQL_HEAD.match(lines[0]):
        return {"graphql": _probe_gql_encoded(lines)}
    if lines is not None:  # JSON Schema / CRD rendering
        secs, cur, kind = {}, None, None
        for ln in lines:
            if ln.startswith("schema "):
                cur, kind = {}, "schema"
                secs[("schema", ln[7:])] = cur
            elif ln.startswith("def "):
                cur = {}
                secs[("def", ln[4:])] = cur
            elif cur is not None and (m := re.match(r"(\S+?)(\*?): (.+)$", ln)):
                cur[m.group(1)] = (m.group(3), bool(m.group(2)))
            elif cur is not None and (m := re.match(r": (.+)$", ln)):
                cur[""] = (m.group(1), False)
        return {"schemas": secs}
    o = jload(text)
    if not isinstance(o, dict):
        return {"document": None}
    sch = o.get("__schema") or (o.get("data") or {}).get("__schema") if isinstance(o.get("data", {}), dict) else o.get("__schema")
    if isinstance(sch, dict) and isinstance(sch.get("types"), list):
        return {"graphql": _probe_gql_original(sch)}
    secs = {}
    if o.get("kind") == "CustomResourceDefinition" and isinstance((o.get("spec") or {}).get("versions"), list):
        for v in o["spec"]["versions"]:
            s = (v.get("schema") or {}).get("openAPIV3Schema")
            if isinstance(s, dict):
                lab, props = _probe_schema_original(s, f"{(o['spec'].get('names') or {}).get('kind', '?')} {v.get('name', '?')}")
                secs[("schema", _norm(lab))] = props
        return {"schemas": secs} if secs else {"document": None}
    if isinstance(o.get("properties"), dict) and (o.get("type") == "object" or "$schema" in o):
        lab, props = _probe_schema_original(o, str(o.get("title") or o.get("$id") or "root"))
        secs[("schema", _norm(lab) or "-")] = props
        for key in ("definitions", "$defs"):
            for dn, ds in (o.get(key) or {}).items():
                if isinstance(ds, dict) and isinstance(ds.get("properties"), dict):
                    secs[("def", dn)] = _probe_schema_original(ds, dn)[1]
                else:
                    secs[("def", dn)] = {"": (_sig2(ds), False)}
        return {"schemas": secs}
    return {"document": None}


def _gql_schema() -> str:
    N = lambda n: {"kind": "NON_NULL", "name": None, "ofType": n}  # noqa: E731
    L = lambda n: {"kind": "LIST", "name": None, "ofType": n}  # noqa: E731
    T = lambda kind, n: {"kind": kind, "name": n, "ofType": None}  # noqa: E731
    S, O = (lambda n: T("SCALAR", n)), (lambda n: T("OBJECT", n))
    def fld(name, typ, desc, args=(), dep=False):
        return {"name": name, "description": desc, "args": [{"name": a, "description": f"The {a} argument. Returns the elements in the list that come after the specified cursor.", "type": t, "defaultValue": dv} for a, t, dv in args],
                "type": typ, "isDeprecated": dep, "deprecationReason": "Use `body` instead." if dep else None}
    def obj(name, fields, ifaces=(), desc=None):
        return {"kind": "OBJECT", "name": name, "description": desc or f"A {name} in the tracker. " * 3, "fields": fields, "inputFields": None, "interfaces": [T("INTERFACE", i) for i in ifaces], "enumValues": None, "possibleTypes": None}
    page = [("first", S("Int"), None), ("after", S("String"), None), ("orderBy", T("INPUT_OBJECT", "IssueOrder"), "{field: CREATED_AT, direction: DESC}")]
    types = [
        obj("Query", [fld("viewer", N(O("User")), "The currently authenticated user."), fld("issues", N(L(N(O("Issue")))), "Issues matching the filter.", page), fld("repository", O("Repository"), "Lookup a repository.", [("owner", N(S("String")), None), ("name", N(S("String")), None)]),
                      fld("search", N(O("SearchResultItemConnection")), "Search across the tracker.", [("query", N(S("String")), None), ("first", S("Int"), "10")])]),
        obj("Issue", [fld("id", N(S("ID")), "The id."), fld("number", N(S("Int")), "The number."), fld("title", N(S("String")), "The title."), fld("bodyText", N(S("String")), "Plain body.", dep=True), fld("state", N(T("ENUM", "IssueState")), "State."),
                      fld("labels", O("LabelConnection"), "Labels.", page[:2]), fld("assignees", N(O("UserConnection")), "Assignees.", page[:2]), fld("comments", N(O("IssueCommentConnection")), "Comments.", page[:2])], ["Node", "Closable"]),
        obj("User", [fld("login", N(S("String")), "Login."), fld("avatarUrl", N(S("URI")), "Avatar.", [("size", S("Int"), None)])], ["Node", "Actor"]),
        obj("Repository", [fld("name", N(S("String")), "Name."), fld("issues", N(O("IssueConnection")), "Issues.", page), fld("defaultBranch", S("String"), "Branch.")], ["Node"]),
        obj("IssueConnection", [fld("nodes", L(O("Issue")), "Nodes."), fld("totalCount", N(S("Int")), "Count."), fld("pageInfo", N(O("PageInfo")), "Page info.")]),
        obj("PageInfo", [fld("hasNextPage", N(S("Boolean")), "More?"), fld("endCursor", S("String"), "Cursor.")]),
        {"kind": "INTERFACE", "name": "Node", "description": "An object with an ID.", "fields": [fld("id", N(S("ID")), "The id.")], "inputFields": None, "interfaces": [], "enumValues": None, "possibleTypes": [O("Issue"), O("User"), O("Repository")]},
        {"kind": "ENUM", "name": "IssueState", "description": "States of an issue.", "fields": None, "inputFields": None, "interfaces": None, "enumValues": [{"name": "OPEN", "description": "Open.", "isDeprecated": False, "deprecationReason": None}, {"name": "CLOSED", "description": "Closed.", "isDeprecated": False, "deprecationReason": None}, {"name": "DRAFT", "description": "Draft.", "isDeprecated": True, "deprecationReason": "Removed."}], "possibleTypes": None},
        {"kind": "INPUT_OBJECT", "name": "IssueOrder", "description": "Ways to order issues.", "fields": None, "inputFields": [{"name": "field", "description": "Field.", "type": N(T("ENUM", "IssueOrderField")), "defaultValue": None}, {"name": "direction", "description": "Direction.", "type": N(T("ENUM", "OrderDirection")), "defaultValue": "ASC"}], "interfaces": None, "enumValues": None, "possibleTypes": None},
        {"kind": "UNION", "name": "SearchResultItem", "description": "Search hits.", "fields": None, "inputFields": None, "interfaces": None, "enumValues": None, "possibleTypes": [O("Issue"), O("Repository"), O("User")]},
        {"kind": "SCALAR", "name": "URI", "description": "A valid URI.", "fields": None, "inputFields": None, "interfaces": None, "enumValues": None, "possibleTypes": None},
        {"kind": "SCALAR", "name": "String", "description": "Text.", "fields": None, "inputFields": None, "interfaces": None, "enumValues": None, "possibleTypes": None},
        {"kind": "OBJECT", "name": "__Type", "description": "Introspection type.", "fields": [fld("kind", N(S("String")), "Kind.")], "inputFields": None, "interfaces": [], "enumValues": None, "possibleTypes": None},
        obj("Mutation", [fld("closeIssue", O("CloseIssuePayload"), "Close an issue.", [("input", N(T("INPUT_OBJECT", "CloseIssueInput")), None)])]),
    ]
    return jp({"data": {"__schema": {"queryType": {"name": "Query"}, "mutationType": {"name": "Mutation"}, "subscriptionType": None, "types": types,
                                    "directives": [{"name": "skip", "description": "Directs the executor to skip.", "locations": ["FIELD"], "args": [{"name": "if", "description": "Skipped when true.", "type": N(S("Boolean")), "defaultValue": None}]}]}}})


def _crd() -> str:
    d = lambda n: f"{n} is the field of the certificate request. More detail about how the value is interpreted is available in the cert-manager documentation. " * 2  # noqa: E731
    cond = {"type": "object", "required": ["status", "type"], "properties": {"type": {"type": "string", "description": d("type")}, "status": {"type": "string", "enum": ["True", "False", "Unknown"], "description": d("status")},
                                                                               "lastTransitionTime": {"type": "string", "format": "date-time", "description": d("lastTransitionTime")}, "observedGeneration": {"type": "integer", "format": "int64", "description": d("observedGeneration")},
                                                                               "message": {"type": "string", "description": d("message")}, "reason": {"type": "string", "description": d("reason")}}}
    schema = {"description": "A Certificate resource should be created to ensure an up to date and signed X.509 certificate is stored in the Kubernetes Secret resource named in `spec.secretName`.", "type": "object", "required": ["spec"], "properties": {
        "apiVersion": {"type": "string", "description": d("apiVersion")}, "kind": {"type": "string", "description": d("kind")}, "metadata": {"type": "object"},
        "spec": {"type": "object", "required": ["issuerRef", "secretName"], "description": d("spec"), "properties": {
            "dnsNames": {"type": "array", "items": {"type": "string"}, "description": d("dnsNames")}, "duration": {"type": "string", "description": d("duration")},
            "issuerRef": {"type": "object", "required": ["name"], "description": d("issuerRef"), "properties": {"group": {"type": "string", "description": d("group")}, "kind": {"type": "string", "description": d("kind")}, "name": {"type": "string", "description": d("name")}}},
            "privateKey": {"type": "object", "description": d("privateKey"), "properties": {"algorithm": {"type": "string", "enum": ["RSA", "ECDSA", "Ed25519"], "description": d("algorithm")}, "size": {"type": "integer", "description": d("size")},
                                                                                                "rotationPolicy": {"type": "string", "enum": ["Never", "Always"], "description": d("rotationPolicy")}}},
            "secretName": {"type": "string", "description": d("secretName")}, "usages": {"type": "array", "items": {"type": "string", "enum": ["signing", "digital signature", "server auth", "client auth"]}, "description": d("usages")}}},
        "status": {"type": "object", "description": d("status"), "properties": {"conditions": {"type": "array", "items": cond, "description": d("conditions")}, "notAfter": {"type": "string", "format": "date-time", "description": d("notAfter")},
                                                                               "nextPrivateKeySecretName": {"type": "string", "description": d("nextPrivateKeySecretName")}}}}}
    return jp({"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition", "metadata": {"name": "certificates.cert-manager.io", "annotations": {"controller-gen.kubebuilder.io/version": "v0.14.0"}},
               "spec": {"group": "cert-manager.io", "names": {"kind": "Certificate", "listKind": "CertificateList", "plural": "certificates", "singular": "certificate"}, "scope": "Namespaced",
                        "versions": [{"name": "v1", "served": True, "storage": True, "schema": {"openAPIV3Schema": schema}, "subresources": {"status": {}}}]}})


def _json_schema() -> str:
    return jp({"$schema": "http://json-schema.org/draft-07/schema#", "title": "deploy-config", "type": "object", "required": ["name", "targets"], "description": "Configuration for a deployment pipeline. " * 4, "properties": {
        "name": {"type": "string", "description": "Pipeline name. " * 3}, "targets": {"type": "array", "items": {"$ref": "#/definitions/Target"}, "description": "Targets. " * 3}, "retries": {"type": "integer", "format": "int32", "description": "Retries. " * 3},
        "mode": {"type": "string", "enum": ["canary", "blue-green", "rolling"], "description": "Mode. " * 3}, "labels": {"type": "object", "additionalProperties": {"type": "string"}}},
        "definitions": {"Target": {"type": "object", "required": ["cluster"], "properties": {"cluster": {"type": "string", "description": "Cluster. " * 3}, "weight": {"type": "number"}, "next": {"$ref": "#/definitions/Target"}}}, "Region": {"type": "string", "enum": ["eu", "us"]}}})


_PLAIN_SCHEMA = jc({"type": "string", "enum": ["a", "b"]})

case(
    521,
    claim="GraphQL introspection JSON becomes one block per type (kind, interfaces, union members, 'field: [Issue!]!' lines with deprecation, 'arg name: Type = default' lines, enum values) without __ types, and JSON Schema or CRD openAPIV3Schema becomes 'path*: type' lines with enums, arrays, maps and $ref targets named once; every type, field, argument with its type signature and default, enum value, required flag and enum list is unchanged.",
    loses="all descriptions, deprecation reasons, directives, introspection (__) types, JSON Schema constraints (format beyond the type, minimum, pattern), default values of JSON Schema properties, and JSON whitespace; schemas with whitespace in property names are left unchanged",
    encode=render_type_signatures,
    probes=safe_probes(probes_521),
    fixtures=lambda: [
        Fixture("GraphQL introspection: Query first, Mutation last, a __Type to drop, a deprecated field and enum value", _gql_schema(), True),
        Fixture("cert-manager style CRD (JSON) with long descriptions and enums", _crd(), True),
        Fixture("JSON Schema with $ref definitions and a recursive type", _json_schema(), True),
        Fixture("a bare enum schema is not an object schema", _PLAIN_SCHEMA, False),
        Fixture("package.json", PACKAGE_JSON, False),
        Fixture("input that starts like the rendering", "# graphql query=Q mutation=- subscription=-\nOBJECT Q\n  a: Int\n", None),
    ],
    min_gain=0.6,
)


# ===============================================================================================================
# #606 npm packument and PyPI JSON API to a version digest.
# npm: dist-tags, the latest version's dependencies and engines, then one 'version date [deprecated msg]' line per release.
# PyPI: latest version, requires-python, requires_dist, vulnerabilities, then one 'version date [yanked reason]' line per release.

REG_NPM = re.compile(r"# npm (\S+) \| latest (\S+) \| (\d+) versions$")
REG_PYPI = re.compile(r"# pypi (\S+) \| latest (\S+) \| (\d+) releases$")
REG_VER = re.compile(r"(\S+) (\S+)(?: (deprecated|yanked): (.*))?$")


def _date(s) -> str:
    return _tok(str(s)[:10]) if s is not None else "-"


def _reg_version(v) -> str:
    if not isinstance(v, str) or re.search(r"\s", v) or not v:
        raise ValueError("odd version")
    return v


@total
def registry_digest(text: str) -> str:
    o = jload(text)
    if not isinstance(o, dict):
        return text
    out = None
    if isinstance(o.get("versions"), dict) and isinstance(o.get("dist-tags"), dict):
        name, tags, versions, times = _tok(o.get("name", "-")), o["dist-tags"], o["versions"], o.get("time") if isinstance(o.get("time"), dict) else {}
        latest = tags.get("latest")
        info = versions.get(latest) if isinstance(latest, str) and isinstance(versions.get(latest), dict) else {}
        out = [f"# npm {name} | latest {_tok(latest) if latest else '-'} | {len(versions)} versions", f"tags {jc(tags)}", f"latest deps {jc(info.get('dependencies', {}))} engines {jc(info.get('engines', {}))}"]
        for v, meta in versions.items():
            dep = meta.get("deprecated") if isinstance(meta, dict) else None
            out.append(f"{_reg_version(v)} {_date(times.get(v))}" + (f" deprecated: {_norm(dep)}" if dep else ""))
    elif isinstance(o.get("info"), dict) and isinstance(o.get("releases"), dict):
        info = o["info"]
        out = [f"# pypi {_tok(info.get('name', '-'))} | latest {_tok(info.get('version', '-'))} | {len(o['releases'])} releases", f"requires-python {jc(info.get('requires_python'))}", f"requires {jc(info.get('requires_dist'))}",
               "vulns " + jc([[v.get("id"), v.get("fixed_in")] for v in o.get("vulnerabilities") or []])]
        for v, files in o["releases"].items():
            files = files if isinstance(files, list) else []
            when = min((str(f.get("upload_time_iso_8601") or f.get("upload_time") or "") for f in files if isinstance(f, dict) and (f.get("upload_time_iso_8601") or f.get("upload_time"))), default=None)
            yanked = [f for f in files if isinstance(f, dict) and f.get("yanked")]
            out.append(f"{_reg_version(v)} {_date(when)}" + (f" yanked: {_norm(yanked[0].get('yanked_reason') or '')}" if yanked else ""))
    if out is None or any("\n" in x for x in out):
        return text
    return gate(text, "\n".join(out) + "\n")


def probes_606(text: str) -> dict:
    o = jload(text)
    if isinstance(o, dict) and isinstance(o.get("versions"), dict) and isinstance(o.get("dist-tags"), dict):
        tm = o.get("time") if isinstance(o.get("time"), dict) else {}
        latest = o["dist-tags"].get("latest")
        meta = o["versions"].get(latest) if isinstance(latest, str) else None
        meta = meta if isinstance(meta, dict) else {}
        return {"package": ("npm", _tok(o.get("name", "-"))), "dist-tags": o["dist-tags"], "latest": (latest, meta.get("dependencies", {}), meta.get("engines", {})),
                "release date and deprecation per version": [(v, _date(tm.get(v)), _norm(m["deprecated"]) if isinstance(m, dict) and m.get("deprecated") else None) for v, m in o["versions"].items()]}
    if isinstance(o, dict) and isinstance(o.get("info"), dict) and isinstance(o.get("releases"), dict):
        info = o["info"]
        rel = []
        for v, files in o["releases"].items():
            files = files if isinstance(files, list) else []
            stamps = sorted(str(f.get("upload_time_iso_8601") or f.get("upload_time")) for f in files if isinstance(f, dict) and (f.get("upload_time_iso_8601") or f.get("upload_time")))
            ys = [f for f in files if isinstance(f, dict) and f.get("yanked")]
            rel.append((v, _date(stamps[0]) if stamps else "-", _norm(ys[0].get("yanked_reason") or "") if ys else None))
        return {"package": ("pypi", _tok(info.get("name", "-"))), "latest": (_tok(info.get("version", "-")), info.get("requires_python"), info.get("requires_dist")),
                "vulnerabilities": [[v.get("id"), v.get("fixed_in")] for v in o.get("vulnerabilities") or []], "release date and yank reason per version": rel}
    lines = text.split("\n")
    if lines and (m := REG_NPM.match(lines[0])) and len(lines) >= 3:
        tags = json.loads(lines[1][5:])
        dm = re.match(r"latest deps (.*) engines (.*)$", lines[2])
        vers = []
        for ln in lines[3:]:
            if ln:
                vm = REG_VER.match(ln)
                vers.append((vm.group(1), vm.group(2) if vm.group(2) != "-" else "-", vm.group(4) if vm.group(3) else None))
        latest = tags.get("latest")
        return {"package": ("npm", m.group(1)), "dist-tags": tags, "latest": (latest, json.loads(dm.group(1)), json.loads(dm.group(2))), "release date and deprecation per version": vers}
    if lines and (m := REG_PYPI.match(lines[0])) and len(lines) >= 4:
        vers = []
        for ln in lines[4:]:
            if ln:
                vm = REG_VER.match(ln)
                vers.append((vm.group(1), vm.group(2), vm.group(4) if vm.group(3) else None))
        return {"package": ("pypi", m.group(1)), "latest": (m.group(2), json.loads(lines[1][len("requires-python "):]), json.loads(lines[2][len("requires "):])),
                "vulnerabilities": json.loads(lines[3][len("vulns "):]), "release date and yank reason per version": vers}
    return {"package": None}


def _packument(n: int = 70) -> str:
    r = rng(4)
    versions, times, tags = {}, {"created": "2010-12-29T19:38:25.450Z", "modified": "2024-04-11T08:11:12.001Z"}, {}
    for i in range(n):
        major, minor, patch = (0, i, 0) if i < 12 else (3, i - 12, 0) if i < 24 else (4, (i - 24) // 4, (i - 24) % 4)
        v = f"{major}.{minor}.{patch}"
        times[v] = f"{2011 + i // 8}-{1 + i % 12:02d}-{1 + i % 27:02d}T{r.randrange(24):02d}:{r.randrange(60):02d}:11.{r.randrange(1000):03d}Z"
        deps = {"accepts": "~1.3.8", "body-parser": "1.20.2", "cookie": "0.6.0", "debug": "2.6.9", "path-to-regexp": f"0.1.{i % 7}", "qs": "6.11.0"}
        meta = {"name": "express", "version": v, "description": "Fast, unopinionated, minimalist web framework", "keywords": ["express", "framework", "sinatra", "web", "http", "rest", "restful", "router", "app", "api"],
                "homepage": "http://expressjs.com/", "bugs": {"url": "https://github.com/expressjs/express/issues"}, "license": "MIT", "repository": {"type": "git", "url": "git+https://github.com/expressjs/express.git"},
                "funding": {"type": "opencollective", "url": "https://opencollective.com/express"}, "author": {"name": "TJ Holowaychuk", "email": "tj@vision-media.ca"}, "contributors": [{"name": f"Contributor {k}", "email": f"c{k}@example.com"} for k in range(8)],
                "dependencies": deps, "devDependencies": {"after": "0.8.2", "connect-redis": "3.4.2", "eslint": "8.47.0", "mocha": "10.2.0", "supertest": "6.3.0"}, "engines": {"node": ">= 0.10.0" if i < 24 else ">= 0.10.0" if i < 60 else ">= 18"},
                "files": ["LICENSE", "History.md", "Readme.md", "index.js", "lib/"], "scripts": {"lint": "eslint .", "test": "mocha --require test/support/env --reporter spec --check-leaks test/ test/acceptance/"},
                "_id": f"express@{v}", "_nodeVersion": "18.18.2", "_npmVersion": "10.2.3", "dist": {"integrity": "sha512-" + base64.b64encode(hashlib.sha512(v.encode()).digest()).decode(), "shasum": hashlib.sha1(v.encode()).hexdigest(),
                                                                                                        "tarball": f"https://registry.npmjs.org/express/-/express-{v}.tgz", "fileCount": 16 + i % 5, "unpackedSize": 210000 + i * 311,
                                                                                                        "signatures": [{"keyid": "SHA256:jl3bwswu80PjjokCgh0o2w5c2U4LhQAE57gj9cz1kzA", "sig": base64.b64encode(hashlib.sha256(v.encode()).digest() * 2).decode()}]},
                "gitHead": hashlib.sha1(v[::-1].encode()).hexdigest(), "_npmUser": {"name": "wesleytodd", "email": "wes@wesleytodd.com"}, "directories": {}, "maintainers": [{"name": f"maint{k}", "email": f"m{k}@example.com"} for k in range(5)],
                "_npmOperationalInternal": {"host": "s3://npm-registry-packages", "tmp": f"tmp/express_{v}_1700000000000_0.1"}, "_hasShrinkwrap": False}
        if i == 3:
            meta["deprecated"] = "Please upgrade to express 4.x, this release has known security issues"  # a deprecation early in the list
        if i == 40:
            meta["deprecated"] = "Contains a regression in the router, use 4.17.2"  # and one in the middle
        versions[v] = meta
    tags = {"latest": f"4.{(n - 25) // 4}.{(n - 25) % 4}", "next": "5.0.0-beta.3", "beta": "5.0.0-beta.3", "v3-legacy": "3.21.2"}
    versions["5.0.0-beta.3"] = {**meta, "version": "5.0.0-beta.3", "_id": "express@5.0.0-beta.3", "engines": {"node": ">= 18"}}
    times["5.0.0-beta.3"] = "2024-03-25T15:00:21.002Z"
    tags["latest"] = list(versions)[-2]  # latest is not the last key: a prerelease follows it
    return jc({"_id": "express", "_rev": "2300-6f3d9a1b7c2e", "name": "express", "dist-tags": tags, "versions": versions, "time": times, "maintainers": meta["maintainers"], "description": meta["description"], "homepage": meta["homepage"],
               "keywords": meta["keywords"], "repository": meta["repository"], "contributors": meta["contributors"], "license": "MIT", "readmeFilename": "Readme.md",
               "readme": "# Express\n\nFast, unopinionated, minimalist web framework for Node.js.\n\n" + "## Features\n- Robust routing\n- Focus on high performance\n- Super-high test coverage\n" * 220,
               "users": {f"user{k}": True for k in range(150)}})


def _pypi(n: int = 60) -> str:
    r = rng(6)
    rel = {}
    for i in range(n):
        v = f"2.{i // 4}.{i % 4}" if i >= 8 else f"0.{i}.0"
        files = []
        for kind in ("sdist", "bdist_wheel"):
            fn = f"requests-{v}.tar.gz" if kind == "sdist" else f"requests-{v}-py3-none-any.whl"
            files.append({"comment_text": "", "digests": {"blake2b_256": hexid(i, 64, kind).lower(), "md5": hexid(i, 32, kind).lower(), "sha256": hexid(i, 64, "sha" + kind).lower()}, "downloads": -1, "filename": fn, "has_sig": False, "md5_digest": hexid(i, 32, kind).lower(),
                          "packagetype": kind, "python_version": "source" if kind == "sdist" else "py3", "requires_python": ">=3.7" if i > 30 else None, "size": 100000 + i * 117, "upload_time": f"{2012 + i // 6}-{1 + i % 12:02d}-{1 + i % 27:02d}T{10 + kind.count('w')}:{r.randrange(60):02d}:00",
                          "upload_time_iso_8601": f"{2012 + i // 6}-{1 + i % 12:02d}-{1 + i % 27:02d}T{10 + kind.count('w')}:{r.randrange(60):02d}:00.123456Z", "url": f"https://files.pythonhosted.org/packages/ab/cd/{fn}",
                          "yanked": i == 5, "yanked_reason": "Broken wheel metadata" if i == 5 else None})
        rel[v] = files
    rel["2.99.0rc1"] = []  # a release with no files has no date
    return jc({"info": {"author": "Kenneth Reitz", "author_email": "me@kennethreitz.org", "classifiers": ["Development Status :: 5 - Production/Stable", "License :: OSI Approved :: Apache Software License", "Programming Language :: Python :: 3"] * 4,
                        "description": "# Requests\n\nRequests is a simple, yet elegant, HTTP library.\n\n" + "```python\n>>> r = requests.get('https://api.github.com/user', auth=('user', 'pass'))\n```\n" * 150,
                        "description_content_type": "text/markdown", "home_page": "https://requests.readthedocs.io", "license": "Apache 2.0", "name": "requests", "package_url": "https://pypi.org/project/requests/", "requires_dist": ["charset-normalizer (<4,>=2)", "idna (<4,>=2.5)", "urllib3 (<3,>=1.21.1)", "certifi (>=2017.4.17)", "PySocks (!=1.5.7,>=1.5.6) ; extra == 'socks'"],
                        "requires_python": ">=3.7", "summary": "Python HTTP for Humans.", "version": rel and list(rel)[-2]}, "last_serial": 21394133, "releases": rel, "urls": rel[list(rel)[-2]],
               "vulnerabilities": [{"id": "PYSEC-2023-74", "aliases": ["CVE-2023-32681"], "details": "Requests leaks Proxy-Authorization headers to destination servers on https redirects.", "fixed_in": ["2.31.0"], "link": "https://osv.dev/vulnerability/PYSEC-2023-74", "source": "osv", "summary": None, "withdrawn": None}]})


case(
    606,
    claim="An npm packument or PyPI project JSON becomes a digest: package, latest version, dist-tags, the latest version's dependencies and engines (or requires-python, requires_dist and vulnerability ids), then one 'version date [deprecated or yanked: reason]' line per release in registry order; latest version, tags, dependencies, engines.node, the release date of every version and every deprecation or yank reason are unchanged.",
    loses="the readme/description, per-version metadata of non-latest versions (dependencies, dist integrity and signatures, maintainers, scripts, files), PyPI file lists, hashes and sizes, time of day of a release (date only), the 'users' map and every other field; documents that are neither a packument nor a PyPI project JSON are left unchanged",
    encode=registry_digest,
    probes=safe_probes(probes_606),
    fixtures=lambda: [
        Fixture("npm packument, 71 versions: deprecations early and in the middle, latest followed by a prerelease", _packument(), True),
        Fixture("PyPI project JSON, 61 releases: one yanked, one file-less, vulnerability on latest", _pypi(), True),
        Fixture("npm view express version --json", jc("4.19.2"), False),
        Fixture("package.json is not a packument", jc({"name": "app", "version": "1.0.0", "dependencies": {"express": "^4.19.2"}, "engines": {"node": ">=18"}}), False),
        Fixture("a digest", "# npm x | latest 1.0.0 | 1 versions\ntags {\"latest\":\"1.0.0\"}\nlatest deps {} engines {}\n1.0.0 2020-01-01\n", None),
    ],
    min_gain=0.8,
)


# ===============================================================================================================
# #732 Strip defaults and float noise from design-file JSON (Figma REST node trees).
# Inside a document that holds Figma nodes: default-valued keys are dropped (blendMode NORMAL/PASS_THROUGH, visible true, opacity 1,
# empty effects/strokes/fills/exportSettings, TOP/LEFT constraints, ...), {r,g,b,a} colours in 0..1 become '#rrggbb[aa]', and every
# other float is rounded to 2 decimals. Names, ids, text, hierarchy and every non-default property stay.

_TUPLE_DEFAULTS = {"blendMode": ("PASS_THROUGH", "NORMAL"), "scrollBehavior": ("SCROLLS",), "layoutAlign": ("INHERIT",)}
_BOOL_DEFAULTS = {"visible": True, "locked": False, "isMask": False, "preserveRatio": False}
_EMPTY_KEYS = ("effects", "strokes", "fills", "exportSettings")
_DEFAULT_CONSTRAINTS = {"vertical": "TOP", "horizontal": "LEFT"}


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _channel(x: float) -> int:
    return max(0, min(255, math.floor(x * 255 + 0.5)))


def _hex(c: dict) -> str | None:
    if set(c) in ({"r", "g", "b"}, {"r", "g", "b", "a"}) and all(_is_num(v) and 0 <= v <= 1 for v in c.values()):
        s = "#%02x%02x%02x" % (_channel(c["r"]), _channel(c["g"]), _channel(c["b"]))
        return s + ("%02x" % _channel(c["a"]) if "a" in c and _channel(c["a"]) != 255 else "")
    return None


def _default(k: str, v) -> bool:
    if k in _TUPLE_DEFAULTS:
        return isinstance(v, str) and v in _TUPLE_DEFAULTS[k]
    if k in _BOOL_DEFAULTS:
        return isinstance(v, bool) and v is _BOOL_DEFAULTS[k]
    if k in ("opacity",):
        return _is_num(v) and v == 1
    if k == "layoutGrow":
        return _is_num(v) and v == 0
    if k in _EMPTY_KEYS:
        return v == []
    if k == "constraints":
        return v == _DEFAULT_CONSTRAINTS
    return False


def _tidy(v):
    if isinstance(v, dict):
        h = _hex(v)
        if h is not None:
            return h
        return {k: _tidy(x) for k, x in v.items() if not _default(k, x)}
    if isinstance(v, list):
        return [_tidy(x) for x in v]
    if isinstance(v, float) and math.isfinite(v):
        r = round(v, 2)
        return int(r) if r == int(r) and abs(r) < 1e15 else r
    return v


@total
def tidy_design_json(text: str) -> str:
    o = jload(text)
    if not (isinstance(o, dict) and ("document" in o or isinstance(o.get("nodes"), dict))):
        return text
    return gate(text, jc(_tidy(o)))


def _hex2(c) -> object:
    """Probe-side colour: '#rrggbb[aa]' for a convertible dict, a rounded dict otherwise, strings as they are."""
    if isinstance(c, dict):
        if set(c) in ({"r", "g", "b"}, {"r", "g", "b", "a"}) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in c.values()):
            chans = [int(c[k] * 255 + 0.5) for k in "rgb"]
            a = int(c["a"] * 255 + 0.5) if "a" in c else 255
            return "#" + "".join(f"{x:02x}" for x in chans) + ("" if a == 255 else f"{a:02x}")
        return {k: _hex2(v) for k, v in c.items()}
    return c


def _r2(x):
    return round(x, 2) if isinstance(x, float) and math.isfinite(x) else x


def _paints(lst) -> list:
    out = []
    for p in lst or []:
        if not isinstance(p, dict):
            out.append(p)
            continue
        blend = p.get("blendMode")
        out.append((p.get("type"), _hex2(p.get("color")) if "color" in p else None, p.get("visible", True), _r2(p.get("opacity", 1)), None if blend in (None, "NORMAL", "PASS_THROUGH") else blend))
    return out


def probes_732(text: str) -> dict:
    o = jload(text)
    if not (isinstance(o, dict) and ("document" in o or isinstance(o.get("nodes"), dict))):
        return {"document": None}
    nodes, stack = {}, [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("id"), str) and isinstance(x.get("type"), str) and ("name" in x):
                bb = x.get("absoluteBoundingBox")
                style = x.get("style") if isinstance(x.get("style"), dict) else {}
                blend = x.get("blendMode")
                nodes[x["id"]] = {"type": x["type"], "name": x["name"], "text": x.get("characters"), "children": [c.get("id") for c in x.get("children", []) if isinstance(c, dict)],
                                  "box": tuple(_r2(bb.get(k)) for k in ("x", "y", "width", "height")) if isinstance(bb, dict) else None, "visible": x.get("visible", True), "opacity": _r2(x.get("opacity", 1)),
                                  "blend": None if blend in (None, "NORMAL", "PASS_THROUGH") else blend, "fills": _paints(x.get("fills")), "strokes": _paints(x.get("strokes")),
                                  "effects": [(e.get("type"), _hex2(e.get("color"))) for e in x.get("effects", []) if isinstance(e, dict)], "radius": _r2(x.get("cornerRadius")),
                                  "font": (style.get("fontFamily"), _r2(style.get("fontSize")), style.get("fontWeight")) if style else None, "constraints": x.get("constraints", _DEFAULT_CONSTRAINTS)}
            stack += list(x.values())
        elif isinstance(x, list):
            stack += x
    return {"nodes by id": nodes, "node count": len(nodes), "hidden nodes": sorted(i for i, n in nodes.items() if n["visible"] is not True),
            "text nodes": sorted((i, n["text"]) for i, n in nodes.items() if n["text"] is not None)}


def _figma_doc(n_items: int = 26) -> str:
    r = rng(12)
    f17 = lambda: r.randrange(256) / 255 + (r.random() * 1e-16)  # noqa: E731  (colours arrive as 17-digit noise around k/255)
    solid = lambda: {"blendMode": "NORMAL", "type": "SOLID", "color": {"r": r.randrange(256) / 255, "g": r.randrange(256) / 255, "b": r.randrange(256) / 255, "a": 1}}  # noqa: E731

    def node(i: int, kind: str, name: str, x: float, y: float, w: float, h: float, **extra) -> dict:
        d = {"id": f"1:{i}", "name": name, "type": kind, "scrollBehavior": "SCROLLS", "blendMode": "PASS_THROUGH", "opacity": 1, "visible": True, "locked": False, "isMask": False, "preserveRatio": False,
             "constraints": {"vertical": "TOP", "horizontal": "LEFT"}, "layoutAlign": "INHERIT", "layoutGrow": 0, "fills": [solid()], "strokes": [], "strokeWeight": 1, "strokeAlign": "INSIDE", "effects": [], "exportSettings": [],
             "absoluteBoundingBox": {"x": x + r.random() * 1e-13, "y": y, "width": w - r.random() * 1e-12, "height": h}, "absoluteRenderBounds": {"x": x, "y": y, "width": w, "height": h}, "cornerRadius": 8 + (i % 3) * 0.5, "itemSpacing": 8.000000000000002}
        d.update(extra)
        return d

    kids = []
    for i in range(n_items):
        kind = ["RECTANGLE", "TEXT", "FRAME", "INSTANCE"][i % 4]
        extra = {}
        if kind == "TEXT":
            extra = {"characters": ["Pay now", "Betaal nu →", "Total: € 129,90", "Cart (3)"][(i // 4) % 4], "style": {"fontFamily": "Inter", "fontPostScriptName": "Inter-Medium", "fontWeight": 500, "fontSize": 14 + r.random() * 1e-14, "textAlignHorizontal": "LEFT",
                                                                                                                                      "textAlignVertical": "TOP", "letterSpacing": 0.1200000000000001, "lineHeightPx": 16.799999237060547, "lineHeightPercent": 100, "lineHeightUnit": "INTRINSIC_%"}}
        kids.append(node(10 + i, kind, f"{kind.title()} {i}", 24 + 8.5 * (i % 5), 100 + 52 * i, 327, 48, **extra))
    kids[0]["visible"] = False  # hidden node at the start
    kids[n_items // 2]["opacity"] = 0.5  # half-transparent in the middle
    kids[n_items // 2]["fills"] = [{"blendMode": "NORMAL", "type": "GRADIENT_LINEAR", "gradientHandlePositions": [{"x": 0.5000000000000001, "y": 0.0}, {"x": 0.5, "y": 1.0000000000000002}, {"x": 0.0, "y": 0.0}], "gradientStops": [{"color": {"r": 0.2, "g": 0.4, "b": 0.8, "a": 1}, "position": 0}, {"color": {"r": 0.2, "g": 0.4, "b": 0.8, "a": 0.30000001192092896}, "position": 1}]}]
    kids[-1]["effects"] = [{"type": "DROP_SHADOW", "visible": True, "color": {"r": 0, "g": 0, "b": 0, "a": 0.25}, "blendMode": "NORMAL", "offset": {"x": 0, "y": 4.000000000000001}, "radius": 12, "showShadowBehindNode": False}]
    kids[-1]["constraints"] = {"vertical": "BOTTOM", "horizontal": "STRETCH"}  # a non-default constraint at the end
    root = node(2, "FRAME", "Checkout", 0, 0, 375, 1800, children=kids, clipsContent=True, layoutMode="VERTICAL")
    return jc({"name": "Checkout", "role": "owner", "lastModified": "2025-01-30T12:00:00Z", "editorType": "figma", "thumbnailUrl": "https://s3-alpha.figma.com/thumbnails/abc", "version": "2150948311",
               "nodes": {"1:2": {"document": root, "components": {}, "componentSets": {}, "schemaVersion": 0, "styles": {}}}})


_FIGMA_PRUNED = tidy_design_json(_figma_doc())

case(
    732,
    claim="In a Figma REST document, default-valued keys (blendMode NORMAL/PASS_THROUGH, visible true, opacity 1, empty effects/strokes/fills/exportSettings, TOP/LEFT constraints, ...) are dropped, {r,g,b,a} colours become hex, other floats round to 2 decimals; every node's id, name, type, text, children order, box, colour, non-default visibility, opacity, blend, effect, radius and font is unchanged.",
    loses="the 17th-digit noise of coordinates and sizes (rounded to 2 decimals), colour precision beyond 8 bits per channel, keys equal to Figma's defaults, whether a default was written explicitly, and JSON whitespace; non-Figma documents are left unchanged",
    encode=tidy_design_json,
    probes=safe_probes(probes_732),
    fixtures=lambda: [
        Fixture("Figma nodes response: 27 nodes; hidden first, half-transparent gradient in the middle, shadow and BOTTOM constraint last", _figma_doc(), True),
        Fixture("small frame", _figma_doc(4), True),
        Fixture("already tidied", _FIGMA_PRUNED, False),
        Fixture("package.json", PACKAGE_JSON, False),
        Fixture("colours outside 0..1 and odd keys", jc({"document": {"id": "0:1", "type": "CANVAS", "name": "p", "fills": [{"type": "SOLID", "color": {"r": 2, "g": 0.5, "b": 0.25, "a": 1}}], "opacity": True, "visible": "yes", "layoutGrow": False, "children": []}}), None),
    ],
    min_gain=0.4,
)


# ===============================================================================================================
# #801 Elasticsearch and OpenSearch hit envelopes to flat rows.
# '#es' header lines (total, shared _index, the last hit's sort for search_after, aggregations and other top-level keys, and
# timed_out / failed shards only when abnormal) then one row per hit: id TAB _source TAB extras. The `fields` map is dropped for
# every entry that merely repeats _source (singleton arrays); _score, per-hit sort, took, max_score and normal _shards go.

ES_HIT_KNOWN = ("_index", "_id", "_score", "_source", "fields", "sort")


def _src_get(src, path: str):
    """Value of a (possibly dotted) field in _source; (found, value)."""
    if isinstance(src, dict) and path in src:
        return True, src[path]
    cur = src
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _dup_fields(src, fields) -> tuple[dict, bool]:
    """(the entries of `fields` that are not repeats of _source, ok)"""
    if not isinstance(fields, dict):
        return {}, False
    keep = {}
    for k, v in fields.items():
        found, val = _src_get(src, k)
        if not (found and v == (val if isinstance(val, list) else [val])):
            keep[k] = v
    return keep, True


@total
def flatten_es_hits(text: str) -> str:
    o = jload(text)
    if not (isinstance(o, dict) and isinstance(o.get("hits"), dict) and isinstance(o["hits"].get("hits"), list)):
        return text
    hits = o["hits"]["hits"]
    if not hits or not all(isinstance(h, dict) and isinstance(h.get("_id"), str) and "\t" not in h["_id"] and "\n" not in h["_id"] and not h["_id"].startswith("#es ") for h in hits):
        return text
    indices = {h.get("_index") for h in hits}
    shared = next(iter(indices)) if len(indices) == 1 and isinstance(next(iter(indices)), str) else None
    sh = o.get("_shards") if isinstance(o.get("_shards"), dict) else {}
    top = {k: v for k, v in o.items() if k not in ("took", "_shards", "hits", "timed_out")}
    if "timed_out" in o and o["timed_out"] is not False:
        top["timed_out"] = o["timed_out"]
    if sh.get("failed") not in (0, None) or "failures" in sh or "_shards" in o and not isinstance(o["_shards"], dict):
        top["_shards"] = o["_shards"]
    out = ["#es total " + jc(o["hits"].get("total"))]
    if shared is not None:
        out.append("#es index " + jc(shared))
    if top:
        out.append("#es top " + jc(top))
    if hits[-1].get("sort") is not None:
        out.append("#es last-sort " + jc(hits[-1]["sort"]))
    for h in hits:
        extra = {k: v for k, v in h.items() if k not in ES_HIT_KNOWN}
        if shared is None:
            extra["_index"] = h.get("_index")
        if "fields" in h:
            keep, ok = _dup_fields(h.get("_source"), h["fields"])
            if keep or not ok:
                extra["fields"] = keep if ok else h["fields"]
        out.append(h["_id"] + "\t" + jc(h.get("_source")) + "\t" + (jc(extra) if extra else "-"))
    return gate(text, "\n".join(out) + "\n")


def probes_801(text: str) -> dict:
    o = jload(text)
    if isinstance(o, dict) and isinstance(o.get("hits"), dict) and isinstance(o["hits"].get("hits"), list) and o["hits"]["hits"] and all(isinstance(h, dict) for h in o["hits"]["hits"]):
        hits = o["hits"]["hits"]
        rows = []
        for h in hits:
            uniq = {}
            for k, v in (h.get("fields") or {}).items() if isinstance(h.get("fields"), dict) else []:
                found, val = _src_get(h.get("_source"), k)
                if not found or v != (val if isinstance(val, list) else [val]):
                    uniq[k] = v
            rows.append((h.get("_index"), h["_id"], h.get("_source"), (uniq or None) if isinstance(h.get("fields"), dict) else h.get("fields"), {k: v for k, v in h.items() if k not in ES_HIT_KNOWN}))
        sh = o.get("_shards") if isinstance(o.get("_shards"), dict) else None
        flags = {"timed_out": o["timed_out"]} if "timed_out" in o and o["timed_out"] is not False else {}
        if ("_shards" in o and sh is None) or (sh is not None and (sh.get("failed") not in (0, None) or "failures" in sh)):
            flags["_shards"] = o["_shards"]
        other = {k: v for k, v in o.items() if k not in ("took", "_shards", "hits", "timed_out")}
        return {"total": o["hits"].get("total"), "hit rows (index, id, _source, unique fields, other keys)": rows, "last sort": hits[-1].get("sort"), "top-level keys other than took/hits": other, "abnormal flags": flags}
    lines = text.split("\n")
    if not any(ln.startswith("#es total ") for ln in lines):
        return {"hits": None}
    head, shared, rows = {}, None, []
    for ln in lines:
        if ln.startswith("#es "):
            key, _, val = ln[4:].partition(" ")
            head[key] = json.loads(val)
        elif ln:
            hid, src, extra = ln.split("\t", 2)
            ex = {} if extra == "-" else json.loads(extra)
            idx = head.get("index") if "index" in head else ex.pop("_index", None)
            fields = ex.pop("fields", None)
            rows.append((idx, hid, json.loads(src), fields, ex))
    top = dict(head.get("top", {}))
    flags = {k: top.pop(k) for k in ("timed_out", "_shards") if k in top}
    return {"total": head["total"], "hit rows (index, id, _source, unique fields, other keys)": rows, "last sort": head.get("last-sort"), "top-level keys other than took/hits": top, "abnormal flags": flags}


def _es_search(n: int = 40, same_index: bool = True, abnormal: bool = False) -> str:
    r = rng(31)
    hits = []
    for i in range(n):
        ts = f"2026-03-02T14:{i // 60:02d}:{i % 60:02d}.{r.randrange(1000):03d}Z"
        src = {"@timestamp": ts, "level": "ERROR" if i in (0, n // 2, n - 1) else ["INFO", "WARN", "INFO", "DEBUG"][i % 4], "service": ["checkout", "cart", "payments"][i % 3],
               "message": ["upstream connect error or disconnect/reset before headers", "cache refreshed", "payment authorised", "slow query detected"][i % 4] + f" #{i}", "trace_id": hexid(i, 32, "tr").lower(),
               "http": {"status": 500 if i == 0 else 200, "method": "POST"}, "host": {"name": f"node-{i % 7}", "ip": f"10.0.0.{i % 200}"}, "user": {"id": f"u{1000 + i}"}, "tags": ["prod", "eu"]}
        h = {"_index": "logs-2026.03.02" if same_index or i < n - 1 else "logs-2026.03.01", "_id": hexid(i, 20, "id"), "_score": None, "_source": src,
             "fields": {"@timestamp": [ts], "http.status": [src["http"]["status"]], "host.name": [src["host"]["name"]], "tags": ["prod", "eu"], "event.duration_ms": [round(r.random() * 100, 1)]},
             "sort": [1772459400000 + i * 1000, hexid(i, 8, "tb")]}
        if i == n // 2:
            h["highlight"] = {"message": ["<em>upstream</em> connect error"]}
            h["matched_queries"] = ["errors"]
        hits.append(h)
    body = {"took": 12, "timed_out": abnormal, "_shards": {"total": 5, "successful": 3 if abnormal else 5, "skipped": 0, "failed": 2 if abnormal else 0, **({"failures": [{"shard": 3, "index": "logs-2026.03.02", "reason": {"type": "node_not_connected_exception", "reason": "[node-4][10.0.0.4:9300] disconnected"}}]} if abnormal else {})},
            "hits": {"total": {"value": 1523 if not abnormal else 10000, "relation": "eq" if not abnormal else "gte"}, "max_score": None, "hits": hits},
            "aggregations": {"by_service": {"doc_count_error_upper_bound": 0, "sum_other_doc_count": 0, "buckets": [{"key": "checkout", "doc_count": 600}, {"key": "cart", "doc_count": 500}, {"key": "payments", "doc_count": 423}]}}, "_scroll_id": "FGluY2x1ZGVfY29udGV4dF91dWlkDXF1ZXJ5QW5kRmV0Y2g="}
    return jp(body)


case(
    801,
    claim="An Elasticsearch/OpenSearch search response becomes '#es' header lines (total with relation, shared _index, the last sort, aggregations and other top-level keys such as _scroll_id, timed_out and failed shards when abnormal) plus one 'id TAB _source TAB extras' row per hit; every _source, _id, index, total, aggregation, highlight and non-duplicate fields entry is unchanged.",
    loses="took, max_score, _score, per-hit sort values except the last hit's (needed for search_after), normal _shards counts, `fields` entries that repeat _source, and JSON whitespace; responses without hits (count, health, bulk) and ids containing a tab are left unchanged",
    encode=flatten_es_hits,
    probes=safe_probes(probes_801),
    fixtures=lambda: [
        Fixture("40 hits in one index: error first, highlighted error in the middle, error last; aggregations and _scroll_id", _es_search(), True),
        Fixture("hits spread over two indices (the last from the previous day)", _es_search(24, same_index=False), True),
        Fixture("partial result: timed_out and failed shards, relation gte", _es_search(12, abnormal=True), True),
        Fixture("_cluster/health is not a search response", jp({"cluster_name": "prod", "status": "yellow", "timed_out": False, "number_of_nodes": 3, "active_shards": 120}), False),
        Fixture("a search response with no hits", jp({"took": 1, "timed_out": False, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}, "hits": {"total": {"value": 0, "relation": "eq"}, "max_score": None, "hits": []}}), False),
        Fixture("input that looks like the flat form", "#es total {\"value\":1}\nid1\t{\"a\":1}\t-\n", None),
    ],
    min_gain=0.3,
)

# captured with git 2.43 in this container: real `git diff` of notebooks written by json.dump(indent=1) as Jupyter does (images are 600-900 byte payloads)
GIT_DIFF_A = r'''diff --git a/forecast.ipynb b/forecast.ipynb
index fc7797e..7ac104d 100644
--- a/forecast.ipynb
+++ b/forecast.ipynb
@@ -11,7 +11,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 1,
+   "execution_count": 2,
    "id": "c0000001",
    "metadata": {},
    "outputs": [
@@ -30,7 +30,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 2,
+   "execution_count": 3,
    "id": "c0000002",
    "metadata": {},
    "outputs": [
@@ -49,7 +49,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 3,
+   "execution_count": 4,
    "id": "c0000003",
    "metadata": {},
    "outputs": [
@@ -68,7 +68,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 4,
+   "execution_count": 5,
    "id": "c0000004",
    "metadata": {},
    "outputs": [
@@ -87,13 +87,13 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 5,
+   "execution_count": 6,
    "id": "c0000005",
    "metadata": {},
    "outputs": [
     {
      "data": {
-      "image/png": "BSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOY\n",
+      "image/png": "CC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXab\n",
       "text/plain": [
        "<Figure size 640x480 with 1 Axes>"
       ]
@@ -109,7 +109,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 6,
+   "execution_count": 7,
    "id": "c0000006",
    "metadata": {},
    "outputs": [
@@ -128,7 +128,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 7,
+   "execution_count": 8,
    "id": "c0000007",
    "metadata": {},
    "outputs": [
@@ -147,7 +147,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 8,
+   "execution_count": 9,
    "id": "c0000008",
    "metadata": {},
    "outputs": [
@@ -166,7 +166,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 9,
+   "execution_count": 10,
    "id": "c0000009",
    "metadata": {},
    "outputs": [
@@ -185,13 +185,13 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 10,
+   "execution_count": 11,
    "id": "c0000010",
    "metadata": {},
    "outputs": [
     {
      "data": {
-      "image/png": "Ci9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3id\n",
+      "image/png": "DTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnug\n",
       "text/plain": [
        "<Figure size 640x480 with 1 Axes>"
       ]
@@ -207,7 +207,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 11,
+   "execution_count": 12,
    "id": "c0000011",
    "metadata": {},
    "outputs": [
@@ -226,7 +226,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 12,
+   "execution_count": 13,
    "id": "c0000012",
    "metadata": {},
    "outputs": [
@@ -245,7 +245,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 13,
+   "execution_count": 14,
    "id": "c0000013",
    "metadata": {},
    "outputs": [
@@ -264,7 +264,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 14,
+   "execution_count": 15,
    "id": "c0000014",
    "metadata": {},
    "outputs": [
'''  # counters-only re-run (same code, regenerated images)
GIT_DIFF_B = r'''diff --git a/forecast.ipynb b/forecast.ipynb
index fc7797e..c9611af 100644
--- a/forecast.ipynb
+++ b/forecast.ipynb
@@ -11,7 +11,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 1,
+   "execution_count": 2,
    "id": "c0000001",
    "metadata": {},
    "outputs": [
@@ -24,13 +24,14 @@
     }
    ],
    "source": [
-    "x1 = load('sheet1.csv')\n",
-    "print(len(x1))"
+    "x1 = load(\"sheet1.csv\", sep=\";\")\n",
+    "print(len(x1))\n",
+    "assert \"id\" in x1.columns"
    ]
   },
   {
    "cell_type": "code",
-   "execution_count": 2,
+   "execution_count": 3,
    "id": "c0000002",
    "metadata": {},
    "outputs": [
@@ -49,7 +50,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 3,
+   "execution_count": 4,
    "id": "c0000003",
    "metadata": {},
    "outputs": [
@@ -68,7 +69,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 4,
+   "execution_count": 5,
    "id": "c0000004",
    "metadata": {},
    "outputs": [
@@ -87,13 +88,13 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 5,
+   "execution_count": 6,
    "id": "c0000005",
    "metadata": {},
    "outputs": [
     {
      "data": {
-      "image/png": "BSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOY\n",
+      "image/png": "CC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXab\n",
       "text/plain": [
        "<Figure size 640x480 with 1 Axes>"
       ]
@@ -109,7 +110,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 6,
+   "execution_count": 7,
    "id": "c0000006",
    "metadata": {},
    "outputs": [
@@ -128,7 +129,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 7,
+   "execution_count": 8,
    "id": "c0000007",
    "metadata": {},
    "outputs": [
@@ -147,7 +148,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 8,
+   "execution_count": 9,
    "id": "c0000008",
    "metadata": {},
    "outputs": [
@@ -160,13 +161,13 @@
     }
    ],
    "source": [
-    "x8 = load('sheet8.csv')\n",
+    "x8 = load(\"sheet8.csv\").dropna()\n",
     "print(len(x8))"
    ]
   },
   {
    "cell_type": "code",
-   "execution_count": 9,
+   "execution_count": 10,
    "id": "c0000009",
    "metadata": {},
    "outputs": [
@@ -185,13 +186,13 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 10,
+   "execution_count": 11,
    "id": "c0000010",
    "metadata": {},
    "outputs": [
     {
      "data": {
-      "image/png": "Ci9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3id\n",
+      "image/png": "DTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnug\n",
       "text/plain": [
        "<Figure size 640x480 with 1 Axes>"
       ]
@@ -207,7 +208,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 11,
+   "execution_count": 12,
    "id": "c0000011",
    "metadata": {},
    "outputs": [
@@ -226,7 +227,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 12,
+   "execution_count": 13,
    "id": "c0000012",
    "metadata": {},
    "outputs": [
@@ -245,7 +246,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 13,
+   "execution_count": 14,
    "id": "c0000013",
    "metadata": {},
    "outputs": [
@@ -264,7 +265,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 14,
+   "execution_count": 15,
    "id": "c0000014",
    "metadata": {},
    "outputs": [
@@ -277,8 +278,9 @@
     }
    ],
    "source": [
-    "x14 = load('sheet14.csv')\n",
-    "print(len(x14))"
+    "x14 = load(\"sheet14.csv\")\n",
+    "forecast = x14.groupby(\"q\").sum()\n",
+    "print(forecast.tail())"
    ]
   }
  ],
'''  # re-run with code edits in cells 1, 8 and 14
GIT_DIFF_C = r'''diff --git a/churn.ipynb b/churn.ipynb
index 0700ffd..e919bfa 100644
--- a/churn.ipynb
+++ b/churn.ipynb
@@ -11,7 +11,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 1,
+   "execution_count": 2,
    "id": "e5f6a7b8",
    "metadata": {},
    "outputs": [],
@@ -23,7 +23,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 2,
+   "execution_count": 3,
    "id": "09c1d2e3",
    "metadata": {},
    "outputs": [
@@ -36,19 +36,20 @@
     }
    ],
    "source": [
-    "df = pd.read_csv(\"data/churn.csv\")\n",
-    "print(f\"loaded {len(df)} rows\")"
+    "df = pd.read_csv(\"data/churn.csv\", na_values=[\"\", \" \"])\n",
+    "print(f\"loaded {len(df)} rows\")\n",
+    "df[\"TotalCharges\"] = pd.to_numeric(df[\"TotalCharges\"], errors=\"coerce\")"
    ]
   },
   {
    "cell_type": "code",
-   "execution_count": 3,
+   "execution_count": 4,
    "id": "f4a5b6c7",
    "metadata": {},
    "outputs": [
     {
      "data": {
-      "image/png": "iVBORw0KGgoAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8=\n",
+      "image/png": "iVBORw0KGgoBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/AVOl+Eqc7zGD1ih6zR9htAZYqv1PkeQ2iNstf8IUZrkLXa/yRJbpO43QInTHGWu+AFKk90mb7jCC1Sd5zB5gswVXqfxOkOM1h9osfsETZbgKXK7xQ5XoOozfIXPGGGq9D1Gj9kia7T+B1CZ4yx1vsgRWqPtNn+I0htkrfcASZLcJW63wQpTnOYveIHLFF2m8DlCi9UeZ7D6A0yV3yhxusQNVp/pMnuEzhdgqfM8RY7YIWqz/QZPmOIrdL3HEFmi7DV+h9EaY6z2P0iR2yRttsAJUpvlLneAyhNcpe84QYrUHWav+QJLlN4ncLnDDFWe6DF6g80WX6jyO0SN1yBpsvwFTpfhKnO8xg9Yoes0fYbQGWKr9T5HkNojbLX/CFGa5C12v8kSW6TuN0CJ0xxlrvgBSpPdJm+4wgtUnecweYLMFV6n8TpDjNYfaLH7BE2W4Clyu8UOV6DqM3yFzxhhqvQ9Ro/ZImu0/gdQmeMsdb7IEVqj7TZ/iNIbZK33AEmS3CVut8EKU5zmL3iByxRdpvA5QovVHmew+gNMld8ocbrEDVaf6TJ7hM4XYKnzPEWO2CFqs/0GT5jiK3S9xxBZouw1fofRGmOs9j9IkdskbbbACVKb5S53gMoTXKXvOEGK1B1mr/kCS5TeJ3C5wwxVnugxeoPNFl+o8jtEjdcgabL8BU6X4SpzvMYPWKHrNH2G0Bliq/U+R5DaI2y1/whRmuQtdr/JEluk7jdAidMcZa74AUqT3SZvuMILVJ3nMHmCzBVep/E6Q4zWH2ix+wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW+yBFao+02f4jSG2St9wBJktwlbrfBClOc5i94gcsUXabwOUKL1R5nsPoDTJXfKHG6xA1Wn+kye4TOF2Cp8zxFjtgharP9Bk+Y4it0vccQWaLsNX6H0RpjrPY/SJHbJG22wAlSm+Uud4DKE1yl7zhBitQdZq/5AkuU3idwucMMVZ7oMXqDzRZfqPI7RI3XIGmy/A=\n",
       "text/plain": [
        "<Figure size 640x480 with 1 Axes>"
       ]
@@ -67,7 +68,7 @@
   },
   {
    "cell_type": "code",
-   "execution_count": 4,
+   "execution_count": 5,
    "id": "d8e9f0a1",
    "metadata": {},
    "outputs": [
@@ -77,7 +78,7 @@
        "0.2654"
       ]
      },
-     "execution_count": 4,
+     "execution_count": 5,
      "metadata": {},
      "output_type": "execute_result"
     }
'''  # re-run with one code edit and a changed image


# ===============================================================================================================
# #880 Decode JSON-escaped source lines in notebook git diffs.
# Only inside 'diff --git' sections of *.ipynb files: a bare JSON string element line becomes 'm|text' (decoded, trailing \n and the
# comma dropped), execution_count lines are folded into one '~|execution_count -a +b' per hunk, image payloads become '<blob mime N>'
# stubs, and a hunk whose only changes are counters and payloads loses its context lines too. Every other line is untouched.

NB_LEGEND = "#ipynb-decoded: 'm|text' = one JSON string element (trailing \\n and comma dropped), '~|' = folded execution_count lines, <blob ..> = payload size"
_CTR = re.compile(r'([ +-])\s*"execution_count": (?:null|\d+),?$')
_STR = re.compile(r'([ +-])\s*("(?:[^"\\]|\\.)*"),?$')
_BLOB_KV = re.compile(r'([ +-])\s*"((?:image/[\w.+-]+)|application/pdf)": "((?:[^"\\]|\\.)*)",?$')
_B64 = re.compile(r"(?:[A-Za-z0-9+/=]|\\n)+\Z")


def _decode_line(ln: str):
    """('str'|'blob'|'ctr'|'raw', marker, text) for one content line of an .ipynb hunk."""
    m = ln[:1]
    if m not in (" ", "+", "-"):
        return "raw", m, ln
    if _CTR.match(ln):
        return "ctr", m, ln
    if b := _BLOB_KV.match(ln):
        return "blob", m, f"<blob {b.group(2)} {len(b.group(3))}>"
    if s := _STR.match(ln):
        try:
            val = json.loads(s.group(2))
        except ValueError:
            return "raw", m, ln
        body = s.group(2)[1:-1]
        if len(body) >= 200 and _B64.match(body):
            return "blob", m, f"<blob base64 {len(body)}>"
        if val.endswith("\n"):
            val = val[:-1]
        if "\n" in val or any(ord(c) < 32 and c != "\t" for c in val):
            return "raw", m, ln
        return "str", m, val
    return "raw", m, ln


def _fold_hunk(lines: list[str]) -> list[str]:
    items = [_decode_line(x) for x in lines]
    meaningful = any(k in ("str", "raw") and m in ("+", "-") for k, m, _t in items)
    removed = sum(k == "ctr" and m == "-" for k, m, _t in items)
    added = sum(k == "ctr" and m == "+" for k, m, _t in items)
    out = []
    for (k, m, t), raw in zip(items, lines):
        if k == "ctr":
            continue
        if k in ("str", "blob"):
            if meaningful or m in ("+", "-"):
                out.append(f"{m}|{t}")
        elif meaningful:
            out.append(raw)
    if removed or added:
        out.append(f"~|execution_count -{removed} +{added}")
    return out


NB_FILE = re.compile(r"diff --git a/(.*\.ipynb) b/(.*\.ipynb)$")


@total
def decode_ipynb_diff(text: str) -> str:
    if NB_LEGEND[:14] in text:
        return text
    lines = text.split("\n")
    out, i, n, changed = [], 0, len(lines), False
    while i < n:
        out.append(lines[i])
        is_nb = bool(NB_FILE.match(lines[i]))
        i += 1
        if not is_nb:
            continue
        header = []
        while i < n and not lines[i].startswith(("diff --git ", "@@")):
            header.append(lines[i])
            i += 1
        if i < n and lines[i].startswith("@@"):
            out.append(NB_LEGEND)
        out += header
        while i < n and not lines[i].startswith("diff --git "):
            out.append(lines[i])  # the @@ line
            i += 1
            body = []
            while i < n and not lines[i].startswith(("@@", "diff --git ")):
                body.append(lines[i])
                i += 1
            last_blank = i >= n and bool(body) and body[-1] == ""  # the newline that ends the whole text
            if last_blank:
                body.pop()
            if any(re.match(r"[ +-]\||~\|", x) for x in body):
                return text
            out += _fold_hunk(body) + ([""] if last_blank else [])
            changed = True
    return gate(text, "\n".join(out)) if changed else text


def _body_lines(text: str) -> list[str]:
    ls = text.split("\n")
    return ls[:-1] if ls and ls[-1] == "" else ls


def _diff_events(text: str) -> list:
    """Probe-side reading of a git diff (original or decoded) as a flat list of facts, tracking the current file and hunk."""
    ev, nb, hunk, started = [], False, None, False
    pending = []

    def flush():
        nonlocal pending
        if hunk is None:
            return
        kinds = [(_decode_line(x) if not x.startswith(("~|",)) else None) for x in pending]
        meaningful = any(k and k[0] in ("str", "raw") and k[1] in ("+", "-") for k in kinds)
        ctr = [0, 0]
        for k in kinds:
            if k and k[0] == "ctr":
                ctr[k[1] == "+"] += 1
        for k in kinds:
            if k is None or k[0] == "ctr":
                continue
            if k[0] in ("str", "blob"):
                if meaningful or k[1] in ("+", "-"):
                    ev.append(("line", k[1], k[2]))
            elif meaningful:
                ev.append(("raw", k[2]))
        if ctr[0] or ctr[1]:
            ev.append(("counters", ctr[0], ctr[1]))
        pending = []

    for ln in _body_lines(text):
        if ln.startswith("diff --git "):
            flush()
            hunk = None
            nb = bool(re.match(r"diff --git a/(.*\.ipynb) b/(.*\.ipynb)$", ln))
            ev.append(("file", ln))
        elif nb and ln.startswith("@@"):
            flush()
            hunk = ln
            ev.append(("hunk", ln))
        elif nb and hunk is not None:
            pending.append(ln)
        elif ln.startswith("#ipynb-decoded"):
            continue
        else:
            ev.append(("raw", ln))
    flush()
    return ev


def _encoded_events(text: str) -> list:
    ev, nb, hunk = [], False, False
    for ln in _body_lines(text):
        if ln.startswith("diff --git "):
            nb, hunk = bool(re.match(r"diff --git a/(.*\.ipynb) b/(.*\.ipynb)$", ln)), False
            ev.append(("file", ln))
        elif ln.startswith("#ipynb-decoded"):
            continue
        elif nb and ln.startswith("@@"):
            hunk = True
            ev.append(("hunk", ln))
        elif nb and hunk and (m := re.match(r"~\|execution_count -(\d+) \+(\d+)$", ln)):
            ev.append(("counters", int(m.group(1)), int(m.group(2))))
        elif nb and hunk and (m := re.match(r"([ +-])\|(.*)$", ln)):
            ev.append(("line", m.group(1), m.group(2)))
        else:
            ev.append(("raw", ln))
    return ev


def probes_880(text: str) -> dict:
    ev = _encoded_events(text) if "#ipynb-decoded" in text else _diff_events(text)
    changed = [(e[1], e[2]) for e in ev if e[0] == "line" and e[1] in ("+", "-")]
    return {"events (files, hunks, decoded lines, folded counters, other lines)": ev, "changed lines": changed,
            "counter folds": [(e[1], e[2]) for e in ev if e[0] == "counters"], "files": [e[1] for e in ev if e[0] == "file"]}


_PY_DIFF = "diff --git a/app.py b/app.py\nindex 83db48f..bf2a3c1 100644\n--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,4 @@\n import os\n+import sys\n def main():\n     print(\"hi\\n\")\n"
_MIXED_DIFF = _PY_DIFF + GIT_DIFF_C
_JSON_DIFF = "diff --git a/package.json b/package.json\nindex 1..2 100644\n--- a/package.json\n+++ b/package.json\n@@ -1,4 +1,4 @@\n {\n-  \"execution_count\": 1,\n+  \"execution_count\": 2,\n   \"x\": \"abc\\n\",\n }\n"

case(
    880,
    claim="In 'diff --git' sections of .ipynb files, JSON string element lines are decoded to 'm|text', execution_count lines are folded to one '~|execution_count -a +b' per hunk, image payloads become '<blob mime N>' stubs, and a hunk whose only changes are counters and payloads keeps no context lines; every added, removed and context source line, every hunk header, every other line and every other file's diff is unchanged.",
    loses="the trailing \\n and comma of each decoded element (so which element was last in an array), the execution_count values and their context lines, image payload bytes (size only), and context lines of hunks that changed nothing but counters and images; a diff that already holds the legend or a '|' decoded-looking line inside a notebook is left unchanged",
    encode=decode_ipynb_diff,
    probes=safe_probes(probes_880),
    fixtures=lambda: [
        Fixture("real git diff: re-run only, 15 counter hunks and 3 image hunks, no code change", GIT_DIFF_A, True),
        Fixture("real git diff: code edits in the first, middle and last cell plus re-run", GIT_DIFF_B, True),
        Fixture("real git diff: one code edit and a changed image", GIT_DIFF_C, True),
        Fixture("a .py diff followed by the notebook diff", _MIXED_DIFF, True),
        Fixture("diff of a .py file", _PY_DIFF, False),
        Fixture("diff of a non-notebook JSON file with the same lines", _JSON_DIFF, False),
        Fixture("a notebook hunk that already holds a decoded-looking line", GIT_DIFF_C.replace('+   "execution_count": 4,', '+|x\n+   "execution_count": 4,'), None),
    ],
    min_gain=0.3,
)
