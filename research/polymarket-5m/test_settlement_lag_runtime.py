from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
import settlement_lag_forward
import settlement_lag_runtime as runtime


class _FakeDistribution:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.version = "1.0"
        self.metadata = {"Name": "fakepkg"}
        self.files = [
            runtime.importlib.metadata.PackagePath("fakepkg.py"),
            runtime.importlib.metadata.PackagePath("fakepkg-1.0.dist-info/RECORD"),
        ]

    def locate_file(self, path: object) -> Path:
        return self.root / str(path)


class _Body:
    def __init__(self, value: bytes) -> None:
        self.stream = io.BytesIO(value)

    def read(self) -> bytes:
        return self.stream.read()


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentLength, ContentType, IfNoneMatch):
        del Bucket, ContentType
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise RuntimeError("precondition failed")
        raw = bytes(Body)
        assert len(raw) == ContentLength
        self.objects[Key] = raw

    def get_object(self, *, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[Key])}


def test_captured_runtime_lock_verifies_and_detects_drift(tmp_path: Path) -> None:
    lock = runtime.capture_runtime(["pytest"])
    path = tmp_path / "runtime-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    assert runtime.verify_runtime(path) == lock

    lock["distributions"]["pytest"]["version"] = "0.0.0"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(RuntimeError, match="runtime lock mismatch"):
        runtime.verify_runtime(path)


def test_formal_runtime_lock_contains_exact_research_host_measurements() -> None:
    lock = runtime.load_lock(runtime.DEFAULT_LOCK_PATH)

    assert lock["python"] == {
        "version": "3.12.3",
        "implementation": "CPython",
        "build": ["main", "Aug 31 2026 10:18:26"],
        "executable_sha256": (
            "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"
        ),
    }
    assert lock["platform"] == {
        "system": "Linux",
        "release": "7.0.0-1013-aws",
        "machine": "x86_64",
        "libc": ["glibc", "2.39"],
    }
    assert len(lock["distributions"]) == 23
    assert lock["distributions"]["scipy"] == {
        "version": "1.17.1",
        "record_sha256": (
            "cf88c01b64998c08fee7fc684ba0dcfa292a2df89efc009288ae3f2ffd549ce2"
        ),
    }


def test_lock_rejects_missing_record_hash(tmp_path: Path) -> None:
    lock = runtime.capture_runtime(["pytest"])
    del lock["distributions"]["pytest"]["record_sha256"]
    path = tmp_path / "runtime-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ValueError, match="runtime lock schema"):
        runtime.load_lock(path)


def test_runtime_verifier_hashes_every_record_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "fakepkg.py"
    package.write_text("VALUE = 1\n", encoding="utf-8")
    dist_info = tmp_path / "fakepkg-1.0.dist-info"
    dist_info.mkdir()
    digest = base64.urlsafe_b64encode(hashlib.sha256(package.read_bytes()).digest())
    encoded = digest.rstrip(b"=").decode("ascii")
    (dist_info / "RECORD").write_text(
        f"fakepkg.py,sha256={encoded},{package.stat().st_size}\n"
        "fakepkg-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    distribution = _FakeDistribution(tmp_path)
    monkeypatch.setattr(
        runtime.importlib.metadata,
        "distribution",
        lambda _name: distribution,
    )
    lock = runtime.capture_runtime(["fakepkg"])
    path = tmp_path / "runtime-lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert runtime.verify_runtime(path) == lock

    package.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="installed file hash mismatch"):
        runtime.verify_runtime(path)


def test_protocol_anchor_binds_freeze_dependency_root_and_remote_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = {
        "formal_holdout_start": "2026-10-15T00:00:00Z",
        "dependencies_sha256": {"a.py": "a" * 64, "b.py": "b" * 64},
    }
    monkeypatch.setattr(
        settlement_lag_forward,
        "load_freeze",
        lambda _path: (frozen, "f" * 64),
    )
    anchor = runtime.build_protocol_anchor(
        tmp_path / "freeze.json",
        source_commit="c" * 40,
        source_tag="family37-protocol-v1",
    )
    path = tmp_path / "anchor.json"
    path.write_bytes(runtime._canonical_bytes(anchor))
    s3 = _FakeS3()

    runtime.publish_protocol_anchor(anchor, s3=s3, bucket="bucket", key="anchor")
    assert runtime.verify_protocol_anchor(
        freeze_path=tmp_path / "freeze.json",
        anchor_path=path,
        s3=s3,
        bucket="bucket",
        key="anchor",
    ) == anchor

    drifted = {**anchor, "source_commit": "d" * 40}
    with pytest.raises(RuntimeError, match="already exists with drift"):
        runtime.publish_protocol_anchor(
            drifted,
            s3=s3,
            bucket="bucket",
            key="anchor",
        )


def test_all_family37_services_verify_runtime_before_start() -> None:
    systemd = Path(__file__).with_name("systemd")
    services = sorted(systemd.glob("settlement-lag-*.service"))
    assert services
    command = (
        "ExecStartPre=/home/ubuntu/rec/venv/bin/python "
        "/home/ubuntu/rec/h-race/settlement_lag_runtime.py verify "
        "--lock /home/ubuntu/rec/h-race/settlement-lag-runtime-lock.json"
    )
    for service in services:
        text = service.read_text(encoding="utf-8")
        assert command in text
        assert (
            "settlement_lag_runtime.py verify-protocol"
            in text
        )

    metadata_service = (systemd / "settlement-lag-meta-recorder.service").read_text(
        encoding="utf-8"
    )
    assert "--clob-root /home/ubuntu/rec/data_poly" in metadata_service
    assert "--clob-service poly-clob-recorder.service" in metadata_service
    assert "ExecStart=/home/ubuntu/rec/venv/bin/python " in metadata_service

    daily_service = (systemd / "settlement-lag-daily.service").read_text(
        encoding="utf-8"
    )
    assert "settlement-lag-strict-artifact.service" in daily_service
    assert "strict-artifact-daily.service" not in daily_service

    for timer_name in (
        "settlement-lag-daily.timer",
        "settlement-lag-strict-artifact.timer",
    ):
        assert "OnUnitInactiveSec=1h" in (systemd / timer_name).read_text(
            encoding="utf-8"
        )
