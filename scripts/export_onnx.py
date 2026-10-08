"""Export a trained compressor to ONNX (fp32 + dynamic int8) and check parity.

    uv run python scripts/export_onnx.py --model runs/v1-final-v1v3/final

Writes <model>/onnx/model.onnx and model_quantized.onnx, then compares
P(keep) against PyTorch on held-out chunks and reports CPU latency.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--data", type=Path, default=Path("/mnt/ssd/ctxprune-data/v1"))
    ap.add_argument("--n-check", type=int, default=40)
    args = ap.parse_args()

    import numpy as np
    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    out = args.model / "onnx"
    out.mkdir(exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(args.model, attn_implementation="eager").eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits

    ex = tok(["hello world"] * 2, return_tensors="pt", padding=True)
    fp32 = out / "model.onnx"
    torch.onnx.export(
        Wrap(model), (ex["input_ids"], ex["attention_mask"]), str(fp32),
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                      "logits": {0: "batch", 1: "seq"}},
        opset_version=17, dynamo=False,
    )
    # Unsigned int8 on MatMul + embedding Gather, per channel, classifier head left in fp32.
    # Signed int8 (QInt8) or quantizing the head breaks the model (keep agreement ~0.1-0.84).
    import onnx

    g = onnx.load(str(fp32), load_external_data=False)
    head = [n.name for n in g.graph.node if n.op_type == "MatMul" and ("classifier" in n.name or "/head/" in n.name)]
    q8 = out / "model_quantized.onnx"
    quantize_dynamic(str(fp32), str(q8), weight_type=QuantType.QUInt8, per_channel=True,
                     op_types_to_quantize=["MatMul", "Gather"], nodes_to_exclude=head)

    # Parity + latency on held-out chunks.
    texts = []
    for p in sorted((args.data / "chunks").glob("*.jsonl")):
        with p.open() as f:
            for line in f:
                c = json.loads(line)
                if c["split"] == "test":
                    texts.append(c["text"])
    texts = texts[:: max(1, len(texts) // args.n_check)][: args.n_check]
    sessions = {name: ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
                for name, path in (("fp32", fp32), ("int8", q8))}
    report = {"sizes_mb": {n: round(p.stat().st_size / 1e6, 1) for n, p in (("fp32", fp32), ("int8", q8))}}
    diffs = {n: [] for n in sessions}
    agree = {n: [] for n in sessions}
    times = {n: 0.0 for n in [*sessions, "torch"]}
    for t in texts:
        enc = tok(t, return_tensors="np", truncation=True, max_length=512)
        feed = {"input_ids": enc["input_ids"].astype(np.int64), "attention_mask": enc["attention_mask"].astype(np.int64)}
        t0 = time.time()
        with torch.no_grad():
            ref = model(input_ids=torch.from_numpy(feed["input_ids"]),
                        attention_mask=torch.from_numpy(feed["attention_mask"])).logits.softmax(-1)[0, :, 1].numpy()
        times["torch"] += time.time() - t0
        for n, s in sessions.items():
            t0 = time.time()
            lg = s.run(["logits"], feed)[0][0]
            times[n] += time.time() - t0
            p = np.exp(lg - lg.max(-1, keepdims=True))
            p = (p / p.sum(-1, keepdims=True))[:, 1]
            diffs[n].append(float(np.abs(p - ref).mean()))
            agree[n].append(float(((p >= 0.5) == (ref >= 0.5)).mean()))
    for n in sessions:
        report[n] = {"mean_abs_prob_diff": round(float(np.mean(diffs[n])), 5),
                     "keep_decision_agreement": round(float(np.mean(agree[n])), 4),
                     "ms_per_chunk": round(1000 * times[n] / len(texts), 1)}
    report["torch_ms_per_chunk"] = round(1000 * times["torch"] / len(texts), 1)
    (out / "parity.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
