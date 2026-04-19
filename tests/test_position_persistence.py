"""
Round-trip test: create position → save → clear → load → verify all fields.
Covers all five strategies (A–E) and the paper position tracker.
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.core.strategy_a import StrategyA, StranglePosition
from trading_system.core.strategy_b import StrategyB, SpreadPosition
from trading_system.core.strategy_c import StrategyC, FuturesPosition
from trading_system.core.strategy_d import StrategyD, IronCondorPosition
from trading_system.core.strategy_e import StrategyE, DeepITMPosition
from trading_system.core.daily_target import DailyTarget
from trading_system.core.risk_manager import RiskManager
from trading_system.core import position_persistence
from trading_system.paper.paper_position_tracker import PaperPositionTracker
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.core.trade_logger import TradeLogger


class FakeMarketData:
    def get_ltp(self, sym):
        return 100.0


class FakeOrderManager:
    def place_order(self, *a, **kw):
        return {"order_id": "FAKE"}
    def build_option_symbol(self, *a, **kw):
        return "NFO|NIFTY13MAR26C24000"


def make_strats():
    om = FakeOrderManager()
    md = FakeMarketData()
    return {
        "A": StrategyA(om, md),
        "B": StrategyB(om, md),
        "C": StrategyC(om, md),
        "D": StrategyD(om, md),
        "E": StrategyE(om, md),
    }


# ── Dataclass round-trip tests ──────────────────────────────────────────

def test_strangle_roundtrip():
    orig = StranglePosition(
        call_strike=24800, put_strike=24200, call_symbol="NFO|CE",
        put_symbol="NFO|PE", premium_received=185.5, lots=2, entry_time="10:15:00",
    )
    d = orig.to_dict()
    restored = StranglePosition.from_dict(d)
    for field in orig.__dataclass_fields__:
        assert getattr(orig, field) == getattr(restored, field), f"A mismatch: {field}"
    print("  [PASS] StranglePosition round-trip")


def test_spread_roundtrip():
    orig = SpreadPosition(
        direction="BULL", buy_strike=24600, sell_strike=24800,
        buy_symbol="NFO|BUY", sell_symbol="NFO|SELL", opt_type="CE",
        debit_paid=42.0, max_profit=158.0, lots=3, entry_time="10:32:00",
    )
    d = orig.to_dict()
    restored = SpreadPosition.from_dict(d)
    for field in orig.__dataclass_fields__:
        assert getattr(orig, field) == getattr(restored, field), f"B mismatch: {field}"
    print("  [PASS] SpreadPosition round-trip")


def test_futures_roundtrip():
    orig = FuturesPosition(
        direction="BULL", fut_symbol="NFO|FUT", entry_price=24550.0,
        target_price=24600.0, stop_price=24520.0, lots=1, entry_time="10:45:00",
    )
    d = orig.to_dict()
    restored = FuturesPosition.from_dict(d)
    for field in orig.__dataclass_fields__:
        assert getattr(orig, field) == getattr(restored, field), f"C mismatch: {field}"
    print("  [PASS] FuturesPosition round-trip")


def test_ironcondor_roundtrip():
    orig = IronCondorPosition(
        short_call=25000, short_put=24200, long_call=25200, long_put=24000,
        sc_sym="NFO|SC", sp_sym="NFO|SP", lc_sym="NFO|LC", lp_sym="NFO|LP",
        net_premium=96.5, lots=2, entry_time="10:32:00",
    )
    d = orig.to_dict()
    restored = IronCondorPosition.from_dict(d)
    for field in orig.__dataclass_fields__:
        assert getattr(orig, field) == getattr(restored, field), f"D mismatch: {field}"
    print("  [PASS] IronCondorPosition round-trip")


def test_deepitm_roundtrip():
    orig = DeepITMPosition(
        direction="UP", option_symbol="NFO|CE", strike=24000.0,
        opt_type="CE", entry_price=620.0, target_price=930.0,
        stop_price=372.0, lots=1, entry_time="10:40:00",
    )
    d = orig.to_dict()
    restored = DeepITMPosition.from_dict(d)
    for field in orig.__dataclass_fields__:
        assert getattr(orig, field) == getattr(restored, field), f"E mismatch: {field}"
    print("  [PASS] DeepITMPosition round-trip")


# ── Full persistence round-trip (save/load via JSON file) ────────────────

def test_full_persistence_roundtrip():
    """Save all five strategies + tracker to disk, reload into fresh objects, compare."""
    # Override state file to a temp location
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_open_positions.json"

    try:
        strats = make_strats()

        # Manually inject positions as if trades were entered
        strats["A"]._position = StranglePosition(
            call_strike=24800, put_strike=24200, call_symbol="NFO|NIFTY13MAR26C24800",
            put_symbol="NFO|NIFTY13MAR26P24200", premium_received=185.5, lots=2,
            entry_time="10:15:00",
        )
        strats["B"]._position = SpreadPosition(
            direction="BULL", buy_strike=24600, sell_strike=24800,
            buy_symbol="NFO|NIFTY13MAR26C24600", sell_symbol="NFO|NIFTY13MAR26C24800",
            opt_type="CE", debit_paid=42.0, max_profit=158.0, lots=3,
            entry_time="10:32:00",
        )
        strats["C"]._position = FuturesPosition(
            direction="BULL", fut_symbol="NFO|NIFTY30MAR26F",
            entry_price=24550.0, target_price=24600.0, stop_price=24520.0,
            lots=1, entry_time="10:45:00",
        )
        strats["D"]._position = IronCondorPosition(
            short_call=25000, short_put=24200, long_call=25200, long_put=24000,
            sc_sym="NFO|NIFTY13MAR26C25000", sp_sym="NFO|NIFTY13MAR26P24200",
            lc_sym="NFO|NIFTY13MAR26C25200", lp_sym="NFO|NIFTY13MAR26P24000",
            net_premium=96.5, lots=2, entry_time="10:32:00",
        )
        strats["E"]._position = DeepITMPosition(
            direction="UP", option_symbol="NFO|NIFTY13MAR26C24000",
            strike=24000.0, opt_type="CE", entry_price=620.0,
            target_price=930.0, stop_price=372.0, lots=1, entry_time="10:40:00",
        )

        # Set up tracker with some positions
        tracker = PaperPositionTracker()
        tracker._positions = {
            "NFO|NIFTY13MAR26C24800": {"symbol": "NFO|NIFTY13MAR26C24800", "qty": -50, "avg_price": 95.0, "side": "SELL", "costs": 12.5},
            "NFO|NIFTY13MAR26P24200": {"symbol": "NFO|NIFTY13MAR26P24200", "qty": -50, "avg_price": 90.5, "side": "SELL", "costs": 11.0},
        }

        # ── SAVE ──
        position_persistence.save(strats, tracker)
        assert os.path.exists(position_persistence.STATE_FILE), "State file not created"

        # Verify JSON is valid and contains all strategies
        with open(position_persistence.STATE_FILE) as f:
            payload = json.load(f)
        assert set(payload["strategies"].keys()) == {"A", "B", "C", "D", "E"}, \
            f"Expected all 5 strategies, got {set(payload['strategies'].keys())}"
        assert len(payload["tracker_positions"]) == 2
        print("  [PASS] Save: all 5 strategies + 2 tracker positions written")

        # ── LOAD into fresh objects ──
        fresh_strats = make_strats()
        fresh_tracker = PaperPositionTracker()

        restored = position_persistence.load(fresh_strats, fresh_tracker)
        assert restored["restored_strategies"] == 5, f"Expected 5 restored, got {restored}"
        assert restored["session_status"] == position_persistence.SESSION_ACTIVE
        print("  [PASS] Load: 5 strategies restored")

        # Verify each strategy's position fields match the originals
        for key in "ABCDE":
            orig_pos = strats[key]._position
            rest_pos = fresh_strats[key]._position
            assert rest_pos is not None, f"Strategy {key}: position is None after restore"
            for field in orig_pos.__dataclass_fields__:
                o = getattr(orig_pos, field)
                r = getattr(rest_pos, field)
                assert o == r, f"Strategy {key} field '{field}': {o!r} != {r!r}"
        print("  [PASS] All strategy fields match after round-trip")

        # Verify tracker positions match
        assert fresh_tracker._positions == tracker._positions, "Tracker positions mismatch"
        print("  [PASS] Tracker positions match after round-trip")

        # Verify is_active() returns True for all restored strategies
        for key in "ABCDE":
            assert fresh_strats[key].is_active(), f"Strategy {key}: not active after restore"
        print("  [PASS] All strategies report is_active()=True after restore")

        # ── CLEAR ──
        position_persistence.clear()
        assert not os.path.exists(position_persistence.STATE_FILE), "State file not deleted"
        print("  [PASS] Clear: state file removed")

        # ── Load after clear → nothing restored ──
        empty_strats = make_strats()
        empty_tracker = PaperPositionTracker()
        cleared = position_persistence.load(empty_strats, empty_tracker)
        assert cleared["restored_strategies"] == 0
        for key in "ABCDE":
            assert not empty_strats[key].is_active()
        print("  [PASS] Load after clear: 0 strategies, all inactive")

    finally:
        position_persistence.STATE_FILE = original_file
        if os.path.exists("/tmp/test_open_positions.json"):
            os.remove("/tmp/test_open_positions.json")
        if os.path.exists("/tmp/test_open_positions.json.tmp"):
            os.remove("/tmp/test_open_positions.json.tmp")


# ── Partial persistence (only some strategies active) ────────────────────

def test_partial_persistence():
    """Only D has a position; A-C, E should remain inactive after restore."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_partial_positions.json"

    try:
        strats = make_strats()
        strats["D"]._position = IronCondorPosition(
            short_call=25000, short_put=24200, long_call=25200, long_put=24000,
            sc_sym="NFO|SC", sp_sym="NFO|SP", lc_sym="NFO|LC", lp_sym="NFO|LP",
            net_premium=96.5, lots=2, entry_time="10:32:00",
        )
        tracker = PaperPositionTracker()

        position_persistence.save(strats, tracker)

        fresh_strats = make_strats()
        fresh_tracker = PaperPositionTracker()
        restored = position_persistence.load(fresh_strats, fresh_tracker)

        assert restored["restored_strategies"] == 1, f"Expected 1, got {restored}"
        assert fresh_strats["D"].is_active()
        for key in "ABCE":
            assert not fresh_strats[key].is_active(), f"{key} should be inactive"
        print("  [PASS] Partial persistence: only D restored, others inactive")

    finally:
        position_persistence.STATE_FILE = original_file
        for f in ("/tmp/test_partial_positions.json", "/tmp/test_partial_positions.json.tmp"):
            if os.path.exists(f):
                os.remove(f)


