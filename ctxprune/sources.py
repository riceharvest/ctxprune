"""Permissively licensed source documents, streamed from the Hub.

Every document carries its dataset, license and a `group` key. Train/val/test
splits are assigned per group (repo, MCP server, web domain, conversation
tree), so held-out data never shares a repo or tool with training data.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlparse

import pyarrow.parquet as pq
from huggingface_hub import HfApi, HfFileSystem

PERMISSIVE_CODE_LICENSES = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "unlicense", "cc0-1.0"}


@dataclass(frozen=True)
class Source:
    name: str
    domain: str
    dataset: str
    license: str
    min_chars: int
    max_chars: int


# Dataset commit pinned per corpus build (filled by build_corpus from
# <out>/chunks/revisions.json), so rebuilding with a larger budget yields a
# superset of the same chunks instead of drifting with upstream edits.
REVISIONS: dict[str, str] = {}


def _rows(dataset: str, pattern: str, columns: list[str], seed: int = 0) -> Iterator[dict]:
    """Yield rows from parquet shards matching `pattern`, visiting shards and
    row groups in a seeded random order. Only the requested columns of the
    visited row groups are fetched (HTTP range reads), so sampling a few
    thousand rows from a multi-GB dataset downloads a few hundred MB."""
    rng = random.Random(f"{seed}:{dataset}:{pattern}")
    rev = REVISIONS.get(dataset)
    files = sorted(f for f in HfApi().list_repo_files(dataset, repo_type="dataset", revision=rev)
                   if fnmatch.fnmatch(f, pattern))
    if not files:
        raise FileNotFoundError(f"no files match {dataset}/{pattern}")
    rng.shuffle(files)
    fs = HfFileSystem()
    for path in files:
        at = f"@{rev}" if rev else ""
        with fs.open(f"datasets/{dataset}{at}/{path}", block_size=4 << 20) as fh:
            pf = pq.ParquetFile(fh)
            groups = list(range(pf.metadata.num_row_groups))
            rng.shuffle(groups)
            for g in groups:
                yield from pf.read_row_group(g, columns=columns).to_pylist()


def _doc(src: Source, text: str, group: str, lang: str = "en", ref: str = "") -> dict:
    return {
        "text": text,
        "source": src.name,
        "domain": src.domain,
        "dataset": src.dataset,
        "license": src.license,
        "group": f"{src.name}:{group}",
        "lang": lang,
        "ref": ref,
    }


def _ok(src: Source, text: str, seen: set[str]) -> bool:
    if not (src.min_chars <= len(text.strip())):
        return False
    h = hashlib.sha1(text.encode()).hexdigest()
    if h in seen:
        return False
    seen.add(h)
    return True


def _clip(src: Source, text: str) -> str:
    return text[: src.max_chars]


# --- coding-agent tool outputs -------------------------------------------------

SWE_SMITH = Source("swe_smith_tool", "agent_tool", "SWE-bench/SWE-smith-trajectories", "MIT", 200, 20000)


def swe_smith() -> Iterator[dict]:
    seen: set[str] = set()
    for r in _rows(SWE_SMITH.dataset, "data/tool-*.parquet", ["instance_id", "traj_id", "messages"]):
        repo = r["instance_id"].split(".")[0]
        msgs = r["messages"]
        if isinstance(msgs, str):
            try:
                msgs = json.loads(msgs)
            except json.JSONDecodeError:
                continue
        for m in msgs:
            if m.get("role") != "tool":
                continue
            c = m.get("content")
            text = "".join(p.get("text", "") for p in c) if isinstance(c, list) else (c or "")
            if _ok(SWE_SMITH, text, seen):
                yield _doc(SWE_SMITH, _clip(SWE_SMITH, text), repo, ref=r["traj_id"])


SWE_REBENCH = Source("swe_rebench_tool", "agent_tool", "nebius/SWE-rebench-openhands-trajectories", "CC-BY-4.0", 200, 20000)
SWE_REBENCH_ASSIST = Source("swe_rebench_assistant", "agent_text", "nebius/SWE-rebench-openhands-trajectories", "CC-BY-4.0", 300, 20000)


def swe_rebench(assistant: bool = False) -> Iterator[dict]:
    src = SWE_REBENCH_ASSIST if assistant else SWE_REBENCH
    role = "assistant" if assistant else "tool"
    seen: set[str] = set()
    for r in _rows(src.dataset, "trajectories.parquet", ["trajectory_id", "repo", "trajectory"]):
        for m in r["trajectory"]:
            if m.get("role") != role:
                continue
            text = m.get("content") or ""
            if _ok(src, text, seen):
                yield _doc(src, _clip(src, text), r["repo"], ref=r["trajectory_id"])


# --- MCP tool outputs (real servers: search, scraping, APIs; mostly JSON) -------

TOUCAN = Source("toucan_mcp", "agent_tool", "Agent-Ark/Toucan-1.5M", "Apache-2.0", 200, 20000)


def toucan(config: str = "Kimi-K2") -> Iterator[dict]:
    seen: set[str] = set()
    for r in _rows(TOUCAN.dataset, f"{config}/train-*.parquet", ["uuid", "messages", "metadata"]):
        try:
            msgs = json.loads(r["messages"])
            meta = json.loads(r["metadata"])
            server = meta["mcp_servers"][0]["server_name"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        for m in msgs:
            if m.get("role") not in ("function", "tool"):
                continue
            text = m.get("content") or ""
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False, indent=2)
            if _ok(TOUCAN, text, seen):
                yield _doc(TOUCAN, _clip(TOUCAN, text), server, ref=r["uuid"])


# --- source code (permissive per-file licenses only) ---------------------------

CODE = Source("github_code", "code", "codeparrot/github-code-clean", "per-file (MIT/Apache/BSD/ISC)", 300, 12000)


def github_code() -> Iterator[dict]:
    seen: set[str] = set()
    for r in _rows(CODE.dataset, "data/train-*.parquet", ["code", "repo_name", "path", "language", "license"]):
        if r["license"] not in PERMISSIVE_CODE_LICENSES:
            continue
        text = r["code"]
        if _ok(CODE, text, seen):
            d = _doc(CODE, _clip(CODE, text), r["repo_name"], lang=r["language"].lower(), ref=f"{r['repo_name']}/{r['path']}")
            d["license"] = r["license"]
            yield d


# --- prose -----------------------------------------------------------------------

FINEWEB_EDU = Source("fineweb_edu", "prose", "HuggingFaceFW/fineweb-edu", "ODC-BY-1.0", 400, 12000)


def fineweb_edu() -> Iterator[dict]:
    seen: set[str] = set()
    for r in _rows(FINEWEB_EDU.dataset, "sample/10BT/*.parquet", ["text", "id", "url"]):
        text = r["text"]
        if _ok(FINEWEB_EDU, text, seen):
            host = urlparse(r.get("url") or "").netloc or "unknown"
            yield _doc(FINEWEB_EDU, _clip(FINEWEB_EDU, text), host, ref=r.get("id", ""))


FINEWEB2 = Source("fineweb2", "prose_ml", "HuggingFaceFW/fineweb-2", "ODC-BY-1.0", 400, 12000)
FINEWEB2_LANGS = ["nld_Latn", "deu_Latn", "fra_Latn", "spa_Latn", "por_Latn", "ita_Latn", "pol_Latn", "rus_Cyrl", "cmn_Hani", "jpn_Jpan"]


def fineweb2(langs: list[str] | None = None) -> Iterator[dict]:
    """Round-robin over languages so a small sample is still balanced."""
    langs = langs or FINEWEB2_LANGS
    iters = {l: _rows(FINEWEB2.dataset, f"data/{l}/train/*.parquet", ["text", "id", "url"]) for l in langs}
    seen: set[str] = set()
    while iters:
        for l in list(iters):
            try:
                r = next(iters[l])
            except StopIteration:
                del iters[l]
                continue
            text = r["text"]
            if _ok(FINEWEB2, text, seen):
                host = urlparse(r.get("url") or "").netloc or "unknown"
                yield _doc(FINEWEB2, _clip(FINEWEB2, text), host, lang=l, ref=r.get("id", ""))


# --- chat --------------------------------------------------------------------------

OASST2 = Source("oasst2", "chat", "OpenAssistant/oasst2", "Apache-2.0", 300, 12000)


def oasst2() -> Iterator[dict]:
    seen: set[str] = set()
    for r in _rows(OASST2.dataset, "data/train-*.parquet", ["text", "role", "lang", "message_tree_id", "message_id"]):
        if r.get("role") != "assistant":
            continue
        text = r["text"]
        if _ok(OASST2, text, seen):
            yield _doc(OASST2, _clip(OASST2, text), r["message_tree_id"], lang=r.get("lang") or "unk", ref=r["message_id"])


REGISTRY = {
    "swe_smith_tool": swe_smith,
    "swe_rebench_tool": swe_rebench,
    "swe_rebench_assistant": lambda: swe_rebench(assistant=True),
    "toucan_mcp": toucan,
    "github_code": github_code,
    "fineweb_edu": fineweb_edu,
    "fineweb2": fineweb2,
    "oasst2": oasst2,
}

SOURCE_DATASETS = {
    "swe_smith_tool": SWE_SMITH.dataset,
    "swe_rebench_tool": SWE_REBENCH.dataset,
    "swe_rebench_assistant": SWE_REBENCH_ASSIST.dataset,
    "toucan_mcp": TOUCAN.dataset,
    "github_code": CODE.dataset,
    "fineweb_edu": FINEWEB_EDU.dataset,
    "fineweb2": FINEWEB2.dataset,
    "oasst2": OASST2.dataset,
}

# Default mix, as fractions of the chunk budget. Agent tool output dominates
# because that is where LLMLingua-2 breaks; prose and chat keep it general.
DEFAULT_MIX = {
    "swe_smith_tool": 0.15,
    "swe_rebench_tool": 0.15,
    "toucan_mcp": 0.15,
    "swe_rebench_assistant": 0.05,
    "github_code": 0.15,
    "fineweb_edu": 0.15,
    "fineweb2": 0.10,
    "oasst2": 0.10,
}
