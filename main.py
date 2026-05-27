"""
PCR Contrarian Credit Spread — main orchestrator.

Entry: Mon or Tue 09:20–10:00 IST, signal from live PCR via Shoonya option chain.
Exit:  expiry-day 14:45, MTM loss > 2× entry credit, or EOD 15:10.
"""

import atexit
import logging
import logging.handlers
import os
import sys
import time as _time
from datetime import datetime, time

import pytz
import yaml

from api_helper import ShoonyaApiPy

sys.path.insert(0, os.path.expanduser("~/git/shoonya-auth"))
from broker_client import BrokerClient

from strategy_runner import is_trading_day_ist
from symbol_manager import SymbolManager
from trading_system.auth import shoonya_selenium_auth
from trading_system.config import settings
from trading_system.core import position_persistence
from trading_system.core.expiry_manager import ExpiryManager
from trading_system.core.pcr_credit_spread import PCRCreditSpreadStrategy
from trading_system.core.pcr_signal import get_weekly_pcr
from trading_system.core.risk_manager import RiskManager
from trading_system.core.trade_logger import TradeLogger
from trading_system.existing.market_data import MarketData
from trading_system.ops.alerts import AlertChannel, NullAlertChannel, build_channel
from trading_system.paper.paper_order_manager import PaperOrderManager
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.paper.paper_position_tracker import PaperPositionTracker

IST = pytz.timezone("Asia/Kolkata")
_LOOP_SLEEP = 30
_SESSION_CHECK_INTERVAL = 900

log = logging.getLogger(__name__)


# ── Logging ────────────────────────────────────────────────────────────────────


def _setup_logging() -> None:
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    today_str = datetime.now(IST).strftime("%Y%m%d")
    log_path = os.path.join(settings.LOG_DIR, f"pcs_{today_str}.log")
    fmt = logging.Formatter(settings.LOG_FORMAT)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


# ── PID lock ───────────────────────────────────────────────────────────────────


def _acquire_pid_lock() -> None:
    pid_path = settings.PID_FILE
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)
    if os.path.exists(pid_path):
        try:
            existing_pid = int(open(pid_path).read().strip())
            os.kill(existing_pid, 0)
            snapshot_path = os.path.join(settings.DATA_DIR, "pnl_snapshot.json")
            snapshot_age = None
            if os.path.exists(snapshot_path):
                snapshot_age = _time.time() - os.path.getmtime(snapshot_path)
            if snapshot_age is not None and snapshot_age > settings.PID_FRESHNESS_TIMEOUT_SEC:
                print(f"WARNING: PID {existing_pid} exists but snapshot is stale — overwriting.", file=sys.stderr)
            else:
                print(f"ERROR: pcrTrader already running (PID {existing_pid}).", file=sys.stderr)
                sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(pid_path) and os.remove(pid_path))


# ── Auth ───────────────────────────────────────────────────────────────────────


_SHARED_CRED = os.path.expanduser("~/.shoonya/cred.yml")


def _load_creds(path=_SHARED_CRED):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_creds(creds, path=_SHARED_CRED):
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(creds, f, sort_keys=False)
    os.chmod(path, 0o600)


def _mask_secret(value):
    s = str(value or "")
    return "***" if len(s) <= 8 else f"{s[:4]}...{s[-4:]}"


def _is_oauth_configured(creds):
    return all(str(creds.get(k, "")).strip() for k in ("oauth_url", "client_id", "Secret_Code", "UID"))


