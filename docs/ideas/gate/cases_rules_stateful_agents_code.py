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
            old = f"total = x * "
            disk[p] = disk[p].replace(old, "total = 2 * x * ", 1)
            call("Edit", {"file_path": p, "old_string": old, "new_string": "total = 2 * x * "}, f"The file {p} has been updated.")
        elif k == 5:  # Read
            call("Read", {"file_path": p}, disk.get(p, "File does not exist."), error=p not in disk)
        elif k == 7:  # a Write that fails: the content is NOT on disk
            c = _code(g, "readonly", 60)
            truth.append(("write", "/etc/protected.py", c, "error", t))
            call("Write", {"file_path": "/etc/protected.py", "content": c}, "EACCES: permission denied, open '/etc/protected.py'", error=True)
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
    claim="Write inputs and `cat > file <<EOF` bodies older than ten assistant turns become one line (line count, sha1, 'on disk at this path'); young turns, failed or deleted writes, short content, script heredocs and files not on disk now keep their content, every stub's hash is reproducible from the disk or marked changed with a later Edit in history, a message flips at most once, and the sim shows that stubbing every turn re-bills the cached prefix while batches of ten do not.",
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
    # identifiers that the assistant mentions later are still in the stub (the path), so the agent knows what to unfold
    later_mentions = {"src/cart.py"}
    for m in folded:
        for b in m["content"] if m["role"] == "user" else []:
            if b.get("folded") and "src/cart.py" in b["content"]:
                later_mentions.discard("src/cart.py")
    assert not later_mentions, "the folded cart.py reads keep their path in the stub"
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
    assert auc_bm > auc_age + 0.1, f"relevance must beat age on this reuse model ({auc_bm:.2f} vs {auc_age:.2f})"
    assert kept_bm > kept_age, "at an equal keep budget the relevance rule keeps more of the results that were reused"

    # --- the policy: which aged results to stub. Keep = youngest 8, top-K by BM25, any result naming a path the prompt names, errors.
    def to_stub(results, prompt, k=12, young=8):
        aged = [i for i, r in enumerate(results) if r["turn"] < len(results) - young]
        if not _terms(prompt):
            return sorted(aged, key=lambda i: results[i]["turn"])[: max(0, len(aged) - k)]  # no prompt to rank against: fall back to age
        sc = _bm25(_terms(prompt), [_terms(results[i]["text"]) for i in aged])
        keep = {aged[j] for j in sorted(range(len(aged)), key=lambda j: (-sc[j], j))[:k] if sc[j] > 0}
        named = {i for i in aged if results[i]["path"] in prompt or any(w in prompt for w in re.findall(r"[a-z_]+\(\)|[\w/]+\.py", results[i]["text"])[:0])}
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
