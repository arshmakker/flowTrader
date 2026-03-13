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
from trading_system.core import position_persistence

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
    from logging.handlers import RotatingFileHandler

    os.makedirs(settings.LOG_DIR, exist_ok=True)
    log_path = os.path.join(
        settings.LOG_DIR,
        f"trading_system_{datetime.now().strftime('%Y%m%d')}.log",
    )
    formatter = logging.Formatter(settings.LOG_FORMAT)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    fh = RotatingFileHandler(
        log_path,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
    )
    fh.setFormatter(formatter)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

    logging.info("Log file: %s (rotation: %d MB x %d backups)",
                 os.path.abspath(log_path),
                 settings.LOG_MAX_BYTES // (1024 * 1024),
                 settings.LOG_BACKUP_COUNT)


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
        logging.error("Shoonya login response: %s", ok)
        raise ValueError("Shoonya login failed — check credentials/2FA and try again")
    if isinstance(ok, dict) and ok.get("stat") != "Ok":
        emsg = ok.get("emsg", ok.get("stat", "unknown error"))
        logging.error("Shoonya login rejected: %s", emsg)
        raise ValueError(f"Shoonya login rejected: {emsg}")
    logging.info(Fore.GREEN + "Logged in successfully")
    return api


def print_session_summary(log, pnl_engine, risk, strats, label="SESSION SUMMARY") -> None:
    """Print a comprehensive P&L summary to the log."""
    if pnl_engine is None:
        return
    summary = pnl_engine.get_summary()
    daily = summary.get("daily", {})
    active = [k for k, s in strats.items() if s.is_active()]

    pf = summary.get("profit_factor", 0)
    pf_str = "∞" if pf == "inf" else f"{pf:.2f}"

    lines = [
        f"═══ {label} ═══",
        f"  Realised P&L : ₹{summary['realised_pnl']:>10,.2f}  (today: ₹{daily.get('realised_pnl', 0):>10,.2f})",
        f"  Unrealised   : ₹{summary['unrealised_pnl']:>10,.2f}",
        f"  Total P&L    : ₹{summary['total_pnl']:>10,.2f}",
        f"  Trades       : {summary['total_trades']}  (today: {daily.get('trades', 0)})  "
        f"WR: {summary['win_rate_pct']:.0f}%  (today: {daily.get('win_rate_pct', 0):.0f}%)",
        f"  Max Drawdown : ₹{summary.get('max_drawdown', 0):>10,.2f}  (today: ₹{daily.get('max_drawdown', 0):>10,.2f})",
        f"  Profit Factor: {pf_str}   Avg Win: ₹{summary.get('avg_win', 0):,.0f}  Avg Loss: ₹{summary.get('avg_loss', 0):,.0f}",
    ]

    for k, ss in summary.get("strategy_stats", {}).items():
        if ss["trades"] > 0:
            day_ss = daily.get("strategy_stats", {}).get(k, {})
            day_t = day_ss.get("trades", 0)
            day_pnl = day_ss.get("total_pnl", 0)
            lines.append(
                f"  Strategy {k}   : {ss['trades']} trades, "
                f"₹{ss['total_pnl']:,.0f}, WR {ss['win_rate']:.0f}%"
                f"  (today: {day_t}t, ₹{day_pnl:,.0f})"
            )

    if active:
        lines.append(f"  Active now   : {', '.join(active)}")

    unmarked = summary.get("unmarked_positions", [])
    if unmarked:
        lines.append(f"  ⚠ UNMARKED   : {', '.join(unmarked)} (LTP=0, excluded from unrealised P&L)")

    lines.append(f"  Daily risk   : ₹{risk.daily_pnl:,.0f} (limit ₹{risk._daily_loss_limit:,.0f})")
    lines.append("═" * len(lines[0]))

    for line in lines:
        log.info(line)


