"""Downstream QA eval: can an LLM still answer from the compressed text?

Step 1 (needs the LLM server):
    python scripts/eval_qa.py questions --data /mnt/ssd/ctxprune-data/v1
  The LLM writes questions whose answers are exact spans of each held-out
  chunk (identifiers, numbers, names, short phrases). Answers not found
  verbatim in the chunk are discarded.

Step 2 (needs the LLM server and a trained model):
    python scripts/eval_qa.py answer --data /mnt/ssd/ctxprune-data/v1 --model runs/v1/final
  Compresses every chunk with each method, asks the reader each question
  with that context, and scores exact match (gold span contained in the
  answer, after normalization) and token F1.

Questions and answers are cached, so reruns and new models only pay for the
new calls.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.teacher import Cache, OpenAITeacher  # noqa: E402

QGEN = """Below is a piece of text that an AI agent received: a tool output, source code, a document or a chat message.

Write {n} questions that the agent might later need to answer using only this text. Rules:
- Each answer must be copied exactly from the text: an identifier, number, file path, URL, error message, name, value, or a short phrase of at most 8 words.
- Prefer facts an agent would act on (IDs, values, errors, paths, settings, names, dates, key claims).
- Each question must be answerable from the text alone and have one clear answer.
- Write the question in the language of the text.

Reply with a JSON list only: [{{"question": "...", "answer": "..."}}]

Text:
<<<
{text}
>>>"""

READ = """Answer the question using only the context below. The context may have been compressed: some words were removed.
Reply with the answer only, as short as possible, and copy identifiers, numbers and names exactly as they appear. If the context does not contain the answer, reply "unknown".

Context:
<<<
{context}
>>>

