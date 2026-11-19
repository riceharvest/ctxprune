from ctxprune.logs import compact_logs


def test_alarms_survive_and_bulk_collapses():
    L = ["INFO ok id=%d took %dms" % (i, 10 + i % 5) for i in range(300)]
    L[50] = "[ERROR]12:00 boom"
    L[60] = 'x GET /a" 503 1'
    L[70] = "process exited with exit code 2"
    o = compact_logs("\n".join(L) + "\n").splitlines()
    for k in (50, 60, 70):
        assert L[k] in o
    assert len(o) < 30


def test_short_input_untouched():
    assert compact_logs("a\n" * 10) == "a\n" * 10
