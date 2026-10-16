"""Independent accounting/readout of a completed MFA grid; never rescore data."""
import argparse,csv,gzip,json,math,platform
import numpy,sklearn
from collections import Counter,defaultdict
from pathlib import Path
from datetime import datetime,timezone
from replay_lab import verify_report_integrity
from minimal_feature_strategy import candidates


def close(a,b):
    if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-7):raise ValueError(f'accounting mismatch {a} != {b}')


def assess(root):
    root=Path(root);batch=json.loads((root/'batch_completion.json').read_text())
    if batch['status']!='completed_development_simulation':raise ValueError('unfinished batch')
    totals=Counter();modes={};signatures={};market_sets={};daily={}
    for mode,metadata in batch['outputs'].items():
        path=Path(metadata['run_path']);completion=json.loads((path/'completion.json').read_text())
        registration=json.loads((path/'registration.json').read_text())
        verify_report_integrity(path,registration['scenarios'],completion['output_sha256'])
        metrics=json.loads((path/'metrics.json').read_text());totals['summary_rows']+=len(metrics)
        if len(metrics)!=(72 if mode=='isolated' else 9):raise ValueError('grid row count mismatch')
        for scenario in registration['scenarios']:
            scenario_name=scenario['name'];directory=path/scenario_name
            rows=[]
            with gzip.open(directory/'attempts.jsonl.gz','rt') as stream:
                for line in stream:
                    row=json.loads(line);rows.append(row);totals['attempt_rows']+=1
                    close(row['requested_shares'],100)
                    close(row['match_ms'],row['decision_ms']+row['latency_ms'])
                    qty=row['filled_shares']
                    if not -1e-9<=qty<=100+1e-9:raise ValueError('invalid fill quantity')
                    close(row['cost_usd'],qty*(row['fill_vwap'] or 0)+row['fees_usd']+row['extra_cost_usd'])
                    if qty:
                        if row['fill_vwap']>row['limit_price']+1e-9:raise ValueError('fill above frozen limit')
                        if not all(row[k] and row[k]['validated'] for k in ('constraints_at_decision','constraints_at_match')):
                            raise ValueError('filled order without validated native market constraints')
                    if row['settled']:
                        close(row['net_pnl_usd'],row['payout_usd']-row['cost_usd'])
                        close(row['payout_usd'],qty if row['winner']==row['side'] else 0)
            groups=defaultdict(list)
            for row in rows:groups[(row['candidate_id'],row['latency_ms'])].append(row)
            cash_groups=defaultdict(list)
            with (directory/'cash_ledger.csv').open(newline='') as stream:
                for row in csv.DictReader(stream):
                    totals['cash_events']+=1
                    if row['report_cash_basis']!='engine_recorded_event':raise ValueError('not actual engine cash trace')
                    cash_groups[(row['candidate_id'],int(row['latency_ms']))].append(row)
                    if float(row['cash_usd'])-float(row['reserved_usd']) < -1e-7:raise ValueError('negative available cash')
            for summary in (x for x in metrics if x['scenario']==scenario_name):
                key=(summary['candidate_id'],summary['latency_ms']);attempts=groups[key];cash=cash_groups[key]
                close(summary['initial_cash_usd'],1000);close(summary['requested_shares'],100)
                close(summary['attempts'],len(attempts));close(summary['filled_shares'],sum(a['filled_shares'] for a in attempts))
                close(summary['full_fills']+summary['partial_fills'],sum(a['filled_shares']>1e-9 for a in attempts))
                close(summary['fees_usd'],sum(a['fees_usd'] for a in attempts))
                expected_cash=1000-sum(a['cost_usd'] for a in attempts)+sum(a.get('payout_usd') or 0 for a in attempts)
                close(summary['ending_cash_usd'],expected_cash)
                if summary['net_pnl_usd'] is not None:
                    close(summary['net_pnl_usd'],sum(a['net_pnl_usd'] or 0 for a in attempts))
                if cash:
                    close(float(cash[-1]['cash_usd']),summary['ending_cash_usd'])
                    close(min([1000]+[float(c['cash_usd'])-float(c['reserved_usd']) for c in cash]),summary['min_available_cash_usd'])
                    peak=1000;dd=0
                    for c in cash:
                        eq=1000+float(c['realized_pnl_usd']);peak=max(peak,eq);dd=max(dd,peak-eq)
                    close(dd,summary['max_realized_drawdown_usd'])
                if scenario_name=='shares100_base_release0' and summary['latency_ms']==500:
                    cid=summary['candidate_id'];signatures[(mode,cid)]=[(a['market_id'],a['decision_ms'],a['side'],a['status'],a['filled_shares'],a['net_pnl_usd']) for a in attempts]
                    market_sets[(mode,cid)]={a['market_id'] for a in attempts}
                    perday=defaultdict(lambda:{'attempts':0,'filled_orders':0,'filled_shares':0.,'settled_pnl_usd':0.})
                    for a in attempts:
                        day=datetime.fromtimestamp(a['decision_ms']/1000,timezone.utc).date().isoformat();d=perday[day]
                        d['attempts']+=1;d['filled_orders']+=a['filled_shares']>1e-9;d['filled_shares']+=a['filled_shares'];d['settled_pnl_usd']+=a['net_pnl_usd'] or 0
                    daily[cid]=dict(perday)
        modes[mode]={'run_path':str(path),'metrics':metrics,'coverage':json.loads((path/'coverage.json').read_text())}
    base=[r for r in modes['isolated']['metrics'] if r['scenario']=='shares100_base_release0' and r['latency_ms']==500]
    valid=[r for r in base if r['status']=='simulated_complete' and r['net_pnl_usd'] is not None]
    ranked=sorted(valid,key=lambda r:r['net_pnl_usd'],reverse=True)
    duplicate_groups=[];seen=set()
    for key,sig in signatures.items():
        if key[0]!='isolated' or key in seen or not sig:continue
        group=[k[1] for k,s in signatures.items() if k[0]=='isolated' and s==sig]
        if len(group)>1:duplicate_groups.append(group)
        seen.update(('isolated',c) for c in group)
    overlap=[];ids=[c['id'] for c in candidates()]
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            x=market_sets.get(('isolated',a),set());y=market_sets.get(('isolated',b),set());union=x|y
            overlap.append({'a':a,'b':b,'shared_signal_markets':len(x&y),'union_signal_markets':len(union),'jaccard':len(x&y)/len(union) if union else None})
    result={'schema':'mfa-completed-assessment-v1','modes':modes,'base500_all':base,'base500_ranked_complete_only':ranked,
        'base500_status_counts':dict(Counter(r['status'] for r in base)),
        'base500_positive_variants':sum(r['net_pnl_usd']>0 for r in valid),'identical_primary_ledgers':duplicate_groups,
        'pairwise_signal_market_overlap':overlap,'base500_daily_by_decision_date':daily,'independent_audit_counts':dict(totals),
        'new_candidates':8,'economic_scenario_rows':81,'qualified_independent_mechanisms':0,'formal_oos_result':None,
        'runtime':{'python':platform.python_version(),'numpy':numpy.__version__,'sklearn':sklearn.__version__},
        'inference_limits':['All inputs are previously-consumed Oct7-9 development, not seven complete days or OOS.',
            'Prior-day fitting does not restore independent holdout status; all8 additional trials retain screening history.',
            'Only two scored receipt dates; no robust day-cluster significance claim or monthly scaling.',
            'Same-market signals, training alternatives, latencies and stresses cannot be summed.',
            'Depth simulation upper bound; no observed fills or actual redemption timings.']}
    (root/'assessment.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'audit':dict(totals),'statuses':result['base500_status_counts'],
        'rank':[(r['candidate_id'],r['net_pnl_usd'],r['attempts']) for r in ranked]}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    assess(p.parse_args().root)
