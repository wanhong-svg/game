"""Continuous read-only odds comparison loop; adapters are supplied by platform UI."""
from __future__ import annotations

import time
from typing import Callable, Iterable

from .core import AuditStore, MarketQuote, compare_quotes


def compare_cycle(op7_quotes: Iterable[MarketQuote], pingbo_quotes: Iterable[MarketQuote],
                  store: AuditStore, *, max_age_seconds: int = 30) -> list[dict]:
    """Compare only exact shared keys and persist each observation; no bet actions."""
    right = {q.match_key: q for q in pingbo_quotes}
    results = []
    for left in op7_quotes:
        other = right.get(left.match_key)
        if other is None:
            store.alert("WARNING", "NO_EXACT_MARKET_PAIR", f"盘口未唯一匹配：{left.match_key}")
            continue
        try:
            result = compare_quotes(left, other, max_age_seconds=max_age_seconds)
        except ValueError as exc:
            store.alert("WARNING", "QUOTE_REJECTED", str(exc))
            continue
        store.save_snapshot(result)
        results.append(result)
    return results


def run_monitor(read_op7: Callable[[], Iterable[MarketQuote]],
                read_pingbo: Callable[[], Iterable[MarketQuote]],
                store: AuditStore, *, interval_seconds: float = 2.0,
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
            results = compare_cycle(a, b, store, max_age_seconds=max_age_seconds)
            print(f"监控周期 {count + 1}：OP7={len(a)} 条，Pingbo={len(b)} 条，可比较={len(results)} 条")
        except Exception as exc:
            store.alert("ERROR", "COLLECTION_FAILED", f"本周期采集失败：{type(exc).__name__}: {exc}")
            print(f"本周期采集失败，已暂停有效比较：{type(exc).__name__}: {exc}")
        count += 1
        time.sleep(interval_seconds)
