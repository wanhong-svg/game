"""Shared event/market normalization, comparison, and SQLite audit storage.

This module is deliberately read-only with respect to betting: it never places,
submits, edits, or cancels wagers.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

ALLOWED_MARKETS = {"FULL_TIME_MONEYLINE", "FULL_TIME_TOTAL", "FULL_TIME_HANDICAP"}
MARKET_LABELS = {
    "FULL_TIME_MONEYLINE": "全场独赢",
    "FULL_TIME_TOTAL": "全场大小",
    "FULL_TIME_HANDICAP": "全场让球",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def decimal_value(value: object) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 1 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Event:
    platform: str
    event_id: str
    home: str
    away: str
    start_time_utc: str
    competition: str = ""
    raw_name: str = ""


@dataclass(frozen=True)
class EventMatch:
    op7_event_id: str
    pingbo_event_id: str
    confidence: float
    start_delta_seconds: float
    orientation: str
    status: str


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def match_events_unique(
    op7_events: Iterable[Event],
    pingbo_events: Iterable[Event],
    *,
    start_tolerance_seconds: int = 300,
) -> list[EventMatch]:
    """Return only mutual one-to-one exact team matches; ambiguity fails closed."""
    left, right = list(op7_events), list(pingbo_events)
    candidates: dict[tuple[int, int], tuple[float, str]] = {}
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            if not all((a.home, a.away, b.home, b.away, a.start_time_utc, b.start_time_utc)):
                continue
            try:
                delta = abs((_parse_time(a.start_time_utc) - _parse_time(b.start_time_utc)).total_seconds())
            except (ValueError, TypeError):
                continue
            if delta > start_tolerance_seconds:
                continue
            ah, aa = normalize_name(a.home), normalize_name(a.away)
            bh, ba = normalize_name(b.home), normalize_name(b.away)
            if ah and aa and ah == bh and aa == ba:
                candidates[(i, j)] = (delta, "SAME")
            elif ah and aa and ah == ba and aa == bh:
                candidates[(i, j)] = (delta, "REVERSED")

    matches = []
    for (i, j), (delta, orientation) in candidates.items():
        if sum(1 for ii, _ in candidates if ii == i) != 1:
            continue
        if sum(1 for _, jj in candidates if jj == j) != 1:
            continue
        matches.append(EventMatch(
            left[i].event_id, right[j].event_id, 1.0, delta, orientation, "UNIQUE"
        ))
    return matches


@dataclass(frozen=True)
class MarketQuote:
    platform: str
    event_id: str
    market_type: str
    period: str
    line: str
    selection: str
    odds: str
    observed_at_utc: str
    market_id: str = ""

    @property
    def match_key(self) -> tuple[str, str, str, str]:
        return (self.market_type, self.period, normalize_name(self.line), normalize_name(self.selection))


def normalize_market_type(value: str) -> Optional[str]:
    """Map only explicit known labels; unknown source labels remain unmapped."""
    key = normalize_name(value)
    aliases = {
        "全场独赢": "FULL_TIME_MONEYLINE", "独赢": "FULL_TIME_MONEYLINE",
        "moneyline": "FULL_TIME_MONEYLINE", "1x2": "FULL_TIME_MONEYLINE",
        "全场大小": "FULL_TIME_TOTAL", "大小": "FULL_TIME_TOTAL",
        "totals": "FULL_TIME_TOTAL", "total": "FULL_TIME_TOTAL",
        "全场让球": "FULL_TIME_HANDICAP", "让球": "FULL_TIME_HANDICAP",
        "让分": "FULL_TIME_HANDICAP", "handicap": "FULL_TIME_HANDICAP",
        "spread": "FULL_TIME_HANDICAP",
    }
    return aliases.get(key)


def compare_quotes(op7: MarketQuote, pingbo: MarketQuote, *, max_age_seconds: int = 30,
                   now: Optional[datetime] = None) -> dict:
    """Compare same market/line/selection quotes; reject stale/mismatched data."""
    if op7.platform != "OP7" or pingbo.platform != "PINGBO":
        raise ValueError("报价平台必须分别为 OP7 和 PINGBO")
    if op7.market_type not in ALLOWED_MARKETS or pingbo.market_type not in ALLOWED_MARKETS:
        raise ValueError("不支持的盘口类型")
    if op7.match_key != pingbo.match_key:
        raise ValueError("盘口类型、比赛阶段、盘口线或投注选项不一致")
    current = now or datetime.now(timezone.utc)
    for quote in (op7, pingbo):
        stamp = _parse_time(quote.observed_at_utc)
        age = (current - stamp).total_seconds()
        if age < -5 or age > max_age_seconds:
            raise ValueError(f"{quote.platform}赔率数据过期或时钟异常")
    a, b = decimal_value(op7.odds), decimal_value(pingbo.odds)
    if a is None or b is None:
        raise ValueError("赔率无效")
    return {
        "event_ids": {"op7": op7.event_id, "pingbo": pingbo.event_id},
        "market_type": op7.market_type,
        "period": op7.period,
        "line": op7.line,
        "selection": op7.selection,
        "op7_odds": str(a),
        "pingbo_odds": str(b),
        "odds_delta_op7_minus_pingbo": str(a - b),
        "observed_at": {"op7": op7.observed_at_utc, "pingbo": pingbo.observed_at_utc},
        "status": "COMPARABLE",
    }


class AuditStore:
    """SQLite audit log; one record per event + market type, regardless of line."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_locks (
          event_key TEXT NOT NULL, market_type TEXT NOT NULL, line_key TEXT NOT NULL,
          created_at TEXT NOT NULL, amount TEXT, status TEXT NOT NULL,
          PRIMARY KEY(event_key, market_type)
        );
        CREATE TABLE IF NOT EXISTS alerts (
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, severity TEXT NOT NULL,
          code TEXT NOT NULL, message TEXT NOT NULL
        );
        """)
        self.db.commit()

    def save_snapshot(self, payload: dict) -> None:
        self.db.execute("INSERT INTO snapshots(created_at,payload_json) VALUES (?,?)",
                        (utc_now(), json.dumps(payload, ensure_ascii=False, default=str)))
        self.db.commit()

    def lock_market(self, event_key: str, market_type: str, line: str = "", amount: object = None) -> bool:
        if market_type not in ALLOWED_MARKETS:
            raise ValueError("不支持的盘口类型")
        if amount is not None:
            value = Decimal(str(amount))
            if not value.is_finite() or value < 50 or value > 500:
                raise ValueError("金额必须在 50–500 之间")
        try:
            self.db.execute("INSERT INTO market_locks VALUES (?,?,?,?,?,?)",
                            (event_key, market_type, normalize_name(line), utc_now(),
                             None if amount is None else str(amount), "RECORDED"))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def alert(self, severity: str, code: str, message: str) -> None:
        self.db.execute("INSERT INTO alerts(created_at,severity,code,message) VALUES (?,?,?,?)",
                        (utc_now(), severity, code, message))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
