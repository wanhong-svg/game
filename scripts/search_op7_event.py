#!/usr/bin/env python3
"""Find OP7's native event-search field and search a CLI-supplied event name.

The script only interacts with a search control. It never touches betting,
orders, wallet, account, or funds controls.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

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
            "selected": attrs.get("selected", ""),
            "checked": attrs.get("checked", ""),
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

def _control_by_label(driver, label: str):
    """Return exactly one clickable control with the exact accessibility label."""
    xpath = f"//*[@clickable='true' and @content-desc={_xpath_literal(label)}]"
    matches = driver.find_elements(AppiumBy.XPATH, xpath)
    if len(matches) != 1:
        xpath = f"//*[@clickable='true' and @text={_xpath_literal(label)}]"
        matches = driver.find_elements(AppiumBy.XPATH, xpath)
    if len(matches) != 1:
        # Sport category labels include a live count, such as "篮球\\n12".
        xpath = (
            "//*[@clickable='true' and "
            f"starts-with(normalize-space(@content-desc), {_xpath_literal(label)})]"
        )
        matches = driver.find_elements(AppiumBy.XPATH, xpath)
    if len(matches) != 1:
        raise ValueError(f"控件「{label}」匹配到 {len(matches)} 个，拒绝猜测点击")
    return matches[0]


def _selected_state(element) -> bool | None:
    """Read explicit accessibility selection state when the app exposes it."""
    selected = element.get_attribute("selected")
    checked = element.get_attribute("checked")
    values = [v.strip().lower() for v in (selected, checked) if v is not None and v != ""]
    if any(v == "true" for v in values):
        return True
    if values and all(v == "false" for v in values):
        return False
    return None


def _element_blue_ratio(element) -> float:
    """Return the fraction of an element screenshot occupied by saturated blue."""
    if Image is None:
        raise RuntimeError(
            "视觉状态确认需要 Pillow；请先执行：python3 -m pip install Pillow"
        )
    screenshot = Image.open(io.BytesIO(element.screenshot_as_png)).convert("RGB")
    width, height = screenshot.size
    if width < 8 or height < 8:
        raise RuntimeError(f"控件截图尺寸异常：{width}x{height}")
    pixels = list(screenshot.getdata())
    blue = sum(
        1 for r, g, b in pixels
        if b >= 125 and b - r >= 45 and b - g >= 12 and max(r, g, b) - min(r, g, b) >= 45
    )
    return blue / max(len(pixels), 1)


def _is_eb_visually_selected(element) -> bool:
    """Recognize EB's blue selected pill from the user-provided OP/EB references."""
    if Image is None:
        raise RuntimeError(
            "EB 视觉确认需要 Pillow；请先执行：python3 -m pip install Pillow"
        )
    screenshot = Image.open(io.BytesIO(element.screenshot_as_png)).convert("RGB")
    width, height = screenshot.size
    if width < 8 or height < 8:
        raise RuntimeError(f"EB 控件截图尺寸异常：{width}x{height}")
    # Sample an interior patch near the left edge, away from the centered EB text
    # and rounded corners. Reference selected blue is RGB (27, 129, 255).
    x0, x1 = int(width * 0.12), int(width * 0.28)
    y0, y1 = int(height * 0.35), int(height * 0.65)
    pixels = list(screenshot.crop((x0, y0, x1, y1)).getdata())
    blue = sum(1 for r, g, b in pixels if b >= 190 and g >= 75 and r <= 75 and b - r >= 120)
    ratio = blue / max(len(pixels), 1)
    print(f"EB 选中视觉检测：蓝色像素占比 {ratio:.0%}。")
    return ratio >= 0.60


def _is_selected(driver, label: str, element) -> bool | None:
    # The bottom navigation and EB channel tabs use blue for their active state.
    # Accessibility selected=false is not authoritative for these custom controls.
    if label == "EB":
        return _is_eb_visually_selected(element)
    if label == "体育":
        ratio = _element_blue_ratio(element)
        print(f"「体育」选中视觉检测：蓝色像素占比 {ratio:.1%}。")
        return ratio >= 0.015
    if label in ("足球", "篮球"):
        ratio = _element_blue_ratio(element)
        print(f"「{label}」选中视觉检测：蓝色像素占比 {ratio:.1%}。")
        # Sport tabs use the same blue active styling. Their clickable bounds
        # can be tighter than the bottom navigation, so use a modest threshold.
        return ratio >= 0.01
    return _selected_state(element)


def _has_visible_control(driver, label: str) -> bool:
    try:
        element = _control_by_label(driver, label)
        return element.is_displayed()
    except Exception:
        return False


