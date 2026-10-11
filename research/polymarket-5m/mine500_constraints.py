"""Causal per-market order constraints for development replay only.

An absent value is never guessed from another token or from a later snapshot.
Sources describe recorded provenance, not independently verified authenticity.
"""
from __future__ import annotations

import math

OFFICIAL_CONSTRAINT_SOURCES = frozenset({"polymarket", "gamma", "clob"})


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_constraints(market, book, updates, side, now, shares, limit,
                      require_constraints=False, min_notional_usd=0.0):
    """Return (audit, rejection_reason); missing provenance is not validation.

    Each field can be supplied on a market, book, or constraint event. Its
    *_available_ms defaults to that event's local recv_ms. Its *_source defaults
    to constraints_source. The latest causal field wins; future fields are
    ignored rather than backfilled. Explicit invalid values fail closed.
    """
    names = ("min_order_size", side.lower() + "_tick_size")
    audit = {"validated": False, "missing": [], "values": {}, "provenance": {},
             "mode": "required" if require_constraints else "upper_bound",
             "min_notional_usd_assumption": min_notional_usd}
    for name in names:
        choices = []
        for index, record in enumerate((market, book, updates)):
            if not record or name not in record:
                continue
            available = record.get(name + "_available_ms", record.get("recv_ms"))
            effective = record.get(name + "_effective_ms", available)
            if not _number(available) or not _number(effective):
                return audit, "invalid_constraint_timestamp"
            if available > now or effective > now:
                continue
            choices.append((available, index, record, effective))
        if not choices:
            audit["missing"].append(name)
            continue
        available, _, record, effective = max(choices, key=lambda x: (x[0], x[1]))
        value = record[name]
        if not _number(value) or value <= 0 or (name.endswith("tick_size") and value >= 1):
            return audit, "invalid_" + name + "_metadata"
        source = record.get(name + "_source", record.get("constraints_source"))
        audit["values"][name] = value
        audit["provenance"][name] = {"available_ms": available, "effective_ms": effective, "source": source}
        if source not in OFFICIAL_CONSTRAINT_SOURCES:
            audit["missing"].append(name + "_official_provenance")
    if require_constraints and audit["missing"]:
        return audit, "missing_required_constraints"
    minimum = audit["values"].get("min_order_size")
    if minimum is not None and shares + 1e-9 < minimum:
        return audit, "below_market_min_order_size"
    tick = audit["values"].get(side.lower() + "_tick_size")
    if tick is not None:
        units = limit / tick
        if abs(units - round(units)) > 1e-7:
            return audit, "limit_not_on_token_tick"
    if shares * limit + 1e-9 < min_notional_usd:
        return audit, "below_assumed_min_notional"
    audit["validated"] = not audit["missing"]
    return audit, None
