# RegimeTrader Technical Reference Manual

This document describes the current code in `/Users/arshdeep/git/regimetrader` as an iron-condor paper-trading system for NIFTY and BANKNIFTY. It focuses on the files that actually drive runtime behavior today: `main.py`, `strategy_runner.py`, `trading_system/config/settings.py`, and the modules under `trading_system/core/`, `trading_system/paper/`, and `trading_system/existing/`.

## 1. Scope and Operating Mode

- The active orchestrator is [`main.py`](/Users/arshdeep/git/regimetrader/main.py).
- The system is hard-wired to paper trading by default through `PAPER_TRADE_MODE = True` in [`trading_system/config/settings.py`](/Users/arshdeep/git/regimetrader/trading_system/config/settings.py).
- Live execution is not integrated for this strategist. `main.py` wires `PaperOrderManager`, `PaperPositionTracker`, `PaperPnLEngine`, and also instantiates `GoLiveEvaluator` (`main.py:321`), though the evaluator object is currently unused after construction.
- The implemented strategy is an always-conditional iron condor flow for `NIFTY` and `BANKNIFTY`, not the older multi-strategy router still described in parts of `README.md`.

## 2. System Architecture

### 2.1 Core runtime components

The process is a single Python runtime with one main control loop and one background collector thread:

- [`main.py`](/Users/arshdeep/git/regimetrader/main.py) initializes API auth, symbol masters, market data, risk, expiry, S/R, persistence, paper execution, and two `IronCondorStrategy` instances.
- [`data_collector.py`](/Users/arshdeep/git/regimetrader/data_collector.py) runs a background polling loop that snapshots quotes into `market_data_YYYYMMDD/raw_data/...`.
- [`trading_system/existing/market_data.py`](/Users/arshdeep/git/regimetrader/trading_system/existing/market_data.py) is the synchronous quote adapter used by classification, strike selection, monitoring, and mark-to-market logic.
- [`api_helper.py`](/Users/arshdeep/git/regimetrader/api_helper.py) is the broker abstraction, including OAuth/login handling and quote throttling.

### 2.2 End-to-end data flow

The runtime data path is:

1. `ShoonyaApiPy.get_quotes()` in [`api_helper.py`](/Users/arshdeep/git/regimetrader/api_helper.py) fetches quotes through a guarded HTTP wrapper.
2. [`data_collector.py`](/Users/arshdeep/git/regimetrader/data_collector.py) polls those quotes every 5 seconds and writes raw tick files under `market_data_YYYYMMDD/raw_data/`.
3. [`MarketData`](/Users/arshdeep/git/regimetrader/trading_system/existing/market_data.py) reads live quotes on demand, caches LTPs for 2 seconds, caches VIX for 60 seconds through the regime layer, and pulls 15-minute intraday bars for VWAP/classification.
4. [`DayClassifier`](/Users/arshdeep/git/regimetrader/trading_system/core/day_classifier.py) classifies the day once at `10:30`.
5. [`RegimeFilter`](/Users/arshdeep/git/regimetrader/trading_system/core/regime_filter.py) continuously checks whether new entries are allowed based on day type, India VIX level, and VIX stability.
6. [`ExpiryManager`](/Users/arshdeep/git/regimetrader/trading_system/core/expiry_manager.py) chooses the option expiry using the 3-DTE rolling rule.
7. [`SRManager`](/Users/arshdeep/git/regimetrader/trading_system/core/sr_manager.py) computes 20-day support/resistance from stored futures ticks and pushes short strikes farther out if needed.
8. [`IronCondorStrategy`](/Users/arshdeep/git/regimetrader/trading_system/core/iron_condor.py) selects strikes, checks minimum credit, submits the four legs, monitors open positions, harvests profit, and exits on breach-driven adjustment.
9. [`PaperOrderManager`](/Users/arshdeep/git/regimetrader/trading_system/paper/paper_order_manager.py) simulates fills with slippage, taxes, and brokerage.
10. [`PaperPositionTracker`](/Users/arshdeep/git/regimetrader/trading_system/paper/paper_position_tracker.py), [`PaperPnLEngine`](/Users/arshdeep/git/regimetrader/trading_system/paper/paper_pnl_engine.py), and [`TradeLogger`](/Users/arshdeep/git/regimetrader/trading_system/core/trade_logger.py) persist open state, realized/unrealized P&L, and completed trade records. **Important gap:** only exits returned from `IronCondorStrategy.monitor()` reach `pnl_engine.record_trade()` (`main.py:399`). Exits via `force_exit()` — triggered by the combined hard stop (`main.py:404-405`) and the end-of-day flattening path (`main.py:365-367`) — return a result dict that is discarded. Those trades never appear in `paper_trades.csv` or `paper_summary.json` even though the exit orders are placed and `IronCondorStrategy._position` is set to `None` (`iron_condor.py:346`). A second related gap: `IronCondorStrategy.exit()` submits all four closing legs with `track_position=False` (`iron_condor.py:320-323`), and `PaperOrderManager.place_order()` only touches the tracker when `track_position=True` (`paper_order_manager.py:152-153`). So `PaperPositionTracker._positions` is **not** updated on exit — the original 4 short entries remain, unoffset by the closing orders. Only the strategy-level `_position` reference is cleared. Any reconciliation or tracker-derived P&L must assume the paper tracker can grow stale after exits, and that the books are additionally incomplete under stop-loss or EOD-flatten scenarios.
11. [`position_persistence.py`](/Users/arshdeep/git/regimetrader/trading_system/core/position_persistence.py) snapshots strategy state, tracker state, and risk state after each loop so the process can resume after restart.

