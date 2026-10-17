"""`ctxprune serve`: the line-model server for the `model` preset (needs `pip install "ctxprune[model]"`).

Wraps scripts/v2_server.py unchanged: its ONNX path loads the tokenizer with `transformers` (export_v2_onnx.load_tok),
which the [model] extra does not install. TokShim is the small subset line_encode.encode uses (callable ->
{"input_ids"}, cls/sep/unk ids), built on `tokenizers` from the model dir's tokenizer.json; it is swapped in for
load_tok before v2_server.main() runs. The model itself is not downloaded here (the v2 weights are not published
yet): pass --model-dir with tokenizer.json (+ tokenizer_config.json / special_tokens_map.json) and the ONNX file.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


class TokShim:
    def __init__(self, model_dir: str):
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        special: dict = {}
        for name in ("special_tokens_map.json", "tokenizer_config.json"):
            try:
                with open(os.path.join(model_dir, name), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            for k in ("cls_token", "sep_token", "unk_token", "bos_token", "eos_token"):
                v = d.get(k)
                if isinstance(v, dict):
                    v = v.get("content")
                if isinstance(v, str) and k not in special:
                    special[k] = v

        def tid(*keys):
            for k in keys:
                t = special.get(k)
                i = self.tok.token_to_id(t) if t else None
                if i is not None:
                    return i
            return None
        # same fallbacks as export_v2_onnx.load_tok: cls <- bos/eos, sep <- eos/cls
        self.cls_token_id = tid("cls_token", "bos_token", "eos_token")
        self.sep_token_id = tid("sep_token", "eos_token", "cls_token")
        self.unk_token_id = tid("unk_token")
        if self.unk_token_id is None:
            self.unk_token_id = self.sep_token_id
        if self.cls_token_id is None or self.sep_token_id is None:
            raise ValueError(f"no cls/sep special tokens found in {model_dir}")

    def __call__(self, text: str, add_special_tokens: bool = False) -> dict:
        return {"input_ids": self.tok.encode(text, add_special_tokens=add_special_tokens).ids}


def serve_main(argv: list) -> int:
    from . import glue
    s = glue.settings()
    ap = argparse.ArgumentParser(prog="ctxprune serve", description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", required=True, help="dir with tokenizer.json and the ONNX file")
    ap.add_argument("--onnx", default="", help="ONNX file (default: the only *.onnx in --model-dir)")
    ap.add_argument("--port", type=int, default=s["port"])
    ap.add_argument("--threads", type=int, default=4)
    a, rest = ap.parse_known_args(argv)
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError as e:
        print(f"ctxprune serve needs the model extra: pip install \"ctxprune[model]\" ({e})", file=sys.stderr)
        return 2
    onnx = a.onnx or next(iter(sorted(glob.glob(os.path.join(a.model_dir, "*.onnx")))), "")
    if not onnx:
        print(f"no .onnx file in {a.model_dir}; pass --onnx", file=sys.stderr)
        return 2
    glue.boot()
    import export_v2_onnx
    export_v2_onnx.load_tok = TokShim   # v2_server.main imports it at call time
    import v2_server
    sys.argv = ["ctxprune serve", "--model", a.model_dir, "--onnx", onnx, "--port", str(a.port),
                "--threads", str(a.threads)] + rest
    v2_server.main()
    return 0
