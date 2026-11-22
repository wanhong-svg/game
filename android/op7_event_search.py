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
    # A colon belongs to the game clock (08:46), not the score.
    match = re.search(r"(?<!\d)(\d{1,3})\s*[-–—]\s*(\d{1,3})(?!\d)", text)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def parse_detail_event(source: str) -> EventSnapshot:
    """Parse the event header even when XML normalizes attribute newlines to spaces."""
    root = ET.fromstring(source)
    league_candidates = []
    for node in root.iter():
        label = (node.attrib.get("content-desc") or node.attrib.get("text") or "").strip()
        if re.fullmatch(r"[A-Z][A-Z0-9 ._-]{1,15}", label):
            league_candidates.append(label)
    league_values = list(dict.fromkeys(league_candidates))
    league = league_values[0] if len(league_values) == 1 else ""
    candidates = []
    for node in root.iter():
        desc = node.attrib.get("content-desc", "").strip()
        if not desc:
            continue
        flat = re.sub(r"\s+", " ", desc)
        score_match = re.search(r"(?<!\d)(\d{1,3})\s*[-–—]\s*(\d{1,3})(?!\d)", flat)
        if not score_match:
            continue
        prefix = flat[:score_match.start()].strip()
        suffix = flat[score_match.end():].strip()
        # The detail header order is home, period, clock, score, away.
        progress = re.search(r"(第[一二三四五六七八九十\d]+节|第[一二三四五六七八九十\d]+局|上半场|下半场|加时)\s+(\d{1,2}:\d{2})$", prefix)
        if not progress or not prefix[:progress.start()].strip() or not suffix:
            continue
        candidates.append(EventSnapshot(
            league=league,
            home=prefix[:progress.start()].strip(),
            away=suffix,
            home_score=int(score_match.group(1)),
            away_score=int(score_match.group(2)),
            period=progress.group(1),
            clock=progress.group(2),
            event_label=desc,
        ))
    if not candidates:
        raise ValueError("未能从当前 UI 控件树唯一读取赛事详情；请确认处于赛事详情页")
    unique = {(x.home, x.away, x.home_score, x.away_score, x.period, x.clock) for x in candidates}
    if len(unique) != 1:
        raise ValueError("赛事详情控件树存在多个候选赛事头部，拒绝猜测")
    return candidates[0]


def parse_search_results(source: str) -> list[EventSnapshot]:
    """Parse search rows whose XML attribute whitespace may be normalized."""
    root = ET.fromstring(source)
    results: list[EventSnapshot] = []
    seen = set()
    for node in root.iter():
        desc = node.attrib.get("content-desc", "").strip()
        if not desc:
            continue
        flat = re.sub(r"\s+", " ", desc)
        matchup = re.search(r"(.+?)\s+VS\s+(.+)$", flat, re.I)
        if not matchup:
            continue
        prefix = flat[:matchup.start()].strip()
        progress_match = re.search(
            r"(第[一二三四五六七八九十\d]+节|第[一二三四五六七八九十\d]+局|上半场|下半场|加时)\s+(\d{1,2}:\d{2})\s*$",
            prefix,
        )
        kickoff_match = re.search(r"\b\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s*$", prefix)
        marker = progress_match or kickoff_match
        league = prefix[:marker.start()].strip() if marker else prefix
        period = progress_match.group(1) if progress_match else ""
        clock = progress_match.group(2) if progress_match else ""
        event = EventSnapshot(league, matchup.group(1).strip(), matchup.group(2).strip(),
                              None, None, period, clock, desc)
        key = (norm(league), norm(event.home), norm(event.away), period, clock)
        if key not in seen:
            results.append(event)
            seen.add(key)
    return results

def _clock_seconds(value: str) -> Optional[int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds > 59:
        return None
    return minutes * 60 + seconds


def same_live_event(a: EventSnapshot, b: EventSnapshot,
                    clock_tolerance_seconds: int = 120) -> bool:
    """Require same competition, teams, period, near-synchronous clocks and score."""
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
    # Both clocks must be visible. Small refresh/network delays are expected,
    # but a materially different clock means the match is not confirmed.
    if not a.clock or not b.clock:
        return False
    clock_a = _clock_seconds(a.clock)
    clock_b = _clock_seconds(b.clock)
    if (clock_tolerance_seconds < 0 or clock_a is None or clock_b is None
            or abs(clock_a - clock_b) > clock_tolerance_seconds):
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
    return "concat(" + ", \"'\", ".join("'" + p + "'" for p in parts) + ")"


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
