import unittest

from pingbo_nba_detail import (
    american_to_decimal,
    extract_full_game_totals,
    is_nba_league,
    match_reversed_totals,
)


class PingboNBATests(unittest.TestCase):
    def setUp(self):
        self.league = {"sportId": 4, "id": 487, "name": "NBA", "leagueCode": "nba"}
        self.event = {
            "id": 1640570286, "time": 1795565400000,
            "participants": [
                {"name": "奥兰多魔术队", "englishName": "Orlando Magic", "type": "HOME"},
                {"name": "底特律活塞队", "englishName": "Detroit Pistons", "type": "AWAY"},
            ],
            "periods": {
                "0": {"overUnder": [
                    {"lineId": 57691018315, "isAlt": True, "overOdds": "-105",
                     "underOdds": "-109", "points": "213.5", "unavailable": False, "offline": False}
                ]},
                "1": {"overUnder": [
                    {"lineId": 111, "overOdds": "-110", "underOdds": "-110", "points": "110"}
                ]}
            }
        }

    def test_nba_league_exact_filter(self):
        self.assertTrue(is_nba_league(self.league))
        self.assertFalse(is_nba_league({**self.league, "id": 12, "name": "NBA 2K", "leagueCode": "nba2k"}))
        self.assertFalse(is_nba_league({**self.league, "sportId": 29}))

    def test_only_full_game_totals_are_extracted(self):
        rows = extract_full_game_totals(self.event, self.league)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["points"], "213.5")
        self.assertEqual(rows[0]["line_id"], "57691018315")
        self.assertEqual(rows[0]["home_team_en"], "Orlando Magic")
        self.assertEqual(rows[0]["away_team_en"], "Detroit Pistons")

    def test_american_odds_conversion(self):
        self.assertEqual(american_to_decimal("-105"), "1.952")
        self.assertEqual(american_to_decimal("+110"), "2.100")
        self.assertIsNone(american_to_decimal("0"))

    def test_eb_under_matches_pingbo_over_same_line_same_game(self):
        pb = extract_full_game_totals(self.event, self.league)
        eb = [{
            "mid": "eb-1", "home_team_en": "Orlando Magic",
            "away_team_en": "Detroit Pistons", "period": "FULL_GAME",
            "handicap": "213.5", "selection": "Under", "odds": "1.95"
        }]
        rows = match_reversed_totals(eb, pb)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pingbo_selection"], "Over")
        self.assertEqual(rows[0]["points"], "213.5")
        self.assertEqual(rows[0]["pingbo_odds_american"], "-105")

    def test_different_line_does_not_match(self):
        pb = extract_full_game_totals(self.event, self.league)
        eb = [{
            "home_team_en": "Orlando Magic", "away_team_en": "Detroit Pistons",
            "period": "FULL_GAME", "handicap": "214", "selection": "Under", "odds": "1.95"
        }]
        self.assertEqual(match_reversed_totals(eb, pb), [])


if __name__ == "__main__":
    unittest.main()
