"""Offline frozen source-race comparison with separate lossless reason receipts.

No orders, parameter changes, alternative lags, or OOS/rank admission. Register
this worker and its dependencies anew; old freezes and results stay unchanged.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
FREEZE_SHA = '29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63'
INPUTS = ('manifest.json', 'source_events.jsonl.gz', 'clob_events.jsonl.gz',
          'market_registry.csv.gz', 'market_outcomes.csv.gz')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')


def inputs(archive):
    root = archive / 'strict' if (archive / 'strict').is_dir() else archive
    return {name: sha(root / name) for name in INPUTS}


def observed_compare(race, engine, archive, out):
    """Bind the export temporarily; return the original comparison unchanged."""
    from h_reason_accounting import summarize_with_receipt
    receipts = []
    def sink(receipt):
        if len(receipts) >= 2:
            raise ValueError('unexpected extra summary call')
        receipt['arm'] = ('baseline', 'candidate')[len(receipts)]
        receipts.append(receipt)
    original = engine.summarize
    engine.summarize = partial(summarize_with_receipt, original, sink=sink)
    try:
        result = race.compare_archive(archive, rows_dir=out / 'rows')
    finally:
        engine.summarize = original
    if len(receipts) != 2:
        raise ValueError('both independent arm receipts required')
    # A receipt must also bind the exact legacy summary returned by that arm.
    for receipt in receipts:
        arm = result['arms'][receipt['arm']]
        expected = {str(k): v['signals'] for k, v in arm['latencies'].items()}
        observed = {k: v['signals'] for k, v in
                    receipt['reason_accounting']['by_latency'].items()}
        if expected != observed:
            raise ValueError('receipt/comparison arm mismatch')
    return result, receipts


def worker(archive, out):
    registration_path = out / 'registration.json'
    registration = json.loads(registration_path.read_text())
    for name, expected in registration['observation_code_sha256'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('observation code drift: ' + name)
    if inputs(archive) != registration['input_files_sha256']:
        raise ValueError('input drift before replay')
    import h_source_race as race
    import h_replay_run as engine
    freeze = Path(race.__file__).parent / 'forward/source-race-freeze.json'
    if sha(freeze) != FREEZE_SHA:
        raise ValueError('registered freeze drift')
    result, receipts = observed_compare(race, engine, archive, out)
    if inputs(archive) != registration['input_files_sha256']:
        raise ValueError('input drift after replay')
    write(out / 'result.json', result)
    write(out / 'reason_receipts.json', receipts)
    paths = [out / 'result.json', out / 'reason_receipts.json',
             out / 'rows/baseline.jsonl', out / 'rows/candidate.jsonl']
    write(out / 'completion.json', dict(
        schema='source-race-reason-audit-completion-v1',
        registration_sha256=sha(registration_path),
        outputs_sha256={str(p.relative_to(out)): sha(p) for p in paths},
        classification='development_execution_with_lossless_reason_receipts',
        original_frozen_rules_unchanged=True, economic_admission=False,
        rank7_pnl_usd=None, formal_oos_result=None))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    archive, out = args.archive.resolve(), args.out_dir.resolve()
    if args.worker:
        worker(archive, out)
        return 0
    import frozen_dependency_runner as runner
    freeze = ROOT / 'forward/source-race-freeze.json'
    if sha(freeze) != FREEZE_SHA:
        raise ValueError('registered freeze drift')
    frozen, _ = runner._read_freeze(freeze)
    audit, selected = runner._select_dependencies(
        ROOT, ROOT / 'frozen-dependencies/source-race-v1',
        frozen['dependencies_sha256'])
    observation = {name: sha(ROOT / name) for name in
                   (Path(__file__).name, 'h_reason_accounting.py')}
    input_hashes = inputs(archive)
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'registration.json', dict(
        schema='source-race-reason-audit-registration-v1',
        registered_at_utc=datetime.now(timezone.utc).isoformat(),
        source_race_freeze_sha256=FREEZE_SHA, dependencies=audit,
        dependency_runner_sha256=sha(ROOT / 'frozen_dependency_runner.py'),
        observation_code_sha256=observation, input_files_sha256=input_hashes,
        classification='development_only', formal_oos_result=None))
    with tempfile.TemporaryDirectory(prefix='reason-audit-frozen-') as tmp:
        root = Path(tmp)
        runner._materialize(root, freeze, Path('forward/source-race-freeze.json'), selected)
        for name in observation:
            shutil.copyfile(ROOT / name, root / name)
        result = subprocess.run([sys.executable, str(root / Path(__file__).name),
            '--worker', '--archive', str(archive), '--out-dir', str(out)], cwd=root)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
