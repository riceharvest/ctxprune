# ctxprune

ctxprune rewrites agent tool outputs into a shorter form before the model reads them. The default `safe` preset
drops no content. It groups grep hits under their file, counts runs of identical lines, writes JSON record lists as
rows, and removes terminal colour codes and progress-bar redraws. Every structural rewrite is applied only if its
decoder gives back the original bytes, checked on each output. It runs locally as a hook or plugin in Claude Code,
Hermes, Pi, OpenCode and dsh, and as a local proxy for Codex. It uses only the Python standard library, with no
model, no network and no server.

It saves a few percent on coding work and more on MCP-heavy work. The measured numbers are below. They are smaller
than you might expect, so please read them before installing.

## Install

```sh
uv tool install ctxprune        # or: pipx install ctxprune
ctxprune install --all          # every supported agent found on this machine
ctxprune doctor                 # checks versions and runs a test call through each integration
```

To install for one agent only: `ctxprune install claude-code` (or `hermes`, `pi`, `opencode`, `dsh`, `codex`).
`ctxprune uninstall --all` removes everything it added. The first time `install` changes one of your config files,
it keeps a backup of the original. [PACKAGE.md](PACKAGE.md) lists every file it touches, the paths it uses, and notes
for Windows and for the Codex proxy.

Don't run `install` through `uvx`. It writes the interpreter path into your agent configs, and uvx deletes that
environment later. `ctxprune install` refuses to run from one.

**Claude Code plugin, without pip.** A self-contained plugin bundles the same stdlib runtime, so it needs only
Python 3.9+ on the machine:

```
/plugin marketplace add riceharvest/ctxprune
/plugin install ctxprune-safe@ctxprune
```

Use either the plugin or `ctxprune install claude-code`, not both. `ctxprune doctor` warns if both are on.

## What is exact and what isn't

"Lossless" means different things for different rules, so here is the precise version.

**Byte-exact.** The decoder (`lossless.decode`) returns the original bytes. Each rule is checked against the original
on every output, and it is dropped for that output if the check fails.
- `hits`/grep grouping: grep-style `path:line:text` runs become a `path` header plus `line:text` lines.
- `paths`: runs of bare paths in one directory are grouped under that directory. This rule is off when the model
  endpoint is local or self-hosted (see "Presets").
- `numbered`: a padded line-number gutter (`cat -n`, `nl`) is unpadded.
- `table`: an aligned table (`ps`, `docker ps`, `kubectl get`) is single-spaced. The columns are restored on decode.
- `diffhdr`: `---`/`+++` lines that repeat the `diff --git` line are dropped.
- `jsonrows`: a JSON array of 3 or more records is written as tab-separated rows under one header. The rewrite is
  used only if the exact original bytes (key order, numbers, spacing) come back.
- `sffiles`: Hermes `search_files` file lists are written one path per line.
- `a11y`: Playwright MCP accessibility snapshots drop the YAML `- ` dash, and ` [cursor=pointer]` becomes ` ↗`.
  Element refs stay byte-exact.
- Marker escaping (see [docs/SAFETY.md](docs/SAFETY.md)).

**Exact content, but no decoder.** Three or more identical consecutive lines become one line plus `[× N]`. The count
is exact, but `lossless.decode` doesn't expand it, so I don't count it as byte-exact.

**Render-preserving, not byte-exact.**
- **ANSI:** colour and title codes (SGR/OSC) are removed. Cursor moves and erases are kept. This never runs on file
  views.
- **CR:** progress-bar redraws that use `\r` are collapsed to the final state the terminal would show.

**Value-preserving, not byte-exact.** These need a JSON parser to see that nothing changed.
- **`json_compact`:** removes the whitespace between JSON tokens of pretty-printed JSON. Every key and value parses
  identically.
- **`json_unescape`:** a JSON wrapper around a text field (e.g. `{"output": "...", "exit_code": 0}`) becomes one header
  line with the other keys, followed by the text unescaped. The text is exact.

