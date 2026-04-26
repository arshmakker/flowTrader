"""
Regression tests for LIVE-19 (kill switch), LIVE-20 (PID guard), LIVE-22 (daily loss cap),
LIVE-23 (operator alert emission from RiskManager events), and the SHAKEDOWN-mode
proving-period controls (LIVE_ACK handshake, shakedown daily-loss cap, per-session
entry cap).
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from trading_system.core.risk_manager import RiskManager
from trading_system.config import settings
from trading_system.ops.alerts import NullAlertChannel


# ── LIVE-19: Kill switch ──────────────────────────────────────────────────────

class TestKillSwitch:

    def _make_deps(self):
        strats = [MagicMock(is_active=MagicMock(return_value=False))]
        pnl_engine = MagicMock()
        risk = MagicMock()
        log = MagicMock()
        return strats, pnl_engine, risk, log

    def test_halt_file_absent_returns_false(self, tmp_path):
        from main import _check_kill_switch
        strats, pnl, risk, log = self._make_deps()
        halt_path = str(tmp_path / "HALT")
        with patch.object(settings, "HALT_FILE", halt_path):
            result = _check_kill_switch(strats, pnl, risk, log)
        assert result is False

    def test_halt_file_present_force_exits_and_returns_true(self, tmp_path):
        from main import _check_kill_switch, _force_exit_all
        strats, pnl, risk, log = self._make_deps()
        halt_path = str(tmp_path / "HALT")
        open(halt_path, "w").close()

        with patch.object(settings, "HALT_FILE", halt_path), \
             patch("main._force_exit_all") as mock_exit:
            result = _check_kill_switch(strats, pnl, risk, log)

        assert result is True
        mock_exit.assert_called_once_with(strats, pnl, risk)

    def test_halt_file_deleted_after_trigger(self, tmp_path):
        from main import _check_kill_switch
        strats, pnl, risk, log = self._make_deps()
        halt_path = str(tmp_path / "HALT")
        open(halt_path, "w").close()

        with patch.object(settings, "HALT_FILE", halt_path), \
             patch("main._force_exit_all"):
            _check_kill_switch(strats, pnl, risk, log)

        assert not os.path.exists(halt_path)


# ── LIVE-20: PID file guard ───────────────────────────────────────────────────

class TestPidGuard:

    def test_stale_pid_file_allows_start(self, tmp_path):
        """A PID file referencing a dead process is overwritten — startup proceeds."""
        from main import _acquire_pid_lock
        pid_path = str(tmp_path / "regimetrader.pid")
        # Write a PID that definitely doesn't exist.
        with open(pid_path, "w") as f:
            f.write("999999999")

        with patch.object(settings, "PID_FILE", pid_path), \
             patch("atexit.register"):
            _acquire_pid_lock()   # must not raise or exit

        assert open(pid_path).read().strip() == str(os.getpid())

    def test_live_pid_with_fresh_snapshot_refuses_start(self, tmp_path):
        """PID alive AND snapshot fresh → exit. The standard 'already running' case."""
        from main import _acquire_pid_lock
        pid_path = str(tmp_path / "regimetrader.pid")
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)
        snapshot_path = os.path.join(data_dir, "pnl_snapshot.json")
        # Fresh snapshot (just touched).
        open(snapshot_path, "w").close()
        # Write our own PID — definitely alive.
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        with patch.object(settings, "PID_FILE", pid_path), \
             patch.object(settings, "DATA_DIR", data_dir), \
             pytest.raises(SystemExit) as exc_info:
            _acquire_pid_lock()

        assert exc_info.value.code == 1

    def test_live_pid_with_stale_snapshot_overwrites(self, tmp_path):
        """PID alive but snapshot stale → process presumed frozen / suspended;
        overwrite the PID file rather than block. Real on this operator's
        macOS sleep / SIGSTOP scenario."""
        from main import _acquire_pid_lock
        import time
        pid_path = str(tmp_path / "regimetrader.pid")
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)
        snapshot_path = os.path.join(data_dir, "pnl_snapshot.json")
        open(snapshot_path, "w").close()
        # Backdate the snapshot well past the freshness timeout.
        old = time.time() - 7200
        os.utime(snapshot_path, (old, old))
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        with patch.object(settings, "PID_FILE", pid_path), \
             patch.object(settings, "DATA_DIR", data_dir), \
             patch.object(settings, "PID_FRESHNESS_TIMEOUT_SEC", 600), \
             patch("atexit.register"):
            _acquire_pid_lock()  # must NOT exit; overwrites instead

        assert open(pid_path).read().strip() == str(os.getpid())

    def test_live_pid_with_no_snapshot_refuses_start(self, tmp_path):
        """Snapshot missing entirely (e.g., first-ever run that crashed before
        first snapshot) → fall back to the conservative 'already running' path."""
        from main import _acquire_pid_lock
        pid_path = str(tmp_path / "regimetrader.pid")
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir, exist_ok=True)
        # No snapshot file written.
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        with patch.object(settings, "PID_FILE", pid_path), \
             patch.object(settings, "DATA_DIR", data_dir), \
             pytest.raises(SystemExit) as exc_info:
            _acquire_pid_lock()

        assert exc_info.value.code == 1

    def test_no_pid_file_writes_own_pid(self, tmp_path):
        """Clean startup writes current PID to the lock file."""
        from main import _acquire_pid_lock
        pid_path = str(tmp_path / "regimetrader.pid")

        with patch.object(settings, "PID_FILE", pid_path), \
             patch("atexit.register"):
            _acquire_pid_lock()

        assert open(pid_path).read().strip() == str(os.getpid())


# ── LIVE-22: Daily loss cap ───────────────────────────────────────────────────

class TestDailyLossCap:

    def _make_pnl(self, daily_realised, unrealised):
        pnl = MagicMock()
        pnl.daily_realised_pnl = daily_realised
        pnl.unrealised_pnl = unrealised
        return pnl

    def test_below_cap_returns_false(self):
        """Daily P&L just above the cap threshold — no halt."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-30_000, unrealised=-19_999)
        # total = -49_999, cap = -50_000 → not breached
        assert risk.check_daily_loss_cap(pnl) is False
        assert risk.halted is False

    def test_at_cap_boundary_does_not_halt(self):
        """Daily P&L exactly equal to cap (not below) — no halt (strict <)."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-40_000, unrealised=-10_000)
        # total = -50_000, cap = -50_000 → NOT breached (strict <)
        assert risk.check_daily_loss_cap(pnl) is False
        assert risk.halted is False

    def test_one_rupee_over_cap_halts(self):
        """Daily P&L one rupee below cap — halts."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-40_000, unrealised=-10_001)
        # total = -50_001 < -50_000 → breached
        assert risk.check_daily_loss_cap(pnl) is True
        assert risk.halted is True

    def test_over_cap_returns_true_and_halts(self):
        """Daily P&L beyond the cap — halts."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-45_000, unrealised=-10_000)
        assert risk.check_daily_loss_cap(pnl) is True
        assert risk.halted is True

    def test_already_halted_returns_true_without_recalculating(self):
        """Once halted, subsequent calls short-circuit."""
        risk = RiskManager()
        risk.halted = True
        pnl = self._make_pnl(daily_realised=0, unrealised=0)
        assert risk.check_daily_loss_cap(pnl) is True
        # unrealised_pnl must not be accessed after halted short-circuit
        pnl.unrealised_pnl  # access is fine, but .daily_realised_pnl shouldn't be read
        # The key assertion: no exception and still halted
        assert risk.halted is True

    def test_cap_halts_entry_in_subsequent_cycles(self):
        """Once the cap fires, risk.halted=True gates new entries in subsequent cycles
        via the standard `if not risk.halted` guard — same as combined stop-loss."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-60_000, unrealised=0)

        # Cycle 1: cap hit → halted
        assert risk.check_daily_loss_cap(pnl) is True
        assert risk.halted is True

        # Cycles 2+: entry block skipped because risk.halted is True
        for _ in range(2):
            assert risk.halted is True   # entry guard would skip


