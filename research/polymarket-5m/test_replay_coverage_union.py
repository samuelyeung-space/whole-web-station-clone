import unittest
from replay_coverage_union import DAY, summarize, union_intervals

def lane(name, spans, markets):
    return {'lane': name, 'coverage': {'observed_intervals_ms': spans, 'markets': markets, 'daily': []}}

def market(name, start=0):
    return {'market_id':name, 'start_ms':start,'end_ms':start+300000}

class CoverageUnionTest(unittest.TestCase):
    def test_duplicate_spans_and_markets_are_not_added(self):
        result = summarize([lane('a', [[0,DAY]], [market('x')]),
                            lane('b', [[0,DAY]], [market('x')])])
        self.assertEqual(result['unique_observed_hours'],24)
        self.assertEqual(result['unique_registered_markets'],1)
        self.assertEqual(result['pairwise_market_overlap'][0]['shared_market_count'],1)
        self.assertIsNone(result['full_executable_days'])
        self.assertEqual(len(result['daily']),1)

    def test_absent_calendar_day_not_removed(self):
        result=summarize([lane('a',[[0,DAY],[2*DAY,3*DAY]],[market('a'),market('b',2*DAY)])])
        self.assertEqual(len(result['daily']),3)
        self.assertEqual(result['daily'][1]['observed_hours_union'],0)
        self.assertEqual(result['daily'][1]['expected_slots'],288)

    def test_conflicting_calendar_rejected(self):
        with self.assertRaisesRegex(ValueError,'conflicting'):
            summarize([lane('a',[[0,DAY]],[market('x')]), lane('b',[[0,DAY]],[market('x',300000)])])

    def test_tiny_gap_prevents_full_span(self):
        result=summarize([lane('a',[[0,DAY-1]],[market('x')])])
        self.assertEqual(result['full_recorded_span_days'],0)
        self.assertEqual(result['daily'][0]['complete_recorded_span_slots'],287)
        self.assertEqual(union_intervals([[0,2],[1,3],[3,4]]),[[0,4]])

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):union_intervals([[0,float('inf')]])

if __name__ == '__main__':unittest.main()
