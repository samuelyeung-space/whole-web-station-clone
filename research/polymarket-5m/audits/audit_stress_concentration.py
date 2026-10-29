"""Audit existing development ledgers; no strategy replay or OOS qualification.

CLI: python audit_stress_concentration.py BASE_ROOT STRESS_ROOT OUTPUT
24h blocks are anchored to the existing window; never select a profitable week.
The bootstrap is conditional, descriptive and unadjusted for prior screening.
"""
import collections,gzip,hashlib,json,sys
from pathlib import Path
import numpy as np


def load(path):
    with gzip.open(path,'rt') as f:return [json.loads(line) for line in f]


def identity(row):
    return tuple(row[k] for k in ('candidate_id','latency_ms','market_id','decision_ms','side'))


def main(base,stress,output):
    base=Path(base);stress=Path(stress)
    window=json.load(open(base/'audit/window_asof.jsonl.gz.audit.json'))
    start,end=window['requested_start_ms'],window['requested_end_ms']
    assert abs(end-start-7*86400000)<1
    paths={'base':base/'results/asof/shares100_base_release0/attempts.jsonl.gz',
           'half_depth':stress/'shares100_half_depth_release0/attempts.jsonl.gz',
           'extra_1cent':stress/'shares100_plus_1cent_release0/attempts.jsonl.gz'}
    data={k:load(p) for k,p in paths.items()}
    source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths.values()}
    metrics=json.load(open(base/'results/asof/metrics.json'))+json.load(open(stress/'metrics.json'))
    maps={k:{identity(r):r for r in rows} for k,rows in data.items()}
    assert all(len(maps[k])==len(rows) for k,rows in data.items())
    assert set(maps['base'])==set(maps['half_depth'])==set(maps['extra_1cent'])
    checks=collections.Counter();results=[];comparisons=[]
    for scenario,rows in data.items():
        by_account=collections.defaultdict(list)
        for r in rows:
            by_account[(r['candidate_id'],r['latency_ms'])].append(r)
            assert start<=r['decision_ms']<end
            assert abs(r['match_ms']-r['decision_ms']-r['latency_ms'])<.01
            if r.get('filled_shares',0)>0:
                assert r['settled'] and r['net_pnl_usd'] is not None
                assert abs(r['cost_usd']-(r['filled_shares']*r['fill_vwap']+r['fees_usd']+r['extra_cost_usd']))<1e-6
                assert abs(r['net_pnl_usd']-(r['payout_usd']-r['cost_usd']))<1e-6
                assert abs(r['extra_cost_usd']-r['filled_shares']*(.01 if scenario=='extra_1cent' else 0))<1e-6
                checks['filled_order_arithmetic']+=1
            checks['all_attempts_in_fixed_window']+=1
        for (cid,lat),aa in sorted(by_account.items()):
            mm=next(m for m in metrics if m['candidate_id']==cid and m['latency_ms']==lat and
                    ('base' if 'base' in m['scenario'] else 'half_depth' if 'half_depth' in m['scenario'] else 'extra_1cent')==scenario)
            blocks=np.zeros(7);markets=collections.defaultdict(float);q=0;pnl=0;fees=0
            for r in aa:
                v=r.get('net_pnl_usd') or 0
                i=int((r['decision_ms']-start)//86400000);blocks[i]+=v
                markets[r['market_id']]+=v;pnl+=v;q+=r.get('filled_shares',0);fees+=r.get('fees_usd',0)
            assert abs(pnl-mm['net_pnl_usd'])<1e-6
            assert abs(q-mm['filled_shares'])<1e-6 and abs(fees-mm['fees_usd'])<1e-6
            assert abs(mm['ending_cash_usd']-1000-pnl)<1e-6
            rng=np.random.default_rng(20261029)
            boot=blocks[rng.integers(0,7,size=(100000,7))].sum(axis=1)
            lo,hi=np.quantile(boot,[.025,.975])
            results.append(dict(candidate_id=cid,latency_ms=lat,scenario=scenario,attempts=len(aa),
                filled_orders=sum(r.get('filled_shares',0)>0 for r in aa),filled_shares=q,fees_usd=fees,
                net_pnl_usd=pnl,net_per_share_usd=pnl/q if q else None,blocks_24h_pnl=blocks.tolist(),
                positive_blocks=int(sum(blocks>0)),best_block_removed_pnl=pnl-float(max(blocks)),
                best_market_removed_pnl=pnl-max(markets.values()),conditional_24h_bootstrap_95_sum_interval=[float(lo),float(hi)],
                max_realized_drawdown_usd=mm['max_realized_drawdown_usd'],formal_oos_result=None))
    for cid,lat in sorted({(r['candidate_id'],r['latency_ms']) for r in data['base']}):
        subset=[key for key in maps['base'] if key[:2]==(cid,lat)]
        b=[maps['base'][k] for k in subset];e=[maps['extra_1cent'][k] for k in subset]
        total_delta=sum((y.get('net_pnl_usd') or 0)-(x.get('net_pnl_usd') or 0) for x,y in zip(b,e))
        extra=sum(r['extra_cost_usd'] for r in e)
        comparisons.append(dict(candidate_id=cid,latency_ms=lat,
            identities_unchanged=len(subset),extra_cost_usd=extra,pnl_delta=total_delta,
            execution_cash_residual=total_delta+extra,
            changed_fill_or_status_attempts=sum((x['status'],x['filled_shares'],x['fill_vwap'])!=(y['status'],y['filled_shares'],y['fill_vwap']) for x,y in zip(b,e))))
    out=dict(schema='stress-concentration-ledger-audit-v1',new_financial_replays=0,
        window_start_ms=start,window_end_ms=end,source_hashes=source_hashes,checks=dict(checks),
        scenario_accounts=results,paired_extra_cost=comparisons,bootstrap_seed=20261029,bootstrap_draws=100000,
        limitations=['7 anchored24h blocks only; no independent-block evidence','bootstrap conditional on consumed incomplete window; not selection-adjusted or OOS significance','capital/fill paths frozen from existing replay; removal is accounting diagnostic, not counterfactual replay','same missing-feed/feature/cash-release/capacity limitations as source','Oct24-Oct28 content already consumed; old Oct24-Oct31 reserve cannot remain wholly untouched'],formal_oos_result=None,qualified_mechanisms=0)
    Path(output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(checks=checks,accounts=len(results),new_financial_replays=0)))


if __name__=='__main__':main(*sys.argv[1:])
