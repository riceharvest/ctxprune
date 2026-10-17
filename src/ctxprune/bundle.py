"""Self-contained plugin bundles: the stdlib runtime scripts copied INTO the plugin dir, so the plugin works with no
pip install and no ~/ctxprune-v2 checkout (what `hermes plugins install` from the catalog and `/plugin install` from a
Claude Code marketplace need).

    ctxprune bundle hermes DEST [--with-model]     DEST = the plugin dir (catalog subdir / ~/.hermes/plugins/ctxprune)
    ctxprune bundle claude-code DEST                DEST = a marketplace repo root: writes .claude-plugin/marketplace.json
                                                    + claude-code-plugin-safe/ (the `ctxprune-safe` plugin)

The bundled scripts are byte copies of scripts/ (glue.SAFE_MODULES + glue.py), so a bundle is regenerated, never
edited. `safe` bundles ship no model code at all (no onnx, no download, no localhost server): only lossless rules,
plus cross-turn dedup in the Claude Code hook. No __pycache__ / .pyc is written (Hermes catalog rule 17).
"""

from __future__ import annotations

import json
import os
import shutil
import sys

from . import __version__, glue

SENTINEL = "ctxprune-managed"
HERMES_HOOKS = ("transform_terminal_output", "transform_tool_result")
HERMES_MIDDLEWARE = ("llm_request",)
REPO = "https://github.com/riceharvest/ctxprune"
AUTHOR = {"name": "riceharvest", "url": "https://github.com/riceharvest"}

HERMES_SHIM = '''"""ctxprune {version} for Hermes ({build} build, {sentinel}). Self-contained: the stdlib runtime is in _rt/ next
to this file, so it needs no pip install. _impl.py is scripts/integrations/hermes/ctxprune/__init__.py unchanged; this
loader points it at _rt/, applies the CTXPRUNE_OFF kill switch{safe_note}, and fails open (no change) on any error.
Regenerate with `ctxprune bundle hermes DIR`; don't edit.
"""

import importlib.util as _u
import os as _os

_DIR = _os.path.dirname(_os.path.abspath(__file__))
_RT = _os.path.join(_DIR, "_rt")
_SAFE_ONLY = {safe_only}
_STATS = {stats}   # counts-only stats log (~/.local/state/ctxprune/stats.jsonl); off in catalog builds


def _load(name, path):
    spec = _u.spec_from_file_location(name, path)
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_os.environ["CTXPRUNE_SCRIPTS"] = _RT   # _impl imports the pipeline from here
try:
    _g = _load("_ctxprune_glue", _os.path.join(_RT, "glue.py"))
    _g.boot(own_process=False, sys_path=False)   # no umask change; _rt is only ever appended to sys.path
    _g.STATS_ON = _STATS
except Exception:
    _g = None
_impl = _load("_ctxprune_hermes_impl", _os.path.join(_DIR, "_impl.py"))
_orig_cc, _orig_term, _orig_tool = _impl._cc, _impl.on_terminal_output, _impl.on_tool_result


class _SafeRun:
    """The compressor module with the line model forced off (safe build: lossless rules only)."""

    def __init__(self, mod):
        self._mod = mod

    def __getattr__(self, name):
        return getattr(self._mod, name)

    def run(self, *a, **kw):
        kw["no_model"] = True
        return self._mod.run(*a, **kw)


def _cc():
    mod = _orig_cc()
    if _g is not None:
        try:
            _g.patch_core()   # kill switch + counts-only stats around ctxprune_core.process
        except Exception:
            pass
    return _SafeRun(mod) if _SAFE_ONLY else mod


def _off(command):
    if _g is not None:
        return _g.off(command if isinstance(command, str) else "")
    return _os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes", "on")


def on_terminal_output(command="", output="", returncode=None, task_id="", env_type="", **kw):
    if _off(command):
        return None
    return _orig_term(command=command, output=output, returncode=returncode, task_id=task_id, env_type=env_type,
                      **kw)


def on_tool_result(tool_name="", args=None, result="", task_id="", **kw):
    if _off(str(args.get("command") or "") if isinstance(args, dict) else ""):
        return None
    return _orig_tool(tool_name=tool_name, args=args, result=result, task_id=task_id, **kw)


_impl._cc = _cc   # our own module: its _compress/_view_safe look _cc up at call time


_orig_llm = getattr(_impl, "on_llm_request", None)


def on_llm_request(request=None, **kw):
    if _off("") or _orig_llm is None:
        return None
    return _orig_llm(request, **kw)


if _orig_llm is not None:
    _impl.on_llm_request = on_llm_request   # register_dedup registers the module global: the kill-switch wrapper


def register(ctx):
    ctx.register_hook("transform_terminal_output", on_terminal_output)
    ctx.register_hook("transform_tool_result", on_tool_result)
    if hasattr(_impl, "register_dedup"):
        _impl.register_dedup(ctx)   # llm_request middleware: per-request cross-turn dedup (lossless)
'''

