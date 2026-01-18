"""
Synthetic Regime Detection Tests

Validates regime detection logic using synthetic market data.
Tests indicator calculations and regime classification.
"""

import logging
import sys
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagnostics.synthetic_data import SyntheticMarketGenerator
from technical_indicators import calculate_adx, calculate_iv_percentile
from regime import RegimeDetector

logger = logging.getLogger(__name__)


# Configuration flags
ENABLE_SYNTHETIC_TESTS = os.getenv('ENABLE_SYNTHETIC_TESTS', 'False').lower() == 'true'
ENABLE_REPLAY_MODE = os.getenv('ENABLE_REPLAY_MODE', 'False').lower() == 'true'


class SyntheticTestResult:
    """Result of a synthetic regime test"""
    
    def __init__(self, scenario_name: str, expected_regime: str, detected_regime: str,
                 indicators: Dict, passed: bool, reason: str = ""):
        self.scenario_name = scenario_name
        self.expected_regime = expected_regime
        self.detected_regime = detected_regime
        self.indicators = indicators
        self.passed = passed
        self.reason = reason
        self.timestamp = datetime.now().isoformat()


def _calculate_indicators_from_candles(candles: List[Dict], iv_series: List[float]) -> Dict:
    """
    Calculate indicators from synthetic candle data
    
    Args:
        candles: List of candle dictionaries
        iv_series: List of IV values
    
    Returns:
        Dictionary with calculated indicators
    """
    try:
        # Extract price series
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        closes = [c['close'] for c in candles]
        
        # Calculate ADX
        adx = calculate_adx(highs, lows, closes, period=14)
        
        # Calculate IV percentile (use current IV vs historical)
        if iv_series:
            # Handle both dict format (with historical) and list format (backward compat)
            if isinstance(iv_series, dict):
                current_iv_list = iv_series.get('current', [])
                historical_ivs = iv_series.get('historical', [])
            else:
                current_iv_list = iv_series
                historical_ivs = iv_series[:-1] if len(iv_series) > 1 else iv_series
            
            if current_iv_list and historical_ivs:
                current_iv = current_iv_list[-1]
                sorted_ivs = sorted(historical_ivs)
                count_below = sum(1 for iv in sorted_ivs if iv < current_iv)
                iv_percentile = (count_below / len(sorted_ivs)) * 100.0
            elif current_iv_list:
                # Fallback if no historical data
                iv_percentile = 50.0
            else:
                iv_percentile = None
        else:
            iv_percentile = None
        
        # Calculate ATR using RegimeDetector's method
        regime_detector = RegimeDetector()
        atr = regime_detector.calculate_atr(highs, lows, closes, period=14)
        
        # Calculate historical ATRs for percentile
        atr_percentile = None
        if atr and len(highs) >= 15:
            historical_atrs = []
            for i in range(14, len(highs)):
                period_highs = highs[i-14:i+1]
                period_lows = lows[i-14:i+1]
                period_closes = closes[i-14:i+1]
                period_atr = regime_detector.calculate_atr(period_highs, period_lows, period_closes, period=14)
                if period_atr:
                    historical_atrs.append(period_atr)
            
            if historical_atrs:
                # Store ATR history for percentile calculation
                for hist_atr in historical_atrs[-10:]:  # Store last 10 for percentile
                    regime_detector.save_atr_data(hist_atr)
                
                # Now calculate percentile (will use stored history)
                atr_percentile = regime_detector.calculate_atr_percentile(atr, historical_atrs)
        
        # Calculate recent range (last 60 minutes equivalent - use last 4 candles)
        if len(candles) >= 4:
            recent_candles = candles[-4:]
            recent_highs = [c['high'] for c in recent_candles]
            recent_lows = [c['low'] for c in recent_candles]
            last_range = max(recent_highs) - min(recent_lows)
            
            # Rolling average range (last 20 candles)
            if len(candles) >= 20:
                avg_ranges = []
                for i in range(max(0, len(candles) - 20), len(candles)):
                    candle = candles[i]
                    avg_ranges.append(candle['high'] - candle['low'])
                rolling_avg_range = sum(avg_ranges) / len(avg_ranges) if avg_ranges else None
            else:
                # Use all available candles
                avg_ranges = [c['high'] - c['low'] for c in candles]
                rolling_avg_range = sum(avg_ranges) / len(avg_ranges) if avg_ranges else None
        else:
            last_range = None
            rolling_avg_range = None
        
        return {
            'adx': adx,
            'iv_percentile': iv_percentile,
            'atr': atr,
            'atr_percentile': atr_percentile,
            'last_range': last_range,
            'rolling_avg_range': rolling_avg_range,
            'spot_price': closes[-1] if closes else None
        }
    
    except Exception as e:
        logger.error(f"Error calculating indicators: {str(e)}", exc_info=True)
        return {
            'adx': None,
            'iv_percentile': None,
            'atr': None,
            'atr_percentile': None,
            'last_range': None,
            'rolling_avg_range': None,
            'spot_price': None
        }


