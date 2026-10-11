"""Synthetic evidence-gate counterexamples; no strategy backtests."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import historical100_evidence as E
from historical100_execution import replay
from test_historical100_execution import book,market,signal


def fixture(rule='R1',lag=500):
    s=signal(rule=rule);s['asklimit']=.5
    m={'m':market()}
    b={'m':[book(1000),book(1000+lag),book(1000+lag+60000,bid=.6,ask=.62),book(1000+2*lag+60000,bid=.6,ask=.62)]}
    return [s],m,replay(m,b,[s],rule_id=rule,latency_ms=lag)

def check(s,m,r,rule='R1',lag=500):
    return E.audit_attempts(s,r['attempts'],r['exits'],m,rule,lag,r['config'])

class EvidenceTests(unittest.TestCase):
    def test_unmodified_settlement_and_exit_pass(self):
        for rule in ('R1','R2'):
            s,m,r=fixture(rule);self.assertEqual(check(s,m,r,rule),[])
    def test_dropped_signal_detected(self):
        s,m,r=fixture();r['attempts']=[]
        self.assertIn('signal_population_changed',{e['code'] for e in check(s,m,r)})
    def test_duplicate_attempt_detected(self):
        s,m,r=fixture();r['attempts']*=2
        self.assertIn('signal_population_changed',{e['code'] for e in check(s,m,r)})
    def test_limit_and_delay_changed_detected(self):
        s,m,r=fixture();r['attempts'][0]['limit_price']=.6;r['attempts'][0]['match_ms']+=150
        self.assertTrue({'fixed_limit_changed','total_latency_changed'}<={e['code'] for e in check(s,m,r)})
    def test_future_book_detected(self):
        s,m,r=fixture();r['attempts'][0]['decision_book_recv_ms']=1001
        self.assertIn('noncausal_or_stale_book',{e['code'] for e in check(s,m,r)})
    def test_wrong_label_and_cash_clock_detected(self):
        s,m,r=fixture();m['m']['outcome']='down';r['attempts'][0]['settlement_ms']-=1
        self.assertTrue({'payout_disagrees_with_market','cash_release_clock_changed'}<={e['code'] for e in check(s,m,r)})
    def test_premature_exit_detected(self):
        s,m,r=fixture('R2');r['exits'][0]['decision_ms']=1501
        self.assertIn('exit_before_holding_period',{e['code'] for e in check(s,m,r,'R2')})
    def test_missing_results_remain_null(self):
        with tempfile.TemporaryDirectory() as d:
            result=E.audit(d);self.assertFalse(result['passed']);self.assertIsNone(result['result'])
    def test_complete_bundle_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            def j(n,x): (p/n).write_text(json.dumps(x))
            def g(n,x):
                with gzip.open(p/n,'wt') as f:
                    for row in x:f.write(json.dumps(row)+'\n')
            arcs=[dict(name=str(i),bytes=1,sha256='a'*64) for i in range(21)]
            (p/'source_MANIFEST.txt').write_text('synthetic fixture only')
            j('registration.json',dict(segments=['B','C'],shares=100,initial_cash=1000,latencies_ms=[500,600,1000],rules=['R1','R2','R3'],archives=arcs,manifest_sha256=E.sha(p/'source_MANIFEST.txt')))
            signals=[];metrics=[]
            for rule in ('R1','R2','R3'):
                s,m,r=fixture(rule);s[0]['signal_id']=rule;signals.extend(s)
                for lag in (500,600,1000):
                    _,_,r=fixture(rule,lag);old=r['attempts'][0]['attempt_id']
                    for k in ('attempts','exits','cash_events'):
                        for row in r[k]:
                            if row.get('attempt_id')==old:row['attempt_id']=rule
                    stem=f'{rule}_{lag}ms'
                    for k in ('attempts','exits','cash_events'):g(stem+'_'+k+'.jsonl.gz',r[k])
                    j(stem+'_summary.json',{'summary':r['summary'],'config':r['config']});metrics.append(dict(rule=rule,latency_ms=lag))
            j('markets.json',m);g('signals.jsonl.gz',signals);j('metrics.json',metrics);j('kline_provenance.json',{})
            j('coverage.json',dict(complete=True,failures=[],unique_signals=3,days=[dict(archive=a['name'],bytes=a['bytes'],sha256=a['sha256'],status='verified_parsed_scored') for a in arcs]))
            j('run_status.json',dict(status='historical_capacity_replay_complete',alternatives=9))
            j('MANIFEST.json',{f.name:dict(bytes=f.stat().st_size,sha256=E.sha(f)) for f in p.iterdir()})
            result=E.audit(p);self.assertTrue(result['passed'],result['errors'])
            (p/'metrics.json').write_text('[]');result=E.audit(p)
            self.assertFalse(result['passed']);self.assertEqual(result['errors'][0]['code'],'artifact_integrity_failure')

if __name__=='__main__':unittest.main()
