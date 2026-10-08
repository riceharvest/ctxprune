"""Assemble the release folder for one trained model and fill the model card from eval results.

    uv run python scripts/build_release.py --model /mnt/ssd/ctxprune-data/runs/v1-final-v1v3/final \
        --name ctxprune-small --repo <user>/ctxprune-small --out release/ctxprune-small

Copies weights + tokenizer + ONNX (run scripts/export_onnx.py first), writes README.md from
release/MODEL_CARD.md with result tables taken from the eval JSONs, and eval/ with the raw numbers.
Uploading is a separate, explicit step (`hf upload <repo> <out>`).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tag_of(model: Path) -> str:
    return re.sub(r"[^\w.-]+", "_", str(model).rstrip("/").replace("/final", "").split("/")[-1])


def qa_table(qa: dict, base: dict | None, ours_label: str) -> str:
    """Markdown table: method x {50%, 33%} exact match, ours first, baselines from `base`."""
    def em(d, key):
        return f"{100 * d[key]['_all']['em']:.1f}" if d and key in d else "-"

    lines = ["| Method | 50% of tokens kept | 33% of tokens kept |", "|---|---|---|",
             f"| Original (no compression) | {em(qa, 'original')} | |",
             f"| **{ours_label}** | **{em(qa, 'ours@0.5')}** | **{em(qa, 'ours@0.33')}** |",
             f"| {ours_label} + `force_protected` | {em(qa, 'ours+ids@0.5')} | {em(qa, 'ours+ids@0.33')} |"]
    src = base or qa
    lines += [f"| LLMLingua-2 large | {em(src, 'llmlingua2-large@0.5')} | {em(src, 'llmlingua2-large@0.33')} |",
              f"| LLMLingua-2 base | {em(src, 'llmlingua2@0.5')} | {em(src, 'llmlingua2@0.33')} |"]
    return "\n".join(lines)


def per_domain(qa: dict, base: dict, ours_label: str) -> str:
    doms = sorted(k for k in qa["ours@0.5"] if not k.startswith("_"))
    head = "| Source | " + " | ".join(d.replace("_", " ") for d in doms) + " |"
    lines = [head, "|---" * (len(doms) + 1) + "|"]
    for label, d, k in ((ours_label, qa, "ours@0.5"), ("LLMLingua-2 large", base, "llmlingua2-large@0.5")):
        lines.append(f"| {label} @50% | " + " | ".join(
            f"{100 * d[k][x]['em']:.0f}" if x in d[k] else "-" for x in doms) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--baseline-model", type=Path, default=Path("/mnt/ssd/ctxprune-data/runs/v1-n3000-small/final"),
                    help="run whose eval JSONs contain the LLMLingua-2 baselines")
    ap.add_argument("--data", type=Path, default=Path("/mnt/ssd/ctxprune-data/v1"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pr-link", default="(link pending)")
    ap.add_argument("--code-link", default="(link pending)")
    args = ap.parse_args()

    tag, btag = tag_of(args.model), tag_of(args.baseline_model)
    qa_dir, ev_dir = args.data / "qa", args.data / "eval"
    ds = json.loads((qa_dir / f"qa_{tag}__deepseek_deepseek-v4-flash.json").read_text())
    ds_base = json.loads((qa_dir / f"qa_{btag}__deepseek_deepseek-v4-flash.json").read_text())
    qw_path = qa_dir / f"qa_{tag}__qwen38.json"
    qw = json.loads(qw_path.read_text()) if qw_path.exists() else None
    qw_base_p = qa_dir / f"qa_{btag}.json"
    qw_base = json.loads(qw_base_p.read_text()) if qw_base_p.exists() else None
    quick = json.loads((ev_dir / f"{tag}.json").read_text())
    quick_base = json.loads((ev_dir / f"{btag}.json").read_text())
    meta = json.loads((args.model / "train_meta.json").read_text())
    parity = json.loads((args.model / "onnx" / "parity.json").read_text())

    def gain(r: str) -> str:
        return f"+{100 * (ds[f'ours@{r}']['_all']['em'] - ds_base[f'llmlingua2-large@{r}']['_all']['em']):.0f} points"

    id_lines = ["| Method | IDs lost | LLM-token ratio |", "|---|---|---|"]
    for label, src, k in ((args.name, quick, "ours@0.5"), (f"{args.name} + force_protected", quick, "ours+ids@0.5"),
                          ("LLMLingua-2 large", quick_base, "llmlingua2-large@0.5"),
                          ("LLMLingua-2 base", quick_base, "llmlingua2@0.5")):
        a = src["methods"][k]["_all"]
        id_lines.append(f"| {label} | {100 * a['lost_rate']:.0f}% | {a['mean_llm_token_ratio']:.2f} |")

    card = (ROOT / "release" / "MODEL_CARD.md").read_text()
    fills = {
        "{{NAME}}": args.name, "{{REPO}}": args.repo, "{{PR_LINK}}": args.pr_link, "{{CODE_LINK}}": args.code_link,
        "{{QA_GAIN_50}}": gain("0.5"), "{{QA_GAIN_33}}": gain("0.33"),
        "{{MS_FP32}}": f"{parity['fp32']['ms_per_chunk']:.0f}", "{{MS_INT8}}": f"{parity['int8']['ms_per_chunk']:.0f}",
        "{{QA_TABLE_DEEPSEEK}}": qa_table(ds, ds_base, args.name) + "\n\nPer source, at 50%:\n\n"
                                 + per_domain(ds, ds_base, args.name),
        "{{QA_TABLE_QWEN}}": qa_table(qw, qw_base, args.name) if qw else "(not run for this model)",
        "{{ID_TABLE}}": "\n".join(id_lines),
        "{{N_CHUNKS}}": f"{meta['train_rows']:,} training",
        "{{TRAIN_MIN}}": f"{meta['train_seconds'] / 60:.0f}",
        "{{OURS_50}}": f"{100 * ds['ours@0.5']['_all']['em']:.0f}%",
    }
    for k, v in fills.items():
        card = card.replace(k, v)
    left = re.findall(r"\{\{\w+\}\}", card)
    if left:
        raise SystemExit(f"unfilled placeholders: {left}")

    out = args.out
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(args.model, out, ignore=shutil.ignore_patterns("checkpoint-*", "training_args.bin",
                                                                    "q_*.onnx", "u8_*", "s8_*", "fp16.onnx"))
    (out / "README.md").write_text(card)
    (out / "eval").mkdir(exist_ok=True)
    for p in (qa_dir / f"qa_{tag}__deepseek_deepseek-v4-flash.json", qw_path, ev_dir / f"{tag}.json",
              qa_dir / f"qa_{btag}__deepseek_deepseek-v4-flash.json", ev_dir / f"{btag}.json"):
        if p.exists():
            shutil.copy(p, out / "eval" / p.name)
    print(f"release folder: {out}")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(out)}  {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
