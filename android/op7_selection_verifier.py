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
    score: str = ""


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
    odds_candidates = []
    # In the observed OP7 slip, the price is the numeric node immediately
    # following the standalone "@" accessibility node (or "@0.88" itself).
    for index, value in enumerate(values):
        inline = re.fullmatch(r"@\s*(\d+\.\d{1,4})", value)
        if inline:
            odds_candidates.append(inline.group(1))
        elif value == "@" and index + 1 < len(values):
            following = values[index + 1]
            if re.fullmatch(r"\d+\.\d{1,4}", following):
                odds_candidates.append(following)
    odds = odds_candidates[0] if len(odds_candidates) == 1 else ""
    matchup = next((x for x in values if re.search(r"\S+\s+VS\s+\S+", x, re.I)), "")
    if not matchup:
        raise ValueError("注单面板未找到赛事主客队字段")
    teams = re.split(r"\s+VS\s+", matchup, maxsplit=1, flags=re.I)
    if len(teams) != 2:
        raise ValueError("注单面板赛事队伍格式无法识别")
    selection_line = next((x for x in values if re.search(r"^\([^)]*\)\s*.+$", x)), "")
    score = ""
    selection_text = ""
    line = ""
    if selection_line:
        score_match = re.search(r"\((\d{1,3}:\d{1,3})\)", selection_line)
        score = score_match.group(1) if score_match else ""
        selection_text = re.sub(r"^\([^)]*\)\s*", "", selection_line).strip()
        if market in {"全场让分", "全场让球"}:
            line_match = re.search(r"([+\-−]\d+(?:\.\d+)?)$", selection_text)
            line = line_match.group(1).replace("−", "-") if line_match else ""
        elif market == "全场大小":
            line_match = re.search(r"(\d+(?:\.\d+)?)$", selection_text)
            line = line_match.group(1) if line_match else ""
    if not market or not league or not odds or not selection_text:
        raise ValueError("注单面板字段不完整：需要联赛、盘口、赔率和已选项目")
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
    side = _norm(expected.selection)
    if side in {"主", "主队", "home"}:
        expected_pick = expected.home
    elif side in {"客", "客队", "away"}:
        expected_pick = expected.away
    else:
        expected_pick = expected.selection
    if _norm(expected_pick) not in _norm(actual.selection_text):
        errors.append(f"投注方向不一致（目标={expected.selection!r}，实际={actual.selection_text!r}）")
    if expected.score and expected.score.replace("：", ":") != actual.score.replace("：", ":"):
        errors.append(f"比分不一致（目标={expected.score!r}，实际={actual.score!r}）")
    if errors:
        raise ValueError("注单面板核验失败，停止采集： " + "；".join(errors))


def verify_current_slip(driver, expected: ExpectedSelection) -> SlipSnapshot:
    """Read and verify the current slip; never interact with stake/submit controls."""
    actual = read_selection_slip(driver.page_source)
    verify_selection(expected, actual)
    return actual
