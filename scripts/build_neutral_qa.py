"""Neutral QA sets in eval_qa.py's questions.jsonl format, from public benchmarks headroom also evaluates on.

    python scripts/build_neutral_qa.py --out /mnt/ssd/ctxprune-data/neutral

SQuAD v2 (answerable validation questions, one per paragraph) and HotpotQA distractor validation
(10 paragraphs per question, yes/no answers dropped). Answers must appear verbatim in the context,
the same rule eval_qa.py applies to its own generated questions. None of this is ctxprune training data.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from datasets import load_dataset


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n", type=int, default=200, help="questions per benchmark")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    rows = []

    squad = [r for r in load_dataset("rajpurkar/squad_v2", split="validation") if r["answers"]["text"]]
    seen, picked = set(), []
    for r in rng.sample(squad, len(squad)):
        if r["context"] in seen:
            continue
        seen.add(r["context"])
        picked.append(r)
        if len(picked) == args.n:
            break
    for r in picked:
        rows.append({"qid": f"squad:{r['id']}", "chunk_id": f"squad:{r['id']}", "source": "squad_v2",
                     "text": r["context"], "question": r["question"], "answer": r["answers"]["text"][0]})

    hotpot = load_dataset("hotpotqa/hotpot_qa", "distractor", split="validation")
    idx = list(range(len(hotpot)))
    rng.shuffle(idx)
    count = 0
    for i in idx:
        r = hotpot[i]
        if r["answer"].lower() in ("yes", "no"):
            continue
        text = "\n\n".join(f"{t}: {''.join(s)}" for t, s in zip(r["context"]["title"], r["context"]["sentences"]))
        if r["answer"] not in text:
            continue
        rows.append({"qid": f"hotpot:{r['id']}", "chunk_id": f"hotpot:{r['id']}", "source": "hotpotqa",
                     "text": text, "question": r["question"], "answer": r["answer"]})
        count += 1
        if count == args.n:
            break

    (args.out / "qa").mkdir(parents=True, exist_ok=True)
    with (args.out / "qa" / "questions.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(len(rows), "questions")


if __name__ == "__main__":
    main()
