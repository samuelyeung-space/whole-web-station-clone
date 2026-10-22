"""Reconcile published Family35/36 rows without rerunning their signal engines.

Reject broken clock, quantity, price-limit and financial identities. These
checks cannot validate private fills, queue priority or historical fee sourcing.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

def close(a, b, tol=1e-7):
    if not math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol):
        raise ValueError(f"numeric identity mismatch: {a} != {b}")

def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical36(rows):
    arms=('baseline','confirmation_veto','receipt_time_shifted_placebo')
    ordered=sorted(rows,key=lambda r:(float(r['signal_receive_ms']),str(r['market_id']),
        arms.index(r['arm']),float(r['evaluation_ms']),str(r['reason'])))
    raw=json.dumps(ordered,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()

def audit_family35(root):
    rp=root/'adverse-reclaim.json';p=root/'adverse-reclaim-rows.jsonl'
    result=json.loads(rp.read_text());rows=read(p)
    assert len(rows)==result['record_count']
    assert result['promotion_eligible'] is False
    for r in rows:
        assert r['ordering_clock']=='local_receipt_ms'
        close(r['evaluation_recv_ms'],r['decision_ms']+r['evaluation_ms'],1e-3)
        if r['filled']:
            assert r['sent'] and 0<r['filled_shares']<=r['target_shares']+1e-8
            assert r['fill_vwap']<=r['fixed_limit']+1e-9
            assert all(x['price']<=r['fixed_limit']+1e-9 for x in r['fill_levels'])
            close(sum(x['shares'] for x in r['fill_levels']),r['filled_shares'])
            close(r['total_cost'],r['filled_shares']*r['fill_vwap']+r['fee'])
            close(r['pnl'],(r['filled_shares'] if r['winner']==r['buy_side'] else 0)-r['total_cost'])
        else:
            close(r['filled_shares'],0)
    groups={}
    for variant,v in result['variants'].items():
        groups[variant]={}
        for delay,metric in v['latencies'].items():
            sample=[r for r in rows if r['variant']==variant and r['evaluation_ms']==float(delay)]
            filled=[r for r in sample if r['filled']]
            assert len(sample)==metric['intents'] and len(filled)==metric['fills']
            pnl=math.fsum(r['pnl'] for r in filled if r['pnl'] is not None)
            close(pnl,metric['intention_to_trade_pnl'])
            groups[variant][delay]={
                'intents':len(sample),'sends':sum(r['sent'] for r in sample),
                'fills':len(filled),'failed_sends':sum(r['sent'] and not r['filled'] for r in sample),
                'shares':math.fsum(r['filled_shares'] for r in filled),
                'markets':len({r['market_id'] for r in filled}),
                'pnl_usd':pnl if sample else None,
                'fees_usd':math.fsum(r['fee'] for r in filled),
                'cost_usd':math.fsum(r['total_cost'] for r in filled),
                'reasons':dict(Counter(r['reason'] for r in sample)),
                'rank7_pnl_usd':None,'formal_oos_pnl_usd':None}
    return {'result_sha256':digest(rp),'rows_sha256':digest(p),
            'rows':len(rows),'groups':groups,
            'classification':'displayed_depth_upper_bound_development'}

def audit_family36(root):
    rp=root/'source-confirmation.json';p=root/'source-confirmation-rows.jsonl'
    result=json.loads(rp.read_text());rows=read(p)
    assert len(rows)==result['record_count']
    assert result['promotion_eligible'] is False
    assert canonical36(rows)==result['rows_sha256']
    stripped=[{k:v for k,v in r.items() if k not in ('artifact_row_count','artifact_rows_sha256')} for r in rows]
    assert canonical36(stripped)==result['dataset']['artifact_rows_sha256']
    for r in rows:
        assert r['ordering_clock']=='receive_ms' and r['match_book_clock']=='receive_ms'
        if 'future_health_checked' in r:
            assert r['future_health_checked'] is False
        else:
            assert not r['sent'] and not r['filled'] and r['reason']=='no_same_direction_secondary'
        close(r['evaluation_time_ms'],r['signal_receive_ms']+r['evaluation_ms'],1e-3)
        if r.get('book_receive_ms') is not None:
            assert r['book_receive_ms']<=r['evaluation_time_ms']+1e-6
        if r['filled']:
            assert r['sent'] and r['evaluation_time_ms']>=r['earliest_match_ms']-1e-6
            assert r['fill_price']<=r['fixed_limit']+1e-9
            close(r['all_in_cost']*r['filled_shares'],r['fill_price']*r['filled_shares']+r['fill_fee'])
            close(r['pnl'],(int(r['winner']==r['direction'])-r['all_in_cost'])*r['filled_shares'])
        else:
            close(r['filled_shares'],0)
    groups={};signatures={}
    for arm,a in result['arms'].items():
        groups[arm]={}
        for delay,metric in a['latencies'].items():
            sample=[r for r in rows if r['arm']==arm and r['evaluation_ms']==float(delay)]
            filled=[r for r in sample if r['filled']]
            assert len(sample)==metric['opportunities'] and len(filled)==metric['fills']
            pnl=math.fsum(r['pnl'] for r in filled)
            close(pnl,metric['intention_to_trade_pnl'])
            signatures[(arm,delay)]={tuple(r[k] for k in ['market_id','signal_receive_ms','fill_price','filled_shares','fill_fee','pnl']) for r in filled}
            groups[arm][delay]={
                'opportunities':len(sample),'sends':sum(r['sent'] for r in sample),
                'fills':len(filled),'shares':math.fsum(r['filled_shares'] for r in filled),
                'recorded_empty_ledger_pnl_usd':pnl,
                'economic_pnl_usd':pnl if filled else None,
                'fees_usd':math.fsum(r['fill_fee'] for r in filled),
                'cost_usd':math.fsum(r['all_in_cost']*r['filled_shares'] for r in filled),
                'reasons':dict(Counter(r['reason'] for r in sample)),
                'rank7_pnl_usd':None,'formal_oos_pnl_usd':None}
    matched={d:signatures[('baseline',d)]==signatures[('confirmation_veto',d)]
             for d in result['arms']['baseline']['latencies']}
    return {'result_sha256':digest(rp),'rows_file_sha256':digest(p),
            'rows_canonical_sha256':canonical36(rows),'rows':len(rows),
            'unsent_confirmation_rejections_without_execution_health_fields':sum('future_health_checked' not in r for r in rows),
            'groups':groups,'same_positive_fill_ledger_baseline_vs_veto':matched,
            'classification':'development_no_confirmation_veto_increment_identified'}

def audit(root):
    return {'schema':'published-families-reconciliation-v1',
            'family35':audit_family35(root),'family36':audit_family36(root),
            'new_private_fill_evidence':False,'rank7_pnl_usd':None,
            'formal_oos_result':None,'qualified_mechanisms':0}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('strict_directory')
    a=ap.parse_args()
    print(json.dumps(audit(Path(a.strict_directory)),indent=2,allow_nan=False))
