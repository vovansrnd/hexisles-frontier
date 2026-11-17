import sys, json, shutil, os, time, itertools
sys.path.insert(0,'/home/claude/ev/lab/tools')
import rep_run as R
arm=sys.argv[1]; nseat=int(sys.argv[2]); out=sys.argv[3]
combos=list(itertools.combinations(['c3po','r2d2','t800','walle'],nseat)) if nseat==3 else [('c3po','r2d2'),('t800','walle'),('c3po','t800'),('r2d2','walle')]
t0=time.time(); n=0
with open(out,'w',encoding='utf-8') as f:
    for s in range(65001,65021):
        for ci,c in enumerate(combos):
            row=R.run_game({'arm':arm,'seed':s,'perm':ci,'out_root':'/tmp/repl','seats':list(c)})
            shutil.rmtree(f'/tmp/repl/{arm}_seed{s}_p{ci:02d}',ignore_errors=True)
            f.write(json.dumps(row,ensure_ascii=False)+'\n'); n+=1
print('done',n,round(time.time()-t0),'s')
