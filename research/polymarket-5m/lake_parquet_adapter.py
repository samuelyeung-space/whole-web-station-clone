"""Receipt-causal Up-only replay projection of the existing polylake Parquet.

This is a separately labelled historical-development sensitivity lane. The lake
already selected one recorder per market using whole-market quality. Original
metadata/fee/settlement receipt clocks and independent Down books are absent.
No source timestamp is substituted for a missing receipt, no book_ok filter is
used, no Down complement is manufactured, and final labels are withheld until
an explicit global cutoff (there is no settlement-cash recycling before it).
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np

VERSION = 'lake-up-only-historical-v1'
LEVELS = [f'{side}{level}{field}' for side in ('b', 'a') for level in range(1,6) for field in ('p','s')]
BOOK_COLUMNS = ['slot','ts_us','rcv_us',*LEVELS,'src']
SPOT_COLUMNS = ['ts_us','rcv_us','price']
WINDOWS = (5,15,30,60)

def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def _number(value):
    return isinstance(value,(int,float,np.number)) and not isinstance(value,(bool,np.bool_)) and math.isfinite(value)

def parquet_modules():
    """Load Arrow only for actual Parquet I/O, not legacy CSV/JSONL replay."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Parquet input requires optional dependency pyarrow. "
            "Install pyarrow to read Parquet; legacy ZIP CSV/JSONL replay does not require it."
        ) from exc
    return pa, pq


def read_columns(paths, columns):
    pa, pq = parquet_modules()
    tables = []
    for path in paths:
        p = Path(path)
        table = pq.ParquetFile(p).read(columns=columns)
        if set(columns)-set(table.column_names):
            raise ValueError(f'missing required columns: {p}: {set(columns)-set(table.column_names)}')
        tables.append(table)
    if not tables: raise ValueError('at least one input Parquet file required')
    table = pa.concat_tables(tables)
    return {name:table[name].to_numpy(zero_copy_only=False) for name in columns}

def receipt_series(receipts, values, *, source_column=None, counts=None):
    """Collapse exact duplicates; mark conflicting same-receipt states unknown.

    Sorting uses explicit local receipts only. Equal-receipt batches are not
    assigned a made-up transport sequence. Queries are strictly before time.
    """
    receipts=np.asarray(receipts,dtype=float); values=np.asarray(values,dtype=float)
    if len(receipts)!=len(values): raise ValueError('receipt/value length mismatch')
    valid=np.isfinite(receipts)&(receipts>0)
    order=np.argsort(receipts[valid],kind='stable')
    stamps=receipts[valid][order]; data=values[valid][order]
    if not len(stamps): return stamps,data,np.zeros(0,dtype=bool)
    indices=np.r_[0,np.flatnonzero(np.diff(stamps)!=0)+1]
    ends=np.r_[indices[1:],len(stamps)]
    if source_column is not None:
        source=data[:,source_column]
        known=np.isfinite(source)&(source>0)
        groupmax=np.maximum.reduceat(np.where(known,source,-np.inf),indices)
        previous=np.r_[-np.inf,np.maximum.accumulate(groupmax)[:-1]]
        keep=(~known)|(source>=np.repeat(previous,ends-indices))
        if counts is not None:
            counts['rejected_source_time_regressions']+=int((~keep).sum())
            counts['missing_source_timestamps']+=int((~known).sum())
        stamps,data=stamps[keep],data[keep]
        indices=np.r_[0,np.flatnonzero(np.diff(stamps)!=0)+1]
        ends=np.r_[indices[1:],len(stamps)]
    ambiguous=np.zeros(len(indices),dtype=bool)
    for group in np.flatnonzero(ends-indices>1):
        first,last=indices[group],ends[group]
        ambiguous[group]=not np.all(np.isclose(data[first:last],data[first],rtol=0,atol=0,equal_nan=True))
    return stamps[indices],data[indices],ambiguous

