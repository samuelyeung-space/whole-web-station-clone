import pandas as pd
import pytest

import historical_epm_chrono_sensitivity as h


def test_fee_rounding_matches_replay_contract():
    assert h.rounded_fee(100, .5) == pytest.approx(1.75)
    assert h.reserved_fee(100, .333333) >= h.rounded_fee(100, .333333)


def test_grid_uses_last_received_not_future_quote():
    tape = pd.DataFrame([
        dict(market_id="m", recv_ms=100_900, source_ms=100_800, start_ms=100_000,
             end_ms=400_000, best_bid=.49, best_ask=.50, bid_size=5., ask_size=6.),
        dict(market_id="m", recv_ms=101_900, source_ms=101_800, start_ms=100_000,
             end_ms=400_000, best_bid=.59, best_ask=.60, bid_size=7., ask_size=8.),
    ])
    grid = h.decision_grid(tape)
    assert grid[["decision_ms", "best_ask"]].to_dict("records") == [
        {"decision_ms": 101_000, "best_ask": .50},
        {"decision_ms": 102_000, "best_ask": .60},
    ]


def test_cash_replay_rejects_only_when_shared_cash_is_insufficient():
    base = dict(candidate_id=h.CANDIDATE_ID, latency_ms=500, side="Up", p_up=.8,
                edge=.1, limit_price=.9, decision_book_recv_ms=900,
                match_book_recv_ms=1400, requested_shares=100., potential_status="filled_full",
                potential_filled_shares=100., fill_price=.9, fees_usd=1.,
                potential_cost_usd=91., potential_payout_usd=100., winner="Up",
                settlement_ms=10_000, reserved_usd_at_decision=901.)
    rows = [dict(base, market_id="a", decision_ms=1000, match_ms=1500),
            dict(base, market_id="b", decision_ms=1100, match_ms=1600)]
    metrics, attempts, _ = h.cash_replay(rows)
    assert metrics["submitted_orders"] == 1
    assert attempts[1]["status"] == "rejected_insufficient_cash"
    assert metrics["net_pnl_usd"] == pytest.approx(9.)
