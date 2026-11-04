"""Stateful cross-call dedup: ledgers and shadows over a scripted tool session.

Ideas #60 #61 #62 #63 #138 #139 #140 #144 #349 #530 #763 #908 #909 #1264 #1265 #1322 (sims) and #380 #483 (transforms).

Every sim runs a short scripted session of tool events through a hook and checks four things:
  1. the saving happens (tokens via harness.tokens, asserted as a number),
  2. a READER VIEW: a function that sees only what the model would see (`Session.ctx`: tool inputs and the possibly
     rewritten tool results still in context) and rebuilds, or can locate, the full content the tool really returned,
  3. the invalidation cases where the ledger must not apply (file edited by something else, size or mtime change,
     different range, different tool args, partial overlap, context compaction),
  4. fail open: a broken ledger hands the model the original output.

Conventions: `turn N` in a stub stands for whatever locator the model can really follow (tool_use id, path plus
ordinal); `Session.ctx` is the model's whole context, and `Session.compact()` drops it (the ledger must forget too).
A Read result is `cat -n` style numbered lines; Read does not show whether the file ends with a newline, so file
identity is compared as a list of lines. Stdlib only.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import random
import re
import tempfile
from collections import Counter

from harness import Fixture, case, tokens

MARK = "[ctxprune:"  # every text stub starts with this; raw output containing it is never rewritten (in-band guard)

# ---------------------------------------------------------------------------------------------------------------
# plumbing shared by the sims


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest()[:16]


_ITEM = re.compile(r"[^\n]*\n|[^\n]+")


def items_of(text: str) -> list[str]:
    """Lines that keep their own '\\n'; a final unterminated line is an item too (so join(items) == text)."""
    return _ITEM.findall(text)


def lines_of(text: str) -> list[str]:
    if not text:
        return []
    return (text[:-1] if text.endswith("\n") else text).split("\n")


def numbered(lines: list[str], start: int = 1) -> str:
    return "\n".join(f"{n:6d}\t{l}" for n, l in enumerate(lines, start))


_NUM = re.compile(r"^ *(\d+)\t(.*)$", re.S)


def parse_numbered(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    if text == "":
        return out
    for l in text.split("\n"):
        m = _NUM.match(l)
        if not m:
            raise ValueError(f"not a numbered line: {l!r}")
        out[int(m.group(1))] = m.group(2)
    return out


def py_module(name: str, n: int) -> str:
    """A plausible python module: n small functions of 8 lines each."""
    out = [f'"""{name}: pipeline stages."""', "import os", "import sys", ""]
    for i in range(n):
        out += [f"def {name}_{i}(a, b=0):", f'    """Compute step {i} of the {name} pipeline."""', f"    total = a * {i + 2} + b",
                f"    if total > {100 * (i + 1)}:", f"        return total - {i + 1}", "    return total", "", ""]
    return "\n".join(out) + "\n"


class Workspace:
    """Real files in a temp dir with controllable mtimes, so stat() behaves like the real thing."""

    def __init__(self, root: str):
        self.root, self.clock = root, 1_700_000_000 * 10**9

    def p(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def put(self, rel: str, text: str, mtime_ns: int | None = None) -> None:
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if mtime_ns is None:
            self.clock += 10**9
            mtime_ns = self.clock
        os.utime(path, ns=(mtime_ns, mtime_ns))

    def touch(self, rel: str) -> None:
        self.clock += 10**9
        os.utime(self.p(rel), ns=(self.clock, self.clock))

    def get(self, rel: str) -> str:
        with open(self.p(rel), encoding="utf-8", newline="") as f:
            return f.read()

    def stat(self, rel: str):
        try:
            s = os.stat(self.p(rel))
        except OSError:
            return None
        return s.st_mtime_ns, s.st_size, s.st_ino

    def files(self, sub: str = "") -> list[str]:
        out = []
        for dp, _dn, fn in os.walk(self.p(sub)):
            out += [os.path.relpath(os.path.join(dp, f), self.root) for f in fn]
        return sorted(out)


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> str:
    n = text.count(old) if old else 0
    if n == 0:
        raise ValueError("string to replace not found")
    if n > 1 and not replace_all:
        raise ValueError(f"{n} matches")
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


class Tools:
    """Read/Edit/Write as far as these ideas need: Read returns numbered lines; Edit and Write demand a prior Read
    that is still current (mtime unchanged since that read), like the real read-first requirement."""

    def __init__(self, ws: Workspace):
        self.ws, self.read_state = ws, {}

    def read(self, path: str, offset: int = 1, limit: int = 2000) -> str:
        st = self.ws.stat(path)
        if st is None:
            return f"Error: File does not exist: {path}"
        lines = lines_of(self.ws.get(path))
        self.read_state[path] = st[0]
        return numbered(lines[offset - 1: offset - 1 + limit], offset)

    def _check(self, path: str) -> str | None:
        st = self.ws.stat(path)
        if st is None:
            return "Error: File does not exist."
        if path not in self.read_state:
            return "Error: File has not been read yet. Read it first before writing to it."
        if self.read_state[path] != st[0]:
            return "Error: File has been modified since read, either by the user or by a linter. Read it again before attempting to write it."
        return None

    def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        err = self._check(path)
        if err:
            return err
        try:
            self.ws.put(path, apply_edit(self.ws.get(path), old, new, replace_all))
        except ValueError as ex:
            return f"Error: {ex}"
        self.read_state[path] = self.ws.stat(path)[0]
        return f"The file {path} has been updated."

    def write(self, path: str, content: str) -> str:
        if self.ws.stat(path) is not None:
            err = self._check(path)
            if err:
                return err
        self.ws.put(path, content)
        self.read_state[path] = self.ws.stat(path)[0]
        return f"File created successfully at: {path}"


def fail_open(post):
    """Hook wrapper: whatever goes wrong inside the ledger code, the model gets the original output."""

    def wrapped(self, turn, tool, args, raw):
        try:
            return post(self, turn, tool, args, raw)
        except Exception:  # noqa: BLE001
            return raw

    return wrapped


class Session:
    """A scripted agent session. `ctx` is exactly what the model sees: its own tool inputs and the (possibly
    rewritten) results. hook.post(turn, tool, args, raw) -> what to show; hook.on_compact() on compaction."""

    def __init__(self, hook=None):
        self.hook, self.ctx, self.turn, self.raw_tok, self.seen_tok = hook, [], 0, 0, 0

    def say(self, label: str, text: str, pinned: bool = False) -> int:
        """Text the model holds without a tool call: system prompt files, @-attachments, prompts, its own messages."""
        self.turn += 1
        self.ctx.append(dict(turn=self.turn, kind="msg", label=label, text=text, pinned=pinned))
        if self.hook is not None and hasattr(self.hook, "seed"):
            self.hook.seed(label, text, pinned)
        return self.turn

    def step(self, tool: str, args: dict, raw: str) -> str:
        self.turn += 1
        self.ctx.append(dict(turn=self.turn, kind="use", tool=tool, args=args))
        seen = self.hook.post(self.turn, tool, args, raw) if self.hook else raw
        self.ctx.append(dict(turn=self.turn, kind="result", tool=tool, args=args, text=seen))
        self.raw_tok += tokens(raw)
        self.seen_tok += tokens(seen)
        return seen

    def result(self, turn: int) -> dict:
        return next(e for e in self.ctx if e["turn"] == turn and e["kind"] == "result")

    def use(self, turn: int) -> dict:
        return next(e for e in self.ctx if e["turn"] == turn and e["kind"] == "use")

    def compact(self) -> None:
        self.ctx = [e for e in self.ctx if e.get("pinned")]
        if self.hook:
            self.hook.on_compact()

    @property
    def saved(self) -> int:
        return self.raw_tok - self.seen_tok


def is_stub(text: str) -> bool:
    return text.startswith(MARK)


# ---------------------------------------------------------------------------------------------------------------
# #60 Re-read of an unchanged file -> stub. Decided by the hash of the text the Read returned (content and range),
# never by mtime alone: a rewrite that keeps size and mtime must still reach the model.


class RereadStub:
    def __init__(self):
        self.seen: dict | None = {}

    def on_compact(self):
        self.seen = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "Read" or MARK in raw or raw.startswith("Error") or not raw:
            return raw
        key = (args["path"], args.get("offset", 1), args.get("limit", 2000))
        h, prev = sha(raw), self.seen.get(key)
        if prev is None or prev[0] != h:
            self.seen[key] = (h, turn)  # the turn of the last LITERAL result, so a stub never points at a stub
            return raw
        stub = f"{MARK} unchanged since your Read at turn {prev[1]}: {args['path']}, {raw.count(chr(10)) + 1} lines, sha {h[:8]}]"
        return stub if len(stub) < len(raw) else raw


def resolve_read_stub(s: Session, stub: str) -> str:
    """Reader view: the stub names a turn; the literal result there must be the text the Read would have returned."""
    m = re.fullmatch(r"\[ctxprune: unchanged since your Read at turn (\d+): (\S+), \d+ lines, sha \w+\]", stub)
    ev = s.result(int(m.group(1)))
    assert ev["tool"] == "Read" and ev["args"]["path"] == m.group(2) and not is_stub(ev["text"]), "stub must point at a literal Read of the same path"
    return ev["text"]


def sim_reread() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws, hook = Workspace(d), RereadStub()
        tools, s = Tools(ws), Session(hook)
        src = py_module("svc", 25)
        ws.put("a.py", src)

        def read(path="a.py", **kw):
            raw = tools.read(path, **kw)
            seen = s.step("Read", {"path": path, **kw}, raw)
            if is_stub(seen):
                assert resolve_read_stub(s, seen) == raw, "reader view must rebuild exactly what the Read returned"
            return raw, seen

        raw, seen = read()
        assert seen == raw, "first read is shown in full"
        raw, seen = read()
        assert is_stub(seen) and len(seen) < len(raw) / 20, "identical re-read becomes a one-line stub"
        raw, seen = read(offset=10, limit=20)
        assert seen == raw, "a different range is a different key: shown in full"
        raw, seen = read(offset=10, limit=20)
        assert is_stub(seen), "same range again is stubbed"
        ws.put("b.py", src)
        raw, seen = read("b.py")
        assert seen == raw, "same bytes under another path are not this idea's business (#140 does that)"
        # the model's own edit changes the file
        assert "updated" in tools.edit("a.py", "svc_3", "svc_three", replace_all=True)
        raw, seen = read()
        assert seen == raw and "svc_three" in raw, "edited file is shown in full"
        # an edit by something else (formatter) -> full; then the new baseline is the formatter's output
        ws.put("a.py", ws.get("a.py").replace("svc_4", "svc_four"))
        raw, seen = read()
        assert seen == raw and "svc_four" in raw
        t_full = s.turn
        raw, seen = read()
        assert is_stub(seen) and f"turn {t_full}:" in seen, "stub points at the newest literal result, not turn 1"
        # touch: mtime moves, bytes do not -> still unchanged for the reader
        ws.touch("a.py")
        raw, seen = read()
        assert is_stub(seen) and f"turn {t_full}:" in seen
        # same size, same mtime, different byte: a stat-only key would be fooled here
        before = ws.stat("a.py")
        ws.put("a.py", ws.get("a.py").replace("svc_5", "svc_6"), mtime_ns=before[0])
        assert ws.stat("a.py")[:2] == before[:2], "premise: stat is identical"
        raw, seen = read()
        assert seen == raw and not is_stub(seen), "hash notices a rewrite that preserved size and mtime"
        # an Edit after a stubbed Read still has its read-first requirement met: only the output was rewritten
        raw, seen = read()
        assert is_stub(seen)
        assert "updated" in tools.edit("a.py", "svc_0(a", "svc_zero(a")
        # compaction empties the context: the ledger must forget or the stub would point at nothing
        s.compact()
        raw, seen = read()
        assert seen == raw, "after compaction the first read is literal again"
        # tiny file: the stub would be longer than the text, so the text stays
        ws.put("t.txt", "x\ny\n")
        read("t.txt")
        raw, seen = read("t.txt")
        assert seen == raw, "self-gating"
        # failure handling: missing file's error text is untouched, and a corrupted ledger fails open
        raw, seen = read("nope.py")
        assert seen == raw and raw.startswith("Error")
        raw, seen = read()
        assert is_stub(seen)
        hook.seen = None
        raw, seen = read()
        assert seen == raw, "ledger exception -> original output"
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    60,
    claim="A Read whose returned text hashes the same as an earlier Read of the same path and range becomes a one-line pointer to that turn; edits by the model or anyone else, a different range or path, compaction and a rewrite that kept size and mtime all show the full text.",
    sim=sim_reread,
)

# ---------------------------------------------------------------------------------------------------------------
# #61 Re-run with identical output -> stub. Key: (cwd, command); the raw output includes the exit-code line, so
# identical output means identical exit status too.


def shell(stdout: str, code: int = 0) -> str:
    return stdout if code == 0 else f"Exit code {code}\n{stdout}"


class RerunStub:
    def __init__(self):
        self.last: dict | None = {}

    def on_compact(self):
        self.last = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "Bash" or MARK in raw:
            return raw
        key, h = (args.get("cwd", ""), args["command"]), sha(raw)
        prev = self.last.get(key)
        if prev is None or prev[0] != h:
            self.last[key] = (h, turn)
            return raw
        stub = f"{MARK} same output (incl. exit status) as your `{args['command'][:50]}` at turn {prev[1]}, {raw.count(chr(10)) + 1} lines, sha {h[:8]}]"
        return stub if len(stub) < len(raw) else raw


def resolve_rerun_stub(s: Session, stub: str, args: dict) -> str:
    m = re.fullmatch(r"\[ctxprune: same output \(incl\. exit status\) as your `(.*)` at turn (\d+), \d+ lines, sha \w+\]", stub)
    ev = s.result(int(m.group(2)))
    assert ev["tool"] == "Bash" and ev["args"]["command"] == args["command"] and ev["args"].get("cwd", "") == args.get("cwd", ""), "same command, same cwd"
    assert not is_stub(ev["text"])
    return ev["text"]


def sim_rerun() -> dict:
    status = "".join(f" M src/service/handler_{i}.py\n" for i in range(9)) + "".join(f"?? scratch/notes_{i}.txt\n" for i in range(5))
    s = Session(RerunStub())

    def run(cmd, raw, cwd=""):
        args = {"command": cmd, "cwd": cwd}
        seen = s.step("Bash", args, raw)
        if is_stub(seen):
            assert resolve_rerun_stub(s, seen, args) == raw, "reader view must rebuild the output exactly"
        return seen

    assert run("git status --short", status) == status
    t1 = s.turn
    seen = run("git status --short", status)
    assert is_stub(seen) and f"turn {t1}" in seen and len(seen) < len(status) / 2
    assert run("git status -s", status) == status, "different command string: key differs, shown in full"
    assert run("git status --short", status, cwd="/other/repo") == status, "different cwd: shown in full"
    pytest_a = "FAILED tests/test_a.py::test_x - assert 3 == 4\n" * 6 + "6 failed, 30 passed in 0.52s\n"
    pytest_b = pytest_a.replace("0.52s", "0.55s")
    assert run("pytest -q", pytest_a) == pytest_a
    assert run("pytest -q", pytest_b) == pytest_b, "timing line differs: not identical, shown in full (#62 territory)"
    status2 = status + "?? new_file.py\n"
    assert run("git status --short", status2) == status2
    t2 = s.turn
    seen = run("git status --short", status2)
    assert is_stub(seen) and f"turn {t2}" in seen, "after a change the stub points at the newest literal output, not turn 1"
    body = "check: 3 problems in src/service/handler_1.py and 2 more files\n" * 4
    assert run("./check.sh", shell(body, 1)) == shell(body, 1)
    assert run("./check.sh", shell(body, 0)) == shell(body, 0), "same text, other exit status: shown in full"
    assert run("echo ok", "ok\n") == "ok\n" and run("echo ok", "ok\n") == "ok\n", "tiny output: a stub would be longer"
    s.compact()
    assert run("git status --short", status2) == status2, "after compaction: literal again"
    hook = s.hook
    assert is_stub(run("git status --short", status2))
    hook.last = None
    assert run("git status --short", status2) == status2, "fail open"
    return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    61,
    claim="A re-run of the same command in the same cwd with byte-identical output (exit status included) becomes a one-line pointer to the previous literal output; changed output, another command string or cwd, a different exit status or compaction show the full text.",
    sim=sim_rerun,
)

# ---------------------------------------------------------------------------------------------------------------
# #62 Re-run with changed output -> line delta against the previous run. Exactly reversible given that output:
# `keep N` = N lines identical to the previous output, `drop N` = N of its lines removed, `add N` = N literal lines
# follow. Chains are capped (keyframe) so a reader never replays more than MAX_CHAIN deltas.

MAX_CHAIN = 3


def make_delta(prev: list[str], new: list[str]) -> str:
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, prev, new, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(f"keep {i2 - i1}\n")
            continue
        if i2 > i1:
            out.append(f"drop {i2 - i1}\n")
        if j2 > j1:
            out.append(f"add {j2 - j1}\n")
            out += new[j1:j2]
    return "".join(out)


def apply_delta(prev: list[str], delta: str) -> list[str]:
    out, i, pos = [], 0, 0
    while pos < len(delta):
        nl = delta.index("\n", pos)
        op, n = delta[pos:nl].split(" ")
        n, pos = int(n), nl + 1
        if op == "keep":
            out += prev[i:i + n]
            i += n
        elif op == "drop":
            i += n
        elif op == "add":
            for _ in range(n):
                m = _ITEM.match(delta, pos)
                out.append(m.group())
                pos = m.end()
        else:
            raise ValueError(op)
    if i != len(prev):
        raise ValueError("delta does not consume the whole previous output")
    return out


class RerunDelta:
    HDR = "{m} delta vs your run at turn {t}: keep/drop N = lines of that output, add N = N literal lines follow]\n"

    def __init__(self):
        self.prev: dict | None = {}

    def on_compact(self):
        self.prev = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "Bash" or MARK in raw:
            return raw
        key, prev, out, depth = (args.get("cwd", ""), args["command"]), None, raw, 0
        prev = self.prev.get(key)
        if prev and prev["depth"] < MAX_CHAIN:
            cand = self.HDR.format(m=MARK, t=prev["turn"]) + make_delta(items_of(prev["text"]), items_of(raw))
            if len(cand) < len(raw):
                out, depth = cand, prev["depth"] + 1
        self.prev[key] = {"turn": turn, "text": raw, "depth": depth}
        return out


def resolve_delta(s: Session, turn: int, depth: list | None = None) -> str:
    """Reader view: replay the chain of deltas from the nearest literal output."""
    t = s.result(turn)["text"]
    m = re.match(r"\[ctxprune: delta vs your run at turn (\d+):[^\n]*\]\n", t)
    if not m:
        return t
    if depth is not None:
        depth.append(turn)
    return "".join(apply_delta(items_of(resolve_delta(s, int(m.group(1)), depth)), t[m.end():]))


def pytest_output(failing: dict[int, int]) -> str:
    """Deterministic pytest -q style output (no timing line) for tests {index: observed value}."""
    out = ["============================= test session starts ==============================", "collected 40 items", "",
           "tests/test_orders.py " + "".join("F" if i in failing else "." for i in range(40)), "",
           "=================================== FAILURES ==================================="]
    for i, got in sorted(failing.items()):
        out += [f"_______________________________ test_total_{i} _______________________________", "",
                f"    def test_total_{i}():", f"        cart = make_cart({i})", f">       assert cart.total() == {i * 10}",
                f"E       assert {got} == {i * 10}", f"E        +  where {got} = <bound method Cart.total of <Cart items={i}>>()", "",
                f"tests/test_orders.py:{30 + i}: AssertionError"]
    out += ["=========================== short test summary info ============================"]
    out += [f"FAILED tests/test_orders.py::test_total_{i} - assert {got} == {i * 10}" for i, got in sorted(failing.items())]
    out += [f"{len(failing)} failed, {40 - len(failing)} passed" if failing else "40 passed", ""]
    return "\n".join(out)


def sim_rerun_delta() -> dict:
    s = Session(RerunDelta())
    fails = {i: i * 10 + 3 for i in range(12)}
    runs = [dict(fails)]
    runs.append({i: v for i, v in fails.items() if i >= 3})                      # fixed three
    runs.append({i: (v + 1 if i == 5 else v) for i, v in runs[-1].items()})      # one assertion value moved
    runs.append({i: v for i, v in runs[-1].items() if i >= 7})                   # fixed more
    runs.append(dict(runs[-1]))                                                  # nothing changed
    runs.append({i: v for i, v in runs[-1].items() if i >= 9})
    runs.append({})                                                              # all green: header lines only in common
    runs.append("ImportError: cannot import name 'make_cart' from 'tests.helpers'\n" * 3)  # unrelated output
    modes, chain_lens = [], []
    for r in runs:
        raw = r if isinstance(r, str) else pytest_output(r)
        seen = s.step("Bash", {"command": "pytest -q", "cwd": ""}, raw)
        depth: list = []
        assert resolve_delta(s, s.turn, depth) == raw, "reader view: previous output + delta rebuilds the run byte for byte"
        chain_lens.append(len(depth))
        modes.append("delta" if is_stub(seen) else "full")
    assert modes[0] == "full" and modes[1] == "delta", modes
    assert "full" in modes[2:], f"keyframe or low-similarity run must be shown in full: {modes}"
    assert max(chain_lens) <= MAX_CHAIN, "a reader never replays more than MAX_CHAIN deltas"
    assert modes[-1] == "full", "unrelated output shares nothing: the gate must prefer the full text"
    # unchanged output still gives a delta, and it is tiny
    ident = pytest_output(runs[3])
    assert isinstance(runs[3], dict)
    s2 = Session(RerunDelta())
    s2.step("Bash", {"command": "pytest -q"}, ident)
    seen = s2.step("Bash", {"command": "pytest -q"}, ident)
    assert is_stub(seen) and len(seen) < 160 and resolve_delta(s2, s2.turn) == ident
    # other command or cwd is a different key; compaction forgets
    seen = s2.step("Bash", {"command": "pytest -q tests/test_orders.py"}, ident)
    assert seen == ident
    seen = s2.step("Bash", {"command": "pytest -q", "cwd": "/elsewhere"}, ident)
    assert seen == ident
    s2.compact()
    assert s2.step("Bash", {"command": "pytest -q"}, ident) == ident
    # exactness on odd text: random pairs, CRLF, no final newline, lines that look like delta ops, empty
    rng = random.Random(62)
    pool = ["keep 3", "add 2", "drop 1", "", "x\r", "é ü", "a b c", "\t", "FAILED tests/x.py::t - boom", "0", "last"]

    def rand_text():
        ls = [rng.choice(pool) + ("" if rng.random() < .9 else str(rng.randrange(9))) for _ in range(rng.randrange(0, 12))]
        return "\n".join(ls) + ("\n" if ls and rng.random() < .7 else "")

    for _ in range(500):
        a = rand_text()
        b = rng.choice([rand_text(), a + rand_text(), a[: rng.randrange(len(a) + 1)], a.replace("\n", "\r\n", 1)])
        assert "".join(apply_delta(items_of(a), make_delta(items_of(a), items_of(b)))) == b, (a, b)
    # fail open
    h = Session(RerunDelta())
    h.step("Bash", {"command": "x"}, ident)
    h.hook.prev["", "x"]["text"] = None
    assert h.step("Bash", {"command": "x"}, ident + "more\n") == ident + "more\n"
    return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "modes": ",".join(modes)}


case(
    62,
    claim="A re-run whose output changed is shown as keep/drop/add line counts plus only the new lines, vs the previous run of the same command; replaying the chain from the last literal output rebuilds every run exactly, chains are capped, and the full text is shown when the delta is not shorter.",
    sim=sim_rerun_delta,
)

# ---------------------------------------------------------------------------------------------------------------
# #63 Overlapping reads and greps -> trim lines already shown. Read ranges: per-path ledger {line: turn it was
# literal}, reset when the file's content changes. Grep hits: keyed (path, line, text); an already shown hit becomes
# a path:line key, new hits stay literal in place.

_HIT = re.compile(r"^([^:\s]+):(\d+):(.*)$", re.S)


class HitLedger:
    """Grep-style `path:line:text` hits. partial=True: replace runs of already-shown hits by their keys (#63).
    partial=False: only when every hit has been shown, then keys only (#1264)."""

    def __init__(self, partial: bool):
        self.partial, self.seen = partial, {}

    def clear(self):
        self.seen = {}

    def _known(self, line: str):
        m = _HIT.match(line)
        if not m:
            return None
        texts = self.seen.get((m.group(1), int(m.group(2))), {})
        # unambiguous only: if two different texts were ever shown for this path:line the key does not identify a line
        return f"{m.group(1)}:{m.group(2)}" if len(texts) == 1 and m.group(3) in texts else None

    def _record(self, line: str, turn: int):
        m = _HIT.match(line)
        if m:
            self.seen.setdefault((m.group(1), int(m.group(2))), {})[m.group(3)] = turn

    def dedup(self, turn: int, raw: str) -> str:
        if not raw or MARK in raw or raw.startswith("Error"):
            return raw
        tail = "\n" if raw.endswith("\n") else ""
        lines = (raw[:-1] if tail else raw).split("\n")
        keys = [self._known(l) for l in lines]
        if not self.partial:
            if all(k is not None for k in keys):
                out = f"{MARK} subset of earlier hits, keys only: " + " ".join(keys) + "]"
                if len(out) + len(tail) < len(raw):
                    return out + tail
            for l in lines:
                self._record(l, turn)
            return raw
        out, i = [], 0
        while i < len(lines):
            j = i
            while j < len(lines) and keys[j] is not None:
                j += 1
            if j > i:
                marker = f"{MARK} already shown: " + " ".join(keys[i:j]) + "]"
                if len(marker) < sum(len(l) + 1 for l in lines[i:j]) - 1:
                    out.append(marker)
                    i = j
                    continue
                j = i + 1  # not worth a marker: keep this one line literal
            out.append(lines[i])
            self._record(lines[i], turn)
            i = max(j, i + 1)
        return "\n".join(out) + tail


def expand_hits(s: Session, text: str) -> str:
    """Reader view for grep stubs: every key must name exactly one literal hit line still in context."""
    if not text:
        return text
    tail = "\n" if text.endswith("\n") else ""
    out = []
    for line in (text[:-1] if tail else text).split("\n"):
        m = re.fullmatch(r"\[ctxprune: (?:already shown|subset of earlier hits, keys only): (.*)\]", line)
        if not m:
            out.append(line)
            continue
        for key in m.group(1).split(" "):
            found = {l for e in s.ctx if e["kind"] == "result" and e["tool"] == "Grep" for l in e["text"].split("\n") if l.startswith(key + ":")}
            assert len(found) == 1, f"key {key} must identify exactly one hit line in context, found {found}"
            out += found
    return "\n".join(out) + tail


def fake_grep(ws: Workspace, pattern: str, sub: str = "src") -> str:
    rx, out = re.compile(pattern), []
    for rel in ws.files(sub):
        for n, l in enumerate(lines_of(ws.get(rel)), 1):
            if rx.search(l):
                out.append(f"{rel}:{n}:{l}")
    return "\n".join(out) + ("\n" if out else "")


class OverlapTrim:
    def __init__(self, ws: Workspace):
        self.ws, self.ver, self.shown, self.hits = ws, {}, {}, HitLedger(partial=True)

    def on_compact(self):
        self.ver, self.shown = {}, {}
        self.hits.clear()

    @fail_open
    def post(self, turn, tool, args, raw):
        if MARK in raw:
            return raw
        if tool == "Grep":
            return self.hits.dedup(turn, raw)
        if tool != "Read" or not raw or raw.startswith("Error"):
            return raw
        path = args["path"]
        ver = sha(self.ws.get(path))
        if self.ver.get(path) != ver:  # the file is not what the ledger described: forget it
            self.ver[path], self.shown[path] = ver, {}
        shown, groups = self.shown[path], []
        for l in raw.split("\n"):
            m = _NUM.match(l)
            if not m:
                return raw
            n, tag = int(m.group(1)), shown.get(int(m.group(1)))
            if groups and groups[-1][0] == tag and groups[-1][1][-1][0] == n - 1:
                groups[-1][1].append((n, l))
            else:
                groups.append([tag, [(n, l)]])
        out = []
        for tag, g in groups:
            lit = "\n".join(l for _, l in g)
            if tag is not None:
                marker = f"{MARK} lines {g[0][0]}-{g[-1][0]} of {path} already shown at turn {tag}]"
                if len(marker) < len(lit):
                    out.append(marker)
                    continue
            for n, _ in g:
                shown[n] = turn
            out.append(lit)
        return "\n".join(out)


def literal_numbered(text: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(2) for l in text.split("\n") for m in [_NUM.match(l)] if m}


def expand_read(s: Session, text: str, path: str) -> dict[int, str]:
    """Reader view for Read stubs: numbered lines, with each pointer filled from the literal lines at its turn."""
    out: dict[int, str] = {}
    for l in text.split("\n"):
        m = re.fullmatch(r"\[ctxprune: lines (\d+)-(\d+) of (\S+) already shown at turn (\d+)\]", l)
        if m:
            assert m.group(3) == path
            src = literal_numbered(s.result(int(m.group(4)))["text"])
            for n in range(int(m.group(1)), int(m.group(2)) + 1):
                out[n] = src[n]  # KeyError = dangling pointer
        else:
            mm = _NUM.match(l)
            out[int(mm.group(1))] = mm.group(2)
    return out


def sim_overlap() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        tools, hook = Tools(ws), OverlapTrim(ws)
        s = Session(hook)
        ws.put("src/a.py", py_module("alpha", 40))
        ws.put("src/b.py", py_module("beta", 40))

        def read(path, offset=1, limit=2000):
            raw = tools.read(path, offset, limit)
            seen = s.step("Read", {"path": path, "offset": offset, "limit": limit}, raw)
            assert expand_read(s, seen, path) == parse_numbered(raw), "reader view rebuilds every returned line"
            return raw, seen

        raw, seen = read("src/a.py", 1, 100)
        assert seen == raw
        t1 = s.turn
        raw, seen = read("src/a.py", 80, 101)
        assert f"lines 80-100 of src/a.py already shown at turn {t1}" in seen and len(seen) < len(raw) * 0.85, "partial overlap: only the overlap is trimmed"
        assert "   101\t" in seen and "   180\t" in seen, "the new lines are literal"
        t2 = s.turn
        raw, seen = read("src/a.py", 1, 200)
        assert f"lines 1-100 of src/a.py already shown at turn {t1}" in seen and f"lines 101-180 of src/a.py already shown at turn {t2}" in seen
        assert "   181\t" in seen and "   100\t" not in seen
        raw, seen = read("src/b.py", 1, 50)
        assert seen == raw, "another file: nothing to trim"
        raw, seen = read("src/a.py", 90, 11)
        assert seen.startswith(MARK) and "\n" not in seen, "fully covered range: one pointer"
        raw, seen = read("src/a.py", 90, 21)
        assert seen.count("\n") == 1 and all(l.startswith(MARK) for l in seen.split("\n")), "covered by two earlier turns: two pointers"
        # file edited by something else: the old ledger would describe the wrong lines
        lines = lines_of(ws.get("src/a.py"))
        lines[94] = "    total = a * 999 + b  # changed by a formatter"
        ws.put("src/a.py", "\n".join(lines) + "\n")
        raw, seen = read("src/a.py", 80, 41)
        assert seen == raw and "formatter" in raw, "content changed: everything literal, ledger reset"
        t3 = s.turn
        raw, seen = read("src/a.py", 60, 41)
        assert f"lines 80-100 of src/a.py already shown at turn {t3}" in seen and "   60\t" in seen and "   79\t" in seen, "ledger restarted from the new version"
        # the model's own Edit changes the file too
        assert "updated" in tools.edit("src/a.py", "alpha_7", "alpha_seven", replace_all=True)
        raw, seen = read("src/a.py", 60, 41)
        assert seen == raw, "after an edit nothing is trimmed"
        # grep: partial overlap of hits, in place; keys for the already shown ones
        g1 = fake_grep(ws, r"def alpha_1\d")
        out1 = s.step("Grep", {"pattern": "def alpha_1\\d"}, g1)
        assert out1 == g1
        g2 = fake_grep(ws, r"def alpha_\d")
        out2 = s.step("Grep", {"pattern": "def alpha_\\d"}, g2)
        assert out2 != g2 and expand_hits(s, out2) == g2, "reader view: keys resolve to the earlier literal hit lines"
        assert any(l.startswith("src/a.py:") and "alpha_2" in l for l in out2.split("\n")), "new hits stay literal"
        # a shift of line numbers (insert at the top) makes every key new: nothing may be trimmed
        ws.put("src/a.py", "# new header line\n" + ws.get("src/a.py"))
        g3 = fake_grep(ws, r"def alpha_\d")
        out3 = s.step("Grep", {"pattern": "def alpha_\\d"}, g3)
        assert out3 == g3, "same text on other line numbers is not 'already shown'"
        # unparsable lines and hostile text pass through
        odd = "Binary file src/x.bin matches\nsrc/a.py:3:[ctxprune: already shown: src/a.py:3]\n"
        assert s.step("Grep", {"pattern": "x"}, odd) == odd
        s.compact()
        out4 = s.step("Grep", {"pattern": "def alpha_\\d"}, g3)
        assert out4 == g3, "after compaction the hits are not in context any more"
        raw, seen = read("src/a.py", 1, 30)
        assert seen == raw
        hook.shown = None
        raw, seen = read("src/a.py", 1, 30)
        assert seen == raw, "fail open"
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    63,
    claim="Lines of a Read range already shown from the same unchanged file become 'lines a-b already shown at turn N' pointers and Grep hits already shown become path:line keys, new lines stay literal in place; a changed file, a different file, shifted line numbers, an Edit or compaction show everything.",
    sim=sim_overlap,
)

# ---------------------------------------------------------------------------------------------------------------
# #1264 Narrowing searches: when every hit of a refined grep is already in context, emit only path:line keys.


class KeysOnly:
    def __init__(self, led: HitLedger):
        self.led = led

    def on_compact(self):
        self.led.clear()

    @fail_open
    def post(self, turn, tool, args, raw):
        return self.led.dedup(turn, raw) if tool == "Grep" else raw


def sim_narrowing() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        led = HitLedger(partial=False)
        s = Session(KeysOnly(led))
        ws.put("src/a.py", py_module("alpha", 30))
        ws.put("src/b.py", py_module("beta", 30))

        def grep(pat):
            raw = fake_grep(ws, pat)
            seen = s.step("Grep", {"pattern": pat}, raw)
            assert expand_hits(s, seen) == raw, "reader view: keys-only form rebuilds the exact hit lines, in order"
            return raw, seen

        raw, seen = grep(r"return total")
        assert seen == raw
        raw, seen = grep(r"return total - 1\d")
        assert is_stub(seen) and "\n" not in seen.strip() and len(seen) < len(raw) * 0.75, "subset of earlier hits: keys only"
        raw_b, seen_b = grep(r"return total$")
        assert seen_b == raw_b or is_stub(seen_b)
        raw, seen = grep(r"total")
        assert not is_stub(seen) or "already shown" not in seen, "superset: new hits exist, so this idea does not apply"
        assert seen == raw, "keys-only is all or nothing"
        raw, seen = grep(r"return total - 2\d")
        assert is_stub(seen), "subset of the union of earlier hits"
        # file edited between the searches: same path:line, different text -> the key would be ambiguous
        ws.put("src/a.py", ws.get("src/a.py").replace("        return total - 12\n", "        return total - 12  # tuned\n"))
        raw, seen = grep(r"return total - 12")
        assert seen == raw, "changed text under a known key: shown in full"
        raw, seen = grep(r"return total - 12")
        assert seen == raw, "ambiguous key (two texts ever shown for it): never abbreviated"
        raw, seen = grep(r"return total - 1[3-5]")
        assert is_stub(seen), "unrelated keys still resolve"
        # context lines / separators and paths with spaces are not path:line:text: leave alone
        weird = "src/a.py-3-ctx\n--\nsrc/a.py:3:x\n"
        assert s.step("Grep", {"pattern": "z"}, weird) == weird
        s.compact()
        raw, seen = grep(r"return total - 1[3-5]")
        assert seen == raw, "after compaction the earlier hits are gone"
        led.seen = None
        raw, seen = grep(r"return total - 1[3-5]")
        assert seen == raw, "fail open"
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    1264,
    claim="A refined search whose every hit line was already shown (same path, line and text, unambiguous) is replaced by the ordered list of path:line keys; a new hit, changed text under a known key, line-number shifts or compaction show the full output.",
    sim=sim_narrowing,
)

