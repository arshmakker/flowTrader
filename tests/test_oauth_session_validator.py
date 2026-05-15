"""Regression tests for validate_oauth_session network-vs-auth classification.

Bug 2026-05-15 14:20 IST: laptop DNS dropped for ~10s. Both watchlist_names
and limits probes raised requests.ConnectionError (NameResolutionError).
The validator treated this the same as a 401, escalated to mid-session reauth,
which also failed (no internet to drive Selenium), triggering CRITICAL exit.
The finally-block no-weekend-carry then force-flattened both ICs against
stale/imputed marks, booking −₹15,114 on a position that was ~+₹15,700
unrealised — a ~₹30k swing caused purely by misclassifying network failure
as session expiry.

These tests pin the new behaviour:
  - Network unreachable on every probe -> validator returns True (transient).
  - HTTP response with Invalid Session Key body -> validator returns False
    (real auth rejection; reauth path correctly invoked).
"""

from unittest.mock import MagicMock, patch

import requests

from api_helper import ShoonyaApiPy


def _api_with_oauth_headers():
    api = ShoonyaApiPy()
    # Service config must exist or _oauth_post_json bails before requests.post.
    setattr(
        type(api).__bases__[0],
        "_NorenApi__service_config",
        {
            "host": "https://api.shoonya.com/NorenWClientAPI",
            "routes": {"watchlist_names": "/MWList", "limits": "/Limits"},
        },
    )
    setattr(api, "_NorenApi__OAuthHeaders", {"Authorization": "Bearer dummy"})
    setattr(api, "_NorenApi__username", "DUMMY")
    setattr(api, "_NorenApi__accountid", "DUMMY")
    return api


def test_validate_returns_true_when_every_probe_raises_connection_error():
    """Today's bug: DNS dead -> ConnectionError on every probe -> session must
    be treated as still valid so the caller does NOT escalate to reauth."""
    api = _api_with_oauth_headers()

    def fake_post(url, data=None, headers=None, timeout=None):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='api.shoonya.com', port=443): " "Max retries exceeded ... NameResolutionError"
        )

    with patch("api_helper.requests.post", side_effect=fake_post):
        assert api.validate_oauth_session() is True


def test_validate_returns_true_on_timeout_every_probe():
    """Read timeout is also transient — broker may be slow, not auth-bad."""
    api = _api_with_oauth_headers()

    def fake_post(url, data=None, headers=None, timeout=None):
        raise requests.Timeout("read timeout")

    with patch("api_helper.requests.post", side_effect=fake_post):
        assert api.validate_oauth_session() is True


def test_validate_returns_false_on_real_invalid_session_key():
    """HTTP response with stat=Not_Ok / Invalid Session Key — must NOT be
    swallowed by the transient branch. Genuine reauth is still required."""
    api = _api_with_oauth_headers()

    def fake_post(url, data=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = '{"stat":"Not_Ok","emsg":"Invalid Session Key"}'
        return resp

    with patch("api_helper.requests.post", side_effect=fake_post):
        assert api.validate_oauth_session() is False


def test_validate_returns_false_when_mixed_transient_and_real_rejection():
    """First probe DNS-fails (transient), second probe returns auth rejection.
    Mixed evidence means we HAVE proof the session is bad — don't hide it."""
    api = _api_with_oauth_headers()
    call_count = {"n": 0}

    def fake_post(url, data=None, headers=None, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.ConnectionError("DNS dead")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = '{"stat":"Not_Ok","emsg":"Invalid Session Key"}'
        return resp

    with patch("api_helper.requests.post", side_effect=fake_post):
        assert api.validate_oauth_session() is False
