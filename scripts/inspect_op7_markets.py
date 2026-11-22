#!/usr/bin/env python3
"""Read OP7 full-time markets and optionally verify a selected slip.

The optional selection action only opens the review panel. This script never
enters a stake and never presses any wager/submit control.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from android.appium_session import (  # noqa: E402
    DEFAULT_PACKAGE, DEFAULT_SERVER_URL, DEFAULT_UDID, close_driver, create_driver,
)
from android.op7_event_search import parse_detail_event  # noqa: E402
from android.op7_market_reader import (  # noqa: E402
    find_unique_option, open_selection_slip, read_market_options,
)
from android.op7_selection_verifier import (  # noqa: E402
    ExpectedSelection, verify_current_slip,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read OP7 full-time markets")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--market-type", choices=(
        "FULL_TIME_MONEYLINE", "FULL_TIME_HANDICAP", "FULL_TIME_TOTAL"
    ))
    parser.add_argument("--selection", help="主/客/大/小")
    parser.add_argument("--line", default="", help="Exact handicap or total line, e.g. +12.5 or 208.5")
    parser.add_argument("--open-slip", action="store_true",
                        help="Open the selected option's review panel and verify it; never submit")
    parser.add_argument("--wait", type=float, default=1.5)
    args = parser.parse_args()

    if args.wait < 0:
        parser.error("--wait must be >= 0")
    if args.open_slip:
        if not args.market_type or not args.selection:
            parser.error("--open-slip requires --market-type and --selection")
        if args.market_type in {"FULL_TIME_HANDICAP", "FULL_TIME_TOTAL"} and not args.line:
            parser.error("handicap/total selection requires --line")
        if args.market_type == "FULL_TIME_MONEYLINE" and args.line:
            parser.error("moneyline selection must not specify --line")

    driver = None
    try:
        driver = create_driver(args.server, args.udid, args.package, no_reset=True)
        source = driver.page_source
        try:
            event = parse_detail_event(source)
            if not event.league:
                raise ValueError("详情页未能唯一读取联赛标签")
            print(f"Event: {event.league} | {event.home} {event.home_score} - "
                  f"{event.away_score} {event.away} | {event.period} {event.clock}")
        except ValueError as exc:
            # Scheduled fixtures may show league/date/kickoff/teams without a
            # live score or period. Do not mislabel that as being off the detail
            # page; emit the visible text nodes so the header parser can be
            # adapted to the actual native hierarchy.
            import xml.etree.ElementTree as ET
            print(f"Event header parser needs scheduled-fixture support: {exc}")
            root = ET.fromstring(source)
            print("Visible UI text nodes (tree order):")
            for node in root.iter():
                value = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
                if value:
                    bounds = node.attrib.get("bounds", "")
                    print(f"  text={value!r} bounds={bounds}")

        options = read_market_options(source)
        if not options:
            raise ValueError("当前页面未读取到全场盘口；请确认已打开赛事详情页并展开盘口")
        print(f"Readable full-time options: {len(options)}")
        for option in options:
            print(f"{option.market_label} [{option.market_type}] | "
                  f"{option.selection} {option.line} | odds={option.odds}")

        if not args.open_slip:
            print("Read-only listing complete. No market option was clicked.")
            return 0

        option = find_unique_option(
            options, market_type=args.market_type,
            selection=args.selection, line=args.line,
        )
        if event.home_score is None or event.away_score is None:
            raise ValueError("详情页比分不完整，不能核验注单面板")
        expected = ExpectedSelection(
            league=event.league,
            home=event.home,
            away=event.away,
            market_label=option.market_label,
            selection=args.selection,
            line=option.line,
            odds=option.odds,
            score=f"{event.home_score}:{event.away_score}",
        )
        open_selection_slip(driver, option)
        time.sleep(args.wait)
        actual = verify_current_slip(driver, expected)
        print("Selection slip verified:")
        print(f"{actual.league} | {actual.home} VS {actual.away} | "
              f"{actual.market_label} | {actual.selection_text} | "
              f"line={actual.line} | odds={actual.odds} | score={actual.score}")
        print("Review panel only. No stake entered; no wager submitted.")
        return 0
    except Exception as exc:
        print(f"OP7 market inspection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
