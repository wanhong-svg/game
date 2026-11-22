import unittest
from android.op7_event_adapter import normalize_event_response


class AndroidEventAdapterTests(unittest.TestCase):
    def setUp(self):
        self.mapping = {
            "events_path": "data.events",
            "fields": {
                "event_id": "id", "league": "league.name", "sport": "sport.name",
                "home": "home.name", "away": "away.name",
                "start_time_ms": "startTime", "raw_status": "status"
            }
        }
        self.payload = {"data": {"events": [{
            "id": 123, "league": {"name": "NBA"}, "sport": {"name": "篮球"},
            "home": {"name": "A"}, "away": {"name": "B"},
            "startTime": "1790000000000", "status": 1
        }]}}

    def test_normalizes_confirmed_response(self):
        rows = normalize_event_response(self.payload, self.mapping, "2026-11-22T00:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["venue"], "ANDROID_APP")
        self.assertEqual(rows[0]["event_id"], "123")
        self.assertEqual(rows[0]["start_time_ms"], 1790000000000)
        self.assertEqual(rows[0]["observed_at_utc"], "2026-11-22T00:00:00+00:00")

    def test_empty_list_fails_closed(self):
        with self.assertRaises(ValueError):
            normalize_event_response({"data": {"events": []}}, self.mapping)

    def test_missing_team_fails_closed(self):
        self.payload["data"]["events"][0]["home"] = {}
        with self.assertRaises(ValueError):
            normalize_event_response(self.payload, self.mapping)


if __name__ == "__main__":
    unittest.main()
