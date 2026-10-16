"""Causal Up-L1 historical sensitivity replay for old published forward ZIPs.

Reuse the lossless-cache schema/clock definitions and receipt-index as-of policy.
Read only native probability observations, Binance prices and Gamma metadata;
sampled full-depth snapshots are deliberately not used as matching books.
This does not certify metadata timing, feed continuity or actual execution.
"""
from __future__ import annotations
import argparse
from array import array
from collections import Counter,defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile
import time
import zipfile
import numpy as np
from legacy_recording_cache import classify,clock,sha
from lake_parquet_adapter import Series,feature_row

VERSION='legacy-up-l1-historical-v1'

def number(x):
    if isinstance(x,bool):raise ValueError('boolean numeric value')
    n=float(x)
    if not math.isfinite(n):raise ValueError('nonfinite numeric value')
    return n

def sequence(x):return json.loads(x) if isinstance(x,str) else x

def market(row):
    raw=row['raw'];slug=row['slug']
    if not slug.startswith('btc-updown-5m-'):raise ValueError('non BTC5m metadata')
    start=number(row['start_sec'])*1000;end=number(row['end_sec'])*1000
    if end-start!=300000 or start%300000:raise ValueError('invalid five-minute interval')
    outcomes=sequence(raw['outcomes']);tokens=sequence(raw['clobTokenIds'])
    if len(tokens)!=2 or set(outcomes)!={'Up','Down'}:raise ValueError('invalid token mapping')
    prices=sequence(raw.get('outcomePrices',row.get('outcome_prices')))
    winner=None
    if row.get('resolved') is True and prices is not None:
        ps=[number(p) for p in prices]
        if sorted(ps)==[0.,1.]:winner=outcomes[ps.index(1.)]
    fee=raw.get('feeSchedule');rate=None
    if fee is not None:
        if fee.get('exponent')!=1 or fee.get('takerOnly') is not True:raise ValueError('unsupported fee schedule')
        rate=number(fee['rate'])
        if not 0<=rate<=1:raise ValueError('invalid fee')
    tick=raw.get('orderPriceMinTickSize');size=raw.get('orderMinSize')
    tick=number(tick) if tick is not None else None;size=number(size) if size is not None else None
    if tick is not None and not 0<tick<1:raise ValueError('invalid tick')
    if size is not None and size<=0:raise ValueError('invalid min size')
    return {'slug':slug,'market_id':str(raw['conditionId']),'start_ms':start,'end_ms':end,
        'up_token_id':str(tokens[outcomes.index('Up')]),'down_token_id':str(tokens[outcomes.index('Down')]),
        'winner':winner,'label_verified_by_recorded_gamma':winner is not None,'fee_rate':rate,'tick':tick,'min_size':size,
        'resolution_source_url':raw.get('resolutionSource'),'raw_gamma_closed_time':raw.get('closedTime')}

def l1(row):
    """Return the observed L1; invalid current states stay invalid, not forward-filled."""
    try:
        bids=sequence(row['bids_json']);asks=sequence(row['asks_json'])
        if not bids or not asks:return [math.nan]*4
        b=[[number(p),number(q)] for p,q in bids];a=[[number(p),number(q)] for p,q in asks]
        if any(not 0<p<1 or q<0 for p,q in b+a):return [math.nan]*4
        b=sorted((p,q) for p,q in b if q>0);a=sorted((p,q) for p,q in a if q>0)
        if not b or not a or b[-1][0]>=a[0][0]:return [math.nan]*4
        # CSV advertises best prices too: a disagreement is evidence of an
        # inconsistent state, not permission to select the cheaper execution.
        if abs(number(row['best_bid'])-b[-1][0])>1e-9 or abs(number(row['best_ask'])-a[0][0])>1e-9:return [math.nan]*4
        return [*b[-1],*a[0]]
    except (ValueError,TypeError,KeyError):return [math.nan]*4

