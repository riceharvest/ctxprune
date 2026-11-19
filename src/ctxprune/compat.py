"""Zero-dependency drop-in compatibility shim for ``llmlingua.PromptCompressor`` (L1.5).

Enables 1-line migration::

    from ctxprune.compat import PromptCompressor   # replaces: from llmlingua import PromptCompressor
    pc = PromptCompressor(use_llmlingua2=True)
    res = pc.compress_prompt(context, rate=0.5, question="...", force_tokens=["\\n", "."])
"""

from __future__ import annotations

import os
from typing import Iterable

from .atoms import atomize, est_atom_tokens
from .compress import DEFAULT_MODEL, INT8, Compressor

_LL2_HF_ALIASES = frozenset({
    "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
    "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
})


def _resolve_model(model_name: str | None) -> str:
    if not model_name or model_name in _LL2_HF_ALIASES:
        return DEFAULT_MODEL
    if model_name.startswith("microsoft/llmlingua") and not os.path.exists(model_name):
        return DEFAULT_MODEL
    return model_name


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return max(1, int(round(sum(est_atom_tokens(a.text, a.ws) for a in atomize(text))))) if text else 0


class PromptCompressor:
    """Drop-in API shim matching ``llmlingua.PromptCompressor`` signatures and return payload."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device_map: str | None = None,
        model_config: dict | None = None,
        open_api_config: dict | None = None,
        use_llmlingua2: bool = True,
        **kwargs,
    ) -> None:
        resolved = _resolve_model(model_name)
        backend = "auto"
        device = None
        if device_map == "cpu":
            backend = "onnx"
        elif device_map in ("cuda", "xpu") or (isinstance(device_map, str) and device_map.startswith(("cuda:", "xpu:"))):
            backend = "torch"
            device = device_map
        onnx_file = kwargs.pop("onnx_file", INT8)
        threads = kwargs.pop("threads", None)
        self.compressor = Compressor(
            model=resolved,
            device=device,
            backend=backend,
            onnx_file=onnx_file,
            threads=threads,
            engine="v04",
        )

    def compress_prompt(
        self,
        context: str | Iterable[str],
        instruction: str = "",
        question: str = "",
        rate: float = 0.5,
        target_token: int = -1,
        force_tokens: list[str] | None = None,
        drop_consecutive: bool = False,
        protect: bool | str = "auto",
        mode: str = "prune",
        **kwargs,
    ) -> dict:
        is_list = isinstance(context, (list, tuple))
        docs = [str(c) for c in context] if is_list else [str(context)]
        joined_orig = "\n\n".join(docs)
        origin_tokens = _count_tokens(joined_orig)
        query = question.strip() if isinstance(question, str) and question.strip() else None

        if target_token is not None and target_token > 0 and origin_tokens > 0:
            eff_rate = min(1.0, max(0.02, float(target_token) / origin_tokens))
        else:
            eff_rate = float(rate)

        extra_force = [t for t in (force_tokens or []) if isinstance(t, str) and t and t != "\n"] or None
        measure_fn = None
        if target_token is not None and target_token > 0:
            try:
                import tiktoken

                enc = tiktoken.get_encoding("o200k_base")
                measure_fn = lambda s: len(enc.encode(s, disallowed_special=()))
            except Exception:
                measure_fn = None

        if len(docs) > 1:
            results = self.compressor.compress_many(
                docs,
                rate=eff_rate,
                query=query,
                force_protected=protect,
                mode=mode,
                force_tokens=extra_force,
            )
            out_list = [r["text"] for r in results if r["text"]]
        else:
            res = self.compressor.compress(
                docs[0],
                rate=eff_rate,
                force_protected=protect,
                query=query,
                mode=mode,
                force_tokens=extra_force,
                measure=measure_fn if (target_token and target_token > 0 and measure_fn) else None,
            )
            out_list = [res["text"]]

        comp_body = "\n\n".join(out_list)
        parts = [p for p in (instruction.strip() if instruction else "", comp_body, question.strip() if question else "") if p]
        compressed_prompt = "\n\n".join(parts) if (instruction or question) else comp_body
        compressed_tokens = _count_tokens(compressed_prompt)
        actual_rate = compressed_tokens / max(1, origin_tokens)
        ratio = f"{origin_tokens / max(1, compressed_tokens):.1f}x"
        saving = (
            f", {max(0, origin_tokens - compressed_tokens)} tokens saved"
            if origin_tokens >= compressed_tokens
            else ""
        )
        return {
            "compressed_prompt": compressed_prompt,
            "compressed_prompt_list": out_list,
            "origin_tokens": origin_tokens,
            "compressed_tokens": compressed_tokens,
            "ratio": ratio,
            "rate": f"{100 * actual_rate:.1f}%",
            "saving": saving,
        }

    def compress_prompt_llmlingua2(
        self,
        context: str | Iterable[str],
        rate: float = 0.5,
        target_token: int = -1,
        force_tokens: list[str] | None = None,
        drop_consecutive: bool = False,
        **kwargs,
    ) -> dict:
        return self.compress_prompt(
            context=context,
            rate=rate,
            target_token=target_token,
            force_tokens=force_tokens,
            drop_consecutive=drop_consecutive,
            **kwargs,
        )
