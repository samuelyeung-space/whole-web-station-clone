"""Label-free historical signals for the immutable, A-only MIX fitted models.

This is a capacity re-examination of already opened historical B/C, not a new
independent OOS experiment. The feature equations and float32 storage are those
of ``mix_hf.day_rows``. Unlike that function this module neither reads outcomes
nor filters decisions using a future entry quote. No fit is performed here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import binary as bo
import jump2s as j2
import mix_hf as M

MODEL_SHA256 = "4f9b8f67b9ee874871c26dc021481b2309b85eaee8bc45132afd76e0be82fea8"
FROZEN_SHA256 = "f18e19dd518159886681e1e360f24ee5ca9a59bc60defed9b2a1fa086f5dc17e"
MANIFEST_SHA256 = "5b1c8dda3d4398b93b262f42a946b5258e4d9d4e975c84542bb5af0dc86254ae"
CANONICAL_A_SHA256 = "b8d735ecc4c9d630b6bcda355af9bd4a9ce3c005a3da08b039f5693c882a7be2"
SIGNAL_COLUMNS = ["day", "market_id", "start_ms", "end_ms", "t_ms", "side",
                  "decision_kind", "rule", "policy", "score", "cut", "asklimit",
                  "decision_ask_size", "signal_status", "missing_features"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_identity():
    """Check pinned repository bytes without unpickling any object."""
    root = M.HERE / "real"
    expected = {"cross-mix-models.joblib": MODEL_SHA256,
                "cross-mix-frozen.json": FROZEN_SHA256,
                "cross-mix-models.json": MANIFEST_SHA256}
    for name, digest in expected.items():
        if sha256(root / name) != digest:
            raise ValueError("historical100 trusted model identity differs: " + name)
    manifest = json.loads((root / "cross-mix-models.json").read_text())
    if manifest["canonical_a_fingerprint"] != CANONICAL_A_SHA256:
        raise ValueError("historical100 model canonical A fingerprint differs")
    return {"file_sha256": expected, "canonical_a_fingerprint": CANONICAL_A_SHA256,
            "training_rows": manifest["training_rows"], "versions": manifest["versions"],
            "rules": manifest["rules"], "training_performed": False}


def load_frozen_scorer():
    """Only load the pinned repo model, then apply its full A-only identity gate.

    The existing loader additionally verifies anchor, migration approval,
    provenance, manifest, serialized payload and exact numerical runtime.
    """
    model_identity()
    from mix_forward import load_scorer
    return load_scorer(M.HERE / "real" / "cross-mix-frozen.json")


def day_features(feat, mkts, binance, carry=None, factor=None, dvol=None,
                 closes=None, day=""):
    """All jump/fixed side rows using decision-time inputs, including no-fill rows.

    Inputs have the existing cross.read_day/market_table/read_binance schemas.
    Market metadata must have market_id,horizon,start,end; up_won is ignored.
    No Polymarket print or future label is accepted. Auxiliary inputs retain the
    frozen study's availability conventions; missing values remain NaN.
    """
    info = {"day": day, "markets": 0, "decision_points": 0, "candidate_rows": 0,
            "jump_points": 0, "fixed_points": 0, "future_fill_filters": 0,
            "outcome_fields_read": 0, "feature_missing_counts": {},
            "no_decision_limit": 0, "training_performed": False}
    cols = ["day", "market", "start", "end", "t", "kind", "side"] + M.FEATURES
    if mkts is None or mkts.empty:
        return pd.DataFrame(columns=cols + ["decision_limit", "decision_ask_size"]), info
    required = {"market_id", "horizon", "start", "end"}
    if not required.issubset(mkts):
        raise ValueError("missing market schema: " + ",".join(sorted(required - set(mkts))))
    m5 = mkts.loc[mkts["horizon"] == 5, list(required)].copy()
    m5["market_id"] = m5["market_id"].astype(str)
    m5 = m5.drop_duplicates("market_id", keep="last")
    win = pd.DataFrame({"market": m5["market_id"], "start": m5["start"] / 1000.0,
                        "end": m5["end"] / 1000.0})
    info["markets"] = len(win)
    if binance is None:
        binance = pd.DataFrame(columns=["trade_ts_ms", "recv_ts_ms", "price"])
    own = pd.to_numeric(binance["trade_ts_ms"], errors="coerce")
    own_from = float(own.min()) / 1000.0 if own.notna().any() else -np.inf
    both = binance if carry is None or carry.empty else pd.concat([carry, binance], ignore_index=True)
    spot = M.Spot(both)
    p = M.plan(spot, win, own_from)
    p = p[p["kind"].isin(("jump", "fixed"))].reset_index(drop=True)
    info.update(decision_points=len(p), jump_points=int((p["kind"] == "jump").sum()),
                fixed_points=int((p["kind"] == "fixed").sum()))
    if p.empty:
        return pd.DataFrame(columns=cols + ["decision_limit", "decision_ask_size"]), info

    i, t = p["i"].to_numpy(np.int64), p["t"].to_numpy(float)
    d = spot.at(spot.d, i)
    sigma = spot.sp.sigma(t)
    sec = np.floor(t).astype(np.int64) - 1
    rv30 = spot.sp._at(spot.rv30, sec) if len(spot.rv30) else np.full(len(p), np.nan)
    lp_now = spot.at(spot.lp, spot.last(t, M.PRICE_MAX_AGE_S))
    lp_ago = spot.at(spot.lp, spot.last(t - M.H_ANCHOR_S, M.PRICE_MAX_AGE_S))
    if closes is not None and len(closes):
        ret4h, vr60 = j2.trend_features(closes, spot.seen_through(t) - M.KLINE_GUARD_S)
    else:
        ret4h, vr60 = np.full(len(p), np.nan), np.full(len(p), np.nan)
    dv = M.dvol_at(dvol, t) / 100.0
    start = p["start"].to_numpy(float)
    w, t_el = j2.settle_twap(start), t - start
    fac = np.where(w > 0, bo.twap_std_factor(t_el, bo.WINDOW_S, np.maximum(w, 1)),
                   np.sqrt(np.maximum(bo.WINDOW_S - t_el, 1.0)))
    idx = np.repeat(np.arange(len(p)), 2)
    s = np.tile([1, -1], len(p))
    n, dd = len(idx), d[idx]
    R = {"day": day, "market": p["market"].to_numpy()[idx],
         "start": np.round(start[idx]).astype(np.int64),
         "end": p["end"].to_numpy(float)[idx], "t": t[idx],
         "kind": p["kind"].to_numpy()[idx], "side": s}
    with np.errstate(invalid="ignore", divide="ignore"):
        R.update(jump_bp=s * dd * 1e4, jump_abs_bp=np.abs(dd) * 1e4,
                 jump_z=np.where(sigma[idx] > 0, s * dd / sigma[idx], np.nan),
                 dir=np.sign(s * dd), is_jump=(R["kind"] == "jump").astype(float),
                 tau=R["end"] - R["t"],
                 trend_agree=np.where(np.isfinite(ret4h[idx]) & np.isfinite(dd),
                                      np.sign(dd) * np.sign(ret4h[idx]), np.nan),
                 trend_side=np.where(np.isfinite(ret4h[idx]), np.sign(s * ret4h[idx]), np.nan),
                 vr60=vr60[idx], dvol_rv=dv[idx] - rv30[idx])
        for c, col in (("f_ridge5", "ridge_5m"), ("f_hgb5", "hgb_5m"),
                       ("f_ridge15", "ridge_15m"), ("f_hgb15", "hgb_15m")):
            v = (factor[col].reindex(np.round(start).astype(np.int64)).to_numpy(float)
                 if factor is not None and len(factor) and col in factor else np.full(len(p), np.nan))
            R[c] = s * (v[idx] - 0.5)
    move, scale = (lp_now - lp_ago)[idx], (fac * sigma)[idx]
    scale = np.where(scale > 0, scale, np.nan)
    bk = M.Books(feat, win["market"])
    F = {c: np.full(n, np.nan) for c in ("h_edge", "dmid2", "dmid10", "imb", "spread", "ask")}
    decision_size = np.full(n, np.nan)
    for m, ix in pd.Series(np.arange(n)).groupby(R["market"], sort=False).indices.items():
        if m not in bk.seg:
            continue
        a, b = bk.seg[m]
        tt, ss = R["t"][ix], s[ix]
        bid, ask, bsz, asz, _ = M._quote(bk, M._asof(bk.T, a, b, tt), ss)
        mid = (bid + ask) / 2
        decision_size[ix] = asz
        with np.errstate(invalid="ignore", divide="ignore"):
            F["spread"][ix], F["imb"][ix], F["ask"][ix] = ask - bid, (bsz - asz) / (bsz + asz), ask
            for lag, c in ((2.0, "dmid2"), (10.0, "dmid10")):
                b0, a0, _, _, _ = M._quote(bk, M._asof(bk.T, a, b, tt - lag), ss)
                F[c][ix] = mid - (b0 + a0) / 2
            ub, ua, _, _, _ = M._quote(bk, M._asof(bk.T, a, b, tt - M.H_ANCHOR_S), np.ones(len(ix)))
            from scipy.stats import norm
            p1 = norm.cdf(norm.ppf(np.clip((ub + ua) / 2, 0.005, 0.995)) + move[ix] / scale[ix])
            F["h_edge"][ix] = np.where(ss > 0, p1, 1 - p1) - ask
    R.update(F)
    R["ask_fee"] = M.fee(F["ask"])
    rows = M.compact(pd.DataFrame(R).drop(columns="end"))
    rows["end"] = R["end"]  # never float32-convert absolute timestamps
    rows["decision_limit"] = F["ask"]  # exact recorded limit, not float32 model input
    rows["decision_ask_size"] = decision_size
    info.update(candidate_rows=len(rows), no_decision_limit=int((~np.isfinite(F["ask"])).sum()),
                feature_missing_counts={c: int((~np.isfinite(rows[c].to_numpy(float))).sum()) for c in M.FEATURES})
    return rows, info


def score_rows(rows, scorer):
    """Score every decision row; only the immutable threshold selects a signal."""
    if tuple(scorer.features) != tuple(M.FEATURES):
        raise ValueError("MIX model feature schema differs")
    out = []
    X = rows.loc[:, M.FEATURES].to_numpy(float)
    missing = [",".join(np.asarray(M.FEATURES)[~np.isfinite(x)]) for x in X]
    for rule in scorer.rules:
        values = scorer.models[str(rule["id"])].predict(X) if len(X) else np.empty(0)
        for k in np.flatnonzero(values >= float(rule["cut"])):
            row = rows.iloc[k]
            limit = float(row["decision_limit"])
            out.append({"day": str(row["day"]), "market_id": str(row["market"]),
                        "start_ms": int(row["start"]) * 1000, "end_ms": float(row["end"]) * 1000,
                        "t_ms": float(row["t"]) * 1000, "side": int(row["side"]),
                        "decision_kind": str(row["kind"]), "rule": str(rule["id"]),
                        "policy": str(rule["policy"]), "score": float(values[k]),
                        "cut": float(rule["cut"]), "asklimit": limit,
                        "decision_ask_size": float(row["decision_ask_size"]),
                        "signal_status": "ready" if np.isfinite(limit) and M.BAND[0] <= limit <= M.BAND[1]
                                         else "invalid_decision_limit",
                        "missing_features": missing[k]})
    return pd.DataFrame(out, columns=SIGNAL_COLUMNS).sort_values(
        ["t_ms", "market_id", "rule", "side"], kind="stable").reset_index(drop=True)


def day_signals(feat, mkts, binance, carry=None, factor=None, dvol=None,
                closes=None, day="", scorer=None):
    rows, info = day_features(feat, mkts, binance, carry, factor, dvol, closes, day)
    scorer = load_frozen_scorer() if scorer is None else scorer
    signals = score_rows(rows, scorer)
    info.update(selected_signals=len(signals), rule_evaluations=len(rows) * len(scorer.rules),
                selected_by_rule=signals.groupby("rule").size().to_dict(),
                selected_status_counts=signals.groupby("signal_status").size().to_dict(),
                historical_policy="all frozen selected rows; not first-per-market",
                execution_filter_before_scoring=False)
    return signals, info
