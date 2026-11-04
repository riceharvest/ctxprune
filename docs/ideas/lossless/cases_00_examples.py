"""Worked examples of the three case kinds. Copy the shape; see harness.py for the rules.

#12 (equivalent), #10 (equivalent, with a CRLF trap), #100 (byte-exact roundtrip with an in-band escape), #869 (sim).
"""
import itertools
import json
import os
import re
import tempfile

from harness import Fixture, case

# ---------------------------------------------------------------------------------------------------------------
# #12 Collapse blank-line runs. Equivalent, not roundtrip: the number of blank lines in a run of 3+ is gone.

_BLANK_RUN = re.compile(r"\n(?:[ \t]*\n){3,}")


def collapse_blank_runs(text: str) -> str:
    return _BLANK_RUN.sub("\n\n", text)


def _skeleton(text: str):
    """Independent oracle view: non-blank lines verbatim, every blank run (any length) as one marker."""
    for blank, grp in itertools.groupby(text.split("\n"), key=lambda l: not l.strip()):
        if blank:
            yield "<blank>"
        else:
            yield from grp


LOG = "build started\n" + "\n" * 5 + "step 1 ok\n   \n\t\n\n\nstep 2 ok\n\n\n\n\nwarning: cache slow\n"
PEP8 = "import os\n\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"  # two blank lines: left alone

case(
    12,
    claim="Runs of 3+ blank lines (whitespace-only counts as blank) become one; every non-blank line and every paragraph break survives in order.",
    loses="how many blank lines a run of three or more had, and whitespace on the collapsed blank lines",
    encode=collapse_blank_runs,
    equivalent=lambda a, b: list(_skeleton(a)) == list(_skeleton(b)),
    fixtures=[Fixture("log with gaps", LOG * 3, True), Fixture("PEP8 spacing", PEP8, False), Fixture("no blanks", "a\nb\nc\n", False)],
)

# ---------------------------------------------------------------------------------------------------------------
# #10 Collapse CR progress updates to the final frame. The trap: CRLF line endings are not progress frames.

_FRAME = re.compile(r"[^\r\n]*\r+(?=[^\r\n])")


def collapse_cr(text: str) -> str:
    return _FRAME.sub("", text)


