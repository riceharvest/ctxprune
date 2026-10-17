#!/usr/bin/env python3
"""ctxprune proxy: an HTTP proxy between any agent harness and its LLM API that compresses tool-result text in
requests, for harnesses without a usable post-tool hook (Hermes Agent, Pi, OpenCode, Codex CLI, ...). Stdlib only.

  harness --(base URL http://127.0.0.1:PORT[/v1])--> ctxprune_proxy --> --upstream (same path appended verbatim)

Rewritten (POST, JSON body, no Content-Encoding; path suffix after stripping the query):
  /chat/completions  OpenAI chat: role "tool" messages (content string or text parts), matched to the assistant
                     tool_calls[].id; legacy role "function" matched to the preceding assistant function_call.
  /responses         OpenAI Responses: input items function_call_output / custom_tool_call_output (output string or
                     input_text parts), matched by call_id to function_call / custom_tool_call / local_shell_call.
  /messages          Anthropic: user content blocks tool_result (content string or text blocks), matched to
  (+ /count_tokens)  tool_use.id; count_tokens gets the same rewrite so the harness's context estimate matches.
Everything else (other paths, methods, non-JSON or encoded bodies) is forwarded byte-for-byte. Responses (incl. SSE
streams) are passed through unchanged. A request where nothing changed is forwarded with its original bytes.

Per tool-result text:
  1. lossless.compress(text, command): command = the matching call's shell command when derivable ("command"/"cmd"/
     "script" args; ["bash","-lc",X] -> X), a stand-in `rg` line for a Grep tool, else None.
  2. if the result is >= --min-chars: the v2 line model via v2_server.py (--v2-port) at --keep, intent = last assistant
     text up to that tool call (last 1500 chars) + "\n" + the call's command or raw args (500), as v2_hook.py.
  Fails open everywhere: model down/slow -> lossless result; any other error -> the original request bytes.
Not touched (decisions):
  - file read/edit/write tools (Read, read_file, view, edit, write, apply_patch, str_replace_editor, ...; --skip-tools
    regex): the agent edits against those exact bytes (same rule as the hook, PLAN §3.0). Images, non-tool messages.
  - error results (Anthropic is_error: true): kept, lossless stage only (never the model; PLAN §2.65 D6).
Cache safety (PLAN §2.65 D5): harnesses resend the whole history every turn, so each tool result must map to the same
bytes on every turn. Content-addressed cache: key = sha256(version, settings, tool name, args, is_error, original text) ->
compressed text, in memory and (--cache-file) appended to a JSONL that is reloaded at startup. The first bytes
forwarded are canonical: a fail-open (model down) result is cached too, so a model coming back later can't change
an old result. Intent is not in the key (a harness trimming old assistant text can't change old bytes). Every result
is treated the same regardless of its position (no "keep the newest N raw"). For a cold cache (restart without
--cache-file) run v2_server with --round so recomputation is deterministic.

Note (--note on|min, default off): appends the constant ctxprune_note.NOTE / NOTE_MIN to the system prompt of every
rewritten-API request, including requests with no tool result yet (so turn 1 and turn N have the same prefix):
  /messages     `system` string -> + "\n\n" + note; block list -> + {"type":"text","text":note} AFTER the existing
                blocks, no cache_control (Claude Code already uses its breakpoints; the blocks before keep their bytes,
                the constant block is covered by the next message breakpoint); absent -> note.
  /chat/completions  first system/developer message: string -> + "\n\n" + note, parts -> + a text part; none ->
                a system message inserted at index 0.
  /responses    `instructions` -> + "\n\n" + note; absent -> note (instructions are not carried over by
                previous_response_id, so every request gets it).
Skipped when the note text is already there (e.g. the Claude Code SessionStart hook also runs).

Hermes read_file (--view-safe on, default): a skipped `read_file` result that is Hermes' JSON wrapper
({"content": "N|code...", "total_lines": ...}) gets view_safe.wrapper_unescape: the content field unescaped under a
header that keeps the sibling keys, plus the lossless2 `numbered` gutter rule only, so the code bytes stay exact.
Failures (error / success false / is_binary / Anthropic is_error) and every other skipped tool stay untouched.

Security (audit 2026-10-17): request paths must start with exactly one "/" (400 otherwise: the upstream URL is
upstream + path); bodies over --max-body-mb get 413; the log stores the path without its query string. No auth:
the proxy binds 127.0.0.1 only and trusts every local user. On a shared host another user could bind the port
first and receive the harness's API key; there, start the proxy before the harness and check the listener is yours
(`ss -ltnp 'sport = :8787'` shows this PID), or pick a private port. (A Unix-socket / token-file mode needs harness
support for socket base URLs and is not built; the rented boxes are single-user.)

Kill switch (live canary finding 0): CTXPRUNE_OFF=1 in the proxy's env, or the file <state>/OFF (--off-file; checked
per request, so `touch ~/.cache/ctxprune/OFF` disables a running proxy and `rm` re-enables it) -> pure pass-through:
original bytes, no note. Stacked behind a native integration (canary finding 2): a tool result whose exact bytes the
hook / plugin recorded in emitted.py's registry (same --state / CTXPRUNE_STATE) is forwarded untouched, so its real
markers are not escaped as if the tool had printed them; anything else is processed (and escaped) as usual.
JSON wrappers (canary finding 1): a JSON object around multi-line text is unwrapped, the full pipeline runs on the
text, and the result (header line with the sibling keys + text) is used when it is shorter than the original.

Endpoints: GET /ctxprune/stats (counts, chars, cache hits; answered locally). Log: --log JSONL, one line per rewritten
request {ts, api, path, stream, results:[{id, tool, before, after, stage, cache, ms}], bytes_in, bytes_out, ms}.

Usage: python3 ctxprune_proxy.py --upstream https://api.openai.com [--port 8787] [--keep 0.5] [--min-chars 6000]
         [--v2-port 8765] [--no-model] [--note on|min|off] [--cache-file ~/.cache/ctxprune/proxy-cache.jsonl] [--log proxy.jsonl]
Then point the harness at it, e.g. OPENAI_BASE_URL=http://127.0.0.1:8787/v1, ANTHROPIC_BASE_URL=http://127.0.0.1:8787.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "proxy-3"  # proxy-3: JSON wrappers unwrapped + full pipeline (canary finding 1); proxy-2: H2 marker escaping; bump when lossless/v2 output for the same input changes
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding", "proxy-connection"}
# (?!skill_): Hermes skill_view / skill_read are skill docs, not file views (the `_view` suffix used to match them)
SKIP_TOOLS = (r"(?i)^(?!skill_)(?:.*[_.:/-])?(read|read_file|read_text_file|read_multiple_files|readfile|view|view_file|view_image|"
              r"open_file|edit|edit_file|multiedit|multi_edit|write|write_file|create_file|patch|apply_patch|"
              r"notebookedit|notebookread|str_replace_editor|str_replace_based_edit_tool|fs_read|fs_write)$")
LOSSLESS_ONLY = re.compile(r"(?i)^skill_view$")   # reached, but lossless stage only (as the Hermes plugin)
SHELLS = {"bash", "sh", "zsh", "dash", "fish", "/bin/bash", "/bin/sh", "/bin/zsh", "/usr/bin/bash", "/usr/bin/zsh"}


# ---------------------------------------------------------------- settings, cache, stats

class Cfg:
    upstream = "http://127.0.0.1:8000"
    keep: float | None = None       # None = the preset's (ctxprune_core.PRESETS)
    min_chars: int | None = None
    preset: str | None = None       # None = CTXPRUNE_PRESET, else safe
    force_lossy = False             # balanced on an opted-out harness (codex, ...) still runs the model
    harness = ""                    # PRESET_HARNESS key (codex, hermes, ...); "" = CTXPRUNE_HARNESS
    settings = None                 # ctxprune_core.Settings resolved by configure()
    settings_sig: tuple = ()
    v2_port = 8765
    v2_timeout = 20.0
    no_model = False
    skip_tools = re.compile(SKIP_TOOLS)
    timeout = 900.0
    log_path: str | None = None
    dedup = False             # cross-turn dedup is opt-in (CTXPRUNE_DEDUP=1 / --dedup on): Qwen3.8 comprehension probe
    recoverable = True
    note: str | None = None   # ctxprune_note text appended to the system prompt (None = off)
    view_safe = True          # Hermes read_file JSON wrapper: unescape + numbered gutter only (view_safe.py)
    max_body = 64 << 20       # request bodies above this get 413 (audit L2)
    state = ""                # shared with the native integrations (emitted.py registry, OFF file); "" = env/default
    off_file = ""             # this file existing = pass-through (runtime kill switch); "" = <state>/OFF


class Cache:
    """sha256 key -> compressed text (None = forwarded unchanged). First value stored wins; optional JSONL on disk."""

    def __init__(self, path: str | None = None):
        self.path, self.mem, self.lock = path, {}, threading.Lock()
        self.key_locks: dict[str, threading.Lock] = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        self.mem.setdefault(r["k"], r["t"])
                    except (ValueError, KeyError, TypeError):
                        continue  # torn last line etc.

    def key_lock(self, k: str) -> threading.Lock:
        with self.lock:
            return self.key_locks.setdefault(k, threading.Lock())

    def get(self, k: str):
        with self.lock:
            return (True, self.mem[k]) if k in self.mem else (False, None)

    def put(self, k: str, t: str | None) -> str | None:
        with self.lock:
            if k in self.mem:
                return self.mem[k]
            self.mem[k] = t
            self.key_locks.pop(k, None)
            if self.path:
                try:
                    d = os.path.dirname(self.path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"k": k, "t": t}, ensure_ascii=False) + "\n")
                        f.flush()
                except (OSError, UnicodeEncodeError):
                    pass
            return t


CACHE = Cache()
STATS_LOCK = threading.Lock()
STATS: dict = {}


def reset_stats() -> None:
    with STATS_LOCK:
        STATS.clear()
        STATS.update(requests=0, rewritten_requests=0, passthrough_requests=0, fail_open=0, tool_results=0,
                     changed_results=0, skipped_results=0, cache_hits=0, cache_misses=0, model_calls=0,
                     model_failures=0, chars_before=0, chars_after=0, unique_chars_before=0, unique_chars_after=0)


reset_stats()


def bump(**kv) -> None:
    with STATS_LOCK:
        for k, v in kv.items():
            STATS[k] = STATS.get(k, 0) + v


# ---------------------------------------------------------------- compression of one text

_model_down_until = [0.0]


def v2_compress(text: str, intent: str) -> str | None:
    if time.time() < _model_down_until[0]:  # circuit breaker: don't pay a timeout per result while it's down
        return None
    from v2_hook import v2_compress as call
    out = call(text, intent, _settings().keep, Cfg.v2_port, Cfg.v2_timeout)
    if out is None:
        _model_down_until[0] = time.time() + 30
    return out


def cache_key(tool: str, args: str, text: str, is_error: bool = False, guard_safe: bool = False) -> str:
    """guard_safe: the refetch guard holds at this result. It is a function of the history BEFORE the result (the
    request is walked in order), so a resent old result gets the same bit and key; without it a re-run with the same
    output would get the cached lossy bytes back, which is exactly what the agent re-ran to get past."""
    s = _settings()
    from lossless import paths_on
    settings = [VERSION, s.keep, s.min_chars, not s.model, s.select, s.alpha, s.preset] + (["guard"] if guard_safe else []) \
        + ([] if paths_on(s.target) else ["no-paths"])
    blob = json.dumps([settings, tool, args, is_error, text], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


_DD = threading.local()   # per-request DedupSession (PLAN §2.656 E2), None when dedup is off; .g = the refetch guard


def _sig() -> tuple:
    return (Cfg.preset, Cfg.force_lossy, Cfg.harness, Cfg.keep, Cfg.min_chars, Cfg.v2_port, Cfg.v2_timeout,
            Cfg.no_model, Cfg.recoverable, Cfg.upstream)


def _settings():
    """ctxprune_core.Settings for the current Cfg (re-resolved when a Cfg field changed after configure())."""
    if Cfg.settings is None or Cfg.settings_sig != _sig():
        _resolve()
    return Cfg.settings


def _resolve() -> None:
    from ctxprune_core import Settings
    Cfg.settings_sig = _sig()
    Cfg.settings = Settings.from_env(preset=Cfg.preset, force=Cfg.force_lossy or None, harness=Cfg.harness or None,
                                     keep=Cfg.keep, min_chars=Cfg.min_chars, port=Cfg.v2_port, timeout=Cfg.v2_timeout,
                                     model=False if Cfg.no_model else None, recoverable=Cfg.recoverable, dedup=False,
                                     target=Cfg.upstream or None)   # local upstream -> no `paths` (lossless.paths_on)


def _g_observe(call_id, name, raw) -> None:
    """Refetch guard (ctxprune_core.Guard), rebuilt from each request's own history in message order: deterministic
    in the history, so a recomputed (cold-cache) old result gets the same decision."""
    g = getattr(_DD, "g", None)
    if g is None:
        return
    args, key = parse_args(raw)
    name = str(name or "")
    g.observe(str(call_id or ""), command_of(name, args) or f"{name} {key}", key)
    _DD.call_t[str(call_id or "")] = g.st["t"]


def _g_note(slot, text: str) -> None:
    g = getattr(_DD, "g", None)
    if g is not None:
        g.note(slot.command or f"{slot.tool} {slot.args_key}", text, t=_DD.call_t.get(str(slot.call_id or "")))


def _dd(text: str, label: str) -> str:
    """Cross-turn dedup of one (already compressed) result against the earlier results OF THIS REQUEST, then
    register what the model will see. Deterministic in the ordered history -> byte-stable across turns. Results the
    proxy must not touch are registered via _dd_register so later outputs can still point at them."""
    s = getattr(_DD, "s", None)
    if s is None:
        return text
    if len(text) >= getattr(s, "min_len", 1000):   # dedup P2: no 1000-char gate (whole-output marker, short runs)
        text, _ = s.dedup(text)
    s.register(text, label)
    return text


def _dd_inputs(name, raw_args) -> None:
    """Register a tool CALL's input (Write content, Edit new_string, multi-line Bash) so later outputs can point at
    it as `input #k` (dedup.call_inputs decides which parts). Called in message order, before the call's result."""
    s = getattr(_DD, "s", None)
    if s is None:
        return
    from dedup import call_inputs
    args, _ = parse_args(raw_args)
    for lab, t in call_inputs(str(name or ""), args):
        s.register_input(t, lab)


