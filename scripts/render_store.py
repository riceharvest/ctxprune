#!/usr/bin/env python3
"""First-render store: what ctxprune sent for an output that is already in a model's context (prompt-cache safety).

Why: a cached prompt prefix is re-read ~85 times, and ANY byte change in an output that is already in context misses
the cache for everything after it. Entry points that re-render history on every request (ctxprune_proxy, the
request-time dedup of the Hermes / Pi / OpenCode plugins) must therefore keep serving the bytes they sent first,
whatever changed since: an upgrade (new marker formats, rules, thresholds, note text), a preset / dedup / paths
toggle, a model that came back, a pruned registry. The store maps a key that identifies "this output at this place
in this conversation" (a chain hash over the conversation's earlier outputs, built by the caller) to the bytes sent,
plus the version that first rendered it (metadata only, never part of the key).

  s = RenderStore(path)                 path None / "" / ":memory:" -> memory only (bounded LRU)
  hit, value = s.get(table, key)        value None = "sent unchanged" (stored as SQL NULL)
  value = s.put(table, key, value, ver) first stored wins (INSERT OR IGNORE, also across processes): returns the
                                        winner; replace=True overwrites (explicit full re-render)

sqlite (stdlib), WAL, one file shared by threads and processes; reads go through a bounded in-memory LRU
(CTXPRUNE_STORE_MEM_MB, default 64). Bounded on disk: entries carry `seen` (unix s, refreshed at most once per
TOUCH_S per entry, so ~85 hits per output don't each write); evict() drops entries unseen for MAX_DAYS, then, while
the live data is over MAX_MB, the least recently seen ones, but never one seen within LIVE_DAYS (a live session's
history must keep its bytes; provider prompt caches live minutes to 24 h, so an entry unseen for days can be
re-rendered at no cache cost: that conversation's next request is a full cache write anyway). Runs at open and
then every EVICT_S. Env: CTXPRUNE_STORE_MAX_MB (512), CTXPRUNE_STORE_LIVE_DAYS (3), CTXPRUNE_STORE_MAX_DAYS (30).
Fails open: any sqlite error -> memory only from then on (byte stability then lasts for this process only).

Legacy (proxy-3 and earlier): `--cache-file X.jsonl` was a JSONL of {"k": content key, "t": text}. import_jsonl()
streams it into a table once (first line per key wins, as before); the JSONL itself is left in place.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict

TOUCH_S = 3600
EVICT_S = 600
TABLES = ("pin", "content")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


class RenderStore:
    def __init__(self, path: str | None = None, max_mb: float | None = None, live_days: float | None = None,
                 max_days: float | None = None, mem_mb: float | None = None):
        self.path = None if path in (None, "", ":memory:") else os.path.expanduser(path)
        self.max_bytes = int((max_mb if max_mb is not None else _env_f("CTXPRUNE_STORE_MAX_MB", 512)) * (1 << 20))
        self.live_s = (live_days if live_days is not None else _env_f("CTXPRUNE_STORE_LIVE_DAYS", 3)) * 86400
        self.max_age_s = (max_days if max_days is not None else _env_f("CTXPRUNE_STORE_MAX_DAYS", 30)) * 86400
        self.mem_max = int((mem_mb if mem_mb is not None else _env_f("CTXPRUNE_STORE_MEM_MB", 64)) * (1 << 20))
        self.lock = threading.RLock()
        self.mem: OrderedDict = OrderedDict()   # (table, key) -> [value, size, touched_at]
        self.mem_size = 0
        self.db = None
        self.error: str | None = None
        self._last_evict = 0.0
        self.evicted = 0
        if self.path:
            try:
                self._open()
            except Exception as e:  # noqa: BLE001 - fail open: memory only
                self._fail(e)

    # ---------------------------------------------------------------- sqlite
    def _open(self) -> None:
        import sqlite3
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, mode=0o700, exist_ok=True)
        # private like every other state file (audit M2): it holds tool output. Created 0600 before sqlite opens it,
        # whatever the caller's umask (Hermes in-process, Pi / OpenCode from Node); -wal / -shm inherit this mode
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600))
        self.db = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        for t in TABLES:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {t} (k TEXT PRIMARY KEY, t TEXT, ver TEXT, seen INTEGER)")
            self.db.execute(f"CREATE INDEX IF NOT EXISTS {t}_seen ON {t}(seen)")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.evict()

    def _fail(self, e: Exception) -> None:
        self.error = repr(e)[:200]
        try:
            if self.db is not None:
                self.db.close()
        except Exception:  # noqa: BLE001
            pass
        self.db = None

    # ---------------------------------------------------------------- memory LRU
    def _mem_put(self, mk, value, touched: float) -> None:
        size = len(value) if isinstance(value, str) else 0
        size += len(mk[1]) + 64
        old = self.mem.pop(mk, None)
        if old is not None:
            self.mem_size -= old[1]
        self.mem[mk] = [value, size, touched]
        self.mem_size += size
        while self.mem_size > self.mem_max and len(self.mem) > 1:
            _, v = self.mem.popitem(last=False)
            self.mem_size -= v[1]

    # ---------------------------------------------------------------- API
    def get(self, table: str, key: str):
        """-> (hit, value); value None = the output was sent unchanged."""
        now = time.time()
        with self.lock:
            mk = (table, key)
            e = self.mem.get(mk)
            if e is not None:
                self.mem.move_to_end(mk)
                if now - e[2] > TOUCH_S and self.db is not None:
                    e[2] = now
                    try:
                        self.db.execute(f"UPDATE {table} SET seen=? WHERE k=?", (int(now), key))
                    except Exception as ex:  # noqa: BLE001
                        self._fail(ex)
                return True, e[0]
            if self.db is None:
                return False, None
            try:
                row = self.db.execute(f"SELECT t, seen FROM {table} WHERE k=?", (key,)).fetchone()
                if row is None:
                    return False, None
                if now - (row[1] or 0) > TOUCH_S:
                    self.db.execute(f"UPDATE {table} SET seen=? WHERE k=?", (int(now), key))
                self._mem_put(mk, row[0], now)
                self._maybe_evict(now)
                return True, row[0]
            except Exception as ex:  # noqa: BLE001
                self._fail(ex)
                return False, None

    def put(self, table: str, key: str, value: str | None, ver: str = "", replace: bool = False):
        """Store value unless the key is already there (first wins, across threads and processes); -> the stored
        value. replace=True overwrites (explicit re-render)."""
        now = time.time()
        with self.lock:
            mk = (table, key)
            if not replace and mk in self.mem:
                self.mem.move_to_end(mk)
                return self.mem[mk][0]
            if self.db is not None:
                try:
                    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
                    self.db.execute(f"{verb} INTO {table} (k, t, ver, seen) VALUES (?, ?, ?, ?)",
                                    (key, value, ver, int(now)))
                    if not replace:
                        row = self.db.execute(f"SELECT t FROM {table} WHERE k=?", (key,)).fetchone()
                        if row is not None:
                            value = row[0]
                except Exception as ex:  # noqa: BLE001
                    self._fail(ex)
            self._mem_put(mk, value, now)
            self._maybe_evict(now)
            return value

    def count(self, table: str) -> int:
        with self.lock:
            if self.db is None:
                return sum(1 for t, _ in self.mem if t == table)
            try:
                return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception as ex:  # noqa: BLE001
                self._fail(ex)
                return 0

    def _maybe_evict(self, now: float) -> None:
        if now - self._last_evict > EVICT_S:
            self.evict(now)

    def _live_bytes(self) -> int:
        ps = self.db.execute("PRAGMA page_size").fetchone()[0]
        pc = self.db.execute("PRAGMA page_count").fetchone()[0]
        fl = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pc - fl) * ps

    def evict(self, now: float | None = None) -> int:
        """Drop entries unseen for max_age, then least-recently-seen ones while over max_bytes, never one seen within
        live_s. -> rows deleted. (Freed pages are reused by later writes; the file does not shrink.)"""
        now = time.time() if now is None else now
        self._last_evict = now
        if self.db is None:
            return 0
        n = 0
        try:
            with self.lock:
                for t in TABLES:
                    n += self.db.execute(f"DELETE FROM {t} WHERE seen < ?", (int(now - self.max_age_s),)).rowcount
                floor = int(now - self.live_s)
                while self._live_bytes() > self.max_bytes:
                    got = 0
                    for t in TABLES:
                        got += self.db.execute(
                            f"DELETE FROM {t} WHERE k IN (SELECT k FROM {t} WHERE seen < ? ORDER BY seen LIMIT 500)",
                            (floor,)).rowcount
                    if not got:
                        break   # everything left is live: over the cap rather than break a live session
                    n += got
                if n:
                    self.mem.clear()
                    self.mem_size = 0
        except Exception as ex:  # noqa: BLE001
            self._fail(ex)
        self.evicted += n
        return n

    def import_jsonl(self, path: str, table: str = "content", ver: str = "legacy") -> int:
        """Stream a legacy proxy cache JSONL ({"k", "t"} per line, first line per key wins) into `table`, once per
        file (meta records it). -> lines imported (0 when already done, missing, or memory only)."""
        path = os.path.expanduser(path)
        if self.db is None or not os.path.isfile(path):
            return 0
        mark = "import:" + os.path.abspath(path)
        n = 0
        try:
            with self.lock:
                if self.db.execute("SELECT 1 FROM meta WHERE k=?", (mark,)).fetchone():
                    return 0
                now = int(time.time())
                self.db.execute("BEGIN")
                with open(path, encoding="utf-8", errors="surrogateescape") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                            k, t = r["k"], r["t"]
                        except (ValueError, KeyError, TypeError):
                            continue   # torn last line etc.
                        if not isinstance(k, str) or not (t is None or isinstance(t, str)):
                            continue
                        n += self.db.execute(f"INSERT OR IGNORE INTO {table} (k, t, ver, seen) VALUES (?, ?, ?, ?)",
                                             (k, t, ver, now)).rowcount
                self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (mark, str(n)))
                self.db.execute("COMMIT")
        except Exception as ex:  # noqa: BLE001
            try:
                self.db.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            self._fail(ex)
        return n

    def close(self) -> None:
        with self.lock:
            if self.db is not None:
                try:
                    self.db.close()
                except Exception:  # noqa: BLE001
                    pass
                self.db = None


def store_path_for(cache_file: str) -> str:
    """--cache-file X: X itself when it is (or will be) sqlite; a legacy `X.jsonl` -> `X.sqlite` next to it."""
    p = os.path.expanduser(cache_file)
    if p.endswith(".jsonl"):
        return p[:-len(".jsonl")] + ".sqlite"
    try:
        with open(p, "rb") as f:
            head = f.read(16)
        if head and head != b"SQLite format 3\x00":
            return p + ".sqlite"   # an existing non-sqlite file (a JSONL without the suffix)
    except OSError:
        pass
    return p


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "s.sqlite")
        old = os.umask(0o022)
        try:
            a, b = RenderStore(db), RenderStore(db)
        finally:
            os.umask(old)
        if os.name != "nt":
            assert os.stat(db).st_mode & 0o077 == 0, oct(os.stat(db).st_mode)
        assert a.get("pin", "k") == (False, None)
        assert a.put("pin", "k", "one", "v1") == "one"
        assert b.put("pin", "k", "two", "v2") == "one"           # first wins across connections / processes
        assert b.put("pin", "n", None) is None and b.get("pin", "n") == (True, None)
        if os.name != "nt":
            for x in os.listdir(td):
                assert os.stat(os.path.join(td, x)).st_mode & 0o077 == 0, (x, oct(os.stat(os.path.join(td, x)).st_mode))
        assert RenderStore(db).get("pin", "k") == (True, "one")   # survives a restart
        assert a.put("pin", "k", "three", replace=True) == "three"
        j = os.path.join(td, "c.jsonl")
        with open(j, "w") as f:
            f.write('{"k": "x", "t": "first"}\n{"k": "x", "t": "second"}\n{"k": "y", "t": null}\n{"k": "torn')
        c = RenderStore(store_path_for(j))
        assert c.import_jsonl(j) == 2 and c.import_jsonl(j) == 0
        assert c.get("content", "x") == (True, "first") and c.get("content", "y") == (True, None)
        # eviction: over the cap, old entries go, live ones stay
        e = RenderStore(os.path.join(td, "e.sqlite"), max_mb=0.05, live_days=1)
        for i in range(60):
            e.put("pin", f"old{i}", "x" * 2000)
        e.db.execute("UPDATE pin SET seen = seen - 5 * 86400")
        e.put("pin", "live", "y" * 2000)
        assert e.evict() > 0 and e.get("pin", "live") == (True, "y" * 2000), e.count("pin")
        m = RenderStore(None, mem_mb=0.01)
        for i in range(100):
            m.put("pin", str(i), "z" * 1000)
        assert m.mem_size <= 0.01 * (1 << 20) and m.get("pin", "99")[0] and not m.get("pin", "0")[0]
    print("render_store self-test OK")
