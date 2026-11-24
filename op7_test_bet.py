#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded local-test wager flow. Network submission is disabled by default."""
import argparse
import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

LATEST_PATH = "/yewu13/v1/betOrder/client/queryLatestMarketInfo"
LIMIT_PATH = "/yewu13/v1/betOrder/client/queryMarketMaxMinBetMoney"
BET_PATH = "/yewu13/v1/betOrder/client/bet"


def as_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def build_latest_market_request(selection):
    event, market, group, option = (selection[k] for k in ("event", "market", "group", "option"))
    return {"idList": [{
        "matchInfoId": str(event["mid"]), "marketId": str(group["hid"]),
        "sportId": int(event["csid"]), "oddsId": str(option["oid"]),
        "oddsType": str(option["ot"]), "playId": str(market["hpid"]),
        "placeNum": int(selection["place_num"]), "matchType": int(selection["match_type"])
    }], "cuid": str(selection["cuid"])}


def build_limit_request(selection, odds_value):
    event, market, group, option = (selection[k] for k in ("event", "market", "group", "option"))
    odds_decimal = Decimal(int(odds_value)) / Decimal(100000)
    return {"orderMaxBetMoney": [{
        "deviceType": int(selection.get("device_type", 2)),
        "oddsFinally": format(odds_decimal, "f"), "oddsValue": int(odds_value),
        "playId": str(market["hpid"]), "playOptionId": str(option["oid"]),
        "matchId": str(event["mid"]), "marketId": str(group["hid"]),
        "matchType": int(selection["match_type"]), "openMiltSingle": 1
    }], "cuid": str(selection["cuid"])}


def build_bet_payload(selection, stake, odds_value=None):
    event, market, group, option = (selection[k] for k in ("event", "market", "group", "option"))
    amount = as_decimal(stake)
    if amount is None or amount <= 0:
        raise ValueError("stake must be a positive number")
    raw_odds = int(odds_value if odds_value is not None else option["ov"])
    if raw_odds <= 100000:
        raise ValueError("odds must be greater than 1.00")
    if not selection.get("cuid") or not selection.get("device_imei"):
        raise ValueError("cuid and device_imei must be supplied from the local test session")
    play_option_name = option.get("otv") or option.get("onb") or option.get("on")
    if not play_option_name:
        raise ValueError("selection has no display name (otv/onb/on)")
    odds_decimal = Decimal(raw_odds) / Decimal(100000)
    detail = {
        "playId": str(market["hpid"]), "matchType": int(selection["match_type"]),
        "marketTypeFinally": str(selection.get("market_type_finally", "EU")),
        "playOptionsId": str(option["oid"]), "placeNum": int(selection["place_num"]),
        "betAmount": format(amount, "f"), "matchId": str(event["mid"]),
        "marketId": str(group["hid"]), "odds": raw_odds,
        "oddFinally": format(odds_decimal, "f"), "sportId": int(event["csid"]),
        "sportName": str(event["csna"]), "tournamentId": int(event["tid"]),
        "playOptionName": str(play_option_name), "playOptions": str(option["ot"]),
        "playName": str(market["hpn"]), "matchName": str(event.get("tn") or event.get("tnjc") or ""),
        "marketValue": str(group.get("hv") or ""),
        "scoreBenchmark": str(selection.get("score_benchmark") or "")
    }
    return {
        "useAcceptOdds": 1, "deviceType": int(selection.get("device_type", 2)),
        "deviceImei": str(selection["device_imei"]), "preBet": 0,
        "seriesOrders": [{"seriesType": 1, "seriesSum": 1, "fullBet": 0, "orderDetailList": [detail]}],
        "cuid": str(selection["cuid"]), "openMiltSingle": 1
    }


