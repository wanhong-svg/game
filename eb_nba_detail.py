#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only EB NBA event/detail parser.

Discovers regular NBA events from noLiveMatchesPagePB, fetches each event's
detail/category/odds endpoints, and returns Under selections from full-game
totals (hpid=38) that were not already displayed in the list response.

No bet/order/wallet endpoints are called by this module.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENDPOINTS = json.loads((ROOT / "endpoints.json").read_text(encoding="utf-8"))
EB_CONFIG = ENDPOINTS["apis"]["eb"]
LIST_PATH = "/yewu11/v1/m/noLiveMatchesPagePB"
DETAIL_PATH = "/yewu11/v1/w/matchDetail/getMatchDetailPB"
CATEGORY_PATH = "/yewu11/v1/w/category/getCategoryList"
ODDS_PATH = "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB"
FULL_GAME_TOTAL_HPID = "38"
SUCCESS_CODES = {"0000000", "0", 0, None}


class EBApiError(RuntimeError):
    """Raised for HTTP, API, or payload decoding failures."""


def _decode_compressed_data(value: str) -> Any:
    try:
        raw = base64.b64decode("".join(value.split()), validate=True)
        raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise EBApiError(f"无法解码 EB Base64/Gzip data 字段: {exc}") from exc


def unwrap_response(payload: Any) -> Any:
    """Unwrap the EB {code,data,msg,ts} envelope and decode compressed data."""
    if not isinstance(payload, dict) or "data" not in payload:
        return payload
    code = payload.get("code")
    if code not in SUCCESS_CODES:
        raise EBApiError(f"EB API 返回错误 code={code!r}, msg={payload.get('msg', '')}")
    data = payload["data"]
    if isinstance(data, str):
        return _decode_compressed_data(data)
    return data


def is_regular_nba(event: dict[str, Any]) -> bool:
    """Require basketball sport id and an exact NBA league label.

    Both tn and tnjc are checked when present; conflicting labels fail closed.
    """
    if str(event.get("csid", "")) != "2":
        return False
    labels = [str(event.get(k) or "").strip().upper() for k in ("tn", "tnjc")]
    labels = [x for x in labels if x]
    return bool(labels) and all(x == "NBA" for x in labels)


def event_identity(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "mid": str(event.get("mid") or event.get("id") or ""),
        "league": event.get("tnjc") or event.get("tn"),
        "sport_id": str(event.get("csid") or ""),
        "home_team": event.get("mhn"),
        "home_team_id": str(event.get("mhid") or ""),
        "away_team": event.get("man"),
        "away_team_id": str(event.get("maid") or ""),
        "tournament_id": str(event.get("tid") or ""),
        "start_time_ms": str(event.get("mgt") or ""),
        "display_time": event.get("mlet"),
        "match_code": event.get("mcid"),
    }


def displayed_full_game_total_hids(event: dict[str, Any]) -> set[str]:
    """Collect full-game total market IDs already shown in the list row."""
    ids: set[str] = set()
    for market in event.get("hps") or []:
        if not isinstance(market, dict) or str(market.get("hpid")) != FULL_GAME_TOTAL_HPID:
            continue
        for line in market.get("hl") or []:
            if isinstance(line, dict) and line.get("hid") not in (None, ""):
                ids.add(str(line["hid"]))
    return ids


