"""Synthetic counterexamples for the separate legacy historical lane."""
import csv
import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
import legacy_up_l1_adapter as legacy
import replay_dataset

class LegacyL1Tests(unittest.TestCase):
    def metadata(self,winner='Up'):
        return {'slug':'btc-updown-5m-300','start_sec':300,'end_sec':600,'resolved':winner is not None,
            'raw':{'conditionId':'market','outcomes':'["Up","Down"]','clobTokenIds':'["u","d"]',
                'outcomePrices':'["1","0"]' if winner=='Up' else '["0.5","0.5"]',
                'feeSchedule':{'rate':.07,'exponent':1,'takerOnly':True},'orderPriceMinTickSize':.001,'orderMinSize':5,
                'resolutionSource':'https://data.chain.link/streams/btc-usd-twap-60s-streams'}}
    def make_archive(self,root,winner='Up'):
        members={
            'bundle-btc/data/polymarket/daily/markets/BTC-5m/day.jsonl.gz':gzip.compress((json.dumps(self.metadata(winner))+'\n').encode()),
            'bundle-btc/latency/binance_trades.jsonl.gz':gzip.compress(('\n'.join(json.dumps({'trade_ts':t,'receive_ts':t+.001,'price':100+t/10000}) for t in range(230,310))+'\n').encode())}
        buff=io.StringIO();fields=['market_id','source_ts','receive_ts','best_bid','best_ask','bids_json','asks_json']
        writer=csv.DictWriter(buff,fieldnames=fields);writer.writeheader()
        for n in range(81):
            t=300+n/10
            writer.writerow({'market_id':'btc-updown-5m-300','source_ts':t,'receive_ts':t+.001,'best_bid':.4,'best_ask':.5,'bids_json':'[[0.4,5]]','asks_json':'[[0.5,6]]'})
        members['bundle-btc/latency/runtime.poly_probability_observations_v1.csv.gz']=gzip.compress(buff.getvalue().encode())
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as tar:
            for name,body in members.items():
                info=tarfile.TarInfo(name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
        path=root/'a.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('raw.tgz',data.getvalue())
        return path
    def test_native_l1_best_price_disagreement_invalidates(self):
        row={'best_bid':.4,'best_ask':.49,'bids_json':'[[0.4,5]]','asks_json':'[[0.5,6]]'}
        import math
        self.assertTrue(math.isnan(legacy.l1(row)[0]))
    def test_real_fields_and_no_down_reconstruction(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);arc=self.make_archive(root);out=root/'events.gz'
            r=legacy.normalize(arc,out,settlement_cutoff_ms=700000)
            rows=[json.loads(x) for x in gzip.open(out,'rt')]
            self.assertEqual(r['registered_markets'],1);self.assertFalse(r['strict_execution_admitted'])
            constraint=next(x for x in rows if x['type']=='constraint')
            self.assertEqual(constraint['up_tick_size'],.001)
            self.assertTrue(constraint['metadata_availability_assumed'])
            for b in (x for x in rows if x['type']=='book'):
                self.assertFalse(b['healthy']);self.assertIsNone(b['down_recv_ms']);self.assertEqual(b['down_asks'],[])
                if b['up_recv_ms'] is not None:self.assertLess(b['up_recv_ms'],b['recv_ms'])
            self.assertEqual(rows[-1]['recv_ms'],700000);self.assertEqual(rows[-1]['winner'],'Up')
            self.assertEqual(replay_dataset._scan(out)['counts']['outcome'],1)
    def test_unresolved_market_retained_not_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);arc=self.make_archive(root,winner=None);out=root/'events.gz'
            r=legacy.normalize(arc,out,settlement_cutoff_ms=700000)
            self.assertEqual(r['registered_markets'],1);self.assertEqual(r['labelled_markets'],0)
            self.assertEqual(r['source_quality_counts']['markets_missing_outcome'],1)
            self.assertEqual(r['counts'].get('outcome',0),0)
            self.assertGreater(r['counts']['decision'],0)
    def test_no_early_settlement_cutoff(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);arc=self.make_archive(root)
            with self.assertRaisesRegex(ValueError,'after every market end'):
                legacy.normalize(arc,root/'events.gz',settlement_cutoff_ms=600000)

if __name__=='__main__':unittest.main()
