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


def test_duplicate_order_ids_fail_closed():
    order = leg("p-yes", "YES", arrival=500, ask=0.5)
    with pytest.raises(ValueError, match="unique"):
        replay([order, order])
