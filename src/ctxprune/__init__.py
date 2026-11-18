"""ctxprune: lossless compression of agent tool outputs (Claude Code, Hermes, Pi, OpenCode, dsh, Codex).

Kept import-free on purpose: the Claude Code hook imports this package on every tool call. The 0.2 token-pruning
API (`ctxprune.prune`, `Compressor`, `DEFAULT_MODEL`) is still here and loads its model code only on first use:

    import ctxprune
    ctxprune.prune(text, keep=0.5)                 # darioooooo0o/ctxprune-small, int8 ONNX on CPU
    ctxprune.prune(text, keep=0.33, protect=True)  # never drop identifiers, numbers or paths
"""

__version__ = "0.3.0"
__all__ = ["Compressor", "prune", "DEFAULT_MODEL"]

_default = None


def prune(text: str, keep: float = 0.5, protect: bool = False) -> str:
    """Compress `text` to about `keep` of its tokens with the default model (loaded once, int8 ONNX).
    protect=True never drops identifiers, numbers or paths. Same behaviour as 0.2.1."""
    global _default
    if _default is None:
        from .compress import Compressor
        _default = Compressor()
    return _default.compress(text, rate=keep, force_protected=protect)["text"]


def __getattr__(name: str):   # PEP 562: `from ctxprune import Compressor` without importing it for the hook
    if name in ("Compressor", "DEFAULT_MODEL"):
        from . import compress
        return getattr(compress, name)
    raise AttributeError(f"module 'ctxprune' has no attribute {name!r}")
