"""Tests for DailyTarget — set, is_hit, progress, reset."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.core.daily_target import DailyTarget


def test_initial_state():
    dt = DailyTarget()
    assert dt.target == 0.0
    assert dt.is_hit(999_999) is False  # no target set


def test_set_target():
    dt = DailyTarget()
    dt.set(8000)
    assert dt.target == 8000
    assert dt.is_hit(0) is False


def test_is_hit_below():
    dt = DailyTarget()
    dt.set(8000)
    assert dt.is_hit(7999) is False


def test_is_hit_exact():
    dt = DailyTarget()
    dt.set(8000)
    assert dt.is_hit(8000) is True


def test_is_hit_above():
    dt = DailyTarget()
    dt.set(8000)
    assert dt.is_hit(10_000) is True


def test_is_hit_stays_true():
    dt = DailyTarget()
    dt.set(8000)
    dt.is_hit(9000)
    assert dt.is_hit(1000) is True  # once hit, always hit


def test_progress_string():
    dt = DailyTarget()
    dt.set(8000)
    p = dt.progress(4000)
    assert "4,000" in p
    assert "8,000" in p
    assert "50%" in p


def test_progress_at_zero():
    dt = DailyTarget()
    dt.set(8000)
    p = dt.progress(0)
    assert "0%" in p


def test_progress_exceeds_target():
    dt = DailyTarget()
    dt.set(8000)
    p = dt.progress(12000)
    assert "100%" in p


def test_progress_no_target():
    dt = DailyTarget()
    p = dt.progress(5000)
    assert "0%" in p


def test_reset():
    dt = DailyTarget()
    dt.set(8000)
    dt.is_hit(9000)
    dt.reset()
    assert dt.target == 0.0
    assert dt.is_hit(999_999) is False


def test_save_restore_state():
    dt = DailyTarget()
    dt.set(8000)
    dt.is_hit(9000)
    state = dt.save_state()

    fresh = DailyTarget()
    fresh.restore_state(state)
    assert fresh.target == 8000
    assert fresh.is_hit(0) is True


def test_restore_state_with_hit_reset():
    dt = DailyTarget()
    dt.set(8000)
    dt.is_hit(9000)
    state = dt.save_state()

    fresh = DailyTarget()
    fresh.restore_state(state, reset_hit=True)
    assert fresh.target == 8000
    assert fresh.is_hit(0) is False


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} DailyTarget tests passed")
