"""Terminal and text-hygiene ideas: #9 ANSI/OSC strip, #11 line endings and invisibles, #13 dedent, #14 whitespace
tokenization study, #15 glyph substitution, #16 stdout/stderr duplicates, #17 repeated failure blocks, #18 NFC,
#332 UTF-16 decode, #517 tree rails, #614 ASCII bars, #616 backspace overstrike, #808 struct-dump join,
#989 non-Latin digit fold, #1035 adjacent text blocks, #1342 double space after a period.

Transform ideas are ordinary cases (encode + decode or an independent oracle). Two ideas, #614 and #989, replace
one glyph by one ASCII character, so their saving is in UTF-8 bytes and tokens but never in characters; the
harness gate counts characters, so they run as sims that apply the same rules (total, fidelity, never grows,
leaves negatives alone, idempotent, ~40 mutations per fixture) with bytes as the unit. See `_run_bytes_gate`.
"""
import itertools
import json
import re
import unicodedata
import importlib.util
import os

import harness
from harness import Fixture, case

ESC, BEL = "\x1b", "\x07"
ST = ESC + "\\"


def _sgr(code: str, s: str) -> str:
    return f"{ESC}[{code}m{s}{ESC}[0m"


def _lines(text: str) -> list[str]:
    """Split on \\n only (str.splitlines also splits on \\x0b, \\x1c, \\u2028 ... which would hide bugs)."""
    return text.split("\n")


# ---------------------------------------------------------------------------------------------------------------
# a bytes-gated runner for the two ideas whose saving is not in characters (see module docstring)

def _run_bytes_gate(name, encode, equivalent, fixtures, min_gain, mutations=40, seed=0):
    """Same rules as harness._check_one/_run_transform, with len(s.encode()) as the size. Raises AssertionError.
    Returns {'bytes_gain_pct': mean gain on shrinking fixtures, 'token_gain_pct': mean token gain, 'mutants': n}."""
    size = lambda s: len(s.encode("utf-8", "surrogatepass"))  # noqa: E731
    assert any(f.shrinks for f in fixtures), f"{name}: no shrinking fixture"
    assert any(f.shrinks is False for f in fixtures), f"{name}: no negative fixture"
    bg, tg, nm = [], [], 0

    def check(x, strict, label):
        try:
            e = encode(x)
        except Exception as ex:  # noqa: BLE001
            raise AssertionError(f"{name} [{label}] encode raised {type(ex).__name__}: {ex}") from None
        assert isinstance(e, str), f"{name} [{label}] encode returned {type(e).__name__}"
        assert equivalent(x, e), f"{name} [{label}] oracle says information changed: {x[:80]!r} -> {e[:80]!r}"
        if strict:
            assert size(e) <= size(x), f"{name} [{label}] grew {size(x)} -> {size(e)} bytes"
            assert encode(e) == e, f"{name} [{label}] not idempotent: {e[:80]!r}"
        return e

    for n, f in enumerate(fixtures):
        e = check(f.text, f.shrinks is not None, f.name)
        gain = 1 - size(e) / size(f.text) if f.text else 0.0
        if f.shrinks:
            assert gain >= min_gain, f"{name} [{f.name}] byte gain {gain:.1%} < min_gain {min_gain:.0%}"
            bg.append(gain)
            tg.append(1 - harness.tokens(e) / max(1, harness.tokens(f.text)))
        elif f.shrinks is False:
            assert e == f.text, f"{name} [{f.name}] must be left alone but changed: {f.text[:80]!r} -> {e[:80]!r}"
        for i, m in enumerate(harness.mutants(f.text, mutations, seed=seed * 1009 + n)):
            nm += 1
            check(m, f.shrinks is not None, f"{f.name} mutant {i}")
    return {"bytes_gain_pct": round(100 * sum(bg) / len(bg), 1), "token_gain_pct": round(100 * sum(tg) / len(tg), 1),
            "mutants_checked": nm}

# ---------------------------------------------------------------------------------------------------------------
# #9 Strip ANSI and OSC escapes. OSC-8 hyperlink targets are kept as "text (uri)" unless the visible text IS the uri.
# Every ESC and its sequence goes, including truncated ones (a terminal would also swallow them), so the output
# never contains ESC and a second pass has nothing to splice or re-read.

