#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive, read-only NBA totals monitor using a visible Pingbo browser.

Commands: 开始 / 结束 / 退出
The browser remains on the user's current list page. NBA list/detail requests
are made through the same Playwright BrowserContext (shared local cookies).
No bet/order/wallet endpoints are called.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from pingbo_nba_detail import (
    is_nba_league, event_identity, extract_full_game_totals,
)
from eb_pingbo_nba_validator import get_eb_under, run_validation

ROOT = Path(__file__).resolve().parent
ENDPOINTS_FILE = ROOT / "endpoints.json"
PROFILE_DIR = ROOT / "browser_profiles" / "pingbo"
SNAPSHOT_DIR = ROOT / "live_snapshots"
POLL_SECONDS = 20
PINGBO_PATHS = {
    "/sports-service/sv/mobile/sports",
    "/sports-service/sv/mobile/leagues",
    "/sports-service/sv/mobile/market-filters",
    "/sports-service/sv/mobile/odds-live",
    "/sports-service/sv/mobile/odds-today",
    "/sports-service/sv/mobile/league-events",
    "/sports-service/sv/mobile/event",
}
# API signatures observed in the supplied HAR files. Website assets are not
# accepted as evidence of an API host.
EB_PATHS = {
    "/yewu11/v1/m/matchesPagePB",
    "/yewu11/v1/m/matchDetail/getMatchOddsInfoPB",
    "/yewu11/v1/m/matchDetail/getMatchDetailPB",
    "/yewu11/v1/m/getMatchBaseInfoByMidsPB",
    "/yewu11/v1/m/noLiveMatchesPagePB",
}
COMMON_PATHS = {
    "/api/match/sport/listbytype",
    "/api/match/live/get100Refactoring",
}
CAPTURED_HEADERS: dict[str, dict[str, str]] = {}


def capture_browser_request(request) -> None:
    try:
        u = urlsplit(request.url)
        if u.path in PINGBO_PATHS:
            family = "pingbo"
        elif u.path in EB_PATHS:
            family = "eb"
        elif u.path in COMMON_PATHS:
            family = "common"
        else:
            return
        blocked = {"host", "content-length", "cookie", "accept-encoding",
                   "connection", "sec-fetch-site", "sec-fetch-mode",
                   "sec-fetch-dest", "priority"}
        CAPTURED_HEADERS[family] = {
            k: v for k, v in request.headers.items()
            if k.lower() not in blocked and not k.lower().startswith("sec-ch-")
        }
    except Exception:
        return


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_endpoints() -> dict:
    return json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))


def save_endpoints(cfg: dict) -> None:
    tmp = ENDPOINTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ENDPOINTS_FILE)


def current_visible_page(context):
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        return context.new_page()
    for page in reversed(pages):
        try:
            if page.evaluate("document.visibilityState") == "visible":
                return page
        except Exception:
            pass
    return pages[-1]


def discover_api_origins(page) -> dict[str, str]:
    """Map service family to origins only when HAR-known API paths were observed."""
    resources = page.evaluate("""() => performance.getEntriesByType('resource')
      .map(x => x.name).filter(x => /^https?:/i.test(x))""")
    page_url = page.url
    found: dict[str, dict[str, int]] = {"pingbo": {}, "eb": {}, "common": {}}
    for raw in resources:
        try:
            u = urlsplit(raw)
            if u.scheme != "https":
                continue
            if u.path in PINGBO_PATHS:
                family = "pingbo"
            elif u.path in EB_PATHS:
                family = "eb"
            elif u.path in COMMON_PATHS:
                family = "common"
            else:
                continue
            origin = f"{u.scheme}://{u.netloc}"
            found[family][origin] = found[family].get(origin, 0) + 1
        except Exception:
            continue

    # The current page origin is not blindly assumed to be an API host. It is
    # accepted only if a HAR-known API path from that service was observed.
    cfg = load_endpoints()
    changed = {}
    for family, origins in found.items():
        if not origins:
            continue
        origin, hits = sorted(origins.items(), key=lambda item: item[1], reverse=True)[0]
        host = urlsplit(origin).hostname
        api = cfg["apis"][family]
        old = api.get("base_url") or api.get("nba_read_base_url")
        if family == "eb":
            api["nba_read_base_url"] = origin
            api["base_url"] = origin
        else:
            api["base_url"] = origin
        hosts = list(dict.fromkeys((api.get("hosts") or []) + [host]))
        api["hosts"] = hosts
        changed[family] = {"old": old, "new": origin, "observed_path_hits": hits}
    if changed:
        cfg.setdefault("runtime_discovery", {})
        cfg["runtime_discovery"] = {
            "updated_at_utc": utc_now(),
            "page_url": page_url,
            "services": changed,
            "source": "browser performance resource URLs matched against supplied HAR endpoint signatures",
        }
        save_endpoints(cfg)
    return changed


