#!/usr/bin/env python3
"""ctxprune audit (prototype): where a Claude Code user's bill goes, and what static context they could drop.

LOCAL ONLY, ADVICE ONLY. Reads ~/.claude/projects/**/*.jsonl (main sessions and sub-agent streams) and, to name
sources, the user's Claude Code settings / plugin manifests (read-only). It never writes config, never opens a network
socket (socket creation is disabled for the whole run, so tiktoken is used only when its o200k file is already
cached), and prints only numbers and NAMES (MCP server names, plugin names, CLAUDE.md paths, hook events): never text
from a prompt, an output or an injected block.

Method (the same one results/billmap uses):
  * One request = one assistant message.id with usage (model claude-*; <synthetic> skipped). Billed input
    T = cache_read R + cache_write W + fresh I, priced at Opus 5.5 list ($/M: input 4, cache write 5, read 0.20,
    output 20) whatever model ran it.
  * Its context is rebuilt in wire order: tool schemas + system prompt (from the `prompt_snapshot` attachment when the
    transcript has one, else inferred per stream as T1 - est(first request's messages)), then the parentUuid chain of
    transcript entries back to the root (a compaction starts a new root). Every entry is cut into typed items
    (CLAUDE.md, skill listing, MCP listing per server, hook injections, user text, assistant text, thinking, tool calls,
    tool results per tool ...). Injected blocks are counted as the model sees them (`rendered`), not as their raw
    hook stdout (a 53 KB SessionStart graph reaches the model as a 2 KB persisted preview).
  * Tokens: o200k x 1.485 (Claude tokens) when tiktoken + a cached o200k file exist, else chars / 2.5.
    Redacted thinking: output_tokens minus the visible text + tool_use tokens of that message.
  * Price by POSITION: the request's first R tokens are cache reads, the next W cache writes, the rest fresh. Estimated
    positions are mapped onto billed ones by T / est; each item is priced est_tokens x the price class at its position.
    The bill minus the sum of items is reported as `unattributed` (tokenizer error, wire framing), not spread around.
  * Output: output_tokens x $20, split into thinking / text / tool-call JSON.

Usage:
  ctx_audit.py [--days 30] [--root ~/.claude/projects] [--exclude GLOB ...] [--json OUT] [--records OUT.jsonl]
  ctx_audit.py --self-test
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import re
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

P_IN, P_W, P_R, P_OUT = 4.0, 5.0, 0.20, 20.0
P_W1 = 8.0                      # 1-hour cache write (2x input); usage.cache_creation splits 5m / 1h
CLAUDE_PER_O200K = 1.485
CHARS_PER_TOK_FALLBACK = 2.5
LOSSY_MIN_CHARS = 2000          # ctxprune_core.PRESETS["balanced"]["min_chars"]
SPILL_MARKERS = ("<persisted-output>", "Output too large")


# ------------------------------------------------------------------------------------------------ no network, ever
def block_network() -> None:
    import socket

    class _NoSocket(socket.socket):
        def __init__(self, *a, **k):
            raise OSError("ctx_audit: network disabled")

    def _no(*a, **k):
        raise OSError("ctx_audit: network disabled")

    socket.socket = _NoSocket            # type: ignore[misc]
    socket.create_connection = _no       # type: ignore[assignment]
    socket.getaddrinfo = _no             # type: ignore[assignment]


# ------------------------------------------------------------------------------------------------ tokens
_ENC = None


def ntok(s: str) -> float:
    global _ENC
    if not s:
        return 0.0
    if _ENC is None:
        _ENC = False
        if os.environ.get("CTX_AUDIT_NO_TIKTOKEN") != "1":
            try:
                import tiktoken
                _ENC = tiktoken.get_encoding("o200k_base")      # cached file only: sockets are blocked
            except Exception:
                _ENC = False
    if _ENC:
        return len(_ENC.encode_ordinary(s)) * CLAUDE_PER_O200K
    return len(s) / CHARS_PER_TOK_FALLBACK


def tokenizer_name() -> str:
    ntok("x")
    return "o200k x %.3f" % CLAUDE_PER_O200K if _ENC else "chars / %.1f (tiktoken not available)" % CHARS_PER_TOK_FALLBACK


def image_tokens(b64: str) -> float:
    """Anthropic: tokens ~ w*h/750 after the long edge is scaled to <= 1568 px. Dimensions from the PNG/JPEG header."""
    try:
        raw = base64.b64decode(b64[:65536] + "=" * (-len(b64[:65536]) % 4), validate=False)
        w = h = 0
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", raw[16:24])
        elif raw[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                mk = raw[i + 1]
                ln = struct.unpack(">H", raw[i + 2:i + 4])[0]
                if mk in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    break
                i += 2 + ln
        if w and h:
            s = min(1.0, 1568 / max(w, h))
            return min(1600.0, (w * s) * (h * s) / 750)
    except Exception:
        pass
    return 1600.0


# ------------------------------------------------------------------------------------------------ naming helpers
def mcp_server(tool_name: str) -> str:
    """mcp__<s>__tool, mcp__claude_ai_<s>__tool, mcp__plugin_<p>_<s>__tool -> the middle part."""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 3:
            return parts[1]
    return ""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def plugin_of(name: str) -> str:
    """'vercel-plugin:nextjs' -> 'vercel-plugin'; unprefixed skills/agents -> '' (user / built-in)."""
    return name.split(":", 1)[0] if ":" in name else ""


REMINDER = re.compile(r"<system-reminder>(.*?)</system-reminder>", re.S)


def reminder_kind(body: str) -> tuple[str, str]:
    """Classify an inline <system-reminder> (older transcripts have no attachment entries). Returns (comp, src)."""
    b = body[:400]
    if "CLAUDE.md" in b or "Codebase and user instructions" in b:
        return "claude_md", "inline"
    if "MEMORY.md" in b or "/memory/" in b:
        return "memory", "inline"
    if "skills are available" in b:
        return "skills_listing", "inline"
    if "agent types" in b.lower():
        return "agents_listing", "inline"
    if "MCP Server Instructions" in b:
        return "mcp_instructions", "inline"
    if "deferred tools" in b or "ToolSearch" in b:
        return "mcp_listing", "inline"
    m = re.match(r"\s*(SessionStart|UserPromptSubmit|SubagentStart|PreToolUse|PostToolUse)[^\n]*hook", b)
    if m:
        return "hooks", m.group(1)
    return "reminders", "inline"


def split_reminders(text: str) -> tuple[str, list[tuple[str, str, float]]]:
    """Strip inline <system-reminder> blocks from a user/tool text; returns (rest, [(comp, src, tok)])."""
    if "<system-reminder>" not in text:
        return text, []
    items = []
    for m in REMINDER.finditer(text):
        comp, src = reminder_kind(m.group(1))
        items.append((comp, src, ntok(m.group(0))))
    return REMINDER.sub("", text), items


def rendered_text(r: dict) -> str:
    out = []
    for x in r.get("rendered") or []:
        if isinstance(x, dict):
            c = x.get("content", x.get("text", ""))
            out.append(c if isinstance(c, str) else json.dumps(c))
        elif isinstance(x, str):
            out.append(x)
    return "\n".join(out)


# components, in the order of the breakdown table
GROUPS = {
    "system_prompt": "system prompt + tool schemas", "tools_builtin": "system prompt + tool schemas",
    "static_inferred": "system prompt + tool schemas",
    "fork_inherited": "forked sub-agents: inherited parent context",
    "tools_mcp": "MCP (schemas, listings, instructions)", "mcp_listing": "MCP (schemas, listings, instructions)",
    "mcp_instructions": "MCP (schemas, listings, instructions)", "mcp_schema": "MCP (schemas, listings, instructions)",
    "skills_listing": "skills + agents listings", "agents_listing": "skills + agents listings",
    "deferred_builtin_listing": "system prompt + tool schemas",
    "claude_md": "CLAUDE.md", "memory": "memory (MEMORY.md, memory system prompt, recalled files)",
    "memory_instr": "memory (MEMORY.md, memory system prompt, recalled files)",
    "hooks": "hook injections (SessionStart, UserPromptSubmit, SubagentStart)",
    "reminders": "other system-reminders (env, date, todo, file-changed, token budget)",
    "meta": "skill bodies + slash-command expansions", "skill_body": "skill bodies + slash-command expansions",
    "user_text": "user messages", "attached_file": "user messages", "compact_summary": "compaction summaries",
    "asst_text": "assistant text (in context)", "thinking": "thinking (in context)",
    "tool_call": "tool calls (inputs)", "tool": "tool results",
}


# ------------------------------------------------------------------------------------------------ stream parser
class Stream:
    """One transcript file: entries keyed by uuid, a cached root->tail chain, and per-key cost accumulators."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self.entries: dict[str, tuple] = {}            # uuid -> (parent, items, tok)
        self.chain: list[list] = []                    # [uuid, items, cum_start, cum_end]
        self.idx: dict[str, int] = {}
        self.cnt: list[float] = [0.0]                  # diff array of full-read counts (see _truncate)
        self.cost: dict[tuple, float] = defaultdict(float)     # (comp, src, tag) -> $
        self.carry: dict[tuple, float] = defaultdict(float)    # (comp, src, tag) -> tokens summed over requests
        self.static: list[tuple] = []                  # [(comp, src, tok)] current snapshot
        self.static_from_snapshot = False
        self.inferred_static: float | None = None
        self.forked = False                            # first entry points into another file: a forked sub-agent
        self.inherited: float | None = None            # its parent's context, carried at the front of every request
        self.billed = {"in": 0, "w": 0, "r": 0, "out": 0, "w1h": 0}
        self.bill_usd = 0.0
        self.requests = 0
        self.cold = 0                                  # requests whose cache read did not cover the static prefix
        self.rewrite_w = 0                             # cache-write tokens of cold requests after the first
        self.rewrite_n = 0
        self.calib: list[tuple] = []                   # (T, E_total, E_thinking) samples
        self.req_log: list[tuple] = []                 # (unix t, fresh, read, write, write_1h, output) per request
        self.first_req: tuple | None = None            # (T, E_static, E_msgs, snapshot?)
        self.out_cost: dict[str, float] = defaultdict(float)
        self.used: dict[str, dict] = {"mcp": defaultdict(int), "skill": defaultdict(int), "agent": defaultdict(int),
                                      "tool": defaultdict(int)}
        self.static_tok_hist: list[float] = []

    # ---- chain maintenance
    def _resolve(self, j_from: int) -> None:
        """Entries j >= j_from leave the chain: book their full-read counts, fold the counts above into j_from."""
        n = len(self.chain)
        if j_from >= n:
            return
        run = 0.0
        for j in range(n - 1, j_from - 1, -1):
            run += self.cnt[j + 1]
            if run:
                for comp, src, tag, tok in self.chain[j][1]:
                    self.cost[(comp, src, tag)] += tok * run * P_R / 1e6
                    self.carry[(comp, src, tag)] += tok * run
        self.cnt[j_from] += run
        for j in range(j_from, n):
            self.idx.pop(self.chain[j][0], None)
        del self.chain[j_from:]
        del self.cnt[j_from + 1:]

    def _ensure(self, tail: str | None) -> None:
        if tail is None or tail not in self.entries:
            self._resolve(0)
            if tail is None:
                return
        if tail in self.idx:
            self._resolve(self.idx[tail] + 1)
            return
        path = []
        u = tail
        while u is not None and u in self.entries and u not in self.idx:
            path.append(u)
            u = self.entries[u][0]
            if len(path) > 10_000_000:
                break
        self._resolve(self.idx[u] + 1 if u in self.idx else 0)
        for u in reversed(path):
            par, items, tok = self.entries[u]
            start = self.chain[-1][3] if self.chain else 0.0
            self.idx[u] = len(self.chain)
            self.chain.append([u, items, start, start + tok])
            self.cnt.append(0.0)

    def finish(self) -> None:
        self._resolve(0)

    # ---- one request
    def request(self, tail: str | None, u: dict, t: float | None = None, out: int = 0) -> None:
        self._ensure(tail)
        R = u.get("cache_read_input_tokens") or 0
        W = u.get("cache_creation_input_tokens") or 0
        I = u.get("input_tokens") or 0
        T = R + W + I
        if T <= 0:
            return
        cc = u.get("cache_creation") or {}
        w1 = min(W, cc.get("ephemeral_1h_input_tokens") or 0)
        pw = ((W - w1) * P_W + w1 * P_W1) / W if W else P_W        # blended write price of this request
        self.billed["w1h"] += w1
        self.billed["in"] += I
        self.billed["w"] += W
        self.billed["r"] += R
        self.billed["w_usd"] = self.billed.get("w_usd", 0.0) + W * pw / 1e6
        self.bill_usd += (I * P_IN + W * pw + R * P_R) / 1e6
        self.req_log.append((t, I, R, W, w1, out))
        self.requests += 1
        msgs = self.chain[-1][3] if self.chain else 0.0
        static = self.static if self.static_from_snapshot else []
        if self.forked:
            if self.inherited is None:
                self.inherited = max(0.0, T - msgs - sum(t for _, _, t in static))
            static = static + [("fork_inherited", "", self.inherited)]
        elif not self.static_from_snapshot:
            if self.inferred_static is None:
                self.inferred_static = max(0.0, T - msgs)
            static = [("static_inferred", "", self.inferred_static)]
        e_static = sum(t for _, _, t in static)
        if self.first_req is None:
            self.first_req = (T, e_static, msgs, self.static_from_snapshot)
        E = e_static + msgs
        f = T / E if E > 0 else 1.0
        cR, cW = R / f, (R + W) / f

        def price(a: float, b: float) -> float:            # $/token * tokens over est interval [a, b)
            c = 0.0
            if a < cR:
                c += (min(b, cR) - a) * P_R
            if b > cR and a < cW:
                c += (min(b, cW) - max(a, cR)) * pw
            if b > cW:
                c += (b - max(a, cW)) * P_IN
            return c / 1e6

        pos = 0.0
        for comp, src, tok in static:
            self.cost[(comp, src, "")] += price(pos, pos + tok)
            self.carry[(comp, src, "")] += tok
            pos += tok
        if R < e_static * f * 0.9:
            self.cold += 1
            if self.requests > 1:                      # cache expired / prefix changed mid-stream: whole rewrite
                self.rewrite_w += W
                self.rewrite_n += 1
        self.static_tok_hist.append(e_static)
        # chain: walk back from the tail; entries wholly before the read boundary are counted by the diff array
        x = cR - e_static
        j = len(self.chain) - 1
        while j >= 0:
            uid, items, cs, ce = self.chain[j]
            if ce <= x:
                break
            p = e_static + cs
            for comp, src, tag, tok in items:
                self.cost[(comp, src, tag)] += price(p, p + tok)
                self.carry[(comp, src, tag)] += tok
                p += tok
            j -= 1
        self.cnt[j + 1] += 1
        if self.requests % 7 == 1:
            th = 0.0                                          # thinking tokens in this context (calibration only)
            for k in range(len(self.chain)):
                for comp, _s, _t, tok in self.chain[k][1]:
                    if comp == "thinking":
                        th += tok
            self.calib.append((T, E, th))


