"""ctxprune: context compression for AI agents.

    import ctxprune
    ctxprune.prune(tool_output)              # keep ~50%, IDs/paths never split
    ctxprune.prune(tool_output, keep=0.33, protect=True)
"""

from .compress import DEFAULT_MODEL, Compressor

__all__ = ["Compressor", "prune", "DEFAULT_MODEL"]
__version__ = "0.2.0"

_default: Compressor | None = None


def prune(text: str, keep: float = 0.5, protect: bool = False) -> str:
    """Compress `text` to about `keep` of its length with the default model (loaded once, int8 ONNX).
    protect=True never drops identifiers, numbers or paths."""
    global _default
    if _default is None:
        _default = Compressor()
    return _default.compress(text, rate=keep, force_protected=protect)["text"]
