import hashlib
import io
import os
import subprocess
import sys
import tarfile
import unittest

import sealed_archive_transport as transport


class SealedArchiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.payload = b"registered replay bytes\n" * 128
        self.digest = hashlib.sha256(self.payload).hexdigest()
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            info = tarfile.TarInfo("bundle/strict/input.jsonl.gz")
            info.size = len(self.payload)
            archive.addfile(info, io.BytesIO(self.payload))
        raw.seek(0)
        archive_bytes = raw.read()
        self.archive_fd = os.memfd_create("test-archive", os.MFD_ALLOW_SEALING)
        os.write(self.archive_fd, archive_bytes)
        transport.seal_fd(self.archive_fd)
        self.registration = transport.RegisteredMember(
            member_path="bundle/strict/input.jsonl.gz",
            exposed_name="input.jsonl.gz",
            size=len(self.payload),
            sha256=self.digest,
        )

    def tearDown(self):
        os.close(self.archive_fd)

    def test_child_reads_exact_registered_bytes_through_path(self):
        members = transport.extract_registered_members(
            self.archive_fd, [self.registration]
        )
        try:
            with transport.inherited_path_view(members) as (root, pass_fds):
                script = "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())"
                result = subprocess.run(
                    [sys.executable, "-c", script, str(root / "input.jsonl.gz")],
                    pass_fds=pass_fds,
                    check=True,
                    capture_output=True,
                )
            self.assertEqual(result.stdout, self.payload)
            transport.verify_members(members)
        finally:
            transport.close_members(members)

    def test_sealed_member_rejects_write(self):
        members = transport.extract_registered_members(
            self.archive_fd, [self.registration]
        )
        try:
            with self.assertRaises(OSError):
                os.write(members[0].fd, b"x")
            transport.verify_members(members)
        finally:
            transport.close_members(members)

    def test_registered_hash_mismatch_closes_partial_result(self):
        bad = transport.RegisteredMember(
            member_path=self.registration.member_path,
            exposed_name=self.registration.exposed_name,
            size=self.registration.size,
            sha256="0" * 64,
        )
        before = set(os.listdir("/proc/self/fd"))
        with self.assertRaises(ValueError):
            transport.extract_registered_members(self.archive_fd, [bad])
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)


if __name__ == "__main__":
    unittest.main()