# ---------------------------------------------------------------------------------------------------------------
# #138 Elide output lines that echo the model's own Edit/Write text. Index the lines of every successful Edit
# new_string and Write content; in later Bash output a run of lines that equals a run of those lines (behind one
# constant prefix: diff '+', pytest's four spaces or '>   ') becomes `<prefix>⟦your Edit (turn N) lines a-b⟧`.

PREFIXES = ("", "+", " ", "    ", ">   ")
POINTER = re.compile(r"^(\+| |    |>   |)⟦your (Edit|Write) \(turn (\d+)\) lines? (\d+)(?:-(\d+))?⟧$")
MIN_RUN_GAIN = 25  # characters a pointer line must save, or the run stays literal


def best_run_at(items: list, i: int, idx: dict, sources: dict):
    """Longest run items[i:j] equal to sources[name][k:k+j-i], or None. A None item never matches."""
    if items[i] is None:
        return None
    best = None
    for name, k in idx.get(items[i], ()):
        src, j = sources[name], 1
        while i + j < len(items) and k + j < len(src) and items[i + j] is not None and items[i + j] == src[k + j]:
            j += 1
        if best is None or j > best[1] - i:
            best = (i, i + j, name, k)
    return best


def index_sources(sources: dict) -> dict:
    idx: dict = {}
    for name, lines in sources.items():
        for k, l in enumerate(lines):
            idx.setdefault(l, []).append((name, k))
    return idx


