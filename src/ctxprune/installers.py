"""`ctxprune install / uninstall` for each harness.

Rules for every config edit:
  * parse the file or refuse to touch it (nothing written, the reason printed);
  * idempotent: a second install leaves every file byte-identical;
  * the first time a file we change already exists, a copy goes to <config dir>/backups/<harness>/ (never overwritten);
  * uninstall removes only our own entries / sentinel blocks / files and leaves the backup where it is;
  * writes are atomic (temp file + replace) and keep the file's mode.
Nothing here runs a harness binary (`hermes plugins enable` would build a whole runtime) or systemctl, unless asked
(`--start` for the Codex proxy service).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import __version__, glue

SENTINEL = "ctxprune-managed"
HARNESSES = ("claude-code", "hermes", "pi", "opencode", "dsh", "codex")
BLOCK_BEGIN = "# >>> ctxprune-managed >>>"
BLOCK_END = "# <<< ctxprune-managed <<<"


class InstallError(Exception):
    pass


# ---------------------------------------------------------------- file helpers
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path: str, text: str, mode: int | None = None) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o7777 if mode is None else mode
    except FileNotFoundError:
        pass
    tmp = os.path.join(d, f".{os.path.basename(path)}.ctxprune-tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
    os.replace(tmp, path)


def backups_dir(harness: str) -> str:
    return os.path.join(glue.config_dir(), "backups", harness)


def backup_once(harness: str, path: str) -> str | None:
    """Copy an existing file/dir into the backups dir once (never overwritten). Returns the backup path or None."""
    if not os.path.lexists(path):
        return None
    dst = os.path.join(backups_dir(harness), os.path.basename(path.rstrip("/\\")))
    if os.path.lexists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
    if os.path.isdir(path):
        shutil.copytree(path, dst, symlinks=True)
    else:
        shutil.copy2(path, dst)
    return dst


def _update(harness: str, path: str, new: str, result: dict) -> None:
    """Write `new` if it differs (backing the old file up first); record what happened in result."""
    old = _read(path)
    if old == new:
        return
    b = backup_once(harness, path) if old is not None else None
    if b:
        result.setdefault("backups", []).append(b)
    _write(path, new)
    result.setdefault("changed", []).append(path)


def _remove(path: str, result: dict) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)
    else:
        return
    result.setdefault("removed", []).append(path)


# ---------------------------------------------------------------- shared bits
def python_exe() -> str:
    return sys.executable or "python3"


def pkg_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def plugin_cli() -> str:
    return os.path.join(pkg_dir(), "plugin_cli.py")


def integration_src(*parts: str) -> str:
    p = os.path.join(glue.runtime_dir(), "integrations", *parts)
    if not os.path.exists(p):
        raise InstallError(f"missing packaged file {p} (broken install? reinstall ctxprune)")
    return p


def ephemeral_env() -> str | None:
    """A python that will disappear (uvx / pip-run / temp venv): its path must not be written into configs."""
    p = os.path.realpath(sys.prefix).replace("\\", "/")
    if re.search(r"/(archive|builds)-v\d+/", p + "/"):   # uv's cache layout (uvx, `uv run --with`), any UV_CACHE_DIR
        return p
    for marker in ("/pipx/.cache/", "/pip-run-", "/pip-build-env-"):
        if marker in p:
            return p
    return None


def js_header(harness: str) -> str:
    return (f"// {SENTINEL}: written by `ctxprune install {harness}` (ctxprune {__version__}); "
            f"`ctxprune uninstall {harness}` removes it.\n"
            f"// Below the next two lines is scripts/integrations/{harness} unchanged.\n"
            f"process.env.CTXPRUNE_PYTHON ??= {json.dumps(python_exe())};\n"
            f"process.env.CTXPRUNE_CLI ??= {json.dumps(plugin_cli())};\n")


def _js_plugin_text(harness: str, *src: str) -> str:
    with open(integration_src(*src), encoding="utf-8") as f:
        return js_header(harness) + f.read()


def _is_ours_file(path: str) -> bool:
    t = _read(path)
    return t is not None and SENTINEL in t[:400]


def _install_file(harness: str, path: str, text: str, result: dict) -> None:
    if os.path.lexists(path) and not _is_ours_file(path):   # someone else's copy (e.g. the research checkout's)
        b = backup_once(harness, path)
        if b:
            result.setdefault("backups", []).append(b)
        result.setdefault("notes", []).append(f"replaced a non-managed {path} (backup: {b})")
    if _read(path) != text:
        _write(path, text, 0o644)
        result.setdefault("changed", []).append(path)


def _uninstall_file(path: str, result: dict) -> None:
    if _is_ours_file(path):
        _remove(path, result)
    elif os.path.lexists(path):
        result.setdefault("notes", []).append(f"left {path} alone (not written by ctxprune install)")


# ---------------------------------------------------------------- Claude Code
def cc_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def cc_settings() -> str:
    return os.path.join(cc_dir(), "settings.json")


def cc_command() -> str:
    # by path, not `-m ctxprune`: Claude Code runs hooks in the user's project dir, which `-m` puts first on sys.path
    return f'"{python_exe()}" "{plugin_cli()}" hook claude-code'


_CC_OURS = re.compile(r"(ctxprune.*hook claude-code)|((^|[/\\\s\"'])(lossless_hook|v2_hook)\.py)")


def cc_is_ours(h) -> bool:
    return isinstance(h, dict) and bool(_CC_OURS.search(str(h.get("command", ""))))


def _cc_load(path: str) -> dict:
    t = _read(path)
    if t is None or not t.strip():
        return {}
    try:
        d = json.loads(t)
    except ValueError as e:
        raise InstallError(f"{path} is not valid JSON ({e}); fix or remove it, nothing was written") from None
    if not isinstance(d, dict):
        raise InstallError(f"{path} is not a JSON object; nothing was written")
    return d


def _cc_strip(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PostToolUse"), list):
        return cfg
    post = []
    for g in hooks["PostToolUse"]:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            kept = [h for h in g["hooks"] if not cc_is_ours(h)]
            if not kept:
                continue
            if len(kept) != len(g["hooks"]):
                g = dict(g, hooks=kept)
        post.append(g)
    hooks["PostToolUse"] = post
    if not post:
        hooks.pop("PostToolUse")
    if not hooks:
        cfg.pop("hooks")
    return cfg


def install_claude_code(opts: dict) -> dict:
    path = cc_settings()
    cfg = _cc_load(path)
    new = _cc_strip(cfg)
    post = new.setdefault("hooks", {}).setdefault("PostToolUse", [])
    post.append({"matcher": glue.HOOK_MATCHER, "hooks": [{"type": "command", "command": cc_command(),
                                                          "timeout": glue.DEFAULTS["hook_timeout"]}]})
    res: dict = {"config": path}
    if new != cfg or _read(path) is None:
        _update("claude-code", path, json.dumps(new, indent=2) + "\n", res)
    v = claude_version()
    if v and v < glue.MIN_CLAUDE_CODE:
        res.setdefault("notes", []).append(f"Claude Code {'.'.join(map(str, v))} is older than "
                                           f"{'.'.join(map(str, glue.MIN_CLAUDE_CODE))}: the hook output is ignored "
                                           "until you update (claude update)")
    return res


def uninstall_claude_code(opts: dict) -> dict:
    path = cc_settings()
    res: dict = {"config": path}
    if _read(path) is None:
        return res
    cfg = _cc_load(path)
    new = _cc_strip(cfg)
    if new != cfg:
        _write(path, json.dumps(new, indent=2) + "\n")
        res.setdefault("changed", []).append(path)
    return res


def claude_version(exe: str | None = None) -> tuple | None:
    exe = exe or shutil.which("claude")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    return tuple(int(x) for x in m.groups()) if m else None


# ---------------------------------------------------------------- Hermes
def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")


def hermes_plugin_dir() -> str:
    return os.path.join(hermes_home(), "plugins", "ctxprune")


def _yaml_top_block(lines: list, key: str):
    """(start, end) line indices of a top-level `key:` block (end exclusive), or None."""
    pat = re.compile(rf"^{re.escape(key)}\s*:(.*)$")
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if m:
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")
                                      or lines[j][:1] in (" ", "\t")):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():   # trailing blank lines belong outside
                j -= 1
            return i, j, m.group(1)
    return None


def yaml_plugins_edit(text: str, name: str, enable: bool) -> str:
    """Add/remove `name` in Hermes' config.yaml `plugins.enabled` list with a minimal text edit. Raises InstallError
    for shapes it doesn't understand (then the user adds one line by hand)."""
    lines = text.split("\n")
    blk = _yaml_top_block(lines, "plugins")
    item = re.compile(rf"^(\s*)-\s*['\"]?{re.escape(name)}['\"]?\s*(#.*)?$")
    if blk is None:
        if not enable:
            return text
        sep = "" if text.endswith("\n") or not text else "\n"
        return text + sep + f"plugins:\n  enabled:\n    - {name}\n"
    s, e, rest = blk
    if rest.strip() and not rest.strip().startswith("#"):
        raise InstallError("config.yaml has an inline `plugins:` value")
    # the enabled key inside the block
    en = None
    for i in range(s + 1, e):
        m = re.match(r"^(\s+)enabled\s*:(.*)$", lines[i])
        if m:
            en = (i, m.group(1), m.group(2).strip())
            break
    if en is None:
        if not enable:
            return text
        ind = next((re.match(r"^(\s+)", lines[i]).group(1) for i in range(s + 1, e)
                    if lines[i].strip() and not lines[i].lstrip().startswith("#")), "  ")
        lines[s + 1:s + 1] = [f"{ind}enabled:", f"{ind}  - {name}"]
        return "\n".join(lines)
    i, ind, val = en
    val = re.sub(r"\s+#.*$", "", val)
    if val.startswith("["):   # flow list
        if not val.endswith("]"):
            raise InstallError("config.yaml plugins.enabled is a multi-line flow list")
        items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        if enable and name not in items:
            items.append(name)
        if not enable:
            items = [x for x in items if x != name]
        if not items:
            lines[i] = f"{ind}enabled: []"
        else:
            lines[i] = f"{ind}enabled:"
            lines[i + 1:i + 1] = [f"{ind}  - {x}" for x in items]
        return "\n".join(lines)
    if val:
        raise InstallError("config.yaml plugins.enabled has an unexpected value")
    # block list: items follow, indented at least as deep as `enabled`
    j, last, item_ind, found = i + 1, i, ind + "  ", []
    while j < e:
        ln = lines[j]
        if ln.strip() and not ln.lstrip().startswith("#"):
            m = re.match(r"^(\s*)-\s*(.*)$", ln)
            if not m or len(m.group(1)) < len(ind):
                break
            item_ind = m.group(1)
            if item.match(ln):
                found.append(j)
            last = j
        j += 1
    if enable:
        if found:
            return text
        lines[last + 1:last + 1] = [f"{item_ind}- {name}"]
        return "\n".join(lines)
    if not found:
        return text
    for k in reversed(found):
        del lines[k]
    k = i + 1   # any item left under `enabled:`? else make it an explicit empty list
    while k < len(lines) and (not lines[k].strip() or lines[k].lstrip().startswith("#")):
        k += 1
    m = re.match(r"^(\s*)-\s", lines[k]) if k < len(lines) else None
    if not m or len(m.group(1)) < len(ind):
        lines[i] = f"{ind}enabled: []"
    return "\n".join(lines)