def api_get(context, origin: str, path: str, params: dict, referer: str,
            captured_headers: dict[str, str] | None = None, timeout_ms: int = 15000):
    if path not in PINGBO_PATHS:
        raise ValueError(f"拒绝调用未在 HAR 白名单中的接口：{path}")
    headers = dict(captured_headers or {})
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers["Referer"] = referer
    headers.setdefault("Origin", f"{urlsplit(referer).scheme}://{urlsplit(referer).netloc}")
    response = context.request.get(
        origin.rstrip("/") + path,
        params=params,
        headers=headers,
        timeout=timeout_ms,
    )
    if response.status in (401, 403):
        raise RuntimeError(f"接口鉴权失败 HTTP {response.status}: {path}；请在已打开的浏览器中重新登录")
    if not response.ok:
        raise RuntimeError(f"接口请求失败 HTTP {response.status}: {path}")
    return response.json()


def extract_list_events(data):
    if not isinstance(data, dict):
        raise ValueError("NBA赛事列表接口返回格式不是对象")
    rows = []
    for league in data.get("leagues") or []:
        if not isinstance(league, dict) or not is_nba_league(league):
            continue
        for event in league.get("events") or []:
            if isinstance(event, dict) and event.get("id"):
                rows.append((event, league))
    return rows


def fetch_nba_details(context, origin: str, referer: str, captured_headers: dict[str, str] | None = None) -> dict:
    common = {"locale": "zh_CN", "withCredentials": "true"}
    params = {
        **common, "sportId": 4, "periodNumber": 0, "periodNumbers": -1,
        "betTypes": "HDP,OU,ML_1X2", "version": 0,
        "_": int(time.time() * 1000),
    }
    list_data = api_get(context, origin, "/sports-service/sv/mobile/odds-today",
                        params, referer, captured_headers)
    listed = extract_list_events(list_data)
    # Live NBA games are a separate captured list endpoint. Merge by event id.
    live_params = dict(params)
    live_params["_"] = int(time.time() * 1000)
    try:
        live_data = api_get(context, origin, "/sports-service/sv/mobile/odds-live",
                             live_params, referer, captured_headers)
        known = {str(ev.get("id")) for ev, _ in listed}
        for ev, league in extract_list_events(live_data):
            if str(ev.get("id")) not in known:
                listed.append((ev, league))
                known.add(str(ev.get("id")))
    except Exception as exc:
        print(f"提示：滚球列表补充失败（不影响今日列表）：{exc}")

    result_events, errors = [], []
    for event, league in listed:
        event_id = str(event["id"])
        detail_params = {
            **common, "eventId": event_id, "version": 0,
            "specialVersion": 0, "_": int(time.time() * 1000),
        }
        try:
            detail = api_get(context, origin, "/sports-service/sv/mobile/event",
                             detail_params, referer, captured_headers)
            normal = detail.get("normal") if isinstance(detail, dict) else None
            source = "EVENT_DETAIL"
            if not isinstance(normal, dict):
                normal = event
                source = "LIST_FALLBACK"
            totals = extract_full_game_totals(normal, league, source)
            result_events.append({
                **event_identity(event, league),
                "detail_source": source,
                "full_game_totals": totals,
            })
        except Exception as exc:
            errors.append({"event_id": event_id, "error": str(exc)})
    return {
        "generated_at_utc": utc_now(),
        "source": origin,
        "nba_events_in_list": len(listed),
        "events": result_events,
        "errors": errors,
    }


