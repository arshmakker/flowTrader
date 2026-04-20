"""
Main Orchestrator — Iron Condor Trading System (agents.md).

Wires all modules together for high-frequency Nifty/BankNifty IC trading.
"""

import os
import sys
import logging
import threading
import time as _time
import re
import subprocess
import urllib.parse
import yaml
from datetime import datetime, time as dtime

from api_helper import ShoonyaApiPy
from symbol_manager import SymbolManager
from data_collector import DataCollector
from strategy_runner import is_market_hours, is_market_closed_ist, is_trading_day_ist

from trading_system.config import settings
from trading_system.core.regime_filter import RegimeFilter
from trading_system.core.day_classifier import DayClassifier
from trading_system.core.signal_engine import SignalEngine
from trading_system.core.iron_condor import IronCondorStrategy
from trading_system.core.risk_manager import RiskManager
from trading_system.core.expiry_manager import ExpiryManager
from trading_system.core.sr_manager import SRManager
from trading_system.core.trade_logger import TradeLogger
from trading_system.core import position_persistence
from trading_system.existing.market_data import MarketData

if settings.PAPER_TRADE_MODE:
    from trading_system.paper.paper_order_manager import PaperOrderManager as OrderMgr
    from trading_system.paper.paper_position_tracker import PaperPositionTracker as PosMgr
    from trading_system.paper.paper_pnl_engine import PaperPnLEngine as PnLEngine
else:
    # Live mode not yet fully integrated for this specific strategist
    OrderMgr = None
    PosMgr = None
    PnLEngine = None

DEFAULT_AUTH_CODE_SCRIPT = "/Users/arshdeep/git/Shoonya_oAuth_API.py/tests/getAuthCode.py"


def _force_exit_all(strats, pnl_engine, risk):
    """Flatten all active strategies and route each exit through the P&L engine.
    Used by the EOD pre-holiday flatten and the combined-hard-stop paths.
    Axiom 5: realised P&L has exactly one home — `pnl_engine.record_trade`.
    The `risk` parameter is kept in the signature for future risk decisions
    that may hook off exits."""
    for s in strats:
        if s.is_active():
            result = s.force_exit()
            if result:
                pnl_engine.record_trade(s.instrument, result['pnl'], result)


def _drain_rollback_failures(strats, risk):
    """BUG-05: after each entry attempt, check whether rollback left stuck legs.
    Escalate to a hard halt through the risk manager and clear the flag."""
    for s in strats:
        stuck = getattr(s, "_last_rollback_stuck_legs", None)
        if stuck:
            risk.escalate_rollback_failure(s.instrument, stuck)
            s._last_rollback_stuck_legs = []


def _halt_on_exception(exc, risk, log):
    """BUG-19 / Axiom 3: convert an unhandled main-loop exception into a
    controlled halt rather than a process crash. Caller is responsible for
    persisting state and continuing the loop."""
    risk.halted = True
    log.critical(
        "Unhandled exception in main loop — halting trading: %s",
        exc,
        exc_info=True,
    )


def _find_expiring_today(strats, today_iso):
    """BUG-18 / Axiom 2: return active strategies whose IC expires today. The
    EOD branch uses this to force-flatten expiring positions regardless of
    whether tomorrow is a trading day."""
    out = []
    for s in strats:
        if not s.is_active():
            continue
        pos = getattr(s, "_position", None)
        if pos is not None and getattr(pos, "expiry_date", "") == today_iso:
            out.append(s)
    return out

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

def _load_creds(path="cred.yml"):
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def _save_creds(creds, path="cred.yml"):
    with open(path, "w") as f:
        yaml.safe_dump(creds, f, sort_keys=False)

def _mask_secret(value):
    s = str(value or "")
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"

def _is_oauth_configured(creds):
    required = ("oauth_url", "client_id", "Secret_Code", "UID")
    return all(str(creds.get(k, "")).strip() for k in required)

