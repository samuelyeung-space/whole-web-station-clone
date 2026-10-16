"""Audit historical strict tapes without rewriting their admission flags.

Distinguish multiplexed venue-time regressions from per-token stale state.
No sorting, repaired timestamps, live actions, or strategy qualification.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip, hashlib, json, math, time
from pathlib import Path
import h_replay_archive as archive


class VenueClockAudit:
    def __init__(self):
        self.previous = None
        self.previous_token = None
        self.latest = {}
        self.counts = Counter()

    def consume(self, row):
        if row['kind'] in ('clob_connection','clob_error'):
            self.latest.clear()
            self.previous = self.previous_token = None
            return
        token = str(row['asset_id'])
        stamp = float(row['source_ts_ms'])
        self.counts['book_events'] += 1
        if self.previous is not None and stamp < self.previous:
            self.counts['adjacent_global_regressions'] += 1
            self.counts['global_regressions_cross_token' if token != self.previous_token
                        else 'global_regressions_same_token'] += 1
        if stamp < self.latest.get(token, float('-inf')):
            self.counts['per_token_stale_events'] += 1
            self.counts['stale_'+row['kind']] += 1
        else:
            self.latest[token] = stamp
        self.previous, self.previous_token = stamp, token


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def metadata_audit(paths, mappings):
    first={};rows=0;missing=0;hashes={}
    for path in sorted(paths):
        hashes[str(path)]=sha(path)
        with gzip.open(path,'rt') as f:
            for line in f:
                row=json.loads(line)
                if not row.get('slug','').startswith('btc-updown-5m-'):continue
                rows+=1;stamp=row.get('_recorded_at_ms')
                if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<=0:
                    missing+=1;continue
                key=row['conditionId']
                if key not in first or stamp<first[key]['_recorded_at_ms']:first[key]=row
    counts=Counter();fees=Counter();ticks=Counter();sizes=Counter()
    for mapping in mappings:
        key=mapping['market_id'];row=first.get(key);end=(mapping['slot']+300)*1000
        counts['registry_after_close']+=mapping['recv_ms'] is not None and mapping['recv_ms']>=end
        if row is None:counts['missing_raw_mapping']+=1;continue
        counts['first_raw_before_close']+=row['_recorded_at_ms']<end
        outcomes=row['outcomes'];tokens=row['clobTokenIds']
        outcomes=json.loads(outcomes) if isinstance(outcomes,str) else outcomes
        tokens=json.loads(tokens) if isinstance(tokens,str) else tokens
        counts['direct_mapping_matches']+=(tokens[outcomes.index('Up')]==mapping['up_token_id'] and
                                          tokens[outcomes.index('Down')]==mapping['down_token_id'])
        fees[json.dumps(row.get('feeSchedule'),sort_keys=True)]+=1
        ticks[str(row.get('orderPriceMinTickSize'))]+=1
        sizes[str(row.get('orderMinSize'))]+=1
    return {'raw_rows':rows,'unique_markets':len(first),'missing_receipt_rows':missing,
            'counts':dict(counts),'first_observed_fee_schedules':dict(fees),
            'first_observed_ticks':dict(ticks),'first_observed_min_order_sizes':dict(sizes),
            'raw_sha256':hashes,'availability_policy':'Use first received values only; never backdate later values'}


def audit(root):
    started=time.perf_counter();root=Path(root)
    executed_code_hash=sha(__file__)
    manifest=json.loads((root/'manifest.json').read_text())
    counts=Counter();clock=VenueClockAudit();errors={};fields={};hashes={}
    for family in ('source','clob'):
        path=root/(family+'_events.jsonl.gz');hashes[path.name]=sha(path)
        fields[family]=Counter()
        try:
            for row in archive.iter_normalized_events(path,family=family):
                counts[row['kind']]+=1
                if family=='clob':clock.consume(row)
                if row['kind']=='spot_trade':
                    for key in ('price','size','aggressor_side','buyer_is_maker','trade_id','source_ts_ms','recv_ms'):
                        fields[family][key]+=row.get(key) is not None
        except (ValueError, OSError, EOFError) as exc:
            errors[family]=str(exc)
    mappings=list(archive.iter_market_mappings(root/'market_registry.csv.gz'))
    outcomes=list(archive.iter_outcomes(root/'market_outcomes.csv.gz'))
    counts.update(markets=len(mappings),outcomes=len(outcomes))
    mismatches={k:{'observed':counts[k],'declared':manifest['counts'].get(k)}
                for k in set(counts)|set(manifest['counts'])
                if counts[k]!=manifest['counts'].get(k,0)}
    for name in ('manifest.json','market_registry.csv.gz','market_outcomes.csv.gz'):hashes[name]=sha(root/name)
    start,end=manifest['started_ms'],manifest['ended_ms']
    by_day=Counter(datetime.fromtimestamp(x['slot'],timezone.utc).date().isoformat() for x in mappings)
    return {'schema':'historical-stream-audit-v1','run_id':manifest['run_id'],
        'original_manifest':manifest,'original_manifest_complete_unchanged':manifest['complete'],
        'input_sha256':hashes,'validator_sha256':sha(archive.__file__),
        'audit_code_sha256':executed_code_hash,'stream_validation_errors':errors,
        'counts':dict(counts),'count_mismatches':mismatches,'venue_clock':dict(clock.counts),
        'source_field_counts':fields,'registered_market_slots_by_utc_day':dict(by_day),
        'observed_hours':(end-start)/3600000,
        'start_utc':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),
        'end_utc':datetime.fromtimestamp(end/1000,timezone.utc).isoformat(),
        'metadata_missing_receipt':sum(x['recv_ms'] is None for x in mappings),
        'raw_metadata_audit':metadata_audit(root.parent.parent.glob('raw/*/markets.jsonl.gz'),mappings),
        'outcome_missing_receipt':sum(x['recv_ms'] is None for x in outcomes),
        'event_contract_and_counts_pass':not errors and not mismatches,
        'new_strategy_replays':0,'strategy_pnl':None,'formal_oos_result':None,
        'elapsed_seconds':time.perf_counter()-started,
        'limitations':['Evidence audit only; not execution admission or a strategy backtest',
            'Per-token stale events require causal handling, never global resorting',
            'Registered slots are not complete or tradable slots',
            'Gamma observation time does not prove wallet cash spendability']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.root);Path(a.out).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ('original_manifest','input_sha256')},indent=2))