class EchoElide:
    def __init__(self):
        self.src: dict | None = {}  # (kind, turn) -> lines
        self.stats = {}

    def on_compact(self):
        self.src = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool == "Edit" and raw.startswith("The file"):
            self.src[("Edit", turn)] = lines_of(args["new_string"])
            return raw
        if tool == "Write" and raw.startswith("File created"):
            self.src[("Write", turn)] = lines_of(args["content"])
            return raw
        if tool != "Bash" or "⟦" in raw or not self.src:
            return raw
        idx, lines = index_sources(self.src), raw.split("\n")
        per_prefix = {p: [l[len(p):] if l.startswith(p) else None for l in lines] for p in PREFIXES}
        out, i = [], 0
        while i < len(lines):
            best = None  # (gain, prefix, run)
            for p, items in per_prefix.items():
                r = best_run_at(items, i, idx, self.src)
                if r:
                    kind, t = r[2]
                    a, b = r[3] + 1, r[3] + (r[1] - r[0])
                    ptr = f"{p}⟦your {kind} (turn {t}) line{'s' if b > a else ''} {a}{f'-{b}' if b > a else ''}⟧"
                    gain = sum(len(l) + 1 for l in lines[r[0]:r[1]]) - (len(ptr) + 1)
                    if gain >= MIN_RUN_GAIN and (best is None or gain > best[0]):
                        best = (gain, ptr, r)
            if best:
                out.append(best[1])
                i = best[2][1]
            else:
                out.append(lines[i])
                i += 1
        res = "\n".join(out)
        st = self.stats.setdefault(tool, [0, 0])
        st[0] += len(raw) - len(res)
        st[1] += len(raw)
        return res if len(res) < len(raw) else raw