def _dd_native(text: str, label: str) -> str:
    """A native integration's text: registered for later dedup references, never deduped itself (forwarded as is)."""
    _dd_register(text, label)
    return text


def _dd_register(content, label: str) -> None:
    s = getattr(_DD, "s", None)
    if s is None:
        return
    t = _texts(content) if not isinstance(content, str) else content
    if t:
        s.register(t, label)


PI_FOOTER = re.compile(r"\n\[ctxprune: compressed from \d+ chars; the full output is in [^\n\]]+\]$")


def native(text: str) -> bool:
    """Already rewritten by a native integration (hook / plugin) of this install: its exact bytes are in the emitted.py
    registry (Pi appends its own footer line after the registered text). Forged text can't get in there, so this never
    lets marker-shaped tool output through unescaped."""
    from emitted import seen
    if seen(text, _state()):
        return True
    m = PI_FOOTER.search(text)
    return bool(m) and seen(text[:m.start()], _state())


def _process(text: str, slot: "Slot") -> tuple[str, dict]:
    """The pipeline for one result. A JSON object wrapping multi-line text (Hermes terminal {"output", "exit_code",
    ...}, MCP JSON) is unwrapped first and the WHOLE pipeline runs on the inner text, judged by the final size (canary
    finding 1: json_unescape alone was only taken when it was shorter by itself, so grep inside a wrapper stayed raw and
    forged markers inside it stayed unescaped). The header line keeps the sibling keys."""
    from ctxprune_core import process
    from view_safe import unwrap
    w = unwrap(text)
    if w is not None:
        head, body, failed = w
        if native(body):
            new, r = body, {"stage": "native"}
        else:
            new, r = process(body, slot.command or "", tool=slot.tool, intent=slot.intent(),
                             is_error=slot.is_error or failed or slot.lossless_only, settings=_settings(),
                             guard=getattr(_DD, "g", None))
        cand = head + "\n" + new
        if len(cand) < len(text):
            r = {**r, "stage": "wrapped_" + str(r.get("stage", "none"))}
            return cand, r
    return process(text, slot.command or "", tool=slot.tool, intent=slot.intent(),
                   is_error=slot.is_error or slot.lossless_only, settings=_settings(), guard=getattr(_DD, "g", None))


