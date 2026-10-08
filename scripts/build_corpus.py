"""Stream permissive sources and write tokenizer-sized chunks.

    uv run scripts/build_corpus.py --out /mnt/ssd/ctxprune-data/v1 --total 2000

Writes <out>/chunks/<source>.jsonl (one file per source, so sources can be
rebuilt independently), <out>/chunks/stats.json and
<out>/chunks/revisions.json.

Extending: sampling is deterministic for a given seed and pinned dataset
revisions, so rerunning with a larger --total (or a --mix with more weight on
a source) rewrites each file as a superset of the old one. label.py then only
queries the teacher for the new chunks; old ones come from its cache.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.chunking import DEFAULT_TOKENIZER, chunk_doc, load_counter  # noqa: E402
from ctxprune import sources  # noqa: E402
from ctxprune.sources import DEFAULT_MIX, REGISTRY, SOURCE_DATASETS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--total", type=int, default=2000, help="total chunk budget across sources")
    ap.add_argument("--sources", nargs="*", default=list(DEFAULT_MIX))
    ap.add_argument("--mix", nargs="*", default=[], metavar="SOURCE=WEIGHT",
                    help="override mix weights, e.g. toucan_mcp=0.3")
    ap.add_argument("--tokenizer", default=DEFAULT_TOKENIZER)
    ap.add_argument("--max-tokens", type=int, default=500)
    ap.add_argument("--max-chunks-per-doc", type=int, default=2)
    ap.add_argument("--max-docs-per-group", type=int, default=50,
                    help="cap docs from one repo/server/site so no group dominates")
    ap.add_argument("--max-docs-per-ref", type=int, default=4,
                    help="cap docs from one trajectory/conversation (they share a lot of text)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    count = load_counter(args.tokenizer)
    outdir = args.out / "chunks"
    outdir.mkdir(parents=True, exist_ok=True)
    rev_path = outdir / "revisions.json"
    revisions = json.loads(rev_path.read_text()) if rev_path.exists() else {}
    from huggingface_hub import HfApi

    for src in args.sources:
        ds = SOURCE_DATASETS[src]
        if ds not in revisions:
            revisions[ds] = HfApi().dataset_info(ds).sha
    rev_path.write_text(json.dumps(revisions, indent=2))
    sources.REVISIONS.update(revisions)

    weights = {s: DEFAULT_MIX[s] for s in args.sources}
    for kv in args.mix:
        k, v = kv.split("=")
        if k not in weights:
            ap.error(f"--mix names unknown or unselected source {k}")
        weights[k] = float(v)
    norm = sum(weights.values())
    stats = {}
    for src in args.sources:
        target = round(args.total * weights[src] / norm)
        rng = random.Random(f"{args.seed}:{src}")
        per_group: Counter = Counter()
        per_ref: Counter = Counter()
        splits: Counter = Counter()
        n_docs = n = 0
        t0 = time.time()
        path = outdir / f"{src}.jsonl"
        with path.open("w") as f:
            for doc in REGISTRY[src]():
                if per_group[doc["group"]] >= args.max_docs_per_group:
                    continue
                if doc["ref"] and per_ref[doc["ref"]] >= args.max_docs_per_ref:
                    continue
                per_group[doc["group"]] += 1
                per_ref[doc["ref"]] += 1
                n_docs += 1
                for c in chunk_doc(doc, count, rng, args.max_tokens, args.max_chunks_per_doc):
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
                    splits[c["split"]] += 1
                    n += 1
                if n >= target:
                    break
        stats[src] = {"chunks": n, "docs": n_docs, "groups": len(per_group),
                      "splits": dict(splits), "seconds": round(time.time() - t0, 1)}
        print(src, stats[src], flush=True)
    meta = {"args": {k: str(v) for k, v in vars(args).items()}, "sources": stats}
    (outdir / "stats.json").write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
