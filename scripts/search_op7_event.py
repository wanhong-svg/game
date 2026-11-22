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

SEARCH_TERMS = ("search", "搜索", "赛事", "球队", "联赛")


def _search_related(node: ET.Element) -> bool:
    values = " ".join(
        node.attrib.get(key, "")
        for key in ("resource-id", "text", "content-desc", "hint", "class")
    ).lower()
    return any(term in values for term in SEARCH_TERMS)


def inspect_search_controls(driver) -> tuple[list[dict], list[dict]]:
    """Return search-like editable fields and clickable search launchers."""
    root = ET.fromstring(driver.page_source)
    fields, launchers = [], []
    for node in root.iter():
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
        if editable and _search_related(node):
            fields.append(item)
        elif item["clickable"] and _search_related(node):
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
            if len(fields) != 1:
                print(f"Search input candidates ({len(fields)}):")
                for item in fields:
                    print(item)
                print(f"Search launcher candidates ({len(launchers)}):")
                for item in launchers:
                    print(item)
                print("无法安全唯一识别搜索输入框。请根据上方控件信息重新运行并传入 --search-field-id；"
                      "若搜索图标需要先点击以展开输入框，传入 --search-trigger-id。", file=sys.stderr)
                return 2
            resource_id = fields[0]["resource_id"]
            if not resource_id:
                print("搜索输入框没有 resource-id，不能安全定位；请检查控件树后补充专用定位方式。", file=sys.stderr)
                return 2
            field = _find_by_resource_id(driver, resource_id)

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
        return 0
    except Exception as exc:
        print(f"OP7 search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
