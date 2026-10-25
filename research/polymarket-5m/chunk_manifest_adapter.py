"""Restore an opaque ZIP from saved subchunks using artifact_parts verification.

Offline only. No network, capture extraction, outcome parsing or economic replay.
The external recovery-manifest digest is mandatory; local_path is never trusted.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import artifact_parts as parts


def _unique(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('duplicate recovery manifest key')
        out[key] = value
    return out


def _basename(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in {'.', '..'}:
        raise ValueError('invalid recovery basename')
    return name


def restore(manifest, chunks_dir, original_name, output, expected_manifest_sha256):
    manifest, chunks_dir, output = Path(manifest), Path(chunks_dir), Path(output)
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 8 * parts.BLOCK:
        raise ValueError('invalid recovery manifest')
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest_sha256 or ''):
        raise ValueError('trusted manifest digest required')
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise ValueError('recovery manifest hash mismatch')
    recovery = json.loads(raw, object_pairs_hook=_unique)
    if recovery.get('schema') != 'opaque-original-zip-subchunks-v1':
        raise ValueError('unsupported recovery schema')
    _basename(original_name)
    originals = recovery.get('originals', [])
    selected = [o for o in originals if o.get('original_file') == original_name]
    if len(selected) != 1:
        raise ValueError('original must match exactly once')
    original = selected[0]
    chunks = original.get('chunks', [])
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 10000:
        raise ValueError('invalid recovery chunk count')
    if output.exists() or output.is_symlink():
        raise ValueError('output exists; refusing overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Rewrap only transport headers to use the existing canonical verifier.
    # At most one original's opaque ZIP size is staged; capture.tar is unopened.
    with tempfile.TemporaryDirectory(prefix='.chunk-recovery-', dir=output.parent) as tmp:
        stage = Path(tmp)
        canonical = []
        names = set()
        for index, chunk in enumerate(chunks):
            if type(chunk.get('index')) is not int or chunk['index'] != index:
                raise ValueError('noncanonical recovery chunk order')
            name = _basename(chunk.get('file_name'))
            if name in names:
                raise ValueError('duplicate recovery chunk filename')
            names.add(name)
            source = chunks_dir / name
            if source.is_symlink() or not source.is_file():
                raise ValueError('missing or symlink recovery chunk')
            if source.stat().st_size != chunk['zip_bytes']:
                raise ValueError('recovery chunk size mismatch')
            target = stage / f'part-{index:05d}.zip'
            with source.open('rb') as stream:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(parts.BLOCK), b''):
                    digest.update(block)
                if digest.hexdigest() != chunk['zip_sha256']:
                    raise ValueError('recovery chunk ZIP hash mismatch')
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    entries = archive.infolist()
                    if len(entries) != 1 or entries[0].filename != 'chunk.bin':
                        raise ValueError('unexpected recovery ZIP members')
                    info = entries[0]
                    if info.compress_type != zipfile.ZIP_STORED or info.file_size != chunk['payload_bytes']:
                        raise ValueError('unexpected recovery compression/size')
                    if type(info.file_size) is not int or not 0 < info.file_size <= parts.LIMIT - parts.BLOCK:
                        raise ValueError('invalid recovery payload size')
                    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as out:
                        with archive.open(info) as src, out.open('payload.bin', 'w') as dst:
                            shutil.copyfileobj(src, dst, parts.BLOCK)
            canonical.append({'index': index, 'name': target.name,
                              'payload_bytes': chunk['payload_bytes'],
                              'payload_sha256': chunk['payload_sha256'],
                              'zip_bytes': target.stat().st_size,
                              'zip_sha256': parts.digest_file(target)})
        normalized = {'schema': parts.SCHEMA, 'source_name': original_name,
                      'source_bytes': original['original_bytes'],
                      'source_sha256': original['original_zip_sha256'], 'parts': canonical}
        path = stage / 'manifest.json'
        path.write_text(json.dumps(normalized, sort_keys=True))
        result = parts.assemble(path, stage, output, parts.digest_file(path))
        result['recovery_manifest_sha256'] = expected_manifest_sha256
        result['original_file'] = original_name
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--chunks-dir', required=True)
    parser.add_argument('--original-name', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        result = restore(args.manifest, args.chunks_dir, args.original_name,
                         args.out, args.expected_manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc), 'pnl': None}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