def _extract_auth_code(text):
    raw = str(text or "")
    if not raw:
        return ""
    # Common script output format: "Auth Code: <value>"
    m = re.search(r"Auth\s*Code\s*:\s*([A-Za-z0-9._-]+)", raw, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Fallback: parse redirect URL containing ?code=<value>
    m = re.search(r"[?&]code=([^&\\s]+)", raw)
    if m:
        return urllib.parse.unquote(m.group(1).strip())
    # Last resort: treat single-token output as the code.
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) == 1 and re.fullmatch(r"[A-Za-z0-9._-]{8,}", lines[0]):
        return lines[0]
    return ""

def _resolve_auth_code_cmd(creds):
    cmd = os.environ.get("SHOONYA_AUTH_CODE_CMD", "").strip() or str(creds.get("auth_code_cmd", "")).strip()
    if cmd:
        return cmd
    if os.path.exists(DEFAULT_AUTH_CODE_SCRIPT):
        return f'python3 "{DEFAULT_AUTH_CODE_SCRIPT}"'
    return ""

def _fetch_auth_code_from_command(creds, log):
    cmd = _resolve_auth_code_cmd(creds)
    if not cmd:
        return ""
    timeout_raw = os.environ.get("SHOONYA_AUTH_CODE_TIMEOUT", "").strip() or str(creds.get("auth_code_timeout", "180")).strip()
    try:
        timeout = max(30, int(timeout_raw))
    except ValueError:
        timeout = 180

    log.info("Attempting auth code via command: %s", cmd)
    try:
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        log.warning("Auth code command failed to execute: %s", exc)
        return ""

    merged_lines = []
    code = ""
    start_ts = _time.time()
    try:
        while True:
            if process.stdout is None:
                break
            line = process.stdout.readline()
            if line:
                merged_lines.append(line)
                # Stream command progress so it does not look hung.
                line_for_log = line.strip()
                if "Auth Code" in line_for_log:
                    line_for_log = "Auth code captured by external script."
                if line_for_log:
                    log.info("auth_code_cmd: %s", line_for_log)
                code = _extract_auth_code(line)
                if code:
                    process.terminate()
                    break
            elif process.poll() is not None:
                break

            if (_time.time() - start_ts) > timeout:
                process.terminate()
                log.warning("Auth code command timed out after %ss.", timeout)
                break
    finally:
        try:
            remaining, _ = process.communicate(timeout=3)
        except Exception:
            remaining = ""
        if remaining:
            merged_lines.append(remaining)

    merged_output = "".join(merged_lines)
    if not code:
        code = _extract_auth_code(merged_output)
    if code:
        log.info("Auth code captured from command output.")
        return code

    return_code = process.returncode
    if return_code and return_code != 0:
        log.warning("Auth code command exited non-zero (%s).", return_code)
    else:
        log.warning("Auth code command completed but no auth code found in output.")
    return ""

def _initialize_api_legacy(api, creds):
    factor2 = os.environ.get("TWOFA", "").strip()
    if not factor2:
        factor2 = input("Enter 2FA code: ").strip()
    result = api.login(
        userid=creds["user"],
        password=creds["pwd"],
        twoFA=factor2,
        vendor_code=creds["vc"],
        api_secret=creds["apikey"],
        imei=creds["imei"],
    )
    if not result or (isinstance(result, dict) and str(result.get("stat", "")).lower() != "ok"):
        detail = api.get_last_broker_error() or (result.get("emsg") if isinstance(result, dict) else "") or "Unknown login failure"
        raise RuntimeError(f"Shoonya legacy login failed: {detail}")
    return api

