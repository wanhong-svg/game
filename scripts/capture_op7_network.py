#!/usr/bin/env python3
"""Capture OP7 emulator network traffic to a local PCAP for API discovery.

This is a transport-level diagnostic only. HTTPS payloads remain encrypted;
the capture does not bypass TLS, certificate pinning, or app security controls.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_adb(adb: str, udid: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adb, "-s", udid, *args], capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def find_tcpdump(adb: str, udid: str) -> tuple[list[str], str]:
    """Return a shell prefix and tcpdump path, preferring root when available."""
    probes = [
        (["shell", "su", "-c", "command -v tcpdump"], ["exec-out", "su", "-c"]),
        (["shell", "command", "-v", "tcpdump"], ["exec-out"]),
    ]
    for probe, prefix in probes:
        result = run_adb(adb, udid, *probe)
        candidate = result.stdout.strip().splitlines()
        if result.returncode == 0 and candidate and candidate[-1].startswith("/"):
            return prefix, candidate[-1]
    raise RuntimeError(
        "模拟器中未找到可执行的 tcpdump。请使用可授权 root 的调试模拟器，"
        "在模拟器内安装 tcpdump 后重试；脚本不会尝试提权或绕过设备安全限制。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture OP7 Android emulator network traffic locally (PCAP)"
    )
    parser.add_argument("--udid", default=os.getenv("ANDROID_UDID", "emulator-5554"))
    parser.add_argument("--package", default=os.getenv("OP7_APP_PACKAGE", "com.seaapp.prod"))
    parser.add_argument("--duration", type=int, default=60, help="Capture seconds (1-300)")
    parser.add_argument("--output", default=None, help="Local PCAP path; defaults to /tmp")
    args = parser.parse_args()

    if not 1 <= args.duration <= 300:
        parser.error("--duration 必须在 1 到 300 秒之间")

    adb = shutil.which("adb")
    if not adb:
        print("错误：找不到 adb，请安装 Android Platform Tools 并加入 PATH。", file=sys.stderr)
        return 2

    state = run_adb(adb, args.udid, "get-state")
    if state.returncode or state.stdout.strip() != "device":
        print(f"错误：设备 {args.udid} 未连接或未授权。", file=sys.stderr)
        return 2

    pkg = run_adb(adb, args.udid, "shell", "dumpsys", "package", args.package)
    if pkg.returncode or args.package not in pkg.stdout:
        print(f"错误：模拟器中未找到包 {args.package}。", file=sys.stderr)
        return 2
    uid_match = re.search(r"\buserId=(\d+)", pkg.stdout)
    print(f"目标 APP：{args.package}")
    print(f"APP UID：{uid_match.group(1) if uid_match else '未能识别'}")
    print("注意：tcpdump 捕获的是模拟器网络接口流量，可能包含其他 APP 的连接。")
    print("HTTPS 内容仍为加密数据；本脚本不解密 TLS，也不绕过证书固定。")

    try:
        prefix, tcpdump_path = find_tcpdump(adb, args.udid)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser() if args.output else Path(
        f"/tmp/op7-network-{time.strftime('%Y%m%d-%H%M%S')}.pcap"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [*prefix, tcpdump_path, "-i", "any", "-s", "0", "-U", "-w", "-"]
    print(f"开始抓取 {args.duration} 秒；请立即在 OP7 中进入/刷新赛事列表。")
    print(f"PCAP 保存位置：{output}")
    print("抓包可能包含敏感网络元数据，请勿直接公开或提交到 Git。")

    try:
        with output.open("wb") as capture:
            proc = subprocess.Popen(
                [adb, "-s", args.udid, *command],
                stdout=capture, stderr=subprocess.PIPE,
            )
            try:
                time.sleep(args.duration)
            except KeyboardInterrupt:
                print("\n收到中断，正在停止抓包……")
            finally:
                proc.terminate()
                try:
                    _, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, stderr = proc.communicate()
        if not output.exists() or output.stat().st_size < 24:
            detail = stderr.decode("utf-8", "replace")[-1200:] if stderr else ""
            raise RuntimeError(f"未生成有效 PCAP。{detail}")
        print(f"抓包完成：{output}（{output.stat().st_size} bytes）")
        print("下一步：在本机用 Wireshark 查看 DNS/TLS SNI/目标主机；HTTPS 请求路径和 JSON 响应无法从加密流量直接读取。")
        return 0
    except Exception as exc:
        print(f"抓包失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
