"""Offline fixed-size MIX execution ledger; no training, networking or orders.

The input is a receipt-clock book tape and a preselected frozen signal list.
Every signal is retained. A new model or first-signal-per-market rule is never
introduced here. Visible depth is a capacity upper bound, not a fill claim.
Missing official outcome receipt times are explicitly imputed, never hidden.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, OrderedDict
import heapq
import itertools
import math


VERSION = "historical100-receipt-book-ledger-v2"
HOLD_MS = {"R1": None, "R2": 60_000., "R3": 30_000.}
EPS = 1e-8


def side_name(side):
    if side in ("up", "Up", 1, "1"):
        return "up"
    if side in ("down", "Down", -1, "-1"):
        return "down"
    raise ValueError("unknown side: " + repr(side))


def finite(value):
    return isinstance(value, (float, int)) and math.isfinite(value)


def fee(quantity, price, rate):
    return quantity * rate * price * (1. - price)


def replay(markets, books, signals, *, rule_id, latency_ms,
           initial_cash=1000., shares=100., outcome_delay_ms=300_000., max_age_ms=1000.,
           entry_fill_policy="partial_fak"):
    """Replay one rule/latency in one continuous cash account across all days.

    ``markets[id]``: end_ms, outcome(up/down), outcome_known_ms(optional),
    fee_rate(required), fee_source(optional), min_order_size(default 5).
    ``books[id]``: snapshots containing recv_ms, active, halted, changed_ms,
    up/down dictionaries with bids/asks [[price, quantity], ...]. Token-level
    active/halted/changed_ms override market-level attributes.
    ``signals``: signal_id, rule_id, market_id, decision_ms, side. All frozen
    selected rows must be supplied, including those whose later fills fail.

    Outcome payout at outcome_known_ms is itself an availability assumption:
    actual redemption evidence is not present in this historical simulator.
    R2/R3 wait for their first causally usable exit-decision bid after 60/30 s;
    a submitted exit is FAK once, residuals held to official settlement. At
    equal timestamps outcome/matches precede decisions; Up precedes Down.
    """
    if rule_id not in HOLD_MS or not finite(latency_ms) or latency_ms < 0:
        raise ValueError("invalid rule or latency")
    if initial_cash <= 0 or shares <= 0 or outcome_delay_ms < 0:
        raise ValueError("invalid capital, quantity or outcome delay")
    if entry_fill_policy not in {"partial_fak", "full100_or_zero"}:
        raise ValueError("invalid entry fill policy")
    markets = {str(k): dict(v) for k, v in markets.items()}
    # books may be a disk-backed Mapping; never materialize all daily tapes.
    tape_cache = OrderedDict()
    def get_tape(mid):
        if mid in tape_cache:
            tape_cache.move_to_end(mid)
            return tape_cache[mid]
        try:
            rows = books[mid]
        except KeyError:
            rows = []
        ordered = sorted(rows, key=lambda row: row["recv_ms"])
        if any(not finite(row["recv_ms"]) for row in ordered):
            raise ValueError("invalid snapshot receipt time")
        stamps = [row["recv_ms"] for row in ordered]
        # Repeated 100 ms copies of an unchanged price/quantity do not replenish
        # depth already consumed by simulated orders. A changed displayed level
        # starts a new generation. This remains an upper bound, not queue data.
        previous, generations, generation_rows = {}, {}, []
        for idx, row in enumerate(ordered):
            current = {}
            for side in ("up", "down"):
                for name in ("bids", "asks"):
                    for level in row.get(side, {}).get(name, []):
                        if len(level) == 2 and finite(level[0]) and finite(level[1]):
                            key = (side, name, float(level[0]))
                            current[key] = current.get(key, 0.) + float(level[1])
            for key, qty in current.items():
                if key not in previous or qty != previous[key]:
                    generations[key] = idx
            generation_rows.append({key: generations[key] for key in current})
            previous = current
        result = ordered, stamps, generation_rows
        tape_cache[mid] = result
        if len(tape_cache) > 8:
            tape_cache.popitem(last=False)
        return result
    selected = [dict(row) for row in signals if row["rule_id"] == rule_id]
    for row in selected:
        row["market_id"] = str(row["market_id"])
        row["side"] = side_name(row["side"])
        if not finite(row["decision_ms"]):
            raise ValueError("invalid signal decision time")
        if row["market_id"] not in markets:
            raise ValueError("missing market for selected signal")
    selected.sort(key=lambda row: (row["decision_ms"], row["market_id"],
                                  0 if row["side"] == "up" else 1,
                                  str(row["signal_id"])))
    ids = [str(row["signal_id"]) for row in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate selected signal id")
    used_markets = {row["market_id"] for row in selected}
    for mid in used_markets:
        m = markets[mid]
        if not finite(m.get("end_ms")) or not finite(m.get("fee_rate")) or not 0 <= m["fee_rate"] <= 1:
            raise ValueError("missing/invalid market end or fee rate")
        if m.get("outcome") is not None:
            m["outcome"] = side_name(m["outcome"])
            known = m.get("outcome_known_ms")
            m["outcome_time_imputed"] = known is None
            if known is None:
                known = m["end_ms"] + outcome_delay_ms
            if not finite(known) or known < m["end_ms"]:
                raise ValueError("invalid official outcome availability time")
            m["payout_ms"] = known

    queue, seq = [], itertools.count()
    def schedule(t, priority, kind, data):
        heapq.heappush(queue, (float(t), priority, next(seq), kind, data))
    for mid in sorted(used_markets):
        if markets[mid].get("outcome") is not None:
            schedule(markets[mid]["payout_ms"], 0, "settle", mid)
    for row in selected:
        schedule(row["decision_ms"], 3, "entry_decision", row)

    def quote(mid, side, t, require_change=True):
        tape, stamps, generation_rows = get_tape(mid)
        idx = bisect_right(stamps, t) - 1
        if idx < 0:
            return None, "missing_book"
        snap = tape[idx]
        tok = snap.get(side, {})
        if t - snap["recv_ms"] > max_age_ms:
            return None, "stale_snapshot"
        if tok.get("active", snap.get("active")) is not True:
            return None, "inactive_or_unknown"
        if tok.get("halted", snap.get("halted")) is not False:
            return None, "halted_or_unknown"
        changed = tok.get("changed_ms", snap.get("changed_ms"))
        if require_change and (not finite(changed) or changed > t or t - changed > max_age_ms):
            return None, "stale_or_unknown_change"
        levels = {}
        for name in ("bids", "asks"):
            raw = tok.get(name, [])
            if any(len(level) != 2 or not finite(level[0]) or not finite(level[1]) or
                   not 0 < level[0] < 1 or level[1] < 0 for level in raw):
                return None, "invalid_level"
            merged = {}
            for p, q in raw:
                merged[float(p)] = merged.get(float(p), 0.) + float(q)
            levels[name] = sorted(((p, q) for p, q in merged.items() if q > 0),
                                  reverse=name == "bids")
        if not levels["bids"] or not levels["asks"]:
            return None, "missing_two_sided_book"
        if levels["bids"][0][0] >= levels["asks"][0][0]:
            return None, "crossed_book"
        return (idx, snap, levels, generation_rows[idx]), None

    # Multiple orders cannot each consume the same displayed snapshot depth.
    consumed = {}
    def sweep(mid, side, qinfo, direction, limit, requested, *, require_full=False):
        idx, snap, levels, generations = qinfo
        fills, left, planned = [], requested, []
        name = "asks" if direction == "buy" else "bids"
        for price, quantity in levels[name]:
            if (direction == "buy" and price > limit + EPS) or (direction == "sell" and price < limit - EPS):
                break
            generation = generations[(side, name, price)]
            key = (mid, side, generation, direction, price)
            remaining = max(0., quantity - consumed.get(key, 0.))
            qty = min(left, remaining)
            if qty > EPS:
                fills.append([price, qty])
                planned.append((key, qty))
                left -= qty
            if left <= EPS:
                break
        if require_full and left > EPS:
            return []
        for key, qty in planned:
            consumed[key] = consumed.get(key, 0.) + qty
        return fills

    attempts, exits, cash_events = [], [], []
    positions = {}
    cash, reserved, realized = float(initial_cash), 0., 0.
    min_available, peak_reserved, peak_cost = cash, 0., 0.
    peak_realized_equity, max_drawdown = cash, 0.
    max_open_positions = 0

    def audit(t, event, aid=None):
        nonlocal min_available, peak_reserved, peak_cost, peak_realized_equity, max_drawdown, max_open_positions
        available = cash - reserved
        if available < -EPS or reserved < -EPS:
            raise AssertionError("cash overdraft or negative reservation")
        outstanding_cost = math.fsum(p["remaining_cost_usd"] for p in positions.values())
        min_available = min(min_available, available)
        peak_reserved = max(peak_reserved, reserved)
        peak_cost = max(peak_cost, outstanding_cost + reserved)
        max_open_positions = max(max_open_positions, len(positions))
        peak_realized_equity = max(peak_realized_equity, initial_cash + realized)
        max_drawdown = max(max_drawdown, peak_realized_equity - initial_cash - realized)
        cash_events.append(dict(time_ms=t, event=event, attempt_id=aid,
            cash_usd=cash, reserved_usd=max(0., reserved), available_cash_usd=available,
            open_cost_usd=outstanding_cost, realized_pnl_usd=realized,
            open_positions=len(positions)))

    while queue:
        t, _, _, kind, data = heapq.heappop(queue)
        if kind == "entry_decision":
            sig = data
            mid, side = sig["market_id"], sig["side"]
            m = markets[mid]
            a = dict(attempt_id=str(sig["signal_id"]), market_id=mid, rule_id=rule_id,
                     decision_ms=t, side=side, latency_ms=latency_ms,
                     requested_shares=float(shares), match_ms=t + latency_ms,
                     limit_price=None, reservation_usd=0., filled_shares=0.,
                     entry_notional_usd=0., entry_fee_usd=0., cost_usd=0.,
                     sold_shares=0., exit_gross_usd=0., exit_fee_usd=0.,
                     settlement_shares=0., payout_usd=0., pnl_usd=0.,
                     fee_rate=m["fee_rate"], fee_source=m.get("fee_source", "unspecified"),
                     outcome_time_imputed=m.get("outcome_time_imputed"),
                     actual_cash_reuse_validated=False)
            if "asklimit" in sig:
                a["frozen_signal_asklimit"] = sig["asklimit"]
            if "signal_status" in sig:
                a["frozen_signal_status"] = sig["signal_status"]
            attempts.append(a)
            qinfo, reason = quote(mid, side, t, require_change=False)
            if t >= m["end_ms"]:
                a["status"] = "rejected_after_end"
            elif shares < m.get("min_order_size", 5.):
                a["status"] = "rejected_minimum_order"
            elif sig.get("signal_status", "ready") != "ready" or ("asklimit" in sig and not finite(sig["asklimit"])):
                a["status"] = "rejected_invalid_frozen_decision_limit"
            elif qinfo is None:
                a["status"] = "rejected_decision_" + reason
            elif "asklimit" in sig and abs(qinfo[2]["asks"][0][0] - sig["asklimit"]) > 1e-9:
                a["status"] = "rejected_frozen_decision_limit_mismatch"
                a["observed_decision_ask"] = qinfo[2]["asks"][0][0]
            else:
                limit = qinfo[2]["asks"][0][0]
                a["limit_price"] = limit
                a["decision_book_recv_ms"] = qinfo[1]["recv_ms"]
                a["reservation_usd"] = shares * limit + fee(shares, limit, m["fee_rate"])
                if a["reservation_usd"] > cash - reserved + EPS:
                    a["status"] = "rejected_insufficient_cash"
                else:
                    a["status"] = "pending_match"
                    reserved += a["reservation_usd"]
                    schedule(a["match_ms"], 1, "entry_match", a)
            audit(t, a["status"], a["attempt_id"])
        elif kind == "entry_match":
            a = data
            mid, side = a["market_id"], a["side"]
            m = markets[mid]
            reserved -= a["reservation_usd"]
            qinfo, reason = quote(mid, side, t)
            if t >= m["end_ms"]:
                a["status"] = "rejected_match_after_end"
            elif qinfo is None:
                a["status"] = "rejected_match_" + reason
            else:
                require_full = entry_fill_policy == "full100_or_zero"
                fills = sweep(mid, side, qinfo, "buy", a["limit_price"], shares,
                              require_full=require_full)
                a["entry_levels"] = fills
                a["match_book_recv_ms"] = qinfo[1]["recv_ms"]
                qty = math.fsum(q for p, q in fills)
                a["filled_shares"] = qty
                a["entry_notional_usd"] = math.fsum(p * q for p, q in fills)
                a["entry_fee_usd"] = math.fsum(fee(q, p, m["fee_rate"]) for p, q in fills)
                a["cost_usd"] = a["entry_notional_usd"] + a["entry_fee_usd"]
                if a["cost_usd"] > a["reservation_usd"] + EPS:
                    raise AssertionError("fixed-limit fill exceeded reserved cash")
                cash -= a["cost_usd"]
                a["status"] = ("full_fill" if qty >= shares - EPS else
                               "partial_fill" if qty > EPS else
                               "rejected_full100_depth_or_limit" if require_full else
                               "no_fill_limit_or_depth")
                if qty > EPS:
                    positions[a["attempt_id"]] = dict(attempt=a, remaining_shares=qty,
                                                     remaining_cost_usd=a["cost_usd"])
                    hold = HOLD_MS[rule_id]
                    if hold is not None and t + hold < m["end_ms"]:
                        schedule(t + hold, 2, "exit_decision", a["attempt_id"])
            audit(t, a["status"], a["attempt_id"])
        elif kind == "exit_decision":
            pos = positions.get(data)
            if not pos:
                continue
            a = pos["attempt"]
            mid, side = a["market_id"], a["side"]
            m = markets[mid]
            if t >= m["end_ms"]:
                continue
            if pos["remaining_shares"] < m.get("min_order_size", 5.) - EPS:
                exits.append(dict(attempt_id=data, market_id=mid, side=side,
                    decision_ms=t, match_ms=None, limit_price=None,
                    requested_shares=pos["remaining_shares"], filled_shares=0.,
                    gross_usd=0., fee_usd=0.,
                    status="not_submitted_below_minimum_residual_to_settle"))
                continue
            qinfo, reason = quote(mid, side, t)
            if qinfo is None:
                stamps = get_tape(mid)[1]
                nxt = bisect_right(stamps, t)
                a["exit_wait_events"] = a.get("exit_wait_events", 0) + 1
                a.setdefault("exit_first_unusable_reason", reason)
                if nxt < len(stamps) and stamps[nxt] < m["end_ms"]:
                    schedule(stamps[nxt], 2, "exit_decision", data)
                continue
            x = dict(attempt_id=data, market_id=mid, side=side, decision_ms=t,
                     match_ms=t + latency_ms, limit_price=qinfo[2]["bids"][0][0],
                     requested_shares=pos["remaining_shares"], filled_shares=0.,
                     gross_usd=0., fee_usd=0., decision_book_recv_ms=qinfo[1]["recv_ms"])
            exits.append(x)
            schedule(x["match_ms"], 1, "exit_match", x)
        elif kind == "exit_match":
            x = data
            pos = positions.get(x["attempt_id"])
            if not pos:
                x["status"] = "already_settled"
                continue
            a = pos["attempt"]
            mid, side = a["market_id"], a["side"]
            m = markets[mid]
            qinfo, reason = quote(mid, side, t)
            if t >= m["end_ms"]:
                x["status"] = "rejected_after_end_residual_to_settle"
            elif qinfo is None:
                x["status"] = "rejected_" + reason + "_residual_to_settle"
            else:
                fills = sweep(mid, side, qinfo, "sell", x["limit_price"], pos["remaining_shares"])
                qty = math.fsum(q for p, q in fills)
                x.update(filled_shares=qty, levels=fills, match_book_recv_ms=qinfo[1]["recv_ms"],
                         gross_usd=math.fsum(p * q for p, q in fills),
                         fee_usd=math.fsum(fee(q, p, m["fee_rate"]) for p, q in fills))
                x["status"] = "full_fill" if qty >= x["requested_shares"] - EPS else "partial_fill_residual_to_settle" if qty > EPS else "no_fill_residual_to_settle"
                allocated_cost = pos["remaining_cost_usd"] * qty / pos["remaining_shares"]
                proceeds = x["gross_usd"] - x["fee_usd"]
                cash += proceeds
                realized += proceeds - allocated_cost
                pos["remaining_cost_usd"] -= allocated_cost
                pos["remaining_shares"] -= qty
                a["sold_shares"] += qty
                a["exit_gross_usd"] += x["gross_usd"]
                a["exit_fee_usd"] += x["fee_usd"]
                a["pnl_usd"] += proceeds - allocated_cost
                if pos["remaining_shares"] <= EPS:
                    del positions[a["attempt_id"]]
                    a["fully_realized"] = True
            audit(t, "exit_" + x["status"], a["attempt_id"])
        elif kind == "settle":
            mid = data
            m = markets[mid]
            for aid in list(positions):
                pos = positions[aid]
                a = pos["attempt"]
                if a["market_id"] != mid:
                    continue
                qty = pos["remaining_shares"]
                payout = qty if a["side"] == m["outcome"] else 0.
                cash += payout
                realized += payout - pos["remaining_cost_usd"]
                a["settlement_shares"] = qty
                a["payout_usd"] = payout
                a["settlement_ms"] = t
                a["pnl_usd"] += payout - pos["remaining_cost_usd"]
                a["fully_realized"] = True
                del positions[aid]
                audit(t, "official_outcome_assumed_cash_release", aid)

    total_cost = math.fsum(a["cost_usd"] for a in attempts)
    gross_exit = math.fsum(x["gross_usd"] for x in exits)
    exit_fees = math.fsum(x["fee_usd"] for x in exits)
    payout = math.fsum(a["payout_usd"] for a in attempts)
    independent_cash = initial_cash - total_cost + gross_exit - exit_fees + payout
    if abs(cash - independent_cash) > 1e-6 or abs(reserved) > EPS:
        raise AssertionError("cash ledger reconciliation failure")
    filled = [a for a in attempts if a["filled_shares"] > EPS]
    total_shares = math.fsum(a["filled_shares"] for a in filled)
    summary = dict(rule_id=rule_id, latency_ms=latency_ms,
        signal_attempts=len(attempts), markets=len(used_markets),
        filled_orders=len(filled), full_fills=sum(a["status"] == "full_fill" for a in attempts),
        partial_fills=sum(a["status"] == "partial_fill" for a in attempts),
        filled_shares=total_shares, status_counts=dict(Counter(a["status"] for a in attempts)),
        cash_rejections=sum(a["status"] == "rejected_insufficient_cash" for a in attempts),
        net_pnl_usd=realized if not positions else None, realized_pnl_usd=realized,
        ending_cash_usd=cash, remaining_cost_usd=math.fsum(p["remaining_cost_usd"] for p in positions.values()),
        unresolved_positions=len(positions), min_available_cash_usd=min_available,
        max_reserved_cash_usd=peak_reserved, max_capital_at_cost_including_reserved_usd=peak_cost,
        max_concurrent_positions=max_open_positions, max_realized_only_drawdown_usd=max_drawdown,
        entry_fee_usd=math.fsum(a["entry_fee_usd"] for a in attempts), exit_fee_usd=exit_fees,
        net_per_filled_share_usd=realized / total_shares if total_shares and not positions else None,
        outcome_time_imputed_markets=sum(markets[mid].get("outcome_time_imputed", False) for mid in used_markets),
        actual_cash_reuse_validated=False, actual_fills_validated=False,
        formal_new_oos_result=None, interpretation="historical frozen-rule execution sensitivity; visible-depth capacity upper bound")
    return dict(config=dict(version=VERSION, rule_id=rule_id, latency_ms=latency_ms,
                            initial_cash_usd=initial_cash, requested_shares=shares,
                            imputed_outcome_delay_ms=outcome_delay_ms,
                            max_age_ms=max_age_ms, total_latency_includes_venue_wait=True,
                            depth_replenishment_assumption="only an observed change of quantity at a price level replenishes that level; unchanged snapshot repeats do not",
                            cash_release_assumption="official outcome receipt or explicit end-time imputation; redemption not observed",
                            rule_signals_unchanged=True, entry_fill_policy=entry_fill_policy,
                            fee_rounding="unrounded formula; per-market coefficient"),
                attempts=attempts, exits=exits, cash_events=cash_events,
                summary=summary)
