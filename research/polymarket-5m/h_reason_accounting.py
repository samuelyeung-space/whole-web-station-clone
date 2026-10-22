"""Lossless report layer for Current-H rows and pre-compaction counters.

This module never changes frozen replay, its financial statistics or admission.
If only a compacted result remains, missing source buckets are unrecoverable.
"""
from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict


def audit_reason_buckets(rows, counters, summary):
    rows = list(rows)
    buckets = counters.get('_suppressed_unsent', [])
    by_delay = defaultdict(Counter)
    sources = defaultdict(lambda: defaultdict(Counter))
    seen = set()
    for item in buckets:
        delay = float(item['evaluation_ms'])
        reason, source, count = item['reason'], item['source'], item['count']
        if (not math.isfinite(delay) or delay < 0 or not isinstance(reason, str)
                or not isinstance(source, str) or type(count) is not int or count < 0):
            raise ValueError('invalid suppressed reason bucket')
        key = (delay, reason, source)
        if key in seen:
            raise ValueError('duplicate suppressed reason bucket')
        seen.add(key)
        by_delay[delay][reason] += count
        sources[delay][source][reason] += count
    for row in rows:
        delay = float(row['evaluation_ms'])
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('invalid row evaluation_ms')
        reason = str(row['reason'])
        source = str(row.get('signal_source') or 'unknown')
        by_delay[delay][reason] += 1
        sources[delay][source][reason] += 1
    output = copy.deepcopy(summary)
    result = {}
    expected_delays = {float(x) for x in output['latencies']}
    if set(by_delay) - expected_delays:
        raise ValueError('reason bucket latency not present in summary')
    for latency, reported in output['latencies'].items():
        delay = float(latency)
        reasons = by_delay[delay]
        total = sum(reasons.values())
        if total != reported['signals']:
            raise ValueError('complete rows/counters required: signal total mismatch')
        # Keep the legacy report so correction is explicit and reviewable.
        result[latency] = dict(signals=total, reasons=dict(sorted(reasons.items())),
            reasons_by_source={s:dict(sorted(v.items())) for s,v in sorted(sources[delay].items())},
            legacy_reported_reasons=reported.get('reasons', {}),
            legacy_missing_count=total - sum(reported.get('reasons', {}).values()))
        reported['reasons'] = dict(sorted(reasons.items()))
    output['reason_accounting'] = dict(schema='current-h-reason-accounting-v1',
        complete=True, pre_compaction_counters_required=True, by_latency=result,
        financial_metrics_unchanged=True, frozen_execution_unchanged=True)
    return output
