"""T1.12: preset `digest` end to end (scripts/digest.py + ctxprune_core.process)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
import ctxprune_core as core
import digest


def _big_json():
    rows = [{"id": i, "ms": 10 + (i % 7), "status": "ok"} for i in range(400)]
    rows[200]["status"] = "error: boom"
    return json.dumps({"results": rows}, indent=1)


def test_preset_registered():
    assert "digest" in core.PRESETS and core.resolve_preset("digest")["digest"] is True
    assert not core.resolve_preset("safe").get("digest")


def test_digest_saves_original_and_discloses():
    text = _big_json()
    with tempfile.TemporaryDirectory() as td:
        s = core.Settings.from_env("digest", state=td)
        assert s.digest and not s.model
        out, rec = core.process(text, "curl -s http://x/api", settings=s)
        assert rec.get("digest"), rec
        assert len(out) < 0.6 * len(text) and "ctxprune digest" in out
        path = out.rsplit("saved at ", 1)[1].split(" (CTXPRUNE_OFF")[0]
        assert open(path).read() == text          # exact original on disk
        assert "error: boom" in out               # the error row survives


def test_safe_preset_untouched_and_small_input_skipped():
    text = _big_json()
    with tempfile.TemporaryDirectory() as td:
        out, rec = core.process(text, "x", settings=core.Settings.from_env("safe", state=td))
        assert "digest" not in rec
        out, rec = core.process("hello\n" * 10, "x", settings=core.Settings.from_env("digest", state=td))
        assert "digest" not in rec


def test_total_on_garbage():
    for t in ["", "\x00" * 5000, "{" * 4000, "@@ -1 +1 @@\n" * 300]:
        new, ids = digest.digest(t)
        assert isinstance(new, str) and len(new) <= len(t)
