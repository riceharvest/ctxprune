# ctxprune 0.4.1 — release notes

Draft for the GitHub release body (publish step 8 uses the CHANGELOG entry; this is the short form, in the
0.3.0 release-notes style). Hotfix: no compression change, no new benchmark numbers.

Sources for every number quoted below (lab-internal, not part of the release body):
- probe identity vs 0.3.2: t_57f9a540, ~/smallest-ai-lab/results/ctxprune_041/probe_final/result.md
- determinism digest / 103 outputs / 0 failures: gates t_24c7f837, ~/smallest-ai-lab/results/ctxprune_041/gates/result.md
- held-out LongBench +2.36 [−0.70, +5.51], n=270 held-out: ~/ctxprune-v2/research/v04_code/lab/heldout/result.md,
  review t_c57ef150 (WEAKER; 'level with' wording), review at research/v04_code/lab/heldout/gate/review.md
- plugin smoke bytes identical: t_b159e4f2 (note in release/claude-code-plugin-0.3.0/README.md)

---

ctxprune 0.4.1 is a patch release: **one-line agent install**. No change to compression, the 0.2 / 0.3 API or the
lossless output.

```sh
uvx ctxprune wrap claude        # or: opencode | hermes | pi | codex | --all
```

One command: it installs ctxprune for good (`uv tool install`, or pipx), adds the lossless `safe` hook to that agent,
and prints the diff of the agent's config. Running it again changes nothing. `ctxprune unwrap claude` removes it.
Nothing it installs drops content. `wrap` is the uvx-safe form of `install`: it bootstraps the permanent install
first, so the interpreter path written into your agent config survives the temporary uvx environment.

Also in this patch:

- **Claude Code plugin 0.4.1** (marketplace `riceharvest/ctxprune`): the uvx fallback pin is widened to
  `ctxprune>=0.3,<0.5`, so plugin users without a local install run the 0.4 lossless hook. The hook's output bytes
  are unchanged (identical to 0.3.2 on the smoke payloads).
- **Pi, OpenCode and Hermes** per-output scripts now hold changed first-render pins in their context store, the same
  residual safety rule the Codex proxy already had: a pin that changed a result's bytes is never evicted, so a
  resumed conversation's history keeps its bytes. No output change.

### Compatibility

- Re-checked against a pristine 0.3.2 install with the release probes (`probe_api.py`, `probe_cli.sh`): the
  0.2 / 0.3 API, the CLI (file, pipe, dash, bare stdin), `--stats` and `serve` `POST /compress` are byte-identical;
  only the version string differs. `install` / `uninstall --scope project|local` against a real 0.2.1 install pass
  every probe check.
- The lossless determinism digest is unchanged from 0.3.x / 0.4.0 (release gate, 103 outputs, 0 failures).
- The one deliberate 0.4 behaviour change still applies: `ctxprune mcp` `read_pruned` defaults to `protect="auto"`
  (it was `true` in 0.3.2). Pass `protect=true` for the old behaviour.

### Compression claims (unchanged from 0.4.0)

0.4.1 adds no new measurement. The 0.4 query mode is **level with LLMLingua-2-large on held-out LongBench — it does
not beat it** (held-out gate, n=270, +2.36 [−0.70, +5.51] 9-task macro; review-allowed wording). The limits, the
dev-set losses (`triviaqa`, `lcc`, the `passage_retrieval_en` shortcut) and the 21-item source-document overlap
disclosure are in
[BENCHMARKS §9](https://github.com/riceharvest/ctxprune/blob/main/docs/BENCHMARKS.md#9-longbench-rematch-against-llmlingua-2).

### Upgrading

- `uv tool upgrade ctxprune` (or `pipx upgrade ctxprune` / `pip install -U ctxprune`). Existing hooks and plugins
  keep working; `ctxprune doctor` checks them.
- Plugin users: `/plugin marketplace update ctxprune` picks up plugin 0.4.1.
- Python API, LangChain / LlamaIndex, CLI and `serve` users: nothing to do.

License: 0BSD. The ctxprune-small model stays Apache-2.0. Full list of changes:
[CHANGELOG.md](https://github.com/riceharvest/ctxprune/blob/main/CHANGELOG.md).
