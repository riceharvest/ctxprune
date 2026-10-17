#!/usr/bin/env python3
"""Non-ASCII hook I/O through the INSTALLED Claude Code hook under non-UTF-8 code pages.

    python tests/packaging/codepage_hook.py        (run with the venv's python that has ctxprune installed)

Claude Code (Node) writes the PostToolUse event to the hook's stdin as UTF-8. On Windows, Python decodes a piped
stdin with the ANSI code page unless it runs in UTF-8 mode, so a hook that reads text stdin turned `café` into
`cafÃ©` (cp1252) or failed open (cp936 / cp932). This runs the exact command `ctxprune install claude-code` writes
(`<python> <plugin_cli.py> hook claude-code`) with:
  - the machine's own code page (PYTHONIOENCODING / PYTHONUTF8 removed; cp1252 on GitHub's Windows runners),
  - PYTHONIOENCODING=cp1252 / cp936 / cp932 as stand-ins for those Windows code pages,
  - on Windows also through cmd.exe after `chcp 936` / `chcp 65001`,
and checks the replacement decodes back to the exact original text (café, 日本語, 中文, Привет, CRLF).
"""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys

import ctxprune
from ctxprune.installers import plugin_cli

sys.path.insert(0, os.path.join(os.path.dirname(ctxprune.__file__), "_rt"))
from lossless import decode  # noqa: E402

TEXT = "\r\n".join(f"src\\app\\módulo\\usuário_{k // 8}.py:{k}:    print('café 日本語 中文 Привет {k}')"
                   for k in range(1, 40))
EV = {"session_id": "cp", "transcript_path": "", "hook_event_name": "PostToolUse", "tool_name": "PowerShell",
      "tool_input": {"command": "Select-String -Path src\\*.py -Pattern café"},
      "tool_response": {"stdout": TEXT, "stderr": "", "interrupted": False, "isImage": False}}
RAW = json.dumps(EV, ensure_ascii=False).encode("utf-8")   # what Node writes
CMD = [sys.executable, plugin_cli(), "hook", "claude-code"]

fails = 0


def check(label: str, p: subprocess.CompletedProcess) -> None:
    global fails
    try:
        new = json.loads(p.stdout.decode("utf-8"))["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        ok = len(new) < len(TEXT) and decode(new) == TEXT
        why = "" if ok else f"decoded text differs ({new[:120]!r})"
    except Exception as e:  # noqa: BLE001
        ok, why = False, f"{e!r}; stdout {p.stdout[:200]!r}; stderr {p.stderr[-300:]!r}"
    fails += not ok
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else ": " + why), flush=True)


def base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env.update(CTXPRUNE_STATS="off", CTXPRUNE_DEDUP="0")
    return env


def main() -> int:
    env = base_env()
    probe = subprocess.run([sys.executable, "-c", "import locale, sys; print(locale.getpreferredencoding(False), "
                            "sys.stdin.encoding)"], input=b"", capture_output=True, env=env)
    print(f"platform {sys.platform}; child stdin code page: {probe.stdout.decode(errors='replace').strip()} "
          f"(parent {locale.getpreferredencoding(False)})", flush=True)
    check("machine code page (no PYTHONIOENCODING / PYTHONUTF8)", subprocess.run(CMD, input=RAW, capture_output=True,
                                                                                env=env, timeout=60))
    for enc in ("cp1252", "cp936", "cp932"):
        check(f"PYTHONIOENCODING={enc}", subprocess.run(CMD, input=RAW, capture_output=True, timeout=60,
                                                         env=dict(env, PYTHONIOENCODING=enc)))
    if os.name == "nt":
        line = subprocess.list2cmdline(CMD)
        for cp in ("936", "65001"):
            check(f"cmd.exe after chcp {cp}", subprocess.run(f"chcp {cp} >NUL & {line}", shell=True, input=RAW,
                                                             capture_output=True, env=env, timeout=60))
    print(f"{fails} failed", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
