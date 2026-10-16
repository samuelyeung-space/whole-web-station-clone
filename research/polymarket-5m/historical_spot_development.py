"""Explicit new development lane for audited old multiplexed strict captures.

Does not edit original manifests, frozen validators or old confirmation rules.
Uses existing causal Book/adapt_events and existing catalog/replay, with first
raw Gamma receipts and independently validated spot projection. CLOB stale
updates are rejected by the existing per-token Book guard, never reordered.
"""
import argparse,csv,gzip,hashlib,heapq,json,math,time
from collections import Counter
from pathlib import Path
import h_replay_archive as archive
import mine500_adapter as adapter
import mine500_catalog as catalog
import mine500_replay_v2 as replay

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def normalize(parent,projection,stream_audit,out):
    parent,projection,out=map(Path,(parent,projection,out));root=parent/'bundle-btc/strict'
    evidence=json.loads(Path(stream_audit).read_text());manifest=json.loads((root/'manifest.json').read_text())
    projected=json.loads((projection/'manifest.json').read_text())
    if 'clob' in evidence['stream_validation_errors'] or any(k.startswith('clob_') for k in evidence['count_mismatches']):
        raise ValueError('CLOB contract or counts failed')
    if evidence['run_id']!=manifest['run_id'] or not manifest['recorder_complete'] or manifest['missing_resolved_market_ids']:
        raise ValueError('recording identity or completeness mismatch')
    for name in ('manifest.json','clob_events.jsonl.gz','market_registry.csv.gz','market_outcomes.csv.gz'):
        if sha(root/name)!=evidence['input_sha256'][name]:raise ValueError('audited input drift: '+name)
    if sha(projection/'source_events.jsonl.gz')!=projected['output_sha256']:
        raise ValueError('spot projection drift')
    if projected['parent_sha256']!=evidence['input_sha256']['source_events.jsonl.gz']:
        raise ValueError('spot projection has different parent')
    if evidence['validator_sha256']!=sha(archive.__file__) or projected['validator_sha256']!=sha(archive.__file__):
        raise ValueError('validator identity drift')
    first={};constraint_events=[];raw_hashes={};fees={}
    for path in sorted((parent/'raw').glob('*/markets.jsonl.gz')):
        raw_hashes[path.relative_to(parent).as_posix()]=sha(path)
        for line in gzip.open(path,'rt'):
            row=json.loads(line)
            if not row.get('slug','').startswith('btc-updown-5m-'):continue
            now=row.get('_recorded_at_ms')
            if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('raw receipt missing')
            mid=row['conditionId'];fee=row.get('feeSchedule')
            if not fee or fee.get('exponent')!=1 or fee.get('takerOnly') is not True:raise ValueError('unsupported fee')
            rate=float(fee['rate'])
            if mid in fees and rate!=fees[mid]:raise ValueError('time-varying fee requires separate adapter')
            fees[mid]=rate
            if mid not in first or now<first[mid]['_recorded_at_ms']:first[mid]=row
            constraint_events.append({'type':'constraint','recv_ms':now,'market_id':mid,'constraints_source':'gamma',
                'min_order_size':float(row['orderMinSize']),'up_tick_size':float(row['orderPriceMinTickSize']),
                'down_tick_size':float(row['orderPriceMinTickSize'])})
    markets=[]
    for m in archive.iter_market_mappings(root/'market_registry.csv.gz'):
        r=first[m['market_id']];outs=r['outcomes'];ids=r['clobTokenIds']
        outs=json.loads(outs) if isinstance(outs,str) else outs
        ids=json.loads(ids) if isinstance(ids,str) else ids
        if any(str(ids[outs.index(s.capitalize())])!=str(m[s+'_token_id']) for s in ('up','down')):
            raise ValueError('direct mapping mismatch')
        start=int(r['slug'].rsplit('-',1)[1])*1000
        if start!=m['slot']*1000:raise ValueError('slot mismatch')
        markets.append({'type':'market','market_id':m['market_id'],'slug':r['slug'],
            'recv_ms':r['_recorded_at_ms'],'start_ms':start,'end_ms':start+300000,
            'up_token_id':m['up_token_id'],'down_token_id':m['down_token_id'],
            'fee_rate':fees[m['market_id']],'fee_provenance':'first_raw_gamma_feeSchedule','label_source':'gamma'})
    mids={r['market_id'] for r in markets};outcomes=[]
    for r in archive.iter_outcomes(root/'market_outcomes.csv.gz'):
        if r['market_id'] not in mids or r['resolution_source']!='gamma' or r['recv_ms'] is None:
            raise ValueError('missing outcome provenance/receipt')
        outcomes.append({'type':'outcome','market_id':r['market_id'],'recv_ms':r['recv_ms'],
                         'resolved_ms':r['source_ts_ms'],'winner':r['winner'],'source':'gamma'})
    start,end=manifest['started_ms'],manifest['ended_ms']
    header={'type':'dataset','recv_ms':min(start,*(r['recv_ms'] for r in markets)),
        'dataset_id':'historical-spot-development:'+manifest['run_id'],'split':'development',
        'provenance':{'parent_manifest_sha256':sha(root/'manifest.json'),'spot_projection':projected,
            'raw_metadata_sha256':raw_hashes,'adapter_code_sha256':sha(__file__),
            'base_adapter_sha256':sha(adapter.__file__)},
        'coverage_start_ms':start,'coverage_end_ms':end,'observed_hours':(end-start)/3600000,
        'decision_grid_ms':1000,'supported_latencies_ms':[500,600,1000],
        'book_resolution':'decision_and_match_projection','original_manifest_complete':manifest['complete'],
        'limitations':['new development lane; no original freeze inheritance','displayed-depth upper bound, not actual fills',
            'aggressor direction and official TWAP tails absent','Gamma availability is not actual spendable cash; no cash reuse needed at five shares and <=56 markets',
            'min-notional assumption zero; observed minimum shares and tick required',
            'per-token stale updates rejected; non-atomic deltas can understate capacity']}
    source=archive.iter_normalized_events(projection/'source_events.jsonl.gz',family='source')
    clob=archive.iter_normalized_events(root/'clob_events.jsonl.gz',family='clob')
    converted=adapter.adapt_events(heapq.merge(source,clob,key=lambda r:r['recv_ms']),markets,
        start_ms=start,end_ms=end,latency_projection=True)
    small=sorted(markets+constraint_events+outcomes,key=lambda r:(r['recv_ms'],r['type']=='outcome'))
    counts=Counter();features=Counter();out.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(out,'wt',compresslevel=1) as f:
        f.write(json.dumps(header,separators=(',',':'))+'\n')
        for r in heapq.merge(small,converted,key=lambda r:r['recv_ms']):
            counts[r['type']]+=1
            if r['type']=='decision':features.update(k for k,v in r['features'].items() if v is not None)
            f.write(json.dumps(r,separators=(',',':'),allow_nan=False)+'\n')
    return {'sha256':sha(out),'counts':dict(counts),'nonmissing_features':dict(features),'header':header}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('parent','projection','stream-audit','out'):p.add_argument('--'+k,required=True)
    a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    candidates=catalog.make_candidates()
    registration={'purpose':'New consumed historical development replay; no model fitting or rule changes',
        'candidates':candidates,'catalog_sha256':sha(catalog.__file__),'replay_sha256':sha(replay.__file__),
        'shares':5,'latencies_ms':[500,600,1000],'initial_cash_per_scenario':1000,'formal_oos_result':None}
    (out/'registration.json').write_text(json.dumps(registration,indent=2)+'\n')
    started=time.perf_counter();normalized=normalize(a.parent,a.projection,a.stream_audit,out/'normalized.jsonl.gz')
    normalized['elapsed_seconds']=time.perf_counter()-started
    (out/'normalization.json').write_text(json.dumps(normalized,indent=2)+'\n')
    print('normalized',normalized['counts'],flush=True)
    started=time.perf_counter()
    result=replay.replay_events(replay.read_jsonl(out/'normalized.jsonl.gz'),candidates,catalog.signal,
        replay.ReplayConfig(shares=5,initial_cash_usd=1000,require_constraints=True))
    with gzip.open(out/'attempts.jsonl.gz','wt',compresslevel=1) as f:
        for r in result.pop('attempts'):f.write(json.dumps(r)+'\n')
    result['replay_elapsed_seconds']=time.perf_counter()-started
    result['formal_oos_result']=None
    (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print('completed',len(result['summary']),'scenarios',flush=True)
