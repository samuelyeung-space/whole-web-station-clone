"""Audit selected immutable run397949 members, not an execution backtest."""
import argparse
from collections import Counter
import csv
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_ledger_weighting_audit import audit as weighting_audit


def check(root):
    root=Path(root); manifest=json.loads((root/'capture-manifest.json').read_text())
    assert manifest['run_id']=='39794955858'
    catalog={r['path']:r for r in manifest['files']}
    retained={}
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        name=str(path.relative_to(root))
        if name=='capture-manifest.json':continue
        declared=catalog[name];raw=path.read_bytes()
        assert len(raw)==declared['bytes'] and hashlib.sha256(raw).hexdigest()==declared['sha256'],name
        if name.endswith('.gz'):
            with gzip.open(path,'rb') as f:
                while f.read(1024*1024):pass
        retained[name]=declared
    markets=[]
    with gzip.open(root/'raw/2026-10-22/markets.jsonl.gz','rt') as f:
        markets=[json.loads(line) for line in f]
    btc=[r for r in markets if str(r.get('slug','')).startswith('btc-updown-5m-')]
    registrations={}; labels={}
    for r in btc:
        slug=r['slug'];start=int(slug.rsplit('-',1)[1]);assert start%300==0
        labels_order=json.loads(r['outcomes']);assert labels_order==['Up','Down']
        tokens=json.loads(r['clobTokenIds']);identity=(r['conditionId'],tuple(tokens))
        assert slug not in registrations or registrations[slug]==identity
        registrations[slug]=identity
        prices=json.loads(r['outcomePrices'])
        if r.get('closed') and prices in (['1','0'],['0','1']):
            label=prices[0]=='1'
            assert slug not in labels or labels[slug]==label
            labels[slug]=label
    with gzip.open(root/'raw/2026-10-22/decisions.jsonl.gz','rt') as f:
        decisions=[json.loads(line) for line in f]
    opportunity=Counter();decision_by_key={};decision_ages=[];decision_rows=[]
    for r in decisions:
        remaining=(r['end']*1000-r['decided_ms'])/1000
        tau=min((30,60),key=lambda x:abs(x-remaining))
        assert abs(tau-remaining)<1
        if 'tau' in r:assert r['tau']==tau
        identity=(r['slug'],tau);assert identity not in decision_by_key
        decision_by_key[identity]=r
        status=r.get('skip','paper_entry')
        opportunity[(tau,status)]+=1
        # The saved BTC timestamp is source time, not local receive time.
        age=r['decided_ms']-r['btc'][0]
        assert age>=0
        decision_ages.append(age)
        decision_rows.append({'slug':r['slug'],'tau_s':tau,'status':status,
                              'source_event_age_ms':age,'source_receive_age_ms':None})
    ledger=list(csv.DictReader((root/'ledger.csv').open()))
    joined=[];entry_ages=[];fee_sum=Decimal(0)
    for row in ledger:
        identity=(row['slug'],int(row['tau']));decision=decision_by_key[identity]
        assert not decision.get('skip') and identity[0] in labels
        for k in ('ask','shares','fee','cost','side','decided_ms'):
            assert str(decision[k])==str(row[k]) or (k not in ('side',) and abs(Decimal(str(decision[k]))-Decimal(row[k]))<Decimal('1e-8')),k
        won=(row['side']=='Up')==labels[row['slug']]
        assert won==(row['won']=='True')
        quantity=Decimal(row['shares']);price=Decimal(row['ask']);fee=Decimal(row['fee'])
        expected_fee=quantity*Decimal('.07')*price*(1-price)
        assert abs(expected_fee-fee)<Decimal('1e-8')
        cost=quantity*price+fee;net=(quantity if won else 0)-cost
        assert abs(cost-Decimal(row['cost']))<Decimal('1e-8')
        assert abs(net-Decimal(row['pnl']))<Decimal('1e-8')
        fee_sum+=fee
        age=decision['decided_ms']-decision['btc'][0];entry_ages.append(age)
        joined.append({'slug':row['slug'],'tau_s':identity[1],'condition_id':registrations[row['slug']][0],
                       'official_up_label':labels[row['slug']],'source_event_age_ms':age})
    def age_summary(values):
        return dict(min_ms=min(values),max_ms=max(values),over_60000_ms=sum(v>60000 for v in values),
                    source_receive_latency_identified=False,used_to_filter_pnl=False)
    runtime=json.loads((root/'capture-runtime.json').read_text());health=json.loads((root/'capture-health.json').read_text())
    snapshots=[]
    for slug in sorted(registrations):
        rows=[r for r in btc if r['slug']==slug]
        for r in rows:
            assert r['resolutionSource']=='https://data.chain.link/streams/btc-usd-twap-60s-streams'
        snapshots.append({'slug':slug,'condition_id':registrations[slug][0],
            'tick_sizes':sorted({r['orderPriceMinTickSize'] for r in rows}),
            'min_order_sizes':sorted({r['orderMinSize'] for r in rows}),
            'fee_rates':sorted({r['feeSchedule']['rate'] for r in rows if 'feeSchedule' in r}),
            'resolved_label_available':slug in labels})
    return dict(schema='397949-selected-raw-ledger-audit-v1',run_id=39794955858,
        selected_member_identities_verified=retained,selected_gzip_full_crc_decoded=True,
        full_capture_archive_verified=False,full_clob_input_verified=False,
        btc_registered_markets=len(registrations),btc_gamma_resolved_markets=len(labels),
        ledger_rows=len(ledger),ledger_markets=len({r['slug'] for r in ledger}),
        ledger_official_labels_reconciled=len(joined),ledger_fee_cost_pnl_reconciled=True,
        ledger_joined_official_results=joined,decision_opportunities=len(decisions),
        decisions=[dict(tau_s=tau,status=status,count=n) for (tau,status),n in sorted(opportunity.items())],
        source_event_age_all_decisions=age_summary(decision_ages),
        source_event_age_paper_entries=age_summary(entry_ages),decision_age_rows=decision_rows,
        weighting=weighting_audit(root/'ledger.csv'),market_snapshots=snapshots,
        tail_rtds_enqueue_age_ms=health['at']-health['writer']['latest_stream_enqueue_ms']['rtds'],
        recorder_span_hours=(runtime['ended_ms']-runtime['started_ms'])/3600000,
        collector_region=runtime['collector_region'],feed_health_verified=runtime['feed_health_verified'],
        direct_500_600_1000_execution_replayed=False,independent_1000usd_100share_week_replayed=False,
        full_recorded_utc_days_verified=0,rank7_pnl_usd=None,formal_oos_result=None,
        strategies_promoted=0,qualified_mechanisms=0,orders_submitted=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('selected_root');p.add_argument('--output',required=True)
    a=p.parse_args();result=check(a.selected_root)
    Path(a.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ['ledger_rows','ledger_markets','btc_registered_markets','btc_gamma_resolved_markets','decisions','source_event_age_paper_entries','weighting','tail_rtds_enqueue_age_ms']}))
