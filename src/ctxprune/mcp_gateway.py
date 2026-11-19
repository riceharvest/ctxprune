"""Local stdio MCP meta-tool gateway for configured stdio servers.

Backends are declared in a JSON object mapping server names to command arrays:
{"github":{"command":["npx","-y","@example/mcp"]}}.  Tool names exposed
to clients are qualified as ``server/tool`` to avoid collisions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any

PROTOCOL = "2025-06-18"
TOOLS = [
    {"name": "list_tools", "description": "List backend tools (server/tool: summary). Optional server filter.",
     "inputSchema": {"type": "object", "properties": {"server": {"type": "string"}}}},
    {"name": "describe_tool", "description": "Full schema of one server/tool.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "call_tool", "description": "Call server/tool with args (describe_tool first if unsure).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "args": {"type": "object"}}, "required": ["name"]}},
]


class Gateway:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.procs: dict[str, subprocess.Popen[str]] = {}
        self.schemas: dict[str, dict[str, dict]] = {}
        self._id = 1
        self._lock = threading.Lock()

    def start(self) -> None:
        for server, spec in self.config.items():
            command = spec.get("command") if isinstance(spec, dict) else None
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError(f"server {server!r} requires a non-empty command string array")
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=None, text=True, bufsize=1, env={**os.environ, **spec.get("env", {})})
            self.procs[server] = proc
            self._request(server, "initialize", {"protocolVersion": PROTOCOL,
                "capabilities": {}, "clientInfo": {"name": "ctxprune-gateway", "version": "0.5"}})
            self._notify(server, "notifications/initialized", {})
            reply = self._request(server, "tools/list", {})
            tools = (reply.get("tools") or [])
            self.schemas[server] = {str(t["name"]): t for t in tools if isinstance(t, dict) and t.get("name")}

    def close(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _request(self, server: str, method: str, params: dict) -> dict:
        proc = self.procs[server]
        if proc.poll() is not None:
            raise RuntimeError(f"backend {server!r} exited with status {proc.returncode}")
        with self._lock:
            mid = self._id
            self._id += 1
            assert proc.stdin and proc.stdout
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"backend {server!r} closed stdout")
                msg = json.loads(line)
                if msg.get("id") != mid:
                    continue
                if "error" in msg:
                    raise RuntimeError(str(msg["error"].get("message", msg["error"])))
                return msg.get("result") or {}

    def _notify(self, server: str, method: str, params: dict) -> None:
        proc = self.procs[server]
        assert proc.stdin
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        proc.stdin.flush()

    def call(self, name: str, args: dict) -> dict:
        if name == "list_tools":
            server = args.get("server")
            if server is not None and server not in self.schemas:
                raise ValueError(f"unknown server {server!r}")
            lines = [f"{key}/{tn}: {_summary(t)}" for key, tools in self.schemas.items()
                     if server is None or key == server for tn, t in tools.items()]
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        if name == "describe_tool":
            server, tool_name = self._split(str(args.get("name", "")))
            try:
                tool = self.schemas[server][tool_name]
                return {"content": [{"type": "text", "text": json.dumps({**tool, "name": f"{server}/{tool_name}"})}]}
            except KeyError:
                raise ValueError(f"unknown tool {args.get('name')!r}") from None
        if name == "call_tool":
            server, tool_name = self._split(str(args.get("name", "")))
            if tool_name not in self.schemas.get(server, {}):
                raise ValueError(f"unknown tool {args.get('name')!r}")
            result = self._request(server, "tools/call", {"name": tool_name, "arguments": args.get("args") or {}})
            return _compact_result(result)
        raise ValueError(f"unknown gateway tool {name!r}")

    def _split(self, name: str) -> tuple[str, str]:
        if "/" not in name:
            raise ValueError("tool name must be server/tool; call list_tools first")
        return tuple(name.split("/", 1))  # type: ignore[return-value]


def _summary(tool: dict) -> str:
    d = " ".join(str(tool.get("description") or tool.get("title") or "").split())
    cut = d.find(". ")
    d = d[:cut + 1] if 0 < cut < 120 else d[:120]
    req = (tool.get("inputSchema") or {}).get("required") or []
    return f"{d} ({', '.join(req)})" if req else d


def _compact_result(result: dict) -> dict:
    # Never alter structuredContent independently from content. Text-only MCP results
    # can use the same deterministic, lossless safe pipeline as ctxprune mcp-wrap.
    if result.get("structuredContent") is not None:
        return result
    try:
        from . import glue
        glue.boot()
        import ctxprune_compress
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                item["text"], _ = ctxprune_compress.run(item["text"], command="", harness="mcp")
    except Exception:
        pass
    return result


def handle(msg: dict, gateway: Gateway) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "ctxprune-mcp-gateway", "version": "0.5"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            result = gateway.call(str(p.get("name", "")), p.get("arguments") or {})
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"ctxprune gateway: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ctxprune mcp-gateway")
    ap.add_argument("--config", default=os.environ.get("CTXPRUNE_MCP_GATEWAY_CONFIG"),
                    help="JSON config mapping backend names to {command: [...], env: {...}}")
    args = ap.parse_args(argv)
    if not args.config:
        ap.error("--config is required (or set CTXPRUNE_MCP_GATEWAY_CONFIG)")
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict) or not config:
        ap.error("config must be a non-empty JSON object")
    gateway = Gateway(config)
    try:
        gateway.start()
        for line in sys.stdin:
            try:
                msg = json.loads(line)
                msgs = msg if isinstance(msg, list) else [msg]
                for reply in (handle(m, gateway) for m in msgs if isinstance(m, dict)):
                    if reply is not None:
                        sys.stdout.write(json.dumps(reply) + "\n")
                sys.stdout.flush()
            except Exception:
                continue
    finally:
        gateway.close()
    return 0
