#!/usr/bin/env python3
"""Find OP7's native event-search field and search a CLI-supplied event name.

The script only interacts with a search control. It never touches betting,
orders, wallet, account, or funds controls.
"""
from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.keys import Keys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from android.appium_session import (  # noqa: E402
    DEFAULT_PACKAGE, DEFAULT_SERVER_URL, DEFAULT_UDID, close_driver, create_driver,
)
from android.op7_event_search import (  # noqa: E402
    _xpath_literal, find_unique_search_result, parse_detail_event, parse_search_results,
)

SEARCH_TERMS = ("search", "搜索", "赛事", "球队", "联赛")


def _search_related(node: ET.Element) -> bool:
    values = " ".join(
        node.attrib.get(key, "")
        for key in ("resource-id", "text", "content-desc", "hint", "class")
    ).lower()
    return any(term in values for term in SEARCH_TERMS)


def _parse_bounds(value: str):
    match = __import__("re").fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    return tuple(map(int, match.groups())) if match else None


def inspect_search_controls(driver) -> tuple[list[dict], list[dict]]:
    """Find editable fields and the unlabeled magnifier immediately left of 联赛."""
    root = ET.fromstring(driver.page_source)
    fields, launchers = [], []
    nodes = list(root.iter())
    league_nodes = [
        node for node in nodes
        if (node.attrib.get("content-desc", "").strip() == "联赛"
            or node.attrib.get("text", "").strip() == "联赛")
        and node.attrib.get("clickable") == "true"
    ]
    league_rects = [_parse_bounds(n.attrib.get("bounds", "")) for n in league_nodes]
    league_rects = [r for r in league_rects if r]
    blank_images = []
    for node in nodes:
        attrs = node.attrib
        item = {
            "resource_id": attrs.get("resource-id", ""),
            "text": attrs.get("text", ""),
            "content_desc": attrs.get("content-desc", ""),
            "class": attrs.get("class", ""),
            "hint": attrs.get("hint", ""),
            "clickable": attrs.get("clickable", "false") == "true",
            "bounds": attrs.get("bounds", ""),
        }
        editable = attrs.get("class", "").endswith(("EditText", "AutoCompleteTextView"))
        if editable:
            fields.append(item)
        elif item["clickable"] and _search_related(node):
            launchers.append(item)
        elif (item["clickable"] and attrs.get("class") == "android.widget.ImageView"
              and not item["content_desc"] and not item["text"] and item["bounds"]):
            rect = _parse_bounds(item["bounds"])
            if rect:
                blank_images.append((item, rect))

    # On this OP7 page the magnifier has no accessibility label. Identify it
    # structurally: a clickable, unlabeled ImageView on the same toolbar row,
    # immediately to the left of the clickable 联赛 filter.
    nearby = []
    for item, (left, top, right, bottom) in blank_images:
        for ll, lt, lr, lb in league_rects:
            vertical_overlap = min(bottom, lb) - max(top, lt)
            horizontal_gap = ll - right
            if vertical_overlap > 0 and 0 <= horizontal_gap <= 100:
                nearby.append(item)
                break
    if len(nearby) == 1:
        item = nearby[0]
        item["locator_xpath"] = (
            "//*[@class='android.widget.ImageView' and @clickable='true' "
            f"and @bounds={_xpath_literal(item['bounds'])}]"
        )
        item["identified_as"] = "unlabeled magnifier left of 联赛"
        launchers.append(item)
    return fields, launchers

