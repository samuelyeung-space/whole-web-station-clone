"""Do not publish bad restore bytes or overwrite a user's existing tape."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import mine500_restore_fixed100_inputs as restore


class RestoreGuards(unittest.TestCase):
    def test_nonmatching_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/"target.gz";temp=root/"temp.gz"
            target.write_bytes(b"existing");temp.write_bytes(b"expected")
            with self.assertRaisesRegex(ValueError,"refusing to overwrite"):
                restore.publish_verified(temp,target,hashlib.sha256(b"expected").hexdigest())
            self.assertEqual(target.read_bytes(),b"existing")

    def test_bad_generated_bytes_are_never_published(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/"target.gz";temp=root/"temp.gz"
            temp.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError,"SHA256 mismatch"):
                restore.publish_verified(temp,target,hashlib.sha256(b"expected").hexdigest())
            self.assertFalse(target.exists())

    def test_matching_generated_bytes_are_atomically_published(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/"target.gz";temp=root/"temp.gz"
            temp.write_bytes(b"expected")
            restore.publish_verified(temp,target,hashlib.sha256(b"expected").hexdigest())
            self.assertEqual(target.read_bytes(),b"expected")
            self.assertFalse(temp.exists())


if __name__=="__main__":unittest.main()
