"""Development-only, decision-stamped feature admission for existing flow rules.

No execution, fitting, labels, PnL, deployment, or new candidate specifications.
The caller must construct auxiliary features causally; timestamps are checked
but their truth cannot be established by this join.
"""
from collections import Counter
from collections.abc import Mapping
import hashlib
import json
import math

from binance_native_flow import NativeFlowTape
from mine500_catalog import make_candidates, make_supplementary_candidates, signal, _valid_feature

VERSION = 'native-flow-catalog-join-dev-v1'
FLOW_FIELDS = frozenset(('flow_15s', 'flow_60s'))


def flow_candidates():
    return [c for c in make_candidates() + make_supplementary_candidates()
            if FLOW_FIELDS.intersection(c['required_features'])]


def _clock(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def evaluate_decision(tape, *, decision_recv_ms, capture_clock_id,
                      auxiliary, split):
    """Evaluate all 180 existing flow-dependent variants, not trade attempts.

    Auxiliary fields have value, as_of_ms, available_ms, capture_clock_id and
    provenance. as_of_ms MUST equal the decision clock: no stale carry-forward
    of tau/returns/volatility/TWAP. available_ms must be no later than as_of_ms.
    Clock IDs identify a recorder clock domain, not a venue or timezone.
    A matching string is an attestation, not proof of clock synchronization.
    Each required field's failure is retained; no missing value becomes zero.
    Native flow fields cannot be overridden by caller-supplied scalar values.
    """
    if split != 'development':
        raise ValueError('new development lane only; never inherit an old freeze')
    if not isinstance(tape, NativeFlowTape) or not _clock(decision_recv_ms):
        raise ValueError('native tape and finite local decision clock required')
    if not isinstance(capture_clock_id, str) or not capture_clock_id.strip():
        raise ValueError('explicit shared capture clock domain required')
    if not isinstance(auxiliary, Mapping):
        raise ValueError('auxiliary must be a field-evidence mapping')
    if FLOW_FIELDS.intersection(auxiliary):
        raise ValueError('native flow cannot be overridden')
    snapshot = tape.features(decision_recv_ms)
    candidates = flow_candidates()
    required = set().union(*(set(c['required_features']) for c in candidates))
    values, field_audit = {}, {}
    for name in sorted(required):
        if name in FLOW_FIELDS:
            audit = snapshot['audit'][name]
            value = snapshot['features'][name]
            available = snapshot['feature_available_ms'][name]
            ok = (_valid_feature(name, value) and _clock(available)
                  and available <= decision_recv_ms)
            field_audit[name] = {'status': 'admitted' if ok else audit['status'],
                                 'evidence': audit}
            if ok:
                values[name] = value
            continue
        evidence = auxiliary.get(name)
        status = 'admitted'
        if not isinstance(evidence, Mapping):
            status = 'missing_field_evidence'
        elif not _valid_feature(name, evidence.get('value')):
            status = 'invalid_or_missing_value'
        elif evidence.get('capture_clock_id') != capture_clock_id:
            status = 'different_or_missing_clock_domain'
        elif not isinstance(evidence.get('provenance'), str) or not evidence['provenance'].strip():
            status = 'missing_provenance'
        elif not _clock(evidence.get('as_of_ms')) or not _clock(evidence.get('available_ms')):
            status = 'invalid_or_missing_feature_clock'
        elif evidence['as_of_ms'] != decision_recv_ms:
            status = 'not_computed_for_this_decision'
        elif evidence['available_ms'] > decision_recv_ms:
            status = 'not_yet_available'
        field_audit[name] = {'status': status}
        if status == 'admitted':
            values[name] = evidence['value']
            field_audit[name]['evidence'] = dict(evidence)
    rows = []
    for c in candidates:
        blockers = {k:field_audit[k]['status'] for k in c['required_features'] if k not in values}
        direction = None
        if blockers:
            status = 'blocked_features'
        else:
            tau, p = values['tau_s'], c['params']
            in_window = p['tau_min_s'] <= tau and (tau <= p['tau_max_s'] if p['tau_upper_inclusive'] else tau < p['tau_max_s'])
            if not in_window:
                status = 'outside_tau_window'
            else:
                direction = signal(c, values)
                status = 'signal' if direction else 'rule_not_triggered'
        rows.append({'candidate_id':c['id'], 'template':c['template'],
                     'rule_fingerprint':c['rule_fingerprint'], 'status':status,
                     'direction':direction, 'blockers':blockers})
    return {'version':VERSION, 'split':split, 'decision_recv_ms':decision_recv_ms,
            'capture_clock_id':capture_clock_id, 'clock_domain_independently_verified':False,
            'candidate_variants':len(rows), 'templates':len({c['template'] for c in candidates}),
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'admitted_features':values, 'field_audit':field_audit,
            'native_snapshot_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True,allow_nan=False).encode()).hexdigest(),
            'rows':rows, 'new_candidates_registered':0, 'actual_backtests_performed':0,
            'order_attempts':0, 'pnl_usd':None, 'formal_oos_result':None,
            'warning':'Feature admission and rule signals only; no fixed-limit FAK, fees, fills, cash or profits evaluated.'}
