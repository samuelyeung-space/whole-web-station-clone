"""Hash-verified, bounded ZIP payloads for private replay data (no network access)."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

SCHEMA = 'replay-portable-payload-index-v2'


def sha_file(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '\\' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError(f'Unsafe relative path: {name!r}')
    return p


def index_files(index):
    if index.get('schema') == SCHEMA:
        return dict(index['files'])
    if index.get('schema') != 'replay-portable-payload-index-v1':
        raise ValueError('Unsupported payload index')
    result = {}
    for part in index['parts']:
        for entry in part['entries']:
            result[entry['path']] = {'sha256': entry['sha256'], 'bytes': entry['bytes'],
                'chunks': [{'part': part['name'], 'member': entry['path'],
                            'sha256': entry['sha256'], 'bytes': entry['bytes']}]}
    return result


def build(root, paths, out, prior=None, chunk_bytes=32*1024**2, max_part_bytes=44*1024**2):
    """Snapshot explicitly selected files; prior data is referenced, not copied."""
    root, out = Path(root).resolve(), Path(out).resolve()
    if chunk_bytes <= 0 or max_part_bytes < chunk_bytes + 4096:
        raise ValueError('Part budget must exceed one chunk plus ZIP overhead')
    out.mkdir(parents=True, exist_ok=True)
    prior = prior or {'schema': SCHEMA, 'parts': [], 'files': {}}
    files, parts = index_files(prior), list(prior['parts'])
    by_content = {(s['sha256'], s['bytes']): s for s in files.values()}
    pending, pending_size, part_entries = [], 0, []
    temp_path = None
    archive = None

    def flush():
        nonlocal pending, pending_size, part_entries, temp_path, archive
        if archive is None:
            return
        archive.close()
        if temp_path.stat().st_size > max_part_bytes:
            raise ValueError('ZIP exceeded explicit part budget')
        digest = sha_file(temp_path)
        name = f'Polymarket_BTC5m_Replay_Data_{digest[:12]}.zip'
        target = out / name
        if target.exists():
            if sha_file(target) != digest:
                raise ValueError('Payload filename collision')
            temp_path.unlink()
        else:
            os.replace(temp_path, target)
        for chunk in pending:
            chunk['part'] = name
        if not any(p['name'] == name for p in parts):
            parts.append({'name': name, 'local_path': str(target), 'sha256': digest,
                          'bytes': target.stat().st_size, 'entries': part_entries})
        pending, pending_size, part_entries, temp_path, archive = [], 0, [], None, None

    try:
        for name in sorted(set(paths)):
            safe_name(name)
            source = root.joinpath(*PurePosixPath(name).parts)
            if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root):
                raise ValueError(f'Not a regular in-root file: {name}')
            before = source.stat()
            digest, size = sha_file(source), before.st_size
            if name in files and files[name]['sha256'] == digest and files[name]['bytes'] == size:
                continue
            if (digest, size) in by_content:
                files[name] = by_content[(digest, size)]
                continue
            spec = {'sha256': digest, 'bytes': size, 'chunks': []}
            observed = hashlib.sha256()
            observed_size = 0
            with source.open('rb') as inp:
                number = 0
                while True:
                    data = inp.read(chunk_bytes)
                    if not data and number:
                        break
                    observed.update(data)
                    observed_size += len(data)
                    member = f'.replay_chunks/{digest}/{number:08d}'
                    # Reserve a conservative deflate bound; gzip tapes are stored directly.
                    overhead = 4096 + len(data)//1000 + 2*len(member.encode())
                    if archive is not None and pending_size + len(data) + overhead + 1024 > max_part_bytes:
                        flush()
                    if archive is None:
                        fd, temp_name = tempfile.mkstemp(prefix='payload-', suffix='.pending', dir=out)
                        os.close(fd)
                        temp_path = Path(temp_name)
                        archive = zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_STORED)
                    info = zipfile.ZipInfo(member, date_time=(1980,1,1,0,0,0))
                    info.external_attr = 0o600 << 16
                    info.compress_type = zipfile.ZIP_STORED if name.endswith(('.gz', '.zip')) else zipfile.ZIP_DEFLATED
                    archive.writestr(info, data, compresslevel=3)
                    chunk = {'member': member, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                    spec['chunks'].append(chunk)
                    pending.append(chunk)
                    part_entries.append({'path': member, 'sha256': chunk['sha256'], 'bytes': len(data)})
                    pending_size += len(data) + overhead
                    number += 1
                    if not data:
                        break
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f'Source changed during snapshot: {name}')
            if observed.hexdigest() != digest or observed_size != size:
                raise ValueError(f'Source bytes changed during snapshot: {name}')
            files[name] = spec
            by_content[(digest, size)] = spec
        flush()
    finally:
        if archive is not None:
            archive.close()
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return {'schema': SCHEMA, 'parts': parts, 'files': files,
            'restore': 'Use replay_payloads.py restore with all referenced ZIP parts. Verify every part, chunk and final file hash. Results from overlapping scenarios must not be summed.'}


def restore(index, payload_dir, destination, strip_prefix=''):
    payload_dir, destination = Path(payload_dir).resolve(), Path(destination).resolve()
    files = index_files(index)
    parts = {p['name']: p for p in index['parts']}
    if len(parts) != len(index['parts']):
        raise ValueError('Duplicate part identities')
    if strip_prefix:
        safe_name(strip_prefix)
        strip_prefix = strip_prefix.rstrip('/') + '/'
    verified, outputs = {}, []
    destination.mkdir(parents=True, exist_ok=True)
    try:
        for name, spec in sorted(files.items()):
            safe_name(name)
            if strip_prefix and not name.startswith(strip_prefix):
                raise ValueError('Every file must have the requested prefix')
            relative = name[len(strip_prefix):]
            safe_name(relative)
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if not target.resolve().is_relative_to(destination) or target.is_symlink():
                raise ValueError('Output path escapes destination')
            if target.exists():
                if target.is_file() and target.stat().st_size == spec['bytes'] and sha_file(target) == spec['sha256']:
                    outputs.append({'path': str(target), 'status': 'verified_existing'})
                    continue
                raise ValueError(f'Refusing to overwrite different bytes: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix='.restore-', dir=target.parent)
            temp = Path(temp_name)
            try:
                digest, size = hashlib.sha256(), 0
                with os.fdopen(fd, 'wb') as out:
                    for chunk in spec['chunks']:
                        part_name = chunk['part']
                        safe_name(part_name)
                        if '/' in part_name or part_name not in parts:
                            raise ValueError('Unknown or unsafe payload part')
                        if part_name not in verified:
                            p, path = parts[part_name], payload_dir / part_name
                            if path.is_symlink() or path.stat().st_size != p['bytes'] or sha_file(path) != p['sha256']:
                                raise ValueError('Payload checksum/length mismatch')
                            z = zipfile.ZipFile(path)
                            names = z.namelist()
                            if len(names) != len(set(names)):
                                z.close()
                                raise ValueError('Duplicate ZIP member')
                            verified[part_name] = z
                        safe_name(chunk['member'])
                        z = verified[part_name]
                        info = z.getinfo(chunk['member'])
                        if info.file_size != chunk['bytes']:
                            raise ValueError('Chunk length mismatch')
                        chunk_hash = hashlib.sha256()
                        with z.open(info) as inp:
                            for data in iter(lambda: inp.read(1024**2), b''):
                                out.write(data); digest.update(data); chunk_hash.update(data); size += len(data)
                        if chunk_hash.hexdigest() != chunk['sha256']:
                            raise ValueError('Chunk hash mismatch')
                if size != spec['bytes'] or digest.hexdigest() != spec['sha256']:
                    raise ValueError('Reassembled file checksum/length mismatch')
                os.replace(temp, target)
                outputs.append({'path': str(target), 'status': 'restored'})
            finally:
                temp.unlink(missing_ok=True)
    finally:
        for z in verified.values():
            z.close()
    return outputs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build'); b.add_argument('--root', required=True); b.add_argument('--files-json', required=True)
    b.add_argument('--out', required=True); b.add_argument('--index-out', required=True); b.add_argument('--prior-index')
    r = sub.add_parser('restore'); r.add_argument('--index', required=True); r.add_argument('--payload-dir', required=True)
    r.add_argument('--destination', required=True); r.add_argument('--strip-prefix', default='')
    args = p.parse_args()
    if args.command == 'build':
        prior = json.loads(Path(args.prior_index).read_text()) if args.prior_index else None
        result = build(args.root, json.loads(Path(args.files_json).read_text()), args.out, prior)
        Path(args.index_out).write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps({'files': len(result['files']), 'parts': len(result['parts'])}))
    else:
        print(json.dumps(restore(json.loads(Path(args.index).read_text()), args.payload_dir, args.destination, args.strip_prefix)))

if __name__ == '__main__':
    main()
