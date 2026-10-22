import unittest
from futures_recording_integrity import ObservedBadPrints


class NegativeIntegrityTests(unittest.TestCase):
    def test_future_bad_print_does_not_exclude_past(self):
        x=ObservedBadPrints();before=x.at(1000,15000)
        x.add(1001);self.assertEqual(before,x.at(1000,15000))
        self.assertEqual(x.at(1001,15000)['observed_bad_prints'],1)

    def test_open_left_closed_right_and_equal_receipts(self):
        x=ObservedBadPrints();x.add(0);x.add(15000);x.add(15000)
        self.assertEqual(x.at(15000,15000)['observed_bad_prints'],2)
        self.assertEqual(x.at(30000,15000)['observed_bad_prints'],0)

    def test_no_witness_never_certifies_coverage(self):
        y=ObservedBadPrints().at(60000,60000)
        self.assertEqual(y['status'],'not_excluded_by_this_check')
        self.assertFalse(y['coverage_certified']);self.assertFalse(y['flow_feature_available'])

    def test_union_merges_overlap_and_clips_to_fixed_bounds(self):
        x=ObservedBadPrints();x.add(0);x.add(5);x.add(20)
        self.assertEqual(x.excluded_duration_ms(2,25,10),18)

    def test_negative_evidence_before_start_may_overlap_window(self):
        x=ObservedBadPrints();x.add(5)
        self.assertEqual(x.excluded_duration_ms(10,30,10),5)

    def test_late_witness_does_not_exclude_fixed_past_interval(self):
        x=ObservedBadPrints();x.add(30)
        self.assertEqual(x.excluded_duration_ms(0,30,10),0)

    def test_invalid_clocks_horizons_and_order_rejected(self):
        x=ObservedBadPrints();x.add(1)
        for t in (-1,True,float('nan'),float('inf'),'2'):
            with self.subTest(t=t),self.assertRaises(ValueError):x.add(t)
        with self.assertRaises(ValueError):x.add(0)
        with self.assertRaises(ValueError):x.at(1,0)
        with self.assertRaises(ValueError):x.excluded_duration_ms(5,5,1)


if __name__=='__main__':unittest.main()
