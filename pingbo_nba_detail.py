#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Pingbo basketball/NBA list-to-event detail client and parser.

The browser can remain on the basketball event list: this client calls the same
JSON GET endpoints in the HAR directly. No browser navigation or bet endpoint
is used. Credentials/cookies are supplied only via local environment variables.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENDPOINTS = json.loads((ROOT / "endpoints.json").read_text(encoding="utf-8"))
PB_CONFIG = ENDPOINTS["apis"]["pingbo"]
SPORT_ID_BASKETBALL = 4
NBA_LEAGUE_ID = 487
NBA_LEAGUE_CODE = "nba"
LIST_PATH = "/sports-service/sv/mobile/odds-today"
LIVE_PATH = "/sports-service/sv/mobile/odds-live"
EVENT_PATH = "/sports-service/sv/mobile/event"
LEAGUES_PATH = "/sports-service/sv/mobile/leagues"
SPORTS_PATH = "/sports-service/sv/mobile/sports"
MARKET_FILTERS_PATH = "/sports-service/sv/mobile/market-filters"
LEAGUE_EVENTS_PATH = "/sports-service/sv/mobile/league-events"
READ_PATHS = {LIST_PATH, LIVE_PATH, EVENT_PATH, LEAGUES_PATH, SPORTS_PATH,
              MARKET_FILTERS_PATH, LEAGUE_EVENTS_PATH}


class PingboApiError(RuntimeError):
    pass


def is_nba_league(league: dict[str, Any]) -> bool:
    return (str(league.get("sportId", "")) == str(SPORT_ID_BASKETBALL)
            and (str(league.get("id", "")) == str(NBA_LEAGUE_ID)
                 or str(league.get("leagueCode", "")).lower() == NBA_LEAGUE_CODE
                 or str(league.get("name", "")).strip().upper() == "NBA"))


def participant(event: dict[str, Any], side: str) -> dict[str, Any]:
    for p in event.get("participants") or []:
        if str(p.get("type", "")).upper() == side:
            return p
    return {}


def event_identity(event: dict[str, Any], league: dict[str, Any] | None = None) -> dict[str, Any]:
    home, away = participant(event, "HOME"), participant(event, "AWAY")
    return {
        "event_id": str(event.get("id") or ""),
        "league_id": str((league or {}).get("id") or ""),
        "league": (league or {}).get("name") or (league or {}).get("leagueCode"),
        "home_team": home.get("name"),
        "home_team_en": home.get("englishName"),
        "away_team": away.get("name"),
        "away_team_en": away.get("englishName"),
        "start_time_ms": event.get("time"),
        "sport_id": str((league or {}).get("sportId") or event.get("sportId") or SPORT_ID_BASKETBALL),
    }


