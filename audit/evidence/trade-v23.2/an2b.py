"""Full-offer predictor comparison: official shadow/recorded predictions (all offers) + labels from the bit-identical replica."""
import json, collections, numpy as np
from lib import *
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss
rng=np.random.default_rng(23)
def logit(p): p=np.clip(np.asarray(p,float),1e-4,1-1e-4); return np.log(p/(1-p))
def build(stage,arm_off,arm_rep,path):
    sd='01_OFFICIAL_STAGE_25' if stage=='25' else '02_OFFICIAL_STAGE_26'
    rows=load_rows(path); out=[]; mism=0; n=0
    for k,r in rows.items():
        off=[json.loads(l) for l in open(f"{R}/{sd}/games/{arm_off}_seed{k[0]}_p{k[1]:02d}/proposals.jsonl",encoding='utf-8') if l.strip()]
        mine=r['props']; offs=offers_from_row(r)
        assert len(off)==len(mine)==len(offs)
        for a,b,o in zip(off,mine,offs):
            n+=1
            if not (a['turn']==b['turn'] and a['proposer']==b['proposer'] and a['give']==b['give'] and a['want']==b['want'] and abs(a['gain']-b['gain'])<1e-9 and abs(a['legacy_pred']-b['legacy_pred'])<1e-9): mism+=1
            out.append(dict(seed=k[0],y=o['acc'],legacy=a['legacy_pred'],model=a['model_pred'],arch=a['arch'],turn=a['turn'],gain=a['gain']))
    print(f'stage {stage} {arm_off}: offers {n}; official proposals vs replica mismatches (turn/proposer/give/want/gain/legacy_pred): {mism}')
    return out
O={'25_A0':build('25','A0','A0x','s25_A0x.jsonl'),'26_A0':build('26','A0','A0x','s26_A0x.jsonl'),'26_A2':build('26','A2','A2','s26_A2.jsonl')}
arr=lambda v,k:np.array([o[k] for o in v],float)
tr=O['25_A0']; platt=LogisticRegression(C=1e6).fit(logit(arr(tr,'legacy')).reshape(-1,1),arr(tr,'y')); base=arr(tr,'y').mean()
print(f"Platt(legacy) fit on Stage 2.5 official A0 offers: slope {platt.coef_[0,0]:.3f} icpt {platt.intercept_[0]:.3f}; base rate {base:.4f}")
def murphy(y,p,bins=10):
    q=np.quantile(p,np.linspace(0,1,bins+1)); idx=np.clip(np.searchsorted(q,p,side='right')-1,0,bins-1); N=len(y); ob=y.mean(); rel=res=0
    for b in range(bins):
        m=idx==b
        if m.sum(): rel+=m.sum()/N*(p[m].mean()-y[m].mean())**2; res+=m.sum()/N*(y[m].mean()-ob)**2
    return rel,res
for key in ('26_A0','26_A2'):
    v=O[key]; y=arr(v,'y'); sd=arr(v,'seed'); P={'const':np.full(len(y),base),'legacy raw':arr(v,'legacy'),'Platt(legacy)':platt.predict_proba(logit(arr(v,'legacy')).reshape(-1,1))[:,1],'v23.2':arr(v,'model')}
    print(f"\n=== {key}: ALL {len(y)} offers, observed accept {y.mean():.4f}; mean p: "+', '.join(f"{n} {p.mean():.3f}" for n,p in P.items()))
    b0=np.mean((P['const']-y)**2)
    for n,p in P.items():
        pc=np.clip(p,1e-6,1-1e-6); br=np.mean((pc-y)**2)
        sl=ic=rel=rs=float('nan')
        if n!='const':
            m=LogisticRegression(C=1e6).fit(logit(pc).reshape(-1,1),y); sl,ic=m.coef_[0,0],m.intercept_[0]; rel,rs=murphy(y,pc)
        print(f"  {n:14s} Brier {br:.4f} logloss {log_loss(y,pc):.4f} AUC {roc_auc_score(y,pc) if n!='const' else .5:.3f} slope {sl:.2f} icpt {ic:.2f} REL {rel:.5f} RES {rs:.5f} BSS {1-br/b0:+.3f}")
    us=np.unique(sd); idx={s:np.where(sd==s)[0] for s in us}; pa=P['v23.2']; pb=P['Platt(legacy)']
    db=[];da=[]
    for _ in range(2000):
        ch=rng.choice(us,len(us)); ii=np.concatenate([idx[s] for s in ch]); yy=y[ii]
        db.append(np.mean((pa[ii]-yy)**2)-np.mean((pb[ii]-yy)**2)); da.append(roc_auc_score(yy,pa[ii])-roc_auc_score(yy,pb[ii]))
    print(f"  seed-cluster paired (20 seeds) v23.2 - Platt(legacy): dBrier {np.mean((pa-y)**2)-np.mean((pb-y)**2):+.4f} CI ({np.percentile(db,2.5):+.4f},{np.percentile(db,97.5):+.4f}); dAUC {roc_auc_score(y,pa)-roc_auc_score(y,pb):+.3f} CI ({np.percentile(da,2.5):+.3f},{np.percentile(da,97.5):+.3f})")
    # per-seed spread of base rate (map heterogeneity)
    rs_=np.array([y[idx[s]].mean() for s in us]); print(f"  per-seed accept rate: mean {rs_.mean():.3f} SD across 20 maps {rs_.std(ddof=1):.3f} (SE of mean {rs_.std(ddof=1)/np.sqrt(20):.3f}); training-set rate implied by const Brier 0.1686: ~0.216")
# within-2.6 GroupKFold Platt
import itertools
v=O['26_A0']; y=arr(v,'y'); sd=arr(v,'seed'); x=logit(arr(v,'legacy')).reshape(-1,1); us=np.unique(sd); folds=[us[i::5] for i in range(5)]; out=np.zeros(len(y))
for f in folds:
    te=np.isin(sd,f); m=LogisticRegression(C=1e6).fit(x[~te],y[~te]); out[te]=m.predict_proba(x[te])[:,1]
print(f"\nwithin-2.6 GroupKFold(5 by seed) Platt(legacy) on A0 offers: Brier {np.mean((out-y)**2):.4f} AUC {roc_auc_score(y,out):.3f} | v23.2: Brier {np.mean((arr(v,'model')-y)**2):.4f} AUC {roc_auc_score(y,arr(v,'model')):.3f}")
