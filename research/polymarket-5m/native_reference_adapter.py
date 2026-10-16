"""Native CLOB + Chainlink historical sensitivity lane (offline only).

Preserves source identity; Chainlink returns are not Binance validation. Reduced
bundle snapshots have no venue clock: it stays missing. BBO contradictions and
recorded disconnects invalidate snapshots; raw frames use the existing Book
source high-watermark. All settlement availability is globally withheld.
"""
from __future__ import annotations
import argparse
from array import array
from collections import Counter,defaultdict
import csv,gzip,hashlib,io,json,math,tarfile,time,zipfile
from pathlib import Path
import numpy as np
from lake_parquet_adapter import Series,feature_row
from legacy_up_l1_adapter import market,number
from legacy_recording_cache import sha
from mine500_adapter import Book

VERSION='native-chainlink-historical-v1'

def members(path):
    with zipfile.ZipFile(path) as z:
        if z.namelist()!=['raw.tgz']:raise ValueError('unsupported ZIP layout')
        with z.open('raw.tgz') as raw:
            with tarfile.open(fileobj=raw,mode='r|gz') as t:
                for m in t:
                    if not m.isfile():continue
                    name=m.name;kind=None
                    if '/book/BTC-5m/' in name:kind='bundle_book'
                    elif '/best_bid_ask/BTC-5m/' in name:kind='bundle_bbo'
                    elif '/markets/BTC-5m/' in name:kind='bundle_market'
                    elif '/prices/BTCUSD/' in name:kind='bundle_reference'
                    elif name.startswith('raw/'):
                        base=Path(name).name.removesuffix('.gz').removesuffix('.jsonl')
                        if base in ('markets','clob','rtds','errors'):kind='raw_'+base
                    if kind is None:continue
                    f=t.extractfile(m)
                    if name.endswith('.gz'):f=gzip.GzipFile(fileobj=f)
                    # tar stream members do not implement seekable(), which
                    # TextIOWrapper probes even for purely sequential input.
                    text=(line.decode('utf-8') for line in f)
                    rows=csv.DictReader(text) if name.removesuffix('.gz').endswith('.csv') else (json.loads(x) for x in text if x.strip())
                    try:
                        for row in rows:yield kind,name,row
                    finally:f.close()
            while raw.read(1024*1024):pass

def raw_market(row):
    slug=row.get('slug','')
    if not slug.startswith('btc-updown-5m-'):return None
    slot=int(slug.rsplit('-',1)[1])
    return market({'slug':slug,'start_sec':slot,'end_sec':slot+300,'resolved':row.get('closed') is True,'raw':row})

def top(book):
    if not book.ready:return [math.nan]*4
    bid=max(book.bids);ask=min(book.asks)
    if not 0<bid<ask<1:return [math.nan]*4
    return [bid,book.bids[bid],ask,book.asks[ask]]

