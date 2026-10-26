"""First-signal patient-entry development replay. No live order operation.

The execution function below is an explicit fork of mine500_replay_v2.py
83a5e555c5ecdc9bef0cd27da383e22fea37c7754ae4dd0a5d7e9b7c91472420.
Its book/fee/constraint helpers remain imported from that unchanged parent.
New semantics: fixed first-signal cohort, staged quantities/limits, shared
cash and shared same-snapshot depth within each parent/policy/latency.
All economic evidence is consumed development; complete-week/OOS remain null.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING
import hashlib
import heapq
import inspect
import json
import math
from pathlib import Path
from typing import Callable, Iterable

import mine500_replay_v2 as parent
import mine500_constraints
from mine500_constraints import check_constraints
from mine500_replay_v2 import (
    ReplayConfig, OFFICIAL_SOURCES, EPS, FEE_QUANTUM, fingerprint,
    _book, _number, _fee, _rounded_order_fee, _same_observed_interval,
    _split_validation, _validate_observed_intervals, _levels,
)

PARENT_SHA256 = "83a5e555c5ecdc9bef0cd27da383e22fea37c7754ae4dd0a5d7e9b7c91472420"
DEADLINE_MS = 10_000
# Each tuple is (quantity / base target, committed dollar discount).
POLICIES = {
    "BASE100": ((1., 0.),), "WAIT1": ((1., .01),), "WAIT2": ((1., .02),),
    "SPLIT50_50": ((.5, 0.), (.5, .01)),
    "FRONT50_25_25": ((.5, 0.), (.25, .01), (.25, .02)),
    "BACK25_25_50": ((.25, 0.), (.25, .01), (.5, .02)),
    "ADD100_100": ((1., 0.), (1., .01)),
    "ADD100_100_100": ((1., 0.), (1., .01), (1., .02)),
    "NOW200": ((2., 0.),), "NOW300": ((3., 0.),),
}


def _leg_signal(candidate, features):
    return "Up" if features["patient_side"] == 1 else "Down"


def replay_events(events, candidates, signal_fn, config=None, policies=None):
    """Replay all selected policies without calling a parent after its first signal.

    Callback is stateless or parent/market-keyed; one invocation is shared by
    every policy and latency. Original scale policies are deliberately replaced
    by first-signal treatment, not inherited or silently approximated.
    """
    if hashlib.sha256(Path(parent.__file__).read_bytes()).hexdigest() != PARENT_SHA256:
        raise RuntimeError("unchanged parent execution dependency hash mismatch")
    config = config or ReplayConfig(shares=100.)
    names = list(POLICIES) if policies is None else list(policies)
    if not names or len(names) != len(set(names)) or any(n not in POLICIES for n in names):
        raise ValueError("unique registered policies required")
    parents = {str(c["id"]): c for c in candidates}
    if len(parents) != len(candidates):
        raise ValueError("parent IDs must be unique")
    legs, leg_map = [], {}
    for cid, candidate in parents.items():
        for policy in names:
            for ordinal, (weight, discount) in enumerate(POLICIES[policy]):
                key = f"{cid}::{policy}::L{ordinal}"
                leg = {"id": key, "required_features": ["tau_s", "patient_side", "patient_limit"],
                    "_ledger_id": f"{cid}::{policy}", "_parent_id": cid,
                    "_policy": policy, "_ordinal": ordinal,
                    "_patient_quantity": config.shares*weight, "_discount": discount}
                legs.append(leg); leg_map[key] = leg
    books, markets, dataset = {}, {}, None
    seen, waiting, plans, cohorts = set(), defaultdict(list), [], []
    counters, rejected = Counter(), defaultdict(Counter)
    valid = Counter()

    def emitted(event, plan):
        plan["status"] = "triggered_once"
        plan["trigger_ms"] = event["recv_ms"]
        now = event["recv_ms"]
        return {"type": "decision", "recv_ms": now, "market_id": plan["market_id"],
                "patient_dispatch_id": plan["leg_id"],
                "patient_parent_first_ms": plan["parent_first_ms"],
                "features": {"tau_s": (markets[plan["market_id"]]["end_ms"]-now)/1000,
                    "patient_side": 1 if plan["side"] == "Up" else -1,
                    "patient_limit": plan["limit_price"] if plan["limit_price"] is not None else 0.},
                "feature_available_ms": {"tau_s": now, "patient_side": plan["parent_first_ms"],
                    "patient_limit": plan["parent_first_ms"]}}

    def raw_ask(mid, side):
        try:
            levels = _levels(books.get(mid, {}).get(side.lower()+"_asks"))
            return levels[0][0] if levels else None
        except ValueError:
            return None

    def routed():
        nonlocal dataset
        for event in events:
            now, kind, mid = event.get("recv_ms"), event.get("type"), event.get("market_id")
            if not _number(now):
                raise ValueError("finite local receipt required")
            if kind == "dataset": dataset = event
            elif kind == "market": markets[mid] = event
            elif kind == "book": books[mid] = event
            if kind != "decision":
                yield event
                continue
            # Only original decision records, never projected book timestamps,
            # can trigger a waiting order. The primary normalized grid is1s.
            if abs(now/1000-round(now/1000)) > 1e-6:
                raise ValueError("patient policy requires original one-second decision grid")
            counters["original_decisions"] += 1
            active = []
            for plan in waiting.pop(mid, []):
                if now >= plan["deadline_ms"]:
                    plan["status"] = "expired_without_send"
                    continue
                if not _same_observed_interval(dataset, plan["parent_first_ms"], now):
                    plan["status"] = "censored_recording_gap"
                    continue
                ask = raw_ask(mid, plan["side"])
                if ask is None:
                    counters["wait_missing_or_invalid_quote_checks"] += 1
                elif ask <= plan["limit_price"] + EPS:
                    yield emitted(event, plan)
                    continue
                active.append(plan)
            waiting[mid].extend(active)
            f, a, market = event.get("features"), event.get("feature_available_ms"), markets.get(mid)
            for cid, candidate in parents.items():
                if (cid, mid) in seen: continue
                error = None
                if market is None or not market.get("start_ms", math.inf) <= now < market.get("end_ms", -math.inf):
                    error = "missing_market_or_outside_window"
                elif not isinstance(f, dict) or not isinstance(a, dict): error = "missing_feature_maps"
                else:
                    for name in set(candidate["required_features"]) | {"tau_s"}:
                        if not _number(f.get(name)): error = "missing_or_invalid_feature:"+name; break
                        if not _number(a.get(name)): error = "missing_feature_availability:"+name; break
                        if a[name] > now: error = "future_feature:"+name; break
                    if error is None and abs(f["tau_s"]-(market["end_ms"]-now)/1000) > .001:
                        error = "invalid_tau_or_market_clock"
                if error: rejected[cid][error] += 1; continue
                valid[cid] += 1
                visible = {name: f[name] for name in set(candidate["required_features"]) | {"tau_s"}}
                try: side = signal_fn(candidate, visible)
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    rejected[cid]["signal_error:"+type(exc).__name__] += 1; continue
                if side is None: continue
                if side not in ("Up", "Down"): raise ValueError("invalid parent signal side")
                seen.add((cid, mid))
                anchor = raw_ask(mid, side)
                _, health = _book(books.get(mid), now, config)
                cohorts.append({"parent_candidate_id": cid, "market_id": mid, "first_signal_ms": now,
                    "side": side, "initial_direct_ask": anchor, "initial_book_warning": health,
                    "original_scale_policy_replaced": bool(candidate.get("scale_policy"))})
                for policy in names:
                    for ordinal, (weight, discount) in enumerate(POLICIES[policy]):
                        limit = None if anchor is None else float(Decimal(str(anchor))-Decimal(str(discount)))
                        plan = {"parent_candidate_id": cid, "policy": policy,
                            "ledger_id": f"{cid}::{policy}", "leg_id": f"{cid}::{policy}::L{ordinal}",
                            "market_id": mid, "parent_first_ms": now, "side": side,
                            "quantity": config.shares*weight, "limit_price": limit,
                            "deadline_ms": min(now+DEADLINE_MS, market["end_ms"]),
                            "status": "waiting", "trigger_ms": None}
                        plans.append(plan)
                        if discount == 0 or limit is None or limit <= 0:
                            yield emitted(event, plan)
                        else: waiting[mid].append(plan)
        # No fictitious extra decision at expiry is introduced. A complete
        # observed horizon establishes a missed limit; missing horizon isnull.
        for pending in waiting.values():
            for plan in pending:
                end = (dataset or {}).get("coverage_end_ms")
                covered = (_number(end) and plan["deadline_ms"] <= end and
                    _same_observed_interval(dataset, plan["parent_first_ms"], plan["deadline_ms"]))
                plan["status"] = "expired_without_send" if covered else "censored_wait_horizon"

    result = _leg_replay_events(routed(), legs, _leg_signal, config)
    by_ledger = defaultdict(list)
    for p in plans: by_ledger[p["ledger_id"]].append(p)
    for summary in result["summary"]:
        cid, policy = summary["candidate_id"].rsplit("::", 1)
        pp = by_ledger[summary["candidate_id"]]
        summary.update(parent_candidate_id=cid, policy=policy,
            parent_valid_feature_rows=valid[cid], parent_feature_rejections=dict(rejected[cid]),
            first_signal_markets=len({p["market_id"] for p in pp}), planned_legs=len(pp),
            waiting_legs_expired=sum(p["status"] == "expired_without_send" for p in pp),
            waiting_legs_censored=sum(p["status"].startswith("censored") for p in pp),
            full_window_pnl_usd=None, formal_oos_result=None,
            planned_total_shares_per_market=sum(w for w,_ in POLICIES[policy])*config.shares)
        if not summary["attempts"] and pp:
            summary["status"] = "evaluated_wait_no_trigger"
            summary["net_pnl_usd"] = 0. if all(p["status"] == "expired_without_send" for p in pp) else None
        elif not summary["attempts"]:
            summary["status"] = ("blocked_no_decision_data" if not counters["original_decisions"] else
                "blocked_no_valid_feature_rows" if not valid[cid] else "evaluated_no_signal")
            summary["net_pnl_usd"] = None
        if any(p["status"].startswith("censored") for p in pp):
            summary["status"] = "incomplete_wait_horizon"
            summary["net_pnl_usd"] = None
    result.update(parent_execution_sha256=PARENT_SHA256, patient_policy_version="patient-entry-v1",
        parent_candidate_sha256=fingerprint(candidates), policies={k: POLICIES[k] for k in names},
        parent_first_signal_cohorts=cohorts, patient_plans=plans,
        patient_diagnostics=dict(counters), policies_and_latencies_not_additive=True,
        policy_replaces_existing_scale_rules=True, full_window_pnl_usd=None, formal_oos_result=None)
    return result


# Explicit parent execution fork; helper implementations are not copied.
def _leg_replay_events(events: Iterable[dict], candidates: list[dict],
                  signal_fn: Callable[[dict, dict], str | None],
                  config: ReplayConfig | None = None) -> dict:
    """Replay each candidate/latency on its own isolated starting-cash ledger.

    All feature availability timestamps are mandatory. Invalid feature rows are
    counted as decision rejections, not evaluated with future data. Once a causal
    signal qualifies, that candidate's market is consumed even if its order fails.
    The first observed price on the selected side freezes the FAK limit.
    """
    config = config or ReplayConfig()
    leg_ids = [str(c["id"]) for c in candidates]
    ids = list(dict.fromkeys(c["_ledger_id"] for c in candidates))
    lookup = {str(c["id"]): c for c in candidates}
    if len(leg_ids) != len(set(leg_ids)):
        raise ValueError("candidate IDs must be unique")
    if any(not isinstance(c.get("required_features"), list) for c in candidates):
        raise ValueError("every candidate requires an explicit required_features list")
    books, markets, outcomes, constraint_updates = {}, {}, {}, {}
    seen = set()
    attempts = []
    queue = []
    sequence = 0
    diagnostics = Counter()
    invalid_by_candidate = defaultdict(Counter)
    valid_by_candidate = Counter()
    dataset = None
    first_decision = None
    last_recv = None
    ledgers = {}
    shared_depth_used = defaultdict(float)
    open_by_market = defaultdict(list)
    for cid in ids:
        for latency in config.latencies_ms:
            ledgers[(cid, latency)] = {
                "cash": config.initial_cash_usd, "reserved": 0.0,
                "min_available": config.initial_cash_usd, "realized_pnl": 0.0,
                "peak_realized_equity": config.initial_cash_usd, "max_drawdown": 0.0,
            }

    def schedule(at, kind, payload):
        nonlocal sequence
        sequence += 1
        heapq.heappush(queue, (at, sequence, kind, payload))

    def settle(attempt, winner, when):
        if attempt["settled"] or not attempt["filled_shares"]:
            return
        ledger = ledgers[(attempt["candidate_id"], attempt["latency_ms"])]
        payout = attempt["filled_shares"] if attempt["side"] == winner else 0.0
        pnl = payout - attempt["cost_usd"]
        ledger["cash"] += payout
        ledger["realized_pnl"] += pnl
        equity = config.initial_cash_usd + ledger["realized_pnl"]
        ledger["peak_realized_equity"] = max(ledger["peak_realized_equity"], equity)
        ledger["max_drawdown"] = max(ledger["max_drawdown"], ledger["peak_realized_equity"] - equity)
        attempt.update(settled=True, settlement_ms=when, winner=winner,
                       payout_usd=payout, net_pnl_usd=pnl,
                       cash_after_settlement=ledger["cash"], realized_equity_after_settlement=equity)

    def execute(attempt, when, horizon=None):
        ledger = ledgers[(attempt["candidate_id"], attempt["latency_ms"])]
        ledger["reserved"] -= attempt["reserved_usd_at_decision"]
        ledger["reserved"] = max(0.0, ledger["reserved"])
        # Separate recordings must not be joined by a stale-but-within-age book,
        # even when both endpoints are covered by different recording intervals.
        # Retain this first attempt as censored; never use coverage to retry it.
        if not _same_observed_interval(dataset, attempt["decision_ms"], when):
            attempt["status"] = "incomplete_match_horizon"
            attempt["coverage_rejection_reason"] = "decision_and_match_not_in_same_observed_interval"
            return
        # Later label receipts do not extend recorded order-book coverage.
        recorded_end = dataset.get("coverage_end_ms") if dataset else None
        if _number(recorded_end) and when > recorded_end:
            attempt["status"] = "incomplete_match_horizon"
            return
        if horizon is not None and when > horizon:
            attempt["status"] = "incomplete_match_horizon"
            return
        market = markets.get(attempt["market_id"])
        if market is None or when >= market["end_ms"]:
            attempt["status"] = "rejected_market_closed_at_match"
            return
        book, error = _book(books.get(attempt["market_id"]), when, config)
        if error:
            attempt["status"] = "rejected_" + error + "_at_match"
            return
        validation, constraint_error = check_constraints(
            market, books.get(attempt["market_id"]), constraint_updates.get(attempt["market_id"]),
            attempt["side"], when, attempt["requested_shares"], attempt["limit_price"],
            config.require_constraints, config.min_notional_usd)
        attempt["constraints_at_match"] = validation
        if constraint_error:
            attempt["status"] = "rejected_" + constraint_error + "_at_match"
            return
        if abs(book["fee_rate"] - attempt["fee_rate_decision"]) > EPS:
            attempt["status"] = "rejected_fee_changed_after_decision"
            return
        raw_book = books[attempt["market_id"]]
        snapshot = (raw_book.get(attempt["side"].lower()+"_recv_ms", raw_book["recv_ms"]), tuple(book[attempt["side"]]))
        remaining, notional, raw_fee = attempt["requested_shares"], 0.0, Decimal(0)
        for price, size in book[attempt["side"]]:
            if price > attempt["limit_price"] + EPS or remaining <= EPS:
                break
            depth_key = (attempt["candidate_id"], attempt["latency_ms"], attempt["market_id"], attempt["side"], snapshot, price)
            available_depth = max(0., size * config.depth_fraction-shared_depth_used[depth_key])
            quantity = min(available_depth, remaining)
            shared_depth_used[depth_key] += quantity
            notional += quantity * price
            raw_fee += _fee(quantity, price, book["fee_rate"])
            remaining -= quantity
        filled = attempt["requested_shares"] - remaining
        fees = float(_rounded_order_fee(raw_fee))
        extra_cost = filled * config.extra_cost_per_filled_share_usd
        cost = notional + fees + extra_cost
        if cost > ledger["cash"] + EPS:
            raise AssertionError("reserved order cost exceeds ledger cash")
        ledger["cash"] -= cost
        ledger["min_available"] = min(ledger["min_available"], ledger["cash"] - ledger["reserved"])
        attempt.update(status="no_fill" if filled <= EPS else ("filled_full" if remaining <= EPS else "filled_partial"),
                       filled_shares=filled, fill_vwap=notional / filled if filled > EPS else None,
                       fees_usd=fees, extra_cost_usd=extra_cost, cost_usd=cost, cash_after_match=ledger["cash"],
                       unrounded_fees_usd=float(raw_fee),
                       fee_rounding_adjustment_usd=float(_rounded_order_fee(raw_fee) - raw_fee),
                       book_recv_ms_at_match=books[attempt["market_id"]]["recv_ms"])
        if filled > EPS:
            open_by_market[attempt["market_id"]].append(attempt)
            outcome = outcomes.get(attempt["market_id"])
            if outcome and outcome["known_ms"] <= when:
                settle(attempt, outcome["winner"], when)
        else:
            attempt["net_pnl_usd"] = 0.0

    def drain(before, inclusive=False, horizon=None):
        while queue and (queue[0][0] < before or (inclusive and queue[0][0] == before)):
            when, _, kind, payload = heapq.heappop(queue)
            if kind == "match":
                execute(payload, when, horizon)
            elif horizon is None or when <= horizon:
                for attempt in open_by_market.get(payload, []):
                    settle(attempt, outcomes[payload]["winner"], when)

    def process(event):
        nonlocal dataset, first_decision
        now, kind = event["recv_ms"], event.get("type")
        diagnostics["input_" + str(kind)] += 1
        if kind == "dataset":
            if dataset is not None:
                raise ValueError("multiple dataset headers are not allowed")
            if "observed_intervals_ms" in event:
                _validate_observed_intervals(event["observed_intervals_ms"])
            dataset = event
            if event.get("book_resolution") == "decision_and_match_projection":
                supported = event.get("supported_latencies_ms")
                grid = event.get("decision_grid_ms")
                if (not isinstance(supported, list) or
                        any(type(v) is not int or v <= 0 for v in supported) or
                        not set(config.latencies_ms).issubset(supported)):
                    raise ValueError("projection does not support requested total latency")
                if type(grid) is not int or grid <= 0:
                    raise ValueError("projection requires fixed positive decision_grid_ms")
            return
        market_id = event.get("market_id")
        if not isinstance(market_id, str) or not market_id:
            diagnostics["rejected_missing_market_id"] += 1
            return
        if kind == "market":
            start, end = event.get("start_ms"), event.get("end_ms")
            if not _number(start) or not _number(end) or end - start != 300000 or event.get("label_source") not in OFFICIAL_SOURCES:
                diagnostics["rejected_invalid_market_metadata"] += 1
                markets.pop(market_id, None)
                return
            previous = markets.get(market_id)
            if previous and any(previous[key] != event[key] for key in ("start_ms", "end_ms", "label_source")):
                raise ValueError("market settlement definition changed during replay")
            markets[market_id] = event
            return
        if kind == "constraint":
            previous = constraint_updates.get(market_id, {})
            merged = dict(previous)
            for name in ("min_order_size", "up_tick_size", "down_tick_size"):
                if name in event:
                    merged[name] = event[name]
                    merged[name + "_available_ms"] = event.get(name + "_available_ms", now)
                    merged[name + "_effective_ms"] = event.get(name + "_effective_ms", merged[name + "_available_ms"])
                    merged[name + "_source"] = event.get(name + "_source", event.get("constraints_source"))
            merged["recv_ms"] = now
            constraint_updates[market_id] = merged
            return
        if kind == "book":
            if (dataset and dataset.get("book_resolution") == "decision_and_match_projection"
                    and any(name not in event for name in ("up_recv_ms", "down_recv_ms"))):
                raise ValueError("projected books must preserve both original side receipt timestamps")
            # Invalid new books replace old books too. Never resurrect stale depth.
            books[market_id] = event
            return
        if kind == "outcome":
            market = markets.get(market_id)
            resolved = event.get("resolved_ms")
            if (market is None or event.get("winner") not in ("Up", "Down")
                    or event.get("source") not in OFFICIAL_SOURCES
                    or event.get("source") != market["label_source"]
                    or not _number(resolved) or resolved < market["end_ms"]):
                diagnostics["rejected_invalid_or_unofficial_outcome"] += 1
                return
            prior = outcomes.get(market_id)
            if prior:
                if prior["winner"] != event["winner"]:
                    raise ValueError("conflicting official outcomes")
                diagnostics["duplicate_outcome"] += 1
                return
            known = max(now, resolved)
            outcomes[market_id] = {"winner": event["winner"], "known_ms": known}
            if known == now:
                for attempt in open_by_market.get(market_id, []):
                    settle(attempt, event["winner"], known)
            else:
                schedule(known, "settle", market_id)
            return
        if kind != "decision":
            diagnostics["rejected_unknown_record_type"] += 1
            return
        if (dataset and dataset.get("book_resolution") == "decision_and_match_projection"
                and abs(now / dataset["decision_grid_ms"] - round(now / dataset["decision_grid_ms"])) > 1e-6):
            raise ValueError("decision outside registered projection grid")
        first_decision = now if first_decision is None else min(first_decision, now)
        features, available = event.get("features"), event.get("feature_available_ms")
        market = markets.get(market_id)
        for candidate in ([lookup[event["patient_dispatch_id"]]] if event.get("patient_dispatch_id") in lookup else []):
            cid = str(candidate["id"])
            ledger_id = candidate["_ledger_id"]
            if (cid, market_id) in seen:
                continue
            error = None
            if market is None or not market["start_ms"] <= now < market["end_ms"]:
                error = "missing_market_or_outside_window"
            elif not isinstance(features, dict) or not isinstance(available, dict):
                error = "missing_feature_maps"
            else:
                for name in set(candidate["required_features"]) | {"tau_s"}:
                    if not _number(features.get(name)):
                        error = "missing_or_invalid_feature:" + name
                        break
                    if not _number(available.get(name)):
                        error = "missing_feature_availability:" + name
                        break
                    if available[name] > now:
                        error = "future_feature:" + name
                        break
                if error is None and (not 0 <= features["tau_s"] <= 300 or
                        abs(features["tau_s"] - (market["end_ms"] - now) / 1000) > 0.001):
                    error = "invalid_tau_or_market_clock"
            if error:
                invalid_by_candidate[ledger_id][error] += 1
                continue
            valid_by_candidate[ledger_id] += 1
            try:
                # Do not expose an unchecked extra column to the signal function.
                visible = {name: features[name] for name in set(candidate["required_features"]) | {"tau_s"}}
                side = signal_fn(candidate, visible)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                invalid_by_candidate[ledger_id]["signal_error:" + type(exc).__name__] += 1
                continue
            if side is None:
                continue
            if side not in ("Up", "Down"):
                raise ValueError(f"invalid signal return for {cid}: {side!r}")
            seen.add((cid, market_id))
            book, book_error = _book(books.get(market_id), now, config)
            for latency in config.latencies_ms:
                ledger = ledgers[(ledger_id, latency)]
                attempt = {"candidate_id": ledger_id, "leg_id": cid,
                    "parent_candidate_id": candidate["_parent_id"], "policy": candidate["_policy"],
                    "parent_first_signal_ms": event["patient_parent_first_ms"], "latency_ms": latency, "market_id": market_id,
                    "decision_ms": now, "match_ms": now + latency, "side": side,
                    "requested_shares": candidate["_patient_quantity"], "limit_price": None,
                    "submitted": False,
                    "fee_rate_decision": None, "status": "scheduled", "filled_shares": 0.0,
                    "fill_vwap": None, "fees_usd": 0.0, "unrounded_fees_usd": 0.0,
                    "fee_rounding_adjustment_usd": 0.0, "extra_cost_usd": 0.0, "cost_usd": 0.0,
                    "constraints_at_decision": None, "constraints_at_match": None,
                    "settled": False, "settlement_ms": None, "winner": None,
                    "payout_usd": None, "net_pnl_usd": None,
                    "cash_before_decision": ledger["cash"], "cash_after_match": None,
                    "cash_after_settlement": None, "reserved_usd_at_decision": 0.0,
                    "reserved_fee_usd_at_decision": 0.0}
                attempts.append(attempt)
                if book_error:
                    attempt["status"] = "rejected_" + book_error + "_at_decision"
                    continue
                if not book[side]:
                    attempt["status"] = "rejected_no_depth_at_decision"
                    continue
                limit = features["patient_limit"]
                if not 0 < limit < 1:
                    attempt["status"] = "rejected_invalid_precommitted_limit"
                    continue
                fee = book["fee_rate"]
                attempt.update(limit_price=limit, fee_rate_decision=fee)
                validation, constraint_error = check_constraints(
                    market, books.get(market_id), constraint_updates.get(market_id),
                    side, now, candidate["_patient_quantity"], limit,
                    config.require_constraints, config.min_notional_usd)
                attempt["constraints_at_decision"] = validation
                if constraint_error:
                    attempt["status"] = "rejected_" + constraint_error + "_at_decision"
                    continue
                reserved_fee = float(_fee(candidate["_patient_quantity"], limit, fee).quantize(FEE_QUANTUM, rounding=ROUND_CEILING))
                attempt["reserved_fee_usd_at_decision"] = reserved_fee
                reservation = (candidate["_patient_quantity"] * limit + reserved_fee
                               + candidate["_patient_quantity"] * config.extra_cost_per_filled_share_usd)
                if ledger["cash"] - ledger["reserved"] + EPS < reservation:
                    attempt["status"] = "rejected_insufficient_available_cash"
                    continue
                attempt["reserved_usd_at_decision"] = reservation
                attempt["submitted"] = True
                ledger["reserved"] += reservation
                ledger["min_available"] = min(ledger["min_available"], ledger["cash"] - ledger["reserved"])
                schedule(now + latency, "match", attempt)

    # Buffer only one equal-timestamp group; matches see all events received by
    # their due timestamp. Decisions remain in observed receipt order within it.
    group = []
    for event in events:
        now = event.get("recv_ms")
        if not _number(now):
            raise ValueError("every record must include finite local recv_ms")
        if last_recv is not None and now < last_recv:
            raise ValueError("input is not sorted by local receipt time")
        if last_recv is not None and now != last_recv:
            drain(last_recv)
            for grouped_event in group:
                process(grouped_event)
            drain(last_recv, inclusive=True)
            group = []
            drain(now)
        group.append(event)
        last_recv = now
    if group:
        drain(last_recv)
        for event in group:
            process(event)
        drain(last_recv, inclusive=True)
    horizon = last_recv if last_recv is not None else -1
    # A declared endpoint may cover quiet periods. execute() separately ensures
    # later outcome receipts never extend the book-recording coverage endpoint.
    if dataset and _number(dataset.get("coverage_end_ms")):
        horizon = max(horizon, dataset["coverage_end_ms"])
    drain(math.inf, horizon=horizon)
    valid_provenance = bool(dataset and dataset.get("dataset_id") and isinstance(dataset.get("provenance"), dict)
                            and dataset["provenance"].get("source"))
    try:
        signal_path = inspect.getsourcefile(signal_fn)
        actual_signal_hash = hashlib.sha256(Path(signal_path).read_bytes()).hexdigest() if signal_path else None
    except (TypeError, OSError):
        actual_signal_hash = None
    split = _split_validation(dataset, candidates, config, first_decision, actual_signal_hash)
    by_key = defaultdict(list)
    for attempt in attempts:
        by_key[(attempt["candidate_id"], attempt["latency_ms"])].append(attempt)
    summary = []
    for cid in ids:
        for latency in config.latencies_ms:
            rows = by_key[(cid, latency)]
            ledger = ledgers[(cid, latency)]
            statuses = Counter(row["status"] for row in rows)
            filled = [row for row in rows if row["filled_shares"] > EPS]
            settled = [row for row in filled if row["settled"]]
            qty = sum(row["filled_shares"] for row in filled)
            incomplete = any(row["status"] == "incomplete_match_horizon" for row in rows) or len(filled) != len(settled)
            missing_execution_inputs = any(any(marker in row["status"] for marker in (
                "missing_book", "missing_or_invalid_fee", "missing_asks", "invalid_asks",
                "fee_changed_after_decision", "invalid_min_order_size_metadata",
                "missing_required_constraints", "invalid_constraint_timestamp", "invalid_up_tick_size_metadata",
                "invalid_down_tick_size_metadata")) for row in rows)
            if not valid_provenance:
                status, pnl = "blocked_missing_data_provenance", None
            elif not diagnostics["input_decision"]:
                status, pnl = "blocked_no_decision_data", None
            elif not valid_by_candidate[cid]:
                status, pnl = "blocked_no_valid_feature_rows", None
            elif not rows:
                status, pnl = "evaluated_no_signal", None
            elif any("rejected_below_market_min_order_size" in name for name in statuses):
                status, pnl = "blocked_below_market_min_order_size", None
            elif missing_execution_inputs:
                status, pnl = "incomplete_missing_execution_inputs", None
            elif incomplete:
                status, pnl = "incomplete_unresolved_or_truncated", None
            elif not filled:
                status, pnl = "evaluated_no_fills", 0.0
            else:
                status, pnl = "simulated_complete", ledger["realized_pnl"]
            summary.append({"candidate_id": cid, "latency_ms": latency, "status": status,
                "effective_split": split["effective_split"], "attempts": len(rows),
                "requested_shares": config.shares,
                "submitted_orders": sum(r["submitted"] for r in rows),
                "markets": len({r["market_id"] for r in rows}),
                "decision_utc_days": len({int(r["decision_ms"] // 86400000) for r in rows}),
                "valid_feature_rows": valid_by_candidate[cid],
                "invalid_decision_reasons": dict(invalid_by_candidate[cid]),
                "status_counts": dict(statuses), "full_fills": statuses["filled_full"],
                "missing_execution_inputs": missing_execution_inputs,
                "partial_fills": statuses["filled_partial"], "filled_shares": qty,
                "settled_positions": len(settled), "unresolved_positions": len(filled) - len(settled),
                "net_pnl_usd": pnl, "realized_pnl_usd": ledger["realized_pnl"] if rows and valid_provenance else None,
                "net_pnl_per_filled_share_usd": pnl / qty if pnl is not None and qty else None,
                "fees_usd": sum(r["fees_usd"] for r in rows),
                "unrounded_fees_usd": sum(r["unrounded_fees_usd"] for r in rows),
                "fee_rounding_adjustment_usd": sum(r["fee_rounding_adjustment_usd"] for r in rows),
                "extra_cost_usd": sum(r["extra_cost_usd"] for r in rows),
                "constraints_validated_attempts": sum(bool(r["constraints_at_decision"] and r["constraints_at_decision"]["validated"]) for r in rows),
                "filled_positions_with_validated_constraints": sum(bool(r["constraints_at_decision"] and r["constraints_at_decision"]["validated"] and r["constraints_at_match"] and r["constraints_at_match"]["validated"]) for r in filled),
                "initial_cash_usd": config.initial_cash_usd,
                "ending_cash_usd": ledger["cash"], "min_available_cash_usd": ledger["min_available"],
                "ending_reserved_usd": ledger["reserved"],
                "max_realized_drawdown_usd": ledger["max_drawdown"],
                "drawdown_basis": "realized_only_no_mark_to_market", "confirmed_edge": False})
    return {"schema_version": "patient_entry_replay_v1_development", "dataset": dataset,
        "config": asdict(config), "candidate_sha256": fingerprint(candidates),
        "execution_sha256": fingerprint(asdict(config)), "split_validation": split,
        "replay_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "constraints_code_sha256": hashlib.sha256(Path(mine500_constraints.__file__).read_bytes()).hexdigest(),
        "simulation_only": True, "fill_model": "displayed_depth_fraction_partial_FAK_sensitivity_not_actual_fills",
        "research_status": "development_only_new_execution_version",
        "constraints_mode": "required" if config.require_constraints else "upper_bound_missing_constraints_not_validated",
        "stress_assumptions": {"depth_fraction": config.depth_fraction,
            "min_notional_usd": config.min_notional_usd,
            "extra_cost_per_filled_share_usd": config.extra_cost_per_filled_share_usd,
            "interpretation": "Explicit assumptions, not observed competition, venue minimum-notional, or actual execution costs."},
        "fee_calculation": "Decimal sum(quantity * fee_rate * fill_price * (1-fill_price)); aggregate-order proxy: raw fee below 0.00001 becomes zero, otherwise rounded to 5 decimal places with assumed HALF_UP ties; no rebates",
        "fee_reservation": "Unrounded full-order fee at fixed limit rounded upward to 5 decimal places",
        "fee_rounding_limitation": "Official precision is 5 decimal places and fees smaller than 0.00001 round to zero. Aggregate-order scope and HALF_UP tie handling are assumptions. Actual maker-trade partitions and venue rounding granularity are unavailable; this is not an exact exchange bill.",
        "fee_precision_reference": {"url": "https://docs.polymarket.com/trading/fees", "checked_utc_date": "2026-10-11"},
        "latency_clock": "local_decision_to_match_total_including_venue_wait",
        "recording_coverage_policy": "decision_and_match_must_share_one_inclusive_observed_interval_when_supplied",
        "capital_scope": "each_parent_policy_latency_has_independent_cash_legs_share_cash_and_snapshot_depth_do_not_sum",
        "diagnostics": dict(diagnostics), "summary": summary, "attempts": attempts}