def _initialize_api_oauth(api, creds, log):
    uid = str(creds.get("UID", "")).strip()
    client_id = str(creds.get("client_id", "")).strip()
    secret_code = str(creds.get("Secret_Code", "")).strip()
    oauth_url = str(creds.get("oauth_url", "")).strip()
    token_url = (
        os.environ.get("SHOONYA_TOKEN_URL", "").strip()
        or str(creds.get("token_url", "")).strip()
    )
    oauth_api_host = (
        os.environ.get("SHOONYA_OAUTH_API_HOST", "").strip()
        or str(creds.get("oauth_api_host", "")).strip()
        or "https://api.shoonya.com/NorenWClientAPI/"
    )
    oauth_ws_endpoint = (
        os.environ.get("SHOONYA_OAUTH_WS", "").strip()
        or str(creds.get("oauth_ws_endpoint", "")).strip()
        or "wss://api.shoonya.com/NorenWS/"
    )
    account_id = str(creds.get("Account_ID", "")).strip() or uid
    access_token = str(creds.get("Access_token", "")).strip()
    retry_raw = os.environ.get("SHOONYA_OAUTH_REAUTH_ATTEMPTS", "").strip() or str(creds.get("oauth_reauth_attempts", "2")).strip()
    try:
        oauth_reauth_attempts = max(1, int(retry_raw))
    except ValueError:
        oauth_reauth_attempts = 2

    # OAuth SDK methods read class-level service config; point it at API host.
    api.configure_oauth_service_host(oauth_api_host, oauth_ws_endpoint)

    if access_token:
        api.inject_oauth_header(access_token, uid, account_id)
        if api.validate_oauth_session():
            log.info("OAuth login: using cached access token (%s).", _mask_secret(access_token))
            return api
        detail = api.get_last_broker_error()
        if detail:
            log.warning("OAuth login: cached token invalid/expired, broker says: %s", detail)
        else:
            log.warning("OAuth login: cached token invalid/expired, re-auth required.")

    oauth_login_url = api.get_oauth_url(oauth_url, client_id)
    if not oauth_login_url:
        raise RuntimeError("Unable to generate OAuth login URL")

    # Always try the command-based auth-code path on OAuth failures.
    for attempt in range(1, oauth_reauth_attempts + 1):
        auth_code = os.environ.get("SHOONYA_AUTH_CODE", "").strip()
        if not auth_code:
            auth_code = _fetch_auth_code_from_command(creds, log)
        if not auth_code:
            log.warning("OAuth re-auth attempt %s/%s: no auth code captured.", attempt, oauth_reauth_attempts)
            continue

        token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
        if not token_data:
            detail = api.get_last_broker_error() or "Unknown token exchange failure"
            log.warning("OAuth re-auth attempt %s/%s failed at token exchange: %s", attempt, oauth_reauth_attempts, detail)
            continue

        new_access_token, user_id, _refresh_token, new_account_id = token_data
        api.inject_oauth_header(new_access_token, user_id, new_account_id)
        if api.validate_oauth_session():
            creds["Access_token"] = new_access_token
            creds["Account_ID"] = new_account_id
            creds["UID"] = user_id
            _save_creds(creds)
            log.info("OAuth login successful; access token cached to cred.yml (%s).", _mask_secret(new_access_token))
            return api

        detail = api.get_last_broker_error() or "Unknown validation failure"
        log.warning("OAuth re-auth attempt %s/%s failed at session validation: %s", attempt, oauth_reauth_attempts, detail)

    # Final fallback: manual code entry.
    log.info("Open this URL, complete login, then paste the auth code:\n%s", oauth_login_url)
    auth_code = input("Enter Shoonya auth code: ").strip()
    token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
    if not token_data:
        detail = api.get_last_broker_error() or "Unknown token exchange failure"
        raise RuntimeError(f"OAuth token exchange failed: {detail}")
    new_access_token, user_id, _refresh_token, new_account_id = token_data
    api.inject_oauth_header(new_access_token, user_id, new_account_id)
    if not api.validate_oauth_session():
        detail = api.get_last_broker_error() or "Unknown validation failure"
        raise RuntimeError(f"OAuth session validation failed after token exchange: {detail}")
    creds["Access_token"] = new_access_token
    creds["Account_ID"] = new_account_id
    creds["UID"] = user_id
    _save_creds(creds)
    log.info("OAuth login successful (manual fallback); access token cached to cred.yml (%s).", _mask_secret(new_access_token))
    return api

