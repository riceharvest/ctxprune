"""Rule cases (probes, not inverses) for log, ops and ML-output ideas:
#124 #125 #411 #413 #415 #416 #417 #419 #425 #804 #809 #885 #978 #979 #982 #1069 #1155 #1163 #1327 #1328 #1330.

Every idea here drops information on purpose, so each case pins what must survive with `probes` (a different parser
from the encoder wherever that is practical) and says what is lost in `loses=`. Conventions used throughout:

* Every encoder is total, self-gating (strictly shorter or the input comes back untouched) and refuses (returns the
  input unchanged) when the input already contains the encoder's own marker, so re-encoding is the identity.
* Regexes use re.ASCII and spell separators out as ' ' (never \\s): the fuzzer inserts NBSP, BOM, ZWSP, CR and tabs, and
  the probes split on ' ' only, so both sides agree on what a field is.
* Probes read the *virtual* log: a fold marker "[... N lines folded ...]" stands for N lines, so positions ("how many
  lines before the first error") and totals are comparable between the original and the folded text.
* Fixtures come from seeded generators (deterministic) in the shape of the real tool output; a few are captured from
  tools installed here (vmstat) and the rest are reconstructed from memory of the tool's format and say so.
"""
from __future__ import annotations

import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict

from harness import Fixture, case, tokens  # noqa: F401  (tokens used by the sizing helper at the bottom)

A = re.ASCII


def split_nl(text: str) -> tuple[list[str], bool]:
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return (text.split("\n") if text else []), False


def join_nl(lines: list[str], nl: bool) -> str:
    return "\n".join(lines) + ("\n" if nl else "")


def gate(text: str, out: str) -> str:
    """Self-gating: the encoding only if strictly shorter."""
    return out if len(out) < len(text) else text


def toks(line: str) -> list[str]:
    """Fields split on ASCII spaces only (what the regexes call a separator); empty fields dropped."""
    return [t for t in line.split(" ") if t]


def isdigits(s: str) -> bool:
    return s.isascii() and s.isdigit()


