"""Package-manager, test-runner, build-tool and stack-trace output formats: #1404 #1405 #1406 #1407 #1408 #1409 #1410 #1411 #1412 #1413.

Ten byte-exact roundtrip cases, all built the same way, each on REAL captures taken in this container (pytest 9.2.0, ninja,
ctest 3.28, python 3.13 unittest, cargo/rustc, node 22 --test, pip 24, mvn 3.9 + surefire 3.2.5; paths anonymised to
/home/dev/proj and the env-specific "Picked up JAVA_TOOL_OPTIONS" line removed). The tools print a lot of text that is a
function of other text in the same output (a percentage from a running count, a step counter, the dot leader that pads a
name to a fixed width, a method name repeated inside its own dotted id, a toolchain hash repeated on every frame, a URL
that is spelled out by its Maven coordinates). The encodings below delete or shorten that text and a decoder recomputes it.

Conventions (see cases_logs.py for the same ones):

* Every encoding starts with a legend line that begins with the sigil "§". The legend is also what tells a reader what was
  done. An input that itself starts with the sigil is escaped with RAW, so decode is a bijection on what encode returns;
  because of that escape the cases set idempotent=False (an injective encoding cannot also be idempotent).
* Each encoder VERIFIES itself: it only returns the encoded text when its own decoder reproduces the input byte for byte and
  the result is strictly shorter, otherwise the input comes back unchanged. That makes a derived value safe to drop only when
  the tool really printed what the formula says (a pytest percentage that does not match, a ctest counter with a gap, a Maven
  URL that does not follow the repository layout all stay verbatim). The decoders are separate code from the encoders.
"""
from __future__ import annotations

import contextlib
import re

from harness import Fixture, case

SIG = "§"
RAW = "§raw\n"


def framed(magic: str, core_enc, core_dec):
    """Legend-framed encoding: `core_enc(text)` returns the body (or None when it does not apply); `core_dec(body)` inverts it.
    The pair returned refuses (returns the input) unless decoding reproduces the input exactly and the result is shorter."""

    def dec(t: str) -> str:
        if t.startswith(RAW):
            return t[len(RAW):]
        if t.startswith(magic):
            return core_dec(t[len(magic):])
        return t

    def enc(text: str) -> str:
        with contextlib.suppress(Exception):  # total: any failure means "does not apply"
            body = core_enc(text)
            if body is not None:
                out = magic + body
                if len(out) < len(text) and dec(out) == text:
                    return out
        return RAW + text if text.startswith(SIG) else text

    return enc, dec


def sigil_fixtures(magic: str, sample: str) -> list[Fixture]:
    """Adversarial inputs for any legend-framed encoding: text that starts like the encoder's own output."""
    return [
        Fixture("starts with the escape line", RAW + sample, None),
        Fixture("starts with the legend", magic + sample, None),
        Fixture("starts with a bare sigil", SIG + sample, None),
    ]


def lines_nl(text: str) -> tuple[list[str], bool]:
    """'a\\nb\\n' -> (['a','b'], True); 'a\\nb' -> (['a','b'], False)."""
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return text.split("\n"), False


# pytest 9.2.0 -v, 80 columns, 28 tests, one failure and one teardown error (real capture)
PT_MAIN = r"""
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3
hypothesis profile 'default'
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collecting ... collected 28 items

tests/test_fix.py::test_with_teardown PASSED                             [  3%]
tests/test_fix.py::test_with_teardown ERROR                              [  3%]
tests/test_fix.py::test_print PASSED                                     [  7%]
tests/test_fix.py::test_next PASSED                                      [ 10%]
tests/test_math.py::test_add[0-1] PASSED                                 [ 14%]
tests/test_math.py::test_add[1-2] PASSED                                 [ 17%]
tests/test_math.py::test_add[2-3] PASSED                                 [ 21%]
tests/test_math.py::test_add[3-4] PASSED                                 [ 25%]
tests/test_math.py::test_add[4-5] PASSED                                 [ 28%]
tests/test_math.py::test_add[5-6] PASSED                                 [ 32%]
tests/test_math.py::test_add[6-7] PASSED                                 [ 35%]
tests/test_math.py::test_add[7-8] PASSED                                 [ 39%]
tests/test_math.py::test_add[8-9] PASSED                                 [ 42%]
tests/test_math.py::test_add[9-10] PASSED                                [ 46%]
tests/test_math.py::test_add[10-11] PASSED                               [ 50%]
tests/test_math.py::test_add[11-12] PASSED                               [ 53%]
tests/test_math.py::test_fail FAILED                                     [ 57%]
tests/test_math.py::test_ok PASSED                                       [ 60%]
tests/test_math.py::test_skip SKIPPED (no)                               [ 64%]
tests/test_math.py::test_xf XFAIL                                        [ 67%]
tests/test_str.py::TestStr::test_a PASSED                                [ 71%]
tests/test_str.py::TestStr::test_b PASSED                                [ 75%]
tests/test_str.py::TestStr::test_c PASSED                                [ 78%]
tests/test_str.py::TestStr::test_d PASSED                                [ 82%]
tests/test_str.py::TestStr::test_e PASSED                                [ 85%]
tests/test_str.py::TestStr::test_f PASSED                                [ 89%]
tests/test_str.py::TestStr::test_g PASSED                                [ 92%]
tests/test_str.py::test_h PASSED                                         [ 96%]
tests/test_str.py::test_i PASSED                                         [100%]

==================================== ERRORS ====================================
___________________ ERROR at teardown of test_with_teardown ____________________

    @pytest.fixture
    def res():
        yield 1
>       raise RuntimeError("teardown boom")
E       RuntimeError: teardown boom

tests/test_fix.py:5: RuntimeError
=================================== FAILURES ===================================
__________________________________ test_fail ___________________________________

    def test_fail():
>       assert 1 == 2
E       assert 1 == 2

tests/test_math.py:7: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_fail - assert 1 == 2
ERROR tests/test_fix.py::test_with_teardown - RuntimeError: teardown boom
========= 1 failed, 25 passed, 1 skipped, 1 xfailed, 1 error in 0.15s ==========
"""[1:]

# pytest -v where a fixture teardown errors: the same node id is reported twice (real capture)
PT_TEARDOWN = r"""
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3
hypothesis profile 'default'
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collecting ... collected 3 items

tests/test_fix.py::test_with_teardown PASSED                             [ 33%]
tests/test_fix.py::test_with_teardown ERROR                              [ 33%]
tests/test_fix.py::test_print PASSED                                     [ 66%]
tests/test_fix.py::test_next PASSED                                      [100%]

==================================== ERRORS ====================================
___________________ ERROR at teardown of test_with_teardown ____________________

    @pytest.fixture
    def res():
        yield 1
>       raise RuntimeError("teardown boom")
E       RuntimeError: teardown boom

tests/test_fix.py:5: RuntimeError
=========================== short test summary info ============================
ERROR tests/test_fix.py::test_with_teardown - RuntimeError: teardown boom
========================== 3 passed, 1 error in 0.14s ==========================
"""[1:]

# pytest -v with COLUMNS=120 (real capture)
PT_W120 = r"""
================================================= test session starts ==================================================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3
hypothesis profile 'default'
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collecting ... collected 9 items

tests/test_str.py::TestStr::test_a PASSED                                                                        [ 11%]
tests/test_str.py::TestStr::test_b PASSED                                                                        [ 22%]
tests/test_str.py::TestStr::test_c PASSED                                                                        [ 33%]
tests/test_str.py::TestStr::test_d PASSED                                                                        [ 44%]
tests/test_str.py::TestStr::test_e PASSED                                                                        [ 55%]
tests/test_str.py::TestStr::test_f PASSED                                                                        [ 66%]
tests/test_str.py::TestStr::test_g PASSED                                                                        [ 77%]
tests/test_str.py::test_h PASSED                                                                                 [ 88%]
tests/test_str.py::test_i PASSED                                                                                 [100%]

================================================== 9 passed in 0.14s ===================================================
"""[1:]

# pytest -v with node ids longer than the terminal (no padding, a single space before the percent) (real capture)
PT_LONG = r"""
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3
hypothesis profile 'default'
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collecting ... collected 6 items

tests2/test_long_names.py::test_a_rather_long_test_name_that_describes_a_scenario_in_far_too_much_detail_to_fit_in_a_line PASSED [ 16%]
tests2/test_long_names.py::test_short PASSED                             [ 33%]
tests2/test_long_names.py::test_another_rather_long_name_that_pads_exactly_to_the_edge_of_an_eighty_column_terminal_x PASSED [ 50%]
tests2/test_long_names.py::test_y_boundary_67chars_padding_probe_aaaaaaaaaaaaaaaaaaaaaa PASSED [ 66%]
tests2/test_long_names.py::test_z_boundary_68chars_padding_probe_aaaaaaaaaaaaaaaaaaaaaaa PASSED [ 83%]
tests2/test_long_names.py::test_w_boundary_69chars_padding_probe_aaaaaaaaaaaaaaaaaaaaaaaa PASSED [100%]

============================== 6 passed in 0.13s ===============================
"""[1:]

# pytest -v -s: no progress column at all (real capture)
PT_NO_PROGRESS = r"""
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0 -- /usr/local/bin/python3
hypothesis profile 'default'
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collecting ... collected 3 items

tests/test_fix.py::test_with_teardown PASSED
tests/test_fix.py::test_with_teardown ERROR
tests/test_fix.py::test_print hello from test
PASSED
tests/test_fix.py::test_next PASSED

==================================== ERRORS ====================================
___________________ ERROR at teardown of test_with_teardown ____________________

    @pytest.fixture
    def res():
        yield 1
>       raise RuntimeError("teardown boom")
E       RuntimeError: teardown boom

tests/test_fix.py:5: RuntimeError
=========================== short test summary info ============================
ERROR tests/test_fix.py::test_with_teardown - RuntimeError: teardown boom
========================== 3 passed, 1 error in 0.14s ==========================
"""[1:]

# pytest default (dot) mode: percent per file line, not per test (real capture)
PT_DOTS = r"""
============================= test session starts ==============================
platform linux -- Python 3.13.18, pytest-9.2.0, pluggy-1.7.0
rootdir: /home/dev/proj
plugins: anyio-4.15.1, hypothesis-6.170.14, platformdirs-4.14.5
collected 28 items

tests/test_fix.py .E..                                                   [ 10%]
tests/test_math.py ............F.sx                                      [ 67%]
tests/test_str.py .........                                              [100%]

==================================== ERRORS ====================================
___________________ ERROR at teardown of test_with_teardown ____________________

    @pytest.fixture
    def res():
        yield 1
>       raise RuntimeError("teardown boom")
E       RuntimeError: teardown boom

tests/test_fix.py:5: RuntimeError
=================================== FAILURES ===================================
__________________________________ test_fail ___________________________________

    def test_fail():
>       assert 1 == 2
E       assert 1 == 2

tests/test_math.py:7: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_fail - assert 1 == 2
ERROR tests/test_fix.py::test_with_teardown - RuntimeError: teardown boom
========= 1 failed, 25 passed, 1 skipped, 1 xfailed, 1 error in 0.15s ==========
"""[1:]

# ninja 1.11 on a pipe, cmake-generated C project, 33 steps (real capture)
NINJA_OK = r"""
ninja: Entering directory `build'
[1/33] Building C object CMakeFiles/core.dir/src/mod6.c.o
[2/33] Building C object CMakeFiles/core.dir/src/mod1.c.o
[3/33] Building C object CMakeFiles/core.dir/src/mod2.c.o
[4/33] Building C object CMakeFiles/core.dir/src/mod5.c.o
[5/33] Building C object CMakeFiles/core.dir/src/mod4.c.o
[6/33] Building C object CMakeFiles/core.dir/src/mod3.c.o
[7/33] Building C object CMakeFiles/core.dir/src/mod7.c.o
[8/33] Building C object CMakeFiles/core.dir/src/mod8.c.o
[9/33] Building C object CMakeFiles/t_decode_utf8.dir/tests/decode_utf8.c.o
[10/33] Building C object CMakeFiles/t_parse_header.dir/tests/parse_header.c.o
[11/33] Building C object CMakeFiles/t_parse_body.dir/tests/parse_body.c.o
[12/33] Building C object CMakeFiles/t_encode_utf8.dir/tests/encode_utf8.c.o
[13/33] Building C object CMakeFiles/t_handles_empty_input.dir/tests/handles_empty_input.c.o
[14/33] Building C object CMakeFiles/t_roundtrip_json.dir/tests/roundtrip_json.c.o
[15/33] Building C object CMakeFiles/t_rejects_truncated_frame.dir/tests/rejects_truncated_frame.c.o
[16/33] Building C object CMakeFiles/t_retry_backoff_grows.dir/tests/retry_backoff_grows.c.o
[17/33] Building C object CMakeFiles/t_concurrent_writers.dir/tests/concurrent_writers.c.o
[18/33] Building C object CMakeFiles/t_timeout_is_honoured.dir/tests/timeout_is_honoured.c.o
[19/33] Building C object CMakeFiles/t_cache_eviction_lru.dir/tests/cache_eviction_lru.c.o
[20/33] Building C object CMakeFiles/t_shutdown_is_clean.dir/tests/shutdown_is_clean.c.o
[21/33] Linking C static library libcore.a
[22/33] Linking C executable t_roundtrip_json
[23/33] Linking C executable t_parse_header
[24/33] Linking C executable t_handles_empty_input
[25/33] Linking C executable t_parse_body
[26/33] Linking C executable t_encode_utf8
[27/33] Linking C executable t_timeout_is_honoured
[28/33] Linking C executable t_rejects_truncated_frame
[29/33] Linking C executable t_decode_utf8
[30/33] Linking C executable t_retry_backoff_grows
[31/33] Linking C executable t_cache_eviction_lru
[32/33] Linking C executable t_concurrent_writers
[33/33] Linking C executable t_shutdown_is_clean
"""[1:]

