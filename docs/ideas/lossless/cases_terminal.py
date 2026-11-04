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
    min_gain=0.2,
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
    "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645 \u06a9\u062a\u0627\u0628\u200c\u0647\u0627 \u0631\u0627 \u0628\u062e\u0648\u0627\u0646\u0645",  # ZWNJ is part of the spelling
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
    margin = os.path.commonprefix([l[: len(l) - len(l.lstrip(" \t"))] for l in body])
    if len(margin) < 2:
        return text
    res = "\n".join(l[len(margin):] if l.strip(" \t\r") else l for l in lines)
    return res if len(res) < len(text) else text


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
        Fixture("statements ending in semicolons, no keywords", "        x = foo(a, b);\n        y = x + 1;\n        bar(y);\n", False),
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

# ---------------------------------------------------------------------------------------------------------------
# #332 Decode UTF-16 text before the binary-stub decision. The tool result is a str in which every byte of the file became
# one character (U+0000..U+00FF): UTF-16LE "Hi" arrives as "H\x00i\x00" (BOM: "ÿþ" = FF FE). Detect a BOM or an alternating-NUL
# pattern, decode strictly, and prefix a one-line header so the original byte string is recoverable exactly.

_U16_HDR = {
    ("le", True): "[decoded from UTF-16LE, BOM removed]\n",
    ("le", False): "[decoded from UTF-16LE]\n",
    ("be", True): "[decoded from UTF-16BE, BOM removed]\n",
    ("be", False): "[decoded from UTF-16BE]\n",
}
_U16_RAW = "[text follows unchanged]\n"  # escape for input that already starts like our own header


def _u16_detect(b: bytes):
    """-> (endian, has_bom, body) or None."""
    if b[:2] == b"\xff\xfe":
        return "le", True, b[2:]
    if b[:2] == b"\xfe\xff":
        return "be", True, b[2:]
    if len(b) >= 16:
        lo, hi = b[0::2], b[1::2]
        text_like = lambda part: sum(0x20 <= v < 0x7F or v in (9, 10, 13) for v in part) * 10 >= len(part) * 9  # noqa: E731
        if hi.count(0) * 10 >= len(hi) * 9 and text_like(lo):
            return "le", False, b
        if lo.count(0) * 10 >= len(lo) * 9 and text_like(hi):
            return "be", False, b
    return None


def utf16_to_text(text: str) -> str:
    if text.startswith(_U16_RAW) or any(text.startswith(h) for h in _U16_HDR.values()):
        return _U16_RAW + text  # grows by design: otherwise decode could not tell this from an encoded text
    if len(text) < 16 or any(ord(c) > 0xFF for c in text):
        return text
    found = _u16_detect(text.encode("latin-1"))
    if found is None:
        return text
    endian, has_bom, body = found
    if len(body) % 2:
        return text  # cut in the middle of a character: do not guess
    try:
        dec = body.decode("utf-16-" + endian)  # strict: lone surrogates mean it is not UTF-16 text
    except UnicodeDecodeError:
        return text
    bad = sum(unicodedata.category(c) in ("Cs", "Co", "Cn") or (unicodedata.category(c) == "Cc" and c not in "\t\r\n") for c in dec)
    if "\x00" in dec or bad * 50 > len(dec):
        return text
    out = _U16_HDR[(endian, has_bom)] + dec
    return out if len(out) < len(text) else text


def text_to_utf16(text: str) -> str:
    if text.startswith(_U16_RAW):
        return text[len(_U16_RAW):]
    for (endian, has_bom), hdr in _U16_HDR.items():
        if text.startswith(hdr):
            raw = text[len(hdr):].encode("utf-16-" + endian, "surrogatepass")
            if has_bom:
                raw = (b"\xff\xfe" if endian == "le" else b"\xfe\xff") + raw
            return raw.decode("latin-1")
    return text