class Context:
    """Per-run naming context (read-only views of the user's config)."""

    def __init__(self, home: Path):
        self.home = home
        self.hook_cmd_plugin: dict[str, str] = {}
        self.user_skills: set[str] = set()
        self.user_agents: set[str] = set()
        for d in (home / ".claude/skills", home / ".agents/skills"):
            if d.is_dir():
                self.user_skills |= {p.name for p in d.iterdir()}
        if (home / ".claude/agents").is_dir():
            self.user_agents |= {p.stem for p in (home / ".claude/agents").iterdir()}
        self._parents: dict[Path, Stream | None] = {}
        inst = read_json(home / ".claude/plugins/installed_plugins.json") or {}
        for key, recs in (inst.get("plugins") or {}).items():
            for rec in recs if isinstance(recs, list) else []:
                hj = read_json(Path(rec.get("installPath", "")) / "hooks/hooks.json") or {}
                for ev in (hj.get("hooks") or {}).values():
                    for grp in ev if isinstance(ev, list) else []:
                        for h in grp.get("hooks") or []:
                            if h.get("command"):
                                self.hook_cmd_plugin[h["command"]] = key.split("@")[0]


    def parent_stream(self, pfile: Path) -> "Stream | None":
        """Entry map of a fork's parent session (cached: the forks of one session are listed next to it)."""
        if pfile not in self._parents:
            if len(self._parents) >= 2:
                self._parents.pop(next(iter(self._parents)))
            try:
                self._parents[pfile] = parse_stream(pfile, self, entries_only=True) if pfile.is_file() else None
            except Exception:
                self._parents[pfile] = None
        return self._parents[pfile]


def read_json(p: Path):
    try:
        with open(p, "r", encoding="utf-8") as fh:            # read-only
            return json.load(fh)
    except Exception:
        return None


def snapshot_items(a: dict) -> list[tuple] | None:
    tools = a.get("tools")
    if not isinstance(tools, list):
        return None
    items = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name") or ""
        srv = mcp_server(name)
        items.append(("tools_mcp" if srv else "tools_builtin", srv or name, ntok(json.dumps(t, ensure_ascii=False))))
    for s in a.get("systemPrompt") or []:
        if not isinstance(s, str) or s == "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__":
            continue
        comp = "memory_instr" if s.lstrip().startswith("# Memory") else "system_prompt"
        items.append((comp, "", ntok(s)))
    return items


def _lines_split(text: str, total: float, keyfn) -> list[tuple]:
    """Split `total` tokens of a listing over its lines by length; keyfn(line) -> src, or None for a continuation
    line (booked to the previous src). Merges equal srcs."""
    lines = text.split("\n")
    n = sum(len(x) + 1 for x in lines) or 1
    acc: dict[str, float] = defaultdict(float)
    cur = "header"
    for ln in lines:
        k = keyfn(ln)
        cur = k if k is not None else cur
        acc[cur] += total * (len(ln) + 1) / n
    return list(acc.items())


