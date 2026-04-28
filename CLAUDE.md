# CLAUDE.md — RegimeTrader

## On conversation start

Every time this project is opened in Claude, do these two things in order.

### 1. Anchor the current date — required for all date-based planning

Run `date` and `TZ=Asia/Kolkata date` (IST is the trading timezone). Never hardcode, infer, or estimate today's date. Every date reference in this session — log filenames, market-hours checks, weekend carry rules, session resume files, checklist "Addressed YYYY-MM-DD" timestamps — must resolve against that anchored value. When the user mentions a relative date ("Thursday", "next week", "yesterday's session"), convert it against the system-anchored date before acting.

### 2. Show session state — PnL summary AND next-session pickup

**PnL summary:**
- Read `data/pnl_snapshot.json`; if `timestamp` matches today, display: daily realised PnL, unrealised PnL, net total, trade count, win rate, per-instrument breakdown (NIFTY/BANKNIFTY)
- Also scan `data/paper_trades.csv` for today's rows if useful
- If the snapshot is from a previous day, report "No trading data for today yet" with the date of the last snapshot
- Format as a concise table; all PnL in INR (₹)

**Next-session pickup:**
- Read the latest `session_<date>_resume.md` under `/Users/arshdeep/.claude/projects/-Users-arshdeep-git-regimetrader/memory/` (the file pointed to by `MEMORY.md`'s first entry)
- Display in 3-5 bullets: current branch @ commit, test count, what landed last session, what's next to pick up
- Flag any operator-pending items (e.g. "LIVE-23 ntfy smoke test still open")
- If the session resume is older than today, note the gap — the user may want to re-plan rather than blindly continue

This block keeps the operator oriented without needing to read the memory file themselves.

## Delegate verbose-output commands to the operator

Real token savings come from avoiding **command output** flooding the context, not from avoiding permission prompts (those are UI, not tokens). Delegate only when output is genuinely verbose:

**Delegate — print the command and ask operator to run it:**
- `pip install` / `pip uninstall` / `brew install` / `npm install` (hundreds of lines of dependency resolution)
- Any long-running build/compile step
- Anything requiring sudo or interactive input

**Confirm first, then run via Bash** (destructive, short output — safety matters more than tokens):
- `rm` / `rm -rf` / `mv` (state the target, get explicit OK, then run)
- `kill` / `pkill` (state the PID and what's being killed)

**Run freely via Bash** (already allowlisted in `.claude/settings.local.json`, output is small):
- `git status` / `git diff` / `git log` / `git show` / `git add` / `git commit` / `git push` / `git checkout` / `git merge` / `git stash`
- `pytest`, `python *`, `ls`, `grep`, `find`, `date`
- Anything purely informational

**Output pattern when delegating:** finish the work, then say "Run this when you're ready: `<command>`" — single line, copy-pasteable. Numbered if multi-step. No permission-prompt ceremony; the operator already knows the drill. If you need the output back to proceed, say so explicitly so the operator knows to paste it.

## Engineering axiom — code earns its existence

Every line, parameter, branch, flag, test, and abstraction must be justified by a concrete failure mode it prevents. Default to **not** writing it. When unsure, smaller code wins; if the same bug recurs, abstract then.

This axiom governs every engineering and architectural decision in this repo. It overrides "completeness," "consistency with similar code elsewhere," "future-proofing," and any urge to add a knob "just in case." If a proposal can't name the concrete failure mode it prevents — cite the line and the scenario — it doesn't ship.

**Rejected by default:**
- Defensive branches against inputs that cannot occur — validate only at system boundaries (broker API responses, `cred.yml`, operator input). Trust internal call-sites.
- Feature flags or settings toggles for safety controls — a disable-able P0 is not P0. Hard-code the gate; let the test suite be the toggle.
- Speculative parameters for hypothetical future consumers (`symbol=None, broker_halt_flag=None` with no current caller).
- Dead branches: paths no production code reaches (e.g. a futures branch in an IC-only system).
- `getattr(settings, "X", default)` when we own `settings.X` — use direct access. The fallback hides config-drift bugs.
- Test-compat branches in production code — e.g. `isinstance(x, (int, float))` added so a `MagicMock` test doesn't crash. Fix the test, not the production code.
- Tests that pin log-string format, kwarg-vs-positional call shape, or which internal field was consulted — they break on valid refactors that preserve user-visible behavior.
- Comments restating what well-named code already says. Comments earn their existence too: only when the *why* is non-obvious (hidden constraint, subtle invariant, workaround for a specific upstream bug).
- Backwards-compat shims, `_unused` renames of removed symbols, "// removed for X" markers when the code is simply gone. `git log` is the history.

**Required:**
- Each bug fix ships with a regression test that fails without the fix and passes with it.
- Pure-deletion fixes are verified by the suite staying green — call that out explicitly in the response, don't skip the verification statement silently.
- The 5 trading-system axioms in `docs/axioms.md` are sacrosanct. Never propose an amendment unprompted; if a design conflicts with one, flag the conflict and stop.

**Enforcement:** the unprompted 4-question post-session audit per commit (see `memory/feedback_post_session_audit.md`). The audit asks per commit: (1) what concrete bug does this prevent, (2) is any part overbuilt, (3) any dead branches or implementation-pinning tests, (4) does the regression test actually exercise the bug. If a commit can't pass all four, it shouldn't have shipped — and the audit is the mechanism that catches it before the next batch compounds it.

## What is this project

RegimeTrader is a Python-based automated trading system for NIFTY derivatives (options/futures) on the Indian stock market. It classifies trading days by regime (ranging vs trending), applies VIX-based filters, and executes **Iron Condor** strategies in paper-trading mode via the **Shoonya (Noren) broker API**.

The system is **paper-trade only** — live order execution is not implemented. All fills, costs, and P&L are simulated with realistic slippage and transaction costs.

## Architecture overview

```
main.py (orchestrator)
  ├── api_helper.py         — Shoonya API wrapper (OAuth + legacy 2FA, rate limiting)
  ├── symbol_manager.py     — NFO/NSE/BSE symbol master loading + token resolution
  ├── data_collector.py     — Background tick collection thread (5s cycle)
  ├── strategy_runner.py    — Market-hours helpers, expiry/VIX utilities
  │
  └── trading_system/
      ├── config/settings.py        — All tunable parameters (single file)
      ├── core/
      │   ├── day_classifier.py     — RANGING vs TRENDING classification (locks at 10:30)
      │   ├── regime_filter.py      — VIX monitoring + entry gate (VIX<30, stable 45min)
      │   ├── iron_condor.py        — 4-leg IC strategy (entry, monitor, harvest, exit)
      │   ├── signal_engine.py      — VWAP, RSI, PCR, Max Pain signals
      │   ├── risk_manager.py       — 3x stop-loss + recovery exception logic
      │   ├── expiry_manager.py     — 3 DTE rolling rule
      │   ├── sr_manager.py         — 20-day high/low S/R with 50-point buffer
      │   ├── trade_logger.py       — CSV trade log + signal log
      │   └── position_persistence.py — JSON state save/restore
      ├── existing/
      │   └── market_data.py        — MarketData adapter (LTP caching, OHLCV, option validation)
      ├── paper/
      │   ├── paper_order_manager.py    — Simulated fills with slippage + costs
      │   ├── paper_position_tracker.py — In-memory position management
      │   ├── paper_pnl_engine.py       — Cumulative + daily P&L tracking
      │   └── go_live_evaluator.py      — Readiness thresholds for live migration
      └── dashboard/
          ├── web_dashboard.py      — Flask dashboard (port 5050)
          └── terminal_dashboard.py — Rich terminal display
```

## Key execution flow

1. **Auth** — Dual-mode: OAuth (preferred, token-cached in `cred.yml`) or legacy 2FA
2. **Data collection** — Background thread collects ticks from 09:15
3. **Classification** — Day type locked at 10:30 (RANGING/TRENDING)
4. **Entry gate** — Only enters on RANGING days with VIX < 30 and stable for 45 min
5. **IC strategy** — VIX-adaptive strikes, S/R buffered, 4-leg atomic entry
6. **Monitoring** — 1% harvest cycles (close + re-enter), breach adjustments
7. **Hard close** — All positions closed at 14:15, system shutdown by 15:30
8. **Persistence** — State saved to `data/open_positions.json` after every cycle

## Common commands

```bash
# Run the system (OAuth login is in-process; no wrapper script)
python main.py

# Run tests (fast unit tests only by default)
pytest

# Run specific test file
pytest tests/test_paper_trading.py

# Install dependencies
pip install -r requirements.txt
```

## Watch loop — paste during market hours

Session-bound monitoring. Paste this after opening Claude on a trading morning; the loop fires every 10 min, reports only deltas (silent ticks if nothing moved), and self-stops at 15:35 IST.

```
/loop 10m Trading day system-watch (silent unless deltas). Each tick: (1) pgrep -f "python main.py" — process alive? (2) Scan today's IST log (logs/ic_system_<YYYYMMDD>.log) for new ERROR|HALT|BREACH|HARD_STOP|FORCE_EXIT lines since last tick. (3) Read data/pnl_snapshot.json — PnL delta vs prior tick. (4) Read data/open_positions.json — position changes. (5) Tail data/paper_trades.csv last 5 rows — new fills. Report ONLY deltas. If current IST time >= 15:35, CronList → CronDelete this job and PushNotification "Watch loop ended for today".
```

Loop is session-bound — closing this terminal stops it. For a durable cloud-resident equivalent that runs every weekday automatically, use `/schedule` instead.

## Testing

Tests live in `tests/`. Fast offline unit tests run by default; integration tests requiring broker credentials are excluded via `conftest.py`.

Key test files:
- `test_paper_trading.py` — Paper order manager + tracker
- `test_ic_strategy.py` — Iron Condor strike calculation, entry/exit
- `test_day_classifier.py` — Day classification logic
- `test_ic_logic.py` — IC-specific logic
- `test_operational_safety.py` — Safety checks
- `test_market_data.py` — MarketData adapter

## Configuration

All tunable parameters are in `trading_system/config/settings.py`. Key ones:

- `PAPER_TRADE_MODE` — Always True (live not implemented)
- `IC_LOT_SIZE` — Lots per entry (default 10)
- `IC_VIX_MAX` — Max VIX for entry (30.0)
- `IC_MIN_CREDIT` — Min per-lot credit to accept (18)
- `IC_STOP_LOSS_MULT` — Hard stop at 3x max profit
- `IC_HARVEST_PCT` — Close at 1% of max profit
- `IC_DTE_THRESHOLD` — Roll if DTE < 3
- `IC_SR_BUFFER` — Min 50-point distance from 20-day H/L
- `NIFTY_LOT_SIZE` / `BANKNIFTY_LOT_SIZE` — 65 / 30

Credentials go in `cred.yml` (git-ignored). See `cred.yml.template` for schema.

## Important data directories

- `data/` — Paper trades CSV, P&L snapshots, open positions JSON, signal logs
- `logs/` — Daily rotating logs (`ic_system_YYYYMMDD.log`)
- `symbols/` — Shoonya master files (NFO.csv, NSE.csv, BSE.csv)
- `market_data_YYYYMMDD/` — Per-day tick data (raw_data/ + processed_data/)
- `tools/` — Utility scripts (backtesting, data cleanup)

## Broker API

The system uses **Shoonya/Noren API** via `NorenRestApiPy`. The `api_helper.py` wrapper adds:
- Thread-safe quote rate limiting (10 calls/sec hard cap, configurable via env)
- Priority lanes (high for strategy, low for background polling)
- OAuth token caching and session validation with retry
- Quote-level auth fallback (OAuth header -> jKey)

## Auth flow — DO NOT modify without re-verifying against the live API

The OAuth flow was end-to-end-verified on 2026-04-22 against this user's Shoonya account. Touching any of the following without explicit go-ahead and a fresh end-to-end test is high-risk — the system will fail to start and lock out paper trading.

**Working configuration (cred.yml):**
- `token_url: https://api.shoonya.com/NorenWClientAPI//GenAcsTok` — double slash is intentional, matches SDK concat. Do NOT switch to `trade.shoonya.com` without first whitelisting the user's static IP in the Shoonya portal (otherwise → `INVALID_IP`).
- `Secret_Code` must be the **64-char** value from the Shoonya portal API-key section. A 40-char value is the dummy/old one and produces `INVALID_VERIFIER` on every exchange.
- `client_id`, `UID`, `oauth_url` — see cred.yml.template for canonical values.

**Files / functions that are load-bearing for auth — review carefully before editing:**
- `api_helper.py` `exchange_auth_code()` — manual reimplementation of Noren's `getAccessToken`. Checksum recipe is `SHA256(client_id + Secret_Code + auth_code)`. Default URL must stay on `api.shoonya.com`.
- `main.py` `_initialize_api_oauth()` — orchestrates env-var → in-process Selenium → subprocess-script → manual-paste fallback chain. Order matters.
- `main.py` `_validate_oauth_creds()` — pre-flight sanity check; logs warnings for stale Secret_Code length, host mismatch, missing fields. Don't silence its warnings; investigate.
- `trading_system/auth/shoonya_selenium_auth.py` — in-process Selenium login. Uses Shoonya's `OAuthlogin/investor-entry-level/login` URL (different from the OAuth authorize URL).

**Regression tests that pin auth behavior:**
- `tests/test_oauth_token_url.py` — pins default URL, override priority, blocks `trade.shoonya.com` from sneaking back in as default.
- `tests/test_shoonya_selenium_auth.py` — pins selenium auth config detection and URL building.
- `tests/test_oauth_preflight_validator.py` — pins the cred.yml shape validator.

**Canonical reference scripts (Shoonya support pointed to these):**
- `deepak-dhyani8742/Shoonya_oAuthAPI-py/GetAuthcode` (Selenium + manual exchange in one file)
- `sauravfinvasia-ai/Shoonya_oAuth_API.py/test_oauth.py` (uses SDK `api.getAccessToken()`)

**Diagnostic log line to look for:**
```
[api_helper] INFO — OAuth token exchange POST -> url=... client_id=... uid=... code_len=36 secret_len=64 checksum=...
```
`secret_len < 50` or `url` not on `api.shoonya.com` → likely broken config.

## Key constraints to respect

- **No live trading** — `PAPER_TRADE_MODE` must stay True
- **Atomic IC entry** — All 4 legs must fill or entire entry is rolled back
- **Option LTP validation** — Quotes outside [0.05, 5000] are rejected
- **Hard stop confirmation** — Requires 2 consecutive tick breaches before halt
- **No weekend exposure** — Flat by Thursday 3:15 PM
- **Credential safety** — Never commit `cred.yml` or token artifacts

## Dependencies

NorenRestApiPy (broker SDK), pandas, numpy, scipy, Flask, PyYAML, requests, psutil, colorama, websocket-client, python-dateutil, pytz.

Python 3.13 (venv in `venv/`).
