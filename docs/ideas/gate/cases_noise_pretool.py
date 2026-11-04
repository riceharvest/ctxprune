"""Cases for tool-specific output rewrites (#54, #229, #603, #618, #803, #1165, #1336) and for PreToolUse rewrites of the
command or its environment (#76, #143, #217, #227, #486, #610, #788, #1227, #1228).

Part 1 holds the output transforms (roundtrip or equivalent). Part 2 holds one small rewrite engine for Bash commands and a
sim per PreToolUse idea. The engine's contract, checked on every PreToolUse case:
  - a command that does not match is returned byte-identical (also when it cannot be parsed: fail open),
  - a match keeps every original token, in order (checked with shlex, not with the engine's own scanner),
  - it is idempotent, and it never overrides an env var or flag the user already set,
  - the rewritten command is run in a real bash against shims: exit status, argv and pipe behaviour are unchanged.
"""
import codecs
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


def _exact_tokens() -> bool:
    import harness

    return harness.exact_tokens()


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
    min_gain=0.4,
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
        Fixture("older layout: blank line after the label, CRLF stanza left alone", "  console.log\n\n    hello\n\n      at Object.<anonymous> (src/a.test.ts:3:9)\n\n  console.log\r\n    hi\r\n\r\n      at x (a.js:1:1)\r\n", None),
    ],
    min_gain=0.5,
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
    min_gain=0.2,
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
    min_gain=0.12,
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
    min_gain=0.1,
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
    min_gain=0.06,
)


# ===============================================================================================================
# PART 2: PreToolUse rewrites of a Bash command or its environment
# ===============================================================================================================
#
# Hook contract used here (Claude Code PreToolUse, from memory of the docs; whether `updatedInput` is honoured for Bash cannot be
# verified offline, see the report): stdin is JSON {"tool_name", "tool_input": {...}, "cwd", ...}; stdout JSON
# {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {<the WHOLE tool_input with the new command>}}}.
# The hook never emits permissionDecision: a rewrite for quieter output must not also auto-approve the command.

_RESERVED = frozenset("if then elif else fi while until do done ! { } time".split())
_WRAP = frozenset("sudo nohup command exec nice builtin env".split())
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_OPS2 = ("&&", "||", ";;", "|&")


def _scan(cmd: str):
    """Tokens (kind, text, start, end) with kind 'w' (word, raw text) or 'op'. None for syntax this engine does not model."""
    toks = []
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c in " \t":
            i += 1
            continue
        if c == "\n":
            toks.append(("op", "\n", i, i + 1))
            i += 1
            continue
        if c == "#":
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        if c in ";|&()":
            op = cmd[i : i + 2] if cmd[i : i + 2] in _OPS2 else c
            toks.append(("op", op, i, i + len(op)))
            i += len(op)
            continue
        j = i
        while j < n:
            c = cmd[j]
            if c in " \t\n;|()":
                break
            if c == "&":
                if (j > i and cmd[j - 1] in "<>") or cmd[j + 1 : j + 2] == ">":
                    j += 1
                    continue
                break
            if c == "\\":
                j += 2
                continue
            if c == "'":
                k = cmd.find("'", j + 1)
                if k < 0:
                    return None
                j = k + 1
                continue
            if c == '"':
                k = j + 1
                while True:
                    if k >= n:
                        return None
                    d = cmd[k]
                    if d == "\\":
                        k += 2
                    elif d == "`" or (d == "$" and cmd[k + 1 : k + 2] == "("):
                        return None
                    elif d == '"':
                        break
                    else:
                        k += 1
                j = k + 1
                continue
            if c == "`" or (c == "$" and cmd[j + 1 : j + 2] == "(") or (c in "<>" and cmd[j + 1 : j + 2] == "(") or (c == "<" and cmd[j + 1 : j + 2] == "<"):
                return None
            j += 1
        toks.append(("w", cmd[i:j], i, j))
        i = j
    return toks


def _plain(raw: str):
    return raw if re.fullmatch(r"[A-Za-z0-9_./:=+,@%-]+", raw) else None


def _commands(cmd: str):
    """[(program basename or None, program token, argument tokens)] per simple command, or None if unparseable."""
    toks = _scan(cmd)
    if toks is None:
        return None
    segs, cur = [], []
    for t in toks:
        if t[0] == "op":
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    out = []
    for seg in segs:
        k, prog = 0, None
        while k < len(seg):
            raw = seg[k][1]
            if raw in _RESERVED or _ASSIGN.match(raw):
                k += 1
            elif raw in _WRAP:
                k += 1
                if k < len(seg) and seg[k][1].startswith("-"):
                    break  # wrapper with options (sudo -u x ...): do not guess
            else:
                p = _plain(raw)
                prog = posixpath.basename(p) if p else None
                break
        out.append((prog, seg[k] if k < len(seg) else None, seg[k + 1 :] if k < len(seg) else []))
    return out


@dataclass(frozen=True)
class FlagAdd:
    """Insert `add` right after the program word (or after its first argument when `subs` is given). Skipped when any argument equals
    or starts with one of `names` + '=' (the user chose a value), or when an argument is not plain text (cannot be sure)."""
    progs: frozenset
    add: tuple
    names: frozenset
    subs: frozenset | None = None


@dataclass(frozen=True)
class EnvRule:
    progs: frozenset
    vars: tuple  # ((name, value), ...)
    atomic: bool = False  # all or nothing: partial injection would be inconsistent
    blockers: tuple = ()  # names whose presence (set by the user) means "do not touch", though we do not inject them


@dataclass(frozen=True)
class Pack:
    env: tuple = ()
    flags: tuple = ()


@dataclass
class Plan:
    cmd: str
    env: dict = field(default_factory=dict)  # variables injected through the export prefix
    splices: list = field(default_factory=list)


def _ident_used(cmd: str, name: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", cmd) is not None


def _export_prefix(cmd: str, rules, env: dict) -> tuple[str, dict]:
    blocked = lambda k: k in env or _ident_used(cmd, k)  # noqa: E731  (an empty value counts as set)
    chosen: dict[str, str] = {}
    for r in rules:
        names = [k for k, _ in r.vars]
        if any(blocked(b) for b in r.blockers):
            continue
        if r.atomic and any(blocked(k) for k in names):
            continue
        for k, v in r.vars:
            if not blocked(k):
                chosen.setdefault(k, v)
    if not chosen:
        return "", {}
    return "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in chosen.items()) + "; ", chosen


def plan(cmd: str, pack: Pack, env: dict) -> Plan:
    """Rewrite plan for a Bash command. Total: anything unexpected returns the command unchanged."""
    try:
        if not cmd.strip() or "$?" in cmd or "$_" in cmd:  # the export prefix would reset $?
            return Plan(cmd)
        cmds = _commands(cmd)
        if cmds is None:
            return Plan(cmd)
        progs = {p for p, _t, _a in cmds if p}
        splices = []
        for prog, ptok, args in cmds:
            if prog is None or ptok is None:
                continue
            vals = [_plain(a[1]) for a in args]
            for fr in pack.flags:
                if prog not in fr.progs:
                    continue
                if fr.subs is not None:
                    if not args or vals[0] not in fr.subs:
                        continue
                    at = args[0][3]
                else:
                    at = ptok[3]
                if any(v is None for v in vals) or any(v == n or v.startswith(n + "=") for v in vals for n in fr.names):
                    continue
                splices.append((at, " " + " ".join(fr.add)))
        out = cmd
        for at, text in sorted(splices, reverse=True):
            out = out[:at] + text + out[at:]
        prefix, chosen = _export_prefix(cmd, [r for r in pack.env if progs & r.progs], env)
        return Plan(prefix + out, chosen, splices)
    except Exception:  # noqa: BLE001  fail open
        return Plan(cmd)


def rewrite(cmd: str, pack: Pack, env: dict) -> str:
    return plan(cmd, pack, env).cmd


def pretool_hook(stdin_text: str, rewriter) -> tuple[str, int]:
    """(stdout, exit code). `rewriter(command, hook_input) -> command`. Empty stdout means: no change."""
    try:
        data = json.loads(stdin_text)
        if data.get("tool_name") != "Bash":
            return "", 0
        ti = data["tool_input"]
        cmd = ti["command"]
        new = rewriter(cmd, data) if isinstance(cmd, str) else cmd
        if not isinstance(new, str) or new == cmd:
            return "", 0
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**ti, "command": new}}}), 0
    except Exception:  # noqa: BLE001
        return "", 0


def _check_hook(rewriter, cmd: str, unchanged_cmd: str) -> None:
    ti = {"command": cmd, "description": "run it", "timeout": 5000}
    payload = {"session_id": "s", "cwd": "/tmp", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": ti}
    out, rc = pretool_hook(json.dumps(payload), rewriter)
    assert rc == 0 and out, "hook must answer with a rewrite for a matching command"
    hs = json.loads(out)["hookSpecificOutput"]
    assert set(json.loads(out)) == {"hookSpecificOutput"} and hs["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hs, "a quieting rewrite must not auto-approve the command"
    assert hs["updatedInput"] == {**ti, "command": rewriter(cmd, payload)}, "updatedInput replaces the whole input: other fields must survive"
    assert pretool_hook(json.dumps({**payload, "tool_name": "Read"}), rewriter) == ("", 0), "other tools are not touched"
    assert pretool_hook(json.dumps({**payload, "tool_input": {**ti, "command": unchanged_cmd}}), rewriter) == ("", 0), "no match, no output"
    for junk in ("", "{not json", "[]", json.dumps({"tool_name": "Bash"}), json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})):
        assert pretool_hook(junk, rewriter) == ("", 0), f"malformed input must fail open: {junk!r}"


