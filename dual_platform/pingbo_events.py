"""Fetch Pingbo events through the existing allow-listed compact/events API.

Reuses the endpoint, request guard, response decoder, and event parser from
op7_fb_eb_pingbo_live_refresh.py. Requests are read-only and use the logged-in
Playwright BrowserContext so its in-memory session cookies are shared.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import op7_fb_eb_pingbo_live_refresh as refresh
import op7_fb_eb_venue_mapper as mapper


def load_pingbo_params(config_path: str | Path = "refresh_config.json") -> dict:
    path = Path(config_path)
    if not path.exists():
        path = Path("refresh_config.example.json")
    if not path.exists():
        raise FileNotFoundError(
            "找不到 refresh_config.json 或 refresh_config.example.json；"
            "请从示例配置创建本地 refresh_config.json"
        )
    config = json.loads(path.read_text(encoding="utf-8"))
    params = config.get("part567", {}).get("events_params")
    if not isinstance(params, dict):
        raise ValueError("配置缺少 part567.events_params 对象")
    return dict(params)


def fetch_pingbo_events(browser_context: Any, *, config_path: str | Path = "refresh_config.json") -> dict:
    """Call the existing Pingbo event-list endpoint using the active browser session."""
    params = load_pingbo_params(config_path)
    params.setdefault("_", str(int(__import__("time").time() * 1000)))
    headers = refresh.headers_env("PINGBO_READONLY_HEADERS_JSON")
    response = refresh.request_json(
        browser_context,
        refresh.SITES["PINGBO_ORIGIN"] + "/sports-service/sv/compact/events",
        "GET",
        headers,
        params=params,
        timeout_ms=15000,
    )
    rows = refresh.validate_event_list(mapper.extract_pingbo_events(response), "Pingbo")
    observed = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    events = []
    for row in rows:
        start_ms = row.get("start_time_ms")
        try:
            start_iso = datetime.fromtimestamp(int(start_ms) / 1000, timezone.utc).isoformat() if start_ms is not None else None
        except (ValueError, TypeError, OverflowError, OSError):
            start_iso = None
        events.append({
            "platform": "PINGBO",
            "event_id": str(row["event_id"]),
            "home": str(row["home"]).strip(),
            "away": str(row["away"]).strip(),
            "start_time_ms": start_ms,
            "start_time_utc": start_iso,
            "raw_status": row.get("raw_status"),
            "market_keys": row.get("market_keys", []),
            "observed_at_utc": observed,
        })
    if not events:
        raise ValueError("Pingbo 接口没有返回可用赛事；不使用空列表覆盖已有快照")
    return {
        "source": "existing_pingbo_compact_events_api",
        "endpoint": "/sports-service/sv/compact/events",
        "observed_at_utc": observed,
        "count": len(events),
        "events": events,
    }
