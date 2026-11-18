#!/usr/bin/env python3
"""Safety gate b for the plugin request-time paths: Hermes `llm_request`, Pi `context`, OpenCode
`experimental.chat.messages.transform` never change history they already sent (test_proxy_history.py is the same
check for ctxprune_proxy). Parity review 2026-10-18.

What each provider does with an edited earlier turn (docs read 2026-10-18):
  * Anthropic 5.x / Fable 5.1 preserved thinking: a thinking block stays valid only while every earlier message is
    unchanged; accounts created on or after 2026-08-31 get a 400 (platform.claude.com ... preserved-thinking).
  * OpenAI Responses: reasoning items (encrypted_content when store=false) must come back, and "all items between the
    last user message and your function call output [must be] passed into the next response untouched"
    (developers.openai.com/api/docs/guides/reasoning); any earlier change re-prices the prefix (prompt-caching).
  * OpenAI chat / local vLLM, llama.cpp: no validation; an edit is a prefix-cache miss from that point.
  * DeepSeek thinking + tools: reasoning_content of every earlier turn must be passed back or 400; edits are not
    validated, but the KV cache matches whole prefix units only (api-docs.deepseek.com thinking_mode, kv_cache).
  * Gemini: thought signatures must be resent exactly, thought blocks never modified (ai.google.dev thinking).
So the bar for every shape is the proxy's: request k+1 starts with request k's messages / input items, unchanged
(compared parsed, field by field, including thinking / signature / reasoning_content / encrypted_content / extra_content),
and the provider-owned fields are never touched at all.

Per plugin a scripted conversation runs through the plugin code (Hermes in-process; Pi and OpenCode as the real
plugin files in Node, calling this repo's ctxprune_compress.py), with these events between turns: dedup forced on,
CTXPRUNE_DEDUP=0, CTXPRUNE_OFF=1, a restart on the same store, a switch to the provider's own model (dedup off by
model), a changed marker format (an upgrade), back to Opus. Plus: Hermes with MAX_STUCK tool-time workers still
running (the request path must not give up then), and OpenCode's hook order (messages.transform runs before
chat.params, so the switch must be read from the last user message). No model, no network, zero spend.
Run: python3 -I ~/ctxprune-v2/scripts/test_plugin_history.py   (~15 s; node for the Pi / OpenCode part)
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="ctxprune-plugin-history-")
_SAVED_ENV: dict = {}


def setUpModule():
    _SAVED_ENV.clear()
    _SAVED_ENV.update(os.environ)
    os.environ.update(CTXPRUNE_SCRIPTS=HERE, CTXPRUNE_NO_MODEL="1", CTXPRUNE_BUDGET="0")
    for k in ("CTXPRUNE_OFF", "CTXPRUNE_DEDUP", "CTXPRUNE_PRESET", "CTXPRUNE_LOG", "CTXPRUNE_RERENDER",
              "CTXPRUNE_REQUEST_BUDGET", "CTXPRUNE_STATE"):
        os.environ.pop(k, None)


def tearDownModule():
    os.environ.clear()
    os.environ.update(_SAVED_ENV)
    shutil.rmtree(TMP, ignore_errors=True)


sys.path.append(HERE)
_spec = importlib.util.spec_from_file_location(
    "hermes_ctxprune_hist", os.path.join(HERE, "integrations", "hermes", "ctxprune", "__init__.py"))
herm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(herm)

# ---------------------------------------------------------------- fixtures (synthetic)
GIT = "On branch main\nChanges not staged for commit:\n\tmodified:   src/app/server.py\n\tmodified:   tests/test_api.py\n"
TESTS = [f"tests/test_api.py::test_endpoint_{i} PASSED                       [{i:3d}%]" for i in range(40)]
PYTEST = "\n".join(TESTS + ["E       AssertionError: assert 500 == 200", "==== 1 failed, 39 passed in 2.31s ===="])
PYTEST2 = PYTEST.replace("2.31s", "2.07s")
# (command, output) per turn: repeats so dedup fires in the Opus turns
STEPS = [("git status", GIT), ("pytest -q", PYTEST), ("git status", GIT), ("pytest -q", PYTEST2),
         ("git status", GIT), ("pytest -q", PYTEST), ("git status", GIT), ("pytest -q", PYTEST2),
         ("git status", GIT)]
# event before turn k (1-based); every shape gets the same schedule
SCHEDULE = {1: "opus", 2: "opus", 3: "opus", 4: "dedup0", 5: "off", 6: "restart", 7: "native", 8: "upgrade", 9: "opus"}
MARK = "[ctxprune:"


def state_dir(name: str) -> str:
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- Hermes request shapes (provider-owned fields set)
def h_chat(n: int, flavour: str) -> dict:
    """OpenAI chat; DeepSeek (reasoning_content); Gemini via chat (extra_content thought_signature); local."""
    msgs = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": "fix the tests"}]
    for i, (cmd, out) in enumerate(STEPS[:n]):
        cid = f"call_{i}"
        tc = {"id": cid, "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"command": cmd})}}
        a = {"role": "assistant", "content": "", "tool_calls": [tc]}
        if flavour == "deepseek":
            a["reasoning_content"] = f"I should run {cmd} (step {i})."
        if flavour == "gemini":
            tc["extra_content"] = {"google": {"thought_signature": f"c2lnLXtpfS0{i}=="}}
        msgs += [a, {"role": "tool", "tool_call_id": cid, "content": out}]
    return {"model": "agent", "messages": msgs}


def h_anthropic(n: int) -> dict:
    msgs = [{"role": "user", "content": [{"type": "text", "text": "fix the tests"}]}]
    for i, (cmd, out) in enumerate(STEPS[:n]):
        msgs.append({"role": "assistant", "content": [
            {"type": "thinking", "thinking": f"Run {cmd} next ({i}).", "signature": f"EqQBCkYIBx{i}AAA"},
            {"type": "tool_use", "id": f"toolu_{i}", "name": "terminal", "input": {"command": cmd}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{i}",
                                                  "content": [{"type": "text", "text": out}]}]})
    return {"model": "agent", "system": [{"type": "text", "text": "You are Hermes."}], "messages": msgs,
            "thinking": {"type": "adaptive"}}


def h_responses(n: int) -> dict:
    items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix the tests"}]}]
    for i, (cmd, out) in enumerate(STEPS[:n]):
        items += [{"type": "reasoning", "id": f"rs_{i}", "summary": [], "encrypted_content": f"gAAAAB{i}enc=="},
                  {"type": "function_call", "call_id": f"fc_{i}", "name": "terminal",
                   "arguments": json.dumps({"command": cmd})},
                  {"type": "function_call_output", "call_id": f"fc_{i}", "output": out}]
    return {"model": "agent", "instructions": "You are Hermes.", "input": items, "store": False,
            "include": ["reasoning.encrypted_content"]}


SHAPES = {   # name: (builder, the provider's own model, provider, base_url)
    "openai_chat": (lambda n: h_chat(n, "openai"), "gpt-6-luna", "openrouter", "https://openrouter.ai/api/v1"),
    "deepseek_chat": (lambda n: h_chat(n, "deepseek"), "deepseek-v4.1-flash", "deepseek", "https://api.deepseek.com"),
    "gemini_chat": (lambda n: h_chat(n, "gemini"), "gemini-3.8-flash", "gemini",
                    "https://generativelanguage.googleapis.com/v1beta/openai"),
    "local_chat": (lambda n: h_chat(n, "openai"), "qwen3.8-27b", "custom", "http://127.0.0.1:8012/v1"),
    "anthropic": (h_anthropic, "claude-sonnet-5-5", "anthropic", "https://api.anthropic.com"),
    "responses": (h_responses, "gpt-5.6-terra", "openai-codex", "https://chatgpt.com/backend-api/codex"),
}


def history_of(req: dict) -> list:
    return req["input"] if "input" in req else req["messages"]


def provider_fields(item: dict) -> dict:
    """The fields a provider validates or caches on that ctxprune must never touch (all but tool-result text)."""
    if item.get("type") == "function_call_output":
        return {k: v for k, v in item.items() if k != "output"}
    if item.get("role") == "tool":
        return {k: v for k, v in item.items() if k != "content"}
    if item.get("role") == "user" and isinstance(item.get("content"), list) and \
            any(isinstance(b, dict) and b.get("type") == "tool_result" for b in item["content"]):
        return {"role": "user", "ids": [b.get("tool_use_id") for b in item["content"]]}
    return item


def hermes_texts(req: dict) -> list:
    out = []
    for it in history_of(req):
        if it.get("type") == "function_call_output":
            out.append(it["output"])
        elif it.get("role") == "tool":
            out.append(it["content"])
        elif it.get("role") == "user" and isinstance(it.get("content"), list):
            out += [b["content"][0]["text"] for b in it["content"] if b.get("type") == "tool_result"]
    return out


class HermesHistory(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @staticmethod
    def restart():
        """A new Hermes process on the same store: the plugin's in-memory state and open stores go."""
        cc = herm._cc()
        for st in list(cc._STORES.values()):
            try:
                st.close()
            except Exception:   # noqa: BLE001
                pass
        cc._STORES.clear()
        herm._TARGETS.clear()
        herm._MODELS.clear()
        herm._FIRST_TARGET[0] = herm._FIRST_MODEL[0] = ""

    def run_shape(self, name: str):
        build, native, provider, base = SHAPES[name]
        os.environ["CTXPRUNE_STATE"] = state_dir("hermes-" + name)
        self.restart()
        sent_prev, upgrade = None, None
        import dedup
        for k in range(1, len(STEPS) + 1):
            ev = SCHEDULE[k]
            for v in ("CTXPRUNE_DEDUP", "CTXPRUNE_OFF"):
                os.environ.pop(v, None)
            model = "claude-opus-5-5"
            if ev == "dedup0":
                os.environ["CTXPRUNE_DEDUP"] = "0"
            elif ev == "off":
                os.environ["CTXPRUNE_OFF"] = "1"
            elif ev == "restart":
                self.restart()
            elif ev == "native":
                model = native
            elif ev == "upgrade":
                upgrade = mock.patch.object(dedup, "WHOLE", "[ctxprune v9: same as output #{k}{hint}]")
                upgrade.start()
            req = build(k)
            req["model"] = model
            orig = copy.deepcopy(req)
            try:
                r = herm.on_llm_request(request=copy.deepcopy(req), model=model, provider=provider, base_url=base,
                                        session_id="s-" + name, task_id="t-" + name, api_mode="x")
            finally:
                if upgrade is not None:
                    upgrade.stop()
                    upgrade = None
            sent = r["request"] if r else req
            hist = history_of(sent)
            for a, b in zip(hist, history_of(orig)):                   # provider-owned fields: never touched
                self.assertEqual(provider_fields(a), provider_fields(b), (name, k))
            for key in ("system", "instructions", "tools", "thinking", "store", "include"):
                self.assertEqual(sent.get(key), orig.get(key), (name, k, key))
            if sent_prev is not None:                                   # history it already sent: unchanged
                old = history_of(sent_prev)
                self.assertEqual(json.dumps(hist[:len(old)], sort_keys=True), json.dumps(old, sort_keys=True),
                                 f"{name}: request {k} ({ev}) changed earlier history")
            sent_prev = sent
        texts = hermes_texts(sent_prev)
        self.assertTrue(any(t.startswith(MARK) for t in texts), (name, "no dedup marker: the test exercised nothing"))
        return texts

    def test_every_provider_shape(self):
        for name in SHAPES:
            with self.subTest(shape=name):
                texts = self.run_shape(name)
                # dedup off, OFF and the provider's own model add their new result raw; Opus again dedups
                self.assertEqual(texts[3], STEPS[3][1], name)       # dedup0: new result raw
                self.assertEqual(texts[4], STEPS[4][1], name)       # OFF: new result raw
                self.assertEqual(texts[6], STEPS[6][1], name)       # native model: dedup off by model (gate a)
                self.assertTrue(texts[8].startswith(MARK), name)    # back on Opus: new results deduped again

    def test_request_path_survives_stuck_tool_workers(self):
        """MAX_STUCK tool-time workers still running (slow compress calls past CTXPRUNE_BUDGET) used to make the
        llm_request middleware skip too: the request then went out with the ORIGINAL results, reverting the pinned
        ones (a history edit on every provider; a 400 on Anthropic 5.x with thinking)."""
        os.environ["CTXPRUNE_STATE"] = state_dir("hermes-stuck")
        os.environ["CTXPRUNE_DEDUP"] = "1"
        os.environ["CTXPRUNE_BUDGET"] = "2"
        self.restart()
        first = herm.on_llm_request(request=h_anthropic(3), model="claude-opus-5-5", session_id="stuck")["request"]
        self.assertTrue(any(t.startswith(MARK) for t in hermes_texts(first)))
        saved = herm._STUCK[0]
        herm._STUCK[0] = herm.MAX_STUCK          # (the list object itself: _within_budget binds it as a default)
        try:
            self.assertIsNone(herm.on_tool_result("search_files", {"pattern": "x"}, "a\n" * 3000, task_id="stuck"))
            r = herm.on_llm_request(request=h_anthropic(4), model="claude-opus-5-5", session_id="stuck")
        finally:
            herm._STUCK[0] = saved
        self.assertIsNotNone(r)
        self.assertEqual(history_of(r["request"])[:len(history_of(first))], history_of(first))


