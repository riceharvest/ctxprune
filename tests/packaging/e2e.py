#!/usr/bin/env python3
"""End-to-end test of the installed `ctxprune` CLI in a throwaway HOME (never the real one).

    python tests/packaging/e2e.py --ctxprune /path/to/venv/bin/ctxprune [--keep]

Every harness: seed a config with the user's own content (and an old ctxprune hook where one existed), install,
install again (every file byte-identical), doctor (canary through the installed integration, CTXPRUNE_OFF included),
uninstall (the user's content is back, our entries are gone), uninstall again (no-op). Then install --all, doctor,
stats, upgrade (--spec a local wheel, if given), uninstall --all. All harness locations are pinned into the temp dir
by env (HOME, XDG_*, CLAUDE_CONFIG_DIR, HERMES_HOME, PI_CODING_AGENT_DIR, DSH_HOME, CODEX_HOME), and the real
configs' sha256 are compared before/after the run. Stdlib only; runs on Linux, macOS and Windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HARNESSES = ("claude-code", "hermes", "pi", "opencode", "dsh", "codex")
FAILS: list = []


def check(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        FAILS.append(what)


def has_marker(out: str) -> bool:
    """A ctxprune marker: the short `[cp: ...]` ones (lossless.GROUP_MARK etc.) or the long `[ctxprune ...` ones."""
    return "[cp:" in out or "[ctxprune" in out


def config_dir(home: str) -> str:
    """glue.config_dir() for the throwaway HOME (Env sets APPDATA / XDG_CONFIG_HOME inside it)."""
    if os.name == "nt":
        return os.path.join(home, "AppData", "Roaming", "ctxprune")
    return os.path.join(home, ".config", "ctxprune")


def stats_path(home: str) -> str:
    """glue's default stats log for the throwaway HOME (%LOCALAPPDATA% on Windows, XDG_STATE_HOME elsewhere)."""
    if os.name == "nt":
        return os.path.join(home, "AppData", "Local", "ctxprune", "stats.jsonl")
    return os.path.join(home, ".local", "state", "ctxprune", "stats.jsonl")


def real_home() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def real_snapshot() -> dict:
    h = real_home()
    paths = [".claude/settings.json", ".claude/settings.local.json", ".hermes/config.yaml", ".hermes/plugins",
             ".config/opencode/plugins", ".config/opencode/opencode.json", ".pi/agent/extensions",
             ".pi/agent/settings.json", ".codex/config.toml", ".dsh/cordis.patch.yml", ".config/ctxprune",
             ".config/systemd/user/ctxprune-proxy.service"]
    if os.name == "nt":   # glue.config_dir() on Windows
        paths.append("AppData/Roaming/ctxprune")
    out = {}
    for p in paths:
        full = os.path.join(h, p)
        if os.path.isdir(full):
            for root, _dirs, files in os.walk(full):
                if "__pycache__" in root:
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        out[fp] = hashlib.sha256(open(fp, "rb").read()).hexdigest()
                    except OSError:
                        pass
        elif os.path.isfile(full):
            out[full] = hashlib.sha256(open(full, "rb").read()).hexdigest()
        else:
            out[full] = None
    return out


def tree_hash(root: str) -> dict:
    out = {}
    for r, _d, files in os.walk(root):
        if "__pycache__" in r or os.sep + "state" in r or os.sep + ".cache" in r or os.sep + ".local" in r:
            continue
        for f in files:
            fp = os.path.join(r, f)
            out[os.path.relpath(fp, root)] = hashlib.sha256(open(fp, "rb").read()).hexdigest()
    return out


