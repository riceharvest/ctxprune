"""Teacher compression: ask an LLM to delete low-value words from a chunk.

The instruction adapts LLMLingua-2's deletion-only prompt (MIT, Microsoft,
experiments/llmlingua2/data_collection/compression_instructions.json) and
adds the agent-specific rule that identifiers must be copied verbatim.

Responses are cached on disk keyed by (prompt version, model, chunk text),
so runs are resumable and re-labeling with a new aligner costs nothing.

Prompt versions:
  v1  conservative: delete what the agent won't need (keeps ~80-90% of tool output).
  v2  aggressive: keep about a third, prioritising what the agent needs most. Atoms
      that survive v2 are the most important ones, which gives a ranking signal
      inside v1's large "keep" set.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import httpx

PROMPT_VERSION = "v1"  # default

SYSTEM_PROMPT = (
    "You are an expert at compressing text for an AI agent's context window. "
    "You shorten text by deleting words only, so that the agent can still act "
    "on it as if it had read the original."
)

USER_TEMPLATE = """Compress the text below by deleting unimportant words. Follow these rules exactly:
1. You may ONLY delete words, symbols or lines. Do not change the order of what remains.
2. Copy every kept word exactly as written: same spelling, case and inner punctuation. 'asking'->'ask' is NOT OK.
3. Do not add anything: no new words, symbols, abbreviations, emojis, summaries or explanations.
4. Always keep, character for character, anything the agent may need to reference or reuse later: identifiers and IDs, hashes, UUIDs, file paths, URLs, numbers and units, versions, dates and times, error types, codes and messages, function, class, variable and key names, commands and flags.
5. Delete boilerplate, repeated or near-duplicate lines, filler words, politeness, decorative formatting and anything the agent is unlikely to need.
6. Keep the language of the original text.
Compress as much as you can while keeping all information the agent needs.

Text:
<<<
{text}
>>>

Reply with the compressed text only, without the <<< >>> markers."""


USER_TEMPLATE_V2 = """Compress the text below aggressively by deleting words. Keep only about a third of it: the parts the agent most needs to act on. Follow these rules exactly:
1. You may ONLY delete words, symbols or lines. Do not change the order of what remains.
2. Copy every kept word exactly as written: same spelling, case and inner punctuation. 'asking'->'ask' is NOT OK.
3. Do not add anything: no new words, symbols, abbreviations, emojis, summaries or explanations.
4. Prioritise, character for character: the identifiers, numbers, paths, URLs, versions, error types, codes and messages, names and values that the text is about. Drop secondary ones (e.g. repeated paths, line numbers, timestamps that do not matter) when needed to reach the target.
5. Delete first: boilerplate, repeated or near-duplicate lines, filler words, explanations, decorative formatting. In code, keep signatures, names and the key statements; drop comments, docstrings, blank lines, imports and routine boilerplate first.
6. Keep the language of the original text.

Text:
<<<
{text}
>>>