def _enrich_result(result: dict, now: datetime, vix: float, regime, day_class, sig, target) -> dict:
    """Add context fields to a trade result so TradeLogger CSV has full data."""
    result.setdefault("time_exit", now.strftime("%H:%M:%S"))
    result.setdefault("vix_entry", round(vix, 2) if vix else 0)
    result.setdefault("regime_entry", regime.get_regime() if regime else "")
    result.setdefault("day_type", day_class.day_type if day_class else "")
    if sig:
        result.setdefault("vwap_bias", sig.vwap_bias)
        result.setdefault("rsi_signal", sig.rsi_signal)
        result.setdefault("pcr_signal", sig.pcr_signal)
        result.setdefault("max_pain", sig.max_pain)
        result.setdefault("signal_confidence", sig.confidence)
    result.setdefault("daily_target", target.target if target else 0)
    result.setdefault("target_hit_today", target._hit if target else False)
    result.setdefault("instrument", "NIFTY")
    return result


def _update_lot_sizes(sm: SymbolManager, log: logging.Logger) -> None:
    """Read NIFTY / BANKNIFTY lot sizes from NFO.csv and patch settings at startup."""
    nfo = sm.nse_fo
    if nfo is None:
        log.warning("NFO data not loaded — using fallback lot sizes")
        return

    for index, attr in [("NIFTY", "NIFTY_LOT_SIZE"), ("BANKNIFTY", "BANKNIFTY_LOT_SIZE")]:
        rows = nfo[(nfo["symbol"] == index) & (nfo["instrument"].isin(["OPTIDX", "FUTIDX"]))]
        if rows.empty:
            log.warning("No NFO rows for %s — keeping fallback %s=%d", index, attr, getattr(settings, attr))
            continue
        lot = int(rows.iloc[0]["lotsize"])
        old = getattr(settings, attr)
        setattr(settings, attr, lot)
        if lot != old:
            log.info("Lot size %s updated: %d → %d (from NFO.csv)", index, old, lot)
        else:
            log.info("Lot size %s confirmed: %d (from NFO.csv)", index, lot)


def _parse_time(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _persist_session_state(
    strats,
    pos_mgr,
    pnl_engine,
    risk,
    target,
    *,
    session_status: str,
    trading_date,
    shutdown_reason: str = "",
    flat_verified: bool = False,
) -> None:
    position_persistence.save(
        strats,
        pos_mgr,
        pnl_engine,
        risk_manager=risk,
        daily_target=target,
        session_status=session_status,
        trading_date=trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date),
        shutdown_reason=shutdown_reason,
        flat_verified_at=datetime.now().isoformat() if flat_verified else None,
    )


def _clear_if_flat(log, strats, pos_mgr) -> bool:
    if position_persistence.is_flat(strats, pos_mgr):
        position_persistence.clear()
        return True
    active = [k for k, s in strats.items() if s.is_active()]
    tracker_positions = len(getattr(pos_mgr, "_positions", {}))
    log.warning(
        "Session not flat after shutdown; keeping persisted state (active=%s tracker_positions=%d)",
        ",".join(active) if active else "none",
        tracker_positions,
    )
    return False


