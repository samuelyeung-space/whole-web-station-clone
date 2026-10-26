"""Run quote-only gates on consumed raw data, with frozen parent arm times.

Does not execute FAKs or rank strategies. Produces auditable receipt-causal
decisions to feed a separately validated off-grid execution projection.
"""
import argparse
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from atomic_quote_groups import AtomicBooks,receipt_groups
from book_balance_vacuum import Config,QuoteGates
from h_replay_archive import _iter_json_objects


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--protocol',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();protocol=json.load(open(args.protocol))
    for source in protocol['inputs'].values():
        if sha(source['path'])!=source['sha256']:raise ValueError('input hash mismatch')
    for name,expected in protocol['code_sha256'].items():
        if sha(Path(__file__).parent/name)!=expected:raise ValueError('code hash mismatch: '+name)
    cohort=protocol['cohort']; pending=list(sorted(cohort,key=lambda x:x['decision_ms']))
    assets={r['asset_id'] for r in cohort}; by_asset={r['asset_id']:r for r in cohort}
    if len(assets)!=len(cohort):raise ValueError('this intake requires distinct one-arm tokens')
    books=AtomicBooks(assets); gates=QuoteGates(Config(**protocol['gate_config']))
    end=max(r['decision_ms'] for r in cohort)+gates.config.deadline_ms
    counts=Counter(); observed_until=None; before_gate=[]; arm_observations=[]
    def arm(r):
        key=r['market_id']; existing=gates.books.get(key)
        quote=existing['quote'] if existing else None
        initial_mid=quote['mid'] if quote else .5
        accepted=gates.arm(key,1,initial_mid,r['decision_ms'],epoch=books.epoch)
        # Always +1 in the bought token coordinate, including a Down contract.
        arm_observations.append(dict(**r,accepted=accepted,token_coordinate_direction=1,
            initial_quote_recv_ms=existing['time'] if existing else None,
            initial_ask=quote['asks'][0][0] if quote else None,
            status=gates.arms[key]['status']))
    rows=(r for _,r in _iter_json_objects(protocol['inputs']['raw_clob']['path']))
    for group in receipt_groups(rows):
        now=group[0]['recv_ms']
        while pending and pending[0]['decision_ms']<=now:
            arm(pending.pop(0)) # before equal-time raw updates: conservative tie order
        if now>end:
            gates.advance_time(end);break
        observed_until=now;counts['rows']+=len(group);counts['receipt_groups']+=1
        gates.advance_time(now)
        reset=(books.epoch!=group[0]['connection_epoch'] or any(
            r['kind'] in ('clob_connection','clob_error') for r in group))
        if reset:
            for key in list(gates.books):gates.disconnect(key,now,'source_lifecycle')
        for obs in books.apply(group):
            counts['selected_asset_groups']+=1
            if obs['ambiguous_receipt']:counts['ambiguous_selected_groups']+=1
            if not obs['validated']:counts['invalid_selected_groups']+=1
            r=by_asset[obs['asset_id']]; key=r['market_id'];book=obs['book']
            emitted=gates.on_book(key,now,obs['epoch'],book.bids,book.asks,
                update_id=obs['update_id'],validated=obs['validated'],
                ambiguous_receipt=obs['ambiguous_receipt'])
            for event in emitted:
                if event['recv_ms']<r['decision_ms']:raise AssertionError('future parent arm')
                initial=next(a for a in arm_observations if a['market_id']==key)
                before_gate.append(dict(**event,asset_id=r['asset_id'],parent_side=r['side'],
                    parent_decision_ms=r['decision_ms'],ask=min(book.asks),bid=max(book.bids),
                    initial_ask=initial['initial_ask'],
                    cheaper_than_parent_ask=min(book.asks)<initial['initial_ask']-1e-12,
                    matching_snapshots_built=False,economic_pnl=None))
    else:
        # Only elapsed observed time may expire a watch. EOF never fills it.
        if observed_until is not None:gates.advance_time(observed_until)
    terminal=Counter(a['status'] for a in gates.arms.values())
    result=dict(schema='book-gate-raw-intake-v1',protocol_sha256=sha(args.protocol),
        evidence_class='consumed_development_quote_diagnostic',cohort_size=len(cohort),
        counts=dict(counts),observed_until_ms=observed_until,
        arm_observations=arm_observations,terminal_status_counts=dict(terminal),
        gate_events=before_gate,gate_count=len(before_gate),audit=gates.audit(),
        physical_frames_proven=False,native_aggressor_flow_proven=False,
        pm_full_l2=True,btc_full_l2=False,financial_replay_completed=False,
        formal_oos_result=None,full_week_pnl=None,net_pnl_usd=None)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('cohort_size','counts','terminal_status_counts','gate_count','net_pnl_usd')}))


if __name__=='__main__':main()