def attachment_items(r: dict, a: dict, ctx: Context) -> list[tuple]:
    """(comp, src, tag, tok) for an attachment entry, from what reaches the model (`rendered`)."""
    t = a.get("type") or ""
    txt = rendered_text(r) if "rendered" in r else ""
    out: list[tuple] = []
    if t == "deferred_tools_delta":
        tok = ntok(txt)

        def key(ln: str) -> str:
            s = ln.strip()
            srv = mcp_server(s)
            return ("mcp:" + srv) if srv else ("builtin" if re.fullmatch(r"[A-Za-z]\w*", s or "-") else "header")
        for src, tk in _lines_split(txt, tok, key):
            if src.startswith("mcp:"):
                out.append(("mcp_listing", src[4:], "", tk))
            else:
                out.append(("deferred_builtin_listing", src, "", tk))
        if "<function>" not in txt:
            for d in a.get("surfacedDefinitions") or []:
                if isinstance(d, dict):
                    name = d.get("name") or ""
                    out.append(("mcp_schema" if mcp_server(name) else "tools_builtin", mcp_server(name) or name, "",
                                ntok(json.dumps(d.get("definition") or d, ensure_ascii=False))))
        return out
    if not txt:
        return out
    tok = ntok(txt)
    if t == "instructions":
        files = [f for f in a.get("files") or [] if isinstance(f, dict)]
        n = sum(len(f.get("content") or "") for f in files) or 1
        for f in files:
            comp = "memory" if (f.get("type") == "AutoMem" or "/memory/" in (f.get("path") or "")) else "claude_md"
            out.append((comp, f.get("path") or "", "", tok * len(f.get("content") or "") / n))
        return out or [("claude_md", "", "", tok)]
    if t == "nested_memory":
        return [("claude_md", a.get("path") or "", "", tok)]
    if t == "skill_listing":
        def key(ln: str) -> str | None:
            m = re.match(r"- ([^:\s]+(?::[^:\s]+)?):", ln)
            if not m:
                return None if ln.startswith((" ", "\t")) or not ln.strip() else "header"
            nm = m.group(1)
            return (plugin_of(nm) or ("user" if nm in ctx.user_skills else "built-in")) + "|" + nm
        return [("skills_listing", src, "", tk) for src, tk in _lines_split(txt, tok, key)]
    if t == "agent_listing_delta":
        def key(ln: str) -> str | None:
            m = re.match(r"- ([^:\s]+(?::[^:\s]+)?):", ln)
            if not m:
                return None if ln.startswith((" ", "\t")) or not ln.strip() else "header"
            nm = m.group(1)
            return (plugin_of(nm) or ("user" if nm in ctx.user_agents else "built-in")) + "|" + nm
        return [("agents_listing", src, "", tk) for src, tk in _lines_split(txt, tok, key)]
    if t == "mcp_instructions_delta":
        blocks = [b for b in a.get("addedBlocks") or [] if isinstance(b, str)]
        names = [n for n in a.get("addedNames") or [] if isinstance(n, str)]
        n = sum(len(b) for b in blocks) or 1
        for i, b in enumerate(blocks):
            nm = names[i] if i < len(names) else "?"
            out.append(("mcp_instructions", nm, "", tok * len(b) / n))
        return out or [("mcp_instructions", "", "", tok)]
    if t in ("hook_success", "hook_additional_context", "hook_system_message", "hook_non_blocking_error",
             "hook_blocking_error"):
        ev = a.get("hookEvent") or (a.get("hookName") or "").split(":")[0] or "hook"
        plug = ctx.hook_cmd_plugin.get(a.get("command") or "", "")
        if not plug:
            m = re.match(r"\s*(?:\[|<!--\s*)([\w.@-]{1,40})[\]:]", txt.split("context:", 1)[-1])
            plug = m.group(1) if m else ""
        tag = "spilled" if "<persisted-output>" in txt[:400] else ""
        return [("hooks", ev + (" / " + plug if plug else ""), tag, tok)]
    if t in ("invoked_skills", "dynamic_skill"):
        return [("skill_body", t, "", tok)]
    if t == "queued_command":
        return [("user_text", "queued", "", tok)]
    if t == "file":
        fn = a.get("filename") or ""
        return [("memory" if "/memory/" in fn else "attached_file", "recalled" if "/memory/" in fn else "", "", tok)]
    return [("reminders", t, "", tok)]


def assistant_pass(path: Path) -> dict[str, dict]:
    """Pass 1: per message.id -> output tokens, visible tokens, thinking block count."""
    msgs: dict[str, dict] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") != "assistant":
                continue
            m = r.get("message") or {}
            mid = m.get("id")
            if not mid:
                continue
            d = msgs.setdefault(mid, {"out": 0, "text": 0.0, "call": 0.0, "nthink": 0, "model": m.get("model", "")})
            d["out"] = max(d["out"], (m.get("usage") or {}).get("output_tokens") or 0)
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    d["text"] += ntok(b.get("text") or "")
                elif bt in ("tool_use", "server_tool_use"):
                    d["call"] += ntok((b.get("name") or "") + json.dumps(b.get("input") or {}, ensure_ascii=False))
                elif bt in ("thinking", "redacted_thinking"):
                    d["nthink"] += 1
                    d["think_text"] = d.get("think_text", 0.0) + ntok(b.get("thinking") or "")
    for d in msgs.values():
        vis = d["text"] + d["call"]
        d["think"] = max(d.get("think_text", 0.0), d["out"] - vis) if d["nthink"] else 0.0
    return msgs


_IS_VIEW = None


def _is_view(cmd: str) -> bool:
    """ctxprune's own file-view test (lossless.is_view) when the repo is importable, else a small fallback."""
    global _IS_VIEW
    if _IS_VIEW is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from lossless import is_view
            _IS_VIEW = is_view
        except Exception:
            _IS_VIEW = lambda c: bool(re.match(r"\s*(cat|head|tail|sed -n|nl|less)\b", c or ""))
    try:
        return bool(_IS_VIEW(cmd))
    except Exception:
        return False


def tool_result_items(b: dict, name: str, cmd: str, tur: dict) -> list[tuple]:
    x = b.get("content")
    texts, imgs = [], 0.0
    if isinstance(x, str):
        texts.append(x)
    elif isinstance(x, list):
        for y in x:
            if not isinstance(y, dict):
                continue
            if y.get("type") == "text":
                texts.append(y.get("text") or "")
            elif y.get("type") == "image":
                imgs += image_tokens(((y.get("source") or {}).get("data")) or "")
            elif y.get("type") == "tool_reference":
                texts.append(y.get("tool_name") or "")
    text = "\n".join(texts)
    text, rem = split_reminders(text)
    srv = mcp_server(name)
    src = ("mcp:" + srv) if srv else (name or "?")
    spilled = bool(tur.get("persistedOutputPath")) or any(m in text[:400] for m in SPILL_MARKERS)
    tag = ""
    if spilled:
        tag = "spilled"
    elif name in ("Bash", "Grep", "PowerShell") or srv:
        view = name == "Bash" and _is_view(cmd)
        if view:
            tag = "view"
        elif len(text) >= LOSSY_MIN_CHARS:
            tag = "lossy"
    items = [("tool", src, tag, ntok(text) + imgs)]
    items += [(c, s, "", t) for c, s, t in rem]
    return items