# ---- independent view of a rewrite: shlex tokens ---------------------------------------------------------------

def _toks(cmd: str) -> list[str]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _check_tokens(orig: str, new: str, pack: Pack) -> None:
    allowed = {"export", ";"}
    for r in pack.env:
        allowed |= {f"{k}={v}" for k, v in r.vars}
    for f in pack.flags:
        allowed |= set(f.add)
    a, b = _toks(orig), _toks(new)
    it = iter(b)
    assert all(t in it for t in a), f"a token was dropped or reordered: {orig!r} -> {new!r}"
    extra = Counter(b) - Counter(a)
    assert set(extra) <= allowed and extra[";"] <= 1, f"unexpected tokens {set(extra) - allowed} in {new!r}"


# ---- a real bash with shim programs ------------------------------------------------------------------------------

_SHIM = """#!/bin/sh
# one write() per record: pipeline stages run concurrently, and several printf calls into one >> would interleave
rec=$(
printf '%s' "${0##*/}"
for a in "$@"; do printf '\\037%s' "$a"; done
printf '\\036'
for v in $SHIM_WATCH; do eval "printf '%s=%s\\037' $v \\"\\${$v-<unset>}\\""; done
)
printf '%s\\n' "$rec" >> "$SHIM_LOG"
for a in "$@"; do [ "$a" = "--fail" ] && exit 3; done
exit 0
"""


class _Shell:
    def __init__(self, progs, watch):
        self.dir = tempfile.mkdtemp(prefix="ctxprune_pretool_")
        self.log = os.path.join(self.dir, "calls.log")
        self.watch = list(watch)
        for sub in ("app", "sub"):
            os.mkdir(os.path.join(self.dir, sub))
        for p in progs:
            path = os.path.join(self.dir, p)
            with open(path, "w") as f:
                f.write(_SHIM)
            os.chmod(path, 0o755)

    def run(self, cmd: str, env: dict | None = None):
        open(self.log, "w").close()
        e = {"PATH": f"{self.dir}:/usr/bin:/bin", "SHIM_LOG": self.log, "SHIM_WATCH": " ".join(self.watch), **(env or {})}
        p = subprocess.run(["bash", "-c", cmd], cwd=self.dir, env=e, capture_output=True, text=True, timeout=20)
        calls = []
        for line in open(self.log).read().split("\n"):
            if line:
                rec, _, envpart = line.partition("\x1e")
                fields = rec.split("\x1f")
                calls.append((fields[0], fields[1:], dict(kv.split("=", 1) for kv in envpart.split("\x1f") if kv)))
        return p.returncode, p.stdout, calls

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


@dataclass
class Banner:
    text: str
    kind: str = "nag"  # 'info' marks a line that carries information the reader may need (dropping it is a real loss)
    flag: str | None = None  # silenced when this flag is in argv
    env: tuple | None = None  # (NAME, predicate or None): silenced when NAME is set (and the value satisfies the predicate)
    env_has: tuple | None = None  # (NAME, substring)

    def silenced(self, argv, env) -> bool:
        if self.flag and self.flag in argv:
            return True
        if self.env:
            v = env.get(self.env[0], "<unset>")
            if v != "<unset>" and (self.env[1] is None or self.env[1](v)):
                return True
        if self.env_has:
            v = env.get(self.env_has[0], "<unset>")
            if v != "<unset>" and self.env_has[1] in v:
                return True
        return False


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _fake_out(prog, argv, env, spec) -> str:
    """Stand-in for a tool that honours the documented switches: base lines (ANSI colour unless NO_COLOR is set non-empty) plus its banners."""
    base, banners = spec[prog]
    colour = env.get("NO_COLOR", "") in ("", "<unset>")
    lines = [f"\x1b[32m{l}\x1b[0m" if colour else l for l in base]
    for b in banners:
        if not b.silenced(argv, env):
            lines += b.text.split("\n")
    if "--fail" in argv:
        lines.append("error: boom")
    return "\n".join(lines) + "\n"


def _engine_checks(pack, doc, match, nomatch, userset, sh, watch, forbidden=frozenset(), env0=None):
    """Contract checks shared by every PreToolUse case. Returns the (command, rewritten) pairs for the saving measurement.
    Corpus rule: no pipeline may contain two shim programs (their log order would depend on scheduling)."""
    env0 = env0 or {}
    names = {k for r in pack.env for k, _ in r.vars}
    for r in pack.env:
        for k, v in r.vars:
            assert k in doc and doc[k] == v, f"{k}={v!r} is not in the documented table"
    assert not (names & forbidden), f"behaviour-changing variables injected: {names & forbidden}"
    assert names <= set(watch), "watch every injected variable in the shim"
    flag_tokens = {t for f in pack.flags for t in f.add}
    pairs = []
    for c in match:
        pl = plan(c, pack, env0)
        assert pl.cmd != c, f"expected a rewrite: {c!r}"
        _check_tokens(c, pl.cmd, pack)
        assert rewrite(pl.cmd, pack, env0) == pl.cmd, f"not idempotent: {pl.cmd!r}"
        rc0, out0, calls0 = sh.run(c, env0)
        rc1, out1, calls1 = sh.run(pl.cmd, env0)
        assert (rc0, out0) == (rc1, out1), f"exit status or stdout changed: {c!r} ({rc0},{out0!r}) vs ({rc1},{out1!r})"
        assert [x[0] for x in calls0] == [x[0] for x in calls1], f"different programs ran: {c!r}"
        for (p0, argv0, e0), (p1, argv1, e1) in zip(calls0, calls1):
            it = iter(argv1)
            assert all(t in it for t in argv0), f"argv lost or reordered: {argv0} -> {argv1}"
            assert not (Counter(argv1) - Counter(argv0)).keys() - flag_tokens, f"unexpected extra argv in {argv1}"
            for k in watch:
                if e0[k] != "<unset>":
                    assert e1[k] == e0[k], f"{k} set by the user changed: {e0[k]!r} -> {e1[k]!r}"
            for k, v in pl.env.items():
                assert e1[k] == v, f"{k} not visible to {p1} (a later pipe stage?): {e1[k]!r}"
        pairs.append((c, pl.cmd, calls0, calls1))
    for c in nomatch:
        assert rewrite(c, pack, env0) == c, f"must stay byte-identical: {c!r}"
    for c, env, expect in userset:  # expect: {NAME: value the program must still see}
        pl = plan(c, pack, env)
        for k in expect:
            assert k not in pl.env, f"{k} was set by the user but injected anyway: {pl.cmd!r}"
        _check_tokens(c, pl.cmd, pack)
        assert rewrite(pl.cmd, pack, env) == pl.cmd, f"not idempotent: {pl.cmd!r}"
        for cmd in {c, pl.cmd}:
            _rc, _out, calls = sh.run(cmd, env)
            for _p, _argv, e in calls:
                for k, v in expect.items():
                    assert e[k] == v, f"{k}: user value {v!r} was replaced by {e[k]!r} ({cmd!r})"
    return pairs


def _savings(pairs, spec, expect_info_lost=frozenset()):
    before = after = 0
    info_lost: set[str] = set()
    banner_text = {t for _b, bs in spec.values() for b in bs for t in b.text.split("\n")}
    info_text = {t for _b, bs in spec.values() for b in bs if b.kind == "info" for t in b.text.split("\n")}
    for _c, _new, calls0, calls1 in pairs:
        for (p0, a0, e0), (p1, a1, e1) in zip(calls0, calls1):
            if p0 not in spec:
                continue
            o0, o1 = _fake_out(p0, a0, e0, spec), _fake_out(p1, a1, e1, spec)
            before, after = before + len(o0), after + len(o1)
            l0, l1 = _ANSI.sub("", o0).split("\n"), _ANSI.sub("", o1).split("\n")
            assert set(spec[p0][0]) <= set(l1), "the tool's real output (base lines) must survive"
            dropped = set(l0) - set(l1)
            assert dropped <= banner_text, f"something other than a banner disappeared: {dropped - banner_text}"
            if "--fail" in a0:
                assert "error: boom" in l1, "errors must survive"
            info_lost |= dropped & info_text
    assert info_lost == set(expect_info_lost), f"information lost: {info_lost}"
    assert after < before, "the rewrite must actually save output"
    return {"output_bytes_before": before, "output_bytes_after": after, "saved_pct": round(100 * (before - after) / before, 1)}


def _fuzz_engine(pack: Pack, progs: list[str], seed: int, n: int) -> int:
    """Random compositions of quoted words, operators and redirections: the rewrite keeps every token, keeps the syntax valid and is idempotent."""
    g = _lcg(seed)
    frags = ['echo "a | b ; c"', "echo 'x && y'", "ls -la", "cat f", "cd /tmp", "sleep 1", "2>&1", "> /dev/null", '"$HOME"', "FOO=bar", "-q", "--flag", "a\\ b", "'it'\"'\"'s'",
             "install", "i", "use", "status", "build", '"--fund"']
    ops = ["&&", "||", ";", "|", "\n", "&"]
    valid = lambda c: subprocess.run(["bash", "-n", "-c", c], capture_output=True).returncode == 0  # noqa: E731
    changed = checked = 0
    for _ in range(n):
        parts = []
        for _k in range(1 + next(g) % 4):
            words = [progs[next(g) % len(progs)]] + [frags[next(g) % len(frags)] for _ in range(next(g) % 4)]
            parts += [" ".join(words), ops[next(g) % len(ops)]]
        cmd = " ".join(parts[:-1])
        if not valid(cmd):
            continue
        checked += 1
        new = rewrite(cmd, pack, {})
        assert rewrite(new, pack, {}) == new, f"not idempotent: {cmd!r}"
        if new != cmd:
            changed += 1
            _check_tokens(cmd, new, pack)
            assert valid(new), f"rewrite broke the syntax: {cmd!r} -> {new!r}"
    assert checked >= n // 3 and changed >= checked // 4, f"fuzz is vacuous: checked {checked}, changed {changed}"
    return changed


