"""Precompute native constraints while immutable spot normalization is running.

Application requires exact market-map equality with the completed normalized
tape and exact native member hashes from the retained original archive. No
engine or normalizer changes; no inferred or forward-filled execution fields.
"""
from __future__ import annotations
import copy
import heapq
import itertools
import json
from pathlib import Path
import shutil
import tempfile

import h_replay_archive as archive_reader
import replay_constraint_overlay as base

VERSION = 'receipt-constraint-prepass-v1'


def native_markets(parent):
    parent = Path(parent)
    first = {}
    for path in sorted((parent / 'raw').glob('*/markets.jsonl.gz')):
        for row in base.extractor.read_jsonl(path):
            if not row.get('slug', '').startswith('btc-updown-5m-'):
                continue
            now = row.get('_recorded_at_ms')
            if not base.extractor._number(now):
                raise ValueError('raw Gamma receipt missing')
            mid = row['conditionId']
            if mid not in first or now < first[mid]['_recorded_at_ms']:
                first[mid] = row
    events = []
    for mapping in archive_reader.iter_market_mappings(parent / 'bundle-btc/strict/market_registry.csv.gz'):
        row = first[mapping['market_id']]
        outcomes, ids = row['outcomes'], row['clobTokenIds']
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        ids = json.loads(ids) if isinstance(ids, str) else ids
        if any(str(ids[outcomes.index(side.capitalize())]) != str(mapping[side + '_token_id']) for side in ('up', 'down')):
            raise ValueError('Gamma/registry direct token identity mismatch')
        start = int(row['slug'].rsplit('-', 1)[1]) * 1000
        if start != mapping['slot'] * 1000:
            raise ValueError('Gamma/registry slot mismatch')
        events.append({'type': 'market', 'market_id': mapping['market_id'],
            'recv_ms': row['_recorded_at_ms'], 'start_ms': start, 'end_ms': start + 300000,
            'up_token_id': mapping['up_token_id'], 'down_token_id': mapping['down_token_id']})
    return base.extractor.market_maps(events)


def precompute(parent, archive, out_root):
    parent, archive, out_root = map(lambda value: Path(value).resolve(), (parent, archive, out_root))
    if base.sha(base.extractor.__file__) != base.EXTRACTOR_SHA256:
        raise ValueError('registered extractor changed')
    names = ['bundle-btc/strict/manifest.json', 'bundle-btc/strict/clob_events.jsonl.gz']
    names += sorted(p.relative_to(parent).as_posix() for p in (parent / 'raw').glob('*/markets.jsonl.gz'))
    names += sorted(p.relative_to(parent).as_posix() for p in (parent / 'raw').glob('*/clob.jsonl.gz'))
    members = {name: base.sha(parent / name) for name in names}
    markets = native_markets(parent)
    native = json.loads((parent / 'bundle-btc/strict/manifest.json').read_text())
    identity = {'schema': VERSION, 'code_sha256': base.sha(__file__),
        'overlay_code_sha256': base.sha(base.__file__), 'extractor_sha256': base.EXTRACTOR_SHA256,
        'archive_sha256': base.sha(archive), 'source_members_sha256': members,
        'registry_sha256': base.sha(parent / 'bundle-btc/strict/market_registry.csv.gz'),
        'market_maps_sha256': __import__('hashlib').sha256(base.encode(markets)).hexdigest()}
    key = __import__('hashlib').sha256(base.encode(identity)).hexdigest()
    out_root.mkdir(parents=True, exist_ok=True)
    dest = out_root / key
    if dest.exists():
        result = json.loads((dest / 'manifest.json').read_text())
        if result['identity'] != identity or base.sha(dest / 'constraints.jsonl') != result['constraint_events_sha256']:
            raise ValueError('prepass cache corrupted')
        return {**result, 'prepass_path': str(dest), 'cache_hit': True}
    gamma = itertools.chain.from_iterable(base.extractor.read_jsonl(parent / name) for name in names if name.endswith('/markets.jsonl.gz'))
    raw_names = [name for name in names if name.startswith('raw/') and name.endswith('/clob.jsonl.gz')]
    raw = heapq.merge(*(base.extractor.read_jsonl(parent / name) for name in raw_names), key=lambda row: row['recv_ms']) if raw_names else None
    constraints, audit = base.extractor.extract_constraints(markets, gamma,
        base.extractor.read_jsonl(parent / 'bundle-btc/strict/clob_events.jsonl.gz'), raw)
    if not constraints:
        raise ValueError('no native constraints extracted')
    if any(base.sha(parent / name) != digest for name, digest in members.items()):
        raise ValueError('prepass source changed')
    stage = Path(tempfile.mkdtemp(prefix=key + '.pending.', dir=out_root))
    (stage / 'constraints.jsonl').write_bytes(b''.join(base.encode(row) for row in constraints))
    result = {'identity': identity, 'run_id': str(native['run_id']), 'market_maps': markets,
        'constraint_extraction_audit': audit, 'constraint_events_sha256': base.sha(stage / 'constraints.jsonl'),
        'status': 'native_constraint_prepass_only_not_backtest', 'new_backtests': 0, 'formal_oos_result': None}
    (stage / 'manifest.json').write_bytes(base.encode(result))
    stage.rename(dest)
    return {**result, 'prepass_path': str(dest), 'cache_hit': False}


