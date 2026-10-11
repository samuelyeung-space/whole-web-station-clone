"""Training-free, fixed-100 future admission and scoring for one frozen model.

This module NEVER declares formal OOS success. It checks an input manifest,
interprets already-fitted numeric trees without pickle/sklearn/fit, and optionally
runs the unchanged development replay engine. Source authenticity, untouched
status and actual fills require independent evidence review. Missing/incomplete
future evidence produces null results, never zero PnL. No network or order API.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

import mine500_replay_v2 as execution

VERSION = 'fixed100-frozen-json-tree-holdout-v1'
CANDIDATE_ID = 'EPM-matched_market-02'
MODEL_ID = 'matched_market'
REQUIRED = ('mid_up', 'tau_s', 'ep_r_5s', 'ep_r_15s', 'ep_r_30s', 'ep_r_60s')
START = '2026-10-12T00:00:00+00:00'
END = '2026-10-19T00:00:00+00:00'
FIELDS = ['value', 'count', 'feature_idx', 'num_threshold', 'missing_go_to_left',
          'left', 'right', 'gain', 'depth', 'is_leaf', 'bin_threshold',
          'is_categorical', 'bitset_idx']
CODE_FILES = ('mine500_fixed100_holdout.py', 'mine500_replay_v2.py',
              'mine500_constraints.py', 'mine500_endpoint.py')


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def millis(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def candidate():
    c = dict(id=CANDIDATE_ID, model_id=MODEL_ID, margin=.02,
             required_features=['ep_prob_matched_market', 'ask_up', 'ask_down', 'decision_fee_rate'],
             family='market_prior_conditional_residual', independent_mechanism_claim=False,
             selection_status='frozen_future_simulation_pending_independent_evidence_review')
    c['rule_fingerprint'] = execution.fingerprint(c)
    return c


def signal(c, f):
    p, fee, up, down = (f.get(k) for k in
                        ('ep_prob_matched_market', 'decision_fee_rate', 'ask_up', 'ask_down'))
    if not all(finite(v) for v in (p, fee, up, down)):
        return None
    if not 0 <= p <= 1 or not 0 <= fee <= 1 or not 0 < up < 1 or not 0 < down < 1:
        return None
    a, b = p-up-fee*up*(1-up), 1-p-down-fee*down*(1-down)
    if max(a, b) < c['margin']:
        return None
    return 'Up' if a >= b else 'Down'


class FrozenModel:
    """Exact raw-numeric prediction of the saved sklearn tree representation."""
    def __init__(self, fold):
        if fold.get('model_id') != MODEL_ID or fold.get('day') != '2026-10-09' or fold.get('status') != 'scored':
            raise ValueError('wrong_preexisting_fitted_fold')
        state = fold['learned_state']
        if execution.fingerprint([MODEL_ID, fold['train_hash'], state]) != fold.get('model_hash'):
            raise ValueError('fitted_model_hash_mismatch')
        self.cutoff = fold['cutoff_ms']
        if (not finite(self.cutoff) or self.cutoff != millis('2026-10-09T00:00:00+00:00')
                or not finite(fold.get('latest_training_label_ms'))
                or fold['latest_training_label_ms'] >= self.cutoff):
            raise ValueError('training_cutoff_or_label_invalid')
        ids = fold['training_market_ids']
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or not all(isinstance(x, str) and x for x in ids):
            raise ValueError('training_market_ids_invalid')
        self.training_ids = frozenset(ids)
        self.model_hash = fold['model_hash']
        baseline = state.get('baseline')
        if (not isinstance(baseline, list) or len(baseline) != 1 or
                not isinstance(baseline[0], list) or len(baseline[0]) != 1 or not finite(baseline[0][0])):
            raise ValueError('unsupported_baseline')
        self.baseline = baseline[0][0]
        stages = state.get('trees')
        if not isinstance(stages, list) or len(stages) != 50:
            raise ValueError('expected_exactly_50_boosting_stages')
        self.trees = []
        for stage in stages:
            if not isinstance(stage, list) or len(stage) != 1:
                raise ValueError('unsupported_stage_arity')
            tree = stage[0]
            if (tree.get('node_fields') != FIELDS or tree.get('raw_left_cat_bitsets') != []
                    or tree.get('binned_left_cat_bitsets') != []):
                raise ValueError('unsupported_numeric_tree_schema_or_categorical_split')
            nodes = tree['nodes']
            if not isinstance(nodes, list) or not 1 <= len(nodes) <= 15:
                raise ValueError('unsupported_tree_size')
            decoded = []
            for row in nodes:
                if not isinstance(row, list) or len(row) != len(FIELDS) or not all(finite(x) for x in row):
                    raise ValueError('invalid_numeric_tree_node')
                node = dict(zip(FIELDS, row))
                for k in ('feature_idx', 'left', 'right', 'is_leaf', 'is_categorical', 'missing_go_to_left'):
                    if type(node[k]) is not int:
                        raise ValueError('noninteger_tree_index_or_flag')
                if node['is_categorical'] != 0 or node['is_leaf'] not in (0, 1):
                    raise ValueError('unsupported_categorical_or_leaf_flag')
                if node['missing_go_to_left'] not in (0, 1) or not 0 <= node['feature_idx'] < 6:
                    raise ValueError('unsupported_numeric_feature_index')
                if not node['is_leaf'] and not all(0 <= node[k] < len(nodes) for k in ('left', 'right')):
                    raise ValueError('tree_child_outside_bounds')
                decoded.append(node)
            visited, active = set(), set()
            def walk(i):
                if i in active or i in visited:
                    raise ValueError('tree_cycle_or_shared_child')
                visited.add(i); active.add(i)
                if not decoded[i]['is_leaf']:
                    walk(decoded[i]['left']); walk(decoded[i]['right'])
                active.remove(i)
            walk(0)
            if len(visited) != len(nodes) or sum(n['is_leaf'] for n in decoded) > 8:
                raise ValueError('unreachable_node_or_too_many_leaves')
            self.trees.append(decoded)

    def predict(self, row):
        now = row.get('recv_ms')
        if not finite(now) or now < self.cutoff:
            raise ValueError('prediction_precedes_fit_cutoff')
        if str(row.get('market_id')) in self.training_ids:
            raise ValueError('training_market_cannot_be_scored')
        f, a = row.get('features', {}), row.get('feature_available_ms', {})
        for k in REQUIRED:
            if not finite(f.get(k)) or not finite(a.get(k)):
                raise ValueError('missing_endpoint_support:' + k)
            if a[k] > now:
                raise ValueError('future_feature:' + k)
        p, tau = f['mid_up'], f['tau_s']
        if not 0 < p < 1 or not 0 < tau <= 300:
            raise ValueError('invalid_probability_or_remaining_time')
        logit = min(6.0, max(-6.0, math.log(p/(1-p)))) / 3
        tail, middle = float(tau <= 60), float(60 < tau <= 120)
        x = [1.0, logit, tail, middle, logit*tail, logit*middle]
        residual = self.baseline
        for tree in self.trees:
            i = 0
            while not tree[i]['is_leaf']:
                n = tree[i]
                i = n['left'] if x[n['feature_idx']] <= n['num_threshold'] else n['right']
            residual += tree[i]['value']
        return min(.999, max(.001, p + residual))


def freeze(audit_path, out):
    """Copy the original Oct9 fitted fold; no data selection or refitting."""
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    audit = json.loads(Path(audit_path).read_text())
    folds = [f for f in audit['folds'] if f.get('day') == '2026-10-09' and f.get('model_id') == MODEL_ID]
    if len(folds) != 1:
        raise ValueError('expected_one_preexisting_fold')
    fold = folds[0]; FrozenModel(fold)
    now = datetime.now(timezone.utc).isoformat()
    if millis(now) >= millis(START):
        raise ValueError('cannot_freeze_after_holdout_start')
    model = out / 'frozen_model_oct9.json'; write_json(model, fold)
    protocol = dict(version=VERSION, protocol_id='EPM-fixed100-20261012-20261019-v1',
        frozen_at_utc=now, start_utc=START, end_utc=END, start_ms=millis(START), end_ms=millis(END),
        model_file=model.name, model_sha256=digest(model), source_model_audit_sha256=digest(audit_path),
        model_hash=fold['model_hash'], model_fit_cutoff_ms=fold['cutoff_ms'],
        model_policy='immutable_preexisting_oct9_fold_no_refit_or_daily_update',
        candidate=candidate(), execution=asdict(execution.ReplayConfig(shares=100, initial_cash_usd=1000,
            latencies_ms=(500,600,1000), require_constraints=True)),
        code_sha256={name:digest(Path(__file__).parent/name) for name in CODE_FILES},
        warmup_ms=300000, expected_market_count=2016,
        code_change_policy='new_protocol_and_strictly_later_untouched_window_required',
        prior_protocols_unchanged=True,
        overlapping_windows_are_independent=False,
        multiplicity='selected_after_at_least_901_historical_adaptive_trials; new_size_is_not_new_mechanism',
        expected_input=dict(split='holdout_candidate', recv_order='nondecreasing_local_receipt',
            market_duration_ms=300000, full_calendar_window=True,
            provenance_fields=['source','inputs','closed_through_ms','untouched_attestation'],
            each_input_fields=['artifact_identity','sha256'],
            unknown_authenticity='manifest_claims_do_not_establish_authenticity'),
        evidence_class='simulation_pending_independent_OOS_evidence_review',
        engine_effective_split='development', formal_oos_result=None, confirmed_strategies=0,
        status='awaiting_future_closed_window_and_independent_source_review')
    write_json(out/'protocol.json', protocol)
    write_json(out/'admission.json', dict(status='not_run_future_input_missing', formal_oos_result=None,
        simulation_result=None, window_start_utc=START, window_end_utc=END))
    return protocol


def load_frozen(protocol_path):
    p = Path(protocol_path); protocol = json.loads(p.read_text())
    if protocol.get('version') != VERSION or protocol.get('candidate') != candidate():
        raise ValueError('protocol_version_or_candidate_changed')
    expected = asdict(execution.ReplayConfig(shares=100, initial_cash_usd=1000,
                      latencies_ms=(500,600,1000), require_constraints=True))
    # JSON serializes the latency tuple as an array.
    expected['latencies_ms'] = list(expected['latencies_ms'])
    if protocol.get('execution') != expected:
        raise ValueError('fixed100_execution_changed')
    if protocol.get('start_ms') != millis(START) or protocol.get('end_ms') != millis(END):
        raise ValueError('frozen_window_changed')
    if millis(protocol['frozen_at_utc']) >= protocol['start_ms']:
        raise ValueError('freeze_not_before_holdout')
    for name in CODE_FILES:
        if protocol.get('code_sha256', {}).get(name) != digest(Path(__file__).parent/name):
            raise ValueError('frozen_code_changed:' + name)
    model_path = p.parent/protocol['model_file']
    if digest(model_path) != protocol['model_sha256']:
        raise ValueError('model_file_hash_changed')
    model = FrozenModel(json.loads(model_path.read_text()))
    if model.model_hash != protocol['model_hash']:
        raise ValueError('protocol_model_hash_changed')
    return protocol, model


def admit(events, protocol, model, now_ms):
    """Stream integrity/temporal admission, not certification of OOS or fills."""
    start, end = protocol['start_ms'], protocol['end_ms']
    reasons, counts, markets, outcomes = set(), Counter(), {}, {}
    dataset = None; previous = -math.inf; first = last = None
    if now_ms < end:
        reasons.add('holdout_window_not_closed')
    for row in events:
        kind, now = row.get('type'), row.get('recv_ms')
        if kind not in execution.SCHEMA or not finite(now):
            raise ValueError('unknown_type_or_missing_local_receipt')
        if now < previous or now > now_ms:
            raise ValueError('regressed_or_future_local_receipt')
        if now < start-protocol['warmup_ms']:
            raise ValueError('old_data_outside_frozen_holdout')
        previous = now; first = now if first is None else first; last = now
        counts[kind] += 1
        if kind == 'dataset':
            if dataset is not None or sum(counts.values()) != 1:
                raise ValueError('dataset_header_must_be_first_and_unique')
            dataset = row
            if row.get('split') != 'holdout_candidate':
                raise ValueError('reused_development_not_admitted')
            continue
        if dataset is None:
            raise ValueError('missing_dataset_header')
        mid = row.get('market_id')
        if not isinstance(mid, str) or not mid or mid in model.training_ids:
            raise ValueError('missing_or_training_market')
        if kind == 'market':
            a, b = row.get('start_ms'), row.get('end_ms')
            if not finite(a) or not finite(b) or b-a != 300000 or not start <= a < b <= end:
                raise ValueError('market_outside_holdout_or_not_5m')
            if row.get('label_source') not in execution.OFFICIAL_SOURCES:
                raise ValueError('unofficial_market_label_source')
            if mid in markets and any(markets[mid].get(k) != row.get(k) for k in ('start_ms','end_ms','label_source')):
                raise ValueError('conflicting_market_definition')
            markets[mid] = row
        elif mid not in markets:
            raise ValueError('event_precedes_market_metadata')
        if kind == 'decision':
            if not start <= now < end or not markets[mid]['start_ms'] <= now < markets[mid]['end_ms']:
                raise ValueError('decision_outside_market_or_holdout')
            f, av = row.get('features'), row.get('feature_available_ms')
            if not isinstance(f, dict) or not isinstance(av, dict):
                raise ValueError('missing_feature_maps')
            for key in REQUIRED:
                if f.get(key) is not None:
                    if not finite(f[key]) or not finite(av.get(key)) or av[key] > now:
                        raise ValueError('invalid_or_future_feature:' + key)
            if finite(f.get('tau_s')) and abs(f['tau_s']-(markets[mid]['end_ms']-now)/1000) > 1e-8:
                raise ValueError('remaining_time_does_not_match_market')
            for seconds in (5,15,30,60):
                key = f'ep_r_{seconds}s'
                if f.get(key) is None:
                    continue
                ev = row.get('endpoint_evidence', {}).get(key)
                if not isinstance(ev, dict) or not all(finite(ev.get(k)) for k in
                        ('value','available_ms','start_recv_ms','end_recv_ms')):
                    raise ValueError('missing_endpoint_evidence')
                if (ev['value'] != f[key] or ev['available_ms'] != av[key]
                        or ev['end_recv_ms'] != av[key]
                        or not 0 <= now-ev['end_recv_ms'] <= 1000
                        or not 0 <= now-seconds*1000-ev['start_recv_ms'] <= 1000
                        or type(ev.get('connection_epoch')) is not int or ev['connection_epoch'] < 1):
                    raise ValueError('invalid_endpoint_receipt_evidence')
        if kind == 'book':
            for side in ('up','down'):
                v = row.get(side+'_recv_ms')
                if not finite(v) or v > now:
                    raise ValueError('missing_or_future_book_side_receipt')
        if kind == 'constraint':
            for key in ('min_order_size','up_tick_size','down_tick_size'):
                if key in row:
                    v = row.get(key+'_available_ms')
                    if not finite(v) or v > now or not finite(row.get(key+'_effective_ms')):
                        raise ValueError('missing_or_future_constraint_receipt')
        if kind == 'outcome':
            market = markets[mid]; resolved = row.get('resolved_ms')
            if (row.get('source') != market['label_source'] or row.get('winner') not in ('Up','Down')
                    or not finite(resolved) or not market['end_ms'] <= resolved <= now):
                raise ValueError('invalid_official_outcome_clock_or_source')
            if mid in outcomes and outcomes[mid] != row['winner']:
                raise ValueError('conflicting_official_outcomes')
            outcomes[mid] = row['winner']
    if dataset is None:
        reasons.add('missing_dataset')
    else:
        prov = dataset.get('provenance', {})
        if (not isinstance(prov, dict) or not prov.get('source') or
                not isinstance(prov.get('inputs'), list) or not prov['inputs']):
            reasons.add('missing_provenance_inputs')
        else:
            for value in prov['inputs']:
                sha = value.get('sha256', '')
                if (not value.get('artifact_identity') or not isinstance(sha, str) or len(sha) != 64
                        or any(c not in '0123456789abcdef' for c in sha)):
                    reasons.add('missing_source_artifact_identity_or_sha256')
            attestation = prov.get('untouched_attestation')
            if not isinstance(attestation, dict) or not attestation.get('evidence_ref'):
                reasons.add('untouched_status_not_documented')
        closed = prov.get('closed_through_ms') if isinstance(prov, dict) else None
        if not finite(closed) or not end <= closed <= now_ms:
            reasons.add('full_window_close_not_documented')
        intervals = dataset.get('observed_intervals_ms')
        try:
            execution._validate_observed_intervals(intervals)
            # A complete input needs uninterrupted declared observation of the
            # whole window. A declaration is not proof; raw evidence still reviewed.
            if len(intervals) != 1 or intervals[0][0] > start or intervals[0][1] < end:
                reasons.add('incomplete_observation_window')
        except (ValueError, TypeError):
            reasons.add('missing_or_invalid_observation_intervals')
    if len(markets) != protocol['expected_market_count']:
        reasons.add('incomplete_5m_market_calendar')
    starts = {m['start_ms'] for m in markets.values()}
    if starts != set(range(start, end, 300000)):
        reasons.add('missing_or_duplicate_calendar_slots')
    if set(markets)-set(outcomes):
        reasons.add('missing_official_outcomes')
    if not counts['decision']:
        reasons.add('no_decisions')
    return dict(status='blocked' if reasons else 'admitted_for_simulation_only', reasons=sorted(reasons),
        counts=dict(counts), markets=len(markets), official_outcomes=len(outcomes),
        first_receipt_ms=first, last_receipt_ms=last,
        evidence_class='simulation_pending_independent_OOS_evidence_review',
        formal_oos_result=None, simulation_result=None, confirmed_strategies=0)


def scored_events(events, model, audit):
    """Single-pass causal score attachment. Outcomes never enter prediction."""
    books = {}; counts = Counter(); previous = -math.inf
    for row in events:
        now = row['recv_ms']
        if not finite(now) or now < previous:
            raise ValueError('event_receipt_clock_regressed')
        previous = now; mid = str(row.get('market_id', ''))
        if row.get('type') == 'book':
            books[mid] = row
        if row.get('type') != 'decision':
            yield row; continue
        f, av = dict(row.get('features', {})), dict(row.get('feature_available_ms', {}))
        try:
            p = model.predict(row)
        except ValueError as exc:
            if not str(exc).startswith('missing_endpoint_support:'):
                raise
            p = None; counts[str(exc)] += 1
        f['ep_prob_matched_market'] = p
        av['ep_prob_matched_market'] = max(model.cutoff, *(av[k] for k in REQUIRED)) if p is not None else None
        book = books.get(mid)
        for side in ('up','down'):
            key = 'ask_'+side
            levels = book.get(side+'_asks', []) if book else []
            valid = [(x,q) for x,q in levels if finite(x) and 0 < x < 1 and finite(q) and q > 0]
            stamp = book.get(side+'_recv_ms', book['recv_ms']) if book else None
            f[key] = min((x for x,q in valid), default=None) if finite(stamp) and stamp <= now else None
            av[key] = stamp if f[key] is not None else None
        fee = book.get('fee_rate') if book else None
        f['decision_fee_rate'] = fee if finite(fee) and 0 <= fee <= 1 else None
        av['decision_fee_rate'] = book['recv_ms'] if f['decision_fee_rate'] is not None else None
        counts['decisions'] += 1; counts['scored'] += p is not None
        yield dict(row, features=f, feature_available_ms=av)
    audit.update(dict(counts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('freeze'); f.add_argument('--model-audit', required=True); f.add_argument('--out', required=True)
    r = sub.add_parser('run'); r.add_argument('--protocol', required=True); r.add_argument('--input', required=True)
    r.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.command == 'freeze':
        freeze(args.model_audit, args.out); return
    protocol, model = load_frozen(args.protocol)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).timestamp()*1000
    report = dict(status='blocked', formal_oos_result=None, simulation_result=None, confirmed_strategies=0)
    path = Path(args.input)
    if not path.is_file():
        report['reasons'] = ['future_input_missing']
    else:
        report['input_sha256'] = digest(path)
        try:
            report.update(admit(execution.read_jsonl(path), protocol, model, now))
        except (ValueError, EOFError, OSError) as exc:
            report['reasons'] = [str(exc)]
        if report['status'] == 'admitted_for_simulation_only':
            audit = {}; config = dict(protocol['execution']); config['latencies_ms'] = tuple(config['latencies_ms'])
            result = execution.replay_events(scored_events(execution.read_jsonl(path), model, audit),
                [candidate()], signal, execution.ReplayConfig(**config))
            if digest(path) != report['input_sha256']:
                raise ValueError('input_changed_between_admission_and_execution')
            with gzip.open(out/'attempts.jsonl.gz', 'wt') as handle:
                for row in result.pop('attempts'):
                    handle.write(json.dumps(row, allow_nan=False)+'\n')
            write_json(out/'simulation_audit.json', result)
            report.update(scoring=audit, simulation_result=result['summary'],
                engine_effective_split=result['split_validation']['effective_split'])
            # Preserve the engine's development classification. This wrapper
            # cannot manufacture a certified OOS result from a metadata tag.
    write_json(out/'admission.json', report)


if __name__ == '__main__':
    main()