def _pack_sim(pack, doc, spec, match, nomatch, userset, programs, forbidden=frozenset(), expect_info_lost=frozenset(), min_saved_pct=10.0, env0=None):
    watch = sorted({k for r in pack.env for k, _ in r.vars} | {"NO_COLOR"})
    sh = _Shell(programs, watch)
    try:
        pairs = _engine_checks(pack, doc, match, nomatch, userset, sh, watch, forbidden, env0)
        _check_hook(lambda c, d: rewrite(c, pack, env0 or {}), match[0], nomatch[0])
        assert rewrite(match[0], None, {}) == match[0], "an internal error must fall back to the original command"
        progs = sorted({p for r in pack.env for p in r.progs} | {p for f in pack.flags for p in f.progs} | {"ls", "echo"})
        res = _savings(pairs, spec, expect_info_lost)
        res["fuzz_rewrites_checked"] = _fuzz_engine(pack, progs, seed=len(progs) * 7 + len(match), n=120 if pack is PACK76 else 40)
        assert res["saved_pct"] >= min_saved_pct, f"saving too small: {res}"
        return res
    finally:
        sh.close()


# Documented switches. Names and values are what the injected commands carry; the sims assert the packs match this table.
# UNCERTAIN lists the ones whose existence or accepted values could not be confirmed offline (see the report).
DOC = {
    "NO_COLOR": "1",  # no-color.org: any non-empty value disables ANSI colour in tools that honour it
    "GIT_PAGER": "cat",  # git(1) environment variables
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",  # pip: option --disable-pip-version-check, env PIP_<OPTION>
    "NPM_CONFIG_UPDATE_NOTIFIER": "false",  # npm: config update-notifier, env npm_config_<key>
    "NEXT_TELEMETRY_DISABLED": "1",  # Next.js telemetry docs
    "BROWSERSLIST_IGNORE_OLD_DATA": "true",
    "PRISMA_HIDE_UPDATE_MESSAGE": "true",
    "DISABLE_OPENCOLLECTIVE": "true",
    "MAVEN_ARGS": "-ntp -B",  # Maven >= 3.9.0
    "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "CARGO_TERM_PROGRESS_WHEN": "never",
    "AWS_PAGER": "",  # empty string disables the client-side pager
    "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK": "1",  # gcloud property component_manager/disable_update_check
    "CLOUDSDK_SURVEY_DISABLE_PROMPTS": "1",  # gcloud property survey/disable_prompts
    "AZURE_CORE_ONLY_SHOW_ERRORS": "true",  # az config core.only_show_errors (also hides warnings)
    "TF_IN_AUTOMATION": "1",  # any non-empty value: no 'next steps' hints
    "DOCKER_CLI_HINTS": "false",  # Docker CLI >= 25
    "GH_NO_UPDATE_NOTIFIER": "1",  # any value
    "DIRENV_LOG_FORMAT": "",  # empty format silences direnv's own log lines
    "LC_ALL": "C.UTF-8",
    "LANGUAGE": "en",
}
UNCERTAIN = {"BROWSERSLIST_IGNORE_OLD_DATA", "PRISMA_HIDE_UPDATE_MESSAGE", "DISABLE_OPENCOLLECTIVE", "MAVEN_ARGS", "CLOUDSDK_SURVEY_DISABLE_PROMPTS", "LC_ALL"}
assert UNCERTAIN <= set(DOC)
# Variables that change what a command DOES, not what it prints. No pack may inject them.
BEHAVIOURAL = frozenset({"CI", "TERM", "CLOUDSDK_CORE_DISABLE_PROMPTS", "TF_INPUT", "GIT_TERMINAL_PROMPT", "NPM_CONFIG_AUDIT", "PIP_QUIET", "NPM_CONFIG_LOGLEVEL", "DEBIAN_FRONTEND"})

_CHATTY = frozenset("git npm npx pnpm yarn pip pip3 pytest cargo make".split())


def _fs(*xs):
    return frozenset(xs)


# ---------------------------------------------------------------------------------------------------------------
# #76 Quiet defaults: NO_COLOR and GIT_PAGER through an export prefix, '--no-fund' after 'npm install'.
# Narrowed from the idea: CI=1 and TERM=dumb change behaviour (CI makes CRA treat warnings as errors, jest and others stop watching),
# '--no-audit' drops the vulnerability summary, 'pip -q' drops the 'Successfully installed' line: none of them is lossless.

PACK76 = Pack(
    env=(EnvRule(_CHATTY, (("NO_COLOR", "1"),)), EnvRule(_fs("git"), (("GIT_PAGER", "cat"),))),
    flags=(FlagAdd(_fs("npm"), ("--no-fund",), _fs("--fund", "--no-fund"), subs=_fs("install", "i", "ci", "add")),),
)
SPEC76 = {
    "git": (["On branch main", "nothing to commit, working tree clean"], []),
    "npm": (["added 1 package in 2s"], [Banner("1 package is looking for funding\n  run `npm fund` for details", flag="--no-fund")]),
    "pytest": (["===== 12 passed in 0.31s ====="], []),
}


def sim_quiet76() -> dict:
    match = ["git status", "cd app && npm install left-pad && npm test | tail -5", "git log --oneline | head -3 && git diff --stat", "npm ci --fail || echo failed",
             "FOO=1 pytest -x tests/ 2>&1 | tail -20", "npm i --save-dev jest; git status --fail", "echo input | git hash-object --stdin", "(cd sub && git status) && npm add x", "npm run build"]
    nomatch = ["echo \"git status is chatty\"", "ls -la /tmp", "cat README.md | head -3", "cd /tmp && ls", "echo $(git status)", "git status <<EOF\nx\nEOF", "python3 -c 'print(1)'",
               "", "   ", "bash -c 'git status'", "git status; echo $?", "grep -rn foo ."]
    userset = [("NO_COLOR=0 git status", {}, {"NO_COLOR": "0"}), ("git log", {"GIT_PAGER": "less"}, {"GIT_PAGER": "less"}), ("git status", {"NO_COLOR": ""}, {"NO_COLOR": ""}),
               ("npm install --fund left-pad", {}, {}), ("npm install --no-fund left-pad", {}, {})]
    res = _pack_sim(PACK76, DOC, SPEC76, match, nomatch, userset, ["git", "npm", "pytest"], forbidden=BEHAVIOURAL)
    assert rewrite("npm install --fund left-pad", PACK76, {}).count("--no-fund") == 0 and rewrite("npm install --no-fund x", PACK76, {}).count("--no-fund") == 1
    # why an export prefix and not an inline 'VAR=1 cmd': the inline form only reaches the first stage of a pipeline
    sh = _Shell(["git"], ["NO_COLOR"])
    try:
        seen = lambda cmd: {c[1][0]: c[2]["NO_COLOR"] for c in sh.run(cmd)[2]}  # noqa: E731  (pipeline stages log in any order: key by argument)
        assert seen("NO_COLOR=1 git a | git b") == {"a": "1", "b": "<unset>"}, "inline prefix reaches stage 1 only"
        assert seen(rewrite("git a | git b", PACK76, {})) == {"a": "1", "b": "1"}, "export prefix reaches every stage"
    finally:
        sh.close()
    return res


case(
    76,
    claim="For commands that run chatty tools, 'export NO_COLOR=1 [GIT_PAGER=cat]; ' is put in front of the unchanged command and '--no-fund' after 'npm install|i|ci|add'; arguments, quoting, pipes and exit status are unchanged, anything the user already set stays theirs, other commands are byte-identical.",
    sim=sim_quiet76,
)

# ---------------------------------------------------------------------------------------------------------------
# #217 Build-tool quiet pack.

PACK217 = Pack(
    env=(EnvRule(_fs("mvn", "mvnw"), (("MAVEN_ARGS", "-ntp -B"),)), EnvRule(_fs("dotnet"), (("DOTNET_NOLOGO", "1"), ("DOTNET_CLI_TELEMETRY_OPTOUT", "1"))),
         EnvRule(_fs("cargo"), (("CARGO_TERM_PROGRESS_WHEN", "never"),))),
    flags=(FlagAdd(_fs("gradle", "gradlew"), ("--console=plain",), _fs("--console")),),
)
SPEC217 = {
    "mvn": (["[INFO] BUILD SUCCESS", "[INFO] Total time:  4.2 s"], [Banner("Progress (1): 12/45 kB\nProgress (1): 45 kB", env_has=("MAVEN_ARGS", "-ntp"))]),
    "dotnet": (["Build succeeded.", "    0 Warning(s)"], [Banner("Welcome to .NET 8.0!\n---------------------\nSDK Version: 8.0.100", env=("DOTNET_NOLOGO", None)),
                                                          Banner("Telemetry\n---------\nThe .NET tools collect usage data in order to help us improve your experience.", env=("DOTNET_CLI_TELEMETRY_OPTOUT", None))]),
    "cargo": (["    Finished dev profile in 3.1s"], [Banner("  Downloading crates ...\n[=====>      ] 12/80: serde", env=("CARGO_TERM_PROGRESS_WHEN", lambda v: v == "never"))]),
    "gradlew": (["BUILD SUCCESSFUL in 2s"], [Banner("<-------------> 0% INITIALIZING [0s]\n<=====--------> 40% EXECUTING [1s]", flag="--console=plain")]),
}


