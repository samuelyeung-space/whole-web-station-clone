"""Synthetic correctness/complexity checks, not historical replay or PnL."""
import copy
import math
import random
import unittest

from binance_native_flow import NativeFlowTape, _AggregateRangeIndex
from historical_flow_receipts import CoverageInterval


def packet(tid, lo, hi, maker=False):
    return dict(e='aggTrade',E=59000,s='BTCUSDT',a=tid,p='100',q='2',
                T=58999,m=maker,f=lo,l=hi)


class IndexTests(unittest.TestCase):
    def add(self, index, lo, hi):
        position=index.position(lo,hi)
        index.insert_at(position,lo,hi)

    def test_touching_inclusive_endpoint_overlaps(self):
        for lo,hi in [(10,10),(12,12),(9,10),(12,13),(11,11),(9,13)]:
            index=_AggregateRangeIndex();self.add(index,10,12)
            with self.subTest(lo=lo,hi=hi),self.assertRaisesRegex(ValueError,'overlap'):
                index.position(lo,hi)

    def test_neighbors_are_not_overlap(self):
        index=_AggregateRangeIndex()
        for lo,hi in [(10,12),(13,15),(7,9),(0,0)]:self.add(index,lo,hi)
        self.assertEqual(list(zip(index.starts,index.ends)),[(0,0),(7,9),(10,12),(13,15)])

    def test_gap_insertion_and_singletons(self):
        index=_AggregateRangeIndex()
        for pair in [(30,35),(10,15),(20,20),(16,19),(21,29)]:self.add(index,*pair)
        self.assertEqual(index.starts,[10,16,20,21,30])

    def test_large_integers_never_float_roundtrip(self):
        index=_AggregateRangeIndex();base=2**90
        self.add(index,base,base);self.add(index,base+1,base+1)
        self.assertEqual(index.starts,[base,base+1])
        with self.assertRaises(ValueError):index.position(base+1,base+2)

    def test_invalid_interval_is_transactionless(self):
        index=_AggregateRangeIndex();self.add(index,3,5)
        for pair in [(True,3),(1.,3),(-1,3),(4,3),('1',3),(None,3)]:
            with self.subTest(pair=pair),self.assertRaises(ValueError):index.position(*pair)
            self.assertEqual((index.starts,index.ends),([3],[5]))

    def test_randomized_parity_against_all_prior_ranges(self):
        rng=random.Random(20261020)
        for seed in range(8):
            index=_AggregateRangeIndex();accepted=[]
            for _ in range(1200):
                lo=rng.randrange(30000);hi=lo+rng.randrange(8)
                oracle=any(lo<=b and a<=hi for a,b in accepted)
                if oracle:
                    with self.assertRaisesRegex(ValueError,'overlap'):index.position(lo,hi)
                else:self.add(index,lo,hi);accepted.append((lo,hi))
            self.assertEqual(list(zip(index.starts,index.ends)),sorted(accepted))

    def test_reverse_order_disjoint_ranges(self):
        index=_AggregateRangeIndex()
        for i in range(999,-1,-1):self.add(index,3*i,3*i+1)
        self.assertEqual(index.starts,list(range(0,3000,3)))

    def test_lookup_does_not_scan_whole_history(self):
        class Probes(list):
            def __init__(self,values):super().__init__(values);self.reads=0
            def __getitem__(self,key):self.reads+=1;return super().__getitem__(key)
        index=_AggregateRangeIndex();n=100000
        index.starts=Probes(range(0,4*n,4));index.ends=Probes(range(1,4*n+1,4))
        self.assertEqual(index.position(4*(n//2)+2,4*(n//2)+3),n//2+1)
        self.assertLessEqual(index.starts.reads+index.ends.reads,math.ceil(math.log2(n))+6)
        index.starts.reads=index.ends.reads=0
        self.assertEqual(index.position(4*n,4*n+1),n)
        self.assertLessEqual(index.starts.reads+index.ends.reads,1)


class TapeTests(unittest.TestCase):
    def tape(self):
        tape=NativeFlowTape(stream_kind='aggTrade',time_unit='MILLISECOND')
        tape.add_coverage(CoverageInterval('binance','BTCUSDT','run',0,60000,60000,'complete','synthetic'))
        return tape

    def add(self,tape,p,recv=59001):
        return tape.add_trade(p,local_recv_ms=recv,capture_run_id='run')

    def test_duplicate_does_not_insert_second_range_or_volume(self):
        tape=self.tape();self.add(tape,packet(1,10,12));self.add(tape,packet(1,10,12),59002)
        self.assertEqual(tape._aggregate_index.starts,[10])
        self.assertEqual(len(tape.native_receipts),2)
        self.assertEqual(tape.features(60000)['audit']['flow_15s']['quote_volume'],200.)

    def test_source_id_reorder_is_accepted_in_receipt_order(self):
        tape=self.tape()
        for i,(lo,hi) in enumerate([(30,32),(10,12),(20,22)]):self.add(tape,packet(i,lo,hi),59001+i)
        self.assertEqual(tape._aggregate_index.starts,[10,20,30])
        self.assertEqual([r['first_raw_trade_id'] for r in tape.native_receipts],[30,10,20])
        self.assertEqual(tape.features(60000)['features']['flow_60s'],1.)

    def test_engine_rejection_cannot_commit_range(self):
        tape=self.tape();self.add(tape,packet(1,10,12))
        before=copy.deepcopy(tape._aggregate_index.__dict__)
        with self.assertRaises(ValueError):self.add(tape,packet(2,20,22),59000)
        self.assertEqual(tape._aggregate_index.__dict__,before)
        self.assertEqual(len(tape.native_receipts),1)
        self.assertIsNone(tape.features(60000)['features']['flow_60s'])

    def test_future_overlap_failure_does_not_filter_past(self):
        tape=self.tape();self.add(tape,packet(1,10,12));before=tape.features(60000)
        with self.assertRaises(ValueError):self.add(tape,packet(2,12,14),60001)
        self.assertEqual(tape.features(60000),before)
        self.assertIsNone(tape.features(60002)['features']['flow_60s'])

    def test_conflicting_duplicate_fails_without_index_mutation(self):
        tape=self.tape();self.add(tape,packet(1,10,12))
        with self.assertRaisesRegex(ValueError,'conflicting native'):self.add(tape,packet(1,20,22),59002)
        self.assertEqual(tape._aggregate_index.starts,[10])

    def test_unknown_side_is_not_repaired_by_index(self):
        tape=self.tape();p=packet(1,10,12);del p['m'];self.add(tape,p)
        self.assertIsNone(tape.features(60000)['features']['flow_15s'])

    def test_tape_parity_with_previous_full_scan_oracle(self):
        class FullScan:
            def __init__(self):self.ranges=[]
            def position(self,lo,hi):
                if any(lo<=b and a<=hi for a,b in self.ranges):raise ValueError('overlap')
                return len(self.ranges)
            def insert_at(self,position,lo,hi):self.ranges.append((lo,hi))
        fast=self.tape();slow=self.tape();slow._aggregate_index=FullScan()
        ranges=list(range(1000));random.Random(77).shuffle(ranges)
        for i,key in enumerate(ranges):
            p=packet(key,4*key,4*key+2,maker=bool(key%2))
            p['q']=str(1+(key%17)/10)
            self.add(fast,p,59001+i/2);self.add(slow,p,59001+i/2)
            if i%97==0:
                self.add(fast,p,59001+i/2);self.add(slow,p,59001+i/2)
        self.assertEqual(fast.native_receipts,slow.native_receipts)
        self.assertEqual(fast.engine.input_audit(),slow.engine.input_audit())
        self.assertEqual(fast.features(60000),slow.features(60000))


if __name__=='__main__':unittest.main()
