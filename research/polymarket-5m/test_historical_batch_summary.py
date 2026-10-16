"""Hand-calculated incomplete multi-day ledgers and non-additivity safeguards."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import historical_batch_summary as h


START = 1790812800000  # 2026-10-01 00:00 UTC
DAY = 86400000


def fixture(root, identity="first", latencies=(500,), unresolved=False, blocked=False):
    run = Path(root) / identity
    scenario = run / "shares5_base_release0"
    scenario.mkdir(parents=True)
    markets = [{"market_id": "a", "start_ms": START}, {"market_id": "b", "start_ms": START + DAY}]
    registration = {"mode": "isolated", "scenarios": [{"name": scenario.name}], "events_sha256": "same_tape",
                    "input_data_role": "consumed_development", "strategies": {"candidate_rules": [{"id": "A"}]},
                    "markets": markets}
    h.dump(run / "registration.json", registration)
    h.dump(run / "coverage.json", {"markets": markets})
    metrics, daily, cash, attempts = [], [], [], []
    for latency in latencies:
        status = "blocked_missing_features" if blocked else "incomplete_unresolved_or_truncated" if unresolved else "simulated_complete"
        metrics.append({"candidate_id": "A", "latency_ms": latency, "status": status,
                        "attempts": 0 if blocked else 2, "submitted_orders": 0 if blocked else 2,
                        "filled_orders": 0 if blocked else 2, "full_fills": 0, "partial_fills": 0 if blocked else 2,
                        "failed_or_unfilled_attempts": 0, "filled_shares": 0 if blocked else 5,
                        "net_pnl_usd": None if blocked or unresolved else 1,
                        "realized_pnl_usd": None if blocked else 2 if unresolved else 1,
                        "net_pnl_per_filled_share_usd": None if blocked or unresolved else .2,
                        "max_realized_drawdown_usd": 0 if unresolved or blocked else 1,
                        "initial_cash_usd": 1000, "ending_cash_usd": None if unresolved else 1001})
        for index, date in enumerate(("2026-10-01", "2026-10-02", "2026-10-03")):
            actual = index < 2
            pnl = 2 if index == 0 else -1
            day_status = "blocked_missing_features" if blocked else "no_signal_observed" if not actual else "incomplete_unresolved_or_truncated" if unresolved and index == 1 else "simulated_complete"
            daily.append({"candidate_id": "A", "latency_ms": latency, "market_start_utc_date": date,
                          "status": day_status, "expected_calendar_slots": 288,
                          "registered_markets": 1 if actual else 0, "observed_seconds": 300 if actual else 0,
                          "recorded_span_slots": 1 if actual else 0, "complete_span_slots": 1 if actual else 0,
                          "data_present_slots": 1 if actual else 0, "attempts": 1 if actual and not blocked else 0,
                          "net_pnl_usd": pnl if actual and not blocked and not (unresolved and index == 1) else None})
            if not actual or blocked:
                continue
            settled = not (unresolved and index == 1)
            attempts.append({"candidate_id": "A", "latency_ms": latency, "market_id": "ab"[index],
                             "settled": settled, "net_pnl_usd": pnl if settled else None,
                             "filled_shares": 2 if index == 0 else 3})
            cumulative = 2 if index == 0 or unresolved else 1
            cash.append({"candidate_id": "A", "latency_ms": latency, "event_sequence": index + 1,
                         "event_ms": START + index * DAY + 301000,
                         "report_cash_basis": "engine_recorded_event", "cash_usd": 1000 + cumulative,
                         "reserved_usd": 0, "realized_pnl_usd": cumulative})
    h.write_csv(scenario / "metrics.csv", metrics)
    h.write_csv(scenario / "daily.csv", daily)
    h.write_csv(scenario / "cash_ledger.csv", cash)
    h.dump(scenario / "audit.json", {"reporting": {"reconciliation_differences": []}})
    with gzip.open(scenario / "attempts.jsonl.gz", "wt") as handle:
        for row in attempts:
            handle.write(json.dumps(row) + "\n")
    h.dump(run / "completion.json", {"status": "completed_development_simulation", "run_key": identity,
                                      "output_sha256": {str(path.relative_to(run)): h.digest(path)
                                                        for path in run.rglob("*") if path.is_file()}})
    return run


class HistoricalSummaryTests(unittest.TestCase):
    def test_partial_calendar_is_not_full_window_and_missing_day_is_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory)
            output = Path(directory) / "out"
            h.summarize([run], output, 100)
            result = h.read_json(output / "summary.json")["results"][0]
            self.assertIsNone(result["net_pnl_usd"])
            self.assertEqual(result["recorded_scope_net_pnl_usd"], 1)
            self.assertEqual(result["settled_subset_pnl_usd"], 1)
            self.assertEqual(result["settled_subset_shares"], 5)
            self.assertEqual(result["registered_markets"], 2)
            self.assertEqual(result["calendar_dates"], 3)
            self.assertEqual(result["observed_dates"], 2)
            self.assertEqual(result["day_cluster_uncertainty"]["day_clusters"], 2)
            self.assertEqual(result["day_cluster_uncertainty"]["mean_observed_day_pnl_95pct_interval_usd"], [-1, 2])
            missing = list(h.read_csv(output / "daily.csv"))[-1]
            self.assertIsNone(missing["settled_subset_pnl_usd"])
            self.assertIsNone(missing["net_pnl_usd"])

    def test_unresolved_inventory_does_not_dilute_settled_share_return(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory, unresolved=True)
            output = Path(directory) / "out"
            h.summarize([run], output, 100)
            result = h.read_json(output / "summary.json")["results"][0]
            self.assertIsNone(result["recorded_scope_net_pnl_usd"])
            self.assertEqual(result["settled_subset_pnl_usd"], 2)
            self.assertEqual(result["settled_subset_shares"], 2)
            self.assertEqual(result["filled_shares"], 5)
            self.assertEqual(result["settled_subset_pnl_per_share_usd"], 1)
            self.assertTrue(result["settled_subset_is_incomplete"])

    def test_blocked_candidate_never_becomes_zero_profit_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory, blocked=True)
            output = Path(directory) / "out"
            h.summarize([run], output, 100)
            result = h.read_json(output / "summary.json")["results"][0]
            self.assertIsNone(result["net_pnl_usd"])
            self.assertIsNone(result["settled_subset_pnl_usd"])
            self.assertEqual(result["day_cluster_uncertainty"]["day_clusters"], 0)

    def test_latencies_and_overlapping_runs_never_sum(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory, latencies=(500, 1000))
            second = fixture(directory, identity="second", latencies=(500, 1000))
            output = Path(directory) / "out"
            h.summarize([run, run / "shares5_base_release0/metrics.csv", second], output, 100)
            report = h.read_json(output / "summary.json")
            self.assertEqual(report["source_run_count"], 2)
            self.assertEqual(len(report["results"]), 4)
            self.assertEqual([row["recorded_scope_net_pnl_usd"] for row in report["results"]], [1, 1, 1, 1])
            self.assertEqual(report["overlap_comparisons"][0]["shared_registered_markets"], 2)
            self.assertFalse(report["overlap_comparisons"][0]["aggregate_pnl_allowed"])

    def test_cash_day_drawdown_carries_prior_day_realized_equity(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory)
            output = Path(directory) / "out"
            h.summarize([run], output, 100)
            cash = list(h.read_csv(output / "cash_daily.csv"))
            self.assertEqual(cash[1]["opening_realized_pnl_usd"], 2)
            self.assertEqual(cash[1]["closing_realized_pnl_usd"], 1)
            self.assertEqual(cash[1]["intraday_realized_drawdown_usd"], 1)
            self.assertEqual(cash[1]["realized_pnl_change_usd"], -1)

    def test_tampered_source_or_pending_run_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            run = fixture(directory)
            (run / "shares5_base_release0/daily.csv").write_text("bad\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                h.summarize([run], Path(directory) / "out", 100)
            pending = fixture(directory, identity="third.pending.abc")
            with self.assertRaisesRegex(ValueError, "unfinished stage"):
                h.summarize([pending], Path(directory) / "out", 100)

    def test_one_day_or_unknown_coverage_has_no_bootstrap(self):
        result = h.day_bootstrap([{"observed_seconds": 100, "settled_subset_pnl_usd": 1,
                                  "settled_subset_shares": 5}], 100)
        self.assertIsNone(result["mean_observed_day_pnl_95pct_interval_usd"])
        self.assertEqual(result["day_clusters"], 1)

    def test_skipped_calendar_date_cannot_make_consecutive_complete_days(self):
        complete = {"expected_calendar_slots": 288, "observed_seconds": 86400,
                    "complete_span_slots": 288, "data_present_slots": 288}
        rows = [{**complete, "market_start_utc_date": date} for date in ("2026-10-01", "2026-10-03")]
        result = h.calendar_diagnostics(rows)
        self.assertEqual(result["calendar_span_dates"], 3)
        self.assertEqual(result["unreported_calendar_dates"], ["2026-10-02"])
        self.assertEqual(result["longest_consecutive_complete_days"], 1)
        self.assertFalse(result["all_dates_complete"])


if __name__ == "__main__":
    unittest.main()