def sim_build217() -> dict:
    match = ["mvn -q test", "./mvnw clean verify --fail || echo red", "dotnet build -c Release && dotnet test", "cargo build --release 2>&1 | tail -3", "./gradlew build",
             "cd app && mvn package | tail -20", "gradlew test --fail"]
    nomatch = ["go test ./...", "make all", "echo mvn", "ls ~/.m2"]
    userset = [("mvn -q test", {"MAVEN_ARGS": "-T 4"}, {"MAVEN_ARGS": "-T 4"}), ("./gradlew --console=rich build", {}, {}), ("dotnet build", {"DOTNET_NOLOGO": ""}, {"DOTNET_NOLOGO": ""}),
               ("MAVEN_ARGS=-X mvn test", {}, {"MAVEN_ARGS": "-X"}), ("cargo build", {"CARGO_TERM_PROGRESS_WHEN": "always"}, {"CARGO_TERM_PROGRESS_WHEN": "always"})]
    assert rewrite("./gradlew --console=rich build", PACK217, {}) == "./gradlew --console=rich build"
    assert rewrite("./gradlew build", PACK217, {}) == "./gradlew --console=plain build"
    return _pack_sim(PACK217, DOC, SPEC217, match, nomatch, userset, ["mvn", "mvnw", "dotnet", "cargo", "gradlew"], forbidden=BEHAVIOURAL)


case(
    217,
    claim="Maven, .NET, Cargo and Gradle commands get MAVEN_ARGS='-ntp -B', DOTNET_NOLOGO and telemetry opt-out, CARGO_TERM_PROGRESS_WHEN=never or '--console=plain' only when the user has not set them; the build's own lines and exit status are unchanged and other commands are byte-identical.",
    sim=sim_build217,
)

# ---------------------------------------------------------------------------------------------------------------
# #610 Ecosystem nag silencing.

_JS = _fs("npm", "npx", "pnpm", "yarn", "next", "prisma", "node")
PACK610 = Pack(env=(
    EnvRule(_fs("pip", "pip3"), (("PIP_DISABLE_PIP_VERSION_CHECK", "1"),)),
    EnvRule(_JS, (("NPM_CONFIG_UPDATE_NOTIFIER", "false"), ("NEXT_TELEMETRY_DISABLED", "1"), ("BROWSERSLIST_IGNORE_OLD_DATA", "true"),
                  ("PRISMA_HIDE_UPDATE_MESSAGE", "true"), ("DISABLE_OPENCOLLECTIVE", "true"))),
))
_NPM_BANNERS = [Banner("npm notice New major version of npm available! 9.8.1 -> 10.2.0\nnpm notice Run npm install -g npm@10.2.0 to update!", env=("NPM_CONFIG_UPDATE_NOTIFIER", lambda v: v == "false")),
                Banner("Thank you for using core-js!\nThe project needs your help! Please consider supporting core-js:\n> https://opencollective.com/core-js", env=("DISABLE_OPENCOLLECTIVE", None))]
SPEC610 = {
    "pip": (["Requirement already satisfied: requests in ./venv/lib/python3.12/site-packages (2.31.0)"],
            [Banner("[notice] A new release of pip is available: 24.0 -> 24.2\n[notice] To update, run: pip install --upgrade pip", env=("PIP_DISABLE_PIP_VERSION_CHECK", None))]),
    "npm": (["added 120 packages in 6s"], _NPM_BANNERS),
    "next": (["Compiled successfully", "Route (app)    Size"], [Banner("Attention: Next.js now collects completely anonymous telemetry regarding usage.", env=("NEXT_TELEMETRY_DISABLED", None)),
                                                               Banner("Browserslist: caniuse-lite is outdated. Please run:\n  npx update-browserslist-db@latest", env=("BROWSERSLIST_IGNORE_OLD_DATA", None))]),
    "prisma": (["Prisma schema loaded from prisma/schema.prisma"], [Banner("Update available 5.1.0 -> 5.2.0\nRun the following to update\n  npm i --save-dev prisma@latest", env=("PRISMA_HIDE_UPDATE_MESSAGE", None))]),
}


def sim_nag610() -> dict:
    match = ["pip install requests", "pip3 install -r requirements.txt --fail | tail -3", "npm install && npm run build", "next build 2>&1 | tail -20", "prisma generate; npm test",
             "cd app && pip install -e . && next build"]
    nomatch = ["python3 -m pytest -q", "make test", "echo pip install", "git status"]
    userset = [("pip install x", {"PIP_DISABLE_PIP_VERSION_CHECK": "0"}, {"PIP_DISABLE_PIP_VERSION_CHECK": "0"}),
               ("NEXT_TELEMETRY_DISABLED=0 next build", {}, {"NEXT_TELEMETRY_DISABLED": "0"}),
               ("npm install", {"NPM_CONFIG_UPDATE_NOTIFIER": "true"}, {"NPM_CONFIG_UPDATE_NOTIFIER": "true"})]
    res = _pack_sim(PACK610, DOC, SPEC610, match, nomatch, userset, ["pip", "pip3", "npm", "next", "prisma"], forbidden=BEHAVIOURAL)
    assert {"NEXT_TELEMETRY_DISABLED", "BROWSERSLIST_IGNORE_OLD_DATA"} <= set(plan("npm install", PACK610, {}).env)
    return res


case(
    610,
    claim="pip commands get PIP_DISABLE_PIP_VERSION_CHECK=1 and JS tool commands get the npm update-notifier, Next.js telemetry, Browserslist, Prisma and OpenCollective silencers through an export prefix, each only when the user has not set it; real output lines and exit status are unchanged.",
    sim=sim_nag610,
)

# ---------------------------------------------------------------------------------------------------------------
# #788 Infra CLI nag, hint and pager silencing. AZURE_CORE_ONLY_SHOW_ERRORS also hides az warnings: declared as information loss.

PACK788 = Pack(env=(
    EnvRule(_fs("aws"), (("AWS_PAGER", ""),)),
    EnvRule(_fs("gcloud", "gsutil"), (("CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK", "1"), ("CLOUDSDK_SURVEY_DISABLE_PROMPTS", "1"))),
    EnvRule(_fs("az"), (("AZURE_CORE_ONLY_SHOW_ERRORS", "true"),)),
    EnvRule(_fs("terraform", "tofu"), (("TF_IN_AUTOMATION", "1"),)),
    EnvRule(_fs("docker"), (("DOCKER_CLI_HINTS", "false"),)),
    EnvRule(_fs("gh"), (("GH_NO_UPDATE_NOTIFIER", "1"),)),
))
AZ_WARNING = "WARNING: The behavior of this command has been altered by the following extension: azure-devops"
SPEC788 = {
    "aws": (['{"Buckets": []}'], []),
    "gcloud": (["Updated property [core/project]."], [Banner("Updates are available for some Google Cloud CLI components.  To install them,\nplease run:\n  $ gcloud components update", env=("CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK", None)),
                                                     Banner("Your active configuration is: [default]\nWe would love your feedback: take a short survey", env=("CLOUDSDK_SURVEY_DISABLE_PROMPTS", None))]),
    "az": (["Name    Location", "rg-app  westeurope"], [Banner(AZ_WARNING, kind="info", env=("AZURE_CORE_ONLY_SHOW_ERRORS", lambda v: v == "true"))]),
    "terraform": (["Terraform has been successfully initialized!"], [Banner("You may now begin working with Terraform. Try running \"terraform plan\" to see\nany changes that are required for your infrastructure.", env=("TF_IN_AUTOMATION", None))]),
    "docker": (["Successfully built 3f2a1b9c0d4e"], [Banner("What's next:\n    View a summary of image vulnerabilities and recommendations -> docker scout quickview", env=("DOCKER_CLI_HINTS", lambda v: v == "false"))]),
    "gh": (["Showing 3 of 3 open pull requests in acme/app"], [Banner("A new release of gh is available: 2.40.0 -> 2.41.0\nhttps://github.com/cli/cli/releases/tag/v2.41.0", env=("GH_NO_UPDATE_NOTIFIER", None))]),
}


def sim_infra788() -> dict:
    match = ["aws s3api list-buckets --fail || echo failed", "gcloud config set project demo && gcloud auth list", "az group list -o table | head", "terraform init -input=false",
             "docker build -t x . 2>&1 | tail -5", "gh pr list; aws sts get-caller-identity", "cd sub && terraform plan --fail"]
    nomatch = ["kubectl get pods", "echo aws", "ls", "dig +short example.com"]
    userset = [("aws s3 ls", {"AWS_PAGER": "less"}, {"AWS_PAGER": "less"}), ("aws s3 ls", {"AWS_PAGER": ""}, {"AWS_PAGER": ""}), ("docker ps", {"DOCKER_CLI_HINTS": "true"}, {"DOCKER_CLI_HINTS": "true"}),
               ("TF_IN_AUTOMATION=0 terraform plan", {}, {"TF_IN_AUTOMATION": "0"})]
    res = _pack_sim(PACK788, DOC, SPEC788, match, nomatch, userset, ["aws", "gcloud", "gsutil", "az", "terraform", "tofu", "docker", "gh"], forbidden=BEHAVIOURAL | {"CLOUDSDK_CORE_DISABLE_PROMPTS"},
                    expect_info_lost={AZ_WARNING})
    assert plan("aws s3 ls", PACK788, {}).env == {"AWS_PAGER": ""} and rewrite("aws s3 ls", PACK788, {}) == "export AWS_PAGER=''; aws s3 ls", "an empty value must be written as ''"
    res["info_lines_lost"] = 1  # the az warning: the one place where 'silence nags' also silences content
    return res


