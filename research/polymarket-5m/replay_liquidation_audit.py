"""Read-only indicative liquidation-risk audit of completed replay ledgers.

Never changes fills, signals, cash, rules or freezes. Not an executable exit
strategy: quotes are displayed-depth upper bounds, not confirmed liquidation.
Missing/stale/closed-market inventory remains an interval, never a zero mark.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path

from replay_engine import _fee, _rounded_order_fee

VERSION = 'indicative-liquidation-audit-v1'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def mark_position(quantity, side, book, now, end_ms, max_age=250):
    """Conditional quote-model interval; unpriced shares have [0,1] payoff.

    Healthy full depth makes the interval a point, but the resulting bid mark
    still assumes hypothetical instantaneous sale at the observed quotes.
    Bounds are not guaranteed actual cash/wealth or statistical confidence.
    """
    def absent(reason):
        return 0., float(quantity), float(quantity), reason
    if side not in ('Up', 'Down') or not number(quantity) or quantity < 0:
        raise ValueError('invalid position')
    if not number(end_ms) or now >= end_ms:
        return absent('closed_or_unknown_market')
    if not isinstance(book, dict):
        return absent('missing_book')
    key = side.lower()
    received = book.get(key + '_recv_ms')
    if not number(book.get('recv_ms')) or not number(received):
        return absent('missing_native_receipt')
    if received > book['recv_ms'] or book['recv_ms'] > now or not 0 <= now-received <= max_age:
        return absent('stale_or_future_native_book')
    if book.get(key + '_healthy') is not True:
        return absent('unhealthy_native_book')
    fee = book.get('fee_rate')
    if not number(fee) or not 0 <= fee <= 1:
        return absent('missing_fee')
    native_bids = book.get(key + '_bids')
    if not isinstance(native_bids, list):
        return absent('missing_native_bids')
    bids = defaultdict(float)
    for level in native_bids:
        if (not isinstance(level, (list, tuple)) or len(level) != 2
                or not number(level[0]) or not 0 < level[0] < 1
                or not number(level[1]) or level[1] < 0):
            return absent('invalid_native_bids')
        bids[level[0]] += level[1]
    remaining = float(quantity);gross = 0.;raw_fee = _fee(0, .5, fee)
    for price, size in sorted(bids.items(), reverse=True):
        sold = min(remaining, size)
        gross += sold*price;raw_fee += _fee(sold, price, fee);remaining -= sold
        if remaining <= 1e-9:
            remaining = 0.;break
    proceeds = gross-float(_rounded_order_fee(raw_fee))
    return proceeds, proceeds+remaining, remaining, 'insufficient_bid_depth' if remaining else 'fully_marked'


class Account:
    def __init__(self, mode, candidate_id, latency_ms, attempts, cash_events, initial=1000):
        self.mode=mode;self.cid=candidate_id;self.latency=latency_ms;self.initial=initial
        self.attempts={str(a['attempt_id']):a for a in attempts}
        if len(self.attempts)!=len(attempts):raise ValueError('duplicate attempt id in account')
        self.events=sorted(cash_events,key=lambda r:(r['event_ms'],r['event_sequence']))
        self.index=0;self.cash=float(initial);self.reserved=0.;self.locked=0.;self.positions={}
        self.peak_low=float(initial);self.peak_high=float(initial);self.dd_low=0.;self.dd_high=0.
        self.samples=0;self.open_samples=0;self.fully_marked_open_samples=0;self.reasons=Counter()
        self.max_unknown=0.;self.max_width=0.;self.minimum_low=float(initial);self.minimum_high=float(initial)
        self.last=None;self.next_expiry=None

    def apply_until(self, now):
        while self.index<len(self.events) and self.events[self.index]['event_ms']<=now:
            r=self.events[self.index];self.index+=1
            a=self.attempts[str(r['attempt_id'])];aid=str(r['attempt_id'])
            self.cash=r['cash_usd'];self.reserved=r['reserved_usd'];self.locked=r['locked_payout_usd']
            if r['event_type']=='fill':
                if not a['filled_shares']>0 or a['match_ms']!=r['event_ms'] or aid in self.positions:
                    raise ValueError('cash fill does not identify one observed filled attempt')
                self.positions[aid]=a
            elif r['event_type']=='settlement':
                if aid not in self.positions or a['settlement_ms']!=r['event_ms']:
                    raise ValueError('settlement without an open position')
                del self.positions[aid]
            if self.cash-self.reserved < -1e-7:raise ValueError('negative available cash')

    def snapshot(self, now, books, market_ends):
        self.apply_until(now)
        # Cash includes reservations; subtracting them from wealth would count
        # a pending order twice. Locked known payout is a receivable, not cash.
        low=high=self.cash+self.locked;unknown=0.;reasons=Counter();expiries=[]
        inventory=defaultdict(float)
        for a in self.positions.values():inventory[(a['market_id'],a['side'])]+=a['filled_shares']
        # Aggregate same-side holdings before walking depth, never reuse one
        # observed bid queue for two positions inside the same account.
        for (market,side),quantity in inventory.items():
            b=books.get(market);end=market_ends.get(market)
            lo,hi,missing,reason=mark_position(quantity,side,b,now,end)
            low+=lo;high+=hi;unknown+=missing;reasons[reason]+=1
            received=(b or {}).get(side.lower()+'_recv_ms')
            if number(received) and received+250>=now:expiries.append(math.nextafter(received+250,math.inf))
            if number(end) and end>now:expiries.append(end)
        self.next_expiry=min(expiries) if expiries else None
        self.samples+=1;self.open_samples+=bool(inventory)
        self.fully_marked_open_samples+=bool(inventory) and unknown<=1e-9
        self.reasons.update(reasons);self.max_unknown=max(self.max_unknown,unknown);self.max_width=max(self.max_width,high-low)
        self.minimum_low=min(self.minimum_low,low);self.minimum_high=min(self.minimum_high,high)
        # Peaks must be strictly earlier: one uncertain interval cannot be at
        # its optimistic high and pessimistic low at the same instant.
        self.dd_low=max(self.dd_low,self.peak_low-high);self.dd_high=max(self.dd_high,self.peak_high-low)
        self.peak_low=max(self.peak_low,low);self.peak_high=max(self.peak_high,high)
        self.last={'recv_ms':now,'cash_usd':self.cash,'reserved_usd':self.reserved,
            'available_cash_usd':self.cash-self.reserved,'locked_known_payout_usd':self.locked,
            'open_positions':len(self.positions),'unknown_shares':unknown,
            'quote_model_equity_low_usd':low,'quote_model_equity_high_usd':high}
        return {'mode':self.mode,'candidate_id':self.cid,'latency_ms':self.latency,**self.last}

    def result(self):
        if self.index!=len(self.events):raise ValueError('cash events remain unprocessed')
        return {'mode':self.mode,'candidate_id':self.cid,'latency_ms':self.latency,
            'snapshots':self.samples,'open_inventory_snapshots':self.open_samples,
            'fully_marked_open_snapshots':self.fully_marked_open_samples,
            'fully_marked_open_snapshot_fraction':self.fully_marked_open_samples/self.open_samples if self.open_samples else None,
            'quote_model_drawdown_low_usd':max(0.,self.dd_low),'quote_model_drawdown_high_usd':max(0.,self.dd_high),
            'minimum_equity_low_usd':self.minimum_low,'minimum_equity_high_usd':self.minimum_high,
            'max_unknown_shares':self.max_unknown,'max_equity_interval_width_usd':self.max_width,
            'position_snapshot_reasons':dict(self.reasons),'final':self.last}


def audit(events_path, assessment_path, output):
    out=Path(output)
    if out.exists():raise ValueError('output path already exists; preserve earlier attempts')
    out.mkdir(parents=True)
    events_path=Path(events_path);assessment_path=Path(assessment_path)
    assessment=json.loads(assessment_path.read_text());accounts=[];inputs={}
    summaries={};all_cash_times=set();source='shares100_base_release0'
    for mode,metadata in assessment['modes'].items():
        run=Path(metadata['run_path']);p=run/source
        attempts=list(rows(p/'attempts.jsonl.gz'))
        with (p/'cash_ledger.csv').open(newline='') as stream:cash=list(csv.DictReader(stream))
        ag=defaultdict(list);cg=defaultdict(list)
        for a in attempts:ag[(a['candidate_id'],a['latency_ms'])].append(a)
        for r in cash:
            if r['report_cash_basis']!='engine_recorded_event':raise ValueError('not engine cash trace')
            for key in ('event_ms','cash_usd','reserved_usd','locked_payout_usd'):r[key]=float(r[key])
            r['latency_ms']=int(r['latency_ms']);r['event_sequence']=int(r['event_sequence'])
            cg[(r['candidate_id'],r['latency_ms'])].append(r);all_cash_times.add(r['event_ms'])
        for key,aa in ag.items():accounts.append(Account(mode,*key,aa,cg[key]))
        for r in metadata['metrics']:
            if r['scenario']==source:summaries[(mode,r['candidate_id'],r['latency_ms'])]=r
        for f in ('attempts.jsonl.gz','cash_ledger.csv'):inputs[mode+'/'+f]=sha256(p/f)
    registration={'schema':VERSION,'events_sha256':sha256(events_path),'assessment_sha256':sha256(assessment_path),
        'ledger_sha256':inputs,'code_sha256':sha256(__file__),'accounts':len(accounts),
        'policy':'read-only; all base500/600/1000 accounts; no new economic replay or strategy trial',
        'limits':['Conditional displayed-bid model, not actual liquidation or risk guarantees.',
            'No order routing/wire latency, sale minNotional/minOrderSize/tick or real fill feedback is modeled.',
            'Sell fee uses the original recorded rate and disclosed aggregate rounding proxy.',
            'Missing/closed/stale remainder is [0,1]/share, not zero; no backward fill or future label.',
            'Snapshots are after equal-timestamp events; no all-tick continuous path assertion.',
            'Quote expiry and market close create independent audit timestamps; sampling is not future-health selection.']}
    (out/'registration.json').write_text(json.dumps(registration,indent=2)+'\n')
    books={};ends={};cash_times=iter(sorted(all_cash_times));next_cash=next(cash_times,None)
    snapshots=0;read_events=0;last_time=None
    with gzip.open(out/'snapshots.jsonl.gz','wt') as sink:
        def snapshot(when, changed_markets=()):
            nonlocal snapshots,last_time
            if last_time is not None and when<=last_time:raise ValueError('audit time did not advance')
            for account in accounts:
                old_index=account.index;account.apply_until(when)
                relevant=any(a['market_id'] in changed_markets for a in account.positions.values())
                expired=account.next_expiry is not None and account.next_expiry<=when
                if account.last is None or account.index!=old_index or relevant or expired:
                    r=account.snapshot(when,books,ends);sink.write(json.dumps(r,separators=(',',':'),allow_nan=False)+'\n');snapshots+=1
            last_time=when
        def intermediate(before):
            nonlocal next_cash
            while True:
                candidates=[a.next_expiry for a in accounts if a.next_expiry is not None]
                if next_cash is not None:candidates.append(next_cash)
                at=min(candidates) if candidates else None
                if at is None or at>=before:break
                snapshot(at)
                if next_cash is not None and at==next_cash:next_cash=next(cash_times,None)
        previous=-math.inf
        for when,group in itertools.groupby(rows(events_path),key=lambda r:r['recv_ms']):
            if not number(when) or when<previous:raise ValueError('unsorted/nonfinite receipt clock')
            previous=when;intermediate(when);changed=False;changed_markets=set()
            for r in group:
                read_events+=1;kind=r.get('type')
                if kind=='market':ends[r['market_id']]=r['end_ms'];changed=True;changed_markets.add(r['market_id'])
                elif kind=='book':books[r['market_id']]=r;changed=True;changed_markets.add(r['market_id'])
            if next_cash is not None and next_cash==when:changed=True;next_cash=next(cash_times,None)
            if changed and accounts and when>=min(a.events[0]['event_ms'] for a in accounts if a.events):snapshot(when,changed_markets)
        intermediate(math.inf)
    results=[]
    for a in accounts:
        r=a.result();s=summaries[(a.mode,a.cid,a.latency)]
        if not math.isclose(a.cash,s['ending_cash_usd'],abs_tol=1e-7):raise ValueError('ending cash mismatch')
        if s['status']=='simulated_complete' and (a.positions or abs(a.locked)>1e-7):raise ValueError('unreconciled terminal inventory')
        r['net_pnl_usd_unchanged']=s['net_pnl_usd'];r['realized_drawdown_usd_unchanged']=s['max_realized_drawdown_usd'];results.append(r)
    result={'schema':VERSION,'registration':registration,'account_results':results,'input_events_read':read_events,
        'account_snapshot_rows':snapshots,'new_economic_replays':0,'new_strategy_trials':0,'formal_oos_result':None,
        'qualified_independent_mechanisms':0,'snapshot_sha256':sha256(out/'snapshots.jsonl.gz')}
    (out/'assessment.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'accounts':len(results),'input_events_read':read_events,'snapshot_rows':snapshots,
        'primary500':[{k:r[k] for k in ('mode','candidate_id','quote_model_drawdown_low_usd','quote_model_drawdown_high_usd','fully_marked_open_snapshot_fraction')} for r in results if r['latency_ms']==500]}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',required=True);parser.add_argument('--assessment',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();audit(args.events,args.assessment,args.output)
