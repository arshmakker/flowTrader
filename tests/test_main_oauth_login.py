"""Regression tests for main OAuth login helpers.

Covers two load-bearing pieces of the Shoonya OAuth flow that had no direct
unit coverage before:

1. `main._extract_auth_code` — the parser that pulls an auth code out of whatever
   the capture mechanism hands back (labeled output, redirect URL, bare token).

2. `main._initialize_api_oauth` — the orchestrator that drives the
   env-var -> in-process Selenium -> external subprocess -> manual-paste
   fallback chain. CLAUDE.md flags this as load-bearing; order matters.
"""
import os
import sys
import logging

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


# -------------------- _extract_auth_code --------------------

def test_extract_from_auth_code_label():
    assert main._extract_auth_code("Auth Code: abc123def") == "abc123def"


def test_extract_case_insensitive_label():
    assert main._extract_auth_code("auth code: xyz789token") == "xyz789token"


def test_extract_label_tolerates_surrounding_noise():
    text = "[getAuthCode] step 3/3 complete\nAuth Code: code_abc.1-2\ndone."
    assert main._extract_auth_code(text) == "code_abc.1-2"


def test_extract_from_redirect_url():
    assert main._extract_auth_code("https://redirect.example/cb?code=foo-bar") == "foo-bar"


def test_extract_url_decodes_code():
    # %2B -> '+'. Real auth codes don't contain '+', but the decoder contract matters.
    assert main._extract_auth_code("?code=foo%2Bbar") == "foo+bar"


def test_extract_auth_code_label_wins_over_redirect_url():
    text = "Auth Code: LABEL_WON\nRedirect: https://x/cb?code=URL_LOSE"
    assert main._extract_auth_code(text) == "LABEL_WON"


def test_extract_single_line_token_fallback():
    assert main._extract_auth_code("abc123_xyz") == "abc123_xyz"


def test_extract_rejects_short_single_token():
    # <8 chars — looks like noise, not a code.
    assert main._extract_auth_code("short") == ""


def test_extract_rejects_multiline_noise_without_markers():
    assert main._extract_auth_code("line one here\nline two here") == ""


def test_extract_empty_string_returns_empty():
    assert main._extract_auth_code("") == ""


def test_extract_none_returns_empty():
    assert main._extract_auth_code(None) == ""


# -------------------- _initialize_api_oauth --------------------

WORKING_CREDS = {
    "UID": "FA50394",
    "client_id": "FA50394_U",
    "Secret_Code": "A" * 64,
    "oauth_url": "https://trade.shoonya.com/NorenWeb/authorize/oauth",
    "token_url": "https://api.shoonya.com/NorenWClientAPI//GenAcsTok",
    "Account_ID": "FA50394",
}


class FakeApi:
    """Minimal stand-in for ShoonyaApiPy exposing only what _initialize_api_oauth touches."""

    def __init__(self, exchange_results=None, validate_results=None, last_error=""):
        # exchange_results: iterable of return values for successive exchange_auth_code calls
        #   (None simulates failure, tuple simulates success)
        self.exchange_results = list(exchange_results or [])
        # validate_results: iterable of return values for successive validate_oauth_session calls
        self.validate_results = list(validate_results or [])
        self.last_error = last_error

        self.configured_api_host = None
        self.configured_ws = None
        self.injected = []
        self.exchange_calls = []
        self.validate_calls = 0

    def configure_oauth_service_host(self, api_host, ws_endpoint):
        self.configured_api_host = api_host
        self.configured_ws = ws_endpoint

    def inject_oauth_header(self, token, uid, account_id):
        self.injected.append((token, uid, account_id))

    def validate_oauth_session(self):
        self.validate_calls += 1
        if not self.validate_results:
            return False
        return self.validate_results.pop(0)

    def get_oauth_url(self, oauth_url, client_id):
        return f"{oauth_url}?client_id={client_id}"

    def exchange_auth_code(self, auth_code, secret_code, client_id, uid, token_url=""):
        self.exchange_calls.append({
            "auth_code": auth_code, "secret_code": secret_code,
            "client_id": client_id, "uid": uid, "token_url": token_url,
        })
        if not self.exchange_results:
            return None
        return self.exchange_results.pop(0)

    def get_last_broker_error(self):
        return self.last_error


