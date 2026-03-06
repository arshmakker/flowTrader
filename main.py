"""
Main orchestrator (agent.md §19).

Wires all modules together. Runs the main trading loop.
Build order: config → regime → classifier → signals → strategies → risk → paper → dashboards → this.
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
from strategy_runner import (
    is_market_hours,
    is_market_closed_ist,
    get_option_chain_data,
    get_nifty_spot_price,
    get_all_eligible_expiries,
)

from trading_system.config import settings
from trading_system.core.regime_filter import RegimeFilter
from trading_system.core.day_classifier import DayClassifier
from trading_system.core.signal_engine import SignalEngine
from trading_system.core.strategy_a import StrategyA
from trading_system.core.strategy_b import StrategyB
from trading_system.core.strategy_c import StrategyC
from trading_system.core.strategy_d import StrategyD
from trading_system.core.strategy_e import StrategyE
from trading_system.core.risk_manager import RiskManager
from trading_system.core.daily_target import DailyTarget
from trading_system.core.trade_logger import TradeLogger
from trading_system.existing.market_data import MarketData
from trading_system.paper.go_live_evaluator import GoLiveEvaluator

if settings.PAPER_TRADE_MODE:
    from trading_system.paper.paper_order_manager import PaperOrderManager as OrderMgr
    from trading_system.paper.paper_position_tracker import PaperPositionTracker as PosMgr
    from trading_system.paper.paper_pnl_engine import PaperPnLEngine as PnLEngine
else:
    OrderMgr = None  # type: ignore[assignment,misc]
    PosMgr = None  # type: ignore[assignment,misc]
    PnLEngine = None  # type: ignore[assignment,misc]

try:
    from colorama import init as colorama_init, Fore
    colorama_init(autoreset=True)
except ImportError:
    class Fore:  # type: ignore[no-redef]
        GREEN = YELLOW = RED = CYAN = ""


def setup_logging() -> None:
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"trading_system_{datetime.now().strftime('%Y%m%d')}.log")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(formatter)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(ch)
    logging.info("Log file: %s", os.path.abspath(log_path))


def initialize_api() -> ShoonyaApiPy:
    try:
        with open("cred.yml", "r") as f:
            creds = yaml.safe_load(f)
    except FileNotFoundError:
        raise ValueError("cred.yml not found. Create from cred.yml.template")

    api = ShoonyaApiPy()
    factor2 = os.environ.get("TWOFA", "").strip()
    if not factor2:
        if not sys.stdin.isatty():
            raise ValueError("No TTY and TWOFA not set. Run: TWOFA=<code> python main.py")
        factor2 = input(Fore.CYAN + "Enter your 2FA code: ").strip()
    if not factor2:
        raise ValueError("2FA code is required")

    logging.info("Logging in to Shoonya API...")
    ok = api.login(
        userid=creds["user"],
        password=creds["pwd"],
        twoFA=factor2,
        vendor_code=creds["vc"],
        api_secret=creds["apikey"],
        imei=creds["imei"],
    )
    if not ok:
        raise ValueError("Shoonya login failed")
    logging.info(Fore.GREEN + "Logged in successfully")
    return api


def run() -> None:
    setup_logging()
    log = logging.getLogger("main")

    mode_str = "PAPER" if settings.PAPER_TRADE_MODE else "LIVE"
    log.info("=== RegimeTrader — %s MODE ===", mode_str)

    if not settings.PAPER_TRADE_MODE:
        print("[LIVE MODE] Real orders will be placed.")
        print("Type YES to confirm: ", end="")
        if input().strip() != "YES":
            sys.exit(0)

    api = initialize_api()

    sm = SymbolManager(api)
    try:
        sm.load_symbol_files()
    except FileNotFoundError:
        log.warning("Symbol files missing — downloading...")
        sm.download_master_files()
        sm.load_symbol_files()

    collector = DataCollector(api, sm)
    md = MarketData(api, sm)

    # ── Module initialisation ───────────────────────────────────────────

    if settings.PAPER_TRADE_MODE:
        order_mgr = OrderMgr(md)
        pos_mgr = PosMgr()
    else:
        log.critical("Live order manager not yet implemented — aborting")
        sys.exit(1)

    trade_logger = TradeLogger()
    pnl_engine = PnLEngine(pos_mgr, md, trade_logger) if settings.PAPER_TRADE_MODE else None

    regime = RegimeFilter(api)
    signals = SignalEngine()
    classifier = DayClassifier(md, signals)
    risk = RiskManager()
    target = DailyTarget()
    evaluator = GoLiveEvaluator()

    strats = {
        "A": StrategyA(order_mgr, md),
        "B": StrategyB(order_mgr, md),
        "C": StrategyC(order_mgr, md),
        "D": StrategyD(order_mgr, md),
        "E": StrategyE(order_mgr, md),
    }

    # ── Dashboards (daemon threads) ─────────────────────────────────────

    if settings.PAPER_TRADE_MODE:
        try:
            from trading_system.dashboard.terminal_dashboard import TerminalDashboard
            td = TerminalDashboard(pnl_engine, trade_logger)
            threading.Thread(target=td.run, daemon=True, name="terminal-dash").start()
        except Exception:
            log.warning("Terminal dashboard unavailable", exc_info=True)

        try:
            from trading_system.dashboard.web_dashboard import WebDashboard
            wd = WebDashboard(pnl_engine, trade_logger, evaluator)
            threading.Thread(target=wd.run, daemon=True, name="web-dash").start()
            log.info("Web dashboard: http://localhost:5050")
        except Exception:
            log.warning("Web dashboard unavailable", exc_info=True)

    # ── Data collection ─────────────────────────────────────────────────

    collection_started = False
    if is_market_hours():
        collector.start_collection()
        collection_started = True

    # ── State ───────────────────────────────────────────────────────────

    routing = None
    day_class = None
    vix_history: list[tuple[float, float]] = []
    cached_max_pain: float | None = None

    log.info("Entering main loop (trade window %s–%s IST)...", settings.TRADE_START, settings.TRADE_END)

    # ── Main loop ───────────────────────────────────────────────────────

    try:
        while True:
            now = datetime.now()
            now_t = now.time()

            # Start data collection when market opens
            if not collection_started and is_market_hours():
                collector.start_collection()
                collection_started = True
                log.info("Data collection started")

            # ── Hard close at 14:15 ────────────────────────────────────
            if now_t >= dtime(14, 15):
                for key, s in strats.items():
                    if s.is_active():
                        result = s.force_exit()
                        if result and pnl_engine:
                            pnl_engine.record_trade(key, result.get("pnl", 0), result)
                            risk.update_pnl(result.get("pnl", 0))

                daily = pnl_engine.realised_pnl if pnl_engine else 0
                log.info("Session closed. Daily P&L: ₹%s", f"{daily:,.0f}")

                classifier.reset()
                target.reset()
                risk.reset_daily()
                signals.reset()
                md.reset_daily()
                routing, day_class = None, None
                vix_history.clear()
                cached_max_pain = None

                if collector:
                    collector.stop_collection()
                    collection_started = False

                if is_market_closed_ist():
                    log.info("Market closed. Shutting down.")
                    break

                _time.sleep(300)
                continue

            # Before trade window
            if now_t < dtime(10, 0):
                _time.sleep(30)
                continue

            # ── VIX tracking (for Strategy D stability check) ──────────
            vix = regime.get_vix()
            if vix > 0:
                vix_history.append((_time.monotonic(), vix))
                # Keep last ~60 minutes of readings
                cutoff = _time.monotonic() - 3600
                vix_history = [(t, v) for t, v in vix_history if t >= cutoff]

            # ── Day classification at 10:30 (once) ─────────────────────
            if now_t >= dtime(10, 30) and day_class is None:
                ohlcv = md.get_ohlcv_df()
                signals.compute_vwap_value(ohlcv)  # warm up
                day_class = classifier.classify()
                routing = regime.get_routing(day_class.day_type)
                target.set(routing["target"])
                risk.update_loss_limit(regime.daily_loss_limit())

                # Compute max pain once
                spot = md.get_ltp("NSE|Nifty 50")
                expiry = md.get_nearest_expiry()
                chain = get_option_chain_data(api, sm, spot, expiry)
                if chain is not None and not chain.empty:
                    cached_max_pain = signals.compute_max_pain(chain)

                trade_logger.log_signal(
                    f"DAY CLASSIFIED: {day_class.day_type} ({day_class.confidence}) | "
                    f"ROUTE: {routing['primary']} | VIX={vix:.1f} [{regime.get_regime()}]"
                )
                log.info(
                    "Day=%s (%s) Regime=%s Route=%s Target=₹%s",
                    day_class.day_type, day_class.confidence,
                    regime.get_regime(), routing["primary"],
                    f"{routing['target']:,.0f}",
                )

            if day_class is None:
                _time.sleep(30)
                continue

            # ── Risk gate ──────────────────────────────────────────────
            if not risk.can_trade():
                log.info("Trading halted (risk limit). Monitoring existing positions...")
                for s in strats.values():
                    if s.is_active():
                        s.monitor()
                _time.sleep(60)
                continue

            # ── Daily target gate ──────────────────────────────────────
            if pnl_engine and target.is_hit(pnl_engine.realised_pnl):
                for s in strats.values():
                    if s.is_active():
                        s.monitor()
                _time.sleep(60)
                continue

            # ── Signals ────────────────────────────────────────────────
            spot = md.get_ltp("NSE|Nifty 50")
            ohlcv = md.get_ohlcv_df()
            close_series = md.get_close_series()
            expiry = md.get_nearest_expiry()
            chain = get_option_chain_data(api, sm, spot, expiry) if spot > 0 else None

            sig = signals.get_signals(
                ohlcv_df=ohlcv,
                close_series=close_series,
                chain_data=chain,
                spot=spot,
                cached_max_pain=cached_max_pain,
            )

            trade_logger.log_signal(
                regime=regime.get_regime(),
                day_type=day_class.day_type,
                day_confidence=day_class.confidence,
                routing=routing,
                signals=sig,
                action="MONITORING" if any(s.is_active() for s in strats.values()) else "SCANNING",
            )

            # ── Monitor active positions ───────────────────────────────
            for key, s in strats.items():
                if not s.is_active():
                    continue
                if key == "E":
                    result = s.monitor(current_day_type=day_class.day_type)
                else:
                    result = s.monitor()
                if result:
                    pnl_val = result.get("pnl", 0)
                    if pnl_engine:
                        pnl_engine.record_trade(key, pnl_val, result)
                    risk.update_pnl(pnl_val)
                    log.info("Trade closed: %s [%s] pnl=₹%s", key, result.get("reason", ""), f"{pnl_val:,.0f}")

            # ── Entry logic (only if nothing active) ───────────────────
            if not any(s.is_active() for s in strats.values()) and routing:
                now_str = now.strftime("%H:%M:%S")
                size_mult = regime.size_multiplier()

                for key in routing["primary"] + routing.get("secondary", []):
                    if key in routing.get("forbidden", []):
                        continue
                    s = strats[key]
                    lots = risk.allowed_lots(key, size_mult)

                    entered = False
                    if key == "A" and s.should_enter(regime.get_regime(), sig.consensus, now_t):
                        entered = s.enter(spot, lots, expiry, now_str) is not None
                    elif key == "B" and s.should_enter(regime.get_regime(), sig.consensus, sig.confidence, now_t):
                        direction = sig.consensus  # BULL or BEAR
                        entered = s.enter(direction, spot, lots, expiry, now_str) is not None
                    elif key == "C":
                        active_map = {k: strats[k].is_active() for k in strats}
                        if s.should_enter(regime.get_regime(), sig.confidence, active_map):
                            fut_sym = f"NFO|NIFTY{(expiry.strftime('%y%b') if hasattr(expiry, 'strftime') else str(expiry)[:5]).upper()}FUT"
                            entered = s.enter(sig.consensus, fut_sym, now_str) is not None
                    elif key == "D" and s.should_enter(vix, day_class.day_type, vix_history, now_t):
                        entered = s.enter(spot, vix, lots, expiry, now_str) is not None
                    elif key == "E" and s.should_enter(vix, day_class.day_type, day_class.confidence, now_t):
                        chain_list = []
                        if chain is not None and not chain.empty:
                            chain_list = chain.to_dict("records")
                            for r in chain_list:
                                r.setdefault("type", r.get("option_type", ""))
                                r.setdefault("delta", 0.0)
                        direction = "UP" if day_class.day_type == "TRENDING_UP" else "DOWN"
                        strike_info = StrategyE.find_deep_itm_strike(spot, direction, chain_list)
                        if strike_info:
                            entered = s.enter(direction, strike_info, expiry, now_str) is not None
                        elif strats["D"].should_enter(vix, day_class.day_type, vix_history, now_t):
                            strats["D"].enter(spot, vix, lots, expiry, now_str)
                            entered = True

                    if entered:
                        log.info("Entered strategy %s", key)
                        break

            _time.sleep(settings.SIGNAL_RECHECK_SEC)

    except KeyboardInterrupt:
        log.info("Interrupted by user")
        for key, s in strats.items():
            if s.is_active():
                result = s.force_exit()
                if result:
                    log.info("Force-exited %s: pnl=₹%s", key, f"{result.get('pnl', 0):,.0f}")
        if collector:
            collector.stop_collection()


if __name__ == "__main__":
    run()
