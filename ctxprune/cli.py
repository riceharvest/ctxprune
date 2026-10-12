"""ctxprune command line.

    ctxprune build.log                       # keep ~50% of the text, print it
    cat build.log | ctxprune --keep 0.33 --protect --stats
    ctxprune install claude-code             # compress big Bash/MCP outputs inside Claude Code
    ctxprune serve                           # local HTTP API on :8787 (skips the model load per call)
"""

from __future__ import annotations

import argparse
import sys

from .compress import DEFAULT_MODEL, FP32, INT8

USAGE = """\
ctxprune FILE [--keep 0.5] [--protect] [--stats]   compress a file (or stdin) and print it
ctxprune install claude-code [--scope user|project|local]
ctxprune uninstall claude-code [--scope ...]
ctxprune serve [--port 8787] [--host 127.0.0.1]
ctxprune hook claude-code                          (called by Claude Code, reads JSON on stdin)"""


def _compress_main(argv: list[str]) -> None:
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
            sys.exit(2)
        text = sys.stdin.read()
    from .compress import Compressor

    c = Compressor(args.model, backend=args.backend, onnx_file=FP32 if args.fp32 else INT8)
    out = c.compress(text, rate=args.rate, force_protected=args.force_protected, keep_structure=args.keep_structure)
    sys.stdout.write(out["text"] + ("" if out["text"].endswith("\n") else "\n"))
    if args.stats:
        print(f"ctxprune: {len(text):,} -> {len(out['text']):,} chars ({len(out['text']) / max(1, len(text)):.0%})",
              file=sys.stderr)


def _agent_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("agent", choices=["claude-code"])


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd == "hook":
        ap = argparse.ArgumentParser(prog="ctxprune hook")
        _agent_arg(ap)
        ap.parse_args(argv[1:])
        from .hook import main_claude_code

        return main_claude_code()
    if cmd in ("install", "uninstall"):
        ap = argparse.ArgumentParser(prog=f"ctxprune {cmd}")
        _agent_arg(ap)
        ap.add_argument("--scope", choices=["user", "project", "local"], default="user",
                        help="user: ~/.claude/settings.json, project: .claude/settings.json, "
                             "local: .claude/settings.local.json")
        args = ap.parse_args(argv[1:])
        from . import hook

        if cmd == "uninstall":
            print(f"removed the ctxprune hook from {hook.uninstall(args.scope)}")
            return
        print("downloading the model once (143 MB) so the first hook call is fast...", file=sys.stderr)
        from .compress import Compressor

        Compressor()
        p = hook.install(args.scope)
        print(f"added a PostToolUse hook to {p}\n"
              f"Claude Code now compresses Bash and MCP outputs over 6,000 characters to ~50% (IDs kept).\n"
              f"Originals go to ~/.cache/ctxprune/outputs/. Tune with CTXPRUNE_RATE / CTXPRUNE_MIN_CHARS;\n"
              f"run `ctxprune serve` in the background to make each compression ~2 s faster.\n"
              f"Undo: ctxprune uninstall claude-code --scope {args.scope}")
        return
    if cmd == "serve":
        ap = argparse.ArgumentParser(prog="ctxprune serve")
        ap.add_argument("--host", default="127.0.0.1")
        ap.add_argument("--port", type=int, default=8787)
        ap.add_argument("--model", default=DEFAULT_MODEL)
        ap.add_argument("--fp32", action="store_true")
        args = ap.parse_args(argv[1:])
        from .serve import main as serve_main

        return serve_main(args.host, args.port, args.model, FP32 if args.fp32 else INT8)
    _compress_main(argv)


if __name__ == "__main__":
    main()
