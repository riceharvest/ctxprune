"""Quick eval on held-out chunks: ours vs LLMLingua-2 at matched rates.

    python scripts/eval_quick.py --data /mnt/ssd/ctxprune-data/v1 --model runs/v1/final \
        --rates 0.5 0.33 --per-source 60

Reports per source: identifiers lost, JSON inputs still valid, actual
character ratio, and (ours only) agreement with the teacher's labels.
Writes <data>/eval/<model-tag>.json and prints a markdown table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.atoms import Atom  # noqa: E402
from ctxprune.idcheck import summarize, survival  # noqa: E402
from ctxprune.tokenlabels import atom_spans  # noqa: E402

# The models to beat: Microsoft's released LLMLingua-2 checkpoints (base mBERT and large XLM-R).
BASELINES = {
    "llmlingua2": "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
    "llmlingua2-large": "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
}


def load_test(data: Path, labels: str, split: str, per_source: int,
              extra: dict[str, str] | None = None) -> list[dict]:
    """Rows of `split`, plus per-source overrides in `extra` (source -> split) for sources
    whose test split came out empty."""
    extra = extra or {}
    by_src: dict[str, list] = defaultdict(list)
    with (data / "labeled" / f"{labels}.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            want = extra.get(r["source"], split)
            if r["split"] == want and not r["filtered"] and len(by_src[r["source"]]) < per_source:
                r["text"] = atom_spans([Atom(w, t) for w, t in r["atoms"]])[0]
                by_src[r["source"]].append(r)
    return [r for rs in by_src.values() for r in rs]


def label_agreement(scores: list[float], keep: list[bool], atoms) -> dict:
    tp = fp = fn = tn = 0
    for s, k, a in zip(scores, keep, atoms):
        if not a.text:
            continue
        p = s >= 0.5
        tp += p and k
        fp += p and not k
        fn += (not p) and k
        tn += (not p) and not k
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "keep_f1": 2 * prec * rec / max(1e-9, prec + rec),
            "acc": (tp + tn) / max(1, tp + fp + fn + tn)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--labels", default="qwen38")
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--extra-splits", nargs="*", default=["swe_smith_tool=val"], metavar="SOURCE=SPLIT",
                    help="SWE-smith has only 68 repos and none hashed into test; its val repos are held out")
    ap.add_argument("--rates", type=float, nargs="+", default=[0.5, 0.33])
    ap.add_argument("--per-source", type=int, default=60)
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--llm-tokenizer", default="/mnt/ssd/models/qwen38-27b-gptq",
                    help="tokenizer of the downstream LLM, for compression ratios users pay for")
    args = ap.parse_args()

    from transformers import AutoTokenizer

    from ctxprune.compress import Compressor

    llm_tok = AutoTokenizer.from_pretrained(args.llm_tokenizer)

    def count_tokens(t: str) -> int:
        return len(llm_tok(t, add_special_tokens=False)["input_ids"])

    def llm_count(t: str) -> int:
        # Measure ours in the reader's tokens so every method's `rate` means the same thing.
        return len(llm_tok(t, add_special_tokens=False)["input_ids"])

    rows = load_test(args.data, args.labels, args.split, args.per_source,
                     dict(kv.split("=") for kv in args.extra_splits))
    print(f"{len(rows)} {args.split} rows", flush=True)
    ours = Compressor(args.model, device=args.device)
    methods = {}
    for r in args.rates:
        methods[f"ours@{r}"] = lambda t, r=r: ours.compress(t, rate=r, measure=llm_count)["text"]
        # User-default settings (character budget, no ratio search): honest latency numbers.
        methods[f"ours(chars)@{r}"] = lambda t, r=r: ours.compress(t, rate=r)["text"]
        methods[f"ours+ids@{r}"] = lambda t, r=r: ours.compress(
            t, rate=r, force_protected=True, measure=llm_count)["text"]
    if not args.no_baseline:
        from llmlingua import PromptCompressor

        for bname, bmodel in BASELINES.items():
            pc = PromptCompressor(model_name=bmodel, use_llmlingua2=True, device_map="cpu")
            for r in args.rates:
                methods[f"{bname}@{r}"] = lambda t, r=r, pc=pc: pc.compress_prompt(
                    t, rate=r, force_tokens=["\n", "?"])["compressed_prompt"]

    results: dict = {"model": args.model, "split": args.split, "methods": {}, "agreement": {}}
    agree = defaultdict(lambda: defaultdict(int))
    for name, fn in methods.items():
        per_src = defaultdict(list)
        t0 = time.time()
        for row in rows:
            per_src[row["source"]].append(survival(row["text"], fn(row["text"]), count_tokens))
        results["methods"][name] = {s: summarize(v) for s, v in per_src.items()}
        results["methods"][name]["_all"] = summarize([x for v in per_src.values() for x in v])
        results["methods"][name]["_seconds"] = round(time.time() - t0, 1)
        results["methods"][name]["_ms_per_chunk"] = round(1000 * (time.time() - t0) / max(1, len(rows)), 1)
        print(name, results["methods"][name]["_all"], flush=True)
    for row in rows:
        atoms, scores, _ = ours.atom_scores(row["text"])
        a = label_agreement(scores, row["keep"], atoms)
        for k in ("tp", "fp", "fn", "tn"):
            agree[row["source"]][k] += a[k]
    for s, c in agree.items():
        prec = c["tp"] / max(1, c["tp"] + c["fp"])
        rec = c["tp"] / max(1, c["tp"] + c["fn"])
        results["agreement"][s] = {"keep_f1": round(2 * prec * rec / max(1e-9, prec + rec), 3),
                                   "acc": round((c["tp"] + c["tn"]) / max(1, sum(c.values())), 3)}

    tag = re.sub(r"[^\w.-]+", "_", str(args.model).rstrip("/").replace("/final", "").split("/")[-1])
    outdir = args.data / "eval"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{tag}.json").write_text(json.dumps(results, indent=2))

    srcs = sorted({s for m in results["methods"].values() for s in m if not s.startswith("_")})
    print("\nIdentifiers lost per source; ratios are kept/original (lower = more compression).")
    print("| method | " + " | ".join(srcs) + " | all | JSON valid | char ratio | LLM-token ratio | ms/chunk (CPU) |")
    print("|---" * (len(srcs) + 6) + "|")
    for name, m in results["methods"].items():
        cells = [f"{m[s]['lost']}/{m[s]['protected']}" if s in m else "-" for s in srcs]
        a = m["_all"]
        print(f"| {name} | " + " | ".join(cells) + f" | {a['lost']}/{a['protected']} ({a['lost_rate']:.0%}) | "
              f"{a['json_still_valid']}/{a['json_inputs']} | {a['mean_char_ratio']:.2f} | "
              f"{a.get('mean_llm_token_ratio', float('nan')):.2f} | {m['_ms_per_chunk']:.0f} |")
    print("\nteacher-label agreement (ours, p>=0.5):", json.dumps(results["agreement"]))


if __name__ == "__main__":
    main()