def expand_echo(s: Session, text: str) -> str:
    """Reader view: each pointer line is expanded from the Edit new_string / Write content in the model's own tool_use."""
    out = []
    for l in text.split("\n"):
        m = POINTER.match(l)
        if not m:
            out.append(l)
            continue
        p, kind, turn, a, b = m.groups()
        ev = s.use(int(turn))
        assert ev["tool"] == kind, "pointer must name the tool call it came from"
        src = lines_of(ev["args"]["new_string" if kind == "Edit" else "content"])
        out += [p + x for x in src[int(a) - 1:int(b or a)]]
    return "\n".join(out)


PRICING = '''"""Pricing rules."""
from decimal import Decimal


def apply_discount(subtotal, rate):
    return subtotal * (1 - rate)


def shipping(weight_kg):
    if weight_kg > 20:
        return 25
    return 5


def total(items, rate=0):
    subtotal = sum(i.price * i.qty for i in items)
    return apply_discount(subtotal, rate) + shipping(sum(i.weight for i in items))
'''

NEW_DISCOUNT = '''def apply_discount(subtotal, rate):
    if not 0 <= rate <= 1:
        raise ValueError(f"discount rate out of range: {rate!r}")
    discounted = Decimal(str(subtotal)) * (Decimal(1) - Decimal(str(rate)))
    return discounted.quantize(Decimal("0.01"))
'''

HELPERS = '''def bulk_discount(items, tiers):
    """Return the best tier rate for the basket, or 0 when no tier applies."""
    quantity = sum(i.qty for i in items)
    best = 0
    for threshold, rate in sorted(tiers.items()):
        if quantity >= threshold:
            best = rate
    return best


def loyalty_discount(customer, base_rate):
    if customer is None:
        return base_rate
    years = (date.today() - customer.joined).days // 365
    return min(base_rate + 0.01 * years, 0.25)
'''

TESTS = '''from decimal import Decimal

from pricing import apply_discount, shipping


def test_discount_rounds_to_cents():
    got = apply_discount(Decimal("19.99"), Decimal("0.15"))
    expected = Decimal("16.99")
    assert got == expected, f"unexpected discounted price {got}"


def test_shipping_heavy_parcel():
    assert shipping(21) == 25
'''


def git_diff(before: dict, after: dict, idx=("1a2b3c4", "5d6e7f8")) -> str:
    out = []
    for path in sorted(after):
        if before.get(path, "") == after[path]:
            continue
        body = list(difflib.unified_diff(lines_of(before.get(path, "")), lines_of(after[path]), f"a/{path}", f"b/{path}", n=3, lineterm=""))
        out += [f"diff --git a/{path} b/{path}", f"index {idx[0]}..{idx[1]} 100644"] + body
    return "\n".join(out) + "\n" if out else ""


def pytest_fail(name: str, func_lines: list[str], fail_at: int, got: str, want: str, line_no: int) -> str:
    out = ["=================================== FAILURES ===================================",
           f"_____________________ {name} _____________________", ""]
    out += [("> " + "  " if k == fail_at else "    ") + l for k, l in enumerate(func_lines)]
    out += [f"E       AssertionError: unexpected discounted price {got}", f"E       assert Decimal('{got}') == Decimal('{want}')", "",
            f"tests/test_pricing.py:{line_no}: AssertionError",
            "=========================== short test summary info ============================",
            f"FAILED tests/test_pricing.py::{name} - AssertionError: unexpected discounted price {got}", "1 failed, 1 passed", ""]
    return "\n".join(out)


def sim_echo_elision() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws, hook = Workspace(d), EchoElide()
        tools, s = Tools(ws), Session(hook)
        ws.put("src/pricing.py", PRICING)
        before = {"src/pricing.py": PRICING}

        def bash(cmd, raw, expect_elided=None):
            seen = s.step("Bash", {"command": cmd}, raw)
            assert expand_echo(s, seen) == raw, "reader view: pointers expand to exactly the raw output"
            if expect_elided is not None:
                assert (seen != raw) == expect_elided, (cmd, seen)
            return seen

        raw = tools.read("src/pricing.py")
        assert s.step("Read", {"path": "src/pricing.py"}, raw) == raw
        old = "def apply_discount(subtotal, rate):\n    return subtotal * (1 - rate)\n"
        e1 = s.turn + 1
        raw = tools.edit("src/pricing.py", old, NEW_DISCOUNT)
        s.step("Edit", {"path": "src/pricing.py", "old_string": old, "new_string": NEW_DISCOUNT}, raw)
        e2 = s.turn + 1
        old2, new2 = "def shipping(weight_kg):\n", HELPERS + "\n\ndef shipping(weight_kg):\n"
        raw = tools.edit("src/pricing.py", old2, new2)
        s.step("Edit", {"path": "src/pricing.py", "old_string": old2, "new_string": new2}, raw)
        w1 = s.turn + 1
        raw = tools.write("tests/test_pricing.py", TESTS)
        s.step("Write", {"path": "tests/test_pricing.py", "content": TESTS}, raw)
        diff = git_diff(before, {"src/pricing.py": ws.get("src/pricing.py")})
        seen = bash("git diff", diff, True)
        assert f"+⟦your Edit (turn {e1}) lines 2-5⟧" in seen, seen
        assert re.search(rf"\+⟦your Edit \(turn {e2}\) lines 1-\d+⟧", seen), seen
        assert len(seen) < len(diff) * 0.6
        diff_saved = tokens(diff) - tokens(seen)
        flines = lines_of(TESTS)[5:9]  # test_discount_rounds_to_cents: def, got, expected, assert (the failing line)
        trace = pytest_fail("test_discount_rounds_to_cents", flines, 3, "16.98", "16.99", 10)
        seen = bash("pytest -x", trace, True)
        assert f"    ⟦your Write (turn {w1}) lines 6-8⟧" in seen, seen
        assert "E       AssertionError" in seen, "output that is not an echo stays literal"
        # Read output is never touched: its lines are numbered and its position is the point of the call
        raw = tools.read("src/pricing.py")
        assert s.step("Read", {"path": "src/pricing.py"}, raw) == raw
        # a short echoed line and a line that differs by one trailing space stay literal
        short = "    assert shipping(21) == 25\n"
        bash("grep -n shipping tests/test_pricing.py", short, False)
        near = NEW_DISCOUNT.replace("quantize(Decimal(\"0.01\"))", "quantize(Decimal(\"0.01\")) ")
        seen = bash("cat src/pricing_v2.py", near, True)
        assert seen.endswith('quantize(Decimal("0.01")) \n'), "the line that differs by one trailing space stays literal, its neighbours are elided"
        # a failed Edit is not indexed: its text was never applied, so the pointer would mislead
        raw = tools.edit("src/pricing.py", "    return", "    return int(0) +  # not unique")  # 'return' occurs many times
        assert raw.startswith("Error")
        s.step("Edit", {"path": "src/pricing.py", "old_string": "    return", "new_string": "    return int(0) +  # not unique"}, raw)
        bash("echo", "    return int(0) +  # not unique\n    return int(0) +  # not unique\n", False)
        # hostile output that already contains the pointer glyph is passed through untouched
        hostile = "see ⟦your Edit (turn 1) lines 1-2⟧ in the docs\n" + diff
        assert s.step("Bash", {"command": "cat NOTES"}, hostile) == hostile
        stats = list(hook.stats["Bash"])
        # compaction removes the Edit inputs from context, so the index must go too
        s.compact()
        seen = bash("git diff", diff, False)
        # fail open
        s2 = Session(EchoElide())
        s2.step("Write", {"path": "x", "content": TESTS}, "File created successfully at: x")
        s2.hook.src = None
        assert s2.step("Bash", {"command": "cat tests/test_pricing.py"}, TESTS) == TESTS
        assert diff_saved >= 100 and stats[0] / stats[1] > 0.2, (diff_saved, stats)
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "git_diff_tokens_saved": diff_saved,
                "bash_chars_elided": stats[0], "bash_chars_total": stats[1]}


case(
    138,
    claim="In Bash output, a run of lines that exactly equals consecutive lines of the model's own successful Edit new_string or Write content (behind one constant prefix such as diff '+' or pytest indentation) becomes a pointer to that tool call; Read output, short or near-miss lines, failed Edits, hostile text and compacted context are left literal.",
    sim=sim_echo_elision,
)

# ---------------------------------------------------------------------------------------------------------------
# #139 Hunk-hash dedup across git diff, git diff --cached, git show, gh pr diff. A hunk is identified by
# (file, @@ header line, body); a repeat keeps its @@ line and replaces the body by a pointer.

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE = re.compile(r"^diff --git a/(.*) b/(.*)$")


def parse_hunks(lines: list[str]) -> list[tuple[str, int, int]]:
    """(file, index of the @@ line, end index) per well-formed hunk, using the header's line counts."""
    out, file, i = [], None, 0
    while i < len(lines):
        m = _DIFF_FILE.match(lines[i])
        if m:
            file = m.group(2)
        h = _HUNK.match(lines[i])
        if h and file:
            old = int(h.group(2)) if h.group(2) is not None else 1
            new = int(h.group(4)) if h.group(4) is not None else 1
            j = i + 1
            while (old > 0 or new > 0) and j < len(lines):
                c = lines[j][:1]
                if c == " " or lines[j] == "":
                    old, new = old - 1, new - 1
                elif c == "-":
                    old -= 1
                elif c == "+":
                    new -= 1
                elif c != "\\":
                    break
                j += 1
            while j < len(lines) and lines[j].startswith("\\"):
                j += 1
            if old == 0 and new == 0:
                out.append((file, i, j))
                i = j
                continue
        i += 1
    return out


class HunkDedup:
    def __init__(self):
        self.seen: dict | None = {}

    def on_compact(self):
        self.seen = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "Bash" or MARK in raw or "@@ " not in raw:
            return raw
        lines = raw.split("\n")
        out, pos = [], 0
        for file, a, b in parse_hunks(lines):
            body = lines[a + 1:b]
            key = (file, lines[a], "\n".join(body))
            out += lines[pos:a + 1]
            pos = b
            prev = self.seen.get(key)
            marker = f"{MARK} hunk body ({len(body)} lines) identical to this hunk of {file} at turn {prev}]"
            if prev is not None and len(marker) < sum(len(l) + 1 for l in body):
                out.append(marker)
            else:
                out += body
                self.seen.setdefault(key, turn)
        out += lines[pos:]
        return "\n".join(out)


def expand_hunks(s: Session, text: str) -> str:
    """Reader view: a pointer names a turn; find the same file section and @@ line there and copy the next K lines."""
    out, file, lines = [], None, text.split("\n")
    for k, l in enumerate(lines):
        m = _DIFF_FILE.match(l)
        if m:
            file = m.group(2)
        mm = re.fullmatch(r"\[ctxprune: hunk body \((\d+) lines\) identical to this hunk of (.*) at turn (\d+)\]", l)
        if not mm:
            out.append(l)
            continue
        n, f, t = int(mm.group(1)), mm.group(2), int(mm.group(3))
        assert f == file and _HUNK.match(lines[k - 1]), "pointer sits right under its own @@ line, in the same file"
        src = s.result(t)["text"].split("\n")
        start = next(i for i, x in enumerate(src) if x == f"diff --git a/{f} b/{f}")
        end = next((i for i in range(start + 1, len(src)) if src[i].startswith("diff --git ")), len(src))
        at = next(i for i in range(start, end) if src[i] == lines[k - 1])
        assert not src[at + 1].startswith(MARK), "target hunk must be literal"
        out += src[at + 1:at + 1 + n]
    return "\n".join(out)


