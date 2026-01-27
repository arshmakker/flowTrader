"""
Configuration for Trend Following Futures Strategy
"""

# Risk limits
MAX_RISK_PCT_OF_CAPITAL = 0.05  # 5.0% of total capital per trade (allows ₹50k risk per lot with 3×ATR stop loss)
MAX_POSITION_SIZE = 1  # Maximum 1 lot
INITIAL_STOP_LOSS_ATR_MULTIPLIER = 3.0  # Initial SL = 3.0 × ATR(14)
TRAILING_STOP_LOSS_ATR_MULTIPLIER = 2.0  # Trailing SL = 2 × ATR (Chandelier)

# Hybrid Trailing Stop (profit-protection mode)
# Phases: 1=No profit, 2=Breakeven, 3=Tight, 4=Very tight
USE_HYBRID_TRAILING_STOP = True  # Enable profit-protection trailing
HYBRID_BREAKEVEN_THRESHOLD_ATR = 0.5  # Move to breakeven after 0.5× ATR profit
HYBRID_PHASE2_THRESHOLD_ATR = 1.0  # Use 1.5× ATR trailing after 1× ATR profit
HYBRID_PHASE3_THRESHOLD_ATR = 2.0  # Use 1× ATR trailing after 2× ATR profit
HYBRID_PHASE1_MULTIPLIER = 2.0  # Not in profit: 2× ATR (standard)
HYBRID_PHASE2_MULTIPLIER = 1.5  # Small profit: 1.5× ATR
HYBRID_PHASE3_MULTIPLIER = 1.0  # Large profit: 1× ATR (tight)

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
EMA_BREAK_CONFIRMATION_CHECKS = 2  # Require N consecutive breaks before exit
EMA_BREAK_TOLERANCE_PCT = 0.1  # Allow 0.1% tolerance before considering break

# Smart exit logic
PRIORITIZE_TRAILING_STOP_IN_PROFIT = True  # When in profit, prioritize trailing stop over EMA break
TRAILING_STOP_PRIORITY_DISTANCE_ATR = 0.5  # If within 0.5× ATR of stop, allow EMA break exit
