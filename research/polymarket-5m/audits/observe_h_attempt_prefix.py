"""Observe a predeclared consumed-data prefix; never load settlement labels.

Runtime instrumentation and the 500/600/1000ms diagnostic are a new research
identity. The immutable source-race dependency closure is materialized first.
No old freeze is modified; this CLI cannot produce profit or ranking evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict,replace
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_FREEZE='29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63'
DURATION_MS=480000.0
LAGS=(500.0,600.0,1000.0)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1<<20):h.update(b)
    return h.hexdigest()

def worker(data,out,*,duration_ms=DURATION_MS,branch_opportunities=False):
    import h_source_race as race
    import h_replay_run as engine
    import h_replay_archive as archive
    import h_attempt_observer as observer
    if branch_opportunities:
        import h_branch_observer as branch_observer
        observed_class=branch_observer.BranchObservedHReplay
    else:
        observed_class=observer.ObservedHReplay
    freeze=Path(race.__file__).parent/'forward/source-race-freeze.json'
    assert sha(freeze)==EXPECTED_FREEZE
    registered=json.loads(freeze.read_text())
    current=json.loads((freeze.parent/'current-h-freeze.json').read_text())
    config=race._receipt_clock_replay_freeze(current,registered)
    original=engine.replay.config_from_freeze(config)
    execution=replace(original,evaluation_ms=LAGS)
    signal=engine.signal_config_from_freeze(config)
    identity,stats,root=race._capture_artifact_identity(data)
    start=float(identity['started_ms']);stop=start+duration_ms
    if stop>float(identity['ended_ms']):
        raise ValueError('diagnostic prefix exceeds captured input')
    mappings=list(archive.iter_market_mappings(root/'market_registry.csv.gz'))
    observed=Counter();suppressed=Counter();source_counts=Counter()
    trace=out/'attempts.jsonl.gz'
    signal_ids=set();branch_keys={};unsent_keys=set();evaluated_keys=set();machines=[]
    def bounded(path,family):
        for row in archive.iter_normalized_events(path,family=family):
            if float(row['recv_ms'])>stop:break
            if float(row['recv_ms'])<start:
                raise ValueError('event precedes declared capture start')
            source_counts[family]+=1
            yield row
    with trace.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as stream:
            def sink(event):
                observed[event['kind']]+=1
                key=(event['audit_signal_sequence'],float(event['evaluation_ms'])) if 'evaluation_ms' in event else None
                if event['kind']=='eligible_signal':
                    assert event['audit_signal_sequence'] not in signal_ids
                    signal_ids.add(event['audit_signal_sequence'])
                if event['kind']=='unsent_attempt':
                    suppressed[(event['evaluation_ms'],event['reason'],event['signal_source'])]+=1
                    assert event['pnl'] is None and event['sent'] is False
                    assert key not in unsent_keys
                    unsent_keys.add(key)
                if event['kind']=='evaluation':
                    key=(event['audit_signal_sequence'],float(event['evaluation']['evaluation_ms']))
                    assert key not in evaluated_keys
                    evaluated_keys.add(key)
                if event['kind']=='branch_opportunity':
                    assert key not in branch_keys and key[0] in signal_ids
                    assert key[1] in LAGS and event['pnl'] is None
                    branch_keys[key]=event['branch_status']
                stream.write((json.dumps(event,sort_keys=True,allow_nan=False)+'\n').encode())
            original_class=engine.replay.HReplay
            def factory(cfg):
                machine=observed_class(cfg,sink=sink);machines.append(machine);return machine
            engine.replay.HReplay=factory
            try:
                rows,counters=engine.replay_normalized(
                    bounded(root/'source_events.jsonl.gz','source'),
                    bounded(root/'clob_events.jsonl.gz','clob'),
                    mappings,{},execution,signal,trigger_sources=race.CANDIDATE_SOURCES)
            finally:
                engine.replay.HReplay=original_class
    expected=Counter({(r['evaluation_ms'],r['reason'],r['source']):r['count']
        for r in counters.get('_suppressed_unsent',[])})
    assert suppressed==expected
    assert observed['evaluation']==len(rows)
    assert all(r['winner'] is None and r['pnl'] is None for r in rows)
    branch_audit=None
    if branch_opportunities:
        assert len(machines)==1
        machine=machines[0]
        pending_keys={(machine._attempt_signal[id(attempt)],float(delay))
            for _,_,delay,attempt in machine._pending}
        assert set(branch_keys)=={(seq,delay) for seq in signal_ids for delay in LAGS}
        assert {key for key,status in branch_keys.items() if status=='suppressed_unsent'}==unsent_keys
        scheduled={key for key,status in branch_keys.items() if status.startswith('scheduled_')}
        assert not (evaluated_keys&pending_keys)
        assert scheduled==evaluated_keys|pending_keys
        assert len(pending_keys)==counters['censored_evaluations']
        branch_audit={'schema':'h-eligible-branch-ledger-audit-v1',
            'eligible_signals':len(signal_ids),'delay_branch_opportunities':len(branch_keys),
            'every_signal_delay_has_exactly_one_outcome':True,
            'suppressed_unsent_join_exact':True,'evaluated_or_censored_join_exact':True,
            'skipped_opportunities_are_order_attempts':False,
            'per_delay':[{'evaluation_ms':d,'status':s,'skip_reason':why or None,'count':count}
                for (d,s,why),count in sorted(machine.branch_counts.items())],
            'pending_evaluation_keys':sorted(pending_keys),
            'complete_upstream_rejection_ledger':False}
    observed_input_counts=dict(source_counts)
    source_counts.clear()
    control_rows,control_counters=engine.replay_normalized(
        bounded(root/'source_events.jsonl.gz','source'),
        bounded(root/'clob_events.jsonl.gz','clob'),
        mappings,{},execution,signal,trigger_sources=race.CANDIDATE_SOURCES)
    assert rows==control_rows and counters==control_counters
    assert dict(source_counts)==observed_input_counts
    assert race._artifact_stats(root)==stats
    result={'schema':'h-branch-prefix-diagnostic-v1' if branch_opportunities else 'h-attempt-prefix-diagnostic-v1',
        'classification':'engineering_validation_on_consumed_data',
        'duration_ms':duration_ms,'start_ms':start,'stop_ms':stop,
        'active_sources':sorted(race.CANDIDATE_SOURCES),'evaluation_ms':LAGS,
        'artifact_identity':identity,'input_events':dict(source_counts),
        'original_execution_config':asdict(original),'observed_execution_config':asdict(execution),
        'engine_sha256':observer.verify_engine(),
        'observer_sha256':sha(Path(observer.__file__)),
        'branch_observer_sha256':sha(Path(branch_observer.__file__)) if branch_opportunities else None,
        'eligible_branch_audit':branch_audit,
        'worker_sha256':sha(Path(__file__)),'trace_sha256':sha(trace),
        'trace_bytes':trace.stat().st_size,'observations':dict(observed),
        'raw_counters':counters,'unsent_bucket_reconciliation':True,
        'evaluation_count_reconciliation':True,'artifact_files_unchanged':True,
        'real_prefix_control_records_and_counters_identical':True,
        'settlement_labels_loaded':False,'financial_result':None,
        'formal_oos_result':None,'rank7_pnl_usd':None,
        'old_freezes_changed':False,'complete_upstream_rejection_ledger':False,
        'real_exchange_execution_evidence':False}
    (out/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'observations':dict(observed),'input_events':dict(source_counts),
        'reconciled':True,'financial_result':None}),flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--archive',required=True,type=Path)
    ap.add_argument('--out-dir',required=True,type=Path)
    ap.add_argument('--worker',action='store_true')
    ap.add_argument('--duration-ms',type=float,default=DURATION_MS)
    ap.add_argument('--branch-opportunities',action='store_true')
    a=ap.parse_args();data=a.archive.resolve();out=a.out_dir.resolve()
    if not math.isfinite(a.duration_ms) or a.duration_ms<=0:
        ap.error('duration-ms must be positive and finite')
    if a.worker:
        worker(data,out,duration_ms=a.duration_ms,branch_opportunities=a.branch_opportunities);return
    sys.path.insert(0,str(ROOT))
    import frozen_dependency_runner as runner
    freeze=ROOT/'forward/source-race-freeze.json'
    assert sha(freeze)==EXPECTED_FREEZE
    frozen,_=runner._read_freeze(freeze)
    audit,selected=runner._select_dependencies(ROOT,ROOT/'frozen-dependencies/source-race-v1',
        frozen['dependencies_sha256'])
    out.mkdir(parents=True,exist_ok=False)
    registration={'schema':'h-attempt-prefix-registration-v1',
        'duration_ms':a.duration_ms,'evaluation_ms':LAGS,'data_already_consumed':True,
        'branch_opportunities':a.branch_opportunities,
        'settlement_labels_loaded':False,'financial_result':None,
        'formal_oos_result':None,'rank7_pnl_usd':None,
        'worker_code_sha256':sha(Path(__file__)),
        'observer_code_sha256':sha(ROOT/'h_attempt_observer.py'),
        'branch_observer_code_sha256':sha(ROOT/'h_branch_observer.py') if a.branch_opportunities else None,
        'registered_source_race_freeze_sha256':EXPECTED_FREEZE,
        'old_freezes_changed':False,'dependencies':audit}
    reg=out/'registration.json'
    reg.write_text(json.dumps(registration,sort_keys=True,indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='observed-h-prefix-') as tmp:
        root=Path(tmp)
        runner._materialize(root,freeze,Path('forward/source-race-freeze.json'),selected)
        shutil.copyfile(ROOT/'h_attempt_observer.py',root/'h_attempt_observer.py')
        if a.branch_opportunities:
            shutil.copyfile(ROOT/'h_branch_observer.py',root/'h_branch_observer.py')
        shutil.copyfile(__file__,root/'worker.py')
        command=[sys.executable,str(root/'worker.py'),'--worker','--archive',str(data),
            '--out-dir',str(out),'--duration-ms',str(a.duration_ms)]
        if a.branch_opportunities:command.append('--branch-opportunities')
        process=subprocess.run(command,cwd=root)
    registration['exit_code']=process.returncode
    reg.write_text(json.dumps(registration,sort_keys=True,indent=2)+'\n')
    raise SystemExit(process.returncode)

if __name__=='__main__':main()
