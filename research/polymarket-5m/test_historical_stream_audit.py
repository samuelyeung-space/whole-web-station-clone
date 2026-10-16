import unittest
from historical_stream_audit import VenueClockAudit

def event(token,stamp,kind='clob_price_change'):
    return {'kind':kind,'asset_id':token,'source_ts_ms':stamp}

class Tests(unittest.TestCase):
    def test_cross_token_is_not_stale(self):
        a=VenueClockAudit()
        for e in (event('a',20),event('b',10),event('a',21)):a.consume(e)
        self.assertEqual(a.counts['adjacent_global_regressions'],1)
        self.assertEqual(a.counts['global_regressions_cross_token'],1)
        self.assertEqual(a.counts['per_token_stale_events'],0)
    def test_stale_cannot_reset_highwater(self):
        a=VenueClockAudit()
        for t in (20,10,15):a.consume(event('a',t))
        self.assertEqual(a.counts['per_token_stale_events'],2)
        self.assertEqual(a.latest['a'],20)
    def test_interleaving_can_hide_stale(self):
        a=VenueClockAudit()
        for e in (event('a',20),event('b',5),event('a',10)):a.consume(e)
        self.assertEqual(a.counts['per_token_stale_events'],1)
    def test_epoch_resets_clock(self):
        a=VenueClockAudit();a.consume(event('a',20));a.consume({'kind':'clob_error'})
        a.consume({'kind':'clob_connection'});a.consume(event('a',10,'clob_snapshot'))
        self.assertEqual(a.counts['per_token_stale_events'],0)
    def test_equal_time_not_regression(self):
        a=VenueClockAudit();a.consume(event('a',20));a.consume(event('a',20))
        self.assertEqual(a.counts['per_token_stale_events'],0)
    def test_snapshot_stale_counted(self):
        a=VenueClockAudit();a.consume(event('a',20));a.consume(event('a',10,'clob_snapshot'))
        self.assertEqual(a.counts['stale_clob_snapshot'],1)

if __name__=='__main__':unittest.main()
