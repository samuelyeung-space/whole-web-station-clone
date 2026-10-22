"""Development-only strict USD-M recorder bridge; no spot-catalog adoption.

The pinned paper_trader handler requires native e=trade before writing kind.
We retain that proven transformation, not a fabricated native payload. Raw
trade IDs never certify coverage. No subscriptions, orders, labels or PnL.
"""
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from binance_native_flow import exact_uint, positive_decimal
from historical_flow_receipts import ReceiptFlowTape

VERSION = 'strict-usdm-recorder-flow-dev-v1'
CONTRACT = 'paper-trader-7bf9cf2a-usdm-raw-trade-v1'
RECORDER_COMMIT = '7bf9cf2a028beeb5af789fc36b6bdbbc069b7d31'
RECORDER_BLOB_SHA = '7b8b746c5a040ab6a97c2b4a68ca03680be624fc'
EXCHANGE = 'binance_usdm'
INSTRUMENT = 'BTCUSDT'


def _clock(value):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def recorder_receipt(raw, *, capture_run_id, recorder_contract, time_unit, split):
    if split != 'development' or recorder_contract != CONTRACT:
        raise ValueError('explicit pinned recorder contract and development split required')
    if time_unit != 'MILLISECOND':
        raise ValueError('explicit MILLISECOND unit required')
    if not isinstance(raw, Mapping) or raw.get('kind') != 'trade' or raw.get('s') != INSTRUMENT:
        raise ValueError('strict BTCUSDT trade recorder row required')
    if 'e' in raw or any(k in raw for k in ('data', 'stream', 'a', 'f', 'l')):
        raise ValueError('native/aggregate schema must not enter this recorder-only route')
    if not isinstance(capture_run_id, str) or not capture_run_id.strip():
        raise ValueError('recording epoch identity required')
    recv = raw.get('recv_ms')
    if not _clock(recv):
        raise ValueError('finite recorder receive clock required')
    trade_id = exact_uint(raw.get('t'), 't')
    epoch = exact_uint(raw.get('connection_epoch'), 'connection_epoch')
    if epoch < 1:
        raise ValueError('positive recorder connection epoch required')
    trade_ms = exact_uint(raw.get('T'), 'T')
    event_ms = exact_uint(raw.get('E'), 'E')
    if max(trade_ms, event_ms) > recv + 10000:
        raise ValueError('source clock over 10 seconds ahead of receive clock')
    price = positive_decimal(raw.get('p'), 'p')
    quantity = positive_decimal(raw.get('q'), 'q')
    if not math.isfinite(price * quantity):
        raise ValueError('quote volume overflow')
    maker = raw.get('m')
    if maker is not None and type(maker) is not bool:
        raise ValueError('m must be original boolean or missing')
    side = ('sell' if maker else 'buy') if maker is not None else None
    return {'version': VERSION, 'recorder_contract': CONTRACT,
            'row': {'exchange': EXCHANGE, 'instrument': INSTRUMENT,
                    'trade_id': 'trade:' + str(trade_id), '_run_id': capture_run_id,
                    'trade_ts_ms': trade_ms, 'recv_ts_ms': recv, 'price': price,
                    'size': quantity, 'taker_side': side},
            'raw_recorder_row': deepcopy(dict(raw)), 'source_event_ms': event_ms,
            'recorder_connection_epoch': epoch, 'recorder_blob_sha': RECORDER_BLOB_SHA,
            'native_event_field_retained': False,
            'event_kind_provenance': 'pinned handler requires native e=trade before recording kind=trade',
            'price_decimal': str(Decimal(str(raw['p']))),
            'quantity_decimal': str(Decimal(str(raw['q']))),
            'side_provenance': 'retained_m_buyer_is_maker' if maker is not None else 'missing_m',
            'coverage_inferred_from_prints_or_ids': False}


