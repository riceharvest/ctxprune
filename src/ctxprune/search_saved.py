"""Local, expiring full-text index for saved/spilled tool output (stdlib SQLite FTS5, no deps).

One FTS row per line, so a hit is a real line number and `context_lines` of context come from the file itself.
The database lives in the ctxprune state dir (dir 0700, file 0600); entries not refreshed for 7 days are dropped.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time

TTL_SECONDS = 7 * 86400
MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_LINE_CHARS = 400
_PRUNE_EVERY = 3600


def db_path() -> str:
    from .glue import state_dir
    return os.path.join(state_dir(), "saved-outputs.sqlite3")


def _connect(prune: bool = True) -> sqlite3.Connection:
    path = db_path()
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fresh = not os.path.exists(path)
    if fresh:  # create 0600 from the start (no window with a umask-default mode)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    db = sqlite3.connect(path, timeout=2)
    db.execute("PRAGMA synchronous=OFF")  # derived cache: losing it on power cut is fine, fsync costs ~20-50 ms
    db.execute("PRAGMA journal_mode=MEMORY")
    db.executescript(
        "CREATE TABLE IF NOT EXISTS saved(handle TEXT PRIMARY KEY, path TEXT NOT NULL, mtime REAL NOT NULL,"
        " size INTEGER NOT NULL, indexed REAL NOT NULL);"
        "CREATE VIRTUAL TABLE IF NOT EXISTS lines USING fts5(handle UNINDEXED, n UNINDEXED, body, tokenize='unicode61');")
    if prune:
        prune_expired(db)
    return db


def prune_expired(db: sqlite3.Connection, now: float | None = None) -> int:
    cutoff = (now or time.time()) - TTL_SECONDS
    old = [r[0] for r in db.execute("SELECT handle FROM saved WHERE indexed < ?", (cutoff,))]
    if old:
        db.executemany("DELETE FROM lines WHERE handle=?", [(h,) for h in old])
        db.execute("DELETE FROM saved WHERE indexed < ?", (cutoff,))
        db.commit()
    return len(old)


def index_file(handle: str, path: str | None = None) -> int:
    """Index (or refresh) a saved text file under `handle`. Idempotent; returns number of lines indexed (0 = unchanged)."""
    handle = str(handle)
    path = os.path.realpath(os.path.expanduser(path or handle))
    st = os.stat(path)
    if st.st_size > MAX_INDEX_BYTES:
        raise ValueError(f"file is over {MAX_INDEX_BYTES} bytes, not indexed")
    db = _connect()
    try:
        old = db.execute("SELECT mtime,size FROM saved WHERE handle=?", (handle,)).fetchone()
        now = time.time()
        if old == (st.st_mtime, st.st_size):
            db.execute("UPDATE saved SET indexed=? WHERE handle=?", (now, handle))
            db.commit()
            return 0
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "surrogateescape")
        rows = [(handle, i, ln) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]
        db.execute("DELETE FROM lines WHERE handle=?", (handle,))
        db.executemany("INSERT INTO lines(handle,n,body) VALUES (?,?,?)", rows)
        db.execute("INSERT OR REPLACE INTO saved VALUES (?,?,?,?,?)", (handle, path, st.st_mtime, st.st_size, now))
        db.commit()
        return len(rows)
    finally:
        db.close()


def forget(handle: str) -> None:
    db = _connect(prune=False)
    try:
        db.execute("DELETE FROM lines WHERE handle=?", (str(handle),))
        db.execute("DELETE FROM saved WHERE handle=?", (str(handle),))
        db.commit()
    finally:
        db.close()


def _fmt(n: int, s: str, hit: bool) -> str:
    s = s.rstrip("\r\n")
    if len(s) > MAX_LINE_CHARS:
        s = s[:MAX_LINE_CHARS] + f"… [+{len(s) - MAX_LINE_CHARS} chars]"
    return f"{n}{':' if hit else '-'} {s}"


def search_saved(handle: str, query: str, context_lines: int = 3, limit: int = 20) -> list[dict]:
    """Search one saved output. Returns blocks: {"start","end","lines","snippet"}; hit lines are `N: text`, context `N- text`."""
    if not str(query).strip():
        raise ValueError("query must not be empty")
    context_lines = int(context_lines)
    if not 0 <= context_lines <= 20:
        raise ValueError("context_lines must be between 0 and 20")
    limit = max(1, min(int(limit), 100))
    handle = str(handle)
    index_file(handle)
    db = _connect(prune=False)
    try:
        try:
            hits = [r[0] for r in db.execute(
                "SELECT n FROM lines WHERE lines MATCH ? AND handle=? ORDER BY bm25(lines), n LIMIT ?",
                (query, handle, limit))]
        except sqlite3.OperationalError as e:
            raise ValueError(f"invalid FTS5 query ({e}); quote phrases with \"...\"") from e
        path = db.execute("SELECT path FROM saved WHERE handle=?", (handle,)).fetchone()[0]
    finally:
        db.close()
    if not hits:
        return []
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "surrogateescape").splitlines()
    hitset = set(hits)
    blocks: list[list[int]] = []
    for n in sorted(hitset):
        a, b = max(1, n - context_lines), min(len(text), n + context_lines)
        if blocks and a <= blocks[-1][1] + 1:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            blocks.append([a, b])
    out = []
    for a, b in blocks:
        out.append({"start": a, "end": b, "lines": sorted(hitset & set(range(a, b + 1))),
                    "snippet": "\n".join(_fmt(i, text[i - 1], i in hitset) for i in range(a, b + 1))})
    return out
