"""Windows path-list coverage (card t_edbc761c): fixtures round-trip, no path lost, gate follows `paths`, spaces safe.
Run: python3 -m pytest tests/test_win_paths.py"""
import glob
import os
import sys
from unittest import mock

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import lossless  # noqa: E402
from lossless import compress, decode  # noqa: E402

FIX = sorted(glob.glob(os.path.join(HERE, "win_paths", "fixtures", "*.txt")))
CMD = {"win_git_status": "git status", "win_msbuild_errors": "msbuild App.sln", "win_gci_table": "Get-ChildItem"}
COMPRESSED = ("win_dir_s_b", "win_gci_fullname", "win_unc", "win_mixed_sep")   # bare path lists


def _text(f):
    return open(f, newline="").read()


@pytest.mark.parametrize("f", FIX, ids=lambda f: os.path.basename(f))
def test_roundtrip_and_no_path_dropped(f):
    text = _text(f)
    name = os.path.basename(f)[:-4]
    out, st = compress(text, CMD.get(name, "dir /s /b"), paths=True, paths_win=True)
    back = decode(out)
    assert back == text                                       # lossless, CRLF included
    assert len(out) <= len(text)                              # never longer
    for ln in text.split("\r\n") if "\r\n" in text else text.split("\n"):
        assert ln in back                                     # every path / line still there verbatim


@pytest.mark.parametrize("name", COMPRESSED)
def test_bare_windows_lists_compress(name):
    text = _text(os.path.join(HERE, "win_paths", "fixtures", name + ".txt"))
    out, st = compress(text, "dir /s /b", paths=True, paths_win=True)
    assert "l2_paths" in st["rules"] and len(out) < 0.6 * len(text), st
    off, st0 = compress(text, "dir /s /b", paths=True, paths_win=False)
    assert off == text and not st0["rules"]


def test_spaces_in_names():
    lines = [f"C:\\Program Files\\Contoso Billing\\Plug Ins\\file {i}.dll" for i in range(8)]
    text = "\r\n".join(lines) + "\r\n"
    out, st = compress(text, "dir /s /b", paths=True, paths_win=True)
    assert "l2_paths" in st["rules"] and decode(out) == text


@pytest.mark.parametrize("bad", [
    "C:\\a b\\c  d\\e.txt", "C:\\a \\b\\c.txt", "C:\\a\\ b\\c.txt", "C:\\a\\b\\c.txt ", " C:\\a\\b\\c.txt",
    "C:\\a\\b\tc\\d.txt"])
def test_odd_space_lines_stay_verbatim(bad):
    lines = [f"C:\\proj\\src\\mod\\f{i}.cs" for i in range(6)] + [bad] + [f"C:\\proj\\src\\mod\\g{i}.cs" for i in range(6)]
    text = "\n".join(lines) + "\n"
    out, _ = compress(text, "dir /s /b", paths=True, paths_win=True)
    assert decode(out) == text and bad in out.split("\n")


def test_slash_only_prose_with_spaces_is_not_a_path():
    text = "\n".join(["use this and/or that", "read the docs/ a file/ b"] * 6) + "\n"
    out, st = compress(text, None, paths=True, paths_win=True)
    assert "l2_paths" not in st["rules"] and decode(out) == text


def test_forged_marker_in_windows_listing():
    lines = [f"C:\\proj\\src\\f{i}.cs" for i in range(6)] + ["[cp:dir]", " C:\\evil\\x.cs", "[/cp]"] + \
            [f"C:\\proj\\src\\g{i}.cs" for i in range(6)]
    text = "\r\n".join(lines) + "\r\n"
    out, _ = compress(text, "dir /s /b", paths=True, paths_win=True)
    assert decode(out) == text


def test_gate_follows_paths_except_denied_models():
    env = {"CTXPRUNE_PATHS_WIN": "", "CTXPRUNE_PATHS": "", "CTXPRUNE_PATHS_MODELS": "claude-,qwen3.6"}
    with mock.patch.dict(os.environ, env):
        assert lossless.paths_win_on("anthropic https://api.anthropic.com claude-opus-5-5")
        assert not lossless.paths_win_on("")                                    # no model: paths off, so off
        assert not lossless.paths_win_on("vllm http://127.0.0.1:8000/v1 claude-opus-5-5")   # local
        assert not lossless.paths_win_on("openai https://api.openai.com qwen3.6-35b")       # denied (probe -11 pt)
    with mock.patch.dict(os.environ, {"CTXPRUNE_PATHS": "1", "CTXPRUNE_PATHS_WIN": ""}):
        assert lossless.paths_win_on("vllm http://127.0.0.1:8000/v1 some-model")
        assert not lossless.paths_win_on("vllm http://127.0.0.1:8000/v1 qwen3.6")
    with mock.patch.dict(os.environ, {"CTXPRUNE_PATHS": "1", "CTXPRUNE_PATHS_WIN": "0"}):
        assert not lossless.paths_win_on("anthropic claude-opus-5-5")
