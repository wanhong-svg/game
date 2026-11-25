#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge the logged-in Playwright browser sessions to the OP7 test workflow.

Credentials remain in the browser profile/context and process memory. This module
never writes cookies, authorization headers, request bodies, or device identifiers
to the endpoint configuration file.
"""
import json
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import op7_fb_eb_pingbo_live_refresh as live
import op7_fb_eb_venue_mapper as mapper


def _side(value):
    s = str(value or "").strip().lower()
    if s in {"over", "o", "大"} or "over" in s:
        return "over"
    if s in {"under", "u", "小"} or "under" in s:
        return "under"
    return None


def _find_eb_totals(raw, event_id, line):
    data = live.data_of(raw)
    rows, _ = mapper.flatten_eb_markets(data, 4, {})
    matches = []
    for market in rows:
        if str(market.get("event_id")) != str(event_id) or str(market.get("play_id_hpid")) != "38":
            continue
        try:
            if Decimal(str(market.get("line_hv"))) != Decimal(str(line)):
                continue
        except Exception:
            continue
        for option in market.get("selections", []):
            side = _side(option.get("selection_code")) or _side(option.get("selection"))
            odds = option.get("decimal_odds")
            if side and odds:
                matches.append((side, odds))
    result = {}
    for side, odds in matches:
        if side in result and result[side] != odds:
            raise RuntimeError("EB full-game total mapping is ambiguous")
        result[side] = odds
    if set(result) != {"over", "under"}:
        raise RuntimeError("EB full-game totals did not return both Over and Under")
    return result


def _find_pingbo_event(payload, event):
    target_home = mapper.norm_team(event.get("home") or event.get("mhn"))
    target_away = mapper.norm_team(event.get("away") or event.get("man"))
    target_time = mapper.timestamp_ms(event.get("mgt") or event.get("start_time_ms"))
    candidates = []
    for item in mapper.extract_pingbo_events(payload):
        home, away = mapper.norm_team(item.get("home")), mapper.norm_team(item.get("away"))
        stamp = mapper.timestamp_ms(item.get("start_time_ms"))
        same = home == target_home and away == target_away
        reverse = home == target_away and away == target_home
        if not (same or reverse):
            continue
        if target_time is not None and stamp is not None and abs(target_time - stamp) > 5 * 60 * 1000:
            continue
        candidates.append(item)
    if len(candidates) != 1:
        raise RuntimeError(f"Pingbo event mapping is ambiguous: {len(candidates)} candidates")
    return candidates[0]


def _find_pingbo_totals(event, target_line):
    rows = mapper.flatten_pingbo_markets(event)
    by_side = {}
    for row in rows:
        if str(row.get("market_key")) != "1":
            continue
        side = _side(row.get("side"))
        if not side:
            continue
        line = row.get("line_numeric_source_index_1")
        if line in (None, ""):
            line = row.get("line")
        try:
            if Decimal(str(line)) != Decimal(str(target_line)):
                continue
        except Exception:
            continue
        if side in by_side and by_side[side] != row.get("decimal_odds"):
            raise RuntimeError("Pingbo full-game total mapping is ambiguous")
        by_side[side] = row.get("decimal_odds")
    if set(by_side) != {"over", "under"}:
        raise RuntimeError("Pingbo full-game totals did not return both Over and Under on the target line")
    return by_side


class BrowserSessionBridge:
    """Own both persistent browser contexts for one CLI execution."""

    def __init__(self, profiles_dir="browser_profiles", timeout=15):
        self.profiles_dir = Path(profiles_dir)
        self.timeout = int(timeout)
        self.playwright = None
        self.op7 = None
        self.pingbo = None

    @staticmethod
    def _prompt_site_url(label):
        while True:
            value = input(f"请输入 {label} 访问地址（http:// 或 https://）：").strip()
            parsed = urlsplit(value)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                return value
            print("地址无效：请输入完整的 http(s) 地址，且不要在 URL 中填写账号或密码。")

    def __enter__(self):
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        (self.profiles_dir / "op7").mkdir(parents=True, exist_ok=True)
        (self.profiles_dir / "pingbo").mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.op7 = self.playwright.chromium.launch_persistent_context(
            str(self.profiles_dir / "op7"), headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        self.pingbo = self.playwright.chromium.launch_persistent_context(
            str(self.profiles_dir / "pingbo"), headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        for ctx, label, family in (
            (self.op7, "OP7", "op7"),
            (self.pingbo, "Pingbo", "pingbo"),
        ):
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("request", lambda req, f=family: live.capture_request(req, f))
            url = self._prompt_site_url(label)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except PlaywrightTimeoutError:
                pass
        # Allow the manually selected authenticated pages to emit their normal API calls.
        time.sleep(1.0)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.op7:
                self.op7.close()
            if self.pingbo:
                self.pingbo.close()
        finally:
            if self.playwright:
                self.playwright.stop()


    def discover_nba_events(self, max_pages=20):
        """Fetch current EB event pages and return only explicitly identified NBA events."""
        path = "/yewu11/v1/m/matchesPagePB"
        template = live.CAPTURED_REQUESTS.get(("eb", path))
        if template is None:
            raise RuntimeError("未捕获 EB 赛事列表请求；请在 OP7 页面进入篮球/NBA赛事列表后重试")
        try:
            body = json.loads(template.get("post_data") or "{}")
        except Exception:
            raise RuntimeError("无法解析浏览器捕获的 EB 赛事列表请求参数") from None
        if not isinstance(body, dict):
            raise RuntimeError("EB 赛事列表请求体不是 JSON 对象")
        body.setdefault("cpn", 1)
        body.setdefault("cps", 50)
        try:
            total_pages = 1
            page_size = max(1, min(int(body.get("cps", 50)), 100))
        except (TypeError, ValueError):
            page_size = 50
            body["cps"] = page_size
        origin = live.CAPTURED_ORIGINS.get("eb", live.SITES["EB_ORIGIN"])
        rows_by_id = {}
        for page_no in range(1, max(1, int(max_pages)) + 1):
            body["cpn"] = page_no
            response = live.request_json(
                self.op7, origin + path, "POST",
                live.headers_env("OP7_READONLY_HEADERS_JSON"),
                body=body, timeout_ms=self.timeout * 1000, family="eb")
            data = live.data_of(response)
            if isinstance(data, dict):
                try:
                    total_pages = max(1, min(int(data.get("pages") or 1), int(max_pages)))
                except (TypeError, ValueError):
                    total_pages = 1
                events = data.get("data") or []
            elif isinstance(data, list):
                events = data
                total_pages = 1
            else:
                raise RuntimeError("EB 赛事列表响应结构无法识别；拒绝猜测赛事字段")
            for event in events:
                if not isinstance(event, dict) or str(event.get("csid")) != "2":
                    continue
                league_names = {
                    str(event.get(k) or "").strip().upper()
                    for k in ("tn", "tnjc") if str(event.get(k) or "").strip()
                }
                if not league_names or league_names != {"NBA"}:
                    continue
                if not event.get("mid") or not isinstance(event.get("hps"), list):
                    continue
                rows_by_id[str(event["mid"])] = event
            if page_no >= total_pages:
                break
        return list(rows_by_id.values())

    def discover_nba_total_selections(self):
        """Build current full-game totals selections from live NBA event trees only."""
        events = self.discover_nba_events()
        selections = []
        for event in events:
            for market in event.get("hps") or []:
                if not isinstance(market, dict) or str(market.get("hpid")) != "38":
                    continue
                for group in market.get("hl") or []:
                    if not isinstance(group, dict) or group.get("hv") in (None, ""):
                        continue
                    for option in group.get("ol") or []:
                        if not isinstance(option, dict) or str(option.get("ot") or "").strip().lower() not in {"under", "over"}:
                            continue
                        required = (event.get("mid"), event.get("tid"), event.get("csid"),
                                    event.get("csna"), market.get("hpn"), group.get("hid"),
                                    option.get("oid"), option.get("ov"))
                        if any(v in (None, "") for v in required):
                            continue
                        selections.append({"event": event, "market": market, "group": group, "option": option})
        if not selections:
            raise RuntimeError("实时赛事列表中没有找到可确认 ID 的 NBA 正规赛事全场大小分选项")
        return selections

    def post_json(self, base_url, path, body, headers, timeout=15):
        url = base_url.rstrip("/") + path
        captured = live.CAPTURED_REQUESTS.get(("eb", path))
        if captured is None:
            # Reuse only in-memory dynamic EB headers from the authenticated browser session.
            captured = next(
                (value for (family, _path), value in reversed(list(live.CAPTURED_REQUESTS.items()))
                 if family == "eb"),
                None,
            )
        req_headers = dict(captured.get("headers", {})) if captured else {}
        req_headers.update(headers or {})
        req_headers.setdefault("Accept", "application/json")
        req_headers.setdefault("Content-Type", "application/json")
        response = self.op7.request.post(
            url, data=json.dumps(body, ensure_ascii=False),
            headers=req_headers, timeout=int(timeout * 1000))
        if response.status in (401, 403):
            raise RuntimeError(f"EB browser session rejected {path}: HTTP {response.status}; please log in again")
        if not response.ok:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        try:
            return response.json()
        except Exception:
            raise RuntimeError(f"{path} returned non-JSON response") from None

    def refresh_cross_odds(self, selection):
        """Fetch both books in one synchronous window using the browser's logged-in sessions."""
        started = time.monotonic()
        event = selection["event"]
        line = selection["group"].get("hv")
        if line in (None, ""):
            raise RuntimeError("selection has no current EB total line")
        eb_origin = live.CAPTURED_ORIGINS.get("eb", live.SITES["EB_ORIGIN"])
        pb_origin = live.CAPTURED_ORIGINS.get("pingbo", live.SITES["PINGBO_ORIGIN"])
        eb_path = "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB"
        pb_path = "/sports-service/sv/compact/events"
        cfg_path = Path(__file__).with_name("refresh_config.json")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        eb_params = dict((cfg.get("op7") or {}).get("odds_params") or {})
        eb_params["mid"] = str(event["mid"])
        pb_params = dict((cfg.get("part567") or {}).get("events_params") or {})
        eb = live.request_json(
            self.op7, eb_origin + eb_path, "GET",
            live.headers_env("OP7_READONLY_HEADERS_JSON"),
            params=eb_params, timeout_ms=self.timeout * 1000, family="eb")
        pb = live.request_json(
            self.pingbo, pb_origin + pb_path, "GET",
            live.headers_env("PINGBO_READONLY_HEADERS_JSON"),
            params=pb_params, timeout_ms=self.timeout * 1000, family="pingbo")
        eb_totals = _find_eb_totals(eb, event["mid"], line)
        pb_event = _find_pingbo_event(pb, event)
        pb_totals = _find_pingbo_totals(pb_event, line)
        elapsed = time.monotonic() - started
        if elapsed > 5.0:
            raise RuntimeError(f"two-book refresh took {elapsed:.3f}s (> 5.0s)")
        return {
            "eb_under": eb_totals["under"], "eb_over": eb_totals["over"],
            "pingbo_over": pb_totals["over"], "pingbo_under": pb_totals["under"],
            "eb_under_line": str(line), "pingbo_over_line": str(line),
            "refresh_elapsed_seconds": round(elapsed, 3),
            "eb_event_id": str(event["mid"]), "pingbo_event_id": str(pb_event["event_id"]),
        }
