"""Adversarial causal/semantic fixtures; synthetic successes are not alpha."""
import copy
import json

import pytest

from mine500_path_candidates import (
    TEMPLATES, augment_events, make_candidates, path_score, run, signal,
)


PATHS = {
    "neutral_first_exit": [.5]*15+[.56],
    "failed_excursion": [.5]+[.34]*10+[.40,.46,.47,.48,.49],
    "leader_dwell_pullback": [.65]*13+[.52,.53,.55],
    "roundtrip_reclaim": [.61]*6+[.50]*5+[.52,.55,.58,.609,.615],
    "oscillation_edge_fade": [.5,.56,.5,.56,.5,.56,.5]+[.52]*8+[.5],
    "isolated_jump_fade": [.6]*10+[.54,.54,.541,.543,.545,.55],
    "staircase_follow": [.4+i*.005 for i in range(16)],
    "contested_majority_release": [.48,.52]*6+[.48,.53,.54,.55],
}


def records(path, *, origin=60000):
    yield {"type": "dataset", "recv_ms": 0, "dataset_id": "synthetic", "split": "development"}
    yield {"type": "market", "recv_ms": 0, "market_id": "m", "start_ms": 0,
           "end_ms": 300000, "label_source": "gamma"}
    for i, midpoint in enumerate(path):
        now = origin+i*1000
        yield {"type": "decision", "market_id": "m", "recv_ms": now,
               "features": {"mid_up": midpoint, "tau_s": (300000-now)/1000,
                            "r_5s": .6, "r_15s": 1.5},
               "feature_available_ms": {"mid_up": now-50, "tau_s": now,
                                        "r_5s": now-90, "r_15s": now-90}}


def test_registry_unique_nonoverlapping_tau_and_prior_denominator():
    registry = make_candidates()
    assert len(registry) == 32
    assert len({row["rule_fingerprint"] for row in registry}) == 32
    assert registry == make_candidates()
    assert {row["family"] for row in registry} == {
        "probability_barrier", "probability_occupation", "return_point_memory",
        "probability_oscillation", "transient_quote_shock", "distributed_repricing"}
    for row in registry:
        assert row["independent_mechanism_claim"] is False
        feature = row["required_features"][1]
        assert signal(row, {"tau_s": 90, feature: 1}) == (
            "Up" if row["params"]["tau_min_s"] == 90 else None)


@pytest.mark.parametrize("key", list(PATHS))
def test_directional_patterns_and_exact_mirror(key):
    path = PATHS[key]
    assert len(path) == 16
    external = {"r_5s": .6, "r_15s": 1.5}
    assert path_score(key, path, external) == 1
    assert path_score(key, [1-p for p in path], {k:-v for k,v in external.items()}) == -1
    assert path_score(key, [.5]*16, external) == 0


def test_equal_endpoints_carry_different_intervening_path_information():
    path = PATHS["roundtrip_reclaim"]
    shallow = [path[0]]*15+[path[-1]]
    assert path[0] == shallow[0] and path[-1] == shallow[-1]
    assert path_score("roundtrip_reclaim", path) == 1
    assert path_score("roundtrip_reclaim", shallow) == 0


def test_decimal_band_edges_are_direction_symmetric():
    path = [.45,.55]*7+[.55,.56]
    assert path_score("neutral_first_exit", path) == 1
    assert path_score("neutral_first_exit", [1-p for p in path]) == -1


def test_exact_four_cent_staircase_is_not_lost_to_float_subtraction():
    path = [.40]*12+[.41,.42,.43,.44]
    assert path[-1]-path[0] < .04  # The binary-float counterexample is real.
    assert path_score("staircase_follow", path) == 1
    assert path_score("staircase_follow", [1-p for p in path]) == -1
    below = path[:-1]+[.439]
    assert path_score("staircase_follow", below) == 0
    assert path_score("staircase_follow", [1-p for p in below]) == 0


def test_external_information_vetoes_jump_fade():
    path = PATHS["isolated_jump_fade"]
    assert path_score("isolated_jump_fade", path, {"r_5s": -2}) == 0
    with pytest.raises(ValueError, match="external"):
        path_score("isolated_jump_fade", path)


