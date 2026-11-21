#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline basketball policy layer for OP7 EB / Part567.

Reads the normalized analysis JSON produced by op7_fb_eb_venue_mapper.py.
No network, login, wager, order, wallet, or financial mutation is performed.
"""
import argparse
import json
import re
from pathlib import Path
from decimal import Decimal, InvalidOperation

ALLOWED_MARKETS = [
    "BASKETBALL_FT_HANDICAP",
    "BASKETBALL_FT_TOTAL",
    "BASKETBALL_FT_MONEYLINE",
]
EXPECTED_SETTLEMENT = "FULL_GAME_INCLUDING_OT"

DEFAULT_CONFIG = {
    "event_filter": {
        "soccer_whitelist": ["EPL", "LaLiga", "SerieA", "Bundesliga", "Ligue1", "UCL"],
        "basketball_whitelist": ["NBA", "KBL"],
        "league_aliases": {
            "NBA": ["NBA", "National Basketball Association"],
            "KBL": ["KBL", "Korean Basketball League", "Korea Basketball League", "韩国篮球联赛", "韩国职业篮球联赛"]
        }
    },
    "platforms": {
        "PINNACLE": {
            "provider": "part567", "source": "Part567",
            "settlement_scope": "UNKNOWN",
            "market_aliases": {
                "BASKETBALL_FT_HANDICAP": {"market_key": ["0"]},
                "BASKETBALL_FT_TOTAL": {"market_key": ["1"]},
                # Populate only after confirming the Part567 raw key/tuple schema from HAR.
                "BASKETBALL_FT_MONEYLINE": {"market_key": []}
            }
        },
        "PANDA": {
            "provider": "op7_eb", "source": "OP7 EB",
            "settlement_scope": "UNKNOWN",
            "market_aliases": {
                "BASKETBALL_FT_HANDICAP": {"play_id": [39], "mty": [3002]},
                "BASKETBALL_FT_TOTAL": {"play_id": [38], "mty": [3003]},
                "BASKETBALL_FT_MONEYLINE": {"play_id": [37], "mty": [3004]}
            }
        }
    },
    "matching": {
        "event_time_tolerance_minutes": 5,
        "odds_stale_seconds": 30,
        "fx_stale_hours": 24,
        "basketball_overtime_enabled": True
    },
    "settlement": {
        "soccer": {"period": "FULL_TIME", "scope": "REGULATION_TIME"},
        "basketball": {
            "period": "FULL_TIME",
            "scope": EXPECTED_SETTLEMENT,
            "include_overtime": True,
            "overtime_points_counted": True
        }
    }
}


def norm(value):
    return re.sub(r"[\s\u3000\-_.()（）\[\]【】]+", "", str(value or "")).lower()


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8")) if path else json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.setdefault("event_filter", {})
    cfg["event_filter"].setdefault("basketball_whitelist", ["NBA", "KBL"])
    cfg.setdefault("matching", {})
    cfg["matching"].setdefault("basketball_overtime_enabled", True)
    cfg.setdefault("settlement", {}).setdefault("basketball", {})
    b = cfg["settlement"]["basketball"]
    b.setdefault("period", "FULL_TIME")
    b.setdefault("scope", EXPECTED_SETTLEMENT)
    b.setdefault("include_overtime", True)
    b.setdefault("overtime_points_counted", True)
    # Fail closed on contradictory or legacy config: don't silently reinterpret it.
    if b["scope"] != EXPECTED_SETTLEMENT or b["include_overtime"] is not True or b["overtime_points_counted"] is not True:
        raise ValueError("篮球结算配置必须为 FULL_GAME_INCLUDING_OT，且 include_overtime/overtime_points_counted 均为 true")
    if cfg["matching"]["basketball_overtime_enabled"] is not True:
        raise ValueError("matching.basketball_overtime_enabled 必须为 true")
    return cfg


def classify_league(event, cfg):
    raw = norm(event.get("league"))
    aliases = cfg.get("event_filter", {}).get("league_aliases", {})
    for canonical in cfg.get("event_filter", {}).get("basketball_whitelist", ["NBA", "KBL"]):
        names = [canonical] + aliases.get(canonical, [])
        if any(norm(x) in raw or raw in norm(x) for x in names if norm(x)):
            return canonical
    return None


def is_basketball(event):
    sport = norm(event.get("sport") or event.get("sport_type"))
    league = norm(event.get("league"))
    return any(x in sport or x in league for x in ("basketball", "篮球", "nba", "kbl", "korea"))


def classify_market(row, venue, cfg):
    platform = "PINNACLE" if venue == "PINGBO" else "PANDA"
    aliases = cfg.get("platforms", {}).get(platform, {}).get("market_aliases", {})
    for market_type, rule in aliases.items():
        if venue == "PINGBO" and str(row.get("market_key")) in {str(x) for x in rule.get("market_key", [])}:
            return market_type
        if venue == "EB" and str(row.get("play_id_hpid")) in {str(x) for x in rule.get("play_id", [])}:
            return market_type
        if venue == "FB" and str(row.get("market_type_mty")) in {str(x) for x in rule.get("mty", [])}:
            return market_type
    return None


def as_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def event_lookup(payload):
    return {(e.get("venue"), str(e.get("event_id"))): e for e in payload.get("events", [])}


def platform_settlement_state(platform_cfg):
    actual = str(platform_cfg.get("settlement_scope", "UNKNOWN")).upper()
    if actual in ("FULL_GAME_INCLUDING_OT", "FULL_TIME_INCLUDING_OT", "INCLUDING_OT"):
        return "VERIFIED", "来源配置已声明全场包含加时"
    if actual in ("REGULATION_TIME", "FULL_GAME_EXCLUDING_OT", "EXCLUDING_OT"):
        return "MISMATCH", "来源结算口径不含加时，与本项目要求冲突"
    return "REVIEW", "来源全场盘口是否包含加时尚未核实"


def normalize_rows(payload, cfg):
    evmap = event_lookup(payload)
    source_rows = [("FB", x) for x in payload.get("markets", []) if x.get("venue") == "FB"]
    source_rows += [("EB", x) for x in payload.get("markets", []) if x.get("venue") == "EB"]
    source_rows += [("PINGBO", x) for x in payload.get("pingbo_markets", [])]
    output = []
    for venue, row in source_rows:
        ev = evmap.get((venue, str(row.get("event_id"))), {})
        if venue == "PINGBO":
            ev = next((e for e in payload.get("pingbo_events", []) if str(e.get("event_id")) == str(row.get("event_id"))), {})
            paired = next((c for c in payload.get("cross_venue_event_candidates", [])
                           if str(c.get("pingbo_event_id")) == str(row.get("event_id"))
                           and c.get("venue_event") in ("EB", "FB")), None)
            if paired:
                paired_ev = evmap.get((paired.get("venue_event"), str(paired.get("event_id"))), {})
                ev = {**ev, "sport": paired_ev.get("sport", paired_ev.get("sport_type")),
                      "league": paired_ev.get("league"), "home": paired_ev.get("home"), "away": paired_ev.get("away")}
        if not is_basketball(ev):
            continue

        league = classify_league(ev, cfg)
        market_type = classify_market(row, venue, cfg)
        platform = "PINNACLE" if venue == "PINGBO" else "PANDA"
        p_cfg = cfg.get("platforms", {}).get(platform, {})

        if league is None:
            status, reason = "EVENT_OUT_OF_SCOPE", "赛事不在 NBA/KBL 白名单或联赛别名未配置"
        elif market_type not in ALLOWED_MARKETS:
            status, reason = "UNSUPPORTED_MARKET", "仅允许全场让分、全场大小分、全场独赢；未知或未配置玩法不猜测"
        else:
            settlement_state, settlement_reason = platform_settlement_state(p_cfg)
            if settlement_state == "MISMATCH":
                status, reason = "SETTLEMENT_MISMATCH", settlement_reason
            elif settlement_state == "REVIEW":
                status, reason = "NEEDS_REVIEW", settlement_reason
            else:
                status, reason = "ELIGIBLE", "赛事、玩法及包含加时结算口径符合配置；仍需通过赛事/盘口/赔率新鲜度校验"

        line = row.get("line", row.get("line_hv"))
        if venue == "PINGBO":
            line = row.get("line_numeric_source_index_1", row.get("line", line)) if market_type == "BASKETBALL_FT_TOTAL" else row.get("signed_line", line)
            selections = [{"selection": row.get("side"), "decimal_odds": row.get("decimal_odds"), "selection_id": row.get("selection_id")}]
        else:
            selections = row.get("selections", [])

        for sel in selections:
            odds = as_decimal(sel.get("decimal_odds"))
            if odds is None or odds <= 1:
                continue
            output.append({
                "source": "Part567" if venue == "PINGBO" else "OP7 EB" if venue == "EB" else "OP7 FB",
                "venue": venue,
                "source_event_id": row.get("event_id"),
                "competition": ev.get("league"),
                "canonical_competition": league,
                "home_team": ev.get("home"),
                "away_team": ev.get("away"),
                "market_type": market_type,
                "period": "FULL_TIME",
                "settlement_scope": EXPECTED_SETTLEMENT,
                "include_overtime": True,
                "overtime_points_counted": True,
                "source_settlement_scope": p_cfg.get("settlement_scope", "UNKNOWN"),
                "selection": sel.get("selection", sel.get("selection_code")),
                "line": line,
                "odds": str(odds),
                "market_status": sel.get("common_market_status", row.get("common_market_status")),
                "source_market_name": row.get("market_name", row.get("market_label")),
                "source_selection_name": sel.get("selection"),
                "match_status": status,
                "reason": reason
            })
    return output


def main():
    ap = argparse.ArgumentParser(description="Offline basketball market policy filter; no network or bet actions")
    ap.add_argument("--analysis", required=True, help="JSON output from op7_fb_eb_venue_mapper.py")
    ap.add_argument("--config", help="matching_config.json; defaults to NBA/KBL including-OT policy")
    ap.add_argument("--out", default="basketball_normalized.json")
    args = ap.parse_args()
    payload = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    cfg = load_config(args.config)
    rows = normalize_rows(payload, cfg)
    summary = {}
    for row in rows:
        summary[row["match_status"]] = summary.get(row["match_status"], 0) + 1
    result = {
        "policy": {
            "basketball_leagues": cfg["event_filter"].get("basketball_whitelist", []),
            "allowed_markets": ALLOWED_MARKETS,
            "settlement_scope": EXPECTED_SETTLEMENT,
            "include_overtime": True,
            "overtime_points_counted": True
        },
        "summary": summary,
        "rows": rows,
        "limitations": [
            "Part567 moneyline raw market key/tuple layout is not inferred; configure its alias only after inspecting HAR schema.",
            "ELIGIBLE means policy-qualified, not a confirmed cross-platform matched pair.",
            "A source remains NEEDS_REVIEW until its actual full-game including-overtime settlement is verified.",
            "Unknown aliases, unsupported markets and out-of-scope leagues are never guessed.",
            "Event-time tolerance, odds freshness and FX freshness require comparable timestamps/rate snapshots; missing values remain unverified."
        ]
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": args.out, "rows": len(rows), "summary": summary, "policy": result["policy"]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