def print_results(pb: dict) -> None:
    print("\n" + "=" * 104)
    print(f"[{pb['generated_at_utc']}] Pingbo NBA赛事 {pb['nba_events_in_list']} 场；详情成功 {len(pb['events'])} 场；详情失败 {len(pb['errors'])} 场")
    for event in pb["events"]:
        print(f"\n{event.get('home_team')} vs {event.get('away_team')}  |  {event.get('league')}  |  ID={event.get('event_id')}  |  {event.get('detail_source')}")
        if not event["full_game_totals"]:
            print("  全场大小分：无可用盘口")
        for row in event["full_game_totals"]:
            print(f"  大 {row['points']} @ {row.get('over_odds_decimal') or '-'}  |  小 {row['points']} @ {row.get('under_odds_decimal') or '-'}")
    if pb["errors"]:
        print("\n详情接口错误：")
        for error in pb["errors"]:
            print(f"  {error['event_id']}: {error['error']}")

    try:
        eb_rows, _ = get_eb_under(SNAPSHOT_DIR)
        validation = run_validation(eb_rows, pb, __import__("decimal").Decimal("0"))
        out_json = ROOT / "eb_pingbo_nba_validation.json"
        out_json.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nEB Under × Pingbo Over 校验：通过 {validation['summary']['rule_pass']} / 未通过 {validation['summary']['rule_fail']} / 未匹配 {validation['summary']['unmatched']}")
        for row in validation["results"]:
            calc = row.get("calculation") or {}
            print(f"  {row.get('home_team')} vs {row.get('away_team')} | EB 小{row.get('points')} @{row.get('odds_decimal')} | PB 大{row.get('pingbo_points') or row.get('points')} @{row.get('pingbo_odds_decimal','-')} | 合并概率 {calc.get('combined_implied_probability','-')} | Edge {calc.get('edge_percent','-')} | {row['rule_status']}")
        print(f"校验JSON：{out_json}")
    except FileNotFoundError:
        print("\n尚未发现 EB_events_latest.json / EB_markets_latest.json；本轮只输出 Pingbo NBA 详情。请先启动现有 EB 只读刷新程序。")
    except Exception as exc:
        print(f"\nEB/Pingbo联合校验暂不可用：{type(exc).__name__}: {exc}")


def monitor_loop(context, page, stop_event):
    last_page_url = None
    while not stop_event.is_set():
        try:
            if page.is_closed():
                raise RuntimeError("当前浏览器标签页已关闭")
            page_url = page.url
            if page_url != last_page_url:
                print(f"当前浏览器页面：{page_url}")
                last_page_url = page_url
            changed = discover_api_origins(page)
            if changed:
                for name, info in changed.items():
                    print(f"API域名自动更新 [{name}]：{info['old']} -> {info['new']}（命中 {info['observed_path_hits']} 个抓包接口请求）")
            cfg = load_endpoints()
            origin = cfg["apis"]["pingbo"]["base_url"].rstrip("/")
            host = urlsplit(origin).hostname
            # Require this tab to be on Pingbo's website; do not silently use
            # an unrelated browser tab or an unobserved host.
            if not page_url.startswith("https://"):
                raise RuntimeError("当前页面不是 HTTPS，请切换到 Pingbo 篮球赛事列表页")
            pb_hosts = set(cfg["apis"]["pingbo"].get("hosts", []))
            page_host = urlsplit(page_url).hostname
            if page_host not in pb_hosts and not changed.get("pingbo"):
                raise RuntimeError(f"当前页面域名 {page_host} 未识别为 Pingbo；请先打开 Pingbo 赛事列表页")
            if host not in pb_hosts:
                raise RuntimeError(f"API域名 {host} 未通过抓包接口域名校验")
            pb = fetch_nba_details(context, origin, page_url)
            (ROOT / "pingbo_nba_live_details.json").write_text(
                json.dumps(pb, ensure_ascii=False, indent=2), encoding="utf-8")
            print_results(pb)
        except Exception as exc:
            print(f"\n[本轮扫描失败] {type(exc).__name__}: {exc}")
            print("提示：确认浏览器停留在 Pingbo 页面、已登录，并且页面已加载赛事列表。")
        stop_event.wait(POLL_SECONDS)