case(
    788,
    claim="aws, gcloud, az, terraform, docker and gh commands get AWS_PAGER='', gcloud update-check and survey, TF_IN_AUTOMATION, DOCKER_CLI_HINTS=false or GH_NO_UPDATE_NOTIFIER through an export prefix unless the user set them (an empty value counts as set); only banner lines disappear, except that AZURE_CORE_ONLY_SHOW_ERRORS also hides az warnings, which the sim asserts as the single content loss.",
    sim=sim_infra788,
)

# ---------------------------------------------------------------------------------------------------------------
# #1227 Shell-startup and env-manager chatter: DIRENV_LOG_FORMAT= after cd-like commands, 'nvm use --silent'.
# nvm's 'Now using node vX' line states the active version, so silencing it is information loss: asserted and reported.

PACK1227 = Pack(
    env=(EnvRule(_fs("cd", "pushd", "popd", "source", "."), (("DIRENV_LOG_FORMAT", ""),)),),
    flags=(FlagAdd(_fs("nvm"), ("--silent",), _fs("--silent", "-s"), subs=_fs("use")),),
)
NVM_LINE = "Now using node v18.19.0 (npm v10.2.3)"
SPEC1227 = {
    "git": (["On branch main"], [Banner("direnv: loading ~/proj/app/.envrc\ndirenv: export +API_KEY ~PATH", env=("DIRENV_LOG_FORMAT", lambda v: v == ""))]),
    "nvm": (["ok"], [Banner(NVM_LINE, kind="info", flag="--silent")]),
}


def sim_shell1227() -> dict:
    match = ["cd app && git status", "pushd sub > /dev/null && git status --fail", "source ./env.sh; git status", "nvm use 18 && git status", "cd sub; nvm use 20 --fail"]
    nomatch = ["git status", "direnv allow", "direnv status", "ls app", "echo cd app"]
    userset = [("cd app && git status", {"DIRENV_LOG_FORMAT": "direnv: %s"}, {"DIRENV_LOG_FORMAT": "direnv: %s"}),
               ("cd app && git status", {"DIRENV_LOG_FORMAT": ""}, {"DIRENV_LOG_FORMAT": ""}), ("nvm use --silent 18", {}, {})]
    res = _pack_sim(PACK1227, DOC, SPEC1227, match, nomatch, userset, ["git", "nvm", "direnv"], forbidden=BEHAVIOURAL, expect_info_lost={NVM_LINE}, env0={})
    assert plan("cd app", PACK1227, {}).env == {"DIRENV_LOG_FORMAT": ""} and rewrite("cd app", PACK1227, {}) == "export DIRENV_LOG_FORMAT=''; cd app"
    assert rewrite("nvm use 18", PACK1227, {}).startswith("nvm use --silent 18") or rewrite("nvm use 18", PACK1227, {}).endswith("nvm use --silent 18")
    res["info_lines_lost"] = 1
    return res


case(
    1227,
    claim="A command that changes directory or sources a file gets DIRENV_LOG_FORMAT='' (an empty value is written as '' and a value the user set, even an empty one, is kept), 'nvm use' gets '--silent'; explicit direnv commands are not touched; the sim asserts that nvm's 'Now using node' line, which names the active version, is the one piece of content this drops.",
    sim=sim_shell1227,
)

# ---------------------------------------------------------------------------------------------------------------
# #1228 Pin LC_ALL and LANGUAGE so diagnostics print in English. A model of GNU gettext's choice of message language.

_CAT = {  # hand-translated sample catalogue for one git fatal line and one gcc warning
    "git": {"en": "fatal: not a git repository (or any of the parent directories): .git",
            "de": "fatal: kein Git-Repository (oder irgendein Elternverzeichnis): .git",
            "ru": "fatal: не найден git-репозиторий (или любой из родительских каталогов): .git",
            "ja": "致命的エラー: gitリポジトリではありません (または親ディレクトリのいずれでもありません): .git"},
    "gcc": {"en": "warning: unused variable 'count' [-Wunused-variable]", "de": "Warnung: ungenutzte Variable »count« [-Wunused-variable]",
            "ru": "предупреждение: неиспользуемая переменная «count» [-Wunused-variable]", "ja": "警告: 未使用の変数 'count' [-Wunused-variable]"},
}


def messages_lang(env: dict) -> str:
    """Which catalogue GNU gettext picks: LC_ALL > LC_MESSAGES > LANG; LANGUAGE (colon list) wins unless the locale is exactly C or POSIX."""
    loc = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or "C"
    if loc in ("C", "POSIX"):
        return "en"
    for entry in [e for e in env.get("LANGUAGE", "").split(":") if e] or [loc]:
        code = re.split(r"[_.@]", entry)[0]
        if code in ("en", *_CAT["git"]):
            return code
    return "en"


def charset(env: dict) -> str:
    loc = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or "C"
    return "UTF-8" if re.search(r"utf-?8", loc, re.I) else "ASCII"


PACK1228 = Pack(env=(EnvRule(_fs("git", "gcc", "cc", "clang", "make", "pip", "pip3", "apt", "apt-get", "dpkg"), (("LC_ALL", "C.UTF-8"), ("LANGUAGE", "en")), atomic=True, blockers=("LC_MESSAGES",)),))


def sim_locale1228() -> dict:
    assert DOC["LC_ALL"].endswith("UTF-8"), "pin a UTF-8 locale so non-ASCII file names survive"
    saved = {}
    for lang, base in (("de", "de_DE.UTF-8"), ("ru", "ru_RU.UTF-8"), ("ja", "ja_JP.UTF-8")):
        env = {"LANG": base}
        pl = plan("git status && gcc -c a.c", PACK1228, env)
        assert messages_lang(env) == lang and messages_lang({**env, **pl.env}) == "en", f"{lang} must switch to English"
        assert charset({**env, **pl.env}) == "UTF-8"
        for tool in ("git", "gcc"):
            saved.setdefault(lang, 0)
            saved[lang] += tokens(_CAT[tool][lang]) - tokens(_CAT[tool]["en"])
        if _exact_tokens():  # non-Latin text is one proxy piece per character, so the comparison is only meaningful on o200k
            assert saved[lang] > 0, f"English must cost fewer tokens than {lang}"
    # LANGUAGE alone would also work, but only the pair is safe: with LC_ALL=C.UTF-8 alone a system LANGUAGE still wins (it is ignored only for exactly 'C')
    assert messages_lang({"LANG": "de_DE.UTF-8", "LC_ALL": "C.UTF-8", "LANGUAGE": "de"}) == "de"
    assert messages_lang({"LANG": "de_DE.UTF-8", "LC_ALL": "C.UTF-8", "LANGUAGE": "en"}) == "en"
    # never override: the user's own LC_ALL, LC_MESSAGES or LANGUAGE choice is respected, even though the messages then stay translated
    for env, still in (({"LC_ALL": "de_DE.UTF-8"}, "de"), ({"LANG": "ru_RU.UTF-8", "LC_MESSAGES": "ru_RU.UTF-8"}, "ru"), ({"LANG": "de_DE.UTF-8", "LANGUAGE": "de:en"}, "de")):
        pl = plan("git status", PACK1228, env)
        assert pl.cmd == "git status" and messages_lang({**env, **pl.env}) == still, f"user choice overridden: {env}"
    assert rewrite("git status", PACK1228, {"LANG": "C"}) == "export LC_ALL=C.UTF-8 LANGUAGE=en; git status", "a plain C locale is not the user's choice of language"
    assert rewrite("ls -la", PACK1228, {"LANG": "de_DE.UTF-8"}) == "ls -la" and rewrite("echo git", PACK1228, {}) == "echo git"
    assert rewrite("LC_ALL=de_DE.UTF-8 git status", PACK1228, {}) == "LC_ALL=de_DE.UTF-8 git status"
    # the locale the pin names must exist where it is used: check it in the container (glibc 2.35+, Debian and Ubuntu, musl ship C.UTF-8)
    out = subprocess.run(["bash", "-c", "LC_ALL=C.UTF-8 locale charmap"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}).stdout.strip()
    assert out == "UTF-8", f"C.UTF-8 is not available here: {out!r}"
    # engine contract and hook shape
    sh = _Shell(["git", "gcc"], ["LC_ALL", "LANGUAGE"])
    try:
        pairs = _engine_checks(PACK1228, DOC, ["git status", "git log | head -2 && gcc -c a.c --fail || echo failed"], ["ls", "echo \"git\"", "cat x | sort"],
                               [("git status", {"LC_ALL": "de_DE.UTF-8"}, {"LC_ALL": "de_DE.UTF-8"}), ("git status", {"LANGUAGE": "de"}, {"LANGUAGE": "de"})], sh, ["LC_ALL", "LANGUAGE"], env0={"LANG": "de_DE.UTF-8"})
    finally:
        sh.close()
    _check_hook(lambda c, d: rewrite(c, PACK1228, {"LANG": "de_DE.UTF-8"}), "git status", "ls")
    return {"tokens_saved_de": saved["de"], "tokens_saved_ru": saved["ru"], "tokens_saved_ja": saved["ja"], "commands_checked": len(pairs)}


case(
    1228,
    claim="Commands that run git, gcc, make, pip or apt get 'export LC_ALL=C.UTF-8 LANGUAGE=en;' so a gettext tool prints its English message (fewer tokens than de, ru or ja) in a UTF-8 charset; the pair is skipped whenever the user set LC_ALL, LANGUAGE or LC_MESSAGES.",
    sim=sim_locale1228,
)

# ---------------------------------------------------------------------------------------------------------------
# #143 Byte-offset cursor for repeated reads of a growing log. A hook with a ledger of what the model has seen.


