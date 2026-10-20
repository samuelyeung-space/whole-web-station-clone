"""Re-derive rank7 per-market presence from the pinned consumed-input cache.

No replay, strategy signals, fitting, prices in output, OOS or feed-health
certification. The caller remains responsible for truthful consumed-data
classification and original source/official-result provenance.
"""
from pathlib import Path
import hashlib
import json


def verify_window(cache_dir, *, start_ms, end_ms, market_ids, cache_key, events_sha256,
                  adapter_code_sha256):
    import replay_dataset
    from replay_dataset import verify_cache, _scan, _coverage
    day=86400000
    if (type(start_ms) is not int or type(end_ms) is not int or start_ms<0
            or start_ms%day or end_ms-start_ms!=7*day):
        raise ValueError('rank7 exact seven UTC days required')
    if (not isinstance(market_ids,list) or len(market_ids)!=2016
            or any(not isinstance(mid,str) or not mid for mid in market_ids)
            or len(set(market_ids))!=2016):
        raise ValueError('rank7 requires 2016 unique pinned market IDs')
    root=Path(cache_dir).resolve()
    adapter=Path(replay_dataset.__file__).read_bytes()
    if hashlib.sha256(adapter).hexdigest()!=adapter_code_sha256:
        raise ValueError('rank7 input adapter code differs from frozen execution identity')
    before=verify_cache(root)
    if before['dataset_key']!=cache_key or before['tape_sha256']!=events_sha256:
        raise ValueError('rank7 input cache/event identity mismatch')
    if before.get('split')!='development':
        raise ValueError('rank7 input check requires explicitly consumed development cache')
    scan=_scan(root/'events.jsonl.gz')
    if scan['event_sha256']!=before['tape_event_sha256']:
        raise ValueError('rank7 canonical input event digest mismatch')
    actual=_coverage([scan],scan['intervals'])
    markets={mid:definition for mid,definition in scan['markets'].items()
             if start_ms<=definition[0]<end_ms}
    pinned=set(market_ids)
    if set(markets)!=pinned:
        raise ValueError('rank7 actual input market universe mismatch')
    missing={kind:sorted(pinned-scan['market_ids'][kind])
             for kind in ('book','decision','outcome')}
    if any(missing.values()):
        counts={k:len(v) for k,v in missing.items() if v}
        raise ValueError('rank7 per-market input evidence missing: '+json.dumps(counts,sort_keys=True))
    records=[]
    for mid,definition in sorted(markets.items()):
        records.append(dict(market_id=mid,start_ms=definition[0],end_ms=definition[1],
                            label_source=definition[2],winner=scan['outcomes'][mid]))
    after=verify_cache(root)
    if after!=before:
        raise ValueError('rank7 cache changed during input verification')
    return dict(schema='rank7-pinned-input-window-v1',cache_key=cache_key,
                events_sha256=events_sha256,verified_market_count=len(records),
                per_market_presence_sha256=hashlib.sha256(json.dumps(records,sort_keys=True,
                    separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                actual_coverage=actual,all_pinned_markets_have_books_decisions_official_outcomes=True,
                source_truth_independently_verified=False,feed_health_certified=False,
                input_adapter_code_sha256=adapter_code_sha256,
                engineering_check_not_financial_replay=True,new_financial_replays=0,
                formal_oos_result=None)
