"""LLMLingua-2 (stock llmlingua 0.2.2, microsoft mBERT/XLM-R models) vs ctxprune-small on held-out chunks.

    python compare.py items                      # -> items.jsonl (held-out test chunks per domain)
    <stock venv>/bin/python compare.py lingua MODEL RATE OUT
    <ctxprune venv>/bin/python compare.py ctxprune RATE OUT
    python compare.py score items.jsonl OUT...   # facts kept verbatim + o200k tokens kept, per domain
"""
import json, os, random, re, sys

D = os.environ.get("CTXPRUNE_CHUNKS", "data/v2/chunks")   # the v2 corpus chunks (research repo)
DOMAINS = {"agent tool output": ["kwai_swe_tool", "terminalbench_obs"], "MCP result": ["toucan_mcp"],
           "chat": ["oasst2"], "multilingual web prose": ["fineweb2"]}
N = 30

# "facts": the things a reader needs verbatim: IDs/hashes, paths, versions, numbers >= 2 digits, URLs, dates, emails
# paths need a word on both sides of a slash (so `</returncode>` closing tags are not counted as paths)
FACT = re.compile(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
                  r"|(?<![<\w])[\w.-]*\w/(?:[\w.-]+/)*[\w.-]*\w|\b[vV]?\d+(?:\.\d+){1,3}\b"
                  r"|\b(?=\w*\d)(?=\w*[A-Za-z])[\w-]{6,}\b|\b\d{2,}\b")


def items():
    rnd = random.Random(7)
    out = open("items.jsonl", "w")
    for dom, srcs in DOMAINS.items():
        pool = []
        for s in srcs:
            for line in open(f"{D}/{s}.jsonl"):
                r = json.loads(line)
                if r["split"] == "test" and 400 <= len(r["text"]) <= 6000:
                    pool.append(r)
        rnd.shuffle(pool)
        for r in pool[:N]:
            out.write(json.dumps({"id": r["id"], "domain": dom, "text": r["text"]}) + "\n")


def run_lingua(model, rate, outp):
    from llmlingua import PromptCompressor
    pc = PromptCompressor(model_name=model, use_llmlingua2=True, device_map="cpu")
    with open(outp, "w") as o:
        for line in open("items.jsonl"):
            it = json.loads(line)
            try:
                txt = pc.compress_prompt([it["text"]], rate=rate, force_tokens=["\n"], drop_consecutive=True)["compressed_prompt"]
            except Exception as e:
                txt = f"<ERROR {type(e).__name__}: {e}>"
            o.write(json.dumps({"id": it["id"], "out": txt}) + "\n")


def run_ctxprune(rate, outp):
    import ctxprune
    with open(outp, "w") as o:
        for line in open("items.jsonl"):
            it = json.loads(line)
            o.write(json.dumps({"id": it["id"], "out": ctxprune.prune(it["text"], keep=rate)}) + "\n")


def score(itemsp, outs):
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    its = {json.loads(l)["id"]: json.loads(l) for l in open(itemsp)}
    res = {}
    for op in outs:
        rows = {json.loads(l)["id"]: json.loads(l)["out"] for l in open(op)}
        for iid, it in its.items():
            facts = set(FACT.findall(it["text"]))
            o = rows.get(iid, "")
            k = (op, it["domain"])
            a = res.setdefault(k, [0, 0, 0, 0, 0])
            a[0] += len(facts); a[1] += sum(1 for f in facts if f in o)
            a[2] += len(enc.encode(it["text"])); a[3] += len(enc.encode(o)); a[4] += o.startswith("<ERROR")
    print(f"{'output':34} {'domain':24} {'facts kept':>12} {'tokens kept':>12} errors")
    for (op, dom), (nf, kf, t0, t1, er) in sorted(res.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        print(f"{op:34} {dom:24} {kf / max(nf, 1):11.1%} {t1 / max(t0, 1):11.1%} {er:5d}   (n_facts={nf})")
    json.dump({f"{op}|{dom}": v for (op, dom), v in res.items()}, open("score.json", "w"), indent=1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "items": items()
    elif cmd == "lingua": run_lingua(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    elif cmd == "ctxprune": run_ctxprune(float(sys.argv[2]), sys.argv[3])
    elif cmd == "score": score(sys.argv[2], sys.argv[3:])
