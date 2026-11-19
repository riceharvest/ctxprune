"""Inference: score atoms with the trained token classifier and keep the best.

    from ctxprune import Compressor
    c = Compressor()                      # darioooooo0o/ctxprune-small, int8 ONNX on CPU
    c.compress(text, rate=0.5)["text"]

`rate` is the fraction of tokens to keep (like LLMLingua-2's `rate`).
Identifiers are never split or re-spaced, because selection happens on whole
atoms and text is rebuilt from the source's own whitespace.
"""

from __future__ import annotations

import base64
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .atoms import (
    Atom,
    atomize,
    detok,
    detok_prepared,
    est_atom_tokens,
    is_prose_artifact_id,
    is_protected,
    is_strict_id,
    is_technical_text,
    line_spans,
    prepare_detok,
)
from .tokenlabels import atom_spans, char_to_atom, special_wrap, token_atom_sets, token_atoms

STRUCTURE = set('{}[]",:')

DEFAULT_MODEL = "darioooooo0o/ctxprune-small"
INT8 = "onnx/model_quantized.onnx"
FP32 = "onnx/model.onnx"

_SOFT_PUNCT = frozenset(",;:(){}[]\"'`~|•·–—-")
_PAIR_OPEN = {"(": ")", "[": "]", "{": "}"}
_PAIR_CLOSE = {")": "(", "]": "[", "}": "{"}
_TWO_COMP = frozenset({"!=", "<=", ">=", "=="})

_NEG_WORDS = frozenset({
    "not", "no", "never", "none", "neither", "nor", "without", "except",
    "unless", "only", "cannot", "can't", "won't", "don't", "doesn't",
    "didn't", "isn't", "aren't", "wasn't", "weren't", "haven't", "hasn't",
    "hadn't", "wouldn't", "couldn't", "shouldn't", "mustn't", "zero", "lack",
})

_STOPWORDS = frozenset(
    "a an the and or but if then else when while of in on at to for with by from as is are was were "
    "be been being have has had do does did will would could should may might must can this that these "
    "those it its they them their he she his her we our you your which who whom what where why how all "
    "any some no not only also more most very much many such into over after before between through during "
    "about above according across actually almost already always among another answer anyone anything around "
    "asked based because become becomes before being below best better both brief called came come could "
    "describe detailed did different does doing done down during each either enough entire especially even "
    "every explain following found from further gave give given going good great happened having here "
    "herself himself however important include including instead into itself just keep kept kind know known "
    "large last later least less like likely little long made main mainly make makes making many maybe mean "
    "meeting might more most much must name named near need needed next often once other others otherwise "
    "overall part particular passage people perhaps please point points possible present provide provided "
    "question quite rather really reason recent regarding related report said same says second section seem "
    "seems seen several should show shown shows since small some someone something sometimes specifically "
    "state stated states still such summarize summary sure take taken taking tell than that their them "
    "themselves then there these they thing things think this those though three through thus time today "
    "together told took toward towards true two under until upon used using usually various very want "
    "wanted ways well went were what whatever when where whether which while whole whom whose why will "
    "with within without word words work working would write written year years your".split()
)

_HEADING_MD_RE = re.compile(r"^#{1,6}\s+")
_HEADING_NUM_RE = re.compile(
    r"^(?:Paragraph|Passage|Section|Chapter|Document|Article|Doc|Item|Part|Appendix)\s+\d+(?:\.\d+)*\b[:.\-]?",
    re.IGNORECASE,
)

# Web/Markdown pre-cleaning regexes (L1.4)
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\'](?:__NEXT_DATA__|__NUXT_DATA__)["\'][^>]*>.*?</script>',
    re.IGNORECASE | re.DOTALL,
)
_NUXT_INLINE_RE = re.compile(r"<script[^>]*>\s*window\.__(?:NUXT|NEXT_DATA|INITIAL_STATE)__=.*?</script>", re.I | re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_TRACKING_PARAM_RE = re.compile(
    r"(https?://[^\s)\"'>]+\?)"
    r"([^#\s)\"'>]+)",
    re.IGNORECASE,
)
_TRACKING_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "igshid",
})
_MD_SEP_CELL_RE = re.compile(r":?-{3,}:?")


def _logit(p: float) -> float:
    p = min(0.999, max(0.001, p))
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 30.0:
        return 1.0
    if z <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def clean_web_text(text: str) -> str:
    """Strip SSR hydration blobs, HTML comments, zero-width noise, tracking URL params & markdown table padding (L1.4)."""
    out = _NEXT_DATA_RE.sub("", text)
    out = _NUXT_INLINE_RE.sub("", out)
    out = _HTML_COMMENT_RE.sub("", out)
    out = _ZERO_WIDTH_RE.sub("", out)

    def _clean_url(m: re.Match) -> str:
        prefix, query = m.group(1), m.group(2)
        kept = [part for part in query.split("&") if part.split("=", 1)[0].lower() not in _TRACKING_KEYS]
        if not kept:
            return prefix[:-1]
        return prefix + "&".join(kept)

    out = _TRACKING_PARAM_RE.sub(_clean_url, out)
    if "|" in out:
        cleaned_lines = []
        for ln in out.splitlines(keepends=True):
            s = ln.strip()
            if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
                nl = "\n" if ln.endswith("\n") else ""
                cells = [c.strip() for c in s[1:-1].split("|")]
                if all(not c or _MD_SEP_CELL_RE.fullmatch(c) for c in cells):
                    cells = [((":" if c.startswith(":") else "") + "-" + (":" if c.endswith(":") else "")) if c else "-"
                             for c in cells]
                cleaned_lines.append("|" + "|".join(cells) + "|" + nl)
            else:
                cleaned_lines.append(ln)
        out = "".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", out)


def _hub_file(model: str, filename: str) -> str:
    """Local path of a model file: a directory checkout, the HF cache (no network), or a download."""
    if os.path.isdir(model):
        return os.path.join(model, filename)
    from huggingface_hub import hf_hub_download

    try:
        return hf_hub_download(model, filename, local_files_only=True)
    except Exception:
        return hf_hub_download(model, filename)


class _RawTokenizer:
    """The slice of the transformers tokenizer API that inference needs, on the `tokenizers`
    library alone. Loads in ~1 s instead of ~7 s and gives identical ids and offsets."""

    def __init__(self, path: str):
        from tokenizers import Tokenizer

        self.t = Tokenizer.from_file(path)
        self.t.no_padding()
        self.t.no_truncation()
        self.pad_token_id = self.t.token_to_id("<pad>")
        if self.pad_token_id is None:
            self.pad_token_id = self.t.token_to_id("[PAD]") or 0

    def __call__(self, text: str, add_special_tokens: bool = True, return_offsets_mapping: bool = False) -> dict:
        e = self.t.encode(text, add_special_tokens=add_special_tokens)
        out = {"input_ids": e.ids}
        if return_offsets_mapping:
            out["offset_mapping"] = e.offsets
        return out


def _pick_backend(model: str, onnx_file: str, device: str | None) -> str:
    if device is not None:
        return "torch"
    if os.path.isdir(model) and not os.path.exists(os.path.join(model, onnx_file)):
        return "torch"
    try:
        import onnxruntime  # noqa: F401
        return "onnx"
    except ImportError:
        return "torch"