def parse_stream(path: Path, ctx: Context, entries_only: bool = False) -> Stream | None:
    """entries_only: build the entry map (and last snapshot) without pricing requests; used for a fork's parent."""
    amsg = assistant_pass(path)
    st = Stream(ctx)
    tools: dict[str, tuple[str, str]] = {}
    seen_req: set[str] = set()
    fork_tail: str | None = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            typ = r.get("type")
            if typ == "fork-context-ref":
                # a forked sub-agent starts from its parent's context: graft the parent's entries in front
                st.forked = True
                pfile = path.parents[2] / f"{r.get('parentSessionId')}.jsonl" if len(path.parents) > 2 else None
                parent = ctx.parent_stream(pfile) if pfile and not entries_only else None
                if parent is not None and r.get("parentLastUuid") in parent.entries:
                    st.entries.update(parent.entries)
                    if parent.static_from_snapshot:
                        st.static, st.static_from_snapshot = list(parent.static), True
                    fork_tail = r.get("parentLastUuid")
                    st.forked = False
                continue
            a = r.get("attachment") if isinstance(r.get("attachment"), dict) else None
            if a is not None and a.get("type") == "prompt_snapshot":
                items = snapshot_items(a)
                if items is not None:
                    st.static = items
                    st.static_from_snapshot = True
                if r.get("uuid"):
                    st.entries[r["uuid"]] = (r.get("parentUuid"), [], 0.0)
                continue
            uid = r.get("uuid")
            if not uid:
                continue
            par = r.get("parentUuid")
            if par is None and fork_tail is not None:
                par, fork_tail = fork_tail, None
            items: list[tuple] = []
            m = r.get("message") if isinstance(r.get("message"), dict) else {}
            if a is not None:
                items = attachment_items(r, a, ctx)
                if a.get("type") == "invoked_skills":
                    for sk in a.get("skills") or []:
                        if isinstance(sk, dict) and sk.get("name"):
                            st.used["skill"][str(sk["name"])] += 1     # re-listed after compaction = it ran
            
            elif typ == "assistant":
                mid = m.get("id")
                d = amsg.get(mid) or {}
                u = m.get("usage")
                model = str(m.get("model", ""))
                if mid and u and mid not in seen_req and model.startswith("claude") and not entries_only:
                    seen_req.add(mid)
                    out = d.get("out", 0)
                    st.request(par, u, _ts(r.get("timestamp")), out)
                    st.billed["out"] += out
                    st.bill_usd += out * P_OUT / 1e6
                    vis = d.get("text", 0.0) + d.get("call", 0.0)
                    th = d.get("think", 0.0)
                    sc = min(1.0, out / vis) if vis else 0.0
                    st.out_cost["thinking"] += th * P_OUT / 1e6 if out else 0.0
                    st.out_cost["text"] += d.get("text", 0.0) * sc * P_OUT / 1e6
                    st.out_cost["tool_call"] += d.get("call", 0.0) * sc * P_OUT / 1e6
                for b in m.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        items.append(("asst_text", "", "", ntok(b.get("text") or "")))
                    elif bt in ("tool_use", "server_tool_use"):
                        name = b.get("name") or ""
                        inp = b.get("input") or {}
                        cmd = str(inp.get("command") or "") if isinstance(inp, dict) else ""
                        tools[b.get("id")] = (name, cmd)
                        items.append(("tool_call", mcp_server(name) and "mcp:" + mcp_server(name) or name, "",
                                      ntok(name + json.dumps(inp, ensure_ascii=False))))
                        st.used["tool"][name] += 1
                        if mcp_server(name):
                            st.used["mcp"][mcp_server(name)] += 1
                        if name == "Skill" and isinstance(inp, dict):
                            st.used["skill"][str(inp.get("skill") or "")] += 1
                        if name in ("Agent", "Task") and isinstance(inp, dict):
                            st.used["agent"][str(inp.get("subagent_type") or "general-purpose")] += 1
                    elif bt in ("thinking", "redacted_thinking"):
                        n = d.get("nthink") or 1
                        items.append(("thinking", "", "", d.get("think", 0.0) / n))
                    elif bt.endswith("_tool_result"):
                        items.append(("tool", bt.replace("_tool_result", ""), "",
                                      ntok(json.dumps(b.get("content"), ensure_ascii=False))))
            elif typ == "user":
                c = m.get("content")
                tur = r.get("toolUseResult") if isinstance(r.get("toolUseResult"), dict) else {}
                if r.get("isCompactSummary"):
                    items.append(("compact_summary", "", "", ntok(c if isinstance(c, str) else json.dumps(c))))
                elif isinstance(c, str):
                    items += _user_text_items(c, bool(r.get("isMeta")))
                    for cmd in re.findall(r"<command-name>/?([^<\s]{1,80})</command-name>", c[:2000]):
                        st.used["skill"][cmd] += 1                       # a slash command = a skill/command use
                elif isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            name, cmd = tools.get(b.get("tool_use_id"), ("", ""))
                            items += tool_result_items(b, name, cmd, tur)
                        elif b.get("type") == "text":
                            items += _user_text_items(b.get("text") or "", bool(r.get("isMeta")))
                        elif b.get("type") == "image":
                            items.append(("user_text", "image", "", image_tokens(((b.get("source") or {}).get("data")) or "")))
            st.entries[uid] = (par, items, sum(x[-1] for x in items))
    if entries_only:
        return st
    st.finish()
    return st if st.requests else None


def _ts(s) -> float | None:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _user_text_items(text: str, meta: bool) -> list[tuple]:
    rest, rem = split_reminders(text)
    items = [(c, s, "", t) for c, s, t in rem]
    if rest.strip():
        if meta:
            kind = "skill_body" if rest.lstrip().startswith("Base directory for this skill") else "meta"
            items.append((kind, "", "", ntok(rest)))
        else:
            items.append(("user_text", "", "", ntok(rest)))
    return items


# ------------------------------------------------------------------------------------------------ run + records
def stream_files(root: Path, days: float, exclude: list[str]) -> list[tuple[Path, str]]:
    cut = time.time() - days * 86400
    out = []
    for p in sorted(root.rglob("*.jsonl")):
        try:
            if p.stat().st_mtime < cut:
                continue
        except OSError:
            continue
        rel = p.relative_to(root).parts
        proj = rel[0] if len(rel) > 1 else ""
        if any(fnmatch.fnmatch(proj.lower(), e.lower()) for e in exclude):
            continue
        if "subagents" not in rel and len(rel) > 2:
            continue                                  # tool-results/, skill-injections etc. are not streams
        out.append((p, proj))
    return out


