import sys, collections, numpy as np
sys.argv=[sys.argv[0],'s26_A0x.jsonl','s26_A2.jsonl','s25_A0x.jsonl']
exec(open('an3.py').read().split("def fates")[0])   # reuse loaders only (A0, A2 loaded)
print('loaded')
# --- leader_help within calls
const=var=0; vals=collections.Counter()
for c in A0:
    lhs={round(x['lh'],3) for x in c['cv']}
    if not c['cv']: continue
    if len(lhs)==1: const+=1
    else: var+=1
    for x in c['cv']: vals[round(x['lh'],2)]+=1
print(f"leader_help within one call: constant across all candidates in {const} of {const+var} calls ({const/(const+var):.2%}); candidate-level values {dict(vals)}")
tot=sum(vals.values()); print('  share of candidates with penalty 0.2: %.1f%%, 0.8: %.1f%%, 0: %.1f%%'%(100*vals[0.2]/tot,100*vals[0.8]/tot,100*vals[0.0]/tot))
# effect of lh on the score floor: candidates failing floor only because of lh
fl=0; tot_gp=0
for c in A2:
    for x in c['cv']:
        if x['pm']<0: continue
        if x['gp']:
            tot_gp+=1
            if x['rej'] and x['gain']*(0.35+0.65*x['pm'])>0.25: fl+=1
print(f"A2 candidates that passed gate and would clear the 0.25 floor without leader_help but fail with it: {fl} of {tot_gp} gate-passers ({fl/tot_gp:.1%})")
# --- decomposition: gate vs rank, on A0 candidate sets
def pick(cv,gate,rank,arch):
    best=None;bs=-1e9
    for x in cv:
        if gate=='legacy' and not x['gp']: continue
        if gate=='A2' and x['pm']<A2THR[arch]: continue
        p=x['pl'] if rank=='legacy' else x['pm']
        s=x['gain']*(0.35+0.65*p)-x['lh']
        floorp=x['pl'] if rank=='legacy' else x['pm']
        if s<=0.25: continue
        if s>bs: bs=s;best=x
    return best
combos=[('legacy','legacy'),('legacy','model'),('A2','legacy'),('A2','model')]
res={}
for g_,r_ in combos:
    n=off=comp=0; gs=0; hi=0; ch=[]
    for c in A0:
        if not c['cv']: continue
        n+=1; x=pick(c['cv'],g_,r_,c['arch'])
        if x is not None: off+=1; comp+=(x['gm']==1); gs+=x['gain']; hi+=(x['gain']>=3); ch.append(id(x))
    res[(g_,r_)]=(off,comp/off,gs/off,hi/off)
print('\nDecomposition on identical A0 candidate sets (gate=who is allowed, rank=which p enters 0.35+0.65p):')
print(f"{'gate':8s} {'rank p':8s} {'offers':>7s} {'%completing':>11s} {'mean gain':>9s} {'%gain>=3':>8s}")
for (g_,r_),(off,cp,mg,hi) in res.items(): print(f"{g_:8s} {r_:8s} {off:7d} {cp:11.1%} {mg:9.3f} {hi:8.1%}")
# realized (A0 vs A2 trajectories) share of offers gain>=3 and completing(afford) for comparison
import pickle
S=pickle.load(open('S_stage26.pkl','rb'))
for a in ('A0x','A2'):
    off=sum(v['offers'] for v in S[a].values()); print(f"realized {a}: share of offers with gain>=3 {sum(v['off_g>=3'] for v in S[a].values())/off:.1%}; share making a build newly affordable {sum(v['off_newaff'] for v in S[a].values())/off:.1%}")
