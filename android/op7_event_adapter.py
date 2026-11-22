"""Configurable, offline adapter for a confirmed OP7 Android event-list response.

No endpoint, authentication, network request, or undocumented field layout is
assumed. Map the response only after inspecting a redacted real app response.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _path_value(obj: Any, path: str) -> Any:
    value = obj
    if not path:
        return value
    for part in path.split("."):
        if isinstance(value, dict):
            if part not in value:
                return None
            value = value[part]
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            if index >= len(value):
                return None
            value = value[index]
        else:
            return None
    return value


def load_mapping(path: str | Path) -> dict:
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"events_path", "fields"}
    if not isinstance(mapping, dict) or not required.issubset(mapping):
        raise ValueError("mapping must define events_path and fields")
    fields = mapping["fields"]
    if not isinstance(fields, dict) or not all(k in fields for k in ("event_id", "home", "away")):
        raise ValueError("fields must map event_id, home and away")
    return mapping


def normalize_event_response(payload: Any, mapping: dict, observed_at_utc: str | None = None) -> list[dict]:
    """Convert a confirmed app response into mapper-compatible event rows."""
    rows = _path_value(payload, mapping["events_path"])
    if not isinstance(rows, list) or not rows:
        raise ValueError("Android event response is empty or does not match the confirmed mapping")
    fields = mapping["fields"]
    observed = observed_at_utc or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    output = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"event at index {index} is not an object")
        item = {name: _path_value(raw, source_path) for name, source_path in fields.items()}
        for required in ("event_id", "home", "away"):
            if item.get(required) is None or str(item[required]).strip() == "":
                raise ValueError(f"event at index {index} is missing required field: {required}")
        start = item.get("start_time_ms")
        if start is not None:
            try:
                start = int(start)
            except (TypeError, ValueError):
                raise ValueError(f"event at index {index} has invalid start_time_ms")
        output.append({
            "venue": "ANDROID_APP",
            "event_id": str(item["event_id"]),
            "league": item.get("league"),
            "sport": item.get("sport"),
            "home": str(item["home"]).strip(),
            "away": str(item["away"]).strip(),
            "start_time_ms": start,
            "raw_match_status": item.get("raw_status"),
            "observed_at_utc": observed,
        })
    return output