# ninja rebuild with a gcc -Wall warning printed between steps (real capture)
NINJA_WARN = r"""
ninja: Entering directory `build'
[1/33] Building C object CMakeFiles/core.dir/src/mod1.c.o
[2/33] Building C object CMakeFiles/core.dir/src/mod5.c.o
[3/33] Building C object CMakeFiles/core.dir/src/mod2.c.o
[4/33] Building C object CMakeFiles/core.dir/src/mod3.c.o
/home/dev/proj/src/mod3.c: In function 'f9':
/home/dev/proj/src/mod3.c:2:20: warning: unused variable 'unused' [-Wunused-variable]
    2 | int f9(int x){ int unused; return x; }
      |                    ^~~~~~
[5/33] Building C object CMakeFiles/core.dir/src/mod4.c.o
[6/33] Building C object CMakeFiles/core.dir/src/mod8.c.o
[7/33] Building C object CMakeFiles/core.dir/src/mod6.c.o
[8/33] Building C object CMakeFiles/t_parse_body.dir/tests/parse_body.c.o
[9/33] Building C object CMakeFiles/core.dir/src/mod7.c.o
[10/33] Building C object CMakeFiles/t_encode_utf8.dir/tests/encode_utf8.c.o
[11/33] Building C object CMakeFiles/t_parse_header.dir/tests/parse_header.c.o
[12/33] Building C object CMakeFiles/t_roundtrip_json.dir/tests/roundtrip_json.c.o
[13/33] Building C object CMakeFiles/t_decode_utf8.dir/tests/decode_utf8.c.o
[14/33] Building C object CMakeFiles/t_rejects_truncated_frame.dir/tests/rejects_truncated_frame.c.o
[15/33] Building C object CMakeFiles/t_handles_empty_input.dir/tests/handles_empty_input.c.o
[16/33] Building C object CMakeFiles/t_timeout_is_honoured.dir/tests/timeout_is_honoured.c.o
[17/33] Building C object CMakeFiles/t_retry_backoff_grows.dir/tests/retry_backoff_grows.c.o
[18/33] Building C object CMakeFiles/t_concurrent_writers.dir/tests/concurrent_writers.c.o
[19/33] Building C object CMakeFiles/t_cache_eviction_lru.dir/tests/cache_eviction_lru.c.o
[20/33] Building C object CMakeFiles/t_shutdown_is_clean.dir/tests/shutdown_is_clean.c.o
[21/33] Linking C static library libcore.a
[22/33] Linking C executable t_decode_utf8
[23/33] Linking C executable t_parse_body
[24/33] Linking C executable t_encode_utf8
[25/33] Linking C executable t_parse_header
[26/33] Linking C executable t_roundtrip_json
[27/33] Linking C executable t_handles_empty_input
[28/33] Linking C executable t_timeout_is_honoured
[29/33] Linking C executable t_retry_backoff_grows
[30/33] Linking C executable t_rejects_truncated_frame
[31/33] Linking C executable t_concurrent_writers
[32/33] Linking C executable t_cache_eviction_lru
[33/33] Linking C executable t_shutdown_is_clean
"""[1:]

# ctest 3.28 serial, 12 tests, one failing (real capture)
CTEST_SERIAL = r"""
Test project /home/dev/proj/build
      Start  1: parse_header
 1/12 Test  #1: parse_header .....................   Passed    0.00 sec
      Start  2: parse_body
 2/12 Test  #2: parse_body .......................   Passed    0.00 sec
      Start  3: encode_utf8
 3/12 Test  #3: encode_utf8 ......................   Passed    0.00 sec
      Start  4: decode_utf8
 4/12 Test  #4: decode_utf8 ......................   Passed    0.00 sec
      Start  5: roundtrip_json
 5/12 Test  #5: roundtrip_json ...................   Passed    0.00 sec
      Start  6: handles_empty_input
 6/12 Test  #6: handles_empty_input ..............   Passed    0.00 sec
      Start  7: rejects_truncated_frame
 7/12 Test  #7: rejects_truncated_frame ..........   Passed    0.00 sec
      Start  8: timeout_is_honoured
 8/12 Test  #8: timeout_is_honoured ..............   Passed    0.00 sec
      Start  9: retry_backoff_grows
 9/12 Test  #9: retry_backoff_grows ..............***Failed    0.00 sec
      Start 10: cache_eviction_lru
10/12 Test #10: cache_eviction_lru ...............   Passed    0.00 sec
      Start 11: concurrent_writers
11/12 Test #11: concurrent_writers ...............   Passed    0.00 sec
      Start 12: shutdown_is_clean
12/12 Test #12: shutdown_is_clean ................   Passed    0.00 sec

92% tests passed, 1 tests failed out of 12

Total Test time (real) =   0.03 sec

The following tests FAILED:
	  9 - retry_backoff_grows (Failed)
Errors while running CTest
Output from these tests are in: /home/dev/proj/build/Testing/Temporary/LastTest.log
Use "--rerun-failed --output-on-failure" to re-run the failed cases verbosely.
"""[1:]

# ctest -j4: Start lines are not next to their result line (real capture)
CTEST_J4 = r"""
Test project /home/dev/proj/build
      Start  9: retry_backoff_grows
      Start  1: parse_header
      Start  6: handles_empty_input
      Start  7: rejects_truncated_frame
 1/12 Test  #9: retry_backoff_grows ..............***Failed    0.01 sec
      Start  2: parse_body
 2/12 Test  #1: parse_header .....................   Passed    0.01 sec
      Start  8: timeout_is_honoured
 3/12 Test  #6: handles_empty_input ..............   Passed    0.01 sec
      Start  3: encode_utf8
 4/12 Test  #7: rejects_truncated_frame ..........   Passed    0.01 sec
      Start  4: decode_utf8
 5/12 Test  #2: parse_body .......................   Passed    0.01 sec
      Start 10: cache_eviction_lru
 6/12 Test  #8: timeout_is_honoured ..............   Passed    0.01 sec
      Start 12: shutdown_is_clean
 7/12 Test  #3: encode_utf8 ......................   Passed    0.01 sec
      Start  5: roundtrip_json
 8/12 Test  #4: decode_utf8 ......................   Passed    0.01 sec
      Start 11: concurrent_writers
 9/12 Test #10: cache_eviction_lru ...............   Passed    0.01 sec
10/12 Test #12: shutdown_is_clean ................   Passed    0.00 sec
11/12 Test  #5: roundtrip_json ...................   Passed    0.00 sec
12/12 Test #11: concurrent_writers ...............   Passed    0.00 sec

92% tests passed, 1 tests failed out of 12

Total Test time (real) =   0.02 sec

The following tests FAILED:
	  9 - retry_backoff_grows (Failed)
Errors while running CTest
Output from these tests are in: /home/dev/proj/build/Testing/Temporary/LastTest.log
Use "--rerun-failed --output-on-failure" to re-run the failed cases verbosely.
"""[1:]

# ctest --output-on-failure (real capture)
CTEST_OF = r"""
Test project /home/dev/proj/build
      Start  1: parse_header
 1/12 Test  #1: parse_header .....................   Passed    0.00 sec
      Start  2: parse_body
 2/12 Test  #2: parse_body .......................   Passed    0.00 sec
      Start  3: encode_utf8
 3/12 Test  #3: encode_utf8 ......................   Passed    0.00 sec
      Start  4: decode_utf8
 4/12 Test  #4: decode_utf8 ......................   Passed    0.00 sec
      Start  5: roundtrip_json
 5/12 Test  #5: roundtrip_json ...................   Passed    0.00 sec
      Start  6: handles_empty_input
 6/12 Test  #6: handles_empty_input ..............   Passed    0.00 sec
      Start  7: rejects_truncated_frame
 7/12 Test  #7: rejects_truncated_frame ..........   Passed    0.00 sec
      Start  8: timeout_is_honoured
 8/12 Test  #8: timeout_is_honoured ..............   Passed    0.00 sec
      Start  9: retry_backoff_grows
 9/12 Test  #9: retry_backoff_grows ..............***Failed    0.00 sec

      Start 10: cache_eviction_lru
10/12 Test #10: cache_eviction_lru ...............   Passed    0.00 sec
      Start 11: concurrent_writers
11/12 Test #11: concurrent_writers ...............   Passed    0.00 sec
      Start 12: shutdown_is_clean
12/12 Test #12: shutdown_is_clean ................   Passed    0.00 sec

92% tests passed, 1 tests failed out of 12

Total Test time (real) =   0.03 sec

The following tests FAILED:
	  9 - retry_backoff_grows (Failed)
Errors while running CTest
"""[1:]

# python 3.13 unittest -v with a docstring test, skip, expected failure, failure and error (real capture)
UT_MAIN = r"""
test_add (tests.test_math.TestMath.test_add) ... ok
test_doc (tests.test_math.TestMath.test_doc)
Checks the docstring variant. ... ok
test_fail (tests.test_math.TestMath.test_fail) ... FAIL
test_mul (tests.test_math.TestMath.test_mul) ... ok
test_skip (tests.test_math.TestMath.test_skip) ... skipped 'not now'
test_sub (tests.test_math.TestMath.test_sub) ... ok
test_sub_t (tests.test_math.TestMath.test_sub_t) ... ok
test_xfail (tests.test_math.TestMath.test_xfail) ... expected failure
test_err (tests.test_math.TestStr.test_err) ... ERROR
test_lower (tests.test_math.TestStr.test_lower) ... ok
test_upper (tests.test_math.TestStr.test_upper) ... ok

======================================================================
ERROR: test_err (tests.test_math.TestStr.test_err)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/dev/proj/tests/test_math.py", line 20, in test_err
    def test_err(self): raise RuntimeError("boom")
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^
RuntimeError: boom

======================================================================
FAIL: test_fail (tests.test_math.TestMath.test_fail)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/dev/proj/tests/test_math.py", line 6, in test_fail
    def test_fail(self): self.assertEqual(1, 2)
                         ~~~~~~~~~~~~~~~~^^^^^^
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 11 tests in 0.002s

FAILED (failures=1, errors=1, skipped=1, expected failures=1)
"""[1:]

# python 3.13 unittest -v, 24 tests in three classes (real capture)
UT_SERVICE = r"""
test_create_user (tests.test_service.TestAccounts.test_create_user) ... ok
test_delete_user (tests.test_service.TestAccounts.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestAccounts.test_duplicate_email_rejected) ... ok
test_login_rate_limit (tests.test_service.TestAccounts.test_login_rate_limit) ... ok
test_password_hash_changes (tests.test_service.TestAccounts.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestAccounts.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestAccounts.test_token_refresh) ... ok
test_update_email (tests.test_service.TestAccounts.test_update_email) ... ok
test_create_user (tests.test_service.TestBilling.test_create_user) ... ok
test_delete_user (tests.test_service.TestBilling.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestBilling.test_duplicate_email_rejected) ... FAIL
test_login_rate_limit (tests.test_service.TestBilling.test_login_rate_limit) ... ok
test_password_hash_changes (tests.test_service.TestBilling.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestBilling.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestBilling.test_token_refresh) ... ok
test_update_email (tests.test_service.TestBilling.test_update_email) ... ok
test_create_user (tests.test_service.TestInventory.test_create_user) ... ok
test_delete_user (tests.test_service.TestInventory.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestInventory.test_duplicate_email_rejected) ... ok
test_login_rate_limit (tests.test_service.TestInventory.test_login_rate_limit) ... ERROR
test_password_hash_changes (tests.test_service.TestInventory.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestInventory.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestInventory.test_token_refresh) ... ok
test_update_email (tests.test_service.TestInventory.test_update_email) ... ok

======================================================================
ERROR: test_login_rate_limit (tests.test_service.TestInventory.test_login_rate_limit)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/dev/proj/tests/test_service.py", line 26, in test_login_rate_limit
    def test_login_rate_limit(self): raise KeyError("sku-42")
                                     ^^^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'sku-42'

======================================================================
FAIL: test_duplicate_email_rejected (tests.test_service.TestBilling.test_duplicate_email_rejected)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/dev/proj/tests/test_service.py", line 15, in test_duplicate_email_rejected
    def test_duplicate_email_rejected(self): self.assertEqual("a", "b")
                                             ~~~~~~~~~~~~~~~~^^^^^^^^^^
AssertionError: 'a' != 'b'
- a
+ b


----------------------------------------------------------------------
Ran 24 tests in 0.002s

FAILED (failures=1, errors=1)
"""[1:]

