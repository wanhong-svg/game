import unittest
from android.op7_event_search import EventSnapshot, same_live_event, _xpath_literal, parse_detail_event, parse_search_results, find_unique_search_result
from android.op7_market_reader import find_unique_option, MarketOption
from android.op7_selection_verifier import ExpectedSelection, SlipSnapshot, verify_selection


class Op7UiModuleTests(unittest.TestCase):
    def test_parse_realistic_op7_detail_header(self):
        xml = """<hierarchy>
          <node content-desc="NBA" />
          <node content-desc="萨克拉门托国王\n第二节\n08:46\n27 - 32\n波士顿凯尔特人" />
        </hierarchy>"""
        event = parse_detail_event(xml)
        self.assertEqual((event.league, event.home, event.away), ("NBA", "萨克拉门托国王", "波士顿凯尔特人"))
        self.assertEqual((event.period, event.clock, event.home_score, event.away_score),
                         ("第二节", "08:46", 27, 32))

    def test_parse_search_result_preserves_competition(self):
        xml = """<hierarchy>
          <node content-desc="NBA\n第一节 06:56\n萨克拉门托国王 VS 波士顿凯尔特人" />
          <node content-desc="澳大利亚国家篮球联赛\n11-22 14:00\n布里斯班子弹 VS 悉尼国王" />
        </hierarchy>"""
        events = parse_search_results(xml)
        self.assertEqual(len(events), 2)
        selected = find_unique_search_result(events, league="NBA",
                    home="萨克拉门托国王", away="波士顿凯尔特人")
        self.assertEqual(selected.league, "NBA")

    def test_live_event_requires_clock_and_rejects_large_progress_gap(self):
        base = EventSnapshot("NBA", "国王", "凯尔特人", 30, 37, "第二节", "08:08")
        self.assertFalse(same_live_event(base,
            EventSnapshot("NBA", "凯尔特人", "国王", 37, 30, "第二节", "")))
        self.assertFalse(same_live_event(base,
            EventSnapshot("NBA", "凯尔特人", "国王", 37, 30, "第二节", "04:00")))
    def test_xpath_literal_handles_both_quote_types(self):
        self.assertEqual(_xpath_literal("NBA"), "'NBA'")
        self.assertEqual(_xpath_literal("team's"), '"team\'s"')
        self.assertEqual(_xpath_literal("a\'b\"c"), "concat('a', \"'\", 'b\"c')")

    def test_same_live_event_requires_competition_teams_period_and_score(self):
        a = EventSnapshot("NBA", "萨克拉门托国王", "波士顿凯尔特人", 30, 37, "第二节", "08:08")
        b = EventSnapshot("NBA", "波士顿凯尔特人", "萨克拉门托国王", 37, 30, "第二节", "07:58")
        self.assertTrue(same_live_event(a, b))
        self.assertFalse(same_live_event(a, EventSnapshot("NBA", a.home, a.away, 31, 37, "第二节")))
        self.assertFalse(same_live_event(a, EventSnapshot("澳大利亚国家篮球联赛", a.home, a.away, 30, 37, "第二节")))
        self.assertFalse(same_live_event(a, EventSnapshot("NBA", a.home, a.away, 30, 37, "第三节")))

    def test_find_exact_market_option(self):
        options = [
            MarketOption("全场让分", "FULL_TIME_HANDICAP", "主", "+12.5", "0.88", "主 +12.5\\n0.88"),
            MarketOption("全场让分", "FULL_TIME_HANDICAP", "客", "-12.5", "0.88", "客 -12.5\\n0.88"),
        ]
        self.assertEqual(find_unique_option(options, market_type="FULL_TIME_HANDICAP",
                                            selection="主", line="+12.5").odds, "0.88")

    def test_slip_verification_rejects_wrong_line(self):
        expected = ExpectedSelection("NBA", "萨克拉门托国王", "波士顿凯尔特人",
                                      "全场让分", "主", "+12.5", "0.88")
        actual = SlipSnapshot("NBA", expected.home, expected.away, "全场让分",
                              "萨克拉门托国王+11.5", "+11.5", "0.88", "30:37")
        with self.assertRaises(ValueError):
            verify_selection(expected, actual)


if __name__ == "__main__":
    unittest.main()