# Known gap (parity review 2026-10-18), the proxy's rule missing from the plugins: ctxprune_proxy forwards an unpinned
# result BEFORE the request's last assistant item exactly as the client sent it (and pins it raw); only this turn's
# new results run the pipeline. dedup.context_dedup has the same cut (fixed 2026-10-18; before, it deduped every
# result it had no pin for). Without it a result the provider already saw raw came back deduped when (a) the plugin first sees a conversation mid-way
# (ctxprune installed or enabled in a running / resumed session; a lost store) or (b) a conversation started on a
# non-Opus model (dedup off, nothing pinned) switches to Opus. On Opus 5.5 with preserved thinking that edit is a 400
# for accounts created on or after 2026-08-31. The cut: results up to the last call event group of the request =
# history: no pin -> unchanged + pinned raw, mirroring ctxprune_proxy.history_cut.
class HistoryCut(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {})
        self.env.start()
        HermesHistory.restart()

    def tearDown(self):
        self.env.stop()

    def test_hermes_first_sight_mid_conversation(self):
        os.environ["CTXPRUNE_STATE"] = state_dir("cut-resume")
        r = herm.on_llm_request(request=h_anthropic(4), model="claude-opus-5-5", session_id="resume")
        texts = hermes_texts(r["request"]) if r else hermes_texts(h_anthropic(4))
        self.assertEqual(texts[:3], [s[1] for s in STEPS[:3]])   # #3 (a repeat of #1) was sent raw before

    def test_hermes_switch_to_opus(self):
        os.environ["CTXPRUNE_STATE"] = state_dir("cut-switch")
        for k in (1, 2, 3):
            herm.on_llm_request(request=h_anthropic(k), model="claude-sonnet-5-5", session_id="switch")
        r = herm.on_llm_request(request=h_anthropic(4), model="claude-opus-5-5", session_id="switch")
        texts = hermes_texts(r["request"]) if r else hermes_texts(h_anthropic(4))
        self.assertEqual(texts[:3], [s[1] for s in STEPS[:3]])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_pi_and_opencode_first_sight(self):
        for kind, build, texts in (("pi", lambda k, m: pi_msgs(k), lambda ms: [x["content"][0]["text"] for x in ms
                                                                               if x["role"] == "toolResult"]),
                                   ("opencode", oc_msgs, lambda ms: [p["state"]["output"] for x in ms
                                                                     for p in x["parts"] if p.get("type") == "tool"])):
            res = node_run(kind, [{"env": {}, "model": "claude-opus-5-5", "messages": build(4, "claude-opus-5-5")}],
                           state_dir("cut-node-" + kind))
            self.assertEqual(texts(res[0])[:3], [s[1] for s in STEPS[:3]], kind)


