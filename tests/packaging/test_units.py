"""Unit tests for the packaging glue (stdlib unittest; run: python -m unittest discover -s tests/packaging).
Imports the package from src/ when it isn't installed."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from ctxprune import glue, installers as ins  # noqa: E402


class YamlPlugins(unittest.TestCase):
    def roundtrip(self, text: str, want: str) -> None:
        on = ins.yaml_plugins_edit(text, "ctxprune", True)
        self.assertEqual(on, want)
        self.assertEqual(ins.yaml_plugins_edit(on, "ctxprune", True), on)   # idempotent
        self.assertEqual(ins.yaml_plugins_edit(on, "ctxprune", False), text if "ctxprune" not in text else on)

    def test_block_list(self):
        self.roundtrip("a: 1\nplugins:\n  enabled:\n    - x\n    - y\nb: 2\n",
                       "a: 1\nplugins:\n  enabled:\n    - x\n    - y\n    - ctxprune\nb: 2\n")

    def test_block_list_dash_at_key_indent(self):
        self.roundtrip("plugins:\n  enabled:\n  - x\n", "plugins:\n  enabled:\n  - x\n  - ctxprune\n")

    def test_no_plugins_key(self):
        on = ins.yaml_plugins_edit("model:\n  default: x\n", "ctxprune", True)
        self.assertEqual(on, "model:\n  default: x\nplugins:\n  enabled:\n    - ctxprune\n")
        self.assertEqual(ins.yaml_plugins_edit(on, "ctxprune", False), "model:\n  default: x\nplugins:\n  enabled: []\n")

    def test_empty_flow_list(self):
        on = ins.yaml_plugins_edit("plugins:\n  enabled: []\n", "ctxprune", True)
        self.assertEqual(on, "plugins:\n  enabled:\n    - ctxprune\n")
        self.assertEqual(ins.yaml_plugins_edit(on, "ctxprune", False), "plugins:\n  enabled: []\n")

    def test_flow_list(self):
        on = ins.yaml_plugins_edit("plugins:\n  enabled: [a, b]\n", "ctxprune", True)
        self.assertEqual(on, "plugins:\n  enabled:\n    - a\n    - b\n    - ctxprune\n")

    def test_plugins_without_enabled(self):
        on = ins.yaml_plugins_edit("plugins:\n  disabled:\n    - z\n", "ctxprune", True)
        self.assertEqual(on, "plugins:\n  enabled:\n    - ctxprune\n  disabled:\n    - z\n")

    def test_refuses_inline(self):
        with self.assertRaises(ins.InstallError):
            ins.yaml_plugins_edit("plugins: {enabled: [a]}\n", "ctxprune", True)

    def test_last_item_removed(self):
        self.assertEqual(ins.yaml_plugins_edit("plugins:\n  enabled:\n    - ctxprune\nx: 1\n", "ctxprune", False),
                         "plugins:\n  enabled: []\nx: 1\n")


class Blocks(unittest.TestCase):
    def test_add_strip(self):
        base = 'model = "x"\n'
        a = ins._add_block(base, "[t]\nk = 1")
        self.assertIn(ins.BLOCK_BEGIN, a)
        self.assertEqual(ins._add_block(a, "[t]\nk = 1"), a)
        self.assertEqual(ins._strip_block(a), base)
        self.assertEqual(ins._add_block("", "x"), f"{ins.BLOCK_BEGIN}\nx\n{ins.BLOCK_END}\n")


class ClaudeCode(unittest.TestCase):
    def test_strip_ours(self):
        cfg = {"hooks": {"PostToolUse": [
            {"matcher": "Bash", "hooks": [{"command": "echo mine"}, {"command": "python3 /x/scripts/v2_hook.py"}]},
            {"matcher": "Bash|mcp__.*", "hooks": [{"command": '"/u/bin/ctxprune" hook claude-code'}]}]}}
        got = ins._cc_strip(cfg)
        self.assertEqual(got, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo mine"}]}]}})
        self.assertEqual(ins._cc_strip({"hooks": {"PostToolUse": [cfg["hooks"]["PostToolUse"][1]]}}), {})
        self.assertFalse(ins.cc_is_ours({"command": "my_v2_hook.py"}))


    def test_strip_session_start_and_weekly_install(self):
        weekly = {"hooks": [{"type": "command", "command": '"/u/py" "/x/ctxprune/plugin_cli.py" hook claude-code-weekly'}]}
        mine = {"hooks": [{"command": "echo hello"}]}
        self.assertEqual(ins._cc_strip({"hooks": {"SessionStart": [mine, weekly]}}), {"hooks": {"SessionStart": [mine]}})
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": td}), \
                mock.patch.object(ins, "claude_version", lambda *a: None):
            ins.install_claude_code({"weekly": True})
            with open(os.path.join(td, "settings.json")) as f:
                cfg = json.load(f)
            ss = [h["command"] for g in cfg["hooks"]["SessionStart"] for h in g["hooks"]]
            self.assertEqual(len(ss), 1)
            self.assertTrue(ss[0].endswith("hook claude-code-weekly"))
            ins.install_claude_code({})   # default: no SessionStart hook
            with open(os.path.join(td, "settings.json")) as f:
                self.assertNotIn("SessionStart", json.load(f)["hooks"])


class Stats(unittest.TestCase):
    def test_cc_input_and_share(self):
        from ctxprune import stats
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "proj"))
            rows = [{"type": "assistant", "timestamp": "2026-10-18T10:00:00.000Z",
                     "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 890,
                                                       "cache_read_input_tokens": 5000}}},
                    {"type": "assistant", "timestamp": "2026-10-18T10:00:01.000Z",   # same response, second block
                     "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 890}}},
                    {"type": "user", "message": {"content": "usage"}},
                    {"type": "assistant", "timestamp": "2020-01-01T00:00:00Z",   # before the window
                     "message": {"id": "m0", "usage": {"input_tokens": 99999}}}]
            with open(os.path.join(td, "proj", "s.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            cc = stats.cc_input(1_700_000_000.0, td)
            self.assertEqual(cc, {"requests": 1, "fresh": 10, "cache_write": 890, "cache_read": 5000})
            self.assertAlmostEqual(stats.share(100.0, cc), 10.0)
            self.assertIsNone(stats.share(100.0, None))
            self.assertIsNone(stats.cc_input(0.0, os.path.join(td, "missing")))

    def test_weekly_hook_once_a_week_and_never_for_the_model(self):
        import io
        from ctxprune import cli
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "CTXPRUNE_STATE": os.path.join(td, "state"), "CTXPRUNE_STATS": os.path.join(td, "stats.jsonl"),
                "CLAUDE_CONFIG_DIR": os.path.join(td, "cc")}):
            import time
            with open(os.environ["CTXPRUNE_STATS"], "w") as f:
                f.write(json.dumps({"ts": time.time(), "h": "claude-code", "t": "Bash", "b": 8000, "a": 4000}) + "\n")
            outs = []
            for _ in range(2):
                buf = io.StringIO()
                with mock.patch.object(sys, "stdin", io.StringIO("{}")), mock.patch.object(sys, "stdout", buf):
                    self.assertEqual(cli._hook(["claude-code-weekly"]), 0)
                outs.append(buf.getvalue())
            msg = json.loads(outs[0])
            self.assertEqual(list(msg), ["systemMessage"])   # user-facing only: no additionalContext
            self.assertIn("1,000 tokens saved", msg["systemMessage"])
            self.assertEqual(outs[1], "")   # at most once a week


class Glue(unittest.TestCase):
    def test_preset_precedence(self):
        keys = ("CTXPRUNE_PRESET", "CTXPRUNE_NO_MODEL", "CTXPRUNE_DEDUP", "CTXPRUNE_FORCE_LOSSY")
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"CTXPRUNE_CONFIG_DIR": td}):
            for k in keys:
                os.environ.pop(k, None)
            self.assertEqual(glue.preset_name(), "safe")   # ctxprune_core.DEFAULT_PRESET
            self.assertFalse(any(glue.resolved(h)["model"] for h in ins.HARNESSES))
            with open(os.path.join(td, "settings.json"), "w") as f:
                json.dump({"preset": "balanced"}, f)
            self.assertEqual(glue.preset_name(), "balanced")
            self.assertTrue(glue.resolved("claude-code")["model"])
            self.assertFalse(glue.resolved("codex")["model"])   # per-harness opt-out from the core table
            os.environ["CTXPRUNE_NO_MODEL"] = "1"
            self.assertFalse(glue.resolved("claude-code")["model"])
            os.environ.pop("CTXPRUNE_NO_MODEL")
            os.environ["CTXPRUNE_PRESET"] = "safe"
            self.assertFalse(glue.resolved("claude-code")["model"])
            os.environ["CTXPRUNE_PRESET"] = "nonsense"
            self.assertEqual(glue.preset_name(), "safe")
            os.environ.pop("CTXPRUNE_PRESET")

    def test_dedup_by_model(self):
        # the preset table says "model" (ctxprune_core.DEDUP_MODELS decides per session); only CTXPRUNE_DEDUP forces it
        for env, want, flag in (("", "model", []), ("1", True, ["--dedup", "on"]), ("0", False, ["--dedup", "off"])):
            with mock.patch.dict(os.environ, {"CTXPRUNE_DEDUP": env}):
                v = glue.resolved("claude-code")
                self.assertEqual(v["dedup"], want, env)
                self.assertEqual(glue.dedup_flag(v), flag, env)

    def test_context_wrapper_passes_new_args(self):
        # glue wraps ctxprune_compress.context for stats; it must forward seed= / model= / dedup= (12c7b79)
        import types
        seen = {}
        fake = types.SimpleNamespace(context=lambda events, harness=None, log=None, seed=None, model=None, dedup=None:
                                     (seen.update(seed=seed, model=model, dedup=dedup) or {}, {}))
        with mock.patch.dict(sys.modules, {"ctxprune_compress": fake}):
            glue._patch_context()
            fake.context([], "pi", None, seed="s", model="claude-opus-5-5", dedup=True)
        self.assertEqual(seen, {"seed": "s", "model": "claude-opus-5-5", "dedup": True})

    def test_off(self):
        with mock.patch.dict(os.environ, {"CTXPRUNE_OFF": ""}):
            self.assertFalse(glue.off("ls"))
            self.assertTrue(glue.off("CTXPRUNE_OFF=1 ls"))
        with mock.patch.dict(os.environ, {"CTXPRUNE_OFF": "1"}):
            self.assertTrue(glue.off("ls"))

    def test_record_counts_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s", "stats.jsonl")
            with mock.patch.dict(os.environ, {"CTXPRUNE_STATS": p}):
                glue.record({"before": 100, "after": 40, "stage": "lossless", "cmd": "cat secret",
                             "probe": "SECRET"}, harness="h", tool="Bash")
            with open(p) as f:
                raw = f.read()
            self.assertEqual(set(json.loads(raw)), {"ts", "h", "t", "b", "a", "d", "s", "v"})
            self.assertNotIn("secret", raw.lower())

    def test_runtime_dir(self):
        self.assertTrue(os.path.isfile(os.path.join(glue.runtime_dir(), "ctxprune_core.py")))

    def test_ephemeral(self):
        with mock.patch.object(sys, "prefix", "/home/u/.cache/uv/archive-v0/abc"):
            self.assertTrue(ins.ephemeral_env())
        with mock.patch.object(sys, "prefix", "/tmp/x/uvcache/archive-v0/AA4f4RekjK3x19he"):
            self.assertTrue(ins.ephemeral_env())
        with mock.patch.object(sys, "prefix", "/home/u/.local/share/uv/tools/ctxprune"):
            self.assertIsNone(ins.ephemeral_env())


if __name__ == "__main__":
    unittest.main()


class Wrap(unittest.TestCase):
    """`ctxprune wrap/unwrap`: the one-liner over install/uninstall, with the config diff."""

    def run_cli(self, home, *args):
        import io
        import contextlib
        from ctxprune import cli
        env = {"HOME": home, "USERPROFILE": home, "PATH": os.environ.get("PATH", ""), "CTXPRUNE_WRAP_NO_BOOTSTRAP": "1"}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(buf), \
                mock.patch.object(ins, "ephemeral_env", return_value=None):
            rc = cli.main(list(args))
        return rc, buf.getvalue()

    def test_wrap_idempotent_and_clean_unwrap(self):
        with tempfile.TemporaryDirectory() as home:
            rc, out = self.run_cli(home, "wrap", "claude")
            self.assertEqual(rc, 0)
            self.assertIn("config diff:", out)
            cfg = os.path.join(home, ".claude", "settings.json")
            first = open(cfg).read()
            self.assertIn("hook claude-code", first)
            rc, out = self.run_cli(home, "wrap", "claude")
            self.assertIn("already up to date", out)
            self.assertIn("config diff: none", out)
            self.assertEqual(open(cfg).read(), first)
            rc, out = self.run_cli(home, "unwrap", "claude")
            self.assertEqual(rc, 0)
            self.assertNotIn("ctxprune", open(cfg).read())

    def test_unknown_agent_and_scope(self):
        with tempfile.TemporaryDirectory() as home:
            for args in (("wrap", "nope"), ("wrap", "pi", "--scope", "project")):
                with self.assertRaises(SystemExit) as c:
                    import io
                    import contextlib
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.run_cli(home, *args)
                self.assertEqual(c.exception.code, 2)

    def test_install_still_refuses_ephemeral(self):
        from ctxprune import cli
        import io
        import contextlib
        with mock.patch.object(ins, "ephemeral_env", return_value="/x/archive-v0/y"), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["install", "claude-code"]), 2)
