#!/usr/bin/env python3
"""ctxprune shared compressor CLI: the ONE entry point every native harness integration calls (Hermes plugin
in-process via run(), Pi extension and OpenCode plugin via `execFileSync(python3, [this, ...], {input})`).

  stdin (or --input-file) text -> stdout compressed text. Always exit 0.

Pipeline (same as v2_hook.py / ctxprune_proxy.py, PLAN §3):
  1. lossless.compress(text, command)                       rules, deterministic, every kept line byte-exact
  2. if the lossless result is >= --min-chars and not --no-model: the v2 line model via v2_server.py
     (127.0.0.1:--port, POST /compress) at --keep, intent = --intent or the command; used only if shorter.
Fail open: model down/slow -> lossless result; any other error -> the input unchanged (byte-exact).

Settings: CTXPRUNE_PRESET (safe | balanced | aggressive; default safe = lossless only) resolved for CTXPRUNE_HARNESS
(ctxprune_core.PRESETS / PRESET_HARNESS: balanced = safe on codex/opencode/pi/dsh unless CTXPRUNE_FORCE_LOSSY=1);
then env, then flags (so a plugin needs no flags; the runner sets env). No refetch guard here (no session state).
  --keep CTXPRUNE_KEEP  --min-chars CTXPRUNE_MIN_CHARS (preset's)  --port CTXPRUNE_PORT (8765)
  --timeout CTXPRUNE_TIMEOUT (20 s)  --no-model CTXPRUNE_NO_MODEL=1  --log CTXPRUNE_LOG  --harness CTXPRUNE_HARNESS
Log (only when --log/CTXPRUNE_LOG is set): one JSONL line per call
  {ts, harness, tool, cmd, before, after, stage: none|lossless|model|error, v2: skipped|ok|down|not_smaller|off,
   rules, ms, probe}
  probe = the first ctxprune marker line in the output (a gap marker, a fence ...), else the first new line (80 chars),
  so a runner can check that the replaced text really reached the model (search the logged requests for it).
  Contents (audit L4): sizes, rules, tool name, the first 80 chars of the command, one probe line; never the
  output itself. Written only when CTXPRUNE_LOG is set; the file is local and plain JSONL (mind its permissions).

Usage: python3 ctxprune_compress.py [--command CMD] [--intent TEXT] [--tool NAME] [--keep R] [--min-chars N]
         [--port P] [--no-model] [--input-file PATH] < in > out

Cross-turn dedup (request time, dedup.context_dedup): the plugins call it on every model REQUEST, with the tool calls
and results of that request in order (Hermes in-process via context(); Pi `context` / OpenCode messages.transform via
  python3 ctxprune_compress.py --context HARNESS < {"events": [...], "seed": ...} > {"out": {"<event index>": "<new text>"}}
). Off with CTXPRUNE_DEDUP=0 or CTXPRUNE_OFF=1; fails open the same way. Byte stability: what a result was first sent
as is pinned (<state>/context-store.sqlite, render_store.py) and served again on every later request, across upgrades
and toggles; off only stops NEW results from being deduped (pinned ones keep their text), CTXPRUNE_RERENDER=1 re-renders
everything (context()). Log record (CTXPRUNE_LOG):
  {ts, harness, stage: "context" | "context_pins", results, pinned, fired, before, after, ms}  (counts only)
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _env(name: str, default, cast):
    try:
        v = os.environ.get(name)
        return cast(v) if v not in (None, "") else default
    except ValueError:
        return default


def _probe(before: str, after: str) -> str | None:
    """A marker line ctxprune added (no tool-output content), else the first new line cut to 80 chars."""
    seen = set(before.split("\n"))
    new = [ln for ln in after.split("\n") if ln.strip() and ln not in seen]
    for ln in new:
        if ln.startswith(("[ctxprune", "[/ctxprune]", "[cp:", "[/cp]", "[…")):
            return ln[:120]
    return new[0][:80] if new else None


def _log(path: str | None, rec: dict) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _argv_utf8(s):
    """An argv string as the plugins sent it: Node passes UTF-8 bytes, but Python decodes argv with the LOCALE
    encoding, so under LC_ALL=de_DE.iso88591 / ja_JP.eucjp a non-ASCII command came in as mojibake and changed the
    output (dedup labels, grep path tokens; determinism audit 2026-10-17). Back to the bytes, then UTF-8. POSIX only
    (Windows argv is UTF-16 already); identity on a UTF-8 locale."""
    if not isinstance(s, str) or os.name == "nt":
        return s
    try:
        return os.fsencode(s).decode("utf-8", "surrogateescape")
    except (UnicodeError, ValueError):
        return s


def run(text: str, command: str | None = None, intent: str | None = None, tool: str | None = None,
        keep: float | None = None, min_chars: int | None = None, port: int | None = None,
        timeout: float | None = None, no_model: bool | None = None, harness: str | None = None,
        log: str | None = None, target: str | None = None, llm: str | None = None) -> tuple[str, dict]:
    """-> (text to show the model, log record). target: the model's base URL / provider (CTXPRUNE_TARGET; a local
    or self-hosted one turns the `paths` rule off, lossless.paths_on); the caller keeps it fixed per session. Never raises; returns the input unchanged on any error.
    The pipeline itself is ctxprune_core.process (one order for every entry point). No cross-turn dedup here: a
    plugin can't see at tool time what the harness will still hold in context later (compaction, prune), so
    references could dangle; dedup runs per request instead (context()).
    CTXPRUNE_OFF=1 (kill switch, as in the Claude Code hooks): the input unchanged."""
    if os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes"):
        return text, {"before": len(text), "after": len(text), "stage": "off"}
    try:   # our own earlier output handed back (a harness re-running its post-execute hook on a re-rendered result,
        # e.g. dsh after compaction): pass it through byte-identical. A second pass would escape our own markers and
        # change bytes the provider has cached (wave C q36 2026-10-18: 2 cache breaks attributed to safe in dsh).
        from emitted import seen
        if seen(text):
            return text, {"before": len(text), "after": len(text), "stage": "emitted"}
    except Exception:
        pass
    from ctxprune_core import Settings, process
    log = log or os.environ.get("CTXPRUNE_LOG") or None
    s = Settings.from_env(keep=keep, min_chars=min_chars, port=port, timeout=timeout, harness=harness,
                          model=(not no_model) if no_model is not None else None, target=target or None,
                          llm=llm)   # llm: the session's model id (None = CTXPRUNE_MODEL): lossless.PATHS_WIN_MODELS
    out, rec = process(text, command or "", tool=tool or "", intent=intent or "", settings=s)
    rec["ts"] = time.time()
    rec["cmd"] = (rec.get("cmd") or "")[:80]   # audit L4: keep the log minimal
    rec["probe"] = _probe(text, out) if out != text else None
    _log(log, rec)
    if out != text:   # a ctxprune_proxy stacked behind this plugin forwards it untouched (emitted.py)
        from emitted import record
        record(out)
    return out, rec


_PSEP = None


def escaped_only(out: str, text: str) -> bool:
    """= escapedOnly() of the Pi / OpenCode / dsh plugins: `out` differs from `text` only by one backslash after the
    invisible prefix of some lines (the H2 escape; two after U+0085 / U+2028 / U+2029, which may stand inside a JSON
    string), split at the escaper's line boundaries."""
    global _PSEP
    import re
    from lossless import INVISIBLE
    if _PSEP is None:
        _PSEP = (re.compile(r"(\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029])"), re.compile(r"^([" + INVISIBLE + r"]*)\\"))
    sep, inv = _PSEP
    a, b = sep.split(out), sep.split(text)
    if len(a) != len(b):
        return False
    n = 0
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        k = 2 if i and a[i - 1] in ("\x85", "\u2028", "\u2029") else 1
        z = x
        for _ in range(k):
            z = inv.sub(r"\1", z, count=1)
        if i % 2 or len(x) != len(y) + k or z != y:
            return False
        n += 1
    return n > 0


