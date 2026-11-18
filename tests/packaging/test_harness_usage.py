"""Per-harness usage readers (`ctxprune stats`) and static-overhead checks (`ctxprune audit`) on synthetic
fixtures: one throwaway HOME per test, laid out the way each harness writes its files. Numbers and names only."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from ctxprune import harness_audit as ha, stats, usage  # noqa: E402

# a fixed date failed test_stats_per_harness once the wall clock passed it + 1 h (its window starts an hour ago)
NOW = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 600))
SECRET = "SECRET-TEXT-never-printed"


def _w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _jl(rows: list) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


class Home(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="ctxprune-hu-")
        self.env = mock.patch.dict(os.environ, {
            "HOME": self.td, "USERPROFILE": self.td, "CLAUDE_CONFIG_DIR": os.path.join(self.td, ".claude"),
            "CODEX_HOME": os.path.join(self.td, ".codex"), "PI_CODING_AGENT_DIR": os.path.join(self.td, ".pi", "agent"),
            "XDG_DATA_HOME": os.path.join(self.td, ".local", "share"), "XDG_CONFIG_HOME": os.path.join(self.td, ".config"),
            "HERMES_HOME": os.path.join(self.td, ".hermes"), "DSH_HOME": os.path.join(self.td, ".dsh"),
            "CTXPRUNE_STATS": os.path.join(self.td, "stats.jsonl"), "CTXPRUNE_STATE": os.path.join(self.td, "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    # ---- fixtures, as each harness lays them out
    def codex(self):
        rows = [{"timestamp": NOW, "type": "session_meta", "payload": {"model_provider": "openai"}},
                {"timestamp": NOW, "type": "response_item", "payload": {"type": "function_call",
                                                                       "name": "exec_command", "arguments": SECRET}},
                {"timestamp": NOW, "type": "response_item", "payload": {"type": "function_call",
                                                                       "name": "mcp__fetch__get"}}]
        tot = 0
        for inp, cached in ((5000, 0), (6000, 4800), (7000, 5900)):
            tot += inp
            ev = {"timestamp": NOW, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 10},
                "total_token_usage": {"input_tokens": tot}}}}
            rows += [ev, ev]   # a repeated event (rate-limit refresh) counts once
        rows.append({"timestamp": NOW, "type": "event_msg", "payload": {"type": "token_count", "info": None}})
        _w(os.path.join(self.td, ".codex", "sessions", "2026", "10", "18", "rollout-x.jsonl"), _jl(rows))
        _w(os.path.join(self.td, ".codex", "config.toml"),
           'model = "x"\n[mcp_servers.fetch]\ncommand = "f"\n[mcp_servers.fetch.env]\nK = "v"\n'
           '[mcp_servers."brave-search"]\ncommand = "b"\n')
        _w(os.path.join(self.td, ".codex", "AGENTS.md"), "x" * 8000)

    def pi(self):
        rows = [{"type": "session", "timestamp": NOW},
                {"type": "message", "timestamp": NOW, "message": {"role": "user", "content": SECRET}},
                {"type": "message", "timestamp": NOW, "provider": "openrouter", "message": {
                    "role": "assistant", "content": [{"type": "toolCall", "name": "bash", "arguments": {}}],
                    "usage": {"input": 900, "cacheRead": 0, "cacheWrite": 100, "output": 5}}},
                {"type": "message", "timestamp": NOW, "message": {
                    "role": "assistant", "provider": "openrouter", "content": [],
                    "usage": {"input": 300, "cacheRead": 1000, "cacheWrite": 0, "output": 5}}}]
        _w(os.path.join(self.td, ".pi", "agent", "sessions", "--proj--", "s.jsonl"), _jl(rows))
        _w(os.path.join(self.td, ".pi", "agent", "extensions", "ctxprune.ts"), "//")

    def opencode(self, legacy: bool = False):
        root = os.path.join(self.td, ".local", "share", "opencode")
        msgs = [{"role": "user", "time": {"created": 1792310400000}},
                {"role": "assistant", "providerID": "anthropic", "time": {"created": 1792310400000},
                 "tokens": {"input": 2000, "output": 3, "cache": {"read": 0, "write": 8000}}},
                {"role": "assistant", "providerID": "anthropic", "time": {"created": 1792310401000},
                 "tokens": {"input": 500, "output": 3, "cache": {"read": 10000, "write": 0}}}]
        if legacy:
            for i, m in enumerate(msgs):
                _w(os.path.join(root, "storage", "message", "ses_1", f"msg_{i}.json"), json.dumps(dict(m, sessionID="ses_1")))
        else:
            os.makedirs(root, exist_ok=True)
            c = sqlite3.connect(os.path.join(root, "opencode.db"))
            c.execute("create table message (id text, session_id text, time_created int, time_updated int, data text)")
            c.execute("create table part (id text, message_id text, session_id text, data text)")
            for i, m in enumerate(msgs):
                c.execute("insert into message values (?,?,?,?,?)", (f"m{i}", "ses_1", i, i, json.dumps(m)))
            c.execute("insert into part values (?,?,?,?)", ("p", "m1", "ses_1", json.dumps({"type": "tool", "tool": "github_get_file"})))
            c.commit()
            c.close()
        _w(os.path.join(self.td, ".config", "opencode", "opencode.json"),
           '{\n // comment\n "mcp": {"github": {"type": "local"}, "off": {"enabled": false}},\n "plugin": ["x-plugin"],\n}\n')

    def hermes(self):
        sess = {"session_id": "s", "base_url": "https://api.deepseek.com/v1", "model": "m", "system_prompt": "s" * 4000,
                "tools": [{"type": "function", "function": {"name": "terminal"}}],
                "messages": [{"role": "user", "content": SECRET},
                             {"role": "assistant", "content": "a" * 400, "tool_calls": [
                                 {"function": {"name": "mcp_brave_search_web", "arguments": "{}"}}]},
                             {"role": "tool", "content": "t" * 1600}, {"role": "assistant", "content": "done"}]}
        _w(os.path.join(self.td, ".hermes", "sessions", "session_20261018_100000_abc.json"), json.dumps(sess))
        _w(os.path.join(self.td, ".hermes", "sessions", "20261018_100000_abc.jsonl"), "{}\n")   # not read
        _w(os.path.join(self.td, ".hermes", "state.db"), "not read")
        _w(os.path.join(self.td, ".hermes", "config.yaml"),
           "model:\n  default: x\nmcp_servers:\n  brave_search:\n    command: b\n  unused-srv:\n    url: u\n"
           "plugins:\n  enabled:\n    - ctxprune\n    - other\n")

    # ---- usage readers
    def test_codex(self):
        self.codex()
        u = usage.read_codex(0)
        self.assertEqual((u["requests"], u["new_input"], u["cache_read"]), (3, 5000 + 1200 + 1100, 10700))
        self.assertEqual(u["first_input"], [5000])
        self.assertEqual(u["tools"], {"exec_command": 1, "mcp__fetch__get": 1})
        self.assertEqual(u["providers"], {"openai": 1})

    def test_pi(self):
        self.pi()
        u = usage.read_pi(0)
        self.assertEqual((u["requests"], u["new_input"], u["cache_read"]), (2, 1000 + 300, 1000))
        self.assertEqual(u["tools"], {"bash": 1})

    def test_opencode_db_and_legacy(self):
        self.opencode()
        u = usage.read_opencode(0)
        self.assertEqual((u["requests"], u["new_input"], u["cache_read"], u["first_input"]),
                         (2, 10000 + 500, 10000, [10000]))
        self.assertEqual(u["tools"], {"github_get_file": 1})
        os.remove(os.path.join(self.td, ".local", "share", "opencode", "opencode.db"))
        self.opencode(legacy=True)
        u = usage.read_opencode(0)
        self.assertEqual((u["requests"], u["new_input"]), (2, 10500))

    def test_hermes_chars_from_session_files_only(self):
        self.hermes()
        u = usage.read_hermes(0)
        self.assertEqual(u["unit"], "chars")
        self.assertEqual(u["requests"], 2)
        static = 4000 + len(json.dumps([{"type": "function", "function": {"name": "terminal"}}]))
        self.assertEqual(u["first_input"], [static])
        self.assertEqual(u["new_input"], static + len(SECRET) + 400 + 2 + 1600 + 4)
        self.assertEqual(u["providers"], {"api.deepseek.com": 1})

    def test_window(self):
        self.codex()
        self.assertIsNone(usage.read_codex(time.time() + 3600))

    # ---- stats: one row per harness + total
    def test_stats_per_harness(self):
        self.codex()
        self.pi()
        self.hermes()
        ts = time.time() - 3600   # the first logged ctxprune row: the window starts there
        rows = [{"ts": ts, "h": "codex", "t": "exec", "b": 4000, "a": 2000},
                {"ts": ts, "h": "pi", "t": "bash", "b": 1000, "a": 600},
                {"ts": ts, "h": "hermes", "t": "terminal", "b": 1000, "a": 600},
                {"ts": ts, "h": "pi", "s": "context", "d": 400},
                {"ts": ts, "h": "dsh", "t": "bash", "b": 800, "a": 400}]
        per = stats.per_harness(rows, 0)
        self.assertEqual(per["codex"]["tokens_saved"], 500)
        self.assertAlmostEqual(per["codex"]["share_pct"], 100 * 500 / (7300 + 500), places=1)
        self.assertEqual(per["pi"]["calls"], 1)
        self.assertEqual(per["pi"]["tokens_saved"], 200)   # 400 chars + 400 request-dedup chars
        self.assertIn("share_pct", per["hermes"])
        self.assertNotIn("share_pct", per["dsh"])
        self.assertEqual(per["total"]["calls"], 4)
        self.assertIsNotNone(per["total"]["share_pct"])
        per = stats.per_harness(rows, 0, transcripts=False)
        self.assertTrue(all(v.get("share_pct") is None for v in per.values()))

    # ---- audit: static overhead per harness
    def test_audit_codex(self):
        self.codex()
        r = ha.audit_harness("codex", 3650, cwd=self.td)
        txt = ha.report([r])
        self.assertIn("first request of each session", txt)
        self.assertIn("MCP server 'fetch': 1 tool calls", txt)
        self.assertIn("MCP server 'brave-search': 0 tool calls", txt)
        self.assertIn("~/.codex/AGENTS.md: ~2,000 tokens", txt)
        self.assertIn("1,024 tokens", txt)   # OpenAI caching advice
        self.assertIn("[verified]", txt)
        self.assertIn("[unverified]", txt)
        self.assertNotIn(SECRET, txt)

    def test_audit_opencode_pi_hermes_dsh(self):
        self.opencode()
        self.pi()
        self.hermes()
        _w(os.path.join(self.td, ".dsh", "cordis.patch.yml"), "- insert: x\n")
        res = [ha.audit_harness(h, 3650, cwd=self.td) for h in ha.HARNESSES]
        txt = ha.report(res)
        self.assertIn("MCP server 'github': 1 tool calls", txt)
        self.assertNotIn("'off'", txt)   # disabled in opencode.json
        self.assertIn("1 plugins: x-plugin", txt)
        self.assertIn("1 extensions: ctxprune.ts", txt)
        self.assertIn("MCP server 'brave_search': 1 tool calls", txt)
        self.assertIn("MCP server 'unused-srv': 0 tool calls", txt)
        self.assertIn("2 plugins enabled: ctxprune, other", txt)
        self.assertIn("api.deepseek.com", txt)
        self.assertIn("dsh", txt)
        self.assertNotIn(SECRET, txt)
        self.assertFalse(ha.audit_harness("codex", 30, cwd=self.td)["found"])   # no ~/.codex here

    def test_doctor_summary(self):
        import contextlib
        import io
        from ctxprune import doctor
        r = doctor.Report.__new__(doctor.Report)
        r.rows, r.fail, r.warn = [("OK", "pi canary", ""), ("WARN", "codex proxy", "x"), ("OK", "preset", "")], 0, 1
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(doctor.ins, "detected", lambda: ["pi", "hermes"]):
            doctor.summary(r, {"harnesses": {"pi": {}, "codex": {}}})
        out = buf.getvalue()
        self.assertRegex(out, r"(?m)^  pi\s+installed\s+OK\b")
        self.assertRegex(out, r"(?m)^  codex\s+installed\s+WARN\b")
        self.assertRegex(out, r"(?m)^  hermes\s+found, not installed\s+-")
        self.assertRegex(out, r"(?m)^  dsh\s+not found")

    def test_parsers(self):
        self.assertEqual(ha.codex_mcp('[mcp_servers.a]\n[mcp_servers.a.env]\n[mcp_servers."b c"]\n'), ["a", "b c"])
        self.assertEqual(ha.yaml_top_keys("x: 1\nmcp_servers:\n  a:\n    k: 1\n  'b':\n    k: 2\ny: 2\n",
                                          "mcp_servers"), ["a", "b"])
        self.assertEqual(ha.yaml_top_keys("mcp_servers: {}\n", "mcp_servers"), [])
        self.assertEqual(ha.hermes_enabled_plugins("plugins:\n  enabled: [a, 'b']\n"), ["a", "b"])
        self.assertEqual(json.loads(ha._strip_jsonc('{"a": "//x", /* c */ "b": [1,],}')), {"a": "//x", "b": [1]})


if __name__ == "__main__":
    unittest.main()
