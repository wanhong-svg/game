# -*- coding: utf-8 -*-
"""Conservative source-scoped Pingbo event/market cache."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

def _decimal_odds(value):
    try: n = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): return None
    if n == 0: return None
    if abs(n) > 1: return Decimal(1) + (n / 100 if n > 0 else Decimal(100) / abs(n))
    return n if n > 1 else None

def _event_id(event):
    value = event.get("event_id") or event.get("id") or event.get("eventId")
    return str(value) if value is not None else None

def _markets(event):
    raw = event.get("_raw_event") if isinstance(event.get("_raw_event"), dict) else event
    normal = raw.get("normal") if isinstance(raw.get("normal"), dict) else raw
    periods = normal.get("periods") if isinstance(normal, dict) else {}
    out = {}
    if not isinstance(periods, dict): return out
    for period, period_obj in periods.items():
        if not isinstance(period_obj, dict): continue
        for market_type, rows in period_obj.items():
            if isinstance(rows, dict): rows = list(rows.values())
            if not isinstance(rows, list): continue
            for row in rows:
                if not isinstance(row, dict) or row.get("lineId") is None: continue
                key = (str(market_type), str(period), str(row["lineId"]))
                over, under = row.get("overOdds"), row.get("underOdds")
                over_dec, under_dec = _decimal_odds(over), _decimal_odds(under)
                out[key] = {"market_type": str(market_type), "period": str(period),
                    "line_id": str(row["lineId"]), "points": row.get("points"),
                    "over_odds": over, "under_odds": under,
                    "over_decimal": str(over_dec) if over_dec is not None else None,
                    "under_decimal": str(under_dec) if under_dec is not None else None,
                    "unavailable": row.get("unavailable", False), "offline": row.get("offline", False),
                    "is_alt": row.get("isAlt", False), "raw": deepcopy(row)}
    return out

def _market_fingerprint(m):
    return (str(m.get("points")), str(m.get("over_odds")), str(m.get("under_odds")),
            bool(m.get("unavailable")), bool(m.get("offline")))

class PingboFeedCache:
    """Independent cache per API path/source; full snapshots replace only that source."""
    def __init__(self): self.sources = {}
    def apply(self, source, refresh_all, version, events, http_status=200, response_at=None, update_count=0):
        response_at = response_at or utc_now()
        state = self.sources.setdefault(source, {"events": {}, "version": None, "snapshot_at": None,
            "last_response_at": None, "http_status": None})
        incoming = {}
        for event in events or []:
            eid = _event_id(event)
            if eid: incoming[eid] = event
        changed = []
        if refresh_all is True:
            new_events = {}
            for eid, event in incoming.items():
                old_markets = state["events"].get(eid, {}).get("markets", {})
                markets = {}
                for key, market in _markets(event).items():
                    previous = old_markets.get(key)
                    odds_changed = previous is None or _market_fingerprint(previous) != _market_fingerprint(market)
                    market["odds_updated_at"] = response_at if odds_changed else previous.get("odds_updated_at")
                    market["last_seen_at"] = response_at
                    if odds_changed: changed.append({"event_id": eid, "market_key": key, "kind": "snapshot_market_changed"})
                    markets[key] = market
                new_events[eid] = {"event": deepcopy(event), "markets": markets, "last_event_update_at": response_at}
            state["events"] = new_events
            state["snapshot_at"] = response_at
            action = "replace_full_snapshot"
        elif incoming:
            for eid, event in incoming.items():
                old = state["events"].get(eid, {"markets": {}})
                markets = dict(old.get("markets", {}))
                for key, market in _markets(event).items():
                    previous = markets.get(key)
                    odds_changed = previous is None or _market_fingerprint(previous) != _market_fingerprint(market)
                    market["odds_updated_at"] = response_at if odds_changed else previous.get("odds_updated_at")
                    market["last_seen_at"] = response_at
                    if odds_changed: changed.append({"event_id": eid, "market_key": key, "kind": "incremental_market_changed"})
                    markets[key] = market
                state["events"][eid] = {"event": deepcopy(event), "markets": markets, "last_event_update_at": response_at}
            action = "merge_incremental_events"
        else:
            action = "preserve_empty_delta" if state["events"] else "no_snapshot_yet"
        state["last_response_at"] = response_at
        state["http_status"] = http_status
        if version is not None: state["version"] = version
        return {"action": action, "events": state["events"], "changed_markets": changed,
            "version": state["version"], "update_count": update_count,
            "snapshot_at": state["snapshot_at"], "last_response_at": state["last_response_at"]}
