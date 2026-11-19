"""The 0.2 command line, kept working in 0.3+: token pruning of a file / stdin, and the HTTP server.

    ctxprune build.log --keep 0.5
    cat build.log | ctxprune --keep 0.33 --protect --stats
    cat doc.txt | ctxprune --keep 0.33 --query "why did worker fail?"
    ctxprune serve [--port 8719] [--host 127.0.0.1]     POST /compress {"text": ..., "rate": 0.5}

`ctxprune serve --model-dir DIR` is the 0.3 line-model server instead (cli.py routes on --model-dir).
"""

from __future__ import annotations

import argparse
import sys

USAGE = """\
ctxprune FILE [--keep 0.5] [--protect | --protect-mode auto|ids|all|none] [--query "..."] [--stats]
ctxprune serve [--port 8719] [--host 127.0.0.1]   HTTP API: POST /compress {"text": ..., "rate": 0.5}"""


def compress_main(argv: list) -> int:
    from .compress import DEFAULT_MODEL, FP32, INT8
    ap = argparse.ArgumentParser(prog="ctxprune", usage=USAGE,
                                 description="Compress text for an LLM by deleting low-value words.")
    ap.add_argument("file", nargs="?", help="input file (default or '-': stdin)")
    ap.add_argument("--keep", "--rate", dest="rate", type=float, default=0.5,
                    help="fraction of the text to keep (default 0.5)")
    ap.add_argument("--protect", "--force-protected", dest="force_protected", action="store_true",
                    help="strict protection: never drop identifiers, numbers or paths")
    ap.add_argument("--protect-auto", action="store_true",
                    help="v0.4 engine with smart auto protection: full IDs on technical logs, strict IDs on prose")
    ap.add_argument("--protect-mode", choices=["auto", "ids", "all", "none"], default=None,
                    help="protection mode when --protect is not set (default: none, as in 0.3; "
                         "auto/ids select the v0.4 engine)")
    ap.add_argument("--query", default=None, help="optional query/question to prioritize relevant spans")
    ap.add_argument("--mode", choices=["prune", "auto", "json"], default="prune",
                    help="prune (neural), auto (lossless-first / JSON values), or json (JSON-values-only)")
    ap.add_argument("--unit", choices=["tokens_est", "o200k", "cl100k", "chars", "tokens"], default=None,
                    help="budget accounting unit (default: chars as in 0.3; tokens_est/o200k/cl100k use the v0.4 engine)")
    ap.add_argument("--clean-web", action="store_true", help="strip web/HTML hydration blobs, UTM params, table pad")
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

    if args.force_protected:
        prot: bool | str | None = True
    elif args.protect_auto:
        prot = "auto"
    elif args.protect_mode is None:
        prot = None  # unset: 0.3 behaviour (False), "auto" once --query/--mode/... is given
    else:
        prot = {"auto": "auto", "ids": "ids", "all": True, "none": False}[args.protect_mode]

    c = Compressor(args.model, backend=args.backend, onnx_file=FP32 if args.fp32 else INT8)
    out = c.compress(
        text,
        rate=args.rate,
        force_protected=prot,
        keep_structure=args.keep_structure,
        unit=args.unit,
        query=args.query,
        mode=args.mode,
        clean_web=args.clean_web,
    )
    sys.stdout.write(out["text"] + ("" if out["text"].endswith("\n") else "\n"))
    if args.stats:
        print(f"ctxprune: {len(text):,} -> {len(out['text']):,} chars ({len(out['text']) / max(1, len(text)):.0%})",
              file=sys.stderr)
    return 0


def serve_main(argv: list) -> int:
    from .compress import DEFAULT_MODEL, FP32, INT8
    ap = argparse.ArgumentParser(prog="ctxprune serve", description="HTTP token-pruning API "
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
