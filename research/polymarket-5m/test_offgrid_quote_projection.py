import unittest
from offgrid_quote_projection import project
from test_atomic_quote_groups import snap,change


def req(t,assets=None):return dict(request_id=str(t),at_ms=t,asset_ids=assets or ['a'])


class ProjectionTests(unittest.TestCase):
    def test_arbitrary_time_never_sees_next_received_quote(self):
        x=list(project([snap(0),change(500.25,'SELL',.51,40),change(900,'BUY',.49,20)],
                       [req(500.1),req(500.25),req(600)]))
        self.assertEqual([r['tokens']['a']['asks'][0][1] for r in x],[10,40,40])
        self.assertEqual(x[0]['tokens']['a']['recv_ms'],0)

    def test_match_at_tie_uses_complete_group(self):
        rows=[snap(0),change(500,'BUY',.52,10),change(500,'SELL',.51,0),change(500,'SELL',.53,20)]
        x=list(project(rows,[req(500)]))[0]['tokens']['a']
        self.assertTrue(x['valid']);self.assertEqual(x['asks'],[(.53,20)])

    def test_conflicting_tie_cannot_be_a_fillable_projection(self):
        x=list(project([snap(0),change(500,'BUY',.49,0),change(500,'BUY',.49,20)],
                       [req(500)]))[0]['tokens']['a']
        self.assertFalse(x['valid']);self.assertEqual(x['asks'],[])

    def test_eof_does_not_extend_recording_even_with_fresh_quote(self):
        x=list(project([snap(0)],[req(1)]))[0]
        self.assertFalse(x['observed_horizon_covered']);self.assertFalse(x['tokens']['a']['valid'])

    def test_lifecycle_and_other_token_never_supply_missing_side(self):
        rows=[snap(0),dict(kind='clob_error',connection_epoch=1,recv_ms=500),snap(700,epoch=2)]
        x=list(project(rows,[req(600),req(700,['a','b'])]))
        self.assertFalse(x[0]['tokens']['a']['valid'])
        self.assertTrue(x[1]['tokens']['a']['valid']);self.assertFalse(x[1]['tokens']['b']['valid'])

    def test_stale_quote_is_rejected_when_horizon_is_observed(self):
        x=list(project([snap(0),snap(2000)],[req(1500)]))[0]['tokens']['a']
        self.assertFalse(x['valid'])


if __name__=='__main__':unittest.main()
