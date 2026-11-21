#!/usr/bin/env python3
"""Connect to OP7 in the Android emulator and inspect its native UI tree.

The page source can contain account-specific text. It is printed to the local
terminal only; this script does not upload or commit inspection output.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from android.appium_session import (  # noqa: E402
    DEFAULT_PACKAGE,
    DEFAULT_SERVER_URL,
    DEFAULT_UDID,
    close_driver,
    create_driver,
)


def adb_output(udid: str, *args: str) -> str:
    cmd = ["adb", "-s", udid, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        return f"ADB_ERROR: {result.stderr.strip()}"
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect OP7 native Android app")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--udid", default=DEFAULT_UDID)
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--output", help="Optional local path for UI hierarchy XML")
    args = parser.parse_args()

    print("== Android device ==")
    print(adb_output(args.udid, "get-state"))
    print(adb_output(args.udid, "shell", "getprop", "ro.build.version.release"))
    print("\n== App package ==")
    print(adb_output(args.udid, "shell", "dumpsys", "package", args.package).split("User 0:")[0][:2500])

    driver = None
    try:
        driver = create_driver(
            server_url=args.server,
            udid=args.udid,
            app_package=args.package,
        )
        source = driver.page_source
        print("\n== Appium session ==")
        print(json.dumps({
            "package": driver.current_package,
            "activity": driver.current_activity,
            "page_source_characters": len(source),
        }, ensure_ascii=False, indent=2))
        if args.output:
            target = Path(args.output).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
            print(f"Native hierarchy saved locally: {target}")
        else:
            print("\n== Native UI hierarchy (first 12000 chars) ==")
            print(source[:12000])
        return 0
    except Exception as exc:
        print(f"Appium inspection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_driver(driver)


if __name__ == "__main__":
    raise SystemExit(main())