def main():
    import queue

    print("NBA盘口实时检索（只读）")
    print("浏览器将保持可见；请在浏览器中登录并打开 Pingbo 篮球赛事列表页。")
    print("命令：开始 / 结束 / 退出")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=False,
        args=["--disable-dev-shm-usage"],
        viewport={"width": 1440, "height": 1000},
    )
    page = current_visible_page(context)
    page.on("request", capture_browser_request)
    commands = queue.Queue()

    def read_commands():
        while True:
            try:
                command = input("\\n请输入命令 [开始/结束/退出]：").strip()
            except EOFError:
                command = "退出"
            commands.put(command)
            if command in {"退出", "exit", "quit", "q"}:
                return

    input_thread = threading.Thread(target=read_commands, daemon=True)
    input_thread.start()
    try:
        try:
            if not page.url or page.url == "about:blank":
                page.goto("https://www.pin135.com/", wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeoutError:
            print("浏览器页面加载等待超时；请检查页面后继续。")
        print(f"浏览器已启动，当前页面：{page.url}")
        print("如未登录，请先在浏览器完成登录并进入篮球赛事列表；然后在终端输入“开始”。")
        running = False
        next_scan = 0.0
        while True:
            try:
                command = commands.get(timeout=0.5)
            except queue.Empty:
                command = None
            if command in {"开始", "start", "START"}:
                if running:
                    print("监控已经在运行。")
                else:
                    running = True
                    next_scan = 0.0
                    print("已开始实时检索。浏览器不会跳转；输入“结束”停止。")
            elif command in {"结束", "停止", "stop", "STOP"}:
                running = False
                print("监控已结束；浏览器仍保持打开。")
            elif command in {"退出", "exit", "quit", "q"}:
                print("正在退出监控程序。")
                break
            elif command is not None:
                print("无法识别命令，请输入：开始、结束或退出")

            if running and time.monotonic() >= next_scan:
                try:
                    if page.is_closed():
                        raise RuntimeError("当前浏览器标签页已关闭")
                    page_url = page.url
                    changed = discover_api_origins(page)
                    if changed:
                        for name, info in changed.items():
                            print(f"API域名自动更新 [{name}]：{info['old']} -> {info['new']}（命中 {info['observed_path_hits']} 个抓包接口请求）")
                    cfg = load_endpoints()
                    origin = cfg["apis"]["pingbo"]["base_url"].rstrip("/")
                    host = urlsplit(origin).hostname
                    pb_hosts = set(cfg["apis"]["pingbo"].get("hosts", []))
                    page_host = urlsplit(page_url).hostname
                    if not page_url.startswith("https://"):
                        raise RuntimeError("当前页面不是 HTTPS，请切换到 Pingbo 篮球赛事列表页")
                    if page_host not in pb_hosts and not changed.get("pingbo"):
                        raise RuntimeError(f"当前页面域名 {page_host} 未识别为 Pingbo；请先打开 Pingbo 赛事列表页")
                    if host not in pb_hosts:
                        raise RuntimeError(f"API域名 {host} 未通过抓包接口域名校验")
                    request_headers = CAPTURED_HEADERS.get("pingbo")
                    if not request_headers:
                        raise RuntimeError("尚未捕获到 Pingbo 列表接口请求头；请在浏览器刷新一次篮球赛事列表页，再输入“开始”。")
                    pb = fetch_nba_details(context, origin, page_url, request_headers)
                    (ROOT / "pingbo_nba_live_details.json").write_text(
                        json.dumps(pb, ensure_ascii=False, indent=2), encoding="utf-8")
                    print_results(pb)
                except Exception as exc:
                    print(f"\\n[本轮扫描失败] {type(exc).__name__}: {exc}")
                    print("提示：确认浏览器停留在 Pingbo 页面、已登录，并且页面已加载赛事列表。")
                next_scan = time.monotonic() + POLL_SECONDS
    finally:
        context.close()
        playwright.stop()


if __name__ == "__main__":
    main()
