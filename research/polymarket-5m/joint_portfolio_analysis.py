"""Descriptive development diagnostics; no post-selection significance claim."""
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from joint_probability_research import dump,sha


def analyze(directory,joint_prior,mean_prior):
    directory=Path(directory)
    signals=json.loads((directory/'signals.json').read_text())
    first={(r['candidate_id'],r['market_id']):r for r in signals['component_first_signals']}
    prior=[]
    for path,ids in [(joint_prior,['JPR-035']),(mean_prior,['MAR-089','MAR-099'])]:
        for r in map(json.loads,gzip.open(path,'rt')):
            key=(r['candidate_id'],r['market_id'])
            if r['candidate_id'] in ids and key in first:
                if r['decision_ms']!=first[key]['decision_ms']:
                    raise ValueError('Prior reference and portfolio component disagree on first signal')
                prior.append(r)
    reports=[]
    for latency in [500,600,1000]:
        path=directory/'full100_plus1c_release0_attempts.jsonl.gz'
        rows=[r for r in map(json.loads,gzip.open(path,'rt')) if r['latency_ms']==latency]
        pnl=[r['net_pnl_usd'] or 0 for r in rows]
        rng=np.random.default_rng(17)
        bootstrap=rng.choice(pnl,(20000,len(pnl)),replace=True).sum(axis=1)
        blocks=defaultdict(float);attribution=defaultdict(float)
        for r in rows:
            blocks[int(r['decision_ms']//1800000)]+=r['net_pnl_usd'] or 0
            attribution[r['selected_candidate']]+=r['net_pnl_usd'] or 0
        naive=sum(r['net_pnl_usd'] or 0 for r in prior if r['latency_ms']==latency)
        reports.append({'latency_ms':latency,'naive_independent_cash_sum_usd_INVALID_PORTFOLIO':naive,
            'shared_portfolio_pnl_usd':sum(pnl),'difference_usd':sum(pnl)-naive,
            'attribution_usd':dict(attribution),'negative_30m_blocks':sum(v<0 for v in blocks.values()),
            'observed_signal_30m_blocks':len(blocks),'worst_30m_block_usd':min(blocks.values()),
            'pnl_without_best_3_markets_usd':sum(pnl)-sum(sorted(pnl,reverse=True)[:3]),
            'naive_market_bootstrap_95_sum_interval_usd':np.quantile(bootstrap,[.025,.975]).tolist(),
            'bootstrap_warning':'descriptive only; ignores day dependence and prior selection; not valid confidence in positive edge',
            'day_cluster_confidence_interval':None,'day_cluster_reason':'only one consumed development date'})
    dump(directory/'analysis.json',{'reports':reports,'prior_input_sha256':{p:sha(p) for p in [joint_prior,mean_prior]},
         'read_existing_baselines_only':True,'formal_oos_result':None})


if __name__=='__main__':analyze(*sys.argv[1:])
