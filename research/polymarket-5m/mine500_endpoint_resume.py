"""Recover a truncated derived gzip from its immutable source and expected hash.

No thresholds, inputs or feature semantics may change. The original failure
and damaged file hash remain recorded. A regenerated file must be BYTE IDENTICAL
to the original post-write digest before atomically replacing the damaged file.
This is a continuation of the same 24 attempts, not 24 additional trials.
"""
import argparse
import gzip
from pathlib import Path
import json

import mine500_endpoint as ep


def verify(path, expected):
    if ep.batch.digest(path)!=expected:return False
    try:
        with gzip.open(path,'rb') as f:
            while f.read(1024*1024):pass
    except (EOFError,OSError):return False
    return True


def resume(out):
    reg=json.loads((out/'registration.json').read_text())
    if reg['candidates']!=ep.candidates():raise ValueError('registered rules changed')
    if (out/'completion.json').exists():raise ValueError('already completed; do not repeat')
    coverage=json.loads((out/'feature_coverage.json').read_text())
    paths,repairs=[],[]
    for i,(source,audit) in enumerate(zip(reg['inputs'],coverage,strict=True)):
        path=out/f'endpoint_{i}.jsonl.gz';paths.append(path)
        expected=audit['output_sha256']
        if verify(path,expected):continue
        old_hash=ep.batch.digest(path)
        for key in ('normalized','source'):
            if ep.batch.digest(source[key])!=source[key+'_sha256']:raise ValueError('original input changed')
        tmp=path.with_name(path.name+'.repair.gz'); new_audit=dict(run_id=source['run_id'])
        ep.batch.write_gzip(tmp,ep.augment(ep.execution.read_jsonl(source['normalized']),
            ep.execution.read_jsonl(source['source']),new_audit))
        if not verify(tmp,expected):raise ValueError('repair not byte-identical to recorded original')
        if {k:v for k,v in audit.items() if k!='output_sha256'}!=new_audit:
            raise ValueError('repair coverage changed')
        tmp.replace(path)
        repairs.append(dict(path=path.name,damaged_sha256=old_hash,restored_sha256=expected,
            original_source_hashes_verified=True,byte_identical_repair=True))
    ep.batch.write_json(out/'repair_audit.json',dict(repairs=repairs,additional_trials=0,
        executor_sha256=ep.batch.digest(ep.execution.__file__),
        feature_code_sha256=ep.batch.digest(ep.__file__),formal_oos_result=None))
    ep.replay_prepared(out,paths,reg['candidates'])


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);args=ap.parse_args()
    resume(Path(args.out))