def plugin_safe(out: str, text: str) -> str:
    """The CLI's answer to a plugin that keeps a result only when it is shorter or escape-only (Pi, OpenCode, dsh):
    a longer result that is not escape-only (H2 escapes plus a json_truncated note, or plus noise rules that saved
    fewer chars than the escapes added) would be dropped and the RAW text, forged markers unescaped, shown instead
    (red team 2: 146 of 64k fuzz outputs). Such a result becomes the escape-only text."""
    try:
        if out == text or len(out) < len(text) or escaped_only(out, text):
            return out
        from lossless import escape_markers
        esc = escape_markers(text)
        return esc if esc != text else out
    except Exception:  # noqa: BLE001
        return out


CONTEXT_VERSION = "context-1"   # metadata on the pins only (render_store: the version that first rendered a result)
_STORES: dict = {}


def context_store_path() -> str:
    """The first-render pins of the request-time dedup (shared by Hermes in-process and the Pi / OpenCode CLI calls).
    Pi / OpenCode only start the CLI while dedup is on, or while this file exists (pins to keep serving: with dedup
    off that is one CLI start per model request). render_store has no age-only deletion since safety gate b (pins
    live a year past their last use, removed only over the size cap), so once any conversation was deduped the file
    stays and a non-Opus Pi / OpenCode session pays one CLI start (~50 ms) per model request."""
    return os.path.join(os.environ.get("CTXPRUNE_STATE") or os.path.expanduser("~/.cache/ctxprune"),
                        "context-store.sqlite")


