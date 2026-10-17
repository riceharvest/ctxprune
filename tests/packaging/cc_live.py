#!/usr/bin/env python3
"""Live Claude Code check: a real `claude -p` run in a throwaway HOME against a local mock Anthropic API.

    python tests/packaging/cc_live.py --plugin-dir DIR        # the ctxprune-safe plugin (ctxprune bundle claude-code)
    python tests/packaging/cc_live.py --ctxprune EXE          # `ctxprune install claude-code` into the temp HOME

The mock answers the first agent request with a Bash tool_use (`grep -rn User src` over a generated file), then
records the tool_result Claude Code sends back on the next request: that is the text the model would really see.
Passes when it is the ctxprune-compressed form (smaller, "[ctxprune" marker), then repeats with CTXPRUNE_OFF=1 and
expects the raw grep output. Needs `claude` on PATH; uses a dummy API key, never the user's credentials or HOME.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL_ID = "toolu_ctxprune_live_1"
COMMAND = "grep -rn User src"


class Mock:
    def __init__(self):
        self.seen_results: list = []
        self.requests = 0


def _sse(handler, events: list) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    for name, data in events:
        handler.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        handler.wfile.flush()


def _message(model: str, blocks: list, stop: str) -> tuple:
    msg = {"id": "msg_mock", "type": "message", "role": "assistant", "model": model, "content": [],
           "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}
    ev = [("message_start", {"type": "message_start", "message": msg})]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "text", "text": ""}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                               "delta": {"type": "text_delta", "text": b["text"]}}))
        else:
            ev.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
            ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 5}}))
    ev.append(("message_stop", {"type": "message_stop"}))
    full = dict(msg, content=blocks, stop_reason=stop)
    return ev, full


def make_handler(mock: Mock):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if "count_tokens" in self.path:
                b = json.dumps({"input_tokens": 100}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            mock.requests += 1
            model = body.get("model", "mock")
            result = None
            for m in body.get("messages", []):
                if isinstance(m.get("content"), list):
                    for blk in m["content"]:
                        if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("tool_use_id") == TOOL_ID:
                            c = blk.get("content")
                            result = c if isinstance(c, str) else "".join(
                                x.get("text", "") for x in (c or []) if isinstance(x, dict))
            tools = [t.get("name") for t in body.get("tools", []) if isinstance(t, dict)]
            if result is not None:
                mock.seen_results.append(result)
                blocks, stop = [{"type": "text", "text": "done"}], "end_turn"
            elif "Bash" in tools:
                blocks, stop = [{"type": "tool_use", "id": TOOL_ID, "name": "Bash",
                                 "input": {"command": COMMAND, "description": "search"}}], "tool_use"
            else:   # side requests (titles, summaries): plain text
                blocks, stop = [{"type": "text", "text": "ok"}], "end_turn"
            ev, full = _message(model, blocks, stop)
            if body.get("stream"):
                _sse(self, ev)
            else:
                b = json.dumps(full).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
    return H


def run_once(claude: str, home: str, work: str, port: int, args: list, off: bool, mock: Mock) -> tuple:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR", "TERM")}
    env.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
               XDG_CACHE_HOME=os.path.join(home, ".cache"), XDG_STATE_HOME=os.path.join(home, ".local", "state"),
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}", ANTHROPIC_API_KEY="sk-ant-dummy-ctxprune-test",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1", DISABLE_TELEMETRY="1",
               DISABLE_ERROR_REPORTING="1", CLAUDE_CODE_SKIP_TOS="1")
    if off:
        env["CTXPRUNE_OFF"] = "1"
    before = len(mock.seen_results)
    p = subprocess.run([claude, "-p", "search for User in src", "--output-format", "json", "--max-turns", "3",
                        "--dangerously-skip-permissions", "--model", "claude-sonnet-4-5"] + args,
                       cwd=work, env=env, capture_output=True, text=True, timeout=240)
    got = mock.seen_results[before:]
    return p, (got[-1] if got else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-dir", default="")
    ap.add_argument("--ctxprune", default="")
    ap.add_argument("--claude", default=shutil.which("claude") or "")
    a = ap.parse_args()
    if not a.claude:
        print("SKIP: claude not on PATH")
        return 0
    mock = Mock()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mock))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    home = tempfile.mkdtemp(prefix="ctxprune-cclive-home-")
    work = tempfile.mkdtemp(prefix="ctxprune-cclive-work-")
    fails = 0
    try:
        os.makedirs(os.path.join(work, "src", "app", "views"))
        with open(os.path.join(work, "src", "app", "views", "user_views.py"), "w") as f:
            f.write("".join(f"    user = User.get({i})  # lookup {i}\n" for i in range(1, 80)))
        # decoys: Claude Code runs hooks in the project dir; none of these may run instead of ctxprune's modules
        os.makedirs(os.path.join(work, "ctxprune"))
        for rel in ("ctxprune/__init__.py", "ctxprune/__main__.py", "dedup.py", "lossless.py", "lossless_hook.py",
                    "glue.py", "ctxprune_core.py"):
            with open(os.path.join(work, *rel.split("/")), "w") as f:
                f.write("raise SystemExit(3)\n")
        os.makedirs(os.path.join(home, ".claude"))
        with open(os.path.join(home, ".claude.json"), "w") as f:   # skip onboarding in the fake HOME
            json.dump({"hasCompletedOnboarding": True, "projects": {work: {"hasTrustDialogAccepted": True}}}, f)
        args = []
        if a.plugin_dir:
            args = ["--plugin-dir", os.path.abspath(a.plugin_dir)]
            label = "ctxprune-safe plugin (--plugin-dir)"
        elif a.ctxprune:
            env = dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
                       XDG_CONFIG_HOME=os.path.join(home, ".config"))
            subprocess.run([a.ctxprune, "install", "claude-code"], env=env, check=True, capture_output=True)
            label = "ctxprune install claude-code (settings.json hook)"
        else:
            print("pass --plugin-dir or --ctxprune")
            return 2
        raw = subprocess.run(["grep", "-rn", "User", "src"], cwd=work, capture_output=True, text=True).stdout
        p, seen = run_once(a.claude, home, work, port, args, False, mock)
        ok = isinstance(seen, str) and "[ctxprune" in seen and len(seen) < len(raw)
        print(("ok   " if ok else "FAIL ") + f"{label}: the model received {len(seen or '')} chars for a "
              f"{len(raw)}-char grep output" + ("" if ok else f"\n  claude exit {p.returncode}: {p.stdout[-400:]} "
                                                          f"{p.stderr[-400:]}\n  seen: {str(seen)[:300]!r}"))
        fails += not ok
        p, seen = run_once(a.claude, home, work, port, args, True, mock)
        ok = isinstance(seen, str) and "[ctxprune" not in seen and seen.startswith(raw.rstrip("\n"))   # CC may append a reminder
        print(("ok   " if ok else "FAIL ") + f"{label}: CTXPRUNE_OFF=1 -> the model received the raw output"
              + ("" if ok else f" (claude exit {p.returncode}; seen {len(seen or '')} chars, tail {str(seen)[-300:]!r}, "
                               f"raw tail {raw[-120:]!r})"))
        fails += not ok
    finally:
        srv.shutdown()
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
