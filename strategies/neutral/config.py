"""
Configuration for Neutral Calendar Strategy
"""

# Enable/disable neutral calendar strategy globally
ENABLE_NEUTRAL_CALENDAR = True  # Set to False to disable calendar trades

# Risk limits
MAX_NET_DEBIT_PCT_OF_CAPITAL = 0.003  # 0.30% of total capital
MAX_CALENDAR_POSITIONS = 1  # Only 1 calendar at a time

# Entry conditions
IV_PERCENTILE_MIN = 40
IV_PERCENTILE_MAX = 60
ADX_MIN = 18
ADX_MAX = 25

# Exit conditions
SHORT_DECAY_THRESHOLD = 0.65  # Exit if short option decayed ≥ 65%
MIN_SHORT_DTE = 1  # Exit if days to short expiry ≤ 1
SPOT_MOVE_THRESHOLD = 0.75  # Exit if abs(spot_move) > 0.75 × expected_move
IV_SPIKE_THRESHOLD = 10.0  # Exit if IV spike ≥ +10 points without price follow-through
