"""Compress the QA eval chunks with headroom's Kompress-v2-base, for scripts/eval_qa.py --extra-contexts.

Runs in a separate env with `pip install headroom-ai` (its deps clash with ours):

    python scripts/baseline_kompress.py --data /mnt/ssd/ctxprune-data/v1 --out kompress_contexts.json

Methods written:
  kompress-default        Kompress decides how much to keep (score threshold 0.5, as headroom's proxy runs it)
  kompress@<rate>         target_ratio binary-searched per chunk so the output is <= rate of the reader's
                          tokens, the same budget rule eval_qa.py uses for ctxprune
CCR (headroom's retrieve-the-original marker) is off, and the small-input floor is lowered so short chunks
are compressed too; otherwise both would hand Kompress uncompressed text.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from headroom.transforms.kompress_compressor import KompressCompressor, KompressConfig
from transformers import AutoTokenizer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rates", type=float, nargs="+", default=[0.5, 0.33])
    ap.add_argument("--llm-tokenizer", default="/mnt/ssd/models/qwen38-27b-gptq")
    args = ap.parse_args()

    qs = [json.loads(line) for line in (args.data / "qa" / "questions.jsonl").open()]
    texts = {q["chunk_id"]: q["text"] for q in qs}
    tok = AutoTokenizer.from_pretrained(args.llm_tokenizer)

    def ntok(t: str) -> int:
        return len(tok(t, add_special_tokens=False)["input_ids"])

    km = KompressCompressor(KompressConfig(enable_ccr=False, min_input_words=10))

    def at_budget(text: str, rate: float) -> str:
        budget = rate * ntok(text)
        lo, hi, best = 0.0, 1.0, None
        for _ in range(12):
            mid = (lo + hi) / 2
            out = km.compress(text, target_ratio=max(mid, 1e-3)).compressed
            if ntok(out) <= budget:
                best, lo = out, mid
            else:
                hi = mid
        return best if best is not None else km.compress(text, target_ratio=1e-3).compressed

    contexts: dict[str, dict[str, str]] = {}
    t0 = time.time()
    contexts["kompress-default"] = {cid: km.compress(t).compressed for cid, t in texts.items()}
    print(f"kompress-default in {time.time() - t0:.1f}s", flush=True)
    for r in args.rates:
        t0 = time.time()
        contexts[f"kompress@{r}"] = {cid: at_budget(t, r) for cid, t in texts.items()}
        print(f"kompress@{r} in {time.time() - t0:.1f}s", flush=True)
    args.out.write_text(json.dumps(contexts, ensure_ascii=False))


if __name__ == "__main__":
    main()
