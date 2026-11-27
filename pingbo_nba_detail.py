#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Pingbo basketball/NBA list-to-event detail client and parser.

The browser can remain on the basketball event list: this client calls the same
JSON GET endpoints in the HAR directly. No browser navigation or bet endpoint
is used. Credentials/cookies are supplied only via local environment variables.
"""
from __future__ import annotations

import argparse
import hashlib
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



ANALYSIS_DIR = Path(os.environ.get("PINGBO_API_ANALYSIS_DIR", str(Path("live_snapshots") / "pingbo_api_analysis")))
ANALYSIS_STAMP = time.strftime("%Y%m%d_%H%M%S", time.localtime()) + f"_{time.time_ns() % 1_000_000_000:09d}"
ANALYSIS_FILE = Path(os.environ.get("PINGBO_API_ANALYSIS_FILE", str(ANALYSIS_DIR / f"pingbo_api_trace_{ANALYSIS_STAMP}.jsonl")))
SENSITIVE_KEYS = ("cookie", "token", "authorization", "password", "passwd", "secret", "session", "credential")

def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if any(x in str(k).lower() for x in SENSITIVE_KEYS) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(x) for x in value]
    return value

def _trace(record: dict[str, Any]) -> None:
    try:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        with ANALYSIS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **_redact(record)}, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        print(f"[Pingbo分析] trace write failed: {type(exc).__name__}: {exc}")

def _latest_browser_params(path: str) -> dict[str, Any] | None:
    if not ANALYSIS_FILE.exists():
        return None
    try:
        for line in reversed(ANALYSIS_FILE.read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            if row.get("kind") == "browser_auto_request" and row.get("path") == path:
                return row.get("request_params")
    except Exception:
        return None
    return None

def _flatten_params(value: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(_flatten_params(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        out[prefix] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        out[prefix] = str(value)
    return out

def _compare_page_params(path: str, script_params: dict[str, Any]) -> dict[str, Any]:
    page_params = _latest_browser_params(path)
    if page_params is None:
        return {"browser_request_found": False, "note": "本地 trace 中尚无该接口的页面自动请求样本"}
    page_flat, script_flat = _flatten_params(page_params), _flatten_params(script_params)
    keys = sorted(set(page_flat) | set(script_flat))
    return {"browser_request_found": True,
            "same": page_flat == script_flat,
            "only_in_browser": {k: page_flat[k] for k in keys if k in page_flat and k not in script_flat},
            "only_in_script": {k: script_flat[k] for k in keys if k in script_flat and k not in page_flat},
            "different_values": {k: {"browser": page_flat[k], "script": script_flat[k]} for k in keys if k in page_flat and k in script_flat and page_flat[k] != script_flat[k]}}

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
        "raw_status": event.get("status") or event.get("matchStatus") or event.get("state"),
        "is_live": event.get("isLive") if isinstance(event.get("isLive"), bool) else event.get("live") if isinstance(event.get("live"), bool) else None,
        "phase_code": event.get("periodLabel") or event.get("periodName"),
        "period_label": event.get("periodLabel") or event.get("periodName"),
        "clock_seconds": event.get("clockSeconds") or event.get("elapsedSeconds") or event.get("remainingSeconds"),
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


TEAM_ALIASES = {
    "奥兰多魔术": "orlandomagic", "奥兰多魔术队": "orlandomagic",
    "底特律活塞": "detroitpistons", "底特律活塞队": "detroitpistons",
    "华盛顿奇才": "washingtonwizards", "华盛顿奇才队": "washingtonwizards",
    "波士顿凯尔特人": "bostonceltics", "波士顿凯尔特人队": "bostonceltics",
    "丹佛掘金": "denvernuggets", "丹佛掘金队": "denvernuggets",
    "达拉斯小牛": "dallasmavericks", "达拉斯独行侠": "dallasmavericks",
    "达拉斯小牛队": "dallasmavericks", "达拉斯独行侠队": "dallasmavericks",
}


def normalize_team_name(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().strip()
    if text in TEAM_ALIASES:
        return TEAM_ALIASES[text]
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
        eb_home = normalize_team_name(eb.get("home_team_en") or eb.get("home_team"))
        eb_away = normalize_team_name(eb.get("away_team_en") or eb.get("away_team"))
        key = (eb_home, eb_away, f"{points}|{side}")
        pb = by_key.get(key)
        if not pb:
            # Do not guess reversed home/away orientation: only accept a
            # canonical team-pair match from known English/Chinese aliases.
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


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial event update without dropping untouched nested market fields."""
    merged = dict(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def extract_moneyline(event: dict[str, Any]) -> dict[str, Any] | list[Any] | None:
    """Preserve moneyLine and normalize only explicitly recognizable side/odds fields."""
    source = event.get("moneyLine")
    if source is None:
        source = event.get("moneyline")
    # Confirmed Pingbo schema: moneyLine is nested under periods["0"].
    periods = event.get("periods")
    if source is None and isinstance(periods, dict):
        full_game = periods.get("0")
        if isinstance(full_game, dict):
            source = full_game.get("moneyLine")
            if source is None:
                source = full_game.get("moneyline")
    if source in (None, "", [], {}):
        return None

    side_aliases = {
        "home": {"home", "homeodds", "homeodd", "homeprice", "homemoneyline", "h"},
        "away": {"away", "awayodds", "awayodd", "awayprice", "awaymoneyline", "a"},
        "draw": {"draw", "drawodds", "drawodd", "drawprice", "drawmoneyline", "d"},
    }
    selections = []

    def odds_value(value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("odds", "odd", "price", "value", "oddsValue", "decimalOdds", "americanOdds"):
                if value.get(key) not in (None, ""):
                    return value.get(key)
        return value if isinstance(value, (str, int, float)) else None

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                side = next((name for name, aliases in side_aliases.items() if normalized in aliases), None)
                if side:
                    raw_odds = odds_value(value)
                    if raw_odds is not None:
                        selections.append({"side": side, "raw_odds": raw_odds,
                                           
                                           "source_path": f"{path}.{key}".strip(".")})
                elif isinstance(value, (dict, list)):
                    walk(value, f"{path}.{key}".strip("."))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, dict):
                    side_value = str(value.get("side") or value.get("type") or value.get("selection") or "").strip().lower()
                    side = next((name for name, aliases in side_aliases.items()
                                 if side_value.replace("_", "").replace("-", "") in aliases), None)
                    raw_odds = next((value.get(k) for k in ("odds", "odd", "price", "value", "oddsValue", "decimalOdds", "americanOdds")
                                     if value.get(k) not in (None, "")), None)
                    if side and raw_odds is not None:
                        selections.append({"side": side, "raw_odds": raw_odds,
                                           "source_path": f"{path}[{index}]".strip(".")})
                    walk(value, f"{path}[{index}]".strip("."))

    walk(source)
    return {"raw": source, "selections": selections}


def period_market_counts(event: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Count market arrays by period without assuming every market family is present."""
    periods = event.get("periods")
    if not isinstance(periods, dict):
        return {}
    result = {}
    for period_id, period in periods.items():
        if not isinstance(period, dict):
            continue
        result[str(period_id)] = {
            str(market_name): (len(value) if isinstance(value, (list, dict)) else 0)
            for market_name, value in period.items()
            if isinstance(value, (list, dict))
        }
    return result


class PingboClient:
    def __init__(self, base_url: str | None = None,
                 headers: dict[str, str] | None = None, timeout: int = 15,
                 state_path: str | Path | None = None):
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
        self.state_path = Path(state_path) if state_path else Path(
            os.environ.get("PINGBO_VERSION_STATE_FILE", str(ANALYSIS_DIR / "pingbo_client_state.json")))
        self._state = self._load_state()
        self.list_version = self._state.get("list_version")
        self._event_cache: dict[str, dict[str, Any]] = self._state.get("events", {})
        self._detail_versions: dict[str, dict[str, Any]] = self._state.get("detail_versions", {})

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            _trace({"kind": "pingbo_state_load_error", "error_type": type(exc).__name__, "error": str(exc)})
            return {}

    def _save_state(self) -> None:
        self._state = {"list_version": self.list_version, "events": self._event_cache,
                       "detail_versions": self._detail_versions,
                       "saved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.state_path)
        except Exception as exc:
            _trace({"kind": "pingbo_state_save_error", "error_type": type(exc).__name__, "error": str(exc)})

    def _record_market_changes(self, event_id: str, event: dict[str, Any]) -> None:
        previous_record = self._event_cache.get(event_id, {})
        previous = previous_record.get("event", previous_record) if isinstance(previous_record, dict) else {}
        old_markets = {"periods": previous.get("periods"), "moneyLine": previous.get("moneyLine", previous.get("moneyline"))}
        new_markets = {"periods": event.get("periods"), "moneyLine": event.get("moneyLine", event.get("moneyline"))}
        old_hash = hashlib.sha256(json.dumps(old_markets, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        new_hash = hashlib.sha256(json.dumps(new_markets, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        if old_hash != new_hash:
            _trace({"kind": "event_market_change", "event_id": event_id,
                    "changed": previous != {}, "previous_market_sha256": old_hash,
                    "current_market_sha256": new_hash,
                    "period_market_counts": period_market_counts(event),
                    "moneyline_selection_count": len((extract_moneyline(event) or {}).get("selections", []))
                    if isinstance(extract_moneyline(event), dict) else 0})

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if path not in READ_PATHS:
            raise ValueError(f"Pingbo read endpoint not allow-listed: {path}")
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        request_url = self.base_url + path + "?" + query
        req = urllib.request.Request(request_url, headers=self.headers, method="GET")
        script_params = {"query": {k: [str(v)] for k, v in params.items() if v is not None}}
        _trace({"kind": "script_request", "source": "pingbo_nba_detail.PingboClient",
                "method": "GET", "url_without_query": self.base_url + path, "path": path,
                "request_params": script_params,
                "page_vs_script_parameter_diff": _compare_page_params(path, script_params)
                if path in {LIST_PATH, LIVE_PATH} else None})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raise PingboApiError(f"Pingbo HTTP {exc.code} at {path}") from exc
        except urllib.error.URLError as exc:
            raise PingboApiError(f"Pingbo network error at {path}: {exc.reason}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
            _trace({"kind": "script_response", "source": "pingbo_nba_detail.PingboClient",
                    "method": "GET", "url_without_query": self.base_url + path, "path": path,
                    "http_status": status, "response_data": payload})
            return payload
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PingboApiError(f"Pingbo invalid JSON at {path}") from exc

    def fetch_leagues(self) -> list[dict[str, Any]]:
        data = self._get(LEAGUES_PATH, {"mk": 1, "sportId": SPORT_ID_BASKETBALL,
                         "c": "Others", "periodNumber": 0, "locale": "zh_CN",
                         "_": int(time.time() * 1000), "withCredentials": "true"})
        return data if isinstance(data, list) else []

    def fetch_nba_events(self, version: int | None = None) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
        # Reuse the last response version across runs; an explicit argument still overrides it.
        request_version = self.list_version if version is None and self.list_version is not None else (version or 0)
        params = {"sportId": SPORT_ID_BASKETBALL, "periodNumber": 0,
                  "periodNumbers": -1, "betTypes": "HDP,OU,ML_1X2",
                  "locale": "zh_CN", "version": request_version,
                  "_": int(time.time() * 1000), "withCredentials": "true"}
        data = self._get(LIST_PATH, params)
        if not isinstance(data, dict):
            raise PingboApiError("odds-today response is not an object")
        raw_leagues = data.get("leagues")
        if raw_leagues is not None and not isinstance(raw_leagues, list):
            raise PingboApiError("odds-today response field 'leagues' is not a list")

        response_sport_id = data.get("sportId")
        incoming: dict[str, dict[str, Any]] = {}
        for league in raw_leagues or []:
            if not isinstance(league, dict) or not is_nba_league(
                    {**league, "sportId": league.get("sportId") or response_sport_id}):
                continue
            for event in league.get("events", []) or []:
                if isinstance(event, dict) and event.get("id") is not None:
                    event_id = str(event["id"])
                    incoming[event_id] = {"event": event, "league": league}

        refresh_all = data.get("refreshAll")
        update_payload = data.get("update")
        update_records = 0

        def apply_update(node: Any) -> None:
            nonlocal update_records
            if isinstance(node, dict):
                event_id_value = node.get("eventId") or node.get("id")
                patch = node.get("normal") if isinstance(node.get("normal"), dict) else node
                if event_id_value is not None and any(
                        key in patch for key in ("periods", "participants", "moneyLine", "moneyline", "status", "time")):
                    event_id = str(event_id_value)
                    cached = self._event_cache.get(event_id) or incoming.get(event_id)
                    if cached:
                        merged_event = _deep_merge(cached.get("event", {}), patch)
                        incoming[event_id] = {"event": merged_event, "league": cached.get("league", {
                            "id": NBA_LEAGUE_ID, "sportId": SPORT_ID_BASKETBALL, "name": "NBA"})}
                        update_records += 1
                    elif isinstance(patch.get("participants"), list):
                        incoming[event_id] = {"event": patch, "league": {
                            "id": NBA_LEAGUE_ID, "sportId": SPORT_ID_BASKETBALL, "name": "NBA"}}
                        update_records += 1
                for child in node.values():
                    if isinstance(child, (dict, list)):
                        apply_update(child)
            elif isinstance(node, list):
                for child in node:
                    apply_update(child)

        if refresh_all is not True and update_payload not in (None, {}, []):
            apply_update(update_payload)

        if refresh_all is True:
            self._event_cache = incoming
            cache_action = "replace_full_snapshot"
        else:
            # refreshAll=False (or omitted) is a delta: merge known updates, never clear on empty.
            self._event_cache.update(incoming)
            cache_action = "merge_incremental" if incoming else "preserve_empty_delta"

        if data.get("version") is not None:
            self.list_version = data.get("version")
        self._save_state()
        events = [(item["event"], item["league"]) for item in self._event_cache.values()
                  if isinstance(item, dict) and isinstance(item.get("event"), dict)
                  and isinstance(item.get("league"), dict)]
        _trace({"kind": "nba_list_cache_summary", "request_version": request_version,
                "response_version": self.list_version, "refreshAll": refresh_all,
                "cache_action": cache_action, "incoming_event_count": len(incoming),
                "update_record_count": update_records,
                "cached_event_count": len(events)})
        return events, data

    def fetch_event_detail(self, event_id: str, version: int = 0,
                           special_version: int = 0, time_rtk: int | None = None) -> dict[str, Any]:
        saved = self._detail_versions.get(str(event_id), {})
        params = {"eventId": event_id,
                  "version": saved.get("version", version),
                  "specialVersion": saved.get("specialVersion", special_version),
                  "locale": "zh_CN", "_": int(time.time() * 1000),
                  "withCredentials": "true", "timeRTK": time_rtk}
        data = self._get(EVENT_PATH, params)
        if not isinstance(data, dict):
            raise PingboApiError("event detail response is not an object")
        self._detail_versions[str(event_id)] = {
            "version": data.get("version", params["version"]),
            "specialVersion": data.get("specialVersion", params["specialVersion"]),
        }
        self._save_state()
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
                moneyline = extract_moneyline(normal)
                self._record_market_changes(event_id, normal)
                self._event_cache[event_id] = {"event": normal, "league": league}
                result_events.append({
                    **event_identity(event, league),
                    "detail_source": detail_source,
                    "detail_version": detail.get("version"),
                    "special_version": detail.get("specialVersion"),
                    "full_game_totals": rows,
                    "moneyline": moneyline,
                    "period_market_counts": period_market_counts(normal),
                })
            except Exception as exc:
                errors.append({"event_id": event_id, "error": str(exc)})
                # Detail failure must not erase the event or its list-embedded markets.
                fallback_rows = extract_full_game_totals(event, league, "LIST_FALLBACK_DETAIL_ERROR")
                result_events.append({
                    **event_identity(event, league),
                    "detail_source": "LIST_FALLBACK_DETAIL_ERROR",
                    "detail_version": None, "special_version": None,
                    "full_game_totals": fallback_rows,
                    "moneyline": extract_moneyline(event),
                    "period_market_counts": period_market_counts(event),
                })
        self._save_state()
        return {
            "source": "PINGBO",
            "sport_id": SPORT_ID_BASKETBALL,
            "league_id": NBA_LEAGUE_ID,
            "list_version": self.list_version,
            "nba_events": len(listed),
            "events": result_events,
            "errors": errors,
            "diagnostics": {
                "listed_count": len(listed), "result_events_count": len(result_events),
                "detail_error_count": len(errors),
                "moneyline_event_count": sum(1 for event in result_events if event.get("moneyline")),
                "period_market_counts": {event["event_id"]: event.get("period_market_counts", {})
                                         for event in result_events},
            },
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