### 2.3 Control loop sequence

The main loop in [`main.py`](/Users/arshdeep/git/regimetrader/main.py) runs every `SIGNAL_RECHECK_SEC = 60` seconds and does the following:

1. Starts `DataCollector` when `is_market_hours()` becomes true.
2. Exits the session when `is_market_closed_ist()` returns true.
3. At or after `TRADE_END = 15:10`, force-exits only if the next calendar day is not a trading day; otherwise positions are carried overnight.
4. At or after `CLASSIFY_TIME = 10:30`, computes and locks the day classification.
5. Monitors any active NIFTY and BANKNIFTY condors for harvest or adjustment exits.
6. Applies the combined hard-stop check across all active condors.
7. If trading is not halted and the regime gate is open, attempts fresh entry for any inactive instrument.
8. Writes `data/pnl_snapshot.json` and `data/open_positions.json` every cycle.

### 2.4 Persistence and generated artifacts

The main files produced by the system are:

- `data/open_positions.json`: persisted strategy, tracker, P&L, and risk state.
- `data/pnl_snapshot.json`: dashboard-friendly current realized and unrealized P&L.
- `data/paper_summary.json`: cumulative summary updated after trades.
- `data/paper_trades.csv`: completed trade records.
- `data/paper_signals.log`: append-only signal log. Supported by `TradeLogger.log_signal()` and consumed by both dashboards, but **no runtime code path currently calls it** — only tests exercise the writer. Treat as present-but-empty under normal operation.
- `market_data_YYYYMMDD/raw_data/...`: raw collector output used later by the S/R manager.
- `logs/ic_system_YYYYMMDD.log`: main rotating runtime log.

## 3. Regime Detection Logic

The strategy has two layers of regime gating:

- A one-time day classification at `10:30`.
- A continuous VIX gate that must stay permissive for entry.

### 3.1 Day classification

[`DayClassifier.classify()`](/Users/arshdeep/git/regimetrader/trading_system/core/day_classifier.py) uses:

- `open_px = MarketData.get_open_price("NIFTY")`
- `current = MarketData.get_ltp(settings.NIFTY_SPOT_KEY)`
- `vwap = SignalEngine.compute_vwap_value()` — **called with no argument** (`day_classifier.py:62`)

The classifier then computes:

```text
move_pct = (current - open_px) / open_px
vwap_dist = abs(current - vwap) / vwap  if vwap else 0.0
abs_move = abs(move_pct)
```

**Runtime bug / known behavioral gap.** `SignalEngine.compute_vwap_value()` is stateless and returns `0.0` when `ohlcv_df` is `None` (`signal_engine.py:53-56`). Because `DayClassifier` never passes the dataframe, `vwap` is always `0.0` and `vwap_dist` is always `0.0`. `main.py:389-390` does compute VWAP with the correct dataframe but discards the return value — no state is cached on the engine, so the call has no effect on the subsequent `classifier.classify()`. Consequence: the trending branch below (which requires `vwap_dist ≥ 0.3%`) **can never fire**. Every day effectively classifies as `RANGING`. This is consistent with the observed behavior that entries are gated on `RANGING` and the system does place condors.

