"""Independent arithmetic of published diagnostics; never a trade backtest.

Public trade rows do not prove parent orders, rounding fragments, credited
rewards or controllable executions. Cross-box omnibus rejection flags cannot
identify the cause without the omitted underlying boolean/provenance fields.
"""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal as D
import gzip
import hashlib
import json
from pathlib import Path


def digest(path):
    with open(path, 'rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    with gzip.open(path, 'rt') as f: return list(csv.DictReader(f))


def audit(dust_path, box_path):
    dust, box = read(dust_path), read(box_path)
    sums = defaultdict(D); wallets = defaultdict(D); discrepancies = []
    for row in dust:
        p, q, outcome = (D(row[k]) for k in ('price','size','settlement_value'))
        if not (D(0)<p<D(1) and q>0 and outcome in (D(0),D(1))):
            raise ValueError('invalid price, size or binary settlement')
        if row['side'] not in ('BUY','SELL'): raise ValueError('invalid side')
        values = dict(raw_fee=q*D('.07')*p*(1-p),
                      nominal_weighted_volume=q*D('2.3')*p*(1-p),
                      settlement_pnl=q*(outcome-p)*(1 if row['side']=='BUY' else -1))
        for key, value in values.items():
            if value != D(row[key]): discrepancies.append([row['public_row_id'],key])
            sums[key] += value
        if values['raw_fee'] >= D('.00001'): raise ValueError('not a below-quantum candidate')
        sums['notional'] += p*q
        wallets[row['proxy_wallet']] += values['nominal_weighted_volume']
    identities = Counter(r['public_row_id'] for r in dust)
    if any(v != 1 for v in identities.values()): raise ValueError('duplicate public row identity')
    pnl_rows = [r for r in box if r.get('pnl') not in ('',None)]
    return dict(
        inputs={str(p):digest(p) for p in (dust_path,box_path)},
        evidence_class='published_public_diagnostics_not_execution_or_OOS',
        reward_dust=dict(rows=len(dust), unique_transactions=len({r['transaction_hash'] for r in dust}),
            markets=len({r['market_id'] for r in dust}),
            sides=dict(Counter(r['side'] for r in dust)),
            sums={k:str(v) for k,v in sums.items()}, arithmetic_discrepancies=discrepancies,
            max_wallet_wv_fraction=str(max(wallets.values())/sums['nominal_weighted_volume']),
            linear_30day_nominal_wv=str(30*sums['nominal_weighted_volume']),
            bronze_target=2000, linear_30day_bronze_fraction=str(30*sums['nominal_weighted_volume']/D(2000)),
            limitation='Only 160 prefiltered rows supplied: cannot independently verify full 318806-row scan or 288-market coverage. Public rows may aggregate independently rounded fragments; no true capacity bound or actual credited rewards.'),
        cross_box=dict(rows=len(box), statuses=dict(Counter(r.get('status') for r in box)),
            pairs=len({r['pair_id'] for r in box}),
            days=sorted({r['day'] for r in box}), rows_with_pnl=len(pnl_rows),
            formal_pnl=None,
            missing_cause_fields=[k for k in ('settlement_conflict','resolution_source_conflict',
                'open_boundary_fallback_used','close_boundary_fallback_used',
                'resolution_usable_for_backtest','resolution_consistency_check') if box and k not in box[0]],
            limitation='Omnibus settlement_authority_conflict includes fallback, unusable, inconsistent and source-conflict gates. CSV omits triggering values, so exact cause and data repair cannot be established from these rows. Do not bypass the gate.'),
        additional_strategy_trials=0, confirmed_strategies=0, formal_oos_result=None)


if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--dust',required=True);ap.add_argument('--box',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args()
    Path(args.out).write_text(json.dumps(audit(args.dust,args.box),indent=2,ensure_ascii=False)+'\n')
