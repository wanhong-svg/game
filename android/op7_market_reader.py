"""Read OP7 full-time market labels and odds from the native UI hierarchy.

This module never places a wager. Clicking a market option is deliberately
separated from reading; callers must explicitly request opening a selection slip.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


MARKETS = {
    "全场独赢": "FULL_TIME_MONEYLINE",
    "全场让分": "FULL_TIME_HANDICAP",
    "全场让球": "FULL_TIME_HANDICAP",
    "全场大小": "FULL_TIME_TOTAL",
}


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(c for c in value if c.isalnum())


@dataclass(frozen=True)
class MarketOption:
    market_label: str
    market_type: str
    selection: str
    line: str
    odds: str
    accessibility_label: str


def _decimal_text(value: str) -> str:
    try:
        number = Decimal(value)
        # OP7 may display Hong Kong odds below 1.00 (e.g. 0.88).
        if not number.is_finite() or number <= 0:
            raise ValueError
        return format(number.normalize(), "f")
    except (InvalidOperation, ValueError):
        raise ValueError(f"赔率格式无效：{value!r}")


def _parse_option(desc: str, current_market: str) -> Optional[MarketOption]:
    lines = [x.strip() for x in desc.splitlines() if x.strip()]
    if not lines or not current_market:
        return None
    joined = " ".join(lines)
    odds_match = re.search(r"(?<![\d.])(\d+\.\d{1,4})(?!\d)", lines[-1])
    if not odds_match:
        return None
    odds = _decimal_text(odds_match.group(1))
    label = " ".join(lines[:-1]) if len(lines) > 1 else lines[0]
    label = re.sub(r"\s+", " ", label).strip()
    # Full-time moneyline options may be 主/客; handicap and totals carry a line.
    selection_match = re.match(r"^(主队?|客队?|主|客|大|小|Over|Under)\s*([+\-−]?\s*\d+(?:\.\d+)?)?$", label, re.I)
    if not selection_match:
        return None
    selection = selection_match.group(1)
    line = (selection_match.group(2) or "").replace(" ", "").replace("−", "-")
    if current_market == "FULL_TIME_HANDICAP" and not line:
        return None
    if current_market == "FULL_TIME_TOTAL" and not line:
        return None
    return MarketOption(
        market_label=next(k for k, v in MARKETS.items() if v == current_market),
        market_type=current_market,
        selection=selection,
        line=line,
        odds=odds,
        accessibility_label=desc,
    )


def read_market_options(source: str) -> list[MarketOption]:
    """Read market sections by heading labels and their adjacent option controls."""
    root = ET.fromstring(source)
    current_market = ""
    output = []
    for node in root.iter():
        desc = node.attrib.get("content-desc", "").strip()
        text = node.attrib.get("text", "").strip()
        label = desc or text
        normalized = _norm(label)
        detected = next((kind for heading, kind in MARKETS.items() if normalized == _norm(heading)), None)
        if detected:
            current_market = detected
            continue
        if current_market and label:
            option = _parse_option(label, current_market)
            if option:
                output.append(option)
    return output


def find_unique_option(options: list[MarketOption], *, market_type: str,
                       selection: str, line: str = "") -> MarketOption:
    target_line = line.replace("−", "-").replace("＋", "+").replace(" ", "")
    matches = [o for o in options if o.market_type == market_type
               and _norm(o.selection) == _norm(selection)
               and o.line.replace("−", "-").replace(" ", "") == target_line]
    if len(matches) != 1:
        raise ValueError(f"目标盘口不唯一或不存在：{market_type}/{selection}/{line}，匹配 {len(matches)} 项")
    return matches[0]


def open_selection_slip(driver, option: MarketOption) -> None:
    """Open the selected option's slip by its exact accessibility label only.

    This opens the review panel, not the final wager action. This function never
    fills an amount and never presses a submit/place-bet control.
    """
    from appium.webdriver.common.appiumby import AppiumBy
    matches = driver.find_elements(
        AppiumBy.XPATH,
        f"//*[@content-desc={_xpath_literal(option.accessibility_label)}]"
    )
    if len(matches) != 1:
        raise ValueError(f"盘口控件匹配到 {len(matches)} 个元素，拒绝点击")
    matches[0].click()


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    parts = value.split("'")
    return "concat(" + ', "\\'", '.join("'" + p + "'" for p in parts) + ")"
