"""
All system parameters live here (agents.md).

Do not hardcode values outside this file.
"""

# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE = True  # Flip to False to go live

# ══ INSTRUMENTS ════════════════════════════════════════════════════
NIFTY_SYMBOL = "NIFTY"
BANKNIFTY_SYMBOL = "BANKNIFTY"
NIFTY_LOT_SIZE = 65  # updated for 2026
BANKNIFTY_LOT_SIZE = 30  # updated for 2026
NIFTY_STRIKE_STEP = 50
BANKNIFTY_STRIKE_STEP = 100

# ══ SHOONYA TOKENS & SYMBOLS ══════════════════════════════════════
NIFTY_SPOT_TOKEN = "26000"
INDIA_VIX_TOKEN = "26017"
NIFTY_SPOT_KEY = "NSE|Nifty 50"        # LTP lookup key
BANKNIFTY_SPOT_KEY = "NSE|Nifty Bank"  # BUG-08: per-instrument classification
INDIA_VIX_KEY = "NSE|India VIX"
NIFTY_SPOT_EXCHANGE = "NSE"

# ══ SESSION ═════════════════════════════════════════════════════════
TRADE_START = "10:00"
CLASSIFY_TIME = "10:30"
TRADE_END = "14:15"  # Hard close ALL positions (per CLAUDE.md axiom; 75 min buffer before 15:30 expiry settlement)
SIGNAL_RECHECK_SEC = 60
PRE_MARKET_SLEEP_SEC = 30
MONITORING_SLEEP_SEC = 60
PNL_LOG_INTERVAL_SEC = 300  # periodic P&L summary every 5 minutes
VIX_HISTORY_WINDOW_SEC = 3600  # keep last 60 min of VIX readings

# ══ IRON CONDOR PARAMETERS ══════════════════════════════════════════
IC_LOT_SIZE = 10  # 10 lots per instrument for greater absolute profit
IC_VIX_MAX = 30.0
IC_VIX_STABLE_MINS = 45
IC_VIX_STABLE_BAND = 1.5
IC_DTE_THRESHOLD = 3  # Roll to next week if current weekly < 3 DTE
IC_SR_BUFFER = 50     # 50-point buffer from 20-day H/L
IC_HARVEST_PCT = 0.01 # 1% of max profit for harvest and re-entry
IC_STOP_LOSS_MULT = 3.0 # 3x max profit stop-loss
IC_HARD_STOP_CONFIRM_TICKS = 2  # require 2 consecutive valid breaches before halt
IC_CREDIT_WIDTH_PCT = 0.25 # Legacy - now using IC_MIN_CREDIT
IC_MIN_CREDIT = 18.0  # Minimum ₹18 credit per lot (lowered for more entries)

# ══ TRADING CALENDAR (IST) ═══════════════════════════════════════════
# Used by strategy runner gates (data collection / strategy loop) and
# by data-quality guards to avoid treating holiday data as a trading day.
# Weekends are handled separately; list only non-weekend holidays here.
TRADING_HOLIDAYS_IST = {
    '2026-01-15',  # Municipal Corporation Election - Maharashtra
    '2026-01-26',  # Republic Day
    '2026-03-03',  # Holi
    '2026-03-26',  # Shri Ram Navami
    '2026-03-31',  # Shri Mahavir Jayanti
    '2026-04-03',  # Good Friday
    '2026-04-14',  # Dr. Baba Saheb Ambedkar Jayanti
    '2026-05-01',  # Maharashtra Day
    '2026-05-28',  # Bakri Id
    '2026-06-26',  # Muharram
    '2026-09-14',  # Ganesh Chaturthi
    '2026-10-02',  # Mahatma Gandhi Jayanti
    '2026-10-20',  # Dussehra
    '2026-11-10',  # Diwali-Balipratipada
    '2026-11-24',  # Prakash Gurpurb Sri Guru Nanak Dev
    '2026-12-25',  # Christmas
}


# ══ VIX TIERS & OTM DISTANCES ══════════════════════════════════════
# VIX < 14: 150-200 OTM, 50 width
VIX_LOW_LIMIT = 14.0
VIX_LOW_OTM = 150
VIX_LOW_WIDTH = 50

# VIX 14-20: 200-250 OTM, 100 width
VIX_NORMAL_LIMIT = 20.0
VIX_NORMAL_OTM = 200
VIX_NORMAL_WIDTH = 100

# VIX > 20: 300+ OTM, 150 width
VIX_HIGH_OTM = 300
VIX_HIGH_WIDTH = 150

# ══ DAY CLASSIFICATION ════════════════════════════════════════════════
TREND_MOVE_THRESHOLD = 0.015  # 1.5% from open = trending
VWAP_TREND_DISTANCE = 0.003  # 0.3% from VWAP = trending
TREND_HIGH_CONFIDENCE = 0.02  # >2% move => HIGH confidence trending


# ══ RISK MANAGEMENT ══════════════════════════════════════════════════
CAPITAL = 1_000_000  # ₹10L base
RECOVERY_DEADLINE = "13:00" # Single-sided recovery only before 1 PM

# ══ PAPER TRADING SIMULATION ═════════════════════════════════════════
PRICE_TICK = 0.05
SLIPPAGE_PCT = 0.0005
SLIPPAGE_MIN_ABS = 0.25
SLIPPAGE_OTM_THRESHOLD = 50.0
# Quote sanity guard to avoid corrupt option fills from bad ticks/token mixups.
PAPER_OPTION_LTP_MIN = 0.05
PAPER_OPTION_LTP_MAX = 5000.0
STT_OPTIONS_SELL = 0.0005
STT_FUTURES = 0.0001
BROKERAGE_PER_ORDER = 5.0

# ══ CACHE TTLs ═══════════════════════════════════════════════════════
LTP_CACHE_SEC = 2.0
VIX_CACHE_SEC = 60.0
OHLCV_CACHE_SEC = 60.0

# ══ GO-LIVE EVALUATOR THRESHOLDS (IC SPECIFIC) ═══════════════════════
GL_MIN_TRADES = 20
GL_OVERALL_WR = 60.0
GL_MIN_TSL_EXITS = 5
GL_WIN_LOSS_RATIO = 1.3
GL_MIN_DAYS = 10
GL_MAX_DD_PCT = 5.0
GL_MAX_PLAUSIBLE_WR = 90.0
GL_PLAUSIBLE_WR_MIN_TRADES = 30

# ══ DATA PATHS ═══════════════════════════════════════════════════════
DATA_DIR = "data"
WEB_DASHBOARD_PORT = 5050

# ══ LOGGING ══════════════════════════════════════════════════════════
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s — %(message)s"
