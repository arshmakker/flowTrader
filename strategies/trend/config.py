"""
Configuration for Trend Following Futures Strategy
"""

# Risk limits
MAX_RISK_PCT_OF_CAPITAL = 0.005  # 0.5% of total capital per trade
MAX_POSITION_SIZE = 1  # Maximum 1 lot
INITIAL_STOP_LOSS_ATR_MULTIPLIER = 1.5  # Initial SL = 1.5 × ATR(14)
TRAILING_STOP_LOSS_ATR_MULTIPLIER = 2.0  # Trailing SL = 2 × ATR (Chandelier)

# Entry conditions
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 100

# Exit conditions
EXIT_ON_REGIME_CHANGE = True  # Exit if regime != TREND_CONTINUATION
EXIT_ON_EMA_BREAK = True  # Exit if EMA structure breaks
