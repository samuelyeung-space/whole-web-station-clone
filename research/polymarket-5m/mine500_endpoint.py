"""Separate receipt-causal endpoint-return experiment on consumed development.

Endpoint returns need two fresh prices, not an uninterrupted path of fresh
one-second samples. This module adds ep_r_* features without overwriting the
frozen r_*, RV, flow, labels, books or execution constraints. Same connection
epoch, complete observed stream sequence, and <=1000 ms endpoint age are
required. No interpolation, tick-rule aggressor inference or source-clock join.
The 24 trials are feature variants of existing momentum/reversal/confirmation
families, NOT 24 independent mechanisms. All outcomes remain development.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import mine500_deep_batch as batch
import mine500_replay_v2 as execution
from mine500_reference_supplement import validate_summary_coverage

VERSION = 'endpoint-receipt-v1'
WINDOWS = (5, 15, 30, 60)
FRESH_MS = 1000


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class EndpointPrices:
    def __init__(self):
        self.times, self.prices = [], []
        self.epoch = self.sequence = None
        self.connected = False
        self.last_recv = -math.inf

    def consume(self, e):
        now = e.get('recv_ms')
        if not finite(now) or now < self.last_recv:
            raise ValueError('regressed or invalid source receipt clock')
        self.last_recv = now
        kind = e.get('kind', '')
        if not kind.startswith('spot_'):
            return
        epoch, seq = e.get('connection_epoch'), e.get('stream_sequence')
        if type(epoch) is not int or epoch < 1 or type(seq) is not int or seq < 0:
            raise ValueError('missing spot epoch/sequence')
        if kind == 'spot_connection':
            if self.epoch is not None and epoch <= self.epoch:
                raise ValueError('non-increasing connection epoch')
            self.epoch, self.sequence, self.connected = epoch, seq, True
            self.times.clear(); self.prices.clear()
            return
        if epoch != self.epoch or self.sequence is None or seq != self.sequence + 1:
            raise ValueError('spot sequence gap or epoch mismatch')
        self.sequence = seq
        if kind == 'spot_disconnect':
            self.connected = False
            self.times.clear(); self.prices.clear()
            return
        if not self.connected:
            raise ValueError('trade without active connection')
        if kind == 'spot_bbo':
            return  # Shares the spot sequence; never substitutes a trade price.
        if kind != 'spot_trade':
            raise ValueError('unknown spot event kind')
        price = e.get('price')
        if not finite(price) or price <= 0:
            raise ValueError('invalid spot price')
        self.times.append(now); self.prices.append(price)
        # Keep a predecessor to the longest endpoint; never retain a full tape.
        if len(self.times) > 10000:
            cut = max(0, bisect_right(self.times, now - 61000) - 1)
            if cut:
                del self.times[:cut]; del self.prices[:cut]

    def at(self, now, seconds):
        if not finite(now) or seconds not in WINDOWS:
            raise ValueError('invalid endpoint request')
        if self.last_recv > now:
            raise ValueError('source consumed past decision')
        if not self.connected or not self.times:
            return None
        target = now - seconds * 1000
        j = bisect_right(self.times, target) - 1
        if j < 0 or not (0 <= target-self.times[j] <= FRESH_MS
                         and 0 <= now-self.times[-1] <= FRESH_MS):
            return None
        return dict(value=10000*math.log(self.prices[-1]/self.prices[j]),
                    available_ms=self.times[-1], start_recv_ms=self.times[j],
                    end_recv_ms=self.times[-1], connection_epoch=self.epoch)


def augment(events, source_events, audit):
    source = iter(source_events)
    pending = next(source, None)
    prices = EndpointPrices()
    counts = Counter()
    for event in events:
        if event.get('type') == 'decision':
            now = event['recv_ms']
            while pending is not None and pending['recv_ms'] <= now:
                prices.consume(pending)
                pending = next(source, None)
            event = dict(event)
            f, a = dict(event['features']), dict(event['feature_available_ms'])
            evidence = {}
            for seconds in WINDOWS:
                key = f'ep_r_{seconds}s'
                if key in f or key in a:
                    raise ValueError('refuse overwriting endpoint feature')
                value = prices.at(now, seconds)
                f[key] = None if value is None else value['value']
                a[key] = None if value is None else value['available_ms']
                evidence[key] = value
                old = f.get(f'r_{seconds}s')
                counts[f'old_valid_{seconds}s'] += finite(old)
                counts[f'endpoint_valid_{seconds}s'] += value is not None
                if finite(old) and value is not None:
                    counts[f'common_{seconds}s'] += 1
                    if not math.isclose(old, value['value'], abs_tol=1e-9):
                        raise ValueError('endpoint differs on common valid rows')
            event.update(features=f, feature_available_ms=a, endpoint_evidence=evidence)
            counts['decisions'] += 1
        yield event
    # Validate the whole source including gzip integrity, never use future
    # health to delete earlier rows or select trades. Corruption aborts the run.
    while pending is not None:
        prices.consume(pending)
        pending = next(source, None)
    audit.update(dict(counts))


def candidates():
    result = []
    for template in ('follow', 'fade', 'nested_confirmation'):
        for seconds in (15, 60):
            for threshold in (1., 2.):
                for lo, hi in ((20, 90), (90, 240)):
                    req = ['tau_s', f'ep_r_{seconds}s']
                    if template == 'nested_confirmation': req.append('ep_r_5s')
                    c = dict(id=f'EP-{len(result)+1:03d}', version=VERSION,
                        template=template, family='external_endpoint_price',
                        params=dict(seconds=seconds, threshold_bps=threshold, tau_lo=lo, tau_hi=hi),
                        required_features=req, novelty='new_feature_semantics_existing_price_family',
                        prior_art=['mine500_catalog.py', 'strategy_zoo.py'],
                        independent_mechanism_claim=False, selection_status='adaptive_development',
                        execution_kind='single_leg_taker_research')
                    c['rule_fingerprint'] = execution.fingerprint({k:c[k] for k in ('template','params','required_features','version')})
                    result.append(c)
    return result


def signal(c, f):
    p = c['params']; tau = f['tau_s']
    if not (p['tau_lo'] <= tau < p['tau_hi'] or tau == p['tau_hi'] == 240): return None
    value = f[f"ep_r_{p['seconds']}s"]
    if abs(value) < p['threshold_bps']: return None
    if c['template'] == 'nested_confirmation' and value*f['ep_r_5s'] <= 0: return None
    side = 1 if value > 0 else -1
    if c['template'] == 'fade': side *= -1
    return 'Up' if side > 0 else 'Down'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=False)
    inputs = json.loads(Path(args.manifest).read_text())
    hashes = [{**r, 'normalized_sha256':batch.digest(r['normalized']),
               'source_sha256':batch.digest(r['source'])} for r in inputs]
    registry = candidates()
    batch.write_json(out/'registration.json', dict(version=VERSION,
        registered_at_utc=datetime.now(timezone.utc).isoformat(), candidates=registry,
        inputs=hashes, code_sha256=batch.digest(__file__), known_trial_lower_bound=897,
        formal_oos_result=None, scenarios=['size5','size10','size20','half_depth','extra_1c'],
        rules_frozen_before_this_batch_results=True, preexisting_development_results_already_seen=True))
    augmented, coverage = [], []
    for i, r in enumerate(inputs):
        audit = dict(run_id=r['run_id'])
        path = out/f'endpoint_{i}.jsonl.gz'
        batch.write_gzip(path, augment(execution.read_jsonl(r['normalized']),
            execution.read_jsonl(r['source']), audit))
        audit['output_sha256'] = batch.digest(path)
        augmented.append(path); coverage.append(audit)
        print(json.dumps(audit), flush=True)
    batch.write_json(out/'feature_coverage.json', coverage)
    tape, input_audit = batch.combine_inputs(augmented, materialize_output=out/'combined.jsonl.gz')
    batch.write_json(out/'input_audit.json', input_audit)
    metrics, daily = [], []
    for name, changes in batch.SCENARIOS:
        if name not in ('size5','size10','size20','half_depth','extra_1c'): continue
        cfg = execution.ReplayConfig(require_constraints=True, **changes)
        result = execution.replay_events(tape, registry, signal, cfg)
        validate_summary_coverage(registry, result['summary'], cfg.latencies_ms)
        day = batch.describe(result)
        batch.write_gzip(out/f'{name}_attempts.jsonl.gz', result['attempts'])
        batch.write_json(out/f'{name}_audit.json', {k:v for k,v in result.items() if k != 'attempts'})
        metrics += [dict(r, scenario=name) for r in result['summary']]
        daily += [dict(r, scenario=name) for r in day]
        print(name, 'completed', len(result['summary']), flush=True)
    batch.write_json(out/'metrics.json', metrics)
    batch.write_json(out/'daily.json', daily)
    batch.write_json(out/'completion.json', dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),
        candidates=len(registry), scenarios=len(metrics), actual_market_replay=True,
        simulations_only=True, formal_oos_result=None, confirmed_strategies=0))


if __name__ == '__main__': main()