def _allowed_test_host(host):
    host = host.lower().rstrip(".")
    explicit = {x.strip().lower().rstrip(".") for x in os.environ.get("OP7_TEST_BET_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if host in explicit:
        return True
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".localhost", ".local", ".test")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def validate_test_target(base_url):
    if os.environ.get("OP7_TEST_BET_ENABLED") != "1":
        raise RuntimeError("Bet submission is locked; set OP7_TEST_BET_ENABLED=1 and pass --submit-test")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OP7_TEST_BET_BASE_URL must be an http(s) URL without embedded credentials")
    if not _allowed_test_host(parsed.hostname):
        raise RuntimeError(
            f"Host {parsed.hostname} is not on the local test allowlist. Verify it is non-production, "
            "then add its exact hostname to OP7_TEST_BET_ALLOWED_HOSTS."
        )
    return base_url.rstrip("/")


def _headers():
    value = json.loads(os.environ.get("OP7_TEST_BET_HEADERS_JSON", "{}"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("OP7_TEST_BET_HEADERS_JSON must be a JSON object of string headers")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers.update(value)
    return headers


def post_json(base_url, path, body, headers, timeout=15):
    req = urllib.request.Request(
        base_url + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{path} network error: {exc.reason}") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"{path} returned non-JSON response") from None


def require_success(response, endpoint):
    if not isinstance(response, dict) or str(response.get("code")) != "0000000":
        code = response.get("code") if isinstance(response, dict) else "INVALID_RESPONSE"
        raise RuntimeError(f"{endpoint} rejected request (code={code}); no bet was submitted")
    return response.get("data")


def submit_test_bet(selection, stake, timeout=15):
    base_url = validate_test_target(os.environ.get("OP7_TEST_BET_BASE_URL", ""))
    headers = _headers()
    latest_response = post_json(base_url, LATEST_PATH, build_latest_market_request(selection), headers, timeout)
    latest_rows = require_success(latest_response, LATEST_PATH)
    if not isinstance(latest_rows, list):
        raise RuntimeError("latest market response has no data list; stopped before bet")
    latest = next((x for x in latest_rows if str(x.get("id")) == str(selection["group"]["hid"])), None)
    if latest is None:
        raise RuntimeError("selected market is no longer available; stopped before bet")
    if latest.get("matchOver") not in (0, "0") or latest.get("matchStatus") not in (1, "1"):
        raise RuntimeError("match is over or not in an accepted state; stopped before bet")
    if latest.get("pendingOrderStatus") not in (1, "1"):
        raise RuntimeError("market does not accept pending orders; stopped before bet")
    latest_options = latest.get("marketOddsList") or []
    latest_option = next((x for x in latest_options
        if str(x.get("id")) == str(selection["option"]["oid"])
        and str(x.get("oddsType")) == str(selection["option"]["ot"])), None)
    if latest_option is None or latest_option.get("oddsStatus") not in (1, "1"):
        raise RuntimeError("selected odds are unavailable; stopped before bet")
    odds_value = latest_option.get("oddsValue")
    if not isinstance(odds_value, (int, float)) or int(odds_value) <= 100000:
        raise RuntimeError("latest odds value is invalid; stopped before bet")

    limit_response = post_json(base_url, LIMIT_PATH, build_limit_request(selection, int(odds_value)), headers, timeout)
    limit_rows = require_success(limit_response, LIMIT_PATH)
    if not isinstance(limit_rows, list) or not limit_rows or not limit_rows[0].get("minBet"):
        raise RuntimeError("stake limit response is incomplete; stopped before bet")
    min_stake = as_decimal(limit_rows[0]["minBet"])
    amount = as_decimal(stake)
    if min_stake is None or amount is None or amount < min_stake:
        raise RuntimeError("stake is below the confirmed minimum; stopped before bet")

    payload = build_bet_payload(selection, amount, odds_value=int(odds_value))
    bet_response = post_json(base_url, BET_PATH, payload, headers, timeout)
    bet_data = require_success(bet_response, BET_PATH)
    details = bet_data.get("orderDetailRespList", []) if isinstance(bet_data, dict) else []
    return {
        "code": bet_response.get("code"), "message": bet_response.get("msg"),
        "accepted_order_count": len(details),
        "order_status_codes": [x.get("orderStatusCode") for x in details],
        "latest_odds": format(Decimal(int(odds_value)) / Decimal(100000), "f"),
        "minimum_stake": str(min_stake)
    }


def redact(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in {"cuid", "deviceimei", "cookie", "authorization"} else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


def main():
    parser = argparse.ArgumentParser(description="Build or submit one local-test OP7/EB wager")
    parser.add_argument("--input", required=True, help="local JSON with event/market/group/option fields")
    parser.add_argument("--stake", required=True, help="test stake amount")
    parser.add_argument("--submit-test", action="store_true", help="send only after local-test guards pass")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    selection = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.submit_test:
        print(json.dumps(submit_test_bet(selection, args.stake, args.timeout), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(redact(build_bet_payload(selection, args.stake)), ensure_ascii=False, indent=2))
        print("\nDRY RUN ONLY: no network request was sent.")


if __name__ == "__main__":
    main()
