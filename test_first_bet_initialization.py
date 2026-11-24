import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import interactive_nba_monitor as monitor


class FirstBetInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.temp_dir.name) / "first_bet_initialization.json"
        self.path_patch = patch.object(monitor, "FIRST_BET_AUDIT_FILE", self.audit_path)
        self.path_patch.start()
        self.endpoints_path = Path(self.temp_dir.name) / "endpoints.json"
        self.endpoints_path.write_text(json.dumps({"apis":{"eb":{"base_url":"https://read-api.invalid","nba_read_base_url":"https://read-api.invalid","hosts":["read-api.invalid"]}}}), encoding="utf-8")
        self.endpoints_patch = patch.object(monitor, "ENDPOINTS_FILE", self.endpoints_path)
        self.endpoints_patch.start()
        monitor.FIRST_BET_CAPTURE.clear()

    def tearDown(self):
        self.endpoints_patch.stop()
        self.path_patch.stop()
        self.temp_dir.cleanup()
        monitor.FIRST_BET_CAPTURE.clear()

    def test_requires_non_empty_order_number(self):
        monitor.FIRST_BET_CAPTURE.update({
            "response_ok": True, "http_status": 200,
            "business_code": "0000000", "order_numbers": [],
            "request_template": {"seriesOrders": []},
            "request_origin": "https://api.sandbox.invalid",
            "page_origin": "https://web.sandbox.invalid",
        })
        with self.assertRaisesRegex(RuntimeError, "orderNo"):
            monitor.confirm_first_bet_initialization()

    def test_records_order_number_after_successful_confirmation(self):
        monitor.FIRST_BET_CAPTURE.update({
            "host": "eb-sandbox.example", "path": monitor.EB_BET_PATH,
            "response_ok": True, "http_status": 200,
            "business_code": "0000000", "order_numbers": ["TEST-ORDER-001"],
            "request_template": {"seriesOrders": []},
            "request_origin": "https://api.sandbox.invalid",
            "page_origin": "https://web.sandbox.invalid",
        })
        audit = monitor.confirm_first_bet_initialization()
        saved = json.loads(self.audit_path.read_text(encoding="utf-8"))
        self.assertTrue(audit["initialized"])
        self.assertEqual(saved["order_numbers"], ["TEST-ORDER-001"])
        self.assertEqual(saved["new_order_numbers"], ["TEST-ORDER-001"])
        cfg = json.loads(self.endpoints_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["apis"]["eb"]["base_url"], "https://read-api.invalid")
        self.assertEqual(cfg["apis"]["eb"]["bet_api_base_url"], "https://api.sandbox.invalid")

    def test_duplicate_order_number_is_not_recorded_as_new(self):
        self.audit_path.write_text(json.dumps({"order_numbers": ["TEST-ORDER-001"]}), encoding="utf-8")
        monitor.FIRST_BET_CAPTURE.update({
            "host": "eb-sandbox.example", "path": monitor.EB_BET_PATH,
            "response_ok": True, "http_status": 200,
            "business_code": "0000000", "order_numbers": ["TEST-ORDER-001"],
            "request_template": {"seriesOrders": []},
            "request_origin": "https://api.sandbox.invalid",
            "page_origin": "https://web.sandbox.invalid",
        })
        audit = monitor.confirm_first_bet_initialization()
        self.assertEqual(audit["new_order_numbers"], [])
        self.assertEqual(audit["duplicate_order_numbers"], ["TEST-ORDER-001"])


if __name__ == "__main__":
    unittest.main()
