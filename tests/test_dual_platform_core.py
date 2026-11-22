from datetime import datetime, timezone

import pytest

from dual_platform.core import (
    AuditStore, Event, MarketQuote, compare_quotes, match_events_unique,
    normalize_market_type,
)


def test_unique_event_match_requires_exact_teams_and_time_agreement():
    a = Event("OP7", "1", "Lakers", "Celtics", "2026-11-22T12:00:00Z")
    b = Event("PINGBO", "2", "Lakers", "Celtics", "2026-11-22T12:02:00Z")
    matches = match_events_unique([a], [b])
    assert len(matches) == 1
    assert matches[0].orientation == "SAME"
    reversed_event = Event("PINGBO", "3", "Celtics", "Lakers", "2026-11-22T12:02:00Z")
    assert match_events_unique([a], [reversed_event])[0].orientation == "REVERSED"


def test_ambiguous_event_match_is_rejected():
    a = Event("OP7", "1", "Lakers", "Celtics", "2026-11-22T12:00:00Z")
    b = Event("PINGBO", "2", "Lakers", "Celtics", "2026-11-22T12:02:00Z")
    duplicate = Event("PINGBO", "3", "Lakers", "Celtics", "2026-11-22T12:01:00Z")
    assert match_events_unique([a], [b, duplicate]) == []


def test_market_aliases_are_explicit():
    assert normalize_market_type("全场独赢") == "FULL_TIME_MONEYLINE"
    assert normalize_market_type("全场大小") == "FULL_TIME_TOTAL"
    assert normalize_market_type("全场让球") == "FULL_TIME_HANDICAP"
    assert normalize_market_type("未知玩法") is None


def test_quote_delta_and_key_validation():
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    a = MarketQuote("OP7", "1", "FULL_TIME_TOTAL", "FULL_TIME", "2.5", "over", "1.95", stamp)
    b = MarketQuote("PINGBO", "2", "FULL_TIME_TOTAL", "FULL_TIME", "2.5", "over", "1.90", stamp)
    result = compare_quotes(a, b, now=now)
    assert result["odds_delta_op7_minus_pingbo"] == "0.05"
    c = MarketQuote("PINGBO", "2", "FULL_TIME_TOTAL", "FULL_TIME", "3.0", "over", "1.90", stamp)
    with pytest.raises(ValueError):
        compare_quotes(a, c, now=now)


def test_market_lock_is_once_per_event_and_market_not_per_line(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    assert store.lock_market("event-1", "FULL_TIME_TOTAL", "2.5", 50)
    assert not store.lock_market("event-1", "FULL_TIME_TOTAL", "3.0", 100)
    assert store.lock_market("event-1", "FULL_TIME_HANDICAP", "-1.5", 100)
    with pytest.raises(ValueError):
        store.lock_market("event-2", "FULL_TIME_HANDICAP", "-1.5", 501)
    store.close()
