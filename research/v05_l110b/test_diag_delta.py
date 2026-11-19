"""Hand-written synthetic fixtures only (no transcript text)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from diag_delta import encode, decode_keys, split_lines

PREV = """src/a.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/a.ts(20,1): error TS2304: Cannot find name 'foo'.
src/a.ts(30,1): error TS2304: Cannot find name 'foo'.
src/b.ts(3,3): error TS7006: Parameter 'x' implicitly has an 'any' type.
Found 4 errors in 2 files."""
CURR = """src/a.ts(15,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/a.ts(25,1): error TS2304: Cannot find name 'foo'.
src/c.ts(1,1): error TS2551: Property 'bar' does not exist.
src/c.ts(9,1): error TS2551: Property 'bar' does not exist.
Found 4 errors in 2 files."""

def test_roundtrip_multiplicity():
    enc = encode(PREV, CURR)
    got, unch = decode_keys(PREV, enc)
    want, _, _ = split_lines(CURR)
    assert got == want
    assert unch == 2          # shifted TS2322 + one TS2304 remain
    assert "NEW 2" in enc and "RESOLVED 2" in enc
    assert "src/a.ts(15,5)" not in enc   # unchanged, shifted line omitted

def test_identical_run():
    enc = encode(PREV, PREV)
    got, _ = decode_keys(PREV, enc)
    assert got == split_lines(PREV)[0] and "NEW" not in enc

def test_mypy_ruff():
    p = "a.py:3: error: Incompatible types  [assignment]\nx.py:1:1: F401 `os` imported but unused"
    c = "a.py:9: error: Incompatible types  [assignment]\nx.py:2:1: F401 `os` imported but unused\nx.py:5:1: E501 Line too long"
    got, _ = decode_keys(p, encode(p, c))
    assert got == split_lines(c)[0]
