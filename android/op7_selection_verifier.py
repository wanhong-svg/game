"""Verify OP7's selected slip against the intended event and market option.

The slip is read-only. No stake is entered and no wager/submit button is clicked.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(c for c in value if c.isalnum())


@dataclass(frozen=True)
class ExpectedSelection:
    league: str
    home: str
    away: str
    market_label: str
    selection: str
    line: str
    odds: str


@dataclass(frozen=True)
class SlipSnapshot:
    league: str
    home: str
    away: str
    market_label: str
    selection_text: str
    line: str
    odds: str
    score: str


def read_selection_slip(source: str) -> SlipSnapshot:
    """Extract visible accessibility/text fields from the opened slip panel."""
    root = ET.fromstring(source)
    values = []
    for node in root.iter():
        text = (node.attrib.get("content-desc") or node.attrib.get("text") or "").strip()
        if text:
            values.append(text)
    joined = "\n".join(values)
    market = next((x for x in values if x in {"全场独赢", "全场让分", "全场让球", "全场大小"}), "")
    league = next((x for x in values if x in {"NBA", "WNBA"}), "")
    odds = ""
    # OP7 slip accessibility tree exposes odds as a separate numeric view.
    for value in values:
        if re.fullmatch(r"\d+\.\d{1,4}", value):
            odds = value
    matchup = next((x for x in values if re.search(r"\S+\s+VS\s+\S+", x, re.I)), "")
    if not matchup:
        raise ValueError("注单面板未找到赛事主客队字段")
    teams = re.split(r"\s+VS\s+", matchup, maxsplit=1, flags=re.I)
    if len(teams) != 2:
        raise ValueError("注单面板赛事队伍格式无法识别")
    selection_line = next((x for x in values if re.search(r"\([^)]*\)\s*.+[+\-−]\d+(?:\.\d+)?$", x)), "")
    score = ""
    selection_text = selection_line
    line = ""
    if selection_line:
        score_match = re.search(r"\((\d{1,3}:\d{1,3})\)", selection_line)
        score = score_match.group(1) if score_match else ""
        line_match = re.search(r"([+\-−]\d+(?:\.\d+)?)$", selection_line)
        line = line_match.group(1).replace("−", "-") if line_match else ""
        selection_text = re.sub(r"^\([^)]*\)\s*", "", selection_line)
    if not market or not league or not odds or not line:
        raise ValueError("注单面板字段不完整：需要联赛、盘口、赔率和盘口数值")
    return SlipSnapshot(league, teams[0].strip(), teams[1].strip(), market,
                        selection_text, line, odds, score)


def verify_selection(expected: ExpectedSelection, actual: SlipSnapshot) -> None:
    """Raise on any mismatch; team order and selected side must match exactly."""
    errors = []
    for name, wanted, got in (
        ("联赛", expected.league, actual.league),
        ("盘口类型", expected.market_label, actual.market_label),
        ("主队", expected.home, actual.home),
        ("客队", expected.away, actual.away),
        ("盘口数值", expected.line.replace("−", "-"), actual.line.replace("−", "-")),
        ("赔率", expected.odds, actual.odds),
    ):
        if _norm(wanted) != _norm(got):
            errors.append(f"{name}不一致（目标={wanted!r}，实际={got!r}）")
    if _norm(expected.selection) not in _norm(actual.selection_text):
        errors.append(f"投注方向不一致（目标={expected.selection!r}，实际={actual.selection_text!r}）")
    if errors:
        raise ValueError("注单面板核验失败，停止采集： " + "；".join(errors))


def verify_current_slip(driver, expected: ExpectedSelection) -> SlipSnapshot:
    """Read and verify the current slip; never interact with stake/submit controls."""
    actual = read_selection_slip(driver.page_source)
    verify_selection(expected, actual)
    return actual
