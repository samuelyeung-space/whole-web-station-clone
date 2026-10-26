import copy
import unittest
import patient_entry_replay as p
import mine500_replay_v2 as old

CANDIDATE = {"id": "P", "required_features": ["x"]}


def signal(c, f): return "Up" if f["x"] > 0 else None


def book(t, ask=.6, size=100., side_recv=None, healthy=True):
    return {"type": "book", "recv_ms": t, "market_id": "m", "up_asks": [[ask,size]],
        "down_asks": [[1-ask,size]], "fee_rate": .07, "healthy": healthy,
        "up_recv_ms": t if side_recv is None else side_recv,
        "down_recv_ms": t if side_recv is None else side_recv}


def decision(t, x=1, future=False):
    return {"type": "decision", "recv_ms": t, "market_id": "m",
        "features": {"x": x, "tau_s": (300000-t)/1000},
        "feature_available_ms": {"x": t+1 if future else t, "tau_s": t}}


def tape(extra=None, end=20000):
    events = [{"type":"dataset","recv_ms":0,"dataset_id":"synth","split":"development",
        "provenance":{"source":"synthetic_unit_test"}, "coverage_end_ms":end,
        "observed_intervals_ms":[[0,end]]},
        {"type":"market","recv_ms":0,"market_id":"m","start_ms":0,"end_ms":300000,"label_source":"gamma"},
        book(1000), decision(1000), book(1500),
        {"type":"outcome","recv_ms":300000,"market_id":"m","resolved_ms":300000,"winner":"Up","source":"gamma"}]
    if extra: events += extra
    return sorted(events,key=lambda e:e["recv_ms"])


def config(**kw): return p.ReplayConfig(latencies_ms=(500,), shares=100, **kw)


class PatientTests(unittest.TestCase):
    def run_policy(self, events, names, **kw):
        return p.replay_events(events,[copy.deepcopy(CANDIDATE)],signal,config(**kw),names)

    def test_base100_matches_unchanged_parent(self):
        events=tape(); a=old.replay_events(copy.deepcopy(events),[CANDIDATE],signal,config())
        b=self.run_policy(copy.deepcopy(events),["BASE100"])
        for name in ["attempts","submitted_orders","filled_shares","net_pnl_usd","fees_usd","ending_cash_usd","max_realized_drawdown_usd"]:
            self.assertAlmostEqual(a["summary"][0][name],b["summary"][0][name])
        self.assertEqual(a["attempts"][0]["decision_ms"],b["attempts"][0]["decision_ms"])

    def test_first_cohort_all_policies_even_bad_initial_health(self):
        calls=[]
        def callback(c,f): calls.append(f["tau_s"]); return signal(c,f)
        events=tape([book(2000,.59),decision(2000),book(2500,.59)])
        events[2]["healthy"]=False
        out=p.replay_events(events,[CANDIDATE],callback,config(),["BASE100","WAIT1","NOW200"])
        self.assertEqual(len(calls),1)
        self.assertEqual(len(out["parent_first_signal_cohorts"]),1)
        self.assertEqual({a["parent_first_signal_ms"] for a in out["attempts"]},{1000})
        self.assertIn("rejected_unhealthy_book_at_decision",[a["status"] for a in out["attempts"]])

    def test_wait_limit_is_initial_anchor_not_new_best_ask(self):
        out=self.run_policy(tape([book(2000,.58),decision(2000),book(2500,.59)]),["WAIT1"])
        a=out["attempts"][0]
        self.assertEqual(a["decision_ms"],2000)
        self.assertAlmostEqual(a["limit_price"],.59)
        self.assertEqual(a["filled_shares"],100)

    def test_book_projection_is_not_wait_decision(self):
        out=self.run_policy(tape([book(1600,.58),book(1800,.58)]),["WAIT1"])
        self.assertEqual(out["summary"][0]["submitted_orders"],0)
        self.assertEqual(out["summary"][0]["waiting_legs_expired"],1)

    def test_deadline_is_end_exclusive(self):
        out=self.run_policy(tape([book(11000,.58),decision(11000),book(11500,.58)]),["WAIT1"])
        self.assertEqual(out["summary"][0]["submitted_orders"],0)
        self.assertEqual(out["summary"][0]["net_pnl_usd"],0)

    def test_partial_does_not_retry_and_cash_reconciles(self):
        events=tape([book(2000,.59,30),decision(2000),book(2500,.59,30),
                     book(3000,.58,100),decision(3000),book(3500,.58,100)])
        out=self.run_policy(events,["WAIT1"])
        self.assertEqual(out["summary"][0]["attempts"],1)
        self.assertEqual(out["summary"][0]["filled_shares"],30)
        a=out["attempts"][0]
        self.assertAlmostEqual(out["summary"][0]["ending_cash_usd"],1000+30-a["cost_usd"])

    def test_same_snapshot_shared_depth_not_reused(self):
        events=tape([book(1400,.58,100),decision(2000)])
        # Remove the1500 book because it would otherwise be a fresh snapshot.
        events=[e for e in events if not(e["type"]=="book" and e["recv_ms"]==1500)]
        out=self.run_policy(events,["ADD100_100"],max_quote_age_ms=5000)
        self.assertEqual(out["summary"][0]["attempts"],2)
        self.assertEqual(out["summary"][0]["filled_shares"],100)
        self.assertEqual(out["attempts"][1]["status"],"no_fill")

    def test_no_future_feature_selection(self):
        events=tape([decision(2000)])
        for e in events:
            if e["type"]=="decision" and e["recv_ms"]==1000:e["feature_available_ms"]["x"]=1001
        out=self.run_policy(events,["BASE100","WAIT1"])
        self.assertEqual(out["parent_first_signal_cohorts"][0]["first_signal_ms"],2000)

    def test_cash_reservations_prevent_borrowing(self):
        out=self.run_policy(tape(),["NOW300"],initial_cash_usd=100)
        self.assertEqual(out["summary"][0]["submitted_orders"],0)
        self.assertEqual(out["attempts"][0]["status"],"rejected_insufficient_available_cash")
        self.assertEqual(out["summary"][0]["ending_cash_usd"],100)

    def test_unknown_labels_and_wait_horizon_remain_null(self):
        events=[e for e in tape(end=1300) if e["type"]!="outcome"]
        out=self.run_policy(events,["BASE100","WAIT1"])
        self.assertTrue(all(r["net_pnl_usd"] is None for r in out["summary"]))
        self.assertTrue(all(r["full_window_pnl_usd"] is None for r in out["summary"]))


if __name__=="__main__":unittest.main(verbosity=2)
