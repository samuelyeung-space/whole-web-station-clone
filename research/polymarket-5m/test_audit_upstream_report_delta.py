import tempfile
import unittest
from pathlib import Path

from audit_upstream_report_delta import audit


class UpstreamReportDeltaAuditTest(unittest.TestCase):
    def roots(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old, new = Path(tmp.name) / "old", Path(tmp.name) / "new"
        (old / "real").mkdir(parents=True)
        (new / "real").mkdir(parents=True)
        return old, new

    def write_pair(self, old: Path, new: Path, old_text: str, new_text: str):
        rel = "real/recorder-check.md"
        (old / rel).write_text(old_text, encoding="utf-8")
        (new / rel).write_text(new_text, encoding="utf-8")
        return (rel,)

    def test_new_run_is_an_append(self):
        old, new = self.roots()
        reports = self.write_pair(old, new, "| GitHub 10000001 | 3 |\n",
                                  "| GitHub 10000001 | 3 |\n| GitHub 10000002 | 4 |\n")
        result = audit(old, new, reports)
        self.assertTrue(result["passed"])
        self.assertEqual([x["key"] for x in result["added_records"]], ["row:10000002"])

    def test_existing_run_rewrite_fails_closed(self):
        old, new = self.roots()
        reports = self.write_pair(old, new, "| GitHub 10000001 | 3 |\n",
                                  "| GitHub 10000001 | 9 |\n")
        result = audit(old, new, reports)
        self.assertFalse(result["passed"])
        self.assertEqual(result["changed_existing_records"][0]["key"], "row:10000001")

    def test_changed_disconnect_note_fails_closed(self):
        old, new = self.roots()
        reports = self.write_pair(old, new, "- btc 10000001: 8 candidates dropped\n",
                                  "- btc 10000001: 7 candidates dropped\n")
        result = audit(old, new, reports)
        self.assertFalse(result["passed"])
        self.assertEqual(result["changed_existing_records"][0]["key"], "note:10000001")

    def test_aggregate_row_is_ignored(self):
        old, new = self.roots()
        reports = self.write_pair(old, new, "| 合计 | 3 |\n", "| 合计 | 9 |\n")
        self.assertTrue(audit(old, new, reports)["passed"])


if __name__ == "__main__":
    unittest.main()
