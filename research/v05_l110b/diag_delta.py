#!/usr/bin/env python3
"""L1.10b: line-shift-proof diagnostic delta, screened on local Claude Code transcripts.

Reads transcripts locally; writes ONLY per-pair aggregate numbers (no text, no paths, hashed session ids).
Usage: diag_delta.py --split dev|held --out pairs_<split>.csv [--root ~/.claude/projects]
"""
import argparse, csv, glob, hashlib, json, os, re, sys
from collections import Counter

CUTOFF = "2026-11-01"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TSC = re.compile(r"^(?P<f>[^\s(][^(]*)\((?P<l>\d+),(?P<c>\d+)\): (?P<s>error|warning) (?P<r>TS\d+): (?P<m>.*)$")
TSC2 = re.compile(r"^(?P<f>\S[^:]*):(?P<l>\d+):(?P<c>\d+) - (?P<s>error|warning) (?P<r>TS\d+): (?P<m>.*)$")
MYPY = re.compile(r"^(?P<f>[^\s:][^:]*):(?P<l>\d+):(?:(?P<c>\d+):)? (?P<s>error|note|warning): (?P<m>.*?)(?:  \[(?P<r>[a-z0-9-]+)\])?$")
RUFF = re.compile(r"^(?P<f>[^\s:][^:]*):(?P<l>\d+):(?P<c>\d+): (?P<r>[A-Z]{1,4}\d{2,4}) (?P<m>.*)$")
PATS = [TSC, TSC2, MYPY, RUFF]


def kind_of(cmd):
    c = cmd.lower()
    if re.search(r"\btsc\b|tsc --|vue-tsc|typecheck|type-check|tsgo", c): return "tsc"
    if re.search(r"\bmypy\b", c): return "mypy"
    if re.search(r"\bruff\b", c): return "ruff"
    if re.search(r"pyright|eslint|cargo (check|clippy|build)|go vet|flake8|pylint|biome", c): return "other"
    return None


def norm_cmd(cmd):
    cmd = re.sub(r"\s+", " ", cmd.strip())
    return cmd


def parse_diag(line):
    line = ANSI.sub("", line)
    for p in PATS:
        m = p.match(line)
        if m:
            d = m.groupdict()
            return (d["f"].strip(), d["s"] if "s" in d and d["s"] else "error", d.get("r") or "", d["m"].strip())
    return None


def split_lines(text):
    """-> (diag multiset Counter, diag raw line blocks {key:[blocks]}, other lines list)."""
    keys = Counter(); blocks = {}; other = []
    cur = None
    for ln in text.split("\n"):
        k = parse_diag(ln)
        if k:
            keys[k] += 1; cur = [ln]; blocks.setdefault(k, []).append(cur)
        elif cur is not None and (ln.startswith(" ") or ln.startswith("\t")) and ln.strip():
            cur.append(ln)  # continuation (tsc multi-line msg)
        else:
            cur = None
            other.append(ln)
    return keys, blocks, other


def fmt_key(k):
    f, s, r, m = k
    return f"{f} {s} {r} {m}".replace("  ", " ")


def encode(prev, curr):
    """Delta of curr vs prev (both raw text). Returns encoded text."""
    pk, _, po = split_lines(prev)
    ck, cb, co = split_lines(curr)
    new = ck - pk; res = pk - ck; same = ck & pk
    poset = set(po)
    out = ["[diag-delta vs previous run of the same command; unchanged diagnostics omitted, line numbers of unchanged may have shifted]"]
    if new:
        out.append(f"NEW {sum(new.values())}:")
        used = Counter()
        for k, n in new.items():
            # emit the last n raw blocks of this key (newest occurrences)
            for blk in cb[k][-n:]:
                out.extend(blk)
    if res:
        out.append(f"RESOLVED {sum(res.values())}:")
        for k, n in res.items():
            out.append(("%dx " % n if n > 1 else "") + "\x1f".join(k))
    out.append(f"UNCHANGED {sum(same.values())}")
    extra = [l for l in co if l.strip() and l not in poset]
    if extra:
        out.append("OTHER:"); out.extend(extra)
    return "\n".join(out)


