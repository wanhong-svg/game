#!/usr/bin/env python3
"""Refresh the OP7 Android event-list screen using a safe pull-down gesture.

Navigate to the event-list screen manually before running. This script does not
inspect or click betting controls and does not make network/API requests.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from android.appium_session import DEFAULT_PACKAGE, DEFAULT_SERVER_URL, DEFAULT_UDID, create_driver, close_driver  # noqa: E402


def pull_to_refresh(driver, top_ratio: float = 0.16, height_ratio: float = 0.62,
                    width_ratio: float = 0.88, percent: float = 0.72) -> None:
    """Swipe down inside the central event-list region to trigger refresh."""
    size = driver.get_window_size()
    width, height = int(size["width"]), int(size["height"])
    left = int(width * (1 - width_ratio) / 2)
    top = int(height * top_ratio)
    area_width = int(width * width_ratio)
    area_height = min(int(height * height_ratio), height - top - 1)
    if min(area_width, area_height) <= 0:
        raise ValueError("Invalid swipe region; check device dimensions and ratios")
    driver.execute_script("mobile: swipeGesture", {
        "left": left, "top": top, "width": area_width, "height": area_height,
        "direction": "down", "percent": percent,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull-to-refresh OP7 Android event list")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--count", type=int, default=1, help="Number of refresh gestures; 0 runs until Ctrl+C")
    parser.add_argument("--interval", type=float, default=20, help="Seconds between gestures (minimum 5)")
    parser.add_argument("--wait-after-refresh", type=float, default=3, help="Wait for the list to settle")
    parser.add_argument("--top-ratio", type=float, default=0.16)
    parser.add_argument("--height-ratio", type=float, default=0.62)
    parser.add_argument("--width-ratio", type=float, default=0.88)
    parser.add_argument("--swipe-percent", type=float, default=0.72)
    args = parser.parse_args()
    if args.count < 0 or args.interval < 5 or args.wait_after_refresh < 0:
        parser.error("--count must be >= 0, --interval >= 5 and --wait-after-refresh >= 0")
    for value in (args.top_ratio, args.height_ratio, args.width_ratio, args.swipe_percent):
        if not 0 < value <= 1:
            parser.error("gesture ratios must be in (0, 1]")

    driver = None
    try:
        driver = create_driver(args.server, args.udid, args.package, no_reset=True)
        print(f"Connected: package={driver.current_package}, activity={driver.current_activity}")
        print("请确认模拟器当前显示 OP7 赛事列表，并且列表已滚动到顶部。")
        print("只执行列表区域下拉刷新手势；不会点击盘口、投注或账户控件。")
        completed = 0
        while args.count == 0 or completed < args.count:
            pull_to_refresh(driver, args.top_ratio, args.height_ratio,
                            args.width_ratio, args.swipe_percent)
            completed += 1
            time.sleep(args.wait_after_refresh)
            print(f"refresh gesture {completed} sent; activity={driver.current_activity}")
            if args.count == 0 or completed < args.count:
                time.sleep(max(0, args.interval - args.wait_after_refresh))
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130
    except Exception as exc:
        print(f"Appium refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