def _context_store(path: str):
    st = _STORES.get(path)
    if st is None:
        from render_store import RenderStore
        st = _STORES[path] = RenderStore(path)
    return st


def context(events: list, harness: str | None = None, log: str | None = None, seed=None,
            model: str | None = None, dedup: bool | None = None) -> tuple[dict, dict]:
    """Request-time cross-turn dedup for a harness plugin: -> ({event index: new text}, stats). The events are the
    request's tool calls and results in order (dedup.context_dedup). Never raises; {} when off or on any error.
    Byte-stable across upgrades and toggles: every result's first rendering is pinned (context_store_path(), keyed by
    the request prefix), and a result already sent always gets its pinned text. Dedup off / CTXPRUNE_OFF=1 after
    dedup was on: pinned results keep their text, new ones go out unchanged. CTXPRUNE_RERENDER=1: pins ignored and
    overwritten (with CTXPRUNE_OFF=1: everything back to the original; one prompt-cache miss per conversation).
    seed: what identifies the conversation besides its tool calls (the first user message, the system prompt), so
    two conversations that start with the same tool output don't share pins."""
    t0 = time.perf_counter()
    try:
        from ctxprune_core import Settings
        harness = harness or os.environ.get("CTXPRUNE_HARNESS") or ""
        off = os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes")
        rerender = os.environ.get("CTXPRUNE_RERENDER", "").strip().lower() in ("1", "true", "yes", "on")
        if dedup is None:   # by model (ctxprune_core.DEDUP_MODELS: claude-opus-*), CTXPRUNE_DEDUP wins
            dedup = Settings.from_env(harness=harness, llm=model).dedup
        path = context_store_path()
        pin_only = off or not dedup
        if pin_only and not os.path.exists(path):
            return {}, {"stage": "off" if off else "dedup_off"}
        from dedup import context_dedup
        memo = _context_store(path)
        out, st = context_dedup(events, harness, memo=memo, pin_only=pin_only, rerender=rerender,
                                ver=CONTEXT_VERSION, seed=seed)
        if pin_only and memo.db is not None and memo.count("pin") == 0:   # every pin aged out (render_store.evict):
            memo.close()                                                  # nothing left to keep, so the plugins stop
            _STORES.pop(path, None)                                       # starting the CLI per request
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(path + ext)
                except OSError:
                    pass
        if out:   # a ctxprune_proxy stacked behind the harness forwards these untouched (emitted.py)
            try:
                from emitted import record
                for t in out.values():
                    record(t)
            except ImportError:   # a bundle without the registry: dedup still applies
                pass
        _log(log or os.environ.get("CTXPRUNE_LOG") or None,
             {"ts": time.time(), "harness": harness, "stage": "context" if not pin_only else "context_pins",
              "results": st.get("results"), "pinned": st.get("pinned"),
              "fired": st.get("fired"), "before": st.get("chars_before"),
              "after": (st.get("chars_before") or 0) - (st.get("chars_saved") or 0),
              "ms": round((time.perf_counter() - t0) * 1000, 1), **({"error": st["error"]} if "error" in st else {})})
        return out, st
    except Exception as e:  # noqa: BLE001 - fail open: the request goes out unchanged
        return {}, {"stage": "error", "error": repr(e)[:200]}