def _run_single_test(scenario_name: str, synthetic_data: Dict, expected_regime: str) -> SyntheticTestResult:
    """
    Run a single synthetic regime test
    
    Args:
        scenario_name: Name of the test scenario
        synthetic_data: Dictionary with candles, iv_series, expected_regime
        expected_regime: Expected regime for this scenario
    
    Returns:
        SyntheticTestResult object
    """
    try:
        candles = synthetic_data['candles']
        iv_series = synthetic_data['iv_series']
        
        # Calculate indicators
        indicators = _calculate_indicators_from_candles(candles, iv_series)
        
        # Build market state for regime detection
        # Include ATR and range info if calculated
        market_state = {
            'iv_percentile': indicators['iv_percentile'],
            'adx_14': indicators['adx'],
            'spot_price': indicators['spot_price'],
            'days_to_expiry': 7,  # Default for testing
            'has_major_event': False,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY',
            'expiry': datetime.now().strftime('%Y-%m-%d')
        }
        
        # Add ATR info if available (for regime detection)
        if indicators.get('atr') is not None:
            market_state['atr'] = indicators['atr']
        if indicators.get('atr_percentile') is not None:
            market_state['atr_percentile'] = indicators['atr_percentile']
        
        # Convert candles to format expected by RegimeDetector
        recent_candles = []
        for candle in candles[-20:]:  # Last 20 candles
            recent_candles.append({
                'high': candle['high'],
                'low': candle['low'],
                'ltp': candle['close'],
                'timestamp': candle.get('timestamp', datetime.now())
            })
        
        # For ATR calculation, we need to provide price data to RegimeDetector
        # Extract all price data for ATR calculation
        all_highs = [c['high'] for c in candles]
        all_lows = [c['low'] for c in candles]
        all_closes = [c['close'] for c in candles]
        
        # Run regime detection using a monkeypatch of the function RegimeDetector actually calls:
        # `regime.regime_detector.get_historical_price_data`
        class MockAPI:
            pass

        class MockSymbolManager:
            pass

        mock_api = MockAPI()
        mock_symbol_manager = MockSymbolManager()

        # Monkey-patch the imported symbol in `regime/regime_detector.py` module
        import regime.regime_detector as rd_module
        original_get_historical = rd_module.get_historical_price_data

        def mock_get_historical_price_data(api, symbol_manager, symbol_name, days=30):
            # Ignore symbol_name/days; return the synthetic series
            return all_highs, all_lows, all_closes

        rd_module.get_historical_price_data = mock_get_historical_price_data

        try:
            regime_detector = RegimeDetector()
            regime_info = regime_detector.detect_regime(
                market_state,
                recent_candles,
                api=mock_api,
                symbol_manager=mock_symbol_manager
            )
        finally:
            rd_module.get_historical_price_data = original_get_historical
        
        detected_regime = regime_info.get('regime', 'UNKNOWN')
        
        # Determine if test passed
        passed = (detected_regime == expected_regime)
        
        # Build reason if failed (with more detail for debugging)
        reason = ""
        if not passed:
            range_state = regime_info.get('range_state', 'N/A')
            atr_percentile = regime_info.get('atr_percentile', 'N/A')
            
            # For CONVEX failures, add range details
            if expected_regime == "CONVEX" and range_state != "COMPRESSED":
                # Try to get range values for debugging
                last_range = None
                rolling_avg = None
                try:
                    # Calculate what the range detector sees (RegimeDetector already imported)
                    temp_detector = RegimeDetector()
                    last_range = temp_detector.calculate_recent_range(recent_candles, minutes=60)
                    
                    # Calculate rolling average
                    if recent_candles and len(recent_candles) >= 20:
                        recent_ranges = []
                        for c in recent_candles[-20:]:
                            high = c.get('high', c.get('ltp', 0))
                            low = c.get('low', c.get('ltp', 0))
                            if high and low:
                                recent_ranges.append(high - low)
                        if recent_ranges:
                            rolling_avg = sum(recent_ranges) / len(recent_ranges)
                    
                    if last_range and rolling_avg:
                        compression_ratio = last_range / rolling_avg if rolling_avg > 0 else None
                        reason = (
                            f"Expected {expected_regime}, got {detected_regime}. "
                            f"IV%={indicators['iv_percentile']:.1f}, "
                            f"ADX={indicators['adx']:.1f}, "
                            f"ATR%={atr_percentile}, "
                            f"Range={range_state} "
                            f"(last_range={last_range:.1f}, rolling_avg={rolling_avg:.1f}, ratio={compression_ratio:.2f}, need<0.6)"
                        )
                    else:
                        reason = (
                            f"Expected {expected_regime}, got {detected_regime}. "
                            f"IV%={indicators['iv_percentile']:.1f}, "
                            f"ADX={indicators['adx']:.1f}, "
                            f"ATR%={atr_percentile}, "
                            f"Range={range_state} (last_range={last_range}, rolling_avg={rolling_avg})"
                        )
                except Exception as e:
                    reason = (
                        f"Expected {expected_regime}, got {detected_regime}. "
                        f"IV%={indicators['iv_percentile']:.1f}, "
                        f"ADX={indicators['adx']:.1f}, "
                        f"ATR%={atr_percentile}, "
                        f"Range={range_state} (debug error: {str(e)})"
                    )
            else:
                reason = (
                    f"Expected {expected_regime}, got {detected_regime}. "
                    f"IV%={indicators['iv_percentile']:.1f}, "
                    f"ADX={indicators['adx']:.1f}, "
                    f"ATR%={atr_percentile}, "
                    f"Range={range_state}"
                )
        
        # Add full indicator info
        indicators['detected_regime'] = detected_regime
        indicators['detected_regime_info'] = regime_info
        
        return SyntheticTestResult(
            scenario_name=scenario_name,
            expected_regime=expected_regime,
            detected_regime=detected_regime,
            indicators=indicators,
            passed=passed,
            reason=reason
        )
    
    except Exception as e:
        logger.error(f"Error running test {scenario_name}: {str(e)}", exc_info=True)
        return SyntheticTestResult(
            scenario_name=scenario_name,
            expected_regime=expected_regime,
            detected_regime="ERROR",
            indicators={},
            passed=False,
            reason=f"Test execution error: {str(e)}"
        )


