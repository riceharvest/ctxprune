"""Runnable fidelity cases for ten new lossless protocol-envelope ideas (#1444-#1453): SSE framing and Responses-API
stream snapshots, LSP ranges, MCP filesystem directory_tree, JSONL LLM request logs, pydantic/FastMCP tool schemas,
OpenAI SDK structured-output reprs, Ethereum JSON-RPC receipts, OpenAI logprobs, Postman collections.
See harness.py for what a pass means.

Conventions used below:
  * every encoder is fail-open (anything that raises hands the input back), self-gating (it returns the input unless
    the result is strictly shorter) and, for roundtrip cases, self-verifying (it runs its own decoder and returns the
    input if decode(encode(x)) != x), so a corner case I did not think of costs nothing worse than a missed saving;
  * in-band markers sit behind a header line, and text that already starts with the header (or the raw escape) is
    escaped, so decode can always tell encoded text from ordinary text; that is why those cases are not idempotent;
  * `equivalent` cases carry an oracle that checks the facts with a different algorithm (a mini JSON Schema validator
    over probe instances, ast, a sequential diff) and a `loses=` that names what is not preserved.
Fixtures marked "captured" were produced by the real tool in this container (pyright 1.1.414, pydantic 2.13, mcp 2.3,
openai 3.45); the rest are reconstructed from the documented formats and say so.
"""
import ast
import hashlib
import json
import re

from harness import Fixture, case

_DEC = json.JSONDecoder()


# ---------------------------------------------------------------------------------------------------------------
# small shared helpers

def _h(seed: str, n: int = 32) -> str:
    """Deterministic hex string that looks like an id or hash."""
    out = ""
    k = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{k}".encode()).hexdigest()
        k += 1
    return out[:n]


def _members(raw: str):
    """[(key, value_start, value_end)] for the members of the JSON object text `raw`; None if raw is not exactly one
    JSON object. Offsets index `raw`, so callers can splice values without re-serialising (whitespace survives)."""
    n = len(raw)

    def ws(i):
        while i < n and raw[i] in " \t\n\r":
            i += 1
        return i

    out = []
    i = ws(0)
    if raw[i:i + 1] != "{":
        return None
    i = ws(i + 1)
    if raw[i:i + 1] == "}":
        return out if ws(i + 1) == n else None
    while True:
        try:
            key, j = _DEC.raw_decode(raw, i)
        except ValueError:
            return None
        if not isinstance(key, str):
            return None
        j = ws(j)
        if raw[j:j + 1] != ":":
            return None
        j = ws(j + 1)
        try:
            _, k = _DEC.raw_decode(raw, j)
        except ValueError:
            return None
        out.append((key, j, k))
        k = ws(k)
        if raw[k:k + 1] == ",":
            i = ws(k + 1)
            continue
        if raw[k:k + 1] == "}":
            return out if ws(k + 1) == n else None
        return None


def _elements(raw: str):
    """[(start, end)] of the elements of the JSON array text `raw`; None if raw is not exactly one JSON array."""
    n = len(raw)

    def ws(i):
        while i < n and raw[i] in " \t\n\r":
            i += 1
        return i

    out = []
    i = ws(0)
    if raw[i:i + 1] != "[":
        return None
    i = ws(i + 1)
    if raw[i:i + 1] == "]":
        return out if ws(i + 1) == n else None
    while True:
        try:
            _, k = _DEC.raw_decode(raw, i)
        except ValueError:
            return None
        out.append((i, k))
        k = ws(k)
        if raw[k:k + 1] == ",":
            i = ws(k + 1)
            continue
        if raw[k:k + 1] == "]":
            return out if ws(k + 1) == n else None
        return None


_LAYOUTS = [dict(separators=(",", ":")), dict(), dict(indent=2), dict(indent=4), dict(indent="\t")]


def _json_layout(text: str):
    """(obj, dumps kwargs, trailing newline) when `text` is exactly json.dumps(obj, **kwargs) for a common layout
    (so re-serialising reproduces every byte), else None. Duplicate keys, 1E5 style numbers, odd whitespace and
    non-canonical escapes all make this None, which makes the callers leave the text alone."""
    tail = "\n" if text.endswith("\n") else ""
    body = text[:-1] if tail else text
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    for ascii_ in (False, True):
        for kw in _LAYOUTS:
            try:
                if json.dumps(obj, ensure_ascii=ascii_, **kw) == body:
                    return obj, dict(kw, ensure_ascii=ascii_), tail
            except Exception:  # noqa: BLE001
                return None
    return None


def _guard(core, decode, *prefixes):
    """Wrap `core(text) -> str`: fail open, escape text that starts like an encoding, self-gate on size, self-verify."""
    esc = prefixes[-1]  # the raw escape is the last prefix

    def encode(text):
        try:
            if text.startswith(prefixes):
                return esc + text
            out = core(text)
            if not isinstance(out, str) or len(out) >= len(text) or decode(out) != text:
                return text
            return out
        except Exception:  # noqa: BLE001
            return text

    return encode


# ---------------------------------------------------------------------------------------------------------------
# #1444 SSE: drop framing that the payload already states: the event: line (= data.type) and sequence_number (= index)

_SSE_RAW = ": raw\n"
_SSE_TYPED = re.compile(r'\{"type":"([A-Za-z0-9_.:-]+)"([,}])')
_SSE_SEQ = re.compile(r'\{"type":"([A-Za-z0-9_.:-]+)","sequence_number":(\d+)([,}])')


def _sse_hdr(e: int, s: int) -> str:
    parts = (["event: lines (= type)"] if e else []) + (["sequence_number (= frame index)"] if s else [])
    return ": omitted, derivable from the payload: " + " and ".join(parts) + "\n"


_SSE_HDRS = {_sse_hdr(e, s): (e, s) for e, s in ((1, 0), (0, 1), (1, 1))}


def _sse_type(data_line: str):
    if not data_line.startswith("data: "):
        return None
    try:
        obj = json.loads(data_line[6:])
    except Exception:  # noqa: BLE001
        return None
    t = obj.get("type") if isinstance(obj, dict) else None
    return t if isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", t) else None


def _sse_data_index(lines):
    """Index of the data line of a one-line or event+data block, else None."""
    if len(lines) == 1 and lines[0].startswith("data: "):
        return 0
    if len(lines) == 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "):
        return 1
    return None


def _sse_core(text: str) -> str:
    if "\r" in text:
        return text
    blocks = text.split("\n\n")
    # events: only when every typed frame has its event line, or none needs one invented
    out, n_e, invent = [], 0, False
    for block in blocks:
        lines = block.split("\n")
        t = _sse_type(lines[-1]) if _sse_data_index(lines) is not None else None
        if len(lines) == 2 and t is not None and lines[0][7:] == t:
            out.append(lines[1])
            n_e += 1
        else:
            invent = invent or (len(lines) == 1 and t is not None)
            out.append(block)
    if not n_e or invent:
        out, n_e = blocks, 0
    # sequence_number: only when every typed compact frame carries the next index right after its type
    k, s_ok, plan = 0, True, []
    for block in out:
        lines = block.split("\n")
        i = _sse_data_index(lines)
        payload = lines[i][6:] if i is not None else ""
        m = _SSE_TYPED.match(payload)
        if m:
            ms = _SSE_SEQ.match(payload)
            if not ms or int(ms.group(2)) != k:
                s_ok = False
                break
            k += 1
            plan.append((lines, i, m.end(1) + 1, ms.end(2)))
        else:
            plan.append((lines, None, 0, 0))
    s_on = s_ok and k > 0
    if s_on:
        res = []
        for lines, i, cut_from, cut_to in plan:
            if i is not None:
                p = lines[i][6:]
                lines[i] = "data: " + p[:cut_from] + p[cut_to:]
            res.append("\n".join(lines))
        out = res
    if not n_e and not s_on:
        return text
    return _sse_hdr(1 if n_e else 0, 1 if s_on else 0) + "\n\n".join(out)