class _Args:
    pass


def _parse(argv: list[str], spec: dict) -> _Args:
    """argparse.parse_known_args for the few CLI options, without argparse (Python 3.14: importing it and building a
    parser pulls in _colorize / shutil / dataclasses, ~15 ms per plugin call; canary 2026-10-18). spec: {"--name":
    type | bool (a flag)}; `--name value` / `--name=value`; unknown arguments are ignored; a bad value raises."""
    a = _Args()
    for k in spec:
        setattr(a, k[2:].replace("-", "_"), None)
    i = 0
    while i < len(argv):
        x = argv[i]
        name, eq, val = x.partition("=")
        typ = spec.get(name)
        if typ is None:
            i += 1
            continue
        if typ is bool:
            if eq:
                raise ValueError(x)
            v = True
        elif eq:
            v = typ(val)
        else:
            i += 1
            if i >= len(argv):
                raise ValueError(x)
            v = typ(argv[i])
        setattr(a, name[2:].replace("-", "_"), v)
        i += 1
    return a

def main_context(harness: str, log: str | None) -> int:
    """--context HARNESS: stdin {"events": [...], "seed": any} -> stdout {"out": {"<index>": text}}; {"out": {}} on
    any error."""
    res: dict = {}
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8", "surrogatepass"))
        m = req.get("model")
        out, _ = context(req.get("events") or [], harness, log, req.get("seed"), m if isinstance(m, str) else None)
        res = {str(k): v for k, v in out.items()}
    except BaseException:  # noqa: BLE001
        res = {}
    try:   # ASCII JSON: lone surrogates from a JS string survive the round trip
        sys.stdout.write(json.dumps({"out": res}, ensure_ascii=True))
    except Exception:
        sys.stdout.write('{"out": {}}')
    return 0


def main() -> int:
    for x in sys.argv[1:]:   # --dedup-for=MODEL: "1" / "0" (the plugins ask once per session; one allow-list, in core)
        if x.startswith("--dedup-for="):
            try:
                from ctxprune_core import dedup_on
                sys.stdout.write("1" if dedup_on(False, _argv_utf8(x.split("=", 1)[1])) else "0")
            except BaseException:  # noqa: BLE001
                sys.stdout.write("0")
            return 0
    if any(x == "--context" or x.startswith("--context=") for x in sys.argv[1:]):
        try:
            a = _parse(sys.argv[1:], {"--context": str, "--log": str})
        except BaseException:  # noqa: BLE001
            sys.stdout.write('{"out": {}}')
            return 0
        return main_context(a.context or "", a.log)
    raw = b""
    try:
        a = _parse(sys.argv[1:], {"--command": str, "--intent": str, "--tool": str, "--keep": float,
                                  "--min-chars": int, "--port": int, "--timeout": float, "--no-model": bool,
                                  "--harness": str, "--target": str, "--log": str, "--input-file": str})
        # (unknown flags: ignored, fail open, never crash a harness)
        a.command, a.intent, a.tool, a.target = (_argv_utf8(x) for x in (a.command, a.intent, a.tool, a.target))
        if a.input_file:
            with open(a.input_file, "rb") as f:
                raw = f.read()
        else:
            raw = sys.stdin.buffer.read()
        text = raw.decode("utf-8", "surrogateescape")
        out, _ = run(text, a.command, a.intent, a.tool, a.keep, a.min_chars, a.port, a.timeout, a.no_model,
                     a.harness, a.log, a.target)
        out = plugin_safe(out, text)
        sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    except BaseException:  # noqa: BLE001 - fail open: echo the input bytes (bad option values included)
        try:
            if not raw and not sys.stdin.isatty():
                raw = sys.stdin.buffer.read()
            sys.stdout.buffer.write(raw)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
