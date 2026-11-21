import unittest
from betting_rules import BettingRulesConfigError, evaluate_bet, validate_config

BASE = {
    "enabled": True,
    "allowed_plays": {"独赢": True, "让球": False},
    "limits": {"max_tickets_per_match_play": 1},
    "stake": {"min": 50, "max": 500},
}

class BettingRulesTests(unittest.TestCase):
    def decide(self, **overrides):
        args = {"match_id": "match-001", "play": "独赢", "stake": 100,
                "existing_ticket_count": 0}
        args.update(overrides)
        return evaluate_bet(BASE, **args)

    def test_allowed(self):
        self.assertTrue(self.decide().allowed)

    def test_disabled_play(self):
        self.assertEqual(self.decide(play="让球").code, "PLAY_DISABLED")

    def test_unknown_play_fails_closed(self):
        self.assertEqual(self.decide(play="波胆").code, "PLAY_NOT_CONFIGURED")

    def test_ticket_limit(self):
        self.assertEqual(self.decide(existing_ticket_count=1).code, "TICKET_LIMIT_REACHED")

    def test_amount_boundaries_are_inclusive(self):
        self.assertTrue(self.decide(stake=50).allowed)
        self.assertTrue(self.decide(stake=500).allowed)

    def test_amount_outside_range(self):
        self.assertEqual(self.decide(stake=49).code, "STAKE_OUT_OF_RANGE")
        self.assertEqual(self.decide(stake=501).code, "STAKE_OUT_OF_RANGE")

    def test_global_disable(self):
        rules = dict(BASE, enabled=False)
        result = evaluate_bet(rules, match_id="m1", play="独赢", stake=100,
                              existing_ticket_count=0)
        self.assertEqual(result.code, "RULES_DISABLED")

    def test_invalid_config(self):
        with self.assertRaises(BettingRulesConfigError):
            validate_config(dict(BASE, limits={"max_tickets_per_match_play": 0}))

    def test_invalid_count(self):
        self.assertEqual(self.decide(existing_ticket_count=-1).code, "INVALID_TICKET_COUNT")

    def test_bool_is_not_valid_amount(self):
        self.assertEqual(self.decide(stake=True).code, "INVALID_STAKE")

if __name__ == "__main__":
    unittest.main()
