# @ctxprune/core

Native TypeScript entry point for ctxprune. `compress()` and `decode()` have no runtime dependencies. ONNX inference is optional and loads `onnxruntime-node` only when `createOnnxLineScorer()` is called.

```ts
import { compress, decode, createOnnxLineScorer } from "@ctxprune/core";
const result = compress(toolOutput);
// result.text, result.rules; decode(result.text) reverses marker escaping.
```

The current implementation is a conservative subset, not a byte-compatible port of Python's complete `lossless.compress` pipeline. It implements marker escaping and JSON compaction; it does not implement grep/path/table/numbered grouping, command-aware detection, Python's token estimate, or tokenizer/window construction for ONNX. The ONNX adapter accepts already-tokenized `input_ids` and returns the class-1 probability for each position. Use the same model artifact and tokenizer as the Python runtime.

Parity against the complete Python determinism fixture suite and the `<5 ms p95` ONNX end-to-end gate are not claimed. This package establishes the native seam; the remaining parity work must port shared tokenization and all rules before it can replace the Python hook.
