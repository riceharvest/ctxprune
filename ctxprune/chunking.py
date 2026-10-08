"""Split documents into chunks that fit the compressor's 512-token window.

Chunks break on line boundaries where possible; a single line longer than
the budget is split between atoms. Token counts use the target encoder's
tokenizer, so every training chunk fits one forward pass, matching how
llmlingua feeds the model (``max_seq_len = 512`` minus CLS/SEP).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable

from .atoms import atomize

DEFAULT_TOKENIZER = "jhu-clsp/mmBERT-small"


def load_counter(name: str = DEFAULT_TOKENIZER) -> Callable[[list[str]], list[int]]:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name)

    def count(texts: list[str]) -> list[int]:
        return [len(ids) for ids in tok(texts, add_special_tokens=False)["input_ids"]]

    return count


def _split_long_line(line: str, budget: int, count) -> list[str]:
    atoms = atomize(line)
    pieces = [a.ws + a.text for a in atoms]
    sizes = count(pieces)
    out, cur, cur_n = [], [], 0
    for p, n in zip(pieces, sizes):
        if cur and cur_n + n > budget:
            out.append("".join(cur))
            cur, cur_n = [], 0
        cur.append(p)
        cur_n += n
    if cur:
        out.append("".join(cur))
    return out


def chunk_text(text: str, count, max_tokens: int = 500, min_tokens: int = 48) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    sizes = count(lines)
    units: list[tuple[str, int]] = []
    for line, n in zip(lines, sizes):
        if n <= max_tokens:
            units.append((line, n))
        else:
            for piece in _split_long_line(line, max_tokens, count):
                units.append((piece, count([piece])[0]))
    chunks, cur, cur_n = [], [], 0
    for u, n in units:
        if cur and cur_n + n > max_tokens:
            chunks.append("".join(cur))
            cur, cur_n = [], 0
        cur.append(u)
        cur_n += n
    if cur:
        chunks.append("".join(cur))
    # Line-sum can undercount at joins; re-check and drop tiny tails.
    final = []
    for c, n in zip(chunks, count(chunks)):
        if n < min_tokens or not c.strip():
            continue
        if n > max_tokens:
            final.extend(p for p in _split_long_line(c, max_tokens, count) if p.strip())
        else:
            final.append(c)
    return final


def split_of(group: str, val_pct: int = 2, test_pct: int = 2) -> str:
    b = int(hashlib.sha1(group.encode()).hexdigest(), 16) % 100
    if b < test_pct:
        return "test"
    if b < test_pct + val_pct:
        return "val"
    return "train"


def chunk_doc(doc: dict, count, rng: random.Random, max_tokens: int = 500,
              max_chunks_per_doc: int = 2) -> list[dict]:
    chunks = chunk_text(doc["text"], count, max_tokens=max_tokens)
    if len(chunks) > max_chunks_per_doc:
        idx = sorted(rng.sample(range(len(chunks)), max_chunks_per_doc))
        chunks = [chunks[i] for i in idx]
    out = []
    for c in chunks:
        meta = {k: v for k, v in doc.items() if k != "text"}
        out.append({
            "id": f"{doc['source']}:{hashlib.sha1(c.encode()).hexdigest()[:16]}",
            "text": c,
            "split": split_of(doc["group"]),
            **meta,
        })
    return out
