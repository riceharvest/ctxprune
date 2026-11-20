"""ctxprune 0.4.1 for Hermes (safe-only build, ctxprune-managed). Self-contained: the stdlib runtime is in _rt/ next
to this file, so it needs no pip install. _impl.py is scripts/integrations/hermes/ctxprune/__init__.py (fallback path -> _rt/); this
loader points it at _rt/, applies the CTXPRUNE_OFF kill switch, forces the lossless path (no model), and fails open (no change) on any error.
Regenerate with `ctxprune bundle hermes DIR`; don't edit.
"""

import importlib.util as _u
import os as _os

_DIR = _os.path.dirname(_os.path.abspath(__file__))
_RT = _os.path.join(_DIR, "_rt")
_SAFE_ONLY = True
_STATS = False   # counts-only stats log (~/.local/state/ctxprune/stats.jsonl); off in catalog builds


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
    # no OFF short-circuit: the runtime's OFF is soft here (pinned history keeps its bytes, so no prompt-cache miss)
    if _orig_llm is None:
        return None
    return _orig_llm(request, **kw)


if _orig_llm is not None:
    _impl.on_llm_request = on_llm_request   # register_dedup registers the module global: the kill-switch wrapper


def register(ctx):
    ctx.register_hook("transform_terminal_output", on_terminal_output)
    ctx.register_hook("transform_tool_result", on_tool_result)
    if hasattr(_impl, "register_dedup"):
        _impl.register_dedup(ctx)   # llm_request middleware: per-request cross-turn dedup (lossless)
