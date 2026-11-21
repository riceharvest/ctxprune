import json,glob,sys,collections
import tiktoken
enc=tiktoken.get_encoding('o200k_base')
T=lambda s:len(enc.encode(s))
def outs(path):
    m=json.load(open(path)); r=[]
    for x in m:
        if x.get('type')=='function_call_output' or ('output' in x and 'call_id' in x and 'arguments' not in x):
            o=x['output']
            try:
                j=json.loads(o)
                if isinstance(j,dict) and 'text' in j: o=j['text']
            except Exception: pass
            r.append(o)
    return r
def walk(n,acc):
    if isinstance(n,dict):
        acc.append(n)
        for v in n.values(): walk(v,acc)
    elif isinstance(n,list):
        for v in n: walk(v,acc)
def screen(texts,minc=60,refcost=8):
    seen_txt=set(); seen={}; tot=0; save=0; n_ent=0
    for ci,t in enumerate(texts):
        if len(t)<=50000: tot+=T(t)
        try: j=json.loads(t)
        except Exception: continue
        acc=[]; walk(j,acc)
        # outermost-first: skip children of replaced
        cur={}; covered=set()
        def go(n):
            global save
            return
        # recursive with replacement
        def rec(n):
            nonlocal save,n_ent
            if isinstance(n,dict):
                s=json.dumps(n,separators=(',',':'),sort_keys=True)
                if len(s)>=minc and len(n)>=2:
                    if s in seen and seen[s]<ci:
                        c=T(s)-refcost
                        if c>0: save+=c; n_ent+=1; return
                    cur.setdefault(s,ci)
                for v in n.values(): rec(v)
            elif isinstance(n,list):
                for v in n: rec(v)
        if t in seen_txt or len(t)>50000: continue_flag=True
        else: continue_flag=False
        seen_txt.add(t)
        if not continue_flag:
            for v in (j.values() if isinstance(j,dict) else j if isinstance(j,list) else []): rec(v)
        for s,c in cur.items(): seen.setdefault(s,c)
    return tot,save,n_ent
if __name__=='__main__':
    for name,pat in [('github','mcpmark/*/claude-*__github/run-1/*/messages.json'),('notion','mcpmark/*/claude-*__notion/run-1/*/messages.json')]:
        files=glob.glob(sys.argv[1]+'/'+pat); T0=S0=0; per=[]
        for f in files:
            o=outs(f)
            if len(o)<2: continue
            t,s,n=screen(o); T0+=t;S0+=s; per.append(s/t if t else 0)
        per.sort()
        print(name,len(per),'sessions tot',T0,'saved',S0,'%.2f%%'%(100*S0/max(T0,1)),'median/sess %.2f%% p90 %.2f%% max %.2f%%'%(100*per[len(per)//2],100*per[int(len(per)*.9)],100*per[-1]), 'sessions>=5%%: %d'%sum(p>=.05 for p in per))