def archive_rows(path):
    """Stream all selected compressed members to EOF, preserving original files."""
    with zipfile.ZipFile(path) as z:
        if z.namelist()!=['raw.tgz']:raise ValueError('unsupported recording layout')
        with z.open('raw.tgz') as zipped:
            with tarfile.open(fileobj=zipped,mode='r|gz') as tar:
                for member in tar:
                    if not member.isfile():continue
                    kind=classify(member.name)
                    if kind not in ('probability_observation','btc_spot_trade','market_metadata','final_label'):continue
                    with gzip.GzipFile(fileobj=tar.extractfile(member)) as gz:
                        with io.TextIOWrapper(gz,encoding='utf-8') as f:
                            rows=(json.loads(line) for line in f) if member.name.endswith('.jsonl.gz') else csv.DictReader(f)
                            for ordinal,row in enumerate(rows,1):yield kind,member.name,ordinal,row
            while zipped.read(1024*1024):pass

def read_archive(path):
    metadata={};raw_books=defaultdict(lambda:array('d'));raw_spot=array('d');labels={};counts=Counter();members=Counter()
    for kind,member,ordinal,row in archive_rows(path):
        counts[kind]+=1;members[member]+=1
        if kind=='market_metadata':
            info=market(row);old=metadata.get(info['slug'])
            if old is not None and old!=info:raise ValueError('conflicting retrospective market parameters or label')
            metadata[info['slug']]=info
        elif kind=='final_label':
            winner=row['winner'];slug=row['market_id']
            if winner not in ('Up','Down',''):raise ValueError('invalid official label CSV')
            if winner:
                if slug in labels and labels[slug]!=winner:raise ValueError('conflicting label CSV')
                labels[slug]=winner
        elif kind=='probability_observation':
            now,field=clock(row)
            if now is None:counts['book_missing_receipt']+=1;continue
            src=number(row['source_ts'])*1000 if row.get('source_ts') not in (None,'') else math.nan
            value=l1(row)
            if not math.isfinite(value[0]):counts['invalid_l1_rows']+=1
            raw_books[row['market_id']].extend([now,*value,src])
        elif kind=='btc_spot_trade':
            now,field=clock(row)
            if now is None:counts['spot_missing_receipt']+=1;continue
            price=number(row['price'])
            src=number(row['trade_ts'])*1000 if row.get('trade_ts') not in (None,'') else math.nan
            if price<=0:raise ValueError('invalid spot price')
            raw_spot.extend([now,price,src])
    for slug,winner in labels.items():
        if slug not in metadata:raise ValueError('outcome without official metadata')
        if metadata[slug]['winner'] is not None and metadata[slug]['winner']!=winner:raise ValueError('Gamma/CSV outcome conflict')
        # Label CSV alone is the published recorder's official-label assertion;
        # preserve provenance. Never create a label from underlying prices.
        metadata[slug]['winner']=winner
    if not raw_books or not raw_spot:raise ValueError('receipt-clock Up L1 and BTC prices required')
    return metadata,raw_books,raw_spot,counts,members

