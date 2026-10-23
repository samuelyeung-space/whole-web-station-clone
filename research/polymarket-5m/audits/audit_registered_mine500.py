"""Audit saved development ledgers and their projected input books; no replay.

Cash helper is extracted unchanged from a pinned existing audit module so the
unrelated model-training imports are never executed. No OOS/rank admission.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter, defaultdict
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

CASH_HELPER_BLOB = 'aea3f9362acb5883ee50e16131c0fac1b66d0eef'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as f:
        for line in f:
            yield json.loads(line)


def close(x, y, label, tolerance=1e-6):
    if not math.isfinite(x) or not math.isfinite(y) or abs(x-y) > tolerance:
        raise ValueError(label + ' mismatch')


def cash_helper(path):
    raw = Path(path).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != CASH_HELPER_BLOB:
        raise ValueError('cash helper source blob mismatch')
    nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef)
             and n.name in ('close', 'cash_events')]
    if {n.name for n in nodes} != {'close', 'cash_events'}:
        raise ValueError('cash helper definition mismatch')
    namespace = {'Counter': Counter}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['cash_events']


def audit(input_path, result_root, helper_path):
    completion = json.loads((result_root/'completion.json').read_text())
    if completion['input_sha256'] != sha(input_path):
        raise ValueError('normalized input hash mismatch')
    for name, expected in completion['outputs_sha256'].items():
        if sha(result_root/name) != expected:
            raise ValueError('completed output hash mismatch')
    books, outcomes, maps, counts = {}, {}, {}, Counter()
    for r in read(input_path):
        counts[r['type']] += 1
        if r['type'] == 'book':
            key = (r['market_id'], r['recv_ms'])
            if key in books:
                raise ValueError('ambiguous projected book')
            books[key] = r
        elif r['type'] == 'outcome':
            if r['market_id'] in outcomes:
                raise ValueError('duplicate outcome')
            outcomes[r['market_id']] = r
        elif r['type'] == 'market':
            maps[r['market_id']] = r
    cash_events = cash_helper(helper_path)
    report = {'schema':'mine500-independent-development-audit-v1',
              'input_sha256':sha(input_path), 'input_record_counts':dict(counts),
              'cash_helper_blob':CASH_HELPER_BLOB, 'scenarios':{},
              'new_financial_replays':0, 'actual_cash_reuse_verified':False,
              'cash_release_assumption':'max(official local outcome receive time,resolved time); actual redeem/spendable balance unobserved',
              'tick_limitation':'observed snapshot constraints only; dynamic update completeness unverified',
              'rank7_result':None, 'formal_oos_result':None}
    for name in ('base','half_depth','plus_1cent'):
        root = result_root/name
        doc = json.loads((root/'summary.json').read_text())
        config = doc['config']
        if (config['shares'] != 100 or config['initial_cash_usd'] != 1000 or
                config['latencies_ms'] != [500,600,1000] or not config['require_constraints']):
            raise ValueError('registered sizing/config mismatch')
        grouped = defaultdict(list)
        keys = set()
        checked_fills = 0
        for r in read(root/'attempts.jsonl'):
            key = (r['candidate_id'], r['latency_ms'], r['market_id'])
            if key in keys:
                raise ValueError('duplicate first-signal attempt')
            keys.add(key)
            if r['requested_shares'] != 100 or r['latency_ms'] not in (500,600,1000):
                raise ValueError('attempt size or latency mismatch')
            grouped[key[:2]].append(r)
            qty = r['filled_shares']
            if not 0 <= qty <= 100+1e-8:
                raise ValueError('quantity outside target')
            if not r['submitted']:
                if qty or r['settled']:
                    raise ValueError('unsubmitted fill')
                continue
            close(r['match_ms']-r['decision_ms'],r['latency_ms'],'actual match delay')
            if qty == 0:
                close(r['cost_usd'],0.,'failed order debit')
                continue
            if not r['settled']:
                raise ValueError('unsettled filled inventory')
            if r['status'] not in ('filled_full','filled_partial'):
                raise ValueError('filled status mismatch')
            if r['status']=='filled_full':close(qty,100.,'full fill quantity')
            book = books[(r['market_id'],r['book_recv_ms_at_match'])]
            if not book['healthy'] or book['recv_ms'] > r['match_ms']:
                raise ValueError('unavailable/unhealthy execution book')
            for side in ('up','down'):
                age=r['match_ms']-book[side+'_recv_ms']
                if not 0 <= age <= config['max_quote_age_ms']:
                    raise ValueError('stale token book')
            left=qty;gross=0.;fee=Decimal(0)
            for price,depth in sorted(book[r['side'].lower()+'_asks']):
                if price>r['limit_price']+1e-9:break
                take=min(left,depth*config['depth_fraction'])
                gross+=take*price
                fee+=Decimal(str(take))*Decimal(str(book['fee_rate']))*Decimal(str(price))*(1-Decimal(str(price)))
                left-=take
                if left<=1e-9:break
            if left>1e-6:raise ValueError('fill exceeds recorded eligible depth')
            close(gross,qty*r['fill_vwap'],'book gross notional')
            close(float(fee),r['unrounded_fees_usd'],'book fee formula')
            close(qty*config['extra_cost_per_filled_share_usd'],r['extra_cost_usd'],'extra cost')
            close(gross+r['fees_usd']+r['extra_cost_usd'],r['cost_usd'],'total debit')
            outcome=outcomes[r['market_id']]
            close(r['settlement_ms'],max(outcome['recv_ms'],outcome['resolved_ms']),'causal settlement release')
            if r['winner']!=outcome['winner']:raise ValueError('official label mismatch')
            close(r['payout_usd'],qty if r['side']==outcome['winner'] else 0.,'payoff')
            close(r['net_pnl_usd'],r['payout_usd']-r['cost_usd'],'net profit')
            for phase,when in [('decision',r['decision_ms']),('match',r['match_ms'])]:
                c=r['constraints_at_'+phase]
                if not c['validated']:raise ValueError('missing execution constraints')
                for provenance in c['provenance'].values():
                    if provenance['available_ms']>when:raise ValueError('future execution constraint')
            checked_fills+=1
        audits=[]
        for summary in doc['summary']:
            rr=grouped[(summary['candidate_id'],summary['latency_ms'])]
            if len(rr)!=summary['attempts']:raise ValueError('summary attempts mismatch')
            ff=[r for r in rr if r['filled_shares']>0]
            close(math.fsum(r['filled_shares'] for r in ff),summary['filled_shares'],'summary shares')
            close(math.fsum(r['fees_usd'] for r in rr),summary['fees_usd'],'summary fees')
            if rr:
                cash=cash_events(rr,summary)
                if cash['minimum_available_cash_usd'] < -1e-7:
                    raise ValueError('negative available cash')
            else:
                close(summary['ending_cash_usd'],1000.,'inactive cash')
                close(summary['ending_reserved_usd'],0.,'inactive reservation')
                cash={'status':'no_attempts_financial_result_remains_null'}
            if summary['net_pnl_usd'] is not None:
                close(math.fsum(r['net_pnl_usd'] for r in ff),summary['net_pnl_usd'],'summary net profit')
            audits.append({'candidate_id':summary['candidate_id'],'latency_ms':summary['latency_ms'],
                           'result_status':summary['status'],'cash_audit':cash})
        report['scenarios'][name]={'registered_cells':len(doc['summary']),
              'attempt_rows':len(keys),'checked_fills':checked_fills,'cells':audits,
              'status_counts':dict(Counter(r['status'] for r in doc['summary'])),
              'all_saved_ledgers_reconciled':True}
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--cash-helper',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    result=audit(args.input,args.results,args.cash_helper)
    with args.out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:{kk:v[kk] for kk in ('registered_cells','attempt_rows','checked_fills','all_saved_ledgers_reconciled')} for k,v in result['scenarios'].items()}))
