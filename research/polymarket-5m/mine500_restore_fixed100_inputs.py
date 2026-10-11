"""Restore two omitted, reproducible fixed100 inputs without fitting or replay.

The six packaged endpoint tapes and saved probabilities authenticate the body.
The original header is retained byte-for-byte as JSON field order and values,
including historical absolute paths used only as provenance (never for access).
Outputs are deterministic gzip files and published only after expected SHA256
matches. Existing nonmatching files are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import heapq
import json
import os
from pathlib import Path
import tempfile

import mine500_deep_batch as batch
import mine500_endpoint_model as model
import mine500_probability_models as probability
import mine500_replay_v2 as execution

EXPECTED_COMBINED = "e33e520bd88065f66d8d37bce95430326c357d9e054e15c2fa9bd31a95857297"
EXPECTED_ENRICHED = "f5890102660511fd574d3f1f68526aea1fdd768346f3c8b5ba0bf7c70f8ab10d"


def existing_matches(path, expected):
    if path.exists():
        if not path.is_file() or batch.digest(path) != expected:
            raise ValueError("refusing to overwrite existing nonmatching file: " + str(path))
        return True
    return False


def publish_verified(temp, destination, expected):
    """No-clobber atomic publication on the destination filesystem."""
    if batch.digest(temp) != expected:
        raise ValueError("restored output SHA256 mismatch: " + str(destination))
    try:
        os.link(temp, destination)
    except FileExistsError:
        # Handles races too; a different file must never be replaced.
        existing_matches(destination, expected)
    temp.unlink()


def authenticated_sources(source_root, original_header):
    entries = original_header.get("provenance", {}).get("inputs", [])
    if len(entries) != 6:
        raise ValueError("exactly six ordered endpoint tapes required")
    sources, headers, intervals = [], [], []
    for i, entry in enumerate(entries):
        filename = "endpoint_" + str(i) + ".jsonl.gz"
        if Path(entry["file"]).name != filename:
            raise ValueError("unexpected original endpoint tape ordering")
        path = source_root / "endpoint24" / filename
        if batch.digest(path) != entry["sha256"]:
            raise ValueError("endpoint input SHA256 mismatch: " + filename)
        header = next(iter(execution.read_jsonl(path)), {})
        if (header.get("type") != "dataset" or header.get("split") != "development"
                or header.get("dataset_id") != entry["dataset_id"]
                or header.get("coverage_start_ms") != entry["start_ms"]
                or header.get("coverage_end_ms") != entry["end_ms"]
                or header.get("decision_grid_ms") != 1000
                or header.get("book_resolution") != "decision_and_match_projection"
                or header.get("supported_latencies_ms") != [500, 600, 1000]
                or header.get("recv_ms", float("inf")) > entry["start_ms"]):
            raise ValueError("endpoint metadata differs from original header manifest")
        sources.append(path)
        headers.append(header)
        intervals.append([entry["start_ms"], entry["end_ms"]])
    execution._validate_observed_intervals(sorted(intervals))
    expected_fields = dict(type="dataset", split="development",
        recv_ms=min(h["recv_ms"] for h in headers),
        coverage_start_ms=min(x[0] for x in intervals),
        coverage_end_ms=max(x[1] for x in intervals),
        observed_hours=sum(b-a for a,b in intervals)/3600000,
        observed_intervals_ms=sorted(intervals),
        book_resolution="decision_and_match_projection",
        supported_latencies_ms=[500, 600, 1000], decision_grid_ms=1000,
        full_tick_change_stream_preserved=all(h.get("full_tick_change_stream_preserved") is True for h in headers))
    if any(original_header.get(k) != v for k,v in expected_fields.items()):
        raise ValueError("original combined header does not describe authenticated tapes")
    return sources, headers


def merged_records(paths, headers, original_header):
    streams = []
    for path, header in zip(paths, headers):
        iterator = iter(execution.read_jsonl(path))
        if next(iterator) != header:
            raise ValueError("endpoint header changed during restore")
        streams.append(batch._validated_stream(iterator, header["coverage_start_ms"],
            header["coverage_end_ms"], header["recv_ms"], str(path)))
    seen = set()
    yield original_header
    for event in heapq.merge(*streams, key=lambda e:e["recv_ms"]):
        if event["type"] == "dataset":
            raise ValueError("embedded dataset header")
        if event["type"] == "decision":
            key = event["market_id"], event["recv_ms"]
            if key in seen or probability._day(event["recv_ms"]) not in batch.CONSUMED_DAYS:
                raise ValueError("duplicate or unconsumed decision")
            seen.add(key)
        yield event


def materialize(destination, records, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if existing_matches(destination, expected):
        return dict(path=str(destination), sha256=expected, status="already_matching")
    fd, name = tempfile.mkstemp(prefix=".restore-",suffix=".jsonl.gz",dir=destination.parent)
    os.close(fd)
    temp = Path(name)
    try:
        count = batch.write_gzip(temp, records)
        publish_verified(temp, destination, expected)
        return dict(path=str(destination),sha256=expected,status="restored",records=count,
                    bytes=destination.stat().st_size)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root",type=Path,required=True,
                    help="Extracted endpoint_research_20261011 folder")
    ap.add_argument("--development",type=Path,required=True,
                    help="Extracted fixed100 development folder with registration and input_audit")
    ap.add_argument("--out-root",type=Path,
                    help="Default: source-root; overrides permit a separate restore test")
    args = ap.parse_args(argv)
    src, dev = args.source_root.resolve(), args.development.resolve()
    out = args.out_root.resolve() if args.out_root else src
    registration = json.loads((dev/"registration.json").read_text())
    model_registration = json.loads((src/"endpoint_models/registration.json").read_text())
    if (model_registration["input_sha256"] != EXPECTED_COMBINED
            or registration["input_files"]["enriched.jsonl.gz"]["sha256"] != EXPECTED_ENRICHED):
        raise ValueError("unexpected original registered target hashes")
    original_header = json.loads((dev/"input_audit.json").read_text())["dataset"]
    paths, headers = authenticated_sources(src, original_header)
    score_path = src/"endpoint_models/scores.jsonl.gz"
    if batch.digest(score_path) != registration["input_files"]["scores.jsonl.gz"]["sha256"]:
        raise ValueError("saved score file SHA256 mismatch")
    # Check both targets before writing either; do not damage a conflicting restore.
    combined, enriched = out/"endpoint24/combined.jsonl.gz", out/"endpoint_models/enriched.jsonl.gz"
    existing_matches(combined, EXPECTED_COMBINED)
    existing_matches(enriched, EXPECTED_ENRICHED)
    results = [materialize(combined, merged_records(paths, headers, original_header), EXPECTED_COMBINED)]
    scores = {}
    for row in execution.read_jsonl(score_path):
        key = str(row["market_id"]),row["recv_ms"],row["model_id"]
        if key in scores:
            raise ValueError("duplicate saved score")
        scores[key] = row
    results.append(materialize(enriched,model.attach(execution.read_jsonl(combined),scores),EXPECTED_ENRICHED))
    report = dict(version="fixed100-input-restore-v1",fitted_models=0,executed_replays=0,
        original_path_strings_accessed=False,header_paths_preserved_as_provenance=True,
        source_endpoint_hashes=[entry["sha256"] for entry in original_header["provenance"]["inputs"]],
        output=results,restorer_sha256=batch.digest(__file__))
    batch.write_json(out/"fixed100_restore_audit.json",report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