def _tail_start(data: bytes, n: int) -> int:
    """Offset where `tail -n N` output begins (a final unterminated line counts as a line)."""
    if n <= 0:
        return len(data)
    pos = len(data) - 1 if data.endswith(b"\n") else len(data)
    for _ in range(n):
        pos = data.rfind(b"\n", 0, pos)
        if pos < 0:
            return 0
    return pos + 1


def _last_lines(buf: bytes, n: int) -> bytes:
    """Independent `tail -n N` on bytes, written with split instead of rfind."""
    parts = buf.split(b"\n")
    trailing = parts[-1] == b""
    if trailing:
        parts = parts[:-1]
    sel = parts[-n:] if n > 0 else []
    return (b"\n".join(sel) + b"\n" if trailing else b"\n".join(sel)) if sel else b""


_PLAIN143 = re.compile(r"[A-Za-z0-9_./-]+")


def parse_read_cmd(cmd: str):
    """('cat', None, file) or ('tail', N, file) for exactly the simple forms the cursor understands; anything else is not touched."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if not parts or not all(_PLAIN143.fullmatch(p) for p in parts):
        return None
    a = parts[1:]
    if parts[0] == "cat" and len(a) == 1 and not a[0].startswith("-"):
        return "cat", None, a[0]
    if parts[0] == "tail":
        if len(a) == 3 and a[0] == "-n" and a[1].isdigit() and not a[2].startswith("-"):
            return "tail", int(a[1]), a[2]
        if len(a) == 2 and a[0][:1] == "-" and a[0][1:].isdigit() and not a[1].startswith("-"):
            return "tail", int(a[0][1:]), a[1]
        if len(a) == 1 and not a[0].startswith("-"):
            return "tail", 10, a[0]
    return None


class Cursor:
    """Ledger: for each file, the byte range [from, to) the model has been shown, with the sha256 of the file's first `to` bytes."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            d = json.load(open(self.path))
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001  corrupt or missing ledger: act as if nothing was seen
            return {}

    def save(self, d: dict) -> None:
        try:
            with open(self.path + ".tmp", "w") as f:
                json.dump(d, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass

    def reset(self) -> None:  # context was compacted or a new session started: nothing is known to be in the model's context any more
        self.save({})

    def pre(self, cmd: str, cwd: str):
        """(command to run, pending ledger entry or None). Never raises: any trouble means the original command."""
        try:
            spec = parse_read_cmd(cmd)
            if not spec:
                return cmd, None
            kind, n, f = spec
            path = os.path.realpath(os.path.join(cwd, f))
            if not os.path.isfile(path):
                return cmd, None
            data = open(path, "rb").read()
            size = len(data)
            w = 0 if kind == "cat" else _tail_start(data, n)  # first byte the plain command would print
            ent = self.load().get(path)
            new_cmd, frm = cmd, w
            if isinstance(ent, dict):
                efrom, eto, sha = int(ent["from"]), int(ent["to"]), str(ent["sha"])
                if 0 < eto <= size and hashlib.sha256(data[:eto]).hexdigest() == sha:
                    if w <= eto:
                        frm = min(efrom, w)
                    if efrom <= w < eto:
                        c = eto if data[eto - 1 : eto] == b"\n" else data.rfind(b"\n", 0, eto) + 1  # re-show a half-written last line
                        c = max(c, w)
                        header = f"[ctxprune: {f}: bytes 0-{c} were shown by an earlier read and are skipped; output continues at byte {c}]"
                        if c - w > len(header) + 1:  # only when it pays for its own header
                            new_cmd = f"printf '%s\\n' {shlex.quote(header)}; tail -c +{c + 1} {shlex.quote(f)}" + (f" | tail -n {n}" if kind == "tail" else "")
            return new_cmd, (path, frm, size, hashlib.sha256(data).hexdigest())
        except Exception:  # noqa: BLE001
            return cmd, None

    def commit(self, pending) -> None:  # only after the tool call succeeded
        if pending:
            path, frm, size, sha = pending
            d = self.load()
            d[path] = {"from": frm, "to": size, "sha": sha}
            self.save(d)


def sim_cursor143() -> dict:
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "build.log")
        led = Cursor(os.path.join(d, "ledger.json"))
        counter = itertools.count(1)

        def chunk(k: int) -> bytes:
            return "".join(f"[{next(counter) // 60:02d}:{next(counter) % 60:02d}] compile module_{next(counter)}.c ... ok (cache miss, 0.{k % 9}s)\n" for _ in range(k)).encode()

        def run(cmd: str) -> bytes:
            p = subprocess.run(["bash", "-c", cmd], cwd=d, capture_output=True)
            return p.stdout

        def append(b: bytes) -> None:
            with open(log, "ab") as fh:
                fh.write(b)

        known: dict[str, tuple[int, bytes]] = {}  # the model's reconstruction of the file, from outputs alone
        tot = {"naive": 0, "cursor": 0}

        def poll(cmd: str, expect_rewrite: bool | None, commit: bool = True) -> bytes:
            naive = run(cmd)
            new_cmd, pending = led.pre(cmd, d)
            if expect_rewrite is not None:
                assert (new_cmd != cmd) == expect_rewrite, f"rewrite expected={expect_rewrite} for {cmd!r}: {new_cmd!r}"
            out = run(new_cmd)
            tot["naive"] += len(naive)
            tot["cursor"] += len(out)
            spec = parse_read_cmd(cmd)
            if spec and commit:
                led.commit(pending)
                # reader view: rebuild what the model knows from the outputs only
                if new_cmd != cmd:
                    header, _, body = out.partition(b"\n")
                    c = int(re.search(rb"output continues at byte (\d+)", header).group(1))
                    start, buf = known[spec[2]]
                    assert start <= c <= start + len(buf), "cursor starts beyond what the model has seen: a gap"
                    known[spec[2]] = (start, buf[: c - start] + body)
                else:
                    known[spec[2]] = (os.path.getsize(log) - len(out), out) if os.path.exists(log) and spec[2] == "build.log" else (0, out)
                start, buf = known[spec[2]]
                want = naive
                got = buf if spec[0] == "cat" else _last_lines(buf, spec[1])
                assert got == want, f"reader view differs from the plain output for {cmd!r}"
            return out

        # phase A: the idea's own scenario, ten polls of tail -n 200 on a growing log
        append(chunk(400))
        before = dict(tot)
        poll("tail -n 200 build.log", False)
        for k in (30, 5, 45, 12, 80, 3, 260, 20, 33):
            append(chunk(k))
            poll("tail -n 200 build.log", k != 260)  # 260 new lines push the whole window past the cursor: nothing to skip, plain command
        a_naive, a_cursor = tot["naive"] - before["naive"], tot["cursor"] - before["cursor"]
        assert a_cursor < 0.45 * a_naive, f"cursor should cut the polling cost: {a_cursor} vs {a_naive}"

        # a half-written last line: the cursor backs up to the start of that line
        append(b"[99:99] linking app (half a li")
        poll("tail -n 200 build.log", True)
        append(b"ne)\n" + chunk(15))
        out = poll("tail -n 200 build.log", True)
        assert out.split(b"\n", 1)[1].startswith(b"[99:99] linking app (half a line)"), "the completed line must be shown whole"

        # asking for more than was shown, or for the whole file after only a tail: plain command
        poll("tail -n 300 build.log", False)
        poll("cat build.log", False)
        append(chunk(12))
        poll("cat build.log", True)
        unchanged = poll("cat build.log", True)  # nothing new: header only
        assert unchanged.count(b"\n") == 1 and unchanged.startswith(b"[ctxprune"), "no growth: just the header"

        # not applicable: a pipeline is not the simple form and must not touch the ledger
        before_ledger = open(led.path).read()
        assert poll("tail -n 200 build.log | grep -c compile", False, commit=False) is not None and open(led.path).read() == before_ledger
        assert parse_read_cmd("tail -f build.log") is None and parse_read_cmd("cat a b") is None and parse_read_cmd("cat build.log > x") is None and parse_read_cmd("tail -n +5 build.log") is None

        # a pending read that never ran (denied, failed) must not advance the cursor
        append(chunk(9))
        r1, p1 = led.pre("cat build.log", d)
        _r2, p2 = led.pre("cat build.log", d)
        assert r1 != "cat build.log" and p1 == p2, "same state, same rewrite until a read is committed"
        poll("cat build.log", True)

        # edited history: same size, one byte changed inside the already-seen part -> hash mismatch -> plain
        raw = bytearray(open(log, "rb").read())
        raw[10] ^= 0x01
        open(log, "wb").write(bytes(raw))
        append(chunk(4))
        poll("cat build.log", False)
        known["build.log"] = (0, bytes(open(log, "rb").read()))

        # log rotation: the file is replaced by a shorter one -> plain
        open(log, "wb").write(chunk(50))
        poll("cat build.log", False)
        append(chunk(5))
        poll("cat build.log", True)

        # corrupt ledger: fail open, and a later commit repairs it
        open(led.path, "w").write("{not json")
        poll("cat build.log", False)
        append(chunk(5))
        poll("cat build.log", True)

        # compaction drops the earlier output from the model's context: reset, then the next read is plain
        led.reset()
        poll("cat build.log", False)

        # missing file, other file: independent entries, no exceptions
        assert led.pre("cat nope.log", d) == ("cat nope.log", None)
        with open(os.path.join(d, "other.log"), "wb") as fh:
            fh.write(chunk(30))
        poll("cat other.log", False)
        append(chunk(3))
        poll("cat build.log", True)
        poll("cat other.log", True)  # its own entry: unchanged since its first read

        # the hook around it
        payload = {"tool_name": "Bash", "cwd": d, "tool_input": {"command": "cat build.log", "description": "read the log"}}
        append(chunk(8))
        out, rc = pretool_hook(json.dumps(payload), lambda c, data: led.pre(c, data["cwd"])[0])
        assert rc == 0 and json.loads(out)["hookSpecificOutput"]["updatedInput"]["description"] == "read the log"
        assert pretool_hook("garbage", lambda c, data: "x") == ("", 0)
        assert led.pre("cat build.log", os.path.join(d, "no-such-dir")) == ("cat build.log", None)
        return {"polls_naive_bytes": a_naive, "polls_cursor_bytes": a_cursor, "polls_saved_pct": round(100 * (1 - a_cursor / a_naive), 1), "all_naive_bytes": tot["naive"], "all_cursor_bytes": tot["cursor"]}


