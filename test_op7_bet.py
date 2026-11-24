import os
import unittest
from unittest.mock import patch
from op7_test_bet import build_latest_market_request, build_limit_request, build_bet_payload, validate_test_target

SELECTION = {
    "event":{"mid":"m1","tid":"262","csid":"1","csna":"足球","tn":"欧洲联赛"},
    "market":{"hpid":"4","hpn":"全场让球"},
    "group":{"hid":"mk1","hv":"-0/0.5","hn":1},
    "option":{"oid":"od1","ot":"1","ov":193000,"otv":"主队 -0/0.5"},
    "match_type":2,"place_num":1,"cuid":"test-cuid","device_imei":"test-device"
}

class BetPayloadTests(unittest.TestCase):
    def test_latest_market_request_maps_source_ids(self):
        row = build_latest_market_request(SELECTION)["idList"][0]
        self.assertEqual(row["matchInfoId"], "m1")
        self.assertEqual(row["marketId"], "mk1")
        self.assertEqual(row["oddsId"], "od1")
        self.assertEqual(row["playId"], "4")
        self.assertEqual(row["matchType"], 2)

    def test_limit_request_uses_latest_odds_value(self):
        row = build_limit_request(SELECTION, 197000)["orderMaxBetMoney"][0]
        self.assertEqual(row["oddsValue"], 197000)
        self.assertEqual(row["oddsFinally"], "1.97")
        self.assertEqual(row["playOptionId"], "od1")

    def test_bet_payload_field_mapping(self):
        detail = build_bet_payload(SELECTION, "10", 197000)["seriesOrders"][0]["orderDetailList"][0]
        self.assertEqual(detail["matchId"], "m1")
        self.assertEqual(detail["tournamentId"], 262)
        self.assertEqual(detail["playOptionsId"], "od1")
        self.assertEqual(detail["odds"], 197000)
        self.assertEqual(detail["oddFinally"], "1.97")
        self.assertEqual(detail["betAmount"], "10")

    def test_target_is_blocked_without_explicit_switch(self):
        with patch.dict(os.environ, {"OP7_TEST_BET_ENABLED":"0"}, clear=False):
            with self.assertRaises(RuntimeError):
                validate_test_target("http://127.0.0.1:8080")

    def test_external_host_requires_exact_allowlist(self):
        with patch.dict(os.environ, {"OP7_TEST_BET_ENABLED":"1","OP7_TEST_BET_ALLOWED_HOSTS":""}, clear=False):
            with self.assertRaises(RuntimeError):
                validate_test_target("https://example.com")

if __name__ == "__main__":
    unittest.main()