def clock_ms(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{10 + h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ===============================================================================================================
# #425 Severity-run folding: runs of TRACE/DEBUG/INFO lines between warnings become one marker line.
#
# Foldable line: "DATE TIME LEVEL ..." (log4j / Python logging) or glog "Immdd TIME pid file:line] ..." with level
# TRACE/DEBUG/INFO/I, that contains no alarm word anywhere. Everything else (WARN and above, stack traces, banners,
# blank lines, lines the format does not recognise) is kept verbatim. Two lines before and after every alarm line are
# kept as context. A run is folded only when the marker is shorter than the run it replaces.

_ALARM_KEYS = ("WARN", "ERROR", "FATAL", "CRITICAL", "SEVERE", "Traceback", "Exception")
_ALARM = re.compile(r"WARN|ERROR|FATAL|CRITICAL|SEVERE|Traceback|Exception|^ *[WEF]\d{4} +\d\d:\d\d:\d\d", A)
_PYLV = re.compile(r"^ *(\d{4}-\d\d-\d\d) +(\d\d:\d\d:\d\d(?:[.,]\d+)?) +(TRACE|DEBUG|INFO)(?= |$)(.*)$", A)
_GLOGI = re.compile(r"^ *I(\d{4}) +(\d\d:\d\d:\d\d(?:\.\d+)?) +\d+ +[^ ]+:\d+\](?: |$)(.*)$", A)
_M425 = re.compile(r"^\[\.\.\. (\d+) lines folded \(([^()]*?) \.\. ([^()]*?)\): ((?:[A-Z]+ \d+(?:, )?)+); top: ", A)


def _sev_info(line: str):
    """(timestamp, level, message) for a foldable line, else None."""
    if _ALARM.search(line):
        return None
    m = _PYLV.match(line)
    if m:
        return f"{m.group(1)} {m.group(2)}", m.group(3), m.group(4).strip()
    m = _GLOGI.match(line)
    if m:
        return f"{m.group(1)} {m.group(2)}", "INFO", m.group(3).strip()
    return None


def _template(msg: str) -> str:
    return re.sub(r"[0-9a-f]{8,}|\d+", "<n>", msg, flags=A)[:48]


def _sev_marker(infos) -> str:
    lv = Counter(i[1] for i in infos)
    tpl = Counter(_template(i[2]) for i in infos)
    first = {}
    for k, i in enumerate(infos):
        first.setdefault(_template(i[2]), k)
    top = sorted(tpl, key=lambda t: (-tpl[t], first[t]))[:3]
    levels = ", ".join(f"{k} {lv[k]}" for k in sorted(lv))
    tops = "; ".join(f"'{t}' x{tpl[t]}" for t in top)
    return f"[... {len(infos)} lines folded ({infos[0][0]} .. {infos[-1][0]}): {levels}; top: {tops}]"


def fold_severity(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M425.match(l) for l in lines):
        return text
    n = len(lines)
    info = [_sev_info(l) for l in lines]
    keep = [i is None for i in info]
    for i, l in enumerate(lines):
        if _ALARM.search(l):
            for d in range(max(0, i - 2), min(n, i + 3)):
                keep[d] = True
    out: list[str] = []
    i = 0
    while i < n:
        if keep[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < n and not keep[j]:
            j += 1
        marker = _sev_marker(info[i:j])
        if len(marker) < sum(len(l) + 1 for l in lines[i:j]):
            out.append(marker)
        else:
            out += lines[i:j]
        i = j
    return gate(text, join_nl(out, nl))


def _isdate(s: str) -> bool:
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and isdigits(s[:4] + s[5:7] + s[8:])


def _istime(s: str, frac: str = ".,") -> bool:
    if len(s) < 8 or s[2] != ":" or s[5] != ":" or not isdigits(s[:2] + s[3:5] + s[6:8]):
        return False
    rest = s[8:]
    return rest == "" or (rest[0] in frac and len(rest) > 1 and isdigits(rest[1:]))


_LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL", "SEVERE")


def _glog_head(line: str):
    """Probe-side glog parser: (letter, 'mmdd HH:MM:SS.frac') when the line starts like a glog record, else None."""
    t = toks(line)
    if len(t) >= 4 and len(t[0]) == 5 and t[0][0] in "IWEF" and isdigits(t[0][1:]) and _istime(t[1], "."):
        if isdigits(t[2]) and t[3].endswith("]") and ":" in t[3] and isdigits(t[3][:-1].rsplit(":", 1)[1]) and t[3].rsplit(":", 1)[0]:
            return t[0][0], f"{t[0][1:]} {t[1]}"
    return None


def _sev_level(line: str):
    """Probe-side classifier: (timestamp, level) or None. A different implementation from the encoder's regexes."""
    t = toks(line)
    if len(t) >= 3 and _isdate(t[0]) and _istime(t[1]) and t[2] in _LEVELS:
        return f"{t[0]} {t[1]}", t[2]
    g = _glog_head(line)
    if g:
        return g[1], {"I": "INFO", "W": "WARN", "E": "ERROR", "F": "FATAL"}[g[0]]
    return None


def _is_alarm(line: str) -> bool:
    """Broad on purpose: a keyword anywhere in the line, or a glog W/E/F record."""
    if any(k in line for k in _ALARM_KEYS):
        return True
    t = toks(line)
    return len(t) >= 2 and len(t[0]) == 5 and t[0][0] in "WEF" and isdigits(t[0][1:]) and len(t[1]) >= 8 and t[1][2] == ":" and t[1][5] == ":" and isdigits(t[1][:2] + t[1][3:5] + t[1][6:8])


def _is_error_class(line: str) -> bool:
    if any(k in line for k in ("ERROR", "FATAL", "CRITICAL", "SEVERE", "Exception", "Traceback")):
        return True
    g = _glog_head(line)
    return bool(g and g[0] in "EF")


def probes_425(text: str) -> dict:
    lines, _ = split_nl(text)
    vtext: list[str | None] = []  # one entry per virtual line; None inside a fold marker
    levels: Counter = Counter()
    stamps: list[str] = []
    other: list[tuple[int, str]] = []
    alarms: list[int] = []
    first_err, before = None, None
    for line in lines:
        m = _M425.match(line)
        if m:
            vtext += [None] * int(m.group(1))
            for part in m.group(4).split(", "):
                k, v = part.split(" ")
                levels[k] += int(v)
            stamps += [m.group(2), m.group(3)]
            continue
        v = len(vtext)
        vtext.append(line)
        if first_err is None and _is_error_class(line):
            first_err, before = v, sorted(levels.items())
        lv = _sev_level(line)
        if lv:
            levels[lv[1]] += 1
            stamps.append(lv[0])
        if _is_alarm(line):
            alarms.append(v)
        elif lv is None:
            other.append((v, line))
    ctx = [[vtext[v + d] if 0 <= v + d < len(vtext) else "" for d in (-2, -1, 1, 2)] for v in alarms]
    return {
        "alarm lines (virtual line number, text)": [(v, vtext[v]) for v in alarms],
        "2 lines around each alarm": ctx,
        "non-log lines kept (virtual line number, text)": other,
        "lines per level": sorted(levels.items()),
        "virtual line count": len(vtext),
        "first and last timestamp": (stamps[0], stamps[-1]) if stamps else None,
        "lines before the first error-class alarm, and their levels": (first_err, before),
    }


def _py_log(seed: int, n: int, events: dict[int, str], start_ms: int = 1500) -> str:
    """A Python-logging service log: DEBUG/INFO traffic with `events` (index -> full line) spliced in."""
    rng = random.Random(seed)
    out, ms = [], start_ms
    for i in range(n):
        ms += rng.randint(3, 140)
        if i in events:
            out.append(events[i].replace("@", f"2024-03-05 {clock_ms(ms)}"))
            continue
        r = rng.random()
        if r < 0.35:
            out.append(f"2024-03-05 {clock_ms(ms)} DEBUG worker.cache: cache hit key=item:{rng.randint(100, 9999)}")
        elif r < 0.7:
            out.append(f"2024-03-05 {clock_ms(ms)} INFO worker.http: GET /api/items/{rng.randint(100, 9999)} 200 {rng.randint(2, 90)}ms")
        elif r < 0.9:
            out.append(f"2024-03-05 {clock_ms(ms)} INFO worker.db: fetched {rng.randint(1, 60)} rows in {rng.randint(1, 30)} ms")
        else:
            out.append(f"2024-03-05 {clock_ms(ms)} DEBUG worker.pool: connection {rng.randint(1, 8)} returned to pool")
    return "\n".join(out) + "\n"


TRACE_425 = ["Traceback (most recent call last):", '  File "/srv/app/worker/jobs.py", line 88, in run_job', "    result = handler(payload)",
             '  File "/srv/app/worker/handlers.py", line 41, in charge', "    raise TimeoutError(\"upstream timed out after 5s\")", "TimeoutError: upstream timed out after 5s"]
_E425_MID = {0: "@ WARN worker.boot: config file /etc/app.toml not found, using defaults",
             118: "@ WARN worker.db: slow query 2310 ms on orders",
             119: "@ ERROR worker.jobs: job 4411 failed: upstream timed out after 5s",
             **{120 + k: l for k, l in enumerate(TRACE_425)},
             300: "@ WARN worker.pool: pool at 8/8 connections, requests are queueing"}


def _glog_log() -> str:
    rng = random.Random(425)
    out = []
    for i in range(70):
        us = 100000 + i * 3917
        stamp = f"0305 10:15:{us // 1000000 + 1:02d}.{us % 1000000:06d}"
        if i == 3:
            out.append(f"W{stamp} 1337 gossip.cc:212] peer 10.0.0.7 missed 3 heartbeats")
        elif i == 66:
            out.append(f"E{stamp} 1337 raft.cc:508] lost quorum: 1 of 3 replicas reachable")
        else:
            out.append(f"I{stamp} 1337 server.cc:{rng.randint(40, 90)}] handled request id={rng.randint(1000, 99999)} in {rng.randint(1, 40)}us")
    return "\n".join(out) + "\n"


_CI_LOG = "$ make test\ncc -c main.c\ncc -c util.c\nlinking\n./run_tests\nok 1 - parse\nok 2 - render\nnot ok 3 - save\n"
_ALL_ALARM = "".join(f"2024-03-05 10:15:{i:02d},000 WARN worker.net: retry {i}\n" for i in range(12))

case(
    425,
    claim="Runs of TRACE/DEBUG/INFO lines (log4j, Python logging, glog) fold to one '[... N lines folded (first .. last): per-level counts; top 3 templates]' line; every line with a WARN/ERROR/FATAL/Traceback/Exception word, every line the format does not recognise, and 2 lines of context around each alarm stay verbatim, and virtual line positions are exact.",
    loses="the individual messages of folded DEBUG/INFO lines (only the top 3 templates with counts, the level counts and the first/last timestamp survive), and the ids and values inside them",
    encode=fold_severity,
    probes=probes_425,
    fixtures=lambda: [
        Fixture("service log, WARN at the start, ERROR+traceback in the middle, WARN near the end", _py_log(425, 330, _E425_MID), True),
        Fixture("failure variant: ERROR is the very first line, nothing after it", "2024-03-05 10:15:01,000 ERROR worker.boot: cannot bind 0.0.0.0:8080 (address in use)\n" + _py_log(7, 90, {}), True),
        Fixture("glog service, W near the start and E at the end", _glog_log(), True),
        Fixture("CI log with no levels", _CI_LOG, False),
        Fixture("every line is a WARN", _ALL_ALARM, False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #417 Fold identical lines across distributed ranks: torchrun '[rankN]:' and Open MPI '[job,rank]<stdout>:' prefixes.
#
# Inside a run of consecutive prefixed lines, lines with the same body from different ranks join one group
# '[ranks 0-3] body'; a rank's own order is never changed (a line may only join a group that comes after that rank's
# previous line), and a group with a single rank is the original line, prefix included, so rank-specific lines stay
# attributed. Unprefixed lines end a run and are never touched.

_TORCH = re.compile(r"^\[rank(0|[1-9]\d*)\]:(.*)$", A)
_MPI = re.compile(r"^\[(0|[1-9]\d*),(0|[1-9]\d*)\]<(stdout|stderr)>:(.*)$", A)
_M417 = re.compile(r"^\[ranks ([0-9,-]+)(?:\|([a-z0-9:]+))?\] (.*)$", A)


def _src(line: str):
    m = _TORCH.match(line)
    if m:
        return "", int(m.group(1)), m.group(2)
    m = _MPI.match(line)
    if m:
        return f"{m.group(1)}:{m.group(3)}", int(m.group(2)), m.group(4)
    return None


def _rank_list(ranks: list[int]) -> str:
    rs = sorted(set(ranks))
    parts, i = [], 0
    while i < len(rs):
        j = i
        while j + 1 < len(rs) and rs[j + 1] == rs[j] + 1:
            j += 1
        parts.append(str(rs[i]) if i == j else f"{rs[i]}-{rs[j]}")
        i = j + 1
    return ",".join(parts)


def _fold_ranks(items) -> list[str]:
    groups: list[list] = []  # [tag, body, ranks, raw line]
    last: dict = {}
    for tag, rank, body, raw in items:
        for gi in range(last.get((tag, rank), -1) + 1, len(groups)):
            g = groups[gi]
            if g[0] == tag and g[1] == body and rank not in g[2]:
                g[2].append(rank)
                last[(tag, rank)] = gi
                break
        else:
            groups.append([tag, body, [rank], raw])
            last[(tag, rank)] = len(groups) - 1
    out = []
    for tag, body, ranks, raw in groups:
        if len(ranks) == 1:
            out.append(raw)
        else:
            out.append(f"[ranks {_rank_list(ranks)}{'|' + tag if tag else ''}] {body}")
    return out


def fold_ranks(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M417.match(l) for l in lines):
        return text
    out: list[str] = []
    run: list = []

    def flush():
        out.extend(_fold_ranks(run))
        run.clear()

    for l in lines:
        s = _src(l)
        if s:
            run.append((s[0], s[1], s[2], l))
        else:
            flush()
            out.append(l)
    flush()
    return gate(text, join_nl(out, nl))


def _src_probe(line: str):
    """Probe-side prefix parser (string surgery, not the encoder's regexes)."""
    if line.startswith("[rank"):
        j = line.find("]:")
        r = line[5:j] if j > 5 else ""
        if isdigits(r) and (r == "0" or r[0] != "0"):
            return "", int(r), line[j + 2:]
    elif line.startswith("["):
        head, sep, rest = line[1:].partition("]<")
        job, comma, rk = head.partition(",")
        stream, sep2, body = rest.partition(">:")
        if sep and comma and sep2 and stream in ("stdout", "stderr") and all(isdigits(x) and (x == "0" or x[0] != "0") for x in (job, rk)):
            return f"{job}:{stream}", int(rk), body
    return None


def _expand(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def probes_417(text: str) -> dict:
    """What each rank said, in its own order, plus every line no rank prefix claims, at its position among those."""
    lines, _ = split_nl(text)
    per_rank: dict = defaultdict(list)
    plain: list[tuple[int, str]] = []
    n_prefixed = 0
    for l in lines:
        m = _M417.match(l)
        if m and all(isdigits(x) for p in m.group(1).split(",") for x in p.split("-")):
            for r in _expand(m.group(1)):
                per_rank[(m.group(2) or "", r)].append(m.group(3))
                n_prefixed += 1
            continue
        s = _src_probe(l)
        if s:
            per_rank[(s[0], s[1])].append(s[2])
            n_prefixed += 1
        else:
            plain.append((n_prefixed, l))
    return {
        "lines said by each rank, in that rank's order": sorted((k, v) for k, v in per_rank.items()),
        "lines without a rank prefix (count of rank lines before it, text)": plain,
    }


def _torch_run(seed: int, ranks: int, events: list, tag=lambda r: f"[rank{r}]:") -> str:
    """events: list of (body, only_rank) or (body, None) for all ranks; ranks shuffle within an event, like real interleaving."""
    rng = random.Random(seed)
    out = []
    for ev in events:
        if isinstance(ev, str):
            out.append(ev)
            continue
        body, only = ev
        rs = [only] if only is not None else list(range(ranks))
        if only is None:
            rng.shuffle(rs)
        for r in rs:
            out.append(tag(r) + (body(r) if callable(body) else body))
    return "\n".join(out) + "\n"


_UW = "/opt/conda/lib/python3.10/site-packages/torch/nn/parallel/distributed.py:1155: UserWarning: find_unused_parameters=True was specified in DDP constructor, but did not find any unused parameters in the forward pass."
_EV_ALL = [
    "W0305 10:15:00.000000 140234 torch/distributed/run.py:757] *****************************************",
    ("[W305 10:15:02.123 ProcessGroupNCCL.cpp:1423] Warning: NCCL_ASYNC_ERROR_HANDLING is deprecated", None),
    (lambda r: f"Rank {r} of 4 ready, device cuda:{r}", None),
    (_UW, None),
    ("Epoch 0: starting, lr=3e-4, batch=32 per rank", None),
    (lambda r: f"step 10 loss={2.3041 + 0.0003 * r:.4f}", None),
]


def _ddp_nan() -> str:
    ev = list(_EV_ALL)
    ev += [("step 20 loss=2.1177", 0), ("step 30 loss=1.9024", 0)]
    ev += [("Epoch 0: step 40 grad_norm=nan (non-finite gradient, skipping update)", 2)]
    ev += [("step 50 loss=nan", 2), ("step 50 loss=1.8312", 0)]
    ev += [("checkpoint saved to out/ckpt-50.pt", 0), (_UW, None), ("Epoch 1: starting, lr=3e-4, batch=32 per rank", None), (lambda r: f"step 60 loss={1.8 + 0.0002 * r:.4f}", None)]
    ev += [("Training finished: 2 epochs, best loss 1.8312", 0)]
    return _torch_run(417, 4, ev)


def _ddp_oom() -> str:
    ev = list(_EV_ALL)
    ev += [(lambda r: f"step 20 loss={2.1 + 0.0004 * r:.4f}", None)]
    tb = ["Traceback (most recent call last):", '  File "/work/train.py", line 91, in <module>', "    loss.backward()"]
    ev += [(t, None) for t in tb]
    ev += [("torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB. GPU 3 has a total capacity of 15.77 GiB", 3)]
    ev += ["W0305 10:16:10.000000 140234 torch/distributed/elastic/multiprocessing/api.py:851] Sending process 1002 closing signal SIGTERM"]
    return _torch_run(419, 4, ev)


def _mpi_run() -> str:
    tag = lambda r: f"[1,{r}]<stdout>:"  # noqa: E731
    ev = [("rank 5: CUDA error: an illegal memory access was encountered", 5)]
    ev += [("Initializing MPI world of 8 ranks", None), ("Warning: UCX transport not available, falling back to TCP", None), (lambda r: f"rank {r} local batch 16 shard {r}", None)]
    ev += [("step 100 allreduce took 0.31s", None), ("step 200 allreduce took 0.29s", None)]
    ev += ["mpirun: 1 total process failed to start"]
    return _torch_run(423, 8, ev, tag)


case(
    417,
    claim="Within a run of rank-prefixed lines (torchrun '[rankN]:', Open MPI '[job,rank]<stdout>:'), identical bodies from different ranks join one '[ranks 0-3] body' line; each rank's own line order, every rank-specific line (still carrying its own prefix) and every unprefixed line are unchanged.",
    loses="the interleaving order of different ranks' lines inside a run, and which of several equal lines a rank printed first relative to other ranks",
    encode=fold_ranks,
    probes=probes_417,
    fixtures=lambda: [
        Fixture("torchrun 4 ranks, rank 2 goes NaN in the middle", _ddp_nan(), True),
        Fixture("torchrun 4 ranks, rank 3 runs out of memory at the end", _ddp_oom(), True),
        Fixture("mpirun 8 ranks, rank 5 fails first", _mpi_run(), True),
        Fixture("rank 0 only (nothing to fold)", "".join(f"[rank0]:step {i} loss={2 - i / 10:.2f}\n" for i in range(1, 8)), False),
        Fixture("no prefixes", "epoch 1 done\nepoch 2 done\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #411 Decimate per-step training curves: keep first and last step lines, the 3 lowest-loss, 3 highest-loss and 3
# highest-grad_norm lines of the whole run, NaN/inf, spikes past 3 sigma of the previous 16
# steps, and every line that is not a step line (eval, checkpoint, warnings); fold the rest into
# '[steps a-b: N lines folded; ...]'. Keeping every *running* record instead (each new minimum as it happens) was
# measured on the same 400-step fixture: a noisy decaying curve sets a new minimum every other step, so only 46-52% of
# the characters go; keeping the run's extremes is what reaches the ratio below.

_NUM = r"(?:[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|nan|inf|-inf)"
_STEP = re.compile(rf"^step (0|[1-9]\d*) loss ({_NUM}) lr ({_NUM}) grad_norm ({_NUM})$", A)
_M411 = re.compile(r"^\[steps (\d+)-(\d+): (\d+) lines folded; ", A)


def decimate_steps(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M411.match(l) for l in lines):
        return text
    parsed = [_STEP.match(l) for l in lines]
    idx = [i for i, m in enumerate(parsed) if m]
    if len(idx) < 8:
        return text
    keep = [m is None for m in parsed]
    keep[idx[0]] = keep[idx[-1]] = True
    finite = [i for i in idx if all(math.isfinite(float(parsed[i].group(g))) for g in (2, 3, 4))]
    for i in idx:
        if i not in finite:
            keep[i] = True
    for g, sign in ((2, 1), (2, -1), (4, -1)):  # 3 lowest losses, 3 highest losses, 3 highest grad_norms (earlier line wins a tie)
        vals = [sign * float(parsed[i].group(g)) for i in finite]
        for k in sorted(range(len(finite)), key=lambda k: (vals[k], k))[:3]:
            keep[finite[k]] = True
    window: list[float] = []
    for i in finite:
        loss = float(parsed[i].group(2))
        if len(window) >= 8:
            mu, sd = statistics.fmean(window), statistics.pstdev(window)
            if (sd > 0 and (loss - mu) > 3 * sd) or (sd == 0 and loss != mu):
                keep[i] = True
        window = (window + [loss])[-16:]
    out: list[str] = []
    i = 0
    while i < len(lines):
        if keep[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and not keep[j]:
            j += 1
        ms = parsed[i:j]
        losses = [float(m.group(2)) for m in ms]
        marker = (f"[steps {ms[0].group(1)}-{ms[-1].group(1)}: {j - i} lines folded; loss {ms[0].group(2)} -> {ms[-1].group(2)}"
                  f" (range {min(losses):g}..{max(losses):g}); lr {ms[0].group(3)} -> {ms[-1].group(3)}; grad_norm max {max(float(m.group(4)) for m in ms):g}]")
        out.append(marker if len(marker) < sum(len(l) + 1 for l in lines[i:j]) else "\n".join(lines[i:j]))
        i = j
    return gate(text, join_nl(out, nl))


def _step_fields(line: str):
    """Probe-side parser: (step, loss, lr, grad_norm) token strings, or None. Token checks, not the encoder's regex."""
    t = line.split(" ")
    if len(t) != 8 or t[0] != "step" or t[2] != "loss" or t[4] != "lr" or t[6] != "grad_norm":
        return None
    if not (isdigits(t[1]) and (t[1] == "0" or t[1][0] != "0")):
        return None
    if not all(re.fullmatch(_NUM, v, A) for v in (t[3], t[5], t[7])):
        return None
    return t[1], t[3], t[5], t[7]


def probes_411(text: str) -> dict:
    lines, _ = split_nl(text)
    steps: list[tuple[int, tuple]] = []  # (virtual index, fields)
    other: list[tuple[int, str]] = []
    nonfinite: list[tuple[int, str]] = []
    v = 0
    for l in lines:
        m = _M411.match(l)
        if m:
            v += int(m.group(3))
            continue
        f = _step_fields(l)
        if f:
            steps.append((v, f))
            if not all(math.isfinite(float(x)) for x in f[1:]):
                nonfinite.append((v, l))
        else:
            other.append((v, l))
        v += 1
    fin = [(i, f) for i, f in steps if all(math.isfinite(float(x)) for x in f[1:])]

    def top(col: int, sign: int):
        order = sorted(range(len(fin)), key=lambda k: (sign * float(fin[k][1][col]), fin[k][0]))
        return [(fin[k][1][0], fin[k][1][col]) for k in order[:3]]

    return {
        "first step line": steps[0] if steps else None,
        "last step line": steps[-1] if steps else None,
        "number of step lines": v - len(other),
        "3 lowest losses (step, value)": top(1, 1),
        "3 highest losses (step, value)": top(1, -1),
        "3 highest grad_norms (step, value)": top(3, -1),
        "non-finite step lines (virtual line, text)": nonfinite,
        "all other lines: eval, checkpoint, warnings (virtual line, text)": other,
    }


def _train_log(nan_at: int | None = None, steps: int = 400, seed: int = 411, overfit: bool = False) -> str:
    rng = random.Random(seed)
    out = ["Training started: model=gpt-mini lr=3e-4 batch=32 steps=%d" % steps]
    for s in range(1, steps + 1):
        loss = 1.1 + 1.3 * math.exp(-s / 120) + rng.gauss(0, 0.025)
        gn = 0.6 + 0.5 * math.exp(-s / 200) + abs(rng.gauss(0, 0.05))
        lr = 3e-4 * (0.5 * (1 + math.cos(math.pi * s / steps)))
        if overfit and s > 300:
            loss += (s - 300) * 0.004  # the best loss is mid-run, not at the end
        if s == 237:
            loss += 0.35
            gn *= 4
        ls, gs = f"{loss:.4f}", f"{gn:.3f}"
        if nan_at is not None and s >= nan_at:
            ls, gs = ("nan", "inf") if s == nan_at else ("nan", "nan")
        out.append(f"step {s} loss {ls} lr {lr:.3e} grad_norm {gs}")
        if s % 100 == 0:
            out.append(f"eval step {s}: eval_loss {loss - 0.04:.4f} eval_acc {0.5 + s / 1000:.3f}")
        if s % 200 == 0:
            out.append(f"Saving checkpoint to out/ckpt-{s}.pt")
        if s == 150:
            out.append("UserWarning: gradient checkpointing is incompatible with use_cache=True; setting use_cache=False")
        if nan_at is not None and s == nan_at + 3:
            out.append("RuntimeError: loss is nan, aborting")
            break
    out.append("Training finished in 912s" if nan_at is None else "Training aborted")
    return "\n".join(out) + "\n"


case(
    411,
    claim="Per-step 'step N loss L lr R grad_norm G' lines fold into '[steps a-b: N lines folded; ...]' markers; first and last step lines, the 3 lowest-loss, 3 highest-loss and 3 highest-grad_norm lines of the run, every NaN/inf line, every spike past 3 sigma and every non-step line (eval, checkpoint, warnings) stay verbatim, and the virtual step count is exact.",
    loses="the intermediate loss, lr and grad_norm values of folded steps (marker keeps loss/lr endpoints, the loss range and the grad_norm maximum), and which steps were folded",
    encode=decimate_steps,
    probes=probes_411,
    fixtures=lambda: [
        Fixture("400-step run: best loss mid-run (step ~300), eval and checkpoint lines, a spike at 237", _train_log(overfit=True), True),
        Fixture("diverged run: loss goes NaN at step 351, then aborts", _train_log(nan_at=351), True),
        Fixture("short run (under 8 step lines)", "Training started\nstep 1 loss 2.4 lr 3e-4 grad_norm 0.9\nstep 2 loss 2.3 lr 3e-4 grad_norm 0.8\n", False),
        Fixture("not a training log", "epoch finished\nsaved\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #413 Spark driver INFO logs: per-task chatter folds into per-stage counts and the slowest task.
#
# Chatter = INFO lines of TaskSetManager ('Starting task' / 'Finished task'), Executor ('Running task' / 'Finished
# task'), MemoryStore, BlockManagerInfo, BlockManagerMasterEndpoint and ResourceUtils that carry no alarm word. A run
# of chatter becomes '[... N lines folded (first .. last): stage S started a finished b slowest TID t ms; other c]'.
# Everything else (WARN, ERROR, stack frames, DAGScheduler stage/job lines, SparkContext, ...) is verbatim.

_SPARK = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d) INFO (TaskSetManager|Executor|MemoryStore|BlockManagerInfo|BlockManagerMasterEndpoint|ResourceUtils): (.*)$", A)
_TSM_START = re.compile(r"^Starting task \S+ in stage (\S+) \(TID (\d+)\)", A)
_TSM_DONE = re.compile(r"^Finished task \S+ in stage (\S+) \(TID (\d+)\) in (\d+) ms on ", A)
_EXE = re.compile(r"^(?:Running|Finished) task ", A)
_M413 = re.compile(r"^\[\.\.\. (\d+) lines folded \(([^()]*?) \.\. ([^()]*?)\): (.*)\]$", A)
_M413_STAGE = re.compile(r"^stage (\S+) started (\d+) finished (\d+) slowest TID (\d+) (\d+)ms$", A)
_M413_OTHER = re.compile(r"^other (\d+)$", A)


def _spark_chatter(line: str):
    """(timestamp, kind, stage, tid, ms) for a foldable chatter line, else None."""
    if _ALARM.search(line):
        return None
    m = _SPARK.match(line)
    if not m:
        return None
    ts, logger, msg = m.groups()
    if logger == "TaskSetManager":
        s = _TSM_START.match(msg)
        if s:
            return ts, "start", s.group(1), int(s.group(2)), 0
        d = _TSM_DONE.match(msg)
        return (ts, "done", d.group(1), int(d.group(2)), int(d.group(3))) if d else None
    if logger == "Executor":
        return (ts, "other", "", 0, 0) if _EXE.match(msg) else None
    return ts, "other", "", 0, 0


def _spark_marker(infos) -> str:
    stages: dict = {}
    other = 0
    for ts, kind, stage, tid, ms in infos:
        if kind == "other":
            other += 1
            continue
        st = stages.setdefault(stage, [0, 0, None])
        if kind == "start":
            st[0] += 1
        else:
            st[1] += 1
            if st[2] is None or (ms, -tid) > (st[2][1], -st[2][0]):
                st[2] = (tid, ms)
    parts = [f"stage {k} started {v[0]} finished {v[1]} slowest TID {v[2][0] if v[2] else 0} {v[2][1] if v[2] else 0}ms" for k, v in stages.items()]
    if other:
        parts.append(f"other {other}")
    return f"[... {len(infos)} lines folded ({infos[0][0]} .. {infos[-1][0]}): {'; '.join(parts)}]"


def fold_spark(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M413.match(l) for l in lines):
        return text
    info = [_spark_chatter(l) for l in lines]
    out: list[str] = []
    i = 0
    while i < len(lines):
        if info[i] is None:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and info[j] is not None:
            j += 1
        marker = _spark_marker(info[i:j])
        out.append(marker if len(marker) < sum(len(l) + 1 for l in lines[i:j]) else "\n".join(lines[i:j]))
        i = j
    return gate(text, join_nl(out, nl))


def _spark_probe_line(line: str):
    """Probe-side parse on single-space tokens: ('start'|'done', stage, tid, ms) or None."""
    t = line.split(" ")
    if len(t) < 12 or t[2] != "INFO" or any(k in line for k in _ALARM_KEYS):
        return None
    if t[3] == "TaskSetManager:" and t[5] == "task" and t[7] == "in" and t[8] == "stage" and t[10] == "(TID" and t[11].endswith(")"):
        tid = t[11][:-1]
        if t[4] == "Starting" and isdigits(tid):
            return "start", t[9], int(tid), 0
        if t[4] == "Finished" and isdigits(tid) and len(t) >= 16 and t[12] == "in" and isdigits(t[13]) and t[14] == "ms" and t[15] == "on":
            return "done", t[9], int(tid), int(t[13])
    return None


def probes_413(text: str) -> dict:
    lines, _ = split_nl(text)
    stages: dict = defaultdict(lambda: [0, 0, None])
    vlines = 0
    alarms: list[tuple[int, str]] = []
    first_err = None

    def add(kind, stage, tid, ms, n=1):
        st = stages[stage]
        st[0 if kind == "start" else 1] += n
        if kind == "done" and (st[2] is None or (ms, -tid) > (st[2][1], -st[2][0])):
            st[2] = (tid, ms)

    for line in lines:
        m = _M413.match(line)
        if m:
            vlines += int(m.group(1))
            for part in m.group(4).split("; "):
                sm = _M413_STAGE.match(part)
                if sm:
                    st = stages[sm.group(1)]
                    st[0] += int(sm.group(2))
                    st[1] += int(sm.group(3))
                    tid, ms = int(sm.group(4)), int(sm.group(5))
                    if int(sm.group(3)) and (st[2] is None or (ms, -tid) > (st[2][1], -st[2][0])):
                        st[2] = (tid, ms)
            continue
        f = _spark_probe_line(line)
        if f:
            add(*f)
        if any(k in line for k in _ALARM_KEYS):
            alarms.append((vlines, line))
            if first_err is None and any(k in line for k in ("ERROR", "Exception", "FATAL")):
                first_err = vlines
        vlines += 1
    return {
        "alarm lines (virtual line number, text)": alarms,
        "virtual line count": vlines,
        "per stage: tasks started, tasks finished, slowest (tid, ms)": sorted((k, v[0], v[1], v[2]) for k, v in stages.items()),
        "lines before the first error": first_err,
    }


def _spark_log(fail_task: int | None = None, per_stage: int = 64, seed: int = 413) -> str:
    rng = random.Random(seed)
    out = []
    t = [0]

    def ts():
        t[0] += rng.randint(0, 1)
        sec = 1 + t[0] // 2
        return f"25/03/05 10:{15 + sec // 60:02d}:{sec % 60:02d}"

    out += [f"{ts()} WARN NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable",
            f"{ts()} INFO SparkContext: Running Spark version 3.5.0",
            f"{ts()} INFO ResourceUtils: ==============================================================",
            f"{ts()} INFO ResourceUtils: No custom resources configured for spark.driver.",
            f"{ts()} INFO ResourceUtils: ==============================================================",
            f"{ts()} INFO MemoryStore: MemoryStore started with capacity 434.4 MiB",
            f"{ts()} INFO BlockManagerMasterEndpoint: Registering block manager 10.0.0.5:44211 with 434.4 MiB RAM, BlockManagerId(driver, 10.0.0.5, 44211, None)"]
    tid = 0
    for stage in range(2):
        out += [f"{ts()} INFO DAGScheduler: Got job {stage} (count at job.py:{12 + stage}) with {per_stage} output partitions",
                f"{ts()} INFO DAGScheduler: Final stage: ResultStage {stage} (count at job.py:{12 + stage})",
                f"{ts()} INFO TaskSchedulerImpl: Adding task set {stage}.0 with {per_stage} tasks resource profile 0"]
        if stage == 1:
            out.append(f"{ts()} WARN TaskSetManager: Stage 1 contains a task of very large size (1210 KiB). The maximum recommended task size is 1000 KiB.")
        for k in range(per_stage):
            out.append(f"{ts()} INFO TaskSetManager: Starting task {k}.0 in stage {stage}.0 (TID {tid}) (10.0.0.5, executor driver, partition {k}, PROCESS_LOCAL, 9013 bytes)")
            out.append(f"{ts()} INFO Executor: Running task {k}.0 in stage {stage}.0 (TID {tid})")
            if fail_task is not None and tid == fail_task:
                out.append(f"{ts()} ERROR Executor: Exception in task {k}.0 in stage {stage}.0 (TID {tid})")
                out += ["java.lang.ArithmeticException: / by zero", "\tat org.apache.spark.sql.catalyst.expressions.Divide.eval(arithmetic.scala:441)", "\tat org.apache.spark.executor.Executor$TaskRunner.run(Executor.scala:552)"]
                out.append(f"{ts()} WARN TaskSetManager: Lost task {k}.0 in stage {stage}.0 (TID {tid}) (10.0.0.5 executor driver): java.lang.ArithmeticException: / by zero")
                out.append(f"{ts()} ERROR TaskSetManager: Task {k} in stage {stage}.0 failed 1 times; aborting job")
                out.append(f"{ts()} INFO DAGScheduler: ResultStage {stage} (count at job.py:{12 + stage}) failed in 4.1 s due to Job aborted")
                return "\n".join(out) + "\n"
            out.append(f"{ts()} INFO Executor: Finished task {k}.0 in stage {stage}.0 (TID {tid}). 1594 bytes result sent to driver")
            ms = rng.randint(30, 400) + (1822 if tid == 77 else 0)
            out.append(f"{ts()} INFO TaskSetManager: Finished task {k}.0 in stage {stage}.0 (TID {tid}) in {ms} ms on 10.0.0.5 (executor driver) ({k + 1}/{per_stage})")
            if k % 16 == 15:
                out.append(f"{ts()} INFO MemoryStore: Block broadcast_{tid // 16 + 1} stored as values in memory (estimated size 5.2 KiB, free 434.4 MiB)")
                out.append(f"{ts()} INFO BlockManagerInfo: Added broadcast_{tid // 16 + 1}_piece0 in memory on 10.0.0.5:44211 (size: 3.1 KiB, free: 434.4 MiB)")
            tid += 1
        out += [f"{ts()} INFO TaskSchedulerImpl: Removed TaskSet {stage}.0, whose tasks have all completed, from pool",
                f"{ts()} INFO DAGScheduler: ResultStage {stage} (count at job.py:{12 + stage}) finished in 5.1 s",
                f"{ts()} INFO DAGScheduler: Job {stage} finished: count at job.py:{12 + stage}, took 5.2 s"]
    out.append(f"{ts()} WARN SparkContext: Please ensure that the number of slots available on your executors is limited by the number of cores to task cpus")
    return "\n".join(out) + "\n"


case(
    413,
    claim="Runs of per-task driver chatter (TaskSetManager Starting/Finished task, Executor Running/Finished task, MemoryStore, BlockManagerInfo, ResourceUtils, BlockManagerMasterEndpoint INFO lines) fold into '[... N lines folded: stage S started a finished b slowest TID t ms ...]'; every WARN, ERROR, stack frame, DAGScheduler/SparkContext line stays verbatim at its exact position, and per-stage started/finished counts and the slowest task are unchanged.",
    loses="the id, host and timing of every folded task except the slowest per stage and run, and the individual MemoryStore/BlockManager block lines",
    encode=fold_spark,
    probes=probes_413,
    fixtures=lambda: [
        Fixture("local[4] job, two stages of 64 tasks, WARN at the start, middle and end", _spark_log(), True),
        Fixture("failure variant: task 20 dies with ArithmeticException and the job aborts", _spark_log(fail_task=20), True),
        Fixture("driver log without per-task lines", "25/03/05 10:15:01 INFO SparkContext: Running Spark version 3.5.0\n25/03/05 10:15:09 INFO SparkContext: Successfully stopped SparkContext\n", False),
    ],
    min_gain=0.6,
)


# ===============================================================================================================
# #1155 Spring Boot logs: drop column padding, '---' and the repeated PID; fold repeated ASCII banners.
#
# 'TS  INFO 12345 --- [           main] c.e.demo.Foo                             : msg' becomes
# 'TS INFO [main] c.e.demo.Foo: msg' with one '[pid 12345]' header line when every record shares the pid (otherwise
# each record keeps 'pid=N'). The second and later banner blocks identical to the first become one marker line.
# Stack traces, blank lines and anything that is not a Boot record stay verbatim. Records are normalised, not kept
# byte-for-byte: the padding is the only thing WARN/ERROR lines lose.

_BOOT = re.compile(r"^([^ ]+) +(ERROR|WARN|INFO|DEBUG|TRACE|FATAL) (\d+) --- \[ *([^\]]*)\] ([^ ]+) +: (.*)$", A)
_BOOT_NORM = re.compile(r"^([^ ]+) (ERROR|WARN|INFO|DEBUG|TRACE|FATAL) (?:pid=\d+ )?\[([^\]]*)\] ([^ ]+): (.*)$", A)
_BANNER6 = ["  .   ____          _            __ _ _", r" /\\ / ___'_ __ _ _(_)_ __  __ _ \ \ \ \\", r"( ( )\___ | '_ | '_| | '_ \/ _` | \ \ \ \\",
            r" \\/  ___)| |_)| | | | | || (_| |  ) ) ) )", r"  '  |____| .__|_| |_|_| |_\__, | / / / /", " =========|_|==============|___/=/_/_/_/"]
_BANNER_LAST = re.compile(r"^ :: Spring Boot :: +\(v[^()]*\)$", A)
_BANNER_MARK = "[Spring Boot banner repeated]"
_HDR_PID = re.compile(r"^\[pid (\d+)\]$", A)


def _banner_at(lines: list[str], i: int):
    if lines[i:i + 6] == _BANNER6 and i + 6 < len(lines) and _BANNER_LAST.match(lines[i + 6]):
        return lines[i:i + 7]
    return None


def normalise_boot(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_BOOT_NORM.match(l) or _HDR_PID.match(l) or l == _BANNER_MARK for l in lines):
        return text
    recs = [_BOOT.match(l) for l in lines]
    pids = {m.group(3) for m in recs if m}
    hoist = len(pids) == 1
    out: list[str] = []
    first_banner = None
    i = 0
    while i < len(lines):
        b = _banner_at(lines, i)
        if b:
            if first_banner is None:
                first_banner = b
            elif b == first_banner:
                out.append(_BANNER_MARK)
                i += 7
                continue
            out += b
            i += 7
            continue
        m = recs[i]
        if m:
            ts, lvl, pid, thread, logger, msg = m.groups()
            out.append(f"{ts} {lvl} {'' if hoist else f'pid={pid} '}[{thread}] {logger}: {msg}")
        else:
            out.append(lines[i])
        i += 1
    if hoist:
        out.insert(0, f"[pid {next(iter(pids))}]")
    return gate(text, join_nl(out, nl))


def _boot_probe_norm(rest: str, ts: str, pid):
    lvl, sp2, tail = rest.partition(" ")  # normalised: exactly one space, then the level
    if lvl in _LEVELS and sp2:
        p = pid
        if tail.startswith("pid="):
            tok, sp3, tail = tail.partition(" ")
            if not (sp3 and isdigits(tok[4:])):
                return None
            p = tok[4:]
        if tail.startswith("[") and "]" in tail:
            j = tail.index("]")
            after = tail[j + 1:]
            if after.startswith(" "):
                tok, sp4, msg = after[1:].partition(" ")
                if sp4 and tok.endswith(":") and tok != ":" and " " not in tok:
                    return ts, lvl, p, tail[1:j], tok[:-1], msg
    return None


def _boot_probe_raw(rest: str, ts: str):
    lvl, sp2, tail = rest.lstrip(" ").partition(" ")  # raw: padded, then 'pid --- [thread] logger  : msg'
    if lvl in _LEVELS and sp2:
        p, sp3, tail = tail.partition(" ")
        if isdigits(p) and sp3 and tail.startswith("--- ["):
            j = tail.find("]")
            after = tail[j + 1:]
            if after.startswith(" "):
                logger, sp4, rest2 = after[1:].partition(" ")
                stripped = (sp4 + rest2).lstrip(" ")
                if logger and sp4 and stripped.startswith(": "):
                    return ts, lvl, p, tail[5:j].lstrip(" "), logger, stripped[2:]
    return None


def _boot_probe_line(line: str, pid):
    """Probe-side record parser (string surgery). Returns (ts, level, pid, thread, logger, msg) or None."""
    ts, sp, rest = line.partition(" ")
    if not ts or not sp:
        return None
    return _boot_probe_norm(rest, ts, pid) or _boot_probe_raw(rest, ts)


def probes_1155(text: str) -> dict:
    lines, _ = split_nl(text)
    recs, other, banners = [], [], []
    pid = None
    first_banner_last = None
    i = 0
    while i < len(lines):
        l = lines[i]
        h = _HDR_PID.match(l)
        if h:
            pid = h.group(1)
            i += 1
            continue
        if l == _BANNER_MARK:
            banners.append(first_banner_last)
            i += 1
            continue
        if l == _BANNER6[0] and lines[i:i + 6] == _BANNER6 and i + 6 < len(lines) and lines[i + 6].startswith(" :: Spring Boot ::") and lines[i + 6].endswith(")") and lines[i + 6].rstrip(" ") == lines[i + 6] and "(v" in lines[i + 6] and lines[i + 6].count("(") == 1 and lines[i + 6].count(")") == 1:
            if first_banner_last is None:
                first_banner_last = lines[i + 6]
            banners.append(lines[i + 6])
            i += 7
            continue
        r = _boot_probe_line(l, pid)
        if r:
            recs.append(r)
        else:
            other.append((len(recs), l))
        i += 1
    return {
        "records (timestamp, level, pid, thread, logger, message)": recs,
        "WARN and ERROR records": [r for r in recs if r[1] in ("WARN", "ERROR", "FATAL")],
        "non-record lines such as stack frames (records before, text)": other,
        "banners (version line of each)": banners,
    }


def _boot_log(contexts: int, with_error: bool = False, two_pids: bool = False) -> str:
    out = []
    banner = "\n".join(_BANNER6) + "\n :: Spring Boot ::                (v3.2.3)"
    ms = 0

    def rec(level, thread, logger, msg, pid="12345"):
        nonlocal ms
        ms += 7 + (len(msg) % 53)
        sec, mil = divmod(ms, 1000)
        return f"2024-03-05T10:15:{sec:02d}.{mil:03d}+01:00 {level:>5} {pid} --- [{thread:>15}] {logger:<40} : {msg}"

    for c in range(contexts):
        out += ["", banner, ""] if c else [banner, ""]
        out += [rec("INFO", "main", "c.e.demo.DemoApplicationTests", f"Starting DemoApplicationTests using Java 17.0.9 with PID 12345 (started by runner in /work/demo)"),
                rec("INFO", "main", "c.e.demo.DemoApplicationTests", 'No active profile set, falling back to 1 default profile: "default"'),
                rec("INFO", "main", ".s.d.r.c.RepositoryConfigurationDelegate", "Bootstrapping Spring Data JPA repositories in DEFAULT mode."),
                rec("INFO", "main", ".s.d.r.c.RepositoryConfigurationDelegate", f"Finished Spring Data repository scanning in {80 + c} ms. Found 3 JPA repository interfaces."),
                rec("INFO", "main", "o.hibernate.jpa.internal.util.LogHelper", "HHH000204: Processing PersistenceUnitInfo [name: default]"),
                rec("INFO", "main", "com.zaxxer.hikari.HikariDataSource", "HikariPool-1 - Start completed."),
                rec("INFO", "main", "o.s.b.t.m.w.SpringBootMockServletContext", "Initializing Spring TestDispatcherServlet ''")]
        if c == 1:
            out.append(rec("WARN", "main", "JpaBaseConfiguration$JpaWebConfiguration", "spring.jpa.open-in-view is enabled by default. Therefore, database queries may be performed during view rendering."))
        for k in range(4):
            out.append(rec("INFO", "Test worker" if two_pids and k == 2 else "main", "c.e.demo.web.OrderControllerTest", f"order {100 + k} created", "12399" if two_pids and k == 2 else "12345"))
        if with_error and c == contexts - 1:
            out.append(rec("ERROR", "main", "o.s.boot.SpringApplication", "Application run failed"))
            out += ["org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'dataSource'", "\tat org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.doCreateBean(AbstractAutowireCapableBeanFactory.java:1786)",
                    "Caused by: java.net.ConnectException: Connection refused"]
        out.append(rec("INFO", "SpringApplicationShutdownHook", "j.LocalContainerEntityManagerFactoryBean", "Closing JPA EntityManagerFactory for persistence unit 'default'"))
    return "\n".join(out) + "\n"


case(
    1155,
    claim="Spring Boot records 'TS  LEVEL PID --- [   thread] logger   : msg' lose the column padding and '---' and the shared PID moves to one '[pid N]' header (kept per record as pid=N when several JVMs log); the second and later identical banner blocks fold to one marker; timestamp, level, thread, logger and message of every record, and every non-record line (stack frames), are unchanged.",
    loses="the fixed column widths (thread slot of 15, logger column of 40), the literal '---', and the repeated banner block after the first",
    encode=normalise_boot,
    probes=probes_1155,
    fixtures=lambda: [
        Fixture("test suite booting 3 contexts: banner each time, WARN in the middle context", _boot_log(3), True),
        Fixture("failure variant: ERROR 'Application run failed' with stack trace in the last context", _boot_log(2, with_error=True), True),
        Fixture("two JVMs interleaved (pids differ): pid stays per record", _boot_log(2, two_pids=True), True),
        Fixture("already normalised output", "[pid 1]\n2024-03-05T10:15:00.000+01:00 INFO [main] c.e.Foo: hi\n", False),
        Fixture("plain application log, not Boot", "starting up\nlistening on 8080\n", False),
    ],
    min_gain=0.1,
)


# ===============================================================================================================
# #885 dbt console pack: drop START lines that have a result line, strip clock and dot leaders from result lines,
# drop --debug blocks (a '[debug]' line plus the SQL lines that follow it) unless a line in the block carries an alarm
# word. Failures, warnings, skips, summaries and failure details are verbatim.

_DBT_PFX = r"(?:\d\d:\d\d:\d\d  |\d\d:\d\d:\d\d\.\d+ \[info \] \[[^\]]*\]: )"
_DBT_RES = re.compile(rf"^(?:{_DBT_PFX})?(\d+) of (\d+) (OK|PASS|WARN|FAIL|ERROR|SKIP) (.+?) \.{{2,}} \[([^\[\]]+)\]$", A)
_DBT_START = re.compile(rf"^(?:{_DBT_PFX})?(\d+) of (\d+) START (.+?) \.{{2,}} \[RUN\]$", A)
_DBT_DEBUG = re.compile(r"^\d\d:\d\d:\d\d\.\d+ \[debug\] ", A)
_DBT_CLOCK = re.compile(r"^\d\d:\d\d:\d\d", A)
_M885 = re.compile(r"^\[\.\.\. (\d+) debug lines dropped\]$", A)
_DBT_ALARM = ("rror", "ERROR", "FAIL", "Fail", "fail", "xception", "Traceback", "WARN", "arning")


def compact_dbt(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M885.match(l) for l in lines):
        return text
    results = {(m.group(1), m.group(2)) for m in map(_DBT_RES.match, lines) if m}
    # debug blocks: [start, end) ranges
    blocks, i = [], 0
    while i < len(lines):
        if _DBT_DEBUG.match(lines[i]):
            j = i + 1
            while j < len(lines) and not _DBT_CLOCK.match(lines[j]):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1
    drop = [False] * len(lines)
    for s, e in blocks:
        if not any(k in l for l in lines[s:e] for k in _DBT_ALARM):
            for k in range(s, e):
                drop[k] = True
    out: list[str] = []
    i = 0
    while i < len(lines):
        if drop[i]:
            j = i
            while j < len(lines) and drop[j]:
                j += 1
            out.append(f"[... {j - i} debug lines dropped]")
            i = j
            continue
        l = lines[i]
        st = _DBT_START.match(l)
        if st and (st.group(1), st.group(2)) in results:
            i += 1
            continue
        r = _DBT_RES.match(l)
        out.append(f"{r.group(1)} of {r.group(2)} {r.group(3)} {r.group(4)} [{r.group(5)}]" if r else l)
        i += 1
    return gate(text, join_nl(out, nl))


def _dbt_node(line: str):
    """Probe-side node-line parser: ('start'|'result', n, m, status, desc, detail) or None. Works on raw and compact lines."""
    body = line
    if line[:2].isdigit() and line[2:3] == ":":  # clock prefix: 'HH:MM:SS  ' or 'HH:MM:SS.ffffff [info ] [thread]: '
        if line[8:10] == "  ":
            body = line[10:]
        else:
            k = line.find("]: ")
            if line[8:9] == "." and " [info ] [" in line[:k + 1] and k > 0:
                body = line[k + 3:]
    t = body.split(" ")
    if len(t) < 5 or t[1] != "of" or not (isdigits(t[0]) and isdigits(t[2])) or not body.endswith("]"):
        return None
    st = t[3]
    cut = body.rfind(" [")
    if cut < 0:
        return None
    head, detail = body[:cut], body[cut + 2:-1]
    parts = head.split(" ", 4)
    rest = parts[4] if len(parts) > 4 else ""
    stripped = rest.rstrip(".")
    if len(rest) - len(stripped) >= 2 and stripped.endswith(" "):
        rest = stripped[:-1]
    if not rest or not detail:
        return None
    if st == "START":
        return ("start", t[0], t[2], st, rest, detail) if detail == "RUN" else None
    return ("result", t[0], t[2], st, rest, detail) if st in ("OK", "PASS", "WARN", "FAIL", "ERROR", "SKIP") else None


def probes_885(text: str) -> dict:
    lines, _ = split_nl(text)
    nodes, starts, other, dbg_alarm = [], [], [], []
    in_dbg, dbg_total, n_res = False, 0, 0
    for l in lines:
        m = _M885.match(l)
        if m:
            dbg_total += int(m.group(1))
            in_dbg = False
            continue
        clock = len(l) >= 8 and l[2:3] == ":" and l[5:6] == ":" and isdigits(l[:2] + l[3:5] + l[6:8])
        if clock:
            in_dbg = False
            frac = l[9:] if l[8:9] == "." else ""
            k = len(frac) - len(frac.lstrip("0123456789"))
            in_dbg = k > 0 and frac[k:].startswith(" [debug] ")
        if in_dbg:
            dbg_total += 1
            if any(k in l for k in _DBT_ALARM):
                dbg_alarm.append(l)
            continue
        nd = _dbt_node(l)
        if nd and nd[0] == "result":
            nodes.append(nd[1:])
            n_res += 1
        elif nd:
            starts.append(nd[1:])
        else:
            other.append((n_res, l))
    done = {(n[0], n[1]) for n in nodes}
    return {
        "node results (n, of, status, description, detail)": nodes,
        "status counts": sorted(Counter(n[2] for n in nodes).items()),
        "nodes that started but have no result": [s for s in starts if (s[0], s[1]) not in done],
        "other lines kept: header, failure details, summary (results before, text)": other,
        "debug lines in all (dropped or kept)": dbg_total,
        "debug lines naming an error or failure": dbg_alarm,
    }


def _dbt_run(ok: bool, debug: bool = False) -> str:
    nodes = []
    for i, n in enumerate(["raw_customers", "raw_orders", "raw_payments"], 1):
        nodes.append((i, "seed file", f"main.{n}", "loaded seed file", f"main.{n}", "INSERT 100", 0.07 + i / 100))
    models = [("sql view model", "created sql view model", "stg_customers"), ("sql view model", "created sql view model", "stg_orders"), ("sql view model", "created sql view model", "stg_payments"),
              ("sql table model", "created sql table model", "customers"), ("sql table model", "created sql table model", "orders")]
    for k, (kind, done, name) in enumerate(models, 4):
        nodes.append((k, kind, f"main.{name}", done, f"main.{name}", "OK", 0.05 + k / 100))
    tests = ["unique_customers_customer_id", "not_null_customers_customer_id", "unique_orders_order_id", "not_null_orders_order_id", "not_null_orders_customer_id",
             "accepted_values_orders_status__placed__shipped", "not_null_stg_payments_amount", "relationships_orders_customer_id", "unique_stg_orders_order_id", "not_null_stg_orders_status"]
    out = ["10:15:01  Running with dbt=1.7.4", "10:15:02  Registered adapter: duckdb=1.7.2", "10:15:02  Found 5 models, 3 seeds, 10 tests, 0 sources, 401 macros", "10:15:02  ",
           "10:15:02  Concurrency: 1 threads (target='dev')", "10:15:02  "]
    n_all = len(nodes) + len(tests)
    clock = [2]
    dots = lambda d: "." * max(3, 62 - len(d))  # noqa: E731

    def line(txt, dbg=False):
        clock[0] += 1
        sec = clock[0] // 3
        if debug:
            return f"10:15:{sec:02d}.{clock[0] * 1111:06d} [{'debug' if dbg else 'info '}] [Thread-1 (]: {txt}"
        return f"10:15:{sec:02d}  {txt}"

    def pair(k, start_desc, status, res_desc, detail):
        out.append(line(f"{k} of {n_all} START {start_desc} {dots(start_desc)} [RUN]"))
        if debug:
            out.append(line(f"Using duckdb connection \"model.jaffle.{res_desc.split()[-1]}\".", True))
            out.append(line(f"On model.jaffle.{res_desc.split()[-1]}: BEGIN", True))
            out.extend(["", "  create view \"main\".\"x__dbt_tmp\" as (", "    select id, first_name from \"main\".\"raw_customers\"", "  );", ""])
            out.append(line("SQL status: OK in 0.003 seconds", True))
            out.append(line(f"On model.jaffle.{res_desc.split()[-1]}: COMMIT", True))
        out.append(line(f"{k} of {n_all} {status} {res_desc} {dots(res_desc)} [{detail}]"))

    for i, (k, kind, full, done, res, detail, t) in enumerate(nodes, 1):
        status = "OK"
        if not ok and k == 1:
            pair(k, f"{kind} {full}", "ERROR", f"loading {kind} {res}", "ERROR in 0.10s")
            continue
        pair(k, f"{kind} {full}", status, f"{done} {res}", f"{detail} in {t:.2f}s" if detail != "OK" else f"OK in {t:.2f}s")
    for j, t in enumerate(tests):
        k = len(nodes) + j + 1
        if not ok and t == "unique_orders_order_id":
            pair(k, f"test {t}", "FAIL", f"3 {t}", "FAIL 3 in 0.04s")
        elif not ok and j == len(tests) - 1:
            pair(k, f"test {t}", "WARN", f"1 {t}", "WARN 1 in 0.05s")
        else:
            pair(k, f"test {t}", "PASS", t, f"PASS in {0.03 + j / 100:.2f}s")
    p = f"10:15:{clock[0] // 3 + 1:02d}  "
    out += [p, p + "Finished running 3 seeds, 2 table models, 3 view models, 10 tests in 0 hours 0 minutes and 2.31 seconds (2.31s).", p]
    if ok:
        out += [p + "Completed successfully", p, p + "Done. PASS=18 WARN=0 ERROR=0 SKIP=0 TOTAL=18"]
    else:
        out += [p + "Completed with 1 error and 1 warning:", p, p + "Failure in test unique_orders_order_id (models/schema.yml)", p + "  Got 3 results, configured to fail if != 0", p,
                p + "  compiled Code at target/compiled/jaffle/models/schema.yml/unique_orders_order_id.sql", p, p + "Done. PASS=16 WARN=1 ERROR=1 SKIP=0 TOTAL=18"]
    return "\n".join(out) + "\n"


case(
    885,
    claim="dbt result lines lose their clock prefix and dot leaders and their START lines are dropped when a result line with the same 'N of M' exists; --debug blocks (a [debug] line and the SQL lines under it) are dropped unless a line in the block names an error or failure; every node's status, description and timing, START lines with no result, failure details and the Done summary are unchanged.",
    loses="the clock time of each node line, the dot-leader alignment, and the connection, BEGIN/COMMIT and SQL-echo lines of --debug runs",
    encode=compact_dbt,
    probes=probes_885,
    fixtures=lambda: [
        Fixture("dbt build, 18 nodes, all pass", _dbt_run(True), True),
        Fixture("failure variant: seed 1 errors first, a test FAILs in the middle, a test WARNs last", _dbt_run(False), True),
        Fixture("dbt build --debug, all pass", _dbt_run(True, debug=True), True),
        Fixture("dbt compile style output without node lines", "10:15:01  Running with dbt=1.7.4\n10:15:02  Compiled node 'orders' is:\nselect 1\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #982 Web fuzzer output: fold the dominant response signature (ffuf and wfuzz).
#
# Signature = (status, size, words, lines) for ffuf, (code, lines, words, chars) for wfuzz. A signature that occurs 10
# or more times is dominant (wildcard / soft-404): all its lines become one marker at the first occurrence, with the
# count and the first and last word. Lines of rarer signatures and every other line (banner, config echo) are verbatim.

_DOM = 10
_FFUF = re.compile(r"^([^ ]+) +\[Status: (\d+), Size: (\d+), Words: (\d+), Lines: (\d+), Duration: (\d+)ms\]$", A)
_WFUZZ = re.compile(r'^(\d{9}):   (\d{3}) +(\d+) L +(\d+) W +(\d+) Ch +"(.*)"$', A)
_M982F = re.compile(r"^\[(\d+) more lines with Status: (\d+), Size: (\d+), Words: (\d+), Lines: (\d+); first word: ([^ ]+); last word: ([^ ]+)\]$", A)
_M982W = re.compile(r'^\[(\d+) more lines with (\d{3}), (\d+) L, (\d+) W, (\d+) Ch; first: (\d{9}) "(.*)"; last: (\d{9}) "(.*)"\]$', A)


def _fuzz_sig(line: str):
    m = _FFUF.match(line)
    if m:
        return ("ffuf", m.group(2), m.group(3), m.group(4), m.group(5)), m.group(1)
    m = _WFUZZ.match(line)
    if m:
        return ("wfuzz", m.group(2), m.group(3), m.group(4), m.group(5)), f'{m.group(1)} "{m.group(6)}"'
    return None


def fold_fuzzer(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M982F.match(l) or _M982W.match(l) for l in lines):
        return text
    sigs = [_fuzz_sig(l) for l in lines]
    count = Counter(s[0] for s in sigs if s)
    dom = {k for k, v in count.items() if v >= _DOM}
    if not dom:
        return text
    firsts: dict = {}
    lasts: dict = {}
    for i, s in enumerate(sigs):
        if s and s[0] in dom:
            firsts.setdefault(s[0], i)
            lasts[s[0]] = i
    out: list[str] = []
    for i, l in enumerate(lines):
        s = sigs[i]
        if not (s and s[0] in dom):
            out.append(l)
        elif firsts[s[0]] == i:
            kind, a, b, c, d = s[0]
            fw, lw = sigs[firsts[s[0]]][1], sigs[lasts[s[0]]][1]
            if kind == "ffuf":
                out.append(f"[{count[s[0]]} more lines with Status: {a}, Size: {b}, Words: {c}, Lines: {d}; first word: {fw}; last word: {lw}]")
            else:
                fi, _, fwd = fw.partition(" ")
                li, _, lwd = lw.partition(" ")
                out.append(f"[{count[s[0]]} more lines with {a}, {b} L, {c} W, {d} Ch; first: {fi} {fwd}; last: {li} {lwd}]")
    return gate(text, join_nl(out, nl))


def _probe_fuzz_line(line: str):
    """Probe-side parse (string surgery): (signature tuple, word) for an ffuf or wfuzz result line, else None."""
    left, sep, rest = line.partition(" [Status: ")
    word = left.rstrip(" ")
    if sep and word and " " not in word and rest.endswith("ms]"):
        p = rest[:-1].split(", ")
        if len(p) == 5 and isdigits(p[0]) and [x.split(": ")[0] for x in p[1:]] == ["Size", "Words", "Lines", "Duration"]:
            vals = [x.split(": ", 1)[1] if ": " in x else "" for x in p[1:]]
            if all(isdigits(v) for v in vals[:3]) and vals[3].endswith("ms") and isdigits(vals[3][:-2]):
                return ("ffuf", p[0], vals[0], vals[1], vals[2]), word
    head, sep, rest = line.partition(":   ")
    if sep and len(head) == 9 and isdigits(head) and len(rest) > 3 and isdigits(rest[:3]) and rest[3] == " ":
        rem, nums = rest[3:], []
        for unit in ("L", "W", "Ch"):
            num, sp, rem = rem.lstrip(" ").partition(" ")
            u, sp2, rem = rem.partition(" ")
            if not (sp and sp2 and isdigits(num) and u == unit):
                return None
            nums.append(num)
        q = rem.lstrip(" ")
        if len(q) >= 2 and q.startswith('"') and q.endswith('"'):
            return ("wfuzz", rest[:3], *nums), f'{head} "{q[1:-1]}"'
    return None


def probes_982(text: str) -> dict:
    lines, _ = split_nl(text)
    count: Counter = Counter()
    words: dict = defaultdict(list)
    marker_fl: dict = {}
    seq: list = []  # (signature or None, line)
    for l in lines:
        m = _M982F.match(l)
        if m:
            sig = ("ffuf", *m.group(2, 3, 4, 5))
            count[sig] += int(m.group(1))
            marker_fl[sig] = (m.group(6), m.group(7))
            continue
        m = _M982W.match(l)
        if m:
            sig = ("wfuzz", *m.group(2, 3, 4, 5))
            count[sig] += int(m.group(1))
            marker_fl[sig] = (f'{m.group(6)} "{m.group(7)}"', f'{m.group(8)} "{m.group(9)}"')
            continue
        r = _probe_fuzz_line(l)
        if r:
            count[r[0]] += 1
            words[r[0]].append(r[1])
        seq.append((r[0] if r else None, l))
    dom = {k for k, v in count.items() if v >= _DOM}
    fl = {k: marker_fl[k] if k in marker_fl else (words[k][0], words[k][-1]) for k in dom}
    return {
        "responses per signature": sorted(count.items()),
        "lines of signatures seen fewer than 10 times, and every non-result line, in order": [l for sig, l in seq if sig not in dom],
        "first and last word of each dominant signature": sorted(fl.items()),
        "total responses": sum(count.values()),
    }


def _ffuf_run(seed: int, n: int, real: dict, wild=("200", 4242, 12, 3), soft404=None) -> str:
    rng = random.Random(seed)
    head = ["        /'___\\  /'___\\           /'___\\", "       /\\ \\__/ /\\ \\__/  __  __  /\\ \\__/", "________________________________________________", "",
            " :: Method           : GET", " :: URL              : http://target.local/FUZZ", " :: Matcher          : Response status: 200-299,301,302,307,401,403,405,500", "________________________________________________", ""]
    out = list(head)
    alpha = "abcdefghijklmnopqrstuvwxyz"
    for i in range(n):
        word = real.get(i) or "".join(rng.choice(alpha) for _ in range(rng.randint(3, 9)))
        if i in real:
            out.append(f"{word:<24}[Status: 301, Size: 169, Words: 5, Lines: 8, Duration: {rng.randint(3, 40)}ms]")
        elif soft404 and i % 97 == 0:
            out.append(f"{word:<24}[Status: 403, Size: 277, Words: 20, Lines: 10, Duration: {rng.randint(3, 40)}ms]")
        else:
            out.append(f"{word:<24}[Status: {wild[0]}, Size: {wild[1]}, Words: {wild[2]}, Lines: {wild[3]}, Duration: {rng.randint(3, 40)}ms]")
    return "\n".join(out) + "\n"


def _wfuzz_run(seed: int, n: int, real: dict) -> str:
    rng = random.Random(seed)
    out = ["********************************************************", "* Wfuzz 3.1.0 - The Web Fuzzer                         *", "********************************************************", "",
           "Target: http://target.local/FUZZ", f"Total requests: {n}", "", "=====================================================================",
           "ID           Response   Lines    Word       Chars       Payload", "=====================================================================", ""]
    alpha = "abcdefghijklmnopqrstuvwxyz"
    for i in range(1, n + 1):
        word = real.get(i) or "".join(rng.choice(alpha) for _ in range(rng.randint(3, 9)))
        if i in real:
            out.append(f'{i:09d}:   200        14 L     41 W       612 Ch      "{word}"')
        else:
            out.append(f'{i:09d}:   404        9 L      31 W       273 Ch      "{word}"')
    out += ["", "Total time: 4.2", "Processed Requests: %d" % n, "Filtered Requests: %d" % (n - len(real))]
    return "\n".join(out) + "\n"


case(
    982,
    claim="ffuf and wfuzz result lines whose response signature (status, size, words, lines) occurs 10 or more times fold into one '[N more lines with ...; first word: a; last word: z]' marker; every line of a rarer signature and every banner or config line stays verbatim and in order, and the per-signature counts add up to the total.",
    loses="the words and durations of the folded lines other than the first and last, and where in the run the dominant responses fell",
    encode=fold_fuzzer,
    probes=probes_982,
    fixtures=lambda: [
        Fixture("ffuf, 400 words, wildcard 200s, real hits at the start, middle and end", _ffuf_run(982, 400, {0: "admin", 211: "api", 399: "robots.txt"}), True),
        Fixture("ffuf failure variant: soft-404 wildcard plus a 403 every 97th word and a hit first", _ffuf_run(11, 300, {0: "backup"}, ("404", 1203, 57, 11), soft404=True), True),
        Fixture("wfuzz, 300 payloads, 404 signature dominant, three 200s", _wfuzz_run(5, 300, {1: "admin", 150: "login", 300: "uploads"}), True),
        Fixture("ffuf run where every response differs (no dominant signature)", "\n".join(f"w{i:<22}[Status: 200, Size: {100 + i}, Words: {i}, Lines: {i % 5}, Duration: 9ms]" for i in range(14)) + "\n", False),
        Fixture("ffuf with fewer than 10 equal responses", _ffuf_run(1, 9, {}), False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #804 Periodic sampler output (vmstat-style numeric tables, ping): print the header once, fold a run of 5+ samples
# into a min/avg/max line, keep the outlier rows verbatim.
#
# Table block = consecutive all-numeric lines with the same column count, continued across a header pair that repeats
# verbatim (vmstat reprints 'procs ---memory---' and the column names every screenful; header = the non-numeric line
# above the block with one name per column, plus the line above it). A block of 5+ rows becomes
# '[N samples: name min/avg/max, constant=v, ...]' plus its outlier rows (a value above 5 x the column median + 20; upward
# spikes only). Ping block = 5+ consecutive replies from one host; outlier = time above 3 x median + 10 ms; the marker
# lists sequence numbers that never answered. Shorter blocks and every non-sample line are verbatim.

_NUMTOK = re.compile(r"-?\d+(?:\.\d+)?$", A)
_PING = re.compile(r"^(\d+) bytes from (.+?): icmp_seq=(\d+) ttl=(\d+) time=(\d+(?:\.\d+)?) ms$", A)
_M804T = re.compile(r"^\[(\d+) samples: (.*)\]$", A)
_M804P = re.compile(r"^\[(\d+) replies of (\d+) bytes from (.+): seq (\d+)-(\d+), ttl ([0-9/]+), time (\d+(?:\.\d+)?)/(\d+\.\d)/(\d+(?:\.\d+)?) ms(?:, missing seq ([0-9,]+))?\]$", A)
_MIN_RUN = 5


def _row(line: str):
    t = [x for x in line.split(" ") if x]
    return t if len(t) >= 4 and all(_NUMTOK.match(x) for x in t) else None


def _triple(vals: list[str]):
    fl = [float(x) for x in vals]
    return vals[fl.index(min(fl))], f"{statistics.fmean(fl):.1f}", vals[fl.index(max(fl))]


def _col_stats(rows: list[list[str]], names: list[str]) -> str:
    parts = []
    for c, name in enumerate(names):
        col = [r[c] for r in rows]
        if len({float(x) for x in col}) == 1:
            parts.append(f"{name}={col[0]}")
        else:
            lo, av, hi = _triple(col)
            parts.append(f"{name} {lo}/{av}/{hi}")
    return ", ".join(parts)


def _is_header(line: str, nc: int):
    t = [x for x in line.split(" ") if x]
    return t if len(t) == nc and not any(_NUMTOK.match(x) for x in t) and not any("," in x for x in t) else None


def _run_end(rows, i: int, nc: int) -> int:
    j = i
    while j < len(rows) and rows[j] and len(rows[j]) == nc:
        j += 1
    return j


def fold_samplers(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M804T.match(l) or _M804P.match(l) for l in lines):
        return text
    n = len(lines)
    rows = [_row(l) for l in lines]
    pings = [_PING.match(l) for l in lines]
    out: list[str] = []
    i = 0
    while i < n:
        if rows[i]:
            nc = len(rows[i])
            j = _run_end(rows, i, nc)
            hdr = _is_header(lines[i - 1], nc) if i > 0 and not rows[i - 1] else None
            unit = None
            if hdr:
                g = lines[i - 2] if i >= 2 and not rows[i - 2] and lines[i - 2].strip(" ") else None
                unit = ([g] if g is not None else []) + [lines[i - 1]]
            first_end, runs = j, [(i, j)]
            while unit and lines[j:j + len(unit)] == unit and j + len(unit) < n and rows[j + len(unit)] and len(rows[j + len(unit)]) == nc:
                k = j + len(unit)
                j = _run_end(rows, k, nc)
                runs.append((k, j))
            blk = [r for a, b in runs for r in rows[a:b]]
            raw = [l for a, b in runs for l in lines[a:b]]
            if len(blk) >= _MIN_RUN:
                names = hdr or [f"c{c + 1}" for c in range(nc)]
                med = [statistics.median(float(r[c]) for r in blk) for c in range(nc)]
                out.append(f"[{len(blk)} samples: {_col_stats(blk, names)}]")
                out += [l for r, l in zip(blk, raw) if any(float(r[c]) > 5 * med[c] + 20 for c in range(nc))]
                i = j
            else:
                out += lines[i:first_end]
                i = first_end
        elif pings[i]:
            key = (pings[i].group(1), pings[i].group(2))
            j = i
            while j < n and pings[j] and (pings[j].group(1), pings[j].group(2)) == key:
                j += 1
            if j - i >= _MIN_RUN:
                ms = [pings[k] for k in range(i, j)]
                times = [m.group(5) for m in ms]
                seqs = [int(m.group(3)) for m in ms]
                ttls = "/".join(sorted({m.group(4) for m in ms}, key=int))
                miss = sorted(set(range(min(seqs), max(seqs) + 1)) - set(seqs))
                lo, av, hi = _triple(times)
                med = statistics.median(float(t) for t in times)
                out.append(f"[{j - i} replies of {key[0]} bytes from {key[1]}: seq {seqs[0]}-{seqs[-1]}, ttl {ttls}, time {lo}/{av}/{hi} ms" + (f", missing seq {','.join(map(str, miss))}" if miss else "") + "]")
                out += [lines[i + k] for k, t in enumerate(times) if float(t) > 3 * med + 10]
            else:
                out += lines[i:j]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return gate(text, join_nl(out, nl))


def _is_num(x: str) -> bool:
    s = x[1:] if x.startswith("-") else x
    a, dot, b = s.partition(".")
    return isdigits(a) and (not dot or isdigits(b))


def _pr_row(line: str):
    t = [x for x in line.split(" ") if x]
    return t if len(t) >= 4 and all(_is_num(x) for x in t) else None


def _pr_ping(line: str):
    """(bytes, host, seq, ttl, time) by token surgery, or None."""
    t = line.split(" ")
    if len(t) < 8 or t[1] != "bytes" or t[2] != "from" or t[-1] != "ms" or not isdigits(t[0]):
        return None
    host, seq, ttl, tm = " ".join(t[3:-4]), t[-4], t[-3], t[-2]
    if not (host.endswith(":") and len(host) > 1 and seq.startswith("icmp_seq=") and ttl.startswith("ttl=") and tm.startswith("time=")):
        return None
    if not (isdigits(seq[9:]) and isdigits(ttl[4:]) and _is_num(tm[5:]) and tm[5] != "-"):
        return None
    return t[0], host[:-1], seq[9:], ttl[4:], tm[5:]


def _pr_triple(vals: list[str]):
    lo = next(v for v in vals if float(v) == min(float(x) for x in vals))
    hi = next(v for v in vals if float(v) == max(float(x) for x in vals))
    return lo, "%.1f" % (sum(float(v) for v in vals) / len(vals)), hi


def _pr_median(xs: list[float]) -> float:
    s = sorted(xs)
    return (s[(len(s) - 1) // 2] + s[len(s) // 2]) / 2


def _pr_table(blk: list[list[str]], names: list[str]) -> list[tuple]:
    out = []
    for c, name in enumerate(names):
        col = [r[c] for r in blk]
        out.append((name, "=", col[0]) if len({float(x) for x in col}) == 1 else (name, *_pr_triple(col)))
    return out


def probes_804(text: str) -> dict:
    lines, _ = split_nl(text)
    n = len(lines)
    kind = ["mt" if _M804T.match(l) else "mp" if _M804P.match(l) else "row" if _pr_row(l) else "ping" if _pr_ping(l) else "other" for l in lines]
    items: list = []  # ('table', n, stats, outliers) | ('ping', facts, outliers) | ('plain', line) | ('other', samples_before, line)
    vsamples = 0
    i = 0
    while i < n:
        k = kind[i]
        if k == "mt":
            m = _M804T.match(lines[i])
            stats = []
            for part in m.group(2).split(", "):
                if " " in part:
                    name, st = part.split(" ", 1)
                    stats.append((name, *st.split("/")))
                else:
                    name, _, v = part.rpartition("=")
                    stats.append((name, "=", v))
            j = i + 1
            while j < n and kind[j] == "row" and len(_pr_row(lines[j])) == len(stats):
                j += 1
            items.append(("table", int(m.group(1)), stats, lines[i + 1:j]))
            vsamples += int(m.group(1))
            i = j
        elif k == "mp":
            m = _M804P.match(lines[i])
            j = i + 1
            while j < n and kind[j] == "ping" and _pr_ping(lines[j])[:2] == (m.group(2), m.group(3)):
                j += 1
            items.append(("ping", m.groups(), lines[i + 1:j]))
            vsamples += int(m.group(1))
            i = j
        elif k == "row":
            nc = len(_pr_row(lines[i]))
            runs = []
            a = i
            while True:  # runs of rows, chained across a repeated header unit
                b = a
                while b < n and kind[b] == "row" and len(_pr_row(lines[b])) == nc:
                    b += 1
                runs.append((a, b))
                unit = None
                if len(runs) == 1 and i > 0 and kind[i - 1] == "other":
                    t = [x for x in lines[i - 1].split(" ") if x]
                    if len(t) == nc and not any(_is_num(x) for x in t) and not any("," in x for x in t):
                        g = lines[i - 2] if i >= 2 and kind[i - 2] == "other" and lines[i - 2].strip(" ") else None
                        unit = ([g] if g is not None else []) + [lines[i - 1]]
                        hdr = t
                if len(runs) == 1:
                    hdr_unit = unit
                    hdr_names = hdr if unit else None
                if hdr_unit and lines[b:b + len(hdr_unit)] == hdr_unit and b + len(hdr_unit) < n and kind[b + len(hdr_unit)] == "row" and len(_pr_row(lines[b + len(hdr_unit)])) == nc:
                    a = b + len(hdr_unit)
                else:
                    break
            blk = [_pr_row(l) for x, y in runs for l in lines[x:y]]
            raw = [l for x, y in runs for l in lines[x:y]]
            if len(blk) >= _MIN_RUN:
                names = hdr_names or [f"c{c + 1}" for c in range(nc)]
                med = [_pr_median([float(r[c]) for r in blk]) for c in range(nc)]
                outs = [l for r, l in zip(blk, raw) if any(float(r[c]) > 5 * med[c] + 20 for c in range(nc))]
                items.append(("table", len(blk), _pr_table(blk, names), outs))
                vsamples += len(blk)
                i = runs[-1][1]
            else:
                items += [("plain", l) for l in lines[runs[0][0]:runs[0][1]]]
                vsamples += runs[0][1] - runs[0][0]
                i = runs[0][1]
        elif k == "ping":
            p0 = _pr_ping(lines[i])
            j = i
            while j < n and kind[j] == "ping" and _pr_ping(lines[j])[:2] == p0[:2]:
                j += 1
            if j - i >= _MIN_RUN:
                ps = [_pr_ping(l) for l in lines[i:j]]
                times, seqs = [p[4] for p in ps], [int(p[2]) for p in ps]
                lo, av, hi = _pr_triple(times)
                med = _pr_median([float(t) for t in times])
                have = set(seqs)
                miss = ",".join(str(q) for q in range(min(seqs), max(seqs) + 1) if q not in have) or None
                items.append(("ping", (str(j - i), p0[0], p0[1], str(seqs[0]), str(seqs[-1]), "/".join(sorted({p[3] for p in ps}, key=int)), lo, av, hi, miss),
                              [l for l, t in zip(lines[i:j], times) if float(t) > 3 * med + 10]))
            else:
                items += [("plain", l) for l in lines[i:j]]
            vsamples += j - i
            i = j
        else:
            items.append(("other", vsamples, lines[i]))
            i += 1
    # a header pair that repeats inside a folded table block is gone from the encoding: the 'other' lines are compared
    # without the pairs that sit between two runs of a folded block (the encoder dropped them by construction)
    return {
        "sample blocks (count, per-column min/avg/max or constant, outlier rows) and short blocks verbatim": [it for it in items if it[0] != "other"],
        "non-sample lines in order (samples before, text)": [(it[1], it[2]) for it in items if it[0] == "other"],
        "virtual sample count": vsamples,
    }


# `vmstat 1 24` captured on this container (a busy build box: bi/bo/in/cs spike; vmstat reprinted its header after 21 rows).
VMSTAT_REAL = """procs -----------memory---------- ---swap-- -----io---- -system-- -------cpu-------
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st gu
 2  0      0 13506864  52604 2339280    0    0  1158   961  563    1  2  1 96  1  0  0
 0  1      0 13490320  53624 2351588    0    0  5960 23384 2379 2521 25  7 55 13  0  0
 1  0      0 13503240  53692 2359572    0    0  5088     0 1430 1625 15  3 74  7  0  0
 1  0      0 13445688  53692 2363520    0    0  1184     0 3445 5704 21  5 74  0  0  0
 1  0      0 13440436  53784 2365812    0    0   424     0 1596 1876 23  3 74  0  0  0
 1  0      0 13438000  53868 2369368    0    0   920     0 3231 5198 22  4 73  0  0  0
 1  0      0 13453644  54380 2376604    0    0  2944 10520 1210 1101 23  3 73  1  0  0
 0  0      0 13475848  54440 2380764    0    0  1328     0 2644 4208 16  1 82  0  0  0
 1  0      0 13493904  54440 2381872    0    0     0     8  564  555  3  1 97  0  0  0
 0  0      0 13444792  54440 2384096    0    0    72     0  981  827 14  3 81  0  2  0
 0  0      0 13466352  54440 2386196    0    0     0     0  678  439  2  1 96  0  0  0
 1  0      0 13469464  54448 2389000    0    0   192 49992 3550 5029 26  6 67  0  0  0
 1  0      0 13460552  54592 2390832    0    0     0     0 1270  903 22  3 75  0  0  0
 0  0      0 13482068  54596 2393760    0    0   516   780 1014  679  4  1 94  0  0  0
 1  0      0 13493628  54616 2395620    0    0     0   104  716  494  2  0 98  0  0  0
 0  0      0 13507816  54648 2397840    0    0   540     8 1072  707  1  2 97  0  0  0
 1  0      0 13500112  55588 2416184    0    0  1092  7204 6245 9532  5  7 87  0  0  0
 0  0      0 13493064  55848 2427340    0    0   428    12 4029 5421 14  7 79  0  1  0
 0  0      0 13485676  56792 2435368    0    0   920   196 2194 2039  5  4 91  1  0  0
 1  0      0 13524232  56824 2438936    0    0   364     8 1565 2046  3  3 94  0  0  0
 3  0      0 13497132  56824 2445948    0    0  3784    32 2920 3536 39  6 47  7  1  0
procs -----------memory---------- ---swap-- -----io---- -system-- -------cpu-------
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st gu
 2  1      0 13447272  56852 2452000    0    0  3436 26952 4263 5831 47  7 43  2  1  0
 0  0      0 13409476  56916 2461104    0    0  6784     0 3104 3253 42  6 48  4  0  0
 5  0      0 13406536  57068 2463288    0    0    16     0 1816 2046 15  3 81  0  1  0
"""


def _vmstat_quiet(rows: int = 30, spikes=((0, "bo", 4096), (14, "us", 63), (29, "wa", 41))) -> str:
    """Idle host, reconstructed from the shape of an idle `vmstat 1` capture: constant swap and io columns, small noise, three
    spikes (first row, middle, last row)."""
    rng = random.Random(804)
    head = ["procs -----------memory---------- ---swap-- -----io---- -system-- -------cpu-------", " r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st gu"]
    out = list(head)
    free = 15735468
    for k in range(rows):
        free += rng.randint(-900, 700)
        vals = {"r": 0, "b": 0, "swpd": 0, "free": free, "buff": 16668, "cache": 410740 + k * 2, "si": 0, "so": 0, "bi": 0, "bo": 0,
                "in": 115 + rng.randint(0, 20), "cs": 85 + rng.randint(0, 30), "us": rng.randint(0, 1), "sy": 0, "id": 99 - rng.randint(0, 1), "wa": 0, "st": 0, "gu": 0}
        for at, col, v in spikes:
            if k == at:
                vals[col] = v
                if col == "us":
                    vals["id"] = 99 - v
                    vals["r"] = 6
                if col == "wa":
                    vals["b"] = 3
        w = [2, 2, 6, 9, 6, 7, 4, 4, 5, 5, 4, 4, 2, 2, 2, 2, 2, 2]
        out.append(" ".join(f"{v:>{w[c]}}" for c, v in enumerate(vals.values())).rjust(0))
    return "\n".join(out) + "\n"


def _ping_run(n: int, lost=(), spike=None, host="ams15s48-in-f14.1e100.net (142.250.179.142)") -> str:
    """Linux iputils `ping -c N` output, reconstructed from memory of the format."""
    rng = random.Random(n)
    out = [f"PING google.com ({host.split('(')[1][:-1]}) 56(84) bytes of data."]
    for q in range(1, n + 1):
        if q in lost:
            continue
        t = 11.0 + rng.random() * 2.5
        if spike and q in spike:
            t = spike[q]
        out.append(f"64 bytes from {host}: icmp_seq={q} ttl=117 time={t:.1f} ms")
    got = n - len(lost)
    out += ["", "--- google.com ping statistics ---", f"{n} packets transmitted, {got} received, {100 * len(lost) // n}% packet loss, time {1000 * n - 955}ms", "rtt min/avg/max/mdev = 11.0/12.3/13.5/0.7 ms"]
    return "\n".join(out) + "\n"


case(
    804,
    claim="Runs of 5+ vmstat-style numeric rows (continued across a repeated header pair) fold to '[N samples: name min/avg/max, constant=v ...]' plus the rows whose value is above 5x the column median + 20; runs of 5+ ping replies fold to count, seq range, ttl, min/avg/max time and the sequence numbers that never answered, plus replies slower than 3x median + 10 ms; short runs and every header, summary or error line stay verbatim.",
    loses="the in-between rows that are not upward outliers (their values survive only as min/avg/max per column), dips below the median (only the minimum), and the repeated header pairs after the first",
    encode=fold_samplers,
    probes=probes_804,
    fixtures=lambda: [
        Fixture("real vmstat 1 24 (busy box, header reprinted after 21 rows)", VMSTAT_REAL, True),
        Fixture("quiet vmstat, 30 rows, spikes at the first, the middle and the last row", _vmstat_quiet(), True),
        Fixture("ping -c 30 with a latency spike at the start and end, replies 11-13 and 18-19 lost", _ping_run(30, lost=(11, 12, 13, 18, 19), spike={1: 240.5, 30: 188.2}), True),
        Fixture("ping -c 4 (too short to fold)", _ping_run(4), False),
        Fixture("vmstat with 3 rows", "\n".join(VMSTAT_REAL.split("\n")[:5]) + "\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #809 Fingerprint SQL statement logs (Postgres log_min_duration_statement lines): literals become ?, parenthesised lists of
# ? become (?+), statements with the same fingerprint seen 3+ times fold into '[xN min a max b total c ms] fingerprint'
# at their first occurrence, followed by the slowest occurrence verbatim (with its literals). Everything else (rarer
# fingerprints, ERROR/STATEMENT/DETAIL lines, multi-line statements) is verbatim.

_PGSQL = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ [A-Z]{3,5} \[\d+\]) LOG:  duration: (\d+\.\d{3}) ms  statement: (.+)$", A)
_M809 = re.compile(r"^\[x(\d+) min (\d+\.\d{3}) max (\d+\.\d{3}) total (\d+\.\d{3}) ms\] (.*)$", A)
_SQLSTR = re.compile(r"'(?:[^']|'')*+'")
_SQLNUM = re.compile(r"(?<![A-Za-z0-9_$])\d+(?:\.\d+(?![A-Za-z0-9_$]))?(?![A-Za-z0-9_$])", A)
_SQLLIST = re.compile(r"\( *\? *(?:, *\? *)+\)")
_GROUP_MIN = 3


def fingerprint(stmt: str) -> str:
    s = _SQLNUM.sub("?", _SQLSTR.sub("?", stmt))
    return " ".join(t for t in _SQLLIST.sub("(?+)", s).split(" ") if t)


def _ms(d: str) -> int:
    return int(d.replace(".", ""))


def _fmt_ms(t: int) -> str:
    return f"{t // 1000}.{t % 1000:03d}"


def fold_sql(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M809.match(l) for l in lines):
        return text
    recs = []
    for i, l in enumerate(lines):
        m = _PGSQL.match(l)
        cont = i + 1 < len(lines) and lines[i + 1][:1] in ("\t", " ")
        recs.append((fingerprint(m.group(3)), _ms(m.group(2))) if m and not cont else None)
    groups: dict = defaultdict(list)
    for i, r in enumerate(recs):
        if r:
            groups[r[0]].append(i)
    big = {fp: idx for fp, idx in groups.items() if len(idx) >= _GROUP_MIN}
    first = {idx[0]: fp for fp, idx in big.items()}
    drop = {i for idx in big.values() for i in idx}
    out: list[str] = []
    for i, l in enumerate(lines):
        if i in first:
            idx = big[first[i]]
            durs = [recs[k][1] for k in idx]
            slow = idx[durs.index(max(durs))]
            out.append(f"[x{len(idx)} min {_fmt_ms(min(durs))} max {_fmt_ms(max(durs))} total {_fmt_ms(sum(durs))} ms] {first[i]}")
            out.append(lines[slow])
        elif i not in drop:
            out.append(l)
    return gate(text, join_nl(out, nl))


def _fp_probe(stmt: str) -> str:
    """Probe-side fingerprint: one left-to-right scan, no regex."""
    out, i, n = [], 0, len(stmt)
    word = lambda ch: ch.isascii() and (ch.isalnum() or ch in "_$")  # noqa: E731
    while i < n:
        ch = stmt[i]
        if ch == "'":
            j = i + 1
            while True:
                k = stmt.find("'", j)
                if k < 0:
                    out.append("'")  # unterminated: the quote stays, scanning resumes after it
                    i += 1
                    break
                if stmt[k + 1:k + 2] == "'":
                    j = k + 2
                    continue
                out.append("?")
                i = k + 1
                break
        elif word(ch):
            j = i
            while j < n and word(stmt[j]):
                j += 1
            w = stmt[i:j]
            if isdigits(w):
                if stmt[j:j + 1] == "." and j + 1 < n and isdigits(stmt[j + 1:j + 2]):
                    k = j + 1
                    while k < n and word(stmt[k]):
                        k += 1
                    if isdigits(stmt[j + 1:k]):
                        j = k
                out.append("?")
            else:
                out.append(w)
            i = j
        else:
            out.append(ch)
            i += 1
    s = "".join(out)
    res, i = [], 0
    while i < len(s):
        if s[i] == "(":
            j, cnt = i + 1, 0
            while True:
                while j < len(s) and s[j] == " ":
                    j += 1
                if j < len(s) and s[j] == "?":
                    cnt += 1
                    j += 1
                    while j < len(s) and s[j] == " ":
                        j += 1
                    if j < len(s) and s[j] == ",":
                        j += 1
                        continue
                break
            if cnt >= 2 and j < len(s) and s[j] == ")":
                res.append("(?+)")
                i = j + 1
                continue
        res.append(s[i])
        i += 1
    return " ".join(t for t in "".join(res).split(" ") if t)


def _pg_probe(line: str):
    """(duration thousandths, statement) for a Postgres duration/statement line, by partitioning."""
    head, sep, rest = line.partition(" LOG:  duration: ")
    if not sep:
        return None
    dur, sep2, stmt = rest.partition(" ms  statement: ")
    h = head.split(" ")
    d, _, frac = dur.partition(".")
    if not (sep2 and stmt and len(h) == 4 and _isdate(h[0]) and _istime(h[1], ".") and "." in h[1] and h[2].isascii() and h[2].isalpha() and h[2].isupper() and 3 <= len(h[2]) <= 5
            and h[3].startswith("[") and h[3].endswith("]") and isdigits(h[3][1:-1]) and isdigits(d) and len(frac) == 3 and isdigits(frac)):
        return None
    return int(d + frac), stmt


def probes_809(text: str) -> dict:
    lines, _ = split_nl(text)
    kind: list = []  # per line: ('mark', fp) | ('stmt', fp) | ('other',)
    raw: dict = defaultdict(list)  # fp -> [(thousandths of ms, line)]
    marks: dict = {}
    for i, l in enumerate(lines):
        m = _M809.match(l)
        if m:
            marks[m.group(5)] = (int(m.group(1)), _ms(m.group(2)), _ms(m.group(3)), _ms(m.group(4)))
            kind.append(("mark", m.group(5)))
            continue
        r = _pg_probe(l)
        if r and not (i + 1 < len(lines) and lines[i + 1][:1] in ("\t", " ")):
            fp = _fp_probe(r[1])
            raw[fp].append((r[0], l))
            kind.append(("stmt", fp))
        else:
            kind.append(("other",))
    groups = dict(marks)  # a marker already carries its group (its slowest line follows it verbatim)
    for fp, rows in raw.items():
        if fp not in marks and len(rows) >= _GROUP_MIN:
            ms = [r[0] for r in rows]
            groups[fp] = (len(rows), min(ms), max(ms), sum(ms))
    slowest = {fp: next((l for ms, l in raw[fp] if ms == g[2]), None) for fp, g in groups.items()}
    return {
        "per repeated fingerprint: count, min, max, total (thousandths of ms)": sorted((fp, *g) for fp, g in groups.items()),
        "slowest statement of each repeated fingerprint, verbatim": sorted(slowest.items()),
        "lines of fingerprints seen fewer than 3 times, and every non-statement line, in order": [l for l, k in zip(lines, kind) if k[0] == "other" or (k[0] == "stmt" and k[1] not in groups)],
    }


def _sql_log(n: int = 70) -> str:
    rng = random.Random(809)
    out = ["2024-03-05 10:15:00.100 UTC [880] LOG:  database system is ready to accept connections"]
    ms = 100
    for i in range(n):
        ms += rng.randint(5, 80)
        ts = f"2024-03-05 10:15:{ms // 1000:02d}.{ms % 1000:03d} UTC [{900 + i % 4}]"
        r = rng.random()
        d = f"{rng.random() * 9 + 0.2:.3f}"
        if i == 33:
            out += [f"{ts} LOG:  duration: 840.213 ms  statement: SELECT i.id, i.name FROM shop_item i WHERE i.sku = 'SKU-77-EXPORT' ORDER BY i.id",
                    f"{ts} ERROR:  deadlock detected", f"{ts} DETAIL:  Process 901 waits for ShareLock on transaction 5521; blocked by process 903.",
                    f"{ts} STATEMENT:  UPDATE shop_cart SET updated = '2024-03-05 10:15:01.123456' WHERE id = 17"]
        elif r < 0.45:
            out.append(f"{ts} LOG:  duration: {d} ms  statement: SELECT u.id, u.username FROM auth_user u WHERE u.id = {rng.randint(1, 5000)} LIMIT 21")
        elif r < 0.7:
            ids = ", ".join(str(rng.randint(1, 900)) for _ in range(rng.randint(3, 12)))
            out.append(f"{ts} LOG:  duration: {d} ms  statement: SELECT i.id, i.price FROM shop_item i WHERE i.id IN ({ids})")
        elif r < 0.9:
            out.append(f"{ts} LOG:  duration: {d} ms  statement: UPDATE shop_cart SET updated = '2024-03-05 10:15:{ms // 1000:02d}.{rng.randint(100000, 999999)}', total = {rng.randint(1, 900)}.{rng.randint(10, 99)} WHERE id = {rng.randint(1, 300)}")
        else:
            out.append(f"{ts} LOG:  duration: {d} ms  statement: INSERT INTO shop_audit (cart_id, note) VALUES ({rng.randint(1, 300)}, 'it''s {rng.choice(['ok', 'late', 'dup'])}')")
    out.append(f"2024-03-05 10:15:09.999 UTC [880] LOG:  duration: 1930.004 ms  statement: SELECT count(*) FROM shop_item WHERE created < '2020-01-01'")
    return "\n".join(out) + "\n"


case(
    809,
    claim="Postgres 'duration: X ms  statement: SQL' lines whose fingerprint (string and number literals -> ?, lists of ? -> (?+), spaces collapsed) occurs 3+ times fold into '[xN min a max b total c ms] fingerprint' followed by the slowest occurrence verbatim; per-fingerprint count, min, max and total duration, the slowest statement with its literals, and every rarer, multi-line, ERROR, DETAIL and STATEMENT line are unchanged.",
    loses="the literals, durations and position of every repeated statement except the slowest one (its duration survives only in min/max/total)",
    encode=fold_sql,
    probes=probes_809,
    fixtures=lambda: [
        Fixture("Django request loop, 70 statements, slow SELECT and a deadlock in the middle, slowest query last", _sql_log(), True),
        Fixture("failure variant: only repeated statements, the ERROR line is the first line", "2024-03-05 10:15:00.000 UTC [1] ERROR:  relation \"shop_item\" does not exist\n" + "".join(f"2024-03-05 10:15:01.{k:03d} UTC [2] LOG:  duration: 0.{k + 100:03d} ms  statement: SELECT id FROM shop_item WHERE id = {k}\n" for k in range(12)), True),
        Fixture("every statement distinct", "".join(f"2024-03-05 10:15:01.{k:03d} UTC [2] LOG:  duration: 1.000 ms  statement: SELECT col{k} FROM t{k}\n" for k in range(6)), False),
        Fixture("application log, no SQL", "listening on :8080\nGET /health 200\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #978 Aggregate auth logs by source and user class; fold cron PAM triples.
#
# sshd failure lines (Failed password, Invalid user, Connection closed/Disconnected ... [preauth], Received disconnect
# ... [preauth], pam_unix(sshd:auth) authentication failure) in a run of 6+ consecutive lines become one
# '[... N auth failure lines folded: IP failed F (root a, invalid b, valid c) noise M first TS last TS; ...]' line, one entry
# per source. A cron 'session opened / (user) CMD / session closed' triple becomes the CMD line + ' [+2 pam lines]'.
# Accepted logins, sudo lines and everything unrecognised are verbatim.

_AUTH = re.compile(r"^([A-Z][a-z]{2} [ 0-3]\d \d\d:\d\d:\d\d) ([^ ]+) ([A-Za-z_./-]+)\[(\d+)\]: (.*)$", A)
_FAILED = re.compile(r"^Failed password for (invalid user )?([^ ]+) from ([^ ]+) port \d+ ssh2$", A)
_NOISE = [re.compile(p, A) for p in (
    r"^Invalid user [^ ]* from ([^ ]+) port \d+$",
    r"^Connection closed by (?:invalid|authenticating) user [^ ]+ ([^ ]+) port \d+ \[preauth\]$",
    r"^Disconnected from (?:invalid|authenticating) user [^ ]+ ([^ ]+) port \d+ \[preauth\]$",
    r"^Received disconnect from ([^ ]+) port \d+:\d+: .* \[preauth\]$",
    r"^pam_unix\(sshd:auth\): authentication failure; logname=.* rhost=([^ ]+)(?:  user=[^ ]*)?$")]
_M978 = re.compile(r"^\[\.\.\. (\d+) auth failure lines folded: (.*)\]$", A)
_M978_ENTRY = re.compile(r"^(\S+) failed (\d+) \(root (\d+), invalid (\d+), valid (\d+)\) noise (\d+) first (.{15}) last (.{15})$", A)
_CRON_OPEN = re.compile(r"^pam_unix\(cron:session\): session opened for user ([^ ]+)\(uid=\d+\) by \(uid=\d+\)$", A)
_CRON_CLOSE = re.compile(r"^pam_unix\(cron:session\): session closed for user ([^ ]+)$", A)
_CRON_SUFFIX = " [+2 pam lines]"
_AUTH_RUN = 6


def _auth_event(line: str):
    """(ts, ip, kind, user_class) for an sshd failure line, else None."""
    m = _AUTH.match(line)
    if not m or m.group(3) != "sshd":
        return None
    msg = m.group(5)
    f = _FAILED.match(msg)
    if f:
        cls = "invalid" if f.group(1) else ("root" if f.group(2) == "root" else "valid")
        return m.group(1), f.group(3), "fail", cls
    for rx in _NOISE:
        n = rx.match(msg)
        if n:
            return m.group(1), n.group(1), "noise", ""
    return None


def _cron_triple(lines: list[str], i: int) -> bool:
    if i + 2 >= len(lines):
        return False
    a, b, c = (_AUTH.match(l) for l in lines[i:i + 3])
    if not (a and b and c) or not (a.group(3) == b.group(3) == c.group(3) == "CRON") or not (a.group(2) == b.group(2) == c.group(2)):
        return False
    o, k = _CRON_OPEN.match(a.group(5)), _CRON_CLOSE.match(c.group(5))
    return bool(o and k and a.group(4) == c.group(4) and o.group(1) == k.group(1) and b.group(5).startswith(f"({o.group(1)}) CMD ("))


def fold_auth(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M978.match(l) or l.endswith(_CRON_SUFFIX) for l in lines):
        return text
    ev = [_auth_event(l) for l in lines]
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _cron_triple(lines, i):
            out.append(lines[i + 1] + _CRON_SUFFIX)
            i += 3
            continue
        if ev[i]:
            j = i
            while j < len(lines) and ev[j]:
                j += 1
            if j - i >= _AUTH_RUN:
                ips: dict = {}
                for e in ev[i:j]:
                    d = ips.setdefault(e[1], {"fail": 0, "root": 0, "invalid": 0, "valid": 0, "noise": 0, "first": e[0], "last": e[0]})
                    d["last"] = e[0]
                    if e[2] == "fail":
                        d["fail"] += 1
                        d[e[3]] += 1
                    else:
                        d["noise"] += 1
                ent = "; ".join(f"{ip} failed {d['fail']} (root {d['root']}, invalid {d['invalid']}, valid {d['valid']}) noise {d['noise']} first {d['first']} last {d['last']}" for ip, d in ips.items())
                marker = f"[... {j - i} auth failure lines folded: {ent}]"
                if len(marker) < sum(len(l) + 1 for l in lines[i:j]):
                    out.append(marker)
                    i = j
                    continue
            out += lines[i:j]
            i = j
            continue
        out.append(lines[i])
        i += 1
    return gate(text, join_nl(out, nl))


def _pa_prefix(line: str):
    """Probe-side syslog prefix: (ts, host, proc, pid, msg) by fixed offsets and partitions."""
    if len(line) < 17 or line[3] != " " or line[6] != " " or line[9] != ":" or line[12] != ":" or line[15] != " ":
        return None
    if not (line[0].isascii() and line[0].isupper() and line[1:3].isascii() and line[1:3].islower() and line[1:3].isalpha()):
        return None
    if not (line[4] in " 0123" and isdigits(line[5]) and isdigits(line[7:9] + line[10:12] + line[13:15])):
        return None
    host, sp, rest = line[16:].partition(" ")
    proc, br, rest2 = rest.partition("[")
    pid, br2, msg = rest2.partition("]: ")
    if not (host and sp and br and br2 and proc and all(c.isascii() and (c.isalpha() or c in "_./-") for c in proc) and isdigits(pid)):
        return None
    return line[:15], host, proc, pid, msg


def _pa_event(line: str):
    p = _pa_prefix(line)
    if not p or p[2] != "sshd":
        return None
    ts, msg = p[0], p[4]
    if msg.startswith("Failed password for "):
        r = msg[len("Failed password for "):]
        inv = r.startswith("invalid user ")
        if inv:
            r = r[len("invalid user "):]
        user, a, r = r.partition(" from ")
        ip, b, r = r.partition(" port ")
        port, c, tail = r.partition(" ")
        if a and b and c and user and " " not in user and ip and " " not in ip and isdigits(port) and tail == "ssh2":
            return ts, ip, "fail", "invalid" if inv else ("root" if user == "root" else "valid")
        return None
    if msg.startswith("Invalid user "):
        r = msg[len("Invalid user "):]
        user, a, r = r.partition(" from ")
        ip, b, port = r.partition(" port ")
        if a and b and " " not in user and ip and " " not in ip and isdigits(port):
            return ts, ip, "noise", ""
        return None
    for lead in ("Connection closed by ", "Disconnected from "):
        if msg.startswith(lead) and msg.endswith(" [preauth]"):
            r = msg[len(lead):-len(" [preauth]")]
            if r.startswith("invalid user ") or r.startswith("authenticating user "):
                _, _, r = r.partition(" user ")
                user, a, r = r.partition(" ")
                ip, b, port = r.partition(" port ")
                if a and b and user and ip and " " not in ip and isdigits(port):
                    return ts, ip, "noise", ""
            return None
    if msg.startswith("Received disconnect from ") and msg.endswith(" [preauth]"):
        r = msg[len("Received disconnect from "):]
        ip, b, r = r.partition(" port ")
        code, c, tail = r.partition(": ")
        n1, _, n2 = code.partition(":")
        if b and c and ip and " " not in ip and isdigits(n1) and isdigits(n2):
            return ts, ip, "noise", ""
        return None
    if msg.startswith("pam_unix(sshd:auth): authentication failure; logname="):
        head, sep, r = msg.rpartition(" rhost=")
        if sep:
            ip, two, usr = r.partition("  user=")
            if ip and " " not in ip and (not two or " " not in usr):
                return ts, ip, "noise", ""
    return None


def _pa_cron(lines: list[str], i: int) -> bool:
    if i + 2 >= len(lines):
        return False
    a, b, c = (_pa_prefix(l) for l in lines[i:i + 3])
    if not (a and b and c) or {a[2], b[2], c[2]} != {"CRON"} or not (a[1] == b[1] == c[1]) or a[3] != c[3]:
        return False
    lead, tail = "pam_unix(cron:session): session opened for user ", ") by (uid="
    if not (a[4].startswith(lead) and a[4].endswith(")") and tail in a[4]):
        return False
    who = a[4][len(lead):].partition("(uid=")
    if not (who[0] and " " not in who[0] and isdigits(who[2].partition(")")[0])):
        return False
    byuid = a[4].rpartition(tail)[2][:-1]
    return isdigits(byuid) and c[4] == f"pam_unix(cron:session): session closed for user {who[0]}" and b[4].startswith(f"({who[0]}) CMD (")


def probes_978(text: str) -> dict:
    lines, _ = split_nl(text)
    per_ip: dict = {}
    other: list[tuple[int, str]] = []
    accepted: list[tuple[int, str]] = []
    sudo: list[tuple[int, str]] = []
    cron: list[tuple[int, str]] = []
    v = 0
    i = 0
    while i < len(lines):
        l = lines[i]
        m = _M978.match(l)
        if m:
            for e in m.group(2).split("; "):
                g = _M978_ENTRY.match(e)
                ip = g.group(1)
                d = per_ip.setdefault(ip, {"fail": 0, "root": 0, "invalid": 0, "valid": 0, "noise": 0, "first": g.group(7), "last": g.group(8)})
                d["last"] = g.group(8)
                d["fail"] += int(g.group(2))
                d["root"] += int(g.group(3))
                d["invalid"] += int(g.group(4))
                d["valid"] += int(g.group(5))
                d["noise"] += int(g.group(6))
            v += int(m.group(1))
            i += 1
            continue
        if l.endswith(_CRON_SUFFIX):
            cron.append((v, l[:-len(_CRON_SUFFIX)]))
            v += 3
            i += 1
            continue
        if _pa_cron(lines, i):
            cron.append((v, lines[i + 1]))
            v += 3
            i += 3
            continue
        e = _pa_event(l)
        if e:
            d = per_ip.setdefault(e[1], {"fail": 0, "root": 0, "invalid": 0, "valid": 0, "noise": 0, "first": e[0], "last": e[0]})
            d["last"] = e[0]
            if e[2] == "fail":
                d["fail"] += 1
                d[e[3]] += 1
            else:
                d["noise"] += 1
        else:
            p = _pa_prefix(l)
            if p and p[2] == "sshd" and p[4].startswith("Accepted "):
                accepted.append((v, l))
            elif p and p[2] == "sudo":
                sudo.append((v, l))
            other.append((v, l))
        v += 1
        i += 1
    return {
        "per source: failed, by user class, noise lines, first and last time": sorted((ip, tuple(sorted(d.items()))) for ip, d in per_ip.items()),
        "accepted logins (virtual line, text)": accepted,
        "sudo lines (virtual line, text)": sudo,
        "cron commands (virtual line, text)": cron,
        "every other line (virtual line, text)": other,
        "virtual line count": v,
    }


def _auth_log() -> str:
    rng = random.Random(978)
    out = []
    t = [10 * 3600 + 15 * 60]

    def ts(step=1):
        t[0] += rng.randint(0, step)
        return f"Mar  5 {t[0] // 3600:02d}:{t[0] % 3600 // 60:02d}:{t[0] % 60:02d}"

    pid = [4000]

    def ssh(msg):
        pid[0] += 1
        return f"{ts()} web1 sshd[{pid[0]}]: {msg}"

    out.append(ssh("Accepted publickey for alice from 198.51.100.7 port 51234 ssh2: RSA SHA256:abcdef0123456789"))
    out.append(f"{ts()} web1 sshd[4000]: pam_unix(sshd:session): session opened for user alice(uid=1000) by (uid=0)")
    out.append(f"{ts()} web1 sudo:    alice : TTY=pts/0 ; PWD=/home/alice ; USER=root ; COMMAND=/usr/bin/apt update")
    for k in range(2):
        out += [f"{ts(30)} web1 CRON[2001]: pam_unix(cron:session): session opened for user root(uid=0) by (uid=0)", f"{ts(0)} web1 CRON[2002]: (root) CMD (   cd / && run-parts --report /etc/cron.hourly)",
                f"{ts(0)} web1 CRON[2001]: pam_unix(cron:session): session closed for user root"]
    users = ["admin", "ubuntu", "test", "oracle", "git", "postgres", "user", "pi"]
    for a in range(36):
        ip = "203.0.113.5" if a % 3 else "192.0.2.77"
        p = rng.randint(30000, 60000)
        if a % 4 == 0:
            out += [ssh(f"Failed password for root from {ip} port {p} ssh2"), ssh(f"Received disconnect from {ip} port {p}:11: Bye Bye [preauth]"), ssh(f"Disconnected from authenticating user root {ip} port {p} [preauth]")]
        else:
            u = rng.choice(users)
            out += [ssh(f"Invalid user {u} from {ip} port {p}"), ssh(f"pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost={ip}"),
                    ssh(f"Failed password for invalid user {u} from {ip} port {p} ssh2"), ssh(f"Connection closed by invalid user {u} {ip} port {p} [preauth]")]
    out += [ssh("Accepted password for deploy from 203.0.113.5 port 40022 ssh2"), f"{ts()} web1 sshd[4999]: pam_unix(sshd:session): session opened for user deploy(uid=1001) by (uid=0)",
            ssh("Failed password for deploy from 198.51.100.9 port 50111 ssh2")]
    return "\n".join(out) + "\n"


case(
    978,
    claim="A run of 6+ consecutive sshd failure lines (Failed password, Invalid user, Connection closed/Disconnected/Received disconnect [preauth], pam authentication failure) folds into one line with, per source address, the failed-password count split root/invalid/valid, the noise-line count and the first and last time; a cron session-open, CMD, session-close triple becomes the CMD line plus ' [+2 pam lines]'; every Accepted login, sudo line and unrecognised line is verbatim and keeps its position.",
    loses="the individual usernames tried, ports, PIDs and per-line times inside a folded run, and the cron session open/close lines",
    encode=fold_auth,
    probes=probes_978,
    fixtures=lambda: [
        Fixture("brute-force storm from two sources, an Accepted login before it and one from the attacker's address after", _auth_log(), True),
        Fixture("failure variant: the log starts inside a storm and ends mid-run", "".join(f"Mar  5 10:15:{k:02d} web1 sshd[{100 + k}]: Failed password for root from 203.0.113.9 port {40000 + k} ssh2\n" for k in range(14)) + "Mar  5 10:16:00 web1 sshd[300]: Accepted password for root from 203.0.113.9 port 40100 ssh2\n", True),
        Fixture("quiet auth.log", "Mar  5 10:15:01 web1 sshd[1]: Accepted publickey for alice from 198.51.100.7 port 51234 ssh2: RSA SHA256:abc\nMar  5 10:15:09 web1 sshd[1]: Failed password for alice from 198.51.100.7 port 51236 ssh2\n", False),
        Fixture("not an auth log", "GET /login 401\nGET /login 401\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #1330 DB settings dumps: keep only non-default rows.
#
# psql 'SELECT * FROM pg_settings' (aligned or '|'-separated): a row with source = 'default' and setting = boot_val is
# dropped. MySQL 'SHOW VARIABLES' (table form): a row whose name is in a small defaults table and whose value equals it is
# dropped; a name the table does not know is always kept (a full table of ~600 defaults would be needed to cover MySQL).
# One note line '-- N rows at their default value omitted' goes after the last kept row; header, separators, footer and every
# kept row are verbatim.

_MYSQL_DEFAULTS = {
    "autocommit": "ON", "auto_increment_increment": "1", "auto_increment_offset": "1", "binlog_format": "ROW", "character_set_client": "utf8mb4",
    "character_set_connection": "utf8mb4", "character_set_database": "utf8mb4", "character_set_server": "utf8mb4", "collation_server": "utf8mb4_0900_ai_ci",
    "connect_timeout": "10", "default_storage_engine": "InnoDB", "foreign_key_checks": "ON", "innodb_buffer_pool_size": "134217728",
    "innodb_flush_log_at_trx_commit": "1", "innodb_file_per_table": "ON", "innodb_lock_wait_timeout": "50", "innodb_log_file_size": "50331648",
    "join_buffer_size": "262144", "long_query_time": "10.000000", "lower_case_table_names": "0", "max_allowed_packet": "67108864",
    "max_connections": "151", "net_read_timeout": "30", "net_write_timeout": "60", "slow_query_log": "OFF", "sort_buffer_size": "262144",
    "sql_mode": "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION",
    "table_open_cache": "4000", "thread_cache_size": "9", "time_zone": "SYSTEM", "tmp_table_size": "16777216", "wait_timeout": "28800",
}
_M1330 = re.compile(r"^-- (\d+) rows at their default value omitted$", A)
_PG_NEED = ("name", "setting", "source", "boot_val")


def _cells(line: str) -> list[str]:
    body = line[1:-1] if line.startswith("|") and line.endswith("|") and len(line) >= 2 else line
    return [c.strip(" ") for c in body.split("|")]


def _is_sep(line: str) -> bool:
    return bool(line) and line.count("-") >= 3 and not line.strip("-+| ")


def _settings_header(lines: list[str]):
    for i, l in enumerate(lines):
        c = _cells(l)
        if all(k in c for k in _PG_NEED):
            return i, "pg", c
        if c == ["Variable_name", "Value"]:
            return i, "mysql", c
    return None


def _default_row(kind: str, cols: list[str], cells: list[str]) -> bool:
    if kind == "pg":
        return cells[cols.index("source")] == "default" and cells[cols.index("setting")] == cells[cols.index("boot_val")]
    return _MYSQL_DEFAULTS.get(cells[0]) == cells[1]


def prune_settings(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M1330.match(l) for l in lines):
        return text
    h = _settings_header(lines)
    if not h:
        return text
    i0, kind, cols = h
    out = lines[: i0 + 1]
    dropped, j, last_row = 0, i0 + 1, i0
    while j < len(lines):
        l = lines[j]
        if _is_sep(l):
            out.append(l)
        else:
            c = _cells(l)
            if len(c) != len(cols) or l.startswith("(") and l.endswith(")"):
                break
            if _default_row(kind, cols, c):
                dropped += 1
            else:
                out.append(l)
                last_row = len(out) - 1
        j += 1
    if not dropped:
        return text
    # the note goes after the last kept row (before a closing border or the footer)
    out.insert(last_row + 1, f"-- {dropped} rows at their default value omitted")
    return gate(text, join_nl(out + lines[j:], nl))


def probes_1330(text: str) -> dict:
    lines, _ = split_nl(text)
    n = len(lines)
    head = None
    for i in range(n):
        body = lines[i]
        if body.startswith("|") and body.endswith("|") and len(body) >= 2:
            body = body[1:-1]
        cs = [c.strip(" ") for c in body.split("|")]
        if set(_PG_NEED) <= set(cs):
            head = (i, "pg", cs)
            break
        if len(cs) == 2 and cs[0] == "Variable_name" and cs[1] == "Value":
            head = (i, "mysql", cs)
            break
    if head is None:
        return {"settings table": None, "lines": lines}
    i0, kind, cols = head
    kept, omitted, other, total_rows = [], 0, [], 0
    for i in range(n):
        l = lines[i]
        if i <= i0:
            other.append(l)
            continue
        m = _M1330.match(l)
        if m:
            omitted += int(m.group(1))
            continue
        body = l[1:-1] if l.startswith("|") and l.endswith("|") and len(l) >= 2 else l
        cs = [c.strip(" ") for c in body.split("|")]
        sep = bool(l) and l.count("-") >= 3 and all(ch in "-+| " for ch in l)
        if len(cs) == len(cols) and not sep and not (l.startswith("(") and l.endswith(")")):
            if kind == "pg":
                default = cs[cols.index("source")] == "default" and cs[cols.index("setting")] == cs[cols.index("boot_val")]
            else:
                default = _MYSQL_DEFAULTS.get(cs[0]) == cs[1]
            if default:
                omitted += 1
            else:
                kept.append(tuple(cs))
        else:
            other.append(l)
    return {
        "columns": cols,
        "rows that are not at their default (every column)": kept,
        "rows at their default (counted, not listed)": omitted,
        "total rows": len(kept) + omitted,
        "header, separators, footer and any other line": other,
    }


_PG_COLS = ["name", "setting", "unit", "category", "short_desc", "extra_desc", "context", "vartype", "source", "min_val", "max_val", "enumvals", "boot_val", "reset_val", "sourcefile", "sourceline", "pending_restart"]
# name, setting, unit, category, short_desc, context, vartype, source, boot_val  (the rest default to empty); reconstructed from memory of pg_settings
_PG_ROWS = [
    ("allow_alter_system", "on", "", "Compatibility", "Allows running the ALTER SYSTEM command.", "sighup", "bool", "default", "on"),
    ("application_name", "psql", "", "Reporting and Logging / What to Log", "Sets the application name to be reported in statistics and logs.", "user", "string", "client", ""),
    ("autovacuum", "on", "", "Autovacuum", "Starts the autovacuum subprocess.", "sighup", "bool", "default", "on"),
    ("autovacuum_naptime", "15", "s", "Autovacuum", "Time to sleep between autovacuum runs.", "sighup", "integer", "configuration file", "60"),
    ("autovacuum_vacuum_scale_factor", "0.02", "", "Autovacuum", "Number of tuple updates or deletes prior to vacuum as a fraction of reltuples.", "sighup", "real", "configuration file", "0.2"),
    ("bgwriter_delay", "200", "ms", "Resource Usage / Background Writer", "Background writer sleep time between rounds.", "sighup", "integer", "default", "200"),
    ("block_size", "8192", "", "Preset Options", "Shows the size of a disk block.", "internal", "integer", "default", "8192"),
    ("checkpoint_completion_target", "0.9", "", "Write-Ahead Log / Checkpoints", "Time spent flushing dirty buffers during checkpoint, as fraction of checkpoint interval.", "sighup", "real", "configuration file", "0.9"),
    ("checkpoint_timeout", "300", "s", "Write-Ahead Log / Checkpoints", "Sets the maximum time between automatic WAL checkpoints.", "sighup", "integer", "default", "300"),
    ("client_encoding", "UTF8", "", "Client Connection Defaults / Locale and Formatting", "Sets the client's character set encoding.", "user", "string", "client", "SQL_ASCII"),
    ("config_file", "/etc/postgresql/16/main/postgresql.conf", "", "File Locations", "Sets the server's main configuration file.", "postmaster", "string", "override", ""),
    ("cpu_tuple_cost", "0.01", "", "Query Tuning / Planner Cost Constants", "Sets the planner's estimate of the cost of processing each tuple (row).", "user", "real", "default", "0.01"),
    ("data_directory", "/var/lib/postgresql/16/main", "", "File Locations", "Sets the server's data directory.", "postmaster", "string", "override", ""),
    ("datestyle", "ISO, MDY", "", "Client Connection Defaults / Locale and Formatting", "Sets the display format for date and time values.", "user", "string", "configuration file", "ISO, MDY"),
    ("deadlock_timeout", "1000", "ms", "Lock Management", "Sets the time to wait on a lock before checking for deadlock.", "superuser", "integer", "default", "1000"),
    ("default_statistics_target", "100", "", "Query Tuning / Other Planner Options", "Sets the default statistics target.", "user", "integer", "default", "100"),
    ("effective_cache_size", "786432", "8kB", "Query Tuning / Planner Cost Constants", "Sets the planner's assumption about the total size of the data caches.", "user", "integer", "configuration file", "524288"),
    ("effective_io_concurrency", "200", "", "Resource Usage / Asynchronous Behavior", "Number of simultaneous requests that can be handled efficiently by the disk subsystem.", "user", "integer", "configuration file", "1"),
    ("enable_bitmapscan", "on", "", "Query Tuning / Planner Method Configuration", "Enables the planner's use of bitmap-scan plans.", "user", "bool", "default", "on"),
    ("enable_hashjoin", "on", "", "Query Tuning / Planner Method Configuration", "Enables the planner's use of hash join plans.", "user", "bool", "default", "on"),
    ("enable_seqscan", "on", "", "Query Tuning / Planner Method Configuration", "Enables the planner's use of sequential-scan plans.", "user", "bool", "default", "on"),
    ("fsync", "on", "", "Write-Ahead Log / Settings", "Forces synchronization of updates to disk.", "sighup", "bool", "default", "on"),
    ("full_page_writes", "on", "", "Write-Ahead Log / Settings", "Writes full pages to WAL when first modified after a checkpoint.", "sighup", "bool", "default", "on"),
    ("hba_file", "/etc/postgresql/16/main/pg_hba.conf", "", "File Locations", "Sets the server's \"hba\" configuration file.", "postmaster", "string", "override", ""),
    ("huge_pages", "try", "", "Resource Usage / Memory", "Use of huge pages on Linux or Windows.", "postmaster", "enum", "default", "try"),
    ("idle_in_transaction_session_timeout", "0", "ms", "Client Connection Defaults / Statement Behavior", "Sets the maximum allowed idle time between queries, when in a transaction.", "user", "integer", "default", "0"),
    ("jit", "on", "", "Query Tuning / Other Planner Options", "Allow JIT compilation.", "user", "bool", "default", "on"),
    ("lc_messages", "C.UTF-8", "", "Client Connection Defaults / Locale and Formatting", "Sets the language in which messages are displayed.", "superuser", "string", "configuration file", ""),
    ("listen_addresses", "*", "", "Connections and Authentication / Connection Settings", "Sets the host name or IP address(es) to listen to.", "postmaster", "string", "configuration file", "localhost"),
    ("log_checkpoints", "on", "", "Reporting and Logging / What to Log", "Logs each checkpoint.", "sighup", "bool", "default", "on"),
    ("log_min_duration_statement", "250", "ms", "Reporting and Logging / When to Log", "Sets the minimum execution time above which all statements will be logged.", "superuser", "integer", "configuration file", "-1"),
    ("log_statement", "none", "", "Reporting and Logging / What to Log", "Sets the type of statements logged.", "superuser", "enum", "default", "none"),
    ("maintenance_work_mem", "262144", "kB", "Resource Usage / Memory", "Sets the maximum memory to be used for maintenance operations.", "user", "integer", "configuration file", "65536"),
    ("max_connections", "300", "", "Connections and Authentication / Connection Settings", "Sets the maximum number of concurrent connections.", "postmaster", "integer", "configuration file", "100"),
    ("max_locks_per_transaction", "64", "", "Lock Management", "Sets the maximum number of locks per transaction.", "postmaster", "integer", "default", "64"),
    ("max_parallel_workers", "8", "", "Resource Usage / Asynchronous Behavior", "Sets the maximum number of parallel workers that can be active at one time.", "user", "integer", "default", "8"),
    ("max_wal_size", "4096", "MB", "Write-Ahead Log / Checkpoints", "Sets the WAL size that triggers a checkpoint.", "sighup", "integer", "configuration file", "1024"),
    ("min_wal_size", "80", "MB", "Write-Ahead Log / Checkpoints", "Sets the minimum size to shrink the WAL to.", "sighup", "integer", "default", "80"),
    ("port", "5432", "", "Connections and Authentication / Connection Settings", "Sets the TCP port the server listens on.", "postmaster", "integer", "configuration file", "5432"),
    ("random_page_cost", "1.1", "", "Query Tuning / Planner Cost Constants", "Sets the planner's estimate of the cost of a nonsequentially fetched disk page.", "user", "real", "configuration file", "4"),
    ("search_path", "\"$user\", public", "", "Client Connection Defaults / Statement Behavior", "Sets the schema search order for names that are not schema-qualified.", "user", "string", "default", "\"$user\", public"),
    ("server_version", "16.2 (Ubuntu 16.2-1.pgdg22.04+1)", "", "Preset Options", "Shows the server version.", "internal", "string", "default", "16.2 (Ubuntu 16.2-1.pgdg22.04+1)"),
    ("shared_buffers", "262144", "8kB", "Resource Usage / Memory", "Sets the number of shared memory buffers used by the server.", "postmaster", "integer", "configuration file", "16384"),
    ("shared_preload_libraries", "pg_stat_statements", "", "Client Connection Defaults / Shared Library Preloading", "Lists shared libraries to preload into server.", "postmaster", "string", "configuration file", ""),
    ("ssl", "off", "", "Connections and Authentication / SSL", "Enables SSL connections.", "sighup", "bool", "default", "off"),
    ("statement_timeout", "0", "ms", "Client Connection Defaults / Statement Behavior", "Sets the maximum allowed duration of any statement.", "user", "integer", "default", "0"),
    ("synchronous_commit", "on", "", "Write-Ahead Log / Settings", "Sets the current transaction's synchronization level.", "user", "enum", "default", "on"),
    ("temp_buffers", "1024", "8kB", "Resource Usage / Memory", "Sets the maximum number of temporary buffers used by each session.", "user", "integer", "default", "1024"),
    ("TimeZone", "Etc/UTC", "", "Client Connection Defaults / Locale and Formatting", "Sets the time zone for displaying and interpreting time stamps.", "user", "string", "configuration file", "GMT"),
    ("track_activities", "on", "", "Statistics / Cumulative Query and Index Statistics", "Collects information about executing commands.", "superuser", "bool", "default", "on"),
    ("wal_buffers", "2048", "8kB", "Write-Ahead Log / Settings", "Sets the number of disk-page buffers in shared memory for WAL.", "postmaster", "integer", "override", "-1"),
    ("wal_level", "logical", "", "Write-Ahead Log / Settings", "Sets the level of information written to the WAL.", "postmaster", "enum", "configuration file", "replica"),
    ("work_mem", "32768", "kB", "Resource Usage / Memory", "Sets the maximum memory to be used for query workspaces.", "user", "integer", "configuration file", "4096"),
    ("zero_damaged_pages", "off", "", "Developer Options", "Continues processing past damaged page headers.", "superuser", "bool", "default", "off"),
]

# More real parameter names at their defaults, so the dump has the ~85% default share of a real instance. 'name value unit' per
# entry; category, description and bool/integer/real/enum types are generic (reconstructed from memory, not copied from a server).
_PG_PAD = """archive_mode=off archive_timeout=0:s array_nulls=on authentication_timeout=60:s autovacuum_analyze_scale_factor=0.1 autovacuum_analyze_threshold=50
autovacuum_freeze_max_age=200000000 autovacuum_max_workers=3 autovacuum_vacuum_cost_delay=2:ms autovacuum_vacuum_threshold=50 backend_flush_after=0:8kB
bgwriter_lru_maxpages=100 bgwriter_lru_multiplier=2 bytea_output=hex check_function_bodies=on checkpoint_flush_after=32:8kB checkpoint_warning=30:s
commit_delay=0 commit_siblings=5 constraint_exclusion=partition cursor_tuple_fraction=0.1 default_table_access_method=heap default_transaction_isolation=read_committed
default_transaction_read_only=off dynamic_shared_memory_type=posix enable_async_append=on enable_gathermerge=on enable_hashagg=on enable_incremental_sort=on
enable_indexonlyscan=on enable_indexscan=on enable_material=on enable_memoize=on enable_mergejoin=on enable_nestloop=on enable_parallel_append=on
enable_parallel_hash=on enable_partition_pruning=on enable_partitionwise_join=off enable_sort=on enable_tidscan=on escape_string_warning=on exit_on_error=off
extra_float_digits=1 from_collapse_limit=8 geqo=on geqo_effort=5 geqo_generations=0 geqo_pool_size=0 geqo_seed=0 geqo_selection_bias=2 geqo_threshold=12
gin_fuzzy_search_limit=0 gin_pending_list_limit=4096:kB hash_mem_multiplier=2 hot_standby=on hot_standby_feedback=off ignore_checksum_failure=off
ignore_invalid_pages=off join_collapse_limit=8 lo_compat_privileges=off lock_timeout=0:ms log_autovacuum_min_duration=600000:ms log_connections=off
log_disconnections=off log_duration=off log_error_verbosity=default log_hostname=off log_lock_waits=off log_min_error_statement=error log_min_messages=warning
log_parameter_max_length=-1:B log_replication_commands=off log_rotation_age=1440:min log_rotation_size=10240:kB log_temp_files=-1:kB log_truncate_on_rotation=off
logical_decoding_work_mem=65536:kB maintenance_io_concurrency=10 max_files_per_process=1000 max_pred_locks_per_transaction=64 max_prepared_transactions=0
max_replication_slots=10 max_stack_depth=2048:kB max_standby_archive_delay=30000:ms max_sync_workers_per_subscription=2 max_wal_senders=10 max_worker_processes=8
min_parallel_index_scan_size=64:8kB min_parallel_table_scan_size=1024:8kB parallel_setup_cost=1000 parallel_tuple_cost=0.1 password_encryption=scram-sha-256
plan_cache_mode=auto quote_all_identifiers=off row_security=on seq_page_cost=1 session_replication_role=origin standard_conforming_strings=on
synchronize_seqscans=on tcp_keepalives_count=0 tcp_keepalives_idle=0:s temp_file_limit=-1:kB track_counts=on track_functions=none track_io_timing=off
transform_null_equals=off unix_socket_permissions=0777 vacuum_cost_delay=0:ms vacuum_cost_limit=200 vacuum_cost_page_hit=1 wal_compression=off wal_init_zero=on
wal_keep_size=0:MB wal_log_hints=off wal_recycle=on wal_sender_timeout=60000:ms xmlbinary=base64 xmloption=content""".split()
_PG_ROWS = sorted(_PG_ROWS + [(n, v.replace("_", " "), u, "Resource Usage / Misc", f"Sets the {n.replace('_', ' ')}.", "user", "bool" if v in ("on", "off") else "integer", "default", v.replace("_", " "))
                              for n, _, rest in (e.partition("=") for e in _PG_PAD) for v, _, u in (rest.partition(":"),)], key=lambda r: r[0].lower())


def _psql_aligned(cols, rows) -> str:
    w = [max(len(c), *(len(r[k]) for r in rows)) for k, c in enumerate(cols)]
    out = ["|".join(" " + c.center(w[k]) + " " for k, c in enumerate(cols)).rstrip(),
           "+".join("-" * (w[k] + 2) for k in range(len(cols)))]
    for r in rows:
        out.append("|".join(" " + (r[k].ljust(w[k]) if k < len(cols) - 1 else r[k]) + ("" if k == len(cols) - 1 else " ") for k in range(len(cols))))
    out.append(f"({len(rows)} rows)")
    return "\n".join(out) + "\n"


def _pg_table(rows=_PG_ROWS, cols=_PG_COLS) -> str:
    full = []
    for name, setting, unit, cat, desc, ctx, vt, src, boot in rows:
        sf = "/etc/postgresql/16/main/postgresql.conf" if src == "configuration file" else ""
        sl = str(60 + len(name)) if sf else ""
        full.append([name, setting, unit, cat, desc, "", ctx, vt, src, "", "", "", boot, setting if src != "client" else boot, sf, sl, "f"])
    keep = [cols.index(c) for c in cols]
    return _psql_aligned(cols, [[r[k] for k in keep] for r in full])


def _mysql_vars() -> str:
    rows = [(k, v) for k, v in _MYSQL_DEFAULTS.items()]
    over = {"innodb_buffer_pool_size": "4294967296", "max_connections": "500", "innodb_flush_log_at_trx_commit": "2", "slow_query_log": "ON", "long_query_time": "0.500000"}
    rows = [(k, over.get(k, v)) for k, v in rows]
    rows += [("version", "8.0.36"), ("version_comment", "MySQL Community Server - GPL"), ("datadir", "/var/lib/mysql/"), ("hostname", "db1"), ("port", "3306")]
    rows.sort()
    w0, w1 = max(len(k) for k, _ in rows), max(len(v) for _, v in rows)
    bar = f"+-{'-' * w0}-+-{'-' * w1}-+"
    out = [bar, f"| {'Variable_name'.ljust(w0)} | {'Value'.ljust(w1)} |", bar] + [f"| {k.ljust(w0)} | {v.ljust(w1)} |" for k, v in rows] + [bar, f"{len(rows)} rows in set (0.01 sec)"]
    return "\n".join(out) + "\n"


case(
    1330,
    claim="In 'SELECT * FROM pg_settings' output a row with source=default and setting=boot_val is dropped, in MySQL 'SHOW VARIABLES' a row equal to a known default is dropped (unknown names are kept); header, separators, footer, every other row with all its columns and the count of omitted rows (one note line) are unchanged.",
    loses="the name, description and category of every setting that is at its default (only their count survives), and the column padding is not re-aligned",
    encode=prune_settings,
    probes=probes_1330,
    fixtures=lambda: [
        Fixture("pg_settings full dump (~165 rows): tuned settings at the top, in the middle and at the end", _pg_table(), True),
        Fixture("SHOW VARIABLES (mysql table form): 5 tuned of 35, the rest at a known default", _mysql_vars(), True),
        Fixture("pg_settings with only the 6 cheap columns (no source/boot_val): cannot tell defaults apart", _psql_aligned(["name", "setting", "unit"], [[r[0], r[1], r[2]] for r in _PG_ROWS[:8]]), False),
        Fixture("pg_settings where every row was changed", _pg_table([r[:7] + ("configuration file", "x") for r in _PG_ROWS[:6]]), False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #415 pg_dump and mysqldump boilerplate: drop the SET preamble, OWNER TO lines, banner comments, mysqldump pragmas, LOCK /
# UNLOCK TABLES and DROP TABLE IF EXISTS; hoist ENGINE/CHARSET options shared by every CREATE TABLE; merge consecutive
# one-row INSERTs with the same column list into one multi-row INSERT. CREATE TABLE blocks (names, columns), every
# INSERT value tuple, COPY data, indexes, constraints and every other line are kept.

_DUMP_DROP = re.compile(
    r"^(?:SET (?:statement_timeout|lock_timeout|idle_in_transaction_session_timeout|client_encoding|standard_conforming_strings|check_function_bodies|xmloption|client_min_messages|row_security|default_tablespace|default_table_access_method) = .*;"
    r"|SELECT pg_catalog\.set_config\('search_path', '', false\);"
    r"|ALTER [A-Z ]+ [^ ]+ OWNER TO [^ ]+;"
    r"|/\*!\d{5} .* \*/;"
    r"|LOCK TABLES .* WRITE;|UNLOCK TABLES;|DROP TABLE IF EXISTS .*;"
    r"|--|-- (?:Name: |Data for Name: |TOC entry |Table structure for table |Dumping data for table |Completed on |Dump completed on |PostgreSQL database dump|MySQL dump |Host: |Server version|-{10,}).*)$", A)
_DUMP_INS = re.compile(r"^(INSERT INTO .*? VALUES )(\(.*\));$", A)
_DUMP_OPTS = re.compile(r"^\) (ENGINE=[^;]*);$", A)
_DUMP_HOIST = "-- table options (default, shown only where different): "


def _split_tuples(s: str, backslash: bool):
    """Top-level '(...)' tuples of 'a),(b' text; None unless s is exactly tuples separated by commas. Quote-aware."""
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] != "(":
            return None
        depth, j, q = 0, i, False
        while j < n:
            ch = s[j]
            if q:
                if backslash and ch == "\\":
                    j += 2
                    continue
                if ch == "'":
                    if s[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    q = False
            elif ch == "'":
                q = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= n or depth != 0:
            return None
        out.append(s[i:j + 1])
        i = j + 1
        if i < n:
            if s[i] != ",":
                return None
            i += 1
            if i >= n:
                return None
    return out


def compact_dump(text: str) -> str:
    lines, nl = split_nl(text)
    if any(l.startswith(_DUMP_HOIST) for l in lines):
        return text
    ends = [(i, _DUMP_OPTS.match(l)) for i, l in enumerate(lines) if l.startswith(")")]
    creates = [i for i, l in enumerate(lines) if l.startswith("CREATE TABLE ")]
    hoist = None
    if len(creates) >= 2:
        # every CREATE TABLE must end in ') ENGINE=...;' before the next CREATE TABLE
        opts = []
        for k, c in enumerate(creates):
            nxt = creates[k + 1] if k + 1 < len(creates) else len(lines)
            e = next((m for i, m in ends if c < i < nxt and lines[i].endswith(";")), None)
            opts.append(e.group(1) if e else None)
        if all(opts):
            best, cnt = Counter(opts).most_common(1)[0]
            if cnt >= 2:
                hoist = best
    out: list[str] = []
    blank = False
    pending = None  # (prefix, [tuple texts]) for consecutive one-row INSERTs
    in_table = False

    def flush():
        nonlocal pending
        if pending:
            out.append(pending[0] + ",".join(pending[1]) + ";")
            pending = None

    for l in lines:
        if hoist and l.startswith("CREATE TABLE ") and not any(o.startswith(_DUMP_HOIST) for o in out):
            flush()
            out.append(_DUMP_HOIST + hoist)
        if in_table:
            if l.startswith(")") and l.endswith(";"):
                in_table = False
                m = _DUMP_OPTS.match(l)
                out.append(");" if hoist and m and m.group(1) == hoist else l)
            else:
                out.append(l)
            continue
        if l.startswith("CREATE TABLE "):
            flush()
            in_table = True
            out.append(l)
            blank = False
            continue
        if _DUMP_DROP.match(l):
            continue
        if l.strip(" ") == "":
            flush()
            if not blank:
                out.append(l)
            blank = True
            continue
        blank = False
        m = _DUMP_INS.match(l)
        if m:
            prefix, row = m.group(1), m.group(2)
            ok = _split_tuples(row, "`" in prefix) is not None and len(_split_tuples(row, "`" in prefix)) == 1
            if ok and pending and pending[0] == prefix:
                pending[1].append(row)
                continue
            flush()
            if ok:
                pending = (prefix, [row])
                continue
        flush()
        out.append(l)
    flush()
    return gate(text, join_nl(out, nl))


_DROP_PREFIXES = ("-- Name: ", "-- Data for Name: ", "-- TOC entry ", "-- Table structure for table ", "-- Dumping data for table ", "-- Completed on ", "-- Dump completed on ",
                  "-- PostgreSQL database dump", "-- MySQL dump ", "-- Host: ", "-- Server version")
_SET_NAMES = ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "client_encoding", "standard_conforming_strings", "check_function_bodies", "xmloption",
              "client_min_messages", "row_security", "default_tablespace", "default_table_access_method")


def _is_boilerplate(l: str) -> bool:
    """Probe-side classification of the lines the rule is allowed to drop (startswith / endswith tests, no regex)."""
    if l == "--" or (l.startswith("-- ") and (l[3:].startswith("-" * 10) or l.startswith(_DROP_PREFIXES))):
        return True
    if l.startswith("SET ") and l.endswith(";") and " = " in l and l[4:].partition(" = ")[0] in _SET_NAMES:
        return True
    if l == "SELECT pg_catalog.set_config('search_path', '', false);" or l == "UNLOCK TABLES;":
        return True
    if l.startswith("ALTER ") and l.endswith(";") and " OWNER TO " in l:
        head, _, role = l[:-1].rpartition(" OWNER TO ")
        words = head.split(" ")
        return len(words) >= 3 and all(w.isascii() and w.isupper() and w.isalpha() for w in words[1:-1]) and " " not in role and bool(role) and bool(words[-1])
    if l.startswith("/*!") and l.endswith(" */;") and isdigits(l[3:8]) and l[8:9] == " ":
        return True
    if l.startswith("LOCK TABLES ") and l.endswith(" WRITE;"):
        return True
    return l.startswith("DROP TABLE IF EXISTS ") and l.endswith(";")


def probes_415(text: str) -> dict:
    lines, _ = split_nl(text)
    n = len(lines)
    hoisted = None
    tables, inserts, other = [], [], []
    i = 0
    while i < n:
        l = lines[i]
        if l.startswith(_DUMP_HOIST):
            hoisted = l[len(_DUMP_HOIST):]
            i += 1
            continue
        if l.startswith("CREATE TABLE "):
            j = i + 1
            while j < n and not (lines[j].startswith(")") and lines[j].endswith(";")):
                j += 1
            body = lines[i:j]
            end = lines[j] if j < n else None
            if end is None:
                opts = "unterminated"
            elif end.startswith(") ENGINE=") and end.endswith(";"):
                opts = end[2:-1]
            elif end == ");":
                opts = hoisted
            else:
                opts = end
            tables.append((tuple(body), opts))
            i = j + 1
            continue
        if _is_boilerplate(l) or l.strip(" ") == "":
            i += 1
            continue
        if l.startswith("INSERT INTO ") and l.endswith(");") and " VALUES (" in l:
            prefix, _, rest = l[:-1].partition(" VALUES (")
            rows = _split_tuples("(" + rest, "`" in prefix)
            if rows is not None:
                inserts += [(prefix, r) for r in rows]
                i += 1
                continue
        other.append(l)
        i += 1
    return {
        "tables: CREATE TABLE lines up to the closing paren, and the effective ENGINE/CHARSET options": tables,
        "every inserted row, in order, with its INSERT INTO ... column list": inserts,
        "every other line: indexes, constraints, COPY data, version comments, errors": other,
    }


def _pg_dump(with_error: bool = False) -> str:
    out = ["--", "-- PostgreSQL database dump", "--", "", "-- Dumped from database version 16.2 (Ubuntu 16.2-1.pgdg22.04+1)", "-- Dumped by pg_dump version 16.2 (Ubuntu 16.2-1.pgdg22.04+1)", "",
           "SET statement_timeout = 0;", "SET lock_timeout = 0;", "SET idle_in_transaction_session_timeout = 0;", "SET client_encoding = 'UTF8';", "SET standard_conforming_strings = on;",
           "SELECT pg_catalog.set_config('search_path', '', false);", "SET check_function_bodies = false;", "SET xmloption = content;", "SET client_min_messages = warning;", "SET row_security = off;", "",
           "SET default_tablespace = '';", "", "SET default_table_access_method = heap;", ""]
    tables = {"customers": ["id integer NOT NULL", "email character varying(255) NOT NULL", "created_at timestamp with time zone DEFAULT now()"],
              "orders": ["id integer NOT NULL", "customer_id integer NOT NULL", "total_cents bigint NOT NULL", "status text DEFAULT 'new'::text"],
              "audit_log": ["id bigint NOT NULL", "actor text", "action text NOT NULL"]}
    for k, (t, cols) in enumerate(tables.items()):
        out += ["--", f"-- Name: {t}; Type: TABLE; Schema: public; Owner: app", "--", "", f"CREATE TABLE public.{t} (", ",\n".join("    " + c for c in cols), ");", "", "", f"ALTER TABLE public.{t} OWNER TO app;", ""]
    for t, rows in (("customers", [(1, "ann@example.com"), (2, "bob@example.com"), (3, "o'brien@example.com"), (4, "d@example.com")]), ("orders", [(1, 1, 2599, "paid"), (2, 1, 1200, "new"), (3, 3, 99, "refunded")])):
        out += ["--", f"-- Data for Name: {t}; Type: TABLE DATA; Schema: public; Owner: app", "--", ""]
        cols = {"customers": "(id, email)", "orders": "(id, customer_id, total_cents, status)"}[t]
        for r in rows:
            out.append(f"INSERT INTO public.{t} {cols} VALUES ({', '.join(chr(39) + v.replace(chr(39), chr(39) * 2) + chr(39) if isinstance(v, str) else str(v) for v in r)});")
        out += ["", ""]
    out += ["--", "-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: app", "--", "", "ALTER TABLE ONLY public.customers", "    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);", "",
            "--", "-- Name: orders orders_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app", "--", "", "ALTER TABLE ONLY public.orders", "    ADD CONSTRAINT orders_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);", "",
            "--", "-- PostgreSQL database dump complete", "--", ""]
    if with_error:
        out = ["pg_dump: error: query failed: ERROR:  permission denied for table audit_log", "pg_dump: error: query was: LOCK TABLE public.audit_log IN ACCESS SHARE MODE"] + out + ["pg_dump: warning: errors ignored on restore: 1"]
    return "\n".join(out) + "\n"


def _mysqldump() -> str:
    out = ["-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)", "--", "-- Host: localhost    Database: shop", "-- ------------------------------------------------------", "-- Server version\t8.0.36", "",
           "/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;", "/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;", "/*!50503 SET NAMES utf8mb4 */;", "/*!40103 SET TIME_ZONE='+00:00' */;", ""]
    opts = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
    for t, cols, rows in (("categories", ["`id` int NOT NULL AUTO_INCREMENT", "`name` varchar(80) NOT NULL", "PRIMARY KEY (`id`)"], [(1, "tools"), (2, "toys"), (3, "it's odd"), (4, "books")]),
                          ("products", ["`id` int NOT NULL AUTO_INCREMENT", "`category_id` int NOT NULL", "`sku` varchar(32) NOT NULL", "`price` decimal(10,2) NOT NULL", "PRIMARY KEY (`id`)", "KEY `category_id` (`category_id`)"], [(1, 1, "H-100", "9.50"), (2, 2, "T-7", "3.25"), (3, 2, "T-8", "4.00")]),
                          ("sessions", ["`id` char(36) NOT NULL", "`data` text", "PRIMARY KEY (`id`)"], [("a1", "x"), ("b2", "y")])):
        out += ["--", f"-- Table structure for table `{t}`", "--", "", f"DROP TABLE IF EXISTS `{t}`;", "/*!40101 SET @saved_cs_client     = @@character_set_client */;", "/*!50503 SET character_set_client = utf8mb4 */;",
                f"CREATE TABLE `{t}` (", ",\n".join("  " + c for c in cols), f") {opts if t != 'sessions' else 'ENGINE=MyISAM DEFAULT CHARSET=latin1'};", "/*!40101 SET character_set_client = @saved_cs_client */;", "",
                "--", f"-- Dumping data for table `{t}`", "--", "", f"LOCK TABLES `{t}` WRITE;", f"/*!40000 ALTER TABLE `{t}` DISABLE KEYS */;"]
        for r in rows:
            out.append(f"INSERT INTO `{t}` VALUES ({','.join(str(v) if not isinstance(v, str) or v.replace('.', '').isdigit() else chr(39) + v.replace(chr(39), chr(39) * 2) + chr(39) for v in r)});")
        out += [f"/*!40000 ALTER TABLE `{t}` ENABLE KEYS */;", "UNLOCK TABLES;", ""]
    out += ["/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;", "", "-- Dump completed on 2024-03-05 10:15:01"]
    return "\n".join(out) + "\n"


case(
    415,
    claim="pg_dump and mysqldump text loses its SET preamble, OWNER TO lines, '-- Name: ...' banner comments, '/*!40101 ... */' pragmas, LOCK/UNLOCK TABLES and DROP TABLE IF EXISTS lines; ENGINE/CHARSET options shared by every CREATE TABLE are stated once; consecutive one-row INSERTs with the same column list merge into one multi-row INSERT; every CREATE TABLE block (table and column names), each inserted row with its column list, COPY data, constraints, indexes and any error line are unchanged.",
    loses="the dropped preamble, owner, banner and pragma lines, DROP TABLE IF EXISTS statements, the repeated ENGINE/CHARSET clause on each table, blank-line runs (collapsed to one), and the one-statement-per-row INSERT layout",
    encode=compact_dump,
    probes=probes_415,
    fixtures=lambda: [
        Fixture("pg_dump --inserts of 3 tables with owners, constraints at the end", _pg_dump(), True),
        Fixture("failure variant: pg_dump error lines before and after the dump", _pg_dump(with_error=True), True),
        Fixture("mysqldump --skip-extended-insert of 3 tables (one MyISAM)", _mysqldump(), True),
        Fixture("schema-only SQL without boilerplate", "CREATE TABLE a (\n    id integer\n);\nCREATE INDEX a_idx ON a (id);\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #1163 Access logs: user-agent legend and dash-field removal. Turns out to be lossless, so it is a roundtrip case, not a rule.
#
# Combined-format lines are rewritten two ways. Form A, when ident and user are '-' and the referer is "-":
#   'IP [TS] "REQ" STATUS SIZE §k'   (the three placeholders and the user agent are gone from the line)
# Form B for every other well-formed line: the final "UA" becomes '§k'. Legend lines '§k UA' open the text. Each line is
# checked by decoding it again; one that would not round-trip stays verbatim. Input that already contains '§' is not
# encoded (a text that starts with '§' gets a '§raw' escape line), so a '§k' in the output always means a legend entry.

_COMBINED = re.compile(r'^([^ ]+) ([^ ]+) ([^ ]+) \[([^\]]+)\] ("[^"]*") (\d{3}) (\d+|-) ("[^"]*") "([^"]*)"$', A)
_FORM_A = re.compile(r'^([^ ]+) \[([^\]]+)\] ("[^"]*") (\d{3}) (\d+|-) §(\d+)$', A)
_FORM_B = re.compile(r"^(.*) §(\d+)$", A)
_LEGEND = re.compile(r"^§(\d+) (.*)$", A)
_RAW = "§raw\n"


def _unform(line: str, ua: dict) -> str:
    m = _FORM_A.match(line)
    if m and int(m.group(6)) in ua:
        return f'{m.group(1)} - - [{m.group(2)}] {m.group(3)} {m.group(4)} {m.group(5)} "-" "{ua[int(m.group(6))]}"'
    m = _FORM_B.match(line)
    if m and int(m.group(2)) in ua:
        return f'{m.group(1)} "{ua[int(m.group(2))]}"'
    return line


def pack_access(text: str) -> str:
    if text.startswith("§"):
        return _RAW + text
    if "§" in text:
        return text
    lines, nl = split_nl(text)
    ms = [_COMBINED.match(l) for l in lines]
    counts = Counter(m.group(9) for m in ms if m)
    legend = [u for u, c in counts.most_common() if c >= 2 and (len(u) + 2 - 4) * c > len(u) + 8 and "\n" not in u]
    if not legend:
        return text
    ids = {u: k for k, u in enumerate(legend, 1)}
    ua = {k: u for u, k in ids.items()}
    out = [f"§{k} {u}" for u, k in ids.items()]
    for l, m in zip(lines, ms):
        enc = l
        if m and m.group(9) in ids:
            k = ids[m.group(9)]
            if m.group(2) == "-" and m.group(3) == "-" and m.group(8) == '"-"':
                enc = f"{m.group(1)} [{m.group(4)}] {m.group(5)} {m.group(6)} {m.group(7)} §{k}"
            else:
                enc = f"{l[: m.start(9) - 2]} §{k}"
            if _unform(enc, ua) != l:
                enc = l
        out.append(enc)
    return gate(text, join_nl(out, nl))


def unpack_access(text: str) -> str:
    if text.startswith(_RAW):
        return text[len(_RAW):]
    lines, nl = split_nl(text)
    ua: dict = {}
    i = 0
    while i < len(lines) and (m := _LEGEND.match(lines[i])):
        ua[int(m.group(1))] = m.group(2)
        i += 1
    if not ua or i == 0:
        return text
    return join_nl([_unform(l, ua) for l in lines[i:]], nl)


_UAS = ["Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "curl/8.4.0"]


def _access_log(n: int = 60) -> str:
    rng = random.Random(1163)
    out = []
    for i in range(n):
        ip = f"203.0.113.{rng.randint(1, 60)}"
        ts = f"05/Mar/2024:10:{i // 60:02d}:{i % 60:02d} +0000"
        path = rng.choice(["/", "/index.html", "/static/app.js", "/api/items", "/api/items/17", "/login", "/healthz"])
        status = 502 if i in (0, 31, n - 1) else rng.choice([200, 200, 200, 304, 404])
        ua = _UAS[0] if i % 3 else rng.choice(_UAS)
        user, ref = ("-", '"-"')
        if i % 17 == 5:
            user, ref = "alice", '"https://shop.example.com/cart"'
        out.append(f'{ip} - {user} [{ts}] "GET {path} HTTP/1.1" {status} {rng.randint(0, 9000) or "-"} {ref} "{ua}"')
    return "\n".join(out) + "\n"


case(
    1163,
    claim="Combined-format access-log lines are rewritten with their user agent as a legend entry ('§k UA' once, then '§k' per line) and, when ident, user and referer are all '-', without those three placeholders; decode restores every byte, including lines with a real user or referer, malformed lines and text that already contains the sigil.",
    encode=pack_access,
    decode=unpack_access,
    fixtures=lambda: [
        Fixture("nginx combined log, 60 lines, 4 agents, 502s at the first, middle and last line, two lines with a user and referer", _access_log(), True),
        Fixture("lines with an extra field or a quote inside the agent stay verbatim", _access_log(30) + '203.0.113.9 - - [05/Mar/2024:10:59:00 +0000] "GET / HTTP/1.1" 200 5 "-" "weird \\"agent\\" x"\n203.0.113.9 - - [05/Mar/2024:10:59:01 +0000] "GET / HTTP/1.1" 200 5\n', True),
        Fixture("common log format without user agents", '203.0.113.5 - - [05/Mar/2024:10:15:01 +0000] "GET / HTTP/1.1" 200 1234\n' * 6, False),
        Fixture("text that starts with the sigil", "§raw\n" + _access_log(8), None),
        Fixture("sigil inside a path", _access_log(12) + '203.0.113.5 - - [05/Mar/2024:10:15:01 +0000] "GET /§1 HTTP/1.1" 200 1 "-" "curl/8.4.0"\n', None),
        Fixture("looks like form A already", "1.2.3.4 [05/Mar/2024:10:15:01 +0000] \"GET / HTTP/1.1\" 200 5 §1\n", None),
    ],
    min_gain=0.2,
    idempotent=False,  # a text that already contains '§' is escaped, so encoding the encoded text grows it by design
)


# ===============================================================================================================
# #1327 sklearn GridSearchCV(verbose=3): fold the per-fold START/END lines of a candidate into one line with its scores.
#
# A candidate is folded only when every fold has exactly one END line with a finite score in seconds and the same
# parameter text; its START and END lines (in any interleaving) are replaced, at the first of them, by
# '[CV c/N] params: scores s1 s2 ... mean m total Ts'. Candidates with a failed fold (score=nan), a missing fold or odd lines
# keep every line verbatim; joblib '[Parallel]' progress lines go except the final 'Done N out of N ... finished'.

_CV_END = re.compile(r"^\[CV (\d+)/(\d+); (\d+)/(\d+)\] END (.*);, score=(-?\d+\.\d+) total time= *(\d+\.\d)s$", A)
_CV_START = re.compile(r"^\[CV (\d+)/(\d+); (\d+)/(\d+)\] START (.*)$", A)
_PAR_DROP = re.compile(r"^\[Parallel\(n_jobs=-?\d+\)\]: (?:Using backend .*|Done +\d+ tasks +\| elapsed: +[\d.]+(?:s|min))$", A)
_M1327 = re.compile(r"^\[CV (\d+)/(\d+)\] (.*): scores ((?:-?\d+\.\d+ )+)mean (-?\d+\.\d{4}) total (\d+\.\d)s$", A)


def fold_cv(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M1327.match(l) for l in lines):
        return text
    ends: dict = defaultdict(list)  # candidate -> [(line index, fold, folds, params, score, tenths)]
    for i, l in enumerate(lines):
        m = _CV_END.match(l)
        if m:
            ends[(m.group(3), m.group(4))].append((i, int(m.group(1)), int(m.group(2)), m.group(5), m.group(6), int(m.group(7).replace(".", ""))))
    fold_of: dict = {}
    for cand, es in ends.items():
        folds = es[0][2]
        if len(es) == folds and sorted(e[1] for e in es) == list(range(1, folds + 1)) and {e[3] for e in es} == {es[0][3]} and all(e[2] == folds for e in es):
            fold_of[cand] = sorted(es, key=lambda e: e[1])
    out: list[str] = []
    emitted: set = set()
    for i, l in enumerate(lines):
        cand = None
        m = _CV_END.match(l) or _CV_START.match(l)
        if m:
            cand = (m.group(3), m.group(4))
        if cand in fold_of:
            if cand not in emitted:
                emitted.add(cand)
                es = fold_of[cand]
                scores = [e[4] for e in es]
                tenths = sum(e[5] for e in es)
                out.append(f"[CV {cand[0]}/{cand[1]}] {es[0][3]}: scores {' '.join(scores)} mean {statistics.fmean(float(s) for s in scores):.4f} total {tenths // 10}.{tenths % 10}s")
            continue
        if _PAR_DROP.match(l):
            continue
        out.append(l)
    return gate(text, join_nl(out, nl))


def _cv_events(lines: list[str]):
    """Probe-side: ('end', cand, fold, folds, params, score, tenths) / ('start', cand) / ('folded', ...) / ('other', line)."""
    ev = []
    for l in lines:
        if l.startswith("[CV ") and "] " in l:
            head, _, rest = l[4:].partition("] ")
            if ";" in head:  # '[CV f/F; c/N]' form
                fp, _, cp = head.partition("; ")
                f, _, F = fp.partition("/")
                c, _, N = cp.partition("/")
                if all(isdigits(x) for x in (f, F, c, N)):
                    if rest.startswith("END ") and ";, score=" in rest and rest.endswith("s") and " total time=" in rest:
                        body, _, tail = rest[4:].rpartition(";, score=")
                        sc, _, tm = tail.partition(" total time=")
                        tm = tm.strip(" ")[:-1]
                        a, dot, b = tm.partition(".")
                        s_ = sc[1:] if sc.startswith("-") else sc
                        s1, d2, s2 = s_.partition(".")
                        if dot and isdigits(a) and isdigits(b) and len(b) == 1 and d2 and isdigits(s1) and isdigits(s2) and tail.partition(" total time=")[2].lstrip(" ") == tm + "s" and (tail.partition(" total time=")[2][:1] in " 0123456789"):
                            ev.append(("end", (c, N), int(f), int(F), body, sc, int(a + b)))
                            continue
                    if rest.startswith("START "):
                        ev.append(("start", (c, N)))
                        continue
            elif "/" in head:  # folded form '[CV c/N] params: scores ... mean m total Ts'
                c, _, N = head.partition("/")
                body, sep, tail = rest.rpartition(": scores ")
                if sep and isdigits(c) and isdigits(N) and " mean " in tail and " total " in tail and tail.endswith("s"):
                    sc, _, r2 = tail.partition(" mean ")
                    mean, _, tot = r2.partition(" total ")
                    ev.append(("folded", (c, N), body, sc.split(" "), mean, tot))
                    continue
        ev.append(("other", l))
    return ev


def probes_1327(text: str) -> dict:
    lines, _ = split_nl(text)
    ev = _cv_events(lines)
    ends: dict = defaultdict(list)
    for e in ev:
        if e[0] == "end":
            ends[e[1]].append(e)
    foldable = {}
    for cand, es in ends.items():
        folds = es[0][3]
        if len(es) == folds and sorted(e[2] for e in es) == list(range(1, folds + 1)) and len({e[4] for e in es}) == 1 and all(e[3] == folds for e in es):
            foldable[cand] = sorted(es, key=lambda e: e[2])
    cands = {}
    for cand, es in foldable.items():
        scores = [e[5] for e in es]
        t = sum(e[6] for e in es)
        cands[cand] = (es[0][4], tuple(scores), "%.4f" % (sum(float(s) for s in scores) / len(scores)), f"{t // 10}.{t % 10}")
    other = []
    for l, e in zip(lines, _cv_events(lines)):  # events align one-to-one with lines
        if e[0] == "folded":
            cands[e[1]] = (e[2], tuple(x for x in e[3] if x), e[4], e[5][:-1])
        elif e[0] == "other" and not (l.startswith("[Parallel(n_jobs=") and _par_droppable(l)):
            other.append(l)
        elif e[0] in ("end", "start") and e[1] not in foldable:
            other.append(l)
    best = max(cands.items(), key=lambda kv: (float(kv[1][2]), -int(kv[0][0]))) if cands else None
    return {
        "per folded candidate (params, fold scores, mean, total seconds)": sorted((int(c[0]), int(c[1]), *v) for c, v in cands.items()),
        "best candidate (index, params, mean)": (best[0][0], best[1][0], best[1][2]) if best else None,
        "every other line: headers, failed or partial candidates, warnings, final progress line": other,
    }


def _par_droppable(l: str) -> bool:
    rest = l.partition("]: ")[2]
    if not l.startswith("[Parallel(n_jobs=") or not rest or not l[len("[Parallel(n_jobs="):].partition(")")[0].lstrip("-").isdigit():
        return False
    if rest.startswith("Using backend "):
        return True
    if rest.startswith("Done ") and " tasks " in rest and "| elapsed:" in rest:
        t = rest.partition("| elapsed:")[2].strip(" ")
        return (t.endswith("s") and t[:-1].replace(".", "", 1).isdigit()) or (t.endswith("min") and t[:-3].replace(".", "", 1).isdigit())
    return False


def _cv_log(folds: int = 5, cand: int = 24, parallel: bool = False, fail: tuple = ()) -> str:
    rng = random.Random(1327)
    grid = [(c, g, k) for k in ("rbf", "linear", "sigmoid") for c in (0.1, 1, 10, 100) for g in (0.01, 0.1)]
    out = [f"Fitting {folds} folds for each of {cand} candidates, totalling {folds * cand} fits"]
    if parallel:
        out.append("[Parallel(n_jobs=4)]: Using backend LokyBackend with 4 concurrent workers.")
    tasks = [(c, f) for c in range(1, cand + 1) for f in range(1, folds + 1)]
    for b in range(0, len(tasks), 4 if parallel else 1):
        batch = tasks[b:b + (4 if parallel else 1)]
        for c, f in batch:
            p = grid[c - 1]
            out.append(f"[CV {f}/{folds}; {c}/{cand}] START C={p[0]}, gamma={p[1]}, kernel={p[2]}" + "." * 30)
        order = list(batch)
        if parallel:
            rng.shuffle(order)
        for c, f in order:
            p = grid[c - 1]
            sc = "nan" if (c, f) in fail else f"{0.80 + 0.003 * c + rng.random() * 0.05 - (0.2 if c % 5 == 0 else 0):.3f}"
            out.append(f"[CV {f}/{folds}; {c}/{cand}] END C={p[0]}, gamma={p[1]}, kernel={p[2]};, score={sc} total time=   {rng.randint(0, 3) / 10:.1f}s")
        if parallel and (b // 4) % 8 == 7:
            out.append(f"[Parallel(n_jobs=4)]: Done {b + 4:3d} tasks      | elapsed:    {b / 20:.1f}s")
    if parallel:
        out.append(f"[Parallel(n_jobs=4)]: Done {folds * cand} out of {folds * cand} | elapsed:    5.2s finished")
    if fail:
        out += ["/usr/lib/python3/dist-packages/sklearn/model_selection/_validation.py:425: FitFailedWarning:", f"{len(fail)} fits failed out of a total of {folds * cand}.", "ValueError: Input contains NaN"]
    out.append("Best parameters: {'C': 10, 'gamma': 0.1, 'kernel': 'rbf'}")
    return "\n".join(out) + "\n"


case(
    1327,
    claim="For each GridSearchCV(verbose=3) candidate whose folds all finished with a finite score, the START/END lines (even interleaved by n_jobs) become '[CV c/N] params: scores s1..sF mean m total Ts'; per-candidate parameters, every fold score, the mean and the best candidate are unchanged, and a candidate with a nan or missing fold keeps all its lines, as do warnings and the final result lines.",
    loses="the START lines, per-fold times (only their sum), the fold-by-fold interleaving order, and joblib '[Parallel]' backend and 'Done N tasks' progress lines",
    encode=fold_cv,
    probes=probes_1327,
    fixtures=lambda: [
        Fixture("GridSearchCV(SVC, verbose=3), 24 candidates x 5 folds, sequential", _cv_log(), True),
        Fixture("n_jobs=4: START and END lines interleaved, joblib progress lines", _cv_log(parallel=True), True),
        Fixture("failure variant: fold 2 of candidate 1 and fold 5 of candidate 24 score nan, FitFailedWarning at the end", _cv_log(fail=((1, 2), (24, 5))), True),
        Fixture("GridSearchCV with verbose=1 (no per-fold lines)", "Fitting 5 folds for each of 4 candidates, totalling 20 fits\nBest parameters: {'C': 1}\n", False),
    ],
    min_gain=0.5,
)


# ===============================================================================================================
# #1328 Fixed-line packs for W&B and Airflow task logs: a table of boilerplate lines becomes '[... N fixed lines folded (wandb)]'.
#
# W&B: login, version, local-path, offline-hint, 'Syncing run', project URL, 'Waiting for W&B process', 'Synced N W&B file(s)',
# 'Find logs at' lines and the sparkline 'Run history:' block. Airflow: dependency, executor, process, command, job, running-on,
# downstream-scheduled and exit-code-0 lines; the 'Exporting env vars: AIRFLOW_CTX_*' line becomes
# '[airflow ctx dag_id=D task_id=T try=N run_id=R]'. Run URLs, 'Run summary:' metrics, task output, state lines, tracebacks
# and anything not in the table are verbatim.

_WB_FIXED = re.compile(
    r"^wandb: (?:Currently logged in as: .*|Tracking run with wandb version .*|Run data is saved locally in .*|Run `wandb offline` to turn off syncing\.|Syncing run .*"
    r"|⭐️ View project at .*|Waiting for W&B process to finish.*|Synced \d+ W&B file\(s\), .*|Find logs at: .*)$", A)
_AF = re.compile(r"^\[\d{4}-\d\d-\d\d[T,] ?\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d{4}| UTC)\] \{[A-Za-z_.]+:\d+\} (?:INFO|WARNING|ERROR|CRITICAL) - (.*)$", A)
_AF_FIXED = re.compile(r"^(?:Dependencies all met for .*|Executing <Task\(.*|Started process \d+ to run task|Running: \[.*|Job \d+: Subtask .*|Running <TaskInstance: .*|\d+ downstream tasks scheduled from follow-on schedule check|Task exited with return code 0)$", A)
_AF_CTX = re.compile(r"^Exporting env vars: AIRFLOW_CTX_DAG_OWNER='[^']*' AIRFLOW_CTX_DAG_ID='([^']*)' AIRFLOW_CTX_TASK_ID='([^']*)' AIRFLOW_CTX_EXECUTION_DATE='[^']*' AIRFLOW_CTX_TRY_NUMBER='(\d+)' AIRFLOW_CTX_DAG_RUN_ID='([^']*)'$", A)
_M1328 = re.compile(r"^\[\.\.\. (\d+) fixed lines folded \((wandb|airflow)\)\]$", A)
_M1328_CTX = re.compile(r"^\[airflow ctx dag_id=(\S*) task_id=(\S*) try=(\d+) run_id=(\S*)\]$", A)


def pack_ml_logs(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M1328.match(l) or _M1328_CTX.match(l) for l in lines):
        return text
    kind: list = []  # None | 'wandb' | 'airflow' | ('ctx', text)
    in_hist = False
    for l in lines:
        k = None
        if in_hist:
            if l.startswith("wandb: ") and l != "wandb: ":
                k = "wandb"
            else:
                in_hist = False
        if k is None:
            if l == "wandb: Run history:":
                in_hist, k = True, "wandb"
            elif _WB_FIXED.match(l):
                k = "wandb"
            else:
                m = _AF.match(l)
                if m:
                    c = _AF_CTX.match(m.group(1))
                    if c:
                        k = ("ctx", f"[airflow ctx dag_id={c.group(1)} task_id={c.group(2)} try={c.group(3)} run_id={c.group(4)}]")
                    elif _AF_FIXED.match(m.group(1)):
                        k = "airflow"
        kind.append(k)
    out: list[str] = []
    i = 0
    while i < len(lines):
        k = kind[i]
        if isinstance(k, tuple):
            out.append(k[1])
            i += 1
        elif k:
            j = i
            while j < len(lines) and kind[j] == k:
                j += 1
            out.append(f"[... {j - i} fixed lines folded ({k})]" if j - i >= 2 else lines[i])
            if j - i < 2:
                pass
            i = j
        else:
            out.append(lines[i])
            i += 1
    return gate(text, join_nl(out, nl))


def _af_msg(line: str):
    """Probe-side: the message part of an Airflow task-log line, by partition."""
    if not line.startswith("[") or "] {" not in line:
        return None
    head, _, rest = line.partition("] {")
    src, _, rest = rest.partition("} ")
    lvl, sep, msg = rest.partition(" - ")
    if not sep or lvl not in ("INFO", "WARNING", "ERROR", "CRITICAL") or ":" not in src or not isdigits(src.rpartition(":")[2]):
        return None
    ts = head[1:]
    if not (len(ts) >= 19 and (ts[10] in "T," ) and isdigits(ts[:4] + ts[5:7] + ts[8:10])):
        return None
    return msg


_WB_PREFIXES = ("wandb: Currently logged in as: ", "wandb: Tracking run with wandb version ", "wandb: Run data is saved locally in ", "wandb: Syncing run ", "wandb: ⭐️ View project at ",
                "wandb: Waiting for W&B process to finish", "wandb: Find logs at: ")


def _wb_fixed(l: str) -> bool:
    if l.startswith(_WB_PREFIXES) or l == "wandb: Run `wandb offline` to turn off syncing.":
        return True
    if l.startswith("wandb: Synced ") and l[14:15].isascii() and l[14:15].isdigit() and " W&B file(s), " in l:
        n = l[14:].partition(" ")[0]
        return isdigits(n)
    return False


def _af_fixed(msg: str) -> bool:
    if msg.startswith(("Dependencies all met for ", "Executing <Task(", "Running: [", "Running <TaskInstance: ")) or msg == "Task exited with return code 0":
        return True
    if msg.startswith("Started process ") and msg.endswith(" to run task"):
        return isdigits(msg[len("Started process "):-len(" to run task")])
    if msg.startswith("Job ") and ": Subtask " in msg:
        return isdigits(msg[4:].partition(":")[0])
    return msg.endswith(" downstream tasks scheduled from follow-on schedule check") and isdigits(msg.partition(" ")[0])


def probes_1328(text: str) -> dict:
    lines, _ = split_nl(text)
    v = 0
    kept: list[tuple[int, str]] = []
    run_urls, summary, ctx, hist_lines = [], {}, [], 0
    in_hist = in_sum = False
    for l in lines:
        m = _M1328.match(l)
        if m:
            v += int(m.group(1))
            in_hist = in_sum = False
            continue
        c = _M1328_CTX.match(l)
        if c:
            ctx.append((v, c.groups()))
            v += 1
            continue
        if in_hist:
            if l.startswith("wandb: ") and l != "wandb: ":
                v += 1
                continue
            in_hist = False
        if l == "wandb: Run history:":
            in_hist = True
            v += 1
            continue
        msg = _af_msg(l)
        if msg is not None and msg.startswith("Exporting env vars: "):
            parts = msg[len("Exporting env vars: "):].split(" ")
            f = dict(p.split("=", 1) for p in parts if p.startswith("AIRFLOW_CTX_") and "=" in p)
            keys = ["AIRFLOW_CTX_DAG_OWNER", "AIRFLOW_CTX_DAG_ID", "AIRFLOW_CTX_TASK_ID", "AIRFLOW_CTX_EXECUTION_DATE", "AIRFLOW_CTX_TRY_NUMBER", "AIRFLOW_CTX_DAG_RUN_ID"]
            if list(f) == keys and all(x.startswith("'") and x.endswith("'") and len(x) >= 2 and "'" not in x[1:-1] for x in f.values()) and isdigits(f["AIRFLOW_CTX_TRY_NUMBER"][1:-1]) and len(parts) == 6:
                ctx.append((v, (f["AIRFLOW_CTX_DAG_ID"][1:-1], f["AIRFLOW_CTX_TASK_ID"][1:-1], f["AIRFLOW_CTX_TRY_NUMBER"][1:-1], f["AIRFLOW_CTX_DAG_RUN_ID"][1:-1])))
                v += 1
                continue
        if _wb_fixed(l) or (msg is not None and _af_fixed(msg)):
            v += 1
            continue
        if l == "wandb: Run summary:":
            in_sum = True
        elif in_sum:
            if l.startswith("wandb: ") and l != "wandb: ":
                k, _, val = l[7:].strip(" ").partition(" ")
                summary[k] = val.strip(" ")
            else:
                in_sum = False
        if l.startswith("wandb: ") and "/runs/" in l:
            run_urls.append((v, l))
        kept.append((v, l))
        v += 1
    return {
        "lines kept verbatim (virtual line, text)": kept,
        "run URL lines": run_urls,
        "W&B summary metrics": sorted(summary.items()),
        "airflow context (virtual line, dag_id, task_id, try, run_id)": ctx,
        "virtual line count": v,
    }


def _wandb_log() -> str:
    return "\n".join([
        "wandb: Currently logged in as: alice (acme-ml). Use `wandb login --relogin` to force relogin", "wandb: Tracking run with wandb version 0.16.3",
        "wandb: Run data is saved locally in /work/wandb/run-20240305_101501-abc123", "wandb: Run `wandb offline` to turn off syncing.", "wandb: Syncing run fancy-sunset-12",
        "wandb: ⭐️ View project at https://wandb.ai/acme-ml/toy", "wandb: 🚀 View run at https://wandb.ai/acme-ml/toy/runs/abc123",
        "epoch 1/4 loss 0.912 acc 0.61", "epoch 2/4 loss 0.644 acc 0.72", "UserWarning: lr scheduler step called before optimizer step", "epoch 3/4 loss 0.431 acc 0.81", "epoch 4/4 loss 0.327 acc 0.86",
        "wandb: Waiting for W&B process to finish... (success).", "wandb:", "wandb: Run history:", "wandb:      acc ▁▃▆█", "wandb:    epoch ▁▃▆█", "wandb:     loss █▅▃▁", "wandb: ",
        "wandb: Run summary:", "wandb:      acc 0.86", "wandb:    epoch 4", "wandb:     loss 0.327", "wandb: ", "wandb: 🚀 View run fancy-sunset-12 at: https://wandb.ai/acme-ml/toy/runs/abc123",
        "wandb: Synced 5 W&B file(s), 0 media file(s), 0 artifact file(s) and 0 other file(s)", "wandb: Find logs at: ./wandb/run-20240305_101501-abc123/logs", "final accuracy 0.86"]) + "\n"


def _airflow_log(fail: bool) -> str:
    ts = lambda k: f"[2024-03-05, 10:15:{k:02d} UTC] "  # noqa: E731
    run = "manual__2024-03-05T10:15:00+00:00"
    out = [ts(1) + "{taskinstance.py:1957} INFO - Dependencies all met for dep_context=non-requeueable deps ti=<TaskInstance: etl.load " + run + " [queued]>",
           ts(1) + "{taskinstance.py:1957} INFO - Dependencies all met for dep_context=requeueable deps ti=<TaskInstance: etl.load " + run + " [queued]>",
           ts(1) + "{taskinstance.py:2171} INFO - Starting attempt 1 of 1",
           ts(1) + "{taskinstance.py:2192} INFO - Executing <Task(PythonOperator): load> on 2024-03-05 10:15:00+00:00",
           ts(2) + "{standard_task_runner.py:60} INFO - Started process 4821 to run task",
           ts(2) + "{standard_task_runner.py:87} INFO - Running: ['airflow', 'tasks', 'run', 'etl', 'load', '" + run + "', '--job-id', '12', '--raw']",
           ts(2) + "{standard_task_runner.py:88} INFO - Job 12: Subtask load",
           ts(3) + "{task_command.py:423} INFO - Running <TaskInstance: etl.load " + run + " [running]> on host 0f3a9c",
           ts(3) + "{taskinstance.py:2480} INFO - Exporting env vars: AIRFLOW_CTX_DAG_OWNER='airflow' AIRFLOW_CTX_DAG_ID='etl' AIRFLOW_CTX_TASK_ID='load' AIRFLOW_CTX_EXECUTION_DATE='2024-03-05T10:15:00+00:00' AIRFLOW_CTX_TRY_NUMBER='1' AIRFLOW_CTX_DAG_RUN_ID='" + run + "'",
           ts(3) + "{logging_mixin.py:188} INFO - loaded 1200 rows into staging"]
    if fail:
        out += [ts(4) + "{taskinstance.py:2731} ERROR - Task failed with exception", "Traceback (most recent call last):", '  File "/opt/airflow/dags/etl.py", line 31, in load', "    raise ValueError(\"row 17 has no customer_id\")",
                "ValueError: row 17 has no customer_id", ts(4) + "{taskinstance.py:1138} INFO - Marking task as FAILED. dag_id=etl, task_id=load, execution_date=20240305T101500, start_date=20240305T101503, end_date=20240305T101504",
                ts(4) + "{standard_task_runner.py:107} ERROR - Failed to execute job 12 for task load (row 17 has no customer_id; 4821)", ts(4) + "{local_task_job_runner.py:234} INFO - Task exited with return code 1"]
    else:
        out += [ts(4) + "{python.py:201} INFO - Done. Returned value was: None", ts(4) + "{taskinstance.py:1138} INFO - Marking task as SUCCESS. dag_id=etl, task_id=load, execution_date=20240305T101500, start_date=20240305T101503, end_date=20240305T101504",
                ts(4) + "{local_task_job_runner.py:234} INFO - Task exited with return code 0", ts(4) + "{taskinstance.py:3280} INFO - 0 downstream tasks scheduled from follow-on schedule check"]
    return "\n".join(out) + "\n"


case(
    1328,
    claim="W&B boilerplate (login, version, local path, offline hint, 'Syncing run', project URL, 'Waiting for W&B process', 'Synced N file(s)', 'Find logs at', the sparkline history block) and Airflow task-log boilerplate (dependency, executor, process, command, job, running-on, downstream and exit-code-0 lines) fold into '[... N fixed lines folded (wandb|airflow)]', the Airflow env-var line shrinks to its dag_id, task_id, try and run_id; run URLs, 'Run summary' metrics, task output, state lines, tracebacks, exit codes other than 0 and every unlisted line are verbatim.",
    loses="the W&B login name, version and local run path, the sparkline history of each metric, the Airflow dependency and command lines, the DAG owner and logical date (the run_id carries it), and the line timestamps of the folded lines",
    encode=pack_ml_logs,
    probes=probes_1328,
    fixtures=lambda: [
        Fixture("toy wandb script: banner, warning in the middle, history and summary, footer", _wandb_log(), True),
        Fixture("airflow tasks test, success", _airflow_log(False), True),
        Fixture("airflow task failure: traceback and exit code 1 stay verbatim", _airflow_log(True), True),
        Fixture("W&B online run URL only", "wandb: 🚀 View run fancy-sunset-12 at: https://wandb.ai/acme-ml/toy/runs/abc123\nwandb: Synced fancy-sunset-12: https://wandb.ai/acme-ml/toy/runs/abc123\n", False),
    ],
    min_gain=0.3,
)


# ===============================================================================================================
# #419 Prometheus /metrics exposition: drop HELP, hoist labels shared by a whole family, fold zero series and empty buckets.
#
# After a '# TYPE' line a block of 2+ sample lines that all parse (no timestamps, no NaN/Inf values) is rewritten: labels every
# line shares (never le or quantile) move to one '#@ k="v",...' line, series whose value is 0 and histogram buckets whose
# cumulative count equals the previous bucket's (le="+Inf" always stays) are dropped and counted in
# '#- N zero series, M empty buckets folded', and a bare '#@' line ends the hoisted labels. A block with any line that does not
# parse is left as it is.

_PM_NAME = r"[A-Za-z_:][A-Za-z0-9_:]*"
_PM_LABEL = r'[A-Za-z_][A-Za-z0-9_]*="(?:[^"\\]|\\.)*"'
_PM_NUM = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_PM_SAMPLE = re.compile(rf"^({_PM_NAME})(?:\{{(.*)\}})? ({_PM_NUM})$", A)
_PM_LABELS = re.compile(rf"(?:{_PM_LABEL}(?:,{_PM_LABEL})*)?")
_PM_LABEL_G = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"', A)
_PM_NOTE = re.compile(r"^#- (\d+) zero series, (\d+) empty buckets folded$", A)


def _pm_parse(line: str):
    from fractions import Fraction

    m = _PM_SAMPLE.match(line)
    if not m or not _PM_LABELS.fullmatch(m.group(2) or "", ) :
        return None
    labels = [(k, v) for k, v in _PM_LABEL_G.findall(m.group(2) or "")]
    if len({k for k, _ in labels}) != len(labels):
        return None
    return m.group(1), labels, m.group(3), Fraction(m.group(3))


def prom_compact(text: str) -> str:
    lines, nl = split_nl(text)
    if any(l.startswith("#@") or _PM_NOTE.match(l) for l in lines):
        return text
    out: list[str] = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("# HELP "):
            i += 1
            continue
        out.append(l)
        i += 1
        if not l.startswith("# TYPE "):
            continue
        j = i
        while j < len(lines) and lines[j] and not lines[j].startswith("#"):
            j += 1
        block = lines[i:j]
        parsed = [_pm_parse(b) for b in block]
        if len(block) < 2 or not all(parsed):
            continue
        common = None
        for _, labels, _, _ in parsed:
            d = {k: v for k, v in labels if k not in ("le", "quantile")}
            common = d if common is None else {k: v for k, v in common.items() if d.get(k) == v}
        order = [k for k, _ in parsed[0][1] if k in (common or {})]
        zero = empty = 0
        prev: dict = {}
        body = []
        for (name, labels, vs, val) in parsed:
            le = next((v for k, v in labels if k == "le"), None)
            if name.endswith("_bucket") and le is not None:
                key = (name, tuple(sorted((k, v) for k, v in labels if k != "le")))
                if val == prev.get(key, 0) and le != "+Inf":
                    empty += 1
                    prev[key] = val
                    continue
                prev[key] = val
            elif val == 0:
                zero += 1
                continue
            rest = [(k, v) for k, v in labels if k not in (common or {})]
            body.append(name + ("{" + ",".join(f'{k}="{v}"' for k, v in rest) + "}" if rest else "") + " " + vs)
        if order:
            out.append("#@ " + ",".join(f'{k}="{common[k]}"' for k in order))
        out += body
        if zero or empty:
            out.append(f"#- {zero} zero series, {empty} empty buckets folded")
        if order:
            out.append("#@")  # ends the hoisted labels (the HELP line that used to end the block is gone)
        i = j
    return gate(text, join_nl(out, nl))


def _pm_labels_probe(s: str):
    """Probe-side label scanner (character by character): list of (name, raw value) or None."""
    out, i = [], 0
    while i < len(s):
        j = s.find("=", i)
        k = s[i:j] if j >= 0 else ""
        if j < 0 or s[j + 1:j + 2] != '"' or not k or not (k[0].isalpha() or k[0] == "_") or not all(c.isascii() and (c.isalnum() or c == "_") for c in k) or not k[0].isascii():
            return None
        m, v = j + 2, []
        while True:
            if m >= len(s):
                return None
            ch = s[m]
            if ch == "\\":
                if m + 1 >= len(s) or s[m + 1] == "\n":
                    return None
                v.append(s[m:m + 2])
                m += 2
            elif ch == '"':
                break
            else:
                v.append(ch)
                m += 1
        out.append((k, "".join(v)))
        i = m + 1
        if i < len(s):
            if s[i] != ",":
                return None
            i += 1
            if i >= len(s):
                return None
    return out


def _pm_number(v: str):
    from fractions import Fraction

    mant, _, exp = v.replace("E", "e").partition("e")
    a, dot, b = (mant[1:] if mant.startswith("-") else mant).partition(".")
    if not (isdigits(a) and (not dot or isdigits(b))):
        return None
    if _ != "" or exp != "":
        e = exp[1:] if exp[:1] in ("+", "-") else exp
        if not isdigits(e):
            return None
    return Fraction(v)


def _pm_sample_probe(line: str):
    """(name, labels, value text, value) by character scanning, or None."""
    k = 0
    while k < len(line) and (line[k].isascii() and (line[k].isalnum() or line[k] in "_:")):
        k += 1
    name = line[:k]
    if not name or name[0].isdigit():
        return None
    rest = line[k:]
    labels: list = []
    if rest.startswith("{"):
        close = rest.rfind("}")
        if close < 0:
            return None
        labels = _pm_labels_probe(rest[1:close])
        if labels is None:
            return None
        rest = rest[close + 1:]
    if not rest.startswith(" ") or " " in rest[1:]:
        return None
    num = _pm_number(rest[1:])
    if num is None or len({k_ for k_, _ in labels}) != len(labels):
        return None
    return name, labels, rest[1:], num


def probes_419(text: str) -> dict:
    lines, _ = split_nl(text)
    types, nonzero, other = [], [], []
    zero_total = empty_total = 0
    shared: list = []
    prev: dict = {}
    increments: dict = defaultdict(list)
    for l in lines:
        if l.startswith("# HELP "):
            continue
        if l.startswith("# TYPE "):
            parts = l[7:].split(" ")
            types.append(tuple(parts))
            shared = []
            continue
        if l == "#@":
            shared = []
            continue
        if l.startswith("#@ "):
            sh = _pm_labels_probe(l[3:])
            if sh is not None:
                shared = sh
                continue
        if l.startswith("#- ") and _PM_NOTE.match(l):
            a, _, b = l[3:].partition(" zero series, ")
            zero_total += int(a)
            empty_total += int(b.partition(" ")[0])
            continue
        s = _pm_sample_probe(l)
        if s is None:
            other.append(l)
            continue
        name, labels, vs, val = s
        labels = shared + labels
        le = next((v for k, v in labels if k == "le"), None)
        if name.endswith("_bucket") and le is not None:
            key = (name, tuple(sorted((k, v) for k, v in labels if k != "le")))
            before = prev.get(key, 0)
            prev[key] = val
            if val == before and le != "+Inf":
                empty_total += 1  # the encoder counts these when it drops them; a raw listing is counted the same way
                continue
            increments[key].append((le, str(val - before)))
        elif val == 0:
            zero_total += 1
        else:
            nonzero.append((name, tuple(sorted(labels)), vs))
    return {
        "family types": sorted(types),
        "series with a non-zero value (name, all labels, value)": sorted(nonzero),
        "histogram series: (le, increment) of every bucket that changed the count": sorted((k, v) for k, v in increments.items()),
        "number of zero series and empty buckets": (zero_total, empty_total),
        "every other line": other,
    }


def _prom_text() -> str:
    rng = random.Random(419)
    inst = 'job="api",instance="10.0.0.5:9100"'
    out = ["# HELP go_goroutines Number of goroutines that currently exist.", "# TYPE go_goroutines gauge", f"go_goroutines{{{inst}}} 41"]
    out += ["# HELP http_requests_total Total number of HTTP requests by method and code.", "# TYPE http_requests_total counter"]
    for method in ("get", "post", "put", "delete"):
        for code in ("200", "404", "500"):
            v = 0 if (method, code) in {("put", "404"), ("put", "500"), ("delete", "404"), ("delete", "500"), ("get", "404"), ("post", "404")} else rng.randint(1, 5000)
            if (method, code) == ("post", "500"):
                v = 3
            out.append(f'http_requests_total{{method="{method}",code="{code}",{inst}}} {v}')
    out += ["# HELP http_request_duration_seconds A histogram of the request duration.", "# TYPE http_request_duration_seconds histogram"]
    for handler in ("/api/items", "/healthz", "/admin"):
        cum = 0
        for le in ("0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "5", "10"):
            if handler != "/admin" and float(le) >= 0.01 and float(le) <= 0.5:
                cum += rng.randint(100, 900)
            elif handler == "/admin" and le == "0.25":
                cum += 2
            out.append(f'http_request_duration_seconds_bucket{{le="{le}",handler="{handler}",method="get",{inst}}} {cum}')
        out.append(f'http_request_duration_seconds_bucket{{le="+Inf",handler="{handler}",method="get",{inst}}} {cum}')
        out.append(f'http_request_duration_seconds_sum{{handler="{handler}",method="get",{inst}}} {cum * 0.04:.2f}')
        out.append(f'http_request_duration_seconds_count{{handler="{handler}",method="get",{inst}}} {cum}')
    out += ["# HELP process_open_fds Number of open file descriptors.", "# TYPE process_open_fds gauge", f"process_open_fds{{{inst}}} 23",
            "# HELP up Whether the last scrape succeeded.", "# TYPE up gauge", f"up{{{inst}}} 1"]
    return "\n".join(out) + "\n"


case(
    419,
    claim="Prometheus exposition text loses its HELP lines; per family the labels every series shares (job, instance, ...) are stated once, series with value 0 and histogram buckets whose cumulative count equals the previous bucket's are folded into one counted note; the type of every family, every non-zero series with its full label set and value, and every bucket that changed a histogram's count (with its increment) are unchanged.",
    loses="the HELP text, the names and labels of zero-valued series (only their count), the order of labels within a series, and the le of buckets that did not change the count",
    encode=prom_compact,
    probes=probes_419,
    fixtures=lambda: [
        Fixture("Go service scrape: gauge first, counter with zero series, 3-handler histogram, gauges last", _prom_text(), True),
        Fixture("single family, timestamps and NaN make it unparseable (left as is except HELP)", "# HELP x_total A thing.\n# TYPE x_total counter\nx_total{a=\"1\"} 5 1700000000\nx_total{a=\"2\"} NaN\n", True),
        Fixture("already compact", "# TYPE up gauge\nup 1\n", False),
        Fixture("not exposition text", "GET /metrics 200\n", False),
    ],
    min_gain=0.1,
)


# ===============================================================================================================
# #416 ML framework startup and warning boilerplate pack: a table of fixed lines (TensorFlow cpu_feature_guard / cuDNN / TensorRT
# notices, the HF 'You should probably TRAIN' advice, the tokenizers fork warning, Lightning hardware lines, sklearn
# ConvergenceWarning advice and doc URLs) folds into '[... N fixed lines folded: pack xK, ...]'. A line is in the table when
# it contains every needle of one entry. Warnings that name user code, the HF 'Some weights ... newly initialized' line, the
# sklearn warning's first line and STOP reason, tracebacks and everything else are verbatim.

_ML_PACK: list[tuple[str, tuple[str, ...]]] = [
    ("tf_cpu_feature_guard", ("cpu_feature_guard.cc", "This TensorFlow binary is optimized")),
    ("tf_cpu_feature_guard", ("To enable the following instructions: ", "rebuild TensorFlow with the appropriate compiler flags.")),
    ("tf_onednn", ("oneDNN custom operations are on",)),
    ("cuda_factory", ("Unable to register cu", "factory: Attempting to register factory for plugin")),
    ("tensorrt", ("TF-TRT Warning: Could not find TensorRT",)),
    ("hf_init_advice", ("You should probably TRAIN this model on a down-stream task",)),
    ("tokenizers_fork", ("huggingface/tokenizers: The current process just got forked",)),
    ("tokenizers_fork", ("To disable this warning, you can either:",)),
    ("tokenizers_fork", ("- Avoid using `tokenizers` before the fork if possible",)),
    ("tokenizers_fork", ("- Explicitly set the environment variable TOKENIZERS_PARALLELISM=",)),
    ("lightning_hw", ("GPU available: ", ", used: ")),
    ("lightning_hw", ("TPU available: ", ", using: ")),
    ("lightning_hw", ("HPU available: ", ", using: ")),
    ("lightning_hw", ("LOCAL_RANK: ", "CUDA_VISIBLE_DEVICES: [")),
    ("lightning_hw", ("Tip: For seamless cloud logging",)),
    ("sklearn_advice", ("Increase the number of iterations (max_iter) or scale the data as shown in:",)),
    ("sklearn_advice", ("https://scikit-learn.org/stable/modules/preprocessing.html",)),
    ("sklearn_advice", ("Please also refer to the documentation for alternative solver options:",)),
    ("sklearn_advice", ("https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression",)),
    ("sklearn_advice", ("n_iter_i = _check_optimize_result(",)),
]
_M416 = re.compile(r"^\[\.\.\. (\d+) fixed lines folded: (.*)\]$", A)


def _ml_pack_of(line: str):
    for name, needles in _ML_PACK:
        if all(n in line for n in needles):
            return name
    return None


def fold_ml_noise(text: str) -> str:
    lines, nl = split_nl(text)
    if any(_M416.match(l) for l in lines):
        return text
    packs = [_ml_pack_of(l) for l in lines]
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not packs[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and packs[j]:
            j += 1
        names: dict = {}
        for p in packs[i:j]:
            names[p] = names.get(p, 0) + 1
        marker = f"[... {j - i} fixed lines folded: {', '.join(f'{k} x{v}' for k, v in names.items())}]"
        out.append(marker if len(marker) < sum(len(l) + 1 for l in lines[i:j]) else "\n".join(lines[i:j]))
        i = j
    return gate(text, join_nl(out, nl))


def probes_416(text: str) -> dict:
    lines, _ = split_nl(text)
    v = 0
    kept: list[tuple[int, str]] = []
    counts: Counter = Counter()
    user: list[tuple[int, str]] = []
    for l in lines:
        m = _M416.match(l)
        if m:
            v += int(m.group(1))
            for part in m.group(2).split(", "):
                name, _, k = part.rpartition(" x")
                counts[name] += int(k)
            continue
        pk = _ml_pack_of(l)
        if pk:
            counts[pk] += 1
        else:
            kept.append((v, l))
        # independent of the table: any line that names user code, a failure or a traceback must have survived
        if "/work/" in l or "Traceback" in l or "Error" in l or l.startswith("  File ") or "Warning:" in l and "tensorflow/" not in l and "TF-TRT" not in l and "sklearn/" not in l:
            user.append((v, l))
        v += 1
    return {
        "lines outside the fixed-line table (virtual line, text)": kept,
        "how many fixed lines of each pack there were": sorted(counts.items()),
        "lines naming user code, errors, tracebacks (virtual line, text)": user,
        "virtual line count": v,
    }


def _tf_run() -> str:
    return "\n".join([
        "2024-03-05 10:15:01.101010: I tensorflow/core util/port.cc:113] oneDNN custom operations are on. You may see slightly different numerical results due to floating-point round-off errors from different computation orders.",
        "2024-03-05 10:15:01.402020: E external/local_xla/xla/stream_executor/cuda/cuda_dnn.cc:9261] Unable to register cuDNN factory: Attempting to register factory for plugin cuDNN when one has already been registered",
        "2024-03-05 10:15:01.403030: E external/local_xla/xla/stream_executor/cuda/cuda_fft.cc:607] Unable to register cuFFT factory: Attempting to register factory for plugin cuFFT when one has already been registered",
        "2024-03-05 10:15:01.404040: E external/local_xla/xla/stream_executor/cuda/cuda_blas.cc:1515] Unable to register cuBLAS factory: Attempting to register factory for plugin cuBLAS when one has already been registered",
        "2024-03-05 10:15:02.505050: W tensorflow/compiler/tf2tensorrt/utils/py_utils.cc:38] TF-TRT Warning: Could not find TensorRT",
        "2024-03-05 10:15:03.606060: I tensorflow/core/platform/cpu_feature_guard.cc:182] This TensorFlow binary is optimized to use available CPU instructions in performance-critical operations.",
        "To enable the following instructions: AVX2 FMA, in other operations, rebuild TensorFlow with the appropriate compiler flags.",
        "Some weights of BertForSequenceClassification were not initialized from the model checkpoint at bert-base-uncased and are newly initialized: ['classifier.bias', 'classifier.weight']",
        "You should probably TRAIN this model on a down-stream task to be able to use it for predictions and inference.",
        "huggingface/tokenizers: The current process just got forked, after parallelism has already been used. Disabling parallelism to avoid deadlocks...",
        "To disable this warning, you can either:", "\t- Avoid using `tokenizers` before the fork if possible", "\t- Explicitly set the environment variable TOKENIZERS_PARALLELISM=(true | false)",
        "epoch 1 loss 0.693 acc 0.51", "/work/train.py:42: UserWarning: labels are unbalanced (84% negative), consider class weights", "epoch 2 loss 0.512 acc 0.74",
        "huggingface/tokenizers: The current process just got forked, after parallelism has already been used. Disabling parallelism to avoid deadlocks...",
        "To disable this warning, you can either:", "\t- Avoid using `tokenizers` before the fork if possible", "\t- Explicitly set the environment variable TOKENIZERS_PARALLELISM=(true | false)",
        "epoch 3 loss 0.388 acc 0.83", "final eval accuracy 0.83"]) + "\n"


def _lightning_sklearn_run(crash: bool) -> str:
    out = ["GPU available: True (cuda), used: True", "TPU available: False, using: 0 TPU cores", "HPU available: False, using: 0 HPUs",
           "LOCAL_RANK: 0 - CUDA_VISIBLE_DEVICES: [0]", "💡 Tip: For seamless cloud logging and experiment tracking, try installing [litlogger](https://pypi.org/project/litlogger/) to enable LitLogger, which logs metrics and artifacts automatically to the Lightning Cloud.",
           "Epoch 0: 100%|██████████| 12/12 [00:01<00:00, 9.1it/s, loss=0.62]", "Epoch 1: 100%|██████████| 12/12 [00:01<00:00, 9.4it/s, loss=0.41]",
           "/usr/lib/python3/dist-packages/sklearn/linear_model/_logistic.py:460: ConvergenceWarning: lbfgs failed to converge (status=1):", "STOP: TOTAL NO. of ITERATIONS REACHED LIMIT.", "",
           "Increase the number of iterations (max_iter) or scale the data as shown in:", "    https://scikit-learn.org/stable/modules/preprocessing.html",
           "Please also refer to the documentation for alternative solver options:", "    https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression", "  n_iter_i = _check_optimize_result(",
           "logreg accuracy 0.91"]
    if crash:
        out += ["Traceback (most recent call last):", '  File "/work/train.py", line 77, in <module>', "    model.fit(X_test)", "ValueError: Found input variables with inconsistent numbers of samples: [100, 0]"]
    return "\n".join(out) + "\n"


case(
    416,
    claim="Lines from a table of ML boilerplate (TensorFlow cpu_feature_guard, oneDNN, cuDNN/cuFFT/cuBLAS factory, TensorRT notices, the HF training advice, the tokenizers fork warning, Lightning hardware and tip lines, sklearn ConvergenceWarning advice and URLs) fold into '[... N fixed lines folded: pack xK]'; every other line (user warnings, the HF newly-initialized weights line, the first line and STOP reason of a ConvergenceWarning, metrics, tracebacks) is verbatim at its exact position and the per-pack counts are unchanged.",
    loses="the text of the folded boilerplate lines (it is the table, so only the pack name and count remain) and their timestamps",
    encode=fold_ml_noise,
    probes=probes_416,
    fixtures=lambda: [
        Fixture("TensorFlow and transformers startup: notices at the top, a user warning in the middle, fork warnings at the end", _tf_run(), True),
        Fixture("Lightning start lines and a sklearn ConvergenceWarning in the middle", _lightning_sklearn_run(False), True),
        Fixture("failure variant: same run ending in a user-code traceback", _lightning_sklearn_run(True), True),
        Fixture("clean script output", "epoch 1 done\nepoch 2 done\nTo enable the following instructions, run make install\n", False),
    ],
    min_gain=0.2,
)
