"""Fail-closed admission for frozen saved features; standard library only.

This module validates inputs without fitting a model, rewriting a tape, dropping
decisions, or changing a frozen enrichment implementation.  Consumers must use
the returned scores only with the exact admitted endpoint bytes.  A successful
admission is engineering evidence, never a profitability or OOS result.
"""
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import re


DAY_MS = 86400000
MODEL_PREFIXES = frozenset({"time", "trend", "book", "joint"})


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON constant: " + value)


def _loads(text):
    return json.loads(text, object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def read_jsonl(path):
    """Read complete gzip/plain JSONL and reject duplicate JSON object keys."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise ValueError(f"empty JSONL record at {path}:{line_number}")
                row = _loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"JSONL record must be object at {path}:{line_number}")
                yield row
    except (EOFError, gzip.BadGzipFile) as error:
        raise ValueError("incomplete or corrupt input: " + str(path)) from error


def _timestamp(value, label):
    if not finite_number(value) or value < 0:
        raise ValueError("invalid " + label)
    return value


def _key(row, label):
    market = row.get("market_id")
    if not isinstance(market, str) or not market:
        raise ValueError("invalid " + label + " market_id")
    return market, _timestamp(row.get("recv_ms"), label + " recv_ms")


def _available(available, feature, now, label):
    value = available.get(feature)
    if not finite_number(value) or not 0 <= value <= now:
        raise ValueError(f"{label} feature unavailable at decision: {feature}")


def _registered_models(registration):
    models = {candidate["model"] for candidate in registration.get("candidates", [])
              if isinstance(candidate, dict) and isinstance(candidate.get("model"), str)}
    if not models or any(not model for model in models):
        raise ValueError("registration has no explicit candidate model names")
    return models


def _cutoffs(audit, models):
    if not isinstance(audit, list):
        raise ValueError("model audit must be a list")
    cutoffs, seen = {}, set()
    for row in audit:
        if not isinstance(row, dict):
            raise ValueError("model audit row must be an object")
        day = row.get("day")
        if not isinstance(day, int) or isinstance(day, bool) or day < 0:
            raise ValueError("invalid audit UTC day")
        model = row.get("model")
        if model is not None and (not isinstance(model, str) or model not in models):
            raise ValueError("unregistered audit model")
        key = day, model
        if key in seen:
            raise ValueError("duplicate model audit key")
        seen.add(key)
        if model is None:
            if row.get("status") != "warmup_no_fit":
                raise ValueError("audit row without model must declare warmup_no_fit")
            continue
        cutoff = _timestamp(row.get("latest_label_ms"), "model label cutoff")
        if cutoff >= day * DAY_MS:
            raise ValueError("model label cutoff not strictly earlier UTC day")
        cutoffs[key] = cutoff
    return cutoffs


def admit_saved_features(endpoint_paths, scores_path, registration_path, audit_path,
                         *, required_aliases=None, expected_scores_sha256=None):
    """Return ``(scores_by_market_time, evidence)`` after complete admission.

    ``required_aliases`` maps destination feature to original source feature,
    e.g. ``{"imbalance": "imbalance_up"}`` for immutable MAR enrichment.
    Missing/null source features remain missing; numeric source features must
    already be available at that decision.  No feature is filled or rewritten.

    The optional expected score digest can pin the original MAR registration's
    saved-score artifact in addition to this function's provenance hashes.
    """
    paths = [Path(path) for path in endpoint_paths]
    if not paths:
        raise ValueError("at least one endpoint is required")
    aliases = dict(required_aliases or {})
    if any(not isinstance(a, str) or not a or not isinstance(b, str) or not b
           for a, b in aliases.items()):
        raise ValueError("feature aliases must be nonempty names")
    sources = {"scores_sha256": (Path(scores_path), sha256(scores_path)),
               "model_registration_sha256": (Path(registration_path), sha256(registration_path)),
               "model_audit_sha256": (Path(audit_path), sha256(audit_path))}
    if expected_scores_sha256 is not None and sources["scores_sha256"][1] != expected_scores_sha256:
        raise ValueError("saved scores differ from expected original hash")
    registration = _loads(Path(registration_path).read_text(encoding="utf-8"))
    if not isinstance(registration, dict) or not isinstance(registration.get("input_sha256"), dict):
        raise ValueError("registration lacks original input hashes")
    allowed = set(registration["input_sha256"].values())
    if not allowed or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                          for value in allowed):
        raise ValueError("registration contains invalid original input hash")
    models = _registered_models(registration)
    audit = _loads(Path(audit_path).read_text(encoding="utf-8"))
    cutoffs = _cutoffs(audit, models)
    scores, score_days, score_fields = {}, Counter(), Counter()
    for row in read_jsonl(scores_path):
        key = _key(row, "score")
        if key in scores:
            raise ValueError("duplicate score key")
        values = row.get("scores")
        if not isinstance(values, dict) or not values:
            raise ValueError("score features must be a nonempty object")
        present_models = set(values).intersection(models)
        if not present_models:
            raise ValueError("score row without registered model")
        day = int(key[1] // DAY_MS)
        for name, value in values.items():
            if not finite_number(value):
                raise ValueError("invalid non-finite or nonnumeric score feature: " + name)
            if name.rsplit("_", 1)[0] in MODEL_PREFIXES and name not in models:
                raise ValueError("unregistered score model: " + name)
            if name in models:
                if not 0 <= value <= 1:
                    raise ValueError("model score probability outside [0,1]: " + name)
                if (day, name) not in cutoffs:
                    raise ValueError("score lacks matching earlier-day model cutoff")
            score_fields[name] += 1
        scores[key] = values
        score_days[day] += 1
    inputs, seen_hashes, seen_decisions, matched_scores = [], set(), set(), set()
    for path in paths:
        digest = sha256(path)
        if digest not in allowed:
            raise ValueError("endpoint not in original model registration")
        if digest in seen_hashes:
            raise ValueError("duplicate endpoint input hash")
        seen_hashes.add(digest)
        counts, alias_counts = Counter(), Counter()
        decision_start, decision_end = None, None
        for number, event in enumerate(read_jsonl(path)):
            typ = event.get("type")
            if not isinstance(typ, str) or not typ:
                raise ValueError("endpoint record lacks event type")
            counts[typ] += 1
            if number == 0 and typ != "dataset":
                raise ValueError("endpoint must begin with dataset header")
            if typ == "dataset":
                if number != 0:
                    raise ValueError("duplicate or misplaced dataset header")
                if event.get("split") != "development":
                    raise ValueError("only registered consumed development inputs admitted")
            if typ != "decision":
                continue
            key = _key(event, "decision")
            if key in seen_decisions:
                raise ValueError("duplicate decision key across endpoint inputs")
            seen_decisions.add(key)
            now = key[1]
            decision_start = now if decision_start is None else min(decision_start, now)
            decision_end = now if decision_end is None else max(decision_end, now)
            features, available = event.get("features"), event.get("feature_available_ms")
            if not isinstance(features, dict) or not isinstance(available, dict):
                raise ValueError("decision requires features and feature availability objects")
            values = scores.get(key)
            if values is None:
                counts["decisions_without_score"] += 1
            else:
                counts["decisions_with_score"] += 1
                matched_scores.add(key)
                for name in set(values).intersection(features):
                    if not finite_number(features[name]) or features[name] != values[name]:
                        raise ValueError("saved score conflicts with original feature: " + name)
                    _available(available, name, now, "overlapping saved score")
            for destination, source in aliases.items():
                if source not in features or features[source] is None:
                    alias_counts[destination + ":missing_source"] += 1
                    continue
                value = features[source]
                if not finite_number(value):
                    raise ValueError("invalid alias source feature: " + source)
                _available(available, source, now, "required alias source")
                if destination in features:
                    if not finite_number(features[destination]) or features[destination] != value:
                        raise ValueError("derived alias conflicts with original feature: " + destination)
                    _available(available, destination, now, "overlapping derived alias")
                alias_counts[destination + ":available_source"] += 1
        if counts["dataset"] != 1:
            raise ValueError("endpoint requires exactly one dataset header")
        if sha256(path) != digest:
            raise ValueError("endpoint changed during admission")
        inputs.append({"path": str(path), "sha256": digest, "split": "development",
                       "counts": dict(counts), "alias_coverage": dict(alias_counts),
                       "decision_range_ms": [decision_start, decision_end]})
    for path, digest in sources.values():
        if sha256(path) != digest:
            raise ValueError("saved model input changed during admission")
    evidence = {
        "schema": "saved-features-admission-v1", "status": "admitted",
        "input_data_role": "already_consumed_development", "model_fitting": False,
        "formal_oos_result": None, "admission_code_sha256": sha256(__file__),
        **{name: digest for name, (_, digest) in sources.items()},
        "expected_score_hash_checked": expected_scores_sha256 is not None,
        "registered_models": sorted(models), "validated_model_day_cutoffs": len(cutoffs),
        "score_rows": len(scores), "score_rows_by_utc_day": dict(score_days),
        "score_feature_counts": dict(score_fields), "inputs": inputs,
        "decisions": len(seen_decisions), "matched_score_rows": len(matched_scores),
        "decisions_without_score": len(seen_decisions) - len(matched_scores),
        "score_rows_outside_selected_tapes": len(scores) - len(matched_scores),
        "required_aliases": aliases,
        "missing_score_policy": "retain original decision; required model feature remains missing",
        "limits": ["Audit cutoffs and original hashes are checked; models are not independently refitted.",
                   "Admission does not verify profitability, complete market coverage, or untouched OOS."]}
    return scores, evidence