def sim_hunk_dedup() -> dict:
    api = lines_of(py_module("api", 12))
    models = lines_of(py_module("model", 12))
    cfg = "name: svc\nreplicas: 2\n"
    before = {"api/handlers.py": "\n".join(api) + "\n", "api/models.py": "\n".join(models) + "\n",
              "config/a.yaml": cfg, "config/b.yaml": cfg, "config/c.yaml": cfg}
    after = dict(before)
    h = list(api)
    h[10:12] = ["    total = a * 3 + b + OFFSET  # edited", "    total = max(total, MIN_TOTAL)  # clamp to the configured floor",
                "    if total > LIMIT:  # new cap from the pricing policy", "        total = LIMIT", "    log.debug('step 3 total=%s', total)", "    metrics.incr('step3')"]
    h[64:66] = ["    if total > 999:  # edited", "        return total - 99  # discount applies above the cap", "    total = round(total, 2)",
                "    log.debug('step 8 total=%s', total)", "    metrics.incr('step8')", "    audit(total)"]
    after["api/handlers.py"] = "\n".join(h) + "\n"
    m = list(models)
    m[30:30] = ["    assert a >= 0, 'negative input'", "    assert b >= 0, 'negative offset'", "    log.debug('model_3 a=%s b=%s', a, b)",
                "    metrics.incr('model_3')", "    cache_key = (a, b)", "    if cache_key in CACHE:", "        return CACHE[cache_key]"]
    after["api/models.py"] = "\n".join(m) + "\n"
    lic = "# SPDX-License-Identifier: MIT\n# Copyright the project authors\n# See LICENSE for details\n"
    after["config/a.yaml"], after["config/b.yaml"] = lic + cfg, lic + cfg   # identical hunk in two files
    after["config/c.yaml"] = "name: svc\nreplicas: 3\n"                    # a hunk too small to be worth a pointer
    full = git_diff(before, after)
    s = Session(HunkDedup())

    def bash(cmd, raw):
        seen = s.step("Bash", {"command": cmd}, raw)
        assert expand_hunks(s, seen) == raw, "reader view: pointers expand to exactly the raw output"
        return seen

    n_hunks = len(parse_hunks(full.split("\n")))
    n_big = n_hunks - 1
    assert n_hunks == 6
    seen = bash("git diff", full)
    t1 = s.turn
    assert seen == full, "first sight: literal"
    assert s.result(t1)["text"].count("+# SPDX-License-Identifier: MIT") == 2, "same hunk text in a.yaml and b.yaml: different files, both literal"
    assert bash("git diff --cached", "") == "", "empty output is untouched"
    seen = bash("git diff --cached", full)
    assert seen.count(MARK) == n_big and len(seen) < len(full) * 0.85, "same hunks again: every body that pays is replaced"
    assert "+replicas: 3" in seen, "the tiny hunk stays literal: its pointer would be longer"
    commit = "commit 9f8e7d6c5b4a\nAuthor: A Dev <dev@example.com>\nDate:   Tue Nov 3 10:00:00 2026 +0000\n\n    Add offset and license headers\n\n" + full
    seen = bash("git show", commit)
    assert seen.startswith("commit 9f8e7d6c5b4a") and seen.count(MARK) == n_big, "commit header stays literal"
    pr = full.replace("index 1a2b3c4..5d6e7f8", "index 1a2b3c4d..5d6e7f8a")
    seen = bash("gh pr diff 12", pr)
    assert "index 1a2b3c4d..5d6e7f8a" in seen and seen.count(MARK) == n_big, "file header lines are never touched"
    # a hunk that grew by a line is a different hunk (what #908 handles) and the hunk after it has a new header
    h2 = list(h)
    h2.insert(11, "    total = max(total, 0)")  # grows the first hunk by one line and shifts every later header
    after2 = dict(after)
    after2["api/handlers.py"] = "\n".join(h2) + "\n"
    full2 = git_diff(before, after2)
    seen = bash("gh pr diff 12", full2)
    assert "max(total, 0)" in seen and seen.count(MARK) == n_big - 2, "grown hunk and the shifted one stay literal, the rest is stubbed"
    # truncated output: a half hunk is never touched
    cut = full[: full.index("diff --git a/config/a.yaml") - 30]
    seen = bash("git diff | head -c 3000", cut)
    assert seen.count(MARK) == 2 and seen.endswith(cut[-30:]), "complete hunks are stubbed, the cut one is literal"
    # hostile text and compaction
    hostile = full + "+[ctxprune: hunk body (3 lines) identical to this hunk of x at turn 1]\n"
    assert s.step("Bash", {"command": "cat PATCH"}, hostile) == hostile
    s.compact()
    assert bash("git diff", full) == full, "after compaction every hunk is literal again"
    assert bash("git show", full).count(MARK) == n_big
    s.hook.seen = None
    assert bash("git diff", full) == full, "fail open"
    assert s.saved > 0.15 * s.raw_tok, (s.saved, s.raw_tok)
    return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "hunks": n_hunks}


case(
    139,
    claim="A diff hunk already shown literally (same file, same @@ header, same body) is replaced under its @@ line by a pointer to that turn, across git diff, --cached, show and gh pr diff; the same text in another file, a grown hunk, a new hunk, tiny hunks, truncated or hostile output and compaction stay literal.",
    sim=sim_hunk_dedup,
)

# ---------------------------------------------------------------------------------------------------------------
# #140 Content-hash stubs across tools. The key is the hash of the file lines a result carries, whatever tool made it
# (Read numbered lines, Bash cat/sed -n/head, MCP get_file_contents). The check runs before the lossy compressor
# so commands that compressor skips verbatim (cat, sed -n) are covered too.

VERBATIM_CMD = re.compile(r"^(cat|sed -n|head|tail) ")
MCP_FILE = "mcp__github__get_file_contents"


def content_lines(tool: str, args: dict, raw: str):
    """The file lines a tool result carries, or None when it is not file content."""
    if not raw or raw.startswith(("Error", "Exit code")):
        return None
    if tool == "Read":
        return list(parse_numbered(raw).values())
    if tool == "Bash" and VERBATIM_CMD.match(args["command"]):
        return lines_of(raw)
    if tool == MCP_FILE:
        return lines_of(raw)
    return None


def lossy(raw: str) -> str:
    """Stand-in for the lossy compressor: it would cut long outputs. cat and friends are exempt (verbatim skip)."""
    return raw[:200] + "\n[...truncated by lossy stage...]" if len(raw) > 200 else raw


class ContentStub:
    def __init__(self):
        self.seen: dict | None = {}

    def on_compact(self):
        self.seen = {}

    def post(self, turn, tool, args, raw):
        try:
            lines = content_lines(tool, args, raw) if MARK not in raw else None
            if lines is not None and len(lines) >= 4:
                h = sha("\n".join(lines))
                prev = self.seen.get(h)
                if prev is not None:
                    stub = f"{MARK} same {len(lines)} lines as the result of {prev[1]} at turn {prev[0]}]"
                    if len(stub) < len(raw):
                        return stub
                else:
                    self.seen[h] = (turn, f"{tool} {args.get('path') or args.get('command')}")
        except Exception:  # noqa: BLE001
            return raw
        if tool == "Bash" and not VERBATIM_CMD.match(args["command"]):
            return lossy(raw)
        return raw


def sim_content_stub() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws, hook = Workspace(d), ContentStub()
        tools, s = Tools(ws), Session(hook)
        src = py_module("svc", 25)
        ws.put("src/a.py", src)

        def call(tool, args, raw, expect):
            seen = s.step(tool, args, raw)
            want = content_lines(tool, args, raw)
            if is_stub(seen):
                m = re.fullmatch(r"\[ctxprune: same (\d+) lines as the result of (.*) at turn (\d+)\]", seen)
                ev = s.result(int(m.group(3)))
                assert not is_stub(ev["text"])
                got = content_lines(ev["tool"], ev["args"], ev["text"])  # reader view: normalise the earlier result
                assert got == want and len(got) == int(m.group(1)), "reader view: the earlier result carries the same lines"
            assert is_stub(seen) == expect, (tool, args, seen[:80])
            return seen

        call("Read", {"path": "src/a.py"}, tools.read("src/a.py"), False)
        t1 = s.turn
        call("Bash", {"command": "cat src/a.py"}, src, True)
        seen = call(MCP_FILE, {"path": "src/a.py"}, src, True)
        assert f"turn {t1}]" in seen, "stubs point at the one literal occurrence, never at another stub"
        # partial ranges across tools: sed -n and a ranged Read return the same lines
        sed = "".join(l + "\n" for l in lines_of(src)[9:20])
        call("Bash", {"command": "sed -n 10,20p src/a.py"}, sed, False)
        call("Read", {"path": "src/a.py", "offset": 10, "limit": 11}, tools.read("src/a.py", 10, 11), True)
        call("Bash", {"command": "head -n 30 src/a.py"}, "".join(l + "\n" for l in lines_of(src)[:30]), False)
        call("Read", {"path": "src/a.py", "offset": 1, "limit": 30}, tools.read("src/a.py", 1, 30), True)
        # same bytes under another path: the content is already in context
        ws.put("src/a_copy.py", src)
        call("Read", {"path": "src/a_copy.py"}, tools.read("src/a_copy.py"), True)
        # one changed character, CRLF, a changed file: shown in full
        call("Bash", {"command": "cat src/b.py"}, src.replace("svc_7", "svc_8"), False)
        call("Bash", {"command": "cat src/c.py"}, src.replace("\n", "\r\n"), False)
        ws.put("src/a.py", src.replace("svc_3", "svc_three"))
        call("Bash", {"command": "cat src/a.py"}, ws.get("src/a.py"), False)
        # reverted: identical to text still in context, so a stub is still exact
        ws.put("src/a.py", src)
        call("Bash", {"command": "cat src/a.py"}, ws.get("src/a.py"), True)
        # no final newline is not represented in a Read result: identity is the list of lines
        trimmed = src.rstrip("\n")
        call("Bash", {"command": "cat src/with_nl.py"}, trimmed + "\n", False)
        call("Bash", {"command": "cat src/nonl.py"}, trimmed, True)
        # small content, errors, and non-file commands are never stubbed
        call("Bash", {"command": "cat tiny.txt"}, "a\nb\n", False)
        tiny = s.step("Bash", {"command": "cat tiny.txt"}, "a\nb\n")
        assert tiny == "a\nb\n"
        call("Bash", {"command": "cat nope.py"}, "Exit code 1\ncat: nope.py: No such file or directory\n", False)
        long_out = "".join(f"line {i} of some build log\n" for i in range(40))
        seen1 = call("Bash", {"command": "make"}, long_out, False)
        assert "truncated by lossy stage" in seen1, "other commands still go to the lossy stage"
        assert "truncated" not in s.step("Bash", {"command": "cat Makefile"}, "".join(f"rule {i}: dep_{i}\n" for i in range(40))), "cat is verbatim-skipped by the lossy stage"
        s.compact()
        call("Bash", {"command": "cat src/a.py"}, src, False)
        call("Bash", {"command": "cat src/a.py"}, src, True)
        hook.seen = None
        call("Bash", {"command": "cat src/a.py"}, src, False)
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    140,
    claim="A file-content result (Read numbered lines, Bash cat/sed -n/head, MCP get_file_contents) whose list of lines equals the lines of an earlier literal result, from any tool or path, becomes a pointer to it, before the lossy stage so verbatim-skipped commands are covered; any differing line, CRLF, short content, errors and compaction stay literal.",
    sim=sim_content_stub,
)

# ---------------------------------------------------------------------------------------------------------------
# #144 Own-edit-aware re-read. Shadow of the last full Read plus the model's successful Edit pairs replayed onto it.
# If the file on disk equals the shadow, a full re-read is shown as the edited ranges (+3 context lines) and pointers
# to unchanged ranges, giving the old line numbers they had at the anchor Read.


class OwnEditShadow:
    CONTEXT = 3

    def __init__(self, ws: Workspace):
        self.ws, self.sh = ws, {}

    def on_compact(self):
        self.sh = {}

    def _replay(self, sh: dict, old: str, new: str, replace_all: bool) -> None:
        text, origin = sh["text"], list(sh["origin"])
        apply_edit(text, old, new, replace_all)  # same acceptance rule as the Edit tool, or ValueError
        pos, hits = text.find(old), []
        while pos != -1:
            hits.append(pos)
            pos = text.find(old, pos + len(old)) if replace_all else -1
        for pos in reversed(hits):  # right to left keeps the earlier offsets valid
            a, b = text.count("\n", 0, pos), text.count("\n", 0, pos + len(old))
            origin[a:b + 1] = [None] * (new.count("\n") + 1)
            text = text[:pos] + new + text[pos + len(old):]
        sh["text"], sh["origin"] = text, origin  # only now: an exception above leaves the shadow as it was

    def _view(self, path: str, sh: dict, raw: str):
        text, n = sh["text"], len(lines_of(sh["text"]))
        lines, orig = lines_of(text), sh["origin"][:n]
        sites = {i for i, o in enumerate(orig) if o is None or (i == 0 and o != 0) or (i > 0 and orig[i - 1] is not None and o != orig[i - 1] + 1)}
        if not sites:
            out = f"{MARK} unchanged since your full Read at turn {sh['turn']}: {path}, {n} lines]"
            return out if len(out) < len(raw) else None
        show = sorted({k for s_ in sites for k in range(max(0, s_ - self.CONTEXT), min(n, s_ + self.CONTEXT + 1))})
        out, i = [f"{MARK} {path} now has {n} lines; changed only by your Edits since your full Read at turn {sh['turn']}. Edited lines with {self.CONTEXT} lines of context are shown; unchanged ranges are pointers (new numbers, old numbers at that turn)]"], 0
        while i < n:
            if i in show:
                out.append(f"{i + 1:6d}\t{lines[i]}")
                i += 1
                continue
            j = i
            while j + 1 < n and (j + 1) not in show:
                j += 1
            assert orig[j] - orig[i] == j - i, "an unchanged range must map to a contiguous anchor range"
            out.append(f"{MARK} lines {i + 1}-{j + 1} unchanged (were {orig[i] + 1}-{orig[j] + 1} at turn {sh['turn']})]")
            i = j + 1
        res = "\n".join(out)
        return res if len(res) < len(raw) else None

    @fail_open
    def post(self, turn, tool, args, raw):
        path = args.get("path")
        if tool == "Edit":
            sh = self.sh.get(path)
            if sh and raw.startswith("The file"):
                try:
                    self._replay(sh, args["old_string"], args["new_string"], args.get("replace_all", False))
                except ValueError:
                    self.sh.pop(path, None)
            return raw
        if tool != "Read" or MARK in raw or raw.startswith("Error") or not raw or "offset" in args or "limit" in args:
            return raw
        actual, sh = self.ws.get(path), self.sh.get(path)
        if sh is not None and sh["text"] == actual:
            out = self._view(path, sh, raw)
            if out is not None:
                return out
        self.sh[path] = dict(turn=turn, text=actual, origin=list(range(len(actual.split("\n")))))
        return raw