def test_flat_and_ambiguous_double_excursion_not_assigned_by_outcome():
    path = [.5,.34,.5,.66,.5]+[.5]*11
    assert path_score("failed_excursion", path) == 0


def test_augmentation_prefix_invariance_no_input_mutation_or_label_use():
    events = list(records(PATHS["neutral_first_exit"]))
    original = copy.deepcopy(events)
    baseline = list(augment_events(events))
    later = {"type":"outcome", "recv_ms":310000, "market_id":"m", "winner":"Down"}
    changed = list(augment_events(events+[later]))
    assert events == original
    assert baseline == changed[:-1]
    assert baseline[-1]["features"]["path_neutral_first_exit_15s"] == 1
    assert baseline[-1]["feature_available_ms"]["path_neutral_first_exit_15s"] == 74950
    assert baseline[-1]["features"]["path_neutral_first_exit_30s"] is None
    # Non-decisions keep their exact execution, fee and outcome payload.
    assert changed[-1] is later


@pytest.mark.parametrize("defect", ["missing_mid", "future", "stale", "missing_clock", "grid_gap"])
def test_historical_missing_or_unavailable_mid_breaks_whole_path(defect):
    events = list(records(PATHS["neutral_first_exit"]))
    row = events[7]
    if defect == "missing_mid":
        row["features"]["mid_up"] = None
    elif defect == "future":
        row["feature_available_ms"]["mid_up"] = row["recv_ms"]+1
    elif defect == "stale":
        row["feature_available_ms"]["mid_up"] = row["recv_ms"]-251
    elif defect == "missing_clock":
        row["feature_available_ms"].pop("mid_up")
    else:
        events.pop(7)
    final = list(augment_events(events))[-1]
    for key, *_ in TEMPLATES:
        assert final["features"][f"path_{key}_15s"] is None
        assert final["feature_available_ms"][f"path_{key}_15s"] is None


def test_late_external_field_only_blocks_rules_that_require_it():
    events = list(records(PATHS["isolated_jump_fade"]))
    events[-1]["feature_available_ms"]["r_5s"] = 76000
    final = list(augment_events(events))[-1]
    assert final["features"]["path_isolated_jump_fade_15s"] is None
    assert final["features"]["path_staircase_follow_15s"] == 0


def test_markets_cannot_share_a_partial_path():
    events = list(records(PATHS["neutral_first_exit"]))
    events[-1]["market_id"] = "other"
    assert list(augment_events(events))[-1]["features"]["path_neutral_first_exit_15s"] is None


def test_duplicate_decision_or_regressed_input_rejected():
    events = list(records(PATHS["neutral_first_exit"]))
    with pytest.raises(ValueError, match="duplicate"):
        list(augment_events(events+[events[-1]]))
    with pytest.raises(ValueError, match="receipt clock"):
        list(augment_events(events+[events[-2]]))


def test_future_split_and_missing_header_fail_closed():
    events = list(records(PATHS["neutral_first_exit"]))
    events[0]["split"] = "prospective_holdout"
    with pytest.raises(ValueError, match="development"):
        list(augment_events(events))
    with pytest.raises(ValueError, match="header"):
        list(augment_events(events[1:]))


def test_registration_survives_failed_execution_and_refuses_overwrite(tmp_path):
    source, out = tmp_path/"events.jsonl", tmp_path/"out"
    source.write_text("\n".join(json.dumps(r) for r in records(PATHS["neutral_first_exit"])))
    report = run(source, out, sizes=(5,), latencies=(500,))
    assert report["scenario_count"] == 32
    registration = json.loads((out/"registration.json").read_text())
    assert registration["minimum_new_trial_count"] == 32
    assert "720" in registration["multiplicity"]
    # No direct book exists, so a valid path cannot fabricate an execution.
    result = json.loads((out/"replay_size5.json").read_text())
    assert result["attempts"]
    assert all(not row["submitted"] for row in result["attempts"])
    assert all(not row["filled_shares"] for row in result["attempts"])
    with pytest.raises(ValueError, match="already exists"):
        run(source, out, sizes=(5,), latencies=(500,))
