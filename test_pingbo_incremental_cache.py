import unittest
from pingbo_incremental_cache import PingboFeedCache

def event(eid, line=213.5, over="-105", under="-109", offline=False, unavailable=False):
    return {"event_id": str(eid), "_raw_event": {"id": eid, "periods": {
        "0": {"overUnder": [{"lineId": 100, "points": str(line), "overOdds": over,
            "underOdds": under, "offline": offline, "unavailable": unavailable}]}}}}

class PingboIncrementalCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = PingboFeedCache()
        self.cache.apply("/today", True, 1, [event(1), event(2)], response_at="t1")
    def test_empty_delta_preserves_cache_and_odds_time(self):
        r = self.cache.apply("/today", False, 2, [], response_at="t2")
        self.assertEqual(r["action"], "preserve_empty_delta")
        self.assertEqual(len(r["events"]), 2)
        self.assertEqual(r["events"]["1"]["markets"][("overUnder","0","100")]["odds_updated_at"], "t1")
        self.assertEqual(r["last_response_at"], "t2")
    def test_incremental_only_changes_one_event_market(self):
        self.cache.apply("/today", False, 2, [event(1, over="-110")], response_at="t2")
        self.assertEqual(self.cache.sources["/today"]["events"]["2"]["markets"][("overUnder","0","100")]["odds_updated_at"], "t1")
        self.assertEqual(self.cache.sources["/today"]["events"]["1"]["markets"][("overUnder","0","100")]["odds_updated_at"], "t2")
    def test_full_snapshot_replaces_only_source(self):
        self.cache.apply("/live", True, 1, [event(9)], response_at="x1")
        self.cache.apply("/today", True, 3, [event(1)], response_at="t3")
        self.assertEqual(set(self.cache.sources["/today"]["events"]), {"1"})
        self.assertEqual(set(self.cache.sources["/live"]["events"]), {"9"})
    def test_status_is_preserved(self):
        self.cache.apply("/today", False, 2, [event(1, offline=True, unavailable=True)], response_at="t2")
        m = self.cache.sources["/today"]["events"]["1"]["markets"][("overUnder","0","100")]
        self.assertTrue(m["offline"])
        self.assertTrue(m["unavailable"])
    def test_version_only_does_not_mark_odds_updated(self):
        self.cache.apply("/today", False, 99, [event(1)], response_at="t2")
        m = self.cache.sources["/today"]["events"]["1"]["markets"][("overUnder","0","100")]
        self.assertEqual(m["odds_updated_at"], "t1")

if __name__ == "__main__": unittest.main()
