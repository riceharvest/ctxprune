# Support any LLMLingua-2-style token classifier (detect subword style from the tokenizer)

`is_begin_of_new_word` and `get_pure_token` pick their word-merging rule by matching substrings of
`model_name` (`bert-base-multilingual-cased`, `tinybert`, `mobilebert`, `xlm-roberta-large`, `slingua`).
Any other checkpoint raises `NotImplementedError` (see #168, #221), even when it follows the
LLMLingua-2 format exactly (2-label token classifier, label 1 = keep).

This PR detects the subword style once from the tokenizer (`##` WordPiece, `▁` SentencePiece, `Ġ`
byte-level BPE) and uses it whenever no model-name rule matches.

- **Backward compatible:** the existing name-based branches run first and are unchanged. Outputs of
  `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank` are byte-identical before and after
  (checked on 15 mixed prompts).
- For SentencePiece and byte-level tokenizers, a new word starts only where the tokenizer saw
  whitespace. Identifiers such as `ord_8f3a2c91` or `10.0.4.17:5432` therefore stay one word instead of
  becoming `ord _ 8f3a2c91`.
- The two `lstrip("▁")` blocks gated on `xlm-roberta-large` now also apply to detected SentencePiece
  and byte-level styles.

Example of a new checkpoint that works with this change: `darioooooo0o/ctxprune-small` (mmBERT-small,
Apache-2.0, trained for agent tool outputs).

```python
from llmlingua import PromptCompressor
pc = PromptCompressor("darioooooo0o/ctxprune-small", use_llmlingua2=True)
pc.compress_prompt(text, rate=0.5, force_tokens=["\n", "?"])
```