class RecorderFuturesFlowTape:
    """Reuse the flow engine in a separate venue namespace; latch corruption.

    Not a NativeFlowTape subtype: the existing spot-catalog join must reject
    it. Futures adoption needs an explicit venue-specific rule registration.
    """
    def __init__(self, *, capture_run_id, recorder_contract, time_unit, split):
        if split != 'development' or recorder_contract != CONTRACT or time_unit != 'MILLISECOND':
            raise ValueError('explicit development recorder contract required')
        if not isinstance(capture_run_id, str) or not capture_run_id.strip():
            raise ValueError('recording epoch identity required')
        self.arguments = dict(capture_run_id=capture_run_id, recorder_contract=recorder_contract,
                              time_unit=time_unit, split=split)
        self.engine = ReceiptFlowTape(exchange=EXCHANGE, instrument=INSTRUMENT)
        self.signatures = {}
        self.failure = None
        self.failed_at = None
        self.retained = []
        self.epoch = None

    def add_trade(self, raw):
        if self.failure is not None:
            raise ValueError('tape failed closed; no continuation or silent drop')
        recv = raw.get('recv_ms') if isinstance(raw, Mapping) else None
        try:
            receipt = recorder_receipt(raw, **self.arguments)
            if self.epoch is not None and self.epoch != receipt['recorder_connection_epoch']:
                raise ValueError('recorder connection epoch changed; start a new tape')
            row = receipt['row']; tid = row['trade_id']
            # Exact decimals precede engine float conversion. E is metadata;
            # a repeated ID may retain its earlier economic identity.
            sig = (row['trade_ts_ms'], Decimal(receipt['price_decimal']),
                   Decimal(receipt['quantity_decimal']), row['taker_side'])
            if tid in self.signatures and sig != self.signatures[tid]:
                raise ValueError('conflicting exact economic identity')
            self.engine.add_trade(row)
        except (ValueError, TypeError, OverflowError) as exc:
            self.failure = str(exc)
            self.failed_at = recv if _clock(recv) and recv >= self.engine._last_recv else -math.inf
            raise
        self.signatures[tid] = sig
        self.epoch = receipt['recorder_connection_epoch']
        self.retained.append(receipt)
        return receipt

    def add_coverage(self, interval):
        self.engine.add_coverage(interval)

    def features(self, decision_recv_ms):
        result = self.engine.features(decision_recv_ms)
        result.update(adapter_version=VERSION, recorder_contract=CONTRACT,
                      venue=EXCHANGE, spot_catalog_compatible=False,
                      capture_clock='strict recorder recv_ms; source clocks are metadata',
                      formal_oos_result=None)
        if self.failure is not None and decision_recv_ms >= self.failed_at:
            for k in result['features']:
                result['features'][k] = None
                result['feature_available_ms'][k] = None
                result['audit'][k].update(value=None, available_ms=None,
                                          status='blocked_recorder_ingestion_error')
            result['ingestion_error'] = self.failure
        return result


def audit_file(path, *, expected_sha256, capture_run_id):
    """Audit every raw row, but never continue a feature tape after corruption.

    Row parseability after the first rejection is diagnostic only. All source
    rows remain in the immutable original gzip; no filtered healthy tape is
    emitted, and no coverage interval is manufactured from lifecycle records.
    """
    source = Path(path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError('input SHA256 mismatch before parsing')
    args = dict(capture_run_id=capture_run_id, recorder_contract=CONTRACT,
                time_unit='MILLISECOND', split='development')
    tape = RecorderFuturesFlowTape(**args)
    counts = Counter(); reasons = Counter(); first_error = None
    accepted_digest = hashlib.sha256(); previous = None
    with gzip.open(source, 'rt') as stream:
        for line_number, line in enumerate(stream, 1):
            raw = json.loads(line); counts['rows'] += 1
            if raw.get('kind') != 'trade' or raw.get('s') != INSTRUMENT:
                counts['other_rows_retained_in_source'] += 1
                continue
            counts['btc_trade_rows'] += 1
            try:
                parsed = recorder_receipt(raw, **args)
            except (ValueError, TypeError, OverflowError) as exc:
                counts['rejected_rows'] += 1; reasons[str(exc)] += 1
                if first_error is None:
                    # Exercise fail-closed state on the actual rejected row.
                    try: tape.add_trade(raw)
                    except (ValueError, TypeError, OverflowError): pass
                    first_error = dict(line_number=line_number, raw_row=raw, error=str(exc))
                continue
            counts['parseable_rows'] += 1
            accepted_digest.update(json.dumps(parsed, sort_keys=True, allow_nan=False).encode() + b'\n')
            if first_error is None:
                tape.add_trade(raw); previous = raw['recv_ms']
                counts['pre_failure_tape_rows'] += 1
    before = tape.features(previous) if previous is not None else None
    after = tape.features(first_error['raw_row']['recv_ms']) if first_error else before
    assert counts['rows'] == counts['other_rows_retained_in_source'] + counts['btc_trade_rows']
    assert counts['btc_trade_rows'] == counts['parseable_rows'] + counts['rejected_rows']
    return dict(version=VERSION, recorder_contract=CONTRACT, source_sha256=digest,
                capture_run_id=capture_run_id, counts=dict(counts), rejection_reasons=dict(reasons),
                parseable_receipts_canonical_sha256=accepted_digest.hexdigest(),
                first_rejection=first_error, last_pre_failure_features=before,
                first_failure_features=after, independently_proven_coverage_intervals=0,
                post_rejection_rows_used_for_features=0, spot_catalog_compatible=False,
                strategies_evaluated=0, actual_backtests_performed=0, pnl_usd=None,
                rank7_pnl_usd=None, formal_oos_result=None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('--sha256', required=True)
    p.add_argument('--capture-run-id', required=True); p.add_argument('--output', required=True)
    a = p.parse_args()
    result = audit_file(a.input, expected_sha256=a.sha256, capture_run_id=a.capture_run_id)
    Path(a.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'counts': result['counts'], 'pnl_usd': None, 'output': a.output}))


if __name__ == '__main__':
    main()
