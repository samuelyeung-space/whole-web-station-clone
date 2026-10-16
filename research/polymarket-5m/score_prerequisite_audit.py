"""Locate saved-JPR score coverage loss without fitting or reading outcomes.

Reconstruct only the pinned upstream feature-admission state. Compare the full
eligible row-key set with the unchanged upstream features() function. Missing
scores are not imputed and quote-age limits are never relaxed.
"""
import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import heapq
import json
import math
from pathlib import Path

from eco_input_audit import number, sha

UPSTREAM_SHA256 = '02f155469f7387dbe8dfb947f16e34f3b03414bf16aa455d5d2c23dc95a5aa09'
NEED = ('mid_up', 'tau_s', 'ep_r_5s', 'ep_r_15s', 'ep_r_60s', 'ep_r_30s', 'imbalance_up')


def read_events(path):
    header = False
    with gzip.open(path, 'rt') as stream:
        for line in stream:
            event = json.loads(line)
            kind = event['type']
            if kind == 'dataset':
                if header or event.get('split') != 'development':
                    raise ValueError('one development header required')
                header = True
            elif not header:
                raise ValueError('event before dataset header')
            elif kind != 'outcome':
                # Outcome payload never reaches either feature-admission path.
                yield event
    if not header:
        raise ValueError('missing development header')


def merged(paths):
    yield from heapq.merge(*(read_events(path) for path in paths), key=lambda e:e['recv_ms'])


def prerequisites(event, book):
    t = event['recv_ms']; f = event['features']; av = event['feature_available_ms']
    bad = []
    for name in NEED:
        if not number(f.get(name)):
            bad.append('feature:' + name + ':missing')
        elif not number(av.get(name)) or av[name] > t:
            bad.append('feature:' + name + ':unavailable')
    if not book:
        bad.append('book:absent')
        return bad
    if book.get('recv_ms', t+1) > t:
        bad.append('book:future_event')
    for key in ('up_asks', 'up_bids', 'down_asks'):
        if not book.get(key):
            bad.append('book:' + key + ':empty')
    if not book.get('healthy'):
        bad.append('book:unhealthy')
    if not number(book.get('fee_rate')):
        bad.append('book:fee_unavailable')
    for side in ('up', 'down'):
        receipt = book.get(side + '_recv_ms')
        if not number(receipt):
            bad.append('book:' + side + ':receipt_missing')
        elif receipt > t:
            bad.append('book:' + side + ':future_receipt')
        elif t-receipt > 250:
            bad.append('book:' + side + ':quote_older_than250ms')
    return bad


class Trace:
    def __init__(self):
        self.books = {}; self.previous_eligible = {}
        self.eligible_keys = set(); self.seen_keys = set()
        self.by_day = defaultdict(Counter); self.rows = []

    def feed(self, event):
        if event['type'] == 'book':
            self.books[event['market_id']] = event
            return
        if event['type'] != 'decision':
            return
        t = event['recv_ms']; mid = event['market_id']; key = (mid,t)
        if key in self.seen_keys:
            raise ValueError('duplicate decision key; audit requires deduplicated input')
        self.seen_keys.add(key)
        day = datetime.fromtimestamp(t/1000, timezone.utc).date().isoformat()
        c = self.by_day[day]; c['decisions'] += 1
        f = event['features']; av = event['feature_available_ms']
        score = number(f.get('book_5')) and number(av.get('book_5')) and av['book_5'] <= t
        c['saved_score_rows' if score else 'no_saved_score_rows'] += 1
        bad = prerequisites(event, self.books.get(mid))
        for reason in bad:
            c['overlapping_reason|' + reason] += 1
        if bad:
            # Same first-failure ordering as upstream: source features, book
            # shape/health/fee, token receipt age. Detailed reasons overlap.
            primary = ('source_features_unavailable' if any(x.startswith('feature:') for x in bad)
                else 'book_shape_health_or_fee' if any(x in bad for x in
                    ['book:absent','book:future_event','book:up_asks:empty','book:up_bids:empty',
                     'book:down_asks:empty','book:unhealthy','book:fee_unavailable'])
                else 'token_quote_receipt_unavailable_or_older_than250ms')
            c['primary|' + primary] += 1
            if score:
                c['saved_score_without_current_upstream_row'] += 1
            return
        c['current_prerequisites_pass'] += 1
        old = self.previous_eligible.get(mid)
        self.previous_eligible[mid] = t
        if old is None or not 0 < (t-old)/1000 <= 2:
            c['primary|upstream_derivative_history_initialization'] += 1
            if score:
                c['saved_score_without_current_upstream_row'] += 1
            return
        self.eligible_keys.add(key)
        c['upstream_vector_rows'] += 1
        if score:
            c['primary|vector_with_saved_score'] += 1
        else:
            c['primary|vector_without_saved_score'] += 1


