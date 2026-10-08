---
license: apache-2.0
language:
- multilingual
- en
- de
- fr
- es
- pt
- it
- nl
- pl
- ru
- zh
- ja
library_name: transformers
pipeline_tag: token-classification
base_model: jhu-clsp/mmBERT-small
tags:
- prompt-compression
- context-compression
- llmlingua
- agents
- tool-use
- modernbert
- mmbert
- onnx
datasets:
- SWE-bench/SWE-smith-trajectories
- nebius/SWE-rebench-openhands-trajectories
- Agent-Ark/Toucan-1.5M
- codeparrot/github-code-clean
- HuggingFaceFW/fineweb-edu
- HuggingFaceFW/fineweb-2
- OpenAssistant/oasst2
---

# {{NAME}}: context compression for AI agents

A 140M-parameter token classifier that shortens text before it reaches an LLM by **deleting** the
words that matter least. It is a successor to Microsoft's
[LLMLingua-2](https://huggingface.co/microsoft/llmlingua-2-xlm-roberta-large-meetingbank), trained for
what agents actually read: tool outputs (JSON, logs, stack traces, diffs, directory listings), code,
documents and chat, in many languages.

- **Keeps identifiers intact.** Text is rebuilt from whole source spans, so IDs, paths, URLs, IPs and
  versions are never split or re-spaced (LLMLingua-2 turns `ord_8f3a2c91` into `ord _ 8f3a2c91` and
  `0.21` into `0. 21`). Optional `force_protected=True` guarantees no identifier is dropped.
- **Better answers from compressed text.** At the same share of LLM tokens kept, QA accuracy from the
  compressed text is {{QA_GAIN_50}} higher than with LLMLingua-2-large at 50% kept, and {{QA_GAIN_33}} at 33%.
  The gain comes from agent tool outputs, agent text and code; on multilingual web prose
  LLMLingua-2-large is still better (see per-source table).
- **Small and fast.** ~{{MS_FP32}} ms per 500-token chunk on CPU (ONNX fp32), ~{{MS_INT8}} ms int8,
  vs ~600 ms for LLMLingua-2-large. ONNX files included; no PyTorch needed.
- **Apache-2.0**, trained only on permissively licensed data (LLMLingua-2's training labels are CC BY-NC-SA).

## Usage

```bash
pip install "ctxprune[onnx] @ git+https://github.com/riceharvest/ctxprune"   # CPU, no torch
pip install "ctxprune[torch] @ git+https://github.com/riceharvest/ctxprune"  # PyTorch (GPU/CPU)
```

```python
from ctxprune import Compressor

c = Compressor("{{REPO}}", backend="onnx")    # or backend="torch"
out = c.compress(tool_output, rate=0.5)       # keep ~50% of the text
print(out["text"])

c.compress(tool_output, rate=0.33, force_protected=True)  # never drop IDs, numbers, paths
```

**With LLMLingua** (drop-in, needs the tokenizer-detection fix proposed upstream in {{PR_LINK}}):

```python
from llmlingua import PromptCompressor
pc = PromptCompressor(model_name="{{REPO}}", use_llmlingua2=True)
pc.compress_prompt(text, rate=0.5, force_tokens=["\n", "?"])
```

**Raw transformers / ONNX:** label 1 = keep (same convention as LLMLingua-2). Score each token with
`softmax(logits)[..., 1]`, average per word, and keep the highest-scoring words. `onnx/model.onnx` is
exact fp32. `onnx/model_quantized.onnx` is int8 (143 MB): its keep decisions match fp32 on ~96% of tokens.

## Results

Held-out evaluation: 378 questions over 196 chunks from repos, MCP servers and sites never seen in
training. Each method compresses the chunk to the same share of the reader's tokens; the reader then
answers from the compressed text and is scored by exact match on the gold span.

**QA exact match, independent reader (DeepSeek V4 Flash):**

{{QA_TABLE_DEEPSEEK}}

**Same, with Qwen3.8-27B as reader** (also the labeling teacher, so this may flatter our model):

{{QA_TABLE_QWEN}}

**Identifiers lost** (IDs, numbers, paths, versions that no longer appear intact), at 50%:

{{ID_TABLE}}

Reasoning on vs off for the reader made no difference on a 40-question check, so readers ran without it.

## Training

- **Data**: {{N_CHUNKS}} chunks (≤500 tokens) from 7 permissive sources: coding-agent tool outputs
  (SWE-smith, MIT; SWE-rebench OpenHands, CC BY 4.0), MCP tool outputs (Toucan-1.5M, Apache-2.0), source
  code (github-code-clean, MIT/Apache/BSD/ISC files only), English and multilingual web text
  (FineWeb-Edu and FineWeb-2, ODC-BY), assistant chat (oasst2, Apache-2.0). Train/val/test are split
  per repository, MCP server, website or conversation.
- **Labels**: Qwen3.8-27B (Apache-2.0) compresses each chunk by deletion only. Its output is aligned to
  the source with an LCS over atoms. Two prompts are used: a conservative one, and an aggressive one with
  an explicit word budget. Training on both gives graded targets: words kept even under the aggressive
  prompt score highest. This teaches ranking inside the large "keep" set, which is what a 33–50% rate needs.
- **Model**: mmBERT-small (MIT) with a 2-class token head, 5 epochs, best epoch by ROC-AUC against
  teacher labels. Training takes {{TRAIN_MIN}} minutes on one Intel Arc Pro B70.
- **What mattered** (same eval, DeepSeek reader, 50% / 33% kept): conservative labels only, 65.6 / 45.8;
  aggressive labels only, 65.6 / 50.8; both together (graded), 68.8 / 53.4. Doubling the labels from ~1k
  to ~2k added 4 points; mmBERT-base instead of small added ~1 point at 2.3x the latency.

Code, data pipeline and evals: {{CODE_LINK}}.

## Limitations

- **Multilingual web prose** is the weakest domain: LLMLingua-2-large answers more questions there
  (small sample: 18 questions). The teacher often rewrote non-English text instead of deleting from it,
  so fewer of those labels survived filtering.
- `force_protected=True` guarantees no identifier is dropped, but at 50% it spends budget on IDs the
  question did not need and scores a few points lower overall. Use it when exact IDs matter more than
  everything else (e.g. you will act on them).
- Compression always loses information. At 50% kept, QA accuracy drops from ~92% (uncompressed) to
  {{OURS_50}}. Use it where the token savings are worth that.
- Labels come from one teacher LLM, and the eval questions were written by that same model.
- Rates are measured in the target LLM's tokens in our evals. With the default character budget,
  CJK text compresses slightly less than the requested rate.

## Attribution

Training data includes content under CC BY 4.0 (nebius/SWE-rebench-openhands-trajectories) and
ODC-BY (FineWeb-Edu, FineWeb-2). The deletion-only teacher prompt adapts LLMLingua-2's
(MIT, Microsoft). Base model: jhu-clsp/mmBERT-small (MIT).
