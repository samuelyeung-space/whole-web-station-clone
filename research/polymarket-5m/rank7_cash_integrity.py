"""Independent capital reconstruction for recorded replay-engine cash events.

No signals, matching, training, networking, real redemption or OOS evidence.
Balances start once per candidate/latency; UTC midnight has no special action.
"""
from collections import Counter
import csv
import math

EPS = 1e-7
BALANCES = ('cash_usd', 'reserved_usd', 'locked_payout_usd', 'realized_pnl_usd')


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def need(test, message):
    if not test:
        raise ValueError('rank7 cash: ' + message)


def amount(row, key):
    value = row.get(key)
    need(number(value), 'missing/nonfinite ' + key)
    return float(value)


def same(a, b, message):
    need(number(a) and number(b) and abs(a-b) <= EPS, message)


def account(row):
    cid, lag = row.get('candidate_id'), row.get('latency_ms')
    need(isinstance(cid, str) and bool(cid) and number(lag) and lag in (500,600,1000),
         'invalid candidate/latency account')
    return cid, lag


def read_cash_csv(path):
    rows = []
    with open(path, newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            for key in (*BALANCES, 'latency_ms', 'event_ms'):
                try: row[key] = float(row[key])
                except (KeyError, ValueError, TypeError):
                    raise ValueError('rank7 cash: invalid CSV ' + key) from None
            try: row['event_sequence'] = int(row['event_sequence'])
            except (KeyError, ValueError, TypeError):
                raise ValueError('rank7 cash: invalid CSV event sequence') from None
            rows.append(row)
    return rows


def audit_cash(attempts, events, summaries):
    """Cross-check event transitions, order collateral and terminal summaries.

    Input event order is observed engine order, not an inferred timestamp sort.
    Equal-time release and fill remain atomic for each account. End-of-window
    locked payouts can remain locked, but cannot be spent before their release.
    """
    states, indexed, counts, expected = {}, {}, Counter(), Counter()
    summary_by_key = {}
    for row in summaries:
        key = account(row)
        need(key not in states, 'duplicate summary account')
        same(amount(row,'initial_cash_usd'),1000.,'starting capital must be1000')
        same(amount(row,'requested_shares'),100.,'target quantity must be100')
        states[key] = dict(zip(BALANCES,(1000.,0.,0.,0.)))
        states[key].update(min_available=1000.,peak_equity=1000.,max_drawdown=0.)
        summary_by_key[key] = row
    need(bool(states), 'missing summary accounts')
    for row in attempts:
        key = account(row);aid = row.get('attempt_id')
        need(key in states and isinstance(aid,str) and bool(aid), 'attempt lacks account or ID')
        ident = key+(aid,)
        need(ident not in indexed, 'duplicate attempt identity')
        indexed[ident] = row
        same(amount(row,'requested_shares'),100.,'attempt target is not100')
        d,m=amount(row,'decision_ms'),amount(row,'match_ms')
        same(m-d,key[1],'attempt match does not equal registered total latency')
        q,c=amount(row,'filled_shares'),amount(row,'cost_usd')
        need(0<=q<=100 and c>=0,'invalid filled shares or cost')
        need(type(row.get('submitted')) is bool,'submitted flag required')
        if row['submitted']:
            need(amount(row,'reserved_usd_at_decision')>0,'submitted order has no collateral')
            expected[ident+('reservation',)]+=1
            expected[ident+('reservation_release',)]+=1
        else:
            need(q==c==0,'unsubmitted order filled or debited')
        if q>0:
            need(row['submitted'] and row.get('settled') is True,'filled order unresolved')
            need(amount(row,'fees_usd')>=0 and amount(row,'extra_cost_usd')>=0,'negative cost')
            same(c,q*amount(row,'fill_vwap')+row['fees_usd']+row['extra_cost_usd'],
                 'order notional/fees/cost mismatch')
            need(c<=row['reserved_usd_at_decision']+EPS,'fill cost exceeds collateral')
            payout=amount(row,'payout_usd')
            need(payout in (0.,q),'invalid binary payout')
            same(amount(row,'net_pnl_usd'),payout-c,'payout/cost profit mismatch')
            expected[ident+('fill',)]+=1;expected[ident+('settlement',)]+=1
            settle=amount(row,'settlement_ms')
            need(settle>=m,'settlement precedes match')
            due=amount(row,'cash_release_due_ms')
            same(due,settle+amount(summary_by_key[key],'cash_release_delay_ms'),
                 'cash release delay mismatch')
            need(type(row.get('cash_released')) is bool,'explicit payout release state required')
            if payout and due>settle and row['cash_released']:
                expected[ident+('payout_release',)]+=1
        else:
            same(c,0.,'unfilled order debited')
            if row.get('status')=='no_fill':expected[ident+('no_fill',)]+=1
            if row.get('status')=='rejected_post_send_stress':expected[ident+('post_send_reject',)]+=1
    previous_ms = -math.inf
    pending_debit = {}
    seen = Counter()
    for sequence,event in enumerate(events,1):
        need(type(event.get('event_sequence')) is int and event['event_sequence']==sequence,
             'missing, reordered or reset event sequence')
        need(event.get('report_cash_basis')=='engine_recorded_event','fallback cash snapshots prohibited')
        at=amount(event,'event_ms');need(at>=previous_ms,'cash event time regressed');previous_ms=at
        key=account(event);ident=key+(event.get('attempt_id'),);kind=event.get('event_type')
        need(ident in indexed,'cash event lacks matching attempt')
        token=ident+(kind,);need(token in expected,'unknown or unjustified cash event')
        seen[token]+=1;need(seen[token]==1,'duplicate cash transition')
        row=indexed[ident];state=states[key]
        if key in pending_debit:
            need(kind=='fill' and ident==pending_debit[key], 'collateral release/fill interleaved')
        if kind=='reservation':
            same(at,row['decision_ms'],'reservation time differs from decision')
            same(amount(row,'cash_before_decision'),state['cash_usd'],'decision cash reset or mismatch')
            reserve=amount(row,'reserved_usd_at_decision')
            need(reserve<=state['cash_usd']-state['reserved_usd']+EPS,'borrowed order collateral')
            state['reserved_usd']+=reserve
        elif kind=='reservation_release':
            same(at,row['match_ms'],'reservation release time differs from match')
            need(seen[ident+('reservation',)]==1,'release before reservation')
            state['reserved_usd']-=row['reserved_usd_at_decision']
            if row['filled_shares']>0:pending_debit[key]=ident
        elif kind in ('fill','no_fill','post_send_reject'):
            same(at,row['match_ms'],'debit/reject time differs from match')
            need(seen[ident+('reservation_release',)]==1,'debit before collateral release')
            state['cash_usd']-=row['cost_usd']
            if kind=='fill':pending_debit.pop(key)
            same(amount(row,'cash_after_match'),state['cash_usd'],'match cash mismatch')
        elif kind=='settlement':
            same(at,row['settlement_ms'],'settlement event time mismatch')
            need(seen[ident+('fill',)]==1,'settlement before fill')
            payout=row['payout_usd'];due=row['cash_release_due_ms']
            if payout and due>at:state['locked_payout_usd']+=payout
            else:state['cash_usd']+=payout
            state['realized_pnl_usd']+=row['net_pnl_usd']
            same(amount(row,'cash_after_settlement'),state['cash_usd'],'settlement cash mismatch')
        elif kind=='payout_release':
            same(at,row['cash_release_due_ms'],'premature payout reuse')
            need(seen[ident+('settlement',)]==1,'payout release precedes settlement')
            state['cash_usd']+=row['payout_usd'];state['locked_payout_usd']-=row['payout_usd']
            same(amount(row,'cash_after_release'),state['cash_usd'],'payout cash mismatch')
        need(state['cash_usd']-state['reserved_usd']>=-EPS and state['reserved_usd']>=-EPS
             and state['locked_payout_usd']>=-EPS,'overdraft, negative collateral or locked payout')
        for name in BALANCES:same(amount(event,name),state[name],'cash event transition mismatch: '+name)
        state['min_available']=min(state['min_available'],state['cash_usd']-state['reserved_usd'])
        state['peak_equity']=max(state['peak_equity'],1000.+state['realized_pnl_usd'])
        state['max_drawdown']=max(state['max_drawdown'],state['peak_equity']-1000.-state['realized_pnl_usd'])
        counts[key]+=1
    need(seen==expected,'missing mandatory order cash transitions')
    for key,state in states.items():
        row=summary_by_key[key]
        for field,balance in [('ending_cash_usd','cash_usd'),('ending_reserved_usd','reserved_usd'),
                ('ending_locked_payout_usd','locked_payout_usd'),('min_available_cash_usd','min_available'),
                ('max_realized_drawdown_usd','max_drawdown')]:
            same(amount(row,field),state[balance],'terminal summary differs: '+field)
        same(state['reserved_usd'],0.,'unreleased order collateral')
        same(state['cash_usd']+state['locked_payout_usd'],1000.+state['realized_pnl_usd'],
             'terminal cash conservation mismatch')
    return dict(schema='rank7-independent-cash-integrity-v1',accounts=len(states),
        submitted_orders=sum(r['submitted'] for r in attempts),events=len(events),
        continuous_capital_reconstruction=True,daily_resets=0,topups_or_borrowing=0,
        observed_redemption_certified=False,financial_replays=0,formal_oos_result=None)