def _default_threads(requested: int | None) -> int:
    if requested is not None and requested > 0:
        return int(requested)
    env = os.environ.get("CTXPRUNE_THREADS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    usable = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 4)
    return max(1, min(usable, 4))


@dataclass(frozen=True)
class ScoredText:
    """Compact pre-scored ingest representation (1 byte per atom in logit space, T1.7)."""

    text: str
    scores_u8: bytes
    z_min: float
    z_max: float
    model: str = DEFAULT_MODEL
    _cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def dequantize(self) -> list[float]:
        if not self.scores_u8:
            return []
        span = self.z_max - self.z_min
        if abs(span) < 1e-12:
            p = _sigmoid(self.z_min)
            return [p] * len(self.scores_u8)
        scale = span / 255.0
        return [_sigmoid(self.z_min + b * scale) for b in self.scores_u8]

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "scores_b64": base64.b64encode(self.scores_u8).decode("ascii"),
            "z_min": self.z_min,
            "z_max": self.z_max,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScoredText:
        return cls(
            text=data["text"],
            scores_u8=base64.b64decode(data["scores_b64"]),
            z_min=float(data["z_min"]),
            z_max=float(data["z_max"]),
            model=data.get("model", DEFAULT_MODEL),
        )


def _unit_spans(atoms: list[Atom], tech: bool) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return (unit_spans, para_spans). Units are lines for tech/newline-dense text, sentences otherwise."""
    n = len(atoms)
    para_spans: list[tuple[int, int]] = []
    p_start = 0
    for i, a in enumerate(atoms):
        if i > p_start and a.ws.count("\n") >= 2:
            para_spans.append((p_start, i))
            p_start = i
    if p_start < n:
        para_spans.append((p_start, n))

    lines = line_spans(atoms)
    if tech or (len(lines) >= 6 and n / max(1, len(lines)) <= 22):
        return lines, para_spans

    units: list[tuple[int, int]] = []
    u_start = 0
    for i, a in enumerate(atoms):
        if i > u_start and "\n" in a.ws:
            units.append((u_start, i))
            u_start = i
        if a.text in (".", "!", "?") and i > u_start:
            prev = atoms[i - 1].text
            if not (len(prev) <= 2 and prev.isalpha() and prev[0].isupper()):
                units.append((u_start, i + 1))
                u_start = i + 1
    if u_start < n:
        units.append((u_start, n))
    return units, para_spans


def _query_terms(query: str) -> tuple[list[str], set[str]]:
    raw_toks = re.findall(r"[A-Za-z0-9_./:-]{2,}", query)
    exact: set[str] = set()
    terms: list[str] = []
    for t in raw_toks:
        tl = t.lower()
        if tl in _STOPWORDS:
            continue
        terms.append(tl)
        if any(c.isdigit() for c in t) or any(c in t for c in "_./:-") or (len(t) >= 2 and any(c.isupper() for c in t[1:])):
            exact.add(tl)
    if not terms:
        terms = [t.lower() for t in raw_toks]
    return terms, exact


def _index_units(atoms: list[Atom], units: list[tuple[int, int]]) -> dict:
    """Pre-index unit word/stem counts once for O(|query|) BM25 lookups."""
    n_units = len(units)
    unit_tfs: list[Counter] = []
    unit_stems: list[Counter] = []
    unit_lens: list[int] = []
    df: Counter = Counter()
    stem_df: Counter = Counter()
    _SUB_PART_RE = re.compile(r"[a-z0-9]{2,}")

    for s, e in units:
        c: Counter = Counter()
        st: Counter = Counter()
        ln = 0
        for i in range(s, e):
            t = atoms[i].text
            if not t or not (t.isalnum() or any(ch.isalnum() for ch in t)):
                continue
            ln += 1
            tl = t.lower()
            c[tl] += 1
            if len(tl) >= 5 and tl.isalpha():
                st[tl[:5]] += 1
            if any(ch in tl for ch in "._/:-"):
                for part in _SUB_PART_RE.findall(tl):
                    c[part] += 0.8
        unit_tfs.append(c)
        unit_stems.append(st)
        unit_lens.append(max(1, ln))
        for term in c:
            df[term] += 1
        for pfx in st:
            stem_df[pfx] += 1

    avgdl = sum(unit_lens) / max(1, n_units)
    return {
        "n_units": n_units,
        "unit_tfs": unit_tfs,
        "unit_stems": unit_stems,
        "unit_lens": unit_lens,
        "df": df,
        "stem_df": stem_df,
        "avgdl": avgdl,
    }


def _bm25_from_index(idx: dict, query: str) -> tuple[list[float], float, set[str]]:
    q_terms, q_exact = _query_terms(query)
    n_units = idx["n_units"]
    if not q_terms or not n_units:
        return [0.0] * n_units, 0.0, set()

    q_set = set(q_terms)
    df = idx["df"]
    stem_df = idx["stem_df"]
    avgdl = idx["avgdl"]
    k1, b = 1.2, 0.75

    raw = []
    active_dfs: dict[str, int] = {}
    for qt in q_set:
        d = df.get(qt, 0)
        if d == 0 and len(qt) >= 5 and qt.isalpha():
            d = stem_df.get(qt[:5], 0)
        active_dfs[qt] = d

    for c, st, dl in zip(idx["unit_tfs"], idx["unit_stems"], idx["unit_lens"]):
        sc = 0.0
        norm_dl = k1 * (1.0 - b + b * dl / max(1.0, avgdl))
        for qt in q_set:
            tf = c.get(qt, 0.0)
            if tf == 0.0 and len(qt) >= 5 and qt.isalpha():
                tf = 0.6 * max(0.0, st.get(qt[:5], 0) - sum(c.get(k, 0.0) for k in q_set if k.startswith(qt[:5])))
            if tf > 0:
                d = active_dfs[qt]
                idf = math.log(1.0 + (n_units - d + 0.5) / (d + 0.5))
                w = 1.8 if qt in q_exact else 1.0
                sc += w * idf * (tf * (k1 + 1.0)) / (tf + norm_dl)
        raw.append(sc)

    max_sc = max(raw) if raw else 0.0
    if max_sc <= 0.0:
        return raw, 0.0, q_set

    nonzero = sum(1 for x in raw if x > 0)
    frac_hit = nonzero / max(1, n_units)
    rare_terms = sum(1 for t in q_set if 0 < active_dfs.get(t, 0) <= max(2, 0.25 * n_units))
    if rare_terms == 0 and frac_hit > 0.4:
        spec = 0.0
    else:
        mean_sc = sum(raw) / max(1, n_units)
        peak_ratio = max_sc / max(1e-6, mean_sc)
        spec = min(1.0, max(0.0, (peak_ratio - 1.5) / 4.0))
        if rare_terms > 0:
            spec = max(spec, min(1.0, 0.5 + 0.25 * rare_terms))
    return raw, spec, q_set


def _bm25_unit_scores(atoms: list[Atom], units: list[tuple[int, int]], query: str) -> tuple[list[float], float, set[str]]:
    """Per-unit BM25 relevance, query specificity scalar in [0, 1], and active query term set."""
    return _bm25_from_index(_index_units(atoms, units), query)


def _prefilter_cascade(text: str, keep_frac: float, query: str | None = None) -> str:
    """Two-stage lexical prefilter before ONNX/torch on long texts (>= 6k chars, L1.3)."""
    if keep_frac >= 0.92 or len(text) < 6000:
        return text
    lines = text.splitlines(keepends=True)
    if len(lines) >= 20 and sum(len(ln) for ln in lines) / len(lines) < 220:
        units = lines
    else:
        split = re.split(r"(?<=[.!?])\s+|\n\n+", text)
        units = [u + ("\n" if "\n" in u else " ") for u in split if u.strip()]
    nu = len(units)
    if nu <= 8:
        return text

    toks_per_u = [re.findall(r"[A-Za-z0-9_]{2,}", u.lower()) for u in units]
    df: Counter = Counter()
    for ts in toks_per_u:
        for t in set(ts):
            if t not in _STOPWORDS:
                df[t] += 1

    q_set = set(_query_terms(query)[0]) if query and query.strip() else set()
    scores = []
    for i, (u, ts) in enumerate(zip(units, toks_per_u)):
        if not ts:
            scores.append(0.0)
            continue
        pos_b = 0.25 if (i < 0.05 * nu or i > 0.95 * nu) else 0.0
        us = u.strip()
        if _HEADING_MD_RE.match(us) or _HEADING_NUM_RE.match(us):
            pos_b += 10.0
        uniq = {t for t in ts if t not in _STOPWORDS}
        tfidf = sum(math.log(1.0 + nu / (1.0 + df[t])) for t in uniq) / math.sqrt(max(4, len(ts)))
        q_sc = sum(math.log(1.0 + nu / (1.0 + df.get(t, 0))) for t in uniq & q_set) * 2.5 if q_set else 0.0
        scores.append(pos_b + tfidf + q_sc)

    target_chars = keep_frac * len(text)
    kept = [False] * nu
    used = 0
    for i in sorted(range(nu), key=lambda k: -scores[k]):
        if used >= target_chars:
            break
        kept[i] = True
        used += len(units[i])
    return "".join(u for u, k in zip(units, kept) if k)


def _strip_chunk_overlaps(docs: list[str], min_overlap: int = 60, max_check: int = 600) -> list[str]:
    """Deduplicate identical chunks and trim adjacent overlap prefixes across retrieved chunks (T1.6)."""
    seen_exact: set[str] = set()
    kept_raw: list[str] = []
    out: list[str] = []
    for d in docs:
        norm = " ".join(d.split())
        if not norm or norm in seen_exact:
            out.append("")
            continue
        seen_exact.add(norm)
        trimmed = d
        head = trimmed[:max_check]
        anchor = head[:40]
        best_cut = 0
        if len(anchor) >= 20:
            for prev in kept_raw:
                tail = prev[-max_check:]
                pos = tail.find(anchor)
                if pos >= 0:
                    cand_len = len(tail) - pos
                    if cand_len >= min_overlap and trimmed.startswith(tail[pos:]) and cand_len > best_cut:
                        best_cut = cand_len
        if best_cut > 0:
            trimmed = trimmed[best_cut:].lstrip()
        kept_raw.append(d)
        out.append(trimmed)
    return out


def _doc_query_scores(docs: list[str], query: str | None) -> list[float]:
    if not query or not query.strip():
        return [0.0] * len(docs)
    q_terms, _ = _query_terms(query)
    q_set = set(q_terms)
    if not q_set:
        return [0.0] * len(docs)
    n_docs = max(1, len(docs))
    dfs = {t: 0 for t in q_set}
    doc_toks = []
    for d in docs:
        ws = re.findall(r"[A-Za-z0-9_./:-]{2,}", d.lower())
        s = set(ws)
        doc_toks.append(ws)
        for t in q_set & s:
            dfs[t] += 1
    out = []
    for ws in doc_toks:
        if not ws:
            out.append(0.0)
            continue
        sc = 0.0
        for t in q_set:
            tf = ws.count(t)
            if tf:
                idf = math.log(1.0 + (n_docs - dfs[t] + 0.5) / (dfs[t] + 0.5))
                sc += idf * (tf * 2.2) / (tf + 1.2)
        out.append(sc)
    return out


def _allocate_doc_rates(
    cur_lens: list[int],
    total_budget: float,
    scores: list[float] | None,
    query_scores: list[float] | None,
) -> list[float]:
    """Water-filling per-document compression rates weighted by retrieval score and query relevance (T1.6)."""
    m = len(cur_lens)
    if m == 0:
        return []
    if scores is not None and len(scores) == m:
        s_min, s_max = min(scores), max(scores)
        span = max(1e-6, s_max - s_min)
        base_w = [0.35 + 1.15 * ((s - s_min) / span) for s in scores]
    else:
        base_w = [1.4 / (1.0 + 0.18 * r) for r in range(m)]
    if query_scores and max(query_scores) > 0:
        qm = max(query_scores)
        for i in range(m):
            base_w[i] *= 0.65 + 1.35 * (query_scores[i] / qm)

    alloc = [0.0] * m
    rem_budget = float(total_budget)
    active = [i for i in range(m) if cur_lens[i] > 0]
    for _ in range(4):
        if not active or rem_budget <= 0:
            break
        denom = sum(base_w[i] * cur_lens[i] for i in active)
        if denom <= 0:
            break
        next_active = []
        spent = 0.0
        for i in active:
            want = rem_budget * (base_w[i] * cur_lens[i]) / denom
            if alloc[i] + want >= cur_lens[i]:
                add = cur_lens[i] - alloc[i]
                alloc[i] = float(cur_lens[i])
                spent += add
            else:
                alloc[i] += want
                spent += want
                next_active.append(i)
        rem_budget -= spent
        active = next_active

    return [min(1.0, max(0.05, alloc[i] / cur_lens[i])) if cur_lens[i] > 0 else 0.0 for i in range(m)]


def _try_lossless_compress(text: str) -> str:
    """Run stdlib `lossless.compress(text)` when runtime scripts are present; else minify JSON or return input."""
    try:
        from . import glue

        glue.boot(own_process=False)
        import lossless

        out, _ = lossless.compress(text)
        return out
    except Exception:
        return text


def _fewshot_qa_mask(atoms: list[Atom], lines: list[tuple[int, int]]) -> list[bool] | None:
    """Detect repeated few-shot `Question:` / `Answer:` blocks (>= 3 pairs); return a mask of their format atoms.

    The mask covers the `Question:` and `Answer:` marker lines and the one short answer line after `Answer:`
    (the part of a few-shot example that teaches the output format), not the question or passage text.
    Returns None when the text does not contain such blocks. Structural only: no dataset names are used.
    """
    n = len(atoms)
    texts = []
    for ls, le in lines:
        texts.append("".join((atoms[j].ws if j > ls else "") + atoms[j].text for j in range(ls, le)).strip().lower())
    mask = [False] * n
    pairs = 0
    li = 0
    while li < len(lines):
        if texts[li] == "question:":
            for lj in range(li + 1, min(len(lines), li + 7)):
                if texts[lj] == "answer:":
                    pairs += 1
                    spans = [lines[li], lines[lj]]
                    if lj + 1 < len(lines) and 0 < lines[lj + 1][1] - lines[lj + 1][0] <= 12:
                        spans.append(lines[lj + 1])
                    for ls, le in spans:
                        for j in range(ls, le):
                            mask[j] = True
                    li = lj
                    break
        li += 1
    return mask if pairs >= 3 else None


def _apply_repetition_decay(
    atoms: list[Atom],
    scores: list[float],
    lines: list[tuple[int, int]],
    lam_ngram: float = 0.16,
    lam_prefix: float = 0.14,
    lam_atom: float = 0.04,
) -> None:
    """Decay keep-scores of boilerplate 2/3-grams, line prefixes and ultra-frequent words after first occurrence (L1.2)."""
    n = len(atoms)
    if n <= 20:
        return
    norms = [a.text.lower() for a in atoms]
    alnums = [bool(a.text) and (a.text.isalnum() or any(ch.isalnum() for ch in a.text)) for a in atoms]
    tri_counts: Counter = Counter()
    bi_counts: Counter = Counter()
    for i in range(n - 1):
        ni, ni1 = norms[i], norms[i + 1]
        if ni and ni1:
            bi_counts[(ni, ni1)] += 1
            if i + 2 < n and norms[i + 2]:
                tri_counts[(ni, ni1, norms[i + 2])] += 1

    tri_occ: Counter = Counter()
    bi_occ: Counter = Counter()
    rep_pen = [0.0] * n
    for i in range(n - 1):
        ni, ni1 = norms[i], norms[i + 1]
        if not ni or not ni1:
            continue
        if i + 2 < n and norms[i + 2] and (alnums[i] or alnums[i + 1] or alnums[i + 2]):
            g3 = (ni, ni1, norms[i + 2])
            if tri_counts[g3] >= 4:
                tri_occ[g3] += 1
                occ = tri_occ[g3]
                if occ >= 3:
                    pen = lam_ngram * math.log2(occ - 1)
                    rep_pen[i] = max(rep_pen[i], pen)
                    rep_pen[i + 1] = max(rep_pen[i + 1], pen)
                    rep_pen[i + 2] = max(rep_pen[i + 2], pen)
        if alnums[i] or alnums[i + 1]:
            g2 = (ni, ni1)
            if bi_counts[g2] >= 5 and (g2[0] not in _STOPWORDS or g2[1] not in _STOPWORDS):
                bi_occ[g2] += 1
                occ = bi_occ[g2]
                if occ >= 3:
                    pen = lam_ngram * 0.75 * math.log2(occ - 1)
                    rep_pen[i] = max(rep_pen[i], pen)
                    rep_pen[i + 1] = max(rep_pen[i + 1], pen)

    if lam_prefix > 0 and len(lines) >= 8:
        pfx_keys = []
        pfx_counts: Counter = Counter()
        for ls, le in lines:
            key_atoms = []
            for j in range(ls, min(le, ls + 5)):
                key_atoms.append(norms[j])
                if atoms[j].text == ":":
                    break
            if key_atoms and (key_atoms[-1] == ":" or (le - ls) <= 4):
                k = tuple(key_atoms)
                pfx_keys.append((ls, min(le, ls + len(key_atoms)), k))
                pfx_counts[k] += 1
            else:
                pfx_keys.append((ls, ls, ()))
        pfx_occ: Counter = Counter()
        for ls, pend, k in pfx_keys:
            if k and pfx_counts[k] >= 4:
                pfx_occ[k] += 1
                occ = pfx_occ[k]
                if occ >= 3:
                    pen = lam_prefix * math.log2(occ - 1)
                    for j in range(ls, pend):
                        rep_pen[j] = max(rep_pen[j], pen)

    if lam_atom > 0:
        atom_counts = Counter(norms[i] for i in range(n) if atoms[i].text)
        atom_occ: Counter = Counter()
        thresh = max(8, int(0.01 * n))
        for i in range(n):
            w = norms[i]
            if atoms[i].text and atom_counts[w] >= thresh and w not in _STOPWORDS:
                atom_occ[w] += 1
                occ = atom_occ[w]
                if occ >= 5:
                    rep_pen[i] = max(rep_pen[i], lam_atom * math.log2(occ - 3))

    for i in range(n):
        if rep_pen[i] > 0:
            scores[i] = max(0.005, scores[i] - rep_pen[i])


def _apply_query_boost(
    atoms: list[Atom],
    scores: list[float],
    units: list[tuple[int, int]],
    paras: list[tuple[int, int]],
    q_info: tuple[list[float], float, set[str]],
) -> None:
    """Combine BM25 unit relevance, proximity, paragraph context and 2nd-hop entity boost in logit space (T1.1)."""
    u_raw, spec, q_set = q_info
    nu = len(units)
    p1, p2 = 0.55, 0.25
    u_sm = [0.0] * nu
    for ui in range(nu):
        val = u_raw[ui]
        if ui > 0:
            val = max(val, p1 * u_raw[ui - 1])
        if ui + 1 < nu:
            val = max(val, p1 * u_raw[ui + 1])
        if ui > 1:
            val = max(val, p2 * u_raw[ui - 2])
        if ui + 2 < nu:
            val = max(val, p2 * u_raw[ui + 2])
        u_sm[ui] = val

    if len(paras) >= 2:
        p_scores = [0.0] * len(paras)
        u_to_p = [0] * nu
        pi = 0
        for ui, (us, ue) in enumerate(units):
            while pi + 1 < len(paras) and us >= paras[pi][1]:
                pi += 1
            u_to_p[ui] = pi
            p_scores[pi] += u_raw[ui]
        p_max = max(p_scores) if p_scores else 1.0
        if p_max > 0:
            top_raw = max(u_raw)
            for ui in range(nu):
                u_sm[ui] += 0.30 * (p_scores[u_to_p[ui]] / p_max) * top_raw

    if nu >= 6:
        top_units = sorted(range(nu), key=lambda ui: -u_raw[ui])[:max(2, nu // 10)]
        top_entities: Counter = Counter()
        for ui in top_units:
            if u_raw[ui] <= 0:
                continue
            us, ue = units[ui]
            for j in range(us, ue):
                t = atoms[j].text
                tl = t.lower()
                if len(t) >= 3 and t[0].isupper() and tl not in _STOPWORDS and tl not in q_set:
                    top_entities[tl] += 1
        bridge_ents = {e for e, c in top_entities.most_common(8) if c >= 1}
        if bridge_ents:
            max_u = max(u_sm) or 1.0
            for ui, (us, ue) in enumerate(units):
                if u_raw[ui] > 0:
                    continue
                hits = sum(1 for j in range(us, ue) if atoms[j].text.lower() in bridge_ents)
                if hits >= 2:
                    u_sm[ui] += 0.30 * max_u * min(1.0, hits / 3.0)

    max_sm = max(u_sm) or 1.0
    gamma = 5.0 * spec
    delta = 1.5 * spec
    for ui, (us, ue) in enumerate(units):
        rel = u_sm[ui] / max_sm
        for j in range(us, ue):
            if not atoms[j].text:
                continue
            hit = delta if atoms[j].text.lower() in q_set else 0.0
            scores[j] = _sigmoid(_logit(min(0.99, max(0.01, scores[j]))) + gamma * rel + hit)


def _extract_headers(
    atoms: list[Atom],
    lines: list[tuple[int, int]],
) -> tuple[list[tuple[int, int, int]], dict[int, list[tuple[int, int]]]]:
    n = len(atoms)
    para_headers: list[tuple[int, int, int]] = []
    line_to_md_ancestors: dict[int, list[tuple[int, int]]] = {}
    md_stack: list[tuple[int, int, int]] = []
    for li, (ls, le) in enumerate(lines):
        line_str = "".join((atoms[j].ws if j > ls else "") + atoms[j].text for j in range(ls, le)).strip()
        if _HEADING_MD_RE.match(line_str):
            lvl = len(line_str) - len(line_str.lstrip("#"))
            while md_stack and md_stack[-1][0] >= lvl:
                md_stack.pop()
            md_stack.append((lvl, ls, min(le, ls + 12)))
        else:
            m_num = _HEADING_NUM_RE.match(line_str)
            if m_num:
                chars = 0
                hend = ls
                tgt_chars = len(m_num.group(0))
                while hend < le and chars < tgt_chars:
                    chars += len(atoms[hend].text) + (len(atoms[hend].ws) if hend > ls else 0)
                    hend += 1
                end_blk = hend
                while end_blk < n and atoms[end_blk].ws.count("\n") < 2:
                    end_blk += 1
                para_headers.append((ls, hend, end_blk))
            if md_stack:
                line_to_md_ancestors[li] = [(s, e) for _, s, e in md_stack]
    return para_headers, line_to_md_ancestors


def _pin_headers(
    atoms: list[Atom],
    costs: list[float],
    lines: list[tuple[int, int]],
    para_headers: list[tuple[int, int, int]],
    line_to_md_ancestors: dict[int, list[tuple[int, int]]],
    keep: list[bool],
    pinned: list[bool],
    asc_order: list[int],
    budget: float,
) -> None:
    to_force: set[int] = set()
    for hs, he, end_blk in para_headers:
        if sum(1 for j in range(he, end_blk) if keep[j]) >= 2:
            for j in range(hs, he):
                if atoms[j].text:
                    pinned[j] = True
                    if not keep[j]:
                        to_force.add(j)
    for li, ancs in line_to_md_ancestors.items():
        ls, le = lines[li]
        if any(keep[j] for j in range(ls, le)):
            for hs, he in ancs:
                for j in range(hs, he):
                    if atoms[j].text:
                        pinned[j] = True
                        if not keep[j]:
                            to_force.add(j)
    max_add = 0.08 * max(1.0, budget)
    added = 0.0
    add_list = []
    for j in sorted(to_force):
        if added + costs[j] > max_add:
            break
        add_list.append(j)
        added += costs[j]
    if add_list:
        for j in add_list:
            keep[j] = True
            pinned[j] = True
        freed = 0.0
        for idx in asc_order:
            if freed >= added:
                break
            if keep[idx] and not pinned[idx]:
                keep[idx] = False
                freed += costs[idx]


def _neg_anchor_plan(atoms: list[Atom]) -> list[tuple]:
    """Static part of `_pin_negation_anchors`: only atoms that can ever pin, with their kind and fixed target.

    Entries are ``(i, kind, target)``: kind 0 two-char comparator, 1 ``<``/``>``, 2 negation word (target = the
    first non-stopword content atom within 3 atoms on the same line, or -1), 3 ``key:value`` colon, 4 ``=``.
    """
    n = len(atoms)
    plan: list[tuple] = []
    for i in range(n):
        text = atoms[i].text
        if not text:
            continue
        if i + 1 < n and atoms[i + 1].ws == "" and (text + atoms[i + 1].text) in _TWO_COMP:
            plan.append((i, 0, -1))
            continue
        if text == "<" or text == ">":
            plan.append((i, 1, -1))
            continue
        tl = text.lower()
        if tl in _NEG_WORDS or tl.endswith("n't"):
            target = -1
            for k in range(i + 1, min(n, i + 4)):
                if "\n" in atoms[k].ws:
                    break
                kt = atoms[k].text
                if kt and any(ch.isalnum() for ch in kt) and kt.lower() not in _STOPWORDS:
                    target = k
                    break
            plan.append((i, 2, target))
        elif (
            text == ":"
            and 0 < i < n - 1
            and "\n" not in atoms[i + 1].ws
            and "\n" not in atoms[i].ws
            and atoms[i - 1].text.replace("_", "").replace("-", "").isalnum()
            and len(atoms[i - 1].text) <= 28
        ):
            plan.append((i, 3, -1))
        elif text == "=" and 0 < i < n - 1 and "\n" not in atoms[i].ws and "\n" not in atoms[i + 1].ws:
            plan.append((i, 4, -1))
    return plan


def _pin_negation_anchors(
    atoms: list[Atom],
    costs: list[float],
    keep: list[bool],
    pinned: list[bool],
    asc_order: list[int],
    budget: float,
    plan: list[tuple] | None = None,
    nl: list[bool] | None = None,
) -> None:
    """Promote negation words, comparators (!=, <=, >=, ==, <, >) and key:value anchors near kept tokens (L1.1).

    `plan` (from `_neg_anchor_plan`) and `nl` (``"\\n" in atom.ws`` per atom) are static per chunk; passing them
    skips the scan over atoms that can never pin. The result is identical with or without them.
    """
    n = len(atoms)
    if plan is None:
        plan = _neg_anchor_plan(atoms)
    if nl is None:
        nl = ["\n" in a.ws for a in atoms]
    to_pin: list[int] = []
    max_pin_cost = max(4.0, 0.03 * max(1.0, budget))
    for i, kind, target in plan:
        if kind == 2:
            after = False
            for j in range(i + 1, min(n, i + 5)):
                if keep[j] and not nl[j]:
                    after = True
                    break
            before = i > 0 and keep[i - 1] and not nl[i]
            ki = keep[i]
            if not ki and (after or before):
                to_pin.append(i)
            if (ki or after or before) and target >= 0 and not keep[target]:
                to_pin.append(target)
        elif kind == 0:
            near_kept = (i > 0 and keep[i - 1] and not nl[i]) or any(
                keep[j] and not nl[j] for j in range(i + 2, min(n, i + 4))
            )
            if near_kept:
                for k in (i, i + 1, i + 2):
                    if k < n and not keep[k] and (k < i + 2 or not nl[k]):
                        to_pin.append(k)
        elif kind == 1:
            near_kept = (i > 0 and keep[i - 1] and not nl[i]) or any(
                keep[j] and not nl[j] for j in range(i + 1, min(n, i + 3))
            )
            if near_kept:
                if not keep[i]:
                    to_pin.append(i)
                if i + 1 < n and not keep[i + 1] and not nl[i + 1]:
                    to_pin.append(i + 1)
        elif keep[i]:
            continue
        elif kind == 3:
            if keep[i + 1]:
                if not keep[i - 1]:
                    to_pin.append(i - 1)
                to_pin.append(i)
        elif keep[i + 1] or keep[i - 1]:
            if not keep[i - 1]:
                to_pin.append(i - 1)
            to_pin.append(i)
            if not keep[i + 1]:
                to_pin.append(i + 1)

    pin_cost = 0.0
    seen_p: set[int] = set()
    for idx in to_pin:
        if idx not in seen_p and not keep[idx]:
            if pin_cost + costs[idx] > max_pin_cost:
                continue
            seen_p.add(idx)
            pin_cost += costs[idx]
    if seen_p:
        for idx in seen_p:
            keep[idx] = True
            pinned[idx] = True
        freed = 0.0
        for idx in asc_order:
            if freed >= pin_cost:
                break
            if keep[idx] and not pinned[idx]:
                keep[idx] = False
                freed += costs[idx]


def _cleanup_prose_orphans(
    atoms: list[Atom],
    costs: list[float],
    lines: list[tuple[int, int]],
    keep: list[bool],
    pinned: list[bool],
    desc_order: list[int],
    budget: float,
) -> None:
    """Drop orphan brackets, empty punctuation lines, duplicate prose punct and empty `Label:` stubs, then refill."""
    dropped: set[int] = set()
    for li, (ls, le) in enumerate(lines):
        kept_idx = [j for j in range(ls, le) if keep[j] and atoms[j].text]
        if not kept_idx:
            continue
        if not any(any(ch.isalnum() for ch in atoms[j].text) for j in kept_idx):
            for j in kept_idx:
                if not pinned[j]:
                    keep[j] = False
                    dropped.add(j)
            continue
        stack: list[tuple[str, int]] = []
        for j in kept_idx:
            t = atoms[j].text
            if t in _PAIR_OPEN:
                stack.append((t, j))
            elif t in _PAIR_CLOSE:
                want = _PAIR_CLOSE[t]
                if stack and stack[-1][0] == want:
                    _, open_j = stack.pop()
                    if not any(keep[k] and any(ch.isalnum() for ch in atoms[k].text) for k in range(open_j + 1, j)):
                        if not pinned[open_j]:
                            keep[open_j] = False
                            dropped.add(open_j)
                        if not pinned[j]:
                            keep[j] = False
                            dropped.add(j)
                elif not pinned[j]:
                    keep[j] = False
                    dropped.add(j)
        for _, open_j in stack:
            if not pinned[open_j]:
                keep[open_j] = False
                dropped.add(open_j)

        prev_p = None
        for j in range(ls, le):
            if not keep[j] or not atoms[j].text:
                continue
            t = atoms[j].text
            if len(t) == 1 and t in (",", ";", ":", "."):
                if prev_p == t or (prev_p in (",", ";", ":") and t in (",", ";", ":")):
                    if not pinned[j]:
                        keep[j] = False
                        dropped.add(j)
                        continue
                prev_p = t
            else:
                prev_p = None

        kept_now = [j for j in range(ls, le) if keep[j] and atoms[j].text]
        if kept_now and atoms[kept_now[-1]].text == ":" and len(kept_now) <= 4:
            colon_j = kept_now[-1]
            if any(any(ch.isalnum() for ch in atoms[k].text) for k in range(colon_j + 1, le)):
                line_txt = "".join((atoms[j].ws if j > ls else "") + atoms[j].text for j in kept_now).strip()
                if not _HEADING_NUM_RE.match(line_txt):
                    cur_ind = len(atoms[ls].ws.rsplit("\n", 1)[-1]) if atoms[ls].ws else 0
                    next_deeper = False
                    if li + 1 < len(lines):
                        nls, nle = lines[li + 1]
                        if any(keep[k] for k in range(nls, nle)):
                            n_ind = len(atoms[nls].ws.rsplit("\n", 1)[-1]) if atoms[nls].ws else 0
                            next_deeper = n_ind > cur_ind
                    if not next_deeper:
                        for j in kept_now:
                            if not pinned[j]:
                                keep[j] = False
                                dropped.add(j)

    cur_used = sum(costs[j] for j in range(len(atoms)) if keep[j])
    if cur_used < budget:
        for idx in desc_order:
            if cur_used >= budget:
                break
            if not keep[idx] and idx not in dropped and any(ch.isalnum() for ch in atoms[idx].text):
                keep[idx] = True
                cur_used += costs[idx]


def _resolve_unit_costs(
    atoms: list[Atom],
    tok_counts: list[int] | None,
    unit: str | Callable,
) -> list[float]:
    if callable(unit):
        return [float(c) for c in unit([(" " if a.ws else "") + a.text for a in atoms])]
    if unit == "tokens_est":
        return [est_atom_tokens(a.text, a.ws) for a in atoms]
    if unit == "chars":
        return [float(len(a.text) + (1 if a.ws else 0)) for a in atoms]
    if unit == "tokens":
        if tok_counts is not None:
            return [float(c) for c in tok_counts]
        return [est_atom_tokens(a.text, a.ws) for a in atoms]
    if unit in ("o200k", "cl100k"):
        try:
            import tiktoken

            enc = tiktoken.get_encoding(f"{unit}_base")
            return [
                float(max(1, len(enc.encode((" " if a.ws else "") + a.text, disallowed_special=())))) if a.text else 0.0
                for a in atoms
            ]
        except Exception:
            return [est_atom_tokens(a.text, a.ws) for a in atoms]
    raise ValueError(f"unit must be 'tokens_est', 'o200k', 'cl100k', 'chars', 'tokens' or callable, not {unit!r}")


class Compressor:
    rep_decay = "all"
    fewshot = "off"
    engine = "auto"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str | None = None,
        max_len: int = 512,
        batch_size: int = 16,
        backend: str = "auto",
        onnx_file: str = INT8,
        threads: int | None = None,
        overlap: int = 0,
        rep_decay: str = "all",
        fewshot: str = "off",
        engine: str = "auto",
    ):
        """backend="onnx" (CPU, needs only onnxruntime + tokenizers), "torch" (any device, needs
        torch + transformers) or "auto" (onnx when available, torch when a device is given).
        onnx_file defaults to the int8 build (143 MB); FP32 ("onnx/model.onnx") is exact.
        threads defaults to min(usable_cpus, 4) (or CTXPRUNE_THREADS env) to avoid cgroup contention.
        rep_decay: "all" (default) applies the intra-document repetition decay everywhere; "prose" skips it on
        technical/code text (repeated identifiers are legitimate there); "off" disables it; "code" skips it on prose.
        fewshot: "off" (default); "pin" keeps the Question:/Answer: format atoms of repeated few-shot blocks;
        "noquery" ignores `query=` on such texts; "both" does both. All opt-in (see CHANGELOG).
        engine: "auto" (default) routes `compress()` by call shape: 0.2/0.3-style calls run the frozen 0.3.2 path
        (same bytes as 0.3.2), calls using anything new run the v0.4 engine. "v04" / "v032" pin one engine."""
        if engine not in ("auto", "v04", "v032"):
            raise ValueError(f"engine must be 'auto', 'v04' or 'v032', not {engine!r}")
        self.engine = engine
        if backend == "auto":
            backend = _pick_backend(model, onnx_file, device)
        self.backend = backend
        self.model_name = model
        self.threads = _default_threads(threads)
        self.overlap = max(0, int(overlap))
        if rep_decay not in ("all", "prose", "code", "off"):
            raise ValueError(f"rep_decay must be 'all', 'prose', 'code' or 'off', not {rep_decay!r}")
        if fewshot not in ("off", "pin", "noquery", "both"):
            raise ValueError(f"fewshot must be 'off', 'pin', 'noquery' or 'both', not {fewshot!r}")
        self.rep_decay = rep_decay
        self.fewshot = fewshot
        if backend == "onnx":
            import onnxruntime as ort

            self.tok = _RawTokenizer(_hub_file(model, "tokenizer.json"))
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            opts.intra_op_num_threads = self.threads
            opts.inter_op_num_threads = 1
            self.session = ort.InferenceSession(_hub_file(model, onnx_file), opts,
                                                providers=["CPUExecutionProvider"])
            self.device = "cpu"
        elif backend == "torch":
            import torch
            from transformers import AutoModelForTokenClassification, AutoTokenizer

            self.tok = AutoTokenizer.from_pretrained(model)
            self.torch = torch
            if device is None:
                device = "xpu" if hasattr(torch, "xpu") and torch.xpu.is_available() else (
                    "cuda" if torch.cuda.is_available() else "cpu")
            self.device = device
            self.model = AutoModelForTokenClassification.from_pretrained(model).to(device).eval()
        else:
            raise ValueError(f"backend must be 'auto', 'onnx' or 'torch', not {backend!r}")
        self.max_len = max_len
        self.batch_size = batch_size
        self.pre, self.post = special_wrap(self.tok)

    def atom_scores(self, text: str, overlap: int | None = None) -> tuple[list[Atom], list[float], list[int]]:
        """Keep-probability and token count for every atom of `text`."""
        atoms = atomize(text)
        full, spans = atom_spans(atoms)
        owner = char_to_atom(spans, len(full))
        enc = self.tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        tok_atom = token_atoms(offs, owner)

        win = self.max_len - len(self.pre) - len(self.post)
        ov = self.overlap if overlap is None else max(0, int(overlap))
        probs = [0.0] * len(ids)

        if ov > 0 and len(ids) > win:
            stride = max(64, win - 2 * ov)
            windows = []
            pos = 0
            while pos < len(ids):
                core_end = min(pos + stride, len(ids))
                if core_end < len(ids):
                    cut = core_end
                    while cut > pos + 1 and tok_atom[cut] == tok_atom[cut - 1]:
                        cut -= 1
                    if cut > pos + 1:
                        core_end = cut
                w_start = max(0, pos - ov)
                w_end = min(len(ids), core_end + ov)
                if w_end - w_start > win:
                    excess = (w_end - w_start) - win
                    trim_r = min(excess, max(0, w_end - core_end))
                    w_end -= trim_r
                    excess -= trim_r
                    if excess > 0:
                        w_start = min(pos, w_start + excess)
                windows.append((w_start, w_end, pos, core_end))
                if core_end >= len(ids):
                    break
                pos = core_end

            for b in range(0, len(windows), self.batch_size):
                batch = windows[b:b + self.batch_size]
                seqs = [self.pre + ids[ws:we] + self.post for ws, we, _, _ in batch]
                width = max(map(len, seqs))
                pad = self.tok.pad_token_id
                ids_rows = [q + [pad] * (width - len(q)) for q in seqs]
                mask_rows = [[1] * len(q) + [0] * (width - len(q)) for q in seqs]
                p = self._keep_probs(ids_rows, mask_rows)
                for (ws, _we, cs, ce), row in zip(batch, p):
                    k = len(self.pre)
                    probs[cs:ce] = row[k + (cs - ws):k + (ce - ws)]
        else:
            windows, start = [], 0
            while start < len(ids):
                end = min(start + win, len(ids))
                if end < len(ids):
                    cut = end
                    while cut > start + 1 and tok_atom[cut] == tok_atom[cut - 1]:
                        cut -= 1
                    end = cut if cut > start + 1 else end
                windows.append((start, end))
                start = end

            for b in range(0, len(windows), self.batch_size):
                batch = windows[b:b + self.batch_size]
                seqs = [self.pre + ids[s:e] + self.post for s, e in batch]
                width = max(map(len, seqs))
                pad = self.tok.pad_token_id
                ids_rows = [q + [pad] * (width - len(q)) for q in seqs]
                mask_rows = [[1] * len(q) + [0] * (width - len(q)) for q in seqs]
                p = self._keep_probs(ids_rows, mask_rows)
                for (s, e), row in zip(batch, p):
                    k = len(self.pre)
                    probs[s:e] = row[k:k + (e - s)]

        sums = [0.0] * len(atoms)
        hits = [0] * len(atoms)
        counts = [0] * len(atoms)
        for a, sets, p in zip(tok_atom, token_atom_sets(offs, owner), probs):
            if a >= 0:
                counts[a] += 1
            for b in sets:
                sums[b] += p
                hits[b] += 1
        scores = [sums[i] / hits[i] if hits[i] else 0.0 for i in range(len(atoms))]
        return atoms, scores, counts

    def _keep_probs(self, ids_rows: list[list[int]], mask_rows: list[list[int]]) -> list[list[float]]:
        if self.backend == "onnx":
            import numpy as np

            logits = self.session.run(["logits"], {"input_ids": np.array(ids_rows, dtype=np.int64),
                                                   "attention_mask": np.array(mask_rows, dtype=np.int64)})[0]
            z = logits - logits.max(-1, keepdims=True)
            e = np.exp(z)
            return (e[..., 1] / e.sum(-1)).tolist()
        t = self.torch
        input_ids = t.tensor(ids_rows, device=self.device)
        mask = t.tensor(mask_rows, device=self.device)
        with t.no_grad():
            logits = self.model(input_ids=input_ids, attention_mask=mask).logits.float()
        return logits.softmax(-1)[..., 1].cpu().tolist()

    def score(self, text: str) -> ScoredText:
        """Run the encoder once and return a compact 1-byte-per-atom ScoredText for sub-ms slicing (T1.7)."""
        atoms, raw_scores, _ = self.atom_scores(text)
        if not raw_scores:
            return ScoredText(text=text, scores_u8=b"", z_min=0.0, z_max=0.0, model=self.model_name)
        zs = [_logit(p) for p in raw_scores]
        z_min, z_max = min(zs), max(zs)
        span = z_max - z_min
        if span < 1e-12:
            q = bytes([128] * len(zs))
        else:
            inv = 255.0 / span
            q = bytes(min(255, max(0, round((z - z_min) * inv))) for z in zs)
        st = ScoredText(text=text, scores_u8=q, z_min=z_min, z_max=z_max, model=self.model_name)
        base = self._prepare_base(atoms, st.dequantize(), text, "tokens_est")
        if not base.get("empty"):
            base["_unit_idx"] = _index_units(atoms, base["units"])
        st._cache["base"] = base
        self._finish_prep(base, query=None)
        return st

    def _prepare_base(
        self,
        atoms: list[Atom],
        raw_scores: list[float],
        orig_text: str,
        unit: str | Callable = "tokens_est",
        tok_counts: list[int] | None = None,
    ) -> dict:
        n = len(atoms)
        if n == 0:
            return {"empty": True}
        tech = is_technical_text(atoms, orig_text)
        costs = _resolve_unit_costs(atoms, tok_counts, unit)
        units, paras = _unit_spans(atoms, tech)
        lines = line_spans(atoms)
        strict_fn = is_strict_id if tech else is_prose_artifact_id
        strict_mask = [bool(a.text) and strict_fn(a.text) for a in atoms]
        prot_mask = [bool(a.text) and is_protected(a.text) for a in atoms]

        cal_scores = list(raw_scores)
        if not tech:
            for i, a in enumerate(atoms):
                t = a.text
                if not t:
                    continue
                if len(t) == 1 and t in _SOFT_PUNCT:
                    cal_scores[i] = max(0.01, cal_scores[i] - 0.18)
                elif len(t) == 1 and t in ".!?" and cal_scores[i] > 0.7:
                    cal_scores[i] = max(0.01, cal_scores[i] - 0.072)
        rd = getattr(self, "rep_decay", "all")
        if rd == "all" or (rd == "prose" and not tech) or (rd == "code" and tech):
            _apply_repetition_decay(atoms, cal_scores, lines)

        fs_mode = getattr(self, "fewshot", "off")
        fewshot_mask = _fewshot_qa_mask(atoms, lines) if fs_mode != "off" else None

        l_rank = None
        if n > 200:
            blocks = paras if len(paras) >= 4 else [(b, min(n, b + 160)) for b in range(0, n, 160)]
            order_g = sorted(range(n), key=lambda i: cal_scores[i])
            l_rank = [0.0] * n
            for r, idx in enumerate(order_g):
                l_rank[idx] = r / max(1, n - 1)
            for bs, be in blocks:
                if be - bs < 4:
                    continue
                sub = sorted(range(bs, be), key=lambda i: cal_scores[i])
                m = len(sub)
                for r, idx in enumerate(sub):
                    l_rank[idx] = r / max(1, m - 1)

        para_headers, line_to_md_ancestors = _extract_headers(atoms, lines)
        para_costs = [sum(costs[j] for j in range(ps, pe)) for ps, pe in paras]
        return {
            "empty": False,
            "atoms": atoms,
            "neg_plan": _neg_anchor_plan(atoms),
            "nl": ["\n" in a.ws for a in atoms],
            "detok_pre": prepare_detok(atoms),
            "n": n,
            "unit_mode": unit if isinstance(unit, str) else None,
            "tech": tech,
            "costs": costs,
            "total_cost": sum(costs),
            "units": units,
            "paras": paras,
            "lines": lines,
            "strict_mask": strict_mask,
            "prot_mask": prot_mask,
            "strict_cost": sum(c for c, f in zip(costs, strict_mask) if f),
            "prot_cost": sum(c for c, f in zip(costs, prot_mask) if f),
            "cal_scores": cal_scores,
            "l_rank": l_rank,
            "para_headers": para_headers,
            "line_to_md_ancestors": line_to_md_ancestors,
            "para_costs": para_costs,
            "fewshot_pin": fewshot_mask if fs_mode in ("pin", "both") else None,
            "fewshot_noquery": fewshot_mask is not None and fs_mode in ("noquery", "both"),
        }

    def _finish_prep(self, base: dict, query: str | None = None) -> dict:
        if base.get("empty"):
            return base
        if base.get("fewshot_noquery"):
            query = None
        cache_noq = base.get("_no_query_prep")
        if (not query or not query.strip()) and cache_noq is not None:
            return cache_noq
        atoms = base["atoms"]
        n = base["n"]
        tech = base["tech"]
        units = base["units"]
        paras = base["paras"]
        scores = list(base["cal_scores"])

        q_info = None
        eff_local_beta = 0.35
        eff_min_block = 0.15
        if query and query.strip():
            uidx = base.get("_unit_idx")
            if uidx is None:
                uidx = _index_units(atoms, units)
                base["_unit_idx"] = uidx
            u_raw, spec, q_set = _bm25_from_index(uidx, query)
            if spec > 0.05 and max(u_raw, default=0.0) > 0:
                q_info = (u_raw, spec, q_set)
                eff_local_beta = 0.35 * (1.0 - 0.85 * spec)
                eff_min_block = 0.15 * (1.0 - spec)

        if eff_local_beta > 0 and base["l_rank"] is not None:
            l_rank = base["l_rank"]
            for i in range(n):
                scores[i] = (1.0 - eff_local_beta) * scores[i] + eff_local_beta * l_rank[i]

        has_active_query = q_info is not None
        if tech and has_active_query:
            sm = base["strict_mask"]
            for i in range(n):
                if sm[i]:
                    scores[i] += 0.08

        if q_info is not None:
            _apply_query_boost(atoms, scores, units, paras, q_info)

        desc_order = [i for i in sorted(range(n), key=lambda k: -scores[k]) if atoms[i].text]
        asc_order = list(reversed(desc_order))
        para_orders = [[j for j in sorted(range(ps, pe), key=lambda k: -scores[k]) if atoms[j].text] for ps, pe in paras]
        out = {
            **base,
            "scores": scores,
            "has_active_query": has_active_query,
            "desc_order": desc_order,
            "asc_order": asc_order,
            "para_orders": para_orders,
            "eff_min_block": eff_min_block,
        }
        if not query or not query.strip():
            base["_no_query_prep"] = out
        return out

    def _prepare_selection(
        self,
        atoms: list[Atom],
        raw_scores: list[float],
        tok_counts: list[int] | None,
        orig_text: str,
        unit: str | Callable = "tokens_est",
        query: str | None = None,
    ) -> dict:
        base = self._prepare_base(atoms, raw_scores, orig_text, unit=unit, tok_counts=tok_counts)
        return self._finish_prep(base, query=query)

    def _slice_prepared(
        self,
        prep: dict,
        keep_rate: float,
        threshold: float | None = None,
        force_protected: bool | str = "auto",
        keep_structure: bool = False,
        force_tokens: list[str] | None = None,
    ) -> dict:
        if prep.get("empty"):
            return {"text": "", "kept": 0, "total": 0}

        atoms = prep["atoms"]
        n = prep["n"]
        scores = prep["scores"]
        costs = prep["costs"]
        total = sum(costs)
        tech = prep["tech"]
        strict_mask = prep["strict_mask"]
        prot_mask = prep["prot_mask"]
        budget = keep_rate * total

        forced = [False] * n
        eff_scores = scores
        desc_order = prep["desc_order"]
        asc_order = prep["asc_order"]

        if force_protected is True:
            forced = list(prot_mask)
        elif force_protected == "ids":
            if prep["strict_cost"] <= 1.05 * budget:
                forced = list(strict_mask)
            else:
                eff_scores = [s + (0.15 if strict_mask[i] else 0.0) for i, s in enumerate(scores)]
                desc_order = [i for i in sorted(range(n), key=lambda k: -eff_scores[k]) if atoms[i].text]
                asc_order = list(reversed(desc_order))
        elif force_protected == "auto":
            if prep["has_active_query"] and tech:
                pass  # soft +0.08 already applied in _prepare_selection
            elif tech:
                if prep["prot_cost"] <= 1.35 * budget:
                    forced = list(prot_mask)
                elif prep["strict_cost"] <= 1.15 * budget:
                    forced = list(strict_mask)
                    eff_scores = [s + (0.10 if (prot_mask[i] and not strict_mask[i]) else 0.0) for i, s in enumerate(scores)]
                    desc_order = [i for i in sorted(range(n), key=lambda k: -eff_scores[k]) if atoms[i].text]
                    asc_order = list(reversed(desc_order))
                else:
                    eff_scores = [
                        s + (0.15 if strict_mask[i] else (0.08 if prot_mask[i] else 0.0))
                        for i, s in enumerate(scores)
                    ]
                    desc_order = [i for i in sorted(range(n), key=lambda k: -eff_scores[k]) if atoms[i].text]
                    asc_order = list(reversed(desc_order))
            else:
                if prep["strict_cost"] <= 1.05 * budget:
                    forced = list(strict_mask)
                else:
                    eff_scores = [s + (0.15 if strict_mask[i] else 0.0) for i, s in enumerate(scores)]
                    desc_order = [i for i in sorted(range(n), key=lambda k: -eff_scores[k]) if atoms[i].text]
                    asc_order = list(reversed(desc_order))

        fs_pin = prep.get("fewshot_pin")
        if fs_pin is not None:
            forced = [f or p for f, p in zip(forced, fs_pin)]

        if keep_structure:
            for i, a in enumerate(atoms):
                if a.text in STRUCTURE:
                    forced[i] = True

        if force_tokens:
            ft_set = set(force_tokens)
            for i, a in enumerate(atoms):
                if a.text in ft_set:
                    forced[i] = True

        if threshold is not None:
            keep = [f or s >= threshold for f, s in zip(forced, eff_scores)]
            pinned = list(forced)
        else:
            keep = list(forced)
            pinned = list(forced)
            used = sum(c for c, f in zip(costs, forced) if f)
            if used < budget:
                eff_min = prep["eff_min_block"]
                if eff_min > 0 and len(prep["paras"]) >= 4:
                    for p_order, p_cost in zip(prep["para_orders"], prep["para_costs"]):
                        p_budget = eff_min * keep_rate * p_cost
                        p_used = sum(costs[j] for j in p_order if forced[j])
                        p_iter = (
                            [j for j in sorted(p_order, key=lambda k: -eff_scores[k])]
                            if eff_scores is not scores
                            else p_order
                        )
                        for j in p_iter:
                            if used >= budget or p_used >= p_budget:
                                break
                            if not keep[j]:
                                keep[j] = True
                                used += costs[j]
                                p_used += costs[j]
                for i in desc_order:
                    if used >= budget:
                        break
                    if not keep[i]:
                        keep[i] = True
                        used += costs[i]

            if prep["para_headers"] or prep["line_to_md_ancestors"]:
                _pin_headers(
                    atoms, costs, prep["lines"], prep["para_headers"],
                    prep["line_to_md_ancestors"], keep, pinned, asc_order, budget,
                )

            _pin_negation_anchors(
                atoms, costs, keep, pinned, asc_order, budget, prep.get("neg_plan"), prep.get("nl"),
            )

            if not tech:
                _cleanup_prose_orphans(atoms, costs, prep["lines"], keep, pinned, desc_order, budget)

        dpre = prep.get("detok_pre")
        out = detok_prepared(dpre, keep) if dpre is not None else detok(atoms, keep, preserve_para=True)
        return {"text": out, "kept": sum(c for c, k in zip(costs, keep) if k), "total": total}

    def _finish_selection(
        self,
        prep: dict,
        orig_text: str,
        rate: float | None,
        threshold: float | None,
        force_protected: bool | str,
        keep_structure: bool,
        measure: Callable[[str], int] | None,
        force_tokens: list[str] | None,
    ) -> dict:
        eff_rate = 1.0 if rate is None else float(rate)
        if measure is not None and rate is not None and threshold is None:
            target = rate * measure(orig_text)
            lo, hi = 0.01, 1.0
            best: dict | None = None
            best_len = -1
            res0 = self._slice_prepared(
                prep, min(1.0, max(0.02, eff_rate)), None, force_protected, keep_structure, force_tokens,
            )
            n0 = measure(res0["text"])
            if n0 <= target:
                best, best_len, lo = res0, n0, min(1.0, max(0.02, eff_rate))
            else:
                hi = min(1.0, max(0.02, eff_rate))
            for _ in range(8):
                if hi - lo < 0.004:
                    break
                mid = 0.5 * (lo + hi)
                cand = self._slice_prepared(prep, mid, None, force_protected, keep_structure, force_tokens)
                nc = measure(cand["text"])
                if nc <= target:
                    if nc >= best_len:
                        best, best_len = cand, nc
                    lo = mid
                else:
                    hi = mid
            return best if best is not None else self._slice_prepared(
                prep, lo, None, force_protected, keep_structure, force_tokens,
            )
        return self._slice_prepared(prep, eff_rate, threshold, force_protected, keep_structure, force_tokens)

    def _select_atoms(
        self,
        atoms: list[Atom],
        raw_scores: list[float],
        tok_counts: list[int] | None,
        orig_text: str,
        rate: float | None = 0.5,
        threshold: float | None = None,
        force_protected: bool | str = "auto",
        keep_structure: bool = False,
        unit: str | Callable = "tokens_est",
        measure: Callable[[str], int] | None = None,
        query: str | None = None,
        force_tokens: list[str] | None = None,
        legacy: bool = False,
    ) -> dict:
        if legacy:
            counts = _resolve_unit_costs(atoms, tok_counts, unit)
            prot = bool(force_protected) if isinstance(force_protected, bool) else (force_protected in ("auto", "ids"))
            forced = [
                bool(a.text) and ((prot and is_protected(a.text)) or (keep_structure and a.text in STRUCTURE))
                for a in atoms
            ]
            if threshold is not None:
                keep = [f or s >= threshold for f, s in zip(forced, raw_scores)]
            else:
                budget = math.ceil((rate if rate is not None else 1.0) * sum(counts))
                keep = list(forced)
                used = sum(c for c, f in zip(counts, forced) if f)
                for i in sorted(range(len(atoms)), key=lambda idx: -raw_scores[idx]):
                    if used >= budget:
                        break
                    if not keep[i] and atoms[i].text:
                        keep[i] = True
                        used += counts[i]
            return {
                "text": detok(atoms, keep, preserve_para=False),
                "kept": sum(c for c, k in zip(counts, keep) if k),
                "total": sum(counts),
            }

        prep = self._prepare_selection(atoms, raw_scores, tok_counts, orig_text, unit=unit, query=query)
        return self._finish_selection(
            prep, orig_text, rate, threshold, force_protected, keep_structure, measure, force_tokens,
        )

    def _prune_json_values_only(
        self,
        obj,
        rate: float,
        unit: str | Callable,
        force_protected: bool | str,
        query: str | None,
    ) -> dict:
        """Compact parsed JSON and prune ONLY long prose string values, preserving 100% valid JSON (T1.5)."""
        import json

        compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        orig_atoms = atomize(compact)
        orig_cost = max(1.0, sum(_resolve_unit_costs(orig_atoms, None, unit)))
        target_cost = max(1.0, rate * orig_cost)

        prose_locs: list[tuple[object, object, str, float]] = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str):
                        if len(v) >= 160 and v.count(" ") >= 10 and not v.lstrip().startswith(("http://", "https://", "/")):
                            va = atomize(v)
                            prose_locs.append((node, k, v, sum(_resolve_unit_costs(va, None, unit))))
                    else:
                        walk(v)
            elif isinstance(node, list):
                for idx, v in enumerate(node):
                    if isinstance(v, str):
                        if len(v) >= 160 and v.count(" ") >= 10 and not v.lstrip().startswith(("http://", "https://", "/")):
                            va = atomize(v)
                            prose_locs.append((node, idx, v, sum(_resolve_unit_costs(va, None, unit))))
                    else:
                        walk(v)

        walk(obj)
        if not prose_locs:
            return {"text": compact, "kept": orig_cost, "total": orig_cost}

        prose_cost = sum(x[3] for x in prose_locs)
        non_prose_cost = max(0.0, orig_cost - prose_cost)
        avail_prose = max(0.15 * prose_cost, target_cost - non_prose_cost)
        inner_rate = min(1.0, max(0.12, avail_prose / max(1.0, prose_cost)))
        if inner_rate >= 0.99:
            return {"text": compact, "kept": orig_cost, "total": orig_cost}

        for container, key, val, _ in prose_locs:
            container[key] = self._compress_v04(
                val,
                rate=inner_rate,
                force_protected=force_protected,
                unit=unit,
                query=query,
                mode="prune",
            )["text"]
        out = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        out_cost = sum(_resolve_unit_costs(atomize(out), None, unit))
        return {"text": out, "kept": out_cost, "total": orig_cost}

    def prune_scored(
        self,
        scored: ScoredText,
        rate: float | None = 0.5,
        threshold: float | None = None,
        force_protected: bool | str = "auto",
        keep_structure: bool = False,
        unit: str | Callable = "tokens_est",
        measure: Callable[[str], int] | None = None,
        query: str | None = None,
        force_tokens: list[str] | None = None,
        legacy: bool = False,
    ) -> dict:
        """Prune pre-scored text from ScoredText in sub-millisecond time without invoking the encoder (T1.7)."""
        if not legacy and (unit == "tokens_est" or unit == "tokens"):
            base = scored._cache.get("base")
            if base is None:
                atoms = atomize(scored.text)
                raw_scores = scored.dequantize()
                if len(raw_scores) < len(atoms):
                    raw_scores = raw_scores + [0.0] * (len(atoms) - len(raw_scores))
                elif len(raw_scores) > len(atoms):
                    raw_scores = raw_scores[:len(atoms)]
                base = self._prepare_base(atoms, raw_scores, scored.text, "tokens_est")
                if not base.get("empty"):
                    base["_unit_idx"] = _index_units(atoms, base["units"])
                scored._cache["base"] = base
            prep = self._finish_prep(base, query=query)
            return self._finish_selection(
                prep, scored.text, rate, threshold, force_protected, keep_structure, measure, force_tokens,
            )

        atoms = atomize(scored.text)
        raw_scores = scored.dequantize()
        if len(raw_scores) < len(atoms):
            raw_scores = raw_scores + [0.0] * (len(atoms) - len(raw_scores))
        elif len(raw_scores) > len(atoms):
            raw_scores = raw_scores[:len(atoms)]
        return self._select_atoms(
            atoms,
            raw_scores,
            None,
            scored.text,
            rate=rate,
            threshold=threshold,
            force_protected=force_protected,
            keep_structure=keep_structure,
            unit=unit,
            measure=measure,
            query=query,
            force_tokens=force_tokens,
            legacy=legacy,
        )

    def compress(
        self,
        text: str,
        rate: float | None = 0.5,
        threshold: float | None = None,
        force_protected: bool | str | None = None,
        keep_structure: bool = False,
        unit: str | Callable | None = None,
        measure: Callable[[str], int] | None = None,
        query: str | None = None,
        mode: str = "prune",
        clean_web: bool = False,
        fast: bool = False,
        overlap: int | None = None,
        force_tokens: list[str] | None = None,
        legacy: bool = False,
    ) -> dict:
        """Compress `text` to about `rate` of its size.

        Engine routing (API compatibility, see CHANGELOG 0.4): a call shaped like 0.2/0.3 (`force_protected` unset
        or a bool, `unit` unset/"chars"/"tokens"/callable, none of `query`, `mode`, `clean_web`, `fast`, `overlap`,
        `force_tokens`, `legacy`, and a constructor without `rep_decay`/`fewshot`/`overlap`) runs the frozen 0.3.2 path and returns the same bytes as 0.3.2. Anything new
        (`force_protected="auto"|"ids"`, `query=...`, ...) runs the v0.4 engine, where an unset `force_protected`
        means "auto" and an unset `unit` means "tokens_est". `Compressor(engine="v04"|"v032")` pins one engine.
        """
        if self.engine == "v032" or (self.engine == "auto" and not self._ctor_wants_v04() and not self._wants_v04(
                force_protected, unit, query, mode, clean_web, fast, overlap, force_tokens, legacy)):
            from . import _v032

            return _v032.compress(self, text, rate, threshold, bool(force_protected), keep_structure,
                                  "chars" if unit is None else unit, measure)
        return self._compress_v04(
            text, rate=rate, threshold=threshold,
            force_protected="auto" if force_protected is None else force_protected,
            keep_structure=keep_structure, unit="tokens_est" if unit is None else unit, measure=measure, query=query,
            mode=mode, clean_web=clean_web, fast=fast, overlap=overlap, force_tokens=force_tokens, legacy=legacy)

    def _ctor_wants_v04(self) -> bool:
        """Constructor options that did not exist in 0.3.2 (`rep_decay`, `fewshot`, `overlap`) only act in the v0.4 engine."""
        return self.rep_decay != "all" or self.fewshot != "off" or getattr(self, "overlap", 0) > 0

    @staticmethod
    def _wants_v04(force_protected, unit, query, mode, clean_web, fast, overlap, force_tokens, legacy) -> bool:
        """True when the call uses anything 0.3.2 did not have, so it must run the v0.4 engine."""
        if isinstance(force_protected, str):
            return True
        if unit is not None and not callable(unit) and unit not in ("chars", "tokens"):
            return True
        return (query is not None or mode != "prune" or clean_web or fast or overlap is not None
                or force_tokens is not None or legacy)

    def _compress_v04(
        self,
        text: str,
        rate: float | None = 0.5,
        threshold: float | None = None,
        force_protected: bool | str = "auto",
        keep_structure: bool = False,
        unit: str | Callable = "tokens_est",
        measure: Callable[[str], int] | None = None,
        query: str | None = None,
        mode: str = "prune",
        clean_web: bool = False,
        fast: bool = False,
        overlap: int | None = None,
        force_tokens: list[str] | None = None,
        legacy: bool = False,
    ) -> dict:
        """Keep the highest-scoring atoms until `rate` of the text is kept (v0.4 engine).

        - `force_protected`: `"auto"` (smart technical ID vs natural prose guard), `"ids"` (strict IDs only),
          `True` (strict legacy protection of all numbers/IDs), or `False` (unconstrained).
        - `unit`: `"tokens_est"` (zero-dep subword estimator, default), `"o200k"`, `"cl100k"`, `"chars"`, `"tokens"`,
          or a custom callable mapping atom strings to costs.
        - `query`: optional question/topic prioritizing relevant units via BM25 + proximity + 2nd-hop entity boost.
        - `mode`: `"prune"` (neural v0.4), `"auto"` (lossless-first + JSON-values-only), or `"json"` (JSON values only).
        - `clean_web`: strip SSR hydration blobs, HTML comments, UTM params and markdown table alignment padding.
        - `fast`: two-stage lexical prefilter before the neural encoder on docs >= 6k chars (nearly 3x faster).
        """
        if not text:
            return {"text": "", "kept": 0, "total": 0}
        work = clean_web_text(text) if clean_web else text
        eff_rate = 1.0 if rate is None else float(rate)

        if mode in ("auto", "json") and work.lstrip().startswith(("{", "[")):
            import json

            try:
                obj = json.loads(work)
                orig_atoms = atomize(work)
                orig_cost = max(1.0, sum(_resolve_unit_costs(orig_atoms, None, unit)))
                compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                compact_cost = sum(_resolve_unit_costs(atomize(compact), None, unit))
                if mode == "auto" and compact_cost <= eff_rate * orig_cost:
                    return {"text": compact, "kept": compact_cost, "total": orig_cost}
                return self._prune_json_values_only(obj, eff_rate, unit, force_protected, query)
            except ValueError:
                pass

        if mode == "auto":
            ll_out = _try_lossless_compress(work)
            if ll_out != work:
                orig_cost = max(1.0, sum(_resolve_unit_costs(atomize(work), None, unit)))
                ll_cost = sum(_resolve_unit_costs(atomize(ll_out), None, unit))
                if ll_cost <= eff_rate * orig_cost:
                    return {"text": ll_out, "kept": ll_cost, "total": orig_cost}
                adj_rate = min(1.0, max(0.05, eff_rate * orig_cost / max(1.0, ll_cost)))
                work = ll_out
                eff_rate = adj_rate

        if fast and eff_rate < 0.92 and len(work) >= 6000 and threshold is None:
            orig_cost = max(1.0, sum(_resolve_unit_costs(atomize(work), None, unit)))
            stage1 = _prefilter_cascade(work, min(1.0, max(0.30, eff_rate * 1.8)), query=query)
            s1_cost = max(1.0, sum(_resolve_unit_costs(atomize(stage1), None, unit)))
            adj = min(1.0, max(0.02, eff_rate * orig_cost / s1_cost))
            atoms, scores, tok_counts = self.atom_scores(stage1, overlap=overlap)
            out = self._select_atoms(
                atoms, scores, tok_counts, stage1,
                rate=adj, threshold=None, force_protected=force_protected,
                keep_structure=keep_structure, unit=unit, measure=measure,
                query=query, force_tokens=force_tokens, legacy=legacy,
            )
            out["total"] = orig_cost
            return out

        atoms, scores, tok_counts = self.atom_scores(work, overlap=overlap)
        return self._select_atoms(
            atoms,
            scores,
            tok_counts,
            work,
            rate=rate,
            threshold=threshold,
            force_protected=force_protected,
            keep_structure=keep_structure,
            unit=unit,
            measure=measure,
            query=query,
            force_tokens=force_tokens,
            legacy=legacy,
        )

    def compress_many(
        self,
        texts: list[str],
        rate: float = 0.5,
        query: str | None = None,
        scores: list[float] | None = None,
        force_protected: bool | str = "auto",
        dedup_overlap: bool = True,
        unit: str | Callable = "tokens_est",
        mode: str = "prune",
        clean_web: bool = False,
        force_tokens: list[str] | None = None,
    ) -> list[dict]:
        """Compress retrieved documents under a shared budget with overlap dedup and rank/query weighting (T1.6)."""
        if not texts:
            return []
        cleaned = _strip_chunk_overlaps(texts) if dedup_overlap else list(texts)
        orig_costs = [
            sum(_resolve_unit_costs(atomize(t), None, unit)) if t else 0.0
            for t in texts
        ]
        cur_costs = [
            sum(_resolve_unit_costs(atomize(t), None, unit)) if t else 0.0
            for t in cleaned
        ]
        total_orig = sum(orig_costs)
        target_budget = float(rate) * total_orig
        q_sc = _doc_query_scores(cleaned, query) if query and query.strip() else None
        doc_rates = _allocate_doc_rates(cur_costs, target_budget, scores, q_sc)

        results: list[dict] = []
        for orig_t, doc_t, d_rate, o_cost in zip(texts, cleaned, doc_rates, orig_costs):
            if not doc_t or d_rate <= 0:
                results.append({"text": "", "kept": 0, "total": o_cost})
                continue
            res = self._compress_v04(
                doc_t,
                rate=d_rate,
                force_protected=force_protected,
                unit=unit,
                query=query,
                mode=mode,
                clean_web=clean_web,
                force_tokens=force_tokens,
            )
            res["total"] = o_cost
            results.append(res)
        return results
