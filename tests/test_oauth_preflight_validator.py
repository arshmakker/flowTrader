"""Regression tests for main._validate_oauth_creds.

The validator catches the two stale-config bugs that recurred during the
2026-04-22 OAuth debugging session: short Secret_Code and wrong token_url host.
These tests pin its behavior so future refactors can't silently weaken it.
"""

import logging

import pytest

from main import _validate_oauth_creds

WORKING_CREDS = {
    "UID": "FA50394",
    "client_id": "FA50394_U",
    "Secret_Code": "A" * 64,  # 64 chars — matches the working value's shape
    "oauth_url": "https://trade.shoonya.com/NorenWeb/authorize/oauth",
    "token_url": "https://api.shoonya.com/NorenWClientAPI//GenAcsTok",
}


@pytest.fixture
def log_capture(caplog):
    caplog.set_level(logging.WARNING)
    return caplog


def test_healthy_creds_emit_no_warnings(log_capture):
    log = logging.getLogger("preflight_test")
    _validate_oauth_creds(WORKING_CREDS, log)
    assert not [r for r in log_capture.records if r.levelno >= logging.WARNING]


def test_missing_fields_emit_warning(log_capture):
    log = logging.getLogger("preflight_test")
    creds = {k: v for k, v in WORKING_CREDS.items() if k != "Secret_Code"}
    creds["Secret_Code"] = ""
    _validate_oauth_creds(creds, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "missing required fields" in msgs
    assert "Secret_Code" in msgs


def test_short_secret_code_emits_warning(log_capture):
    log = logging.getLogger("preflight_test")
    creds = dict(WORKING_CREDS, Secret_Code="A" * 40)
    _validate_oauth_creds(creds, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "40 chars" in msgs
    assert "INVALID_VERIFIER" in msgs


def test_short_secret_code_at_boundary_49_warns(log_capture):
    log = logging.getLogger("preflight_test")
    creds = dict(WORKING_CREDS, Secret_Code="A" * 49)
    _validate_oauth_creds(creds, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "49 chars" in msgs


def test_secret_code_exactly_50_does_not_warn(log_capture):
    log = logging.getLogger("preflight_test")
    creds = dict(WORKING_CREDS, Secret_Code="A" * 50)
    _validate_oauth_creds(creds, log)
    secret_warnings = [r for r in log_capture.records if "Secret_Code" in r.message]
    assert not secret_warnings


def test_trade_host_token_url_emits_ip_whitelist_warning(log_capture):
    log = logging.getLogger("preflight_test")
    creds = dict(WORKING_CREDS, token_url="https://trade.shoonya.com/NorenWClientAPI/GenAcsTok")
    _validate_oauth_creds(creds, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "trade.shoonya.com" in msgs
    assert "INVALID_IP" in msgs


def test_unknown_host_token_url_emits_warning(log_capture):
    log = logging.getLogger("preflight_test")
    creds = dict(WORKING_CREDS, token_url="https://something.else.example/GenAcsTok")
    _validate_oauth_creds(creds, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "unrecognized host" in msgs


def test_env_var_token_url_takes_precedence(log_capture, monkeypatch):
    """SHOONYA_TOKEN_URL env var should be what the validator inspects."""
    log = logging.getLogger("preflight_test")
    monkeypatch.setenv("SHOONYA_TOKEN_URL", "https://trade.shoonya.com/NorenWClientAPI/GenAcsTok")
    # cred.yml has the healthy URL but env var overrides it
    _validate_oauth_creds(WORKING_CREDS, log)
    msgs = " ".join(r.message for r in log_capture.records)
    assert "trade.shoonya.com" in msgs
