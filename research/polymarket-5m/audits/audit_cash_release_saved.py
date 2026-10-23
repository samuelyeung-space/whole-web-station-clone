"""Verify saved redemption-stress execution books, official labels and assets.

No replay or orders. Cash accounting reuses existing independent reconcile;
book reconstruction, fee arithmetic and summary binding are engine-free.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
import gzip
import hashlib
import json
import math
from pathlib import Path

from cash_release_audit import reconcile, close


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def rows(path):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rt') as f:
        for line in f:yield json.loads(line)


def reconstruct_cash_events(attempts, metrics, delay):
    """Recover an independent cash trace from intact saved order lifecycles.

    This is not the lost engine mutation stream. Every intermediate saved
    per-order cash value, admission and terminal asset must reconcile. The
    reconstruction refuses ambiguous same-account simultaneous event phases.
    """
    states={(m['candidate_id'],m['latency_ms']):dict(cash=1000.,reserved=0.,locked=0.,realized=0.) for m in metrics}
    actions=[]; occupied={}
    for r in attempts:
        actions.append((r['decision_ms'],0,int(r['attempt_id']),'decision',r))
        if r['submitted']:actions.append((r['match_ms'],1,int(r['attempt_id']),'match',r))
        if r['settled']:
            actions.append((r['settlement_ms'],2,int(r['attempt_id']),'settlement',r))
            if r['payout_usd'] and r['cash_released']:
                actions.append((r['cash_release_due_ms'],3,int(r['attempt_id']),'payout_release',r))
    trace=[]
    def emit(kind,now,r,s):
        trace.append(dict(candidate_id=r['candidate_id'],latency_ms=r['latency_ms'],
            attempt_id=r['attempt_id'],event_sequence=len(trace)+1,event_ms=now,event_type=kind,
            cash_usd=s['cash'],reserved_usd=s['reserved'],locked_payout_usd=s['locked'],
            realized_pnl_usd=s['realized']))
    for now,phase,_,kind,r in sorted(actions,key=lambda x:x[:3]):
        k=(r['candidate_id'],r['latency_ms']);s=states[k]
        token=(k,now)
        if token in occupied and occupied[token]!=phase:
            raise ValueError('ambiguous same-account coincident lifecycle phases; require original engine trace')
        occupied[token]=phase
        if kind=='decision':
            close(r['cash_before_decision'],s['cash'],'reconstructed decision cash')
            if r['submitted'] or r['status']=='rejected_insufficient_available_cash':
                price=Decimal(str(r['limit_price']));rate=Decimal(str(r['fee_rate_decision']))
                fee=(Decimal(100)*rate*price*(1-price)).quantize(Decimal('.00001'),rounding=ROUND_CEILING)
                need=100*r['limit_price']+float(fee)
                admit=s['cash']-s['reserved']+1e-9>=need
                if admit!=r['submitted']:raise ValueError('reconstructed cash admission differs')
                if admit:
                    close(r['reserved_usd_at_decision'],need,'reconstructed reserve')
                    s['reserved']+=need;emit('reservation',now,r,s)
        elif kind=='match':
            s['reserved']-=r['reserved_usd_at_decision'];s['reserved']=max(0.,s['reserved'])
            emit('reservation_release',now,r,s)
            s['cash']-=r['cost_usd']
            if r['cash_after_match'] is not None:close(r['cash_after_match'],s['cash'],'reconstructed match cash')
            if r['filled_shares'] or r['status']=='no_fill':
                emit('fill' if r['filled_shares'] else 'no_fill',now,r,s)
        elif kind=='settlement':
            s['locked']+=r['payout_usd'];s['realized']+=r['net_pnl_usd']
            close(r['cash_after_settlement'],s['cash'],'reconstructed outcome cash')
            close(r['realized_equity_after_settlement'],1000+s['realized'],'reconstructed realized equity')
            emit(kind,now,r,s)
        else:
            s['cash']+=r['payout_usd'];s['locked']-=r['payout_usd']
            close(r['cash_after_release'],s['cash'],'reconstructed release cash')
            emit(kind,now,r,s)
    return trace


def audit(input_path, root):
    completion=json.loads((root/'completion.json').read_text())
    if completion['input_sha256']!=sha(input_path):raise ValueError('input changed')
    damaged=[]
    for name,want in completion['outputs_sha256'].items():
        if sha(root/name)!=want:
            if name!='86400000/cash_events.jsonl.gz':raise ValueError('saved artifact changed: '+name)
            try:
                list(rows(root/name))
            except EOFError:
                pass
            else:
                raise ValueError('hash mismatch without proved truncation; reconstruction refused')
            damaged.append(dict(path=name,expected_sha256=want,observed_sha256=sha(root/name),
              observed_bytes=(root/name).stat().st_size,truncation_verified='gzip EOFError',
              accepted_as_original_engine_trace=False))
    books=defaultdict(list);clocks=defaultdict(list);outcomes={};header=None
    for r in rows(input_path):
        if r['type']=='dataset':header=r
        elif r['type']=='book':
            books[r['market_id']].append(r);clocks[r['market_id']].append(r['recv_ms'])
        elif r['type']=='outcome':outcomes[r['market_id']]=r
    def book(mid,now):
        i=bisect_right(clocks[mid],now)-1
        if i<0:raise ValueError('no causal book')
        return books[mid][i]
    report={'schema':'cash-release-saved-independent-audit-v1','input_sha256':sha(input_path),
            'new_financial_replays':0,'rank7_result':None,'formal_oos_result':None,
            'damaged_original_files_quarantined':damaged,'scenarios':{}}
    for delay in (3600000,86400000):
        folder=root/str(delay);doc=json.loads((folder/'summary.json').read_text())
        aa=list(rows(folder/'attempts.jsonl.gz'))
        cfg=doc['config'];metrics=doc['summary'];grouped=defaultdict(list)
        if delay==86400000 and damaged:
            ee=reconstruct_cash_events(aa,metrics,delay)
            target=root/'reconstructed_86400000_cash_events.jsonl.gz'
            if target.exists():
                if list(rows(target))!=ee:raise ValueError('prior reconstruction changed')
            else:
                with target.open('xb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as f:
                    for row in ee:f.write((json.dumps(row,separators=(',',':'))+'\n').encode())
            if sum(1 for _ in rows(target))!=len(ee):raise ValueError('truncated reconstruction')
            report['reconstructed_cash_trace']=dict(path=target.name,sha256=sha(target),rows=len(ee),
                scope='independent lifecycle reconstruction; not recovered original mutation bytes',
                financial_replays_repeated=0,intermediate_order_cash_values_all_checked=True)
        else:ee=list(rows(folder/'cash_events.jsonl.gz'))
        if (cfg['cash_release_delay_ms']!=delay or cfg['shares']!=100 or cfg['initial_cash_usd']!=1000
              or cfg['latencies_ms']!=[500,600,1000] or not cfg['require_constraints']
              or cfg['full_or_zero'] or cfg['side_specific_books'] or cfg['post_send_reject_bps']
              or cfg['depth_fraction']!=1 or cfg['extra_cost_per_filled_share_usd']):
            raise ValueError('unregistered execution policy')
        keys=set();checked=0
        for r in aa:
            k=(r['candidate_id'],r['latency_ms'],r['market_id'])
            if k in keys:raise ValueError('duplicate first signal')
            keys.add(k);grouped[k[:2]].append(r)
            close(r['match_ms']-r['decision_ms'],r['latency_ms'],'actual latency')
            if not r['submitted']:
                if r['cost_usd'] or r['filled_shares'] or r['settled']:
                    raise ValueError('unsubmitted debit/fill')
                continue
            db=book(r['market_id'],r['decision_ms'])
            eligible=sorted(db[r['side'].lower()+'_asks'])
            close(r['limit_price'],eligible[0][0],'fixed causal limit')
            if not r['filled_shares']:
                close(r['cost_usd'],0.,'failed order cost');continue
            b=book(r['market_id'],r['match_ms'])
            close(b['recv_ms'],r['book_recv_ms_at_match'],'latest available match book')
            if not b['healthy']:raise ValueError('unhealthy match')
            for side in ('up','down'):
                if not 0<=r['match_ms']-b[side+'_recv_ms']<=250:raise ValueError('stale side')
            left=100.;gross=0.;fee=Decimal(0)
            levels=defaultdict(float)
            for price,depth in b[r['side'].lower()+'_asks']:levels[price]+=depth
            for price,depth in sorted(levels.items()):
                if price>r['limit_price']+1e-9 or left<=1e-9:break
                take=min(left,depth);left-=take;gross+=take*price
                fee+=Decimal(str(take))*Decimal(str(price))*Decimal(str(b['fee_rate']))*(1-Decimal(str(price)))
            qty=100.-left
            close(qty,r['filled_shares'],'complete eligible FAK depth')
            close(gross/qty,r['fill_vwap'],'VWAP')
            expected_fee=Decimal(0) if fee<Decimal('.00001') else fee.quantize(Decimal('.00001'),rounding=ROUND_HALF_UP)
            close(float(fee),r['unrounded_fees_usd'],'fee formula')
            close(float(expected_fee),r['fees_usd'],'fee rounding assumption')
            close(gross+r['fees_usd'],r['cost_usd'],'fill debit')
            o=outcomes[r['market_id']]
            if r['winner']!=o['winner']:raise ValueError('official winner mismatch')
            close(r['settlement_ms'],max(o['recv_ms'],o['resolved_ms']),'outcome knowledge')
            close(r['cash_release_due_ms'],r['settlement_ms']+delay,'assumed spendability')
            close(r['payout_usd'],qty if r['side']==o['winner'] else 0.,'official payoff')
            close(r['net_pnl_usd'],r['payout_usd']-r['cost_usd'],'net profit')
            for phase,now in [('decision',r['decision_ms']),('match',r['match_ms'])]:
                c=r['constraints_at_'+phase]
                if not c['validated']:raise ValueError('unvalidated constraints')
                if any(v['available_ms']>now for v in c['provenance'].values()):
                    raise ValueError('future constraints')
            checked+=1
        active=[r for r in metrics if r['attempts']]
        cash=reconcile(aa,ee,active,delay)
        lookup={r['attempt_id']:r for r in aa};equity={};dd={};peak={}
        for e in ee:
            if e['event_type']!='settlement':continue
            k=(e['candidate_id'],e['latency_ms']);equity[k]=equity.get(k,1000.)+lookup[e['attempt_id']]['net_pnl_usd']
            peak[k]=max(peak.get(k,1000.),equity[k]);dd[k]=max(dd.get(k,0.),peak[k]-equity[k])
        for m in metrics:
            k=(m['candidate_id'],m['latency_ms']);rr=grouped[k]
            if len(rr)!=m['attempts']:raise ValueError('summary count mismatch')
            if Counter(r['status'] for r in rr)!=Counter(m['status_counts']):raise ValueError('status counts mismatch')
            for field in ('fees_usd','filled_shares'):
                close(m[field],math.fsum(r[field] for r in rr),'summary '+field)
            close(m['max_realized_drawdown_usd'],dd.get(k,0.),'realized drawdown')
            if m['net_pnl_usd'] is not None:
                close(m['net_pnl_usd'],math.fsum(r['net_pnl_usd'] for r in rr if r['settled']),'summary PnL')
            if not rr and (m['net_pnl_usd'] is not None or m['realized_pnl_usd'] is not None):
                raise ValueError('inactive PnL must remain null')
            if not m['unresolved_positions']:
                close(m['ending_cash_usd']+m['ending_locked_payout_usd'],1000+(m['realized_pnl_usd'] or 0.),'terminal assets')
        report['scenarios'][str(delay)]=dict(registered_accounts=len(metrics),attempt_rows=len(aa),
            checked_fills=checked,cash_event_rows=len(ee),cash_audit=cash,
            all_saved_ledgers_reconciled=True,all_filled_books_labels_fees_reconciled=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--results',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=audit(a.input,a.results)
    with a.out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:{f:v for f,v in row.items() if f!='cash_audit'} for k,row in result['scenarios'].items()}))
