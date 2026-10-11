"""Append-only forward gate for the Family 37 settlement-lag strategy."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import h_forward
import settlement_lag as replay
import source_confirmation
import source_race_daily

ROOT = Path(__file__).resolve().parent
REQUIRED_DEPENDENCIES = frozenset(
    {
        "archive_day.py",
        "eu_strict.py",
        "forward/current-h-freeze.json",
        "h_forward.py",
        "h_replay.py",
        "h_replay_archive.py",
        "h_replay_run.py",
        "h_source_race.py",
        "pm_outcomes.py",
        "poly_clob_rec.py",
        "raw_rec.py",
        "requirements.txt",
        "settlement_lag.py",
        "settlement_lag_archive.py",
        "settlement_lag_daily.py",
        "settlement_lag_eu_strict.py",
        "settlement_lag_forward.py",
        "settlement_lag_meta_rec.py",
        "settlement-lag-runtime-lock.json",
        "settlement_lag_runtime.py",
        "settlement_lag_strict_prebuild.py",
        "settlement_lag_twap_rec.py",
        "settlement_proxy.py",
        "source_confirmation.py",
        "source_confirmation_daily.py",
        "source_confirmation_forward.py",
        "source_race_daily.py",
        "source_race_forward.py",
        "strict_prebuild.py",
        "systemd/recorder-day-boundary.service",
        "systemd/recorder-day-boundary.timer",
        "systemd/poly-clob-recorder.service",
        "systemd/raw-recorder.service",
        "systemd/settlement-lag-daily.service",
        "systemd/settlement-lag-daily.timer",
        "systemd/settlement-lag-meta-recorder.service",
        "systemd/settlement-lag-strict-artifact.service",
        "systemd/settlement-lag-strict-artifact.timer",
        "systemd/settlement-lag-twap-recorder.service",
        "systemd/strict-artifact-daily.service",
        "systemd/strict-artifact-daily.timer",
    }
)
OBSERVATIONS = "observations.jsonl"
STATUS = "status.json"
VERDICT = "verdict.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EPS = 1e-9


def load_freeze(
    path: str | Path = replay.FREEZE_PATH,
) -> tuple[dict[str, Any], str]:
    """Verify the immutable protocol bytes and its exact runtime closure."""
    source = Path(path)
    frozen = replay.load_freeze(source)
    digest = _sha256(source)
    dependencies = frozen.get("dependencies_sha256")
    if (
        not isinstance(dependencies, Mapping)
        or set(dependencies) != REQUIRED_DEPENDENCIES
    ):
        raise ValueError("settlement-lag dependency closure drift")
    root = ROOT.resolve()
    for raw_name, raw_digest in dependencies.items():
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or not isinstance(raw_digest, str)
            or _SHA256_RE.fullmatch(raw_digest) is None
        ):
            raise ValueError("settlement-lag dependency identity is invalid")
        relative = Path(raw_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("settlement-lag dependency path is invalid")
        dependency = (ROOT / relative).resolve()
        try:
            dependency.relative_to(root)
        except ValueError as exc:
            raise ValueError("settlement-lag dependency path is invalid") from exc
        if not dependency.is_file() or _sha256(dependency) != raw_digest:
            raise ValueError(f"settlement-lag dependency drift: {raw_name}")
    replay.config_from_freeze(frozen)
    safety = frozen.get("safety")
    if not isinstance(safety, Mapping) or (
        safety.get("paper_only") is not True
        or safety.get("wallet_access") is not False
        or safety.get("production_orders") is not False
    ):
        raise ValueError("settlement-lag protocol is not paper-only")
    return frozen, digest


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} is non-finite")
    return number


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def observation_key(row: Mapping[str, Any]) -> tuple[str, str, str, float, str]:
    return (
        str(row["observation_run_id"]),
        str(row["variant"]),
        str(row["market_id"]),
        float(row["evaluation_ms"]),
        str(row["artifact_files_sha256"]),
    )


def _validate_row(row: Mapping[str, Any], frozen: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    required = (
        "strategy_id",
        "family",
        "variant",
        "market_id",
        "protocol_slot",
        "day",
        "direction",
        "winner",
        "source_revision_after_decision",
        "evaluation_ms",
        "evaluation_time_ms",
        "observation_available_ms",
        "sent",
        "filled_shares",
        "qualified_fill",
        "reason",
        "pnl",
        "pnl_per_share",
        "all_in_cost",
        "gross_pnl",
        "capital_lock_seconds",
        "capital_lock_cost",
        "redemption_cost",
        "fixed_limit",
        "fill_price",
        "fill_fee",
        "fill_fee_per_share",
        "market_close_ms",
        "result_time_ms",
        "proof_payload_ms",
        "proof_push_ms",
        "proof_lead_ms",
        "proof_opening",
        "proof_closing",
        "proof_margin",
        "artifact_files_sha256",
        "artifact_started_ms",
        "artifact_ended_ms",
        "artifact_collector_region",
        "observation_run_id",
        "match_book_clock",
        "match_level_clock",
        "decision_execution_path",
        "effective_execution_path",
    )
    if any(name not in normalized for name in required):
        raise ValueError("settlement-lag observation is incomplete")
    if (
        normalized["strategy_id"] != frozen["strategy_id"]
        or normalized["family"] != frozen["economic_family"]
        or normalized["variant"]
        not in {
            replay.FINAL_VARIANT,
            replay.PREFINAL_VARIANT,
            replay.WRONG_SIDE_VARIANT,
        }
        or normalized["direction"] not in {"Up", "Down"}
        or normalized["winner"] not in {"Up", "Down"}
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(normalized["day"]))
        or not _SHA256_RE.fullmatch(str(normalized["artifact_files_sha256"]))
    ):
        raise ValueError("settlement-lag observation identity drift")
    evaluation = _finite(normalized["evaluation_ms"], "evaluation_ms")
    if evaluation not in {
        float(value) for value in frozen["execution"]["evaluation_ms"]
    }:
        raise ValueError("settlement-lag observation horizon drift")
    evaluation_time = _finite(normalized["evaluation_time_ms"], "evaluation_time_ms")
    available = _finite(
        normalized["observation_available_ms"],
        "observation_available_ms",
    )
    shares = _finite(normalized["filled_shares"], "filled_shares")
    if available + _EPS < evaluation_time or shares < 0:
        raise ValueError("settlement-lag observation availability drift")
    slot = int(_finite(normalized["protocol_slot"], "protocol_slot"))
    if float(slot) != _finite(normalized["protocol_slot"], "protocol_slot"):
        raise ValueError("settlement-lag protocol slot drift")
    market_close = _finite(normalized["market_close_ms"], "market_close_ms")
    if not math.isclose(market_close, (slot + 300) * 1_000.0, abs_tol=_EPS):
        raise ValueError("settlement-lag market close drift")
    decision_time = evaluation_time - evaluation
    if not slot * 1_000.0 <= decision_time < market_close:
        raise ValueError("settlement-lag decision clock drift")
    expected_day = (
        datetime.fromtimestamp(decision_time / 1_000.0, UTC).date().isoformat()
    )
    if normalized["day"] != expected_day:
        raise ValueError("settlement-lag UTC day drift")
    artifact_start = _finite(normalized["artifact_started_ms"], "artifact_started_ms")
    artifact_end = _finite(normalized["artifact_ended_ms"], "artifact_ended_ms")
    if (
        normalized["artifact_collector_region"] != frozen["market"]["collector_region"]
        or not str(normalized["observation_run_id"])
        or artifact_start > decision_time + _EPS
        or artifact_end + _EPS < evaluation_time
        or artifact_end < artifact_start
    ):
        raise ValueError("settlement-lag artifact provenance drift")
    holdout_start = (
        datetime.fromisoformat(
            str(frozen["formal_holdout_start"]),
        ).timestamp()
        * 1_000.0
    )
    if slot * 1_000.0 < holdout_start - _EPS:
        raise ValueError("settlement-lag observation predates the holdout")
    result_time = _finite(normalized["result_time_ms"], "result_time_ms")
    if result_time < market_close - _EPS or not math.isclose(
        available, max(evaluation_time, result_time), abs_tol=_EPS
    ):
        raise ValueError("settlement-lag result availability drift")
    proof_payload = _finite(normalized["proof_payload_ms"], "proof_payload_ms")
    proof_push = _finite(normalized["proof_push_ms"], "proof_push_ms")
    proof_lead = _finite(normalized["proof_lead_ms"], "proof_lead_ms")
    expected_offset = (
        float(frozen["controls"]["prefinality_payload_offset_ms"])
        if normalized["variant"] == replay.PREFINAL_VARIANT
        else float(frozen["proof"]["closing_payload_offset_ms"])
    )
    if (
        not math.isclose(proof_payload, market_close + expected_offset, abs_tol=_EPS)
        or proof_push > decision_time + _EPS
        or not math.isclose(proof_lead, market_close - decision_time, abs_tol=1e-6)
    ):
        raise ValueError("settlement-lag proof clock drift")
    opening = _finite(normalized["proof_opening"], "proof_opening")
    closing = _finite(normalized["proof_closing"], "proof_closing")
    margin = _finite(normalized["proof_margin"], "proof_margin")
    proof_direction = "Up" if closing >= opening else "Down"
    expected_direction = proof_direction
    if normalized["variant"] == replay.WRONG_SIDE_VARIANT:
        expected_direction = "Down" if proof_direction == "Up" else "Up"
    if (
        opening <= 0
        or closing <= 0
        or not math.isclose(margin, abs(closing - opening) / opening, abs_tol=1e-12)
        or normalized["direction"] != expected_direction
    ):
        raise ValueError("settlement-lag proof economics drift")
    fixed_limit = _finite(normalized["fixed_limit"], "fixed_limit")
    if (
        not math.isclose(
            fixed_limit,
            float(frozen["execution"]["fixed_limit"]),
            abs_tol=_EPS,
        )
        or normalized["match_book_clock"] != "receipt_ms"
        or normalized["match_level_clock"] != "original_local_receipt_ms"
    ):
        raise ValueError("settlement-lag execution contract drift")
    base_shares = float(frozen["execution"]["base_shares"])
    if shares > base_shares:
        raise ValueError("settlement-lag fill exceeds frozen base shares")
    valid_paths = {"native_ask", "complementary_bid"}
    decision_path = normalized.get("decision_execution_path")
    effective_path = normalized.get("effective_execution_path")
    if (
        (decision_path is not None and decision_path not in valid_paths)
        or (effective_path is not None and effective_path not in valid_paths)
        or (bool(normalized["sent"]) and decision_path not in valid_paths)
        or (shares > 0 and effective_path not in valid_paths)
    ):
        raise ValueError("settlement-lag execution path provenance drift")
    expected_qualified = (
        shares >= base_shares - _EPS and normalized["reason"] == "filled"
    )
    if bool(normalized["qualified_fill"]) != expected_qualified:
        raise ValueError("settlement-lag full-fill qualification drift")
    if shares > 0:
        if normalized.get("book_receipt_provenance_exact") is not True:
            raise ValueError(
                "filled settlement-lag row lacks exact book receipt provenance"
            )
        numeric = (
            "fill_price",
            "fill_fee",
            "fill_fee_per_share",
            "all_in_cost",
            "gross_pnl",
            "capital_lock_seconds",
            "capital_lock_cost",
            "redemption_cost",
            "pnl",
            "pnl_per_share",
        )
        try:
            values = {name: float(normalized[name]) for name in numeric}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "filled settlement-lag row has incomplete economics"
            ) from exc
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("filled settlement-lag row has non-finite economics")
        won = normalized["direction"] == normalized["winner"]
        expected = (
            ((1.0 if won else 0.0) - values["all_in_cost"]) * shares
            - values["capital_lock_cost"]
            - values["redemption_cost"]
        )
        expected_lock_seconds = max(0.0, (result_time - evaluation_time) / 1_000.0)
        expected_capital_cost = (
            values["all_in_cost"]
            * shares
            * float(frozen["execution"].get("capital_annual_rate", 0.10))
            * expected_lock_seconds
            / (365.25 * 86_400.0)
        )
        if (
            normalized.get("won") is not won
            or not 0 < values["fill_price"] <= fixed_limit + _EPS
            or values["fill_fee"] < 0
            or not math.isclose(
                values["fill_fee_per_share"],
                values["fill_fee"] / shares,
                abs_tol=1e-12,
            )
            or not math.isclose(
                values["all_in_cost"],
                values["fill_price"] + values["fill_fee_per_share"],
                abs_tol=1e-12,
            )
            or not math.isclose(
                values["gross_pnl"],
                ((1.0 if won else 0.0) - values["all_in_cost"]) * shares,
                abs_tol=1e-9,
            )
            or not math.isclose(
                values["capital_lock_seconds"],
                expected_lock_seconds,
                abs_tol=1e-9,
            )
            or not math.isclose(
                values["capital_lock_cost"],
                expected_capital_cost,
                abs_tol=1e-9,
            )
            or not math.isclose(
                values["redemption_cost"],
                float(frozen["execution"].get("redemption_cost_usd", 0.01)),
                abs_tol=1e-12,
            )
            or not math.isclose(values["pnl"], expected, abs_tol=1e-9)
            or not math.isclose(
                values["pnl_per_share"], expected / shares, abs_tol=1e-9
            )
        ):
            raise ValueError("settlement-lag observation PnL drift")
    elif (
        any(
            normalized.get(name) is not None
            for name in (
                "fill_price",
                "fill_fee_per_share",
                "all_in_cost",
                "gross_pnl",
                "pnl",
                "pnl_per_share",
            )
        )
        or not math.isclose(_finite(normalized["fill_fee"], "fill_fee"), 0.0)
        or not math.isclose(
            _finite(normalized["capital_lock_seconds"], "capital_lock_seconds"),
            0.0,
        )
        or not math.isclose(
            _finite(normalized["capital_lock_cost"], "capital_lock_cost"),
            0.0,
        )
        or not math.isclose(
            _finite(normalized["redemption_cost"], "redemption_cost"),
            0.0,
        )
    ):
        raise ValueError("unfilled settlement-lag observation has economics")
    return normalized


def merge_rows(
    existing: Iterable[Mapping[str, Any]],
    incoming: Iterable[Mapping[str, Any]],
    frozen: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    frozen = load_freeze()[0] if frozen is None else frozen
    merged: dict[tuple[str, str, str, float, str], dict[str, Any]] = {}
    for raw in [*existing, *incoming]:
        row = _validate_row(raw, frozen)
        key = observation_key(row)
        prior = merged.get(key)
        if prior is not None and _canonical(prior) != _canonical(row):
            raise ValueError(f"settlement-lag observation conflict: {key}")
        merged[key] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            float(row["observation_available_ms"]),
            int(row["protocol_slot"]),
            str(row["market_id"]),
            str(row["variant"]),
            float(row["evaluation_ms"]),
        ),
    )


def first_stopping_day(
    rows: Sequence[Mapping[str, Any]],
    frozen: Mapping[str, Any],
) -> str | None:
    primary = float(frozen["execution"]["primary_evaluation_ms"])
    minimum_fills = int(frozen["stopping"]["minimum_primary_full_fills"])
    minimum_days = int(frozen["stopping"]["minimum_utc_days"])
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["variant"] != replay.FINAL_VARIANT or not math.isclose(
            float(row["evaluation_ms"]),
            primary,
            abs_tol=1e-9,
        ):
            continue
        by_day.setdefault(str(row["day"]), []).append(row)
    fills = 0
    fill_days = 0
    for day in sorted(by_day):
        day_fills = sum(bool(row["qualified_fill"]) for row in by_day[day])
        fills += day_fills
        if day_fills:
            fill_days += 1
        if fills >= minimum_fills and fill_days >= minimum_days:
            return day
    return None


def _utc_day_end_ms(day: str) -> float:
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return (start + timedelta(days=1)).timestamp() * 1_000.0 - 1.0


def _cutoff_day_from_ms(cutoff: float | None) -> str | None:
    if cutoff is None:
        return None
    return datetime.fromtimestamp(float(cutoff) / 1_000.0, UTC).date().isoformat()


def first_stopping_cutoff(
    rows: Sequence[Mapping[str, Any]],
    frozen: Mapping[str, Any],
) -> float | None:
    """Return the inclusive UTC-day boundary kept for status compatibility."""
    day = first_stopping_day(rows, frozen)
    return _utc_day_end_ms(day) if day is not None else None


def rows_at_cutoff(
    rows: Sequence[Mapping[str, Any]],
    cutoff: float | None,
    *,
    cutoff_day: str | None = None,
) -> list[dict[str, Any]]:
    if cutoff_day is None:
        cutoff_day = _cutoff_day_from_ms(cutoff)
    if cutoff_day is None:
        return [dict(row) for row in rows]
    return [dict(row) for row in rows if str(row["day"]) <= cutoff_day]


def _signals(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[replay.SettlementSignal]]:
    output: dict[str, list[replay.SettlementSignal]] = {
        replay.FINAL_VARIANT: [],
        replay.PREFINAL_VARIANT: [],
        replay.WRONG_SIDE_VARIANT: [],
    }
    for variant in output:
        unique: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if row["variant"] != variant:
                continue
            market_id = str(row["market_id"])
            prior = unique.get(market_id)
            if prior is not None and (
                prior["direction"] != row["direction"]
                or prior["winner"] != row["winner"]
                or prior["source_revision_after_decision"]
                != row["source_revision_after_decision"]
                or int(prior["protocol_slot"]) != int(row["protocol_slot"])
                or prior["observation_run_id"] != row["observation_run_id"]
                or prior["artifact_files_sha256"] != row["artifact_files_sha256"]
                or any(
                    not math.isclose(
                        float(prior[name]),
                        float(row[name]),
                        abs_tol=1e-9,
                    )
                    for name in (
                        "proof_payload_ms",
                        "proof_push_ms",
                        "proof_lead_ms",
                        "proof_opening",
                        "proof_closing",
                        "proof_margin",
                    )
                )
                or not math.isclose(
                    float(prior["evaluation_time_ms"]) - float(prior["evaluation_ms"]),
                    float(row["evaluation_time_ms"]) - float(row["evaluation_ms"]),
                    abs_tol=1e-9,
                )
            ):
                raise ValueError(
                    f"settlement-lag signal drift across horizons: {market_id}"
                )
            unique[market_id] = row
        output[variant] = [
            replay.SettlementSignal(
                variant=variant,
                market_id=market_id,
                slot=int(row["protocol_slot"]),
                decision_ms=float(row["evaluation_time_ms"])
                - float(row["evaluation_ms"]),
                direction=str(row["direction"]),
                opening=float(row["proof_opening"]),
                closing=float(row["proof_closing"]),
                margin=float(row["proof_margin"]),
                proof_payload_ms=float(row.get("proof_payload_ms") or 0.0),
                proof_push_ms=float(row.get("proof_push_ms") or 0.0),
                proof_lead_ms=float(row.get("proof_lead_ms") or 0.0),
                actual_winner=str(row["winner"]),
                source_revision_after_decision=bool(
                    row["source_revision_after_decision"]
                ),
            )
            for market_id, row in sorted(unique.items())
        ]
    return output


def _control_identity(
    row: Mapping[str, Any],
) -> tuple[str, str, str, int, float]:
    return (
        str(row["artifact_files_sha256"]),
        str(row["observation_run_id"]),
        str(row["market_id"]),
        int(row["protocol_slot"]),
        float(row["evaluation_ms"]),
    )


def _control_integrity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    indexed: dict[str, dict[tuple[str, str, str, int, float], Mapping[str, Any]]] = {
        replay.FINAL_VARIANT: {},
        replay.PREFINAL_VARIANT: {},
        replay.WRONG_SIDE_VARIANT: {},
    }
    for row in rows:
        variant = str(row["variant"])
        if variant not in indexed:
            continue
        key = _control_identity(row)
        if key in indexed[variant]:
            raise ValueError(f"duplicate settlement-lag {variant} observation: {key}")
        indexed[variant][key] = row

    primary = set(indexed[replay.FINAL_VARIANT])
    prefinal = set(indexed[replay.PREFINAL_VARIANT])
    wrong_side = set(indexed[replay.WRONG_SIDE_VARIANT])
    matched_prefinal = primary & prefinal
    matched_wrong_side = primary & wrong_side
    tainted = sum(
        bool(row["source_revision_after_decision"])
        for variant in (replay.PREFINAL_VARIANT, replay.WRONG_SIDE_VARIANT)
        for row in indexed[variant].values()
    )
    count = len(primary)
    missing_prefinal = primary - prefinal
    missing_wrong_side = primary - wrong_side
    unmatched_prefinal = prefinal - primary
    unmatched_wrong_side = wrong_side - primary
    complete = bool(
        count
        and not missing_prefinal
        and not missing_wrong_side
        and not unmatched_prefinal
        and not unmatched_wrong_side
        and tainted == 0
    )
    return {
        "complete": complete,
        "primary_opportunities": count,
        "matched_prefinal": len(matched_prefinal),
        "matched_wrong_side": len(matched_wrong_side),
        "prefinal_coverage": len(matched_prefinal) / count if count else 0.0,
        "wrong_side_coverage": len(matched_wrong_side) / count if count else 0.0,
        "missing_prefinal": len(missing_prefinal),
        "missing_wrong_side": len(missing_wrong_side),
        "unmatched_prefinal": len(unmatched_prefinal),
        "unmatched_wrong_side": len(unmatched_wrong_side),
        "revision_tainted_controls": tainted,
    }


def evaluate(
    rows: Sequence[Mapping[str, Any]], frozen: Mapping[str, Any]
) -> dict[str, Any]:
    config = replay.config_from_freeze(frozen)
    grouped = {
        variant: [dict(row) for row in rows if row["variant"] == variant]
        for variant in (
            replay.FINAL_VARIANT,
            replay.PREFINAL_VARIANT,
            replay.WRONG_SIDE_VARIANT,
        )
    }
    signals = _signals(rows)
    final_signals = signals[replay.FINAL_VARIANT]
    audit = {
        "official_matches": sum(
            signal.direction == signal.actual_winner for signal in final_signals
        ),
        "official_mismatches": sum(
            signal.direction != signal.actual_winner for signal in final_signals
        ),
        "unresolved_official": 0,
        "post_decision_revisions": sum(
            signal.source_revision_after_decision for signal in final_signals
        ),
    }
    control_integrity = _control_integrity(rows)
    result = replay.evaluate(signals, grouped, audit, config)
    controls_gate = bool(
        result["gate"]["controls_gate"] and control_integrity["complete"]
    )
    result["control_integrity"] = control_integrity
    result["gate"]["controls_gate"] = controls_gate
    result["gate"]["paper_gate_passes"] = bool(
        result["gate"]["paper_gate_passes"] and controls_gate
    )
    result["stopping_rows"] = len(rows)
    result["passes"] = False
    result["status"] = (
        "paper_passed_capacity_pending"
        if result["gate"]["paper_gate_passes"]
        else "rejected"
    )
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} must contain an object")
    return value


def accumulate(
    incoming: Iterable[Mapping[str, Any]],
    frozen: Mapping[str, Any],
    freeze_sha256: str,
    *,
    ledger_path: str | Path,
    status_path: str | Path,
    verdict_path: str | Path,
) -> dict[str, Any]:
    if not _SHA256_RE.fullmatch(freeze_sha256):
        raise ValueError("invalid settlement-lag freeze identity")
    ledger = Path(ledger_path)
    status_file = Path(status_path)
    verdict_file = Path(verdict_path)
    existing = h_forward._read_jsonl(ledger) if ledger.is_file() else []
    incoming_rows = [dict(row) for row in incoming]
    pinned = _read_json(verdict_file)
    if pinned is not None:
        if pinned.get("freeze_sha256") != freeze_sha256 or pinned.get(
            "ledger_sha256"
        ) != _sha256(ledger):
            raise ValueError("settlement-lag pinned verdict identity drift")
        merged = merge_rows(existing, incoming_rows, frozen)
        if len(merged) != len(existing):
            raise ValueError(
                "new settlement-lag observation arrived after the first verdict"
            )
        return pinned

    status = _read_json(status_file)
    if status is not None and status.get("status") != "collecting":
        merged = merge_rows(existing, incoming_rows, frozen)
        if len(merged) != len(existing):
            raise ValueError(
                "new settlement-lag observation arrived after the first verdict"
            )
        result = evaluate(existing, frozen)
        result.update(
            {
                "freeze_sha256": freeze_sha256,
                "cutoff_ms": status["cutoff_ms"],
                "cutoff_day": status.get("cutoff_day")
                or _cutoff_day_from_ms(status.get("cutoff_ms")),
                "ledger_sha256": _sha256(ledger),
            }
        )
        source_race_daily._atomic_json(verdict_file, result)
        return result

    merged = merge_rows(existing, incoming_rows, frozen)
    cutoff_day = first_stopping_day(merged, frozen)
    cutoff = _utc_day_end_ms(cutoff_day) if cutoff_day is not None else None
    selected = rows_at_cutoff(merged, cutoff, cutoff_day=cutoff_day)
    source_confirmation._atomic_write_rows(ledger, selected)
    ledger_sha256 = _sha256(ledger)
    if cutoff is None:
        result = {
            "status": "collecting",
            "freeze_sha256": freeze_sha256,
            "rows": len(selected),
            "cutoff_ms": None,
            "cutoff_day": None,
            "ledger_sha256": ledger_sha256,
            "passes": False,
        }
        source_race_daily._atomic_json(status_file, result)
        return result

    result = evaluate(selected, frozen)
    result.update(
        {
            "freeze_sha256": freeze_sha256,
            "cutoff_ms": cutoff,
            "cutoff_day": cutoff_day,
            "ledger_sha256": ledger_sha256,
        }
    )
    source_race_daily._atomic_json(status_file, result)
    source_race_daily._atomic_json(verdict_file, result)
    return result
