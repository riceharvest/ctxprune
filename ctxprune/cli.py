"""ctxprune command line: compress a file (or stdin) and print the result.

    ctxprune order.json --rate 0.5
    cat build.log | ctxprune --rate 0.33 --force-protected
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_MODEL = "darioooooo0o/ctxprune-small"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ctxprune", description="Compress text for an LLM by deleting low-value words.")
    ap.add_argument("file", nargs="?", help="input file (default: stdin)")
    ap.add_argument("--rate", type=float, default=0.5, help="fraction of the text to keep (default 0.5)")
    ap.add_argument("--force-protected", action="store_true", help="never drop identifiers, numbers or paths")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--backend", choices=["onnx", "torch"], default=None,
                    help="default: onnx if onnxruntime is installed, else torch")
    args = ap.parse_args(argv)

    text = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
    backend = args.backend
    if backend is None:
        try:
            import onnxruntime  # noqa: F401
            backend = "onnx"
        except ImportError:
            backend = "torch"
    from .compress import Compressor

    c = Compressor(args.model, backend=backend)
    sys.stdout.write(c.compress(text, rate=args.rate, force_protected=args.force_protected)["text"] + "\n")


if __name__ == "__main__":
    main()
