"""Execution counterexamples; these are not strategy-performance evidence."""
import pytest

from mine500_constraints import check_constraints
from mine500_replay_v2 import ReplayConfig, replay_events


CANDIDATES = [{"id": "test", "required_features": []}]


def always_up(candidate, features):
    return "Up"


def fixture(price=0.5, size=5.0, constraints=True):
    market = {"type": "market", "market_id": "m", "recv_ms": 0,
              "start_ms": 0, "end_ms": 300000, "label_source": "gamma"}
    if constraints:
        market.update(min_order_size=5.0, up_tick_size=0.01,
                      down_tick_size=0.01, constraints_source="clob")
    return [
        {"type": "dataset", "recv_ms": 0, "dataset_id": "counterexample",
         "split": "development", "coverage_end_ms": 2000,
         "provenance": {"source": "synthetic_execution_counterexample"}},
        market,
        {"type": "book", "market_id": "m", "recv_ms": 1000,
         "up_asks": [[price, size]], "down_asks": [[0.5, size]],
         "healthy": True, "fee_rate": 0.07},
        {"type": "decision", "market_id": "m", "recv_ms": 1000,
         "features": {"tau_s": 299.0}, "feature_available_ms": {"tau_s": 1000}},
        {"type": "book", "market_id": "m", "recv_ms": 1500,
         "up_asks": [[price, size]], "down_asks": [[0.5, size]],
         "healthy": True, "fee_rate": 0.07},
        {"type": "outcome", "market_id": "m", "recv_ms": 300001,
         "resolved_ms": 300000, "winner": "Up", "source": "gamma"},
    ]


def run(events=None, **kwargs):
    return replay_events(events or fixture(), CANDIDATES, always_up,
                         ReplayConfig(latencies_ms=(500,), **kwargs))


def test_depth_and_extra_cost_flow_into_cash_and_pnl():
    result = run(depth_fraction=0.5, extra_cost_per_filled_share_usd=0.01,
                 require_constraints=True)
    row = result["attempts"][0]
    assert row["status"] == "filled_partial"
    assert row["filled_shares"] == 2.5
    assert row["fees_usd"] == pytest.approx(0.04375)
    assert row["extra_cost_usd"] == pytest.approx(0.025)
    assert row["cost_usd"] == pytest.approx(1.31875)
    assert row["cash_after_match"] == pytest.approx(998.68125)
    assert row["net_pnl_usd"] == pytest.approx(1.18125)
    assert result["summary"][0]["ending_cash_usd"] == pytest.approx(1001.18125)
    assert row["reserved_usd_at_decision"] == pytest.approx(2.6375)


def test_reservation_accounts_for_extra_cost_before_submit():
    result = run(initial_cash_usd=3, extra_cost_per_filled_share_usd=0.1)
    assert result["attempts"][0]["status"] == "rejected_insufficient_available_cash"
    assert result["summary"][0]["ending_cash_usd"] == 3


def test_fee_rounding_aggregates_price_levels_before_rounding():
    events = fixture(price=0.02)
    events[4]["up_asks"] = [[0.01, 1.25], [0.02, 3.75]]
    result = run(events)
    row = result["attempts"][0]
    # Individually rounded levels would total .00602; the aggregate-order
    # proxy is .00601. This does not establish actual venue trade partitioning.
    assert row["unrounded_fees_usd"] == pytest.approx(0.00601125)
    assert row["fees_usd"] == pytest.approx(0.00601)
    assert row["fee_rounding_adjustment_usd"] == pytest.approx(-0.00000125)
    assert row["cost_usd"] == pytest.approx(0.09351)
    assert row["reserved_fee_usd_at_decision"] == pytest.approx(0.00686)
    assert result["summary"][0]["fee_rounding_adjustment_usd"] == pytest.approx(-0.00000125)


