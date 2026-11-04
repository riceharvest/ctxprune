"""New lossless ideas #1424-#1433 (theme: version control, CI and code review): GitHub and GitLab API payloads, git porcelain
output, diff -rq and GitHub Actions annotations.

Reference prototypes, fixtures and oracles; see harness.py for the rules. Conventions used here:

* Real output is captured where the tool exists in the container (git 2.43.0, GNU diff, ruff 0.16.9) and inlined below as
  constants; authors, dates, repos and paths are made up. GitHub and GitLab API payloads cannot be fetched from this sandbox, so
  those fixtures are reconstructed from memory of the documented example responses and marked `# reconstructed from memory`.
* In-band signals start with the sigil "§". Text that itself starts with "§" is escaped with "§raw\\n" (so the escape path may
  grow, and those roundtrip cases set idempotent=False). Encodings whose layout is already a valid output of the tool (blame
  --porcelain, a status line) need no sigil and are "equivalent" cases with an independent expander as oracle.
* Every encoder is self-verifying: it builds the candidate, decodes it again and only returns it if the decode is exactly the
  input and the candidate is shorter. Anything unrecognised therefore comes back untouched, so a wrong guess about a tool's
  format costs savings, never information.
* JSON encoders work on lexemes (a tiny tokenizer keeps number spelling, string escapes, duplicate keys and key order) and only
  re-emit a document when it is byte-identical to its own canonical layout (2-space pretty or compact); other layouts are
  returned unchanged.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from harness import Fixture, case

SIG = "§"
RAW = "§raw\n"


def guarded(text, fn):
    """Total + self-gating wrapper: any failure, or a result that is not strictly shorter, hands the input back."""
    try:
        out = fn(text)
    except Exception:  # noqa: BLE001  (RecursionError, ValueError, IndexError ...: unknown shape means leave it alone)
        return text
    return out if isinstance(out, str) and len(out) < len(text) else text


def sigil_guard(text, build, dec):
    """For a '§'-headed encoding: escape text that starts with the sigil, otherwise build + verify by decoding."""
    if text.startswith(SIG):
        return RAW + text
    try:
        cand = build(text)
        if isinstance(cand, str) and len(cand) < len(text) and dec(cand) == text:
            return cand
    except Exception:  # noqa: BLE001
        pass
    return text


def nl_split(text):
    """'a\\nb\\n' -> (['a', 'b'], True); 'a\\nb' -> (['a', 'b'], False); '' -> ([], False)."""
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return (text.split("\n") if text else []), False
"""Real tool output captured in this container (git 2.43.0, GNU diffutils), authors and dates are made up."""


# git blame -L 5,12 -L 25,28 -L 33,38 --line-porcelain src/app/handlers.py (git 2.43.0)
BLAME_LP = (
    '38639077e8342e3946423a8e72ec4ed7a157b19f 5 5 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 2, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 6 6\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 7 7 1\n'
    'author Ben Reviewer\n'
    'author-mail <ben@example.org>\n'
    'author-time 1770730200\n'
    'author-tz +0100\n'
    'committer Ben Reviewer\n'
    'committer-mail <ben@example.org>\n'
    'committer-time 1770730200\n'
    'committer-tz +0100\n'
    'summary Pass ctx to handlers 3 and 4\n'
    'previous 38639077e8342e3946423a8e72ec4ed7a157b19f src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_3(request, ctx):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 8 8 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 3, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 9 9\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 10 10 1\n'
    'author Ben Reviewer\n'
    'author-mail <ben@example.org>\n'
    'author-time 1770730200\n'
    'author-tz +0100\n'
    'committer Ben Reviewer\n'
    'committer-mail <ben@example.org>\n'
    'committer-time 1770730200\n'
    'committer-tz +0100\n'
    'summary Pass ctx to handlers 3 and 4\n'
    'previous 38639077e8342e3946423a8e72ec4ed7a157b19f src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_4(request, ctx):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 11 11 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 4, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 12 12\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 25 25 1\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_9(request):\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 26 26 1\n'
    'author Ben Reviewer\n'
    'author-mail <ben@example.org>\n'
    'author-time 1770730200\n'
    'author-tz +0100\n'
    'committer Ben Reviewer\n'
    'committer-mail <ben@example.org>\n'
    'committer-time 1770730200\n'
    'committer-tz +0100\n'
    'summary Pass ctx to handlers 3 and 4\n'
    'previous 38639077e8342e3946423a8e72ec4ed7a157b19f src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 9, 'v': 2, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 27 27 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 28 28\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_10(request):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 33 33 1\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    'ad019a1d9ff844c101f10c0ea5f906808ef96e9b 34 34 1\n'
    'author Chen Maintainer\n'
    'author-mail <chen@example.net>\n'
    'author-time 1772370900\n'
    'author-tz -0500\n'
    'committer Chen Maintainer\n'
    'committer-mail <chen@example.net>\n'
    'committer-time 1772370900\n'
    'committer-tz -0500\n'
    'summary Mark handlers needing work\n'
    'previous fe889c00326b541ddaca2858a9fc65146b9a4f99 src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_12(request):  # TODO\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 35 35 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 12, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 36 36\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    '\t\n'
    'ad019a1d9ff844c101f10c0ea5f906808ef96e9b 37 37 1\n'
    'author Chen Maintainer\n'
    'author-mail <chen@example.net>\n'
    'author-time 1772370900\n'
    'author-tz -0500\n'
    'committer Chen Maintainer\n'
    'committer-mail <chen@example.net>\n'
    'committer-time 1772370900\n'
    'committer-tz -0500\n'
    'summary Mark handlers needing work\n'
    'previous fe889c00326b541ddaca2858a9fc65146b9a4f99 src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_13(request):  # FIXME\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 38 38 1\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 13, 'ok': True}\n"
)

# the same range with --porcelain (git 2.43.0)
BLAME_PORCELAIN = (
    '38639077e8342e3946423a8e72ec4ed7a157b19f 5 5 2\n'
    'author Ada Example\n'
    'author-mail <ada@example.com>\n'
    'author-time 1767603600\n'
    'author-tz +0000\n'
    'committer Ada Example\n'
    'committer-mail <ada@example.com>\n'
    'committer-time 1767603600\n'
    'committer-tz +0000\n'
    'summary Initial handlers\n'
    'boundary\n'
    'filename src/app/handlers.py\n'
    "\t    return {'id': 2, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 6 6\n'
    '\t\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 7 7 1\n'
    'author Ben Reviewer\n'
    'author-mail <ben@example.org>\n'
    'author-time 1770730200\n'
    'author-tz +0100\n'
    'committer Ben Reviewer\n'
    'committer-mail <ben@example.org>\n'
    'committer-time 1770730200\n'
    'committer-tz +0100\n'
    'summary Pass ctx to handlers 3 and 4\n'
    'previous 38639077e8342e3946423a8e72ec4ed7a157b19f src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_3(request, ctx):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 8 8 2\n'
    "\t    return {'id': 3, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 9 9\n'
    '\t\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 10 10 1\n'
    '\tdef handler_4(request, ctx):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 11 11 2\n'
    "\t    return {'id': 4, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 12 12\n'
    '\t\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 25 25 1\n'
    '\tdef handler_9(request):\n'
    'fe889c00326b541ddaca2858a9fc65146b9a4f99 26 26 1\n'
    "\t    return {'id': 9, 'v': 2, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 27 27 2\n'
    '\t\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 28 28\n'
    '\tdef handler_10(request):\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 33 33 1\n'
    '\t\n'
    'ad019a1d9ff844c101f10c0ea5f906808ef96e9b 34 34 1\n'
    'author Chen Maintainer\n'
    'author-mail <chen@example.net>\n'
    'author-time 1772370900\n'
    'author-tz -0500\n'
    'committer Chen Maintainer\n'
    'committer-mail <chen@example.net>\n'
    'committer-time 1772370900\n'
    'committer-tz -0500\n'
    'summary Mark handlers needing work\n'
    'previous fe889c00326b541ddaca2858a9fc65146b9a4f99 src/app/handlers.py\n'
    'filename src/app/handlers.py\n'
    '\tdef handler_12(request):  # TODO\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 35 35 2\n'
    "\t    return {'id': 12, 'ok': True}\n"
    '38639077e8342e3946423a8e72ec4ed7a157b19f 36 36\n'
    '\t\n'
    'ad019a1d9ff844c101f10c0ea5f906808ef96e9b 37 37 1\n'
    '\tdef handler_13(request):  # FIXME\n'
    '38639077e8342e3946423a8e72ec4ed7a157b19f 38 38 1\n'
    "\t    return {'id': 13, 'ok': True}\n"
)

# the same range, default format
BLAME_DEFAULT = (
    "^3863907 (Ada Example     2026-01-05 09:00:00 +0000  5)     return {'id': 2, 'ok': True}\n"
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000  6) \n'
    'fe889c00 (Ben Reviewer    2026-02-10 14:30:00 +0100  7) def handler_3(request, ctx):\n'
    "^3863907 (Ada Example     2026-01-05 09:00:00 +0000  8)     return {'id': 3, 'ok': True}\n"
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000  9) \n'
    'fe889c00 (Ben Reviewer    2026-02-10 14:30:00 +0100 10) def handler_4(request, ctx):\n'
    "^3863907 (Ada Example     2026-01-05 09:00:00 +0000 11)     return {'id': 4, 'ok': True}\n"
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 12) \n'
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 25) def handler_9(request):\n'
    "fe889c00 (Ben Reviewer    2026-02-10 14:30:00 +0100 26)     return {'id': 9, 'v': 2, 'ok': True}\n"
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 27) \n'
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 28) def handler_10(request):\n'
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 33) \n'
    'ad019a1d (Chen Maintainer 2026-03-01 08:15:00 -0500 34) def handler_12(request):  # TODO\n'
    "^3863907 (Ada Example     2026-01-05 09:00:00 +0000 35)     return {'id': 12, 'ok': True}\n"
    '^3863907 (Ada Example     2026-01-05 09:00:00 +0000 36) \n'
    'ad019a1d (Chen Maintainer 2026-03-01 08:15:00 -0500 37) def handler_13(request):  # FIXME\n'
    "^3863907 (Ada Example     2026-01-05 09:00:00 +0000 38)     return {'id': 13, 'ok': True}\n"
)

# git reflog (piped, so no decoration)
REFLOG_HEAD = (
    'aebe2e2 HEAD@{0}: checkout: moving from feature/login to main\n'
    '437fc25 HEAD@{1}: checkout: moving from main to feature/login\n'
    'aebe2e2 HEAD@{2}: reset: moving to HEAD\n'
    'aebe2e2 HEAD@{3}: reset: moving to HEAD\n'
    'aebe2e2 HEAD@{4}: reset: moving to HEAD\n'
    'aebe2e2 HEAD@{5}: commit (amend): Add d line (amended)\n'
    'a8808d3 HEAD@{6}: commit: Add d line\n'
    '6b75820 HEAD@{7}: reset: moving to HEAD~1\n'
    '822370f HEAD@{8}: merge fix/typo: Fast-forward\n'
    '6b75820 HEAD@{9}: checkout: moving from fix/typo to main\n'
    '822370f HEAD@{10}: commit: Fix typo in readme\n'
    '6b75820 HEAD@{11}: checkout: moving from main to fix/typo\n'
    "6b75820 HEAD@{12}: merge feature/login: Merge made by the 'ort' strategy.\n"
    '220d7a4 HEAD@{13}: commit: Add c line\n'
    '10d55ba HEAD@{14}: checkout: moving from feature/login to main\n'
    '437fc25 HEAD@{15}: commit: Handle empty password\n'
    'd2aa77d HEAD@{16}: commit: Add login module\n'
    '10d55ba HEAD@{17}: checkout: moving from main to feature/login\n'
    '10d55ba HEAD@{18}: commit: Add b line\n'
    'b6c1b49 HEAD@{19}: commit (initial): Initial commit\n'
)

# git reflog show main
REFLOG_MAIN = (
    'aebe2e2 main@{0}: commit (amend): Add d line (amended)\n'
    'a8808d3 main@{1}: commit: Add d line\n'
    '6b75820 main@{2}: reset: moving to HEAD~1\n'
    '822370f main@{3}: merge fix/typo: Fast-forward\n'
    "6b75820 main@{4}: merge feature/login: Merge made by the 'ort' strategy.\n"
    '220d7a4 main@{5}: commit: Add c line\n'
    '10d55ba main@{6}: commit: Add b line\n'
    'b6c1b49 main@{7}: commit (initial): Initial commit\n'
)

# git reflog show --all | head -12
REFLOG_ALL = (
    '437fc25 refs/heads/feature/login@{0}: commit: Handle empty password\n'
    'aebe2e2 refs/heads/main@{0}: commit (amend): Add d line (amended)\n'
    'a8808d3 refs/heads/main@{1}: commit: Add d line\n'
    '6b75820 refs/heads/main@{2}: reset: moving to HEAD~1\n'
    '822370f refs/heads/main@{3}: merge fix/typo: Fast-forward\n'
    "6b75820 refs/heads/main@{4}: merge feature/login: Merge made by the 'ort' strategy.\n"
    '220d7a4 refs/heads/main@{5}: commit: Add c line\n'
    '10d55ba refs/heads/main@{6}: commit: Add b line\n'
    '258c16a refs/stash@{0}: On main: experiment with g\n'
    '63b834e refs/stash@{1}: WIP on main: aebe2e2 Add d line (amended)\n'
    '94215db refs/stash@{2}: On main: wip: e line\n'
    'aebe2e2 HEAD@{0}: checkout: moving from feature/login to main\n'
)

# git stash list
STASH_LIST = (
    'stash@{0}: On main: experiment with g\n'
    'stash@{1}: WIP on main: aebe2e2 Add d line (amended)\n'
    'stash@{2}: On main: wip: e line\n'
)

# git status in a repo with staged, unstaged, renamed, deleted and untracked paths, 2 commits ahead
STATUS_LONG = (
    'On branch main\n'
    "Your branch is ahead of 'origin/main' by 2 commits.\n"
    '  (use "git push" to publish your local commits)\n'
    '\n'
    'Changes to be committed:\n'
    '  (use "git restore --staged <file>..." to unstage)\n'
    '\trenamed:    docs/index.md -> docs/home.md\n'
    '\tdeleted:    docs/usage.md\n'
    '\tmodified:   src/app/config.py\n'
    '\tnew file:   src/app/models.py\n'
    '\tmodified:   src/app/routes.py\n'
    '\trenamed:    src/lib/cache.py -> src/lib/cache_v2.py\n'
    '\n'
    'Changes not staged for commit:\n'
    '  (use "git add/rm <file>..." to update what will be committed)\n'
    '  (use "git restore <file>..." to discard changes in working directory)\n'
    '\tmodified:   src/app/routes.py\n'
    '\tmodified:   src/lib/cache_v2.py\n'
    '\tmodified:   src/lib/util.py\n'
    '\tdeleted:    tests/test_cache.py\n'
    '\n'
    'Untracked files:\n'
    '  (use "git add <file>..." to include in what will be committed)\n'
    '\t"caf\\\\303\\\\251.txt"\n'
    '\tnotes.txt\n'
    '\tsp ace.txt\n'
    '\ttmp/\n'
    '\n'
)

# git status -sb of the same state (git's own short form)
STATUS_SB = (
    '## main...origin/main [ahead 2]\n'
    'R  docs/index.md -> docs/home.md\n'
    'D  docs/usage.md\n'
    'M  src/app/config.py\n'
    'A  src/app/models.py\n'
    'MM src/app/routes.py\n'
    'RM src/lib/cache.py -> src/lib/cache_v2.py\n'
    ' M src/lib/util.py\n'
    ' D tests/test_cache.py\n'
    '?? "caf\\\\303\\\\251.txt"\n'
    '?? notes.txt\n'
    '?? "sp ace.txt"\n'
    '?? tmp/\n'
)

# git status before the first commit
ST_INITIAL = (
    'On branch main\n'
    '\n'
    'No commits yet\n'
    '\n'
    'nothing to commit (create/copy files and use "git add" to track)\n'
)

# git status, clean tree
ST_CLEAN = (
    'On branch main\n'
    "Your branch is up to date with 'origin/main'.\n"
    '\n'
    'nothing to commit, working tree clean\n'
)

# git status, only untracked files
ST_UNTRACKED = (
    'On branch main\n'
    "Your branch is up to date with 'origin/main'.\n"
    '\n'
    'Untracked files:\n'
    '  (use "git add <file>..." to include in what will be committed)\n'
    '\tnewdir/\n'
    '\tuntracked1.txt\n'
    '\n'
    'nothing added to commit but untracked files present (use "git add" to track)\n'
)

# git status, behind upstream
ST_BEHIND = (
    'On branch main\n'
    "Your branch is behind 'origin/main' by 1 commit, and can be fast-forwarded.\n"
    '  (use "git pull" to update your local branch)\n'
    '\n'
    'nothing to commit, working tree clean\n'
)

# git status, diverged
ST_DIVERGED = (
    'On branch main\n'
    "Your branch and 'origin/main' have diverged,\n"
    'and have 2 and 1 different commits each, respectively.\n'
    '  (use "git pull" if you want to integrate the remote branch with yours)\n'
    '\n'
    'nothing to commit, working tree clean\n'
)

# git status in a conflicted merge
ST_CONFLICT = (
    'On branch main\n'
    "Your branch and 'origin/main' have diverged,\n"
    'and have 2 and 2 different commits each, respectively.\n'
    '  (use "git pull" if you want to integrate the remote branch with yours)\n'
    '\n'
    'You have unmerged paths.\n'
    '  (fix conflicts and run "git commit")\n'
    '  (use "git merge --abort" to abort the merge)\n'
    '\n'
    'Changes to be committed:\n'
    '\tnew file:   c.txt\n'
    '\n'
    'Unmerged paths:\n'
    '  (use "git add <file>..." to mark resolution)\n'
    '\tboth modified:   a.txt\n'
    '\n'
)

# git status -sb of the conflicted merge
ST_CONFLICT_SB = (
    '## main...origin/main [ahead 2, behind 2]\n'
    'UU a.txt\n'
    'A  c.txt\n'
)

# git stash pop that hit a conflict: status block plus a trailing message
ST_STASHPOP = (
    'On branch main\n'
    "Your branch and 'origin/main' have diverged,\n"
    'and have 2 and 2 different commits each, respectively.\n'
    '  (use "git pull" if you want to integrate the remote branch with yours)\n'
    '\n'
    'Changes not staged for commit:\n'
    '  (use "git add <file>..." to update what will be committed)\n'
    '  (use "git restore <file>..." to discard changes in working directory)\n'
    '\tmodified:   b.txt\n'
    '\n'
    'no changes added to commit (use "git add" and/or "git commit -a")\n'
    'The stash entry is kept in case you need it again.\n'
)

# git status on a detached HEAD
ST_DETACHED = (
    'HEAD detached at c23e4df\n'
    'Changes not staged for commit:\n'
    '  (use "git add <file>..." to update what will be committed)\n'
    '  (use "git restore <file>..." to discard changes in working directory)\n'
    '\tmodified:   a.txt\n'
    '\n'
    'no changes added to commit (use "git add" and/or "git commit -a")\n'
)

# diff -rq old new
DRQ_REL = (
    'Only in old: NOTES.txt\n'
    'File old/assets/logo.txt is a regular file while file new/assets/logo.txt is a directory\n'
    'Files old/docs/release notes.md and new/docs/release notes.md differ\n'
    'Files old/docs/usage.md and new/docs/usage.md differ\n'
    'Files old/src/app/main.py and new/src/app/main.py differ\n'
    'Only in new/src/app: models.py\n'
    'Files old/src/lib/cache.py and new/src/lib/cache.py differ\n'
    'Only in old/src/lib: legacy.py\n'
    'Only in new/src/lib: vendor\n'
)

# diff -rq with absolute roots (prefix anonymised to /home/dev/app/builds)
DRQ_ABS = (
    'Only in /home/dev/app/builds/old: NOTES.txt\n'
    'File /home/dev/app/builds/old/assets/logo.txt is a regular file while file /home/dev/app/builds/new/assets/logo.txt is a directory\n'
    'Files /home/dev/app/builds/old/docs/release notes.md and /home/dev/app/builds/new/docs/release notes.md differ\n'
    'Files /home/dev/app/builds/old/docs/usage.md and /home/dev/app/builds/new/docs/usage.md differ\n'
    'Files /home/dev/app/builds/old/src/app/main.py and /home/dev/app/builds/new/src/app/main.py differ\n'
    'Only in /home/dev/app/builds/new/src/app: models.py\n'
    'Files /home/dev/app/builds/old/src/lib/cache.py and /home/dev/app/builds/new/src/lib/cache.py differ\n'
    'Only in /home/dev/app/builds/old/src/lib: legacy.py\n'
    'Only in /home/dev/app/builds/new/src/lib: vendor\n'
)

# diff -rq old/ new/ (trailing-slash roots)
DRQ_SLASH = (
    'Only in old/: NOTES.txt\n'
    'File old/assets/logo.txt is a regular file while file new/assets/logo.txt is a directory\n'
    'Files old/docs/release notes.md and new/docs/release notes.md differ\n'
    'Files old/docs/usage.md and new/docs/usage.md differ\n'
    'Files old/src/app/main.py and new/src/app/main.py differ\n'
    'Only in new/src/app: models.py\n'
    'Files old/src/lib/cache.py and new/src/lib/cache.py differ\n'
    'Only in old/src/lib: legacy.py\n'
    'Only in new/src/lib: vendor\n'
)

# diff -r old/src new/src (normal diff, not -q)
DIFF_R_FULL = (
    'diff -r old/src/app/main.py new/src/app/main.py\n'
    '2a3\n'
    '> changed\n'
    'Only in new/src/app: models.py\n'
    'diff -r old/src/lib/cache.py new/src/lib/cache.py\n'
    '2a3\n'
    '> changed\n'
    'Only in old/src/lib: legacy.py\n'
    'Only in new/src/lib: vendor\n'
)

# ruff check --output-format=github over 5 files, ruff 0.16.9 (runner root path substituted for the scratch dir)
RUFF_GHA = (
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/models.py,line=1,col=8,endLine=1,endColumn=10::src/app/models.py:1:8: F401 `os` imported but unused%0A  help: Remove unused import: `os`\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/models.py,line=2,col=8,endLine=2,endColumn=11::src/app/models.py:2:8: F401 `sys` imported but unused%0A  help: Remove unused import: `sys`\n'
    '::error title=ruff (UP035),file=/home/runner/work/widgets/widgets/src/app/models.py,line=4,col=1,endLine=4,endColumn=30::src/app/models.py:4:1: UP035 `typing.List` is deprecated, use `list` instead\n'
    '::error title=ruff (UP035),file=/home/runner/work/widgets/widgets/src/app/models.py,line=4,col=1,endLine=4,endColumn=30::src/app/models.py:4:1: UP035 `typing.Dict` is deprecated, use `dict` instead\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/models.py,line=4,col=20,endLine=4,endColumn=24::src/app/models.py:4:20: F401 `typing.List` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/models.py,line=4,col=26,endLine=4,endColumn=30::src/app/models.py:4:26: F401 `typing.Dict` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (SIM115),file=/home/runner/work/widgets/widgets/src/app/models.py,line=7,col=22,endLine=7,endColumn=26::src/app/models.py:7:22: SIM115 Use a context manager for opening files\n'
    '::error title=ruff (F841),file=/home/runner/work/widgets/widgets/src/app/models.py,line=8,col=5,endLine=8,endColumn=11::src/app/models.py:8:5: F841 Local variable `unused` is assigned to but never used%0A  help: Remove assignment to unused variable `unused`\n'
    '::error title=ruff (E741),file=/home/runner/work/widgets/widgets/src/app/models.py,line=16,col=9,endLine=16,endColumn=10::src/app/models.py:16:9: E741 Ambiguous variable name: `l`\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/services/billing.py,line=2,col=8,endLine=2,endColumn=15::src/app/services/billing.py:2:8: F401 `decimal` imported but unused%0A  help: Remove unused import: `decimal`\n'
    '::error title=ruff (F403),file=/home/runner/work/widgets/widgets/src/app/services/billing.py,line=3,col=1,endLine=3,endColumn=25::src/app/services/billing.py:3:1: F403 `from app.models import *` used; unable to detect undefined names\n'
    '::error title=ruff (E711),file=/home/runner/work/widgets/widgets/src/app/services/billing.py,line=13,col=20,endLine=13,endColumn=24::src/app/services/billing.py:13:20: E711 Comparison to `None` should be `cond is None`%0A  help: Replace with `cond is None`\n'
    '::error title=ruff (UP031),file=/home/runner/work/widgets/widgets/src/app/services/billing.py,line=18,col=12,endLine=18,endColumn=20::src/app/services/billing.py:18:12: UP031 Use format specifiers instead of percent format%0A  help: Replace with format specifiers\n'
    '::error title=ruff (E401),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=1,col=1,endLine=1,endColumn=17::src/app/services/export.py:1:1: E401 Multiple imports on one line%0A  help: Split imports\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=2,col=8,endLine=2,endColumn=15::src/app/services/export.py:2:8: F401 `pathlib` imported but unused%0A  help: Remove unused import: `pathlib`\n'
    '::error title=ruff (SIM115),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=5,col=9,endLine=5,endColumn=13::src/app/services/export.py:5:9: SIM115 Use a context manager for opening files\n'
    '::error title=ruff (E711),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=8,col=17,endLine=8,endColumn=21::src/app/services/export.py:8:17: E711 Comparison to `None` should be `cond is not None`%0A  help: Replace with `cond is not None`\n'
    '::error title=ruff (E741),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=13,col=5,endLine=13,endColumn=6::src/app/services/export.py:13:5: E741 Ambiguous variable name: `l`\n'
    '::error title=ruff (F841),file=/home/runner/work/widgets/widgets/src/app/services/export.py,line=13,col=5,endLine=13,endColumn=6::src/app/services/export.py:13:5: F841 Local variable `l` is assigned to but never used%0A  help: Remove assignment to unused variable `l`\n'
    '::error title=ruff (E401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=1,col=1,endLine=1,endColumn=15::src/app/utils.py:1:1: E401 Multiple imports on one line%0A  help: Split imports\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=1,col=8,endLine=1,endColumn=10::src/app/utils.py:1:8: F401 `os` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=1,col=12,endLine=1,endColumn=15::src/app/utils.py:1:12: F401 `sys` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=2,col=8,endLine=2,endColumn=19::src/app/utils.py:2:8: F401 `collections` imported but unused%0A  help: Remove unused import: `collections`\n'
    '::error title=ruff (UP035),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=3,col=1,endLine=3,endColumn=34::src/app/utils.py:3:1: UP035 `typing.List` is deprecated, use `list` instead\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=3,col=20,endLine=3,endColumn=28::src/app/utils.py:3:20: F401 `typing.Optional` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=3,col=30,endLine=3,endColumn=34::src/app/utils.py:3:30: F401 `typing.List` imported but unused%0A  help: Remove unused import\n'
    '::error title=ruff (E711),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=6,col=13,endLine=6,endColumn=17::src/app/utils.py:6:13: E711 Comparison to `None` should be `cond is None`%0A  help: Replace with `cond is None`\n'
    '::error title=ruff (SIM105),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=8,endLine=11::src/app/utils.py:8:5: SIM105 Use `contextlib.suppress(BaseException)` instead of `try`-`except`-`pass`%0A  help: Replace `try`-`except`-`pass` with `with contextlib.suppress(BaseException): ...`\n'
    '::error title=ruff (F841),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=9,col=9,endLine=9,endColumn=10::src/app/utils.py:9:9: F841 Local variable `x` is assigned to but never used%0A  help: Remove assignment to unused variable `x`\n'
    '::error title=ruff (E722),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=10,col=5,endLine=10,endColumn=11::src/app/utils.py:10:5: E722 Do not use bare `except`\n'
    '::error title=ruff (E741),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=12,col=5,endLine=12,endColumn=6::src/app/utils.py:12:5: E741 Ambiguous variable name: `l`\n'
    '::error title=ruff (F841),file=/home/runner/work/widgets/widgets/src/app/utils.py,line=16,col=5,endLine=16,endColumn=15::src/app/utils.py:16:5: F841 Local variable `unused_var` is assigned to but never used%0A  help: Remove assignment to unused variable `unused_var`\n'
    '::error title=ruff (F403),file=/home/runner/work/widgets/widgets/src/app/views.py,line=1,col=1,endLine=1,endColumn=25::src/app/views.py:1:1: F403 `from app.models import *` used; unable to detect undefined names\n'
    '::error title=ruff (F401),file=/home/runner/work/widgets/widgets/src/app/views.py,line=2,col=8,endLine=2,endColumn=10::src/app/views.py:2:8: F401 `re` imported but unused%0A  help: Remove unused import: `re`\n'
    '::error title=ruff (E711),file=/home/runner/work/widgets/widgets/src/app/views.py,line=8,col=15,endLine=8,endColumn=19::src/app/views.py:8:15: E711 Comparison to `None` should be `cond is None`%0A  help: Replace with `cond is None`\n'
)

# git merge --no-edit feature/search (ort strategy)
MERGE_OUT = (
    "Merge made by the 'ort' strategy.\n"
    ' assets/search-icon.png                             | Bin 0 -> 512 bytes\n'
    ' docs/old-guide.md                                  |  20 ----------\n'
    ' docs/usage.md                                      |   1 +\n'
    ' scripts/build.sh                                   |   0\n'
    ' scripts/deploy.sh                                  |   1 +\n'
    ' src/app/main.py                                    |   1 +\n'
    ' .../forms/LongComponentNameForRegistration.tsx     |  40 ++++++++++++++++++++\n'
    ' src/lib/{util.py => helpers.py}                    |   1 +\n'
    ' src/search/module_1.py                             |   7 ++++\n'
    ' src/search/module_2.py                             |  14 +++++++\n'
    ' src/search/module_3.py                             |  21 +++++++++++\n'
    ' src/search/module_4.py                             |  28 ++++++++++++++\n'
    ' src/search/module_5.py                             |  35 +++++++++++++++++\n'
    ' src/search/module_6.py                             |  42 +++++++++++++++++++++\n'
    ' tests/search/test_case_1.py                        |  15 ++++++++\n'
    ' tests/search/test_case_2.py                        |  15 ++++++++\n'
    ' tests/search/test_case_3.py                        |  15 ++++++++\n'
    ' 17 files changed, 236 insertions(+), 20 deletions(-)\n'
    ' create mode 100644 assets/search-icon.png\n'
    ' delete mode 100644 docs/old-guide.md\n'
    ' mode change 100644 => 100755 scripts/build.sh\n'
    ' create mode 100755 scripts/deploy.sh\n'
    ' create mode 100644 src/components/forms/LongComponentNameForRegistration.tsx\n'
    ' rename src/lib/{util.py => helpers.py} (98%)\n'
    ' create mode 100644 src/search/module_1.py\n'
    ' create mode 100644 src/search/module_2.py\n'
    ' create mode 100644 src/search/module_3.py\n'
    ' create mode 100644 src/search/module_4.py\n'
    ' create mode 100644 src/search/module_5.py\n'
    ' create mode 100644 src/search/module_6.py\n'
    ' create mode 100644 tests/search/test_case_1.py\n'
    ' create mode 100644 tests/search/test_case_2.py\n'
    ' create mode 100644 tests/search/test_case_3.py\n'
)

# git fetch --tags origin into an empty clone (stderr)
FETCH_FIRST = (
    'From ../up\n'
    ' * [new branch]      chore/deps-bump       -> origin/chore/deps-bump\n'
    ' * [new branch]      docs/api-guide        -> origin/docs/api-guide\n'
    ' * [new branch]      experiment/wasm-build -> origin/experiment/wasm-build\n'
    ' * [new branch]      feature/auth-refresh  -> origin/feature/auth-refresh\n'
    ' * [new branch]      feature/billing-v2    -> origin/feature/billing-v2\n'
    ' * [new branch]      fix/ci-cache          -> origin/fix/ci-cache\n'
    ' * [new branch]      fix/null-deref        -> origin/fix/null-deref\n'
    ' * [new branch]      hotfix/login-timeout  -> origin/hotfix/login-timeout\n'
    ' * [new branch]      main                  -> origin/main\n'
    ' * [new branch]      main-backup           -> origin/main-backup\n'
    ' * [new branch]      release/1.4           -> origin/release/1.4\n'
    ' * [new branch]      release/1.5           -> origin/release/1.5\n'
    ' * [new branch]      user/ben/spike-queue  -> origin/user/ben/spike-queue\n'
    ' * [new tag]         nightly-20260401      -> nightly-20260401\n'
    ' * [new tag]         nightly-20260402      -> nightly-20260402\n'
    ' * [new tag]         v1.0.0                -> v1.0.0\n'
    ' * [new tag]         v1.0.1                -> v1.0.1\n'
    ' * [new tag]         v1.1.0                -> v1.1.0\n'
    ' * [new tag]         v1.2.0                -> v1.2.0\n'
    ' * [new tag]         v1.2.1                -> v1.2.1\n'
    ' * [new tag]         v1.3.0                -> v1.3.0\n'
    ' * [new tag]         v1.3.1                -> v1.3.1\n'
    ' * [new tag]         v1.4.0                -> v1.4.0\n'
    ' * [new tag]         v1.4.0-rc1            -> v1.4.0-rc1\n'
    ' * [new tag]         v1.4.1                -> v1.4.1\n'
    ' * [new tag]         v1.5.0                -> v1.5.0\n'
)

# git fetch --tags --prune origin after upstream changes (stderr)
FETCH_UPDATE = (
    'From ../up\n'
    ' - [deleted]         (none)           -> origin/docs/api-guide\n'
    '   28618fc..106e579  fix/ci-cache     -> origin/fix/ci-cache\n'
    '   28618fc..8c32686  main             -> origin/main\n'
    ' + 28618fc...b27a814 release/1.4      -> origin/release/1.4  (forced update)\n'
    ' ! [rejected]        nightly-20260402 -> nightly-20260402  (would clobber existing tag)\n'
    ' * [new tag]         v1.6.0           -> v1.6.0\n'
)


# ===============================================================================================================
# #1427 git blame --line-porcelain: keep each commit's metadata block once, as `--porcelain` itself prints it.
# Equivalent (not a roundtrip): the result is a valid --porcelain document, so there is no sigil and a real --porcelain input
# cannot be told from an encoding; the independent oracle re-expands both sides to the --line-porcelain layout.

_BL_HDR = re.compile(r"[0-9a-f]{40} \d+ \d+(?: \d+)?")
_BL_MAXMETA = 40


def _bl_scan(lines):
    """Items: ('blk', header, meta lines, content line) or ('raw', line). A block is a header line, any number of metadata
    lines (not tab-led, not header-shaped), then a tab-led source line."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if _BL_HDR.fullmatch(lines[i]):
            j = i + 1
            while j < n and j - i <= _BL_MAXMETA and not lines[j].startswith("\t") and not _BL_HDR.fullmatch(lines[j]):
                j += 1
            if j < n and lines[j].startswith("\t"):
                out.append(("blk", lines[i], lines[i + 1:j], lines[j]))
                i = j + 1
                continue
        out.append(("raw", lines[i]))
        i += 1
    return out