class Env:
    def __init__(self, exe: str, home: str):
        self.exe, self.home = exe, home
        keep = {k: v for k, v in os.environ.items()
                if k in ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "COMSPEC",
                         "PATHEXT", "WINDIR")}
        cfg = os.path.join(home, ".config")
        keep.update(HOME=home, USERPROFILE=home, XDG_CONFIG_HOME=cfg, XDG_CACHE_HOME=os.path.join(home, ".cache"),
                    XDG_STATE_HOME=os.path.join(home, ".local", "state"),
                    APPDATA=os.path.join(home, "AppData", "Roaming"),
                    LOCALAPPDATA=os.path.join(home, "AppData", "Local"),
                    CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"), HERMES_HOME=os.path.join(home, ".hermes"),
                    PI_CODING_AGENT_DIR=os.path.join(home, ".pi", "agent"), DSH_HOME=os.path.join(home, ".dsh"),
                    CODEX_HOME=os.path.join(home, ".codex"), PYTHONIOENCODING="utf-8")
        self.env = keep

    def run(self, *args, ok=True, input_text=None, extra=None):
        env = dict(self.env, **(extra or {}))
        p = subprocess.run([self.exe, *args], capture_output=True, text=True, env=env, input=input_text,
                           timeout=600, encoding="utf-8", errors="replace")
        if ok and p.returncode != 0:
            print(f"--- {' '.join(args)} exit {p.returncode}\n{p.stdout}\n{p.stderr}", flush=True)
        return p


