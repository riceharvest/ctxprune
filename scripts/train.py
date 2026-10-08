"""Fine-tune a token classifier (keep / drop) on teacher labels.

    # first model
    python scripts/train.py --data /mnt/ssd/ctxprune-data/v1 --out runs/v1-mmbert-small
    # more data later: continue from the previous model instead of starting over
    python scripts/train.py --data /mnt/ssd/ctxprune-data/v2 --init-from runs/v1-mmbert-small/final \
        --out runs/v2-mmbert-small
    # resume an interrupted run from its last checkpoint
    python scripts/train.py ... --out runs/v1-mmbert-small --resume

Use /mnt/ssd/b70-venv/bin/python on the B70 (torch XPU); any torch works on CPU.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.atoms import Atom  # noqa: E402
from ctxprune.tokenlabels import IGNORE, atom_spans, char_to_atom, special_wrap, token_atoms, token_labels  # noqa: E402


def load_rows(paths: list[Path], no_deletion_frac: float, seed: int) -> tuple[list[dict], list[dict], dict]:
    rng = random.Random(seed)
    train, val = [], []
    stats = {"rows": 0, "filtered": 0, "no_deletion_dropped": 0}
    for p in paths:
        with p.open() as f:
            for line in f:
                r = json.loads(line)
                stats["rows"] += 1
                if r["filtered"]:
                    stats["filtered"] += 1
                    continue
                if r.get("no_deletion") and rng.random() >= no_deletion_frac:
                    stats["no_deletion_dropped"] += 1
                    continue
                if r["split"] == "train":
                    train.append(r)
                elif r["split"] == "val":
                    val.append(r)
    return train, val, stats


def balance(rows: list[dict], mix: dict[str, float], seed: int, slack: float = 1.5) -> tuple[list[dict], dict]:
    """Cap sources that exceed `slack` x their corpus-mix share (downsample only).

    Labeling order can over-represent a source (e.g. the first sequential run
    labeled fineweb2 first); this trims it without throwing away other data."""
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["source"], []).append(r)
    n0 = len(rows)
    wsum = sum(w for s, w in mix.items() if s in by)
    rng = random.Random(seed + 2)
    out, counts = [], {}
    for s, rs in sorted(by.items()):
        cap = round(slack * n0 * mix[s] / wsum) if s in mix else len(rs)
        keep = rng.sample(rs, cap) if len(rs) > cap else rs
        out.extend(keep)
        counts[s] = len(keep)
    rng.shuffle(out)
    return out, counts


def encode(rows: list[dict], tok, max_len: int) -> list[dict]:
    pre, post = special_wrap(tok)
    room = max_len - len(pre) - len(post)
    out = []
    for r in rows:
        atoms = [Atom(ws, t) for ws, t in r["atoms"]]
        full, spans = atom_spans(atoms)
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        tok_atom = token_atoms(enc["offset_mapping"], char_to_atom(spans, len(full)))
        labels = token_labels(tok_atom, r["keep"])[:room]
        ids = enc["input_ids"][:room]
        out.append({
            "input_ids": pre + ids + post,
            "attention_mask": [1] * (len(pre) + len(ids) + len(post)),
            "labels": [IGNORE] * len(pre) + labels + [IGNORE] * len(post),
        })
    return out


def compute_metrics(pred):
    import numpy as np

    logits, labels = pred
    yhat = logits.argmax(-1)
    m = labels != IGNORE
    y, p = labels[m], yhat[m]
    res = {"accuracy": float((y == p).mean())}
    f1s = []
    for cls, name in ((1, "keep"), (0, "drop")):
        tp = float(((p == cls) & (y == cls)).sum())
        prec = tp / max(1.0, float((p == cls).sum()))
        rec = tp / max(1.0, float((y == cls).sum()))
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        res.update({f"{name}_precision": prec, f"{name}_recall": rec, f"{name}_f1": f1})
        f1s.append(f1)
    res["macro_f1"] = float(np.mean(f1s))
    # ROC-AUC of P(keep) vs teacher labels (rank-based, ties averaged). Compression keeps the
    # top-k atoms by score, so ranking quality matters more than the 0.5 threshold.
    z = logits[m].astype(np.float64)
    score = z[:, 1] - z[:, 0]  # monotone in softmax P(keep)
    order = score.argsort(kind="mergesort")
    ranks = np.empty(len(score))
    s_sorted = score[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())
    res["auc"] = float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / max(1, n_pos * n_neg))
    res["keep_rate_pred"] = float((p == 1).mean())
    res["keep_rate_true"] = float((y == 1).mean())
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path, nargs="+", help="one or more data dirs")
    ap.add_argument("--labels", nargs="+", default=["qwen38"],
                    help="labeled/<name>.jsonl inside each data dir; several names = train on all of them. "
                         "Rows of one chunk labeled by a conservative and an aggressive prompt then act as "
                         "graded targets: P(keep) learns 1 for atoms both keep, ~0.5 for v1-only atoms.")
    ap.add_argument("--base", default="jhu-clsp/mmBERT-small")
    ap.add_argument("--init-from", default=None, help="previous model dir to continue training from")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--no-deletion-frac", type=float, default=0.5,
                    help="fraction of 'teacher kept everything' rows to train on")
    ap.add_argument("--max-train", type=int, default=None, help="cap train rows (smoke tests)")
    ap.add_argument("--no-balance", action="store_true",
                    help="train on rows as labeled instead of downsampling to the corpus mix")
    ap.add_argument("--train-frac", type=float, default=1.0,
                    help="random fraction of train rows (learning-curve runs); val is unchanged")
    ap.add_argument("--device", default=None, help="cpu to force CPU")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import torch
    from transformers import (AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification,
                              Trainer, TrainingArguments)

    paths = [d / "labeled" / f"{name}.jsonl" for d in args.data for name in args.labels]
    train_rows, val_rows, stats = load_rows(paths, args.no_deletion_frac, args.seed)
    if not args.no_balance:
        from ctxprune.sources import DEFAULT_MIX

        before = len(train_rows)
        train_rows, counts = balance(train_rows, DEFAULT_MIX, args.seed)
        stats["balanced"] = {"before": before, "after": len(train_rows), "per_source": counts}
    if args.train_frac < 1:
        rng = random.Random(args.seed + 1)
        train_rows = [r for r in train_rows if rng.random() < args.train_frac]
    if args.max_train:
        train_rows = train_rows[: args.max_train]
        val_rows = val_rows[: max(20, args.max_train // 10)]
    src = args.init_from or args.base
    tok = AutoTokenizer.from_pretrained(src)
    t0 = time.time()
    train_ds, val_ds = encode(train_rows, tok, args.max_len), encode(val_rows, tok, args.max_len)
    print(f"train {len(train_ds)} val {len(val_ds)} rows; encode {time.time() - t0:.1f}s; {stats}", flush=True)

    model = AutoModelForTokenClassification.from_pretrained(
        src, num_labels=2, id2label={0: "drop", 1: "keep"}, label2id={"drop": 0, "keep": 1})
    use_cpu = args.device == "cpu"
    accel = not use_cpu and ((hasattr(torch, "xpu") and torch.xpu.is_available()) or torch.cuda.is_available())
    targs = TrainingArguments(
        output_dir=str(args.out),
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size * 2,
        gradient_accumulation_steps=args.grad_accum,
        warmup_steps=0.1,  # float < 1 = ratio of total steps (transformers 5)
        weight_decay=0.01,
        lr_scheduler_type="linear",
        bf16=accel,
        use_cpu=use_cpu,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="auc",
        logging_steps=25,
        dataloader_num_workers=2,
        report_to="none",
        seed=args.seed,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train_ds, eval_dataset=val_ds,
                      data_collator=DataCollatorForTokenClassification(tok), processing_class=tok,
                      compute_metrics=compute_metrics)
    t0 = time.time()
    trainer.train(resume_from_checkpoint=True if args.resume else None)
    train_s = time.time() - t0
    metrics = trainer.evaluate()
    final = args.out / "final"
    trainer.save_model(str(final))
    tok.save_pretrained(str(final))
    meta = {"args": {k: str(v) for k, v in vars(args).items()}, "data_stats": stats,
            "train_rows": len(train_ds), "val_rows": len(val_ds), "train_seconds": round(train_s, 1),
            "val_metrics": metrics}
    (final / "train_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
