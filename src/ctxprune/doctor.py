"""`ctxprune doctor`: versions, the installed files, and a canary call through every installed integration.

Each canary runs the integration exactly as the harness would (the hook command string read back from Claude Code's
settings.json; the installed JS plugin file loaded in node with a fake harness object; the installed Hermes plugin
loaded by path; the proxy started against a local echo server) on a sample grep output, checks that a smaller
replacement comes back, then repeats with CTXPRUNE_OFF=1 and checks that nothing is replaced. Canaries use a
temporary state dir and stats file, so they leave no trace in your real ones.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from . import __version__, glue, installers as ins

SAMPLE_CMD = "grep -rn User src"
SAMPLE = "\n".join(f"src/app/views/user_views.py:{i}:    user = User.get({i})  # lookup {i}" for i in range(1, 80)) + "\n"

NODE_RUNNER = r"""
import { pathToFileURL } from 'node:url';
const [kind, file, cmd] = process.argv.slice(2);
const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const text = Buffer.concat(chunks).toString('utf8');
const mod = await import(pathToFileURL(file).href);
let out = text;
if (kind === 'opencode') {
  const p = await mod.CtxPrune();
  const o = { output: text };
  await p['tool.execute.before']({ tool: 'bash', callID: 'c1' }, { args: { command: cmd } });
  await p['tool.execute.after']({ tool: 'bash', callID: 'c1' }, o);
  out = o.output;
} else if (kind === 'pi') {
  let h;
  mod.default({ on: (ev, fn) => { if (ev === 'tool_result') h = fn; } });
  const r = await h({ toolName: 'bash', input: { command: cmd }, content: [{ type: 'text', text }] });
  out = r && r.content ? r.content[0].text : text;
} else if (kind === 'dsh') {
  let h;
  mod.apply({ on: (ev, fn) => { if (ev === 'tools/post-execute') h = fn; } });
  const d = await h({ name: 'bash', arguments: { command: cmd } }, { content: [{ type: 'text', text }] },
                    async () => ({ kind: 'accept' }));
  out = d && d.content ? d.content[0].text : text;
}
process.stdout.write(out);
"""

HERMES_RUNNER = r"""
import importlib.util, json, sys
path, cmd = sys.argv[1], sys.argv[2]
text = sys.stdin.read()
spec = importlib.util.spec_from_file_location("ctxprune_hermes_canary", path,
                                              submodule_search_locations=[__import__("os").path.dirname(path)])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
hooks = {}
class Ctx:
    def register_hook(self, name, fn): hooks[name] = fn
