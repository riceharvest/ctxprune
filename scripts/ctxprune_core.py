#!/usr/bin/env python3
"""ctxprune core: ONE pipeline for every entry point (runtime audit 2026-10-17: the pipeline existed 4x in Python and
5x in plugins, and the copies had drifted — e.g. the Claude Code hooks deduped the RAW text but registered the
post-lossless text, so a repeated grep/pytest output never matched).

    process(text, command, ...) -> (text for the model, record)

Order (fixed):
  1. lossless stage 0 (lossless.compress: CR/ANSI, JSON, dup runs, grep grouping, lossless2) — always, exact
  2. lossy model (v2_server over HTTP), only if enabled, above min_chars, not an error result, not a FILE VIEW;
     harness output caps are respected by tightening keep; a circuit breaker skips the model for BREAK_S seconds after a timeout/error
  3. recoverable: the stage-0 text is saved and every gap gets its exact range (recover.py), built from the kept line
     indices the server returns (want_keep); if that fails, the stage-0 text is kept (rec["recover"] = "failed")
  4. cross-turn dedup against the session's earlier rendered outputs and tool inputs (only when the caller passes a
     session that knows what is still in context: Claude Code hook via the transcript, the proxy via the request
     itself); outputs shorter than session.min_len (the shortest marker) skip it. The Hermes / Pi / OpenCode plugins
     don't pass one: they dedup each model request from its message list instead (dedup.context_dedup)
  5. the caller registers the final text with the session (session.register)
Never raises: any error returns the best text produced so far (stage 0 at worst, the input at the very worst).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:   # appended, not prepended: inside Hermes/the proxy our generic module names must not shadow
    sys.path.append(_HERE)

# audit H1: localhost calls never go through http(s)_proxy (tool output + intent would leave the machine).
# Built on first use: urllib.request costs ~23 ms of import, and most hook calls never reach the model.
_OPENER = None


def _open(req, timeout: float):
    global _OPENER
    if _OPENER is None:
        import urllib.request
        _OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _OPENER.open(req, timeout=timeout)

HARNESS_CAPS = {"pi": (51_200, 2000), "opencode": (51_200, 2000), "hermes": (50_000, None),
                "dsh": (45_000, None),   # dsh spill-policy maxInlineTokens 12500 (token estimate)
                "cc-persisted": (24_000, None)}   # Claude Code persisted output shown inline (lossless_hook PERSIST_*)
CAP_MARGIN = 0.9
BREAK_S = 120


# ---------------------------------------------------------------- presets (PLAN §2.657; results/consolidated/balanced.md)
# ONE table, read by every entry point through Settings.from_env (and by packaging). Precedence, low -> high:
#   PRESETS[name] -> PRESET_HARNESS[harness] (lossy presets only) -> env (CTXPRUNE_KEEP, _MIN_CHARS, _SELECT, _ALPHA,
#   _NO_MODEL, _RECOVERABLE, _DEDUP, _GUARD_TURNS) -> explicit keyword overrides (CLI flags).
# File views (lossless.is_view) never go to the model in any preset (CTXPRUNE_VIEW_MODEL=1 = ablation only): the
# replay put 71% of v2's excess refetch on views, and leaving them whole removed ~2/3 of it (replay_fold audit).
# select: the v2_server selector sent with every request (line | smooth | anchored); "" = the server's --select.
DEFAULT_PRESET = "safe"
BALANCED_SELECT = "anchored"   # replay verdict (v2anchnv / v2smoothnv / v2noview): results/consolidated/balanced.md
PRESETS: dict[str, dict] = {
    # dedup OFF in every preset (opt in: CTXPRUNE_DEDUP=1): the live comprehension probe on Qwen3.8 (results/
    # lprobe-dedup-live/table.md) lost 7 pts with whole-output refs, 18 with line-run refs and 21-27 with input refs,
    # with 1.5-4x the thinking tokens, and the marker note did not help.
    # lossless stage 0; nothing is ever dropped
    "safe": {"model": False, "keep": 0.5, "min_chars": 2000, "select": "", "recoverable": True, "dedup": False},
    # + the model on logs / tests / search / other non-view outputs >= min_chars, few large gaps, every gap recoverable
    "balanced": {"model": True, "keep": 0.5, "min_chars": 2000, "select": BALANCED_SELECT, "recoverable": True,
                 "dedup": False},
    # one step past balanced; the user asked for it, so the harness opt-out below does not apply
    "aggressive": {"model": True, "keep": 0.33, "min_chars": 1500, "select": BALANCED_SELECT, "recoverable": True,
                   "dedup": False},
}
# Per-harness overrides of `balanced` (wave data, results/consolidated/README.md + matrix-*.md, ratio-of-totals cost):
#   lossy=False -> balanced runs as safe there unless forced (CTXPRUNE_FORCE_LOSSY=1 / force=True): Codex +36.8%
#   [+15.8, +60.6] (holes in views -> python re-read loops); dsh +14% prompt tokens and +1.9 turns per episode (everyday);
#   OpenCode and Pi: lossy did not lower cost reliably in their general waves (+1.5% Pi, -3.4% OpenCode, CIs +-15-20 pts)
#   -> conservative opt-out (lead's call, 2026-10-17). Hermes stays lossy (general +9.2% [-1.5, +19.9], everyday
#   -18.2% at n=13) although its plugin, like every plugin CLI, gets no refetch guard (no session state).
#   keep: Claude Code paid +4.8..+11.5% at 0.5 (extra turns) -> a lighter 0.6; persisted (>30k) outputs go to Claude
#   Code's own file + preview (persisted pairs cost +24.7% with the model at 24k inline).
PRESET_HARNESS: dict[str, dict] = {
    "codex": {"lossy": False}, "dsh": {"lossy": False}, "opencode": {"lossy": False}, "pi": {"lossy": False},
    "claude-code": {"keep": 0.6, "persisted": "skip"},
    "hermes": {"keep": 0.5},
}
GUARD_WINDOW = 3    # a re-run / recovered-range read within this many tool calls of a lossy output = a refetch
GUARD_TURNS = 10    # ... then that session runs `safe` for this many tool calls (CTXPRUNE_GUARD_TURNS; 0 = off)
SELECTS = ("line", "smooth", "anchored")
_TRUE = ("1", "true", "yes", "on")


def dedup_on(default: bool = False) -> bool:
    """Cross-turn dedup is opt-in: CTXPRUNE_DEDUP=1|true|yes|on turns it on, 0|false|no|off off, unset = default."""
    x = os.environ.get("CTXPRUNE_DEDUP", "").strip().lower()
    return True if x in _TRUE else False if x in ("0", "false", "no", "off") else default


def resolve_preset(name: str | None, harness: str = "", force: bool = False) -> dict:
    """The settings a preset gives on a harness: {"preset": effective name, "requested": name, "model", "keep",
    "min_chars", "select", "recoverable", "dedup", "persisted"}. Unknown names fall back to DEFAULT_PRESET."""
    req = (name or DEFAULT_PRESET).strip().lower()
    if req not in PRESETS:
        req = DEFAULT_PRESET
    v = dict(PRESETS[req], persisted="drop", preset=req, requested=req)
    h = PRESET_HARNESS.get((harness or "").strip().lower(), {})
    if req == "balanced":
        if h.get("lossy") is False and not force:
            v.update(PRESETS["safe"], preset="safe", opt_out=True)
        else:
            v.update({k: h[k] for k in ("keep", "min_chars", "select", "persisted") if k in h})
    if not v["model"]:
        v["persisted"] = "skip"   # a persisted output never fits inline without the model
    return v


def preset_table(harnesses=("", "claude-code", "hermes", "codex", "opencode", "pi", "dsh")) -> list[dict]:
    """Every (preset, harness) resolved, for docs / packaging."""
    return [dict(resolve_preset(p, h), harness=h or "*") for p in PRESETS for h in harnesses]


class Settings:
    """Pipeline settings (a plain class: dataclasses costs ~9 ms of import in every hook process). Build them with
    Settings.from_env(); the constructor defaults are the raw pipeline knobs (model on), kept for tests and callers
    that set everything themselves."""
    FIELDS = ("keep", "min_chars", "port", "timeout", "model", "recoverable", "dedup", "harness", "view_model", "state",
              "preset", "select", "alpha", "guard_turns", "persisted", "target")

    def __init__(self, keep: float = 0.5, min_chars: int = 2000, port: int = 8765, timeout: float = 20.0,
                 model: bool = True, recoverable: bool = True, dedup: bool = False, harness: str = "",
                 view_model: bool | None = None, state: str | None = None, preset: str = "", select: str = "",
                 alpha: float | None = None, guard_turns: int = GUARD_TURNS, persisted: str = "drop", target: str = ""):
        self.keep, self.min_chars, self.port, self.timeout = keep, min_chars, port, timeout
        self.model = model              # False = lossless only (the `safe` preset)
        self.recoverable, self.dedup, self.harness = recoverable, dedup, harness
        # file views (lossless.is_view) get lossless only. Harness data 2026-10-17: holes in `sed -n` views made Codex
        # re-read ranges in python loops (+36.8% cost); CTXPRUNE_VIEW_MODEL=1 restores the old behaviour (ablation)
        self.view_model = (os.environ.get("CTXPRUNE_VIEW_MODEL", "") in _TRUE) if view_model is None else view_model
        self.state = state or os.environ.get("CTXPRUNE_STATE") or os.path.expanduser("~/.cache/ctxprune")
        self.preset = preset            # effective preset name ("" = built by hand)
        self.select = select if select in SELECTS else ""   # "" = the server's --select
        self.alpha = alpha              # anchored head/tail share; None = the server's --alpha
        self.guard_turns = guard_turns  # refetch guard hold (tool calls); 0 = off
        self.persisted = persisted      # Claude Code > 30k outputs: skip | keep | drop (v2_hook --persisted)
        self.target = target            # model endpoint (base URL and/or provider): local -> no `paths` rule
                                        # (lossless.paths_on; CTXPRUNE_PATHS=0|1 overrides)

    def __repr__(self) -> str:
        return "Settings(" + ", ".join(f"{k}={getattr(self, k)!r}" for k in self.FIELDS) + ")"

    def __eq__(self, other) -> bool:
        return isinstance(other, Settings) and all(getattr(self, k) == getattr(other, k) for k in self.FIELDS)

    @classmethod
    def from_env(cls, preset: str | None = None, force: bool | None = None, **over) -> "Settings":
        """CTXPRUNE_PRESET (safe | balanced | aggressive; default safe) resolved for CTXPRUNE_HARNESS, then the env
        knobs, then `over` (None values ignored: an unset CLI flag never clobbers the preset)."""
        e = os.environ.get
        harness = over.pop("harness", None) or e("CTXPRUNE_HARNESS", "")
        if force is None:
            force = e("CTXPRUNE_FORCE_LOSSY", "").lower() in _TRUE
        v = resolve_preset(preset or e("CTXPRUNE_PRESET"), harness, force)

        def env(name, cast, default):
            try:
                x = e(name)
                return cast(x) if x not in (None, "") else default
            except ValueError:
                return default
        s = cls(keep=env("CTXPRUNE_KEEP", float, v["keep"]), min_chars=env("CTXPRUNE_MIN_CHARS", int, v["min_chars"]),
                port=env("CTXPRUNE_PORT", int, 8765), timeout=env("CTXPRUNE_TIMEOUT", float, 20.0),
                model=v["model"] and e("CTXPRUNE_NO_MODEL", "").lower() not in _TRUE,
                recoverable=v["recoverable"] and e("CTXPRUNE_RECOVERABLE", "1") != "0",
                dedup=dedup_on(v["dedup"]), harness=harness, preset=v["preset"],
                select=env("CTXPRUNE_SELECT", str, v["select"]), alpha=env("CTXPRUNE_ALPHA", float, None),
                guard_turns=env("CTXPRUNE_GUARD_TURNS", int, GUARD_TURNS), persisted=v["persisted"],
                target="" if harness == "claude-code" else e("CTXPRUNE_TARGET", ""))   # CC hooks: hosted, paths on
        for k, val in over.items():
            if val is not None:
                setattr(s, k, val)
        if not s.model:
            s.persisted = "skip"
        return s


# ---------------------------------------------------------------- refetch guard (PLAN §2.657 runtime guard)
_RECOVER_FILE = re.compile(r"\b([0-9a-f]{12})\.txt\b")
_FOOTER = re.compile(r"^\[ctxprune: kept \d+ of \d+ lines; omitted ranges are lines A–B of (.+?) \(e\.g\. ", re.M)
_TAIL_FILTER = re.compile(r"\s*(?:\|\s*(?:head|tail|less|more|cat)\b[^|]*|2>&1)\s*$")


def cmd_key(command: str) -> str:
    """A command with whitespace collapsed and trailing `| head/tail ...` / `2>&1` stripped (a re-run that only
    changes the view of the same output is still a re-run)."""
    c = re.sub(r"\s+", " ", command or "").strip()
    while True:
        c2 = _TAIL_FILTER.sub("", c).strip()
        if c2 == c:
            return c
        c = c2


def recover_file_of(text: str) -> str:
    """The recovery file a lossy output names in its footer (basename `<sha12>.txt`), else ""."""
    m = _FOOTER.search(text or "")
    if not m:
        return ""
    f = _RECOVER_FILE.search(m.group(1))
    return f.group(0) if f else ""


class Guard:
    """In-session "too much" detector over a plain dict (persisted by the caller: the Claude Code hook keeps it in the
    dedup session state, the proxy rebuilds it from each request's history). One observe() per tool call, in order;
    note() after a tool result. A call that re-runs the command of a lossy output, or reads that output's recovery
    file, within GUARD_WINDOW calls trips the guard: this call and the next `turns` - 1 calls run safe."""

    def __init__(self, st: dict, turns: int = GUARD_TURNS, window: int = GUARD_WINDOW):
        self.st, self.turns, self.window = st, turns, window
        for k, d in (("t", 0), ("until", 0), ("trips", 0), ("lossy", []), ("ids", [])):
            st.setdefault(k, d)

    def observe(self, call_id: str = "", command: str = "", extra: str = "") -> bool:
        """Count one tool call; True when it is a refetch of a recent lossy output (the guard trips). A call id seen
        before is not counted again (the hook sees the current call both directly and in the transcript)."""
        st = self.st
        if call_id:
            if call_id in st["ids"]:
                return False
            st["ids"] = (st["ids"] + [call_id])[-64:]
        st["t"] += 1
        t, key, blob = st["t"], cmd_key(command), f"{command}\n{extra}"
        st["lossy"] = [x for x in st["lossy"] if t - x[0] <= self.window]
        hit = any((x[1] and key == x[1]) or (x[2] and x[2] in blob) for x in st["lossy"] if x[0] < t)
        if hit and self.turns > 0:
            st["until"], st["trips"] = t + self.turns - 1, st["trips"] + 1
            st["lossy"] = []
        return hit

    def safe(self) -> bool:
        return self.turns > 0 and self.st["until"] >= self.st["t"] > 0

    def note(self, command: str, out_text: str, t: int | None = None) -> list | None:
        """Record a lossy output (one that names a recovery file) of the current call; returns the record."""
        f = recover_file_of(out_text)
        if not f:
            return None
        rec = [self.st["t"] if t is None else t, cmd_key(command), f]
        self.add(rec)
        return rec

    def add(self, rec) -> None:
        """Add [t, command key, recovery file]; the same output noted twice keeps its earliest t."""
        if not (isinstance(rec, list) and len(rec) == 3 and isinstance(rec[0], int)):
            return
        same = [x for x in self.st["lossy"] if x[1:] == rec[1:]]
        if same and same[0][0] <= rec[0]:
            return
        self.st["lossy"] = ([x for x in self.st["lossy"] if x[1:] != rec[1:]] + [list(rec)])[-32:]


def over_cap(t: str, cap) -> float:
    if not cap:
        return 0.0
    b, ln = cap
    r = 0.0
    if b:
        r = max(r, len(t.encode("utf-8", "surrogateescape")) / (b * CAP_MARGIN))
    if ln:
        r = max(r, (t.count("\n") + 1) / (ln * CAP_MARGIN))
    return r


def _breaker_path(s: Settings) -> str:   # per server port: a dead server must not disable another one
    return os.path.join(s.state, f"breaker-{s.port}")


def breaker_open(s: Settings) -> bool:
    try:
        with open(_breaker_path(s)) as f:
            return time.time() < float(f.read().strip() or 0)
    except (OSError, ValueError):
        return False


def _trip(s: Settings) -> None:
    try:
        from recover import private_dir, private_open
        private_dir(s.state)
        with private_open(_breaker_path(s)) as f:
            f.write(str(time.time() + BREAK_S))
    except OSError:
        pass


def post_compress(port: int, payload: dict, timeout: float) -> dict:
    """POST /compress to the local v2_server, never via a proxy. Raises on any problem."""
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{int(port)}/compress", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with _open(req, timeout) as r:
        res = json.load(r)
    if not isinstance(res, dict) or not isinstance(res.get("text"), str):
        raise ValueError("bad v2 response")
    return res


def v2_call(text: str, intent: str, keep: float, s: Settings, want_keep: bool = False):
    """Compressed text, or (text, kept indices into text.splitlines() | None) with want_keep; None on failure."""
    payload = {"text": text, "intent": intent, "keep": keep, "want_keep": want_keep}
    if s.select:   # the preset's selector (v2_server honours it per request; an older server ignores the field)
        payload["select"] = s.select
        if s.alpha is not None:
            payload["alpha"] = s.alpha
    try:
        res = post_compress(s.port, payload, s.timeout)
    except Exception:
        _trip(s)   # timeout / server down / 413: skip the model for BREAK_S seconds (every later call would wait too)
        return None
    if not want_keep:
        return res["text"]
    k = res.get("keep")
    ok = (isinstance(k, list) and res.get("n_lines") == len(text.splitlines())
          and all(isinstance(i, int) and not isinstance(i, bool) for i in k))
    return res["text"], (k if ok else None)


def is_view(command: str) -> bool:
    """Output is file bytes: the same detector lossless.compress uses (lossless.view_kind)."""
    from lossless import is_view as _iv
    return _iv(command)


def process(text: str, command: str = "", *, tool: str = "", intent: str = "", is_error: bool = False,
            settings: Settings | None = None, session=None, label: str = "",
            keep_override: float | None = None, guard: Guard | None = None) -> tuple[str, dict]:
    """guard: the session's refetch Guard, already observe()d for this call; while it holds, the model is skipped
    (rec["v2"] = "guard") and a lossy result is noted on it."""
    s = settings or Settings.from_env()
    t0 = time.perf_counter()
    rec: dict = {"before": len(text), "harness": s.harness, "tool": tool, "cmd": (command or "")[:200],
                 "stage": "none", "v2": "off" if not s.model else "skipped"}
    if s.preset:
        rec["preset"] = s.preset
    model_on = s.model and not (guard is not None and guard.safe())
    out = text
    try:
        from lossless import compress, paths_on
        new, st = compress(text, command, paths=paths_on(s.target))
        rec["rules"] = st.get("rules", [])
        if new != text:
            rec["stage"] = "lossless"
        out = new
        if s.model and not model_on and not is_error and len(new) >= s.min_chars:
            rec["v2"] = "guard"   # refetch guard: this session runs safe for a while
        elif model_on and not is_error and len(new) >= s.min_chars:
            if not s.view_model and is_view(command):
                rec["v2"] = "view"   # lossless only, never holes in a file view
            elif breaker_open(s):
                rec["v2"] = "breaker"
            else:
                cap = HARNESS_CAPS.get((s.harness or "").lower())
                k = keep_override if keep_override is not None else s.keep
                for attempt in range(3):
                    r = v2_call(new, (intent or command or "")[-2000:].strip(), k, s, want_keep=s.recoverable)
                    if r is None:
                        rec["v2"] = "down"
                        break
                    if s.select:
                        rec["select"] = s.select
                    if s.recoverable:   # audit M1: no lossy text without its recovery file
                        from recover import keep_from_pieces, recoverable
                        got, kept = r
                        nl_keep = keep_from_pieces(new, kept) if kept is not None else None
                        if nl_keep is not None:      # current server: markers + file from the kept indices
                            got = recoverable(new, keep=nl_keep, state=s.state)
                        elif "omitted …]" in got:    # older server: map its relative markers (fails closed)
                            got = recoverable(new, compressed=got, state=s.state)
                        # else: a server without gap markers (custom / test double): nothing to annotate
                        if got is None:
                            rec["recover"], rec["v2"] = "failed", "recover_failed"
                            break
                    else:
                        got = r
                    over = over_cap(got, cap)
                    if over <= 1.0 or attempt == 2:
                        if len(got) < len(new):
                            out, rec["stage"], rec["v2"] = got, "model", "ok"
                        else:
                            rec["v2"] = "not_smaller"
                        if over > 1.0:
                            rec["cap"] = "still_over"
                        break
                    k = k / over * 0.95
                    rec["cap_keep"] = round(k, 4)
        if guard is not None and rec.get("v2") == "ok":   # before dedup, while the footer is still there
            g = guard.note(command, out)
            if g:
                rec["lossy"] = g
        if s.dedup and session is not None and len(out) >= getattr(session, "min_len", 1000):
            d, info = session.dedup(out)
            if d != out:
                rec["dedup"] = len(out) - len(d)
                out = d
    except Exception as e:  # noqa: BLE001 - fail open with the best text so far
        rec["error"] = repr(e)[:300]
    rec["after"] = len(out)
    rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out, rec


def _self_test() -> None:
    import tempfile
    from dedup import DedupSession
    with tempfile.TemporaryDirectory() as td:
        s = Settings(model=False, state=td, dedup=True)   # dedup is opt-in (default off)
        grep = "\n".join(f"src/app/views/user_views.py:{i}:    user = User.get({i})  # lookup {i}" for i in range(1, 80))
        sess = DedupSession()
        a, r1 = process(grep, "grep -rn User src", settings=s, session=sess)
        sess.register(a, "grep -rn User src")
        b, r2 = process(grep, "grep -rn User src", settings=s, session=sess)
        assert r1["stage"] == "lossless" and len(a) < len(grep), r1
        assert "dedup" in r2 and len(b) < 200, (r2, b[:200])   # the audit's failure case: a repeat now dedups
        s2 = Settings(model=True, port=1, timeout=0.5, state=td)  # server down -> breaker trips, stage-0 result
        c, r3 = process(grep, "grep -rn User src", settings=s2)
        assert r3["v2"] == "down" and c == a and breaker_open(s2), r3
        c2, r4 = process(grep, "grep -rn User src", settings=s2)
        assert r4["v2"] == "breaker", r4
        view = "\n".join(f"    x_{i} = compute({i})" for i in range(400))
        for cmd in ("sed -n '1,400p' src/app.py", "bash -lc \"cat src/app.py\"", "ls && head -400 src/app.py"):
            v, r5 = process(view, cmd, settings=Settings(model=True, port=1, timeout=0.5, state=td + "/v"))
            assert r5["v2"] == "view" and not breaker_open(Settings(port=1, state=td + "/v")), (cmd, r5)
        assert not is_view("pytest -q 2>&1 | tail -50") and not is_view("grep -rn x src")
        _self_test_presets()
        _self_test_guard(td)
    print("ctxprune_core self-test OK")


def _self_test_presets() -> None:
    keys = ("CTXPRUNE_PRESET", "CTXPRUNE_HARNESS", "CTXPRUNE_FORCE_LOSSY", "CTXPRUNE_KEEP", "CTXPRUNE_MIN_CHARS",
            "CTXPRUNE_NO_MODEL", "CTXPRUNE_SELECT", "CTXPRUNE_ALPHA", "CTXPRUNE_GUARD_TURNS", "CTXPRUNE_DEDUP")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        s = Settings.from_env()
        assert s.preset == "safe" and not s.model and not s.dedup and s.persisted == "skip", s   # the default
        for p in PRESETS:                                    # dedup is opt-in everywhere (Qwen3.8 probe)
            assert not resolve_preset(p)["dedup"] and not Settings.from_env(preset=p).dedup, p
        os.environ["CTXPRUNE_DEDUP"] = "1"
        assert Settings.from_env().dedup and Settings.from_env(preset="balanced").dedup
        os.environ.pop("CTXPRUNE_DEDUP")
        os.environ["CTXPRUNE_PRESET"] = "balanced"
        b = Settings.from_env()
        assert b.model and b.select == BALANCED_SELECT and b.keep == 0.5 and b.min_chars == 2000 and b.recoverable
        assert not b.view_model
        cc = Settings.from_env(harness="claude-code")
        assert cc.model and cc.keep == 0.6 and cc.persisted == "skip", cc
        for h in ("codex", "opencode", "pi", "dsh"):   # opt-out: balanced = safe there ...
            o = Settings.from_env(harness=h)
            assert not o.model and o.preset == "safe", (h, o)
            assert Settings.from_env(harness=h, force=True).model   # ... unless forced
        os.environ.update(CTXPRUNE_HARNESS="codex", CTXPRUNE_FORCE_LOSSY="1")
        assert Settings.from_env().model
        os.environ.pop("CTXPRUNE_FORCE_LOSSY")
        assert not Settings.from_env().model
        assert Settings.from_env(preset="aggressive").model   # aggressive is the user's explicit choice
        os.environ.update(CTXPRUNE_HARNESS="", CTXPRUNE_KEEP="0.4", CTXPRUNE_SELECT="smooth")   # env beats the table
        e = Settings.from_env()
        assert e.keep == 0.4 and e.select == "smooth", e
        assert Settings.from_env(keep=0.7, min_chars=None).keep == 0.7   # a flag beats env; None never clobbers
        os.environ["CTXPRUNE_NO_MODEL"] = "1"
        assert not Settings.from_env().model
        os.environ.pop("CTXPRUNE_NO_MODEL")
        os.environ["CTXPRUNE_PRESET"] = "nonsense"
        assert Settings.from_env().preset == "safe"      # unknown name: fail safe
        assert {r["preset"] for r in preset_table()} == {"safe", "balanced", "aggressive"}
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def _self_test_guard(td: str) -> None:
    g = Guard({}, turns=4)
    ftr = f"[ctxprune: kept 2 of 9 lines; omitted ranges are lines A–B of {td}/out/0123456789ab.txt (e.g. sed -n 'A,Bp' x)]"
    assert recover_file_of("a\n" + ftr) == "0123456789ab.txt" and recover_file_of("a\nb") == ""
    assert cmd_key("pytest  -q 2>&1 | tail -50") == cmd_key("pytest -q") == "pytest -q"
    g.observe("c1", "pytest -q")
    assert g.note("pytest -q", "x\n" + ftr) == [1, "pytest -q", "0123456789ab.txt"] and not g.safe()
    g.observe("c1", "pytest -q")                        # the same call seen again (transcript): not counted
    assert g.st["t"] == 1
    assert not g.observe("c2", "ls src") and not g.safe()
    assert g.observe("c3", "pytest -q  | tail -80") and g.safe()   # re-run within 3 calls -> trips (this call safe)
    for i in range(3):
        g.observe(f"d{i}", "ls")
    assert g.safe() and g.st["t"] == 6
    g.observe("d3", "ls")
    assert not g.safe() and g.st["trips"] == 1         # held for 4 calls (3..6)
    g.observe("e1", "make test")
    g.note("make test", ftr.replace("0123456789ab", "ffffffffffff"))
    assert g.observe("e2", "Read /x", f"{td}/out/ffffffffffff.txt") and g.safe()   # read of the recovery file
    g2 = Guard({}, turns=4)
    g2.observe("a", "make")
    g2.note("make", ftr)
    for c in ("b", "c", "d"):
        g2.observe(c, "ls")
    assert not g2.observe("e", "make") and not g2.safe()   # 4 calls later: outside the window
    assert not Guard({}, turns=0).observe("x", "make")


if __name__ == "__main__":
    _self_test()
