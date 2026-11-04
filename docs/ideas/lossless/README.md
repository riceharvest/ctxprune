# Runnable gate for the lossless ideas

Every kept idea tagged **L** in [`../ideas.jsonl`](../ideas.jsonl) (162 of them) has a runnable test:

```bash
python docs/ideas/lossless/run.py            # all 162 cases, about 20 s, stdlib only
python docs/ideas/lossless/run.py 9 392 -v   # just these idea ids, with per-fixture detail
python docs/ideas/lossless/run.py --list     # id, kind, what each case claims
python -m pytest tests/test_ideas_lossless.py
```

Each idea in [`ideas.md`](../ideas.md) and [`shortlist.md`](../shortlist.md) now carries a `Run:` line with the command, the
case kind and what the test asserts (and what it does *not* preserve).

## What a pass means, and what it does not

A case is a small **reference prototype** of the idea (a transform, or for policies a deterministic simulation), built-in
fixtures that look like the tool output the idea names, and an oracle. A pass means: *this idea can be lossless on these
fixtures and on ~40 seeded mutations of each (42k mutated inputs in all)*. It does **not** mean:

- the idea saves what its finder claimed on real captures. That is still the idea's prose `Test:` line (real data);
- an LLM reads the transformed text as well as the original (#183 is the reader-parity study);
- the prototype is production code. It exists to be tested; the real implementation would be written against it.

Three kinds of case, strictest first (41 / 54 / 67 of the 162):

| kind | claim | oracle |
|---|---|---|
| `roundtrip` | `decode(encode(x)) == x` byte for byte | the decoder |
| `equivalent` | same information under a stated relation; `loses=` names exactly what is dropped (58 cases state one) | an independent function that parses both sides, not the encoder's own regex |
| `sim` | a policy or stateful behaviour (dedup ledgers, hook changes, PreToolUse rewrites, schedulers, caches) | assertions over a scripted session, including a *reader view* showing nothing needed is lost, the cases where it must not apply, and fail-open |

For transform cases the harness enforces, on every fixture and every mutation: encode is **total** (never raises);
**faithful** (decode or oracle holds); **never grows** the text; hits `min_gain` on fixtures that should shrink; leaves
**negatives** (look-alike or must-stay-verbatim input) byte-identical; and is **idempotent** (49 cases set
`idempotent=False` because an in-band escape makes re-encoding grow by design). `shrinks=None` fixtures are adversarial
inputs for in-band signalling (text that looks like the encoder's own output, a stray sigil): only totality and fidelity
apply, since a byte-exact encoding is injective and cannot shrink anything without growing something else.

Size is gated in characters so a run is deterministic and offline; tokens are reported next to it (o200k when `tiktoken` is
installed, otherwise a labelled word-piece proxy; `CTXPRUNE_IDEAS_TOKENIZER=proxy` forces it).

## Tests of the tests

A pass is only worth something if the gate can fail:

- `python docs/ideas/lossless/run.py --selftest` plants defects (a lossy transform claiming an inverse, one that grows, one that
  touches a negative, a non-idempotent one, one that raises, one that never saves, one that hangs, a raising `fixtures()`) and
  requires each to be rejected, and a sound transform to pass.
- `python docs/ideas/lossless/run.py --oracles` corrupts every transform's encoded output (replace or drop a character at 12
  spread positions, truncate) and requires the case's own decode or oracle to reject at least 75% of them (all of the
  truncations). All 95 transform cases do. A decoder may legitimately tolerate one redundant character, such as an optional `+`.
  Order-sensitivity probes (swap or duplicate a line) are informational; the misses are blank lines, defaults, YAML map keys, a decoder that ignores trailing duplicate data (#130), or
  orderings the case's `loses=` says are not preserved.
- Each case author also planted bugs in their own prototype and confirmed the case failed (hundreds in total, reported per
  module; those scratch scripts are not part of the repo).
- Every case has a per-case watchdog (30 s, `CTXPRUNE_IDEAS_CASE_TIMEOUT`) so a hanging prototype fails instead of hanging the run.

Known blind spots: the built-in mutations are line- and character-level, so escape paths for in-band markers (a leading `§`,
a legend header) are exercised only by each case's explicit adversarial fixtures; sims get no automatic fuzzing; and sims
that model Claude Code's behaviour (#530, #1322, hook payload shapes) rest on the author's model of it, not on the real tool.

## What building the gate turned up

Reported by the agents that wrote the cases, and encoded in each case's `claim`/`loses` and fixtures (every module was re-run
and passes; the findings were not independently re-derived). Several ideas are narrower than their finder wrote:

- **Not actually lossless as written:** #31 JSON minify (`json.loads`/`dumps` rewrites `1E5`, merges duplicate keys, fails on
  4300+ digit ints, so the test is lexeme-level); #392 git blame (the measured variant omitted time of day); #476 `sha7`
  shorthand; #988 and #1250 number and timestamp canonicalisation (value-equal only); #20, #516, #799 (order or timing lost);
  #522 (hoisting `time LEVEL msg` reorders keys); #9 (only OSC-8 link targets are kept).
- **Hidden traps the fuzz found:** CRLF is not a progress frame (#10); ANSI stripping can splice a truncated escape into a new
  one (#492); a table cell ending in `\` before `|` (#38); `>` inside an HTML attribute value (#323); a bare `+` at end of a
  new-file diff (#1079); in-band sigils need an escape path (#100, #102, #315, #476, #784, #1237, #1358 and others).
- **Characters are not tokens:** #37 saves 30% of characters but costs 7% more o200k tokens; #897 is lossless but tokens rise 8%
  while characters fall 27%; #11 and #13 save 18-19% of characters but about 3% of tokens; #1079's "39 vs 31 tokens" does not
  reproduce once the header is counted (95 vs 94 for 8 lines).
- **Policy ideas with a catch:** #67 a short saved-file id collides (4 of 1,000 outputs at 4 hex digits) and a relative path breaks
  after `cd`; #834's `rate >= 0.97` bail-out arm is unsound; #870 plain-substring matching of "verbatim" has 19 false positives
  in 20 prompts that should not trigger; #483 is lossless only if the host forwards `structuredContent`; #76's `CI=1` and
  `--no-audit` change behaviour, so only `NO_COLOR`, `GIT_PAGER` and `--no-fund` survive.
- **A real gap in today's hook (#71):** `hook.py`'s verbatim-command list misses `tail`, `awk`, `tac`, `FOO=1 cat`, `sudo cat`,
  `git -C … diff`, `git --no-pager diff` and `git log --patch`, so their output is compressed even though Edit needs it
  verbatim. Not changed here; the case documents it.
- Unverified offline (from memory, flagged in the cases): several documented env var names for #217, #610, #788 (for example
  `BROWSERSLIST_IGNORE_OLD_DATA`, `PRISMA_HIDE_UPDATE_MESSAGE`, `MAVEN_ARGS`), whether Claude Code honours `updatedInput` for
  Bash, and the real Grep/Glob `tool_response` shape (#959).

## Layout

| file | what |
|---|---|
| `harness.py` | `case()`, `Fixture`, rules, mutation fuzzer, watchdog, `selftest()`, `oracle_check()` |
| `run.py` | CLI: run, `--list`, `--coverage`, `--selftest`, `--oracles`, `--json`, `--sync`, `--module` |
| `cases_*.py` | the prototypes and cases, grouped by theme; `cases_00_examples.py` shows one of each kind |

`python docs/ideas/lossless/run.py --sync` copies every case's claim into the `check` fields of `ideas.jsonl` and rebuilds the
markdown (a pytest keeps the two in step). To add a case for a new `L` idea, write a `case(...)` call in a `cases_*.py`, run
`--coverage`, then `--sync`.