mod.register(Ctx())
out = hooks["transform_terminal_output"](command=cmd, output=text, returncode=0, task_id="doctor", env_type="local")
sys.stdout.write(json.dumps({"out": out, "hooks": sorted(hooks)}))
"""


class Report:
    def __init__(self):
        self.rows, self.fail, self.warn = [], 0, 0

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        self.fail += status == "FAIL"
        self.warn += status == "WARN"
        print(f"{status:<4}  {name}" + (f": {detail}" if detail else ""), flush=True)


def _canary_env(td: str, off: bool = False) -> dict:
    env = dict(os.environ)
    env["CTXPRUNE_STATE"] = os.path.join(td, "state")
    env["CTXPRUNE_STATS"] = os.path.join(td, "stats.jsonl")
    env.pop("CTXPRUNE_OFF", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"   # canaries import the installed plugin files: leave no .pyc next to them
    if off:
        env["CTXPRUNE_OFF"] = "1"
    return env


def _shrunk(out, text: str = SAMPLE) -> bool:
    return isinstance(out, str) and 0 < len(out) < len(text) and "[ctxprune" in out


def _run(cmd, env: dict, stdin: str, shell: bool = False, timeout: float = 60):
    return subprocess.run(cmd, input=stdin.encode("utf-8"), capture_output=True, env=env, shell=shell,
                          timeout=timeout)


def _ver(v) -> str:
    return ".".join(map(str, v)) if v else "?"


# ---------------------------------------------------------------- checks
def check_base(r: Report) -> None:
    r.add("OK", "ctxprune", f"{__version__} on Python {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 9):
        r.add("FAIL", "python", "ctxprune needs Python 3.9+")
    eph = ins.ephemeral_env()
    if eph:
        r.add("WARN", "environment", f"temporary environment {eph}: install with uv tool / pipx before `install`")
    rt = glue.runtime_dir()
    missing = [m for m in glue.RUNTIME_MODULES if not os.path.isfile(os.path.join(rt, m + ".py"))]
    r.add("FAIL" if missing else "OK", "runtime", f"{rt}" + (f" missing {', '.join(missing)}" if missing else ""))
    s = glue.settings()
    hs = sorted(ins.load_manifest()["harnesses"]) or list(ins.HARNESSES)
    per = {h: glue.resolved(h) for h in hs}
    lossy = [h for h, v in per.items() if v.get("model")]
    rest = [h for h in hs if h not in lossy]
    desc = ("lossless + dedup, no model" if not lossy else "line model on for " + ", ".join(lossy)
            + ("; lossless on " + ", ".join(rest) if rest else ""))
    r.add("OK", "preset", f"{s['preset']} (ctxprune_core table): {desc}")
    if lossy:
        try:
            import urllib.request
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(f"http://127.0.0.1:{s['port']}/health", timeout=2) as resp:
                ok = resp.status == 200
        except Exception:
            ok = False
        r.add("OK" if ok else "WARN", "model server", f"127.0.0.1:{s['port']} " +
              ("answers" if ok else "not running: outputs get lossless only (start: ctxprune serve --model-dir DIR)"))
    if glue.off():
        r.add("WARN", "CTXPRUNE_OFF", "is set in this environment: ctxprune is disabled here")
    r.add("OK", "paths", f"config {glue.config_dir()} | state {glue.state_dir()} | stats {glue.stats_path()}")
    with tempfile.TemporaryDirectory(prefix="ctxprune-doctor-") as td:
        cmd = [sys.executable, ins.plugin_cli(), f"--command={SAMPLE_CMD}", "--tool=bash", "--harness", "doctor"]
        try:
            on = _run(cmd, _canary_env(td), SAMPLE).stdout.decode("utf-8", "replace")
            off = _run(cmd, _canary_env(td, off=True), SAMPLE).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError) as e:
            r.add("FAIL", "pipeline canary", repr(e))
            return
        if _shrunk(on) and off == SAMPLE:
            r.add("OK", "pipeline canary", f"{len(SAMPLE)} -> {len(on)} chars; CTXPRUNE_OFF=1 passes it through")
        else:
            r.add("FAIL", "pipeline canary", f"got {len(on)} chars (on) / {'unchanged' if off == SAMPLE else 'changed'}"
                  " (off)")


def check_claude_code(r: Report) -> None:
    v = ins.claude_version()
    if v is None:
        r.add("WARN", "claude-code version", "`claude` not found on PATH")
    else:
        ok = v >= glue.MIN_CLAUDE_CODE
        r.add("OK" if ok else "FAIL", "claude-code version",
              f"{_ver(v)}" + ("" if ok else f" < {_ver(glue.MIN_CLAUDE_CODE)}: hook replacements are ignored; "
                                            "run `claude update`"))
    path = ins.cc_settings()
    try:
        cfg = ins._cc_load(path)
    except ins.InstallError as e:
        r.add("FAIL", "claude-code settings", str(e))
        return
    ours = [(g.get("matcher"), h) for g in ((cfg.get("hooks") or {}).get("PostToolUse") or []) if isinstance(g, dict)
            for h in (g.get("hooks") or []) if ins.cc_is_ours(h)]
    if not ours:
        r.add("FAIL", "claude-code hook", f"no ctxprune hook in {path} (ctxprune install claude-code)")
        return
    plugins = [k for k, v in (cfg.get("enabledPlugins") or {}).items()
               if v and str(k).split("@")[0] in ("ctxprune", "ctxprune-safe")]
    if plugins:
        r.add("WARN", "claude-code plugin", f"{', '.join(plugins)} is enabled as well as the settings.json hook: both "
              "rewrite the same output. Keep one (`/plugin disable ...` or `ctxprune uninstall claude-code`)")
    if len(ours) > 1:
        r.add("WARN", "claude-code hook", f"{len(ours)} ctxprune hooks in {path}: outputs get compressed twice "
                                          "(ctxprune install claude-code replaces them with one)")
    matcher, h = ours[-1]
    cmd = str(h.get("command", ""))
    r.add("OK" if cmd == ins.cc_command() else "WARN", "claude-code hook",
          f"{path}: matcher {matcher!r}, {cmd}" + ("" if cmd == ins.cc_command()
                                                   else " (not this install: ctxprune install claude-code)"))
    ev = {"session_id": "ctxprune-doctor", "transcript_path": "", "cwd": os.getcwd(),
          "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": SAMPLE_CMD},
          "tool_response": {"stdout": SAMPLE, "stderr": "", "interrupted": False, "isImage": False}}
    with tempfile.TemporaryDirectory(prefix="ctxprune-doctor-") as td:
        try:
            p_on = _run(cmd, _canary_env(td), json.dumps(ev), shell=True)
            p_off = _run(cmd, _canary_env(td, off=True), json.dumps(ev), shell=True)
        except (OSError, subprocess.SubprocessError) as e:
            r.add("FAIL", "claude-code canary", repr(e))
            return
        try:
            upd = json.loads(p_on.stdout)["hookSpecificOutput"]["updatedToolOutput"]
        except (ValueError, KeyError, TypeError):
            upd = None
        stats_ok = os.path.exists(os.path.join(td, "stats.jsonl"))
        if upd and _shrunk(upd.get("stdout")) and upd.get("isImage") is False and not p_off.stdout.strip():
            r.add("OK", "claude-code canary", f"Bash stdout {len(SAMPLE)} -> {len(upd['stdout'])} chars, other fields "
                  f"kept; CTXPRUNE_OFF=1 -> no replacement; stats {'logged' if stats_ok else 'NOT logged'}")
        else:
            r.add("FAIL", "claude-code canary", f"exit {p_on.returncode}, stdout {p_on.stdout[:200]!r}, "
                  f"stderr {p_on.stderr[-300:]!r}, off-stdout {p_off.stdout[:80]!r}")


def check_hermes(r: Report) -> None:
    d = ins.hermes_plugin_dir()
    init = os.path.join(d, "__init__.py")
    if not ins._is_ours_file(init):
        r.add("FAIL", "hermes plugin", f"{d} missing or not written by ctxprune (ctxprune install hermes)")
        return
    cfg = os.path.join(ins.hermes_home(), "config.yaml")
    text = ins._read(cfg)
    try:
        enabled = text is not None and ins.yaml_plugins_edit(text, "ctxprune", True) == text
    except ins.InstallError:
        enabled = None
    r.add("OK" if enabled else "WARN" if enabled is None else "FAIL", "hermes plugin",
          f"{d}; " + ("enabled in " if enabled else "could not read plugins.enabled in " if enabled is None
                      else "NOT enabled in ") + cfg)
    with tempfile.TemporaryDirectory(prefix="ctxprune-doctor-") as td:
        runner = os.path.join(td, "hermes_canary.py")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(HERMES_RUNNER)
        try:
            outs = []
            for off in (False, True):
                env = _canary_env(td, off)
                env.pop("CTXPRUNE_SCRIPTS", None)
                p = _run([sys.executable, runner, init, SAMPLE_CMD], env, SAMPLE)
                outs.append(json.loads(p.stdout or b"{}").get("out") if p.returncode == 0 else
                            f"ERROR {p.stderr[-300:]!r}")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            r.add("FAIL", "hermes canary", repr(e))
            return
    if _shrunk(outs[0]) and outs[1] is None:
        r.add("OK", "hermes canary", f"terminal output {len(SAMPLE)} -> {len(outs[0])} chars; CTXPRUNE_OFF=1 -> "
                                     "unchanged (run with this Python; Hermes uses its own)")
    else:
        r.add("FAIL", "hermes canary", f"on {str(outs[0])[:200]!r} / off {str(outs[1])[:120]!r}")


def check_js(r: Report, harness: str, path: str) -> None:
    if not ins._is_ours_file(path):
        r.add("FAIL", f"{harness} plugin", f"{path} missing or not written by ctxprune (ctxprune install {harness})")
        return
    r.add("OK", f"{harness} plugin", path)
    node = shutil.which("node")
    with tempfile.TemporaryDirectory(prefix="ctxprune-doctor-") as td:
        if not node:
            cmd = [sys.executable, ins.plugin_cli(), f"--command={SAMPLE_CMD}", "--tool=bash", "--harness", harness]
            how = "node not found: ran the CLI the plugin calls"
        else:
            runner, copy = os.path.join(td, "runner.mjs"), os.path.join(td, f"plugin-{harness}.mjs")
            with open(runner, "w", encoding="utf-8") as f:
                f.write(NODE_RUNNER)
            shutil.copyfile(path, copy)   # .mjs: ESM regardless of the nearest package.json / .ts loader
            cmd = [node, runner, harness, copy, SAMPLE_CMD]
            how = "installed plugin loaded in node with a fake harness"
        try:
            on = _run(cmd, _canary_env(td), SAMPLE)
            off = _run(cmd, _canary_env(td, off=True), SAMPLE)
        except (OSError, subprocess.SubprocessError) as e:
            r.add("FAIL", f"{harness} canary", repr(e))
            return
    o1, o2 = on.stdout.decode("utf-8", "replace"), off.stdout.decode("utf-8", "replace")
    if _shrunk(o1) and o2 == SAMPLE:
        r.add("OK", f"{harness} canary", f"{len(SAMPLE)} -> {len(o1)} chars; CTXPRUNE_OFF=1 -> unchanged ({how})")
    else:
        r.add("FAIL", f"{harness} canary", f"exit {on.returncode}, {len(o1)} chars, off {'unchanged' if o2 == SAMPLE else 'changed'}"
              f", stderr {on.stderr.decode('utf-8', 'replace')[-300:]!r}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _echo_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    got: list = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.path, self.rfile.read(n)))
            b = b'{"id":"resp_doctor","output":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def _proxy_canary(port: int, upstream_port: int, env: dict) -> tuple:
    import urllib.request
    p = subprocess.Popen(ins.proxy_argv(port, f"http://127.0.0.1:{upstream_port}"), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if p.poll() is not None:
                    return None, p.stderr.read().decode("utf-8", "replace")[-400:]
                time.sleep(0.1)
        body = {"model": "doctor", "input": [
            {"type": "function_call", "call_id": "c1", "name": "shell",
             "arguments": json.dumps({"command": ["bash", "-lc", SAMPLE_CMD]})},
            {"type": "function_call_output", "call_id": "c1", "output": SAMPLE}]}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/responses", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer doctor"})
        with op.open(req, timeout=30) as resp:
            resp.read()
        return True, ""
    except Exception as e:
        return None, repr(e)
    finally:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def check_codex(r: Report) -> None:
    cfg = ins.codex_config()
    text = ins._read(cfg) or ""
    r.add("OK" if ins.BLOCK_BEGIN in text else "FAIL", "codex config",
          f"{cfg}: " + ("[model_providers.ctxprune] + [profiles.ctxprune]; use `codex --profile ctxprune`"
                        if ins.BLOCK_BEGIN in text else "no ctxprune block (ctxprune install codex)"))
    sf = ins.service_file()
    if sf:
        r.add("OK" if ins._is_ours_file(sf) else "WARN", "codex proxy service",
              sf if ins._is_ours_file(sf) else f"{sf} missing (ctxprune install codex)")
    from .stats import _proxy_stats
    port = glue.settings()["proxy_port"]
    up = _proxy_stats(port)
    r.add("OK" if up is not None else "WARN", "codex proxy", f"127.0.0.1:{port} " +
          ("is running" if up is not None else "is not running: start the service (see `ctxprune install codex` "
                                              "output) or run `ctxprune proxy` in a terminal"))
    with tempfile.TemporaryDirectory(prefix="ctxprune-doctor-") as td:
        results = []
        for off in (False, True):
            srv, got = _echo_server()
            try:
                ok, err = _proxy_canary(_free_port(), srv.server_address[1],
                                         _canary_env(os.path.join(td, "off" if off else "on"), off))
            finally:
                srv.shutdown()
            out = None
            if ok and got:
                try:
                    out = json.loads(got[-1][1])["input"][1]["output"]
                except (ValueError, KeyError, IndexError, TypeError):
                    out = None
            results.append((out, err, got[-1][0] if got else None))
    (o1, e1, path1), (o2, e2, _) = results
    if _shrunk(o1) and o2 == SAMPLE and path1 == "/v1/responses":
        r.add("OK", "codex canary", f"proxy forwarded /v1/responses with the tool output {len(SAMPLE)} -> {len(o1)} "
                                    "chars; CTXPRUNE_OFF=1 -> unchanged")
    else:
        r.add("FAIL", "codex canary", f"on {str(o1)[:120]!r} {e1} / off {'unchanged' if o2 == SAMPLE else o2!r} {e2}")


def main(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ctxprune doctor")
    ap.add_argument("harness", nargs="*", help="only these (default: every installed one)")
    a = ap.parse_args(argv)
    r = Report()
    check_base(r)
    m = ins.load_manifest()
    targets = a.harness or sorted(m["harnesses"])
    if not targets:
        found = ins.detected()
        r.add("WARN", "integrations", "none installed yet" + (f"; found {', '.join(found)}: ctxprune install --all"
                                                              if found else ""))
    checks = {"claude-code": check_claude_code, "hermes": check_hermes, "codex": check_codex,
              "pi": lambda rr: check_js(rr, "pi", ins.pi_file()),
              "opencode": lambda rr: check_js(rr, "opencode", ins.opencode_file()),
              "dsh": lambda rr: check_js(rr, "dsh", ins.dsh_file())}
    for h in targets:
        if h not in checks:
            r.add("WARN", h, "unknown harness")
            continue
        try:
            checks[h](r)
        except Exception as e:  # noqa: BLE001 - a doctor bug must not hide the other checks
            r.add("FAIL", h, f"doctor error {e!r}")
    print(f"\n{r.fail} failed, {r.warn} warnings")
    return 1 if r.fail else 0
