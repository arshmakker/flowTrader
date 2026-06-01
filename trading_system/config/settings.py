"""
All system parameters live here.

Do not hardcode values outside this file.
"""

# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE = True

# ══ INSTRUMENTS ════════════════════════════════════════════════════
NIFTY_LOT_SIZE = 65
NIFTY_STRIKE_STEP = 50

# ══ SHOONYA TOKENS & SYMBOLS ══════════════════════════════════════
NIFTY_SPOT_TOKEN = "26000"
INDIA_VIX_TOKEN = "26017"
NIFTY_SPOT_KEY = "NSE|Nifty 50"
INDIA_VIX_KEY = "NSE|India VIX"
NIFTY_SPOT_EXCHANGE = "NSE"

# ══ PCR CREDIT SPREAD ═════════════════════════════════════════════
PCS_LOT_SIZE = 1  # lots per entry; scale up after paper validation
PCS_SHORT_OTM_PTS = 100  # short leg distance from ATM (pts)
PCS_LONG_OTM_PTS = 300  # long leg distance from ATM (pts)
PCS_MIN_CREDIT = 20.0  # minimum net credit (pts) required to enter
PCS_STOP_MULT = 2.0  # exit if MTM loss > PCS_STOP_MULT × entry credit
PCS_PCR_BULL = 999  # bull side disabled — PCR never reaches this on NIFTY; bear-only strategy
PCS_PCR_BEAR = 0.85  # PCR below this → BEAR_CALL (backtest: 67 trades, 97% win rate, ₹2.52L)
PCS_ENTRY_DAYS = [0, 1]  # weekday indices: 0=Mon, 1=Tue
PCS_ENTRY_START = "09:20"  # IST entry window open
PCS_ENTRY_END = "10:00"  # IST entry window close
PCS_EXIT_TIME = "14:45"  # force-exit IST time on expiry day
PCS_PCR_CHAIN_COUNT = 30  # strikes each side to fetch for PCR computation

# ══ MARKET DATA ════════════════════════════════════════════════════
IC_FRESH_LTP_MAX_AGE_SEC = 10.0  # max LTP age (sec) before freshness guard skips
LTP_CACHE_SEC = 2.0
OHLCV_CACHE_SEC = 60.0

# ══ RISK / SAFETY CONTROLS ════════════════════════════════════════
DAILY_MAX_LOSS = -50_000
HALT_FILE = "data/HALT"
PID_FILE = "data/pcs.pid"
PID_FRESHNESS_TIMEOUT_SEC = 600

# ══ PAPER TRADING SIMULATION ═════════════════════════════════════
PRICE_TICK = 0.05
SLIPPAGE_PCT = 0.0005
SLIPPAGE_MIN_ABS = 1.50
SLIPPAGE_OTM_THRESHOLD = 50.0
SLIPPAGE_OTM_MIN_ABS = 3.00
PAPER_OPTION_LTP_MIN = 0.05
PAPER_OPTION_LTP_MAX = 5000.0
FEES_NIFTY_OPT = {
    "brokerage_per_order": 5.0,
    "stt_sell_pct": 0.0015,
    "stt_exercise_pct": 0.0015,
    "exch_txn_pct": 0.0000255,
    "sebi_pct": 0.000001,
    "stamp_buy_pct": 0.00003,
    "gst_pct": 0.18,
}

# ══ LIVE ORDER POLLING ════════════════════════════════════════════
POLL_INTERVAL_SEC = 1
MAX_POLL_ERRORS = 10
MAX_POLL_WAIT_SEC = 45

# ══ TRADING CALENDAR (IST) ════════════════════════════════════════
TRADING_HOLIDAYS_IST = {
    "2026-01-15",
    "2026-01-26",
    "2026-03-03",
    "2026-03-26",
    "2026-03-31",
    "2026-04-03",
    "2026-04-14",
    "2026-05-01",
    "2026-05-28",
    "2026-06-26",
    "2026-09-14",
    "2026-10-02",
    "2026-10-20",
    "2026-11-10",
    "2026-11-24",
    "2026-12-25",
}
PRE_OPEN_START_IST = "09:00"
PRE_OPEN_END_IST = "09:15"
MUHURAT_SESSIONS: list = []

# ══ OPERATOR ALERTS ═══════════════════════════════════════════════
ALERTS_ENABLED = True
ALERTS_CHANNEL = "ntfy"

# ══ DATA PATHS ════════════════════════════════════════════════════
DATA_DIR = "data"

# ══ LOGGING ═══════════════════════════════════════════════════════
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s — %(message)s"
