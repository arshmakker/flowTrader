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
