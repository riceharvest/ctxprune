import pytest

LOG = ('2026-10-08T09:14:06.002Z ERROR [worker-3] job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a failed after 3 retries: '
       'ECONNREFUSED 10.0.4.17:5432 while the scheduler was trying to reconnect to the primary database replica.')


def test_langchain_compressor():
    pytest.importorskip("langchain_core")
    pytest.importorskip("onnxruntime")
    from langchain_core.documents import Document

    from ctxprune.integrations import CtxpruneDocumentCompressor

    docs = [Document(page_content=LOG, metadata={"source": "log"})]
    out = CtxpruneDocumentCompressor(rate=0.5, force_protected=True).compress_documents(docs, "why did the job fail?")
    assert out[0].metadata == {"source": "log"}
    assert len(out[0].page_content) < len(LOG)
    assert "5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a" in out[0].page_content


def test_llamaindex_postprocessor():
    pytest.importorskip("llama_index.core")
    pytest.importorskip("onnxruntime")
    from llama_index.core.schema import NodeWithScore, TextNode

    from ctxprune.integrations import CtxpruneNodePostprocessor

    nodes = [NodeWithScore(node=TextNode(text=LOG), score=1.0)]
    out = CtxpruneNodePostprocessor(rate=0.5, force_protected=True).postprocess_nodes(nodes, query_str="why?")
    assert len(out[0].node.get_content()) < len(LOG)
    assert "10.0.4.17:5432" in out[0].node.get_content()
