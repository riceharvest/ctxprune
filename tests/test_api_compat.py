"""0.2/0.3 API compatibility: old call shapes keep returning the exact bytes 0.3.2 returned (`_v032.py`).

tests/golden/golden_032.json holds outputs recorded from a pristine ctxprune==0.3.2 install on the inputs below
(runbook probe_api.py; see research/v04_code/lab/API_DIFF.md). The routing tests need no model.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from ctxprune.compress import Compressor  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden" / "golden_032.json").read_text())

TEXTS = {
    "json": json.dumps([{"id": f"ord_8f3a2c{i:02d}", "status": "failed", "amount": 1999 + i, "ip": f"10.0.{i}.7",
                         "path": f"/srv/app/v1.2.{i}/worker.py",
                         "msg": "Timeout while waiting for the payment provider to answer the capture request"}
                        for i in range(12)], indent=2),
    "log": "\n".join(f"2026-11-18T10:{i:02d}:03Z WARN worker-{i % 3} retrying job 4f3a-91{i} after connection reset "
                     f"by peer (attempt {i}/5)" for i in range(40)) + "\nERROR job 4f3a-9139 failed permanently: database is locked\n",
    "prose": ("The quarterly planning meeting covered the migration of the billing service to the new cluster, the open "
              "questions about data retention for European customers, and a proposal to move the on-call rotation from "
              "weekly to bi-weekly shifts. Most attendees agreed that the migration should wait until the retention "
              "policy is settled, because moving the data twice would cost more than the delay. ") * 4,
}


@pytest.fixture(scope="module")
def comp() -> Compressor:
    pytest.importorskip("onnxruntime")
    try:
        return Compressor()
    except Exception as e:  # model not cached and no network
        pytest.skip(f"model unavailable: {e}")


def test_old_prune_calls_match_0_3_2_bytes(comp):
    import ctxprune

    ctxprune._default = comp
    try:
        for key, want in GOLDEN.items():
            if not key.startswith("prune/"):
                continue
            _, name, keep, prot = key.split("/")
            assert ctxprune.prune(TEXTS[name], keep=float(keep), protect=(prot == "True")) == want, key
    finally:
        ctxprune._default = None


def test_old_compressor_call_matches_0_3_2_bytes(comp):
    r = comp.compress(TEXTS["log"], rate=0.5, keep_structure=True)
    assert r["text"] == GOLDEN["Compressor.text"]
    assert sorted(r) == ["kept", "text", "total"]


def test_new_entry_points_use_v04_engine(comp):
    old = comp.compress(TEXTS["prose"], rate=0.5, force_protected=False)["text"]
    v04 = comp.compress(TEXTS["prose"], rate=0.5, force_protected="auto")["text"]
    pinned = Compressor(engine="v04").compress(TEXTS["prose"], rate=0.5, force_protected=False)["text"]
    assert v04 != old and pinned != old
    assert comp.compress(TEXTS["prose"], rate=0.5, query="on-call rotation?")["text"] != old


@pytest.mark.parametrize("kw,expected", [
    ({}, False),
    ({"force_protected": False}, False),
    ({"force_protected": True}, False),
    ({"unit": "chars"}, False),
    ({"unit": "tokens"}, False),
    ({"unit": len}, False),
    ({"force_protected": "auto"}, True),
    ({"force_protected": "ids"}, True),
    ({"unit": "tokens_est"}, True),
    ({"unit": "o200k"}, True),
    ({"query": "q"}, True),
    ({"mode": "auto"}, True),
    ({"mode": "json"}, True),
    ({"clean_web": True}, True),
    ({"fast": True}, True),
    ({"overlap": 0}, True),
    ({"force_tokens": ["x"]}, True),
    ({"legacy": True}, True),
])
def test_engine_routing_table(kw, expected):
    args = dict(force_protected=None, unit=None, query=None, mode="prune", clean_web=False, fast=False,
                overlap=None, force_tokens=None, legacy=False)
    args.update(kw)
    assert Compressor._wants_v04(**args) is expected


@pytest.mark.parametrize("ctor,expected", [
    ({}, False), ({"rep_decay": "prose"}, True), ({"rep_decay": "off"}, True), ({"fewshot": "pin"}, True),
    ({"overlap": 64}, True), ({"overlap": 0}, False),
])
def test_constructor_options_route_plain_compress_to_v04(monkeypatch, ctor, expected):
    c = Compressor.__new__(Compressor)
    for k, v in ctor.items():
        setattr(c, k, v)
    seen = []
    monkeypatch.setattr(c, "_compress_v04", lambda text, **kw: seen.append("v04") or {}, raising=False)
    import ctxprune._v032 as v032
    monkeypatch.setattr(v032, "compress", lambda comp, text, *a: seen.append("v032") or {})
    c.compress("x", rate=0.5)
    assert seen == (["v04"] if expected else ["v032"])


def test_engine_pinning_and_validation(monkeypatch):
    seen = []
    c = Compressor.__new__(Compressor)
    monkeypatch.setattr(c, "_compress_v04", lambda text, **kw: seen.append(("v04", kw["force_protected"], kw["unit"])) or {}, raising=False)
    import ctxprune._v032 as v032
    monkeypatch.setattr(v032, "compress", lambda comp, text, *a: seen.append(("v032", a[2], a[4])) or {})
    c.compress("x")
    c.compress("x", force_protected=True)
    c.compress("x", force_protected="auto")
    c.engine = "v04"
    c.compress("x", force_protected=False)
    c.engine = "v032"
    c.compress("x", force_protected="auto")
    assert seen == [("v032", False, "chars"), ("v032", True, "chars"), ("v04", "auto", "tokens_est"),
                    ("v04", False, "tokens_est"), ("v032", True, "chars")]
    with pytest.raises(ValueError):
        Compressor(engine="nope")


def _fake_frameworks(monkeypatch):
    class FakeDoc:
        def __init__(self, page_content, metadata=None, id=None):
            self.page_content, self.metadata, self.id = page_content, metadata or {}, id

    class FakeBase:
        model_fields: dict = {}

        def __init__(self, **kw):
            for k, v in {**{k: getattr(type(self), k) for k in ("model", "backend", "rate", "force_protected",
                                                              "dedup_overlap", "mode")}, **kw}.items():
                setattr(self, k, v)

    lc = types.ModuleType("langchain_core")
    docs = types.ModuleType("langchain_core.documents")
    docs.Document = FakeDoc
    comp = types.ModuleType("langchain_core.documents.compressor")
    comp.BaseDocumentCompressor = FakeBase
    for k, m in {"langchain_core": lc, "langchain_core.documents": docs, "langchain_core.documents.compressor": comp}.items():
        monkeypatch.setitem(sys.modules, k, m)
    return FakeDoc


def test_langchain_default_is_per_document_0_3_2_behaviour(monkeypatch):
    FakeDoc = _fake_frameworks(monkeypatch)
    calls = []

    class Stub:
        def compress(self, text, **kw):
            calls.append(("compress", kw))
            return {"text": text[:5]}

        def compress_many(self, texts, **kw):
            calls.append(("compress_many", kw))
            return [{"text": t[:5]} for t in texts]

    import ctxprune.integrations as integ

    monkeypatch.setattr(integ, "_compressor", lambda model, backend: Stub())
    integ._built.clear()
    DC = integ.__getattr__("CtxpruneDocumentCompressor")
    docs = [FakeDoc("aaaaaaaaaa", {"score": 1}), FakeDoc("bbbbbbbbbb", {"score": 2})]
    out = DC(rate=0.5).compress_documents(docs, "some query")
    assert [d.page_content for d in out] == ["aaaaa", "bbbbb"]
    assert calls == [("compress", {"rate": 0.5, "force_protected": False})] * 2  # no query, no shared budget
    calls.clear()
    DC(rate=0.5, force_protected="auto").compress_documents(docs, "some query")
    assert calls[0][0] == "compress_many" and calls[0][1]["query"] == "some query"
