import unittest
from decimal import Decimal
from eb_pingbo_nba_validator import run_validation, team_key

class ValidatorTests(unittest.TestCase):
    def base(self, line="220", under="2.10", over="2.10"):
        return {"event_id":"eb1","home_team":"奥兰多魔术队","away_team":"底特律活塞队",
            "points":line,"odds_decimal":under,"over_odds_decimal":over,"selection":"Under",
            "start_time_ms":1795554000000,"is_live":False}
    def pb(self, line="219", over="2.10", under="1.80", reverse=False):
        return {"events":[{"event_id":"pb1",
            "home_team":"底特律活塞" if reverse else "奥兰多魔术",
            "away_team":"奥兰多魔术" if reverse else "底特律活塞",
            "start_time_ms":1795554000000,"is_live":False,
            "full_game_totals":[{"period":"FULL_GAME","points":line,
                "over_odds_decimal":over,"under_odds_decimal":under,
                "line_id":"line-1","unavailable":False,"offline":False}]}]}
    def test_chinese_team_aliases(self):
        self.assertEqual(team_key("奥兰多魔术队"),"orlandomagic")
        self.assertEqual(team_key("底特律活塞队"),"detroitpistons")
    def test_under_entry_accepts_eb_line_greater_than_pingbo_over(self):
        row=run_validation([self.base("220")],self.pb("219",reverse=True),Decimal("0"))["results"][0]
        self.assertEqual(row["rule_status"],"CANDIDATE_A")
        self.assertEqual(row["candidate_side"],"EB_UNDER")
    def test_equal_line_is_allowed(self):
        row=run_validation([self.base("220")],self.pb("220"),Decimal("0"))["results"][0]
        self.assertIn(row["rule_status"],{"CANDIDATE_A","CANDIDATE_B","NO_MATCH","ANOMALY_BOTH_DIRECTIONS"})
        self.assertNotEqual(row["rule_status"],"NO_REVERSE_LINE")
    def test_eb_under_line_lower_than_pingbo_over_is_excluded(self):
        row=run_validation([self.base("220")],self.pb("221"),Decimal("0"))["results"][0]
        self.assertEqual(row["rule_status"],"NO_REVERSE_LINE")
    def test_time_mismatch_is_rejected(self):
        pb=self.pb()
        pb["events"][0]["start_time_ms"]+=60000
        row=run_validation([self.base()],pb,Decimal("0"))["results"][0]
        self.assertEqual(row["rule_status"],"EVENT_STATE_MISMATCH")
    def test_both_directions_passing_is_anomaly(self):
        row=run_validation([self.base("220","2.20","2.20")],self.pb("219","2.20","2.20"),Decimal("0"))["results"][0]
        self.assertEqual(row["rule_status"],"ANOMALY_BOTH_DIRECTIONS")
    def test_no_directions_passing_is_no_match(self):
        row=run_validation([self.base("220","1.10","1.10")],self.pb("219","1.10","1.10"),Decimal("0"))["results"][0]
        self.assertEqual(row["rule_status"],"NO_MATCH")
    def test_live_requires_same_explicit_progress(self):
        eb=self.base(); eb.update({"is_live":True,"period_label":"Q2","clock_seconds":420})
        pb=self.pb(); pb["events"][0].update({"is_live":True,"period_label":"Q2","clock_seconds":420})
        self.assertNotEqual(run_validation([eb],pb,Decimal("0"))["results"][0]["rule_status"],"EVENT_STATE_MISMATCH")
        pb["events"][0]["clock_seconds"]=300
        self.assertEqual(run_validation([eb],pb,Decimal("0"))["results"][0]["rule_status"],"EVENT_STATE_MISMATCH")

if __name__=="__main__": unittest.main()
