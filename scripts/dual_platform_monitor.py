#!/usr/bin/env python3
"""Start both user-facing sessions and guide the read-only monitoring workflow."""
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
        print("双端会话已就绪。下一阶段需要从实际页面确认赛事搜索控件与结果结构。")
        print("当前版本不会自动提交投注，也不会猜测未确认的页面选择器/API。")
        print(f"审计数据库：{args.db}")
        input("按 Enter 关闭双端会话：")
        return 0
    except KeyboardInterrupt:
        print("\\n用户中断。")
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