def _sync_dir(harness: str, src: str, dst: str, res: dict) -> None:
    """Make dst a byte copy of src (write changed files only, drop extras): a re-install changes nothing."""
    want = {}
    for root, _dirs, files in os.walk(src):
        for f in files:
            want[os.path.relpath(os.path.join(root, f), src)] = os.path.join(root, f)
    for root, dirs, files in os.walk(dst, topdown=False):
        if "__pycache__" in root.replace("\\", "/").split("/"):
            continue   # the interpreter's byte-code cache: not ours to track
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), dst)
            if rel not in want:
                os.remove(os.path.join(root, f))
                res.setdefault("changed", []).append(os.path.join(dst, rel))
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    for rel, path in sorted(want.items()):
        with open(path, "rb") as f:
            data = f.read()
        out = os.path.join(dst, rel)
        try:
            with open(out, "rb") as f:
                same = f.read() == data
        except OSError:
            same = False
        if not same:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
            res.setdefault("changed", []).append(out)


def install_hermes(opts: dict) -> dict:
    """The self-contained bundle (runtime copied into the plugin dir: Hermes runs in its own venv) + plugins.enabled."""
    import tempfile
    from . import bundle
    d = hermes_plugin_dir()
    res: dict = {"config": os.path.join(hermes_home(), "config.yaml"), "plugin": d}
    init = os.path.join(d, "__init__.py")
    if os.path.isdir(d) and not _is_ours_file(init):
        b = backup_once("hermes", d)
        res.setdefault("notes", []).append(f"replaced a non-managed {d} (backup: {b})")
        shutil.rmtree(d)
    safe_only = not glue.resolved("hermes").get("model")
    with tempfile.TemporaryDirectory(prefix="ctxprune-hermes-") as td:
        bundle.build_hermes(td, safe_only=safe_only, stats=True)   # feeds `ctxprune stats` (counts only)
        _sync_dir("hermes", td, d, res)
    cfg = res["config"]
    old = _read(cfg) or ""
    try:
        new = yaml_plugins_edit(old, "ctxprune", True)
    except InstallError as e:
        res.setdefault("notes", []).append(f"{e}: add `ctxprune` to plugins.enabled in {cfg} by hand "
                                           "(or run `hermes plugins enable ctxprune`)")
        return res
    if new != old or not os.path.exists(cfg):
        _update("hermes", cfg, new, res)
    return res


