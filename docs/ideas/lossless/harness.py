"""Fidelity gate for the lossless ideas (tag [L] in docs/ideas/ideas.jsonl).

Each [L] idea gets one registered `case`: a small reference prototype of the transform (or, for ideas that are
policies rather than transforms, a deterministic simulation) plus built-in fixtures and an oracle. Running a case
proves the idea can be lossless *on its own fixtures and on mutated variants of them*; it does not replace the
real-capture measurement in each idea's prose "Test:" line, and it says nothing about whether an LLM reads the
transformed text as well as the original (see #183).

Three kinds of case, strictest first:

  roundtrip   decode(encode(x)) == x byte for byte.          (`decode=` given, `loses=""`)
  equivalent  no inverse, but an independent oracle(original, encoded) says both carry the same information under
              a stated relation; `loses=` names exactly what is NOT preserved ("blank-line count beyond one").
  sim         a policy or stateful behaviour, checked by a function that raises AssertionError on failure.

Rules enforced for every transform case, on each fixture AND on ~40 seeded mutations of it:
  1. total          encode never raises, whatever the input.
  2. fidelity       roundtrip or oracle holds.
  3. never grows    len(encode(x)) <= len(x); a transform that cannot help must hand the input back untouched.
  4. gain           fixtures with shrinks=True shrink by >= min_gain (characters).
  5. leaves alone   fixtures with shrinks=False come back byte-identical (negatives: wrong input kind, or text that
                    must stay verbatim, such as diffs and code about to be edited).
  6. idempotent     encode(encode(x)) == encode(x) unless the case sets idempotent=False.

Fixtures with shrinks=None are adversarial inputs (text that looks like the encoder's own output, a stray sentinel,
odd bytes). Only rules 1 and 2 apply to them and to their mutations: a byte-exact encoding is injective, so it cannot
shrink anything without growing something, and the escape path for in-band ambiguity must be allowed to grow.
Every roundtrip case that signals in-band (a legend header, an alias sigil) needs such a fixture.

Characters, not tokens, gate the result so it is deterministic and offline. Token savings are reported alongside:
o200k via tiktoken if it is installed and its vocabulary loads, otherwise a regex word-piece proxy (labelled).

Prototypes in cases_*.py exist to be tested; they are not shipped code and use only the standard library.
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------------------------------------------
# tokens (informational only)

_PIECE = re.compile(r"[A-Za-z]{1,6}|\d{1,3}|\s+|[^\sA-Za-z\d]")
_enc = None
_enc_tried = False
TOKENIZER = "proxy"


def _load_o200k():
    global _enc, _enc_tried, TOKENIZER
    if _enc_tried:
        return _enc
    _enc_tried = True
    if os.environ.get("CTXPRUNE_IDEAS_TOKENIZER") == "proxy":
        return None
    try:
        import tiktoken

        _enc = tiktoken.get_encoding("o200k_base")
        TOKENIZER = "o200k"
    except Exception:
        _enc = None
    return _enc


def tokens(text: str) -> int:
    """o200k token count when tiktoken works, else a deterministic word-piece proxy (see TOKENIZER)."""
    enc = _load_o200k()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return len(_PIECE.findall(text))


# ---------------------------------------------------------------------------------------------------------------
# mutation fuzzing

_SNIPPETS = ["\r", "\r\n", "\x1b[0m", "\x1b[", "\x1b]8;;", "﻿", "​", " ", '"', "'", "\\", "{", "}", "[", "]",
             "\t", "  ", "\n\n\n", "\n", "é", "é", "0x1F", "1E5", "-0", "null", "true", ",", ":", "- ", "# ", "/",
             "./", "=", "&amp;", "<", ">", "%", "$", "`", "...", " ", "a", "0", "\\n", "\\u0041"]


def mutants(text: str, n: int, seed: int) -> list[str]:
    """Deterministic variants of `text`: line and char edits plus tricky snippets. Always includes truncations."""
    rng = random.Random(seed)
    out: list[str] = []
    for i in range(n):
        s = text
        for _ in range(rng.randint(1, 3)):
            lines = s.split("\n")
            op = rng.randrange(12)
            if op == 0 and len(lines) > 1:
                del lines[rng.randrange(len(lines))]
                s = "\n".join(lines)
            elif op == 1:
                j = rng.randrange(len(lines))
                lines.insert(j, lines[j])
                s = "\n".join(lines)
            elif op == 2 and len(lines) > 1:
                a, b = rng.randrange(len(lines)), rng.randrange(len(lines))
                lines[a], lines[b] = lines[b], lines[a]
                s = "\n".join(lines)
            elif op == 3 and s:
                p = rng.randrange(len(s) + 1)
                s = s[:p] + rng.choice(_SNIPPETS) + s[p:]
            elif op == 4 and s:
                s = s[: rng.randrange(1, len(s) + 1)]  # truncation, as when a tool is cut off
            elif op == 5 and s:
                a = rng.randrange(len(s))
                s = s[:a] + s[a + rng.randint(1, 12):]
            elif op == 6 and len(lines) > 1:
                j = rng.randrange(len(lines) - 1)
                lines[j : j + 2] = [lines[j] + lines[j + 1]]
                s = "\n".join(lines)
            elif op == 7:
                j = rng.randrange(len(lines))
                k = rng.randint(0, len(lines[j]))
                lines[j : j + 1] = [lines[j][:k], lines[j][k:]]
                s = "\n".join(lines)
            elif op == 8:
                j = rng.randrange(len(lines))
                lines[j] = rng.choice(_SNIPPETS) + lines[j] + rng.choice(_SNIPPETS)
                s = "\n".join(lines)
            elif op == 9:
                j = rng.randrange(len(lines))
                lines[j] = lines[j][::-1]
                s = "\n".join(lines)
            elif op == 10:
                s = s + rng.choice(_SNIPPETS)
            elif op == 11:
                s = rng.choice(_SNIPPETS) + s
        out.append(s)
    out += [text[: len(text) // 2], text[:1], text[:-1], "", "\n", text + "\n", "\n" + text]
    return out


# ---------------------------------------------------------------------------------------------------------------
# registry

@dataclass
class Fixture:
    name: str
    text: str
    shrinks: bool | None = True  # True: must shrink >= min_gain | False: must come back identical | None: adversarial, fidelity only


@dataclass
class Case:
    id: int
    claim: str
    loses: str = ""
    encode: Callable[[str], str] | None = None
    decode: Callable[[str], str] | None = None
    equivalent: Callable[[str, str], bool] | None = None
    fixtures: list[Fixture] | Callable[[], list[Fixture]] | None = None
    sim: Callable[[], dict | None] | None = None
    min_gain: float = 0.10
    idempotent: bool = True
    mutations: int = 40
    manual: str = ""
    title: str = ""

    @property
    def kind(self) -> str:
        if self.manual:
            return "manual"
        if self.sim is not None:
            return "sim"
        return "roundtrip" if self.decode is not None and not self.loses else "equivalent"


REGISTRY: dict[int, Case] = {}


def case(id: int, claim: str, **kw) -> Case:
    """Register the gate for idea #id. See the module docstring for the arguments."""
    if id in REGISTRY:
        raise ValueError(f"idea #{id} already has a case")
    c = Case(id=id, claim=claim, **kw)
    if c.manual:
        pass
    elif c.sim is not None:
        if c.encode or c.decode or c.equivalent or c.fixtures:
            raise ValueError(f"#{id}: a sim case takes no encode/decode/equivalent/fixtures")
    else:
        if c.encode is None or c.fixtures is None:
            raise ValueError(f"#{id}: transform cases need encode= and fixtures=")
        if c.decode is None and c.equivalent is None:
            raise ValueError(f"#{id}: need decode= (exact inverse) or equivalent= (independent oracle)")
        if c.decode is None and not c.loses:
            raise ValueError(f"#{id}: an equivalent-only case must say what it loses (loses=...)")
    REGISTRY[id] = c
    return c