Configured thresholds in [`settings.py`](/Users/arshdeep/git/regimetrader/trading_system/config/settings.py):

- `TREND_MOVE_THRESHOLD = 0.015` meaning 1.5%
- `VWAP_TREND_DISTANCE = 0.003` meaning 0.3%
- `TREND_HIGH_CONFIDENCE = 0.02` meaning 2%

Decision rules:

- If `abs_move >= 1.5%` and `vwap_dist >= 0.3%`, classify as:
  - `TRENDING_UP` when `move_pct > 0`
  - `TRENDING_DOWN` when `move_pct <= 0`
- Confidence is:
  - `HIGH` if `abs_move > 2%`
  - otherwise `MEDIUM`
- If `abs_move >= 1.5%` but `vwap_dist < 0.3%`, classify as `RANGING` with `MEDIUM` confidence.
- Otherwise classify as `RANGING` with `HIGH` confidence.
- If open price had to be sourced by a full fallback to `MarketData.get_ltp()` (i.e. the broker quote call itself failed or returned nothing usable), `_open_price_fallback` is flagged and `DayClassifier` downgrades confidence to `LOW` via `is_open_price_reliable()`.
- Note a gap in the current implementation: when the quote payload is returned but its `o` field is missing/zero, `get_open_price()` silently substitutes `q["lp"]` from the same payload (`market_data.py:140`). This path does **not** set the unreliable flag, so the day is not downgraded to `LOW` even though the "open" is actually just a current tick.
- If open price or current price is non-positive, the classifier returns `RANGING` with `LOW` confidence.

Important implementation detail:

- The result is locked after the first call. `DayClassifier` stores `_result` and returns the same object for the rest of the day unless `reset()` is called.

### 3.2 VWAP math

[`SignalEngine.compute_vwap_value()`](/Users/arshdeep/git/regimetrader/trading_system/core/signal_engine.py) computes session VWAP from the intraday 15-minute bars:

```text
typical_price = (high + low + close) / 3
vwap = sum(typical_price * volume) / sum(volume)
```

Those bars come from `MarketData.get_ohlcv_df()`, which first attempts `api.get_time_price_series(... interval=15)` and falls back to locally accumulated bars if API bars are unavailable.

### 3.3 VIX regime gate

[`RegimeFilter`](/Users/arshdeep/git/regimetrader/trading_system/core/regime_filter.py) maintains a cached India VIX value and a rolling history of samples.

Inputs and settings:

- India VIX token: `INDIA_VIX_TOKEN = "26017"`
- Entry VIX ceiling: `IC_VIX_MAX = 30.0`
- Stability window: `IC_VIX_STABLE_MINS = 45`
- Stability band: `IC_VIX_STABLE_BAND = 1.5`
- VIX cache TTL: `VIX_CACHE_SEC = 60.0`

VIX sourcing in `get_vix()` is websocket-first when a `quote_stream` is injected:

- It first attempts `quote_stream.get_quote(...)` with `settings.WS_VIX_MAX_AGE_SEC`.
- If that returns `None` and `settings.WS_STRICT_MODE` is falsey, it falls back to `api.get_quotes(...)`.
- If `quote_stream` is absent, it uses `api.get_quotes(...)` directly.

The entry gate in `get_regime_gate(day_type)` opens only when all of the following are true:

```text
day_type == "RANGING"
vix < 30.0
VIX has stayed within a 1.5-point band for the last 45 minutes
```

The stability check requires at least 5 samples in the last 45 minutes:

```text
recent_history = VIX samples from last 45 minutes
stable if max(recent_history) - min(recent_history) <= 1.5
```

`RegimeFilter.is_vix_falling()` also exists for the post-stop recovery rule. It returns true when the latest VIX sample is below the average of the prior 9 samples from the most recent 10-sample window.

### 3.4 Strike regime selection

Once the entry gate is open, [`IronCondorStrategy.get_vix_tier_params()`](/Users/arshdeep/git/regimetrader/trading_system/core/iron_condor.py) converts VIX into strike distance and wing width:

| VIX band | OTM distance | Wing width |
| --- | ---: | ---: |
| `< 14` | 150 points | 50 points |
| `14 <= VIX < 20` | 200 points | 100 points |
| `>= 20` | 300 points | 150 points |

These values come from [`settings.py`](/Users/arshdeep/git/regimetrader/trading_system/config/settings.py).

Strike computation in `calculate_strikes()` is:

```text
short_call = round((spot + otm_distance) / step) * step
short_put  = round((spot - otm_distance) / step) * step
```

Where:

- `step = 50` for NIFTY
- `step = 100` for BANKNIFTY

After that:

- `SRManager.apply_buffer()` pushes the short call above `20-day high + 50`
- `SRManager.apply_buffer()` pushes the short put below `20-day low - 50`
- Long wings are then set by adding/subtracting the regime width, rounded to at least one strike step

### 3.5 Support/resistance proxy

[`SRManager.get_20day_high_low()`](/Users/arshdeep/git/regimetrader/trading_system/core/sr_manager.py) scans the latest `market_data_*` directories and uses stored futures raw data as the proxy source for 20-day levels:

- The loop stops after 20 qualifying trading-day directories have been consumed.
- Only Monday-Friday directories are counted.
- Dates in `TRADING_HOLIDAYS_IST` are skipped.
- The manager looks inside `raw_data/futures/`.
- It reads the first file matching the instrument name for that day and extracts:
  - daily high = max of `ltp`
  - daily low = min of `ltp`
- The final resistance/support pair is:

```text
sr_high = max(all_daily_highs)
sr_low  = min(all_daily_lows)
```

If no valid data is found, it returns `(0.0, 0.0)` and the strike-buffer rule is effectively bypassed for that entry.

### 3.6 Expiry regime

[`ExpiryManager.get_expiry()`](/Users/arshdeep/git/regimetrader/trading_system/core/expiry_manager.py) applies the 3-DTE rolling rule:

```text
dte = nearest_expiry - current_date
if dte < IC_DTE_THRESHOLD (3):
    use next available expiry
else:
    use nearest expiry
```

The selected expiry is returned in `DD-MMM-YYYY` format and used to build option symbols.

### 3.7 Auxiliary signal engine

[`SignalEngine`](/Users/arshdeep/git/regimetrader/trading_system/core/signal_engine.py) contains extra indicator logic:

- VWAP bias
- Wilder RSI
- Put-Call Ratio
- Max Pain
- A majority-vote consensus engine

At present, only `compute_vwap_value()` is used directly by the main iron-condor flow. The broader signal consensus engine is present but not used by `main.py` for entry decisions.

## 4. Safety and Risk Controls

### 4.1 Hard-coded position and timing limits

The most important hard-coded controls from [`settings.py`](/Users/arshdeep/git/regimetrader/trading_system/config/settings.py) are:

- `TRADE_START = "10:00"`
- `CLASSIFY_TIME = "10:30"`
- `TRADE_END = "15:10"`
- `IC_LOT_SIZE = 10`
- `IC_DTE_THRESHOLD = 3`
- `IC_SR_BUFFER = 50`
- `IC_HARVEST_PCT = 0.01`
- `IC_STOP_LOSS_MULT = 3.0`
- `IC_HARD_STOP_CONFIRM_TICKS = 2`
- `IC_MIN_CREDIT = 18.0`
- `RECOVERY_DEADLINE = "13:00"`

### 4.2 Entry rejection: minimum credit

Before placing any leg, [`IronCondorStrategy.enter()`](/Users/arshdeep/git/regimetrader/trading_system/core/iron_condor.py) calculates:

```text
net_credit_unit = (short_call_ltp + short_put_ltp) - (long_call_ltp + long_put_ltp)
```

If:

```text
net_credit_unit < IC_MIN_CREDIT
```

the trade is rejected and logged with an `IC_REJECT reason=CREDIT_BELOW_MIN` record.

### 4.3 Atomic four-leg entry with rollback

The strategy places the four legs in this order:

1. sell short call
2. sell short put
3. buy long call
4. buy long put

If any order returns a non-`COMPLETE` status:

- entry is aborted
- `_rollback_partial_entry()` submits reverse orders for any already-filled legs
- no active strategy state is created

This is the main defense against partial iron-condor creation in paper mode.

