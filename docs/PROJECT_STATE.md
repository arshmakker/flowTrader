# Project State Snapshot

**Snapshot Date:** February 2026  
**Repo:** `regimetrader`  

## Working tree summary (snapshot)

- Modified: `api_helper.py`, `README.md`, `main.py`, `docs/PROJECT_CONTEXT.md`, `docs/PROJECT_STATE.md`
- Untracked/new docs: `docs/BUSINESS_OVERVIEW.md`, `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md`, `docs/PROJECT_CONTEXT.md`, `docs/PROJECT_STATE.md`
- Untracked: `agents.md`, `skills.md`

## Latest product artifact added

- `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md` (PRD v1.0): requirements for an Iron Condor–only trading product, including:
  - Benchmark defaults (delta selection, DTE conventions, 50% profit taking, 21 DTE management)
  - Risk sizing and daily gates
  - Logging/metrics requirements with primary KPI: **% exits with positive TSL**

## Current implementation status

- In progress: startup auth migration to support OAuth token reuse from `cred.yml`.
- Added in code: OAuth helper methods in `api_helper.py` (`get_oauth_url`, `exchange_auth_code`, `inject_oauth_header`, `validate_oauth_session`).
- Completed: `main.py` startup flow now prefers OAuth when configured, validates cached token, prompts for auth code if required, and persists refreshed `Access_token` + `Account_ID` to `cred.yml`.
- Completed: `cred.yml.template` now documents both legacy and OAuth credential modes.
- Completed: `README.md` updated with dual-mode login instructions and OAuth token-cache behavior.
- Completed: `start.sh` now prompts for `TWOFA` only when legacy login mode is active.
- Completed: minor cleanup in OAuth startup path (`main.py`) to keep runtime token tuple handling tidy.
- Completed: syntax validation passed for updated `main.py` and `api_helper.py` via `python -m py_compile`.
- Completed: `.gitignore` expanded for OAuth/token artifacts (`cred.local.yml`, token json/txt patterns, `tokens/`, `auth_code.txt`).
- Completed: `main.py` OAuth flow can now optionally execute an external auth-code command (`auth_code_cmd`/`SHOONYA_AUTH_CODE_CMD`) and parse `Auth Code` output before falling back to manual input.
- Completed: `cred.yml.template` documents optional `auth_code_cmd` and timeout settings.
- Completed: `README.md` includes external command-based auth-code capture usage notes.
- Completed: `cred.yml` removed from git tracking in main repo (local file retained).
- Completed: in sibling `Shoonya_oAuth_API.py` repo, `cred.yml`, `tests/getAuthCode.py`, and `test_auth Code.py` removed from index and protected via `.gitignore`.
- Completed: `api_helper.py` OAuth token exchange now performs direct HTTP request/parse with explicit diagnostics for HTTP errors, empty bodies, and non-JSON responses.
- Completed: `main.py` OAuth flow now retries token exchange once using manual auth-code input when command-provided code fails.
- Completed: `api_helper.py` OAuth exchange now supports explicit `token_url` and defaults to `NorenWClientAPI` endpoint behavior.
- Completed: `main.py` passes optional token endpoint override (`token_url` / `SHOONYA_TOKEN_URL`) into OAuth exchange.
- Completed: `cred.yml.template` includes OAuth `token_url` aligned with working `NorenWClientAPI` `GenAcsTok` endpoint.
- Completed: `README.md` documents OAuth `token_url` and `SHOONYA_TOKEN_URL` override.
- Completed: local runtime `cred.yml` updated with `auth_code_timeout: 300`.
- Completed: `api_helper.py` OAuth validation now uses `get_watch_list_names()` as primary check and `get_limits()` as fallback.
- Completed: `api_helper.py` gained `configure_oauth_service_host()` to switch SDK service config to OAuth API endpoints.
- Completed: `main.py` now applies OAuth service host/websocket config before validation/exchange (`oauth_api_host`, `oauth_ws_endpoint`, env overrides).
- Completed: `cred.yml.template` now includes OAuth host defaults for API/websocket endpoint alignment.
- Completed: `README.md` now documents OAuth host override settings and env mappings.
- Completed: `.gitignore` expanded again for additional secret artifact patterns (`cred.yaml`, `cred.local.yaml`, `auth_code*.txt`, `oauth_response*.json`, `*.secrets.yml`).
- Completed: broker error propagation hardened in `api_helper.py` (`_last_broker_error`, explicit OAuth/legacy diagnostics) and surfaced through `main.py` runtime errors/warnings.
- Completed: `main.py` OAuth startup now retries command-based auth-code capture/exchange (`oauth_reauth_attempts`) whenever OAuth session validation/exchange fails before manual fallback.
- Completed: `cred.yml.template` and `README.md` updated for `oauth_reauth_attempts` behavior.
- Completed: local runtime `cred.yml` updated with `oauth_reauth_attempts: 2`.
- Completed: `main.py` auth-code command runner switched to streamed subprocess output with timeout-aware polling to improve startup transparency and responsiveness.
- Completed: `api_helper.py` quote path hardened via `get_quotes_safe()` retry + explicit broker diagnostics; `get_quotes()` now routes through this safer path.