def read(path):
    counts=Counter();member_counts=Counter();meta={};rawmeta={};books=defaultdict(lambda:array('d'));bbo=defaultdict(lambda:array('d'))
    reference=array('d');rawreference=array('d');errors=[];rawbooks={};last_raw=-math.inf;last_token={};snapshots={};tick_events=[];tick_source={};bundle_seen=False;raw_seen=False
    def register(target,m):
        if m is None:return
        old=target.get(m['slug'])
        if old:
            for field in ('market_id','up_token_id','down_token_id','start_ms','end_ms','fee_rate','min_size'):
                if old[field]!=m[field]:raise ValueError('conflicting market metadata field '+field)
            if old['tick']!=m['tick']:
                counts['retrospective_tick_variation_not_backfilled']+=1
                m['tick']=old['tick']  # earliest metadata is an explicit initial assumption
            if old['winner'] and m['winner'] and old['winner']!=m['winner']:raise ValueError('conflicting official outcome')
            if not m['winner']:m['winner']=old['winner'];m['label_verified_by_recorded_gamma']=old['label_verified_by_recorded_gamma']
        target[m['slug']]=m
    for kind,name,r in members(path):
        counts[kind]+=1;member_counts[name]+=1
        if kind=='bundle_market':register(meta,market(r))
        elif kind=='raw_markets':
            # Bundle metadata is explicitly retrospective. Do not replace its
            # schema with unclocked raw metadata from another export stage.
            if not meta:register(rawmeta,raw_market(r))
        elif kind=='raw_errors':
            if r.get('where')=='clob' and r.get('at') is not None:errors.append(number(r['at']))
        elif kind=='bundle_book':
            bundle_seen=True;now=r.get('recv_ms')
            if now is None:counts['book_missing_receipt']+=1;continue
            now=number(now);p=r['payload'];token=str(r['asset_id'])
            # Historical recording.py copied local recv into event_ts_ms.
            # Reduced bundle has no native venue clock, even if this alias exists.
            src=math.nan
            book=Book();book.apply({'kind':'clob_snapshot','source_ts_ms':src if math.isfinite(src) else 0,'recv_ms':now,'bids':p['bids'],'asks':p['asks']})
            books[token].extend([now,*top(book),src,now])
        elif kind=='bundle_bbo':
            now=r.get('recv_ms')
            if now is None:counts['bbo_missing_receipt']+=1;continue
            p=r['payload'];src=None
            try:bid=number(p['best_bid']);ask=number(p['best_ask'])
            except (ValueError,TypeError,KeyError):bid=ask=math.nan;counts['invalid_bbo_rows']+=1
            bbo[str(r['asset_id'])].extend([number(now),bid,ask,number(src) if src is not None else math.nan])
        elif kind=='bundle_reference':
            if r.get('recv_ms') in (None,''):counts['reference_missing_receipt']+=1;continue
            reference.extend([number(r['recv_ms']),number(r['value']),number(r['feed_ts_ms'])])
        elif kind=='raw_rtds':
            msg=r.get('msg',{});p=msg.get('payload',{})
            if msg.get('topic')!='crypto_prices_chainlink' or p.get('symbol')!='btc/usd':continue
            if r.get('recv_ms') is None:counts['reference_missing_receipt']+=1;continue
            rawreference.extend([number(r['recv_ms']),number(p['value']),number(p['timestamp'])])
        elif kind=='raw_clob':
            raw_seen=True;now=r.get('recv_ms')
            if now is None:counts['book_missing_receipt']+=1;continue
            now=number(now)
            if now<last_raw:raise ValueError('raw CLOB receipt order regressed; sequence cannot be guessed')
            last_raw=now;frames=r.get('msg');frames=frames if isinstance(frames,list) else [frames]
            for p in frames:
                if not isinstance(p,dict):counts['nonbook_raw_frame']+=1;continue
                typ=p.get('event_type');src=p.get('timestamp')
                if src is None:counts['raw_book_missing_source_timestamp']+=1;continue
                src=number(src)
                if typ=='tick_size_change':
                    token=str(p['asset_id']);tick=number(p['new_tick_size'])
                    if not 0<tick<1:raise ValueError('invalid native tick size')
                    if src>=tick_source.get(token,-math.inf):
                        tick_source[token]=src;tick_events.append((now,token,tick,src));counts['native_tick_changes']+=1
                    else:counts['native_tick_regressions_rejected']+=1
                    continue
                changes=[]
                if 'bids' in p and 'asks' in p:
                    changes=[(str(p['asset_id']),{'kind':'clob_snapshot','bids':p['bids'],'asks':p['asks']})]
                elif typ=='price_change':
                    changes=[(str(c['asset_id']),{'kind':'clob_price_change','price':c['price'],'size':c['size'],'side':c['side']}) for c in p.get('price_changes',[])]
                touched=set()
                for token,change in changes:
                    book=rawbooks.setdefault(token,Book());event={**change,'recv_ms':now,'source_ts_ms':src}
                    if change['kind']=='clob_price_change' and (change['side'] not in ('BUY','SELL') or not 0<=number(change['price'])<=1 or number(change['size'])<0):raise ValueError('invalid raw CLOB delta')
                    if not book.apply(event):counts['raw_source_regressions_rejected']+=1;continue
                    if change['kind']=='clob_snapshot':snapshots[token]=now
                    touched.add(token)
                for token in touched:
                    book=rawbooks[token];state=top(book)
                    previous=last_token.get(token)
                    if previous and previous[0]==now and previous[1]!=state:
                        # Distinct same-receipt frames lack transport order.
                        # Require a later full snapshot before reconstructing.
                        book.ready=False;state=[math.nan]*4;counts['raw_equal_receipt_conflicts']+=1
                    last_token[token]=(now,state)
                    books[token].extend([now,*state,src,snapshots.get(token,math.nan)])
    if bundle_seen and raw_seen:raise ValueError('both reduced and raw CLOB present; explicit source selection required')
    if not meta:meta=rawmeta
    if not reference:reference=rawreference
    if not books or not meta or not reference:
        raise ValueError('required native CLOB/Gamma/Chainlink absent; field counts='+json.dumps(counts))
    return meta,books,bbo,reference,errors,tick_events,counts,member_counts,'reduced_bundle_snapshots' if bundle_seen else 'raw_clob_reconstruction'

