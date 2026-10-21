#!/usr/bin/env python3
"""Fail closed when a cumulative upstream report rewrites an existing run.

The source workflow intentionally appends newly published recording runs to several
Markdown reports.  Aggregate rows are expected to change, but a row/note attributed
to an already-seen run ID must remain byte-stable.  A changed historical run is kept
as evidence, but the cumulative report must not be treated as append-only/OOS input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


DEFAULT_REPORTS = (
    "real/latency-test-c.md",
    "real/latency-test-d.md",
    "real/latency-test-f.md",
    "real/latency-test-g.md",
    "real/latency-test-i.md",
    "real/latency-test-j.md",
    "real/recorder-check.md",
)

NOTE_RE = re.compile(r"^-\s+(?:[a-z]+\s+)?(?P<run>\d{8,})\s*:\s*(?P<body>.*)$", re.I)
ROW_RE = re.compile(r"^\|\s*GitHub\s+(?P<run>\d{8,})\s*\|(?P<body>.*)\|\s*$", re.I)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_records(path: Path) -> tuple[dict[str, str], list[str]]:
    records: dict[str, str] = {}
    duplicates: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = " ".join(raw.strip().split())
        match = NOTE_RE.match(line)
        kind = "note"
        if match is None:
            match = ROW_RE.match(line)
            kind = "row"
        if match is None:
            continue
        key = f"{kind}:{match.group('run')}"
        if key in records:
            duplicates.append(key)
        records[key] = line
    return records, sorted(set(duplicates))


def audit(old_root: Path, new_root: Path, reports: tuple[str, ...]) -> dict:
    files = []
    changed = []
    removed = []
    duplicate_keys = []
    added = []
    for rel in reports:
        old_path, new_path = old_root / rel, new_root / rel
        if not old_path.is_file() or not new_path.is_file():
            files.append({"path": rel, "status": "missing", "old_exists": old_path.is_file(),
                          "new_exists": new_path.is_file()})
            continue
        old, old_dups = run_records(old_path)
        new, new_dups = run_records(new_path)
        for key in sorted(old.keys() & new.keys()):
            if old[key] != new[key]:
                changed.append({"path": rel, "key": key, "old": old[key], "new": new[key]})
        for key in sorted(old.keys() - new.keys()):
            removed.append({"path": rel, "key": key, "old": old[key]})
        for key in sorted(new.keys() - old.keys()):
            added.append({"path": rel, "key": key, "new": new[key]})
        duplicate_keys.extend({"path": rel, "side": "old", "key": key} for key in old_dups)
        duplicate_keys.extend({"path": rel, "side": "new", "key": key} for key in new_dups)
        files.append({"path": rel, "status": "checked", "old_sha256": sha256(old_path),
                      "new_sha256": sha256(new_path), "old_records": len(old),
                      "new_records": len(new)})
    missing = [item for item in files if item["status"] == "missing"]
    passed = not changed and not removed and not duplicate_keys and not missing
    return {
        "schema": "btc5m-upstream-report-delta-audit-v1",
        "passed": passed,
        "interpretation": (
            "append_only_per_run" if passed else
            "historical_run_rewritten_fail_closed_not_rank7_or_oos_input"
        ),
        "files": files,
        "changed_existing_records": changed,
        "removed_existing_records": removed,
        "duplicate_record_keys": duplicate_keys,
        "added_records": added,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", required=True, type=Path)
    parser.add_argument("--new-root", required=True, type=Path)
    parser.add_argument("--report", action="append", dest="reports")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.old_root, args.new_root, tuple(args.reports or DEFAULT_REPORTS))
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
