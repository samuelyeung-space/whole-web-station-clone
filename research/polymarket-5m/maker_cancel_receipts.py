"""Offline audit of explicitly normalized private maker lifecycle receipts.

This post-only resting-BUY schema is not a Polymarket API parser. Retain
the original private messages and attest their order/clock/sequence mapping.
No network, order generation, matching simulation, queue inference or PnL.
"""
from collections import Counter
import argparse
import json
import math
from pathlib import Path

VERSION='maker-cancel-receipts-dev-v1'
EPS=1e-9


def need(ok,message):
    if not ok:raise ValueError('maker receipts: '+message)


def number(row,key,positive=False):
    x=row.get(key)
    need(type(x) in (int,float) and math.isfinite(x) and x>=0 and
         (not positive or x>0),'invalid '+key)
    return x


def identity(row,key):
    x=row.get(key);need(isinstance(x,str) and bool(x.strip()),'missing '+key)
    return x


def venue_sequence(row):
    n=row.get('venue_sequence')
    need(type(n) is int and n>=0,'exact order-lifecycle venue sequence required')
    return n


def reconcile(events):
    """Consume original local receipt order; retain a snapshot after each row.

    Header requires explicit capture/clock IDs and order_lifecycle sequence
    scope. Each source record carries its raw-message SHA256. These are caller
    attestations, not independently authenticated venue provenance.
    cancel_ack reports terminal cumulative filled and canceled quantities.
    Late fill receipts remain real observations if their venue sequence is
    earlier than that acknowledgment; their local arrival is never backdated.
    """
    orders={};snapshots=[];latest={};counts=Counter();header=None;last=-math.inf
    for i,row in enumerate(events):
        need(isinstance(row,dict),'record must be an object')
        if i==0:
            need(row.get('type')=='capture' and row.get('schema')==VERSION,
                 'canonical capture header required')
            need(row.get('split')=='development','development input only')
            need(row.get('venue_sequence_scope')=='order_lifecycle',
                 'explicit per-order lifecycle sequence contract required')
            identity(row,'capture_id');identity(row,'clock_id');header=dict(row);continue
        need(header is not None,'capture header required')
        need(type(row.get('local_sequence')) is int and row['local_sequence']==i,
             'local receipt sequence missing or reordered')
        at=number(row,'local_recv_ms');need(at>=last,'local receipt clock regressed');last=at
        need(row.get('capture_id')==header['capture_id'] and row.get('clock_id')==header['clock_id'],
             'capture or clock domain mismatch')
        digest=row.get('raw_message_sha256')
        need(isinstance(digest,str) and len(digest)==64 and
             all(c in '0123456789abcdef' for c in digest),'raw-message identity required')
        oid=identity(row,'order_id');kind=row.get('type');duplicate=False
        if kind=='submit':
            need(oid not in orders,'duplicate order submission')
            need(row.get('order_side')=='BUY' and row.get('order_type') in ('GTC','GTD')
                 and row.get('post_only') is True,'explicit post-only resting BUY intent required')
            limit=number(row,'fixed_limit',True);need(limit<1,'fixed binary limit outside bounds')
            orders[oid]=dict(requested=number(row,'requested_shares',True),limit=limit,
                hold=number(row,'recorded_order_hold_usd',True),filled=0.,trades={},fill_sequences=set(),
                cancels={},terminal_ack=None,fill_after_request=0.,fill_after_ack=0.,
                place_ack_seen=False,market_id=identity(row,'market_id'),token_id=identity(row,'token_id'))
        else:
            need(oid in orders,'receipt lacks prior local submission');s=orders[oid]
            need(row.get('market_id')==s['market_id'] and row.get('token_id')==s['token_id'],
                 'order market/token identity changed')
            if kind=='cancel_request':
                cid=identity(row,'cancel_id');need(cid not in s['cancels'],'duplicate cancel request ID')
                need(s['terminal_ack'] is None,'cancel request after terminal cancellation')
                s['cancels'][cid]=dict(request_recv_ms=at,state='requested')
            elif kind in ('cancel_ack','cancel_reject'):
                cid=identity(row,'cancel_id');need(cid in s['cancels'],'cancel response lacks request')
                request=s['cancels'][cid];need(request['state']=='requested','duplicate cancel response')
                request['state']=kind
                if kind=='cancel_ack':
                    need(s['terminal_ack'] is None,'multiple terminal cancel acknowledgments')
                    q=number(row,'cumulative_filled_shares');c=number(row,'canceled_shares')
                    need(abs(q+c-s['requested'])<=EPS,'cancel quantities do not partition original order')
                    need(q+EPS>=s['filled'],'cancel acknowledgment omits received fills')
                    seq=venue_sequence(row)
                    need(max(s['fill_sequences'],default=-1)<seq,'fill follows terminal cancel venue sequence')
                    s['terminal_ack']=dict(venue_sequence=seq,filled=q,local_recv_ms=at)
            elif kind=='place_ack':
                need(not s['place_ack_seen'],'duplicate place acknowledgment')
                s['place_ack_seen']=True
            elif kind=='fill':
                tid=identity(row,'trade_id');q=number(row,'filled_shares',True)
                price=number(row,'fill_price',True);seq=venue_sequence(row)
                need(price<1 and price<=s['limit']+EPS,'fill exceeds original fixed limit')
                signature=(q,price,seq)
                if tid in s['trades']:
                    need(s['trades'][tid]==signature,'conflicting duplicate fill identity');duplicate=True
                else:
                    need(seq not in s['fill_sequences'],'two distinct fills share venue sequence')
                    need(s['filled']+q<=s['requested']+EPS,'fills exceed original quantity')
                    ack=s['terminal_ack']
                    if ack:
                        need(seq<ack['venue_sequence'],'fill follows terminal cancel venue sequence')
                        need(s['filled']+q<=ack['filled']+EPS,'late fills exceed acknowledged cumulative quantity')
                        s['fill_after_ack']+=q
                    if s['cancels']:s['fill_after_request']+=q
                    s['trades'][tid]=signature;s['fill_sequences'].add(seq);s['filled']+=q
            else:raise ValueError('maker receipts: unsupported lifecycle record')
        s=orders[oid];ack=s['terminal_ack']
        terminal=(bool(s['trades']) and abs(s['filled']-s['requested'])<=EPS or
                  bool(ack and abs(s['filled']-ack['filled'])<=EPS))
        counts[kind]+=1
        if duplicate:counts['duplicate_fill_receipts_retained']+=1
        snapshots.append(dict(local_sequence=i,local_recv_ms=at,order_id=oid,type=kind,
            duplicate_fill_receipt=duplicate,received_filled_shares=s['filled'],
            pending_acknowledged_fill_shares=max(0.,ack['filled']-s['filled']) if ack else None,
            terminal_quantity_reconciled=terminal,
            unreconciled_order_hold_usd=0. if terminal else s['hold'],
            fill_received_after_cancel_request_shares=s['fill_after_request'],
            fill_received_after_cancel_ack_shares=s['fill_after_ack']))
        latest[oid]=snapshots[-1]
    need(header is not None,'empty capture')
    final={}
    for oid,row in latest.items():
        final[oid]=dict(row,status='terminal_quantity_reconciled' if
            row['terminal_quantity_reconciled'] else 'pending_private_receipts')
    return dict(schema=VERSION,capture_id=header['capture_id'],clock_id=header['clock_id'],
        classification='private_receipt_accounting_not_execution_replay',counts=dict(counts),
        snapshots=snapshots,orders=final,
        holds_are_not_spendable_cash_or_cash_ledger=True,raw_messages_independently_authenticated=False,
        public_depth_is_not_queue_evidence=True,queue_position=None,financial_replays=0,
        pnl_usd=None,rank7_pnl_usd=None,formal_oos_result=None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    with open(a.input) as f:
        result=reconcile(json.loads(line) for line in f if line.strip())
    Path(a.out).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