def compress_text(text: str, slot: "Slot") -> tuple[str, dict]:
    """-> (text to forward, log record). Cached by content; the first result computed for a key is reused forever.
    Text a native integration already produced is forwarded untouched (and not cached: the registry decides)."""
    t0 = time.perf_counter()
    rec = {"id": slot.call_id, "tool": slot.tool, "before": len(text)}
    if native(text):
        rec.update(stage="native", after=len(text), cache="none", ms=round((time.perf_counter() - t0) * 1000, 1))
        bump(tool_results=1, native_results=1, chars_before=len(text), chars_after=len(text))
        return text, rec
    g = getattr(_DD, "g", None)
    k = cache_key(slot.tool, slot.args_key, text, slot.is_error, g is not None and g.safe())
    hit, val = CACHE.get(k)
    if not hit:
        with CACHE.key_lock(k):  # concurrent requests with the same result compute it once
            hit, val = CACHE.get(k)
            if not hit:
                new, r = _process(text, slot)   # the one pipeline (lossless -> model -> recover), unwrapped
                stage = "error" if "error" in r else r["stage"]
                if r.get("v2") in ("ok", "not_smaller", "down", "breaker"):
                    bump(model_calls=1)
                if r.get("v2") in ("down", "breaker"):
                    bump(model_failures=1)
                val = CACHE.put(k, None if new == text else new)
                rec["stage"] = stage
                bump(cache_misses=1, unique_chars_before=len(text),
                     unique_chars_after=len(text if val is None else val))
    if hit:
        rec["stage"] = "cached"
        bump(cache_hits=1)
    out = text if val is None else val
    rec.update(after=len(out), cache="hit" if hit else "miss", ms=round((time.perf_counter() - t0) * 1000, 1))
    bump(tool_results=1, changed_results=int(out != text), chars_before=len(text), chars_after=len(out))
    return out, rec


