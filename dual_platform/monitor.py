"""Continuous read-only odds comparison loop; adapters are supplied by platform UI."""
from __future__ import annotations

import time
from typing import Callable, Iterable

from .core import AuditStore, MarketQuote, compare_quotes


def compare_cycle(op7_quotes: Iterable[MarketQuote], pingbo_quotes: Iterable[MarketQuote],
                  store: AuditStore, *, event_pairs: dict[str, str],
                  max_age_seconds: int = 30) -> list[dict]:
    """Compare only quotes belonging to a confirmed one-to-one event pair."""
    right: dict[tuple[str, tuple], list[MarketQuote]] = {}
    for quote in pingbo_quotes:
        right.setdefault((quote.event_id, quote.match_key), []).append(quote)
    results = []
    for left in op7_quotes:
        paired_id = event_pairs.get(left.event_id)
        if not paired_id:
            store.alert("WARNING", "EVENT_NOT_UNIQUELY_MATCHED",
                        f"OP7赛事 {left.event_id} 没有唯一 Pingbo 赛事映射")
            continue
        candidates = right.get((paired_id, left.match_key), [])
        if len(candidates) != 1:
            code = "NO_EXACT_MARKET_PAIR" if not candidates else "AMBIGUOUS_MARKET_PAIR"
            store.alert("WARNING", code, f"盘口未唯一匹配：赛事={left.event_id}，盘口={left.match_key}")
            continue
        try:
            result = compare_quotes(left, candidates[0], max_age_seconds=max_age_seconds)
        except ValueError as exc:
            store.alert("WARNING", "QUOTE_REJECTED", str(exc))
            continue
        store.save_snapshot(result)
        results.append(result)
    return results


def run_monitor(read_op7: Callable[[], Iterable[MarketQuote]],
                read_pingbo: Callable[[], Iterable[MarketQuote]],
                store: AuditStore, *, event_pairs: dict[str, str],
                interval_seconds: float = 2.0,
                max_age_seconds: int = 30, cycles: int = 0) -> None:
    """Run until Ctrl+C (cycles=0) or a finite number of observation cycles.

    Platform-specific readers must return structured quotes from visible UI or
    documented read-only data. This loop intentionally has no order/bet API.
    """
    if interval_seconds < 0.5:
        raise ValueError("轮询间隔不能低于 0.5 秒")
    count = 0
    while cycles == 0 or count < cycles:
        try:
            a, b = list(read_op7()), list(read_pingbo())
            results = compare_cycle(a, b, store, event_pairs=event_pairs,
                                    max_age_seconds=max_age_seconds)
            print(f"监控周期 {count + 1}：OP7={len(a)} 条，Pingbo={len(b)} 条，可比较={len(results)} 条")
        except Exception as exc:
            store.alert("ERROR", "COLLECTION_FAILED", f"本周期采集失败：{type(exc).__name__}: {exc}")
            print(f"本周期采集失败，已暂停有效比较：{type(exc).__name__}: {exc}")
        count += 1
        time.sleep(interval_seconds)
