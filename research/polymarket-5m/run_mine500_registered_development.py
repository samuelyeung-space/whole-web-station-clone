"""Replay an unchanged 720-rule catalog on new consumed development inputs.

Independent USD1000/100-share scenarios. No training, parameter selection,
seven-day admission, OOS claim, network, deployment or live order path.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import mine500_catalog as catalog
import mine500_replay_v2 as engine


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    registered = json.loads(args.registration.read_text())
    candidates = catalog.make_candidates() + catalog.make_supplementary_candidates()
    if len(candidates) != 720 or candidates != registered['candidates']:
        raise ValueError('unchanged original+supplement catalog required')
    for name, expected in registered['code_sha256'].items():
        if sha(root / name) != expected:
            raise ValueError('code drift: ' + name)
    # The input header is observed before any signal evaluation. The existing
    # execution engine also rejects claimed split/model hash inconsistencies.
    header = next(engine.read_jsonl(args.input))
    if header.get('type') != 'dataset' or header.get('split') != 'development':
        raise ValueError('consumed development input required')
    scenarios = [('base', 1., 0.), ('half_depth', .5, 0.), ('plus_1cent', 1., .01)]
    configs = [engine.ReplayConfig(latencies_ms=(500, 600, 1000), shares=100.,
               initial_cash_usd=1000., max_quote_age_ms=250, require_constraints=True,
               min_notional_usd=1., depth_fraction=depth,
               extra_cost_per_filled_share_usd=cost) for _, depth, cost in scenarios]
    expected = [dict(scenario=name, latencies_ms=list(c.latencies_ms), initial_cash_usd=1000,
                target_shares=100, cash_reset=False, require_constraints=True,
                depth_fraction=c.depth_fraction,
                extra_cost_per_filled_share_usd=c.extra_cost_per_filled_share_usd,
                max_quote_age_ms=250, min_notional_usd=1.) for (name, _, _), c in zip(scenarios, configs)]
    if expected != registered['scenarios']:
        raise ValueError('execution scenarios differ from prior registration')
    args.out.mkdir(parents=True, exist_ok=False)
    input_hash = sha(args.input)
    identity = dict(registered_at_utc=datetime.now(timezone.utc).isoformat(),
                    earlier_registration_sha256=sha(args.registration), input_sha256=input_hash,
                    input_header=header, worker_sha256=sha(__file__),
                    configs=[asdict(c) for c in configs], financial_results=None,
                    classification='partial_day_development', rank7_result=None, formal_oos_result=None)
    write(args.out / 'execution_registration.json', identity)
    for (name, _, _), config in zip(scenarios, configs):
        if sha(args.input) != input_hash:
            raise ValueError('input changed after registration')
        result = engine.replay_events(engine.read_jsonl(args.input), candidates, catalog.signal, config)
        if result['split_validation']['effective_split'] != 'development':
            raise ValueError('unexpected evidence classification')
        folder = args.out / name
        folder.mkdir()
        with (folder / 'attempts.jsonl').open('x') as f:
            for row in result.pop('attempts'):
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        result.update(candidate_count=720, new_candidates=0,
                      signal_code_sha256_actual=sha(root / 'mine500_catalog.py'),
                      seven_day_result=None, formal_oos_result=None)
        write(folder / 'summary.json', result)
        print(json.dumps(dict(scenario=name, completed_cells=len(result['summary']))), flush=True)
    if sha(args.input) != input_hash:
        raise ValueError('input changed during execution')
    write(args.out / 'completion.json', dict(input_sha256=input_hash,
          outputs_sha256={str(f.relative_to(args.out)): sha(f) for f in args.out.rglob('*') if f.is_file()},
          classification='partial_day_development', new_candidates=0,
          registered_candidate_scenarios=6480, rank7_result=None, formal_oos_result=None))


if __name__ == '__main__':
    main()
