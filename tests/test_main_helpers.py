"""tests/test_main_helpers.py — BUG-02 regression for _force_exit_all."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.paper.paper_position_tracker import PaperPositionTracker
from trading_system.core.risk_manager import RiskManager
from trading_system.core.trade_logger import TradeLogger


class FakeMD:
    def get_ltp(self, sym):
        return 100.0


class FakeStrategy:
    def __init__(self, instrument, pnl):
        self.instrument = instrument
        self._pnl = pnl
        self._active = True

    def is_active(self):
        return self._active

    def force_exit(self):
        if not self._active:
            return None
        self._active = False
        return {"instrument": self.instrument, "pnl": self._pnl, "exit_reason": "FORCE_EXIT"}


def _build_pnl(tmpdir):
    tracker = PaperPositionTracker()
    tl = TradeLogger(data_dir=tmpdir)
    return PaperPnLEngine(tracker, FakeMD(), tl, data_dir=tmpdir), tracker


def test_force_exit_all_records_trades():
    """BUG-02 / BUG-21: force_exit results must flow through pnl_engine.record_trade.
    Daily realised P&L is read off the engine, not off the risk manager."""
    pnl, _ = _build_pnl("/tmp/test_bug02_a")
    risk = RiskManager()
    strats = [FakeStrategy("NIFTY", 500.0), FakeStrategy("BANKNIFTY", -200.0)]
    main._force_exit_all(strats, pnl, risk)
    assert pnl.realised_pnl == 300.0
    assert pnl.daily_realised_pnl == 300.0
    assert pnl.total_trades == 2
    assert all(not s.is_active() for s in strats)


def test_force_exit_all_skips_inactive():
    pnl, _ = _build_pnl("/tmp/test_bug02_b")
    risk = RiskManager()
    inactive = FakeStrategy("NIFTY", 999.0)
    inactive._active = False
    main._force_exit_all([inactive], pnl, risk)
    assert pnl.total_trades == 0
    assert pnl.daily_realised_pnl == 0.0


def test_force_exit_all_handles_mix_of_active_and_inactive():
    pnl, _ = _build_pnl("/tmp/test_bug02_c")
    risk = RiskManager()
    s_active = FakeStrategy("NIFTY", 400.0)
    s_inactive = FakeStrategy("BANKNIFTY", 999.0)
    s_inactive._active = False
    main._force_exit_all([s_active, s_inactive], pnl, risk)
    assert pnl.realised_pnl == 400.0
    assert pnl.daily_realised_pnl == 400.0
    assert pnl.total_trades == 1


class StuckStrategy:
    """Mimics an IC strategy that has stuck legs after a failed rollback."""
    def __init__(self, instrument, stuck):
        self.instrument = instrument
        self._last_rollback_stuck_legs = list(stuck)

    def is_active(self):
        return False


def test_drain_rollback_failures_escalates_and_clears():
    """BUG-05: any strategy with stuck legs must escalate via risk.escalate_rollback_failure,
    and its flag must be cleared so we don't double-report."""
    risk = RiskManager()
    stuck = [{"symbol": "NFO|NIFTY19MAR26C22150", "original_side": "SELL",
              "rollback_side": "BUY", "qty": 65, "reason": "sim"}]
    strats = [StuckStrategy("NIFTY", stuck)]
    main._drain_rollback_failures(strats, risk)
    assert risk.halted is True
    assert risk.stop_hit_at is not None
    assert len(risk._rollback_failures) == 1
    assert risk._rollback_failures[0]["instrument"] == "NIFTY"
    # Flag cleared so next cycle doesn't re-escalate.
    assert strats[0]._last_rollback_stuck_legs == []


def test_drain_rollback_failures_noop_when_clean():
    risk = RiskManager()
    strats = [StuckStrategy("NIFTY", [])]
    main._drain_rollback_failures(strats, risk)
    assert risk.halted is False
    assert len(risk._rollback_failures) == 0


# ── _evaluate_stop_checks: log/flatten gating after halt ─────────────────────

def test_evaluate_stop_checks_noop_when_already_halted():
    """Incident 2026-04-28: a Phase-5b halt at 10:49 caused both stop predicates
    to keep returning True for the rest of the loop, re-firing CRITICAL logs
    and _force_exit_all every cycle. The fix gates on risk.halted; this pins
    that subsequent ticks after a halt don't re-emit either log line."""
    import logging
    from unittest.mock import MagicMock
    pnl, _ = _build_pnl("/tmp/test_eval_stop_halted")
    risk = RiskManager()
    risk.halted = True  # pre-set: simulates the second-tick state after a halt
    log = MagicMock(spec=logging.Logger)

    main._evaluate_stop_checks([FakeStrategy("NIFTY", 0.0)], pnl, risk, log)

    log.critical.assert_not_called()