def sse_restore(text: str) -> str:
    if text.startswith(_SSE_RAW):
        return text[len(_SSE_RAW):]
    for hdr, (e, s) in _SSE_HDRS.items():
        if text.startswith(hdr):
            break
    else:
        return text
    out, k = [], 0
    for block in text[len(hdr):].split("\n\n"):
        lines = block.split("\n")
        i = _sse_data_index(lines)
        if i is not None and s:
            payload = lines[i][6:]
            m = _SSE_TYPED.match(payload)
            if m:
                lines[i] = "data: " + payload[:m.end(1) + 1] + ',"sequence_number":%d' % k + payload[m.end(1) + 1:]
                k += 1
        if e and len(lines) == 1:
            t = _sse_type(lines[0])
            if t is not None:
                lines.insert(0, f"event: {t}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


sse_drop_event = _guard(_sse_core, sse_restore, *_SSE_HDRS, _SSE_RAW)


def _compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _anthropic_stream(words, tool=True) -> str:  # reconstructed from the documented Messages streaming format
    ev = []

    def add(t, obj):
        ev.append(f"event: {t}\ndata: {_compact(obj)}")

    add("message_start", {"type": "message_start", "message": {"id": "msg_01XFDUDYJgAACzvnptvVoYEL", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5-20250929", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 472, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 2, "service_tier": "standard"}}})
    add("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    add("ping", {"type": "ping"})
    for w in words:
        add("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w}})
    add("content_block_stop", {"type": "content_block_stop", "index": 0})
    if tool:
        add("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_01A09q90qw90lq917835lq9", "name": "get_weather", "input": {}}})
        for frag in ['', '{"ci', 'ty": "Par', 'is", "un', 'it": "cel', 'sius"}']:
            add("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": frag}})
        add("content_block_stop", {"type": "content_block_stop", "index": 1})
    add("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 64}})
    add("message_stop", {"type": "message_stop"})
    return "\n\n".join(ev) + "\n\n"


def _chat_chunks(words) -> str:  # OpenAI chat.completions streaming: no event lines at all
    out = []
    for i, w in enumerate(words):
        delta = {"role": "assistant", "content": w} if i == 0 else {"content": w}
        out.append("data: " + _compact({"id": "chatcmpl-AbCdEf123", "object": "chat.completion.chunk", "created": 1730000000, "model": "gpt-4o-mini-2024-07-18", "system_fingerprint": "fp_0ba0d124f1", "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": None}]}))
    out.append("data: [DONE]")
    return "\n\n".join(out) + "\n\n"


_WORDS = "The weather in Paris is usually mild in spring , with highs around 18 degrees and the occasional shower , so a light jacket and an umbrella are a good idea .".split(" ")
_ANTHROPIC = _anthropic_stream([w if i == 0 else " " + w for i, w in enumerate(_WORDS)])
_MIXED = _anthropic_stream(["Hi", "!"], tool=False).replace("event: message_stop", "event: done")  # one frame whose event name differs
_DATA_ONLY = _anthropic_stream(["Hi", "!"], tool=False) + 'data: {"type":"ping"}\n\n'  # typed frame without an event line
_MCP_SSE = "".join(f"event: message\nid: {i}\ndata: " + _compact({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": f"row {i}"}]}}) + "\n\n" for i in range(1, 6))


def _resp_stream(words, text_escapes=False):  # reconstructed from the documented Responses API streaming events
    rid, mid = "resp_68b0f2a4c1d88193a9e2d7b6c0f4e5a1", "msg_68b0f2a5e0b88193b7a1c3d4e5f60718"
    tool = {"type": "function", "name": "run_shell", "description": "Run a shell command in the project directory and return its standard output and exit code.", "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command line to run with /bin/sh -c"}, "timeout_s": {"type": "integer", "description": "Seconds before the command is killed"}}, "required": ["command", "timeout_s"], "additionalProperties": False}, "strict": True}
    base = {"id": rid, "object": "response", "created_at": 1756295844, "status": "in_progress", "background": False, "error": None, "incomplete_details": None, "instructions": "You are a careful shell assistant. Prefer one command over prose, never run destructive commands without asking, and keep answers under three sentences.", "max_output_tokens": None, "max_tool_calls": None, "model": "gpt-4.1-2025-04-14", "output": [], "parallel_tool_calls": True, "previous_response_id": None, "prompt_cache_key": None, "reasoning": {"effort": None, "summary": None}, "safety_identifier": None, "service_tier": "auto", "store": True, "temperature": 1.0, "text": {"format": {"type": "text"}, "verbosity": "medium"}, "tool_choice": "auto", "tools": [tool], "top_logprobs": 0, "top_p": 1.0, "truncation": "disabled", "usage": None, "user": None, "metadata": {}}
    full = "".join(words)
    part = {"type": "output_text", "annotations": [], "logprobs": [], "text": full}
    item = {"id": mid, "type": "message", "status": "completed", "content": [part], "role": "assistant"}
    done = dict(base, status="completed", output=[item], usage={"input_tokens": 121, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": len(words), "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 121 + len(words)})
    ev = []
    seq = [0]

    def add(t, **kw):
        obj = {"type": t, "sequence_number": seq[0], **kw}
        seq[0] += 1
        ev.append(f"event: {t}\ndata: {json.dumps(obj, separators=(',', ':'), ensure_ascii=not text_escapes)}")

    add("response.created", response=base)
    add("response.in_progress", response=base)
    add("response.output_item.added", output_index=0, item={"id": mid, "type": "message", "status": "in_progress", "content": [], "role": "assistant"})
    add("response.content_part.added", item_id=mid, output_index=0, content_index=0, part={"type": "output_text", "annotations": [], "logprobs": [], "text": ""})
    for i, w in enumerate(words):
        add("response.output_text.delta", item_id=mid, output_index=0, content_index=0, delta=w, logprobs=[], obfuscation=_h(f"obf{i}", 12))
    add("response.output_text.done", item_id=mid, output_index=0, content_index=0, text=full, logprobs=[])
    add("response.content_part.done", item_id=mid, output_index=0, content_index=0, part=part)
    add("response.output_item.done", output_index=0, item=item)
    add("response.completed", response=done)
    return "\n\n".join(ev) + "\n\n"


_RESP = _resp_stream([w if i == 0 else " " + w for i, w in enumerate(_WORDS)])

case(
    1444,
    claim="Removes each 'event: X' line whose X equals the type member of its data JSON, and the sequence_number member when it equals the frame's index in every typed frame (Anthropic Messages and OpenAI Responses streams); both are restored from the payload and position, frames that disagree stay as they are, and a stream with a typed data-only frame keeps its event lines.",
    encode=sse_drop_event,
    decode=sse_restore,
    fixtures=[
        Fixture("Anthropic Messages stream, text then tool_use", _ANTHROPIC, True),
        Fixture("short Anthropic stream", _anthropic_stream(["Hi", "!"], tool=False), True),
        Fixture("OpenAI Responses stream", _RESP, True),
        Fixture("one event name differs from its type: that frame keeps its line", _MIXED, True),
        Fixture("OpenAI chat.completions chunks have no event lines", _chat_chunks(_WORDS), False),
        Fixture("MCP Streamable HTTP: event: message, JSON-RPC data without type", _MCP_SSE, False),
        Fixture("a typed data frame without an event line", _DATA_ONLY, False),
        Fixture("input that starts like the header", _sse_hdr(1, 1) + "data: {\"type\":\"x\",\"sequence_number\":0}\n\n", None),
        Fixture("sequence_number off by one in one frame: events only", _RESP.replace('"sequence_number":7,', '"sequence_number":70,'), True),
        Fixture("input that starts like the raw escape", _SSE_RAW + "event: a\ndata: {\"type\":\"a\"}\n\n", None),
    ],
    min_gain=0.08,
    idempotent=False,  # re-encoding encoded text escapes it (it starts with the header), so it grows by design
)


# ---------------------------------------------------------------------------------------------------------------
# #1445 Responses API stream: back-reference the response snapshots and the full-text copies in the done events

_SNAP_BASE = ': elided, same as earlier events: "§Δ" = the text streamed so far, "§created" and "§=" = the same value in response.created'
_SNAP_HDR_U = _SNAP_BASE + "\n"
_SNAP_HDR_A = _SNAP_BASE + " (ascii escapes)\n"
_SNAP_RAW = ": raw\n"
_DONE_TYPES = ("response.output_text.done", "response.content_part.done", "response.output_item.done", "response.completed")


def _snap_walk(blocks, edit):
    """Run the event state machine over `blocks`; `edit(kind, raw, state)` returns the replacement for the payload.
    Shared by encode and decode so both see the same buffer: the text is the concatenation of the deltas since the last
    output_item.added / content_part.added, and created is the member table of the first response.created."""
    st = {"buf": "", "created": None, "created_raw": None}
    out = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) == 2 and lines[0].startswith("event: ") and lines[1].startswith("data: "):
            raw = lines[1][6:]
            try:
                obj = json.loads(raw)
            except Exception:  # noqa: BLE001
                obj = None
            t = obj.get("type") if isinstance(obj, dict) else None
            if t in ("response.output_item.added", "response.content_part.added"):
                st["buf"] = ""
            elif t == "response.output_text.delta" and isinstance(obj.get("delta"), str):
                st["buf"] += obj["delta"]
            new = edit(t, raw, st)
            if t == "response.created":
                mem = _members(raw)
                resp = next(((j, k) for key, j, k in mem if key == "response"), None) if mem else None
                rm = _members(raw[resp[0]:resp[1]]) if resp else None
                if rm is not None:
                    sub = raw[resp[0]:resp[1]]
                    st["created"] = {key: sub[j:k] for key, j, k in rm}
                    st["created_raw"] = sub
            block = lines[0] + "\n" + "data: " + new
        out.append(block)
    return out


