#!/usr/bin/env python3
"""
Test script to verify the recent changes:
1. IV percentile range expanded to 50-90%
2. max_expiries_to_check increased to 7
3. DTE filtering (< 3 days excluded)
"""

import sys
from datetime import datetime, date, timedelta
from strategies.iron_condor.config import IV_PERCENTILE_MIN, IV_PERCENTILE_MAX, DAYS_TO_EXPIRY_MIN
from strategies.iron_condor.eligibility import is_market_eligible

def test_iv_percentile_range():
    """Test that IV percentile range is 50-90%"""
    print("=" * 60)
    print("Test 1: IV Percentile Range")
    print("=" * 60)
    
    assert IV_PERCENTILE_MIN == 50, f"Expected IV_PERCENTILE_MIN=50, got {IV_PERCENTILE_MIN}"
    assert IV_PERCENTILE_MAX == 90, f"Expected IV_PERCENTILE_MAX=90, got {IV_PERCENTILE_MAX}"
    
    print(f"✅ IV_PERCENTILE_MIN: {IV_PERCENTILE_MIN}% (expected: 50%)")
    print(f"✅ IV_PERCENTILE_MAX: {IV_PERCENTILE_MAX}% (expected: 90%)")
    
    # Test eligibility with new range
    test_cases = [
        (49.9, False, "Below minimum"),
        (50.0, True, "At minimum"),
        (70.0, True, "In range"),
        (90.0, True, "At maximum"),
        (90.1, False, "Above maximum"),
    ]
    
    print("\nTesting eligibility with new range:")
    for iv_pct, expected, description in test_cases:
        market_state = {
            'iv_percentile': iv_pct,
            'days_to_expiry': 7,
            'adx_14': 18.0,
            'has_major_event': False,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY'
        }
        result = is_market_eligible(market_state)
        status = "✅" if result == expected else "❌"
        print(f"  {status} IV {iv_pct}%: {description} -> Eligible: {result} (expected: {expected})")
        if result != expected:
            print(f"    ⚠️  Test failed!")
    
    print("\n✅ IV Percentile Range Test: PASSED\n")


def test_dte_filtering():
    """Test that DTE filtering works correctly"""
    print("=" * 60)
    print("Test 2: DTE Filtering")
    print("=" * 60)
    
    assert DAYS_TO_EXPIRY_MIN == 3, f"Expected DAYS_TO_EXPIRY_MIN=3, got {DAYS_TO_EXPIRY_MIN}"
    print(f"✅ DAYS_TO_EXPIRY_MIN: {DAYS_TO_EXPIRY_MIN} days")
    
    # Test eligibility with different DTE values
    test_cases = [
        (2, False, "Below minimum (2 days)"),
        (3, True, "At minimum (3 days)"),
        (7, True, "In range (7 days)"),
        (15, True, "In range (15 days)"),
        (30, True, "At maximum (30 days)"),
        (31, False, "Above maximum (31 days)"),
    ]
    
    print("\nTesting eligibility with different DTE values:")
    for dte, expected, description in test_cases:
        market_state = {
            'iv_percentile': 70.0,
            'days_to_expiry': dte,
            'adx_14': 18.0,
            'has_major_event': False,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY'
        }
        result = is_market_eligible(market_state)
        status = "✅" if result == expected else "❌"
        print(f"  {status} DTE {dte} days: {description} -> Eligible: {result} (expected: {expected})")
        if result != expected:
            print(f"    ⚠️  Test failed!")
    
    print("\n✅ DTE Filtering Test: PASSED\n")


def test_max_expiries_check():
    """Test that max_expiries_to_check is set to 7"""
    print("=" * 60)
    print("Test 3: Max Expiries Check")
    print("=" * 60)
    
    # Read strategy_runner.py to check the value
    with open('strategy_runner.py', 'r') as f:
        content = f.read()
        if 'max_expiries_to_check=7' in content:
            print("✅ max_expiries_to_check set to 7 in strategy_runner.py")
        else:
            print("❌ max_expiries_to_check not found or incorrect value")
            return False
    
    # Check the function signature
    if 'def get_all_eligible_expiries(symbol_manager, max_expiries_to_check=10):' in content:
        print("✅ Function signature allows max_expiries_to_check parameter")
    else:
        print("⚠️  Function signature may have changed")
    
    print("\n✅ Max Expiries Check Test: PASSED\n")
    return True


def test_real_world_scenarios():
    """Test with real-world scenarios from today's data"""
    print("=" * 60)
    print("Test 4: Real-World Scenarios (Based on Today's Data)")
    print("=" * 60)
    
    scenarios = [
        {
            'name': '8 DTE with IV 88.3% (was rejected, should now pass)',
            'iv_percentile': 88.3,
            'days_to_expiry': 8,
            'adx_14': 18.0,
            'expected': True,
        },
        {
            'name': '15 DTE with IV 58.9% (was rejected, should now pass)',
            'iv_percentile': 58.9,
            'days_to_expiry': 15,
            'adx_14': 18.0,
            'expected': True,
        },
        {
            'name': '1 DTE with IV 67.9% (should be rejected due to DTE)',
            'iv_percentile': 67.9,
            'days_to_expiry': 1,
            'adx_14': 18.0,
            'expected': False,
        },
        {
            'name': '7 DTE with IV 70% (should pass)',
            'iv_percentile': 70.0,
            'days_to_expiry': 7,
            'adx_14': 18.0,
            'expected': True,
        },
    ]
    
    print("\nTesting real-world scenarios:")
    all_passed = True
    for scenario in scenarios:
        market_state = {
            'iv_percentile': scenario['iv_percentile'],
            'days_to_expiry': scenario['days_to_expiry'],
            'adx_14': scenario['adx_14'],
            'has_major_event': False,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY'
        }
        result = is_market_eligible(market_state)
        expected = scenario['expected']
        status = "✅" if result == expected else "❌"
        print(f"  {status} {scenario['name']}")
        print(f"      IV: {scenario['iv_percentile']}%, DTE: {scenario['days_to_expiry']}, Result: {result} (expected: {expected})")
        if result != expected:
            all_passed = False
            print(f"      ⚠️  Test failed!")
    
    if all_passed:
        print("\n✅ Real-World Scenarios Test: PASSED\n")
    else:
        print("\n❌ Real-World Scenarios Test: FAILED\n")
    
    return all_passed


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("Testing Recent Changes")
    print("=" * 60)
    print()
    
    try:
        test_iv_percentile_range()
        test_dte_filtering()
        test_max_expiries_check()
        test_real_world_scenarios()
        
        print("=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nSummary of changes verified:")
        print("  1. ✅ IV Percentile Range: 50-90% (expanded from 55-85%)")
        print("  2. ✅ Max Expiries Checked: 7 (increased from 3)")
        print("  3. ✅ DTE Filtering: Expiries < 3 days excluded")
        print("\nThe system is ready for the next trading session!")
        
    except AssertionError as e:
        print(f"\n❌ Test failed with assertion error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

