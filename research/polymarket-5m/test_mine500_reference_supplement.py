"""Coverage accounting regressions; synthetic fixtures are not market results."""
import gzip
import json

import pytest

import mine500_catalog as catalog
import mine500_reference_supplement as supplement


def summaries(candidates, lags=(500, 600, 1000)):
    return [{"candidate_id": c["id"], "latency_ms": lag} for c in candidates for lag in lags]


def test_catalog_partition_exactly_old500_plus_existing220():
    original, additional = catalog.make_candidates(), catalog.make_supplementary_candidates()
    ids = supplement.validate_supplement_registry(additional)
    assert len(original) == 500 and len(additional) == 220
    assert ids[0] == "M500-501" and ids[-1] == "M500-720"
    assert not {c["id"] for c in original}.intersection(ids)
    assert len({c["id"] for c in original+additional}) == 720


def test_actual_500_summaries_must_count_1500_not_hardcoded2160():
    candidates = catalog.make_candidates()
    audit = supplement.validate_summary_coverage(candidates, summaries(candidates), (500, 600, 1000))
    assert audit["actual_candidate_count"] == 500
    assert audit["actual_summary_rows"] == audit["expected_summary_rows"] == 1500


def test_supplement_has660_scenarios_derived_from_220_actual_ids():
    candidates = catalog.make_supplementary_candidates()
    audit = supplement.validate_summary_coverage(candidates, summaries(candidates), (500, 600, 1000))
    assert audit["actual_candidate_count"] == 220 and audit["actual_summary_rows"] == 660


@pytest.mark.parametrize("kind", ["missing", "duplicate", "original500", "wrong_latency"])
def test_wrong_scenario_coverage_fails_closed(kind):
    candidates = catalog.make_supplementary_candidates()
    rows = summaries(candidates)
    if kind == "missing": rows.pop()
    if kind == "duplicate": rows[-1] = dict(rows[0])
    if kind == "original500": rows[-1]["candidate_id"] = "M500-001"
    if kind == "wrong_latency": rows[-1]["latency_ms"] = 250
    with pytest.raises(ValueError, match="coverage mismatch"):
        supplement.validate_summary_coverage(candidates, rows, (500, 600, 1000))


def test_original_registry_cannot_accidentally_be_executed_as_supplement():
    with pytest.raises(ValueError, match="exactly ordered"):
        supplement.validate_supplement_registry(catalog.make_candidates())
    modified = catalog.make_supplementary_candidates()
    modified[-1]["id"] = "M500-500"
    with pytest.raises(ValueError, match="exactly ordered"):
        supplement.validate_supplement_registry(modified)


def tape(tmp_path, *, day_ms=1791331200000, enriched=False, split="development"):
    row = {"type": "decision", "recv_ms": day_ms, "market_id": "synthetic",
           "features": {"tau_s": 50, **({"prob_fake": .5} if enriched else {})}}
    header = {"type": "dataset", "recv_ms": day_ms, "split": split,
              "book_resolution": "decision_and_match_projection", "decision_grid_ms": 1000,
              "supported_latencies_ms": [500, 600, 1000]}
    p = tmp_path/"input.jsonl.gz"
    with gzip.open(p, "wt") as handle:
        for event in (header, row): handle.write(json.dumps(event)+"\n")
    return p


@pytest.mark.parametrize("kwargs,match", [({"enriched":True}, "unenriched"),
    ({"split":"prospective"}, "development"), ({"day_ms":1791590400000}, "unconsumed")])
def test_replay_input_rejects_enrichment_oos_and_oct10(tmp_path, kwargs, match):
    with pytest.raises(ValueError, match=match):
        list(supplement.checked_events(tape(tmp_path, **kwargs), {}))


def test_truncated_compressed_input_does_not_complete_audit(tmp_path):
    p = tape(tmp_path)
    p.write_bytes(p.read_bytes()[:-8])
    audit = {}
    with pytest.raises(EOFError): list(supplement.checked_events(p, audit))
    assert not audit.get("stream_fully_consumed")


def test_run_registers220_before_only_supplement_replay(tmp_path, monkeypatch):
    source = tape(tmp_path)
    target = tmp_path/"output"
    observed = []

    def fake_replay(events, candidates, signal, config):
        registration = json.loads((target/"registration.json").read_text())
        assert registration["new_trials"] == 0
        assert registration["actual_registered_candidates"] == 220
        assert registration["original_500_will_be_replayed"] is False
        assert candidates == catalog.make_supplementary_candidates()
        assert signal is catalog.signal
        assert config == supplement.execution.ReplayConfig()
        assert not isinstance(events, list)
        list(events)
        observed.append(len(candidates))
        return {"summary": summaries(candidates), "attempts": []}

    monkeypatch.setattr(supplement.execution, "replay_events", fake_replay)
    completion = supplement.run(source, target)
    assert observed == [220]
    assert completion["actual_candidate_scenarios"] == 660
    assert completion["new_trials"] == 0 and completion["formal_oos_result"] is None
    saved = json.loads((target/"replay.json").read_text())
    assert len(saved["summary"]) == 660 and "attempts" not in saved
    with gzip.open(target/"attempts.jsonl.gz", "rt") as handle: assert handle.read() == ""
    assert json.loads((target/"daily.json").read_text()) == []
    with pytest.raises(FileExistsError): supplement.run(source, target)