def _snap_response_span(raw):
    mem = _members(raw)
    return next(((j, k) for key, j, k in mem if key == "response"), None) if mem else None


def _snap_encode_with(text, ascii_mode):
    def edit(t, raw, st):
        new = raw
        if t in ("response.in_progress", "response.completed") and st["created"] is not None:
            span = _snap_response_span(raw)
            if span:
                sub = raw[span[0]:span[1]]
                if t == "response.in_progress" and sub == st["created_raw"]:
                    new = raw[:span[0]] + '"§created"' + raw[span[1]:]
                elif t == "response.completed":
                    rm = _members(sub)
                    if rm:
                        pieces, last = [], 0
                        for key, j, k in rm:
                            if st["created"].get(key) == sub[j:k] and k - j > 6:
                                pieces.append(sub[last:j] + '"§="')
                                last = k
                        pieces.append(sub[last:])
                        new = raw[:span[0]] + "".join(pieces) + raw[span[1]:]
        if t in _DONE_TYPES and st["buf"]:
            new = new.replace('"text":' + json.dumps(st["buf"], ensure_ascii=ascii_mode, separators=(",", ":")), '"text":"§Δ"')
        return new

    blocks = _snap_walk(text.split("\n\n"), edit)
    return (_SNAP_HDR_A if ascii_mode else _SNAP_HDR_U) + "\n\n".join(blocks)


def _snap_core(text: str) -> str:
    if "\r" in text or "§" in text:
        return text
    for ascii_mode in (False, True):
        out = _snap_encode_with(text, ascii_mode)
        if len(out) < len(text) and snap_restore(out) == text:
            return out
    return text


def snap_restore(text: str) -> str:
    if text.startswith(_SNAP_RAW):
        return text[len(_SNAP_RAW):]
    if text.startswith(_SNAP_HDR_U):
        ascii_mode, body = False, text[len(_SNAP_HDR_U):]
    elif text.startswith(_SNAP_HDR_A):
        ascii_mode, body = True, text[len(_SNAP_HDR_A):]
    else:
        return text

    def edit(t, raw, st):
        new = raw
        if t in ("response.in_progress", "response.completed") and st["created"] is not None:
            span = _snap_response_span(raw)
            if span:
                sub = raw[span[0]:span[1]]
                if t == "response.in_progress" and sub == '"§created"':
                    new = raw[:span[0]] + st["created_raw"] + raw[span[1]:]
                elif t == "response.completed":
                    rm = _members(sub)
                    if rm:
                        pieces, last = [], 0
                        for key, j, k in rm:
                            if sub[j:k] == '"§="' and key in st["created"]:
                                pieces.append(sub[last:j] + st["created"][key])
                                last = k
                        pieces.append(sub[last:])
                        new = raw[:span[0]] + "".join(pieces) + raw[span[1]:]
        if t in _DONE_TYPES and st["buf"]:
            new = new.replace('"text":"§Δ"', '"text":' + json.dumps(st["buf"], ensure_ascii=ascii_mode, separators=(",", ":")))
        return new

    return "\n\n".join(_snap_walk(body.split("\n\n"), edit))


snap_backref = _guard(_snap_core, snap_restore, _SNAP_HDR_U, _SNAP_HDR_A, _SNAP_RAW)

_RESP_UNI = _resp_stream(["Der ", "Regen ", "fällt ", "in ", "Köln ", "– ", "bring ", "einen ", "Schirm ", "mit ", "und ", "ein ", "paar ", "dünne ", "Handschuhe ", "für ", "den ", "Abend ", "…"], text_escapes=True)
_RESP_SHORT = _resp_stream(["OK", "."])

case(
    1445,
    claim="In an OpenAI Responses stream, a repeated response.created snapshot (in_progress) and the unchanged members of the completed snapshot become '§created' / '§=' references, and the full-text copies in the done and completed events become '§Δ'; every event stays, and decode rebuilds each byte.",
    encode=snap_backref,
    decode=snap_restore,
    fixtures=[
        Fixture("Responses stream, 35-word answer, one function tool", _RESP, True),
        Fixture("two-word answer: the snapshots still dominate", _RESP_SHORT, True),
        Fixture("non-ASCII answer with \\u escapes in the text", _RESP_UNI, True),
        Fixture("Anthropic stream has no such events", _ANTHROPIC, False),
        Fixture("Responses stream cut before any done event", _RESP[: _RESP.index("event: response.output_text.done")], True),
        Fixture("input that already contains the marker sigil", _RESP.replace("Paris", "Paris §Δ"), False),
        Fixture("input that starts like the header", _SNAP_HDR_U + "event: a\ndata: {}\n\n", None),
        Fixture("input that starts like the raw escape", _SNAP_RAW + "x\n", None),
    ],
    min_gain=0.15,
    idempotent=False,  # re-encoding encoded text escapes it (it starts with the header), so it grows by design
)


# ---------------------------------------------------------------------------------------------------------------
# #1446 LSP: Range objects to line:character spans in language-server tool results

_LSP_RAW = "§raw\n"
_LSP_LEGEND = '"@a:b-c:d" = Range, start line a character b, end line c character d'
_LSP_HDRS = {"compact": f"§lsp compact JSON: {_LSP_LEGEND}\n", "spaced": f"§lsp spaced JSON: {_LSP_LEGEND}\n",
             "indent2": f"§lsp indent-2 JSON: {_LSP_LEGEND}\n"}
_LSP_TOKEN = re.compile(r'"@(\d+):(\d+)-(\d+):(\d+)"')
_LSP_PAT = {
    "compact": re.compile(r'\{"start":\{"line":(\d+),"character":(\d+)\},"end":\{"line":(\d+),"character":(\d+)\}\}'),
    "spaced": re.compile(r'\{"start": \{"line": (\d+), "character": (\d+)\}, "end": \{"line": (\d+), "character": (\d+)\}\}'),
    "indent2": re.compile(r'\{\n(?P<i> +)"start": \{\n(?P=i)  "line": (\d+),\n(?P=i)  "character": (\d+)\n(?P=i)\},\n(?P=i)"end": \{\n(?P=i)  "line": (\d+),\n(?P=i)  "character": (\d+)\n(?P=i)\}\n(?P<j> *)\}'),
}


def _lsp_line_indent(text: str, pos: int) -> int:
    ls = text.rfind("\n", 0, pos) + 1
    n = 0
    while ls + n < len(text) and text[ls + n] == " ":
        n += 1
    return n


def _lsp_core(text: str) -> str:
    if _LSP_TOKEN.search(text):
        return text  # a real string that looks like a span token would be expanded by decode
    best, best_n = None, 0
    for layout, pat in _LSP_PAT.items():
        n = len(pat.findall(text))
        if n > best_n:
            best, best_n = layout, n
    if best is None:
        return text

    def sub(m):
        if best == "indent2":
            i, j = m.group("i"), m.group("j")
            if len(i) != len(j) + 2 or _lsp_line_indent(m.string, m.start()) != len(j):
                return m.group(0)
            a, b, c, d = m.group(2, 3, 4, 5)
        else:
            a, b, c, d = m.groups()
        return f'"@{a}:{b}-{c}:{d}"'

    return _LSP_HDRS[best] + _LSP_PAT[best].sub(sub, text)


