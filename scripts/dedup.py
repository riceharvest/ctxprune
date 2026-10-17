"""E2 `dedup`: cross-turn dedup of tool outputs (lossless w.r.t. what is already in the agent's context).

Rules (crossturn lab P1-P3 + miner V1/V2, 2026-10-17), against the session's earlier outputs AS THE MODEL SAW THEM
(rendered, after their own compression) and earlier tool INPUTS (Write content, Edit new_string, multi-line Bash
commands / heredocs), only the last MAX_AGE (~100) outputs:
  * an output identical to an earlier one ->  [ctxprune: output identical to output #12 above (`git status`)]
  * every run of >= MIN_RUN (2) consecutive lines byte-identical to a contiguous run of ONE earlier output / input,
    carrying >= MIN_CHARS (40; inputs 60) non-blank chars and more tokens than its marker ->
        [ctxprune: lines 40–180 identical to output #12 above (`cat src/app.py`)]
        [ctxprune: lines 3–40 identical to input #4 above (`Write src/app.py`)]
    a–b are 1-based line numbers inside that output / input; #k numbers outputs (and, separately, inputs) since the
    last compaction; the hint is the producing command (<= 60 chars), so the model can find it.
  Runs start at an anchor line (>= 4 non-blank chars, one a letter or digit; 256 candidates), never hold a gap marker / recovery footer and
  never cut a lossless block. Greedy left to right; ties: longest run, then the most recent source, then its lowest
  line. Deterministic in (ordered earlier sources, this output): identical history -> identical bytes. expand() is
  the exact inverse given the session state the output was deduped against, and never raises.
MIN_RUN 2 waits on the comprehension probe: set it back to 3 if the probe objects.

Library (proxy / hooks):
  s = DedupSession(); new, info = s.dedup(text); s.register(final_rendered_text, label)   # incremental
  s.register_input(text, label)                 # an earlier tool input the model has in context
  s.process(text, label)                        # dedup + register in one call (when nothing compresses afterwards)
  s.reset()                                     # prompt shrank without a compaction record: forget all candidates
  dedup_history([(label, text), ...]) -> [rendered, ...]   # pure, whole history (what a proxy recomputes per request)
  expand(rendered, s_before) -> original
  hook_session(ev, state_dir) -> HookSession   # Claude Code PostToolUse: lazy, incremental per-session state
  check_transcript(path) -> counts             # inverse-check harness on a real transcript
Proxy note: ctxprune_proxy's per-result cache is keyed by (tool, args, text). A dedup'd result depends on its position
in the history, so it must NOT go through that cache. Apply DedupSession to the ordered tool results of each request
instead (deterministic).
Hook note (UNVERIFIED): whether transcript_path stores the hook's replacement or the original output is not known, so
the hook keeps what it rendered (<sid>.side.jsonl) and uses that for those tool_use_ids.

Simulation (E2 job, PLAN: held-out trajectories, same sources/split as build_replay):
  collect   (box, network) stream held-out trajectories -> traj.jsonl (+ items.jsonl: outputs >= 6k for v2 scoring)
  [GPU]     train_lines.py score --model <frozen v2> --data items.jsonl --out scores.json
  simulate  (CPU) arms per trajectory: none | dedup | v2@0.5 (outputs >= 6k) | dedup+v2 (i: budget 0.5 x ORIGINAL
            tokens, ii: 0.5 x DEDUP'D tokens); o200k tokens saved, fire rates, losslessness check (expand == original
            for every dedup'd output), determinism check, used-token survival (a token counts if it is in the output
            or in the earlier lines a marker points to). v2 line scores come from the ORIGINAL output (one GPU pass);
            under dedup+v2 the remaining lines keep those scores (approximation: in deployment the model would see the
            dedup'd text).
  python3 scripts/dedup.py --self-test   (< 2 s, no network)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:   # appended: never shadow the host process's modules
    sys.path.append(_HERE)

# P1-P3 + age cap (crossturn lab 2026-10-17: CC 0.68% -> 0.89% of hook tokens, Hermes 4.5% -> 4.8%; 832k inverse
# checks, 0 failures). MIN_RUN 2 is pending the comprehension probe: set it back to 3 if the probe objects.
MIN_RUN = 2
MIN_CHARS = 40          # non-blank chars a run must carry (output sources)
INPUT_MIN_CHARS = 60    # ... when the source is an earlier tool INPUT (miner V1+V2: Write/Edit/heredoc bodies)
ANCHOR_CHARS = 4
MAX_CANDS = 256
MAX_AGE = 100           # only the last ~100 outputs are referenced (86% / 97% of the savings, CC / Hermes)
MIN_LEN = 48            # shorter outputs can't beat the shortest marker (47 chars): skip without loading the state

MARK = "[ctxprune: lines {a}–{b} identical to {src} #{k} above{hint}]"
MARK_RE = re.compile(r"^\[ctxprune: lines (\d+)–(\d+) identical to (output|input) #(\d+) above(?: \(`.*`\))?\]$")
WHOLE = "[ctxprune: output identical to output #{k} above{hint}]"
WHOLE_RE = re.compile(r"^\[ctxprune: output identical to output #(\d+) above(?: \(`.*`\))?\]$")


def _is_mark(line: str) -> bool:
    return line.startswith("[ctxprune: ") and bool(MARK_RE.match(line) or WHOLE_RE.match(line))


_OPEN = re.compile(r"^\[(?:ctxprune|cp):[a-z][\w-]*(?:\]| )")    # lossless block fence: [ctxprune:file], [cp:table …]
_CLOSE = re.compile(r"^\[/(?:ctxprune|cp)\]$")
HARD, OPEN, CLOSE = 3, 1, 2


def _kind(line: str) -> int:
    """0 = text or a self-contained marker; OPEN / CLOSE = lossless block fence (a run must hold whole blocks, red
    team #7: cut fences left an orphan header or `[/ctxprune]`); HARD = lossy pointer (gap marker, recovery footer:
    they refer to their own output's recovery file, so they never go into a ref)."""
    if not line.startswith("["):
        return 0
    if line.startswith("[…") or line.startswith("[ctxprune: kept "):
        return HARD
    if _OPEN.match(line):
        return OPEN
    if _CLOSE.match(line):
        return CLOSE
    return 0


def _fence(line: str) -> bool:
    return _kind(line) != 0


def _span(lines: list[str]) -> tuple[int, int]:
    """Referable lines [lo, hi) of a source, the same for every reader. Chat templates that strip() each message
    (Qwen3.6/3.8 do, tool results included) drop leading/trailing blank lines and the first line's indentation /
    the last line's trailing spaces. So: a source starting with a blank line is never referenced into (its line
    numbers differ per template), its first line only if it has no leading whitespace, trailing blank lines and a last
    line with trailing whitespace never. Whole-output markers are unaffected (strip of identical texts is identical)."""
    if not lines or not lines[0].strip():
        return 0, 0
    lo = 1 if lines[0][:1].isspace() else 0
    hi = len(lines)
    while hi > lo and not lines[hi - 1].strip():
        hi -= 1
    if hi > lo and lines[hi - 1][-1:].isspace():
        hi -= 1
    return lo, hi


def _hint(label: str) -> str:
    label = " ".join((label or "").split())
    if not label:
        return ""
    label = label if len(label) <= 60 else label[:57] + "..."
    return f" (`{label.replace('`', chr(39))}`)"


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def _est_tokens(s: str) -> int:
    """lossless2's BPE-like estimate: a separator line (`=====`, 70 chars) is ~5 tokens, not len // 4 = 17. A marker
    must beat the run on this count too (typical-workload study: dedup markers grew separator-heavy outputs)."""
    from lossless2 import _est
    return _est(s)


class DedupSession:
    """Earlier rendered outputs (and tool inputs) of one context, numbered from 1 since the last compaction. Only
    the last max_age outputs (and inputs registered while they were current) are referenced; reset() (a prompt
    shrink without a compaction record) drops every candidate but keeps the numbering."""

    def __init__(self, min_run: int = MIN_RUN, min_chars: int = MIN_CHARS, max_cands: int = MAX_CANDS,
                 anchor_chars: int = ANCHOR_CHARS, count=None, max_age: int = MAX_AGE,
                 input_min_chars: int = INPUT_MIN_CHARS, whole: bool = True):
        self.min_run, self.min_chars, self.max_cands, self.anchor_chars = min_run, min_chars, max_cands, anchor_chars
        self.max_age, self.input_min_chars, self.whole = max_age, input_min_chars, whole
        self.min_len = MIN_LEN
        self.count = count or _approx_tokens
        self.outputs: list[tuple[str, list[str]]] = []   # (label, rendered lines); output #k = outputs[k - 1 - offset]
        self.inputs: list[tuple[str, list[str], int]] = []   # (label, lines, outputs registered before it)
        self.offset = self.in_offset = 0
        self.index: dict[str, list[tuple[int, int, int, int]]] = {}   # line -> [(seq, src 0/1, k, j)]
        self.full: dict[str, int] = {}                    # rendered text -> latest output number
        self.seq = 0
        self.hi: dict[tuple[int, int], int] = {}          # (src, k) -> end of the referable lines (_span)
        self.floor = 0                                    # outputs <= floor are not referenced (reset)

    @property
    def n(self) -> int:
        return self.offset + len(self.outputs)

    def _anchor(self, line: str) -> bool:   # a run never starts on a separator / punctuation-only line
        return len(line.strip()) >= self.anchor_chars and _kind(line) in (0, OPEN) and any(c.isalnum() for c in line)

    def _lines(self, src: int, k: int):
        if src == 0:
            i = k - 1 - self.offset
            return self.outputs[i][1] if 0 <= i < len(self.outputs) else None
        i = k - 1 - self.in_offset
        return self.inputs[i][1] if 0 <= i < len(self.inputs) else None

    def _label(self, src: int, k: int) -> str:
        return (self.outputs[k - 1 - self.offset] if src == 0 else self.inputs[k - 1 - self.in_offset])[0]

    def _live(self, src: int, k: int) -> bool:
        cut = max(self.floor, self.n - self.max_age)
        if src == 0:
            return k > cut
        i = k - 1 - self.in_offset
        return 0 <= i < len(self.inputs) and self.inputs[i][2] >= cut and k > self.in_floor

    in_floor = 0

    def _add(self, src: int, k: int, lines: list[str]) -> None:
        self.seq += 1
        lo, hi = _span(lines)
        self.hi[(src, k)] = hi
        for j in range(lo, hi):
            ln = lines[j]
            if self._anchor(ln) and not _is_mark(ln):
                self.index.setdefault(ln, []).append((self.seq, src, k, j))

    def register(self, rendered: str, label: str = "") -> int:
        lines = rendered.split("\n")
        self.outputs.append((label, lines))
        k = self.n
        self._add(0, k, lines)
        self.full[rendered] = k
        return k

    def register_input(self, text: str, label: str = "") -> int:
        """An earlier tool INPUT the model has in context as its own tool call (Write content, Edit new_string, a
        Bash heredoc): lines of it become referable as `input #k`."""
        lines = text.split("\n")
        self.inputs.append((label, lines, self.n))
        k = self.in_offset + len(self.inputs)
        self._add(1, k, lines)
        return k

    def reset(self) -> None:
        """Prompt shrank without a compaction record: nothing earlier is referenced any more."""
        self.floor, self.in_floor = self.n, self.in_offset + len(self.inputs)

    def _run(self, lines: list[str], i: int, E: list[str], j: int) -> int:
        """Longest L with lines[i:i+L] == E[j:j+L], no HARD line, fences balanced (never a cut block)."""
        n, depth, L, best = len(lines), 0, 0, 0
        while i + L < n and j + L < len(E) and lines[i + L] == E[j + L]:
            k = _kind(lines[i + L])
            if k == HARD or (k == CLOSE and depth == 0):
                break
            depth += 1 if k == OPEN else -1 if k == CLOSE else 0
            L += 1
            if depth == 0:
                best = L
        return best

    def dedup(self, text: str) -> tuple[str, dict]:
        """-> (new text, info{refs: [(src_no, a, b, start_line_in_text, n_lines, src)], lines_removed, chars_saved,
        whole}). src_no is the output number (src "output") or input number (src "input")."""
        none = {"refs": [], "lines_removed": 0, "chars_saved": 0}
        lines = text.split("\n")
        if any(_is_mark(x) for x in lines):     # raw text holds a marker-shaped line (stage 0 escapes these;
            return text, none                   # without it expand would misread it)
        if self.whole:
            k = self.full.get(text)
            if k is not None and self._live(0, k):
                mark = WHOLE.format(k=k, hint=_hint(self._label(0, k)))
                if len(text) > len(mark) and self.count(text) > self.count(mark) and \
                        _est_tokens(text) > _est_tokens(mark):
                    return mark, {"refs": [(k, 1, len(lines), 0, len(lines), "output")], "whole": True,
                                  "lines_removed": len(lines), "chars_saved": len(text) - len(mark)}
        out, refs, i, n = [], [], 0, len(lines)
        while i < n:
            best = None  # (L, seq, src, k, j)
            if self._anchor(lines[i]):
                cands = self.index.get(lines[i], [])
                taken = 0
                for seq, src, k, j in reversed(cands):     # newest first, at most max_cands live ones
                    if not self._live(src, k):
                        if src == 0 and k <= max(self.floor, self.n - self.max_age):
                            break   # older entries are older still (index lists are in registration order)
                        continue
                    taken += 1
                    if taken > self.max_cands:
                        break
                    E = self._lines(src, k)
                    L = self._run(lines, i, E[:self.hi.get((src, k), len(E))], j)
                    if best is None or (L, seq, -j) > (best[0], best[1], -best[4]):
                        best = (L, seq, src, k, j)
            if best and best[0] >= max(1, self.min_run):
                L, _, src, k, j = best
                run = lines[i:i + L]
                mark = MARK.format(a=j + 1, b=j + L, src="input" if src else "output", k=k,
                                   hint=_hint(self._label(src, k)))
                need = self.input_min_chars if src else self.min_chars
                if sum(len(x.strip()) for x in run) >= need and self.count("\n".join(run)) > self.count(mark) \
                        and _est_tokens("\n".join(run)) > _est_tokens(mark):
                    out.append(mark)
                    refs.append((k, j + 1, j + L, i, L, "input" if src else "output"))
                    i += L
                    continue
            out.append(lines[i])
            i += 1
        new = "\n".join(out)
        return new, {"refs": refs, "lines_removed": sum(r[4] for r in refs), "chars_saved": len(text) - len(new)}

    def process(self, text: str, label: str = "", min_len: int = 0) -> tuple[str, dict]:
        new, info = self.dedup(text) if len(text) >= min_len else (text, {"refs": [], "lines_removed": 0,
                                                                         "chars_saved": 0})
        self.register(new, label)
        return new, info

    def ref_lines(self, refs) -> list[str]:
        """The earlier rendered lines the markers of one output point to (still in the model's context)."""
        res = []
        for r in refs:
            k, a, b = r[0], r[1], r[2]
            src = 1 if len(r) > 5 and r[5] == "input" else 0
            res += (self._lines(src, k) or [])[a - 1:b]
        return res


def expand(rendered: str, s: DedupSession) -> str:
    """Inverse of s.dedup (s = the session state BEFORE the output was registered). Never raises: a marker that
    doesn't resolve (past the session, malformed numbers) stays as it is."""
    m = WHOLE_RE.match(rendered) if "\n" not in rendered else None
    if m and len(m.group(1)) < 10:
        lines = s._lines(0, int(m.group(1)))
        if lines is not None:
            return "\n".join(lines)
    out = []
    for ln in rendered.split("\n"):
        m = MARK_RE.match(ln) if ln.startswith("[ctxprune: lines ") else None
        if m and all(len(m.group(g)) < 10 for g in (1, 2, 4)):
            lines = s._lines(1 if m.group(3) == "input" else 0, int(m.group(4)))
            a, b = int(m.group(1)), int(m.group(2))
            if lines is not None and 1 <= a <= b <= len(lines):
                out += lines[a - 1:b]
                continue
        out.append(ln)
    return "\n".join(out)


def dedup_history(history: list[tuple[str, str]], **kw) -> list[str]:
    s = DedupSession(**kw)
    return [s.process(text, label)[0] for label, text in history]


# ---------------------------------------------------------------- Claude Code hook support
# State is private (audit M2): 0700 dir, 0600 files; a session's files are pruned together once the newest of them
# is older than KEEP_DAYS (Claude Code's own transcript retention, cleanupPeriodDays default 30).
# Per session (hook latency, productization audit: the old full rescan cost 0.4-0.5 s and 140 MB per call in a
# 2,000-output session):
#   <sid>.state.json  transcript offset parsed so far, output/input counters, the last MAX_AGE rendered outputs and
#                     the inputs still in reach, labels of tool calls whose results are pending, last prompt size
#   <sid>.side.jsonl  append-only: {"id", "label", "text"} for every output the hook rewrote (what the model saw)
#   <sid>.lock        flock around read-catch up-write of the state
# Each call parses only the new transcript tail and the new side lines; outputs < MIN_LEN chars never load it.
KEEP_DAYS = float(os.environ.get("CTXPRUNE_DEDUP_KEEP_DAYS", 30))
_SUFFIXES = (".seen.jsonl", ".side.jsonl", ".state.json", ".jsonl", ".off", ".lock")
STATE_V = 2
MAX_INPUTS = 200


def _state(state_dir) -> Path:
    from recover import private_dir
    d = private_dir(state_dir)
    _prune(d)
    return d


def _prune(d: Path) -> None:
    import time
    try:
        stamp = d / ".pruned"
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 3600:
            return
        cut = time.time() - KEEP_DAYS * 86400
        groups: dict[str, list[Path]] = {}
        for p in d.iterdir():
            if p.name.endswith(".tmp"):
                if p.stat().st_mtime < time.time() - 86400:
                    p.unlink(missing_ok=True)
                continue
            for suf in _SUFFIXES:
                if p.name.endswith(suf):
                    groups.setdefault(p.name[:-len(suf)], []).append(p)
                    break
        for files in groups.values():
            if max(f.stat().st_mtime for f in files) < cut:
                for f in files:
                    f.unlink(missing_ok=True)
        from recover import private_open
        with private_open(stamp):
            pass
    except OSError:
        pass


def _lock_nb(f) -> None:
    """Exclusive non-blocking lock on an open lock file, released when it is closed; BlockingIOError while another
    hook holds it. fcntl does not exist on Windows (the hook used to die there with ImportError, so CTXPRUNE_DEDUP=1
    never deduped and every call logged an error): msvcrt locks the first byte instead."""
    try:
        import fcntl
    except ImportError:
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise BlockingIOError(str(e)) from e
        return
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _popen(path, mode="w"):
    from recover import private_open
    return private_open(path, mode)


def _sid(ev: dict) -> str:
    return re.sub(r"[^\w.-]", "_", str(ev.get("session_id") or ""))


def call_label(name: str, inp) -> str:
    """The hint for a tool call's output (red team #7: unlabelled refs had no hint). Same text the hooks register."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Bash", "PowerShell"):
        return str(inp.get("command") or "")[:200]
    if name == "Grep":
        from lossless_hook import grep_command
        return grep_command(inp)[:200]
    if name.startswith("mcp__"):
        return name
    arg = next((str(v) for k in ("file_path", "path", "pattern", "url", "query", "command", "description")
                if (v := inp.get(k))), "")
    return f"{name} {arg}".strip()[:200]


def call_inputs(name: str, inp) -> list[tuple[str, str]]:
    """(label, text) of the parts of a tool call's input worth referencing (miner V1+V2): Write content, Edit /
    MultiEdit new_string, a multi-line Bash command (heredoc bodies)."""
    if not isinstance(inp, dict):
        return []
    fp = str(inp.get("file_path") or "")
    out = []
    if name == "Write" and isinstance(inp.get("content"), str):
        out.append((f"Write {fp}", inp["content"]))
    elif name in ("Edit", "MultiEdit"):
        for e in [inp] + [e for e in inp.get("edits") or [] if isinstance(e, dict)]:
            if isinstance(e.get("new_string"), str):
                out.append((f"{name} {fp}", e["new_string"]))
    elif name in ("Bash", "PowerShell") and isinstance(inp.get("command"), str):
        out.append((f"{name} " + inp["command"].split("\n", 1)[0], inp["command"]))
    return [(lab, t) for lab, t in out if t.count("\n") >= 2]


def _text_of(x) -> str:
    return x if isinstance(x, str) else "".join(y.get("text", "") for y in (x or []) if isinstance(y, dict))


def _prompt_size(u: dict) -> int:
    pt = lambda x: (x.get("input_tokens") or 0) + (x.get("cache_creation_input_tokens") or 0) + \
        (x.get("cache_read_input_tokens") or 0)
    its = u.get("iterations")
    if isinstance(its, list) and its and isinstance(its[-1], dict):
        main = [x for x in its if isinstance(x, dict) and "advisor" not in str(x.get("type", ""))]
        if main:
            return pt(main[-1])
    return pt(u)


def _new_state() -> dict:
    return {"v": STATE_V, "off": 0, "side_off": 0, "n": 0, "n_in": 0, "floor": 0, "in_floor": 0, "ring": [],
            "inputs": [], "labels": {}, "side": {}, "prompt": 0, "tr": "", "guard": {}}


def _guard(st: dict):
    """The session's refetch guard (ctxprune_core.Guard) over st["guard"] (states written before it get one)."""
    from ctxprune_core import Guard
    g = st.get("guard")
    if not isinstance(g, dict):
        g = st["guard"] = {}
    return Guard(g, turns=_guard_turns())


def _guard_turns() -> int:
    from ctxprune_core import GUARD_TURNS
    try:
        return int(os.environ.get("CTXPRUNE_GUARD_TURNS") or GUARD_TURNS)
    except ValueError:
        return GUARD_TURNS


def _soft_reset(st: dict) -> None:
    st["floor"], st["in_floor"], st["ring"], st["inputs"] = st["n"], st["n_in"], [], []


def catch_up(st: dict, records, skip_id: str = "") -> None:
    """Apply transcript records (dicts, in order) to the state: compaction resets the numbering, a prompt shrink by
    more than max(2000 tokens, 5%) with no compaction record drops every candidate (crossturn P4g), tool_use blocks
    give labels and referable inputs, tool_result blocks become outputs (our rendered text when the hook rewrote
    them)."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("isSidechain"):
            continue
        if rec.get("isCompactSummary") or (rec.get("type") == "system" and rec.get("subtype") == "compact_boundary"):
            st.update(n=0, n_in=0, floor=0, in_floor=0, ring=[], inputs=[], prompt=0)
            if isinstance(st.get("guard"), dict):   # lossy outputs left the context; a running hold stays
                st["guard"]["lossy"] = []
            continue
        msg = rec.get("message") or {}
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if rec.get("type") == "assistant":
            u = msg.get("usage")
            if isinstance(u, dict) and str(msg.get("model", "")).startswith("claude"):
                q = _prompt_size(u)
                if q and st["prompt"] and q < st["prompt"] - max(2000, 0.05 * st["prompt"]):
                    _soft_reset(st)
                if q:
                    st["prompt"] = q
            for b in c if isinstance(c, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name, inp = str(b.get("name") or ""), b.get("input")
                    st["labels"][str(b.get("id") or "")] = call_label(name, inp)
                    # every tool call counts for the refetch guard, incl. Read of a recovery file (no hook sees it)
                    _guard(st).observe(str(b.get("id") or ""), _guard_cmd(name, inp), _guard_extra(inp))
                    for lab, t in call_inputs(name, inp):
                        st["n_in"] += 1
                        st["inputs"].append([st["n"], st["n_in"], lab, t])
        elif rec.get("type") == "user" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tid = str(b.get("tool_use_id") or "")
                    if tid and tid == skip_id:
                        continue
                    lab = st["labels"].pop(tid, "")
                    side = st["side"].pop(tid, None)
                    lab, text = (side[0] or lab, side[1]) if side else (lab, _text_of(b.get("content")))
                    st["n"] += 1
                    st["ring"].append([st["n"], lab, text])
    cut = max(st["floor"], st["n"] - MAX_AGE)
    st["ring"] = [r for r in st["ring"] if r[0] > cut]
    st["inputs"] = [x for x in st["inputs"] if x[0] >= cut and x[1] > st["in_floor"]][-MAX_INPUTS:]
    if len(st["labels"]) > 512:     # results that never came (interrupted calls)
        st["labels"] = dict(list(st["labels"].items())[-256:])
    if len(st["side"]) > 512:
        st["side"] = dict(list(st["side"].items())[-256:])


def _guard_cmd(name: str, inp) -> str:
    """What a re-run is matched on: a Bash command, else the call label (tool + its main argument)."""
    if name in ("Bash", "PowerShell") and isinstance(inp, dict):
        return str(inp.get("command") or "")
    return call_label(name, inp)


def _guard_extra(inp) -> str:
    """Path-like arguments of a call (a Read / view of a recovery file)."""
    if not isinstance(inp, dict):
        return ""
    return " ".join(str(v) for k, v in inp.items() if k in ("file_path", "path", "filePath", "pattern")
                    and isinstance(v, str))[:2000]


def session_from_state(st: dict, **kw) -> DedupSession:
    s = DedupSession(**kw)
    ring, inputs = st["ring"], st["inputs"]
    s.offset = ring[0][0] - 1 if ring else st["n"]
    s.in_offset = inputs[0][1] - 1 if inputs else st["n_in"]
    s.floor, s.in_floor = st["floor"], st["in_floor"]
    # register inputs and outputs in their original order (seq = recency for the tie-break)
    ev = [(r[0], 1, r) for r in ring] + [(x[0] + 0.5, 0, x) for x in inputs]
    for _, is_out, r in sorted(ev, key=lambda e: (e[0], -e[1])):
        if is_out:
            s.register(r[2], r[1])
        else:
            s.register_input(r[3], r[2])
    return s


class HookSession:
    """Lazy DedupSession for one Claude Code hook call: the state is loaded (and caught up with the transcript) only
    when an output of >= MIN_LEN chars is deduped. dedup() -> (text, info); register() appends what the model sees."""

    def __init__(self, ev: dict, state_dir, **kw):
        self.ev, self.dir, self.kw = ev, Path(state_dir), kw
        self.sid = _sid(ev)
        self.min_len = MIN_LEN
        self.sess = None
        self.g = None   # refetch guard (ctxprune_core.Guard) over this state, set by load()
        # Claude Code 2.1.302 gives subagent tool calls the MAIN session's transcript_path plus agent_id; a subagent
        # sees only its own context, kept in <session dir>/subagents/agent-<agent_id>.jsonl. Its state is separate
        # (the side file is shared: keyed by tool_use_id). No subagent transcript -> no dedup.
        self.tr = ev.get("transcript_path")
        self.key = self.sid
        agent = re.sub(r"[^\w.-]", "_", str(ev.get("agent_id") or ""))
        if agent and self.tr:
            base = os.path.splitext(self.tr)[0]
            self.tr = os.path.join(base, "subagents", f"agent-{agent}.jsonl")
            self.key = f"{self.sid}.a-{agent}"

    def load(self) -> DedupSession | None:
        if self.sess is not None or not self.sid:
            return self.sess
        tr = self.tr
        if not tr or not os.path.exists(tr):
            return None
        try:
            import time
            d = _state(self.dir)
            with _popen(d / f"{self.key}.lock", "a") as lk:
                t_end = time.monotonic() + 2.0
                while True:
                    try:
                        _lock_nb(lk)
                        break
                    except BlockingIOError:
                        if time.monotonic() > t_end:
                            return None          # another hook is busy for > 2 s: no dedup this time
                        time.sleep(0.005)
                st = self._read(d)
                changed = self._catch_up(d, st, tr)
                g = _guard(st)   # count the current call now (its tool_use may not be in the transcript yet)
                cur = str(self.ev.get("tool_use_id") or "")
                if cur and cur not in g.st["ids"]:
                    ti = self.ev.get("tool_input")
                    g.observe(cur, _guard_cmd(str(self.ev.get("tool_name") or ""), ti), _guard_extra(ti))
                    changed = True
                self.g = g
                if changed:
                    tmp = d / f"{self.key}.state.{os.getpid()}.tmp"
                    with _popen(tmp) as f:
                        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
                    os.replace(tmp, d / f"{self.key}.state.json")
        except (OSError, ValueError):
            return None
        self.sess = session_from_state(st, **self.kw)
        return self.sess

    def _read(self, d: Path) -> dict:
        try:
            st = json.loads((d / f"{self.key}.state.json").read_text(encoding="utf-8"))
            if isinstance(st, dict) and st.get("v") == STATE_V:
                return st
        except (OSError, ValueError):
            pass
        st = _new_state()
        try:   # one-time upgrade: outputs the old hook rewrote (<sid>.jsonl sidecar) keep their rendered text
            for line in (d / f"{self.sid}.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                    st["side"][str(r["id"])] = [r.get("label") or "", r["text"]]
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            pass
        return st

    def _catch_up(self, d: Path, st: dict, tr: str) -> bool:
        size = os.path.getsize(tr)
        if st["tr"] != tr or st["off"] > size:       # another / a shrunken transcript: start over (the side file is
            side = st["side"] if not st["tr"] else {}  # read again from 0, so no rendered text is lost)
            st.clear()
            st.update(_new_state(), tr=tr, side=side)
        changed = False
        side_p = d / f"{self.sid}.side.jsonl"
        if side_p.exists() and side_p.stat().st_size > st["side_off"]:
            with open(side_p, "rb") as f:
                f.seek(st["side_off"])
                data = f.read()
            end = data.rfind(b"\n") + 1
            for raw in data[:end].splitlines():
                try:
                    r = json.loads(raw)
                    st["side"][str(r["id"])] = [r.get("label") or "", r["text"]]
                    if r.get("lossy") and r.get("gk", self.key) == self.key:   # a lossy output of THIS context
                        _guard(st).add(r["lossy"])                              # [guard t, cmd key, recovery file]
                except (ValueError, KeyError, TypeError):
                    continue
            st["side_off"] += end
            changed = True
        if size > st["off"]:
            skip = str(self.ev.get("tool_use_id") or "")
            with open(tr, "rb") as f:     # streamed in batches: the first call on a 200 MB transcript stays small
                f.seek(st["off"])
                recs, pos = [], st["off"]
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break             # a half-written last line is parsed next time
                    pos += len(raw)
                    try:
                        recs.append(json.loads(raw))
                    except ValueError:
                        pass
                    if len(recs) >= 2000:
                        catch_up(st, recs, skip)
                        recs = []
                catch_up(st, recs, skip)
            st["off"] = pos
            changed = True
        return changed

    def dedup(self, text: str) -> tuple[str, dict]:
        s = self.load() if len(text) >= self.min_len else None
        if s is None:
            return text, {"refs": [], "lines_removed": 0, "chars_saved": 0}
        return s.dedup(text)

    def guard(self):
        """The session's refetch guard with the current call counted (state caught up and saved under the lock);
        None without a session id or transcript."""
        if self.g is None:
            self.load()
        return self.g

    def register(self, rendered: str, label: str, lossy=None) -> None:
        """lossy: the Guard record of a lossy output (process() rec["lossy"]), so later calls can spot its refetch."""
        if not self.sid:
            return
        r = {"id": str(self.ev.get("tool_use_id") or ""), "label": label, "text": rendered}
        if lossy:
            r["lossy"], r["gk"] = lossy, self.key   # the side file is shared with subagents; the guard is not
        with _popen(_state(self.dir) / f"{self.sid}.side.jsonl", "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def hook_session(ev: dict, state_dir: str | os.PathLike, **kw) -> "HookSession | None":
    """The session's earlier rendered outputs that are still in context, loaded lazily (HookSession)."""
    return HookSession(ev, state_dir, **kw) if _sid(ev) else None


def hook_dedup(ev: dict, text: str, label: str, state_dir: str | os.PathLike, min_len: int = MIN_LEN,
               **kw) -> tuple[str, dict]:
    """PostToolUse: dedup `text` against this session's earlier rendered outputs; the caller passes the FINAL rendered
    text back through hook_register after its own compression."""
    hs = hook_session(ev, state_dir, **kw)
    if hs is None or len(text) < min_len:
        return text, {"refs": []}
    s = hs.load()
    if s is None:
        return text, {"refs": [], "skipped": "no transcript"}
    return s.dedup(text)


def hook_register(ev: dict, rendered: str, label: str, state_dir: str | os.PathLike) -> None:
    hs = hook_session(ev, state_dir)
    if hs is not None:
        hs.register(rendered, label)


def check_transcript(path: str, **kw) -> dict:
    """Inverse-check harness (crossturn lab, reused as a test): replay a Claude Code transcript the way the hook sees
    it (state per tool result, after catch_up of everything before it), dedup every tool result's text and check
    expand(new) == text, determinism, and that the state survives a JSON round trip. Pure; prints nothing."""
    st, c = _new_state(), {"outputs": 0, "fired": 0, "refs": 0, "inverse_fail": 0, "nondeterministic": 0,
                           "chars_saved": 0}
    pending: list = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        cont = msg.get("content") if isinstance(msg, dict) else None
        if rec.get("type") == "user" and isinstance(cont, list) and not rec.get("isSidechain"):
            for b in cont:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    catch_up(st, pending)
                    pending = []
                    st = json.loads(json.dumps(st))
                    text = _text_of(b.get("content"))
                    s = session_from_state(st, **kw)
                    new, info = s.dedup(text)
                    c["outputs"] += 1
                    if new != text:
                        c["fired"] += 1
                        c["refs"] += len(info["refs"])
                        c["chars_saved"] += len(text) - len(new)
                        c["inverse_fail"] += expand(new, s) != text
                        c["nondeterministic"] += session_from_state(st, **kw).dedup(text)[0] != new
                    tid = str(b.get("tool_use_id") or "")
                    st["side"][tid] = [st["labels"].get(tid, ""), new]   # the hook's rendered text
        pending.append(rec)
    return c


# ---------------------------------------------------------------- request-time dedup (Hermes / Pi / OpenCode plugins)
# A plugin's tool-result hook can't know what the harness will still hold in context later: Hermes compaction keeps
# head + tail and summarizes the middle, micro-compaction folds old exchanges, and the proactive prune turns old
# results into stubs; OpenCode's prune shows old outputs as "[Old tool result content cleared]"; Pi switches branches
# (session_tree) and compacts. A marker written at tool time can then point at an output that left the context. So
# these plugins do not dedup at tool time. They dedup each model REQUEST, from the message list the harness is about
# to send (Hermes llm_request middleware, Pi `context` event, OpenCode experimental.chat.messages.transform), exactly
# like ctxprune_proxy: every reference points at an earlier tool result IN THAT REQUEST, so it can't dangle, and the
# numbering restarts with whatever the harness kept after a compaction. The rewrite is request-local (the stored
# session keeps the full outputs, so the harness's own compaction summarizer reads them) and a pure function of the
# request prefix, so the prompt cache sees identical bytes for an unchanged prefix. No per-session state is needed.
# Events, in request order (the plugin builds them from its harness's message format):
#   {"t": "call", "id": call id, "name": tool, "args": dict | JSON string}     a tool call the model made (its input
#                                                                              becomes referable as `input #k`)
#   {"t": "result", "id": call id, "name": tool, "text": str, "dedup": bool}  a tool result exactly as the model
#                                                                              sees it; dedup False = register only
# Every result is registered (its number #k and the age cap count every result, like the Claude Code hook); only
# results of tools outside PLUGIN_NO_DEDUP (file views, edits, memory/todo, skills) are rewritten.
PLUGIN_NO_DEDUP = {
    # file views and edits (the agent edits against exact bytes), memory / todo / skill bodies
    "hermes": re.compile(r"(?i)^(read_file|patch|write_file|edit_file|create_file|apply_patch|str_replace_editor|"
                         r"view|view_file|view_image|notebook_.*|memory|todo|skill_.*|skills_.*)$"),
    "pi": re.compile(r"(?i)^(read|edit|write|multiedit|multi_edit|apply_patch|patch|notebook.*)$"),
    "opencode": re.compile(r"(?i)^(read|edit|write|multiedit|patch|apply_patch|todowrite|todoread)$"),
}
_NO_DEDUP_ANY = re.compile("|".join(p.pattern.replace("(?i)", "") for p in PLUGIN_NO_DEDUP.values()), re.I)
# Hermes wraps results in JSON objects ({"output": ..., "exit_code": 1} for a failing terminal command, which the
# plugin leaves as JSON so Hermes still reads exit_code): dedup runs on the multi-line string field and the result
# is re-serialized byte-exactly around it.
PLUGIN_JSON = {"hermes"}


def plugin_label(name: str, args) -> str:
    """The marker hint for a tool call: its shell command, else tool + main argument."""
    a = args if isinstance(args, dict) else {}
    cmd = a.get("command", a.get("cmd"))
    if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd):
        import shlex
        cmd = shlex.join(cmd)
    if isinstance(cmd, str) and cmd.strip():
        return cmd[:200]
    arg = next((str(v) for k in ("file_path", "filePath", "path", "pattern", "query", "url", "action", "description")
                if isinstance(v := a.get(k), (str, int, float)) and str(v)), "")
    return f"{name or ''} {arg}".strip()[:200]


