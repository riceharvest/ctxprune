"""ctxprune: lossless compression of agent tool outputs (Claude Code, Hermes, Pi, OpenCode, dsh, Codex).

Kept import-free on purpose: the Claude Code hook imports this package on every tool call. The token-pruning
API (`ctxprune.prune`, `Compressor`, `score`, `prune_scored`, `compress_many`, `PromptCompressor`) loads its
model code only on first use:

    import ctxprune
    ctxprune.prune(text, keep=0.5)                              # darioooooo0o/ctxprune-small, int8 ONNX on CPU
    ctxprune.prune(text, keep=0.33, protect="auto", query=q)   # v0.4 engine: query-aware + smart ID/prose guard
"""

from __future__ import annotations

__version__ = "0.4.1"
__all__ = [
    "Compressor",
    "ScoredText",
    "PromptCompressor",
    "prune",
    "score",
    "prune_scored",
    "compress_many",
    "DEFAULT_MODEL",
]

_default = None


def _get_default():
    global _default
    if _default is None:
        from .compress import Compressor

        _default = Compressor()
    return _default


def prune(
    text: str,
    keep: float = 0.5,
    protect: bool | str | None = None,
    query: str | None = None,
    mode: str = "prune",
    unit: str | None = None,
    clean_web: bool = False,
) -> str:
    """Compress `text` to about `keep` of its tokens with the default model (loaded once, int8 ONNX).

    `prune(text, keep)` / `prune(text, keep, protect=True|False)` return exactly the bytes 0.3.2 returned.
    The v0.4 engine is opt-in: pass `protect="auto"` (keeps technical IDs/logs verbatim while plain prose prunes
    cleanly; recommended for tool output and retrieved text), `protect="ids"`, or any of `query`, `mode`, `unit`,
    `clean_web` (then an unset `protect` means "auto"). protect=True keeps every ID/number/path (strict).
    """
    return _get_default().compress(
        text,
        rate=keep,
        force_protected=protect,
        query=query,
        mode=mode,
        unit=unit,
        clean_web=clean_web,
    )["text"]


def score(text: str):
    """Pre-score `text` into a compact uint8 ScoredText artifact for sub-millisecond query-time slicing."""
    return _get_default().score(text)


def prune_scored(
    scored,
    keep: float = 0.5,
    protect: bool | str = "auto",
    query: str | None = None,
    unit: str = "tokens_est",
) -> str:
    """Slice a pre-scored ScoredText object with zero neural inference."""
    return _get_default().prune_scored(
        scored,
        rate=keep,
        force_protected=protect,
        query=query,
        unit=unit,
    )["text"]


def compress_many(
    texts: list[str],
    keep: float = 0.5,
    query: str | None = None,
    scores: list[float] | None = None,
    protect: bool | str = "auto",
    dedup_overlap: bool = True,
    mode: str = "prune",
) -> list[str]:
    """Compress retrieved documents under a shared token budget with chunk overlap deduplication."""
    return [
        r["text"]
        for r in _get_default().compress_many(
            texts,
            rate=keep,
            query=query,
            scores=scores,
            force_protected=protect,
            dedup_overlap=dedup_overlap,
            mode=mode,
        )
    ]


def __getattr__(name: str):  # PEP 562: keep `import ctxprune` import-free for the hook
    if name in ("Compressor", "DEFAULT_MODEL", "ScoredText"):
        from . import compress

        return getattr(compress, name)
    if name == "PromptCompressor":
        from .compat import PromptCompressor

        return PromptCompressor
    raise AttributeError(f"module 'ctxprune' has no attribute {name!r}")
