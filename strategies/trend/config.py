"""
Configuration for Trend Following Futures Strategy
"""

# Risk limits
MAX_RISK_PCT_OF_CAPITAL = 0.05  # 5.0% of total capital per trade (allows ₹50k risk per lot with 3×ATR stop loss)
MAX_POSITION_SIZE = 1  # Maximum 1 lot
INITIAL_STOP_LOSS_ATR_MULTIPLIER = 3.0  # Initial SL = 3.0 × ATR(14)
TRAILING_STOP_LOSS_ATR_MULTIPLIER = 2.0  # Trailing SL = 2 × ATR (Chandelier)

# Entry conditions
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 100

# Exit conditions
EXIT_ON_REGIME_CHANGE = True  # Exit if regime != TREND_CONTINUATION
EXIT_ON_EMA_BREAK = True  # Exit if EMA structure breaks
EXIT_DAYS_BEFORE_EXPIRY = 2  # Exit if expiry is within N days