def lsp_restore(text: str) -> str:
    if text.startswith(_LSP_RAW):
        return text[len(_LSP_RAW):]
    for layout, hdr in _LSP_HDRS.items():
        if text.startswith(hdr):
            body = text[len(hdr):]
            break
    else:
        return text

    def sub(m):
        a, b, c, d = m.groups()
        if layout == "compact":
            return f'{{"start":{{"line":{a},"character":{b}}},"end":{{"line":{c},"character":{d}}}}}'
        if layout == "spaced":
            return f'{{"start": {{"line": {a}, "character": {b}}}, "end": {{"line": {c}, "character": {d}}}}}'
        p = " " * _lsp_line_indent(m.string, m.start())
        return (f'{{\n{p}  "start": {{\n{p}    "line": {a},\n{p}    "character": {b}\n{p}  }},\n'
                f'{p}  "end": {{\n{p}    "line": {c},\n{p}    "character": {d}\n{p}  }}\n{p}}}')

    return _LSP_TOKEN.sub(sub, body)


lsp_spans = _guard(_lsp_core, lsp_restore, *_LSP_HDRS.values(), _LSP_RAW)

# captured: pyright-langserver 1.1.414 --stdio on a 3-file project (paths shortened to /home/dev/shop)
_LSP_REFS = '[{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":4,"character":6},"end":{"line":4,"character":13}}},{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":10,"character":26},"end":{"line":10,"character":33}}},{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":14,"character":28},"end":{"line":14,"character":35}}},{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":14,"character":54},"end":{"line":14,"character":61}}},{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":16,"character":11},"end":{"line":16,"character":18}}},{"uri":"file:///home/dev/shop/billing.py","range":{"start":{"line":19,"character":29},"end":{"line":19,"character":36}}},{"uri":"file:///home/dev/shop/app.py","range":{"start":{"line":0,"character":20},"end":{"line":0,"character":27}}},{"uri":"file:///home/dev/shop/app.py","range":{"start":{"line":4,"character":10},"end":{"line":4,"character":17}}},{"uri":"file:///home/dev/shop/test_billing.py","range":{"start":{"line":0,"character":20},"end":{"line":0,"character":27}}},{"uri":"file:///home/dev/shop/test_billing.py","range":{"start":{"line":4,"character":10},"end":{"line":4,"character":17}}}]'
_LSP_SYMS = '[{"name":"Invoice","kind":5,"range":{"start":{"line":3,"character":0},"end":{"line":7,"character":25}},"selectionRange":{"start":{"line":4,"character":6},"end":{"line":4,"character":13}},"children":[{"name":"number","kind":13,"range":{"start":{"line":5,"character":4},"end":{"line":5,"character":10}},"selectionRange":{"start":{"line":5,"character":4},"end":{"line":5,"character":10}},"children":[]},{"name":"total_cents","kind":13,"range":{"start":{"line":6,"character":4},"end":{"line":6,"character":15}},"selectionRange":{"start":{"line":6,"character":4},"end":{"line":6,"character":15}},"children":[]},{"name":"currency","kind":13,"range":{"start":{"line":7,"character":4},"end":{"line":7,"character":12}},"selectionRange":{"start":{"line":7,"character":4},"end":{"line":7,"character":12}},"children":[]}]},{"name":"format_total","kind":12,"range":{"start":{"line":10,"character":0},"end":{"line":11,"character":64}},"selectionRange":{"start":{"line":10,"character":4},"end":{"line":10,"character":16}},"children":[{"name":"invoice","kind":13,"range":{"start":{"line":10,"character":17},"end":{"line":10,"character":33}},"selectionRange":{"start":{"line":10,"character":17},"end":{"line":10,"character":33}},"children":[]}]},{"name":"apply_discount","kind":12,"range":{"start":{"line":14,"character":0},"end":{"line":16,"character":63}},"selectionRange":{"start":{"line":14,"character":4},"end":{"line":14,"character":18}},"children":[{"name":"invoice","kind":13,"range":{"start":{"line":14,"character":19},"end":{"line":14,"character":35}},"selectionRange":{"start":{"line":14,"character":19},"end":{"line":14,"character":35}},"children":[]},{"name":"percent","kind":13,"range":{"start":{"line":14,"character":37},"end":{"line":14,"character":49}},"selectionRange":{"start":{"line":14,"character":37},"end":{"line":14,"character":49}},"children":[]},{"name":"new_total","kind":13,"range":{"start":{"line":15,"character":4},"end":{"line":15,"character":13}},"selectionRange":{"start":{"line":15,"character":4},"end":{"line":15,"character":13}},"children":[]}]},{"name":"summarize","kind":12,"range":{"start":{"line":19,"character":0},"end":{"line":21,"character":27}},"selectionRange":{"start":{"line":19,"character":4},"end":{"line":19,"character":13}},"children":[{"name":"invoices","kind":13,"range":{"start":{"line":19,"character":14},"end":{"line":19,"character":37}},"selectionRange":{"start":{"line":19,"character":14},"end":{"line":19,"character":37}},"children":[]},{"name":"lines","kind":13,"range":{"start":{"line":20,"character":4},"end":{"line":20,"character":9}},"selectionRange":{"start":{"line":20,"character":4},"end":{"line":20,"character":9}},"children":[]}]}]'
_LSP_INIT = '{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"textDocumentSync":2,"definitionProvider":{"workDoneProgress":true},"declarationProvider":{"workDoneProgress":true},"typeDefinitionProvider":{"workDoneProgress":true},"referencesProvider":{"workDoneProgress":true},"documentSymbolProvider":{"workDoneProgress":true},"workspaceSymbolProvider":{"workDoneProgress":true},"hoverProvider":{"workDoneProgress":true},"documentHighlightProvider":{"workDoneProgress":true},"renameProvider":{"prepareProvider":true,"workDoneProgress":true},"completionProvider":{"triggerCharacters":[".","[","\\"","\'"],"resolveProvider":true,"workDoneProgress":true,"completionItem":{"labelDetailsSupport":true}},"signatureHelpProvider":{"triggerCharacters":["(",",",")"],"workDoneProgress":true},"codeActionProvider":{"codeActionKinds":["quickfix","source.organizeImports"],"workDoneProgress":true},"executeCommandProvider":{"commands":[],"workDoneProgress":true},"callHierarchyProvider":true,"workspace":{"workspaceFolders":{"supported":true,"changeNotifications":true}}}}}'
_LSP_DIAG = '{"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"file:///home/dev/shop/app.py","version":1,"diagnostics":[{"range":{"start":{"line":9,"character":20},"end":{"line":9,"character":23}},"message":"Argument of type \\"Invoice\\" cannot be assigned to parameter \\"invoices\\" of type \\"list[Invoice]\\" in function \\"summarize\\"\\n\xa0\xa0\\"Invoice\\" is not assignable to \\"list[Invoice]\\"","severity":1,"code":"reportArgumentType","source":"Pyright","codeDescription":{"href":"https://github.com/microsoft/pyright/blob/main/docs/configuration.md#reportArgumentType"}},{"range":{"start":{"line":10,"character":23},"end":{"line":10,"character":24}},"message":"Argument of type \\"Literal[7]\\" cannot be assigned to parameter \\"invoice\\" of type \\"Invoice\\" in function \\"format_total\\"\\n\xa0\xa0\\"Literal[7]\\" is not assignable to \\"Invoice\\"","severity":1,"code":"reportArgumentType","source":"Pyright","codeDescription":{"href":"https://github.com/microsoft/pyright/blob/main/docs/configuration.md#reportArgumentType"}}]}}'

def _diag_many(frame: str, k: int):
    obj = json.loads(frame)
    base = obj["params"]["diagnostics"]
    obj["params"]["diagnostics"] = [
        {**d, "range": {"start": {"line": d["range"]["start"]["line"] + 20 * r, "character": d["range"]["start"]["character"]},
                        "end": {"line": d["range"]["end"]["line"] + 20 * r, "character": d["range"]["end"]["character"]}}}
        for r in range(k) for d in base]
    return obj


case(
    1446,
    claim="Every LSP Range object ({start:{line,character},end:{line,character}}, compact, ', '-separated or 2-space pretty-printed) becomes a \"@a:b-c:d\" span under a one-line legend and decode rebuilds the exact object text; text that already contains a span-shaped string is left alone.",
    encode=lsp_spans,
    decode=lsp_restore,
    fixtures=[
        Fixture("textDocument/references result (captured)", _LSP_REFS, True),
        Fixture("textDocument/documentSymbol result with range and selectionRange (captured)", _LSP_SYMS, True),
        Fixture("the same references result pretty-printed with indent=2", json.dumps(json.loads(_LSP_REFS), indent=2), True),
        Fixture("publishDiagnostics frame, default json.dumps separators, 8 diagnostics (captured payload, entries repeated)", json.dumps(_diag_many(_LSP_DIAG, 4)), True),
        Fixture("a single captured publishDiagnostics frame: the legend costs about what two ranges save", json.dumps(json.loads(_LSP_DIAG)), None),
        Fixture("initialize result has no ranges (captured)", _LSP_INIT, False),
        Fixture("a Range-shaped object with an extra member is not a Range", '[{"start":{"line":1,"character":2},"end":{"line":3,"character":4},"kind":"x"}]', False),
        Fixture("a string that already looks like a span token", _LSP_REFS + ' "@1:2-3:4"', None),
        Fixture("input that starts like the header", _LSP_HDRS["compact"] + "[]", None),
    ],
    min_gain=0.25,
    idempotent=False,  # re-encoding encoded text escapes it (it starts with the header), so it grows by design
)