# python 3.13 unittest -v, the same 24 tests all passing (real capture)
UT_PASS = r"""
test_create_user (tests.test_service.TestAccounts.test_create_user) ... ok
test_delete_user (tests.test_service.TestAccounts.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestAccounts.test_duplicate_email_rejected) ... ok
test_login_rate_limit (tests.test_service.TestAccounts.test_login_rate_limit) ... ok
test_password_hash_changes (tests.test_service.TestAccounts.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestAccounts.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestAccounts.test_token_refresh) ... ok
test_update_email (tests.test_service.TestAccounts.test_update_email) ... ok
test_create_user (tests.test_service.TestBilling.test_create_user) ... ok
test_delete_user (tests.test_service.TestBilling.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestBilling.test_duplicate_email_rejected) ... ok
test_login_rate_limit (tests.test_service.TestBilling.test_login_rate_limit) ... ok
test_password_hash_changes (tests.test_service.TestBilling.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestBilling.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestBilling.test_token_refresh) ... ok
test_update_email (tests.test_service.TestBilling.test_update_email) ... ok
test_create_user (tests.test_service.TestInventory.test_create_user) ... ok
test_delete_user (tests.test_service.TestInventory.test_delete_user) ... ok
test_duplicate_email_rejected (tests.test_service.TestInventory.test_duplicate_email_rejected) ... ok
test_login_rate_limit (tests.test_service.TestInventory.test_login_rate_limit) ... ok
test_password_hash_changes (tests.test_service.TestInventory.test_password_hash_changes) ... ok
test_session_expiry (tests.test_service.TestInventory.test_session_expiry) ... ok
test_token_refresh (tests.test_service.TestInventory.test_token_refresh) ... ok
test_update_email (tests.test_service.TestInventory.test_update_email) ... ok

----------------------------------------------------------------------
Ran 24 tests in 0.000s

OK
"""[1:]

# cargo test (rustc 1.97, RUST_BACKTRACE=1), 30 tests on the default thread count, one panic with backtrace (real capture)
RS_TEST = r"""
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.01s
     Running unittests src/lib.rs (target/debug/deps/demo-88d774561b498750)

running 30 tests
test codec::json::tests::handles_unicode ... ok
test codec::json::tests::rejects_eof ... ok
test codec::json::tests::scans_number ... ok
test codec::json::tests::scans_string ... ok
test codec::utf8::tests::handles_unicode ... ignored
test codec::json::tests::scans_ident ... ok
test codec::json::tests::trims_space ... ok
test codec::utf8::tests::scans_ident ... ok
test codec::utf8::tests::rejects_eof ... ok
test codec::utf8::tests::scans_string ... ok
test codec::utf8::tests::scans_number ... ok
test codec::utf8::tests::trims_space ... ok
test net::http::tests::handles_unicode ... ok
test net::http::tests::rejects_eof ... ok
test net::http::tests::scans_ident ... ok
test net::http::tests::scans_number ... ok
test net::http::tests::trims_space ... ok
test parser::ast::tests::handles_unicode ... ok
test parser::ast::tests::rejects_eof ... ok
test parser::ast::tests::scans_number ... ok
test parser::ast::tests::scans_string ... ok
test parser::ast::tests::trims_space ... ok
test parser::lexer::tests::handles_unicode ... ok
test parser::lexer::tests::rejects_eof ... ok
test parser::lexer::tests::scans_ident ... ok
test parser::lexer::tests::scans_number ... ok
test parser::ast::tests::scans_ident ... ok
test parser::lexer::tests::scans_string ... ok
test parser::lexer::tests::trims_space ... ok
test net::http::tests::scans_string ... FAILED

failures:

---- net::http::tests::scans_string stdout ----

thread 'net::http::tests::scans_string' (15131) panicked at src/lib.rs:55:42:
assertion `left == right` failed
  left: 1
 right: 2
stack backtrace:
   0: __rustc::rust_begin_unwind
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/panicking.rs:689:5
   1: core::panicking::panic_fmt
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:80:14
   2: core::panicking::assert_failed_inner
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:439:17
   3: core::panicking::assert_failed::<i32, i32>
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:394:5
   4: demo::net::http::tests::scans_string
             at ./src/lib.rs:55:42
   5: demo::net::http::tests::scans_string::{closure#0}
             at ./src/lib.rs:55:39
   6: <demo::net::http::tests::scans_string::{closure#0} as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
   7: <fn() -> core::result::Result<(), alloc::string::String> as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.


failures:
    net::http::tests::scans_string

test result: FAILED. 28 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.10s

error: test failed, to rerun pass `--lib`
"""[1:]

# same crate with -- --test-threads=1 (real capture)
RS_TEST_T1 = r"""
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.01s
     Running unittests src/lib.rs (target/debug/deps/demo-88d774561b498750)

running 30 tests
test codec::json::tests::handles_unicode ... ok
test codec::json::tests::rejects_eof ... ok
test codec::json::tests::scans_ident ... ok
test codec::json::tests::scans_number ... ok
test codec::json::tests::scans_string ... ok
test codec::json::tests::trims_space ... ok
test codec::utf8::tests::handles_unicode ... ignored
test codec::utf8::tests::rejects_eof ... ok
test codec::utf8::tests::scans_ident ... ok
test codec::utf8::tests::scans_number ... ok
test codec::utf8::tests::scans_string ... ok
test codec::utf8::tests::trims_space ... ok
test net::http::tests::handles_unicode ... ok
test net::http::tests::rejects_eof ... ok
test net::http::tests::scans_ident ... ok
test net::http::tests::scans_number ... ok
test net::http::tests::scans_string ... FAILED
test net::http::tests::trims_space ... ok
test parser::ast::tests::handles_unicode ... ok
test parser::ast::tests::rejects_eof ... ok
test parser::ast::tests::scans_ident ... ok
test parser::ast::tests::scans_number ... ok
test parser::ast::tests::scans_string ... ok
test parser::ast::tests::trims_space ... ok
test parser::lexer::tests::handles_unicode ... ok
test parser::lexer::tests::rejects_eof ... ok
test parser::lexer::tests::scans_ident ... ok
test parser::lexer::tests::scans_number ... ok
test parser::lexer::tests::scans_string ... ok
test parser::lexer::tests::trims_space ... ok

failures:

---- net::http::tests::scans_string stdout ----

thread 'net::http::tests::scans_string' (15168) panicked at src/lib.rs:55:42:
assertion `left == right` failed
  left: 1
 right: 2
stack backtrace:
   0: __rustc::rust_begin_unwind
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/panicking.rs:689:5
   1: core::panicking::panic_fmt
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:80:14
   2: core::panicking::assert_failed_inner
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:439:17
   3: core::panicking::assert_failed::<i32, i32>
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:394:5
   4: demo::net::http::tests::scans_string
             at ./src/lib.rs:55:42
   5: demo::net::http::tests::scans_string::{closure#0}
             at ./src/lib.rs:55:39
   6: <demo::net::http::tests::scans_string::{closure#0} as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
   7: <fn() -> core::result::Result<(), alloc::string::String> as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.


failures:
    net::http::tests::scans_string

test result: FAILED. 28 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.10s

error: test failed, to rerun pass `--lib`
"""[1:]

# cargo test with doc-tests: test names that contain spaces (real capture)
RS_DOC = r"""
   Compiling docs v0.1.0 (/home/dev/proj)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.20s
     Running unittests src/lib.rs (target/debug/deps/docs-acd543637857303c)

running 1 test
test tests::it_works ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

   Doc-tests docs

running 2 tests
test src/lib.rs - sub (line 5) ... ok
test src/lib.rs - add (line 1) ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

all doctests ran in 0.36s; merged doctests compilation took 0.35s
"""[1:]

# cargo run, Option::unwrap on None with RUST_BACKTRACE=1 (real capture)
RS_PANIC = r"""
   Compiling demo v0.1.0 (/home/dev/proj)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.18s
     Running `target/debug/demo`

thread 'main' (15427) panicked at src/main.rs:4:15:
called `Option::unwrap()` on a `None` value
stack backtrace:
   0: __rustc::rust_begin_unwind
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/panicking.rs:689:5
   1: core::panicking::panic_fmt
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:80:14
   2: core::panicking::panic
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/panicking.rs:150:5
   3: core::option::unwrap_failed
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/option.rs:2248:5
   4: <core::option::Option<&u32>>::unwrap
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/option.rs:1013:21
   5: demo::lookup
             at ./src/main.rs:4:15
   6: demo::handle
             at ./src/main.rs:8:5
   7: demo::main
             at ./src/main.rs:13:20
   8: <fn() as core::ops::function::FnOnce<()>>::call_once
             at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/core/src/ops/function.rs:250:5
note: Some details are omitted, run with `RUST_BACKTRACE=full` for a verbose backtrace.
"""[1:]

# node 22 --test, tap reporter, a suite with a failure, a skip and an async test (real capture)
TAP_A = r"""
TAP version 13
# Subtest: adds numbers
ok 1 - adds numbers
  ---
  duration_ms: 0.924928
  type: 'test'
  ...
# Subtest: subtracts numbers
ok 2 - subtracts numbers
  ---
  duration_ms: 0.212557
  type: 'test'
  ...
# Subtest: parser
    # Subtest: parses ident
    ok 1 - parses ident
      ---
      duration_ms: 0.221629
      type: 'test'
      ...
    # Subtest: parses number
    ok 2 - parses number
      ---
      duration_ms: 0.089544
      type: 'test'
      ...
    # Subtest: rejects eof
    not ok 3 - rejects eof
      ---
      duration_ms: 0.888619
      type: 'test'
      location: '/home/dev/proj/a.test.js:8:3'
      failureType: 'testCodeFailure'
      error: |-
        Expected values to be strictly equal:
        
        1 !== 2
        
      code: 'ERR_ASSERTION'
      name: 'AssertionError'
      expected: 2
      actual: 1
      operator: 'strictEqual'
      stack: |-
        TestContext.<anonymous> (/home/dev/proj/a.test.js:8:38)
        Test.runInAsyncScope (node:async_hooks:214:14)
        Test.run (node:internal/test_runner/test:1047:25)
        Suite.processPendingSubtests (node:internal/test_runner/test:744:18)
        Test.postRun (node:internal/test_runner/test:1173:19)
        Test.run (node:internal/test_runner/test:1101:12)
        async Suite.processPendingSubtests (node:internal/test_runner/test:744:7)
      ...
    1..3
not ok 3 - parser
  ---
  duration_ms: 1.563595
  type: 'suite'
  location: '/home/dev/proj/a.test.js:5:6'
  failureType: 'subtestsFailed'
  error: '1 subtest failed'
  code: 'ERR_TEST_FAILURE'
  ...
# Subtest: skipped one
ok 4 - skipped one # SKIP
  ---
  duration_ms: 0.103662
  type: 'test'
  ...
# Subtest: async ok
ok 5 - async ok
  ---
  duration_ms: 7.093998
  type: 'test'
  ...
1..5
# tests 7
# suites 1
# pass 5
# fail 1
# cancelled 0
# skipped 1
# todo 0
# duration_ms 76.460889
"""[1:]

# node 22 --test, tap reporter, 18 passing tests (real capture)
TAP_B = r"""
TAP version 13
# Subtest: cache
    # Subtest: get hit
    ok 1 - get hit
      ---
      duration_ms: 1.040293
      type: 'test'
      ...
    # Subtest: get miss
    ok 2 - get miss
      ---
      duration_ms: 0.127188
      type: 'test'
      ...
    # Subtest: set overwrites
    ok 3 - set overwrites
      ---
      duration_ms: 0.090444
      type: 'test'
      ...
    # Subtest: evicts oldest
    ok 4 - evicts oldest
      ---
      duration_ms: 0.228801
      type: 'test'
      ...
    # Subtest: ttl expires
    ok 5 - ttl expires
      ---
      duration_ms: 0.097447
      type: 'test'
      ...
    # Subtest: clear empties
    ok 6 - clear empties
      ---
      duration_ms: 0.077901
      type: 'test'
      ...
    # Subtest: size tracks
    ok 7 - size tracks
      ---
      duration_ms: 0.097529
      type: 'test'
      ...
    # Subtest: keys sorted
    ok 8 - keys sorted
      ---
      duration_ms: 0.141686
      type: 'test'
      ...
    # Subtest: values copy
    ok 9 - values copy
      ---
      duration_ms: 0.228373
      type: 'test'
      ...
    # Subtest: has works
    ok 10 - has works
      ---
      duration_ms: 0.319374
      type: 'test'
      ...
    # Subtest: delete missing
    ok 11 - delete missing
      ---
      duration_ms: 0.080024
      type: 'test'
      ...
    # Subtest: stats count
    ok 12 - stats count
      ---
      duration_ms: 0.053422
      type: 'test'
      ...
    1..12
ok 1 - cache
  ---
  duration_ms: 5.043523
  type: 'suite'
  ...
# Subtest: top level a
ok 2 - top level a
  ---
  duration_ms: 0.09852
  type: 'test'
  ...
# Subtest: top level b
ok 3 - top level b
  ---
  duration_ms: 0.067591
  type: 'test'
  ...
# Subtest: top level c
ok 4 - top level c
  ---
  duration_ms: 0.051415
  type: 'test'
  ...
# Subtest: top level d
ok 5 - top level d
  ---
  duration_ms: 0.043712
  type: 'test'
  ...
# Subtest: top level e
ok 6 - top level e
  ---
  duration_ms: 0.04426
  type: 'test'
  ...
# Subtest: top level f
ok 7 - top level f
  ---
  duration_ms: 0.043088
  type: 'test'
  ...
1..7
# tests 18
# suites 1
# pass 18
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 72.495092
"""[1:]

