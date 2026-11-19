"""`ctxprune mcp`: a stdio MCP server for clients without tool-output hooks (Claude Desktop, Cursor, Cline, ...).

Two read tools, so a big log, JSON dump or report enters the context already shortened:
  read_compact  the lossless `safe` pipeline (the same bytes the agent hooks produce); nothing is dropped
  read_pruned   opt-in token pruning with the v0.4 neural model (lossy); identifiers & query evidence prioritized

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
                        "gist matters more than every word. Pass `query` to focus the budget on relevant spans."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to read."},
                "keep": {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "default": 0.5,
                         "description": "Share of tokens to keep (0-1]."},
                "protect": {
                    "anyOf": [
                        {"type": "boolean"},
                        {"type": "string", "enum": ["auto", "ids"]},
                    ],
                    "default": "auto",
                    "description": "Identifier protection: 'auto' (smart technical vs prose), 'ids', true, or false.",
                },
                "query": {"type": "string", "description": "Optional question/topic to prioritize relevant spans."},
                "mode": {
                    "type": "string",
                    "enum": ["prune", "auto"],
                    "default": "prune",
                    "description": "'prune' (neural) or 'auto' (lossless first; JSON-values-only for valid JSON).",
                },
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "read_skeleton",
        "title": "Read source signatures and exact line ranges",
        "description": ("Read a source file as an outline of imports, classes, functions and type declarations with "
                        "exact 1-based source line ranges. Opt-in navigation aid; use read_compact for non-code."),
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "read_symbols",
        "title": "Read query-ranked source symbols",
        "description": ("Find symbols relevant to a query using lexical BM25 ranking and return the top matching "
                        "definitions verbatim with exact line ranges. Read-only; results are source code."),
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5}},
            "required": ["path", "query"]},
        "name": "search_saved",
        "title": "Search a saved output",
        "description": "Search a saved or spilled text output by its file path handle and return matching snippets.",
        "inputSchema": {"type": "object", "properties": {
            "handle": {"type": "string", "description": "Path to the saved output (the handle shown by the spill)."},
            "query": {"type": "string"},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3}},
            "required": ["handle", "query"]},
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


def _pruned(text: str, keep: float, protect: bool | str, query: str | None = None,
            mode: str = "prune") -> tuple[str, str]:
    from . import prune
    out = prune(text, keep=keep, protect=protect, query=query, mode=mode, unit="tokens_est")  # v0.4 engine
    pnote = f", protect={protect}" if protect is not False else ""
    qnote = ", query-aware" if query else ""
    return out, (f"[ctxprune: {len(text):,} -> {len(out):,} chars, token-pruned, target {keep:.0%} of tokens"
                 f"{pnote}{qnote} (lossy)]")


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "search_saved":
            from .search_saved import search_saved
            hits = search_saved(str(args.get("handle", "")), str(args.get("query", "")),
                                int(args.get("context_lines", 3)))
            return {"content": [{"type": "text", "text": json.dumps(hits, ensure_ascii=True)}], "isError": False}
        text = _read(args)
        if name == "read_compact":
            out, note = _compact(text)
        elif name in ("read_skeleton", "read_symbols"):
            from . import code_reader
            path = os.path.expanduser(str(args.get("path", "")))
            if name == "read_skeleton":
                out = code_reader.read_skeleton(path, text)
            else:
                out = code_reader.read_symbols(path, text, str(args.get("query", "")), int(args.get("limit", 5)))
            note = f"[ctxprune: source outline; {len(text.splitlines())} source lines]"
        elif name == "read_pruned":
            keep = float(args.get("keep", 0.5))
            if not 0 < keep <= 1:
                raise ValueError("keep must be in (0, 1]")
            prot = args.get("protect", "auto")
            if isinstance(prot, str) and prot not in ("auto", "ids"):
                low = prot.lower()
                prot = True if low in ("true", "1", "yes") else (False if low in ("false", "0", "no") else "auto")
            query = args.get("query")
            query = str(query) if query is not None and str(query).strip() else None
            mode = str(args.get("mode", "prune"))
            if mode not in ("prune", "auto", "json"):
                raise ValueError("mode must be 'prune', 'auto', or 'json'")
            out, note = _pruned(text, keep, prot, query=query, mode=mode)
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
