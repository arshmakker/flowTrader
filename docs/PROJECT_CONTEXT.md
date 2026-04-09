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
- `main.py` now stops `DataCollector` immediately at `TRADE_END` before the post-session sleep, preventing quote polling from continuing after daily strategy shutdown.
- Websocket quote ingestion has been removed from runtime flow for now:
  - `main.py` no longer starts/manages websocket sessions and runs fully on REST quote pulls.
  - `MarketData`, `RegimeFilter`, and `DataCollector` now use API quote paths only (no stream-cache branches).
  - websocket runtime dependencies are intentionally disabled in active execution paths to avoid partial/unsupported auth assumptions.
- Global quote throttling is now pinned to a hard ceiling of **10 quote calls/second** in `api_helper.py`:
  - `SHOONYA_QUOTE_MAX_PER_SEC` is still accepted but clamped to `10`.
  - This guarantees runtime never exceeds the requested per-second quote rate.
- OAuth session validation hardened further in `api_helper.py`:
  - when OAuth-header validation calls return `401 Invalid Session Key`, the wrapper now retries the same route once using `jKey` session-token auth (if available) before failing.
- `api_helper.py` quote path now includes endpoint-specific auth fallback:
  - when `getquotes` returns `401 Invalid Session Key` on OAuth-header auth, it retries once with `jKey` (`susertoken`) before returning an error.
  - this targets observed runtime behavior where OAuth login/validation succeeds but quote requests are rejected by broker with session-key errors.
- Auth-code command handling in `main.py` improved:
  - `_extract_auth_code` now decodes broader auth-code formats safely (including URL-encoded values and quoted output tokens).
  - `_fetch_auth_code_from_command` now uses non-blocking stdout polling (`select`) to avoid long blocking reads and improve interrupt/timeout responsiveness.
  - OAuth re-auth loop now ignores duplicate command-captured auth codes across attempts to reduce repeated stale-code retries.
- Paper execution quote sanity guard added:
  - `trading_system/config/settings.py` now defines `PAPER_OPTION_LTP_MIN` and `PAPER_OPTION_LTP_MAX`.
  - Goal: reject obviously invalid option LTPs in paper fills (e.g., underlying-like 55k prints on option contracts) to protect PnL integrity.
  - `trading_system/paper/paper_order_manager.py` now enforces the guard inside `place_order()` and rejects orders with reason `suspicious_option_ltp` when option LTP is out of bounds.
- 2026-04-08 data correction applied after manual cross-check:
  - Today paper trades `20260408_0076` and `20260408_0077` are marked `INVALID_DATA` in `data/paper_trades.csv`.
  - `data/pnl_snapshot.json` and `data/paper_summary.json` are overridden for the day with zeroed PnL metrics and an `invalid_trades` list for auditability.
- Iron condor entry safety has been strengthened:
  - `trading_system/core/iron_condor.py` now treats entry as atomic in paper mode: all 4 leg orders must return `COMPLETE`.
  - If any leg is rejected (for example due to `suspicious_option_ltp`), entry is aborted and any already-filled legs are rolled back immediately.
  - This prevents partial-leg state from being logged as a valid condor entry and blocks inflated PnL from rejected-leg contamination.
- 2026-04-09 post-audit correction applied:
  - Trade `20260409_0081` (NIFTY) is marked `INVALID_DATA` in `data/paper_trades.csv` after leg rejection/credit contamination review.
  - Day summary artifacts (`data/pnl_snapshot.json`, `data/paper_summary.json`) are sanitized to realised/total PnL `8160.0` with `invalid_trades` including `20260409_0081`.
- Runtime anti-corruption safeguards expanded:
  - `trading_system/existing/market_data.py` now validates option LTP centrally for all callers (`get_ltp`) using configured min/max bounds, with fallback to last valid option price for the same symbol.
  - `trading_system/core/risk_manager.py` now skips hard-stop decisions on invalid quote snapshots and requires consecutive breach confirmation (`IC_HARD_STOP_CONFIRM_TICKS`) before halting.
  - `trading_system/config/settings.py` now includes `IC_HARD_STOP_CONFIRM_TICKS`.

