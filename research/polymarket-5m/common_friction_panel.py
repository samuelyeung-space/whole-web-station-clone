"""Same-input independent-account friction comparison, development only.

No new alpha or fitting. All24 DPP rules,all8 MFA rules and the previously
requested M500439/669/677 are included, not selected by compound-stress PnL.
Reuses72 DPP base cells with exact source/execution hashes; revalidates24 MFA
base cells without counting them as new experiments. Nine M500 cells on this
specific $1000-start27h cohort and210 compound-friction cells are new scenarios.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

import depth_pressure_persistence as dpp
import minimal_feature_strategy as mfa
import mine500_catalog as catalog
import replay_engine as ex
from cash_release_audit import reconcile


LEGACY_IDS=('M500-439','M500-669','M500-677')
EXPECTED_TAPE_SHA='8a7336935690e0c021bd5dbffcc0c818ff6e76f395b27c5c7951230d329fc7d4'


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def dump(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')


def rows(path):
    with gzip.open(path,'rt') as f:
        for line in f:yield json.loads(line)


def write_rows(path,items):
    logical=hashlib.sha256();n=0
    with Path(path).open('xb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=1) as f:
        for item in items:
            line=(json.dumps(item,separators=(',',':'),allow_nan=False)+'\n').encode()
            logical.update(line);f.write(line);n+=1
    if sum(1 for _ in rows(path))!=n:raise ValueError('incomplete ledger')
    return dict(rows=n,sha256=sha(path),logical_sha256=logical.hexdigest(),gzip_eof_crc_verified=True)


def candidates():
    legacy={c['id']:c for c in catalog.make_candidates()+catalog.make_supplementary_candidates()}
    return [legacy[cid] for cid in LEGACY_IDS]+mfa.candidates()+dpp.candidates()


def signal(candidate,features):
    cid=candidate['id']
    if cid.startswith('M500-'):return catalog.signal(candidate,features)
    if cid.startswith('MFA-'):return mfa.signal(candidate,features)
    if cid.startswith('DPP-'):return dpp.signal(candidate,features)
    raise ValueError('unregistered strategy family')


def configurations():
    common=dict(shares=100,initial_cash_usd=1000,require_constraints=True,
                latencies_ms=(500,600,1000),post_send_reject_seed='research-only-v1')
    return [('base_validation',ex.ReplayConfig(**common)),
        ('stack_1h_reject10',ex.ReplayConfig(**common,depth_fraction=.5,
           extra_cost_per_filled_share_usd=.01,cash_release_delay_ms=3600000,post_send_reject_bps=1000)),
        ('stack_4h_reject30',ex.ReplayConfig(**common,depth_fraction=.5,
           extra_cost_per_filled_share_usd=.01,cash_release_delay_ms=14400000,post_send_reject_bps=3000))]


def first_intents(items):
    return {(r['candidate_id'],r['latency_ms'],r['market_id']):
             (r['decision_ms'],r['match_ms'],r['side'],r['limit_price']) for r in items}


def check_mfa_parity(old,new,old_attempts,new_attempts):
    fields=('status','attempts','submitted_orders','full_fills','partial_fills','filled_shares',
            'settled_positions','unresolved_positions','net_pnl_usd','fees_usd','ending_cash_usd',
            'max_realized_drawdown_usd','min_available_cash_usd','ending_reserved_usd','ending_locked_payout_usd')
    a={(r['candidate_id'],r['latency_ms']):r for r in old if r['scenario']=='shares100_base_release0'}
    b={(r['candidate_id'],r['latency_ms']):r for r in new if r['candidate_id'].startswith('MFA-')}
    if set(a)!=set(b) or len(a)!=24:raise ValueError('MFA baseline cell mismatch')
    # Global attempt numbering changes when other independently funded rules
    # join the batch. Every other per-order field must remain numerically exact.
    key=lambda r:(r['candidate_id'],r['latency_ms'],r['market_id'])
    oa={key(r):r for r in old_attempts if r['candidate_id'].startswith('MFA-')}
    na={key(r):r for r in new_attempts if r['candidate_id'].startswith('MFA-')}
    if len(oa)!=len(old_attempts) or len(na)!=sum(r['candidate_id'].startswith('MFA-') for r in new_attempts):
        raise ValueError('duplicate MFA intent')
    if set(oa)!=set(na):raise ValueError('MFA order inventory mismatch')
    for k in oa:
        x={f:v for f,v in oa[k].items() if f!='attempt_id'}
        y={f:v for f,v in na[k].items() if f!='attempt_id'}
        if x!=y:raise ValueError(f'MFA per-order field mismatch:{k}')
    rounding=[]
    for key in a:
        for field in fields:
            if a[key][field]==b[key][field]:continue
            # Python3.12 uses different sum paths for all-float versus mixed
            # int/float lists. Only the share aggregate may differ by <=1e-9;
            # exact individual orders AND stable fsum must match. No monetary,
            # count, status, cash or execution tolerance is introduced.
            oq=math.fsum(r['filled_shares'] for k,r in oa.items() if k[:2]==key)
            nq=math.fsum(r['filled_shares'] for k,r in na.items() if k[:2]==key)
            if field!='filled_shares' or abs(a[key][field]-b[key][field])>1e-9 or oq!=nq:
                raise ValueError(f'MFA baseline financial/count mismatch:{key}:{field}')
            rounding.append(dict(candidate_id=key[0],latency_ms=key[1],field=field,
                prior=a[key][field],current=b[key][field],delta=b[key][field]-a[key][field],stable_fsum=oq))
    return dict(cells=24,fields=list(fields),economic_or_per_order_differences=0,
        compared_orders=len(oa),ignored_only_global_attempt_id=True,aggregate_share_roundoff=rounding,
        policy='Only filled_shares summary abs<=1e-9 with exact per-order parity and equal math.fsum;all other summary fields exact',
        interpretation='full historical parity check,not24new economic samples')


def run(dpp_root,mfa_root,out):
    dr,mr,out=Path(dpp_root),Path(mfa_root),Path(out);tape=dr/'dpp_events.jsonl.gz'
    if sha(tape)!=EXPECTED_TAPE_SHA:raise ValueError('exact consumed DPP input required')
    code=Path(__file__).parent
    names=['common_friction_panel.py','depth_pressure_persistence.py','minimal_feature_strategy.py',
           'mine500_catalog.py','replay_engine.py','mine500_constraints.py','cash_release_audit.py']
    identity={name:sha(code/name) for name in names}
    dpp_protocol=json.loads((dr/'protocol.json').read_text());dpp_base=json.loads((dr/'base_replay.json').read_text())
    for name in ('depth_pressure_persistence.py','replay_engine.py','mine500_constraints.py'):
        if dpp_protocol['code_sha256'][name]!=identity[name]:raise ValueError('reused DPP code identity drift')
    if dpp_base['replay_code_sha256']!=identity['replay_engine.py']:raise ValueError('DPP baseline executor drift')
    if dpp_protocol['candidates']!=dpp.candidates():raise ValueError('DPP rules changed')
    specs=candidates();configs=configurations()
    if len(specs)!=35 or len({c['id'] for c in specs})!=35:raise ValueError('35 unique prior strategies required')
    out.mkdir(parents=True,exist_ok=False)
    refs={str(p):sha(p) for p in (dr/'base_replay.json',dr/'base_attempts.jsonl.gz',dr/'protocol.json',
                                 mr/'isolated/metrics.json',mr/'isolated/shares100_base_release0/attempts.jsonl.gz')}
    protocol=dict(schema='common-friction-panel-v1',registered_at_utc=datetime.now(timezone.utc).isoformat(),
        source_role='already_consumed_development_no_fitting',tape_sha256=EXPECTED_TAPE_SHA,
        candidates=specs,code_sha256=identity,configurations={n:asdict(c) for n,c in configs},
        reference_sha256=refs,new_candidates=0,new_independent_mechanisms=0,
        expected_common_panel_cells=315,new_distinct_scenario_rows=219,
        new_scenario_breakdown=dict(compound_cells=210,legacy_common_cohort_base_cells=9),
        baseline_reproduction_cells_not_new_evidence=24,baseline_DPP_cells_reused_without_rerun=72,
        parity_policy='Exact per-order except global attempt numbering;exact summary except filled_shares abs<=1e-9 with equal math.fsum',
        compound_assumptions='uncalibrated10/30percent intent-keyed rejection thresholds;halfdepth;+1cent;1h/4h delay after official outcome local knowledge',
        receipt_proxy='failure learned at match,not actualACK;no private100share calibration',
        selection='All24DPP+all8MFA+the prior requested439/669/677;no selecting by compound results or best latency',
        capital_scope='each strategy/latency/scenario own$1000;no sharedcash,reset,borrow,topup',
        multiple_testing='retain all previous720catalog,24DPP and historical trials;no new independent sample by changing friction',
        seven_day_rank_admitted=False,formal_oos_result=None)
    dump(out/'protocol.json',protocol)
    baseline_reference=list(rows(dr/'base_attempts.jsonl.gz'))+list(rows(mr/'isolated/shares100_base_release0/attempts.jsonl.gz'))
    original_mfa=json.loads((mr/'isolated/metrics.json').read_text())
    metrics=[dict(r,scenario='base',cell_origin='reused_exact_DPP_baseline') for r in dpp_base['summary']]
    baseline_all=None;receipts={}
    for name,cfg in configs:
        selected=[c for c in specs if not c['id'].startswith('DPP-')] if name=='base_validation' else specs
        result=ex.replay_events(ex.read_jsonl(tape),selected,signal,cfg)
        audit=reconcile(result['attempts'],result['cash_events'],result['summary'],cfg.cash_release_delay_ms)
        # Retain raw evidence even if a validation gate fails. Never silently
        # discard a completed diagnostic replay or overwrite its registration.
        receipts[name]=dict(attempts=write_rows(out/f'{name}_attempts.jsonl.gz',result['attempts']),
            cash_events=write_rows(out/f'{name}_cash.jsonl.gz',result['cash_events']),cash_audit=audit)
        dump(out/f'{name}_replay.json',{k:v for k,v in result.items() if k not in ('attempts','cash_events')})
        if name=='base_validation':
            dump(out/'mfa_full_history_parity.json',check_mfa_parity(original_mfa,result['summary'],
                [r for r in baseline_reference if r['candidate_id'].startswith('MFA-')],result['attempts']))
            old=first_intents(baseline_reference)
            new=first_intents([r for r in result['attempts'] if r['candidate_id'].startswith('MFA-')])
            if {k:v for k,v in old.items() if k[0].startswith('MFA-')}!=new:
                raise ValueError('MFA baseline intent drift')
            baseline_all=old|first_intents([r for r in result['attempts'] if r['candidate_id'].startswith('M500-')])
        elif first_intents(result['attempts'])!=baseline_all:
            raise ValueError('compound changed first signal/time/side/limit inventory')
        for r in result['summary']:
            r['scenario']='base' if name=='base_validation' else name
            r['cell_origin']=('baseline_MFA_parity_not_new' if r['candidate_id'].startswith('MFA-') else 'new_legacy_common_cohort') if name=='base_validation' else 'new_compound_sensitivity'
            metrics.append(r)
        if sha(tape)!=EXPECTED_TAPE_SHA or any(sha(code/n)!=v for n,v in identity.items()):
            raise ValueError('tape/code mutated during replay')
        print(json.dumps(dict(phase=name,executed_cells=len(result['summary']))),flush=True)
    if len(metrics)!=315:raise ValueError('panel inventory incomplete')
    dump(out/'metrics.json',metrics)
    dump(out/'completion.json',dict(status='completed_consumed_development',common_panel_cells=315,
        executed_cells=243,new_distinct_execution_scenarios=219,baseline_MFA_reproductions=24,
        reused_DPP_base_cells=72,new_candidates=0,receipts=receipts,
        source_observed_hours=result['dataset'].get('observed_hours'),complete_utc_days=0,
        formal_oos_result=None,ranking_admitted=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dpp-root','mfa-root','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();run(a.dpp_root,a.mfa_root,a.out)
