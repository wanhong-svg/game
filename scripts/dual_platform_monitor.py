#!/usr/bin/env python3
"""Start OP7 + Pingbo sessions and fetch Pingbo events via the existing API."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_platform.sessions import launch_dual_sessions, wait_for_manual_login
from dual_platform.core import AuditStore
from dual_platform.pingbo_events import fetch_pingbo_events


def prompt_pingbo_url() -> str:
    while True:
        value = input("请输入 Pingbo 网站地址（登录入口 URL）：").strip()
        parsed = urlparse(value)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return value
        print("地址格式不正确，请输入完整的 http:// 或 https:// 网站地址。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch OP7 Android + Pingbo browser sessions")
    parser.add_argument("--pingbo-url", default=os.getenv("PINGBO_URL"),
                        help="Optional Pingbo login URL; omitted means prompt in terminal")
    parser.add_argument("--db", default="dual_platform_monitor.sqlite3")
    parser.add_argument("--refresh-config", default="refresh_config.json",
                        help="Local config containing part567.events_params; falls back to refresh_config.example.json")
    parser.add_argument("--headless", action="store_true",
                        help="Launch browser headless (not recommended for manual login)")
    args = parser.parse_args()
    session = None
    store = None
    try:
        pingbo_url = args.pingbo_url or prompt_pingbo_url()
        parsed = urlparse(pingbo_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("--pingbo-url 必须是完整的 http:// 或 https:// 地址")
        session = launch_dual_sessions(pingbo_url, headless=args.headless)
        wait_for_manual_login(session)

        store = AuditStore(args.db)
        print("\n双端登录已由用户确认。")
        print("正在调用现有 Pingbo 赛事列表接口 /sports-service/sv/compact/events ...")
        pingbo_snapshot = fetch_pingbo_events(
            session.context, config_path=args.refresh_config
        )
        store.save_snapshot(pingbo_snapshot)

        print(f"Pingbo 接口获取成功：{pingbo_snapshot['count']} 场赛事")
        for event in pingbo_snapshot["events"][:30]:
            print(
                f"  [{event['event_id']}] {event['home']} vs {event['away']} "
                f"| 开赛={event['start_time_utc'] or '接口未提供'}"
            )
        if pingbo_snapshot["count"] > 30:
            print(f"  ... 另有 {pingbo_snapshot['count'] - 30} 场未展开显示")

        print("Pingbo 赛事数据已写入 SQLite。")
        print("下一步将接入 OP7 赛事列表来源，再执行系统唯一匹配。")
        print("当前程序仅读取赛事数据，不会自动提交投注。")
        print(f"审计数据库：{args.db}")
        input("按 Enter 关闭双端会话：")
        return 0
    except KeyboardInterrupt:
        print("\n用户中断。")
        return 130
    except Exception as exc:
        print(f"启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()
        if session:
            try:
                session.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
