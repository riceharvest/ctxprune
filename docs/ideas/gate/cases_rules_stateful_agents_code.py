"""Gate cases for the shortlisted RULE ideas of the "stateful agents and code" slice:
#127 #141 #142 #189 #206 #209 #288 #304 #305 #387 #449 #485 #503 #579 #593 #684 #946 #1302 #1303 #1324.

Four of them are text rules with probes (#127 audit digest, #206 source skeleton, #209 BM25 chunks, #305 ranked bodies);
the other sixteen depend on state or policy (ledgers, MCP wrappers and gateways, shell shims, PATH, SHELL and BASH_ENV
overrides, sparse worktrees, history rewriting), so they are sims with a reader view, invalidation cases and fail-open.
Where the idea is about a process boundary the sim runs REAL processes in a temp dir: bash, python, git, grep, find, jq,
ruff. Rules for those: stdout and stderr are captured on separate pipes and no assertion depends on how two pipeline
stages interleave. The independent oracles are python's ast module (#206, #305), real jq (#304), real git (#1303),
real grep/find/ls (#1302) and real bash exit statuses (#189, #579, #946).

Honest caveats live next to each case (what the probes caught, what did not reproduce). Everything here is a
prototype of the idea, not shipped code.
"""
import ast
import hashlib
import itertools
import json
import math
import os
import re
import shlex
import shutil
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

def _run(argv, cwd=None, env=None, stdin: bytes | None = None, timeout: int = 20):
    """Run a real process. stdout and stderr are separate pipes (never merged), so nothing here depends on interleaving."""
    e = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": cwd or "/tmp", **(env or {})}
    p = subprocess.run(argv, cwd=cwd, env=e, input=stdin, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _write(path: str, text: str, mode: int | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)
    return path


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
    real = shutil.which("ruff") is not None
    with tempfile.TemporaryDirectory() as d:
        outs = []
        for v in versions:
            if real:
                _write(os.path.join(d, "a.py"), v)
                rc, out, _ = _run(["ruff", "check", "--isolated", "--no-cache", "--select", "F,E", "--output-format=concise", "a.py"], cwd=d)
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
        runs = [_pytest_run(10, {}, 0.02), _pytest_run(10, {}, 0.03), _pytest_run(11, {}, 0.02), _pytest_run(11, {"7": "280"}, 0.03), _pytest_run(11, {"7": "280"}, 0.02), _pytest_run(11, {}, 0.03)]
        shown = [bl.show(fam, r) for r in runs]
        assert shown[:3] == runs[:3], "no hiding before three prior runs exist"
        assert shown[3] != runs[3] and "lines identical in each of the last 3 runs" in shown[3], "from the fourth run the invariant lines are hidden"
        # --- the safety checks, on every run: failure lines visible, count honest, nothing unaccounted for
        tot_before = tot_after = 0
        for k in range(3, 6):
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
        assert "FAILED tests/test_shop.py::test_case_7 - assert 300 == 280" in shown[4], "the failing test that fails in all prior runs too is still shown"
        assert "1 failed, 10 passed" in shown[4]
        # --- invalidation by construction
        # a tool upgrade changes the banner line: it is visible again immediately and is not baseline until three runs carry it
        up = bl.show(fam, _pytest_run(11, {}, 0.03, version="9.2.0"))
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
    return {"pytest_chars_saved_runs_4_to_6": f"{py_saving:.0%}", "npm_test_chars_saved_run_5": f"{saving:.0%}", "lines_hidden_pytest_run_4": int(re.match(r"\[ctxprune: (\d+)", shown[3]).group(1)),
            "failure_lines_hidden": 0}


case(
    142,
    claim="Lines identical in each of the last three runs of a command family (cwd, program, output-shaping flags) are hidden behind one counted header with the full output saved; failure, assertion and error lines are never hidden even when they repeat in every run, a line missing from any prior run or changed by a tool upgrade stays visible, and corrupt history fails open.",
    sim=sim_142,
)