**Never touched.** File views (`Read`, `cat`, `sed -n`, `git show`/`diff`/`blame` and the like) get only the `numbered`
rule, so code lines that an agent copies into an edit stay byte-identical. Edit/Write results are not changed.

On the public traces below, the per-output checks found 0 failures. 1,529 changed outputs were decoded byte-exact,
and 4,679 were checked for identical JSON values. 2,243 outputs carry a combination the check script doesn't verify
end to end, mostly `json_compact` followed by `jsonrows`. 194 had ANSI codes removed.

## How much it saves

These figures are % of the tool-output tokens the model sees, measured with o200k on 2,225 public agent
trajectories (28,174 tool outputs). They use the `safe` preset at the 0.3.0 runtime, run through each integration's
real reach: in Claude Code that is Bash, PowerShell, Grep and MCP tools, not Read. Brackets are 95% trajectory
bootstrap CIs.

| who | saved | 95% CI |
|---|--:|--:|
| Claude Code coder, Linux/macOS (37 real sessions) | **0.98%** | 0.32–1.62 |
| Claude Code coder, Windows (18 real sessions, PowerShell) | 4.47% | 2.20–7.41 |
| Mix 1, coder with few MCP servers (coding 0.8, GitHub MCP 0.1, web search 0.1) | 2.95% | 1.73–4.33 |
| Mix 2, coder plus MCP (coding 0.5, browser 0.2, GitHub 0.15, DB 0.05, web 0.1) | 3.85% | 3.07–4.67 |
| Mix 3, MCP-heavy assistant (multi-server MCP 0.3, Notion 0.15, browser 0.2, web 0.15, API tools 0.2) | **9.42%** | 8.81–10.09 |

How to read this:
- **The weights in mixes 1–3 are my assumptions,** not measured user mixes. "Coding" in the mixes means the 55 real
  Claude Code sessions, Windows and non-Windows pooled.
- **Most of the Windows gain is removing ANSI colour codes** from PowerShell output. That is a render-preserving rule,
  not a byte-exact one.
- **Claude Code's biggest tool is Read** (58% of tool tokens in these 55 sessions). ctxprune leaves Read alone by
  design, which is why the coding number is small.
- **MCP JSON is where the structural rules pay off,** mostly through `jsonrows` and `json_compact`.
- Counted with the Qwen3.6 tokenizer, the mixes are 2.53 / 3.39 / 9.42%. I have no CIs for those.

**Big outputs Claude Code saves to a file** (Bash over 30k chars, MCP over 50k chars or over its token cap) are not in
the table above, because the model only sees a preview of them. For these, ctxprune rewrites the file itself
losslessly ("spill files", on by default). If the agent later reads a quarter of such a file, this adds
+0.17 / +0.25 / +0.24 points to mixes 1 / 2 / 3. How much of the file gets read back is an assumption; I couldn't
measure it.

Every number, the exact commands and the scripts are in [docs/BENCHMARKS.md](docs/BENCHMARKS.md). `ctxprune stats`
shows what it saved on your own machine (a counts-only log; see "Privacy").

## Which models it was tested on

I checked that models still read the rewritten output correctly. Each rule got 150 questions, asked once on the raw
output and once on the rewritten one, and accuracy was compared with a paired bootstrap. The readers were Qwen3.8-27B
(INT4), Qwen3.6-35B-A3B (NVFP4) and Gemma 4 31B (QAT W4A16), with thinking on and off.

The default rules that were probed showed no accuracy loss (all Δ within ±2 points on every reader):
- `hits`
- `paths`
- `numbered` (probed in its earlier `cat -n` form: the same unpadding, with a different marker line)
- flat `jsonrows`
- Playwright `a11y`

