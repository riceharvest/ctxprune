"""Export the frozen v2 line model to ONNX (fp32 + dynamic int8, the v0.2.1 recipe from ~/ctxprune/scripts/export_onnx.py),
check parity against torch on real eval items with the v2 windowing, and time it on CPU.

  export:  eager attention, opset 17, dynamo=False, traced on a real ~1024-token window; int8 = quantize_dynamic(QUInt8,
           per_channel, MatMul + Gather), classifier/head MatMuls left fp32 (v0.2.1: signed int8 or a quantized head
           broke the model). Parity on --n-check usage items (with intent), windows of 1024 tokens via
           train_lines.encode, per-line mean keep prob (= train_lines.score / v2_server.scores), torch fp32 reference
           (no autocast). Reports token |dp|, keep agreement (p >= .5), line-score |d|, and kept-line overlap of
           methods._select at the 0.5 budget (Jaccard + share of torch's lines kept + identical selections), split by
           window length (<= 256 vs > 256 tokens: a mask baked in by the tracer shows up as long-window drift).
  latency: onnxruntime CPU, intra_op_num_threads=--threads (4), inter_op 1, as research/enc-bench/interleave.py:
           (1) fixed 512/1024-token windows of the enc-bench mixed text, 4 interleaved rounds (3 warmup, median of 15 /
               8), incl. the released v0.2.1 int8 (darioooooo0o/ctxprune-small) as the cross-machine anchor
               (encoders.md: ~116 ms idle desktop, ~137 ms at load 8, per 512 tokens, 4 threads);
           (2) end to end with the v2 windowing: 1k-char and 20k-char code (enc-bench code_python.txt), 20k chars of
               real tool outputs (usage_v1_s400), with and without intent; total ms incl. tokenization and model-only.
           Records nproc, CPU model, threads per core, load1. Run it as its own process with nothing else on the CPU.

  python export_v2_onnx.py export  --model models/v2 --out out/evals/onnx --data data/bench/usage_v1_s400.jsonl
  python export_v2_onnx.py latency --model models/v2 --onnx-dir out/evals/onnx --ref-onnx <v0.2.1 int8> --out lat.json

Speed step (PLAN §2.6): --max-len / --line-cap set the runtime windowing (train_lines.encode) for export parity, latency
and `score` (speed2: --intent-cap too); the trace example is always a >= 900-token window of the 1024 windowing (dynamic axes cover shorter ones).
  python export_v2_onnx.py score --model models/v2 --onnx out/x/model_quantized.onnx --data <usage|squeez test> --out s.json
           ONNX (CPU) line scores in train_lines.score's format (one list per item, same order; Squeez rows with no gold
           line get [] with --skip-no-gold: eval_squeez skips them and make_scored pads).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

MAX_LEN = 1024


def load_tok(path):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path)
    if tok.cls_token_id is None:
        tok.cls_token = tok.bos_token or tok.eos_token
    if tok.sep_token_id is None:
        tok.sep_token = tok.eos_token or tok.cls_token
    return tok


def windows(tok, lines, intent, max_len=MAX_LEN, line_cap=0, intent_cap=0):
    from line_encode import encode
    out, start = [], 0
    while start < len(lines):
        ids, line_of, end, _ = encode(tok, intent, lines, max_len, None, start, line_cap, intent_cap)
        out.append((ids, line_of))
        start = max(end, start + 1)
    return out


def ort_probs(sess, ids):
    import numpy as np
    a = np.array([ids], dtype=np.int64)
    lg = sess.run(["logits"], {"input_ids": a, "attention_mask": np.ones_like(a)})[0][0]
    p = np.exp(lg - lg.max(-1, keepdims=True))
    return (p / p.sum(-1, keepdims=True))[:, 1]


def line_scores(n_lines, wins, probs_per_win):
    scores = [0.0] * n_lines
    for (ids, line_of), p in zip(wins, probs_per_win):
        acc: dict[int, list[float]] = {}
        for li, pp in zip(line_of, p):
            if li >= 0:
                acc.setdefault(li, []).append(float(pp))
        for li, ps in acc.items():
            scores[li] = sum(ps) / len(ps)
    return scores


def ort_session(path, threads: int):
    """CPU session as everywhere in this file: intra_op = threads, inter_op 1, quiet."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def onnx_line_scores(sess, tok, lines, intent, max_len=MAX_LEN, line_cap=0, intent_cap=0):
    """Per-line scores of one tool output with an ONNX session: the v2 runtime path (windows -> probs -> per-line mean),
    shared by `score`, v2_server --onnx and speed3."""
    wins = windows(tok, lines, intent, max_len, line_cap, intent_cap)
    return line_scores(len(lines), wins, [ort_probs(sess, w[0]) for w in wins]), len(wins)


