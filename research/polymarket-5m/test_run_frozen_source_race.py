from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_frozen_source_race as launcher


class FrozenSourceRaceLauncherTest(unittest.TestCase):
    def test_audit_uses_registered_closure_without_entry_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            launcher.frozen_runner, "run", return_value=0,
        ) as mocked:
            report = Path(temporary) / "dependency.json"
            result = launcher.audit(report)

        self.assertEqual(result, 0)
        mocked.assert_called_once_with(
            root=launcher.ROOT,
            freeze_path=launcher.FREEZE,
            snapshot_root=launcher.SNAPSHOT_ROOT,
            entrypoint=launcher.ENTRYPOINT,
            freeze_target=launcher.FREEZE_TARGET,
            entry_args=(),
            report_path=report.resolve(),
            audit_only=True,
        )

    def test_replay_normalizes_external_paths_and_forwards_formal_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            launcher.frozen_runner, "run", return_value=7,
        ) as mocked:
            base = Path(temporary)
            result = launcher.replay(
                archive=base / "strict",
                output=base / "result.json",
                dependency_report=base / "dependency.json",
                rows_dir=base / "rows",
                require_formal=True,
                pretty=True,
            )

        self.assertEqual(result, 7)
        call = mocked.call_args.kwargs
        self.assertEqual(call["root"], launcher.ROOT)
        self.assertEqual(call["freeze_path"], launcher.FREEZE)
        self.assertEqual(call["snapshot_root"], launcher.SNAPSHOT_ROOT)
        self.assertEqual(call["entrypoint"], Path("h_source_race.py"))
        self.assertEqual(call["freeze_target"], Path("forward/source-race-freeze.json"))
        self.assertEqual(call["report_path"], (base / "dependency.json").resolve())
        self.assertFalse(call["audit_only"])
        self.assertEqual(call["entry_args"], [
            "--archive", str((base / "strict").resolve()),
            "--out", str((base / "result.json").resolve()),
            "--rows-dir", str((base / "rows").resolve()),
            "--require-formal",
            "--pretty",
        ])

    def test_cli_audit_propagates_fail_closed_status(self) -> None:
        with patch.object(launcher, "audit", return_value=2) as mocked:
            result = launcher.main(["audit", "--report", "closure.json"])
        self.assertEqual(result, 2)
        mocked.assert_called_once_with("closure.json")


if __name__ == "__main__":
    unittest.main()