def stream_record(st: Stream, path: Path, proj: str) -> dict:
    return {
        "file": str(path), "project": proj, "subagent": "subagents" in path.parts, "requests": st.requests,
        "cold": st.cold, "rewrite_w": st.rewrite_w, "rewrite_n": st.rewrite_n, "billed": st.billed, "bill_usd": st.bill_usd, "snapshot": st.static_from_snapshot,
        "first_req": st.first_req, "calib": st.calib, "req_log": st.req_log,
        "static_tok_med": sorted(st.static_tok_hist)[len(st.static_tok_hist) // 2] if st.static_tok_hist else 0,
        "cost": [[k[0], k[1], k[2], v] for k, v in st.cost.items()],
        "carry": [[k[0], k[1], k[2], v] for k, v in st.carry.items()],
        "out_cost": dict(st.out_cost), "used": {k: dict(v) for k, v in st.used.items()},
        "mtime": path.stat().st_mtime,
    }


def run(root: Path, days: float, exclude: list[str], home: Path, records_out: str | None = None,
        progress: bool = False) -> list[dict]:
    ctx = Context(home)
    recs = []
    files = stream_files(root, days, exclude)
    t0 = time.time()
    fh = open(records_out, "w") if records_out else None
    for i, (p, proj) in enumerate(files):
        try:
            st = parse_stream(p, ctx)
        except Exception as e:                          # a broken transcript must not stop the audit
            print(f"skip {p.name}: {type(e).__name__}", file=sys.stderr)
            continue
        if st is None:
            continue
        rec = stream_record(st, p, proj)
        recs.append(rec)
        if fh:
            fh.write(json.dumps(rec) + "\n")
        if progress and i % 50 == 0:
            print(f"[{i}/{len(files)}] {time.time() - t0:.0f}s", file=sys.stderr)
    if fh:
        fh.close()
    return recs


# ------------------------------------------------------------------------------------------------ aggregation
def aggregate(recs: list[dict]) -> dict:
    cost: dict[tuple, float] = defaultdict(float)
    carry: dict[tuple, float] = defaultdict(float)
    out_cost: dict[str, float] = defaultdict(float)
    bill = 0.0
    billed = defaultdict(float)
    req = cold = 0
    used = {k: defaultdict(int) for k in ("mcp", "skill", "agent", "tool")}
    for r in recs:
        for c, s, t, v in r["cost"]:
            cost[(c, s, t)] += v
        for c, s, t, v in r["carry"]:
            carry[(c, s, t)] += v
        for k, v in r["out_cost"].items():
            out_cost[k] += v
        bill += r["bill_usd"]
        for k, v in r["billed"].items():
            billed[k] += v
        req += r["requests"]
        cold += r["cold"]
        for k, d in r["used"].items():
            for n, v in d.items():
                used[k][n] += v
    in_bill = billed["in"] * P_IN / 1e6 + billed.get("w_usd", billed["w"] * P_W / 1e6) + billed["r"] * P_R / 1e6
    attributed_in = sum(cost.values())
    return {"cost": cost, "carry": carry, "out_cost": out_cost, "bill": bill, "in_bill": in_bill,
            "out_bill": billed["out"] * P_OUT / 1e6, "billed": dict(billed), "requests": req, "cold": cold,
            "unattributed_in": in_bill - attributed_in, "used": used, "streams": len(recs)}


def breakdown_rows(agg: dict) -> list[tuple[str, float, float]]:
    """(component, $ , % of bill). Tool results are split per tool (top 8 + rest)."""
    bill = agg["bill"] or 1.0
    by: dict[str, float] = defaultdict(float)
    tools: dict[str, float] = defaultdict(float)
    for (c, s, t), v in agg["cost"].items():
        if c == "tool":
            tools[s.split(":")[0] if s.startswith("mcp:") else s] += v
        else:
            by[GROUPS.get(c, c)] += v
    rows = [(k, v, 100 * v / bill) for k, v in sorted(by.items(), key=lambda kv: -kv[1])]
    tl = sorted(tools.items(), key=lambda kv: -kv[1])
    for k, v in tl[:8]:
        rows.append(("tool results: " + k, v, 100 * v / bill))
    rest = sum(v for _, v in tl[8:])
    rows.append(("tool results: other", rest, 100 * rest / bill))
    rows.append(("input, unattributed (estimate gap)", agg["unattributed_in"], 100 * agg["unattributed_in"] / bill))
    for k in ("thinking", "text", "tool_call"):
        v = agg["out_cost"].get(k, 0.0)
        rows.append(("output: " + k, v, 100 * v / bill))
    v = agg["out_bill"] - sum(agg["out_cost"].values())
    rows.append(("output: unattributed", v, 100 * v / bill))
    return rows


# ------------------------------------------------------------------------------------------------ suggestions
STATIC_SOURCES = ("tools_mcp", "mcp_listing", "mcp_instructions", "mcp_schema", "skills_listing", "agents_listing",
                  "hooks", "claude_md", "memory", "memory_instr")
MIN_SUGGEST_PCT = 0.02          # suggestions below this share of the bill are summarised in one line

# Which config changes were VERIFIED to remove tokens from the request (Claude Code 2.1.302, local stub API in a
# throwaway HOME, `claude -p`, request bodies diffed: results/billmap/stubcheck/README.md). A suggestion whose mechanism
# is not in here is printed as [unverified] and left out of the savings total.
#   permissions.deny with a bare built-in tool name -> the schema leaves `tools` (all 18 tools of an API-key session)
#   disabledBuiltinTools in ~/.claude/settings.json  -> NO change (it is a workspace/managed key)
#   permissions.deny "Skill(name)"                   -> NO change to the skill listing
#   skillOverrides {"name": "off" | "user-invocable-only"} -> the listing line goes
#   disableBundledSkills / CLAUDE_CODE_DISABLE_BUNDLED_SKILLS -> bundled lines go, but the Workflow tool description
#       grows by 17.4 KB: the request gets 11.6 KB BIGGER. Never suggested.
#   MCP server removed / plugin not loaded / SessionStart hook removed / CLAUDE.md content -> its tokens go
#   disableArtifact, deny "Artifact"                 -> NOT testable: Artifact is absent from API-key sessions
VERIFIED_DENY_TOOLS = frozenset({
    "Agent", "CronCreate", "CronDelete", "CronList", "Edit", "EnterWorktree", "ExitWorktree", "ListAgents",
    "NotebookEdit", "ReportFindings", "ScheduleWakeup", "SendMessage", "Skill", "TaskStop", "WebFetch", "WebSearch",
    "Workflow", "Write"})
VERIFIED = {
    "unused MCP server": "remove the server from its MCP config",
    "rarely used MCP server": "move the server to the configs of the projects that use it",
    "unused plugin": "set the plugin to false in enabledPlugins",
    "unused user skills": 'skillOverrides {"<name>": "off"} (or delete the skill dir)',
    "unused built-in/bundled skills": 'skillOverrides {"<name>": "off"} per skill; NOT disableBundledSkills (grows '
                                      'the request)',
    "large hook injection": "remove or shorten the hook's output",
    "large CLAUDE.md / memory (halved)": "trim the file",
    "unused built-in tool schema": 'permissions.deny ["<Tool>"] (bare name)',
    "rarely used built-in tool schema": 'permissions.deny ["<Tool>"] (bare name)',
}


def _server_match(instr_name: str, servers: set[str]) -> str:
    n = norm(instr_name)
    for s in sorted(servers):
        if norm(s) == n:
            return s
    for s in sorted(servers, key=len):
        if norm(s).endswith("_" + n):
            return s
    return instr_name


def _servers(agg: dict) -> set[str]:
    """Real MCP servers: names seen in a tool schema, a deferred-tool listing or a call."""
    out = {s for (c, s, t) in agg["cost"] if c in ("tools_mcp", "mcp_listing", "mcp_schema") and s and s != "inline"}
    out |= {s[4:] for (c, s, t) in agg["cost"] if c in ("tool", "tool_call") and s.startswith("mcp:")}
    return out


def source_key(c: str, s: str, servers: set[str]) -> tuple[str, str] | None:
    """(kind, name) a cost item belongs to in the per-source view, or None for conversation content."""
    if c in ("tools_mcp", "mcp_listing", "mcp_schema"):
        return ("MCP server", s or "?")
    if c == "mcp_instructions":
        return ("MCP server", _server_match(s, servers) if s else "(instructions header)")
    if c in ("tool", "tool_call") and s.startswith("mcp:"):
        return ("MCP calls + results", s[4:])
    if c in ("skills_listing", "agents_listing"):
        grp = s.split("|", 1)[0]
        what = " skills" if c == "skills_listing" else " agents"
        if grp in ("user", "built-in", "header"):
            return ("listing", grp + what)
        return ("plugin listing", grp + what)
    if c == "hooks":
        return ("hook injection", s)
    if c == "claude_md":
        return ("CLAUDE.md", s or "(inline)")
    if c in ("memory", "memory_instr"):
        return ("memory", "memory system prompt" if c == "memory_instr" else "MEMORY.md + recalled memory files")
    if c == "skill_body":
        return ("skill bodies", s)
    if c == "system_prompt":
        return ("built-in", "system prompt")
    if c == "tools_builtin":
        return ("built-in", "tool schemas")
    if c == "deferred_builtin_listing":
        return ("built-in", "deferred tool names")
    if c == "static_inferred":
        return ("built-in", "system + tools (inferred, no snapshot)")
    return None


def sources(agg: dict, days: float) -> list[dict]:
    """Per (kind, name): $ over the window, $/month, % of bill, tokens carried per request."""
    req = agg["requests"] or 1
    k30 = 30.0 / days
    servers = _servers(agg)
    acc: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for (c, s, t), v in agg["cost"].items():
        key = source_key(c, s, servers)
        if key is None:
            continue
        acc[key][0] += v
        acc[key][1] += agg["carry"].get((c, s, t), 0.0)
    rows = [{"kind": k, "name": n, "usd": usd, "usd_month": usd * k30, "pct_bill": 100 * usd / (agg["bill"] or 1),
             "tok_per_req": carry / req} for (k, n), (usd, carry) in acc.items()]
    rows.sort(key=lambda r: -r["usd"])
    return rows


def _named(agg: dict, comp: str) -> dict[str, list[float]]:
    """Per-name [$, carried tokens] of one listing component (src 'group|name')."""
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for (c, s, t), v in agg["cost"].items():
        if c == comp:
            out[s][0] += v
            out[s][1] += agg["carry"].get((c, s, t), 0.0)
    return out


def suggestions(agg: dict, days: float, enabled_plugins: list[str] | None = None) -> list[dict]:
    """Concrete, non-overlapping advice; `usd` is the measured positional cost of the blocks it would remove."""
    srcs = sources(agg, days)
    used = agg["used"]
    req = agg["requests"] or 1
    bill = agg["bill"] or 1.0
    out: list[dict] = []

    def add(what, cat, usd, tpr, n=1, verified=None):
        ok = (cat in VERIFIED) if verified is None else verified
        out.append({"what": what, "category": cat, "usd": usd, "pct_bill": 100 * usd / bill, "tok_per_req": tpr,
                    "n": n, "verified": ok, "how": VERIFIED.get(cat, "") if ok else ""})
    servers = _servers(agg)
    # 1. MCP servers: never called -> remove; rarely called but expensive -> info with its cost
    for r in srcs:
        if r["kind"] != "MCP server" or r["name"] not in servers or r["usd"] <= 0:
            continue
        calls = used["mcp"].get(r["name"], 0)
        if calls == 0:
            add(f"remove/disable MCP server '{r['name']}': 0 calls in {days:g} days, {r['tok_per_req']:.0f} "
                f"tokens/request of names + instructions", "unused MCP server", r["usd"], r["tok_per_req"])
        elif calls < 10 and r["pct_bill"] >= 0.1:
            add(f"MCP server '{r['name']}' was called {calls}x in {days:g} days but its listing costs "
                f"{r['tok_per_req']:.0f} tokens/request: enable it only in the projects that use it",
                "rarely used MCP server", r["usd"], r["tok_per_req"])
    # 2. plugins: skills / agents never invoked -> disable the plugin (listing + its hooks)
    skills_used = {k for k, v in used["skill"].items() if v}
    agents_used = {k for k, v in used["agent"].items() if v}
    sk, ag = _named(agg, "skills_listing"), _named(agg, "agents_listing")
    plug: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0])
    for comp, d, usedset in (("skills", sk, skills_used), ("agents", ag, agents_used)):
        for key, (usd, carry) in d.items():
            grp, _, name = key.partition("|")
            if grp in ("user", "built-in", "header") or not name:
                continue
            plug[grp][0] += usd
            plug[grp][1] += carry / req
            plug[grp][2] += name in usedset or name.split(":", 1)[-1] in usedset
    hooks = {r["name"]: r for r in srcs if r["kind"] == "hook injection"}
    claimed_hooks = set()
    for p, (usd, tpr, n_used) in plug.items():
        hk = [h for h in hooks if h.endswith("/ " + p)]
        if n_used == 0:
            husd = sum(hooks[h]["usd"] for h in hk)
            htpr = sum(hooks[h]["tok_per_req"] for h in hk)
            claimed_hooks |= set(hk)
            add(f"disable plugin '{p}': none of its skills/agents ran in {days:g} days; removes {tpr:.0f} tokens/"
                f"request of listings" + (f" and {htpr:.0f} of hook injections" if hk else ""), "unused plugin",
                usd + husd, tpr + htpr)
    # 3. user / built-in skills never invoked (listing lines)
    for grp, cat in (("user", "unused user skills"), ("built-in", "unused built-in/bundled skills")):
        rows = [(k.partition("|")[2], usd, carry / req) for k, (usd, carry) in sk.items() if k.startswith(grp + "|")]
        idle = [x for x in rows if x[0] not in skills_used]
        if idle:
            usd = sum(x[1] for x in idle)
            tpr = sum(x[2] for x in idle)
            add(f"{len(idle)} of {len(rows)} {grp} skills were never invoked in {days:g} days; their listing lines "
                f"cost {tpr:.0f} tokens/request", cat, usd, tpr, len(idle))
    # 4. hook injections not covered by a plugin above
    for name, r in hooks.items():
        if name in claimed_hooks or r["usd"] <= 0:
            continue
        add(f"hook injection '{name}' adds {r['tok_per_req']:.0f} tokens/request", "large hook injection",
            r["usd"], r["tok_per_req"])
    # 5. CLAUDE.md / memory index over 1,500 tokens per request: trimming half of it
    for r in srcs:
        if r["kind"] in ("CLAUDE.md", "memory") and r["tok_per_req"] >= 1500:
            add(f"{r['kind']} '{r['name']}' is {r['tok_per_req']:.0f} tokens/request (savings shown for halving it)",
                "large CLAUDE.md / memory (halved)", r["usd"] / 2, r["tok_per_req"] / 2)
    # 6. built-in tools whose schema rides on every request but that were never called
    bt = _named(agg, "tools_builtin")
    for name, (usd, carry) in sorted(bt.items(), key=lambda kv: -kv[1][0]):
        calls = used["tool"].get(name, 0)
        ok = name in VERIFIED_DENY_TOOLS
        hint = "" if ok else " (no setting verified to remove this tool's schema; check /context)"
        if calls == 0 and 100 * usd / bill >= 0.1:
            add(f"built-in tool '{name}' was never called in {days:g} days; its schema is {carry / req:.0f} tokens/"
                f"request{hint}", "unused built-in tool schema", usd, carry / req, verified=ok)
        elif calls * 1000 < req and 100 * usd / bill >= 0.5:
            add(f"built-in tool '{name}' was called {calls}x in {req:,} requests but its schema is {carry / req:.0f} "
                f"tokens/request{hint}", "rarely used built-in tool schema", usd, carry / req, verified=ok)
    fk = sum(v for (c, s, t), v in agg["cost"].items() if c == "fork_inherited")
    if 100 * fk / bill >= 0.5:
        add(f"forked sub-agents re-send the parent's context: {100 * fk / bill:.1f}% of the bill "
            f"(${fk * 30 / days:,.0f}/month); a fresh sub-agent with a short brief avoids it when the task does not "
            f"need the history", "forked sub-agents (info)", 0.0, 0.0)
    # 7. duplicate plugins (same base name from two marketplaces)
    if enabled_plugins:
        base = defaultdict(list)
        for p in enabled_plugins:
            base[re.sub(r"-plugin$", "", p.split("@")[0])].append(p)
        for b, ps in base.items():
            if len(ps) > 1:
                add(f"{len(ps)} plugins with the same base name '{b}' are enabled: " + ", ".join(ps) +
                    " (their listings and hooks are counted under each name above)", "duplicate plugin", 0.0, 0.0)
    big = [s for s in out if s["pct_bill"] >= MIN_SUGGEST_PCT or s["category"] == "duplicate plugin"]
    small = [s for s in out if s not in big]
    if small:
        vs = [x for x in small if x["verified"]]
        add(f"{len(small)} more items under {MIN_SUGGEST_PCT}% of the bill each ("
            + ", ".join(sorted({x['category'] for x in small})) + ")", "small items", sum(x["usd"] for x in vs),
            sum(x["tok_per_req"] for x in vs), sum(x["n"] for x in small), verified=True)
        big.append(out[-1])
    big.sort(key=lambda s: -s["usd"])
    return big


