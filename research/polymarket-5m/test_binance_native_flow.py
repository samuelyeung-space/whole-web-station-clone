"""Synthetic parser tests only. No historical trade or profit evidence."""
import copy
import unittest
from binance_native_flow import NativeFlowTape, native_receipt
from historical_flow_receipts import CoverageInterval


def raw(tid=1, maker=False):
    return dict(e='trade',E=59000,s='BTCUSDT',t=tid,p='100',q='2',T=58999,m=maker,M=True)


class Tests(unittest.TestCase):
    def tape(self,stream='trade'):
        t=NativeFlowTape(stream_kind=stream,time_unit='MILLISECOND')
        t.add_coverage(CoverageInterval('binance','BTCUSDT','run',0,60000,60000,'complete','synthetic test only'))
        return t

    def add(self,t,p=None,recv=59001):
        return t.add_trade(raw() if p is None else p,local_recv_ms=recv,capture_run_id='run')

    def test_native_side_boolean_semantics(self):
        for maker,side in [(True,-1),(False,1)]:
            t=self.tape();self.add(t,raw(maker=maker))
            self.assertEqual(t.features(60000)['features']['flow_60s'],side)

    def test_preserves_exact_id_and_raw_decimal_and_both_clocks(self):
        t=self.tape();p=raw(9007199254740993123);p['q']='2.000000000000001'
        n=self.add(t,p)
        self.assertEqual(n['row']['trade_id'],'trade:9007199254740993123')
        self.assertEqual(n['raw_payload'],p)
        self.assertEqual(n['source_event_ms'],59000)
        self.assertEqual(n['source_trade_ms'],58999)
        self.assertEqual(n['local_recv_ms'],59001)
        p['q']='9';self.assertNotEqual(n['raw_payload']['q'],p['q'])

    def test_uppercase_M_not_aggressor(self):
        t=self.tape();p=raw();del p['m'];self.add(t,p)
        self.assertIsNone(t.features(60000)['features']['flow_15s'])
        self.assertEqual(t.native_receipts[0]['side_provenance'],'missing_native_m')

    def test_missing_quantity_never_unit_trade(self):
        t=self.tape();p=raw();del p['q']
        with self.assertRaises(ValueError):self.add(t,p)
        self.assertEqual(t.features(60000)['audit']['flow_15s']['status'],'blocked_native_ingestion_error')

    def test_string_boolean_rejected_and_not_silently_skipped(self):
        t=self.tape();self.add(t)
        with self.assertRaises(ValueError):self.add(t,raw(2,'false'),59002)
        self.assertIsNone(t.features(60000)['features']['flow_60s'])
        with self.assertRaises(ValueError):self.add(t,raw(3),59003)

    def test_combined_payload_routes_exact_stream(self):
        t=self.tape();p={'stream':'btcusdt@trade','data':raw()}
        self.add(t,p);self.assertEqual(t.features(60000)['features']['flow_15s'],1)
        t=self.tape();p['stream']='ethusdt@trade'
        with self.assertRaises(ValueError):self.add(t,p)

    def test_quote_not_base_volume(self):
        t=self.tape();self.add(t);p=raw(2,True);p['p']='200';self.add(t,p,59002)
        self.assertAlmostEqual(t.features(60000)['features']['flow_15s'],-1/3)

    def test_duplicate_receipts_count_once_and_do_not_move_first_receipt(self):
        t=self.tape();self.add(t);self.add(t,recv=59002)
        self.assertEqual(len(t.native_receipts),2)
        self.assertEqual(t.features(60000)['audit']['flow_15s']['quote_volume'],200)

    def test_sub_float_decimal_conflict_not_hidden_by_conversion(self):
        t=self.tape();self.add(t);p=raw();p['q']='2.000000000000000000001'
        self.assertEqual(float(p['q']),2.)
        with self.assertRaisesRegex(ValueError,'conflicting native'):self.add(t,p,59002)

    def test_future_input_does_not_change_past(self):
        t=self.tape();self.add(t);before=copy.deepcopy(t.features(60000))
        p=raw(2,True);p['q']='1000';p['T']=58000
        self.add(t,p,60001)
        self.assertEqual(t.features(60000),before)

    def test_future_parse_error_does_not_filter_past(self):
        t=self.tape();self.add(t);before=copy.deepcopy(t.features(60000))
        p=raw(2);p['q']='bad'
        with self.assertRaises(ValueError):self.add(t,p,60001)
        self.assertEqual(t.features(60000),before)
        self.assertIsNone(t.features(60002)['features']['flow_15s'])

    def test_no_coverage_no_feature(self):
        t=NativeFlowTape(stream_kind='trade',time_unit='MILLISECOND');self.add(t)
        self.assertIsNone(t.features(60000)['features']['flow_60s'])

    def test_raw_and_aggregate_never_mixed(self):
        t=self.tape();p=raw();p.update(e='aggTrade',a=1,f=1,l=5)
        with self.assertRaises(ValueError):self.add(t,p)

    def test_aggregate_ranges_preserved_and_overlaps_rejected(self):
        t=self.tape('aggTrade');p=raw();p.update(e='aggTrade',a=1,f=10,l=12)
        n=self.add(t,p);self.assertEqual((n['first_raw_trade_id'],n['last_raw_trade_id']),(10,12))
        self.add(t,p,59002)
        p.update(a=2,f=12,l=14)
        with self.assertRaisesRegex(ValueError,'overlap'):self.add(t,p,59003)

    def test_float_ids_units_and_wrong_symbol_fail_closed(self):
        for field,value in [('t',9007199254740992.),('t',True),('s','ETHUSDT'),('q','NaN'),('q',True)]:
            p=raw();p[field]=value
            with self.assertRaises(ValueError):self.add(self.tape(),p)
        with self.assertRaises(ValueError):NativeFlowTape(stream_kind='trade',time_unit='MICROSECOND')

    def test_legacy_price_archive_cannot_be_normalized(self):
        p={'event':'BINANCE_WS_TRADE','trade_ts':58.999,'receive_ts':59.001,'price':100}
        with self.assertRaises(ValueError):self.add(self.tape(),p)


if __name__=='__main__':unittest.main()