# ---------------------------------------------------------------- Pi / OpenCode (the plugin files, in Node)
NODE_DRIVER = r"""
import { pathToFileURL } from "node:url";
const [kind, mod] = process.argv.slice(2);
const turns = JSON.parse(await new Promise((r) => { let s = ""; process.stdin.on("data", (d) => s += d); process.stdin.on("end", () => r(s)); }));
const m = await import(pathToFileURL(mod).href);
const res = [];
let pi = null, oc = null;
if (kind === "pi") { pi = {}; m.default({ on: (ev, fn) => { pi[ev] = fn; } }); } else { oc = await m.CtxPrune({}); }
for (const t of turns) {
  for (const [k, v] of Object.entries(t.env || {})) { if (v === null) delete process.env[k]; else process.env[k] = v; }
  if (kind === "pi") {
    const r = await pi.context({ type: "context", messages: structuredClone(t.messages) },
                               { model: { id: t.model, provider: "anthropic", baseUrl: "https://api.anthropic.com" } });
    res.push(r ? r.messages : t.messages);
  } else {
    const out = { messages: structuredClone(t.messages) };
    await oc["experimental.chat.messages.transform"]({}, out);       // OpenCode's order: transform, then chat.params
    await oc["chat.params"]({ sessionID: "oc1", model: { id: t.model, providerID: "anthropic" }, provider: {} });
    res.push(out.messages);
  }
}
process.stdout.write(JSON.stringify(res));
"""


