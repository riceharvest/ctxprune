# Runnable gate for the lossless and shortlisted-rule ideas

347 ideas in [`../ideas.jsonl`](../ideas.jsonl) have a runnable test: all 222 kept lossless (**L**) ideas and the 125
shortlisted rule (**R**) ideas that the finders rated medium or large.

```bash
python docs/ideas/gate/run.py            # all 347 cases, about 50 s, stdlib only
python docs/ideas/gate/run.py 9 392 -v   # just these idea ids, with per-fixture detail
python docs/ideas/gate/run.py --list     # id, kind, what each case claims
python -m pytest tests/test_ideas_gate.py
```

Each gated idea in [`ideas.md`](../ideas.md) and [`shortlist.md`](../shortlist.md) has a `Run:` line with the command, the case
kind, what the test asserts, and what it does *not* preserve.

## What a pass means, and what it does not

A case is a small **reference prototype** of the idea (a transform, or for policies a deterministic simulation), built-in
fixtures that look like the tool output the idea names, and an oracle. A pass means: *this idea keeps what it promises on these
fixtures and on ~40 seeded mutations of each (2,111 fixtures and 97,660 mutated inputs in all)*. It does **not** mean:

- the idea saves what its finder claimed on real captures. That is still the idea's prose `Test:` line (real data). Where the
  cases reproduced a claimed ratio they say so, and many did not (see below);
