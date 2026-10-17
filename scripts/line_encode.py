"""Token/line encoding of (intent, tool-output lines) for the v2 line model. Pure Python (tokenizer only): the
shipping ONNX runtime (export_v2_onnx.windows, v2_server --onnx) must not need torch. train_lines.py re-exports it."""

from __future__ import annotations


def encode(tok, query, lines: list[str], max_len: int, labels=None, start: int = 0, line_cap: int = 0,
           intent_cap: int = 0, intent2=None):
    """Encode query + lines[start:] up to max_len. Returns ids, token->line index (-1 for query/special), end line.
    line_cap > 0 (speed step): the model sees at most the first line_cap tokens of each line (line_cap - 1 head tokens
    + the line's last token, which carries the newline, so capped lines stay separated). The line itself is still kept
    or dropped whole; its score is the mean over the tokens it got. line_cap = 0: unchanged.
    intent_cap > 0 (speed2): the query is the LAST intent_cap tokens of query[-1200:] (the end holds the tool call and
    the latest reasoning), so a long intent can't eat a 512 window (it is repeated in every window). intent_cap = 0:
    unchanged (first 200 tokens of query[-1200:])."""
    if not query:
        q = []
    elif isinstance(query, tuple):   # E4 intent2: (task, last text, call) with per-part token budgets H, T, C
        H, T, C = intent2 or (128, 192, 64)
        task, last, call = query
        q = tok(task, add_special_tokens=False)["input_ids"][:H]
        if last and T:
            q += tok("\n" + last, add_special_tokens=False)["input_ids"][-T:]
        if call and C:
            q += tok("\n" + call, add_special_tokens=False)["input_ids"][:C]
    elif intent_cap > 0:
        q = tok(query[-1200:], add_special_tokens=False)["input_ids"][-intent_cap:]
    else:
        q = tok(query[-1200:], add_special_tokens=False)["input_ids"][:200]
    ids = [tok.cls_token_id] + q + [tok.sep_token_id]
    line_of = [-1] * len(ids)
    i = start
    while i < len(lines):
        t = tok(lines[i] + "\n", add_special_tokens=False)["input_ids"] or [tok.unk_token_id]
        if line_cap and len(t) > line_cap:
            t = t[: line_cap - 1] + t[-1:] if line_cap > 1 else t[-1:]
        if len(ids) + len(t) + 1 > max_len:
            if i == start:  # a single giant line: truncate it
                t = t[: max_len - len(ids) - 1]
            else:
                break
        ids += t
        line_of += [i] * len(t)
        i += 1
        if len(ids) >= max_len - 1:
            break
    ids.append(tok.sep_token_id)
    line_of.append(-1)
    tl = None
    if labels is not None:
        tl = [-100 if li < 0 else labels[li] for li in line_of]
    return ids, line_of, i, tl