def normalize(path,output,*,settlement_cutoff_ms,max_book_age_ms=250,max_source_age_ms=1000):
    started=time.perf_counter();path=Path(path);output=Path(output)
    if output.exists():raise ValueError('immutable output already exists')
    if not math.isfinite(settlement_cutoff_ms):raise ValueError('invalid settlement cutoff')
    input_hash=sha(path);metadata,raw_books,raw_spot,counts,members=read_archive(path)
    source_checks=Counter();book_checks=Counter()
    a=np.frombuffer(raw_spot,dtype=float).reshape(-1,3)
    spot=Series(a[:,0]*1000,np.column_stack([a[:,1],a[:,2]*1000]),source_column=1,counts=source_checks)
    books={};starts=[];ends=[]
    for slug,buf in raw_books.items():
        if slug not in metadata:raise ValueError('clocked L1 without official token mapping')
        a=np.frombuffer(buf,dtype=float).reshape(-1,6)
        v=a[:,1:].copy();v[:,4]*=1000
        books[slug]=Series(a[:,0]*1000,v,source_column=4,counts=book_checks)
        starts.append(a[:,0].min());ends.append(a[:,0].max())
    begin=float(min(starts));end=float(max(ends))
    chosen=sorted((m for m in metadata.values() if m['start_ms']<=end and m['end_ms']>begin),key=lambda m:m['start_ms'])
    if not chosen:raise ValueError('no registered BTC5m market overlaps recording')
    if settlement_cutoff_ms<=max(m['end_ms'] for m in chosen):raise ValueError('global settlement cutoff must be after every market end')
    identity={'schema':VERSION,'archive_sha256':input_hash,'code_sha256':sha(__file__),
        'receipt_series_feature_code_sha256':sha(Path(__file__).with_name('lake_parquet_adapter.py')),
        'legacy_clock_code_sha256':sha(Path(__file__).with_name('legacy_recording_cache.py')),
        'settlement_cutoff_ms':settlement_cutoff_ms,'max_book_age_ms':max_book_age_ms,'max_source_age_ms':max_source_age_ms}
    header={'type':'dataset','recv_ms':begin,'dataset_id':VERSION+':'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20],
        'split':'development','provenance':{'source':str(path.resolve()),'identity':identity},
        'coverage_start_ms':begin,'coverage_end_ms':end,'observed_intervals_ms':[[begin,end]],'observed_hours':(end-begin)/3600000,
        'coverage_basis':'first/last clocked native Up-L1 observation; span is not continuous feed certification',
        'book_resolution':'decision_and_match_projection','decision_grid_ms':1000,'supported_latencies_ms':[500,600,1000],
        'execution_book_sides':['Up'],'execution_mode':'up_only_limited','strict_execution_admitted':False,'formal_oos_result':None,
        'assumptions':{'market_parameters':'recorded post-resolution Gamma fee/tick/minSize assumed valid throughout market; local availability absent',
            'metadata_registration':'retrospective stable token/slot mapping at first applicable recording time',
            'cash_release':'all final labels withheld until supplied GLOBAL cutoff; zero settlement cash reuse before cutoff',
            'settlement_cutoff_ms':settlement_cutoff_ms,'up_l1':'native source+local receipt observations only; no sampled full-depth proxy',
            'missing_down':'unobserved; all Down orders must fail','source_continuity':'not established; flow fields null'},
        'limitations':['historical sensitivity only; no old frozen-protocol inheritance',
            'fee/tick/minimum-share effective/local availability clocks absent; retrospective values explicit',
            'settlement local receipt/cash spendability unknown; global withholding is a conservative capital scenario',
            'Up L1 depth only is displayed capacity, not real fills; no Down complement or sampled snapshot matching',
            'source time regressions ignored causally; equal-receipt conflicting states invalid until a later state',
            'no source connection/sequence proof; no queue or live failure calibration']}
    emitted=Counter();features=Counter();outcomes=[];output.parent.mkdir(parents=True,exist_ok=True);pending=output.with_name(output.name+'.pending')
    def write(f,row):emitted[row['type']]+=1;f.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
    with gzip.open(pending,'wt',compresslevel=1) as f:
        write(f,header)
        for m in chosen:
            slug=m['slug'];mid=m['market_id'];low=max(begin,m['start_ms']);high=min(end,m['end_ms'])
            write(f,{'type':'market','recv_ms':low,'market_id':mid,'slug':slug,'start_ms':m['start_ms'],'end_ms':m['end_ms'],
                'label_source':'gamma','fee_rate':m['fee_rate'],'fee_provenance':'retrospective_recorded_Gamma_feeSchedule',
                'local_metadata_recv_ms':None,'metadata_availability_assumed':True,'up_token_id':m['up_token_id'],'down_token_id':m['down_token_id'],
                'resolution_source_url':m['resolution_source_url']})
            constraint={'type':'constraint','recv_ms':low,'market_id':mid,'constraints_source':'retrospective_recorded_Gamma',
                'metadata_availability_assumed':True}
            if m['min_size'] is not None:constraint['min_order_size']=m['min_size']
            if m['tick'] is not None:constraint['up_tick_size']=m['tick']
            if any(k in constraint for k in ('min_order_size','up_tick_size')):write(f,constraint)
            hist=[];series=books.get(slug)
            for sec in range(int(math.ceil(low/1000)*1000),int(high)+1,1000):
                for delay in (0,500,600):
                    now=sec+delay
                    if now>high or now>=m['end_ms']:continue
                    value,receipt,status=series.before(now*1000,max_book_age_ms*1000) if series else (None,None,'missing')
                    book=None
                    if value is not None and np.isfinite(value[:4]).all():book=([[float(value[0]),float(value[1])]],[[float(value[2]),float(value[3])]])
                    elif value is not None:status='invalid_book'
                    counts['projection_'+status]+=1;at=receipt/1000 if receipt is not None else None
                    write(f,{'type':'book','recv_ms':now,'market_id':mid,'projection_asof_ms':now,
                        'up_asks':book[1] if book else [],'up_bids':book[0] if book else [],'down_asks':[],'down_bids':[],
                        'up_recv_ms':at,'down_recv_ms':None,'up_source_ts_ms':float(value[4])/1000 if value is not None and math.isfinite(value[4]) else None,
                        'up_healthy':bool(book),'down_healthy':False,'healthy':False,'execution_book_sides':['Up'],'execution_mode':'up_only_limited',
                        'fee_rate':m['fee_rate'],'fee_provenance':'retrospective_recorded_Gamma_feeSchedule','max_book_age_ms':max_book_age_ms,'up_status':status})
                    if delay==0:
                        vals,available=feature_row(spot,hist,now,m['end_ms'],book,at,max_source_age_ms)
                        features.update(k for k,v in vals.items() if v is not None)
                        write(f,{'type':'decision','recv_ms':now,'market_id':mid,'features':vals,'feature_available_ms':available})
            if m['winner'] is not None:
                outcomes.append({'type':'outcome','recv_ms':settlement_cutoff_ms,'resolved_ms':m['end_ms'],'market_id':mid,
                    'winner':m['winner'],'source':'gamma','local_outcome_recv_ms':None,'settlement_availability_assumed':True,
                    'label_verified':m['label_verified_by_recorded_gamma'],
                    'label_provenance':'recorded raw Gamma outcomePrices and published outcome CSV; no underlying-price proxy'})
            else:counts['markets_missing_outcome']+=1
        for row in outcomes:write(f,row)
    pending.replace(output)
    result={'schema':VERSION,'identity':identity,'output':str(output.resolve()),'output_sha256':sha(output),'header':header,
        'counts':dict(emitted),'source_quality_counts':dict(counts),'spot_source_clock_checks':dict(source_checks),
        'book_source_clock_checks':dict(book_checks),'nonmissing_features':dict(features),'input_member_counts':dict(members),
        'registered_markets':len(chosen),'labelled_markets':len(outcomes),'elapsed_seconds':time.perf_counter()-started,
        'formal_oos_result':None,'strict_execution_admitted':False,'new_backtests':0,'pnl':None}
    Path(str(output)+'.manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True);p.add_argument('--out',required=True)
    p.add_argument('--settlement-cutoff-ms',required=True,type=float);p.add_argument('--max-book-age-ms',type=int,default=250)
    p.add_argument('--max-source-age-ms',type=int,default=1000);a=p.parse_args()
    r=normalize(a.archive,a.out,settlement_cutoff_ms=a.settlement_cutoff_ms,max_book_age_ms=a.max_book_age_ms,max_source_age_ms=a.max_source_age_ms)
    print(json.dumps({k:v for k,v in r.items() if k not in ('header','input_member_counts')},indent=2))

if __name__=='__main__':main()
