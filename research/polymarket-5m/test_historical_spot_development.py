import csv,gzip,json,tempfile,unittest
from pathlib import Path
import historical_spot_development as h

def gzrows(path,rows):
    with gzip.open(path,'wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')

def fixture(d):
    p=Path(d)/'parent';root=p/'bundle-btc/strict';root.mkdir(parents=True)
    raw=p/'raw/day';raw.mkdir(parents=True)
    projection=Path(d)/'projection';projection.mkdir()
    manifest={'run_id':'synthetic','recorder_complete':True,'missing_resolved_market_ids':[],
              'started_ms':1000,'ended_ms':5000,'complete':False}
    (root/'manifest.json').write_text(json.dumps(manifest))
    mapping={'market_id':'m','start_ts':0,'up_token_id':'u','down_token_id':'d','updated_at':400,'fee_rate':.07}
    outcome={'market_id':'m','winner':'Up','resolution_ts':300,'source':'gamma','recorded_at':301}
    for name,row in [('market_registry.csv.gz',mapping),('market_outcomes.csv.gz',outcome)]:
        with gzip.open(root/name,'wt') as f:
            w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
    rows=[{'kind':'clob_connection','recv_ms':1000,'seq':0,'source_ts_ms':None,'connection_epoch':1}]
    for i,token in enumerate(('u','d'),1):
        rows.append({'kind':'clob_snapshot','recv_ms':1001,'seq':i,'source_ts_ms':1001,'connection_epoch':1,
            'market_id':'m','asset_id':token,'bids':[{'price':.4,'size':5}],'asks':[{'price':.5,'size':5}]})
    rows.append({'kind':'clob_error','recv_ms':5000,'seq':3,'source_ts_ms':None,'connection_epoch':1})
    gzrows(root/'clob_events.jsonl.gz',rows)
    spot=[]
    for i,kind in enumerate(('spot_connection','spot_trade','spot_disconnect')):
        spot.append({'kind':kind,'stream':'spot','recv_ms':[1000,1001,5000][i],'seq':i,'stream_sequence':i,
                     'source_ts_ms':1000+i,'connection_epoch':1,'price':80000,'size':1})
    gzrows(projection/'source_events.jsonl.gz',spot)
    hashes={n:h.sha(root/n) for n in ('manifest.json','clob_events.jsonl.gz','market_registry.csv.gz','market_outcomes.csv.gz')}
    hashes['source_events.jsonl.gz']='synthetic-parent'
    evidence={'run_id':'synthetic','stream_validation_errors':{},'count_mismatches':{},'input_sha256':hashes,
              'validator_sha256':h.sha(h.archive.__file__)}
    evidence_path=Path(d)/'audit.json';evidence_path.write_text(json.dumps(evidence))
    (projection/'manifest.json').write_text(json.dumps({'output_sha256':h.sha(projection/'source_events.jsonl.gz'),
        'parent_sha256':'synthetic-parent','validator_sha256':h.sha(h.archive.__file__)}))
    rawrow={'conditionId':'m','slug':'btc-updown-5m-0','_recorded_at_ms':1000,'outcomes':['Up','Down'],
            'clobTokenIds':['u','d'],'feeSchedule':{'exponent':1,'takerOnly':True,'rate':.07},
            'orderMinSize':5,'orderPriceMinTickSize':.01}
    gzrows(raw/'markets.jsonl.gz',[rawrow])
    return p,projection,evidence_path,raw/'markets.jsonl.gz',rawrow

class Tests(unittest.TestCase):
    def test_first_receipt_not_terminal_registry(self):
        with tempfile.TemporaryDirectory() as d:
            p,proj,a,_,_=fixture(d);out=Path(d)/'out.gz'
            r=h.normalize(p,proj,a,out)
            rows=list(h.replay.read_jsonl(out));m=next(x for x in rows if x['type']=='market')
            self.assertEqual(m['recv_ms'],1000);self.assertEqual(rows[0]['split'],'development')
            self.assertFalse(rows[0]['original_manifest_complete'])
            self.assertTrue(rows[0]['provenance']['source'])
            self.assertEqual(r['counts']['market'],1)
    def test_mapping_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,proj,a,path,r=fixture(d);r['clobTokenIds']=['wrong','d'];gzrows(path,[r])
            with self.assertRaises(ValueError):h.normalize(p,proj,a,Path(d)/'out.gz')
    def test_audited_clob_drift_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,proj,a,_,_=fixture(d);gzrows(p/'bundle-btc/strict/clob_events.jsonl.gz',[])
            with self.assertRaises(ValueError):h.normalize(p,proj,a,Path(d)/'out.gz')

if __name__=='__main__':unittest.main()
