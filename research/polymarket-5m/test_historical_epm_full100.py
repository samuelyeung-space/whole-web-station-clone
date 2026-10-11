import historical_epm_full100 as replay


def test_partial_wins_and_losses_both_cancel_without_changing_decisions():
    rows = [dict(requested_shares=100., potential_filled_shares=q,
                 potential_status="filled_partial", fill_price=.5, fees_usd=.1,
                 potential_cost_usd=q*.5+.1, potential_payout_usd=payout,
                 decision_ms=1000, reserved_usd_at_decision=51.75)
            for q, payout in [(99.99,99.99),(50.,0.)]]
    changed = replay.full_or_zero(rows)
    assert [r["potential_filled_shares"] for r in rows] == [99.99,50.]
    assert all(r["potential_filled_shares"] == r["potential_cost_usd"] ==
               r["potential_payout_usd"] == r["fees_usd"] == 0 for r in changed)
    assert all(r["decision_ms"] == 1000 and r["reserved_usd_at_decision"] == 51.75 for r in changed)


def test_full_fill_and_prior_no_fill_preserved():
    rows = [dict(requested_shares=100.,potential_filled_shares=q,
                 potential_status=status,potential_cost_usd=cost)
            for q,status,cost in [(100.,"filled_full",51.75),(0.,"no_fill",0.)]]
    changed = replay.full_or_zero(rows)
    for old,new in zip(rows, changed):
        assert all(new[key] == value for key,value in old.items())


def test_failed_full100_keeps_reservation_until_match_then_releases():
    base = dict(requested_shares=100.,potential_filled_shares=50.,
                potential_status="filled_partial",fill_price=.5,fees_usd=.875,
                potential_cost_usd=25.875,potential_payout_usd=50.,
                candidate_id=replay.base.CANDIDATE_ID,latency_ms=500,
                settlement_ms=10000,reserved_usd_at_decision=901.)
    rows = [dict(base,market_id="a",decision_ms=1000,match_ms=1500),
            dict(base,market_id="b",decision_ms=1100,match_ms=1600),
            dict(base,market_id="c",decision_ms=1700,match_ms=2200,
                 potential_status="filled_full",potential_filled_shares=100.,
                 fees_usd=1.75,potential_cost_usd=51.75,potential_payout_usd=100.)]
    metrics,attempts,_ = replay.base.cash_replay(replay.full_or_zero(rows))
    assert metrics["submitted_orders"] == 2
    assert attempts[1]["status"] == "rejected_insufficient_cash"
    assert attempts[0]["cost_usd"] == 0
    assert metrics["net_pnl_usd"] == 48.25
    assert metrics["ending_cash_usd"] == 1048.25
