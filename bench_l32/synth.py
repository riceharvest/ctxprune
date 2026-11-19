"""30 synthetic repetitive logs (6 formats x 5 seeds, 5000 lines) with injected anomalies."""
import sys, random, time
sys.path.insert(0, "src")
from ctxprune.logs import compact_logs, _is_alarm
def nginx(r,i): return f'10.1.{r.randint(0,9)}.{r.randint(1,250)} - - [19/Nov/2026:10:{i//60%60:02d}:{i%60:02d} +0000] "GET /api/v1/items/{r.randint(1,9999)} HTTP/1.1" 200 {r.randint(200,900)} "-" "curl/8.1"'
def app(r,i): return f'2026-11-19 10:{i//60%60:02d}:{i%60:02d},{r.randint(100,999)} INFO  [worker-{r.randint(1,8)}] OrderService - processed order id={r.randint(10000,99999)} in {r.randint(8,40)}ms user=u{r.randint(1,500)}'
def ci(r,i): return f'[{i:05d}] PASS tests/test_mod{r.randint(1,40)}.py::test_case_{r.randint(1,30)} ({r.randint(1,90)} ms)'
def k8s(r,i): return f'I1119 10:{i//60%60:02d}:{i%60:02d}.{r.randint(100000,999999)}  {r.randint(1,9)} reconciler.go:{r.randint(100,300)}] reconciled pod default/web-{r.randint(1000,9999)} generation={r.randint(1,20)}'
def sysl(r,i): return f'Nov 19 10:{i//60%60:02d}:{i%60:02d} host{r.randint(1,4)} sshd[{r.randint(1000,9999)}]: Accepted publickey for deploy from 192.168.{r.randint(0,3)}.{r.randint(1,250)} port {r.randint(20000,60000)} ssh2'
def metr(r,i): return f'ts={1700000000+i} svc=cache op=get hit=true latency_us={r.randint(80,160)} bytes={r.randint(100,300)}'
FM=[nginx,app,ci,k8s,sysl,metr]
BAD=['{} ERROR [worker-3] OrderService - payment gateway unreachable: Connection refused', 'WARN disk usage at 91% on /var', 'Traceback (most recent call last):', 'FATAL: out of memory, killed process 4412',
     'x - - "POST /api/v1/pay HTTP/1.1" 503 12', 'x - - "GET /login HTTP/1.1" 404 0', 'FAILED tests/test_mod3.py::test_case_2 - AssertionError', 'E1119 reconciler.go:88] failed to patch pod: timeout']
rows=[]
for fi,f in enumerate(FM):
  for seed in range(5):
    r=random.Random(seed*10+fi); L=[f(r,i) for i in range(5000)]
    inj=[]
    for k in range(r.randint(3,8)):
        pos=r.randrange(5,4995); b=r.choice(BAD); L[pos]=b; inj.append(b)
    # silent anomaly: unusual spike / unseen line
    L[r.randrange(5,4995)]="service restarted unexpectedly after config reload by operator"
    t="\n".join(L)+"\n"
    t0=time.perf_counter(); o=compact_logs(t); dt=(time.perf_counter()-t0)*1000
    ol=set(o.splitlines()); lost=sum(1 for l in L if (_is_alarm(l) or l.startswith("service restarted")) and l not in ol)
    rows.append((f.__name__,seed,100*(1-len(o)/len(t)),lost,dt))
for r in rows: print("%-6s s%d sav=%5.1f%% lost=%d ms=%.1f"%r)
print("N",len(rows),"mean sav %.1f%%"%(sum(r[2] for r in rows)/len(rows)),"min %.1f%%"%min(r[2] for r in rows),"lost",sum(r[3] for r in rows),"max ms/5k %.1f"%max(r[4] for r in rows))
