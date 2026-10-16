"""Audit legacy sampled depth against receipt-causal event-level Up L1.

Offline diagnostic only, never a strategy or fill simulation. Retrospective
token mapping is used solely for evidence comparison, not decision admission.
No equal-time tie order, Down complement, fee clock or settlement clock imputed.
"""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import gzip
import json
import math
from pathlib import Path
from legacy_recording_cache import sha

VERSION = 'legacy-depth-admission-v1'

def num(value):
    if isinstance(value, bool): raise ValueError('boolean numeric field')
    value = float(value)
    if not math.isfinite(value): raise ValueError('nonfinite numeric field')
    return value

def top(payload, csv=False):
    result = []
    for side in ('bids', 'asks'):
        raw = json.loads(payload[side+'_json']) if csv else payload[side]
        levels = {}
        for row in raw:
            price, size = (row if csv else (row['price'], row['size']))
            price, size = num(price), num(size)
            if not 0 <= price <= 1 or size < 0: raise ValueError('invalid level')
            if price in levels: raise ValueError('duplicate price')
            if size: levels[price] = size
        if not levels: return None
        price = (max if side == 'bids' else min)(levels)
        result.extend([price, levels[price]])
    return tuple(result) if result[0] < result[2] else None

class Series:
    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r:r[0])
        self.times = [r[0] for r in self.rows]

    def before(self, now):
        i = bisect_left(self.times, now)-1
        if i < 0: return 'missing', None, None
        stamp = self.times[i]
        j = bisect_left(self.times, stamp)
        choices = {row[1] for row in self.rows[j:i+1]}
        if len(choices) != 1: return 'ambiguous', stamp, None
        value = self.rows[i][1]
        return ('invalid' if value is None else 'available'), stamp, value

def compare(snapshot, event, now):
    a, at, av = snapshot.before(now)
    b, bt, bv = event.before(now)
    out = Counter(queries=1)
    out['snapshot_'+a] += 1; out['event_'+b] += 1
    if a != 'available' or b != 'available': return out
    out['both_available'] += 1
    out['snapshot_older_than_250ms'] += now-at > 250
    out['event_older_than_250ms'] += now-bt > 250
    price_match = math.isclose(av[0],bv[0],abs_tol=1e-9,rel_tol=0) and math.isclose(av[2],bv[2],abs_tol=1e-9,rel_tol=0)
    size_match = math.isclose(av[1],bv[1],abs_tol=1e-6,rel_tol=0) and math.isclose(av[3],bv[3],abs_tol=1e-6,rel_tol=0)
    out['top_price_mismatch'] += not price_match
    out['top_price_or_size_mismatch'] += not (price_match and size_match)
    out['snapshot_ask_cheaper'] += av[2] < bv[2]-1e-9
    out['snapshot_ask_more_expensive'] += av[2] > bv[2]+1e-9
    out['same_ask_price_excess_size'] += math.isclose(av[2],bv[2],abs_tol=1e-9,rel_tol=0) and av[3] > bv[3]+1e-6
    return out

def audit(directory):
    root = Path(directory)
    manifest_path = root/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['schema'] != 'btc5m-legacy-lossless-cache-v1': raise ValueError('schema mismatch')
    selected = [s for s in manifest['streams'] if s['kind'] in ('market_metadata','token_snapshot','probability_observation')]
    for s in selected:
        p = root/s['cache_file']
        if p.resolve().parent != root.resolve() or sha(p) != s['sha256']: raise ValueError('stream integrity mismatch')
    def rows(kind):
        for s in selected:
            if s['kind'] == kind:
                with gzip.open(root/s['cache_file'],'rt') as f:
                    for line in f: yield json.loads(line)
    markets = {}; tokens = {}; metadata_missing = Counter()
    for e in rows('market_metadata'):
        row=e['raw']; raw=row['raw']; slug=row['slug']
        outcomes=raw['outcomes']; outcomes=json.loads(outcomes) if isinstance(outcomes,str) else outcomes
        ids=raw['clobTokenIds']; ids=json.loads(ids) if isinstance(ids,str) else ids
        if len(ids)!=2 or set(outcomes)!={'Up','Down'}: raise ValueError('invalid mapping')
        up=str(ids[outcomes.index('Up')])
        mapping=(up,num(row['start_sec'])*1000,num(row['end_sec'])*1000)
        if slug in markets and markets[slug]!=mapping: raise ValueError('conflicting mapping')
        if up in tokens and tokens[up]!=slug: raise ValueError('shared token')
        markets[slug]=mapping;tokens[up]=slug
        metadata_missing['mapping_receipt'] += e['recv_ms'] is None
        metadata_missing['settlement_known_ms'] += row.get('settlement_known_ms') is None
    snapshots=defaultdict(list);events=defaultdict(list);counts=Counter()
    for e in rows('token_snapshot'):
        counts['all_token_snapshot_rows'] += 1
        slug=tokens.get(str(e['raw']['asset_id']))
        if slug is None: continue
        if e['recv_ms'] is None: counts['unclocked_snapshots']+=1;continue
        snapshots[slug].append((num(e['recv_ms']),top(e['raw']['payload'])))
    for e in rows('probability_observation'):
        row=e['raw'];slug=row['market_id'];counts['event_up_l1_rows']+=1
        if slug not in markets: counts['unmapped_events']+=1;continue
        if e['recv_ms'] is None: counts['unclocked_events']+=1;continue
        events[slug].append((num(e['recv_ms']),top(row, csv=True)))
    summaries={str(ms):Counter() for ms in (500,600,1000)}
    per_market=[]
    for slug,(token,start,end) in sorted(markets.items()):
        if not snapshots[slug] or not events[slug]:
            counts['markets_without_both_streams']+=1;continue
        snap,evt=Series(snapshots[slug]),Series(events[slug])
        # Common observed span is a diagnostic query range, not a future-health
        # strategy filter. No query lies beyond the latest evidence in either file.
        low=max(start,snap.times[0],evt.times[0]); high=min(end,snap.times[-1],evt.times[-1])
        if low>=high: counts['markets_without_common_span']+=1;continue
        totals={str(ms):Counter() for ms in (500,600,1000)}
        for decision in range(math.ceil(low/1000)*1000,math.floor(high)+1,1000):
            for delay in (500,600,1000):
                now=decision+delay
                if now>=end or now>high:continue
                totals[str(delay)].update(compare(snap,evt,now))
        for delay,value in totals.items():summaries[delay].update(value)
        per_market.append(dict(market_id=slug,query_counts={d:v['queries'] for d,v in totals.items()}))
    return dict(schema=VERSION,code_sha256=sha(__file__),input_manifest_sha256=sha(manifest_path),
        input_archive_sha256=manifest['identity']['archive_sha256'],counts=dict(counts),
        retrospective_market_mappings=len(markets),metadata_missing=dict(metadata_missing),
        matched_market_count=len(per_market),latency_diagnostics={k:dict(v) for k,v in summaries.items()},
        per_market=per_market,strict_execution_admitted=False,simulation_pnl=None,formal_oos_result=None,
        semantics='All numeric counts are diagnostic queries, not signals, attempts or fills. Mapping is retrospective and not admitted to a decision tape. Both as-of queries use recv < query time. Equal-receipt conflicting states are ambiguous. Event stream has Up L1 only; no Down/deep reconstruction. Sparse snapshots cannot prove exact subsecond full-depth matching. No future-health strategy filtering performed.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.cache);Path(a.out).write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k!='per_market'}))
if __name__=='__main__': main()