def _initialize_api(log, alerts=None) -> ShoonyaApiPy:
    """Delegate to full auth logic in main module; mirrors flowTrader pattern."""
    import re
    import shlex
    import subprocess

    creds = _load_creds()
    api = ShoonyaApiPy()

    if not _is_oauth_configured(creds):
        factor2 = os.environ.get("TWOFA", "").strip() or input("Enter 2FA code: ").strip()
        result = api.login(
            userid=creds["user"],
            password=creds["pwd"],
            twoFA=factor2,
            vendor_code=creds["vc"],
            api_secret=creds["apikey"],
            imei=creds["imei"],
        )
        if not result or str(result.get("stat", "")).lower() != "ok":
            raise RuntimeError(f"Legacy login failed: {result}")
        return api

    # OAuth path
    uid = str(creds.get("UID", "")).strip()
    client_id = str(creds.get("client_id", "")).strip()
    secret_code = str(creds.get("Secret_Code", "")).strip()
    oauth_url = str(creds.get("oauth_url", "")).strip()
    token_url = os.environ.get("SHOONYA_TOKEN_URL", "").strip() or str(creds.get("token_url", "")).strip()
    oauth_api_host = (
        os.environ.get("SHOONYA_OAUTH_API_HOST", "").strip()
        or str(creds.get("oauth_api_host", "")).strip()
        or "https://api.shoonya.com/NorenWClientAPI/"
    )
    oauth_ws = (
        os.environ.get("SHOONYA_OAUTH_WS", "").strip()
        or str(creds.get("oauth_ws_endpoint", "")).strip()
        or "wss://api.shoonya.com/NorenWS/"
    )
    account_id = str(creds.get("Account_ID", "")).strip() or uid
    access_token = str(creds.get("Access_token", "")).strip()

    api.configure_oauth_service_host(oauth_api_host, oauth_ws)

    if access_token:
        api.inject_oauth_header(access_token, uid, account_id)
        if api.validate_oauth_session():
            log.info("OAuth login: cached token valid (%s).", _mask_secret(access_token))
            return api
        log.warning("OAuth login: cached token invalid, re-auth required.")

    # Automated auth-code capture
    def _fetch_auth_code():
        auth_code = os.environ.get("SHOONYA_AUTH_CODE", "").strip()
        if not auth_code and shoonya_selenium_auth.is_configured(creds):
            auth_code = shoonya_selenium_auth.fetch_auth_code(creds)
        if not auth_code:
            cmd = os.environ.get("SHOONYA_AUTH_CODE_CMD", "").strip() or str(creds.get("auth_code_cmd", "")).strip()
            if cmd:
                try:
                    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=180)
                    for line in result.stdout.splitlines():
                        m = re.search(r"Auth\s*Code\s*:\s*([A-Za-z0-9._-]+)", line, re.IGNORECASE)
                        if m:
                            auth_code = m.group(1).strip()
                            break
                except Exception as exc:
                    log.warning("Auth code command failed: %s", exc)
        return auth_code

    oauth_login_url = api.get_oauth_url(oauth_url, client_id)
    for attempt in range(1, 3):
        auth_code = _fetch_auth_code()
        if not auth_code:
            log.warning("OAuth attempt %d: no auth code captured.", attempt)
            continue
        token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
        if not token_data:
            log.warning("OAuth attempt %d: token exchange failed.", attempt)
            continue
        new_token, user_id, _, new_account_id = token_data
        api.inject_oauth_header(new_token, user_id, new_account_id)
        if api.validate_oauth_session():
            creds.update({"Access_token": new_token, "Account_ID": new_account_id, "UID": user_id})
            _save_creds(creds)
            log.info("OAuth login successful (%s).", _mask_secret(new_token))
            return api
        log.warning("OAuth attempt %d: session validation failed.", attempt)

    log.info("Open this URL, complete login, then paste the auth code:\n%s", oauth_login_url)
    auth_code = input("Enter Shoonya auth code: ").strip()
    token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
    if not token_data:
        raise RuntimeError("OAuth token exchange failed (manual fallback)")
    new_token, user_id, _, new_account_id = token_data
    api.inject_oauth_header(new_token, user_id, new_account_id)
    if not api.validate_oauth_session():
        raise RuntimeError("OAuth session validation failed after manual token exchange")
    creds.update({"Access_token": new_token, "Account_ID": new_account_id, "UID": user_id})
    _save_creds(creds)
    log.info("OAuth login successful (manual fallback).")
    return api


