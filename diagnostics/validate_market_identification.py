"""
Diagnostic script to validate if the system is correctly identifying market situations.

This script:
1. Checks current indicator calculations (IV%, ADX, ATR%)
2. Validates regime detection logic
3. Verifies sub-state determination (NEUTRAL_ACTIVE vs NEUTRAL_PASSIVE)
4. Shows what the system sees vs expected behavior
5. Validates data quality and calculation methods
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
import json

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    from api_helper import get_api_instance
    from symbol_manager import SymbolManager
    from technical_indicators import (
        calculate_iv_percentile, calculate_atm_iv, 
        calculate_adx, get_historical_price_data
    )
    from regime.regime_detector import RegimeDetector
    from strategy_runner import build_market_state, get_option_chain_data
    from strategies.iron_condor.position_tracker import IronCondorPositionTracker
except ImportError as e:
    logger.error(f"Import error: {e}")
    logger.error("Make sure you're running from the project root directory")
    sys.exit(1)


def check_indicator_calculations(api, symbol_manager, spot_price, expiry_date) -> Dict:
    """Validate IV%, ADX, and ATR% calculations"""
    results = {
        'iv_percentile': None,
        'adx': None,
        'atr': None,
        'atr_percentile': None,
        'errors': []
    }
    
    logger.info("=" * 80)
    logger.info("STEP 1: VALIDATING INDICATOR CALCULATIONS")
    logger.info("=" * 80)
    
    # 1. Get option chain for IV calculation
    logger.info("\n1.1 Fetching option chain data...")
    option_chain = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50)
    
    if option_chain.empty:
        results['errors'].append("No option chain data available")
        logger.warning("⚠️  No option chain data - cannot calculate IV%")
    else:
        logger.info(f"✓ Option chain loaded: {len(option_chain)} contracts")
        logger.info(f"  Sample strikes: {sorted(option_chain['strike'].unique())[:5]}")
        
        # Calculate IV percentile
        logger.info("\n1.2 Calculating IV Percentile...")
        try:
            days_to_expiry = (expiry_date - datetime.now().date()).days
            current_iv = calculate_atm_iv(option_chain, spot_price, days_to_expiry)
            if current_iv:
                logger.info(f"  Current ATM IV: {current_iv:.2f}%")
            else:
                logger.warning("  ⚠️  Could not calculate current ATM IV")
            
            iv_percentile = calculate_iv_percentile(option_chain, spot_price, days_to_expiry)
            results['iv_percentile'] = iv_percentile
            if iv_percentile is not None:
                logger.info(f"✓ IV Percentile: {iv_percentile:.2f}%")
                logger.info(f"  Interpretation: {'HIGH' if iv_percentile > 70 else 'MEDIUM' if iv_percentile > 40 else 'LOW'} IV environment")
            else:
                logger.warning("  ⚠️  IV Percentile calculation returned None")
                results['errors'].append("IV percentile calculation failed")
        except Exception as e:
            logger.error(f"  ✗ Error calculating IV percentile: {e}")
            results['errors'].append(f"IV percentile error: {str(e)}")
    
    # 2. Calculate ADX
    logger.info("\n1.3 Calculating ADX(14)...")
    try:
        highs, lows, closes = get_historical_price_data(api, symbol_manager, 'Nifty 50', days=30)
        if highs and lows and closes:
            logger.info(f"  Historical data: {len(closes)} days")
            adx = calculate_adx(highs, lows, closes, period=14)
            results['adx'] = adx
            if adx is not None:
                logger.info(f"✓ ADX(14): {adx:.2f}")
                logger.info(f"  Interpretation: {'STRONG TREND' if adx > 25 else 'WEAK TREND' if adx < 20 else 'MODERATE TREND'}")
            else:
                logger.warning("  ⚠️  ADX calculation returned None")
                results['errors'].append("ADX calculation failed")
        else:
            logger.warning("  ⚠️  No historical price data available")
            results['errors'].append("No historical price data for ADX")
    except Exception as e:
        logger.error(f"  ✗ Error calculating ADX: {e}")
        results['errors'].append(f"ADX error: {str(e)}")
    
    # 3. Calculate ATR and ATR%
    logger.info("\n1.4 Calculating ATR and ATR Percentile...")
    try:
        regime_detector = RegimeDetector()
        if highs and lows and closes:
            atr = regime_detector.calculate_atr(highs, lows, closes, period=14)
            results['atr'] = atr
            if atr:
                logger.info(f"✓ ATR(14): {atr:.2f}")
                
                # Calculate ATR percentile
                atr_history = regime_detector.load_atr_history()
                if atr_history and len(atr_history) >= 10:
                    historical_atrs = [h['atr'] for h in atr_history if h.get('atr') and h['atr'] > 0]
                    if len(historical_atrs) >= 10:
                        atr_percentile = regime_detector.calculate_atr_percentile(atr, historical_atrs)
                        results['atr_percentile'] = atr_percentile
                        if atr_percentile is not None:
                            logger.info(f"✓ ATR Percentile: {atr_percentile:.2f}%")
                            logger.info(f"  Historical ATR samples: {len(historical_atrs)}")
                        else:
                            logger.warning("  ⚠️  ATR Percentile calculation returned None")
                    else:
                        logger.warning(f"  ⚠️  Insufficient ATR history: {len(historical_atrs)} samples (need 10+)")
                else:
                    logger.warning(f"  ⚠️  No ATR history available (need 10+ sessions)")
            else:
                logger.warning("  ⚠️  ATR calculation returned None")
        else:
            logger.warning("  ⚠️  No price data for ATR calculation")
    except Exception as e:
        logger.error(f"  ✗ Error calculating ATR: {e}")
        results['errors'].append(f"ATR error: {str(e)}")
    
    return results


def check_regime_detection(api, symbol_manager, indicators: Dict) -> Dict:
    """Validate regime detection logic"""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: VALIDATING REGIME DETECTION")
    logger.info("=" * 80)
    
    results = {
        'detected_regime': None,
        'regime_info': {},
        'errors': []
    }
    
    try:
        # Build market state
        logger.info("\n2.1 Building market state...")
        # We need spot_price and expiry_date - get from current market
        # For now, use a placeholder - in real usage, get from live data
        logger.info("  Note: Using current market data...")
        
        # Create regime detector
        regime_detector = RegimeDetector(confirmation_count=2)
        
        # Build market state dictionary
        market_state = {
            'iv_percentile': indicators.get('iv_percentile'),
            'adx_14': indicators.get('adx'),
            'atr': indicators.get('atr'),
            'atr_percentile': indicators.get('atr_percentile'),
            'range_state': 'NORMAL'  # Default, would be calculated from price range
        }
        
        logger.info(f"  Market state inputs:")
        logger.info(f"    IV%: {market_state['iv_percentile']}")
        logger.info(f"    ADX: {market_state['adx_14']}")
        logger.info(f"    ATR%: {market_state['atr_percentile']}")
        
        # Detect regime
        logger.info("\n2.2 Detecting regime...")
        regime_result = regime_detector.detect_regime(
            market_state, 
            recent_candles=None,
            api=api,
            symbol_manager=symbol_manager
        )
        
        detected_regime = regime_result.get('regime')
        results['detected_regime'] = detected_regime
        results['regime_info'] = regime_result
        
        logger.info(f"✓ Detected Regime: {detected_regime}")
        logger.info(f"  Regime Info: {json.dumps(regime_result, indent=2, default=str)}")
        
        # Validate regime logic
        logger.info("\n2.3 Validating regime logic...")
        iv_pct = indicators.get('iv_percentile')
        adx = indicators.get('adx')
        atr_pct = indicators.get('atr_percentile')
        
        expected_regime = None
        if iv_pct and adx:
            if iv_pct > 70 and adx < 20:
                expected_regime = "INCOME"
            elif iv_pct < 30 and atr_pct and atr_pct < 25:
                expected_regime = "CONVEX"
            else:
                expected_regime = "NEUTRAL"
        
        if expected_regime:
            logger.info(f"  Expected regime (based on thresholds): {expected_regime}")
            if detected_regime == expected_regime:
                logger.info(f"  ✓ Regime matches expected value")
            else:
                logger.warning(f"  ⚠️  Regime mismatch: detected={detected_regime}, expected={expected_regime}")
                logger.info(f"     (This may be due to regime persistence or additional factors)")
        
    except Exception as e:
        logger.error(f"  ✗ Error in regime detection: {e}")
        results['errors'].append(f"Regime detection error: {str(e)}")
    
    return results


def check_sub_state_determination(indicators: Dict, regime: str) -> Dict:
    """Validate NEUTRAL sub-state determination"""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: VALIDATING SUB-STATE DETERMINATION")
    logger.info("=" * 80)
    
    results = {
        'sub_state': None,
        'reasoning': [],
        'errors': []
    }
    
    if regime != "NEUTRAL":
        logger.info(f"  Regime is {regime}, not NEUTRAL - sub-state check skipped")
        return results
    
    logger.info("\n3.1 Determining NEUTRAL sub-state...")
    
    iv_pct = indicators.get('iv_percentile')
    adx = indicators.get('adx')
    atr_pct = indicators.get('atr_percentile')
    
    # Sub-state logic: NEUTRAL_ACTIVE if IV% 40-60 AND ADX 18-25, else NEUTRAL_PASSIVE
    sub_state = "NEUTRAL_PASSIVE"
    reasoning = []
    
    if iv_pct is not None and adx is not None:
        iv_ok = 40 <= iv_pct <= 60
        adx_ok = 18 <= adx <= 25
        
        logger.info(f"  IV% check: {iv_pct:.2f}% in [40, 60]? {iv_ok}")
        logger.info(f"  ADX check: {adx:.2f} in [18, 25]? {adx_ok}")
        
        if iv_ok and adx_ok:
            sub_state = "NEUTRAL_ACTIVE"
            reasoning.append("IV% and ADX within active trading range")
        else:
            if not iv_ok:
                reasoning.append(f"IV% {iv_pct:.2f}% outside [40, 60] range")
            if not adx_ok:
                reasoning.append(f"ADX {adx:.2f} outside [18, 25] range")
    else:
        reasoning.append("Missing indicator values")
    
    results['sub_state'] = sub_state
    results['reasoning'] = reasoning
    
    logger.info(f"✓ Sub-state: {sub_state}")
    for reason in reasoning:
        logger.info(f"  - {reason}")
    
    return results


def check_strategy_routing(regime: str, sub_state: str, indicators: Dict) -> Dict:
    """Validate strategy routing logic"""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: VALIDATING STRATEGY ROUTING")
    logger.info("=" * 80)
    
    results = {
        'strategy_allowed': [],
        'strategy_blocked': [],
        'blocking_reason': None,
        'errors': []
    }
    
    logger.info("\n4.1 Checking strategy eligibility...")
    
    if regime == "INCOME":
        results['strategy_allowed'].append("IRON_CONDOR")
        logger.info("✓ INCOME regime → Iron Condor allowed")
    
    elif regime == "CONVEX":
        results['strategy_allowed'].append("CONVEX_CALL_BACKSPREAD")
        logger.info("✓ CONVEX regime → Convex Call Backspread allowed")
    
    elif regime == "NEUTRAL":
        if sub_state == "NEUTRAL_ACTIVE":
            results['strategy_allowed'].append("ATM_CALL_CALENDAR")
            logger.info("✓ NEUTRAL_ACTIVE → Calendar strategy allowed")
            
            # Check calendar entry conditions
            logger.info("\n4.2 Checking Calendar entry conditions...")
            iv_pct = indicators.get('iv_percentile')
            adx = indicators.get('adx')
            
            conditions_met = []
            conditions_failed = []
            
            if iv_pct is not None:
                if 40 <= iv_pct <= 60:
                    conditions_met.append(f"IV% {iv_pct:.2f}% in [40, 60]")
                else:
                    conditions_failed.append(f"IV% {iv_pct:.2f}% NOT in [40, 60]")
            
            if adx is not None:
                if 18 <= adx <= 25:
                    conditions_met.append(f"ADX {adx:.2f} in [18, 25]")
                else:
                    conditions_failed.append(f"ADX {adx:.2f} NOT in [18, 25]")
            
            if conditions_met:
                logger.info("  ✓ Conditions met:")
                for cond in conditions_met:
                    logger.info(f"    - {cond}")
            
            if conditions_failed:
                logger.info("  ✗ Conditions failed:")
                for cond in conditions_failed:
                    logger.info(f"    - {cond}")
                results['blocking_reason'] = "; ".join(conditions_failed)
        else:
            logger.info("✗ NEUTRAL_PASSIVE → No strategies allowed")
            results['strategy_blocked'].append("All strategies")
            results['blocking_reason'] = "NEUTRAL_PASSIVE sub-state"
    
    return results


def check_active_positions() -> Dict:
    """Check for active positions that might block new trades"""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 5: CHECKING ACTIVE POSITIONS")
    logger.info("=" * 80)
    
    results = {
        'active_positions': [],
        'count': 0
    }
    
    try:
        tracker = IronCondorPositionTracker()
        positions = tracker.get_active_positions()
        results['active_positions'] = positions
        results['count'] = len(positions)
        
        if positions:
            logger.info(f"  ⚠️  {len(positions)} active position(s) found:")
            for pos in positions:
                logger.info(f"    - {pos.get('strategy_type', 'Unknown')} (Entry: {pos.get('entry_time', 'N/A')})")
        else:
            logger.info("  ✓ No active positions")
    except Exception as e:
        logger.error(f"  ✗ Error checking positions: {e}")
    
    return results


def main():
    """Run full diagnostic validation"""
    logger.info("=" * 80)
    logger.info("MARKET IDENTIFICATION VALIDATION")
    logger.info("=" * 80)
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    logger.info("")
    
    try:
        # Initialize API and symbol manager
        logger.info("Initializing API and Symbol Manager...")
        api = get_api_instance()
        symbol_manager = SymbolManager()
        
        # Get current spot price (simplified - in production, get from live data)
        logger.info("\nGetting current market data...")
        # For diagnostic, we'll use a representative spot price
        # In production, this would come from live market data
        spot_price = None
        expiry_date = None
        
        # Try to get from recent log or use a default
        logger.info("  Note: This diagnostic uses current market state")
        logger.info("  For full validation, ensure system is running and collecting data")
        
        # Calculate indicators
        if spot_price and expiry_date:
            indicators = check_indicator_calculations(api, symbol_manager, spot_price, expiry_date)
        else:
            # Use build_market_state to get current state
            logger.info("\nUsing build_market_state to get current indicators...")
            # This requires spot_price and expiry_date - get from strategy_runner logic
            logger.warning("  ⚠️  Cannot get spot_price/expiry without running full system")
            logger.info("  Running partial validation with available data...")
            
            # Try to calculate what we can
            indicators = {'iv_percentile': None, 'adx': None, 'atr': None, 'atr_percentile': None, 'errors': []}
            
            # Calculate ADX if possible
            try:
                highs, lows, closes = get_historical_price_data(api, symbol_manager, 'Nifty 50', days=30)
                if highs and lows and closes:
                    from technical_indicators import calculate_adx
                    adx = calculate_adx(highs, lows, closes, period=14)
                    indicators['adx'] = adx
                    logger.info(f"✓ ADX calculated: {adx:.2f}")
            except Exception as e:
                logger.warning(f"  Could not calculate ADX: {e}")
        
        # Check regime detection
        regime_results = check_regime_detection(api, symbol_manager, indicators)
        detected_regime = regime_results.get('detected_regime')
        
        # Check sub-state
        sub_state_results = check_sub_state_determination(indicators, detected_regime or "NEUTRAL")
        sub_state = sub_state_results.get('sub_state')
        
        # Check strategy routing
        routing_results = check_strategy_routing(detected_regime or "NEUTRAL", sub_state or "NEUTRAL_PASSIVE", indicators)
        
        # Check active positions
        position_results = check_active_positions()
        
        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("VALIDATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Indicators:")
        logger.info(f"  IV%: {indicators.get('iv_percentile', 'N/A')}")
        logger.info(f"  ADX: {indicators.get('adx', 'N/A')}")
        logger.info(f"  ATR%: {indicators.get('atr_percentile', 'N/A')}")
        logger.info(f"\nRegime: {detected_regime}")
        logger.info(f"Sub-state: {sub_state}")
        logger.info(f"\nStrategy Allowed: {routing_results.get('strategy_allowed', [])}")
        if routing_results.get('blocking_reason'):
            logger.info(f"Blocking Reason: {routing_results.get('blocking_reason')}")
        logger.info(f"\nActive Positions: {position_results.get('count', 0)}")
        
        if indicators.get('errors'):
            logger.warning(f"\n⚠️  Errors encountered: {len(indicators['errors'])}")
            for err in indicators['errors']:
                logger.warning(f"  - {err}")
        
    except Exception as e:
        logger.error(f"Fatal error in validation: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
