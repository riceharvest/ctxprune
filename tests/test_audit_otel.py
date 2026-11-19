"""ctxprune audit --otel / --share: priced totals match logged usage, output is numbers only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ctx_audit as ca  # noqa: E402


def _log(tmp_path, gaps):
    rows, t = [], 1_800_000_000
    for k, gap in enumerate(gaps):
        t += gap
        rows.append({"attributes": {"event.name": "claude_code.api_request", "session.id": "SECRET-SESSION",
                                    "input_tokens": 10, "output_tokens": 200, "cache_read_tokens": 0 if k == 0 else 50000,
                                    "cache_creation_tokens": 50000 if k == 0 else 1000},
                     "timeUnixNano": str(t * 10**9)})
    rows.append({"attributes": {"event.name": "claude_code.user_prompt"}})   # ignored
    p = tmp_path / "otel.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_otel_bill_matches_usage(tmp_path):
    p = _log(tmp_path, [0, 60, 400, 2000])
    recs = ca.otel_records(str(p))
    txt, blob = ca.otel_report(recs)
    exp = sum((10 * 4 + o * 20 + r * 0.2 + w * 5) / 1e6 for o, r, w in
              [(200, 0, 50000)] + [(200, 50000, 1000)] * 3)
    assert abs(blob["bill_usd"] - exp) <= 0.03 * exp
    assert blob["requests"] == 4 and blob["sessions"] == 1
    assert "SECRET-SESSION" not in txt + json.dumps(blob)
    assert blob["ttl"]["main"]["best"] in ("5m", "1h")


def test_otel_long_gaps_prefer_1h(tmp_path):
    p = _log(tmp_path, [0, 1000, 1000, 1000, 1000])    # every gap 300..3600 s: 5m TTL would rewrite
    _, blob = ca.otel_report(ca.otel_records(str(p)))
    assert blob["ttl"]["main"]["best"] == "5m" or blob["ttl"]["main"]["usd"]["1h"] <= blob["ttl"]["main"]["usd"]["5m"]


def test_cli_otel(tmp_path):
    p = _log(tmp_path, [0, 60])
    r = subprocess.run([sys.executable, "-m", "ctxprune", "audit", "--otel", str(p), "--share"], capture_output=True,
                       text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert "api_request events" in r.stdout and "ctxprune-audit-share/1" in r.stdout
