"""Adversarial dispatch/capital/clock tests; synthetic fixtures are not backtests."""
import copy
import importlib
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import replay_runner as rr


class ReplayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sys.path.insert(0, str(self.root))
        self.modules = []

    def tearDown(self):
        sys.path.remove(str(self.root))
        for name in self.modules:
            sys.modules.pop(name, None)
        self.tmp.cleanup()

    def plugin(self, candidates=None, declaration="", provider="catalog", body=None, extra=""):
        name = "test_strategy_" + uuid.uuid4().hex
        self.modules.append(name)
        candidates = candidates if candidates is not None else [
            {"id": "A", "required_features": ["x"], "side": "Up"},
            {"id": "B", "required_features": ["x"], "side": "Down"}]
        source = declaration + "\ndef " + provider + "():\n    return " + repr(candidates) + "\n"
        source += "def signal(candidate, features):\n"
        source += body or "    assert set(features) == set(candidate['required_features']) | {'tau_s'}\n    return candidate.get('side', 'Up') if features.get('x', 1) > 0 else None\n"
        (self.root / (name + ".py")).write_text(source + "\n" + extra)
        importlib.invalidate_caches()
        return name

    @staticmethod
    def events(two_markets=False, depth=100, later_decision=True):
        out = [{"type": "dataset", "recv_ms": 0, "dataset_id": "synthetic",
                "provenance": {"source": "synthetic-test-not-real-backtest"},
                "split": "development", "coverage_end_ms": 2000}]
        mids = ["m1", "m2"] if two_markets else ["m1"]
        for mid in mids:
            out.append({"type": "market", "recv_ms": 0, "market_id": mid,
                        "start_ms": 0, "end_ms": 300000, "label_source": "gamma"})
        for mid in mids:
            out.append({"type": "book", "recv_ms": 100, "market_id": mid,
                        "up_asks": [[.4, depth]], "down_asks": [[.5, depth]],
                        "fee_rate": .07, "healthy": True})
        for i, mid in enumerate(mids):
            now = 100 + i
            out.append({"type": "decision", "recv_ms": now, "market_id": mid,
                        "features": {"tau_s": (300000-now)/1000, "x": 1, "future_unrequested": 100},
                        "feature_available_ms": {"tau_s": now, "x": now, "future_unrequested": now+9999}})
        if later_decision:
            out.append({"type": "decision", "recv_ms": 900, "market_id": "m1",
                        "features": {"tau_s": 299.1, "x": 1},
                        "feature_available_ms": {"tau_s": 900, "x": 900}})
        for mid in mids:
            out.append({"type": "outcome", "recv_ms": 300000, "market_id": mid,
                        "resolved_ms": 300000, "winner": "Up", "source": "gamma"})
        return out

    @staticmethod
    def config(**kw):
        return rr.ex.ReplayConfig(**{"shares": 100, "max_quote_age_ms": 1000,
                                   "latencies_ms": (500, 600), **kw})

    def test_wildcard_and_three_catalog_provider_names(self):
        for provider in ("catalog", "candidates", "make_candidates"):
            name = self.plugin(provider=provider)
            loaded = rr.load_strategies([name + ":*"])
            self.assertEqual([x["id"] for x in loaded.candidates], ["A", "B"])
            self.assertEqual(len(loaded.plugin_code_sha256[name]), 64)
            self.assertEqual(loaded.registration["candidate_count"], 2)
        name = self.plugin(candidates=[{"id": "A", "required_features": []}], provider="make_candidates",
            extra="def make_supplementary_candidates():\n    return [{'id':'C','required_features':[]}]\n")
        self.assertEqual(len(rr.load_strategies([name + ":*"]).candidates), 2)
        self.assertEqual(rr.load_strategies([name + ":C"]).candidates[0]["id"], "C")

    def test_shared_collision_single_depth_single_cash_and_separate_latencies(self):
        name = self.plugin()
        events = self.events(two_markets=True)
        unchanged = copy.deepcopy(events)
        result = rr.execute(events, [name + ":*"], self.config(initial_cash_usd=50), mode="shared")
        self.assertEqual(events, unchanged)
        self.assertEqual(len(result["attempts"]), 4)  # one market order, two independent scenarios
        self.assertEqual(len(result["chosen_signals"]), 2)
        self.assertEqual(len(result["suppressed_signals"]), 2)
        self.assertTrue(all(x["selected_candidate"] == "A" for x in result["attempts"]))
        for row in result["summary"]:
            self.assertEqual(row["filled_shares"], 100)
            self.assertEqual(row["status_counts"]["rejected_insufficient_available_cash"], 1)
            self.assertAlmostEqual(row["net_pnl_usd"], 58.32)
            self.assertAlmostEqual(row["ending_cash_usd"], 108.32)
        self.assertEqual(result["pair_overlap"][0]["shared_markets"], 2)
        self.assertEqual(result["pair_overlap"][0]["opposite_first_signal_markets"], 2)
        self.assertEqual(result["engine_config"]["shares"], 100)

    def test_fixed_priority_controls_collision(self):
        name = self.plugin()
        result = rr.execute(self.events(), [name + ":*"], self.config(), mode="shared", priority=["B", "A"])
        self.assertEqual(result["chosen_signals"][0]["selected_candidate"], "B")
        self.assertTrue(all(x["side"] == "Down" for x in result["attempts"]))
        with self.assertRaises(ValueError):
            rr.execute([], [name + ":*"], self.config(), mode="shared", priority=["A"])

    def test_isolated_keeps_independent_candidate_ledgers(self):
        name = self.plugin()
        result = rr.execute(self.events(), [name + ":*"], self.config(), mode="isolated")
        self.assertEqual(len(result["summary"]), 4)
        self.assertTrue(all(x["filled_shares"] == 100 for x in result["summary"]))
        self.assertEqual({x["candidate_id"] for x in result["attempts"]}, {"A", "B"})

    def test_all_missing_features_remain_blocked_and_null(self):
        name = self.plugin(candidates=[{"id": "missing", "required_features": ["absent"]}])
        for mode in ("isolated", "shared"):
            result = rr.execute(self.events(), [name + ":*"], self.config(), mode=mode)
            self.assertFalse(result["attempts"])
            self.assertTrue(all(x["status"] == "blocked_no_valid_feature_rows" for x in result["summary"]))
            self.assertTrue(all(x["net_pnl_usd"] is None for x in result["summary"]))
        self.assertEqual(result["component_feature_audit"]["missing"]["status"], "blocked_no_valid_feature_rows")

    def test_missing_component_does_not_block_valid_component(self):
        name = self.plugin(candidates=[{"id": "missing", "required_features": ["absent"]},
                                      {"id": "good", "required_features": ["x"]}])
        result = rr.execute(self.events(), [name + ":*"], self.config(), mode="shared")
        self.assertTrue(result["attempts"])
        self.assertTrue(all(x["selected_candidate"] == "good" for x in result["attempts"]))
        self.assertEqual(result["component_feature_audit"]["missing"]["net_pnl_usd"], None)

    def test_no_retry_after_first_order_rejected(self):
        name = self.plugin()
        events = self.events()
        first_book = next(e for e in events if e["type"] == "book")
        first_book["healthy"] = False
        recovery = {**first_book, "recv_ms": 800, "healthy": True}
        events.insert(next(i for i, e in enumerate(events) if e["recv_ms"] == 900), recovery)
        result = rr.execute(events, [name + ":*"], self.config(), mode="shared")
        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(all(x["decision_ms"] == 100 for x in result["attempts"]))
        self.assertTrue(all(x["status"] == "rejected_unhealthy_book_at_decision" for x in result["attempts"]))

    def test_future_features_invalid_clock_and_nonfinite_are_rejected(self):
        name = self.plugin()
        for mutation in ("future", "clock", "nan", "map"):
            events = self.events(later_decision=False)
            decision = next(e for e in events if e["type"] == "decision")
            if mutation == "future": decision["feature_available_ms"]["x"] = 101
            elif mutation == "clock": decision["features"]["tau_s"] = 100
            elif mutation == "nan": decision["features"]["x"] = float("nan")
            else: decision["features"] = 123
            result = rr.execute(events, [name + ":*"], self.config(), mode="shared")
            self.assertFalse(result["attempts"])
            self.assertTrue(all(x["net_pnl_usd"] is None for x in result["summary"]))

    def test_future_book_is_not_used_for_earlier_match(self):
        name = self.plugin(candidates=[{"id": "A", "required_features": ["x"]}])
        events = self.events(later_decision=False)
        # At 600ms the 500ms scenario must retain 100-share depth. At 700ms the
        # 600ms scenario sees depth disappear at 601ms and receives zero fill.
        events.insert(-1, {"type": "book", "recv_ms": 601, "market_id": "m1",
            "up_asks": [[.4, 0]], "down_asks": [[.5, 0]], "fee_rate": .07, "healthy": True})
        result = rr.execute(events, [name + ":*"], self.config(), mode="shared")
        self.assertEqual([x["filled_shares"] for x in result["summary"]], [100, 0])

    def test_partial_fak_and_full_or_zero_are_separate_sensitivities(self):
        name = self.plugin(candidates=[{"id": "A", "required_features": ["x"]}])
        for full, quantity in ((False, 40), (True, 0)):
            result = rr.execute(self.events(depth=40), [name + ":*"], self.config(full_or_zero=full), mode="shared")
            self.assertTrue(all(x["filled_shares"] == quantity for x in result["summary"]))

    def test_actual_cash_trace_reservation_fill_settlement_and_delayed_release(self):
        name = self.plugin(candidates=[{"id": "A", "required_features": ["x"]}])
        events = self.events()
        events.append({"type": "outcome", "recv_ms": 301000, "market_id": "m1",
                       "resolved_ms": 300000, "winner": "Up", "source": "gamma"})
        result = rr.execute(events, [name + ":*"], self.config(cash_release_delay_ms=1000), mode="shared")
        trace = result["cash_events"]
        self.assertEqual([x["event_sequence"] for x in trace], list(range(1, len(trace)+1)))
        for latency in (500, 600):
            rows = [x for x in trace if x["latency_ms"] == latency]
            self.assertEqual([x["event_type"] for x in rows],
                ["reservation", "reservation_release", "fill", "settlement", "payout_release"])
            self.assertAlmostEqual(rows[0]["reserved_usd"], 41.68)
            self.assertAlmostEqual(rows[2]["cash_usd"], 958.32)
            self.assertEqual(rows[3]["locked_payout_usd"], 100)
            self.assertEqual(rows[4]["locked_payout_usd"], 0)
            self.assertAlmostEqual(rows[-1]["cash_usd"], 1058.32)
        self.assertEqual(len({x["attempt_id"] for x in trace}), 2)

    def test_plugin_change_duplicates_invalid_declarations_and_id_are_rejected(self):
        name = self.plugin()
        loaded = rr.load_strategies([name + ":*"])
        with self.assertRaises(ValueError): rr.load_strategies([name + ":A", name + ":A"])
        with self.assertRaises(ValueError): rr.load_strategies([name + ":unknown"])
        with (self.root / (name + ".py")).open("a") as handle: handle.write("\n# changed\n")
        with self.assertRaises(ValueError): rr.execute([], loaded)
        for declaration in ("EXECUTION_STYLE='maker'", "EXECUTION_CONTRACT={'hedge':True}"):
            name = self.plugin(declaration=declaration)
            with self.assertRaises(ValueError): rr.load_strategies([name + ":*"])
        name = self.plugin(candidates=[{"id": "bad", "required_features": [], "order_type": "GTC"}])
        with self.assertRaises(ValueError): rr.load_strategies([name + ":*"])

    def test_current_source_bytes_override_cached_module_and_same_size_mtime_pyc(self):
        name = self.plugin(candidates=[{"id": "A", "required_features": ["x"]}],
                           body="    return 'Up'\n")
        path = self.root / (name + ".py")
        # Generate a real pyc and retain the original module as a stale object.
        original = importlib.import_module(name)
        before = path.stat()
        changed = path.read_bytes().replace(b"return 'Up'", b"return None")
        self.assertEqual(len(changed), before.st_size)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(original.signal({}, {}), "Up")
        loaded = rr.load_strategies([name + ":*"])
        self.assertIsNone(loaded.signal_functions["A"]({}, {}))
        self.assertEqual(loaded.plugin_code_sha256[name], hashlib.sha256(changed).hexdigest())
        result = rr.execute(self.events(), loaded, self.config())
        self.assertFalse(result["attempts"])
        # Subsequent imports also see the currently compiled module.
        self.assertIsNone(importlib.import_module(name).signal({}, {}))

    def test_source_loader_preserves_package_spec_and_relative_imports(self):
        package = "test_package_" + uuid.uuid4().hex
        folder = self.root / package; folder.mkdir()
        (folder / "__init__.py").write_text("")
        (folder / "helper.py").write_text("SIDE='Up'\n")
        (folder / "strategy.py").write_text(
            "from .helper import SIDE\n"
            "assert __package__ == '" + package + "'\n"
            "assert __spec__.name == __name__\n"
            "def catalog():\n    return [{'id':'PACKAGE','required_features':['x']}]\n"
            "def signal(c,f):\n    return SIDE\n")
        self.modules.extend([package, package + ".helper", package + ".strategy"])
        importlib.invalidate_caches()
        loaded = rr.load_strategies([package + ".strategy:*"])
        self.assertEqual(loaded.signal_functions["PACKAGE"]({}, {}), "Up")

    def test_legacy_engine_results_are_unchanged_except_cash_audit(self):
        import joint_probability_execution as legacy
        candidates = [{"id": "A", "required_features": ["x"]}]
        def signal(c, f): return "Up"
        old = legacy.replay_events(self.events(two_markets=True), candidates, signal,
            legacy.ReplayConfig(shares=100, latencies_ms=(500, 600), max_quote_age_ms=1000, initial_cash_usd=50))
        new = rr.ex.replay_events(self.events(two_markets=True), candidates, signal,
            self.config(initial_cash_usd=50))
        self.assertEqual(old["summary"], new["summary"])
        self.assertEqual(old["attempts"], [{k: v for k, v in r.items() if k != "attempt_id"} for r in new["attempts"]])


if __name__ == "__main__":
    unittest.main()
