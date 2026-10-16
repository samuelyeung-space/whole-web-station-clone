"""Immutable correction of a completed tape's misreported producer-code hash.

This is a transport/provenance amendment, never a normalization or backtest.
Startup identity must match independently retrieved exact source bytes and
their Git blob IDs. Body bytes are copied verbatim and verified through EOF.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _json(line):
    def reject(value):
        raise ValueError('nonfinite JSON: ' + value)
    return json.loads(line, parse_constant=reject)


def inspect_tape(path, normalization):
    """Validate gzip CRC, exact header/body counts and raw uncompressed body."""
    counts, body_hash, body_bytes = Counter(), hashlib.sha256(), 0
    with gzip.open(path, 'rb') as f:
        header_bytes = f.readline()
        if not header_bytes.endswith(b'\n'):
            raise ValueError('incomplete header')
        header = _json(header_bytes)
        if header != normalization['header'] or header.get('type') != 'dataset':
            raise ValueError('header does not match completed producer evidence')
        for line in f:
            row = _json(line)
            if not isinstance(row, dict) or not isinstance(row.get('type'), str):
                raise ValueError('invalid event')
            if row['type'] == 'dataset':
                raise ValueError('multiple headers')
            counts[row['type']] += 1
            body_hash.update(line); body_bytes += len(line)
    if dict(counts) != normalization['counts']:
        raise ValueError('body event counts mismatch')
    return {'header': header, 'body_sha256': body_hash.hexdigest(),
            'body_bytes': body_bytes, 'body_counts': dict(counts),
            'original_header_bytes_sha256': hashlib.sha256(header_bytes).hexdigest()}


def amend(cache_dir, out_root, source_dir, *, source_commit, git_blob_ids, reason):
    """Return unchanged input or publish a new tape with an auditable amendment.

    git_blob_ids maps file names to independent GitHub blob identities, not
    values inferred from the producer manifest. The caller supplies the known
    startup revision; a mismatch refuses amendment rather than guessing.
    """
    cache_dir, out_root, source_dir = map(lambda p: Path(p).resolve(),
                                          (cache_dir, out_root, source_dir))
    if '.pending.' in cache_dir.name or (cache_dir/'failed.json').exists():
        raise ValueError('producer has not published a successful completed cache')
    manifest_path, tape = cache_dir/'manifest.json', cache_dir/'normalized.jsonl.gz'
    if not manifest_path.is_file() or not tape.is_file():
        raise ValueError('completed producer manifest and tape are required')
    if not reason.strip() or len(source_commit) != 40:
        raise ValueError('explicit reason and source commit required')
    manifest = json.loads(manifest_path.read_text())
    original_manifest_hash, original_tape_hash = sha(manifest_path), sha(tape)
    if manifest['normalized_sha256'] != original_tape_hash:
        raise ValueError('source tape compressed hash mismatch')
    if manifest['normalization'].get('sha256') != original_tape_hash:
        raise ValueError('normalization evidence tape hash mismatch')
    expected_names = {'historical_spot_development.py', 'replay_raw.py'}
    if set(git_blob_ids) != expected_names:
        raise ValueError('both normalizer and raw-admission source identities required')
    evidence = {}
    for name, blob_id in git_blob_ids.items():
        data = (source_dir/name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        git_digest = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if git_digest != blob_id:
            raise ValueError('independent Git source verification failed: ' + name)
        if manifest['identity']['code_sha256'].get(name) != digest:
            raise ValueError('startup producer identity differs from exact source: ' + name)
        evidence[name] = {'git_blob_sha1': git_digest, 'sha256': digest, 'bytes': len(data)}
    inspected = inspect_tape(tape, manifest['normalization'])
    header = inspected['header']
    actual = evidence['historical_spot_development.py']['sha256']
    recorded = header['provenance']['adapter_code_sha256']
    if recorded == actual:
        return {'status': 'no_amendment_needed', 'normalized_path': str(tape),
                'body_sha256': inspected['body_sha256'], 'new_backtests': 0}
    if 'code_identity_amendment' in header['provenance']:
        raise ValueError('source already has an amendment; refuse implicit chaining')
    amendment = {'schema': 'producer-code-hash-amendment-v1',
                 'recorded_adapter_code_sha256': recorded,
                 'actual_loaded_adapter_code_sha256': actual,
                 'startup_source_commit': source_commit, 'source_files': evidence,
                 'reason': reason, 'source_manifest_sha256': original_manifest_hash,
                 'source_tape_sha256': original_tape_hash,
                 'original_header_bytes_sha256': inspected['original_header_bytes_sha256'],
                 'unchanged_body_sha256': inspected['body_sha256'],
                 'unchanged_body_bytes': inspected['body_bytes']}
    corrected = copy.deepcopy(header)
    corrected['provenance']['adapter_code_sha256'] = actual
    corrected['provenance']['code_identity_amendment'] = amendment
    key = hashlib.sha256(json.dumps(amendment, sort_keys=True).encode()).hexdigest()
    out_root.mkdir(parents=True, exist_ok=True)
    destination = out_root/key
    if destination.exists():
        existing = json.loads((destination/'manifest.json').read_text())
        if existing['amendment'] != amendment or sha(destination/'normalized.jsonl.gz') != existing['normalized_sha256']:
            raise ValueError('existing amendment integrity mismatch')
        check = inspect_tape(destination/'normalized.jsonl.gz', existing['normalization'])
        if check['body_sha256'] != inspected['body_sha256']:
            raise ValueError('existing amendment body mismatch')
        return {**existing, 'cache_hit': True, 'normalized_path': str(destination/'normalized.jsonl.gz')}
    stage = Path(tempfile.mkdtemp(prefix=key+'.pending.', dir=out_root))
    try:
        body_hash = hashlib.sha256()
        with gzip.open(tape, 'rb') as src, (stage/'normalized.jsonl.gz').open('xb') as raw:
            src.readline()
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=1, mtime=0) as dst:
                dst.write(json.dumps(corrected, separators=(',', ':'), allow_nan=False).encode()+b'\n')
                while chunk := src.read(1024*1024):
                    body_hash.update(chunk); dst.write(chunk)
        if body_hash.hexdigest() != inspected['body_sha256']:
            raise ValueError('source body changed during copy')
        if sha(tape) != original_tape_hash or sha(manifest_path) != original_manifest_hash:
            raise ValueError('source changed during amendment')
        normalized_hash = sha(stage/'normalized.jsonl.gz')
        normalization = {**manifest['normalization'], 'header': corrected, 'sha256': normalized_hash}
        check = inspect_tape(stage/'normalized.jsonl.gz', normalization)
        if check['body_sha256'] != inspected['body_sha256']:
            raise ValueError('amended body differs')
        report = {'schema': 'immutable-provenance-amendment-v1', 'status': 'amended_header_only',
                  'source_cache': str(cache_dir), 'source_identity': manifest['identity'],
                  'amendment': amendment, 'original_header': header,
                  'normalization': normalization, 'normalized_sha256': normalized_hash,
                  'body_counts': check['body_counts'], 'body_bytes': check['body_bytes'],
                  'body_sha256': check['body_sha256'], 'new_backtests': 0,
                  'formal_oos_result': None}
        (stage/'manifest.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        stage.rename(destination)
        return {**report, 'cache_hit': False, 'normalized_path': str(destination/'normalized.jsonl.gz')}
    except BaseException:
        shutil.rmtree(stage)
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('cache-dir', 'out-root', 'source-dir', 'source-commit', 'git-blob-ids', 'reason'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    result = amend(a.cache_dir, a.out_root, a.source_dir, source_commit=a.source_commit,
                   git_blob_ids=json.loads(a.git_blob_ids), reason=a.reason)
    print(json.dumps(result, indent=2, allow_nan=False))
