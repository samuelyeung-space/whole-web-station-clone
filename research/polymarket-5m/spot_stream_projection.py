"""Project a multiplexed archive onto its independently sequenced BTC spot stream.

Unrelated futures numeric errors never become spot trades. Global receipt and
sequence integrity must pass for EVERY row; spot connection/sequence/numeric
contracts must pass for EVERY selected row. Nothing is imputed or fitted.
Original global seq is preserved as parent_seq; new seq is an explicit derived
file ordinal, NOT a claimed exchange sequence. Missing aggressor side stays missing.
"""
import argparse,gzip,hashlib,json,math,time
from collections import Counter
from pathlib import Path
import h_replay_archive as archive


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def project_rows(rows, counts):
    previous_recv=-math.inf;previous_seq=-1;selected=0
    for row in rows:
        seq=row.get('seq');recv=row.get('recv_ms');stream=row.get('stream')
        if type(seq) is not int or seq!=previous_seq+1:raise ValueError('parent global sequence gap')
        if type(recv) not in (int,float) or not math.isfinite(recv) or recv<previous_recv:
            raise ValueError('parent global receipt regression or invalid clock')
        if stream not in ('spot','futures','futures_trade','deribit'):
            raise ValueError('unknown parent stream')
        previous_seq,previous_recv=seq,recv
        counts['parent_rows']+=1;counts['parent_'+stream]+=1
        if stream!='spot':
            counts['excluded_'+str(row.get('kind'))]+=1
            if row.get('kind')=='futures_trade':
                if type(row.get('price')) not in (int,float) or row['price']<=0:
                    counts['excluded_nonpositive_or_missing_futures_price']+=1
            continue
        if row.get('kind') not in ('spot_connection','spot_trade','spot_bbo','spot_disconnect'):
            raise ValueError('unknown spot event')
        out=dict(row);out['parent_seq']=seq;out['seq']=selected;selected+=1
        counts['selected_'+row['kind']]+=1
        if row['kind']=='spot_trade':
            for k in ('price','size','aggressor_side','buyer_is_maker','trade_id'):
                counts['spot_field_'+k]+=row.get(k) is not None
        yield out


def project(source,destination):
    started=time.perf_counter();source=Path(source);destination=Path(destination)
    code_hash=sha(__file__);parent_hash=sha(source)
    if destination.exists():raise ValueError('destination already exists; verify its manifest before reuse')
    destination.mkdir(parents=True);pending=destination/'spot.pending.jsonl.gz'
    counts=Counter()
    try:
        with gzip.open(source,'rt') as f,gzip.open(pending,'wt',compresslevel=1) as out:
            for row in project_rows((json.loads(line) for line in f),counts):
                out.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
        validated=Counter(row['kind'] for row in archive.iter_normalized_events(pending,family='source'))
        if not validated['spot_connection'] or not validated['spot_trade'] or not validated['spot_disconnect']:
            raise ValueError('spot connection/trade/disconnect required')
        final=destination/'source_events.jsonl.gz';pending.replace(final)
        result={'schema':'spot-stream-projection-v1','parent_sha256':parent_hash,
            'output_sha256':sha(final),'code_sha256':code_hash,'validator_sha256':sha(archive.__file__),
            'counts':dict(counts),'validated_counts':dict(validated),'elapsed_seconds':time.perf_counter()-started,
            'purpose':'development-only spot-dependent research, not admission of futures-dependent strategies',
            'missing_fields_imputed':False,'market_pnl':None,'new_strategy_replays':0,
            'limitations':['Does not certify CLOB, metadata, outcomes, actual fills, fees or cash spendability',
                           'Connection and file continuity are recorded continuity, not proof of zero upstream loss']}
        (destination/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
    except Exception:
        # Failed files stay explicitly pending; no reusable manifest is published.
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(project(a.source,a.out),indent=2))
