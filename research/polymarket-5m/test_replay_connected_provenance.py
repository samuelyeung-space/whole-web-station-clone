"""Merged native/legacy provenance cannot silently drop consent requirements."""
import unittest
import replay_connected as connected


class ProvenanceTests(unittest.TestCase):
    def test_nested_headers_preserve_legacy_classification(self):
        header = {'provenance': {'source_headers': [{'provenance': {'source_headers': [
            {'strict_execution_admitted': False}]}}]}}
        self.assertTrue(connected.requires_legacy_assumptions(header))
        self.assertFalse(connected.requires_legacy_assumptions({'provenance': {'source_headers': [{}]}}))


if __name__ == '__main__':
    unittest.main()
