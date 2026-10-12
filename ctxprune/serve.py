"""Small local HTTP API so other processes (the Claude Code hook, Node/Go/shell tools) skip the model load.

    ctxprune serve                    # http://127.0.0.1:8787
    curl -s localhost:8787/compress -d '{"text": "...", "rate": 0.5}'

POST /compress  {"text": str} or {"texts": [str]}, optional "rate" (0.5), "force_protected" (false),
                "keep_structure" (false)  ->  {"text", "kept", "total"} or {"results": [...]}
GET  /health    ->  {"ok": true, "model": ...}

Binds to localhost only by default. Results are cached by input, so repeated tool outputs are free.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .compress import DEFAULT_MODEL, INT8, Compressor

MAX_BODY = 32 * 1024 * 1024


class _State:
    def __init__(self, model: str, onnx_file: str, cache_size: int = 512):
        self.model = model
        self.c = Compressor(model, backend="onnx", onnx_file=onnx_file)
        self.lock = threading.Lock()
        self.cache: OrderedDict = OrderedDict()
        self.cache_size = cache_size

    def compress(self, text: str, rate: float, force_protected: bool, keep_structure: bool) -> dict:
        key = (text, rate, force_protected, keep_structure)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            out = self.c.compress(text, rate=rate, force_protected=force_protected, keep_structure=keep_structure)
            self.cache[key] = out
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            return out


def make_server(host: str = "127.0.0.1", port: int = 8787, model: str = DEFAULT_MODEL,
                onnx_file: str = INT8) -> ThreadingHTTPServer:
    state = _State(model, onnx_file)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/health"):
                self._send(200, {"ok": True, "model": state.model})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/compress":
                return self._send(404, {"error": "POST /compress"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                rate = float(req.get("rate", 0.5))
                opts = (bool(req.get("force_protected", False)), bool(req.get("keep_structure", False)))
                if "texts" in req:
                    return self._send(200, {"results": [state.compress(str(t), rate, *opts) for t in req["texts"]]})
                if "text" in req:
                    return self._send(200, state.compress(str(req["text"]), rate, *opts))
                return self._send(400, {"error": 'send {"text": ...} or {"texts": [...]}'})
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})

        def log_message(self, fmt, *args):  # quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(host: str, port: int, model: str, onnx_file: str) -> None:
    srv = make_server(host, port, model, onnx_file)
    print(f"ctxprune serving {model} on http://{host}:{port}  (POST /compress)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
