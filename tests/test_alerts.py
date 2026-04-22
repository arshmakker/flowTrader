"""Regression tests for LIVE-23 (operator alert channel)."""

from __future__ import annotations

import logging

import pytest

from trading_system.ops.alerts import (
    Alert,
    LogAlertChannel,
    NtfyAlertChannel,
    NullAlertChannel,
    build_channel,
)


class _FakeResp:
    def raise_for_status(self) -> None:
        return None


class TestNullAlertChannel:
    def test_records_sends_for_inspection(self):
        chan = NullAlertChannel()
        a = Alert("x", "info", "T", "B")
        assert chan.send(a) is True
        assert chan.sent == [a]


class TestLogAlertChannel:
    def test_emits_at_severity_level(self, caplog):
        chan = LogAlertChannel()
        with caplog.at_level(logging.WARNING, logger="trading_system.ops.alerts"):
            assert chan.send(Alert("x", "warning", "Title", "Body")) is True
        assert any("Title" in rec.message and "Body" in rec.message for rec in caplog.records)


class TestNtfyAlertChannel:
    def test_posts_to_topic_with_critical_headers(self, monkeypatch):
        calls: list[dict] = []

        def fake_post(url, data, headers, timeout):
            calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
            return _FakeResp()

        monkeypatch.setattr("trading_system.ops.alerts.requests.post", fake_post)
        chan = NtfyAlertChannel("https://ntfy.sh/test-topic")
        alert = Alert("combined_stop", "critical", "Title", "Body")
        assert chan.send(alert) is True
        chan._q.join()

        assert len(calls) == 1
        assert calls[0]["url"] == "https://ntfy.sh/test-topic"
        assert calls[0]["headers"]["Priority"] == "urgent"
        assert calls[0]["headers"]["Title"] == "Title"
        assert calls[0]["data"] == b"Body"

    def test_dedups_within_window(self, monkeypatch):
        calls: list[bytes] = []

        def fake_post(url, data, headers, timeout):
            calls.append(data)
            return _FakeResp()

        monkeypatch.setattr("trading_system.ops.alerts.requests.post", fake_post)
        chan = NtfyAlertChannel("https://ntfy.sh/test-topic")

        assert chan.send(Alert("combined_stop", "critical", "T", "body1")) is True
        # Second send of the same event within the 60s window is dropped.
        assert chan.send(Alert("combined_stop", "critical", "T", "body2")) is False

        chan._q.join()
        assert calls == [b"body1"]

    def test_distinct_events_are_not_deduped(self, monkeypatch):
        calls: list[bytes] = []

        def fake_post(url, data, headers, timeout):
            calls.append(data)
            return _FakeResp()

        monkeypatch.setattr("trading_system.ops.alerts.requests.post", fake_post)
        chan = NtfyAlertChannel("https://ntfy.sh/test-topic")

        assert chan.send(Alert("combined_stop", "critical", "T", "body1")) is True
        assert chan.send(Alert("daily_loss_cap", "critical", "T", "body2")) is True

        chan._q.join()
        assert sorted(calls) == [b"body1", b"body2"]

    def test_http_failure_does_not_raise(self, monkeypatch):
        def fake_post(url, data, headers, timeout):
            raise RuntimeError("network down")

        monkeypatch.setattr("trading_system.ops.alerts.requests.post", fake_post)
        chan = NtfyAlertChannel("https://ntfy.sh/test-topic")
        # send() itself returns True (enqueued); failure is swallowed in the worker.
        assert chan.send(Alert("combined_stop", "critical", "T", "B")) is True
        chan._q.join()  # worker must complete without propagating

    def test_empty_topic_url_raises(self):
        with pytest.raises(ValueError):
            NtfyAlertChannel("")


class TestBuildChannel:
    def test_disabled_returns_null(self):
        chan = build_channel(enabled=False, channel_type="ntfy", ntfy_topic_url="https://x.y")
        assert isinstance(chan, NullAlertChannel)

    def test_log_when_enabled(self):
        chan = build_channel(enabled=True, channel_type="log")
        assert isinstance(chan, LogAlertChannel)

    def test_ntfy_without_url_falls_back_to_log(self):
        chan = build_channel(enabled=True, channel_type="ntfy", ntfy_topic_url=None)
        assert isinstance(chan, LogAlertChannel)

    def test_ntfy_with_url_returns_ntfy(self):
        chan = build_channel(
            enabled=True, channel_type="ntfy", ntfy_topic_url="https://ntfy.sh/t"
        )
        assert isinstance(chan, NtfyAlertChannel)

    def test_unknown_type_falls_back_to_null(self):
        chan = build_channel(enabled=True, channel_type="slack")
        assert isinstance(chan, NullAlertChannel)
