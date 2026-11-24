import unittest
from decimal import Decimal
from eb_pingbo_nba_validator import run_validation, team_key

class ValidatorTests(unittest.TestCase):
    def test_chinese_team_aliases(self):
        self.assertEqual(team_key("奥兰多魔术队"), "orlandomagic")
        self.assertEqual(team_key("底特律活塞队"), "detroitpistons")

    def test_under_matches_reverse_over_and_calculates_edge(self):
        eb=[{"event_id":"eb1","home_team":"奥兰多魔术队","away_team":"底特律活塞队",
             "points":"213.5","odds_decimal":"2.10","selection":"Under"}]
        pb={"events":[{"event_id":"pb1","home_team":"奥兰多魔术","away_team":"底特律活塞",
             "full_game_totals":[{"points":"213.5","over_odds_decimal":"2.10",
               "over_odds_american":"+110","line_id":"line-1","unavailable":False,"offline":False}]}]}
        result=run_validation(eb,pb,Decimal("0"))
        row=result["results"][0]
        self.assertEqual(row["pingbo_selection"],"Over")
        self.assertEqual(row["rule_status"],"RULE_PASS")
        self.assertEqual(row["calculation"]["combined_implied_probability"],"95.238%")
        self.assertEqual(row["calculation"]["edge_percent"],"4.762%")

    def test_different_line_is_not_matched(self):
        eb=[{"event_id":"eb1","home_team":"奥兰多魔术队","away_team":"底特律活塞队",
             "points":"213.5","odds_decimal":"2.10"}]
        pb={"events":[{"event_id":"pb1","home_team":"奥兰多魔术","away_team":"底特律活塞",
             "full_game_totals":[{"points":"214","over_odds_decimal":"2.10"}]}]}
        self.assertEqual(run_validation(eb,pb,Decimal("0"))["results"][0]["rule_status"],"NO_REVERSE_LINE")

if __name__=="__main__": unittest.main()