def normalize(path,output,*,settlement_cutoff_ms,max_book_age_ms=250,max_source_age_ms=1000):
    started=time.perf_counter();path=Path(path);output=Path(output)
    if output.exists():raise ValueError('immutable output already exists')
    meta,raw,bboraw,reference,errors,tick_events,quality,member_counts,mode=read(path)
    source_checks=Counter();book_checks=Counter();bbo_checks=Counter();series={};bbos={};starts=[];ends=[]
    a=np.frombuffer(reference,dtype=float).reshape(-1,3)
    if np.any(a[:,1]<=0):raise ValueError('invalid Chainlink price')
    spot=Series(a[:,0]*1000,np.column_stack([a[:,1],a[:,2]*1000]),source_column=1,counts=source_checks)
    tokens={str(m[s+'_token_id']) for m in meta.values() for s in ('up','down')}
    for token,buf in raw.items():
        if token not in tokens:quality['unmapped_book_rows']+=len(buf)//7;continue
        a=np.frombuffer(buf,dtype=float).reshape(-1,7);v=a[:,1:].copy();v[:,4]*=1000
        series[token]=Series(a[:,0]*1000,v,source_column=4,counts=book_checks);starts.append(a[:,0].min());ends.append(a[:,0].max())
    for token,buf in bboraw.items():
        if token not in tokens:continue
        a=np.frombuffer(buf,dtype=float).reshape(-1,4);v=a[:,1:].copy();v[:,2]*=1000
        bbos[token]=Series(a[:,0]*1000,v,source_column=2,counts=bbo_checks)
    if not series:raise ValueError('no CLOB rows map to BTC5m Gamma tokens')
    begin=float(min(starts));end=float(max(ends));errors=np.sort(np.asarray(errors,float))
    chosen=sorted((m for m in meta.values() if m['start_ms']<=end and m['end_ms']>begin),key=lambda m:m['start_ms'])
    if not chosen:raise ValueError('no market intersects actual receipt span')
    if not math.isfinite(settlement_cutoff_ms) or settlement_cutoff_ms<=max(m['end_ms'] for m in chosen):raise ValueError('settlement cutoff must be after every market end')
    identity={'version':VERSION,'archive_sha256':sha(path),'code_sha256':sha(__file__),'helpers_sha256':{n:sha(Path(__file__).with_name(n)) for n in ('lake_parquet_adapter.py','legacy_up_l1_adapter.py','legacy_recording_cache.py','mine500_adapter.py')},'settlement_cutoff_ms':settlement_cutoff_ms,'max_book_age_ms':max_book_age_ms,'max_source_age_ms':max_source_age_ms,'source_reference':'chainlink'}
    header={'type':'dataset','recv_ms':begin,'dataset_id':VERSION+':'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20],'split':'development','provenance':{'source':str(path.resolve()),'identity':identity},'source_reference':'chainlink','source_reference_semantics':'BTC/USD observed Chainlink price; not Binance and not official opening/final TWAP labels','coverage_start_ms':begin,'coverage_end_ms':end,'observed_intervals_ms':[[begin,end]],'observed_hours':(end-begin)/3600000,'coverage_basis':'first/last observed mapped native CLOB receipt; not complete continuous feed certification','book_resolution':'decision_and_match_projection','decision_grid_ms':1000,'supported_latencies_ms':[500,600,1000],'execution_book_sides':['Up','Down'],'strict_execution_admitted':False,'formal_oos_result':None,'native_book_mode':mode,'assumptions':{'market_parameters':'recorded Gamma fee/tick/minSize assumed valid during market; missing values remain null','metadata_registration':'retrospective stable mapping; local receipt absent','cash_release':'all outcomes withheld until supplied GLOBAL cutoff','source_continuity':'unverified; no aggressor/volume inferred'},'limitations':['separate Chainlink historical sensitivity lane; not same-source Binance validation','retrospective Gamma metadata timing and settlement spendability unverified','reduced snapshot venue clocks absent; no source-time substitution','BBO price contradictions and known disconnects invalidate stale snapshots','L1 displayed depth is only capacity upper bound; no queue or real execution inference','equal local receipt conflicts are unknown; all queries require recv strictly before decision/match']}
    header['assumptions']['initial_tick']='earliest recorded Gamma tick is an explicit initial assumption; later unclocked metadata never backfills it; native tick events change it only after receipt'
    header['limitations'].append('reduced bundle books retain the last snapshot per second; those tapes are sampled displayed-capacity sensitivity, not exact subsecond execution')
    header['book_observation_semantics']='one_snapshot_per_second' if mode=='reduced_bundle_snapshots' else 'native_snapshot_and_delta_reconstruction'
    def at(token,now):
        s=series.get(token)
        value,recv,status=s.before(now*1000,max_book_age_ms*1000) if s else (None,None,'missing')
        if value is not None and not np.isfinite(value[:4]).all():value=None;status='invalid_book'
        if value is not None:
            ix=np.searchsorted(errors,now,side='left')-1
            # A delta after a disconnect must never revive a pre-disconnect
            # reconstruction. Require a genuinely later full snapshot.
            if ix>=0 and (not math.isfinite(value[5]) or errors[ix]>=value[5]):value=None;status='recorded_clob_error'
        if value is not None and token in bbos:
            b,br,bs=bbos[token].before(now*1000,math.inf)
            if br is not None and br>=recv:
                if b is None or not np.isfinite(b[:2]).all() or abs(b[0]-value[0])>1e-9 or abs(b[1]-value[2])>1e-9:value=None;status='bbo_contradiction_or_ambiguity'
        quality['projection_'+status]+=1
        return value,recv/1000 if recv is not None else None,status
    emitted=Counter();features=Counter();outcomes=[];output.parent.mkdir(parents=True,exist_ok=True);pending=output.with_name(output.name+'.pending')
    def write(f,r):emitted[r['type']]+=1;f.write(json.dumps(r,separators=(',',':'),allow_nan=False)+'\n')
    with gzip.open(pending,'wt',compresslevel=1) as f:
        write(f,header)
        for m in chosen:
            low=max(begin,m['start_ms']);high=min(end,m['end_ms']);mid=m['market_id'];hist=[]
            write(f,{'type':'market','recv_ms':low,'market_id':mid,'slug':m['slug'],'start_ms':m['start_ms'],'end_ms':m['end_ms'],'up_token_id':m['up_token_id'],'down_token_id':m['down_token_id'],'label_source':'gamma','fee_rate':m['fee_rate'],'fee_provenance':'retrospective_recorded_Gamma','metadata_availability_assumed':True,'local_metadata_recv_ms':None,'source_reference':'chainlink'})
            c={'type':'constraint','recv_ms':low,'market_id':mid,'metadata_availability_assumed':True,'constraints_source':'retrospective_recorded_Gamma'}
            if m['min_size'] is not None:c['min_order_size']=m['min_size']
            if m['tick'] is not None:c.update(up_tick_size=m['tick'],down_tick_size=m['tick'])
            pending_ticks=[]
            for at_ms,token,tick,source_ms in sorted(tick_events):
                side='up' if token==m['up_token_id'] else 'down' if token==m['down_token_id'] else None
                if side is None or at_ms>=m['end_ms']:continue
                if at_ms<low:c[side+'_tick_size']=tick;c[side+'_tick_size_available_ms']=at_ms
                else:pending_ticks.append({'type':'constraint','recv_ms':at_ms,'market_id':mid,side+'_tick_size':tick,side+'_tick_size_available_ms':at_ms,'constraints_source':'native_clob_tick_size_change','source_ts_ms':source_ms,'metadata_availability_assumed':False})
            if len(c)>5:write(f,c)
            for sec in range(int(math.ceil(low/1000)*1000),int(high)+1,1000):
                for delay in (0,500,600):
                    now=sec+delay
                    if now>high or now>=m['end_ms']:continue
                    while pending_ticks and pending_ticks[0]['recv_ms']<now:write(f,pending_ticks.pop(0))
                    values={};row={'type':'book','recv_ms':now,'market_id':mid,'projection_asof_ms':now,'fee_rate':m['fee_rate'],'fee_provenance':'retrospective_recorded_Gamma','max_book_age_ms':max_book_age_ms}
                    for side in ('up','down'):
                        v,receipt,status=at(m[side+'_token_id'],now);values[side]=(v,receipt)
                        row.update({side+'_asks':[[float(v[2]),float(v[3])]] if v is not None else [],side+'_bids':[[float(v[0]),float(v[1])]] if v is not None else [],side+'_recv_ms':receipt,side+'_healthy':v is not None,side+'_status':status,side+'_source_ts_ms':float(v[4])/1000 if v is not None and math.isfinite(v[4]) else None})
                    row['healthy']=row['up_healthy'] and row['down_healthy'];write(f,row)
                    if delay==0:
                        v,receipt=values['up'];book=([[float(v[0]),float(v[1])]],[[float(v[2]),float(v[3])]]) if v is not None else None
                        vals,av=feature_row(spot,hist,now,m['end_ms'],book,receipt,max_source_age_ms);features.update(k for k,v in vals.items() if v is not None)
                        write(f,{'type':'decision','recv_ms':now,'market_id':mid,'features':vals,'feature_available_ms':av,'source_reference':'chainlink'})
            for event in pending_ticks:
                if event['recv_ms']<=high:write(f,event)
            if m['winner'] is not None:outcomes.append({'type':'outcome','recv_ms':settlement_cutoff_ms,'resolved_ms':m['end_ms'],'market_id':mid,'winner':m['winner'],'source':'gamma','label_verified':m['label_verified_by_recorded_gamma'],'local_outcome_recv_ms':None,'settlement_availability_assumed':True})
            else:quality['markets_missing_outcome']+=1
        for r in outcomes:write(f,r)
    pending.replace(output)
    result={'identity':identity,'header':header,'output':str(output.resolve()),'output_sha256':sha(output),'registered_markets':len(chosen),'labelled_markets':len(outcomes),'counts':dict(emitted),'quality_counts':dict(quality),'book_source_checks':dict(book_checks),'reference_source_checks':dict(source_checks),'bbo_source_checks':dict(bbo_checks),'nonmissing_features':dict(features),'input_member_counts':dict(member_counts),'elapsed_seconds':time.perf_counter()-started,'pnl':None,'new_strategy_backtests':0,'formal_oos_result':None}
    Path(str(output)+'.manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True);p.add_argument('--out',required=True);p.add_argument('--settlement-cutoff-ms',required=True,type=float);a=p.parse_args();r=normalize(a.archive,a.out,settlement_cutoff_ms=a.settlement_cutoff_ms);print(json.dumps({k:v for k,v in r.items() if k not in ('header','input_member_counts')},indent=2))
if __name__=='__main__':main()