### 4.4 Profit harvest logic

During monitoring, the strategy computes:

```text
current_premium = (sc + sp) - (lc + lp)
pnl_unit = entry_credit - current_premium
total_pnl = pnl_unit * lots * lot_size
harvest_trigger = max_profit * IC_HARVEST_PCT
```

If:

```text
total_pnl >= harvest_trigger
```

the condor is exited with reason `PROFIT_HARVEST`.

Because `IC_HARVEST_PCT = 0.01`, the harvest threshold is 1% of the position’s maximum profit as encoded by entry credit.

### 4.5 Adjustment gate on strike breach

Still inside `monitor()`:

- The strategy only considers adjustment if the open condor is currently profitable.
- It fetches the current spot for the underlying.
- If spot breaches either short strike:

```text
spot >= short_call_strike
or
spot <= short_put_strike
```

the position is exited with reason `ADJUSTMENT_REQUIRED`.

The actual “adjustment” is implemented as exit-and-allow-reentry on a later loop, not as an in-place roll.

### 4.6 Combined hard stop-loss

[`RiskManager.check_combined_stop_loss()`](/Users/arshdeep/git/regimetrader/trading_system/core/risk_manager.py) evaluates all active instruments together.

For each active condor with valid quotes:

```text
current_premium = (sc + sp) - (lc + lp)
unrealized = (entry_credit - current_premium) * lots * lot_size
```

The portfolio stop limit is:

```text
stop_limit = - total_max_profit * IC_STOP_LOSS_MULT
```

With `IC_STOP_LOSS_MULT = 3.0`, trading halts when the combined unrealized loss reaches 3x combined max profit.

This stop requires confirmation:

- A breach increments `_stop_breach_streak`
- The stop is only triggered when streak >= `IC_HARD_STOP_CONFIRM_TICKS`
- Current setting: 2 consecutive valid breaches

When triggered:

- `risk.halted = True`
- `stop_hit_at` is recorded
- `main.py` force-exits all active strategies

### 4.7 Invalid quote defense in the stop-loss path

The hard-stop logic deliberately refuses to act on incomplete quote snapshots.

If at least one active strategy exists but any active strategy has one or more non-positive leg prices:

- the stop check is skipped for that loop
- `_stop_breach_streak` is reset to zero

This prevents bad or missing data from falsely tripping the combined hard stop.

### 4.8 Recovery-mode gate

`RiskManager.can_enter_recovery()` allows a future single-sided recovery only when:

```text
trading is halted
and recovery mode is not already active
and stop_hit_at exists
and stop time is before 13:00
and (VIX is stable or VIX is falling)
```

The gating logic exists, but `main.py` does not currently wire an execution flow for a recovery trade.

### 4.9 End-of-day and carry behavior

Current behavior in [`main.py`](/Users/arshdeep/git/regimetrader/main.py):

- At or after `15:10` (`TRADE_END`), positions are force-exited only if the next day is not a trading day.
- Otherwise the system persists active positions and carries them overnight.
- `EOW_EXIT_TIME = "15:15"` exists in `settings.py` but is **not referenced by any runtime code**. The older "fully flat every Thursday" rule described in `AGENTS.md` is not wired; only the next-day-is-trading check at `TRADE_END` controls exit.

## 5. Quote Integrity and API Error Handling

### 5.1 Quote throttling

[`ShoonyaApiPy`](/Users/arshdeep/git/regimetrader/api_helper.py) wraps every quote read with a sliding-window limiter:

- Global hard cap: `10` quote calls per second
- Global minute cap: default `170` per minute
- Background low-priority lane: default `4` per second and `120` per minute

The collector uses `priority="low"`, leaving capacity for strategy and risk paths.

### 5.2 Safe quote requests

`api_helper.py` replaces the SDK quote path with:

- `_quote_request()`: explicit HTTP POST, explicit parse checks, explicit broker error messages
- `get_quotes_safe()`: retries with backoff and stores broker error details
- `get_quotes()`: compatibility wrapper over `get_quotes_safe()`

Error cases handled explicitly include:

- missing service config
- missing route mapping
- HTTP failure
- empty body
- non-JSON body
- broker-level rejection
- OAuth-header failure followed by a `jKey` retry

### 5.3 OAuth and login handling

