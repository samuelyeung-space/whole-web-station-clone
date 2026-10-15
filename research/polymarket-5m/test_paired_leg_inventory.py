import math

import pytest

from paired_leg_inventory import LegIntent, ReplayConfig, replay, taker_fee


def leg(
    order_id,
    side,
    *,
    arrival,
    ask,
    depth=5,
    settlement=1000,
    cash_release=None,
    outcome=True,
):
    if cash_release is None:
        cash_release = settlement
    return LegIntent(
        order_id=order_id,
        pair_id=order_id.split("-")[0],
        market_id="m1",
        side=side,
        decision_ms=0,
        arrival_ms=arrival,
        settlement_ms=settlement,
        cash_release_ms=cash_release,
        outcome_yes=outcome,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=ask,
        displayed_shares=depth,
    )


def test_two_filled_legs_include_exact_fees_and_settlement():
    result = replay([leg("p-yes", "YES", arrival=500, ask=0.45), leg("p-no", "NO", arrival=600, ask=0.45)])
    expected = 5 - 5 * 0.9 - 2 * taker_fee(5, 0.45, 0.07)
    assert result["schema_version"] == 6
    assert result["counts"]["pairs_with_both_sides_filled"] == 1
    assert result["counts"]["pairs_with_single_side_filled"] == 0
    assert result["risk"]["total_unhedged_ms"] == 100
    assert result["risk"]["max_unhedged_episode_ms"] == 100
    assert result["risk"]["total_pair_unhedged_ms"] == 100
    assert result["risk"]["max_pair_unhedged_episode_ms"] == 100
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
    first = leg(
        "a-yes",
        "YES",
        arrival=100,
        ask=0.5,
        settlement=400,
        cash_release=500,
        outcome=True,
    )
    second = LegIntent(
        order_id="b-yes",
        pair_id="b",
        market_id="m2",
        side="YES",
        decision_ms=499,
        arrival_ms=500,
        settlement_ms=1000,
        cash_release_ms=1000,
        outcome_yes=True,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=0.5,
        displayed_shares=5,
    )
    result = replay([first, second], ReplayConfig(initial_cash=3.0))
    assert result["counts"]["full_fills"] == 2
    assert result["counts"]["cash_rejects"] == 0
    assert result["risk"]["max_cash_release_delay_ms"] == 100


def test_cash_cannot_be_reused_before_spendable_timestamp():
    first = leg(
        "a-yes",
        "YES",
        arrival=100,
        ask=0.5,
        settlement=400,
        cash_release=600,
        outcome=True,
    )
    second = LegIntent(
        order_id="b-yes",
        pair_id="b",
        market_id="m2",
        side="YES",
        decision_ms=499,
        arrival_ms=500,
        settlement_ms=1000,
        cash_release_ms=1000,
        outcome_yes=True,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=0.5,
        displayed_shares=5,
    )
    result = replay([first, second], ReplayConfig(initial_cash=3.0))
    assert result["counts"]["full_fills"] == 1
    assert result["counts"]["cash_rejects"] == 1
    assert result["ledger"][2]["event"] == "order_arrival"
    assert result["ledger"][2]["reason"] == "insufficient_shared_cash"
    assert result["ledger"][3]["event"] == "cash_release"


def test_unhedged_risk_ends_at_settlement_not_cash_release():
    result = replay(
        [
            leg(
                "p-yes",
                "YES",
                arrival=100,
                ask=0.5,
                settlement=500,
                cash_release=900,
            )
        ]
    )
    assert result["risk"]["total_unhedged_ms"] == 400
    assert result["risk"]["total_pair_unhedged_ms"] == 400
    assert result["risk"]["max_cash_release_delay_ms"] == 400


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
        ("cash_release_ms", 1000.5),
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


