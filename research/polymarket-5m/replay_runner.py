"""Strategy plugin dispatcher over the existing receipt-clock development engine.

This module does not fetch data, fit rules or change any frozen protocol. Supported
execution is long Up/Down taker FAK, held until an official binary settlement.
Maker queues, shorts, dynamic hedges and scale-ins are explicitly unsupported.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Callable, Iterable

import replay_engine as ex


PORTFOLIO_ID = "__SHARED_TAKER_PORTFOLIO__"
CONTRACT = {
    "style": "taker", "order_type": "FAK", "position": "long_binary",
    "legs": 1, "exit": "official_settlement", "scale_in": False,
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_current_source(name):
    """Execute the exact source bytes we register, bypassing sys.modules/pyc.

    A plain import may reuse an already loaded module or timestamp/size-valid
    bytecode after a same-second edit. That would make a source hash incorrectly
    describe the executed rule. Standard module metadata and relative imports
    are preserved, but this loader deliberately supports Python source plugins
    only. Module dependencies are governed by the run's environment/code manifest.
    """
    spec = importlib.util.find_spec(name)
    if (spec is None or spec.origin is None or spec.loader is None
            or not spec.origin.endswith(".py")):
        raise ValueError(f"{name} must resolve to a hashable Python source plugin")
    path = Path(spec.origin).resolve()
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module, path, digest


def _check_contract(value, owner):
    """Check execution declarations, never infer fills from a mechanism label."""
    if value is None:
        return
    if isinstance(value, str):
        if value.lower() not in {"taker", "fak", "long_binary_taker"}:
            raise ValueError(f"unsupported execution declaration for {owner}: {value}")
        return
    if not isinstance(value, dict):
        raise ValueError(f"execution contract must be an object for {owner}")
    aliases = {"style": {"taker"}, "execution_style": {"taker"},
               "order_type": {"fak"}, "position": {"long", "long_binary"},
               "exit": {"official_settlement", "settlement"}}
    for key, allowed in aliases.items():
        if key in value and str(value[key]).lower() not in allowed:
            raise ValueError(f"unsupported {key} for {owner}: {value[key]}")
    for key in ("maker", "hedge", "short", "scale_in", "requires_queue", "requires_hedge"):
        if value.get(key) not in (None, False):
            raise ValueError(f"unsupported {key} for {owner}")
    if "legs" in value and (type(value["legs"]) is not int or value["legs"] != 1):
        raise ValueError(f"only one binary taker leg is supported for {owner}")


@dataclass
class LoadedStrategies:
    candidates: list[dict]
    signal_functions: dict[str, Callable]
    plugin_code_sha256: dict[str, str]
    registration: dict


def load_strategies(specs: list[str]) -> LoadedStrategies:
    """Load module:id or module:*; call before persisting the run registration.

    Catalog providers may be catalog(), candidates() or make_candidates(). An
    explicit make_all_candidates() takes precedence; supplementary providers
    are appended to make_candidates() where present. IDs
    must be globally unique. Catalog order is the default fixed tie priority.
    No signal is evaluated while loading and no price/outcome data is accessed.
    """
    if not isinstance(specs, (list, tuple)) or not specs:
        raise ValueError("at least one module:candidate-id strategy is required")
    candidates, functions, hashes, sources, modules = [], {}, {}, {}, {}
    for spec in specs:
        if not isinstance(spec, str) or ":" not in spec:
            raise ValueError("strategy must be module:candidate-id or module:*")
        name, wanted = spec.rsplit(":", 1)
        if not name or not wanted:
            raise ValueError("empty module or candidate ID")
        if name not in modules:
            plugin, source_path, source_digest = _load_current_source(name)
            for declaration in (getattr(plugin, "EXECUTION_CONTRACT", None),
                                getattr(plugin, "EXECUTION_STYLE", None)):
                _check_contract(declaration, name)
            provider = next((getattr(plugin, attr, None) for attr in
                             ("make_all_candidates", "catalog", "candidates", "make_candidates")
                             if callable(getattr(plugin, attr, None))), None)
            if provider is None or not callable(getattr(plugin, "signal", None)):
                raise ValueError(f"{name} requires a catalog provider and signal(candidate, features)")
            catalog = list(provider())
            if (getattr(provider, "__name__", None) == "make_candidates" and
                    callable(getattr(plugin, "make_supplementary_candidates", None))):
                catalog += list(plugin.make_supplementary_candidates())
            catalog_ids = [c.get("id") for c in catalog if isinstance(c, dict)]
            if len(catalog_ids) != len(catalog) or any(not isinstance(x, str) or not x for x in catalog_ids):
                raise ValueError(f"{name} catalog must contain nonempty string IDs")
            if len(set(catalog_ids)) != len(catalog_ids):
                raise ValueError(f"{name} has duplicate catalog IDs")
            hashes[name] = source_digest
            sources[name] = str(source_path)
            modules[name] = (plugin, catalog)
        plugin, catalog = modules[name]
        matches = catalog if wanted == "*" else [c for c in catalog if c["id"] == wanted]
        if not matches:
            raise ValueError(f"unknown or empty candidate selection: {spec}")
        for raw in matches:
            candidate = copy.deepcopy(raw)
            cid = candidate["id"]
            if cid in functions or cid == PORTFOLIO_ID:
                raise ValueError(f"duplicated or reserved candidate ID: {cid}")
            names = candidate.get("required_features")
            if (not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names)
                    or len(names) != len(set(names))):
                raise ValueError(f"{cid} requires an explicit unique feature-name list")
            _check_contract(candidate, cid)
            _check_contract(candidate.get("execution_contract"), cid)
            _check_contract(candidate.get("execution"), cid)
            # Freeze a JSON-compatible copy; reject NaN in rule configuration.
            candidate = json.loads(json.dumps(candidate, sort_keys=True, allow_nan=False))
            candidates.append(candidate)
            functions[cid] = plugin.signal
    registration = {
        "strategy_specs": list(specs), "candidate_rules": candidates,
        "candidate_sha256": ex.fingerprint(candidates),
        "plugin_code_sha256": hashes, "plugin_source_paths": sources,
        "default_priority": [c["id"] for c in candidates],
        "execution_contract": CONTRACT, "formal_oos_result": None,
        "candidate_count": len(candidates),
        "runner_code_sha256": _sha(__file__), "engine_code_sha256": _sha(ex.__file__),
    }
    return LoadedStrategies(candidates, functions, hashes, registration)


def _feature_error(event, candidate, market):
    now = event["recv_ms"]
    if market is None or not market["start_ms"] <= now < market["end_ms"]:
        return "missing_market_or_outside_window"
    features, available = event.get("features"), event.get("feature_available_ms")
    if not isinstance(features, dict) or not isinstance(available, dict):
        return "missing_feature_maps"
    for name in sorted(set(candidate["required_features"]) | {"tau_s"}):
        if not ex._number(features.get(name)):
            return "missing_or_invalid_feature:" + name
        if not ex._number(available.get(name)):
            return "missing_feature_availability:" + name
        if available[name] > now:
            return "future_feature:" + name
    if (not 0 <= features["tau_s"] <= 300 or
            abs(features["tau_s"] - (market["end_ms"] - now) / 1000) > 0.001):
        return "invalid_tau_or_market_clock"
    return None


def execute(events: Iterable[dict], specs, config=None, mode="isolated", priority=None) -> dict:
    """Run one registered scenario. Different latency buckets never share cash.

    Shared mode consumes each market on its earliest component signal, including
    a rejected/no-fill first order; later rules cannot retry. Priority resolves
    simultaneous hits in a decision. Distinct receipt records preserve input
    order. Only validated required features are passed to any signal function.
    """
    if mode not in ("isolated", "shared"):
        raise ValueError("mode must be isolated or shared")
    loaded = specs if isinstance(specs, LoadedStrategies) else load_strategies(specs)
    if config is None:
        config = ex.ReplayConfig()
    elif isinstance(config, dict):
        config = ex.ReplayConfig(**config)
    if not isinstance(config, ex.ReplayConfig):
        raise TypeError("config must be ReplayConfig or a config dictionary")
    # Do not execute source that changed after registration.
    for name, path in loaded.registration["plugin_source_paths"].items():
        if _sha(path) != loaded.plugin_code_sha256[name]:
            raise ValueError(f"plugin code changed after registration: {name}")
    if loaded.registration["candidate_sha256"] != ex.fingerprint(loaded.candidates):
        raise ValueError("candidate rules changed after registration")
    if loaded.registration["engine_code_sha256"] != _sha(ex.__file__):
        raise ValueError("execution engine changed after registration")
    ids = [c["id"] for c in loaded.candidates]
    ordered = ids if priority is None else list(priority)
    if len(ordered) != len(ids) or set(ordered) != set(ids):
        raise ValueError("priority must contain every selected candidate exactly once")
    by_id = {c["id"]: c for c in loaded.candidates}
    candidates = [by_id[cid] for cid in ordered]

    def component_signal(candidate, features):
        return loaded.signal_functions[candidate["id"]](candidate, features)

    audit = {"chosen_signals": [], "suppressed_signals": [], "component_first_signals": [],
             "component_feature_audit": {}, "pair_overlap": []}
    if mode == "isolated":
        result = ex.replay_events(events, candidates, component_signal, config)
    else:
        markets, chosen, first = {}, {}, {}
        invalid = {cid: Counter() for cid in ids}
        valid, no_signal, signal_errors = Counter(), Counter(), Counter()

        def arbitrated():
            for original in events:
                event = original
                now, kind = event.get("recv_ms"), event.get("type")
                if not ex._number(now):
                    raise ValueError("every record must include finite local recv_ms")
                market_id = event.get("market_id")
                if kind == "market":
                    start, end = event.get("start_ms"), event.get("end_ms")
                    if (ex._number(start) and ex._number(end) and end - start == 300000
                            and event.get("label_source") in ex.OFFICIAL_SOURCES):
                        markets[market_id] = event
                    else:
                        markets.pop(market_id, None)
                if kind != "decision":
                    yield event
                    continue
                hits, any_valid = [], False
                for candidate in candidates:
                    cid = candidate["id"]
                    if (cid, market_id) in first:
                        continue
                    error = _feature_error(event, candidate, markets.get(market_id))
                    if error:
                        invalid[cid][error] += 1
                        continue
                    any_valid = True
                    valid[cid] += 1
                    visible = {name: event["features"][name] for name in
                               set(candidate["required_features"]) | {"tau_s"}}
                    try:
                        side = component_signal(candidate, visible)
                    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                        invalid[cid]["signal_error:" + type(exc).__name__] += 1
                        signal_errors[cid] += 1
                        continue
                    if side is None:
                        no_signal[cid] += 1
                        continue
                    if side not in ("Up", "Down"):
                        raise ValueError(f"invalid signal return for {cid}: {side!r}")
                    row = {"candidate_id": cid, "market_id": market_id,
                           "decision_ms": now, "side": side}
                    first[(cid, market_id)] = row
                    audit["component_first_signals"].append(row)
                    hits.append(row)
                selected = None
                if hits and market_id not in chosen:
                    selected = hits[0]
                    choice = {**selected, "selected_candidate": selected["candidate_id"],
                              "simultaneous_signals": [{"candidate_id": x["candidate_id"], "side": x["side"]} for x in hits],
                              "opposite_simultaneous": len({x["side"] for x in hits}) > 1}
                    chosen[market_id] = choice
                    audit["chosen_signals"].append(choice)
                for hit in hits:
                    if hit is not selected:
                        audit["suppressed_signals"].append({**hit,
                            "selected_candidate": chosen[market_id]["selected_candidate"],
                            "reason": "fixed_priority_tie" if selected else "market_already_consumed_no_retry"})
                # Never mutate a caller's cached feature tape. An entirely invalid
                # decision does not receive a synthetic zero/valid feature row.
                event = dict(event)
                event["features"] = dict(original["features"]) if isinstance(original.get("features"), dict) else {}
                event["feature_available_ms"] = dict(original["feature_available_ms"]) if isinstance(original.get("feature_available_ms"), dict) else {}
                event["features"].pop("portfolio_side", None)
                event["feature_available_ms"].pop("portfolio_side", None)
                if any_valid:
                    event["features"]["portfolio_side"] = (1 if selected["side"] == "Up" else -1) if selected else 0
                    event["feature_available_ms"]["portfolio_side"] = now
                yield event

        portfolio_candidate = {"id": PORTFOLIO_ID, "required_features": ["portfolio_side"]}

        def portfolio_signal(candidate, features):
            return {1: "Up", -1: "Down"}.get(features["portfolio_side"])

        result = ex.replay_events(arbitrated(), [portfolio_candidate], portfolio_signal, config)
        for attempt in result["attempts"]:
            attempt["selected_candidate"] = chosen[attempt["market_id"]]["selected_candidate"]
        for cid in ids:
            rows = [v for (candidate_id, _), v in first.items() if candidate_id == cid]
            audit["component_feature_audit"][cid] = {
                "valid_feature_rows": valid[cid], "invalid_decision_reasons": dict(invalid[cid]),
                "first_signal_markets": len(rows), "no_signal_rows": no_signal[cid],
                "status": "blocked_no_valid_feature_rows" if not valid[cid] else (
                    "blocked_signal_errors" if signal_errors[cid] == valid[cid] else
                    "evaluated_first_signals" if rows else "evaluated_no_signal"),
                "net_pnl_usd": None,
                "pnl_scope": "component signal diagnostic; only shared portfolio ledger has PnL",
            }
        for i, a in enumerate(ids):
            aa = {m: row for (c, m), row in first.items() if c == a}
            for b in ids[i + 1:]:
                bb = {m: row for (c, m), row in first.items() if c == b}
                intersection, union = set(aa) & set(bb), set(aa) | set(bb)
                audit["pair_overlap"].append({"a": a, "b": b, "shared_markets": len(intersection),
                    "union_markets": len(union), "jaccard": len(intersection) / len(union) if union else None,
                    "opposite_first_signal_markets": sum(aa[m]["side"] != bb[m]["side"] for m in intersection)})
        result["capital_scope"] = "one_shared_portfolio_cash_ledger_per_latency_do_not_sum_latencies"
        result["arbitration_policy"] = "earliest eligible first signal; fixed candidate priority within decision; input order across receipt records; one order per market; no retry"
    result.update(audit)
    result.update(mode=mode, engine_config=asdict(config), execution_contract=CONTRACT,
                  selected_candidate_count=len(candidates), component_candidate_sha256=ex.fingerprint(loaded.candidates),
                  strategy_registration=copy.deepcopy(loaded.registration), fixed_priority=ordered,
                  plugin_code_sha256=dict(loaded.plugin_code_sha256), runner_code_sha256=_sha(__file__),
                  formal_oos_result=None, confirmed_strategies=0)
    result["limitations"] = ["development only; no inherited holdout freeze",
        "displayed order-book depth is a simulated capacity upper bound, not observed fills",
        "long binary taker held to official settlement only; no maker, short, hedge or scale-in execution",
        "realized drawdown only; cash release uses configured delay after locally known official outcome",
        "isolated candidate results and alternative latency scenarios must not be added"]
    return result
