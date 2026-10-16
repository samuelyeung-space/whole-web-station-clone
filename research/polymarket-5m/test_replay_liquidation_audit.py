import math
import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from replay_liquidation_audit import Account, audit, mark_position
from verify_liquidation_artifact import sha, verify


def book(**change):
    out={'recv_ms':1000,'up_recv_ms':1000,'down_recv_ms':1000,
        'up_healthy':True,'down_healthy':True,'up_bids':[[.5,100]],
        'down_bids':[[.4,100]],'fee_rate':.07}
    out.update(change);return out


def attempt(aid='a',quantity=100):
    return {'attempt_id':aid,'candidate_id':'c','latency_ms':500,'market_id':'m','side':'Up','filled_shares':quantity,
        'match_ms':1000,'settlement_ms':2000}


def cash(kind,at=1000,seq=1,aid='a',balance=40,reserved=0,locked=0):
    return {'event_type':kind,'event_ms':at,'event_sequence':seq,'attempt_id':aid,
        'cash_usd':balance,'reserved_usd':reserved,'locked_payout_usd':locked}


class LiquidationTests(unittest.TestCase):
    def test_full_native_depth_and_fee(self):
        lo,hi,missing,why=mark_position(100,'Up',book(),1000,5000)
        self.assertAlmostEqual(lo,48.25);self.assertEqual(lo,hi)
        self.assertEqual((missing,why),(0,'fully_marked'))

    def test_short_depth_is_interval_not_zero(self):
        lo,hi,missing,why=mark_position(100,'Up',book(up_bids=[[.5,20]]),1000,5000)
        self.assertAlmostEqual(lo,9.65);self.assertAlmostEqual(hi,89.65)
        self.assertEqual((missing,why),(80,'insufficient_bid_depth'))

    def test_stale_side_cannot_use_envelope(self):
        self.assertEqual(mark_position(100,'Up',book(up_recv_ms=500),1000,5000)[:3],(0,100,100))
        self.assertEqual(mark_position(100,'Up',book(up_recv_ms=1001),1000,5000)[3],'stale_or_future_native_book')

    def test_health_is_native_selected_side(self):
        self.assertEqual(mark_position(100,'Up',book(up_healthy=False),1000,5000)[3],'unhealthy_native_book')
        self.assertEqual(mark_position(100,'Up',book(down_healthy=False),1000,5000)[3],'fully_marked')

    def test_missing_and_closed_not_fabricated(self):
        self.assertEqual(mark_position(100,'Up',None,1000,5000)[3],'missing_book')
        self.assertEqual(mark_position(100,'Up',book(),1000,1000)[3],'closed_or_unknown_market')
        self.assertEqual(mark_position(100,'Up',book(up_bids=None),1000,5000)[3],'missing_native_bids')

    def test_fee_and_bid_schema_fail_closed(self):
        for b in (book(fee_rate=None),book(up_bids=[[float('nan'),1]]),book(up_bids=[[.5,-1]])):
            self.assertEqual(mark_position(100,'Up',b,1000,5000)[:3],(0,100,100))

    def test_reservation_not_subtracted_from_equity_twice(self):
        a=Account('x','c',500,[attempt()],[cash('reservation',balance=1000,reserved=60)])
        s=a.snapshot(1000,{}, {'m':5000})
        self.assertEqual(s['available_cash_usd'],940)
        self.assertEqual(s['quote_model_equity_low_usd'],1000)

    def test_combined_positions_do_not_reuse_bid_depth(self):
        a=Account('x','c',500,[attempt('a',60),attempt('b',60)],
            [cash('fill',aid='a'),cash('fill',seq=2,aid='b',balance=10)])
        s=a.snapshot(1000,{'m':book()}, {'m':5000})
        self.assertAlmostEqual(s['unknown_shares'],20)
        self.assertAlmostEqual(s['quote_model_equity_low_usd'],58.25)

    def test_settlement_removes_position_and_locked_receivable_not_cash(self):
        a=Account('x','c',500,[attempt()], [cash('fill'),cash('settlement',at=2000,seq=2,locked=100),
            cash('payout_release',at=3000,seq=3,balance=140)])
        s=a.snapshot(2000,{'m':book()}, {'m':5000})
        self.assertEqual(s['open_positions'],0);self.assertEqual(s['available_cash_usd'],40)
        self.assertEqual(s['quote_model_equity_low_usd'],140)
        self.assertEqual(a.snapshot(3000,{}, {})['quote_model_equity_low_usd'],140)

    def test_drawdown_interval_and_expiry(self):
        a=Account('x','c',500,[attempt()],[cash('fill',balance=940)])
        s=a.snapshot(1000,{'m':book()}, {'m':5000})
        self.assertAlmostEqual(a.dd_low,11.75);self.assertAlmostEqual(a.dd_high,11.75)
        self.assertGreater(a.next_expiry,1250)
        a.snapshot(math.nextafter(1250,math.inf),{'m':book()}, {'m':5000})
        self.assertEqual(a.dd_low,11.75);self.assertAlmostEqual(a.dd_high,60)
        self.assertAlmostEqual(a.max_width,100)

    def test_end_to_end_expiry_and_post_close_settlement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'run';sc=run/'shares100_base_release0';sc.mkdir(parents=True)
            aa=attempt();aa.update(net_pnl_usd=40,settled=True)
            with gzip.open(sc/'attempts.jsonl.gz','wt') as f:f.write(json.dumps(aa)+'\n')
            cc=[cash('fill',balance=940),cash('settlement',at=6000,seq=2,balance=1040)]
            aa['settlement_ms']=6000
            with gzip.open(sc/'attempts.jsonl.gz','wt') as f:f.write(json.dumps(aa)+'\n')
            for c in cc:c.update(candidate_id='c',latency_ms=500,report_cash_basis='engine_recorded_event')
            with (sc/'cash_ledger.csv').open('w') as f:
                writer=csv.DictWriter(f,fieldnames=list(cc[0]));writer.writeheader();writer.writerows(cc)
            assessment={'modes':{'isolated':{'run_path':str(run),'metrics':[{'candidate_id':'c','latency_ms':500,
                'scenario':'shares100_base_release0','ending_cash_usd':1040,'status':'simulated_complete',
                'net_pnl_usd':40,'max_realized_drawdown_usd':0,'initial_cash_usd':1000,'requested_shares':100}]}}}
            ap=root/'assessment.json';ap.write_text(json.dumps(assessment))
            ep=root/'events.jsonl';ep.write_text('\n'.join(json.dumps(r) for r in [
                {'type':'market','recv_ms':500,'market_id':'m','end_ms':5000},
                {'type':'book','market_id':'m',**book()}])+'\n')
            (run/'registration.json').write_text(json.dumps({'events_sha256':sha(ep),
                'strategies':{'engine_code_sha256':sha(Path(__file__).with_name('replay_engine.py'))}}))
            audit(ep,ap,root/'output');r=json.loads((root/'output/assessment.json').read_text())
            self.assertEqual(r['new_economic_replays'],0)
            self.assertEqual(r['account_results'][0]['final']['cash_usd'],1040)
            with gzip.open(root/'output/snapshots.jsonl.gz','rt') as f:snapshots=[json.loads(l) for l in f]
            self.assertTrue(any(1250<s['recv_ms']<1251 and s['unknown_shares']==100 for s in snapshots))
            self.assertTrue(any(s['recv_ms']==5000 for s in snapshots))
            self.assertEqual(snapshots[-1]['open_positions'],0)
            verify(root/'output',ap)
            (run/'registration.json').write_text(json.dumps({'events_sha256':'wrong_tape'}))
            with self.assertRaisesRegex(ValueError,'risk tape differs'):
                verify(root/'output',ap)


if __name__=='__main__':unittest.main()
