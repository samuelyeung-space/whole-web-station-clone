"""Development result admission: unknown decision inputs cannot imply completion.

The original replay output is retained under raw_development_diagnostics.
This wrapper does not repair input, replace fills or promote results into OOS.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import usdm_flow_bundle_replay as bundle

VERSION = 'usdm-flow-checked-development-v1'


def audit_result(result):
    if result.get('evidence_class') not in ('synthetic_fixture','consumed_development'):
        raise ValueError('development evidence class required')
    rows = []
    for scenario, cell in result['scenarios'].items():
        for original in cell['summary']:
            row = copy.deepcopy(original)
            reasons = row.get('invalid_decision_reasons')
            if not isinstance(reasons, dict) or any(type(n) is not int or n < 0 for n in reasons.values()):
                raise ValueError('explicit nonnegative invalid-decision counters required')
            invalid = sum(reasons.values())
            row['scenario'] = scenario
            row['engine_status_before_admission'] = row['status']
            row['invalid_decisions_before_market_consumed'] = invalid
            if invalid:
                # Conservative input completeness, never a winner-dependent rule.
                row['status'] = 'incomplete_invalid_decision_inputs'
                row['net_pnl_usd'] = None
                row['net_pnl_per_filled_share_usd'] = None
            row['complete_development_simulation'] = row['status'] == 'simulated_complete'
            # Known partial-ledger figures remain only in the nested raw diagnostic.
            for key in ('realized_pnl_usd','ending_cash_usd','min_available_cash_usd',
                        'ending_reserved_usd','max_realized_drawdown_usd'):
                row.pop(key, None)
            rows.append(row)
    return dict(version=VERSION, evidence_class=result['evidence_class'],
        admitted_development_summary=rows,
        raw_development_diagnostics=copy.deepcopy(result),
        input_completeness_truth_independently_verified=False,
        main_ranking_admitted=False, rank7_pnl_usd=None, formal_oos_result=None,
        interpretation='Nonzero pre-consumption invalid-decision counters veto complete PnL for that candidate/scenario/latency. Passing this guard proves neither complete recording coverage nor true fills.')


def checked_replay(manifest_path, *, expected_manifest_sha256):
    return audit_result(bundle.replay_bundle(manifest_path,
        expected_manifest_sha256=expected_manifest_sha256))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest');p.add_argument('--sha256',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();result=checked_replay(a.manifest,expected_manifest_sha256=a.sha256)
    result['checked_entry_file_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    Path(a.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(output=a.output,rank7_pnl_usd=None,formal_oos_result=None)))

if __name__=='__main__':main()