class Series:
    def __init__(self, receipts, values, *, source_column=None, counts=None):
        self.times,self.values,self.ambiguous=receipt_series(receipts,values,source_column=source_column,counts=counts)
    def before(self, when_us, max_age_us):
        index=int(np.searchsorted(self.times,when_us,side='left'))-1
        if index<0: return None,None,'missing'
        stamp=self.times[index]
        if when_us-stamp>max_age_us:return None,float(stamp),'stale'
        if self.ambiguous[index]:return None,float(stamp),'ambiguous_equal_receipt'
        return self.values[index],float(stamp),'available'

def parse_levels(values):
    bids,asks=[],[]
    for n in range(0,20,2):
        p,s=values[n:n+2]
        if np.isnan(p) and np.isnan(s):continue
        if not (_number(p) and _number(s) and 0<p<1 and s>=0):return None
        if s>0:(bids if n<10 else asks).append([float(p),float(s)])
    bids.sort(reverse=True);asks.sort()
    if not bids or not asks or bids[0][0]>=asks[0][0]:return None
    if len({p for p,s in bids})!=len(bids) or len({p for p,s in asks})!=len(asks):return None
    return bids,asks

def canonical_market(row):
    slot=row.get('slot')
    if not _number(slot) or int(slot)!=slot or int(slot)%300:raise ValueError('unaligned market slot')
    winner=row.get('winner')
    if winner not in (None,'Up','Down','') and not (isinstance(winner,float) and math.isnan(winner)):
        raise ValueError('winner must use documented Up/Down strings; numeric encoding is not inferred')
    mid=row.get('condition_id')
    if mid is None or str(mid).strip()=='':mid='btc-updown-5m-'+str(int(slot))
    return int(slot),str(mid),winner if winner in ('Up','Down') else None

def _interval_union(intervals):
    result=[]
    for a,b in sorted(intervals):
        if a>=b:continue
        if result and a<=result[-1][1]:result[-1][1]=max(result[-1][1],b)
        else:result.append([a,b])
    return result

def feature_row(spot, history, now_ms, market_end_ms, book, book_receipt, source_age_ms):
    # A source-event timestamp is NEVER an availability timestamp.
    queries=now_ms*1000-np.arange(60,-1,-1,dtype=float)*1_000_000
    idx=np.searchsorted(spot.times,queries,side='left')-1
    px=np.full(61,np.nan);receipts=np.full(61,np.nan)
    ok=idx>=0
    if np.any(ok):
        inds=idx[ok];good=((queries[ok]-spot.times[inds])<=source_age_ms*1000)&~spot.ambiguous[inds]
        goodidx=np.flatnonzero(ok)[good]
        px[goodidx]=spot.values[inds[good],0]
        receipts[goodidx]=spot.times[inds[good]]/1000
    px[px<=0]=np.nan
    f={};av={}
    for w in WINDOWS:
        values=px[-w-1:];valid=np.isfinite(values).all()
        f[f'r_{w}s']=float(10000*math.log(values[-1]/values[0])) if valid else None
        av[f'r_{w}s']=float(np.nanmax(receipts[-w-1:])) if valid else None
        if w in (15,60):
            f[f'rv_{w}s']=float(10000*np.sqrt(np.sum(np.diff(np.log(values))**2))) if valid else None
            av[f'rv_{w}s']=av[f'r_{w}s']
            # No connection/sequence record: do not call recorded ticks complete aggressor flow.
            f[f'flow_{w}s']=None;av[f'flow_{w}s']=None
    mid=(book[0][0][0]+book[1][0][0])/2 if book else None
    history.append((now_ms,mid,book_receipt if book else None))
    if len(history)>61:del history[:-61]
    f.update(mid_up=mid,tau_s=(market_end_ms-now_ms)/1000,imbalance_up=None,
             fair_up=None,tail_gap_bps=None,tail_sigma_bps=None,twap_known_fraction=None)
    av.update(mid_up=book_receipt if book else None,tau_s=now_ms,imbalance_up=None,
              fair_up=None,tail_gap_bps=None,tail_sigma_bps=None,twap_known_fraction=None)
    if book:
        b,a=book[0][0][1],book[1][0][1]
        f['imbalance_up']=(b-a)/(b+a);av['imbalance_up']=book_receipt
    for w in WINDOWS:
        rows=history[-w-1:]
        valid=len(rows)==w+1 and all(t==now_ms-(w-i)*1000 and p is not None for i,(t,p,r) in enumerate(rows))
        f[f'pm_r_{w}s']=rows[-1][1]-rows[0][1] if valid else None
        av[f'pm_r_{w}s']=max(r[2] for r in rows) if valid else None
    return f,av

