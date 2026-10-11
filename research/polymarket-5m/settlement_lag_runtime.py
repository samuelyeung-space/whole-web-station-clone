"""Verify the exact interpreter and distributions used by Family 37."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA = "settlement-lag-runtime-lock-v1"
ANCHOR_SCHEMA = "settlement-lag-protocol-anchor-v1"
DEFAULT_LOCK_PATH = Path(__file__).with_name("settlement-lag-runtime-lock.json")
DEFAULT_FREEZE_PATH = Path(__file__).with_name("forward") / "settlement-lag-freeze.json"
DEFAULT_ANCHOR_PATH = Path(__file__).with_name("forward") / "settlement-lag-anchor.json"
DEFAULT_ANCHOR_BUCKET = "sgk90-rec-archive-114469424813-euw1"
DEFAULT_ANCHOR_KEY = "formal/settlement-lag-v1/protocol-anchor.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _record_path(distribution: importlib.metadata.Distribution) -> Path:
    candidates = [
        item
        for item in distribution.files or ()
        if item.name == "RECORD" and item.parent.name.endswith(".dist-info")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"distribution RECORD identity is ambiguous: {distribution.metadata['Name']}"
        )
    path = Path(str(distribution.locate_file(candidates[0])))
    if not path.is_file():
        raise RuntimeError(
            f"distribution RECORD is absent: {distribution.metadata['Name']}"
        )
    return path


def _record_sha256(distribution: importlib.metadata.Distribution) -> str:
    return _sha256(_record_path(distribution))


def _record_digest(value: str, distribution_name: str) -> bytes:
    algorithm, separator, encoded = value.partition("=")
    if not separator or algorithm != "sha256" or not encoded:
        raise RuntimeError(
            f"distribution RECORD hash is not sha256: {distribution_name}"
        )
    try:
        digest = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(
            f"distribution RECORD hash is malformed: {distribution_name}"
        ) from exc
    if len(digest) != hashlib.sha256().digest_size:
        raise RuntimeError(
            f"distribution RECORD hash is malformed: {distribution_name}"
        )
    return digest


def _verify_record_files(distribution: importlib.metadata.Distribution) -> None:
    name = str(distribution.metadata["Name"])
    record = _record_path(distribution)
    verified = 0
    try:
        with record.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            for row in rows:
                if len(row) != 3:
                    raise RuntimeError(f"distribution RECORD is malformed: {name}")
                relative, hash_value, size_value = row
                if not hash_value:
                    continue
                expected = _record_digest(hash_value, name)
                installed = Path(
                    str(
                        distribution.locate_file(
                            importlib.metadata.PackagePath(relative)
                        )
                    )
                )
                if not installed.is_file():
                    raise RuntimeError(
                        f"distribution installed file is absent: {name}: {relative}"
                    )
                if size_value:
                    try:
                        expected_size = int(size_value)
                    except ValueError as exc:
                        raise RuntimeError(
                            f"distribution RECORD size is malformed: {name}: {relative}"
                        ) from exc
                    if installed.stat().st_size != expected_size:
                        raise RuntimeError(
                            f"distribution installed file size mismatch: {name}: {relative}"
                        )
                if bytes.fromhex(_sha256(installed)) != expected:
                    raise RuntimeError(
                        f"distribution installed file hash mismatch: {name}: {relative}"
                    )
                verified += 1
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"distribution RECORD cannot be verified: {name}") from exc
    if verified == 0:
        raise RuntimeError(f"distribution RECORD has no hashed files: {name}")


def capture_runtime(distributions: Iterable[str]) -> dict[str, Any]:
    locked: dict[str, dict[str, str]] = {}
    for name in sorted(set(distributions), key=str.casefold):
        distribution = importlib.metadata.distribution(name)
        _verify_record_files(distribution)
        locked[name] = {
            "version": distribution.version,
            "record_sha256": _record_sha256(distribution),
        }
    return {
        "schema": SCHEMA,
        "record_hash_algorithm": "sha256-record-file-v1",
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "build": list(platform.python_build()),
            "executable_sha256": _sha256(Path(sys.executable)),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "distributions": locked,
    }


def _valid_hash(value: object) -> bool:
    return _SHA256_RE.fullmatch(str(value or "")) is not None


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()


def _dependency_root(dependencies: Mapping[str, Any]) -> str:
    normalized = {str(name): str(digest) for name, digest in dependencies.items()}
    if (
        not normalized
        or any(not name or not _valid_hash(digest) for name, digest in normalized.items())
    ):
        raise ValueError("Family37 dependency closure is invalid")
    return hashlib.sha256(_canonical_bytes(normalized)).hexdigest()


def build_protocol_anchor(
    freeze_path: str | Path,
    *,
    source_commit: str,
    source_tag: str,
) -> dict[str, Any]:
    import settlement_lag_forward

    frozen, freeze_sha256 = settlement_lag_forward.load_freeze(freeze_path)
    commit = str(source_commit)
    tag = str(source_tag)
    if _COMMIT_RE.fullmatch(commit) is None or not tag or any(ch.isspace() for ch in tag):
        raise ValueError("Family37 source anchor identity is invalid")
    dependencies = frozen.get("dependencies_sha256")
    if not isinstance(dependencies, Mapping):
        raise ValueError("Family37 dependency closure is invalid")
    return {
        "schema": ANCHOR_SCHEMA,
        "freeze_sha256": freeze_sha256,
        "dependency_root_sha256": _dependency_root(dependencies),
        "formal_holdout_start": str(frozen["formal_holdout_start"]),
        "source_commit": commit,
        "source_tag": tag,
    }


def load_protocol_anchor(path: str | Path = DEFAULT_ANCHOR_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Family37 protocol anchor") from exc
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema",
            "freeze_sha256",
            "dependency_root_sha256",
            "formal_holdout_start",
            "source_commit",
            "source_tag",
        }
        or payload.get("schema") != ANCHOR_SCHEMA
        or not _valid_hash(payload.get("freeze_sha256"))
        or not _valid_hash(payload.get("dependency_root_sha256"))
        or not isinstance(payload.get("formal_holdout_start"), str)
        or _COMMIT_RE.fullmatch(str(payload.get("source_commit") or "")) is None
        or not isinstance(payload.get("source_tag"), str)
        or not payload["source_tag"]
        or any(ch.isspace() for ch in payload["source_tag"])
    ):
        raise ValueError("Family37 protocol anchor schema drift")
    return payload


def _remote_anchor(s3: Any, bucket: str, key: str) -> dict[str, Any]:
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError("Family37 remote protocol anchor is unavailable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Family37 remote protocol anchor is invalid")
    return payload


def publish_protocol_anchor(
    anchor: Mapping[str, Any],
    *,
    s3: Any,
    bucket: str = DEFAULT_ANCHOR_BUCKET,
    key: str = DEFAULT_ANCHOR_KEY,
) -> dict[str, Any]:
    payload = dict(anchor)
    raw = _canonical_bytes(payload)
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=raw,
            ContentLength=len(raw),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except Exception:
        if _remote_anchor(s3, bucket, key) != payload:
            raise RuntimeError("Family37 protocol anchor already exists with drift")
    return payload


def verify_protocol_anchor(
    *,
    freeze_path: str | Path = DEFAULT_FREEZE_PATH,
    anchor_path: str | Path = DEFAULT_ANCHOR_PATH,
    s3: Any,
    bucket: str = DEFAULT_ANCHOR_BUCKET,
    key: str = DEFAULT_ANCHOR_KEY,
) -> dict[str, Any]:
    local = load_protocol_anchor(anchor_path)
    expected = build_protocol_anchor(
        freeze_path,
        source_commit=str(local["source_commit"]),
        source_tag=str(local["source_tag"]),
    )
    if local != expected or _remote_anchor(s3, bucket, key) != expected:
        raise RuntimeError("Family37 protocol anchor mismatch")
    return expected


def protocol_identity(
    *,
    freeze_path: str | Path = DEFAULT_FREEZE_PATH,
    anchor_path: str | Path = DEFAULT_ANCHOR_PATH,
    lock_path: str | Path = DEFAULT_LOCK_PATH,
) -> dict[str, str]:
    import settlement_lag_forward

    _frozen, freeze_sha256 = settlement_lag_forward.load_freeze(freeze_path)
    anchor = load_protocol_anchor(anchor_path)
    if anchor["freeze_sha256"] != freeze_sha256:
        raise RuntimeError("Family37 local protocol anchor mismatch")
    return {
        "protocol_freeze_sha256": freeze_sha256,
        "protocol_anchor_sha256": _sha256(Path(anchor_path)),
        "runtime_lock_sha256": _sha256(Path(lock_path)),
    }


def current_process_identity(
    *,
    service: str,
    unit_path: str | Path,
    script_path: str | Path,
    freeze_path: str | Path = DEFAULT_FREEZE_PATH,
    anchor_path: str | Path = DEFAULT_ANCHOR_PATH,
    lock_path: str | Path = DEFAULT_LOCK_PATH,
) -> dict[str, Any]:
    unit = Path(unit_path).resolve()
    script = Path(script_path).resolve()
    executable = Path(sys.executable).resolve()
    proc_cmdline = Path("/proc/self/cmdline")
    if proc_cmdline.is_file():
        argv = [
            value.decode("utf-8")
            for value in proc_cmdline.read_bytes().rstrip(b"\0").split(b"\0")
        ]
    else:
        argv = [sys.executable, *sys.argv]
    return {
        "recorder_service": service,
        "recorder_unit_path": str(unit),
        "recorder_unit_sha256": _sha256(unit),
        "process_executable_path": str(executable),
        "process_executable_sha256": _sha256(executable),
        "process_cwd": str(Path.cwd().resolve()),
        "process_argv": argv,
        "process_script_path": str(script),
        "process_script_sha256": _sha256(script),
        **protocol_identity(
            freeze_path=freeze_path,
            anchor_path=anchor_path,
            lock_path=lock_path,
        ),
    }


def load_lock(path: str | Path = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Family37 runtime lock") from exc
    python = payload.get("python") if isinstance(payload, Mapping) else None
    system = payload.get("platform") if isinstance(payload, Mapping) else None
    distributions = (
        payload.get("distributions") if isinstance(payload, Mapping) else None
    )
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema",
            "record_hash_algorithm",
            "python",
            "platform",
            "distributions",
        }
        or payload.get("schema") != SCHEMA
        or payload.get("record_hash_algorithm") != "sha256-record-file-v1"
        or not isinstance(python, Mapping)
        or set(python) != {"version", "implementation", "build", "executable_sha256"}
        or not isinstance(python.get("version"), str)
        or not isinstance(python.get("implementation"), str)
        or not isinstance(python.get("build"), list)
        or len(python["build"]) != 2
        or any(not isinstance(value, str) for value in python["build"])
        or not _valid_hash(python.get("executable_sha256"))
        or not isinstance(system, Mapping)
        or set(system) != {"system", "release", "machine", "libc"}
        or any(
            not isinstance(system.get(key), str)
            for key in ("system", "release", "machine")
        )
        or not isinstance(system.get("libc"), list)
        or len(system["libc"]) != 2
        or any(not isinstance(value, str) for value in system["libc"])
        or not isinstance(distributions, Mapping)
        or not distributions
    ):
        raise ValueError("Family37 runtime lock schema drift")
    for name, row in distributions.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(row, Mapping)
            or set(row) != {"version", "record_sha256"}
            or not isinstance(row.get("version"), str)
            or not _valid_hash(row.get("record_sha256"))
        ):
            raise ValueError("Family37 runtime lock schema drift")
    return payload


def verify_runtime(path: str | Path = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    expected = load_lock(path)
    actual = capture_runtime(expected["distributions"])
    if actual != expected:
        sections = [
            key
            for key in ("python", "platform", "distributions")
            if actual.get(key) != expected.get(key)
        ]
        raise RuntimeError("Family37 runtime lock mismatch: " + ", ".join(sections))
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("verify", "capture", "verify-protocol", "publish-anchor"),
    )
    parser.add_argument("--lock", default=str(DEFAULT_LOCK_PATH))
    parser.add_argument("--freeze", default=str(DEFAULT_FREEZE_PATH))
    parser.add_argument("--anchor", default=str(DEFAULT_ANCHOR_PATH))
    parser.add_argument("--bucket", default=DEFAULT_ANCHOR_BUCKET)
    parser.add_argument("--key", default=DEFAULT_ANCHOR_KEY)
    parser.add_argument("--source-commit")
    parser.add_argument("--source-tag")
    parser.add_argument("--distribution", action="append", default=[])
    args = parser.parse_args()
    if args.command == "verify":
        result = verify_runtime(args.lock)
    elif args.command == "capture":
        if not args.distribution:
            parser.error("capture requires at least one --distribution")
        result = capture_runtime(args.distribution)
    else:
        import boto3

        s3 = boto3.client("s3", region_name="eu-west-1")
        if args.command == "verify-protocol":
            result = verify_protocol_anchor(
                freeze_path=args.freeze,
                anchor_path=args.anchor,
                s3=s3,
                bucket=args.bucket,
                key=args.key,
            )
        else:
            if not args.source_commit or not args.source_tag:
                parser.error("publish-anchor requires --source-commit and --source-tag")
            result = build_protocol_anchor(
                args.freeze,
                source_commit=args.source_commit,
                source_tag=args.source_tag,
            )
            anchor_path = Path(args.anchor)
            if anchor_path.exists():
                if load_protocol_anchor(anchor_path) != result:
                    raise RuntimeError("Family37 local protocol anchor already drifted")
            else:
                anchor_path.write_bytes(_canonical_bytes(result))
            publish_protocol_anchor(
                result,
                s3=s3,
                bucket=args.bucket,
                key=args.key,
            )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
