"""Preregister DPP and execute all 24 variants on consumed development only."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
import gzip
import hashlib
import io
import json
from pathlib import Path

import depth_pressure_persistence as dpp
import replay_engine as ex
from cash_release_audit import reconcile


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def dump(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,allow_nan=False);f.write('\n')


def write_rows(path,rows):
    digest=hashlib.sha256();count=0
    with Path(path).open('xb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0,compresslevel=1) as f:
        for row in rows:
            line=(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n').encode()
            digest.update(line);f.write(line);count+=1
    observed=0
    with gzip.open(path,'rt') as f:
        for line in f:json.loads(line);observed+=1
    if observed!=count:raise ValueError('incomplete gzip output')
    return dict(rows=count,sha256=sha(path),logical_sha256=digest.hexdigest(),gzip_eof_crc_verified=True)


def run(tape,expected_sha,out):
    tape,out=Path(tape),Path(out)
    if sha(tape)!=expected_sha:raise ValueError('input hash mismatch')
    out.mkdir(parents=True,exist_ok=False);code=Path(__file__).parent
    names=['depth_pressure_persistence.py','run_depth_pressure_study.py','replay_engine.py',
           'mine500_constraints.py','cash_release_audit.py']
    identity={name:sha(code/name) for name in names}
    configs=[('base',ex.ReplayConfig(shares=100,initial_cash_usd=1000,require_constraints=True)),
        ('half_depth',ex.ReplayConfig(shares=100,initial_cash_usd=1000,require_constraints=True,depth_fraction=.5)),
        ('plus_1cent',ex.ReplayConfig(shares=100,initial_cash_usd=1000,require_constraints=True,
                                   extra_cost_per_filled_share_usd=.01))]
    protocol=dict(schema='dpp-study-v1',registered_at_utc=datetime.now(timezone.utc).isoformat(),
        evidence_class='already_consumed_development_adaptive_new_variants',input_sha256=expected_sha,
        candidates=dpp.candidates(),code_sha256=identity,configs={name:asdict(c) for name,c in configs},
        expected_new_scenario_rows=216,new_candidates=24,new_independent_mechanisms_claimed=0,
        prior_catalog_trials_preserved=720,
        prior_art='3-level Up imbalance exists; joint Up-minus-Down persistence, complete 3/5s paths and price interactions are new variants, not independent mechanisms',
        multiple_testing='All 24 variants and 216 related execution scenarios reported, plus prior history; no denominator reset, no independent-evidence sum',
        feature_policy='four/six consecutive 1s decision samples with fresh direct bids/asks both tokens; no full raw feed continuity, no aggressor/cancellation inference',
        ranking_admitted=False,formal_oos_result=None)
    dump(out/'protocol.json',protocol)
    coverage=Counter()
    tape_receipt=write_rows(out/'dpp_events.jsonl.gz',dpp.augment(ex.read_jsonl(tape),coverage))
    dump(out/'feature_receipt.json',dict(input_sha256=expected_sha,output=tape_receipt,counts=dict(coverage)))
    print(json.dumps(dict(phase='features',counts=dict(coverage))),flush=True)
    metrics=[];receipts={};reference=None
    for name,cfg in configs:
        result=ex.replay_events(ex.read_jsonl(out/'dpp_events.jsonl.gz'),dpp.candidates(),dpp.signal,cfg)
        audit=reconcile(result['attempts'],result['cash_events'],result['summary'],0)
        keys={(r['candidate_id'],r['latency_ms'],r['market_id']):(r['decision_ms'],r['side']) for r in result['attempts']}
        if reference is None:reference=keys
        elif keys!=reference:raise ValueError('stress changed first signal inventory/time/side')
        receipts[name]=dict(attempts=write_rows(out/f'{name}_attempts.jsonl.gz',result.pop('attempts')),
                            cash_events=write_rows(out/f'{name}_cash.jsonl.gz',result.pop('cash_events')),
                            cash_audit=audit)
        for r in result['summary']:r['scenario']=name;metrics.append(r)
        dump(out/f'{name}_replay.json',result)
        if any(sha(code/n)!=v for n,v in identity.items()) or sha(tape)!=expected_sha:
            raise ValueError('code/input changed during run')
        print(json.dumps(dict(phase=name,scenario_rows=len(result['summary']))),flush=True)
    dump(out/'metrics.json',metrics)
    dump(out/'completion.json',dict(status='completed_consumed_development',new_candidates=24,
        new_scenario_rows=len(metrics),receipts=receipts,source_observed_hours=result['dataset'].get('observed_hours'),
        full_utc_days=0,ranking_admitted=False,formal_oos_result=None))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for x in ('tape','expected-sha','out'):p.add_argument('--'+x,required=True)
    a=p.parse_args();run(a.tape,a.expected_sha,a.out)
