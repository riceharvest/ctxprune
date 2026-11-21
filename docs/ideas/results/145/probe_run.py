"""T1.10b reader probe (GPU). raw vs enc on the same questions, greedy, local Qwen2.5-1.5B-Instruct.
      --items docs/ideas/results/145/probe_items.jsonl --out docs/ideas/results/145/probe_answers.jsonl [--limit N]
Writes answers incrementally (chunked saves), then prints accuracy + session-clustered paired bootstrap CI + MDE."""
import argparse, json, os, random, re, sys, time
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
MODEL = "the local reader model"
SYS = ("You are a careful assistant. You are given the outputs of tool calls an agent ran, numbered #1, #2, ... in order. "
       "Answer the question using only those outputs. Reply with the answer only, no explanation.")
LEG = "[ctxprune:"
norm = lambda s: re.sub(r"[\s\"'`]+", "", s).lower()

def prompt(outs, q):
    body = "\n\n".join(f"### Tool output #{i}\n{o}" for i, o in enumerate(outs, 1))
    return [{"role": "system", "content": SYS}, {"role": "user", "content": body + "\n\nQuestion: " + q}]

def score(path):
    rows = [json.loads(l) for l in open(path)]
    by = {}
    for r in rows: by.setdefault(r["id"], {})[r["variant"]] = r
    ids = [i for i, v in by.items() if "raw" in v and "enc" in v]
    sess = {}
    for i in ids: sess.setdefault(by[i]["raw"]["session"], []).append(i)
    acc = lambda v, xs: sum(by[i][v]["ok"] for i in xs) / len(xs)
    rng = random.Random(0); keys = list(sess); d = []
    for _ in range(2000):
        xs = [i for k in (rng.choice(keys) for _ in keys) for i in sess[k]]
        d.append(acc("enc", xs) - acc("raw", xs))
    d.sort(); n = len(ids)
    diffs = [by[i]["enc"]["ok"] - by[i]["raw"]["ok"] for i in ids]
    mean = sum(diffs) / n; sd = (sum((x - mean) ** 2 for x in diffs) / (n - 1)) ** .5
    print(f"n={n} sessions={len(keys)} raw_acc={100*acc('raw',ids):.1f}% enc_acc={100*acc('enc',ids):.1f}% "
          f"delta={100*(acc('enc',ids)-acc('raw',ids)):+.1f}pt CI95(session bootstrap)=[{100*d[50]:+.1f},{100*d[1949]:+.1f}] "
          f"MDE(80% power, paired, measured sd)={100*2.8*sd/n**.5:.1f}pt")
    for s in ("github", "notion"):
        xs = [i for i in ids if i.startswith(s)]
        if xs: print(f"  {s}: n={len(xs)} raw={100*acc('raw',xs):.1f}% enc={100*acc('enc',xs):.1f}%")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--items"); ap.add_argument("--out"); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--score-only", action="store_true"); ap.add_argument("--budget-s", type=int, default=1500); a = ap.parse_args()
    if a.score_only: score(a.out); sys.exit()
    items = [json.loads(l) for l in open(a.items)]
    if a.limit: items = items[:a.limit]
    done = set()
    if os.path.exists(a.out):
        for l in open(a.out): r = json.loads(l); done.add((r["id"], r["variant"]))
    tok = AutoTokenizer.from_pretrained(MODEL); m = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=torch.bfloat16).cuda().eval()
    t0 = time.time(); f = open(a.out, "a")
    for it in items:
        for v in ("raw", "enc"):
            if (it["id"], v) in done: continue
            if time.time() - t0 > a.budget_s: print("budget hit"); f.close(); score(a.out); sys.exit()
            ids = tok.apply_chat_template(prompt(it[v], it["q"]), add_generation_prompt=True, return_tensors="pt", return_dict=True).to("cuda")
            with torch.no_grad(): o = m.generate(**ids, max_new_tokens=48, do_sample=False)
            out = tok.decode(o[0, ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()
            ok = norm(it["answer"]) in norm(out)
            f.write(json.dumps({"id": it["id"], "variant": v, "session": it["session"], "out": out, "ok": int(ok),
                                "prompt_tokens": ids["input_ids"].shape[1]}) + "\n"); f.flush()
    f.close(); score(a.out)