def rebuild_own_edit_read(s: Session, text: str, path: str) -> list[str]:
    """Reader view: numbered lines as given, pointer ranges copied from the anchor Read's literal lines."""
    head = text.split("\n")[0]
    m = re.match(r"\[ctxprune: unchanged since your full Read at turn (\d+): (\S+), (\d+) lines\]$", head)
    if m:
        anchor = parse_numbered(s.result(int(m.group(1)))["text"])
        return [anchor[i] for i in range(1, int(m.group(3)) + 1)]
    m = re.match(r"\[ctxprune: (\S+) now has (\d+) lines; ", head)
    total, got = int(m.group(2)), {}
    for l in text.split("\n")[1:]:
        mm = re.fullmatch(r"\[ctxprune: lines (\d+)-(\d+) unchanged \(were (\d+)-(\d+) at turn (\d+)\)\]", l)
        if mm:
            a, b, c, _d, t = map(int, mm.groups())
            anchor = parse_numbered(s.result(t)["text"])
            for k in range(b - a + 1):
                got[a + k] = anchor[c + k]
        else:
            nn = _NUM.match(l)
            got[int(nn.group(1))] = nn.group(2)
    assert sorted(got) == list(range(1, total + 1)), "every line of the file is either shown or pointed at"
    return [got[i] for i in range(1, total + 1)]


def sim_own_edit_read() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        tools, hook = Tools(ws), OwnEditShadow(ws)
        s = Session(hook)
        P = "app/service.py"
        ws.put(P, py_module("svc", 28))

        def read(**kw):
            raw = tools.read(P, **kw)
            seen = s.step("Read", {"path": P, **kw}, raw)
            if is_stub(seen):
                assert rebuild_own_edit_read(s, seen, P) == lines_of(ws.get(P)), "reader view: anchor Read + pointers + shown lines = the file"
            return raw, seen

        def edit(old, new, **kw):
            raw = tools.edit(P, old, new, **kw)
            s.step("Edit", {"path": P, "old_string": old, "new_string": new, **kw}, raw)
            return raw

        raw, seen = read()
        assert seen == raw
        n_lines = len(lines_of(ws.get(P)))
        assert "updated" in edit("    total = a * 7 + b\n", "    total = a * 70 + b\n")              # same line count
        assert "updated" in edit("def svc_12(a, b=0):\n", "def svc_12(a, b=0, *, scale=1):\n    # scale is applied below\n    a = a * scale\n")  # +2 lines
        assert "updated" in edit("        return total - 20\n", "")                                    # -1 line
        raw, seen = read()
        assert is_stub(seen) and len(lines_of(ws.get(P))) == n_lines + 1
        assert tokens(seen) < tokens(raw) * 0.2, (tokens(seen), tokens(raw))
        assert "were 1-" in seen or "unchanged (were" in seen
        hunk_tokens = (tokens(raw), tokens(seen))
        # a failed Edit (not unique) changes nothing, the shadow stays right
        assert edit("    return total\n", "    return total + 1\n").startswith("Error")
        assert "updated" in edit("    total = a * 9 + b\n", "    total = a * 90 + b\n")
        raw, seen = read()
        assert is_stub(seen) and "a * 90" in seen and "a * 70" in seen, "both successful edits are shown, the failed one is not"
        # a ranged Read is not rewritten and does not disturb the shadow
        raw, seen = read(offset=50, limit=20)
        assert seen == raw
        raw, seen = read()
        assert is_stub(seen)
        # a foreign change (formatter) on top of the model's edits: no stub, new anchor
        ws.put(P, ws.get(P).replace("svc_5(a, b=0)", "svc_5(a, b = 0)"))
        raw, seen = read()
        assert seen == raw and "b = 0" in raw, "foreign change: full text"
        anchor2 = s.turn
        assert "updated" in edit("    total = a * 11 + b\n", "    total = a * 110 + b\n")
        raw, seen = read()
        assert is_stub(seen) and f"turn {anchor2}" in seen and "a * 70" not in seen, "the new anchor is the formatter-era read"
        # the model's Edit loses to a foreign change between its Read and its Edit: tool refuses, shadow must not diverge
        ws.put(P, ws.get(P) + "# appended by a hook\n")
        assert edit("    total = a * 13 + b\n", "    total = a * 130 + b\n").startswith("Error: File has been modified")
        raw, seen = read()
        assert seen == raw, "file differs from the shadow: full read"
        # no edits at all: a plain unchanged stub
        raw, seen = read()
        assert is_stub(seen) and "unchanged since your full Read" in seen
        # replace_all touches several places
        assert "updated" in edit("    return total\n", "    return total  # ok\n", replace_all=True)
        raw, seen = read()
        assert seen == raw, "28 edit sites: the pointer form would not be shorter, so the full text is shown"
        # compaction forgets
        s.compact()
        raw, seen = read()
        assert seen == raw
        assert "updated" in edit("    total = a * 15 + b\n", "    total = a * 150 + b\n")
        hook.sh[P]["origin"] = None  # corrupted shadow
        raw, seen = read()
        assert seen == raw, "fail open"
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "full_read_tokens": hunk_tokens[0], "hunk_read_tokens": hunk_tokens[1]}


case(
    144,
    claim="A full re-read of a file that differs from the last read only by the model's own successful Edits (replayed onto a shadow) is shown as the edited lines plus 3 context lines and pointers (new and old line numbers) to unchanged ranges; a foreign change, a failed Edit, a ranged Read, compaction or a broken shadow give the full text.",
    sim=sim_own_edit_read,
)

# ---------------------------------------------------------------------------------------------------------------
# #349 Delta-encode repeated browser accessibility snapshots per tab. The ledger holds the last snapshot of each tab;
# the delta is the same keep/drop/add line format as #62 plus an explicit `removed refs:` list, so a model that cannot
# replay the delta can still tell which refs are gone. Refs must be stable on the server: if they are not, almost
# every line changes and the size gate falls back to the full snapshot.


class FakePage:
    """A Playwright-MCP-like page. stable=True: a ref is assigned to an element once and kept; stable=False: refs are
    renumbered in document order on every snapshot (the failure the idea says to check first)."""

    def __init__(self, base: str, stable: bool = True):
        self.base, self.stable, self.refs = base, stable, {}
        self.cart, self.query, self.page, self.dialog, self.status = 0, "", 1, False, ""

    def _ref(self, key, n):
        if not self.stable:
            return f"e{n}"
        return f"e{self.refs.setdefault(key, len(self.refs) + 1)}"

    def ref(self, key) -> str:
        return f"e{self.refs[key]}"

    def products(self):
        hit = [i for i in range(1, 25) if self.query.lower() in f"product {i}"]
        return hit[(self.page - 1) * 12: self.page * 12]

    def url(self):
        return f"{self.base}/products?page={self.page}"

    def snapshot(self) -> str:
        n, out = [0], []

        def el(depth, role, name, key, extra=""):
            n[0] += 1
            r = self._ref(key, n[0])
            nm = f' "{name}"' if name else ""
            out.append(f"{'  ' * depth}- {role}{nm} [ref={r}]{extra}")

        el(0, "banner", "", ("banner",))
        for i, label in enumerate(["Home", "Shop", "Deals", "Help", "Account", "Wishlist"]):
            el(1, "link", label, ("nav", i), " [cursor=pointer]")
        el(1, "button", f"Cart ({self.cart})", ("cart",))
        el(0, "main", "", ("main",))
        if self.status:
            el(1, "status", self.status, ("status",))
        el(1, "textbox", "Search", ("search",), f": {self.query}" if self.query else "")
        el(1, "list", "", ("list",))
        for pid in self.products():
            el(2, "listitem", "", ("item", pid))
            el(3, "heading", f"Product {pid}", ("h", pid), " [level=3]")
            el(3, "text", "", ("price", pid), f": ${pid * 3}.99")
            el(3, "button", f"Add Product {pid} to cart", ("add", pid), " [cursor=pointer]")
        el(1, "link", "Next page", ("pager",), " [cursor=pointer]")
        if self.dialog:
            el(1, "dialog", f"Cart: {self.cart} items", ("dialog",))
            el(2, "button", "Close", ("dclose",), " [cursor=pointer]")
        el(0, "contentinfo", "", ("footer",))
        for i, label in enumerate(["Terms", "Privacy", "Contact"]):
            el(1, "link", label, ("foot", i), " [cursor=pointer]")
        return f"- Page URL: {self.url()}\n- Page Title: Shop\n- Page Snapshot:\n```yaml\n" + "\n".join(out) + "\n```\n"

    def do(self, op: tuple) -> None:
        kind = op[0]
        if kind == "type":
            self.query, self.page = op[1], 1
        elif kind == "add":
            self.cart += 1
            self.status = f"Added Product {op[1]} to cart"
        elif kind == "dialog":
            self.dialog = not self.dialog
        elif kind == "page":
            self.page = op[1]
        else:
            raise ValueError(kind)


_REF = re.compile(r"\[ref=(e\d+)\]")
_ELEM = re.compile(r"^\s*- (\w+)(?: \"([^\"]*)\")?.*\[ref=(e\d+)\]")


def refs_of(lines) -> set:
    return {r for l in lines for r in _REF.findall(l)}


def refs_stable(snaps: list[str]) -> bool:
    """The server-side check the idea starts with: an element that persists keeps its ref between snapshots."""
    for a, b in zip(snaps, snaps[1:]):
        ea = {(m.group(1), m.group(2)): m.group(3) for l in a.split("\n") for m in [_ELEM.match(l)] if m and m.group(2)}
        eb = {(m.group(1), m.group(2)): m.group(3) for l in b.split("\n") for m in [_ELEM.match(l)] if m and m.group(2)}
        if any(ea[k] != eb[k] for k in ea.keys() & eb.keys() if k[0] not in ("status", "dialog")):
            return False
    return True


SNAP_HDR = "{m} snapshot delta vs turn {t} of this tab: keep/drop N = lines of that snapshot, add N = N literal lines follow; removed refs: {r}]\n"
SNAP_RX = re.compile(r"\[ctxprune: snapshot delta vs turn (\d+) of this tab: [^\n]*; removed refs: ([^\]]*)\]\n")


class SnapshotDelta:
    MAX_CHAIN = 4

    def __init__(self):
        self.tab: dict | None = {}

    def on_compact(self):
        self.tab = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        t = args.get("tab")
        if tool == "browser_close":
            self.tab.pop(t, None)
            return raw
        if tool != "browser_snapshot" or MARK in raw:
            return raw
        url = raw.split("\n", 1)[0]
        prev, out, depth = self.tab.get(t), raw, 0
        if prev and prev["url"] == url and prev["depth"] < self.MAX_CHAIN:  # other page: start over, refs are not comparable
            a, b = items_of(prev["text"]), items_of(raw)
            removed = sorted(refs_of(a) - refs_of(b), key=lambda r: int(r[1:]))
            cand = SNAP_HDR.format(m=MARK, t=prev["turn"], r=" ".join(removed) or "none") + make_delta(a, b)
            if len(cand) < 0.8 * len(raw):
                out, depth = cand, prev["depth"] + 1
        self.tab[t] = {"turn": turn, "text": raw, "url": url, "depth": depth}
        return out


def resolve_snapshot(s: Session, turn: int) -> str:
    t = s.result(turn)["text"]
    m = SNAP_RX.match(t)
    if not m:
        return t
    assert s.use(int(m.group(1)))["args"]["tab"] == s.use(turn)["args"]["tab"], "a delta never references another tab"
    return "".join(apply_delta(items_of(resolve_snapshot(s, int(m.group(1)))), t[m.end():]))


def visible_refs(s: Session, tab: str) -> set:
    """Reader view that does not replay the delta: refs literally shown (full snapshot or `add` lines) minus the
    explicit removed list, accumulated over the tab's results in order."""
    refs: set = set()
    for e in s.ctx:
        if e["kind"] != "result" or e["tool"] != "browser_snapshot" or e["args"]["tab"] != tab:
            continue
        m = SNAP_RX.match(e["text"])
        if not m:
            refs = refs_of(e["text"].split("\n"))
            continue
        refs -= set(m.group(2).split())
        body, pos = e["text"][m.end():], 0
        while pos < len(body):
            nl = body.index("\n", pos)
            op, n = body[pos:nl].split(" ")
            pos = nl + 1
            if op == "add":
                for _ in range(int(n)):
                    mm = _ITEM.match(body, pos)
                    refs |= refs_of([mm.group()])
                    pos = mm.end()
    return refs