def kept_idx(lines, scores, budget_chars):
    """The line indices methods._select keeps (same greedy order and budget rule)."""
    order = sorted(range(len(lines)), key=lambda i: (-scores[i], i))
    keep, used = set(), 0
    for i in order:
        if used + len(lines[i]) + 1 > budget_chars and used > 0:
            continue
        keep.add(i)
        used += len(lines[i]) + 1
    return keep


def read_items(path, n):
    items = []
    for l in open(path):
        it = json.loads(l)
        items.append({"lines": it["text"].splitlines(), "intent": it.get("intent", ""), "text": it["text"]})
        if len(items) >= n:
            break
    return items


def export(a):
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForTokenClassification
    from train_lines import load_model

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = load_tok(a.model)
    items = read_items(a.data, a.n_check)
    # trace on a real long window (not "hello world"): ModernBERT's sliding-window mask under a transformers-5.x trace
    ex_ids = next((w[0] for it in items for w in windows(tok, it["lines"], it["intent"]) if len(w[0]) >= 900),
                  windows(tok, items[0]["lines"], items[0]["intent"])[0][0])
    model = AutoModelForTokenClassification.from_pretrained(a.model, attn_implementation="eager", dtype=torch.float32).eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits

    fp32, q8 = out / "model.onnx", out / "model_quantized.onnx"
    ex = torch.tensor([ex_ids], dtype=torch.long)
    t0 = time.time()
    with torch.no_grad():
        torch.onnx.export(Wrap(model), (ex, torch.ones_like(ex)), str(fp32),
                          input_names=["input_ids", "attention_mask"], output_names=["logits"],
                          dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                                        "logits": {0: "batch", 1: "seq"}},
                          opset_version=17, dynamo=False)
    g = onnx.load(str(fp32), load_external_data=False)
    head = [n.name for n in g.graph.node if n.op_type == "MatMul" and ("classifier" in n.name or "/head/" in n.name)]
    print("head MatMuls kept fp32:", head, flush=True)
    if not head:
        print("WARNING: no classifier/head MatMul matched; the head gets quantized (v0.2.1: that breaks parity)", flush=True)
    quantize_dynamic(str(fp32), str(q8), weight_type=QuantType.QUInt8, per_channel=True,
                     op_types_to_quantize=["MatMul", "Gather"], nodes_to_exclude=head)
    del model, g
    report: dict = {"export_seconds": round(time.time() - t0, 1), "trace_len": len(ex_ids), "head_excluded": head,
                    "sizes_mb": {n: round(p.stat().st_size / 1e6, 1) for n, p in (("fp32", fp32), ("int8", q8))},
                    "versions": {"torch": torch.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__,
                                 "transformers": __import__("transformers").__version__}}

    # parity: torch fp32 reference = the scoring model (train_lines.load_model, default attention), no autocast
    dev = a.ref_device or ("cuda" if torch.cuda.is_available() else "cpu")
    ref = load_model(a.model, dev, False).eval()
    so = ort.SessionOptions()
    so.intra_op_num_threads = os.cpu_count() or 4
    so.log_severity_level = 3
    sess = {n: ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"]) for n, p in (("fp32", fp32), ("int8", q8))}
    st = {n: {"dp_short": [], "dp_long": [], "agree": [], "line_d": [], "jacc": [], "recall": [], "same": [], "max_dp": 0.0}
          for n in sess}
    for it in items:
        wins = windows(tok, it["lines"], it["intent"], a.max_len, a.line_cap, a.intent_cap)
        with torch.no_grad():
            pr = [ref(input_ids=torch.tensor([w[0]], device=dev)).logits.float().softmax(-1)[0, :, 1].cpu().numpy() for w in wins]
        ls_ref = line_scores(len(it["lines"]), wins, pr)
        k_ref = kept_idx(it["lines"], ls_ref, 0.5 * len(it["text"]))
        for n, s in sess.items():
            po = [ort_probs(s, w[0]) for w in wins]
            for w, p0, p1 in zip(wins, pr, po):
                d = np.abs(p1 - p0)
                st[n]["dp_short" if len(w[0]) <= 256 else "dp_long"].append(float(d.mean()))
                st[n]["agree"].append(float(((p1 >= 0.5) == (p0 >= 0.5)).mean()))
                st[n]["max_dp"] = max(st[n]["max_dp"], float(d.max()))
            ls = line_scores(len(it["lines"]), wins, po)
            st[n]["line_d"].append(sum(abs(x - y) for x, y in zip(ls, ls_ref)) / max(1, len(ls)))
            k = kept_idx(it["lines"], ls, 0.5 * len(it["text"]))
            st[n]["jacc"].append(len(k & k_ref) / max(1, len(k | k_ref)))
            st[n]["recall"].append(len(k & k_ref) / max(1, len(k_ref)))
            st[n]["same"].append(float(k == k_ref))
    m = lambda xs: round(float(np.mean(xs)), 5) if xs else None  # noqa: E731
    report["parity"] = {"n_items": len(items), "ref": f"torch fp32 on {dev}, default attention",
                        "max_len": a.max_len, "line_cap": a.line_cap, "intent_cap": a.intent_cap,
                        **{n: {"token_mean_abs_dp_win_le256": m(v["dp_short"]), "token_mean_abs_dp_win_gt256": m(v["dp_long"]),
                               "token_max_abs_dp": round(v["max_dp"], 4), "token_keep_agree": m(v["agree"]),
                               "line_score_mean_abs_d": m(v["line_d"]), "kept_lines_jaccard_at_0.5": m(v["jacc"]),
                               "torch_kept_lines_also_kept_at_0.5": m(v["recall"]), "identical_selection_rate": m(v["same"]),
                               "n_windows_le256": len(v["dp_short"]), "n_windows_gt256": len(v["dp_long"])} for n, v in st.items()}}
    (out / "parity.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1), flush=True)


def box_info():
    info = {"nproc": os.cpu_count(), "affinity": len(os.sched_getaffinity(0)), "load1": os.getloadavg()[0]}
    try:
        for ln in open("/proc/cpuinfo"):
            if ln.startswith("model name"):
                info["cpu_model"] = ln.split(":", 1)[1].strip()
                break
        lc = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        for ln in lc.splitlines():
            k = ln.split(":", 1)[0].strip()
            if k in ("Thread(s) per core", "Core(s) per socket", "Socket(s)", "Flags") and ":" in ln:
                v = ln.split(":", 1)[1].strip()
                info[k] = " ".join(f for f in v.split() if f.startswith(("avx512", "avx2", "amx", "avx_vnni"))) if k == "Flags" else v
    except Exception as e:  # noqa: BLE001
        info["err"] = repr(e)
    return info


def latency(a):
    import numpy as np
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = a.threads
    so.inter_op_num_threads = 1
    so.log_severity_level = 3
    od = Path(a.onnx_dir)
    paths = {"v2_int8": od / "model_quantized.onnx", "v2_fp32": od / "model.onnx"}
    paths = {n: p for n, p in paths.items() if p.exists()}
    if a.ref_onnx and Path(a.ref_onnx).exists():
        paths["v021_int8"] = Path(a.ref_onnx)
    sess = {n: ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"]) for n, p in paths.items()}
    tok = load_tok(a.model)  # v0.2.1 is an mmBERT-small fine-tune: same vocabulary
    res: dict = {"box": box_info(), "threads": a.threads, "onnxruntime": ort.__version__, "models": {n: str(p) for n, p in paths.items()},
                 "max_len": a.max_len, "line_cap": a.line_cap, "intent_cap": a.intent_cap}
    sd = Path(a.samples)

    # (1) fixed windows, interleaved rounds (enc-bench mixed text, as interleave.py)
    text = "\n".join((sd / f).read_text() for f in ["code_python.txt", "prose_en.txt", "json_log.txt", "prose_de.txt"] * 4)
    ids = tok(text, add_special_tokens=False)["input_ids"]
    feeds = {n: {"input_ids": np.array([ids[:n]], dtype=np.int64)} for n in a.fixed_lens}
    for f in feeds.values():
        f["attention_mask"] = np.ones_like(f["input_ids"])
    fixed = {k: {n: [] for n in a.fixed_lens} for k in sess}
    loads = []
    for rnd in range(a.fixed_rounds):
        names = list(sess)
        for name in names[rnd % len(names):] + names[: rnd % len(names)]:
            for n, runs in ((n, 15 if n <= 512 else 8) for n in a.fixed_lens):
                s, f = sess[name], feeds[n]
                for _ in range(3):
                    s.run(None, f)
                ts = []
                for _ in range(runs):
                    t0 = time.perf_counter()
                    s.run(None, f)
                    ts.append((time.perf_counter() - t0) * 1000)
                fixed[name][n].append(statistics.median(ts))
        loads.append(round(os.getloadavg()[0], 2))
    res["fixed_windows_ms"] = {k: {f"{n}_median": round(statistics.median(v[n]), 1) for n in v} | {f"{n}_min": round(min(v[n]), 1) for n in v}
                               for k, v in fixed.items()}
    res["load1_per_round"] = loads

    # (2) end to end with the v2 windowing (--max-len windows, --line-cap, per-line mean prob), v2 int8 + fp32
    def cut(s, n):
        s = (s * (n // max(1, len(s)) + 2))[:n]
        return s[: s.rfind("\n")] if "\n" in s else s

    code = (sd / "code_python.txt").read_text()
    real, intent, long_intent = "", "", ""
    for l in open(a.usage):
        it = json.loads(l)
        intent = intent or it.get("intent", "")
        long_intent = max(long_intent, it.get("intent", ""), key=len)   # encode keeps its last 1200 chars / 200 tokens
        if len(real) < 20000:
            real += it["text"] + "\n"
    inputs = {"code_1k": (cut(code, 1000), ""), "code_20k": (cut(code, 20000), ""), "code_20k_intent": (cut(code, 20000), intent),
              "real_20k_intent": (cut(real, 20000), intent), "real_20k": (cut(real, 20000), ""),
              "real_20k_longintent": (cut(real, 20000), long_intent)}
    inputs = {k: v for k, v in inputs.items() if k in a.inputs}
    # query tokens per window, as train_lines.encode takes them (first 200, or the last --intent-cap)
    # (= positions with no line in a one-line window, minus CLS + 2 SEP)
    res["intent_tokens"] = {k: sum(li < 0 for li in windows(tok, ["x"], v[1], a.max_len, 0, a.intent_cap)[0][1]) - 3
                            for k, v in inputs.items()}
    e2e: dict = {}
    for rnd in range(a.rounds):
        for name in [n for n in ("v2_int8", "v2_fp32") if n in sess][:: 1 if rnd % 2 == 0 else -1]:
            for key, (txt, q) in inputs.items():
                runs = 9 if key == "code_1k" else 3
                tot, mod = [], []
                for r in range(runs + 1):
                    t0 = time.perf_counter()
                    lines = txt.splitlines()
                    wins = windows(tok, lines, q, a.max_len, a.line_cap, a.intent_cap)
                    tm = 0.0
                    probs = []
                    for w in wins:
                        t1 = time.perf_counter()
                        probs.append(ort_probs(sess[name], w[0]))
                        tm += time.perf_counter() - t1
                    line_scores(len(lines), wins, probs)
                    if r:  # first run = warmup
                        tot.append((time.perf_counter() - t0) * 1000)
                        mod.append(tm * 1000)
                d = e2e.setdefault(name, {}).setdefault(key, {"chars": len(txt), "windows": len(wins),
                                                              "tokens": sum(len(w[0]) for w in wins), "total": [], "model": []})
                d["total"] += tot
                d["model"] += mod
    for name, per in e2e.items():
        for key, d in per.items():
            d["total_ms"] = round(statistics.median(d.pop("total")), 1)
            d["model_ms"] = round(statistics.median(d.pop("model")), 1)
            d["ms_per_1k_chars"] = round(d["total_ms"] / (d["chars"] / 1000), 1)
    res["end_to_end"] = e2e
    res["load1_end"] = round(os.getloadavg()[0], 2)

    # cross-machine anchor: scale box ms onto the desktop numbers in research/encoders.md via the v0.2.1 int8
    if "v021_int8" in res["fixed_windows_ms"] and 512 in a.fixed_lens and "v2_int8" in res["fixed_windows_ms"]:
        ref512 = res["fixed_windows_ms"]["v021_int8"]["512_median"]
        res["desktop_estimate"] = {
            "note": "box ms x (desktop v0.2.1 int8 ms per 512 / box v0.2.1 int8 ms per 512); 116 = idle anchor, 137 = load ~8",
            "v2_int8_vs_v021_int8_per_512": round(res["fixed_windows_ms"]["v2_int8"]["512_median"] / ref512, 3),
            **{f"{name}.{key}_ms_per_1k_chars_desktop_{anc}": round(d["ms_per_1k_chars"] * anc / ref512, 1)
               for name, per in e2e.items() for key, d in per.items() if key in ("code_20k", "real_20k_intent") for anc in (116, 137)},
            **{f"{name}.{key}_total_ms_desktop_{anc}": round(d["total_ms"] * anc / ref512, 1)
               for name, per in e2e.items() for key, d in per.items() if key in ("code_20k", "real_20k_intent") for anc in (116, 137)}}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1), flush=True)


def score(a):
    """ONNX (CPU) line scores for an eval file, same format and item order as train_lines.score (intent mode default)."""
    threads = a.threads or os.cpu_count() or 4
    sess = ort_session(a.onnx, threads)
    tok = load_tok(a.model)
    if a.data.endswith(("encoder_test.jsonl", "encoder_dev.jsonl")):
        from train_lines import load_squeez   # imports torch; only needed for the Squeez files
        items = [{"intent": r["query"], "lines": r["lines"], "skip": a.skip_no_gold and not any(r["labels"])}
                 for r in load_squeez(a.data)]
    else:
        items = [{"intent": (it := json.loads(l)).get("intent", ""), "lines": it["text"].splitlines(), "skip": False}
                 for l in open(a.data)]
    out, t0, n_win = [], time.time(), 0
    for it in items:
        if it["skip"]:
            out.append([])
            continue
        sc, nw = onnx_line_scores(sess, tok, it["lines"], "" if a.no_intent else it["intent"], a.max_len, a.line_cap,
                                  a.intent_cap)
        n_win += nw
        out.append(sc)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out))
    print(f"scored {len(items)} items ({sum(not i['skip'] for i in items)} non-skipped, {n_win} windows) in "
          f"{time.time() - t0:.0f}s with {a.onnx} ({threads} threads)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--model", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--data", default="data/bench/usage_v1_s400.jsonl")
    e.add_argument("--n-check", type=int, default=50)
    e.add_argument("--ref-device", default=None)
    e.add_argument("--max-len", type=int, default=MAX_LEN, help="parity windowing (runtime window length)")
    e.add_argument("--line-cap", type=int, default=0, help="parity windowing: tokens per line cap (0 = off)")
    e.add_argument("--intent-cap", type=int, default=0, help="parity windowing: last N intent tokens (0 = off)")
    t = sub.add_parser("latency")
    t.add_argument("--model", required=True, help="model dir (tokenizer)")
    t.add_argument("--onnx-dir", required=True)
    t.add_argument("--ref-onnx", default=None, help="released v0.2.1 int8 ONNX (cross-machine anchor)")
    t.add_argument("--samples", default="research/enc-bench/samples")
    t.add_argument("--usage", default="data/bench/usage_v1_s400.jsonl")
    t.add_argument("--threads", type=int, default=4)
    t.add_argument("--out", required=True)
    t.add_argument("--max-len", type=int, default=MAX_LEN)
    t.add_argument("--line-cap", type=int, default=0)
    t.add_argument("--intent-cap", type=int, default=0, help="last N intent tokens per window (0 = off: first 200)")
    t.add_argument("--fixed-lens", type=int, nargs="*", default=[512, 1024], help="fixed-window benchmark lengths")
    t.add_argument("--fixed-rounds", type=int, default=4)
    t.add_argument("--rounds", type=int, default=3, help="end-to-end rounds (int8/fp32 order alternates)")
    t.add_argument("--inputs", nargs="+", default=["code_1k", "code_20k", "code_20k_intent", "real_20k_intent", "real_20k"],
                   help="end-to-end inputs; also: real_20k_longintent (longest intent in --usage)")
    c = sub.add_parser("score")
    c.add_argument("--model", required=True, help="model dir (tokenizer)")
    c.add_argument("--onnx", required=True)
    c.add_argument("--data", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--max-len", type=int, default=MAX_LEN)
    c.add_argument("--line-cap", type=int, default=0)
    c.add_argument("--intent-cap", type=int, default=0)
    c.add_argument("--threads", type=int, default=0, help="0 = all CPUs")
    c.add_argument("--no-intent", action="store_true")
    c.add_argument("--skip-no-gold", action="store_true", help="Squeez: [] for rows without gold lines (eval skips them)")
    a = ap.parse_args()
    {"export": export, "latency": latency, "score": score}[a.cmd](a)


if __name__ == "__main__":
    main()
