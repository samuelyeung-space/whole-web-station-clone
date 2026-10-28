"""Compile frozen first signals for book-gate experiments; no financial replay.

Reuse the existing catalog. Never inspect book health, future labels or PnL to
select an arm. Deduplicate receipt-identical arms for one raw-book intake pass,
retaining the complete candidate/market membership and missing-feature counts.
"""
import argparse,gzip,hashlib,json,math
from collections import Counter
from pathlib import Path
import mine500_catalog as catalog


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def arm_key(r):return (r['market_id'],r['decision_ms'],r['side'],r['asset_id'])


def compile_cohorts(events,candidates,signal_fn=catalog.signal,known_arms=()):
    markets={};seen=set();last=None;arms={};members={};records={};row_counts=Counter()
    known={arm_key(r) for r in known_arms}
    for c in candidates:
        if c['id'] in records:raise ValueError('duplicate candidate ID')
        records[c['id']]=dict(candidate_id=c['id'],rule_fingerprint=c.get('rule_fingerprint'),
            family=c.get('family'),required_features=c['required_features'],valid_rows=0,
            rejection_reasons=Counter(),cohort=[])
    for e in events:
        now=e.get('recv_ms')
        if not finite(now) or (last is not None and now<last):raise ValueError('invalid receipt order')
        last=now;kind=e.get('type');row_counts[kind]+=1
        mid=e.get('market_id')
        if kind=='market':
            if (finite(e.get('start_ms')) and finite(e.get('end_ms')) and
                e['end_ms']-e['start_ms']==300000 and e.get('label_source') in ('gamma','polymarket','chainlink')):
                markets[mid]=e
            continue
        if kind!='decision':continue
        m=markets.get(mid);f=e.get('features',{});available=e.get('feature_available_ms',{})
        for c in candidates:
            cid=c['id'];r=records[cid]
            if (cid,mid) in seen:continue
            error=None
            if m is None or not m['start_ms']<=now<m['end_ms']:error='missing_market_or_outside_window'
            elif not isinstance(f,dict) or not isinstance(available,dict):error='missing_feature_maps'
            else:
                for name in dict.fromkeys(['tau_s']+c['required_features']):
                    if not finite(f.get(name)):error='missing_or_invalid_feature:'+name;break
                    if not finite(available.get(name)):error='missing_availability:'+name;break
                    if available[name]>now:error='future_feature:'+name;break
                if error is None and abs(f['tau_s']-(m['end_ms']-now)/1000)>.001:
                    error='invalid_tau_clock'
            if error:
                r['rejection_reasons'][error]+=1;continue
            r['valid_rows']+=1
            visible={name:f[name] for name in set(c['required_features'])|{'tau_s'}}
            side=signal_fn(c,visible)
            if side is None:continue
            if side not in ('Up','Down'):raise ValueError('invalid signal direction')
            seen.add((cid,mid)) # Consume before mapping/health; never reselect later.
            arm=dict(market_id=mid,decision_ms=now,side=side,
                asset_id=m.get('up_token_id' if side=='Up' else 'down_token_id'),
                up_token_id=m.get('up_token_id'),down_token_id=m.get('down_token_id'))
            key=digest(arm_key(arm));arms[key]=arm;members.setdefault(key,[]).append(cid)
            r['cohort'].append(key)
    groups={}
    for r in records.values():
        r['cohort_sha256']=digest(r['cohort']);r['first_signal_markets']=len(r['cohort'])
        overlap=sum(arm_key(arms[k]) in known for k in r['cohort'])
        r['known108_arm_count']=overlap
        r['coverage_status']=('no_signal' if not r['cohort'] else
            'all_arms_known' if overlap==len(r['cohort']) else 'some_arms_known' if overlap else 'new_raw_intake_required')
        r['status']='blocked_no_valid_features' if not r['valid_rows'] else 'compiled'
        r['rejection_reasons']=dict(r['rejection_reasons'])
        if r['cohort']:groups.setdefault(r['cohort_sha256'],[]).append(r['candidate_id'])
    return dict(schema='book-parent-cohorts-v1',candidate_records=list(records.values()),
        unique_arms=arms,arm_candidate_memberships=members,identical_cohort_groups=groups,
        input_row_counts=dict(row_counts),book_health_used_for_selection=False,
        economic_pnl=None,financial_replays=0,formal_oos_result=None,full_week_pnl=None)


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--known',required=True)
    p.add_argument('--output',required=True);p.add_argument('--register-only',action='store_true');a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    candidates=catalog.make_candidates()+catalog.make_supplementary_candidates()
    here=Path(__file__).parent
    protocol=dict(schema='book-parent-cohorts-protocol-v1',input_sha256=sha(a.input),known_sha256=sha(a.known),
        code_sha256={n:sha(here/n) for n in ['book_parent_cohorts.py','mine500_catalog.py']},
        candidate_registry_sha256=digest(candidates),candidate_count=len(candidates),
        evidence='consumed Oct9 development only; first signal extraction, no financial rerun',
        first_signal_consumed_before_book_or_mapping=True,formal_oos_result=None)
    path=out/'protocol.json';encoded=json.dumps(protocol,indent=2)+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('changed registered protocol')
    path.write_text(encoded)
    if a.register_only:return
    with gzip.open(a.input,'rt') as f:
        result=compile_cohorts((json.loads(l) for l in f),candidates,known_arms=json.load(open(a.known))['cohort'])
    result['protocol_sha256']=sha(path)
    with gzip.open(out/'cohorts.json.gz','wt') as f:json.dump(result,f)
    records=result['candidate_records'];known=json.load(open(a.known))['cohort'];keys={arm_key(r) for r in known}
    summary=dict(candidate_count=len(records),candidate_status_counts=dict(Counter(r['status'] for r in records)),
        coverage_counts=dict(Counter(r['coverage_status'] for r in records)),
        first_signal_memberships=sum(r['first_signal_markets'] for r in records),
        unique_nonempty_cohorts=len(result['identical_cohort_groups']),unique_arms=len(result['unique_arms']),
        unique_arms_known_from108=sum(arm_key(r) in keys for r in result['unique_arms'].values()),
        financial_replays=0,economic_pnl=None,formal_oos_result=None,full_week_pnl=None,
        no_new_independent_economic_candidates=True)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
