import unittest
from op7_fb_eb_venue_mapper import event_summary, timestamp_ms


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

    def test_timestamp_seconds_and_milliseconds_are_normalized(self):
        self.assertEqual(timestamp_ms(1790000000), 1790000000000)
        self.assertEqual(timestamp_ms(1790000000000), 1790000000000)
        self.assertIsNone(timestamp_ms(None))


if __name__ == "__main__":
    unittest.main()