# ---------------------------------------------------------------- tool calls, intent

def parse_args(raw) -> tuple[dict | None, str]:
    """-> (args dict or None, canonical args string for the cache key / intent)."""
    if isinstance(raw, dict):
        return raw, json.dumps(raw, ensure_ascii=False, sort_keys=True)
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return d, raw
        except ValueError:
            pass
        return None, raw
    if raw is None:
        return None, ""
    return None, json.dumps(raw, ensure_ascii=False, sort_keys=True)


def command_of(tool: str, args: dict | None) -> str | None:
    if not isinstance(args, dict):
        return None
    if tool == "Grep" and "pattern" in args:
        from lossless_hook import grep_command
        return grep_command(args)
    for k in ("command", "cmd", "script"):
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            if len(v) >= 3 and v[0] in SHELLS and v[1] in ("-c", "-lc", "-ic", "-lic"):
                return v[2]
            return shlex.join(v)
        if isinstance(v, dict) and isinstance(v.get("command"), list):  # local_shell_call action
            return command_of(tool, v)
    return None


class Slot:
    """One tool result: where its text lives and what produced it."""

    def __init__(self, call_id, tool, raw_args, is_error, intent_text):
        self.call_id, self.tool, self.is_error = call_id, tool or "", bool(is_error)
        args, self.args_key = parse_args(raw_args)
        self.command = command_of(self.tool, args)
        self._intent_text = intent_text

    def intent(self) -> str:
        call = (self.command or self.args_key or "")[:500]
        return ((self._intent_text or "")[-1500:] + "\n" + call).strip()

    @property
    def skipped(self) -> bool:
        return bool(Cfg.skip_tools.search(self.tool))

    @property
    def lossless_only(self) -> bool:
        return bool(LOSSLESS_ONLY.search(self.tool))