def _search_overlay_open(driver) -> bool:
    """Identify the OP7 search modal from its visible labels and search field."""
    try:
        root = ET.fromstring(driver.page_source)
    except ET.ParseError:
        return False
    texts = " ".join(
        " ".join((node.attrib.get("text", ""), node.attrib.get("content-desc", ""))).strip()
        for node in root.iter()
    )
    has_cancel = any(
        node.attrib.get("clickable") == "true"
        and (node.attrib.get("text", "").strip() == "取消"
             or node.attrib.get("content-desc", "").strip() == "取消")
        for node in root.iter()
    )
    has_search_tabs = "筛选" in texts and "搜索" in texts
    has_input = any(
        node.attrib.get("class", "").endswith(("EditText", "AutoCompleteTextView"))
        for node in root.iter()
    )
    return has_cancel and has_search_tabs and has_input


def _click_cancel_if_present(driver, wait: float) -> bool:
    """Dismiss a visible search/modal overlay only through its labelled 取消 action."""
    xpath = (
        f"//*[@clickable='true' and (@text={_xpath_literal('取消')} "
        f"or @content-desc={_xpath_literal('取消')})]"
    )
    matches = driver.find_elements(AppiumBy.XPATH, xpath)
    visible = [item for item in matches if item.is_displayed()]
    if len(visible) != 1:
        return False
    visible[0].click()
    time.sleep(wait)
    print("检测到弹层，已点击「取消」并等待页面返回。")
    return True


def _is_home_navigation_visible(driver) -> bool:
    """Treat the main five-item bottom navigation as the home-screen marker."""
    labels = ("发现", "娱乐", "体育", "注单", "我的")
    return sum(_has_visible_control(driver, label) for label in labels) >= 3


def recover_navigation_page(driver, target_label: str, wait: float = 1.5) -> None:
    """Close an accidental search overlay and return to home before menu navigation."""
    if _search_overlay_open(driver):
        print("菜单切换期间检测到搜索弹层，启动页面恢复流程。")
        _click_cancel_if_present(driver, wait)

    if _has_visible_control(driver, target_label):
        print(f"页面恢复后已找到目标功能「{target_label}」，继续菜单切换。")
        return

    print(f"当前页面未找到目标功能「{target_label}」，尝试逐级返回主页。")
    for step in range(1, 9):
        if _is_home_navigation_visible(driver):
            break
        # If another search/modal layer is exposed during back navigation,
        # prefer its explicit 取消 control before using Android Back.
        if _search_overlay_open(driver):
            if _click_cancel_if_present(driver, wait):
                continue
        driver.back()
        time.sleep(wait)
        print(f"已执行第 {step} 次返回并等待 {wait:.1f}s。")

    if not _is_home_navigation_visible(driver):
        raise RuntimeError("最多返回 8 层仍无法确认主页导航，停止操作以避免误点。")
    if not _has_visible_control(driver, target_label):
        raise RuntimeError(
            f"已返回主页，但仍未找到目标功能「{target_label}」，停止后续菜单切换。"
        )
    print(f"已确认返回主页，并找到目标功能「{target_label}」。")


def ensure_selected(driver, label: str, wait: float = 1.5) -> None:
    """Select a navigation tab, wait for rendering, then verify its active state.

    EB and the bottom 体育 tab are custom controls whose accessibility
    selected/checked flags do not reflect the visible active styling.
    """
    element = _control_by_label(driver, label)
    state = _is_selected(driver, label, element)
    if state is True:
        print(f"「{label}」已确认处于选中状态。")
        return
    if state is False:
        print(f"「{label}」明确未选中，执行切换。")
    else:
        print(f"「{label}」未提供可确认的选中状态，点击后必须重新验证。")
    element.click()
    time.sleep(wait)
    refreshed = _control_by_label(driver, label)
    verified = _is_selected(driver, label, refreshed)
    if verified is not True:
        reason = "仍明确未选中" if verified is False else "无法确认选中状态"
        raise RuntimeError(
            f"点击「{label}」并等待 {wait:.1f}s 后，{reason}；"
            "停止后续操作，不能仅凭控件存在判定切换成功。"
        )
    print(f"已等待 {wait:.1f}s，并确认「{label}」处于选中状态。")


def prepare_op7_page(driver, sport: str, wait: float = 1.5) -> None:
    """Select 体育 -> EB -> requested sport, waiting and verifying each step."""
    # Recover from an accidentally opened search/filter sheet before following
    # the configured navigation route. The target function for this route is 体育.
    recover_navigation_page(driver, "体育", wait)
    ensure_selected(driver, "体育", wait)
    ensure_selected(driver, "EB", wait)
    sport_label = {"football": "足球", "basketball": "篮球"}[sport]
    ensure_selected(driver, sport_label, wait)


