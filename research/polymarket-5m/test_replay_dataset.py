import gzip
import json
from pathlib import Path
import tempfile
import unittest

from replay_dataset import ingest, iter_events, verify_cache

DAY = 86400000
SLOT = 300000


def tape(start=0, end=SLOT, mid='m', split='development', outcome=True):
    result = [
        {'type':'dataset', 'recv_ms':start, 'dataset_id':mid, 'split':split,
         'provenance':{'source':'synthetic_engineering_fixture'},
         'observed_intervals_ms':[[start,end]]},
        {'type':'market','recv_ms':start,'market_id':mid,'start_ms':start,
         'end_ms':start+SLOT,'label_source':'gamma'},
        {'type':'book','recv_ms':start+1,'market_id':mid,'up_asks':[[0.4,10]],
         'down_asks':[[0.6,10]],'healthy':True,'fee_rate':0.07},
        {'type':'decision','recv_ms':start+2,'market_id':mid,'features':{'x':1,'tau_s':299.998},
         'feature_available_ms':{'x':start+1,'tau_s':start+2}},
    ]
    if outcome:
        result.append({'type':'outcome','recv_ms':start+SLOT+500,'market_id':mid,
                       'resolved_ms':start+SLOT,'winner':'Up','source':'gamma'})
    return result


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root/'cache'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows, name='a.jsonl.gz'):
        p = self.root/name
        opener = gzip.open if str(p).endswith('.gz') else open
        with opener(p,'wt') as f:
            for row in rows:
                f.write(json.dumps(row)+'\n')
        return p

    def test_full_midnight_day_counts_288_not_289(self):
        p = self.write(tape(0,DAY))
        result = ingest([p],self.cache)
        day = result['coverage']['daily'][0]
        self.assertEqual(len(result['coverage']['daily']),1)
        self.assertEqual(day['recorded_span_slots'],288)
        self.assertEqual(day['complete_span_slots'],288)
        self.assertEqual(day['expected_slots'],288)
        self.assertEqual(day['gaps_ms'],[])
        self.assertFalse(result['coverage']['complete_feed_health_verified'])
        self.assertEqual(result['coverage']['markets'][0]['market_id'],'m')
        self.assertEqual(result['new_backtests'],0)
        self.assertIsNone(result['pnl'])

    def test_full_declared_span_does_not_create_missing_market_data(self):
        result=ingest(self.write(tape(0,DAY)),self.cache)
        day=result['coverage']['daily'][0]
        self.assertEqual(day['observed_seconds'],86400)
        self.assertEqual(day['complete_span_slots'],288)
        self.assertEqual(day['data_present_slots'],1)
        self.assertEqual(len(result['identity']['adapter_code_sha256']),64)
        self.assertFalse(day['data_present_slots']==day['expected_slots'])

    def test_gap_day_remains_missing_not_filled(self):
        a = self.write(tape(0,SLOT,'a'),'a.jsonl.gz')
        b = self.write(tape(2*DAY,2*DAY+SLOT,'b'),'b.jsonl.gz')
        result = ingest([b,a],self.cache)
        days = result['coverage']['daily']
        self.assertEqual(len(days),3)
        self.assertEqual(days[1]['recorded_span_slots'],0)
        self.assertEqual(days[1]['missing_span_slots'],288)
        events = list(iter_events(result['cache_path']))
        self.assertEqual(sum(e['type']=='dataset' for e in events),1)
        self.assertEqual(events[0]['observed_intervals_ms'],[[0,SLOT],[2*DAY,2*DAY+SLOT]])
        self.assertEqual([e['recv_ms'] for e in events],sorted(e['recv_ms'] for e in events))
        self.assertEqual(result['coverage']['observed_hours'],1/6)
        self.assertEqual(result['coverage']['inter_recording_gaps_ms'],[[SLOT,2*DAY]])

    def test_content_duplicates_idempotent_even_recompressed(self):
        rows = tape()
        a = self.write(rows,'a.jsonl.gz')
        b = self.write(rows,'b.jsonl')
        first = ingest([a,b,a],self.cache)
        self.assertEqual(first['duplicate_inputs_ignored'],2)
        self.assertEqual(first['source_tape_count'],1)
        again = ingest([b],self.cache)
        self.assertTrue(again['cache_hit'])
        self.assertEqual(first['cache_path'],again['cache_path'])
        self.assertEqual(first['record_count'],5)

    def test_distinct_overlapping_recordings_rejected(self):
        a = self.write(tape(mid='a'),'a.jsonl.gz')
        b = self.write(tape(mid='b'),'b.jsonl.gz')
        with self.assertRaisesRegex(ValueError,'overlap'):
            ingest([a,b],self.cache)

    def test_mixed_split_rejected(self):
        a = self.write(tape(mid='a'),'a.jsonl.gz')
        b = self.write(tape(SLOT+1,2*SLOT+1,'b',split='holdout'),'b.jsonl.gz')
        with self.assertRaisesRegex(ValueError,'mixed input split'):
            ingest([a,b],self.cache)

    def test_corrupted_cached_tape_refused_without_overwriting(self):
        p = self.write(tape())
        result = ingest(p,self.cache)
        cached = Path(result['cache_path'])/'events.jsonl.gz'
        cached.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'corrupted'):
            list(iter_events(result['cache_path']))
        with self.assertRaisesRegex(ValueError,'corrupted'):
            ingest(p,self.cache)
        self.assertEqual(cached.read_bytes(),b'corrupt')

    def test_corrupted_manifest_refused(self):
        p = self.write(tape())
        result = ingest(p,self.cache)
        path = Path(result['cache_path'])/'manifest.json'
        data = json.loads(path.read_text()); data['split']='OOS'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'manifest corrupted'):
            verify_cache(result['cache_path'])

    def test_backward_local_clock_rejected(self):
        rows = tape(); rows[3]['recv_ms']=0
        with self.assertRaisesRegex(ValueError,'sorted by local'):
            ingest(self.write(rows),self.cache)

    def test_future_book_receipt_rejected(self):
        rows = tape(); rows[2]['up_recv_ms']=2
        with self.assertRaisesRegex(ValueError,'future local receipt'):
            ingest(self.write(rows),self.cache)

    def test_future_missing_features_preserved_and_counted(self):
        rows = tape()
        rows[3]['feature_available_ms']['x']=1000
        rows[3]['features']['missing']=None
        rows[2].pop('fee_rate')
        result = ingest(self.write(rows),self.cache)
        self.assertEqual(result['quality_issues']['future_feature:x'],1)
        self.assertEqual(result['quality_issues']['null_feature:missing'],1)
        self.assertEqual(result['quality_issues']['missing_or_invalid_fee'],1)
        decision = next(e for e in iter_events(result['cache_path']) if e['type']=='decision')
        self.assertEqual(decision['feature_available_ms']['x'],1000)
        self.assertIsNone(decision['features']['missing'])

    def test_projection_metadata_preserved(self):
        rows = tape()
        rows[0].update(book_resolution='decision_and_match_projection',decision_grid_ms=1,
                       supported_latencies_ms=[500,600,1000])
        rows[2].update(up_recv_ms=1,down_recv_ms=1)
        result = ingest(self.write(rows),self.cache)
        header = next(iter_events(result['cache_path']))
        self.assertEqual(header['supported_latencies_ms'],[500,600,1000])
        self.assertEqual(header['provenance']['source_headers'][0],rows[0])

    def test_cross_recording_same_market_keeps_local_order_and_late_outcomes(self):
        a = tape(0,100,'same')
        a[-1]['recv_ms']=600000
        b = tape(200,300,'same')
        b[1].update(start_ms=0,end_ms=SLOT)
        b[-1].update(resolved_ms=SLOT,recv_ms=SLOT+500)
        pa=self.write(a,'a.jsonl.gz'); pb=self.write(b,'b.jsonl.gz')
        result=ingest([pa,pb],self.cache)
        events=list(iter_events(result['cache_path']))
        self.assertEqual([e['recv_ms'] for e in events],sorted(e['recv_ms'] for e in events))
        self.assertEqual(sum(e['type']=='market' for e in events),2)
        self.assertEqual(sum(e['type']=='outcome' for e in events),2)
        self.assertEqual(result['coverage']['registered_markets'],1)
        self.assertEqual(result['coverage']['outcome_markets'],1)
        self.assertEqual([e['recv_ms'] for e in events if e['type']=='outcome'],[SLOT+500,600000])

    def test_equal_receipt_events_preserve_input_sequence(self):
        rows=tape()
        rows[2]['recv_ms']=0
        rows[3].update(recv_ms=0,feature_available_ms={'x':0,'tau_s':0})
        result=ingest(self.write(rows),self.cache)
        events=list(iter_events(result['cache_path']))
        self.assertEqual([e['type'] for e in events[:4]],['dataset','market','book','decision'])

    def test_half_slots_not_complete(self):
        rows = tape(0,1.5*SLOT)
        rows[0]['observed_intervals_ms']=[[SLOT/2,1.5*SLOT]]
        rows[2]['recv_ms']=SLOT/2
        rows[3]['recv_ms']=SLOT/2+1
        result = ingest(self.write(rows),self.cache)
        day=result['coverage']['daily'][0]
        self.assertEqual(day['recorded_span_slots'],2)
        self.assertEqual(day['complete_span_slots'],0)


if __name__ == '__main__':
    unittest.main()