def decode_keys(prev, enc):
    """Reconstruct curr diagnostic multiset from prev + encoded delta (parses the encoded text only)."""
    pk, _, _ = split_lines(prev)
    lines = enc.split("\n")[1:]
    new_txt = []; res = Counter(); unchanged = None; mode = None
    for ln in lines:
        if ln.startswith("NEW "): mode = "new"; continue
        if ln.startswith("RESOLVED "): mode = "res"; continue
        if ln.startswith("UNCHANGED "): unchanged = int(ln.split()[1]); mode = None; continue
        if ln == "OTHER:": mode = "other"; continue
        if mode == "new": new_txt.append(ln)
        elif mode == "res":
            m = re.match(r"(?:(\d+)x )?(.*)$", ln); n = int(m.group(1) or 1)
            res[tuple(m.group(2).split("\x1f"))] += n
    nk, _, _ = split_lines("\n".join(new_txt))
    cur = (pk - res) + nk
    return cur, unchanged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["dev", "held"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--files-list", default=None)
    ap.add_argument("--relax", action="store_true", help="group by checker kind only (sensitivity), not exact command")
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    tok = lambda s: len(enc.encode(s, disallowed_special=()))
    try:
        from ctxprune import glue
        glue.boot(own_process=False)
        import lossless
        lossy = lambda s: lossless.compress(s)[0]
    except Exception as e:
        print("lossless unavailable:", e, file=sys.stderr); lossy = lambda s: s
    files = [l.strip() for l in open(a.files_list)] if a.files_list else glob.glob(a.root + "/**/*.jsonl", recursive=True)
    rows = []
    for fp in files:
        uses = {}; runs = {}  # id -> (kind, cmd); (kind,cmd) -> [(ts, text)]
        try:
            fh = open(fp, errors="replace")
        except OSError:
            continue
        for ln in fh:
            if '"tool_use"' not in ln and '"tool_result"' not in ln: continue
            try: d = json.loads(ln)
            except Exception: continue
            msg = d.get("message"); cont = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(cont, list): continue
            for c in cont:
                if not isinstance(c, dict): continue
                if c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command", "")
                    k = kind_of(cmd)
                    if k: uses[c["id"]] = (k, norm_cmd(cmd))
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in uses:
                    t = c.get("content")
                    if isinstance(t, list): t = "\n".join(x.get("text", "") for x in t if isinstance(x, dict))
                    if not isinstance(t, str): continue
                    ts = d.get("timestamp", "")
                    runs.setdefault((uses[c["tool_use_id"]][0], "" if a.relax else uses[c["tool_use_id"]][1]), []).append((ts, t))
        sid = hashlib.sha1(os.path.basename(fp).encode()).hexdigest()[:8]
        for (kind, cmd), lst in runs.items():
            for i in range(1, len(lst)):
                ts, curr = lst[i]; prev = lst[i - 1][1]
                if not ts or (ts[:10] >= CUTOFF) != (a.split == "held"): continue
                pk, _, _ = split_lines(prev); ck, _, _ = split_lines(curr)
                if not pk: continue
                e = encode(prev, curr)
                dk, unch = decode_keys(prev, e)
                missed = sum(((ck - dk)).values()); extra = sum((dk - ck).values())
                raw_t = tok(ANSI.sub("", curr)); e_t = tok(e)
                ll = tok(lossy(curr))
                rows.append(dict(split=a.split, tool=kind, session=sid, turn=i + 1, raw_tok=raw_t, lossless_tok=ll,
                                 delta_tok=e_t, delta_fb_tok=min(raw_t, e_t), curr_diags=sum(ck.values()),
                                 prev_diags=sum(pk.values()), new=sum((ck - pk).values()), resolved=sum((pk - ck).values()),
                                 missed=missed, spurious=extra, unchanged_ok=int(unch == sum((ck & pk).values()))))
    with open(a.out, "w", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=list(rows[0].keys()) if rows else ["split"]); w.writeheader(); w.writerows(rows)
    print(f"{a.split}: n_pairs={len(rows)} sessions={len({r['session'] for r in rows})}")


if __name__ == "__main__":
    main()