# node 22 --test with the spec reporter (real capture)
NODE_SPEC = r"""
✔ adds numbers (0.880012ms)
✔ subtracts numbers (0.205061ms)
▶ parser
  ✔ parses ident (0.234158ms)
  ✔ parses number (0.094572ms)
  ✖ rejects eof (0.877086ms)
✖ parser (1.676499ms)
﹣ skipped one (0.126591ms) # SKIP
✔ async ok (7.425974ms)
ℹ tests 7
ℹ suites 1
ℹ pass 5
ℹ fail 1
ℹ cancelled 0
ℹ skipped 1
ℹ todo 0
ℹ duration_ms 70.643906

✖ failing tests:

test at a.test.js:8:3
✖ rejects eof (0.877086ms)
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
  
  1 !== 2
  
      at TestContext.<anonymous> (/home/dev/proj/a.test.js:8:38)
      at Test.runInAsyncScope (node:async_hooks:214:14)
      at Test.run (node:internal/test_runner/test:1047:25)
      at Suite.processPendingSubtests (node:internal/test_runner/test:744:18)
      at Test.postRun (node:internal/test_runner/test:1173:19)
      at Test.run (node:internal/test_runner/test:1101:12)
      at async Suite.processPendingSubtests (node:internal/test_runner/test:744:7) {
    generatedMessage: true,
    code: 'ERR_ASSERTION',
    actual: 1,
    expected: 2,
    operator: 'strictEqual',
    diff: 'simple'
  }
"""[1:]

# pip 24 install -r on a mostly installed environment, first 80 lines (real capture)
PIP_SAT = r"""
Requirement already satisfied: pytest in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 1)) (9.2.0)
Requirement already satisfied: hypothesis in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 2)) (6.170.14)
Requirement already satisfied: anyio in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 3)) (4.15.1)
Requirement already satisfied: requests in /root/.local/lib/python3.13/site-packages (from -r req.txt (line 4)) (2.34.2)
Requirement already satisfied: tiktoken in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 5)) (0.14.0)
Requirement already satisfied: mypy in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 6)) (2.4.1)
Requirement already satisfied: flake8 in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 7)) (7.5.0)
Requirement already satisfied: ruff in /usr/local/lib/python3.13/site-packages (from -r req.txt (line 8)) (0.17.3)
Collecting uv (from -r req.txt (line 9))
  Downloading uv-0.13.7-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (11 kB)
Collecting poetry (from -r req.txt (line 10))
  Downloading poetry-2.5.1-py3-none-any.whl.metadata (7.6 kB)
Requirement already satisfied: iniconfig>=2 in /usr/local/lib/python3.13/site-packages (from pytest->-r req.txt (line 1)) (2.3.1)
Requirement already satisfied: packaging>=24 in /usr/local/lib/python3.13/site-packages (from pytest->-r req.txt (line 1)) (26.3)
Requirement already satisfied: pluggy<2,>=1.6 in /usr/local/lib/python3.13/site-packages (from pytest->-r req.txt (line 1)) (1.7.0)
Requirement already satisfied: pygments>=2.15 in /usr/local/lib/python3.13/site-packages (from pytest->-r req.txt (line 1)) (2.21.0)
Requirement already satisfied: sortedcontainers<3.0.0,>=2.1.0 in /usr/local/lib/python3.13/site-packages (from hypothesis->-r req.txt (line 2)) (2.4.0)
Requirement already satisfied: idna>=2.8 in /root/.local/lib/python3.13/site-packages (from anyio->-r req.txt (line 3)) (3.20)
Requirement already satisfied: typing_extensions>=4.16.0 in /usr/local/lib/python3.13/site-packages (from anyio->-r req.txt (line 3)) (4.16.0)
Requirement already satisfied: charset_normalizer<4,>=2 in /root/.local/lib/python3.13/site-packages (from requests->-r req.txt (line 4)) (3.5.1)
Requirement already satisfied: urllib3<3,>=1.26 in /root/.local/lib/python3.13/site-packages (from requests->-r req.txt (line 4)) (2.8.0)
Requirement already satisfied: certifi>=2023.5.7 in /root/.local/lib/python3.13/site-packages (from requests->-r req.txt (line 4)) (2026.10.18)
Requirement already satisfied: regex in /usr/local/lib/python3.13/site-packages (from tiktoken->-r req.txt (line 5)) (2026.9.29)
Requirement already satisfied: mypy_extensions>=1.0.0 in /usr/local/lib/python3.13/site-packages (from mypy->-r req.txt (line 6)) (1.2.0)
Requirement already satisfied: pathspec>=1.0.0 in /usr/local/lib/python3.13/site-packages (from mypy->-r req.txt (line 6)) (1.1.1)
Requirement already satisfied: librt>=0.16.0 in /usr/local/lib/python3.13/site-packages (from mypy->-r req.txt (line 6)) (0.16.0)
Requirement already satisfied: ast-serialize<1.0.0,>=0.12.1 in /usr/local/lib/python3.13/site-packages (from mypy->-r req.txt (line 6)) (0.13.1)
Requirement already satisfied: mccabe<0.8.0,>=0.7.0 in /usr/local/lib/python3.13/site-packages (from flake8->-r req.txt (line 7)) (0.7.0)
Requirement already satisfied: pycodestyle<2.16.0,>=2.15.0 in /usr/local/lib/python3.13/site-packages (from flake8->-r req.txt (line 7)) (2.15.0)
Requirement already satisfied: pyflakes<4.2.0,>=4.1.0 in /usr/local/lib/python3.13/site-packages (from flake8->-r req.txt (line 7)) (4.1.0)
Collecting build<2.0.0,>=1.2.1 (from poetry->-r req.txt (line 10))
  Downloading build-1.6.1-py3-none-any.whl.metadata (5.6 kB)
Collecting cachecontrol<0.15.0,>=0.14.0 (from cachecontrol[filecache]<0.15.0,>=0.14.0->poetry->-r req.txt (line 10))
  Downloading cachecontrol-0.14.4-py3-none-any.whl.metadata (3.1 kB)
Collecting cleo<3.0.0,>=2.1.0 (from poetry->-r req.txt (line 10))
  Downloading cleo-2.1.0-py3-none-any.whl.metadata (12 kB)
Collecting dulwich<2,>=0.25.0 (from poetry->-r req.txt (line 10))
  Downloading dulwich-1.2.20-cp313-cp313-manylinux_2_28_x86_64.whl.metadata (6.1 kB)
Collecting fastjsonschema<3.0.0,>=2.18.0 (from poetry->-r req.txt (line 10))
  Using cached fastjsonschema-2.22.3-py3-none-any.whl.metadata (2.1 kB)
Collecting findpython<0.9.0,>=0.6.2 (from poetry->-r req.txt (line 10))
  Downloading findpython-0.8.0-py3-none-any.whl.metadata (5.4 kB)
Collecting installer<2.0.0,>=0.7.0 (from poetry->-r req.txt (line 10))
  Downloading installer-1.1.0-py3-none-any.whl.metadata (1.3 kB)
Collecting keyring<26.0.0,>=25.1.0 (from poetry->-r req.txt (line 10))
  Downloading keyring-25.7.0-py3-none-any.whl.metadata (21 kB)
Collecting pbs-installer>=2025.6.10 (from pbs-installer[download,install]>=2025.6.10->poetry->-r req.txt (line 10))
  Downloading pbs_installer-2026.10.25-py3-none-any.whl.metadata (1.2 kB)
Collecting pkginfo<2.0,>=1.12 (from poetry->-r req.txt (line 10))
  Downloading pkginfo-1.13.3-py3-none-any.whl.metadata (14 kB)
Collecting platformdirs<5,>=3.0.0 (from poetry->-r req.txt (line 10))
  Using cached platformdirs-4.14.5-py3-none-any.whl.metadata (5.7 kB)
Collecting poetry-core==2.5.0 (from poetry->-r req.txt (line 10))
  Downloading poetry_core-2.5.0-py3-none-any.whl.metadata (3.8 kB)
Collecting pyproject-hooks<2.0.0,>=1.0.0 (from poetry->-r req.txt (line 10))
  Downloading pyproject_hooks-1.3.3-py3-none-any.whl.metadata (1.3 kB)
Collecting requests-toolbelt<2.0.0,>=1.0.0 (from poetry->-r req.txt (line 10))
  Downloading requests_toolbelt-1.0.0-py2.py3-none-any.whl.metadata (14 kB)
Collecting shellingham<2.0,>=1.5 (from poetry->-r req.txt (line 10))
  Downloading shellingham-1.5.4-py2.py3-none-any.whl.metadata (3.5 kB)
Collecting tomlkit<1.0.0,>=0.11.4 (from poetry->-r req.txt (line 10))
  Downloading tomlkit-0.15.2-py3-none-any.whl.metadata (3.4 kB)
Collecting trove-classifiers>=2022.5.19 (from poetry->-r req.txt (line 10))
  Downloading trove_classifiers-2026.9.21.13-py3-none-any.whl.metadata (2.4 kB)
Collecting virtualenv>=20.26.6 (from poetry->-r req.txt (line 10))
  Downloading virtualenv-21.15.5-py3-none-any.whl.metadata (4.1 kB)
Collecting msgpack<2.0.0,>=0.5.2 (from cachecontrol<0.15.0,>=0.14.0->cachecontrol[filecache]<0.15.0,>=0.14.0->poetry->-r req.txt (line 10))
  Downloading msgpack-1.2.4-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (8.3 kB)
Collecting filelock>=3.8.0 (from cachecontrol[filecache]<0.15.0,>=0.14.0->poetry->-r req.txt (line 10))
  Downloading filelock-4.2.8-py3-none-any.whl.metadata (2.3 kB)
Collecting crashtest<0.5.0,>=0.4.1 (from cleo<3.0.0,>=2.1.0->poetry->-r req.txt (line 10))
  Downloading crashtest-0.4.1-py3-none-any.whl.metadata (1.1 kB)
Collecting rapidfuzz<4.0.0,>=3.0.0 (from cleo<3.0.0,>=2.1.0->poetry->-r req.txt (line 10))
  Downloading rapidfuzz-3.14.6-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (12 kB)
Collecting SecretStorage>=3.2 (from keyring<26.0.0,>=25.1.0->poetry->-r req.txt (line 10))
  Downloading secretstorage-3.5.0-py3-none-any.whl.metadata (4.0 kB)
Collecting jeepney>=0.4.2 (from keyring<26.0.0,>=25.1.0->poetry->-r req.txt (line 10))
  Downloading jeepney-0.9.0-py3-none-any.whl.metadata (1.2 kB)
Collecting jaraco.classes (from keyring<26.0.0,>=25.1.0->poetry->-r req.txt (line 10))
  Downloading jaraco.classes-3.4.0-py3-none-any.whl.metadata (2.6 kB)
"""[1:]