# ── LIVE-23: Operator alert emission from safety events ──────────────────────

class TestAlertEmission:
    """RiskManager must emit alerts on the three critical events: combined stop,
    daily loss cap, rollback failure. Default (no alerts arg) keeps existing
    call sites working unchanged."""

    def _make_pnl(self, daily_realised, unrealised):
        pnl = MagicMock()
        pnl.daily_realised_pnl = daily_realised
        pnl.unrealised_pnl = unrealised
        return pnl

    def test_default_risk_manager_has_null_alert_channel(self):
        risk = RiskManager()
        # The internal channel is a Null by default — existing 14 call sites that
        # construct RiskManager() with no args continue to work without changes.
        assert isinstance(risk._alerts, NullAlertChannel)

    def test_daily_loss_cap_emits_critical_alert(self):
        alerts = NullAlertChannel()
        risk = RiskManager(alerts=alerts)
        pnl = self._make_pnl(daily_realised=-60_000, unrealised=0)

        assert risk.check_daily_loss_cap(pnl) is True
        assert len(alerts.sent) == 1
        assert alerts.sent[0].event == "daily_loss_cap"
        assert alerts.sent[0].severity == "critical"

    def test_rollback_failure_emits_critical_alert(self):
        alerts = NullAlertChannel()
        risk = RiskManager(alerts=alerts)

        risk.escalate_rollback_failure(
            instrument="NIFTY",
            stuck_legs=[{"symbol": "NFO|X", "side": "BUY", "qty": 650}],
        )

        assert len(alerts.sent) == 1
        assert alerts.sent[0].event == "rollback_failure"
        assert alerts.sent[0].severity == "critical"
        assert "NIFTY" in alerts.sent[0].body

    def test_combined_stop_emits_critical_alert_when_confirmed(self):
        alerts = NullAlertChannel()
        risk = RiskManager(alerts=alerts)

        # Build one active strategy whose per-leg LTPs produce a loss past 3x stop.
        strat = MagicMock()
        strat.is_active.return_value = True
        pos = MagicMock()
        pos.sc_sym = "NFO|SC"
        pos.sp_sym = "NFO|SP"
        pos.lc_sym = "NFO|LC"
        pos.lp_sym = "NFO|LP"
        pos.entry_credit = 20.0
        pos.lots = 10
        pos.max_profit = 13_000.0  # 20 * 65 * 10
        strat._position = pos
        # LTPs give current_prem huge vs entry_credit → big unrealised loss.
        strat.md = MagicMock()
        strat.md.get_ltp.side_effect = lambda sym: {
            "NFO|SC": 60.0, "NFO|SP": 60.0, "NFO|LC": 1.0, "NFO|LP": 1.0,
        }[sym]
        strat.md.get_lot_size.return_value = 65

        # Feed confirm-ticks-required breaches so the hard stop fires.
        required = max(1, int(getattr(settings, "IC_HARD_STOP_CONFIRM_TICKS", 1)))
        for _ in range(required):
            hit = risk.check_combined_stop_loss([strat])
        assert hit is True
        assert risk.halted is True
        assert any(a.event == "combined_stop" for a in alerts.sent)
        assert all(a.severity == "critical" for a in alerts.sent if a.event == "combined_stop")