def uninstall_hermes(opts: dict) -> dict:
    d = hermes_plugin_dir()
    res: dict = {"config": os.path.join(hermes_home(), "config.yaml"), "plugin": d}
    if _is_ours_file(os.path.join(d, "__init__.py")):
        _remove(d, res)
    elif os.path.isdir(d):
        res.setdefault("notes", []).append(f"left {d} alone (not written by ctxprune install)")
    cfg = res["config"]
    old = _read(cfg)
    if old is not None and not os.path.isdir(d):
        try:
            new = yaml_plugins_edit(old, "ctxprune", False)
        except InstallError as e:
            res.setdefault("notes", []).append(f"{e}: remove `ctxprune` from plugins.enabled in {cfg} by hand")
            return res
        if new != old:
            _write(cfg, new)
            res.setdefault("changed", []).append(cfg)
    return res


# ---------------------------------------------------------------- Pi / OpenCode
def pi_dir() -> str:
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(os.path.expanduser("~"), ".pi", "agent")


def pi_file() -> str:
    return os.path.join(pi_dir(), "extensions", "ctxprune.ts")


def opencode_dir() -> str:
    # OpenCode uses ~/.config/opencode on every OS (xdg-basedir), XDG_CONFIG_HOME when set
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                        "opencode")


def opencode_file() -> str:
    return os.path.join(opencode_dir(), "plugins", "ctxprune.js")