SCRIPT_349 = [("a", ("snap",)), ("a", ("type", "Product 1")), ("a", ("type", "")), ("a", ("add", 3)), ("a", ("add", 5)),
              ("a", ("dialog",)), ("a", ("dialog",)), ("b", ("snap",)), ("b", ("add", 2)), ("a", ("page", 2)),
              ("a", ("add", 14)), ("a", ("page", 1)), ("a", ("add", 7)), ("a", ("type", "Product 2")), ("a", ("type", "")),
              ("b", ("add", 4)), ("a", ("add", 9)), ("a", ("dialog",)), ("a", ("dialog",)), ("a", ("add", 11))]


def click_key(pg: FakePage, op: tuple):
    """The element the model acts on for `op`, known before the action runs."""
    return {"type": ("search",), "add": ("add", op[1] if len(op) > 1 else None), "page": ("pager",),
            "dialog": ("dclose",) if pg.dialog else ("cart",)}[op[0]]


def run_browser(stable: bool, hook=None):
    s = Session(hook)
    pages = {"a": FakePage("https://shop.test", stable), "b": FakePage("https://blog.test", stable)}
    snaps, modes = [], []
    for tab, op in SCRIPT_349:
        pg = pages[tab]
        if op[0] != "snap":
            pg.do(op)
        raw = pg.snapshot()
        seen = s.step("browser_snapshot", {"tab": tab, "action": op[0]}, raw)
        snaps.append((tab, raw))
        modes.append("delta" if is_stub(seen) else "full")
        if hook is not None:
            assert resolve_snapshot(s, s.turn) == raw, "reader view: previous snapshot + delta rebuilds the page exactly"
            assert visible_refs(s, tab) == refs_of(raw.split("\n")), "refs the model can see == refs on the page (nothing stale, nothing missing)"
    return s, snaps, modes


def sim_snapshot_delta() -> dict:
    # 1. refs stable on this server, then the delta run
    _, snaps, _ = run_browser(True, None)
    for tab in "ab":
        assert refs_stable([t for tb, t in snaps if tb == tab]), "precondition from the idea: refs stay stable across snapshots"
    hook = SnapshotDelta()
    s, snaps, modes = run_browser(True, hook)
    assert modes.count("delta") >= 12, modes
    assert modes[9] == "full" and modes[11] == "full", "URL change (page 2, back to page 1) starts a fresh snapshot"
    assert s.saved > 0.5 * s.raw_tok, (s.saved, s.raw_tok)
    # every click's ref was visible before the click: replay the script and check against the ctx at that moment
    s2, pages = Session(SnapshotDelta()), {"a": FakePage("https://shop.test"), "b": FakePage("https://blog.test")}
    clicked = 0
    for tab, op in SCRIPT_349:
        pg = pages[tab]
        if op[0] != "snap":
            key = click_key(pg, op)
            assert pg.ref(key) in visible_refs(s2, tab), f"{key} (ref {pg.ref(key)}) must be visible before the click"
            clicked += 1
            pg.do(op)
        s2.step("browser_snapshot", {"tab": tab}, pg.snapshot())
    assert clicked >= 17
    # 2. the failure case: refs renumbered on every snapshot -> the check detects it, the gate falls back to full text
    _, snaps_u, _ = run_browser(False, None)
    assert not refs_stable([t for tb, t in snaps_u if tb == "a"]), "the stability check must catch a server that renumbers refs"
    su = Session(SnapshotDelta())
    pg = FakePage("https://shop.test", stable=False)
    su.modes_full = 0
    for op in [("snap",), ("add", 3), ("add", 5), ("type", "Product 1"), ("type", "")]:
        if op[0] != "snap":
            pg.do(op)
        raw = pg.snapshot()
        seen = su.step("browser_snapshot", {"tab": "a"}, raw)
        su.modes_full += seen == raw
        assert resolve_snapshot(su, su.turn) == raw
    assert su.modes_full >= 3, "every structural change renumbers everything below it: those steps must be full snapshots"
    # 3. closing a tab drops its ledger; compaction forgets; fail open
    h3, pg = SnapshotDelta(), FakePage("https://shop.test")
    s3 = Session(h3)
    raw = pg.snapshot()
    s3.step("browser_snapshot", {"tab": "a"}, raw)
    pg.do(("add", 1))
    raw2 = pg.snapshot()
    assert is_stub(s3.step("browser_snapshot", {"tab": "a"}, raw2))
    s3.step("browser_close", {"tab": "a"}, "closed")
    assert s3.step("browser_snapshot", {"tab": "a"}, raw2) == raw2, "reopened tab id: full snapshot"
    pg.do(("add", 2))
    assert is_stub(s3.step("browser_snapshot", {"tab": "a"}, pg.snapshot()))
    s3.compact()
    pg.do(("add", 3))
    raw4 = pg.snapshot()
    assert s3.step("browser_snapshot", {"tab": "a"}, raw4) == raw4, "after compaction the previous snapshot is gone"
    h3.tab["a"]["text"] = None
    pg.do(("add", 4))
    raw5 = pg.snapshot()
    assert s3.step("browser_snapshot", {"tab": "a"}, raw5) == raw5, "fail open"
    return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "delta_steps": modes.count("delta"), "steps": len(modes)}


case(
    349,
    claim="After the first snapshot of a tab, a repeated accessibility snapshot of the same URL is sent as keep/drop/add line counts, the changed lines and an explicit removed-refs list; replaying it rebuilds every page exactly, every ref the model acts on is visible beforehand, and a URL change, renumbered refs, a closed tab or compaction give the full snapshot.",
    sim=sim_snapshot_delta,
)

# ---------------------------------------------------------------------------------------------------------------
# #530 Seed the ledger with text the model already holds: CLAUDE.md (survives compaction), @-attachments, prompts and
# its own messages (do not). A tool result whose lines contain a run of lines equal to a run of a seeded text
# gets that run replaced by a pointer naming the source and the source lines.

MIN_RUN_LINES = 3


class SeededLedger:
    def __init__(self):
        self.src: dict | None = {}

    def seed(self, label, text, pinned=False):
        self.src[label] = dict(lines=lines_of(text), pinned=pinned)

    def on_compact(self):
        self.src = {k: v for k, v in self.src.items() if v["pinned"]}

    @fail_open
    def post(self, turn, tool, args, raw):
        if MARK in raw or not raw or raw.startswith(("Error", "Exit code")) or not self.src:
            return raw
        if tool == "Read":
            rows = [_NUM.match(l) for l in raw.split("\n")]
            if not all(rows):
                return raw
            nums, items = [int(r.group(1)) for r in rows], [r.group(2) for r in rows]
        elif tool == "Bash":
            nums, items = None, raw.split("\n")
        else:
            return raw
        sources = {k: v["lines"] for k, v in self.src.items()}
        idx, rawlines = index_sources(sources), raw.split("\n")
        out, i = [], 0
        while i < len(items):
            r = best_run_at(items, i, idx, sources)
            if r and r[1] - r[0] >= MIN_RUN_LINES and (nums is None or all(nums[k + 1] == nums[k] + 1 for k in range(r[0], r[1] - 1))):
                n, label, c = r[1] - r[0], r[2], r[3]
                where = f"lines {nums[r[0]]}-{nums[r[1] - 1]} already in context: {label}, lines {c + 1}-{c + n}" if nums else f"{n} lines already in context: {label}, lines {c + 1}-{c + n}"
                marker = f"{MARK} {where}]"
                orig = rawlines[r[0]:r[1]]
                if len(marker) + 25 <= sum(len(l) + 1 for l in orig):
                    out.append(marker)
                    i = r[1]
                    continue
            out.append(rawlines[i])
            i += 1
        res = "\n".join(out)
        return res if len(res) < len(raw) else raw


def expand_seeded(s: Session, text: str) -> str:
    """Reader view: each pointer names a source label that must still be in context; copy its lines back."""
    out = []
    for l in text.split("\n"):
        m = re.fullmatch(r"\[ctxprune: (?:lines (\d+)-(\d+)|(\d+) lines) already in context: (.*), lines (\d+)-(\d+)\]", l)
        if not m:
            out.append(l)
            continue
        a, b, _n, label, c, d = m.groups()
        src = next(e for e in s.ctx if e["kind"] == "msg" and e["label"] == label)  # StopIteration = dangling pointer
        body = lines_of(src["text"])[int(c) - 1:int(d)]
        out += [f"{int(a) + k:6d}\t{x}" for k, x in enumerate(body)] if a else body
    return "\n".join(out)


def sim_seeded() -> dict:
    with tempfile.TemporaryDirectory() as d:
        ws, hook = Workspace(d), SeededLedger()
        tools, s = Tools(ws), Session(hook)
        claude_md = "# Project rules\n" + "".join(f"- Rule {i}: always run the linter before committing change set {i}.\n" for i in range(8))
        config = py_module("config", 14)
        trace = ("Traceback (most recent call last):\n" + "".join(f'  File "src/app/worker.py", line {40 + i}, in step_{i}\n    value = compute_{i}(payload)\n' for i in range(5))
                 + "KeyError: 'payload_id'\n")
        ws.put("CLAUDE.md", claude_md)
        ws.put("src/config.py", config)
        s.say("CLAUDE.md", claude_md, pinned=True)
        s.say("@src/config.py", config)
        s.say("user prompt 3", "The worker crashes with this:\n\n" + trace + "\nPlease fix it.")
        s.say("assistant message 4", "I will start from this snippet:\n```python\n" + "".join(lines_of(config)[4:14]) + "\n```\n")

        def call(tool, args, raw, expect):
            seen = s.step(tool, args, raw)
            assert expand_seeded(s, seen) == raw, "reader view: pointers expand to exactly the raw result"
            assert (seen != raw) == expect, (tool, args, seen[:100])
            return seen

        seen = call("Read", {"path": "src/config.py"}, tools.read("src/config.py"), True)
        assert seen.count("\n") == 0 and "@src/config.py" in seen, "whole attached file: one pointer"
        call("Bash", {"command": "cat CLAUDE.md"}, claude_md, True)
        out = "collecting...\nERROR in worker\n" + trace + "worker exited with status 1\n"
        seen = call("Bash", {"command": "python -m app.worker"}, out, True)
        assert "collecting..." in seen and "worker exited" in seen and "user prompt 3" in seen, "only the echoed block is replaced"
        # the file changed on disk after the attachment (git pull): unchanged runs are pointers, new lines literal
        lines = lines_of(config)
        lines[30] = "    total = a * 999 + b  # changed upstream"
        lines.insert(60, "# inserted line shifts everything below")
        ws.put("src/config.py", "\n".join(lines) + "\n")
        seen = call("Read", {"path": "src/config.py"}, tools.read("src/config.py"), True)
        assert "changed upstream" in seen and "inserted line shifts" in seen and seen.count(MARK) >= 3
        # unrelated text, tiny overlaps: nothing to do
        call("Bash", {"command": "ls"}, "a.py\nb.py\nc.py\n", False)
        call("Bash", {"command": "echo"}, "- Rule 1: always run the linter before committing change set 1.\n", False)
        ws.put("src/other.py", "".join(f"Note {i}: this paragraph of the release notes is about topic number {i}.\n" for i in range(30)))
        call("Read", {"path": "src/other.py"}, tools.read("src/other.py"), False)
        assert s.step("Bash", {"command": "cat NOTE"}, f"see {MARK} x]\n" + claude_md) == f"see {MARK} x]\n" + claude_md, "hostile text passes through"
        # compaction: CLAUDE.md is still in context, the attachment, prompt and assistant text are not
        s.compact()
        assert [e["label"] for e in s.ctx] == ["CLAUDE.md"]
        call("Read", {"path": "src/config.py"}, tools.read("src/config.py"), False)
        call("Bash", {"command": "cat CLAUDE.md"}, claude_md, True)
        call("Bash", {"command": "python -m app.worker"}, out, False)
        hook.src = {"x": {"lines": None, "pinned": True}}  # corrupted ledger
        call("Bash", {"command": "cat CLAUDE.md"}, claude_md, False)
        assert s.saved > 0.3 * s.raw_tok, (s.saved, s.raw_tok)
        return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved}


case(
    530,
    claim="A Read or Bash result containing runs of at least 3 lines that equal lines of CLAUDE.md, an @-attachment, a user prompt or an assistant message already in context gets each run replaced by a pointer naming that source and its lines; changed lines stay literal, and after compaction only CLAUDE.md remains a source.",
    sim=sim_seeded,
)

# ---------------------------------------------------------------------------------------------------------------
# #763 Chunk-id seen-set across retrieval calls. Key (tool, chunk id) plus a hash of the chunk text; a chunk seen in
# one of the last WINDOW calls is replaced by {id, score, seen_in_call}, keeping this call's score and rank.

WINDOW = 5
TOPICS = {
    "token": "rotate api token key credentials secret rotation expire revoke",
    "invoice": "billing invoice export csv payment statement tax receipt",
    "deploy": "deploy rollback release canary rollout revert pipeline",
}


