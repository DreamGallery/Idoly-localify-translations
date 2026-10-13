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

    def test_collector_errors_export_only_fixed_signals(self):
        result = classify('Notice collection stopped: Firebase refresh rejected (HTTP 400)\n'
                          'Notice collection failed (_InactiveRpcError); check input, session and network\n'
                          'Notice collection stopped: Collector account is locked; inspect previous process\n'
                          'refresh_token=private-token https://private.invalid/account\n'
                          'Notice collection failed (private-account);\n')
        self.assertEqual(result['signals'], {'firebase_refresh_http_400': 1,
                                            'collector__InactiveRpcError': 1,
                                            'collector_locked': 1})
        self.assertNotIn('private', json.dumps(result))

    def test_official_rpc_codes_are_reported_without_free_form_values(self):
        result = classify('Notice collection stopped: Official RPC failed '
                          '(status FAILED_PRECONDITION, application codes 1001,1002)\n'
                          'Official RPC failed (status private-status, application codes private-token)\n')
        self.assertEqual(result['signals'], {'official_rpc_FAILED_PRECONDITION': 1,
                                            'official_app_1001': 1, 'official_app_1002': 1})
        self.assertNotIn('private', json.dumps(result))
