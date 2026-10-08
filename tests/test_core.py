import asyncio
import json

import pytest

from ctxprune.align import align
from ctxprune.atoms import atomize, detok, is_protected
from ctxprune.chunking import chunk_text, split_of
from ctxprune.idcheck import survival
from ctxprune.teacher import Cache, MockTeacher, cache_key, clean_output, run_teacher

LOG = (
    "2026-10-08T09:14:06.002Z ERROR [worker-3] job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a "
    "failed after 3 retries: ECONNREFUSED 10.0.4.17:5432\n"
    "  File \"/srv/app/lib/api_client.py\", line 88, in fetch_page\n"
)


@pytest.mark.parametrize("text", [
    LOG,
    '{"id": "ord_8f3a2c91", "amount": 14950, "vat": 0.21}',
    "  leading and trailing  \n\n",
    "日本語のテキスト、句読点。Ünïcödé — dash",
    "diff --git a/x.ts b/x.ts\n@@ -42,7 +42,9 @@\n-  a\n+  b\n",
    "",
])
def test_atomize_roundtrip(text):
    atoms = atomize(text)
    assert "".join(a.ws + a.text for a in atoms) == text


def test_identifiers_are_single_atoms():
    texts = {a.text for a in atomize(LOG)}
    for ident in ["5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a", "10.0.4.17:5432", "/srv/app/lib/api_client.py",
                  "2026-10-08T09:14:06.002Z", "ECONNREFUSED", "fetch_page"]:
        assert ident in texts, ident


def test_cjk_is_per_character():
    atoms = [a.text for a in atomize("支持循環播放，v1.22.3-x64")]
    assert atoms[:4] == ["支", "持", "循", "環"]
    assert "v1.22.3-x64" in atoms
    src = atomize("在軟件界面查看推薦的歌單")
    assert align(src, "查看推薦歌單").variation_rate == 0


def test_is_protected():
    for yes in ["ord_8f3a2c91", "3.14.2", "/tmp", "--format", "ECONNREFUSED", "computeTotal", "429", "a71b0d4"]:
        assert is_protected(yes), yes
    for no in ["the", "failed", ",", "{", "Error", "WARRANTIES", "THE"]:
        assert not is_protected(no), no


def test_detok_keeps_spacing_and_lines():
    atoms = atomize("vatRate = 0.21\nreturn subtotal * (1 + vatRate);")
    keep = [a.text not in {"=", "*", "(", ")", "+", ";"} for a in atoms]
    out = detok(atoms, keep)
    assert out == "vatRate 0.21\nreturn subtotal 1 vatRate"


def test_align_deletion_only_is_exact():
    src = atomize("The job 5f2d8e7a failed after 3 retries on host db-1.")
    a = align(src, "job 5f2d8e7a failed 3 retries host db-1")
    assert a.variation_rate == 0
    assert detok(src, a.keep) == "job 5f2d8e7a failed 3 retries host db-1"
    assert a.protected_total == a.protected_kept


def test_align_casefold_and_variation():
    src = atomize("Connection Refused by server")
    a = align(src, "connection refused server (summarised)")
    assert a.keep[:2] == [True, True]
    assert a.variation_rate > 0  # "(summarised)" is not in the source


def test_align_prefers_order():
    src = atomize("a b a b")
    a = align(src, "b a")
    assert sum(a.keep) == 2 and a.variation_rate == 0


def test_survival_counts_mangled_ids_as_lost():
    r = survival('{"vat": 0.21, "id": "ord_8f3a2c91"}', "vat 0. 21 id ord _ 8f3a2c91")
    assert r["lost"] == 2 and r["json"] is False
    r = survival('{"vat": 0.21}', '{"vat": 0.21}')
    assert r["lost"] == 0 and r["json"] is True


def _count(texts):  # whitespace "tokenizer" for tests
    return [max(1, len(t.split())) for t in texts]


def test_chunking_respects_budget_and_preserves_text():
    text = "\n".join(f"line {i} " + "word " * (i % 7) for i in range(200))
    chunks = chunk_text(text, _count, max_tokens=40, min_tokens=1)
    assert all(_count([c])[0] <= 40 for c in chunks)
    assert "".join(chunks) == text


def test_chunking_splits_overlong_line():
    text = "x " * 300
    chunks = chunk_text(text, _count, max_tokens=50, min_tokens=1)
    assert len(chunks) >= 6 and all(_count([c])[0] <= 50 for c in chunks)


def test_split_is_stable_per_group():
    assert split_of("swe:repo-a") == split_of("swe:repo-a")
    counts = {s: 0 for s in ("train", "val", "test")}
    for i in range(5000):
        counts[split_of(f"g{i}")] += 1
    assert 50 < counts["test"] < 150 and 50 < counts["val"] < 150


def test_clean_output_strips_wrappers():
    assert clean_output("<think>hmm</think>\n<<<\nkeep this\n>>>") == "keep this"
    assert clean_output("```\nkeep\n```") == "keep"


def test_teacher_cache_resume(tmp_path):
    chunks = [{"id": "c1", "text": "the job failed\nthe job failed\nretry 3"}]
    cache = Cache(tmp_path / "c.jsonl")
    out, err = asyncio.run(run_teacher(MockTeacher(), chunks, cache))
    cache.close()
    assert not err and out["c1"] == "job failed\nretry 3"
    cache2 = Cache(tmp_path / "c.jsonl")
    assert cache2.get(cache_key(MockTeacher.model, chunks[0]["text"])) == "job failed\nretry 3"
    cache2.close()
    lines = (tmp_path / "c.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["chunk_id"] == "c1"


def test_tokens_spanning_atoms_cover_all_of_them():
    from ctxprune.tokenlabels import atom_spans, char_to_atom, token_atom_sets, token_atoms, token_labels

    atoms = atomize("f(x): ok")  # f ( x ) : ok
    full, spans = atom_spans(atoms)
    owner = char_to_atom(spans, len(full))
    offsets = [(0, 2), (2, 3), (3, 5), (5, 8)]  # "f(", "x", "):", " ok"
    sets = token_atom_sets(offsets, owner)
    assert sets == [[0, 1], [2], [3, 4], [5]]
    assert token_atoms(offsets, owner) == [0, 2, 3, 5]
    keep = [True, False, True, False, True, True]
    assert token_labels(token_atoms(offsets, owner), keep) == [1, 1, 0, 1]