def correct(normalized, archive, prepass_path, out_root):
    normalized, archive, prepass_path, out_root = map(lambda value: Path(value).resolve(), (normalized, archive, prepass_path, out_root))
    if '.pending.' in str(normalized):
        raise ValueError('refuse in-progress source tape')
    before = base.inspect(normalized)
    if before['header'].get('split') != 'development' or before['header'].get('constraint_overlay'):
        raise ValueError('requires original development normalized tape')
    producer_path = normalized.parent / 'manifest.json'
    producer = json.loads(producer_path.read_text())
    source_hash, archive_hash = base.sha(normalized), base.sha(archive)
    if producer.get('normalized_sha256', producer.get('normalization', {}).get('sha256')) != source_hash:
        raise ValueError('completed producer tape hash mismatch')
    lineage = producer.get('source_identity', producer.get('identity', {}))
    expected_archive = lineage.get('inputs', {}).get('archive_sha256')
    expected_clob = lineage.get('audit_inputs', {}).get('clob_events.jsonl.gz')
    if expected_archive is None and expected_clob is None:
        raise ValueError('completed producer lacks archive/audited CLOB identity')
    if expected_archive is not None and expected_archive != archive_hash:
        raise ValueError('archive differs from completed producer identity')
    prepass = json.loads((prepass_path / 'manifest.json').read_text())
    pid = prepass['identity']
    if (pid['archive_sha256'] != archive_hash or pid['code_sha256'] != base.sha(__file__)
            or pid['overlay_code_sha256'] != base.sha(base.__file__)
            or pid['extractor_sha256'] != base.sha(base.extractor.__file__)
            or pid['extractor_sha256'] != base.EXTRACTOR_SHA256):
        raise ValueError('prepass archive/code identity mismatch')
    if prepass['market_maps'] != before['markets']:
        raise ValueError('prepass market maps differ from completed normalized tape')
    constraints_path = prepass_path / 'constraints.jsonl'
    if base.sha(constraints_path) != prepass['constraint_events_sha256']:
        raise ValueError('prepass constraints corrupted')
    identity = {'schema': VERSION + '-applied', 'source_normalized_sha256': source_hash,
        'archive_sha256': archive_hash, 'wrapper_sha256': base.sha(__file__),
        'overlay_sha256': base.sha(base.__file__), 'extractor_sha256': base.EXTRACTOR_SHA256,
        'producer_manifest_sha256': base.sha(producer_path), 'prepass_manifest_sha256': base.sha(prepass_path / 'manifest.json')}
    key = __import__('hashlib').sha256(base.encode(identity)).hexdigest()
    out_root.mkdir(parents=True, exist_ok=True)
    dest = out_root / key
    if dest.exists():
        result = json.loads((dest / 'manifest.json').read_text())
        if result['identity'] != identity or base.sha(dest / 'normalized.jsonl.gz') != result['normalized_sha256']:
            raise ValueError('completed applied overlay corrupted')
        check = base.inspect(dest / 'normalized.jsonl.gz')
        if check['counts'] != result['normalization']['counts'] or check['nonconstraint_sha256'] != before['nonconstraint_sha256']:
            raise ValueError('applied overlay body mismatch')
        return {**result, 'normalized_path': str(dest / 'normalized.jsonl.gz'), 'cache_hit': True}
    stage = Path(tempfile.mkdtemp(prefix=key + '.pending.', dir=out_root))
    try:
        # This scans the original compressed archive, but never re-decodes its
        # multi-million-row CLOB. It proves prepass membership independently.
        selected = base.unpack_constraint_sources(archive, stage / 'sources')
        members = {name: base.sha(path) for name, path in selected.items()}
        if members != pid['source_members_sha256']:
            raise ValueError('prepass native sources differ from original archive')
        if expected_clob is not None and members['bundle-btc/strict/clob_events.jsonl.gz'] != expected_clob:
            raise ValueError('native CLOB differs from completed producer audit')
        provenance = before['header'].get('provenance', {})
        if members['bundle-btc/strict/manifest.json'] != provenance.get('parent_manifest_sha256'):
            raise ValueError('archive manifest differs from normalized parent')
        gamma = {name: digest for name, digest in members.items() if name.endswith('/markets.jsonl.gz')}
        if not gamma or gamma != provenance.get('raw_metadata_sha256'):
            raise ValueError('Gamma receipts differ from completed normalized input')
        native = json.loads(selected['bundle-btc/strict/manifest.json'].read_text())
        run_id = str(native['run_id'])
        if run_id != prepass['run_id'] or before['header']['dataset_id'] != 'historical-spot-development:' + run_id:
            raise ValueError('recording identity mismatch')
        constraints = list(base.extractor.read_jsonl(constraints_path))
        if not constraints or constraints[0]['recv_ms'] < before['header']['recv_ms']:
            raise ValueError('invalid prepass constraint timing')
        audit = prepass['constraint_extraction_audit']
        header = copy.deepcopy(before['header'])
        header['constraint_overlay'] = {'schema': VERSION + '-applied', 'identity': identity,
            'run_id': run_id, 'source_members_sha256': members,
            'nonconstraint_body_unchanged_sha256': before['nonconstraint_sha256'],
            'scope': 'development schema/code correction; no frozen OOS inheritance',
            'prepass_market_maps_match_exactly': True}
        header.setdefault('limitations', []).extend([audit['tick_coverage_limitation'], audit['same_time_merge_assumption']])
        shutil.copyfile(constraints_path, stage / 'constraints.jsonl')
        output = stage / 'normalized.jsonl.gz'
        written = base.write_overlay(normalized, output, header, constraints)
        check = base.inspect(output)
        if written['nonconstraint_sha256'] != before['nonconstraint_sha256'] or check['nonconstraint_sha256'] != before['nonconstraint_sha256']:
            raise ValueError('non-constraint events changed')
        if check['counts'] != written['counts'] or base.sha(normalized) != source_hash or base.sha(archive) != archive_hash or base.sha(producer_path) != identity['producer_manifest_sha256']:
            raise ValueError('source/output integrity changed')
        result = {'identity': identity, 'status': 'complete_constraint_corrected_development', 'run_id': run_id,
            'normalization': {'sha256': base.sha(output), 'counts': check['counts'], 'header': header},
            'normalized_sha256': base.sha(output), 'original_counts': before['counts'],
            'copy_verification': written, 'constraint_extraction_audit': audit,
            'constraint_events_sha256': base.sha(stage / 'constraints.jsonl'), 'new_backtests': 0,
            'formal_oos_result': None, 'source_normalized': str(normalized), 'source_archive': str(archive)}
        shutil.rmtree(stage / 'sources')
        (stage / 'manifest.json').write_bytes(base.encode(result))
        stage.rename(dest)
        return {**result, 'normalized_path': str(dest / 'normalized.jsonl.gz'), 'cache_hit': False}
    except BaseException as error:
        (stage / 'failed.json').write_bytes(base.encode({'status': 'failed', 'error': str(error), 'identity': identity}))
        shutil.rmtree(stage / 'sources', ignore_errors=True)
        raise
