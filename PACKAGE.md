# ctxprune

Makes agent tool outputs smaller before the model reads them, without losing anything. The default `safe`
preset is lossless: it groups grep hits by file, folds repeated lines and JSON, strips ANSI codes, and replaces
output the model has already seen with a pointer to it. No model, no server, standard library only.

Works with Claude Code (hook), Hermes, Pi, OpenCode, dsh (plugins) and Codex (local proxy).

## Install

```sh
uv tool install ctxprune        # or: pipx install ctxprune
ctxprune install --all          # every supported agent found on this machine
ctxprune doctor                 # checks versions and runs a test call through each integration
```

One agent only: `ctxprune install claude-code` (also `hermes`, `pi`, `opencode`, `dsh`, `codex`).
Don't run `install` through `uvx`: it writes the interpreter path into your agent configs, and uvx deletes that
environment later. `ctxprune install` refuses to run from one.

## Use

- `ctxprune stats`: tokens saved so far and a rough dollar estimate (`--price` = your model's USD per
  million input tokens). The log keeps counts only, never tool output or commands.
- `CTXPRUNE_OFF=1`: turns ctxprune off, either in the agent's environment or inside one command
  (`CTXPRUNE_OFF=1 cat big.log`).
- `ctxprune upgrade`: upgrades the package (uv tool, pipx or pip, whichever installed it) and rewrites
  every installed integration for the new version.
- `ctxprune uninstall --all`: removes everything it added. Your original config files stay in the backups
  folder (see below).

## What `install` changes

Each install is idempotent, and the first time it changes a file it copies the original to
`<config>/backups/<agent>/`.

| Agent | File | Change |
|---|---|---|
| Claude Code (2.1.121 or newer) | `~/.claude/settings.json` (`$CLAUDE_CONFIG_DIR`) | one `PostToolUse` hook, matcher `Bash\|Grep\|mcp__.*`; older ctxprune hooks are replaced |
| Hermes (0.21+) | `~/.hermes/plugins/ctxprune/`, `config.yaml` (`$HERMES_HOME`) | the self-contained plugin (runtime copied in, since Hermes runs in its own venv), added to `plugins.enabled` |
| Pi | `~/.pi/agent/extensions/ctxprune.ts` (`$PI_CODING_AGENT_DIR`) | the extension |
| OpenCode | `~/.config/opencode/plugins/ctxprune.js` (`$XDG_CONFIG_HOME`) | the plugin |
| dsh | `~/.dsh/ctxprune-plugin/ctxprune.mjs`, `~/.dsh/cordis.patch.yml` (`$DSH_HOME`) | the plugin, one `insert` patch item |
| Codex | `~/.codex/config.toml` (`$CODEX_HOME`), a user service | provider + profile `ctxprune`; the proxy on 127.0.0.1:8787 |

Codex: run `codex --profile ctxprune`. `install codex` writes the service file and prints the command that
starts it (`--start` runs that command for you). What has been checked so far is the proxy itself: it rewrites a
Responses-API tool output on its way to an upstream. Codex has not yet been run through it, so the provider
block (`env_key = "OPENAI_API_KEY"`, `wire_api = "responses"`) still needs a live check. ChatGPT-login accounts
are not expected to work through it.

## Presets

The preset table lives in one place: `ctxprune_core.PRESETS` / `PRESET_HARNESS` in the runtime. Packaging reads
it and adds only ports and timeouts (`ctxprune/glue.py` `DEFAULTS`).

- `safe` (default): lossless + cross-turn dedup. Dedup runs where ctxprune can see what is still in context:
  the Claude Code hook and the proxy. The Hermes, Pi, OpenCode and dsh plugins get the lossless rules only.
- `balanced` / `aggressive` (`ctxprune install --all --preset balanced`): also drop low-value lines with the v2
  line model. They need `pip install "ctxprune[model]"` (onnxruntime, tokenizers, numpy) and a running
  `ctxprune serve --model-dir DIR`. On Codex, dsh, OpenCode and Pi, `balanced` stays `safe` unless you set
  `CTXPRUNE_FORCE_LOSSY=1`. Experimental: the weights are not published yet.

## No-pip plugins (catalog / marketplace builds)

`ctxprune bundle hermes DIR` and `ctxprune bundle claude-code DIR` write self-contained plugin folders. They
contain a byte copy of the stdlib runtime, so the target machine needs only Python 3.9+:

- Hermes (verified on 0.21.6): a plugin directory for `hermes plugins install`. It passes Hermes' plugin
  validator and declares `transform_terminal_output` and `transform_tool_result`. The safe build ships no model
  code and writes no files (`--stats` turns on the counts-only log).
- Claude Code: `.claude-plugin/marketplace.json` plus `claude-code-plugin-safe/`, which installs with
  `/plugin marketplace add riceharvest/ctxprune` and then `/plugin install ctxprune-safe@ctxprune`. Its hook
  runs `scripts/hook.sh`, which runs the bundled `lossless_hook.py` with the first `python3`/`python` it finds.
  Use either the plugin or `ctxprune install claude-code`, not both: `ctxprune doctor` warns if both are on.

