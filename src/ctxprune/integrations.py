"""Drop-in compressors for LangChain and LlamaIndex retrieval pipelines.

    from ctxprune.integrations import CtxpruneDocumentCompressor   # LangChain
    retriever = ContextualCompressionRetriever(base_compressor=CtxpruneDocumentCompressor(rate=0.5),
                                               base_retriever=base)

    from ctxprune.integrations import CtxpruneNodePostprocessor    # LlamaIndex
    engine = index.as_query_engine(node_postprocessors=[CtxpruneNodePostprocessor(rate=0.5)])

Default behaviour is exactly 0.2/0.3: each document is pruned on its own, `force_protected=False`. Opt in to the
v0.4 engine with `force_protected="auto"` (or "ids", or `mode="auto"`): it passes the retrieval query into the
query-aware pruner and allocates a shared budget across a multi-document batch with overlap deduplication.
Classes are built on first access so neither framework is required to import ctxprune.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Union

from .compress import DEFAULT_MODEL


@lru_cache(maxsize=4)
def _compressor(model: str, backend: str | None):
    from .compress import Compressor

    return Compressor(model, backend=backend or "auto")


def _extract_scores(metadatas: list[dict]) -> list[float] | None:
    vals = []
    for m in metadatas:
        if not isinstance(m, dict):
            return None
        s = m.get("score", m.get("relevance_score"))
        if not isinstance(s, (int, float)):
            return None
        vals.append(float(s))
    return vals if vals else None


def _v04(force_protected, mode: str) -> bool:
    """The v0.4 engine (query-aware, shared budget across documents) is opt-in on these classes: the 0.2/0.3
    defaults (`force_protected=False`, no query, independent documents) keep returning the same bytes."""
    return force_protected in ("auto", "ids") or mode != "prune"


def _langchain():
    from langchain_core.documents import Document
    from langchain_core.documents.compressor import BaseDocumentCompressor

    class CtxpruneDocumentCompressor(BaseDocumentCompressor):
        """LangChain document compressor: query-aware, cross-document budget allocation."""

        model: str = DEFAULT_MODEL
        backend: str | None = None
        rate: float = 0.5
        force_protected: Union[bool, str] = False
        dedup_overlap: bool = True
        mode: str = "prune"

        def compress_documents(self, documents, query, callbacks=None):
            docs = list(documents)
            if not docs:
                return []
            c = _compressor(self.model, self.backend)
            if not _v04(self.force_protected, self.mode):  # 0.2/0.3 behaviour, byte-identical
                return [Document(page_content=c.compress(d.page_content, rate=self.rate,
                                                         force_protected=self.force_protected)["text"],
                                 metadata=d.metadata, id=d.id)
                        for d in docs]
            if len(docs) == 1:
                txt = c.compress(
                    docs[0].page_content,
                    rate=self.rate,
                    force_protected=self.force_protected,
                    query=query,
                    mode=self.mode,
                )["text"]
                return [Document(page_content=txt, metadata=docs[0].metadata, id=docs[0].id)]
            scores = _extract_scores([d.metadata for d in docs])
            res = c.compress_many(
                [d.page_content for d in docs],
                rate=self.rate,
                query=query,
                scores=scores,
                force_protected=self.force_protected,
                dedup_overlap=self.dedup_overlap,
                mode=self.mode,
            )
            return [
                Document(page_content=r["text"], metadata=d.metadata, id=d.id)
                for d, r in zip(docs, res)
            ]

    return CtxpruneDocumentCompressor


def _llamaindex():
    from llama_index.core.postprocessor.types import BaseNodePostprocessor

    class CtxpruneNodePostprocessor(BaseNodePostprocessor):
        """LlamaIndex node postprocessor: query-aware, score-weighted pruning across nodes."""

        model: str = DEFAULT_MODEL
        backend: str | None = None
        rate: float = 0.5
        force_protected: Union[bool, str] = False
        dedup_overlap: bool = True
        mode: str = "prune"

        @classmethod
        def class_name(cls) -> str:
            return "CtxpruneNodePostprocessor"

        def _postprocess_nodes(self, nodes, query_bundle=None):
            node_list = list(nodes)
            if not node_list:
                return node_list
            c = _compressor(self.model, self.backend)
            if not _v04(self.force_protected, self.mode):  # 0.2/0.3 behaviour, byte-identical
                for n in node_list:
                    n.node.set_content(c.compress(n.node.get_content(), rate=self.rate,
                                                  force_protected=self.force_protected)["text"])
                return node_list
            query = getattr(query_bundle, "query_str", None) if query_bundle is not None else None
            if len(node_list) == 1:
                txt = c.compress(
                    node_list[0].node.get_content(),
                    rate=self.rate,
                    force_protected=self.force_protected,
                    query=query,
                    mode=self.mode,
                )["text"]
                node_list[0].node.set_content(txt)
                return node_list
            raw_scores = [n.score for n in node_list]
            scores = [float(s) for s in raw_scores] if all(isinstance(s, (int, float)) for s in raw_scores) else None
            res = c.compress_many(
                [n.node.get_content() for n in node_list],
                rate=self.rate,
                query=query,
                scores=scores,
                force_protected=self.force_protected,
                dedup_overlap=self.dedup_overlap,
                mode=self.mode,
            )
            for n, r in zip(node_list, res):
                n.node.set_content(r["text"])
            return node_list

    return CtxpruneNodePostprocessor


_FACTORIES = {"CtxpruneDocumentCompressor": _langchain, "CtxpruneNodePostprocessor": _llamaindex}
_built: dict[str, type] = {}


def __getattr__(name: str):
    if name not in _FACTORIES:
        raise AttributeError(name)
    if name not in _built:
        _built[name] = _FACTORIES[name]()
    return _built[name]
