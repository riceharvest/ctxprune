"""ctxprune command line.

    ctxprune install claude-code|hermes|pi|opencode|dsh|codex|--all [--preset safe|model]
    ctxprune uninstall <harness>|--all
    ctxprune upgrade [--spec SPEC] [--dry-run]       upgrade the package, then refresh every installed integration
    ctxprune doctor                                   versions + a canary call through every installed integration
    ctxprune stats [--days N] [--price USD_PER_MTOK]  tokens saved (counts-only local log)
    ctxprune compress [FILE] [--command CMD]          run the pipeline on a file / stdin, print the result
    ctxprune proxy --upstream URL [--port 8787]       compressing HTTP proxy (Codex; any OpenAI/Anthropic client)
    ctxprune serve --model-dir DIR                    line-model server for lossy presets (ctxprune[model])
    ctxprune bundle hermes|claude-code DEST           self-contained plugin dirs (catalog / marketplace builds)
    ctxprune hook claude-code                         (called by Claude Code: hook JSON on stdin)
Kill switch: CTXPRUNE_OFF=1 (environment, or inside a single command).
"""

from __future__ import annotations

import sys

from . import __version__


def _hook(argv: list) -> int:
    """Claude Code PostToolUse hook: the research hook scripts, unchanged, with the defaults table applied.
    No argparse here (import cost on every tool call)."""
    if argv[:1] != ["claude-code"]:
        return 0
    try:
        from . import glue
        glue.boot()
        v = glue.resolved("claude-code")
        dedup = "on" if v.get("dedup", True) else "off"
        if v.get("model"):   # a lossy preset: v2_hook resolves keep/min_chars/persisted from the same table
            import v2_hook as h
            args = ["--preset", v.get("requested") or v["preset"], "--dedup", dedup]
        else:
            import lossless_hook as h
            args = ["--dedup", dedup]
        sys.argv = [h.__file__] + args   # the hook parses sys.argv strictly (and fails silently on a stray arg)
        h.main()
    except BaseException:  # noqa: BLE001 - fail open: no output = Claude Code keeps the original
        pass
    return 0


def _print_result(verb: str, harness: str, res: dict) -> None:
    changed = res.get("changed", []) + res.get("removed", [])
    plugin = res.get("plugin")
    if plugin and len(changed) > 3:   # a bundle dir: name the dir once, not every vendored file
        inside = [c for c in changed if c.startswith(plugin)]
        changed = [c for c in changed if not c.startswith(plugin)] + [f"{plugin} ({len(inside)} files)"]
    head = f"{harness}: " + ("updated " + ", ".join(changed) if changed else "already up to date"
                             if verb == "install" else "nothing to remove")
    print(head)
    for b in res.get("backups", []):
        print(f"  backup of the original: {b}")
    for n in res.get("notes", []):
        print(f"  note: {n}")
    for r in res.get("ran", []):
        print(f"  ran: {r}")
    for n in res.get("next", []):
        print(f"  next: {n}")


def _install(argv: list, verb: str) -> int:
    import argparse
    from . import glue, installers as ins
    ap = argparse.ArgumentParser(prog=f"ctxprune {verb}")
    ap.add_argument("harness", nargs="*", metavar="HARNESS", help=" | ".join(ins.HARNESSES))
    ap.add_argument("--all", action="store_true", help="every harness found on this machine (install) / "
                                                       "every installed one (uninstall)")
    if verb == "install":
        ap.add_argument("--preset", choices=glue.presets(), default=None,
                        help="safe (default): lossless + dedup, no model. balanced / aggressive: + the line model "
                             "(needs ctxprune[model] + ctxprune serve; harnesses that opt out stay safe)")
        ap.add_argument("--allow-ephemeral", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=None, help="codex: proxy port (default 8787)")
    ap.add_argument("--upstream", default=None, help="codex: upstream API (default https://api.openai.com)")
    ap.add_argument("--start", action="store_true", help="codex: also enable/start (or stop) the proxy user service")
    a = ap.parse_args(argv)
    bad = [h for h in a.harness if h not in ins.HARNESSES]
    if bad:
        ap.error(f"unknown harness {', '.join(bad)} (choose from {', '.join(ins.HARNESSES)})")
    if verb == "install":
        eph = ins.ephemeral_env()
        if eph and not a.allow_ephemeral:
            print(f"ctxprune runs from a temporary environment ({eph}); the paths it would write into your agent "
                  "configs disappear with it.\nInstall it for good first, then run install again:\n"
                  "  uv tool install ctxprune && ctxprune install --all\n"
                  "  (or: pipx install ctxprune / pip install --user ctxprune)", file=sys.stderr)
            return 2
    if a.all:
        targets = ins.detected() if verb == "install" else \
            sorted(set(ins.load_manifest()["harnesses"]) | set(a.harness or []))
        if verb == "uninstall" and not targets:
            targets = list(ins.HARNESSES)
    else:
        targets = list(dict.fromkeys(a.harness))
    if not targets:
        ap.error("name a harness or pass --all" if not a.all else "no supported harness found on this machine")
    if verb == "install" and a.preset:
        ins.save_preset(a.preset)
    opts = {"port": a.port, "upstream": a.upstream, "start": a.start}
    rc = 0
    for h in targets:
        try:
            res = (ins.install if verb == "install" else ins.uninstall)(h, opts)
            _print_result(verb, h, res)
        except ins.InstallError as e:
            print(f"{h}: NOT {verb}ed: {e}", file=sys.stderr)
            rc = 1
        except OSError as e:
            print(f"{h}: NOT {verb}ed: {e}", file=sys.stderr)
            rc = 1
    if verb == "install" and rc == 0:
        p = glue.preset_name()
        lossy = [h for h in targets if glue.resolved(h).get("model")]
        print(f"preset: {p} (" + ("lossless + dedup, no model" if not lossy else
                                  "line model on for " + ", ".join(lossy) + "; needs `ctxprune serve`") +
              "). Check it works: ctxprune doctor")
    return rc


