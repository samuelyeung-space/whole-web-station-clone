"""Tree batch protocol tests use synthetic tapes, never market-return evidence."""
import json

import pytest

import mine500_tree_batch as batch

DAY = 1791331200000  # October 7, 2026 UTC, consumed development only.


def tape(tmp_path):
    filename = tmp_path / "development.jsonl"
    rows = [dict(type="dataset", recv_ms=DAY, dataset_id="synthetic-only", split="development",
                 coverage_start_ms=DAY, coverage_end_ms=DAY+2000,
                 book_resolution="decision_and_match_projection",
                 supported_latencies_ms=[500, 600, 1000], decision_grid_ms=1000),
            dict(type="market", market_id="m", recv_ms=DAY, start_ms=DAY,
                 end_ms=DAY+300000, label_source="gamma"),
            dict(type="decision", market_id="m", recv_ms=DAY+1000,
                 features={"mid_up": .5, "tau_s": 299},
                 feature_available_ms={"mid_up": DAY+1000, "tau_s": DAY+1000})]
    filename.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return filename


def test_registration_is_on_disk_before_fit_and_overwrite_refused(tmp_path, monkeypatch):
    source, out = tape(tmp_path), tmp_path / "out"
    class AfterRegistration(Exception):
        pass
    def stop(*args, **kwargs):
        assert isinstance(args[0], batch.common.ReplayTape)
        registration = json.loads((out / "registration.json").read_text())
        assert len(registration["candidates"]) == registration["new_trials"] == 12
        assert registration["cumulative_mine500_trial_lower_bound"] == 772
        assert registration["known_multiplicity_lower_bound_including_zoo100"] == 872
        assert registration["formal_oos_results"] is None
        assert len(registration["scenarios"]) == 4
        assert not (out / "fit_folds.json").exists()
        assert registration["input_hashes"]
        assert registration['combined_tape']['sha256'] == batch.common.digest(out/'combined_input.jsonl.gz')
        assert set(batch.DEPENDENCY_NAMES) == set(registration["code_hashes"])
        raise AfterRegistration
    monkeypatch.setattr(batch.tree, "train_forward_days", stop)
    with pytest.raises(AfterRegistration):
        batch.run([source], out)
    with pytest.raises(FileExistsError):
        batch.run([source], out)


def test_all_144_scenarios_present_and_missing_scores_stay_blocked(tmp_path, monkeypatch):
    source, out = tape(tmp_path), tmp_path / "run"
    monkeypatch.setattr(batch.tree, "train_forward_days", lambda *a, **kw: ({"folds": [], "formal_oos_result": None}, {}))
    batch.run([source], out)
    done = json.loads((out / "completion.json").read_text())
    metrics = json.loads((out / "new_candidate_metrics.json").read_text())
    assert len(metrics) == done["new_candidate_scenarios"] == 144
    assert done["formal_oos_result"] is None
    assert done["confirmed_strategies"] == 0
    assert done["code_and_input_integrity_verified"]
    assert {r["scenario"] for r in metrics} == {name for name, _ in batch.SCENARIOS}
    assert {r["latency_ms"] for r in metrics} == {500, 600, 1000}
    assert len({r["candidate_id"] for r in metrics}) == 12
    assert all(r["net_pnl_usd"] is None for r in metrics)
    assert all(r["status"] == "blocked_no_valid_feature_rows" for r in metrics)
    assert len((out / "new_candidate_metrics.csv").read_text().splitlines()) == 145
    scores = json.loads((out / "score_audit.json").read_text())
    assert scores["scored_model_decisions"] == 0
    assert scores["missing_model_decisions"] == 6
    for name, _ in batch.SCENARIOS:
        stats = json.loads((out / (name + "_statistics.json")).read_text())
        assert all(not row["qualified"] for row in stats["results"])
        assert stats['current_registered_candidate_count'] == 12
        assert stats['historical_multiplicity_lower_bound'] == 872
        assert all(row['declared_rules'] == 12 and row['missing_runner_summaries'] == 0
                   for row in stats['denominator_audit'])
        intent = json.loads((out / (name + '_panel_intent_replay.json')).read_text())
        assert intent['config']['initial_cash_usd'] == 1e12
        assert (out / (name + '_panel_intent_attempts.jsonl.gz')).exists()
    panels = json.loads((out / 'shared_cash_panels.json').read_text())
    assert all(row['portfolio_cash_usd'] == 1000 and row['intent_capital_censoring'] is False
               for scenario in panels for row in scenario['results'])


