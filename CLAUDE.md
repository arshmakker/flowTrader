# CLAUDE.md — PCR Trader

## On conversation start

## Conversation type

All conversations have to follow CAVEMAN language

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

PCR Trader is a Python-based automated trading system for NIFTY weekly options on the Indian stock market. It reads the Put-Call Ratio from the live Shoonya option chain and executes **PCR Contrarian Credit Spread** strategies in paper-trading mode via the **Shoonya (Noren) broker API**.

Backtest: 93.9% win rate, 33 trades over 28 months, ₹1,10,188 gross P&L per lot.

The system is **paper-trade only** — live order execution requires explicit operator sign-off. All fills, costs, and P&L are simulated with realistic slippage and transaction costs.

## Architecture overview

```
main.py (orchestrator)
  ├── api_helper.py         — Shoonya API wrapper (OAuth + legacy 2FA, rate limiting)
  ├── symbol_manager.py     — NFO/NSE/BSE symbol master loading + token resolution
  ├── strategy_runner.py    — Market-hours helpers, trading-day guard
  │
  └── trading_system/
      ├── config/settings.py        — All tunable parameters (single file)
      ├── core/
      │   ├── pcr_credit_spread.py  — 2-leg PCR spread strategy (entry, monitor, exit)
      │   ├── pcr_signal.py         — Live PCR via Shoonya get_option_chain (5-min cache)
      │   ├── risk_manager.py       — Daily loss cap + rollback-failure halt
      │   ├── expiry_manager.py     — Nearest active weekly expiry lookup
      │   ├── trade_logger.py       — CSV trade log
      │   └── position_persistence.py — JSON state save/restore
      ├── existing/
      │   └── market_data.py        — MarketData adapter (LTP caching, OHLCV)
      └── paper/
          ├── paper_order_manager.py    — Simulated fills with slippage + costs
          ├── paper_position_tracker.py — In-memory position management
          └── paper_pnl_engine.py       — Cumulative + daily P&L tracking
```

## Key execution flow

1. **Auth** — Dual-mode: OAuth (preferred, token-cached in `cred.yml`) or legacy 2FA
2. **Entry gate** — Mon or Tue 09:20–10:00 IST only; PCR outside 0.7–1.3 neutral band
3. **PCR signal** — `get_option_chain` → nearest weekly expiry OI → pe_oi / ce_oi
4. **Spread entry** — SELL ATM±100, BUY ATM±300 (LMT both legs, atomic rollback on failure)
5. **Monitoring** — Stop if MTM loss > 2× credit; expiry-day exit at 14:45
6. **Hard close** — Expiring at 15:00; all others at 15:10; shutdown by 15:30
7. **Persistence** — State saved to `data/open_positions.json` after every change

## Common commands

```bash
# Run the system (OAuth login is in-process; no wrapper script)
python main.py

# Run PCR strategy tests (no API required)
pytest tests/test_pcr_credit_spread.py -v

# Run full suite
pytest

# Backtest (historical NSE bhavcopy)
python tools/backtest_pcr_spread.py --start 2023-10-01 --end 2026-01-31

# Install dependencies
pip install -r requirements.txt
```

## Watch loop — paste during market hours

Session-bound monitoring + auto-restart. Fires every 10 min, self-stops at 15:35 IST.

```
/loop 10m Trading day system-watch. Each tick: (1) CHECK PROCESS: pgrep -f "python main.py". If dead AND IST 09:15–15:20: (a) tail last 100 lines of logs/pcs_$(TZ=Asia/Kolkata date +%Y%m%d).log for root cause; (b) read data/open_positions.json — if risk_state.halted=true, clear it: python -c "import json; f='data/open_positions.json'; d=json.load(open(f)); d.get('risk_state',{}).update({'halted':False,'stop_hit_at':None,'rollback_failures':[]}); json.dump(d,open(f,'w'),indent=2)"; (c) restart: python main.py > logs/restart_$(TZ=Asia/Kolkata date +%Y%m%d_%H%M%S).log 2>&1 & — report PID and root cause. (2) SCAN LOGS: grep new ERROR|HALT|FORCE_EXIT lines since last tick — quote + diagnose. (3) PnL: data/pnl_snapshot.json — delta vs prior tick. (4) POSITIONS: data/open_positions.json — changes, flag halted state. (5) FILLS: tail data/paper_trades.csv last 5 rows — new entries. Report ONLY deltas (silent if nothing changed). Stop loop at IST >= 15:35.
```

## Testing

Tests live in `tests/`. Fast offline unit tests run by default; integration tests requiring broker credentials are excluded via `conftest.py`.

Key test files:
- `test_pcr_credit_spread.py` — PCR strategy: entry, stop, expiry exit, rollback, state round-trip
- `test_market_data.py` — MarketData adapter
- `test_paper_order_persistence.py` — Paper order state
- `test_oauth_*.py` — OAuth flow and preflight validation

## Configuration

All tunable parameters are in `trading_system/config/settings.py`. Key ones:

- `PAPER_TRADE_MODE` — True (paper only until explicitly signed off)
- `PCS_LOT_SIZE` — Lots per entry (default 1, scale after paper validation)
- `PCS_PCR_BEAR` / `PCS_PCR_BULL` — Entry thresholds (0.7 / 1.3)
- `PCS_SHORT_OTM_PTS` / `PCS_LONG_OTM_PTS` — Strike distances (100 / 300 pts)
- `PCS_MIN_CREDIT` — Min net credit to accept entry (20.0 pts)
- `PCS_STOP_MULT` — Stop if MTM loss > N× credit (2.0)
- `PCS_ENTRY_DAYS` — [0, 1] = Mon, Tue
- `DAILY_MAX_LOSS` — Session halt threshold (−₹50,000)
- `NIFTY_LOT_SIZE` — 65

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
- **Broker MKT order ban** — Shoonya blocks MKT via API. Every `place_order` call must carry a non-zero `price=` argument. `LiveOrderManager` auto-promotes `MKT + price > 0 → LMT` (line 84–85). If `price=0` (e.g. illiquid book with `bid=0`), the order goes as MKT and will be rejected. Paper mode uses `price` as LTP fallback when LTP=0.
- **Credential safety** — Never commit `cred.yml` or token artifacts

## Dependencies

NorenRestApiPy (broker SDK), pandas, numpy, scipy, Flask, PyYAML, requests, psutil, colorama, websocket-client, python-dateutil, pytz.

Python 3.13 (venv in `venv/`).