def test_evaluate_stop_checks_flattens_and_logs_on_combined_stop_trigger():
    """When not yet halted and combined-stop fires, the helper must flatten
    active strategies and emit the CRITICAL log line exactly once."""
    import logging
    from unittest.mock import MagicMock
    pnl, _ = _build_pnl("/tmp/test_eval_stop_combined")
    risk = MagicMock(spec=RiskManager)
    risk.halted = False
    risk.check_combined_stop_loss.return_value = True
    risk.check_daily_loss_cap.return_value = False
    log = MagicMock(spec=logging.Logger)
    s = FakeStrategy("NIFTY", 100.0)

    main._evaluate_stop_checks([s], pnl, risk, log)

    assert s.is_active() is False, "force_exit must run when combined-stop trips"
    log.critical.assert_called_once_with("COMBINED STOP LOSS HIT - Trading Halted.")


def test_evaluate_stop_checks_flattens_and_logs_on_daily_cap_trigger():
    """When the daily rupee cap trips (combined-stop clean), the helper must
    flatten and log only the daily-cap line."""
    import logging
    from unittest.mock import MagicMock
    pnl, _ = _build_pnl("/tmp/test_eval_stop_daily")
    risk = MagicMock(spec=RiskManager)
    risk.halted = False
    risk.check_combined_stop_loss.return_value = False
    risk.check_daily_loss_cap.return_value = True
    log = MagicMock(spec=logging.Logger)
    s = FakeStrategy("NIFTY", 0.0)

    main._evaluate_stop_checks([s], pnl, risk, log)

    assert s.is_active() is False
    log.critical.assert_called_once_with("DAILY LOSS CAP HIT - Trading Halted for the session.")


def test_halt_on_exception_sets_halted_without_reraising():
    """BUG-19 / Axiom 3: an unhandled exception must halt trading cleanly."""
    import logging
    risk = RiskManager()
    log = logging.getLogger("test_bug19")
    # Must not raise.
    main._halt_on_exception(RuntimeError("simulated cycle failure"), risk, log)
    assert risk.halted is True


class FakePositionedStrategy:
    """Strategy stub with an active IC_Position-like object, including expiry_date."""
    def __init__(self, instrument, expiry_iso):
        self.instrument = instrument
        class _Pos:
            pass
        pos = _Pos()
        pos.expiry_date = expiry_iso
        self._position = pos

    def is_active(self):
        return self._position is not None


def test_find_expiring_today_matches_expiry():
    """BUG-18: strategies with expiry_date == today must be returned."""
    today_iso = "2026-04-17"
    a = FakePositionedStrategy("NIFTY", today_iso)
    b = FakePositionedStrategy("BANKNIFTY", "2026-04-24")
    result = main._find_expiring_today([a, b], today_iso)
    assert result == [a]


def test_find_expiring_today_skips_inactive():
    today_iso = "2026-04-17"
    inactive = FakePositionedStrategy("NIFTY", today_iso)
    inactive._position = None
    result = main._find_expiring_today([inactive], today_iso)
    assert result == []


def test_find_expiring_today_tolerates_missing_expiry_date():
    """Strategies restored from old state before expiry_date existed should not match and not crash."""
    today_iso = "2026-04-17"
    s = FakePositionedStrategy("NIFTY", "")
    result = main._find_expiring_today([s], today_iso)
    assert result == []


def test_find_past_expiry_matches_strictly_earlier():
    """Position whose expiry is before today needs manual reconciliation on startup —
    regression for the 2026-04-21 NIFTY IC that stranded overnight."""
    today_iso = "2026-04-22"
    past = FakePositionedStrategy("NIFTY", "2026-04-21")
    today_pos = FakePositionedStrategy("NIFTY", "2026-04-22")
    future = FakePositionedStrategy("BANKNIFTY", "2026-04-28")
    result = main._find_past_expiry([past, today_pos, future], today_iso)
    assert result == [past]


def test_find_past_expiry_ignores_missing_expiry_date():
    """Empty expiry_date must not be treated as past (empty string < any ISO date lexicographically)."""
    today_iso = "2026-04-22"
    s = FakePositionedStrategy("NIFTY", "")
    assert main._find_past_expiry([s], today_iso) == []


def test_find_past_expiry_ignores_inactive():
    today_iso = "2026-04-22"
    s = FakePositionedStrategy("NIFTY", "2026-04-21")
    s._position = None  # is_active() → False
    assert main._find_past_expiry([s], today_iso) == []