# mvn 3.9 -B first build into an empty local repo: two slices of one real run (sequential pairs, then a parallel batch)
MVN_DL = r"""
[INFO] Scanning for projects...
[INFO] 
[INFO] -------------------------------< a:demo >-------------------------------
[INFO] Building demo 1.0
[INFO]   from pom.xml
[INFO] --------------------------------[ jar ]---------------------------------
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-resources-plugin/3.3.1/maven-resources-plugin-3.3.1.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-resources-plugin/3.3.1/maven-resources-plugin-3.3.1.pom (8.2 kB at 23 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-plugins/39/maven-plugins-39.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-plugins/39/maven-plugins-39.pom (8.1 kB at 184 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/maven-parent/39/maven-parent-39.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/maven-parent/39/maven-parent-39.pom (48 kB at 872 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/apache/29/apache-29.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/apache/29/apache-29.pom (21 kB at 423 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-resources-plugin/3.3.1/maven-resources-plugin-3.3.1.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-resources-plugin/3.3.1/maven-resources-plugin-3.3.1.jar (31 kB at 619 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-compiler-plugin/3.13.0/maven-compiler-plugin-3.13.0.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-compiler-plugin/3.13.0/maven-compiler-plugin-3.13.0.pom (10 kB at 101 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-plugins/41/maven-plugins-41.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-plugins/41/maven-plugins-41.pom (7.4 kB at 160 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/maven-parent/41/maven-parent-41.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/maven-parent/41/maven-parent-41.pom (50 kB at 1.1 MB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/apache/31/apache-31.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/apache/31/apache-31.pom (24 kB at 588 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-compiler-plugin/3.13.0/maven-compiler-plugin-3.13.0.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-compiler-plugin/3.13.0/maven-compiler-plugin-3.13.0.jar (83 kB at 1.4 MB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-surefire-plugin/3.2.5/maven-surefire-plugin-3.2.5.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-surefire-plugin/3.2.5/maven-surefire-plugin-3.2.5.pom (5.3 kB at 121 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/surefire/surefire/3.2.5/surefire-3.2.5.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/surefire/surefire/3.2.5/surefire-3.2.5.pom (22 kB at 499 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/junit-bom/5.9.3/junit-bom-5.9.3.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/junit-bom/5.9.3/junit-bom-5.9.3.pom (5.6 kB at 152 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-surefire-plugin/3.2.5/maven-surefire-plugin-3.2.5.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/plugins/maven-surefire-plugin/3.2.5/maven-surefire-plugin-3.2.5.jar (45 kB at 1.0 MB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter/5.10.2/junit-jupiter-5.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter/5.10.2/junit-jupiter-5.10.2.pom (3.2 kB at 65 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/junit-bom/5.10.2/junit-bom-5.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/junit-bom/5.10.2/junit-bom-5.10.2.pom (5.6 kB at 157 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-api/5.10.2/junit-jupiter-api-5.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-api/5.10.2/junit-jupiter-api-5.10.2.pom (3.2 kB at 66 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/opentest4j/opentest4j/1.3.0/opentest4j-1.3.0.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/opentest4j/opentest4j/1.3.0/opentest4j-1.3.0.pom (2.0 kB at 50 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-commons/1.10.2/junit-platform-commons-1.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-commons/1.10.2/junit-platform-commons-1.10.2.pom (2.8 kB at 62 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apiguardian/apiguardian-api/1.1.2/apiguardian-api-1.1.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apiguardian/apiguardian-api/1.1.2/apiguardian-api-1.1.2.pom (1.5 kB at 37 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-params/5.10.2/junit-jupiter-params-5.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-params/5.10.2/junit-jupiter-params-5.10.2.pom (3.0 kB at 61 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-engine/5.10.2/junit-jupiter-engine-5.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-engine/5.10.2/junit-jupiter-engine-5.10.2.pom (3.2 kB at 62 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-engine/1.10.2/junit-platform-engine-1.10.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-engine/1.10.2/junit-platform-engine-1.10.2.pom (3.2 kB at 63 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/codehaus/plexus/plexus-interpolation/1.26/plexus-interpolation-1.26.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/codehaus/plexus/plexus-interpolation/1.26/plexus-interpolation-1.26.jar (85 kB at 2.6 MB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/codehaus/plexus/plexus-utils/3.5.1/plexus-utils-3.5.1.jar
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-filtering/3.3.1/maven-filtering-3.3.1.jar
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/javax/inject/javax.inject/1/javax.inject-1.jar
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/sonatype/plexus/plexus-build-api/0.0.7/plexus-build-api-0.0.7.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/javax/inject/javax.inject/1/javax.inject-1.jar (2.5 kB at 73 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/commons-io/commons-io/2.11.0/commons-io-2.11.0.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/sonatype/plexus/plexus-build-api/0.0.7/plexus-build-api-0.0.7.jar (8.5 kB at 229 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/commons/commons-lang3/3.12.0/commons-lang3-3.12.0.jar
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar (41 kB at 979 kB/s)
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-filtering/3.3.1/maven-filtering-3.3.1.jar (55 kB at 1.3 MB/s)
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/codehaus/plexus/plexus-utils/3.5.1/plexus-utils-3.5.1.jar (269 kB at 5.6 MB/s)
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/commons-io/commons-io/2.11.0/commons-io-2.11.0.jar (327 kB at 3.5 MB/s)
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/commons/commons-lang3/3.12.0/commons-lang3-3.12.0.jar (587 kB at 6.2 MB/s)
[INFO] skip non existing resourceDirectory /home/dev/proj/src/main/resources
[INFO] 
[INFO] --- compiler:3.13.0:compile (default-compile) @ demo ---
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-shared-utils/3.4.2/maven-shared-utils-3.4.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-shared-utils/3.4.2/maven-shared-utils-3.4.2.pom (5.9 kB at 111 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-shared-incremental/1.1/maven-shared-incremental-1.1.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-shared-incremental/1.1/maven-shared-incremental-1.1.pom (4.7 kB at 105 kB/s)
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/apache/maven/shared/maven-shared-components/19/maven-shared-components-19.pom
"""[1:]

# mvn test, surefire 3.2.5 + JUnit 5: seven classes, one failure, one skip (real capture)
MVN_SUREFIRE = r"""
[INFO] Scanning for projects...
[INFO] 
[INFO] -------------------------------< a:demo >-------------------------------
[INFO] Building demo 1.0
[INFO]   from pom.xml
[INFO] --------------------------------[ jar ]---------------------------------
[INFO] 
[INFO] --- resources:3.3.1:resources (default-resources) @ demo ---
[INFO] skip non existing resourceDirectory /home/dev/proj/src/main/resources
[INFO] 
[INFO] --- compiler:3.13.0:compile (default-compile) @ demo ---
[INFO] Nothing to compile - all classes are up to date.
[INFO] 
[INFO] --- resources:3.3.1:testResources (default-testResources) @ demo ---
[INFO] skip non existing resourceDirectory /home/dev/proj/src/test/resources
[INFO] 
[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ demo ---
[INFO] Recompiling the module because of changed source code.
[INFO] Compiling 7 source files with javac [debug target 17] to target/test-classes
[WARNING] system modules path not set in conjunction with -source 17
[INFO] 
[INFO] --- surefire:3.2.5:test (default-test) @ demo ---
[INFO] Using auto detected provider org.apache.maven.surefire.junitplatform.JUnitPlatformProvider
[INFO] 
[INFO] -------------------------------------------------------
[INFO]  T E S T S
[INFO] -------------------------------------------------------
[INFO] Running a.web.OrderControllerTest
[ERROR] Tests run: 6, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.074 s <<< FAILURE! -- in a.web.OrderControllerTest
[ERROR] a.web.OrderControllerTest.t2 -- Time elapsed: 0.008 s <<< FAILURE!
org.opentest4j.AssertionFailedError: expected: <1> but was: <2>
	at org.junit.jupiter.api.AssertionFailureBuilder.build(AssertionFailureBuilder.java:151)
	at org.junit.jupiter.api.AssertionFailureBuilder.buildAndThrow(AssertionFailureBuilder.java:132)
	at org.junit.jupiter.api.AssertEquals.failNotEqual(AssertEquals.java:197)
	at org.junit.jupiter.api.AssertEquals.assertEquals(AssertEquals.java:150)
	at org.junit.jupiter.api.AssertEquals.assertEquals(AssertEquals.java:145)
	at org.junit.jupiter.api.Assertions.assertEquals(Assertions.java:531)
	at a.web.OrderControllerTest.t2(OrderControllerTest.java:6)
	at java.base/java.lang.reflect.Method.invoke(Method.java:580)
	at java.base/java.util.ArrayList.forEach(ArrayList.java:1596)
	at java.base/java.util.ArrayList.forEach(ArrayList.java:1596)

[INFO] Running a.web.SessionStoreTest
[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.007 s -- in a.web.SessionStoreTest
[INFO] Running a.web.AuthFilterTest
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.018 s -- in a.web.AuthFilterTest
[INFO] Running a.billing.LedgerRepositoryTest
[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.018 s -- in a.billing.LedgerRepositoryTest
[INFO] Running a.billing.InvoiceServiceTest
[INFO] Tests run: 5, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.031 s -- in a.billing.InvoiceServiceTest
[INFO] Running a.billing.TaxCalculatorTest
[WARNING] Tests run: 8, Failures: 0, Errors: 0, Skipped: 1, Time elapsed: 0.042 s -- in a.billing.TaxCalculatorTest
[INFO] Running a.FooTest
[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.006 s -- in a.FooTest
[INFO] 
[INFO] Results:
[INFO] 
[ERROR] Failures: 
[ERROR]   OrderControllerTest.t2:6 expected: <1> but was: <2>
[INFO] 
[ERROR] Tests run: 29, Failures: 1, Errors: 0, Skipped: 1
[INFO] 
[INFO] ------------------------------------------------------------------------
[INFO] BUILD FAILURE
[INFO] ------------------------------------------------------------------------
[INFO] Total time:  2.157 s
[INFO] Finished at: 2026-11-04T17:42:04Z
[INFO] ------------------------------------------------------------------------
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.2.5:test (default-test) on project demo: There are test failures.
[ERROR] 
[ERROR] Please refer to /home/dev/proj/target/surefire-reports for the individual test results.
[ERROR] Please refer to dump files (if any exist) [date].dump, [date]-jvmRun[N].dump and [date].dumpstream.
[ERROR] -> [Help 1]
[ERROR] 
[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.
[ERROR] Re-run Maven using the -X switch to enable full debug logging.
[ERROR] 
[ERROR] For more information about the errors and possible solutions, please read the following articles:
[ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/MojoFailureException
"""[1:]

# ===============================================================================================================
# #1404 pytest -v: the right-aligned "[ NN%]" on every result line is (distinct node ids reported so far) * 100 // collected,
# placed so the line is exactly the terminal width. Everything it needs is in the output itself: the width is the length of
# the "=== test session starts ===" rule, the total is on the "collected N items" line. So the column is dropped when every
# line equals what the formula prints, and re-added by the decoder. A teardown ERROR repeats a node id and does not advance
# the count (verified on a real capture), so the decoder counts distinct ids, as pytest does.

_PT_SESSION = re.compile(r"=+ test session starts =+")
_PT_COLLECTED = re.compile(r"(?:collecting \.\.\. )?collected (\d+) items?(?: / (\d+) deselected)?(?: / (\d+) selected)?")
_PT_RESULT = re.compile(r"(\S+::\S.*?) (?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?: \(.*\))?")
_PT_PCT = re.compile(r" \[ *(\d+)%\]$")
_PT_MAGIC = '§pytest: "[NN%]" column dropped, derivable from the width rule and "collected N"\n'


def _pt_total(m: re.Match) -> int | None:
    """Tests that pytest divides by: the selected count when there is one, nothing when only 'deselected' is shown."""
    if m.group(3):
        return int(m.group(3))
    return None if m.group(2) else int(m.group(1))


def _pt_line(content: str, pct: int, width: int) -> str:
    return content + f" [{pct:3d}%]".rjust(max(0, width - len(content) - 1))


