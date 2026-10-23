"""Shared-cash ancillary development: unchanged M500-010 and M500-680.

Post-selected development components, not new independent mechanisms. Earliest
causal signal wins; ID breaks ties; one100-share attempt per market, no retry.
Existing replay_engine executes actual matching/cash, never summed PnL.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path

import mine500_catalog as catalog
import replay_engine as ex
from cash_release_audit import reconcile, close

IDS=('M500-010','M500-680')
PORT_ID='PORT-M500-010-680-v1'


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def dump(path, value):
    with Path(path).open('x') as f:
        json.dump(value,f,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def write_rows(path, items):
    pending=Path(str(path)+'.pending')
    with pending.open('xb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as f:
            for r in items:f.write((json.dumps(r,separators=(',',':'),allow_nan=False)+'\n').encode())
        raw.flush();os.fsync(raw.fileno())
    with gzip.open(pending,'rt') as f:
        if sum(1 for _ in f)!=len(items):raise ValueError('incomplete gzip ledger')
    if Path(path).exists():raise FileExistsError(path)
    pending.rename(path)


def rules():
    lookup={c['id']:c for c in catalog.make_candidates()+catalog.make_supplementary_candidates()}
    return [lookup[cid] for cid in IDS]


def arbitrate(events):
    selected=rules();markets={};choices={};first={};invalid=Counter();valid=Counter();result=[]
    for original in events:
        e=dict(original)
        if e['type']=='market':markets[e['market_id']]=e
        if e['type']!='decision':result.append(e);continue
        now=e['recv_ms'];mid=e['market_id'];f=e.get('features',{});av=e.get('feature_available_ms',{})
        market=markets.get(mid);hits=[]
        clock_ok=(market is not None and ex._number(f.get('tau_s'))
          and market['start_ms']<=now<market['end_ms']
          and abs(f['tau_s']-(market['end_ms']-now)/1000)<=.001)
        for c in selected:
            names=set(c['required_features'])|{'tau_s'}
            ok=clock_ok and all(ex._number(f.get(n)) and ex._number(av.get(n)) and av[n]<=now for n in names)
            if not ok:invalid[c['id']]+=1;continue
            valid[c['id']]+=1
            side=catalog.signal(c,{n:f[n] for n in names})
            if side is not None:
                hits.append((c['id'],side))
                first.setdefault((c['id'],mid),dict(candidate_id=c['id'],market_id=mid,decision_ms=now,side=side))
        side=0
        if hits and mid not in choices:
            cid,outcome=hits[0];side=1 if outcome=='Up' else -1
            choices[mid]=dict(market_id=mid,decision_ms=now,selected_candidate=cid,side=outcome,
                simultaneous_signals=hits,opposite_simultaneous=len({s for _,s in hits})>1)
        e['features']={'tau_s':f.get('tau_s'),'portfolio_side':side}
        e['feature_available_ms']={'tau_s':av.get('tau_s'),'portfolio_side':now}
        result.append(e)
    return result,dict(choices=list(choices.values()),component_first_signals=list(first.values()),
        valid_decisions_by_component=dict(valid),invalid_decisions_by_component=dict(invalid),
        arbitration_uses_book_health=False,arbitration_uses_outcomes=False,priority=list(IDS),
        policy='earliest causal eligible signal; stable ID tie; consume market even if execution fails')


def portfolio_signal(candidate, features):
    return {1:'Up',-1:'Down'}.get(features['portfolio_side'])


def configurations():
    common=dict(latencies_ms=(500,600,1000),shares=100.,initial_cash_usd=1000.,
        max_quote_age_ms=250,require_constraints=True,min_notional_usd=1.,
        full_or_zero=False,side_specific_books=False,post_send_reject_bps=0)
    return [('base',ex.ReplayConfig(**common)),
      ('half_depth',ex.ReplayConfig(**common,depth_fraction=.5)),
      ('plus_1cent',ex.ReplayConfig(**common,extra_cost_per_filled_share_usd=.01)),
      ('release24h',ex.ReplayConfig(**common,cash_release_delay_ms=86400000))]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parent
    a.out.mkdir(parents=True,exist_ok=False)
    registered=dict(schema='m500-two-mechanism-shared-account-development-v1',
      registered_at_utc=datetime.now(timezone.utc).isoformat(),input_sha256=sha(a.input),
      components=rules(),component_priority=list(IDS),portfolio_id=PORT_ID,
      policy='earliest eligible causal signal; stable ID tie; one order/market; failure never retries',
      selection='two distinct templates selected using already consumed399701 development; no OOS',
      new_component_rules=0,new_combination_policies=1,new_independent_mechanisms=0,
      configs={n:asdict(c) for n,c in configurations()},planned_new_shared_accounts=12,
      code_sha256={n:sha(root/n) for n in ('mine500_pair_portfolio.py','replay_engine.py',
        'mine500_catalog.py','mine500_constraints.py','cash_release_audit.py')},
      formal_oos_result=None,rank7_result=None,old_freezes_modified=False,
      evidence='already_consumed_partial_day_development; shared portfolio ancillary, never replaces single-strategy ranking')
    dump(a.out/'registration.json',registered)
    events,diagnostics=arbitrate(ex.read_jsonl(a.input))
    if set(diagnostics['valid_decisions_by_component'])!=set(IDS):
        raise ValueError('component feature coverage missing; no financial replay allowed')
    dump(a.out/'signals.json',diagnostics)
    write_rows(a.out/'arbitrated_events.jsonl.gz',events)
    for name,config in configurations():
        for filename,want in registered['code_sha256'].items():
            if sha(root/filename)!=want:raise ValueError('code changed after registration')
        result=ex.replay_events(events,[dict(id=PORT_ID,required_features=['portfolio_side'])],portfolio_signal,config)
        audit=reconcile(result['attempts'],result['cash_events'],result['summary'],config.cash_release_delay_ms)
        expected={r['market_id']:(r['decision_ms'],r['side']) for r in diagnostics['choices']}
        for row in result['attempts']:
            if (row['decision_ms'],row['side'])!=expected[row['market_id']]:raise ValueError('arbitration mismatch')
        folder=a.out/name;folder.mkdir()
        dump(folder/'cash_audit.json',audit)
        write_rows(folder/'attempts.jsonl.gz',result.pop('attempts'))
        write_rows(folder/'cash_events.jsonl.gz',result.pop('cash_events'))
        result['capital_scope']='one shared1000USD account per alternative scenario; not additive'
        result.update(evidence='post-selected development ancillary portfolio',rank7_result=None,formal_oos_result=None)
        dump(folder/'summary.json',result)
        print(json.dumps(dict(scenario=name,shared_account_cells=len(result['summary']))),flush=True)
    if sha(a.input)!=registered['input_sha256']:raise ValueError('input changed')
    # Seal only after every gzip is closed and reread at its final name.
    manifests={}
    for f in a.out.rglob('*'):
        if not f.is_file():continue
        count=None
        if f.suffix=='.gz':
            with gzip.open(f,'rt') as stream:count=sum(1 for _ in stream)
        manifests[str(f.relative_to(a.out))]=dict(bytes=f.stat().st_size,sha256=sha(f),gzip_rows=count)
    dump(a.out/'completion.json',dict(outputs=manifests,registered_shared_accounts=12,
        new_shared_policy=1,rank7_result=None,formal_oos_result=None))


if __name__=='__main__':main()