Question: {question}"""

# The models to beat: Microsoft's released LLMLingua-2 checkpoints (base mBERT and large XLM-R).
BASELINES = {
    "llmlingua2": "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
    "llmlingua2-large": "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
}


def norm(s: str) -> str:
    s = s.casefold().strip()
    s = re.sub(r"^[\"'`(\[]+|[\"'`)\].,;:!?]+$", "", s)
    return re.sub(r"\s+", " ", s)


def score(pred: str, gold: str) -> tuple[float, float]:
    p, g = norm(pred), norm(gold)
    em = float(bool(g) and (g == p or g in p))
    pt, gt = p.split(), g.split()
    common = sum(min(pt.count(w), gt.count(w)) for w in set(gt))
    f1 = 0.0 if not common else 2 * common / (len(pt) + len(gt))
    return em, f1


def load_test(data: Path, split: str, per_source: int, sources: list[str] | None = None) -> list[dict]:
    by_src: dict[str, list] = defaultdict(list)
    for p in sorted((data / "chunks").glob("*.jsonl")):
        if sources and p.stem not in sources:
            continue
        with p.open() as f:
            for line in f:
                c = json.loads(line)
                if c["split"] == split and len(by_src[c["source"]]) < per_source:
                    by_src[c["source"]].append(c)
    return [c for cs in by_src.values() for c in cs]


def parse_qas(raw: str, text: str) -> list[dict]:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        q, a = str(it.get("question", "")).strip(), str(it.get("answer", "")).strip()
        if q and a and len(a) <= 120 and a in text:
            out.append({"question": q, "answer": a})
    return out


def make_llm(args) -> OpenAITeacher:
    """Local vLLM by default (thinking off); any OpenAI-compatible API with --api-key-env."""
    extra = json.loads(args.extra_body) if args.extra_body else (
        {} if args.api_key_env else {"chat_template_kwargs": {"enable_thinking": False}})
    key_ = os.environ[args.api_key_env] if args.api_key_env else None
    return OpenAITeacher(args.base_url, args.llm, api_key=key_, extra_body=extra)


def key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


async def gather_cached(llm: OpenAITeacher, cache: Cache, jobs: list[tuple[str, list[dict], int]],
                        concurrency: int) -> dict[str, str]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}
    done = 0

    async def one(k, messages, max_tokens):
        nonlocal done
        hit = cache.get(k)
        if hit is None:
            async with sem:
                try:
                    hit = await llm.chat(messages, max_tokens)
                except Exception as e:  # noqa: BLE001
                    print(f"\nerror: {type(e).__name__}: {e}", flush=True)
                    return
            cache.put(k, hit, {})
        out[k] = hit
        done += 1
        if done % 100 == 0:
            print(f"\r{done}/{len(jobs)}", end="", flush=True)

    await asyncio.gather(*(one(*j) for j in jobs))
    print(flush=True)
    return out


def cmd_questions(args) -> None:
    chunks = load_test(args.data, args.split, args.per_source, args.sources)
    llm = make_llm(args)
    cache = Cache(args.data / "qa" / f"qgen_{re.sub(r'[^\w.-]+', '_', args.llm)}.jsonl")
    jobs = [(key("qgen-v1", args.llm, c["text"]),
             [{"role": "user", "content": QGEN.format(n=args.n, text=c["text"])}], 600) for c in chunks]

    async def go():
        try:
            return await gather_cached(llm, cache, jobs, args.concurrency)
        finally:
            await llm.aclose()

    raw = asyncio.run(go())
    cache.close()
    n = 0
    with (args.data / "qa" / "questions.jsonl").open("a" if args.append else "w") as f:
        for c, (k, _, _) in zip(chunks, jobs):
            for i, qa in enumerate(parse_qas(raw.get(k, ""), c["text"])[: args.n]):
                f.write(json.dumps({"qid": f"{c['id']}#{i}", "chunk_id": c["id"], "source": c["source"],
                                    "domain": c["domain"], "lang": c["lang"], "text": c["text"], **qa},
                                   ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} questions from {len(chunks)} chunks -> {args.data / 'qa' / 'questions.jsonl'}")


def cmd_answer(args) -> None:
    qs = [json.loads(l) for l in (args.data / "qa" / "questions.jsonl").open()]
    texts = {q["chunk_id"]: q["text"] for q in qs}
    print(f"{len(qs)} questions over {len(texts)} chunks", flush=True)

    # Compress every chunk once per method (CPU or GPU, before any LLM calls).
    from transformers import AutoTokenizer

    from ctxprune.compress import Compressor

    llm_tok = AutoTokenizer.from_pretrained(args.llm_tokenizer)

    def llm_count(t: str) -> int:
        # Measure ours in the reader's tokens so every method's `rate` means the same thing.
        return len(llm_tok(t, add_special_tokens=False)["input_ids"])

    ours = Compressor(args.model, device=args.device, backend=args.backend, onnx_file=args.onnx_file)
    methods = {"original": lambda t: t}
    for r in args.rates:
        methods[f"ours@{r}"] = lambda t, r=r: ours.compress(t, rate=r, measure=llm_count)["text"]
        methods[f"ours+ids@{r}"] = lambda t, r=r: ours.compress(
            t, rate=r, force_protected=True, measure=llm_count)["text"]
    if not args.no_baseline:
        from llmlingua import PromptCompressor

        for bname, bmodel in BASELINES.items():
            pc = PromptCompressor(model_name=bmodel, use_llmlingua2=True, device_map="cpu")
            for r in args.rates:
                methods[f"{bname}@{r}"] = lambda t, r=r, pc=pc: pc.compress_prompt(
                    t, rate=r, force_tokens=["\n", "?"])["compressed_prompt"]
    contexts: dict[str, dict[str, str]] = {}
    for path in args.extra_contexts or []:  # methods compressed elsewhere, e.g. scripts/baseline_kompress.py
        for name, by_chunk in json.loads(Path(path).read_text()).items():
            contexts[name] = by_chunk
            methods[name] = None
    for name, fn in methods.items():
        if fn is None:
            continue
        t0 = time.time()
        contexts[name] = {cid: fn(t) for cid, t in texts.items()}
        print(f"compressed {name} in {time.time() - t0:.1f}s", flush=True)

    tag = re.sub(r"[^\w.-]+", "_", str(args.model).rstrip("/").replace("/final", "").split("/")[-1])
    if args.backend == "onnx":
        tag += "-onnx-" + Path(args.onnx_file).stem
    outdir = args.data / "qa"
    (outdir / f"contexts_{tag}.json").write_text(json.dumps(contexts, ensure_ascii=False))

    llm = make_llm(args)
    # Reader identity includes request options (e.g. reasoning on/off), so settings never share a cache.
    opts = args.extra_body or ""
    reader = re.sub(r"[^\w.-]+", "_", args.llm) + (f"_{hashlib.sha1(opts.encode()).hexdigest()[:6]}" if opts else "")
    cache = Cache(outdir / f"answers_{reader}.jsonl")
    jobs, index = [], []
    for name in methods:
        for q in qs:
            ctx = contexts[name][q["chunk_id"]]
            k = key("read-v1", args.llm, opts, ctx, q["question"])
            jobs.append((k, [{"role": "user", "content": READ.format(context=ctx, question=q["question"])}],
                         args.max_answer_tokens))
            index.append((name, q, k))

    async def go():
        try:
            return await gather_cached(llm, cache, jobs, args.concurrency)
        finally:
            await llm.aclose()

    answers = asyncio.run(go())
    cache.close()

    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))
    for name, q, k in index:
        if k not in answers:
            continue
        em, f1 = score(answers[k], q["answer"])
        for bucket in (q["source"], "_all"):
            a = agg[name][bucket]
            a[0] += em
            a[1] += f1
            a[2] += 1
    ratio = {name: sum(len(contexts[name][c]) for c in texts) / sum(len(t) for t in texts.values())
             for name in methods}
    def ntok(t: str) -> int:
        return len(llm_tok(t, add_special_tokens=False)["input_ids"])

    orig_tok = sum(ntok(t) for t in texts.values())
    tok_ratio = {name: sum(ntok(contexts[name][c]) for c in texts) / orig_tok for name in methods}
    report = {name: {b: {"em": round(v[0] / v[2], 3), "f1": round(v[1] / v[2], 3), "n": v[2]}
                     for b, v in buckets.items()} for name, buckets in agg.items()}
    for name in report:
        report[name]["_char_ratio"] = round(ratio[name], 3)
        report[name]["_llm_token_ratio"] = round(tok_ratio[name], 3)
    (outdir / f"qa_{tag}__{reader}.json").write_text(json.dumps(report, indent=2))

    srcs = sorted({b for v in report.values() for b in v if not b.startswith("_")})
    print(f"\nExact match (gold span in answer), reader={args.llm} {opts}:")
    print("| method | LLM-token ratio | char ratio | " + " | ".join(srcs) + " | all |")
    print("|---" * (len(srcs) + 4) + "|")
    for name, v in report.items():
        print(f"| {name} | {v['_llm_token_ratio']:.2f} | {v['_char_ratio']:.2f} | " + " | ".join(
            f"{v[s]['em']:.2f}" if s in v else "-" for s in srcs) + f" | {v['_all']['em']:.3f} (n={v['_all']['n']}) |")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("questions", "answer"):
        p = sub.add_parser(name)
        p.add_argument("--data", required=True, type=Path)
        p.add_argument("--split", default="test")
        p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
        p.add_argument("--llm", default="qwen38")
        p.add_argument("--concurrency", type=int, default=32)
        p.add_argument("--api-key-env", default=None, help="env var holding the API key (e.g. OPENROUTER_API_KEY)")
        p.add_argument("--extra-body", default=None, help="JSON merged into each request body")
        if name == "questions":
            p.add_argument("--per-source", type=int, default=40)
            p.add_argument("--n", type=int, default=2, help="questions per chunk")
            p.add_argument("--sources", nargs="*", default=None)
            p.add_argument("--append", action="store_true", help="add to questions.jsonl instead of replacing")
        else:
            p.add_argument("--model", required=True)
            p.add_argument("--rates", type=float, nargs="+", default=[0.5, 0.33])
            p.add_argument("--device", default=None)
            p.add_argument("--no-baseline", action="store_true")
            p.add_argument("--extra-contexts", nargs="*", default=None,
                           help="JSON files of {method: {chunk_id: compressed text}} to score alongside")
            p.add_argument("--backend", default="torch", choices=["torch", "onnx"])
            p.add_argument("--onnx-file", default="onnx/model.onnx", help="e.g. onnx/model_quantized.onnx")
            p.add_argument("--max-answer-tokens", type=int, default=64,
                           help="raise (e.g. 4096) for reasoning readers: reasoning counts toward the limit")
            p.add_argument("--llm-tokenizer", default="/mnt/ssd/models/qwen38-27b-gptq")
    args = ap.parse_args()
    (args.data / "qa").mkdir(parents=True, exist_ok=True)
    {"questions": cmd_questions, "answer": cmd_answer}[args.cmd](args)


if __name__ == "__main__":
    main()
