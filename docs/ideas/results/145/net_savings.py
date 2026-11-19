"""T1.10b net-savings + round-trip measurement. Run with a python that has tiktoken (o200k):
  /home/dario/ctxprune-v2/venv-enc/bin/python docs/ideas/results/145/net_savings.py --split val [--min-chars 60]
Per session (sorted glob order; github dev=1-22 val=23-45, notion dev=1-28 val=29-56, same sessions as the screen):
outputs -> lossless.compress (json_compact etc, rows off) -> exact-dup outputs skipped (existing dedup) -> entity refs.
Baseline tokens = o200k of the post-compress outputs (<=50k chars). Net = refs saved - legend (once per session that fires).
Control arm = legend only (refs counted as zero saving but legend still paid)."""
import argparse, copy, glob, json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../../.."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tiktoken
from lossless import compress
import entityrefs
enc = tiktoken.get_encoding("o200k_base")
T = lambda s: len(enc.encode(s))
RAW = os.path.expanduser("~/.cache/ctxprune-typical/raw/mcpmark")
SETS = {"github": (22, "claude-*__github"), "notion": (28, "claude-*__notion")}

def outs(path):
    r = []
    for x in json.load(open(path)):
        if x.get("type") == "function_call_output" or ("output" in x and "call_id" in x and "arguments" not in x):
            o = x["output"]
            try:
                j = json.loads(o)
                if isinstance(j, dict) and "text" in j: o = j["text"]
            except Exception: pass
            r.append(o)
    return r

def session(texts, min_chars, rows=False):
    er = entityrefs.EntityRefs(min_chars=min_chars, tok=T)
    seen, base, saved, nref, fail, nout_ref = set(), 0, 0, 0, 0, 0
    leg = 0
    for t in texts:
        new, _ = compress(t, "", rows=rows) if len(t) <= 50000 else (t, None)
        if len(new) > 50000: er.skip(); continue
        if new in seen: er.skip(); continue
        seen.add(new)
        snap = copy.deepcopy(er)
        r, info = er.process(new)
        tb = T(new); base += tb
        if info["refs"]:
            if entityrefs.expand(r, snap) != new: fail += 1
            body = r[len(entityrefs.LEGEND) + 1:] if info["legend"] else r
            saved += tb - T(body); nref += info["refs"]; nout_ref += 1
            if info["legend"]: leg = T(entityrefs.LEGEND + "\n")
    return base, saved, leg, nref, nout_ref, fail

def boot(xs, n=2000, seed=0):
    rng = random.Random(seed); out = []
    for _ in range(n):
        s = [xs[rng.randrange(len(xs))] for _ in xs]
        out.append(100 * sum(a for a, b in s) / max(1, sum(b for a, b in s)))
    out.sort(); return out[int(.025 * n)], out[int(.975 * n)]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--split", default="val"); ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--rows", action="store_true"); a = ap.parse_args()
    for name, (ndev, pat) in SETS.items():
        files = sorted(glob.glob(f"{RAW}/*/{pat}/run-1/*/messages.json"))
        files = [f for f in files if len(outs(f)) >= 2]
        sel = {"dev": files[:ndev], "val": files[ndev:], "all": files}[a.split]
        B = S = L = NR = F = 0; per = []; rows_ = []
        for f in sel:
            b, s, l, nr, no, fl = session(outs(f), a.min_chars, a.rows)
            B += b; S += s; L += l; NR += nr; F += fl; net = s - l if nr else 0
            per.append(net / b if b else 0); rows_.append((net, b))
        per.sort(); lo, hi = boot(rows_)
        print(f"{name} {a.split} n={len(sel)} min_chars={a.min_chars} base={B} refs_saved={S} legend={L} net={S-L} "
              f"agg={100*(S-L)/max(B,1):.2f}% CI95=[{lo:.2f},{hi:.2f}] median={100*per[len(per)//2]:.2f}% "
              f"gross_agg={100*S/max(B,1):.2f}% refs={NR} roundtrip_fail={F}")
