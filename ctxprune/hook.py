"""Claude Code PostToolUse hook: compress large Bash and MCP tool outputs before Claude reads them.

Install with `ctxprune install claude-code` (or the plugin in this repo). Claude Code runs
`ctxprune hook claude-code` after every matching tool call and passes the call as JSON on stdin.

What it does, and what it deliberately leaves alone:
- Only outputs above CTXPRUNE_MIN_CHARS (default 6000) are touched; small outputs exit in ~50 ms.
- Read/Grep/Glob/Edit are never matched, and Bash commands that print file contents
  (cat, head, sed -n, nl, git diff/show/log -p) are skipped, because Claude edits files by
  exact string match and needs them verbatim.
- Identifiers, numbers and paths are always kept (force_protected), so IDs stay exact.
- The full output is saved under ~/.cache/ctxprune/outputs/ and Claude is told where, so it can
  read the original if something it needs was cut. A command containing CTXPRUNE_OFF=1 is
  never compressed.
- Any error means no output: Claude then gets the original text (fail open).

Settings (environment of the Claude Code process): CTXPRUNE_RATE (default 0.5),
CTXPRUNE_MIN_CHARS (6000), CTXPRUNE_SERVER (default http://127.0.0.1:8787; used when
`ctxprune serve` is running, which avoids the ~2 s model load per compressed call).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MATCHER = "Bash|mcp__.*"
VERBATIM_CMD = re.compile(
    r"(^|[;&|(]\s*|\bxargs\s+)(cat|head|nl|bat|less|more|sed\s+-n|git\s+(diff|show|blame|log\s+.*-p))\b")
VERBATIM_MCP = re.compile(r"read_(text_)?file|read_multiple|get_file|file_contents", re.I)


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "ctxprune" / "outputs"


def _save_original(text: str) -> str:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / (hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16] + ".txt")
    if not p.exists():
        p.write_text(text, encoding="utf-8", errors="surrogatepass")
    return str(p)


def _compress(texts: list[str], rate: float) -> list[str]:
    """Compress via a running `ctxprune serve` if there is one, else in process."""
    server = os.environ.get("CTXPRUNE_SERVER", "http://127.0.0.1:8787")
    if server:
        try:
            import urllib.request

            req = urllib.request.Request(server.rstrip("/") + "/compress", method="POST",
                                         headers={"Content-Type": "application/json"},
                                         data=json.dumps({"texts": texts, "rate": rate,
                                                          "force_protected": True}).encode())
            with urllib.request.urlopen(req, timeout=60) as r:
                return [o["text"] for o in json.load(r)["results"]]
        except OSError:
            pass  # not running: load the model here
    from .compress import Compressor

    c = Compressor()
    return [c.compress(t, rate=rate, force_protected=True)["text"] for t in texts]


def _text_slots(tool: str, resp) -> list[tuple[list, object]]:
    """(container, key) pairs that hold compressible text in a tool_response."""
    slots = []
    if tool == "Bash":
        if isinstance(resp, dict):
            for k in ("stdout", "stderr"):
                if isinstance(resp.get(k), str):
                    slots.append((resp, k))
        return slots
    # MCP: a string, a list of content blocks, or {"content": [...]}
    if isinstance(resp, dict) and isinstance(resp.get("content"), list):
        resp = resp["content"]
    if isinstance(resp, list):
        for b in resp:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                slots.append((b, "text"))
    return slots


def run(event: dict, rate: float, min_chars: int) -> dict | None:
    tool = event.get("tool_name", "")
    if tool != "Bash" and not tool.startswith("mcp__"):
        return None
    if tool.startswith("mcp__") and VERBATIM_MCP.search(tool):
        return None
    if tool == "Bash":
        cmd = str((event.get("tool_input") or {}).get("command", ""))
        if "CTXPRUNE_OFF=1" in cmd or VERBATIM_CMD.search(cmd):
            return None
    resp = event.get("tool_response")
    if isinstance(resp, str):  # MCP tools may return a bare string
        if len(resp) < min_chars:
            return None
        out = _compress([resp], rate)[0]
        new, before, after, original = out, len(resp), len(out), resp
    else:
        new = copy.deepcopy(resp)
        slots = [(c, k) for c, k in _text_slots(tool, new) if len(c[k]) >= min_chars]
        if not slots:
            return None
        originals = [c[k] for c, k in slots]
        outs = _compress(originals, rate)
        for (c, k), o in zip(slots, outs):
            c[k] = o
        before, after = sum(map(len, originals)), sum(map(len, outs))
        original = "\n\n".join(originals)
    if after >= before * 0.95:
        return None
    path = _save_original(original)
    note = (f"ctxprune compressed this {tool} output from {before:,} to {after:,} characters by deleting "
            f"low-value words; IDs, numbers and paths are kept exactly. The full output is at {path} . "
            f"Only if a detail you expected is absent, grep that file (cheaper than rerunning the command).")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new,
                                   "additionalContext": note}}


def main_claude_code() -> None:
    if os.environ.get("CTXPRUNE_OFF") == "1":
        return
    try:
        event = json.load(sys.stdin)
        rate = float(os.environ.get("CTXPRUNE_RATE", "0.5"))
        min_chars = int(os.environ.get("CTXPRUNE_MIN_CHARS", "6000"))
        out = run(event, rate, min_chars)
        if out is not None:
            sys.stdout.write(json.dumps(out))
    except Exception as e:  # fail open: Claude gets the original output
        if os.environ.get("CTXPRUNE_DEBUG"):
            print(f"ctxprune hook error: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------- install / uninstall

def _settings_path(scope: str) -> Path:
    if scope == "user":
        return Path(os.path.expanduser("~")) / ".claude" / "settings.json"
    name = "settings.json" if scope == "project" else "settings.local.json"
    return Path.cwd() / ".claude" / name


def _hook_command() -> str:
    import shutil

    exe = shutil.which("ctxprune")
    return f'"{exe}" hook claude-code' if exe else f'"{sys.executable}" -m ctxprune hook claude-code'


def _is_ours(h: dict) -> bool:
    return "ctxprune" in str(h.get("command", "")) and "hook claude-code" in str(h.get("command", ""))


def install(scope: str = "user") -> Path:
    p = _settings_path(scope)
    cfg = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
    post = cfg.setdefault("hooks", {}).setdefault("PostToolUse", [])
    for group in post:
        group["hooks"] = [h for h in group.get("hooks", []) if not _is_ours(h)]
    post[:] = [g for g in post if g.get("hooks")]
    post.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": _hook_command(), "timeout": 60}]})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    return p


def uninstall(scope: str = "user") -> Path:
    p = _settings_path(scope)
    if not p.exists():
        return p
    cfg = json.loads(p.read_text())
    post = cfg.get("hooks", {}).get("PostToolUse", [])
    for group in post:
        group["hooks"] = [h for h in group.get("hooks", []) if not _is_ours(h)]
    post[:] = [g for g in post if g.get("hooks")]
    if not post:
        cfg.get("hooks", {}).pop("PostToolUse", None)
    if cfg.get("hooks") == {}:
        cfg.pop("hooks")
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    return p
