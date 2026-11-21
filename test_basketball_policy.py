import json
import tempfile
import unittest
from pathlib import Path
from basketball_policy import load_config, normalize_rows, DEFAULT_CONFIG, EXPECTED_SETTLEMENT, event_time_delta_seconds, freshness_state

class BasketballPolicyTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "events": [{"venue":"EB","event_id":"e1","sport":"篮球","league":"NBA","home":"A","away":"B"}],
            "markets": [
                {"venue":"EB","event_id":"e1","play_id_hpid":39,"selections":[{"selection":"A","decimal_odds":"1.91"}]},
                {"venue":"EB","event_id":"e1","play_id_hpid":38,"selections":[{"selection":"over","decimal_odds":"1.95"}]},
                {"venue":"EB","event_id":"e1","play_id_hpid":37,"selections":[{"selection":"home","decimal_odds":"1.80"}]},
                {"venue":"EB","event_id":"e1","play_id_hpid":999,"selections":[{"selection":"other","decimal_odds":"1.80"}]}
            ]
        }

    def test_unknown_settlement_requires_review_and_all_three_markets_detected(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        rows = normalize_rows(self.payload, cfg)
        self.assertEqual({r["market_type"] for r in rows if r["market_type"]}, {
            "BASKETBALL_FT_HANDICAP", "BASKETBALL_FT_TOTAL", "BASKETBALL_FT_MONEYLINE"
        })
        self.assertTrue(all(r["match_status"] == "NEEDS_REVIEW" for r in rows if r["market_type"]))
        self.assertTrue(all(r["include_overtime"] is True for r in rows if r["market_type"]))

    def test_verified_including_ot_is_eligible(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["platforms"]["PANDA"]["settlement_scope"] = EXPECTED_SETTLEMENT
        rows = normalize_rows(self.payload, cfg)
        self.assertEqual([r["match_status"] for r in rows if r["market_type"]], ["ELIGIBLE"] * 3)

    def test_excluding_ot_is_mismatch(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["platforms"]["PANDA"]["settlement_scope"] = "REGULATION_TIME"
        rows = normalize_rows(self.payload, cfg)
        self.assertTrue(all(r["match_status"] == "SETTLEMENT_MISMATCH" for r in rows if r["market_type"]))

    def test_legacy_no_ot_config_rejected(self):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["settlement"]["basketball"]["include_overtime"] = False
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "legacy.json"
            p.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(p)

    def test_event_time_tolerance_requires_both_timestamps(self):
        self.assertEqual(event_time_delta_seconds(100000, 104000), 4)
        self.assertIsNone(event_time_delta_seconds(100000, None))

    def test_missing_freshness_timestamp_is_unknown(self):
        state, _ = freshness_state(None, 30)
        self.assertEqual(state, "UNKNOWN")

    def test_stale_freshness_timestamp_is_rejected(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        state, _ = freshness_state((now - timedelta(seconds=60)).isoformat(), 30, now)
        self.assertEqual(state, "STALE")

if __name__ == "__main__":
    unittest.main()