def install_pi(opts: dict) -> dict:
    res: dict = {"plugin": pi_file()}
    _install_file("pi", pi_file(), _js_plugin_text("pi", "pi", "ctxprune.ts"), res)
    return res


def uninstall_pi(opts: dict) -> dict:
    res: dict = {"plugin": pi_file()}
    _uninstall_file(pi_file(), res)
    return res


def install_opencode(opts: dict) -> dict:
    res: dict = {"plugin": opencode_file()}
    _install_file("opencode", opencode_file(), _js_plugin_text("opencode", "opencode", "ctxprune.js"), res)
    return res


def uninstall_opencode(opts: dict) -> dict:
    res: dict = {"plugin": opencode_file()}
    _uninstall_file(opencode_file(), res)
    return res


# ---------------------------------------------------------------- dsh
def dsh_home() -> str:
    return os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")


def dsh_file() -> str:
    return os.path.join(dsh_home(), "ctxprune-plugin", "ctxprune.mjs")


def dsh_patch() -> str:
    return os.path.join(dsh_home(), "cordis.patch.yml")


def _strip_block(text: str) -> str:
    """Remove our sentinel block (and the newline before it that we added)."""
    pat = re.compile(rf"\n?{re.escape(BLOCK_BEGIN)}\n.*?{re.escape(BLOCK_END)}\n?", re.S)
    return pat.sub("\n", text).rstrip("\n") + "\n" if pat.search(text) else text


