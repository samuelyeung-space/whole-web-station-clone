"""Join published BTC5m rows to resolved Gamma snapshots, never to OHLC.

This is a label/recorded-fee audit, not an order-book or fill validation.
Post-resolution fee snapshots do not establish historical effective parameters.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path

TOLERANCE = 1e-6


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def near(actual, expected):
    if not math.isfinite(float(actual)) or not math.isclose(
            float(actual), float(expected), rel_tol=0, abs_tol=TOLERANCE):
        raise ValueError('published fee/cost arithmetic differs from Gamma parameters')


def won(value):
    if str(value) in ('True', '1', '1.0'): return True
    if str(value) in ('False', '0', '0.0'): return False
    raise ValueError('invalid recorded win flag')


def audit(published, gamma):
    markets = {}
    for g in gamma:
        slug = g['slug']
        if not slug.startswith('btc-updown-5m-'):
            raise ValueError('non-BTC5m Gamma input')
        slot = int(slug.rsplit('-', 1)[1])
        if slot % 300 or slot != g['start_sec'] or g['end_sec'] != slot + 300:
            raise ValueError('Gamma slot/slug mismatch')
        labels = decoded(g['raw']['outcomes'])
        prices = list(map(float, decoded(g['outcome_prices'])))
        if not g['resolved'] or set(labels) != {'Up', 'Down'} or len(labels) != 2 or sorted(prices) != [0., 1.]:
            raise ValueError('unresolved or ambiguous Gamma label')
        fee = g['raw']['feeSchedule']
        if fee['rate'] != .07 or fee['exponent'] != 1:
            raise ValueError('new fee schema/parameters require separate audit')
        if slug in markets:
            raise ValueError('duplicate Gamma market identity')
        markets[slug] = (labels[prices.index(1.)], float(fee['rate']))
    counts = {}; used = set()
    for name, file in published['files'].items():
        if name not in ('momentum5s.csv', 'theta30.csv', 'paper_ledger.csv', 'trades.csv'):
            continue
        if file['removed_identities'] or file['material_rewrites']:
            raise ValueError('historical row rewrite requires separate review')
        c = Counter()
        for r in file['new_rows']:
            slug = r.get('market_id', r.get('slug', ''))
            if not slug.startswith('btc-updown-5m-'):
                if name != 'trades.csv': raise ValueError('unexpected non-BTC published row')
                c['non_btc_rows_not_admitted'] += 1
                continue
            if slug not in markets:
                raise ValueError('published row has no matching official Gamma snapshot')
            used.add(slug); c['market_identity_rows'] += 1
            if 'filled' in r and r['filled'] not in ('True', 'False'):
                raise ValueError('invalid published fill flag')
            if r.get('filled', 'True') != 'True':
                c['unfilled_rows_no_fill_validation'] += 1
                continue
            if name == 'theta30.csv' and r['side_up'] not in ('True', 'False'):
                raise ValueError('invalid published side flag')
            side = ('Up' if r['side_up'] == 'True' else 'Down') if name == 'theta30.csv' else r['side']
            if side not in ('Up', 'Down'):
                raise ValueError('unknown published outcome side')
            winner, rate = markets[slug]
            if won(r['won']) != (side == winner):
                raise ValueError('published win flag differs from official Gamma outcome')
            c['official_win_flags_matched'] += 1
            if name == 'paper_ledger.csv':
                price = float(r['ask']); shares = float(r['shares'])
                if not 0 < price < 1 or not math.isfinite(shares) or shares <= 0:
                    raise ValueError('invalid binary price/quantity')
                near(r['fee'], shares * rate * price * (1-price))
                near(r['cost'], shares * price + float(r['fee']))
            else:
                price = float(r['fill_price'] if name == 'theta30.csv' else r['price'])
                if not 0 < price < 1: raise ValueError('invalid binary price')
                kind = r['kind'] if name == 'trades.csv' else 'taker'
                if kind not in ('maker', 'taker'): raise ValueError('unknown execution kind')
                near(r['fill_fee'] if name == 'theta30.csv' else r['fee'],
                     0 if kind == 'maker' else rate * price * (1-price))
            c['recorded_fee_arithmetic_matched_post_resolution_parameters'] += 1
        counts[name] = dict(c)
    if set(counts) != {'momentum5s.csv', 'theta30.csv', 'paper_ledger.csv', 'trades.csv'}:
        raise ValueError('missing required published row family')
    return {'schema':'published-forward-gamma-label-audit-v1',
            'classification':'official snapshot label and recorded fee arithmetic only',
            'gamma_markets':len(markets), 'used_unique_btc5m_markets':len(used),
            'counts':counts, 'fee_absolute_tolerance_usd':TOLERANCE,
            'fee_effective_at_decision_verified':False, 'actual_TWAP_samples_verified':False,
            'causal_book_or_real_fills_verified':False, 'reference_price_age_verified':False,
            'new_financial_replays':0, 'rank7_pnl_usd':None, 'formal_oos_result':None}


if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('published_audit',type=Path)
    ap.add_argument('gamma_gzip',type=Path);a=ap.parse_args()
    with gzip.open(a.gamma_gzip,'rt') as f: gamma=[json.loads(line) for line in f]
    result=audit(json.loads(a.published_audit.read_text()),gamma)
    result['inputs_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (a.published_audit,a.gamma_gzip)}
    result['code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result,indent=2,allow_nan=False))