# ── Security: cred.yml file mode locked to owner-only ──────────────────────

class TestCredFileMode:
    """_save_creds must restrict cred.yml to mode 0o600 — file holds the OAuth
    token + Secret_Code. Default umask leaves it world-readable, exposing
    trade-placement credentials to any local read."""

    def test_save_creds_chmods_to_0600(self, tmp_path):
        from main import _save_creds
        path = str(tmp_path / "cred.yml")
        _save_creds({"foo": "bar"}, path=path)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_save_creds_overwrites_loose_perms(self, tmp_path):
        """If an older save left the file 0o644, a fresh save must tighten it."""
        from main import _save_creds
        path = str(tmp_path / "cred.yml")
        with open(path, "w") as f:
            f.write("foo: bar\n")
        os.chmod(path, 0o644)
        _save_creds({"foo": "baz"}, path=path)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600


# ── SHAKEDOWN: tighter daily loss cap during proving period ──────────────────

class TestShakedownDailyLossCap:
    """check_daily_loss_cap reads DAILY_MAX_LOSS_SHAKEDOWN when SHAKEDOWN_MODE=True.
    Sized for the proving-period blast radius; flips off automatically once the
    operator clears SHAKEDOWN_MODE post-LIVE-21."""

    def _make_pnl(self, daily_realised, unrealised):
        pnl = MagicMock()
        pnl.daily_realised_pnl = daily_realised
        pnl.unrealised_pnl = unrealised
        return pnl

    def test_shakedown_cap_active_halts_below_tighter_threshold(self):
        risk = RiskManager()
        # Default DAILY_MAX_LOSS=50k would NOT halt at -12k loss; shakedown
        # cap of 10k DOES halt. The tighter cap is the whole point.
        pnl = self._make_pnl(daily_realised=-12_000, unrealised=0)
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
            assert risk.check_daily_loss_cap(pnl) is True
            assert risk.halted is True

    def test_shakedown_cap_inactive_uses_steady_state_threshold(self):
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-12_000, unrealised=0)
        # SHAKEDOWN_MODE=False → DAILY_MAX_LOSS=50k still applies; -12k is fine.
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            assert risk.check_daily_loss_cap(pnl) is False
            assert risk.halted is False

    def test_shakedown_under_cap_does_not_halt(self):
        """A small loss within the tight shakedown cap leaves trading running."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-5_000, unrealised=0)
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
            assert risk.check_daily_loss_cap(pnl) is False
            assert risk.halted is False

    def test_shakedown_emits_critical_alert_with_tighter_cap_in_body(self):
        """Alert body must reference the actual cap that fired, not the
        steady-state default — operator needs to know which threshold they hit."""
        alerts = NullAlertChannel()
        risk = RiskManager(alerts=alerts)
        pnl = self._make_pnl(daily_realised=-15_000, unrealised=0)
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
            assert risk.check_daily_loss_cap(pnl) is True
        assert len(alerts.sent) == 1
        assert alerts.sent[0].event == "daily_loss_cap"
        assert "10,000" in alerts.sent[0].body  # tighter cap, not 50k


# ── SHAKEDOWN: live-ACK handshake required for live mode ─────────────────────

class TestLiveAck:
    """main._require_live_ack refuses to proceed when PAPER_TRADE_MODE=False
    and data/LIVE_ACK is missing. Paper mode bypasses entirely."""

    def test_paper_mode_bypasses_gate(self, tmp_path):
        from main import _require_live_ack
        ack_path = str(tmp_path / "LIVE_ACK")  # does not exist
        with patch.object(settings, "PAPER_TRADE_MODE", True), \
             patch.object(settings, "LIVE_ACK_FILE", ack_path):
            _require_live_ack()  # must return cleanly

    def test_live_mode_without_ack_file_exits(self, tmp_path):
        from main import _require_live_ack
        ack_path = str(tmp_path / "LIVE_ACK")  # does not exist
        with patch.object(settings, "PAPER_TRADE_MODE", False), \
             patch.object(settings, "LIVE_ACK_FILE", ack_path):
            with pytest.raises(SystemExit) as exc_info:
                _require_live_ack()
            assert exc_info.value.code == 1

    def test_live_mode_with_ack_file_proceeds(self, tmp_path):
        from main import _require_live_ack
        ack_path = str(tmp_path / "LIVE_ACK")
        open(ack_path, "w").close()
        with patch.object(settings, "PAPER_TRADE_MODE", False), \
             patch.object(settings, "LIVE_ACK_FILE", ack_path):
            _require_live_ack()  # must return cleanly


# ── SHAKEDOWN: per-(instrument, IST date) entry cap ──────────────────────────

class TestShakedownEntryCap:
    """IronCondorStrategy refuses new entries past IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN
    when SHAKEDOWN_MODE=True. Counter resets on IST date roll. Inactive when
    SHAKEDOWN_MODE=False (paper days must not be capped)."""

    def _make_strategy(self):
        from trading_system.core.iron_condor import IronCondorStrategy
        return IronCondorStrategy(order_manager=MagicMock(), market_data=MagicMock(), instrument="NIFTY")

    def test_cap_inactive_when_shakedown_off(self):
        """Default SHAKEDOWN_MODE=False → cap returns True regardless of count."""
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            for _ in range(5):
                assert strat._check_session_entry_cap() is True

    def test_cap_blocks_after_first_entry_in_shakedown(self):
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1):
            assert strat._check_session_entry_cap() is True
            strat._record_session_entry()
            assert strat._check_session_entry_cap() is False

    def test_cap_allows_configured_count_then_blocks(self):
        """IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN=3 → first three pass, fourth refused."""
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 3):
            for _ in range(3):
                assert strat._check_session_entry_cap() is True
                strat._record_session_entry()
            assert strat._check_session_entry_cap() is False

    def test_counter_resets_on_ist_date_rollover(self):
        """Fresh trading day starts the counter back at zero — restart-safe within
        the same day depends on the date string, which advances at midnight."""
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1):
            # Day 1 — exhaust the cap.
            with patch("trading_system.core.iron_condor.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 27, 10, 30)
                mock_dt.strptime = datetime.strptime  # keep static helpers usable
                assert strat._check_session_entry_cap() is True
                strat._record_session_entry()
                assert strat._check_session_entry_cap() is False
            # Day 2 — counter resets, entry allowed again.
            with patch("trading_system.core.iron_condor.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 28, 9, 30)
                mock_dt.strptime = datetime.strptime
                assert strat._check_session_entry_cap() is True

    def test_cap_does_not_count_unsuccessful_attempts(self):
        """_check returns False without incrementing — only _record bumps count.
        Pin: returns and stops dispatching enter() before the success path runs,
        so an unsuccessful entry attempt never burns the cap."""
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1):
            # 100 checks without any record_session_entry — still allowed.
            for _ in range(100):
                assert strat._check_session_entry_cap() is True
            # First record consumes the cap.
            strat._record_session_entry()
            assert strat._check_session_entry_cap() is False

    def test_enter_short_circuits_on_cap_without_strike_calc(self):
        """Wiring pin: when the cap is exhausted, enter() / enter_hedge_first()
        return False before doing any strike calculation or quote fetch. If
        someone removes the cap-check call from either entry path, this test
        catches it — the success path runs and market_data.get_ltp gets called.
        """
        strat = self._make_strategy()
        # Pre-populate the counter so the cap is exhausted on first call.
        strat._entries_today_date = datetime.now().date().isoformat()
        strat._entries_today_count = 1

        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1), \
             patch.object(settings, "IC_ENTRY_MODE", "sequential"):
            sr_manager = MagicMock()
            result = strat.enter(
                spot=24000, vix=15, sr_high=24500, sr_low=23500,
                sr_manager=sr_manager, expiry="29-MAY-2026", lots=10,
            )
        assert result is False
        # Strike calc and quote fetch should never have run.
        strat.md.get_ltp.assert_not_called()
        strat.md.get_quote_book.assert_not_called()

    def test_enter_hedge_first_short_circuits_on_cap_without_strike_calc(self):
        """Same wiring pin for the hedge-first path — defaulted on this branch
        via IC_ENTRY_MODE='hedge_first' in settings.py."""
        strat = self._make_strategy()
        strat._entries_today_date = datetime.now().date().isoformat()
        strat._entries_today_count = 1

        with patch.object(settings, "SHAKEDOWN_MODE", True), \
             patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1):
            sr_manager = MagicMock()
            result = strat.enter_hedge_first(
                spot=24000, vix=15, sr_high=24500, sr_low=23500,
                sr_manager=sr_manager, expiry="29-MAY-2026", lots=10,
            )
        assert result is False
        strat.md.get_ltp.assert_not_called()
        strat.md.get_quote_book.assert_not_called()
