"""Read and search OP7 event UI; matching is strict and fails closed.

Only visible UI metadata is read. No credentials, network tokens, or betting APIs
are accessed. Coordinates are never used for element selection.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


def norm(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in value if ch.isalnum())


@dataclass(frozen=True)
class EventSnapshot:
    league: str
    home: str
    away: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    period: str = ""
    clock: str = ""
    event_label: str = ""


def _score_pair(text: str) -> tuple[Optional[int], Optional[int]]:
    match = re.search(r"(?<!\d)(\d{1,3})\s*[-:：]\s*(\d{1,3})(?!\d)", text)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def parse_detail_event(source: str) -> EventSnapshot:
    """Parse the event header descriptor seen on OP7 detail page."""
    root = ET.fromstring(source)
    candidates = []
    for node in root.iter():
        desc = node.attrib.get("content-desc", "").strip()
        if not desc:
            continue
        lines = [x.strip() for x in desc.splitlines() if x.strip()]
        if len(lines) >= 4 and "VS" not in lines[0].upper():
            # Header commonly exposes: home, period, clock, score, away.
            score_index = next((i for i, x in enumerate(lines) if _score_pair(x) != (None, None)), None)
            if score_index is not None and score_index >= 1 and score_index + 1 < len(lines):
                progress_text = " ".join(lines[1:score_index])
                period_match = re.search(r"(第[一二三四五六七八九十\d]+节|第[一二三四五六七八九十\d]+局|上半场|下半场|加时)", progress_text)
                clock_match = re.search(r"\b\d{1,2}:\d{2}\b", progress_text)
                hs, aws = _score_pair(lines[score_index])
                candidates.append(EventSnapshot(
                    league="", home=lines[0], away=lines[score_index + 1],
                    home_score=hs, away_score=aws,
                    period=period_match.group(1) if period_match else period_clock,
                    clock=clock_match.group(0) if clock_match else "",
                    event_label=desc,
                ))
    if not candidates:
        raise ValueError("未能从当前 UI 控件树唯一读取赛事详情；请确认处于赛事详情页")
    # The largest descriptive header is normally the event header. Ambiguity is rejected.
    unique = {(x.home, x.away, x.home_score, x.away_score, x.period) for x in candidates}
    if len(unique) != 1:
        raise ValueError("赛事详情控件树存在多个候选赛事头部，拒绝猜测")
    return candidates[0]


def parse_search_results(source: str) -> list[EventSnapshot]:
    """Parse search result rows whose accessibility descriptions contain league + teams."""
    root = ET.fromstring(source)
    results: list[EventSnapshot] = []
    seen = set()
    for node in root.iter():
        desc = node.attrib.get("content-desc", "").strip()
        if not desc or "VS" not in desc.upper():
            continue
        lines = [x.strip() for x in desc.splitlines() if x.strip()]
        if len(lines) < 2:
            continue
        matchup_line = next((x for x in lines if re.search(r"\s+VS\s+", x, re.I)), "")
        if not matchup_line:
            continue
        teams = re.split(r"\s+VS\s+", matchup_line, maxsplit=1, flags=re.I)
        if len(teams) != 2:
            continue
        league = lines[0]
        period, clock = "", ""
        for line in lines[1:]:
            pm = re.search(r"(第[一二三四五六七八九十\d]+节|第[一二三四五六七八九十\d]+局|上半场|下半场|加时)", line)
            cm = re.search(r"\b\d{1,2}:\d{2}\b", line)
            if pm:
                period = pm.group(1)
            if cm:
                clock = cm.group(0)
        hs, aws = _score_pair(desc)
        event = EventSnapshot(league, teams[0].strip(), teams[1].strip(), hs, aws, period, clock, desc)
        key = (norm(league), norm(event.home), norm(event.away), hs, aws, period, clock)
        if key not in seen:
            results.append(event)
            seen.add(key)
    return results


def same_live_event(a: EventSnapshot, b: EventSnapshot) -> bool:
    """Require same competition, same two teams, same period and oriented score."""
    if not all((a.league, b.league, a.home, a.away, b.home, b.away)):
        return False
    if norm(a.league) != norm(b.league):
        return False
    same_order = norm(a.home) == norm(b.home) and norm(a.away) == norm(b.away)
    reverse_order = norm(a.home) == norm(b.away) and norm(a.away) == norm(b.home)
    if not (same_order or reverse_order):
        return False
    if not a.period or not b.period or norm(a.period) != norm(b.period):
        return False
    if None in (a.home_score, a.away_score, b.home_score, b.away_score):
        return False
    b_home, b_away = (b.home_score, b.away_score) if same_order else (b.away_score, b.home_score)
    return (a.home_score, a.away_score) == (b_home, b_away)


def find_unique_search_result(events: list[EventSnapshot], *, league: str,
                              home: str, away: str) -> EventSnapshot:
    matches = [e for e in events if norm(e.league) == norm(league)
               and ((norm(e.home) == norm(home) and norm(e.away) == norm(away))
                    or (norm(e.home) == norm(away) and norm(e.away) == norm(home)))]
    if len(matches) != 1:
        raise ValueError(f"赛事搜索结果不唯一：找到 {len(matches)} 场符合联赛和主客队条件的赛事")
    return matches[0]


def click_accessibility_label(driver, label: str):
    """Click a unique UI element by its accessibility label, never by coordinates."""
    from appium.webdriver.common.appiumby import AppiumBy
    matches = driver.find_elements(AppiumBy.XPATH, f"//*[@content-desc={_xpath_literal(label)}]")
    if len(matches) != 1:
        raise ValueError(f"控件标签 {label!r} 匹配到 {len(matches)} 个元素，拒绝点击")
    matches[0].click()
    return matches[0]


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    parts = value.split("'")
    return "concat(" + ', \"'\", '.join("'" + p + "'" for p in parts) + ")"


def search_by_visible_input(driver, query: str) -> None:
    """Fill the only visible EditText; does not submit or click guessed coordinates."""
    from appium.webdriver.common.appiumby import AppiumBy
    fields = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
    visible = [field for field in fields if field.is_displayed()]
    if len(visible) != 1:
        raise ValueError(f"当前可见输入框数量为 {len(visible)}，无法安全确定赛事搜索框")
    field = visible[0]
    field.click()
    field.clear()
    field.send_keys(query)