# ---------------------------------------------------------------------------------------------------------------
# #1447 MCP filesystem server directory_tree: JSON name/type/children objects to an indented outline

_TREE_HDR = "§tree: name/ = directory, files plain, 2 spaces of indent per level\n"
_TREE_RAW = "§raw\n"


def _tree_names_ok(nodes) -> bool:
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("name"), str):
            return False
        name = n["name"]
        if not name or name != name.strip() or "/" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            return False
        if list(n) == ["name", "type"] and n["type"] == "file":
            continue
        if list(n) == ["name", "type", "children"] and n["type"] == "directory" and isinstance(n["children"], list) and _tree_names_ok(n["children"]):
            continue
        return False
    return True


def _tree_lines(nodes, depth, out):
    for n in nodes:
        if n["type"] == "directory":
            out.append("  " * depth + n["name"] + "/")
            _tree_lines(n["children"], depth + 1, out)
        else:
            out.append("  " * depth + n["name"])


def _tree_core(text: str) -> str:
    lay = _json_layout(text)
    if lay is None:
        return text
    obj, kw, tail = lay
    if tail or kw != dict(indent=2, ensure_ascii=False) or not isinstance(obj, list) or not obj or not _tree_names_ok(obj):
        return text
    out = []
    _tree_lines(obj, 0, out)
    return _TREE_HDR + "\n".join(out)


def tree_restore(text: str) -> str:
    if text.startswith(_TREE_RAW):
        return text[len(_TREE_RAW):]
    if not text.startswith(_TREE_HDR):
        return text
    root, stack = [], []  # stack of (depth, children list)
    for line in text[len(_TREE_HDR):].split("\n"):
        body = line.lstrip(" ")
        depth, rem = divmod(len(line) - len(body), 2)
        if rem or not body or depth > len(stack):
            raise ValueError("bad outline")
        del stack[depth:]
        siblings = stack[-1] if stack else root
        if body.endswith("/"):
            node = {"name": body[:-1], "type": "directory", "children": []}
            siblings.append(node)
            stack.append(node["children"])
        else:
            siblings.append({"name": body, "type": "file"})
    return json.dumps(root, indent=2, ensure_ascii=False)


dir_tree_outline = _guard(_tree_core, tree_restore, _TREE_HDR, _TREE_RAW)

# captured: `git ls-files` of this repository (minus the case modules), as the sorted entries directory_tree would list
_TREE_PATHS = '.claude-plugin/\n.claude-plugin/marketplace.json\n.gitignore\nAGENTS.md\nLICENSE\nREADME.md\nassets/\nassets/before-after.html\nassets/before-after.png\nclaude-code-plugin/\nclaude-code-plugin/.claude-plugin/\nclaude-code-plugin/.claude-plugin/plugin.json\nclaude-code-plugin/hooks/\nclaude-code-plugin/hooks/hooks.json\nclaude-code-plugin/scripts/\nclaude-code-plugin/scripts/hook.sh\nctxprune/\nctxprune/__init__.py\nctxprune/__main__.py\nctxprune/align.py\nctxprune/atoms.py\nctxprune/chunking.py\nctxprune/cli.py\nctxprune/compress.py\nctxprune/hook.py\nctxprune/idcheck.py\nctxprune/integrations.py\nctxprune/serve.py\nctxprune/sources.py\nctxprune/teacher.py\nctxprune/tokenlabels.py\ndocs/\ndocs/ideas/\ndocs/ideas/README.md\ndocs/ideas/build.py\ndocs/ideas/dropped.md\ndocs/ideas/gate/\ndocs/ideas/gate/README.md\ndocs/ideas/gate/harness.py\ndocs/ideas/gate/run.py\ndocs/ideas/ideas.jsonl\ndocs/ideas/ideas.md\ndocs/ideas/shortlist.md\npyproject.toml\nrelease/\nrelease/MODEL_CARD.md\nscripts/\nscripts/after_label.sh\nscripts/baseline_idcheck.py\nscripts/baseline_kompress.py\nscripts/build_corpus.py\nscripts/build_neutral_qa.py\nscripts/build_release.py\nscripts/checkpoint.sh\nscripts/demo_data.py\nscripts/eval_external.sh\nscripts/eval_qa.py\nscripts/eval_quick.py\nscripts/exp_labels.sh\nscripts/export_onnx.py\nscripts/final.sh\nscripts/label.py\nscripts/make_hero.py\nscripts/run_v1.sh\nscripts/teacher_up.sh\nscripts/train.py\nspace/\nspace/app.py\nspace/requirements.txt\nspace/static/\nspace/static/README.md\nspace/static/build.py\nspace/static/index.html\nspace/static/js/\nspace/static/js/ctxprune.js\nspace/static/rebuild_page.py\nspace/static/template.html\ntests/\ntests/test_core.py\ntests/test_hook.py\ntests/test_ideas_gate.py\ntests/test_integrations.py\nupstream/\nupstream/PR.md\nupstream/llmlingua-subword-style.patch\nuv.lock'.split("\n")


def _tree_json(paths):
    """directory_tree's text for these paths: JSON.stringify(tree, null, 2) of {name, type[, children]} nodes."""
    root = {}
    for p in paths:
        cur = root
        parts = p.rstrip("/").split("/")
        for i, part in enumerate(parts):
            is_dir = i < len(parts) - 1 or p.endswith("/")
            cur = cur.setdefault(part, {"dir": is_dir, "kids": {}})["kids"]

    def build(level):
        out = []
        for name, v in level.items():
            node = {"name": name, "type": "directory" if v["dir"] else "file"}
            if v["dir"]:
                node["children"] = build(v["kids"])
            out.append(node)
        return out

    return json.dumps(build(root), indent=2, ensure_ascii=False)


_TREE_REPO = _tree_json(_TREE_PATHS)
_TREE_SUB = _tree_json([p for p in _TREE_PATHS if p.startswith("ctxprune/") or p == "ctxprune/"])
_TREE_UNI = _tree_json(["café.md", "docs/", "docs/日本語/", "docs/日本語/メモ.txt", "docs/日本語/résumé.pdf", "naïve/", "src/", "src/main.rs"])
_TREE_EXTRA = _TREE_SUB.replace('"type": "file"', '"type": "file",\n    "size": 12', 1)

case(
    1447,
    claim="A directory_tree JSON text (exactly JSON.stringify(tree, null, 2) of name/type/children nodes) becomes one outline line per entry, directories marked with a trailing slash and children indented two spaces, and decode rebuilds the identical JSON text; anything else is left alone.",
    encode=dir_tree_outline,
    decode=tree_restore,
    fixtures=[
        Fixture("directory_tree of this repository, 86 entries", _TREE_REPO, True),
        Fixture("directory_tree of one package directory", _TREE_SUB, True),
        Fixture("non-ASCII names stay raw as JSON.stringify prints them", _TREE_UNI, True),
        Fixture("list_directory output is not JSON", "[DIR] ctxprune\n[DIR] docs\n[FILE] README.md\n[FILE] pyproject.toml", False),
        Fixture("a node with an extra size member is another tool's shape", _TREE_EXTRA, False),
        Fixture("an empty tree", "[]", False),
        Fixture("a file name with a newline cannot be one outline line", json.dumps([{"name": "a\nb", "type": "file"}, {"name": "c", "type": "file"}], indent=2), False),
        Fixture("input that starts like the header", _TREE_HDR + "a/\n  b\n", None),
        Fixture("input that starts like the raw escape", _TREE_RAW + "[]", None),
    ],
    min_gain=0.5,
    idempotent=False,  # re-encoding encoded text escapes it (it starts with the header), so it grows by design
)


