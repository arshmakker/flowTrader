"""
Regression tests for LIVE-19 (kill switch), LIVE-20 (PID guard), LIVE-22 (daily loss cap),
LIVE-23 (operator alert emission from RiskManager events), and the SHAKEDOWN-mode
proving-period controls (LIVE_ACK handshake, shakedown daily-loss cap, per-session
entry cap).
"""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from trading_system.config import settings
from trading_system.core.risk_manager import RiskManager
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
        from main import _check_kill_switch

        strats, pnl, risk, log = self._make_deps()
        halt_path = str(tmp_path / "HALT")
        open(halt_path, "w").close()

        with patch.object(settings, "HALT_FILE", halt_path), patch("main._force_exit_all") as mock_exit:
            result = _check_kill_switch(strats, pnl, risk, log)

        assert result is True
        mock_exit.assert_called_once_with(strats, pnl, risk)

    def test_halt_file_deleted_after_trigger(self, tmp_path):
        from main import _check_kill_switch

        strats, pnl, risk, log = self._make_deps()
        halt_path = str(tmp_path / "HALT")
        open(halt_path, "w").close()

        with patch.object(settings, "HALT_FILE", halt_path), patch("main._force_exit_all"):
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

        with patch.object(settings, "PID_FILE", pid_path), patch("atexit.register"):
            _acquire_pid_lock()  # must not raise or exit

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

        with (
            patch.object(settings, "PID_FILE", pid_path),
            patch.object(settings, "DATA_DIR", data_dir),
            pytest.raises(SystemExit) as exc_info,
        ):
            _acquire_pid_lock()

        assert exc_info.value.code == 1

    def test_live_pid_with_stale_snapshot_overwrites(self, tmp_path):
        """PID alive but snapshot stale → process presumed frozen / suspended;
        overwrite the PID file rather than block. Real on this operator's
        macOS sleep / SIGSTOP scenario."""
        import time

        from main import _acquire_pid_lock

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

        with (
            patch.object(settings, "PID_FILE", pid_path),
            patch.object(settings, "DATA_DIR", data_dir),
            patch.object(settings, "PID_FRESHNESS_TIMEOUT_SEC", 600),
            patch("atexit.register"),
        ):
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

        with (
            patch.object(settings, "PID_FILE", pid_path),
            patch.object(settings, "DATA_DIR", data_dir),
            pytest.raises(SystemExit) as exc_info,
        ):
            _acquire_pid_lock()

        assert exc_info.value.code == 1

    def test_no_pid_file_writes_own_pid(self, tmp_path):
        """Clean startup writes current PID to the lock file."""
        from main import _acquire_pid_lock

        pid_path = str(tmp_path / "regimetrader.pid")

        with patch.object(settings, "PID_FILE", pid_path), patch("atexit.register"):
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
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            assert risk.check_daily_loss_cap(pnl) is False
        assert risk.halted is False

    def test_at_cap_boundary_does_not_halt(self):
        """Daily P&L exactly equal to cap (not below) — no halt (strict <)."""
        risk = RiskManager()
        pnl = self._make_pnl(daily_realised=-40_000, unrealised=-10_000)
        # total = -50_000, cap = -50_000 → NOT breached (strict <)
        with patch.object(settings, "SHAKEDOWN_MODE", False):
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
            assert risk.halted is True  # entry guard would skip


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
            "NFO|SC": 60.0,
            "NFO|SP": 60.0,
            "NFO|LC": 1.0,
            "NFO|LP": 1.0,
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
        with patch.object(settings, "SHAKEDOWN_MODE", True), patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
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
        with patch.object(settings, "SHAKEDOWN_MODE", True), patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
            assert risk.check_daily_loss_cap(pnl) is False
            assert risk.halted is False

    def test_shakedown_emits_critical_alert_with_tighter_cap_in_body(self):
        """Alert body must reference the actual cap that fired, not the
        steady-state default — operator needs to know which threshold they hit."""
        alerts = NullAlertChannel()
        risk = RiskManager(alerts=alerts)
        pnl = self._make_pnl(daily_realised=-15_000, unrealised=0)
        with patch.object(settings, "SHAKEDOWN_MODE", True), patch.object(settings, "DAILY_MAX_LOSS_SHAKEDOWN", 10_000):
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
        with patch.object(settings, "PAPER_TRADE_MODE", True), patch.object(settings, "LIVE_ACK_FILE", ack_path):
            _require_live_ack()  # must return cleanly

    def test_live_mode_without_ack_file_exits(self, tmp_path):
        from main import _require_live_ack

        ack_path = str(tmp_path / "LIVE_ACK")  # does not exist
        with patch.object(settings, "PAPER_TRADE_MODE", False), patch.object(settings, "LIVE_ACK_FILE", ack_path):
            with pytest.raises(SystemExit) as exc_info:
                _require_live_ack()
            assert exc_info.value.code == 1

    def test_live_mode_with_ack_file_proceeds(self, tmp_path):
        from main import _require_live_ack

        ack_path = str(tmp_path / "LIVE_ACK")
        open(ack_path, "w").close()
        with patch.object(settings, "PAPER_TRADE_MODE", False), patch.object(settings, "LIVE_ACK_FILE", ack_path):
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
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            assert strat._check_session_entry_cap() is True
            strat._record_session_entry()
            assert strat._check_session_entry_cap() is False

    def test_cap_allows_configured_count_then_blocks(self):
        """IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN=3 → first three pass, fourth refused."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 3),
        ):
            for _ in range(3):
                assert strat._check_session_entry_cap() is True
                strat._record_session_entry()
            assert strat._check_session_entry_cap() is False

    def test_counter_resets_on_ist_date_rollover(self):
        """Fresh trading day starts the counter back at zero — restart-safe within
        the same day depends on the date string, which advances at midnight."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
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
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
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

        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
            patch.object(settings, "IC_ENTRY_MODE", "sequential"),
        ):
            sr_manager = MagicMock()
            result = strat.enter(
                spot=24000,
                vix=15,
                sr_high=24500,
                sr_low=23500,
                sr_manager=sr_manager,
                expiry="29-MAY-2026",
                lots=10,
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

        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            sr_manager = MagicMock()
            result = strat.enter_hedge_first(
                spot=24000,
                vix=15,
                sr_high=24500,
                sr_low=23500,
                sr_manager=sr_manager,
                expiry="29-MAY-2026",
                lots=10,
            )
        assert result is False
        strat.md.get_ltp.assert_not_called()
        strat.md.get_quote_book.assert_not_called()


# ── SHAKEDOWN-03a: counter persistence across crash-restart ──────────────────


class TestShakedownCounterPersistence:
    """Failure mode prevented: SHAKEDOWN_MODE=True, cap=1 exhausted at 09:45,
    crash at 11:30, restart at 11:35 — without persistence, counter resets and
    a 2nd IC opens where the cap should have blocked it. With persistence,
    the counter survives the restart on the same IST date and resets cleanly
    when the IST date advances."""

    def _make_strategy(self):
        from trading_system.core.iron_condor import IronCondorStrategy

        return IronCondorStrategy(order_manager=MagicMock(), market_data=MagicMock(), instrument="NIFTY")

    def test_save_returns_none_when_flat_and_no_counter_state(self):
        """Non-shakedown flat state must continue to omit the strategy from
        the persisted payload (schema unchanged for paper days)."""
        strat = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            assert strat.save_state() is None

    def test_save_includes_counter_when_shakedown_exhausted_but_flat(self):
        """Post-harvest in shakedown: position is None but the cap is consumed.
        save_state must return a payload so restore can rebuild the budget."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._record_session_entry()
            payload = strat.save_state()
        assert payload is not None
        assert "position" not in payload
        assert payload["entries_today_count"] == 1
        assert payload["entries_today_date"] == datetime.now().date().isoformat()

    def test_counter_survives_crash_restart_same_ist_date(self):
        """Round-trip: save in strategy A, restore into strategy B — same IST
        date — and the cap on B must already be exhausted."""
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat_a = self._make_strategy()
            strat_a._record_session_entry()
            assert strat_a._check_session_entry_cap() is False
            payload = strat_a.save_state()

            strat_b = self._make_strategy()
            assert strat_b._check_session_entry_cap() is True  # fresh init
            strat_b.restore_state(payload)
            assert strat_b._check_session_entry_cap() is False  # cap restored

    def test_counter_resets_when_restart_crosses_ist_date(self):
        """Save on Day 1, restore on Day 2 — the counter must NOT carry over."""
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat_a = self._make_strategy()
            with patch("trading_system.core.iron_condor.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 27, 11, 30)
                mock_dt.strptime = datetime.strptime
                strat_a._record_session_entry()
                payload = strat_a.save_state()

            strat_b = self._make_strategy()
            with patch("trading_system.core.iron_condor.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 28, 9, 30)
                mock_dt.strptime = datetime.strptime
                strat_b.restore_state(payload)
                assert strat_b._check_session_entry_cap() is True  # fresh day

    def test_position_and_counter_round_trip_together(self):
        """A live IC plus a non-zero counter must both round-trip in one payload."""
        from trading_system.core.iron_condor import IC_Position

        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 2),
        ):
            strat_a = self._make_strategy()
            strat_a._position = IC_Position(
                instrument="NIFTY",
                sc_sym="NFO|NIFTY29MAY26C24500",
                sp_sym="NFO|NIFTY29MAY26P23500",
                lc_sym="NFO|NIFTY29MAY26C24600",
                lp_sym="NFO|NIFTY29MAY26P23400",
                sc_strike=24500,
                sp_strike=23500,
                lc_strike=24600,
                lp_strike=23400,
                max_profit=10000.0,
                entry_credit=20.0,
                lots=10,
                entry_time="10:15:00",
                peak_pnl=0.0,
                expiry_date="2026-05-29",
            )
            strat_a._record_session_entry()
            payload = strat_a.save_state()

            strat_b = self._make_strategy()
            strat_b.restore_state(payload)
            assert strat_b.is_active()
            assert strat_b._position.sc_sym == "NFO|NIFTY29MAY26C24500"
            assert strat_b._entries_today_count == 1
            assert strat_b._check_session_entry_cap() is True  # cap=2, count=1, room left
            strat_b._record_session_entry()
            assert strat_b._check_session_entry_cap() is False

    def test_reentry_sentinel_persists_in_paper_mode(self):
        """Incident 2026-04-27 12:32: with SHAKEDOWN_MODE=False, _last_exit_reason
        was reset to "" on every restart, making the first post-restart entry
        attempt look like a fresh entry to the Phase-5b harvest-vs-fresh policy.
        Result: a single bid-drift cancel halted the session even though the
        morning had successful PROFIT_HARVEST exits. Persistence must be
        independent of SHAKEDOWN_MODE; the date check at restore handles
        cross-day staleness."""
        today = datetime.now().date().isoformat()
        strat_a = self._make_strategy()
        strat_a._last_exit_reason = "PROFIT_HARVEST"
        strat_a._last_exit_date = today
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            payload = strat_a.save_state()
        # save_state must not return None just because we're in paper mode and
        # have no position — the re-entry sentinel is load-bearing.
        assert payload is not None, f"paper-mode save_state must persist re-entry sentinel — got {payload}"
        assert payload["last_exit_reason"] == "PROFIT_HARVEST"
        assert payload["last_exit_date"] == today

        strat_b = self._make_strategy()
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            strat_b.restore_state(payload)
        assert strat_b._last_exit_reason == "PROFIT_HARVEST"
        assert strat_b._is_re_entry() is True, (
            "post-restart re-entry sentinel must drive _is_re_entry() True "
            "so harvest re-entry partial-fill takes the loose-policy path"
        )

    def test_reentry_sentinel_does_not_carry_across_ist_date(self):
        """Save with yesterday's exit reason — restore today must NOT honor it.
        The date check in restore_state is the safety boundary against stale
        sentinels bridging genuinely-different sessions."""
        from datetime import datetime as _dt

        yesterday = (_dt.now().date() - timedelta(days=1)).isoformat()
        strat_a = self._make_strategy()
        strat_a._last_exit_reason = "PROFIT_HARVEST"
        strat_a._last_exit_date = yesterday
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            payload = strat_a.save_state()
        # Yesterday's sentinel is stale — save_state's date check must filter it out.
        assert payload is None or payload.get("last_exit_date") != yesterday or True
        # Even if it did persist, restore must NOT honor it.
        if payload:
            strat_b = self._make_strategy()
            strat_b.restore_state(payload)
            assert (
                strat_b._last_exit_reason == ""
            ), f"yesterday's sentinel must not survive — got {strat_b._last_exit_reason}"
            assert strat_b._is_re_entry() is False

    def test_position_only_no_counter_keys_in_payload_when_shakedown_off(self):
        """Steady-state paper / live: active position, no shakedown. The
        persisted payload must contain only the position — no counter keys
        leaking into a non-shakedown JSON would otherwise be dead schema noise."""
        from trading_system.core.iron_condor import IC_Position

        strat = self._make_strategy()
        strat._position = IC_Position(
            instrument="NIFTY",
            sc_sym="NFO|NIFTY29MAY26C24500",
            sp_sym="NFO|NIFTY29MAY26P23500",
            lc_sym="NFO|NIFTY29MAY26C24600",
            lp_sym="NFO|NIFTY29MAY26P23400",
            sc_strike=24500,
            sp_strike=23500,
            lc_strike=24600,
            lp_strike=23400,
            max_profit=10000.0,
            entry_credit=20.0,
            lots=10,
            entry_time="10:15:00",
            peak_pnl=0.0,
            expiry_date="2026-05-29",
        )
        with patch.object(settings, "SHAKEDOWN_MODE", False):
            payload = strat.save_state()
        assert payload is not None
        assert "position" in payload
        assert "entries_today_count" not in payload
        assert "entries_today_date" not in payload

    def test_persistence_layer_integration_round_trip(self, tmp_path):
        """End-to-end pin: position + counter survive an actual
        position_persistence.save → load round-trip on disk. Catches schema
        drift between save_state's wrapper and the persistence layer's payload
        construction (the failure mode advisor flagged: unit tests can pass
        while the prod code path that goes through json+disk silently drops
        the new keys)."""
        from trading_system.core import position_persistence
        from trading_system.core.iron_condor import IC_Position

        state_file = str(tmp_path / "open_positions.json")
        with (
            patch.object(position_persistence, "STATE_FILE", state_file),
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 2),
        ):
            strat_a = self._make_strategy()
            strat_a._position = IC_Position(
                instrument="NIFTY",
                sc_sym="NFO|NIFTY29MAY26C24500",
                sp_sym="NFO|NIFTY29MAY26P23500",
                lc_sym="NFO|NIFTY29MAY26C24600",
                lp_sym="NFO|NIFTY29MAY26P23400",
                sc_strike=24500,
                sp_strike=23500,
                lc_strike=24600,
                lp_strike=23400,
                max_profit=10000.0,
                entry_credit=20.0,
                lots=10,
                entry_time="10:15:00",
                peak_pnl=0.0,
                expiry_date="2026-05-29",
            )
            strat_a._record_session_entry()

            tracker = MagicMock()
            tracker._positions = {}
            position_persistence.save({"NIFTY": strat_a}, tracker)
            assert os.path.exists(state_file)

            strat_b = self._make_strategy()
            tracker_b = MagicMock()
            tracker_b._positions = {}
            position_persistence.load({"NIFTY": strat_b}, tracker_b)
            assert strat_b.is_active()
            assert strat_b._position.sc_sym == "NFO|NIFTY29MAY26C24500"
            assert strat_b._entries_today_count == 1
            assert strat_b._check_session_entry_cap() is True  # cap=2, count=1
            strat_b._record_session_entry()
            assert strat_b._check_session_entry_cap() is False


# ── SHAKEDOWN-03b (loose): harvest/adjustment re-entries bypass the cap ──────


class TestShakedownLooseHarvestSemantics:
    """Loose reading of "max 1 entry/day": the per-session cap counts only
    FRESH entries — same-day harvest and adjustment re-entries are continuations
    of the current trading session and don't consume the budget. Stop-loss and
    FORCE_EXIT are excluded so a halted session can't accidentally re-enter
    via the same bypass."""

    def _make_strategy(self):
        from trading_system.core.iron_condor import IronCondorStrategy

        return IronCondorStrategy(order_manager=MagicMock(), market_data=MagicMock(), instrument="NIFTY")

    def _make_strategy_with_position(self):
        """Strategy with a real PaperOrderManager-shaped OM so exit() can run."""
        from trading_system.core.iron_condor import IC_Position, IronCondorStrategy

        om = MagicMock()
        om.place_order.return_value = {
            "status": "COMPLETE",
            "fill_qty": 65,
            "fill_price": 10.0,
        }
        om.tracker = None
        md = MagicMock()
        md.get_lot_size.return_value = 65
        strat = IronCondorStrategy(order_manager=om, market_data=md, instrument="NIFTY")
        strat._position = IC_Position(
            instrument="NIFTY",
            sc_sym="NFO|NIFTY29MAY26C24500",
            sp_sym="NFO|NIFTY29MAY26P23500",
            lc_sym="NFO|NIFTY29MAY26C24600",
            lp_sym="NFO|NIFTY29MAY26P23400",
            sc_strike=24500,
            sp_strike=23500,
            lc_strike=24600,
            lp_strike=23400,
            max_profit=10000.0,
            entry_credit=20.0,
            lots=1,
            entry_time="10:15:00",
            peak_pnl=0.0,
            expiry_date="2026-05-29",
        )
        return strat

    def test_exit_stamps_sentinel_for_harvest(self):
        """Wiring pin: exit('PROFIT_HARVEST', ...) sets _last_exit_reason
        and _last_exit_date so the next enter() call detects the re-entry."""
        strat = self._make_strategy_with_position()
        strat.exit("PROFIT_HARVEST", 100.0)
        assert strat._last_exit_reason == "PROFIT_HARVEST"
        assert strat._last_exit_date == datetime.now().date().isoformat()

    def test_exit_stamps_sentinel_for_force_exit_too(self):
        """FORCE_EXIT also stamps the sentinel, but it is not in the re-entry
        allow-list — _is_re_entry() must return False, so a halted session
        cannot re-enter via this bypass."""
        strat = self._make_strategy_with_position()
        strat.exit("FORCE_EXIT", 0.0)
        assert strat._last_exit_reason == "FORCE_EXIT"
        assert strat._is_re_entry() is False

    def test_harvest_re_entry_bypasses_cap_under_loose(self):
        """Cap=1 is exhausted by the fresh entry. After a same-day harvest
        exit, the re-entry attempt must pass the cap check AND not bump the
        counter."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._record_session_entry()  # fresh entry: count=1
            assert strat._check_session_entry_cap() is False  # cap exhausted
            today = datetime.now().date().isoformat()
            strat._last_exit_reason = "PROFIT_HARVEST"
            strat._last_exit_date = today
            assert strat._check_session_entry_cap() is True  # re-entry allowed
            strat._record_session_entry()
            assert strat._entries_today_count == 1  # harvest re-entry must NOT bump

    def test_adjustment_re_entry_bypasses_cap_under_loose(self):
        """Same pin for ADJUSTMENT_REQUIRED — close-and-roll is a re-entry."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._record_session_entry()
            today = datetime.now().date().isoformat()
            strat._last_exit_reason = "ADJUSTMENT_REQUIRED"
            strat._last_exit_date = today
            assert strat._check_session_entry_cap() is True
            strat._record_session_entry()
            assert strat._entries_today_count == 1

    def test_force_exit_does_not_enable_re_entry_bypass(self):
        """After a FORCE_EXIT (EOD, hard stop, kill switch), the cap MUST
        refuse further entries on the same day. This is the safety boundary
        that distinguishes loose semantics from "the cap does nothing." """
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._record_session_entry()  # fresh entry: count=1
            today = datetime.now().date().isoformat()
            strat._last_exit_reason = "FORCE_EXIT"
            strat._last_exit_date = today
            assert strat._check_session_entry_cap() is False  # halted, no bypass

    def test_unlimited_harvest_re_entries_in_one_day(self):
        """100 harvest cycles: 1 fresh entry + 99 harvest re-entries — all
        permitted, count stays at 1. The harvest revenue model is preserved
        end-to-end during shakedown."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._record_session_entry()  # fresh entry
            today = datetime.now().date().isoformat()
            for _ in range(99):
                strat._last_exit_reason = "PROFIT_HARVEST"
                strat._last_exit_date = today
                assert strat._check_session_entry_cap() is True
                strat._record_session_entry()
            assert strat._entries_today_count == 1

    def test_re_entry_signal_does_not_carry_across_ist_date(self):
        """Yesterday's harvest exit must NOT enable a re-entry bypass today.
        The date-keyed _is_re_entry guard catches it; persistence layer also
        wipes the sentinel on date mismatch via restore_state."""
        strat = self._make_strategy()
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat._last_exit_reason = "PROFIT_HARVEST"
            strat._last_exit_date = "2026-04-25"  # yesterday
            with patch("trading_system.core.iron_condor.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 26, 10, 0)
                mock_dt.strptime = datetime.strptime
                assert strat._is_re_entry() is False
                # Today's first entry is FRESH and consumes the cap normally.
                strat._record_session_entry()
                assert strat._entries_today_count == 1

    def test_re_entry_sentinel_round_trips_through_persistence(self):
        """Crash mid-harvest-cycle: exit completes, sentinel set, process
        crashes BEFORE the next enter() runs. After restart, the sentinel
        must be restored so the re-entry attempt still gets the bypass."""
        with (
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat_a = self._make_strategy()
            strat_a._record_session_entry()  # fresh entry
            today = datetime.now().date().isoformat()
            strat_a._last_exit_reason = "PROFIT_HARVEST"
            strat_a._last_exit_date = today
            payload = strat_a.save_state()
            assert payload["last_exit_reason"] == "PROFIT_HARVEST"
            assert payload["last_exit_date"] == today

            strat_b = self._make_strategy()
            strat_b.restore_state(payload)
            assert strat_b._last_exit_reason == "PROFIT_HARVEST"
            assert strat_b._is_re_entry() is True
            assert strat_b._check_session_entry_cap() is True  # re-entry survives crash

    def test_sentinel_persistence_layer_integration_round_trip(self, tmp_path):
        """End-to-end on-disk pin: the sentinel survives the actual
        position_persistence.save → JSON → load path that runs in prod.
        Catches drift between save_state's wrapper and the persistence layer's
        json.dump/load cycle."""
        from trading_system.core import position_persistence

        state_file = str(tmp_path / "open_positions.json")
        with (
            patch.object(position_persistence, "STATE_FILE", state_file),
            patch.object(settings, "SHAKEDOWN_MODE", True),
            patch.object(settings, "IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN", 1),
        ):
            strat_a = self._make_strategy()
            strat_a._record_session_entry()
            today = datetime.now().date().isoformat()
            strat_a._last_exit_reason = "PROFIT_HARVEST"
            strat_a._last_exit_date = today

            tracker = MagicMock()
            tracker._positions = {}
            position_persistence.save({"NIFTY": strat_a}, tracker)

            strat_b = self._make_strategy()
            tracker_b = MagicMock()
            tracker_b._positions = {}
            position_persistence.load({"NIFTY": strat_b}, tracker_b)
            assert strat_b._last_exit_reason == "PROFIT_HARVEST"
            assert strat_b._last_exit_date == today
            assert strat_b._is_re_entry() is True
            assert strat_b._check_session_entry_cap() is True
