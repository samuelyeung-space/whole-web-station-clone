"""Read-only quantity-semantics audit; never converts a budget replay to share-capped PnL."""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


def audit_rows(rows, tolerance=1e-5):
    groups = defaultdict(lambda: dict(sent=0, fills=0, nominal_target_shares=0.0,
        actual_filled_shares=0.0, signed_size_above_target=0,
        fills_above_target=0, fills_above_signed_size=0, max_signed_size=0.0,
        max_filled_shares=0.0, budget_excess_max_usd=0.0, pnl_usd=0.0))
    for row in rows:
        if not row.get('sent'):
            raise ValueError('only complete sent-order ledgers accepted')
        target, signed, budget, qty = [float(row[k]) for k in
            ('target_shares', 'signed_size', 'maker_usd', 'filled_shares')]
        limit = float(row['fixed_limit'])
        if not all(math.isfinite(x) and x >= 0 for x in (target,signed,budget,qty,limit)):
            raise ValueError('invalid quantity/budget')
        if target <= 0 or limit <= 0:
            raise ValueError('nonpositive target/limit')
        if not math.isclose(signed*limit,budget,abs_tol=tolerance,rel_tol=0):
            raise ValueError('signed notional does not match recorded maker budget')
        if bool(row['filled']) != (qty > 0):
            raise ValueError('fill flag differs from actual quantity')
        price = float(row['fill_price']) if qty else 0.0
        if not math.isfinite(price) or price < 0 or price > limit+tolerance:
            raise ValueError('fill price invalid or above limit')
        excess=qty*price-budget
        if excess > tolerance:
            raise ValueError('fill spending exceeds dollar budget tolerance')
        b=groups[str(int(float(row['evaluation_ms'])))]
        b['sent']+=1;b['fills']+=int(qty>0);b['nominal_target_shares']+=target
        b['actual_filled_shares']+=qty
        b['signed_size_above_target']+=int(signed>target+tolerance)
        b['fills_above_target']+=int(qty>target+tolerance)
        b['fills_above_signed_size']+=int(qty>signed+tolerance)
        b['max_signed_size']=max(b['max_signed_size'],signed)
        b['max_filled_shares']=max(b['max_filled_shares'],qty)
        b['budget_excess_max_usd']=max(b['budget_excess_max_usd'],excess)
        b['pnl_usd']+=float(row.get('pnl') or 0)
    return dict(schema='source-race-budget-quantity-audit-v1',by_latency=dict(groups),
        observed_dollar_budget_checks_passed=True,
        share_capped_quantity_admission=bool(groups) and all(
            b['signed_size_above_target']==b['fills_above_target']==0 for b in groups.values()),
        corrected_share_capped_pnl_usd=None,
        counterfactual_replay_executed=False,
        financial_qualification=False,rank7_pnl_usd=None,formal_oos_result=None)


def audit_file(path):
    p=Path(path)
    with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    with p.open() as f:result=audit_rows(json.loads(line) for line in f if line.strip())
    result['ledger_sha256']=digest
    result['ledger_bytes']=p.stat().st_size
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    output=Path(args.out)
    if output.exists():raise SystemExit('refuse to overwrite audit')
    output.write_text(json.dumps(audit_file(args.rows),indent=2,sort_keys=True)+'\n')
