"""Offline archive boundary tests: no downloads or private data needed."""
import gzip
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import replay_raw as raw


class RawRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def strict_members():
        return {"recording/bundle-btc/strict/" + name:
            (b"{}" if name == "manifest.json" else gzip.compress(b""))
            for name in raw.STRICT_NAMES}

    def zip(self, members, name="recording.zip"):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as out:
            for key, value in members.items():
                out.writestr(key, value)
        return path

    @staticmethod
    def tar_bytes(members, symlink=None):
        target = io.BytesIO()
        with tarfile.open(fileobj=target, mode="w:gz") as out:
            for name, data in members.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                out.addfile(info, io.BytesIO(data))
            if symlink:
                info = tarfile.TarInfo(symlink)
                info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
                out.addfile(info)
        return target.getvalue()

    def test_zip_with_nested_raw_tgz_extracts_strict_and_raw_gamma_only(self):
        members = self.strict_members()
        gamma = gzip.compress(b'{"conditionId":"synthetic"}\n')
        members["recording/raw/2026-10-04/markets.jsonl.gz"] = gamma
        members["recording/raw/2026-10-04/credentials.txt"] = b"must not extract"
        path = self.zip({"raw.tgz": self.tar_bytes(members), "README.txt": b"ignore"})
        parent = raw.extract_recording(path, self.root / "out")
        self.assertEqual(parent, self.root / "out" / "recording")
        self.assertEqual((parent / "raw/2026-10-04/markets.jsonl.gz").read_bytes(), gamma)
        self.assertEqual(len(list((self.root / "out").rglob("*.*"))), 6)
        self.assertFalse((parent / "raw/2026-10-04/credentials.txt").exists())

    def test_plain_tgz_is_supported(self):
        path = self.root / "recording.tgz"
        path.write_bytes(self.tar_bytes(self.strict_members()))
        parent = raw.extract_recording(path, self.root / "out")
        self.assertTrue((parent / "bundle-btc/strict/manifest.json").exists())

    def test_unsafe_paths_rejected_even_if_not_selected(self):
        for index, path in enumerate(("../escape", "/absolute", "recording\\escape")):
            source = self.zip({**self.strict_members(), path: b"x"}, name=f"unsafe{index}.zip")
            with self.assertRaisesRegex(ValueError, "unsafe archive path"):
                raw.extract_recording(source, self.root / f"out{index}")
        nested = self.zip({"raw.tgz": self.tar_bytes({"../escape": b"x"})}, name="nested.zip")
        with self.assertRaisesRegex(ValueError, "unsafe archive path"):
            raw.extract_recording(nested, self.root / "nested")

    def test_zip_and_nested_tar_links_rejected(self):
        source = self.root / "link.zip"
        with zipfile.ZipFile(source, "w") as out:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            out.writestr(info, "/etc/passwd")
        with self.assertRaisesRegex(ValueError, "links"):
            raw.extract_recording(source, self.root / "out")
        nested = self.zip({"raw.tgz": self.tar_bytes(self.strict_members(), symlink="recording/link")}, name="nested.zip")
        with self.assertRaisesRegex(ValueError, "links"):
            raw.extract_recording(nested, self.root / "nested")

    def test_duplicate_and_expansion_limits_fail_closed(self):
        source = self.root / "duplicate.zip"
        with zipfile.ZipFile(source, "w") as out, warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            out.writestr("recording/bundle-btc/strict/manifest.json", "{}")
            out.writestr("recording/bundle-btc/strict/manifest.json", "{}")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            raw.extract_recording(source, self.root / "dupout")
        source = self.zip(self.strict_members())
        with patch.object(raw, "MAX_EXPANDED", 1), self.assertRaisesRegex(ValueError, "size limit"):
            raw.extract_recording(source, self.root / "large")

    def test_ladder_only_and_incomplete_directory_are_not_btc_backtests(self):
        source = self.zip({"bundle-ladder/strict/manifest.json": b"{}"})
        with self.assertRaisesRegex(ValueError, "BTC strict manifest"):
            raw.extract_recording(source, self.root / "out")
        parent = self.root / "incomplete"; parent.mkdir()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            raw.prepare_raw(parent, self.root / "cache")

    def test_missing_real_manifest_schema_retains_failed_intake_and_null_pnl(self):
        source = self.zip(self.strict_members())
        with self.assertRaisesRegex(ValueError, "raw intake blocked"):
            raw.prepare_raw(source, self.root / "cache")
        failures = list((self.root / "cache").glob("*.pending.*/failed.json"))
        self.assertEqual(len(failures), 1)
        evidence = json.loads(failures[0].read_text())
        self.assertEqual(evidence["status"], "blocked_raw_intake")
        self.assertEqual(evidence["completed_backtests"], 0)
        self.assertIsNone(evidence["pnl"])
        self.assertFalse(list((self.root / "cache").glob("*/normalized.jsonl.gz")))


if __name__ == "__main__":
    unittest.main()