def _add_block(text: str, body: str) -> str:
    base = _strip_block(text)
    if not base.strip():
        base = ""
    elif not base.endswith("\n"):
        base += "\n"
    return base + ("\n" if base else "") + f"{BLOCK_BEGIN}\n{body.rstrip()}\n{BLOCK_END}\n"


def install_dsh(opts: dict) -> dict:
    res: dict = {"config": dsh_patch(), "plugin": dsh_file()}
    _install_file("dsh", dsh_file(), _js_plugin_text("dsh", "dsh", "ctxprune.mjs"), res)
    old = _read(dsh_patch()) or ""
    body_lines = [ln for ln in _strip_block(old).split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if body_lines and not body_lines[0].startswith("- "):
        raise InstallError(f"{dsh_patch()} is not a block-style YAML list; add this item by hand:\n"
                           f"- insert:\n    - id: ctxprune\n      name: {json.dumps(dsh_file())}")
    block = f"- insert:\n    - id: ctxprune\n      name: {json.dumps(dsh_file())}"
    new = _add_block(old, block)
    if new != old:
        _update("dsh", dsh_patch(), new, res)
    return res


def uninstall_dsh(opts: dict) -> dict:
    res: dict = {"config": dsh_patch(), "plugin": dsh_file()}
    _uninstall_file(dsh_file(), res)
    try:
        os.rmdir(os.path.dirname(dsh_file()))
    except OSError:
        pass
    old = _read(dsh_patch())
    if old is not None and BLOCK_BEGIN in old:
        new = _strip_block(old)
        if not new.strip():
            _remove(dsh_patch(), res)
        else:
            _write(dsh_patch(), new)
            res.setdefault("changed", []).append(dsh_patch())
    return res


# ---------------------------------------------------------------- Codex (proxy)
def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def codex_config() -> str:
    return os.path.join(codex_home(), "config.toml")


def proxy_argv(port: int | None = None, upstream: str | None = None, harness: str = "codex") -> list:
    s = glue.settings()
    return [python_exe(), plugin_cli(), "proxy", "--upstream", upstream or s["proxy_upstream"],
            "--port", str(port or s["proxy_port"])] + (["--harness", harness] if harness else [])


def service_file() -> str | None:
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", "dev.ctxprune.proxy.plist")
    if glue.is_windows():
        return None
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                        "systemd", "user", "ctxprune-proxy.service")