# ── save_state returns None when no position ─────────────────────────────

def test_save_state_none():
    strats = make_strats()
    for key in "ABCDE":
        assert strats[key].save_state() is None, f"{key}.save_state() should be None"
    print("  [PASS] save_state() returns None when no position (all strategies)")


# ── Error and edge paths ─────────────────────────────────────────────────

def test_load_corrupt_json_returns_zero():
    """Corrupt state file → load() returns 0 and does not crash."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_corrupt_positions.json"
    try:
        with open(position_persistence.STATE_FILE, "w") as f:
            f.write("{ invalid json here")
        strats = make_strats()
        tracker = PaperPositionTracker()
        n = position_persistence.load(strats, tracker)
        assert n["restored_strategies"] == 0
    finally:
        position_persistence.STATE_FILE = original_file
        if os.path.exists("/tmp/test_corrupt_positions.json"):
            os.remove("/tmp/test_corrupt_positions.json")


def test_load_unknown_strategy_key_skipped():
    """Payload with unknown strategy key (e.g. Z) → that key skipped, others restored."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_unknown_strat.json"
    try:
        strats = make_strats()
        strats["A"]._position = StranglePosition(
            call_strike=24800, put_strike=24200,
            call_symbol="NFO|CE", put_symbol="NFO|PE",
            premium_received=100.0, lots=1, entry_time="10:00:00",
        )
        tracker = PaperPositionTracker()
        position_persistence.save(strats, tracker)
        with open(position_persistence.STATE_FILE) as f:
            payload = json.load(f)
        payload["strategies"]["Z"] = {"strategy": "Z", "position": {"x": 1}}
        with open(position_persistence.STATE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        n = position_persistence.load(fresh, fresh_tracker)
        assert n["restored_strategies"] == 1
        assert fresh["A"].is_active()
        assert not fresh["B"].is_active()
    finally:
        position_persistence.STATE_FILE = original_file
        for p in ("/tmp/test_unknown_strat.json", "/tmp/test_unknown_strat.json.tmp"):
            if os.path.exists(p):
                os.remove(p)


def test_clear_when_file_missing_no_error():
    """clear() when state file does not exist does not raise."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/nonexistent_positions_xyz.json"
    try:
        position_persistence.clear()
    finally:
        position_persistence.STATE_FILE = original_file


def test_load_with_pnl_state_but_pnl_engine_none_no_crash():
    """load() when payload has pnl_state but pnl_engine is None does not crash; strategies still restored."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_pnl_none_state.json"
    try:
        payload = {
            "saved_at": "2026-01-01T10:00:00",
            "strategies": {
                "A": {
                    "strategy": "A",
                    "position": {
                        "call_strike": 24800, "put_strike": 24200,
                        "call_symbol": "NFO|CE", "put_symbol": "NFO|PE",
                        "premium_received": 100.0, "lots": 1, "entry_time": "10:00:00",
                    },
                },
            },
            "tracker_positions": {},
            "pnl_state": {"realised_pnl": 1000, "total_trades": 5},
        }
        with open(position_persistence.STATE_FILE, "w") as f:
            json.dump(payload, f)
        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        n = position_persistence.load(fresh, fresh_tracker, pnl_engine=None)
        assert n["restored_strategies"] == 1
        assert fresh["A"].is_active()
    finally:
        position_persistence.STATE_FILE = original_file
        if os.path.exists("/tmp/test_pnl_none_state.json"):
            os.remove("/tmp/test_pnl_none_state.json")


def test_load_malformed_strategy_state_skipped_others_restored():
    """One strategy's state malformed (e.g. position not a dict) → that one skipped, others restored."""
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_malformed_state.json"
    try:
        strats = make_strats()
        strats["A"]._position = StranglePosition(
            call_strike=24800, put_strike=24200,
            call_symbol="NFO|CE", put_symbol="NFO|PE",
            premium_received=100.0, lots=1, entry_time="10:00:00",
        )
        tracker = PaperPositionTracker()
        position_persistence.save(strats, tracker)
        with open(position_persistence.STATE_FILE) as f:
            payload = json.load(f)
        payload["strategies"]["B"] = {"strategy": "B", "position": 123}  # invalid: position must be dict
        with open(position_persistence.STATE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        n = position_persistence.load(fresh, fresh_tracker)
        assert n["restored_strategies"] == 1
        assert fresh["A"].is_active()
        assert not fresh["B"].is_active()
    finally:
        position_persistence.STATE_FILE = original_file
        for p in ("/tmp/test_malformed_state.json", "/tmp/test_malformed_state.json.tmp"):
            if os.path.exists(p):
                os.remove(p)


def test_load_closing_session_restores_positions():
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_closing_positions.json"
    try:
        strats = make_strats()
        strats["A"]._position = StranglePosition(
            call_strike=24800, put_strike=24200,
            call_symbol="NFO|CE", put_symbol="NFO|PE",
            premium_received=100.0, lots=1, entry_time="10:00:00",
        )
        tracker = PaperPositionTracker()
        position_persistence.save(
            strats,
            tracker,
            session_status=position_persistence.SESSION_CLOSING,
            trading_date="2026-03-12",
            shutdown_reason="trade_end_hard_close_started",
        )
        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        meta = position_persistence.load(fresh, fresh_tracker)
        assert meta["restored_strategies"] == 1
        assert meta["session_status"] == position_persistence.SESSION_CLOSING
        assert fresh["A"].is_active()
    finally:
        position_persistence.STATE_FILE = original_file
        for p in ("/tmp/test_closing_positions.json", "/tmp/test_closing_positions.json.tmp"):
            if os.path.exists(p):
                os.remove(p)


def test_load_flat_session_skips_restore():
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_flat_positions.json"
    try:
        strats = make_strats()
        strats["A"]._position = StranglePosition(
            call_strike=24800, put_strike=24200,
            call_symbol="NFO|CE", put_symbol="NFO|PE",
            premium_received=100.0, lots=1, entry_time="10:00:00",
        )
        tracker = PaperPositionTracker()
        position_persistence.save(
            strats,
            tracker,
            session_status=position_persistence.SESSION_FLAT,
            trading_date="2026-03-12",
            shutdown_reason="flat_verified",
            flat_verified_at="2026-03-12T14:15:00",
        )
        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        meta = position_persistence.load(fresh, fresh_tracker)
        assert meta["restored_strategies"] == 0
        assert meta["session_status"] == position_persistence.SESSION_FLAT
        assert not fresh["A"].is_active()
    finally:
        position_persistence.STATE_FILE = original_file
        for p in ("/tmp/test_flat_positions.json", "/tmp/test_flat_positions.json.tmp"):
            if os.path.exists(p):
                os.remove(p)


def test_load_restores_risk_target_and_daily_pnl_state():
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_runtime_state.json"
    try:
        strats = make_strats()
        tracker = PaperPositionTracker()
        trade_logger = TradeLogger(data_dir="/tmp/test_runtime_state_logs")
        pnl = PaperPnLEngine(tracker, FakeMarketData(), trade_logger, data_dir="/tmp/test_runtime_state_logs")
        risk = RiskManager()
        target = DailyTarget()

        pnl.record_trade("A", 5000, {"strategy": "A"})
        pnl.record_trade("B", -1000, {"strategy": "B"})
        risk.update_loss_limit(7500)
        risk.update_pnl(-8000)
        target.set(4000)
        target.is_hit(pnl.daily_realised_pnl)

        position_persistence.save(strats, tracker, pnl, risk, target)

        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        fresh_trade_logger = TradeLogger(data_dir="/tmp/test_runtime_state_logs_2")
        fresh_pnl = PaperPnLEngine(fresh_tracker, FakeMarketData(), fresh_trade_logger, data_dir="/tmp/test_runtime_state_logs_2")
        fresh_risk = RiskManager()
        fresh_target = DailyTarget()

        meta = position_persistence.load(fresh, fresh_tracker, fresh_pnl, fresh_risk, fresh_target)
        assert meta["session_status"] == position_persistence.SESSION_ACTIVE
        assert fresh_pnl.daily_realised_pnl == 4000
        assert fresh_pnl.daily_trades == 2
        assert fresh_risk.daily_pnl == -8000
        assert fresh_risk.halted is True
        assert fresh_risk._daily_loss_limit == 7500
        assert fresh_target.target == 4000
        assert fresh_target.is_hit(0) is True
    finally:
        position_persistence.STATE_FILE = original_file
        for p in (
            "/tmp/test_runtime_state.json",
            "/tmp/test_runtime_state.json.tmp",
        ):
            if os.path.exists(p):
                os.remove(p)
        import shutil
        for p in ("/tmp/test_runtime_state_logs", "/tmp/test_runtime_state_logs_2"):
            if os.path.isdir(p):
                shutil.rmtree(p)


def test_load_stale_trading_day_resets_daily_runtime_state():
    original_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_stale_runtime_state.json"
    try:
        strats = make_strats()
        strats["D"]._position = IronCondorPosition(
            short_call=25000, short_put=24200, long_call=25200, long_put=24000,
            sc_sym="NFO|SC", sp_sym="NFO|SP", lc_sym="NFO|LC", lp_sym="NFO|LP",
            net_premium=96.5, lots=2, entry_time="10:32:00",
        )
        tracker = PaperPositionTracker()
        tracker._positions = {
            "NFO|SC": {"symbol": "NFO|SC", "qty": -50, "avg_price": 95.0, "side": "SELL", "costs": 12.5},
        }
        trade_logger = TradeLogger(data_dir="/tmp/test_stale_runtime_state_logs")
        pnl = PaperPnLEngine(tracker, FakeMarketData(), trade_logger, data_dir="/tmp/test_stale_runtime_state_logs")
        risk = RiskManager()
        target = DailyTarget()

        pnl.record_trade("A", 5000, {"strategy": "A"})
        pnl.record_trade("B", -1000, {"strategy": "B"})
        risk.update_loss_limit(7500)
        risk.update_pnl(-8000)
        target.set(4000)
        target.is_hit(pnl.daily_realised_pnl)

        position_persistence.save(
            strats,
            tracker,
            pnl,
            risk,
            target,
            trading_date=(datetime.now().date() - timedelta(days=1)).isoformat(),
        )

        fresh = make_strats()
        fresh_tracker = PaperPositionTracker()
        fresh_trade_logger = TradeLogger(data_dir="/tmp/test_stale_runtime_state_logs_2")
        fresh_pnl = PaperPnLEngine(fresh_tracker, FakeMarketData(), fresh_trade_logger, data_dir="/tmp/test_stale_runtime_state_logs_2")
        fresh_risk = RiskManager()
        fresh_target = DailyTarget()

        meta = position_persistence.load(fresh, fresh_tracker, fresh_pnl, fresh_risk, fresh_target)
        assert meta["session_status"] == position_persistence.SESSION_ACTIVE
        assert meta["is_stale_trading_day"] is True
        assert meta["trading_date"] == (datetime.now().date() - timedelta(days=1)).isoformat()
        assert fresh["D"].is_active()
        assert fresh_tracker._positions == tracker._positions
        assert fresh_pnl.realised_pnl == 4000
        assert fresh_pnl.total_trades == 2
        assert fresh_pnl.daily_realised_pnl == 0.0
        assert fresh_pnl.daily_trades == 0
        assert fresh_risk.monthly_pnl == -8000
        assert fresh_risk.daily_pnl == 0.0
        assert fresh_risk.trades_today == 0
        assert fresh_risk.halted is False
        assert fresh_target.target == 4000
        assert fresh_target.is_hit(0) is False
    finally:
        position_persistence.STATE_FILE = original_file
        for p in (
            "/tmp/test_stale_runtime_state.json",
            "/tmp/test_stale_runtime_state.json.tmp",
        ):
            if os.path.exists(p):
                os.remove(p)
        import shutil
        for p in ("/tmp/test_stale_runtime_state_logs", "/tmp/test_stale_runtime_state_logs_2"):
            if os.path.isdir(p):
                shutil.rmtree(p)


def test_is_flat_false_when_tracker_has_positions():
    strats = make_strats()
    tracker = PaperPositionTracker()
    tracker._positions = {"NFO|CE": {"qty": 50}}
    assert not position_persistence.is_flat(strats, tracker)


if __name__ == "__main__":
    print("=== Dataclass round-trip tests ===")
    test_strangle_roundtrip()
    test_spread_roundtrip()
    test_futures_roundtrip()
    test_ironcondor_roundtrip()
    test_deepitm_roundtrip()

    print("\n=== save_state None tests ===")
    test_save_state_none()

    print("\n=== Full persistence round-trip (all 5 strategies) ===")
    test_full_persistence_roundtrip()

    print("\n=== Partial persistence (only Strategy D) ===")
    test_partial_persistence()

    print("\n✅ ALL TESTS PASSED")
