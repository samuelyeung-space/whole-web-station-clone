"""Offline admission of already downloaded recording archives; never downloads.

The existing spot/CLOB causal adapters own normalization. Legacy data without
the required clocks/depth is retained as a failed intake, never interpolated.
"""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile


MAX_EXPANDED = 32 * 1024**3
STRICT_NAMES = {'manifest.json', 'source_events.jsonl.gz', 'clob_events.jsonl.gz',
                'market_registry.csv.gz', 'market_outcomes.csv.gz'}


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def validate_normalized(path, normalization):
    """Read to gzip EOF/CRC and reconcile the emitted tape with its evidence.

    The existing normalizer counts body events, excluding its dataset header.
    A matching file hash alone does not establish that a producer finished.
    """
    if not isinstance(normalization, dict):
        raise ValueError('normalization evidence must be an object')
    expected_header = normalization.get('header')
    expected_counts = normalization.get('counts')
    if not isinstance(expected_header, dict) or expected_header.get('type') != 'dataset':
        raise ValueError('normalization dataset header missing')
    if (not isinstance(expected_counts, dict) or 'dataset' in expected_counts
            or any(not isinstance(k, str) or not k or type(v) is not int or v < 0
                   for k, v in expected_counts.items())):
        raise ValueError('normalization body counts invalid')

    def invalid_constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)

    counts, lines = Counter(), 0
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            # Exhausting the iterator, including an attempted read after the
            # last record, is required to check the gzip trailer and CRC.
            for lines, line in enumerate(handle, 1):
                row = json.loads(line, parse_constant=invalid_constant)
                if not isinstance(row, dict) or not isinstance(row.get('type'), str) or not row['type']:
                    raise ValueError(f'normalized line {lines} requires an object and type')
                if lines == 1:
                    if row.get('type') != 'dataset':
                        raise ValueError('normalized first line must be dataset header')
                    if row != expected_header:
                        raise ValueError('normalized dataset header mismatch')
                elif row['type'] == 'dataset':
                    raise ValueError('normalized tape contains multiple dataset headers')
                else:
                    counts[row['type']] += 1
    except (OSError, EOFError, UnicodeError, ValueError) as exc:
        raise ValueError(f'normalized stream integrity failure: {exc}') from exc
    if not lines:
        raise ValueError('normalized dataset header missing from empty tape')
    if dict(counts) != expected_counts:
        raise ValueError(f'normalized body counts mismatch: observed={dict(counts)}, expected={expected_counts}')
    return {'records': lines, 'dataset_headers': 1, 'body_counts': dict(counts),
            'gzip_eof_crc_verified': True}


def _safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or '\x00' in name:
        raise ValueError('unsafe archive path')
    return p


def _wanted(p):
    return (len(p.parts) >= 3 and p.parts[-3:-1] == ('bundle-btc', 'strict')
            and p.name in STRICT_NAMES) or ('raw' in p.parts and p.name == 'markets.jsonl.gz')


