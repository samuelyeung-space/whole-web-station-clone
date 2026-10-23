"""Registered redemption assumptions on consumed development, no live orders.

Reuse replay_engine and cash_release_audit unchanged. Baseline financial rows
are retained, never counted again; a nine-cell zero-delay parity check is
engineering validation only. All720 original rules enter both fixed delays.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import mine500_catalog as catalog
import replay_engine as engine
from cash_release_audit import reconcile, close

DELAYS_MS = (3600000, 86400000)
PARITY_IDS = ('M500-001', 'M500-680', 'M500-720')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(path, obj):
    with Path(path).open('x') as f:
        json.dump(obj, f, indent=2, allow_nan=False)
        f.write('\n')


def gz_rows(path, rows):
    with Path(path).open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as f:
        for row in rows:
            f.write((json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n').encode())
    with gzip.open(path, 'rt') as f:
        if sum(1 for _ in f) != len(rows):
            raise ValueError('truncated saved ledger')


def config(delay):
    return engine.ReplayConfig(latencies_ms=(500,600,1000), shares=100.,
        initial_cash_usd=1000., max_quote_age_ms=250, require_constraints=True,
        min_notional_usd=1., depth_fraction=1., extra_cost_per_filled_share_usd=0.,
        cash_release_delay_ms=delay, full_or_zero=False, side_specific_books=False,
        post_send_reject_bps=0)


def intents(rows):
    return {(r['candidate_id'],r['latency_ms'],r['market_id']):
        (r['decision_ms'],r['match_ms'],r['side'],r['limit_price'],r['requested_shares']) for r in rows}


def zero_parity(input_path, baseline, candidates):
    small = [c for c in candidates if c['id'] in PARITY_IDS]
    result = engine.replay_events(engine.read_jsonl(input_path), small, catalog.signal, config(0))
    prior = {(r['candidate_id'],r['latency_ms'],r['market_id']):r for r in baseline if r['candidate_id'] in PARITY_IDS}
    current = {(r['candidate_id'],r['latency_ms'],r['market_id']):r for r in result['attempts']}
    if set(prior) != set(current):
        raise ValueError('zero-delay parity attempt inventory mismatch')
    for k, old in prior.items():
        for field, value in old.items():
            if current[k][field] != value:
                raise ValueError('zero-delay parity per-order mismatch: '+field)
    return dict(candidates=list(PARITY_IDS), account_cells=9, compared_attempts=len(prior),
                per_order_financial_differences=0, new_independent_observations=0,
                interpretation='engineering parity against saved baseline, not new financial evidence')


def audit_cash(result, delay):
    metrics = result['summary']
    active = [r for r in metrics if r['attempts']]
    audit = reconcile(result['attempts'], result['cash_events'], active, delay)
    inactive = [r for r in metrics if not r['attempts']]
    for r in inactive:
        if r['net_pnl_usd'] is not None or r['realized_pnl_usd'] is not None:
            raise ValueError('inactive financial result must remain null')
        close(r['ending_cash_usd'],1000.,'inactive cash')
        close(r['ending_reserved_usd']+r['ending_locked_payout_usd'],0.,'inactive asset')
    audit.update(registered_accounts=len(metrics), inactive_accounts=len(inactive),
                 inactive_financial_results_null=True)
    return audit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--candidate-registration',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    candidates=catalog.make_candidates()+catalog.make_supplementary_candidates()
    previous=json.loads(a.candidate_registration.read_text())
    if len(candidates)!=720 or candidates!=previous['candidates']:
        raise ValueError('original catalog differs')
    completion=json.loads((a.baseline/'completion.json').read_text())
    if completion['input_sha256']!=sha(a.input):raise ValueError('baseline input mismatch')
    for name, expected in completion['outputs_sha256'].items():
        if sha(a.baseline/name)!=expected:raise ValueError('baseline output changed')
    base_rows=[json.loads(l) for l in (a.baseline/'base/attempts.jsonl').open()]
    a.out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent
    registration=dict(schema='registered-cash-release-development-v1',
        registered_at_utc=datetime.now(timezone.utc).isoformat(), input_sha256=sha(a.input),
        prior_completion_sha256=sha(a.baseline/'completion.json'),
        candidate_registration_sha256=sha(a.candidate_registration), candidates=candidates,
        candidates_count=720, configs=[asdict(config(d)) for d in DELAYS_MS],
        code_sha256={name:sha(root/name) for name in ('run_registered_cash_release.py',
          'replay_engine.py','cash_release_audit.py','mine500_catalog.py','mine500_constraints.py')},
        primary_latency_ms=500, planned_new_account_cells=4320,
        parity_ids=list(PARITY_IDS), engineering_parity_cells_not_new_evidence=9,
        evidence='already_consumed_development', training=False, old_freezes_modified=False,
        rank7_result=None, formal_oos_result=None, financial_results=None,
        cash_release_assumption='fixed delay after max(local official result receipt,resolved time); not measured redemption',
        terminal_policy='payouts due after input horizon remain locked receivables, not spendable cash')
    write(a.out/'registration.json',registration)
    write(a.out/'zero_delay_parity.json',zero_parity(a.input,base_rows,candidates))
    original_intents=intents(base_rows)
    for delay in DELAYS_MS:
        if sha(a.input)!=registration['input_sha256']:raise ValueError('input mutated')
        for name,expected in registration['code_sha256'].items():
            if sha(root/name)!=expected:raise ValueError('code mutated after registration')
        result=engine.replay_events(engine.read_jsonl(a.input),candidates,catalog.signal,config(delay))
        if intents(result['attempts'])!=original_intents:
            raise ValueError('cash stress changed original first signals or fixed limits')
        folder=a.out/str(delay);folder.mkdir()
        audit=audit_cash(result,delay)
        write(folder/'cash_audit.json',audit)
        gz_rows(folder/'attempts.jsonl.gz',result.pop('attempts'))
        gz_rows(folder/'cash_events.jsonl.gz',result.pop('cash_events'))
        result.update(registered_candidates=720,new_candidates=0,formal_oos_result=None,rank7_result=None)
        write(folder/'summary.json',result)
        print(json.dumps(dict(delay_ms=delay,completed_accounts=len(result['summary']),
          cash_audit='passed',registered_intents=len(original_intents))),flush=True)
    write(a.out/'completion.json',dict(schema='cash-release-development-completion-v1',
        input_sha256=sha(a.input),new_account_cells=4320,parity_cells_not_new_evidence=9,
        outputs_sha256={str(f.relative_to(a.out)):sha(f) for f in a.out.rglob('*') if f.is_file()},
        all_scenarios_complete=True,rank7_result=None,formal_oos_result=None))


if __name__=='__main__':main()