_ESC_SEQ = re.compile(
    r"\x1b(?:"
    r"\](?P<osc>[^\x07\x1b\r\n]{0,2048}?)(?:\x07|\x1b\\)"        # OSC ... BEL | ST (same line only)
    r"|[PX^_][^\x07\x1b\r\n]{0,2048}?(?:\x07|\x1b\\)"            # DCS / SOS / PM / APC strings
    r"|\[[0-?]*[ -/]*[@-~]?"                                       # CSI, final byte optional (truncated output)
    r"|[ -/]*[0-~]?"                                               # two-byte escapes; lone ESC; unterminated OSC introducer
    r")"
)


def strip_ansi(text: str) -> str:
    if ESC not in text:
        return text
    out: list[str] = []
    pos = 0
    link: tuple[str, int] | None = None  # (uri, index in `out` where the link text starts)

    def close() -> None:
        nonlocal link
        if link is None:
            return
        uri, start = link
        link = None
        seg = "".join(out[start:])
        if seg == uri:
            return  # the visible text already shows the target
        out.append(uri if seg == "" else f" ({uri})")

    for m in _ESC_SEQ.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        body = m.group("osc")
        if body is not None and body.startswith("8;"):
            parts = body.split(";", 2)  # 8 ; params ; URI   (a URI may itself contain ';')
            if len(parts) == 3:
                close()
                if parts[2]:
                    link = (parts[2], len(out))
    out.append(text[pos:])
    close()
    res = "".join(out)
    return res if len(res) < len(text) else text


def _render_plain(s: str) -> str:
    """Oracle: a character-at-a-time scanner (no regex) that renders what a terminal would show, with link targets."""
    out: list[str] = []
    i, n = 0, len(s)
    link = None  # (uri, len(out) at open)

    def close():
        nonlocal link
        if link:
            uri, start = link
            link = None
            seg = "".join(out[start:])
            if seg != uri:
                out.append(uri if not seg else " (" + uri + ")")

    while i < n:
        c = s[i]
        if c != ESC:
            out.append(c)
            i += 1
            continue
        j = i + 1
        nxt = s[j] if j < n else ""
        if nxt == "[":
            k = j + 1
            while k < n and "0" <= s[k] <= "?":
                k += 1
            while k < n and " " <= s[k] <= "/":
                k += 1
            if k < n and "@" <= s[k] <= "~":
                k += 1
            i = k
        elif nxt and nxt in "]PX^_":
            k, start, term = j + 1, j + 1, 0
            while k < n and k - start <= 2048:
                ch = s[k]
                if ch == BEL:
                    term = 1
                    break
                if ch == ESC:
                    term = 2 if s[k + 1:k + 2] == "\\" else 0
                    break
                if ch in "\r\n":
                    break
                k += 1
            if term and k - start <= 2048:
                if nxt == "]":
                    body = s[start:k]
                    if body.startswith("8;") and body.count(";") >= 2:
                        _, _, uri = body.split(";", 2)
                        close()
                        if uri:
                            link = (uri, len(out))
                i = k + term
            else:
                i = j + 1  # unterminated: drop ESC and the introducer, keep what follows as text
        else:
            k = j
            while k < n and " " <= s[k] <= "/":
                k += 1
            if k < n and "0" <= s[k] <= "~":
                k += 1
            i = k
    close()
    return "".join(out)


_KNOWN_9 = [
    ("a\x1b[31mred\x1b[0m b", "a" + "red b"),
    ("\x1b]8;;http://x/y\x07go\x1b]8;;\x07!", "go (http://x/y)!"),
    ("\x1b]8;id=3;http://x/y\x1b\\http://x/y\x1b]8;;\x1b\\", "http://x/y"),
    ("\x1b]0;title\x07body", "body"),
    ("p\x1b]8;;u;v\x07t\x1b]8;;\x07", "pt (u;v)"),
    ("\x1b[3\x1b[1m1m", "1m"),
]


def _oracle_9(x: str, e: str) -> bool:
    for src, want in _KNOWN_9:  # the oracle checks itself first, so a wrong oracle cannot pass a wrong encoder
        assert _render_plain(src) == want, f"oracle self-check failed on {src!r}"
    assert ESC not in e, "an ESC byte survived"
    return _render_plain(x) == e


