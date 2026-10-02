import json
import unittest
from release_diagnostics import classify


class DiagnosticTests(unittest.TestCase):
    def test_only_known_codes_and_counts_are_exported(self):
        result = classify('secret123 https://private.invalid/model\n'
                          'Notice batch failed (ValueError): notice numbers, dates or amounts changed\n'
                          'Translation API returned HTTP 429\n')
        self.assertEqual(result['signals'], {'notice_numbers_changed': 1, 'http_429': 1,
                                             'batch_ValueError': 1})
        self.assertNotIn('secret123', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))
