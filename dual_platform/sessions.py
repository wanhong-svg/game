"""Launch OP7 Android and Pingbo browser; user performs both logins manually."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from android.appium_session import create_driver, close_driver


@dataclass
class DualSession:
    op7_driver: Any
    browser: Any
    context: Any
    page: Any
    playwright: Any

    def close(self) -> None:
        try:
            if self.op7_driver:
                close_driver(self.op7_driver)
        finally:
            try:
                if self.browser:
                    self.browser.close()
            finally:
                if self.playwright:
                    self.playwright.stop()


def launch_dual_sessions(pingbo_url: str | None = None, *, headless: bool = False) -> DualSession:
    """Connect to the running Android emulator and launch a visible Pingbo browser."""
    url = pingbo_url or os.getenv("PINGBO_URL")
    if not url:
        raise ValueError("请先设置 PINGBO_URL，或通过 --pingbo-url 指定 Pingbo 登录入口")
    from playwright.sync_api import sync_playwright

    driver = create_driver(no_reset=True)
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print(f"Pingbo 页面已打开：{page.url}")
        print(f"OP7 已连接：package={driver.current_package}, activity={driver.current_activity}")
        return DualSession(driver, browser, context, page, playwright)
    except Exception:
        close_driver(driver)
        playwright.stop()
        raise


def _visible_login_form(page) -> bool:
    try:
        return page.locator('input[type="password"]').count() > 0
    except Exception:
        return False


def browser_login_state(page) -> str:
    """Heuristic only; final confirmation remains with the user."""
    if _visible_login_form(page):
        return "LOGIN_FORM_VISIBLE"
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
        if any(x in text for x in ("logout", "sign out", "退出登录", "退出")):
            return "POSSIBLY_LOGGED_IN"
    except Exception:
        pass
    return "UNKNOWN"


def op7_login_state(driver) -> str:
    """Inspect visible hierarchy without reading credentials or tapping controls."""
    try:
        source = driver.page_source.lower()
    except Exception:
        return "UNKNOWN"
    if "password" in source or "密码" in source or "login" in source or "登录" in source:
        return "LOGIN_FORM_OR_LOGIN_TEXT_VISIBLE"
    return "UNKNOWN"


def wait_for_manual_login(session: DualSession) -> None:
    print("\n登录状态检测（启发式，不读取密码/token）：")
    print(f"OP7：{op7_login_state(session.op7_driver)}")
    print(f"Pingbo：{browser_login_state(session.page)}")
    print("请分别在 Android OP7 和 Pingbo 浏览器中手动登录。")
    input("确认两个端都已成功登录后，按 Enter 继续；未成功请勿继续：")
    if not session.op7_driver.current_package:
        raise RuntimeError("OP7 会话不可用，停止后续流程")
    if session.page.is_closed():
        raise RuntimeError("Pingbo 页面已关闭，停止后续流程")
    print("用户已确认双端登录；进入下一阶段。")
