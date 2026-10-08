# ctxprune

Context compression for AI agents. A small token classifier deletes the least useful words from
tool outputs, code, documents and chat before they reach an LLM. It is a successor to Microsoft's
LLMLingua-2, built for agent text, and it never splits or re-spaces identifiers.

Model: [darioooooo0o/ctxprune-small](https://huggingface.co/darioooooo0o/ctxprune-small) (Apache-2.0).
Results and limitations are on the model card.

```bash
pip install "ctxprune[onnx] @ git+https://github.com/riceharvest/ctxprune"
```

```python
from ctxprune import Compressor

c = Compressor("darioooooo0o/ctxprune-small", backend="onnx")
c.compress(tool_output, rate=0.5)["text"]                         # keep ~50%
c.compress(tool_output, rate=0.33, force_protected=True)["text"]  # never drop IDs/numbers/paths
```

Works inside LLMLingua too, once its tokenizer-detection fix is merged
(`upstream/llmlingua-subword-style.patch`).

---

## Reproducing the model

Why: LLMLingua-2 (Microsoft, Mar 2024) was trained only on meeting transcripts
under CC BY-NC-SA labels. On agent tool outputs it loses or mangles about half
of all identifiers (UUIDs, IPs, paths, versions) and breaks JSON.

## Pipeline

| step | script | where | notes |
|---|---|---|---|
| corpus | `scripts/build_corpus.py` | CPU, ~2 min | ranged parquet reads from 8 permissive sources; split per repo/server/site |
| labels | `scripts/label.py` | teacher LLM (`scripts/teacher_up.sh`) | deletion-only compression, LCS-aligned to atoms; cached |
| train | `scripts/train.py` | any torch device (we used an Intel Arc Pro B70) or CPU | token classifier, label 1 = keep (LLMLingua-2 convention) |
| quick eval | `scripts/eval_quick.py` | CPU | identifiers lost, JSON validity, char ratio vs LLMLingua-2 |
| QA eval | `scripts/eval_qa.py questions/answer` | teacher LLM | can a reader still answer from the compressed text |

`scripts/run_v1.sh` runs corpus and labels; `scripts/after_label.sh` runs everything after them.

## Data sources (all permissive)

SWE-smith trajectories (MIT), SWE-rebench OpenHands trajectories (CC BY 4.0),
Toucan-1.5M MCP tool calls (Apache-2.0), github-code-clean filtered to
MIT/Apache/BSD/ISC files, FineWeb-Edu and FineWeb-2 (ODC-BY), oasst2
(Apache-2.0). Every chunk records its dataset, license and source reference.
Teacher: Qwen3.8-27B GPTQ (Apache-2.0 outputs).

## Training more after an eval

Nothing has to be redone:

1. **More data, same mix.** Rerun `build_corpus.py` into the same directory
   with a larger `--total`. Sampling is deterministic, and dataset revisions
   are pinned in `chunks/revisions.json`, so every source file becomes a
   superset of the old one. To weight a domain up, add `--mix toucan_mcp=0.3`.
2. **Label only the new chunks.** Rerun `label.py`. The teacher cache is keyed
   by (prompt version, model, text), so old chunks come back from disk.
3. **Continue training** from the previous model:
   `train.py --data <dir> --init-from runs/v1-mmbert-small/final --out runs/v2-...`
   (or `--resume` to continue an interrupted run from its checkpoint).
   `--data` accepts several directories, to mix label sets.
4. **Re-run evals.** Question and answer caches mean only new contexts cost LLM calls.

Changing the teacher prompt bumps `PROMPT_VERSION` in `ctxprune/teacher.py`, which
invalidates the cache on purpose.

## Tests

`uv run pytest`: atom round-trips, identifier atoms, CJK handling, LCS
alignment, chunk budgets, split stability and cache resume.
