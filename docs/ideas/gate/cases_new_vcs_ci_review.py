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
