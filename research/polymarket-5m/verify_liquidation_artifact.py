"""Independent post-build checks for indicative risk artifacts, never rescore."""
import argparse
from collections import defaultdict
import gzip,hashlib,json,math
from pathlib import Path


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def close(a,b):
    if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-7):raise ValueError(f'mismatch {a} != {b}')


def verify(directory,original_assessment):
    root=Path(directory);a=json.loads((root/'assessment.json').read_text());reg=a['registration']
    original=json.loads(Path(original_assessment).read_text())
    if sha(original_assessment)!=reg['assessment_sha256']:raise ValueError('original assessment changed')
    if sha(root/'snapshots.jsonl.gz')!=a['snapshot_sha256']:raise ValueError('snapshot hash mismatch')
    count=0;by_account=defaultdict(lambda:{'peak_low':1000.,'peak_high':1000.,'dd_low':0.,'dd_high':0.,
        'snapshots':0,'open':0,'full':0,'last_time':-math.inf,'last':None})
    for mode,metadata in original['modes'].items():
        run=Path(metadata['run_path']);prior=json.loads((run/'registration.json').read_text())
        if reg['events_sha256']!=prior['events_sha256']:raise ValueError('risk tape differs from original economic tape')
        for name in ('attempts.jsonl.gz','cash_ledger.csv'):
            if sha(run/'shares100_base_release0'/name)!=reg['ledger_sha256'][mode+'/'+name]:
                raise ValueError('original economic ledger changed')
    with gzip.open(root/'snapshots.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line);key=(r['mode'],r['candidate_id'],r['latency_ms']);d=by_account[key]
            low=r['quote_model_equity_low_usd'];high=r['quote_model_equity_high_usd']
            if r['recv_ms']<=d['last_time'] or low>high+1e-7:raise ValueError('invalid snapshot')
            if r['available_cash_usd'] < -1e-7:raise ValueError('negative spendable balance')
            close(r['available_cash_usd'],r['cash_usd']-r['reserved_usd'])
            close(high-low,r['unknown_shares'])
            if low+1e-7<r['cash_usd']+r['locked_known_payout_usd']:raise ValueError('cash counted negatively')
            d['dd_low']=max(d['dd_low'],d['peak_low']-high);d['dd_high']=max(d['dd_high'],d['peak_high']-low)
            d['peak_low']=max(d['peak_low'],low);d['peak_high']=max(d['peak_high'],high)
            d['snapshots']+=1;d['open']+=r['open_positions']>0
            d['full']+=r['open_positions']>0 and r['unknown_shares']<=1e-9
            d['last_time']=r['recv_ms'];d['last']=r;count+=1
    if count!=a['account_snapshot_rows'] or len(by_account)!=len(a['account_results']):raise ValueError('snapshot count mismatch')
    for result in a['account_results']:
        key=(result['mode'],result['candidate_id'],result['latency_ms']);d=by_account[key]
        for name,computed in [('quote_model_drawdown_low_usd',d['dd_low']),('quote_model_drawdown_high_usd',d['dd_high']),
                ('snapshots',d['snapshots']),('open_inventory_snapshots',d['open']),('fully_marked_open_snapshots',d['full'])]:
            close(result[name],computed)
        last=d['last']
        if last['open_positions'] or last['locked_known_payout_usd']:raise ValueError('terminal inventory not empty')
        close(last['quote_model_equity_low_usd'],last['cash_usd'])
        close(last['quote_model_equity_high_usd'],last['cash_usd'])
        close(last['cash_usd'],1000+result['net_pnl_usd_unchanged'])
    receipt={'schema':'indicative-liquidation-independent-check-v1','status':'passed','accounts':len(by_account),
        'snapshot_rows':count,'original_tape_hash_matches_all_economic_registrations':True,
        'original_ledger_hashes_match':True,'ending_cash_and_pnl_unchanged':True,'snapshot_crc_eof_verified':True,
        'risk_assessment_sha256':sha(root/'assessment.json'),'verifier_code_sha256':sha(__file__),
        'new_economic_replays':0,'formal_oos_result':None}
    (root/'independent_check.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);p.add_argument('--original-assessment',required=True)
    args=p.parse_args();verify(args.directory,args.original_assessment)