# ---------------------------------------------------------------------------------------------------------------
# #1448 JSONL LLM request logs: back-reference the messages prefix and the unchanged members of the previous request

_REQ_HDR = '§reqlog: "§prev:n" = the first n messages of the previous request line, "§same" = unchanged since the previous request line\n'
_REQ_RAW = "§raw\n"
_REQ_MIN = 40


def _req_scan(line: str):
    """(members, messages span or None, element spans of the messages array) for one request line, or None."""
    mem = _members(line)
    if mem is None:
        return None
    for key, vs, ve in mem:
        if key == "messages":
            els = _elements(line[vs:ve])
            return mem, (vs, ve), els
    return mem, None, None


def _req_encode(text: str) -> str:
    if "§" in text:
        return text
    prev_arr, prev_els, prev_val, out = None, None, {}, []
    for line in text.split("\n"):
        sc = _req_scan(line)
        if sc is None:
            out.append(line)
            continue
        mem, span, els = sc
        edits = []
        for key, vs, ve in mem:
            raw = line[vs:ve]
            if key == "messages":
                if els and prev_arr is not None and prev_els:
                    arr, j = line[vs:ve], 0
                    while j < min(len(els), len(prev_els)) and arr[1:els[j][1]] == prev_arr[1:prev_els[j][1]]:
                        j += 1
                    if j and els[j - 1][1] - 1 > len('"§prev:%d"' % j):
                        edits.append((vs + 1, vs + els[j - 1][1], '"§prev:%d"' % j))
            elif len(raw) >= _REQ_MIN and prev_val.get(key) == raw:
                edits.append((vs, ve, '"§same"'))
        new, last = [], 0
        for s, e, rep in sorted(edits):
            new.append(line[last:s])
            new.append(rep)
            last = e
        new.append(line[last:])
        out.append("".join(new))
        for key, vs, ve in mem:
            prev_val[key] = line[vs:ve]
        if span:
            prev_arr, prev_els = line[span[0]:span[1]], els
    return _REQ_HDR + "\n".join(out)


def _req_decode(text: str) -> str:
    if text.startswith(_REQ_RAW):
        return text[len(_REQ_RAW):]
    if not text.startswith(_REQ_HDR):
        return text
    prev_arr, prev_els, prev_val, out = None, None, {}, []
    for line in text[len(_REQ_HDR):].split("\n"):
        mem = _members(line)
        if mem is None:
            out.append(line)
            continue
        edits = []
        for key, vs, ve in mem:
            raw = line[vs:ve]
            if key == "messages":
                m = re.match(r'\[\s*"§prev:(\d+)"', raw)
                if m:
                    j = int(m.group(1))
                    edits.append((vs + 1, vs + m.end(), prev_arr[1:prev_els[j - 1][1]]))
            elif raw == '"§same"':
                edits.append((vs, ve, prev_val[key]))
        new, last = [], 0
        for s, e, rep in sorted(edits):
            new.append(line[last:s])
            new.append(rep)
            last = e
        new.append(line[last:])
        dec = "".join(new)
        out.append(dec)
        sc = _req_scan(dec)
        mem2, span, els = sc
        for key, vs, ve in mem2:
            prev_val[key] = dec[vs:ve]
        if span:
            prev_arr, prev_els = dec[span[0]:span[1]], els
    return "\n".join(out)


req_log_prefix = _guard(_req_encode, _req_decode, _REQ_HDR, _REQ_RAW)


def _agent_requests(turns: int, ts0: int = 0, sys_prompt=None) -> str:
    """One JSON request body per line, as a logger would write them for an OpenAI-format tool-calling loop; every
    request resends the whole history. Reconstructed (the shape is the public Chat Completions request)."""
    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file from the workspace and return its contents with line numbers.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Workspace-relative file path"}, "offset": {"type": "integer", "description": "First line to return (1-based)"}}, "required": ["path"]}}},
        {"type": "function", "function": {"name": "run_tests", "description": "Run the project's test suite or a single test id and return the pytest summary.", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "Test id such as tests/test_core.py::test_prune"}}, "required": []}}},
        {"type": "function", "function": {"name": "apply_patch", "description": "Apply a unified diff to the workspace.", "parameters": {"type": "object", "properties": {"diff": {"type": "string"}}, "required": ["diff"]}}},
    ]
    msgs = [{"role": "system", "content": sys_prompt or "You are a coding agent. Work in small steps, read before you edit, run the tests after every patch, and stop when they pass."},
            {"role": "user", "content": "tests/test_core.py::test_prune fails with a KeyError, please fix it."}]
    lines = []
    for t in range(turns):
        lines.append(_compact({"ts": f"2025-08-27T10:{t:02d}:{(7 * t) % 60:02d}Z", "model": "gpt-4.1-2025-04-14", "temperature": 0, "tools": tools, "messages": msgs}))
        cid = "call_" + _h(f"c{t}", 24)
        if t % 2 == 0:
            call = {"id": cid, "type": "function", "function": {"name": "read_file", "arguments": _compact({"path": f"ctxprune/module{t}.py", "offset": 1})}}
            out = "\n".join(f"{i + 1:4d}  def helper_{t}_{i}(x):  return x + {i}" for i in range(12))
        else:
            call = {"id": cid, "type": "function", "function": {"name": "run_tests", "arguments": _compact({"target": "tests/test_core.py::test_prune"})}}
            out = "FAILED tests/test_core.py::test_prune - KeyError: 'span'\n1 failed, 14 passed in 0.82s"
        msgs = msgs + [{"role": "assistant", "content": None, "tool_calls": [call]}, {"role": "tool", "tool_call_id": cid, "content": out}]
    return "\n".join(lines) + "\n"


_REQS = _agent_requests(6)
_REQS_PRETTY = json.dumps(json.loads(_REQS.split("\n")[2]), indent=2)

case(
    1448,
    claim="In a JSONL log of LLM request bodies, the leading messages that equal the previous request's become one \"§prev:n\" element and any other member (tools, system, parameters) identical to the previous request's becomes \"§same\"; decode rebuilds every byte, including lines it could not parse.",
    encode=req_log_prefix,
    decode=_req_decode,
    fixtures=[
        Fixture("six requests of a tool-calling loop, each resending the history", _REQS, True),
        Fixture("same loop, 3 requests, system prompt edited after the first", _agent_requests(1) + _agent_requests(3, sys_prompt="You are a terse coding agent.")[0:], True),
        Fixture("a pretty-printed single request is not JSONL", _REQS_PRETTY, False),
        Fixture("application log lines in JSONL", "\n".join(_compact({"level": "info", "msg": f"request {i} done", "ms": 40 + i}) for i in range(8)) + "\n", False),
        Fixture("history that already mentions the marker sigil", _REQS.replace("KeyError, please", "KeyError §prev:2, please"), False),
        Fixture("input that starts like the header", _REQ_HDR + "{}\n", None),
        Fixture("input that starts like the raw escape", _REQ_RAW + "{}\n", None),
    ],
    min_gain=0.4,
    idempotent=False,  # re-encoding encoded text escapes it (it starts with the header), so it grows by design
)


# ---------------------------------------------------------------------------------------------------------------
# #1449 pydantic / FastMCP tool schemas: nullable anyOf to type arrays, single-use $defs inlined, derivable titles dropped

_SCHEMA_KEYS = ("inputSchema", "input_schema", "parameters", "outputSchema")
# keywords that only constrain instances of their own type, so moving them next to a type array changes nothing
_FOLD_OK = {"type", "items", "prefixItems", "minItems", "maxItems", "uniqueItems", "contains", "properties", "required",
            "additionalProperties", "patternProperties", "minProperties", "maxProperties", "propertyNames", "minLength",
            "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
            "title", "description", "examples"}
_REF = re.compile(r"#/\$defs/([A-Za-z0-9_.-]+)")


def _humanize(key: str) -> str:
    return key.replace("_", " ").title()  # pydantic's default field title


