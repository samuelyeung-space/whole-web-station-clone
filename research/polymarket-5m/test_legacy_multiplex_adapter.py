"""Counterexamples for routing original multiplex receipt-clock prices."""
import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import legacy_multiplex_adapter as multiplex
import legacy_up_l1_adapter as legacy
import replay_dataset
import test_legacy_up_l1_adapter as fixtures


def replace_price_member(path, frames, keep_dedicated=False):
    with zipfile.ZipFile(path) as z:
        with tarfile.open(fileobj=io.BytesIO(z.read('raw.tgz')), mode='r:gz') as t:
            members={m.name:t.extractfile(m).read() for m in t if m.isfile()}
    if not keep_dedicated:
        del members['bundle-btc/latency/binance_trades.jsonl.gz']
    members['bundle-btc/latency/shadow_current.jsonl.gz']=gzip.compress(
        ('\n'.join(json.dumps(x) for x in frames)+'\n').encode())
    data=io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as t:
        for name, body in members.items():
            info=tarfile.TarInfo(name);info.size=len(body)
            t.addfile(info, io.BytesIO(body))
    with zipfile.ZipFile(path, 'w') as z:z.writestr('raw.tgz', data.getvalue())


class MultiplexTests(unittest.TestCase):
    def make(self, root, **kwargs):
        arc=fixtures.LegacyL1Tests().make_archive(root)
        frames=[{'event':'BINANCE_AGG_TRADE','trade_ts':t,'receive_ts':t+.001,'price':100+t/10000}
                for t in range(230,310)]
        replace_price_member(arc,frames,**kwargs)
        return arc

    def test_original_clocks_and_old_adapter_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);arc=self.make(root)
            spot=[r for k,_,_,r in multiplex.archive_rows(arc) if k=='btc_spot_trade']
            self.assertEqual(spot[0],{'trade_ts':230,'receive_ts':230.001,'price':100.023})
            with self.assertRaisesRegex(ValueError,'BTC prices required'):
                legacy.read_archive(arc)
            out=root/'tape.gz';result=multiplex.normalize(arc,out,settlement_cutoff_ms=700000)
            self.assertEqual(result['registered_markets'],1)
            self.assertEqual(result['header']['source_reference'],'polymarket_rtds_binance_relay')
            self.assertEqual(replay_dataset._scan(out)['counts']['outcome'],1)
            for row in (json.loads(x) for x in gzip.open(out,'rt')):
                if row['type']=='book':
                    self.assertEqual(row['down_asks'],[])
                    self.assertIsNone(row['down_recv_ms'])

    def test_no_receipt_fallback_and_no_other_event_substitution(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);arc=fixtures.LegacyL1Tests().make_archive(root)
            frames=[{'event':'BINANCE_AGG_TRADE','trade_ts':300,'price':100},
                    {'event':'COINBASE_TRADE','trade_ts':301,'receive_ts':301.1,'price':100},
                    {'event':'POLY_BOOK','trade_ts':302,'receive_ts':302.1,'price':.5}]
            replace_price_member(arc,frames)
            routed=[r for k,_,_,r in multiplex.archive_rows(arc) if k=='btc_spot_trade']
            self.assertEqual(len(routed),1)
            self.assertNotIn('receive_ts',routed[0])
            with self.assertRaisesRegex(ValueError,'BTC prices required'):
                multiplex.read_archive(arc)

    def test_duplicate_binance_sources_need_explicit_selection(self):
        with tempfile.TemporaryDirectory() as td:
            arc=self.make(Path(td),keep_dedicated=True)
            with self.assertRaisesRegex(ValueError,'multiple Binance members'):
                multiplex.read_archive(arc)

    def test_source_timestamp_is_not_changed_to_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            arc=fixtures.LegacyL1Tests().make_archive(Path(td))
            replace_price_member(arc,[{'event':'BINANCE_AGG_TRADE','trade_ts':300,
                                       'receive_ts':301.75,'price':100}])
            _,_,spot,_,_=multiplex.read_archive(arc)
            self.assertEqual(list(spot),[301750.,100.,300000.])


if __name__=='__main__':unittest.main()
