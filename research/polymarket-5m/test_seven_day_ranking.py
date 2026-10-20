"""Hand-built accounting fixtures test admission, never financial evidence."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from replay_reporting import write_reports
from seven_day_ranking import DAY, SLOT, IDENTITY_KEYS, STRESSES, assess, fingerprint, sha


class SevenDayRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'run'
        self.root.mkdir()
        self.markets = [{"market_id": "BTC-" + str(i), "start_ms": i * SLOT,
                         "end_ms": (i + 1) * SLOT, "label_source": "gamma"} for i in range(2016)]
        self.coverage = {"markets": self.markets, "daily": [
            {"date_utc": "1970-01-" + str(i + 1).zfill(2), "observed_seconds": 86400,
             "complete_span_slots": 288, "data_present_slots": 288, "gaps_ms": []} for i in range(7)]}
        rules = [{"id": "HAND-1", "required_features": ["x"]}]
        strategies = {"candidate_rules": rules, "candidate_sha256": fingerprint(rules),
                      "plugin_code_sha256": {"hand_strategy": "a" * 64}}
        self.scenarios = []
        for name, (depth, cost) in STRESSES.items():
            config = {"shares": 100, "initial_cash_usd": 1000, "latencies_ms": [500, 600, 1000],
                      "max_quote_age_ms": 250, "require_constraints": True, "cash_release_delay_ms": 0,
                      "full_or_zero": False, "side_specific_books": False, "min_notional_usd": 0,
                      "depth_fraction": depth, "extra_cost_per_filled_share_usd": cost}
            self.scenarios.append({"name": name, "config": config})
        self.registration = {"schema": "replay-lab-run-v1", "cache_key": "b" * 64,
                             "events_sha256": "c" * 64, "strategies": strategies, "mode": "isolated",
                             "priority": None, "scenarios": self.scenarios,
                             "code_sha256": {name: "d" * 64 for name in ("replay_lab.py", "replay_dataset.py", "replay_runner.py", "replay_engine.py", "replay_reporting.py", "mine500_constraints.py")},
                             "require_full_days": 7, "formal_oos_result": None,
                             "registered_at_utc": "1970-01-09T00:00:00Z", "input_data_role": "consumed_development",
                             "model_fitting": False, "cash_scope": "separate per candidate and latency"}
        self.protocol = {"schema": "rank7-protocol-v2", "registered_at_utc": "1970-01-08T00:00:00Z",
                         "admission_code_sha256":{name:sha(Path(__file__).with_name(name)) for name in ('seven_day_ranking.py','rank7_input_window.py')},
                         "window_start_utc": "1970-01-01T00:00:00Z", "window_end_utc": "1970-01-08T00:00:00Z",
                         "candidate_sha256": fingerprint(rules), "strategy_identity_sha256": fingerprint(strategies),
                         "code_sha256": self.registration["code_sha256"], "cache_key": "b" * 64,
                         "events_sha256": "c" * 64,
                         "market_universe": {"asset": "BTC", "interval_seconds": 300,
                                             "market_ids": [m["market_id"] for m in self.markets]},
                         "benchmark": {"initial_cash_usd": 1000, "requested_shares": 100,
                                       "primary_latency_ms": 500, "latencies_ms": [500, 600, 1000],
                                       "max_quote_age_ms": 250, "require_constraints": True, "cash_release_delay_ms": 0}}
        self.results = {}
        for scenario in self.scenarios:
            attempts, summaries = [], []
            for latency in (500, 600, 1000):
                extra = 100 * scenario["config"]["extra_cost_per_filled_share_usd"]
                pnl = 58.32 - extra
                attempts.append({"candidate_id": "HAND-1", "latency_ms": latency, "market_id": "BTC-0",
                                 "decision_ms": 100, "match_ms": 100 + latency, "status": "filled_full",
                                 "submitted": True, "filled_shares": 100, "fees_usd": 1.68,
                                 "extra_cost_usd": extra, "cost_usd": 41.68 + extra, "settled": True,
                                 "settlement_ms": SLOT, "payout_usd": 100, "net_pnl_usd": pnl,
                                 "constraints_at_decision": {"validated": True}, "constraints_at_match": {"validated": True}})
                summaries.append({"candidate_id": "HAND-1", "latency_ms": latency, "status": "simulated_complete",
                                  "initial_cash_usd": 1000, "requested_shares": 100, "cash_release_delay_ms": 0,
                                  "attempts": 1, "submitted_orders": 1, "full_fills": 1, "partial_fills": 0,
                                  "filled_shares": 100, "fees_usd": 1.68, "settled_positions": 1,
                                  "unresolved_positions": 0, "missing_execution_inputs": False, "net_pnl_usd": pnl})
            self.results[scenario["name"]] = {"mode": "isolated", "engine_config": scenario["config"],
                                              "constraints_mode": "required", "inherited_execution_assumptions": [],
                                              "summary": summaries, "attempts": attempts, "cash_events": []}
        self.events=[dict(type='dataset',recv_ms=0,dataset_id='synthetic-rank7',
            split='development',provenance={'source':'synthetic engineering fixture, not market data'},
            coverage_start_ms=0,coverage_end_ms=7*DAY)]
        for m in self.markets:
            mid=m['market_id'];at=m['start_ms'];end=m['end_ms']
            self.events.extend([
                dict(type='market',recv_ms=at,**m),
                dict(type='book',recv_ms=at+10,market_id=mid,healthy=True,fee_rate=.07,up_asks=[[.4,100]],down_asks=[[.6,100]]),
                dict(type='decision',recv_ms=at+100,market_id=mid,features={},feature_available_ms={}),
                dict(type='outcome',recv_ms=end,market_id=mid,resolved_ms=end,source='gamma',winner='Up')])
        self.bind_input()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, data):
        path.write_text(json.dumps(data, allow_nan=False) + "\n")

    def bind_input(self):
        import replay_dataset
        from replay_dataset import ingest
        source=Path(self.temp.name)/'synthetic_input.jsonl'
        source.write_text(''.join(json.dumps(e)+'\n' for e in sorted(self.events,key=lambda e:e['recv_ms'])))
        manifest=ingest([source],Path(self.temp.name)/'input-cache')
        self.cache=Path(manifest['cache_path'])
        for target in (self.registration,self.protocol):
            target['cache_key']=manifest['dataset_key']
            target['events_sha256']=manifest['tape_sha256']
            target['code_sha256']['replay_dataset.py']=sha(replay_dataset.__file__)

    def assess(self):
        return assess(self.protocol,self.root,self.cache)

    def build(self):
        reg = self.registration
        reg["run_key"] = fingerprint({k: reg[k] for k in IDENTITY_KEYS})
        self.write(self.root / "registration.json", reg)
        self.write(self.root / "coverage.json", self.coverage)
        metrics = []
        for scenario in reg["scenarios"]:
            result = self.results[scenario["name"]]
            write_reports(self.root / scenario["name"], result, self.coverage, reg)
            metrics.extend({"scenario": scenario["name"], **row} for row in result["summary"])
        self.write(self.root / "metrics.json", metrics)
        self.write(self.root / "completion.json", {"run_key": reg["run_key"], "mode": reg["mode"],
                   "status": "completed_development_simulation", "formal_oos_result": None,
                   "candidate_count": 1, "execution_scenario_rows": len(metrics), "scenario_count": len(reg["scenarios"]),
                   "output_sha256": {str(p.relative_to(self.root)): sha(p) for p in self.root.rglob("*")
                                     if p.is_file() and p.name != "completion.json"}})

    def blocked(self, fragment):
        self.build()
        result = self.assess()
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn(fragment, result["reasons"][0])
        self.assertEqual(result["ranking"], [])
        self.assertIsNone(result["net_pnl_usd"])
        self.assertIsNone(result["formal_oos_result"])

    def test_native_positive_fixture_ranks_primary_only_and_preserves_all_stresses(self):
        self.build()
        result = self.assess()
        self.assertEqual(result["status"], "assessed_development_only", result)
        self.assertEqual(len(result["ranking"]), 1)
        self.assertEqual(result["ranking"][0]["primary_net_pnl_usd"], 58.32)
        self.assertEqual(len(result["ranking"][0]["cells"]), 9)
        self.assertIsNone(result["formal_oos_result"])
        self.assertEqual(result["qualified_strategies"], 0)

    def test_six_day_and_nonconsecutive_coverage_block(self):
        self.coverage["daily"].pop()
        self.blocked("consecutive coverage")
        self.coverage["daily"].append({**self.coverage["daily"][-1], "date_utc": "1970-01-09"})
        self.blocked("consecutive coverage")

    def test_declared_spans_alone_are_insufficient(self):
        self.coverage["daily"][0]["data_present_slots"] = 1
        self.blocked("incomplete daily")

    def test_tiny_gap_and_duplicate_slot_block(self):
        self.coverage["daily"][0]["gaps_ms"] = [[100, 101]]
        self.blocked("incomplete daily")
        self.coverage["daily"][0]["gaps_ms"] = []
        self.markets[1].update(start_ms=0, end_ms=SLOT)
        self.blocked("duplicate or missing")

    def test_shared_wrong_cash_old_size_block(self):
        self.registration["mode"] = "shared"
        self.blocked("shared")
        self.registration["mode"] = "isolated"
        self.scenarios[0]["config"]["initial_cash_usd"] = 10000
        self.blocked("configuration")
        self.scenarios[0]["config"]["initial_cash_usd"] = 1000
        self.scenarios[0]["config"]["shares"] = 5
        self.blocked("configuration")

    def test_broad_run_with_outside_attempt_cannot_masquerade_as_window(self):
        self.results["base"]["attempts"][0]["decision_ms"] = -1
        self.blocked("outside exact seven-day")

    def test_non_midnight_wrong_length_and_changed_rule_pin_block(self):
        self.protocol["window_start_utc"] = "1970-01-01T01:00:00Z"
        self.blocked("midnight")
        self.protocol["window_start_utc"] = "1970-01-01T00:00:00Z"
        self.protocol["window_end_utc"] = "1970-01-07T00:00:00Z"
        self.blocked("exactly seven")
        self.protocol["window_end_utc"] = "1970-01-08T00:00:00Z"
        self.protocol["candidate_sha256"] = "0" * 64
        self.blocked("candidate rule")

    def test_missing_stress_and_native_constraint_proof_block(self):
        removed = self.registration["scenarios"].pop()
        self.blocked("all base/half_depth")
        self.registration["scenarios"].append(removed)
        self.results["base"]["inherited_execution_assumptions"] = [{"assumed": True}]
        self.blocked("native independent")

    def test_late_freeze_and_strategy_dependency_change_block(self):
        self.protocol["registered_at_utc"] = "1970-01-10T00:00:00Z"
        self.blocked("after replay")
        self.protocol["registered_at_utc"] = "1970-01-08T00:00:00Z"
        self.registration["strategies"]["plugin_code_sha256"]["hand_strategy"] = "0" * 64
        self.blocked("code/dependency")

    def test_damaged_file_and_missing_hash_block(self):
        self.build()
        path = self.root / "base/attempts.jsonl.gz"
        path.write_bytes(path.read_bytes()[:-8])
        self.assertEqual(self.assess()["status"], "blocked")
        self.build()
        complete = json.loads((self.root / "completion.json").read_text())
        complete["output_sha256"].pop("metrics.json")
        self.write(self.root / "completion.json", complete)
        self.assertIn("required output hashes", self.assess()["reasons"][0])

    def test_no_signal_and_missing_cells_remain_unranked_null(self):
        for result in self.results.values():
            result["attempts"] = []
            for row in result["summary"]:
                row.update(status="evaluated_no_signal", attempts=0, submitted_orders=0, full_fills=0,
                           filled_shares=0, fees_usd=0, settled_positions=0, net_pnl_usd=None)
        self.results["base"]["summary"].pop()
        self.build()
        result = self.assess()
        self.assertEqual(result["status"], "assessed_development_only", result)
        self.assertEqual(result["ranking"], [])
        self.assertEqual(len(result["unranked"]), 1)
        self.assertTrue(any(r["status"] is None for r in result["unranked"][0]["reasons"]))

    def test_no_fills_zero_diagnostic_is_not_ranked(self):
        for result in self.results.values():
            for attempt in result["attempts"]:
                attempt.update(status="no_fill_price_moved", filled_shares=0, fees_usd=0,
                               extra_cost_usd=0, cost_usd=0, settled=False, net_pnl_usd=0)
            for row in result["summary"]:
                row.update(status="evaluated_no_fills", full_fills=0, filled_shares=0,
                           fees_usd=0, settled_positions=0, net_pnl_usd=0)
        self.build()
        result = self.assess()
        self.assertEqual(result["status"], "assessed_development_only", result)
        self.assertEqual(result["ranking"], [])
        self.assertEqual(result["unranked"][0]["cells"][0]["metrics"]["net_pnl_usd"], 0)

    def test_duplicate_metric_cells_block(self):
        self.results["base"]["summary"].append(deepcopy(self.results["base"]["summary"][0]))
        # write_reports rightly refuses duplicates first; model corruption
        # after valid reports and refresh only the affected completion hash.
        self.results["base"]["summary"].pop()
        self.build()
        path = self.root / "metrics.json"
        rows = json.loads(path.read_text())
        rows.append(deepcopy(rows[0]))
        self.write(path, rows)
        cp = self.root / "completion.json"
        complete = json.loads(cp.read_text())
        complete["execution_scenario_rows"] += 1
        complete["output_sha256"]["metrics.json"] = sha(path)
        self.write(cp, complete)
        self.assertIn("duplicate/unexpected", self.assess()["reasons"][0])

    def test_path_escape_and_null_template_fail_closed(self):
        self.build()
        cp = self.root / "completion.json"
        complete = json.loads(cp.read_text())
        complete["output_sha256"]["../outside.json"] = "0" * 64
        self.write(cp, complete)
        self.assertIn("unsafe or missing", self.assess()["reasons"][0])
        self.protocol["window_start_utc"] = None
        self.assertIn("UTC timestamp", self.assess()["reasons"][0])

    def test_negative_completed_filled_strategy_is_rankable(self):
        for result in self.results.values():
            for attempt in result["attempts"]:
                attempt["net_pnl_usd"] = -41.68
                attempt["payout_usd"] = 0
            for row in result["summary"]:
                row["net_pnl_usd"] = -41.68
        self.build()
        result = self.assess()
        self.assertEqual(result["status"], "assessed_development_only", result)
        self.assertEqual(result["ranking"][0]["primary_net_pnl_usd"], -41.68)

    def test_filled_attempt_with_null_constraints_blocks_cleanly(self):
        self.results["base"]["attempts"][0]["constraints_at_decision"] = None
        self.blocked("filled position lacks native constraints")

    def test_unused_market_missing_official_outcome_cannot_hide_in_counts(self):
        self.events=[e for e in self.events if not(e['type']=='outcome' and e.get('market_id')=='BTC-2015')]
        self.bind_input()
        self.blocked('per-market input evidence missing')

    def test_unused_market_missing_book_cannot_hide_in_counts(self):
        self.events=[e for e in self.events if not(e['type']=='book' and e.get('market_id')=='BTC-2015')]
        self.bind_input()
        self.blocked('per-market input evidence missing')

    def test_unused_market_missing_decision_cannot_hide_in_counts(self):
        self.events=[e for e in self.events if not(e['type']=='decision' and e.get('market_id')=='BTC-2015')]
        self.bind_input()
        self.blocked('per-market input evidence missing')

    def test_cannot_rank_without_actual_cache(self):
        self.build()
        result=assess(self.protocol,self.root)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('pinned input cache required',result['reasons'][0])
        self.assertEqual(result['ranking'],[])

    def test_pinned_digest_must_match_actual_input(self):
        for target in (self.registration,self.protocol):target['events_sha256']='0'*64
        self.blocked('cache/event identity mismatch')

    def test_input_corruption_is_not_certified_by_good_output_reports(self):
        self.build()
        tape=self.cache/'events.jsonl.gz';tape.write_bytes(tape.read_bytes()[:-8])
        result=self.assess()
        self.assertEqual(result['status'],'blocked')
        self.assertIn('cached tape corrupted',result['reasons'][0])

    def test_new_protected_input_cannot_enter_development_check(self):
        self.events[0]['split']='formal_oos'
        self.bind_input()
        self.blocked('explicitly consumed development cache')

    def test_actual_tiny_recording_gap_cannot_hide_in_report(self):
        self.events[0]['observed_intervals_ms']=[[0,1],[2,7*DAY]]
        self.bind_input()
        self.blocked('actual input lacks complete day')

    def test_input_and_report_market_definitions_must_match(self):
        for event in self.events:
            if event['type']=='market' and event.get('market_id')=='BTC-2015':
                event['start_ms']+=1;event['end_ms']+=1
            if event['type']=='outcome' and event.get('market_id')=='BTC-2015':
                event['resolved_ms']+=1;event['recv_ms']+=1
        self.bind_input()
        self.blocked('actual input market definitions differ')

    def test_positive_fixture_reports_input_evidence_not_oos(self):
        self.build();result=self.assess();evidence=result['input_window_evidence']
        self.assertEqual(evidence['verified_market_count'],2016)
        self.assertTrue(evidence['all_pinned_markets_have_books_decisions_official_outcomes'])
        self.assertFalse(evidence['feed_health_certified'])
        self.assertIsNone(evidence['formal_oos_result'])

    def test_current_input_validator_must_match_pinned_code(self):
        for target in (self.registration,self.protocol):target['code_sha256']['replay_dataset.py']='0'*64
        self.blocked('input adapter code differs')

    def test_old_protocol_cannot_inherit_new_admission(self):
        self.protocol['schema']='rank7-protocol-v1'
        self.blocked('unsupported protocol schema')

    def test_changed_admission_code_cannot_inherit_frozen_identity(self):
        self.protocol['admission_code_sha256']['rank7_input_window.py']='0'*64
        self.blocked('ranking admission code/dependency identity mismatch')


if __name__ == "__main__":
    unittest.main()

