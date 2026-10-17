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
