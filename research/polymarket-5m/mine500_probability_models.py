"""Small, fixed market-prior residual models on consumed development dates.

The four specifications and two cost margins are eight new research trials,
not eight independent mechanisms. This module does not select a winner, tune
hyperparameters, infer settlement timestamps, or label reused data OOS. A fit
for UTC day D sees only earlier-day markets whose official outcome was actually
received before D. Each market has total training and diagnostic weight one.

Ordinary sklearn LogisticRegression has no fixed-offset argument. The objective
here is explicitly logit(p)=logit(received Up midpoint)+X beta, with fixed L2,
optimized by scipy. All scaling is fixed ex ante; no full-sample scaler is fit.
Public entry points: train_forward_days, attach_scores, make_candidates, signal.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


VERSION = "market-prior-residual-v1"
MODEL_IDS = ("market_raw", "market_calibration", "external_path", "full_residual")
MARGINS = (0.02, 0.04)
L2 = 0.10
MIN_TRAIN_MARKETS = 20
MIN_CLASS_MARKETS = 5
OFFICIAL_SOURCES = frozenset(("gamma", "polymarket", "chainlink"))
BASE = ("mid_up", "tau_s")
PATH = ("r_5s", "r_15s", "r_30s", "r_60s", "rv_15s", "rv_60s")
FULL = ("pm_r_5s", "pm_r_15s", "imbalance_up")
REQUIRED = {
    "market_raw": BASE, "market_calibration": BASE,
    "external_path": BASE + PATH, "full_residual": BASE + PATH + FULL,
}


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def _start(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()


def _logit(p):
    return math.log(p / (1 - p))


def _clip(x, limit=5.0):
    return min(limit, max(-limit, x))


def feature_vector(features: Mapping[str, float], model_id: str) -> np.ndarray:
    """Deterministic scales, no imputations, settlement strike or future features."""
    for name in REQUIRED[model_id]:
        if not _finite(features.get(name)):
            raise ValueError("missing_or_invalid_feature:" + name)
    p, tau = features["mid_up"], features["tau_s"]
    if not 0 < p < 1 or not 0 < tau <= 300:
        raise ValueError("invalid_probability_or_remaining_time")
    if model_id == "market_raw":
        return np.empty(0)
    logit = _clip(_logit(p), 6.0) / 3
    tail, middle = float(tau <= 60), float(60 < tau <= 120)
    result = [1.0, logit, tail, middle, logit * tail, logit * middle]
    if model_id in ("external_path", "full_residual"):
        if features["rv_15s"] < 0 or features["rv_60s"] < 0:
            raise ValueError("negative_realized_volatility")
        short_vol, long_vol = max(.25, features["rv_15s"]), max(.25, features["rv_60s"])
        r5, r15, r30, r60 = (features[f"r_{s}s"] for s in (5, 15, 30, 60))
        short, long = _clip(r15 / short_vol), _clip(r60 / long_vol)
        acceleration = _clip((r5 - (r15 - r5) / 2) / short_vol)
        expansion = _clip(math.log((short_vol * 2) / long_vol), 3)
        result += [_clip(r5 / short_vol), short, _clip(r30 / long_vol), long,
                   acceleration, expansion, short * tail, long * tail]
    if model_id == "full_residual":
        if not -1 <= features["imbalance_up"] <= 1:
            raise ValueError("invalid_book_imbalance")
        pm5, pm15 = _clip(features["pm_r_5s"] / .05), _clip(features["pm_r_15s"] / .10)
        result += [pm5, pm15, features["imbalance_up"], pm5 * tail,
                   _clip(short - pm15), _clip(short * pm15)]
    return np.asarray(result, dtype=float)


def _causal_vector(row, model_id):
    features, available, now = row.get("features", {}), row.get("feature_available_ms", {}), row["recv_ms"]
    for name in REQUIRED[model_id]:
        if not _finite(available.get(name)):
            raise ValueError("missing_feature_availability:" + name)
        if available[name] > now:
            raise ValueError("future_feature:" + name)
    return feature_vector(features, model_id)


@dataclass(frozen=True)
class FittedResidual:
    model_id: str
    beta: tuple[float, ...]
    fit_cutoff_ms: int
    training_market_ids: tuple[str, ...]
    training_hash: str
    objective: float | None
    gradient_max_abs: float | None

    def predict(self, row):
        if row["recv_ms"] < self.fit_cutoff_ms:
            raise ValueError("prediction_precedes_fit_cutoff")
        if str(row["market_id"]) in self.training_market_ids:
            raise ValueError("training_market_cannot_be_scored_forward")
        x = _causal_vector(row, self.model_id)
        return float(expit(_logit(row["features"]["mid_up"]) + np.dot(x, self.beta)))

    def as_dict(self):
        return {"model_id": self.model_id, "beta": list(self.beta),
                "fit_cutoff_ms": self.fit_cutoff_ms,
                "training_market_ids": list(self.training_market_ids),
                "training_hash": self.training_hash, "objective": self.objective,
                "gradient_max_abs": self.gradient_max_abs, "fixed_l2": L2,
                "model_hash": _hash([self.model_id, self.beta, self.fit_cutoff_ms,
                                      self.training_market_ids, self.training_hash, L2])}


def fit_residual(rows, model_id, cutoff_ms):
    """Fit supplied eligible rows. Internal checks prevent unsafe direct use."""
    if model_id not in MODEL_IDS:
        raise ValueError("unknown_model")
    if model_id == "market_raw":
        return FittedResidual(model_id, (), cutoff_ms, (), _hash([]), None, None)
    if not rows:
        raise ValueError("insufficient_training_markets")
    groups, labels = Counter(), {}
    xs, ys, offsets = [], [], []
    for row in rows:
        mid = str(row["market_id"])
        if (_day(row["recv_ms"]) >= _day(cutoff_ms)
                or row["settlement_known_ms"] >= cutoff_ms
                or row["market_end_ms"] >= cutoff_ms
                or row["settlement_known_ms"] < row["market_end_ms"]
                or row["recv_ms"] >= row["market_end_ms"]):
            raise ValueError("training_label_or_market_not_available_before_cutoff")
        y = row["target_up"]
        if y not in (0, 1) or isinstance(y, bool):
            raise ValueError("invalid_label")
        if mid in labels and labels[mid] != y:
            raise ValueError("conflicting_labels")
        labels[mid] = y
        groups[mid] += 1
        xs.append(_causal_vector(row, model_id))
        ys.append(y)
        offsets.append(_logit(row["features"]["mid_up"]))
    if len(groups) < MIN_TRAIN_MARKETS:
        raise ValueError("insufficient_training_markets")
    counts = Counter(labels.values())
    if min(counts.get(0, 0), counts.get(1, 0)) < MIN_CLASS_MARKETS:
        raise ValueError("insufficient_training_class_markets")
    x, y, offset = np.asarray(xs), np.asarray(ys), np.asarray(offsets)
    # Every market has weight 1 / number_of_markets, regardless of row density.
    w = np.asarray([1.0 / (len(groups) * groups[str(r["market_id"])]) for r in rows])

    def objective(beta):
        z = offset + x @ beta
        loss = float(np.dot(w, np.logaddexp(0, z) - y * z) + L2 * np.dot(beta, beta) / 2)
        grad = x.T @ (w * (expit(z) - y)) + L2 * beta
        return loss, grad

    result = minimize(objective, np.zeros(x.shape[1]), method="L-BFGS-B", jac=True,
                      options={"maxiter": 500, "gtol": 1e-8, "ftol": 1e-12})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError("optimizer_not_converged:" + str(result.message))
    training_hash = _hash([{k: r[k] for k in ("market_id", "recv_ms", "features",
                  "feature_available_ms", "target_up", "settlement_known_ms", "market_end_ms")}
                  for r in rows])
    return FittedResidual(model_id, tuple(float(v) for v in result.x), cutoff_ms,
                          tuple(sorted(groups)), training_hash, float(result.fun),
                          float(np.max(np.abs(result.jac))))


def _collect(events, consumed_days):
    """Preserve labels as label-only metadata. Neither prediction nor selection reads y."""
    days = set(consumed_days)
    if any(_day(_start(day)) != day for day in days):
        raise ValueError("consumed_days_must_be_iso_utc_dates")
    markets, outcomes, decisions = {}, {}, {}
    counts = Counter()
    for event in events:
        kind = event.get("type")
        if kind == "dataset":
            if event.get("split") != "development":
                raise ValueError("only_consumed_development_input_allowed")
            counts["dataset_records"] += 1
        if kind == "market":
            mid = str(event["market_id"])
            prior = markets.get(mid)
            if prior and any(prior.get(k) != event.get(k) for k in ("start_ms", "end_ms", "label_source")):
                raise ValueError("conflicting_market_metadata")
            if prior is None or event["recv_ms"] < prior["recv_ms"]:
                markets[mid] = event
        elif kind == "decision":
            if _day(event["recv_ms"]) not in days:
                raise ValueError("unconsumed_decision_date")
            key = (str(event["market_id"]), event["recv_ms"])
            if key in decisions and _hash(decisions[key]) != _hash(event):
                raise ValueError("conflicting_duplicate_decision")
            counts["duplicate_decision_rows"] += int(key in decisions)
            decisions[key] = event
        elif kind == "outcome":
            mid = str(event["market_id"])
            prior = outcomes.get(mid)
            if prior and prior.get("winner") != event.get("winner"):
                raise ValueError("conflicting_labels")
            if prior is None or event["recv_ms"] < prior["recv_ms"]:
                outcomes[mid] = event
    if not counts["dataset_records"]:
        raise ValueError("missing_development_dataset_provenance")
    rows = []
    for (mid, now), event in sorted(decisions.items(), key=lambda x: (x[0][1], x[0][0])):
        market, outcome = markets.get(mid), outcomes.get(mid)
        if (not market or market.get("label_source") not in OFFICIAL_SOURCES
                or market["recv_ms"] > now or not market["start_ms"] <= now < market["end_ms"]
                or market["end_ms"] - market["start_ms"] != 300000):
            counts["invalid_market_decisions"] += 1
            continue
        row = dict(event, market_end_ms=market["end_ms"], target_up=None, settlement_known_ms=None)
        if (outcome and outcome.get("source") == market["label_source"]
                and outcome.get("winner") in ("Up", "Down")
                and _finite(outcome.get("resolved_ms")) and _finite(outcome.get("recv_ms"))
                and outcome["resolved_ms"] >= market["end_ms"]
                and outcome["recv_ms"] >= outcome["resolved_ms"]):
            row.update(target_up=int(outcome["winner"] == "Up"), settlement_known_ms=outcome["recv_ms"])
        elif outcome:
            counts["invalid_outcome_decisions"] += 1
        rows.append(row)
    return rows, dict(counts)


def _prediction_metrics(rows, predictions):
    groups = defaultdict(list)
    for row, p in zip(rows, predictions):
        if row["target_up"] is not None:
            y, q = row["target_up"], row["features"]["mid_up"]
            p = min(1 - 1e-12, max(1e-12, p))
            q = min(1 - 1e-12, max(1e-12, q))
            groups[str(row["market_id"])].append(((p-y)**2, (q-y)**2,
                -y*math.log(p)-(1-y)*math.log1p(-p), -y*math.log(q)-(1-y)*math.log1p(-q)))
    if not groups:
        return {"labeled_markets": 0, "brier": None, "mid_brier": None,
                "logloss": None, "mid_logloss": None}
    market_means = [np.mean(values, axis=0) for values in groups.values()]
    means = np.mean(market_means, axis=0)
    return dict(zip(("brier", "mid_brier", "logloss", "mid_logloss"), map(float, means)),
                labeled_markets=len(groups), labeled_rows=sum(map(len, groups.values())),
                weighting="equal_market_then_equal_rows_within_market",
                inference="descriptive_only_no_independence_or_significance_claim")


def train_forward_days(events: Iterable[dict], *, consumed_days: Iterable[str],
                       input_hashes: Mapping[str, str]):
    """Return (audit, score_map); score_map key is (market_id, recv_ms, model_id).

    Event order can span several separately sorted files; duplicates must agree.
    Scores contain model_fit_ms and model_hash, but never use the prediction-day
    outcome. Metrics are computed after scoring and are reused-development only.
    """
    if not input_hashes or any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
                               for v in input_hashes.values()):
        raise ValueError("explicit_sha256_input_hashes_required")
    days = sorted(set(consumed_days))
    rows, input_audit = _collect(events, days)
    folds, scores = [], {}
    for day in days:
        cutoff = _start(day)
        test = [r for r in rows if _day(r["recv_ms"]) == day]
        # A market spanning midnight cannot straddle fitting and scoring groups.
        test_ids = {str(r["market_id"]) for r in test}
        for model_id in MODEL_IDS:
            training, eligible_test, invalid = [], [], Counter()
            for row in rows:
                if _day(row["recv_ms"]) >= day or str(row["market_id"]) in test_ids:
                    continue
                if (row["target_up"] is None or row["settlement_known_ms"] >= cutoff
                        or row["market_end_ms"] >= cutoff):
                    invalid["training_outcome_not_known_before_day"] += 1
                    continue
                try:
                    _causal_vector(row, model_id)
                except ValueError as exc:
                    invalid["training:" + str(exc)] += 1
                    continue
                training.append(row)
            fold = {"day_utc": day, "model_id": model_id,
                    "evidence_class": "reused_development_forward_diagnostic",
                    "train_days": sorted({_day(r["recv_ms"]) for r in training}),
                    "train_markets": len({r["market_id"] for r in training}),
                    "train_rows": len(training), "prediction_rows_total": len(test),
                    "prediction_markets_total": len(test_ids), "fit_cutoff_ms": cutoff,
                    "invalid_rows": dict(invalid), "metrics": None, "fitted": None}
            label_by_market = {str(r["market_id"]): r["target_up"] for r in training}
            fold.update(training_class_market_counts=dict(Counter(label_by_market.values())),
                        latest_training_label_known_ms=max((r["settlement_known_ms"] for r in training), default=None),
                        fit_mode="unfitted_midpoint_control" if model_id == "market_raw" else "fixed_offset_l2_fit")
            try:
                model = fit_residual(training, model_id, cutoff)
            except ValueError as exc:
                fold.update(status="blocked", reason=str(exc), scored_rows=0, scored_markets=0)
                folds.append(fold)
                continue
            predicted = []
            descriptor = model.as_dict()
            for row in test:
                try:
                    p = model.predict(row)
                except ValueError as exc:
                    invalid["prediction:" + str(exc)] += 1
                    continue
                key = (str(row["market_id"]), row["recv_ms"], model_id)
                scores[key] = {"p_up": p, "model_fit_ms": cutoff, "model_hash": descriptor["model_hash"],
                               "feature_available_ms": max(row["feature_available_ms"][x] for x in REQUIRED[model_id])}
                eligible_test.append(row)
                predicted.append(p)
            fold.update(status="scored", fitted=descriptor, invalid_rows=dict(invalid),
                        scored_rows=len(predicted), scored_markets=len({r["market_id"] for r in eligible_test}),
                        metrics=_prediction_metrics(eligible_test, predicted))
            folds.append(fold)
    audit = {"version": VERSION, "evidence_class": "reused_development_forward_diagnostic",
             "registered_trials": len(MODEL_IDS) * len(MARGINS), "model_specs": list(MODEL_IDS),
             "independent_economic_mechanisms_claimed": 0,
             "fixed_cost_margins": list(MARGINS), "fixed_l2": L2,
             "minimum_training_markets": MIN_TRAIN_MARKETS,
             "minimum_markets_per_class": MIN_CLASS_MARKETS, "input_sha256": dict(input_hashes),
             "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "consumed_days_utc": days, "input_audit": input_audit, "folds": folds,
             "formal_oos_result": None, "confirmed_strategies": 0,
             "limitations": ["historical screening counts remain in multiplicity denominator",
                 "probability calibration improvement alone does not establish tradable edge",
                 "daily folds and repeated decisions are dependent",
                 "displayed-book replay does not calibrate actual fill probability"]}
    return audit, scores


def make_candidates():
    return [{"id": f"MPR-{model_id}-{round(margin*100):02d}", "model_id": model_id,
             "family": "market_prior_conditional_residual", "margin": margin,
             "required_features": [f"prob_{model_id}", "ask_up", "ask_down", "decision_fee_rate"],
             "novelty": "fixed_offset_probability_residual_comparison_not_new_independent_mechanism",
             "status": "registered_development_only"}
            for model_id in MODEL_IDS for margin in MARGINS]


def attach_scores(events: Iterable[dict], score_map):
    """Join by exact market+receipt time; read only previous book state.

    Invalid/stale current books are NOT silently removed: their prices still
    create the first signal where available, and the unchanged replay executor
    records its rejection. An absent price remains absent; no future search.
    """
    books, previous = {}, -math.inf
    for raw in events:
        now = raw["recv_ms"]
        if now < previous:
            raise ValueError("event_receipt_clock_regressed")
        previous = now
        mid = str(raw.get("market_id", ""))
        if raw.get("type") == "book":
            books[mid] = raw
        if raw.get("type") != "decision":
            yield raw
            continue
        features, available = dict(raw.get("features", {})), dict(raw.get("feature_available_ms", {}))
        book = books.get(mid)
        for side in ("up", "down"):
            key = "ask_" + side
            levels = book.get(side + "_asks", []) if book else []
            valid = [(p, q) for p, q in levels if _finite(p) and 0 < p < 1 and _finite(q) and q > 0]
            stamp = book.get(side + "_recv_ms", book["recv_ms"]) if book else None
            features[key] = min((p for p, _ in valid), default=None) if _finite(stamp) and stamp <= now else None
            available[key] = stamp if features[key] is not None else None
        fee = book.get("fee_rate") if book else None
        features["decision_fee_rate"] = fee if _finite(fee) and 0 <= fee <= 1 else None
        available["decision_fee_rate"] = book["recv_ms"] if features["decision_fee_rate"] is not None else None
        for model_id in MODEL_IDS:
            value = score_map.get((mid, now, model_id))
            key = "prob_" + model_id
            if value and (value["model_fit_ms"] > now or value["feature_available_ms"] > now):
                raise ValueError("future_model_or_features_in_score_map")
            features[key] = value["p_up"] if value else None
            available[key] = max(value["model_fit_ms"], value["feature_available_ms"]) if value else None
        yield dict(raw, features=features, feature_available_ms=available)


def signal(candidate, features):
    p = features.get("prob_" + candidate["model_id"])
    fee, up, down = (features.get(k) for k in ("decision_fee_rate", "ask_up", "ask_down"))
    if not all(_finite(x) for x in (p, fee, up, down)) or not 0 <= p <= 1:
        return None
    if not 0 <= fee <= 1 or not 0 < up < 1 or not 0 < down < 1:
        return None
    up_edge, down_edge = p-up-fee*up*(1-up), 1-p-down-fee*down*(1-down)
    if max(up_edge, down_edge) < candidate["margin"]:
        return None
    return "Up" if up_edge >= down_edge else "Down"
