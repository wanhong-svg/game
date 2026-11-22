#!/usr/bin/env python3
"""Start both user-facing sessions and guide the read-only monitoring workflow."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_platform.sessions import launch_dual_sessions, wait_for_manual_login
from dual_platform.core import AuditStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch OP7 Android + Pingbo browser sessions")
    parser.add_argument("--pingbo-url", default=os.getenv("PINGBO_URL"), help="Pingbo login URL")
    parser.add_argument("--db", default="dual_platform_monitor.sqlite3")
    parser.add_argument("--headless", action="store_true", help="Launch browser headless (not recommended for manual login)")
    args = parser.parse_args()
    session = None
    store = None
    try:
        session = launch_dual_sessions(args.pingbo_url, headless=args.headless)
        wait_for_manual_login(session)
        store = AuditStore(args.db)
        print("双端会话已就绪。下一阶段需要从实际页面确认赛事搜索控件与结果结构。")
        print("当前版本不会自动提交投注，也不会猜测未确认的页面选择器/API。")
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
                session._playwright.stop()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
