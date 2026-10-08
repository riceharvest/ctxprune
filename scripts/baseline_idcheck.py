"""Measure LLMLingua-2 identifier survival on held-out chunks (CPU is fine).

    uv run --extra baseline scripts/baseline_idcheck.py --data /mnt/ssd/ctxprune-data/v1 \
        --split test --rate 0.5 --per-source 40

Writes <data>/baseline/<model-tag>_r<rate>.json with per-source summaries
and a few side-by-side examples for the model card.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.idcheck import summarize, survival  # noqa: E402

DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--split", default="test")
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--per-source", type=int, default=40)
    args = ap.parse_args()

    from llmlingua import PromptCompressor

    pc = PromptCompressor(model_name=args.model, use_llmlingua2=True, device_map="cpu")
    by_src: dict[str, list[dict]] = defaultdict(list)
    for p in sorted((args.data / "chunks").glob("*.jsonl")):
        with p.open() as f:
            for line in f:
                c = json.loads(line)
                if c["split"] == args.split and len(by_src[c["source"]]) < args.per_source:
                    by_src[c["source"]].append(c)

    report = {"model": args.model, "rate": args.rate, "split": args.split, "sources": {}, "examples": []}
    for src, chunks in sorted(by_src.items()):
        rows = []
        for c in chunks:
            out = pc.compress_prompt(c["text"], rate=args.rate, force_tokens=["\n", "?"])["compressed_prompt"]
            r = survival(c["text"], out)
            rows.append(r)
            if r["lost"] and len(report["examples"]) < 12 and src.endswith(("tool", "mcp")):
                report["examples"].append({"source": src, "id": c["id"], "original": c["text"][:600],
                                           "compressed": out[:600], "lost": r["lost_examples"]})
        report["sources"][src] = summarize(rows)
        print(src, report["sources"][src], flush=True)

    tag = re.sub(r"[^\w.-]+", "_", args.model.split("/")[-1])
    outdir = args.data / "baseline"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{tag}_r{args.rate}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
