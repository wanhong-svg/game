import unittest
from op7_fb_eb_venue_mapper import event_summary, timestamp_ms, identify_venue, flatten_pingbo_markets


class MapperIntegrityTests(unittest.TestCase):
    def test_fb_team_names_fall_back_to_event_name(self):
        row = event_summary({
            "id": "fb-1",
            "nm": "Home Club vs Away Club",
            "lg": {"na": "NBA"},
            "bt": 1790000000000,
            "mc": {"pe": 3001},
        }, "FB")
        self.assertEqual(row["home"], "Home Club")
        self.assertEqual(row["away"], "Away Club")

    def test_explicit_fb_team_names_take_precedence(self):
        row = event_summary({
            "id": "fb-2",
            "nm": "Fallback Home vs Fallback Away",
            "home": "Explicit Home",
            "away": "Explicit Away",
            "lg": {"na": "NBA"},
        }, "FB")
        self.assertEqual(row["home"], "Explicit Home")
        self.assertEqual(row["away"], "Explicit Away")

    def test_pingbo_current_and_legacy_hosts_are_recognized(self):
        self.assertEqual(identify_venue("https://www.pin135.com/sports-service/sv/compact/events"), "PINGBO")
        self.assertEqual(identify_venue("https://www.part567.com/sports-service/sv/compact/events"), "PINGBO")

    def test_pingbo_market_inherits_event_observation_timestamp(self):
        event = {"event_id": "p1", "observed_at_utc": "2026-11-24T12:00:00+00:00", "_raw_markets": {"0": [[0, 1, "-2.5", 1.91, 1.95, 1, 1, "sel"]]}}
        rows = flatten_pingbo_markets(event)
        self.assertTrue(rows)
        self.assertTrue(all(r["observed_at_utc"] == event["observed_at_utc"] for r in rows))

    def test_timestamp_seconds_and_milliseconds_are_normalized(self):
        self.assertEqual(timestamp_ms(1790000000), 1790000000000)
        self.assertEqual(timestamp_ms(1790000000000), 1790000000000)
        self.assertIsNone(timestamp_ms(None))


if __name__ == "__main__":
    unittest.main()
