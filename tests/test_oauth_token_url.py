"""Regression tests for OAuth token-exchange URL resolution.

The exchange URL is resolved in this priority order (api_helper.py:482-492):
  1. Explicit `token_url` arg (caller / cred.yml override) — wins.
  2. SDK `__service_config` host + `gen_acs_tok` route (after TP -> API substitution).
  3. Hardcoded default (`https://api.shoonya.com/NorenWClientAPI//GenAcsTok`),
     which matches the host the canonical Shoonya_oAuth_API.py/test_oauth.py uses
     (verified end-to-end on 2026-04-22 — returned a live access_token).

These tests pin that priority order so future refactors can't silently break it.
"""

from unittest.mock import MagicMock, patch

from api_helper import ShoonyaApiPy

CANONICAL_DEFAULT_URL = "https://api.shoonya.com/NorenWClientAPI//GenAcsTok"


def _api_with_no_oauth_host():
    """Construct an api with empty service config so the hardcoded default is exercised."""
    api = ShoonyaApiPy()
    setattr(type(api).__bases__[0], "_NorenApi__service_config", {"host": "", "routes": {}})
    return api


def _capture_post_url(api, **call_kwargs):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = '{"emsg": "test stub"}'
        return resp

    with patch("api_helper.requests.post", side_effect=fake_post):
        api.exchange_auth_code(
            auth_code=call_kwargs.get("auth_code", "dummy_code"),
            secret_code=call_kwargs.get("secret_code", "dummy_secret"),
            client_id=call_kwargs.get("client_id", "DUMMY_U"),
            uid=call_kwargs.get("uid", "DUMMY"),
            token_url=call_kwargs.get("token_url"),
        )
    return captured.get("url", "")


def test_default_token_url_is_canonical_when_config_empty():
    """No override + empty SDK config => hardcoded canonical URL kicks in."""
    api = _api_with_no_oauth_host()
    assert _capture_post_url(api) == CANONICAL_DEFAULT_URL


def test_explicit_token_url_overrides_default():
    """If caller passes token_url, that wins over both default and config."""
    api = _api_with_no_oauth_host()
    custom = "https://example.test/CustomTokenEndpoint"
    assert _capture_post_url(api, token_url=custom) == custom


def test_default_url_uses_api_subdomain_not_trade():
    """Pin the host to api.shoonya.com.

    Earlier we briefly set the default to trade.shoonya.com — that host enforces
    static-IP whitelist and returned INVALID_IP for the user. The api. host is
    what the working test_oauth.py setup hits, verified end-to-end.
    """
    api = _api_with_no_oauth_host()
    url = _capture_post_url(api)
    assert "api.shoonya.com" in url
    assert "trade.shoonya.com" not in url
