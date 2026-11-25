#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decode an EB API response whose JSON envelope contains Base64 + GZIP data.

Usage:
  python3 decode_eb_response.py eb_response.json
  python3 decode_eb_response.py eb_response.json -o eb_response.decoded.json

The input file must contain the complete JSON response. The data value is
extracted with json.loads(), so surrounding JSON quotation marks are not part
of the Base64 input. The script never prints the encoded payload or decoded
event contents.
"""
import argparse
import base64
import binascii
import gzip
import json
import zlib
from pathlib import Path
from urllib.parse import unquote


def decode_data(value):
    if not isinstance(value, str):
        raise TypeError("响应中的 data 字段必须是字符串")

    encoded = "".join(value.split()).replace("-", "+").replace("_", "/")
    if not encoded:
        raise ValueError("响应中的 data 字段为空")

    try:
        packed = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise ValueError("data 字段不是有效的 Base64 数据") from None

    try:
        try:
            raw = gzip.decompress(packed)
            compression = "gzip"
        except (OSError, EOFError):
            raw = zlib.decompress(packed)
            compression = "zlib"

        decoded_text = raw.decode("utf-8")
        try:
            payload = json.loads(decoded_text)
        except json.JSONDecodeError:
            payload = json.loads(unquote(decoded_text))
    except (OSError, EOFError, zlib.error, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Base64 解码成功，但压缩数据或解压后的 JSON 无效") from None

    return payload, len(encoded), len(packed), len(raw), compression


def main():
    parser = argparse.ArgumentParser(
        description="提取 EB 响应的 data 字段并解码 Base64 + GZIP/zlib JSON"
    )
    parser.add_argument("input", help="完整接口响应 JSON 文件，例如 eb_response.json")
    parser.add_argument(
        "-o", "--output",
        help="输出文件；默认写入 eb_response.decoded.json",
        default="eb_response.decoded.json",
    )
    args = parser.parse_args()

    source = Path(args.input)
    target = Path(args.output)
    envelope = json.loads(source.read_text(encoding="utf-8"))

    if not isinstance(envelope, dict) or "data" not in envelope:
        raise ValueError("输入文件不是包含 data 字段的 JSON 响应对象")

    if isinstance(envelope["data"], str):
        payload, chars, packed_size, raw_size, compression = decode_data(envelope["data"])
        result = dict(envelope)
        result["data"] = payload
        print("data 字段解码成功")
        print(f"压缩格式：{compression}")
        print(f"Base64 字符数：{chars}")
        print(f"压缩后字节数：{packed_size}")
        print(f"解压后字节数：{raw_size}")
        print(f"解码后 data 类型：{type(payload).__name__}")
        if isinstance(payload, dict):
            print("data 顶层字段：", ", ".join(sorted(map(str, payload.keys()))[:60]))
        elif isinstance(payload, list):
            print(f"data 数组记录数：{len(payload)}")
    else:
        result = envelope
        print(f"data 字段已是解析后的 {type(envelope['data']).__name__}，无需 Base64 解码")

    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已写入：{target}")


if __name__ == "__main__":
    main()
