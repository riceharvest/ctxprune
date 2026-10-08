"""Produce real compressor outputs for the launch video (written as a JS data file).

    uv run python scripts/demo_data.py --model <run>/final --out ~/ctxprune-launch-video/data.js
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctxprune.atoms import atomize, is_protected  # noqa: E402

TEXT = """{
  "id": "ord_8f3a2c91",
  "status": "failed",
  "customer": {
    "id": "cus_Q7xK2mP9",
    "email": "j.devries@example.nl"
  },
  "amount": 14950,
  "currency": "eur",
  "error": {
    "code": "card_declined",
    "decline_code": "insufficient_funds",
    "message": "Your card has insufficient funds."
  },
  "retry_after": 3600
}"""


def mangled_form(ident: str, out: str) -> str | None:
    """How a lost identifier shows up in a compressor's output, if its pieces survived
    with spaces inserted (e.g. ord_8f3a2c91 -> "ord _ 8f3a2c91")."""
    pieces = [p for p in re.split(r"(\W)", ident) if p]
    pat = r"\s*".join(re.escape(p) for p in pieces)
    m = re.search(pat, out)
    if m and m.group(0) != ident:
        return m.group(0)
    # otherwise the longest fragment of it that survived (e.g. "_8f3a2c91", "j.devries@example")
    # only fragments that are most of the ID (a short one like "decline" may come from another word)
    for n in range(len(ident) - 1, max(5, math.ceil(0.6 * len(ident))) - 1, -1):
        for i in range(len(ident) - n + 1):
            frag = ident[i:i + n]
            if frag in out:
                return frag
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--qa", type=Path, required=True, help="QA json for our model (DeepSeek reader)")
    ap.add_argument("--qa-base", type=Path, required=True, help="QA json with LLMLingua-2 baselines (DeepSeek reader)")
    ap.add_argument("--speedup", type=float, required=True, help="CPU speedup vs LLMLingua-2 large (measured)")
    args = ap.parse_args()

    from llmlingua import PromptCompressor

    from ctxprune.compress import Compressor

    ll = PromptCompressor(model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                          use_llmlingua2=True, device_map="cpu")
    ll_out = ll.compress_prompt(TEXT, rate=args.rate, force_tokens=["\n", "?"])["compressed_prompt"]

    c = Compressor(args.model, device="cpu")
    atoms, scores, _ = c.atom_scores(TEXT)
    res = c.compress(TEXT, rate=args.rate, force_protected=True)
    out_atoms = {a.text for a in atomize(res["text"])}
    # recover the keep mask by re-running the selection deterministically
    keep_mask = _keep_mask(c, TEXT, args.rate)

    prot = sorted({a.text for a in atoms if is_protected(a.text)}, key=TEXT.index)
    ll_have = {a.text for a in atomize(ll_out)}
    ll_broken = [{"id": p, "as": mangled_form(p, ll_out)} for p in prot if p not in ll_have]

    def valid(s: str) -> bool:
        try:
            json.loads(s)
            return True
        except ValueError:
            return False

    data = {
        "input": TEXT,
        "rate": args.rate,
        "llmlingua": {"model": "LLMLingua-2 large", "text": ll_out, "broken": ll_broken,
                      "json_valid": valid(ll_out), "chars": len(ll_out)},
        "ours": {"atoms": [[a.ws, a.text, bool(k), is_protected(a.text)] for a, k in zip(atoms, keep_mask)],
                 "text": res["text"], "ids_kept": sum(1 for p in prot if p in out_atoms),
                 "ids_total": len(prot), "chars": len(res["text"])},
        "input_chars": len(TEXT),
    }
    qa, qb = json.loads(args.qa.read_text()), json.loads(args.qa_base.read_text())
    data["results"] = {
        "original": round(100 * qa["original"]["_all"]["em"], 1),
        "ours": round(100 * qa["ours@0.5"]["_all"]["em"], 1),
        "ll_large": round(100 * qb["llmlingua2-large@0.5"]["_all"]["em"], 1),
        "ll_base": round(100 * qb["llmlingua2@0.5"]["_all"]["em"], 1),
        "n_questions": qa["ours@0.5"]["_all"]["n"],
        "speedup": args.speedup,
    }
    args.out.write_text("window.DEMO = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print(json.dumps({k: v for k, v in data.items() if k != "ours"}, indent=1, ensure_ascii=False)[:1500])
    print("ours:", res["text"])
    print(f"ours IDs kept {data['ours']['ids_kept']}/{data['ours']['ids_total']}")


def _keep_mask(c, text: str, rate: float) -> list[bool]:
    """Same selection as Compressor.compress(force_protected=True), returned as a mask."""
    import math

    atoms, scores, _ = c.atom_scores(text)
    counts = [len(a.text) + (1 if a.ws else 0) for a in atoms]
    keep = [bool(a.text) and is_protected(a.text) for a in atoms]
    budget = math.ceil(rate * sum(counts))
    used = sum(n for n, k in zip(counts, keep) if k)
    for i in sorted(range(len(atoms)), key=lambda i: -scores[i]):
        if used >= budget:
            break
        if not keep[i] and atoms[i].text:
            keep[i] = True
            used += counts[i]
    return keep


if __name__ == "__main__":
    main()
