"""Development bridge into existing receipt-clock FAK engine; no new engine.

Bindings carry the original recorder tape and current-decision tau evidence.
Caller attestations do not establish independent real coverage or OOS eligibility.
"""
from copy import deepcopy
import hashlib
import json
import mine500_replay_v2 as execution
from mine500_catalog import signal
import usdm_flow_candidates as rules

VERSION = 'usdm-flow-execution-bridge-dev-v1'
SCENARIOS = (('base', {}), ('half_depth', {'depth_fraction': .5}),
             ('extra_1cent', {'extra_cost_per_filled_share_usd': .01}))


def replay_development(events, bindings, *, split, input_evidence_class):
    if split != 'development' or input_evidence_class not in ('synthetic_fixture', 'consumed_development'):
        raise ValueError('explicit development evidence required; no OOS adoption')
    events = deepcopy(list(events))
    headers = [e for e in events if e.get('type') == 'dataset']
    if len(headers) != 1 or not events or events[0] is not headers[0] or headers[0].get('split') != 'development':
        raise ValueError('one first development dataset header required')
    index = {}
    for b in bindings:
        key = (str(b['market_id']), b['decision_recv_ms'])
        if key in index: raise ValueError('duplicate decision binding')
        index[key] = b
    seen = set(); audit = []
    required = ('tau_s', 'flow_15s', 'flow_60s')
    for event in events:
        if event.get('type') != 'decision': continue
        key = (str(event['market_id']), event['recv_ms'])
        if key in seen: raise ValueError('duplicate input decision')
        seen.add(key)
        if {'flow_15s','flow_60s'}.intersection(event.get('features', {})):
            raise ValueError('caller scalar flow cannot override native recorder evidence')
        b = index.get(key)
        if b is None:
            event['features'] = dict.fromkeys(required)
            event['feature_available_ms'] = dict.fromkeys(required)
            audit.append(dict(market_id=key[0], decision_recv_ms=key[1], status='missing_binding'))
            continue
        result = rules.evaluate_decision(b['tape'], decision_recv_ms=key[1],
            tau_evidence=b['tau_evidence'], capture_clock_id=b['capture_clock_id'], split=split)
        event['features'] = {n:result['admitted_features'].get(n) for n in required}
        available = {n:result['flow_audit'][n].get('available_ms') for n in required if n != 'tau_s'}
        available['tau_s'] = b['tau_evidence'].get('available_ms') if 'tau_s' in result['admitted_features'] else None
        event['feature_available_ms'] = available
        audit.append(dict(market_id=key[0], decision_recv_ms=key[1], result=result))
    if set(index) - seen: raise ValueError('binding without an input decision')
    candidates = rules.candidates()
    cells = {}
    for name, changes in SCENARIOS:
        config = execution.ReplayConfig(shares=100., initial_cash_usd=1000.,
            latencies_ms=(500,600,1000), require_constraints=True, **changes)
        cells[name] = execution.replay_events(events, candidates, signal, config)
    return dict(version=VERSION, evidence_class=input_evidence_class,
        enriched_input_sha256=hashlib.sha256(json.dumps(events,sort_keys=True,allow_nan=False).encode()).hexdigest(),
        candidate_rule_fingerprints={c['id']:c['rule_fingerprint'] for c in candidates},
        decision_binding_audit=audit, scenarios=cells,
        independent_starting_cash_per_candidate_latency_scenario=1000., target_shares=100.,
        independent_coverage_truth_verified=False, real_fills_verified=False,
        main_ranking_admitted=False, rank7_pnl_usd=None, formal_oos_result=None,
        limitation='Displayed-depth development simulation; bindings are caller attestations, not coverage proof. No old freeze inherited.')
