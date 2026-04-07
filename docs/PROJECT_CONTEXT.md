# Project Context

**Project:** RegimeTrader  
**Last Updated:** February 2026  

## What this repo is

RegimeTrader is a Python-based automated trading system for NIFTY derivatives (options and futures). It classifies the trading day (ranging vs trending), applies volatility (VIX) regime filters, routes to a strategy, and manages paper-mode execution with position persistence, daily risk gates, and dashboards.

## Current product direction

The system is shifting to an **Iron Condor–only** product direction:

- Focus on a single defined-risk short premium structure (Iron Condor).
- Paper-first validation and go-live gating.
- Exit logic designed to **lock in profit** and maximize **exits with positive trailing stop (TSL)**.

## Key documents

- `docs/BUSINESS_OVERVIEW.md`: business & operating model overview.
- `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md`: product requirements for Iron Condor–only system (benchmarked defaults + TSL KPI).

## Current engineering focus

- Login/auth is being upgraded to support **dual-mode broker auth**:
  - Legacy Shoonya login (`user/pwd/2FA/vendor/api_secret/imei`)
  - OAuth login (`oauth_url/client_id/Secret_Code`) with local `cred.yml` token cache
- Goal: reuse cached OAuth access tokens on restart, validate session at startup, and prompt for auth code only when token is missing/invalid.
- `main.py` now contains dual-path startup auth wiring:
  - OAuth path preferred when OAuth keys are configured in `cred.yml`
  - Legacy 2FA path retained as fallback for backward compatibility
- `cred.yml.template` has been updated to document both credential modes and runtime OAuth token cache fields.
- `README.md` credentials section now explains legacy vs OAuth startup behavior and token reuse from `cred.yml`.
- `start.sh` now detects auth mode from `cred.yml` and prompts for 2FA only in legacy mode.
- OAuth startup path in `main.py` now includes token caching without unused runtime fields.
- `.gitignore` now explicitly excludes additional OAuth/token artifact patterns (`cred.local.yml`, `token*.json`, `tokens/`, `oauth*.json`, `auth_code.txt`) to reduce secret leakage risk.
- OAuth startup now supports optional external auth-code command execution (`auth_code_cmd` / `SHOONYA_AUTH_CODE_CMD`) before manual auth-code prompt.
- Local git safety hardening applied:
  - `cred.yml` has been removed from git tracking (kept locally).
  - Sensitive auth helper assets in sibling OAuth repo are now ignored and removed from index to prevent accidental push.
- OAuth token exchange is now handled with a robust in-repo HTTP path (instead of relying on SDK JSON parse assumptions), with explicit logging for HTTP status and non-JSON response bodies.
- OAuth startup now retries once with a manual auth-code prompt when command-captured code fails token exchange, reducing false negatives from stale/incorrect command output.
- OAuth token exchange now targets `NorenWClientAPI` endpoint semantics explicitly (with optional `token_url` override) to avoid TP-host non-JSON failures.
- `main.py` now allows token endpoint override via `token_url` in `cred.yml` or env `SHOONYA_TOKEN_URL`.
- `cred.yml.template` now includes `token_url` defaulting to `https://api.shoonya.com/NorenWClientAPI//GenAcsTok` for OAuth exchange consistency.
- `README.md` now documents OAuth `token_url` and env override `SHOONYA_TOKEN_URL`.
- Local `cred.yml` runtime config now sets `auth_code_timeout` to 300s to reduce Selenium auth-code capture timeouts under slower broker/web conditions.
- OAuth session validation now checks `get_watch_list_names()` first (matching known-good OAuth flow) with `get_limits()` as fallback, reducing false failures after successful token exchange.
- Added explicit OAuth host switch capability in `api_helper.py` so SDK class-level service config can move from `NorenWClientTP` to `NorenWClientAPI` before OAuth validation calls.
- `main.py` now applies OAuth host/websocket overrides (`oauth_api_host`, `oauth_ws_endpoint` or env equivalents) before cached-token validation and token exchange.
- `cred.yml.template` now includes explicit OAuth service host defaults (`oauth_api_host`, `oauth_ws_endpoint`) aligned with `NorenWClientAPI`/`NorenWS`.
- `README.md` now documents OAuth host override knobs (`oauth_api_host`, `oauth_ws_endpoint`, and env variants).
- `.gitignore` was further hardened for alternate credential/token artifact names (`cred.yaml`, `cred.local.yaml`, `auth_code*.txt`, `oauth_response*.json`, `*.secrets.yml`).
- Broker error visibility has been strengthened:
  - `api_helper.py` now preserves last broker/API error detail (`_last_broker_error`) across legacy login, OAuth exchange, and OAuth validation.
  - `main.py` now includes these broker details in raised runtime errors and warning logs so console output remains actionable.
- OAuth recovery behavior now retries command-driven auth code capture (`tests/getAuthCode.py` style) for session/exchange failures before manual auth-code fallback.
- Local `cred.yml` runtime config now includes `oauth_reauth_attempts: 2` for command-based OAuth recovery retries.
- External auth-code command execution now streams live command output in console and no longer appears stalled while Selenium login is running.
- Quote fetching has been hardened at wrapper level:
  - `ShoonyaApiPy.get_quotes()` is overridden to use resilient `get_quotes_safe()` with one retry.
  - Explicit broker diagnostics (HTTP code/body snippet/rejection reason/non-JSON) are logged instead of raw JSON parse stack traces.
- Global quote throttling control has been added in `api_helper.py`:
  - A thread-safe global limiter now gates all `get_quotes()` calls with default caps tuned below documented broker limits.
  - Priority lanes are supported (`high` for strategy/risk paths, `low` for background polling), preserving headroom for trading decisions while still protecting broker request budgets.
  - Runtime knobs are available via env (`SHOONYA_QUOTE_MAX_PER_SEC`, `SHOONYA_QUOTE_MAX_PER_MIN`, `SHOONYA_QUOTE_LOW_MAX_PER_SEC`, `SHOONYA_QUOTE_LOW_MAX_PER_MIN`, `SHOONYA_QUOTE_LIMIT_ENABLED`).
- `data_collector.py` now tags quote fetches with low-priority context (`priority="low"`) so background collection respects reserved headroom for strategy/risk quote paths during high-load periods.

