"""Offline negative-only flow integrity evidence. Never certifies coverage.

Contiguous recorder sequence and trade IDs do not establish economically
complete volume. A zero/nonfinite print blocks a trailing window only once
received; later events cannot filter earlier decisions. No rule or PnL replay.
"""
from bisect import bisect_right
from collections import Counter
from decimal import Decimal, InvalidOperation
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from binance_native_flow import exact_uint

VERSION = 'futures-recording-negative-integrity-v1'


def finite_clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('finite nonnegative local millisecond clock required')
    return value


class ObservedBadPrints:
    """Point witnesses, not assumed healthy intervals or source clocks."""
    def __init__(self):
        self.times = []

    def add(self, recv_ms):
        finite_clock(recv_ms)
        if self.times and recv_ms < self.times[-1]:
            raise ValueError('bad-print receipt order moved backwards')
        self.times.append(recv_ms)

    def at(self, decision_recv_ms, horizon_ms):
        finite_clock(decision_recv_ms); finite_clock(horizon_ms)
        if horizon_ms <= 0:
            raise ValueError('positive trailing horizon required')
        left = bisect_right(self.times, decision_recv_ms - horizon_ms)
        right = bisect_right(self.times, decision_recv_ms)
        count = right-left
        return dict(observed_bad_prints=count,
                    status='blocked_observed_bad_print' if count else 'not_excluded_by_this_check',
                    coverage_certified=False, flow_feature_available=False)

    def excluded_duration_ms(self, start, end, horizon_ms):
        """Measure union of [receipt, receipt+horizon) inside fixed bounds.

        This retrospective summary does not move receipt-time availability.
        Unexcluded duration remains UNKNOWN, never healthy or complete.
        """
        finite_clock(start); finite_clock(end); finite_clock(horizon_ms)
        if end <= start or horizon_ms <= 0:
            raise ValueError('positive interval and horizon required')
        total = 0.; current_start = current_end = None
        for t in self.times:
            lo, hi = max(start, t), min(end, t+horizon_ms)
            if hi <= lo:
                continue
            if current_end is None:
                current_start, current_end = lo, hi
            elif lo <= current_end:
                current_end = max(current_end, hi)
            else:
                total += current_end-current_start
                current_start, current_end = lo, hi
        return total + (current_end-current_start if current_end is not None else 0)


def audit(path, expected_sha256):
    source = Path(path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError('input hash mismatch before gzip parsing')
    c = Counter(); kinds = Counter(); bad_sides = Counter(); witnesses = []
    seq_prev = recv_prev = id_prev = None
    first_recv = last_recv = first_id = last_id = None
    taint = ObservedBadPrints()
    with gzip.open(source, 'rt') as f:
        for line_number, line in enumerate(f, 1):
            r = json.loads(line); c['rows'] += 1; kinds[r['kind']] += 1
            seq = exact_uint(r.get('sequence'), 'sequence')
            recv = finite_clock(r.get('recv_ms'))
            if seq_prev is not None and seq != seq_prev+1:
                c['recorder_sequence_breaks'] += 1
            if recv_prev is not None and recv < recv_prev:
                raise ValueError('raw local receipt clock moved backwards; do not sort')
            if first_recv is None: first_recv = recv
            last_recv = recv; seq_prev = seq; recv_prev = recv
            if r.get('kind') != 'trade' or r.get('s') != 'BTCUSDT':
                continue
            c['btc_trade_rows'] += 1
            tid = exact_uint(r.get('t'), 't')
            if first_id is None: first_id = tid
            if id_prev is not None and tid != id_prev+1:
                c['trade_id_nonunit_steps'] += 1
            last_id = tid; id_prev = tid
            try:
                price, qty = Decimal(str(r.get('p'))), Decimal(str(r.get('q')))
                invalid = not price.is_finite() or not qty.is_finite() or price <= 0 or qty <= 0
            except InvalidOperation:
                invalid = True
            if invalid:
                c['observed_bad_prints'] += 1; taint.add(recv)
                bad_sides[str(r.get('m'))] += 1
                if r.get('p') == '0' and r.get('q') == '0': c['both_literal_zero'] += 1
                if len(witnesses) < 5: witnesses.append(dict(line=line_number, raw_row=r))
    if first_recv is None or last_recv <= first_recv:
        raise ValueError('positive-duration captured recorder interval required')
    summaries = {}
    start_grid, end_grid = math.ceil(first_recv/1000)*1000, math.floor(last_recv/1000)*1000
    for horizon in (15000, 60000):
        total = excluded = 0
        for decision in range(start_grid, end_grid+1, 1000):
            total += 1; excluded += taint.at(decision, horizon)['observed_bad_prints'] > 0
        duration = taint.excluded_duration_ms(first_recv, last_recv, horizon)
        summaries[str(horizon)] = dict(horizon_ms=horizon,
            grid_origin='aligned UTC seconds inside first/last recorder receipt',
            grid_decisions=total, decisions_excluded_by_observed_bad_print=excluded,
            decisions_not_excluded_but_still_unknown=total-excluded,
            excluded_duration_ms=duration, observed_capture_duration_ms=last_recv-first_recv,
            excluded_time_fraction=duration/(last_recv-first_recv),
            coverage_certified_decisions=0, flow_features_admitted=0)
    return dict(version=VERSION, source_sha256=digest, counts=dict(c), kinds=dict(kinds),
        recorder_sequence_breaks=c['recorder_sequence_breaks'],
        trade_id_nonunit_steps=c['trade_id_nonunit_steps'],
        first_trade_id=first_id, last_trade_id=last_id,
        first_recorder_recv_ms=first_recv, last_recorder_recv_ms=last_recv,
        bad_native_m_counts=dict(bad_sides), first_bad_print_witnesses=witnesses,
        bad_print_recv_ms=taint.times, horizons=summaries,
        native_id_contiguity_proves_volume_completeness=False,
        lifecycle_or_sequence_proves_source_completeness=False,
        bad_print_true_notional_bound=None, zero_row_semantics_resolved=False,
        strategies_evaluated=0, orders=0, fills=0, pnl_usd=None,
        rank7_pnl_usd=None, formal_oos_result=None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('--sha256', required=True)
    p.add_argument('--output', required=True); a = p.parse_args()
    result = audit(a.input, a.sha256)
    Path(a.output).write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ['counts','recorder_sequence_breaks','trade_id_nonunit_steps','horizons','pnl_usd']}))


if __name__ == '__main__': main()
