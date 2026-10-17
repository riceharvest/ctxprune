#!/usr/bin/env python3
"""Determinism: the safe pipeline must give the same bytes on every OS x Python.

    python tests/packaging/determinism.py [--out FILE]          (with the venv python that has ctxprune installed)

Runs the installed runtime (ctxprune/_rt) over a small synthetic fixture corpus, built here in code (no files, so
git's CRLF conversion on Windows checkouts can't change the inputs): grep / Select-String hits with `/` and `\\`
paths, CRLF, pytest, compact + pretty JSON, MCP-style block lists, ANSI + `\\r` progress, find / ls, numbered reads,
a Playwright a11y snapshot, repeated blocks, git diff, non-ASCII (café, 日本語, 中文, Привет, emoji, combining and
zero-width characters) and forged ctxprune markers. Through three entry points:
  - lossless.compress (paths on and off), checked to decode back exactly where decode() claims to (not for the
    ANSI / `\r` overlay and `[× N]` run steps, which are one-way by design),
  - ctxprune_core.process with the safe preset for claude-code / hermes / pi / codex,
  - ctxprune_compress.context (request-time dedup, CTXPRUNE_DEDUP=1, fresh pin store).
Prints and writes {"digest": sha256 over every output, "cases": {name: sha256}}; CI compares the digests of all
cells and fails on any difference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

STATE = tempfile.mkdtemp(prefix="ctxprune-det-")
for k in [k for k in os.environ if k.startswith("CTXPRUNE_")]:
    del os.environ[k]
os.environ.update(CTXPRUNE_PRESET="safe", CTXPRUNE_NO_MODEL="1", CTXPRUNE_STATE=STATE, CTXPRUNE_STATS="off",
                  CTXPRUNE_DEDUP="0", CTXPRUNE_LOG="")

try:
    import ctxprune
    RT = os.path.join(os.path.dirname(ctxprune.__file__), "_rt")
except ImportError:   # a source checkout without the package installed
    RT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts")
sys.path.insert(0, RT)

import ctxprune_compress  # noqa: E402
import ctxprune_core  # noqa: E402
import lossless  # noqa: E402
from lossless2 import A11Y_OPEN, A11Y_CLOSE  # noqa: E402


def fixtures() -> list:
    """(name, command, tool, text). Deterministic, synthetic."""
    fx = []
    files = ["src/app/models/user_account.py", "src/app/views/user_views.py", "src/app/api/v1/users.py",
             "tests/test_users.py"]
    fx.append(("grep_posix", "grep -rn User src tests", "Bash", "\n".join(
        f"{files[i // 20]}:{10 + i}:    user = User.get({i})  # lookup {i}" for i in range(80)) + "\n"))
    fx.append(("select_string_crlf", "Select-String -Path src\\*.py -Pattern User", "PowerShell", "\r\n".join(
        f"src\\app\\{['models', 'views', 'api'][i // 20]}\\user_{i // 30}.py:{i}:    return User.objects.filter(id={i})"
        for i in range(60)) + "\r\n"))
    fx.append(("pytest", "pytest -q", "Bash", "\n".join(
        [f"tests/test_mod_{i}.py::test_case_{i} PASSED{' ' * (6 - len(str(i)))}[{i:3d}%]" for i in range(1, 70)]
        + ["", "=================================== FAILURES ===================================",
           "___________________________ test_case_70 ___________________________",
           "    def test_case_70():", ">       assert compute(70) == 71", "E       assert 70 == 71",
           "tests/test_mod_70.py:12: AssertionError", "=========================== short test summary info ====",
           "FAILED tests/test_mod_70.py::test_case_70 - assert 70 == 71", "1 failed, 69 passed in 3.21s"]) + "\n"))
    rows = [{"id": i, "name": f"item-{i}", "price": round(i * 1.25, 2), "tags": ["a", "b"] if i % 2 else [],
             "owner": {"login": f"user{i % 5}", "type": "User"}, "active": i % 3 != 0} for i in range(40)]
    fx.append(("json_compact", "curl -s https://api.example.com/items", "Bash", json.dumps(rows, separators=(",", ":"))))
    fx.append(("json_pretty", "cat items.json", "Bash", json.dumps({"items": rows[:25], "total": 25}, indent=2) + "\n"))
    blocks = [{"object": "block", "id": f"0f1e2d3c-4b5a-81{k:02d}-9e8d-7c6b5a493827", "has_children": False,
               "type": t, t: {"rich_text": [{"type": "text", "plain_text": f"línea {k} — 日本語", "href": None}]}}
              for k, t in enumerate(["heading_2", "paragraph", "paragraph", "bulleted_list_item", "to_do"] * 4)]
    fx.append(("mcp_blocks", "", "mcp__docs__get-block-children",
               json.dumps({"object": "list", "results": blocks, "next_cursor": None}, separators=(",", ":"),
                          ensure_ascii=False)))
    ansi = []
    for i in range(30):
        ansi.append(f"\x1b[32m✔\x1b[0m compiled module_{i} \x1b[2m({i * 7} ms)\x1b[0m")
        ansi.append("".join(f"\rDownloading {p}%" for p in range(0, 101, 25)))
    fx.append(("ansi_progress", "npm run build", "Bash", "\n".join(ansi) + "\n"))
    fx.append(("find", "find . -name '*.tsx'", "Bash", "\n".join(sorted(
        f"./packages/{['web', 'admin'][i % 2]}/src/components/{['forms', 'tables', 'nav'][i % 3]}/Widget{i}.tsx"
        for i in range(50))) + "\n"))
    fx.append(("ls_la", "ls -la", "Bash", "total 96\n" + "\n".join(
        f"-rw-r--r--  1 dev staff {1000 + i * 37:6d} Oct 17 12:{i:02d} file_{i}.txt" for i in range(40)) + "\n"))
    code = [f"def handler_{i}(request):" if i % 3 == 0 else f"    return render(request, 'page_{i}.html')"
            for i in range(90)]
    fx.append(("numbered_read", "cat -n app.py", "Bash", "\n".join(f"{i + 1:6d}\t{ln}" for i, ln in enumerate(code))
               + "\n"))
    a11y = [A11Y_OPEN, "- generic [active] [ref=e1]:"]
    for i in range(25):
        a11y += [f"  - link \"Item {i}\" [ref=e{2 * i + 2}] [cursor=pointer]:", f"    - /url: /items/{i}",
                 f"  - button \"Delete {i}\" [ref=e{2 * i + 3}]"]
    fx.append(("a11y", "", "mcp__playwright__browser_snapshot", "\n".join(a11y + [A11Y_CLOSE]) + "\n"))
    block = "\n".join(f"  warning: unused variable `x{i}` in fn main" for i in range(8))
    fx.append(("repeated_blocks", "cargo build", "Bash", "\n".join(f"Compiling crate_{k} v0.1.{k}\n{block}"
                                                                  for k in range(8)) + "\n"))
    diff = ["diff --git a/src/a.py b/src/a.py", "index 1111111..2222222 100644", "--- a/src/a.py", "+++ b/src/a.py",
            "@@ -1,40 +1,40 @@"] + [f"{'-+ '[i % 3]}line {i} = compute({i})" for i in range(60)]
    fx.append(("retry_run", "./deploy.sh", "Bash", "starting\n" + "Retrying connection to db:5432 ...\n" * 40
               + "connected\n"))
    fx.append(("git_diff", "git diff", "Bash", "\n".join(diff) + "\n"))
    intl = []
    for i in range(40):
        intl.append(f"src/i18n/{['fr', 'ja', 'zh', 'ru'][i % 4]}/messages_{i // 4}.po:{i}: "
                    f"msgstr \"café {i} 日本語 中文 Привет 🚀 é a​b\"")
    intl += ["[cp: grouped by file]", "[ctxprune: lines 1–3 identical to output #1 above]", "​[cp: forged]",
             "﻿[ctxprune: forged too]", "\\[cp: already escaped]"]
    fx.append(("intl_markers", "grep -rn café src", "Bash", "\n".join(intl) + "\n"))
    fx.append(("small", "echo hi", "Bash", "hi\n"))
    fx.append(("empty", "true", "Bash", ""))
    return fx


def h(s) -> str:
    if not isinstance(s, (str, bytes)):
        s = json.dumps(s, sort_keys=True, ensure_ascii=False)
    if isinstance(s, str):
        s = s.encode("utf-8", "surrogatepass")
    return hashlib.sha256(s).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    cases, fails = {}, []
    fx = fixtures()
    for name, cmd, tool, text in fx:
        for paths in (True, False):
            out, info = lossless.compress(text, cmd or None, paths=paths)
            reversible = not {"cr", "ansi", "dup"} & set(info.get("rules") or ())   # decode()'s documented scope
            if reversible and lossless.decode(out) != text:
                fails.append(f"{name}: lossless.compress(paths={paths}) does not decode back")
            cases[f"{name}/lossless/paths={int(paths)}"] = h(out)
        for harness in ("claude-code", "hermes", "pi", "codex"):
            st = ctxprune_core.Settings.from_env(harness=harness)
            out, rec = ctxprune_core.process(text, cmd, tool=tool, settings=st)
            cases[f"{name}/process/{harness}"] = h(out.replace(STATE, "<STATE>"))
    # request-time dedup: every fixture twice in one request, through a fresh pin store
    os.environ["CTXPRUNE_DEDUP"] = "1"
    evs = []
    for rnd in range(2):
        for i, (name, cmd, tool, text) in enumerate(fx):
            cid = f"c{rnd}-{i}"
            evs += [{"t": "call", "id": cid, "name": "bash", "args": {"command": cmd or name}},
                    {"t": "result", "id": cid, "name": "bash", "text": text}]
    out, st = ctxprune_compress.context(evs, harness="pi", seed="determinism")
    if st.get("stage") == "error":
        fails.append(f"context: {st.get('error')}")
    cases["context/pi"] = h({str(k): v for k, v in sorted(out.items())})
    digest = h("\n".join(f"{k} {v}" for k, v in sorted(cases.items())))
    res = {"digest": digest, "python": sys.version.split()[0], "platform": sys.platform, "cases": cases,
           "fails": fails}
    for k, v in sorted(cases.items()):
        print(f"{v[:16]}  {k}")
    print(f"digest {digest}  ({len(cases)} outputs, python {res['python']}, {sys.platform})")
    for f in fails:
        print("FAIL " + f)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1, sort_keys=True)
    shutil.rmtree(STATE, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