def _pt_enc(text: str):
    width = total = None
    seen: set[str] = set()
    out, changed = [], False
    for line in text.split("\n"):
        if _PT_SESSION.fullmatch(line):
            width, total, seen = len(line), None, set()
        elif m := _PT_COLLECTED.fullmatch(line):
            total, seen = _pt_total(m), set()
        elif m := _PT_PCT.search(line):
            content = line[: m.start()].rstrip(" ")
            if r := _PT_RESULT.fullmatch(content):
                seen.add(r.group(1))
                if width and total and line == _pt_line(content, len(seen) * 100 // total, width):
                    line, changed = content, True
        elif width and total and _PT_RESULT.fullmatch(line):
            return None  # a result line that never had a percentage: the decoder would invent one
        out.append(line)
    return "\n".join(out) if changed else None


def _pt_dec(body: str) -> str:
    width = total = None
    seen: set[str] = set()
    out = []
    for line in body.split("\n"):
        if _PT_SESSION.fullmatch(line):
            width, total, seen = len(line), None, set()
        elif m := _PT_COLLECTED.fullmatch(line):
            total, seen = _pt_total(m), set()
        elif m := _PT_PCT.search(line):
            if r := _PT_RESULT.fullmatch(line[: m.start()].rstrip(" ")):
                seen.add(r.group(1))  # a verbatim line still counts toward the running total
        elif width and total and (r := _PT_RESULT.fullmatch(line)):
            seen.add(r.group(1))
            line = _pt_line(line, len(seen) * 100 // total, width)
        out.append(line)
    return "\n".join(out)


pt_enc, pt_dec = framed(_PT_MAGIC, _pt_enc, _pt_dec)

_PT_HAND = re.sub(r"(test_ok PASSED +\[ *)(\d+)%", lambda m: m.group(1) + str(int(m.group(2)) + 1) + "%", PT_MAIN)
_PT_XDIST = ("============================= test session starts ==============================\ncollecting ... collected 4 items\n\n"
             "[gw0] [ 25%] PASSED tests/test_a.py::test_one \n[gw1] [ 50%] PASSED tests/test_a.py::test_two \n"
             "tests/test_a.py::test_three PASSED                                       [ 75%]\n"
             "tests/test_a.py::test_four PASSED                                        [100%]\n")
_PT_NO_PCT_LINE = re.sub(r"(TestStr::test_c PASSED) +\[ *\d+%\]", r"\1", PT_MAIN)
assert _PT_HAND != PT_MAIN and _PT_NO_PCT_LINE != PT_MAIN

case(
    1404,
    claim="Drops the right-aligned '[ NN%]' column from pytest -v result lines when each equals distinct-ids-so-far*100//collected padded to the width of the session rule, and the decoder recomputes it byte for byte; a result line with a different or missing percentage stays verbatim or blocks the transform.",
    encode=pt_enc,
    decode=pt_dec,
    fixtures=[
        Fixture("pytest -v, 28 tests, a failure and a teardown error", PT_MAIN, True),
        Fixture("COLUMNS=120", PT_W120, True),
        Fixture("teardown error repeats a node id (6 result lines: the legend costs about what it saves)", PT_TEARDOWN, True),
        Fixture("-s: no percent column at all", PT_NO_PROGRESS, False),
        Fixture("default dot mode: percent per file line, not per test", PT_DOTS, False),
        Fixture("node ids longer than the terminal: no padding, one space (6 lines: legend costs more than it saves)", PT_LONG, None),
        Fixture("one hand-edited percentage stays verbatim, the other lines are still derived", _PT_HAND, True),
        Fixture("CRLF capture (Windows runner): the width rule no longer matches", PT_MAIN.replace("\n", "\r\n"), False),
        Fixture("a result line that lost its percent", _PT_NO_PCT_LINE, None),
        Fixture("xdist-style lines mixed in", _PT_XDIST, None),
        *sigil_fixtures(_PT_MAGIC, PT_TEARDOWN),
    ],
    min_gain=0.05,
    idempotent=False,
)

# ===============================================================================================================
# #1405 ninja on a pipe prints "[N/M] Building ..." where N counts finished edges one by one. The counter is dropped, the
# first number and M go in the legend, and the (few) lines that are not steps are marked "· " so the decoder can tell them
# apart without a marker on the (many) step lines.

_NJ_STEP = re.compile(r"\[(\d+)/(\d+)\] (.*)")
_NJ_MAGIC = "§ninja "
_NJ_LEGEND = re.compile(r"(\d+)/(\d+) ")


def _nj_enc(text: str):
    lines, nl = lines_nl(text)
    first = total = None
    k = 0
    out = []
    for line in lines:
        m = _NJ_STEP.fullmatch(line)
        if m:
            n, tot = int(m.group(1)), int(m.group(2))
            if str(n) != m.group(1) or str(tot) != m.group(2):
                return None
            if first is None:
                first, total, k = n, tot, n
            if n != k or tot != total or m.group(3).startswith("·"):
                return None
            k += 1
            out.append(m.group(3))
        else:
            out.append("·" if not line else "· " + line)
    if first is None:
        return None
    head = f'{first}/{total} (steps in order, "[N/M] " dropped; "· " marks other output)\n'
    return head + "\n".join(out) + ("\n" if nl else "")


def _nj_dec(body: str) -> str:
    head, _, rest = body.partition("\n")
    m = _NJ_LEGEND.match(head)
    k, total = int(m.group(1)), int(m.group(2))
    lines, nl = lines_nl(rest)
    out = []
    for line in lines:
        if line == "·":
            out.append("")
        elif line.startswith("· "):
            out.append(line[2:])
        else:
            out.append(f"[{k}/{total}] {line}")
            k += 1
    return "\n".join(out) + ("\n" if nl else "")


nj_enc, nj_dec = framed(_NJ_MAGIC, _nj_enc, _nj_dec)

_NJ_GAP = NINJA_OK.replace("[7/33] Building C object CMakeFiles/core.dir/src/mod7.c.o\n", "")
_NJ_MAKE = "".join(f"[ {p:2d}%] Building C object CMakeFiles/core.dir/src/mod{i}.c.o\n" for i, p in enumerate(range(10, 100, 10), 1))
_NJ_TWO_RUNS = NINJA_OK + NINJA_OK
_NJ_BLANKS = NINJA_WARN.replace("      |                    ^~~~~~\n", "      |                    ^~~~~~\n\n", 1).replace("[8/33]", "\n[8/33]", 1)
assert _NJ_BLANKS != NINJA_WARN
_NJ_DOT = NINJA_OK.replace("Building C object CMakeFiles/core.dir/src/mod2.c.o", "· Building C object")

case(
    1405,
    claim="ninja's non-tty '[N/M] ' step prefix is dropped (first number and M in the legend, other output marked '· '); decode renumbers the step lines, and a gap, a repeated run or a changed M leaves the text untouched.",
    encode=nj_enc,
    decode=nj_dec,
    fixtures=[
        Fixture("33-step cmake+ninja build", NINJA_OK, True),
        Fixture("rebuild with a gcc warning between steps", NINJA_WARN, True),
        Fixture("blank lines between the other output and the steps", _NJ_BLANKS, True),
        Fixture("make's [ NN%] prefix is not a step counter", _NJ_MAKE, False),
        Fixture("counter with a gap", _NJ_GAP, False),
        Fixture("two builds concatenated (counter restarts)", _NJ_TWO_RUNS, False),
        Fixture("step text that starts with the marker", _NJ_DOT, None),
        Fixture("blank line and no trailing newline", "ninja: Entering directory `build'\n\n[1/2] CC a.o\n[2/2] LINK a\nlast", None),
        Fixture("CRLF capture", NINJA_WARN.replace("\n", "\r\n"), None),
        *sigil_fixtures(_NJ_MAGIC, NINJA_OK),
    ],
    min_gain=0.05,
    idempotent=False,
)

# ===============================================================================================================
# #1406 ctest prints, per test, a "Start N: name" line and then "k/M Test #N: name ....... Passed 0.01 sec". The Start line
# repeats the result line that follows, k/M is the running count, and the dot leader pads the name to one width W.

_CT_START = re.compile(r" {6}Start +(\d+): (\S+)")
_CT_TEST = re.compile(r" *(\d+)/(\d+) Test +#(\d+): (\S+) (\.+)((?:   |\*\*\*).*)")
_CT_MAGIC = "§ctest "
_CT_PAIRED = re.compile(r"#(\d+) (\S+) (.*)")
_CT_ALONE = re.compile(r"Test #(\d+): (\S+) (.*)")


def _ct_render(c: int, total: int, width: int, n: str, name: str, tail: str) -> str:
    d = len(str(total))
    return f"{c:>{d}}/{total} Test {'#' + n:>{d + 1}}: {name} {'.' * (width - len(name) - 1)}{tail}"


def _ct_enc(text: str):
    lines = text.split("\n")
    out = []
    total = width = None
    c = 0
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        s = _CT_START.fullmatch(line)
        nxt = _CT_TEST.fullmatch(lines[i + 1]) if i + 1 < len(lines) else None
        t = _CT_TEST.fullmatch(line)
        paired = bool(s and nxt and nxt.group(3) == s.group(1) and nxt.group(4) == s.group(2))
        test = nxt if paired else t
        if test:
            c += 1
            w = len(test.group(4)) + 1 + len(test.group(5))
            if total is None:
                total, width = int(test.group(2)), w
            if int(test.group(1)) != c or int(test.group(2)) != total or w != width:
                return None
            n, name, tail = test.group(3), test.group(4), test.group(6)
            out.append(f"#{n} {name} {tail}" if paired else f"Test #{n}: {name} {tail}")
            i += 2 if paired else 1
            changed = True
            continue
        out.append(line)
        i += 1
    if not changed:
        return None
    return f"{total} {width} (N/M counter, Start lines and dot leaders dropped: rederivable)\n" + "\n".join(out)


def _ct_dec(body: str) -> str:
    head, _, rest = body.partition("\n")
    total, width = (int(x) for x in re.match(r"(\d+) (\d+) ", head).groups())
    d = len(str(total))
    out = []
    c = 0
    for line in rest.split("\n"):
        if m := _CT_PAIRED.fullmatch(line):
            c += 1
            out.append(f"{' ' * 6}Start {int(m.group(1)):>{d}}: {m.group(2)}")
            out.append(_ct_render(c, total, width, m.group(1), m.group(2), m.group(3)))
        elif m := _CT_ALONE.fullmatch(line):
            c += 1
            out.append(_ct_render(c, total, width, m.group(1), m.group(2), m.group(3)))
        else:
            out.append(line)
    return "\n".join(out)


ct_enc, ct_dec = framed(_CT_MAGIC, _ct_enc, _ct_dec)

_CT_NLIST = "Test project /home/dev/proj/build\n" + "".join(f"  Test #{i}: t{i}\n" for i in range(1, 9)) + "\nTotal Tests: 8\n"
_CT_GAP = CTEST_SERIAL.replace(" 5/12 Test  #5: roundtrip_json ...................   Passed    0.00 sec\n", "")

case(
    1406,
    claim="ctest result lines lose the 'k/M' counter and the dot leader, and a 'Start N: name' line directly above its own result line is folded into it; decode re-pads and renumbers exactly. Start lines that are not directly above their result (-j) keep their place, and a counter gap or restarted counter leaves the whole text untouched.",
    encode=ct_enc,
    decode=ct_dec,
    fixtures=[
        Fixture("12 tests serial, one failing", CTEST_SERIAL, True),
        Fixture("ctest --output-on-failure", CTEST_OF, True),
        Fixture("ctest -j4: Start lines far from their results", CTEST_J4, True),
        Fixture("ctest -N listing is not a result table", _CT_NLIST, False),
        Fixture("counter gap (a result line is missing)", _CT_GAP, False),
        Fixture("CRLF capture", CTEST_SERIAL.replace("\n", "\r\n"), None),
        *sigil_fixtures(_CT_MAGIC, CTEST_SERIAL),
    ],
    min_gain=0.1,
    idempotent=False,
)

# ===============================================================================================================
# #1407 Python 3.11+ unittest -v prints "test_add (tests.test_math.TestMath.test_add) ... ok": the method name is spelled
# twice. Print the dotted id only, and "~." for the class of the line above. FAIL:/ERROR: headers get the same treatment.

_UT_LINE = re.compile(r"(\w+) \(([\w.]+)\.\1\)( \.\.\. .*)?")
_UT_HEAD = re.compile(r"(FAIL|ERROR): (\w+) \(([\w.]+)\.\2\)( \(.*\))?")
_UT_ENC = re.compile(r"(~|[A-Za-z_]\w*(?:\.\w+)*)\.(\w+)( \.\.\. .*)?")
_UT_HEAD_ENC = re.compile(r"(FAIL|ERROR): ([A-Za-z_]\w*(?:\.\w+)*)\.(\w+)( \(.*\))?")
_UT_MAGIC = '§unittest: "name (pkg.Class.name)" written as "pkg.Class.name"; "~." = class of the line above\n'


def _ut_enc(text: str):
    out = []
    prev = None
    changed = False
    for line in text.split("\n"):
        if m := _UT_LINE.fullmatch(line):
            name, pre, rest = m.group(1), m.group(2), m.group(3) or ""
            out.append((f"~.{name}" if pre == prev else f"{pre}.{name}") + rest)
            prev, changed = pre, True
        elif m := _UT_HEAD.fullmatch(line):
            out.append(f"{m.group(1)}: {m.group(3)}.{m.group(2)}{m.group(4) or ''}")
            prev, changed = None, True
        else:
            out.append(line)
            prev = None
    return "\n".join(out) if changed else None


def _ut_dec(body: str) -> str:
    out = []
    prev = None
    for line in body.split("\n"):
        if m := _UT_ENC.fullmatch(line):
            pre = prev if m.group(1) == "~" else m.group(1)
            if pre is None:
                raise ValueError("ditto with no class above")
            out.append(f"{m.group(2)} ({pre}.{m.group(2)}){m.group(3) or ''}")
            prev = pre
        elif m := _UT_HEAD_ENC.fullmatch(line):
            out.append(f"{m.group(1)}: {m.group(3)} ({m.group(2)}.{m.group(3)}){m.group(4) or ''}")
            prev = None
        else:
            out.append(line)
            prev = None
    return "\n".join(out)


ut_enc, ut_dec = framed(_UT_MAGIC, _ut_enc, _ut_dec)

_UT_OLD = "test_add (tests.test_math.TestMath) ... ok\ntest_sub (tests.test_math.TestMath) ... ok\n\nRan 2 tests in 0.000s\n\nOK\n"  # python <= 3.10 layout
_UT_LOOKALIKE = UT_MAIN + "tests.test_math.TestMath.test_add ... ok\nos.path\n"

case(
    1407,
    claim="unittest -v lines 'name (pkg.Class.name) ... status' become 'pkg.Class.name ... status' ('~.name' when the class equals the previous line's), and FAIL:/ERROR: headers likewise; decode restores the repeated name, and lines whose id does not end in the name, or the python <=3.10 layout, stay verbatim.",
    encode=ut_enc,
    decode=ut_dec,
    fixtures=[
        Fixture("24 tests in three classes, all passing", UT_PASS, True),
        Fixture("24 tests in three classes, a failure and an error", UT_SERVICE, True),
        Fixture("docstring, skip, expected failure, subtests", UT_MAIN, True),
        Fixture("python <=3.10 layout 'name (pkg.Class)'", _UT_OLD, False),
        Fixture("lines that already look like the encoding", _UT_LOOKALIKE, None),
        *sigil_fixtures(_UT_MAGIC, UT_MAIN),
    ],
    min_gain=0.1,
    idempotent=False,
)

# ===============================================================================================================
# #1408 libtest (cargo test): "test parser::lexer::tests::scans_ident ... ok". Drop the 'test' and '...' wrappers and write
# '~::name' when the module path equals the line above's. Threads reorder lines slightly, so the saving is on runs of tests
# from one module, not on every line.

_RT_STATUS = r"(?:ok|FAILED|ignored(?:, .*)?|bench: .*)(?: <\d+\.\d+s>)?"
_RT_LINE = re.compile(rf"test ((?:\w+::)*\w+)::(\w+) \.\.\. ({_RT_STATUS})")
_RT_ENC = re.compile(rf"((?:\w+::)*\w+|~)::(\w+) ({_RT_STATUS})")
_RT_MAGIC = '§cargo-test: "test P::n ... S" written as "P::n S"; "~::" = module path of the line above\n'


def _rt_enc(text: str):
    out = []
    prev = None
    changed = False
    for line in text.split("\n"):
        if m := _RT_LINE.fullmatch(line):
            pre = m.group(1)
            out.append(f"{'~' if pre == prev else pre}::{m.group(2)} {m.group(3)}")
            prev, changed = pre, True
        else:
            out.append(line)
            prev = None
    return "\n".join(out) if changed else None


def _rt_dec(body: str) -> str:
    out = []
    prev = None
    for line in body.split("\n"):
        if m := _RT_ENC.fullmatch(line):
            pre = prev if m.group(1) == "~" else m.group(1)
            if pre is None:
                raise ValueError("ditto with no module above")
            out.append(f"test {pre}::{m.group(2)} ... {m.group(3)}")
            prev = pre
        else:
            out.append(line)
            prev = None
    return "\n".join(out)


rt_enc, rt_dec = framed(_RT_MAGIC, _rt_enc, _rt_dec)

_RT_REPORT_TIME = "running 3 tests\ntest a::b::x ... ok <0.001s>\ntest a::b::y ... ok <0.002s>\ntest a::b::z ... FAILED\n\ntest result: FAILED. 2 passed; 1 failed\n"
_RT_LOOKALIKE = RS_TEST + "a::b::c ok\n~::d ok\n"

case(
    1408,
    claim="cargo test list lines 'test P::n ... status' are written 'P::n status' ('~::n' when the module path equals the line above's); decode restores the 'test' and '...' wrappers and the path exactly, and doc-test names with spaces or top-level tests with no '::' stay verbatim.",
    encode=rt_enc,
    decode=rt_dec,
    fixtures=[
        Fixture("30 tests, default threads, one failing with a backtrace", RS_TEST, True),
        Fixture("same with --test-threads=1", RS_TEST_T1, True),
        Fixture("--report-time suffixes, three lines (too short to pay for the legend)", _RT_REPORT_TIME, None),
        Fixture("doc-tests: names contain spaces, top-level test has no path", RS_DOC, False),
        Fixture("lines that already look like the encoding", _RT_LOOKALIKE, None),
        *sigil_fixtures(_RT_MAGIC, RS_TEST),
    ],
    min_gain=0.1,
    idempotent=False,
)

# ===============================================================================================================
# #1409 Rust backtraces (RUST_BACKTRACE=1) print each frame as two lines, "  3: core::panicking::panic" and
# "             at /rustc/<40 hex>/library/core/src/panicking.rs:150:5". Join them as "  3: sym @ loc" and replace the
# toolchain prefix "/rustc/<hash>/" (repeated on every std frame) with §R/, the hash being in the legend once.

_RB_FRAME = re.compile(r"( *\d+: )(.*)")
_RB_AT = re.compile(r" {13}at (\S.*)")
_RB_HASH = re.compile(r"/rustc/([0-9a-f]{40})/")
_RB_MAGIC = "§rustc "


def _rb_enc(text: str):
    lines = text.split("\n")
    h = None
    for line in lines:
        a = _RB_AT.fullmatch(line)
        if a and (m := _RB_HASH.match(a.group(1))):
            h = m.group(1)
            break
    out = []
    i = 0
    changed = False
    while i < len(lines):
        f = _RB_FRAME.fullmatch(lines[i])
        locs = []
        j = i + 1
        while f and j < len(lines) and (a := _RB_AT.fullmatch(lines[j])):
            locs.append(a.group(1))
            j += 1
        if f and locs:
            pref = f"/rustc/{h}/" if h else None
            locs = ["§R/" + loc[len(pref):] if pref and loc.startswith(pref) else loc for loc in locs]
            out.append(f.group(1) + f.group(2) + "".join(" @ " + loc for loc in locs))
            i, changed = j, True
        else:
            out.append(lines[i])
            i += 1
    if not changed:
        return None
    return f'{h or "-"} = "§R" in paths; the "at" line of a frame is joined as " @ loc"\n' + "\n".join(out)


def _rb_dec(body: str) -> str:
    head, _, rest = body.partition("\n")
    h = head.split(" ", 1)[0]
    out = []
    for line in rest.split("\n"):
        f = _RB_FRAME.fullmatch(line)
        if f and " @ " in f.group(2):
            sym, *locs = f.group(2).split(" @ ")
            out.append(f.group(1) + sym)
            for loc in locs:
                out.append(" " * 13 + "at " + (f"/rustc/{h}/" + loc[3:] if loc.startswith("§R/") else loc))
        else:
            out.append(line)
    return "\n".join(out)


rb_enc, rb_dec = framed(_RB_MAGIC, _rb_enc, _rb_dec)

_RB_FULL = (
    "stack backtrace:\n   0:     0x5571842edf18 - std[bcb96dfbba0ab899]::backtrace_rs::backtrace::libunwind::trace\n"
    "                               at /rustc/2d8144b7880597b6e6d3dfd63a9a9efae3f533d3/library/std/src/../../backtrace/src/backtrace/libunwind.rs:117:9\n"
    "   5:     0x5571842feb0a - core[940e698046960fd5]::fmt::write\n"
)
_RB_INLINED = RS_PANIC.replace("             at ./src/main.rs:8:5\n", "             at ./src/main.rs:8:5\n             at ./src/main.rs:9:5\n")

case(
    1409,
    claim="Each RUST_BACKTRACE=1 frame '  N: symbol' plus its '             at path:line:col' line(s) becomes one line '  N: symbol @ path:line:col', with the repeated '/rustc/<40 hex>/' toolchain prefix written §R/ and the hash given once in the legend; decode splits the lines and restores the prefix byte for byte, and the RUST_BACKTRACE=full layout is left alone.",
    encode=rb_enc,
    decode=rb_dec,
    fixtures=[
        Fixture("cargo run: unwrap on None, 9 frames", RS_PANIC, True),
        Fixture("cargo test failure: 8 frames inside a test run", RS_TEST, True),
        Fixture("frame with two at lines (inlined)", _RB_INLINED, True),
        Fixture("RUST_BACKTRACE=full layout: addresses, different indent", _RB_FULL, False),
        Fixture("no backtrace at all", "running 1 test\ntest tests::it_works ... ok\n", False),
        *sigil_fixtures(_RB_MAGIC, RS_PANIC),
    ],
    min_gain=0.05,
    idempotent=False,
)

# ===============================================================================================================
# #1410 Maven prints "Downloading from central: URL" and then "Downloaded from central: URL (8.2 kB at 16 kB/s)": the URL is
# spelled twice, and it is itself BASE/group/path/artifact/version/artifact-version.ext, a function of the coordinates. An
# adjacent pair becomes one 'Downloaded' line carrying 'group:artifact:version@ext' (the legend gives BASE per repository id);
# a Downloading line with no Downloaded line right after it keeps its place, shortened the same way; a Downloaded line with
# no Downloading line right before it keeps its full URL, which is how the decoder tells the three cases apart.

_MV_DL = re.compile(r"(\[INFO\] )?Downloading from (\S+): (\S+)")
_MV_DD = re.compile(r"(\[INFO\] )?Downloaded from (\S+): (\S+) \((.+)\)")
_MV_BASE = re.compile(r"https?://[^/]+/(?:(?:maven2|m2|repository|release|releases|snapshot|snapshots|milestone|public|repo|repo1|maven)/)?")
_MV_COORD = re.compile(r"([^:@/\s]+):([^:@/\s]+):([^:@/\s]+)(?::([^:@/\s]+))?@(\S+)")
_MV_MAGIC = "§mvn "


def _mv_coord(url: str, base: str):
    if not url.startswith(base):
        return None
    segs = url[len(base):].split("/")
    if len(segs) < 4 or "" in segs:
        return None
    *g, a, v, f = segs
    if any("." in s for s in g) or not f.startswith(f"{a}-{v}"):
        return None
    suf = f[len(a) + 1 + len(v):]
    if suf.startswith("."):
        cls, ext = None, suf[1:]
    elif suf.startswith("-") and "." in suf:
        cls, _, ext = suf[1:].partition(".")
    else:
        return None
    if not ext or not cls and cls is not None:
        return None
    return ".".join(g) + f":{a}:{v}" + (f":{cls}" if cls else "") + f"@{ext}"


def _mv_url(base: str, coord: str) -> str:
    g, a, v, cls, ext = _MV_COORD.fullmatch(coord).groups()
    return f"{base}{g.replace('.', '/')}/{a}/{v}/{a}-{v}" + (f"-{cls}" if cls else "") + f".{ext}"


def _mv_enc(text: str):
    lines = text.split("\n")
    bases: dict[str, str] = {}
    for line in lines:
        m = _MV_DL.fullmatch(line) or _MV_DD.fullmatch(line)
        if m and m.group(2) not in bases and (b := _MV_BASE.match(m.group(3))):
            bases[m.group(2)] = b.group(0)
    out = []
    i = 0
    changed = False
    while i < len(lines):
        dl = _MV_DL.fullmatch(lines[i])
        if dl and (base := bases.get(dl.group(2))) and (coord := _mv_coord(dl.group(3), base)):
            pfx, repo = dl.group(1) or "", dl.group(2)
            dd = _MV_DD.fullmatch(lines[i + 1]) if i + 1 < len(lines) else None
            if dd and (dd.group(1) or "") == pfx and dd.group(2) == repo and dd.group(3) == dl.group(3):
                out.append(f"{pfx}Downloaded from {repo}: {coord} ({dd.group(4)})")
                i += 2
            else:
                out.append(f"{pfx}Downloading from {repo}: {coord}")
                i += 1
            changed = True
            continue
        out.append(lines[i])
        i += 1
    if not changed:
        return None
    legend = " ".join(f"{r}={b}" for r, b in bases.items())
    return legend + ' (g:a:v[:classifier]@ext = BASE/g/a/v/a-v[-classifier].ext; a Downloaded line also stands for the Downloading line before it)\n' + "\n".join(out)


def _mv_dec(body: str) -> str:
    head, _, rest = body.partition("\n")
    bases = dict(tok.split("=", 1) for tok in head.split(" (", 1)[0].split(" "))
    out = []
    for line in rest.split("\n"):
        if (dd := _MV_DD.fullmatch(line)) and "/" not in dd.group(3):
            pfx, repo = dd.group(1) or "", dd.group(2)
            url = _mv_url(bases[repo], dd.group(3))
            out.append(f"{pfx}Downloading from {repo}: {url}")
            out.append(f"{pfx}Downloaded from {repo}: {url} ({dd.group(4)})")
        elif (dl := _MV_DL.fullmatch(line)) and "/" not in dl.group(3):
            out.append(f"{dl.group(1) or ''}Downloading from {dl.group(2)}: {_mv_url(bases[dl.group(2)], dl.group(3))}")
        else:
            out.append(line)
    return "\n".join(out)


mv_enc, mv_dec = framed(_MV_MAGIC, _mv_enc, _mv_dec)

_MV_OTHER_REPO = (
    "Downloading from spring-milestones: https://repo.spring.io/milestone/org/springframework/spring-core/6.2.0-M1/spring-core-6.2.0-M1.jar\n"
    "Downloaded from spring-milestones: https://repo.spring.io/milestone/org/springframework/spring-core/6.2.0-M1/spring-core-6.2.0-M1.jar (1.9 MB at 4.0 MB/s)\n"
    "Downloading from central: https://repo.maven.apache.org/maven2/io/netty/netty-transport-native-epoll/4.1.100.Final/netty-transport-native-epoll-4.1.100.Final-linux-x86_64.jar\n"
    "Downloaded from central: https://repo.maven.apache.org/maven2/io/netty/netty-transport-native-epoll/4.1.100.Final/netty-transport-native-epoll-4.1.100.Final-linux-x86_64.jar (38 kB at 600 kB/s)\n"
)
_MV_ODD_URLS = (
    "[INFO] Downloading from central: https://repo.maven.apache.org/maven2/org/foo/bar/1.0-SNAPSHOT/bar-1.0-20240101.120000-1.jar\n"
    "[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/org/foo/bar/1.0-SNAPSHOT/bar-1.0-20240101.120000-1.jar (5 kB at 9 kB/s)\n"
    "[INFO] Downloading from central: https://repo.maven.apache.org/maven2/a.b/c/1/c-1.pom\n"
    "[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/a.b/c/1/c-1.pom (1 kB at 2 kB/s)\n"
)
_MV_NO_PREFIX = "".join(l[len("[INFO] "):] + "\n" for l in MVN_DL.split("\n")[6:24] if l.startswith("[INFO] Download"))  # maven 3.8 prints no [INFO] on these lines
_MV_LOOKALIKE = MVN_DL + "[INFO] Downloaded from central: org.foo:bar:1.0@jar (1 kB at 2 kB/s)\n"

case(
    1410,
    claim="Maven 'Downloading from R: URL' + 'Downloaded from R: URL (size at speed)' pairs become one 'Downloaded' line with 'group:artifact:version@ext' coordinates (BASE per repository id in the legend); a lone Downloading line is shortened in place, a lone Downloaded line and URLs that do not follow BASE/group/artifact/version/artifact-version.ext stay verbatim, and decode restores every URL and both lines exactly.",
    encode=mv_enc,
    decode=mv_dec,
    fixtures=[
        Fixture("first build: sequential pairs, then a parallel batch (two slices of one real run)", MVN_DL, True),
        Fixture("two repositories, a classifier artifact", _MV_OTHER_REPO, True),
        Fixture("no [INFO] prefix (older Maven)", _MV_NO_PREFIX, True),
        Fixture("timestamped snapshot and dotted directory: not coordinate-shaped", _MV_ODD_URLS, False),
        Fixture("surefire run without any download", MVN_SUREFIRE, False),
        Fixture("CRLF capture", MVN_DL.replace("\n", "\r\n"), None),
        Fixture("input that already contains a coordinate-form line", _MV_LOOKALIKE, None),
        *sigil_fixtures(_MV_MAGIC, MVN_DL[:900]),
    ],
    min_gain=0.3,
    idempotent=False,
)

# ===============================================================================================================
# #1411 node --test prints, for every passing test, six lines (with the tap reporter, the default when stdout is not a
# terminal on node 22): '# Subtest: NAME', 'ok N - NAME', '  ---', '  duration_ms: X', "  type: 'test'", '  ...'.
# The Subtest line repeats the name and the YAML block is three constant lines around the duration.

_TAP_MAGIC = '§tap: a passing test block (# Subtest, ok, --- duration_ms type ...) folded to "ok N - name (X ms)"\n'
_TAP_ENC = re.compile(r"( *)ok (\d+) - (.*) \((\S+) ms\)")
_TAP_SUB = re.compile(r"( *)# Subtest: (.*)")
_TAP_DUR = re.compile(r" *  duration_ms: (\S+)")


def _tap_enc(text: str):
    lines = text.split("\n")
    out = []
    i = 0
    changed = False
    while i < len(lines):
        s = _TAP_SUB.fullmatch(lines[i])
        if s and i + 5 < len(lines):
            ind, name = s.group(1), s.group(2)
            ok = re.fullmatch(re.escape(ind) + r"ok (\d+) - " + re.escape(name), lines[i + 1])
            dur = _TAP_DUR.fullmatch(lines[i + 3])
            if (ok and dur and lines[i + 2] == ind + "  ---" and lines[i + 3].startswith(ind + "  duration_ms: ")
                    and lines[i + 4] == ind + "  type: 'test'" and lines[i + 5] == ind + "  ..."):
                out.append(f"{ind}ok {ok.group(1)} - {name} ({dur.group(1)} ms)")
                i += 6
                changed = True
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) if changed else None


def _tap_dec(body: str) -> str:
    out = []
    for line in body.split("\n"):
        if m := _TAP_ENC.fullmatch(line):
            ind, n, name, dur = m.groups()
            out += [f"{ind}# Subtest: {name}", f"{ind}ok {n} - {name}", f"{ind}  ---", f"{ind}  duration_ms: {dur}",
                    f"{ind}  type: 'test'", f"{ind}  ..."]
        else:
            out.append(line)
    return "\n".join(out)


tap_enc, tap_dec = framed(_TAP_MAGIC, _tap_enc, _tap_dec)

_TAP_NAME_DIFFERS = TAP_B.replace("ok 2 - get miss", "ok 2 - get miss!", 1)
_TAP_NAME_WITH_MS = "# Subtest: x (5 ms)\nok 1 - x (5 ms)\n  ---\n  duration_ms: 5\n  type: 'test'\n  ...\n"
_TAP_SPEC_LOOKALIKE = TAP_B + "ok 99 - something (4 ms)\n"

case(
    1411,
    claim="A passing node:test TAP block of six lines ('# Subtest: N', 'ok k - N', '---', 'duration_ms: X', \"type: 'test'\", '...') becomes 'ok k - N (X ms)'; decode rebuilds the six lines at the same indent exactly, and failing tests, skips, suites and non-TAP reporters keep their full text.",
    encode=tap_enc,
    decode=tap_dec,
    fixtures=[
        Fixture("18 passing tests in a suite and at top level", TAP_B, True),
        Fixture("failure, skip, suite and async test mixed", TAP_A, True),
        Fixture("spec reporter is not TAP", NODE_SPEC, False),
        Fixture("CRLF capture", TAP_B.replace("\n", "\r\n"), None),
        Fixture("a '(N ms)' suffix in the test name and in a folded-looking line", _TAP_NAME_WITH_MS, None),
        Fixture("folded-looking line next to real blocks", _TAP_SPEC_LOOKALIKE, None),
        Fixture("one block whose ok name differs from its Subtest name stays unfolded", _TAP_NAME_DIFFERS, True),
        *sigil_fixtures(_TAP_MAGIC, TAP_B),
    ],
    min_gain=0.1,
    idempotent=False,
)

# ===============================================================================================================
# #1412 pip install on an environment that already has the packages prints one very long line per requirement:
# 'Requirement already satisfied: SPEC in LOCATION (from PARENT) (VERSION)'. Consecutive lines mostly share the location and
# often the parent. A run of two or more becomes a header 'Requirement already satisfied (n):' and n entries
# '  SPEC VERSION', with 'in LOCATION' and '(from PARENT)' written only where they change from the entry above (the first entry
# always carries both; '(from)' means the requirement had no parent).

_PIP_LINE = re.compile(r"Requirement already satisfied: (?P<spec>.+?) in (?P<loc>\S+)(?: \(from (?P<par>.+)\))? \((?P<ver>[^()\s]+)\)")
_PIP_HEAD = re.compile(r"Requirement already satisfied \((\d+)\):")
_PIP_ENTRY = re.compile(r"  (?P<spec>.+?) (?P<ver>[^\s()]+)(?: in (?P<loc>\S+))?(?P<tag> \(from(?: (?P<par>.+))?\))?")
_PIP_MAGIC = '§pip: runs of "Requirement already satisfied" grouped; entry "  spec version", "in location" and "(from parent)" only when they change\n'
_NONE = object()


def _pip_enc(text: str):
    lines = text.split("\n")
    out = []
    i = 0
    changed = False
    while i < len(lines):
        j = i
        run = []
        while j < len(lines) and (m := _PIP_LINE.fullmatch(lines[j])):
            run.append(m)
            j += 1
        if len(run) >= 2:
            out.append(f"Requirement already satisfied ({len(run)}):")
            loc = par = _NONE
            for r in run:
                tags = ""
                if r.group("loc") != loc:
                    loc = r.group("loc")
                    tags += f" in {loc}"
                if r.group("par") != par or par is _NONE:
                    par = r.group("par")
                    tags += f" (from {par})" if par is not None else " (from)"
                out.append(f"  {r.group('spec')} {r.group('ver')}{tags}")
            i, changed = j, True
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) if changed else None


