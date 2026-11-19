"""ctxprune command line.

    ctxprune install claude-code|hermes|pi|opencode|dsh|codex|--all [--preset safe|balanced|aggressive]
    ctxprune uninstall <harness>|--all
    ctxprune upgrade [--spec SPEC] [--dry-run]       upgrade the package, then refresh every installed integration
    ctxprune doctor                                   versions + a canary call through every installed integration
    ctxprune stats [--days N] [--price USD_PER_MTOK]  tokens saved (counts-only local log)
    ctxprune audit [--harness H] [--days 30]          per agent: static overhead, MCP use, cache facts (Claude Code:
                                                      the full bill map). Local, advice only, numbers and names
    ctxprune compress [FILE] [--command CMD]          run the pipeline on a file / stdin, print the result
    ctxprune proxy --upstream URL [--port 8787]       compressing HTTP proxy (Codex; any OpenAI/Anthropic client)
    ctxprune serve --model-dir DIR                    line-model server for lossy presets (ctxprune[model])
    ctxprune bundle hermes|claude-code DEST           self-contained plugin dirs (catalog / marketplace builds)
    ctxprune hook claude-code                         (called by Claude Code: hook JSON on stdin)
    ctxprune mcp                                      stdio MCP server: read_compact (lossless), read_pruned (lossy)
    ctxprune mcp-gateway --config FILE                stdio meta-tool gateway for configured MCP servers
  0.2 token pruning (lossy, still supported; model darioooooo0o/ctxprune-small):
    ctxprune FILE [--keep 0.5] [--protect] [--stats]  token-prune a file (or stdin) and print it
    ctxprune serve [--port 8719]                      0.2 HTTP API: POST /compress {"text": ..., "rate": 0.5}
Kill switch: CTXPRUNE_OFF=1 (environment, or inside a single command).
"""

from __future__ import annotations

import sys

from . import __version__


def _hook(argv: list) -> int:
    """Claude Code PostToolUse hook: the research hook scripts, unchanged, with the defaults table applied.
    No argparse here (import cost on every tool call)."""
    if argv[:1] == ["claude-code-weekly"]:
        return _weekly_hook()
    if argv[:1] != ["claude-code"]:
        return 0
    try:
        from . import glue
        glue.boot()
        v = glue.resolved("claude-code")
        dedup = glue.dedup_flag(v)   # [] = by model, decided per output by the hook (claude-opus-*: on)
        if v.get("model"):   # a lossy preset: v2_hook resolves keep/min_chars/persisted from the same table
            import v2_hook as h
            args = ["--preset", v.get("requested") or v["preset"]] + dedup
        else:
            import lossless_hook as h
            args = dedup
        sys.argv = [h.__file__] + args   # the hook parses sys.argv strictly (and fails silently on a stray arg)
        h.main()
    except BaseException:  # noqa: BLE001 - fail open: no output = Claude Code keeps the original
        pass
    return 0


def _weekly_hook() -> int:
    """Opt-in SessionStart hook (`install claude-code --weekly-summary`): at most once a week, one line for the USER
    (`systemMessage`, which Claude Code shows in the terminal and does not send to the model). Prints nothing
    otherwise, so no session gets extra tokens. Fails silent."""
    try:
        import json
        import os
        import time
        try:
            sys.stdin.read()
        except Exception:
            pass
        from . import glue, stats
        if glue.off():
            return 0
        mark = os.path.join(glue.state_dir(), "weekly-summary.ts")
        try:
            last = os.path.getmtime(mark)
        except OSError:
            last = 0.0
        if time.time() - last < 7 * 86400:
            return 0
        line = stats.weekly_line()
        if not line:
            return 0
        os.makedirs(glue.state_dir(), mode=0o700, exist_ok=True)
        with open(mark, "w") as f:
            f.write(str(int(time.time())))
        sys.stdout.write(json.dumps({"systemMessage": line}))
    except BaseException:  # noqa: BLE001
        pass
    return 0