def score_fixture():
    events = [{"type": "decision", "market_id": "m", "recv_ms": DAY}]
    candidates = [{"model_id": "x"}]
    value = dict(p_up=.6, model_fit_ms=DAY-100, feature_available_ms=DAY)
    return events, candidates, value


@pytest.mark.parametrize("field,value", [("p_up", float("nan")), ("p_up", True),
    ("p_up", 1.01), ("p_up", -.01), ("model_fit_ms", DAY+1),
    ("feature_available_ms", DAY+1), ("feature_available_ms", None)])
def test_invalid_or_future_scores_abort(field, value):
    events, candidates, score = score_fixture()
    score[field] = value
    with pytest.raises(ValueError, match="invalid or future"):
        batch.validate_scores(events, {("m", DAY, "x"): score}, candidates)


@pytest.mark.parametrize("key", [("other", DAY, "x"), ("m", DAY+1, "x"),
    ("m", DAY, "unknown"), ("m", DAY)])
def test_score_join_is_exact(key):
    events, candidates, score = score_fixture()
    with pytest.raises(ValueError, match="join"):
        batch.validate_scores(events, {key: score}, candidates)


def test_missing_score_is_not_imputed():
    events, candidates, _ = score_fixture()
    assert batch.validate_scores(events, {}, candidates)["missing_model_decisions"] == 1


def test_attachment_cannot_change_nondecision_or_decision_identity():
    with pytest.raises(ValueError, match="count"):
        batch.validate_enrichment([dict(type="book")], [])
    with pytest.raises(ValueError, match="nondecision"):
        batch.validate_enrichment([dict(type="outcome", recv_ms=10)], [dict(type="outcome", recv_ms=1)])
    with pytest.raises(ValueError, match="identity"):
        batch.validate_enrichment([dict(type="decision", recv_ms=10)], [dict(type="decision", recv_ms=1)])


def test_input_mutation_invalidates_outputs_without_completion(tmp_path, monkeypatch):
    source, out = tape(tmp_path), tmp_path / "invalidated"
    def mutate(*args, **kwargs):
        source.write_text(source.read_text() + "\n")
        return {"folds": []}, {}
    monkeypatch.setattr(batch.tree, "train_forward_days", mutate)
    with pytest.raises(ValueError, match="changed during"):
        batch.run([source], out)
    assert (out / "invalidated_run.json").exists()
    assert not (out / "completion.json").exists()


def test_streamed_enrichment_all_content_equals_list_reference_and_is_reentrant(tmp_path):
    source = tape(tmp_path)
    reference, _ = batch.common.combine_inputs([source])
    materialized, _ = batch.common.combine_inputs(
        [source], materialize_output=tmp_path/'combined.jsonl.gz')
    model = batch.tree.MODEL_IDS[0]
    scores = {('m', DAY+1000, model): {
        'p_up': .6, 'model_fit_ms': DAY, 'feature_available_ms': DAY+1000}}
    # JSON preserves interval tuples as arrays; compare the complete wire data.
    expected = json.loads(json.dumps(list(batch.tree.attach_scores(reference, scores))))
    actual = batch.materialize_enrichment(materialized, scores, tmp_path/'enriched.jsonl.gz')
    assert isinstance(actual, batch.common.ReplayTape)
    assert len(actual) == len(reference)
    assert list(actual) == expected
    assert list(actual) == expected  # replay can re-open without caching events
    batch.common.write_gzip(tmp_path/'reference.jsonl.gz', expected)
    assert (tmp_path/'reference.jsonl.gz').read_bytes() == (tmp_path/'enriched.jsonl.gz').read_bytes()


def test_materialized_count_cannot_hide_truncated_event_sequence(tmp_path):
    source = tape(tmp_path)
    reference, _ = batch.common.combine_inputs([source])
    reference = json.loads(json.dumps(reference))
    batch.common.write_gzip(tmp_path/'truncated.jsonl.gz', reference[:-1])
    forged_count = batch.common.ReplayTape(tmp_path/'truncated.jsonl.gz', len(reference))
    with pytest.raises(ValueError, match='actual event count'):
        batch.validate_enrichment(reference, forged_count)