def make_corpus() -> dict:
    out = {}
    for topic, words in TOPICS.items():
        w = words.split()
        for k in range(10):
            out[f"{topic}.md#{k}"] = " ".join(f"The {w[(k + j) % len(w)]} step {k} says to check the {w[(k * 3 + j) % len(w)]} settings first, then confirm with the {w[(k + 2 * j) % len(w)]} dashboard." for j in range(3))
    return out


def retrieve(corpus: dict, query: str, k: int = 5) -> str:
    q = set(query.lower().split())
    scored = sorted(((len(q & set(t.lower().replace(".", "").replace(",", "").split())) / (1 + len(t) / 400), cid) for cid, t in corpus.items()), key=lambda x: (-x[0], x[1]))[:k]
    return json.dumps([{"id": cid, "score": round(sc, 3), "text": corpus[cid]} for sc, cid in scored], ensure_ascii=False)


class ChunkSeen:
    def __init__(self):
        self.seen: dict | None = {}
        self.calls = 0

    def on_compact(self):
        self.seen = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "search_docs":
            return raw
        self.calls += 1
        data = json.loads(raw)
        out, changed = [], False
        for e in data:
            key, h = (tool, e["id"]), sha(e["text"])
            prev = self.seen.get(key)
            if prev and prev[0] == h and self.calls - prev[2] <= WINDOW:
                stub = {"id": e["id"], "score": e["score"], "seen_in_call": prev[1]}
                if len(json.dumps(stub)) < len(json.dumps(e, ensure_ascii=False)):
                    out.append(stub)
                    changed = True
                    continue
            self.seen[key] = (h, turn, self.calls)
            out.append(e)
        return json.dumps(out, ensure_ascii=False) if changed else raw


def rebuild_chunks(s: Session, text: str) -> list:
    """Reader view: a stub's text comes from the earlier call's literal entry with the same id."""
    out = []
    for e in json.loads(text):
        if "seen_in_call" in e:
            src = [x for x in json.loads(s.result(e["seen_in_call"])["text"]) if x["id"] == e["id"] and "text" in x]
            assert len(src) == 1, "the pointed-at call must carry the chunk literally"
            e = {"id": e["id"], "score": e["score"], "text": src[0]["text"]}
        out.append(e)
    return out


def sim_chunk_seen() -> dict:
    corpus = make_corpus()
    s = Session(ChunkSeen())
    queries = ["how do I rotate the api token", "token rotation procedure api key", "rotate api credentials safely",
               "billing invoice export csv", "export invoices as csv statement", "deploy rollback steps",
               "how to rollback a deploy release", "rotate api token then revoke old key", "invoice csv export tax",
               "canary rollout revert pipeline", "rotate token credentials expire"]
    shares = []
    seen_ids: list[set] = []
    for q in queries:
        raw = retrieve(corpus, q)
        ids = [e["id"] for e in json.loads(raw)]
        recent = set().union(*seen_ids[-WINDOW:]) if seen_ids else set()
        shares.append(len(set(ids) & recent) / len(ids))
        seen_ids.append(set(ids))
        seen = s.step("search_docs", {"query": q}, raw)
        assert rebuild_chunks(s, seen) == json.loads(raw), "reader view: stubs + earlier literal calls rebuild the result list (ids, scores, text, order)"
    assert max(shares) > 0.5 and s.saved > 0.2 * s.raw_tok, (shares, s.saved, s.raw_tok)
    # re-indexed corpus: same id, different text -> shown in full
    corpus2 = dict(corpus)
    first_id = json.loads(retrieve(corpus, "rotate api token"))[0]["id"]
    corpus2[first_id] = corpus[first_id].replace("check", "verify")
    raw = retrieve(corpus2, "rotate api token")
    seen = s.step("search_docs", {"query": "rotate api token"}, raw)
    assert any(e["id"] == first_id and "text" in e and "verify" in e["text"] for e in json.loads(seen)), "changed chunk text is never stubbed"
    assert rebuild_chunks(s, seen) == json.loads(raw)
    # outside the window: a chunk seen long ago is shown again
    h = Session(ChunkSeen())
    h.step("search_docs", {"query": "x"}, retrieve(corpus, "rotate api token"))
    for _ in range(WINDOW):
        h.step("search_docs", {"query": "y"}, retrieve(corpus, "billing invoice csv"))
    raw = retrieve(corpus, "rotate api token")
    seen = h.step("search_docs", {"query": "x"}, raw)
    assert seen == raw, "seen more than WINDOW calls ago: literal again"
    # another tool with the same chunk ids, odd payloads, compaction, fail open
    assert h.step("other_search", {"query": "x"}, raw) == raw
    assert h.step("search_docs", {"query": "x"}, "not json at all") == "not json at all"
    assert h.step("search_docs", {"query": "x"}, '[{"title": "no id"}]') == '[{"title": "no id"}]'
    h.compact()
    assert h.step("search_docs", {"query": "x"}, raw) == raw
    h.hook.seen = None
    assert h.step("search_docs", {"query": "x"}, raw) == raw
    return {"tokens_without": s.raw_tok, "tokens_with": s.seen_tok, "saved": s.saved, "max_share_seen": round(max(shares), 2)}


case(
    763,
    claim="A retrieved chunk whose id and text hash were returned literally by the same tool within the last 5 calls is replaced by {id, score, seen_in_call}, keeping this call's score and rank; re-indexed text, a chunk last seen longer ago, another tool, malformed results and compaction stay literal.",
    sim=sim_chunk_seen,
)

# ---------------------------------------------------------------------------------------------------------------
# #908 Line-keyed delta for repeated git diff after further edits. A diff body line is keyed (file, line with its
# +/-/space marker); a run of lines already shown literally becomes `K lines as before: turn T lines a-b`, where a-b
# are visible line numbers of that turn's output (those lines are literal there, so the reader needs no replay).


class LineKeyedDiff:
    def __init__(self):
        self.loc: dict | None = {}  # (file, line) -> {turn: visible line number} of every literal occurrence

    def on_compact(self):
        self.loc = {}

    @fail_open
    def post(self, turn, tool, args, raw):
        if tool != "Bash" or MARK in raw or "@@ " not in raw:
            return raw
        lines = raw.split("\n")
        file_of = {}
        for file, a, b in parse_hunks(lines):
            for k in range(a + 1, b):
                file_of[k] = file
        cand = [self.loc.get((file_of[k], lines[k])) if k in file_of else None for k in range(len(lines))]
        out, vis_of, i = [], {}, 0
        while i < len(lines):
            if cand[i]:  # longest run whose earlier copies sit at consecutive visible lines of ONE earlier output
                live, j, best = dict(cand[i]), i + 1, None
                best = (j, dict(live))
                while j < len(lines) and cand[j]:
                    nxt = {t: v for t, v in live.items() if cand[j].get(t) == v + 1}
                    if not nxt:
                        break
                    live, j = {t: v + 1 for t, v in live.items() if t in nxt}, j + 1
                    best = (j, dict(live))
                j, live = best
                t = max(live)
                marker = f"{MARK} {j - i} lines as before: turn {t} lines {live[t] - (j - i) + 1}-{live[t]}]"
                if len(marker) < sum(len(l) + 1 for l in lines[i:j]):
                    out.append(marker)
                    i = j
                    continue
            out.append(lines[i])
            vis_of[i] = len(out)
            i += 1
        res = "\n".join(out)
        if len(res) >= len(raw):
            res, vis_of = raw, {k: k + 1 for k in range(len(lines))}
        for k, f in file_of.items():
            if k in vis_of:  # only lines that are literal in this output can be pointed at later
                self.loc.setdefault((f, lines[k]), {})[turn] = vis_of[k]
        return res


def expand_line_keyed(s: Session, text: str) -> str:
    out = []
    for l in text.split("\n"):
        m = re.fullmatch(r"\[ctxprune: (\d+) lines as before: turn (\d+) lines (\d+)-(\d+)\]", l)
        if not m:
            out.append(l)
            continue
        n, t, a, b = map(int, m.groups())
        src = s.result(t)["text"].split("\n")[a - 1:b]
        assert len(src) == n == b - a + 1 and not any(x.startswith(MARK) for x in src), "pointer must address literal lines"
        out += src
    return "\n".join(out)


def sim_line_keyed() -> dict:
    func = ["def process_order(order, ledger):", "    items = order.items", "    total = 0", "    for item in items:", "        total += item.price * item.qty",
            "    ledger.record(order.id, total)", "    return total"]
    base_file = lines_of(py_module("util", 3)) + [""] + func + ["", "def main():", "    return process_order(load(), Ledger())"]
    base = {"app.py": "\n".join(base_file) + "\n", "util.py": "import os\n\nVALUE = 1\n"}

    def ins_before(f, anchor, new):
        k = f.index(anchor)
        return f[:k] + new + f[k:]

    def ins_after(f, anchor, new):
        k = f.index(anchor) + 1
        return f[:k] + new + f[k:]

    sub = lambda f, a, b: [l.replace(a, b) for l in f]  # noqa: E731
    steps = [  # each edit is applied to the cumulative file, as in an edit-diff-edit loop
        lambda f: ins_before(f, "    total = 0", ["    if not order.items:", "        raise ValueError('empty order')", "    if order.currency not in SUPPORTED:",
                                                  "        raise ValueError('unsupported currency: ' + order.currency)"]),
        lambda f: ["import logging"] + ins_before(f, "    total = 0", ["    log.info('processing order %s with %d items', order.id, len(items))", "    started = time.monotonic()"]),
        lambda f: sub(f, "raise ValueError('empty order')", "raise OrderError(order.id, 'empty order')"),
        lambda f: ins_before(f, "    ledger.record(order.id, total)", ["    # totals are rounded to cents in the order currency", "    total = round(total, CURRENCY_DIGITS[order.currency])",
                                                                      "    if total < 0:", "        raise OrderError(order.id, 'negative total')"]),
        lambda f: [l for l in f if "log.info" not in l and "started =" not in l],
        lambda f: ins_after(f, "    for item in items:", ["        if item.discontinued:", "            continue", "        if item.qty <= 0:", "            raise OrderError(order.id, 'bad quantity')"]),
        lambda f: ins_after(sub(f, "total += item.price * item.qty", "total += item.price * item.qty * (1 - item.discount)"), "        total += item.price * item.qty * (1 - item.discount)",
                            ["    tax = total * TAX_RATE[order.country]", "    total += tax"]),
        lambda f: ins_after(f, "    ledger.record(order.id, total)", ["    metrics.incr('orders.processed')", "    metrics.observe('orders.total', total)", "    audit.log(order.id, total)"]),
    ]
    cur, outputs = list(base_file), []
    for st in steps:
        cur = st(cur)
        after = {"app.py": "\n".join(cur) + "\n", "util.py": "import os\n\nVALUE = 1\nimport logging\n" if len(outputs) >= 2 else base["util.py"]}
        outputs.append(git_diff(base, after))
    full, hh, lk = Session(), Session(HunkDedup()), Session(LineKeyedDiff())
    for raw in outputs:
        full.step("Bash", {"command": "git diff"}, raw)
        hh.step("Bash", {"command": "git diff"}, raw)
        seen = lk.step("Bash", {"command": "git diff"}, raw)
        assert expand_line_keyed(lk, seen) == raw, "reader view: every pointer resolves to literal lines still in context; patch stays readable"
        assert seen.count("@@ ") == raw.count("@@ "), "current hunk headers are always literal"
    full_tok, hunk_tok, line_tok = full.seen_tok, hh.seen_tok, lk.seen_tok
    assert hunk_tok >= 0.95 * full_tok, "hunk-hash dedup (#139) saves nothing when every hunk grows or shifts"
    assert line_tok <= 0.65 * full_tok, (line_tok, full_tok)
    # the same added line in a different file is not 'as before'; markers differ from space-prefixed context
    last = lk.result(lk.turn)["text"]
    util_part = last[last.index("diff --git a/util.py"):]
    assert "+import logging" in util_part and MARK not in util_part.split("@@ ", 1)[1].split("\n", 1)[1].split("\n")[2], "util.py's added line is literal in its own file, though app.py shows the same text"
    # hostile text, compaction, fail open
    hostile = outputs[-1] + f"+{MARK} 3 lines as before: turn 1 lines 1-3]\n"
    assert lk.step("Bash", {"command": "cat PATCH"}, hostile) == hostile
    lk.compact()
    assert lk.step("Bash", {"command": "git diff"}, outputs[-1]) == outputs[-1], "after compaction nothing is 'as before'"
    lk.hook.loc = None
    assert lk.step("Bash", {"command": "git diff"}, outputs[-1]) == outputs[-1], "fail open"
    return {"tokens_full_diffs": full_tok, "tokens_hunk_hash": hunk_tok, "tokens_line_keyed": line_tok, "saved": full_tok - line_tok}


case(
    908,
    claim="For repeated git diff after incremental edits, diff body lines already shown literally (same file, same text and marker) become 'K lines as before: turn T lines a-b' pointers under the current literal @@ headers; where hunk-hash dedup saves nothing, this saves a measured share; other files, unseen lines, hostile text and compaction stay literal.",
    sim=sim_line_keyed,
)