def test_cash_release_before_settlement_fails_closed():
    bad = leg(
        "p-yes",
        "YES",
        arrival=500,
        ask=0.5,
        settlement=1000,
        cash_release=999,
    )
    with pytest.raises(ValueError, match="must not precede"):
        replay([bad])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_shares", True),
        ("requested_shares", "5"),
        ("fixed_limit", "0.6"),
        ("arrival_ask", None),
        ("displayed_shares", False),
    ],
)
def test_non_numeric_execution_fields_fail_closed(field, value):
    valid = leg("p-yes", "YES", arrival=500, ask=0.5)
    bad = LegIntent(**{**valid.__dict__, field: value})
    with pytest.raises(ValueError, match="finite JSON number"):
        replay([bad])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_cash", "1000"),
        ("depth_haircut", True),
        ("fee_rate", "0.07"),
        ("extra_cost_per_share", None),
    ],
)
def test_non_numeric_config_fields_fail_closed(field, value):
    config = ReplayConfig(**{**ReplayConfig().__dict__, field: value})
    with pytest.raises(ValueError, match="finite JSON number"):
        replay([], config)


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_nonboolean_full_or_zero_fails_closed(value):
    config = ReplayConfig(full_or_zero=value)
    with pytest.raises(ValueError, match="must be boolean"):
        replay([], config)


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_nonboolean_reserve_cash_at_send_fails_closed(value):
    config = ReplayConfig(reserve_cash_at_send=value)
    with pytest.raises(ValueError, match="must be boolean"):
        replay([], config)


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_nonboolean_consume_snapshot_depth_fails_closed(value):
    config = ReplayConfig(consume_snapshot_depth=value)
    with pytest.raises(ValueError, match="must be boolean"):
        replay([], config)


def test_snapshot_depth_pool_prevents_concurrent_double_use():
    first = LegIntent(
        **{
            **leg("a-yes", "YES", arrival=500, ask=0.4).__dict__,
            "snapshot_id": "book-7",
        }
    )
    second = LegIntent(
        **{
            **leg("b-yes", "YES", arrival=500, ask=0.4).__dict__,
            "snapshot_id": "book-7",
        }
    )
    result = replay(
        [first, second], ReplayConfig(consume_snapshot_depth=True)
    )
    assert result["counts"]["full_fills"] == 1
    assert result["counts"]["zero_fills"] == 1
    assert result["counts"]["snapshot_depth_rejects"] == 1
    assert result["snapshot_depth"]["initial_shares"] == 5
    assert result["snapshot_depth"]["consumed_shares"] == 5
    assert result["snapshot_depth"]["remaining_shares"] == 0
    arrivals = [row for row in result["ledger"] if row["event"] == "order_arrival"]
    assert arrivals[0]["snapshot_depth_before"] == 5
    assert arrivals[0]["snapshot_depth_after"] == 0
    assert arrivals[1]["reason"] == "shared_snapshot_depth_exhausted"


def test_new_snapshot_can_represent_replenished_depth():
    first = LegIntent(
        **{
            **leg("a-yes", "YES", arrival=500, ask=0.4).__dict__,
            "snapshot_id": "book-7",
        }
    )
    second = LegIntent(
        **{
            **leg("b-yes", "YES", arrival=600, ask=0.4).__dict__,
            "snapshot_id": "book-8",
        }
    )
    result = replay(
        [first, second], ReplayConfig(consume_snapshot_depth=True)
    )
    assert result["counts"]["full_fills"] == 2
    assert result["snapshot_depth"]["pool_count"] == 2
    assert result["snapshot_depth"]["consumed_shares"] == 10


def test_snapshot_depth_mode_requires_exact_consistent_identity():
    missing = leg("p-yes", "YES", arrival=500, ask=0.4)
    with pytest.raises(ValueError, match="snapshot_id is required"):
        replay([missing], ReplayConfig(consume_snapshot_depth=True))

    first = LegIntent(**{**missing.__dict__, "snapshot_id": "book-7"})
    second = LegIntent(
        **{
            **leg("q-yes", "YES", arrival=600, ask=0.41).__dict__,
            "snapshot_id": "book-7",
        }
    )
    with pytest.raises(ValueError, match="inconsistent quote metadata"):
        replay([first, second], ReplayConfig(consume_snapshot_depth=True))


