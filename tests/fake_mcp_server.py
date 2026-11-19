"""Tiny stdio MCP server for the mcp-wrap tests: initialize, tools/list, tools/call (list_items, read_file, structured)."""
import json
import sys

ITEMS = [{"id": i, "name": f"item-{i}", "owner": {"login": "octocat", "id": 1, "type": "User"}, "state": "open",
          "labels": [], "url": f"https://api.example.com/repos/a/b/issues/{i}"} for i in range(60)]

for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    meth, p = m.get("method"), m.get("params") or {}
    if meth == "tools/call":
        name = p.get("name")
        txt = json.dumps(ITEMS, indent=2)
        res = {"content": [{"type": "text", "text": txt}]}
        if name == "structured":
            res["structuredContent"] = {"items": ITEMS}
        if name == "fail":
            res["isError"] = True
        r = {"jsonrpc": "2.0", "id": m["id"], "result": res}
    else:
        r = {"jsonrpc": "2.0", "id": m["id"], "result": {"echo": meth}}
    sys.stdout.write(json.dumps(r) + "\n")
    sys.stdout.flush()