def blame_header_once(text):
    items = _bl_scan(text.split("\n"))
    seen, out, folded = {}, [], False
    for it in items:
        if it[0] == "raw":
            out.append(it[1])
            continue
        _, head, meta, content = it
        if not meta:  # already the --porcelain layout: a bare header would be read as "same metadata as before"
            return text
        sha = head[:40]
        if sha not in seen:
            seen[sha] = meta
        elif seen[sha] == meta:
            out += [head, content]
            folded = True
            continue
        out += [head] + meta + [content]
    enc = "\n".join(out)
    return enc if folded and len(enc) < len(text) else text


def _expand_blame(text):
    """Oracle side: --porcelain (or mixed) layout -> --line-porcelain layout, by a regex over the whole text."""
    block = re.compile(r"^([0-9a-f]{40}) \d+ \d+(?: \d+)?\n((?:(?!\t|[0-9a-f]{40} \d+ \d+(?: \d+)?(?:\n|$))[^\n]*\n)*)(\t[^\n]*)",
                       re.M)
    first = {}

    def fill(m):
        sha, meta = m.group(1), m.group(2)
        if meta:
            first.setdefault(sha, meta)
        return m.group(0) if meta else m.group(0).replace("\n", "\n" + first.get(sha, ""), 1)

    return block.sub(fill, text)


