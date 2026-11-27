import os
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from op7_test_bet import (build_latest_market_request, build_limit_request, build_bet_payload,
    validate_test_target, select_eb_full_game_total, validate_cross_odds_snapshot,
    refresh_and_validate_cross_odds, parse_bet_result, log_successful_bet_summary)

SELECTION = {
    "event":{"mid":"m1","tid":"262","csid":"1","csna":"足球","tn":"欧洲联赛"},
    "market":{"hpid":"4","hpn":"全场让球"},
    "group":{"hid":"mk1","hv":"-0/0.5","hn":1},
    "option":{"oid":"od1","ot":"1","ov":193000,"otv":"主队 -0/0.5"},
    "market_type_finally":"EU",
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
        self.assertEqual(detail["marketTypeFinally"], "EU")


    def test_market_type_finally_has_no_guessed_default(self):
        selection = dict(SELECTION)
        selection.pop("market_type_finally")
        with self.assertRaisesRegex(ValueError, "market_type_finally"):
            build_bet_payload(selection, "11")

    def test_selects_current_nba_option_from_raw_event_tree(self):
        event = {
            "mid":"nba-1", "tid":"132", "csid":"2", "csna":"篮球", "tn":"NBA", "tnjc":"NBA",
            "hps":[{"hpid":"38","topKey":"38","hpn":"全场大小","hl":[{"hid":"market-1","hv":"213.5",
                "ol":[{"oid":"under-1","ot":"Under","ov":194000,"otv":"小 213.5"},
                      {"oid":"over-1","ot":"Over","ov":194000,"otv":"大 213.5"}]}]}]
        }
        selection = select_eb_full_game_total(event, "213.5", "Under", match_type=1,
            place_num=1, cuid="runtime-cuid", device_imei="runtime-device",
            market_type_finally="EU")
        self.assertEqual(selection["option"]["oid"], "under-1")
        self.assertEqual(selection["market"]["hpid"], "38")

    def test_selector_includes_additional_total(self):
        event = {
            "mid":"nba-3", "tid":"132", "csid":"2", "csna":"篮球", "tn":"NBA", "tnjc":"NBA",
            "hps":[{"hpid":"38","topKey":"38-1","hpn":"全场大小-附加盘","hl":[
                {"hid":"additional-line","hv":"223.5",
                 "ol":[{"oid":"additional-under","ot":"Under","ov":194000,"otv":"小 223.5"}]}
            ]}]
        }
        selection = select_eb_full_game_total(event, "223.5", "Under", match_type=1,
            place_num=1, cuid="runtime-cuid", device_imei="runtime-device",
            market_type_finally="EU")
        self.assertEqual(selection["market"]["hpn"], "全场大小-附加盘")
        self.assertEqual(selection["market"]["topKey"], "38-1")
        self.assertEqual(selection["option"]["oid"], "additional-under")


        event = {
            "mid":"nba-2", "tid":"132", "csid":"2", "csna":"篮球", "tn":"NBA", "tnjc":"NBA",
            "hps":[
                {"hpid":"38","topKey":"38-1","hpn":"全场大小-附加盘","hl":[
                    {"hid":"additional-line","hv":"213.5",
                     "ol":[{"oid":"additional-under","ot":"Under","ov":194000,"otv":"小 213.5"}]}]},
                {"hpid":"38","topKey":"38","hpn":"全场大小","hl":[
                    {"hid":"ordinary-line","hv":"213.5",
                     "ol":[{"oid":"ordinary-under","ot":"Under","ov":195000,"otv":"小 213.5"}]}]},
            ]
        }
        selection = select_eb_full_game_total(event, "213.5", "Under", match_type=1,
            place_num=1, cuid="runtime-cuid", device_imei="runtime-device",
            market_type_finally="EU")
        self.assertEqual(selection["market"]["topKey"], "38")
        self.assertEqual(selection["option"]["oid"], "ordinary-under")

    def test_cross_odds_selects_only_one_direction(self):
        result = validate_cross_odds_snapshot({
            "eb_under":"2.05","eb_over":"1.80","pingbo_over":"2.05","pingbo_under":"1.80",
            "eb_under_line":"214.5","pingbo_over_line":"214.0"
        })
        self.assertEqual(result["direction"], "Under")
        self.assertTrue(result["a_pass"])
        self.assertFalse(result["b_pass"])

    def test_cross_odds_rejects_both_or_neither(self):
        with self.assertRaisesRegex(ValueError, "ambiguous or not profitable"):
            validate_cross_odds_snapshot({
                "eb_under":"2.0","eb_over":"2.0","pingbo_over":"2.0","pingbo_under":"2.0",
                "eb_under_line":"214.5","pingbo_over_line":"214.0"
            })

    def test_cross_odds_requires_eb_under_line_at_least_pingbo_over(self):
        with self.assertRaisesRegex(ValueError, "below"):
            validate_cross_odds_snapshot({
                "eb_under":"2.05","eb_over":"1.8","pingbo_over":"2.05","pingbo_under":"1.8",
                "eb_under_line":"213.5","pingbo_over_line":"214.0"
            })


    def test_two_book_refresh_must_finish_within_five_seconds(self):
        snapshot = {
            "eb_under":"2.05","eb_over":"1.80","pingbo_over":"2.05","pingbo_under":"1.80",
            "eb_under_line":"214.5","pingbo_over_line":"214.0"
        }
        with patch("op7_test_bet.time.monotonic", side_effect=[0.0, 5.1]):
            with self.assertRaisesRegex(RuntimeError, "5.1s"):
                refresh_and_validate_cross_odds(lambda: snapshot, max_seconds=5.0)

    def test_parse_bet_result_converts_fen_to_yuan_and_keeps_order_no(self):
        parsed = parse_bet_result({"code":"0000000","data":{
            "betMoneyTotal":"1100","orderDetailRespList":[{
                "orderNo":"ORDER-1","orderStatusCode":1,"betMoney":"1100.0"
            }]
        }})
        self.assertEqual(parsed["orders"][0]["orderNo"], "ORDER-1")
        self.assertEqual(parsed["bet_money_total_yuan"], "11")
        self.assertEqual(parsed["orders"][0]["bet_money_yuan"], "11")


    def test_success_summary_groups_orders_and_sums_event_amounts(self):
        results = [
            {"event_id":"game-1","event_name":"主队 vs 客队","accepted_order_count":2,
             "orders":[{},{}],"bet_money_total_yuan":"100.50"},
            {"event_id":"game-1","event_name":"主队 vs 客队","accepted_order_count":1,
             "orders":[{}],"bet_money_total_yuan":"50"},
            {"event_id":"game-2","event_name":"另一场赛事","accepted_order_count":1,
             "orders":[{}],"bet_money_total_yuan":"25.25"},
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            log_successful_bet_summary(results)
        text = output.getvalue()
        self.assertIn("成功投注=3 单", text)
        self.assertIn("赛事投注金额合计=150.50 元", text)
        self.assertIn("成功投注=1 单", text)
        self.assertIn("赛事投注金额合计=25.25 元", text)
        self.assertIn("成功投注金额总计=175.75 元", text)

    def test_success_summary_does_not_claim_total_when_amount_is_missing(self):
        output = io.StringIO()
        with redirect_stdout(output):
            log_successful_bet_summary([{
                "event_id":"game-1","event_name":"主队 vs 客队",
                "accepted_order_count":1,"orders":[{"bet_money_yuan":None}],
            }])
        self.assertIn("金额不完整，无法准确汇总", output.getvalue())

    def test_bet_payload_rejects_nonpositive_stake(self):
        with self.assertRaises(ValueError):
            build_bet_payload(SELECTION, "0")

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