def _decimal_odds(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # EB's ov values in the supplied HAR are integer odds scaled by 100000.
    if value >= 10000:
        value /= Decimal(100000)
    return value if value > 1 else None


def extract_extra_under_markets(
    event: dict[str, Any], odds_payload: Any
) -> list[dict[str, Any]]:
    """Return only additional full-game Under selections not in list hps."""
    displayed_hids = displayed_full_game_total_hids(event)
    identity = event_identity(event)
    result: list[dict[str, Any]] = []

    markets = odds_payload if isinstance(odds_payload, list) else []
    for market in markets:
        if not isinstance(market, dict):
            continue
        if str(market.get("hpid")) != FULL_GAME_TOTAL_HPID:
            continue
        for line in market.get("hl") or []:
            if not isinstance(line, dict):
                continue
            hid = str(line.get("hid") or "")
            if not hid or hid in displayed_hids:
                continue
            for option in line.get("ol") or []:
                if not isinstance(option, dict):
                    continue
                selection_code = str(option.get("ot") or "").strip().lower()
                if selection_code != "under":
                    continue
                odds = _decimal_odds(option.get("ov", option.get("ov2")))
                if odds is None:
                    continue
                result.append({
                    **identity,
                    "period": "FULL_GAME",
                    "market_type": "TOTAL",
                    "hpid": FULL_GAME_TOTAL_HPID,
                    "market_name": market.get("hpn"),
                    "hid": hid,
                    "handicap": str(line.get("hv") or ""),
                    "line_number": line.get("hn"),
                    "selection": "Under",
                    "oid": str(option.get("oid") or ""),
                    "odds": str(odds),
                    "raw_odds": option.get("ov"),
                    "market_status": line.get("hs"),
                    "selection_status": option.get("os"),
                    "source": "EB",
                })
    return result


class EBClient:
    """Small read-only HTTP client; credentials are supplied locally by caller."""

    def __init__(
        self,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ):
        self.base_url = (base_url or os.environ.get(
            "EB_API_BASE_URL", EB_CONFIG.get("nba_read_base_url", EB_CONFIG["base_url"])
        )).rstrip("/")
        host = urllib.parse.urlsplit(self.base_url).hostname
        if urllib.parse.urlsplit(self.base_url).scheme != "https":
            raise ValueError("EB API base URL must use HTTPS")
        if host not in set(EB_CONFIG.get("hosts", [])):
            raise ValueError(f"EB API host is not allow-listed in endpoints.json: {host}")
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            **(headers or {}),
        }
        self.timeout = timeout

    def _request(self, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None) -> Any:
        allowed = {LIST_PATH, DETAIL_PATH, CATEGORY_PATH, ODDS_PATH}
        if path not in allowed:
            raise ValueError(f"EB read endpoint is not allow-listed: {path}")
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Never include request headers/cookies in error output.
            raise EBApiError(f"EB HTTP {exc.code} at {path}") from exc
        except urllib.error.URLError as exc:
            raise EBApiError(f"EB network error at {path}: {exc.reason}") from exc
        try:
            return unwrap_response(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EBApiError(f"EB returned invalid JSON at {path}") from exc

    def fetch_event_pages(self, body_template: dict[str, Any] | None = None,
                          page_size: int = 50, max_pages: int | None = None) -> list[dict[str, Any]]:
        body = dict(body_template or {})
        body.setdefault("type", 3)
        body.setdefault("sort", 1)
        body.setdefault("device", "v2_h5")
        body.setdefault("euid", os.environ.get("EB_EUID", ""))
        body.setdefault("cuid", os.environ.get("EB_CUID", ""))
        body["cps"] = page_size
        body["cpn"] = 1
        first = self._request(LIST_PATH, body=body)
        if not isinstance(first, dict):
            raise EBApiError("赛事列表解压结果不是分页对象")
        pages = int(first.get("pages") or 1)
        if max_pages is not None:
            pages = min(pages, max_pages)
        events = list(first.get("data") or [])
        for page in range(2, pages + 1):
            next_body = {**body, "cpn": page}
            payload = self._request(LIST_PATH, body=next_body)
            if not isinstance(payload, dict):
                raise EBApiError(f"赛事列表第 {page} 页不是分页对象")
            events.extend(payload.get("data") or [])
        return events

    def fetch_event_detail(self, mid: str, cuid: str | None = None) -> dict[str, Any]:
        detail = self._request(DETAIL_PATH, params={"mid": mid})
        category = self._request(CATEGORY_PATH, params={"sportId": 2, "mid": mid})
        odds_params = {"mid": mid, "mcid": 0, "cuid": cuid if cuid is not None else os.environ.get("EB_CUID", "")}
        odds = self._request(ODDS_PATH, params=odds_params)
        return {"detail": detail, "categories": category, "odds": odds}

    def discover_extra_under(self, body_template: dict[str, Any] | None = None,
                             page_size: int = 50, max_pages: int | None = None,
                             max_events: int | None = None) -> dict[str, Any]:
        events = [e for e in self.fetch_event_pages(body_template, page_size, max_pages)
                  if isinstance(e, dict) and is_regular_nba(e)]
        if max_events is not None:
            events = events[:max_events]
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for event in events:
            identity = event_identity(event)
            if not identity["mid"]:
                continue
            try:
                detail = self.fetch_event_detail(identity["mid"])
                rows.extend(extract_extra_under_markets(event, detail["odds"]))
            except Exception as exc:
                errors.append({"mid": identity["mid"], "error": str(exc)})
        return {
            "source": "EB",
            "sport": "BASKETBALL",
            "league_filter": "NBA",
            "events_scanned": len(events),
            "extra_under_count": len(rows),
            "extra_under_markets": rows,
            "errors": errors,
        }


def _headers_from_env() -> dict[str, str]:
    raw = os.environ.get("EB_READONLY_HEADERS_JSON", "")
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("EB_READONLY_HEADERS_JSON must be a JSON object of string headers")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="EB NBA additional full-game Under market discovery (read-only)")
    parser.add_argument("--out", default="eb_nba_extra_under.json")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    client = EBClient(headers=_headers_from_env(), timeout=args.timeout)
    result = client.discover_extra_under(page_size=args.page_size,
                                         max_pages=args.max_pages,
                                         max_events=args.max_events)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "extra_under_markets"},
                     ensure_ascii=False, indent=2))
    print(f"结果已写入 {args.out}")


if __name__ == "__main__":
    main()
