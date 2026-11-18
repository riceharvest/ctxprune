"""The 0.2 command line, kept working in 0.3: token pruning of a file / stdin, and the 0.2 HTTP server.

    ctxprune build.log --keep 0.5
    cat build.log | ctxprune --keep 0.33 --protect --stats
    ctxprune serve [--port 8719] [--host 127.0.0.1]     POST /compress {"text": ..., "rate": 0.5}

`ctxprune serve --model-dir DIR` is the 0.3 line-model server instead (cli.py routes on --model-dir).
Code copied from 0.2.1 `ctxprune/cli.py`; behaviour and flags unchanged.
"""

from __future__ import annotations

import argparse
import sys

USAGE = """\
ctxprune FILE [--keep 0.5] [--protect] [--stats]   token-prune a file (or stdin) with the 0.2 model and print it
ctxprune serve [--port 8719] [--host 127.0.0.1]   0.2 HTTP API: POST /compress {"text": ..., "rate": 0.5}"""


def compress_main(argv: list) -> int:
    from .compress import DEFAULT_MODEL, FP32, INT8
    ap = argparse.ArgumentParser(prog="ctxprune", usage=USAGE,
                                 description="Compress text for an LLM by deleting low-value words.")
    ap.add_argument("file", nargs="?", help="input file (default or '-': stdin)")
    ap.add_argument("--keep", "--rate", dest="rate", type=float, default=0.5,
                    help="fraction of the text to keep (default 0.5)")
    ap.add_argument("--protect", "--force-protected", dest="force_protected", action="store_true",
                    help="never drop identifiers, numbers or paths")
    ap.add_argument("--keep-structure", action="store_true", help="never drop JSON brackets, quotes, colons")
    ap.add_argument("--stats", action="store_true", help="print characters before/after to stderr")
    ap.add_argument("--fp32", action="store_true", help="exact fp32 model (563 MB) instead of int8 (143 MB)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--backend", choices=["auto", "onnx", "torch"], default="auto")
    args = ap.parse_args(argv)
    if not 0 < args.rate <= 1:
        ap.error("--keep must be in (0, 1]")

    if args.file and args.file != "-":
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        if sys.stdin.isatty():
            ap.print_help()
            return 2
        text = sys.stdin.read()
    from .compress import Compressor

    c = Compressor(args.model, backend=args.backend, onnx_file=FP32 if args.fp32 else INT8)
    out = c.compress(text, rate=args.rate, force_protected=args.force_protected, keep_structure=args.keep_structure)
    sys.stdout.write(out["text"] + ("" if out["text"].endswith("\n") else "\n"))
    if args.stats:
        print(f"ctxprune: {len(text):,} -> {len(out['text']):,} chars ({len(out['text']) / max(1, len(text)):.0%})",
              file=sys.stderr)
    return 0


def serve_main(argv: list) -> int:
    from .compress import DEFAULT_MODEL, FP32, INT8
    ap = argparse.ArgumentParser(prog="ctxprune serve", description="0.2 token-pruning HTTP API "
                                 "(POST /compress). For the 0.3 line-model server pass --model-dir DIR.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8719)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fp32", action="store_true")
    args = ap.parse_args(argv)
    from .serve import main as serve
    serve(args.host, args.port, args.model, FP32 if args.fp32 else INT8)
    return 0


def is_legacy_compress(cmd: str, rest: list) -> bool:
    """0.2 invocations that 0.3 has no subcommand for: `ctxprune FILE ...`, `ctxprune --keep ...`, `ctxprune -`,
    and bare `ctxprune` with piped stdin."""
    import os
    if not cmd:
        return not sys.stdin.isatty()
    return cmd == "-" or (cmd.startswith("-") and cmd not in ("-h", "--help", "-V", "--version")) \
        or os.path.isfile(cmd)
