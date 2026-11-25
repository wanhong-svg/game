#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded local-test wager flow. Network submission is disabled by default."""
import contextlib
import io
import argparse
import hashlib
import ipaddress
import time
import threading
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path

ENDPOINTS = json.loads((Path(__file__).with_name("endpoints.json")).read_text(encoding="utf-8"))
ORDER_AUDIT_PATH = Path(__file__).with_name("bet_order_audit.json")
MAX_SUCCESSFUL_BETS_PER_EVENT = 5
LATEST_PATH, LIMIT_PATH, BET_PATH = ENDPOINTS["apis"]["eb"]["bet_paths"]



_EXECUTION_STAGE = '脚本初始化'
_EXECUTION_STAGE_LOCK = threading.Lock()
def _set_execution_stage(stage):
    global _EXECUTION_STAGE
    with _EXECUTION_STAGE_LOCK:
        _EXECUTION_STAGE = str(stage)
def _heartbeat(stop_event):
    while not stop_event.wait(5):
        with _EXECUTION_STAGE_LOCK:
            stage = _EXECUTION_STAGE
        from op7_fb_eb_pingbo_live_refresh import USER_INPUT_WAITING
        if not USER_INPUT_WAITING.is_set():
            print('[执行状态] 当前环节=' + stage, flush=True)

def load_runtime_endpoints():
    return json.loads(Path(__file__).with_name("endpoints.json").read_text(encoding="utf-8"))


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
        "matchType": int(selection["match_type"])
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


def build_bet_payload(selection, stake, odds_value=None, latest_market=None):
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
    if not all(event.get(k) not in (None, "") for k in ("mid", "tid", "csid", "csna")):
        raise ValueError("event is missing one of mid/tid/csid/csna")
    if not market.get("hpid") or not market.get("hpn") or not group.get("hid") or not option.get("oid"):
        raise ValueError("market/group/option is missing required live IDs or names")
    if not selection.get("market_type_finally"):
        raise ValueError("market_type_finally must come from the live market context; no default is assumed")
    odds_decimal = Decimal(raw_odds) / Decimal(100000)
    detail = {
        "playId": str(market["hpid"]), "matchType": int(selection["match_type"]),
        "marketTypeFinally": str(selection["market_type_finally"]),
        "playOptionsId": str(option["oid"]),
        "betAmount": format(amount, "f"), "matchId": str(event["mid"]),
        "marketId": str(group["hid"]), "odds": raw_odds,
        "oddFinally": format(odds_decimal, "f"), "sportId": int(event["csid"]),
        "sportName": str(event["csna"]), "tournamentId": int(event["tid"]),
        "playOptionName": str(play_option_name), "playOptions": str(option["ot"]),
        "playName": str(market["hpn"]), "matchName": str(event.get("tn") or event.get("tnjc") or ""),
        "marketValue": str(group.get("hv") or ""),
        "scoreBenchmark": str(selection.get("score_benchmark") or "")
    }
    if not isinstance(latest_market, dict):
        raise ValueError("queryLatestMarketInfo result is required to assemble the bet position field")
    latest_place_num = latest_market.get("placeNum")
    if latest_place_num not in (None, ""):
        try:
            detail["placeNum"] = int(latest_place_num)
        except (TypeError, ValueError):
            raise ValueError("queryLatestMarketInfo.placeNum is invalid; stopped before bet") from None
    else:
        latest_play_name = latest_market.get("playName")
        if latest_play_name in (None, ""):
            raise ValueError("queryLatestMarketInfo returned neither placeNum nor playName; stopped before bet")
        # API schema fallback: when placeNum is absent, send playName instead.
        detail["playName"] = str(latest_play_name)

    return {
        "useAcceptOdds": 1, "deviceType": int(selection.get("device_type", 2)),
        "deviceImei": str(selection["device_imei"]), "preBet": 0,
        "seriesOrders": [{"seriesType": 1, "seriesSum": 1, "fullBet": 0, "orderDetailList": [detail]}],
        "cuid": str(selection["cuid"]), "openMiltSingle": 1
    }



