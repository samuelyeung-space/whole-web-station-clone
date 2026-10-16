import unittest
from eco_input_audit import Audit, reasons, NORMS, HORIZONS, DERIVATIVES


def event(t, market='A', tau=200, ready=(), missing=None):
    f = {'book_5': .6, 'mid_up': .5, 'tau_s': tau}
    av = {k: t for k in f}
    if missing:
        f.pop(missing)
    for norm, horizon in ready:
        for k in DERIVATIVES:
            f[f'eco_{norm}_{horizon}_{k}'] = .01
            av[f'eco_{norm}_{horizon}_{k}'] = t
    return {'type': 'decision', 'market_id': market, 'recv_ms': t,
            'features': f, 'feature_available_ms': av}


class AttritionTests(unittest.TestCase):
    def test_exact_warmup_and_finalization(self):
        a = Audit()
        for i in range(1, 62):
            ready = [(n, h) for n in NORMS for h in HORIZONS if i >= 4*h]
            a.decision(event(i*1000, ready=ready))
        r = a.finish()
        self.assertEqual(r, a.finish())
        self.assertEqual(r['counts']['expected_ready|plain_h5'], 42)
        self.assertEqual(r['counts']['expected_ready|plain_h15'], 2)
        self.assertEqual(r['max_valid_run_rows']['plain'], 61)
        self.assertEqual(sum(r['counts'][k] for k in r['counts'] if k.startswith('readiness_mismatch|')), 0)

    def test_missing_score_breaks_run(self):
        a = Audit()
        for i in range(1, 20):
            a.decision(event(i*1000))
        a.decision(event(20000, missing='book_5'))
        a.decision(event(21000))
        r = a.finish()
        self.assertEqual(r['max_valid_run_rows']['plain'], 19)
        self.assertEqual(r['counts']['expected_ready|plain_h5'], 0)
        self.assertEqual(r['counts']['reason|book_5:missing_or_nonfinite'], 1)

    def test_market_states_are_separate(self):
        a = Audit()
        for i in range(1, 20):
            a.decision(event(i*1000, market='A'))
            a.decision(event(i*1000, market='B'))
        self.assertEqual(a.finish()['counts']['expected_ready|plain_h5'], 0)

    def test_gap_breaks_run_without_interpolation(self):
        a = Audit()
        for i in range(1, 20):
            a.decision(event(i*1000))
        a.decision(event(21000))
        r = a.finish()
        self.assertEqual(r['counts']['non_1000ms_pairs'], 1)
        self.assertEqual(r['max_valid_run_rows']['plain'], 19)

    def test_variance_join_only_resets_normalized(self):
        a = Audit()
        for i in range(1, 21):
            a.decision(event(i*1000, tau=80-i,
                ready=[(n, 5) for n in NORMS] if i == 20 else []))
        a.decision(event(21000, tau=59, ready=[('plain', 5)]))
        r = a.finish()
        self.assertEqual(r['counts']['variance_join_resets'], 1)
        self.assertEqual(r['counts']['expected_ready|plain_h5'], 2)
        self.assertEqual(r['counts']['expected_ready|twap_variance_h5'], 1)

    def test_future_and_bool_rejected(self):
        e = event(1000)
        e['feature_available_ms']['book_5'] = 1001
        self.assertIn('book_5:future_availability', reasons(e))
        e['features']['book_5'] = True
        self.assertIn('book_5:missing_or_nonfinite', reasons(e))

    def test_incomplete_derivative_set_detected(self):
        a = Audit()
        e = event(1000)
        e['features']['eco_plain_5_v'] = .1
        a.decision(e)
        self.assertEqual(a.finish()['counts']['readiness_mismatch|plain_h5'], 1)

    def test_out_of_bounds_rejected(self):
        for key, value in [('book_5', 1), ('mid_up', 0), ('tau_s', 301)]:
            e = event(1000)
            e['features'][key] = value
            self.assertEqual(len(reasons(e)), 1)

    def test_midnight_does_not_reset_market(self):
        a = Audit()
        midnight = 86400000
        for i in range(1, 22):
            a.decision(event(midnight + (i-20)*1000,
                ready=[(n, 5) for n in NORMS] if i >= 20 else []))
        r = a.finish()
        self.assertEqual(r['counts']['expected_ready|plain_h5'], 2)
        self.assertEqual(r['by_decision_receipt_utc_date']['1970-01-02']['expected_ready|plain_h5'], 2)


if __name__ == '__main__':
    unittest.main()
