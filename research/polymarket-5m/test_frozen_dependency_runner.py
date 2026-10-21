from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import frozen_dependency_runner as runner


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    snapshot = tmp_path / "snapshot"
    freeze = root / "forward" / "freeze.json"
    frozen_dep = b"VALUE = 'frozen'\n"
    entry = (
        b"import dep\n"
        b"import sys\n"
        b"from pathlib import Path\n"
        b"Path(sys.argv[1]).write_text(dep.VALUE, encoding='utf-8')\n"
    )
    _write(root / "dep.py", b"VALUE = 'drifted'\n")
    _write(snapshot / "dep.py", frozen_dep)
    _write(root / "entry.py", entry)
    payload = {
        "schema": "test-freeze-v1",
        "dependencies_sha256": {
            "dep.py": _sha(frozen_dep),
            "entry.py": _sha(entry),
        },
    }
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    _write(freeze, raw)
    _write(freeze.with_suffix(".sha256"), (_sha(raw) + "\n").encode())
    return root, snapshot, freeze


class FrozenDependencyRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temporary.name)
        self.root, self.snapshot, self.freeze = _fixture(self.tmp_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_audit_selects_only_hash_matching_snapshot_for_drift(self) -> None:
        report = self.tmp_path / "report.json"
        result = runner.run(
            root=self.root,
            freeze_path=self.freeze,
            snapshot_root=self.snapshot,
            entrypoint=Path("entry.py"),
            freeze_target=Path("forward/freeze.json"),
            entry_args=(),
            report_path=report,
            audit_only=True,
        )
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(payload["exit_code"], 0)
        self.assertTrue(payload["runnable"])
        self.assertEqual(payload["unresolved_dependencies"], [])
        self.assertEqual(len(payload["substituted_dependencies"]), 1)
        substituted = payload["substituted_dependencies"][0]
        self.assertEqual(substituted["origin"], "immutable_snapshot")
        self.assertEqual(substituted["path"], "dep.py")
        self.assertEqual(substituted["sha256"], _sha(b"VALUE = 'frozen'\n"))
        self.assertEqual(substituted["snapshot_sha256"], substituted["sha256"])
        self.assertNotEqual(substituted["current_sha256"], substituted["sha256"])

    def test_execution_imports_frozen_bytes_without_editing_checkout(self) -> None:
        report = self.tmp_path / "report.json"
        output = self.tmp_path / "executed.txt"
        result = runner.run(
            root=self.root,
            freeze_path=self.freeze,
            snapshot_root=self.snapshot,
            entrypoint=Path("entry.py"),
            freeze_target=Path("forward/freeze.json"),
            entry_args=(str(output),),
            report_path=report,
            audit_only=False,
        )
        self.assertEqual(result, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "frozen")
        self.assertEqual(
            (self.root / "dep.py").read_text(encoding="utf-8"),
            "VALUE = 'drifted'\n",
        )

    def test_audit_reports_every_missing_hash_matching_dependency(self) -> None:
        (self.snapshot / "dep.py").write_text(
            "VALUE = 'also drifted'\n", encoding="utf-8",
        )
        missing = b"MISSING = True\n"
        payload = json.loads(self.freeze.read_text(encoding="utf-8"))
        payload["dependencies_sha256"]["missing.py"] = _sha(missing)
        raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        _write(self.freeze, raw)
        _write(self.freeze.with_suffix(".sha256"), (_sha(raw) + "\n").encode())
        report = self.tmp_path / "report.json"

        result = runner.run(
            root=self.root,
            freeze_path=self.freeze,
            snapshot_root=self.snapshot,
            entrypoint=Path("entry.py"),
            freeze_target=Path("forward/freeze.json"),
            entry_args=(),
            report_path=report,
            audit_only=True,
        )

        audit = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(result, 2)
        self.assertFalse(audit["runnable"])
        self.assertEqual(
            [row["path"] for row in audit["unresolved_dependencies"]],
            ["dep.py", "missing.py"],
        )
        self.assertIsNotNone(audit["unresolved_dependencies"][0]["current_sha256"])
        self.assertIsNotNone(audit["unresolved_dependencies"][0]["snapshot_sha256"])
        self.assertIsNone(audit["unresolved_dependencies"][1]["current_sha256"])
        self.assertIsNone(audit["unresolved_dependencies"][1]["snapshot_sha256"])

    def test_execution_with_missing_matching_bytes_fails_closed(self) -> None:
        (self.snapshot / "dep.py").write_text(
            "VALUE = 'also drifted'\n", encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError, "no hash-matching dependency bytes: dep.py",
        ):
            runner.run(
                root=self.root,
                freeze_path=self.freeze,
                snapshot_root=self.snapshot,
                entrypoint=Path("entry.py"),
                freeze_target=Path("forward/freeze.json"),
                entry_args=(),
                report_path=None,
                audit_only=False,
            )

    def test_unsafe_paths_are_rejected(self) -> None:
        for unsafe in ("../entry.py", "/entry.py", "dir\\entry.py"):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(
                ValueError, "unsafe entrypoint",
            ):
                runner.run(
                    root=self.root,
                    freeze_path=self.freeze,
                    snapshot_root=self.snapshot,
                    entrypoint=Path(unsafe),
                    freeze_target=Path("forward/freeze.json"),
                    entry_args=(),
                    report_path=None,
                    audit_only=True,
                )


class FrozenSourceRaceClosureTest(unittest.TestCase):
    def test_registered_source_race_freeze_has_complete_dependency_closure(self) -> None:
        root = Path(__file__).resolve().parent
        freeze = root / "forward" / "source-race-freeze.json"
        snapshot = root / "frozen-dependencies" / "source-race-v1"

        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            result = runner.run(
                root=root,
                freeze_path=freeze,
                snapshot_root=snapshot,
                entrypoint=Path("h_source_race.py"),
                freeze_target=Path("forward/source-race-freeze.json"),
                entry_args=(),
                report_path=report,
                audit_only=True,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(
            payload["freeze_sha256"],
            "29ad66236aa0d2927843764970e384cb5de238c405be4e050f6b790a31a1dc63",
        )
        self.assertEqual(len(payload["dependencies"]), 18)
        self.assertTrue(payload["runnable"])
        self.assertEqual(payload["unresolved_dependencies"], [])
        self.assertEqual(
            [row["path"] for row in payload["substituted_dependencies"]],
            ["eu_strict.py", "poly_clob_rec.py"],
        )


if __name__ == "__main__":
    unittest.main()
