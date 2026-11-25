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
from urllib.parse import urlsplit, parse_qsl, unquote

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


def _decode_api_response(raw):
    """Use the shared response normalizer for raw JSON or Base64 + gzip/zlib envelopes."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        raise RuntimeError("EB 接口响应不是有效 JSON") from None
    decoded = live.unwrap(envelope)
    if isinstance(decoded, dict) and isinstance(decoded.get("data"), str):
        value = decoded["data"].strip()
        if value.startswith(("H4sI", "eJ", "eN")):
            raise RuntimeError("EB 接口 data 字段疑似 Base64 压缩数据，但自动解码未成功")
    return decoded

def _walk_field_values(value, aliases, found=None, path="$"):
    found = found if found is not None else {name: [] for name in aliases}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("_", "").lower()
            for canonical, variants in aliases.items():
                if normalized in variants and child not in (None, "") and not isinstance(child, (dict, list)):
                    found[canonical].append((str(child), f"{path}.{key}"))
            _walk_field_values(child, aliases, found, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_field_values(child, aliases, found, f"{path}[{index}]")
    return found


def _resolve_runtime_fields(sources, event_id, market=None, group=None):
    aliases = {
        "cuid": {"cuid"},
        "device_imei": {"deviceimei", "imei"},
        "match_type": {"matchtype"},
        "market_type_finally": {"markettypefinally"},
    }
    resolved = {}
    for field in aliases:
        values = []
        for source_name, payload in sources:
            scan_payload = payload
            if field == "market_type_finally" and market is not None:
                scoped = []
                market_hpid = str(market.get("hpid") or "")
                group_hid = str((group or {}).get("hid") or "")
                def locate(node):
                    if isinstance(node, dict):
                        same_market = market_hpid and str(node.get("hpid") or "") == market_hpid
                        same_group = group_hid and str(node.get("hid") or "") == group_hid
                        if same_market or same_group:
                            scoped.append(node)
                            return
                        for child in node.values():
                            locate(child)
                    elif isinstance(node, list):
                        for child in node:
                            locate(child)
                locate(payload)
                if scoped:
                    scan_payload = scoped
            found = _walk_field_values(scan_payload, aliases)
            values.extend((value, f"{source_name}:{where}") for value, where in found[field])
        distinct = {value for value, _ in values}
        if not distinct:
            raise RuntimeError(f"赛事 {event_id} 详情上下文缺少字段 {field}；拒绝使用默认值")
        if len(distinct) != 1:
            origins = ", ".join(where for _, where in values)
            raise RuntimeError(f"赛事 {event_id} 字段 {field} 存在冲突来源：{origins}")
        resolved[field] = next(iter(distinct))
        resolved.setdefault("_sources", {})[field] = [where for _, where in values]
    resolved["_event_id"] = str(event_id)
    if market is not None:
        resolved["_market_hpid"] = str(market.get("hpid") or "")
    if group is not None:
        resolved["_market_id"] = str(group.get("hid") or "")
    return resolved


class BrowserSessionBridge:
    """Own both persistent browser contexts for one CLI execution."""

    def __init__(self, profiles_dir="browser_profiles", timeout=15):
        self.profiles_dir = Path(profiles_dir)
        self.timeout = int(timeout)
        self.playwright = None
        self.op7 = None
        self.pingbo = None
        self._detail_responses = {}
        self._list_responses = {}
        self._request_templates_by_id = {}
        self._nba_capture_started = 0.0
        self._attached_pages = {"op7": set(), "pingbo": set()}

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
        # Launch both authenticated browser contexts in mobile/H5 emulation.
        # DevTools opens automatically; Playwright's mobile emulation (viewport,
        # mobile UA, touch and device scale factor) does not depend on manually
        # toggling Chrome's device toolbar.
        h5_options = {
            "headless": False,
            "viewport": {"width": 390, "height": 844},
            "screen": {"width": 390, "height": 844},
            "device_scale_factor": 3,
            "is_mobile": True,
            "has_touch": True,
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--auto-open-devtools-for-tabs",
            ],
        }
        self.op7 = self.playwright.chromium.launch_persistent_context(
            str(self.profiles_dir / "op7"), **h5_options)
        self.pingbo = self.playwright.chromium.launch_persistent_context(
            str(self.profiles_dir / "pingbo"), **h5_options)
        for ctx, label, family in (
            (self.op7, "OP7", "op7"),
            (self.pingbo, "Pingbo", "pingbo"),
        ):
            # Listen at context level so tabs/popups opened after login are covered too.
            ctx.on("page", lambda page, f=family: self._attach_page(page, f))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._attach_page(page, family)
            url = self._prompt_site_url(label)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except PlaywrightTimeoutError:
                pass
        # Allow the manually selected authenticated pages to emit their normal API calls.
        time.sleep(1.0)
        return self

    def _attach_page(self, page, family):
        """Attach request/response observers once to every page in a browser context."""
        attached = self._attached_pages.setdefault(family, set())
        if page in attached:
            return
        attached.add(page)
        if family == "op7":
            page.on("request", self._capture_op7_request)
            page.on("response", self._capture_op7_response)
        else:
            page.on("request", lambda req, f=family: live.capture_request(req, f))

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                print("\\n检测过程中发生异常。浏览器暂时保持打开，便于检查页面；回到终端按 Enter 后关闭浏览器。")
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
            if self.op7:
                self.op7.close()
            if self.pingbo:
                self.pingbo.close()
        finally:
            if self.playwright:
                self.playwright.stop()


    def _capture_op7_request(self, request):
        """Capture allow-listed requests and bind their exact template to the request object."""
        try:
            parsed = urlsplit(request.url)
            live.capture_request(request, "op7")
            if parsed.path in {
                "/yewu11/v1/m/matchesPagePB",
                "/yewu11/v1/m/noLiveMatchesPagePB",
            }:
                template = live.CAPTURED_REQUESTS.get(("eb", parsed.path))
                if template:
                    self._request_templates_by_id[id(request)] = dict(template)
        except Exception as exc:
            print("[EB_CAPTURE_DEBUG] request_capture_failed", f"error={type(exc).__name__}", flush=True)

    def _capture_op7_response(self, response):
        """Decode EB API responses as they arrive; retain only detail payloads in memory."""
        try:
            parsed = urlsplit(response.url)
            if not parsed.path.startswith(("/yewu11/",)):
                return
            if not (parsed.path in live.EB_NBA_PAGE_ENDPOINTS
                    or parsed.path.endswith("/getMatchDetailPB")):
                return

            payload = _decode_api_response(response.text())
            data = payload.get("data") if isinstance(payload, dict) else payload
            short_name = parsed.path.rsplit("/", 1)[-1]

            # Retain only list responses whose request was captured in this fresh epoch.
            if parsed.path in {
                "/yewu11/v1/m/matchesPagePB",
                "/yewu11/v1/m/noLiveMatchesPagePB",
            }:
                template = self._request_templates_by_id.get(id(response.request))
                if template is None:
                    template = live.CAPTURED_REQUESTS.get(("eb", parsed.path))
                captured_at = (template or {}).get("captured_at_monotonic", 0.0)
                if captured_at >= self._nba_capture_started > 0:
                    self._list_responses[parsed.path] = {
                        "response": payload,
                        "request_template": dict(template),
                        "captured_at": captured_at,
                    }

            # Page-list diagnostics deliberately expose only schema/counts, never raw rows.
            if parsed.path.endswith("/matchesPagePB"):
                rows = data.get("data") if isinstance(data, dict) else data
                rows = rows if isinstance(rows, list) else []
                nba_exact = 0
                sport_counts = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sport_id = str(row.get("csid") or "<empty>")
                    sport_counts[sport_id] = sport_counts.get(sport_id, 0) + 1
                    leagues = {
                        str(row.get(key) or "").strip().upper()
                        for key in ("tn", "tnjc")
                        if str(row.get(key) or "").strip()
                    }
                    if leagues == {"NBA"} and row.get("mid"):
                        nba_exact += 1
                print(
                    "[EB_RESPONSE_DECODE]",
                    f"path={short_name}",
                    f"http={response.status}",
                    f"data_type={type(data).__name__}",
                    f"row_count={len(rows)}",
                    f"exact_nba_rows={nba_exact}",
                    f"csid_counts={json.dumps(sport_counts, ensure_ascii=False, sort_keys=True)}",
                    flush=True,
                )
            else:
                fields = sorted(str(key) for key in data.keys())[:40] if isinstance(data, dict) else []
                print(
                    "[EB_RESPONSE_DECODE]",
                    f"path={short_name}",
                    f"http={response.status}",
                    f"data_type={type(data).__name__}",
                    f"data_fields={json.dumps(fields, ensure_ascii=False)}",
                    flush=True,
                )

            if not parsed.path.endswith("/getMatchDetailPB"):
                return
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            request = response.request
            request_payload = {"url_params": query}
            raw_body = getattr(request, "post_data", None)
            if raw_body:
                try:
                    request_payload["body"] = json.loads(raw_body)
                except Exception:
                    request_payload["body"] = dict(parse_qsl(raw_body, keep_blank_values=True))
            mid = str(query.get("mid") or (request_payload.get("body") or {}).get("mid") or "")
            if not mid:
                candidates = _walk_field_values(data, {"mid": {"mid"}})["mid"]
                ids = {value for value, _ in candidates}
                if len(ids) == 1:
                    mid = next(iter(ids))
            if mid:
                self._detail_responses[mid] = {
                    "request": request_payload, "response": payload,
                    "captured_at": time.time(),
                }
        except Exception as exc:
            # Do not print URLs, headers, request bodies, or response content.
            print(
                "[EB_RESPONSE_DECODE] response_parse_failed",
                f"error={type(exc).__name__}",
                flush=True,
            )

    def fetch_match_detail(self, event):
        """Build a detail request from the live yewu11 template and the discovered event mid."""
        mid = str(event.get("mid") or "").strip()
        if not mid:
            raise RuntimeError("NBA 赛事列表记录缺少 mid，无法请求赛事详情")
        path = "/yewu11/v1/w/matchDetail/getMatchDetailPB"
        origin = live.CAPTURED_ORIGINS.get("eb")
        if not origin:
            raise RuntimeError("尚未捕获 OP7 yewu11 API 来源；请先加载 NBA 赛事列表")
        response = live.request_json(
            self.op7, origin + path, "GET",
            live.headers_env("OP7_READONLY_HEADERS_JSON"),
            params={"mid": mid}, timeout_ms=self.timeout * 1000, family="eb")
        payload = _decode_api_response(response)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0000000":
            code = payload.get("code") if isinstance(payload, dict) else "INVALID_RESPONSE"
            raise RuntimeError(f"NBA 赛事 {mid} 详情接口返回异常状态 code={code}")
        self._detail_responses[mid] = {
            "request": {"url_params": {"mid": mid}},
            "response": payload, "captured_at": time.time(),
        }
        return payload

    @staticmethod
    def _detail_market_lists(value):
        """Find actual hps market arrays inside the decoded detail payload."""
        found = []
        seen = set()
        def walk(node):
            if isinstance(node, dict):
                markets = node.get("hps")
                if isinstance(markets, list) and id(markets) not in seen:
                    seen.add(id(markets))
                    found.extend(x for x in markets if isinstance(x, dict))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(value)
        return found

    def runtime_context_for_event(self, event_id, market=None, group=None):
        record = self._detail_responses.get(str(event_id))
        if not record:
            raise RuntimeError(
                f"未捕获赛事 {event_id} 的 getMatchDetailPB 详情响应；"
                "请在 OP7 检测界面打开该场 NBA 赛事详情后重试"
            )
        return _resolve_runtime_fields(
            [("detail_request", record["request"]), ("detail_response", record["response"])],
            event_id, market=market, group=group,
        )

    def discover_nba_events(self, max_pages=20):
        """Fetch current EB event pages and return only explicitly identified NBA events."""
        target_paths = (
            "/yewu11/v1/m/matchesPagePB",
            "/yewu11/v1/m/noLiveMatchesPagePB",
        )
        input(
            "请先在 OP7 浏览器进入「篮球 / NBA 正规赛事」列表；"
            "确认页面已打开后回到终端按 Enter，脚本随后开始捕获最新列表请求："
        )

        # Drain callbacks queued while input() blocked Playwright's event loop.
        for page in list(self.op7.pages):
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass

        # Pre-confirmation templates and responses are not eligible for this run.
        self._nba_capture_started = time.monotonic()
        self._list_responses.clear()
        for endpoint in target_paths:
            live.CAPTURED_REQUESTS.pop(("eb", endpoint), None)

        print(
            "请现在刷新一次当前 NBA 列表页；"
            "脚本正在等待 matchesPagePB 或 noLiveMatchesPagePB 的最新请求（最多 20 秒）...",
            flush=True,
        )
        deadline = time.monotonic() + 20.0
        first_response_at = None
        while time.monotonic() < deadline:
            if self._list_responses and first_response_at is None:
                first_response_at = time.monotonic()
            if first_response_at is not None and time.monotonic() - first_response_at >= 1.5:
                break
            for page in list(self.op7.pages):
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    pass
            if not self.op7.pages:
                break

        if not self._list_responses:
            raise RuntimeError(
                "确认进入 NBA 列表页后，未捕获到新的 matchesPagePB 或 "
                "noLiveMatchesPagePB 请求；请确认列表已加载，并在提示后刷新页面重试。"
            )

        # If both endpoints were observed, select the one with the latest request timestamp.
        selected_path, selected_record = max(
            self._list_responses.items(),
            key=lambda item: item[1]["captured_at"],
        )
        path = selected_path
        template = selected_record["request_template"]
        print(
            "[EB_NBA_LIST_SOURCE]",
            f"endpoint={path.rsplit('/', 1)[-1]}",
            "source=post_confirmation_latest_request",
            flush=True,
        )
        capture_status = live.eb_nba_capture_status()
        print("\n=== EB NBA 页面监听状态 ===")
        for endpoint, state in capture_status.items():
            short_name = endpoint.rsplit("/", 1)[-1]
            mark = "✓" if state["captured"] else ("已触发/未捕获" if state["seen"] else "未触发")
            print(f"[{mark}] {short_name} method={state['method'] or '-'}")
        print("页面状态：已确认进入 EB NBA 列表页")
        print("检测状态：已捕获本轮最新列表请求")
        try:
            if template.get("method") == "GET":
                body = dict(parse_qsl(urlsplit(template.get("url", "")).query, keep_blank_values=True))
            else:
                body = json.loads(template.get("post_data") or "{}")
        except Exception:
            raise RuntimeError("无法解析浏览器捕获的 EB 赛事列表请求参数") from None
        if not isinstance(body, dict):
            raise RuntimeError("EB 赛事列表请求参数不是对象")
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
        response_schema_samples = []
        classification_keys = {
            "csid", "csna", "tn", "tnjc", "league", "leaguename",
            "league_name", "sport", "sportname", "sport_name",
            "category", "categoryname", "competition", "competitionname",
            "competition_name", "type", "matchtype", "sportid"
        }
        def inspect_candidate_rows(node, found=None):
            found = found if found is not None else []
            if isinstance(node, dict):
                keys = {str(k).lower().replace("-", "_") for k in node}
                has_mid = any(k in keys for k in ("mid", "matchid", "id"))
                has_teams = any(k in keys for k in ("mhn", "man", "homename", "awayname", "home", "away"))
                if has_mid and has_teams and len(found) < 8:
                    class_values = {}
                    for key, value in node.items():
                        normalized = str(key).lower().replace("-", "_")
                        if normalized in classification_keys and not isinstance(value, (dict, list)):
                            class_values[str(key)] = str(value)[:80]
                    found.append({
                        "field_names": sorted(str(k) for k in node.keys())[:80],
                        "classification_values": class_values,
                    })
                for child in node.values():
                    inspect_candidate_rows(child, found)
            elif isinstance(node, list):
                for child in node:
                    inspect_candidate_rows(child, found)
            return found
        for page_no in range(1, max(1, int(max_pages)) + 1):
            body["cpn"] = page_no
            if page_no == 1:
                response = selected_record["response"]
            else:
                response = live.request_json(
                    self.op7, origin + path, template.get("method", "POST"),
                    live.headers_env("OP7_READONLY_HEADERS_JSON"),
                    params=body if template.get("method") == "GET" else None,
                    body=body if template.get("method") == "POST" else None,
                    timeout_ms=self.timeout * 1000, family="eb")
            data = live.data_of(response)
            response_schema_samples.append({
                "data_type": type(data).__name__,
                "data_field_names": sorted(str(k) for k in data.keys())[:80] if isinstance(data, dict) else [],
                "nested_data_type": type(data.get("data")).__name__ if isinstance(data, dict) else None,
                "candidate_event_rows": inspect_candidate_rows(data),
            })
            if isinstance(data, dict):
                pages_value = data.get("pages")
                try:
                    total_pages = max(1, min(int(pages_value or 1), int(max_pages)))
                except (TypeError, ValueError):
                    total_pages = 1
                events = data.get("data") or []
            elif isinstance(data, list):
                pages_value = 1
                events = data
                total_pages = 1
            else:
                raise RuntimeError("EB 赛事列表响应结构无法识别；拒绝猜测赛事字段")
            if not isinstance(events, list):
                raise RuntimeError("EB 赛事列表 data 字段不是数组；拒绝猜测分页结构")

            # Count every row on every fetched page; the eight sample rows above
            # are illustrative only and must not be mistaken for the full result.
            page_nba_exact = 0
            page_nba_missing_mid = 0
            page_csids = {}
            page_leagues = {}
            for event in events:
                if not isinstance(event, dict):
                    continue
                csid = str(event.get("csid") or "<empty>")
                page_csids[csid] = page_csids.get(csid, 0) + 1
                tn = str(event.get("tn") or "").strip()
                tnjc = str(event.get("tnjc") or "").strip()
                league_key = f"tn={tn or '<empty>'} | tnjc={tnjc or '<empty>'}"
                page_leagues[league_key] = page_leagues.get(league_key, 0) + 1

                league_names = {
                    value.upper() for value in (tn, tnjc) if value
                }
                if str(event.get("csid")) != "2" or league_names != {"NBA"}:
                    continue
                page_nba_exact += 1
                if not event.get("mid"):
                    page_nba_missing_mid += 1
                    continue
                rows_by_id[str(event["mid"])] = event

            print(
                "[EB_NBA_PAGE_DIAG]",
                f"page={page_no}",
                f"rows={len(events)}",
                f"pages_type={type(pages_value).__name__}",
                f"pages_value={str(pages_value)[:80]}",
                f"exact_nba_rows={page_nba_exact}",
                f"nba_missing_mid={page_nba_missing_mid}",
                f"csid_counts={json.dumps(page_csids, ensure_ascii=False, sort_keys=True)}",
                f"league_counts={json.dumps(page_leagues, ensure_ascii=False, sort_keys=True)}",
                flush=True,
            )
            if page_no >= total_pages:
                break
        if not rows_by_id:
            print("\\n=== EB NBA 赛事列表结构诊断（不含凭证、请求体或完整赛事内容） ===")
            print(json.dumps({
                "response_samples": response_schema_samples[:3],
                "filter_rule": {
                    "csid_required": "2",
                    "league_fields": ["tn", "tnjc"],
                    "league_value_required": "NBA",
                    "mid_required": True,
                },
            }, ensure_ascii=False, indent=2))
        return list(rows_by_id.values())

    def discover_nba_total_selections(self):
        """Discover NBA events and report safe schema diagnostics if totals cannot be resolved."""
        events = self.discover_nba_events()
        selections = []
        diagnostics = {
            "nba_events": len(events),
            "details_fetched": 0,
            "details_with_hps": 0,
            "markets_total": 0,
            "markets_hpid_38": 0,
            "groups_hpid_38": 0,
            "over_under_options": 0,
            "complete_options": 0,
            "missing_required_fields": {},
            "market_ids": {},
            "market_name_samples": [],
            "event_field_names": [],
            "market_field_names": [],
            "group_field_names": [],
            "option_field_names": [],
        }

        def add_field_names(key, value):
            if isinstance(value, dict) and not diagnostics[key]:
                diagnostics[key] = sorted(str(name) for name in value.keys())[:80]

        for event in events:
            add_field_names("event_field_names", event)
            detail = self.fetch_match_detail(event)
            diagnostics["details_fetched"] += 1
            detail_data = detail.get("data") if isinstance(detail, dict) else None
            markets = self._detail_market_lists(detail_data)
            if markets:
                diagnostics["details_with_hps"] += 1
            diagnostics["markets_total"] += len(markets)
            for market in markets:
                hpid = str(market.get("hpid") or "")
                diagnostics["market_ids"][hpid or "<empty>"] = (
                    diagnostics["market_ids"].get(hpid or "<empty>", 0) + 1
                )
                add_field_names("market_field_names", market)
                if len(diagnostics["market_name_samples"]) < 12:
                    name = market.get("hpn") or market.get("name") or market.get("hn")
                    if name not in (None, ""):
                        diagnostics["market_name_samples"].append(str(name)[:100])
                if hpid != "38":
                    continue
                diagnostics["markets_hpid_38"] += 1
                for group in market.get("hl") or []:
                    if not isinstance(group, dict):
                        continue
                    diagnostics["groups_hpid_38"] += 1
                    add_field_names("group_field_names", group)
                    for option in group.get("ol") or []:
                        if not isinstance(option, dict):
                            continue
                        side = str(option.get("ot") or "").strip().lower()
                        if side not in {"under", "over"}:
                            continue
                        diagnostics["over_under_options"] += 1
                        add_field_names("option_field_names", option)
                        required = {
                            "event.mid": event.get("mid"),
                            "event.tid": event.get("tid"),
                            "event.csid": event.get("csid"),
                            "event.csna": event.get("csna"),
                            "market.hpn": market.get("hpn"),
                            "group.hid": group.get("hid"),
                            "option.oid": option.get("oid"),
                            "option.ov": option.get("ov"),
                        }
                        missing = [name for name, value in required.items() if value in (None, "")]
                        if missing:
                            for name in missing:
                                diagnostics["missing_required_fields"][name] = (
                                    diagnostics["missing_required_fields"].get(name, 0) + 1
                                )
                            continue
                        diagnostics["complete_options"] += 1
                        runtime_context = self.runtime_context_for_event(
                            event["mid"], market=market, group=group)
                        selections.append({
                            "event": event, "market": market, "group": group,
                            "option": option, "runtime_context": runtime_context,
                        })
        if not selections:
            print("\\n=== EB NBA 大小分字段诊断（仅字段名和计数，不含请求凭证或原始响应） ===")
            print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
            if not events:
                reason = "没有从赛事列表筛选到 NBA 正规赛事；请核对赛事列表响应中的联赛字段"
            elif diagnostics["markets_total"] == 0:
                reason = "详情响应中没有找到 hps 盘口数组"
            elif diagnostics["markets_hpid_38"] == 0:
                reason = "详情中没有 hpid=38 的盘口；需要根据实际市场名称确认全场大小分对应的 hpid"
            elif diagnostics["over_under_options"] == 0:
                reason = "hpid=38 盘口中没有 ot=over/under 的选项"
            else:
                reason = "找到了大小分选项，但缺少构建选项所需字段；请查看 missing_required_fields"
            raise RuntimeError("实时 NBA 全场大小分选项未能完整提取：" + reason)
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