def _audit(argv: list) -> int:
    """`ctxprune audit`: per harness, local only and advice only (numbers and names, never transcript content; no
    network, sockets are disabled for the run; never changes a config file). Claude Code: the runtime's ctx_audit.py
    bill map; Codex / Pi / OpenCode / Hermes / dsh: ctxprune.harness_audit."""
    import argparse
    import json
    import os
    from . import glue, harness_audit as ha
    ap = argparse.ArgumentParser(prog="ctxprune audit", add_help=False)
    ap.add_argument("--harness", action="append", default=[], help="only these (repeatable; default: every one found)")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--json", default=None)
    ap.add_argument("-h", "--help", action="store_true")
    a, rest = ap.parse_known_args(argv)
    if a.help:
        print("ctxprune audit [--harness NAME ...] [--days 30] [--json OUT] [Claude Code options: --root DIR "
              "--exclude GLOB ... --records OUT.jsonl --share [FILE] --otel LOGFILE --self-test]\nLocal and advice-only: numbers and names, never "
              "content; nothing is changed.")
        return 0
    glue.boot()
    import ctx_audit
    if "--self-test" in rest:
        sys.argv = ["ctxprune audit", "--self-test"]
        ctx_audit.main()
        return 0
    if any(x == "--otel" or x.startswith("--otel=") for x in rest):   # fleet mode: a log file, no transcripts, no harness scan
        sys.argv = ["ctxprune audit"] + rest
        ctx_audit.main()
        return 0
    want = a.harness or ["claude-code"] + list(ha.HARNESSES)
    cc_root = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"),
                           "projects")
    any_found = False
    if "claude-code" in want and (os.path.isdir(cc_root) or any(x == "--root" or x.startswith("--root=")
                                                                 for x in rest)):
        any_found = True
        print("claude-code:")
        sys.argv = ["ctxprune audit"] + (["--root", cc_root] if not any(x == "--root" or x.startswith("--root=")
                                                                       for x in rest) else []) + \
            ["--days", f"{a.days:g}"] + rest + (["--json", a.json + ".claude-code.json"] if a.json else [])
        ctx_audit.main()   # disables network for the rest of the run
        print("  Dollar figures are Opus 5.5 API list prices. On a subscription (Pro / Max) nothing is billed per "
              "token: read them as shares of your usage limits.")
    ha.block_network()
    res = [ha.audit_harness(h, a.days) for h in want if h in ha.HARNESSES]
    txt = ha.report(res)
    if txt:
        any_found = True
        print(("\n" if "claude-code" in want else "") + txt)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump([r for r in res if r["found"]], f, indent=1)
    if not any_found:
        print("no supported agent's local records found (Claude Code, Codex, Pi, OpenCode, Hermes, dsh).")
    else:
        print("\nAdvice only; nothing was changed. [verified] = measured from your local records or checked by "
              "ctxprune; [unverified] = from the harness's or provider's documentation.")
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
                        help="safe (default): lossless, no model (dedup by model: on for claude-opus-*). balanced / aggressive: + the line model "
                             "(needs ctxprune[model] + ctxprune serve; harnesses that opt out stay safe)")
        ap.add_argument("--allow-ephemeral", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=None, help="codex: proxy port (default 8787)")
    ap.add_argument("--upstream", default=None, help="codex: upstream API (default https://api.openai.com)")
    ap.add_argument("--start", action="store_true", help="codex: also enable/start (or stop) the proxy user service")
    ap.add_argument("--scope", choices=["user", "project", "local"], default=None,
                    help="claude-code: user (default, ~/.claude/settings.json), project (.claude/settings.json) or "
                         "local (.claude/settings.local.json) in the current directory (as in 0.2)")
    if verb == "install":
        ap.add_argument("--weekly-summary", dest="weekly_summary", action="store_true", default=None,
                        help="claude-code: also a SessionStart hook that shows you a one-line savings summary at most "
                             "once a week (shown in the terminal, never sent to the model; off by default)")
        ap.add_argument("--no-weekly-summary", dest="weekly_summary", action="store_false", help=argparse.SUPPRESS)
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
    if a.scope not in (None, "user") and targets != ["claude-code"]:
        ap.error("--scope project|local applies to claude-code only")
    opts = {"port": a.port, "upstream": a.upstream, "start": a.start, "scope": a.scope,
            "weekly": getattr(a, "weekly_summary", None)}   # None: keep what the last install chose
    rc = 0
    for h in targets:
        try:
            if a.scope in ("project", "local"):   # a per-project hook, as in 0.2: not in the user-level manifest
                res = (ins.INSTALL if verb == "install" else ins.UNINSTALL)[h](opts)
            else:
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
        print(f"preset: {p} (" + ("lossless, no model, " + glue.dedup_desc(glue.resolved()) if not lossy else
                                  "line model on for " + ", ".join(lossy) + "; needs `ctxprune serve`") +
              "). Check it works: ctxprune doctor")
        print(f"See what it saves ({', '.join(targets)}): ctxprune stats; static overhead per agent: ctxprune audit")
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
    ap.add_argument("--redact-secrets", action="store_true", help="mask recognized credentials before compression (reversible only in this process)")
    ap.add_argument("--stats", action="store_true", help="chars before/after on stderr")
    a = ap.parse_args(argv)
    if a.file and a.file != "-":
        with open(a.file, "rb") as f:
            raw = f.read()
    else:
        raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", "surrogateescape")
    if a.redact_secrets:
        from .redact import Redactor
        text = Redactor().redact(text)
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
            *(["--dedup", "off"] if glue.off() else glue.dedup_flag(glue.resolved(a.harness))),   # [] = by model
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
    if cmd == "audit":
        return _audit(rest)
    if cmd == "compress":
        return _compress(rest)
    if cmd == "notebook":
        from .notebook import main as notebook_main
        return notebook_main(rest)
    if cmd == "proxy":
        return _proxy(rest)
    if cmd == "mcp":
        from .mcp_server import main as mcp_main
        return mcp_main(rest)
    if cmd == "mcp-gateway":
        from .mcp_gateway import main as gateway_main
        return gateway_main(rest)
    if cmd == "bundle":
        from .bundle import main as bundle_main
        return bundle_main(rest)
    if cmd == "serve":
        if not any(x == "--model-dir" or x.startswith("--model-dir=") for x in rest) and \
                not any(x in ("-h", "--help") for x in rest):
            from .legacy_cli import serve_main as legacy_serve   # 0.2 HTTP API (POST /compress on :8719)
            return legacy_serve(rest)
        from .model import serve_main
        return serve_main(rest)
    from .legacy_cli import is_legacy_compress
    if is_legacy_compress(cmd, rest):   # 0.2: `ctxprune FILE --keep 0.5`, `cmd | ctxprune --keep 0.33 --protect`
        from .legacy_cli import compress_main
        return compress_main(argv)
    print(__doc__.strip(), file=sys.stderr if cmd not in ("-h", "--help", "help") else sys.stdout)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main())