def _refs_in(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                _refs_in(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs_in(v, out)


def _inline_defs(root: dict) -> None:
    defs = root.get("$defs")
    if not isinstance(defs, dict) or not defs:
        return
    skip = set()
    while True:
        allrefs = []
        _refs_in(root, allrefs)
        if any(not _REF.fullmatch(r) or _REF.fullmatch(r).group(1) not in defs for r in allrefs):
            return  # a ref we do not understand: touch nothing more
        counts = {n: allrefs.count(f"#/$defs/{n}") for n in defs}
        own = {}
        for n, body in defs.items():
            r = []
            _refs_in(body, r)
            own[n] = f"#/$defs/{n}" in r
        pick = next((n for n in defs if n not in skip and counts[n] == 1 and not own[n]), None)
        if pick is None:
            break

        def swap(node):
            """Replace the one {"$ref": pointer} node under `node`; returns the new node, or None if not found here."""
            if isinstance(node, dict):
                if node.get("$ref") == f"#/$defs/{pick}":
                    sib = {k: v for k, v in node.items() if k != "$ref"}
                    body = defs[pick]
                    if any(k in body and body[k] != v for k, v in sib.items()):
                        raise KeyError("conflicting siblings")
                    return {**body, **sib}
                for k, v in node.items():
                    if k == "$defs":
                        continue
                    new = swap(v)
                    if new is not None:
                        node[k] = new
                        return node
                for k, v in node.get("$defs", {}).items():
                    if k != pick:
                        new = swap(v)
                        if new is not None:
                            node["$defs"][k] = new
                            return node
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    new = swap(v)
                    if new is not None:
                        node[i] = new
                        return node
            return None

        try:
            if swap(root) is None:
                skip.add(pick)
                continue
        except KeyError:
            skip.add(pick)
            continue
        del defs[pick]
    if not defs:
        del root["$defs"]


def _fold(node):
    if isinstance(node, list):
        return [_fold(x) for x in node]
    if not isinstance(node, dict):
        return node
    d = {k: _fold(v) for k, v in node.items()}
    any_ = d.get("anyOf")
    if isinstance(any_, list) and len(any_) == 2 and "type" not in d:
        nulls = [i for i, b in enumerate(any_) if b == {"type": "null"}]
        if len(nulls) == 1:
            a = any_[1 - nulls[0]]
            if (isinstance(a, dict) and isinstance(a.get("type"), str) and a["type"] != "null" and set(a) <= _FOLD_OK
                    and all(k == "anyOf" or d[k] == a[k] for k in a if k in d)):
                new = {}
                for k, v in d.items():
                    if k == "anyOf":
                        for ak, av in a.items():
                            new[ak] = [av, "null"] if ak == "type" else av
                    elif k not in new:
                        new[k] = v
                d = new
    props = d.get("properties")
    if isinstance(props, dict):
        d["properties"] = {pk: ({k: v for k, v in sub.items() if k != "title"} if isinstance(sub, dict) and sub.get("title") == _humanize(pk) else sub)
                           for pk, sub in props.items()}
    return d


def _normalise_schema(s: dict) -> dict:
    _inline_defs(s)
    return _fold(s)


def _schemas_in(node, is_root=True):
    """Rewrite every tool schema slot in a parsed JSON tree (inputSchema, input_schema, parameters, outputSchema, or a bare schema)."""
    if isinstance(node, dict):
        if is_root and node.get("type") == "object" and isinstance(node.get("properties"), dict):
            return _normalise_schema(node)
        for k, v in list(node.items()):
            if k in _SCHEMA_KEYS and isinstance(v, dict):
                node[k] = _normalise_schema(v)
            else:
                node[k] = _schemas_in(v, False)
    elif isinstance(node, list):
        return [_schemas_in(v, False) for v in node]
    return node


def schema_normal_form(text: str) -> str:
    try:
        lay = _json_layout(text)
        if lay is None:
            return text
        obj, kw, tail = lay
        out = json.dumps(_schemas_in(obj), **kw) + tail
        return out if len(out) < len(text) else text
    except Exception:  # noqa: BLE001
        return text


# --- the oracle: a mini JSON Schema validator run over probe instances, plus the annotation facts ---

def _is_type(x, t) -> bool:
    return {"string": isinstance(x, str), "boolean": isinstance(x, bool), "null": x is None, "object": isinstance(x, dict),
            "array": isinstance(x, list), "integer": isinstance(x, int) and not isinstance(x, bool),
            "number": isinstance(x, (int, float)) and not isinstance(x, bool)}.get(t, False)


def _valid(x, s, root, depth=0) -> bool:
    if not isinstance(s, dict) or depth > 12:
        return True
    if "$ref" in s:
        m = _REF.fullmatch(s["$ref"])
        target = (root.get("$defs") or {}).get(m.group(1)) if m else None
        if target is None or not _valid(x, target, root, depth + 1):
            return False
    t = s.get("type")
    if t is not None and not any(_is_type(x, tt) for tt in ([t] if isinstance(t, str) else t)):
        return False
    if "enum" in s and not any(json.dumps(x) == json.dumps(e) for e in s["enum"]):
        return False
    if "anyOf" in s and not any(_valid(x, b, root, depth + 1) for b in s["anyOf"]):
        return False
    if isinstance(x, dict):
        if any(k not in x for k in s.get("required", [])):
            return False
        props = s.get("properties", {})
        if any(k in x and not _valid(x[k], sub, root, depth + 1) for k, sub in props.items()):
            return False
        if s.get("additionalProperties") is False and any(k not in props for k in x):
            return False
    if isinstance(x, list) and "items" in s and not all(_valid(v, s["items"], root, depth + 1) for v in x):
        return False
    if isinstance(x, str) and ("minLength" in s and len(x) < s["minLength"] or "maxLength" in s and len(x) > s["maxLength"]):
        return False
    return True


def _sample(s, root, depth=0):
    """A few valid instances of schema s (as a list), built by a different walk than the encoder's."""
    if not isinstance(s, dict) or depth > 4:
        return [None]
    if "$ref" in s:
        m = _REF.fullmatch(s["$ref"])
        return _sample((root.get("$defs") or {}).get(m.group(1), {}), root, depth + 1) if m else [None]
    if "enum" in s:
        return list(s["enum"])
    if "anyOf" in s:
        return [v for b in s["anyOf"] for v in _sample(b, root, depth + 1)][:4]
    t = s.get("type")
    ts = [t] if isinstance(t, str) else (t or [])
    out = []
    for tt in ts:
        if tt == "object":
            props = s.get("properties", {})
            req = {k: _sample(props.get(k, {}), root, depth + 1)[0] for k in s.get("required", [])}
            full = {k: _sample(sub, root, depth + 1)[0] for k, sub in props.items()}
            out += [req, full]
        elif tt == "array":
            out += [[], [v for v in _sample(s.get("items", {}), root, depth + 1)][:1]]
        else:
            out.append({"string": "x", "integer": 1, "number": 1.5, "boolean": True, "null": None}.get(tt))
    return out or [None]


def _mutations(inst):
    yield inst
    if isinstance(inst, dict):
        for k in list(inst):
            for bad in (None, 7, "zzz", [], {}, True):
                yield {**inst, k: bad}
            yield {kk: v for kk, v in inst.items() if kk != k}
        yield {**inst, "unexpected": 1}


def _schema_facts(s):
    descs, defaults, enums, names, reqs, titles, keys = [], [], [], [], [], [], []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "description" and isinstance(v, str):
                    descs.append(v)
                elif k == "default":
                    defaults.append(json.dumps(v))
                elif k == "enum":
                    enums.append(json.dumps(v))
                elif k == "required" and isinstance(v, list):
                    reqs.append(json.dumps(sorted(map(str, v))))
                elif k == "title" and isinstance(v, str):
                    titles.append(v)
                elif k == "properties" and isinstance(v, dict):
                    names.extend(v)
                    keys.extend(v)
            for k, v in n.items():
                if k == "properties" and isinstance(v, dict):
                    for sub in v.values():
                        walk(sub)
                else:
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(s)
    return sorted(descs), sorted(defaults), sorted(enums), sorted(names), sorted(reqs), titles, set(keys)


def _slots(node, is_root=True):
    out = []
    if isinstance(node, dict):
        if is_root and node.get("type") == "object" and isinstance(node.get("properties"), dict):
            return [node]
        for k, v in node.items():
            if k in _SCHEMA_KEYS and isinstance(v, dict):
                out.append(v)
            else:
                out += _slots(v, False)
    elif isinstance(node, list):
        for v in node:
            out += _slots(v, False)
    return out


def _skeleton(node, is_root=True):
    if isinstance(node, dict):
        if is_root and node.get("type") == "object" and isinstance(node.get("properties"), dict):
            return "<schema>"
        return {k: ("<schema>" if k in _SCHEMA_KEYS and isinstance(v, dict) else _skeleton(v, False)) for k, v in node.items()}
    if isinstance(node, list):
        return [_skeleton(v, False) for v in node]
    return node


def schemas_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        ja, jb = json.loads(a), json.loads(b)
    except Exception:  # noqa: BLE001
        return False
    if _skeleton(ja) != _skeleton(jb):
        return False
    sa, sb = _slots(ja), _slots(jb)
    if len(sa) != len(sb):
        return False
    for x, y in zip(sa, sb):
        fa, fb = _schema_facts(x), _schema_facts(y)
        if fa[:5] != fb[:5]:  # descriptions, defaults, enums, property names, required lists
            return False
        gone = list(fa[5])
        for t in fb[5]:
            if t not in gone:
                return False
            gone.remove(t)
        if any(t not in {_humanize(k) for k in fa[6]} for t in gone):  # a dropped title must be a derivable one
            return False
        probes = [m for root, other in ((x, x), (y, y)) for inst in _sample(root, root) for m in _mutations(inst)]
        for p in probes:
            if _valid(p, x, x) != _valid(p, y, y):
                return False
    return True


_FASTMCP_TOOLS = '{"ttlMs":0,"cacheScope":"private","tools":[{"name":"search_issues","description":"Search issues in a repository.","inputSchema":{"$defs":{"Filter":{"properties":{"field":{"title":"Field","type":"string"},"op":{"default":"eq","enum":["eq","ne","gt","lt"],"title":"Op","type":"string"},"value":{"title":"Value","type":"string"}},"required":["field","value"],"title":"Filter","type":"object"},"Page":{"properties":{"page":{"default":1,"title":"Page","type":"integer"},"per_page":{"default":30,"description":"Results per page (max 100)","title":"Per Page","type":"integer"}},"title":"Page","type":"object"}},"properties":{"repo":{"title":"Repo","type":"string"},"query":{"title":"Query","type":"string"},"state":{"anyOf":[{"enum":["open","closed"],"type":"string"},{"type":"null"}],"default":null,"title":"State"},"labels":{"anyOf":[{"items":{"type":"string"},"type":"array"},{"type":"null"}],"default":null,"title":"Labels"},"assignee":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Assignee"},"filters":{"anyOf":[{"items":{"$ref":"#/$defs/Filter"},"type":"array"},{"type":"null"}],"default":null,"title":"Filters"},"page":{"anyOf":[{"$ref":"#/$defs/Page"},{"type":"null"}],"default":null},"sort":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Sort"}},"required":["repo","query"],"title":"search_issuesArguments","type":"object"},"outputSchema":{"properties":{"result":{"title":"Result","type":"string"}},"required":["result"],"title":"search_issuesOutput","type":"object"}},{"name":"create_issue","description":"Create an issue.","inputSchema":{"$defs":{"Label":{"properties":{"name":{"title":"Name","type":"string"},"color":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Color"}},"required":["name"],"title":"Label","type":"object"}},"properties":{"repo":{"title":"Repo","type":"string"},"title":{"title":"Title","type":"string"},"body":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Body"},"assignees":{"anyOf":[{"items":{"type":"string"},"type":"array"},{"type":"null"}],"default":null,"title":"Assignees"},"milestone":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":null,"title":"Milestone"},"labels":{"anyOf":[{"items":{"$ref":"#/$defs/Label"},"type":"array"},{"type":"null"}],"default":null,"title":"Labels"}},"required":["repo","title"],"title":"create_issueArguments","type":"object"},"outputSchema":{"properties":{"result":{"title":"Result","type":"string"}},"required":["result"],"title":"create_issueOutput","type":"object"}},{"name":"list_commits","description":"List commits on a branch.","inputSchema":{"properties":{"repo":{"title":"Repo","type":"string"},"branch":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Branch"},"author":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Author"},"since":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Since"},"until":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Until"},"path":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Path"},"per_page":{"default":30,"title":"Per Page","type":"integer"},"page":{"default":1,"title":"Page","type":"integer"}},"required":["repo"],"title":"list_commitsArguments","type":"object"},"outputSchema":{"properties":{"result":{"items":{"type":"string"},"title":"Result","type":"array"}},"required":["result"],"title":"list_commitsOutput","type":"object"}},{"name":"update_pull_request","description":"Update a pull request.","inputSchema":{"$defs":{"Reviewer":{"properties":{"login":{"title":"Login","type":"string"},"required":{"default":false,"title":"Required","type":"boolean"}},"required":["login"],"title":"Reviewer","type":"object"}},"properties":{"repo":{"title":"Repo","type":"string"},"number":{"title":"Number","type":"integer"},"title":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Title"},"body":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Body"},"draft":{"anyOf":[{"type":"boolean"},{"type":"null"}],"default":null,"title":"Draft"},"reviewers":{"anyOf":[{"items":{"$ref":"#/$defs/Reviewer"},"type":"array"},{"type":"null"}],"default":null,"title":"Reviewers"},"base":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Base"},"maintainer_can_modify":{"anyOf":[{"type":"boolean"},{"type":"null"}],"default":null,"title":"Maintainer Can Modify"}},"required":["repo","number"],"title":"update_pull_requestArguments","type":"object"},"outputSchema":{"properties":{"result":{"title":"Result","type":"string"}},"required":["result"],"title":"update_pull_requestOutput","type":"object"}},{"name":"get_file_contents","description":"Read a file.","inputSchema":{"properties":{"repo":{"title":"Repo","type":"string"},"path":{"title":"Path","type":"string"},"ref":{"anyOf":[{"type":"string"},{"type":"null"}],"default":null,"title":"Ref"},"max_bytes":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":null,"title":"Max Bytes"}},"required":["repo","path"],"title":"get_file_contentsArguments","type":"object"},"outputSchema":{"properties":{"result":{"title":"Result","type":"string"}},"required":["result"],"title":"get_file_contentsOutput","type":"object"}}],"resultType":"complete"}'
_ZOD_SCHEMA = '{"type":"object","properties":{"path":{"type":"string","description":"File to read"},"head":{"type":"number","description":"Read only the first N lines"}},"required":["path"],"additionalProperties":false,"$schema":"http://json-schema.org/draft-07/schema#"}'  # reconstructed: the shape zod-to-json-schema writes
_UNSAFE = json.dumps({"type": "object", "properties": {"state": {"anyOf": [{"enum": ["open", "closed"], "type": "string"}, {"type": "null"}], "default": None}, "n": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}, "required": []}, separators=(",", ":"))
_RECURSIVE = json.dumps({"type": "object", "properties": {"root": {"$ref": "#/$defs/Node"}}, "$defs": {"Node": {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#/$defs/Node"}}}}}}, separators=(",", ":"))
_TWICE = json.dumps({"type": "object", "properties": {"a": {"$ref": "#/$defs/P"}, "b": {"anyOf": [{"$ref": "#/$defs/P"}, {"type": "null"}], "default": None}}, "required": ["a"], "$defs": {"P": {"type": "object", "properties": {"x": {"type": "integer"}}}}}, separators=(",", ":"))

case(
    1449,
    claim="In pydantic/FastMCP tool schemas, anyOf [T, null] with a plain T becomes type [T, null], a $defs entry referenced exactly once is inlined, and a property title equal to the default title-case of its name is dropped; a mini JSON Schema validator accepts and rejects the same probe instances before and after, and descriptions, defaults, enums, property names and required lists are unchanged.",
    loses="the name of a single-use $defs entry as a separate definition (its own title stays), and which properties carried an explicit title equal to the default title-case of their name (annotation only, no validation effect); JSON formatting is kept",
    encode=schema_normal_form,
    equivalent=schemas_equivalent,
    fixtures=[
        Fixture("tools/list of 5 FastMCP tools with outputSchema (captured, mcp 2.3.0 + pydantic 2.13)", _FASTMCP_TOOLS, True),
        Fixture("the same tools/list pretty-printed with indent=2", json.dumps(json.loads(_FASTMCP_TOOLS), indent=2), True),
        Fixture("one bare inputSchema (captured)", json.dumps(json.loads(_FASTMCP_TOOLS)["tools"][0]["inputSchema"], separators=(",", ":")), True),
        Fixture("zod-to-json-schema output has nothing to fold", _ZOD_SCHEMA, False),
        Fixture("anyOf with an enum branch or two real types is not a nullable type", _UNSAFE, False),
        Fixture("recursive definition referenced from itself and the root", _RECURSIVE, False),
        Fixture("a definition used twice stays hoisted (only the nullable fold applies, so no gain)", _TWICE, None),
        Fixture("not JSON", "name: search_issues\ndescription: Search issues in a repository", False),
    ],
    min_gain=0.12,
)