def test_sub_minimum_fee_is_zero_and_half_up_ties_are_explicit_proxy():
    events = fixture()
    for event in events:
        if event["type"] == "book":
            event["fee_rate"] = 0.0000048
    row = run(events)["attempts"][0]
    assert row["unrounded_fees_usd"] == pytest.approx(0.000006)
    assert row["fees_usd"] == 0
    assert row["reserved_fee_usd_at_decision"] == pytest.approx(0.00001)
    for event in events:
        if event["type"] == "book":
            event["fee_rate"] = 0.000012
    result = run(events)
    assert result["attempts"][0]["unrounded_fees_usd"] == pytest.approx(0.000015)
    assert result["attempts"][0]["fees_usd"] == pytest.approx(0.00002)
    assert "assumptions" in result["fee_rounding_limitation"]


def test_rounded_fee_is_reserved_before_testing_cash():
    events = fixture(price=0.33)
    result = run(events, initial_cash_usd=1.727385)
    assert result["attempts"][0]["status"] == "rejected_insufficient_available_cash"
    result = run(events, initial_cash_usd=1.72739)
    row = result["attempts"][0]
    assert row["status"] == "filled_full"
    assert row["unrounded_fees_usd"] == pytest.approx(0.077385)
    assert row["fees_usd"] == pytest.approx(0.07739)
    assert row["reserved_fee_usd_at_decision"] == pytest.approx(0.07739)
    assert row["cash_after_match"] == pytest.approx(0, abs=1e-12)
    assert result["summary"][0]["ending_reserved_usd"] == 0


def test_missing_constraints_block_strict_and_never_retry_first_signal():
    events = fixture(constraints=False)
    events.insert(-1, {"type": "constraint", "market_id": "m", "recv_ms": 1600,
                      "min_order_size": 5, "up_tick_size": 0.01,
                      "constraints_source": "clob"})
    events.insert(-1, {"type": "decision", "market_id": "m", "recv_ms": 1600,
                      "features": {"tau_s": 298.4}, "feature_available_ms": {"tau_s": 1600}})
    result = run(events, require_constraints=True)
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["status"] == "rejected_missing_required_constraints_at_decision"
    assert result["summary"][0]["net_pnl_usd"] is None
    assert result["summary"][0]["missing_execution_inputs"] is True


def test_upper_bound_does_not_claim_missing_constraints_validated():
    result = run(fixture(constraints=False))
    assert result["attempts"][0]["status"] == "filled_full"
    assert result["attempts"][0]["constraints_at_decision"]["validated"] is False
    assert result["summary"][0]["filled_positions_with_validated_constraints"] == 0
    assert result["constraints_mode"] == "upper_bound_missing_constraints_not_validated"


def test_same_timestamp_metadata_after_decision_is_not_used_retroactively():
    events = fixture(constraints=False)
    events.insert(4, {"type": "constraint", "market_id": "m", "recv_ms": 1000,
                      "min_order_size": 5, "up_tick_size": 0.01,
                      "constraints_source": "clob"})
    result = run(events, require_constraints=True)
    assert result["attempts"][0]["status"] == "rejected_missing_required_constraints_at_decision"
    assert result["attempts"][0]["submitted"] is False


def test_limit_is_fixed_before_latency_and_no_crossing_retry():
    events = fixture()
    events[4]["up_asks"] = [[0.51, 100]]
    result = run(events)
    assert result["attempts"][0]["limit_price"] == 0.5
    assert result["attempts"][0]["status"] == "no_fill"
    assert result["attempts"][0]["extra_cost_usd"] == 0


def test_tick_change_can_invalidate_sent_limit_at_match():
    events = fixture(price=0.51)
    events.insert(4, {"type": "constraint", "market_id": "m", "recv_ms": 1200,
                      "up_tick_size": 0.1, "up_tick_size_source": "clob"})
    result = run(events, require_constraints=True)
    assert result["attempts"][0]["status"] == "rejected_limit_not_on_token_tick_at_match"
    assert result["attempts"][0]["filled_shares"] == 0
    assert result["summary"][0]["min_available_cash_usd"] < 1000
    assert result["summary"][0]["ending_cash_usd"] == 1000


