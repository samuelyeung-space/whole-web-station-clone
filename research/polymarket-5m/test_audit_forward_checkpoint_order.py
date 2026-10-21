"""Synthetic tests for workflow checkpoint ordering; no economic replay."""
from pathlib import Path
import tempfile
import unittest

import audit_forward_checkpoint_order as a


def workflow(order, persist_if=None):
    blocks = []
    for name in order:
        condition = f"\n        if: {persist_if}" if name.startswith(a.PERSIST_PREFIX) and persist_if else ""
        blocks.append(f"      - name: {name}{condition}\n        run: true")
    return "name: synthetic\njobs:\n  analyze:\n    steps:\n" + "\n".join(blocks) + "\n"


class CheckpointOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workflow.yml"

    def audit_order(self, order, persist_if=None):
        self.path.write_text(workflow(order, persist_if), encoding="utf-8")
        return a.audit(self.path)

    def test_checkpoint_before_strict_passes(self):
        result = self.audit_order([a.MOMENTUM, a.PERSIST_PREFIX, a.STRICT])
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["checkpoint_between_momentum_and_strict"])

    def test_post_strict_checkpoint_is_blocked_even_with_always(self):
        result = self.audit_order([a.MOMENTUM, a.STRICT, a.PERSIST_PREFIX], "${{ always() }}")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["post_strict_checkpoint_runs_after_failure"])
        self.assertTrue(result["post_strict_checkpoint_runs_after_cancellation"])
        self.assertTrue(result["strict_gate_can_delay_or_lose_unpersisted_momentum_state"])

    def test_current_style_condition_runs_after_failure_not_cancellation(self):
        result = self.audit_order([a.MOMENTUM, a.STRICT, a.PERSIST_PREFIX], "${{ !cancelled() }}")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["post_strict_checkpoint_runs_after_failure"])
        self.assertFalse(result["post_strict_checkpoint_runs_after_cancellation"])
        self.assertEqual(len(result["reasons"]), 1)

    def test_missing_required_step_fails_closed(self):
        self.path.write_text(workflow([a.MOMENTUM, a.STRICT]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "persistence"):
            a.audit(self.path)


if __name__ == "__main__":
    unittest.main()