# ------------------------------------------------------------------------------------------------ cache-TTL advisor
# Folded from research/strategy/kasim.py + billscan.py: re-prices the user's LOGGED usage under other cache TTLs,
# per side (main thread vs sub-agents), from their own request gaps. Exact for the logged requests; causal for
# keep-alive (pings charged, including after a stream's last request).
#   5m  : every write at $5; a request after a 5-60 min gap that was served from a 1 h cache becomes a rewrite of
#         what it read.
#   1h  : every write at $8; a rewrite (T >= 20k, W > T/2) after a 5-60 min gap becomes a read of the previous prompt.
#   5m+KA(K): 5m, plus a ping (cache read of the last prompt) every 240 s for up to K min after each response;
#         rewrites after gaps < K*60+300 s become reads; reads after longer 1h-served gaps become rewrites.
# Settings verified on a stub to change the request's cache_control ttl (results/billmap/stubcheck, request side
# only: whether the API bills the ttl as asked is not checked): promptCacheTtl "1h" (API key) and "5m" (OAuth /
# subscriber mode, whose default is 1h); subagentPromptCacheTtl "1h" (sub-agent requests only); sub-agents are 5m by
# default in both modes. Keep-alive has no setting and is reported as info only.
# Round-2 safety change (research/strategy/round2): the stub only showed the setting changes the request's ttl, not how
# it is billed. API key: advice kept, [unverified] and out of the total until the API billing check runs (TTL_VERIFIED
# empty). Subscription (OAuth) / unknown auth: NO advice at all (how plan quota counts 1h vs 5m writes is unverified
# and public sources conflict; the owner won't spend quota on it), only a "check it yourself" footnote.
TTL_VERIFIED: set = set()
PING_S = 240.0