def _oracle_1427(x, e):
    if _expand_blame(x) != _expand_blame(e):
        return False
    if x == BLAME_LP:  # ground truth: git's own --porcelain for the same range is exactly the encoding
        return e == BLAME_PORCELAIN
    return True


def _lp_block(sha, n, who, summary, meta_extra=""):
    return (f"{sha} {n} {n}\nauthor {who}\nauthor-mail <{who.split()[0].lower()}@example.com>\nauthor-time 1770730200\n"
            f"author-tz +0100\ncommitter {who}\ncommitter-mail <{who.split()[0].lower()}@example.com>\ncommitter-time 1770730200\n"
            f"committer-tz +0100\nsummary {summary}\n{meta_extra}filename a.py\n\tline {n}\n")


_SHA_A, _SHA_B = "a" * 40, "b" * 40
BLAME_EDGE = (_lp_block(_SHA_A, 1, "Ada Example", "first") + _lp_block(_SHA_A, 2, "Ada Example", "first")
              + _lp_block(_SHA_B, 3, "Ben Reviewer", "second") + _lp_block(_SHA_A, 4, "Ada Example", "first")
              + _lp_block(_SHA_A, 5, "Ada Example", "first (renamed author)").replace("author Ada Example", "author A. Example"))

case(
    1427,
    claim="In git blame --line-porcelain output, the metadata block of a commit is kept on its first line only and later lines of the same commit keep just the 'sha orig final' header and the tab-led source line (the layout --porcelain prints); a repeated sha whose block differs keeps its own.",
    loses="nothing: the repeated author, committer, summary, previous and filename lines are re-derived from the first block of the same sha (they are identical by construction)",
    encode=lambda t: guarded(t, blame_header_once),
    equivalent=_oracle_1427,
    fixtures=[
        Fixture("real: 18 lines from 3 commits", BLAME_LP, True),
        Fixture("repeated sha with a different block keeps its block", BLAME_EDGE, True),
        Fixture("real --porcelain is already compact", BLAME_PORCELAIN, False),
        Fixture("default blame is a different format", BLAME_DEFAULT, False),
        Fixture("every line a different commit", "".join(_lp_block(f"{i:040x}", i, "Ada Example", f"c{i}") for i in range(1, 5)), False),
        Fixture("header-shaped source text and CRLF", BLAME_LP.replace("\t    return", "\t" + "0" * 40 + " 1 1\r\n\t    return", 1), None),
    ],
    min_gain=0.25,
)

# ===============================================================================================================
# #1428 git reflog and git stash list: the N in ref@{N} is the line's position, and the ref is the same on every line of
# `git reflog` and `git stash list`; the header `§seq HEAD` names it once and the counters are dropped.

_SEQ_PRE = r"(?:[0-9a-f]{7,40} )?(?:\([^)]*\) )?"
_SEQ_LINE = re.compile(rf"({_SEQ_PRE})([^\s:@{{}}]+)@\{{(0|[1-9]\d*)\}}: (.*)")
_SEQ_PRE_REST = re.compile(rf"({_SEQ_PRE})(.*)")


def _seq_build(text):
    lines, nl = nl_split(text)
    if len(lines) < 3:
        return None
    ms = [_SEQ_LINE.fullmatch(l) for l in lines]
    if any(m is None for m in ms) or len({m[2] for m in ms}) != 1:
        return None  # not every line is a counted entry of one ref (reflog show --all interleaves refs and saves little)
    if any(int(m[3]) != n for n, m in enumerate(ms)):
        return None  # offset start (--skip), gaps or reordering: the position would not reproduce the counter
    return f"{SIG}seq {ms[0][2]}\n" + "\n".join(m[1] + m[4] for m in ms) + ("\n" if nl else "")


def seq_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(f"{SIG}seq "):
        return text
    head, _, rest = text.partition("\n")
    ref = head[len(f"{SIG}seq "):]
    lines, nl = nl_split(rest)
    out = []
    for n, l in enumerate(lines):
        m = _SEQ_PRE_REST.fullmatch(l)
        out.append(f"{m[1]}{ref}@{{{n}}}: {m[2]}")
    return "\n".join(out) + ("\n" if nl else "")


def seq_encode(text):
    return sigil_guard(text, _seq_build, seq_decode)


_REFLOG_STASH_MIX = "".join(f"{i:07x} stash@{{{i}}}: WIP on main: {i:07x} work {i}\n" for i in range(4))

case(
    1428,
    claim="In git reflog and git stash list output the ref name and the @{N} counters (which equal each line's position) are dropped from every line and named once in a '§seq REF' header; decode restores the exact text.",
    encode=seq_encode,
    decode=seq_decode,
    fixtures=[
        Fixture("real: git reflog, 20 entries", REFLOG_HEAD, True),
        Fixture("real: git reflog show main", REFLOG_MAIN, True),
        Fixture("real: git stash list", STASH_LIST, True),
        Fixture("real: reflog show --all interleaves refs, so it is left alone", REFLOG_ALL, False),
        Fixture("decorated first line and no trailing newline",
                "abc1234 (HEAD -> main, origin/main) HEAD@{0}: commit: x\nbcd2345 HEAD@{1}: commit: y\ncde3456 HEAD@{2}: commit: z", True),
        Fixture("--skip=3 starts at 3: counters are not positions", "abc1234 HEAD@{3}: a\nbcd2345 HEAD@{4}: b\ncde3456 HEAD@{5}: c\n", False),
        Fixture("a gap in the sequence", "abc1234 HEAD@{0}: a\nbcd2345 HEAD@{2}: b\ncde3456 HEAD@{3}: c\n", False),
        Fixture("--date=iso uses dates, not counters", "abc1234 HEAD@{2026-04-01 10:00:00 +0000}: commit: a\n" * 4, False),
        Fixture("a leading zero is not the printed counter", "abc1234 HEAD@{00}: a\nbcd2345 HEAD@{01}: b\ncde3456 HEAD@{02}: c\n", False),
        Fixture("input that starts like the escape line", RAW + REFLOG_MAIN, None),
        Fixture("input that starts like the header", f"{SIG}seq HEAD\nabc1234 commit: a\n", None),
        Fixture("a bare sigil", SIG + REFLOG_MAIN, None),
        Fixture("message text that looks like a decoration", "abc1234 HEAD@{0}: (x) y\nbcd2345 HEAD@{1}: (z) w\ncde3456 HEAD@{2}: (v) u\n", None),
    ],
    min_gain=0.15,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)

# ===============================================================================================================
# #1429 git status (long form) -> the short form's two-letter codes, with git's own `## branch...upstream [ahead N]` line.
# Equivalent: hint lines, section headers and the prose summary are dropped; every path keeps its index and worktree state.

_ST_HINT = re.compile(r"  \(.*\)")
_ST_SECTIONS = {"Changes to be committed:": "staged", "Changes not staged for commit:": "unstaged",
                "Untracked files:": "untracked", "Unmerged paths:": "unmerged", "Ignored files:": "ignored"}
_ST_STAGED = {"new file": "A", "modified": "M", "deleted": "D", "renamed": "R", "copied": "C", "typechange": "T"}
_ST_UNSTAGED = {"modified": "M", "deleted": "D", "typechange": "T"}
_ST_UNMERGED = {"both modified": "UU", "both added": "AA", "both deleted": "DD", "added by us": "AU", "added by them": "UA",
                "deleted by us": "DU", "deleted by them": "UD"}
_ST_ENTRY = re.compile(r"\t([a-z ]+):\s+(\S.*)")
_ST_SUMMARY = ('no changes added to commit (use "git add" and/or "git commit -a")',
               'nothing added to commit but untracked files present (use "git add" to track)',
               "nothing to commit, working tree clean", 'nothing to commit (create/copy files and use "git add" to track)')


_ST_UPSTREAM = [
    (re.compile(r"Your branch is up to date with '(.+)'\."), lambda m: (m[1], "")),
    (re.compile(r"Your branch is ahead of '(.+)' by (\d+) commits?\."), lambda m: (m[1], f" [ahead {m[2]}]")),
    (re.compile(r"Your branch is behind '(.+)' by (\d+) commits?, and can be fast-forwarded\."), lambda m: (m[1], f" [behind {m[2]}]")),
    (re.compile(r"Your branch is based on '(.+)', but the upstream is gone\."), lambda m: (m[1], " [gone]")),
]
_ST_DIVERGED = re.compile(r"Your branch and '(.+)' have diverged,")
_ST_COUNTS = re.compile(r"and have (\d+) and (\d+) different commits each, respectively\.")
_ST_PREAMBLE_ONLY = ("No commits yet", "Initial commit", "You have unmerged paths.")


def _sp_quote(path):
    """git's short form quotes a name that contains a space; the long form prints it bare."""
    return f'"{path}"' if " " in path and not path.startswith('"') else path


def status_short(text):
    lines, nl = nl_split(text)
    if not lines:
        return text
    m = re.fullmatch(r"On branch (.+)", lines[0])
    if m:
        branch, up, state, initial, seen_pre = m[1], None, "", False, set()
        i = 1
        while i < len(lines) and not lines[i].startswith("\t") and lines[i] not in _ST_SECTIONS and lines[i] not in _ST_SUMMARY:
            l, kind = lines[i], None
            for k, (rx, f) in enumerate(_ST_UPSTREAM):
                if (u := rx.fullmatch(l)):
                    kind, (up, state) = f"up{k}", f(u)
            if (u := _ST_DIVERGED.fullmatch(l)):
                v = _ST_COUNTS.fullmatch(lines[i + 1]) if i + 1 < len(lines) else None
                if not v:
                    return text
                kind, up, state, i = "div", u[1], f" [ahead {v[1]}, behind {v[2]}]", i + 1
            elif l in _ST_PREAMBLE_ONLY:
                kind, initial = l, initial or l != "You have unmerged paths."
            elif kind is None and not (l == "" or _ST_HINT.fullmatch(l)):
                return text  # rebase or bisect in progress, or something unexpected: not worth guessing
            if kind is not None:
                if kind in seen_pre:
                    return text
                seen_pre.add(kind)
            i += 1
        out_head = "## " + (f"No commits yet on {branch}" if initial else branch) + (f"...{up}" if up else "") + state
        structural = bool(up) or initial
    elif re.fullmatch(r"HEAD detached (?:at|from) \S+", lines[0]):
        out_head, i, structural = "## " + lines[0], 1, False
    else:
        return text
    entries, untracked, ignored, post, clean, section, seen_entry = {}, [], [], [], False, None, set()
    while i < len(lines):
        l = lines[i]
        i += 1
        if l in _ST_SECTIONS:
            if post:
                return text
            section, structural = _ST_SECTIONS[l], True
        elif l == "" or _ST_HINT.fullmatch(l):
            continue
        elif l.startswith("\t"):
            if post:
                return text
            if section in ("untracked", "ignored"):
                (untracked if section == "untracked" else ignored).append(_sp_quote(l[1:]))
                continue
            m = _ST_ENTRY.fullmatch(l)
            if not m or section is None:
                return text
            label, path = m[1], m[2]
            key = path.split(" -> ")[-1] if section == "staged" else path
            if (section, key) in seen_entry:
                return text  # git never lists a path twice in one section
            seen_entry.add((section, key))
            rec = entries.setdefault(key, {"X": " ", "Y": " ", "disp": key})
            if section == "staged" and label in _ST_STAGED:
                rec["X"], rec["disp"] = _ST_STAGED[label], " -> ".join(_sp_quote(p) for p in path.split(" -> "))
            elif section == "unstaged" and label in _ST_UNSTAGED:
                rec["Y"] = _ST_UNSTAGED[label]
            elif section == "unmerged" and label in _ST_UNMERGED:
                rec["X"], rec["Y"] = _ST_UNMERGED[label]
            else:
                return text
            if rec["disp"] == key:
                rec["disp"] = _sp_quote(key)
        elif l in _ST_SUMMARY:
            if post:
                return text
            clean, structural = clean or l.startswith("nothing to commit"), True
        else:
            if any(rx.fullmatch(l) for rx, _ in _ST_UPSTREAM) or _ST_DIVERGED.fullmatch(l) or _ST_COUNTS.fullmatch(l) or l in _ST_PREAMBLE_ONLY:
                return text  # a preamble line out of place
            post.append(l)
    if not structural:
        return text
    body = [f"{r['X']}{r['Y']} {r['disp']}" for _, r in sorted(entries.items())]
    body += [f"?? {p}" for p in untracked] + [f"!! {p}" for p in ignored]
    out = [out_head] + body + (["(clean)"] if clean and not body else []) + post
    return "\n".join(out) + ("\n" if nl else "")


def _unq(path):
    return path[1:-1] if len(path) > 1 and path[0] == path[-1] == '"' and "\\" not in path[1:-1] and '"' not in path[1:-1] else path