def _refresh(argv: list) -> int:
    """Re-render every installed integration with this version (after an upgrade)."""
    from . import installers as ins
    m = ins.load_manifest()
    rc = 0
    for h, info in sorted(m["harnesses"].items()):
        if h not in ins.INSTALL:
            continue
        try:
            _print_result("install", h, ins.install(h, dict((info or {}).get("opts") or {})))
        except (ins.InstallError, OSError) as e:
            print(f"{h}: NOT refreshed: {e}", file=sys.stderr)
            rc = 1
    if not m["harnesses"]:
        print("nothing installed yet: ctxprune install --all")
    return rc


def _upgrade(argv: list) -> int:
    import argparse
    import os
    import subprocess
    ap = argparse.ArgumentParser(prog="ctxprune upgrade")
    ap.add_argument("--spec", default="ctxprune", help="what to install (default: ctxprune from PyPI; a wheel path "
                                                       "or `ctxprune==X` works too)")
    ap.add_argument("--dry-run", action="store_true", help="print the commands only")
    a = ap.parse_args(argv)
    prefix = os.path.realpath(sys.prefix).replace("\\", "/")
    if "/uv/tools/" in prefix:
        cmd = ["uv", "tool", "upgrade", "ctxprune"] if a.spec == "ctxprune" else \
            ["uv", "tool", "install", "--force", a.spec]
    elif "/pipx/venvs/" in prefix:
        cmd = ["pipx", "upgrade", "ctxprune"] if a.spec == "ctxprune" else ["pipx", "install", "--force", a.spec]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", a.spec]
    from .installers import plugin_cli
    refresh = [sys.executable, plugin_cli(), "refresh"]   # by path: never a `ctxprune` package in the cwd
    if a.dry_run:
        print(" ".join(cmd))
        print(" ".join(refresh))
        return 0
    print("running: " + " ".join(cmd), flush=True)
    try:
        r = subprocess.run(cmd)
    except OSError as e:
        print(f"could not run it: {e}", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("upgrade failed; the installed integrations are unchanged", file=sys.stderr)
        return r.returncode
    return subprocess.run(refresh).returncode   # the NEW code re-renders the plugin copies / hook commands


def _compress(argv: list) -> int:
    import argparse
    from . import glue
    ap = argparse.ArgumentParser(prog="ctxprune compress")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--command", default="", help="the command that produced the text (enables grep grouping etc.)")
    ap.add_argument("--stats", action="store_true", help="chars before/after on stderr")
    a = ap.parse_args(argv)
    if a.file and a.file != "-":
        with open(a.file, "rb") as f:
            raw = f.read()
    else:
        raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", "surrogateescape")
    glue.boot()
    import ctxprune_compress
    out, rec = ctxprune_compress.run(text, command=a.command, harness="cli")
    sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    if a.stats:
        print(f"ctxprune: {len(text):,} -> {len(out):,} chars ({rec.get('stage')})", file=sys.stderr)
    return 0


def _proxy(argv: list) -> int:
    import argparse
    import os
    from . import glue
    s = glue.settings()
    ap = argparse.ArgumentParser(prog="ctxprune proxy", description="compressing proxy; any extra flags go to "
                                 "scripts/ctxprune_proxy.py")
    ap.add_argument("--upstream", default=s["proxy_upstream"])
    ap.add_argument("--port", type=int, default=s["proxy_port"])
    ap.add_argument("--harness", default="", help="the harness behind the proxy (codex, hermes, ...)")
    a, rest = ap.parse_known_args(argv)
    s = glue.boot(own_process=True)
    import ctxprune_proxy
    args = ["--upstream", a.upstream, "--port", str(a.port), "--preset", s["preset"], "--v2-port", str(s["port"]),
            "--dedup", "off" if glue.off() or os.environ.get("CTXPRUNE_DEDUP") == "0" else "on",
            "--cache-file", os.path.join(glue.state_dir(), f"proxy-cache-{s['preset']}.jsonl")]
    if a.harness:   # per-harness opt-out of lossy presets (codex: balanced runs as safe)
        args += ["--harness", a.harness]
    os.environ.setdefault("CTXPRUNE_HARNESS", a.harness or "proxy")
    sys.argv = ["ctxprune proxy"] + args + rest
    os.makedirs(glue.state_dir(), mode=0o700, exist_ok=True)
    ctxprune_proxy.main()
    return 0


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "hook":
        return _hook(rest)
    if cmd in ("-V", "--version", "version"):
        print(f"ctxprune {__version__}")
        return 0
    if cmd in ("install", "uninstall"):
        return _install(rest, cmd)
    if cmd == "upgrade":
        return _upgrade(rest)
    if cmd == "refresh":
        return _refresh(rest)
    if cmd == "doctor":
        from .doctor import main as doctor_main
        return doctor_main(rest)
    if cmd == "stats":
        from .stats import main as stats_main
        return stats_main(rest)
    if cmd == "compress":
        return _compress(rest)
    if cmd == "proxy":
        return _proxy(rest)
    if cmd == "bundle":
        from .bundle import main as bundle_main
        return bundle_main(rest)
    if cmd == "serve":
        from .model import serve_main
        return serve_main(rest)
    print(__doc__.strip(), file=sys.stderr if cmd not in ("-h", "--help", "help") else sys.stdout)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main())
