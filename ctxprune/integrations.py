"""Drop-in compressors for LangChain and LlamaIndex retrieval pipelines.

    from ctxprune.integrations import CtxpruneDocumentCompressor   # LangChain
    retriever = ContextualCompressionRetriever(base_compressor=CtxpruneDocumentCompressor(rate=0.5),
                                               base_retriever=base)

    from ctxprune.integrations import CtxpruneNodePostprocessor    # LlamaIndex
    engine = index.as_query_engine(node_postprocessors=[CtxpruneNodePostprocessor(rate=0.5)])

Both compress each document's text in place and leave metadata alone. Classes are built
on first access so neither framework is required to import ctxprune.
"""

from __future__ import annotations

from functools import lru_cache

from .cli import DEFAULT_MODEL


@lru_cache(maxsize=4)
def _compressor(model: str, backend: str | None):
    from .compress import Compressor

    if backend is None:
        try:
            import onnxruntime  # noqa: F401
            backend = "onnx"
        except ImportError:
            backend = "torch"
    return Compressor(model, backend=backend)


def _langchain():
    from langchain_core.documents import Document
    from langchain_core.documents.compressor import BaseDocumentCompressor

    class CtxpruneDocumentCompressor(BaseDocumentCompressor):
        """LangChain document compressor: keeps `rate` of each document's text."""

        model: str = DEFAULT_MODEL
        backend: str | None = None
        rate: float = 0.5
        force_protected: bool = False

        def compress_documents(self, documents, query, callbacks=None):
            c = _compressor(self.model, self.backend)
            return [Document(page_content=c.compress(d.page_content, rate=self.rate,
                                                     force_protected=self.force_protected)["text"],
                             metadata=d.metadata, id=d.id)
                    for d in documents]

    return CtxpruneDocumentCompressor


def _llamaindex():
    from llama_index.core.postprocessor.types import BaseNodePostprocessor

    class CtxpruneNodePostprocessor(BaseNodePostprocessor):
        """LlamaIndex node postprocessor: keeps `rate` of each retrieved node's text."""

        model: str = DEFAULT_MODEL
        backend: str | None = None
        rate: float = 0.5
        force_protected: bool = False

        @classmethod
        def class_name(cls) -> str:
            return "CtxpruneNodePostprocessor"

        def _postprocess_nodes(self, nodes, query_bundle=None):
            c = _compressor(self.model, self.backend)
            for n in nodes:
                n.node.set_content(c.compress(n.node.get_content(), rate=self.rate,
                                              force_protected=self.force_protected)["text"])
            return nodes

    return CtxpruneNodePostprocessor


_FACTORIES = {"CtxpruneDocumentCompressor": _langchain, "CtxpruneNodePostprocessor": _llamaindex}
_built: dict[str, type] = {}


def __getattr__(name: str):
    if name not in _FACTORIES:
        raise AttributeError(name)
    if name not in _built:
        _built[name] = _FACTORIES[name]()
    return _built[name]
