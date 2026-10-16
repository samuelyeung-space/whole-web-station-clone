import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import relay_source_amendment as amend


class RelayAmendmentTests(unittest.TestCase):
    def test_deterministic_semantic_repair_preserves_numeric_producer_and_events(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'old.gz';rec=root/'recording.py'
            rec.write_text("# inspected fixture: crypto_prices -> BINANCE_AGG_TRADE\n")
            identity={'code_sha256':'original-numeric-producer','archive_sha256':'original-raw'}
            header={'type':'dataset','dataset_id':'old','source_reference':'binance',
                    'provenance':{'identity':identity},'assumptions':{},'limitations':[]}
            rows=[header,{'type':'market','market_id':'m','start_ms':100},
                  {'type':'decision','market_id':'m','recv_ms':200,'features':{'r_5s':1.2}},
                  {'type':'book','market_id':'m','recv_ms':250,'up_asks':[[.4,5]]}]
            with gzip.open(src,'wt') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
            before=amend.sha(src)
            Path(str(src)+'.manifest.json').write_text(json.dumps({'identity':identity,'header':header,
                'counts':{'dataset':1,'market':1,'decision':1,'book':1},'output_sha256':before}))
            kw={'recorder_source':rec,'recorder_commit':'a'*40,'recorder_sha256':amend.sha(rec)}
            a=amend.repair(src,root/'new1.gz',**kw);b=amend.repair(src,root/'new2.gz',**kw)
            self.assertEqual(amend.sha(src),before)
            self.assertEqual(a['output_sha256'],b['output_sha256'])
            self.assertEqual(a['original_numeric_event_stream_sha256'],a['corrected_numeric_event_stream_sha256'])
            self.assertEqual(a['identity']['numeric_producer_identity'],identity)
            self.assertEqual(a['new_strategy_trials'],0)
            self.assertTrue(a['derived_same_events_zero_new_strategy_trials'])
            changed=[json.loads(x) for x in gzip.open(root/'new1.gz','rt')]
            for old,new in zip(rows[1:],changed[1:]):
                self.assertEqual(old,{k:v for k,v in new.items() if k not in amend.FIELDS})
            self.assertEqual(changed[0]['source_reference'],amend.SOURCE)
            self.assertEqual(changed[2]['source_topic'],'crypto_prices')

    def test_recorder_evidence_mismatch_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);r=root/'recording.py';r.write_text('crypto_prices BINANCE_AGG_TRADE')
            out=root/'out.gz'
            with self.assertRaisesRegex(ValueError,'evidence identity mismatch'):
                amend.repair(root/'absent.gz',out,recorder_source=r,recorder_commit='a'*40,recorder_sha256='0'*64)
            self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
