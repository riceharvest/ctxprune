"""ctxprune plugin for Hermes Agent (v0.22.x). Install + enable:

  cp -r scripts/integrations/hermes/ctxprune ~/.hermes/plugins/ctxprune
  hermes plugins enable ctxprune          # or add `ctxprune` to plugins.enabled in ~/.hermes/config.yaml

Hooks (docs: hermes-agent.nousresearch.com/docs/user-guide/features/hooks):
  transform_terminal_output(command, output, returncode, task_id, env_type, **kw)
      bash output after bounded capture, before Hermes' final output cap -> compressed text (or None = unchanged).
      Background-process output (returncode None while running) is left alone.
  transform_tool_result(tool_name, args, result, task_id, **kw)
      every other tool's model-bound result string. Error results get the lossless stage only. Per tool:
        read_file   view-safe (view_safe.py): the JSON wrapper's content unescaped under a header that keeps the
                    sibling keys (total_lines ...), plus the `numbered` gutter rule only, so code bytes stay exact
                    (18.1% of Hermes prompt tokens, +1.48% saved; reach study 2026-10-17). Errors: untouched.
        terminal    its output was compressed above; here only the JSON wrapper is unescaped, and only when
                    exit_code == 0 (Hermes' terminal failure check reads exit_code from the JSON; the compaction
                    stub regex-reads "exit_code", which stays in the header).
        write_file  lossless stage only; the result must still parse as JSON (Hermes reads bytes_written from it).
        skill_view  lossless stage only (never the model: Hermes keeps skill bodies intact through compaction).
        patch, edit/create tools, memory, todo, other skill_* tools: untouched (patch stays JSON: background
                    review reads its `success`; the agent edits against exact bytes, PLAN §3.0).
      `paths` (dir-grouped path lists) is off when the model is local / self-hosted (provider + base_url from the
      llm_request middleware, remembered per task / session; lossless.paths_on, CTXPRUNE_PATHS=0|1 overrides).
      A call without a tool_call_id (execute_code's RPC: a script json.loads the result) keeps JSON results JSON.
      Hermes classifies a result AFTER this hook; a string that no longer parses as JSON is called a failure when
      '"error"' / '"failed"' appears in its first 500 chars. Unescaping can create such a match, so a rewrite that
      would newly trip that check is dropped (view_safe.hermes_failure_text).
Both call scripts/ctxprune_compress.py in-process (run()); CTXPRUNE_SCRIPTS points at the scripts dir (default
~/ctxprune-v2/scripts). Settings come from env (CTXPRUNE_KEEP, CTXPRUNE_MIN_CHARS, CTXPRUNE_PORT, CTXPRUNE_LOG,
CTXPRUNE_NO_MODEL). Fails open: any error -> None -> Hermes keeps the original. Time budget: every call runs in a
worker thread and is abandoned after CTXPRUNE_BUDGET seconds (default 2; 0 = none): Hermes keeps the original.
Every rewritten output is recorded in emitted.py's registry, so a stacked ctxprune_proxy forwards it untouched.

Cross-turn dedup (llm_request middleware, on_llm_request): NOT at tool time. Hermes compaction keeps head + tail and
summarizes the middle, micro-compaction folds old exchanges and the proactive prune demotes old results to stubs, so
a reference written into a result could later point at an output that is gone. Instead every model request is
deduped from its own message list (dedup.context_dedup, like ctxprune_proxy): a tool result's repeated lines become
`[ctxprune: lines a–b identical to output #k above (`cmd`)]` pointing at an earlier result IN THAT REQUEST. The
rewrite is request-local (the session keeps the full outputs; compaction and its summarizer see them) and depends
only on the request prefix, so the provider prompt cache sees the same bytes. Results that stay JSON (a failing
terminal command: Hermes reads its exit_code) are deduped inside their "output" string and re-serialized exactly.
Not rewritten: read_file, patch/edit/write, memory, todo, skill_* (PLAN §3.0); they are still counted and referable.
Chat Completions, Anthropic Messages and Responses request shapes. Opt-in: only with CTXPRUNE_DEDUP=1 (the Qwen3.8
comprehension probe lost 7-27 pts on dedup'd outputs).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import threading
import time

_SKIP = re.compile(os.environ.get("CTXPRUNE_SKIP_TOOLS") or
                   r"(?i)^(terminal|process|read_file|patch|edit_file|create_file|apply_patch|"
                   r"str_replace_editor|view|view_file|view_image|notebook_.*|memory|todo|skill_(?!view$).*)$")
_VIEW = re.compile(os.environ.get("CTXPRUNE_VIEW_TOOLS") or r"(?i)^(read_file)$")
_KEEP_JSON = {"write_file"}
_LOSSLESS_ONLY = {"skill_view", "write_file"}   # newly reached tools: lossless stage only, never lossy line selection
_mod = None


def _cc():
    global _mod
    if _mod is None:
        d = os.environ.get("CTXPRUNE_SCRIPTS") or os.path.expanduser("~/ctxprune-v2/scripts")
        # audit M4: our scripts dir goes at the END of sys.path, so Hermes' own modules are never shadowed by ours
        # (methods, dedup, recover, ...). Some ctxprune modules insert their dir at the front when imported, so the
        # pipeline is imported eagerly here and sys.path is restored afterwards (later lazy imports resolve via the
        # appended entry; a name clash there fails open).
        snap = [p for p in sys.path if p != d] + [d]
        sys.path[:] = snap
        try:
            import ctxprune_compress  # noqa: PLC0415
            import ctxprune_core, dedup, lossless, lossless2, recover, view_safe  # noqa: F401,E401,PLC0415
        finally:
            sys.path[:] = snap + [p for p in sys.path if p not in snap]
        _mod = ctxprune_compress
    return _mod


def _is_json(s):
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def _view_safe(text, tool, rules, exit0=False):
    """JSON-wrapper unescape (+ lossless2 `rules`) for a successful result; logged like the normal path."""
    try:
        if not isinstance(text, str) or not text:
            return None
        cc = _cc()
        import view_safe  # noqa: PLC0415  (same scripts dir)
        t0 = time.perf_counter()
        out = view_safe.wrapper_unescape(text, tuple(rules), require_exit0=exit0)
        if out is None or (tool != "terminal" and view_safe.hermes_failure_text(out)
                           and not view_safe.hermes_failure_text(text)):
            return None
        log = os.environ.get("CTXPRUNE_LOG") or None
        if log:
            cc._log(log, {"ts": time.time(), "harness": "hermes", "tool": tool, "cmd": "", "before": len(text),
                          "after": len(out), "stage": "lossless", "v2": "view",
                          "rules": ["json_unescape"] + [f"l2_{r}" for r in rules if f"[cp:{r}" in out],
                          "ms": round((time.perf_counter() - t0) * 1000, 1), "probe": cc._probe(text, out)})
        from emitted import record   # a ctxprune_proxy stacked behind Hermes forwards it untouched
        record(out)
        return out
    except Exception:
        return None


_TARGETS: dict = {}   # (kind, task_id | session_id) -> the model's "provider base_url", first seen (fixed per session)
_FIRST_TARGET = [""]  # the process's first one: fallback for hooks without an id


def _note_target(kw):
    """llm_request carries provider + base_url (Hermes turn_api_request); tool hooks don't, so remember them per
    task / session. First seen wins: the `paths` choice (lossless.paths_on: off for a local / self-hosted model)
    stays fixed for the session, so the prompt cache sees the same bytes."""
    t = " ".join(str(kw.get(k) or "") for k in ("provider", "base_url")).strip()
    if not t:
        return
    for kind in ("task_id", "session_id"):
        if kw.get(kind):
            _TARGETS.setdefault((kind, str(kw[kind])), t)
    while len(_TARGETS) > 4096:
        _TARGETS.pop(next(iter(_TARGETS)))
    if not _FIRST_TARGET[0]:
        _FIRST_TARGET[0] = t


def _target(kw):
    for kind in ("task_id", "session_id"):
        t = _TARGETS.get((kind, str(kw.get(kind) or "")))
        if t:
            return t
    return _FIRST_TARGET[0] or None


def _compress(text, command, tool, error=False, target=None):
    try:
        if not isinstance(text, str) or not text:
            return None
        out, _rec = _cc().run(text, command=command, tool=tool, harness="hermes", no_model=True if error else None,
                              target=target)
        return out if isinstance(out, str) and out != text else None
    except Exception:
        return None


def _stand_in(tool_name, args):
    """A command line for lossless (path tokens for grep grouping) and the v2 intent."""
    a = args if isinstance(args, dict) else {}
    if isinstance(a.get("command"), str):
        return a["command"]
    if re.search(r"(?i)search|grep", tool_name or "") and a.get("pattern"):
        parts = ["rg", "-n", "--", str(a.get("pattern"))] + ([str(a["path"])] if a.get("path") else [])
        return " ".join(shlex.quote(p) for p in parts)
    try:
        return f"{tool_name} {json.dumps(a, ensure_ascii=False, sort_keys=True)}"[:500]
    except Exception:
        return tool_name or ""


def _off():
    return os.environ.get("CTXPRUNE_OFF", "") in ("1", "true", "yes")   # kill switch (as in the Claude Code hooks)


_STUCK = [0]                 # worker threads still running past their budget
_STUCK_LOCK = threading.Lock()
MAX_STUCK = 8


def _budget() -> float:
    try:
        return float(os.environ.get("CTXPRUNE_BUDGET", "2"))
    except ValueError:
        return 2.0


def _within_budget(fn, *args, **kw):
    """Run fn in a daemon thread and wait at most CTXPRUNE_BUDGET seconds (default 2; 0 = no limit): the plugin runs
    in-process, so a slow compress would stall Hermes itself (live canary: 5 x 3 s -> 19.5 s wall). Past the budget
    -> None (Hermes keeps the original); the thread finishes in the background and its result is dropped. With
    MAX_STUCK threads still running, calls are skipped outright."""
    b = _budget()
    if b <= 0:
        return fn(*args, **kw)
    if _STUCK[0] >= MAX_STUCK:
        return None
    box, state = [], {"late": False, "done": False}

    def work():
        try:
            box.append(fn(*args, **kw))
        except Exception:
            box.append(None)
        finally:
            with _STUCK_LOCK:
                state["done"] = True
                if state["late"]:
                    _STUCK[0] -= 1

    t = threading.Thread(target=work, name="ctxprune", daemon=True)
    t.start()
    t.join(b)
    with _STUCK_LOCK:
        if not state["done"]:
            state["late"] = True
            _STUCK[0] += 1
            return None
    return box[0] if box else None


def on_terminal_output(command="", output="", returncode=None, task_id="", env_type="", **kwargs):
    if returncode is None or _off():
        return None
    return _within_budget(_compress, output, command if isinstance(command, str) else "", "terminal",
                          target=_target({"task_id": task_id, **kwargs}))


def on_tool_result(tool_name="", args=None, result="", task_id="", **kwargs):
    if not tool_name or _off():
        return None
    out = _within_budget(_on_tool_result, tool_name, args, result, task_id=task_id, **kwargs)
    # Calls that do not go to the model: execute_code's RPC (handle_function_call without a tool_call_id; Hermes
    # passes tool_call_id="" then) hands the result to a script that json.loads it (Hermes #90322). A result that
    # was JSON stays JSON there (json_unescape, jsonrows, sffiles and view-safe make it text).
    if out is not None and "tool_call_id" in kwargs and not kwargs["tool_call_id"] and _is_json(result) \
            and not _is_json(out):
        return None
    return out


def _on_tool_result(tool_name="", args=None, result="", **kwargs):
    err = bool(kwargs.get("error_type")) or str(kwargs.get("status") or "").lower() in ("error", "failed", "failure")
    if tool_name == "terminal":
        return None if err else _view_safe(result, tool_name, (), exit0=True)
    if _VIEW.match(tool_name):
        return None if err else _view_safe(result, tool_name, ("numbered",))
    if _SKIP.match(tool_name):
        return None
    out = _compress(result, _stand_in(tool_name, args), tool_name, error=err or tool_name in _LOSSLESS_ONLY,
                    target=_target(kwargs))
    if out is None:
        return None
    if tool_name in _KEEP_JSON and not _is_json(out):
        return None
    try:
        import view_safe  # noqa: PLC0415
        if view_safe.hermes_failure_text(out) and not view_safe.hermes_failure_text(result):
            return None
    except Exception:
        return None
    return out


def _text_at(obj, path):
    """(text, path to it) of a message content at `path`: a string, or a parts list with exactly one text part.
    A parts list with several text parts -> (joined text, None): counted for dedup, never rewritten."""
    c = obj
    for k in path:
        c = c[k]
    if isinstance(c, str):
        return c, path
    if isinstance(c, list):
        idx = [j for j, p in enumerate(c) if isinstance(p, dict) and p.get("type") in ("text", "input_text",
                                                                                      "output_text")]
        if len(idx) == 1 and isinstance(c[idx[0]].get("text"), str):
            return c[idx[0]]["text"], path + (idx[0], "text")
        return "\n".join(c[j].get("text") or "" for j in idx if isinstance(c[j].get("text"), str)), None
    return None, None


def _request_events(request):
    """The request's tool calls and results in order (dedup.context_dedup events) + the path of each rewritable
    result text inside the request ({event index: path})."""
    events, paths = [], {}

    def result(cid, path, name=""):
        try:
            text, p = _text_at(request, path)
        except (KeyError, IndexError, TypeError):
            return
        if text is None:
            return
        if p is not None:
            paths[len(events)] = p
        events.append({"t": "result", "id": cid, "name": name, "text": text, "dedup": p is not None})

    if isinstance(request.get("input"), list):   # Responses API (codex_responses)
        for n, it in enumerate(request["input"]):
            if not isinstance(it, dict):
                continue
            typ = it.get("type")
            if typ in ("function_call", "custom_tool_call"):
                events.append({"t": "call", "id": it.get("call_id") or it.get("id"), "name": it.get("name"),
                               "args": it.get("arguments") if typ == "function_call" else {}})
            elif typ in ("function_call_output", "custom_tool_call_output"):
                result(it.get("call_id") or it.get("id"), ("input", n, "output"))
        return events, paths
    msgs = request.get("messages")
    for n, m in enumerate(msgs if isinstance(msgs, list) else []):
        if not isinstance(m, dict):
            continue
        role, c = m.get("role"), m.get("content")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:    # Chat Completions
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    events.append({"t": "call", "id": tc.get("id"), "name": fn.get("name"), "args": fn.get("arguments")})
            for b in c if isinstance(c, list) else []:   # Anthropic Messages
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    events.append({"t": "call", "id": b.get("id"), "name": b.get("name"), "args": b.get("input")})
        elif role == "tool":
            result(m.get("tool_call_id"), ("messages", n, "content"), m.get("name") or "")
        elif role == "user" and isinstance(c, list):
            for j, b in enumerate(c):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    result(b.get("tool_use_id"), ("messages", n, "content", j, "content"))
    return events, paths


def _cow_set(obj, path, value):
    """obj with the value at `path` replaced; every dict / list on the path is copied, nothing is mutated (Hermes'
    request copy can be shallow, and a budget-abandoned worker must not write into a live request)."""
    if not path:
        return value
    c = dict(obj) if isinstance(obj, dict) else list(obj)
    c[path[0]] = _cow_set(obj[path[0]], path[1:], value)
    return c


def _on_llm_request(request):
    if request.get("previous_response_id"):   # server-side history: the numbering would not match what the model sees
        return None
    cc = _cc()
    events, paths = _request_events(request)
    if sum(1 for e in events if e["t"] == "result") < 2:
        return None
    out, _st = cc.context(events, "hermes")
    new = request
    for i, text in out.items():
        if i in paths and isinstance(text, str):
            new = _cow_set(new, paths[i], text)
    return {"request": new, "source": "ctxprune", "reason": "cross-turn dedup"} if new is not request else None


def on_llm_request(request=None, **kwargs):
    """llm_request middleware: Hermes passes this callback its own copy of the provider request; returning
    {"request": ...} replaces it for this call only."""
    _note_target(kwargs)
    if not isinstance(request, dict) or _off() or \
            os.environ.get("CTXPRUNE_DEDUP", "").strip().lower() not in ("1", "true", "yes", "on"):   # opt-in
        return None
    return _within_budget(_on_llm_request, request)


def register_dedup(ctx):
    """The request-time dedup middleware (Hermes >= the hermes.middleware.v1 contract); a no-op on older Hermes."""
    reg = getattr(ctx, "register_middleware", None)
    if callable(reg):
        reg("llm_request", on_llm_request)


def register(ctx):
    ctx.register_hook("transform_terminal_output", on_terminal_output)
    ctx.register_hook("transform_tool_result", on_tool_result)
    register_dedup(ctx)
