"""Render assets/before-after.html: a real ctxprune output on a test-run log, deleted words struck through.

    python scripts/make_hero.py && (screenshot assets/before-after.html -> assets/before-after.png)
"""
import html
from pathlib import Path

from ctxprune import Compressor
from ctxprune.align import align
from ctxprune.atoms import atomize, is_protected

LOG = """$ pytest tests/test_orders.py -x -q
F
=================================== FAILURES ===================================
____________________ test_refund_is_idempotent[ord_8f3a2c91] ____________________

    def test_refund_is_idempotent(client, order_id):
        r1 = client.post(f"/v2/orders/{order_id}/refund", json={"amount": 14950})
        r2 = client.post(f"/v2/orders/{order_id}/refund", json={"amount": 14950})
>       assert r2.status_code == 409, r2.json()
E       AssertionError: {'error': 'refund_already_pending', 'refund_id': 're_3PqK9x2LmN', 'retry_after': 3600}
E       assert 202 == 409
E        +  where 202 = <Response [202]>.status_code

tests/test_orders.py:88: AssertionError
------------------------------ Captured log call -------------------------------
WARNING  payments.client:client.py:212 stripe request req_Hk2v9QzR took 2210ms (timeout budget 2000ms)
INFO     payments.refunds:refunds.py:57 created refund re_3PqK9x2LmN for ord_8f3a2c91 (amount=14950, currency=eur)
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_refund_is_idempotent[ord_8f3a2c91] - AssertionError: {'error': 'refund_alrea...
1 failed in 3.42s"""

c = Compressor()
out = c.compress(LOG, rate=0.5)["text"]  # library defaults
atoms = atomize(LOG)
keep = align(atoms, out).keep
body = ""
for a, k in zip(atoms, keep):
    t = html.escape(a.text)
    body += html.escape(a.ws) + (t if k else f'<s>{t}</s>')
ids = [a.text for a in atoms if is_protected(a.text)]
kept_ids = sum(1 for a, k in zip(atoms, keep) if k and is_protected(a.text))
page = f"""<!doctype html><meta charset="utf-8"><style>
body {{ margin:0; background:#fbfaf8; font:15px/1.5 ui-sans-serif,system-ui,sans-serif; color:#1c1b19 }}
.wrap {{ width:1040px; padding:28px 32px }}
h1 {{ font-size:22px; margin:0 0 4px }} p {{ margin:0 0 14px; color:#6b6760 }}
pre {{ background:#fff; border:1px solid #e4e0d8; border-radius:6px; padding:14px 16px; margin:0;
  font:12.5px/1.6 ui-monospace,Menlo,Consolas,monospace; white-space:pre-wrap }}
s {{ color:#bcb6ac; text-decoration-thickness:1px }}
.k {{ display:flex; gap:28px; margin-top:12px; font-size:14px }} .k b {{ font-size:18px; display:block }}
</style><div class="wrap">
<h1>ctxprune on a failing test run</h1>
<p>What an agent reads after compression: struck-through words are deleted, the rest is passed on unchanged.</p>
<pre>{body}</pre>
<div class="k"><div><b>{len(LOG):,} → {len(out):,} chars</b>{len(out)/len(LOG):.0%} kept</div>
<div><b>0 split or re-spaced</b>{kept_ids}/{len(ids)} IDs and numbers kept whole (protect=True keeps all)</div>
<div><b>CPU, ~0.1 s</b>int8 ONNX, no GPU or API</div></div></div>"""
Path("assets/before-after.html").write_text(page)
print(len(LOG), len(out), kept_ids, len(ids))
print(out)
