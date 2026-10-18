import copy
import unittest
from capture_tail_contract import check


class TailContractTests(unittest.TestCase):
    def runtime(self):
        return dict(started_ms=1000,ended_ms=100000,complete=True,run_id='synthetic',
            writer=dict(accepted=10,written=10,flushed=10,pending_bytes=0,error=None,
                latest_stream_enqueue_ms={'binance-strict':99900,'clob-btc':99900,'rtds':99900}))

    def test_passed_tail_never_admits_replay_or_full_days(self):
        r=check(self.runtime());self.assertTrue(r['status'].startswith('tail_contract_passed'))
        self.assertFalse(r['execution_input_admitted']);self.assertFalse(r['complete_days_verified'])
        self.assertFalse(r['quotes_or_outcomes_read']);self.assertFalse(r['intrarun_continuity_verified'])
        self.assertEqual(r['financial_replays'],0);self.assertIsNone(r['formal_oos_result'])

    def test_writer_success_does_not_mask_stale_feed(self):
        x=self.runtime();x['writer']['latest_stream_enqueue_ms']['rtds']=1000
        r=check(x);self.assertTrue(r['writer_drained']);self.assertEqual(r['status'],'blocked_tail_contract')
        self.assertEqual(next(s['reason'] for s in r['streams'] if s['stream']=='rtds'),'stale_tail')

    def test_missing_boolean_nan_or_future_timestamp_fails_closed(self):
        for stamp in (None,True,float('nan'),float('inf'),100001,999):
            with self.subTest(stamp=stamp):
                x=self.runtime();x['writer']['latest_stream_enqueue_ms']['rtds']=stamp
                self.assertEqual(check(x)['status'],'blocked_tail_contract')

    def test_incomplete_and_undrained_writer_block_even_fresh_tail(self):
        for edit in ('complete','pending','error','counts','bool_count'):
            x=self.runtime()
            if edit=='complete':x['complete']=False
            elif edit=='pending':x['writer']['pending_bytes']=1
            elif edit=='error':x['writer']['error']='failure'
            elif edit=='counts':x['writer']['written']=9
            else:x['writer'].update(accepted=True,written=True,flushed=True)
            self.assertEqual(check(x)['status'],'blocked_tail_contract')

    def test_age_boundary_and_shutdown_drain_remain_visible(self):
        x=self.runtime();x['writer']['latest_stream_enqueue_ms']['rtds']=70000
        self.assertTrue(check(x)['status'].startswith('tail_contract_passed'))
        x['writer']['latest_stream_enqueue_ms']['rtds']=69999
        self.assertEqual(check(x)['status'],'blocked_tail_contract')

    def test_requirements_are_explicit_validated_and_not_mutated(self):
        req={'custom':100};snapshot=copy.deepcopy(req);x=self.runtime()
        x['writer']['latest_stream_enqueue_ms']['custom']=99999
        self.assertTrue(check(x,req)['status'].startswith('tail_contract_passed'))
        self.assertEqual(req,snapshot)
        for invalid in ({},{'x':0},{'x':True},{'x':-1},{'x':float('nan')}):
            with self.assertRaises(ValueError):check(x,invalid)

    def test_bad_runtime_span_rejected(self):
        for start,end in ((0,None),(True,100),(-1,100),(100,100),(101,100)):
            x=self.runtime();x.update(started_ms=start,ended_ms=end)
            with self.assertRaises(ValueError):check(x)


if __name__=='__main__':unittest.main()