def select_eb_full_game_total(event, line, side, *, match_type, place_num,
                              cuid, device_imei, market_type_finally):
    """Resolve a candidate against the current raw EB event tree; never reuse old IDs."""
    if str(event.get("csid")) != "2":
        raise ValueError("candidate event is not basketball (csid != 2)")
    labels = {str(event.get(k) or "").strip().upper() for k in ("tn", "tnjc")}
    labels.discard("")
    if not labels or labels != {"NBA"}:
        raise ValueError("candidate event is not an unambiguous regular NBA event")
    if side not in {"Under", "Over"}:
        raise ValueError("side must be Under or Over")
    try:
        target_line = Decimal(str(line))
    except Exception:
        raise ValueError("candidate line is invalid") from None
    matches = []
    for market in event.get("hps") or []:
        if not isinstance(market, dict) or str(market.get("hpid")) != "38":
            continue
        for group in market.get("hl") or []:
            if not isinstance(group, dict):
                continue
            try:
                group_line = Decimal(str(group.get("hv")))
            except Exception:
                continue
            if group_line != target_line:
                continue
            for option in group.get("ol") or []:
                if isinstance(option, dict) and str(option.get("ot", "")).lower() == side.lower():
                    matches.append((market, group, option))
    if len(matches) != 1:
        raise ValueError(f"EB full-game totals mapping is ambiguous: {len(matches)} matching {side} options")
    market, group, option = matches[0]
    required = {
        "event.mid": event.get("mid"), "event.tid": event.get("tid"),
        "event.csid": event.get("csid"), "event.csna": event.get("csna"),
        "event.tn": event.get("tn"), "market.hpid": market.get("hpid"),
        "market.hpn": market.get("hpn"), "group.hid": group.get("hid"),
        "option.oid": option.get("oid"), "option.ot": option.get("ot"),
        "option.ov": option.get("ov"), "option display": option.get("otv") or option.get("onb") or option.get("on"),
        "cuid": cuid, "device_imei": device_imei, "market_type_finally": market_type_finally,
    }
    missing = [key for key, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError("missing live EB bet fields: " + ", ".join(missing))
    return {
        "event": event, "market": market, "group": group, "option": option,
        "match_type": int(match_type), "place_num": int(place_num),
        "cuid": str(cuid), "device_imei": str(device_imei),
        "market_type_finally": str(market_type_finally),
    }


def validate_cross_odds_snapshot(snapshot, threshold=Decimal("1.0052")):
    """Apply the agreed A/B reciprocal-sum rule to one fresh two-book snapshot."""
    required = ("eb_under", "eb_over", "pingbo_over", "pingbo_under",
                "eb_under_line", "pingbo_over_line")
    missing = [k for k in required if snapshot.get(k) in (None, "")]
    if missing:
        raise ValueError("fresh cross-odds snapshot missing: " + ", ".join(missing))
    vals = {k: as_decimal(snapshot[k]) for k in required}
    if any(vals[k] is None for k in required):
        raise ValueError("fresh cross-odds snapshot contains invalid numeric values")
    if any(vals[k] <= 1 for k in ("eb_under", "eb_over", "pingbo_over", "pingbo_under")):
        raise ValueError("all four fresh odds must be greater than 1.00")
    if vals["eb_under_line"] < vals["pingbo_over_line"]:
        raise ValueError("EB Under line is below Pingbo Over line")
    a = Decimal(1) / vals["pingbo_over"] + Decimal(1) / vals["eb_under"]
    b = Decimal(1) / vals["pingbo_under"] + Decimal(1) / vals["eb_over"]
    pass_a, pass_b = a < threshold, b < threshold
    if pass_a == pass_b:
        raise ValueError("cross-odds result is ambiguous or not profitable; no candidate")
    return {
        "direction": "Under" if pass_a else "Over",
        "a_reciprocal_sum": str(a), "b_reciprocal_sum": str(b),
        "a_pass": pass_a, "b_pass": pass_b, "threshold": str(threshold),
    }


def refresh_and_validate_cross_odds(refresh_both, max_seconds=5.0):
    """Refresh both books within one five-second window; callback must return raw odds."""
    started = time.monotonic()
    snapshot = refresh_both()
    elapsed = time.monotonic() - started
    if elapsed > max_seconds:
        raise RuntimeError(f"two-book odds refresh took {elapsed:.3f}s (> {max_seconds:.1f}s)")
    result = validate_cross_odds_snapshot(snapshot)
    result["refresh_elapsed_seconds"] = round(elapsed, 3)
    result["snapshot"] = snapshot
    return result


def _candidate_key(selection):
    event, market, group, option = (selection[k] for k in ("event", "market", "group", "option"))
    raw = "|".join(str(x) for x in (
        event.get("mid"), market.get("hpid"), group.get("hid"),
        option.get("oid"), option.get("ot"), group.get("hv")
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _successful_bet_counts():
    """Count only confirmed ACCEPTED orders, grouped by stable EB event ID."""
    if not ORDER_AUDIT_PATH.exists():
        return {}
    try:
        payload = json.loads(ORDER_AUDIT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("bet order audit file is unreadable; refusing to apply event limits")
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise RuntimeError("bet order audit file has invalid structure")
    prepared_event_by_key = {
        str(record.get("candidate_key")): str(record.get("event_id") or "").strip()
        for record in records
        if isinstance(record, dict) and record.get("state") == "PREPARED"
        and record.get("candidate_key")
    }
    counts = {}
    for record in records:
        if not isinstance(record, dict) or record.get("state") != "ACCEPTED":
            continue
        event_id = str(
            record.get("event_id")
            or prepared_event_by_key.get(str(record.get("candidate_key")), "")
        ).strip()
        if event_id:
            counts[event_id] = counts.get(event_id, 0) + 1
    return counts


def _write_order_audit(record):
    current = {"version": 1, "records": []}
    if ORDER_AUDIT_PATH.exists():
        try:
            current = json.loads(ORDER_AUDIT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("bet order audit file is unreadable; refusing to continue")
    records = current.get("records")
    if not isinstance(records, list):
        raise RuntimeError("bet order audit file has invalid structure")
    records.append(record)
    temp = ORDER_AUDIT_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(ORDER_AUDIT_PATH)


def parse_bet_result(response):
    data = require_success(response, BET_PATH)
    details = data.get("orderDetailRespList", []) if isinstance(data, dict) else []
    if not details:
        raise RuntimeError("bet response contains no orderDetailRespList")
    orders = []
    for item in details:
        if not isinstance(item, dict) or item.get("orderStatusCode") not in (1, "1"):
            raise RuntimeError("bet response contains an unaccepted order status")
        order_no = str(item.get("orderNo") or "").strip()
        if not order_no:
            raise RuntimeError("accepted bet response is missing orderNo")
        fen = as_decimal(item.get("betMoney"))
        orders.append({
            "orderNo": order_no,
            "orderStatusCode": item.get("orderStatusCode"),
            "bet_money_fen": str(fen) if fen is not None else None,
            "bet_money_yuan": str(fen / Decimal(100)) if fen is not None else None,
            "odds": item.get("oddsValues"),
            "match": item.get("matchInfo"),
        })
    total_fen = as_decimal(data.get("betMoneyTotal")) if isinstance(data, dict) else None
    return {
        "orders": orders,
        "bet_money_total_fen": str(total_fen) if total_fen is not None else None,
        "bet_money_total_yuan": str(total_fen / Decimal(100)) if total_fen is not None else None,
    }


def _allowed_test_host(host):
    host = host.lower().rstrip(".")
    explicit = {x.strip().lower().rstrip(".") for x in os.environ.get("OP7_TEST_BET_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if host in explicit:
        return True
    # Project owner has designated the configured API hosts as their managed test environment.
    runtime = load_runtime_endpoints()
    configured = set(runtime.get("apis", {}).get("eb", {}).get("hosts", []))
    discovered = {
        str(x.get("host", "")).lower().rstrip(".")
        for x in runtime.get("discovered_endpoints", [])
        if x.get("family") == "eb"
    }
    if host in configured | discovered:
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


def post_json(base_url, path, body, headers, timeout=15, browser_session=None):
    if browser_session is not None:
        return browser_session.post_json(base_url, path, body, headers, timeout)
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


def submit_test_bet(selection, stake, timeout=15, cross_odds_refresh=None, browser_session=None):
    runtime_endpoints = load_runtime_endpoints()
    eb_config = runtime_endpoints["apis"]["eb"]
    base_url = validate_test_target(os.environ.get("OP7_TEST_BET_BASE_URL", eb_config.get("bet_api_base_url") or eb_config["base_url"]))
    headers = _headers()
    _set_execution_stage("请求 EB 最新盘口数据")
    latest_request = build_latest_market_request(selection)
    latest_response = post_json(base_url, LATEST_PATH, latest_request, headers, timeout, browser_session)
    latest_rows = require_success(latest_response, LATEST_PATH)
    if not isinstance(latest_rows, list) or not latest_rows:
        if browser_session is None:
            raise RuntimeError("EB 最新盘口接口未返回数据，当前没有可等待的浏览器会话")
        browser_session.confirm_and_wait_eb_retry(LATEST_PATH)
        _set_execution_stage("人工确认后重新请求 EB 最新盘口数据")
        latest_response = post_json(base_url, LATEST_PATH, latest_request, headers, timeout, browser_session)
        latest_rows = require_success(latest_response, LATEST_PATH)
    if not isinstance(latest_rows, list) or not latest_rows:
        raise RuntimeError("人工确认后 EB 最新盘口接口仍未返回数据；停止本次投注")
    latest = next((x for x in latest_rows if str(x.get("id")) == str(selection["group"]["hid"])), None)
    if latest is None:
        raise RuntimeError("selected market is no longer available; stopped before bet")
    if str(latest.get("matchInfoId") or "") != str(selection["event"].get("mid")):
        raise RuntimeError("queryLatestMarketInfo matchInfoId mismatch; stopped before bet")
    if str(latest.get("playId") or latest.get("chpid") or "") != str(selection["market"].get("hpid")):
        raise RuntimeError("queryLatestMarketInfo playId mismatch; stopped before bet")
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

    amount = as_decimal(stake)
    if amount is None or amount != Decimal("100"):
        raise RuntimeError("本流程固定每笔模拟投注 100 元；金额不一致，已停止")
    max_test_stake = as_decimal(os.environ.get("OP7_TEST_BET_MAX_STAKE", "100"))
    if max_test_stake is None or amount > max_test_stake:
        raise RuntimeError("stake exceeds OP7_TEST_BET_MAX_STAKE; stopped before bet")

    if cross_odds_refresh is None and browser_session is not None:
        cross_odds_refresh = lambda: browser_session.refresh_cross_odds(selection)
    if cross_odds_refresh is None:
        raise RuntimeError("未连接浏览器会话，无法自动刷新 EB/Pingbo 双边赔率；已停止")
    _set_execution_stage("投注前最后一次 EB/Pingbo 赔率复核")
    cross_check = refresh_and_validate_cross_odds(cross_odds_refresh, max_seconds=5.0)
    selected_side = str(selection["option"].get("ot") or "").lower()
    if selected_side != cross_check["direction"].lower():
        raise RuntimeError("候选方向与最新A/B复核方向不一致；禁止发送投注")
    fresh_key = "eb_" + selected_side
    fresh_decimal_odds = as_decimal(cross_check["snapshot"].get(fresh_key))
    if fresh_decimal_odds is None or fresh_decimal_odds <= 1:
        raise RuntimeError("5秒复核未取得有效的 EB 最新赔率；已停止")
    odds_value = int((fresh_decimal_odds * Decimal(100000)).quantize(Decimal("1")))
    if odds_value <= 100000:
        raise RuntimeError("5秒复核后的 EB 最新赔率无效；已停止")

    # Query the limit using the odds from the same fresh cross-book snapshot.
    _set_execution_stage("请求 EB 投注限额数据")
    limit_request = build_limit_request(selection, odds_value)
    limit_response = post_json(base_url, LIMIT_PATH, limit_request, headers, timeout, browser_session)
    limit_rows = require_success(limit_response, LIMIT_PATH)
    if not isinstance(limit_rows, list) or not limit_rows or not limit_rows[0].get("minBet"):
        if browser_session is None:
            raise RuntimeError("EB 投注限额接口未返回数据，当前没有可等待的浏览器会话")
        browser_session.confirm_and_wait_eb_retry(LIMIT_PATH)
        _set_execution_stage("人工确认后重新请求 EB 投注限额数据")
        limit_response = post_json(base_url, LIMIT_PATH, limit_request, headers, timeout, browser_session)
        limit_rows = require_success(limit_response, LIMIT_PATH)
    if not isinstance(limit_rows, list) or not limit_rows or not limit_rows[0].get("minBet"):
        raise RuntimeError("人工确认后 EB 投注限额接口仍未返回有效数据；停止本次投注")
    min_stake = as_decimal(limit_rows[0]["minBet"])
    if min_stake is None or amount < min_stake:
        raise RuntimeError("stake is below the confirmed minimum; stopped before bet")

    payload = build_bet_payload(selection, amount, odds_value=odds_value, latest_market=latest)
    key = _candidate_key(selection)
    if ORDER_AUDIT_PATH.exists():
        try:
            prior = json.loads(ORDER_AUDIT_PATH.read_text(encoding="utf-8")).get("records", [])
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("bet order audit file is unreadable; refusing to submit")
        latest_state_by_key = {}
        for record in prior:
            if isinstance(record, dict) and record.get("candidate_key"):
                latest_state_by_key[str(record["candidate_key"])] = record.get("state")
        if latest_state_by_key.get(key) in {"PREPARED", "ACCEPTED", "UNKNOWN", "RECHECK_READY"}:
            raise RuntimeError("duplicate candidate already submitted or has uncertain outcome; refusing duplicate")
    _write_order_audit({
        "candidate_key": key, "state": "PREPARED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": str(selection["event"].get("mid")),
        "market_id": str(selection["group"].get("hid")),
        "option_id": str(selection["option"].get("oid")),
        "direction": selected_side, "stake_yuan": str(amount),
        "cross_check": {k: v for k, v in cross_check.items() if k != "snapshot"},
    })
    event_id = str(selection["event"].get("mid"))

    def send_once(request_payload, attempt_label):
        try:
            response = post_json(base_url, BET_PATH, request_payload, headers, timeout, browser_session)
        except Exception as exc:
            _write_order_audit({
                "candidate_key": key, "event_id": event_id, "state": "UNKNOWN",
                "attempt": attempt_label,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__, "error": str(exc),
            })
            raise RuntimeError(
                "bet POST outcome is uncertain; no automatic retry or re-comparison was made"
            ) from None
        if not isinstance(response, dict) or str(response.get("code")) != "0000000":
            return response, None, False
        try:
            return response, parse_bet_result(response), True
        except Exception as exc:
            _write_order_audit({
                "candidate_key": key, "event_id": event_id, "state": "UNKNOWN",
                "attempt": attempt_label,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__, "error": str(exc),
            })
            raise RuntimeError(
                "bet API returned success but order acceptance could not be verified; "
                "no automatic retry was made"
            ) from None

    # First submission; retry the exact same payload once only after an explicit API rejection.
    _set_execution_stage("提交 EB 模拟投注请求")
    bet_response, parsed_result, accepted = send_once(payload, "initial")
    if not accepted:
        _write_order_audit({
            "candidate_key": key, "event_id": event_id, "state": "REJECTED",
            "attempt": "initial",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "api_code": bet_response.get("code") if isinstance(bet_response, dict) else "INVALID_RESPONSE",
            "api_message": str(bet_response.get("msg") or "")[:300] if isinstance(bet_response, dict) else "",
        })
        bet_response, parsed_result, accepted = send_once(payload, "retry_same_payload")

    if not accepted:
        _write_order_audit({
            "candidate_key": key, "event_id": event_id, "state": "REJECTED",
            "attempt": "retry_same_payload",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "api_code": bet_response.get("code") if isinstance(bet_response, dict) else "INVALID_RESPONSE",
            "api_message": str(bet_response.get("msg") or "")[:300] if isinstance(bet_response, dict) else "",
        })
        # The same-payload retry failed: compare this exact event/market once more.
        try:
            refreshed_cross = refresh_and_validate_cross_odds(cross_odds_refresh, max_seconds=5.0)
            if str(refreshed_cross.get("direction") or "").lower() != selected_side:
                raise RuntimeError("重新比较后的方向已变化")
            refreshed_eb_odds = as_decimal(refreshed_cross.get("snapshot", {}).get("eb_" + selected_side))
            if refreshed_eb_odds is None or refreshed_eb_odds <= 1:
                raise RuntimeError("重新比较未取得有效 EB 赔率")
            refreshed_odds_value = int(
                (refreshed_eb_odds * Decimal(100000)).quantize(Decimal("1"))
            )
            refreshed_latest_response = post_json(
                base_url, LATEST_PATH, build_latest_market_request(selection),
                headers, timeout, browser_session
            )
            refreshed_latest_rows = require_success(refreshed_latest_response, LATEST_PATH)
            if not isinstance(refreshed_latest_rows, list):
                raise RuntimeError("重新比较后最新盘口数据格式无效")
            refreshed_latest = next((
                row for row in refreshed_latest_rows
                if str(row.get("id")) == str(selection["group"]["hid"])
            ), None)
            if refreshed_latest is None:
                raise RuntimeError("重新比较后原盘口已不存在")
            if str(refreshed_latest.get("matchInfoId") or "") != event_id:
                raise RuntimeError("重新比较后赛事 ID 不匹配")
            if str(refreshed_latest.get("playId") or refreshed_latest.get("chpid") or "") != str(selection["market"].get("hpid")):
                raise RuntimeError("重新比较后盘口类型不匹配")
            refreshed_options = refreshed_latest.get("marketOddsList") or []
            refreshed_option = next((
                row for row in refreshed_options
                if str(row.get("id")) == str(selection["option"]["oid"])
                and str(row.get("oddsType")) == selected_side
            ), None)
            if refreshed_option is None or refreshed_option.get("oddsStatus") not in (1, "1"):
                raise RuntimeError("重新比较后原投注选项不可用")
            if refreshed_odds_value <= 100000:
                raise RuntimeError("重新比较后的赔率无效")
            refreshed_limit_response = post_json(
                base_url, LIMIT_PATH,
                build_limit_request(selection, refreshed_odds_value),
                headers, timeout, browser_session
            )
            refreshed_limit_rows = require_success(refreshed_limit_response, LIMIT_PATH)
            if not isinstance(refreshed_limit_rows, list) or not refreshed_limit_rows or not refreshed_limit_rows[0].get("minBet"):
                raise RuntimeError("重新比较后的投注限额数据不完整")
            refreshed_min_stake = as_decimal(refreshed_limit_rows[0]["minBet"])
            if refreshed_min_stake is None or amount < refreshed_min_stake:
                raise RuntimeError("重新比较后的最低投注额超过本次投注金额")
            refreshed_payload = build_bet_payload(
                selection, amount, odds_value=refreshed_odds_value,
                latest_market=refreshed_latest
            )
        except Exception as exc:
            _write_order_audit({
                "candidate_key": key, "event_id": event_id, "state": "ABANDONED",
                "attempt": "one_time_recomparison",
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "reason": str(exc)[:400],
            })
            raise RuntimeError(
                "同盘口重试仍失败；已完成唯一一次重新比较，但条件不再满足或盘口数据无效，本次投注放弃"
            ) from None

        _write_order_audit({
            "candidate_key": key, "event_id": event_id, "state": "RECHECK_READY",
            "attempt": "one_time_recomparison",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "latest_odds": format(Decimal(refreshed_odds_value) / Decimal(100000), "f"),
        })
        bet_response, parsed_result, accepted = send_once(
            refreshed_payload, "after_one_time_recomparison"
        )
        if not accepted:
            _write_order_audit({
                "candidate_key": key, "event_id": event_id, "state": "ABANDONED",
                "attempt": "after_one_time_recomparison",
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "api_code": bet_response.get("code") if isinstance(bet_response, dict) else "INVALID_RESPONSE",
                "api_message": str(bet_response.get("msg") or "")[:300] if isinstance(bet_response, dict) else "",
            })
            raise RuntimeError("重新比较后再次投注仍被接口拒绝；本次匹配已放弃，不再重试")

        odds_value = refreshed_odds_value
        min_stake = refreshed_min_stake
        cross_check = refreshed_cross
        latest = refreshed_latest

    _write_order_audit({
        "candidate_key": key, "event_id": event_id,
        "state": "ACCEPTED",
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "orders": parsed_result["orders"],
        "bet_money_total_fen": parsed_result["bet_money_total_fen"],
        "bet_money_total_yuan": parsed_result["bet_money_total_yuan"],
    })
    return {
        "code": bet_response.get("code"), "message": bet_response.get("msg"),
        "accepted_order_count": len(parsed_result["orders"]),
        "orders": parsed_result["orders"],
        "bet_money_total_yuan": parsed_result["bet_money_total_yuan"],
        "latest_odds": format(Decimal(int(odds_value)) / Decimal(100000), "f"),
        "minimum_stake": str(min_stake), "audit_file": str(ORDER_AUDIT_PATH),
    }


def redact(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in {"cuid", "deviceimei", "cookie", "authorization"} else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


def _selection_with_runtime(candidate):
    runtime = candidate["runtime_context"]
    return {
        "event": candidate["event"],
        "market": candidate["market"],
        "group": candidate["group"],
        "option": candidate["option"],
        "match_type": int(runtime["match_type"]),
        "cuid": str(runtime["cuid"]),
        "device_imei": str(runtime["device_imei"]),
        "market_type_finally": str(runtime["market_type_finally"]),
    }


def _write_selection_file(selection, output_path):
    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    temp_path.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(output_path)
    return output_path


def auto_run_once(browser_session, stake, timeout, output_path, candidates=None):
    """Run one sequential round over the current day's NBA full-game totals."""
    successful_counts = _successful_bet_counts()
    ignored_event_ids = {
        event_id for event_id, count in successful_counts.items()
        if count >= MAX_SUCCESSFUL_BETS_PER_EVENT
    }
    browser_session.set_ignored_event_ids(ignored_event_ids)
    if candidates is None:
        print("[执行节点] 正在重新读取当日 NBA 赛事列表及全场大小盘口。", flush=True)
        candidates = browser_session.discover_nba_total_selections()
    if not candidates:
        print("[执行节点] 当前轮没有可检测的 NBA 全场大小盘口。", flush=True)
        return []
    print("[执行节点]", f"本轮发现 {len(candidates)} 个盘口选项，开始按赛事逐个校验。", flush=True)

    events = {}
    for candidate in candidates:
        event_id = str(candidate["event"].get("mid"))
        if event_id not in ignored_event_ids:
            events.setdefault(event_id, []).append(candidate)

    round_results = []
    for event_id, event_candidates in events.items():
        _set_execution_stage("处理赛事 " + event_id + "：整理全场大小盘口")
        if _successful_bet_counts().get(event_id, 0) >= MAX_SUCCESSFUL_BETS_PER_EVENT:
            continue
        groups = {}
        for candidate in event_candidates:
            group_key = (
                str(candidate["market"].get("hpid")),
                str(candidate["group"].get("hid")),
                str(candidate["group"].get("hv")),
            )
            groups.setdefault(group_key, candidate)

        for group_index, (group_key, probe) in enumerate(groups.items(), 1):
            if _successful_bet_counts().get(event_id, 0) >= MAX_SUCCESSFUL_BETS_PER_EVENT:
                browser_session.set_ignored_event_ids(set(ignored_event_ids) | {event_id})
                break
            line = group_key[2]
            event = probe["event"]
            matchup = f"{event.get('mhn')} vs {event.get('man')}"
            print(
                "[执行节点]",
                f"当前赛事盘口 {group_index}/{len(groups)}：{matchup}，全场大小={line}；准备进行首次赔率校验。",
                flush=True,
            )
            _set_execution_stage("赔率首次校验：" + matchup + " 全场大小=" + str(line))
            print("[盘口校验中]", f"赛事={matchup}", f"event_id={event_id}", f"全场大小={line}", flush=True)
            try:
                first_cross = refresh_and_validate_cross_odds(
                    lambda p=probe: browser_session.refresh_cross_odds(p), max_seconds=5.0
                )
                first_side = first_cross["direction"].lower()
                first_candidate = next((
                    item for item in event_candidates
                    if str(item["market"].get("hpid")) == group_key[0]
                    and str(item["group"].get("hid")) == group_key[1]
                    and str(item["option"].get("ot") or "").lower() == first_side
                ), None)
                if first_candidate is None:
                    continue
                print("[执行节点]", "首次规则校验通过，等待 5 秒后进行第二次赔率校验。", flush=True)
                _set_execution_stage("等待 5 秒后进行二次赔率校验：" + matchup)
                time.sleep(5)
                _set_execution_stage("赔率二次校验：" + matchup + " 全场大小=" + str(line))
                second_cross = refresh_and_validate_cross_odds(
                    lambda p=first_candidate: browser_session.refresh_cross_odds(p), max_seconds=5.0
                )
                second_side = second_cross["direction"].lower()
                candidate = next((
                    item for item in event_candidates
                    if str(item["market"].get("hpid")) == group_key[0]
                    and str(item["group"].get("hid")) == group_key[1]
                    and str(item["option"].get("ot") or "").lower() == second_side
                ), None)
                if candidate is None:
                    continue
                for success_log_index in range(1, 6):
                    print("[盘口匹配成功]", f"第{success_log_index}/5次", f"赛事={matchup}", f"全场大小={line}", f"方向={second_side}", flush=True)
                print("[执行节点]", "二次校验通过，正在读取最新投注限额并组装投注参数。", flush=True)
                selection = _selection_with_runtime(candidate)
                saved_path = _write_selection_file(selection, output_path)
                _set_execution_stage("提交模拟投注：" + matchup + " 全场大小=" + str(line))
                print("[执行节点]", "投注参数已保存，正在提交本次模拟投注。", flush=True)
                result = submit_test_bet(
                    selection, stake, timeout,
                    cross_odds_refresh=lambda s=selection: browser_session.refresh_cross_odds(s),
                    browser_session=browser_session,
                )
                result["input_file"] = str(saved_path)
                result["auto_run"] = True
                result["event_id"] = event_id
                round_results.append(result)
                print("[投注成功]", f"赛事={matchup}", f"全场大小={line}", f"订单数={result.get('accepted_order_count')}", flush=True)
            except Exception as exc:
                message = str(exc)
                is_bet_failure = (
                    "outcome is uncertain" in message or "duplicate candidate" in message
                    or "bet" in message.lower() or "投注" in message
                )
                print(
                    "[投注失败]" if is_bet_failure else "[错误]",
                    f"赛事={matchup}", f"全场大小={line}",
                    f"原因={type(exc).__name__}: {message[:300]}",
                    flush=True,
                )
                if "outcome is uncertain" in message or "duplicate candidate" in message:
                    raise
                continue
    return round_results


def main():
    parser = argparse.ArgumentParser(description="Build or submit one local-test OP7/EB wager")
    parser.add_argument("--input", help="optional legacy JSON; omitted when using --discover-nba")
    parser.add_argument("--discover-nba", action="store_true", help="read live EB NBA events/total markets; read-only, no order submission")
    parser.add_argument("--stake", default="100", help="fixed simulated test stake in yuan (default: 100)")
    parser.add_argument("--submit-test", action="store_true", help="send only after local-test guards pass")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--profiles-dir", default="browser_profiles")
    parser.add_argument("--output", help="write one discovered live selection JSON to this path")
    parser.add_argument("--selection-index", type=int, default=0, help="zero-based candidate index to write (default: 0)")
    parser.add_argument("--auto-run", action="store_true", help="discover, compare live odds, build input, and submit at most one qualifying order")
    args = parser.parse_args()
    if args.auto_run:
        if args.input or args.discover_nba or not args.submit_test:
            parser.error("--auto-run cannot be combined with --input/--discover-nba and requires --submit-test")
        runtime = load_runtime_endpoints()
        eb_config = runtime["apis"]["eb"]
        validate_test_target(os.environ.get(
            "OP7_TEST_BET_BASE_URL",
            eb_config.get("bet_api_base_url") or eb_config["base_url"],
        ))
        from op7_browser_session_bridge import BrowserSessionBridge
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(target=_heartbeat, args=(heartbeat_stop,), daemon=True)
        heartbeat_thread.start()
        with BrowserSessionBridge(profiles_dir=args.profiles_dir, timeout=args.timeout) as browser_session:
            first_round = True
            while True:
                try:
                    if first_round:
                        _set_execution_stage("首次初始化：打开 OP7 与 Pingbo 浏览器窗口")
                        print("[执行节点] 首次启动：请等待 OP7 与 Pingbo 浏览器窗口打开。", flush=True)
                        from op7_fb_eb_pingbo_live_refresh import USER_INPUT_WAITING
                        USER_INPUT_WAITING.set()
                        try:
                            input(
                                "【页面操作 1】请在 OP7 浏览器进入「篮球 / NBA 正规赛事」列表页面；"
                                "确认列表已经显示后回到终端按 Enter。随后脚本会单独提示你刷新页面："
                            )
                        finally:
                            USER_INPUT_WAITING.clear()
                        browser_session._nba_capture_confirmed = True
                        first_round = False
                        _set_execution_stage("已进入 NBA 列表页，等待刷新并捕获列表请求")
                    _set_execution_stage("捕获 NBA 列表刷新请求并读取当日完整赛事")
                    print("[执行节点]", "已确认 NBA 列表页，准备捕获本轮刷新请求。", flush=True)
                    # Suppress diagnostics only; bridge prompts/stages use sys.__stdout__.
                    with contextlib.redirect_stdout(io.StringIO()):
                        candidates = browser_session.discover_nba_total_selections()
                    _set_execution_stage("NBA 列表读取完成，准备处理 " + str(len(candidates)) + " 个盘口")
                    print("[执行节点]", f"本轮盘口发现完成，候选盘口数={len(candidates)}。", flush=True)
                    if candidates:
                        auto_run_once(
                            browser_session, args.stake, args.timeout,
                            args.output or "./selections.json",
                            candidates=candidates,
                        )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    print("[错误]", f"{type(exc).__name__}: {str(exc)[:400]}", flush=True)
                # Wait only after the entire currently discovered NBA slate is processed.
                _set_execution_stage("本轮结束，等待 120 秒后重新获取赛事列表")
                print("[执行节点]", "本轮所有 NBA 赛事处理完毕，暂停 120 秒；随后重新获取当日赛事列表。", flush=True)
                time.sleep(120)
        return
    if args.discover_nba:
        if args.input or args.submit_test:
            parser.error("--discover-nba only generates input data; run submission separately with --input and --submit-test")
        from op7_browser_session_bridge import BrowserSessionBridge
        with BrowserSessionBridge(profiles_dir=args.profiles_dir, timeout=args.timeout) as browser_session:
            selections = browser_session.discover_nba_total_selections()
            summary = [{
                "index": index,
                "event_id": str(s["event"]["mid"]),
                "league": s["event"].get("tn"),
                "home": s["event"].get("mhn"),
                "away": s["event"].get("man"),
                "start_time": s["event"].get("mgt"),
                "line": s["group"].get("hv"),
                "side": s["option"].get("ot"),
                "odds_raw": s["option"].get("ov"),
                "market_id": str(s["group"].get("hid")),
                "option_id": str(s["option"].get("oid")),
            } for index, s in enumerate(selections)]
            if args.output:
                if args.selection_index < 0 or args.selection_index >= len(selections):
                    raise ValueError(
                        f"--selection-index {args.selection_index} out of range; "
                        f"valid range is 0..{len(selections) - 1}"
                    )
                selected = selections[args.selection_index]
                runtime = selected["runtime_context"]
                input_data = {
                    "event": selected["event"],
                    "market": selected["market"],
                    "group": selected["group"],
                    "option": selected["option"],
                    "match_type": int(runtime["match_type"]),
                    "cuid": str(runtime["cuid"]),
                    "device_imei": str(runtime["device_imei"]),
                    "market_type_finally": str(runtime["market_type_finally"]),
                }
                output_path = Path(args.output).expanduser()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = output_path.with_name(output_path.name + ".tmp")
                temp_path.write_text(
                    json.dumps(input_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temp_path.replace(output_path)
                print(f"[INPUT_GENERATED] path={output_path} selection_index={args.selection_index}")
        print(json.dumps({
            "mode": "LIVE_NBA_DISCOVERY",
            "count": len(summary),
            "selections": summary,
        }, ensure_ascii=False, indent=2))
        return
    if not args.input:
        parser.error("请提供 --input，或使用 --discover-nba 执行实时 NBA 只读发现")
    selection = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.submit_test:
        from op7_browser_session_bridge import BrowserSessionBridge
        with BrowserSessionBridge(profiles_dir=args.profiles_dir, timeout=args.timeout) as browser_session:
            result = submit_test_bet(
                selection, args.stake, args.timeout,
                cross_odds_refresh=lambda: browser_session.refresh_cross_odds(selection),
                browser_session=browser_session,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(redact(build_bet_payload(selection, args.stake)), ensure_ascii=False, indent=2))
        print("\nDRY RUN ONLY: no network request was sent.")


if __name__ == "__main__":
    main()