case(
    143,
    claim="A repeated 'cat F' or 'tail -n N F' of a log the model already saw is rewritten to print only the bytes after the last fully shown line (with a header naming the offset) when the sha256 of the shown prefix still matches; after truncation, an edited prefix, a corrupt ledger or a compaction reset the original command runs, and the reader's reconstruction equals the plain output at every poll.",
    sim=sim_cursor143,
)


# ---------------------------------------------------------------------------------------------------------------
# Real git for #227 and #486: a scratch repository, no global or system config, so the runs are reproducible.

def _git_env(d: str, global_cfg: str = os.devnull, extra: dict | None = None) -> dict:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "GIT_CONFIG_GLOBAL": global_cfg, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C.UTF-8",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", **(extra or {})}


def _run(cmd: str, cwd: str, env: dict) -> tuple[int, bytes]:
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, capture_output=True, timeout=30)
    return p.returncode, p.stdout


def _need_git() -> None:
    assert shutil.which("git"), "git is required for this sim"


_ESC = {7: "a", 8: "b", 9: "t", 10: "n", 11: "v", 12: "f", 13: "r", 34: '"', 92: "\\"}


def decode_quotepath(text: str) -> str:
    """The decode alternative of #227: octal escapes inside git's quoted names that form valid UTF-8 become the characters; invalid ones stay escaped."""
    def one(m: re.Match) -> str:
        body = m.group(1)
        if not re.search(r"\\[0-3][0-7]{2}", body):
            return m.group(0)
        raw, i = bytearray(), 0
        while i < len(body):
            c = body[i]
            if c != "\\":
                raw += c.encode("utf-8")
                i += 1
            elif re.fullmatch(r"[0-3][0-7]{2}", body[i + 1 : i + 4]):
                raw.append(int(body[i + 1 : i + 4], 8))
                i += 4
            else:
                esc = {v: k for k, v in _ESC.items()}.get(body[i + 1 : i + 2])
                if esc is None:
                    return m.group(0)
                raw.append(esc)
                i += 2
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            return m.group(0)
        if not any(ord(ch) < 32 or ord(ch) == 127 or ch in '"\\ ' for ch in name):
            return name  # plain name: git leaves it unquoted under core.quotepath=false (status also quotes names with a space, so those keep their quotes)
        return '"' + "".join("\\" + _ESC[ord(ch)] if ord(ch) in _ESC else ("\\%03o" % ord(ch) if ord(ch) < 32 or ord(ch) == 127 else ch) for ch in name) + '"'

    return re.sub(r'"((?:[^"\\\n]|\\.)*)"', one, text)


def _unq(s: str) -> str:
    """C-style unquote of one git path with CPython's own escape decoder (independent of decode_quotepath)."""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return codecs.escape_decode(s[1:-1].encode("utf-8"))[0].decode("utf-8")
    return s


def _canon(cmd: str, text: str) -> list[str]:
    """Output lines with every path unquoted, so differently quoted spellings of the same names compare equal (stat column padding dropped)."""
    out = []
    for l in text.split("\n"):
        m = re.match(r"( .*?)(\s+\|.*)$", l)
        if cmd.startswith("git status") and len(l) > 3:
            out.append(l[:3] + _unq(l[3:]))
        elif "--stat" in cmd and m:
            out.append(_unq(m.group(1)[1:]) + m.group(2).lstrip())
        else:
            out.append(_unq(l))
    return out


# ---------------------------------------------------------------------------------------------------------------
# #227 core.quotepath=false through PreToolUse, with the decode alternative as the exact fallback.

PACK227 = Pack(flags=(FlagAdd(_fs("git"), ("-c", "core.quotepath=false"), _fs("core.quotepath")),))