def seed(home: str) -> dict:
    """User content that must survive install + uninstall. Returns {path: original text}."""
    files = {
        ".claude/settings.json": json.dumps({"model": "opus", "hooks": {"PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]},
            {"matcher": "Bash|mcp__.*", "hooks": [{"type": "command", "command": "\"/old/bin/ctxprune\" hook claude-code",
                                                   "timeout": 60}]},
            {"matcher": "Bash|Grep", "hooks": [{"type": "command",
                                                "command": "python3 /home/x/ctxprune-v2/scripts/lossless_hook.py"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}, indent=2) + "\n",
        ".hermes/config.yaml": "model:\n  default: agent\nplugins:\n  enabled:\n    - web/local-web\n    - other\n"
                               "memory:\n  memory_enabled: false\n",
        ".config/opencode/plugins/other.js": "export const Other = async () => ({});\n",
        ".dsh/cordis.patch.yml": "# my patch\n- id: foo\n  config:\n    bar: 1\n",
        ".codex/config.toml": 'model = "gpt-5"\n\n[model_providers.other]\nname = "x"\nbase_url = "http://x"\n',
    }
    out = {}
    for rel, text in files.items():
        p = os.path.join(home, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        out[p] = text
    os.makedirs(os.path.join(home, ".pi", "agent"), exist_ok=True)
    return out


def read(p: str) -> str | None:
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def after_uninstall_checks(home: str, harness: str, seeded: dict) -> None:
    j = lambda *a: os.path.join(home, *a)  # noqa: E731
    if harness == "claude-code":
        cfg = json.loads(read(j(".claude", "settings.json")))
        cmds = [h["command"] for g in cfg["hooks"]["PostToolUse"] for h in g["hooks"]]
        check(cmds == ["echo user-hook"] and cfg["hooks"]["Stop"] and cfg["model"] == "opus",
              "claude-code: uninstall keeps the user's hooks, removes every ctxprune hook (old ones too)")
    elif harness == "hermes":
        t = read(j(".hermes", "config.yaml"))
        check(t == seeded[j(".hermes", "config.yaml")], "hermes: config.yaml byte-identical to the user's original")
        check(not os.path.exists(j(".hermes", "plugins", "ctxprune")), "hermes: plugin dir removed")
    elif harness == "opencode":
        check(not os.path.exists(j(".config", "opencode", "plugins", "ctxprune.js"))
              and os.path.exists(j(".config", "opencode", "plugins", "other.js")), "opencode: only our plugin removed")
    elif harness == "pi":
        check(not os.path.exists(j(".pi", "agent", "extensions", "ctxprune.ts")), "pi: extension removed")
    elif harness == "dsh":
        p = j(".dsh", "cordis.patch.yml")
        check(read(p) == seeded[p], "dsh: cordis.patch.yml byte-identical to the user's original")
        check(not os.path.exists(j(".dsh", "ctxprune-plugin")), "dsh: plugin dir removed")
    elif harness == "codex":
        p = j(".codex", "config.toml")
        check(read(p) == seeded[p], "codex: config.toml byte-identical to the user's original")


def after_install_checks(home: str, harness: str, exe_dir: str) -> None:
    j = lambda *a: os.path.join(home, *a)  # noqa: E731
    if harness == "claude-code":
        cfg = json.loads(read(j(".claude", "settings.json")))
        hooks = [(g["matcher"], h["command"]) for g in cfg["hooks"]["PostToolUse"] for h in g["hooks"]]
        ours = [h for h in hooks if "ctxprune" in h[1]]
        check(len(ours) == 1 and ours[0][0] == "Bash|PowerShell|Grep|mcp__.*" and "plugin_cli.py\" hook claude-code" in ours[0][1]
              and ("echo user-hook" in [h[1] for h in hooks]),
              "claude-code: exactly one ctxprune hook (old v0.2.1 + research hooks replaced), user hook kept")
        bdir = os.path.join(config_dir(home), "backups", "claude-code", "settings.json")
        check(os.path.exists(bdir), "claude-code: original settings.json backed up")
    elif harness == "hermes":
        t = read(j(".hermes", "config.yaml"))
        check(t.count("- ctxprune") == 1 and "- web/local-web" in t, "hermes: ctxprune added to plugins.enabled")
        init = read(j(".hermes", "plugins", "ctxprune", "__init__.py"))
        check(init is not None and "ctxprune-managed" in init and "ctxprune-v2" not in init.split("_impl")[0],
              "hermes: shim points at the installed runtime, not ~/ctxprune-v2")
    elif harness in ("pi", "opencode", "dsh"):
        p = {"pi": j(".pi", "agent", "extensions", "ctxprune.ts"),
             "opencode": j(".config", "opencode", "plugins", "ctxprune.js"),
             "dsh": j(".dsh", "ctxprune-plugin", "ctxprune.mjs")}[harness]
        t = read(p) or ""
        head = "\n".join(t.split("\n")[:4])
        check("process.env.CTXPRUNE_PYTHON ??=" in head and "plugin_cli.py" in head and json.dumps(sys.executable)
              not in ("", None), f"{harness}: plugin header resolves the installed python + CLI")
        if harness == "dsh":
            pt = read(j(".dsh", "cordis.patch.yml"))
            check(pt.startswith("# my patch\n- id: foo") and "id: ctxprune" in pt, "dsh: patch item appended")
    elif harness == "codex":
        t = read(j(".codex", "config.toml"))
        check('[model_providers.ctxprune]' in t and t.startswith('model = "gpt-5"'), "codex: provider block appended")
        if sys.platform.startswith("linux"):
            svc = read(j(".config", "systemd", "user", "ctxprune-proxy.service"))
            check(svc is not None and "plugin_cli.py proxy" in svc, "codex: systemd user unit written (not started)")


def bundle_checks(e: "Env", home: str) -> None:
    """Self-contained plugin bundles: no pip needed at the target, so run them with THIS interpreter's stdlib."""
    out = os.path.join(home, "bundles")
    p = e.run("bundle", "claude-code", out)
    q = e.run("bundle", "hermes", os.path.join(out, "hermes", "ctxprune"))
    check(p.returncode == 0 and q.returncode == 0, "bundle claude-code + hermes")
    pyc = [os.path.join(r, f) for r, _d, fs in os.walk(out) for f in fs if f.endswith(".pyc")]
    check(not pyc, "bundles hold no .pyc")
    root = os.path.join(out, "claude-code-plugin-safe")
    hooks = json.loads(read(os.path.join(root, "hooks", "hooks.json")))
    cmd = hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    mp = json.loads(read(os.path.join(out, ".claude-plugin", "marketplace.json")))
    check(any(x["name"] == "ctxprune-safe" and x["source"] == "./claude-code-plugin-safe" for x in mp["plugins"]),
          "marketplace.json lists ctxprune-safe")
    sample = "\n".join(f"src/a/b.py:{i}:    x = User.get({i})" for i in range(1, 60))
    ev = {"session_id": "bundle", "transcript_path": "", "hook_event_name": "PostToolUse", "tool_name": "Bash",
          "tool_input": {"command": "grep -rn User src"},
          "tool_response": {"stdout": sample, "stderr": "", "interrupted": False, "isImage": False}}
    if os.name == "nt" and not shutil.which("sh"):
        check(True, "ctxprune-safe hook.sh: skipped (no sh on this Windows runner)")
        return
    env = dict(e.env, CLAUDE_PLUGIN_ROOT=root)
    # PATH: the bundle must work with a bare `python3` (no ctxprune installed); point python3 at this interpreter
    shim = os.path.join(home, "pybin")
    os.makedirs(shim, exist_ok=True)
    for name in ("python3", "python"):
        link = os.path.join(shim, name)
        if not os.path.exists(link) and os.name != "nt":
            os.symlink(sys.executable, link)
    env["PATH"] = shim + os.pathsep + env.get("PATH", "")
    # Claude Code runs hook commands through a POSIX shell (Git Bash on Windows), never cmd.exe
    sh = [shutil.which("sh"), "-c", cmd] if os.name == "nt" else cmd
    r = subprocess.run(sh, shell=os.name != "nt", input=json.dumps(ev), capture_output=True, text=True, env=env)
    try:
        got = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    except (ValueError, KeyError, TypeError):
        got = ""
    check(0 < len(got) < len(sample) and has_marker(got),
          f"ctxprune-safe hook via its hooks.json command: {len(sample)} -> {len(got)} ({r.stderr[-200:]})")
    r = subprocess.run(sh, shell=os.name != "nt", input=json.dumps(ev), capture_output=True, text=True,
                       env=dict(env, CTXPRUNE_OFF="1"))
    check(r.stdout.strip() == "", "ctxprune-safe hook: CTXPRUNE_OFF=1 -> no replacement")
    # the Hermes bundle, loaded the way Hermes does (package dir + register(ctx)), with a plain interpreter
    hp = os.path.join(out, "hermes", "ctxprune", "__init__.py")
    code = ("import importlib.util, json, os, sys\n"
            "spec = importlib.util.spec_from_file_location('hermes_plugins.ctxprune', sys.argv[1], "
            "submodule_search_locations=[os.path.dirname(sys.argv[1])])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "h = {}\nclass C:\n    def register_hook(self, n, f): h[n] = f\n"
            "    def register_middleware(self, n, f): h['mw:' + n] = f\nm.register(C())\n"
            "print(json.dumps([sorted(h), h['transform_terminal_output'](command='grep -rn User src', "
            "output=sys.stdin.read(), returncode=0)]))\n")
    r = subprocess.run([sys.executable, "-c", code, hp], input=sample, capture_output=True, text=True, env=e.env)
    try:
        hooks_got, out_text = json.loads(r.stdout)
    except ValueError:
        hooks_got, out_text = [], None
    check(hooks_got == ["mw:llm_request", "transform_terminal_output", "transform_tool_result"] and isinstance(out_text, str)
          and len(out_text) < len(sample), f"hermes bundle: registers both hooks + llm_request middleware, terminal {len(sample)} -> "
          f"{len(out_text or '')} ({r.stderr[-200:]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctxprune", required=True, help="the installed ctxprune executable")
    ap.add_argument("--wheel", default="", help="a local wheel for the upgrade test")
    ap.add_argument("--keep", action="store_true", help="keep the temp HOME")
    ap.add_argument("--only", default="", help="comma list of harnesses")
    a = ap.parse_args()
    before = real_snapshot()
    home = tempfile.mkdtemp(prefix="ctxprune-e2e-home-")
    e = Env(os.path.abspath(a.ctxprune), home)
    exe_dir = os.path.dirname(os.path.abspath(a.ctxprune))
    try:
        seeded = seed(home)
        p = e.run("--version")
        check(p.returncode == 0 and p.stdout.startswith("ctxprune "), f"version: {p.stdout.strip()}")
        p = e.run("doctor", ok=False)
        check("runtime" in p.stdout and "FAIL  runtime" not in p.stdout and "pipeline canary" in p.stdout
              and "FAIL  pipeline canary" not in p.stdout, "doctor (nothing installed): runtime + pipeline canary")
        only = [h for h in (a.only.split(",") if a.only else HARNESSES) if h]
        for h in only:
            p = e.run("install", h)
            check(p.returncode == 0, f"{h}: install ({p.stdout.strip().splitlines()[0] if p.stdout.strip() else p.stderr[-200:]})")
            after_install_checks(home, h, exe_dir)
            t1 = tree_hash(home)
            p = e.run("install", h)
            t2 = tree_hash(home)
            check(p.returncode == 0 and t1 == t2, f"{h}: second install leaves every file byte-identical"
                  + ("" if t1 == t2 else f" (changed: {sorted(k for k in set(t1) | set(t2) if t1.get(k) != t2.get(k))})"))
            p = e.run("doctor", h, ok=False)
            fails = [ln for ln in p.stdout.splitlines() if ln.startswith("FAIL")]
            check(p.returncode == 0 and not fails, f"{h}: doctor passes ({'; '.join(fails)[:400]})")
            print("\n".join("      " + ln for ln in p.stdout.splitlines() if h.split("-")[0] in ln), flush=True)
            p = e.run("uninstall", h)
            check(p.returncode == 0, f"{h}: uninstall")
            after_uninstall_checks(home, h, seeded)
            t3 = tree_hash(home)
            p = e.run("uninstall", h)
            check(p.returncode == 0 and tree_hash(home) == t3, f"{h}: second uninstall is a no-op")
        bundle_checks(e, home)
        # everything at once
        p = e.run("install", "--all")
        got = [h for h in HARNESSES if p.stdout.find(h + ":") >= 0]
        check(p.returncode == 0 and set(got) >= {"claude-code", "hermes", "pi", "opencode", "dsh", "codex"} & set(only),
              f"install --all installed {got}")
        p = e.run("doctor", ok=False)
        fails = [ln for ln in p.stdout.splitlines() if ln.startswith("FAIL")]
        check(p.returncode == 0 and not fails, f"doctor (all): {'; '.join(fails)[:600] or 'no failures'}")
        # a real hook call through the installed Claude Code command, then stats
        if "claude-code" in only:
            cfg = json.loads(read(os.path.join(home, ".claude", "settings.json")))
            cmd = [h["command"] for g in cfg["hooks"]["PostToolUse"] for h in g["hooks"] if "ctxprune" in h["command"]][0]
            sample = "\n".join(f"src/a/b.py:{i}:    x = User.get({i})" for i in range(1, 60))
            ev = {"session_id": "e2e", "transcript_path": "", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "grep -rn User src"},
                  "tool_response": {"stdout": sample, "stderr": "", "interrupted": False, "isImage": False}}
            r = subprocess.run(cmd, shell=True, input=json.dumps(ev), capture_output=True, text=True, env=e.env)
            out = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"] if r.stdout else ""
            check(0 < len(out) < len(sample), f"claude-code hook via its settings.json command: {len(sample)} -> {len(out)}")
            decoy = os.path.join(home, "decoy-project")   # Claude Code runs hooks in the user's project dir
            os.makedirs(os.path.join(decoy, "ctxprune"), exist_ok=True)
            for rel in ("ctxprune/__init__.py", "ctxprune/__main__.py", "dedup.py", "lossless.py", "lossless_hook.py",
                        "glue.py", "ctxprune_core.py"):
                with open(os.path.join(decoy, *rel.split("/")), "w") as f:
                    f.write("print('DECOY'); raise SystemExit(3)\n")
            r3 = subprocess.run(cmd, shell=True, input=json.dumps(ev), capture_output=True, text=True, env=e.env,
                                cwd=decoy)
            out3 = json.loads(r3.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"] if r3.stdout.startswith("{") else ""
            check(out3 == out and "DECOY" not in r3.stdout + r3.stderr,
                  "claude-code hook run from a project with decoy ctxprune/, dedup.py, lossless.py ...: ours runs")
            r2 = subprocess.run(cmd, shell=True, input=json.dumps(dict(ev, tool_input={"command": "CTXPRUNE_OFF=1 grep -rn User src"})),
                                capture_output=True, text=True, env=e.env)
            check(r2.stdout.strip() == "", "claude-code hook: CTXPRUNE_OFF=1 inside the command -> no replacement")
            p = e.run("stats", "--json")
            st = json.loads(p.stdout)
            check(st["calls"] >= 1 and st["saved"] > 0 and st["tokens_saved_est"] > 0, f"stats: {st['calls']} calls, "
                  f"{st['saved']} chars, ~{st['tokens_saved_est']} tokens, ${st['usd_est']}")
            log = read(stats_path(home))
            check(bool(log), f"stats log written ({stats_path(home)})")
            log = log or ""
            check("User.get" not in log and "grep" not in log, "stats log holds no tool output and no commands")
            if os.name != "nt":
                mode = os.stat(stats_path(home)).st_mode & 0o777
                check(mode == 0o600, f"stats log mode {oct(mode)}")
            p = e.run("stats")
            check(p.returncode == 0 and "tokens saved" in p.stdout, "stats (text)")
        # per-request dedup (Pi / OpenCode `--context`): through the installed plugin CLI, as the JS plugins call it
        py = os.path.join(os.path.dirname(e.exe), "python" + (".exe" if os.name == "nt" else ""))
        cli = subprocess.run([py, "-c", "from ctxprune.installers import plugin_cli; print(plugin_cli())"],
                             capture_output=True, text=True, env=e.env, cwd=home).stdout.strip()
        big = "\n".join(f"tests/test_mod_{i}.py::test_case_{i} PASSED   [{i}%]" for i in range(1, 90))
        evs = []
        for i in range(2):
            evs += [{"t": "call", "id": f"c{i}", "name": "bash", "args": {"command": "pytest -q"}},
                    {"t": "result", "id": f"c{i}", "name": "bash", "text": big}]
        r = subprocess.run([py, cli, "--context=pi"], input=json.dumps({"events": evs}), capture_output=True,
                           text=True, env=dict(e.env, CTXPRUNE_DEDUP="1"))   # dedup is opt-in in every preset
        try:
            outs = json.loads(r.stdout)["out"]
        except (ValueError, KeyError):
            outs = {}
        new = next(iter(outs.values()), "")
        check(outs and len(new) < len(big) and has_marker(new), f"plugin --context dedup: the repeated "
              f"{len(big)}-char result -> {len(new)} chars ({r.stderr[-200:]})")
        st = json.loads(e.run("stats", "--json").stdout)
        check(st.get("request_dedup", 0) > 0, f"stats count per-request dedup ({st.get('request_dedup')} chars)")
        r = subprocess.run([py, cli, "--context=pi"], input=json.dumps({"events": evs}), capture_output=True,
                           text=True, env=dict(e.env, CTXPRUNE_OFF="1"))
        check(json.loads(r.stdout or "{}").get("out") == outs,
              "plugin --context: CTXPRUNE_OFF=1 keeps the first-render pins (same bytes, prompt cache intact)")
        evs2 = [dict(x, text=x["text"].replace("PASSED", "passed")) if x["t"] == "result" else x for x in evs]   # never sent: no pins
        r = subprocess.run([py, cli, "--context=pi"], input=json.dumps({"events": evs2}), capture_output=True,
                           text=True, env=dict(e.env, CTXPRUNE_OFF="1"))
        check(json.loads(r.stdout or "{}").get("out") == {}, "plugin --context: CTXPRUNE_OFF=1 -> no new rewrites")
        r = subprocess.run([py, cli, "--context=pi"], input=json.dumps({"events": evs}), capture_output=True,
                           text=True, env=dict(e.env, CTXPRUNE_OFF="1", CTXPRUNE_RERENDER="1"))
        check(json.loads(r.stdout or "{}").get("out") == {}, "plugin --context: CTXPRUNE_OFF=1 + CTXPRUNE_RERENDER=1 "
              "-> everything back to the original")
        if a.wheel:
            p = e.run("upgrade", "--spec", os.path.abspath(a.wheel))
            check(p.returncode == 0 and "claude-code:" in p.stdout, f"upgrade --spec wheel + refresh "
                  f"({p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-300:]})")
        p = e.run("uninstall", "--all")
        check(p.returncode == 0, "uninstall --all")
        for h in only:
            after_uninstall_checks(home, h, seeded)
    finally:
        after = real_snapshot()
        check(before == after, "real configs untouched (sha256 of ~/.claude, ~/.hermes, ~/.config/opencode, ~/.pi, "
                               "~/.codex, ~/.dsh, ~/.config/ctxprune before == after)")
        if a.keep:
            print("kept", home)
        else:
            shutil.rmtree(home, ignore_errors=True)
    print(f"\n{len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