def _q(a: str) -> str:
    """Quote one argument for a systemd ExecStart line / a printed command, only when needed."""
    if a and re.fullmatch(r"[\w@%+=:,./-]+", a):
        return a
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_text(argv: list) -> str:
    if sys.platform == "darwin":
        args = "\n".join(f"    <string>{a.replace('&', '&amp;').replace('<', '&lt;')}</string>" for a in argv)
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!-- {SENTINEL} (ctxprune {__version__}) -->\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n'
                "  <key>Label</key><string>dev.ctxprune.proxy</string>\n  <key>ProgramArguments</key><array>\n"
                f"{args}\n  </array>\n  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n</dict></plist>\n")
    return (f"# {SENTINEL}: written by `ctxprune install codex` (ctxprune {__version__})\n[Unit]\n"
            "Description=ctxprune proxy (lossless tool-output compression for Codex)\n\n[Service]\n"
            f"ExecStart={' '.join(_q(a) for a in argv)}\nRestart=always\nRestartSec=1\n\n[Install]\nWantedBy=default.target\n")


def service_commands(start: bool) -> list:
    f = service_file()
    if sys.platform == "darwin":
        return [["launchctl", "load", "-w", f]] if start else [["launchctl", "unload", "-w", f]]
    if glue.is_windows():
        return []
    if start:
        return [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now",
                                                           "ctxprune-proxy.service"]]
    return [["systemctl", "--user", "disable", "--now", "ctxprune-proxy.service"]]


def codex_block(port: int) -> str:
    return (f'# written by `ctxprune install codex` (ctxprune {__version__}); use: codex --profile ctxprune\n'
            "[model_providers.ctxprune]\n"
            'name = "OpenAI via ctxprune"\n'
            f'base_url = "http://127.0.0.1:{port}/v1"\n'
            'env_key = "OPENAI_API_KEY"\n'
            'wire_api = "responses"\n\n'
            "[profiles.ctxprune]\n"
            'model_provider = "ctxprune"\n')


_CODEX_CLASH = re.compile(r"(?m)^\s*\[\s*(model_providers\.ctxprune|profiles\.ctxprune)\s*\]")


def _run(cmds: list, res: dict) -> None:
    for c in cmds:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=60)
            res.setdefault("ran", []).append(" ".join(c) + f"  -> exit {r.returncode}")
        except (OSError, subprocess.SubprocessError) as e:
            res.setdefault("ran", []).append(" ".join(c) + f"  -> {e!r}")


def install_codex(opts: dict) -> dict:
    port = int(opts.get("port") or glue.settings()["proxy_port"])
    res: dict = {"config": codex_config()}
    old = _read(codex_config()) or ""
    if _CODEX_CLASH.search(_strip_block(old)):
        raise InstallError(f"{codex_config()} already defines [model_providers.ctxprune] or [profiles.ctxprune] "
                           "outside the ctxprune-managed block; nothing was written")
    new = _add_block(old, codex_block(port))
    if new != old:
        _update("codex", codex_config(), new, res)
    argv = proxy_argv(port, opts.get("upstream"))
    sf = service_file()
    if sf:
        res["service"] = sf
        if os.path.exists(sf) and not _is_ours_file(sf):
            b = backup_once("codex", sf)
            res.setdefault("notes", []).append(f"replaced a non-managed {sf} (backup: {b})")
        if _read(sf) != service_text(argv):
            _write(sf, service_text(argv), 0o644)
            res.setdefault("changed", []).append(sf)
        cmds = service_commands(True)
        if opts.get("start"):
            _run(cmds, res)
        else:
            res.setdefault("next", []).extend(" ".join(c) for c in cmds)
    else:   # Windows: no service manager we write to
        cmd = " ".join(_q(a) for a in argv)
        res.setdefault("next", []).append(
            f'schtasks /Create /SC ONLOGON /TN ctxprune-proxy /TR "{cmd.replace(chr(34), chr(92) + chr(34))}"  '
            f"(or run it in a terminal: {cmd})")
    res.setdefault("next", []).append("codex --profile ctxprune   (API-key auth via OPENAI_API_KEY; ChatGPT-login "
                                      "accounts are not verified to work through the proxy)")
    return res


