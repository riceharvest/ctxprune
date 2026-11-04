"""Cases for tool-specific output rewrites (#54, #229, #603, #618, #803, #1165, #1336) and for PreToolUse rewrites of the
command or its environment (#76, #143, #217, #227, #486, #610, #788, #1227, #1228).

Part 1 holds the output transforms (roundtrip or equivalent). Part 2 holds one small rewrite engine for Bash commands and a
sim per PreToolUse idea. The engine's contract, checked on every PreToolUse case:
  - a command that does not match is returned byte-identical (also when it cannot be parsed: fail open),
  - a match keeps every original token, in order (checked with shlex, not with the engine's own scanner),
  - it is idempotent, and it never overrides an env var or flag the user already set,
  - the rewritten command is run in a real bash against shims: exit status, argv and pipe behaviour are unchanged.
"""
import hashlib
import ipaddress
import itertools
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field

from harness import Fixture, case, tokens


def _lcg(seed: int):
    """Tiny deterministic generator so fixtures are stable without importing random."""
    s = seed
    while True:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        yield s >> 8


# ===============================================================================================================
# PART 1: output transforms
# ===============================================================================================================

# ---------------------------------------------------------------------------------------------------------------
# #54 Group linter diagnostics by (rule, message): the message once, then the location list.
# Equivalent, not roundtrip: inside one run of consecutive diagnostic lines the order across different messages is gone.

_S54 = "§"
_DIAG54 = re.compile(r"([^\s:][^:\n]*):(\d+)(?::(\d+))?: (\S.*)", re.ASCII)


