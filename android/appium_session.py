"""Appium session helpers for the installed OP7 Android application.

This module only launches/inspects the app. It does not automate betting,
orders, wallet, deposits, withdrawals, or account mutations.
"""
from __future__ import annotations

import os
from typing import Optional

from appium import webdriver
from appium.options.android import UiAutomator2Options

DEFAULT_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
DEFAULT_UDID = os.getenv("ANDROID_UDID", "emulator-5554")
DEFAULT_PACKAGE = os.getenv("OP7_APP_PACKAGE", "com.seaapp.prod")


def create_driver(
    server_url: str = DEFAULT_SERVER_URL,
    udid: str = DEFAULT_UDID,
    app_package: str = DEFAULT_PACKAGE,
    no_reset: bool = True,
    command_timeout: int = 120,
):
    """Create a UiAutomator2 session for an already-installed app."""
    options = UiAutomator2Options()
    options.load_capabilities(
        {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": udid,
            "appium:deviceName": udid,
            "appium:appPackage": app_package,
            "appium:noReset": no_reset,
            "appium:newCommandTimeout": command_timeout,
            "appium:autoGrantPermissions": False,
        }
    )
    return webdriver.Remote(server_url, options=options)


def current_app_state(driver) -> dict:
    """Return non-secret app/session metadata and the native hierarchy."""
    return {
        "package": driver.current_package,
        "current_activity": driver.current_activity,
        "current_package": driver.current_package,
        "page_source": driver.page_source,
    }


def close_driver(driver: Optional[object]) -> None:
    if driver is not None:
        driver.quit()