def _final_frames(text: str) -> list[str]:
    """Independent oracle view: per line, the last non-empty frame between carriage returns."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        frames = [f for f in line.split("\r") if f]
        out.append(frames[-1] if frames else "")
    return out


PIP = "Downloading numpy-2.0.0.whl\n" + "".join(f"\r  {p:3d}% |{'#' * (p // 5):<20}| {p * 160} kB" for p in range(0, 101, 4)) + "\ndone\n"
CRLF = "line one\r\nline two\r\nline three\r\n"

case(
    10,
    claim="Within a line, only the last non-empty carriage-return frame is kept; CRLF endings, line count and trailing CRs are untouched.",
    loses="intermediate progress frames",
    encode=collapse_cr,
    equivalent=lambda a, b: _final_frames(a) == _final_frames(b) and a.count("\r\n") == b.count("\r\n") and a.count("\n") == b.count("\n"),
    fixtures=[Fixture("pip progress bar", PIP, True), Fixture("CRLF file is not progress", CRLF, False), Fixture("plain", "a\nb\n", False)],
    min_gain=0.5,
)

# ---------------------------------------------------------------------------------------------------------------
# #100 Path aliasing. Byte-exact roundtrip with a legend header. Fixed escape for text that starts with the sigil.

SIGIL, HDR, RAW = "§", "§paths ", "§raw\n"
_PATH = re.compile(r"/(?:[\w.@+-]+/)+")


def alias_paths(text: str) -> str:
    if text.startswith(SIGIL):
        return RAW + text  # escape: otherwise decode could not tell this from an encoded text
    if SIGIL in text:
        return text
    counts: dict[str, int] = {}
    for m in _PATH.finditer(text):
        parts = m.group(0).split("/")
        for k in range(3, len(parts)):  # directory prefixes with at least two components
            p = "/".join(parts[:k])
            counts[p] = counts.get(p, 0) + 1
    chosen: list[str] = []
    body = text
    for _ in range(9):
        best, gain = None, 0
        for p, _n in counts.items():
            n = body.count(p)
            g = n * (len(p) - 2) - (len(p) + 4)  # alias costs 2 chars; legend entry costs path + sigil + id + '=' + space
            if g > gain:
                best, gain = p, g
        if best is None:
            break
        chosen.append(best)
        body = body.replace(best, f"{SIGIL}{len(chosen)}")
    if not chosen:
        return text
    out = HDR + " ".join(f"{i}={p}" for i, p in enumerate(chosen, 1)) + "\n" + body
    return out if len(out) < len(text) else text


def unalias_paths(text: str) -> str:
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(HDR):
        return text
    head, _, body = text.partition("\n")
    legend = [e.split("=", 1) for e in head[len(HDR):].split(" ")]
    for i, p in sorted(legend, key=lambda e: -int(e[0])):
        body = body.replace(f"{SIGIL}{i}", p)
    return body


PATHS = "".join(f"error in /home/runner/work/app/app/src/module{i}/handler.py line {i}\n  at /home/runner/work/app/app/src/lib/util.py\n" for i in range(12))
case(
    100,
    claim="Repeated directory prefixes become §N aliases with a one-line legend; decode restores the exact original, including text that already contains the sigil.",
    encode=alias_paths,
    decode=unalias_paths,
    fixtures=[
        Fixture("repeated paths", PATHS, True),
        Fixture("no repeated paths", "see /etc/hosts and /usr/bin/env\n", False),
        Fixture("input that starts like encoder output", "§paths 1=/a/b/c\n§1/x and §raw\nplain\n", None),
        Fixture("sigil in the middle", PATHS + "price: 5§\n", None),
    ],
    min_gain=0.2,
    idempotent=False,  # re-encoding encoded text must escape it (it starts with the sigil), so it grows by design
)

# ---------------------------------------------------------------------------------------------------------------
# #869 Latency circuit breaker: a policy, so a sim. Fake clock; the stamp file survives across "processes".


class Breaker:
    def __init__(self, stamp: str, slow_s: float, backoff_s: float, clock):
        self.stamp, self.slow_s, self.backoff_s, self.clock = stamp, slow_s, backoff_s, clock

    def tripped(self) -> bool:
        try:
            return self.clock() < float(open(self.stamp).read())
        except (OSError, ValueError):
            return False  # no or corrupt stamp: fail open to the model path

    def call(self, text: str, model, lossless) -> str:
        if self.tripped():
            return lossless(text)
        t0 = self.clock()
        out = model(text)
        if self.clock() - t0 > self.slow_s:
            open(self.stamp, "w").write(str(self.clock() + self.backoff_s))
        return out


def sim_breaker() -> dict:
    now = [0.0]
    spent = [0.0]

    def model(t):  # first three calls are slow (a throttled laptop), later ones fast
        d = 8.0 if model.calls < 3 else 0.5
        model.calls += 1
        now[0] += d
        spent[0] += d
        return "MODEL:" + t

    model.calls = 0
    lossless = lambda t: "LOSSLESS:" + t  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        stamp = os.path.join(d, "stamp")
        b = Breaker(stamp, slow_s=5, backoff_s=600, clock=lambda: now[0])
        assert b.call("a", model, lossless) == "MODEL:a" and b.tripped(), "one slow call must trip the breaker"
        for _ in range(20):  # a burst inside the window: all pass through, no model time spent
            now[0] += 10
            assert b.call("x", model, lossless) == "LOSSLESS:x"
        assert model.calls == 1 and spent[0] == 8.0, "no model call while tripped"
        now[0] += 600
        fresh = Breaker(stamp, 5, 600, lambda: now[0])  # a new process reads the same stamp
        assert not fresh.tripped(), "window expires"
        assert fresh.call("b", model, lossless) == "MODEL:b"  # call 2: still slow, trips again
        open(stamp, "w").write("garbage")
        assert not Breaker(stamp, 5, 600, lambda: now[0]).tripped(), "corrupt stamp fails open"
    return {"model_seconds_spent": spent[0], "model_seconds_without_breaker_for_22_calls": 8 * 3 + 0.5 * 19}


case(
    869,
    claim="After one call slower than the threshold the model path is skipped for the backoff window (lossless rules only, zero model time), then retried; a stamp shared by new processes persists the state and a corrupt stamp fails open.",
    sim=sim_breaker,
)
