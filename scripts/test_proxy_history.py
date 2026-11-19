#!/usr/bin/env python3
"""Canary for safety gate b (research/strategy/README.md §5b, 2026-10-18): ctxprune_proxy never changes history it
already sent. On Claude Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 a thinking block is valid only while the
system prompt, the tools and every earlier message are unchanged; accounts created on or after 2026-08-31 get a 400
by default on any edit (platform.claude.com/docs/en/build-with-claude/preserved-thinking: "Make your integration
append-only"). The docs judge content (cache_control moves are allowed), so bodies are compared parsed.

A scripted multi-turn conversation per API (Anthropic with thinking blocks + signatures, Responses with reasoning
items, chat) runs through a real proxy in front of a stub upstream that records every forwarded body. Between turns
come the events that used to re-render history: soft OFF on / off, hard OFF on / off, --rerender, a VERSION + preset
+ note change, a restart on the same store, render_store eviction 200 days later, and (Anthropic) a request with
server-side context editing (clear_tool_uses). After every event the next request must carry the previous request's
system, tools and messages unchanged, and its new results still get the pipeline. A deleted store is the one edit
nobody can avoid; there the history goes out exactly as the client sent it and no note appears mid-conversation.
No model, no network, zero spend. Run: python3 -I ~/ctxprune-v2/scripts/test_proxy_history.py   (~3 s)
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="ctxprune-history-test-")
os.environ["CTXPRUNE_STATE"] = TMP
for k in ("CTXPRUNE_OFF", "CTXPRUNE_RERENDER", "CTXPRUNE_PRESET", "CTXPRUNE_DEDUP", "CTXPRUNE_LOG"):
    os.environ.pop(k, None)
sys.path.insert(0, HERE)
import ctxprune_proxy as cp  # noqa: E402

os.environ.setdefault("CTXPRUNE_HITS", "1")   # grep grouping is opt-in (lprobe-long2); these tests use grep output
                                             # as the lossless example; the default is tested in test_lossless.GrepDefault

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def grep_out(word: str, n: int = 90) -> str:
    return "\n".join([f"src/app/models/{word}_model.py:{i}:    obj = {word.title()}.get({i})  # lookup {i}"
                      for i in range(1, n)] + [f"src/app/views/{word}_views.py:{i}:from ..models import X  # {i}"
                                               for i in range(1, n // 2)])


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        b = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.bodies.append(b)
        out = b'{"id":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


# ---------------------------------------------------------------- per-API conversations (what the CLIENT sends)
SYSTEM = [{"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}]
TOOLS = [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}]


def anth_turn(conv: dict, k: int, text: str) -> None:
    """Append one assistant turn (thinking + text + tool_use) and its tool_result, as Claude Code echoes them."""
    conv["messages"] += [
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": f"sig-{k}-" + "A" * 40},   # Opus 5.5: omitted display
            {"type": "text", "text": f"Searching step {k}."},
            {"type": "tool_use", "id": f"toolu_{k}", "name": "Bash", "input": {"command": f"rg -n Word{k} src"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{k}", "content": text}]}]


def resp_turn(conv: dict, k: int, text: str) -> None:
    conv["input"] += [
        {"type": "reasoning", "id": f"rs_{k}", "summary": [], "encrypted_content": f"enc-{k}-" + "B" * 40},
        {"type": "function_call", "call_id": f"c{k}", "name": "shell",
         "arguments": json.dumps({"command": ["bash", "-lc", f"rg -n Word{k} src"]})},
        {"type": "function_call_output", "call_id": f"c{k}", "output": text}]


def chat_turn(conv: dict, k: int, text: str) -> None:
    conv["messages"] += [
        {"role": "assistant", "content": f"Step {k}.", "tool_calls": [{"id": f"call_{k}", "type": "function", "function": {
            "name": "terminal", "arguments": json.dumps({"command": f"rg -n Word{k} src"})}}]},
        {"role": "tool", "tool_call_id": f"call_{k}", "content": text}]


APIS = {
    "anthropic": ("/v1/messages", lambda: {"model": "claude-opus-5-5", "max_tokens": 64, "system": copy.deepcopy(SYSTEM),
                                           "tools": TOOLS, "thinking": {"type": "adaptive"},
                                           "messages": [{"role": "user", "content": "find the lookups"}]}, anth_turn),
    "responses": ("/v1/responses", lambda: {"model": "gpt-5", "instructions": "You are Codex.", "tools": TOOLS,
                                            "input": [{"type": "message", "role": "user",
                                                       "content": [{"type": "input_text", "text": "find"}]}]}, resp_turn),
    "chat": ("/v1/chat/completions", lambda: {"model": "qwen", "messages": [
        {"role": "system", "content": "You are an agent."}, {"role": "user", "content": "find"}]}, chat_turn),
}


def prefix_parts(api: str, body: dict):
    """(system-ish, tools, history list) of a forwarded body, parsed: what the prefix check covers."""
    if api == "anthropic":
        return body.get("system"), body.get("tools"), body.get("messages")
    if api == "responses":
        return body.get("instructions"), body.get("tools"), body.get("input")
    return None, body.get("tools"), body.get("messages")


def strip_cc(x):
    """cache_control markers are not part of the prefix check (docs: 'Add, move, or remove cache_control: Valid')."""
    if isinstance(x, list):
        return [strip_cc(v) for v in x]
    if isinstance(x, dict):
        return {k: strip_cc(v) for k, v in x.items() if k != "cache_control"}
    return x


class History(unittest.TestCase):
    maxDiff = 600

    def setUp(self):
        self.up = serve(Upstream)
        self.up.bodies = []
        self.addCleanup(lambda: (self.up.shutdown(), self.up.server_close()))
        self.store = os.path.join(tempfile.mkdtemp(dir=TMP), "proxy-store.sqlite")
        self.off = os.path.join(TMP, "OFF")
        if os.path.exists(self.off):
            os.remove(self.off)
        self.version = cp.VERSION
        self.addCleanup(setattr, cp, "VERSION", self.version)
        self.start()

    def start(self, **kw):
        """(Re)start the proxy on the same store: a restart, or new code / settings when kw differ."""
        args = dict(keep=None, min_chars=None, cache_file=self.store, note="on", dedup=True, preset="safe",
                    off_file=self.off, harness="")
        args.update(kw)
        cp.configure(f"http://127.0.0.1:{self.up.server_address[1]}", **args)
        if getattr(self, "px", None):
            self.px.shutdown()
            self.px.server_close()
        self.px = serve(cp.Proxy)
        self.addCleanup(lambda px=self.px: (px.shutdown(), px.server_close()) if px is self.px else None)

    def post(self, path: str, body: dict) -> dict:
        n = len(self.up.bodies)
        req = urllib.request.Request(f"http://127.0.0.1:{self.px.server_address[1]}{path}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with OPENER.open(req, timeout=20) as r:
            r.read()
        self.assertEqual(len(self.up.bodies), n + 1)
        return json.loads(self.up.bodies[-1])

    def run_conversation(self, api: str, events) -> list:
        """events: list of (name, callable or None). Turn k+1 follows event k. -> forwarded bodies."""
        path, fresh, turn = APIS[api]
        conv = fresh()
        sent = [self.post(path, conv)]                         # turn 0: no tool result yet (gets the note)
        for k, (name, ev) in enumerate([("first", None)] + list(events), start=1):
            if ev is not None:
                ev()
            # a repeated output (dedup) and a fresh grep (lossless grouping) per turn
            turn(conv, k, grep_out("word" if k % 2 else f"w{k}"))
            body = copy.deepcopy(conv)
            if name == "server_clears":
                body["context_management"] = {"edits": [{"type": "clear_tool_uses_20250919"}]}
            fwd = self.post(path, body)
            ps, pt, ph = prefix_parts(api, sent[-1])
            s, t, h = prefix_parts(api, fwd)
            with self.subTest(api=api, event=name):
                self.assertEqual(strip_cc(s), strip_cc(ps), f"{api}: system changed after {name}")
                self.assertEqual(t, pt, f"{api}: tools changed after {name}")
                self.assertEqual(strip_cc(h[:len(ph)]), strip_cc(ph), f"{api}: earlier messages changed after {name}")
            sent.append(fwd)
        return sent

    def events(self, with_clears: bool):
        def touch(txt=""):
            with open(self.off, "w") as f:
                f.write(txt)

        def evict_200_days_on():
            cp.STORE.db.execute("UPDATE pin SET seen = seen - 200 * 86400")
            cp.STORE.db.execute("UPDATE content SET seen = seen - 200 * 86400")
            cp.STORE.mem.clear()
            cp.STORE.mem_size = 0
            cp.STORE.evict(time.time())

        def new_code():
            cp.VERSION = self.version + "-next"
            self.start(note="min", dedup=False)

        evs = [("soft OFF on", lambda: touch()), ("soft OFF lifted", lambda: os.remove(self.off)),
               ("hard OFF on", lambda: touch("hard\n")), ("hard OFF lifted", lambda: os.remove(self.off)),
               ("CTXPRUNE_OFF=1", lambda: os.environ.__setitem__("CTXPRUNE_OFF", "1")),
               ("CTXPRUNE_OFF lifted", lambda: os.environ.pop("CTXPRUNE_OFF")),
               ("--rerender", lambda: self.start(rerender=True)),
               ("VERSION + preset + note change", new_code),
               ("restart same store", lambda: self.start()),
               ("eviction 200 days later", evict_200_days_on),
               ("plain turn", None)]
        if with_clears:
            evs.append(("server_clears", None))
        return evs

    def check_pipeline_still_runs(self, api: str, sent: list) -> None:
        """Every new result after an ON event was compressed (grouped) when sent first, raw while OFF."""
        _, _, h = prefix_parts(api, sent[1])
        blob = json.dumps(h[-1])
        self.assertTrue("[cp:" in blob or "[ctxprune" in blob, f"{api}: the first turn's result was not compressed")

    def test_anthropic(self):
        sent = self.run_conversation("anthropic", self.events(with_clears=False))
        self.check_pipeline_still_runs("anthropic", sent)
        from ctxprune_note import NOTE
        self.assertIn(json.dumps(NOTE)[1:-1], json.dumps(sent[0]["system"]))   # the note: turn 0, then pinned

    def test_anthropic_server_clears(self):
        """Context editing on the server (clear_tool_uses) must not make the proxy rewrite pinned history either."""
        self.run_conversation("anthropic", [("plain turn", None), ("server_clears", None), ("plain turn", None)])

    def test_responses(self):
        sent = self.run_conversation("responses", self.events(with_clears=False))
        self.check_pipeline_still_runs("responses", sent)

    def test_chat(self):
        sent = self.run_conversation("chat", self.events(with_clears=False))
        self.check_pipeline_still_runs("chat", sent)

    def test_store_lost_history_goes_raw_no_note(self):
        """A deleted store is the one unavoidable edit: then history goes out exactly as the client has it (never
        computed again with today's code), only the new result is compressed, and no note appears mid-conversation."""
        path, fresh, turn = APIS["anthropic"]
        conv = fresh()
        self.post(path, conv)
        for k in (1, 2):
            turn(conv, k, grep_out(f"w{k}"))
            self.post(path, copy.deepcopy(conv))
        self.store = os.path.join(tempfile.mkdtemp(dir=TMP), "empty.sqlite")   # store gone
        self.start()
        turn(conv, 3, grep_out("w3"))
        fwd = self.post(path, copy.deepcopy(conv))
        self.assertEqual(strip_cc(fwd["system"]), strip_cc(conv["system"]))     # no note added mid-conversation
        self.assertEqual(fwd["messages"][:-1], conv["messages"][:-1])           # history: the client's bytes
        new = json.dumps(fwd["messages"][-1])
        self.assertTrue("[cp:" in new or "[ctxprune" in new)                    # the new result: pipeline
        st = json.loads(OPENER.open(f"http://127.0.0.1:{self.px.server_address[1]}/ctxprune/stats", timeout=5).read())
        self.assertGreaterEqual(st.get("history_raw", 0), 2)

    def test_unreadable_pin_is_no_pin_store_stays(self):
        """A pin row whose value can't be read (corrupt at runtime) is 'no pin': the history result goes out as the
        client sent it, the row is replaced, and the store stays on disk (not memory-only)."""
        path, fresh, turn = APIS["anthropic"]
        conv = fresh()
        self.post(path, conv)
        turn(conv, 1, grep_out("w1"))
        self.post(path, copy.deepcopy(conv))
        cp.STORE.db.execute("UPDATE pin SET t = '{not json' WHERE t IS NOT NULL AND k NOT LIKE 'note:%'")
        cp.STORE.mem.clear()
        cp.STORE.mem_size = 0
        turn(conv, 2, grep_out("w2"))
        fwd = self.post(path, copy.deepcopy(conv))
        self.assertEqual(fwd["messages"][:-1], conv["messages"][:-1])   # history: the client's bytes
        self.assertIsNotNone(cp.STORE.db)                                 # still persistent
        self.assertGreaterEqual(cp.STORE.bad_rows, 1)
        again = self.post(path, copy.deepcopy(conv))
        self.assertEqual(again["messages"], fwd["messages"])             # and stable from then on

    def test_store_defaults_keep_resumable_conversations(self):
        from render_store import RenderStore
        s = RenderStore(os.path.join(TMP, "defaults.sqlite"))
        self.assertEqual(s.max_age_s, 0)
        self.assertGreaterEqual(s.live_s, 365 * 86400)


# ---------------------------------------------------------------- T2.1 (v0.4): per-request dedup + pins that never go
REF = "identical to"   # dedup.py WHOLE / MARK text: only a dedup reference contains it


class Gate(History):
    """T2.1 canaries on the same proxy + stub upstream as History (dedup decided BY MODEL here, not forced on)."""

    def start(self, **kw):
        kw.setdefault("dedup", None)
        super().start(**kw)

    def fresh(self, api: str) -> dict:
        """A conversation of its own: the session seed (system + first message) keys clears_seen / pin state that
        lives in the shared CTXPRUNE_STATE dir, so History's server_clears run must not reach these."""
        conv = APIS[api][1]()
        msgs = conv["messages"]
        msgs[-1 if api == "chat" else 0]["content"] = f"gate {self.id()}"
        return conv

    def turn_texts(self, body: dict) -> list:
        return [b["content"] for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                for b in m["content"] if b.get("type") == "tool_result"]

    def test_model_switch_opus_sonnet_haiku_opus(self):
        """Opus -> Sonnet -> Haiku -> Opus mid-session (/model, or Claude Code's usage-limit fallback): a result that
        arrives while the request's model is not Opus never gets a dedup reference; Opus gets them again; history
        already sent is byte-stable at every step (the switch is the one event the cache can't survive anyway)."""
        path, _, turn = APIS["anthropic"]
        conv = self.fresh("anthropic")
        models = ["claude-opus-5-5", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
        sent = []
        for k, model in enumerate(models, start=1):
            turn(conv, k, grep_out("word"))   # the same output every turn: a dedup candidate each time
            conv["model"] = model
            sent.append(self.post(path, copy.deepcopy(conv)))
            if k > 1:
                self.assert_prefix("anthropic", sent[-2], sent[-1], f"{model} turn {k}")
        last = [self.turn_texts(b)[-1] for b in sent]
        self.assertIn(REF, last[1], "Opus: a repeated output is deduped")
        self.assertNotIn(REF, last[2], "Sonnet: new result must carry no dedup reference")
        self.assertNotIn(REF, last[3], "Haiku: new result must carry no dedup reference")
        self.assertIn(REF, last[4], "back on Opus: dedup is on again")

    def test_failed_setup_does_not_leak_a_dedup_session(self):
        """rewrite() keeps its per-request state in thread-locals; a request that raised during setup (fail-open: the
        original bytes go out) must not leave the Opus DedupSession for the next request on that thread (HTTP/1.1
        keep-alive reuses the handler thread): that request is a Sonnet one."""
        path, _, turn = APIS["anthropic"]

        def body(model: str, n: int = 2) -> bytes:
            conv = self.fresh("anthropic")
            conv["model"] = model
            for k in range(1, n + 1):
                turn(conv, k, grep_out("word"))
            return json.dumps(conv).encode()

        cp.rewrite("anthropic", body("claude-opus-5-5", 1))   # turn 1 pinned (compressed), as the earlier request
        real = cp.clears_seen
        cp.clears_seen = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                cp.rewrite("anthropic", body("claude-opus-5-5"))
        finally:
            cp.clears_seen = real
        out, _, _ = cp.rewrite("anthropic", body("claude-sonnet-5-5"))
        self.assertFalse(REF.encode() in out, "a Sonnet request inherited the failed Opus request's dedup session")

    def age_store(self, days: float) -> None:
        cp.STORE.db.execute(f"UPDATE pin SET seen = seen - {int(days * 86400)}")
        cp.STORE.db.execute(f"UPDATE content SET seen = seen - {int(days * 86400)}")
        cp.STORE.mem.clear()
        cp.STORE.mem_size = 0

    def drive(self, api: str, n: int, start: int = 1, conv=None):
        path, _, turn = APIS[api]
        conv = conv or self.fresh(api)
        out = []
        for k in range(start, start + n):
            turn(conv, k, grep_out(f"w{k}"))
            out.append(self.post(path, copy.deepcopy(conv)))
        return conv, out

    def assert_prefix(self, api: str, old: dict, new: dict, why: str) -> None:
        ps, pt, ph = prefix_parts(api, old)
        s, t, h = prefix_parts(api, new)
        self.assertTrue(strip_cc(h[:len(ph)]) == strip_cc(ph), f"{api}: earlier messages changed: {why}")

    def test_anthropic_pins_never_evicted_even_over_the_cap_and_past_the_live_window(self):
        """A resumed Claude Code session (weeks / months later) must find every pin: losing one turns a compressed
        result raw in history = an edit of the prefix = a 400 on the 5.x models. The cap and the 365-day live window
        used to let a pin that old go; for the Anthropic API a pin that changed bytes now never does (a NULL pin,
        'sent unchanged', is harmless to lose: raw stays raw). Also at open (restart) and with env limits lowered."""
        conv, sent = self.drive("anthropic", 3)
        self.assertGreater(sum(1 for r in cp.STORE.db.execute("SELECT t FROM pin") if r[0] is not None), 0)
        cp.STORE.max_bytes, cp.STORE.live_s = 1, 60                       # CTXPRUNE_STORE_MAX_MB / LIVE_DAYS lowered
        self.age_store(400)
        cp.STORE.evict(time.time())
        conv, more = self.drive("anthropic", 1, start=4, conv=conv)
        self.assert_prefix("anthropic", sent[-1], more[0], "pins evicted (400 days, over the cap)")
        self.start()                                                      # restart: eviction at open (default limits)
        self.age_store(800)
        cp.STORE.max_bytes, cp.STORE.live_s = 1, 60
        cp.STORE.evict(time.time())
        conv, more2 = self.drive("anthropic", 1, start=5, conv=conv)
        self.assert_prefix("anthropic", more[0], more2[0], "pins evicted (800 days, after a restart)")

    def test_anthropic_hold_is_decided_at_open(self):
        """The store remembers that it holds Anthropic pins: after a restart the eviction that runs at open (before any
        request has been seen) already honours it."""
        from render_store import RenderStore
        conv, sent = self.drive("anthropic", 2)
        path = cp.STORE.path
        cp.STORE.close()
        db = __import__("sqlite3").connect(path)
        db.execute("UPDATE pin SET seen = seen - 800*86400")
        db.commit(); db.close()
        s = RenderStore(path, max_mb=0.000001, live_days=1)
        self.assertGreater(sum(1 for r in s.db.execute("SELECT t FROM pin") if r[0] is not None), 0)
        s.close()

    def test_other_apis_keep_the_old_limits(self):
        """Scope: only Anthropic-format traffic holds its pins; chat / responses stores are evicted as before."""
        conv, sent = self.drive("chat", 2)
        cp.STORE.max_bytes, cp.STORE.live_s = 1, 60
        self.age_store(400)
        cp.STORE.evict(time.time())
        self.assertEqual(cp.STORE.count("pin"), 0)


for _n in [n for n in dir(History) if n.startswith("test_")]:   # Gate inherits History's helpers, not its tests
    setattr(Gate, _n, None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
