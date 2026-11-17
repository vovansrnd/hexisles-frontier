"""Part 3: candidate-level diagnostics, counterfactual decision replay, threshold stability, time-feature check."""
import sys, json, collections, numpy as np
from lib import *
rng=np.random.default_rng(17)
A2THR={'c3po':0.142,'r2d2':0.118,'t800':0.151,'walle':0.133}
LEG={'c3po':0.34,'r2d2':0.28,'t800':0.38,'walle':0.34}
FI={n:i for i,n in enumerate(FO)}
def calls_of(path):
    rows=load_rows(path); out=[]
    for k,r in rows.items():
        seats=r['seats']
        for c in r['cands']:
            arch=seats[str(c['pid'])]
            out.append(dict(seed=r['seed'],perm=k[1],turn=c['turn'],pid=c['pid'],arch=arch,status=c['status'],chosen=c['chosen'],sel=c.get('sel'),cands=c['cands']))
    return out
def cand_view(c,arm_model_used):
    give,want,gain,pu,pl,gate,gp,lh,score,rej,f=c
    pm=pu if arm_model_used else model_p(f)
    leg=pl if arm_model_used else pu
    return dict(gain=gain,pm=pm,pl=leg,gate=gate,gp=gp,lh=lh,score=score,rej=rej,f=f,give=give,want=want,miss=f[FI['goal_missing']],gm=f[FI['goal_missing']])
def load(path,arm_model_used):
    calls=calls_of(path)
    for c in calls: c['cv']=[cand_view(x,arm_model_used) for x in c['cands']]
    return calls
A0=load(sys.argv[1],False); A2=load(sys.argv[2],True)
def fates(calls,label):
    n_calls=sum(1 for c in calls if c['cv']); ncand=sum(len(c['cv']) for c in calls)
    gate_fail=sum(1 for c in calls for x in c['cv'] if not x['gp']); sc_fail=sum(1 for c in calls for x in c['cv'] if x['gp'] and x['rej'])
    passed=ncand-gate_fail-sc_fail
    selected=sum(1 for c in calls if c['status']=='selected')
    print(f"{label}: calls with >=1 scored candidate {n_calls}; candidates logged {ncand}: gate-fail {gate_fail} ({gate_fail/ncand:.1%}), gate-pass & score<=0.25 {sc_fail} ({sc_fail/ncand:.1%}), gate+score pass {passed} ({passed/ncand:.1%}); calls returning a selection {selected}")
    # share of calls where >=1 candidate passes
fates(A0,'A0x (legacy decisions, = official A0) Stage 2.6'); fates(A2,'A2 Stage 2.6')
print('NOTE: the logger sees only candidates that survive structural filters and gain>=min_gain; earlier-filtered legal trades are not logged anywhere.')
# --- gain/p relationship in candidate sets of A0 trajectories (unaffected by A2 gate)
g=np.array([x['gain'] for c in A0 for x in c['cv']]); pm=np.array([x['pm'] for c in A0 for x in c['cv']]); pl=np.array([x['pl'] for c in A0 for x in c['cv']]); gm=np.array([x['gm'] for c in A0 for x in c['cv']])
from scipy.stats import spearmanr
print(f"\ncandidate-level (A0 trajectories, n={len(g)}): Spearman(gain, model p) {spearmanr(g,pm)[0]:+.3f}; Spearman(gain, legacy p) {spearmanr(g,pl)[0]:+.3f}")
for m_ in (1,2):
    s=gm==m_; print(f"  goal_missing={m_}: n={s.sum()}  mean gain {g[s].mean():.2f}  mean model p {pm[s].mean():.3f}  mean legacy p {pl[s].mean():.3f}")
# within-call
cors=[]; 
for c in A0:
    if len(c['cv'])>=2:
        gg=[x['gain'] for x in c['cv']]; pp=[x['pm'] for x in c['cv']]
        if len(set(gg))>1 and len(set(pp))>1: cors.append(spearmanr(gg,pp)[0])
print(f"  within-call Spearman(gain, model p) over {len(cors)} calls with >=2 candidates: mean {np.mean(cors):+.3f}")
# --- counterfactual decision replay on A0 trajectories (same candidate sets, different rule)
def choose(cv,rule,arch):
    best=None;bs=-1e9
    for x in cv:
        if rule=='legacy':
            if not x['gp']: continue
            s=x['gain']*(0.35+0.65*x['pl'])-x['lh']
        elif rule=='A2':
            if x['pm']<A2THR[arch]: continue
            s=x['gain']*(0.35+0.65*x['pm'])-x['lh']
        elif rule=='A2_gain_rank':     # A2 gate, rank by gain
            if x['pm']<A2THR[arch]: continue
            s=x['gain']-x['lh']
        elif rule=='A2_pgain':         # A2 gate, expected gain p*gain
            if x['pm']<A2THR[arch]: continue
            s=x['gain']*x['pm']-x['lh']
        elif rule=='A2_score_floor':   # A2 gate, rank by current formula (no hard floor -> same ranking, tests floor effect)
            if x['pm']<A2THR[arch]: continue
            s=x['gain']*(0.35+0.65*x['pm'])-x['lh']
            if s<=0: continue
            best_=None
        if rule!='A2_score_floor' and s<=0.25: continue
        if s>bs: bs=s;best=x
    return best