def _texts(content) -> str:
    """Text of a message content (string, or parts with text)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and isinstance(p.get("text"), str) and
                         p.get("type") in ("text", "output_text", "input_text", None)).strip()
    return ""


def _rewrite_parts(content, slot: Slot, recs: list, part_types=("text",)):
    """Compress a string content or each text part of a parts list; returns the (maybe new) content."""
    label = (slot.command or slot.tool or "")[:200]
    if isinstance(content, str):
        if not content:
            return content
        new, rec = compress_text(content, slot)
        _g_note(slot, new)
        new = _dd_native(new, label) if rec.get("stage") == "native" else _dd(new, label)
        rec["after"] = len(new)
        recs.append(rec)
        return new
    if isinstance(content, list):
        out, changed = [], False
        for p in content:
            if isinstance(p, dict) and p.get("type") in part_types and isinstance(p.get("text"), str) and p["text"]:
                new, rec = compress_text(p["text"], slot)
                _g_note(slot, new)
                new = _dd_native(new, label) if rec.get("stage") == "native" else _dd(new, label)
                rec["after"] = len(new)
                recs.append(rec)
                if new != p["text"]:
                    p = {**p, "text": new}
                    changed = True
            out.append(p)
        return out if changed else content
    return content


def _view_safe(content, slot: Slot, recs: list):
    """A skipped (file view) result. Hermes `read_file` (exact name) whose result is its JSON wrapper gets the
    view-safe lossless form (view_safe.py: JSON unescape + the `numbered` gutter rule; code bytes exact); every other
    skipped result is forwarded untouched. Either way it is registered for dedup as the model sees it."""
    label = (slot.command or slot.tool or "")[:200]
    new = content
    if Cfg.view_safe and slot.tool == "read_file" and not slot.is_error and isinstance(content, str):
        from view_safe import wrapper_unescape
        t0 = time.perf_counter()
        out = wrapper_unescape(content)   # pure and deterministic: no cache needed for byte stability
        if out is not None:
            new = out
            recs.append({"id": slot.call_id, "tool": slot.tool, "before": len(content), "after": len(out),
                         "stage": "view_safe", "cache": "none", "ms": round((time.perf_counter() - t0) * 1000, 1)})
            bump(tool_results=1, changed_results=1, chars_before=len(content), chars_after=len(out))
    if new is content:
        bump(skipped_results=1)
    _dd_register(new, label)
    return new


# ---------------------------------------------------------------- per-API walkers (mutate body in place)

def walk_chat(body: dict, recs: list) -> None:
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return
    calls: dict = {}
    last_fn = None  # legacy function_call: (name, arguments, intent)
    last_text = ""
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "assistant":
            t = _texts(m.get("content"))
            if t:
                last_text = t
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    calls[tc.get("id")] = (fn.get("name"), fn.get("arguments"), last_text)
                    _dd_inputs(fn.get("name"), fn.get("arguments"))
                    _g_observe(tc.get("id"), fn.get("name"), fn.get("arguments"))
            fc = m.get("function_call")
            if isinstance(fc, dict):
                last_fn = (fc.get("name"), fc.get("arguments"), last_text)
        elif role in ("tool", "function"):
            if role == "tool":
                name, raw, it = calls.get(m.get("tool_call_id"), (m.get("name"), None, last_text))
                cid = m.get("tool_call_id")
            else:
                name, raw, it = last_fn if last_fn and last_fn[0] == m.get("name") else (m.get("name"), None, last_text)
                cid = m.get("name")
            slot = Slot(cid, name, raw, False, it)
            if slot.skipped:
                new = _view_safe(m.get("content"), slot, recs)
                if new is not m.get("content"):
                    msgs[i] = {**m, "content": new}
                continue
            new = _rewrite_parts(m.get("content"), slot, recs)
            if new is not m.get("content"):
                msgs[i] = {**m, "content": new}


RESP_CALLS = ("function_call", "custom_tool_call", "local_shell_call")
RESP_OUTS = ("function_call_output", "custom_tool_call_output", "local_shell_call_output")


def walk_responses(body: dict, recs: list) -> None:
    items = body.get("input")
    if not isinstance(items, list):
        return
    calls: dict = {}
    last_text = ""
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        typ = it.get("type")
        if (typ == "message" or (typ is None and "role" in it)) and it.get("role") == "assistant":
            t = _texts(it.get("content"))
            if t:
                last_text = t
        elif typ in RESP_CALLS:
            raw = it.get("arguments") if typ == "function_call" else \
                it.get("input") if typ == "custom_tool_call" else it.get("action")
            name = it.get("name") or ("shell" if typ == "local_shell_call" else "")
            if typ == "custom_tool_call" and isinstance(raw, str):
                raw = json.dumps({"input": raw})  # free-form tool input, not shell args
            calls[it.get("call_id") or it.get("id")] = (name, raw, last_text)
            _dd_inputs(name, raw)
            _g_observe(it.get("call_id") or it.get("id"), name, raw)
        elif typ in RESP_OUTS:
            cid = it.get("call_id") or it.get("id")
            name, raw, itx = calls.get(cid, ("", None, last_text))  # previous_response_id: call not in this request
            slot = Slot(cid, name, raw, False, itx)
            if slot.skipped:
                new = _view_safe(it.get("output"), slot, recs)
                if new is not it.get("output"):
                    items[i] = {**it, "output": new}
                continue
            new = _rewrite_parts(it.get("output"), slot, recs, ("input_text", "output_text", "text"))
            if new is not it.get("output"):
                items[i] = {**it, "output": new}


def walk_anthropic(body: dict, recs: list) -> None:
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return
    calls: dict = {}
    last_text = ""
    for m in msgs:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if m.get("role") == "assistant":
            if isinstance(content, str) and content.strip():
                last_text = content
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    last_text = b["text"]
                elif b.get("type") in ("tool_use", "server_tool_use"):
                    calls[b.get("id")] = (b.get("name"), b.get("input"), last_text)
                    _dd_inputs(b.get("name"), b.get("input"))
                    _g_observe(b.get("id"), b.get("name"), b.get("input"))
        elif m.get("role") == "user" and isinstance(content, list):
            for j, b in enumerate(content):
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                name, raw, itx = calls.get(b.get("tool_use_id"), ("", None, last_text))
                slot = Slot(b.get("tool_use_id"), name, raw, b.get("is_error"), itx)
                if slot.skipped:
                    new = _view_safe(b.get("content"), slot, recs)
                    if new is not b.get("content"):
                        content[j] = {**b, "content": new}
                    continue
                new = _rewrite_parts(b.get("content"), slot, recs)
                if new is not b.get("content"):
                    content[j] = {**b, "content": new}


# ---------------------------------------------------------------- system-prompt note (byte-constant, cache-safe)

def _append_note(content, note: str):
    """string -> string + blank line + note; parts list -> list + one text part; else None (leave alone)."""
    if isinstance(content, str):
        return content + "\n\n" + note if content else note
    if isinstance(content, list):
        typ = "text"
        for p in content:   # match the part type the harness uses (Responses-style input_text in chat is rare)
            if isinstance(p, dict) and p.get("type") in ("text", "input_text"):
                typ = p["type"]
        return content + [{"type": typ, "text": note}]
    return None


def add_note(api: str, body: dict, note: str) -> bool:
    """Append the note to the request's system prompt in place; True when the body changed."""
    if api == "anthropic":
        msgs = body.get("messages")
        # Claude Code SessionStart hook already added it: as a <system-reminder> in the first user message, or (live
        # CC 2.1.302, canary 2026-10-17) as a role "system" message after it. Text parts only, never tool results,
        # so a tool output quoting the note can't flip the decision between turns.
        for m in msgs if isinstance(msgs, list) else []:
            if isinstance(m, dict) and m.get("role") in ("user", "system") and note in _texts(m.get("content")):
                return False
        sysp = body.get("system")
        if sysp is None or sysp == "" or sysp == []:
            body["system"] = note
            return True
        if note in (sysp if isinstance(sysp, str) else _texts(sysp)):
            return False
        new = _append_note(sysp, note)
        if new is None:
            return False
        body["system"] = new
        return True
    if api == "responses":
        ins = body.get("instructions")
        if not ins:
            body["instructions"] = note
            return True
        if not isinstance(ins, str) or note in ins:
            return False
        body["instructions"] = ins + "\n\n" + note
        return True
    if api == "chat":
        msgs = body.get("messages")
        if not isinstance(msgs, list):
            return False
        for i, m in enumerate(msgs):
            if isinstance(m, dict) and m.get("role") in ("system", "developer"):
                c = m.get("content")
                if note in (c if isinstance(c, str) else _texts(c)):
                    return False
                new = _append_note(c, note)
                if new is None:
                    return False
                msgs[i] = {**m, "content": new}
                return True
        msgs.insert(0, {"role": "system", "content": note})
        return True
    return False


