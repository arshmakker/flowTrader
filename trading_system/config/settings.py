"""
All system parameters live here (agent.md §4).

Do not hardcode values outside this file.
"""

# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE = True  # Flip to False to go live

# ══ INSTRUMENTS ════════════════════════════════════════════════════
NIFTY_SYMBOL = "NIFTY"
BANKNIFTY_SYMBOL = "BANKNIFTY"
NIFTY_LOT_SIZE = 25
BANKNIFTY_LOT_SIZE = 15
NIFTY_STRIKE_STEP = 50
BANKNIFTY_STRIKE_STEP = 100

# ══ SESSION ═════════════════════════════════════════════════════════
TRADE_START = "10:00"
CLASSIFY_TIME = "10:30"  # Day classification runs at this time
TRADE_END = "14:15"  # Hard close ALL positions
SIGNAL_RECHECK_SEC = 60

# ══ VIX REGIMES ══════════════════════════════════════════════════════
VIX_CALM = 13.0
VIX_NORMAL_HIGH = 17.0
VIX_DANGER = 20.0

# ══ DAY CLASSIFICATION ════════════════════════════════════════════════
TREND_MOVE_THRESHOLD = 0.015  # 1.5% from open = trending
VWAP_TREND_DISTANCE = 0.003  # 0.3% from VWAP = trending

# ══ SIGNAL PARAMETERS ════════════════════════════════════════════════
RSI_PERIOD = 14
RSI_BULL_THRESH = 52
RSI_BEAR_THRESH = 48
RSI_OVERBOUGHT = 65
RSI_OVERSOLD = 35
VWAP_BAND_PCT = 0.002  # ±0.2% = ranging
PCR_BULL = 1.0
PCR_BEAR = 0.8
MAX_PAIN_DISTANCE = 150

# ══ STRATEGY A — SHORT STRANGLE ═════════════════════════════════════
SA_OTM_PCT = 0.010
SA_TARGET_PCT = 0.45
SA_STOP_MULT = 2.0
SA_MAX_LOTS = 2

# ══ STRATEGY B — DIRECTIONAL SPREAD ════════════════════════════════
SB_OTM_PCT = 0.015
SB_TARGET_PCT = 0.70
SB_STOP_DEBIT_PCT = 0.40
SB_MAX_SPREADS = 3

# ══ STRATEGY C — FUTURES SCALP ══════════════════════════════════════
SC_TARGET_PTS = 50
SC_STOP_PTS = 28
SC_MAX_LOTS = 1

# ══ STRATEGY D — WIDE IRON CONDOR ═══════════════════════════════════
SD_OTM_PCT_NORMAL = 0.010
SD_OTM_PCT_ELEVATED = 0.020
SD_OTM_PCT_HIGH = 0.025
SD_WING_PCT = 0.015
SD_TARGET_PCT = 0.30
SD_STOP_PCT = 0.80
SD_MAX_LOTS = 2
SD_ENTRY_START = "10:15"
SD_ENTRY_END = "11:30"
SD_VIX_STABLE_MINS = 45
SD_VIX_STABLE_BAND = 1.5  # Max VIX range in stability window

# ══ STRATEGY E — DEEP ITM DIRECTIONAL ════════════════════════════════
SE_DELTA_TARGET = 0.70
SE_TARGET_PCT = 0.50
SE_STOP_PCT = 0.40
SE_MAX_LOTS = 1
SE_MIN_TREND_MOVE = 0.015
SE_ENTRY_DEADLINE = "10:45"

# ══ RISK MANAGEMENT ══════════════════════════════════════════════════
CAPITAL = 1_000_000  # ₹10L base — adjust to actual
MAX_RISK_PCT_TRADE = 0.015  # 1.5% per trade
LOSS_LIMIT_CALM = 20_000
LOSS_LIMIT_NORMAL = 15_000
LOSS_LIMIT_ELEVATED = 10_000
LOSS_LIMIT_HIGH_VIX = 7_500
MONTHLY_DD_LIMIT = 60_000

# ══ DAILY TARGETS BY REGIME ══════════════════════════════════════════
TARGET_CALM = 8_000
TARGET_NORMAL = 6_000
TARGET_ELEVATED = 4_000
TARGET_HIGH_VIX = 2_500

# ══ SHOONYA SYMBOL FORMATS ═══════════════════════════════════════════
# Futures:  'NFO|NIFTY25JANFUT'
# Options:  'NFO|NIFTY25JAN24000CE'
# VIX:      'NSE|India VIX'
# Spot:     'NSE|Nifty 50'

