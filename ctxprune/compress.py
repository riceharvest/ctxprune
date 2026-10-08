"""Inference: score atoms with the trained token classifier and keep the best.

    from ctxprune.compress import Compressor
    c = Compressor("path/or/hub-id")
    c.compress(text, rate=0.5)["text"]

`rate` is the fraction of tokens to keep (like LLMLingua-2's `rate`).
Identifiers are never split or re-spaced, because selection happens on whole
atoms and text is rebuilt from the source's own whitespace.
"""

from __future__ import annotations

import math

from .atoms import atomize, detok, is_protected
from .tokenlabels import atom_spans, char_to_atom, special_wrap, token_atom_sets, token_atoms

STRUCTURE = set('{}[]",:')


class Compressor:
    def __init__(self, model: str, device: str | None = None, max_len: int = 512, batch_size: int = 16,
                 backend: str = "torch", onnx_file: str = "onnx/model.onnx"):
        """backend="torch" (any device) or "onnx" (CPU, no torch needed; onnx_file may be
        "onnx/model_quantized.onnx" for the int8 build)."""
        from transformers import AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(model)
        self.backend = backend
        if backend == "onnx":
            import os

            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            path = os.path.join(model, onnx_file) if os.path.isdir(model) else hf_hub_download(model, onnx_file)
            self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            self.device = "cpu"
        else:
            import torch
            from transformers import AutoModelForTokenClassification

            self.torch = torch
            if device is None:
                device = "xpu" if hasattr(torch, "xpu") and torch.xpu.is_available() else (
                    "cuda" if torch.cuda.is_available() else "cpu")
            self.device = device
            self.model = AutoModelForTokenClassification.from_pretrained(model).to(device).eval()
        self.max_len = max_len
        self.batch_size = batch_size
        self.pre, self.post = special_wrap(self.tok)

    def atom_scores(self, text: str) -> tuple[list, list[float], list[int]]:
        """Keep-probability and token count for every atom of `text`."""
        atoms = atomize(text)
        full, spans = atom_spans(atoms)
        owner = char_to_atom(spans, len(full))
        enc = self.tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        tok_atom = token_atoms(offs, owner)

        # Windows of max_len - 2 tokens, cut where a new atom starts.
        win = self.max_len - len(self.pre) - len(self.post)
        windows, start = [], 0
        while start < len(ids):
            end = min(start + win, len(ids))
            if end < len(ids):
                cut = end
                while cut > start + 1 and tok_atom[cut] == tok_atom[cut - 1]:
                    cut -= 1
                end = cut if cut > start + 1 else end
            windows.append((start, end))
            start = end

        probs = [0.0] * len(ids)
        for b in range(0, len(windows), self.batch_size):
            batch = windows[b:b + self.batch_size]
            seqs = [self.pre + ids[s:e] + self.post for s, e in batch]
            width = max(map(len, seqs))
            pad = self.tok.pad_token_id
            ids_rows = [q + [pad] * (width - len(q)) for q in seqs]
            mask_rows = [[1] * len(q) + [0] * (width - len(q)) for q in seqs]
            p = self._keep_probs(ids_rows, mask_rows)
            for (s, e), row in zip(batch, p):
                k = len(self.pre)
                probs[s:e] = row[k:k + (e - s)]

        # Score: mean prob over every token touching the atom. Budget: each token is
        # charged once, to the first atom it touches.
        sums = [0.0] * len(atoms)
        hits = [0] * len(atoms)
        counts = [0] * len(atoms)
        for a, sets, p in zip(tok_atom, token_atom_sets(offs, owner), probs):
            if a >= 0:
                counts[a] += 1
            for b in sets:
                sums[b] += p
                hits[b] += 1
        scores = [sums[i] / hits[i] if hits[i] else 0.0 for i in range(len(atoms))]
        return atoms, scores, counts

    def _keep_probs(self, ids_rows: list[list[int]], mask_rows: list[list[int]]) -> list[list[float]]:
        if self.backend == "onnx":
            import numpy as np

            logits = self.session.run(["logits"], {"input_ids": np.array(ids_rows, dtype=np.int64),
                                                   "attention_mask": np.array(mask_rows, dtype=np.int64)})[0]
            z = logits - logits.max(-1, keepdims=True)
            e = np.exp(z)
            return (e[..., 1] / e.sum(-1)).tolist()
        t = self.torch
        input_ids = t.tensor(ids_rows, device=self.device)
        mask = t.tensor(mask_rows, device=self.device)
        with t.no_grad():
            logits = self.model(input_ids=input_ids, attention_mask=mask).logits.float()
        return logits.softmax(-1)[..., 1].cpu().tolist()

    def compress(self, text: str, rate: float | None = 0.5, threshold: float | None = None,
                 force_protected: bool = False, keep_structure: bool = False, unit="chars",
                 measure=None) -> dict:
        """Keep the highest-scoring atoms until `rate` of the text is kept.

        unit="chars" measures the budget in characters (one per whitespace gap), which
        tracks a downstream LLM's token count without depending on our tokenizer: the
        encoder splits digits one by one, so in "tokens" identifiers look expensive.
        unit may also be a callable mapping a list of strings to their costs.
        measure: optional callable str -> int (e.g. the target LLM's token count). The
        cut is then binary-searched so measure(output) <= rate * measure(text) exactly,
        which makes `rate` comparable across compressors with different tokenizers.
        """
        atoms, scores, tok_counts = self.atom_scores(text)
        if callable(unit):
            counts = list(unit([(" " if a.ws else "") + a.text for a in atoms]))
        elif unit == "chars":
            counts = [len(a.text) + (1 if a.ws else 0) for a in atoms]
        elif unit == "tokens":
            counts = tok_counts
        else:
            raise ValueError(f"unit must be 'chars' or 'tokens', not {unit!r}")
        forced = [
            bool(a.text) and ((force_protected and is_protected(a.text)) or (keep_structure and a.text in STRUCTURE))
            for a in atoms
        ]
        if threshold is not None:
            keep = [f or s >= threshold for f, s in zip(forced, scores)]
        elif measure is not None and rate is not None:
            order = [i for i in sorted(range(len(atoms)), key=lambda i: -scores[i])
                     if atoms[i].text and not forced[i]]
            target = rate * measure(text)

            def with_top(k: int) -> list[bool]:
                kk = list(forced)
                for i in order[:k]:
                    kk[i] = True
                return kk

            lo, hi = 0, len(order)  # largest k whose output fits the target
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if measure(detok(atoms, with_top(mid))) <= target:
                    lo = mid
                else:
                    hi = mid - 1
            keep = with_top(lo)
        else:
            total = sum(counts)
            budget = math.ceil((rate if rate is not None else 1.0) * total)
            keep = list(forced)
            used = sum(c for c, f in zip(counts, forced) if f)
            for i in sorted(range(len(atoms)), key=lambda i: -scores[i]):
                if used >= budget:
                    break
                if not keep[i] and atoms[i].text:
                    keep[i] = True
                    used += counts[i]
        out = detok(atoms, keep)
        return {"text": out, "kept": sum(c for c, k in zip(counts, keep) if k), "total": sum(counts)}