def _pip_dec(body: str) -> str:
    lines = body.split("\n")
    out = []
    i = 0
    while i < len(lines):
        h = _PIP_HEAD.fullmatch(lines[i])
        if not h:
            out.append(lines[i])
            i += 1
            continue
        n = int(h.group(1))
        loc = par = None
        for e in lines[i + 1: i + 1 + n]:
            m = _PIP_ENTRY.fullmatch(e)
            if m.group("loc") is not None:
                loc = m.group("loc")
            if m.group("tag") is not None:  # explicit parent; '(from)' means the requirement had none
                par = m.group("par")
            frm = f" (from {par})" if par is not None else ""
            out.append(f"Requirement already satisfied: {m.group('spec')} in {loc}{frm} ({m.group('ver')})")
        i += 1 + n
    return "\n".join(out)


pip_enc, pip_dec = framed(_PIP_MAGIC, _pip_enc, _pip_dec)

_PIP_LISTING = "Package                   Version\n------------------------- ---------------\nannotated-types           0.8.0\nanyio                     4.15.1\n"
_PIP_SINGLE = "Requirement already satisfied: pip in /usr/lib/python3/dist-packages (24.0)\nSuccessfully installed nothing\n"
_PIP_NOPARENT_RUN = "".join(f"Requirement already satisfied: {n} in /usr/lib/python3/dist-packages ({v})\n" for n, v in
                           [("pip", "24.0"), ("setuptools", "68.1.2"), ("wheel", "0.42.0"), ("packaging", "24.0")])