def extract_full_game_totals(event: dict[str, Any], league: dict[str, Any] | None = None,
                             source: str = "PINGBO") -> list[dict[str, Any]]:
    """Extract period 0 full-game OU lines; period 1/3/4 are not full-game."""
    identity = event_identity(event, league)
    period = (event.get("periods") or {}).get("0") or {}
    rows = []
    for line in period.get("overUnder") or []:
        if not isinstance(line, dict):
            continue
        try:
            points = Decimal(str(line.get("points")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        rows.append({
            **identity,
            "period": "FULL_GAME",
            "market_type": "TOTAL",
            "line_id": str(line.get("lineId") or ""),
            "points": str(points.normalize()),
            "over_odds_american": line.get("overOdds"),
            "under_odds_american": line.get("underOdds"),
            "over_odds_decimal": american_to_decimal(line.get("overOdds")),
            "under_odds_decimal": american_to_decimal(line.get("underOdds")),
            "is_alt": bool(line.get("isAlt")),
            "unavailable": bool(line.get("unavailable")),
            "offline": bool(line.get("offline")),
            "source": source,
        })
    return rows


def american_to_decimal(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if value > 0:
        dec = Decimal(1) + value / Decimal(100)
    elif value < 0:
        dec = Decimal(1) + Decimal(100) / abs(value)
    else:
        return None
    return str(dec.quantize(Decimal("0.001")))


def reverse_side(side: str) -> str:
    normalized = side.strip().lower()
    if normalized in {"under", "小", "小分"}:
        return "over"
    if normalized in {"over", "大", "大分"}:
        return "under"
    raise ValueError(f"unsupported totals side: {side}")


def normalize_team_name(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().strip()
    for suffix in ("队", "team"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return "".join(ch for ch in text if ch.isalnum())


def same_match(eb: dict[str, Any], pb: dict[str, Any]) -> bool:
    """Confirm both teams and home/away orientation, accepting Chinese/English
    names only when an exact normalized alias is available to caller."""
    eb_home = normalize_team_name(eb.get("home_team_en") or eb.get("home_team"))
    eb_away = normalize_team_name(eb.get("away_team_en") or eb.get("away_team"))
    pb_home = normalize_team_name(pb.get("home_team_en") or pb.get("home_team"))
    pb_away = normalize_team_name(pb.get("away_team_en") or pb.get("away_team"))
    return bool(eb_home and eb_away and eb_home == pb_home and eb_away == pb_away)


def match_reversed_totals(eb_rows: list[dict[str, Any]],
                          pb_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match EB Under to Pingbo Over (and vice versa) at identical full-game line.

    Team orientation must be confirmed. The function never treats different
    periods or different total points as equivalent.
    """
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in pb_rows:
        if row.get("period") != "FULL_GAME" or row.get("unavailable") or row.get("offline"):
            continue
        for side in ("over", "under"):
            price = row.get(f"{side}_odds_decimal")
            if price:
                by_key[(normalize_team_name(row.get("home_team_en") or row.get("home_team")),
                        normalize_team_name(row.get("away_team_en") or row.get("away_team")),
                        f'{row.get("points")}|{side}')] = {**row, "matched_side": side,
                                                            "matched_odds_decimal": price}
    matches = []
    for eb in eb_rows:
        if eb.get("period") != "FULL_GAME":
            continue
        side = reverse_side(str(eb.get("selection") or eb.get("side") or "Under"))
        points = str(Decimal(str(eb.get("handicap") or eb.get("points"))).normalize())
        key = (normalize_team_name(eb.get("home_team_en") or eb.get("home_team")),
               normalize_team_name(eb.get("away_team_en") or eb.get("away_team")),
               f"{points}|{side}")
        pb = by_key.get(key)
        if not pb:
            continue
        matches.append({
            "event_id": eb.get("event_id") or eb.get("mid"),
            "home_team": eb.get("home_team"),
            "away_team": eb.get("away_team"),
            "period": "FULL_GAME",
            "points": points,
            "eb_selection": "Under" if side == "over" else "Over",
            "pingbo_selection": side.title(),
            "eb_odds": eb.get("odds"),
            "pingbo_odds_american": pb.get(f"{side}_odds_american"),
            "pingbo_odds_decimal": pb.get("matched_odds_decimal"),
            "pingbo_line_id": pb.get("line_id"),
            "match_status": "MATCHED_EXACT_LINE",
        })
    return matches


class PingboClient:
    def __init__(self, base_url: str | None = None,
                 headers: dict[str, str] | None = None, timeout: int = 15):
        self.base_url = (base_url or os.environ.get("PINGBO_API_BASE_URL",
                          PB_CONFIG["base_url"])).rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Pingbo API base URL must use HTTPS")
        if parsed.hostname not in set(PB_CONFIG.get("hosts", [])):
            raise ValueError(f"Pingbo host is not allow-listed: {parsed.hostname}")
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
            "Referer": os.environ.get("PINGBO_REFERER",
                         "https://www.pin135.com/zh-cn/x/sports/today/basketball"),
            **(headers or {}),
        }
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if path not in READ_PATHS:
            raise ValueError(f"Pingbo read endpoint not allow-listed: {path}")
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(self.base_url + path + "?" + query,
                                     headers=self.headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise PingboApiError(f"Pingbo HTTP {exc.code} at {path}") from exc
        except urllib.error.URLError as exc:
            raise PingboApiError(f"Pingbo network error at {path}: {exc.reason}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PingboApiError(f"Pingbo invalid JSON at {path}") from exc

    def fetch_leagues(self) -> list[dict[str, Any]]:
        data = self._get(LEAGUES_PATH, {"mk": 1, "sportId": SPORT_ID_BASKETBALL,
                         "c": "Others", "periodNumber": 0, "locale": "zh_CN",
                         "_": int(time.time() * 1000), "withCredentials": "true"})
        return data if isinstance(data, list) else []

    def fetch_nba_events(self, version: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        params = {"sportId": SPORT_ID_BASKETBALL, "periodNumber": 0,
                  "periodNumbers": -1, "betTypes": "HDP,OU,ML_1X2",
                  "locale": "zh_CN", "version": version,
                  "_": int(time.time() * 1000), "withCredentials": "true"}
        data = self._get(LIST_PATH, params)
        if not isinstance(data, dict):
            raise PingboApiError("odds-today response is not an object")
        leagues = [x for x in data.get("leagues", []) if isinstance(x, dict) and is_nba_league(x)]
        events = []
        for league in leagues:
            for event in league.get("events", []):
                if isinstance(event, dict):
                    events.append((event, league))
        return events, data

    def fetch_event_detail(self, event_id: str, version: int = 0,
                           special_version: int = 0, time_rtk: int | None = None) -> dict[str, Any]:
        params = {"eventId": event_id, "version": version,
                  "specialVersion": special_version, "locale": "zh_CN",
                  "_": int(time.time() * 1000), "withCredentials": "true",
                  "timeRTK": time_rtk}
        data = self._get(EVENT_PATH, params)
        if not isinstance(data, dict):
            raise PingboApiError("event detail response is not an object")
        return data

    def fetch_nba_details(self) -> dict[str, Any]:
        listed, list_response = self.fetch_nba_events()
        result_events = []
        errors = []
        for event, league in listed:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            try:
                # First detail request observed in HAR uses version=0/specialVersion=0.
                detail = self.fetch_event_detail(event_id)
                normal = detail.get("normal")
                if not isinstance(normal, dict):
                    # Some subsequent delta polls return normal=null; the full
                    # list event remains the fallback source of current markets.
                    normal = event
                    detail_source = "LIST_FALLBACK"
                else:
                    detail_source = "EVENT_DETAIL"
                rows = extract_full_game_totals(normal, league, detail_source)
                result_events.append({
                    **event_identity(event, league),
                    "detail_source": detail_source,
                    "detail_version": detail.get("version"),
                    "special_version": detail.get("specialVersion"),
                    "full_game_totals": rows,
                })
            except Exception as exc:
                errors.append({"event_id": event_id, "error": str(exc)})
        return {
            "source": "PINGBO",
            "sport_id": SPORT_ID_BASKETBALL,
            "league_id": NBA_LEAGUE_ID,
            "list_version": list_response.get("version"),
            "nba_events": len(listed),
            "events": result_events,
            "errors": errors,
        }


def _headers_from_env() -> dict[str, str]:
    raw = os.environ.get("PINGBO_READONLY_HEADERS_JSON", "")
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("PINGBO_READONLY_HEADERS_JSON must be a JSON object of string headers")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Pingbo NBA full-game totals reader (read-only)")
    parser.add_argument("--out", default="pingbo_nba_totals.json")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    client = PingboClient(headers=_headers_from_env(), timeout=args.timeout)
    result = client.fetch_nba_details()
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "events"},
                     ensure_ascii=False, indent=2))
    print(f"结果已写入 {args.out}")


if __name__ == "__main__":
    main()
