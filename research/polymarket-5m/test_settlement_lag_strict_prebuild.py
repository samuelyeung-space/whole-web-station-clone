from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import settlement_lag_strict_prebuild as builder


def test_build_day_reuses_the_shared_strict_builder(monkeypatch, tmp_path):
    day = builder.previous_utc_day()
    calls = []

    def run_phase(day_arg, phase, data, poly, root, *, s3, bucket):
        assert builder.strict_prebuild.eu_strict is builder.settlement_lag_eu_strict
        calls.append((day_arg, phase, Path(data), Path(poly), Path(root), s3, bucket))
        if phase == "finalize":
            (Path(root) / day_arg / "artifact").mkdir(parents=True)
            return {"status": "complete"}
        return {"status": f"{phase}_ready"}

    monkeypatch.setattr(builder.strict_prebuild, "run_phase", run_phase)
    marker = object()
    result = builder.build_day(
        day,
        data_dir=tmp_path / "data",
        poly_dir=tmp_path / "poly",
        root=tmp_path / "strict",
        s3=marker,
        bucket="bucket",
    )

    assert result["status"] == "complete"
    assert builder.strict_prebuild.eu_strict is not builder.settlement_lag_eu_strict
    assert [call[1] for call in calls] == ["source", "clob", "finalize"]
    assert all(call[5] is marker and call[6] == "bucket" for call in calls)


def test_build_day_rejects_an_unclosed_utc_day(tmp_path):
    tomorrow = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)).strftime(
        "%Y%m%d"
    )
    with pytest.raises(ValueError, match="closed UTC day"):
        builder.build_day(
            tomorrow,
            data_dir=tmp_path / "data",
            poly_dir=tmp_path / "poly",
            root=tmp_path / "strict",
            s3=object(),
        )


def test_default_builder_targets_oldest_unadmitted_day(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setattr(
        builder.settlement_lag_forward,
        "load_freeze",
        lambda: ({}, "f" * 64),
    )

    def expected(state_root, freeze_sha256):
        observed.append((Path(state_root), freeze_sha256))
        return "20261015"

    monkeypatch.setattr(builder.settlement_lag_daily, "next_expected_day", expected)

    assert builder.next_expected_day(tmp_path / "state") == "20261015"
    assert observed == [(tmp_path / "state", "f" * 64)]
