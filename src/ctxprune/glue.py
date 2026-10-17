"""ctxprune runtime glue: the ONE defaults table, the platform paths, and the single patch point every entry uses.

Standalone on purpose (stdlib only, no package-relative imports, Python 3.9+): the Hermes plugin runs inside Hermes'
own venv, where `import ctxprune` is not available, so it loads this file by path. Everything else imports it as
`ctxprune.glue`.

The runtime itself (lossless, lossless2, dedup, recover, ctxprune_core, the hooks, the proxy, the plugins) is the
research tree's `scripts/` directory, vendored unchanged into the wheel as `ctxprune/_rt/` (see pyproject.toml). This
module never re-implements any of it: it only sets the settings the scripts read (env), puts `_rt` on sys.path, and
wraps `ctxprune_core.process` (the function every entry point calls) for the kill switch and the counts-only stats.
"""

from __future__ import annotations

import json
import os
import sys
import time

# ---------------------------------------------------------------- defaults
# The preset table (safe | balanced | aggressive: model, keep, min_chars, select, per-harness opt-outs) is
# scripts/ctxprune_core.py PRESETS / PRESET_HARNESS / DEFAULT_PRESET ("safe"): ONE table, read here through
# resolve_preset(), never copied. Packaging only adds the knobs below (ports, upstream, hook timeout).
DEFAULTS = {
    "port": 8765,          # lossy presets: local v2 scoring server (`ctxprune serve`)
    "proxy_port": 8787,    # `ctxprune proxy` (Codex, or any OpenAI/Anthropic-compatible harness)
    "proxy_upstream": "https://api.openai.com",
    "hook_timeout": 30,    # seconds Claude Code waits for the hook (fails open after)
}
FALLBACK_PRESET = "safe"   # only if ctxprune_core can't be imported (broken install)

MIN_CLAUDE_CODE = (2, 1, 121)   # updatedToolOutput with the full tool object
HOOK_MATCHER = "Bash|PowerShell|Grep|mcp__.*"   # PowerShell: Windows Claude Code (lands with lossless-transforms)
# the scripts the shipping paths import (wheel: ctxprune/_rt; checked by `ctxprune doctor`)
SAFE_MODULES = ("ctxprune_core", "ctxprune_compress", "lossless", "lossless2", "dedup", "recover", "view_safe",
                "lossless_hook", "emitted", "spill", "read_trim")   # stdlib only: what the self-contained plugin bundles ship
MODEL_MODULES = ("v2_hook", "v2_server", "methods", "export_v2_onnx", "line_encode")
RUNTIME_MODULES = SAFE_MODULES + MODEL_MODULES + ("ctxprune_proxy", "ctxprune_note")

_TRUE = ("1", "true", "yes", "on")


# ---------------------------------------------------------------- paths
def _home() -> str:
    return os.path.expanduser("~")


def is_windows() -> bool:
    return os.name == "nt"


def config_dir() -> str:
    """CTXPRUNE_CONFIG_DIR > %APPDATA%\\ctxprune (Windows) > $XDG_CONFIG_HOME/ctxprune > ~/.config/ctxprune."""
    e = os.environ.get
    if e("CTXPRUNE_CONFIG_DIR"):
        return e("CTXPRUNE_CONFIG_DIR")
    if is_windows() and e("APPDATA"):
        return os.path.join(e("APPDATA"), "ctxprune")
    return os.path.join(e("XDG_CONFIG_HOME") or os.path.join(_home(), ".config"), "ctxprune")


def state_dir() -> str:
    """Saved outputs + dedup state (may hold tool output: created 0700/0600 by the scripts).
    CTXPRUNE_STATE > %LOCALAPPDATA%\\ctxprune\\cache (Windows) > $XDG_CACHE_HOME/ctxprune > ~/.cache/ctxprune."""
    e = os.environ.get
    if e("CTXPRUNE_STATE"):
        return e("CTXPRUNE_STATE")
    if is_windows() and e("LOCALAPPDATA"):
        return os.path.join(e("LOCALAPPDATA"), "ctxprune", "cache")
    return os.path.join(e("XDG_CACHE_HOME") or os.path.join(_home(), ".cache"), "ctxprune")