The JSON-row form used for MCP results passed on Qwen3.8 and Gemma. On Qwen3.6 with thinking, the extended form
(flattened `key.sub` columns, quoted keys) lost 7.3 points on those items, so that form is now opt-in. The default
form was 0.0 on the items that used it, but those were small sets (n = 20, 10 and 10).

Not probed: `table`, `diffhdr`, `sffiles`, the `[× N]` repeat counts, and the JSON compaction and unescape steps.

**Claude has not been probed.** No Claude model was used as a reader in any of these tests. The Claude Code numbers
above are about token counts, not about Claude's comprehension.

## Presets

- **`safe` (the default).** The rules above, with nothing dropped and no model.
  - The `paths` rule is off when the model endpoint is local or self-hosted (localhost, private IPs, vLLM, llama.cpp,
    Ollama, LM Studio, SGLang…). It costs self-hosted thinking models 38 to 149 extra thinking tokens each time they
    need a full path back, which is a latency loss for local models. `CTXPRUNE_PATHS=0|1` overrides this.
  - It stays on in the Claude Code hooks and for hosted APIs.
- **`balanced` and `aggressive` (opt-in, experimental).** These also drop low-value lines with a small line-scoring
  model, keep a recovery file for every dropped range, and leave file views whole.
  - They need `pip install "ctxprune[model]"` and `ctxprune serve --model-dir DIR`.
  - The 0.3.0 model weights are not published yet, so for now these presets are for experiments.
  - In my session runs, the cost results were mixed (see "Presets" in [docs/BENCHMARKS.md](docs/BENCHMARKS.md)).
- **Harnesses that opt out of lossy.** On **Codex, dsh, OpenCode and Pi**, `balanced` runs as `safe` unless you set
  `CTXPRUNE_FORCE_LOSSY=1`.
  - Codex cost +36.8% [+15.8, +60.6] with the model on: holes in file views made it re-read ranges in loops.
  - dsh used 14% more prompt tokens and 1.9 more turns per episode.
  - Pi and OpenCode didn't lower cost reliably.
  - `aggressive` applies everywhere, because choosing it is an explicit opt-in.

Pick a preset with `ctxprune install --all --preset balanced`, or set `CTXPRUNE_PRESET`.

## Opt-in rules

These rules are off by default. In the comprehension probes each one cost accuracy or extra thinking, so you should
only turn one on if you've checked it with your model.

| flag | what it does | why it's off |
|---|---|---|
| `CTXPRUNE_DEDUP=1` | Cross-turn dedup. An output, or a run of lines, that repeats something still in context becomes a reference to it. In the Claude Code hook, the proxy, and per request in the Hermes/Pi/OpenCode plugins. | Qwen3.8 lost 7.3 points with whole-output references, 18 with line-run references and 21 with references to tool inputs, and used 1.4–3.4× the thinking tokens. Only Qwen3.8 was probed. With it on, the mixes save 4.88 / 7.72 / 12.80%. |
| `CTXPRUNE_BLOCKREF=1` | A repeated run of lines inside one output becomes `[cp:= same as lines A–B]`. | It lost 27–85 points overall (3 on Qwen3.8 with the marker note) and 73–100 on "how many times" questions. |
| `CTXPRUNE_PREFIX=1` | Lines that share a prefix (e.g. `web-1  \| ` in `docker compose logs`) are written once. | Without thinking it cost 2.7–6.7 points. With thinking it used ~2.9× the thinking tokens. |
| `CTXPRUNE_JSONROWS_EXT=1` | `jsonrows` also flattens sub-objects into `key.sub` columns and allows quoted keys. | Qwen3.6 with thinking: −7.3 points on those items. |
| `CTXPRUNE_READ_TRIM=1` | Claude Code `Read`: an already-shown, unchanged prefix or suffix of the file is dropped, and the line numbers stay exact. | It saved 1.14% [0.33, 2.34] of tool tokens on real sessions, but in 1.85% of Edits it would have hidden the latest copy of the text being edited. The A/B on Edit failures hasn't run yet. **The installer's hook matcher doesn't include `Read`**, so you also have to add `Read` to the matcher in `~/.claude/settings.json` yourself. |