def plugin_inputs(name: str, args) -> list[tuple[str, str]]:
    """(label, text) of a tool call's input worth referencing, in any harness's argument names (call_inputs for
    Claude Code names): file content written, an edit's new text, a multi-line shell command (heredoc bodies)."""
    if not isinstance(args, dict):
        return []
    path = next((str(args[k]) for k in ("file_path", "filePath", "path") if isinstance(args.get(k), str)), "")
    out = []
    for k in ("content", "new_string", "newString", "newText", "new_str"):
        if isinstance(args.get(k), str):
            out.append((f"{name} {path}".strip(), args[k]))
    for e in args.get("edits") or [] if isinstance(args.get("edits"), list) else []:
        for k in ("new_string", "newString", "newText"):
            if isinstance(e, dict) and isinstance(e.get(k), str):
                out.append((f"{name} {path}".strip(), e[k]))
    cmd = args.get("command")
    if isinstance(cmd, str) and "\n" in cmd:
        out.append((cmd.split("\n", 1)[0][:200], cmd))
    return [(lab, t) for lab, t in out if t.count("\n") >= 2]


def _json_body(text: str):
    """(key, value) of the multi-line string field of a JSON object result ("output" first, else the longest), or
    None."""
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    multi = [(k, v) for k, v in obj.items() if isinstance(v, str) and "\n" in v]
    if not multi:
        return None
    return next(((k, v) for k, v in multi if k == "output"), max(multi, key=lambda kv: len(kv[1])))


