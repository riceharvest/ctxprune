#!/usr/bin/env python3
"""Tiny local scoring server for the v2 line model, so a PostToolUse hook (one process per tool call) doesn't reload
the model every time. Loads once; POST /compress {"text", "intent", "keep"} -> {"text", "kept_chars", "ms"}.
With "want_keep": true the response adds "keep" (sorted indices into text.splitlines() of the kept lines, after
structure protection) and "n_lines", so the caller builds gap markers / recovery from indices instead of re-parsing
marker text (audit M1). Bodies over --max-body bytes (Content-Length, checked before reading) get 413 (audit L2).

Same scoring as runbook/ab/ctxprune_ab.py (_v2_scores: windows of 1024 tokens, per-line mean keep prob) and the same
selection (methods._select: whole lines kept byte-exact, gap markers). Binds 127.0.0.1 only.
The selector is --select (line | smooth | anchored, --alpha) unless the request names one: {"select": ..., "alpha": ...}
(what ctxprune_core sends for a preset); the same selection runs after torch or --onnx scoring.
Usage: python3 v2_server.py --model ~/ctxprune-v2/out/model-v2 [--device cuda] [--port 8765]
       python3 v2_server.py --self-test   (no model: random-drop scores; every selector through ctxprune_core.process)
       speed variants (PLAN §2.6 speed step): --max-len 512 --line-cap 48 (must match how the model was scored)
       speed2 students: --max-len 512 --intent-cap 64
       speed3 (PLAN §2.65; defaults off): --onnx <model_quantized.onnx> (the shipping ONNX int8 path, CPU, --threads
       intra-op; --model then only supplies the tokenizer), --round 1e-3 (D5: rank by rounded scores, ties by line
       index), --keep-errors 3 (D6: error lines + 3 following / 2 preceding frame lines ranked first, within budget).
       The response adds "score_ms" (model scoring only) next to "ms" (scoring + selection).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

SELECTS = ("line", "smooth", "anchored")
_lock = threading.Lock()
_state: dict = {}
_tl = threading.local()   # the final keep mask of the current request (recorded by _recording_keep_lines)


def _install_keep_recorder() -> None:
    """Every selector ends in methods._keep_lines(lines, keep); record the mask it renders (after protect_structure)
    per thread, without changing what it returns."""
    import methods
    if getattr(methods._keep_lines, "_ctxprune_recorder", False):
        return
    orig = methods._keep_lines

    def _recording_keep_lines(lines, keep):
        if any(l.startswith(methods._OWN_MARK) for l in lines):
            keep = methods.protect_structure(lines, keep)
        _tl.keep = [i for i, k in enumerate(keep) if k]
        return orig(lines, keep)

    _recording_keep_lines._ctxprune_recorder = True
    methods._keep_lines = _recording_keep_lines


def scores(text: str, intent: str) -> list[float]:
    if _state.get("random_drop"):   # positive control (PLAN §2.655 G7): same selection + budget, random line scores
        import hashlib
        import random
        rng = random.Random(hashlib.sha256(text.encode("utf-8", "replace")).hexdigest())   # deterministic per output
        return [rng.random() for _ in text.splitlines()]
    if _state.get("sess") is not None:
        from export_v2_onnx import onnx_line_scores
        with _lock:
            return onnx_line_scores(_state["sess"], _state["tok"], text.splitlines(), intent, _state["max_len"],
                                    _state["line_cap"], _state["intent_cap"])[0]
    import torch
    from line_encode import encode
    tok, model, dev = _state["tok"], _state["model"], _state["dev"]
    lines = text.splitlines()
    out = [0.0] * len(lines)
    start = 0
    with _lock, torch.no_grad():
        while start < len(lines):
            ids, line_of, end, _ = encode(tok, intent, lines, _state["max_len"], None, start, _state["line_cap"],
                                          _state["intent_cap"])
            p = model(input_ids=torch.tensor([ids], device=dev)).logits.float().softmax(-1)[0, :, 1].tolist()
            acc: dict[int, list[float]] = {}
            for li, pp in zip(line_of, p):
                if li >= 0:
                    acc.setdefault(li, []).append(pp)
            for li, ps in acc.items():
                out[li] = sum(ps) / len(ps)
            start = max(end, start + 1)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {})

    def do_POST(self):
        if self.path != "/compress":
            return self._send(404, {})
        try:
            import methods
            _install_keep_recorder()
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"error": "Content-Length required"})
            if n < 0 or n > _state["max_body"]:   # before reading: a huge body must not be buffered at all
                return self._send(413, {"error": f"body over --max-body {_state['max_body']} bytes"})
            req = json.loads(self.rfile.read(n))
            if len(req.get("text", "")) > _state["max_chars"]:   # one huge output must not stall every later call
                return self._send(413, {"error": f"text over --max-input {_state['max_chars']} chars"})
            t0 = time.perf_counter()
            _tl.keep = None
            text, keep = req["text"], float(req.get("keep", 0.5))
            sc = scores(text, req.get("intent", ""))
            t1 = time.perf_counter()
            lines = text.splitlines()
            force = methods.error_lines(lines, _state["keep_errors"]) if _state["keep_errors"] else None
            sel = req.get("select") or _state.get("select", "line")   # per request (ctxprune_core presets)
            if sel not in SELECTS:
                return self._send(400, {"error": f"select must be one of {SELECTS}"})
            alpha = req.get("alpha", _state.get("alpha", 0.3))
            if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0.0 <= alpha <= 1.0:
                return self._send(400, {"error": "alpha must be a number in [0, 1]"})
            if sel == "line":
                new = methods._select(lines, sc, keep * len(text), _state["round"], force)
            else:   # --round / --keep-errors apply to the chosen selector (they used to force plain line selection)
                if _state["round"]:
                    sc = [round(x / _state["round"]) * _state["round"] for x in sc]
                if force:
                    top = max(sc) if sc else 1.0
                    sc = [top + 1.0 if i in force else x for i, x in enumerate(sc)]
                if sel == "smooth":
                    new = methods.smooth_select(lines, sc, keep * len(text))
                else:   # anchored (PLAN §2.656: head/tail reserve + model blocks)
                    new = methods.anchored_select(lines, sc, keep * len(text), alpha=float(alpha))
            res = {"text": new, "kept_chars": len(new), "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "score_ms": round((t1 - t0) * 1000, 1)}
            if req.get("want_keep"):
                res["keep"], res["n_lines"] = getattr(_tl, "keep", None), len(lines)
            _tl.keep = None
            self._send(200, res)
        except Exception as e:  # the hook fails open on any non-200
            self._send(500, {"error": repr(e)})

    def _send(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def _self_test() -> None:
    """Every selector, server-wide and per request, through ctxprune_core.process (recoverable on): the result must
    be lossy, recoverable (footer + exact ranges) and smaller; bad select / alpha get 400. Random-drop scores (no
    model): selection is the same code after torch or ONNX scoring."""
    import tempfile
    import urllib.error
    import urllib.request
    from ctxprune_core import Settings, process, recover_file_of
    _state.update(max_len=1024, line_cap=0, intent_cap=0, round=0.0, keep_errors=0, sess=None, random_drop=True,
                  select="line", alpha=0.3, max_chars=400_000, max_body=6 * 400_000 + 65536)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log = "\n".join(f"2026-10-17 12:{i // 60:02d}:{i % 60:02d} INFO worker-{i % 7} step {i} loss={i * 0.37:.3f} "
                     f"lr=3e-4 tokens={i * 1013}" for i in range(400))
    holes = {}
    try:
        with tempfile.TemporaryDirectory() as td:
            for server_sel in SELECTS:
                _state["select"] = server_sel
                for sel in ("",) + SELECTS:
                    s = Settings(model=True, port=port, timeout=10, state=td, select=sel, recoverable=True)
                    out, rec = process(log, "python train.py 2>&1 | tail -400", intent="loss", settings=s)
                    assert rec["stage"] == "model" and rec["v2"] == "ok", (server_sel, sel, rec)
                    f = recover_file_of(out)
                    assert f and os.path.exists(os.path.join(td, "out", f)) and len(out) < len(log), (sel, rec)
                    holes[(server_sel, sel)] = out.count(" omitted …]")
            # the request's selector wins over --select; smooth/anchored make few large gaps, line many small ones
            for sel in SELECTS:
                assert len({holes[(srv_sel, sel)] for srv_sel in SELECTS}) == 1, holes
            assert holes[("line", "anchored")] < holes[("line", "line")], holes
            assert holes[("line", "smooth")] < holes[("line", "line")], holes
            for bad in ({"select": "fold"}, {"alpha": 2}, {"alpha": "x"}):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/compress",
                                             json.dumps({"text": log, "keep": 0.5, **bad}).encode(),
                                             {"Content-Type": "application/json"})
                try:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
                    raise AssertionError(f"accepted {bad}")
                except urllib.error.HTTPError as e:
                    assert e.code == 400, (bad, e.code)
    finally:
        srv.shutdown()
    print(f"v2_server self-test OK (gaps per output: line {holes[('line', 'line')]}, smooth "
          f"{holes[('line', 'smooth')]}, anchored {holes[('line', 'anchored')]})")


def main() -> None:
    if "--self-test" in sys.argv:
        return _self_test()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--max-len", type=int, default=1024, help="window length in tokens")
    ap.add_argument("--line-cap", type=int, default=0, help="model sees at most N tokens per line (0 = off)")
    ap.add_argument("--intent-cap", type=int, default=0, help="model sees the last N intent tokens (0 = off: first 200)")
    ap.add_argument("--onnx", default="", help="ONNX model file: score with onnxruntime CPU instead of torch")
    ap.add_argument("--round", type=float, default=0.0, help="rank by scores rounded to this step (0 = off)")
    ap.add_argument("--keep-errors", type=int, default=0, help="hard-keep error lines + N frame lines (0 = off)")
    ap.add_argument("--random-drop", action="store_true", help="positive control: random line scores (no model)")
    ap.add_argument("--select", choices=SELECTS, default="line",
                    help="line = top lines (v2.0); smooth = contiguous, no 1-3 line holes, markers in budget; "
                         "anchored = smooth + --alpha of the budget reserved for head/tail. A request's "
                         "\"select\" / \"alpha\" fields override these (ctxprune_core presets)")
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--max-input", type=int, default=400_000, help="larger inputs get 413 (caller keeps stage 0)")
    ap.add_argument("--max-body", type=int, default=0,
                    help="request body cap in bytes, checked before reading (0 = 6 x --max-input + 64 KiB: JSON "
                         "\\uXXXX escaping of non-ASCII text)")
    a = ap.parse_args()
    _state.update(max_len=a.max_len, line_cap=a.line_cap, intent_cap=a.intent_cap, round=a.round,
                  keep_errors=a.keep_errors, sess=None, random_drop=a.random_drop,
                  select=a.select, alpha=a.alpha, max_chars=a.max_input,
                  max_body=a.max_body or 6 * a.max_input + 65536)
    if a.random_drop:
        backend = "random-drop control (no model)"
    elif a.onnx:
        from export_v2_onnx import load_tok, ort_session
        _state.update(tok=load_tok(a.model), sess=ort_session(a.onnx, a.threads))
        backend = f"onnx {a.onnx}, {a.threads} threads"
    else:
        import torch
        from transformers import AutoTokenizer
        from train_lines import load_model
        torch.set_num_threads(a.threads)
        _state.update(tok=AutoTokenizer.from_pretrained(a.model), model=load_model(a.model, a.device, False).eval(),
                      dev=a.device)
        backend = a.device
    print(f"v2 server ready on 127.0.0.1:{a.port} ({backend})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
