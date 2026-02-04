"""
Configuration for Trend Following Futures Strategy
"""

# Risk limits
MAX_RISK_PCT_OF_CAPITAL = 0.05  # 5.0% of total capital per trade (₹50k max risk per trade)
MAX_POSITION_SIZE = 5  # Maximum 5 lots
INITIAL_STOP_LOSS_ATR_MULTIPLIER = 2.5  # Initial SL = 2.5 × ATR(14); 2.5× keeps 1 lot under 5% when ATR ~260 (lot 65)
TRAILING_STOP_LOSS_ATR_MULTIPLIER = 2.0  # Trailing SL = 2 × ATR (Chandelier)

# Stop loss exit order: use LIMIT at stop price (not market). Backtest assumes fill at stop price; production should match.

# --- Profit lock & exit (TREND_CONTINUATION: INR first, then ATR ladder; no fixed profit target) ---
# First lock: move stop to breakeven as soon as P&L >= this (INR)
HYBRID_MIN_PNL_LOCK_INR = 300  # Lock breakeven when P&L >= ₹300
# Production: trailing only (no fixed profit target). None = do not exit on profit target.
# Backtest showed trailing-only beat 0.35× ATR target on 20251222–20260116 (higher net P&L and return).
PROFIT_TARGET_ATR_MULTIPLIER = None  # Trailing stop locks gains; no cap on upside

# Hybrid Trailing Stop (incremental phases; no cap at 2.5× ATR)
# Phases: 1=No profit, 2=Breakeven, 3–4=Tight, 5=3× ATR, 6=4× ATR+
USE_HYBRID_TRAILING_STOP = True
HYBRID_BREAKEVEN_THRESHOLD_ATR = 0.5  # Breakeven phase at 0.5× ATR profit
HYBRID_PHASE2_THRESHOLD_ATR = 1.0  # 1× ATR → 1.5× ATR trail
HYBRID_PHASE3_THRESHOLD_ATR = 2.0  # 2× ATR → 1× ATR trail
HYBRID_PHASE4_THRESHOLD_ATR = 2.5  # 2.5× ATR → 0.75× ATR trail
HYBRID_PHASE5_THRESHOLD_ATR = 3.0  # 3× ATR → 0.5× ATR trail
HYBRID_PHASE6_THRESHOLD_ATR = 4.0  # 4× ATR+ → 0.25× ATR trail
HYBRID_PHASE1_MULTIPLIER = 2.0
HYBRID_PHASE2_MULTIPLIER = 1.5
HYBRID_PHASE3_MULTIPLIER = 1.0
HYBRID_PHASE4_MULTIPLIER = 0.75
HYBRID_PHASE5_MULTIPLIER = 0.5
HYBRID_PHASE6_MULTIPLIER = 0.25

# Entry conditions
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 100

# Exit conditions
EXIT_ON_REGIME_CHANGE = True  # Exit if regime != TREND_CONTINUATION
EXIT_ON_EMA_BREAK = True  # Exit if EMA structure breaks
EXIT_DAYS_BEFORE_EXPIRY = 2  # Exit if expiry is within N days

# Overnight position management
EXIT_BEFORE_MARKET_CLOSE = True  # Exit positions before market close
MARKET_CLOSE_EXIT_MINUTES = 15  # Exit N minutes before market close (3:15 PM)

# EMA structure break confirmation
EMA_BREAK_CONFIRMATION_CHECKS = 2  # Require N consecutive breaks before exit (when in profit)
EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS = 1  # When in loss, require only 1 consecutive break (faster exit on trend flip)
EMA_BREAK_TOLERANCE_PCT = 0.1  # Allow 0.1% tolerance before considering break

# Smart exit logic
PRIORITIZE_TRAILING_STOP_IN_PROFIT = True  # When in profit, prioritize trailing stop over EMA break
TRAILING_STOP_PRIORITY_DISTANCE_ATR = 0.5  # If within 0.5× ATR of stop, allow EMA break exit

# Loss-control circuit breakers (avoid holding a wrong trade all day)
MAX_INTRADAY_LOSS_INR = 15000  # Exit if unrealized loss exceeds this (₹)
MAX_TIME_IN_LOSS_MINUTES = 150  # Exit if position has been in loss for this many minutes (2.5 hours)

# Regime-change exit: require N consecutive non-TREND checks (reduce whipsaw)
REGIME_CHANGE_CONFIRMATION_CHECKS = 2  # Require N consecutive regime != TREND_CONTINUATION before exit

# Entry filters (reduce churn on choppy / high-vol days)
REENTRY_COOLDOWN_MINUTES = 30  # After STOP_LOSS_HIT, block new trend entry for this many minutes
HIGH_VOL_ATR_PERCENTILE_THRESHOLD = 90  # When ATR% >= this, require stronger trend (ADX >= HIGH_VOL_MIN_ADX)
HIGH_VOL_MIN_ADX = 40  # When ATR% >= HIGH_VOL_ATR_PERCENTILE_THRESHOLD, require ADX >= this to allow trend entry
MAX_TREND_TRADES_PER_DAY = 3  # Max trend entries per calendar day (circuit breaker)
