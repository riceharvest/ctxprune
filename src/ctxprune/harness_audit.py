"""`ctxprune audit` for the harnesses other than Claude Code (Codex, Pi, OpenCode, Hermes, dsh).

Claude Code gets the full bill map (the runtime's ctx_audit.py). For the others this prints the static-overhead
checks that apply to each, from the harness's own local records and config files, read-only:

  * the prefix each session starts with (system prompt + tool schemas + first message), measured from the
    harness's own usage records (Hermes: from the session files' system prompt and tool schemas);
  * instruction files it may load (AGENTS.md / CLAUDE.md / HERMES.md / SOUL.md ...) and their size;
  * configured MCP servers, and how often their tools were called in the window;
  * skills and plugins / extensions installed;
  * the prompt cache: the measured cache-read share, and what the provider documents about caching.

Every finding says whether it is VERIFIED (measured from local records, or checked by ctxprune) or UNVERIFIED (from
the harness's or provider's documentation, not checked against a real request). Numbers and names only, never text
from a prompt, an output or a file; no network (sockets are disabled for the run); nothing is changed.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time

from . import usage

CHARS_PER_TOKEN = usage.CHARS_PER_TOKEN
BIG_INSTRUCTIONS = 1500   # tokens per request worth trimming (the same bar as the Claude Code audit's CLAUDE.md check)


def block_network() -> None:
    import socket

    class _NoSocket(socket.socket):
        def __init__(self, *a, **k):
            raise OSError("ctxprune audit: network disabled")

    socket.socket = _NoSocket   # type: ignore[misc]

    def _no(*a, **k):
        raise OSError("ctxprune audit: network disabled")
    socket.create_connection = _no   # type: ignore[assignment]


def _home() -> str:
    return os.path.expanduser("~")


def _dirs() -> dict:
    e = os.environ.get
    cfg = e("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")
    return {"codex": e("CODEX_HOME") or os.path.join(_home(), ".codex"),
            "pi": e("PI_CODING_AGENT_DIR") or os.path.join(_home(), ".pi", "agent"),
            "opencode": os.path.join(cfg, "opencode"),
            "hermes": e("HERMES_HOME") or os.path.join(_home(), ".hermes"),
            "dsh": e("DSH_HOME") or os.path.join(_home(), ".dsh")}


def _read(p: str) -> str | None:
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _size_tokens(p: str) -> float | None:
    try:
        return os.path.getsize(p) / CHARS_PER_TOKEN if os.path.isfile(p) else None
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------- config parsers
def codex_mcp(text: str) -> list:
    """[mcp_servers.NAME] tables of a Codex config.toml (sub-tables like .env are not servers)."""
    names = []
    for m in re.finditer(r'^\s*\[mcp_servers\.("([^"]+)"|[A-Za-z0-9_-]+)\]\s*$', text or "", re.M):
        n = m.group(2) or m.group(1)
        if n not in names:
            names.append(n)
    return names


def _strip_jsonc(text: str) -> str:
    out, i, n, s = [], 0, len(text), None
    while i < n:
        c = text[i]
        if s:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == s:
                s = None
        elif c in "\"'":
            s = c
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def opencode_config(d: str) -> dict:
    for fn in ("opencode.json", "opencode.jsonc", "config.json"):
        t = _read(os.path.join(d, fn))
        if t:
            try:
                v = json.loads(_strip_jsonc(t))
                return v if isinstance(v, dict) else {}
            except ValueError:
                continue
    return {}


def yaml_top_keys(text: str, key: str) -> list:
    """Keys one level under a top-level `key:` block of a YAML file (block style; flow style `{}` -> none)."""
    lines = (text or "").splitlines()
    out, inside, ind = [], False, None
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not inside:
            if re.match(rf"^{re.escape(key)}:\s*(#.*)?$", ln):
                inside = True
            continue
        if not ln.startswith((" ", "\t")):
            break
        cur = len(ln) - len(ln.lstrip())
        if ind is None:
            ind = cur
        if cur == ind:
            m = re.match(r"^\s*(?:-\s+)?[\"']?([^\"':#]+?)[\"']?\s*:(\s|$)", ln) or re.match(r"^\s*-\s+(\S+)", ln)
            if m:
                out.append(m.group(1).strip())
    return out


def hermes_enabled_plugins(text: str) -> list:
    lines = (text or "").splitlines()
    out, inp, ine = [], False, False
    for ln in lines:
        if re.match(r"^plugins:\s*$", ln):
            inp = True
            continue
        if inp and ln and not ln.startswith((" ", "\t")):
            break
        if inp and re.match(r"^\s+enabled:\s*\[(.*)\]\s*$", ln):
            return [x.strip().strip("'\"") for x in re.match(r"^\s+enabled:\s*\[(.*)\]", ln).group(1).split(",")
                    if x.strip()]
        if inp and re.match(r"^\s+enabled:\s*$", ln):
            ine = True
            continue
        if ine:
            m = re.match(r"^\s+-\s+['\"]?([^'\"#\s]+)", ln)
            if m:
                out.append(m.group(1))
            elif ln.strip() and not ln.lstrip().startswith("-"):
                ine = False
    return out


def _count_files(d: str, name: str) -> list:
    out = []
    if os.path.isdir(d):
        for r, _, fs in os.walk(d):
            if name in fs:
                out.append(os.path.basename(r))
    return out


# ---------------------------------------------------------------------------------------------- provider caching
PROVIDER_CACHE = (   # (match, documented behaviour) - provider documentation, not measured by ctxprune
    (("openai", "azure", "chatgpt"), "OpenAI caches prompt prefixes automatically from 1,024 tokens, with no setting, and keeps "
                          "them only for a short idle window (minutes, per its docs): keep the system prompt, the tool "
                          "list and early history byte-stable, since a change near the top turns every later token "
                          "into a miss, and long pauses start cold"),
    (("anthropic", "claude"), "Anthropic caches only up to explicit cache_control breakpoints (5-minute default "
                              "lifetime, a 1-hour option at a higher write price); the harness places them"),
    (("deepseek",), "DeepSeek caches repeated prompt prefixes automatically (best effort) and bills hits lower: "
                    "stable prefixes matter the same way"),
    (("google", "gemini", "vertex"), "recent Gemini models cache implicitly above a minimum prompt size; explicit "
                                     "context caching is a separate API: stable prefixes matter"),
    (("openrouter",), "OpenRouter passes caching through to the model's provider, so that provider's rules apply "
                      "(Anthropic models need cache_control breakpoints)"),
    (("127.0.0.1", "localhost", "vllm", "llama", "lmstudio", "ollama", "local"),
     "a local server reuses its prefix (KV) cache only for an identical prefix: nothing is billed, but a changed "
     "prefix costs prefill time"),
)


def provider_advice(name: str) -> str | None:
    n = (name or "").lower()
    for keys, text in PROVIDER_CACHE:
        if any(k in n for k in keys):
            return text
    return None


# ---------------------------------------------------------------------------------------------- the audit
def _f(what: str, verified: bool, how: str, advice: str = "", tok: float | None = None) -> dict:
    return {"what": what, "verified": verified, "how": how, "advice": advice,
            "tok_per_req": round(tok) if tok is not None else None}


def _mcp_calls(tools: dict, server: str, style: str) -> int:
    s = server.lower()
    n = 0
    for name, c in tools.items():
        t = name.lower()
        if style == "codex" and t.startswith(f"mcp__{s}__"):
            n += c
        elif style == "prefix" and (t.startswith(s + "_") or t.startswith(s.replace("-", "_") + "_")):
            n += c
        elif style == "contains" and (s in t or s.replace("-", "_") in t):
            n += c
    return n


def audit_harness(h: str, days: float, cwd: str | None = None, data: dict | None = None) -> dict:
    """Findings for one harness. `data` (tests): a usage record instead of reading the harness's own files."""
    since = time.time() - days * 86400
    d = _dirs().get(h, "")
    cwd = cwd or os.getcwd()
    u = data if data is not None else (usage.READERS[h](since) if h in usage.READERS else None)
    found = os.path.isdir(d) or u is not None
    out = {"harness": h, "found": found, "days": days, "findings": []}
    if not found:
        return out
    F = out["findings"]
    if u:
        out["requests"] = u["requests"]
        firsts = [x for x in u.get("first_input") or [] if x]
        if firsts:
            med = statistics.median(firsts) / (CHARS_PER_TOKEN if u.get("unit") == "chars" else 1)
            what = ("system prompt + tool schemas at the start of each session" if h == "hermes" else
                    "first request of each session (system prompt + tool schemas + first message)")
            F.append(_f(f"{what}: median {med:,.0f} tokens over {len(firsts):,} sessions", True,
                        "measured from the harness's own records" + (" (characters / 4)" if u.get("unit") == "chars"
                                                                     else ""),
                        "every request carries this prefix; the items below are the parts you control", med))
        if u.get("unit") != "chars" and u["cache_read"] + u["new_input"]:
            hit = 100.0 * u["cache_read"] / (u["cache_read"] + u["new_input"])
            F.append(_f(f"prompt cache: {hit:.0f}% of input tokens were cache reads ({u['requests']:,} requests)", True,
                        "measured from the harness's own usage records",
                        "a low share on long sessions means the prefix changes between requests" if hit < 50 else ""))
        unit = "sessions" if h in ("codex", "hermes") else "requests"
        for p, n in sorted(u.get("providers", {}).items(), key=lambda kv: -kv[1]):
            adv = provider_advice(p)
            F.append(_f(f"provider '{p}' ({n:,} {unit}): " + (adv or "no caching rule on file for this provider"),
                        False, "provider documentation, not measured by ctxprune"))
    # instruction files (names + size only)
    cands = {"codex": [os.path.join(d, "AGENTS.md"), os.path.join(d, "AGENTS.override.md"),
                       os.path.join(cwd, "AGENTS.md")],
             "pi": [os.path.join(d, "AGENTS.md"), os.path.join(cwd, "AGENTS.md"), os.path.join(cwd, "CLAUDE.md")],
             "opencode": [os.path.join(d, "AGENTS.md"), os.path.join(cwd, "AGENTS.md"), os.path.join(cwd, "CLAUDE.md")],
             "hermes": [os.path.join(d, "SOUL.md"), os.path.join(d, "memories", "MEMORY.md"),
                        os.path.join(d, "memories", "USER.md"), os.path.join(cwd, "AGENTS.md"),
                        os.path.join(cwd, ".hermes.md"), os.path.join(cwd, "HERMES.md")],
             "dsh": [os.path.join(cwd, "AGENTS.md")]}.get(h, [])
    for p in cands:
        t = _size_tokens(p)
        if t is None:
            continue
        shown = p
        try:
            rel = os.path.relpath(p, _home())
            if not rel.startswith(".."):
                shown = "~/" + rel.replace(os.sep, "/")
        except ValueError:   # another drive (Windows)
            pass
        F.append(_f(f"instruction file {shown}: ~{t:,.0f} tokens", False,
                    "file size / 4; whether this harness loads it comes from its documentation",
                    "loaded on every request: trim it" if t >= BIG_INSTRUCTIONS else "", t))
    # MCP servers
    tools = (u or {}).get("tools") or {}
    servers, style = [], "contains"
    if h == "codex":
        servers, style = codex_mcp(_read(os.path.join(d, "config.toml")) or ""), "codex"
    elif h == "opencode":
        mcp = opencode_config(d).get("mcp") or {}
        servers = [k for k, v in mcp.items() if not (isinstance(v, dict) and v.get("enabled") is False)] \
            if isinstance(mcp, dict) else []
        style = "prefix"
    elif h == "hermes":
        servers = yaml_top_keys(_read(os.path.join(d, "config.yaml")) or "", "mcp_servers")
    for s in servers:
        calls = _mcp_calls(tools, s, style)
        F.append(_f(f"MCP server '{s}': {calls:,} tool calls in {days:g} days", False,
                    "call count from tool names in the harness's records" + (" (mcp__<server>__<tool>)" if style ==
                                                                             "codex" else " (name match)") +
                    "; that disabling it removes its schemas from the request is not checked for this harness",
                    "unused: its tool schemas ride on every request; disable it, or enable it only where you use it"
                    if calls == 0 else ""))
    # skills and plugins
    skill_dirs = {"hermes": os.path.join(d, "skills"), "pi": os.path.join(d, "skills"),
                  "codex": os.path.join(d, "skills"), "opencode": os.path.join(d, "skill")}
    if h in skill_dirs:
        sk = _count_files(skill_dirs[h], "SKILL.md") + (_count_files(os.path.join(d, "skills"), "SKILL.md")
                                                        if h == "opencode" else [])
        if sk:
            F.append(_f(f"{len(sk)} skills installed", False, "SKILL.md files; listing cost per request not measured "
                        "for this harness", "each listed skill adds its name and description to the prompt: remove "
                        "the ones you never use" if len(sk) >= 20 else ""))
    if h == "hermes":
        pl = hermes_enabled_plugins(_read(os.path.join(d, "config.yaml")) or "")
        if pl:
            F.append(_f(f"{len(pl)} plugins enabled: " + ", ".join(pl[:12]), False, "config.yaml plugins.enabled",
                        "a plugin can inject context or tools; disable the ones you don't use"))
    elif h == "pi":
        ext = sorted(f for f in os.listdir(os.path.join(d, "extensions")) if not f.startswith(".")) \
            if os.path.isdir(os.path.join(d, "extensions")) else []
        if ext:
            F.append(_f(f"{len(ext)} extensions: " + ", ".join(ext[:12]), False, "~/.pi/agent/extensions",
                        "an extension can add tools or context; remove the ones you don't use"))
    elif h == "opencode":
        pd = os.path.join(d, "plugins")
        pls = sorted(f for f in os.listdir(pd) if not f.startswith(".")) if os.path.isdir(pd) else []
        cfgp = opencode_config(d).get("plugin") or []
        names = pls + [str(x) for x in cfgp if isinstance(x, str)]
        if names:
            F.append(_f(f"{len(names)} plugins: " + ", ".join(names[:12]), False, "plugins dir + opencode.json plugin",
                        "a plugin can add tools or context; remove the ones you don't use"))
    elif h == "dsh":
        t = _read(os.path.join(d, "cordis.patch.yml")) or ""
        n = len(re.findall(r"^\s*-\s", t, re.M))
        if n:
            F.append(_f(f"{n} cordis.patch.yml items", False, "~/.dsh/cordis.patch.yml"))
        F.append(_f("no local usage records read for dsh (no verified format)", False, "", ""))
    return out


def report(results: list) -> str:
    L = []
    for r in results:
        if not r["found"]:
            continue
        L.append(f"\n{r['harness']}: last {r['days']:g} days" + (f", {r['requests']:,} requests" if r.get("requests")
                                                                 else ", no local usage records found"))
        if not r["findings"]:
            L.append("  nothing to report")
        for f in r["findings"]:
            tag = "[verified]  " if f["verified"] else "[unverified]"
            L.append(f"  {tag} {f['what']}" + (f"\n               -> {f['advice']}" if f["advice"] else ""))
    return "\n".join(L).lstrip("\n")


HARNESSES = ("codex", "pi", "opencode", "hermes", "dsh")
