# L3.2 Drain-style log-template miner — result

Verdict: **PARTIAL / kill gate FAILED on latency**; savings and retention pass. Not wired into the public API.

- Code: `src/ctxprune/logs.py` (`compact_logs(text)`, ~110 lines, stdlib only). Bench: `bench_l32/synth.py`, `bench_l32/run.py`.
- Savings: 30 synthetic repetitive 5k-line logs (6 formats, injected ERROR/WARN/FATAL/5xx/Traceback + silent anomaly): mean 99.9%, 0 lost.
  30 real unknown logs in /tmp and ~/ctxprune-v2/logs (many are small, non-repetitive eval logs): mean 46.0%, aggregate 58.5%, 0 alarm lines lost (alarms = keyword/4xx-5xx/exit code).
- Latency: ~70-130 ms per 5k lines (process_time, best of 9) on a machine at load ~54 on 32 cores; idle likely 2-3x lower, still well above the 15 ms gate.
  Cost is the regex alarm scans (keyword scan done once per distinct digit-normalised line; the 4xx/5xx scan is ~25 ms alone), not the Drain tree (<1 ms with exact-line fast path).
  Pure-Python regex over 5k lines cannot reach 3 us/line; would need a cheaper alarm test (e.g. token-set lookup) at some recall risk.
- Lossy: output is a display digest ("[template xN | #p=lo..hi]"), use only where raw log is kept.
- Distribution: would be used by the Claude Code/Hermes/Pi tool-output hooks (existing lossless-hook seam) for >50-line Bash output, but only as an opt-in lossy mode; do not ship until latency gate is met or relaxed.