[`main.py`](/Users/arshdeep/git/regimetrader/main.py) supports both legacy Shoonya login and OAuth login. The OAuth path includes:

- cached token reuse
- token validation
- automatic auth-code capture through an external command if configured
- token exchange retry loop
- final manual fallback to pasted auth code

`ShoonyaApiPy` stores the last broker error so the orchestrator can surface meaningful auth failures instead of generic SDK parse errors.

### 5.4 MarketData quote sanity checks

[`MarketData.get_ltp()`](/Users/arshdeep/git/regimetrader/trading_system/existing/market_data.py) applies option-price sanity bounds:

- valid option LTP band is `[0.05, 5000.0]`
- if a new option quote is outside that range:
  - use the last valid option LTP if one exists
  - otherwise return `0.0`

This protects the strategy from token mixups or corrupted broker responses.

### 5.5 Paper-order quote sanity checks

[`PaperOrderManager.place_order()`](/Users/arshdeep/git/regimetrader/trading_system/paper/paper_order_manager.py) enforces another quote gate:

- reject if LTP is missing and no explicit fallback price is provided
- reject if option LTP is outside `[0.05, 5000.0]`

Rejected paper orders return structured payloads with:

- `status = "REJECTED"`
- `reason = "missing_ltp"` or `reason = "suspicious_option_ltp"`

### 5.6 Paper fill model

Paper fills are not mid-price fills. They include:

- adverse slippage on both buys and sells
- 3x slippage multiplier for cheap OTM options below `SLIPPAGE_OTM_THRESHOLD = 50`
- rounding to `PRICE_TICK = 0.05`
- STT
- flat brokerage per order

This keeps P&L closer to executable behavior than raw mark-at-last simulation.

### 5.7 Collector error handling

[`DataCollector`](/Users/arshdeep/git/regimetrader/data_collector.py) catches per-symbol failures and keeps the cycle alive.

Special handling exists for DNS-resolution-style failures:

- errors are inspected for common DNS failure signatures
- the collector backs off for `5` seconds on DNS errors
- collection continues instead of killing the thread

## 6. Notable Implementation Characteristics

- Only the NIFTY-based day classification drives the entry gate for both NIFTY and BANKNIFTY condors.
- `SignalEngine` contains richer directional logic than the orchestrator currently uses.
- The system persists and restores halt state, P&L state, tracker state, and strategy state across restarts.
- Overnight carry is currently permitted across adjacent trading days.
- The codebase is consistent with a paper-first execution model and does not expose a complete live order path for this strategist.

## 7. Primary Files to Read

For system understanding, the most important files are:

- [`main.py`](/Users/arshdeep/git/regimetrader/main.py)
- [`strategy_runner.py`](/Users/arshdeep/git/regimetrader/strategy_runner.py)
- [`trading_system/config/settings.py`](/Users/arshdeep/git/regimetrader/trading_system/config/settings.py)
- [`trading_system/core/iron_condor.py`](/Users/arshdeep/git/regimetrader/trading_system/core/iron_condor.py)
- [`trading_system/core/day_classifier.py`](/Users/arshdeep/git/regimetrader/trading_system/core/day_classifier.py)
- [`trading_system/core/regime_filter.py`](/Users/arshdeep/git/regimetrader/trading_system/core/regime_filter.py)
- [`trading_system/core/risk_manager.py`](/Users/arshdeep/git/regimetrader/trading_system/core/risk_manager.py)
- [`trading_system/core/expiry_manager.py`](/Users/arshdeep/git/regimetrader/trading_system/core/expiry_manager.py)
- [`trading_system/core/sr_manager.py`](/Users/arshdeep/git/regimetrader/trading_system/core/sr_manager.py)
- [`trading_system/existing/market_data.py`](/Users/arshdeep/git/regimetrader/trading_system/existing/market_data.py)
- [`trading_system/paper/paper_order_manager.py`](/Users/arshdeep/git/regimetrader/trading_system/paper/paper_order_manager.py)
- [`trading_system/core/position_persistence.py`](/Users/arshdeep/git/regimetrader/trading_system/core/position_persistence.py)
- [`api_helper.py`](/Users/arshdeep/git/regimetrader/api_helper.py)
- [`data_collector.py`](/Users/arshdeep/git/regimetrader/data_collector.py)
