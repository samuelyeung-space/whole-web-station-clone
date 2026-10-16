import unittest
from legacy_depth_admission import Series,compare,top

class Tests(unittest.TestCase):
    def test_no_future_or_equal_receipt(self):
        s=Series([(10,(.4,2,.5,3)),(20,(.3,2,.6,3))])
        self.assertEqual(s.before(10)[0],'missing')
        self.assertEqual(s.before(20)[1],10)
    def test_ambiguous_ties(self):
        s=Series([(10,(.4,2,.5,3)),(10,(.4,2,.5,4))])
        self.assertEqual(s.before(11)[0],'ambiguous')
    def test_identical_ties(self):
        self.assertEqual(Series([(10,(1,)),(10,(1,))]).before(11)[0],'available')
    def test_invalid_does_not_resurrect_previous(self):
        s=Series([(10,(.4,2,.5,3)),(20,None)])
        self.assertEqual(s.before(21)[0],'invalid')
    def test_price_and_size_disagreement(self):
        a=Series([(1,(.4,2,.5,9))]);b=Series([(2,(.4,2,.6,3))])
        self.assertEqual(compare(a,b,100)['snapshot_ask_cheaper'],1)
        b=Series([(2,(.4,2,.5,3))])
        r=compare(a,b,100)
        self.assertEqual(r['top_price_mismatch'],0)
        self.assertEqual(r['same_ask_price_excess_size'],1)
    def test_sort_and_invalid_level(self):
        self.assertEqual(top({'bids':[{'price':'.4','size':'2'},{'price':'.3','size':'5'}],
            'asks':[{'price':'.6','size':'2'},{'price':'.5','size':'3'}]}),(.4,2,.5,3))
        with self.assertRaises(ValueError):top({'bids':[{'price':'nan','size':2}],'asks':[]})
    def test_empty_or_crossed(self):
        self.assertIsNone(top({'bids':[],'asks':[]}))
        self.assertIsNone(top({'bids_json':'[[0.6,2]]','asks_json':'[[0.5,2]]'},csv=True))

if __name__=='__main__':unittest.main()
