# T1.10b: cross-call MCP entity refs (opt-in rule, net savings, reader probe)

**Verdict: savings gate PASS, round trip PASS, reader probe FAIL (-24.3 pt, local 1.5B reader). Ship opt-in only with a warning; NO preset, not default.**

## Rule
`scripts/entityrefs.py` (`EntityRefs`, `expand`): a nested JSON object (>=40 compact chars, >=2 keys; min fixed on dev) that appeared byte-for-byte in an earlier output of the session becomes `{"$ref":"#K/json/pointer"}` (output #K, pointer of its first, unreplaced occurrence). One legend line (~32 o200k tokens) on the first output that uses a ref. Root never replaced; only used if the ref is shorter in o200k tokens; fail-open on pretty-printed / duplicate-key / `$ref`-bearing / >50k outputs. Not wired into DedupSession or any default path (default output unchanged; no existing file modified).
Tests: `python -I -m unittest discover -s scripts -p test_entityrefs.py` -> 5 tests OK (incl. 300 random round trips).

## Net savings (o200k, json_compact applied first by lossless.compress rows=off, exact-dup outputs skipped, legend counted)
Dev sweep of min_chars (dev only): 40 / 60 / 100 / 200 -> notion agg 20.77 / 20.68 / 11.93 / 9.63 %, github 9.05 / 9.05 / 9.05 / 8.41 %. Chose 40.
Val (command `lab-cpu -- /home/dario/ctxprune-v2/venv-enc/bin/python docs/ideas/results/145/net_savings.py --split val`, raw output `t110b_val.txt`):

| server | n | baseline tok | net saved | aggregate net | CI95 (session bootstrap) | median/session | legend tok | round-trip fails |
|---|--:|--:|--:|--:|---|--:|--:|--:|
| github | 23 | 351,989 | 36,175 | **10.28%** (gate >=3%) | [3.94, 18.31] | 0.00% | 320 | 0 |
| notion | 28 | 669,985 | 148,966 | **22.23%** (gate >=10%) | [18.15, 27.36] | 19.78% | 896 | 0 |

Legend-only control = the legend column (320 / 896 tokens, 0.09% / 0.13% of baseline). Round trip: every ref'd val output (125 github + 5150 notion refs) expanded byte-equal (`roundtrip_fail=0`). Github median is 0 because ref-bearing sessions are few; the gain is concentrated in list-heavy sessions.

## Reader probe (lab-gpu 3060, one job, ~20 min)
Items: `probe_gen.py` (val sessions only, seed 20261119, frozen before the run): value of a scalar field of a replaced object at a stated JSON pointer of output #i; context = outputs 1..i, <=12k o200k tokens. n=243 (notion 196, github 47) from 34 sessions. Reader: local Qwen2.5-1.5B-Instruct bf16, greedy, 48 new tokens, no tools. Command:
`~/smallest-ai-lab/bin/lab-gpu 3060 --need-gib 5 -- /mnt/ssd/mg-tmp/mvv-cuda/bin/python docs/ideas/results/145/probe_run.py --items docs/ideas/results/145/probe_items.jsonl --out docs/ideas/results/145/probe_answers.jsonl`

`n=243 sessions=34 raw_acc=61.7% enc_acc=37.4% delta=-24.3pt CI95(session bootstrap)=[-30.8,-17.9] MDE=8.8pt` (github 51.1% -> 38.3%, notion 64.3% -> 37.2%).
Gate (enc >= raw - 1 pt) FAILED, far beyond the MDE. Caveats: the questions target exactly the replaced content (worst case for refs), the reader is a 1.5B model (a strong reader may resolve pointers; untested, not claimed), the MDE is 8.8 pt, not the planned ~4.

## Claim
"Opt-in entity refs net 10.3% (github, CI 3.9-18.3) / 22.2% (notion, CI 18.2-27.4) more o200k tokens on MCPMark val sessions on top of json_compact, lossless round trip (0 fails); but a 1.5B local reader loses 24.3 pt (CI -30.8..-17.9) on questions about ref'd content, so: opt-in with a warning, not in any preset."

## Next (not done)
Re-run the probe with a stronger reader (e.g. 27B on b70 when its lock is free) and an id-style ref the reader can find without counting output numbers; wiring into `hook_session` deferred until a probe passes.

Branch `ab/t_6a814735`. No models downloaded (reader already on disk).