def _status_facts(text):
    """Independent oracle view: branch facts, per-path index/worktree states, untracked, ignored, leftover lines; works on both
    forms. Whole-text anchored regexes (not the encoder's line walk); quoting of names with spaces is normalised away."""
    f = {"branch": None, "up": None, "ahead": 0, "behind": 0, "gone": False, "initial": False, "detached": None,
         "paths": Counter(), "untracked": [], "ignored": [], "clean": False, "other": []}
    M = re.M
    if re.match(r"On branch |HEAD detached ", text):
        chunks = re.split(r"^(Changes to be committed:|Changes not staged for commit:|Untracked files:|Unmerged paths:|Ignored files:)\n",
                          text, flags=M)
        head, rest = chunks[0], chunks[1:]
        pats = [r"Your branch is up to date with '.+'\.", r"Your branch is ahead of '.+' by \d+ commits?\.",
                r"Your branch is behind '.+' by \d+ commits?, and can be fast-forwarded\.",
                r"Your branch is based on '.+', but the upstream is gone\.", r"Your branch and '.+' have diverged,",
                r"and have \d+ and \d+ different commits each, respectively\.", r"No commits yet", r"Initial commit",
                r"You have unmerged paths\.", r"  \(.*\)", r"nothing to commit, working tree clean",
                r'nothing to commit \(create/copy files and use "git add" to track\)',
                r'no changes added to commit \(use "git add" and/or "git commit -a"\)',
                r'nothing added to commit but untracked files present \(use "git add" to track\)', r""]
        known = re.compile("|".join(pats))
        hl = head.split("\n")
        f["branch"] = (re.match(r"On branch (.+)$", hl[0]) or [None, None])[1]
        f["detached"] = hl[0] if hl[0].startswith("HEAD detached") else None
        for l in hl[1:]:
            if not known.fullmatch(l):
                f["other"].append(l)
        f["initial"] = bool(re.search(r"^(No commits yet|Initial commit)$", head, M))
        if (m := re.search(r"^Your branch (?:is (?:up to date with|ahead of|behind|based on)|and) '(.+?)'", head, M)):
            f["up"] = m[1]
        if (m := re.search(r"^Your branch is ahead of '.*' by (\d+) commits?\.$", head, M)):
            f["ahead"] = int(m[1])
        if (m := re.search(r"^Your branch is behind '.*' by (\d+) commits?, and", head, M)):
            f["behind"] = int(m[1])
        if (m := re.search(r"^and have (\d+) and (\d+) different commits each, respectively\.$", head, M)):
            f["ahead"], f["behind"] = int(m[1]), int(m[2])
        f["gone"] = bool(re.search(r"^Your branch is based on '.*', but the upstream is gone\.$", head, M))
        for name, body in zip(rest[0::2], rest[1::2]):
            kind = _ST_SECTIONS[name]
            for l in body.split("\n"):
                if l.startswith("\t"):
                    if kind in ("untracked", "ignored"):
                        f[kind].append(_unq(_sp_quote(l[1:])))
                    else:
                        label, _, path = l[1:].partition(":")
                        path = path.strip()
                        key = path.split(" -> ")[-1]
                        disp = " -> ".join(_unq(_sp_quote(p)) for p in path.split(" -> "))
                        if kind == "staged":
                            f["paths"][(_unq(_sp_quote(key)), "index", _ST_STAGED[label], disp)] += 1
                        elif kind == "unstaged":
                            f["paths"][(_unq(_sp_quote(key)), "worktree", _ST_UNSTAGED[label], _unq(_sp_quote(key)))] += 1
                        else:
                            f["paths"][(_unq(_sp_quote(key)), "unmerged", _ST_UNMERGED[label], _unq(_sp_quote(key)))] += 1
                elif not known.fullmatch(l):
                    f["other"].append(l)
        f["clean"] = bool(re.search(r"^nothing to commit(?:, working tree clean| \(create/copy)", text, M))
        return f
    for l in text.split("\n"):
        if l == "":
            continue
        if (m := re.fullmatch(r"## (HEAD detached .*)", l)):
            f["detached"] = m[1]
        elif (m := re.fullmatch(r"## (?:(No commits yet) on )?(.+?)(?:\.\.\.(\S+))?(?: \[(.*)\])?", l)):
            f["initial"], f["branch"], f["up"] = bool(m[1]), m[2], m[3]
            for part in (m[4] or "").split(", "):
                if part.startswith("ahead "):
                    f["ahead"] = int(part[6:])
                elif part.startswith("behind "):
                    f["behind"] = int(part[7:])
                elif part == "gone":
                    f["gone"] = True
        elif l == "(clean)":
            f["clean"] = True
        elif re.fullmatch(r"[ MADRCTU?!]{2} .+", l):
            xy, path = l[:2], l[3:]
            key = _unq(path.split(" -> ")[-1])
            disp = " -> ".join(_unq(p) for p in path.split(" -> "))
            if xy == "??":
                f["untracked"].append(_unq(path))
            elif xy == "!!":
                f["ignored"].append(_unq(path))
            elif xy in _ST_UNMERGED.values():
                f["paths"][(key, "unmerged", xy, key)] += 1
            else:
                if xy[0] != " ":
                    f["paths"][(key, "index", xy[0], disp)] += 1
                if xy[1] != " ":
                    f["paths"][(key, "worktree", xy[1], key)] += 1
        else:
            f["other"].append(l)
    return f


def _oracle_1429(x, e):
    if x == e:
        return True
    if x in _STATUS_TRUTH and _status_facts(e) != _status_facts(_STATUS_TRUTH[x]):
        return False  # ground truth: git's own short form of the same state
    return _status_facts(x) == _status_facts(e)


_STATUS_TRUTH = {STATUS_LONG: STATUS_SB, ST_CONFLICT: ST_CONFLICT_SB}

