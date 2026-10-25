"""Hash-bound, development-only file entry point for the existing flow bridge.

Hashes establish byte identity, never truthful coverage or an untouched split.
Data files are read once into snapshots before parsing; Python imports are not
sealed, so recorded module hashes are not proof of immutable code execution.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import usdm_flow_execution_bridge as bridge
from recorder_futures_flow import RecorderFuturesFlowTape, CONTRACT, EXCHANGE, INSTRUMENT
from historical_flow_receipts import CoverageInterval

VERSION = 'usdm-flow-bundle-replay-dev-v1'
FILES = {'events', 'trades', 'coverage', 'bindings'}
MODULES = ('usdm_flow_execution_bridge', 'usdm_flow_candidates', 'recorder_futures_flow',
           'historical_flow_receipts', 'binance_native_flow', 'mine500_catalog',
           'mine500_replay_v2', 'mine500_constraints')
MAX_BYTES = 256 * 1024 * 1024


def digest(data): return hashlib.sha256(data).hexdigest()


def unique_pairs(pairs):
    out = {}
    for k,v in pairs:
        if k in out: raise ValueError('duplicate JSON key: ' + k)
        out[k] = v
    return out


def loads(data):
    def reject(x): raise ValueError('nonfinite JSON scalar: ' + x)
    return json.loads(data, object_pairs_hook=unique_pairs, parse_constant=reject)


def rows(data):
    result = [loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    if any(not isinstance(x, dict) for x in result): raise ValueError('JSONL object rows required')
    return result


def replay_bundle(manifest_path, *, expected_manifest_sha256):
    path = Path(manifest_path).resolve()
    manifest_bytes = path.read_bytes()
    if digest(manifest_bytes) != expected_manifest_sha256: raise ValueError('manifest SHA256 mismatch')
    m = loads(manifest_bytes)
    if m.get('schema') != VERSION or m.get('split') != 'development':
        raise ValueError('explicit development bundle required')
    evidence = m.get('input_evidence_class')
    if evidence not in ('synthetic_fixture', 'consumed_development'):
        raise ValueError('only synthetic or previously consumed development evidence allowed')
    if not isinstance(m.get('consumption_provenance'), str) or not m['consumption_provenance'].strip():
        raise ValueError('consumption provenance required; still caller attested')
    run = m['capture_run_id']; clock = m['capture_clock_id']
    if not isinstance(run, str) or not run.strip() or clock != run + ':recv_ms':
        raise ValueError('capture clock/run mismatch')
    if m.get('recorder_contract') != CONTRACT or m.get('time_unit') != 'MILLISECOND':
        raise ValueError('pinned recorder contract and clock unit required')
    if set(m['files']) != FILES: raise ValueError('all four input files required')
    snapshots = {}; identities = {}; paths = set()
    # Hash every file before any input row is semantically parsed.
    for role in sorted(FILES):
        spec = m['files'][role]; target = (path.parent / spec['path']).resolve()
        if not target.is_relative_to(path.parent) or target in paths:
            raise ValueError('unique bundle-relative file paths required')
        paths.add(target)
        with target.open('rb') as f: data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: raise ValueError('input exceeds snapshot size limit')
        if len(data) != spec['bytes'] or digest(data) != spec['sha256']:
            raise ValueError(role + ' size/SHA256 mismatch')
        snapshots[role] = data
        identities[role] = dict(path=spec['path'], bytes=len(data), sha256=digest(data))
    events = rows(snapshots['events'])
    if not events or events[0].get('capture_clock_id') != clock:
        raise ValueError('dataset capture clock does not match native tape')
    tape = RecorderFuturesFlowTape(capture_run_id=run, recorder_contract=CONTRACT,
                                   time_unit='MILLISECOND', split='development')
    payload = snapshots['trades']
    if m.get('trades_encoding') == 'gzip-jsonl':
        # Bound decompression separately from the compressed-file size.
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as f: payload = f.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES: raise ValueError('decompressed tape exceeds size limit')
    elif m.get('trades_encoding') != 'jsonl': raise ValueError('explicit tape encoding required')
    retained_other = 0; native_rows = 0
    for raw in rows(payload):
        if raw.get('kind') != 'trade' or raw.get('s') != INSTRUMENT:
            retained_other += 1
            continue
        tape.add_trade(raw)  # Abort on any corrupt row/epoch/ID, never filter/repair.
        native_rows += 1
    if not native_rows: raise ValueError('no native BTCUSDT trade rows')
    coverage = loads(snapshots['coverage']); binding_rows = loads(snapshots['bindings'])
    if not isinstance(coverage, list) or not isinstance(binding_rows, list):
        raise ValueError('coverage/bindings arrays required')
    for raw in coverage:
        c = CoverageInterval(**raw)
        if (c.exchange,c.instrument,c.capture_run_id) != (EXCHANGE,INSTRUMENT,run):
            raise ValueError('coverage venue/instrument/run mismatch')
        tape.add_coverage(c)
    bindings = []
    for raw in binding_rows:
        if set(raw) != {'market_id','decision_recv_ms','capture_clock_id','tau_evidence'}:
            raise ValueError('binding schema mismatch; no arbitrary tape override')
        if raw['capture_clock_id'] != clock: raise ValueError('binding capture clock mismatch')
        bindings.append(dict(raw,tape=tape))
    result = bridge.replay_development(events, bindings, split='development', input_evidence_class=evidence)
    result['bundle_audit'] = dict(version=VERSION, manifest_sha256=expected_manifest_sha256,
        input_identities=identities, input_bytes_snapshotted_before_parsing=True,
        native_trade_rows=native_rows, other_rows_retained_in_original=retained_other,
        coverage_intervals_supplied=len(coverage), consumption_provenance=m['consumption_provenance'],
        consumption_and_coverage_independently_verified=False,
        execution_sealed=False, module_file_sha256={name:digest(Path(sys.modules[name].__file__).read_bytes()) for name in MODULES})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest'); p.add_argument('--sha256', required=True); p.add_argument('--output', required=True)
    args = p.parse_args()
    # No output is written if admission, ingestion or replay raises.
    result = replay_bundle(args.manifest, expected_manifest_sha256=args.sha256)
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(output=args.output,evidence_class=result['evidence_class'],
                         rank7_pnl_usd=None,formal_oos_result=None)))

if __name__ == '__main__': main()