def test_send_reservation_prevents_inflight_cash_reuse():
    first = LegIntent(
        **{**leg("a-yes", "YES", arrival=500, ask=0.5).__dict__, "send_ms": 100}
    )
    second = LegIntent(
        **{
            **leg("b-yes", "YES", arrival=600, ask=0.5).__dict__,
            "send_ms": 200,
        }
    )
    result = replay(
        [first, second],
        ReplayConfig(initial_cash=5.0, reserve_cash_at_send=True),
    )
    assert result["counts"]["fills"] == 1
    assert result["counts"]["cash_rejects"] == 1
    assert result["counts"]["send_cash_rejects"] == 1
    assert result["counts"]["arrival_cash_rejects"] == 0
    assert result["cash"]["max_reserved"] == pytest.approx(3.0875)
    assert result["cash"]["minimum_available"] == pytest.approx(1.9125)
    assert result["ledger"][0]["event"] == "order_send"
    assert result["ledger"][1]["reason"] == "insufficient_shared_cash_at_send"
    assert result["ledger"][3]["reason"] == "insufficient_shared_cash_at_send"


def test_cash_release_precedes_same_timestamp_send_reservation():
    first = LegIntent(
        **{
            **leg(
                "a-yes",
                "YES",
                arrival=100,
                ask=0.5,
                settlement=400,
                cash_release=500,
            ).__dict__,
            "send_ms": 50,
        }
    )
    second = LegIntent(
        order_id="b-yes",
        pair_id="b",
        market_id="m2",
        side="YES",
        decision_ms=499,
        arrival_ms=600,
        settlement_ms=1000,
        cash_release_ms=1000,
        outcome_yes=True,
        requested_shares=5,
        fixed_limit=0.6,
        arrival_ask=0.5,
        displayed_shares=5,
        send_ms=500,
    )
    result = replay(
        [first, second],
        ReplayConfig(initial_cash=3.2, reserve_cash_at_send=True),
    )
    assert result["counts"]["full_fills"] == 2
    assert result["counts"]["send_cash_rejects"] == 0
    at_500 = [row["event"] for row in result["ledger"] if row["timestamp_ms"] == 500]
    assert at_500 == ["cash_release", "order_send"]


@pytest.mark.parametrize(
    ("send_ms", "message"),
    [
        (None, "required"),
        (-1, "non-negative integer"),
        (True, "non-negative integer"),
        (501, "arrival_ms precedes send_ms"),
    ],
)
def test_send_reservation_requires_valid_causal_clock(send_ms, message):
    order = LegIntent(
        **{**leg("p-yes", "YES", arrival=500, ask=0.5).__dict__, "send_ms": send_ms}
    )
    with pytest.raises(ValueError, match=message):
        replay([order], ReplayConfig(reserve_cash_at_send=True))


def test_worst_case_fee_reservation_covers_lower_price_fill():
    order = LegIntent(
        **{
            **leg("p-yes", "YES", arrival=500, ask=0.5).__dict__,
            "fixed_limit": 0.9,
            "send_ms": 100,
        }
    )
    result = replay([order], ReplayConfig(reserve_cash_at_send=True))
    send = result["ledger"][0]
    arrival = result["ledger"][1]
    assert arrival["cash_debit"] <= send["reservation"]
    assert result["cash"]["reserved_final"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("order_id", 1), ("pair_id", True), ("market_id", None)],
)
def test_non_string_identifiers_fail_closed(field, value):
    valid = leg("p-yes", "YES", arrival=500, ask=0.5)
    bad = LegIntent(**{**valid.__dict__, field: value})
    with pytest.raises(ValueError, match="non-empty string"):
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


def test_opposite_single_legs_in_distinct_pairs_do_not_hide_pair_risk():
    yes = leg("a-yes", "YES", arrival=100, ask=0.45)
    no = leg("b-no", "NO", arrival=100, ask=0.45)
    result = replay([yes, no])
    assert result["risk"]["total_unhedged_ms"] == 0
    assert result["counts"]["unhedged_market_episodes"] == 1
    assert result["risk"]["total_pair_unhedged_ms"] == 1800
    assert result["risk"]["max_pair_unhedged_episode_ms"] == 900
    assert result["risk"]["max_pair_unhedged_shares"] == 5
    assert result["counts"]["unhedged_pair_episodes"] == 2


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
    assert rows[1]["cash_after"] == rows[0]["cash_after"]
    assert rows[2]["event"] == "cash_release"
    assert rows[2]["cash_before"] == rows[1]["cash_after"]
    assert rows[2]["cash_after"] == result["cash"]["final"]