def _oracle_332(x: str, e: str) -> bool:
    """Independent: decode the UTF-16 code units by hand and compare with the decoded text's code units."""
    if e == x or e == _U16_RAW + x:
        return True
    for (endian, has_bom), hdr in _U16_HDR.items():
        if e.startswith(hdr):
            break
    else:
        return False
    body = e[len(hdr):]
    assert "\x00" not in body, "a NUL survived the decode, so the binary stub would still fire"
    raw = x.encode("latin-1")  # raises if the encoder converted a string that was not byte-per-char
    if has_bom:
        if raw[:2] != (b"\xff\xfe" if endian == "le" else b"\xfe\xff"):
            return False
        raw = raw[2:]
    if len(raw) % 2:
        return False
    units_x = [(raw[i] | raw[i + 1] << 8) if endian == "le" else (raw[i] << 8 | raw[i + 1]) for i in range(0, len(raw), 2)]
    units_e = []
    for ch in body:
        cp = ord(ch)
        if cp > 0xFFFF:
            cp -= 0x10000
            units_e += [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
        else:
            units_e.append(cp)
    return units_x == units_e


def _as_u16(s: str, endian="le", bom=True) -> str:
    raw = s.encode("utf-16-" + endian)
    if bom:
        raw = (b"\xff\xfe" if endian == "le" else b"\xfe\xff") + raw
    return raw.decode("latin-1")


_REG = "Windows Registry Editor Version 5.00\r\n\r\n" + "".join(
    f'[HKEY_LOCAL_MACHINE\\SOFTWARE\\Acme\\Widget\\Module{i}]\r\n"Version"="4.2.{i}"\r\n"InstallPath"="C:\\\\Program Files\\\\Acme\\\\Widget{i}"\r\n"Enabled"=dword:00000001\r\n\r\n'
    for i in range(14))
_SQL = "SET NOCOUNT ON;\r\n" + "".join(f"INSERT INTO dbo.Orders (Id, Customer, Total) VALUES ({i}, 'Customer {i}', {i * 13}.50);\r\n" for i in range(12)) + "GO\r\n"
_PNG_LIKE = "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x02\x00\x00\x00\x01\x00\x08\x06\x00\x00\x00\x5c\x72\xa8\x66\x00\x00\x00\x09pHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x07tIME\x07\xe7\x0b\x03\x09\x0c\x2d"
_PS_NONASCII = "Name\tSize\r\nGr\u00f6\u00dfe.txt\t12\r\n\u5927\u304d\u3055.log\t98\r\nsmile \U0001F600.md\t7\r\n" * 3

case(
    332,
    claim="A byte-per-character string holding UTF-16 (BOM or alternating NULs, LE or BE) is decoded to text behind a one-line header that records endianness and BOM, so the exact original byte string is recoverable; binary data and undecodable or cut-off UTF-16 are left untouched.",
    encode=utf16_to_text,
    decode=text_to_utf16,
    equivalent=_oracle_332,
    fixtures=[
        Fixture(".reg export, UTF-16LE with BOM", _as_u16(_REG), True),
        Fixture("SQL Server script, UTF-16LE without BOM", _as_u16(_SQL, bom=False), True),
        Fixture("big-endian without BOM", _as_u16("Report generated on host build-07\r\nstatus: all checks passed\r\n" * 4, "be", False), True),
        Fixture("non-ASCII names with emoji (surrogate pair), LE BOM", _as_u16(_PS_NONASCII), True),
        Fixture("PNG-like binary with NULs, no pattern", _PNG_LIKE, False),
        Fixture("ordinary ASCII", "plain text output\nsecond line\n" * 3, False),
        Fixture("NUL-separated strings, sparse NULs", "name\x00value\x00path\x00/usr/bin\x00mode\x00755\x00user\x00root\x00", False),
        Fixture("UTF-16LE with a lone surrogate", _as_u16("ok text here!\r\n")[:6] + "\x00\xd8" + _as_u16("tail of the file\r\n", bom=False), False),
        Fixture("BOM then an odd number of bytes (cut off)", _as_u16("a longer sentence than before\r\n")[:-1], False),
        Fixture("BOM already destroyed by a UTF-8 decode (U+FFFD U+FFFD)", "\ufffd\ufffd" + _as_u16("Windows Registry Editor Version 5.00\r\n", bom=False), False),
        Fixture("input that starts like our header", "[decoded from UTF-16LE]\nhello\n[text follows unchanged]\n", None),
        Fixture("input that starts with the escape", "[text follows unchanged]\n" + _as_u16(_SQL, bom=False), None),
        Fixture("BOM only, BOM plus a character above U+00FF", "\u00ff\u00fe\n\u00ff\u00fe\u0100" * 3, None),
    ],
    min_gain=0.3,
    idempotent=False,  # an encoded text starts with the header, so encoding it again must escape it
)

# ---------------------------------------------------------------------------------------------------------------
# #616 Resolve backspace overstrike like `col -b`: per line, a cursor moves left on \b and each character overwrites the one
# in its column; the last character written to a column wins. Covers man/nroff bold (X\bX), underline (_\bX) and spinners.

def apply_backspaces(text: str) -> str:
    if "\b" not in text:
        return text
    out = []
    for line in text.split("\n"):
        if "\b" in line:
            cells: list[str] = []
            col = 0
            for ch in line:
                if ch == "\b":
                    col = max(0, col - 1)
                elif col < len(cells):
                    cells[col] = ch
                    col += 1
                else:
                    cells.append(ch)
                    col += 1
            line = "".join(cells)
        out.append(line)
    res = "\n".join(out)
    return res if len(res) < len(text) else text


_KNOWN_616 = [("N\bNA\bAM\bME\bE", "NAME"), ("_\bf_\bi_\bl_\be", "file"), ("-\b\\\b|\b/", "/"), ("\bab", "ab"), ("ab\b\bX", "Xb"), ("a\b", "a"), ("x\b\b\bY\n\bz", "Y\nz")]


def _oracle_616(x: str, e: str) -> bool:
    """Independent: write characters into a column -> char grid and read the grid back, instead of a growing cell list."""
    def render(line: str) -> str:
        grid, col = {}, 0
        for ch in line:
            if ch == "\b":
                col = col - 1 if col > 0 else 0
            else:
                grid[col] = ch
                col += 1
        return "".join(grid[k] for k in sorted(grid))

    for src, want in _KNOWN_616:  # known answers from `col -b` semantics; a wrong oracle cannot pass a wrong encoder
        assert "\n".join(render(l) for l in src.split("\n")) == want, f"oracle self-check failed on {src!r}"
    assert "\b" not in e, "a backspace survived"
    return "\n".join(render(l) for l in x.split("\n")) == e


def _bold(s: str) -> str:
    return "".join(f"{c}\b{c}" if c != " " else c for c in s)


def _ul(s: str) -> str:
    return "".join(f"_\b{c}" if c != " " else c for c in s)


_MAN = "\n".join([
    f"LS(1)                            User Commands                            LS(1)",
    "",
    _bold("NAME"),
    "       ls - list directory contents",
    "",
    _bold("SYNOPSIS"),
    f"       {_bold('ls')} [{_bold('OPTION')}]... [{_ul('FILE')}]...",
    "",
    _bold("DESCRIPTION"),
    f"       List information about the {_ul('FILE')}s (the current directory by default).",
    "",
    f"       {_bold('-a')}, {_bold('--all')}",
    "              do not ignore entries starting with .",
    "",
    f"       {_bold('-l')}     use a long listing format",
    f"       {_bold('--block-size')}={_ul('SIZE')}",
    f"              with {_bold('-l')}, scale sizes by {_ul('SIZE')} when printing them",
    "",
    f"       {_bold('--color')}[={_ul('WHEN')}]",
    "              colorize the output; WHEN can be 'always', 'auto', or 'never'",
]) + "\n"
_SPINNER = "installing " + "".join(f"{f}\b" for f in "-\\|/-\\|/") + "done\n" + "building " + "-\b\\\b|\b/\b" * 6 + "ok\n"

case(
    616,
    claim="Backspaces are applied like a terminal (col -b): per line, each character overwrites the one in its column, so X\\bX bold, _\\bX underline and -\\b\\\\b|\\b/ spinners reduce to the final visible character.",
    loses="the bold/underline emphasis itself, intermediate spinner frames, and display-column semantics (a tab or wide character counts as one cell)",
    encode=apply_backspaces,
    equivalent=_oracle_616,
    fixtures=[
        Fixture("man ls page with bold and underline", _MAN, True),
        Fixture("script typescript with spinners", _SPINNER * 3, True),
        Fixture("cat -v form ^H, literal backslash-b, plain man text", "N^HNA^HAM^HME^HE\nuse \\b for a backspace in C strings\nLS(1) User Commands\n", False),
        Fixture("backspace at line start, trailing, deleting two cells", "\bab\nab\b\bX\nend\b\nx\b\b\bY\n\b\b\n", None),
    ],
    min_gain=0.15,
)

# ---------------------------------------------------------------------------------------------------------------
# #808 Join multi-line pretty-printed struct dumps (Rust {:#?}, pprint). A block that starts on a line ending in an opener
# and closes where the brackets balance is joined onto one line if the result is under 160 characters, every string closes on
# its own line and the block looks like data (no ';', comments or code keywords). Only line breaks and indentation change.

_CODEWORD = re.compile(r"\b(?:def|fn|function|if|else|for|while|class|struct|impl|return|let|var|const|import|match)\b")
_CLOSERS_ONLY = re.compile(r"[\]\)\}]+,?")


def _scan_line(line: str):
    """-> (net bracket balance, lowest running balance, ok). ok is False for an unclosed string, ';', a comment or a code word."""
    depth = low = i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c in "\"'":
            j = i + 1
            while j < n and line[j] != c:
                j += 2 if line[j] == "\\" else 1
            if j >= n:
                return depth, low, False
            i = j + 1
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
            low = min(low, depth)
        elif c == ";" or (c == "/" and line[i + 1:i + 2] in ("/", "*")):
            return depth, low, False
        i += 1
    return depth, low, not _CODEWORD.search(line)


def join_struct_dumps(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        first = lines[i].rstrip()
        net, low, ok = _scan_line(first)
        joined = None
        if ok and net > 0 and low == 0 and (first[-1:] in ("{", "[", "(") or (first.lstrip()[:1] in ("{", "[", "(") and first[-1:] == ",")):
            depth, parts, j = net, [first], i + 1
            while j < len(lines):
                s = lines[j].strip()
                n2, low2, ok2 = _scan_line(s)
                if not s or not ok2 or depth + low2 < 0:
                    break
                if depth + n2 == 0:  # the line that closes the block
                    parts.append(s)
                    cand = ""
                    for p in parts:
                        cand += p if not cand or cand[-1] in "[(" else " " + p
                    joined = (cand, j) if len(cand) < 160 else None
                    break
                if depth + low2 <= 0 or not (s[-1] in ",{[(" or _CLOSERS_ONLY.fullmatch(s)):
                    break
                depth += n2
                parts.append(s)
                j += 1
        if joined:
            out.append(joined[0])
            i = joined[1] + 1
        else:
            out.append(lines[i])
            i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _lex_808(s: str):
    """Oracle lexer: strings (closed on their own line), single brackets and bare words; whitespace is dropped."""
    toks, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "\"'":
            j = i + 1
            while j < n and s[j] != c and s[j] != "\n":
                j += 2 if s[j] == "\\" else 1
            if j < n and s[j] == c:
                toks.append(("str", s[i:j + 1]))
                i = j + 1
            else:
                toks.append(("quote", c))
                i += 1
        elif c in "{}[]()":
            toks.append(("br", c))
            i += 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in "\"'{}[]()":
                j += 1
            toks.append(("word", s[i:j]))
            i = j
    return toks


def _oracle_808(x: str, e: str) -> bool:
    if _lex_808(x) != _lex_808(e):  # same strings, brackets and words in the same order: balance and content unchanged
        return False
    old = set(x.split("\n"))
    return all(l in old or len(l) < 160 for l in e.split("\n")) and e.count("\n") <= x.count("\n")


_CARGO_TEST = """running 3 tests
test config::tests::parses_defaults ... ok
test config::tests::merges_overrides ... FAILED
test server::tests::binds_port ... FAILED
test server::tests::reuses_socket ... ok

failures:

---- config::tests::merges_overrides stdout ----
thread 'config::tests::merges_overrides' panicked at src/config.rs:88:9:
assertion `left == right` failed
  left: Config {
    name: "svc",
    listen: Listen {
        host: "0.0.0.0",
        port: 8080,
    },
    tags: [
        "edge",
        "blue",
    ],
    retries: Some(
        3,
    ),
}
 right: Config {
    name: "svc",
    listen: Listen {
        host: "0.0.0.0",
        port: 9090,
    },
    tags: [
        "edge",
        "blue",
    ],
    retries: Some(
        3,
    ),
}

---- server::tests::binds_port stdout ----
thread 'server::tests::binds_port' panicked at src/server.rs:41:9:
assertion `left == right` failed: unexpected state
  left: State {
    phase: Ready,
    peers: [
        Peer {
            id: 7,
            addr: "10.0.0.7:9000",
        },
        Peer {
            id: 9,
            addr: "10.0.0.9:9000",
        },
    ],
    last_error: None,
}
 right: State {
    phase: Draining,
    peers: [],
    last_error: Some(
        "connection reset by peer",
    ),
}

failures:
    config::tests::merges_overrides
    server::tests::binds_port

test result: FAILED. 2 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out
"""

_BIG_DUMP = "  left: Inventory {\n    items: [\n" + "".join(f'        Item {{ sku: "SKU-{i:05d}", qty: {i}, price: {i}.99 }},\n' for i in range(8)) + "    ],\n}\n"
_MULTILINE_STRING = 'Msg {\n    text: "line one\nline two",\n    n: 3,\n}\n'
_C_CODE = "int main(void) {\n    int rc = run(1,\n                2);\n    return rc;\n}\n"
_DEF = "def configure(\n    name,\n    port,\n):\n    return name, port\n"
_NO_TRAILING_COMMA = "call(\n    first(a),\n    second(b)\n)\n"
_PPRINT = "{'name': 'svc',\n 'ports': [80,\n           443],\n 'meta': {'owner': 'infra', 'tier': 2}}\n"

case(
    808,
    claim="A multi-line bracketed dump whose brackets balance, whose strings each close on their own line and whose joined form is under 160 characters becomes one line; the sequence of strings, brackets and words is identical.",
    loses="line breaks and indentation inside the joined dump (token order, string contents and trailing commas are kept)",
    encode=join_struct_dumps,
    equivalent=_oracle_808,
    fixtures=[
        Fixture("cargo test assert_eq with {:#?} dumps", _CARGO_TEST, True),
        Fixture("python pprint dict", _PPRINT * 4, True),
        Fixture("dump too long to join (over 160 characters)", _BIG_DUMP, False),
        Fixture("string literal spanning two lines", _MULTILINE_STRING, False),
        Fixture("C code with ';' and python def", _C_CODE + _DEF, False),
        Fixture("last argument without a trailing comma (code, not a dump)", _NO_TRAILING_COMMA, False),
        Fixture("unbalanced and over-closed brackets, apostrophes, tuple", "Foo {\n    a: 1,\n}}\nBar(\n    x: 'don't,\n)\nT(\n    1,\n)\nbad [\n  1,\n", None),
        Fixture("CRLF and tabs", "Cfg {\r\n\tname: \"a b\",\r\n\tn: [\r\n\t\t1,\r\n\t],\r\n}\r\n", None),
        Fixture("brackets inside strings", 'S {\n    a: "}{",\n    b: "[(",\n}\n', None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #1342 Collapse the extra spaces after a sentence end in prose. Only inside prose lines (8+ words, mostly plain words, no tab or
# pipe, no alignment gap); indentation is left as it is, and code, tables and option lists are untouched.

_GAP = re.compile(r"(?<=[.!?]) {2,}(?=[A-Z])")
_ALIGN = re.compile(r"(?<![.!? ]) {3,}")
_CODEY = re.compile(r"[=<>{}\[\]\\;]|\w\(|::|->|&&|\|\|")
_WORDY = re.compile(r"""[(\"']?[A-Za-z][A-Za-z'’-]*[.,;:!?)\"']*""")


def _is_prose(body: str) -> bool:
    if "\t" in body or "|" in body or _ALIGN.search(body) or _CODEY.search(body):
        return False
    words = body.split()
    return len(words) >= 8 and sum(bool(_WORDY.fullmatch(w)) for w in words) * 10 >= len(words) * 7


def collapse_sentence_gaps(text: str) -> str:
    out = []
    for line in _lines(text):
        body = line.lstrip(" ")
        if "  " in body and _is_prose(body):
            line = line[: len(line) - len(body)] + _GAP.sub(" ", body)
        out.append(line)
    return "\n".join(out)


def _only_sentence_gaps(a: str, b: str) -> bool:
    """Oracle: split into (token, following gap) pairs; tokens must match and only a gap after a sentence end before a capital may shrink."""
    ia, ib = a[: len(a) - len(a.lstrip())], b[: len(b) - len(b.lstrip())]
    ta, tb = re.findall(r"(\S+)(\s*)", a), re.findall(r"(\S+)(\s*)", b)
    if ia != ib or len(ta) != len(tb):
        return False
    for k, ((wa, ga), (wb, gb)) in enumerate(zip(ta, tb)):
        if wa != wb:
            return False
        if ga != gb:
            nxt = ta[k + 1][0] if k + 1 < len(ta) else ""
            if not (set(ga) == {" "} and len(ga) >= 2 and gb == " " and wa[-1] in ".!?" and nxt[:1].isascii() and nxt[:1].isupper()):
                return False
    return True


def _prose_by_scan(body: str) -> bool:
    """Oracle's own view of 'prose line': at least 8 words, no tab or pipe, no run of 3+ spaces away from a sentence end."""
    if "\t" in body or "|" in body or len(body.split()) < 8 or any(c in body for c in "=;{}"):
        return False
    i = 0
    while i < len(body):
        if body[i] == " ":
            j = i
            while j < len(body) and body[j] == " ":
                j += 1
            if j - i >= 3 and not (i > 0 and body[i - 1] in ".!?"):
                return False
            i = j
        else:
            i += 1
    return True


def _oracle_1342(x: str, e: str) -> bool:
    xl, el = _lines(x), _lines(e)
    if len(xl) != len(el):
        return False
    for a, b in zip(xl, el):
        if a == b:
            continue
        if not _prose_by_scan(a.lstrip(" ")) or not _only_sentence_gaps(a, b):
            return False
    return True


_TERSE = "\n".join([
    "   It works.  It is fast.  It is cheap.  Use it daily.  Enjoy it.  Smile.",
    "   Copy the file.  Run it.  Read the log.  Then stop.  Rest well.  Sleep.",
    "   Set the flag.  Clear it.  Run it twice.  Done now.  Go home.  Relax.",
    "Open it first.  Close it later.  Do not leave it open.  Ever.  Really.  Never.",
    "   Wait for it.  Press the key.  Look.  Note the time.  Write it down.  Breathe.",
    "   The server MUST reply.  The client MAY retry.  Both MUST close.  Stop.  Now.  Go.",
    "   Is it done?  Yes it is!  Then ship the build.  Tell the team.  Cheer.  Rest.",
]) + "\n"
_KEEP_1342 = "\n".join([
    "Usage: tool [OPTIONS] FILE",
    "  -f, --force     Overwrite existing files.  Never asks for confirmation.",
    "Name        Value.  Note",
    "a | b | c | d | e | f | g | h | Fine.  Really.",
    ">>> print(\"Done.  Next\", end=\"\", flush=True)  # one two three four five six",
    "if x == \"a.  B\" and y == \"C\":  # Then do it.  Really.  Right now please",
    "Short one.  Two words only.",
    "tab\tseparated line with many words in it.  Never touch.  Please keep them.",
]) + "\n"

case(
    1342,
    claim="Two or more spaces after . ! or ? and before a capital letter become one space, only inside prose lines (8+ words, mostly plain words, no tab, pipe or alignment gap); indentation, code, tables and option lists are byte-identical.",
    loses="the second space after a sentence end in prose lines",
    encode=collapse_sentence_gaps,
    equivalent=_oracle_1342,
    fixtures=[
        Fixture("RFC and old-docstring style prose with two spaces", _TERSE, True),
        Fixture("option list, table, pipes, code, short line, tabs", _KEEP_1342, False),
        Fixture("lowercase and digit after the gap, 3+ spaces, quotes, CRLF", "Write e.g.  the value, then wait.  and then 3.  4 more things happen now.\nWe stopped.   Then we left it be and went home early.\r\nHe said \"stop.\"  The end came soon after that moment.\n", None),
    ],
    min_gain=0.05,
)

# ---------------------------------------------------------------------------------------------------------------
# #517 Replace tree rails with indentation. A run of 3+ `tree` entry lines whose rails are exactly what the depths imply
# (so parent-of, depth and last-child are all still recoverable) becomes two-space indentation; anything else is left alone.
# Also, in perf call-graph output, drop the pure decoration lines ('#' alone, the '# ........' underline row, '|'-only
# connector lines). perf's information-bearing '#' header lines (Samples, Event count, column titles) are KEPT: dropping
# them would lose the event name and the meaning of the columns.

_ENTRY = re.compile(r"^((?:│   |│\u00a0\u00a0 |    )*)(├── |└── )(\S.*)$")
_PERF_EDGE = re.compile(r"--\d+(?:\.\d+)?%--")


def _canonical_run(entries) -> bool:
    """entries: [(depth, prefix, connector)]. True when every rail cell and connector is what the depth profile requires."""
    n = len(entries)
    depth = [e[0] for e in entries]
    if depth[0] != 1 or any(depth[i] > depth[i - 1] + 1 for i in range(1, n)):
        return False
    last = []
    for i in range(n):
        j = i + 1
        while j < n and depth[j] > depth[i]:
            j += 1
        last.append(j >= n or depth[j] < depth[i])
    nbsp = any("\u00a0" in e[1] for e in entries)
    bar = "│\u00a0\u00a0 " if nbsp else "│   "
    anc: dict[int, int] = {}
    for i, (d, prefix, conn) in enumerate(entries):
        anc[d] = i
        want = "".join(("    " if last[anc[l]] else bar) for l in range(1, d))
        if prefix != want or conn != ("└── " if last[i] else "├── "):
            return False
    return True


def _perf_noise(l: str) -> bool:
    s = l.strip()
    return s == "#" or re.fullmatch(r"#[ .]*\.[ .]*", s) is not None or (s != "" and set(s) <= {"|", " "})


def tree_rails_to_indent(text: str) -> str:
    lines = text.split("\n")
    if any(_PERF_EDGE.search(l) for l in lines):  # perf call graph: decoration-only lines carry nothing
        lines = [l for l in lines if not _perf_noise(l)]
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and _ENTRY.match(lines[j]):
            j += 1
        if j - i >= 3:
            ents = [_ENTRY.match(l).groups() for l in lines[i:j]]
            ents = [(len(p) // 4 + 1, p, c, nm) for p, c, nm in ents]
            if _canonical_run([e[:3] for e in ents]):
                out += ["  " * d + nm for d, _p, _c, nm in ents]
                i = j
                continue
        out.append(lines[i])
        i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _render_tree(ents: list[tuple[int, str]], nbsp: bool) -> list[str]:
    """Oracle: build the tree explicitly and print it the way `tree` does (recursion instead of a depth/last-child table)."""
    root: dict = {"kids": []}
    stack = [(0, root)]
    for d, name in ents:
        while stack[-1][0] >= d:
            stack.pop()
        node = {"name": name, "kids": []}
        stack[-1][1]["kids"].append(node)
        stack.append((d, node))
    lines: list[str] = []

    def walk(node, prefix):
        for k, child in enumerate(node["kids"]):
            last = k == len(node["kids"]) - 1
            lines.append(prefix + ("└── " if last else "├── ") + child["name"])
            walk(child, prefix + ("    " if last else ("│\u00a0\u00a0 " if nbsp else "│   ")))

    walk(root, "")
    return lines


def _perf_deco(l: str) -> bool:
    """Oracle's own notion of a decoration-only line: '#' alone, '# ....' underline rows, or pipes and spaces only."""
    s = l.strip()
    if s == "#":
        return True
    if s.startswith("#") and "." in s and not s[1:].replace(".", "").replace(" ", ""):
        return True
    return "|" in s and not s.replace("|", "").replace(" ", "")


def _oracle_517(x: str, e: str) -> bool:
    xl = x.split("\n")
    if re.search(r"\d%--", x):
        xl = [l for l in xl if not _perf_deco(l)]
    el = e.split("\n")
    if len(xl) != len(el):
        return False
    k = 0
    while k < len(xl):
        if xl[k] == el[k]:
            k += 1
            continue
        m = k
        while m < len(xl) and xl[m] != el[m]:  # a maximal run of changed lines must be one whole canonical tree body
            m += 1
        ents = []
        for l in el[k:m]:
            body = l.lstrip(" ")
            if (len(l) - len(body)) % 2 or not body or len(l) == len(body):
                return False
            ents.append(((len(l) - len(body)) // 2, body))
        nbsp = "\u00a0" in "".join(xl[k:m])
        if _render_tree(ents, nbsp) != xl[k:m] or len(ents) < 3:
            return False
        if any(g in "".join(el[k:m]) for g in "├└│"):  # no rail glyph may remain in the converted lines
            if not any(g in n for _d, n in ents for g in "├└│"):
                return False
        k = m
    return True


def _tree_text(nbsp: bool, files: dict) -> str:
    bar = "│\u00a0\u00a0 " if nbsp else "│   "
    out = ["."]

    def walk(d, prefix):
        items = list(d.items())
        for i, (name, sub) in enumerate(items):
            last = i == len(items) - 1
            out.append(prefix + ("└── " if last else "├── ") + name)
            if isinstance(sub, dict):
                walk(sub, prefix + ("    " if last else bar))

    walk(files, "")
    return "\n".join(out) + "\n\n3 directories, 14 files\n"


_PROJECT = {"Cargo.lock": 0, "Cargo.toml": 0, "src": {"api": {"mod.rs": 0, "routes.rs": 0, "handlers": {"orders.rs": 0, "users.rs": 0}},
            "config.rs": 0, "lib.rs": 0, "main.rs": 0}, "tests": {"fixtures": {"orders.json": 0}, "smoke.rs": 0}, "docs": {"design notes.md": 0}, "README.md": 0}
_PERF = """# To display the perf.data header info, please use --header/--header-only options.
#
# Total Lost Samples: 0
#
# Samples: 12K of event 'cycles'
# Event count (approx.): 3150000000
#
# Children      Self  Command  Shared Object  Symbol
# ........  ........  .......  .............  ......
#
    45.67%     0.00%  prog     prog           [.] main
            |
            ---main
               |
               |--30.12%--compute
               |          |
               |          |--20.00%--inner_loop
               |          |
               |           --10.12%--helper
               |
                --15.55%--io_wait
                          |
                          |--9.10%--read
                          |
                           --6.45%--poll

    21.30%    21.30%  prog     libc.so.6      [.] memcpy
            |
            ---memcpy
               compute
               main
"""
_BAD_TREE = "src\n├── a.rs\n└── b.rs\n├── c.rs\n└── d.rs\n"  # b claims to be the last child, but c and d follow

case(
    517,
    claim="A run of 3+ `tree` entry lines whose rails match what the depths imply becomes two-space indentation (parent-of, depth and last-child stay derivable); non-canonical trees are left alone; in perf call graphs only decoration lines ('#', '# ......', '|') are dropped.",
    loses="the rail glyphs themselves (re-derivable from depth order, including the NBSP padding variant, which must be restored by the reader), and perf's decoration-only lines; perf's Samples/Event-count/column-title header lines are kept on purpose",
    encode=tree_rails_to_indent,
    equivalent=_oracle_517,
    fixtures=[
        Fixture("tree of a rust project, plain spaces", _tree_text(False, _PROJECT), True),
        Fixture("same tree with NBSP rail padding (GNU tree)", _tree_text(True, _PROJECT), True),
        Fixture("perf report -g --stdio call graph", _PERF, True),
        Fixture("non-canonical tree: wrong glyphs, stray rail", _BAD_TREE, False),
        Fixture("script comments and pipes without perf markers", "# banner\n#\n# ........\n|\n|\ncat file | grep x\n", False),
        Fixture("depth jump, name starting with a space, two-entry run", "r\n├── a\n│   │   └── jump\n└──  spaced\n\n├── x\n└── y\n", None),
        Fixture("mixed NBSP and plain rails", "d\n├── a\n│   ├── b\n│\u00a0\u00a0 └── c\n└── e\n", None),
    ],
    min_gain=0.1,
)

# ---------------------------------------------------------------------------------------------------------------
# #17 Dedupe repeated failure blocks. (A) jest/vitest print every failure in the body and again, byte for byte, under "Summary of
# all failing tests": the second copy becomes one marker line. (B) pytest repeats only a message: "FAILED id - <msg>" in the short
# summary where the block above has the same text on its first E line. Exact roundtrip behind a header line; the encoder checks its
# own decode and hands the input back if the check fails (so a block that is not provably identical is never folded).

_F_HDR = "[repeats folded, see text above]\n"
_F_RAW = "[text follows unchanged]\n"
_BLOCK_HEAD = re.compile(r"^(?P<ind> *)(?:(?P<jest>● .+)|(?P<py>_{3,} (?P<pyname>.+?) _{3,}))$")
_MARK_A = re.compile(r"^( *)\[same as the failure block above: (.+)\]$")
_MARK_B = re.compile(r"^(FAILED|ERROR) (\S+) - \[same as the E line above\]$")
_SUMMARY = re.compile(r"^(FAILED|ERROR) (\S+) - (.+)$")


def _blocks(lines: list[str]):
    """-> [(start, end, header_stripped, pytest_name|None)] ; trailing blank lines are not part of a block."""
    heads = [(i, m) for i, l in enumerate(lines) if (m := _BLOCK_HEAD.match(l)) or _MARK_A.match(l)]
    res = []
    for n, (i, m) in enumerate(heads):
        if m is None:
            continue
        ind = len(m.group("ind"))
        j = i + 1
        while j < len(lines) and not (n + 1 < len(heads) and j == heads[n + 1][0]):
            l = lines[j]
            if re.match(r"={3,}", l) or (m.group("jest") and l.strip() and len(l) - len(l.lstrip(" ")) <= ind):
                break
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        res.append((i, j, lines[i].strip(), m.group("pyname")))
    return res


def _first_e(lines: list[str], start: int, end: int):
    for l in lines[start:end]:
        if (m := re.match(r"^E {1,}(\S.*)$", l)):
            return m.group(1)
    return None


def _decode_failures(text: str) -> str:
    if text.startswith(_F_RAW):
        return text[len(_F_RAW):]
    if not text.startswith(_F_HDR):
        return text
    out: list[str] = []
    for l in text[len(_F_HDR):].split("\n"):
        ma, mb = _MARK_A.match(l), _MARK_B.match(l)
        if ma:
            hit = [b for b in _blocks(out) if b[2] == ma.group(2)]
            if hit:
                out += out[hit[-1][0]:hit[-1][1]]
                continue
        elif mb:
            name = ".".join(mb.group(2).split("::")[1:])
            hit = [b for b in _blocks(out) if b[3] == name]
            msg = _first_e(out, hit[-1][0], hit[-1][1]) if hit else None
            if msg is not None:
                out.append(f"{mb.group(1)} {mb.group(2)} - {msg}")
                continue
        out.append(l)
    return "\n".join(out)


def fold_failures(text: str) -> str:
    if text.startswith(_F_HDR) or text.startswith(_F_RAW):
        return _F_RAW + text  # grows by design: otherwise decode could not tell this from an encoded text
    lines = text.split("\n")
    blocks = _blocks(lines)
    replace: dict[int, tuple[int, str]] = {}
    for k, (s, e, head, _name) in enumerate(blocks):
        if e - s < 3 or sum(map(len, lines[s:e])) < 90:
            continue
        prev = [b for b in blocks[:k] if b[2] == head]
        if prev and lines[prev[-1][0]:prev[-1][1]] == lines[s:e]:
            ind = lines[s][: len(lines[s]) - len(lines[s].lstrip(" "))]
            replace[s] = (e, f"{ind}[same as the failure block above: {head}]")
    out, i = [], 0
    while i < len(lines):
        if i in replace:
            out.append(replace[i][1])
            i = replace[i][0]
            continue
        m = _SUMMARY.match(lines[i])
        if m and len(m.group(3)) > 30:
            name = ".".join(m.group(2).split("::")[1:])
            hit = [b for b in blocks if b[3] == name and b[0] < i]
            if hit and _first_e(lines, hit[-1][0], hit[-1][1]) == m.group(3):
                out.append(f"{m.group(1)} {m.group(2)} - [same as the E line above]")
                i += 1
                continue
        out.append(lines[i])
        i += 1
    res = _F_HDR + "\n".join(out)
    if len(res) >= len(text) or _decode_failures(res) != text:
        return text
    return res


_JEST_BLOCK = """  ● Cart › applies a percentage coupon

    expect(received).toBe(expected) // Object.is equality

    Expected: 90
    Received: 100

      21 |     cart.add({ sku: 'A-1', price: 100 });
      22 |     cart.applyCoupon('TEN_PERCENT');
    > 23 |     expect(cart.total()).toBe(90);
         |                          ^
      24 |   });

      at Object.<anonymous> (src/cart.test.js:23:26)
"""
_JEST_BLOCK2 = _JEST_BLOCK.replace("applies a percentage coupon", "rejects an expired coupon").replace("Expected: 90", "Expected: 100").replace("Received: 100", "Received: 80").replace("toBe(90)", "toBe(100)")


def _jest_run() -> str:
    body = "FAIL src/cart.test.js\n" + _JEST_BLOCK + "\n" + _JEST_BLOCK2 + "\nFAIL src/user.test.js\n" + _JEST_BLOCK.replace("Cart", "User") + "\n"
    summary = "Summary of all failing tests\nFAIL src/cart.test.js\n" + _JEST_BLOCK + "\n" + _JEST_BLOCK2 + "\nFAIL src/user.test.js\n" + _JEST_BLOCK.replace("Cart", "User") + "\n"
    return body + "Test Suites: 2 failed, 2 total\nTests:       3 failed, 9 passed, 12 total\n\n" + summary + "Test Suites: 2 failed, 2 total\n"


def _pytest_run() -> str:
    blocks, summ = [], []
    cases = [("test_create_order", "test_create_order", "AssertionError: unexpected status: {'detail': 'quantity must be positive', 'field': 'items[0].qty'}"),
             ("TestOrders.test_cancel", "TestOrders::test_cancel", "assert {'status': 'shipped', 'id': 41} == {'status': 'cancelled', 'id': 41}"),
             ("test_refund[partial-0.5]", "test_refund[partial-0.5]", "ValueError: refund of 0.5 exceeds the remaining balance of 0.25 for order 7")]
    cases += [(f"test_ship[{i}]", f"test_ship[{i}]", f"KeyError: 'warehouse-{i}' is not in the inventory snapshot taken at 2026-11-0{i} 10:00:00") for i in range(1, 6)]
    for name, ident, msg in cases:
        blocks.append(f"{'_' * 20} {name} {'_' * 20}\n\n    def check():\n>       do_the_thing()\nE       {msg}\nE       assert 1 == 2\n\ntests/test_api.py:21: AssertionError\n")
        summ.append(f"FAILED tests/test_api.py::{ident} - {msg}")
    return ("============ test session starts ============\ncollected 12 items\n\ntests/test_api.py .F.F.   [100%]\n\n============ FAILURES ============\n" + "\n".join(blocks) +
            "============ short test summary info ============\n" + "\n".join(summ) + "\n============ 8 failed, 4 passed in 0.41s ============\n")


_NO_REPEAT = ("FAIL src/a.test.js\n" + _JEST_BLOCK + "\nFAIL src/b.test.js\n" + _JEST_BLOCK2 + "\nTests: 2 failed\n")
_SAME_HEADER_DIFFERENT_BODY = "  ● Cart › x\n    one\n    two\n    three\n    four five six seven eight nine ten eleven twelve thirteen\n\nsummary\n  ● Cart › x\n    one\n    two\n    three\n    four five six seven eight nine ten eleven twelve THIRTEEN\n"
_TRUNCATED_SUMMARY = ("____ test_a ____\n\n>   boom()\nE   RuntimeError: a very long message that pytest cuts in the short summary because it is wide\n\nt.py:3: RuntimeError\n"
                      "==== short test summary info ====\nFAILED t.py::test_a - RuntimeError: a very long message that pytest cuts in the short summ...\n")

case(
    17,
    claim="A failure block repeated byte-for-byte later in the same output (jest summary) becomes one marker line, and a pytest summary message identical to the first E line above becomes a marker; the exact original is restored from the text above, and anything not provably identical is left in place.",
    encode=fold_failures,
    decode=_decode_failures,
    fixtures=[
        Fixture("jest run with a summary section repeating three failures", _jest_run(), True),
        Fixture("pytest summary messages equal to the E lines", _pytest_run(), True),
        Fixture("failures that appear only once", _NO_REPEAT, False),
        Fixture("same header, different body", _SAME_HEADER_DIFFERENT_BODY, False),
        Fixture("pytest summary truncated with ...", _TRUNCATED_SUMMARY, False),
        Fixture("input that starts like the folded header", _F_HDR + _jest_run(), None),
        Fixture("input that starts with the escape", _F_RAW + _jest_run(), None),
        Fixture("input that already contains marker lines next to a real repeat", _jest_run() + "  [same as the failure block above: ● Cart › applies a percentage coupon]\nFAILED t.py::test_x - [same as the E line above]\n", None),
    ],
    min_gain=0.1,
    idempotent=False,  # an encoded text starts with the header, so encoding it again must escape it
)

# ---------------------------------------------------------------------------------------------------------------
# #14 Whitespace tokenization study (eval idea): a sim that measures, on built-in samples, which whitespace transforms change the
# token count (harness.tokens: o200k when tiktoken loads, else the word-piece proxy, which counts every whitespace run as one
# token and so sees no difference), and proves each candidate is fidelity-safe: every candidate has an independent oracle, and a
# guard that returns the sample untouched where the transform would be unsafe (empty table cells, TSV, Makefile recipes).

def _t_strip_trailing(s: str) -> str:
    return s if _is_diff(s) else "\n".join(l.rstrip(" \t") for l in _lines(s))


def _clean_table(s: str) -> bool:
    rows = [l.split() for l in _lines(s) if l.strip()]
    return len(rows) >= 3 and len({len(r) for r in rows}) == 1 and "\t" not in s


def _squeeze(sep: str, minrun: int):
    pat = re.compile(r"(?<=\S) {%d,}(?=\S)" % minrun)
    return lambda s: "\n".join(pat.sub(sep, l) for l in _lines(s)) if _clean_table(s) else s


def _indent_spaces_ok(s: str) -> bool:
    ind = [len(l) - len(l.lstrip(" ")) for l in _lines(s) if l.strip()]
    return any(ind) and all(i % 4 == 0 for i in ind) and not any(l.startswith("\t") for l in _lines(s))


def _t_indent_4_to_2(s: str) -> str:
    return "\n".join(re.sub(r"^( +)", lambda m: " " * (len(m.group(1)) // 2), l) for l in _lines(s)) if _indent_spaces_ok(s) else s


def _t_spaces_to_tabs(s: str) -> str:
    return "\n".join(re.sub(r"^( +)", lambda m: "\t" * (len(m.group(1)) // 4), l) for l in _lines(s)) if _indent_spaces_ok(s) else s


def _makefile_like(s: str) -> bool:
    ls = _lines(s)
    return any(re.match(r"[\w./%$()-]+(?: [\w./%$()-]+)*:(?:[^=]|$)", l) and i + 1 < len(ls) and ls[i + 1].startswith("\t") for i, l in enumerate(ls))


def _t_tabs_to_spaces(s: str) -> str:
    ls = _lines(s)
    lead_tabs = [len(l) - len(l.lstrip("\t")) for l in ls]
    if not any(lead_tabs) or _makefile_like(s) or any(l.startswith((" ", "\t ")) for l in ls):
        return s
    return "\n".join("    " * k + l[k:] for l, k in zip(ls, lead_tabs))


_CANDIDATES_14 = {
    "strip_trailing": _t_strip_trailing,
    "table_gap_to_1_space": _squeeze(" ", 2),
    "table_gap_to_2_spaces": _squeeze("  ", 3),
    "table_gap_to_tab": _squeeze("\t", 2),
    "indent_4_to_2_spaces": _t_indent_4_to_2,
    "indent_4_spaces_to_tab": _t_spaces_to_tabs,
    "indent_tab_to_4_spaces": _t_tabs_to_spaces,
}


def _samples_14() -> dict[str, str]:
    w = [10, 6, 6, 6, 9, 8, 6, 6, 8, 7, 0]
    rows = [("USER", "PID", "%CPU", "%MEM", "VSZ", "RSS", "TTY", "STAT", "START", "TIME", "COMMAND")]
    for i in range(14):
        rows.append((("root", "www", "postgres")[i % 3], str(100 + i * 37), f"{i * 0.7:.1f}", f"{i * 0.3:.1f}", str(169436 + i * 811), str(11704 + i * 97), "?", "Ss", "Oct12", f"0:{i:02d}", f"/usr/sbin/svc{i}"))
    ps = "\n".join("".join(c.ljust(n) if n else c for c, n in zip(r, w)) for r in rows) + "\n"
    docker = ("CONTAINER ID   IMAGE        STATUS         PORTS                  NAMES\n"
              "3f2a9c0d17b8   nginx:1.25   Up 3 hours     0.0.0.0:80->80/tcp     web\n"
              "9be1d7c4a002   redis:7      Up 3 hours                            cache\n"
              "c41f0e5d9a77   postgres:16  Up 2 hours     5432/tcp               db\n"
              "77aa31b9c0de   busybox      Exited (0)                            job\n")
    nested = "".join("    " * (i % 5) + f"key_{i}: value {i}\n" for i in range(40))
    go = "func main() {\n" + "".join("\t" * (1 + i % 3) + f"step{i}()\n" for i in range(30)) + "}\n"
    make = "build: deps\n\tcc -o app main.c\n\t./app --check\n\nclean:\n\trm -f app\n"
    tsv = "".join(f"{i}\talpha beta\tgamma\t{i * 3}\n" for i in range(30))
    padded = "".join(f"{'line ' + str(i):<80}\n" for i in range(20))
    return {"ps_table": ps, "docker_ps_empty_cells": docker, "nested_4_space": nested, "go_tab_indent": go, "makefile_tabs": make, "tsv": tsv, "padded_80_columns": padded}


def _fields_equal(a: str, b: str) -> bool:
    """Oracle for alignment transforms: same number of lines, and per line the same indentation and the same whitespace-separated fields."""
    la, lb = _lines(a), _lines(b)
    return len(la) == len(lb) and all(x.split() == y.split() and x[: len(x) - len(x.lstrip())] == y[: len(y) - len(y.lstrip())] for x, y in zip(la, lb))


def _indent_levels(s: str, unit: str) -> list[tuple[int, str]]:
    out = []
    for l in _lines(s):
        body = l.lstrip(" \t")
        ws = l[: len(l) - len(body)]
        assert ws.count(unit) * len(unit) == len(ws), f"indent {ws!r} is not made of {unit!r}"
        out.append((len(ws) // len(unit), body))
    return out


_ORACLES_14 = {
    "strip_trailing": lambda a, b: len(_lines(a)) == len(_lines(b)) and all(x.startswith(y) and not x[len(y):].strip(" \t") for x, y in zip(_lines(a), _lines(b))),
    "table_gap_to_1_space": _fields_equal,
    "table_gap_to_2_spaces": _fields_equal,
    "table_gap_to_tab": _fields_equal,
    "indent_4_to_2_spaces": lambda a, b: _indent_levels(a, "    ") == _indent_levels(b, "  "),
    "indent_4_spaces_to_tab": lambda a, b: _indent_levels(a, "    ") == _indent_levels(b, "\t"),
    "indent_tab_to_4_spaces": lambda a, b: _indent_levels(a, "\t") == _indent_levels(b, "    "),
}


def measure_14(samples: dict[str, str]) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    """(sample, transform) -> (tokens before, tokens after, chars before, chars after). Pure: no state is kept between calls."""
    res = {}
    for sname, text in samples.items():
        tb = harness.tokens(text)
        for tname, fn in _CANDIDATES_14.items():
            out = fn(text)
            res[(sname, tname)] = (tb, harness.tokens(out), len(text), len(out))
    return res


def sim_whitespace_study() -> dict:
    samples = _samples_14()
    first = measure_14(samples)
    assert measure_14(dict(reversed(list(samples.items())))) == first, "measurement depends on sample order (hidden state)"
    assert measure_14(samples) == first, "measurement is not deterministic"
    assert all(harness.tokens(t) == harness.tokens(t) for t in samples.values())
    changed = {}
    for (sname, tname), (tb, ta, cb, ca) in first.items():
        out = _CANDIDATES_14[tname](samples[sname])
        changed[(sname, tname)] = out != samples[sname]
        assert _CANDIDATES_14[tname](out) == out, f"{tname} on {sname} is not idempotent"
        assert ca <= cb or tname == "indent_tab_to_4_spaces", f"{tname} grew {sname} in characters"
        if out != samples[sname]:  # fidelity-safe: the independent oracle for this transform holds on the transformed sample
            assert _ORACLES_14[tname](samples[sname], out), f"fidelity: {tname} changed information in {sname}"
    # guards: where the transform would be unsafe the sample comes back byte-identical
    for sname, tname in [("docker_ps_empty_cells", "table_gap_to_1_space"), ("docker_ps_empty_cells", "table_gap_to_tab"), ("tsv", "table_gap_to_tab"),
                         ("tsv", "table_gap_to_1_space"), ("makefile_tabs", "indent_tab_to_4_spaces"), ("go_tab_indent", "indent_4_to_2_spaces"),
                         ("ps_table", "indent_4_to_2_spaces"), ("ps_table", "indent_tab_to_4_spaces")]:
        assert not changed[(sname, tname)], f"guard failed: {tname} touched {sname}"
    for sname, tname in [("ps_table", "table_gap_to_1_space"), ("padded_80_columns", "strip_trailing"), ("nested_4_space", "indent_4_to_2_spaces"),
                         ("nested_4_space", "indent_4_spaces_to_tab"), ("go_tab_indent", "indent_tab_to_4_spaces")]:
        assert changed[(sname, tname)], f"{tname} should apply to {sname}"
    # the study's findings. The proxy tokenizer cannot see whitespace, so tokenizer-specific claims are made only for o200k.
    saved = lambda s, t: first[(s, t)][0] - first[(s, t)][1]  # noqa: E731
    assert saved("ps_table", "table_gap_to_1_space") >= 0 and saved("nested_4_space", "indent_4_spaces_to_tab") >= 0
    assert saved("go_tab_indent", "indent_tab_to_4_spaces") <= 0, "tabs to spaces must not help"
    if harness.TOKENIZER == "o200k":
        tb = first[("ps_table", "table_gap_to_1_space")][0]
        assert saved("ps_table", "table_gap_to_1_space") >= 0.15 * tb, "a single space between columns should clearly beat padding"
        assert saved("ps_table", "table_gap_to_2_spaces") <= 0.02 * tb, "two spaces cost as much as the padding they replace"
        assert saved("padded_80_columns", "strip_trailing") >= 10, "long trailing padding costs about a token per line"
        assert saved("nested_4_space", "indent_4_to_2_spaces") == 0, "indent width is free"
    return {"tokenizer_is_o200k": int(harness.TOKENIZER == "o200k"),
            "ps_gap_to_1_space_tokens_saved": saved("ps_table", "table_gap_to_1_space"), "ps_gap_to_2_spaces_tokens_saved": saved("ps_table", "table_gap_to_2_spaces"),
            "ps_gap_to_tab_tokens_saved": saved("ps_table", "table_gap_to_tab"), "padded80_strip_tokens_saved": saved("padded_80_columns", "strip_trailing"),
            "indent_4_to_2_tokens_saved": saved("nested_4_space", "indent_4_to_2_spaces"), "indent_to_tab_tokens_saved": saved("nested_4_space", "indent_4_spaces_to_tab"),
            "tabs_to_spaces_tokens_saved": saved("go_tab_indent", "indent_tab_to_4_spaces"),
            "guarded_unsafe_pairs": 8, "pairs_measured": len(first)}


case(
    14,
    claim="Seven whitespace transforms (strip trailing blanks, collapse table gaps to 1 space / 2 spaces / a tab, halve indentation, spaces to tabs, tabs to spaces) are measured on built-in samples with harness.tokens, the measurement is deterministic and order-independent, every transform is checked by an independent oracle, and each declines the samples where it would be unsafe.",
    sim=sim_whitespace_study,
)

# ---------------------------------------------------------------------------------------------------------------
# #16 Drop stdout/stderr duplicates (hook level). Bash tool_response is {"stdout", "stderr", ...}. When stderr is byte-for-byte the
# whole of stdout, or a block of whole lines of it (`make | tee /dev/stderr`), stderr becomes a one-line pointer into stdout.

_PTR16 = re.compile(r"^\[stderr = stdout lines (\d+)-(\d+)(, no final newline)?\]$")
_RAW16 = "[stderr verbatim]\n"


def dedupe_streams(event: dict) -> dict:
    """PostToolUse-style rewrite. Returns the same object when nothing applies; never raises (fails open)."""
    try:
        if event.get("tool_name") != "Bash":
            return event
        resp = event.get("tool_response")
        out, err = resp.get("stdout"), resp.get("stderr")
        if not isinstance(out, str) or not isinstance(err, str):
            return event
        if _PTR16.match(err) or err.startswith(_RAW16):
            new_err = _RAW16 + err  # a stderr that already looks like our pointer must stay distinguishable
        else:
            nl = err.endswith("\n")
            errl = (err[:-1] if nl else err).split("\n")
            outl = out.split("\n")
            new_err = None
            for i in range(len(outl) - len(errl) + 1):
                if outl[i:i + len(errl)] == errl:
                    ptr = f"[stderr = stdout lines {i + 1}-{i + len(errl)}{'' if nl else ', no final newline'}]"
                    new_err = ptr if len(ptr) < len(err) and len(err) >= 120 else None
                    break
            if new_err is None:
                return event
        new = dict(event)
        new["tool_response"] = {**resp, "stderr": new_err}
        return new
    except Exception:  # noqa: BLE001  fail open: the model gets the original output
        return event


def reader_view_16(event: dict) -> dict:
    """What a reader can reconstruct from the rewritten response (the pointer is resolved against stdout)."""
    resp = event["tool_response"]
    err = resp.get("stderr")
    if isinstance(err, str):
        if err.startswith(_RAW16):
            err = err[len(_RAW16):]
        elif (m := _PTR16.match(err)):
            lo, hi = int(m.group(1)), int(m.group(2))
            err = "\n".join(resp["stdout"].split("\n")[lo - 1:hi]) + ("" if m.group(3) else "\n")
    return {**event, "tool_response": {**resp, "stderr": err}}


def sim_stream_dedupe() -> dict:
    log = "".join(f"[{i:02d}/24] compiling module_{i}.c\n" + (f"module_{i}.c:{i + 3}: warning: unused variable 'tmp{i}'\n" if i % 3 == 0 else "") for i in range(1, 25))
    ev = lambda out, err, tool="Bash": {"tool_name": tool, "tool_input": {"command": "make | tee /dev/stderr"}, "tool_response": {"stdout": out, "stderr": err, "interrupted": False}}  # noqa: E731
    saved = 0
    # (a) the saving, and (b) the reader view restores the exact original response
    cases = {
        "whole duplicate (tee /dev/stderr)": ev(log, log),
        "stderr is a block inside stdout": ev("== build ==\n" + log + "== done ==\n", "".join(log.splitlines(True)[10:30])),
        "no final newline on either side": ev(log.rstrip("\n"), log.rstrip("\n")),
        "stderr without final newline inside stdout": ev(log, "".join(log.splitlines(True)[4:20]).rstrip("\n")),
    }
    for name, e in cases.items():
        d = dedupe_streams(e)
        assert d is not e and len(d["tool_response"]["stderr"]) < 80, f"{name}: should have been deduplicated"
        assert reader_view_16(d) == e, f"{name}: reader view differs from the original"
        assert reader_view_16(reader_view_16(dedupe_streams(d))) == e, f"{name}: a second pass over the hook's own output must stay recoverable"
        saved += len(e["tool_response"]["stderr"]) - len(d["tool_response"]["stderr"])
    assert saved > 2000, saved
    # (c) must not apply
    keep = {
        "one character differs": ev(log, log.replace("unused", "unusad", 1)),
        "extra line at the end": ev(log, log + "extra\n"),
        "CRLF versus LF": ev(log, log.replace("\n", "\r\n")),
        "stderr empty": ev(log, ""),
        "tiny duplicate": ev("ok\n", "ok\n"),
        "different tool with the same keys": ev(log, log, tool="mcp__build__run"),
        "stderr not a block of whole lines": ev(log, log[5:400]),
        "lines out of order": ev(log, "".join(reversed(log.splitlines(True)[:6]))),
    }
    for name, e in keep.items():
        assert dedupe_streams(e) is e, f"{name}: must be left alone"
    # an unrelated stderr that already looks like the pointer or the escape stays distinguishable
    for odd in ("[stderr = stdout lines 1-2]", _RAW16 + "x", "[stderr = stdout lines 3-9, no final newline]"):
        e = ev(log, odd)
        d = dedupe_streams(e)
        assert reader_view_16(d) == e and d["tool_response"]["stderr"] != odd
    # (d) fail open on malformed events
    for bad in (None, 7, "x", {}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_response": None}, {"tool_name": "Bash", "tool_response": {"stdout": None, "stderr": "a" * 300}},
                {"tool_name": "Bash", "tool_response": {"stdout": ["a"], "stderr": ["a"]}}, {"tool_name": "Bash", "tool_response": "text"}):
        assert dedupe_streams(bad) is bad
    # fuzz: any stderr taken from mutants of stdout round-trips, and anything not an exact block is untouched
    n = 0
    for m in harness.mutants(log, 60, seed=16):
        for e in (ev(log, m), ev(m, log), ev(m, m)):
            d = dedupe_streams(e)
            assert reader_view_16(d) == e, f"round trip failed for {m[:60]!r}"
            n += 1
    return {"stderr_chars_saved_in_4_scenarios": saved, "fuzz_pairs_roundtripped": n, "tokens_saved_whole_duplicate": harness.tokens(log) - harness.tokens(dedupe_streams(cases["whole duplicate (tee /dev/stderr)"])["tool_response"]["stderr"])}


case(
    16,
    claim="In a Bash tool_response whose stderr equals stdout or a block of whole lines of it, stderr becomes '[stderr = stdout lines i-j]'; a reader resolves the pointer to the exact original, and mismatching, tiny, other-tool or malformed responses pass through untouched.",
    sim=sim_stream_dedupe,
)

# ---------------------------------------------------------------------------------------------------------------
# #614 Same-length ASCII for progress bars, sparklines and braille meters. One glyph becomes one ASCII character, so run lengths
# are kept exactly and the saving is in bytes (3 per glyph) and tokens, never in characters: run through `_run_bytes_gate`.
#   block bar   (no lower-block glyph in the run):  FULL # | light shade . | medium shade : | dark shade = | left 1/8..7/8 -> 1..7
#   sparkline   (run has a lower-block glyph)     :  lower 1/8..8/8 (full block = 8/8) -> 1..8
#   braille run (3+ cells, at least one dot)      :  blank . | all eight dots # | n dots -> n (1..7)

_BLOCKS = re.compile("[▁-▏░-▓]{3,}")
_BRAILLE = re.compile("[⠀-⣿]{3,}")
_LEFT_ASCII = {0x258F: "1", 0x258E: "2", 0x258D: "3", 0x258C: "4", 0x258B: "5", 0x258A: "6", 0x2589: "7"}


def _block_run(m: re.Match) -> str:
    run = m.group(0)
    spark = any("▁" <= c <= "▇" for c in run)
    out = []
    for c in run:
        o = ord(c)
        if 0x2581 <= o <= 0x2588 and (spark or o != 0x2588):
            out.append(str(o - 0x2580))  # lower n/8 block -> n; the full block is 8 in a sparkline
        elif o == 0x2588:
            out.append("#")
        elif o in _LEFT_ASCII:
            out.append(_LEFT_ASCII[o])
        else:
            out.append({0x2591: ".", 0x2592: ":", 0x2593: "="}[o])
    return "".join(out)


def _braille_run(m: re.Match) -> str:
    run = m.group(0)
    if all(c == "⠀" for c in run):
        return run  # invisible padding, not a meter
    return "".join("." if c == "⠀" else "#" if ord(c) == 0x28FF else str(bin(ord(c) - 0x2800).count("1")) for c in run)


def ascii_bars(text: str) -> str:
    out = _BRAILLE.sub(_braille_run, _BLOCKS.sub(_block_run, text))
    return out if len(out.encode()) < len(text.encode()) else text


_ORD = {("ONE", "EIGHTH"): 1, ("ONE", "QUARTER"): 2, ("THREE", "EIGHTHS"): 3, ("HALF",): 4, ("FIVE", "EIGHTHS"): 5, ("THREE", "QUARTERS"): 6, ("SEVEN", "EIGHTHS"): 7}


def _glyph(ch: str):
    """Oracle: classify from the Unicode name. -> (family, kind, value) or None."""
    n = unicodedata.name(ch, "")
    w = n.split()
    if n == "FULL BLOCK":
        return ("block", "full", 8)
    if w[-1:] == ["SHADE"]:
        return ("block", "shade", {"LIGHT": ".", "MEDIUM": ":", "DARK": "="}[w[0]])
    if w[:1] in (["LOWER"], ["LEFT"]) and w[-1:] == ["BLOCK"] and tuple(w[1:-1]) in _ORD:
        return ("block", w[0].lower(), _ORD[tuple(w[1:-1])])
    if n.startswith("BRAILLE PATTERN"):
        return ("braille", "cell", 0 if n.endswith("BLANK") else len(w[-1].split("-")[1]))
    return None


def _expected_614(x: str) -> str:
    out, i = [], 0
    while i < len(x):
        g = _glyph(x[i])
        if g is None:
            out.append(x[i])
            i += 1
            continue
        j = i
        while j < len(x) and (g2 := _glyph(x[j])) is not None and g2[0] == g[0]:
            j += 1
        run = [_glyph(c) for c in x[i:j]]
        if j - i < 3 or (g[0] == "braille" and not any(r[2] for r in run)):
            out.append(x[i:j])
        elif g[0] == "braille":
            out.append("".join("." if v == 0 else "#" if v == 8 else str(v) for _f, _k, v in run))
        else:
            spark = any(k == "lower" for _f, k, _v in run)
            out.append("".join(str(v) if k in ("lower", "left") or (k == "full" and spark) else "#" if k == "full" else v for _f, k, v in run))
        i = j
    return "".join(out)


def _oracle_614(x: str, e: str) -> bool:
    return e == x or (len(e) == len(x) and e == _expected_614(x))  # same length, level for level


def _bars_text() -> str:
    tq = "".join(f"{p:3d}%|{'█' * (p // 5)}{'▏▎▍▌▋▊▉'[p % 7] if p % 5 else ''}{' ' * (20 - p // 5 - 1)}| {p}/100 [00:{p // 4:02d}<00:{(100 - p) // 4:02d}, 12.{p % 10}it/s]\n" for p in range(5, 100, 9))
    shade = "".join(f"build {'▓' * k}{'▒' if k < 9 else ''}{'░' * (9 - k)} {k * 11}%\n" for k in range(0, 10, 2))
    spark = "cpu  ▁▂▃▅▇█▇▅▃▂▁▁▂▄▆█▆▄▂\nmem  ▄▄▅▅▆▆▇▇▇▇\nnet  ▁▁▁▂▁▁▁█▁▁▁▁\n"
    braille = "cpu0 ⣀⣀⣤⣤⣶⣶⣿⣿⣿⣷⣶⣤⣀⡀\nmem  ⠀⠀⠀⢀⣀⣤⣴⣶⣿⣿⣿⣿\nblank ⠀⠀⠀⠀ then ⠁⠃⠇⡇⣇⣧⣷⣿\n"
    return tq + shade + spark + braille


def sim_ascii_bars() -> dict:
    return _run_bytes_gate(
        "#614", ascii_bars, _oracle_614,
        [
            Fixture("tqdm bars, shade bars, sparklines, braille meters", _bars_text(), True),
            Fixture("short runs, a lone cursor block, a spinner, box drawing, emoji bar, invisible braille padding",
                    "██ two only\n█ cursor\n⠋ Loading\n┌──┐\n🟩🟩🟩⬜⬜\nx⠀⠀⠀⠀y\n▀▀▀▀ upper\n", False),
            Fixture("run of exactly three, mixed families, digits next to a bar", "███ ▁▂▃ ⠁⠃⠇ ███⠿⠿⠿ 45%|██▌|3\n▏▎▍\n░▒▓\n▇▇█", None),
        ],
        min_gain=0.2,
        seed=614,
    )


case(
    614,
    claim="Runs of 3+ block glyphs and braille cells become same-length ASCII (bar # . : =, sparkline levels 1-8, braille dot count 0-8 as . 1-7 #); the level of every glyph is kept, short runs, spinners and invisible padding are left alone, and the size saving is in bytes and tokens.",
    sim=sim_ascii_bars,
)

# ---------------------------------------------------------------------------------------------------------------
# #989 Fold Arabic-Indic, Devanagari, Thai (any Nd) digits to ASCII with int()/unicodedata.decimal. One digit stays one character.
# A token that also contains an ASCII letter (an identifier such as var\u0661 or COVID-\u0661\u0669) is left alone: Python and most languages treat
# the two digit forms as different identifiers. Saving is in bytes and tokens only, so this runs through `_run_bytes_gate`.

def fold_digits(text: str) -> str:
    if text.isascii():
        return text
    parts = re.split(r"(\s+)", text)
    for i in range(0, len(parts), 2):
        tok = parts[i]
        if not tok.isascii() and not re.search(r"[A-Za-z]", tok):
            parts[i] = "".join(str(unicodedata.decimal(c)) if unicodedata.category(c) == "Nd" and not c.isascii() else c for c in tok)
    return "".join(parts)


def _oracle_989(x: str, e: str) -> bool:
    if len(x) != len(e):
        return False
    start = 0
    for tok in re.split(r"(\s+)", x):
        mixed = re.search(r"[A-Za-z]", tok) is not None  # identifier-like: must be untouched
        for k, c in enumerate(tok):
            d = e[start + k]
            if unicodedata.category(c) == "Nd" and not c.isascii() and not mixed:
                if d != str(int(c)):  # int() parses every Nd digit: the folded character must be that value
                    return False
            elif d != c:
                return False
        start += len(tok)
    return True


def _load_atoms():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(harness.HERE))), "ctxprune", "atoms.py")
    import sys
    name = "ctxprune_atoms_for_989"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # @dataclass looks its module up in sys.modules while the class body runs
    try:
        spec.loader.exec_module(mod)  # atoms.py is stdlib-only: importing it by path avoids the model dependencies of the package
    finally:
        sys.modules.pop(name, None)
    return mod


_NUMBERS_989 = "\n".join([
    "\u062a\u0645 \u0646\u0633\u062e \u0663\u0664\u0662 \u0645\u0644\u0641\u0627\u064b \u0641\u064a \u0661\u0662\u066b\u0665 \u062b\u0627\u0646\u064a\u0629\u060c \u0627\u0644\u062d\u062c\u0645 \u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u0660 \u0628\u0627\u064a\u062a",
    "\u0632\u0645\u0627\u0646 \u0627\u062c\u0631\u0627: \u06f1\u06f4\u06f0\u06f3/\u06f0\u06f8/\u06f1\u06f2 \u0633\u0627\u0639\u062a \u06f1\u06f0:\u06f3\u06f5 \u0634\u0645\u0627\u0631\u0647 \u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "क\u0941ल \u0967\u0968\u0969\u096a फ\u093c\u093eइल\u0947\u0902, \u096d\u096e त\u094dर\u0941ट\u093fय\u093e\u0901, समय \u0966.\u096b स\u0947क\u0902ड",
    "พบ \u0e51\u0e52\u0e53 ไฟล\u0e4c ใช\u0e49เวลา \u0e54.\u0e55 ว\u0e34นาท\u0e35 รห\u0e31ส \u0e56\u0e57\u0e58\u0e59\u0e50",
    "port \uff18\uff10\uff18\uff10 and \U0001d7d0\U0001d7d1\U0001d7d2 and var\u0661 and COVID-\u0661\u0669 and x_\u0662",
]) + "\n"

_INTS_989 = "".join(f"{a} {b} {c}\n" for a, b, c in zip(["\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668", "\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6", "\u0967\u0968\u0969\u096a\u096b\u096c\u096d"] * 6,
                                                          ["\u0e51\u0e52\u0e53\u0e54", "\u0660\u0661\u0662", "\u0969\u096a\u096b"] * 6, ["\u0669\u0668\u0667\u0666", "\u06f9\u06f8\u06f7", "\u0e59\u0e58"] * 6))


def sim_digit_fold() -> dict:
    atoms = _load_atoms()
    # is_protected must still flag every folded number (it already flags the original digits, so nothing is un-protected)
    folded = fold_digits(_NUMBERS_989 + _INTS_989)
    n_prot = 0
    for tok in folded.split():
        if any(c.isdigit() and c.isascii() for c in tok):
            assert atoms.is_protected(tok), f"is_protected lost {tok!r}"
            n_prot += 1
    for ch in "\u0663\u06f4\u0967\u0e51":
        assert atoms.is_protected(ch + "2") and atoms.is_protected(str(unicodedata.decimal(ch)) + "2")
    # int() equality for every Nd digit of the script blocks the idea names, plus a few astral ones
    for cp in itertools.chain(range(0x0660, 0x066A), range(0x06F0, 0x06FA), range(0x0966, 0x0970), range(0x0E50, 0x0E5A), range(0xFF10, 0xFF1A), range(0x1D7CE, 0x1D800)):
        c = chr(cp)
        assert fold_digits(c) == str(int(c)) == str(unicodedata.decimal(c)), hex(cp)
    stats = _run_bytes_gate(
        "#989", fold_digits, _oracle_989,
        [
            Fixture("numbers in Arabic, Persian, Hindi, Thai, fullwidth and math digits", _NUMBERS_989, True),
            Fixture("integer columns in five scripts", _INTS_989, True),
            Fixture("ASCII digits, Arabic words without digits, identifiers with ASCII letters", "total 12345 files\n\u0645\u0644\u0641\u0627\u062a \u0628\u062f\u0648\u0646 \u0623\u0631\u0642\u0627\u0645\nvar\u0661 x\u0662_y COVID-\u0661\u0669 abc\u0967\n", False),
            Fixture("digits with marks, separators, a lone digit, superscripts (not Nd)", "\u0661\u066c\u0662\u0663\u0664\u066b\u0665 \u0663\u0301 \u0661 x² ⑧ \u0e51\u200d\u0e52 \u0663%\n", None),
        ],
        min_gain=0.15,
        seed=989,
    )
    stats["protected_numbers_checked"] = n_prot
    stats["tokens_before"] = harness.tokens(_NUMBERS_989)
    stats["tokens_after"] = harness.tokens(fold_digits(_NUMBERS_989))
    assert stats["tokens_after"] < stats["tokens_before"], "folding should lower the token count"
    return stats


case(
    989,
    claim="Every non-ASCII decimal digit (Arabic-Indic, Persian, Devanagari, Thai, fullwidth, math) in a token without ASCII letters becomes the ASCII digit with the same int() value, one character for one character; identifier-like tokens are untouched and is_protected still flags the folded numbers.",
    sim=sim_digit_fold,
)

# ---------------------------------------------------------------------------------------------------------------
# #1035 Merge adjacent text blocks inside one request message (proxy-level payload rewrite). Adjacent {"type": "text"} blocks that
# carry nothing but type/text/cache_control are joined with a newline; a block with cache_control closes its group, so no merge ever
# crosses a cache breakpoint. `respect_cc=False` is the alternative that ignores breakpoints and re-attaches the marker to the merged
# block; the sim shows why that one is the byte-stable choice when a breakpoint can sit mid-message.

_MERGEABLE_KEYS = {"type", "text", "cache_control"}


def merge_text_blocks(messages, respect_cc: bool = True):
    """Returns a new list (inputs are never mutated) or the very same object when nothing changed. Fails open on any error."""
    try:
        out, changed = [], False
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                out.append(msg)
                continue
            blocks, group = [], []

            def flush():
                nonlocal changed
                if len(group) > 1:
                    changed = True
                    merged = {"type": "text", "text": "\n".join(b["text"] for b in group)}
                    cc = next((b["cache_control"] for b in reversed(group) if "cache_control" in b), None)
                    if cc is not None:
                        merged["cache_control"] = cc
                    blocks.append(merged)
                else:
                    blocks.extend(group)
                group.clear()

            for b in content:
                ok = isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and set(b) <= _MERGEABLE_KEYS
                if not ok:
                    flush()
                    blocks.append(b)
                    continue
                group.append(b)
                if respect_cc and "cache_control" in b:
                    flush()  # the breakpoint sits after this block: nothing after it may join it
            flush()
            out.append({**msg, "content": blocks})
        return out if changed else messages
    except Exception:  # noqa: BLE001
        return messages


def reader_view_1035(messages) -> list:
    """What the model reads, per message: a list of segments; consecutive text blocks are one segment joined by a newline,
    every other block (tool_result, tool_use, image, ...) is its own segment compared by value."""
    view = []
    for msg in messages:
        segs, run = [], []
        content = msg["content"]
        if isinstance(content, str):
            view.append((msg["role"], [("text", content)]))
            continue
        for b in content:
            if b.get("type") == "text":
                run.append(b["text"])
            else:
                if run:
                    segs.append(("text", "\n".join(run)))
                    run = []
                segs.append((b.get("type"), json.dumps({k: v for k, v in b.items() if k != "cache_control"}, sort_keys=True)))
        if run:
            segs.append(("text", "\n".join(run)))
        view.append((msg["role"], segs))
    return view


def _history(turns: int, cc_policy: str) -> list[list[dict]]:
    """The request sent at each turn k (k = 1..turns). cc_policy: 'last' puts one breakpoint on the last block of the last message
    (Claude Code), 'mid' puts it on the first reminder block of the last user message (a client that marks a non-final block)."""
    reqs = []
    for k in range(1, turns + 1):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Fix the failing test in cart.py"}]}]
        for t in range(1, k):
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Running tests, step {t}."}, {"type": "tool_use", "id": f"toolu_{t:02d}", "name": "Bash", "input": {"command": f"pytest -x -k case{t}"}}]})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{t:02d}", "content": f"case{t}: 1 failed, 4 passed"},
                                                      {"type": "text", "text": "<system-reminder>\nTodo list is empty.\n</system-reminder>"},
                                                      {"type": "text", "text": f"<system-reminder>\nToday is 2026-11-04, turn {t}.\n</system-reminder>"}]})
        last = msgs[-1]["content"]
        if cc_policy == "last":
            last[-1] = {**last[-1], "cache_control": {"type": "ephemeral"}}
        elif len(last) > 1:
            last[-2 if len(last) > 2 else 0] = {**last[-2 if len(last) > 2 else 0], "cache_control": {"type": "ephemeral"}}
        reqs.append(msgs)
    return reqs


def _dump(msgs) -> str:
    return json.dumps(msgs, sort_keys=True, separators=(",", ":"))


def _prefix_stable(reqs, respect_cc: bool, ignore_cc_marker: bool = True) -> bool:
    """Is the serialized form of every message that was already sent identical on the next turn? (Only then does the cached prefix stay valid.)
    The breakpoint marker itself moves every turn by design, so it is not part of the comparison."""
    strip = lambda ms: [{**m, "content": [{k: v for k, v in b.items() if k != "cache_control"} if ignore_cc_marker and isinstance(b, dict) else b for b in m["content"]]} for m in ms]  # noqa: E731
    prev = None
    for r in reqs:
        cur = strip(merge_text_blocks(r, respect_cc))
        if prev is not None and _dump(prev) != _dump(cur[: len(prev)]):
            return False
        prev = cur
    return True


def sim_merge_text_blocks() -> dict:
    reqs = _history(30, "last")
    final = reqs[-1]
    merged = merge_text_blocks(final)
    # (a) the saving: one boundary per user turn, counted in blocks and in serialized bytes
    nblocks = lambda ms: sum(len(m["content"]) for m in ms if isinstance(m["content"], list))  # noqa: E731
    boundaries = nblocks(final) - nblocks(merged)
    saved_bytes = len(_dump(final)) - len(_dump(merged))
    assert boundaries == 29 and saved_bytes > 29 * 20, (boundaries, saved_bytes)
    assert merged is not final and len(final[-1]["content"]) == 3, "the input must not be mutated"
    # (b) reader view: the model reads the same segments, tool_use/tool_result ids in the same order
    assert reader_view_1035(merged) == reader_view_1035(final)
    ids = lambda ms: [b.get("id") or b.get("tool_use_id") for m in ms for b in m["content"] if b.get("type") in ("tool_use", "tool_result")]  # noqa: E731
    assert ids(merged) == ids(final) and len(ids(final)) == 58
    assert merged[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}, "the breakpoint rides on the merged block"
    assert merge_text_blocks(merged) is merged, "merging twice changes nothing"
    # (c) where it must not apply
    tr = {"type": "tool_result", "tool_use_id": "t1", "content": "x"}
    a, b, c = ({"type": "text", "text": s} for s in "abc")
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    def one(blocks, **kw):
        return merge_text_blocks([{"role": "user", "content": blocks}], **kw)[0]["content"]
    assert one([a, tr, b]) == [a, tr, b], "a tool_result between two text blocks"
    assert one([a, img, b]) == [a, img, b]
    assert one([{**a, "cache_control": {"type": "ephemeral"}}, b]) == [{**a, "cache_control": {"type": "ephemeral"}}, b], "never across a breakpoint"
    assert one([a, {**b, "cache_control": {"type": "ephemeral"}}]) == [{"type": "text", "text": "a\nb", "cache_control": {"type": "ephemeral"}}]
    cit = {"type": "text", "text": "q", "citations": [{"type": "char_location"}]}
    assert one([a, cit, b]) == [a, cit, b], "a block with citations carries metadata that cannot be joined"
    assert one([{"type": "thinking", "thinking": "hm", "signature": "sig"}, a, b]) == [{"type": "thinking", "thinking": "hm", "signature": "sig"}, {"type": "text", "text": "a\nb"}]
    only_str = [{"role": "user", "content": "plain string content"}]
    assert merge_text_blocks(only_str) is only_str
    single = [{"role": "user", "content": [a]}]
    assert merge_text_blocks(single) is single, "one block: nothing to merge, same object back"
    # (d) fail open: malformed payloads are returned as they came, never raised
    class Boom(dict):
        def get(self, *a):
            raise RuntimeError("poisoned")
    for bad in (None, 5, "x", [None], [{"role": "user"}], [{"role": "user", "content": [None, 3]}], [{"role": "user", "content": [{"type": "text", "text": 5}, {"type": "text", "text": "a"}]}],
                [Boom(role="user", content=[a, b])]):
        r = merge_text_blocks(bad)
        assert r is bad or r == bad, bad
    # (e) byte-stable prefixes across 30 turns, and the hazard the idea has to avoid
    assert _prefix_stable(_history(30, "last"), respect_cc=True), "with the breakpoint on a message's last block the prefix stays identical"
    assert not _prefix_stable(_history(30, "mid"), respect_cc=True), "respect_cc with a mid-message breakpoint flips a message between merged and unmerged"
    assert _prefix_stable(_history(30, "mid"), respect_cc=False), "ignoring the breakpoint position keeps every earlier message byte-identical"
    return {"turns": 30, "boundaries_merged": boundaries, "json_bytes_saved": saved_bytes, "bytes_saved_per_boundary": round(saved_bytes / boundaries, 1),
            "blocks_before": nblocks(final), "blocks_after": nblocks(merged), "stable_when_cc_on_last_block": 1, "unstable_when_cc_mid_message_and_respected": 1}


case(
    1035,
    claim="Adjacent plain text blocks in one message are joined with a newline unless a cache_control breakpoint sits between them, the reader sees the same segments and tool ids, blocks with other keys, other block types and malformed payloads are left alone, and the merged prefix is byte-identical across turns while the breakpoint sits on a message's last block.",
    sim=sim_merge_text_blocks,
)
