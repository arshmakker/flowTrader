# Project State Snapshot

**Snapshot Date:** February 2026  
**Repo:** `regimetrader`  

## Working tree summary (snapshot)

- Modified: `README.md`, `api_helper.py`, `docs/PROJECT_CONTEXT.md`, `docs/PROJECT_STATE.md`
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
- Completed: `api_helper.py` now includes a thread-safe global quote limiter with priority lanes:
  - Global caps (default below broker ceilings) throttle all `get_quotes()` requests.
  - Low-priority quotas reserve capacity so strategy/risk quote calls are not starved by background collectors.
  - Quote limiter behavior is configurable via env (`SHOONYA_QUOTE_*`, `SHOONYA_QUOTE_LIMIT_ENABLED`).
- Completed: `data_collector.py` now routes quote polling through the low-priority lane (`priority="low"` with collector context tags) so bulk background fetching yields to real-time strategy/risk quote demand.
- Completed: websocket runtime path removed from this branch per broker-token compatibility decision.
- Completed: `main.py` now runs pure REST quote flow (no websocket startup, subscriptions, staleness gate, or shutdown hooks).
- Completed: `main.py` now stops `DataCollector` at `TRADE_END` before entering post-session sleep so collection does not continue after daily strategy shutdown.
- Completed: websocket-only config knobs removed from `trading_system/config/settings.py`.
- Completed: `MarketData`, `RegimeFilter`, and `DataCollector` now use API quote paths only (stream-cache branches removed).
- Completed: websocket runtime path disabled in orchestrator/data flow; sample websocket test scripts may still exist but are not part of active runtime.
- Completed: quote throttling now enforces a hard maximum of `10` quote calls per second in `api_helper.py` by clamping `SHOONYA_QUOTE_MAX_PER_SEC`.
- Completed (auth reliability patch): `api_helper.py` OAuth validation calls now include `jKey` retry fallback when broker responds with `401 Invalid Session Key` under OAuth-header path.
- Completed (auth usability patch): `main.py` auth-code capture/parser now handles broader code formats, avoids blocking `readline()` hangs via `select` polling, and skips duplicate captured auth codes across OAuth retries.
- Completed (quote auth patch): `api_helper.py` `getquotes` path now retries once with `jKey` when OAuth-header quote calls return `401 Invalid Session Key`, addressing post-login quote failures observed at runtime.
- Completed (paper price-integrity patch): `trading_system/config/settings.py` now includes `PAPER_OPTION_LTP_MIN` / `PAPER_OPTION_LTP_MAX` thresholds to support rejecting invalid option quote magnitudes during paper fills.
- Completed (paper execution guard): `trading_system/paper/paper_order_manager.py` now validates option LTP bounds before fill simulation and rejects abnormal quotes with explicit reason `suspicious_option_ltp`.
- Completed (8 Apr data rectification): invalid quote contamination on 2026-04-08 was handled by:
  - marking `20260408_0076` and `20260408_0077` as `INVALID_DATA` with zero PnL in `data/paper_trades.csv`.
  - zeroing day-level PnL aggregates in `data/pnl_snapshot.json` and `data/paper_summary.json`.
  - recording invalid trade IDs under `invalid_trades` in both JSON summary artifacts.
- Completed (entry atomicity fix): `trading_system/core/iron_condor.py` now requires all 4 entry legs to be `COMPLETE`; if any leg is rejected, the strategy aborts entry and rolls back already-placed legs to avoid partial-entry ghost positions and invalid high-credit/PnL artifacts.
- Completed (9 Apr data rectification): `20260409_0081` marked as `INVALID_DATA` with zero PnL in `data/paper_trades.csv`; summary snapshots updated to sanitized day PnL (`8160.0`) and invalid-trade audit list extended.
- Completed (quote/risk hardening pack):
  - `trading_system/existing/market_data.py`: centralized option quote validation with suspicious-LTP rejection and last-valid-price fallback.
  - `trading_system/core/risk_manager.py`: hard-stop now ignores invalid quote snapshots and requires configurable consecutive breach confirmation before triggering halt.
  - `trading_system/config/settings.py`: added `IC_HARD_STOP_CONFIRM_TICKS` (default `2`).