def reference_keys(paths, upstream):
    if sha(upstream) != UPSTREAM_SHA256:
        raise ValueError('pinned upstream source changed')
    parsed = ast.parse(Path(upstream).read_text())
    nodes = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in ('finite','features')]
    if {n.name for n in nodes} != {'finite','features'}:
        raise ValueError('missing pinned reference functions')
    # Execute unchanged feature functions only; no sklearn import, main(),
    # training, prediction, replay or outcome stream is evaluated.
    namespace = {'math':math, 'events':merged}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(upstream),'exec'),namespace)
    rows, labels, _ = namespace['features'](paths)
    if labels:
        raise ValueError('outcome leaked into reference audit')
    return {(r['market'],r['t']) for r in rows}


def audit(registration, input_root, upstream, output):
    reg = json.loads(Path(registration).read_text())
    if reg.get('split') != 'already_consumed_development':
        raise ValueError('consumed development registration required')
    paths = []; sources = []
    for item in reg['inputs']:
        path = Path(input_root)/Path(item['output']).name
        if sha(path) != item['output_sha256']:
            raise ValueError('input hash mismatch')
        paths.append(path); sources.append({'name':path.name,'sha256':item['output_sha256']})
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate inputs')
    trace = Trace()
    for event in merged(paths):
        trace.feed(event)
    expected = reference_keys(paths,upstream)
    missing = expected-trace.eligible_keys; extra = trace.eligible_keys-expected
    if missing or extra:
        raise ValueError(f'upstream differential parity failed: missing={len(missing)} extra={len(extra)}')
    totals = Counter()
    for counts in trace.by_day.values():
        assert sum(v for k,v in counts.items() if k.startswith('primary|')) == counts['decisions']
        totals.update(counts)
    result = {'schema':'saved-score-prerequisite-audit-v1', 'registration_sha256':sha(registration),
        'source_tapes':sources,'code_sha256':sha(__file__), 'shared_helper_sha256':sha(Path(__file__).with_name('eco_input_audit.py')),
        'upstream_sha256':sha(upstream), 'upstream_differential_row_keys':len(expected),
        'upstream_key_mismatches':0,'totals':dict(totals),
        'by_decision_receipt_utc_date':{d:dict(c) for d,c in sorted(trace.by_day.items())},
        'new_candidates':0,'new_backtests':0,'pnl':None,'formal_oos_result':None,
        'boundaries':['Quote age250ms is the unchanged input freshness gate, NOT venue taker delay.',
            'Vector eligibility is not model training eligibility, an order, or a proven fill.',
            'First observed development date has no earlier-date training rows by construction.',
            'Detailed reasons overlap; primary categories partition all decisions.',
            'No raw recorder-health inference, no score imputation, no freeze or OOS change.']}
    Path(output).write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n')
    print(json.dumps({'decisions':totals['decisions'],'upstream_keys':len(expected),'parity_mismatches':0,'new_backtests':0}),flush=True)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('registration','input-root','upstream','out'):
        p.add_argument('--'+name,required=True)
    a=p.parse_args();audit(a.registration,a.input_root,a.upstream,a.out)