def run() -> None:
    setup_logging()
    log = logging.getLogger("main")

    mode_str = "PAPER" if settings.PAPER_TRADE_MODE else "LIVE"
    log.info("=== RegimeTrader — %s MODE ===", mode_str)

    if not settings.PAPER_TRADE_MODE:
        log.warning("[LIVE MODE] Real orders will be placed.")
        if sys.stdin.isatty():
            confirm = input(Fore.RED + "[LIVE MODE] Type YES to confirm: ").strip()
        else:
            confirm = os.environ.get("LIVE_CONFIRM", "")
        if confirm != "YES":
            log.info("Live mode not confirmed. Exiting.")
            sys.exit(0)
        log.warning("Live mode CONFIRMED by operator.")

    api = initialize_api()

    sm = SymbolManager(api)
    try:
        sm.load_symbol_files()
    except FileNotFoundError:
        log.warning("Symbol files missing — downloading...")
        sm.download_master_files()
        sm.load_symbol_files()

    # Pick lot sizes from NFO.csv so they stay current with exchange changes
    _update_lot_sizes(sm, log)

    collector = DataCollector(api, sm)
    md = MarketData(api, sm)

    # ── Module initialisation ───────────────────────────────────────────

    if settings.PAPER_TRADE_MODE:
        pos_mgr = PosMgr()
        order_mgr = OrderMgr(md, pos_mgr)
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

    # ── Restore persisted positions + P&L (survive restarts) ────────────
    restore_meta = position_persistence.load(strats, pos_mgr, pnl_engine, risk, target)
    restored = restore_meta["restored_strategies"]
    if restore_meta["session_status"] and restore_meta["session_status"] != position_persistence.SESSION_FLAT:
        reason = "unfinished session"
        if restore_meta["session_status"] == position_persistence.SESSION_CLOSING:
            reason = "post-EOD unfinished positions"
        if restore_meta["trading_date"] and restore_meta["trading_date"] != datetime.now().date().isoformat():
            log.warning(
                "Restoring stale session from %s (%s)",
                restore_meta["trading_date"],
                reason,
            )
        log.info(
            Fore.YELLOW + "Restoring %s: %d strategy position(s), %d tracker position(s)",
            reason,
            restored,
            restore_meta["tracker_positions"],
        )
        if restore_meta.get("is_stale_trading_day"):
            log.warning(
                "Restored cross-day session state from %s; daily P&L/risk/target counters were reset for %s",
                restore_meta["trading_date"],
                datetime.now().date().isoformat(),
            )
    else:
        log.info("No unfinished persisted session found; starting fresh")

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
    session_closed = False
    last_pnl_log: float = 0.0
    _last_waiting_classify_log: float = 0.0
    _last_trading_date = datetime.now().date()
    vix = 0.0

    log.info("Entering main loop (trade window %s–%s IST)...", settings.TRADE_START, settings.TRADE_END)

    # ── Main loop ───────────────────────────────────────────────────────
    try:
        while True:
            now = datetime.now()
            now_t = now.time()

            if now.date() != _last_trading_date:
                log.info("New trading day detected (%s → %s). Resetting daily counters.",
                         _last_trading_date, now.date())
                sm.refresh_for_current_day()
                collector.refresh_for_current_day()
                risk.reset_daily()
                if pnl_engine:
                    pnl_engine.reset_daily()
                day_class = None
                routing = None
                cached_max_pain = None
                session_closed = False
                vix_history.clear()
                _last_trading_date = now.date()
                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_ACTIVE,
                    trading_date=_last_trading_date,
                    shutdown_reason="new_trading_day_reset",
                )

            # Start data collection when market opens
            if not collection_started and is_market_hours():
                collector.start_collection()
                collection_started = True
                log.info("Data collection started")

            # ── Hard close at TRADE_END (run once) ───────────────────
            if now_t >= _parse_time(settings.TRADE_END) and not session_closed:
                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_CLOSING,
                    trading_date=now.date(),
                    shutdown_reason="trade_end_hard_close_started",
                )
                for key, s in strats.items():
                    if s.is_active():
                        try:
                            result = s.force_exit()
                        except Exception:
                            log.exception("force_exit FAILED for strategy %s — position may be orphaned!", key)
                            result = None
                        if result:
                            _enrich_result(result, now, vix, regime, day_class, None, target)
                            if pnl_engine:
                                pnl_engine.record_trade(key, result.get("pnl", 0), result)
                            risk.update_pnl(result.get("pnl", 0))
                            _persist_session_state(
                                strats,
                                pos_mgr,
                                pnl_engine,
                                risk,
                                target,
                                session_status=position_persistence.SESSION_CLOSING,
                                trading_date=now.date(),
                                shutdown_reason=f"hard_close_progress_{key}",
                            )

                print_session_summary(log, pnl_engine, risk, strats, label="END OF DAY")

                if pnl_engine:
                    pnl_engine.write_snapshot()

                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_FLAT
                    if position_persistence.is_flat(strats, pos_mgr)
                    else position_persistence.SESSION_CLOSING,
                    trading_date=now.date(),
                    shutdown_reason="trade_end_hard_close_complete",
                    flat_verified=position_persistence.is_flat(strats, pos_mgr),
                )
                _clear_if_flat(log, strats, pos_mgr)
                session_closed = True

                if collector:
                    collector.stop_collection()
                collection_started = False

            if session_closed:
                if is_market_closed_ist():
                    log.info("Market closed. Shutting down.")
                    break
                _time.sleep(settings.MONITORING_SLEEP_SEC)
                continue

            # Before trade window
            if now_t < _parse_time(settings.TRADE_START):
                _time.sleep(settings.PRE_MARKET_SLEEP_SEC)
                continue

            # ── VIX tracking (for Strategy D stability check) ────────
            vix = regime.get_vix()
            if vix > 0:
                vix_history.append((_time.monotonic(), vix))
                cutoff = _time.monotonic() - settings.VIX_HISTORY_WINDOW_SEC
                vix_history = [(t, v) for t, v in vix_history if t >= cutoff]

            # ── Day classification at CLASSIFY_TIME (once) ───────────
            if now_t >= _parse_time(settings.CLASSIFY_TIME) and day_class is None:
                log.info("Running day classification (OHLCV + classify)...")
                try:
                    ohlcv = md.get_ohlcv_df()
                    signals.compute_vwap_value(ohlcv)
                    day_class = classifier.classify()
                    routing = regime.get_routing(day_class.day_type)
                    target.set(routing["target"])
                    risk.update_loss_limit(regime.daily_loss_limit())

                    spot = md.get_ltp(settings.NIFTY_SPOT_KEY)
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
                except Exception:
                    log.exception("Day classification failed — using RANGING/LOW for today")
                    from trading_system.core.day_classifier import DayClassification
                    day_class = DayClassification(
                        day_type="RANGING", confidence="LOW",
                        open_price=0.0, current_price=0.0, move_pct=0.0, vwap_distance_pct=0.0,
                        classified_at=now.strftime("%H:%M:%S"),
                    )
                    routing = regime.get_routing(day_class.day_type)
                    target.set(routing["target"])
                    risk.update_loss_limit(regime.daily_loss_limit())

            if day_class is None:
                if now_t >= _parse_time(settings.TRADE_START):
                    mono = _time.monotonic()
                    if mono - _last_waiting_classify_log >= 300.0:  # at most every 5 min
                        log.info(
                            "Waiting for day classification at %s IST (current %s) — no trades until then",
                            settings.CLASSIFY_TIME, now.strftime("%H:%M:%S"),
                        )
                        _last_waiting_classify_log = mono
                _time.sleep(settings.PRE_MARKET_SLEEP_SEC)
                continue

            # ── Risk gate ────────────────────────────────────────────
            if not risk.can_trade():
                log.info("Trading halted (risk limit). Monitoring existing positions...")
                for key, s in strats.items():
                    if not s.is_active():
                        continue
                    try:
                        result = s.monitor(current_day_type=day_class.day_type) if key == "E" else s.monitor()
                    except Exception:
                        log.exception("monitor() FAILED for strategy %s during risk-halt", key)
                        result = None
                    if result:
                        _enrich_result(result, now, vix, regime, day_class, None, target)
                        pnl_val = result.get("pnl", 0)
                        if pnl_engine:
                            pnl_engine.record_trade(key, pnl_val, result)
                        risk.update_pnl(pnl_val)
                        log.info("Trade closed: %s [%s] pnl=₹%s", key, result.get("reason", ""), f"{pnl_val:,.0f}")
                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_ACTIVE,
                    trading_date=now.date(),
                    shutdown_reason="risk_halt_monitoring",
                )
                _time.sleep(settings.MONITORING_SLEEP_SEC)
                continue

            # ── Daily target gate ────────────────────────────────────
            if pnl_engine and target.is_hit(pnl_engine.daily_realised_pnl):
                for key, s in strats.items():
                    if not s.is_active():
                        continue
                    try:
                        result = s.monitor(current_day_type=day_class.day_type) if key == "E" else s.monitor()
                    except Exception:
                        log.exception("monitor() FAILED for strategy %s during target-gate", key)
                        result = None
                    if result:
                        _enrich_result(result, now, vix, regime, day_class, None, target)
                        pnl_val = result.get("pnl", 0)
                        if pnl_engine:
                            pnl_engine.record_trade(key, pnl_val, result)
                        risk.update_pnl(pnl_val)
                        log.info("Trade closed: %s [%s] pnl=₹%s", key, result.get("reason", ""), f"{pnl_val:,.0f}")
                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_ACTIVE,
                    trading_date=now.date(),
                    shutdown_reason="daily_target_monitoring",
                )
                _time.sleep(settings.MONITORING_SLEEP_SEC)
                continue

            # ── Signals ──────────────────────────────────────────────
            spot = md.get_ltp(settings.NIFTY_SPOT_KEY)
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

            # ── Monitor active positions ─────────────────────────────
            active_keys = [k for k, s in strats.items() if s.is_active()]
            if active_keys:
                unrl = pnl_engine.unrealised_pnl if pnl_engine else 0
                log.info(
                    "Monitoring %s | spot=%.0f VIX=%.1f | unrealised=₹%s | signals: %s/%s conf=%d",
                    ",".join(active_keys), spot, vix,
                    f"{unrl:,.0f}", sig.consensus, sig.rsi_signal, sig.confidence,
                )

            for key, s in strats.items():
                if not s.is_active():
                    continue
                try:
                    if key == "E":
                        result = s.monitor(current_day_type=day_class.day_type)
                    else:
                        result = s.monitor()
                except Exception:
                    log.exception("monitor() FAILED for strategy %s", key)
                    result = None
                if result:
                    _enrich_result(result, now, vix, regime, day_class, sig, target)
                    pnl_val = result.get("pnl", 0)
                    if pnl_engine:
                        pnl_engine.record_trade(key, pnl_val, result)
                    risk.update_pnl(pnl_val)
                    log.info("Trade closed: %s [%s] pnl=₹%s", key, result.get("reason", ""), f"{pnl_val:,.0f}")

            # ── Entry logic (only if nothing active) ─────────────────
            if not any(s.is_active() for s in strats.values()) and routing:
                now_str = now.strftime("%H:%M:%S")
                size_mult = regime.size_multiplier()

                for key in routing["primary"] + routing.get("secondary", []):
                    if key in routing.get("forbidden", []):
                        continue
                    s = strats[key]
                    lots = risk.allowed_lots(key, size_mult)

                    entered = False
                    entered_key = key
                    if key == "A" and s.should_enter(regime.get_regime(), sig.consensus, now_t):
                        entered = s.enter(spot, lots, expiry, now_str) is not None
                    elif key == "B" and s.should_enter(regime.get_regime(), sig.consensus, sig.confidence, now_t):
                        direction = sig.consensus
                        entered = s.enter(direction, spot, lots, expiry, now_str) is not None
                    elif key == "C":
                        active_map = {k: strats[k].is_active() for k in strats}
                        if s.should_enter(regime.get_regime(), sig.confidence, active_map):
                            if hasattr(expiry, "strftime"):
                                _exp = expiry.strftime("%d%b%y").upper()
                            else:
                                from datetime import datetime as _dt
                                try:
                                    _exp = _dt.strptime(str(expiry)[:11].strip(), "%d-%b-%Y").strftime("%d%b%y").upper()
                                except (ValueError, TypeError):
                                    _exp = str(expiry).replace("-", "").upper()
                            fut_sym = f"NFO|NIFTY{_exp}F"
                            entered = s.enter(sig.consensus, fut_sym, now_str) is not None
                    elif key == "D" and s.should_enter(vix, day_class.day_type, vix_history, now_t):
                        entered = s.enter(spot, vix, lots, expiry, now_str) is not None
                    elif key == "E" and s.should_enter(vix, day_class.day_type, day_class.confidence, now_t):
                        chain_list = []
                        if chain is not None and not chain.empty:
                            chain_list = chain.to_dict("records")
                            for r in chain_list:
                                r.setdefault("type", r.get("option_type", ""))
                                _strike = r.get("strike", 0)
                                _ot = r.get("type", "")
                                if _ot == "CE":
                                    _m = (spot - _strike) / spot if spot > 0 else 0
                                elif _ot == "PE":
                                    _m = (_strike - spot) / spot if spot > 0 else 0
                                else:
                                    _m = 0
                                r["delta"] = max(settings.SE_DELTA_CLAMP_LOW,
                                                 min(settings.SE_DELTA_CLAMP_HIGH,
                                                     0.5 + _m * settings.SE_DELTA_SCALE))
                        direction = "UP" if day_class.day_type == "TRENDING_UP" else "DOWN"
                        strike_info = StrategyE.find_deep_itm_strike(spot, direction, chain_list)
                        if strike_info:
                            entered = s.enter(direction, strike_info, expiry, now_str) is not None
                        elif strats["D"].should_enter(vix, day_class.day_type, vix_history, now_t):
                            entered = strats["D"].enter(spot, vix, lots, expiry, now_str) is not None
                            entered_key = "D"

                    if entered:
                        log.info("Entered strategy %s", entered_key)
                        _persist_session_state(
                            strats,
                            pos_mgr,
                            pnl_engine,
                            risk,
                            target,
                            session_status=position_persistence.SESSION_ACTIVE,
                            trading_date=now.date(),
                            shutdown_reason=f"entered_strategy_{entered_key}",
                        )
                        break

            # Persist positions + P&L after every cycle
            _persist_session_state(
                strats,
                pos_mgr,
                pnl_engine,
                risk,
                target,
                session_status=position_persistence.SESSION_ACTIVE,
                trading_date=now.date(),
                shutdown_reason="loop_checkpoint",
            )

            # Write P&L snapshot to disk
            if pnl_engine:
                pnl_engine.write_snapshot()

            # Periodic P&L summary to log
            mono_now = _time.monotonic()
            if pnl_engine and (mono_now - last_pnl_log) >= settings.PNL_LOG_INTERVAL_SEC:
                print_session_summary(log, pnl_engine, risk, strats, label="P&L CHECK")
                last_pnl_log = mono_now

            _time.sleep(settings.SIGNAL_RECHECK_SEC)

    except KeyboardInterrupt:
        active = [k for k, s in strats.items() if s.is_active()]

        print_session_summary(log, pnl_engine, risk, strats, label="INTERRUPTED")

        if active:
            log.info("Active positions: %s", ",".join(active))
            _persist_session_state(
                strats,
                pos_mgr,
                pnl_engine,
                risk,
                target,
                session_status=position_persistence.SESSION_CLOSING,
                trading_date=datetime.now().date(),
                shutdown_reason="keyboard_interrupt_active_positions",
            )
            log.info("Positions + P&L saved to disk. They will be restored on next start.")

            if pnl_engine:
                pnl_engine.write_snapshot()

            force_close = False
            try:
                if sys.stdin.isatty():
                    ans = input(Fore.YELLOW + "Force-close all positions now? [y/N]: ").strip().lower()
                    force_close = ans in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                pass

            if force_close:
                for key, s in strats.items():
                    if s.is_active():
                        try:
                            result = s.force_exit()
                        except Exception:
                            log.exception("force_exit FAILED for strategy %s during Ctrl+C close", key)
                            result = None
                        if result:
                            _enrich_result(result, datetime.now(), vix, regime, day_class, None, target)
                            if pnl_engine:
                                pnl_engine.record_trade(key, result.get("pnl", 0), result)
                            log.info("Force-exited %s: pnl=₹%s", key, f"{result.get('pnl', 0):,.0f}")
                print_session_summary(log, pnl_engine, risk, strats, label="AFTER FORCE CLOSE")
                _persist_session_state(
                    strats,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    target,
                    session_status=position_persistence.SESSION_FLAT
                    if position_persistence.is_flat(strats, pos_mgr)
                    else position_persistence.SESSION_CLOSING,
                    trading_date=datetime.now().date(),
                    shutdown_reason="keyboard_interrupt_force_close_complete",
                    flat_verified=position_persistence.is_flat(strats, pos_mgr),
                )
                _clear_if_flat(log, strats, pos_mgr)
            else:
                log.info("Positions preserved. Restart to resume monitoring.")
        else:
            log.info("No active positions")
            flat = position_persistence.is_flat(strats, pos_mgr)
            _persist_session_state(
                strats,
                pos_mgr,
                pnl_engine,
                risk,
                target,
                session_status=position_persistence.SESSION_FLAT if flat else position_persistence.SESSION_CLOSING,
                trading_date=datetime.now().date(),
                shutdown_reason="keyboard_interrupt_no_active_positions",
                flat_verified=flat,
            )
            if flat:
                _clear_if_flat(log, strats, pos_mgr)
            if pnl_engine:
                pnl_engine.write_snapshot()

        if collector:
            collector.stop_collection()


if __name__ == "__main__":
    run()
