"""Receipt-causal probability-path research, separate from the frozen registry.

The 32 rules condition on the ORDER of previously observed direct Up midpoints,
not just endpoint returns. Eight overlapping hypotheses, two lookbacks and two
time-to-expiry bands are not 32 independent economic mechanisms. Designs are
adaptive development research. Neither the registry nor synthetic tests are PnL
evidence; actual execution uses mine500_replay_v2's fixed-limit partial FAK.

Usage::

    python mine500_path_candidates.py --input /private/development.jsonl.gz \
        --out /private/path-development

No training or outcome-dependent selection is performed. Every first qualifying
signal is retained, including rejected and unfilled attempts. Books, outcomes,
fees and original clocks are passed through unchanged. A path requires all L+1
one-second decisions with fresh direct midpoints. Gaps, missing observations and
future availability invalidate the path; nothing is interpolated. External
returns are used only when explicitly present and causally available. The
module cannot recover aggressor side, reference prices or official TWAP samples.

Existing overlap: endpoint follow/fade, acceleration and nested-return rules in
mine500_catalog, mid_move/stale_move in strategy_zoo, and PM momentum in zoo100.
New information is crossing order, occupation, intrawindow extrema, variation
concentration and oscillation. This is a feature-level novelty claim only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path

VERSION = "mine500-path-v1"
LOOKBACKS = (15, 30)
TAU_BANDS = ((20, 90), (90, 240))
MAX_MID_AGE_MS = 250
TEMPLATES = (
    ("neutral_first_exit", "probability_barrier", (),
     "Fresh exit from 45-55c after the whole previous path stayed inside; "
     "tests persistence of a new majority rather than endpoint momentum."),
    ("failed_excursion", "probability_barrier", (),
     "From a neutral start, visit 65c then return below 55c for three seconds "
     "without a symmetric 35c excursion; fade the failed excursion."),
    ("leader_dwell_pullback", "probability_occupation", (),
     "At least 80% of the pre-pullback path above 60c, then a neutral-zone "
     "pullback and 1c rebound; tests remembered demand after a discount."),
    ("roundtrip_reclaim", "return_point_memory", (),
     "Return through the starting price after an 8c adverse excursion; "
     "same current price but a different intervening path."),
    ("oscillation_edge_fade", "probability_oscillation", (),
     "At least four reversals and 16c total variation, net efficiency <=30%, "
     "then fade an outer decile of the path's range."),
    ("isolated_jump_fade", "transient_quote_shock", ("r_5s",),
     "A >=4c jump explains >=60% quadratic variation, occurred 3-8s ago, "
     "and is 10-50% retraced without >=1bp external confirmation; fade."),
    ("staircase_follow", "distributed_repricing", (),
     "At least four nonzero moves, >=4c net repricing, >=75% path efficiency, "
     "no step over 40% net move or 35% quadratic variation; follow."),
    ("contested_majority_release", "probability_occupation", ("r_5s", "r_15s"),
     "At least four direct crosses of 50c precede three seconds above 52c "
     "with same-sign >=0.5/1bp external 5/15s returns; follow the release."),
)
TEMPLATE_MAP = {row[0]: row for row in TEMPLATES}


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def make_candidates():
    """Deterministic registration; thresholds are fixed before this batch runs."""
    result = []
    for key, family, external, rationale in TEMPLATES:
        for lookback in LOOKBACKS:
            for low, high in TAU_BANDS:
                candidate = {
                    "id": f"PATH-{len(result)+1:03d}", "version": VERSION,
                    "template": key, "family": family,
                    "parameter_variant_of": key,
                    "params": {"lookback_s": lookback, "tau_min_s": low,
                               "tau_max_s": high, "upper_inclusive": high == 240},
                    "required_features": ["tau_s", f"path_{key}_{lookback}s"],
                    "raw_feature_dependencies": ["mid_up", *external],
                    "execution_kind": "single_leg_taker_research",
                    "name": f"{key} / L={lookback}s / tau={low}-{high}s",
                    "causal_rule": rationale + "; Down is the exact Up/Down mirror.",
                    "novelty": "new_path_interaction_in_existing_price_families",
                    "independent_mechanism_claim": False,
                    "research_status": "registered_not_backtested",
                    "prior_art": ["mine500_catalog.py", "strategy_zoo.py", "zoo100.py"],
                    "selection_status": "adaptive_development_no_OOS_claim",
                }
                # Cosmetic IDs and descriptions cannot manufacture distinct rules.
                candidate["rule_fingerprint"] = _hash({key: candidate[key] for key in (
                    "version", "template", "params", "required_features",
                    "raw_feature_dependencies", "execution_kind")})
                result.append(candidate)
    return result


def _up_trigger(key, p, external):
    """Up-side condition; symmetric Down condition uses 1-p and -returns."""
    increments = [b-a for a, b in zip(p, p[1:])]
    absolute = [abs(x) for x in increments]
    variation = sum(absolute)
    squared = sum(x*x for x in increments)
    net = p[-1]-p[0]
    if key == "neutral_first_exit":
        return all(.45 <= x <= .55 for x in p[:-1]) and p[-1] >= .555
    if key == "failed_excursion":
        # Up is a failed DOWN excursion. This orientation keeps mirror exact.
        return (.45 <= p[0] <= .55 and min(p[:-3]) <= .35
                and max(p) < .65 and all(x >= .45 for x in p[-3:]))
    if key == "leader_dwell_pullback":
        prefix = p[:-3]
        return (sum(x >= .60 for x in prefix)/len(prefix) >= .8
                and .50 <= p[-1] < .60 and min(p[-3:]) >= .45
                and p[-1]-min(p[-3:]) >= .01 and p[-1] > p[-2])
    if key == "roundtrip_reclaim":
        return (.40 <= p[0] <= .75 and min(p[1:-1]) <= p[0]-.08
                and p[-2] < p[0] <= p[-1] <= p[0]+.01)
    if key == "oscillation_edge_fade":
        signs = [1 if x > 0 else -1 for x in increments if abs(x) > 1e-10]
        turns = sum(a != b for a, b in zip(signs, signs[1:]))
        width = max(p)-min(p)
        return (variation >= .16 and turns >= 4 and width >= .04
                and abs(net) <= .3*variation
                and p[-1] <= min(p)+.1*width)
    if key == "isolated_jump_fade":
        if squared <= 0:
            return False
        index = max(range(len(increments)), key=lambda j: abs(increments[j]))
        jump = increments[index]
        age = len(increments)-1-index
        retrace = (p[-1]-p[index+1])/(-jump) if jump < 0 else -1
        return (jump <= -.04 and jump*jump >= .6*squared and 3 <= age <= 8
                and .1 <= retrace <= .5 and increments[-1] > 0
                and external["r_5s"] > -1.)
    if key == "staircase_follow":
        return (net >= .04 and variation > 0 and net >= .75*variation
                and sum(x > 1e-10 for x in absolute) >= 4
                and max(absolute) <= .4*net
                and max(x*x for x in increments) <= .35*squared)
    if key == "contested_majority_release":
        prefix = p[:-3]
        crossings = sum((a-.5)*(b-.5) < 0 for a, b in zip(prefix, prefix[1:]))
        return (crossings >= 4 and all(x >= .52 for x in p[-3:])
                and external["r_5s"] >= .5 and external["r_15s"] >= 1.)
    raise ValueError("unregistered path template")


def path_score(key, path, external=None):
    """Return 1/-1/0 on a COMPLETE path; path validity belongs to augmentation."""
    if key not in TEMPLATE_MAP or len(path) < 5:
        raise ValueError("unknown template or short path")
    if any(not _number(p) or not 0 < p < 1 for p in path):
        raise ValueError("invalid direct midpoint")
    # Recorded probability ticks are far coarser than 1e-12. Canonicalizing the
    # float representation keeps e.g. 0.45 -> 0.55 -> 0.45 exactly symmetric.
    path = [round(float(p), 12) for p in path]
    external = external or {}
    needed = TEMPLATE_MAP[key][2]
    if any(not _number(external.get(name)) for name in needed):
        raise ValueError("missing external input")
    up = _up_trigger(key, path, external)
    down = _up_trigger(key, [round(1-p, 12) for p in path], {k: -v for k, v in external.items()})
    # Ambiguous two-sided patterns fail closed instead of choosing by payout.
    return 1 if up and not down else -1 if down and not up else 0


def augment_events(events):
    """One-pass prefix-only path features; original records are not mutated."""
    histories = defaultdict(lambda: deque(maxlen=max(LOOKBACKS)+1))
    previous = -math.inf
    market_end = {}
    seen_header = False
    for event in events:
        now = event.get("recv_ms")
        if not _number(now) or now < previous:
            raise ValueError("receipt clock must be finite and nondecreasing")
        previous = now
        kind = event.get("type")
        if kind == "dataset":
            if seen_header or event.get("split") != "development":
                raise ValueError("only a single consumed development dataset is allowed")
            seen_header = True
            out = dict(event)
            out["path_feature_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            out["path_feature_version"] = VERSION
            out["path_feature_causality"] = "complete receipt-grid history; no interpolation"
            out["signal_code_sha256"] = out["path_feature_code_sha256"]
            yield out
            continue
        if not seen_header:
            raise ValueError("development dataset header must precede records")
        if kind == "market":
            mid = event.get("market_id")
            end = event.get("end_ms")
            if mid in market_end and market_end[mid] != end:
                raise ValueError("market end changed")
            market_end[mid] = end
        if kind != "decision":
            yield event
            continue
        mid = event.get("market_id")
        original = event.get("features") or {}
        availability = event.get("feature_available_ms") or {}
        p, at = original.get("mid_up"), availability.get("mid_up")
        valid = (_number(p) and 0 < p < 1 and _number(at)
                 and 0 <= now-at <= MAX_MID_AGE_MS)
        history = histories[mid]
        if history and now <= history[-1][0]:
            raise ValueError("duplicate or regressed market decision")
        history.append((now, p if valid else None, at if valid else None))
        features, available = dict(original), dict(availability)
        for lookback in LOOKBACKS:
            rows = list(history)[-(lookback+1):]
            complete = (len(rows) == lookback+1 and all(
                t == now-(lookback-i)*1000 and value is not None
                for i, (t, value, _at) in enumerate(rows)))
            for key, _family, dependencies, _rationale in TEMPLATES:
                name = f"path_{key}_{lookback}s"
                features[name] = available[name] = None
                if not complete:
                    continue
                if any(not _number(original.get(dep)) or not _number(availability.get(dep))
                       or not 0 <= now-availability[dep] <= 1000
                       for dep in dependencies):
                    continue
                external = {dep: original[dep] for dep in dependencies}
                features[name] = path_score(key, [r[1] for r in rows], external)
                available[name] = max([r[2] for r in rows] + [availability[d] for d in dependencies])
        out = dict(event, features=features, feature_available_ms=available)
        yield out


def signal(candidate, features):
    params = candidate["params"]
    tau = features.get("tau_s")
    if not _number(tau) or not params["tau_min_s"] <= tau <= params["tau_max_s"]:
        return None
    if tau == params["tau_max_s"] and not params["upper_inclusive"]:
        return None
    score = features.get(f"path_{candidate['template']}_{params['lookback_s']}s")
    if not _number(score):
        return None
    return "Up" if score == 1 else "Down" if score == -1 else None


def run(input_path, out, *, sizes=(5., 10., 20.), latencies=(500, 600, 1000)):
    """Write registration BEFORE PnL; no rerun over an existing result directory."""
    from mine500_replay_v2 import ReplayConfig, read_jsonl, replay_events

    input_path, out = Path(input_path), Path(out)
    if not input_path.is_file():
        raise ValueError("missing development input")
    if out.exists():
        raise ValueError("output directory already exists; preserve all attempted runs")
    candidates = make_candidates()
    with input_path.open("rb") as handle:
        input_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # Check split before creating a registered attempt, without inspecting outcomes.
    header = next(read_jsonl(input_path), {})
    if header.get("type") != "dataset" or header.get("split") != "development":
        raise ValueError("input must declare consumed development data")
    configs = [ReplayConfig(tuple(latencies), size) for size in sizes]
    out.mkdir(parents=True)
    registration = {"version": VERSION, "candidate_count": len(candidates),
                    "candidates": candidates, "input_sha256": input_hash,
                    "path_code_sha256": source_hash,
                    "execution_configs": [asdict(c) for c in configs],
                    "selection_status": "adaptive_development",
                    "minimum_new_trial_count": len(candidates),
                    "multiplicity": "append to all 720 prior variants and all historical trials",
                    "qualification": "no OOS or real-fill claim"}
    (out/"registration.json").write_text(json.dumps(registration, indent=2)+"\n")
    summary = []
    for config in configs:
        result = replay_events(augment_events(read_jsonl(input_path)), candidates, signal, config)
        (out/f"replay_size{config.shares:g}.json").write_text(json.dumps(result, allow_nan=False)+"\n")
        summary.extend(dict(row, requested_shares=config.shares) for row in result["summary"])
    if source_hash != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError("path code changed during run")
    with input_path.open("rb") as handle:
        if input_hash != hashlib.file_digest(handle, "sha256").hexdigest():
            raise RuntimeError("input changed during run")
    counts = Counter(row["status"] for row in summary)
    report = {"candidate_count": len(candidates), "scenario_count": len(summary),
              "evaluation_status_counts": dict(counts), "effective_split": "development",
              "confirmed_OOS_mechanisms": 0, "summary": summary}
    (out/"summary.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.input, args.out)
    print(json.dumps({k: v for k, v in report.items() if k != "summary"}))


if __name__ == "__main__":
    main()
