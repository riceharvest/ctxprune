import json

import pytest

from ctxprune import hook

BIG = "worker 5f2d8e7a-1c4b failed to reach 10.0.4.17:5432 while the scheduler retried the job again. " * 120


@pytest.fixture
def fake_compress(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(hook, "_compress", lambda texts, rate: [t[: int(len(t) * rate)] for t in texts])


def ev(tool, resp, cmd="make test"):
    return {"tool_name": tool, "tool_input": {"command": cmd}, "tool_response": resp}


def test_bash_output_is_replaced_with_same_shape(fake_compress, tmp_path):
    resp = {"stdout": BIG, "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False}
    out = hook.run(ev("Bash", resp), 0.5, 6000)["hookSpecificOutput"]
    new = out["updatedToolOutput"]
    assert set(new) == set(resp) and new["interrupted"] is False
    assert len(new["stdout"]) == len(BIG) // 2
    saved = out["additionalContext"].split("full output is at ")[1].split(" ")[0]
    assert open(saved).read() == BIG


@pytest.mark.parametrize("cmd", ["cat src/app.py", "cd x && sed -n 1,200p a.py", "git diff HEAD~1", "CTXPRUNE_OFF=1 make test",
                                 "head -500 big.log"])
def test_verbatim_commands_are_skipped(fake_compress, cmd):
    assert hook.run(ev("Bash", {"stdout": BIG, "stderr": ""}, cmd), 0.5, 6000) is None


def test_small_outputs_and_other_tools_are_skipped(fake_compress):
    assert hook.run(ev("Bash", {"stdout": "ok", "stderr": ""}), 0.5, 6000) is None
    assert hook.run(ev("Read", BIG), 0.5, 6000) is None
    assert hook.run(ev("mcp__filesystem__read_text_file", [{"type": "text", "text": BIG}]), 0.5, 6000) is None


def test_mcp_content_blocks(fake_compress):
    resp = [{"type": "text", "text": BIG}, {"type": "image", "data": "..."}]
    new = hook.run(ev("mcp__search__query", resp), 0.5, 6000)["hookSpecificOutput"]["updatedToolOutput"]
    assert new[1] == resp[1] and len(new[0]["text"]) == len(BIG) // 2
    assert hook.run(ev("mcp__search__query", BIG), 0.5, 6000)["hookSpecificOutput"]["updatedToolOutput"] == BIG[: len(BIG) // 2]


def test_install_uninstall_roundtrip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / ".claude" / "settings.local.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"model": "x", "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}]}}))
    hook.install("local")
    hook.install("local")  # idempotent
    cfg = json.loads(p.read_text())
    assert cfg["model"] == "x" and len(cfg["hooks"]["PostToolUse"]) == 2
    hook.uninstall("local")
    assert json.loads(p.read_text()) == {"model": "x", "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}]}}


def test_hook_fails_open(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    hook.main_claude_code()
    assert capsys.readouterr().out == ""