Reply with the compressed text only, without the <<< >>> markers."""

# v3 = v2 with an explicit word budget: LLMs follow numbers far better than "about a third".
USER_TEMPLATE_V3 = USER_TEMPLATE_V2.replace(
    "Keep only about a third of it: the parts the agent most needs to act on.",
    "The text has {n_words} words; your output must have at most {target} words. "
    "Spend that budget on the parts the agent most needs to act on.",
)

PROMPTS = {"v1": USER_TEMPLATE, "v2": USER_TEMPLATE_V2, "v3": USER_TEMPLATE_V3}
V3_KEEP = 0.3


def build_messages(text: str, prompt: str = PROMPT_VERSION) -> list[dict]:
    n = len(text.split())
    body = PROMPTS[prompt].format(text=text, n_words=n, target=max(1, round(V3_KEEP * n)))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": body},
    ]


def cache_key(model: str, text: str, prompt: str = PROMPT_VERSION) -> str:
    h = hashlib.sha256()
    for part in (prompt, model, text):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


_FENCE_RE = re.compile(r"^\s*(?:<<<|```[\w-]*)\s*\n?|\n?\s*(?:>>>|```)\s*$")
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def clean_output(s: str) -> str:
    s = _THINK_RE.sub("", s)
    return _FENCE_RE.sub("", s).strip()


class Cache:
    """Append-only JSONL cache; safe to resume after a crash."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            with path.open() as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn final line from a killed run
                    self.data[r["key"]] = r["output"]
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a")

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, output: str, meta: dict) -> None:
        self.data[key] = output
        self._f.write(json.dumps({"key": key, "output": output, **meta}) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class OpenAITeacher:
    """Any OpenAI-compatible /chat/completions server (llama.cpp, vLLM, ...)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 max_tokens: int = 2048, temperature: float = 0.0,
                 extra_body: dict | None = None, timeout: float = 600.0, prompt: str = PROMPT_VERSION):
        self.model = model
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra_body = extra_body or {}
        headers = {"Authorization": f"Bearer {api_key or os.environ.get('CTXPRUNE_API_KEY', 'none')}"}
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    async def compress(self, text: str) -> str:
        body = {
            "model": self.model,
            "messages": build_messages(text, self.prompt),
            # A deletion-only output is never longer than its input; ~2 chars
            # per token is a safe upper bound that stops runaway generations.
            "max_tokens": min(self.max_tokens, len(text) // 2 + 64),
            "temperature": self.temperature,
            **self.extra_body,
        }
        for attempt in range(5):
            try:
                r = await self.client.post("/chat/completions", json=body)
                r.raise_for_status()
                return clean_output(r.json()["choices"][0]["message"]["content"] or "")
            except (httpx.HTTPError, KeyError):
                if attempt == 4:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    async def chat(self, messages: list[dict], max_tokens: int = 256) -> str:
        """Plain chat call (used by the QA eval for question writing and reading)."""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": 0.0, **self.extra_body}
        for attempt in range(5):
            try:
                r = await self.client.post("/chat/completions", json=body)
                r.raise_for_status()
                return clean_output(r.json()["choices"][0]["message"]["content"] or "")
            except (httpx.HTTPError, KeyError):
                if attempt == 4:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self.client.aclose()


_STOP = set(
    "a an the of to in on at for and or but is are was were be been being it its this that these those "
    "with as by from just very really please so then there here i you we they he she some any".split()
)


class MockTeacher:
    """Deterministic stand-in for pipeline tests: drops stopwords and
    repeated lines, keeps everything else verbatim."""

    model = "mock-stopword-v1"
    prompt = PROMPT_VERSION

    async def compress(self, text: str) -> str:
        seen, lines = set(), []
        for line in text.splitlines():
            key = line.strip()
            if key and key in seen:
                continue
            seen.add(key)
            lines.append(" ".join(w for w in line.split() if w.lower() not in _STOP))
        return "\n".join(l for l in lines if l)

    async def aclose(self) -> None:
        pass


async def run_teacher(teacher, chunks: list[dict], cache: Cache, concurrency: int = 16,
                      on_done=None) -> tuple[dict[str, str], dict[str, str]]:
    """Compress every chunk (cached). Returns ({chunk_id: output}, {chunk_id: error}).

    Failed chunks are not cached, so a rerun retries them.
    """
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}
    errors: dict[str, str] = {}

    async def one(c: dict):
        key = cache_key(teacher.model, c["text"], teacher.prompt)
        hit = cache.get(key)
        if hit is None:
            async with sem:
                try:
                    hit = await teacher.compress(c["text"])
                except Exception as e:  # keep going; report at the end
                    errors[c["id"]] = f"{type(e).__name__}: {e}"
                    return
            cache.put(key, hit, {"model": teacher.model, "prompt": teacher.prompt, "chunk_id": c["id"]})
        out[c["id"]] = hit
        if on_done:
            on_done()

    await asyncio.gather(*(one(c) for c in chunks))
    return out, errors
