import gzip,io,json,tarfile,tempfile,unittest,zipfile
from pathlib import Path
from legacy_recording_cache import clock,envelope,ingest

def fixture(path):
    payloads={
      'bundle-btc/latency/binance_trades.jsonl.gz': '\n'.join(json.dumps(r) for r in [
        {'receive_ts':10.,'trade_ts':9.,'price':100.},
        {'receive_ts':9.,'trade_ts':8.,'price':99.}])+'\n',
      'bundle-btc/latency/runtime_1000.market_outcomes.csv.gz':'market_id,winner\nbtc-updown-5m-0,Up\n',
      'bundle-btc/data/polymarket/daily/markets/BTC-5m/x.jsonl.gz':json.dumps({'slug':'btc-updown-5m-0','raw':{'closedTime':'later'}})+'\n'}
    tgz=io.BytesIO()
    with tarfile.open(fileobj=tgz,mode='w:gz') as t:
        for name,body in payloads.items():
            data=gzip.compress(body.encode()); member=tarfile.TarInfo(name);member.size=len(data)
            t.addfile(member,io.BytesIO(data))
    with zipfile.ZipFile(path,'w') as z:z.writestr('raw.tgz',tgz.getvalue())

class Tests(unittest.TestCase):
    def test_clock_units(self): self.assertEqual(clock({'receive_ts':1.2}),(1200.,'receive_ts'))
    def test_missing_is_none(self):self.assertEqual(clock({'source_ts':1,'closedTime':2,'updatedAt':3}),(None,None))
    def test_bool_clock_rejected(self):
        with self.assertRaises(ValueError):clock({'recv_ms':True})
    def test_nan_clock_rejected(self):
        with self.assertRaises(ValueError):clock({'recv_ms':float('nan')})
    def test_raw_preserved(self):
        raw={'receive_ts':2.,'price':5.,'unknown':{'a':[1]}}
        e=envelope(raw,'btc_spot_trade','x',1)
        self.assertEqual(e['raw'],raw);self.assertNotIn('aggressor_side',e['raw'])
    def test_ingest_idempotent_and_no_fake_clocks(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);fixture(p/'x.zip');a=ingest(p/'x.zip',p/'cache');b=ingest(p/'x.zip',p/'cache')
            self.assertFalse(a['reused']);self.assertTrue(b['reused'])
            self.assertEqual(a['cache_key'],b['cache_key'])
            m=a['manifest']; self.assertEqual(m['total_rows'],4);self.assertIsNone(m['pnl'])
            trade=m['streams'][0]; self.assertEqual(trade['receipt_regressions'],1)
            self.assertEqual(trade['missing_counts']['size'],2)
            self.assertEqual(m['streams'][1]['missing_counts']['local_receipt_clock'],1)
            self.assertIsNone(m['markets'][0]['local_metadata_recv_ms'])
            target=p/'cache'/a['cache_key']/trade['cache_file']
            target.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):ingest(p/'x.zip',p/'cache')

if __name__=='__main__':unittest.main()
