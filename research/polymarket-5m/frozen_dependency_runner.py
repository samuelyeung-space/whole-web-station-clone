"""Run a frozen evaluator from hash-verified dependency bytes.

The adapter does not edit the registered freeze or the working tree.  It creates a
temporary import root containing exactly the dependency bytes named by the freeze,
using the current checkout when it still matches and an explicit immutable snapshot
only when the checkout has drifted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


_DIGEST = re.compile(r"[0-9a-f]{64}")
_REPORT_SCHEMA = "frozen-dependency-runner-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: object, label: str) -> Path:
    text = str(value)
    posix = PurePosixPath(text)
    if (
        not text
        or posix.is_absolute()
        or ".." in posix.parts
        or "\\" in text
        or str(posix) != text
    ):
        raise ValueError(f"unsafe {label}: {text!r}")
    return Path(*posix.parts)


def _read_freeze(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    fingerprint = path.with_suffix(".sha256")
    expected = fingerprint.read_text(encoding="ascii").strip()
    if _DIGEST.fullmatch(expected) is None or actual != expected:
        raise ValueError("freeze fingerprint drift")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("freeze must be a JSON object")
    dependencies = payload.get("dependencies_sha256")
    if not isinstance(dependencies, Mapping) or not dependencies:
        raise ValueError("freeze has no dependency hashes")
    return payload, actual


def _select_dependencies(
    root: Path,
    snapshot_root: Path,
    dependencies: Mapping[str, object],
) -> tuple[list[dict[str, str]], list[tuple[Path, Path]]]:
    audit: list[dict[str, str]] = []
    selected: list[tuple[Path, Path]] = []
    for raw_name in sorted(dependencies):
        relative = _safe_relative(raw_name, "dependency path")
        expected = str(dependencies[raw_name]).lower()
        if _DIGEST.fullmatch(expected) is None:
            raise ValueError(f"invalid dependency digest: {raw_name}")
        current = root / relative
        snapshot = snapshot_root / relative
        if current.is_file() and _sha256(current) == expected:
            source = current
            origin = "current_checkout"
        elif snapshot.is_file() and _sha256(snapshot) == expected:
            source = snapshot
            origin = "immutable_snapshot"
        else:
            raise ValueError(f"no hash-matching dependency bytes: {raw_name}")
        audit.append({
            "path": str(PurePosixPath(*relative.parts)),
            "sha256": expected,
            "origin": origin,
        })
        selected.append((relative, source))
    return audit, selected


def _copy(path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)


def _materialize(
    destination: Path,
    root: Path,
    freeze_path: Path,
    selected: Sequence[tuple[Path, Path]],
) -> None:
    for relative, source in selected:
        _copy(source, destination / relative)
        fingerprint = source.with_suffix(".sha256")
        if fingerprint.is_file():
            _copy(fingerprint, (destination / relative).with_suffix(".sha256"))

    relative_freeze = freeze_path.relative_to(root)
    _copy(freeze_path, destination / relative_freeze)
    _copy(
        freeze_path.with_suffix(".sha256"),
        (destination / relative_freeze).with_suffix(".sha256"),
    )


def _write_report(path: Path | None, report: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    *,
    root: Path,
    freeze_path: Path,
    snapshot_root: Path,
    entrypoint: Path,
    entry_args: Sequence[str],
    report_path: Path | None,
    audit_only: bool,
) -> int:
    root = root.resolve()
    freeze_path = freeze_path.resolve()
    snapshot_root = snapshot_root.resolve()
    if freeze_path.parent != root and root not in freeze_path.parents:
        raise ValueError("freeze must be inside root")
    entrypoint = _safe_relative(entrypoint, "entrypoint")
    frozen, freeze_sha256 = _read_freeze(freeze_path)
    audit, selected = _select_dependencies(
        root, snapshot_root, frozen["dependencies_sha256"],
    )
    entry_hashes = {
        row["path"]: row for row in audit
    }
    entry_key = str(PurePosixPath(*entrypoint.parts))
    if entry_key not in entry_hashes:
        raise ValueError("entrypoint is not a frozen dependency")

    report: dict[str, Any] = {
        "schema": _REPORT_SCHEMA,
        "freeze_sha256": freeze_sha256,
        "entrypoint": entry_key,
        "dependencies": audit,
        "substituted_dependencies": [
            row for row in audit if row["origin"] == "immutable_snapshot"
        ],
        "audit_only": audit_only,
        "exit_code": None,
    }
    if audit_only:
        report["exit_code"] = 0
        _write_report(report_path, report)
        return 0

    with tempfile.TemporaryDirectory(prefix="frozen-evaluator-") as temporary:
        materialized = Path(temporary)
        _materialize(materialized, root, freeze_path, selected)
        environment = dict(os.environ)
        prior_python_path = environment.get("PYTHONPATH")
        search = [str(materialized), str(root)]
        if prior_python_path:
            search.append(prior_python_path)
        environment["PYTHONPATH"] = os.pathsep.join(search)
        result = subprocess.run(
            [sys.executable, str(materialized / entrypoint), *entry_args],
            cwd=materialized,
            env=environment,
            check=False,
        )
        report["exit_code"] = result.returncode
        _write_report(report_path, report)
        return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--entrypoint", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("entry_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    entry_args = list(args.entry_args)
    if entry_args[:1] == ["--"]:
        entry_args.pop(0)
    raise SystemExit(run(
        root=args.root,
        freeze_path=args.freeze,
        snapshot_root=args.snapshot_root,
        entrypoint=args.entrypoint,
        entry_args=entry_args,
        report_path=args.report,
        audit_only=args.audit_only,
    ))


if __name__ == "__main__":
    main()