def _cargo_gcc_ls() -> str:
    gh = "https://github.com/acme/widget/pull/4182"
    rows = []
    rows.append(f"\x1b]0;build@ci: ~/widget{BEL}\x1b[?25l")
    for n, (name, line, col) in enumerate([("count", 12, 9), ("buf", 31, 13), ("retries", 57, 5), ("timeout", 80, 17)]):
        rows += [
            _sgr("1;33", "warning") + _sgr("1", f": unused variable: `{name}`"),
            f" {_sgr('1;34', '-->')} src/net/client.rs:{line}:{col}",
            f"  {_sgr('1;34', '|')}",
            f"{_sgr('1;34', f'{line} |')}         let {name} = compute({n});",
            f"  {_sgr('1;34', '|')}             {_sgr('1;33', '^' * len(name))} {_sgr('1;33', f'help: if this is intentional, prefix it with an underscore: `_{name}`')}",
            f"  {_sgr('1;34', '=')} {_sgr('1', 'note')}: `#[warn(unused_variables)]` on by default",
            "",
        ]
    for flag in ("-Wunused-variable", "-Wformat", "-Wshadow"):
        url = f"https://gcc.gnu.org/onlinedocs/gcc/Warning-Options.html#index-{flag}"
        rows.append(f"{_sgr('01', 'src/main.c:5:9:')} {_sgr('01;35', 'warning:')} suspicious use [{ESC}]8;;{url}{ST}{flag}{ESC}]8;;{ST}]")
    for fn in ("README.md", "Cargo.toml", "src", "target"):
        colour = "01;34" if fn in ("src", "target") else "0"
        rows.append(f"-rw-r--r-- 1 ci ci 4096 Nov  3 09:12 {ESC}]8;;file://runner-07/home/ci/widget/{fn}{BEL}{_sgr(colour, fn)}{ESC}]8;;{BEL}")
    rows.append(f"{_sgr('32', 'Opened')} {ESC}]8;;{gh}{BEL}{gh}{ESC}]8;;{BEL} {_sgr('2', '(draft)')}")
    rows.append(f"progress: {ESC}[2K{ESC}[1A{ESC}[2K[{'=' * 20}] 100%{ESC}[?25h")
    return "\n".join(rows) + "\n"


_PLAIN_9 = (
    "Docs: in cat -v the colour code shows as ^[[31m and in JSON as \\u001b[31m; the literal text ESC[0m or [1;31m\n"
    "has no ESC byte, so it must stay as written. See https://example.com/a?b=1;c=2 (docs) and [x](http://y.z).\n"
)

case(
    9,
    claim="Removes every ESC sequence (colours, cursor moves, titles, truncated ones); an OSC-8 hyperlink becomes 'text (uri)' unless the visible text equals the uri, so link targets are kept.",
    loses="colours and styles, cursor movement, window titles, cwd and shell-integration marks (OSC 0/7/133), OSC-8 link params such as id=, and a truncated escape's partial bytes",
    encode=strip_ansi,
    equivalent=_oracle_9,
    fixtures=[
        Fixture("cargo + gcc + ls --hyperlink + gh", _cargo_gcc_ls(), True),
        Fixture("literal bracket codes, no ESC byte", _PLAIN_9, False),
        Fixture("link text equals url, SGR inside the text, url with ';'",
                f"{ESC}]8;;http://a/b{BEL}{_sgr('4', 'http://a/')}b{ESC}]8;;{BEL} {ESC}]8;;http://c/d;e{ST}x{ESC}]8;;{ST}", None),
        Fixture("unclosed link, nested links, empty text", f"{ESC}]8;;http://one{BEL}first {ESC}]8;;http://two{BEL}second {ESC}]8;;http://three{BEL}{ESC}]8;;{BEL} tail\n{ESC}]8;;http://dangling{BEL}end", None),
        Fixture("truncated and malformed escapes", "a\x1b[31\nb\x1b]8;;http://x\nc\x1b\x1b[1md\x1b[3\x1b[1m1me\x1b(Bf\x1b \x1bg\x1b", None),
        Fixture("text that already looks like the output", "name (http://x/y) and (x) done\n", None),
    ],
    min_gain=0.25,
)

# ---------------------------------------------------------------------------------------------------------------
# #11 Normalise line endings and invisibles. CRLF (and \r\r\n) to LF, trailing blanks, BOM, ZWSP, word joiner,
# soft hyphen, nbsp. ZWNJ/ZWJ are NOT removed (they change Persian words and emoji sequences). Unified diffs are left
# alone: a context line that is a single space, and trailing-whitespace errors, are content there.

_INVIS_DEL = {ord(c): None for c in "\u200b\u2060\ufeff\u00ad"}
_NBSP_MAP = {0xA0: " ", 0x202F: " "}
_CRLF = re.compile(r"\r+\n")
_TRAIL = re.compile(r"[ \t\r]+$", re.M)
_DIFF_RE = re.compile(r"^(?:@@ -\d|diff --git |--- a/|\+\+\+ b/|Index: )", re.M)


