"""Focused counterexamples for native Chainlink input; no profit claims."""
import csv,gzip,io,json,tarfile,tempfile,unittest,zipfile
from pathlib import Path
import native_reference_adapter as native
from replay_dataset import _scan

class NativeReferenceTests(unittest.TestCase):
    def gamma(self,closed=True):
        return {'slug':'btc-updown-5m-300','conditionId':'m','outcomes':['Up','Down'],'clobTokenIds':['u','d'],'outcomePrices':['1','0'] if closed else ['0.5','0.5'],'closed':closed,'feeSchedule':{'rate':.07,'exponent':1,'takerOnly':True},'orderMinSize':5,'orderPriceMinTickSize':.01}
    def snapshot(self,token,now,src=None,ask=.5):
        return {'recv_ms':now,'msg':{'asset_id':token,'market':'m','timestamp':src if src is not None else now-10,'bids':[{'price':.4,'size':10}],'asks':[{'price':ask,'size':10}],'event_type':'book'}}
    def archive(self,root,raw=False,error=None,bbo=None,frames=None,raw_metadata=None):
        data={};gamma=self.gamma()
        if raw:
            data['raw/day/markets.jsonl']=raw_metadata or [gamma]
            data['raw/day/errors.jsonl']=[{'at':error,'where':'clob'}] if error else []
            data['raw/day/rtds.jsonl']=[{'recv_ms':t,'msg':{'topic':'crypto_prices_chainlink','payload':{'symbol':'btc/usd','timestamp':t-10,'value':100+t/100000}}} for t in range(290001,306001,100)]
            data['raw/day/clob.jsonl']=frames or []
        else:
            data['bundle-btc/data/polymarket/daily/markets/BTC-5m/day.jsonl.gz']=[{'slug':gamma['slug'],'start_sec':300,'end_sec':600,'resolved':True,'raw':gamma}]
            data['bundle-btc/data/polymarket/daily/book/BTC-5m/day.jsonl.gz']=[{'asset_id':s,'recv_ms':t,'payload':{'bids':[{'price':.4,'size':10}],'asks':[{'price':.5,'size':10}]}} for t in range(300001,306001,100) for s in ('u','d')]
            data['bundle-btc/data/polymarket/daily/best_bid_ask/BTC-5m/day.jsonl.gz']=bbo or []
            buff=io.StringIO();w=csv.DictWriter(buff,fieldnames=['feed_ts_ms','value','recv_ms']);w.writeheader()
            for t in range(290001,306001,100):w.writerow({'feed_ts_ms':t-10,'value':100+t/100000,'recv_ms':t})
            data['bundle-btc/data/chainlink/daily/prices/BTCUSD/day.csv.gz']=buff.getvalue().encode()
        bio=io.BytesIO()
        with tarfile.open(fileobj=bio,mode='w:gz') as tar:
            for name,rows in data.items():
                body=rows if isinstance(rows,bytes) else ('\n'.join(json.dumps(r) for r in rows)+'\n').encode()
                if name.endswith('.gz'):body=gzip.compress(body)
                info=tarfile.TarInfo(name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
        path=root/'a.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('raw.tgz',bio.getvalue())
        return path
    def run_case(self,**kwargs):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);root=Path(td.name);p=self.archive(root,**kwargs);out=root/'n.gz';result=native.normalize(p,out,settlement_cutoff_ms=700000)
        rows=[json.loads(x) for x in gzip.open(out,'rt')];_scan(out);return result,rows
    def test_explicit_chainlink_causality_and_missing_source_clocks(self):
        r,rows=self.run_case();self.assertEqual(r['header']['source_reference'],'chainlink');self.assertGreater(r['book_source_checks']['missing_source_timestamps'],0)
        books=[x for x in rows if x['type']=='book'];self.assertTrue(any(b['healthy'] and b['down_asks'] for b in books))
        for b in books:
            for side in ('up','down'):
                if b[side+'_recv_ms'] is not None:self.assertLess(b[side+'_recv_ms'],b['recv_ms'])
                self.assertIsNone(b[side+'_source_ts_ms'])
        for d in (x for x in rows if x['type']=='decision'):self.assertIsNone(d['features']['flow_15s'])
        self.assertEqual(rows[-1]['recv_ms'],700000)
    def test_empty_bbo_is_invalid_observation_not_forward_fill(self):
        r,rows=self.run_case(bbo=[{'asset_id':'u','recv_ms':302999,'payload':{'best_bid':'','best_ask':''}}])
        b=next(x for x in rows if x['type']=='book' and x['recv_ms']==303000);self.assertFalse(b['up_healthy']);self.assertEqual(r['quality_counts']['invalid_bbo_rows'],1)
    def test_disconnect_delta_cannot_revive_old_book(self):
        frames=[self.snapshot('u',300800),self.snapshot('d',300800),{'recv_ms':301900,'msg':{'event_type':'price_change','timestamp':301890,'market':'m','price_changes':[{'asset_id':'u','price':.5,'size':8,'side':'SELL'}]}},self.snapshot('u',302900),self.snapshot('d',302900),self.snapshot('u',303100)]
        r,rows=self.run_case(raw=True,error=301000,frames=frames)
        b=next(x for x in rows if x['type']=='book' and x['recv_ms']==302000);self.assertFalse(b['up_healthy']);self.assertEqual(b['up_status'],'recorded_clob_error')
        b=next(x for x in rows if x['type']=='book' and x['recv_ms']==303000);self.assertTrue(b['healthy'])
    def test_raw_older_source_cannot_refresh_age_or_price(self):
        frames=[self.snapshot('u',300800,300790),self.snapshot('d',300800,300790),self.snapshot('u',300900,300780,ask=.45),self.snapshot('u',301100),self.snapshot('d',301100)]
        r,rows=self.run_case(raw=True,frames=frames)
        b=next(x for x in rows if x['type']=='book' and x['recv_ms']==301000);self.assertEqual(b['up_asks'][0][0],.5);self.assertEqual(b['up_recv_ms'],300800);self.assertGreater(r['quality_counts']['raw_source_regressions_rejected'],0)
    def test_retrospective_tick_cannot_rewrite_earlier_constraints(self):
        early=self.gamma(False);late=self.gamma(True);late['orderPriceMinTickSize']=.001
        frames=[self.snapshot('u',300800),self.snapshot('d',300800),{'recv_ms':301500,'msg':{'asset_id':'u','market':'m','timestamp':301490,'event_type':'tick_size_change','old_tick_size':.01,'new_tick_size':.001}},self.snapshot('u',302900),self.snapshot('d',302900),self.snapshot('u',303100)]
        r,rows=self.run_case(raw=True,raw_metadata=[early,late],frames=frames)
        cs=[x for x in rows if x['type']=='constraint'];self.assertEqual(cs[0]['up_tick_size'],.01);self.assertTrue(cs[0]['metadata_availability_assumed']);self.assertEqual(cs[1]['up_tick_size'],.001);self.assertEqual(cs[1]['recv_ms'],301500);self.assertFalse(cs[1]['metadata_availability_assumed'])
        self.assertEqual(r['quality_counts']['retrospective_tick_variation_not_backfilled'],1)

if __name__=='__main__':unittest.main()
