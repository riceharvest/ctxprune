"""`ctxprune mcp`: a stdio MCP server for clients without tool-output hooks (Claude Desktop, Cursor, Cline, ...).

Two read tools, so a big log, JSON dump or report enters the context already shortened:
  read_compact  the lossless `safe` pipeline (the same bytes the agent hooks produce); nothing is dropped
  read_pruned   opt-in token pruning with the 0.2 model (lossy); identifiers, numbers and paths kept by default

Stdlib JSON-RPC 2.0 over stdin/stdout (one message per line), protocol 2025-06-18; no MCP SDK dependency.
Local only: it reads the files the client asks for and makes no network calls (read_pruned downloads the model from
Hugging Face once, on first use).
"""
from __future__ import annotations

import json
import os
import sys

from . import __version__

PROTOCOL = "2025-06-18"
MAX_BYTES = int(os.environ.get("CTXPRUNE_MCP_MAX_BYTES", str(20 * 1024 * 1024)))

TOOLS = [
    {
        "name": "read_compact",
        "title": "Read a file, losslessly compacted",
        "description": ("Read a text file (log, JSON/MCP dump, CSV, test output, report) and return it rewritten into a "
                        "shorter form that keeps every value: runs of identical lines counted, JSON record lists as "
                        "rows, escaped JSON shown as text, terminal colour codes and progress redraws removed. Use it "
                        "instead of a plain read for big machine-generated files. Not for source files you will edit."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to read."},
                "start_line": {"type": "integer", "minimum": 1, "description": "First line to read (1-based)."},
                "end_line": {"type": "integer", "minimum": 1, "description": "Last line to read (inclusive)."},
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "read_pruned",
        "title": "Read a file, token-pruned (lossy)",
        "description": ("Read a text file and delete its least useful words with a small local model, keeping about "
                        "`keep` of its tokens. Lossy: use it for long prose, retrieved documents or logs when the "
                        "gist matters more than every word. Identifiers, numbers and paths are kept unless "
                        "protect=false."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to read."},
                "keep": {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "default": 0.5,
                         "description": "Share of tokens to keep (0-1]."},
                "protect": {"type": "boolean", "default": True,
                            "description": "Never drop identifiers, numbers or paths."},
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _read(args: dict) -> str:
    path = os.path.expanduser(str(args.get("path", "")))
    if not path:
        raise ValueError("path is required")
    if not os.path.isfile(path):
        raise ValueError(f"not a file: {path}")
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError(f"file is over {MAX_BYTES} bytes (CTXPRUNE_MCP_MAX_BYTES)")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "surrogateescape")
    a, b = args.get("start_line"), args.get("end_line")
    if a or b:
        lines = text.splitlines(keepends=True)
        text = "".join(lines[(int(a) - 1 if a else 0):(int(b) if b else len(lines))])
    return text


def _compact(text: str) -> tuple[str, str]:
    from . import glue
    glue.boot()
    import ctxprune_compress
    out, rec = ctxprune_compress.run(text, command="", harness="mcp")
    return out, f"[ctxprune: {len(text):,} -> {len(out):,} chars, lossless]"


def _pruned(text: str, keep: float, protect: bool) -> tuple[str, str]:
    from . import prune
    out = prune(text, keep=keep, protect=protect)
    return out, (f"[ctxprune: {len(text):,} -> {len(out):,} chars, token-pruned, target {keep:.0%} of tokens"
                 f"{', identifiers kept' if protect else ''} (lossy)]")


def call_tool(name: str, args: dict) -> dict:
    try:
        text = _read(args)
        if name == "read_compact":
            out, note = _compact(text)
        elif name == "read_pruned":
            keep = float(args.get("keep", 0.5))
            if not 0 < keep <= 1:
                raise ValueError("keep must be in (0, 1]")
            out, note = _pruned(text, keep, bool(args.get("protect", True)))
        else:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        return {"content": [{"type": "text", "text": out}, {"type": "text", "text": note}], "isError": False}
    except Exception as e:   # tool errors go back to the model, not as protocol errors
        return {"content": [{"type": "text", "text": f"ctxprune: {e}"}], "isError": True}


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:   # notification (initialized, cancelled, ...)
        return None
    if method == "initialize":
        req = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": req if isinstance(req, str) else PROTOCOL,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "ctxprune", "title": "ctxprune", "version": __version__},
                  "instructions": "Use read_compact for big logs, JSON/MCP dumps and test output: it is lossless."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        result = call_tool(str(p.get("name")), p.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv: list | None = None) -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        replies = [r for r in (handle(m) for m in msgs if isinstance(m, dict)) if r is not None]
        for r in replies:
            out.write(json.dumps(r) + "\n")
        out.flush()
    return 0
