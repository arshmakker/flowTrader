"""
Main Orchestrator — Iron Condor Trading System (agents.md).

Wires all modules together for high-frequency Nifty/BankNifty IC trading.
"""

import atexit
import logging
import os
import re
import shlex
import subprocess
import sys
import time as _time
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import yaml

from api_helper import ShoonyaApiPy
from data_collector import DataCollector
from strategy_runner import is_market_closed_ist, is_market_hours, is_trading_day_ist
from symbol_manager import SymbolManager
from trading_system.auth import shoonya_selenium_auth
from trading_system.config import settings
from trading_system.core import position_persistence
from trading_system.core.day_classifier import DayClassifier
from trading_system.core.expiry_manager import ExpiryManager
from trading_system.core.iron_condor import IronCondorStrategy
from trading_system.core.regime_filter import RegimeFilter
from trading_system.core.risk_manager import RiskManager
from trading_system.core.signal_engine import SignalEngine
from trading_system.core.sr_manager import SRManager
from trading_system.core.trade_logger import TradeLogger
from trading_system.existing.market_data import MarketData
from trading_system.live.live_order_manager import LiveOrderManager
from trading_system.ops.alerts import Alert, AlertChannel, NullAlertChannel, build_channel
from trading_system.ops.startup_reconcile import reconcile_startup_positions
from trading_system.paper.paper_order_manager import PaperOrderManager
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.paper.paper_position_tracker import PaperPositionTracker

DEFAULT_AUTH_CODE_SCRIPT = "/Users/arshdeep/git/Shoonya_oAuth_API.py/tests/getAuthCode.py"


def _build_order_stack(api, md, trade_logger):
    """LIVE-01: construct (pos_mgr, order_mgr, pnl_engine) per settings.PAPER_TRADE_MODE.

    PaperPositionTracker and PaperPnLEngine are state containers — mode-agnostic
    despite the name — so they're shared. Only the order manager swaps. The
    tracker is threaded into whichever order manager is built, so fills flow
    into the same in-memory position state regardless of mode.
    """
    pos_mgr = PaperPositionTracker()
    if settings.PAPER_TRADE_MODE:
        order_mgr = PaperOrderManager(md, pos_mgr)
    else:
        order_mgr = LiveOrderManager(api, md, pos_mgr)
    pnl_engine = PaperPnLEngine(pos_mgr, md, trade_logger)
    return pos_mgr, order_mgr, pnl_engine


def _acquire_pid_lock() -> None:
    """LIVE-20: Refuse to start if another instance is already running.

    Single-laptop reality: macOS sleep / SIGSTOP / a frozen Python process
    leaves the PID alive but non-trading. A pure existence check would block
    a legitimate restart in that case. Combine PID liveness with snapshot
    freshness — if the snapshot is older than PID_FRESHNESS_TIMEOUT_SEC,
    the existing process is presumed unresponsive and the PID file is
    overwritten. The existing LIVE-24 heartbeat is the authoritative
    silent-death signal; this is just the start-time corollary.
    """
    pid_path = settings.PID_FILE
    snapshot_path = os.path.join(settings.DATA_DIR, "pnl_snapshot.json")
    freshness_timeout = settings.PID_FRESHNESS_TIMEOUT_SEC

    os.makedirs(os.path.dirname(pid_path), exist_ok=True)
    if os.path.exists(pid_path):
        try:
            existing_pid = int(open(pid_path).read().strip())
            os.kill(existing_pid, 0)  # signal 0 = check existence only
            # Process exists. Check if it is still trading by snapshot age.
            snapshot_age = None
            if os.path.exists(snapshot_path):
                snapshot_age = _time.time() - os.path.getmtime(snapshot_path)
            if snapshot_age is not None and snapshot_age > freshness_timeout:
                print(
                    f"WARNING: PID {existing_pid} exists but pnl_snapshot.json is "
                    f"{snapshot_age:.0f}s old (> {freshness_timeout}s). Treating "
                    f"as a frozen / suspended process and overwriting the PID file.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"ERROR: RegimeTrader already running (PID {existing_pid}, "
                    f"snapshot age {snapshot_age:.0f}s). If the process is dead, "
                    f"delete {pid_path} and retry."
                    if snapshot_age is not None
                    else f"ERROR: RegimeTrader already running (PID {existing_pid}). "
                    f"If the process is dead, delete {pid_path} and retry.",
                    file=sys.stderr,
                )
                sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file — overwrite below

    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_release_pid_lock)


def _release_pid_lock() -> None:
    """LIVE-20: Remove PID file on clean shutdown."""
    try:
        os.remove(settings.PID_FILE)
    except FileNotFoundError:
        pass


def _log_holiday_calendar(log) -> None:
    """Surface the loaded NSE holiday calendar at startup.

    The calendar lives in settings.TRADING_HOLIDAYS_IST as a hardcoded set;
    NSE adds muhurat sessions and extended-break dates throughout the year
    that won't appear here unless the operator updates the file. Logging the
    next few entries on every startup makes a stale calendar visible — if
    "next 3 holidays" looks wrong against the current date, the calendar is
    out of date and weekend-flatten / is_trading_day_ist may pass through
    a holiday silently.
    """
    today = datetime.now().date().isoformat()
    holidays = sorted(h for h in settings.TRADING_HOLIDAYS_IST if h >= today)
    log.info(
        "Holiday calendar loaded: %d total entries, %d upcoming (next: %s).",
        len(settings.TRADING_HOLIDAYS_IST),
        len(holidays),
        ", ".join(holidays[:3]) if holidays else "none — verify settings.TRADING_HOLIDAYS_IST",
    )


