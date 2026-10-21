"""Audit forward-workflow checkpoint ordering; never run trading or replay code.

The momentum step mutates append-only stopping state.  A later strict replay can
be slow or fail, so at least one persistence checkpoint must occur between the
momentum producer and that strict gate.  A checkpoint only after the gate does
not protect against a hung gate, regardless of its ``if`` expression.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SCHEMA = "forward-checkpoint-order-audit-v1"
MOMENTUM = "Frozen five-second momentum paper test"
STRICT = "Require strict BTC replay artifact"
PERSIST_PREFIX = "Persist pooled stopping state"


def parse_steps(text: str) -> list[dict]:
    starts = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^(\s*)- name:\s*(.+?)\s*$", line)
        if match:
            starts.append((number, len(match.group(1)), match.group(2).strip("'\"")))
    steps = []
    lines = text.splitlines()
    for index, (line_no, indent, name) in enumerate(starts):
        end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        body = lines[line_no:end]
        condition = None
        for line in body:
            match = re.match(rf"^\s{{{indent + 2},}}if:\s*(.+?)\s*$", line)
            if match:
                condition = match.group(1)
                break
        steps.append({"index": index, "line": line_no, "name": name, "if": condition})
    return steps


def audit(path: str | Path) -> dict:
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    steps = parse_steps(text)

    def one(name: str) -> dict:
        found = [step for step in steps if step["name"] == name]
        if len(found) != 1:
            raise ValueError(f"expected exactly one workflow step named {name!r}")
        return found[0]

    momentum = one(MOMENTUM)
    strict = one(STRICT)
    persists = [step for step in steps if step["name"].startswith(PERSIST_PREFIX)]
    if not persists:
        raise ValueError("no pooled stopping-state persistence step")
    between = [step for step in persists if momentum["index"] < step["index"] < strict["index"]]
    after = [step for step in persists if step["index"] > strict["index"]]
    # GitHub suppresses the implicit success() guard when a status function is
    # present.  In particular, !cancelled() still runs after an ordinary
    # failure, but it is false after cancellation.  Keep those cases distinct.
    post_failure_capable = [
        step for step in after
        if step["if"] and any(token in step["if"] for token in
                              ("always()", "failure()", "!cancelled()"))
    ]
    post_cancellation_capable = [
        step for step in after
        if step["if"] and (
            "always()" in step["if"] or
            ("cancelled()" in step["if"] and "!cancelled()" not in step["if"])
        )
    ]
    reasons = []
    if not momentum["index"] < strict["index"]:
        reasons.append("strict gate is not after the momentum producer")
    if not between:
        reasons.append("no stopping-state persistence checkpoint exists before the strict gate")
    return {
        "schema": SCHEMA,
        "workflow": str(path),
        "workflow_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "blocked" if reasons else "pass",
        "steps": {"momentum": momentum, "strict": strict, "persistence": persists},
        "checkpoint_between_momentum_and_strict": bool(between),
        "post_strict_checkpoint_runs_after_failure": bool(post_failure_capable),
        "post_strict_checkpoint_runs_after_cancellation": bool(post_cancellation_capable),
        "strict_gate_can_delay_or_lose_unpersisted_momentum_state": not bool(between),
        "reasons": reasons,
        "required_repair": (
            "add an append-only persistence checkpoint immediately after momentum, before strict replay; "
            "retain a separate later checkpoint for strict outputs"
            if reasons else None
        ),
        "financial_replay_performed": False,
        "rank7_pnl_usd": None,
        "formal_oos_result": None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        result = audit(args.workflow)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        result = {"schema": SCHEMA, "status": "blocked", "reason": str(exc),
                  "financial_replay_performed": False, "formal_oos_result": None}
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