def normalise_text(text: str) -> str:
    if _DIFF_RE.search(text):
        return text
    out = text.translate({**_INVIS_DEL, **_NBSP_MAP})  # invisibles first: "\r\u200b\n" must become a CRLF
    out = _TRAIL.sub("", _CRLF.sub("\n", out))
    return out if len(out) < len(text) else text


_DROP_11, _SPACE_11 = set("\u200b\u2060\ufeff\u00ad"), set("\u00a0\u202f")


def _view_11(s: str) -> list[str]:
    """Oracle view: per line, visible characters with blanks mapped and trailing blank/CR dropped."""
    lines, cur = [], []

    def flush():
        while cur and cur[-1] in " \t\r":
            cur.pop()
        lines.append("".join(cur))
        cur.clear()

    for ch in s:
        if ch in _DROP_11:
            continue
        if ch == "\n":
            flush()
        else:
            cur.append(" " if ch in _SPACE_11 else ch)
    flush()
    return lines


def _is_diff(s: str) -> bool:
    for l in _lines(s):
        if l.startswith(("diff --git ", "--- a/", "+++ b/", "Index: ")) or (l.startswith("@@ -") and l[4:5].isdecimal()):
            return True
    return False


def _oracle_11(x: str, e: str) -> bool:
    if _is_diff(x):
        return e == x
    if _view_11(x) != _view_11(e):
        return False
    if e == x:
        return True
    return not any(c in _DROP_11 or c in _SPACE_11 for c in e) and not any(l.endswith((" ", "\t", "\r")) for l in _lines(e))


def _powershell() -> str:
    rows = [("Handles", "NPM(K)", "PM(K)", "WS(K)", "CPU(s)", "Id", "ProcessName")]
    for i, name in enumerate(["svchost", "explorer", "chrome", "Code", "powershell", "node", "dwm", "SearchHost"]):
        rows.append((str(300 + i * 41), str(18 + i), str(9000 + i * 1371), f"{21000 + i * 2202}", f"{3.5 + i * 11.25:.2f}", str(900 + i * 77), name))
    w = [10, 8, 10, 10, 10, 8, 20]
    body = "\r\n".join("".join(c.ljust(n) for c, n in zip(r, w)) for r in rows)
    body += "\r\n\r\nTotal\u00a0memory:\u00a01\u202f234\u202f567 K\u200b   \r\n"
    return "\ufeff" + body


_PERSIAN_EMOJI = "\n".join([
    "می\u200cخواهم کتاب\u200cها را بخوانم",  # ZWNJ is part of the spelling
    "team \U0001F469\u200d\U0001F4BB shipped \U0001F468\u200d\U0001F469\u200d\U0001F467 the fix",       # ZWJ builds one emoji
    "plain line",
]) + "\n"

_DIFF_TEXT = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n context  \n \n-old line\n+new line   \r\n tail\n"

