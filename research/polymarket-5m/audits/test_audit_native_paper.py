import csv
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from audit_native_paper import audit, winner


class NativeAuditTests(unittest.TestCase):
    def fixture(self, root):
        root = Path(root); raw = root/'raw/2000-01-01'; raw.mkdir(parents=True)
        slug = 'btc-updown-5m-946684800'
        market = dict(slug=slug, outcomes='["Up","Down"]', outcomePrices='["1","0"]',
                      closed=True, _recorded_at_ms=100,
                      feeSchedule=dict(exponent=1, rate=.07))
        decision = dict(slug=slug, tau=60, decided_ms=200, side='Up', shares=5,
                        ask=.5, fee=.0875, cost=2.5875, btc=[190, 1])
        ledger = dict(slug=slug, tau=60, decided_ms=200, side='Up', shares=5,
                      ask=.5, fee=.0875, cost=2.5875, won='True', pnl=2.4125)
        (root/'capture-manifest.json').write_text(json.dumps(dict(files=[], run_id='synthetic')))
        (root/'capture-runtime.json').write_text(json.dumps(dict(started_ms=0, ended_ms=1000,
                            collector_region='unverified', feed_health_verified=False)))
        with (root/'ledger.csv').open('w') as f:
            w=csv.DictWriter(f, fieldnames=list(ledger)); w.writeheader(); w.writerow(ledger)
        self.write(raw/'markets.jsonl.gz', [market])
        self.write(raw/'decisions.jsonl.gz', [decision])
        return raw, market, decision

    @staticmethod
    def write(path, rows):
        with gzip.open(path, 'wt') as f:
            for r in rows: f.write(json.dumps(r)+'\n')

    def test_unsettled_quote_never_becomes_label(self):
        self.assertIsNone(winner(dict(outcomes=['Up','Down'], outcomePrices=[.9,.1], closed=True)))
        self.assertIsNone(winner(dict(outcomes=['Up','Down'], outcomePrices=[1,0], closed=False)))

    def test_later_fee_cannot_replace_decision_fee(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, market, _ = self.fixture(tmp)
            later = dict(market, _recorded_at_ms=300, feeSchedule=dict(exponent=1, rate=.99))
            self.write(raw/'markets.jsonl.gz', [market, later])
            r = audit(tmp)
            self.assertEqual(r['details'][0]['fee_rate'], .07)
            self.assertIsNone(r['formal_oos_result'])
            self.assertEqual(r['actual_fills_verified'], 0)

    def test_ambiguous_decision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, _, decision = self.fixture(tmp)
            self.write(raw/'decisions.jsonl.gz', [decision, decision])
            with self.assertRaises(AssertionError): audit(tmp)

    def test_skipped_missing_tau_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, _, decision = self.fixture(tmp)
            self.write(raw/'decisions.jsonl.gz', [dict(slug=decision['slug'], skip='no book'), decision])
            self.assertEqual(audit(tmp)['skips'], {'no book': 1})

    def test_official_label_disagreement_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, market, _ = self.fixture(tmp)
            self.write(raw/'markets.jsonl.gz', [dict(market, outcomePrices='["0","1"]')])
            with self.assertRaises(AssertionError): audit(tmp)


if __name__ == '__main__': unittest.main()
