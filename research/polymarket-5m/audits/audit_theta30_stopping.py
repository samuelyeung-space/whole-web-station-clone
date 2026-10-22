"""Independent accounting of a frozen Theta30 stopping sample, not a replay.

All probabilities below condition on published paper costs and outcomes.
Neither complete-day coverage, exchange fills, cash, nor formal OOS is inferred.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean,stdev
import numpy as np
from scipy.stats import t as student_t

EXPECTED_FREEZE='edf92163e514469e26c7d896a961d7b5d2b95f158b964915284b7f527cc309eb'
REQUIRED=('market_id','day','decision_ms','sent','filled','reason','side_up',
 'decision_ask','decision_mid_up','fixed_limit','fill_price','fill_fee',
 'all_in_cost','won','pnl_per_share','pnl')

def flag(value):
    if value not in ('True','False'):raise ValueError('invalid Boolean')
    return value=='True'

def num(value):
    x=float(value)
    if not math.isfinite(x):raise ValueError('nonfinite input')
    return x

def near(a,b):
    if not math.isclose(a,b,rel_tol=0,abs_tol=1e-10):
        raise ValueError('published arithmetic mismatch')

def exact_bernoulli_tail(costs,wins):
    probabilities=[1.0]
    for cost in costs:
        following=[0.0]*(len(probabilities)+1)
        for k,weight in enumerate(probabilities):
            following[k]+=weight*(1-cost);following[k+1]+=weight*cost
        probabilities=following
    near(math.fsum(probabilities),1.0)
    return math.fsum(probabilities[wins:])

def audit(rows,freeze):
    seen=set();filled=[];sent=0
    freeze_ms=datetime.fromisoformat(freeze['frozen_at'].replace('Z','+00:00')).timestamp()*1000
    rate=num(freeze['strategy']['fee_rate']);shares=num(freeze['strategy']['shares'])
    if set(rows[0])!=set(REQUIRED):raise ValueError('unexpected schema')
    for r in rows:
        market=r['market_id']
        if market in seen:raise ValueError('duplicate market')
        seen.add(market)
        if not market.startswith('btc-updown-5m-'):raise ValueError('non-BTC5m market')
        start=int(market.rsplit('-',1)[1])
        if start%300:raise ValueError('unaligned slot')
        if r['day']!=datetime.fromtimestamp(start,timezone.utc).date().isoformat():
            raise ValueError('day/market mismatch')
        decision=num(r['decision_ms'])
        if decision<freeze_ms:raise ValueError('pre-freeze row')
        sends=flag(r['sent']);fills=flag(r['filled']);sent+=sends
        if fills:
            if not sends:raise ValueError('fill without send')
            won=flag(r['won']);price=num(r['fill_price']);fee=num(r['fill_fee'])
            cost=num(r['all_in_cost']);pnl=num(r['pnl_per_share'])
            if not 0<price<1 or not 0<cost<1:raise ValueError('invalid binary cost')
            near(fee,rate*price*(1-price));near(cost,price+fee)
            near(pnl,float(won)-cost);near(num(r['pnl']),shares*pnl)
            filled.append({**r,'decision_ms':decision,'cost':cost,'net':pnl,'is_win':won})
    filled.sort(key=lambda r:(r['decision_ms'],r['market_id']))
    nstop=int(freeze['stopping_rule']['first_fills']);judged=filled[:nstop]
    by_day={}
    for r in judged:by_day.setdefault(r['day'],[]).append(r['net'])
    days=len(by_day);mean=fmean(r['net'] for r in judged) if judged else None
    eligible=len(judged)==nstop and days>=int(freeze['minimum_days'])
    lower=None;mc_p=None;exact_p=None;robust_se=None
    if judged:
        costs=[r['cost'] for r in judged];wins=sum(r['is_win'] for r in judged)
        exact_p=exact_bernoulli_tail(costs,wins)
        rng=np.random.default_rng(300001);sim=np.zeros(200000);cost=np.asarray(costs)
        for start in range(0,len(cost),256):
            c=cost[start:start+256];sim+=((rng.random((200000,c.size))<c)-c).sum(axis=1)
        mc_p=float((sim>=math.fsum(r['net'] for r in judged)-1e-12).mean())
        if eligible:
            daily=[fmean(v) for _,v in sorted(by_day.items())]
            se=stdev(daily)/math.sqrt(days) if days>1 else math.inf
            critical=float(student_t.ppf(1-float(freeze['alpha']),days-1))
            lower=mean if se==0 else mean-critical*se
            robust_se=math.sqrt(days/(days-1)*math.fsum(
                (math.fsum(v)-len(v)*mean)**2 for v in by_day.values()))/len(judged)
    gate=bool(eligible and mean>0 and lower>0 and mc_p<float(freeze['alpha']))
    return {'schema':'theta30-published-stopping-audit-v1',
        'classification':'published_paper_accounting_only','rows':len(rows),'sent':sent,
        'published_fills':len(filled),'judged_fills':len(judged),'judged_signal_days':days,
        'first_fill_market':judged[0]['market_id'] if judged else None,
        'last_fill_market':judged[-1]['market_id'] if judged else None,
        'stopping_sample_complete':eligible,'paper_gate_passed':gate,
        'mean_net_per_share_usd':mean,
        'sum_net_per_share_usd':math.fsum(r['net'] for r in judged) if judged else None,
        'recorded_five_share_pnl_usd':math.fsum(num(r['pnl']) for r in judged) if judged else None,
        'original_frozen_day_mean_t_lower99_usd':lower,
        'original_mc_fair_cost_p_200000_seed300001':mc_p,
        'independent_poisson_binomial_tail_p':exact_p,
        'additional_fill_weighted_day_cluster_se_usd':robust_se,
        'days':[{'day':d,'fills':len(v),'net_per_share_sum':math.fsum(v),'mean':fmean(v)}
            for d,v in sorted(by_day.items())],
        'whole_calendar_days_verified':0,'official_labels_independently_verified':False,
        'real_exchange_fills_verified':False,'rank7_pnl_usd':None,'formal_oos_result':None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--rows',required=True,type=Path)
    ap.add_argument('--freeze',required=True,type=Path);ap.add_argument('--out',required=True,type=Path)
    a=ap.parse_args()
    if hashlib.sha256(a.freeze.read_bytes()).hexdigest()!=EXPECTED_FREEZE:
        raise ValueError('frozen Theta30 protocol identity drift')
    with a.rows.open(newline='') as f:rows=list(csv.DictReader(f))
    if not rows:raise ValueError('empty published ledger')
    result=audit(rows,json.loads(a.freeze.read_text()))
    result.update({'rows_sha256':hashlib.sha256(a.rows.read_bytes()).hexdigest(),
        'freeze_sha256':hashlib.sha256(a.freeze.read_bytes()).hexdigest(),
        'auditor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    a.out.write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n')

if __name__=='__main__':main()