`CTXPRUNE_SPILL=0` turns off the spill-file rewrite.

## Turning it off

- **Kill switch:** set `CTXPRUNE_OFF=1` in the agent's environment, or put it inside one command
  (`CTXPRUNE_OFF=1 cat big.log`). The output then passes through untouched.
- **The Codex proxy** also checks per request for the file `~/.cache/ctxprune/OFF`, so you don't need to restart it.
  The native hooks read only the environment variable.
- **Uninstall:** `ctxprune uninstall --all` removes everything that `install` added.

Every integration fails open. If ctxprune crashes, hangs past its timeout, or can't import, the agent gets the
original output. The exception is the in-process Hermes plugin, which has no timeout (details in
[docs/SAFETY.md](docs/SAFETY.md)).

## Privacy

Nothing leaves your machine. ctxprune makes no network calls in the `safe` preset. What it stores locally:

| what | where (Linux/macOS) | contents | kept |
|---|---|---|---|
| stats log | `~/.local/state/ctxprune/stats.jsonl` | counts only: time, agent, tool name, chars before/after. No tool output, no commands. `CTXPRUNE_STATS=off` disables it. | until you delete it |
| "already sent" registry | `~/.cache/ctxprune/emitted/` | one empty file per sha256 of a rewritten output. No text. | 14 days |
| dedup state (only with `CTXPRUNE_DEDUP=1` or Read trim) | `~/.cache/ctxprune/` | the last ~100 rendered outputs per session | 30 days (`CTXPRUNE_DEDUP_KEEP_DAYS`) |
| recovery files (only `balanced`/`aggressive`) | `~/.cache/ctxprune/out/` | the full output, so the agent can read dropped ranges | 7 days |
| Codex proxy cache | `~/.cache/ctxprune/proxy-cache-<preset>.jsonl` | the rewritten text of each tool result, by hash | **not pruned yet:** delete it while the proxy is stopped |

- State directories are created 0700 and files 0600.
- Spill files are Claude Code's own `tool-results/` files. ctxprune rewrites them in place, losslessly. It doesn't
  create new copies.
- On Windows the same files live under `%LOCALAPPDATA%\ctxprune`.

## Limitations

- **Tested platform.** I've only run it on Linux. A CI matrix for macOS and Windows (Python 3.9–3.13) exists, but it
  hasn't run against this release yet. The Windows notes in [PACKAGE.md](PACKAGE.md) come from reading the code, not
  from a Windows machine.
- **Codex needs the proxy.**
  - Codex hooks can't replace shell output, so ctxprune sits in front of the API as a local proxy (127.0.0.1:8787).
  - I've checked the proxy's request rewriting, but I haven't run Codex itself through it yet. ChatGPT-login accounts
    are not expected to work.
  - The proxy is in the request path: if it dies, Codex fails until it restarts. The systemd/launchd units restart it
    within ~1 s; the Windows scheduled task doesn't.
- **dsh** gets the per-output rules only. It has no request-level hook, so dedup can't run there even when opted in.
  Use the proxy if you need it.
- **Pi, OpenCode and dsh** start a Python process per tool output (about 50–70 ms), and the call blocks the harness
  while it runs.
- **No Claude comprehension data** (see above).

## License

0BSD (see [LICENSE](LICENSE)). ctxprune 0.2.1 and earlier were Apache-2.0.

The optional line-scoring model weights for `balanced`/`aggressive` are not part of this package and are not 0BSD.
They are a fine-tune of mmBERT-small (MIT), trained on data that includes CC-BY-4.0 sets (SWE-rebench-openhands,
Nemotron-SWE). Their license and attributions are on the Hugging Face model card. See
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).

Changes from 0.2.1, including what was removed: [CHANGELOG.md](CHANGELOG.md).
