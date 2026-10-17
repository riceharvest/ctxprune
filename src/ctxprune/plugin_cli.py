#!/usr/bin/env python3
"""The entry point every command ctxprune writes into an agent config runs BY PATH (never `python -m ctxprune`:
`-m` puts the caller's working directory first on sys.path, and Claude Code runs hooks in the user's project, so a
project with its own `ctxprune/`, `dedup.py` or `lossless.py` would run instead of ours).

  python .../ctxprune/plugin_cli.py --command=... --tool=... --harness ...   the OpenCode / Pi / dsh plugins
        (their CTXPRUNE_CLI): stdin text -> stdout text, like scripts/ctxprune_compress.py, which it wraps after
        glue.boot() (defaults, kill switch, stats). Any failure before that echoes the input (fail open, exit 0).
  python .../ctxprune/plugin_cli.py hook claude-code | proxy ... | refresh    the package CLI (Claude Code hook,
        the Codex proxy service, `upgrade`'s refresh step)
"""

import os
import sys


def _glue():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glue.py")
    spec = importlib.util.spec_from_file_location("_ctxprune_glue", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ctxprune_glue"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):   # a CLI subcommand; plugins always pass --flags first
        here = os.path.dirname(os.path.abspath(__file__))
        if sys.path and os.path.abspath(sys.path[0] or ".") == here:
            sys.path[0] = os.path.dirname(here)   # site-packages: `ctxprune` is the package, our modules stay private
        from ctxprune.cli import main as cli_main
        return cli_main(argv)
    try:
        _glue().boot()
        import ctxprune_compress
    except BaseException:  # noqa: BLE001 - fail open: echo the input (--context mode: no rewrites)
        try:
            if any(x == "--context" or x.startswith("--context=") for x in argv):
                sys.stdout.write('{"out": {}}')
                return 0
            sys.stdout.buffer.write(sys.stdin.buffer.read())
        except Exception:
            pass
        return 0
    return ctxprune_compress.main()


if __name__ == "__main__":
    sys.exit(main())