def _require_live_ack() -> None:
    """SHAKEDOWN: refuse to start in live mode without an explicit operator handshake.

    Paper mode bypasses this gate. In live, the operator must explicitly
    create settings.LIVE_ACK_FILE (e.g. `touch data/LIVE_ACK`) to bless the
    session; contents are not parsed, presence alone is the signal. Blocks
    the failure mode where PAPER_TRADE_MODE is flipped to False without a
    deliberate operator decision (config drift, bad rebase, accidental edit).
    """
    if settings.PAPER_TRADE_MODE:
        return
    if not os.path.exists(settings.LIVE_ACK_FILE):
        print(
            f"ERROR: PAPER_TRADE_MODE=False but {settings.LIVE_ACK_FILE} not found. "
            f"Live trading requires an explicit operator handshake. "
            f"Create the file (e.g. `touch {settings.LIVE_ACK_FILE}`) to proceed.",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_kill_switch(strats, pnl_engine, risk, log, alerts=None) -> bool:
    """LIVE-19: Returns True if the HALT file was present (and acted on).
    LIVE-23: emits a warning alert when the file triggers."""
    if not os.path.exists(settings.HALT_FILE):
        return False
    log.critical("HALT FILE DETECTED — initiating emergency stop.")
    if alerts is not None:
        alerts.send(
            Alert(
                event="halt_file_detected",
                severity="warning",
                title="RegimeTrader: halt file triggered",
                body="Operator dropped data/HALT — initiating emergency flatten and exit.",
            )
        )
    _force_exit_all(strats, pnl_engine, risk)
    try:
        os.remove(settings.HALT_FILE)
    except FileNotFoundError:
        pass
    return True


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
                pnl_engine.record_trade(s.instrument, result["pnl"], result)


def _evaluate_stop_checks(strats, pnl_engine, risk, log, regime=None, alerts=None):
    """5/5b. Combined hard stop + LIVE-22 daily rupee cap. Both check_*
    methods short-circuit ``if self.halted: return True`` to signal "session
    is dead" — that's correct as a predicate. The CRITICAL log + flatten
    attempt below is a state-transition action; it must not re-fire every
    cycle once the halt is already set.

    Incident 2026-04-28: a Phase-5b halt at 10:49 produced 20 redundant
    CRITICAL lines (10× combined-stop, 10× daily-cap) in the next 11 minutes,
    polluting the log and making grep on real triggers useless.

    Recovery Exception (AGENTS.md): After a stop-loss, allow single-sided
    re-entry if before 1:00 PM and VIX is stable/falling.
    """
    if risk.halted:
        # Check if recovery is allowed
        if risk.is_recovery_allowed(regime):
            log.info("Recovery exception triggered - attempting single-sided re-entry")
            risk.use_recovery()
            if alerts is not None:
                alerts.send(
                    Alert(
                        event="recovery_activated",
                        severity="info",
                        title="RegimeTrader: recovery exception activated",
                        body="Stop-loss recovery permitted - single-sided re-entry before 1:00 PM",
                    )
                )
        return
    if risk.check_combined_stop_loss(strats):
        _force_exit_all(strats, pnl_engine, risk)
        log.critical("COMBINED STOP LOSS HIT - Trading Halted.")
    if pnl_engine and risk.check_daily_loss_cap(pnl_engine):
        _force_exit_all(strats, pnl_engine, risk)
        log.critical("DAILY LOSS CAP HIT - Trading Halted for the session.")


def _drain_rollback_failures(strats, risk):
    """BUG-05: after each entry attempt, check whether rollback left stuck legs.
    Escalate to a hard halt through the risk manager and clear the flag."""
    for s in strats:
        stuck = getattr(s, "_last_rollback_stuck_legs", None)
        if stuck:
            risk.escalate_rollback_failure(s.instrument, stuck)
            s._last_rollback_stuck_legs = []


def _halt_on_exception(exc, risk, log, alerts=None):
    """BUG-19 / Axiom 3: convert an unhandled main-loop exception into a
    controlled halt rather than a process crash. Caller is responsible for
    persisting state and continuing the loop.
    LIVE-23: emits a critical alert so the operator learns the loop halted."""
    risk.halted = True
    log.critical(
        "Unhandled exception in main loop — halting trading: %s",
        exc,
        exc_info=True,
    )
    if alerts is not None:
        alerts.send(
            Alert(
                event="unhandled_exception",
                severity="critical",
                title="RegimeTrader: main loop halted on exception",
                body=f"{type(exc).__name__}: {exc}. Trading halted. See logs for traceback.",
            )
        )


class _AuthSessionExpired(Exception):
    """Raised when OAuth session is invalid and the one mid-session reauth attempt failed."""


_SESSION_CHECK_INTERVAL = 900  # seconds between OAuth health probes


def _check_mid_session_auth(api, log, alerts, reauth_state) -> None:
    """BUG-07: OAuth health gate called every _SESSION_CHECK_INTERVAL seconds.
    `reauth_state` is a mutable dict with key 'attempted' (False on loop entry).
    Raises _AuthSessionExpired if session is invalid and recovery fails or was
    already attempted — caller must let it propagate to crash the process."""
    if api.validate_oauth_session():
        return
    if reauth_state["attempted"]:
        raise _AuthSessionExpired("OAuth session invalid; reauth already attempted this session — giving up")
    reauth_state["attempted"] = True
    log.warning("OAuth session invalid mid-session; attempting one reauth")
    if not _mid_session_reauth(api, log, alerts=alerts):
        raise _AuthSessionExpired("OAuth mid-session reauth failed — giving up")
    log.info("Mid-session reauth succeeded; continuing")


def _mid_session_reauth(api, log, alerts=None) -> bool:
    """One OAuth refresh attempt mid-session. Never blocks for manual input.
    Returns True on success, False if any step fails."""
    creds = _load_creds()
    uid = str(creds.get("UID", "")).strip()
    client_id = str(creds.get("client_id", "")).strip()
    secret_code = str(creds.get("Secret_Code", "")).strip()
    token_url = os.environ.get("SHOONYA_TOKEN_URL", "").strip() or str(creds.get("token_url", "")).strip()

    auth_code = os.environ.get("SHOONYA_AUTH_CODE", "").strip()
    if not auth_code and shoonya_selenium_auth.is_configured(creds):
        log.info("Mid-session reauth: capturing auth code via in-process Selenium.")
        auth_code = shoonya_selenium_auth.fetch_auth_code(creds)
    if not auth_code:
        auth_code = _fetch_auth_code_from_command(creds, log)
    if not auth_code:
        log.warning("Mid-session reauth: no auth code available from any automated path.")
        return False

    token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
    if not token_data:
        detail = api.get_last_broker_error() or "unknown token exchange failure"
        log.warning("Mid-session reauth: token exchange failed: %s", detail)
        return False

    new_token, user_id, _refresh, new_account_id = token_data
    api.inject_oauth_header(new_token, user_id, new_account_id)
    if not api.validate_oauth_session():
        detail = api.get_last_broker_error() or "unknown validation failure"
        log.warning("Mid-session reauth: session validation failed after exchange: %s", detail)
        return False

    creds["Access_token"] = new_token
    creds["Account_ID"] = new_account_id
    creds["UID"] = user_id
    _save_creds(creds)
    log.info("Mid-session reauth succeeded; access token updated.")
    return True


def _build_alert_channel(log) -> AlertChannel:
    """LIVE-23: build the operator alert channel at startup, honoring the
    settings master switch and pulling the ntfy topic URL from cred.yml."""
    if not settings.ALERTS_ENABLED:
        return NullAlertChannel()
    topic_url = None
    try:
        with open("cred.yml") as f:
            creds = yaml.safe_load(f) or {}
        topic_url = creds.get("ALERTS_NTFY_TOPIC_URL")
    except FileNotFoundError:
        pass
    channel = build_channel(
        enabled=True,
        channel_type=settings.ALERTS_CHANNEL,
        ntfy_topic_url=topic_url,
    )
    log.info("Alert channel built: %s", type(channel).__name__)
    return channel


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


def _next_trading_session_date(from_date):
    """Fix #4: the next IST trading day strictly after ``from_date``.

    Skips weekends and configured IST holidays. Returns None if none found
    within a 14-day horizon (defensive — prevents infinite loop on a bad
    calendar).
    """
    d = from_date + timedelta(days=1)
    for _ in range(14):
        if is_trading_day_ist(datetime.combine(d, datetime.min.time())):
            return d
        d += timedelta(days=1)
    return None


def _find_near_dte_at_next_session(strats, next_session_date, threshold):
    """Fix #4: active strategies whose DTE at ``next_session_date`` would be
    below ``threshold``. Matches expiry_manager.py's calendar-day convention
    ``(expiry - session).days`` for consistency with the entry-gate rule.

    Motivation: the 2026-04-17 → 2026-04-21 incident saw an IC with Fri→Mon
    DTE dropping from 4 to 1 across the weekend. The entry-gate DTE check ran
    only at entry (Thursday, DTE=5) and never re-evaluated. This helper closes
    the loop: at every TRADE_END, check whether tomorrow's DTE will still pass
    the threshold; if not, flatten now.
    """
    out = []
    for s in strats:
        if not s.is_active():
            continue
        pos = getattr(s, "_position", None)
        if pos is None:
            continue
        expiry_str = getattr(pos, "expiry_date", "") or ""
        if not expiry_str:
            continue
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (expiry_date - next_session_date).days
        if dte < threshold:
            out.append(s)
    return out


def _crossed_non_trading_day(saved_at_iso: str, now: Optional[datetime] = None) -> bool:
    """Fix #5b: True if any calendar day strictly between ``saved_at`` and ``now``
    (or either boundary day) is a non-trading day (weekend / IST holiday).

    Why: identifies the "Friday afternoon crash → Monday morning restart" window
    where an abnormal shutdown stranded positions across a non-trading gap. We
    need to catch it even when the gap is short (e.g., shutdown Fri 13:48,
    restart Mon 09:23 — only ~67h, but the weekend changed the market state).
    """
    if not saved_at_iso:
        return False
    try:
        saved_dt = datetime.fromisoformat(saved_at_iso)
    except ValueError:
        return False
    now = now or datetime.now()
    if now < saved_dt:
        return False
    d = saved_dt.date()
    end = now.date()
    while d <= end:
        if not is_trading_day_ist(datetime.combine(d, datetime.min.time())):
            return True
        d += timedelta(days=1)
    return False


def _find_past_expiry(strats, today_iso):
    """Return active strategies whose IC expiry is strictly earlier than today.

    Why: the on-the-day TRADE_END hard-close can be missed (e.g. position
    persisted before `expiry_date` existed, like the 2026-04-21 NIFTY miss that
    motivated b99bfea). On next startup the stranded position would otherwise
    sit forever logging `symbol not in master, LTP=0, excluded from mark`. Auto-
    settling off stale ticks is unsafe — correct settlement needs NSE's final
    settlement price — so the caller halts startup and directs the operator to
    run a reconciliation tool manually with the authoritative spot.
    """
    out = []
    for s in strats:
        if not s.is_active():
            continue
        pos = getattr(s, "_position", None)
        if pos is None:
            continue
        expiry = getattr(pos, "expiry_date", "") or ""
        if expiry and expiry < today_iso:
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
    # Restrict to owner-only — file holds OAuth token + Secret_Code; default
    # umask leaves it world-readable, exposing trade-placement credentials to
    # any local read (backup process, log scrape, container layer).
    os.chmod(path, 0o600)


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
    timeout_raw = (
        os.environ.get("SHOONYA_AUTH_CODE_TIMEOUT", "").strip() or str(creds.get("auth_code_timeout", "180")).strip()
    )
    try:
        timeout = max(30, int(timeout_raw))
    except ValueError:
        timeout = 180

    # Tokenize so we can run without a shell — shell=True would interpret
    # any metacharacter in cmd (potentially injected via cred.yml drift or
    # SHOONYA_AUTH_CODE_CMD env). _resolve_auth_code_cmd returns either the
    # operator's exact string or `python3 "<DEFAULT_AUTH_CODE_SCRIPT>"`, both
    # of which split cleanly under POSIX rules.
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        log.warning("Auth code command unparseable (%s): %s", exc, cmd)
        return ""
    if not argv:
        return ""

    # Runner uses shell=False, so shell control tokens (`&&`, `;`, pipes,
    # redirects) survive shlex.split as literal argv entries — they would be
    # passed to the binary as positional args, never interpreted. The classic
    # case is `cd path && python script.py`: shell=False execs `cd` (a macOS
    # shim that exits 0 ignoring the trailing args), the script never runs,
    # and we silently fall through with no auth code. Refuse loudly instead.
    shell_tokens = {"&&", "||", ";", "|", "&", ">", "<", ">>", "<<", ">&", "<&"}
    leaked = [a for a in argv if a in shell_tokens]
    if leaked:
        log.warning(
            "Auth code command contains shell control token(s) %s; runner uses "
            "shell=False so they cannot be interpreted. Rewrite cred.yml's "
            "auth_code_cmd as a single binary invocation (e.g. "
            "'/usr/bin/python3 /abs/path/to/script.py').",
            leaked,
        )
        return ""

    log.info("Attempting auth code via command: %s", cmd)
    try:
        process = subprocess.Popen(
            argv,
            shell=False,
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
        detail = (
            api.get_last_broker_error()
            or (result.get("emsg") if isinstance(result, dict) else "")
            or "Unknown login failure"
        )
        raise RuntimeError(f"Shoonya legacy login failed: {detail}")
    return api


def _validate_oauth_creds(creds, log):
    """Pre-flight sanity check on cred.yml OAuth fields.

    Logs WARNING (not error — we still attempt the call) for drift from the
    end-to-end-verified working configuration. Catches the two stale-config bugs
    that recurred during the 2026-04-22 debugging session: a 40-char dummy
    Secret_Code and a token_url pointing at the IP-whitelisted trade.shoonya.com
    host. See CLAUDE.md "Auth flow" section for full context.
    """
    required = ("UID", "client_id", "Secret_Code", "oauth_url")
    missing = [k for k in required if not str(creds.get(k, "")).strip()]
    if missing:
        log.warning("OAuth pre-flight: cred.yml missing required fields: %s", missing)

    secret_code = str(creds.get("Secret_Code", "")).strip()
    if secret_code and len(secret_code) < 50:
        log.warning(
            "OAuth pre-flight: Secret_Code is %d chars; the working value is 64 chars. "
            "Likely the dummy/old value — exchange will return INVALID_VERIFIER.",
            len(secret_code),
        )

    token_url = os.environ.get("SHOONYA_TOKEN_URL", "").strip() or str(creds.get("token_url", "")).strip()
    if token_url and "api.shoonya.com" not in token_url:
        if "trade.shoonya.com" in token_url:
            log.warning(
                "OAuth pre-flight: token_url uses trade.shoonya.com which enforces "
                "static-IP whitelist. If your IP isn't whitelisted in the Shoonya "
                "portal you'll get INVALID_IP. Switch to api.shoonya.com to bypass. "
                "Current: %s",
                token_url,
            )
        else:
            log.warning(
                "OAuth pre-flight: token_url is on an unrecognized host: %s. Working host is api.shoonya.com.",
                token_url,
            )


def _initialize_api_oauth(api, creds, log, alerts=None):
    _validate_oauth_creds(creds, log)
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
    oauth_ws_endpoint = (
        os.environ.get("SHOONYA_OAUTH_WS", "").strip()
        or str(creds.get("oauth_ws_endpoint", "")).strip()
        or "wss://api.shoonya.com/NorenWS/"
    )
    account_id = str(creds.get("Account_ID", "")).strip() or uid
    access_token = str(creds.get("Access_token", "")).strip()
    retry_raw = (
        os.environ.get("SHOONYA_OAUTH_REAUTH_ATTEMPTS", "").strip()
        or str(creds.get("oauth_reauth_attempts", "2")).strip()
    )
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

    # Auth-code capture priority: env var (manual override) -> in-process Selenium ->
    # external subprocess fallback. In-process Selenium runs the entire OAuth flow
    # in this Python process so the token exchange fires before the browser's
    # redirect can race against it.
    for attempt in range(1, oauth_reauth_attempts + 1):
        auth_code = os.environ.get("SHOONYA_AUTH_CODE", "").strip()
        if not auth_code and shoonya_selenium_auth.is_configured(creds):
            log.info(
                "OAuth re-auth attempt %s/%s: capturing auth code via in-process Selenium.",
                attempt,
                oauth_reauth_attempts,
            )
            auth_code = shoonya_selenium_auth.fetch_auth_code(creds)
        if not auth_code:
            auth_code = _fetch_auth_code_from_command(creds, log)
        if not auth_code:
            log.warning("OAuth re-auth attempt %s/%s: no auth code captured.", attempt, oauth_reauth_attempts)
            continue

        token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
        if not token_data:
            detail = api.get_last_broker_error() or "Unknown token exchange failure"
            log.warning(
                "OAuth re-auth attempt %s/%s failed at token exchange: %s", attempt, oauth_reauth_attempts, detail
            )
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
        log.warning(
            "OAuth re-auth attempt %s/%s failed at session validation: %s", attempt, oauth_reauth_attempts, detail
        )

    # Final fallback: manual code entry.
    log.info("Open this URL, complete login, then paste the auth code:\n%s", oauth_login_url)
    auth_code = input("Enter Shoonya auth code: ").strip()
    token_data = api.exchange_auth_code(auth_code, secret_code, client_id, uid, token_url=token_url)
    if not token_data:
        detail = api.get_last_broker_error() or "Unknown token exchange failure"
        if alerts is not None:
            alerts.send(
                Alert(
                    event="oauth_auth_failure",
                    severity="critical",
                    title="RegimeTrader: OAuth login failed",
                    body=f"All auth paths exhausted. Token exchange failure: {detail}",
                )
            )
        raise RuntimeError(f"OAuth token exchange failed: {detail}")
    new_access_token, user_id, _refresh_token, new_account_id = token_data
    api.inject_oauth_header(new_access_token, user_id, new_account_id)
    if not api.validate_oauth_session():
        detail = api.get_last_broker_error() or "Unknown validation failure"
        if alerts is not None:
            alerts.send(
                Alert(
                    event="oauth_auth_failure",
                    severity="critical",
                    title="RegimeTrader: OAuth validation failed",
                    body=f"Token exchange succeeded but session validation failed: {detail}",
                )
            )
        raise RuntimeError(f"OAuth session validation failed after token exchange: {detail}")
    creds["Access_token"] = new_access_token
    creds["Account_ID"] = new_account_id
    creds["UID"] = user_id
    _save_creds(creds)
    log.info(
        "OAuth login successful (manual fallback); access token cached to cred.yml (%s).",
        _mask_secret(new_access_token),
    )
    return api


def initialize_api(log, alerts=None) -> ShoonyaApiPy:
    creds = _load_creds()
    api = ShoonyaApiPy()
    if _is_oauth_configured(creds):
        return _initialize_api_oauth(api, creds, log, alerts=alerts)
    return _initialize_api_legacy(api, creds)


def run():
    setup_logging()
    log = logging.getLogger("main")
    _acquire_pid_lock()  # LIVE-20: fail fast if already running
    _require_live_ack()  # SHAKEDOWN: live mode requires explicit operator handshake
    log.info("=== IRON CONDOR SYSTEM STARTING ===")
    _log_holiday_calendar(log)

    # Non-trading-day short-circuit. Without this, a Saturday/Sunday/holiday
    # startup runs through OAuth (against a broker that's typically in weekend
    # maintenance and returns 502 on validation), which the system interprets
    # as "cached token expired" and burns an unattended re-auth — guaranteed
    # to fail because the auth-code subprocess can't operate without a TTY.
    if not is_trading_day_ist(datetime.now()):
        log.info("Today is not a trading day (weekend or IST holiday). Exiting.")
        return

    # LIVE-23: build alert channel before API init so OAuth failures surface.
    alerts = _build_alert_channel(log)

    api = initialize_api(log, alerts=alerts)
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
        "NIFTY": DayClassifier(md, signals, settings.NIFTY_SYMBOL, settings.NIFTY_SPOT_KEY),
        "BANKNIFTY": DayClassifier(md, signals, settings.BANKNIFTY_SYMBOL, settings.BANKNIFTY_SPOT_KEY),
    }
    risk = RiskManager(alerts=alerts)
    expiry_mgr = ExpiryManager(sm)
    sr_mgr = SRManager()
    trade_logger = TradeLogger()

    pos_mgr, order_mgr, pnl_engine = _build_order_stack(api, md, trade_logger)

    # Strategies
    nifty_ic = IronCondorStrategy(order_mgr, md, "NIFTY")
    banknifty_ic = IronCondorStrategy(order_mgr, md, "BANKNIFTY")
    strats = [nifty_ic, banknifty_ic]
    strats_map = {"NIFTY": nifty_ic, "BANKNIFTY": banknifty_ic}

    # Restore any carried-overnight positions + P&L state.
    meta = position_persistence.load(strats_map, pos_mgr, pnl_engine, risk, regime_filter=regime)
    if meta.get("restored_strategies") or meta.get("tracker_positions"):
        log.info(
            "Restored carried state: %d strategies, %d tracker positions (saved_at=%s, trading_date=%s)",
            meta.get("restored_strategies", 0),
            meta.get("tracker_positions", 0),
            meta.get("saved_at"),
            meta.get("trading_date"),
        )
        # FixQ2: seed the market_data last-valid-option-LTP cache with each
        # restored leg's avg_price. The first post-restore monitor cycle may
        # hit a Shoonya lp-is-spot response (see FixQ1) on a cold-cache symbol
        # and return 0.0 — which silently freezes monitor() via its any(p<=0)
        # early-exit. Seeding gives a stale-but-finite fallback on cycle one.
        try:
            tracker_positions = getattr(pos_mgr, "_positions", {}) or {}
            seeded = 0
            for sym, pos_dict in tracker_positions.items():
                avg = pos_dict.get("avg_price") if isinstance(pos_dict, dict) else None
                if avg is None:
                    continue
                try:
                    md.seed_option_ltp(sym, float(avg))
                    seeded += 1
                except (TypeError, ValueError):
                    continue
            if seeded:
                log.info("Seeded %d option LTP cache entries from restored avg_prices.", seeded)
        except Exception:
            log.exception("Failed to seed option LTP cache from restored tracker positions")

    # Past-expiry safety: if startup restored positions whose expiry was before
    # today, the on-the-day hard-close was missed. Refuse to enter the trading
    # loop; operator must reconcile manually at the exchange's settlement price.
    startup_today_iso = datetime.now().date().isoformat()
    past_expiry = _find_past_expiry(strats, startup_today_iso)
    if past_expiry:
        for s in past_expiry:
            expiry = getattr(s._position, "expiry_date", "")
            log.error(
                "Past-expiry position: %s expired %s (today=%s). Hard-close was missed on expiry day.",
                s.instrument,
                expiry,
                startup_today_iso,
            )
        log.error(
            "HALTED at startup: %d past-expiry position(s) require manual settlement at the NSE "
            "final settlement price. See tools/reconcile_expired_nifty_20260421.py for a template; "
            "adapt instrument + settlement spot and rerun. State left untouched.",
            len(past_expiry),
        )
        risk.halted = True
        risk.stop_hit_at = datetime.now()
        return

    # Fix #5b: abnormal-exit-over-non-trading-day guard. If the previous session
    # ended before TRADE_END (shutdown_reason != "eod") AND the gap between
    # shutdowns crossed a weekend/holiday AND positions are still open, the
    # weekend-flatten path was bypassed (the gate lives inside the main loop at
    # TRADE_END; Ctrl-C / crash exits the loop first). Restoring blindly re-
    # exposes the positions to whatever the market did across the gap. Halt and
    # require the operator to reconcile intentionally.
    last_reason = meta.get("last_shutdown_reason", "") or ""
    last_saved_at = meta.get("saved_at", "") or ""
    if (
        last_reason
        and last_reason != "eod"
        and not position_persistence.is_flat(strats_map, pos_mgr)
        and _crossed_non_trading_day(last_saved_at)
    ):
        open_syms = [s.instrument for s in strats if s.is_active()]
        log.error(
            "Past-abnormal-exit position(s): %s. Previous session ended with reason=%r "
            "at %s; restart crossed a non-trading day.",
            ", ".join(open_syms) or "(tracker-only)",
            last_reason,
            last_saved_at,
        )
        log.error(
            "HALTED at startup: abnormal shutdown (reason=%r) on %s left open positions that were "
            "carried across a non-trading day. Weekend-flatten was bypassed. Manual reconciliation "
            "required — inspect marks vs next-session open and close intentionally, or clear state "
            "if positions were already closed out-of-band.",
            last_reason,
            last_saved_at,
        )
        risk.halted = True
        risk.stop_hit_at = datetime.now()
        return

    # LIVE-07: broker is the authoritative source of open exposure in live
    # mode. A crash between leg-2 fill and leg-3 send leaves the engine's
    # JSON stale (0 legs on disk, 2 at broker) or phantom (JSON says 4,
    # broker squared off overnight). Reconcile against get_positions BEFORE
    # entering the loop; any divergence halts startup until operator clears.
    # Skipped in paper mode — no broker counterpart to compare against.
    if not settings.PAPER_TRADE_MODE:
        try:
            broker_positions = api.get_positions() or []
        except Exception:
            log.exception("HALTED at startup: get_positions() call failed; cannot verify broker state")
            if alerts is not None:
                alerts.send(
                    Alert(
                        event="startup_reconcile_failed",
                        severity="critical",
                        title="RegimeTrader startup halted - broker query failed",
                        body="get_positions() raised; engine cannot verify broker state. Inspect and clear.",
                    )
                )
            risk.halted = True
            risk.stop_hit_at = datetime.now()
            return

        engine_positions = getattr(pos_mgr, "_positions", {}) or {}
        report = reconcile_startup_positions(engine_positions, broker_positions)
        log.info(report.summary())
        if not report.consistent:
            log.error(
                "HALTED at startup: engine and broker positions diverge. %s",
                report.summary(),
            )
            if alerts is not None:
                alerts.send(
                    Alert(
                        event="startup_reconcile_divergent",
                        severity="critical",
                        title="RegimeTrader startup halted - broker/engine divergence",
                        body=report.summary(),
                    )
                )
            risk.halted = True
            risk.stop_hit_at = datetime.now()
            return

    day_classes = {"NIFTY": None, "BANKNIFTY": None}
    collection_started = False
    _auth_state = {"attempted": False}
    _last_session_check = 0.0

    log.info("Entering main loop...")

    try:
        while True:
            try:
                # LIVE-19: operator emergency stop — checked before anything else.
                if _check_kill_switch(strats, pnl_engine, risk, log, alerts=alerts):
                    position_persistence.save(
                        strats_map,
                        pos_mgr,
                        pnl_engine,
                        risk,
                        regime,
                        session_status=position_persistence.SESSION_FLAT,
                        shutdown_reason="kill-switch",
                    )
                    sys.exit(0)

                # BUG-07: periodic OAuth health check. Broker guarantees no mid-session
                # expiry, but if it does happen: one automated reauth, then crash loudly.
                _now_wall = _time.time()
                if _now_wall - _last_session_check >= _SESSION_CHECK_INTERVAL:
                    _last_session_check = _now_wall
                    _check_mid_session_auth(api, log, alerts, _auth_state)

                now = datetime.now()
                now_t = now.time()

                # 1. Market Hours & Data Collection
                if not collection_started and is_market_hours():
                    collector.start_collection()
                    collection_started = True

                if is_market_closed_ist():
                    log.info("Market closed. Exiting loop.")
                    break

                # LIVE-11: intra-day margin shortfall. SEBI peak-margin snapshots
                # hit at random intervals; a position that passed LIVE-10's
                # pre-entry check can still hit shortfall if spot moves or SPAN
                # re-prices. Halt new entries on any broker-reported shortfall;
                # existing positions keep being monitored/harvested.
                if not settings.PAPER_TRADE_MODE and not risk.halted:
                    shortfall = order_mgr.get_margin_shortfall()
                    if shortfall > 0:
                        log.critical("LIVE-11 intraday margin shortfall ₹%.2f — halting new entries.", shortfall)
                        if alerts is not None:
                            alerts.send(
                                Alert(
                                    event="intraday_margin_shortfall",
                                    severity="critical",
                                    title="RegimeTrader: intraday margin shortfall",
                                    body=f"Broker reports margin shortfall of Rs {shortfall:,.2f}. New entries halted; reconcile against broker before clearing.",
                                )
                            )
                        risk.halted = True
                        risk.stop_hit_at = datetime.now()

                # 2a. Expiry-day early close (TRADE_END_EXPIRY = 15:00).
                #     Fires 10 min before TRADE_END to avoid the expiry settlement squeeze.
                if now_t >= datetime.strptime(settings.TRADE_END_EXPIRY, "%H:%M").time():
                    expiring_early = _find_expiring_today(strats, datetime.now().date().isoformat())
                    if expiring_early:
                        _force_exit_all(expiring_early, pnl_engine, risk)
                        log.info("Expiry-day close: force-exited %d expiring position(s).", len(expiring_early))

                # 2b. End-of-day: flatten remaining positions; also flatten if
                #     next day is not a trading day or if next-session DTE would
                #     drop below IC_DTE_THRESHOLD.
                if now_t >= datetime.strptime(settings.TRADE_END, "%H:%M").time():
                    today_date = datetime.now().date()
                    today_date.isoformat()
                    tomorrow = datetime.now() + timedelta(days=1)
                    next_day_is_trading = is_trading_day_ist(tomorrow)
                    # Fix #4: Overnight-DTE block. Close any position whose DTE at
                    # the next trading session would be below IC_DTE_THRESHOLD.
                    # Catches the Fri→Mon weekend-gap case where calendar DTE
                    # collapses (Fri=4 → Mon=1 for a Tue weekly).
                    next_session = _next_trading_session_date(today_date)
                    if next_session is not None:
                        near_expiry_next = _find_near_dte_at_next_session(
                            strats, next_session, settings.IC_DTE_THRESHOLD
                        )
                        if near_expiry_next:
                            _force_exit_all(near_expiry_next, pnl_engine, risk)
                            log.info(
                                "Pre-near-expiry close: force-exited %d position(s) "
                                "whose DTE at next session (%s) would be < %d.",
                                len(near_expiry_next),
                                next_session.isoformat(),
                                settings.IC_DTE_THRESHOLD,
                            )
                    if not next_day_is_trading:
                        _force_exit_all(strats, pnl_engine, risk)
                        log.info("Pre-holiday/weekend close: force-exited all positions.")
                    if pnl_engine:
                        pnl_engine.write_snapshot()
                    if collection_started:
                        collector.stop_collection()
                        collection_started = False
                    flat_now = position_persistence.is_flat(strats_map, pos_mgr)
                    position_persistence.save(
                        strats_map,
                        pos_mgr,
                        pnl_engine,
                        risk,
                        regime,
                        session_status=(
                            position_persistence.SESSION_FLAT if flat_now else position_persistence.SESSION_ACTIVE
                        ),
                        shutdown_reason="eod",
                        flat_verified_at=datetime.now().isoformat() if flat_now else None,
                    )
                    log.info(
                        "Daily session ended.%s",
                        " All positions closed." if not next_day_is_trading else " Positions carried overnight.",
                    )
                    break

                # 3. Day Classification — per instrument (BUG-08).
                # Allow classification any time after market open, not just 10:30 AM.
                for inst, clf in classifiers.items():
                    if day_classes[inst] is None:
                        day_classes[inst] = clf.classify()
                        log.info(
                            "Day Classified %s: %s (%s)",
                            inst,
                            day_classes[inst].day_type,
                            day_classes[inst].confidence,
                        )

                # 4. Monitoring & Harvest Cycle (runs pre-classification so carried positions are watched).
                for s in strats:
                    if s.is_active():
                        result = s.monitor()
                        if result:
                            pnl_engine.record_trade(s.instrument, result["pnl"], result)

                # 5 / 5b. Combined hard stop + LIVE-22 daily rupee cap.
                _evaluate_stop_checks(strats, pnl_engine, risk, log, regime, alerts)

                # 6. Entry Logic (requires per-instrument classification).
                if not risk.halted:
                    for s in strats:
                        dc = day_classes.get(s.instrument)
                        if dc is None:
                            continue  # pre-classify time for this instrument
                        if s.is_active():
                            continue
                        if not regime.get_regime_gate(dc.day_type, s.instrument):
                            continue
                        # Fetch context for entry
                        spot_key = settings.NIFTY_SPOT_KEY if s.instrument == "NIFTY" else settings.BANKNIFTY_SPOT_KEY
                        spot = md.get_ltp(spot_key)
                        vix = regime.get_vix()
                        sr_high, sr_low = sr_mgr.get_20day_high_low(s.instrument)
                        expiry = expiry_mgr.get_expiry(s.instrument)

                        if spot > 0 and expiry:
                            s.enter(spot, vix, sr_high, sr_low, sr_mgr, expiry, settings.IC_LOT_SIZE)

                # BUG-05: escalate any stuck-rollback events from this cycle's entries.
                _drain_rollback_failures(strats, risk)

                # Keep live P&L fresh for dashboards (realised + unrealised).
                # LIVE-01/LIVE-24: snapshot must refresh in both modes so the
                # heartbeat watchdog sees a live process.
                if pnl_engine:
                    pnl_engine.write_snapshot()

                # Persist position + P&L state so a crash/restart can resume cleanly.
                position_persistence.save(
                    strats_map,
                    pos_mgr,
                    pnl_engine,
                    risk,
                    regime,
                    session_status=position_persistence.SESSION_ACTIVE,
                )

                _time.sleep(settings.SIGNAL_RECHECK_SEC)
            except _AuthSessionExpired:
                raise  # bypass halt — crash loudly so the operator knows auth is broken
            except Exception as exc:
                # BUG-19 / Axiom 3: convert unhandled cycle exceptions into a halt.
                _halt_on_exception(exc, risk, log, alerts=alerts)
                try:
                    position_persistence.save(
                        strats_map,
                        pos_mgr,
                        pnl_engine,
                        risk,
                        regime,
                        session_status=position_persistence.SESSION_ACTIVE,
                        shutdown_reason="exception-halt",
                    )
                except Exception:
                    log.exception("Failed to persist state after halt")
                _time.sleep(settings.SIGNAL_RECHECK_SEC)

    except _AuthSessionExpired as exc:
        log.critical("OAuth session expired mid-session and reauth failed: %s — exiting", exc)
    except KeyboardInterrupt:
        log.info("Interrupted by user. Exiting...")
    finally:
        if collection_started:
            collector.stop_collection()
        # Fix #5a (HARD RULE — no weekend/holiday carry): if shutdown happens
        # before TRADE_END and the next session crosses a non-trading day, the
        # in-loop weekend-flatten gate never fires. Force flatten here
        # unconditionally — per operator directive, positions must NEVER be
        # carried across a non-trading day, even through abnormal exits.
        #
        # Fill-quality caveat: if the abnormal exit was triggered by a quote
        # feed failure (the 2026-04-17 incident), flatten will use whatever
        # cached/stale LTPs the market_data layer has. In paper mode this books
        # an imperfect-but-bounded PnL. In live mode, the broker may reject or
        # fill off-market — but still preferable to silent weekend carry.
        try:
            tomorrow = datetime.now() + timedelta(days=1)
            if not is_trading_day_ist(tomorrow) and not position_persistence.is_flat(strats_map, pos_mgr):
                log.warning(
                    "Abnormal shutdown before TRADE_END with open positions and "
                    "next day is non-trading — force-flattening (no-weekend-carry rule)."
                )
                _force_exit_all(strats, pnl_engine, risk)
        except Exception:
            log.exception("Shutdown-time force-flatten raised")
        try:
            flat_now = position_persistence.is_flat(strats_map, pos_mgr)
            position_persistence.save(
                strats_map,
                pos_mgr,
                pnl_engine,
                risk,
                regime,
                session_status=(position_persistence.SESSION_FLAT if flat_now else position_persistence.SESSION_ACTIVE),
                shutdown_reason="shutdown",
                flat_verified_at=datetime.now().isoformat() if flat_now else None,
            )
        except Exception:
            log.exception("Failed to persist state on shutdown")


if __name__ == "__main__":
    run()
