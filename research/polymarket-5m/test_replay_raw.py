"""Offline archive boundary tests: no downloads or private data needed."""
from contextlib import contextmanager
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

    @contextmanager
    def normalization_pipeline(self, defect=None):
        """Small producer fixture; verify admission without replaying markets."""
        header = {'type': 'dataset', 'dataset_id': 'synthetic-intake', 'split': 'development'}
        rows = [header, {'type': 'book', 'recv_ms': 1000}]

        def project(source, destination):
            destination.mkdir()
            (destination/'manifest.json').write_text('{}')

        def normalize(parent, spot, auditpath, output):
            content = gzip.compress(('\n'.join(json.dumps(r) for r in rows)+'\n').encode())
            if defect == 'truncated':
                content = content[:-8]
            output.write_bytes(content)
            evidence = {'counts': {'book': 1}, 'header': dict(header), 'sha256': raw.sha(output)}
            if defect == 'counts':
                evidence['counts']['book'] = 2
            elif defect == 'header':
                evidence['header']['dataset_id'] = 'wrong-dataset'
            return evidence

        with patch('historical_stream_audit.audit', return_value={}), \
                patch('spot_stream_projection.project', side_effect=project), \
                patch('historical_spot_development.normalize', side_effect=normalize) as producer:
            yield producer

    @staticmethod
    def refresh_cached_hash(destination):
        manifest_path = destination/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['normalized_sha256'] = raw.sha(destination/'normalized.jsonl.gz')
        manifest_path.write_text(json.dumps(manifest))

    def test_valid_new_tape_is_checked_and_cache_reused(self):
        source = self.zip(self.strict_members())
        with self.normalization_pipeline() as producer:
            first = raw.prepare_raw(source, self.root/'cache')
            again = raw.prepare_raw(source, self.root/'cache')
            self.assertFalse(first['cache_hit'])
            self.assertTrue(again['cache_hit'])
            self.assertEqual(producer.call_count, 1)
            self.assertEqual(first['normalized_sha256'], again['normalized_sha256'])
            verified = raw.validate_normalized(first['normalized_path'], first['normalization'])
            self.assertEqual(verified['records'], 2)
            self.assertEqual(verified['body_counts'], {'book': 1})
            self.assertTrue(verified['gzip_eof_crc_verified'])

    def test_cached_truncated_gzip_with_matching_hash_fails_without_overwrite(self):
        source = self.zip(self.strict_members())
        with self.normalization_pipeline() as producer:
            first = raw.prepare_raw(source, self.root/'cache')
            tape = Path(first['normalized_path'])
            truncated = tape.read_bytes()[:-8]
            tape.write_bytes(truncated)
            self.refresh_cached_hash(tape.parent)
            manifest_before = (tape.parent/'manifest.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'raw cache integrity blocked'):
                raw.prepare_raw(source, self.root/'cache')
            self.assertEqual(producer.call_count, 1)
            self.assertEqual(tape.read_bytes(), truncated)
            self.assertEqual((tape.parent/'manifest.json').read_bytes(), manifest_before)
            failures = list((self.root/'cache').glob('*.invalid.*/failed.json'))
            self.assertEqual(len(failures), 1)
            diagnostic = json.loads(failures[0].read_text())
            self.assertEqual(diagnostic['status'], 'blocked_raw_cache_integrity')
            self.assertIsNone(diagnostic['pnl'])
            self.assertEqual(diagnostic['completed_backtests'], 0)

    def test_cached_count_and_header_mismatches_fail_closed(self):
        source = self.zip(self.strict_members())
        for defect in ('counts', 'header'):
            with self.subTest(defect=defect), self.normalization_pipeline() as producer:
                cache = self.root/defect
                first = raw.prepare_raw(source, cache)
                destination = Path(first['normalized_path']).parent
                manifest_path = destination/'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                if defect == 'counts':
                    manifest['normalization']['counts']['book'] = 3
                else:
                    manifest['normalization']['header']['dataset_id'] = 'wrong-dataset'
                manifest_path.write_text(json.dumps(manifest))
                before = manifest_path.read_bytes()
                with self.assertRaisesRegex(ValueError, f'{"body counts" if defect == "counts" else "dataset header"} mismatch'):
                    raw.prepare_raw(source, cache)
                self.assertEqual(producer.call_count, 1)
                self.assertEqual(manifest_path.read_bytes(), before)
                self.assertEqual(len(list(cache.glob('*.invalid.*/failed.json'))), 1)

    def test_bad_new_outputs_retain_inputs_and_never_publish(self):
        source = self.zip(self.strict_members())
        for defect in ('truncated', 'counts', 'header'):
            with self.subTest(defect=defect), self.normalization_pipeline(defect):
                cache = self.root/defect
                with self.assertRaisesRegex(ValueError, 'raw intake blocked'):
                    raw.prepare_raw(source, cache)
                failures = list(cache.glob('*.pending.*/failed.json'))
                self.assertEqual(len(failures), 1)
                stage = failures[0].parent
                self.assertTrue((stage/'spot/manifest.json').exists())
                self.assertTrue((stage/'extracted').exists())
                self.assertTrue((stage/'normalization.json').exists())
                self.assertFalse((stage/'manifest.json').exists())
                self.assertIsNone(json.loads(failures[0].read_text())['pnl'])

    def test_all_json_lines_header_uniqueness_and_gzip_crc_are_checked(self):
        header = {'type': 'dataset', 'dataset_id': 'synthetic-intake'}
        expected = {'header': header, 'counts': {'book': 1}}
        head, body = json.dumps(header), '{"type":"book"}'
        bad_streams = [head+'\nnot JSON\n', head+'\n\n', head+'\n'+head+'\n',
                       body+'\n'+head+'\n', '', head+'\n{"type":"book","x":NaN}\n',
                       head+'\n[]\n']
        path = self.root/'normalized.jsonl.gz'
        for content in bad_streams:
            with self.subTest(content=content):
                path.write_bytes(gzip.compress(content.encode()))
                with self.assertRaises(ValueError):
                    raw.validate_normalized(path, expected)
        valid = bytearray(gzip.compress((head+'\n'+body+'\n').encode()))
        valid[-8] ^= 1  # Corrupt only the CRC; JSON and record counts still fit.
        path.write_bytes(valid)
        with self.assertRaisesRegex(ValueError, 'CRC check failed'):
            raw.validate_normalized(path, expected)

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