def _check_mid_session_auth(api, reauth_state: dict) -> None:
    if api.validate_oauth_session():
        return
    if isinstance(api, BrokerClient):
        raise RuntimeError(
            "Broker proxy session expired — restart broker_proxy.py after regimetrader refreshes its token"
        )
    if reauth_state["attempted"]:
        raise RuntimeError("OAuth session invalid; reauth already attempted — giving up")
    reauth_state["attempted"] = True
    log.warning("OAuth session invalid mid-session; attempting one reauth")
    creds = _load_creds()
    uid = str(creds.get("UID", "")).strip()
    client_id = str(creds.get("client_id", "")).strip()
    secret_code = str(creds.get("Secret_Code", "")).strip()
    token_url = os.environ.get("SHOONYA_TOKEN_URL", "").strip() or str(creds.get("token_url", "")).strip()
    auth_code = os.environ.get("SHOONYA_AUTH_CODE", "").strip()
    if not auth_code and shoonya_selenium_auth.is_configured(creds):
        auth_code = shoonya_selenium_auth.fetch_auth_code(creds)
    if not auth_code:
        raise RuntimeError("Mid-session reauth: no auth code available — giving up")
    token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
    if not token_data:
        raise RuntimeError("Mid-session reauth: token exchange failed")
    new_token, user_id, _, new_account_id = token_data
    api.inject_oauth_header(new_token, user_id, new_account_id)
    if not api.validate_oauth_session():
        raise RuntimeError("Mid-session reauth: session validation failed")
    creds.update({"Access_token": new_token, "Account_ID": new_account_id, "UID": user_id})
    _save_creds(creds)
    log.info("Mid-session reauth succeeded.")


# ── Alerts ─────────────────────────────────────────────────────────────────────


def _build_alert_channel() -> AlertChannel:
    if not settings.ALERTS_ENABLED:
        return NullAlertChannel()
    topic_url = None
    try:
        creds = _load_creds()
        topic_url = creds.get("ALERTS_NTFY_TOPIC_URL")
    except FileNotFoundError:
        pass
    channel = build_channel(
        enabled=True,
        channel_type=settings.ALERTS_CHANNEL,
        ntfy_topic_url=topic_url,
    )
    log.info("Alert channel: %s", type(channel).__name__)
    return channel


# ── Time helpers ───────────────────────────────────────────────────────────────


def _now_ist() -> datetime:
    return datetime.now(IST)


def _in_market_hours() -> bool:
    t = _now_ist().time()
    return time(9, 15) <= t <= time(15, 30)


def _in_entry_window() -> bool:
    t = _now_ist().time()
    sh, sm = (int(x) for x in settings.PCS_ENTRY_START.split(":"))
    eh, em = (int(x) for x in settings.PCS_ENTRY_END.split(":"))
    return time(sh, sm) <= t <= time(eh, em)


def _is_entry_day() -> bool:
    return _now_ist().weekday() in settings.PCS_ENTRY_DAYS


def _past_eod() -> bool:
    return _now_ist().time() >= time(15, 10)


def _past_expiry_close() -> bool:
    return _now_ist().time() >= time(15, 0)


# ── Main ───────────────────────────────────────────────────────────────────────


