import json, subprocess, sys, textwrap
from pathlib import Path

BACKEND = textwrap.dedent('''
    import json, sys
    name = sys.argv[1]
    tools = [{"name": f"{name}_op{i}", "description": f"Does operation {i} on {name}. " + "Long detail. " * 30,
              "inputSchema": {"type": "object", "required": ["a"], "properties": {
                  k: {"type": "string", "description": "parameter " + k + " " * 1 + "explained at length. " * 8}
                  for k in "abcdefgh"}}} for i in range(12)]
    for line in sys.stdin:
        m = json.loads(line); mid = m.get("id")
        if mid is None: continue
        if m["method"] == "initialize": r = {"protocolVersion": "2025-06-18", "capabilities": {}}
        elif m["method"] == "tools/list": r = {"tools": tools}
        else: r = {"content": [{"type": "text", "text": "x\\n" * 50 + json.dumps(m["params"])}]}
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": r}), flush=True)
''')


def test_gateway_roundtrip(tmp_path):
    (tmp_path / "b.py").write_text(BACKEND)
    cfg = {n: {"command": [sys.executable, str(tmp_path / "b.py"), n]} for n in ("gh", "pw", "notion")}
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    p = subprocess.Popen([sys.executable, "-m", "ctxprune", "mcp-gateway", "--config", str(tmp_path / "c.json")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def rpc(i, method, params=None):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())["result"]

    rpc(1, "initialize", {})
    tools = rpc(2, "tools/list")["tools"]
    assert [t["name"] for t in tools] == ["list_tools", "describe_tool", "call_tool"]
    listing = rpc(3, "tools/call", {"name": "list_tools", "arguments": {"server": "gh"}})["content"][0]["text"]
    assert "gh/gh_op3:" in listing and listing.count("\n") == 11
    d = json.loads(rpc(4, "tools/call", {"name": "describe_tool", "arguments": {"name": "pw/pw_op1"}})["content"][0]["text"])
    assert d["name"] == "pw/pw_op1" and "a" in d["inputSchema"]["properties"]
    r = rpc(5, "tools/call", {"name": "call_tool", "arguments": {"name": "notion/notion_op0", "args": {"a": "v"}}})
    assert '"a": "v"' in r["content"][0]["text"] and not r.get("isError")
    bad = rpc(6, "tools/call", {"name": "call_tool", "arguments": {"name": "nope/x"}})
    assert bad["isError"]
    p.stdin.close(); p.wait(timeout=5)
    # static reduction gate: what a client would load without the gateway (36 backend tools) vs the 3 meta-tools
    ns = {}
    exec(BACKEND.split("for line in")[0].replace("name = sys.argv[1]", "name = 'gh'"), {"json": json, "sys": sys}, ns)
    direct = len(json.dumps(ns["tools"])) * 3
    assert 1 - len(json.dumps(tools)) / direct >= 0.85


def test_discovery_turns_scripted_session(tmp_path):
    """Count the real discovery turns (list_tools + describe_tool tools/call round trips) before the first
    backend call on one scripted session. The 'agent' is a fixed script, not a model: it shows the protocol
    minimum (1 turn if the list line carries enough to call, 2 with describe_tool), not a live completion rate."""
    (tmp_path / "b.py").write_text(BACKEND)
    (tmp_path / "c.json").write_text(json.dumps({"gh": {"command": [sys.executable, str(tmp_path / "b.py"), "gh"]}}))
    p = subprocess.Popen([sys.executable, "-m", "ctxprune", "mcp-gateway", "--config", str(tmp_path / "c.json")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    turns = []

    def call(i, tool, args):
        turns.append(tool)
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())["result"]["content"][0]["text"]

    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
    p.stdin.flush(); p.stdout.readline()
    listing = call(2, "list_tools", {"server": "gh"})
    line = [l for l in listing.splitlines() if l.startswith("gh/gh_op0:")][0]
    list_only_enough = line.endswith(" (a)")   # required arg names (not types) are on the list line
    discovery_list_only = len(turns)
    call(3, "describe_tool", {"name": "gh/gh_op0"})
    discovery_with_describe = len(turns)
    call(4, "call_tool", {"name": "gh/gh_op0", "args": {"a": "v"}})
    p.stdin.close(); p.wait(timeout=5)
    assert turns == ["list_tools", "describe_tool", "call_tool"]
    assert list_only_enough
    assert (discovery_list_only, discovery_with_describe) == (1, 2)
