"""T1.10b reader-probe item generator (CPU, deterministic, frozen before the run). Val sessions only.
  lab-cpu -- /home/dario/ctxprune-v2/venv-enc/bin/python docs/ideas/results/145/probe_gen.py --out docs/ideas/results/145/probe_items.jsonl
One item = (session, target output i, one replaced object at pointer P, one scalar field F of it). Question: value of F
in the object at pointer P of output #i. raw = every output as the baseline pipeline renders it; enc = with entity refs.
Context cap CAP o200k tokens (the local reader is a 1.5B model)."""
import argparse, copy, glob, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_savings as N, entityrefs
from lossless import compress
CAP, PER = 12000, 8

def items_for(f, rng):
    er = entityrefs.EntityRefs(tok=N.T); seen = set(); raws, encs, cands = [], [], []
    for i, t in enumerate(N.outs(f), 1):
        new = compress(t, "", rows=False)[0] if len(t) <= 50000 else t
        if len(new) > 50000 or new in seen:
            er.skip(); raws.append("[omitted]"); encs.append("[omitted]"); continue
        seen.add(new); r, info = er.process(new)
        raws.append(new); encs.append(r)
        for ptr, k, kp, o in info.get("items", []):
            fs = [(a, v) for a, v in o.items() if isinstance(v, (str, int)) and not isinstance(v, bool) and 0 < len(str(v)) <= 50]
            if fs and ptr:
                a, v = rng.choice(fs); cands.append((i, ptr, a, str(v)))
    return raws, encs, cands

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    rng = random.Random(20261119); out = []
    for name, (ndev, pat) in N.SETS.items():
        files = sorted(glob.glob(f"{N.RAW}/*/{pat}/run-1/*/messages.json"))
        files = [f for f in files if len(N.outs(f)) >= 2][ndev:]   # val only
        for f in files:
            raws, encs, cands = items_for(f, rng); rng.shuffle(cands); n = 0
            for (i, ptr, fld, val) in cands:
                if n >= PER: break
                if N.T("\n".join(raws[:i])) > CAP: continue
                q = (f'In tool output #{i}, what is the value of the field "{fld}" in the object at JSON pointer "{ptr}" '
                     f'(counting from the top-level value of that output)? Answer with the value only.')
                out.append({"id": f"{name}-{len(out)}", "server": name, "session": f.split("/")[-2], "q": q, "answer": val,
                            "raw": raws[:i], "enc": encs[:i]}); n += 1
    json.dump(out, open(a.out, "w")) if False else open(a.out, "w").write("\n".join(json.dumps(x) for x in out))
    from collections import Counter
    print(len(out), Counter(x["server"] for x in out), "sessions", len({x["session"] for x in out}))
    print("avg tok raw/enc", sum(N.T("\n".join(x["raw"])) for x in out)/len(out), sum(N.T("\n".join(x["enc"])) for x in out)/len(out))
