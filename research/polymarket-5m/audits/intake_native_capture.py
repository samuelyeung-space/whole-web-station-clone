"""Verify every opaque part while streaming selected original tar members.

Transfer URLs are short-lived input only; never include that input in reports.
No order, account, training, replay or settlement-label substitution is present.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

CHUNK = 1024 * 1024
SELECTED = {"capture-manifest.json", "capture-runtime.json", "capture-health.json",
            "ledger.csv", "report.md", "decisions.jsonl.gz", "errors.jsonl.gz",
            "markets.jsonl.gz", "binance-futures-trade-strict.jsonl.gz"}


def digest(stream):
    h = hashlib.sha256(); size = 0
    while block := stream.read(CHUNK):
        size += len(block); h.update(block)
    return size, h.hexdigest()


def verify(stream, size, expected, label):
    actual_size, actual_hash = digest(stream)
    if actual_size != size or actual_hash != expected:
        raise ValueError(label + " size/hash mismatch")


class JoinedParts(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks = iter(chunks); self.buffer = b""
        self.hash = hashlib.sha256(); self.bytes = 0

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            raise ValueError("unbounded read forbidden")
        while len(self.buffer) < size:
            try:
                self.buffer += next(self.chunks)
            except StopIteration:
                break
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        self.hash.update(result); self.bytes += len(result)
        return result


def payloads(manifest, transfers, work, receipts, downloader):
    parts = manifest["parts"]
    if len(parts) != len(transfers) or [p["index"] for p in parts] != list(range(len(parts))):
        raise ValueError("incomplete or unordered part manifest")
    for part, transport in zip(parts, transfers):
        if transport["index"] != part["index"]:
            raise ValueError("transfer index mismatch")
        outer_path = work / ("verified-transfer-%05d.zip" % part["index"])
        downloader(transport, outer_path)
        with outer_path.open("rb") as f:
            verify(f, transport["outer_bytes"], transport["outer_sha256"], "outer ZIP")
        with zipfile.ZipFile(outer_path) as outer:
            names = outer.namelist()
            if len(names) != 1 or Path(names[0]).name != part["name"]:
                raise ValueError("unexpected outer ZIP members")
            with outer.open(names[0]) as inner_bytes:
                verify(inner_bytes, part["zip_bytes"], part["zip_sha256"], "inner ZIP")
                inner_bytes.seek(0)
                with zipfile.ZipFile(inner_bytes) as inner:
                    names = inner.namelist()
                    if names != ["payload.bin"]:
                        raise ValueError("unexpected payload member")
                    h = hashlib.sha256(); count = 0
                    with inner.open("payload.bin") as source:
                        while block := source.read(CHUNK):
                            h.update(block); count += len(block)
                            yield block
                    if count != part["payload_bytes"] or h.hexdigest() != part["payload_sha256"]:
                        raise ValueError("payload size/hash mismatch")
        receipts.append({k: transport[k] for k in ("index", "artifact_id", "outer_bytes", "outer_sha256")}
                        | {"inner_zip_verified": True, "payload_verified": True})
        outer_path.unlink()  # Only this just-verified reproducible transfer file.


def intake(manifest, transfers, output, downloader):
    output.mkdir(parents=True, exist_ok=False)
    receipts = []; inventory = []; selected = []
    joined = JoinedParts(payloads(manifest, transfers, output, receipts, downloader))
    extract = output / "extract"; extract.mkdir()
    with tarfile.open(fileobj=joined, mode="r|") as archive:
        for member in archive:
            inventory.append({"path": member.name, "bytes": member.size, "regular_file": member.isfile()})
            if Path(member.name).name not in SELECTED:
                continue
            target = (extract / member.name).resolve()
            if not target.is_relative_to(extract.resolve()) or not member.isfile() or member.size > 200 * CHUNK:
                raise ValueError("unsafe or oversized selected member")
            if target.exists():
                raise ValueError("duplicate selected member")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest, CHUNK)
            with target.open("rb") as source:
                size, sha = digest(source)
            if size != member.size:
                raise ValueError("selected member truncated")
            selected.append({"path": member.name, "bytes": size, "sha256": sha})
    while joined.read(CHUNK):  # Hash the entire opaque source, including tar padding.
        pass
    if joined.bytes != manifest["source_bytes"] or joined.hash.hexdigest() != manifest["source_sha256"]:
        raise ValueError("full source size/hash mismatch")
    result = {"schema": "streamed-native-capture-intake-v1", "full_capture_archive_verified": True,
              "source_bytes": joined.bytes, "source_sha256": joined.hash.hexdigest(),
              "parts": receipts, "selected_members": selected, "inventory": inventory,
              "all_original_members_retained_locally": False, "financial_result": None,
              "market_coverage_verified": False, "formal_oos_result": None}
    (output / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def download(transport, target):
    result = subprocess.run(["curl", "--silent", "--show-error", "--fail", "--location", "--retry", "2",
                             transport["download_url"], "--output", str(target)], check=False)
    if result.returncode:
        # CalledProcessError would expose the short-lived URL in its command.
        raise RuntimeError("artifact byte transfer failed; no source admission")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path); p.add_argument("private_transfers", type=Path)
    p.add_argument("output", type=Path); a = p.parse_args()
    r = intake(json.loads(a.manifest.read_text()), json.loads(a.private_transfers.read_text()), a.output, download)
    print(json.dumps({"verified_parts": len(r["parts"]), "source_bytes": r["source_bytes"],
                      "selected_members": len(r["selected_members"]), "financial_result": None}))