def group_diagnostics(text: str) -> str:
    lines = text.split("\n")
    if any(l.startswith(_S54) for l in lines):
        return text  # the sigil is in-band: refuse rather than risk a misread
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _DIAG54.fullmatch(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and _DIAG54.fullmatch(lines[j]):
            j += 1
        parsed = []
        for l in lines[i:j]:
            m = _DIAG54.fullmatch(l)
            loc = f"{m.group(1)}:{m.group(2)}" + (f":{m.group(3)}" if m.group(3) is not None else "")
            parsed.append((l, loc, m.group(4)))
        members: dict[str, list[str]] = {}
        for _l, loc, rest in parsed:
            members.setdefault(rest, []).append(loc)
        done: set[str] = set()
        for l, _loc, rest in parsed:
            if len(members[rest]) == 1:
                out.append(l)  # a lone diagnostic costs more as a group, so it stays as written
            elif rest not in done:
                done.add(rest)
                out.append(f"{_S54}{len(members[rest])} {rest}")
                out += [f"  {loc}" for loc in members[rest]]
        i = j
    enc = "\n".join(out)
    return enc if len(enc) < len(text) else text


def _diag_split(line: str):
    """Independent parser (index scanning instead of a regex): (location, message) or None."""
    ws = " \t\r\n\f\v"
    if not line or line[0] in ws or line[0] == ":":
        return None
    i = line.find(":")
    if i < 0:
        return None
    k = i + 1
    digits = 0
    while k < len(line) and "0" <= line[k] <= "9":
        k += 1
        digits += 1
    if not digits:
        return None
    if k + 1 < len(line) and line[k] == ":" and "0" <= line[k + 1] <= "9":
        k += 1
        while k < len(line) and "0" <= line[k] <= "9":
            k += 1
    if line[k : k + 2] != ": " or k + 2 >= len(line) or line[k + 2] in ws:
        return None
    return line[:k], line[k + 2 :]


def _view54(text: str):
    """Events: raw lines verbatim, and one Counter of (location, message) per maximal run of diagnostics."""
    lines = text.split("\n")
    events: list = []
    run: Counter | None = None
    i = 0
    while i < len(lines):
        l = lines[i]
        n = None
        if l.startswith(_S54):
            head, sp, rest = l[len(_S54):].partition(" ")
            if sp and head.isascii() and head.isdigit() and int(head) >= 2:
                n = int(head)
        if n is not None and i + n < len(lines) and all(x.startswith("  ") for x in lines[i + 1 : i + 1 + n]):
            run = run if run is not None else Counter()
            for x in lines[i + 1 : i + 1 + n]:
                run[(x[2:], rest)] += 1
            i += 1 + n
            continue
        d = _diag_split(l)
        if d:
            run = run if run is not None else Counter()
            run[d] += 1
        else:
            if run is not None:
                events.append(run)
                run = None
            events.append(l)
        i += 1
    if run is not None:
        events.append(run)
    return events


def _ruff_log() -> str:
    g = _lcg(54)
    files = ["src/app/models.py", "src/app/api.py", "src/app/utils/io.py", "src/app/utils/text.py", "tests/test_api.py", "tests/conftest.py"]
    msgs = [("F401", "[*] `os` imported but unused"), ("F401", "[*] `typing.Any` imported but unused"), ("E501", "Line too long (104 > 100)"),
            ("F841", "Local variable `result` is assigned to but never used"), ("B008", "Do not perform function call `Depends` in argument defaults")]
    rows = []
    for f in files:
        for _ in range(7):
            code, msg = msgs[next(g) % len(msgs)]
            rows.append((f, 1 + next(g) % 300, 1 + next(g) % 40, code, msg))
    rows.sort(key=lambda r: (r[0], r[1]))
    return "".join(f"{f}:{l}:{c}: {code} {msg}\n" for f, l, c, code, msg in rows) + "Found 42 errors.\n[*] 19 fixable with the `--fix` option.\n"


def _mypy_log() -> str:
    g = _lcg(55)
    msgs = ['error: Incompatible return value type (got "None", expected "int")  [return-value]',
            'error: Item "None" of "Optional[str]" has no attribute "lower"  [union-attr]',
            'error: Argument 1 to "load" has incompatible type "str"; expected "Path"  [arg-type]',
            'note: See https://mypy.readthedocs.io/en/stable/running_mypy.html#missing-imports']
    out = []
    for f in ("pkg/core.py", "pkg/cli.py", "pkg/store.py"):
        for _ in range(8):
            out.append(f"{f}:{1 + next(g) % 400}: {msgs[next(g) % 3]}\n")
        out.append(f"{f}:{1 + next(g) % 400}: {msgs[3]}\n")
    return "".join(out) + "Found 24 errors in 3 files (checked 31 source files)\n"


UNIQUE54 = "a.py:1:1: F401 `os` imported but unused\nb.py:4:9: E501 Line too long (121 > 100)\nc.py:2:5: F841 `x` is assigned to but never used\n"
INTERLEAVED54 = "== a.py ==\na.py:1:1: W1 same\na.py:2:1: W1 same\ncontext line\na.py:9:1: W1 same\n  indented explanation\na.py:10:1: W1 same\n"

case(
    54,
    claim="Within each run of consecutive 'file:line[:col]: message' lines, a message that occurs twice or more is written once as '§N message' followed by N location lines; singletons and every other line stay as written.",
    loses="the order of diagnostics inside one run (locations are listed by message, in first-seen order)",
    encode=group_diagnostics,
    equivalent=lambda a, b: _view54(a) == _view54(b),
    fixtures=[
        Fixture("ruff concise, repeated rules", _ruff_log(), True),
        Fixture("mypy with notes and summary", _mypy_log(), True),
        Fixture("every diagnostic unique", UNIQUE54, False),
        Fixture("clean run", "All checks passed!\n", False),
        Fixture("grep -n output is not a diagnostic", "src/a.py:12:def foo():\nsrc/b.py:40:def foo():\n", False),
        Fixture("context lines split the runs", INTERLEAVED54, None),
        Fixture("input already containing the sigil", "§2 W1 same\n  a.py:1:1\n  a.py:2:1\nb.py:3:3: W1 same\nb.py:4:3: W1 same\n", None),
        Fixture("CRLF output", _ruff_log().replace("\n", "\r\n"), None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #229 Symbolic mode strings to octal (ls -l, tar -tv). Exact roundtrip; the decoder uses CPython's stat.filemode, so the
# mapping the encoder computes by hand is checked against an independent implementation.

_RAW229 = "§raw\n"
_SYM = re.compile(r"([-dlcbps])([-r][-w][-xsS][-r][-w][-xsS][-r][-w][-xtT])([.+@]?)(?= +(?:\d+ |[^\s/]+/[^\s/]+ ))")
_ENC = re.compile(r"([-dlcbps])([1-7][0-7]{3}|[0-7]{3})([.+@]?)(?= +(?:\d+ |[^\s/]+/[^\s/]+ ))")
_IFMT = {"-": stat.S_IFREG, "d": stat.S_IFDIR, "l": stat.S_IFLNK, "c": stat.S_IFCHR, "b": stat.S_IFBLK, "p": stat.S_IFIFO, "s": stat.S_IFSOCK}


def modes_to_octal(text: str) -> str:
    lines = text.split("\n")
    if text.startswith(_RAW229) or any(_ENC.match(l) for l in lines):
        return _RAW229 + text  # text that already looks like the encoding: say so, decode will strip this
    out = []
    for l in lines:
        m = _SYM.match(l)
        if not m:
            out.append(l)
            continue
        perm, special, digits = m.group(2), 0, ""
        for k in range(3):
            r, w, x = perm[3 * k : 3 * k + 3]
            digits += str((4 if r != "-" else 0) + (2 if w != "-" else 0) + (1 if x in "xst" else 0))
            if k < 2 and x in "sS":
                special |= 4 if k == 0 else 2
            if k == 2 and x in "tT":
                special |= 1
        out.append(m.group(1) + (str(special) if special else "") + digits + m.group(3) + l[m.end():])
    enc = "\n".join(out)
    return enc if len(enc) < len(text) else text


def octal_to_modes(text: str) -> str:
    if text.startswith(_RAW229):
        return text[len(_RAW229):]
    out = []
    for l in text.split("\n"):
        m = _ENC.match(l)
        if not m:
            out.append(l)
            continue
        sym = stat.filemode(_IFMT[m.group(1)] | int(m.group(2), 8))
        out.append(sym + m.group(3) + l[m.end():])
    return "\n".join(out)


def _ls_usr_bin() -> str:
    g = _lcg(229)
    rows = ["-rwxr-xr-x ls", "-rwsr-xr-x su", "-rwsr-xr-x passwd", "-rwxr-sr-x wall", "lrwxrwxrwx bzcat -> bzip2", "-rwxr-xr-x bzip2", "-rw-r--r-- README",
            "-rwx------ cron-job", "drwxrwxrwt tmp", "drwxr-xr-x share", "-rwSr--r-- odd-setuid", "-rw-r--r-T odd-sticky", "-rwxr-xr-x+ acl-file",
            "-rw-r--r--. selinux-file", "crw-rw-rw- null", "brw-rw---- sda", "prw------- fifo"]
    out = ["total 4812"]
    for i in range(60):
        mode, name = rows[next(g) % len(rows)].split(" ", 1)
        out.append(f"{mode} {1 + next(g) % 3} root root {next(g) % 2000000:>8} Mar {1 + next(g) % 28:>2} 09:{next(g) % 60:02d} {name}")
    return "\n".join(out) + "\n"


def _tar_tv() -> str:
    return "".join(f"{m} user/staff {s:>7} 2024-05-0{d} 10:{d}1 proj/{n}\n" for d, (m, s, n) in enumerate(
        [("-rw-r--r--", 1204, "README.md"), ("-rwxr-xr-x", 88231, "bin/run"), ("drwxr-xr-x", 0, "src/"), ("-rw-r--r--", 5521, "src/main.c"), ("-rwsr-xr-x", 12, "bin/helper"), ("lrwxrwxrwx", 0, "latest -> run")], 1))


def _all_modes() -> str:
    return "".join(f"{stat.filemode(stat.S_IFREG | m)} 1 u g 0 f\n" for m in range(4096))


case(
    229,
    claim="The type character plus nine permission letters of an ls -l or tar -tv line become the type plus 3 octal digits (4 when setuid, setgid or sticky is set), keeping an ACL/SELinux/xattr suffix; decode restores the exact original.",
    encode=modes_to_octal,
    decode=octal_to_modes,
    fixtures=[
        Fixture("ls -lA of a bin directory", _ls_usr_bin(), True),
        Fixture("tar -tv listing", _tar_tv(), True),
        Fixture("every one of the 4096 mode bit combinations", _all_modes(), True),
        Fixture("plain names, no modes", "bzip2\nls\nREADME\n", False),
        Fixture("prose that mentions modes", "chmod u=rw,go=r file sets -rw-r--r-- on it\n", False),
        Fixture("text that looks like encoder output", "-644 1 root root 12 Mar 1 10:00 a\nd755 2 root root 4096 Mar 1 10:00 b\n-rw-r--r-- 1 root root 5 Mar 1 10:00 c\n", None),
        Fixture("input that starts with the escape", "§raw\n-rw-r--r-- 1 root root 5 Mar 1 10:00 c\n", None),
        Fixture("unknown type character left alone", "?rwxr-xr-x 1 root root 5 Mar 1 10:00 c\n-rwxr-xr-x 1 root root 5 Mar 1 10:00 d\n", None),
    ],
    min_gain=0.08,
    idempotent=False,  # an encoded listing looks like encoder output, so re-encoding must escape it (and grow) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #603 Jest console stanzas to one line, identical neighbours folded with a count.

def _stanza603(lines: list[str], i: int):
    """(indent, kind, file, line, message, next index) for a single-line-message console stanza at lines[i], else None."""
    m = re.fullmatch(r"( +)console\.(log|info|warn|error|debug)", lines[i])
    if not m:
        return None
    ind, kind = m.groups()
    k = i + 1
    if k < len(lines) and lines[k] == "":
        k += 1  # some versions print a blank line after the label
    if k + 2 >= len(lines):
        return None
    msg, blank, at = lines[k], lines[k + 1], lines[k + 2]
    if not msg.startswith(ind + "  ") or not msg[len(ind) + 2 :] or msg[len(ind) + 2].isspace() or blank != "" or not at.startswith(ind + "    at "):
        return None
    body = at[len(ind) + 7 :]
    if body.endswith(")"):
        inner = body[:-1]
        if " (" not in inner:
            return None
        part = inner.rpartition(" (")[2]
    else:
        part = body
    pieces = part.split(":")
    if len(pieces) != 3 or not all(p.isascii() and p.isdigit() for p in pieces[1:]):
        return None
    f, ln = pieces[0], pieces[1]
    if not f or f[0].isspace() or any(c in f for c in "()"):
        return None
    return ind, kind, f, ln, msg[len(ind) + 2 :], k + 3


def jest_oneline(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        st = _stanza603(lines, i)
        if st is None:
            out.append(lines[i])
            i += 1
            continue
        n, j = 1, st[5]
        while j + 1 < len(lines) and lines[j] == "":
            nxt = _stanza603(lines, j + 1)
            if nxt is None or nxt[:5] != st[:5]:
                break
            n, j = n + 1, nxt[5]
        ind, kind, f, ln, msg, _ = st
        out.append(f"{ind}console.{kind}{'×%d' % n if n > 1 else ''} {f}:{ln}: {msg}")
        i = j
    enc = "\n".join(out)
    return enc if len(enc) < len(text) else text


_F603 = r"[^\s():][^():\n]*"
_STZ603 = re.compile(r"(?P<i> +)console\.(?P<k>log|info|warn|error|debug)\n(?:\n)?(?P=i)  (?P<m>\S[^\n]*)\n\n(?P=i)    at (?P<at>[^\n]*)(?:\n|\Z)")
_HEAD603 = re.compile(r"(?P<i> +)console\.(?P<k>log|info|warn|error|debug)(?:×(?P<n>[0-9]+))? (?P<f>" + _F603 + r"):(?P<l>[0-9]+): (?P<m>[^\n]*)(?:\n|\Z)")
_AT603 = re.compile(r"(?:[^\n]*? \((?P<f>" + _F603 + r"):(?P<l>[0-9]+):[0-9]+\)|(?P<g>" + _F603 + r"):(?P<n>[0-9]+):[0-9]+)")


def _view603(text: str):
    """Scan line by line: console events expanded to one per call, everything else verbatim, blank-only gaps between events dropped."""
    toks: list = []
    pos = raw_from = 0
    while pos < len(text):
        evs, end = None, 0
        m = _STZ603.match(text, pos)
        at = _AT603.fullmatch(m.group("at")) if m else None
        if at:
            evs = [("ev", m.group("i"), m.group("k"), at.group("f") or at.group("g"), at.group("l") or at.group("n"), m.group("m"))]
            end = m.end()
        else:
            m = _HEAD603.match(text, pos)
            if m:
                evs = [("ev", m.group("i"), m.group("k"), m.group("f"), m.group("l"), m.group("m"))] * int(m.group("n") or 1)
                end = m.end()
        if evs is None:
            nl = text.find("\n", pos)
            pos = len(text) if nl < 0 else nl + 1
            continue
        if raw_from < pos:
            toks.append(("raw", text[raw_from:pos]))
        toks += evs
        pos = raw_from = end
    if raw_from < len(text):
        toks.append(("raw", text[raw_from:]))
    return [t for k, t in enumerate(toks) if not (t[0] == "raw" and set(t[1]) == {"\n"} and 0 < k < len(toks) - 1 and toks[k - 1][0] == "ev" and toks[k + 1][0] == "ev")]


def _jest_log() -> str:
    g = _lcg(603)
    sites = [("src/cart/cart.test.ts", 14, "log"), ("src/cart/cart.test.ts", 31, "warn"), ("src/api/client.test.ts", 8, "log"), ("src/api/client.test.ts", 52, "error"), ("src/util/format.test.ts", 5, "info")]
    msgs = ["cart total 1999", "retrying request 1", "retrying request 2", "debug: token refreshed", "price recalculated", "ok"]
    out = [" PASS  src/cart/cart.test.ts", "  ● Console", ""]
    for n in range(40):
        f, ln, kind = sites[next(g) % len(sites)]
        m = msgs[next(g) % len(msgs)]
        reps = 1 + (next(g) % 4 == 0) * 3
        for _ in range(reps):
            out += [f"    console.{kind}", f"      {m}", "", f"        at Object.<anonymous> ({f}:{ln}:{11 + n % 5})", ""]
    out += ["Test Suites: 3 passed, 3 total", "Tests:       41 passed, 41 total"]
    return "\n".join(out) + "\n"


MULTI603 = "  console.log\n    {\n      id: 1,\n      name: 'a'\n    }\n\n      at Object.<anonymous> (src/a.test.ts:9:11)\n\n  console.log\n    line one\n\n    line three\n\n      at Object.<anonymous> (src/a.test.ts:10:11)\n"
HEADLIKE603 = "  console.log src/a.test.ts:3: hi\n  console.log×3 src/a.test.ts:4: again\n  console.log\n    x\n\n      at Object.<anonymous> (C:\\proj\\a.test.ts:5:1)\n"
ODD603 = "  console.log\n    value is (x3) and ×2 and a: b\n\n      at Object.<anonymous> (src/a.test.ts:9:11)\n\n  console.log\n    value is (x3) and ×2 and a: b\n\n      at src/a.test.ts:9:30\n"

case(
    603,
    claim="A Jest console stanza (label, one message line, blank line, 'at ... file:line:col') becomes '<indent>console.<kind> file:line: message'; identical neighbouring stanzas fold into 'console.<kind>×N ...'; multi-line messages and anything else stay verbatim.",
    loses="the function name and the column in the 'at' line, and the blank lines between folded stanzas",
    encode=jest_oneline,
    equivalent=lambda a, b: _view603(a) == _view603(b),
    fixtures=[
        Fixture("jest run with 100 debug prints", _jest_log(), True),
        Fixture("jest run without console output", " PASS  src/a.test.ts\n  cart\n    ✓ adds an item (3 ms)\n\nTests: 1 passed, 1 total\n", False),
        Fixture("multi-line messages stay verbatim", MULTI603, False),
        Fixture("lines that look like the output, windows path", HEADLIKE603, None),
        Fixture("message with count-like text, anonymous at-line", ODD603, None),
    ],
    min_gain=0.25,
)

# ---------------------------------------------------------------------------------------------------------------
# #618 Canonicalise zero-padded hex words (>= 8 digits) and fully written IPv6 addresses (RFC 5952 form).

_HEX618 = re.compile(r"(?<![\w.])0([xX])([0-9a-fA-F]{8,})(?!\w)")
_V6_618 = re.compile(r"(?<![\w:.%])(?=[0-9A-Fa-f:.]*:)[0-9A-Fa-f:.]++(?!\w)")


def _hex_sub(m: re.Match) -> str:
    digits = m.group(2)
    short = digits.lstrip("0") or "0"
    return f"0{m.group(1)}{short}" if len(short) < len(digits) else m.group(0)


def _v6_sub(m: re.Match) -> str:
    tok, tail = m.group(0), ""
    while tok.endswith("."):
        tok, tail = tok[:-1], tail + "."
    try:
        addr = ipaddress.IPv6Address(tok)
    except ValueError:
        return m.group(0)
    groups = tok.split(":")
    padded = any(len(g) == 4 and g[0] == "0" for g in groups) or ("::" not in tok and sum(1 for g in groups if g == "0") >= 2)
    new = addr.compressed
    return new + tail if padded and tok.lower() != new and len(new) < len(tok) else m.group(0)


def canon_numbers(text: str) -> str:
    out = _V6_618.sub(_v6_sub, _HEX618.sub(_hex_sub, text))
    return out if len(out) < len(text) else text


def _equiv618(a: str, b: str) -> bool:
    """Tokenise both sides into words and delimiters; every differing word must denote the same number or address."""
    word = re.compile(r"[\w:.%]+|[^\w:.%]+")
    wa, wb = word.findall(a), word.findall(b)
    if len(wa) != len(wb):
        return False
    for x, y in zip(wa, wb):
        if x == y:
            continue
        xs, ys = x.rstrip("."), y.rstrip(".")
        if len(x) - len(xs) != len(y) - len(ys) or len(y) >= len(x):
            return False
        if xs[:2].lower() == "0x":
            if ys[:2] != xs[:2]:
                return False
            try:
                if int(xs, 16) != int(ys, 16):
                    return False
            except ValueError:
                return False
            continue
        xa, _, xz = xs.partition("%")
        ya, _, yz = ys.partition("%")
        try:
            if xz != yz or ipaddress.IPv6Address(xa) != ipaddress.IPv6Address(ya):
                return False
        except ValueError:
            return False
    return True


def _regs() -> str:
    names = "rax rbx rcx rdx rsi rdi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 rip rflags cs fs gs".split()
    g = _lcg(618)
    rows = [f"  {n:>7} = 0x{(next(g) & 0xFFFF) << (16 * (i % 3)) if i % 4 else next(g) % 7:016x}" for i, n in enumerate(names)]
    return "General Purpose Registers:\n" + "\n".join(rows) + "\nframe: Frame { ip: 0x00007ff6a1b2c3d4, sp: 0x000000c3a6f7e8a0, flags: 0X00000000000000AB }\n"


def _v6_log() -> str:
    return "".join(
        f"accepted connection from {a} port {40000 + i}\n  route via {b}/64 dev eth0\n"
        for i, (a, b) in enumerate([("2001:0db8:85a3:0000:0000:8a2e:0370:7334", "fe80:0000:0000:0000:0204:61ff:fe9d:f156"),
                                    ("2001:0DB8:0000:0000:0000:0000:0000:0001", "fe80:0000:0000:0000:0204:61ff:fe9d:f157"),
                                    ("0000:0000:0000:0000:0000:0000:0000:0001", "2001:0db8:0000:0042:0000:8a2e:0370:7334")] * 3))


HEXDUMP618 = "00000000  7f 45 4c 46 02 01 01 00  00 00 00 00 00 00 00 00  |.ELF............|\n00000010  03 00 3e 00 01 00 00 00  40 10 00 00 00 00 00 00  |..>.....@.......|\n"
LOOKALIKE618 = "MAC 00:1a:2b:ff:fe:3c:4d:5e at 12:30:45; Add::add and dead::beef; 0x1F 0x00ff 0x0000_0001 v0x00000001 0x00000001x; 00:00:5e:ff:fe:00:53:af ::1 and 2001:db8::1/64\n"
EDGE618 = ("[2001:0db8:0000:0000:0000:0000:0000:0001]:443 ::ffff:192.0.2.1 0:0:0:0:0:ffff:192.0.2.1 fe80:0000:0000:0000:0204:61ff:fe9d:f156%eth0 "
           "ended at 2001:0db8:0:0:0:0:0:1. then 0x0000000000000000 0x00000000DEADBEEF ::0001 1:0:0:0:0:0:0:1 2001:db8:0:1:1:1:1:1 0:0:0:1:0:0:0:1\n")

case(
    618,
    claim="Hex words of 8+ digits with leading zeros lose the zeros (0x0000000000000001 -> 0x1), and fully written IPv6 addresses become their RFC 5952 compressed form; every value is equal, hexdump columns, MACs, times and already-short forms are not touched.",
    loses="zero padding of hex words, hex-digit case inside a rewritten IPv6 address, and column alignment that relied on fixed width",
    encode=canon_numbers,
    equivalent=_equiv618,
    fixtures=[
        Fixture("lldb registers and Rust frame", _regs(), True),
        Fixture("expanded IPv6 in a connection log", _v6_log(), True),
        Fixture("hexdump columns", HEXDUMP618, False),
        Fixture("look-alikes: MAC, time, Add::add, short hex", LOOKALIKE618, False),
        Fixture("brackets, port, zone, IPv4-mapped, trailing period", EDGE618, None),
    ],
    min_gain=0.25,
)

# ---------------------------------------------------------------------------------------------------------------
# #803 Java frames: drop 'Outer.java' from '(Outer.java:212)' when it is the class's own file. Byte-exact roundtrip.

_RAW = "§raw\n"
_JFRAME = re.compile(r"([ \t]*at )([^\s()]+)\(([^\s():]+)\.java:(\d+)\)")
_JSHORT = re.compile(r"([ \t]*at )([^\s()]+)\(:(\d+)\)")


def _jclass_stem(qual: str) -> str:
    """File stem derived from 'loader/module/pkg.Outer$Inner.method': text after the last '/', class = before the last '.', Outer = before the first '$'."""
    cls = qual.rsplit("/", 1)[-1].rpartition(".")[0]
    return cls.rpartition(".")[2].split("$", 1)[0]


def drop_java_file(text: str) -> str:
    lines = text.split("\n")
    if text.startswith(_RAW) or any(_JSHORT.match(l) for l in lines):
        return _RAW + text  # a literal '(:N)' frame would be expanded by decode: escape the whole text
    out = []
    for l in lines:
        m = _JFRAME.match(l)
        stem = _jclass_stem(m.group(2)) if m else ""
        if m and stem and m.group(3) == stem:
            l = f"{m.group(1)}{m.group(2)}(:{m.group(4)})" + l[m.end():]
        out.append(l)
    enc = "\n".join(out)
    return enc if len(enc) < len(text) else text


def restore_java_file(text: str) -> str:
    if text.startswith(_RAW):
        return text[len(_RAW):]
    out = []
    for l in text.split("\n"):
        m = _JSHORT.match(l)
        if m:
            l = f"{m.group(1)}{m.group(2)}({_jclass_stem(m.group(2))}.java:{m.group(3)})" + l[m.end():]
        out.append(l)
    return "\n".join(out)


JAVA803 = """java.lang.IllegalStateException: card declined
\tat com.acme.billing.InvoiceService.charge(InvoiceService.java:212)
\tat com.acme.billing.InvoiceService.lambda$retry$0(InvoiceService.java:187)
\tat com.acme.billing.InvoiceService$Retry.run(InvoiceService.java:190)
\tat com.acme.billing.InvoiceService$1.call(InvoiceService.java:77)
\tat com.acme.billing.Helper.assist(InvoiceService.java:12)
\tat com.acme.web.OrderController.submit(OrderController.java:64)
\tat jdk.internal.reflect.GeneratedMethodAccessor12.invoke(Unknown Source)
\tat java.base/jdk.internal.reflect.DelegatingMethodAccessorImpl.invoke(DelegatingMethodAccessorImpl.java:43)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:568)
\tat org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:205) ~[spring-web-6.0.9.jar:6.0.9]
\tat org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:1072)
\tat com.acme.util.FooKt.helper(Foo.kt:9)
\tat com.acme.Main.main(Main.java)
Caused by: java.io.IOException: timeout
\tat com.acme.net.HttpClient.send(HttpClient.java:301)
\t... 11 more
"""

case(
    803,
    claim="'at pkg.Outer$Inner.m(Outer.java:N)' becomes 'at pkg.Outer$Inner.m(:N)' exactly when the file is the outer class's own .java file; Kotlin files, mismatched names and frames without a line number are kept, and decode restores the exact original.",
    encode=drop_java_file,
    decode=restore_java_file,
    fixtures=[
        Fixture("spring style trace with inner classes and lambdas", JAVA803 * 3, True),
        Fixture("python traceback is not a java trace", 'Traceback (most recent call last):\n  File "a.py", line 3, in f\n    g()\nValueError: x\n', False),
        Fixture("only kotlin and mismatched frames", "\tat com.acme.FooKt.helper(Foo.kt:9)\n\tat com.acme.Helper.assist(Other.java:12)\n\tat com.acme.A.b(Unknown Source)\n", False),
        Fixture("literal (:N) already in the input", "\tat com.acme.A.b(:12)\n\tat com.acme.A.c(A.java:13)\n", None),
        Fixture("input that starts with the escape", "§raw\n\tat com.acme.A.c(A.java:13)\n", None),
        Fixture("default package, CRLF", "\tat Main.main(Main.java:5)\r\n\tat Main$Task.run(Main.java:9)\r\n", None),
    ],
    min_gain=0.1,
    idempotent=False,  # re-encoding an encoded trace finds '(:N)' and must escape it (and grow) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #1165 Python traceback frames to '@path:line function'. Byte-exact roundtrip; '@' is the in-band marker.

_PYF = re.compile(r'([ \t]*)File "(.*)", line (\d+), in (\S+)(\r?)')
_PYE = re.compile(r"([ \t]*)@(.*):(\d+) (\S+)(\r?)")


def compact_py_frames(text: str) -> str:
    lines = text.split("\n")
    if text.startswith(_RAW) or any(_PYE.fullmatch(l) for l in lines):
        return _RAW + text
    out = []
    for l in lines:
        m = _PYF.fullmatch(l)
        out.append(f"{m.group(1)}@{m.group(2)}:{m.group(3)} {m.group(4)}{m.group(5)}" if m else l)
    enc = "\n".join(out)
    return enc if len(enc) < len(text) else text


def expand_py_frames(text: str) -> str:
    if text.startswith(_RAW):
        return text[len(_RAW):]
    out = []
    for l in text.split("\n"):
        m = _PYE.fullmatch(l)
        out.append(f'{m.group(1)}File "{m.group(2)}", line {m.group(3)}, in {m.group(4)}{m.group(5)}' if m else l)
    return "\n".join(out)


PY1165 = '''Traceback (most recent call last):
  File "/srv/app/main.py", line 41, in <module>
    run(config)
  File "/srv/app/runner.py", line 212, in run
    return charge(order, retries=3)
  File "/srv/app/billing/charge.py", line 88, in charge
    raise CardDeclined(order.id)
billing.errors.CardDeclined: 4417

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/lib/python3.12/runpy.py", line 198, in _run_module_as_main
    return _run_code(code, main_globals, None,
  File "<frozen runpy>", line 88, in _run_code
  File "<string>", line 1, in <lambda>
  File "/srv/app/odd path/with, comma.py", line 7, in <listcomp>
    [f(x) for x in xs]
RuntimeError: gave up
'''

case(
    1165,
    claim="Each traceback line '<indent>File \"path\", line N, in func' becomes '<indent>@path:N func'; every other line, including SyntaxError locations without 'in', is untouched, and decode restores the exact original (text that already contains '@path:N func' lines is escaped).",
    encode=compact_py_frames,
    decode=expand_py_frames,
    fixtures=[
        Fixture("chained traceback with odd paths", PY1165, True),
        Fixture("syntax error location has no 'in'", '  File "bad.py", line 3\n    x = (\n        ^\nSyntaxError: \'(\' was never closed\n', False),
        Fixture("node stack trace", "Error: boom\n    at run (/srv/app/index.js:12:5)\n    at main (/srv/app/index.js:30:1)\n", False),
        Fixture("a line that looks like a compact frame", "  @app/main.py:12 run\n" + PY1165, None),
        Fixture("path containing quotes and spaces, CRLF", '  File "a", line 1, in f", line 2, in g\r\n  File "x y:3 z.py", line 4, in w\r\n', None),
        Fixture("input that starts with the escape", "§raw\n" + PY1165, None),
    ],
    min_gain=0.05,
    idempotent=False,  # an encoded traceback contains '@path:N func' lines, so re-encoding must escape (and grow) by design
)

# ---------------------------------------------------------------------------------------------------------------
# #1336 Strip the leading './' from find, grep -r and tar listings (only when every line is such a path).

def strip_dot_slash(text: str) -> str:
    if "\0" in text:
        return text
    lines = text.split("\n")
    body = [l for l in lines if l]
    if not body or not all(l == "." or l == "--" or l.startswith("./") for l in body):
        return text
    out = [l[2:] if l.startswith("./") and len(l) > 2 and l[2] not in "-/~" else l for l in lines]
    return "\n".join(out)


def _equiv1336(a: str, b: str) -> bool:
    la, lb = a.split("\n"), b.split("\n")
    if len(la) != len(lb):
        return False
    listing = all(l in (".", "--") or l.startswith("./") for l in la if l)
    path = lambda s: s.split(":", 1)[0]  # noqa: E731  (grep -n prints 'path:line:text')
    for x, y in zip(la, lb):
        if x == y:
            continue
        # a changed line must be a listing line that lost exactly its './', and still name the same path
        if not (listing and x.startswith("./") and y and y[0] not in "-/~" and x[2:] == y and posixpath.normpath(path(x)) == posixpath.normpath(path(y))):
            return False
    return True


def _find_out() -> str:
    dirs = ["src", "src/components", "src/lib/net", "tests", "docs/guide", "scripts"]
    names = ["index.ts", "Button.tsx", "client.py", "test_client.py", "README.md", "build.sh", "util.go", ".eslintrc.json"]
    g = _lcg(1336)
    return "".join(f"./{dirs[next(g) % len(dirs)]}/{names[next(g) % len(names)]}\n" for _ in range(50)).replace("./src/lib/net/README.md", "./README.md") + ".\n"


def _grep_out() -> str:
    return "".join(f"./src/m{i}.py:{3 + i}:import os\n" for i in range(14)) + "./Makefile:9:\t./build.sh --all\n--\n./docs/a.md-4-see ./build.sh\n"


def _tar_out() -> str:
    return "./\n./src/\n" + "".join(f"./src/f{i}.c\n./include/h{i}.h\n" for i in range(15))


NEG1336 = "total 8\ndrwxr-xr-x 2 u g 4096 Mar 1 10:00 .\n-rw-r--r-- 1 u g 12 Mar 1 10:00 notes.txt\n"
EDGE1336 = "./-rf\n./a b/ spaced name.txt\n.//abs\n./~tmp\n./\n./ünï/日本.txt\n.\n./.hidden\n./x\r\n"

case(
    1336,
    claim="When every line of a find, grep -r or tar -t output is a path starting with './' (or '.' or '--'), the leading './' is removed from each line; names starting with '-', '/' or '~', a bare './', and any output that is not wholly such a listing stay untouched.",
    loses="the './' prefix, so './x' and 'x' printed by the same command become indistinguishable",
    encode=strip_dot_slash,
    equivalent=_equiv1336,
    fixtures=[
        Fixture("find . listing", _find_out(), True),
        Fixture("grep -rn with './' inside the matched text", _grep_out(), True),
        Fixture("tar -tf listing", _tar_out(), True),
        Fixture("ls -la is not a path listing", NEG1336, False),
        Fixture("names that need their ./ and odd entries", EDGE1336, None),
    ],
    min_gain=0.05,
)

# @@PART2@@