def extract_recording(source, destination):
    """Extract only BTC strict tape plus original Gamma metadata, with limits."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    written = set()

    def copy_member(name, size, handle):
        nonlocal total
        p = _safe_name(name)
        if not _wanted(p):
            return
        if str(p) in written:
            raise ValueError('duplicate archive member: ' + str(p))
        written.add(str(p)); total += size
        if size < 0 or total > MAX_EXPANDED:
            raise ValueError('archive expanded size limit exceeded')
        target = destination.joinpath(*p.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as out:
            remaining = size
            while remaining:
                chunk = handle.read(min(1024**2, remaining))
                if not chunk:
                    raise ValueError('truncated archive member')
                out.write(chunk); remaining -= len(chunk)

    def read_tar(handle):
        with tarfile.open(fileobj=handle, mode='r|*') as archive:
            for info in archive:
                p = _safe_name(info.name)
                if info.issym() or info.islnk():
                    raise ValueError('archive links are unsupported')
                if info.isfile() and _wanted(p):
                    with archive.extractfile(info) as member:
                        copy_member(info.name, info.size, member)

    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = set()
            for info in archive.infolist():
                p = _safe_name(info.filename)
                if info.filename in names:
                    raise ValueError('duplicate ZIP member')
                names.add(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('ZIP links are unsupported')
                if info.is_dir():
                    continue
                with archive.open(info) as member:
                    if p.name == 'raw.tgz' or p.name.endswith(('.tar.gz', '.tgz', '.tar')):
                        read_tar(member)
                    elif _wanted(p):
                        copy_member(info.filename, info.file_size, member)
    else:
        with source.open('rb') as member:
            read_tar(member)
    manifests = list(destination.rglob('bundle-btc/strict/manifest.json'))
    if len(manifests) != 1:
        raise ValueError('expected one BTC strict manifest; legacy/ladder-only archive requires its own adapter')
    return manifests[0].parent.parent.parent


def prepare_raw(source, cache_root):
    """Return a reusable normalized tape, or retain failed-intake diagnostics."""
    import historical_stream_audit as audit
    import historical_spot_development as normalizer
    import spot_stream_projection as projection
    import mine500_adapter as adapter
    import h_replay_archive as contracts
    started = time.perf_counter()
    source, cache_root = Path(source).resolve(), Path(cache_root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        parent = source
        strict = parent/'bundle-btc/strict'
        paths = [strict/n for n in sorted(STRICT_NAMES)] + sorted((parent/'raw').glob('*/markets.jsonl.gz'))
        if not paths or any(not p.is_file() or p.is_symlink() for p in paths):
            raise ValueError('strict recording directory incomplete or contains links')
        inputs = {str(p.relative_to(parent)): sha(p) for p in paths}
    else:
        inputs = {'archive_sha256': sha(source)}
    codes = {Path(m.__file__).name: sha(m.__file__) for m in
             (audit, normalizer, projection, adapter, contracts)}
    codes['replay_raw.py'] = sha(__file__)
    identity = {'schema': 'replay-raw-v1', 'inputs': inputs, 'code_sha256': codes}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    destination = cache_root/key
    if destination.exists():
        try:
            manifest = json.loads((destination/'manifest.json').read_text())
            if manifest['identity'] != identity or sha(destination/'normalized.jsonl.gz') != manifest['normalized_sha256']:
                raise ValueError('raw normalization cache integrity mismatch')
            validate_normalized(destination/'normalized.jsonl.gz', manifest['normalization'])
        except Exception as exc:
            # Do not replace a corrupt cache or mutate its evidence. Preserve a
            # separate diagnostic for this refused intake attempt.
            failed = Path(tempfile.mkdtemp(prefix=key+'.invalid.', dir=cache_root))/'failed.json'
            failed.write_text(json.dumps({'error': str(exc), 'identity': identity,
                'cache_path': str(destination), 'pnl': None, 'completed_backtests': 0,
                'status': 'blocked_raw_cache_integrity'}, indent=2))
            raise ValueError(f'raw cache integrity blocked; diagnostics: {failed}: {exc}') from exc
        return {**manifest, 'cache_hit': True, 'normalized_path': str(destination/'normalized.jsonl.gz'),
                'elapsed_seconds': time.perf_counter()-started}
    stage = Path(tempfile.mkdtemp(prefix=key+'.pending.', dir=cache_root))
    try:
        if not source.is_dir():
            parent = extract_recording(source, stage/'extracted')
        evidence = audit.audit(parent/'bundle-btc/strict')
        auditpath = stage/'stream_audit.json'
        auditpath.write_text(json.dumps(evidence, indent=2, allow_nan=False))
        projection.project(parent/'bundle-btc/strict/source_events.jsonl.gz', stage/'spot')
        evidence = normalizer.normalize(parent, stage/'spot', auditpath, stage/'normalized.jsonl.gz')
        (stage/'normalization.json').write_text(json.dumps(evidence, indent=2, allow_nan=False))
        validate_normalized(stage/'normalized.jsonl.gz', evidence)
        # Derived source streams are regenerable; keep small audit files and the
        # reusable normalized tape instead of copying raw private bytes again.
        shutil.copy2(stage/'spot/manifest.json', stage/'spot_projection.json')
        shutil.rmtree(stage/'spot')
        if (stage/'extracted').exists():
            shutil.rmtree(stage/'extracted')
        manifest = {'identity': identity, 'cache_key': key,
                    'normalized_sha256': sha(stage/'normalized.jsonl.gz'),
                    'normalization': evidence, 'formal_oos_result': None,
                    'elapsed_seconds': time.perf_counter()-started}
        (stage/'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False))
        stage.rename(destination)
        return {**manifest, 'cache_hit': False, 'normalized_path': str(destination/'normalized.jsonl.gz')}
    except Exception as exc:
        (stage/'failed.json').write_text(json.dumps({'error': str(exc), 'identity': identity,
            'pnl': None, 'completed_backtests': 0, 'status': 'blocked_raw_intake'}, indent=2))
        raise ValueError(f'raw intake blocked; diagnostics: {stage / "failed.json"}: {exc}') from exc
