"""Read-only ECO feature attrition audit; never fits, signals, or calculates PnL.

Independently reconstructs the frozen Enricher's readiness counters. This is a
diagnostic of existing development tapes, not permission to loosen warmup or
carry forward missing scores. Per-input state resets mirror the registered run.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

FIELDS = ('book_5', 'mid_up', 'tau_s')
HORIZONS = (5, 15)
NORMS = ('plain', 'twap_variance')
DERIVATIVES = ('e', 'v', 'a', 'j', 'dq')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def reasons(event):
    f = event.get('features', {})
    av = event.get('feature_available_ms', {})
    t = event.get('recv_ms')
    if not number(t):
        raise ValueError('invalid decision receipt')
    result = []
    for key in FIELDS:
        value = f.get(key)
        if not number(value):
            result.append(key + ':missing_or_nonfinite')
        elif not number(av.get(key)):
            result.append(key + ':unproven_availability')
        elif av[key] > t:
            result.append(key + ':future_availability')
        elif key in ('book_5', 'mid_up') and not 0 < value < 1:
            result.append(key + ':outside_open_unit_interval')
        elif key == 'tau_s' and not 0 < value <= 300:
            result.append(key + ':outside_market_horizon')
    return result


class Audit:
    def __init__(self):
        self.counts = Counter()
        self.by_day = defaultdict(Counter)
        self.states = {}
        self.last_time = {}
        self.previous_regime = {}
        self.run_histogram = {n: Counter() for n in NORMS}
        self.market_ids = set()
        self.mismatch_examples = []
        self.finished = False

    def close_run(self, state, norm):
        if state[norm]:
            self.run_histogram[norm][state[norm]] += 1
        state[norm] = 0

    def decision(self, event):
        if self.finished:
            raise ValueError('audit already finalized')
        mid = event['market_id']
        t = event['recv_ms']
        self.market_ids.add(mid)
        counts = Counter(decisions=1)
        state = self.states.setdefault(mid, {n: 0 for n in NORMS})
        bad = reasons(event)
        f = event['features']
        av = event['feature_available_ms']
        delta = t - self.last_time[mid] if mid in self.last_time else None
        if delta is not None:
            counts['same_market_decision_pairs'] += 1
            counts['exact_1000ms_pairs' if delta == 1000 else 'non_1000ms_pairs'] += 1
        self.last_time[mid] = t
        if bad:
            counts['invalid_source_rows'] += 1
            counts['invalid_pattern|' + ','.join(bad)] += 1
            for reason in bad:
                counts['reason|' + reason] += 1
            for norm in NORMS:
                self.close_run(state, norm)
        else:
            counts['valid_source_rows'] += 1
            regime = f['tau_s'] >= 60
            crossing = mid in self.previous_regime and regime != self.previous_regime[mid]
            self.previous_regime[mid] = regime
            if delta != 1000:
                for norm in NORMS:
                    self.close_run(state, norm)
            if crossing:
                counts['variance_join_resets'] += 1
                self.close_run(state, 'twap_variance')
            for norm in NORMS:
                state[norm] += 1
        for norm in NORMS:
            for horizon in HORIZONS:
                name = f'{norm}_h{horizon}'
                expected = not bad and state[norm] >= 4 * horizon
                keys = [f'eco_{norm}_{horizon}_{k}' for k in DERIVATIVES]
                present = [k in f for k in keys]
                valid = all(number(f.get(k)) and av.get(k) == t for k in keys)
                counts['expected_ready|' + name] += int(expected)
                counts['observed_ready|' + name] += int(valid)
                mismatch = (expected != valid or (any(present) and not valid))
                counts['readiness_mismatch|' + name] += int(mismatch)
                if mismatch and len(self.mismatch_examples) < 10:
                    self.mismatch_examples.append({'market_id': mid, 'recv_ms': t,
                        'group': name, 'expected': expected, 'valid': valid})
        self.counts.update(counts)
        day = datetime.fromtimestamp(t / 1000, timezone.utc).date().isoformat()
        self.by_day[day].update(counts)

    def finish(self):
        if not self.finished:
            for state in self.states.values():
                for norm in NORMS:
                    self.close_run(state, norm)
            self.finished = True
        return {'counts': dict(self.counts), 'decision_markets': len(self.market_ids),
                'by_decision_receipt_utc_date': {d: dict(c) for d, c in sorted(self.by_day.items())},
                'run_length_histogram': {n: dict(sorted(c.items())) for n, c in self.run_histogram.items()},
                'max_valid_run_rows': {n: max(c, default=0) for n, c in self.run_histogram.items()},
                'mismatch_examples': self.mismatch_examples}


def audit(registration, output):
    reg = json.loads(Path(registration).read_text())
    if reg.get('role') != 'already_consumed_development':
        raise ValueError('consumed development registration required')
    sources = []
    # Validate every input before processing any rows; no discovery by glob.
    for item in reg['inputs']:
        path = Path(item['output'])
        if sha(path) != item['output_sha256']:
            raise ValueError('derived input hash mismatch: ' + str(path))
        sources.append({'path': str(path), 'sha256': item['output_sha256']})
    if len({row['path'] for row in sources}) != len(sources):
        raise ValueError('duplicate input')
    segments = []
    for row in sources:
        segment = Audit()
        headers = 0
        with gzip.open(row['path'], 'rt') as stream:
            for line in stream:
                event = json.loads(line)
                if event['type'] == 'dataset':
                    headers += 1
                    if event.get('split') != 'development' or headers != 1:
                        raise ValueError('one development header required')
                elif event['type'] == 'decision':
                    if headers != 1:
                        raise ValueError('decision before header')
                    segment.decision(event)
        if headers != 1:
            raise ValueError('missing dataset header')
        segments.append({**row, **segment.finish()})
        print(json.dumps({'audited': Path(row['path']).name}), flush=True)
    totals = Counter()
    by_day = defaultdict(Counter)
    for segment in segments:
        totals.update(segment['counts'])
        for day, counts in segment['by_decision_receipt_utc_date'].items():
            by_day[day].update(counts)
    if any(v for k, v in totals.items() if k.startswith('readiness_mismatch|')):
        raise ValueError('frozen feature readiness parity failed')
    result = {'schema': 'eco-feature-attrition-audit-v1', 'new_backtests': 0,
        'new_candidates': 0, 'pnl': None, 'formal_oos_result': None,
        'registration_sha256': sha(registration), 'analysis_code_sha256': sha(__file__),
        'source_feature_requirements': FIELDS, 'horizons_seconds': HORIZONS,
        'warmup_multiplier': 4, 'totals': dict(totals),
        'by_decision_receipt_utc_date': {d: dict(v) for d, v in sorted(by_day.items())},
        'segments': segments,
        'interpretation': ['Counts are pre-signal feature coverage, not order attempts or failures.',
            'Source missingness reasons overlap; invalid patterns partition invalid rows.',
            'Run resets match frozen per-market/per-input ECO-v1 behavior, not global forward filling.',
            'No strategy, warmup, model, normalization or OOS window is modified.',
            'This identifies stored-input attrition, not unobserved raw recorder health.']}
    Path(output).write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    audit(args.registration, args.out)
