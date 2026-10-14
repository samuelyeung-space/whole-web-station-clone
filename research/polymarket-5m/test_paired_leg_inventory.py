import math

import pytest

from paired_leg_inventory import LegIntent, ReplayConfig, replay, taker_fee


def leg(order_id, side, *, arrival, ask, depth=5, settlement=1000, outcome=True):
    return LegIntent(
        order_id=order_id,
        pair_id=order_id.split("-")[0],
        market_id="m1",
        side=side,
        decision_ms=0,
        arrival_ms=arrival,
        settlement_ms=settlement,
        outcome_yes=outcome,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=ask,
        displayed_shares=depth,
    )


def test_two_filled_legs_include_exact_fees_and_settlement():
    result = replay([leg("p-yes", "YES", arrival=500, ask=0.45), leg("p-no", "NO", arrival=600, ask=0.45)])
    expected = 5 - 5 * 0.9 - 2 * taker_fee(5, 0.45, 0.07)
    assert result["counts"]["pairs_with_both_sides_filled"] == 1
    assert result["counts"]["pairs_with_single_side_filled"] == 0
    assert result["risk"]["total_unhedged_ms"] == 100
    assert result["risk"]["max_unhedged_episode_ms"] == 100
    assert result["risk"]["max_pair_arrival_gap_ms"] == 100
    assert result["cash"]["net_pnl"] == pytest.approx(expected)


def test_second_leg_cash_reject_creates_unhedged_loss():
    config = ReplayConfig(initial_cash=3.0)
    result = replay(
        [
            leg("p-yes", "YES", arrival=500, ask=0.50, outcome=False),
            leg("p-no", "NO", arrival=600, ask=0.50, outcome=False),
        ],
        config,
    )
    assert result["counts"]["cash_rejects"] == 1
    assert result["counts"]["pairs_with_single_side_filled"] == 1
    assert result["risk"]["max_unhedged_shares"] == 5
    assert result["risk"]["total_unhedged_ms"] == 500
    assert result["ledger"][1]["reason"] == "insufficient_shared_cash"
    assert result["cash"]["net_pnl"] < -2.5


def test_partial_fak_respects_depth_haircut():
    result = replay(
        [leg("p-yes", "YES", arrival=500, ask=0.4, depth=4)],
        ReplayConfig(depth_haircut=0.5),
    )
    assert result["counts"]["partial_fills"] == 1
    assert result["fills"][0]["filled_shares"] == 2


def test_full_or_zero_kills_insufficient_depth():
    result = replay(
        [leg("p-yes", "YES", arrival=500, ask=0.4, depth=4)],
        ReplayConfig(depth_haircut=0.5, full_or_zero=True),
    )
    assert result["counts"]["fills"] == 0
    assert result["counts"]["zero_fills"] == 1
    assert result["ledger"][0]["reason"] == "full_or_zero_depth_shortfall"


def test_settlement_releases_cash_before_same_timestamp_arrival():
    first = leg("a-yes", "YES", arrival=100, ask=0.5, settlement=500, outcome=True)
    second = LegIntent(
        order_id="b-yes",
        pair_id="b",
        market_id="m2",
        side="YES",
        decision_ms=499,
        arrival_ms=500,
        settlement_ms=1000,
        outcome_yes=True,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=0.5,
        displayed_shares=5,
    )
    result = replay([first, second], ReplayConfig(initial_cash=3.0))
    assert result["counts"]["full_fills"] == 2
    assert result["counts"]["cash_rejects"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        leg("p-yes", "YES", arrival=-1, ask=0.5),
        leg("p-yes", "YES", arrival=500, ask=math.nan),
        leg("p-yes", "YES", arrival=500, ask=1.0),
    ],
)
def test_invalid_input_fails_closed(bad):
    with pytest.raises(ValueError):
        replay([bad])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision_ms", -1),
        ("arrival_ms", 500.5),
        ("settlement_ms", True),
    ],
)
def test_malformed_event_clocks_fail_closed(field, value):
    valid = leg("p-yes", "YES", arrival=500, ask=0.5)
    bad = LegIntent(**{**valid.__dict__, field: value})
    with pytest.raises(ValueError, match="non-negative integer"):
        replay([bad])


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_nonboolean_settlement_outcome_fails_closed(value):
    valid = leg("p-yes", "YES", arrival=500, ask=0.5)
    bad = LegIntent(**{**valid.__dict__, "outcome_yes": value})
    with pytest.raises(ValueError, match="must be boolean"):
        replay([bad])


def test_duplicate_order_ids_fail_closed():
    order = leg("p-yes", "YES", arrival=500, ask=0.5)
    with pytest.raises(ValueError, match="unique"):
        replay([order, order])


def test_pair_id_spanning_markets_fails_closed():
    yes = leg("p-yes", "YES", arrival=500, ask=0.5)
    no = LegIntent(
        **{
            **yes.__dict__,
            "order_id": "p-no",
            "market_id": "m2",
            "side": "NO",
            "arrival_ms": 600,
        }
    )
    with pytest.raises(ValueError, match="span markets"):
        replay([yes, no])


def test_duplicate_pair_side_fails_closed():
    first = leg("p-first", "YES", arrival=500, ask=0.5)
    second = leg("p-second", "YES", arrival=600, ask=0.5)
    with pytest.raises(ValueError, match="one order per side"):
        replay([first, second])


def test_unequal_two_leg_fills_report_residual_inventory():
    result = replay(
        [
            leg("p-yes", "YES", arrival=500, ask=0.45),
            leg("p-no", "NO", arrival=600, ask=0.45, depth=2),
        ]
    )
    assert result["counts"]["pairs_with_both_sides_filled"] == 1
    assert result["counts"]["pairs_with_residual_inventory"] == 1
    assert result["risk"]["total_unhedged_ms"] == 500


def test_order_at_own_settlement_fails_closed():
    bad = leg("p-yes", "YES", arrival=500, ask=0.5, settlement=500)
    with pytest.raises(ValueError, match="must follow"):
        replay([bad])


def test_ledger_reconciles_cash_path():
    result = replay([leg("p-yes", "YES", arrival=500, ask=0.4)])
    rows = result["ledger"]
    assert rows[0]["cash_before"] == 1000
    assert rows[0]["cash_after"] == pytest.approx(1000 - rows[0]["cash_debit"])
    assert rows[1]["event"] == "settlement"
    assert rows[1]["cash_before"] == rows[0]["cash_after"]
    assert rows[1]["cash_after"] == result["cash"]["final"]