def _assert_indicator_sanity(indicators: Dict, scenario_name: str) -> List[str]:
    """
    Assert indicator sanity checks
    
    Args:
        indicators: Dictionary with indicator values
        scenario_name: Name of the scenario
    
    Returns:
        List of error messages (empty if all checks pass)
    """
    errors = []
    
    # Check for NaNs
    for key, value in indicators.items():
        if isinstance(value, float):
            if value != value:  # NaN check
                errors.append(f"{scenario_name}: {key} is NaN")
            elif value < 0 and key not in ['adx']:  # ADX can be 0, but others shouldn't be negative
                if key in ['iv_percentile', 'atr_percentile']:
                    if value < 0:
                        errors.append(f"{scenario_name}: {key} is negative ({value})")
    
    # Check ADX range (0-100)
    if indicators.get('adx') is not None:
        adx = indicators['adx']
        if adx < 0 or adx > 100:
            errors.append(f"{scenario_name}: ADX out of range ({adx})")
    
    # Check IV percentile range (0-100)
    if indicators.get('iv_percentile') is not None:
        iv_pct = indicators['iv_percentile']
        if iv_pct < 0 or iv_pct > 100:
            errors.append(f"{scenario_name}: IV percentile out of range ({iv_pct})")
    
    return errors


def run_synthetic_regime_tests() -> Dict:
    """
    Run all synthetic regime detection tests
    
    Returns:
        Dictionary with test summary:
        {
            "total_cases": int,
            "passed": int,
            "failed": int,
            "results": [SyntheticTestResult, ...]
        }
    """
    logger.info("=" * 60)
    logger.info("Running Synthetic Regime Detection Tests")
    logger.info("=" * 60)
    
    # Reset regime persistence between test runs
    from regime import RegimeDetector
    import os
    import json
    
    RegimeDetector._last_confirmed_regime = None
    RegimeDetector._candidate_regime = None
    RegimeDetector._confirmation_count_current = 0
    
    # Clear ATR history between test runs to avoid cross-test contamination
    atr_history_file = os.path.join('market_data_atr', 'atr_history.json')
    if os.path.exists(atr_history_file):
        try:
            os.remove(atr_history_file)
        except Exception as e:
            logger.debug(f"Could not clear ATR history: {e}")
    
    generator = SyntheticMarketGenerator(seed=42)
    results = []
    
    # CASE 1: SIDEWAYS RANGE (INCOME)
    logger.info("\n[CASE 1] Testing SIDEWAYS RANGE (Expected: INCOME)")
    # Reset persistence before each test
    RegimeDetector._last_confirmed_regime = None
    RegimeDetector._candidate_regime = None
    RegimeDetector._confirmation_count_current = 0
    
    sideways_data = generator.generate_sideways_market(
        length=30,
        base_price=26000.0,
        volatility=0.005,
        iv_level=70.0
    )
    result1 = _run_single_test("SIDEWAYS_RANGE", sideways_data, "INCOME")
    results.append(result1)
    
    # CASE 2: STRONG TREND (NEUTRAL_PASSIVE)
    logger.info("\n[CASE 2] Testing STRONG TREND (Expected: NEUTRAL)")
    # Reset persistence before each test
    RegimeDetector._last_confirmed_regime = None
    RegimeDetector._candidate_regime = None
    RegimeDetector._confirmation_count_current = 0
    
    trending_data = generator.generate_trending_market(
        length=30,
        base_price=26000.0,
        trend_strength=0.002,
        volatility=0.008,
        iv_level=65.0
    )
    result2 = _run_single_test("STRONG_TREND", trending_data, "NEUTRAL")
    results.append(result2)
    
    # CASE 3: VOL COMPRESSION (CONVEX)
    logger.info("\n[CASE 3] Testing VOL COMPRESSION (Expected: CONVEX)")
    # Reset persistence and ATR history before each test
    RegimeDetector._last_confirmed_regime = None
    RegimeDetector._candidate_regime = None
    RegimeDetector._confirmation_count_current = 0
    atr_history_file = os.path.join('market_data_atr', 'atr_history.json')
    if os.path.exists(atr_history_file):
        try:
            os.remove(atr_history_file)
        except Exception:
            pass
    
    compression_data = generator.generate_compression_then_breakout(
        compression_length=25,  # Longer compression for better range calculation
        breakout_length=5,  # Shorter breakout
        base_price=26000.0,
        compression_vol=0.002,  # Lower volatility
        breakout_vol=0.010,
        iv_start=20.0,  # Lower IV
        iv_end=25.0
    )
    result3 = _run_single_test("VOL_COMPRESSION", compression_data, "CONVEX")
    results.append(result3)
    
    # CASE 4: TRANSITION (NEUTRAL)
    logger.info("\n[CASE 4] Testing TRANSITION (Expected: NEUTRAL)")
    # Reset persistence and ATR history before each test (avoid cross-test contamination)
    RegimeDetector._last_confirmed_regime = None
    RegimeDetector._candidate_regime = None
    RegimeDetector._confirmation_count_current = 0
    atr_history_file = os.path.join('market_data_atr', 'atr_history.json')
    if os.path.exists(atr_history_file):
        try:
            os.remove(atr_history_file)
        except Exception:
            pass

    transition_data = generator.generate_transition_market(
        length=30,
        base_price=26000.0,
        volatility=0.006,
        iv_level=50.0
    )
    result4 = _run_single_test("TRANSITION", transition_data, "NEUTRAL")
    results.append(result4)
    
    # Run indicator sanity checks
    logger.info("\n" + "=" * 60)
    logger.info("Running Indicator Sanity Checks")
    logger.info("=" * 60)
    
    all_sanity_errors = []
    for result in results:
        errors = _assert_indicator_sanity(result.indicators, result.scenario_name)
        if errors:
            all_sanity_errors.extend(errors)
            for error in errors:
                logger.error(f"  ❌ {error}")
        else:
            logger.info(f"  ✅ {result.scenario_name}: All indicator sanity checks passed")
    
    # Print test results
    logger.info("\n" + "=" * 60)
    logger.info("Test Results Summary")
    logger.info("=" * 60)
    
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    
    for result in results:
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info(f"\n{status} - {result.scenario_name}")
        logger.info(f"  Expected: {result.expected_regime}")
        logger.info(f"  Detected: {result.detected_regime}")
        logger.info(f"  IV%: {result.indicators.get('iv_percentile', 'N/A'):.1f}")
        logger.info(f"  ADX: {result.indicators.get('adx', 'N/A'):.1f}")
        if not result.passed:
            logger.info(f"  Reason: {result.reason}")
    
    logger.info("\n" + "=" * 60)
    logger.info(f"Total Cases: {len(results)}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    if all_sanity_errors:
        logger.info(f"Sanity Errors: {len(all_sanity_errors)}")
    logger.info("=" * 60)
    
    return {
        "total_cases": len(results),
        "passed": passed,
        "failed": failed,
        "sanity_errors": len(all_sanity_errors),
        "results": results
    }


if __name__ == "__main__":
    # Setup logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run tests
    summary = run_synthetic_regime_tests()
    
    # Exit with error code if tests failed
    if summary['failed'] > 0 or summary['sanity_errors'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)
