"""Development-only 600/1000ms source-race stress from the frozen dependency closure.

The registered source-race comparison and all old freezes remain untouched.
This separate worker changes only actual evaluation timestamps in ReplayConfig.
It does not create Rank7, independent OOS, or fixed-100-share evidence.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_FREEZE='29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63'
LAGS=(600.0,1000.0)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1<<20):h.update(b)
    return h.hexdigest()

def canonical(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def worker(archive,out):
    import h_source_race as race
    import h_replay_run as engine
    import h_replay as replay
    import h_replay_archive as artifact
    freeze=Path(race.__file__).parent/'forward/source-race-freeze.json'
    assert sha(freeze)==EXPECTED_FREEZE
    registered=json.loads(freeze.read_text())
    current=json.loads((Path(race.__file__).parent/'forward/current-h-freeze.json').read_text())
    config=race._receipt_clock_replay_freeze(current,registered)
    original=replay.config_from_freeze(config)
    stressed=replace(original,evaluation_ms=LAGS)
    assert {k:v for k,v in asdict(stressed).items() if k!='evaluation_ms'}=={
        k:v for k,v in asdict(original).items() if k!='evaluation_ms'}
    signal=engine.signal_config_from_freeze(config)
    root=archive/'strict' if (archive/'strict').is_dir() else archive
    identity,stats,root=race._capture_artifact_identity(archive)
    mappings=list(artifact.iter_market_mappings(root/'market_registry.csv.gz'))
    outcomes={r['market_id']:r['winner'] for r in artifact.iter_outcomes(root/'market_outcomes.csv.gz')}
    out.mkdir(parents=True,exist_ok=True)
    result={'schema':'source-race-execution-stress-development-v1',
            'classification':'consumed_partial_day_development',
            'registered_source_race_freeze_sha256':EXPECTED_FREEZE,
            'evaluation_ms':LAGS,'artifact_identity':identity,
            'original_execution_config':asdict(original),
            'stress_execution_config':asdict(stressed),
            'rules_and_original_freezes_changed':False,
            'rank7_pnl_usd':None,'formal_oos_result':None,
            'fixed_100_share_evidence':False,'arms':{}}
    for arm,sources in [('baseline',race.BASELINE_SOURCES),('candidate',race.CANDIDATE_SOURCES)]:
        rows,counters=engine.replay_normalized(
            artifact.iter_normalized_events(root/'source_events.jsonl.gz',family='source'),
            artifact.iter_normalized_events(root/'clob_events.jsonl.gz',family='clob'),
            mappings,outcomes,stressed,signal,trigger_sources=sources)
        rowpath=out/(arm+'.jsonl')
        with rowpath.open('w') as f:
            for r in rows:
                r['observation_run_id']=str(identity['run_id'])
                r['strategy_id']=str(current['strategy_id'])
                assert r['ordering_clock']=='receive_ms'
                assert r['match_book_clock']=='receive_ms'
                assert abs(r['evaluation_time_ms']-r['signal_receive_ms']-r['evaluation_ms'])<1e-3
                if r.get('book_receive_ms') is not None:
                    assert r['book_receive_ms']<=r['evaluation_time_ms']+1e-6
                f.write(json.dumps(r,sort_keys=True,allow_nan=False)+'\n')
        result['arms'][arm]={'summary':engine.summarize(rows,counters),
            'raw_counters':counters,'rows_sha256':sha(rowpath),'rows':len(rows),
            'active_sources':sorted(sources)}
        print(arm+' complete',flush=True)
    assert race._artifact_stats(root)==stats
    result['artifact_files_unchanged']=True
    target=out/'result.json'
    target.write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--archive',required=True,type=Path);ap.add_argument('--out-dir',required=True,type=Path)
    ap.add_argument('--worker',action='store_true')
    a=ap.parse_args(); archive=a.archive.resolve();out=a.out_dir.resolve()
    if a.worker:
        worker(archive,out);return
    sys.path.insert(0,str(ROOT))
    import frozen_dependency_runner as runner
    freeze=ROOT/'forward/source-race-freeze.json'
    assert sha(freeze)==EXPECTED_FREEZE
    frozen,_=runner._read_freeze(freeze)
    audit,selected=runner._select_dependencies(ROOT,ROOT/'frozen-dependencies/source-race-v1',frozen['dependencies_sha256'])
    out.mkdir(parents=True,exist_ok=True)
    registration={'schema':'development-execution-stress-registration-v1',
        'worker_code_sha256':sha(Path(__file__)),
        'source_race_freeze_sha256':EXPECTED_FREEZE,
        'evaluation_ms':LAGS,'data_already_consumed':True,
        'rule_parameters_retuned':False,'new_confirmatory_window':None,
        'dependencies':audit,'out_of_sample':False}
    reg=out/'registration.json';reg.write_text(json.dumps(registration,sort_keys=True,indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='registered-lag-stress-') as tmp:
        root=Path(tmp)
        runner._materialize(root,freeze,Path('forward/source-race-freeze.json'),selected)
        shutil.copyfile(__file__,root/'stress_worker.py')
        result=subprocess.run([sys.executable,str(root/'stress_worker.py'),'--worker',
            '--archive',str(archive),'--out-dir',str(out)],cwd=root)
    registration['exit_code']=result.returncode
    reg.write_text(json.dumps(registration,sort_keys=True,indent=2)+'\n')
    raise SystemExit(result.returncode)

if __name__=='__main__':
    main()
