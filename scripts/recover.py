#!/usr/bin/env python3
"""Recoverable lossy compression (user 2026-10-17: "lossy will always be less trustful than lossless"): nothing the
model drops is lost from the system. The full text the model would have seen (stage-0 output, itself lossless) is
saved to a content-addressed file, every gap marker states its exact line range in that file, and a footer names the
file in a tool-neutral way (any file reader works; sed is only the example):

    [… lines 120–180 omitted …]
    ...
    [ctxprune: kept 212 of 840 lines; omitted ranges are lines A–B of /home/u/.cache/ctxprune/out/3fa2c1d09b7e.txt
     (e.g. sed -n 'A,Bp' <file>)]

Line numbers count "\\n" only (what sed, head, an editor or a Read tool count; splitlines() would also split at \\f,
U+2028, ... and drop the \\r of CRLF), and the file holds the stage-0 text byte for byte.

Fail closed (audit M1): `recoverable()` returns None when the gaps can't be mapped onto the source or the file can't
be written; the caller then keeps the stage-0 text (never a lossy result without its recovery file).
Deterministic in (text, kept lines): same input -> same bytes (prompt-cache safe). Files older than KEEP_DAYS (and
stale *.tmp) are pruned opportunistically. State is private: 0700 dir, 0600 files (audit M2). Pure stdlib.

API:
  recoverable(src, keep=<kept "\\n"-line indices> | compressed=<text with relative markers>, state=None) -> str | None
  keep_from_pieces(src, piece_keep) -> kept "\\n"-line indices   (piece_keep: indices into src.splitlines(), what
                                                                  v2_server returns with want_keep)
  make_recoverable(src, compressed, state=None) -> str          (legacy: compressed unchanged on failure)
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import time
from pathlib import Path

GAP = re.compile(r"^\[… (\d+) lines? omitted …\]$")
KEEP_DAYS = 7
TMP_MAX_AGE = 3600


def state_dir(d: str | None = None) -> Path:
    return Path(d or os.environ.get("CTXPRUNE_STATE") or os.path.expanduser("~/.cache/ctxprune")) / "out"


# ---------------------------------------------------------------- private state helpers (also used by dedup / core)
def private_dir(d: str | os.PathLike) -> Path:
    """mkdir -p with 0700 on the leaf (also tightens an existing leaf we own). Parents keep the umask."""
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    try:
        if p.stat().st_uid == os.getuid() and (p.stat().st_mode & 0o077):
            os.chmod(p, 0o700)
    except (OSError, AttributeError):
        pass
    return p


def private_open(path: str | os.PathLike, mode: str = "w", encoding: str = "utf-8", errors: str | None = None):
    """open() for state files: created 0600 and an existing file is tightened to 0600 (no process umask change: this
    runs inside the proxy / Hermes too)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC)
    fd = os.open(path, flags | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    if "b" in mode:
        return os.fdopen(fd, mode)
    return os.fdopen(fd, mode, encoding=encoding, errors=errors)


def _prune(d: Path) -> None:
    try:
        if (d / ".pruned").exists() and time.time() - (d / ".pruned").stat().st_mtime < 3600:
            return
        now = time.time()
        cut = now - KEEP_DAYS * 86400
        for p in d.glob("*.txt"):
            if p.stat().st_mtime < cut:
                p.unlink(missing_ok=True)
        for p in d.glob("*.tmp"):          # a crash between mkstemp and replace
            if p.stat().st_mtime < now - TMP_MAX_AGE:
                p.unlink(missing_ok=True)
        with private_open(d / ".pruned"):
            pass
    except OSError:
        pass


# ---------------------------------------------------------------- line model
def _nl_lines(src: str) -> tuple[list[str], bool]:
    """src split at "\\n" only; (lines, trailing_newline). A final "\\n" ends the last line, it doesn't open one."""
    lines = src.split("\n")
    trail = len(lines) > 1 and lines[-1] == ""
    return (lines[:-1] if trail else lines), trail


def keep_from_pieces(src: str, piece_keep) -> list[int] | None:
    """Map kept indices into src.splitlines() (the line model's units) onto "\\n"-lines: a "\\n"-line is kept when
    any of its pieces is (kept text stays byte-exact; at worst a few chars over the model's budget). None if the
    indices don't fit src."""
    pieces = src.splitlines(keepends=True)
    idx = sorted(set(piece_keep))
    if idx and (idx[0] < 0 or idx[-1] >= len(pieces)):
        return None
    owner, nl = [], 0
    for pc in pieces:
        owner.append(nl)
        nl += pc.count("\n")
    return sorted({owner[i] for i in idx})


def _from_compressed(src: str, compressed: str) -> list[int] | None:
    """Legacy text API: kept "\\n"-line indices from a compressed text with relative `[… N lines omitted …]` markers.
    Only when the model's line units are the "\\n" lines (no \\r, \\f, U+2028, ... in src) and the output is an
    in-order subsequence of src with markers; else None."""
    lines, _ = _nl_lines(src)
    if src.splitlines() != lines:
        return None
    keep, i = [], 0
    for ln in compressed.split("\n"):
        m = GAP.match(ln)
        if m:
            n = int(m.group(1))
            if n < 1 or i + n > len(lines):
                return None
            i += n
        else:
            if i >= len(lines) or lines[i] != ln:
                return None
            keep.append(i)
            i += 1
    return keep if i == len(lines) else None


def annotate(src_lines: list[str], out_lines: list[str]) -> list[str] | None:
    """Replace `[… N lines omitted …]` markers by absolute 1-based ranges of src; None if out is not an in-order
    subsequence of src with markers. (Kept for callers of the old API; the pipeline renders from kept indices.)"""
    res, i = [], 0
    for ln in out_lines:
        m = GAP.match(ln)
        if m:
            n = int(m.group(1))
            if n < 1 or i + n > len(src_lines):
                return None
            a, b = i + 1, i + n
            res.append(_gap(a, b))
            i += n
        else:
            if i >= len(src_lines) or src_lines[i] != ln:
                return None
            res.append(ln)
            i += 1
    return res if i == len(src_lines) else None


def _gap(a: int, b: int) -> str:
    return f"[… line {a} omitted …]" if a == b else f"[… lines {a}–{b} omitted …]"


def render(lines: list[str], keep: list[int]) -> list[str]:
    """Kept lines byte-exact, every run of dropped lines one absolute-range marker."""
    ks = set(keep)
    out, gap0 = [], None
    for i, ln in enumerate(lines):
        if i in ks:
            if gap0 is not None:
                out.append(_gap(gap0 + 1, i))
                gap0 = None
            out.append(ln)
        elif gap0 is None:
            gap0 = i
    if gap0 is not None:
        out.append(_gap(gap0 + 1, len(lines)))
    return out


def footer(kept: int, total: int, p: Path) -> str:
    return (f"[ctxprune: kept {kept} of {total} lines; omitted ranges are lines A–B of {p} "
            f"(e.g. sed -n 'A,Bp' {shlex.quote(str(p))})]")


def _write(d: Path, src: str) -> Path:
    """Content-addressed <sha12>.txt. An existing file is reused only if its bytes are src (L4); new content goes
    through a unique temp file + os.replace (no fixed .tmp name to race on or follow)."""
    data = src.encode("utf-8", "surrogateescape")
    h = hashlib.sha256(data).hexdigest()[:12]
    p = d / f"{h}.txt"
    try:
        if not p.is_symlink() and p.is_file() and p.stat().st_size == len(data) and p.read_bytes() == data:
            os.utime(p)
            return p
    except OSError:
        pass
    import tempfile   # lazily: ~5 ms of import in every hook process otherwise
    fd, tmp = tempfile.mkstemp(prefix=f"{h}.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def recoverable(src: str, keep=None, compressed: str | None = None, state: str | None = None) -> str | None:
    """Lossy output with absolute gap ranges + footer, built from kept "\\n"-line indices (or, legacy, from a
    compressed text). Nothing dropped -> src unchanged. None = can't make it recoverable (caller keeps src)."""
    lines, _ = _nl_lines(src)
    if keep is None:
        if compressed is None:
            return None
        if compressed == src:
            return src
        keep = _from_compressed(src, compressed)
        if keep is None:
            return None
    keep = sorted(set(keep))
    if keep and (keep[0] < 0 or keep[-1] >= len(lines)):
        return None
    if len(keep) == len(lines):
        return src
    out = render(lines, keep)
    try:
        d = private_dir(state_dir(state))
        p = _write(d, src)
        _prune(d)
    except OSError:
        return None
    out.append(footer(len(keep), len(lines), p))
    return "\n".join(out)


def make_recoverable(src: str, compressed: str, state: str | None = None) -> str:
    """Legacy API: compressed unchanged when it can't be made recoverable (the pipeline uses recoverable())."""
    if compressed == src or "omitted …]" not in compressed:
        return compressed
    r = recoverable(src, compressed=compressed, state=state)
    return compressed if r is None else r


def _self_test() -> None:
    import tempfile
    src = "\n".join(f"line {i}" for i in range(1, 21))
    comp = "\n".join(["line 1", "line 2", "[… 5 lines omitted …]", "line 8", "[… 12 lines omitted …]"])
    with tempfile.TemporaryDirectory() as td:
        out = make_recoverable(src, comp, td)
        assert "[… lines 3–7 omitted …]" in out and "[… lines 9–20 omitted …]" in out, out
        f = re.search(r"lines A–B of (\S+) ", out).group(1)
        lines = open(f).read().splitlines()
        assert lines[2:7] == [f"line {i}" for i in range(3, 8)]          # the range points at the right lines
        assert make_recoverable(src, comp, td) == out                    # deterministic
        assert recoverable(src, keep=[0, 1, 7], state=td) == out         # index path == legacy text path
        assert make_recoverable(src, src, td) == src                     # nothing dropped: untouched
        bad = comp.replace("line 8", "line 9")                           # not a subsequence: untouched / None
        assert make_recoverable(src, bad, td) == bad and recoverable(src, compressed=bad, state=td) is None
        one = "\n".join(["line 1", "[… 1 line omitted …]"] + [f"line {i}" for i in range(3, 21)])
        assert "[… line 2 omitted …]" in make_recoverable(src, one, td)
        # "\n"-only numbering: \f, U+2028 and CRLF stay inside their line; sed counts the same lines
        src2 = "a\r\nb\x0cB\nc C\nd\ne\n"
        out2 = recoverable(src2, keep=keep_from_pieces(src2, [0, 5]), state=td)   # pieces a, d
        assert out2.split("\n")[:4] == ["a\r", "[… lines 2–3 omitted …]", "d", "[… line 5 omitted …]"], out2
        f2 = re.search(r"lines A–B of (\S+) ", out2).group(1)
        assert open(f2, newline="").read() == src2 and oct(os.stat(f2).st_mode & 0o777) == "0o600"
        assert oct(os.stat(os.path.dirname(f2)).st_mode & 0o777) == "0o700"
        assert recoverable(src2, compressed="a\n[… 4 lines omitted …]", state=td) is None   # can't map: fail closed
        # L4: a tampered content-addressed file is rewritten, not trusted
        open(f, "w").write("tampered\n" * 30)
        assert make_recoverable(src, comp, td) == out and open(f).read() == src
        # unwritable state -> None (M1: caller keeps the stage-0 text)
        ro = os.path.join(td, "ro")
        os.makedirs(ro)
        open(os.path.join(ro, "out"), "w").close()                       # `out` is a file: mkdir fails
        assert recoverable(src, keep=[0], state=ro) is None and make_recoverable(src, comp, ro) == comp
    print("recover self-test OK")


if __name__ == "__main__":
    _self_test()