def uninstall_codex(opts: dict) -> dict:
    res: dict = {"config": codex_config()}
    old = _read(codex_config())
    if old is not None and BLOCK_BEGIN in old:
        new = _strip_block(old)
        _write(codex_config(), new)
        res.setdefault("changed", []).append(codex_config())
    sf = service_file()
    if sf and _is_ours_file(sf):
        cmds = service_commands(False)
        if opts.get("start"):
            _run(cmds, res)
        else:
            res.setdefault("next", []).extend(" ".join(c) for c in cmds)
        _remove(sf, res)
    if glue.is_windows():
        res.setdefault("next", []).append("schtasks /Delete /TN ctxprune-proxy /F   (if you created it)")
    return res


# ---------------------------------------------------------------- registry
INSTALL = {"claude-code": install_claude_code, "hermes": install_hermes, "pi": install_pi,
           "opencode": install_opencode, "dsh": install_dsh, "codex": install_codex}
UNINSTALL = {"claude-code": uninstall_claude_code, "hermes": uninstall_hermes, "pi": uninstall_pi,
             "opencode": uninstall_opencode, "dsh": uninstall_dsh, "codex": uninstall_codex}


def detected() -> list:
    """Harnesses that look installed for this user (for --all): a config dir or the binary on PATH."""
    home = os.path.expanduser("~")
    probes = {"claude-code": (cc_dir(), "claude"), "hermes": (hermes_home(), "hermes"),
              "pi": (os.path.join(home, ".pi"), "pi"), "opencode": (opencode_dir(), "opencode"),
              "dsh": (dsh_home(), "dsh"), "codex": (codex_home(), "codex")}
    if os.environ.get("PI_CODING_AGENT_DIR"):
        probes["pi"] = (pi_dir(), "pi")
    return [h for h, (d, exe) in probes.items() if os.path.isdir(d) or shutil.which(exe)]


def manifest_path() -> str:
    return os.path.join(glue.config_dir(), "installed.json")


def load_manifest() -> dict:
    t = _read(manifest_path())
    try:
        d = json.loads(t) if t else {}
    except ValueError:
        d = {}
    d.setdefault("harnesses", {})
    return d


def save_manifest(m: dict) -> None:
    m["version"], m["python"] = __version__, python_exe()
    os.makedirs(glue.config_dir(), exist_ok=True)
    _write(manifest_path(), json.dumps(m, indent=2, sort_keys=True) + "\n", 0o600)


def save_preset(preset: str) -> None:
    p = glue.settings_file()
    cur = glue.user_settings()
    if cur.get("preset") == preset or (preset == glue.DEFAULTS["preset"] and "preset" not in cur):
        return
    cur["preset"] = preset
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _write(p, json.dumps(cur, indent=2, sort_keys=True) + "\n", 0o600)


def install(harness: str, opts: dict) -> dict:
    res = INSTALL[harness](opts)
    m = load_manifest()
    entry = {"opts": {k: v for k, v in opts.items() if k in ("port", "upstream", "start") and v},
             "files": sorted(v for k, v in res.items() if k in ("config", "plugin", "service") and isinstance(v, str))}
    old = dict(m["harnesses"].get(harness) or {})
    old_ts = old.pop("ts", None)
    if old != entry or old_ts is None or m.get("version") != __version__ or m.get("python") != python_exe():
        m["harnesses"][harness] = dict(entry, ts=int(time.time()))   # unchanged install: manifest byte-identical
        save_manifest(m)
    return res


def uninstall(harness: str, opts: dict) -> dict:
    m = load_manifest()
    o = dict((m["harnesses"].get(harness) or {}).get("opts") or {})
    o.update({k: v for k, v in opts.items() if v})
    res = UNINSTALL[harness](o)
    if m["harnesses"].pop(harness, None) is not None:
        save_manifest(m)
    return res
