import sys, json, shutil, os, time, itertools
sys.path.insert(0,'/home/claude/ev/lab/tools')
import rep_run as R
arm=sys.argv[1]; seeds=range(int(sys.argv[2].split('-')[0]),int(sys.argv[2].split('-')[1])+1)
perms=[int(x) for x in sys.argv[3].split(',')] if sys.argv[3]!='all' else list(range(24))
out=sys.argv[4]; budget=float(sys.argv[5]) if len(sys.argv)>5 else 1e9
done=set()
if os.path.exists(out):
    for l in open(out,encoding='utf-8'):
        r=json.loads(l); done.add((r['seed'],r['perm']))
t0=time.time(); n=0
with open(out,'a',encoding='utf-8') as f:
    for s in seeds:
        for p in perms:
            if (s,p) in done: continue
            if time.time()-t0>budget: print('budget reached',n); sys.exit(0)
            job={'arm':arm,'seed':s,'perm':p,'out_root':'/tmp/repl'}
            row=R.run_game(job)
            shutil.rmtree(f'/tmp/repl/{arm}_seed{s}_p{p:02d}',ignore_errors=True)
            f.write(json.dumps(row,ensure_ascii=False)+'\n'); f.flush(); n+=1
print('done',n,'games in',round(time.time()-t0),'s')