- an LLM reads the transformed text as well as the original (#183 is the reader-parity study);
- the prototype is production code. It exists to be tested; the real implementation would be written against it.

Four kinds of case, strictest first (84 / 72 / 106 / 85 of the 347):

| kind | claim | oracle |
|---|---|---|
| `roundtrip` | `decode(encode(x)) == x` byte for byte | the decoder |
| `equivalent` | same information under a stated relation; `loses=` names exactly what is dropped | an independent function that parses both sides, not the encoder's own regex |
| `rule` | information is dropped on purpose; `loses=` says what | `probes(text)`: named answers to the questions an agent must still be able to answer (which test failed, file:line of every error, the latest version). The answers must be identical on the original and the encoding, on every fixture and mutation |
| `sim` | a policy or stateful behaviour (dedup ledgers, hook changes, PreToolUse rewrites, MCP wrappers, schedulers, caches) | assertions over a scripted session: the saving as a number, a *reader view* showing nothing needed is lost, the cases where it must not apply, and fail-open |

For transform cases (roundtrip, equivalent, rule) the harness enforces, on every fixture and every mutation: encode is **total**
(never raises); **faithful** (decode, oracle or probes hold); **never grows** the text; hits `min_gain` on fixtures that should
shrink; leaves **negatives** (look-alike or must-stay-verbatim input) byte-identical; and is **idempotent** (92 cases set
`idempotent=False` because an in-band escape makes re-encoding grow by design). `shrinks=None` fixtures are adversarial inputs
for in-band signalling (text that looks like the encoder's own output, a stray sigil): only totality and fidelity apply, since a
byte-exact encoding is injective and cannot shrink anything without growing something else.

Size is gated in characters so a run is deterministic and offline; tokens are reported next to it (o200k when `tiktoken` is
installed, otherwise a labelled word-piece proxy; `CTXPRUNE_IDEAS_TOKENIZER=proxy` forces it). Assertions about the *magnitude*
of a token saving sit behind `harness.exact_tokens()`, so all 347 cases pass in both modes.

## Tests of the tests

A pass is only worth something if the gate can fail:

- `python docs/ideas/gate/run.py --selftest` plants defects (a lossy transform claiming an inverse, one that grows, one that
  touches a negative, a non-idempotent one, one that raises, one that never saves, one that hangs, a raising `fixtures()`, a rule
  that drops the answer-bearing line, probes that answer nothing, a rule that is just head truncation, probes that are not a dict)
  and requires each to be rejected, and sound transforms to pass.
- `python docs/ideas/gate/run.py --oracles` checks that the oracle is not vacuous.
  - **roundtrip / equivalent:** corrupt every encoded output (replace or drop a character at 12 spread positions, truncate) and
    require the case's own decode or oracle to reject at least 75% of them, and all truncations. All 156 such cases do. A decoder
    may legitimately tolerate one redundant character, such as an optional `+`. Order-sensitivity probes (swap or duplicate a line)
    are informational: the misses are blank lines, defaults, YAML map keys, or orderings the case's `loses=` says are not preserved.
  - **rule:** the probes must notice dropping everything on every shrinking fixture, and must tell the rule apart from naive head
    truncation and from tail truncation cut to the rule's own output size, on at least one fixture each. A rule that only matches
    plain truncation has not shown it keeps anything a dumb cut would not. All 106 rule cases do.
- Each case author also planted bugs in their own prototype and confirmed the case failed (several hundred in total, reported per
  module; those scratch scripts are not part of the repo). Several first-round survivors exposed weak fixtures or probes that reused
  the encoder's own helper; those were fixed.
- Every case has a watchdog (30 s, `CTXPRUNE_IDEAS_CASE_TIMEOUT`), so a hanging prototype fails instead of hanging the run.
- The suite was run repeatedly, concurrently and under different `PYTHONHASHSEED` values without a failure. An early flake in #76
  (pipeline stages interleaving partial writes to one log) is fixed.

Known blind spots: the built-in mutations are line- and character-level, so escape paths for in-band markers (a leading `§`,
a legend header) are exercised only by each case's explicit adversarial fixtures; sims get no automatic fuzzing; several encoders
verify their own output against their decoder before emitting it, so for those the roundtrip check is largely guaranteed by
construction and the real signals are the gain on realistic fixtures, the negatives and the oracle check; a few probes share a
spec constant with their encoder (a wrong constant would not be caught); and sims that model Claude Code's behaviour (#530,
#1322, hook payload shapes) rest on the author's model of it, not on the real tool. Fixtures are a mix: real captures where the tool was
installed in the build container (git, gcc, clang, python, pytest, strace, maven, gradle, cargo, go, jstack, lsof, and others),
and reconstructions from memory elsewhere (most CI, cloud, log-pack and ML cases, and API payloads), marked above each case.

## What building the gate turned up

Reported by the agents that wrote the cases, and encoded in each case's `claim`/`loses`, fixtures and probes (every module was
re-run and passes; the findings were not independently re-derived, except #71 below). Many ideas are narrower or weaker than their
finder wrote.

**Lossless ideas that were not lossless as written:** #31 JSON minify (`json.loads`/`dumps` rewrites `1E5`, merges duplicate
keys, fails on 4300+ digit ints, so the test is lexeme-level); #392 git blame (the measured variant omitted time of day); #476
`sha7` shorthand; #988 and #1250 number and timestamp canonicalisation (value-equal only); #20, #516, #799 (order or timing lost);
#522 (hoisting `time LEVEL msg` reorders keys); #9 (only OSC-8 link targets are kept).

**Traps the fuzz found:** CRLF is not a progress frame (#10); ANSI stripping can splice a truncated escape into a new one
(#492); a table cell ending in `\` before `|` (#38); `>` inside an HTML attribute value (#323); a bare `+` at the end of a
new-file diff (#1079); in-band sigils need an escape path (#100, #102, #315, #476, #784, #1237, #1358 and others); regex
extraction misreading truncated pages (#122, #1067, #1168).

**Characters are not tokens:** #37 saves 30% of characters but costs 7% more o200k tokens; #897 is lossless but tokens rise 8%
while characters fall 27%; #11 and #13 save 18-19% of characters but about 3% of tokens; #1079's "39 vs 31 tokens" does not
reproduce once the header is counted. In the other direction, #1404 saves 27% of characters but 10% of tokens, #1412 21% / 9%,
and #1439 2-7% of characters but 17% of tokens.

**Claimed savings that did not reproduce** (fixture figures are reconstructions of the cited tool output, so read them as
"smaller than claimed", not as exact): #310 first-paragraph-only keeps 52% of a real 14k-token `gcloud ... --help`, not 47%, and
drops `must be one of` lists; #1155 Spring Boot padding and PID save about 13-21%, where the finder cited 1,709 tokens down to 839; #801 hit envelopes save
55%, where the finder cited 677 down to 74 on tiny `_source` objects; #1154 pbxproj aliasing saves 20% of characters; #604 `--traceResolution` folding saves 9% (22% with `--listFiles`) because
most blocks are unresolved ambient modules; #223 gradle trees 14% on a real Java project; #224 the Gradle 8.14 footer is one
constant line, so 0%; #701 ninja 28%, Bazel 21%; #760's roughly 10x needs 120-character chunks (about -82% tokens at best, -68% at 700 characters); #507 depends on how many
tasks repeat the rows; #1327, #809, #885 and #425 reach less than their cited ratios.

**Rules that are unsafe as written:** #703 "drop flag-3 regions" corrupts `fprintf(stderr, ...)` because gcc marks macro text
from system headers inside the user's own file; #498's "first three of the rest" ranks a real cross-file break 17th of 17; #209
pure BM25 loses the failing-test lines and the last line; #120 a plain key blocklist loses jest's suite-level `message`; #980
folding generated chains loses which pod IP backs each service; #449 rejects an edit when a line is inserted above it where Edit
would succeed; #384 a document-frequency rule strips a shared opening code fence but not the closing one; #478 digit-blurring
merges legs that fail with different assertion values; #288 stubbing old Write inputs saves 41% of request tokens but costs about
59% more per turn once prompt caching is counted.

**Policy ideas with a catch:** #67 a short saved-file id collides (4 of 1,000 outputs at 4 hex digits) and a relative path breaks
after `cd`; #834's `rate >= 0.97` bail-out arm is unsound; #870 plain-substring matching of "verbatim" has 19 false positives
in 20 prompts that should not trigger; #483 is lossless only if the host forwards `structuredContent`; #76's `CI=1` and
`--no-audit` change behaviour, so only `NO_COLOR`, `GIT_PAGER` and `--no-fund` survive; #304 adds about 2,000 tokens per turn for
a 48-tool server; #684's sim gives the mcp2cli bridge a 16% billed saving for a browser-like server where the cited measurement saw about none; #579
`BASH_ENV` is ignored when stdin is a socket.

**A real gap in today's hook (#71):** `hook.py`'s verbatim-command list misses `tail`, `awk`, `tac`, `FOO=1 cat`, `sudo cat`,
`git -C ... diff`, `git --no-pager diff` and `git log --patch`, so their output is compressed even though Edit needs it verbatim
(checked against the real regex). Not changed here; the case documents it.

**Unverified offline** (from memory, flagged in the cases): several env var names for #217, #610, #788 (for example
`BROWSERSLIST_IGNORE_OLD_DATA`, `PRISMA_HIDE_UPDATE_MESSAGE`, `MAVEN_ARGS`), whether Claude Code honours `updatedInput` for Bash,
whether Aider, Cline, Roo or Cursor honour `$SHELL` (#946), the claim that tool_use inputs are not signature-checked (#288), and
the real Grep/Glob `tool_response` shape (#959).

## The 60 ideas added with this gate (#1404-#1463)

Written together with their cases, in six themes (package managers and language tools, cloud and IaC, VCS and CI, data and text
formats, protocol envelopes, system tools and terminals). All are lossless (42 roundtrip and 18 equivalent cases), tagged `L`, round 15, deduplicated against all 1,403 existing ideas by a TF-IDF screen plus a manual read of every title. 44 are
rated small, 13 tiny and 3 medium (#1410 Maven transfers, #1424 GitHub REST objects, #1429 git status short form): most real
tool output is either already compact or dominated by content that cannot be dropped, and candidates that measured under ~15%
(go.sum pair folding, YAML quote removal, `env` prefix grouping, `/proc/net/tcp` hex decode, padded-CSV comma trimming) were
dropped rather than listed.

## Layout

| file | what |
|---|---|
| `harness.py` | `case()`, `Fixture`, rules, mutation fuzzer, watchdog, `selftest()`, `oracle_check()`, `exact_tokens()` |
| `run.py` | CLI: run, `--list`, `--coverage`, `--selftest`, `--oracles`, `--json`, `--sync`, `--module` |
| `cases_*.py` | the prototypes and cases, grouped by theme; `cases_00_examples.py` shows one of each kind. The `cases_rules_*` files hold the rule ideas; `cases_new_*` the 60 new ideas. Several are large (up to ~0.5 MB) because they inline real tool captures |

`python docs/ideas/gate/run.py --sync` copies every case's claim into the `check` fields of `ideas.jsonl` and rebuilds the
markdown (a pytest keeps the two in step). To add a case for a new idea, write a `case(...)` call in a `cases_*.py`, run
`--coverage`, then `--sync`. The gated set is every kept `L` idea plus the shortlisted `R` ideas; other ideas may have a case too.