def stats_path() -> str:
    """Counts-only log (no tool output, no commands). CTXPRUNE_STATS > %LOCALAPPDATA%\\ctxprune\\stats.jsonl >
    $XDG_STATE_HOME/ctxprune/stats.jsonl > ~/.local/state/ctxprune/stats.jsonl. CTXPRUNE_STATS=off disables it."""
    e = os.environ.get
    if e("CTXPRUNE_STATS"):
        return e("CTXPRUNE_STATS")
    if is_windows() and e("LOCALAPPDATA"):
        return os.path.join(e("LOCALAPPDATA"), "ctxprune", "stats.jsonl")
    return os.path.join(e("XDG_STATE_HOME") or os.path.join(_home(), ".local", "state"), "ctxprune", "stats.jsonl")


def settings_file() -> str:
    return os.path.join(config_dir(), "settings.json")


def runtime_dir() -> str:
    """The vendored scripts: <package>/_rt in a wheel, <repo>/scripts in a source checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "ctxprune_core.py")):   # a self-contained plugin bundle: glue sits in _rt
        return here
    rt = os.path.join(here, "_rt")
    if os.path.isfile(os.path.join(rt, "ctxprune_core.py")):
        return rt
    src = os.path.normpath(os.path.join(here, "..", "..", "scripts"))
    return src if os.path.isfile(os.path.join(src, "ctxprune_core.py")) else rt


# ---------------------------------------------------------------- settings
def user_settings() -> dict:
    try:
        with open(settings_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _core():
    rt = runtime_dir()
    if rt not in sys.path:
        sys.path.append(rt)
    import ctxprune_core
    return ctxprune_core


def presets() -> tuple:
    try:
        return tuple(_core().PRESETS)
    except Exception:
        return (FALLBACK_PRESET,)


def preset_name() -> str:
    """CTXPRUNE_PRESET > settings.json (`ctxprune install --preset`) > ctxprune_core.DEFAULT_PRESET."""
    try:
        default = _core().DEFAULT_PRESET
    except Exception:
        default = FALLBACK_PRESET
    p = (os.environ.get("CTXPRUNE_PRESET") or user_settings().get("preset") or default).strip().lower()
    return p if p in presets() else default


def resolved(harness: str = "") -> dict:
    """ctxprune_core.resolve_preset for this harness (the preset table + per-harness opt-outs + CTXPRUNE_FORCE_LOSSY),
    plus the env switches the pipeline honours (CTXPRUNE_NO_MODEL; CTXPRUNE_DEDUP=1 / 0 via ctxprune_core.dedup_on:
    dedup is opt-in in every preset)."""
    try:
        force = os.environ.get("CTXPRUNE_FORCE_LOSSY", "").lower() in _TRUE
        core = _core()
        v = dict(core.resolve_preset(preset_name(), harness, force))
        v["dedup"] = core.dedup_on(bool(v.get("dedup")))
    except Exception:
        v = {"preset": FALLBACK_PRESET, "requested": FALLBACK_PRESET, "model": False,
             "dedup": os.environ.get("CTXPRUNE_DEDUP", "").strip().lower() in _TRUE}
    if os.environ.get("CTXPRUNE_NO_MODEL", "").lower() in _TRUE:
        v["model"] = False
    return v


def settings() -> dict:
    s = dict(DEFAULTS)
    s.update({k: v for k, v in user_settings().items() if k in DEFAULTS})
    s["preset"] = preset_name()
    return s


def off(command: str = "") -> bool:
    """Kill switch, as in v0.2.1: CTXPRUNE_OFF=1 in the environment, or `CTXPRUNE_OFF=1` inside the command."""
    return os.environ.get("CTXPRUNE_OFF", "") in _TRUE or "CTXPRUNE_OFF=1" in (command or "")


def apply_env() -> dict:
    """setdefault the env the scripts read: the preset name (resolved per harness by ctxprune_core itself) and the
    state dir. An explicit user env var always wins."""
    s = settings()
    os.environ.setdefault("CTXPRUNE_PRESET", s["preset"])
    os.environ.setdefault("CTXPRUNE_PORT", str(s["port"]))
    os.environ.setdefault("CTXPRUNE_STATE", state_dir())
    return s


def boot(own_process: bool = True, sys_path: bool = True) -> dict:
    """Call first in every entry point, BEFORE importing any runtime module (lossless_hook reads CTXPRUNE_STATE at
    import). own_process: our own short-lived process -> private umask (never inside Hermes: umask is process-wide)."""
    if own_process:
        try:
            os.umask(0o077)
        except Exception:
            pass
    if sys_path:
        rt = runtime_dir()
        if rt not in sys.path:   # appended: never shadow the host's modules (same rule as the scripts)
            sys.path.append(rt)
    s = apply_env()
    patch_core()
    return s


# ---------------------------------------------------------------- counts-only stats
STATS_FIELDS = ("before", "after", "dedup", "stage", "v2")
STATS_ON = True   # the catalog Hermes bundle sets False ("writes no files") unless CTXPRUNE_STATS names a file
STATS_MAX_BYTES = 8_000_000


def record(rec: dict, harness: str = "", tool: str = "") -> None:
    """Append one counts-only line: {ts, h, t, b, a, d, s, v}. Never the text, never the command. Fails silently."""
    if not STATS_ON and not os.environ.get("CTXPRUNE_STATS"):
        return
    path = stats_path()
    if path in ("off", "0", "none"):
        return
    try:
        line = json.dumps({"ts": round(time.time(), 1), "h": rec.get("harness") or harness or
                           os.environ.get("CTXPRUNE_HARNESS", ""), "t": (tool or rec.get("tool") or "")[:80],
                           "b": int(rec.get("before") or 0), "a": int(rec.get("after") or 0),
                           "d": int(rec.get("dedup") or 0), "s": rec.get("stage") or "",
                           "v": rec.get("v2") or ""}, separators=(",", ":")) + "\n"
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            if os.path.getsize(path) > STATS_MAX_BYTES:   # one rotation, bounded disk
                os.replace(path, path + ".1")
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def patch_core() -> bool:
    """Wrap ctxprune_core.process (imported lazily by every entry: lossless_hook, v2_hook, ctxprune_compress.run, the
    proxy) with the kill switch and the stats record. Idempotent; False when ctxprune_core can't be imported."""
    try:
        import ctxprune_core as cc
    except Exception:
        return False
    if getattr(cc.process, "_ctxprune_glue", False):
        return True
    orig = cc.process

    def process(text, command="", **kw):
        if off(command):
            rec = {"before": len(text), "after": len(text), "stage": "off", "v2": "off"}
            record(rec, harness=getattr(kw.get("settings"), "harness", "") or "", tool=kw.get("tool") or "")
            return text, rec
        out, rec = orig(text, command, **kw)
        try:
            s = kw.get("settings")
            record(rec, harness=getattr(s, "harness", "") or "", tool=kw.get("tool") or "")
        except Exception:
            pass
        return out, rec

    process._ctxprune_glue = True
    process.__wrapped__ = orig
    process.__doc__ = orig.__doc__
    cc.process = process
    _patch_context()
    return True


def _patch_context() -> None:
    """Per-request dedup of the plugins (ctxprune_compress.context: Hermes middleware, Pi `context`, OpenCode
    messages.transform): kill switch + one stats row per request that saved something (dedup chars only: the
    request's full history is resent every turn, so its `before` would be counted again and again)."""
    try:
        import ctxprune_compress as cx
    except Exception:
        return
    orig = getattr(cx, "context", None)
    if orig is None or getattr(orig, "_ctxprune_glue", False):
        return

    def context(events, harness=None, log=None):
        if off():
            return {}, {"stage": "off"}
        out, st = orig(events, harness, log)
        try:
            saved = int((st or {}).get("chars_saved") or 0)
            if out and saved > 0:
                record({"before": 0, "after": 0, "dedup": saved, "stage": "context", "v2": ""}, harness=harness or "",
                       tool="(request dedup)")
        except Exception:
            pass
        return out, st

    context._ctxprune_glue = True
    context.__wrapped__ = orig
    cx.context = context