def initialize_api(log) -> ShoonyaApiPy:
    creds = _load_creds()
    api = ShoonyaApiPy()
    if _is_oauth_configured(creds):
        return _initialize_api_oauth(api, creds, log)
    return _initialize_api_legacy(api, creds)

def run():
    setup_logging()
    log = logging.getLogger("main")
    log.info("=== IRON CONDOR SYSTEM STARTING ===")

    api = initialize_api(log)
    sm = SymbolManager(api)
    sm.load_symbol_files()
    
    collector = DataCollector(api, sm)
    md = MarketData(api, sm)
    
    # Core Components
    regime = RegimeFilter(api)
    signals = SignalEngine()
    # BUG-08: one classifier per instrument — BANKNIFTY regime should not be
    # gated on NIFTY's day type.
    classifiers = {
        'NIFTY': DayClassifier(md, signals, settings.NIFTY_SYMBOL, settings.NIFTY_SPOT_KEY),
        'BANKNIFTY': DayClassifier(md, signals, settings.BANKNIFTY_SYMBOL, settings.BANKNIFTY_SPOT_KEY),
    }
    risk = RiskManager()
    expiry_mgr = ExpiryManager(sm)
    sr_mgr = SRManager()
    trade_logger = TradeLogger()
    
    if settings.PAPER_TRADE_MODE:
        pos_mgr = PosMgr()
        order_mgr = OrderMgr(md, pos_mgr)
        pnl_engine = PnLEngine(pos_mgr, md, trade_logger)
    
    # Strategies
    nifty_ic = IronCondorStrategy(order_mgr, md, 'NIFTY')
    banknifty_ic = IronCondorStrategy(order_mgr, md, 'BANKNIFTY')
    strats = [nifty_ic, banknifty_ic]
    strats_map = {'NIFTY': nifty_ic, 'BANKNIFTY': banknifty_ic}

    # Restore any carried-overnight positions + P&L state.
    meta = position_persistence.load(strats_map, pos_mgr, pnl_engine, risk)
    if meta.get("restored_strategies") or meta.get("tracker_positions"):
        log.info(
            "Restored carried state: %d strategies, %d tracker positions (saved_at=%s, trading_date=%s)",
            meta.get("restored_strategies", 0),
            meta.get("tracker_positions", 0),
            meta.get("saved_at"),
            meta.get("trading_date"),
        )

    day_classes = {'NIFTY': None, 'BANKNIFTY': None}
    collection_started = False

    log.info("Entering main loop...")
    
    try:
        while True:
          try:
            now = datetime.now()
            now_t = now.time()

            # 1. Market Hours & Data Collection
            if not collection_started and is_market_hours():
                collector.start_collection()
                collection_started = True

            if is_market_closed_ist():
                log.info("Market closed. Exiting loop.")
                break

            # 2. End-of-day: force-exit expiring positions; also flatten if
            #    next day is not a trading day.
            if now_t >= datetime.strptime(settings.TRADE_END, "%H:%M").time():
                from datetime import timedelta
                today_iso = datetime.now().date().isoformat()
                tomorrow = datetime.now() + timedelta(days=1)
                next_day_is_trading = is_trading_day_ist(tomorrow)
                # BUG-18: close any IC whose own expiry is today, regardless of tomorrow.
                expiring = _find_expiring_today(strats, today_iso)
                if expiring:
                    _force_exit_all(expiring, pnl_engine, risk)
                    log.info("Expiry-day close: force-exited %d expiring position(s).", len(expiring))
                if not next_day_is_trading:
                    _force_exit_all(strats, pnl_engine, risk)
                    log.info("Pre-holiday/weekend close: force-exited all positions.")
                if settings.PAPER_TRADE_MODE and pnl_engine:
                    pnl_engine.write_snapshot()
                if collection_started:
                    collector.stop_collection()
                    collection_started = False
                flat_now = position_persistence.is_flat(strats_map, pos_mgr)
                position_persistence.save(
                    strats_map, pos_mgr, pnl_engine, risk,
                    session_status=(
                        position_persistence.SESSION_FLAT if flat_now
                        else position_persistence.SESSION_ACTIVE
                    ),
                    shutdown_reason="eod",
                    flat_verified_at=datetime.now().isoformat() if flat_now else None,
                )
                log.info("Daily session ended.%s", " All positions closed." if not next_day_is_trading else " Positions carried overnight.")
                break

            # 3. Day Classification (10:30 AM) — per instrument (BUG-08).
            if now_t >= datetime.strptime(settings.CLASSIFY_TIME, "%H:%M").time():
                for inst, clf in classifiers.items():
                    if day_classes[inst] is None:
                        day_classes[inst] = clf.classify()
                        log.info(
                            "Day Classified %s: %s (%s)",
                            inst, day_classes[inst].day_type, day_classes[inst].confidence,
                        )

            # 4. Monitoring & Harvest Cycle (runs pre-classification so carried positions are watched).
            for s in strats:
                if s.is_active():
                    result = s.monitor()
                    if result:
                        pnl_engine.record_trade(s.instrument, result['pnl'], result)

            # 5. Combined Stop Loss
            if risk.check_combined_stop_loss(strats):
                _force_exit_all(strats, pnl_engine, risk)
                log.critical("COMBINED STOP LOSS HIT - Trading Halted.")

            # 6. Entry Logic (requires per-instrument classification).
            if not risk.halted:
                for s in strats:
                    dc = day_classes.get(s.instrument)
                    if dc is None:
                        continue  # pre-classify time for this instrument
                    if s.is_active():
                        continue
                    if not regime.get_regime_gate(dc.day_type):
                        continue
                    # Fetch context for entry
                    spot_key = settings.NIFTY_SPOT_KEY if s.instrument == 'NIFTY' else settings.BANKNIFTY_SPOT_KEY
                    spot = md.get_ltp(spot_key)
                    vix = regime.get_vix()
                    sr_high, sr_low = sr_mgr.get_20day_high_low(s.instrument)
                    expiry = expiry_mgr.get_expiry(s.instrument)

                    if spot > 0 and expiry:
                        s.enter(spot, vix, sr_high, sr_low, sr_mgr, expiry, settings.IC_LOT_SIZE)

            # BUG-05: escalate any stuck-rollback events from this cycle's entries.
            _drain_rollback_failures(strats, risk)

            # Keep live P&L fresh for dashboards (realised + unrealised).
            if settings.PAPER_TRADE_MODE and pnl_engine:
                pnl_engine.write_snapshot()

            # Persist position + P&L state so a crash/restart can resume cleanly.
            position_persistence.save(
                strats_map, pos_mgr, pnl_engine, risk,
                session_status=position_persistence.SESSION_ACTIVE,
            )

            _time.sleep(settings.SIGNAL_RECHECK_SEC)
          except Exception as exc:
            # BUG-19 / Axiom 3: convert unhandled cycle exceptions into a halt.
            _halt_on_exception(exc, risk, log)
            try:
                position_persistence.save(
                    strats_map, pos_mgr, pnl_engine, risk,
                    session_status=position_persistence.SESSION_ACTIVE,
                    shutdown_reason="exception-halt",
                )
            except Exception:
                log.exception("Failed to persist state after halt")
            _time.sleep(settings.SIGNAL_RECHECK_SEC)

    except KeyboardInterrupt:
        log.info("Interrupted by user. Exiting...")
    finally:
        if collection_started:
            collector.stop_collection()
        try:
            flat_now = position_persistence.is_flat(strats_map, pos_mgr)
            position_persistence.save(
                strats_map, pos_mgr, pnl_engine, risk,
                session_status=(
                    position_persistence.SESSION_FLAT if flat_now
                    else position_persistence.SESSION_ACTIVE
                ),
                shutdown_reason="shutdown",
                flat_verified_at=datetime.now().isoformat() if flat_now else None,
            )
        except Exception:
            log.exception("Failed to persist state on shutdown")

if __name__ == "__main__":
    run()
