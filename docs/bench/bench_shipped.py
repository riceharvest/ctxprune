"""Time the shipped Claude Code hook entry (wheel: ctxprune/plugin_cli.py hook claude-code) on the canary's real
hook events, fresh process per call as Claude Code runs it. argv: PYTHON PLUGIN_CLI EVENTS N OUT"""
import json, os, subprocess, sys, tempfile, time
py, cli, evp, n, out = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
st = tempfile.mkdtemp(prefix="bench-shipped-")
env = {k: v for k, v in os.environ.items() if not k.startswith("CTXPRUNE_")}
env.update(CTXPRUNE_STATE=st, CTXPRUNE_STATS=os.path.join(st, "stats.jsonl"), CTXPRUNE_CONFIG_DIR=os.path.join(st, "cfg"))
evs = [json.loads(x)["event"] for x in open(evp)]
ms, changed = [], 0
for ev in evs:
    raw = json.dumps(ev).encode()
    for _ in range(n):
        t = time.perf_counter()
        r = subprocess.run([py, cli, "hook", "claude-code"], input=raw, capture_output=True, env=env)
        ms.append((time.perf_counter() - t) * 1000)
    changed += bool(r.stdout.strip())
ms.sort()
res = {"n": len(ms), "events": len(evs), "events_rewritten": changed, "p50": round(ms[len(ms) // 2], 1),
       "p95": round(ms[int(.95 * len(ms)) - 1], 1), "max": round(ms[-1], 1), "python": py}
open(out, "w").write(json.dumps(res, indent=1) + "\n"); print(res)
