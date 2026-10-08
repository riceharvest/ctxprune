"""Run the teacher over chunks, align its output, and write keep labels.

    # pipeline check without a GPU
    uv run scripts/label.py --data /mnt/ssd/ctxprune-data/v1 --teacher mock
    # real run against a local OpenAI-compatible server
    uv run scripts/label.py --data /mnt/ssd/ctxprune-data/v1 --teacher openai \
        --base-url http://127.0.0.1:8012/v1 --model <served-model-name> --concurrency 32

Outputs <data>/labeled/<model>.jsonl and <data>/labeled/<model>.stats.json.
Teacher responses are cached in <data>/teacher_cache/<model>.jsonl, so an
interrupted run resumes and re-aligning never re-queries the teacher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.align import align  # noqa: E402
from ctxprune.atoms import atomize  # noqa: E402
from ctxprune.teacher import Cache, MockTeacher, OpenAITeacher, run_teacher  # noqa: E402


def load_chunks(data: Path, sources: list[str] | None, limit: int | None) -> list[dict]:
    chunks = []
    for p in sorted((data / "chunks").glob("*.jsonl")):
        if sources and p.stem not in sources:
            continue
        with p.open() as f:
            rows = [json.loads(l) for l in f]
        chunks.extend(rows[:limit] if limit else rows)
    return chunks


def filter_reason(a, max_variation: float, min_comp: float) -> str | None:
    if a.n_comp == 0:
        return "empty_output"
    if a.variation_rate > max_variation:
        return "rewrote"
    if a.comp_rate < min_comp:
        return "over_deleted"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--teacher", choices=["mock", "openai"], default="mock")
    ap.add_argument("--base-url", default="http://127.0.0.1:8012/v1")
    ap.add_argument("--model", default=None)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--no-think", action="store_true",
                    help="send chat_template_kwargs.enable_thinking=false (Qwen3-style servers)")
    ap.add_argument("--sources", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None, help="max chunks per source")
    ap.add_argument("--prompt", default="v1", choices=["v1", "v2", "v3"], help="teacher prompt version (see teacher.py)")
    ap.add_argument("--subset-from", type=Path, default=None,
                    help="only label chunk ids present in this labeled .jsonl (e.g. relabel a set with another prompt)")
    ap.add_argument("--exclude-from", type=Path, default=None,
                    help="skip chunk ids present in this labeled .jsonl (label new chunks only)")
    ap.add_argument("--max-chunks", type=int, default=None, help="label at most this many chunks (after shuffling)")
    ap.add_argument("--cache-only", action="store_true",
                    help="write labels for chunks already in the teacher cache; make no LLM calls "
                         "(checkpointing a running labeling job)")
    ap.add_argument("--max-variation", type=float, default=0.10)
    ap.add_argument("--min-comp", type=float, default=0.05)
    ap.add_argument("--no-deletion-at", type=float, default=0.98,
                    help="flag (not filter) rows where the teacher kept at least this fraction: "
                         "'keep everything' is a valid label, but the trainer may want to downsample it")
    args = ap.parse_args()

    chunks = load_chunks(args.data, args.sources, args.limit)
    # Interleave sources so a partially finished run is a representative sample.
    random.Random(0).shuffle(chunks)
    if args.teacher == "mock":
        teacher = MockTeacher()
    else:
        if not args.model:
            ap.error("--model is required with --teacher openai")
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if args.no_think else {}
        teacher = OpenAITeacher(args.base_url, args.model, max_tokens=args.max_tokens, extra_body=extra,
                                prompt=args.prompt)
    tag = re.sub(r"[^\w.-]+", "_", teacher.model) + ("" if args.prompt == "v1" else f"_{args.prompt}")
    if args.subset_from:
        with args.subset_from.open() as f:
            want = {json.loads(l)["id"] for l in f}
        chunks = [c for c in chunks if c["id"] in want]
        print(f"subset: {len(chunks)} chunks from {args.subset_from}", flush=True)
    if args.exclude_from:
        with args.exclude_from.open() as f:
            skip = {json.loads(l)["id"] for l in f}
        chunks = [c for c in chunks if c["id"] not in skip]
        print(f"excluding {len(skip)} already-labeled ids -> {len(chunks)} chunks", flush=True)
    if args.max_chunks:
        chunks = chunks[: args.max_chunks]
    cache = Cache(args.data / "teacher_cache" / f"{tag}.jsonl")
    if args.cache_only:
        from ctxprune.teacher import cache_key

        chunks = [c for c in chunks if cache.get(cache_key(teacher.model, c["text"], teacher.prompt)) is not None]
        print(f"cache-only: {len(chunks)} labeled chunks available", flush=True)

    done = 0
    t0 = time.time()

    def tick():
        nonlocal done
        done += 1
        if done % 100 == 0 or done == len(chunks):
            rate = done / max(1e-9, time.time() - t0)
            print(f"\r{done}/{len(chunks)} chunks  {rate:.2f}/s", end="", flush=True)

    async def go():
        try:
            return await run_teacher(teacher, chunks, cache, args.concurrency, on_done=tick)
        finally:
            await teacher.aclose()

    outputs, errors = asyncio.run(go())
    cache.close()
    print()
    wall = time.time() - t0

    outdir = args.data / "labeled"
    outdir.mkdir(parents=True, exist_ok=True)
    agg = defaultdict(lambda: defaultdict(float))
    with (outdir / f"{tag}.jsonl").open("w") as f:
        for c in chunks:
            if c["id"] not in outputs:
                continue
            atoms = atomize(c["text"])
            a = align(atoms, outputs[c["id"]])
            reason = filter_reason(a, args.max_variation, args.min_comp)
            no_del = a.comp_rate >= args.no_deletion_at
            row = {
                "id": c["id"], "split": c["split"], "source": c["source"], "domain": c["domain"],
                "lang": c["lang"], "license": c["license"], "dataset": c["dataset"], "ref": c["ref"],
                "atoms": [[x.ws, x.text] for x in atoms], "keep": a.keep,
                "teacher": teacher.model, "comp": outputs[c["id"]],
                "variation_rate": round(a.variation_rate, 4), "comp_rate": round(a.comp_rate, 4),
                "protected_total": a.protected_total, "protected_kept": a.protected_kept,
                "filtered": reason, "no_deletion": no_del,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            s = agg[c["source"]]
            s["n"] += 1
            s["kept_rows"] += reason is None
            s["no_deletion_rows"] += no_del and reason is None
            s[f"filtered_{reason}"] += reason is not None
            s["comp_rate_sum"] += a.comp_rate
            s["variation_sum"] += a.variation_rate
            s["protected_total"] += a.protected_total
            s["protected_kept"] += a.protected_kept

    stats = {"teacher": teacher.model, "chunks": len(chunks), "errors": len(errors),
             "wall_seconds": round(wall, 1), "error_examples": dict(list(errors.items())[:5]), "sources": {}}
    for src, s in sorted(agg.items()):
        n = s["n"]
        stats["sources"][src] = {
            "n": int(n), "kept_rows": int(s["kept_rows"]), "no_deletion_rows": int(s["no_deletion_rows"]),
            "mean_comp_rate": round(s["comp_rate_sum"] / n, 3),
            "mean_variation": round(s["variation_sum"] / n, 3),
            "protected_keep_rate": round(s["protected_kept"] / max(1, s["protected_total"]), 3),
            **{k: int(v) for k, v in s.items() if k.startswith("filtered_")},
        }
    (outdir / f"{tag}.stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