@dataclass
class Result:
    id: int
    kind: str
    status: str  # pass | fail | manual
    fixtures: int = 0
    mutants: int = 0
    char_gain: float = 0.0  # mean over shrinking fixtures
    tok_gain: float = 0.0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    detail: list[str] = field(default_factory=list)


def _clip(s: str, n: int = 90) -> str:
    s = repr(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _check_one(c: Case, x: str, strict: bool) -> tuple[str | None, str | None]:
    """Apply the rules to one input (strict=False: totality and fidelity only). Returns (error or None, encoded)."""
    try:
        e = c.encode(x)
    except Exception as ex:  # noqa: BLE001
        return f"encode raised {type(ex).__name__}: {ex}; input={_clip(x)}", None
    if not isinstance(e, str):
        return f"encode returned {type(e).__name__}, not str; input={_clip(x)}", None
    try:
        if c.decode is not None:
            d = c.decode(e)
            if d != x:
                return f"decode(encode(x)) != x: x={_clip(x)} enc={_clip(e)} dec={_clip(d)}", e
        if c.equivalent is not None and not c.equivalent(x, e):
            return f"oracle says information changed: x={_clip(x)} enc={_clip(e)}", e
    except Exception as ex:  # noqa: BLE001
        return f"decode/oracle raised {type(ex).__name__}: {ex}; x={_clip(x)} enc={_clip(e)}", e
    if not strict:
        return None, e
    if len(e) > len(x):
        return f"grew {len(x)} -> {len(e)} chars; input={_clip(x)}", e
    if c.idempotent:
        try:
            e2 = c.encode(e)
        except Exception as ex:  # noqa: BLE001
            return f"encode(encode(x)) raised {type(ex).__name__}: {ex}; input={_clip(x)}", e
        if e2 != e:
            return f"not idempotent: {_clip(e)} -> {_clip(e2)}; input={_clip(x)}", e
    return None, e


def run_case(c: Case) -> Result:
    t0 = time.perf_counter()
    r = Result(c.id, c.kind, "pass")
    try:
        if c.manual:
            r.status = "manual"
            r.detail.append(c.manual)
        elif c.sim is not None:
            try:
                info = c.sim() or {}
                r.detail += [f"{k}={v}" for k, v in info.items()]
            except AssertionError as ex:
                r.errors.append(f"sim assertion failed: {ex}")
            except Exception as ex:  # noqa: BLE001
                r.errors.append(f"sim raised {type(ex).__name__}: {ex}")
        else:
            _run_transform(c, r)
    finally:
        r.seconds = time.perf_counter() - t0
    if r.errors:
        r.status = "fail"
    return r


def _run_transform(c: Case, r: Result) -> None:
    fx = c.fixtures() if callable(c.fixtures) else c.fixtures
    if not fx:
        r.errors.append("no fixtures")
        return
    if not any(f.shrinks for f in fx):
        r.errors.append("no fixture with shrinks=True: nothing shows the idea saves anything")
    if not any(f.shrinks is False for f in fx):
        r.errors.append("no negative fixture (shrinks=False): add input the transform must leave alone")
    cg, tg = [], []
    for n, f in enumerate(fx):
        r.fixtures += 1
        err, e = _check_one(c, f.text, f.shrinks is not None)
        if err:
            r.errors.append(f"[{f.name}] {err}")
        if e is None:
            continue
        gain = 1 - len(e) / len(f.text) if f.text else 0.0
        if f.shrinks is True:
            if gain < c.min_gain:
                r.errors.append(f"[{f.name}] gain {gain:.1%} < min_gain {c.min_gain:.0%}")
            cg.append(gain)
            tg.append(1 - tokens(e) / max(1, tokens(f.text)))
        elif f.shrinks is False and e != f.text:
            r.errors.append(f"[{f.name}] must be left alone but changed: {_clip(f.text)} -> {_clip(e)}")
        r.detail.append(f"{f.name}: {len(f.text)} -> {len(e)} chars ({-gain:+.0%})")
        for i, m in enumerate(mutants(f.text, c.mutations, seed=c.id * 1009 + n)):
            r.mutants += 1
            err, _ = _check_one(c, m, f.shrinks is not None)
            if err:
                r.errors.append(f"[{f.name} mutant {i}] {err}")
                break
    if cg:
        r.char_gain = sum(cg) / len(cg)
        r.tok_gain = sum(tg) / len(tg)


# ---------------------------------------------------------------------------------------------------------------
# discovery, coverage

HERE = os.path.dirname(os.path.abspath(__file__))
_LOADED: set[str] = set()


LOAD_ERRORS: dict[str, str] = {}


def discover(only: list[str] | None = None) -> dict[int, Case]:
    """Import cases_*.py next to this file (each registers its cases on import). `only` limits it to those module
    names (with or without .py). A module that fails to import is recorded in LOAD_ERRORS, not fatal to the rest."""
    import importlib.util
    import traceback

    want = {o[:-3] if o.endswith(".py") else o for o in only or []}
    for fn in sorted(os.listdir(HERE)):
        if not (fn.startswith("cases_") and fn.endswith(".py")) or fn in _LOADED:
            continue
        if want and fn[:-3] not in want:
            continue
        _LOADED.add(fn)
        before = set(REGISTRY)
        try:
            spec = importlib.util.spec_from_file_location(f"ideas_lossless_{fn[:-3]}", os.path.join(HERE, fn))
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
        except Exception:  # noqa: BLE001
            LOAD_ERRORS[fn] = traceback.format_exc(limit=3)
            for k in set(REGISTRY) - before:  # drop a half-registered module so coverage reports the truth
                del REGISTRY[k]
    return REGISTRY


def lossless_ideas() -> list[dict]:
    """Kept [L] records from ideas.jsonl (status != dropped)."""
    import json

    path = os.path.join(os.path.dirname(HERE), "ideas.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return [r for r in rows if r["tag"] == "L" and r["status"] != "dropped"]


def coverage() -> tuple[list[int], list[int]]:
    """(kept [L] ideas with no case, cases whose id is not a kept [L] idea)."""
    want = {r["id"] for r in lossless_ideas()}
    have = set(REGISTRY)
    return sorted(want - have), sorted(have - want)


# ---------------------------------------------------------------------------------------------------------------
# self-test: the gate must reject known-bad transforms, or a pass means nothing

def selftest() -> list[str]:
    """Return a list of problems; empty means the gate rejects every defect planted below and accepts a good one."""
    sample = [Fixture("pos", "alpha  \n\n\n\n\nbeta  \n" * 6, True), Fixture("neg", "plain text", False)]
    squeeze = lambda t: re.sub(r"\n{3,}", "\n\n", t)  # noqa: E731
    ok_oracle = lambda a, b: re.sub(r"\n{3,}", "\n\n", a) == b  # noqa: E731
    always = lambda a, b: True  # noqa: E731

    def run(**kw) -> Result:
        return run_case(Case(id=-1, claim="selftest", fixtures=sample, **kw))

    planted = {
        "lossy transform claiming a byte-exact inverse": dict(encode=lambda t: t.replace("beta", "b"), decode=lambda t: t.replace("b\n", "beta\n")),
        "an oracle that detects dropped content": dict(encode=lambda t: t.replace("beta", ""), equivalent=ok_oracle, loses="x"),
        "growing transform": dict(encode=lambda t: t + " ", equivalent=always, loses="x"),
        "transform that touches the negative": dict(encode=lambda t: t.replace("plain", "pln"), equivalent=always, loses="x"),
        "non-idempotent transform": dict(encode=lambda t: t[:-1] if len(t) > 9 else t, equivalent=always, loses="x", min_gain=0.0),
        "transform that raises on odd input": dict(encode=lambda t: 1 / 0 if "\x1b" in t else squeeze(t), equivalent=ok_oracle, loses="x"),
        "transform that never saves anything": dict(encode=lambda t: t, equivalent=always, loses="x"),
    }
    problems = [f"gate did not reject: {name}" for name, kw in planted.items() if run(**kw).status != "fail"]
    good = run(encode=squeeze, equivalent=ok_oracle, loses="blank-line count above two")
    if good.status != "pass":
        problems.append(f"gate rejected a good transform: {good.errors[:2]}")
    return problems