def sim_quotepath227() -> dict:
    _need_git()
    sh = _Shell(["git"], [])
    try:
        match = ["git status --short", "git ls-files | head -5", "cd app && git diff --stat", "git -C sub log --stat", "git status && git ls-files", "git status --fail || echo failed"]
        _engine_checks(PACK227, DOC, match, ["echo git", "ls", "gitk --all", "cat .gitignore"], [], sh, [])
    finally:
        sh.close()
    for c in ("git -c core.quotepath=true status", "git -c core.quotepath=false status", "git config core.quotepath true", "git config --get core.quotepath"):
        assert rewrite(c, PACK227, {}) == c, f"the user already decided: {c!r}"
    assert rewrite("git status", PACK227, {}) == "git -c core.quotepath=false status"
    _check_hook(lambda c, d: rewrite(c, PACK227, {}), "git status", "ls")

    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "repo")
        os.mkdir(repo)
        env = _git_env(d)
        names = ["café.txt", "日本語.md", "naïve dir/ü.py", "tab\tname.txt", "plain.txt"]
        _run("git init -q -b main .", repo, env)
        for n in names:
            os.makedirs(os.path.dirname(os.path.join(repo, n)) or repo, exist_ok=True)
            with open(os.path.join(repo, n), "w", encoding="utf-8") as fh:
                fh.write("one\n")
        _run("git add -A && git commit -qm 'add files'", repo, env)
        for n in names:
            with open(os.path.join(repo, n), "a", encoding="utf-8") as fh:
                fh.write("two\n")
        with open(os.path.join(repo, "новый.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        b0 = b1 = t0 = t1 = 0
        for cmd in ("git status --short", "git ls-files", "git log --name-only --format=%s", "git diff --stat", "git ls-files -o --exclude-standard"):
            rc0, o0 = _run(cmd, repo, env)
            new = rewrite(cmd, PACK227, {})
            rc1, o1 = _run(new, repo, env)
            assert rc0 == rc1 == 0 and new != cmd
            x0, x1 = o0.decode("utf-8"), o1.decode("utf-8")
            assert re.search(r"\\[0-3][0-7]{2}", x0), f"default git should escape non-ASCII names: {cmd}"
            assert not re.search(r"\\[0-3][0-7]{2}", x1), f"rewritten command still prints octal escapes: {cmd}"
            assert '"tab\\tname.txt"' in x1 or "tab" not in x1, "names with control characters stay quoted either way"
            assert _canon(cmd, x0) == _canon(cmd, x1) == _canon(cmd, decode_quotepath(x0)), f"the three forms must name the same files: {cmd}"
            assert not re.search(r"\\[0-3][0-7]{2}", decode_quotepath(x0)), "the decode alternative must remove every escape of a valid UTF-8 name"
            b0, b1, t0, t1 = b0 + len(o0), b1 + len(o1), t0 + tokens(x0), t1 + tokens(x1)
        assert b1 < b0 and t1 < t0

        # the one place the injection is lossy: a name that is not valid UTF-8 (the decode alternative keeps it exact)
        bad = os.path.join(d, "bad")
        os.mkdir(bad)
        _run("git init -q -b main .", bad, env)
        with open(os.path.join(os.fsencode(bad), b"caf\xe9.txt"), "wb") as fh:
            fh.write(b"x")
        _rc, o0 = _run("git ls-files -o", bad, env)
        _rc, o1 = _run(rewrite("git ls-files -o", PACK227, {}), bad, env)
        assert o0 == b'"caf\\351.txt"\n' and o1 == b"caf\xe9.txt\n"
        assert "�" in o1.decode("utf-8", "replace"), "the raw byte cannot survive text decoding: the model could not name this file"
        assert decode_quotepath(o0.decode()) == o0.decode(), "the decode alternative leaves invalid UTF-8 escaped, so nothing is lost"
    return {"bytes_default": b0, "bytes_quotepath_off": b1, "tokens_default": t0, "tokens_quotepath_off": t1, "token_saving_pct": round(100 * (1 - t1 / t0), 1)}


case(
    227,
    claim="'git -c core.quotepath=false' is inserted after 'git' unless the command already sets core.quotepath; on a real repository the output has the characters instead of octal escapes (same names as decoding the escapes, control-character names stay quoted), costs fewer tokens, and the one loss, a file name that is not valid UTF-8, is shown to be avoided by the decode alternative.",
    sim=sim_quotepath227,
)

# ---------------------------------------------------------------------------------------------------------------
# #486 Quiet git config through GIT_CONFIG_COUNT. The hook probes the user's config first: an env override beats ~/.gitconfig and .git/config.

_QUIET486 = (("advice.statusHints", "false"), ("status.short", "true"), ("status.branch", "true"))


def plan486(cmd: str, env: dict, probe) -> tuple[str, dict]:
    try:
        if not cmd.strip() or "$?" in cmd:
            return cmd, {}
        cmds = _commands(cmd)
        if cmds is None or not any(p == "git" for p, _t, _a in cmds):
            return cmd, {}
        if any(k.startswith("GIT_CONFIG") for k in env) or "GIT_CONFIG" in cmd:
            return cmd, {}  # the user already drives git through the environment: never merge into it
        keep = []
        for k, v in _QUIET486:
            try:
                if probe(k) is None:
                    keep.append((k, v))
            except Exception:  # noqa: BLE001  cannot tell whether the user set it: leave it alone
                pass
        if not keep:
            return cmd, {}
        vars_ = [("GIT_CONFIG_COUNT", str(len(keep)))] + [(f"GIT_CONFIG_KEY_{i}", k) for i, (k, _v) in enumerate(keep)] + [(f"GIT_CONFIG_VALUE_{i}", v) for i, (_k, v) in enumerate(keep)]
        prefix, chosen = _export_prefix(cmd, [EnvRule(_fs("git"), tuple(vars_), atomic=True)], env)
        return prefix + cmd, chosen
    except Exception:  # noqa: BLE001
        return cmd, {}


def _git_probe(cwd: str, env: dict):
    def probe(key: str):
        r = subprocess.run(["git", "config", "--get", key], cwd=cwd, env=env, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
        if r.returncode == 1:
            return None
        raise RuntimeError(r.stderr)

    return probe


def _parse_long(text: str) -> dict:
    out: dict[str, list[str]] = {}
    section = None
    labels = {"new file": "A", "modified": "M", "deleted": "D", "renamed": "R", "copied": "C", "typechange": "T"}
    for line in text.split("\n"):
        if line.startswith("Changes to be committed"):
            section = "staged"
        elif line.startswith("Changes not staged"):
            section = "unstaged"
        elif line.startswith("Untracked files"):
            section = "untracked"
        elif line.startswith("\t"):
            if section == "untracked":
                out[line[1:]] = ["?", "?"]
            else:
                label, _, path = line[1:].partition(":")
                cur = out.setdefault(path.strip(), [" ", " "])
                cur[0 if section == "staged" else 1] = labels[label.strip()]
    return {k: "".join(v) for k, v in out.items()}


def _parse_short(text: str) -> dict:
    return {l[3:]: l[:2] for l in text.split("\n") if l and not l.startswith("##")}


def sim_gitcfg486() -> dict:
    _need_git()
    ver = tuple(int(x) for x in re.match(r"git version (\d+)\.(\d+)", subprocess.run(["git", "--version"], capture_output=True, text=True).stdout).groups())
    assert ver >= (2, 31), "GIT_CONFIG_COUNT needs git 2.31"
    with tempfile.TemporaryDirectory() as d:
        def mk(name: str) -> tuple[str, dict]:
            repo = os.path.join(d, name)
            os.mkdir(repo)
            env = _git_env(d)
            _run("git init -q -b main .", repo, env)
            return repo, env

        def put(repo: str, name: str, text: str = "one\n") -> None:
            with open(os.path.join(repo, name), "a") as fh:
                fh.write(text)

        def go(cmd: str, repo: str, env: dict, probe_env: dict | None = None) -> str:
            new, chosen = plan486(cmd, probe_env if probe_env is not None else {}, _git_probe(repo, env))
            rc, out = _run(new, repo, env)
            assert rc == 0, (cmd, rc, out)
            return out.decode()

        # (1) the idea's own measurement: a two-file status
        repo, env = mk("two")
        put(repo, "a.py")
        _run("git add . && git commit -qm init", repo, env)
        put(repo, "a.py", "two\n")
        put(repo, "new.py")
        long_out = _run("git status", repo, env)[1].decode()
        short_out = go("git status", repo, env)
        assert short_out != long_out and len(short_out) < 0.3 * len(long_out), (len(short_out), len(long_out))
        assert _parse_long(long_out) == _parse_short(short_out) == {"a.py": " M", "new.py": "??"}
        saved = {"status_chars_before": len(long_out), "status_chars_after": len(short_out)}

        # (2) reader view on a repository with every common state: the same path -> state map in both forms
        repo, env = mk("states")
        for n in ("mod.txt", "both.txt", "del.txt", "old.txt"):
            put(repo, n)
        _run("git add . && git commit -qm init", repo, env)
        put(repo, "mod.txt", "x\n")
        put(repo, "both.txt", "x\n")
        _run("git add both.txt && git mv old.txt renamed.txt", repo, env)
        put(repo, "both.txt", "y\n")
        put(repo, "added.txt", "a completely different file\n")
        _run("git add added.txt", repo, env)
        os.remove(os.path.join(repo, "del.txt"))
        put(repo, "untracked.txt")
        long_out = _run("git status", repo, env)[1].decode()
        short_out = go("git status", repo, env)
        want = {"mod.txt": " M", "both.txt": "MM", "del.txt": " D", "old.txt -> renamed.txt": "R ", "added.txt": "A ", "untracked.txt": "??"}
        assert _parse_long(long_out) == _parse_short(short_out) == want, (_parse_long(long_out), _parse_short(short_out))
        assert re.search(r"^## main$", short_out, re.M) and "On branch main" in long_out, "branch name is kept"

        # (3) what the short form does NOT carry: say so, so the claim stays honest
        _run("git stash -q -u", repo, env)
        head = _run("git rev-parse --short HEAD", repo, env)[1].decode().strip()
        _run("git checkout -q --detach", repo, env)
        long_out = _run("git status", repo, env)[1].decode()
        short_out = go("git status", repo, env)
        assert head in long_out and head not in short_out, "detached HEAD: the commit id is only in the long form"
        _run("git checkout -q main", repo, env)
        _run("git checkout -q -b other && git commit -q --allow-empty -m x", repo, env)
        put(repo, "c.txt", "A\n")
        _run("git add c.txt && git commit -qm A", repo, env)
        _run("git checkout -q main", repo, env)
        put(repo, "c.txt", "B\n")
        _run("git add c.txt && git commit -qm B", repo, env)
        _run("git merge other", repo, env)
        long_out = _run("git status", repo, env)[1].decode()
        short_out = go("git status", repo, env)
        assert "You have unmerged paths" in long_out and "unmerged" not in short_out.lower() and _parse_short(short_out).get("c.txt") == "AA", (short_out,)
        _run("git merge --abort", repo, env)

        # (4) never override: a status.short the user set in the repo or in ~/.gitconfig is kept (an env override would beat both)
        repo, env = mk("userset")
        put(repo, "a.py")
        _run("git add . && git commit -qm init", repo, env)
        put(repo, "b.py")
        _run("git config status.short false", repo, env)
        naive = _run("export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=status.short GIT_CONFIG_VALUE_0=true; git status", repo, env)[1].decode()
        assert "?? b.py" in naive and not naive.startswith("On branch"), "an env override beats the user's repo config, which is why the hook probes first"
        cmd, chosen = plan486("git status", {}, _git_probe(repo, env))
        assert chosen["GIT_CONFIG_COUNT"] == "2" and "status.short" not in chosen.values(), chosen
        out = go("git status", repo, env)
        assert out.startswith("On branch main") and "(use \"git" not in out, "long format kept, hints still removed"
        gfile = os.path.join(d, "global.cfg")
        with open(gfile, "w") as fh:
            fh.write("[status]\n\tshort = false\n")
        repo2, _ = mk("userglobal")
        env2 = _git_env(d, gfile)
        put(repo2, "a.py")
        assert "status.short" not in plan486("git status", {}, _git_probe(repo2, env2))[1].values()
        # the user already uses GIT_CONFIG_COUNT: untouched, and their value is what git sees
        user_env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "status.short", "GIT_CONFIG_VALUE_0": "false"}
        assert plan486("git status", user_env, _git_probe(repo, env)) == ("git status", {})
        assert plan486("GIT_CONFIG_COUNT=0 git status", {}, _git_probe(repo, env)) == ("GIT_CONFIG_COUNT=0 git status", {})
        # explicit flags and machine formats are not changed by the config
        repo, env = mk("flags")
        put(repo, "a.py")
        _run("git add . && git commit -qm init", repo, env)
        put(repo, "a.py", "two\n")
        put(repo, "u.py")
        assert go("git status --long", repo, env).startswith("On branch main"), "an explicit --long wins over status.short"
        assert go("git status --porcelain", repo, env) == _run("git status --porcelain", repo, env)[1].decode(), "porcelain output must not change"
        for other in ("git log --oneline", "git diff --stat", "git branch", "git rev-parse HEAD"):
            assert go(other, repo, env) == _run(other, repo, env)[1].decode(), f"{other} must not change"
        # fail open
        def broken(key):
            raise OSError("git not runnable")
        assert plan486("git status", {}, broken) == ("git status", {})
        for c in ("ls", "echo git status", "git status <<EOF\nx\nEOF", "git status; echo $?", ""):
            assert plan486(c, {}, _git_probe(repo, env)) == (c, {}), c
        # hook shape, with the working directory taken from the hook input
        probe = lambda c, data: plan486(c, {}, _git_probe(data.get("cwd", repo), env))[0]  # noqa: E731
        out, rc = pretool_hook(json.dumps({"tool_name": "Bash", "cwd": repo, "tool_input": {"command": "git status", "timeout": 1000}}), probe)
        assert rc == 0 and json.loads(out)["hookSpecificOutput"]["updatedInput"]["timeout"] == 1000 and "permissionDecision" not in json.loads(out)["hookSpecificOutput"]
        assert pretool_hook(json.dumps({"tool_name": "Bash", "cwd": repo, "tool_input": {"command": "ls"}}), probe) == ("", 0)
    return saved


case(
    486,
    claim="Before a git command the hook exports GIT_CONFIG_COUNT/KEY/VALUE for advice.statusHints=false, status.short and status.branch, skipping every key the user already configured (an env override beats repo and global config) and doing nothing when the user already uses GIT_CONFIG_*; on a real repository the short status carries the same path-to-state map as the long one, but not the detached-HEAD commit id or the 'unmerged paths' notice.",
    sim=sim_gitcfg486,
)