def run() -> None:
    _setup_logging()
    log.info("=== PCR CREDIT SPREAD SYSTEM STARTING === PAPER_TRADE_MODE=%s", settings.PAPER_TRADE_MODE)
    _acquire_pid_lock()

    if not is_trading_day_ist(datetime.now()):
        log.info("Today is not a trading day. Exiting.")
        return

    alerts = _build_alert_channel()

    proxy_url = os.environ.get("BROKER_PROXY_URL", "").strip()
    if proxy_url:
        api = BrokerClient(proxy_url)
        if not api.validate_oauth_session():
            log.error(
                "Broker proxy at %s not reachable or session invalid — "
                "ensure broker_proxy.py is running and regimetrader has a valid Access_token",
                proxy_url,
            )
            sys.exit(1)
        log.info("Using broker proxy at %s", proxy_url)
    else:
        api = _initialize_api(log, alerts=alerts)

    sm = SymbolManager(api)
    sm.load_symbol_files()

    md = MarketData(api, sm)
    risk = RiskManager(alerts=alerts)
    expiry_mgr = ExpiryManager(sm)
    trade_logger = TradeLogger()

    pos_mgr = PaperPositionTracker()
    order_mgr = PaperOrderManager(md, pos_mgr)
    pnl_engine = PaperPnLEngine(pos_mgr, md, trade_logger)

    strat = PCRCreditSpreadStrategy(order_mgr, md, "NIFTY")
    strats_map = {"NIFTY_PCR": strat}

    meta = position_persistence.load(strats_map, pos_mgr, pnl_engine, risk)
    if meta.get("restored_strategies") or meta.get("tracker_positions"):
        log.info(
            "Restored state: %d strategies, %d tracker positions (saved_at=%s)",
            meta.get("restored_strategies", 0),
            meta.get("tracker_positions", 0),
            meta.get("saved_at"),
        )

    reauth_state = {"attempted": False}
    last_auth_check = _time.time()

    log.info("Entering market-hours loop (sleep=%ds)", _LOOP_SLEEP)

    while True:
        now_ist = _now_ist()

        if not _in_market_hours():
            if now_ist.time() > time(15, 30):
                log.info("Market closed — shutting down")
                break
            _time.sleep(_LOOP_SLEEP)
            continue

        # Kill-switch file
        if os.path.exists(settings.HALT_FILE):
            log.warning("HALT file detected — stopping")
            if strat.is_active():
                record = strat.force_exit("HALT_FILE")
                if record:
                    pnl_engine.record_trade("NIFTY", record["gross_pnl"], record)
                    trade_logger.log_trade(record)
            position_persistence.save(
                strats_map,
                pos_mgr,
                pnl_engine,
                risk,
                session_status=position_persistence.SESSION_FLAT,
                shutdown_reason="halt_file",
            )
            break

        # OAuth health gate
        if _time.time() - last_auth_check >= _SESSION_CHECK_INTERVAL:
            try:
                _check_mid_session_auth(api, reauth_state)
            except RuntimeError as exc:
                log.critical("Auth failure: %s — shutting down", exc)
                break
            last_auth_check = _time.time()

        # Daily loss guard
        if risk.check_daily_loss_cap(pnl_engine) and not settings.PAPER_TRADE_MODE:
            if strat.is_active():
                record = strat.force_exit("DAILY_LOSS_HALT")
                if record:
                    pnl_engine.record_trade("NIFTY", record["gross_pnl"], record)
                    trade_logger.log_trade(record)
            position_persistence.save(
                strats_map,
                pos_mgr,
                pnl_engine,
                risk,
                session_status=position_persistence.SESSION_FLAT,
                shutdown_reason="daily_loss_halt",
            )
            break

        # Expiry-day close at 15:00
        if strat.is_active() and _past_expiry_close():
            pos = strat.pos
            if pos and pos.expiry == now_ist.date().isoformat():
                log.info("Expiry-day 15:00 force-exit")
                record = strat.force_exit("EOD_EXPIRY")
                if record:
                    pnl_engine.record_trade("NIFTY", record["gross_pnl"], record)
                    trade_logger.log_trade(record)
                position_persistence.save(strats_map, pos_mgr, pnl_engine, risk)

        # EOD flat by 15:10
        if strat.is_active() and _past_eod():
            log.info("EOD force-exit at %s", now_ist.strftime("%H:%M"))
            record = strat.force_exit("EOD")
            if record:
                pnl_engine.record_trade("NIFTY", record["gross_pnl"], record)
                trade_logger.log_trade(record)
            position_persistence.save(
                strats_map,
                pos_mgr,
                pnl_engine,
                risk,
                session_status=position_persistence.SESSION_FLAT,
                shutdown_reason="eod",
            )

        # Monitor open position
        if strat.is_active():
            exit_signal = strat.monitor()
            if exit_signal and exit_signal.get("action") == "exit":
                reason = exit_signal["reason"]
                record = strat.force_exit(reason)
                if record:
                    pnl_engine.record_trade("NIFTY", record["gross_pnl"], record)
                    trade_logger.log_trade(record)
                position_persistence.save(strats_map, pos_mgr, pnl_engine, risk)

        # Entry attempt
        if not strat.is_active() and not risk.halted and _is_entry_day() and _in_entry_window():
            spot = md.get_ltp(settings.NIFTY_SPOT_KEY)
            if spot and spot > 0:
                pcr = get_weekly_pcr(api, spot)
                expiry = expiry_mgr.get_expiry("NIFTY")
                if expiry:
                    entered = strat.enter(spot, pcr, expiry, settings.PCS_LOT_SIZE)
                    if entered:
                        position_persistence.save(strats_map, pos_mgr, pnl_engine, risk)
                else:
                    log.warning("Could not determine nearest expiry — skipping entry")
            else:
                log.warning("NIFTY spot LTP unavailable — skipping entry tick")

        _time.sleep(_LOOP_SLEEP)

    log.info("pcrTrader session complete")
    pnl_engine._write_summary()


if __name__ == "__main__":
    run()
