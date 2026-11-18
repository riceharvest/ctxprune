"""Read-only usage readers for `ctxprune stats` and `ctxprune audit`: what each agent harness itself records locally.

Every reader returns NUMBERS and NAMES only (token counts, request counts, tool names, MCP server names, provider
names), never message text, and never writes anything. Network is never used. Each reader says where its numbers
come from (`source`) and how they are counted (`basis`):

  claude-code  ~/.claude/projects/**/*.jsonl         assistant `usage` per response id: fresh + cache writes
  codex        ~/.codex/sessions/**/rollout-*.jsonl  token_count events (last_token_usage): input - cached
  pi           ~/.pi/agent/sessions/**/*.jsonl       assistant message usage (pi-ai: input excludes cache) + writes
  opencode     ~/.local/share/opencode/opencode.db   assistant message tokens (input excludes cache) + cache.write
               (and the older storage/message/**/*.json files), opened read-only
  hermes       ~/.hermes/sessions/session_*.json     no token counts in these files: CHARACTERS of the system
               prompt, tool schemas and messages (each enters the context once); nothing else under ~/.hermes is read
  dsh          -                                     no verified local usage format: not read

"New input" is what newly entered the context (fresh input + cache writes, i.e. not cache reads). ctxprune's share
is saved / (new + saved). Content re-read from the cache on later turns shrinks by about the same share.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

CHARS_PER_TOKEN = 4.0


def _ts(v) -> float:
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    if not isinstance(v, str):
        return 0.0
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _home() -> str:
    return os.path.expanduser("~")


def roots() -> dict:
    e = os.environ.get
    data = e("XDG_DATA_HOME") or os.path.join(_home(), ".local", "share")
    return {
        "claude-code": os.path.join(e("CLAUDE_CONFIG_DIR") or os.path.join(_home(), ".claude"), "projects"),
        "codex": os.path.join(e("CODEX_HOME") or os.path.join(_home(), ".codex"), "sessions"),
        "pi": os.path.join(e("PI_CODING_AGENT_DIR") or os.path.join(_home(), ".pi", "agent"), "sessions"),
        "opencode": os.path.join(data, "opencode"),
        "hermes": os.path.join(e("HERMES_HOME") or os.path.join(_home(), ".hermes"), "sessions"),
    }


def _files(root: str, pred, since: float):
    if not os.path.isdir(root):
        return
    for d, _, fs in os.walk(root):
        for fn in fs:
            if not pred(fn):
                continue
            p = os.path.join(d, fn)
            try:
                if os.path.getmtime(p) >= since:
                    yield p
            except OSError:
                continue


def _jsonl(p: str, needle: str = ""):
    try:
        f = open(p, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for ln in f:
            if needle and needle not in ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def _new(**kw) -> dict:
    d = {"requests": 0, "new_input": 0, "cache_read": 0, "unit": "tokens", "tools": {}, "providers": {},
         "first_input": []}
    d.update(kw)
    return d


def _int(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _count(d: dict, key: str, name) -> None:
    if isinstance(name, str) and name:
        d[key][name[:80]] = d[key].get(name[:80], 0) + 1


# ---------------------------------------------------------------------------------------------- readers
def read_claude_code(since: float, root: str | None = None) -> dict | None:
    root = root or roots()["claude-code"]
    out = _new(source=root, basis="assistant usage per response id: input_tokens + cache_creation_input_tokens")
    seen = set()
    for p in _files(root, lambda f: f.endswith(".jsonl"), since):
        first = True
        for r in _jsonl(p, '"assistant"'):
            m = r.get("message")
            if r.get("type") != "assistant" or not isinstance(m, dict) or _ts(r.get("timestamp")) < since:
                continue
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    _count(out, "tools", b.get("name"))
            u = m.get("usage")
            key = m.get("id") or r.get("requestId") or r.get("uuid")
            if not isinstance(u, dict) or key in seen:
                continue
            seen.add(key)
            out["requests"] += 1
            new = _int(u.get("input_tokens")) + _int(u.get("cache_creation_input_tokens"))
            out["new_input"] += new
            out["cache_read"] += _int(u.get("cache_read_input_tokens"))
            if first:
                out["first_input"].append(new + _int(u.get("cache_read_input_tokens")))
                first = False
            _count(out, "providers", "anthropic")
    return out if out["requests"] else None


def read_codex(since: float, root: str | None = None) -> dict | None:
    root = root or roots()["codex"]
    out = _new(source=root, basis="token_count events: last_token_usage.input_tokens - cached_input_tokens")
    for p in _files(root, lambda f: f.endswith(".jsonl"), since):
        prev, first = None, True
        for r in _jsonl(p):
            pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            if r.get("type") == "session_meta" or pl.get("model_provider"):
                _count(out, "providers", pl.get("model_provider"))
            if pl.get("type") in ("function_call", "custom_tool_call"):
                _count(out, "tools", pl.get("name"))
                continue
            if r.get("type") != "event_msg" or pl.get("type") != "token_count" or _ts(r.get("timestamp")) < since:
                continue
            info = pl.get("info") if isinstance(pl.get("info"), dict) else None
            if not info:
                continue
            tot = json.dumps(info.get("total_token_usage"), sort_keys=True)
            if tot == prev:   # a repeated event (rate-limit refresh) for the same request
                continue
            prev = tot
            u = info.get("last_token_usage") or {}
            inp, cached = _int(u.get("input_tokens")), _int(u.get("cached_input_tokens"))
            if not inp:
                continue
            out["requests"] += 1
            out["new_input"] += max(0, inp - cached)
            out["cache_read"] += cached
            if first:
                out["first_input"].append(inp)
                first = False
    return out if out["requests"] else None


def read_pi(since: float, root: str | None = None) -> dict | None:
    root = root or roots()["pi"]
    out = _new(source=root, basis="assistant message usage: input (pi-ai excludes cache reads/writes) + cacheWrite")
    for p in _files(root, lambda f: f.endswith(".jsonl"), since):
        first = True
        for r in _jsonl(p, '"message"'):
            m = r.get("message")
            if r.get("type") != "message" or not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            if max(_ts(r.get("timestamp")), _ts(m.get("timestamp"))) < since:
                continue
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") in ("toolCall", "tool_call", "tool_use"):
                    _count(out, "tools", b.get("name"))
            u = m.get("usage")
            if not isinstance(u, dict):
                continue
            new = _int(u.get("input")) + _int(u.get("cacheWrite"))
            if not new and not _int(u.get("cacheRead")):
                continue
            out["requests"] += 1
            out["new_input"] += new
            out["cache_read"] += _int(u.get("cacheRead"))
            _count(out, "providers", m.get("provider") or r.get("provider"))
            if first:
                out["first_input"].append(new + _int(u.get("cacheRead")))
                first = False
    return out if out["requests"] else None


def _opencode_msg(out: dict, d: dict, since: float, firsts: dict) -> None:
    if not isinstance(d, dict) or d.get("role") != "assistant":
        return
    t = d.get("time") if isinstance(d.get("time"), dict) else {}
    if _ts(t.get("created")) < since:
        return
    tok = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
    cache = tok.get("cache") if isinstance(tok.get("cache"), dict) else {}
    new = _int(tok.get("input")) + _int(cache.get("write"))
    if not new and not _int(cache.get("read")):
        return
    out["requests"] += 1
    out["new_input"] += new
    out["cache_read"] += _int(cache.get("read"))
    model = d.get("model") if isinstance(d.get("model"), dict) else {}
    _count(out, "providers", d.get("providerID") or model.get("providerID"))
    sid = d.get("sessionID")
    if sid and sid not in firsts:
        firsts[sid] = new + _int(cache.get("read"))


def read_opencode(since: float, root: str | None = None) -> dict | None:
    root = root or roots()["opencode"]
    out = _new(source=root, basis="assistant message tokens: input (excludes cache reads) + cache.write")
    firsts: dict = {}
    db = os.path.join(root, "opencode.db")
    if os.path.isfile(db):
        try:
            import sqlite3
            from urllib.parse import quote
            c = sqlite3.connect(f"file:{quote(db)}?mode=ro", uri=True, timeout=2.0)
            try:
                for sid, data in c.execute("select session_id, data from message order by time_created"):
                    try:
                        d = json.loads(data)
                        if isinstance(d, dict):
                            d.setdefault("sessionID", sid)
                        _opencode_msg(out, d, since, firsts)
                    except ValueError:
                        continue
                for (data,) in c.execute("select data from part where data like '%\"tool\"%'"):
                    try:
                        pd = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(pd, dict) and pd.get("type") == "tool":
                        _count(out, "tools", pd.get("tool"))
            finally:
                c.close()
        except Exception:   # locked / other schema: the older JSON files below may still answer
            pass
    if not out["requests"]:   # older OpenCode: one JSON file per message
        for p in _files(os.path.join(root, "storage", "message"), lambda f: f.endswith(".json"), since):
            try:
                with open(p, encoding="utf-8") as f:
                    _opencode_msg(out, json.load(f), since, firsts)
            except (OSError, ValueError):
                continue
    out["first_input"] = list(firsts.values())
    return out if out["requests"] else None


_HERMES_FILE = re.compile(r"^session_.*\.json$")


def read_hermes(since: float, root: str | None = None) -> dict | None:
    root = root or roots()["hermes"]
    out = _new(source=root, unit="chars", basis="session_*.json carry no token counts: characters of the system "
               "prompt, tool schemas and messages (each enters the context once)", sessions=0)
    for p in _files(root, lambda f: bool(_HERMES_FILE.match(f)), since):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        msgs = [m for m in d.get("messages") or [] if isinstance(m, dict)]
        static = len(d.get("system_prompt") or "") + len(json.dumps(d.get("tools") or [], ensure_ascii=False))
        chars = static
        for m in msgs:
            for k in ("content", "reasoning", "reasoning_content"):
                v = m.get(k)
                chars += len(v) if isinstance(v, str) else len(json.dumps(v, ensure_ascii=False)) if v else 0
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    _count(out, "tools", fn.get("name"))
                    chars += len(fn.get("arguments") or "") if isinstance(fn.get("arguments"), str) else 0
        calls = sum(1 for m in msgs if m.get("role") == "assistant")
        if not calls:
            continue
        out["sessions"] += 1
        out["requests"] += calls
        out["new_input"] += chars
        out["first_input"].append(static)
        host = re.sub(r"^https?://([^/:]+).*$", r"\1", d.get("base_url") or "") if isinstance(d.get("base_url"),
                                                                                             str) else ""
        _count(out, "providers", host or None)
    return out if out["requests"] else None


READERS = {"claude-code": read_claude_code, "codex": read_codex, "pi": read_pi, "opencode": read_opencode,
           "hermes": read_hermes}
NOT_READ = {"dsh": "no verified local usage format (counts log only)"}


def read_all(since: float) -> dict:
    out = {}
    for h, fn in READERS.items():
        try:
            out[h] = fn(since)
        except Exception:  # noqa: BLE001 - a reader bug must not break stats
            out[h] = None
    return out


def new_input_tokens(u: dict | None) -> float:
    if not u:
        return 0.0
    return u["new_input"] / CHARS_PER_TOKEN if u.get("unit") == "chars" else float(u["new_input"])


def share(saved_tokens: float, u: dict | None) -> float | None:
    new = new_input_tokens(u)
    return 100.0 * saved_tokens / (new + saved_tokens) if u and new + saved_tokens > 0 else None
