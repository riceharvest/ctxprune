import ast,sys,os
G="docs/ideas/gate/"
SRC={"cases_rules_compilers_build_native.py":{118:"strip_echo",121:"lock_digest",220:"cap_candidates",703:"drop_system_regions"},
"cases_rules_structured_json.py":{122:"extract_traceback",132:"sample_arrays",133:"elide_numeric_arrays",606:"registry_digest"},
"cases_rules_ci_infra_cloud.py":{137:"stub_last_applied",314:"digest_plan_json"},
"cases_rules_logs_ops_ml.py":{411:"decimate_steps"}}
out=[]; seen=set(); entry=[]
for f,m in SRC.items():
    t=open(G+f).read(); tree=ast.parse(t)
    defs={}
    imports=[]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)): defs[n.name]=n
        elif isinstance(n,ast.Assign):
            for tg in n.targets:
                for x in ast.walk(tg):
                    if isinstance(x,ast.Name): defs[x.id]=n
        elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name): defs[n.target.id]=n
    # dependency closure in source order
    need=set(); stack=list(m.values())
    while stack:
        nm=stack.pop()
        if nm in need or nm not in defs: continue
        need.add(nm)
        for x in ast.walk(defs[nm]):
            if isinstance(x,ast.Name) and x.id in defs: stack.append(x.id)
    pre=f.split("_",2)[2][:-3]
    ren={n:f"_{pre[:4]}_{n}" for n in need}
    for n in tree.body:
        names=[]
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)): names=[n.name]
        elif isinstance(n,ast.Assign): names=[x.id for tg in n.targets for x in ast.walk(tg) if isinstance(x,ast.Name)]
        elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name): names=[n.target.id]
        if names and all(x in need for x in names):
            seg=ast.get_source_segment(t,n)
            # rename via token-level regex
            import re
            for o,nw in ren.items(): seg=re.sub(r"(?<![\w.])"+re.escape(o)+r"\b",nw,seg)
            out.append(seg)
    for i,nm in m.items(): entry.append((i,ren[nm]))
body="\n\n".join(out)+"\n"
d="scripts/digest.py"; t=open(d).read()
a=t.index("# >>> generated\n")+len("# >>> generated\n"); b=t.index("# <<< generated")
open(d,"w").write(t[:a]+body+t[b:])
print(entry)
