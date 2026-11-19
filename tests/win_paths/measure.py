#!/usr/bin/env python3
"""Savings (o200k tokens, % of input) of lossless.compress on the path-listing fixtures, paths on + paths_win on/off,
plus decode round-trip. Run: uv run --no-project --with tiktoken python measure.py"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
import lossless  # noqa: E402
import tiktoken  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
T = lambda s: len(ENC.encode(s, disallowed_special=()))
CMD = {"win_dir_s_b": "dir /s /b", "win_gci_fullname": "Get-ChildItem -Recurse | % FullName", "win_unc": "dir /s /b \\\\fileserver01\\share",
       "win_git_status": "git status", "win_git_status_short": "git status -s", "win_msbuild_errors": "msbuild App.sln",
       "win_mixed_sep": "dir /s /b", "win_gci_table": "Get-ChildItem -Recurse", "linux_find": "find . -type f",
       "linux_rel": "find . -type f", "linux_git_diff_name_only": "git diff --name-only", "linux_compiler_errors": "make"}


def run(win_flag):
    res = {}
    for f in sorted(glob.glob(os.path.join(HERE, "fixtures", "*.txt"))):
        name = os.path.basename(f)[:-4]
        text = open(f, newline="").read()
        out, st = lossless.compress(text, CMD.get(name), paths=True, paths_win=win_flag)
        assert lossless.decode(out) == text, name
        res[name] = (T(text), T(out), st.get("rules"))
    return res


def main():
    for win_flag in (False, True):
        print(f"== paths=True paths_win={win_flag}")
        res = run(win_flag)
        grp = {"win": [0, 0], "linux": [0, 0]}
        for n, (a, b, rules) in res.items():
            g = grp[n.split("_")[0]]
            g[0] += a; g[1] += b
            print(f"  {n:28s} {a:5d} -> {b:5d}  {100*(a-b)/a:5.1f}%  {rules}")
        for k, (a, b) in grp.items():
            print(f"  TOTAL {k}: {a} -> {b}  {100*(a-b)/a:.1f}%")


if __name__ == "__main__":
    main()
