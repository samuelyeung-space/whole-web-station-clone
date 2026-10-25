"""Development-only diagnostics around the existing fail-closed recorder tape.

No repaired/filtered tape, coverage, signals, orders, labels or PnL is emitted.
Rows after the first failure are inspected only as standalone diagnostics.
"""
import argparse
from collections import Counter
from collections.abc import Mapping
import gzip
import hashlib
import json
from pathlib import Path
from recorder_futures_flow import CONTRACT, INSTRUMENT, RecorderFuturesFlowTape, recorder_receipt

VERSION = 'recorder-flow-file-audit-v1'


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()


def audit_file(path, *, expected_sha256, capture_run_id, split):
    if split != 'development':
        raise ValueError('explicit development input required; no OOS adoption')
    actual = digest_file(path)
    if actual != expected_sha256:
        raise ValueError('input SHA256 mismatch before parsing')
    args = dict(capture_run_id=capture_run_id, recorder_contract=CONTRACT,
                time_unit='MILLISECOND', split=split)
    tape = RecorderFuturesFlowTape(**args)
    counts = Counter(); reasons = Counter(); first = None

    def reject(stage, exc, line_number, line):
        nonlocal first
        counts['rejected_rows'] += 1
        reasons[stage + ': ' + str(exc)] += 1
        if first is None:
            first = dict(line_number=line_number, stage=stage, error=str(exc),
                         raw_line_sha256=hashlib.sha256(line.encode()).hexdigest())

    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            counts['rows'] += 1
            try:
                raw = json.loads(line)
            except ValueError as exc:
                reject('json', exc, line_number, line); continue
            if not isinstance(raw, Mapping):
                reject('shape', 'recorder row is not an object', line_number, line); continue
            if raw.get('kind') != 'trade' or raw.get('s') != INSTRUMENT:
                counts['other_rows_retained_in_source'] += 1; continue
            counts['btc_trade_rows'] += 1
            try:
                recorder_receipt(raw, **args)
            except (ValueError, TypeError, OverflowError) as exc:
                reject('receipt', exc, line_number, line); continue
            counts['standalone_parseable_btc_rows'] += 1
            if first is not None:
                counts['standalone_parseable_btc_rows_after_failure_diagnostic_only'] += 1
                continue
            try:
                tape.add_trade(raw)
            except (ValueError, TypeError, OverflowError) as exc:
                reject('tape', exc, line_number, line); continue
            counts['tape_add_trade_calls_before_failure'] += 1
    assert counts['rows'] == (counts['other_rows_retained_in_source'] +
        counts['tape_add_trade_calls_before_failure'] + counts['rejected_rows'] +
        counts['standalone_parseable_btc_rows_after_failure_diagnostic_only'])
    return dict(version=VERSION, source_sha256=actual, capture_run_id=capture_run_id,
                split=split, recorder_contract=CONTRACT, counts=dict(counts),
                first_failure=first, rejection_reasons=dict(reasons),
                status='blocked_input_error' if first else 'no_detected_error_coverage_unverified',
                features_emitted=False, repaired_tape_emitted=False,
                post_failure_rows_used_for_features=0, coverage_certificates_produced=0,
                signals_evaluated=0, actual_backtests_performed=0, pnl_usd=None,
                rank7_pnl_usd=None, formal_oos_result=None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('--sha256', required=True)
    p.add_argument('--capture-run-id', required=True)
    p.add_argument('--development', action='store_true', required=True)
    p.add_argument('--output', required=True); a = p.parse_args()
    result = audit_file(a.input, expected_sha256=a.sha256,
                        capture_run_id=a.capture_run_id, split='development')
    Path(a.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'counts': result['counts'],
                      'pnl_usd': None, 'output': a.output}))


if __name__ == '__main__': main()
