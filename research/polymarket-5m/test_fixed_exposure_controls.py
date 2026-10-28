import unittest,tempfile,gzip,json
from pathlib import Path
from fixed_exposure_controls import route,legs,atomic_json_gzip

class ExposureTests(unittest.TestCase):
    def test_controls_are_two_isolated_ledgers(self):
        ls=legs();self.assertEqual([l['_patient_quantity'] for l in ls],[25,50]);self.assertEqual(len({l['_ledger_id'] for l in ls}),2)
    def test_original_down_direction_limit_and_clock(self):
        tape=[dict(type='book',market_id='m',recv_ms=10,down_asks=[[.23,100]]),dict(type='decision',market_id='m',recv_ms=10,features=dict(experiment_kind=0,experiment_side=-1,tau_s=20))]
        out=list(route(tape,[dict(market_id='m',decision_ms=10)]));ds=[r for r in out if r['type']=='decision'];self.assertEqual(len(ds),2)
        for d in ds:self.assertEqual(d['features']['patient_side'],-1);self.assertEqual(d['features']['patient_limit'],.23);self.assertEqual(d['feature_available_ms']['patient_side'],10)
    def test_gate_never_becomes_an_immediate_control(self):
        tape=[dict(type='decision',market_id='m',recv_ms=10,features=dict(experiment_kind=1,experiment_side=1,tau_s=20))]
        with self.assertRaises(ValueError):list(route(tape,[dict(market_id='m',decision_ms=0)]))
    def test_atomic_file_is_complete_and_replaces_old_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'result.json.gz';p.write_bytes(b'old');atomic_json_gzip(p,{'x':[1,2]});self.assertEqual(json.loads(gzip.decompress(p.read_bytes())),{'x':[1,2]});self.assertFalse(p.with_suffix('.pending').exists())

if __name__=='__main__':unittest.main()
