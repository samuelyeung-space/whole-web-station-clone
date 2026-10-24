"""Immutable, memory-backed transport for large registered replay inputs.

The module intentionally knows nothing about strategies or results.  It moves a
registered tar member into a Linux memfd, seals the descriptor against changes,
and exposes an inherited ``/proc/self/fd`` path to unmodified, path-based replay
workers.  Callers remain responsible for registering the input identities and
for rechecking them after replay.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
from typing import BinaryIO, Iterable, Iterator


F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
F_SEAL_SEAL = getattr(fcntl, "F_SEAL_SEAL", 1)
F_SEAL_SHRINK = getattr(fcntl, "F_SEAL_SHRINK", 2)
F_SEAL_GROW = getattr(fcntl, "F_SEAL_GROW", 4)
F_SEAL_WRITE = getattr(fcntl, "F_SEAL_WRITE", 8)
IMMUTABLE_SEALS = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE


@dataclass(frozen=True)
class RegisteredMember:
    member_path: str
    exposed_name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SealedMember:
    fd: int
    registered: RegisteredMember

    @property
    def child_path(self) -> Path:
        return Path("/proc/self/fd") / str(self.fd)


def sha256_fd(fd: int, block_size: int = 1 << 20) -> str:
    """Hash a descriptor without changing its final offset."""
    original = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while block := os.read(fd, block_size):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.lseek(fd, original, os.SEEK_SET)


def seal_fd(fd: int) -> None:
    os.fsync(fd)
    fcntl.fcntl(fd, F_ADD_SEALS, IMMUTABLE_SEALS)
    if fcntl.fcntl(fd, F_GET_SEALS) != IMMUTABLE_SEALS:
        raise RuntimeError("memfd did not accept the complete immutable seal set")


def verify_fd(fd: int, size: int, sha256: str) -> None:
    if os.fstat(fd).st_size != size:
        raise ValueError("registered byte count mismatch")
    if sha256_fd(fd) != sha256:
        raise ValueError("registered SHA-256 mismatch")
    if fcntl.fcntl(fd, F_GET_SEALS) != IMMUTABLE_SEALS:
        raise ValueError("descriptor is not immutable")


def copy_to_sealed_memfd(source: BinaryIO, name: str, size: int, sha256: str) -> int:
    fd = os.memfd_create(name, os.MFD_ALLOW_SEALING)
    digest = hashlib.sha256()
    count = 0
    try:
        while block := source.read(1 << 20):
            os.write(fd, block)
            digest.update(block)
            count += len(block)
        if (count, digest.hexdigest()) != (size, sha256):
            raise ValueError("registered source identity mismatch")
        seal_fd(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def extract_registered_members(
    archive_fd: int, registrations: Iterable[RegisteredMember]
) -> list[SealedMember]:
    """Extract exact registered members from a seekable tar archive descriptor."""
    result: list[SealedMember] = []
    duplicate = os.dup(archive_fd)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        with os.fdopen(duplicate, "rb") as source:
            duplicate = -1
            with tarfile.open(fileobj=source, mode="r:gz") as archive:
                for registered in registrations:
                    member = archive.getmember(registered.member_path)
                    if not member.isfile() or member.size != registered.size:
                        raise ValueError("registered tar member metadata mismatch")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ValueError("registered tar member is unreadable")
                    with extracted:
                        fd = copy_to_sealed_memfd(
                            extracted,
                            registered.exposed_name,
                            registered.size,
                            registered.sha256,
                        )
                    result.append(SealedMember(fd=fd, registered=registered))
        return result
    except BaseException:
        for item in result:
            os.close(item.fd)
        raise
    finally:
        if duplicate >= 0:
            os.close(duplicate)


@contextmanager
def inherited_path_view(
    members: Iterable[SealedMember], parent: Path | None = None
) -> Iterator[tuple[Path, tuple[int, ...]]]:
    """Yield a path-compatible view and the descriptors a child must inherit."""
    items = list(members)
    with tempfile.TemporaryDirectory(prefix="sealed-inputs-", dir=parent) as tmp:
        root = Path(tmp).resolve()
        for item in items:
            (root / item.registered.exposed_name).symlink_to(item.child_path)
        yield root, tuple(item.fd for item in items)


def verify_members(members: Iterable[SealedMember]) -> None:
    for item in members:
        verify_fd(
            item.fd,
            item.registered.size,
            item.registered.sha256,
        )


def close_members(members: Iterable[SealedMember]) -> None:
    for item in members:
        os.close(item.fd)
