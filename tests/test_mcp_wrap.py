"""ctxprune mcp-wrap: transparent stdio proxy, lossless rewrite of tools/call text, fail open, guardrails."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = str(HERE.parent / "src")
FAKE = str(HERE / "fake_mcp_server.py")


def run(requests, wrap=True, extra=()):
    env = dict(os.environ, PYTHONPATH=SRC, CTXPRUNE_STATS="off")
    env.pop("CTXPRUNE_OFF", None)
    cmd = ([sys.executable, "-m", "ctxprune", "mcp-wrap", *extra, "--"] if wrap else []) + [sys.executable, FAKE]
    inp = "".join(json.dumps(r) + "\n" for r in requests)
    p = subprocess.run(cmd, input=inp, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return {m["id"]: m for m in map(json.loads, p.stdout.splitlines())}


def call(i, name):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": {}}}


REQS = [{"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, call(1, "list_items"), call(2, "read_file"),
        call(3, "structured"), call(4, "fail")]


def test_rewrites_and_passes_through():
    raw, wrapped = run(REQS, wrap=False), run(REQS)
    assert wrapped[0] == raw[0]                       # non tools/call untouched
    t_raw = raw[1]["result"]["content"][0]["text"]
    t_new = wrapped[1]["result"]["content"][0]["text"]
    assert len(t_new) < 0.8 * len(t_raw)
    sys.path.insert(0, SRC)
    from ctxprune import glue
    glue.boot(own_process=False)
    import lossless
    assert json.loads(lossless.decode(t_new)) == json.loads(t_raw)   # whitespace-only difference (json_compact)
    assert wrapped[2] == raw[2]                        # file read tool skipped
    assert wrapped[3] == raw[3]                        # structuredContent present: untouched
    assert wrapped[4] == raw[4]                        # isError untouched
    assert wrapped[1]["jsonrpc"] == "2.0" and wrapped[1]["id"] == 1


def test_structured_flag_keeps_structured_content():
    raw, wrapped = run(REQS, wrap=False), run(REQS, extra=["--structured"])
    assert wrapped[3]["result"]["structuredContent"] == raw[3]["result"]["structuredContent"]
    assert len(wrapped[3]["result"]["content"][0]["text"]) < len(raw[3]["result"]["content"][0]["text"])


def test_fail_open_on_garbage():
    sys.path.insert(0, SRC)
    from ctxprune.mcp_wrap import Transformer
    tf = Transformer()
    for line in (b"not json\n", b"[1,2]\n", b'{"id": 9, "result": 3}\n', b'{"result":{"content":5},"id":1}\n'):
        tf.pending["1"] = "x"
        assert tf.on_response(line) == line


def test_off_switch(monkeypatch):
    sys.path.insert(0, SRC)
    from ctxprune.mcp_wrap import Transformer
    monkeypatch.setenv("CTXPRUNE_OFF", "1")
    tf = Transformer()
    tf.on_request(json.dumps(call(1, "list_items")).encode())
    line = (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "x" * 3000}]}}) + "\n").encode()
    assert tf.on_response(line) == line