def node_run(kind: str, turns: list, state: str) -> list:
    d = tempfile.mkdtemp(dir=TMP)
    src = os.path.join(HERE, "integrations", "pi", "ctxprune.ts") if kind == "pi" else \
        os.path.join(HERE, "integrations", "opencode", "ctxprune.js")
    shutil.copy(src, os.path.join(d, "plugin.mjs"))
    with open(os.path.join(d, "driver.mjs"), "w") as f:
        f.write(NODE_DRIVER)
    env = {**os.environ, "CTXPRUNE_CLI": os.path.join(HERE, "ctxprune_compress.py"), "CTXPRUNE_PYTHON": sys.executable,
           "CTXPRUNE_STATE": state}
    for k in ("CTXPRUNE_DEDUP", "CTXPRUNE_OFF"):
        env.pop(k, None)
    p = subprocess.run(["node", os.path.join(d, "driver.mjs"), kind, os.path.join(d, "plugin.mjs")],
                       input=json.dumps(turns).encode(), capture_output=True, env=env, timeout=180)
    assert p.returncode == 0, p.stderr.decode()[-2000:]
    return json.loads(p.stdout)


def pi_msgs(n: int, native_from: int | None = None) -> list:
    out = [{"role": "user", "content": [{"type": "text", "text": "fix the tests"}]}]
    for i, (cmd, text) in enumerate(STEPS[:n]):
        out.append({"role": "assistant", "content": [
            {"type": "thinking", "thinking": f"run {cmd}", "thinkingSignature": f"EqQB{i}"},
            {"type": "toolCall", "id": f"p{i}", "name": "bash", "arguments": {"command": cmd}}],
            "provider": "anthropic", "model": "claude-opus-5-5"})
        out.append({"role": "toolResult", "toolCallId": f"p{i}", "toolName": "bash",
                    "content": [{"type": "text", "text": text}], "isError": False})
    return out


