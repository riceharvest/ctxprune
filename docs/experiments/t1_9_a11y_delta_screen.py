import glob,json,re,difflib,sys
from pathlib import Path
R=Path.home()/".cache/ctxprune-typical/raw/mcpmark/mcpmark-v1-0905"
def txt(out):
    try: o=json.loads(out) if isinstance(out,str) else out
    except ValueError: return out if isinstance(out,str) else ""
    def w(x):
        if isinstance(x,str): return x
        if isinstance(x,dict): return x.get("text") or "".join(w(v) for v in x.values())
        if isinstance(x,list): return "\n".join(w(v) for v in x)
        return ""
    return w(o)
tot=dict(full=0,exact=0,delta=0,snaps=0,first=0,reset=0,runs=0)
refre=re.compile(r"\[ref=(e\d+)\]")
def toks(s): return len(s)/3.5
rows=[]
for d in sorted(glob.glob(str(R/"*playwright*/run-1/*/messages.json"))):
    msgs=json.load(open(d)); prev=None; prevurl=None; names={}
    f=e=de=n=0; resets=0; ok=True; missing=0
    for m in msgs:
        if m.get("type")=="function_call": names[m["call_id"]]=m.get("name")
        if m.get("type")!="function_call_output": continue
        t=txt(m.get("output"))
        if "[ref=e" not in t: continue
        n+=1; f+=toks(t)
        url=(re.search(r"Page URL: (\S+)",t) or [None,None])[1]
        lines=t.split("\n")
        if prev is None: e+=toks(t); de+=toks(t); 
        elif t==prev[0]: de+=toks("[unchanged]")
        else:
            e+=toks(t)
            if url!=prevurl: de+=toks(t); resets+=1
            else:
                pl=prev[1]; ps=set(pl); ns=set(lines)
                # keep/drop/add: emit added lines (with one context) + removed ref list
                added=[l for l in lines if l not in ps]
                oldrefs=set(refre.findall("\n".join(pl))); newrefs=set(refre.findall(t))
                rem=oldrefs-newrefs
                dl="\n".join(added)+"\nremoved refs: "+",".join(sorted(rem))
                # add ~25% for position pointers
                size=toks(dl)*1.25+10
                de+=min(size,toks(t))
        if prev is None: e+=0
        prev=(t,lines); prevurl=url
    if n:
        rows.append((d.split("/")[-4:-1],n,f,e,de,resets))
        for k,v in zip(("snaps","full","exact","delta","reset"),(n,f,e,de,resets)): tot[k]+=v
        tot["runs"]+=1
print(tot)
print("vs full: exact-dedup saves %.1f%%, delta saves %.1f%%"%(100*(1-tot['exact']/tot['full']),100*(1-tot['delta']/tot['full'])))
print("delta beyond exact dedup: %.1f%% of post-dedup"%(100*(1-tot['delta']/tot['exact'])))
for r in rows[:5]: print(r)
