#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge the logged-in Playwright browser sessions to the OP7 test workflow.

Credentials remain in the browser profile/context and process memory. This module
never writes cookies, authorization headers, request bodies, or device identifiers
to the endpoint configuration file.
"""
import json
import re
import sys
import threading
import time
from decimal import Decimal
from datetime import datetime, timezone
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


def _split_eb_teams(event):
    """Read separate EB team fields, or split a combined 'home vs away' label."""
    home = str(event.get("home") or event.get("mhn") or "").strip()
    away = str(event.get("away") or event.get("man") or "").strip()
    combined = home if re.search(r"\s*vs\.?\s*", home, re.I) else ""
    if not combined:
        for key in ("mn", "matchName", "match_name", "eventName", "event_name"):
            value = str(event.get(key) or "").strip()
            if re.search(r"\s*vs\.?\s*", value, re.I):
                combined = value
                break
    if combined:
        parts = re.split(r"\s*vs\.?\s*", combined, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            home, away = parts[0].strip(), parts[1].strip()
    return home, away


def _team_name_matches(eb_name, pingbo_names):
    """Match canonical NBA identities first, then conservative normalized containment."""
    eb_name = str(eb_name or "").strip()
    if not eb_name:
        return False
    eb_id = mapper.nba_team_id(eb_name)
    for pb_name in pingbo_names:
        pb_name = str(pb_name or "").strip()
        if not pb_name:
            continue
        pb_id = mapper.nba_team_id(pb_name)
        if eb_id and pb_id:
            if eb_id == pb_id:
                return True
            continue
        left = mapper.norm_team(eb_name)
        right = mapper.norm_team(pb_name)
        if left and right and (left in right or right in left):
            return True
    return False


def _kickoff_hour_key(value):
    stamp = mapper.timestamp_ms(value)
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime("%Y%m%d%H")


def _clock_seconds(value):
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,3}):(\d{1,2})(?::(\d{1,2}))?", text)
    if match:
        parts = [int(x) for x in match.groups() if x is not None]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _score_pair(value):
    match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _event_progress_signature(event, reverse_score=False):
    """Extract a complete period/clock/score signature without guessing missing fields."""
    aliases = {
        "period": {"period","quarter","currentperiod","currentquarter","periodname","quartername","stage"},
        "clock": {"clock","gameclock","matchclock","remainingtime","timeremaining","livetime","matchtimeleft"},
        "home_score": {"homescore","hometeamscore","scorehome"},
        "away_score": {"awayscore","awayteamscore","scoreaway"},
        "score": {"score","currentscore","gamescore","scoreboard"},
    }
    found = {key: [] for key in aliases}
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                normalized = str(key).replace("_", "").replace("-", "").lower()
                for name, keys in aliases.items():
                    if normalized in keys and value not in (None, "") and not isinstance(value, (dict, list)):
                        found[name].append(str(value).strip())
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(event)
    result = {}
    for key, values in found.items():
        unique = list(dict.fromkeys(values))
        if len(unique) > 1:
            return None
        if unique:
            result[key] = unique[0]
    if "score" not in result and {"home_score", "away_score"} <= result.keys():
        score = (int(result["home_score"]), int(result["away_score"]))
        if reverse_score:
            score = (score[1], score[0])
    else:
        score = _score_pair(result.get("score"))
        if score and reverse_score:
            score = (score[1], score[0])
    clock = _clock_seconds(result.get("clock"))
    period = str(result.get("period") or "").strip().lower()
    if not period or clock is None or score is None:
        return None
    return {"period": period, "clock_seconds": clock, "score": score}


def _progress_matches(left, right):
    if not left or not right or left["period"] != right["period"]:
        return False
    if abs(left["clock_seconds"] - right["clock_seconds"]) > 60:
        return False
    return (
        abs(left["score"][0] - right["score"][0]) <= 5
        and abs(left["score"][1] - right["score"][1]) <= 5
    )


def _find_pingbo_event(payload, event):
    eb_home_raw, eb_away_raw = _split_eb_teams(event)
    if not eb_home_raw or not eb_away_raw:
        raise RuntimeError(f"EB 赛事球队名称无法按 vs 拆分：{eb_home_raw} vs {eb_away_raw}")
    target_hour = _kickoff_hour_key(event.get("mgt") or event.get("start_time_ms"))
    eb_progress = _event_progress_signature(event)
    candidates = []
    home_candidates = []
    for item in mapper.extract_pingbo_events(payload):
        pb_home_raw = str(item.get("home") or "").strip()
        pb_away_raw = str(item.get("away") or "").strip()
        pb_home_names = (pb_home_raw, item.get("home_zh"))
        pb_away_names = (pb_away_raw, item.get("away_zh"))
        # First locate the EB home team anywhere in the Pingbo fixture.
        if _team_name_matches(eb_home_raw, pb_home_names):
            opponent_names, reverse = pb_away_names, False
        elif _team_name_matches(eb_home_raw, pb_away_names):
            opponent_names, reverse = pb_home_names, True
        else:
            continue
        home_candidates.append(item)
        # Only after the home team has identified a fixture, validate the away team.
        if not _team_name_matches(eb_away_raw, opponent_names):
            continue
        stamp_hour = _kickoff_hour_key(item.get("start_time_ms"))
        kickoff_match = target_hour is not None and stamp_hour is not None and target_hour == stamp_hour
        pb_progress = _event_progress_signature(item.get("_raw_event") or item, reverse_score=reverse)
        progress_match = _progress_matches(eb_progress, pb_progress)
        if kickoff_match or progress_match:
            candidates.append(item)
    if len(candidates) != 1:
        raise RuntimeError(
            f"Pingbo event mapping failed: candidates={len(candidates)}; "
            f"home_team_candidates={len(home_candidates)}; EB={eb_home_raw} vs {eb_away_raw}; "
            "开赛时间按年月日和小时一致，或滚球节次一致、时钟相差不超过1分钟且主客队对应比分各相差不超过5分"
        )
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
    # A captured manual bet request is the authoritative source for these
    # four runtime fields. Do not mix values from read APIs/storage into it.
    manual_sources = [
        (source_name, payload)
        for source_name, payload in sources
        if source_name.startswith("manual_bet_request:")
    ]
    preferred_sources = manual_sources if manual_sources else sources
    resolved = {}
    for field in aliases:
        values = []
        for source_name, payload in preferred_sources:
            # Manual bet values are taken from the exact submitted request
            # payload, without market-tree scoping or fallback to other APIs.
            scan_payload = payload
            if not manual_sources and field == "market_type_finally" and market is not None:
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
            if manual_sources:
                raise RuntimeError(
                    f"人工投注请求中缺少字段 {field}；"
                    "本轮不会从详情接口、盘口接口或浏览器存储补值"
                )
            raise RuntimeError(f"赛事 {event_id} 详情上下文缺少字段 {field}；拒绝使用默认值")
        if len(distinct) != 1:
            origins = ", ".join(where for _, where in values)
            source_label = "人工投注请求" if manual_sources else "运行参数来源"
            raise RuntimeError(f"赛事 {event_id} 的{source_label}字段 {field} 存在冲突：{origins}")
        resolved[field] = next(iter(distinct))
        resolved.setdefault("_sources", {})[field] = [where for _, where in values]
    resolved["_event_id"] = str(event_id)
    if market is not None:
        resolved["_market_hpid"] = str(market.get("hpid") or "")
    if group is not None:
        resolved["_market_id"] = str(group.get("hid") or "")
    return resolved

def _console_prompt(message):
    """Show required browser-operation prompts even while diagnostic stdout is suppressed."""
    live.USER_INPUT_WAITING.set()
    try:
        sys.__stdout__.write("\n" + str(message).strip() + "\n")
        sys.__stdout__.flush()
        return input()
    finally:
        live.USER_INPUT_WAITING.clear()


def _console_stage(message):
    sys.__stdout__.write("[执行节点] " + str(message).strip() + "\n")
    sys.__stdout__.flush()


_PAGE_AUTO_SCROLL_SCRIPT = r"""
(() => {
  if (window.__op7AlternatingScrollTimer) return;
  // First movement is upward after one minute, then alternate down/up.
  window.__op7AlternatingScrollDirection = -1;
  window.__op7AlternatingScrollTimer = window.setInterval(() => {
    try {
      const candidates = [document.scrollingElement, ...document.querySelectorAll("*")];
      let target = document.scrollingElement || document.documentElement;
      let bestScrollable = 0;
      for (const el of candidates) {
        if (!el || !el.getBoundingClientRect) continue;
        const range = el.scrollHeight - el.clientHeight;
        if (range > bestScrollable && el.clientHeight > 0) {
          const style = window.getComputedStyle(el);
          if (el === document.scrollingElement || /(auto|scroll)/.test(style.overflowY)) {
            bestScrollable = range;
            target = el;
          }
        }
      }
      const amount = Math.max(300, Math.floor((target.clientHeight || window.innerHeight) * 0.7));
      const direction = window.__op7AlternatingScrollDirection;
      target.scrollBy({top: direction * amount, behavior: "smooth"});
      console.log("[页面滚动] " + (direction < 0 ? "上滑" : "下滑"));
      window.__op7AlternatingScrollDirection *= -1;
    } catch (_) {}
  }, 60000);
})();
"""


class BrowserSessionBridge:
    """Own both persistent browser contexts for one CLI execution."""

    def __init__(self, profiles_dir="browser_profiles", timeout=15):
        self.profiles_dir = Path(profiles_dir)
        self.timeout = int(timeout)
        self.playwright = None
        self.op7 = None
        self.pingbo = None
        self._detail_responses = {}
        self._odds_responses = {}
        self._init_responses = []
        self._manual_bet_runtime_requests = []
        self._manual_runtime_initialized = False
        self._odds_request_template = None
        self._list_responses = {}
        self._request_templates_by_id = {}
        self._request_templates_by_signature = {}
        self._nba_capture_started = 0.0
        self._nba_list_template = None
        self._nba_list_path = None
        self._nba_capture_confirmed = False
        self._attached_pages = {"op7": set(), "pingbo": set()}
        self._ignored_event_ids = set()
        self._pingbo_feed_responses = {}
        self._pingbo_capture_confirmed = False
        self._pingbo_market_payload = None
        self._pingbo_last_refresh_monotonic = 0.0
        self._pingbo_refresh_interval_seconds = 20.0
        self._round_pingbo_matched_event_ids = set()
        self._pending_event_snapshots = {"EB": None, "PINGBO": None}
        self._pending_event_snapshot_lock = threading.Lock()
        self._pending_event_monitor_stop = threading.Event()
        self._pending_event_monitor_thread = threading.Thread(
            target=self._pending_event_monitor_loop,
            name="nba-pending-event-monitor",
            daemon=True,
        )
        self._pending_event_monitor_thread.start()

    @staticmethod
    def _format_pending_event(event):
        home = event.get('home') or event.get('mhn') or '?'
        away = event.get('away') or event.get('man') or '?'
        event_id = event.get('mid') or event.get('event_id') or '?'
        start = event.get('mgt') or event.get('start_time_ms') or event.get('time') or '?'
        league = event.get('tnjc') or event.get('league') or 'NBA'
        return f"{league} | {home} vs {away} | event_id={event_id} | start={start}"

    def _set_pending_event_snapshot(self, venue, events):
        with self._pending_event_snapshot_lock:
            self._pending_event_snapshots[venue] = [
                self._format_pending_event(event) for event in events
            ]

    def _pending_event_monitor_loop(self):
        while not self._pending_event_monitor_stop.wait(600):
            if live.USER_INPUT_WAITING.is_set():
                continue
            with self._pending_event_snapshot_lock:
                snapshots = {
                    venue: list(rows) if rows is not None else None
                    for venue, rows in self._pending_event_snapshots.items()
                }
            for venue, label in (("EB", "【EB】"), ("PINGBO", "【pingBo】")):
                rows = snapshots.get(venue)
                if rows is None:
                    continue
                print(f"{label}[待匹配赛事｜10分钟定时输出] 数量={len(rows)}", flush=True)
                if not rows:
                    print(f"{label}[待匹配赛事] 当前没有已获取的 NBA 赛事。", flush=True)
                else:
                    for index, row in enumerate(rows, 1):
                        print(f"{label}[待匹配赛事] {index}/{len(rows)} {row}", flush=True)

    @staticmethod
    def _prompt_site_url(label):
        while True:
            live.USER_INPUT_WAITING.set()
            try:
                value = input(f"请输入 {label} 访问地址（http:// 或 https://）：").strip()
            finally:
                live.USER_INPUT_WAITING.clear()
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
        page.on("console", lambda msg: _console_stage(msg.text) if msg.type == "log" and msg.text.startswith("[页面滚动]") else None)
        try:
            # Install in the current document and every subsequent navigation.
            page.add_init_script(_PAGE_AUTO_SCROLL_SCRIPT)
            page.evaluate(_PAGE_AUTO_SCROLL_SCRIPT)
            _console_stage(f"{family} 页面已启用自动滚动：每 60 秒上滑/下滑交替一次。")
        except Exception as exc:
            _console_stage(f"{family} 页面自动滚动未能启用：{type(exc).__name__}")
        if family == "op7":
            page.on("request", self._capture_op7_request)
            page.on("response", self._capture_op7_response)
        else:
            page.on("request", lambda req, f=family: live.capture_request(req, f))
            page.on("response", self._capture_pingbo_response)

    def _capture_pingbo_response(self, response):
        """Capture sanitized schema diagnostics for Pingbo mobile odds feeds."""
        try:
            parsed = urlsplit(response.url)
            if parsed.path not in {"/sports-service/sv/mobile/odds-live",
                                   "/sports-service/sv/mobile/odds-today"}:
                return
            request = response.request
            request_params = dict(parse_qsl(urlsplit(request.url).query, keep_blank_values=True))
            if request.method == "POST" and request.post_data:
                try:
                    posted = request.post_data
                    body_params = json.loads(posted) if posted.lstrip().startswith("{") else dict(parse_qsl(posted, keep_blank_values=True))
                    if isinstance(body_params, dict):
                        request_params.update({str(k): str(v) for k, v in body_params.items()})
                except Exception:
                    pass
            if request_params.get("sportId") != "4" or request_params.get("leagueIds") != "487":
                return
            try:
                payload = json.loads(response.text())
            except Exception:
                print("【pingBo】[RESPONSE] invalid_json",
                      f"path={parsed.path.rsplit('/', 1)[-1]}",
                      f"http={response.status}", flush=True)
                return
            payload = live.unwrap(payload)
            events = mapper.extract_pingbo_events(payload)
            self._set_pending_event_snapshot("PINGBO", events)
            root = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
            leagues = root.get("leagues", []) if isinstance(root, dict) else []
            raw_count = sum(len(x.get("events") or []) for x in leagues
                            if isinstance(x, dict) and isinstance(x.get("events"), list)) if isinstance(leagues, list) else 0
            self._pingbo_feed_responses[parsed.path] = {
                "request_params": request_params,
                "payload": payload, "captured_at": time.time(),
                "status": response.status, "event_count": len(events),
            }
            self._pingbo_market_payload = payload
            self._pingbo_last_refresh_monotonic = time.monotonic()
            self._pingbo_active_feed_path = parsed.path
            self._pingbo_capture_confirmed = True
            print("【pingBo】[RESPONSE]",
                  f"path={parsed.path.rsplit('/', 1)[-1]}",
                  f"http={response.status}",
                  f"league_count={len(leagues) if isinstance(leagues, list) else 0}",
                  f"raw_event_count={raw_count}",
                  f"nba_event_count={len(events)}", flush=True)
        except Exception as exc:
            print("【pingBo】[RESPONSE] decode_failed", f"error={type(exc).__name__}", flush=True)

    def __exit__(self, exc_type, exc, tb):
        self._pending_event_monitor_stop.set()
        if self._pending_event_monitor_thread.is_alive():
            self._pending_event_monitor_thread.join(timeout=1.0)
        try:
            if exc_type is not None:
                print("\\n检测过程中发生异常。浏览器暂时保持打开，便于检查页面；回到终端按 Enter 后关闭浏览器。")
                live.USER_INPUT_WAITING.set()
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
                finally:
                    live.USER_INPUT_WAITING.clear()
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
            # Observe the manual bet API request without modifying or aborting it.
            # Keep the parsed payload in memory only; never log field values or headers.
            if request.method.upper() == "POST" and parsed.path.startswith("/yewu13/") and (
                parsed.path.endswith("/bet") or "/betOrder/client/bet" in parsed.path
            ):
                bet_payload = {
                    "query": dict(parse_qsl(parsed.query, keep_blank_values=True))
                }
                raw_bet_body = getattr(request, "post_data", None)
                if raw_bet_body:
                    try:
                        bet_payload["body"] = json.loads(raw_bet_body)
                    except Exception:
                        bet_payload["body"] = dict(parse_qsl(raw_bet_body, keep_blank_values=True))
                aliases = {
                    "device_imei": {"deviceimei", "imei"},
                    "cuid": {"cuid"},
                    "match_type": {"matchtype"},
                    "market_type_finally": {"markettypefinally"},
                }
                found = _walk_field_values(bet_payload, aliases)
                self._manual_bet_runtime_requests.append({
                    "path": parsed.path,
                    "method": request.method,
                    "payload": bet_payload,
                    "captured_at": time.monotonic(),
                    "fields_found": {name: bool(entries) for name, entries in found.items()},
                })
                self._manual_bet_runtime_requests = self._manual_bet_runtime_requests[-5:]
                for field, entries in found.items():
                    paths = sorted({where for _, where in entries})
                    print(
                        "【EB】[EB_MANUAL_BET_RUNTIME_DIAG]",
                        f"method={request.method}",
                        f"path={parsed.path}",
                        f"field={field}",
                        f"found={bool(entries)}",
                        f"paths={json.dumps(paths, ensure_ascii=False)}",
                        "request_forwarded=True",
                        flush=True,
                    )
            # Trace only the presence and paths of explicitly named runtime fields
            # in OP7 read requests. Never log values, headers, cookies or URLs.
            if parsed.path.startswith("/yewu11/"):
                runtime_aliases = {
                    "device_imei": {"deviceimei", "imei"},
                    "cuid": {"cuid"},
                    "match_type": {"matchtype"},
                    "market_type_finally": {"markettypefinally"},
                }
                request_payload = {
                    "query": dict(parse_qsl(parsed.query, keep_blank_values=True))
                }
                raw_runtime_body = getattr(request, "post_data", None)
                if raw_runtime_body:
                    try:
                        request_payload["body"] = json.loads(raw_runtime_body)
                    except Exception:
                        request_payload["body"] = dict(parse_qsl(raw_runtime_body, keep_blank_values=True))
                for field, entries in _walk_field_values(
                    request_payload, runtime_aliases
                ).items():
                    if entries:
                        paths = sorted({where for _, where in entries})
                        print(
                            "【EB】[EB_REQUEST_RUNTIME_DIAG]",
                            f"method={request.method}",
                            f"path={parsed.path}",
                            f"field={field}",
                            "found=True",
                            f"paths={json.dumps(paths, ensure_ascii=False)}",
                            flush=True,
                        )
            # Detail-page requests need their own sanitized trace. The generic
            # capture helper intentionally logs only the NBA list lifecycle,
            # so without this line an unseen detail request is indistinguishable
            # from a click that emitted no API call.
            if parsed.path == "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB":
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                template = live.CAPTURED_REQUESTS.get(("eb", parsed.path))
                if template and query.get("cuid") and query.get("mcid") is not None:
                    self._odds_request_template = {
                        "method": request.method,
                        "url": request.url,
                        "query": query,
                        "captured_at_monotonic": template.get("captured_at_monotonic", time.monotonic()),
                    }
                print(
                    "【EB】[EB_ODDS_REQUEST]",
                    f"method={request.method}",
                    f"path={parsed.path}",
                    f"has_mid={bool(query.get('mid'))}",
                    f"has_mcid={query.get('mcid') is not None}",
                    f"has_cuid={bool(query.get('cuid'))}",
                    f"captured={bool(self._odds_request_template)}",
                    flush=True,
                )
            if parsed.path.startswith("/yewu11/") and (
                "detail" in parsed.path.lower() or "matchdetail" in parsed.path.lower()
            ):
                api = live.ENDPOINTS.get("apis", {}).get("eb", {})
                allowed_paths = set(api.get("read_paths", []) + api.get("bet_paths", []))
                captured_template = live.CAPTURED_REQUESTS.get(("eb", parsed.path))
                print(
                    "【EB】[EB_DETAIL_REQUEST]",
                    "family=op7",
                    f"method={request.method}",
                    f"path={parsed.path}",
                    f"resource_type={getattr(request, 'resource_type', '')}",
                    f"allowed={parsed.path in allowed_paths}",
                    f"captured={bool(captured_template)}",
                    flush=True,
                )
            if parsed.path in {
                "/yewu11/v1/m/matchesPagePB",
                "/yewu11/v1/m/noLiveMatchesPagePB",
            }:
                template = live.CAPTURED_REQUESTS.get(("eb", parsed.path))
                if template:
                    snapshot = dict(template)
                    self._request_templates_by_id[id(request)] = snapshot
                    signature = (request.url, request.method, request.post_data or "")
                    self._request_templates_by_signature[signature] = snapshot
        except Exception as exc:
            print("[EB_CAPTURE_DEBUG] request_capture_failed", f"error={type(exc).__name__}", flush=True)

    def _capture_op7_response(self, response):
        """Decode EB API responses as they arrive; retain only detail payloads in memory."""
        try:
            parsed = urlsplit(response.url)
            if not parsed.path.startswith(("/yewu11/",)):
                return
            odds_path = "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB"
            if not (parsed.path in live.EB_NBA_PAGE_ENDPOINTS
                    or parsed.path.endswith("/getMatchDetailPB")
                    or parsed.path.endswith("/initPB")
                    or parsed.path == odds_path):
                return

            payload = _decode_api_response(response.text())
            data = payload.get("data") if isinstance(payload, dict) else payload
            short_name = parsed.path.rsplit("/", 1)[-1]

            # Keep a small in-memory sample of init responses for runtime-field discovery.
            # Never log response values or persist device identifiers.
            if parsed.path.endswith("/initPB"):
                self._init_responses.append(payload)
                self._init_responses = self._init_responses[-5:]
                init_found = _walk_field_values(payload, {
                    "device_imei": {"deviceimei", "imei"},
                    "cuid": {"cuid"},
                    "match_type": {"matchtype"},
                    "market_type_finally": {"markettypefinally"},
                })
                for field, entries in init_found.items():
                    paths = sorted({where for _, where in entries})
                    print("【EB】[EB_INIT_RUNTIME_DIAG]", f"field={field}",
                          f"found={bool(entries)}",
                          f"paths={json.dumps(paths, ensure_ascii=False)}", flush=True)

            # Retain only list responses whose request was captured in this fresh epoch.
            if parsed.path in {
                "/yewu11/v1/m/matchesPagePB",
                "/yewu11/v1/m/noLiveMatchesPagePB",
            }:
                response_request = response.request
                signature = (
                    response_request.url,
                    response_request.method,
                    response_request.post_data or "",
                )
                template = self._request_templates_by_signature.get(signature)
                if template is None:
                    template = self._request_templates_by_id.get(id(response_request))
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
                    "【EB】[EB_RESPONSE_DECODE]",
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
                    "【EB】[EB_RESPONSE_DECODE]",
                    f"path={short_name}",
                    f"http={response.status}",
                    f"data_type={type(data).__name__}",
                    f"data_fields={json.dumps(fields, ensure_ascii=False)}",
                    flush=True,
                )

            if parsed.path == odds_path:
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                mid = str(query.get("mid") or "")
                if mid:
                    self._odds_responses[mid] = {
                        "request": {"url_params": query},
                        "response": payload,
                        "captured_at": time.time(),
                    }
                return

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

    def fetch_match_odds(self, event):
        """Fetch odds using the exact GET query template observed in the authenticated browser."""
        mid = str(event.get("mid") or "").strip()
        if not mid:
            raise RuntimeError("NBA 赛事列表记录缺少 mid，无法请求盘口")
        path = "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB"
        template = self._odds_request_template or live.CAPTURED_REQUESTS.get(("eb", path))
        if not template:
            raise RuntimeError(
                "尚未捕获 getMatchOddsInfoPB 的真实浏览器请求；"
                "请在 OP7 浏览器打开一场 NBA 赛事详情，确认盘口加载后回到终端重试"
            )
        query = dict(parse_qsl(urlsplit(template.get("url", "")).query, keep_blank_values=True))
        if not query.get("mcid") or not query.get("cuid"):
            raise RuntimeError(
                "捕获到 getMatchOddsInfoPB 请求，但缺少 mcid 或 cuid；"
                "请在已登录的 OP7 浏览器重新打开赛事详情并等待盘口加载"
            )
        query["mid"] = mid
        origin = live.CAPTURED_ORIGINS.get("eb")
        if not origin:
            raise RuntimeError("尚未捕获 OP7 yewu11 API 来源，无法请求盘口")
        response = live.request_json(
            self.op7, origin + path, "GET",
            live.headers_env("OP7_READONLY_HEADERS_JSON"),
            params=query, timeout_ms=self.timeout * 1000, family="eb")
        payload = _decode_api_response(response)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0000000":
            code = payload.get("code") if isinstance(payload, dict) else "INVALID_RESPONSE"
            raise RuntimeError(f"NBA 赛事 {mid} 盘口接口返回异常状态 code={code}")
        self._odds_responses[mid] = {
            "request": {"url_params": query},
            "response": payload,
            "captured_at": time.time(),
        }
        data = payload.get("data")
        fields = sorted(str(key) for key in data.keys())[:50] if isinstance(data, dict) else []
        print(
            "【EB】[EB_ODDS_DECODE]",
            f"mid={mid}",
            f"data_type={type(data).__name__}",
            f"data_fields={json.dumps(fields, ensure_ascii=False)}",
            f"hps_count={len(self._detail_market_lists(data))}",
            flush=True,
        )
        return payload

    @staticmethod
    def _detail_market_lists(value):
        """Find market records in decoded EB odds data, including direct market arrays."""
        found = []
        seen_lists = set()
        seen_markets = set()

        def add_market(item):
            if not isinstance(item, dict) or id(item) in seen_markets:
                return
            # Market identity is established by the documented display name and topKey,
            # not by assuming the response always wraps records in an "hps" property.
            if item.get("hpn") and (item.get("topKey") is not None or item.get("hpid") is not None):
                seen_markets.add(id(item))
                found.append(item)

        def walk(node):
            if isinstance(node, dict):
                markets = node.get("hps")
                if isinstance(markets, list) and id(markets) not in seen_lists:
                    seen_lists.add(id(markets))
                    for item in markets:
                        add_market(item)
                add_market(node)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                if id(node) not in seen_lists:
                    seen_lists.add(id(node))
                for child in node:
                    add_market(child)
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
        odds_record = self._odds_responses.get(str(event_id))
        sources = [
            ("detail_request", record["request"]),
            ("detail_response", record["response"]),
        ]
        if odds_record:
            sources.extend([
                ("odds_request", odds_record["request"]),
                ("odds_response", odds_record["response"]),
            ])
        for index, payload in enumerate(self._init_responses):
            sources.append((f"init_response[{index}]", payload))
        # The latest manually submitted bet request is observed as-is and was
        # forwarded normally by the browser. The payload remains memory-only.
        if self._manual_bet_runtime_requests:
            latest_manual = self._manual_bet_runtime_requests[-1]
            sources.append((
                f"manual_bet_request:{latest_manual['path']}",
                latest_manual["payload"],
            ))
            print(
                "【EB】[EB_MANUAL_BET_RUNTIME_SOURCE]",
                f"path={latest_manual['path']}",
                f"captured_fields={json.dumps(latest_manual['fields_found'], ensure_ascii=False, sort_keys=True)}",
                flush=True,
            )

        # Inspect only explicitly named runtime keys in browser storage. Values stay
        # in process memory and are never written to logs or files.
        storage_aliases = {
            "deviceimei", "imei", "cuid", "matchtype", "markettypefinally"
        }
        if self.op7:
            for page_index, page in enumerate(self.op7.pages):
                try:
                    storage_payload = page.evaluate("""() => {
                        const out = {localStorage: {}, sessionStorage: {}};
                        for (const [name, store] of [["localStorage", window.localStorage], ["sessionStorage", window.sessionStorage]]) {
                            for (let i = 0; i < store.length; i++) {
                                const key = store.key(i);
                                const normalized = String(key).replace(/_/g, "").toLowerCase();
                                if (["deviceimei", "imei", "cuid", "matchtype", "markettypefinally"].includes(normalized)) {
                                    out[name][key] = store.getItem(key);
                                }
                            }
                        }
                        return out;
                    }""")
                    if isinstance(storage_payload, dict):
                        sources.append((f"browser_storage[{page_index}]", storage_payload))
                        found = _walk_field_values(storage_payload, {
                            "device_imei": {"deviceimei", "imei"},
                            "cuid": {"cuid"},
                            "match_type": {"matchtype"},
                            "market_type_finally": {"markettypefinally"},
                        })
                        for field, entries in found.items():
                            paths = sorted({where for _, where in entries})
                            print("【EB】[EB_BROWSER_RUNTIME_DIAG]", f"page={page_index}",
                                  f"field={field}", f"found={bool(entries)}",
                                  f"paths={json.dumps(paths, ensure_ascii=False)}", flush=True)
                except Exception as exc:
                    print("【EB】[EB_BROWSER_RUNTIME_DIAG]", f"page={page_index}",
                          f"storage_read_error={type(exc).__name__}", flush=True)
        return _resolve_runtime_fields(sources, event_id, market=market, group=group)

    def set_ignored_event_ids(self, event_ids):
        """Exclude events whose confirmed successful-bet count reached the configured cap."""
        self._ignored_event_ids = {str(value) for value in (event_ids or set()) if str(value).strip()}

    def discover_nba_events(self, max_pages=20):
        """Fetch current EB event pages and return only explicitly identified NBA events."""
        target_paths = (
            "/yewu11/v1/m/matchesPagePB",
            "/yewu11/v1/m/noLiveMatchesPagePB",
        )
        if not getattr(self, "_nba_list_template", None):
            if not self._nba_capture_confirmed:
                _console_prompt(
                    "【页面操作 1】请在 OP7 浏览器进入「篮球 / NBA 正规赛事」列表。"
                    "确认列表页面已打开后，回到终端按 Enter；脚本收到确认后才开始捕获列表请求。"
                )
            self._nba_capture_confirmed = False
    
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
    
            wait_seconds = 30.0
            _console_prompt(
                "【页面操作 2｜刷新并捕获请求】列表页已在上一步确认打开。现在只需刷新当前 NBA 列表页；"
                "刷新完成后回到终端按 Enter，脚本才开始等待并捕获本次列表请求。"
            )
            _console_stage(
                f"正在监听 NBA 列表请求，最长等待 {int(wait_seconds)} 秒；请勿关闭浏览器页面。"
            )
            # Keep listening for the full window so late pagination/refresh requests
            # are included; the newest captured request is selected after the window.
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
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
            _console_stage(
                f"已捕获 NBA 列表请求：{path.rsplit('/', 1)[-1]}；正在读取当日赛事分页。"
            )
            capture_status = live.eb_nba_capture_status()
            print("\n=== EB NBA 页面监听状态 ===")
            for endpoint, state in capture_status.items():
                short_name = endpoint.rsplit("/", 1)[-1]
                mark = "✓" if state["captured"] else ("已触发/未捕获" if state["seen"] else "未触发")
                print(f"[{mark}] {short_name} method={state['method'] or '-'}")
            print("页面状态：已确认进入 EB NBA 列表页")
            print("检测状态：已捕获本轮最新列表请求")
    
            self._nba_list_template = template
            self._nba_list_path = path
        else:
            # Subsequent polling rounds reuse the authenticated browser-captured
            # request shape but fetch a fresh current-day NBA list from EB.
            path = self._nba_list_path
            template = self._nba_list_template
            try:
                if template.get("method") == "GET":
                    body = dict(parse_qsl(urlsplit(template.get("url", "")).query, keep_blank_values=True))
                else:
                    body = json.loads(template.get("post_data") or "{}")
            except Exception:
                raise RuntimeError("无法解析已捕获的 EB 赛事列表请求参数") from None
            if not isinstance(body, dict):
                raise RuntimeError("EB 赛事列表请求参数不是对象")
            body.setdefault("cpn", 1)
            body.setdefault("cps", 50)
            origin = live.CAPTURED_ORIGINS.get("eb", live.SITES["EB_ORIGIN"])
            response = live.request_json(
                self.op7, origin + path, template.get("method", "POST"),
                live.headers_env("OP7_READONLY_HEADERS_JSON"),
                params=body if template.get("method") == "GET" else None,
                body=body if template.get("method") == "POST" else None,
                timeout_ms=self.timeout * 1000, family="eb",
            )
            selected_record = {
                "request_template": template,
                "response": response,
                "captured_at": time.time(),
            }
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
                "【EB】[EB_NBA_PAGE_DIAG]",
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
        eligible_rows = [
            event for event_id, event in rows_by_id.items()
            if event_id not in self._ignored_event_ids
        ]
        self._set_pending_event_snapshot("EB", eligible_rows)
        if self._ignored_event_ids:
            print(
                "[EVENT_LIMIT_FILTER]",
                f"listed_nba_events={len(rows_by_id)}",
                f"ignored={len(rows_by_id) - len(eligible_rows)}",
                f"remaining={len(eligible_rows)}",
                flush=True,
            )
        return eligible_rows

    def _print_event_match_diagnostics(self, eb_events):
        """Print only the EB/Pingbo NBA home/away team pairs after a fully unmatched round."""
        print("【EB】[NBA赛事匹配汇总] 本轮 EB NBA 主客场球队：", flush=True)
        for event in eb_events:
            home, away = _split_eb_teams(event)
            print(f"【EB】{home or '?'} vs {away or '?'}", flush=True)
        path = getattr(self, "_pingbo_active_feed_path", None)
        record = self._pingbo_feed_responses.get(path, {}) if path else {}
        payload = self._pingbo_market_payload or record.get("payload")
        print("【pingBo】[NBA赛事匹配汇总] 本轮 Pingbo NBA 主客场球队：", flush=True)
        for item in mapper.extract_pingbo_events(payload) if payload is not None else []:
            print(f"【pingBo】{item.get('home_zh') or item.get('home') or '?'} vs "
                  f"{item.get('away_zh') or item.get('away') or '?'}", flush=True)

    def discover_nba_total_selections(self):
        """Discover NBA events and report safe schema diagnostics if totals cannot be resolved."""
        events = self.discover_nba_events()
        if not events:
            raise RuntimeError("当前列表没有筛选到 NBA 正规赛事，停止盘口请求")
        self.ensure_pingbo_live_capture()
        feed_path = getattr(self, "_pingbo_active_feed_path", None)
        feed_record = self._pingbo_feed_responses.get(feed_path, {}) if feed_path else {}
        feed_payload = self._pingbo_market_payload or feed_record.get("payload")
        self._round_pingbo_matched_event_ids = set()
        if feed_payload is not None:
            matched_events = []
            for event in events:
                try:
                    _find_pingbo_event(feed_payload, event)
                    matched_events.append(event)
                    self._round_pingbo_matched_event_ids.add(str(event.get("mid") or ""))
                except RuntimeError:
                    continue
            if not matched_events:
                self._print_event_match_diagnostics(events)
                print("【执行节点】本轮 EB NBA 主队/客队或赛事时间与 Pingbo 均未匹配，跳过本轮盘口详情请求。", flush=True)
                return []
            events = matched_events
        if not self._odds_request_template:
            _console_prompt(
                "【页面操作 3】请在 OP7 浏览器打开任意一场 NBA 正规赛事详情，并等待全场大小盘口赔率加载完成。"
                "确认盘口已经显示后，回到终端按 Enter；脚本收到确认后才读取真实赔率请求参数。"
            )
            for page in list(self.op7.pages):
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
        if not self._odds_request_template:
            raise RuntimeError(
                "未捕获 getMatchOddsInfoPB 真实请求；未使用固定 cuid，停止盘口发现"
            )
        if not self._manual_runtime_initialized:
            self._manual_bet_runtime_requests.clear()
            _console_prompt(
                "【页面操作 4｜仅首次初始化】请在 OP7 浏览器选择一场 NBA 全场大小盘口，"
                "按当前测试流程完成一次模拟投注提交。页面请求会正常发送。"
                "完成后回到终端按 Enter；脚本只采集运行参数，后续轮次不会再次要求投注确认。"
            )
            for page in list(self.op7.pages):
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    pass
            if not self._manual_bet_runtime_requests:
                raise RuntimeError("未捕获到初始化用的人工投注接口请求；请确认页面已正常提交")
            self._manual_runtime_initialized = True
            _console_stage("运行参数已完成首次初始化；后续轮次直接复用。")
        else:
            _console_stage("运行参数已初始化，跳过页面操作 4。")

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

        for event_index, event in enumerate(events, 1):
            add_field_names("event_field_names", event)

            # Print identifying information from the already-captured list row
            # before requesting detail, so a detail API error still identifies
            # the fixture without relying on the detail response.
            def first_text(*keys):
                for key in keys:
                    value = event.get(key)
                    if value not in (None, "") and not isinstance(value, (dict, list)):
                        return str(value).strip()
                return ""

            home_name = first_text("mhn", "homeName", "homeTeamName", "home", "hn")
            away_name = first_text("man", "awayName", "awayTeamName", "away", "an")
            league_name = first_text("tnjc", "tn", "leagueName", "league_name") or "NBA"
            event_name = first_text("mn", "matchName", "match_name", "eventName", "event_name")
            if not event_name and home_name and away_name:
                event_name = f"{home_name} vs {away_name}"
            if not event_name:
                event_name = "赛事名称未在列表字段中提供"

            _console_stage(
                f"赛事详情读取 {event_index}/{len(events)}：{event_name}（{home_name or '?'} vs {away_name or '?'}），"
                "正在获取赛事详情和全场大小盘口。"
            )
            detail = self.fetch_match_detail(event)
            diagnostics["details_fetched"] += 1
            odds = self.fetch_match_odds(event)
            odds_data = odds.get("data") if isinstance(odds, dict) else None
            markets = self._detail_market_lists(odds_data)
            if markets:
                diagnostics["details_with_hps"] += 1
            diagnostics["markets_total"] += len(markets)
            for market in markets:
                top_key = str(market.get("topKey") or market.get("hpid") or "")
                market_name = str(market.get("hpn") or "")
                market_id = top_key or "<empty>"
                diagnostics["market_ids"][market_id] = (
                    diagnostics["market_ids"].get(market_id, 0) + 1
                )
                add_field_names("market_field_names", market)
                if len(diagnostics["market_name_samples"]) < 12:
                    name = market.get("hpn") or market.get("name") or market.get("hn")
                    if name not in (None, ""):
                        diagnostics["market_name_samples"].append(str(name)[:100])
                target_market = (
                    (market_name == "全场大小" and top_key == "38")
                    or (market_name == "全场大小-附加盘" and top_key == "38-1")
                )
                if not target_market:
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
            if not self._round_pingbo_matched_event_ids:
                self._print_event_match_diagnostics(events)
            print("\\n=== EB NBA 大小分字段诊断（仅字段名和计数，不含请求凭证或原始响应） ===")
            print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
            if not events:
                reason = "没有从赛事列表筛选到 NBA 正规赛事；请核对赛事列表响应中的联赛字段"
            elif diagnostics["markets_total"] == 0:
                reason = "解码后的 getMatchOddsInfoPB 响应中没有识别到盘口记录（hpn/topKey）"
            elif diagnostics["markets_hpid_38"] == 0:
                reason = "没有找到目标盘口：全场大小/topKey=38 或 全场大小-附加盘/topKey=38-1"
            elif diagnostics["over_under_options"] == 0:
                reason = "hpid=38 盘口中没有 ot=over/under 的选项"
            else:
                reason = "找到了大小分选项，但缺少构建选项所需字段；请查看 missing_required_fields"
            raise RuntimeError("实时 NBA 全场大小分选项未能完整提取：" + reason)
        return selections

    def confirm_and_wait_eb_retry(self, endpoint):
        short_path = str(endpoint).rsplit("/", 1)[-1]
        _console_prompt(
            "【人工确认｜EB 数据未返回】接口 " + short_path +
            " 未取得有效数据。请在 OP7 浏览器确认对应页面已加载，必要时刷新或重新打开赛事；"
            "完成后回到终端按 Enter，脚本才会重新请求一次。"
        )
        for page in list(self.op7.pages):
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass

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

    def ensure_pingbo_live_capture(self):
        """Reuse a successful initialization; prompt only until the first valid feed arrives."""
        paths = ("/sports-service/sv/mobile/odds-live",
                 "/sports-service/sv/mobile/odds-today")
        def valid_feed(path):
            req = live.CAPTURED_REQUESTS.get(("pingbo", path))
            record = self._pingbo_feed_responses.get(path)
            return bool(req and record and record.get("event_count", 0) > 0
                        and req.get("url") and record.get("request_params", {}).get("sportId") == "4"
                        and record.get("request_params", {}).get("leagueIds") == "487")
        valid_paths = [path for path in paths if valid_feed(path)]
        valid_path = max(
            valid_paths,
            key=lambda path: (
                live.CAPTURED_REQUESTS.get(("pingbo", path), {}).get("captured_at_monotonic", 0),
                self._pingbo_feed_responses.get(path, {}).get("captured_at", 0),
            ),
            default=None,
        )
        if valid_path:
            self._pingbo_active_feed_path = valid_path
            self._pingbo_capture_confirmed = True
            self._pingbo_market_payload = self._pingbo_feed_responses[valid_path]["payload"]
            return
        if (self._pingbo_capture_confirmed and getattr(self, "_pingbo_active_feed_path", None)
                and self._pingbo_market_payload is not None):
            return
        _console_prompt(
            "【页面操作｜Pingbo 接口初始化】尚未取得有效的 Pingbo NBA 数据。"
            "监控接口为 odds-live 或 odds-today，且请求 parameters 必须同时包含 sportId=4、leagueIds=487。"
            "请在 Pingbo 浏览器进入篮球 NBA 赛事列表并手动刷新，等待赛事数据加载完成；"
            "然后回到终端按 Enter。脚本会重新捕获符合条件的请求和响应，成功前不会继续赔率校验。"
        )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            for page in list(self.pingbo.pages):
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    pass
            valid_path = next((candidate for candidate in paths if valid_feed(candidate)), None)
            if valid_path:
                break
        if not valid_path:
            raise RuntimeError("Pingbo NBA 初始化失败：刷新后仍未捕获到 parameters 同时包含 sportId=4、leagueIds=487 的 odds-live/odds-today 有效赛事响应；停止本轮")
        self._pingbo_capture_confirmed = True
        self._pingbo_active_feed_path = valid_path
        record = self._pingbo_feed_responses[valid_path]
        print("【pingBo】[INIT] NBA 接口请求与响应已捕获",
              f"endpoint={valid_path.rsplit('/', 1)[-1]}",
              f"nba_event_count={record['event_count']}", flush=True)

    def refresh_cross_odds(self, selection):
        """Fetch both books in one synchronous window using the browser's logged-in sessions."""
        event = selection["event"]
        line = selection["group"].get("hv")
        if line in (None, ""):
            raise RuntimeError("selection has no current EB total line")
        eb_origin = live.CAPTURED_ORIGINS.get("eb", live.SITES["EB_ORIGIN"])
        pb_origin = live.CAPTURED_ORIGINS.get("pingbo", live.SITES["PINGBO_ORIGIN"])
        eb_path = "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB"
        self.ensure_pingbo_live_capture()
        pb_path = getattr(self, "_pingbo_active_feed_path", None)
        if pb_path not in ("/sports-service/sv/mobile/odds-live", "/sports-service/sv/mobile/odds-today"):
            raise RuntimeError("Pingbo NBA feed path missing after initialization")
        started = time.monotonic()
        cfg_path = Path(__file__).with_name("refresh_config.json")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        policy_path = Path(__file__).with_name("refresh_policy.json")
        policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else {}
        intervals = policy.get("intervals_seconds", {})
        self._pingbo_refresh_interval_seconds = float(
            intervals.get("EB_Pingbo_shared_interval", 20)
        )
        eb_params = dict((cfg.get("op7") or {}).get("odds_params") or {})
        eb_params["mid"] = str(event["mid"])
        captured_pb = live.CAPTURED_REQUESTS.get(("pingbo", pb_path)) or {}
        pb_query = dict(parse_qsl(urlsplit(captured_pb.get("url", "")).query, keep_blank_values=True))
        pb_params = {
            "sportId": pb_query.get("sportId", "4"),
            "leagueIds": pb_query.get("leagueIds", "487"),
            "periodNumber": pb_query.get("periodNumber", "0"),
            "periodNumbers": pb_query.get("periodNumbers", "-1"),
            "betTypes": pb_query.get("betTypes", "HDP,OU,ML_1X2"),
            "resultingUnit": pb_query.get("resultingUnit", ""),
            "locale": pb_query.get("locale", "zh_CN"),
            "version": pb_query.get("version", "0"),
            "specialName": pb_query.get("specialName", ""),
            "_": str(int(time.time() * 1000)),
            "withCredentials": pb_query.get("withCredentials", "true"),
        }
        print("【EB】 正在刷新当前赛事盘口赔率。", flush=True)
        eb = live.request_json(
            self.op7, eb_origin + eb_path, "GET",
            live.headers_env("OP7_READONLY_HEADERS_JSON"),
            params=eb_params, timeout_ms=self.timeout * 1000, family="eb")
        now = time.monotonic()
        refresh_pingbo = (
            self._pingbo_market_payload is None
            or now - self._pingbo_last_refresh_monotonic >= self._pingbo_refresh_interval_seconds
        )
        if refresh_pingbo:
            # Use the latest browser-observed odds-today/odds-live request template
            # at the shared EB/Pingbo refresh clock, then cache its NBA response.
            latest_template = live.CAPTURED_REQUESTS.get(("pingbo", pb_path)) or captured_pb
            latest_query = dict(parse_qsl(urlsplit(latest_template.get("url", "")).query, keep_blank_values=True))
            for key in ("sportId", "leagueIds", "periodNumber", "periodNumbers",
                        "betTypes", "resultingUnit", "locale", "version", "specialName",
                        "withCredentials"):
                if key in latest_query:
                    pb_params[key] = latest_query[key]
            print("【pingBo】[赔率刷新] 共享时钟到达，读取浏览器最近一次 NBA feed 请求。", flush=True)
            pb = live.request_json(
                self.pingbo, pb_origin + pb_path, "GET",
                live.headers_env("PINGBO_READONLY_HEADERS_JSON"),
                params=pb_params, timeout_ms=self.timeout * 1000, family="pingbo")
            pb = live.unwrap(pb)
            self._pingbo_market_payload = pb
            self._pingbo_last_refresh_monotonic = time.monotonic()
        else:
            pb = self._pingbo_market_payload
            print("【pingBo】[赔率缓存] 共享刷新周期内复用最近一次 NBA feed 响应。",
                  f"剩余刷新秒数={max(0, self._pingbo_refresh_interval_seconds - (now - self._pingbo_last_refresh_monotonic)):.1f}",
                  flush=True)
        pb_events = mapper.extract_pingbo_events(pb)
        print("【pingBo】 odds-live 响应解析完成", f"nba_event_count={len(pb_events)}", flush=True)
        if not pb_events:
            raise RuntimeError("Pingbo odds-live 响应中没有解析到 NBA 赛事；停止赛事映射")
        eb_totals = _find_eb_totals(eb, event["mid"], line)
        pb_event = _find_pingbo_event(pb, event)
        self._round_pingbo_matched_event_ids.add(str(event.get("mid") or event.get("event_id") or ""))
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