def _json_put(text: str, key: str, old: str, new: str) -> str | None:
    """`text` with the JSON string `old` (field `key`) replaced by `new`, in the same escaping; None unless the result
    parses to the same object with only that field changed."""
    try:
        want = dict(json.loads(text))
        want[key] = new
        for ascii_ in (False, True):
            enc = json.dumps(old, ensure_ascii=ascii_)[1:-1]
            if enc and text.count(enc) == 1:
                out = text.replace(enc, json.dumps(new, ensure_ascii=ascii_)[1:-1])
                if json.loads(out) == want:
                    return out
    except (ValueError, TypeError):
        pass
    return None


def _args(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _chain(prev: str, ev) -> str:
    import hashlib
    return hashlib.sha256(prev.encode() + json.dumps(ev, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                                                     default=str).encode()).hexdigest()


def context_dedup(events: list, harness: str = "", check: bool = False, memo=None, pin_only: bool = False,
                  rerender: bool = False, ver: str = "", seed=None, **kw) -> tuple[dict, dict]:
    """Dedup one request's tool results against the earlier ones IN THE SAME REQUEST (see the section comment).
    -> ({event index: new text for that result}, stats{results, fired, chars_before, chars_saved, inverse_fail}).
    Deterministic in the events; a result's rewrite depends only on the events before it. check=True verifies
    expand() == the original for every rewritten result (tests, measurement). Never raises: on error, no rewrites.

    memo (render_store.RenderStore, table "pin"): first-render pins. Each result's key chains the harness and every
    earlier call (name, args) and result (name, ORIGINAL text, rewritable flag) of the request, so a resent history
    gets the same keys: a pinned result gets exactly the text it was first sent with (an upgrade, a changed rule or
    threshold can't touch it), and only new results are deduped by the current code. pin_only (dedup off or
    CTXPRUNE_OFF=1 after dedup was on): pinned results keep their text, new ones go out unchanged and, in a
    conversation that has pins, are pinned unchanged (so turning dedup back on doesn't rewrite them). rerender:
    pins ignored and overwritten (explicit full re-render; with pin_only: everything back to the original). seed (any
    JSON: the first user message ...) starts the chain, so conversations that open with the same tool output differ."""
    stats = {"results": 0, "fired": 0, "chars_before": 0, "chars_saved": 0, "inverse_fail": 0}
    try:
        skip = PLUGIN_NO_DEDUP.get(harness, _NO_DEDUP_ANY)
        js = harness in PLUGIN_JSON
        s = DedupSession(**kw)
        calls: dict = {}
        out: dict = {}
        chain = _chain("ctxprune-context-1", [harness, seed]) if memo is not None else ""
        puts, pinned = [], 0
        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                continue
            if ev.get("t") == "call":
                name, args = str(ev.get("name") or ""), _args(ev.get("args"))
                calls[str(ev.get("id"))] = (name, args)
                if memo is not None:
                    chain = _chain(chain, ["c", name, args])
                for lab, t in plugin_inputs(name, args):
                    s.register_input(t, lab)
                continue
            text = ev.get("text")
            if ev.get("t") != "result" or not isinstance(text, str) or not text:
                continue
            name, args = calls.get(str(ev.get("id")), (str(ev.get("name") or ""), {}))
            name = name or str(ev.get("name") or "")
            body = _json_body(text) if js else None
            view = body[1] if body else text
            new_view = view
            stats["results"] += 1
            stats["chars_before"] += len(text)
            key = None
            if memo is not None:
                key = chain = _chain(chain, ["r", name, text, bool(ev.get("dedup", True))])
                hit, val = (False, None) if rerender else memo.get("pin", key)
                if hit:
                    pinned += 1
                    if isinstance(val, str) and val != text:
                        out[i] = val
                        pb = _json_body(val) if js else None
                        new_view = pb[1] if pb else val
                        stats["fired"] += 1
                        stats["chars_saved"] += len(text) - len(val)
                    s.register(new_view, plugin_label(name, args))
                    continue
            if not pin_only and ev.get("dedup", True) and not skip.match(name) and len(view) >= s.min_len:
                new_view, _ = s.dedup(view)   # (dedup doesn't change s: s is still the state before this result)
                if new_view != view:
                    new = _json_put(text, body[0], view, new_view) if body else new_view
                    if new is None:
                        new_view = view
                    else:
                        out[i] = new
                        stats["fired"] += 1
                        stats["chars_saved"] += len(text) - len(new)
                        if check and expand(new_view, s) != view:
                            stats["inverse_fail"] += 1
            if key is not None:
                puts.append((key, out.get(i)))
            s.register(new_view, plugin_label(name, args))
        # pin_only: only a conversation that already has pins needs its new raw results pinned (dedup on later must
        # not rewrite them); one that never had dedup gets no entries
        if memo is not None and (not pin_only or pinned or rerender):
            for key, val in puts:   # first stored wins: two concurrent requests computing the same new result
                memo.put("pin", key, val, ver, replace=rerender)   # differ only if their settings did
        stats["pinned"] = pinned
        return out, stats
    except Exception as e:  # noqa: BLE001 - fail open: send the request as it is
        stats["error"] = repr(e)[:200]
        return {}, stats


# ---------------------------------------------------------------- simulation: collect (box, network)
def cmd_collect(a) -> None:
    import time
    sys.path.insert(0, str(Path(__file__).parent))
    from build_hindsight import STREAMS, iter_rows, load_pins
    from build_replay import producing_call, train_repo_keys
    from build_usage_eval import items_from
    sys.path.insert(0, str(Path.home() / "ctxprune"))
    from ctxprune.chunking import split_of
    from ctxprune.sources import repo_key
    print("pinned", load_pins(a.pins), flush=True)
    train_keys = train_repo_keys(a.train_repos)
    print(len(train_keys), "training repo keys excluded", flush=True)
    shares = {k: float(v) for k, v in (x.split("=") for x in a.shares.split(",") if x.strip())}
    t_end = time.time() + a.max_minutes * 60 if a.max_minutes else None
    a.out_dir.mkdir(parents=True, exist_ok=True)
    ft, fi = (a.out_dir / "traj.jsonl").open("w"), (a.out_dir / "items.jsonl").open("w")
    n_items = 0
    for name, share in shares.items():
        st = STREAMS[name]
        cap, got, per_repo = int(a.max_traj * share), 0, {}
        for r in iter_rows(st, a.seed):
            if got >= cap or (t_end and time.time() > t_end):
                break
            g = st.get(r)
            if g is None:
                continue
            repo, ref, raw = g
            if split_of(repo) != "test" or repo_key(repo) in train_keys or per_repo.get(repo, 0) >= a.per_repo:
                continue
            msgs = st.norm(list(raw), False)   # raw observations: what the agent saw
            used = {it["turn"]: it for it in items_from(msgs, repo, str(ref), name, 3, a.v2_min)}
            outs = []
            for t, m in enumerate(msgs):
                if m["role"] != "tool":
                    continue
                text = m.get("content") or ""
                try:
                    _, command, cmd, intent = producing_call(msgs, t, st.fmt)
                except Exception:
                    command, cmd, intent = None, "", ""
                o = {"turn": t, "tool": m.get("name") or "bash", "label": command or cmd.replace("\n", " ")[:60],
                     "text": text}
                if len(text) >= a.v2_min:
                    o["item"] = n_items
                    it = used.get(t)
                    if it:
                        o["used_tokens"], o["used_lines"] = it["used_tokens"], it["used_lines"]
                    fi.write(json.dumps({"intent": it["intent"] if it else intent, "text": text}) + "\n")
                    n_items += 1
                outs.append(o)
            if not outs:
                continue
            ft.write(json.dumps({"source": name, "repo": repo, "ref": str(ref), "outputs": outs}) + "\n")
            got += 1
            per_repo[repo] = per_repo.get(repo, 0) + 1
        print(name, got, "trajectories from", len(per_repo), "repos", flush=True)
    ft.close()
    fi.close()
    print("items for v2 scoring:", n_items, flush=True)


# ---------------------------------------------------------------- simulation: arms (CPU)
def v2_select(lines: list[str], scores: list[float], target_tokens: int, count, force: set[int]) -> str:
    """frozen-v2 line selection (methods._select, forced marker lines) fitted to <= target o200k tokens."""
    sys.path.insert(0, str(Path(__file__).parent))
    import methods
    text = "\n".join(lines)
    if count(text) <= target_tokens:
        return text
    lo, hi, best = 0.0, 1.0, None
    for _ in range(10):
        mid = (lo + hi) / 2
        out = methods._select(lines, scores, mid * len(text), force=force)
        if count(out) <= target_tokens:
            best, lo = out, mid
        else:
            hi = mid
    return best if best is not None else methods._select(lines, scores, 0.0, force=force)


def line_scores(text: str, sc: list[float]) -> tuple[list[float], bool]:
    """Per-"\n"-line scores from a train_lines score list, which is indexed by text.splitlines() (that also splits at
    \r, \x0c, \x85, \u2028, ...). A "\n"-line gets the max of its pieces (0 if it has none, e.g. a trailing "").
    -> (scores, aligned: piece count == len(sc))"""
    n_lines = text.count("\n") + 1
    out = [0.0] * n_lines
    pieces = text.splitlines(keepends=True)
    nl = 0
    for i, pc in enumerate(pieces):
        if i < len(sc):
            out[nl] = max(out[nl], sc[i])
        nl += pc.count("\n")
    return out, len(pieces) == len(sc)


def kind_of(o: dict) -> str:
    lab = (o.get("label") or "").lstrip()
    if re.match(r"(cat|sed|head|tail|nl|less|view)\b", lab) or o["tool"] in ("str_replace_editor", "Read"):
        return "view"
    if re.match(r"(grep|rg|git grep|find|ag|ack)\b", lab) or o["tool"] in ("Grep", "Glob"):
        return "search"
    return "other"


def cmd_simulate(a) -> None:
    import random
    from eval_usage import enc
    count = enc()
    scores = json.load(open(a.scores)) if a.scores and Path(a.scores).exists() else None
    if scores is None:
        print("WARNING: no v2 scores -> v2 arms skipped", flush=True)
    rate = a.rate
    trajs = [json.loads(l) for l in open(a.out_dir / "traj.jsonl")]
    arms = ["none", "dedup"] + (["v2", "dedup+v2_i", "dedup+v2_ii"] if scores else [])
    per_traj, fires, qual = [], [], {k: [] for k in arms}
    bad_lossless = bad_det = misaligned = 0
    for tr in trajs:
        sess = {k: DedupSession(min_run=a.min_run, min_chars=a.min_chars, count=count) for k in arms if "dedup" in k}
        tok = {k: 0 for k in arms}
        for o in tr["outputs"]:
            text, lab = o["text"], o["label"]
            n0 = count(text)
            tok["none"] += n0
            sc = None
            if scores is not None and "item" in o:
                sc, aligned = line_scores(text, scores[o["item"]])
                misaligned += not aligned
            used = o.get("used_tokens")
            # dedup only
            s = sess["dedup"]
            snap_n = len(s.outputs)
            new, info = s.dedup(text) if len(text) >= a.dedup_min else (text, {"refs": []})
            if info["refs"]:
                st = DedupSession(min_run=a.min_run, min_chars=a.min_chars, count=count)
                st.outputs, st.index = s.outputs[:snap_n], s.index
                if expand(new, st) != text:
                    bad_lossless += 1
                if s.dedup(text)[0] != new:
                    bad_det += 1
            if len(text) >= a.dedup_min:
                fires.append({"source": tr["source"], "kind": kind_of(o), "fired": bool(info["refs"]),
                              "saved": n0 - count(new), "tokens": n0})
            s.register(new, lab)
            tok["dedup"] += count(new)
            if used:
                qual["none"].append(1.0)
                qual["dedup"].append(_surv(used, new, s.ref_lines(info["refs"])))
            if scores is None:
                continue
            # v2 alone (outputs >= v2_min, budget rate x tokens)
            v2o = v2_select(text.split("\n"), sc, int(rate * n0), count, set()) if sc is not None else text
            tok["v2"] += count(v2o)
            if used:
                qual["v2"].append(_surv(used, v2o, []))
            # dedup first, then v2 on the remaining lines (markers forced)
            for arm in ("dedup+v2_i", "dedup+v2_ii"):
                s2 = sess[arm]
                d, inf2 = s2.dedup(text) if len(text) >= a.dedup_min else (text, {"refs": []})
                if sc is not None:
                    dl = d.split("\n")
                    dsc, force, src = [], set(), 0
                    removed = {r[3]: r[4] for r in inf2["refs"]}
                    for idx, ln in enumerate(dl):   # map scores of surviving lines; marker -> forced
                        if src in removed and MARK_RE.match(ln):
                            force.add(idx)
                            dsc.append(1.0)
                            src += removed[src]
                        else:
                            dsc.append(sc[src] if src < len(sc) else 0.0)
                            src += 1
                    tgt = int(rate * n0) if arm.endswith("_i") else int(rate * count(d))
                    d = v2_select(dl, dsc, tgt, count, force)
                s2.register(d, lab)
                tok[arm] += count(d)
                if used:
                    qual[arm].append(_surv(used, d, s2.ref_lines(inf2["refs"])))
        per_traj.append({"source": tr["source"], **tok})
    res = {"n_traj": len(trajs), "params": vars(a) | {"out_dir": str(a.out_dir)}, "lossless_failures": bad_lossless,
           "determinism_failures": bad_det, "score_alignment_mismatches": misaligned, "arms": {}}
    res["params"].pop("func", None)
    tot0 = sum(p["none"] for p in per_traj)
    rng = random.Random(0)
    for k in arms:
        saved = [1 - p[k] / p["none"] for p in per_traj if p["none"]]
        boots = sorted(sum(rng.choice(saved) for _ in saved) / len(saved) for _ in range(1000))
        res["arms"][k] = {"agg_tokens_saved_pct": round(100 * (1 - sum(p[k] for p in per_traj) / tot0), 2),
                          "mean_traj_saved_pct": round(100 * sum(saved) / len(saved), 2),
                          "ci95": [round(100 * boots[25], 2), round(100 * boots[974], 2)],
                          "traj_with_saving_pct": round(100 * sum(x > 0.001 for x in saved) / len(saved), 1),
                          "used_tok_survival_ge6k": round(100 * sum(qual[k]) / len(qual[k]), 1) if qual[k] else None,
                          "n_quality_items": len(qual[k])}
    res["by_source"] = {}
    for src in sorted({p["source"] for p in per_traj}):
        ps = [p for p in per_traj if p["source"] == src]
        t0 = sum(p["none"] for p in ps)
        res["by_source"][src] = {"n_traj": len(ps), **{k: round(100 * (1 - sum(p[k] for p in ps) / t0), 2)
                                                       for k in arms if k != "none"}}
    res["fires"] = {}
    for key in ["all"] + sorted({f["kind"] for f in fires}) + sorted({"src:" + f["source"] for f in fires}):
        fs = [f for f in fires if key == "all" or f["kind"] == key or "src:" + f["source"] == key]
        if fs:
            res["fires"][key] = {"n_outputs": len(fs), "fire_pct": round(100 * sum(f["fired"] for f in fs) / len(fs), 1),
                                 "tokens_saved_pct": round(100 * sum(f["saved"] for f in fs) /
                                                           max(1, sum(f["tokens"] for f in fs)), 2)}
    (a.out_dir / "results.json").write_text(json.dumps(res, indent=1))
    md = [f"# E2 cross-turn dedup ({res['n_traj']} held-out trajectories)", "",
          f"Rule: runs of >= {a.min_run} identical consecutive lines (>= {a.min_chars} non-blank chars) from one earlier "
          f"rendered output -> one marker; applied to outputs >= {a.dedup_min} chars. v2 = frozen line model at "
          f"{rate} on outputs >= {a.v2_min} chars (scores from the original output). dedup+v2_i: v2 budget = {rate} x "
          f"ORIGINAL tokens (same cost as v2, more novel lines kept); _ii: {rate} x dedup'd tokens (extra saving). "
          "Tokens = o200k over ALL tool outputs of a trajectory.", "",
          f"Checks: lossless (expand == original) failures {bad_lossless}; determinism failures {bad_det}; v2 score "
          f"files misaligned with the output's lines {misaligned}.", "",
          "| arm | tokens saved (aggregate) | mean per trajectory [95% CI] | trajectories with a saving | "
          "used tokens surviving, outputs >= 6k (n) |", "|---|---|---|---|---|"]
    for k, v in res["arms"].items():
        md.append(f"| {k} | {v['agg_tokens_saved_pct']}% | {v['mean_traj_saved_pct']}% {v['ci95']} | "
                  f"{v['traj_with_saving_pct']}% | {v['used_tok_survival_ge6k']} ({v['n_quality_items']}) |")
    md += ["", "Used-token survival counts a token as kept when it is in the output OR in the earlier rendered lines a "
           "marker points to (still in context).", "", "## How often dedup fires (outputs >= dedup-min)", "",
           "| slice | outputs | fired % | tokens saved % |", "|---|---|---|---|"]
    for key, v in res["fires"].items():
        md.append(f"| {key} | {v['n_outputs']} | {v['fire_pct']} | {v['tokens_saved_pct']} |")
    md += ["", "## Tokens saved by source (aggregate %)", "", "| source | n | " + " | ".join(k for k in arms if k != "none")
           + " |", "|---" * (2 + len(arms) - 1) + "|"]
    for src, v in res["by_source"].items():
        md.append(f"| {src} | {v['n_traj']} | " + " | ".join(str(v[k]) for k in arms if k != "none") + " |")
    (a.out_dir / "table.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


def _surv(used: list[str], out: str, ref_lines: list[str]) -> float:
    ctx = out + "\n" + "\n".join(ref_lines)
    return sum(t in ctx for t in used) / len(used) if used else 1.0


# ---------------------------------------------------------------- self-test
def self_test() -> None:
    file_a = [f"def func_{i}(x):\n    return compute_value_{i}(x) + {i}  # body line" for i in range(30)]
    a_text = "\n".join(file_a)
    s = DedupSession(min_run=5, min_chars=100)
    n1, i1 = s.process(a_text, "cat app.py")
    assert n1 == a_text and not i1["refs"]
    # second view: header + lines 11-50 of the first output + new tail
    a_lines = a_text.split("\n")
    b_text = "\n".join(["$ sed -n 10,60p app.py"] + a_lines[10:50] + ["new line one is here", "another new line"])
    import copy
    snap = copy.deepcopy(s)
    n2, i2 = s.dedup(b_text)
    assert i2["refs"] == [(1, 11, 50, 1, 40, "output")], i2["refs"]
    assert n2.split("\n")[1] == "[ctxprune: lines 11–50 identical to output #1 above (`cat app.py`)]", n2
    assert expand(n2, snap) == b_text
    assert s.dedup(b_text)[0] == n2   # deterministic
    s.register(n2, "sed -n 10,60p app.py")
    # third: re-run of the first command -> references the LATEST earlier output on ties? output 1 is longer: 60 vs 40
    n3, i3 = s.dedup(a_text)      # P1: identical to output #1 as a whole
    assert n3 == "[ctxprune: output identical to output #1 above (`cat app.py`)]" and i3["whole"], n3
    assert expand(n3, s) == a_text
    s.whole = False
    n3, i3 = s.dedup(a_text)
    assert i3["refs"][0][0] == 1 and i3["refs"][0][1:3] == (1, 60), i3["refs"]
    s.whole = True
    # separator lines: never an anchor, and a run must beat its marker on the BPE-like estimate too
    sp = DedupSession()
    sp.register("\n".join(["=" * 70, "== section ==", "=" * 70, "-" * 70, "x = 1"]), "make test")
    sep_run = "\n".join(["=" * 70, "== section ==", "=" * 70, "-" * 70])
    assert sp.dedup("new\n" + sep_run)[1]["refs"] == [], sp.dedup("new\n" + sep_run)
    # score alignment: train_lines indexes text.splitlines() (splits at \r, \x0c too); dedup works on "\n"-lines
    ls, ok = line_scores("a\rb\nc\x0cd\n\ne\n", [0.1, 0.9, 0.2, 0.3, 0.0, 0.5])
    assert ok and ls == [0.9, 0.3, 0.0, 0.5, 0.0], ls
    assert line_scores("x\ny", [0.4])[1] is False
    # short / trivial runs are kept
    s2 = DedupSession(min_run=5, min_chars=200)
    s2.register("}\n}\n}\n}\n}\n}\nx = 1", "a")
    assert s2.dedup("}\n}\n}\n}\n}\n}\nx = 1")[1]["refs"] == []
    # markers are never anchors; dedup_history is pure
    h = dedup_history([("cat app.py", a_text), ("cat app.py", a_text)], min_run=5, min_chars=100)
    assert h == dedup_history([("cat app.py", a_text), ("cat app.py", a_text)], min_run=5, min_chars=100)
    assert h[1] == "[ctxprune: output identical to output #1 above (`cat app.py`)]", h[1]
    # red team #7/#8: runs never cross a ctxprune fence; expand never raises on a past-session / malformed marker
    blk = ["[ctxprune:file]", "src/app/x.py"] + [f"{i}:    value_{i} = compute_value({i})  # x" for i in range(12)] + \
        ["[/ctxprune]"] + [f"after line {i} with enough text here" for i in range(6)]
    s3 = DedupSession(min_run=3, min_chars=40)
    s3.register("\n".join(blk), "rg x")
    s3.whole = False
    d3, inf3 = s3.dedup("\n".join(blk))
    assert d3 == "[ctxprune: lines 1–21 identical to output #1 above (`rg x`)]", d3   # whole block + tail
    s3.whole = True
    for a0, b0, k0 in ((r[1], r[2], r[0]) for r in inf3["refs"]):   # whole blocks only
        ref = s3.outputs[k0 - 1][1][a0 - 1:b0]
        assert sum(_kind(x) == OPEN for x in ref) == sum(_kind(x) == CLOSE for x in ref), ref
    d4, inf4 = s3.dedup("\n".join(blk[3:]))     # starts inside the block: the run stops before the close fence
    assert d4.split("\n")[0].startswith("[ctxprune: lines") and "[/ctxprune]" in d4.split("\n"), d4
    s3.register("x\n[… lines 2–9 omitted …]\n" + "\n".join(blk[5:9]), "y")   # lossy pointers never go into a ref
    assert all("[…" not in ln for ln in s3.ref_lines(s3.dedup("[… lines 2–9 omitted …]\n" + "\n".join(blk[5:9]))[1]["refs"]))
    assert expand("[ctxprune: lines 1–3 identical to output #99 above]", s3) == \
        "[ctxprune: lines 1–3 identical to output #99 above]"
    assert expand("[ctxprune: lines 1–3 identical to output #" + "9" * 5000 + " above]", s3).endswith(" above]")
    # P3 thresholds: a 2-line run of >= 40 chars; inputs (Write/Edit/heredoc) referable with their own numbering
    s4 = DedupSession()
    s4.register("alpha line with quite a few more words in it than before, okay\nbeta line with more words here and some more to make it longer\nother", "make")
    d5, i5 = s4.dedup("head\nalpha line with quite a few more words in it than before, okay\nbeta line with more words here and some more to make it longer\ntail")
    assert i5["refs"] == [(1, 1, 2, 1, 2, "output")], (d5, i5)
    body = [f"    def method_{i}(self):  return self.value_{i} * {i}" for i in range(6)]
    s4.register_input("\n".join(["class A:"] + body), "Write src/a.py")
    d6, i6 = s4.dedup("$ cat src/a.py\n" + "\n".join(["class A:"] + body))
    assert d6 == "$ cat src/a.py\n[ctxprune: lines 1–7 identical to input #1 above (`Write src/a.py`)]", d6
    assert expand(d6, s4) == "$ cat src/a.py\n" + "\n".join(["class A:"] + body)
    # age cap: only the last MAX_AGE outputs are referenced; reset() drops every candidate, numbering stays
    s5 = DedupSession(max_age=3)
    s5.register(a_text, "cat app.py")
    for i in range(3):
        s5.register(f"filler output {i}", "x")
    assert s5.dedup(a_text)[0] == a_text
    s5.register(a_text, "cat app.py")
    assert s5.dedup(a_text)[0].startswith("[ctxprune: output identical to output #5 above")
    s5.reset()
    assert s5.dedup(a_text)[0] == a_text and s5.register("y", "y") == 6
    # strip()-ing chat templates (Qwen3.6/3.8): never reference into a source that starts with a blank line, nor its
    # stripped edges (first line's indentation, trailing blank lines, last line's trailing spaces); inputs alike
    rows = [f"row {i} of the result table with enough text to count" for i in range(8)]
    for src_text, ok in (("\n\n" + "\n".join(rows), False), ("\n".join(rows) + "\n\n", True),
                         ("   " + "\n".join(rows), True)):
        for as_input in (False, True):
            s6 = DedupSession()
            s6.register_input(src_text, "Write t.txt") if as_input else s6.register(src_text, "cat t.txt")
            d7, i7 = s6.dedup("head\n" + "\n".join(rows) + "\ntail")
            assert bool(i7["refs"]) == ok, (src_text[:8], as_input, d7)
            for r in i7["refs"]:
                assert r[1] >= 2 if src_text.startswith(" ") else True, r      # indented first line not referenced
                strip_lines = src_text.strip().split("\n")
                assert s6.ref_lines([r]) == strip_lines[r[1] - 1:r[2]], r   # same lines in the stripped form
            assert expand(d7, s6) == "head\n" + "\n".join(rows) + "\ntail"
    s7 = DedupSession()
    s7.register("\n" + "\n".join(rows), "x")
    assert s7.dedup("\n" + "\n".join(rows))[0].startswith("[ctxprune: output identical")   # whole: still fine
    # hook support: sidecar + transcript (compaction resets)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "t.jsonl"
        recs = [{"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": a_text}]}}]
        tp.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        ev = {"session_id": "S1", "transcript_path": str(tp), "tool_use_id": "t2"}
        hook_register({"session_id": "S1", "tool_use_id": "t1"}, a_text, "cat app.py", d)
        new, info = hook_dedup(ev, b_text, "sed", d, min_len=10, min_run=5, min_chars=100)
        assert info["refs"] and "output #1" in new
        tp.write_text(tp.read_text() + json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")
        assert hook_dedup(ev, b_text, "sed", d, min_len=10, min_run=5, min_chars=100)[1]["refs"] == []
        assert hook_dedup({**ev, "transcript_path": str(Path(d) / "missing")}, b_text, "x", d, min_len=10)[0] == b_text
    print("dedup self-test OK")


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--max-traj", type=int, default=600)
    c.add_argument("--shares", default="kwai=0.25,swe_smith=0.25,swe_rebench=0.5")
    c.add_argument("--per-repo", type=int, default=40)
    c.add_argument("--train-repos", nargs="*", default=[])
    c.add_argument("--v2-min", type=int, default=6000)
    c.add_argument("--pins", default=str(Path(__file__).resolve().parent.parent / "pins/v2/revisions.json"))
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--max-minutes", type=float, default=0)
    s = sub.add_parser("simulate")
    s.add_argument("--out-dir", type=Path, required=True)
    s.add_argument("--scores", default="")
    s.add_argument("--rate", type=float, default=0.5)
    s.add_argument("--min-run", type=int, default=5)
    s.add_argument("--min-chars", type=int, default=200)
    s.add_argument("--dedup-min", type=int, default=1000)
    s.add_argument("--v2-min", type=int, default=6000)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    cmd_collect(a) if a.cmd == "collect" else cmd_simulate(a)


if __name__ == "__main__":
    main()