case(
    11,
    claim="CRLF and CR-CR-LF become LF, trailing blanks and CRs are removed, BOM/ZWSP/word-joiner/soft-hyphen are deleted and nbsp becomes a space; ZWNJ/ZWJ and unified diffs are left byte-identical.",
    loses="CR count before LF, trailing whitespace (so a Markdown two-space hard break is lost), invisible characters used as markers, nbsp-versus-space distinction",
    encode=normalise_text,
    equivalent=_oracle_11,
    fixtures=[
        Fixture("PowerShell table: BOM, CRLF, padding, nbsp, ZWSP", _powershell(), True),
        Fixture("Persian ZWNJ and emoji ZWJ, already clean", _PERSIAN_EMOJI, False),
        Fixture("unified diff with trailing-space context lines", _DIFF_TEXT, False),
        Fixture("CR CR LF, lone CR, CR after blanks, blank-only text", "a\r\r\nb \r \nprogress 10%\r20%\r30%\n   \t \n\r", None),
        Fixture("invisible between CR and LF", "x\r\u200b\ny\u200b \u00a0\n\ufeff", None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #13 Dedent common leading whitespace: display-only blocks. A source-code excerpt (what `sed -n` or Read returns before
# an edit) must keep its exact indentation, so anything that looks like code, a line-numbered file view or a diff is left alone.

_CODE_START = re.compile(
    r"^\s*(?:\d+[\t:|→]\s?)?(?:(?:def|class|import|from\s+\S+\s+import|async|fn|func|function|const|let|var|pub|public|private|"
    r"protected|static|package|use|struct|impl|interface|enum|return|raise|yield|break|continue|elif|else|try|except|finally|with|"
    r"switch|case|#include|#define|@\w+)\b|(?:if|for|while)\b.*[:{]\s*$)", re.M)
_CODE_END = re.compile(r"[;{}]\s*$", re.M)
_NUMBERED = re.compile(r"^\s*\d+[\t→]", re.M)


def _looks_like_source(text: str) -> bool:
    if _CODE_START.search(text) or _CODE_END.search(text) or _DIFF_RE.search(text):
        return True
    nonblank = [l for l in _lines(text) if l.strip()]
    return bool(nonblank) and sum(bool(_NUMBERED.match(l)) for l in nonblank) * 2 >= len(nonblank)


def dedent_display(text: str) -> str:
    if _looks_like_source(text):
        return text
    lines = _lines(text)
    body = [l for l in lines if l.strip(" \t\r")]
    if len(body) < 3:
        return text
    margin = os_commonprefix([l[: len(l) - len(l.lstrip(" \t"))] for l in body])
    if len(margin) < 2:
        return text
    res = "\n".join(l[len(margin):] if l.strip(" \t\r") else l for l in lines)
    return res if len(res) < len(text) else text


def os_commonprefix(items: list[str]) -> str:
    a, b = min(items), max(items)
    for i, ch in enumerate(a):
        if ch != b[i]:
            return a[:i]
    return a


def _oracle_13(x: str, e: str) -> bool:
    """Same text with one whitespace-only prefix removed from every non-blank line (and nothing else changed)."""
    if e == x:
        return True
    xl, el = _lines(x), _lines(e)
    if len(xl) != len(el):
        return False
    removed_all = None
    for a, b in zip(xl, el):
        if not a.strip(" \t\r"):
            if a != b:
                return False
            continue
        if not a.endswith(b):
            return False
        gone = a[: len(a) - len(b)]
        if gone.strip(" \t"):
            return False
        if removed_all is None:
            removed_all = gone
        elif gone != removed_all:
            return False
    flush_line = any(l.strip(" \t\r") and l[0] not in " \t" for l in el)
    return bool(removed_all) and flush_line  # something was removed and the block now starts at column 0


def _describe_pod(indent: str) -> str:
    base = """Name:             web-7d9f8c6b5-x2k4p
Namespace:        prod
Node:             ip-10-0-3-17.ec2.internal/10.0.3.17
Start Time:       Mon, 02 Nov 2026 08:14:09 +0000
Status:           Running
IP:               10.0.3.88
Containers:
  web:
    Container ID:   containerd://3f2a9c0d17b84e5fa1c4d2e9b7a60c1188de
    Image:          registry.example.com/web:1.4.2
    Port:           8080/TCP
    State:          Running
      Started:      Mon, 02 Nov 2026 08:14:31 +0000
    Ready:          True
    Restart Count:  0
    Limits:
      cpu:     500m
      memory:  512Mi
    Environment:
      LOG_LEVEL:  info
Conditions:
  Type              Status
  Initialized       True
  Ready             True
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  3m    default-scheduler  Successfully assigned prod/web-7d9f8c6b5-x2k4p to ip-10-0-3-17
  Normal  Pulled     3m    kubelet            Container image already present on machine
"""
    return "".join((indent + l if l else l) + "\n" for l in base.split("\n")[:-1])


_PY_SNIPPET = """        def handle(self, request):
            if not request.user.is_authenticated:
                raise PermissionDenied("login required")
            items = self.queryset.filter(owner=request.user)
            return Response(serialize(items))
"""
_JS_SNIPPET = "    const total = items.reduce((a, b) => a + b.price, 0);\n    if (total > limit) {\n        notify(user);\n    }\n"
_CAT_N = "".join(f"{i:6d}\t{t}\n" for i, t in enumerate(["The cache is warm.", "Nothing to report.", "Runs nightly at 02:00.", "Owner: platform team", "See the wiki page for details."], 1))

case(
    13,
    claim="Removes the whitespace prefix shared by every non-blank line of an indented display block; relative indentation is kept, and source excerpts, line-numbered file views and diffs are left byte-identical.",
    loses="the absolute column offset of the block (relative indentation between lines is kept)",
    encode=dedent_display,
    equivalent=_oracle_13,
    fixtures=[
        Fixture("kubectl describe nested 8 spaces by a wrapper", _describe_pod("        "), True),
        Fixture("python excerpt from sed -n (source, edit target)", _PY_SNIPPET, False),
        Fixture("javascript excerpt with braces", _JS_SNIPPET, False),
        Fixture("cat -n of prose (file view)", _CAT_N, False),
        Fixture("already flush block", _describe_pod(""), False),
        Fixture("tab margin, whitespace-only lines, CRLF", "\t\talpha one\r\n\t\t\tbeta two\r\n  \t\r\n\t\tgamma three\r\n\t\tdelta four\r\n", None),
        Fixture("mixed tab/space margin", "\t  one\n\t  two\n\t   three\n\t  four\n", None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #15 Glyph substitution. Box-drawing glyphs to | - +, a horizontal rule line to '---', smart quotes and dashes to ASCII,
# check/cross/warning marks to [ok] [x] [!], braille spinner frames removed. Guards: a curly quote is only straightened on a
# line that has no ASCII quote of that kind (so JSON strings and shell quoting cannot be broken).

def _box_table() -> dict[int, str]:
    t: dict[int, str] = {}
    for cp in (0x2500, 0x2501, 0x2504, 0x2505, 0x2508, 0x2509, 0x254C, 0x254D, 0x2550, 0x2574, 0x2576, 0x2578, 0x257A, 0x257C, 0x257E):
        t[cp] = "-"
    for cp in (0x2502, 0x2503, 0x2506, 0x2507, 0x250A, 0x250B, 0x254E, 0x254F, 0x2551, 0x2575, 0x2577, 0x2579, 0x257B, 0x257D, 0x257F):
        t[cp] = "|"
    for cp in itertools.chain(range(0x250C, 0x254C), range(0x2552, 0x256D), range(0x256D, 0x2571)):
        t.setdefault(cp, "+")
    t[0x2571], t[0x2572], t[0x2573] = "/", "\\", "X"
    return t


_BOX = _box_table()
_RULE_KINDS = set("-+")
_MARK_RE = re.compile("([\u2713\u2714\u2611\u2705\u2717\u2718\u274c\u2612\u26a0])\ufe0f?")
_MARK_TXT = {**{c: "[ok]" for c in "\u2713\u2714\u2611\u2705"}, **{c: "[x]" for c in "\u2717\u2718\u274c\u2612"}, "\u26a0": "[!]"}
_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
_SPIN = re.compile("(^|\r)[\u2801-\u28ff] (?=\\S)", re.M)


def _glyph_line(line: str) -> str:
    body = line.lstrip(" ")
    if len(body.replace(" ", "")) >= 3 and all(c == " " or _BOX.get(ord(c)) in _RULE_KINDS for c in body):
        return line[: len(line) - len(body)] + "---"  # a rule line: keep only its indent
    t = {**_BOX, **_DASHES}
    if '"' not in line:
        t.update({0x201C: '"', 0x201D: '"', 0x201E: '"'})
    if "'" not in line:
        t.update({0x2018: "'", 0x2019: "'", 0x201A: "'"})
    return _MARK_RE.sub(lambda m: _MARK_TXT[m.group(1)], line).translate(t)


def glyph_to_ascii(text: str) -> str:
    out = "\n".join(_glyph_line(l) for l in _lines(text))
    while True:  # repeat: removing one frame can expose another at the start of the line
        nxt = _SPIN.sub(r"\1", out)
        if nxt == out:
            break
        out = nxt
    return out if len(out) < len(text) else text


def _box_kind(ch: str) -> str | None:
    """Oracle: classify a box-drawing glyph from its Unicode name instead of a codepoint table."""
    if not "\u2500" <= ch <= "\u257f":
        return None
    w = unicodedata.name(ch).split()
    if "DIAGONAL" in w:
        return "X" if "CROSS" in w else "/" if w[-2:] == ["LOWER", "LEFT"] else "\\"
    h = any(x in w for x in ("HORIZONTAL", "LEFT", "RIGHT"))
    v = any(x in w for x in ("VERTICAL", "UP", "DOWN"))
    return "+" if h and v else "-" if h else "|"


_OK_NAMES = {"CHECK MARK", "HEAVY CHECK MARK", "BALLOT BOX WITH CHECK", "WHITE HEAVY CHECK MARK"}
_X_NAMES = {"BALLOT X", "HEAVY BALLOT X", "CROSS MARK", "BALLOT BOX WITH X"}
_DASH_NAMES = {"HYPHEN", "NON-BREAKING HYPHEN", "FIGURE DASH", "EN DASH", "EM DASH", "HORIZONTAL BAR", "MINUS SIGN"}
_DQ_NAMES = {"LEFT DOUBLE QUOTATION MARK", "RIGHT DOUBLE QUOTATION MARK", "DOUBLE LOW-9 QUOTATION MARK"}
_SQ_NAMES = {"LEFT SINGLE QUOTATION MARK", "RIGHT SINGLE QUOTATION MARK", "SINGLE LOW-9 QUOTATION MARK"}


def _expected_line(line: str) -> str:
    body = line.lstrip(" ")
    boxes = [k for k in map(_box_kind, line) if k]
    if len(boxes) >= 3 and all(k in ("-", "+") for k in boxes) and all(c == " " or _box_kind(c) for c in body):
        return line[: len(line) - len(body)] + "---"
    dq, sq = '"' not in line, "'" not in line
    out, i = [], 0
    while i < len(line):
        c = line[i]
        name = unicodedata.name(c, "")
        mark = "[ok]" if name in _OK_NAMES else "[x]" if name in _X_NAMES else "[!]" if name == "WARNING SIGN" else None
        if mark:
            out.append(mark)
            i += 2 if line[i + 1:i + 2] == "\ufe0f" else 1  # a variation selector right after a mark goes with it
            continue
        if (k := _box_kind(c)) is not None:
            out.append(k)
        elif name in _DASH_NAMES:
            out.append("-")
        elif name in _DQ_NAMES and dq:
            out.append('"')
        elif name in _SQ_NAMES and sq:
            out.append("'")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _strip_spinners(s: str) -> str:
    res, i, at_start = [], 0, True
    while i < len(s):
        if at_start and "\u2801" <= s[i] <= "\u28ff" and s[i + 1:i + 2] == " " and s[i + 2:i + 3] and not s[i + 2].isspace():
            i += 2  # the start-of-line state is kept, so a second frame right behind the first goes too
            continue
        at_start = s[i] in "\r\n"
        res.append(s[i])
        i += 1
    return "".join(res)


def _oracle_15(x: str, e: str) -> bool:
    if e == x:
        return True
    return e == _strip_spinners("\n".join(_expected_line(l) for l in _lines(x)))


def _rich_table() -> str:
    rule = lambda l, m, r: l + m.join("─" * n for n in (12, 10, 14, 10)) + r  # noqa: E731
    rows = [("service", "status", "latency", "region"), ("api", "✓ healthy", "42 ms", "eu-west-1"), ("worker", "✓ healthy", "118 ms", "eu-west-1"),
            ("search", "⚠ degraded", "910 ms", "us-east-1"), ("billing", "✗ down", "-", "us-east-1"), ("auth", "✓ healthy", "37 ms", "ap-south-1")]
    out = ["Status report — production", rule("┌", "┬", "┐")]
    w = (12, 10, 14, 10)
    for i, r in enumerate(rows):
        out.append("│" + "│".join(f" {c:<{n - 1}}" for c, n in zip(r, w)) + "│")
        if i == 0:
            out.append(rule("├", "┼", "┤"))
    out.append(rule("└", "┴", "┘"))
    out += ["", "├── src", "│   ├── main.rs", "│   └── lib.rs", "└── Cargo.toml"]
    return "\n".join(out) + "\n"


def _spinner_log() -> str:
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    live = "".join(f"\r{frames[i % 10]} Fetching “web-app”" for i in range(30))
    return "npm info using npm@10.2.4\n" + live + "\r✓ Installed 312 packages – it’s done\n"


case(
    15,
    claim="Box-drawing glyphs become | - +, a pure horizontal rule line becomes '---', curly quotes/dashes/minus become ASCII (quotes only on lines without that ASCII quote), check/cross/warning marks become [ok] [x] [!], and lone braille spinner frames are dropped.",
    loses="glyph shape (light/heavy/double borders, quote and dash style), the width and junction positions of horizontal rules, which of several check/cross glyphs was used, spinner frames (a one-cell braille meter before a word is misread as a frame)",
    encode=glyph_to_ascii,
    equivalent=_oracle_15,
    fixtures=[
        Fixture("rich table with marks, em dash, tree", _rich_table(), True),
        Fixture("npm spinner frames and smart quotes", _spinner_log(), True),
        Fixture("json string with curly quotes on a line that has ASCII quotes", '{"title": "The “Best” of 2024", "n": 3}\n{"note": "it’s fine"}\n', None),
        Fixture("plain ASCII, ellipsis, bullets, braille meter (not a spinner)", "Loading... • item × 3\n⣿⣿⣿⡀⠀ 60% used\n| a | b |\n|---|---|\n", False),
        Fixture("every box-drawing glyph", "".join(chr(c) for c in range(0x2500, 0x2580)) + "\n┌─┬─┐ x\n  ╔══╗  \n", None),
        Fixture("both curly and ASCII quotes on one line", "say “hi” to \"bob\" and it’s 'ok' ‘x’\n“q” only\n", None),
        Fixture("literal [ok] next to a check, VS16, doubled spinner", "[ok] ✓\ufe0f ⚠\ufe0f x ✅\n\r⠋ ⠙ x\n⠋ ⠙ ⠼ \n", None),
    ],
    min_gain=0.06,
)

# ---------------------------------------------------------------------------------------------------------------
# #18 NFC-normalise Unicode. Only plain words (letters, marks, apostrophes, hyphens, edge punctuation) are normalised;
# a line that lists files or names a path, URL or address is left whole, because a Linux file system compares bytes.

_EDGE = ".,;:!?()[]{}\"“”«»‘’'…*-–—"


def _plain_word(tok: str) -> bool:
    core = tok.strip(_EDGE)
    return bool(core) and all(unicodedata.category(c)[0] in "LM" or c in "'’-" for c in core)


_PERMS = re.compile(r"[-dlcbps][-rwxsStT]{9}\b")
_EXT = re.compile(r"\.[^\W_]{1,8}$")


def _listing_like(line: str) -> bool:
    """A line that lists files or names a location: its bare words may be directory names, so none is touched."""
    return bool(_PERMS.match(line)) or any("/" in t or "@" in t or "\\" in t or _EXT.search(t) for t in line.split())


def nfc_words(text: str) -> str:
    out = []
    for line in _lines(text):
        if not line.isascii() and not _listing_like(line):
            parts = re.split(r"(\s+)", line)
            for i in range(0, len(parts), 2):
                tok = parts[i]
                if tok and not tok.isascii() and _plain_word(tok):
                    n = unicodedata.normalize("NFC", tok)
                    if len(n) < len(tok):
                        parts[i] = n
            line = "".join(parts)
        out.append(line)
    return "\n".join(out)


def _oracle_18(x: str, e: str) -> bool:
    if e == x:
        return True
    xl, el = _lines(x), _lines(e)
    if len(xl) != len(el):
        return False
    for a_line, b_line in zip(xl, el):
        if a_line == b_line:
            continue
        xs, es = a_line.split(), b_line.split()
        if len(xs) != len(es) or re.findall(r"\s+", a_line) != re.findall(r"\s+", b_line):
            return False
        if re.match(r"[-dlcbps][-rwxsStT]{9}\b", a_line) or any(re.search(r"[/@\\]|\.[^\W_]{1,8}$", t) for t in xs):
            return False  # a file listing or location line must come back byte-identical
        for a, b in zip(xs, es):
            if unicodedata.normalize("NFD", a) != unicodedata.normalize("NFD", b):  # canonically equivalent
                return False
            if a != b and any(c.isdigit() or c in "./\\@_=~$#%&+<>|^`:" for c in a.strip(_EDGE)):
                return False  # a changed token was a plain word, never an identifier
    return True


def _nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


_PROSE_18 = _nfd("\n".join([
    "Le café de l’école a été fermé après l’incendie; les élèves sont répartis dans trois bâtiments.",
    "Thủ tướng đã gặp các bạn ở Hà Nội và đưa ra những ý kiến rất quan trọng.",
    "한국어 문서는 맥에서 복사하면 자모가 분리된 형태로 들어옵니다.",
    "Zürich, München und Köln: größere Städte haben längere Übergänge.",
    "See /Users/josé/Documents/résumé.pdf and naïve@café.example for the originals.",
])) + "\n"
_LISTING_18 = _nfd("-rw-r--r-- 1 u u 120 Nov  3 café.txt\n-rw-r--r-- 1 u u  98 Nov  3 résumé.pdf\ndrwxr-xr-x 2 u u 4096 Nov  3 crème\n/home/u/été/notesé.md\nhttps://exemple.fr/café?x=é\n")

case(
    18,
    claim="Decomposed letters in plain words (letters, marks, apostrophes, hyphens, edge punctuation) are composed to NFC; lines that list files or contain a path, URL or address, and tokens that look like identifiers, keep their exact code points.",
    loses="the decomposed code-point form of ordinary words (canonically equivalent, but not byte-identical)",
    encode=nfc_words,
    equivalent=_oracle_18,
    fixtures=[
        Fixture("NFD prose: French, Vietnamese, Korean, German, a path and an address", _PROSE_18, True),
        Fixture("decomposed file names, paths and a URL (identity matters)", _LISTING_18, False),
        Fixture("already NFC prose", unicodedata.normalize("NFC", _PROSE_18), False),
        Fixture("lone mark, reordered marks, singleton, jamo, ZWJ", "\u0301 e\u0301\u0323 \u212b \u0344 \u1100\u1161\u11a8 \U0001F469\u200d\U0001F4BB \u0958 x\n", None),
    ],
    min_gain=0.08,
)
