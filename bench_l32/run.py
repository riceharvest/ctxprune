import sys, glob, time, re, os, random
sys.path.insert(0, "src")
from ctxprune.logs import compact_logs, _is_alarm
files = sorted(set(glob.glob("/tmp/*.log")+glob.glob(os.path.expanduser("~/ctxprune-v2/logs/*.log"))+glob.glob(os.path.expanduser("~/ctxprune-v2/logs*.out"))))
random.seed(0); random.shuffle(files)
rows=[]
for f in files:
    try: t=open(f,errors="replace").read()
    except: continue
    if t.count("\n")<=50 or len(t)>3_000_000: continue
    t0=time.perf_counter(); o=compact_logs(t); dt=time.perf_counter()-t0
    n=t.count("\n")+1
    ol=set(o.splitlines())
    errs=[l for l in t.splitlines() if _is_alarm(l)]
    lost=sum(1 for l in errs if l not in ol)
    rows.append((os.path.basename(f),n,len(t),len(o),lost,len(errs),dt/n*5000*1000))
    if len(rows)==30: break
tot=sum(r[2] for r in rows); to=sum(r[3] for r in rows)
for r in rows: print("%-40s lines=%6d sav=%5.1f%% errlost=%d/%d ms/5k=%.1f"%(r[0],r[1],100*(1-r[3]/r[2]),r[4],r[5],r[6]))
print("N",len(rows),"mean sav %.1f%%"%(sum(100*(1-r[3]/r[2]) for r in rows)/len(rows)),"agg %.1f%%"%(100*(1-to/tot)),"errlost",sum(r[4] for r in rows),"max ms/5k %.1f"%max(r[6] for r in rows))
