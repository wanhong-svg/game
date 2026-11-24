import base64
import gzip
import json
import unittest

from eb_nba_detail import (
    displayed_full_game_total_hids,
    event_identity,
    extract_extra_under_markets,
    is_regular_nba,
    unwrap_response,
)


class EBNBADetailTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "mid": "5878862", "csid": "2", "tn": "NBA", "tnjc": "NBA",
            "tid": "132", "mhn": "奥兰多魔术", "mhid": "14848",
            "man": "底特律活塞", "maid": "33963", "mgt": "1795564800000",
            "hps": [{
                "hpid": "38",
                "hl": [{"hid": "list-main-hid", "hv": "212.5", "ol": []}]
            }]
        }

    def test_regular_nba_requires_basketball_and_exact_league(self):
        self.assertTrue(is_regular_nba(self.event))
        self.assertFalse(is_regular_nba({**self.event, "tn": "NBA梦幻对垒"}))
        self.assertFalse(is_regular_nba({**self.event, "csid": "1"}))
        self.assertFalse(is_regular_nba({**self.event, "tn": "NBA", "tnjc": "NBA 2K"}))

    def test_event_identity_preserves_team_ids(self):
        identity = event_identity(self.event)
        self.assertEqual(identity["mid"], "5878862")
        self.assertEqual(identity["home_team_id"], "14848")
        self.assertEqual(identity["away_team_id"], "33963")

    def test_list_main_market_is_excluded_but_detail_additional_lines_remain(self):
        odds = [
            {"mid": "5878862", "hpid": "38", "hpn": "全场大小", "hl": [
                {"hid": "list-main-hid", "hv": "212.5", "ol": [
                    {"oid": "main-under", "ot": "Under", "ov": 200000}
                ]}
            ]},
            {"mid": "5878862", "hpid": "38", "hpn": "全场大小-附加盘", "hl": [
                {"hid": "extra-over", "hv": "213.5", "ol": [
                    {"oid": "extra-over-id", "ot": "Over", "ov": 198000}
                ]},
                {"hid": "extra-under", "hv": "211.5", "ol": [
                    {"oid": "extra-under-id", "ot": "Under", "ov": 207000}
                ]}
            ]},
            {"mid": "5878862", "hpid": "18", "hpn": "上半场大小", "hl": [
                {"hid": "half-under", "hv": "109.5", "ol": [
                    {"oid": "half-under-id", "ot": "Under", "ov": 177000}
                ]}
            ]}
        ]
        self.assertEqual(displayed_full_game_total_hids(self.event), {"list-main-hid"})
        rows = extract_extra_under_markets(self.event, odds)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hid"], "extra-under")
        self.assertEqual(rows[0]["handicap"], "211.5")
        self.assertEqual(rows[0]["oid"], "extra-under-id")
        self.assertEqual(rows[0]["odds"], "2.07")
        self.assertEqual(rows[0]["selection"], "Under")
        self.assertEqual(rows[0]["home_team_id"], "14848")

    def test_uncompressed_list_response_data_is_supported(self):
        self.assertEqual(unwrap_response({"code": "0000000", "data": [1, 2]}), [1, 2])

    def test_base64_gzip_envelope_is_decoded(self):
        inner = json.dumps({"pages": 1, "data": [{"mid": "m1"}]}).encode()
        encoded = base64.b64encode(gzip.compress(inner)).decode()
        self.assertEqual(unwrap_response({"code": "0000000", "data": encoded}),
                         {"pages": 1, "data": [{"mid": "m1"}]})

    def test_api_error_code_fails_closed(self):
        with self.assertRaises(RuntimeError):
            unwrap_response({"code": "1001", "msg": "denied", "data": []})


if __name__ == "__main__":
    unittest.main()