HERMES_DESC_SAFE = ("Lossless rewrite of tool results before they reach the model: grep-style hits grouped under "
                    "their path, repeated lines replaced by a reference to the first copy (each kept only if it "
                    "decodes back to the exact original), read_file/terminal JSON wrappers unescaped with every other "
                    "key kept. patch/edit/memory/todo results are never touched. Before each LLM call (llm_request middleware) a "
                    "tool result repeating an earlier one in the same request becomes a reference to it. Stdlib "
                    "Python, no model, no network.")

CC_HOOK_SH = '''#!/bin/sh
# ctxprune-safe: run the bundled stdlib hook (scripts/rt/) with the first working Python 3.9+. No pip, no network.
# No Python found -> exit 0 with no output: Claude Code keeps the original tool output.
HERE=$(dirname "$0")
case "${OS:-}" in Windows_NT) CANDIDATES="py python python3" ;; *) CANDIDATES="python3 python" ;; esac
for py in $CANDIDATES; do
  if command -v "$py" >/dev/null 2>&1; then
    if [ "$py" = py ]; then exec py -3 "$HERE/hook.py"; fi
    exec "$py" "$HERE/hook.py"
  fi
done
cat >/dev/null 2>&1
exit 0
'''

CC_HOOK_PY = '''"""ctxprune-safe Claude Code hook ({sentinel}, ctxprune {version}): scripts/lossless_hook.py from the bundled
stdlib runtime (rt/), lossless rules + cross-turn dedup, never the model. Fails open: any error -> no output."""

import os
import sys


def main():
    if sys.version_info < (3, 9):
        return
    try:
        import importlib.util
        rt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rt")
        spec = importlib.util.spec_from_file_location("_ctxprune_glue", os.path.join(rt, "glue.py"))
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        os.environ["CTXPRUNE_PRESET"] = "safe"
        g.boot()   # private umask, rt/ on sys.path (appended), CTXPRUNE_OFF + counts-only stats
        import lossless_hook
        dedup = "off" if os.environ.get("CTXPRUNE_DEDUP") == "0" else "on"
        sys.argv = [lossless_hook.__file__, "--dedup", dedup]
        lossless_hook.main()
    except BaseException:
        pass


if __name__ == "__main__":
    main()
'''


def _copy_runtime(dest: str, modules) -> list:
    rt = glue.runtime_dir()
    os.makedirs(dest, exist_ok=True)
    out = []
    for m in modules:
        src = os.path.join(rt, m + ".py")
        shutil.copyfile(src, os.path.join(dest, m + ".py"))
        out.append(m + ".py")
    shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "glue.py"), os.path.join(dest, "glue.py"))
    return out + ["glue.py"]


