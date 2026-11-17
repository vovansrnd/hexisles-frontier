"""Part 1: replication equivalence, labeled offers, official metrics, deal->build timing."""
import json, sys, collections, numpy as np
from lib import *
rng=np.random.default_rng(99)
def boot_seed(d_by_seed,B=10000):
    d=np.array(d_by_seed); bs=np.array([d[rng.integers(0,len(d),len(d))].mean() for _ in range(B)])
    return d.mean(),np.percentile(bs,2.5),np.percentile(bs,97.5)
def per_seed(rows_dict, fn):
    by=collections.defaultdict(list)
    for k,r in rows_dict.items(): by[k[0]].append(fn(r))
    return {s:float(np.mean(v)) for s,v in by.items()}
def paired(ra,rb,fn):
    a=per_seed(ra,fn); b=per_seed(rb,fn); ss=sorted(set(a)&set(b))
    ma=np.array([a[s] for s in ss]); mb=np.array([b[s] for s in ss])
    m,lo,hi=boot_seed(mb-ma); return ma.mean(),mb.mean(),m,lo,hi,int((mb-ma<0).sum()),len(ss)
files={'A0x':sys.argv[1],'A2':sys.argv[2]}
X={a:load_rows(f) for a,f in files.items()}
print({a:len(v) for a,v in X.items()})
# --- S1 equivalence with official manifests
for arm,off in (('A0x','A0'),('A2','A2')):
    man=official_manifest('26',off); same=0; n=0; bad=[]
    for k,r in X[arm].items():
        m=man[k]; n+=1
        ok=all(r.get(q)==m.get(q) for q in ['turns','winner_type','n_proposals','vp','decisions','map_md5'])
        same+=ok
        if not ok: bad.append(k)
    print(f'S1 {arm} replica vs official {off}: identical {same}/{n}', bad[:5])
# --- S2 per-game labeled offers
def game_stats(r):
    offs=offers_from_row(r); ev=r['ev']
    acc=[o for o in offs if o['acc']]
    d={'offers':len(offs),'acc':len(acc),'acc_rate':len(acc)/max(1,len(offs))}
    for b in BUILD: d[b]=sum(1 for e in ev if e[0]=='d' and e[3]==b and e[4])
    d['bank']=sum(1 for e in ev if e[0]=='d' and e[3]=='trade_bank' and e[4])
    # official accel metric: builds by proposer at T < turn <= T+6
    def anyb(o,lo,hi): return any(lo<dt<=hi for dt,_ in o['later'])
    d['off_accel']=sum(1 for o in acc if anyb(o,0,6))
    d['same_turn']=sum(1 for o in acc if o['same'])
    d['same_or_next_own']=sum(1 for o in acc if o['same'] or anyb(o,0,4))
    d['within2']=sum(1 for o in acc if o['same'] or anyb(o,0,8))
    d['within3']=sum(1 for o in acc if o['same'] or anyb(o,0,12))
    d['next_own_only']=sum(1 for o in acc if (not o['same']) and anyb(o,0,4))
    d['same_outpost_or_city']=sum(1 for o in acc if any(b in ('build_outpost','build_metropolis') for b in o['same']))
    d['same_or_next_oc']=sum(1 for o in acc if any(b in ('build_outpost','build_metropolis') for b in o['same']) or any(dt<=4 and b in ('build_outpost','build_metropolis') for dt,b in o['later']))
    # afford_after flags: could proposer afford a settlement/city/route right after trade, and not before?
    def newaff(o):
        b=o['aff_b'] or {}; a=o['aff_a'] or {}
        return any(a.get(k) and not b.get(k) for k in ('route','outpost','metropolis'))
    def newaff_oc(o):
        b=o['aff_b'] or {}; a=o['aff_a'] or {}
        return any(a.get(k) and not b.get(k) for k in ('outpost','metropolis'))
    d['acc_newaff']=sum(1 for o in acc if newaff(o)); d['acc_newaff_oc']=sum(1 for o in acc if newaff_oc(o))
    d['off_newaff']=sum(1 for o in offs if newaff(o)); d['off_newaff_oc']=sum(1 for o in offs if newaff_oc(o))
    d['turns']=r['turns']
    d['mean_vp']=float(np.mean(list(r['vp'].values())))
    for lo,hi,nm in ((-9,1,'g<1'),(1,3,'g1-3'),(3,99,'g>=3')):
        d['off_'+nm]=sum(1 for o in offs if lo<=o['gain']<hi); d['acc_'+nm]=sum(1 for o in acc if lo<=o['gain']<hi)
    return d
S={a:{k:game_stats(r) for k,r in X[a].items()} for a in X}
import pickle; pickle.dump(S,open('S_stage26.pkl','wb'))
names=['offers','acc','acc_rate','build_route','build_outpost','build_metropolis','bank','turns','mean_vp','off_accel','same_turn','next_own_only','same_or_next_own','within2','within3','same_outpost_or_city','same_or_next_oc','off_newaff','off_newaff_oc','acc_newaff','acc_newaff_oc','off_g<1','off_g1-3','off_g>=3','acc_g<1','acc_g1-3','acc_g>=3']
print('\nS2 Stage 2.6 replica (labeled) A0x -> A2, paired by seed (20 seeds, bootstrap 10000)')
for nm in names:
    a={k:v[nm] for k,v in S['A0x'].items()}; b={k:v[nm] for k,v in S['A2'].items()}
    ma,mb,m,lo,hi,neg,ns=paired({k:{'x':v} for k,v in a.items()},{k:{'x':v} for k,v in b.items()},lambda r:r['x'])
    print(f'{nm:24s} {ma:8.3f} -> {mb:8.3f}  diff {m:+7.3f} CI95 ({lo:+.3f},{hi:+.3f})  seeds<0 {neg}/{ns}')