def _find_by_resource_id(driver, resource_id: str):
    return driver.find_element(AppiumBy.ID, resource_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search an event in the OP7 Android app")
    parser.add_argument("--event", required=True, help="Event/team/league name to search")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--search-field-id", help="Exact Android resource-id of the search input")
    parser.add_argument("--search-trigger-id", help="Exact resource-id of the search icon/button, if it first opens the input")
    parser.add_argument("--wait", type=float, default=2, help="Seconds to wait after search submission")
    parser.add_argument("--league", help="Exact competition label used to select a result")
    parser.add_argument("--home", help="Home team name used with --league/--away")
    parser.add_argument("--away", help="Away team name used with --league/--home")
    parser.add_argument("--submit", choices=("enter", "none"), default="enter",
                        help="Submit with keyboard Enter, or leave the query in the field")
    args = parser.parse_args()
    if not args.event.strip():
        parser.error("--event cannot be blank")
    if args.wait < 0:
        parser.error("--wait must be >= 0")

    driver = None
    try:
        driver = create_driver(args.server, args.udid, args.package, no_reset=True)
        print(f"Connected: package={driver.current_package}, activity={driver.current_activity}")
        print("请确认 APP 当前停留在赛事列表/搜索页面。")
        if args.search_trigger_id:
            _find_by_resource_id(driver, args.search_trigger_id).click()
            time.sleep(0.5)

        if args.search_field_id:
            field = _find_by_resource_id(driver, args.search_field_id)
        else:
            fields, launchers = inspect_search_controls(driver)
            if not fields and not args.search_trigger_id:
                structural = [item for item in launchers if item.get("locator_xpath")]
                if len(structural) == 1:
                    print("Detected search launcher:", structural[0]["identified_as"])
                    matches = driver.find_elements(AppiumBy.XPATH, structural[0]["locator_xpath"])
                    if len(matches) != 1:
                        raise ValueError(f"放大镜控件匹配到 {len(matches)} 个元素，拒绝点击")
                    matches[0].click()
                    time.sleep(0.5)
                    fields, launchers = inspect_search_controls(driver)
            if len(fields) != 1:
                print(f"Search input candidates ({len(fields)}):")
                for item in fields:
                    print(item)
                print(f"Search launcher candidates ({len(launchers)}):")
                for item in launchers:
                    print(item)
                print("无法安全唯一识别搜索输入框；已拒绝猜测点击。", file=sys.stderr)
                return 2
            resource_id = fields[0]["resource_id"]
            if resource_id:
                field = _find_by_resource_id(driver, resource_id)
            else:
                visible_fields = [
                    item for item in driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                    if item.is_displayed()
                ]
                if len(visible_fields) != 1:
                    print(f"可见 EditText 数量为 {len(visible_fields)}，拒绝猜测搜索框。", file=sys.stderr)
                    return 2
                field = visible_fields[0]

        field.click()
        try:
            field.clear()
        except Exception:
            pass
        field.send_keys(args.event.strip())
        print(f"Query entered: {args.event.strip()}")
        if args.submit == "enter":
            field.send_keys(Keys.ENTER)
            print("Search submitted with Enter.")
        else:
            print("Query left in the search field; not submitted.")
        time.sleep(args.wait)
        print(f"Search finished waiting; activity={driver.current_activity}")
        events = parse_search_results(driver.page_source)
        print(f"Parsed search results: {len(events)}")
        for index, event in enumerate(events, 1):
            print(f"[{index}] league={event.league} | {event.home} VS {event.away} | "
                  f"period={event.period or '-'} | clock={event.clock or '-'}")
        selectors = (args.league, args.home, args.away)
        if any(selectors) and not all(selectors):
            print("--league、--home、--away 必须同时提供。", file=sys.stderr)
            return 2
        if all(selectors):
            selected = find_unique_search_result(
                events, league=args.league, home=args.home, away=args.away
            )
            matches = driver.find_elements(
                AppiumBy.XPATH,
                f"//*[@content-desc={_xpath_literal(selected.event_label)}]"
            )
            if len(matches) != 1:
                raise ValueError(f"目标赛事行匹配到 {len(matches)} 个控件，拒绝点击")
            matches[0].click()
            time.sleep(args.wait)
            detail = parse_detail_event(driver.page_source)
            print("Selected event detail:")
            print(f"league={detail.league} | {detail.home} {detail.home_score} - "
                  f"{detail.away_score} {detail.away} | period={detail.period} | clock={detail.clock}")
        return 0
    except Exception as exc:
        print(f"OP7 search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
