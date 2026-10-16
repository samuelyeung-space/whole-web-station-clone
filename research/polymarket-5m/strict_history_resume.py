"""Resume audited historical conversion without re-reading a finished raw audit.

The prior failed intake remains intact. Missing final outcomes are retained as
unresolved markets, rather than deleting the day or selecting resolved winners.
No recording or formal freeze is modified. All results remain development.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import tempfile

import h_replay_archive as contracts
import historical_spot_development as normalizer
import historical_stream_audit as audit
import mine500_adapter as adapter
import replay_raw
import spot_stream_projection as projection


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def resume(stage, output_root, *, interrupted=False):
    start = time.perf_counter()
    stage = Path(stage).resolve()
    failure_path = stage/'failed.json'
    if not failure_path.is_file() and not interrupted:
        raise ValueError('resume requires failed intake or explicit interrupted acknowledgement')
    if (stage/'manifest.json').is_file():
        raise ValueError('completed intake must use its original cache')
    prior_status = {'kind': 'failed' if failure_path.is_file() else 'interrupted',
                    'failed_sha256': sha(failure_path) if failure_path.is_file() else None}
    evidence = json.loads((stage/'stream_audit.json').read_text())
    roots = list((stage/'extracted').rglob('bundle-btc/strict/manifest.json'))
    if len(roots) != 1:
        raise ValueError('one retained strict source required')
    parent = roots[0].parent.parent.parent
    for name, value in evidence['input_sha256'].items():
        if sha(parent/'bundle-btc/strict'/name) != value:
            raise ValueError('retained audit input changed: '+name)
    if evidence['audit_code_sha256'] != sha(audit.__file__) or evidence['validator_sha256'] != sha(contracts.__file__):
        raise ValueError('audit/validator changed; fresh audit required')
    projected = json.loads((stage/'spot/manifest.json').read_text())
    if (projected['code_sha256'] != sha(projection.__file__) or
            projected['output_sha256'] != sha(stage/'spot/source_events.jsonl.gz')):
        raise ValueError('retained spot projection changed')
    identity = {'schema':'strict-history-unresolved-v2', 'audit_inputs':evidence['input_sha256'],
        'raw_metadata_sha256':{str(p.relative_to(parent)):sha(p) for p in sorted((parent/'raw').glob('*/markets.jsonl.gz'))},
        'code_sha256':{Path(m.__file__).name:sha(m.__file__) for m in
                       (contracts,normalizer,audit,adapter,projection,replay_raw)},
        'allow_unresolved':True,'prior_status':prior_status}
    identity['code_sha256']['strict_history_resume.py'] = sha(__file__)
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    target = Path(output_root).resolve()/key
    manifest_path = target/'manifest.json'
    if manifest_path.exists():
        r = json.loads(manifest_path.read_text())
        if r['identity'] != identity or sha(target/'normalized.jsonl.gz') != r['normalization']['sha256']:
            raise ValueError('resume cache corrupted')
        replay_raw.validate_normalized(target/'normalized.jsonl.gz', r['normalization'])
        return {**r,'cache_hit':True,'elapsed_seconds':time.perf_counter()-start}
    target.parent.mkdir(parents=True,exist_ok=True)
    # A process can disappear before writing a failure marker. Preserve its
    # bytes and start a new attempt; never promote an unverified partial tape.
    if target.exists():
        abandoned = Path(tempfile.mkdtemp(prefix=key+'.interrupted.', dir=target.parent))
        abandoned.rmdir()
        target.rename(abandoned)
    target.mkdir(exist_ok=False)
    (target/'attempt.json').write_text(json.dumps({'identity':identity,'prior_intake':str(stage)},indent=2)+'\n')
    try:
        r = normalizer.normalize(parent,stage/'spot',stage/'stream_audit.json',
                                 target/'normalized.jsonl.gz',allow_unresolved=True)
        verified = replay_raw.validate_normalized(target/'normalized.jsonl.gz',r)
        result = {'identity':identity,'normalization':r,'normalized_path':str(target/'normalized.jsonl.gz'),
            'integrity':verified,'prior_failed_intake':str(stage),'cache_hit':False,
            'elapsed_seconds':time.perf_counter()-start,'formal_oos_result':None}
        manifest_path.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        return result
    except Exception as exc:
        (target/'failed.json').write_text(json.dumps({'error':str(exc),'pnl':None,'identity':identity},indent=2)+'\n')
        raise


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',required=True);p.add_argument('--out',required=True)
    p.add_argument('--interrupted',action='store_true',help='Acknowledge prior producer stopped without a completion marker')
    a=p.parse_args();r=resume(a.stage,a.out,interrupted=a.interrupted)
    print(json.dumps({k:v for k,v in r.items() if k not in ('normalization','identity')},indent=2))