def normalize(books_paths, spot_paths, markets_path, output, *, settlement_cutoff_ms,
              assumed_fee_rate, assumed_min_order_size, assumed_tick_size,
              max_book_age_ms=250, max_source_age_ms=1000, provenance=None):
    """Write ordinary replay events, with explicit side and assumption markers.

    Engine must opt into ``up_only_limited`` execution. Dataset-wide health is
    deliberately false: the absent Down tape must never be represented as fresh.
    Strict original-protocol admission is explicitly false.
    """
    started=time.perf_counter();output=Path(output)
    if output.exists():raise ValueError('output already exists; do not overwrite an immutable normalization')
    for name,v in [('settlement cutoff',settlement_cutoff_ms),('fee',assumed_fee_rate),('min shares',assumed_min_order_size),('tick',assumed_tick_size)]:
        if not _number(v) or v<0:raise ValueError('invalid '+name)
    if not 0<=assumed_fee_rate<=1 or assumed_min_order_size<=0 or not 0<assumed_tick_size<1:raise ValueError('invalid execution assumptions')
    books_paths=[Path(p) for p in books_paths];spot_paths=[Path(p) for p in spot_paths];markets_path=Path(markets_path)
    hashes={str(p.resolve()):sha(p) for p in [*books_paths,*spot_paths,markets_path]}
    b=read_columns(books_paths,BOOK_COLUMNS);s=read_columns(spot_paths,SPOT_COLUMNS)
    source_counts=Counter();book_source_counts=Counter()
    spot=Series(s['rcv_us'],np.column_stack([s['price'],s['ts_us']]),source_column=1,counts=source_counts)
    _, pq = parquet_modules()
    market_rows=pq.ParquetFile(markets_path).read().to_pylist();markets={}
    for row in market_rows:
        slot,mid,winner=canonical_market(row)
        if slot in markets and markets[slot]!=(mid,winner):raise ValueError('conflicting market registry')
        markets[slot]=(mid,winner)
    count=Counter();features=Counter();selected=[];intervals=[]
    book_slots=np.asarray(b['slot']);valid_receipt=np.isfinite(np.asarray(b['rcv_us'],float))&(np.asarray(b['rcv_us'],float)>0)
    count['raw_book_rows']=len(book_slots);count['book_rows_missing_local_receipt']=int((~valid_receipt).sum())
    count['raw_spot_rows']=len(s['price']);count['spot_rows_missing_local_receipt']=int((~(np.isfinite(np.asarray(s['rcv_us'],float))&(np.asarray(s['rcv_us'],float)>0))).sum())
    matrix=np.column_stack([b[c] for c in LEVELS]+[b['ts_us']])
    # Select by actual market/date in requested files only. Never inspect book_ok,
    # eventual maximum gap, source lag, winner or full-session health to filter.
    for slot in sorted(set(int(x) for x in book_slots)):
        if slot not in markets:raise ValueError('book slot has no market definition')
        sources={str(v) for v in np.asarray(b['src'])[book_slots==slot]}
        if len(sources)!=1:raise ValueError('multiple recorder sources for one market; explicit stream separation required')
        mask=(book_slots==slot)&valid_receipt
        if not np.any(mask):count['markets_without_clocked_books']+=1;continue
        series=Series(np.asarray(b['rcv_us'])[mask],matrix[mask],source_column=20,counts=book_source_counts)
        low=max(slot*1000,float(series.times[0]/1000));high=min((slot+300)*1000,float(series.times[-1]/1000))
        if low>=high:count['markets_without_in_market_receipts']+=1;continue
        if settlement_cutoff_ms<=(slot+300)*1000:raise ValueError('settlement cutoff must be later than every market end')
        selected.append((slot,series,low,high));intervals.append([low,high])
    if not selected:raise ValueError('no market with locally clocked Up books')
    intervals=_interval_union(intervals);begin=intervals[0][0];end=intervals[-1][1]
    assumptions={'fee_rate':float(assumed_fee_rate),'min_order_size':float(assumed_min_order_size),
        'tick_size':float(assumed_tick_size),'metadata_registration':'retrospective immutable token/slot mapping at first observed book',
        'settlement_cutoff_ms':float(settlement_cutoff_ms),'cash_policy':'all outcomes withheld until global cutoff; no recycling before cutoff',
        'source_continuity':'unverified; aggressor-flow catalog fields kept null','book_age_ms':max_book_age_ms,'source_age_ms':max_source_age_ms}
    assumptions['label_source_verification']='upstream assertion, not independently verified'
    identity={'version':VERSION,'input_sha256':hashes,'adapter_sha256':sha(__file__),'assumptions':assumptions}
    header={'type':'dataset','recv_ms':begin,'dataset_id':VERSION+':'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20],
        'split':'development','provenance':{'source':provenance or 'sgk90-shadow-archive/lake','input_sha256':hashes,'adapter_sha256':sha(__file__)},
        'coverage_start_ms':begin,'coverage_end_ms':end,'observed_intervals_ms':intervals,
        'observed_hours':sum(b-a for a,b in intervals)/3600000,'coverage_basis':'per-market first/last locally clocked book spans; not continuous recording or healthy-day certification',
        'book_resolution':'decision_and_match_projection','decision_grid_ms':1000,'supported_latencies_ms':[500,600,1000],
        'execution_book_sides':['Up'],'execution_mode':'up_only_limited','strict_execution_admitted':False,
        'metadata_availability_assumed':True,'assumptions':assumptions,'formal_oos_result':None,
        'limitations':['upstream recorder selection used whole-market health; selection bias is inherited',
            'no independent Down book; Down orders must fail, never use a complement',
            'retrospective market definitions and fee/tick/minimum-share assumptions are not local as-of evidence',
            'markets.winner is upstream settlement label; no Binance direction substitution; original label receipt absent',
            'displayed top-five depth is a capacity upper bound, not measured fills',
            'missing receipt clocks are excluded; no source-clock fallback or interpolation',
            'no queue, maker, scale-in, live fill or original frozen OOS validation']}
    output.parent.mkdir(parents=True,exist_ok=True);pending=output.with_name(output.name+'.pending')
    emitted=Counter();outcomes=[]
    def write(f,event):
        emitted[event['type']]+=1
        f.write(json.dumps(event,separators=(',',':'),allow_nan=False)+'\n')
    with gzip.open(pending,'wt',compresslevel=1) as f:
        write(f,header)
        for slot,series,low,high in selected:
            mid,winner=markets[slot];market_end=(slot+300)*1000
            write(f,{'type':'market','recv_ms':low,'market_id':mid,'slug':'btc-updown-5m-'+str(slot),
                'start_ms':slot*1000,'end_ms':market_end,'label_source':'polymarket',
                'fee_rate':float(assumed_fee_rate),'fee_provenance':'explicit_historical_sensitivity_assumption',
                'local_metadata_recv_ms':None,'metadata_availability_assumed':True})
            write(f,{'type':'constraint','recv_ms':low,'market_id':mid,'min_order_size':float(assumed_min_order_size),
                'up_tick_size':float(assumed_tick_size),'constraints_source':'explicit_historical_sensitivity_assumption',
                'metadata_availability_assumed':True})
            history=[]
            first=int(math.ceil(low/1000)*1000)
            for sec in range(first,int(high)+1,1000):
                for delay in (0,500,600):
                    now=sec+delay
                    if now>high or now>=market_end:continue
                    value,receipt,status=series.before(now*1000,max_book_age_ms*1000)
                    book=parse_levels(value) if value is not None else None
                    if value is not None and book is None:status='invalid_book'
                    count['projection_'+status]+=1
                    at=receipt/1000 if receipt is not None else None
                    write(f,{'type':'book','recv_ms':now,'market_id':mid,'projection_asof_ms':now,
                        'up_asks':book[1] if book else [],'up_bids':book[0] if book else [],
                        'down_asks':[],'down_bids':[],'up_recv_ms':at,'down_recv_ms':None,
                        'up_source_ts_ms':float(value[20])/1000 if value is not None and _number(value[20]) else None,
                        'up_healthy':bool(book),'down_healthy':False,'healthy':False,
                        'execution_book_sides':['Up'],'execution_mode':'up_only_limited',
                        'fee_rate':float(assumed_fee_rate),'fee_provenance':'explicit_historical_sensitivity_assumption',
                        'max_book_age_ms':max_book_age_ms,'up_status':status})
                    if delay==0:
                        vals,available=feature_row(spot,history,now,market_end,book,at,max_source_age_ms)
                        source_value,source_receipt,source_status=spot.before(now*1000,max_source_age_ms*1000)
                        features.update(k for k,v in vals.items() if v is not None)
                        write(f,{'type':'decision','recv_ms':now,'market_id':mid,'features':vals,'feature_available_ms':available,
                            'external_source_event_ms':float(source_value[1])/1000 if source_value is not None and _number(source_value[1]) else None,
                            'external_local_recv_ms':source_receipt/1000 if source_receipt is not None else None,
                            'external_price':float(source_value[0]) if source_value is not None and _number(source_value[0]) and source_value[0]>0 else None,
                            'external_status':source_status})
            if winner is not None:
                outcomes.append({'type':'outcome','recv_ms':float(settlement_cutoff_ms),'resolved_ms':market_end,
                    'market_id':mid,'winner':winner,'source':'polymarket','label_provenance':'lake.markets.winner',
                    'label_verified':False,'label_source_verification':'upstream assertion, not independently verified',
                    'local_outcome_recv_ms':None,'settlement_availability_assumed':True})
            else:count['markets_missing_label']+=1
        for row in outcomes:write(f,row)
    pending.replace(output)
    result={'schema':VERSION,'identity':identity,'output':str(output.resolve()),'output_sha256':sha(output),'header':header,
        'counts':dict(emitted),'source_quality_counts':dict(count),'book_source_clock_checks':dict(book_source_counts),
        'spot_source_clock_checks':dict(source_counts),'nonmissing_features':dict(features),
        'registered_markets':len(selected),'labelled_markets':len(outcomes),'elapsed_seconds':time.perf_counter()-started,
        'formal_oos_result':None,'strict_execution_admitted':False,'new_backtests':0,'pnl':None}
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--books',nargs='+',required=True);p.add_argument('--spot',nargs='+',required=True)
    p.add_argument('--markets',required=True);p.add_argument('--out',required=True)
    p.add_argument('--settlement-cutoff-ms',type=float,required=True)
    p.add_argument('--assumed-fee-rate',type=float,required=True)
    p.add_argument('--assumed-min-order-size',type=float,required=True)
    p.add_argument('--assumed-tick-size',type=float,required=True)
    p.add_argument('--max-book-age-ms',type=int,default=250);p.add_argument('--max-source-age-ms',type=int,default=1000)
    args=p.parse_args();r=normalize(args.books,args.spot,args.markets,args.out,settlement_cutoff_ms=args.settlement_cutoff_ms,
        assumed_fee_rate=args.assumed_fee_rate,assumed_min_order_size=args.assumed_min_order_size,assumed_tick_size=args.assumed_tick_size,
        max_book_age_ms=args.max_book_age_ms,max_source_age_ms=args.max_source_age_ms)
    Path(args.out+'.manifest.json').write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k!='header'},indent=2))

if __name__=='__main__':main()