@pytest.fixture
def clean_env(monkeypatch):
    """Ensure no SHOONYA_* env var leaks into the test from the host shell."""
    for k in (
        "SHOONYA_AUTH_CODE", "SHOONYA_AUTH_CODE_CMD", "SHOONYA_AUTH_CODE_TIMEOUT",
        "SHOONYA_TOKEN_URL", "SHOONYA_OAUTH_API_HOST", "SHOONYA_OAUTH_WS",
        "SHOONYA_OAUTH_REAUTH_ATTEMPTS",
    ):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def no_disk_writes(monkeypatch):
    """Prevent _save_creds from touching the real cred.yml during tests."""
    saved = {}
    def _fake_save(creds, path="cred.yml"):
        saved["creds"] = dict(creds)
        saved["path"] = path
    monkeypatch.setattr(main, "_save_creds", _fake_save)
    return saved


@pytest.fixture
def log():
    return logging.getLogger("test_main_oauth_login")


def _patch_selenium(monkeypatch, configured=False, code=""):
    monkeypatch.setattr(main.shoonya_selenium_auth, "is_configured", lambda c: configured)
    monkeypatch.setattr(main.shoonya_selenium_auth, "fetch_auth_code", lambda c: code)


def _patch_subprocess_fetch(monkeypatch, code=""):
    monkeypatch.setattr(main, "_fetch_auth_code_from_command", lambda c, l: code)


def test_cached_token_short_circuits_when_valid(clean_env, no_disk_writes, log):
    """If cred.yml has a valid Access_token, skip the entire auth-code capture chain."""
    creds = dict(WORKING_CREDS, Access_token="cached-token-xyz")
    api = FakeApi(validate_results=[True])
    result = main._initialize_api_oauth(api, creds, log)
    assert result is api
    assert api.validate_calls == 1
    assert api.exchange_calls == []
    assert api.injected == [("cached-token-xyz", "FA50394", "FA50394")]
    # No token save when reusing cached.
    assert "creds" not in no_disk_writes


def test_cached_token_invalid_falls_through_to_reauth(clean_env, no_disk_writes, monkeypatch, log):
    """Cached token that fails validation must trigger the re-auth chain."""
    creds = dict(WORKING_CREDS, Access_token="stale-token")
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "manual_code_env_123")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")

    api = FakeApi(
        exchange_results=[("new-access", "FA50394", "refresh", "FA50394")],
        validate_results=[False, True],  # cached fails, new succeeds
    )
    main._initialize_api_oauth(api, creds, log)
    assert len(api.exchange_calls) == 1
    assert api.exchange_calls[0]["auth_code"] == "manual_code_env_123"
    assert no_disk_writes["creds"]["Access_token"] == "new-access"