## Paths

| | Linux / macOS | Windows |
|---|---|---|
| settings, install manifest, backups | `$XDG_CONFIG_HOME/ctxprune` (`~/.config/ctxprune`) | `%APPDATA%\ctxprune` |
| saved outputs, dedup state (0700) | `$XDG_CACHE_HOME/ctxprune` (`~/.cache/ctxprune`) | `%LOCALAPPDATA%\ctxprune\cache` |
| stats log (counts only, 0600) | `$XDG_STATE_HOME/ctxprune/stats.jsonl` | `%LOCALAPPDATA%\ctxprune\stats.jsonl` |

`CTXPRUNE_CONFIG_DIR`, `CTXPRUNE_STATE` and `CTXPRUNE_STATS` override these (`CTXPRUNE_STATS=off` turns the log off).

### Windows notes

- `ctxprune install` writes the full path of the Python that runs ctxprune (`sys.executable`, e.g.
  `C:\Users\you\AppData\Roaming\uv\tools\ctxprune\Scripts\python.exe`) into every config. The installed
  plugins never call a bare `python3`, which on Windows can be the Microsoft Store stub that does nothing.
- The `ctxprune-safe` marketplace plugin has no install step, so its `hook.sh` looks for an interpreter itself:
  `py -3`, then `python`, then `python3` on Windows. If none works, the hook prints nothing and Claude Code
  keeps the original output.
- On Windows, Claude Code runs hook commands through Git Bash (not tested here; check it on a Windows machine).
  The command is `"C:\...\python.exe" "C:\...\ctxprune\plugin_cli.py" hook claude-code`. Backslashes inside double quotes pass
  through bash unchanged.
- OpenCode reads `%USERPROFILE%\.config\opencode` on Windows as well (or `XDG_CONFIG_HOME` when it is set).
- Codex: Windows has no user-service manager that ctxprune writes to. `install codex` prints a `schtasks`
  command that starts the proxy at logon.
- Tool outputs with `\r\n` line endings are handled exactly by the safe preset. The grep grouping is tuned for
  `/` paths, so it saves less on `C:\` paths.
- WSL: install ctxprune on the same side as the agent. A Windows-side agent can't run a WSL Python path,
  and the reverse is true too.

## The Codex proxy

The proxy sits in the request path, so it is a hard dependency: if it dies, the harness behind it fails (from the
live canary on 2026-10-17: Claude Code exits 1 after ~180 s of retries, Hermes hangs, Pi/OpenCode exit 0 with the
task unfinished). So:

- `install --all` uses native plugins for Claude Code, Hermes, Pi, OpenCode and dsh, and the proxy only for Codex.
- The systemd unit has `Restart=always` + `RestartSec=1` (back within ~1 s after `kill -9` or OOM); launchd uses
  `KeepAlive`. The Windows `schtasks` task does **not** restart the proxy: if it dies, Codex fails until you start it.
- Kill switch: the proxy passes requests through untouched while `CTXPRUNE_OFF=1` is in its env or the file
  `<state>/OFF` exists (checked per request, no restart). The native hooks read only `CTXPRUNE_OFF`, not the file.
- Hooks/plugins record what they emit in `<state>/emitted/`, and the proxy forwards that text untouched. It only
  works with one shared state dir: the unit uses the same default as the hooks (`CTXPRUNE_STATE`, else
  `~/.cache/ctxprune`); don't give the proxy a different `--state`.

## Upgrading from 0.2.1

The 0.2.1 Claude Code plugin (`riceharvest/ctxprune`, `claude-code-plugin/hooks/hooks.json`) matches
`Bash|mcp__.*` and runs `uvx --from "ctxprune>=0.2.1" ctxprune hook claude-code` when ctxprune isn't installed.
Once 0.3.0 is on PyPI, that range resolves to 0.3.0, so existing plugin users silently switch from the 0.2.1 lossy
token model to the 0.3.0 `safe` hook (lossless + dedup), and Grep/PowerShell stay unhooked until the plugin's
matcher is updated. 0.3.0 also drops `ctxprune FILE`, the LangChain/LlamaIndex integrations and the 0.2.1 `serve`.
Updated plugin files for that repo (matcher `Bash|PowerShell|Grep|mcp__.*`, pinned to `ctxprune>=0.3,<0.4`) are
in `release/claude-code-plugin-0.3.0/` (not pushed).

## License

ctxprune is released under [0BSD](LICENSE) (public-domain equivalent, OSI approved; no attribution required).
The optional `[model]` weights are not part of this package and are not 0BSD: they are derived from the
MIT-licensed mmBERT-small and trained on data that includes the CC-BY-4.0 datasets SWE-rebench-openhands and
Nemotron-SWE, and carry their own license and attribution on the Hugging Face model card. See
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