_PIP_MARKER = ("Requirement already satisfied: colorama; sys_platform == \"win32\" in /usr/lib/python3/dist-packages (from pytest) (0.4.6)\n"
               "Requirement already satisfied: exceptiongroup>=1 in /usr/lib/python3/dist-packages (from pytest) (1.2.0)\n")
_PIP_LOOKALIKE = "Requirement already satisfied (2):\n  a 1 in /x/y (from)\n  b 2\n" + "".join(PIP_SAT.split("\n")[:3])

case(
    1412,
    claim="Runs of two or more 'Requirement already satisfied: SPEC in LOCATION (from PARENT) (VERSION)' lines become a header with the location and count plus one '  SPEC VERSION' entry each, '(from PARENT)' written only when the parent changes; decode rebuilds every original line byte for byte, single lines and other pip output stay verbatim.",
    encode=pip_enc,
    decode=pip_dec,
    fixtures=[
        Fixture("pip install -r on an installed environment (80 lines)", PIP_SAT, True),
        Fixture("run with no parent (pip install pkg), then a different parent", _PIP_NOPARENT_RUN + PIP_SAT.split("\n")[3] + "\n", True),
        Fixture("requirement with an environment marker (spaces in the spec), two lines", _PIP_MARKER, None),
        Fixture("pip list table", _PIP_LISTING, False),
        Fixture("a single satisfied line", _PIP_SINGLE, False),
        Fixture("text that already contains a group header", _PIP_LOOKALIKE, None),
        *sigil_fixtures(_PIP_MAGIC, "".join(PIP_SAT.split("\n")[:12])),
    ],
    min_gain=0.1,
    idempotent=False,
)

# ===============================================================================================================
# #1413 surefire prints, per test class, '[INFO] Running X' and then '[LEVEL] Tests run: R, Failures: F, Errors: E, Skipped: S,
# Time elapsed: T s[ <<< FAILURE!] -- in X'. The Running line repeats the class that ends the next line and the five labels are
# the same on every class. The pair becomes one row '[LEVEL] R F E S Ts X[ <<< FAILURE!]' and the legend names the columns.

_SF_RUN = re.compile(r"\[INFO\] Running (\S+)")
_SF_RES = re.compile(r"\[(INFO|WARNING|ERROR)\] Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+), Time elapsed: ([\d.]+) s((?: <<< [A-Z]+!)?) -- in (\S+)")
_SF_ROW = re.compile(r"\[(INFO|WARNING|ERROR)\] (\d+) (\d+) (\d+) (\d+) ([\d.]+)s (\S+)((?: <<< [A-Z]+!)?)")
_SF_MAGIC = '§surefire: each "[INFO] Running X" + "Tests run: ... -- in X" pair is one row "[LEVEL] run fail err skip time X [<<< FLAG!]"\n'


def _sf_enc(text: str):
    lines = text.split("\n")
    out = []
    i = 0
    changed = False
    while i < len(lines):
        run = _SF_RUN.fullmatch(lines[i])
        res = _SF_RES.fullmatch(lines[i + 1]) if run and i + 1 < len(lines) else None
        if run and res and res.group(8) == run.group(1):
            lvl, r, f, e, s, t, flag, cls = res.groups()
            out.append(f"[{lvl}] {r} {f} {e} {s} {t}s {cls}{flag}")
            i += 2
            changed = True
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) if changed else None


def _sf_dec(body: str) -> str:
    out = []
    for line in body.split("\n"):
        if m := _SF_ROW.fullmatch(line):
            lvl, r, f, e, s, t, cls, flag = m.groups()
            out.append(f"[INFO] Running {cls}")
            out.append(f"[{lvl}] Tests run: {r}, Failures: {f}, Errors: {e}, Skipped: {s}, Time elapsed: {t} s{flag} -- in {cls}")
        else:
            out.append(line)
    return "\n".join(out)


sf_enc, sf_dec = framed(_SF_MAGIC, _sf_enc, _sf_dec)

_SF_JUNIT4 = "Running com.acme.FooTest\nTests run: 3, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.05 sec - in com.acme.FooTest\n"  # surefire 2.x layout
_SF_OUTPUT_BETWEEN = ("[INFO] Running a.FooTest\nsome stdout from the test\n[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.006 s -- in a.FooTest\n"
                      "[INFO] Running a.BarTest\n[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.016 s -- in a.OtherTest\n")
_SF_ROWLIKE = MVN_SUREFIRE + "[INFO] 9 0 0 0 0.1s a.Fake\n"

case(
    1413,
    claim="A surefire '[INFO] Running X' line plus the '[LEVEL] Tests run: R, Failures: F, Errors: E, Skipped: S, Time elapsed: T s [<<< FLAG!] -- in X' line right after it become one row '[LEVEL] R F E S Ts X [<<< FLAG!]'; decode rebuilds both lines exactly, and pairs with output in between, a different class name or the surefire 2.x layout stay verbatim.",
    encode=sf_enc,
    decode=sf_dec,
    fixtures=[
        Fixture("7 classes, one failure with stack trace, one skip", MVN_SUREFIRE, True),
        Fixture("single class (one row: the legend costs more than it saves)", "[INFO] Running a.FooTest\n[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.049 s -- in a.FooTest\n", None),
        Fixture("surefire 2.x layout (no [INFO], ' sec - in')", _SF_JUNIT4, False),
        Fixture("output between Running and Tests run, and a class name mismatch", _SF_OUTPUT_BETWEEN, None),
        Fixture("a line that already looks like a row", _SF_ROWLIKE, None),
        *sigil_fixtures(_SF_MAGIC, MVN_SUREFIRE[:700]),
    ],
    min_gain=0.1,
    idempotent=False,
)
