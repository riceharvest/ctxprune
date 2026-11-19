import sys, json, glob, os, time
sys.path.insert(0,'.'); import harness
harness.discover(None)
IDS=[1424,325,520,130,519,412,1326,323,1429]
texts=[]  # (src,text)
for l in open(os.path.expanduser('~/ctxprune-v2/private/agent_samples.jsonl')):
    d=json.loads(l); texts.append(('agent_samples',d['text']))
for l in open(os.path.expanduser('~/ctxprune-v2/private/local_eval.jsonl')):
    d=json.loads(l)
    for k in ('output','text','result'):
        if isinstance(d.get(k),str): texts.append(('local_eval',d[k])); break

import random
for f in glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl')):
    try:
        for l in open(f):
            if '"tool_result"' not in l: continue
            d=json.loads(l); m=d.get('message',{}); c=m.get('content') if isinstance(m,dict) else None
            if not isinstance(c,list): continue
            for b in c:
                if isinstance(b,dict) and b.get('type')=='tool_result':
                    x=b.get('content')
                    if isinstance(x,list): x=''.join(p.get('text','') for p in x if isinstance(p,dict))
                    if isinstance(x,str) and len(x)>200: texts.append(('cc',x))
    except Exception: pass
print(len(texts), 'outputs', file=sys.stderr)
tot=sum(len(t) for _,t in texts)
res={}
for i in IDS:
    c=harness.REGISTRY[i]; fired=0;saved=0;fail=0;err=0;ex=[];t0=time.time()
    for s,t in texts:
        if len(t)>400000: continue
        try: e=c.encode(t)
        except Exception: err+=1; continue
        if e!=t and len(e)<len(t):
            fired+=1; saved+=len(t)-len(e)
            if c.decode:
                try: ok=c.decode(e)==t
                except Exception: ok=False
            elif c.equivalent: ok=c.equivalent(t,e)
            else: ok=True
            if not ok: fail+=1
            ex.append((len(t),len(e),s))
    res[i]=dict(fired=fired,saved_chars=saved,pct_total=round(100*saved/tot,4),fails=fail,errors=err,secs=round(time.time()-t0,1),top=sorted(ex,reverse=True)[:3])
    print(i,res[i],flush=True)
json.dump(res,open('/tmp/t111/screen_cc.json','w'),indent=1)