def _write(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _yaml_str(s: str) -> str:
    return json.dumps(s)   # a JSON string is a valid YAML scalar


def build_hermes(dest: str, safe_only: bool = True, stats: bool = False) -> list:
    """Write the self-contained Hermes plugin into dest (replacing what a previous build left there)."""
    src_impl = os.path.join(glue.runtime_dir(), "integrations", "hermes", "ctxprune", "__init__.py")
    if os.path.isdir(os.path.join(dest, "_rt")):
        shutil.rmtree(os.path.join(dest, "_rt"))
    os.makedirs(dest, exist_ok=True)
    shutil.copyfile(src_impl, os.path.join(dest, "_impl.py"))
    mods = glue.SAFE_MODULES if safe_only else glue.SAFE_MODULES + glue.MODEL_MODULES
    files = ["_rt/" + f for f in _copy_runtime(os.path.join(dest, "_rt"), mods)]
    build = "safe-only" if safe_only else "full"
    _write(os.path.join(dest, "__init__.py"), HERMES_SHIM.format(
        version=__version__, build=build, sentinel=SENTINEL, safe_only=bool(safe_only), stats=bool(stats),
        safe_note=", forces the lossless path (no model)" if safe_only else ", follows the ctxprune preset"))
    desc = HERMES_DESC_SAFE if safe_only else HERMES_DESC_SAFE.replace(
        " Stdlib Python, no model, no network.", " With a lossy preset (balanced) big outputs also go through the "
        "local line model (ctxprune serve).")
    _write(os.path.join(dest, "plugin.yaml"),
           f"# {SENTINEL}: generated by `ctxprune bundle hermes` (ctxprune {__version__}, {build} build)\n"
           f"name: ctxprune\nversion: {_yaml_str(__version__)}\ndescription: {_yaml_str(desc)}\n"
           f"author: riceharvest\nrequires_hermes: \">=0.21.0\"\nprovides_hooks:\n"
           + "".join(f"  - {h}\n" for h in HERMES_HOOKS) + "provides_middleware:\n"
           + "".join(f"  - {m}\n" for m in HERMES_MIDDLEWARE))
    return ["__init__.py", "_impl.py", "plugin.yaml"] + files


def build_claude_code(root: str, keep_marketplace: bool = True) -> list:
    """Write the `ctxprune-safe` plugin to root/claude-code-plugin-safe and add it to root/.claude-plugin/
    marketplace.json (other entries kept)."""
    pdir = os.path.join(root, "claude-code-plugin-safe")
    if os.path.isdir(os.path.join(pdir, "scripts", "rt")):
        shutil.rmtree(os.path.join(pdir, "scripts", "rt"))
    plugin = {"name": "ctxprune-safe", "version": __version__,
              "description": "Lossless: rewrites big Bash, Grep and MCP tool outputs into a shorter form without "
                             "dropping anything (grep hits grouped by file, repeated lines and earlier outputs "
                             "referenced). Stdlib Python, no model, no network; fails open.",
              "author": AUTHOR, "homepage": REPO, "repository": REPO, "license": "0BSD",
              "keywords": ["context", "tokens", "tool-output", "lossless", "compression"]}
    hooks = {"hooks": {"PostToolUse": [{"matcher": glue.HOOK_MATCHER, "hooks": [
        {"type": "command", "command": "CTXPRUNE_PRESET=safe \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook.sh",
         "timeout": 10}]}]}}
    _write(os.path.join(pdir, ".claude-plugin", "plugin.json"), json.dumps(plugin, indent=2) + "\n")
    _write(os.path.join(pdir, "hooks", "hooks.json"), json.dumps(hooks, indent=2) + "\n")
    _write(os.path.join(pdir, "scripts", "hook.sh"), CC_HOOK_SH, 0o755)
    _write(os.path.join(pdir, "scripts", "hook.py"), CC_HOOK_PY.format(sentinel=SENTINEL, version=__version__))
    files = ["scripts/rt/" + f for f in _copy_runtime(os.path.join(pdir, "scripts", "rt"), glue.SAFE_MODULES)]
    mp_path = os.path.join(root, ".claude-plugin", "marketplace.json")
    mp = {}
    if keep_marketplace and os.path.exists(mp_path):
        with open(mp_path, encoding="utf-8") as f:
            mp = json.load(f)
    mp.setdefault("name", "ctxprune")
    mp.setdefault("description", "ctxprune: smaller agent tool outputs for Claude Code")
    mp.setdefault("owner", AUTHOR)
    entry = {"name": "ctxprune-safe", "source": "./claude-code-plugin-safe", "description": plugin["description"]}
    mp["plugins"] = [p for p in mp.get("plugins", []) if p.get("name") != "ctxprune-safe"] + [entry]
    _write(mp_path, json.dumps(mp, indent=2) + "\n")
    return [".claude-plugin/marketplace.json", "claude-code-plugin-safe/.claude-plugin/plugin.json",
            "claude-code-plugin-safe/hooks/hooks.json", "claude-code-plugin-safe/scripts/hook.sh",
            "claude-code-plugin-safe/scripts/hook.py"] + ["claude-code-plugin-safe/" + f for f in files]


def main(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ctxprune bundle", description=__doc__.split("\n")[0])
    ap.add_argument("target", choices=("hermes", "claude-code"))
    ap.add_argument("dest")
    ap.add_argument("--with-model", action="store_true", help="hermes: also bundle the lossy-preset code")
    ap.add_argument("--stats", action="store_true", help="hermes: write the counts-only stats log (default off: the "
                                                         "catalog build writes no files)")
    a = ap.parse_args(argv)
    sys.dont_write_bytecode = True
    files = build_hermes(a.dest, not a.with_model, a.stats) if a.target == "hermes" else build_claude_code(a.dest)
    print(f"wrote {len(files)} files under {a.dest}")
    for f in files:
        print("  " + f)
    return 0
