#!/usr/bin/env python3
"""Run a PostToolUse hook command and log what it did (one JSONL line per call), passing its stdout through.

Why: Claude Code silently ignores a hook that is missing, crashes or returns the wrong shape, which would turn a
compression arm into the default arm without any sign. run_episode.sh wraps every hook arm with this, and
summarize.py marks a hook arm episode with zero logged calls (or only errors) as invalid.

Usage (settings.json command): python3 hook_wrap.py --log EP/hook.jsonl -- <hook command ...>
Log line: {ts, rc, ms, tool, keys, before, after, action: updated|passthrough|bad_shape|error|timeout}
          (+ mode for Grep; persisted, persisted_size, ptr_kept for Bash output Claude Code persisted to a file).
          The text field is `content` for the Grep tool, `stdout` otherwise.
Fails open: on any error nothing is printed, so Claude Code keeps the original output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def main() -> None:
    argv = sys.argv[1:]
    log = ""
    if argv[:1] == ["--log"]:
        log, argv = argv[1], argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    raw = sys.stdin.buffer.read()
    rec: dict = {"ts": time.time(), "cmd": argv[0] if argv else ""}
    t0 = time.time()
    try:
        data = json.loads(raw)
        rec["tool"] = data.get("tool_name")
        resp = data.get("tool_response")
        field = "content" if rec["tool"] == "Grep" else "stdout"
        if isinstance(resp, dict):
            rec["keys"] = sorted(resp)                      # evidence for the tool's output schema on this version
            rec["before"] = len(resp.get(field) or "") if isinstance(resp.get(field) or "", str) else None
            if resp.get("persistedOutputPath"):
                rec["persisted"], rec["persisted_size"] = True, resp.get("persistedOutputSize")
                pp = str(resp["persistedOutputPath"])       # diagnostics: why a persisted output passed through
                rec["preview_head"] = str(resp.get(field) or "")[:200]
                rec["file_exists"] = os.path.isfile(pp)
                if rec["file_exists"]:
                    rec["file_size"] = os.path.getsize(pp)
                    with open(pp, "rb") as fh:
                        rec["file_head"] = fh.read(400).decode("utf-8", "replace")[:200]
            if rec["tool"] == "Grep":
                rec["mode"] = resp.get("mode")
        else:
            rec["before"] = None
    except Exception:
        data, field, resp = None, "stdout", None
    out = b""
    try:
        p = subprocess.run(argv, input=raw, capture_output=True, timeout=110)
        rec["rc"] = p.returncode
        out = p.stdout
        if p.returncode != 0:
            rec["action"], rec["stderr"] = "error", p.stderr.decode("utf-8", "replace")[-300:]
            out = b""
        elif not out.strip():
            rec["action"] = "passthrough"
        else:
            j = json.loads(out)
            upd = (j.get("hookSpecificOutput") or {}).get("updatedToolOutput")
            if isinstance(upd, dict) and isinstance(upd.get(field), str):
                rec["action"], rec["after"] = "updated", len(upd[field])
                if isinstance(resp, dict) and resp.get("persistedOutputPath"):
                    rec["ptr_kept"] = "persistedOutputPath" in upd
            else:
                rec["action"] = "bad_shape"       # e.g. a bare string: Claude Code would ignore it
    except subprocess.TimeoutExpired:
        rec["action"], out = "timeout", b""
    except Exception as e:  # noqa: BLE001
        rec["action"], rec["stderr"], out = "error", repr(e)[-300:], b""
    rec["ms"] = round((time.time() - t0) * 1000)
    if out:
        sys.stdout.buffer.write(out)
    if log:
        try:
            with open(log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass


if __name__ == "__main__":
    main()