def test_notional_floor_is_explicit_assumption_not_default_venue_rule():
    events = fixture(price=0.1)
    assert run(events)["attempts"][0]["status"] == "filled_full"
    result = run(events, min_notional_usd=1)
    assert result["attempts"][0]["status"] == "rejected_below_assumed_min_notional_at_decision"
    assert result["stress_assumptions"]["min_notional_usd"] == 1


def test_future_constraint_not_visible_and_other_token_not_substitute():
    market = {"recv_ms": 100, "min_order_size": 5, "down_tick_size": 0.01,
              "up_tick_size": 0.001, "up_tick_size_available_ms": 1001,
              "constraints_source": "clob"}
    audit, error = check_constraints(market, None, None, "Up", 1000, 5, 0.5, True)
    assert error == "missing_required_constraints"
    assert "up_tick_size" in audit["missing"]
    assert "up_tick_size" not in audit["values"]


def test_claimed_old_freeze_never_qualifies_new_engine_as_oos():
    events = fixture()
    events[0]["split"] = "prospective_holdout"
    events[0]["untouched_attestation"] = True
    events[0]["freeze"] = {"frozen_at_ms": 1}
    result = run(events)
    assert result["split_validation"]["effective_split"] == "development"
    assert result["summary"][0]["effective_split"] == "development"
    assert not result["summary"][0]["confirmed_edge"]


def test_decision_and_match_in_different_recordings_do_not_bridge_gap():
    events = fixture()
    # A fresh match-side book exists in the second interval, but continuity from
    # the order decision was not observed. Global coverage_end cannot repair it.
    events[0]["observed_intervals_ms"] = [[0, 1100], [1400, 2000]]
    result = run(events)
    row = result["attempts"][0]
    assert row["submitted"] is True
    assert row["status"] == "incomplete_match_horizon"
    assert row["filled_shares"] == 0
    assert result["summary"][0]["net_pnl_usd"] is None
    assert result["summary"][0]["ending_reserved_usd"] == 0
    assert result["summary"][0]["ending_cash_usd"] == 1000


def test_recording_tail_300ms_cannot_support_500ms_match_or_retry():
    events = fixture()
    events[0]["coverage_end_ms"] = 2500
    events[0]["observed_intervals_ms"] = [[0, 1300], [1800, 2500]]
    events.insert(-1, {"type": "book", "market_id": "m", "recv_ms": 1800,
                      "up_asks": [[0.5, 5]], "down_asks": [[0.5, 5]],
                      "healthy": True, "fee_rate": 0.07})
    events.insert(-1, {"type": "decision", "market_id": "m", "recv_ms": 1800,
                      "features": {"tau_s": 298.2}, "feature_available_ms": {"tau_s": 1800}})
    result = run(events)
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["status"] == "incomplete_match_horizon"
    assert result["summary"][0]["ending_reserved_usd"] == 0


def test_observed_interval_end_is_inclusive():
    events = fixture()
    events[0]["observed_intervals_ms"] = [[0, 1500], [1600, 2000]]
    assert run(events)["attempts"][0]["status"] == "filled_full"


@pytest.mark.parametrize("intervals", [[], [[0, 1000], [1000, 2000]],
    [[0, 1001], [1000, 2000]], [[1000, 2000], [0, 900]],
    [[0, 0]], [[0, float("nan")]], [[0, True]], [[0, 1, 2]]])
def test_malformed_or_overlapping_observed_intervals_rejected(intervals):
    events = fixture()
    events[0]["observed_intervals_ms"] = intervals
    with pytest.raises(ValueError, match="observed_intervals_ms"):
        run(events)


@pytest.mark.parametrize("kwargs", [{"depth_fraction": 0}, {"depth_fraction": 1.01},
    {"extra_cost_per_filled_share_usd": -0.01}, {"min_notional_usd": -1},
    {"require_constraints": "yes"}])
def test_invalid_stress_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        ReplayConfig(**kwargs)