rules=['legacy','A2','A2_gain_rank','A2_pgain']
ref=collections.defaultdict(list)
stats={r:collections.Counter() for r in rules}; sums={r:collections.defaultdict(float) for r in rules}
for c in A0:
    if not c['cv']: continue
    ch={r:choose(c['cv'],r,c['arch']) for r in rules}
    base=ch['legacy']
    for r in rules:
        x=ch[r]; stats[r]['calls']+=1
        if x is not None:
            stats[r]['offer']+=1; sums[r]['gain']+=x['gain']; sums[r]['p']+=x['pm']; stats[r]['complete']+=(x['gm']==1)
        stats[r]['same_as_legacy']+= (x is base) or (x is None and base is None)
        if base is not None and x is None: stats[r]['lost_vs_legacy']+=1
        if base is None and x is not None: stats[r]['new_vs_legacy']+=1
        if base is not None and x is not None and x is not base: stats[r]['switched']+=1
print('\nCounterfactual decision replay on identical A0 candidate sets (Stage 2.6; static, no trajectory feedback):')
print(f"{'rule':14s} {'calls':>6s} {'offers':>7s} {'same':>6s} {'lost':>6s} {'new':>5s} {'switched':>8s} {'mean gain':>9s} {'mean model p':>12s} {'%completing(missing=1)':>22s}")
for r in rules:
    s=stats[r]; n=s['offer']
    print(f"{r:14s} {s['calls']:6d} {n:7d} {s['same_as_legacy']:6d} {s['lost_vs_legacy']:6d} {s['new_vs_legacy']:5d} {s['switched']:8d} {sums[r]['gain']/n:9.3f} {sums[r]['p']/n:12.3f} {s['complete']/n:22.1%}")
# when A2 gate cuts something legacy passes: what is it?
cut=[x for c in A0 for x in c['cv'] if x['gp'] and x['pm']<A2THR[c['arch']]]
keep=[x for c in A0 for x in c['cv'] if x['gp'] and x['pm']>=A2THR[c['arch']]]
print(f"\nlegacy-gate-passing candidates: {len(cut)+len(keep)}; cut by A2 gate {len(cut)} ({len(cut)/(len(cut)+len(keep)):.1%}); mean gain cut {np.mean([x['gain'] for x in cut]):.2f} vs kept {np.mean([x['gain'] for x in keep]):.2f}; completing share cut {np.mean([x['gm']==1 for x in cut]):.1%} vs kept {np.mean([x['gm']==1 for x in keep]):.1%}")
# --- pass shares + threshold stability
def pass_share(calls):
    out={}
    for a in LEG:
        xs=[x for c in calls if c['arch']==a for x in c['cv']]
        out[a]=(len(xs),float(np.mean([x['pl']>=LEG[a] for x in xs])),xs)
    return out
def quantile_thr(xs,share):
    p=np.array([x['pm'] for x in xs]); return float(np.quantile(p,1-share))
print('\nThreshold transplant ("keep pass share") recomputed on MY Stage 2.5 A0x candidates vs the developer table (calibrated on A1 candidates):')
import pickle
c25=load(sys.argv[3],False); ps=pass_share(c25)
dev={'c3po':(0.142,3333,0.850),'r2d2':(0.118,4773,0.877),'t800':(0.151,2507,0.838),'walle':(0.133,3585,0.846)}
for a in LEG:
    n,sh,xs=ps[a]; q=quantile_thr(xs,sh)
    # bootstrap over seeds
    seeds=sorted({c['seed'] for c in c25 if c['arch']==a}); by=collections.defaultdict(list)
    for c in c25:
        if c['arch']==a: by[c['seed']].extend(c['cv'])
    bs=[]
    for _ in range(300):
        ch=rng.choice(seeds,len(seeds)); xs2=[x for s in ch for x in by[s]]
        sh2=np.mean([x['pl']>=LEG[a] for x in xs2]); bs.append(quantile_thr(xs2,sh2))
    print(f"  {a:6s} legacy gate {LEG[a]:.2f}: my n={n} pass share {sh:.3f} -> equivalent model thr {q:.3f} (seed-bootstrap 95% {np.percentile(bs,2.5):.3f}..{np.percentile(bs,97.5):.3f}) | developer: thr {dev[a][0]} (n={dev[a][1]}, share {dev[a][2]:.3f})")
c26=A0; ps26=pass_share(c26)
print('  Applying DEVELOPER thresholds to Stage 2.6 A0 candidate sets: share of candidates passing A2 gate vs legacy gate')
for a in LEG:
    n,sh,xs=ps26[a]; print(f"    {a:6s} n={n}: legacy pass {sh:.3f} | A2-threshold pass {np.mean([x['pm']>=A2THR[a] for x in xs]):.3f} | quantile thr on 2.6 {quantile_thr(xs,sh):.3f}")
# --- time-feature check (4 seats)
f=np.array([x['f'] for c in A0 for x in c['cv']]); gt=f[:,FI['turn_div100']]*100; pt=f[:,FI['personal_turn_div30']]*30
print(f"\nTime features on 4-seat candidates (n={len(gt)}): corr(global_turn, personal_turn)={np.corrcoef(gt,pt)[0,1]:.4f}; global/personal median ratio {np.median(gt[pt>0]/pt[pt>0]):.2f}; residual SD of global_turn after regressing on 4*personal_turn: {np.std(gt-4*pt):.2f} turns")
