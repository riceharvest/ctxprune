"""Tests for the Hermes plugin's per-tool routing (scripts/integrations/hermes/ctxprune): read_file view-safe,
terminal wrapper unescape on exit 0, write_file stays JSON, Hermes failure-classification guard, sys.path append.
No model (CTXPRUNE_NO_MODEL=1). Run: python3 -I ~/ctxprune-v2/scripts/test_hermes_plugin.py      (< 2 s)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="ctxprune-hermes-test-")
plug = None
N_PATH = 0
_SAVED_PATH: list = []
_SAVED_ENV: dict = {}


def setUpModule():
    """Not at import time: `python -m unittest a b c` imports every test module first, and the ones after this one
    still need HERE on sys.path and their own env (no CTXPRUNE_NO_MODEL). tearDownModule puts both back."""
    global plug, N_PATH
    _SAVED_ENV.update(os.environ)
    os.environ.update(CTXPRUNE_SCRIPTS=HERE, CTXPRUNE_NO_MODEL="1", CTXPRUNE_STATE=TMP,
                      CTXPRUNE_LOG=os.path.join(TMP, "log.jsonl"))
    for k in ("CTXPRUNE_SKIP_TOOLS", "CTXPRUNE_VIEW_TOOLS"):
        os.environ.pop(k, None)
    _SAVED_PATH[:] = sys.path
    while HERE in sys.path:   # (unittest adds it) the plugin must add it itself, at the end
        sys.path.remove(HERE)
    N_PATH = len(sys.path)
    spec = importlib.util.spec_from_file_location(
        "hermes_ctxprune", os.path.join(HERE, "integrations", "hermes", "ctxprune", "__init__.py"))
    plug = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plug)


def tearDownModule():
    sys.path[:] = _SAVED_PATH + [p for p in sys.path if p not in _SAVED_PATH]
    os.environ.clear()
    os.environ.update(_SAVED_ENV)

CODE = [f'    log("step {i}", key="v")' for i in range(300)]
PADDED = "\n".join(f"{i + 1:6d}|{ln}" for i, ln in enumerate(CODE))          # older Hermes gutter
COMPACT = "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(CODE))             # current Hermes gutter


def read_result(content, **extra):
    return json.dumps({"content": content, "total_lines": len(CODE), "file_size": 9000, "truncated": False, **extra},
                      ensure_ascii=False)


class HermesPluginTest(unittest.TestCase):
    def test_read_file_view_safe(self):
        plug._cc()
        from lossless2 import decode
        for content, numbered in ((PADDED, True), (COMPACT, False)):
            raw = read_result(content)
            out = plug.on_tool_result("read_file", {"path": "a.py"}, raw, status="ok")
            self.assertIsNotNone(out)
            head, body = out.split("\n", 1)
            self.assertTrue(head.startswith('[ctxprune: unescaped JSON string field "content"; rest'))
            self.assertIn('"total_lines":300', head)
            self.assertEqual(body.startswith("[cp:numbered"), numbered)
            self.assertEqual(decode(body), content)                     # code bytes exact
            self.assertLess(len(out), len(raw))
        with open(os.environ["CTXPRUNE_LOG"]) as f:
            recs = [json.loads(x) for x in f]
        self.assertTrue(any(r["tool"] == "read_file" and r["v2"] == "view" and "l2_numbered" in r["rules"]
                            for r in recs))

    def test_read_file_failures_untouched(self):
        self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(PADDED, error="File not found: a")))
        self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(PADDED), status="error",
                                              error_type="tool_error"))
        self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(PADDED, success=False)))
        # code with "error" in quotes near the top: unescaped, Hermes would classify the read as failed -> skip
        bad = '1|raise ValueError("error")\n' + COMPACT
        self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(bad)))

    def test_terminal_wrapper_exit0_only(self):
        ok = json.dumps({"output": COMPACT, "exit_code": 0, "error": None})
        out = plug.on_tool_result("terminal", {"command": "cat a"}, ok)
        self.assertIsNotNone(out)
        self.assertRegex(out.split("\n", 1)[0], r'"exit_code"\s*:\s*0')   # Hermes compaction stub regex
        self.assertEqual(out.split("\n", 1)[1], COMPACT)
        self.assertIsNone(plug.on_tool_result("terminal", {}, json.dumps({"output": COMPACT, "exit_code": 2})))
        self.assertIsNone(plug.on_tool_result("terminal", {}, ok, status="error", error_type="x"))

    def test_kill_switch(self):   # live canary 2026-10-17: CTXPRUNE_OFF=1 was ignored by the plugin
        ok = json.dumps({"output": COMPACT, "exit_code": 0, "error": None})
        os.environ["CTXPRUNE_OFF"] = "1"
        try:
            self.assertIsNone(plug.on_tool_result("terminal", {"command": "cat a"}, ok))
            self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(PADDED)))
            self.assertIsNone(plug.on_terminal_output("grep -rn x .", "a/b.py:1:x\n" * 50, 0))
        finally:
            os.environ.pop("CTXPRUNE_OFF")
        self.assertIsNotNone(plug.on_tool_result("terminal", {"command": "cat a"}, ok))

    def test_patch_and_write_file_stay_json(self):
        big = json.dumps({"success": True, "diff": "\n".join(f"+line {i}" for i in range(500))})
        self.assertIsNone(plug.on_tool_result("patch", {}, big))
        w = json.dumps({"bytes_written": 10, "lint": "\n".join(f"warning {i}: x" for i in range(500))})
        out = plug.on_tool_result("write_file", {"path": "a"}, w)
        self.assertTrue(out is None or isinstance(json.loads(out), dict))

    def test_skill_view_and_write_file_lossless_only(self):
        from unittest import mock
        cc = plug._cc()
        seen = []
        real = cc.run
        with mock.patch.object(cc, "run", side_effect=lambda *a, **k: seen.append(k.get("no_model")) or real(*a, **k)):
            plug.on_tool_result("skill_view", {"name": "s"}, "x\n" * 5000)
            plug.on_tool_result("write_file", {"path": "a"}, "y\n" * 5000)
            plug.on_tool_result("search_files", {"pattern": "p"}, "z\n" * 5000)
        self.assertEqual(seen, [True, True, None])   # None = the env decides (model allowed) for other tools

    def test_skill_view_normal_path(self):
        doc = "\n".join(f"Step {i}: do the thing with care" for i in range(400))
        out = plug.on_tool_result("skill_view", {"name": "s"}, json.dumps({"success": True, "content": doc}))
        self.assertIsNotNone(out)
        self.assertIn("Step 399: do the thing with care", out)
        self.assertIsNone(plug.on_tool_result("skill_manage", {}, json.dumps({"success": True, "content": doc})))

    def test_time_budget_slow_compress(self):   # live canary finding 4: in-process, so a slow compress stalled Hermes
        import time
        from unittest import mock
        slow = lambda *a, **k: (time.sleep(3), "never used")[1]
        os.environ["CTXPRUNE_BUDGET"] = "0.3"
        try:
            with mock.patch.object(plug, "_compress", slow):
                t0 = time.time()
                self.assertIsNone(plug.on_terminal_output("ls", "x\n" * 100, 0))
                self.assertIsNone(plug.on_tool_result("search_files", {"pattern": "p"}, "z\n" * 5000))
                self.assertLess(time.time() - t0, 1.5)
            self.assertEqual(plug._STUCK[0], 2)                  # still running in the background ...
            time.sleep(3.2)
            self.assertEqual(plug._STUCK[0], 0)                  # ... and accounted for once they finish
            out = plug.on_tool_result("read_file", {}, read_result(PADDED))   # fast path still within budget
            self.assertIsNotNone(out)
        finally:
            os.environ.pop("CTXPRUNE_BUDGET")

    def test_rewrites_recorded_for_stacked_proxy(self):   # canary finding 2: proxy forwards these untouched
        from emitted import seen
        out = plug.on_tool_result("read_file", {}, read_result(PADDED))
        self.assertTrue(out and seen(out))
        t = plug.on_terminal_output("rg -n User src", "\n".join(f"src/a.py:{i}:x = {i}" for i in range(1, 60)), 0)
        self.assertTrue(t and seen(t))

    def test_search_files_files_mode_and_rpc_keeps_json(self):
        files = [f"src/hermes/tools/module_{i}.py" for i in range(40)] + [f"tests/test_{i}.py" for i in range(10)]
        res = json.dumps({"total_count": 50, "files": files}, ensure_ascii=False)
        out = plug.on_tool_result("search_files", {"pattern": "*.py", "target": "files"}, res, tool_call_id="call_1")
        self.assertIsNotNone(out)
        self.assertIn("[cp:files below, one per line]", out.split("\n")[0])
        self.assertIn("module_7.py", out)
        sys.path.append(HERE)
        from lossless import decode
        self.assertEqual(decode(out), res)
        # execute_code RPC (Hermes passes tool_call_id="" there): a JSON result stays JSON (#90322)
        self.assertIsNone(plug.on_tool_result("search_files", {"pattern": "*.py"}, res, tool_call_id=""))
        self.assertIsNone(plug.on_tool_result("read_file", {}, read_result(PADDED), tool_call_id=""))

    def test_paths_off_for_local_model(self):   # results/netcost: paths costs local thinking readers latency
        listing = "\n".join(f"/home/u/proj/packages/frontend/src/components/widget_{i}.tsx" for i in range(12))
        plug.on_llm_request({"messages": []}, task_id="t-local", session_id="s-local", provider="custom",
                            base_url="http://127.0.0.1:8000/v1")
        plug.on_llm_request({"messages": []}, task_id="t-local", session_id="s-local", provider="anthropic",
                            base_url="https://api.anthropic.com")      # a later switch: first seen stays (cache)
        plug.on_llm_request({"messages": []}, task_id="t-api", session_id="s-api", provider="openrouter",
                            base_url="https://openrouter.ai/api/v1")
        local = plug.on_terminal_output("find /home/u/proj", listing, 0, task_id="t-local")
        api = plug.on_terminal_output("find /home/u/proj", listing, 0, task_id="t-api")
        self.assertTrue(local is None or "[cp:dir]" not in local)
        self.assertIn("[cp:dir]", api)
        files = json.dumps({"total_count": 12, "files": listing.split("\n")})
        out = plug.on_tool_result("search_files", {"pattern": "*.tsx"}, files, task_id="t-local", session_id="s-local",
                                  tool_call_id="c1")
        self.assertTrue(out is None or "[cp:dir]" not in out)       # sffiles needs the dir grouping (paths)
        out = plug.on_tool_result("search_files", {"pattern": "*.tsx"}, files, task_id="t-api", session_id="s-api",
                                  tool_call_id="c2")
        self.assertIn("[cp:files below", out)
        self.assertIn("[cp:dir]", out)
        plug._TARGETS.clear()                                          # the process fallback is the local model now
        plug._FIRST_TARGET[0] = ""

    def test_sys_path_appended(self):
        plug._cc()   # audit M4: appended after Hermes' own entries, never in front of them
        self.assertGreaterEqual(sys.path.index(HERE), N_PATH)


if __name__ == "__main__":
    unittest.main(verbosity=1)