def test_halt_on_exception_does_not_overwrite_stop_hit_if_already_set():
    """Escalation from a prior rollback failure should not be clobbered by a later exception halt."""
    from datetime import datetime
    risk = RiskManager()
    original = datetime(2026, 4, 19, 10, 30)
    risk.stop_hit_at = original
    risk.halted = True
    import logging
    main._halt_on_exception(RuntimeError("later exception"), risk, logging.getLogger("test_bug19"))
    # halted stays True; stop_hit_at semantics up to _halt_on_exception — currently it doesn't
    # touch stop_hit_at, which is the desired behavior.
    assert risk.halted is True
    assert risk.stop_hit_at == original


# ── LIVE-23: alert emission from main helpers ────────────────────────────────

def test_halt_on_exception_emits_alert_when_channel_provided():
    """LIVE-23: an unhandled cycle exception must surface to the operator alert channel."""
    import logging
    from trading_system.ops.alerts import NullAlertChannel
    alerts = NullAlertChannel()
    risk = RiskManager()
    main._halt_on_exception(
        RuntimeError("simulated"), risk, logging.getLogger("t"), alerts=alerts,
    )
    assert risk.halted is True
    assert len(alerts.sent) == 1
    assert alerts.sent[0].event == "unhandled_exception"
    assert alerts.sent[0].severity == "critical"


def test_halt_on_exception_without_alerts_still_halts():
    """LIVE-23: alerts param is optional; existing callers that don't pass it keep working."""
    import logging
    risk = RiskManager()
    main._halt_on_exception(RuntimeError("simulated"), risk, logging.getLogger("t"))
    assert risk.halted is True  # still halts; alert channel just not notified


def test_check_kill_switch_emits_alert_when_channel_provided(tmp_path, monkeypatch):
    """LIVE-23: halt file detection must surface as a warning alert."""
    import logging
    from unittest.mock import MagicMock, patch
    from trading_system.ops.alerts import NullAlertChannel
    from trading_system.config import settings

    alerts = NullAlertChannel()
    halt_path = str(tmp_path / "HALT")
    open(halt_path, "w").close()
    strats = [MagicMock(is_active=MagicMock(return_value=False))]
    pnl, risk, log = MagicMock(), MagicMock(), logging.getLogger("t")

    with patch.object(settings, "HALT_FILE", halt_path), \
         patch("main._force_exit_all"):
        result = main._check_kill_switch(strats, pnl, risk, log, alerts=alerts)

    assert result is True
    assert len(alerts.sent) == 1
    assert alerts.sent[0].event == "halt_file_detected"
    assert alerts.sent[0].severity == "warning"


# ── LIVE-01: _build_order_stack ──────────────────────────────────────────────
#
# These pin the main.py wiring that selects LiveOrderManager vs PaperOrderManager
# at run-time based on settings.PAPER_TRADE_MODE. The prior shape resolved the
# import-time conditional once against the default (True), so live was
# unreachable from the running process even with the flag flipped.

def test_build_order_stack_paper_mode_uses_paper_order_manager(tmp_path):
    from unittest.mock import MagicMock, patch
    from trading_system.config import settings
    from trading_system.paper.paper_order_manager import PaperOrderManager
    from trading_system.paper.paper_position_tracker import PaperPositionTracker
    from trading_system.paper.paper_pnl_engine import PaperPnLEngine

    api, md, tl = MagicMock(), FakeMD(), MagicMock()
    with patch.object(settings, "PAPER_TRADE_MODE", True), \
         patch.object(settings, "DATA_DIR", str(tmp_path)):
        pos, om, pnl = main._build_order_stack(api, md, tl)

    assert isinstance(om, PaperOrderManager)
    assert isinstance(pos, PaperPositionTracker)
    assert isinstance(pnl, PaperPnLEngine)
    # Tracker threaded into the order manager so fills flow into the same state.
    assert om.tracker is pos
    # Paper mode must not touch the Shoonya API.
    api.assert_not_called()


def test_build_order_stack_live_mode_uses_live_order_manager(tmp_path):
    from unittest.mock import MagicMock, patch
    from trading_system.config import settings
    from trading_system.live.live_order_manager import LiveOrderManager
    from trading_system.paper.paper_position_tracker import PaperPositionTracker
    from trading_system.paper.paper_pnl_engine import PaperPnLEngine

    api, md, tl = MagicMock(), FakeMD(), MagicMock()
    with patch.object(settings, "PAPER_TRADE_MODE", False), \
         patch.object(settings, "DATA_DIR", str(tmp_path)):
        pos, om, pnl = main._build_order_stack(api, md, tl)

    assert isinstance(om, LiveOrderManager)
    # Tracker/PnL containers are mode-agnostic and shared.
    assert isinstance(pos, PaperPositionTracker)
    assert isinstance(pnl, PaperPnLEngine)
    # LiveOrderManager must carry the api handle and the tracker.
    assert om.api is api
    assert om.tracker is pos
    assert om.md is md
