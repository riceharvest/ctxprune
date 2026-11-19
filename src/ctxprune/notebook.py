"""Deterministic, loss-aware views of Jupyter notebooks (#322) and notebook git diffs (#880)."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

_ANSI_SGR = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;:]*m")
_HEADER_RE = re.compile(r"^(# %%(?: \[(?:markdown|raw)\]| output| error)?)$", re.M)
_SPLIT_RE = re.compile(r"\n(?=# %%(?: \[(?:markdown|raw)\]| output| error)?\n)")


def _source(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return "".join(value)
    return ""


def _clean_stream(text: str) -> str:
    if "\x1b" in text:
        text = _ANSI_SGR.sub("", text)
    if "\r" in text:
        out = []
        for ln in text.split("\n"):
            if "\r" in ln:
                segs = [s for s in ln.split("\r") if s]
                ln = segs[-1] if segs else ""
            out.append(ln)
        text = "\n".join(out)
    return text


def _cap_output(text: str, *, max_output_chars: int = 2000, max_output_lines: int = 12) -> str:
    text = _clean_stream(text)
    if max_output_lines > 0:
        lines = text.splitlines()
        if len(lines) > max_output_lines:
            head = max(1, (max_output_lines * 2) // 3)
            tail = max(1, max_output_lines - head)
            omitted = len(lines) - head - tail
            trail_nl = "\n" if text.endswith("\n") else ""
            text = (
                "\n".join(
                    lines[:head]
                    + [f"[... {omitted} lines omitted ({len(lines)} lines total)]"]
                    + lines[-tail:]
                )
                + trail_nl
            )
    if len(text) > max_output_chars:
        total = len(text)
        text = text[:max_output_chars] + f"\n[output truncated; {total} chars total]"
    return text


def _sanitize_non_code(text: str) -> str:
    return _HEADER_RE.sub(r"\1 ", text)


def render_notebook(
    data: str | bytes | dict[str, Any],
    *,
    max_output_chars: int = 2000,
    max_output_lines: int = 12,
) -> str:
    """Render notebook cells as percent format (#322); code cell source is emitted byte-for-byte."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    notebook = json.loads(data) if isinstance(data, str) else data
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise ValueError("expected a notebook dict with a 'cells' list")
    if notebook.get("nbformat") not in (None, 4):
        raise ValueError(f"unsupported nbformat: {notebook.get('nbformat')!r}")

    sections: list[str] = []
    for cell in notebook.get("cells", []):
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type", "code")
        tag = {"code": "# %%", "markdown": "# %% [markdown]", "raw": "# %% [raw]"}.get(kind, "# %%")
        source = _source(cell.get("source", ""))
        if kind == "code":
            sections.append(f"{tag}\n{source}")
        else:
            sections.append(f"{tag}\n{_sanitize_non_code(source)}")
        if kind == "code":
            raw_outs = cell.get("outputs", [])
            coalesced: list[dict[str, Any]] = []
            for o in raw_outs:
                if not isinstance(o, dict):
                    continue
                is_stream = o.get("output_type") == "stream" or ("text" in o and "data" not in o)
                prev_stream = bool(coalesced) and (
                    coalesced[-1].get("output_type") == "stream"
                    or ("text" in coalesced[-1] and "data" not in coalesced[-1])
                )
                if is_stream and prev_stream and coalesced[-1].get("name") == o.get("name"):
                    coalesced[-1] = dict(
                        coalesced[-1],
                        text=_source(coalesced[-1].get("text", "")) + _source(o.get("text", "")),
                    )
                else:
                    coalesced.append(o)
            for output in coalesced:
                text = _source(output.get("text", ""))
                if text:
                    capped = _cap_output(
                        text, max_output_chars=max_output_chars, max_output_lines=max_output_lines
                    )
                    sections.append(f"# %% output\n{_sanitize_non_code(capped)}")
                data_map = output.get("data", {})
                if isinstance(data_map, dict):
                    if "text/plain" in data_map:
                        plain = _source(data_map["text/plain"])
                        if plain:
                            capped = _cap_output(
                                plain,
                                max_output_chars=max_output_chars,
                                max_output_lines=max_output_lines,
                            )
                            sections.append(f"# %% output\n{_sanitize_non_code(capped)}")
                    for mime in sorted(m for m in data_map if m != "text/plain"):
                        value = data_map[mime]
                        if (
                            mime
                            in (
                                "image/png",
                                "image/jpeg",
                                "image/gif",
                                "image/webp",
                                "application/pdf",
                            )
                            or ";base64" in mime
                        ):
                            raw = (
                                "".join(value)
                                if isinstance(value, list)
                                else (value if isinstance(value, str) else "")
                            )
                            try:
                                size = len(base64.b64decode(raw, validate=False))
                            except (ValueError, TypeError):
                                size = 0
                            size_str = f"{size // 1024}KB" if (mime == "image/png" or size >= 1024) else f"{size}B"
                        else:
                            if isinstance(value, (dict, list)) and not (
                                isinstance(value, list) and all(isinstance(x, str) for x in value)
                            ):
                                size = len(
                                    json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
                                )
                            else:
                                size = len(_source(value).encode("utf-8"))
                            size_str = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
                        sections.append(f"# %% output\n# [{mime} {size_str} elided]")
                if output.get("output_type") == "error" or "ename" in output:
                    ename = str(output.get("ename", "Error")).strip()
                    evalue = str(output.get("evalue", "")).strip()
                    err_line = f"{ename}: {evalue}".rstrip()
                    sections.append(f"# %% error\n{_sanitize_non_code(err_line)}")
    return ("\n".join(sections) + "\n") if sections else "# %% [empty notebook]\n"


def extract_code_cells(data: str | bytes | dict[str, Any]) -> list[str]:
    """Extract code cell source strings in order from raw .ipynb JSON/dict or rendered percent format."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    if isinstance(data, dict):
        return [
            _source(c.get("source", ""))
            for c in data.get("cells", [])
            if isinstance(c, dict) and c.get("cell_type", "code") == "code"
        ]
    s = data.lstrip()
    if s.startswith("{"):
        try:
            nb = json.loads(data)
            if isinstance(nb, dict) and isinstance(nb.get("cells"), list):
                return [
                    _source(c.get("source", ""))
                    for c in nb["cells"]
                    if isinstance(c, dict) and c.get("cell_type", "code") == "code"
                ]
        except (json.JSONDecodeError, TypeError):
            pass
    if not data or data == "# %% [empty notebook]\n":
        return []
    body = data[:-1] if data.endswith("\n") else data
    cells: list[str] = []
    for sec in _SPLIT_RE.split(body):
        if sec.startswith("# %%\n"):
            cells.append(sec[5:])
    return cells


_NOTEBOOK_DIFF_PATH = re.compile(r"^(?:diff --git a/|\+\+\+ b/|--- a/)(.+\.ipynb)(?:\s|$)")
_CTR_LINE = re.compile(r'^([ +-])\s*"execution_count":\s*(?:null|[0-9]+),?$')
_EXEC_META_LINE = re.compile(
    r'^([ +-])\s*(?:"iopub\.(?:execute_input|status\.busy|status\.idle)"|"shell\.execute_reply"):\s*"[^"]*",?$'
)
_BLOB_KV_LINE = re.compile(
    r'^([ +-])\s*"((?:image|audio|video)/[\w.+-]+|application/pdf|text/html)":\s*"((?:[^"\\]|\\.)*)",?$'
)
_STR_ELEM_LINE = re.compile(r'^([ +-])\s*("((?:[^"\\]|\\.)*)"),?$')
_B64_BODY = re.compile(r"^(?:[A-Za-z0-9+/=]|\\n)+\Z")
_DATA_URI = re.compile(r"data:(?:image|audio|video)/[\w.+-]+;base64,[A-Za-z0-9+/=]+")


def _classify_diff_line(raw_line: str) -> tuple[str, str, str]:
    ending = "\r\n" if raw_line.endswith("\r\n") else "\n" if raw_line.endswith("\n") else ""
    ln = raw_line[: -len(ending)] if ending else raw_line
    if not ln or ln[0] not in ("+", "-", " ") or ln.startswith(("+++", "---", "@@")):
        return "other", "", raw_line
    m = ln[0]
    if _CTR_LINE.match(ln):
        return "ctr", m, raw_line
    if _EXEC_META_LINE.match(ln):
        return "meta_ts", m, raw_line
    if b := _BLOB_KV_LINE.match(ln):
        try:
            json.loads('"' + b.group(3) + '"')
            return "blob", m, f"{m}<blob {b.group(2)} {len(b.group(3))}>{ending}"
        except ValueError:
            pass
    if s := _STR_ELEM_LINE.match(ln):
        try:
            val = json.loads(s.group(2))
        except ValueError:
            return "raw", m, raw_line
        body = s.group(3)
        if len(body) >= 200 and _B64_BODY.match(body):
            return "blob", m, f"{m}<blob base64 {len(body)}>{ending}"
        if len(body) >= 200 and _DATA_URI.search(body):
            return "blob", m, f"{m}<blob data-uri {len(body)}>{ending}"
        if isinstance(val, str):
            if val.endswith("\n"):
                val = val[:-1]
            if "\n" not in val and not any(ord(c) < 32 and c != "\t" for c in val):
                return "str", m, f"{m}{val}{ending}"
    return "raw", m, raw_line


def _flush_hunk(lines: list[str]) -> list[str]:
    if not lines:
        return []
    ending = "\r\n" if lines[0].endswith("\r\n") else "\n" if lines[0].endswith("\n") else "\n"
    classified = [_classify_diff_line(x) for x in lines]
    meaningful = any(k in ("str", "raw") and m in ("+", "-") for k, m, _ in classified)
    removed_ctr = sum(k == "ctr" and m == "-" for k, m, _ in classified)
    added_ctr = sum(k == "ctr" and m == "+" for k, m, _ in classified)
    out: list[str] = []
    for k, m, rendered in classified:
        if k in ("ctr", "meta_ts"):
            continue
        if k in ("str", "blob"):
            if meaningful or m in ("+", "-"):
                out.append(rendered)
        elif meaningful:
            out.append(rendered)
    if removed_ctr or added_ctr:
        out.append(f"~|execution_count -{removed_ctr} +{added_ctr}{ending}")
    return out


def unescape_notebook_diff(diff: str) -> str:
    """Decode JSON string literals, stub blobs, and fold execution_count lines in .ipynb git diff hunks (#880)."""
    active = False
    result: list[str] = []
    hunk_lines: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            if hunk_lines:
                result.extend(_flush_hunk(hunk_lines))
                hunk_lines = []
            match = _NOTEBOOK_DIFF_PATH.match(line.rstrip("\r\n"))
            if line.startswith("diff --git "):
                active = bool(match)
            elif match:
                active = True
            result.append(line)
            continue
        if active and line.startswith("@@"):
            if hunk_lines:
                result.extend(_flush_hunk(hunk_lines))
                hunk_lines = []
            result.append(line)
            continue
        if active:
            hunk_lines.append(line)
        else:
            result.append(line)
    if hunk_lines:
        result.extend(_flush_hunk(hunk_lines))
    return "".join(result)


decode_ipynb_diff = unescape_notebook_diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ctxprune notebook")
    parser.add_argument("path", nargs="?", help="notebook path; omit to read a git diff from stdin")
    parser.add_argument("--max-output-chars", type=int, default=2000)
    parser.add_argument("--max-output-lines", type=int, default=12)
    args = parser.parse_args(argv)
    if args.path:
        sys.stdout.write(
            render_notebook(
                Path(args.path).read_bytes(),
                max_output_chars=args.max_output_chars,
                max_output_lines=args.max_output_lines,
            )
        )
    else:
        sys.stdout.write(unescape_notebook_diff(sys.stdin.read()))
    return 0
