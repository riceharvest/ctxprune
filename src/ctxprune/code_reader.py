"""Deterministic, read-only source outlines for the opt-in MCP code readers."""
from __future__ import annotations

import ast
import math
import os
import re
from collections import Counter


def _python_symbols(source: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = getattr(node, "end_lineno", node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ast.unparse(node.args) if hasattr(ast, "unparse") else "..."
            sig = f"{ 'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({args})"
            if node.returns:
                sig += f" -> {ast.unparse(node.returns)}"
        else:
            bases = ", ".join(ast.unparse(x) for x in node.bases)
            sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
        doc = ast.get_docstring(node, clean=True)
        out.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "start": start, "end": end, "signature": sig,
                    "doc": (doc or "").splitlines()[0] if doc else ""})
    return sorted(out, key=lambda x: (x["start"], x["end"]))


# Deliberately conservative line-based outlines. Ranges identify the declaration
# line; only Python AST nodes promise full declaration extents.
_DECL = re.compile(r"^\s*(?:export\s+|public\s+|private\s+|protected\s+|static\s+|async\s+|abstract\s+|default\s+)*(?:(?:const|let|var|function|class|interface|type|enum|struct|trait|impl|fn|func|def|module|record|union)\s+[^=;{(]+|(?:pub\s+)?(?:async\s+)?fn\s+[^({]+|(?:func\s+\([^)]*\)\s*)?\w+\s*\([^;]*\)\s*(?:->[^;{]+)?)[{(;=]?")
_TYPED_METHOD = re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|virtual|inline|pub|async)\s+)*(?:[\w.$<>?\[\],&*]+\s+)+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:throws\s+[\w., ]+\s*)?[{;]")


def symbols(source: str, path: str) -> list[dict]:
    if path.lower().endswith(".py"):
        parsed = _python_symbols(source)
        if parsed:
            return parsed
    out = []
    for n, line in enumerate(source.splitlines(), 1):
        if (_DECL.match(line) or _TYPED_METHOD.match(line)) and not line.lstrip().startswith(("//", "*", "#")):
            sig = line.strip().rstrip("{").rstrip()
            decl = re.search(r"\b(?:const|let|var|function|class|interface|type|enum|struct|trait|impl|fn|func|def|module|record|union)\s+([A-Za-z_$][\w$]*)", sig)
            gofunc = re.search(r"\bfunc\s*\([^)]*\)\s*([A-Za-z_$][\w$]*)", sig)
            typed = _TYPED_METHOD.match(line)
            match = decl or gofunc or (typed if typed else None)
            name = match.group(1) if match else sig.split()[0]
            kind = next((k for k in ("class", "interface", "type", "enum", "struct", "trait", "impl", "fn", "func", "def") if re.search(rf"\b{k}\b", sig)), "function")
            out.append({"name": name, "kind": kind, "start": n, "end": n, "signature": sig, "doc": ""})
    return out


def _tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.findall(r"[A-Za-z0-9$]+", text.lower())


def rank(items: list[dict], query: str) -> list[dict]:
    q = set(_tokens(query))
    if not q:
        return items
    docs = [_tokens(i["name"] + " " + i["signature"] + " " + i["doc"]) for i in items]
    df = Counter(t for doc in docs for t in set(doc))
    n = max(1, len(docs))
    for item, doc in zip(items, docs):
        tf = Counter(doc)
        score = sum((1 + math.log(tf[t])) * (math.log(1 + (n - df[t] + .5) / (df[t] + .5))) for t in q if tf[t])
        # Exact identifier overlap is an intentional high precision BM25 signal.
        if set(_tokens(item["name"])) & q:
            score += 2.0
        item["score"] = round(score, 4)
    return sorted(items, key=lambda x: (-x["score"], x["start"]))


def read_skeleton(path: str, source: str) -> str:
    lines = source.splitlines()
    entries = symbols(source, path)
    imports = [f"L{i}: {line.strip()}" for i, line in enumerate(lines, 1)
               if re.match(r"\s*(?:import\s|from\s+\S+\s+import\s|#include\s|use\s+|package\s)", line)]
    out = [f"# {os.path.basename(path)} ({len(lines)} lines)"]
    out.extend(imports[:40])
    for item in entries:
        row = f"L{item['start']}-L{item['end']} {item['signature']}"
        if item["doc"]:
            row += f" — {item['doc']}"
        out.append(row)
    return "\n".join(out)


def read_symbols(path: str, source: str, query: str, limit: int = 5) -> str:
    if not query.strip():
        raise ValueError("query is required")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    lines = source.splitlines(keepends=True)
    selected = rank(symbols(source, path), query)[:limit]
    if not selected:
        return "No matching symbols found."
    out = []
    for item in selected:
        out.append(f"# {item['kind']} {item['name']} L{item['start']}-L{item['end']} (BM25 {item['score']})\n")
        out.extend(lines[item["start"] - 1:item["end"]])
        if out[-1] and not out[-1].endswith("\n"):
            out[-1] += "\n"
    return "".join(out).rstrip()