def _find_by_resource_id(driver, resource_id: str):
    return driver.find_element(AppiumBy.ID, resource_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search an event in the OP7 Android app")
    parser.add_argument("--event", required=True, help="Event/team/league name to search")
    parser.add_argument("--sport", choices=("football", "basketball"), default="basketball",
                        help="Sport tab to select before searching (default: basketball)")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--search-field-id", help="Exact Android resource-id of the search input")
    parser.add_argument("--search-trigger-id", help="Exact resource-id of the search icon/button, if it first opens the input")
    parser.add_argument("--wait", type=float, default=30,
                        help="Maximum seconds to wait for search results or the empty-result message (default: 30)")
    parser.add_argument("--poll-interval", type=float, default=0.5,
                        help="Seconds between search-result checks (default: 0.5)")
    parser.add_argument("--detail-wait", type=float, default=2,
                        help="Seconds to wait after opening a matched event detail (default: 2)")
    parser.add_argument("--setup-wait", type=float, default=1.5,
                        help="Seconds to wait after each initial navigation step (default: 1.5)")
    parser.add_argument("--league", help="Exact competition label used to select a result")
    parser.add_argument("--home", help="Home team name used with --league/--away")
    parser.add_argument("--away", help="Away team name used with --league/--home")
    parser.add_argument("--submit", choices=("enter", "none"), default="enter",
                        help="Submit with keyboard Enter, or leave the query in the field")
    args = parser.parse_args()
    if not args.event.strip():
        parser.error("--event cannot be blank")
    if args.wait <= 0:
        parser.error("--wait must be > 0")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be > 0")
    if args.detail_wait < 0:
        parser.error("--detail-wait must be >= 0")
    if args.setup_wait < 0:
        parser.error("--setup-wait must be >= 0")

    driver = None
    try:
        driver = create_driver(args.server, args.udid, args.package, no_reset=True)
        print(f"Connected: package={driver.current_package}, activity={driver.current_activity}")
        print("准备 OP7 页面：体育主菜单 -> EB -> 指定球类。")
        prepare_op7_page(driver, args.sport, args.setup_wait)
        print("页面切换完成，开始识别赛事搜索入口。")
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

        query = args.event.strip()
        field.click()
        try:
            field.clear()
        except Exception:
            pass
        # Selenium/Appium WebElement does not implement set_value().
        # Use the supported send_keys API, then verify the exact text before submit.
        field.send_keys(query)
        actual_value = field.get_attribute("text") or ""
        if actual_value != query:
            raise RuntimeError(
                f"搜索框输入校验失败：期望 {query!r}，实际 {actual_value!r}；"
                "已停止提交，避免使用含异常字符的搜索词。"
            )
        print(f"搜索框内容校验通过：{actual_value!r}")
        if args.submit == "enter":
            search_button = _control_by_label(driver, "搜索")
            search_button.click()
            print("已点击页面「搜索」按钮。")
        else:
            print("Query left in the search field; not submitted.")

        # Wait for either parsed event rows or the explicit empty-result state.
        deadline = time.monotonic() + args.wait
        events = []
        result_state = None
        while time.monotonic() < deadline:
            source = driver.page_source
            events = parse_search_results(source)
            normalized_source = " ".join(source.replace("\\n", " ").split())
            if events:
                result_state = "results"
                break
            if "暂无搜索结果" in normalized_source:
                result_state = "empty"
                break
            time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))

        if result_state == "empty":
            print("未找到赛事信息：页面提示「暂无搜索结果」。")
            print("保持当前搜索页面，不执行联赛筛选，也不返回主页。")
            return 3
        if result_state != "results":
            raise TimeoutError(
                f"等待 {args.wait:.1f}s 后，既未检测到赛事结果，也未检测到「暂无搜索结果」。"
            )

        print(f"已检测到赛事结果：{len(events)} 条。")
        print(f"Search finished; activity={driver.current_activity}")
        print(f"Parsed search results: {len(events)}")
        for index, event in enumerate(events, 1):
            print(f"[{index}] league={event.league} | {event.home} VS {event.away} | "
                  f"period={event.period or '-'} | clock={event.clock or '-'}")
        # 联赛筛选只允许在已经出现赛事搜索结果后执行；空结果已在上方原地结束。
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
            time.sleep(args.detail_wait)
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
