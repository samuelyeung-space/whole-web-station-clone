"""Venue-specific development variants reusing the existing flow rules.

Only signal admission is implemented here. No orders, fills, fitting, labels,
economic replay or adoption into a frozen spot/source-race protocol.
"""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math

from mine500_catalog import make_candidates, make_supplementary_candidates, signal, _valid_feature
from recorder_futures_flow import RecorderFuturesFlowTape, CONTRACT, EXCHANGE, INSTRUMENT

VERSION = 'usdm-flow-development-v1'
TEMPLATES = ('flow_persistence', 'flow_acceleration', 'flow_exhaustion', 'flow_flip')


def candidates():
    """One existing first-registered default per template; no winner selection."""
    catalog = make_candidates() + make_supplementary_candidates()
    output = []
    for i, template in enumerate(TEMPLATES, 1):
        base = next(c for c in catalog if c['template'] == template)
        c = deepcopy(base)
        c.update(id=f'UFLOW-20261025-{i:03d}', version=VERSION,
                 source_candidate_id=base['id'],
                 source_rule_fingerprint=base['rule_fingerprint'],
                 venue=EXCHANGE, instrument=INSTRUMENT, recorder_contract=CONTRACT,
                 novelty='explicit_futures_venue_variant_of_existing_flow_rule',
                 research_status='registered_signal_only_execution_unbound',
                 independent_mechanism_claim=False,
                 execution_binding=None,
                 main_ranking_admitted=False)
        c.pop('rule_fingerprint')
        c['rule_fingerprint'] = hashlib.sha256(json.dumps(c, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        output.append(c)
    return output


def _clock(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def evaluate_decision(tape, *, decision_recv_ms, tau_evidence, capture_clock_id, split):
    if split != 'development' or not isinstance(tape, RecorderFuturesFlowTape):
        raise ValueError('explicit development RecorderFuturesFlowTape required; no spot substitution')
    if not _clock(decision_recv_ms):
        raise ValueError('finite local receive decision clock required')
    expected_clock = tape.arguments['capture_run_id'] + ':recv_ms'
    if capture_clock_id != expected_clock:
        raise ValueError('clock attestation must identify this recorder run receive clock')
    snapshot = tape.features(decision_recv_ms)
    if snapshot['venue'] != EXCHANGE or snapshot['recorder_contract'] != CONTRACT:
        raise ValueError('futures provenance mismatch')
    blockers = {}
    values = {}
    for name in ('flow_15s', 'flow_60s'):
        value = snapshot['features'][name]
        available = snapshot['feature_available_ms'][name]
        if not _valid_feature(name, value) or not _clock(available) or available > decision_recv_ms:
            blockers[name] = snapshot['audit'][name]['status']
        else:
            values[name] = value
    tau = tau_evidence
    if not isinstance(tau, Mapping) or not _valid_feature('tau_s', tau.get('value')):
        blockers['tau_s'] = 'missing_or_invalid_tau'
    elif tau.get('capture_clock_id') != expected_clock:
        blockers['tau_s'] = 'different_clock_domain'
    elif not _clock(tau.get('as_of_ms')) or tau['as_of_ms'] != decision_recv_ms:
        blockers['tau_s'] = 'tau_not_computed_for_this_decision'
    elif not _clock(tau.get('available_ms')) or tau['available_ms'] > decision_recv_ms:
        blockers['tau_s'] = 'tau_not_yet_available'
    elif not isinstance(tau.get('provenance'), str) or not tau['provenance'].strip():
        blockers['tau_s'] = 'missing_tau_provenance'
    else:
        values['tau_s'] = tau['value']
    rows = []
    for c in candidates():
        direction = None if blockers else signal(c, values)
        status = 'blocked_features' if blockers else ('signal' if direction else 'rule_not_triggered')
        rows.append(dict(candidate_id=c['id'], template=c['template'],
                         rule_fingerprint=c['rule_fingerprint'], status=status,
                         direction=direction, blockers=dict(blockers)))
    return dict(version=VERSION, split=split, venue=EXCHANGE, instrument=INSTRUMENT,
                decision_recv_ms=decision_recv_ms, capture_clock_id=capture_clock_id,
                clock_attestation_independently_verified=False,
                coverage_truth_independently_verified=False,
                admitted_features=values, flow_audit=snapshot['audit'], rows=rows,
                candidate_variants=4, independent_new_mechanisms=0,
                actual_backtests_performed=0, order_attempts=0, pnl_usd=None,
                formal_oos_result=None, main_ranking_admitted=False)
