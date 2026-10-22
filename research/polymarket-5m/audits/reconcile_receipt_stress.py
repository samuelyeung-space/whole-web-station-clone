"""Reconcile newly executed source-race and separate stress rows (no new replay)."""
from __future__ import annotations
import argparse,hashlib,json,math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from h_reason_accounting import audit_reason_buckets

def close(a,b,tol=1e-7):
    assert math.isclose(float(a),float(b),rel_tol=0,abs_tol=tol),(a,b)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def audit_arm(rows,summary):
    for r in rows:
        assert r['ordering_clock']=='receive_ms' and r['match_book_clock']=='receive_ms'
        assert r['future_health_checked'] is False
        close(r['signal_ms'],r['signal_receive_ms'])
        close(r['evaluation_time_ms'],r['signal_receive_ms']+r['evaluation_ms'],1e-3)
        if r.get('book_receive_ms') is not None:assert r['book_receive_ms']<=r['evaluation_time_ms']+1e-6
        if r['filled']:
            assert r['sent'] and r['filled_shares']>0
            assert r['evaluation_time_ms']>=r['earliest_match_ms']-1e-6
            assert r['fill_price']<=r['fixed_limit']+1e-9
            close(r['all_in_cost']*r['filled_shares'],r['fill_price']*r['filled_shares']+r['fill_fee'])
            close(r['pnl'],(int(r['winner']==r['direction'])-r['all_in_cost'])*r['filled_shares'])
        else:close(r['filled_shares'],0)
    out={}
    for d,m in summary['latencies'].items():
        rr=[r for r in rows if r['evaluation_ms']==float(d)]
        ff=[r for r in rr if r['filled']]
        sent=sum(r['sent'] for r in rr);shares=math.fsum(r['filled_shares'] for r in ff)
        pnl=math.fsum(r['pnl'] for r in ff);fees=math.fsum(r['fill_fee'] for r in ff)
        cost=math.fsum(r['all_in_cost']*r['filled_shares'] for r in ff)
        assert sent==m['sent'] and len(ff)==m['fills']
        close(pnl,m['pnl']);close(shares,m['filled_shares'])
        reason_total=sum(m['reasons'].values())
        out[d]={'signals':m['signals'],'retained_rows':len(rr),'sends':sent,
            'failed_sends':sent-len(ff),'fills':len(ff),'markets_filled':len({r['market_id'] for r in ff}),
            'shares':shares,'fees_usd':fees,'cost_usd':cost,'pnl_usd':pnl if ff else None,
            'net_cents_per_share':100*pnl/shares if shares else None,
            'day_cluster_lower_99':None,'exact_fair_price_p':m['exact_p'],
            'losses':sum(r['won'] is False for r in ff),
            'max_recorded_order_shares':max((r['filled_shares'] for r in ff),default=None),
            'cash_floor_if_no_payouts_reused_from_1000':1000-cost,
            'cash_floor_scope':'recorded small-size fills only, not fixed100; all payouts withheld to end',
            'summary_reason_missing_count':m['signals']-reason_total,
            'retained_row_reasons':{k:sum(r['reason']==k for r in rr) for k in sorted({r['reason'] for r in rr})},
            'rank7_pnl_usd':None,'formal_oos_result':None}
    return out

def main(root):
    frozen=json.loads((root/'source_race_result.json').read_text())
    stress=json.loads((root/'stress/result.json').read_text())
    identity=frozen['artifact_identity']
    assert str(identity['run_id'])==str(stress['artifact_identity']['run_id'])
    assert identity['files']==stress['artifact_identity']['files']
    out={'schema':'new-receipt-and-stress-reconciliation-v2','run_id':int(identity['run_id']),
        'registered_comparison':{},'separate_development_stress':{},
        'source_race_formal_sample_eligible':frozen['formal_sample_eligible'],
        'source_race_activation_gate':frozen['activation_gate'],
        'formal_oos_result':None,'rank7_pnl_usd':None,'positive_pnl_is_confirmed_alpha':False}
    for arm in ['baseline','candidate']:
        p=root/'rows'/(arm+'.jsonl');rows=[json.loads(l) for l in p.read_text().splitlines()]
        assert digest(p)==frozen['observation_files'][arm]['sha256']
        out['registered_comparison'][arm]={'rows_sha256':digest(p),'cells':audit_arm(rows,frozen['arms'][arm])}
        p=root/'stress'/(arm+'.jsonl');rows=[json.loads(l) for l in p.read_text().splitlines()]
        raw=stress['arms'][arm]
        assert digest(p)==raw['rows_sha256']
        corrected=audit_reason_buckets(rows,raw['raw_counters'],raw['summary'])
        out['separate_development_stress'][arm]={'rows_sha256':digest(p),
            'cells':audit_arm(rows,corrected),'reason_accounting':corrected['reason_accounting']}
    return out

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('result_directory');a=ap.parse_args()
    print(json.dumps(main(Path(a.result_directory)),indent=2,allow_nan=False))
