"""Inventory recording coverage across lanes, without pooling strategy returns.

Only consumes coverage reports produced by completed runs. A union of recording
spans is not a union of executable strategies or proof of feed health. Missing
per-market presence identities cannot be inferred from aggregate counts.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

DAY = 86_400_000
SLOT = 300_000


def union_intervals(values):
    ordered = []
    for pair in values:
        if (len(pair) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                  or not math.isfinite(x) for x in pair) or pair[1] <= pair[0]):
            raise ValueError('invalid recording interval')
        ordered.append(tuple(pair))
    result = []
    for start, end in sorted(ordered):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def summarize(lanes):
    if not lanes or len({x['lane'] for x in lanes}) != len(lanes):
        raise ValueError('unique nonempty lane inventory required')
    intervals, markets, identities = [], {}, {}
    lane_rows = []
    for entry in lanes:
        data = entry['coverage']
        lane = entry['lane']
        span = union_intervals(data['observed_intervals_ms'])
        intervals.extend(span)
        ids = set()
        for market in data['markets']:
            mid = market['market_id']
            identity = (market['start_ms'], market['end_ms'])
            if mid in identities and identities[mid] != identity:
                raise ValueError('conflicting market calendar: ' + mid)
            identities[mid] = identity
            markets[mid] = market
            ids.add(mid)
        lane_rows.append({'lane': lane, 'coverage_sha256': entry.get('sha256'),
                          'coverage_path': entry.get('path'),
                          'observed_hours': sum(b-a for a,b in span)/3_600_000,
                          'registered_markets': len(ids),
                          'book_markets': data.get('book_markets'),
                          'decision_markets': data.get('decision_markets'),
                          'outcome_markets': data.get('outcome_markets'),
                          'daily': data['daily'], 'market_ids': sorted(ids)})
    merged = union_intervals(intervals)
    if not merged:
        raise ValueError('no recorded intervals')
    daily = []
    first = int(merged[0][0]//DAY)*DAY
    last = int(merged[-1][1]//DAY)*DAY
    for day in range(first, last+1, DAY):
        clipped = [(max(a, day), min(b, day+DAY)) for a,b in merged
                   if min(b, day+DAY) > max(a, day)]
        if not clipped and day == last and merged[-1][1] == day:
            continue
        complete = sum(any(a <= s and s+SLOT <= b for a,b in clipped)
                       for s in range(day, day+DAY, SLOT))
        hours = sum(b-a for a,b in clipped)/3_600_000
        date = datetime.fromtimestamp(day/1000, timezone.utc).date().isoformat()
        daily.append({'date_utc': date, 'expected_slots': 288,
                      'observed_hours_union': hours, 'complete_recorded_span_slots': complete,
                      'registered_markets_union': sum(day <= m['start_ms'] < day+DAY for m in markets.values()),
                      'full_span_day': hours == 24,
                      'joint_strategy_usable_slots': None})
    overlap = []
    for index, left in enumerate(lane_rows):
        for right in lane_rows[index+1:]:
            overlap.append({'left': left['lane'], 'right': right['lane'],
                            'shared_market_count': len(set(left['market_ids']) & set(right['market_ids']))})
    for row in lane_rows:
        del row['market_ids']
    return {'schema': 'replay-recording-inventory-v1', 'lanes': lane_rows,
            'unique_observed_hours': sum(b-a for a,b in merged)/3_600_000,
            'unique_registered_markets': len(markets), 'observed_intervals_union_ms': merged,
            'daily': daily, 'pairwise_market_overlap': overlap,
            'full_recorded_span_days': sum(row['full_span_day'] for row in daily),
            'full_executable_days': None, 'formal_oos_result': None,
            'note': 'Coverage union only. Different source lanes and overlapping strategies have no pooled PnL. '
                    'Full span does not prove continuous health, feature availability or executable depth.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', action='append', required=True, help='lane=coverage.json')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    lanes = []
    for item in args.coverage:
        lane, name = item.split('=', 1)
        path = Path(name)
        body = path.read_bytes()
        lanes.append({'lane': lane, 'path': str(path.resolve()),
                      'sha256': hashlib.sha256(body).hexdigest(), 'coverage': json.loads(body)})
    result = summarize(lanes)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({key: result[key] for key in ('unique_observed_hours', 'unique_registered_markets',
                                                 'full_recorded_span_days', 'full_executable_days')}))


if __name__ == '__main__':
    main()