def _policy_costs(rs: list[tuple], ka_min: float = 20.0) -> dict[str, float]:
    """rs = sorted [(t, fresh, read, write, write_1h, out)] of one stream -> $ under each policy."""
    c = {"actual": 0.0, "5m": 0.0, "1h": 0.0, "5m+KA": 0.0}
    cap = ka_min * 60.0
    for k, (t, i, r, w, w1, o) in enumerate(rs):
        T = i + r + w
        base = (i * P_IN + r * P_R + o * P_OUT) / 1e6
        c["actual"] += base + ((w - w1) * P_W + w1 * P_W1) / 1e6
        c["5m"] += base + w * P_W / 1e6
        c["1h"] += base + w * P_W1 / 1e6
        c["5m+KA"] += base + w * P_W / 1e6
        if k == 0 or t is None or rs[k - 1][0] is None:
            continue
        pt, pi, pr, pw_, _, _ = rs[k - 1]
        pT = pi + pr + pw_
        gap = t - pt
        rew = T >= 20000 and w > 0.5 * T
        n_ping = int(min(gap, cap) // PING_S)
        c["5m+KA"] += n_ping * pT * P_R / 1e6
        if 300 <= gap < 3600:
            if rew:
                c["1h"] -= min(w, pT) * (P_W1 - P_R) / 1e6
                if gap < cap + 300:
                    c["5m+KA"] -= min(w, pT) * (P_W - P_R) / 1e6
            else:
                c["5m"] += min(r, pT) * (P_W - P_R) / 1e6
                if gap >= cap + 300:
                    c["5m+KA"] += min(r, pT) * (P_W - P_R) / 1e6
    if rs and rs[-1][0] is not None:                       # pings after the last request are charged too
        t, i, r, w, _, _ = rs[-1]
        c["5m+KA"] += int(cap // PING_S) * (i + r + w) * P_R / 1e6
    return c


def ttl_advice(recs: list[dict]) -> dict:
    """Per side: $ under each policy, the cheapest SETTABLE policy (5m / 1h) and its saving vs actual."""
    side_c = {"main": defaultdict(float), "sub": defaultdict(float)}
    w1 = {"main": [0.0, 0.0], "sub": [0.0, 0.0]}
    for r in recs:
        side = "sub" if r.get("subagent") else "main"
        rs = sorted((tuple(x) for x in r.get("req_log") or []), key=lambda x: (x[0] is None, x[0] or 0))
        for k, v in _policy_costs(rs).items():
            side_c[side][k] += v
        for x in rs:
            w1[side][0] += x[3]
            w1[side][1] += x[4]
    out = {}
    for side, c in side_c.items():
        if not c:
            continue
        best = min(("5m", "1h"), key=lambda p: c[p])
        now = "1h" if w1[side][1] > 0.5 * (w1[side][0] or 1) else "5m"
        save = max(0.0, c["actual"] - c[best]) if best != now else 0.0
        out[side] = {"now": now, "best": best, "usd": dict(c), "save_usd": save,
                     "verified": (side, best) in TTL_VERIFIED,
                     "setting": ("promptCacheTtl" if side == "main" else "subagentPromptCacheTtl") + f' "{best}"',
                     "keepalive_save_usd": max(0.0, c["actual"] - c["5m+KA"])}
    return out


def auth_mode(home: Path) -> str:
    """"api_key" (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN set, or an apiKeyHelper in settings), "subscription"
    (Claude Code OAuth credentials file present), else "unknown". Only key presence is checked; nothing is printed."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "api_key"
    if (read_json(home / ".claude/settings.json") or {}).get("apiKeyHelper"):
        return "api_key"
    cred = read_json(home / ".claude/.credentials.json")
    if isinstance(cred, dict) and "claudeAiOauth" in cred:
        return "subscription"
    return "unknown"


def enabled_plugins(home: Path) -> list[str]:
    s = read_json(home / ".claude/settings.json") or {}
    return [k for k, v in (s.get("enabledPlugins") or {}).items() if v]


def report(agg: dict, days: float, home: Path, tok: str, recs: list[dict] | None = None,
           mode: str | None = None) -> str:
    k30 = 30.0 / days
    L = []
    L.append(f"ctxprune audit: last {days:g} days, {agg['streams']} transcript streams, {agg['requests']:,} requests "
             f"(~{agg['requests'] * k30:,.0f}/month). Tokens: {tok}.")
    L.append(f"Bill at Opus 5.5 list prices: ${agg['bill']:,.2f} (~${agg['bill'] * k30:,.0f}/month); input side "
             f"{100 * agg['in_bill'] / (agg['bill'] or 1):.0f}%, cold prefix writes on {agg['cold']:,} requests.")
    L.append("")
    L.append("Where the bill goes")
    for name, usd, pct in breakdown_rows(agg):
        if abs(pct) >= 0.05:
            L.append(f"  {name:<62} {pct:6.1f}%   ${usd * k30:>9,.0f}/mo")
    L.append("")
    L.append("Static and injected context by source (tokens carried per request, cost per month)")
    for r in sources(agg, days)[:25]:
        if r["usd"] <= 0:
            continue
        L.append(f"  {r['kind']:<26} {r['name'][:44]:<44} {r['tok_per_req']:8,.0f} tok/req  "
                 f"${r['usd_month']:>8,.0f}/mo  {r['pct_bill']:5.2f}%")
    L.append("")
    L.append("Suggestions (advice only; nothing was changed)")
    sug = suggestions(agg, days, enabled_plugins(home))
    tot = sum(s["usd"] for s in sug if s["verified"])
    unv = sum(s["usd"] for s in sug if not s["verified"])
    for s in sug:
        save = f"  -> -${s['usd'] * k30:,.0f}/mo (-{s['pct_bill']:.2f}% of bill)" if s["usd"] > 0 else ""
        tag = "" if s["verified"] or not s["usd"] else "[unverified, not in total] "
        how = f"  [how: {s['how']}]" if s.get("how") and s["usd"] > 0 else ""
        L.append(f"  - {tag}{s['what']}{save}{how}")
    mode = mode or auth_mode(home)
    ttl = ttl_advice(recs) if recs is not None and mode == "api_key" else {}   # never for a subscription (unverified)
    for side, v in ttl.items():
        if v["save_usd"] > 0:
            tag = "" if v["verified"] else "[unverified, not in total] "
            L.append(f"  - {tag}cache TTL ({side}): your gaps make {v['best']} cheaper than today's {v['now']}: set "
                     f"{v['setting']}  -> -${v['save_usd'] * k30:,.0f}/mo (-{100 * v['save_usd'] / (agg['bill'] or 1):.2f}% "
                     f"of bill)")
            if v["verified"]:
                tot += v["save_usd"]
            else:
                unv += v["save_usd"]
        if v["keepalive_save_usd"] > v["save_usd"]:
            L.append(f"  - (info) {side}: 5m + a keep-alive ping every 4 min for <=20 min would save "
                     f"{100 * v['keepalive_save_usd'] / (agg['bill'] or 1):.2f}% (no Claude Code setting does this)")
    L.append(f"  Total, verified mechanisms only: -${tot * k30:,.0f}/month (-{100 * tot / (agg['bill'] or 1):.1f}% of "
             f"bill); unverified items: -{100 * unv / (agg['bill'] or 1):.1f}% more if they work")
    if recs is not None and mode != "api_key":
        L.append("  Note: no cache-TTL advice on a subscription: how plan quota counts 1-hour vs 5-minute cache writes "
                 "is not verified. Verify it yourself before changing promptCacheTtl.")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------ self-test
def self_test() -> None:
    import tempfile
    import socket
    os.environ["CTX_AUDIT_NO_TIKTOKEN"] = "1"           # deterministic chars/2.5 in the test
    global _ENC
    _ENC = None
    CANARY = "CANARY-7f3a-SECRET"
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        proj = home / ".claude/projects/-home-u-app"
        proj.mkdir(parents=True)
        (home / ".claude/skills/myskill").mkdir(parents=True)
        plug = home / "plug"
        (plug / "hooks").mkdir(parents=True)
        (plug / "hooks/hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "node inject.mjs"}]}]}}))
        (home / ".claude/plugins").mkdir(parents=True)
        (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({"plugins": {
            "bigplug@mkt": [{"installPath": str(plug)}]}}))
        (home / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": {
            "bigplug@mkt": True, "bigplug@other": True}}))
        cfg_before = {p: p.read_bytes() for p in home.rglob("*.json")}
        lines = []
        n = [0]

        def add(rec, parent=True):
            n[0] += 1
            rec.setdefault("uuid", f"u{n[0]}")
            rec.setdefault("parentUuid", f"u{n[0] - 1}" if n[0] > 1 and parent else None)
            lines.append(rec)

        sys_prompt = "You are a coding agent. " * 200 + CANARY                     # 5,000+ chars
        tools = [{"name": "Bash", "description": "run " * 500 + CANARY, "input_schema": {}}]
        lines.append({"type": "attachment", "attachment": {"type": "prompt_snapshot", "systemPrompt": [sys_prompt],
                                                           "tools": tools}})
        add({"type": "attachment", "attachment": {"type": "hook_success", "hookEvent": "SessionStart",
                                                  "hookName": "SessionStart:startup", "command": "node inject.mjs",
                                                  "content": "G" * 50000},
             "rendered": [{"content": "<system-reminder>SessionStart:startup hook success: " + "K" * 4000 + CANARY +
                           "</system-reminder>"}]}, parent=False)
        listing = ("- bigplug:a: " + "x" * 2000 + CANARY + "\n- bigplug:b: " + "y" * 2000 + "\n- myskill: " +
                   "z" * 1000)
        add({"type": "attachment", "attachment": {"type": "skill_listing", "content": listing},
             "rendered": [{"content": listing}]})
        dtd = "The following deferred tools are now available via ToolSearch:\n" + "\n".join(
            [f"mcp__used__t{i}" for i in range(20)] + [f"mcp__idle__tool_number_{i}" for i in range(200)])
        add({"type": "attachment", "attachment": {"type": "deferred_tools_delta", "addedNames": []},
             "rendered": [{"content": dtd}]})
        add({"type": "attachment", "attachment": {"type": "mcp_instructions_delta", "addedNames": ["idle"],
                                                  "addedBlocks": ["## idle\n" + "i" * 3000 + CANARY]},
             "rendered": [{"content": "## idle\n" + "i" * 3000 + CANARY}]})
        add({"type": "attachment", "attachment": {"type": "instructions", "files": [
            {"path": "/home/u/CLAUDE.md", "type": "User", "content": "c" * 6000 + CANARY}]},
            "rendered": [{"content": "c" * 6000 + CANARY}]})
        add({"type": "user", "message": {"role": "user", "content": "please fix the bug " + CANARY}})
        static_tok = (len(sys_prompt) + len(json.dumps(tools[0]))) / 2.5
        total = static_tok + sum(it[-1] for it in [])  # filled below
        # 12 requests, each: assistant tool_use mcp__used__t0 -> tool result 3000 chars
        hist = 0.0
        for k in range(12):
            mid = f"msg{k}"
            add({"type": "assistant", "message": {"id": mid, "model": "claude-opus-5-5", "role": "assistant",
                                                  "content": [{"type": "tool_use", "id": f"tu{k}",
                                                               "name": "mcp__used__t0", "input": {"q": CANARY}}],
                                                  "usage": {"input_tokens": 3, "cache_creation_input_tokens": 0,
                                                            "cache_read_input_tokens": 0, "output_tokens": 50}}})
            add({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"tu{k}", "content": "r" * 3000 + CANARY}]}})
        # fill usage so billed totals equal the estimate exactly (positions: all cache read except the newest 1.5k)
        parsed: list[dict] = []
        est = 0.0
        for rec in lines:
            if rec.get("type") == "assistant":
                T = int(round(static_tok + est))
                rec["message"]["usage"].update({"cache_read_input_tokens": T - 1300, "cache_creation_input_tokens": 1297,
                                                "input_tokens": 3})
            a = rec.get("attachment")
            if a and a.get("type") != "prompt_snapshot":
                est += len(rendered_text(rec)) / 2.5 if a.get("type") != "deferred_tools_delta" else len(dtd) / 2.5
            elif rec.get("type") == "user":
                cc = rec["message"]["content"]
                est += (len(cc) if isinstance(cc, str) else len(cc[0]["content"])) / 2.5
            elif rec.get("type") == "assistant":
                b = rec["message"]["content"][0]
                est += len(b["name"] + json.dumps(b["input"])) / 2.5
            parsed.append(rec)
        with open(proj / "s1.jsonl", "w") as fh:
            for rec in parsed:
                fh.write(json.dumps(rec) + "\n")
        # an excluded project must be skipped
        ex = home / ".claude/projects/-home-u-secretclient"
        ex.mkdir()
        (ex / "s2.jsonl").write_text((proj / "s1.jsonl").read_text())

        block_network()
        try:
            socket.create_connection(("example.com", 80))
            raise AssertionError("network not blocked")
        except OSError:
            pass
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            recs = run(home / ".claude/projects", 30, ["*secretclient*"], home)
            agg = aggregate(recs)
            print(report(agg, 30, home, tokenizer_name(), recs))
        text = buf.getvalue()
        assert CANARY not in text, "content leaked into the report"
        assert "G" * 20 not in text and "K" * 20 not in text
        assert len(recs) == 1, f"excluded project counted ({len(recs)} streams)"
        assert agg["requests"] == 12
        # positional pricing reconciles with the bill: residual ~0 because usage was built from the same estimate
        assert abs(agg["unattributed_in"]) < 0.02 * agg["in_bill"], (agg["unattributed_in"], agg["in_bill"])
        srcs = {(r["kind"], r["name"]): r for r in sources(agg, 30)}
        assert ("MCP server", "idle") in srcs and ("MCP server", "used") in srcs
        idle, used_ = srcs[("MCP server", "idle")], srcs[("MCP server", "used")]
        assert idle["tok_per_req"] > 5 * used_["tok_per_req"] > 0      # 200 names + instructions vs 20 names
        hook = [r for k, r in srcs.items() if k[0] == "hook injection"]
        assert hook and hook[0]["name"] == "SessionStart / bigplug", hook
        assert abs(hook[0]["tok_per_req"] - len(rendered_text(parsed[1])) / 2.5) < 1     # rendered, not stdout
        sug = suggestions(agg, 30, enabled_plugins(home))
        cats = {s["category"] for s in sug}
        assert "unused MCP server" in cats and "unused plugin" in cats and "duplicate plugin" in cats, cats
        assert not any("'used'" in s["what"] and s["category"] == "unused MCP server" for s in sug)
        plug_s = [s for s in sug if s["category"] == "unused plugin"][0]
        assert plug_s["usd"] > srcs[("plugin listing", "bigplug skills")]["usd"]        # includes its hook
        assert "unused user skills" in cats
        assert not any(s["category"] == "large hook injection" for s in sug)            # no double count
        bash_s = [s for s in sug if "'Bash'" in s["what"]]
        assert bash_s and not bash_s[0]["verified"], bash_s                            # deny of Bash not verified
        assert all(s["verified"] for s in sug if s["category"] in ("unused MCP server", "unused plugin"))
        assert "[unverified, not in total]" in text and "verified mechanisms only" in text
        assert "disableBundledSkills" not in text.replace("NOT disableBundledSkills", "")
        # every request reads its full static prefix from cache except the newest 1.3k tokens
        sp = srcs[("built-in", "system prompt")]
        assert abs(sp["tok_per_req"] - len(sys_prompt) / 2.5) < 1
        assert abs(sp["usd"] - 12 * len(sys_prompt) / 2.5 * P_R / 1e6) < 1e-9
        cfg_after = {p: p.read_bytes() for p in home.rglob("*.json")}
        assert cfg_before == cfg_after, "config changed"
        # tool results by tool + output split
        rows = dict((n, p) for n, _, p in breakdown_rows(agg))
        assert rows.get("tool results: mcp", 0) > 0 and rows.get("output: tool_call", 0) > 0
        # a forked sub-agent: its first request carries the parent's whole context, grafted from the parent file
        fork_dir = proj / "s1" / "subagents"
        fork_dir.mkdir(parents=True)
        T = int(round(static_tok + est)) + 40
        fork = [{"type": "fork-context-ref", "parentSessionId": "s1", "parentLastUuid": parsed[-1]["uuid"]},
                {"type": "assistant", "uuid": "f1", "parentUuid": None, "message": {
                    "id": "fmsg", "model": "claude-opus-5-5", "role": "assistant",
                    "content": [{"type": "text", "text": "done " + CANARY}],
                    "usage": {"input_tokens": 40, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": T - 40, "output_tokens": 5}}}]
        (fork_dir / "agent-f.jsonl").write_text("\n".join(json.dumps(x) for x in fork) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            recs2 = run(home / ".claude/projects", 30, ["*secretclient*"], home)
            print(report(aggregate(recs2), 30, home, tokenizer_name()))
        assert CANARY not in buf.getvalue()
        fr = [r for r in recs2 if r["subagent"]]
        assert len(fr) == 1 and fr[0]["requests"] == 1
        T1, e_static, e_msgs, snap = fr[0]["first_req"]
        assert snap and abs(T1 - (e_static + e_msgs)) < 0.01 * T1, fr[0]["first_req"]
        assert any(c == "claude_md" and v > 0 for c, s_, t, v in fr[0]["cost"]), "parent context not grafted"
        assert not any(c == "fork_inherited" for c, s_, t, v in fr[0]["cost"])
    # cache-TTL advisor: 5m user with a 10-min gap rewrite -> 1h cheaper; 1h user with 1-min gaps -> 5m cheaper
    rs5 = [(0, 3, 0, 100000, 0, 100), (600, 3, 0, 101000, 0, 100), (660, 3, 101000, 1000, 0, 100)]
    c5 = _policy_costs(rs5)
    assert c5["1h"] < c5["actual"] and abs(c5["5m"] - c5["actual"]) < 1e-12, c5
    adv = ttl_advice([{"subagent": True, "req_log": rs5}])["sub"]
    assert adv["best"] == "1h" and not adv["verified"] and adv["save_usd"] > 0   # API billing check pending
    rs1 = [(0, 3, 0, 100000, 100000, 100)] + [(60 * k, 3, 100000 + 1000 * (k - 1), 1000, 1000, 100)
                                              for k in range(1, 20)]
    adv1 = ttl_advice([{"subagent": False, "req_log": rs1}])["main"]
    assert adv1["now"] == "1h" and adv1["best"] == "5m" and not adv1["verified"] and adv1["save_usd"] > 0
    assert abs(adv1["save_usd"] - sum(x[3] for x in rs1) * (P_W1 - P_W) / 1e6) < 1e-9     # no gap >= 5 min
    # report: a subscription gets no TTL advice (footnote only); an API key gets it [unverified], out of the total
    agg0 = {"streams": 1, "requests": 20, "bill": 10.0, "in_bill": 9.0, "cold": 1}
    recs1 = [{"subagent": False, "req_log": rs1}]
    import unittest.mock as um
    with um.patch(__name__ + ".breakdown_rows", lambda a: []), um.patch(__name__ + ".sources", lambda a, d: []), \
            um.patch(__name__ + ".suggestions", lambda a, d, p: []), um.patch(__name__ + ".enabled_plugins", lambda h: []):
        sub = report(agg0, 30, Path("/nonexistent"), "x", recs1, mode="subscription")
        assert "cache TTL (" not in sub and "promptCacheTtl\"" not in sub and "Verify it yourself" in sub, sub
        api = report(agg0, 30, Path("/nonexistent"), "x", recs1, mode="api_key")
        assert "[unverified, not in total] cache TTL (main)" in api and "Total, verified mechanisms only: -$0/month" in api, api
    with tempfile.TemporaryDirectory() as td:
        h = Path(td)
        (h / ".claude").mkdir()
        with um.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}):
            assert auth_mode(h) == "unknown"
            (h / ".claude/.credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))
            assert auth_mode(h) == "subscription"
        with um.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            assert auth_mode(h) == "api_key"
    print("ctx_audit self-test OK")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--exclude", nargs="*", default=[], help="project-dir globs to skip (e.g. '*client*')")
    ap.add_argument("--json", help="write aggregates (numbers and names only) here")
    ap.add_argument("--records", help="write per-stream aggregates (JSONL) here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    block_network()
    home = Path.home()
    recs = run(Path(a.root), a.days, a.exclude, home, a.records, progress=True)
    agg = aggregate(recs)
    print(report(agg, a.days, home, tokenizer_name(), recs))
    if a.json:
        out = {"days": a.days, "requests": agg["requests"], "bill_usd": agg["bill"], "in_bill": agg["in_bill"],
               "breakdown": breakdown_rows(agg), "sources": sources(agg, a.days),
               "suggestions": suggestions(agg, a.days, enabled_plugins(home))}
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
