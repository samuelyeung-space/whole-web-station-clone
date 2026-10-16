"""Exact observed execution overlap in completed runs; no strategy selection.

Candidates and latency accounts are never added.  Identical observed execution
does not establish identical rules, and differing execution does not establish
economically independent mechanisms.  Zero-attempt candidates stay separate.
"""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def execution_overlap(rows, candidate_latency_keys):
    keys = set(candidate_latency_keys)
    digests = {key: hashlib.sha256() for key in keys}
    counts = Counter()
    fills = defaultdict(set)
    for row in rows:
        key = str(row["candidate_id"]), int(row["latency_ms"])
        if key not in keys:
            raise ValueError("attempt candidate/latency absent from metrics")
        economic = {name: value for name, value in row.items()
                    if name not in {"candidate_id", "attempt_id"}}
        encoded = json.dumps(economic, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digests[key].update((encoded + "\n").encode())
        counts[key] += 1
        if row.get("filled_shares", 0) > 0:
            fills[key].add((row["market_id"], row["side"]))
    per_delay = []
    for latency in sorted({key[1] for key in keys}):
        candidates = sorted(key[0] for key in keys if key[1] == latency)
        groups = defaultdict(list)
        zero = []
        signatures = []
        for candidate in candidates:
            key = candidate, latency
            if counts[key]:
                groups[digests[key].hexdigest()].append(candidate)
            else:
                zero.append(candidate)
            signatures.append({"candidate_id": candidate, "attempts": counts[key],
                               "attempt_sequence_sha256": digests[key].hexdigest() if counts[key] else None,
                               "filled_market_sides": len(fills[key])})
        pairs = []
        for index, left in enumerate(candidates):
            a = fills[left, latency]
            if not a:
                continue
            for right in candidates[index + 1:]:
                b = fills[right, latency]
                if not b:
                    continue
                intersection, union = len(a & b), len(a | b)
                if intersection:
                    pairs.append({"left": left, "right": right, "shared_filled_market_sides": intersection,
                                  "union_filled_market_sides": union, "jaccard": intersection / union})
        pairs.sort(key=lambda row: (-row["jaccard"], -row["shared_filled_market_sides"], row["left"], row["right"]))
        per_delay.append({"latency_ms": latency, "registered_candidates": len(candidates),
                          "candidates_with_attempts": len(candidates)-len(zero),
                          "distinct_nonempty_observed_attempt_sequences": len(groups),
                          "zero_attempt_candidates": zero, "signatures": signatures,
                          "exact_nonempty_sequence_groups": [
                              {"sha256": digest, "candidate_ids": members}
                              for digest, members in sorted(groups.items())],
                          "highest_filled_market_side_overlap_pairs": pairs[:30]})
    return per_delay


def analyze(run_path):
    root = Path(run_path).resolve()
    if any(".pending." in part for part in root.parts):
        raise ValueError("unfinished stage is not a completed run")
    complete = json.loads((root / "completion.json").read_text())
    if complete.get("status") != "completed_development_simulation":
        raise ValueError("completed development simulation required")
    evidence = {"schema": "observed-execution-overlap-v1", "source_run": str(root),
                "run_key": complete["run_key"], "completion_sha256": sha256(root / "completion.json"),
                "code_sha256": sha256(__file__), "new_backtests": 0,
                "formal_oos_result": None, "strategy_selection": False,
                "signature_definition": "Original ordered attempt JSON excluding only candidate_id and attempt_id; all other fields retained.",
                "policy": "Observed equality only; not structural rule equivalence. All registered trial counts remain. Never add candidate or latency PnL.",
                "scenarios": []}
    hashes = complete.get("output_sha256", {})
    for relative in sorted(name for name in hashes if name.endswith("/attempts.jsonl.gz")):
        attempts = root / relative
        metrics = attempts.with_name("metrics.csv")
        for path in [attempts, metrics]:
            if not path.resolve().is_relative_to(root):
                raise ValueError("completed output path escapes run")
            if sha256(path) != hashes.get(str(path.relative_to(root))):
                raise ValueError("completed output hash mismatch: " + str(path))
        with metrics.open(newline="") as f:
            metric_rows = list(csv.DictReader(f))
        keys = [(row["candidate_id"], int(row["latency_ms"])) for row in metric_rows]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate candidate/latency metric key")
        with gzip.open(attempts, "rt") as f:
            result = execution_overlap((json.loads(line) for line in f), keys)
        metric_counts = {(row["candidate_id"], int(row["latency_ms"])): int(row["attempts"])
                         for row in metric_rows}
        for group in result:
            for row in group["signatures"]:
                if row["attempts"] != metric_counts[row["candidate_id"], group["latency_ms"]]:
                    raise ValueError("attempt signature count does not reconcile with metrics")
        evidence["scenarios"].append({"name": attempts.parent.name,
                                     "attempts_sha256": hashes[relative], "latencies": result})
    if not evidence["scenarios"]:
        raise ValueError("completed run has no registered attempt files")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(analyze(args.run), indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "new_backtests": 0}))