def test_env_auth_code_takes_priority_over_selenium(clean_env, no_disk_writes, monkeypatch, log):
    """SHOONYA_AUTH_CODE env var must short-circuit the Selenium branch even when Selenium is configured."""
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "env_override_code_42")
    selenium_called = {"count": 0}
    def fake_fetch(c):
        selenium_called["count"] += 1
        return "selenium_code_should_not_be_used"
    monkeypatch.setattr(main.shoonya_selenium_auth, "is_configured", lambda c: True)
    monkeypatch.setattr(main.shoonya_selenium_auth, "fetch_auth_code", fake_fetch)
    _patch_subprocess_fetch(monkeypatch, code="subprocess_should_not_be_used")

    api = FakeApi(
        exchange_results=[("tok", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert selenium_called["count"] == 0
    assert api.exchange_calls[0]["auth_code"] == "env_override_code_42"


def test_selenium_used_when_env_empty_and_configured(clean_env, no_disk_writes, monkeypatch, log):
    """No env var + Selenium configured -> Selenium wins; subprocess fallback stays untouched."""
    subprocess_called = {"count": 0}
    def fake_sub(c, l):
        subprocess_called["count"] += 1
        return "subprocess_code_should_not_be_used"
    _patch_selenium(monkeypatch, configured=True, code="selenium_code_ok")
    monkeypatch.setattr(main, "_fetch_auth_code_from_command", fake_sub)

    api = FakeApi(
        exchange_results=[("tok", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert subprocess_called["count"] == 0
    assert api.exchange_calls[0]["auth_code"] == "selenium_code_ok"


def test_subprocess_used_when_selenium_not_configured(clean_env, no_disk_writes, monkeypatch, log):
    """No env var + Selenium not configured -> subprocess fallback is used."""
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="subprocess_captured_code")

    api = FakeApi(
        exchange_results=[("tok", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert api.exchange_calls[0]["auth_code"] == "subprocess_captured_code"


def test_retries_on_token_exchange_failure(clean_env, no_disk_writes, monkeypatch, log):
    """First exchange returns None; loop retries and succeeds on attempt 2."""
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "retry_code")
    monkeypatch.setenv("SHOONYA_OAUTH_REAUTH_ATTEMPTS", "2")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")

    api = FakeApi(
        exchange_results=[None, ("final-token", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert len(api.exchange_calls) == 2
    assert no_disk_writes["creds"]["Access_token"] == "final-token"


def test_successful_login_persists_new_token(clean_env, no_disk_writes, monkeypatch, log):
    """On successful re-auth the new Access_token/Account_ID/UID land in cred.yml."""
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "persist_code")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")

    api = FakeApi(
        exchange_results=[("persisted-token", "NEW_UID", "refresh", "NEW_ACCOUNT")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert no_disk_writes["creds"]["Access_token"] == "persisted-token"
    assert no_disk_writes["creds"]["Account_ID"] == "NEW_ACCOUNT"
    assert no_disk_writes["creds"]["UID"] == "NEW_UID"


def test_env_token_url_overrides_creds_token_url(clean_env, no_disk_writes, monkeypatch, log):
    """SHOONYA_TOKEN_URL env must override creds.token_url when calling exchange_auth_code."""
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "code123_abc")
    monkeypatch.setenv("SHOONYA_TOKEN_URL", "https://api.shoonya.com/NorenWClientAPI//GenAcsTok")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")

    creds = dict(WORKING_CREDS, token_url="https://trade.shoonya.com/should-not-be-used")
    api = FakeApi(
        exchange_results=[("t", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, creds, log)
    assert api.exchange_calls[0]["token_url"] == "https://api.shoonya.com/NorenWClientAPI//GenAcsTok"


def test_configures_service_host_before_auth(clean_env, no_disk_writes, monkeypatch, log):
    """Service host must be pointed at api.shoonya.com by default before any auth work."""
    monkeypatch.setenv("SHOONYA_AUTH_CODE", "code123_abc")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")

    api = FakeApi(
        exchange_results=[("t", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    assert api.configured_api_host == "https://api.shoonya.com/NorenWClientAPI/"
    assert api.configured_ws == "wss://api.shoonya.com/NorenWS/"


# -------------------- _fetch_auth_code_from_command shell-metachar refusal --------------------
#
# 2026-04-28 latent regression: Sunday's 2548a84 hardened the runner to
# shell=False + shlex.split. Operator cred.yml had `cd /path && python script`,
# which under shlex.split becomes argv ['cd', '/path', '&&', 'python', 'script'].
# With shell=False, /usr/bin/cd (a macOS shim) execs and ignores the trailing
# args; the python script never runs and the runner falls through with no code.
# Bug stayed dormant Monday because the cached OAuth token was still valid; it
# fired Tuesday morning the moment the token expired and re-auth ran for the
# first time since the hardening.
#
# These tests pin the contract operator cred.yml MUST satisfy: shell control
# tokens that survive shlex.split as literal argv entries are refused with a
# warning and an empty return, not silently exec'd as positional args.

def test_auth_code_cmd_with_shell_and_chain_is_refused(clean_env, monkeypatch, caplog, log):
    """The exact cred.yml shape that broke 2026-04-28 must refuse with a clear log line."""
    creds = {"auth_code_cmd": "cd /tmp && /usr/bin/python3 script.py"}
    # Guard: ensure no real subprocess is spawned even if the refusal regresses.
    def _never_spawn(*args, **kwargs):
        raise AssertionError("subprocess.Popen must not be called when shell tokens leak")
    monkeypatch.setattr(main.subprocess, "Popen", _never_spawn)

    with caplog.at_level(logging.WARNING, logger="test_main_oauth_login"):
        result = main._fetch_auth_code_from_command(creds, log)

    assert result == ""
    assert any("shell control token" in r.message for r in caplog.records), (
        "operator must see an actionable warning, not a silent no-op"
    )


@pytest.mark.parametrize("cmd", [
    "python a.py; python b.py",          # ;
    "python a.py | grep code",            # |
    "python a.py || python fallback.py",  # ||
    "python a.py > /tmp/out",             # >
    "python a.py < /tmp/in",              # <
    "python a.py &",                      # & (background)
])
def test_auth_code_cmd_other_shell_tokens_refused(clean_env, monkeypatch, cmd, log):
    """Refusal covers the full set of bash control tokens that shlex preserves as separate argv entries."""
    creds = {"auth_code_cmd": cmd}
    def _never_spawn(*args, **kwargs):
        raise AssertionError(f"subprocess.Popen must not be called for: {cmd!r}")
    monkeypatch.setattr(main.subprocess, "Popen", _never_spawn)
    assert main._fetch_auth_code_from_command(creds, log) == ""


def test_auth_code_cmd_clean_argv_is_not_refused(clean_env, monkeypatch, log):
    """Plain argv-style commands (the documented shape) must reach subprocess.Popen."""
    creds = {"auth_code_cmd": "/usr/bin/python3 /abs/path/getAuthCode.py"}
    spawn_called = {"argv": None}

    class _FakeProc:
        stdout = None
        returncode = 0
        def poll(self):
            return 0
        def communicate(self, timeout=3):
            return ("", "")
        def terminate(self):
            pass

    def _capture(argv, **kwargs):
        spawn_called["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(main.subprocess, "Popen", _capture)
    main._fetch_auth_code_from_command(creds, log)
    assert spawn_called["argv"] == ["/usr/bin/python3", "/abs/path/getAuthCode.py"]


def test_manual_fallback_reached_when_all_attempts_capture_no_code(clean_env, no_disk_writes, monkeypatch, log):
    """Retry loop exhausts with no auth code -> manual input() fallback runs and succeeds."""
    monkeypatch.setenv("SHOONYA_OAUTH_REAUTH_ATTEMPTS", "1")
    _patch_selenium(monkeypatch, configured=False)
    _patch_subprocess_fetch(monkeypatch, code="")
    monkeypatch.setattr("builtins.input", lambda prompt="": "manual_paste_code")

    api = FakeApi(
        exchange_results=[("manual-token", "FA50394", "r", "FA50394")],
        validate_results=[True],
    )
    main._initialize_api_oauth(api, dict(WORKING_CREDS), log)
    # Loop never called exchange (no code captured); manual fallback did.
    assert len(api.exchange_calls) == 1
    assert api.exchange_calls[0]["auth_code"] == "manual_paste_code"
    assert no_disk_writes["creds"]["Access_token"] == "manual-token"
