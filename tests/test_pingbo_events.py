from datetime import datetime, timezone

import dual_platform.pingbo_events as pingbo_events


def test_fetch_pingbo_events_reuses_existing_endpoint_and_parser(tmp_path, monkeypatch):
    config = tmp_path / "refresh_config.json"
    config.write_text(
        '{"part567":{"events_params":{"locale":"zh_CN","mk":"1"}}}',
        encoding="utf-8",
    )
    calls = {}

    def fake_request_json(context, url, method, headers, params=None, body=None, timeout_ms=15000):
        calls.update({
            "context": context, "url": url, "method": method,
            "headers": headers, "params": params, "timeout_ms": timeout_ms,
        })
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return [[
            "event-123", "Home FC", "Away FC", 0, now_ms,
            None, None, None, {"0": [], "1": []},
        ]]

    monkeypatch.setattr(pingbo_events.refresh, "request_json", fake_request_json)
    monkeypatch.setattr(pingbo_events.refresh, "headers_env", lambda name: {"Accept": "application/json"})
    context = object()
    result = pingbo_events.fetch_pingbo_events(context, config_path=config)

    assert calls["context"] is context
    assert calls["url"].endswith("/sports-service/sv/compact/events")
    assert calls["method"] == "GET"
    assert calls["params"]["locale"] == "zh_CN"
    assert result["count"] == 1
    assert result["events"][0]["event_id"] == "event-123"
    assert result["events"][0]["home"] == "Home FC"
    assert result["events"][0]["away"] == "Away FC"