def _state() -> str:
    return Cfg.state or os.environ.get("CTXPRUNE_STATE") or os.path.expanduser("~/.cache/ctxprune")


def off() -> bool:
    """Kill switch, checked per request: CTXPRUNE_OFF=1 in the proxy's env, or the OFF file (default
    <state>/OFF, e.g. ~/.cache/ctxprune/OFF) existing -> pure pass-through (no rewrite, no note). `touch` / `rm` the
    file to toggle a running proxy. Toggling mid-session changes old tool results once (one prompt-cache miss)."""
    if os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes"):
        return True
    try:
        return os.path.exists(Cfg.off_file or os.path.join(_state(), "OFF"))
    except Exception:
        return False


def api_of(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    p = path.split("?", 1)[0].rstrip("/")
    if p.endswith("/chat/completions"):
        return "chat"
    if p.endswith("/responses"):
        return "responses"
    if p.endswith("/messages") or p.endswith("/messages/count_tokens"):
        return "anthropic"
    return None


WALKERS = {"chat": walk_chat, "responses": walk_responses, "anthropic": walk_anthropic}


def rewrite(api: str, raw: bytes) -> tuple[bytes, list, bool]:
    """-> (body to forward, per-result records, stream flag). Original bytes when nothing changed. Raises on bad JSON."""
    body = json.loads(raw)
    if not isinstance(body, dict):
        return raw, [], False
    recs: list = []
    if Cfg.dedup:
        from dedup import DedupSession
        _DD.s = DedupSession()
    st = _settings()
    if st.model and st.guard_turns > 0:
        from ctxprune_core import Guard
        _DD.g, _DD.call_t = Guard({}, turns=st.guard_turns), {}
    try:
        WALKERS[api](body, recs)
    finally:
        _DD.s = _DD.g = None
    noted = bool(Cfg.note) and add_note(api, body, Cfg.note)
    stream = bool(body.get("stream"))
    if not noted and not any(r["after"] != r["before"] for r in recs):
        return raw, recs, stream
    try:
        out = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates
        out = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode()
    return out, recs, stream


# ---------------------------------------------------------------- HTTP

_log_lock = threading.Lock()


def write_log(rec: dict) -> None:
    if not Cfg.log_path:
        return
    try:
        with _log_lock, open(Cfg.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def forward_headers(items) -> dict[str, str]:
    out = {k: v for k, v in items if k.lower() not in HOP}
    out["Accept-Encoding"] = "identity"
    return out


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def log_message(self, *a):
        pass

    def _read_body(self) -> bytes | None:
        """The request body, or None when it is over Cfg.max_body (the caller answers 413 and closes)."""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return out
                if len(out) + size > Cfg.max_body:
                    return None
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        if n > Cfg.max_body:
            return None
        return self.rfile.read(n) if n else b""

    def _json(self, code: int, obj) -> None:
        b = json.dumps(obj).encode()
        self.send_response_only(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _handle(self):
        t0 = time.perf_counter()
        # audit L1: the upstream URL is upstream + path, so the path must start with exactly one "/" (a path like
        # `@evil.host/x` or `//evil.host` would change the host the request, with its API key, goes to)
        if not self.path.startswith("/") or self.path.startswith("//"):
            self.close_connection = True
            return self._json(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                              "message": "ctxprune proxy: bad request path"}})
        body = self._read_body()
        if body is None:   # audit L2
            self.close_connection = True
            return self._json(413, {"type": "error", "error": {"type": "request_too_large",
                                                              "message": "ctxprune proxy: request body too large"}})
        if self.command == "GET" and self.path.split("?")[0] == "/ctxprune/stats":
            with STATS_LOCK:
                snap = dict(STATS)
            snap["cache_entries"] = len(CACHE.mem)
            snap["off"] = off()
            return self._json(200, snap)
        bump(requests=1)
        api = api_of(self.command, self.path)
        stream, rec = False, None
        log_path = self.path.split("?", 1)[0]   # audit L3: query strings (may carry keys) never reach the log
        if api and off():
            bump(off_requests=1)
            rec = {"ts": time.time(), "api": api, "path": log_path, "off": True}
        elif api and body and not self.headers.get("Content-Encoding"):
            try:
                new, recs, stream = rewrite(api, body)
                rec = {"ts": time.time(), "api": api, "path": log_path, "stream": stream, "results": recs,
                       "bytes_in": len(body), "bytes_out": len(new)}
                if new is not body:
                    bump(rewritten_requests=1)
                body = new
            except Exception as e:  # fail open: original bytes
                bump(fail_open=1)
                rec = {"ts": time.time(), "api": api, "path": log_path, "error": repr(e)[:300]}
        else:
            bump(passthrough_requests=1)
        if rec is not None:
            rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            write_log(rec)
        req = urllib.request.Request(Cfg.upstream.rstrip("/") + self.path, data=body if body else None,
                                     method=self.command, headers=forward_headers(self.headers.items()))
        try:
            resp = self.opener.open(req, timeout=Cfg.timeout)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:  # upstream down / timeout before headers
            return self._json(502, {"type": "error", "error": {"type": "api_error", "message": f"ctxprune proxy: {e}"}})
        status = resp.status if hasattr(resp, "status") else resp.code
        is_sse = "text/event-stream" in (resp.headers.get("Content-Type") or "")
        self.send_response_only(status, getattr(resp, "reason", None))
        for k, v in resp.headers.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        try:
            if is_sse:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            try:
                resp.close()
            except Exception:
                pass

    do_POST = do_GET = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _handle


def configure(upstream: str, keep: float | None = None, min_chars: int | None = None, v2_port: int = 8765,
              no_model: bool = False, cache_file: str | None = None, log: str | None = None,
              skip_tools: str = SKIP_TOOLS, v2_timeout: float = 20.0, timeout: float = 900.0, dedup: bool | None = None,
              note: str = "off", view_safe: bool = True, max_body: int = 64 << 20, preset: str | None = None,
              force_lossy: bool = False, harness: str = "", state: str = "", off_file: str = "") -> None:
    """keep / min_chars None = the preset's; preset None = CTXPRUNE_PRESET, else safe (ctxprune_core.PRESETS)."""
    global CACHE
    from ctxprune_note import note_for
    if dedup is None:   # opt-in: CTXPRUNE_DEDUP=1 (ctxprune_core.dedup_on)
        from ctxprune_core import dedup_on
        dedup = dedup_on(False)
    Cfg.dedup, Cfg.note, Cfg.view_safe, Cfg.max_body = dedup, note_for(note), view_safe, max_body
    Cfg.upstream, Cfg.keep, Cfg.min_chars, Cfg.v2_port, Cfg.no_model = upstream, keep, min_chars, v2_port, no_model
    Cfg.skip_tools, Cfg.log_path, Cfg.v2_timeout, Cfg.timeout = re.compile(skip_tools), log, v2_timeout, timeout
    Cfg.preset, Cfg.force_lossy, Cfg.harness = preset, force_lossy, harness
    Cfg.state, Cfg.off_file = os.path.expanduser(state or ""), os.path.expanduser(off_file or "")
    _resolve()
    CACHE = Cache(os.path.expanduser(cache_file) if cache_file else None)
    _model_down_until[0] = 0.0
    reset_stats()


def make_server(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Proxy)
    srv.daemon_threads = True
    return srv


def main() -> None:
    os.umask(0o077)   # audit M2: cache / log / state files private (own process)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", required=True, help="e.g. https://api.openai.com or http://127.0.0.1:8000")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--preset", choices=("safe", "balanced", "aggressive"), default=None,
                    help="ctxprune_core.PRESETS (default: CTXPRUNE_PRESET, else safe)")
    ap.add_argument("--harness", default="", help="harness behind the proxy (codex, hermes, ...): per-harness preset "
                    "values; balanced = safe on codex/opencode/pi/dsh unless --force-lossy (default CTXPRUNE_HARNESS)")
    ap.add_argument("--force-lossy", action="store_true", help="run balanced's model even where the harness opts out")
    ap.add_argument("--keep", type=float, default=None, help="default: the preset's")
    ap.add_argument("--min-chars", type=int, default=None, help="default: the preset's")
    ap.add_argument("--v2-port", type=int, default=8765)
    ap.add_argument("--v2-timeout", type=float, default=20.0)
    ap.add_argument("--no-model", action="store_true", help="lossless stage only")
    ap.add_argument("--cache-file", default=None, help="JSONL cache shared across restarts (recommended)")
    ap.add_argument("--log", default=None, help="per-request JSONL log")
    ap.add_argument("--skip-tools", default=SKIP_TOOLS, help="regex of tool names never touched")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--dedup", choices=("on", "off"), default=None,
                    help="cross-turn dedup within each request (default off; CTXPRUNE_DEDUP=1 turns it on)")
    ap.add_argument("--note", choices=("on", "min", "off"), default="off",
                    help="append the constant ctxprune note (ctxprune_note.py) to the system prompt")
    ap.add_argument("--view-safe", choices=("on", "off"), default="on",
                    help="Hermes read_file JSON results: JSON unescape + numbered gutter only (code bytes exact)")
    ap.add_argument("--max-body-mb", type=float, default=64, help="413 for request bodies above this")
    ap.add_argument("--state", default="", help="state dir shared with the hooks/plugins (default CTXPRUNE_STATE, "
                    "else ~/.cache/ctxprune): emitted.py registry + the OFF file")
    ap.add_argument("--off-file", default="", help="pass-through while this file exists (default <state>/OFF)")
    a = ap.parse_args()
    configure(a.upstream, a.keep, a.min_chars, a.v2_port, a.no_model, a.cache_file, a.log, a.skip_tools,
              a.v2_timeout, a.timeout, None if a.dedup is None else a.dedup == "on", a.note, a.view_safe == "on",
              int(a.max_body_mb * (1 << 20)),
              a.preset, a.force_lossy, a.harness, a.state, a.off_file)
    srv = make_server(a.port)
    st = Cfg.settings
    mode = f"preset {st.preset}: " + ("lossless only" if not st.model else
                                      f"v2 on :{a.v2_port} keep {st.keep} >= {st.min_chars} chars, {st.select or 'server'} select")
    print(f"ctxprune proxy: 127.0.0.1:{a.port} -> {a.upstream} ({mode}; note {a.note}; cache {len(CACHE.mem)} entries)",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