def oc_msgs(n: int, model: str = "claude-opus-5-5") -> list:
    out = [{"info": {"role": "user", "id": "u0", "sessionID": "oc1",
                     "model": {"providerID": "anthropic", "modelID": "claude-opus-5-5"}},
            "parts": [{"type": "text", "text": "fix the tests"}]}]
    for i, (cmd, text) in enumerate(STEPS[:n]):
        st = {"status": "completed", "input": {"command": cmd, "description": "step"}, "output": text, "title": "",
              "metadata": {}, "time": {"start": 1, "end": 2}}
        out.append({"info": {"role": "assistant", "id": f"a{i}", "sessionID": "oc1", "modelID": "claude-opus-5-5"},
                    "parts": [{"type": "reasoning", "text": f"run {cmd}",
                               "metadata": {"anthropic": {"signature": f"EqQB{i}"}}},
                              {"type": "tool", "callID": f"o{i}", "tool": "bash", "state": st}]})
    out[0]["info"]["model"]["modelID"] = model   # the step's model (OpenCode: lastUser.model)
    return out


def ev_env(ev: str) -> dict:
    return {"CTXPRUNE_DEDUP": "0" if ev == "dedup0" else None, "CTXPRUNE_OFF": "1" if ev == "off" else None}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class NodeHistory(unittest.TestCase):
    def check(self, kind: str, build, items, texts):
        """Turns 1-5 in one process, 6-9 in a second one (the restart); the upgrade event is not reproducible in
        Node (the marker format lives in dedup.py), so turn 8 is a plain Opus turn there."""
        state = state_dir(kind)
        native = "gpt-6-luna"
        turns = []
        for k in range(1, len(STEPS) + 1):
            ev = SCHEDULE[k]
            model = native if ev == "native" else "claude-opus-5-5"
            turns.append({"env": ev_env(ev), "model": model, "messages": build(k, model)})
        res = node_run(kind, turns[:5], state) + node_run(kind, turns[5:], state)
        for k in range(1, len(res)):
            old, new = items(res[k - 1]), items(res[k])
            self.assertEqual(json.dumps(new[:len(old)], sort_keys=True), json.dumps(old, sort_keys=True),
                             f"{kind}: request {k + 1} ({SCHEDULE[k + 1]}) changed earlier history")
        for k, r in enumerate(res):   # provider-owned parts (thinking + signatures) never touched
            self.assertEqual([x for x in items(r) if not is_result(x)],
                             [x for x in items(turns[k]["messages"]) if not is_result(x)], (kind, k))
        t = texts(res[-1])
        self.assertTrue(any(x.startswith(MARK) for x in t[:3]), kind)
        self.assertEqual(t[3], STEPS[3][1])     # dedup0
        self.assertEqual(t[4], STEPS[4][1])     # OFF
        self.assertEqual(t[6], STEPS[6][1], kind + ": the switch away from Opus still deduped (gate a)")
        self.assertTrue(t[8].startswith(MARK), kind)

    def test_pi(self):
        self.check("pi", lambda k, model: pi_msgs(k), lambda ms: ms,
                   lambda ms: [m["content"][0]["text"] for m in ms if m["role"] == "toolResult"])

    def test_opencode(self):
        def items(ms):
            return [p for m in ms for p in m["parts"]]
        self.check("opencode", lambda k, model: oc_msgs(k, model), items,
                   lambda ms: [p["state"]["output"] for m in ms for p in m["parts"] if p.get("type") == "tool"])


