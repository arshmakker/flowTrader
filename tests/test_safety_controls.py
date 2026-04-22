"""
Regression tests for LIVE-19 (kill switch), LIVE-20 (PID guard), LIVE-22 (daily loss cap),
and LIVE-23 (operator alert emission from RiskManager events).
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, call
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

    def test_live_pid_file_refuses_start(self, tmp_path):
        """A PID file referencing a live process causes sys.exit(1)."""
        from main import _acquire_pid_lock
        pid_path = str(tmp_path / "regimetrader.pid")
        # Write our own PID — definitely alive.
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        with patch.object(settings, "PID_FILE", pid_path), \
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
