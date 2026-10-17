"""Registered hypothetical rejection stress on already consumed MFA inputs.

No new fitting, candidates, seed search, production calls or formal OOS.
The protocol and input/code identities are written before reading replay rows.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import replay_engine as ex
import minimal_feature_strategy as strategy
from cash_release_audit import reconcile


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def dump(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def ledger(path, rows):
    with gzip.open(path, 'wt') as f:
        for row in rows:
            f.write(json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n')
    with gzip.open(path, 'rt') as f:
        count = sum(1 for line in f if isinstance(json.loads(line), dict))
    if count != len(rows):
        raise ValueError('ledger count mismatch')
    return dict(rows=count, sha256=sha(path), gzip_eof_crc_verified=True)


def run(tape, expected_sha, baseline, out):
    tape, baseline, out = Path(tape), Path(baseline), Path(out)
    if sha(tape) != expected_sha:
        raise ValueError('input hash mismatch')
    out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).parent
    files = ['replay_engine.py', 'mine500_constraints.py', 'minimal_feature_strategy.py',
             'cash_release_audit.py', 'post_send_failure_study.py']
    code = {name: sha(root/name) for name in files}
    configs = [ex.ReplayConfig(shares=100, initial_cash_usd=1000,
                latencies_ms=(500, 600, 1000), require_constraints=True,
                post_send_reject_bps=bps, post_send_reject_seed='research-only-v1')
               for bps in (1000, 3000)]
    protocol = dict(schema='mfa-post-send-rejection-stress-v1',
        registered_at_utc=datetime.now(timezone.utc).isoformat(),
        evidence_class='already_consumed_development_hypothetical_execution_stress',
        input_sha256=expected_sha, baseline_metrics_sha256=sha(baseline), code_sha256=code,
        candidates=strategy.candidates(), configs=[asdict(c) for c in configs],
        expected_new_scenario_rows=48, new_candidates=0, new_independent_mechanisms=0,
        baseline_policy='reuse saved base metrics; never rerun or scale old PnL',
        rejection_basis='intent-keyed common SHA256 shock, conditional on valid match inputs; not measured rejection rates',
        receipt_proxy='rejection learned at match; collateral is not released earlier; real receipt delay unavailable',
        selection_policy='one seed, all eight prior MFA candidates, no outcome/fill/cash/latency in shock key',
        ranking_admitted=False, formal_oos_result=None)
    dump(out/'protocol.json', protocol)
    baseline_rows = json.loads(baseline.read_text())
    old = {(r['candidate_id'], r['latency_ms']): r for r in baseline_rows
           if r['scenario']=='shares100_base_release0'}
    if len(old)!=24:
        raise ValueError('24 frozen baseline rows required')
    metrics, receipts = [], {}
    for cfg in configs:
        bps=cfg.post_send_reject_bps
        def events():
            for index, row in enumerate(ex.read_jsonl(tape)):
                if index==0 and (row.get('type')!='dataset' or row.get('split')!='development'):
                    raise ValueError('development input header required')
                yield row
        result=ex.replay_events(events(), strategy.candidates(), strategy.signal, cfg)
        audit=reconcile(result['attempts'], result['cash_events'], result['summary'], 0)
        if sha(tape)!=expected_sha or any(sha(root/n)!=v for n,v in code.items()):
            raise ValueError('input/code mutated during run')
        receipts[str(bps)]={
            'attempts':ledger(out/f'attempts_{bps}.jsonl.gz',result.pop('attempts')),
            'cash_events':ledger(out/f'cash_events_{bps}.jsonl.gz',result.pop('cash_events')),
            'cash_audit':audit}
        for r in result['summary']:
            r['scenario']=f'base_post_send_reject_{bps}bps'
            r['post_send_reject_bps']=bps
            prior=old[(r['candidate_id'],r['latency_ms'])]
            r['saved_baseline_net_pnl_usd']=prior['net_pnl_usd']
            r['delta_from_saved_baseline_usd']=(r['net_pnl_usd']-prior['net_pnl_usd']
                       if r['net_pnl_usd'] is not None and prior['net_pnl_usd'] is not None else None)
            metrics.append(r)
        dump(out/f'replay_{bps}.json', result)
        print(json.dumps(dict(bps=bps,status='completed_development_stress',
                              rows=len(result['summary']))),flush=True)
    dump(out/'metrics.json',metrics)
    dump(out/'completion.json',dict(schema=protocol['schema'],status='completed_development_stress',
         new_scenario_rows=len(metrics),new_candidates=0,formal_oos_result=None,
         source_observed_hours=result['dataset'].get('observed_hours'),
         complete_utc_days=0,ranking_admitted=False,receipts=receipts))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('tape','expected-sha','baseline','out'):
        p.add_argument('--'+name, required=True)
    a=p.parse_args();run(a.tape,a.expected_sha,a.baseline,a.out)
