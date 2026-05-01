"""Regression tests for trading_system.auth.shoonya_selenium_auth.

Covers config detection and login-URL building. Does not launch a browser.
"""

import pytest

from trading_system.auth import shoonya_selenium_auth as auth


def test_is_configured_false_when_keys_missing():
    assert auth.is_configured({}) is False
    assert auth.is_configured({"selenium_user_id": "x"}) is False
    assert auth.is_configured({"selenium_user_id": "x", "selenium_password": "y"}) is False


def test_is_configured_false_when_value_blank():
    assert (
        auth.is_configured(
            {
                "selenium_user_id": "x",
                "selenium_password": "y",
                "selenium_totp_secret": "   ",
            }
        )
        is False
    )


def test_is_configured_true_when_all_keys_present():
    assert (
        auth.is_configured(
            {
                "selenium_user_id": "FA50394",
                "selenium_password": "secret",
                "selenium_totp_secret": "BASE32SECRET",
            }
        )
        is True
    )


def test_build_login_url_uses_default_template_with_client_id():
    url = auth._build_login_url({"client_id": "FA50394_U"})
    assert url == ("https://trade.shoonya.com/OAuthlogin/investor-entry-level/login?api_key=FA50394_U&route_to=abc")


def test_build_login_url_uses_custom_template():
    url = auth._build_login_url(
        {
            "client_id": "FA50394_U",
            "selenium_login_url_template": "https://example.test/oauth?cid={client_id}",
        }
    )
    assert url == "https://example.test/oauth?cid=FA50394_U"


def test_build_login_url_raises_when_client_id_missing():
    with pytest.raises(ValueError):
        auth._build_login_url({})


def test_fetch_auth_code_returns_empty_when_not_configured(caplog):
    caplog.set_level("WARNING")
    assert auth.fetch_auth_code({}) == ""
    assert any("missing required cred fields" in rec.message for rec in caplog.records)
