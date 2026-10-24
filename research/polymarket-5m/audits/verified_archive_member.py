"""Verified immutable compressed bytes for offline research; no financial admission.

Never extracts a pathname. Consumers request separate GzipFile readers backed by
an immutable bytes object. An existing path-based engine needs an explicit adapter
and a new execution registration before using this transport.
"""
import gzip, hashlib, io, tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

@dataclass(frozen=True)
class VerifiedMember:
    payload: bytes
    sha256: str
    decoded_bytes: int
    def reader(self):
        return gzip.GzipFile(fileobj=io.BytesIO(self.payload), mode='rb')

def _sha(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def load(archive, *, archive_sha256, member, member_bytes, member_sha256,
         max_compressed_bytes, max_decoded_bytes):
    if member_bytes < 0 or member_bytes > max_compressed_bytes:
        raise ValueError('compressed byte budget exceeded')
    p = PurePosixPath(member)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('unsafe member name')
    if _sha(archive) != archive_sha256:
        raise ValueError('archive identity mismatch')
    payload = None
    with tarfile.open(archive, 'r:*') as tf:
        for entry in tf:
            if entry.name != member:
                continue
            if payload is not None or not entry.isfile():
                raise ValueError('duplicate or non-regular member')
            if entry.size != member_bytes:
                raise ValueError('member size mismatch')
            with tf.extractfile(entry) as src:
                payload = src.read(member_bytes + 1)
    if payload is None or len(payload) != member_bytes:
        raise ValueError('missing or truncated member')
    digest = hashlib.sha256(payload).hexdigest()
    if digest != member_sha256:
        raise ValueError('member identity mismatch')
    total = 0
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode='rb') as f:
        while block := f.read(1 << 20):
            total += len(block)
            if total > max_decoded_bytes:
                raise ValueError('decoded byte budget exceeded')
    if _sha(archive) != archive_sha256:
        raise ValueError('archive changed during validation')
    return VerifiedMember(payload, digest, total)
