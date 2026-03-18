"""
Main Orchestrator — Iron Condor Trading System (agents.md).

Wires all modules together for high-frequency Nifty/BankNifty IC trading.
"""

import os
import sys
import logging
import threading
import time as _time
import yaml
from datetime import datetime, time as dtime

from api_helper import ShoonyaApiPy
from symbol_manager import SymbolManager
from data_collector import DataCollector
from strategy_runner import is_market_hours, is_market_closed_ist

from trading_system.config import settings
from trading_system.core.regime_filter import RegimeFilter
from trading_system.core.day_classifier import DayClassifier
from trading_system.core.signal_engine import SignalEngine
from trading_system.core.iron_condor import IronCondorStrategy
from trading_system.core.risk_manager import RiskManager
from trading_system.core.expiry_manager import ExpiryManager
from trading_system.core.sr_manager import SRManager
from trading_system.core.trade_logger import TradeLogger
from trading_system.existing.market_data import MarketData
from trading_system.paper.go_live_evaluator import GoLiveEvaluator

if settings.PAPER_TRADE_MODE:
    from trading_system.paper.paper_order_manager import PaperOrderManager as OrderMgr
    from trading_system.paper.paper_position_tracker import PaperPositionTracker as PosMgr
    from trading_system.paper.paper_pnl_engine import PaperPnLEngine as PnLEngine
else:
    # Live mode not yet fully integrated for this specific strategist
    OrderMgr = None
    PosMgr = None
    PnLEngine = None

def setup_logging() -> None:
    from logging.handlers import RotatingFileHandler
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    log_path = os.path.join(settings.LOG_DIR, f"ic_system_{datetime.now().strftime('%Y%m%d')}.log")
    formatter = logging.Formatter(settings.LOG_FORMAT)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    fh = RotatingFileHandler(log_path, maxBytes=settings.LOG_MAX_BYTES, backupCount=settings.LOG_BACKUP_COUNT)
    fh.setFormatter(formatter)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

def initialize_api() -> ShoonyaApiPy:
    with open("cred.yml", "r") as f:
        creds = yaml.safe_load(f)
    api = ShoonyaApiPy()
    factor2 = os.environ.get("TWOFA", "").strip()
    if not factor2:
        factor2 = input("Enter 2FA code: ").strip()
    api.login(userid=creds["user"], password=creds["pwd"], twoFA=factor2, 
              vendor_code=creds["vc"], api_secret=creds["apikey"], imei=creds["imei"])
    return api

def run():
    setup_logging()
    log = logging.getLogger("main")
    log.info("=== IRON CONDOR SYSTEM STARTING ===")

    api = initialize_api()
    sm = SymbolManager(api)
    sm.load_symbol_files()
    
    collector = DataCollector(api, sm)
    md = MarketData(api, sm)
    
    # Core Components
    regime = RegimeFilter(api)
    signals = SignalEngine()
    classifier = DayClassifier(md, signals)
    risk = RiskManager()
    expiry_mgr = ExpiryManager(sm)
    sr_mgr = SRManager()
    trade_logger = TradeLogger()
    
    if settings.PAPER_TRADE_MODE:
        pos_mgr = PosMgr()
        order_mgr = OrderMgr(md, pos_mgr)
        pnl_engine = PnLEngine(pos_mgr, md, trade_logger)
        evaluator = GoLiveEvaluator()
    
    # Strategies
    nifty_ic = IronCondorStrategy(order_mgr, md, 'NIFTY')
    banknifty_ic = IronCondorStrategy(order_mgr, md, 'BANKNIFTY')
    strats = [nifty_ic, banknifty_ic]

    day_class = None
    collection_started = False
    
    log.info("Entering main loop...")
    
    try:
        while True:
            now = datetime.now()
            now_t = now.time()
            
            # 1. Market Hours & Data Collection
            if not collection_started and is_market_hours():
                collector.start_collection()
                collection_started = True
            
            if is_market_closed_ist():
                log.info("Market closed. Exiting loop.")
                break

            # 2. Hard Close & EOW Close
            if now_t >= datetime.strptime(settings.TRADE_END, "%H:%M").time():
                for s in strats:
                    if s.is_active(): s.force_exit()
                log.info("Daily session ended. Closed all positions.")
                _time.sleep(3600)
                continue

            # 3. Day Classification (10:30 AM)
            if now_t >= datetime.strptime(settings.CLASSIFY_TIME, "%H:%M").time() and day_class is None:
                ohlcv = md.get_ohlcv_df()
                signals.compute_vwap_value(ohlcv)
                day_class = classifier.classify()
                log.info(f"Day Classified: {day_class.day_type} ({day_class.confidence})")

            if day_class is None:
                _time.sleep(30)
                continue

            # 4. Monitoring & Harvest Cycle
            for s in strats:
                if s.is_active():
                    result = s.monitor()
                    if result:
                        pnl_engine.record_trade(s.instrument, result['pnl'], result)
                        risk.update_pnl(result['pnl'])

            # 5. Combined Stop Loss
            if risk.check_combined_stop_loss(strats):
                for s in strats:
                    if s.is_active(): s.force_exit()
                log.critical("COMBINED STOP LOSS HIT - Trading Halted.")

            # 6. Entry Logic (If Gates pass and not active)
            if not risk.halted and regime.get_regime_gate(day_class.day_type):
                for s in strats:
                    if not s.is_active():
                        # Fetch context for entry
                        spot = md.get_ltp(settings.NIFTY_SPOT_KEY if s.instrument == 'NIFTY' else "NSE|Nifty Bank")
                        vix = regime.get_vix()
                        sr_high, sr_low = sr_mgr.get_20day_high_low(s.instrument)
                        expiry = expiry_mgr.get_expiry(s.instrument)
                        
                        if spot > 0 and expiry:
                            s.enter(spot, vix, sr_high, sr_low, sr_mgr, expiry, settings.IC_LOT_SIZE)

            _time.sleep(settings.SIGNAL_RECHECK_SEC)

    except KeyboardInterrupt:
        log.info("Interrupted by user. Exiting...")
    finally:
        if collection_started:
            collector.stop_collection()

if __name__ == "__main__":
    run()