case(
    1429,
    claim="Long-form git status becomes the short form's two-letter codes under git's own '## branch...upstream [ahead N]' line: staged and unstaged entries of one path merge into one XY line, renames keep 'old -> new', untracked and ignored paths get '??' and '!!', and a clean tree gets '(clean)'; unknown lines or labels make it leave the text alone.",
    loses="the hint lines and section headers, and the prose summary line (all derivable from the entries); order inside sections (tracked paths are sorted by path as in the short form, untracked and ignored keep their order)",
    encode=lambda t: guarded(t, status_short),
    equivalent=_oracle_1429,
    fixtures=[
        Fixture("real: staged, unstaged, MM, rename, delete, quoted and untracked, 2 ahead", STATUS_LONG, True),
        Fixture("real: conflicted merge, diverged", ST_CONFLICT, True),
        Fixture("real: untracked only", ST_UNTRACKED, True),
        Fixture("real: clean tree", ST_CLEAN, True),
        Fixture("real: behind upstream", ST_BEHIND, True),
        Fixture("real: diverged, clean", ST_DIVERGED, True),
        Fixture("real: before the first commit", ST_INITIAL, True),
        Fixture("real: detached HEAD with a modification", ST_DETACHED, True),
        Fixture("real: git stash pop with a trailing message", ST_STASHPOP, True),
        Fixture("git's own short form is left alone", STATUS_SB, False),
        Fixture("not a status", "On branch names are listed below\nfoo\n", False),
        Fixture("unknown label inside a section", STATUS_LONG.replace("modified:   src/app/config.py", "frobnicated: src/app/config.py"), False),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #1430 diff -rq: the second path of 'Files A/p and B/p differ' is the first with the root swapped, and every Only-in line
# repeats a root. The roots go in a '§drq A<TAB>B' header and each line keeps only its relative path.

_DQ_TYPES = ("regular file", "regular empty file", "directory", "symbolic link", "fifo", "socket", "block special file",
             "character special file")
_DQ_TY = "|".join(re.escape(t) for t in _DQ_TYPES)
_DQ_SIGILS = ("~ ", "~b ", "< ", "> ", "! ")


def _dq_roots(lines):
    for l in lines:
        m = re.fullmatch(r"(?:Files|Binary files) (.+) differ", l)
        if not m:
            continue
        rest = m[1]
        for sep in re.finditer(" and ", rest):
            cx, cy = rest[:sep.start()].split("/"), rest[sep.end():].split("/")
            k = 0
            while k < min(len(cx), len(cy)) - 1 and cx[-1 - k] == cy[-1 - k]:
                k += 1
            if k:
                a, b = "/".join(cx[:-k]), "/".join(cy[:-k])
                if a and b and a != b and "\t" not in a + b:
                    return a, b
        return None
    return None


def _dq_compact(l, a, b):
    """One diff -rq line -> its compact form, or None when the line does not follow the two roots."""
    if (m := re.fullmatch(r"(Files|Binary files) (.+) differ", l)):
        tag, rest = ("~" if m[1] == "Files" else "~b"), m[2]
        if rest.startswith(a + "/"):
            r2, sep = rest[len(a) + 1:], f" and {b}/"
            pos = r2.find(sep)
            while pos >= 0:
                if pos and r2[pos + len(sep):] == r2[:pos]:
                    return f"{tag} {r2[:pos]}"
                pos = r2.find(sep, pos + 1)
        return None
    if l.startswith("Only in "):
        rest = l[len("Only in "):]
        for sep in re.finditer(": ", rest):
            d, n = rest[:sep.start()], rest[sep.end():]
            if not n or "/" in n:
                continue
            for root, sig in ((a, "<"), (b, ">")):
                if d == root:
                    return f"{sig} {n}"
                if d.startswith(root + "/"):
                    return f"{sig} {d[len(root) + 1:]}/{n}" if d != root + "/" else f"{sig} /{n}"
        return None
    if (m := re.fullmatch(rf"File (.+) is a ({_DQ_TY}) while file (.+) is a ({_DQ_TY})", l)):
        if m[1].startswith(a + "/") and m[3] == b + "/" + m[1][len(a) + 1:]:
            return f"! {m[1][len(a) + 1:]}: {m[2]} -> {m[4]}"
    return None


def _drq_build(text):
    lines, nl = nl_split(text)
    roots = _dq_roots(lines)
    if not roots:
        return None
    a, b = roots
    body = []
    for l in lines:
        c = _dq_compact(l, a, b)
        if c is None and l.startswith(_DQ_SIGILS):
            return None  # a verbatim line that looks like a compact one
        body.append(c if c is not None else l)
    return f"{SIG}drq {a}\t{b}\n" + "\n".join(body) + ("\n" if nl else "")


def drq_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(f"{SIG}drq "):
        return text
    head, _, rest = text.partition("\n")
    a, _, b = head[len(f"{SIG}drq "):].partition("\t")
    lines, nl = nl_split(rest)
    out = []
    for l in lines:
        if l.startswith("~b "):
            out.append(f"Binary files {a}/{l[3:]} and {b}/{l[3:]} differ")
        elif l.startswith("~ "):
            out.append(f"Files {a}/{l[2:]} and {b}/{l[2:]} differ")
        elif l[:2] in ("< ", "> "):
            root = a if l[0] == "<" else b
            d, sep, n = l[2:].rpartition("/")
            out.append(f"Only in {root + ('/' + d if d else '/') if sep else root}: {n}")
        elif (m := re.fullmatch(rf"! (.+): ({_DQ_TY}) -> ({_DQ_TY})", l)):
            out.append(f"File {a}/{m[1]} is a {m[2]} while file {b}/{m[1]} is a {m[3]}")
        else:
            out.append(l)
    return "\n".join(out) + ("\n" if nl else "")


def drq_encode(text):
    return sigil_guard(text, _drq_build, drq_decode)


case(
    1430,
    claim="diff -rq lines are rewritten against two roots named once in a '§drq A<TAB>B' header: 'Files A/p and B/p differ' becomes '~ p', 'Only in A/d: n' becomes '< d/n' (B: '>'), 'File A/p is a X while file B/p is a Y' becomes '! p: X -> Y'; decode restores the exact text, and lines that do not follow the roots stay verbatim.",
    encode=drq_encode,
    decode=drq_decode,
    fixtures=[
        Fixture("real: diff -rq old new", DRQ_REL, True),
        Fixture("real: absolute roots", DRQ_ABS, True),
        Fixture("real: trailing-slash roots (Only in old/: x)", DRQ_SLASH, True),
        Fixture("names containing ' and ' and ': '", "Files a/Q and A.md and b/Q and A.md differ\nOnly in a/x: y: z\nOnly in b: n\nFiles a/k and b/k differ\n", True),
        Fixture("Binary files lines (diff -r without -q)", "Binary files out/a/x.bin and ref/a/x.bin differ\nBinary files out/y.bin and ref/y.bin differ\nOnly in ref: z\n", True),
        Fixture("real: normal diff -r is not -q output", DIFF_R_FULL, False),
        Fixture("no Files line: no way to name the roots", "Only in old: a\nOnly in new: b\n", False),
        Fixture("a verbatim line that looks compact", "Files old/a and new/a differ\n< old/x\nCommon subdirectories: old/s and new/s\n", False),
        Fixture("input that starts like the escape line", RAW + DRQ_REL, None),
        Fixture("input that starts like the header", f"{SIG}drq old\tnew\n~ x\n", None),
        Fixture("a bare sigil", SIG + DRQ_REL, None),
        Fixture("unrelated roots in the Only-in lines", "Files old/a and new/a differ\nOnly in elsewhere/dir: f\nOnly in old/sub: g\n", None),
    ],
    min_gain=0.25,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)

# ===============================================================================================================
# #1431 git merge, pull and diff --stat --summary: 'create mode', 'delete mode', 'rename' and 'mode change' lines restate paths
# that the stat table above already lists. Mark the table row instead and drop the summary line. Equivalent, not a roundtrip:
# a summary line whose path the table abbreviated (.../x.py) stays, so the line order can change (see `loses`).

_MS_ROW = re.compile(r" (?P<path>\S.*?) +\| +(?P<rest>\S.*)")
_MS_CHANGED = re.compile(r" \d+ files? changed(?:, \d+ insertions?\(\+\))?(?:, \d+ deletions?\(-\))?")
_MS_MARKED = re.compile(r"(?P<row> \S.*? +\| +\S.*?) \((?P<k>new|del|rename|chmod)(?P<a>[^()]*)\)")
_MS_SUMS = [
    (re.compile(r" create mode (\d+) (.+)"), lambda m: (f"new {m[1]}" if m[1] != "100644" else "new", m[2])),
    (re.compile(r" delete mode (\d+) (.+)"), lambda m: (f"del {m[1]}" if m[1] != "100644" else "del", m[2])),
    (re.compile(r" rename (.+) \((\d+)%\)"), lambda m: (f"rename {m[2]}%", m[1])),
    (re.compile(r" mode change (\d+) => (\d+) (.+)"), lambda m: (f"chmod {m[1]} => {m[2]}", m[3])),
]


def _ms_summary(line):
    for rx, f in _MS_SUMS:
        if (m := rx.fullmatch(line)):
            return f(m)
    return None


def _ms_blocks(lines):
    """(row start, changed-line index, summary end) for every 'N files changed' line, last block first."""
    out = []
    for k, l in enumerate(lines):
        if _MS_CHANGED.fullmatch(l):
            r0 = k
            while r0 > 0 and _MS_ROW.fullmatch(lines[r0 - 1]):
                r0 -= 1
            s1 = k + 1
            while s1 < len(lines) and _ms_summary(lines[s1]):
                s1 += 1
            out.append((r0, k, s1))
    return out[::-1]


def stat_fold(text):
    lines = text.split("\n")
    if any(_MS_MARKED.fullmatch(l) for l in lines):
        return text  # already carries marker-like rows: a reader could not tell which are ours
    changed = False
    for r0, k, s1 in _ms_blocks(lines):
        rows, sums = lines[r0:k], lines[k + 1:s1]
        if not rows or not sums:
            continue
        paths = [_MS_ROW.fullmatch(r)["path"] for r in rows]
        marks, left, last = {}, [], -1
        for s in sums:
            kind, path = _ms_summary(s)
            j = next((j for j in range(last + 1, len(rows)) if paths[j] == path), None)
            if j is None:
                left.append(s)  # the table abbreviated this path ('.../x.py') or listed it out of order: keep its own line
            else:
                marks[j], last = kind, j
        if marks:
            lines[r0:s1] = [f"{r} ({marks[j]})" if j in marks else r for j, r in enumerate(rows)] + [lines[k]] + left
            changed = True
    return "\n".join(lines) if changed else text


def _ms_facts(text):
    """Independent oracle view: (kind, arg, path) of every summary line and every marked row, plus the text without either."""
    facts, skeleton = Counter(), []
    for l in text.split("\n"):
        if (s := _ms_summary(l)):
            facts[(s[0].split(" ")[0], s[0].partition(" ")[2], s[1])] += 1
            continue
        m = _MS_MARKED.fullmatch(l)
        if m:
            arg = m["a"].strip()
            facts[(m["k"], arg if arg != "100644" else "", _MS_ROW.fullmatch(m["row"])["path"])] += 1
            skeleton.append(m["row"])
        else:
            skeleton.append(l)
    return facts, skeleton


def _oracle_1431(x, e):
    if x == e:
        return True
    return _ms_facts(x) == _ms_facts(e)


MS_NEW_ONLY = "".join(f" src/gen/file_{i}.py | {i + 3} {'+' * (i + 3)}\n" for i in range(6)) + " 6 files changed, 33 insertions(+)\n" + "".join(
    f" create mode 100644 src/gen/file_{i}.py\n" for i in range(6))
MS_COMMIT = "[main 1a2b3c4] Add generated files\n 6 files changed, 33 insertions(+)\n" + "".join(f" create mode 100644 src/gen/file_{i}.py\n" for i in range(6))

case(
    1431,
    claim="In a stat table followed by its --summary block (git merge, git pull, git diff --stat --summary), each 'create mode', 'delete mode', 'rename' and 'mode change' line whose path is a table row is folded into that row as a trailing (new), (del), (rename 98%) or (chmod a => b) marker and removed; summary lines that match no row (paths the table abbreviated as '.../x') keep their own line.",
    loses="the position of a summary line that keeps its own line (leftover lines come first, before the folded ones are implied by the table order); otherwise nothing, the lines are re-derived from the markers (mode 100644 is the default)",
    encode=lambda t: guarded(t, stat_fold),
    equivalent=_oracle_1431,
    fixtures=[
        Fixture("real: git merge with create, delete, chmod and rename", MERGE_OUT, True),
        Fixture("six new files", MS_NEW_ONLY, True),
        Fixture("two blocks (git log --stat --summary), one with a summary line no row matches",
                MS_NEW_ONLY + "\n" + " a.py | 1 +\n 1 file changed, 1 insertion(+)\n create mode 100644 other/long/path/a.py\n", True),
        Fixture("real-shaped git commit output has no table to fold into", MS_COMMIT, False),
        Fixture("table without summary lines", " a.py | 2 +-\n b.py | 1 +\n 2 files changed, 3 insertions(+), 1 deletion(-)\n", False),
        Fixture("text that already has marker-like rows", " a.py | 2 +- (new)\n 1 file changed, 2 insertions(+)\n create mode 100644 a.py\n", False),
        Fixture("two summary lines for one row: the second keeps its line", " a.py | 2 +-\n 1 file changed, 2 insertions(+)\n rename a.py (90%)\n mode change 100644 => 100755 a.py\n", None),
        Fixture("summary lines in another order than the rows: the late one keeps its line", " a.py | 1 +\n b.py | 1 +\n 2 files changed, 2 insertions(+)\n create mode 100644 b.py\n create mode 100644 a.py\n", None),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #1432 git fetch ref-update lines: the destination restates the source (-> origin/NAME, or NAME for tags) and both
# columns are padded to a common width. Header '§fetch W1 W2 REMOTE' carries the widths and the remote, rows keep flag,
# summary and source.

_FT_ROW = re.compile(r" ([ *+\-!t=]) (\[[^\]]+\]|[0-9a-f]+\.\.\.?[0-9a-f]+)( +)(\S+)( +)-> (\S+)((?:  \(.*\))?)")
_FT_COMPACT = re.compile(r"([ *+\-!t=]) (\[[^\]]+\]|[0-9a-f]+\.\.\.?[0-9a-f]+) (\S+)(?: (=)| -> (\S+))?((?:  \(.*\))?)")


def _fetch_build(text):
    lines, nl = nl_split(text)
    rows = [(_FT_ROW.fullmatch(l), l) for l in lines]
    first = next((m for m, _ in rows if m), None)
    if first is None or sum(1 for m, _ in rows if m) < 3:
        return None
    w1, w2 = len(first[2]) + len(first[3]) - 1, len(first[4]) + len(first[5]) - 1
    remote = next((m[6][:-len(m[4]) - 1] for m, _ in rows if m and m[6] != m[4] and m[6].endswith("/" + m[4])), "")
    body = []
    for m, l in rows:
        if not m:
            if _FT_COMPACT.fullmatch(l):
                return None
            body.append(l)
            continue
        flag, summ, src, dst, why = m[1], m[2], m[4], m[6], m[7]
        tail = "" if remote and dst == f"{remote}/{src}" else " =" if dst == src else f" -> {dst}"
        body.append(f"{flag} {summ} {src}{tail}{why}")
    return f"{SIG}fetch {w1} {w2} {remote}\n" + "\n".join(body) + ("\n" if nl else "")


def fetch_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(f"{SIG}fetch "):
        return text
    head, _, rest = text.partition("\n")
    w1, w2, remote = head[len(f"{SIG}fetch "):].split(" ", 2)
    w1, w2 = int(w1), int(w2)
    lines, nl = nl_split(rest)
    out = []
    for l in lines:
        m = _FT_COMPACT.fullmatch(l)
        if not m:
            out.append(l)
            continue
        flag, summ, src, same, dst, why = m.groups()
        dst = src if same else dst if dst else f"{remote}/{src}"
        out.append(f" {flag} {summ.ljust(w1)} {src.ljust(w2)} -> {dst}{why}")
    return "\n".join(out) + ("\n" if nl else "")


def fetch_encode(text):
    return sigil_guard(text, _fetch_build, fetch_decode)


case(
    1432,
    claim="git fetch ref-update rows lose the column padding and the destination that restates the source: '* [new tag]   v1.0   -> v1.0' keeps flag, summary and source (a destination other than REMOTE/source or source is kept after '->'), widths and the remote go in a '§fetch W1 W2 REMOTE' header, and decode restores the exact text.",
    encode=fetch_encode,
    decode=fetch_decode,
    fixtures=[
        Fixture("real: first fetch --tags, 13 branches and 13 tags", FETCH_FIRST, True),
        Fixture("real: update with prune, forced update, rejected tag", FETCH_UPDATE, True),
        Fixture("tags only: no remote prefix to hoist", " * [new tag]         v1.0.0 -> v1.0.0\n * [new tag]         v1.1.0 -> v1.1.0\n * [new tag]         v2.0.0 -> v2.0.0\n", True),
        Fixture("progress lines and a remote: banner stay verbatim",
                "remote: Enumerating objects: 5, done.\nFrom github.com:acme/widgets\n * [new branch]      a        -> origin/a\n * [new branch]      bb       -> origin/bb\n * [new branch]      ccc      -> origin/ccc\n", True),
        Fixture("fetch with nothing new", "From ../up\n", False),
        Fixture("two rows are below the threshold", " * [new tag]         v1 -> v1\n * [new tag]         v2 -> v2\n", False),
        Fixture("input that starts like the escape line", RAW + FETCH_FIRST, None),
        Fixture("input that starts like the header", f"{SIG}fetch 17 12 origin\n* [new tag] a\n", None),
        Fixture("a verbatim line shaped like a compact row", FETCH_FIRST + "* [new tag] v9\n", None),
        Fixture("a bare sigil", SIG + FETCH_UPDATE, None),
    ],
    min_gain=0.2,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)


# ===============================================================================================================
# JSON toolkit, lexeme level. Trees are ("o", [(key lexeme, node)]), ("a", [node]) or ("l", lexeme); number spelling, string
# escapes, duplicate keys and key order all survive. A document is re-emitted only when it is byte-identical to its own
# canonical layout (2-space pretty or compact, with or without a trailing newline); anything else is returned unchanged.

_JSTR = r'"(?:[^"\\]|\\.)*"'
_TOK = re.compile(rf'[ \t\r\n]*(?:({_JSTR})|([{{}}\[\]:,])|([^\s"{{}}\[\]:,]+))')
_LAYOUTS = [(True, "\n"), (True, ""), (False, "\n"), (False, "")]


def jtree(text):
    json.loads(text, parse_int=str, parse_float=str, parse_constant=str)  # strict validation, no int conversion limit
    toks = [m.group(1) or m.group(2) or m.group(3) for m in _TOK.finditer(text)]
    at = [0]

    def value():
        t = toks[at[0]]
        at[0] += 1
        if t == "{":
            pairs = []
            if toks[at[0]] == "}":
                at[0] += 1
                return ("o", pairs)
            while True:
                k = toks[at[0]]
                at[0] += 2
                pairs.append((k, value()))
                sep = toks[at[0]]
                at[0] += 1
                if sep == "}":
                    return ("o", pairs)
        if t == "[":
            items = []
            if toks[at[0]] == "]":
                at[0] += 1
                return ("a", items)
            while True:
                items.append(value())
                sep = toks[at[0]]
                at[0] += 1
                if sep == "]":
                    return ("a", items)
        return ("l", t)

    return value()


def jdump(n, pretty, ind=0):
    if n[0] == "l":
        return n[1]
    if not n[1]:
        return "[]" if n[0] == "a" else "{}"
    if not pretty:
        if n[0] == "a":
            return "[" + ",".join(jdump(c, False) for c in n[1]) + "]"
        return "{" + ",".join(k + ":" + jdump(v, False) for k, v in n[1]) + "}"
    pad, end = "  " * (ind + 1), "  " * ind
    if n[0] == "a":
        return "[\n" + ",\n".join(pad + jdump(c, True, ind + 1) for c in n[1]) + "\n" + end + "]"
    return "{\n" + ",\n".join(pad + k + ": " + jdump(v, True, ind + 1) for k, v in n[1]) + "\n" + end + "}"


def layout_of(tree, text):
    return next(((p, nl) for p, nl in _LAYOUTS if jdump(tree, p) + nl == text), None)


def jstr(lex):
    """Plain string lexeme -> Python str, else None."""
    if not lex or lex[0] != '"':
        return None
    try:
        return json.loads(lex)
    except ValueError:
        return None


def jlex(s):
    return json.dumps(s, ensure_ascii=False)


def walk_objects(n, fn):
    """Rebuild a tree bottom-up: fn(pairs) -> pairs (or None for 'unchanged') for every object, children first."""
    if n[0] == "a":
        return ("a", [walk_objects(c, fn) for c in n[1]])
    if n[0] == "o":
        pairs = [(k, walk_objects(v, fn)) for k, v in n[1]]
        return ("o", fn(pairs) or pairs)
    return n


def walk_arrays(n, fn):
    """Same for arrays: fn(items) -> items (or None), children first."""
    if n[0] == "a":
        items = [walk_arrays(c, fn) for c in n[1]]
        return ("a", fn(items) or items)
    if n[0] == "o":
        return ("o", [(k, walk_arrays(v, fn)) for k, v in n[1]])
    return n


def json_codec(header, transform, restore):
    """(encode, decode) for '§header\\n' + canonical JSON. transform/restore map trees to trees (restore returns None when the
    text carries nothing to restore). encode verifies by decoding and returns the input unchanged on any doubt."""
    hdr = f"{SIG}{header}\n"

    def decode(text):
        if text.startswith(RAW):
            return text[len(RAW):]
        if not text.startswith(hdr):
            return text
        body = text[len(hdr):]
        try:
            tree = jtree(body)
            lay = layout_of(tree, body)
            out = restore(tree)
            return jdump(out, *lay[:1]) + lay[1] if lay and out is not None else text
        except Exception:  # noqa: BLE001
            return text

    def encode(text):
        if text.startswith(SIG):
            return RAW + text
        try:
            tree = jtree(text)
            lay = layout_of(tree, text)
            if lay is None:
                return text
            new = transform(tree)
            if new is None:
                return text
            cand = hdr + jdump(new, lay[0]) + lay[1]
            return cand if len(cand) < len(text) and decode(cand) == text else text
        except Exception:  # noqa: BLE001  (not JSON, too deep, ...)
            return text

    return encode, decode


MARK = re.compile(r'"~(\d*)"')


def marker_keys_present(tree):
    found = []
    walk_objects(tree, lambda pairs: found.extend(k for k, _ in pairs if MARK.fullmatch(k)) or None)
    return bool(found)


# ===============================================================================================================
# #1424 GitHub REST entity objects. A simple-user object carries 13 URL fields that are fixed functions of login and id (plus
# node_id, the base64 of 'NN:User<id>'); a repository object carries about 40 URL fields that are fixed functions of
# full_name; a pull request or review comment carries a _links object that restates sibling fields; a zero-reaction summary is
# all zeros. Header '§ghrest', then the document with each fully matching run of fields replaced by one marker pair.

import base64

_API, _WEB = "https://api.github.com", "https://github.com"


def _msgpack_uint(n):
    if n < 128:
        return bytes([n])
    for fmt, size in ((0xCC, 1), (0xCD, 2), (0xCE, 4), (0xCF, 8)):
        if n < 1 << (8 * size):
            return bytes([fmt]) + n.to_bytes(size, "big")
    raise ValueError("id too large")


def _node_id_new(prefix, i):
    return prefix + base64.urlsafe_b64encode(b"\x92\x00" + _msgpack_uint(int(i))).decode().rstrip("=")


def _node_id_legacy(typ, i):
    return base64.b64encode(f"0{len(typ)}:{typ}{i}".encode()).decode()


def _user_entries(node_id_fn):
    u = lambda tail: (lambda p: f"{_API}/users/{p['L']}{tail}")  # noqa: E731
    return [
        ("node_id", node_id_fn),
        ("avatar_url", lambda p: f"https://avatars.githubusercontent.com/u/{p['I']}?v=4"),
        ("gravatar_id", lambda p: ""),
        ("url", u("")),
        ("html_url", lambda p: f"{_WEB}/{p['L']}"),
        ("followers_url", u("/followers")),
        ("following_url", u("/following{/other_user}")),
        ("gists_url", u("/gists{/gist_id}")),
        ("starred_url", u("/starred{/owner}{/repo}")),
        ("subscriptions_url", u("/subscriptions")),
        ("organizations_url", u("/orgs")),
        ("repos_url", u("/repos")),
        ("events_url", u("/events{/privacy}")),
        ("received_events_url", u("/received_events")),
    ]


def _repo_entries():
    r = lambda tail: (lambda p: f"{_API}/repos/{p['F']}{tail}")  # noqa: E731
    return [
        ("html_url", lambda p: f"{_WEB}/{p['F']}"), ("description", None), ("fork", None),
        ("url", r("")), ("forks_url", r("/forks")), ("keys_url", r("/keys{/key_id}")),
        ("collaborators_url", r("/collaborators{/collaborator}")), ("teams_url", r("/teams")), ("hooks_url", r("/hooks")),
        ("issue_events_url", r("/issues/events{/number}")), ("events_url", r("/events")), ("assignees_url", r("/assignees{/user}")),
        ("branches_url", r("/branches{/branch}")), ("tags_url", r("/tags")), ("blobs_url", r("/git/blobs{/sha}")),
        ("git_tags_url", r("/git/tags{/sha}")), ("git_refs_url", r("/git/refs{/sha}")), ("trees_url", r("/git/trees{/sha}")),
        ("statuses_url", r("/statuses/{sha}")), ("languages_url", r("/languages")), ("stargazers_url", r("/stargazers")),
        ("contributors_url", r("/contributors")), ("subscribers_url", r("/subscribers")), ("subscription_url", r("/subscription")),
        ("commits_url", r("/commits{/sha}")), ("git_commits_url", r("/git/commits{/sha}")), ("comments_url", r("/comments{/number}")),
        ("issue_comment_url", r("/issues/comments{/number}")), ("contents_url", r("/contents/{+path}")),
        ("compare_url", r("/compare/{base}...{head}")), ("merges_url", r("/merges")), ("archive_url", r("/{archive_format}{/ref}")),
        ("downloads_url", r("/downloads")), ("issues_url", r("/issues{/number}")), ("pulls_url", r("/pulls{/number}")),
        ("milestones_url", r("/milestones{/number}")), ("notifications_url", r("/notifications{?since,all,participating}")),
        ("labels_url", r("/labels{/name}")), ("releases_url", r("/releases{/id}")), ("deployments_url", r("/deployments")),
        ("created_at", None), ("updated_at", None), ("pushed_at", None),
        ("git_url", lambda p: f"git://github.com/{p['F']}.git"), ("ssh_url", lambda p: f"git@github.com:{p['F']}.git"),
        ("clone_url", lambda p: f"{_WEB}/{p['F']}.git"), ("svn_url", lambda p: f"{_WEB}/{p['F']}"),
    ]


_FAMILIES = {
    "u": _user_entries(lambda p: _node_id_legacy("User", p["I"])),
    "v": _user_entries(lambda p: _node_id_new("U_", p["I"])),
    "r": _repo_entries(),
}


def _params(fam, lookup):
    """Parameters of a family from the object's literal pairs (lookup(key) -> lexeme or None)."""
    if fam in "uv":
        login, uid = jstr(lookup("login") or ""), lookup("id") or ""
        if login and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", login) and re.fullmatch(r"[1-9]\d*", uid):
            return {"L": login, "I": uid}
        return None
    full = jstr(lookup("full_name") or "")
    return {"F": full} if full and re.fullmatch(r"[\w.-]+/[\w.-]+", full) else None


def lexeme(v):
    """Python value derived by a template -> the JSON lexeme it would be spelled as (str -> string, int -> number)."""
    return jlex(v) if isinstance(v, str) else str(v)


def _best_run(pairs, canon, params, min_n=3):
    """Longest run of consecutive pairs that equals consecutive canonical entries (key and derived value): (pair index, canon index, length)."""
    best = None
    for p in range(len(pairs)):
        for i, (ck, fn) in enumerate(canon):
            if fn is None or jstr(pairs[p][0]) != ck:
                continue
            n = 0
            while p + n < len(pairs) and i + n < len(canon):
                ck2, fn2 = canon[i + n]
                if fn2 is None or jstr(pairs[p + n][0]) != ck2 or pairs[p + n][1] != ("l", lexeme(fn2(params))):
                    break
                n += 1
            if n >= min_n and (best is None or n > best[2]):
                best = (p, i, n)
    return best


_LINK_MAP = {"self": "url", "html": "html_url", "issue": "issue_url", "comments": "comments_url", "review_comments": "review_comments_url",
             "review_comment": "review_comment_url", "commits": "commits_url", "statuses": "statuses_url", "pull_request": "pull_request_url"}
_REACTION_KEYS = ["url", "total_count", "+1", "-1", "laugh", "hooray", "confused", "heart", "rocket", "eyes"]


def _gh_transform(tree):
    if marker_keys_present(tree):
        return None

    def per_object(pairs):
        lit = lambda k: next((v[1] for kk, v in pairs if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
        out = pairs
        for group in ("uv", "r"):  # a user object is tried against both node_id schemes, the longer run wins
            for slot in ("", "2", "3"):
                lookup = lambda k: next((v[1] for kk, v in out if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
                cands = []
                for fam in group:
                    params = _params(fam, lookup)
                    run = _best_run(out, _FAMILIES[fam], params) if params else None
                    if run:
                        cands.append((run[2], fam, run))
                if not cands:
                    break
                _, fam, (p, i, n) = max(cands, key=lambda c: c[0])
                out = out[:p] + [(f'"~{slot}"', ("l", jlex(f"{fam}{i}-{i + n - 1}")))] + out[p + n:]
        res = []
        for k, v in out:
            if jstr(k) == "_links" and v[0] == "o" and v[1]:
                names = []
                for lk, lv in v[1]:
                    name = jstr(lk)
                    sib = lit(_LINK_MAP.get(name, "")) if name in _LINK_MAP else None
                    if not (lv[0] == "o" and len(lv[1]) == 1 and lv[1][0][0] == '"href"' and sib is not None and lv[1][0][1] == ("l", sib)):
                        names = None
                        break
                    names.append(("l", lk))
                if names:
                    v = ("a", names)
            elif jstr(k) == "reactions" and v[0] == "o" and [jstr(kk) for kk, _ in v[1]] == _REACTION_KEYS:
                url = lit("url")
                if url and v[1][0][1] == ("l", url[:-1] + '/reactions"') and all(vv == ("l", "0") for _, vv in v[1][1:]):
                    v = ("l", "0")
            res.append((k, v))
        return res

    return walk_objects(tree, per_object)


def _gh_restore(tree):
    changed = [False]

    def per_object(pairs):
        lit = lambda k: next((v[1] for kk, v in pairs if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
        res = []
        for k, v in pairs:
            m = MARK.fullmatch(k)
            if m and v[0] == "l" and (spec := re.fullmatch(r"([uvr])(\d+)-(\d+)", jstr(v[1]) or "")):
                fam, i, j = spec[1], int(spec[2]), int(spec[3])
                params = _params(fam, lit)
                canon = _FAMILIES[fam]
                for ck, fn in canon[i:j + 1]:
                    res.append((jlex(ck), ("l", lexeme(fn(params)))))
                changed[0] = True
            elif jstr(k) == "_links" and v[0] == "a" and all(c[0] == "l" for c in v[1]):
                res.append((k, ("o", [(c[1], ("o", [('"href"', ("l", lit(_LINK_MAP[jstr(c[1])])))])) for c in v[1]])))
                changed[0] = True
            elif jstr(k) == "reactions" and v == ("l", "0"):
                url = lit("url")
                res.append((k, ("o", [(jlex(name), ("l", url[:-1] + '/reactions"') if name == "url" else ("l", "0"))
                                      for name in _REACTION_KEYS])))
                changed[0] = True
            else:
                res.append((k, v))
        return res

    out = walk_objects(tree, per_object)
    return out if changed[0] else None


gh_encode, gh_decode = json_codec(
    "ghrest: \"~\" stands for omitted standard GitHub URL fields (derived from login+id or full_name); _links lists hrefs equal to sibling *_url; reactions 0 = all zero",
    _gh_transform, _gh_restore)


# --- fixtures for #1424: reconstructed from memory of the documented example responses (simple-user, repository, pull request,
# pull request review comment, issue comment); URL fields are written out by hand, separately from the encoder's tables, and none of
# it was captured from the live API

def _pretty(o):
    return json.dumps(o, indent=2, ensure_ascii=False) + "\n"


def _compact(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def _user(login, uid, node, typ="User", admin=False):
    a = f"https://api.github.com/users/{login}"
    return {"login": login, "id": uid, "node_id": node, "avatar_url": f"https://avatars.githubusercontent.com/u/{uid}?v=4",
            "gravatar_id": "", "url": a, "html_url": f"https://github.com/{login}", "followers_url": a + "/followers",
            "following_url": a + "/following{/other_user}", "gists_url": a + "/gists{/gist_id}",
            "starred_url": a + "/starred{/owner}{/repo}", "subscriptions_url": a + "/subscriptions",
            "organizations_url": a + "/orgs", "repos_url": a + "/repos", "events_url": a + "/events{/privacy}",
            "received_events_url": a + "/received_events", "type": typ, "user_view_type": "public", "site_admin": admin}


OCTOCAT = _user("octocat", 583231, "MDQ6VXNlcjU4MzIzMQ==")  # legacy node_id
ALICE = _user("alice-dev", 4418267, "MDQ6VXNlcjQ0MTgyNjc=")
BOB = _user("bob-reviewer", 98765432, "U_kgDOBeMKeA")  # newer account: msgpack-style node_id
CAROL = _user("carol-ops", 143207381, "U_kgDOCIkr1Q")
ACME = _user("acme-corp", 9919, "MDEyOk9yZ2FuaXphdGlvbjk5MTk=", typ="Organization")
BOT = {"login": "dependabot[bot]", "id": 49699333, "node_id": "MDM6Qm90NDk2OTkzMzM=", "avatar_url": "https://avatars.githubusercontent.com/in/29110?v=4",
       "gravatar_id": "", "url": "https://api.github.com/users/dependabot%5Bbot%5D", "html_url": "https://github.com/apps/dependabot",
       "followers_url": "https://api.github.com/users/dependabot%5Bbot%5D/followers", "type": "Bot", "site_admin": False}


def _repo(full, rid, owner, node):
    b = f"https://api.github.com/repos/{full}"
    return {"id": rid, "node_id": node, "name": full.split("/")[1], "full_name": full, "private": False, "owner": owner,
            "html_url": f"https://github.com/{full}", "description": "Widgets for the acme platform", "fork": full != "acme-corp/widgets",
            "url": b, "forks_url": b + "/forks", "keys_url": b + "/keys{/key_id}", "collaborators_url": b + "/collaborators{/collaborator}",
            "teams_url": b + "/teams", "hooks_url": b + "/hooks", "issue_events_url": b + "/issues/events{/number}",
            "events_url": b + "/events", "assignees_url": b + "/assignees{/user}", "branches_url": b + "/branches{/branch}",
            "tags_url": b + "/tags", "blobs_url": b + "/git/blobs{/sha}", "git_tags_url": b + "/git/tags{/sha}",
            "git_refs_url": b + "/git/refs{/sha}", "trees_url": b + "/git/trees{/sha}", "statuses_url": b + "/statuses/{sha}",
            "languages_url": b + "/languages", "stargazers_url": b + "/stargazers", "contributors_url": b + "/contributors",
            "subscribers_url": b + "/subscribers", "subscription_url": b + "/subscription", "commits_url": b + "/commits{/sha}",
            "git_commits_url": b + "/git/commits{/sha}", "comments_url": b + "/comments{/number}",
            "issue_comment_url": b + "/issues/comments{/number}", "contents_url": b + "/contents/{+path}",
            "compare_url": b + "/compare/{base}...{head}", "merges_url": b + "/merges", "archive_url": b + "/{archive_format}{/ref}",
            "downloads_url": b + "/downloads", "issues_url": b + "/issues{/number}", "pulls_url": b + "/pulls{/number}",
            "milestones_url": b + "/milestones{/number}", "notifications_url": b + "/notifications{?since,all,participating}",
            "labels_url": b + "/labels{/name}", "releases_url": b + "/releases{/id}", "deployments_url": b + "/deployments",
            "created_at": "2024-02-11T09:12:45Z", "updated_at": "2026-03-30T16:02:11Z", "pushed_at": "2026-03-30T16:01:58Z",
            "git_url": f"git://github.com/{full}.git", "ssh_url": f"git@github.com:{full}.git", "clone_url": f"https://github.com/{full}.git",
            "svn_url": f"https://github.com/{full}", "homepage": None, "size": 5120, "stargazers_count": 214, "watchers_count": 214,
            "language": "Python", "has_issues": True, "has_projects": True, "has_downloads": True, "has_wiki": False, "has_pages": False,
            "has_discussions": False, "forks_count": 31, "mirror_url": None, "archived": False, "disabled": False,
            "open_issues_count": 17, "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT", "url": "https://api.github.com/licenses/mit",
                                                 "node_id": "MDc6TGljZW5zZTEz"},
            "allow_forking": True, "is_template": False, "web_commit_signoff_required": False, "topics": [], "visibility": "public",
            "forks": 31, "open_issues": 17, "watchers": 214, "default_branch": "main"}


BASE_REPO = _repo("acme-corp/widgets", 650321, ACME, "R_kgDOAAnsUQ")
FORK_REPO = _repo("alice-dev/widgets", 712004455, ALICE, "R_kgDOKiD1Zw")
_Z = {"total_count": 0, "+1": 0, "-1": 0, "laugh": 0, "hooray": 0, "confused": 0, "heart": 0, "rocket": 0, "eyes": 0}


def _reactions(url, **nz):
    return {"url": url + "/reactions", **{**_Z, **nz}}


def _pull_request(n=1347):
    base = f"https://api.github.com/repos/acme-corp/widgets"
    html = f"https://github.com/acme-corp/widgets/pull/{n}"
    sha = "6dcb09b5b57875f334f61aebed695e2e4193db5e"
    links = {"self": {"href": f"{base}/pulls/{n}"}, "html": {"href": html}, "issue": {"href": f"{base}/issues/{n}"},
             "comments": {"href": f"{base}/issues/{n}/comments"}, "review_comments": {"href": f"{base}/pulls/{n}/comments"},
             "review_comment": {"href": base + "/pulls/comments{/number}"}, "commits": {"href": f"{base}/pulls/{n}/commits"},
             "statuses": {"href": f"{base}/statuses/{sha}"}}
    return {"url": f"{base}/pulls/{n}", "id": 1934771123, "node_id": "PR_kwDOAAnsUc5zWx2z", "html_url": html, "diff_url": html + ".diff",
            "patch_url": html + ".patch", "issue_url": f"{base}/issues/{n}", "number": n, "state": "open", "locked": False,
            "title": "Retry uploads with exponential backoff", "user": ALICE,
            "body": "Fixes #1301.\n\n- add `retry()` helper\n- wrap the S3 upload step\n", "created_at": "2026-03-28T10:14:07Z",
            "updated_at": "2026-03-30T16:01:58Z", "closed_at": None, "merged_at": None, "merge_commit_sha": "e5bd3914e2e596debea16f433f57875b5b90bcd6",
            "assignee": BOB, "assignees": [BOB], "requested_reviewers": [BOB, CAROL], "requested_teams": [], "labels": [], "milestone": None,
            "draft": False, "commits_url": f"{base}/pulls/{n}/commits", "review_comments_url": f"{base}/pulls/{n}/comments",
            "review_comment_url": base + "/pulls/comments{/number}", "comments_url": f"{base}/issues/{n}/comments",
            "statuses_url": f"{base}/statuses/{sha}",
            "head": {"label": "alice-dev:retry-uploads", "ref": "retry-uploads", "sha": sha, "user": ALICE, "repo": FORK_REPO},
            "base": {"label": "acme-corp:main", "ref": "main", "sha": "9a4f1c2d5e0b7a3c8d6e1f2a3b4c5d6e7f8a9b0c", "user": ACME, "repo": BASE_REPO},
            "_links": links, "author_association": "CONTRIBUTOR", "auto_merge": None, "active_lock_reason": None, "merged": False,
            "mergeable": True, "rebaseable": True, "mergeable_state": "clean", "merged_by": None, "comments": 2, "review_comments": 3,
            "maintainer_can_modify": True, "commits": 2, "additions": 87, "deletions": 12, "changed_files": 4}


def _review_comments():
    base, n = "https://api.github.com/repos/acme-corp/widgets", 1347
    out = []
    for i, (who, path, line, body) in enumerate([(BOB, "src/upload.py", 41, "Should this catch `ClientError` only?"),
                                                 (ALICE, "src/upload.py", 41, "Good point, narrowed in 3f2a1c9."),
                                                 (CAROL, "tests/test_upload.py", 12, "Nit: use `pytest.raises`.")]):
        cid = 1812000100 + i
        out.append({"url": f"{base}/pulls/comments/{cid}", "pull_request_review_id": 2012345678, "id": cid,
                    "node_id": f"PRRC_kwDOAAnsUc5{cid}", "diff_hunk": "@@ -38,6 +38,9 @@ def upload(path):\n     try:\n         put(path)\n+    except Exception:\n+        retry(path)\n",
                    "path": path, "commit_id": "6dcb09b5b57875f334f61aebed695e2e4193db5e", "original_commit_id": "6dcb09b5b57875f334f61aebed695e2e4193db5e",
                    "user": who, "body": body, "created_at": f"2026-03-29T1{i}:20:00Z", "updated_at": f"2026-03-29T1{i}:20:00Z",
                    "html_url": f"https://github.com/acme-corp/widgets/pull/{n}#discussion_r{cid}", "pull_request_url": f"{base}/pulls/{n}",
                    "author_association": "MEMBER",
                    "_links": {"self": {"href": f"{base}/pulls/comments/{cid}"},
                               "html": {"href": f"https://github.com/acme-corp/widgets/pull/{n}#discussion_r{cid}"},
                               "pull_request": {"href": f"{base}/pulls/{n}"}},
                    "reactions": _reactions(f"{base}/pulls/comments/{cid}", **({"total_count": 1, "+1": 1} if i == 0 else {})),
                    "start_line": None, "original_start_line": None, "start_side": None, "line": line, "original_line": line, "side": "RIGHT",
                    "original_position": 5, "position": 5, "subject_type": "line"})
    return out


def _issue_comments():
    base = "https://api.github.com/repos/acme-corp/widgets"
    return [{"url": f"{base}/issues/comments/{1700000000 + i}", "html_url": f"https://github.com/acme-corp/widgets/issues/1301#issuecomment-{1700000000 + i}",
             "issue_url": f"{base}/issues/1301", "id": 1700000000 + i, "node_id": f"IC_kwDOAAnsUc5{i}Abc", "user": u,
             "created_at": f"2026-03-2{i}T08:00:00Z", "updated_at": f"2026-03-2{i}T08:00:00Z", "author_association": "NONE", "body": b,
             "reactions": _reactions(f"{base}/issues/comments/{1700000000 + i}"), "performed_via_github_app": None}
            for i, (u, b) in enumerate([(ALICE, "Reproduced on 2.3.1, traceback below."), (BOB, "Thanks, taking a look."), (CAROL, "+1 here"),
                                        (BOT, "Bumps boto3 from 1.34.0 to 1.34.9.")])]


GH_PR_DOC = _pretty(_pull_request())
GH_REVIEW_DOC = _pretty(_review_comments())
GH_ISSUE_DOC = _compact(_issue_comments())
GH_MCP_MINIMAL = _pretty([{"login": "alice-dev", "id": 4418267, "avatar_url": "https://avatars.githubusercontent.com/u/4418267?v=4",
                           "html_url": "https://github.com/alice-dev"}] * 3)

case(
    1424,
    claim="In a GitHub REST document, a user object whose 13 URL fields (and legacy or msgpack-style node_id) are the fixed functions of login and id, a repository object whose ~40 URL templates are functions of full_name, a _links object that restates sibling url fields, and an all-zero reactions summary are each replaced by one short marker; a '§ghrest' header marks the encoding and decode restores the exact bytes.",
    encode=gh_encode,
    decode=gh_decode,
    fixtures=[
        Fixture("pulls/N payload: fork head.repo, base.repo, 9 user objects, _links", GH_PR_DOC, True),
        Fixture("pulls/N/comments: users, _links, reactions", GH_REVIEW_DOC, True),
        Fixture("issues/N/comments, compact layout, one bot author", GH_ISSUE_DOC, True),
        Fixture("MCP-style minimal user objects have no templates", GH_MCP_MINIMAL, False),
        Fixture("4-space indent is not a layout we can reproduce", json.dumps(_pull_request(), indent=4) + "\n", False),
        Fixture("not JSON", "HTTP/2 200\ncontent-type: application/json\n", False),
        Fixture("a user whose avatar_url is custom keeps that field",
                _pretty([{**ALICE, "avatar_url": "https://example.com/a.png"}]), True),
        Fixture("input that starts like the escape line", RAW + GH_REVIEW_DOC, None),
        Fixture("input that starts like the header", "§ghrest\n" + GH_REVIEW_DOC, None),
        Fixture("a literal marker key in the input", _pretty({**ALICE, "~": "u0-13"}), None),
        Fixture("_links that is already an array and reactions that is already 0",
                _pretty({"url": "https://api.github.com/x", "_links": ["self"], "reactions": 0, "user": ALICE}), None),
        Fixture("duplicate keys and number spellings survive", '{\n  "a": 1E5,\n  "a": 2,\n  "user": ' + json.dumps(OCTOCAT, indent=2).replace("\n", "\n  ") + '\n}\n', None),
    ],
    min_gain=0.3,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)


# ===============================================================================================================
# #1425 GitHub REST file entries (pulls/N/files, commits/SHA, compare): blob_url, raw_url and contents_url all embed the same
# owner/repo, head sha and URL-encoded filename; changes is additions + deletions. The first entry of an array keeps its
# blob_url as the reference, later entries drop all three URLs; every entry drops changes.

import urllib.parse

_FILE_P = re.compile(r"https://github\.com/([^/]+)/([^/]+)/blob/([0-9a-f]{40})/")
_qp = lambda name: urllib.parse.quote(name, safe="/")  # noqa: E731
_raw = lambda p: p["P"].replace("/blob/", "/raw/", 1) + p["q"]  # noqa: E731


def _contents(p):
    m = _FILE_P.fullmatch(p["P"])
    return f"https://api.github.com/repos/{m[1]}/{m[2]}/contents/{p['q']}?ref={m[3]}"


_CHANGES = ("changes", lambda p: int(p["a"]) + int(p["d"]))
_FILE_FAMS = {
    "f": [_CHANGES, ("blob_url", lambda p: p["P"] + p["q"]), ("raw_url", _raw), ("contents_url", _contents)],  # prefix from an earlier entry
    "g": [("raw_url", _raw), ("contents_url", _contents)],  # prefix from the entry's own blob_url
    "c": [_CHANGES],
}
_FILE_MIN = {"f": 3, "g": 2, "c": 1}


def _file_params(lit, prefix):
    name = jstr(lit("filename") or "")
    a, d = lit("additions"), lit("deletions")
    if name is None or not (a and d and a.isdigit() and d.isdigit()):
        return None
    return {"q": _qp(name), "a": a, "d": d, "P": prefix}


def _files_transform(tree):
    if marker_keys_present(tree):
        return None

    def per_array(items):
        ref, out = None, []
        for it in items:
            if it[0] != "o":
                out.append(it)
                continue
            pairs = it[1]
            lit = lambda k, pairs=pairs: next((v[1] for kk, v in pairs if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
            base = _file_params(lit, None)
            if base is None:
                out.append(it)
                continue
            own = None
            blob = jstr(lit("blob_url") or "")
            if blob and blob.endswith(base["q"]) and _FILE_P.fullmatch(blob[:-len(base["q"])]):
                own = blob[:-len(base["q"])]
            cur = pairs
            for slot, (fam, prefix) in zip(("", "2", "3"), (("f", ref), ("g", own), ("c", None))):
                if fam in "fg" and prefix is None:
                    continue
                params = {**base, "P": prefix}
                if fam == "g" and not any(jstr(k) == "blob_url" for k, _ in cur):
                    continue
                run = _best_run(cur, _FILE_FAMS[fam], params, _FILE_MIN[fam])
                if run:
                    p, i, n = run
                    cur = cur[:p] + [(f'"~{slot}"', ("l", jlex(f"{fam}{i}-{i + n - 1}")))] + cur[p + n:]
            out.append(("o", cur))
            ref = own or ref
        return out

    return walk_arrays(tree, per_array)


def _files_restore(tree):
    changed = [False]

    def per_array(items):
        ref, out = None, []
        for it in items:
            if it[0] != "o":
                out.append(it)
                continue
            lit = lambda k, pairs=it[1]: next((v[1] for kk, v in pairs if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
            own_blob = jstr(lit("blob_url") or "")
            res = []
            for k, v in it[1]:
                spec = re.fullmatch(r"([fgc])(\d+)-(\d+)", jstr(v[1]) or "") if MARK.fullmatch(k) and v[0] == "l" else None
                if not spec:
                    res.append((k, v))
                    continue
                fam, i, j = spec[1], int(spec[2]), int(spec[3])
                base = _file_params(lit, None)
                q = base["q"]
                prefix = ref if fam == "f" else own_blob[:-len(q)] if fam == "g" and own_blob else None
                params = {**base, "P": prefix}
                res += [(jlex(ck), ("l", lexeme(fn(params)))) for ck, fn in _FILE_FAMS[fam][i:j + 1]]
                changed[0] = True
            item = ("o", res)
            out.append(item)
            lit2 = lambda k, pairs=res: next((v[1] for kk, v in pairs if jstr(kk) == k and v[0] == "l"), None)  # noqa: E731
            q = _qp(jstr(lit2("filename") or "") or "\0")
            blob = jstr(lit2("blob_url") or "")
            if blob and blob.endswith(q) and _FILE_P.fullmatch(blob[:-len(q)]):
                ref = blob[:-len(q)]
        return out

    out = walk_arrays(tree, per_array)
    return out if changed[0] else None


files_encode, files_decode = json_codec(
    "ghfiles: \"~\" stands for omitted changes (= additions + deletions) and blob_url/raw_url/contents_url derived from filename and the first entry's blob_url",
    _files_transform, _files_restore)


# reconstructed from memory: entries shaped like the documented pulls/N/files response (sha, filename, status, additions, deletions,
# changes, blob_url, raw_url, contents_url, patch, previous_filename), URLs written out by hand, not captured from the live API
def _file_entry(name, status, a, d, patch=None, prev=None, sha="6dcb09b5b57875f334f61aebed695e2e4193db5e", repo="acme-corp/widgets"):
    q = name.replace(" ", "%20")
    e = {"sha": "bbcd538c8e72b8c175046e27cc8f907076331401"[: 40 - len(name) % 7] + "0" * (len(name) % 7), "filename": name, "status": status,
         "additions": a, "deletions": d, "changes": a + d, "blob_url": f"https://github.com/{repo}/blob/{sha}/{q}",
         "raw_url": f"https://github.com/{repo}/raw/{sha}/{q}", "contents_url": f"https://api.github.com/repos/{repo}/contents/{q}?ref={sha}"}
    if patch:
        e["patch"] = patch
    if prev:
        e["previous_filename"] = prev
    return e


_PATCH = "@@ -38,6 +38,9 @@ def upload(path):\n     try:\n         put(path)\n+    except Exception:\n+        retry(path)\n     finally:\n         close()\n"
FILES = [
    _file_entry("src/upload.py", "modified", 3, 0, _PATCH),
    _file_entry("src/retry.py", "added", 42, 0, "@@ -0,0 +1,42 @@\n+import time\n+\n+def retry(fn, tries=5):\n+    for i in range(tries):\n+        try:\n+            return fn()\n+        except Exception:\n+            time.sleep(2 ** i)\n"),
    _file_entry("tests/test_upload.py", "modified", 12, 2, "@@ -1,4 +1,14 @@\n import pytest\n+from src.retry import retry\n"),
    _file_entry("docs/read me.md", "modified", 1, 1, "@@ -1 +1 @@\n-Uploads\n+Uploads (with retries)\n"),
    _file_entry("src/old_name.py", "renamed", 0, 0, prev="src/legacy.py"),
    _file_entry("assets/diagram.png", "added", 0, 0),
    _file_entry("CHANGELOG.md", "modified", 2, 0, "@@ -1,3 +1,5 @@\n # Changelog\n+\n+- retry uploads\n"),
]
GH_FILES_DOC = _pretty(FILES)

case(
    1425,
    claim="In an array of GitHub file entries, the first entry keeps blob_url as the reference and every later entry drops blob_url, raw_url and contents_url (derived from the filename and the reference's owner, repo and head sha); every entry drops changes (additions plus deletions); a '§ghfiles' header marks the encoding and decode restores the exact bytes.",
    encode=files_encode,
    decode=files_decode,
    fixtures=[
        Fixture("pulls/N/files: 7 entries, a space in one name, a rename, a binary", GH_FILES_DOC, True),
        Fixture("compact layout, no patches", _compact([_file_entry(f"pkg/mod{i}/file_{i}.py", "modified", i, i) for i in range(1, 9)]), True),
        Fixture("entries from two commits: a new reference mid-array",
                _pretty([_file_entry("a.py", "added", 1, 0), _file_entry("b.py", "added", 1, 0, sha="1" * 40), _file_entry("c.py", "added", 1, 0, sha="1" * 40)]), True),
        Fixture("a url that does not follow the filename keeps its literal",
                _pretty([_file_entry("a.py", "added", 1, 0), {**_file_entry("b.py", "added", 1, 0), "raw_url": "https://example.com/other"}, _file_entry("c.py", "added", 1, 0)]), True),
        Fixture("a single entry has no reference to hoist", _pretty([{k: v for k, v in _file_entry("a.py", "added", 1, 0).items() if k not in ("raw_url", "contents_url", "changes")}]), False),
        Fixture("changes that is not additions + deletions is kept",
                _pretty([{**_file_entry("a.py", "added", 1, 0), "changes": 5}, {**_file_entry("b.py", "added", 1, 0), "changes": 7}]), True),
        Fixture("3-space indent is not a layout we reproduce", json.dumps(FILES, indent=3) + "\n", False),
        Fixture("input that starts like the escape line", RAW + GH_FILES_DOC, None),
        Fixture("input that starts like the header", f"{SIG}ghfiles: x\n" + GH_FILES_DOC, None),
        Fixture("a literal marker key in the input", _pretty([{**_file_entry("a.py", "added", 1, 0), "~": "f0-3"}]), None),
    ],
    min_gain=0.25,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)


# ===============================================================================================================
# #1426 GitLab API v4 commit objects restate their own fields: short_id is a prefix of id, title is the first line of message (or
# message is title), created_at and committer_* repeat committed_date and author_*, committed_date repeats authored_date, web_url
# is the first entry's prefix plus the id. Each twin value becomes a '=source' string; a '§gitlab' header marks the encoding.
# (Merge request objects and MR change entries have twins too, but saved 2-6% of tokens on the fixtures: not worth a rule.)

_GL_MARK = re.compile(r'"=(?:id\[:\d+\]|authored_date|committed_date|author_name|author_email|title|title\+LF|message\[0\]|trailers|web_url\[0\])"')


def _lits(pairs):
    """key -> lexeme of the literal (string, number, bool, null) pairs of one object."""
    return {jstr(k) or k: v[1] for k, v in pairs if v[0] == "l"}


def _gl_markers(pairs, first_web):
    """Encoder: key -> marker string for every field of this commit object that is a twin of another field."""
    L = _lits(pairs)
    S = lambda k: jstr(L.get(k) or "")  # noqa: E731
    marks, ident = {}, S("id")
    short = S("short_id")
    if ident and short and 7 <= len(short) < len(ident) and ident.startswith(short):
        marks["short_id"] = f"=id[:{len(short)}]"
    if L.get("authored_date") and L.get("committed_date") == L["authored_date"]:
        marks["committed_date"] = "=authored_date"
    if L.get("created_at") and L.get("created_at") in (L.get("authored_date"), L.get("committed_date")):
        marks["created_at"] = "=authored_date" if L["created_at"] == L.get("authored_date") else "=committed_date"
    for who, src in (("committer_name", "author_name"), ("committer_email", "author_email")):
        if L.get(src) and L.get(who) == L[src]:
            marks[who] = f"={src}"
    title, message = S("title"), S("message")
    if title and message is not None:
        if message == title:
            marks["message"] = "=title"
        elif message == title + "\n":
            marks["message"] = "=title+LF"
        elif "\n" in message and message.split("\n", 1)[0] == title:
            marks["title"] = "=message[0]"
    web = S("web_url")
    if ident and web and first_web and web == first_web + ident:
        marks["web_url"] = "=web_url[0]"
    if ("trailers", ("o", [])) in [(jstr(k), v) for k, v in pairs] and ("extended_trailers", ("o", [])) in [(jstr(k), v) for k, v in pairs]:
        marks["extended_trailers"] = "=trailers"
    return marks


def _gl_resolve(pairs, first_web):
    """Decoder: replace every marker value by the twin it names, in dependency order."""
    pairs = list(pairs)
    todo = [(n, jstr(k)) for n, (k, v) in enumerate(pairs) if v[0] == "l" and _GL_MARK.fullmatch(v[1])]
    while todo:
        L = {k: v for k, v in _lits(pairs).items() if not _GL_MARK.fullmatch(v)}
        S = lambda k: jstr(L.get(k) or "")  # noqa: E731
        progress, rest = False, []
        for n, key in todo:
            mk = jstr(pairs[n][1][1])
            node = None
            if key == "short_id" and S("id") and (m := re.fullmatch(r"=id\[:(\d+)\]", mk)):
                node = ("l", jlex(S("id")[:int(m[1])]))
            elif key in ("committed_date", "created_at") and mk in ("=authored_date", "=committed_date") and L.get(mk[1:]):
                node = ("l", L[mk[1:]])
            elif key in ("committer_name", "committer_email") and mk in ("=author_name", "=author_email") and L.get(mk[1:]):
                node = ("l", L[mk[1:]])
            elif key == "message" and S("title") is not None and mk in ("=title", "=title+LF"):
                node = ("l", jlex(S("title") + ("\n" if mk == "=title+LF" else "")))
            elif key == "title" and mk == "=message[0]" and S("message") is not None:
                node = ("l", jlex(S("message").split("\n", 1)[0]))
            elif key == "web_url" and mk == "=web_url[0]" and S("id") and first_web:
                node = ("l", jlex(first_web + S("id")))
            elif key == "extended_trailers" and mk == "=trailers":
                node = ("o", [])
            if node is None:
                rest.append((n, key))
            else:
                pairs[n] = (pairs[n][0], node)
                progress = True
        if not progress:
            raise ValueError("unresolvable marker")
        todo = rest
    return pairs


def _gl_walk(n, fn, first_web=None):
    """Rebuild a tree children first; fn(pairs, prefix of the enclosing array) -> pairs for every object. Array items share a
    prefix found on the first item whose web_url is a prefix plus its id (read after fn ran, so the decoder sees literals)."""
    if n[0] == "a":
        prefix, items = None, []
        for it in n[1]:
            new = _gl_walk(it, fn, prefix)
            items.append(new)
            if prefix is None and new[0] == "o":
                L = _lits(new[1])
                web, ident = jstr(L.get("web_url") or ""), jstr(L.get("id") or "")
                if web and ident and web.endswith(ident) and not _GL_MARK.fullmatch(L["web_url"]):
                    prefix = web[:-len(ident)]
        return ("a", items)
    if n[0] == "o":
        return ("o", fn([(k, _gl_walk(v, fn)) for k, v in n[1]], first_web))
    return n


def _gl_unsafe(n):
    """True for a literal that looks like a marker, or an object with duplicate keys."""
    if n[0] == "l":
        return bool(_GL_MARK.fullmatch(n[1]))
    if n[0] == "a":
        return any(_gl_unsafe(c) for c in n[1])
    keys = [k for k, _ in n[1]]
    return len(set(keys)) != len(keys) or any(_gl_unsafe(v) for _, v in n[1])


def _gl_transform(tree):
    if _gl_unsafe(tree):
        return None

    def per_object(pairs, first_web):
        marks = _gl_markers(pairs, first_web)
        return [(k, ("l", jlex(marks[jstr(k)])) if jstr(k) in marks else v) for k, v in pairs]

    return _gl_walk(tree, per_object)


def _gl_restore(tree):
    changed = [False]

    def per_object(pairs, first_web):
        if any(v[0] == "l" and _GL_MARK.fullmatch(v[1]) for _, v in pairs):
            changed[0] = True
            return _gl_resolve(pairs, first_web)
        return pairs

    out = _gl_walk(tree, per_object)
    return out if changed[0] else None


gitlab_encode, gitlab_decode = json_codec("gitlab", _gl_transform, _gl_restore)


# reconstructed from memory: commit objects shaped like the documented GET /projects/:id/repository/commits response (id, short_id,
# created_at, parent_ids, title, message, author_*, authored_date, committer_*, committed_date, trailers, extended_trailers, web_url)
def _gl_commit(i, author, email, title, body="", committer=None, prefix="https://gitlab.com/acme/widgets/-/commit/"):
    sha = f"{(0x9fceb02d0ae598e95dc970b74767f19372d61af8 + i * 0x1234567) % (1 << 160):040x}"
    date = f"2026-03-{10 + i:02d}T{9 + i:02d}:15:00.000+01:00"
    cdate = date if committer is None else f"2026-03-{10 + i:02d}T{9 + i:02d}:55:00.000+00:00"
    msg = title + "\n" + (("\n" + body) if body else "")
    return {"id": sha, "short_id": sha[:8], "created_at": cdate, "parent_ids": [f"{(int(sha, 16) + 7) % (1 << 160):040x}"], "title": title,
            "message": msg, "author_name": author, "author_email": email, "authored_date": date,
            "committer_name": committer or author, "committer_email": ("noreply@gitlab.com" if committer else email),
            "committed_date": cdate, "trailers": {}, "extended_trailers": {}, "web_url": prefix + sha}


GL_COMMITS = [_gl_commit(0, "Ada Example", "ada@example.com", "Fix flaky cache test"),
              _gl_commit(1, "Ada Example", "ada@example.com", "Retry uploads with backoff", "Wraps the S3 call in retry().\n\nCloses #12"),
              _gl_commit(2, "Ben Reviewer", "ben@example.org", "Bump boto3 to 1.34.9"),
              _gl_commit(3, "Ada Example", "ada@example.com", "Add CI cache key", committer="GitLab"),
              _gl_commit(4, "Chen Maintainer", "chen@example.net", "Merge branch 'retry-uploads' into 'main'")]
GL_COMMITS_DOC = _compact(GL_COMMITS)


def _gl_mr(k=0):
    sha = f"{(0xed899a2f4b50b4370feeea94676502b42383c746 + k * 0x7654321) % (1 << 160):040x}"
    return {"id": 84201733 + k, "iid": 312 + k, "project_id": 4172, "title": f"Retry uploads with backoff ({k})", "description": "Closes #12", "state": "opened",
            "created_at": "2026-03-10T09:15:00.000Z", "updated_at": "2026-03-12T10:02:11.482Z", "merged_by": None, "merge_user": None,
            "merged_at": None, "closed_by": None, "closed_at": None, "target_branch": "main", "source_branch": "retry-uploads",
            "user_notes_count": 3, "upvotes": 0, "downvotes": 0,
            "author": {"id": 5521, "username": "ada", "name": "Ada Example", "state": "active", "locked": False,
                       "avatar_url": "https://gitlab.com/uploads/-/system/user/avatar/5521/avatar.png", "web_url": "https://gitlab.com/ada"},
            "assignees": [], "assignee": None, "reviewers": [], "source_project_id": 4172, "target_project_id": 4172, "labels": [],
            "draft": False, "work_in_progress": False, "milestone": None, "merge_when_pipeline_succeeds": False,
            "merge_status": "can_be_merged", "detailed_merge_status": "mergeable", "sha": sha, "merge_commit_sha": None,
            "squash_commit_sha": None, "discussion_locked": None, "should_remove_source_branch": None, "force_remove_source_branch": False,
            "reference": f"!{312 + k}", "references": {"short": f"!{312 + k}", "relative": f"!{312 + k}", "full": f"acme/widgets!{312 + k}"},
            "web_url": f"https://gitlab.com/acme/widgets/-/merge_requests/{312 + k}",
            "time_stats": {"time_estimate": 0, "total_time_spent": 0, "human_time_estimate": None, "human_total_time_spent": None},
            "squash": False, "squash_on_merge": False, "task_completion_status": {"count": 0, "completed_count": 0},
            "has_conflicts": False, "blocking_discussions_resolved": True, "approvals_before_merge": None,
            "diff_refs": {"base_sha": "9a4f1c2d5e0b7a3c8d6e1f2a3b4c5d6e7f8a9b0c", "head_sha": sha, "start_sha": "9a4f1c2d5e0b7a3c8d6e1f2a3b4c5d6e7f8a9b0c"}}


GL_MR_LIST_DOC = _compact([_gl_mr(k) for k in range(8)])
GL_CHANGES_DOC = _compact({"changes": [
    {"old_path": f"src/pkg/mod_{i}.py", "new_path": f"src/pkg/mod_{i}.py", "a_mode": "100644", "b_mode": "100644", "diff": "@@ -1 +1 @@\n-a\n+b\n",
     "new_file": False, "renamed_file": False, "deleted_file": False} for i in range(10)] + [
    {"old_path": "src/retry.py", "new_path": "src/retry.py", "a_mode": "0", "b_mode": "100644", "diff": "@@ -0,0 +1 @@\n+x\n",
     "new_file": True, "renamed_file": False, "deleted_file": False},
    {"old_path": "docs/old.md", "new_path": "docs/new.md", "a_mode": "100644", "b_mode": "100644", "diff": "", "new_file": False,
     "renamed_file": True, "deleted_file": False}]})

case(
    1426,
    claim="In GitLab v4 commit objects, a field that repeats another field of the same object (short_id as a prefix of id, committed_date and created_at as the author or committed date, committer_* as author_*, title as message's first line or message as title, web_url as the first entry's prefix plus id) is replaced by a '=source' marker; a '§gitlab' header marks the encoding and decode restores the exact bytes.",
    encode=gitlab_encode,
    decode=gitlab_decode,
    fixtures=[
        Fixture("repository/commits: 5 commits, compact layout (as the API returns it)", GL_COMMITS_DOC, True),
        Fixture("the same list pretty-printed", _pretty(GL_COMMITS), True),
        Fixture("20 commits, one committer differs on every fifth", _compact([_gl_commit(i % 20, "Ada Example", "ada@example.com", f"Change {i}",
                                                                              committer="GitLab" if i % 5 == 4 else None) for i in range(20)]), True),
        Fixture("one commit of five has a created_at unlike both its dates",
                _compact([{**GL_COMMITS[2], "created_at": "2026-04-01T00:00:00.000Z"}] + GL_COMMITS[:2] + GL_COMMITS[3:]), True),
        Fixture("merge request objects have no commit twins", GL_MR_LIST_DOC, False),
        Fixture("merge request change entries have none either", GL_CHANGES_DOC, False),
        Fixture("a lone commit whose twins differ keeps every literal",
                _compact([{**GL_COMMITS[0], "short_id": "zzzzzzzz", "committer_name": "Other", "created_at": "2025-01-01T00:00:00.000Z", "committer_email": "o@x.org",
                           "committed_date": "2025-01-02T00:00:00.000Z", "message": "other", "web_url": "https://x.org/c/1"}]), False),
        Fixture("GitHub-shaped JSON has none of these fields", GH_MCP_MINIMAL, False),
        Fixture("not JSON", "glab: 401 Unauthorized\n", False),
        Fixture("input that starts like the escape line", RAW + GL_COMMITS_DOC, None),
        Fixture("input that starts like the header", "§gitlab\n" + GL_COMMITS_DOC, None),
        Fixture("a literal marker value in the input", _compact([{**GL_COMMITS[0], "note": "=author_name"}]), None),
        Fixture("duplicate keys are not touched", '{"id":"abcdef0123456789abcdef0123456789abcdef01","short_id":"abcdef01","short_id":"abcdef01"}', None),
        Fixture("a lone commit whose title is only the first line of a longer message", _compact([GL_COMMITS[1]]), None),
    ],
    min_gain=0.05,  # characters; the hex ids are cheap in characters and costly in tokens, which fall about 40% more
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)


# ===============================================================================================================
# #1433 GitHub Actions workflow-command annotations from linters that print absolute paths (ruff --output-format=github, ESLint
# formatters): '::error title=..,file=/abs/root/src/a.py,line=1,col=8,...::src/a.py:1:8: F401 ...' repeats the checkout root on every
# line and restates path, line and column in the message. Header '§gha ROOT'; file=@rel hoists the root, file=@@rel also drops the
# message prefix 'rel:line:col: '.

_GHA_LINE = re.compile(r"::(error|warning|notice) ([^:\n]*)::(.*)")


def _gha_parse(line):
    m = _GHA_LINE.fullmatch(line)
    if not m:
        return None
    props = [p.partition("=") for p in m[2].split(",")]
    if any(sep == "" for _, sep, _ in props):
        return None
    return m[1], props, m[3]


def _gha_get(props, key):
    vals = [v for k, _, v in props if k == key]
    return vals[0] if len(vals) == 1 else None


def _gha_join(level, props, msg):
    return f"::{level} " + ",".join(f"{k}={v}" for k, _, v in props) + f"::{msg}"


def _gha_root(lines):
    for l in lines:
        p = _gha_parse(l)
        if p:
            f, ln, col = _gha_get(p[1], "file"), _gha_get(p[1], "line"), _gha_get(p[1], "col")
            if f and ln and col:
                m = re.match(r"(.+?):" + re.escape(ln) + ":" + re.escape(col) + ": ", p[2])
                if m and f.startswith("/") and f.endswith("/" + m[1]) and len(f) > len(m[1]) + 1:
                    return f[:-len(m[1]) - 1]
    files = [_gha_get(p[1], "file") for l in lines if (p := _gha_parse(l))]
    files = [f for f in files if f and f.startswith("/")]
    if len(files) >= 2:
        parts = [f.split("/")[:-1] for f in files]
        common = []
        for cols in zip(*parts):
            if len(set(cols)) != 1:
                break
            common.append(cols[0])
        root = "/".join(common)
        if root.count("/") >= 2:
            return root
    return None


def _gha_build(text):
    lines, nl = nl_split(text)
    if any("file=@" in l for l in lines):
        return None
    root = _gha_root(lines)
    if not root or "\n" in root:
        return None
    out = []
    for l in lines:
        p = _gha_parse(l)
        f = _gha_get(p[1], "file") if p else None
        if not (p and f and f.startswith(root + "/") and len(f) > len(root) + 1):
            out.append(l)
            continue
        level, props, msg = p
        rel = f[len(root) + 1:]
        ln, col = _gha_get(props, "line"), _gha_get(props, "col")
        pre = f"{rel}:{ln}:{col}: " if ln and col else None
        if pre and msg.startswith(pre):
            val, msg = "@@" + rel, msg[len(pre):]
        else:
            val = "@" + rel
        out.append(_gha_join(level, [(k, s, val if k == "file" else v) for k, s, v in props], msg))
    return f"{SIG}gha {root}\n" + "\n".join(out) + ("\n" if nl else "")


def gha_decode(text):
    if text.startswith(RAW):
        return text[len(RAW):]
    if not text.startswith(f"{SIG}gha "):
        return text
    head, _, rest = text.partition("\n")
    root = head[len(f"{SIG}gha "):]
    lines, nl = nl_split(rest)
    out = []
    for l in lines:
        p = _gha_parse(l)
        f = _gha_get(p[1], "file") if p else None
        if not (p and f and f.startswith("@")):
            out.append(l)
            continue
        level, props, msg = p
        full = f.startswith("@@")
        rel = f[2:] if full else f[1:]
        if full:
            msg = f"{rel}:{_gha_get(props, 'line')}:{_gha_get(props, 'col')}: " + msg
        out.append(_gha_join(level, [(k, s, f"{root}/{rel}" if k == "file" else v) for k, s, v in props], msg))
    return "\n".join(out) + ("\n" if nl else "")


def gha_encode(text):
    return sigil_guard(text, _gha_build, gha_decode)


RUFF_LOG = ("Run ruff check --output-format=github .\n" + RUFF_GHA + "Found 35 errors.\n##[error]Process completed with exit code 1.\n")
_ESLINT_LIKE = "".join(  # reconstructed from memory: an ESLint GitHub formatter prints no path:line:col prefix in the message
    f"::error file=/home/runner/work/app/app/src/components/Widget{i}.tsx,line={10 + i},col={3 + i},title=@typescript-eslint/no-unused-vars::"
    f"'x{i}' is assigned a value but never used.\n" for i in range(6))

case(
    1433,
    claim="In GitHub Actions workflow-command annotation lines that carry an absolute file path (ruff --output-format=github, ESLint formatters), the checkout root is named once in a '§gha ROOT' header; file=ROOT/rel becomes file=@rel, and when the message starts with 'rel:line:col: ' that restated prefix is dropped too (file=@@rel); decode restores the exact bytes, and lines that do not fit stay verbatim.",
    encode=gha_encode,
    decode=gha_decode,
    fixtures=[
        Fixture("real: ruff 0.16.9 annotations, 35 findings in 5 files", RUFF_GHA, True),
        Fixture("the same inside a CI log with other lines", RUFF_LOG, True),
        Fixture("no message prefix: only the root is hoisted (reconstructed from memory)", _ESLINT_LIKE, True),
        Fixture("relative file paths (golangci-lint style) have no root to hoist",
                "".join(f"::error file=cmd/server/main.go,line={i},col=2::unused variable (unused)\n" for i in range(1, 6)), False),
        Fixture("real: ruff concise output is not an annotation", "src/app/models.py:1:8: F401 `os` imported but unused\n" * 5, False),
        Fixture("a single annotation: the message prefix still names the root", RUFF_GHA.split("\n")[0] + "\n", None),
        Fixture("a file in a sibling directory that shares the root's text stays literal",
                RUFF_GHA + "::error title=ruff (F401),file=/home/runner/work/widgets/widgets-fork/src/a.py,line=1,col=1,endLine=1,endColumn=2::src/a.py:1:1: F401 x\n", True),
        Fixture("a message that starts with the path but not with path:line:col: keeps its text",
                RUFF_GHA + "::error title=custom,file=/home/runner/work/widgets/widgets/src/app/models.py,line=3,col=4::src/app/models.py has a problem\n", True),
        Fixture("a path that starts with @ (scoped npm dir) is never rewritten",
                "::error file=/home/runner/w/a/@types/node/index.d.ts,line=1,col=1::x\n::error file=/home/runner/w/a/src/b.ts,line=2,col=2::y\n", False),
        Fixture("input that starts like the escape line", RAW + RUFF_GHA, None),
        Fixture("input that starts like the header", f"{SIG}gha /x/y\n::error file=@a,line=1,col=1::m\n", None),
        Fixture("a bare sigil", SIG + RUFF_GHA, None),
        Fixture("a message that contains '::' and a CRLF-terminated log",
                "::warning file=/r/w/a/x.py,line=1,col=1::x.py:1:1: a::b\r\n::warning file=/r/w/a/y.py,line=2,col=2::y.py:2:2: c\r\n", None),
    ],
    min_gain=0.15,
    idempotent=False,  # re-encoding encoded text must escape its leading sigil, so it grows by design
)
