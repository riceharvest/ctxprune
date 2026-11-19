"""ctxprune mcp-wrap -- <any stdio MCP server command>: a transparent stdio proxy that applies the lossless
transforms (ctxprune's `safe` pipeline, no model, no network) to tools/call results at the MCP boundary.

Point any MCP client (Cursor, Claude Desktop, Cline, Windsurf, Codex, Claude Code) at
    {"command": "ctxprune", "args": ["mcp-wrap", "--", "npx", "@playwright/mcp@latest"]}
instead of the server itself. Every other message passes through byte-for-byte.

Guardrails: only `result.content[*].text` blocks of tools/call responses are rewritten; `structuredContent`, isError
results, and results that carry structuredContent (unless --structured) are left alone; file read/write/edit tools
(lossless_hook.MCP_SKIP) are skipped; a rewrite is kept only if lossless.decode() gives the original text back and
it is smaller. Any exception forwards the original line unchanged (fail open). CTXPRUNE_OFF=1 disables it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

MIN_CHARS = 400
_STATS = {"before": 0, "after": 0, "calls": 0, "rewritten": 0}


def _load():
    from . import glue
    glue.boot(own_process=False)
    import lossless
    import lossless_hook
    return glue, lossless, lossless_hook


def _pairs(x):
    return json.loads(x, object_pairs_hook=lambda kv: kv)


def _verified(lossless, text: str, new: str, rules: list) -> bool:
    """Exact round trip (lossless.decode), or, when json_compact fired (whitespace only), decode() gives back JSON that
    parses to the same key/value pairs in the same order, duplicates kept (the research harness's semantic_ok)."""
    try:
        back = lossless.decode(new)
        if back == text:
            return True
        if "json_compact" in rules:
            return _pairs(back.strip()) == _pairs(text.strip())
    except Exception:
        pass
    return False


class Transformer:
    """Pure, testable core: feed it the client->server and server->client lines."""

    def __init__(self, structured: bool = False, min_chars: int = MIN_CHARS):
        self.structured, self.min_chars = structured, min_chars
        self.pending: dict = {}   # request id -> tool name (tools/call only)
        self._mods = None

    def mods(self):
        if self._mods is None:
            self._mods = _load()
        return self._mods

    def on_request(self, line: bytes) -> None:
        try:
            m = json.loads(line)
            if isinstance(m, dict) and m.get("method") == "tools/call" and "id" in m:
                p = m.get("params")
                self.pending[json.dumps(m["id"])] = str((p or {}).get("name", "")) if isinstance(p, dict) else ""
        except Exception:
            pass

    def on_response(self, line: bytes) -> bytes:
        try:
            return self._on_response(line)
        except Exception:
            return line   # fail open

    def _on_response(self, line: bytes) -> bytes:
        stripped = line.strip()
        if not stripped.startswith(b"{") or b'"result"' not in stripped:
            return line
        m = json.loads(stripped)
        if not isinstance(m, dict) or "id" not in m:
            return line
        key = json.dumps(m["id"])
        if key not in self.pending:
            return line
        tool = self.pending.pop(key)
        res = m.get("result")
        if not isinstance(res, dict) or not isinstance(res.get("content"), list) or res.get("isError"):
            return line
        if res.get("structuredContent") is not None and not self.structured:
            return line
        glue, lossless, hook = self.mods()
        if glue.off() or hook.MCP_SKIP.search(tool.rsplit("__", 1)[-1]):
            return line
        changed = False
        for b in res["content"]:
            if not (isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)):
                continue
            t = b["text"]
            if len(t) < self.min_chars:
                continue
            new, st = lossless.compress(t, tool, paths=False, paths_win=False, rows=True)
            if len(new) < len(t) and _verified(lossless, t, new, st.get("rules") or []):
                b["text"] = new
                changed = True
                _STATS["before"] += len(t)
                _STATS["after"] += len(new)
                glue.record({"before": len(t), "after": len(new), "stage": "lossless"}, harness="mcp-wrap", tool=tool)
        _STATS["calls"] += 1
        if not changed:
            return line
        _STATS["rewritten"] += 1
        nl = b"\n" if line.endswith(b"\n") else b""
        return json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + nl


def _pump_in(src, dst, tf: Transformer) -> None:
    try:
        for line in iter(src.readline, b""):
            tf.on_request(line)
            dst.write(line)
            dst.flush()
    except Exception:
        pass
    try:
        dst.close()
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    structured = False
    while argv and argv[0].startswith("--") and argv[0] != "--":
        if argv[0] == "--structured":
            structured = True
            argv = argv[1:]
        else:
            print("usage: ctxprune mcp-wrap [--structured] -- <mcp server command...>", file=sys.stderr)
            return 2
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print("usage: ctxprune mcp-wrap [--structured] -- <mcp server command...>", file=sys.stderr)
        return 2
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except OSError as e:
        print(f"ctxprune mcp-wrap: cannot start {argv[0]}: {e}", file=sys.stderr)
        return 127
    tf = Transformer(structured=structured)
    threading.Thread(target=_pump_in, args=(sys.stdin.buffer, child.stdin, tf), daemon=True).start()
    out = sys.stdout.buffer
    for line in iter(child.stdout.readline, b""):
        out.write(tf.on_response(line))
        out.flush()
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())
