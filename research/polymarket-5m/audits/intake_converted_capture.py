"""Reuse opaque-part verification to stream only the BTC converted bundle.

Transport validity is separate from stream/market/execution admission.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
from intake_native_capture import JoinedParts, payloads, digest, download, CHUNK


def intake(manifest, transfers, output, downloader=download):
    if manifest["source_name"] != "raw.tgz":
        raise ValueError("expected converted raw.tgz identity")
    output.mkdir(parents=True, exist_ok=False)
    extract = output / "extract"; extract.mkdir()
    receipts = []; inventory = []; selected = []
    stream = JoinedParts(payloads(manifest, transfers, output, receipts, downloader))
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            inventory.append({"path": member.name, "bytes": member.size})
            if not member.name.startswith("bundle-btc/") or member.isdir():
                continue
            target = (extract/member.name).resolve()
            if not target.is_relative_to(extract.resolve()) or not member.isfile() or member.size > 1024*CHUNK:
                raise ValueError("unsafe or oversized converted member")
            if target.exists(): raise ValueError("duplicate converted member")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest, CHUNK)
            with target.open("rb") as f: size, sha = digest(f)
            if size != member.size: raise ValueError("truncated converted member")
            selected.append({"path": member.name, "bytes": size, "sha256": sha})
    while stream.read(CHUNK): pass
    if stream.bytes != manifest["source_bytes"] or stream.hash.hexdigest() != manifest["source_sha256"]:
        raise ValueError("converted full source size/hash mismatch")
    required = {"manifest.json", "source_events.jsonl.gz", "clob_events.jsonl.gz",
                "market_registry.csv.gz", "market_outcomes.csv.gz"}
    actual = {Path(x["path"]).name for x in selected if x["path"].startswith("bundle-btc/strict/")}
    if not required <= actual: raise ValueError("incomplete BTC strict bundle")
    result = {"schema":"streamed-converted-capture-intake-v1", "full_converted_archive_verified":True,
              "source_bytes":stream.bytes,"source_sha256":stream.hash.hexdigest(),
              "parts":receipts,"selected_members":selected,"inventory":inventory,
              "market_coverage_verified":False,"financial_result":None,"formal_oos_result":None}
    (output/"receipt.json").write_text(json.dumps(result,indent=2)+"\n")
    return result


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest",type=Path);p.add_argument("private_transfers",type=Path);p.add_argument("output",type=Path)
    a=p.parse_args();r=intake(json.loads(a.manifest.read_text()),json.loads(a.private_transfers.read_text()),a.output)
    print(json.dumps({"verified_parts":len(r["parts"]),"source_bytes":r["source_bytes"],"BTC_members":len(r["selected_members"])}))
