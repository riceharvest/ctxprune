"""Comprehensive unit and integration tests for ctxprune v0.4 code-only improvements."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from ctxprune import legacy_cli, mcp_server  # noqa: E402
from ctxprune.atoms import (  # noqa: E402
    atomize,
    detok,
    est_atom_tokens,
    is_prose_artifact_id,
    is_strict_id,
    is_technical_text,
)
from ctxprune.compress import Compressor, ScoredText, clean_web_text  # noqa: E402


@pytest.fixture(scope="module")
def comp() -> Compressor:
    pytest.importorskip("onnxruntime")
    return Compressor()


def test_import_free_startup():
    """Importing ctxprune must NEVER load onnxruntime, numpy, tokenizers, or compress."""
    code = (
        "import sys, ctxprune; "
        "loaded = [m for m in ('onnxruntime', 'numpy', 'tokenizers', 'torch', 'transformers', 'ctxprune.compress', 'ctxprune.compat') if m in sys.modules]; "
        "assert not loaded, f'eager imports found: {loaded}'"
    )
    env = dict(os.environ, PYTHONPATH=REPO_SRC)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
    assert p.returncode == 0


def test_detok_preserves_paragraph_breaks():
    text = "Paragraph one first sentence. Filler words removed here.\n\nParagraph two keeps going nicely."
    atoms = atomize(text)
    keep = [a.text not in ("Filler", "words", "removed", "here", ".") or i < 5 or i > 10 for i, a in enumerate(atoms)]
    # Explicitly drop atoms spanning right before "\n\n"
    for i, a in enumerate(atoms):
        if a.text in ("words", "removed", "here"):
            keep[i] = False
    out = detok(atoms, keep)
    assert "\n\n" in out
    assert "Paragraph one" in out and "Paragraph two" in out


def test_strict_id_prose_artifact_and_technical_detector():
    assert is_strict_id("5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a")
    assert is_strict_id("/var/log/nginx/error.log")
    assert is_strict_id("--max-tokens")
    assert is_strict_id("v2.4.1")
    assert not is_strict_id("1970s")
    assert not is_strict_id("U.S")
    assert not is_strict_id("McKinna")
    assert is_prose_artifact_id("https://example.com/a/b")
    assert is_prose_artifact_id("my_func_name")
    assert not is_prose_artifact_id("1985")

    prose = (
        "The team met on Tuesday to discuss the quarterly roadmap and review feedback from customers. "
        "Participants shared updates {vocalsound} about timelines and staffing across departments.\n\n"
        "Marketing: We should focus on onboarding clarity before adding new features.\n"
        "Project Manager: Agreed, let us revisit metrics after the launch."
    )
    assert not is_technical_text(atomize(prose), prose)

    log = (
        "2026-10-08T09:14:06Z ERROR [worker-3] job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a failed\n"
        "status: failed\nretryable: false\nerror_code: ECONNREFUSED\nendpoint: 10.0.4.17:5432\n"
    )
    assert is_technical_text(atomize(log), log)


def test_est_atom_tokens():
    assert 0.8 <= est_atom_tokens("hello") <= 1.2
    assert est_atom_tokens("https://api.github.com/repos/foo/bar") > 3.0
    assert est_atom_tokens("我") < 1.5


def test_t13_thread_cap_and_env_override(monkeypatch):
    pytest.importorskip("onnxruntime")
    c_def = Compressor()
    assert 1 <= c_def.threads <= 4
    monkeypatch.setenv("CTXPRUNE_THREADS", "2")
    c_env = Compressor()
    assert c_env.threads == 2


def test_t14_overlap_option(comp):
    long_text = "\n\n".join(f"Paragraph {i}: Unique factoid_{i} in the retrieval evaluation set." for i in range(1, 90))
    a0, s0, _ = comp.atom_scores(long_text, overlap=0)
    a64, s64, _ = comp.atom_scores(long_text, overlap=64)
    assert len(a0) == len(s0) == len(a64) == len(s64)
    out = comp.compress(long_text, rate=0.5, overlap=32)["text"]
    assert len(out) > 100


def test_l14_web_cleaner():
    raw = (
        'Intro https://docs.example.com/guide?utm_source=x&utm_medium=y&id=42 and '
        'https://a.test/?fbclid=123\u200b.\n'
        '<script id="__NEXT_DATA__" type="application/json">{"huge": "blob"}</script>\n'
        '<!-- hidden comment -->\n'
        '|  Name    |  Score   |\n'
        '| -------- | :------: |\n'
        '|  Alice   |   98     |\n'
    )
    cleaned = clean_web_text(raw)
    assert "__NEXT_DATA__" not in cleaned
    assert "hidden comment" not in cleaned
    assert "utm_source" not in cleaned
    assert "fbclid" not in cleaned
    assert "https://docs.example.com/guide?id=42" in cleaned
    assert "https://a.test/" in cleaned
    assert "|Name|Score|" in cleaned
    assert "\u200b" not in cleaned


def test_l11_negation_comparator_and_kv_pinning(comp):
    bg = " ".join(["Routine background telemetry notes about weekly regional capacity reviews."] * 8)
    cases = [
        ("does backup encrypt?", "The backup service does not encrypt snapshots by default.", ["not", "encrypt"]),
        ("when fail?", "Health check fails whenever status_code != 200 or latency >= 1500 ms.", ["!=", ">="]),
        ("reject?", "Reject payload if content_length == 0 or header_count > 64.", ["=="]),
        ("retryable?", "status: failed\nretryable: false\nregion: eu-central-1\nerror_code: ERR_TIMEOUT", ["retryable:", "false"]),
    ]
    for _q, target, must in cases:
        doc = f"{bg}\n\n{target}\n\n{bg}"
        out = comp.compress(doc, rate=0.5, force_protected="auto")["text"]
        low = out.lower()
        for m in must:
            assert m.lower() in low, f"missing {m!r} in {out!r}"


def test_t12_protect_auto_preserves_tech_ids(comp):
    log = (
        "Routine worker chatter started up cleanly and waited on idle queue polling.\n" * 6
        + "ERROR [worker-3] job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a failed: "
        + "ECONNREFUSED 10.0.4.17:5432 on /srv/app/worker.py\n"
        + "Routine worker chatter shut down cleanly after flushing metrics.\n" * 6
    )
    out = comp.compress(log, rate=0.45, force_protected="auto")["text"]
    assert "5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a" in out
    assert "10.0.4.17:5432" in out
    assert "/srv/app/worker.py" in out


def test_t11_query_mode_focuses_evidence(comp):
    noise = "\n\n".join(
        f"Section {i}: Routine infrastructure deployment summary covering mirror latency and weekly logs."
        for i in range(1, 20)
    )
    needle = "\n\nSection 11b: The secret canary code is ZXQ_9941_DELTA enabled only in eu-north-1.\n\n"
    doc = noise[: len(noise) // 2] + needle + noise[len(noise) // 2 :]
    out_q = comp.compress(doc, rate=0.25, query="what is the secret canary code?")["text"]
    assert "ZXQ_9941_DELTA" in out_q


def test_t15_l31_mode_auto_and_json_validity(comp):
    payload = {
        "id": "issue-4091",
        "state": "open",
        "labels": ["bug", "p0"],
        "url": "https://github.com/org/repo/issues/4091",
        "body": " ".join(["This bug causes connection pooling stalls under burst load when keepalive expires."] * 8),
        "comments": [
            {"author": "alice", "body": " ".join(["Reproduced locally on Python 3.12 with TLS enabled."] * 7)},
        ],
    }
    raw = json.dumps(payload, indent=2)
    out_auto = comp.compress(raw, rate=0.45, mode="auto")["text"]
    parsed = json.loads(out_auto)
    assert parsed["id"] == "issue-4091"
    assert parsed["state"] == "open"
    assert parsed["url"] == "https://github.com/org/repo/issues/4091"
    assert len(out_auto) < len(raw)

    out_json = comp.compress(raw, rate=0.40, mode="json")["text"]
    parsed_j = json.loads(out_json)
    assert parsed_j["id"] == "issue-4091"


def test_t16_compress_many_dedup_and_allocation(comp):
    overlap = "This shared overlap paragraph describes the shared database schema migration step and rollback."
    docs = [
        "Doc1: Primary root cause was bad TLS cert rotation on ingress-west. " + overlap,
        overlap + " Doc2: Secondary unrelated note on frontend CSS bundle caching.",
        "Doc1: Primary root cause was bad TLS cert rotation on ingress-west. " + overlap,  # exact duplicate
        "Doc4: Completely unrelated sports trivia about basketball championships and scores.",
    ]
    res = comp.compress_many(docs, rate=0.5, query="what caused TLS failure?", scores=[0.95, 0.70, 0.50, 0.20])
    assert len(res) == 4
    assert res[2]["text"] == ""  # exact duplicate stripped
    joined = "\n\n".join(r["text"] for r in res if r["text"])
    assert "TLS" in joined or "ingress-west" in joined
    assert joined.count("shared database schema migration") == 1


def test_t17_score_and_prune_scored_roundtrip_and_speed(comp):
    doc = "\n\n".join(
        f"Paragraph {i}: Service worker {i} observed latency 142ms on cluster-{i} with status ok."
        for i in range(1, 25)
    )
    scored = comp.score(doc)
    d = scored.to_dict()
    restored = ScoredText.from_dict(d)
    assert restored.text == scored.text
    assert restored.scores_u8 == scored.scores_u8

    direct = comp.compress(doc, rate=0.5, force_protected="auto")["text"]
    from_scored = comp.prune_scored(restored, rate=0.5, force_protected="auto")["text"]
    # High atom IoU between float32 and uint8 logit-quantized scores
    a_dir = set(direct.split())
    a_sco = set(from_scored.split())
    iou = len(a_dir & a_sco) / max(1, len(a_dir | a_sco))
    assert iou >= 0.90, f"uint8 IoU too low: {iou:.3f}"

    chunks = [scored] * 10
    t0 = time.perf_counter()
    for _ in range(5):
        for sc in chunks:
            comp.prune_scored(sc, rate=0.45)
    ms_per_10_noq = (time.perf_counter() - t0) / 5 * 1000
    assert ms_per_10_noq < 10.0, f"prune_scored (no query) unexpectedly slow: {ms_per_10_noq:.1f} ms"

    t1 = time.perf_counter()
    for _ in range(5):
        for sc in chunks:
            comp.prune_scored(sc, rate=0.45, query="worker 12 latency")
    ms_per_10 = (time.perf_counter() - t1) / 5 * 1000
    assert ms_per_10 < 20.0, f"prune_scored (with query) unexpectedly slow: {ms_per_10:.1f} ms"


def test_integrations_forward_query_and_compress_many(monkeypatch):
    """Verify integrations.py forwards query + retrieval scores cleanly without heavy frameworks installed."""
    import sys
    import types

    class FakeDoc:
        def __init__(self, page_content, metadata=None, id=None):
            self.page_content = page_content
            self.metadata = metadata or {}
            self.id = id

    class FakeBaseComp:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class FakeInnerNode:
        def __init__(self, text):
            self._text = text

        def get_content(self):
            return self._text

        def set_content(self, t):
            self._text = t

    class FakeNWS:
        def __init__(self, node, score=None):
            self.node = node
            self.score = score

    class FakeBaseNodePost:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    lc_mod = types.ModuleType("langchain_core")
    lc_docs = types.ModuleType("langchain_core.documents")
    lc_docs.Document = FakeDoc
    lc_comp = types.ModuleType("langchain_core.documents.compressor")
    lc_comp.BaseDocumentCompressor = FakeBaseComp

    li_mod = types.ModuleType("llama_index")
    li_core = types.ModuleType("llama_index.core")
    li_post = types.ModuleType("llama_index.core.postprocessor")
    li_types = types.ModuleType("llama_index.core.postprocessor.types")
    li_types.BaseNodePostprocessor = FakeBaseNodePost

    calls = []

    class StubComp:
        def compress(self, text, **kw):
            calls.append(("compress", kw.get("query"), kw.get("force_protected")))
            return {"text": text[:max(10, len(text) // 2)], "kept": 1, "total": 2}

        def compress_many(self, texts, **kw):
            calls.append(("compress_many", kw.get("query"), kw.get("scores"), kw.get("force_protected")))
            return [{"text": t[:max(10, len(t) // 2)], "kept": 1, "total": 2} for t in texts]

    for k, m in {
        "langchain_core": lc_mod,
        "langchain_core.documents": lc_docs,
        "langchain_core.documents.compressor": lc_comp,
        "llama_index": li_mod,
        "llama_index.core": li_core,
        "llama_index.core.postprocessor": li_post,
        "llama_index.core.postprocessor.types": li_types,
    }.items():
        monkeypatch.setitem(sys.modules, k, m)

    import ctxprune.integrations as integ

    monkeypatch.setattr(integ, "_compressor", lambda model, backend: StubComp())
    integ._built.clear()

    DocComp = integ.__getattr__("CtxpruneDocumentCompressor")
    dc = DocComp(rate=0.5, force_protected="auto")
    docs = [FakeDoc("doc one text content", {"score": 0.9}, "id1"), FakeDoc("doc two text content", {"score": 0.4}, "id2")]
    out_docs = dc.compress_documents(docs, query="secret question")
    assert len(out_docs) == 2 and out_docs[0].metadata == {"score": 0.9} and out_docs[0].id == "id1"
    assert calls[-1] == ("compress_many", "secret question", [0.9, 0.4], "auto")

    NodePost = integ.__getattr__("CtxpruneNodePostprocessor")
    np_ = NodePost(rate=0.5, force_protected="auto")
    nodes = [FakeNWS(FakeInnerNode("node one text"), 0.8), FakeNWS(FakeInnerNode("node two text"), 0.3)]
    out_nodes = np_._postprocess_nodes(nodes, query_bundle=types.SimpleNamespace(query_str="node query"))
    assert len(out_nodes) == 2
    assert calls[-1] == ("compress_many", "node query", [0.8, 0.3], "auto")


def test_l15_compat_prompt_compressor():
    pytest.importorskip("onnxruntime")
    from ctxprune.compat import PromptCompressor

    pc = PromptCompressor(model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank", use_llmlingua2=True)
    ctx = [
        "First document discusses caching strategies for PostgreSQL read replicas in eu-central-1.",
        "Second document explains how the payment gateway fails when webhook_secret is missing.",
    ]
    res = pc.compress_prompt(ctx, question="why does payment gateway fail?", rate=0.5, force_tokens=["\n", "."])
    assert set(res) >= {"compressed_prompt", "compressed_prompt_list", "origin_tokens", "compressed_tokens", "ratio", "rate", "saving"}
    assert res["compressed_tokens"] <= res["origin_tokens"]
    assert "payment" in res["compressed_prompt"].lower() or "webhook_secret" in res["compressed_prompt"].lower()

    res2 = pc.compress_prompt_llmlingua2("Simple standalone string with enough words to compress down safely. " * 6, target_token=30)
    assert res2["compressed_tokens"] <= 35


def test_l13_fast_cascade(comp):
    para = "Routine background log line with filler descriptions and standard metrics collected every hour.\n"
    long_doc = para * 140 + "CRITICAL_NEEDLE_XYZ: failover triggered at 03:14Z due to quorum loss.\n" + para * 60
    res = comp.compress(long_doc, rate=0.25, query="why did failover trigger?", fast=True)
    assert "CRITICAL_NEEDLE_XYZ" in res["text"] or "quorum" in res["text"]


def test_mcp_and_cli_options(capsys):
    pytest.importorskip("onnxruntime")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write("Alpha background line one.\nBeta secret token XYZ_771.\nGamma background line three.\n" * 6)
        path = tf.name
    try:
        res = mcp_server.call_tool("read_pruned", {"path": path, "keep": 0.5, "protect": "auto", "query": "secret token"})
        assert not res["isError"]
        assert "query-aware" in res["content"][1]["text"]

        rc = legacy_cli.compress_main([path, "--keep", "0.5", "--query", "secret token", "--protect-mode", "auto"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "XYZ_771" in out
    finally:
        os.unlink(path)


def _synthetic_scored(text: str):
    """Deterministic fake keep-scores (no model): early atoms score lower so selection has something to choose."""
    atoms = atomize(text)
    scores = [0.15 + 0.7 * ((i * 37) % 101) / 100 for i in range(len(atoms))]
    return atoms, scores


def _bare_compressor(**flags) -> Compressor:
    c = Compressor.__new__(Compressor)  # no model: _select_atoms only needs the scores
    for k, v in flags.items():
        setattr(c, k, v)
    return c


_FEWSHOT = "".join(
    f"Passage:\nPassage number {i} about topic {i} with several filler words in it.\nQuestion:\nWhat is topic {i}?\nAnswer:\nAns{i}\n"
    for i in range(6)
)


def test_fewshot_qa_mask_detects_blocks_and_keeps_format_atoms():
    from ctxprune.compress import _fewshot_qa_mask
    from ctxprune.atoms import line_spans

    atoms = atomize(_FEWSHOT)
    mask = _fewshot_qa_mask(atoms, line_spans(atoms))
    assert mask is not None
    pinned = {a.text for a, m in zip(atoms, mask) if m}
    assert {"Question", "Answer", "Ans0", "Ans5"} <= {p.rstrip(":") for p in pinned} | pinned
    assert "filler" not in pinned and "Passage" not in pinned
    two = "".join(_FEWSHOT.splitlines(True)[:12])
    a2 = atomize(two)
    assert _fewshot_qa_mask(a2, line_spans(a2)) is None  # needs >= 3 Question/Answer pairs


def test_fewshot_flag_pins_format_and_default_is_unchanged():
    atoms, scores = _synthetic_scored(_FEWSHOT)
    q = "topic 3 filler"
    off = _bare_compressor()._select_atoms(atoms, scores, None, _FEWSHOT, rate=0.3, query=q)["text"]
    explicit_off = _bare_compressor(fewshot="off", rep_decay="all")._select_atoms(
        atoms, scores, None, _FEWSHOT, rate=0.3, query=q)["text"]
    assert off == explicit_off  # defaults unchanged
    pin = _bare_compressor(fewshot="pin")._select_atoms(atoms, scores, None, _FEWSHOT, rate=0.3, query=q)["text"]
    assert pin.count("Answer:") == 6 and pin.count("Question:") == 6
    assert all(f"Ans{i}" in pin for i in range(6))
    noq = _bare_compressor(fewshot="noquery")._select_atoms(atoms, scores, None, _FEWSHOT, rate=0.3, query=q)["text"]
    none = _bare_compressor()._select_atoms(atoms, scores, None, _FEWSHOT, rate=0.3, query=None)["text"]
    assert noq == none  # query ignored on few-shot text
    plain = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon"
    pa, ps = _synthetic_scored(plain)
    assert _bare_compressor(fewshot="both")._select_atoms(pa, ps, None, plain, rate=0.5, query="gamma")["text"] == \
        _bare_compressor()._select_atoms(pa, ps, None, plain, rate=0.5, query="gamma")["text"]  # no few-shot: no-op


def test_rep_decay_prose_mode_skips_code_only():
    code = ("def f(x):\n    return self.value(x) + self.value(x)\n" * 12) + "    return self.value("
    atoms, scores = _synthetic_scored(code)
    assert is_technical_text(atoms, code)
    allm = _bare_compressor()._prepare_base(atoms, scores, code)["cal_scores"]
    prose = _bare_compressor(rep_decay="prose")._prepare_base(atoms, scores, code)["cal_scores"]
    off = _bare_compressor(rep_decay="off")._prepare_base(atoms, scores, code)["cal_scores"]
    assert prose == off == list(scores)  # code: no decay in "prose" mode
    assert allm != list(scores)  # default still decays repeated code tokens
    text = "The committee met on Tuesday and the committee agreed. " * 12
    pa, ps = _synthetic_scored(text)
    assert not is_technical_text(pa, text)
    assert _bare_compressor(rep_decay="prose")._prepare_base(pa, ps, text)["cal_scores"] == \
        _bare_compressor()._prepare_base(pa, ps, text)["cal_scores"]  # prose unchanged
def test_t17_hot_path_helpers_match_reference():
    """prepare_detok/detok_prepared and the precomputed negation plan are byte-identical to the plain paths."""
    import random

    from ctxprune.atoms import detok_prepared, prepare_detok
    from ctxprune.compress import _neg_anchor_plan, _pin_negation_anchors

    texts = [
        "The job did not fail.\n\nIt returned status: 500 but x != y and a<=b, never a > b\nlevel=high  mode = low",
        "  leading ws\n\n\nand  gaps\t\ttabs n't can't zero.\n",
        "no\nretry: yes\n\n\nunless x == 3 or foo:bar baz=qux",
    ]
    rng = random.Random(17)
    for text in texts:
        atoms = atomize(text)
        pre = prepare_detok(atoms)
        n = len(atoms)
        plan, nl = _neg_anchor_plan(atoms), ["\n" in a.ws for a in atoms]
        for _ in range(300):
            keep = [rng.random() < 0.4 for _ in range(n)]
            assert detok_prepared(pre, keep) == detok(atoms, keep, preserve_para=True)
            costs = [1.0] * n
            k1, p1, k2, p2 = list(keep), list(keep), list(keep), list(keep)
            asc = list(range(n))
            _pin_negation_anchors(atoms, costs, k1, p1, asc, 8.0)
            _pin_negation_anchors(atoms, costs, k2, p2, asc, 8.0, plan, nl)
            assert (k1, p1) == (k2, p2)


def test_mcp_help_and_unknown_args():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "ctxprune", "mcp", "--help"], input="", capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and "read_compact" in r.stdout
    r = subprocess.run([sys.executable, "-m", "ctxprune", "mcp", "--bogus"], input="", capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "unrecognized" in r.stderr