DSH_DRIVER = r"""
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[2]).href);
const h = {};
m.apply({ on: (ev, fn) => { h[ev] = fn; } });
const cfg = { provider: "vllm-local", model: "qwen3.8-27b" };
const got = await h["agent/request"]({ turn: 1, step: 1 }, async () => cfg);
const d = await h["tools/post-execute"]({ name: "bash", arguments: { command: "rg -n x" } },
                                        { content: [{ type: "text", text: "x".repeat(3000) }] },
                                        async () => ({ kind: "accept" }));
process.stdout.write(JSON.stringify({ same: got === cfg, text: d.content ? d.content[0].text : null }));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DshTarget(unittest.TestCase):
    """dsh passed neither the model route nor the model id to the compressor, so a local route kept the `paths` rule
    (lossless.paths_on is off for local / self-hosted targets in every other plugin)."""

    def test_route_and_model_reach_the_cli(self):
        d = tempfile.mkdtemp(dir=TMP)
        shutil.copy(os.path.join(HERE, "integrations", "dsh", "ctxprune.mjs"), os.path.join(d, "plugin.mjs"))
        with open(os.path.join(d, "driver.mjs"), "w") as f:
            f.write(DSH_DRIVER)
        cli = os.path.join(d, "fake_cli.py")   # stands in for ctxprune_compress.py: prints what it was given
        with open(cli, "w") as f:
            f.write("import os, sys\nsys.stdin.read()\nprint(os.environ.get('CTXPRUNE_TARGET'), "
                    "os.environ.get('CTXPRUNE_MODEL'))\n")
        env = {k: v for k, v in os.environ.items() if k not in ("CTXPRUNE_TARGET", "CTXPRUNE_MODEL", "CTXPRUNE_OFF")}
        env.update(CTXPRUNE_CLI=cli, CTXPRUNE_PYTHON=sys.executable)
        p = subprocess.run(["node", os.path.join(d, "driver.mjs"), os.path.join(d, "plugin.mjs")],
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode()[-1500:])
        r = json.loads(p.stdout)
        self.assertTrue(r["same"])                                   # agent/request: observed, never switched
        self.assertEqual(r["text"].strip(), "vllm-local qwen3.8-27b")
        sys.path.insert(0, HERE)
        from lossless import paths_on
        self.assertFalse(paths_on("vllm-local"))                       # -> no `paths` rule for this route


def is_result(x: dict) -> bool:
    return x.get("role") == "toolResult" or x.get("type") == "tool"


if __name__ == "__main__":
    unittest.main(verbosity=1)
