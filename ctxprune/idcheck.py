"""Identifier-survival metric: did the IDs, numbers and paths make it through?

A protected atom survives only if it appears intact as an atom of the
compressed text, so "0.21" -> "0. 21" counts as lost: that is what the
downstream LLM actually sees.
"""

from __future__ import annotations

import json

from .atoms import atomize, is_protected


def protected_atoms(text: str) -> set[str]:
    return {a.text for a in atomize(text) if is_protected(a.text)}


def survival(original: str, compressed: str, count_tokens=None) -> dict:
    want = protected_atoms(original)
    have = {a.text for a in atomize(compressed)}
    lost = sorted(want - have)
    r = {
        "protected": len(want),
        "lost": len(lost),
        "lost_examples": lost[:10],
        "char_ratio": len(compressed) / max(1, len(original)),
    }
    if count_tokens is not None:
        r["llm_token_ratio"] = count_tokens(compressed) / max(1, count_tokens(original))
    try:
        json.loads(original)
    except (json.JSONDecodeError, ValueError):
        r["json"] = None
    else:
        try:
            json.loads(compressed)
            r["json"] = True
        except (json.JSONDecodeError, ValueError):
            r["json"] = False
    return r


def summarize(rows: list[dict]) -> dict:
    prot = sum(r["protected"] for r in rows)
    lost = sum(r["lost"] for r in rows)
    js = [r["json"] for r in rows if r.get("json") is not None]
    summary = {
        "n": len(rows),
        "protected": prot,
        "lost": lost,
        "lost_rate": lost / prot if prot else 0.0,
        "json_inputs": len(js),
        "json_still_valid": sum(js),
        "mean_char_ratio": sum(r["char_ratio"] for r in rows) / len(rows) if rows else 0.0,
    }
    if rows and "llm_token_ratio" in rows[0]:
        summary["mean_llm_token_ratio"] = sum(r["llm_token_ratio"] for r in rows) / len(rows)
    return summary
