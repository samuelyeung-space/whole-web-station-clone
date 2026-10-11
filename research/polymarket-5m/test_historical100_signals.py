"""Causality/identity checks, not market backtests or profitability evidence."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import historical100_signals as H
import mix_hf as M
import test_mix_hf as T


class ConstantModel:
    def predict(self, x):
        return np.ones(len(x))


def scorer():
    return SimpleNamespace(features=tuple(M.FEATURES),
                           rules=({"id": "R1", "policy": "settle", "cut": .1},),
                           models={"R1": ConstantModel()})


def inputs():
    return T.book(), T.markets(("m1", T.S, 1.0)), T.binance()


def test_old_eligible_features_exactly_equal_with_all_auxiliary_inputs():
    f, m, b = inputs()
    factor = pd.DataFrame({c: [.51 + i * .01] for i, c in enumerate(
        ["ridge_5m", "hgb_5m", "ridge_15m", "hgb_15m"])}, index=[T.S])
    dvol = pd.DataFrame({"hour": [T.S - 3600], "dvol": [43.]})
    times = np.arange(T.S - 400 * 60, T.S + 600, 60)
    closes = pd.Series(60000.0 * np.exp(np.sin(np.arange(len(times))) * .002), index=times)
    new, _ = H.day_features(f, m, b, factor=factor, dvol=dvol, closes=closes)
    old, _ = M.day_rows(f, m, b, T.prints([]), factor=factor, dvol=dvol, closes=closes)
    keys = ["market", "t", "kind", "side"]
    old = old[old.kind != "random"].set_index(keys)
    new = new.set_index(keys).loc[old.index]
    np.testing.assert_array_equal(new[M.FEATURES].to_numpy(float), old[M.FEATURES].to_numpy(float))


def test_future_unfillable_does_not_erase_decision():
    f, m, b = inputs()
    f.loc[f.timestamp_ms > T.T0 * 1000, "up_ask_size"] = 0.0
    old, _ = M.day_rows(f, m, b, T.prints([]))
    new, audit = H.day_features(f, m, b)
    key = (new.kind == "jump") & (new.side == 1) & np.isclose(new.t, T.T0, rtol=0, atol=1e-6)
    assert key.sum() == 1
    old_key = (old.kind == "jump") & (old.side == 1) & np.isclose(old.t, T.T0, rtol=0, atol=1e-6)
    assert old_key.sum() == 0
    assert audit["future_fill_filters"] == 0


def test_future_quotes_outcomes_and_trades_cannot_change_prior_features():
    f, m, b = inputs()
    original, _ = H.day_features(f, m, b)
    cutoff = T.S + 130
    f.loc[f.timestamp_ms > cutoff * 1000, ["up_best_bid", "up_best_ask"]] = [.1, .2]
    b.loc[b.recv_ts_ms > cutoff * 1000, "price"] *= 2
    m["up_won"] = 0.0
    edited, _ = H.day_features(f, m, b)
    cols = ["market", "t", "kind", "side"] + M.FEATURES
    a = original[original.t <= cutoff][cols].reset_index(drop=True)
    z = edited[edited.t <= cutoff][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, z)


def test_missing_outcome_still_generates_all_fixed_decisions():
    f, m, b = inputs()
    with_labels, _ = H.day_features(f, m, b)
    without, audit = H.day_features(f, m.drop(columns="up_won"), b)
    pd.testing.assert_frame_equal(with_labels, without)
    assert audit["outcome_fields_read"] == 0


def test_selected_invalid_limit_is_retained_for_execution_rejection():
    f, m, b = inputs()
    f.loc[:, ["up_best_bid", "up_best_ask"]] = [.9, .1]  # crossed now
    signals, audit = H.day_signals(f, m, b, scorer=scorer())
    assert len(signals) == audit["candidate_rows"]
    assert (signals[signals.side == 1].signal_status == "invalid_decision_limit").all()
    assert not any(c.startswith("pnl_") or c == "won" for c in signals.columns)


def test_no_binance_preserves_fixed_points_with_missing_features():
    f, m, b = inputs()
    rows, audit = H.day_features(f, m, b.iloc[:0])
    assert audit["fixed_points"] == 4 and len(rows) == 8
    assert rows.jump_bp.isna().all()


def test_trusted_model_files_pinned_without_loading_pickle():
    identity = H.model_identity()
    assert identity["training_rows"] == 162591
    assert identity["training_performed"] is False
