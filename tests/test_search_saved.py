import os, stat, time, random
import pytest
from ctxprune import search_saved as ss


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr("ctxprune.glue.state_dir", lambda: str(tmp_path / "state"))


def _mk(tmp_path, n=2000):
    r = random.Random(1)
    lines = [f"2026-11-19 INFO worker{r.randint(1,9)} processed item {i} ok" for i in range(n)]
    lines[1234] = "ERROR kaboom_unique failed to open socket"
    p = tmp_path / "out.log"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def test_search_context_and_perms(tmp_path):
    p = _mk(tmp_path)
    b = ss.search_saved(p, "kaboom_unique", 2)
    assert len(b) == 1 and b[0]["lines"] == [1235]
    assert b[0]["start"] == 1233 and b[0]["end"] == 1237
    assert "1235: ERROR kaboom_unique" in b[0]["snippet"]
    assert stat.S_IMODE(os.stat(ss.db_path()).st_mode) == 0o600
    assert ss.search_saved(p, "nonexistentword") == []


def test_ttl_and_reindex(tmp_path):
    p = _mk(tmp_path)
    assert ss.index_file(p) > 0 and ss.index_file(p) == 0
    db = ss._connect()
    assert ss.prune_expired(db, time.time() + 8 * 86400) == 1
    assert db.execute("SELECT count(*) FROM lines").fetchone()[0] == 0
    db.close()


def test_bad_query(tmp_path):
    with pytest.raises(ValueError):
        ss.search_saved(_mk(tmp_path), 'a AND (')


def test_ingest_latency(tmp_path):
    p = _mk(tmp_path, 1700)  # ~100 KB
    t = time.perf_counter(); ss.index_file(p); ms = (time.perf_counter() - t) * 1000
    print(os.path.getsize(p), ms)
    assert ms < 20 * 5  # generous in CI; kill-gate number measured separately
