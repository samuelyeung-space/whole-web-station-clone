"""Audit or replay the registered source-race evaluator from frozen bytes.

This is the dedicated, fail-closed entry point for the 2026-10-09 source-race
freeze.  It deliberately routes through ``frozen_dependency_runner`` instead
of importing ``h_source_race`` from the mutable checkout.  The registered
freeze and its dependency hashes are never rewritten.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import frozen_dependency_runner as frozen_runner


ROOT = Path(__file__).resolve().parent
FREEZE = ROOT / "forward" / "source-race-freeze.json"
SNAPSHOT_ROOT = ROOT / "frozen-dependencies" / "source-race-v1"
ENTRYPOINT = Path("h_source_race.py")
FREEZE_TARGET = Path("forward/source-race-freeze.json")


def audit(report: str | Path) -> int:
    """Verify the entire registered dependency closure without replaying data."""
    return frozen_runner.run(
        root=ROOT,
        freeze_path=FREEZE,
        snapshot_root=SNAPSHOT_ROOT,
        entrypoint=ENTRYPOINT,
        freeze_target=FREEZE_TARGET,
        entry_args=(),
        report_path=Path(report).resolve(),
        audit_only=True,
    )


def replay(
    *,
    archive: str | Path,
    output: str | Path,
    dependency_report: str | Path,
    rows_dir: str | Path | None = None,
    require_formal: bool = False,
    pretty: bool = False,
) -> int:
    """Execute ``h_source_race.py`` from the hash-selected frozen closure."""
    entry_args: list[str] = [
        "--archive", str(Path(archive).resolve()),
        "--out", str(Path(output).resolve()),
    ]
    if rows_dir is not None:
        entry_args.extend(("--rows-dir", str(Path(rows_dir).resolve())))
    if require_formal:
        entry_args.append("--require-formal")
    if pretty:
        entry_args.append("--pretty")
    return frozen_runner.run(
        root=ROOT,
        freeze_path=FREEZE,
        snapshot_root=SNAPSHOT_ROOT,
        entrypoint=ENTRYPOINT,
        freeze_target=FREEZE_TARGET,
        entry_args=entry_args,
        report_path=Path(dependency_report).resolve(),
        audit_only=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    audit_parser = commands.add_parser(
        "audit", help="verify the frozen dependency closure without replaying data",
    )
    audit_parser.add_argument("--report", required=True)

    replay_parser = commands.add_parser(
        "replay", help="run an immutable source-race replay on one strict artifact",
    )
    replay_parser.add_argument("--archive", required=True)
    replay_parser.add_argument("--out", required=True)
    replay_parser.add_argument("--dependency-report", required=True)
    replay_parser.add_argument("--rows-dir")
    replay_parser.add_argument("--require-formal", action="store_true")
    replay_parser.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "audit":
        return audit(args.report)
    return replay(
        archive=args.archive,
        output=args.out,
        dependency_report=args.dependency_report,
        rows_dir=args.rows_dir,
        require_formal=args.require_formal,
        pretty=args.pretty,
    )


if __name__ == "__main__":
    raise SystemExit(main())
