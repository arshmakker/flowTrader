"""
Quick diagnostic to check current market state identification.

Reads from logs and validates:
1. Current indicator values (IV%, ADX, ATR%)
2. Detected regime and sub-state
3. Strategy routing decisions
4. Why trades are/aren't being placed
"""

import sys
import os
import re
import json
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_latest_market_state(log_file: str) -> dict:
    """Extract latest market state from log file"""
    state = {
        'iv_percentile': None,
        'adx': None,
        'atr_percentile': None,
        'regime': None,
        'sub_state': None,
        'strategy_allowed': [],
        'strategy_executed': None,
        'no_trade_reason': None,
        'timestamp': None
    }
    
    if not os.path.exists(log_file):
        logger.warning(f"Log file not found: {log_file}")
        return state
    
    with open(log_file, 'r') as f:
        lines = f.readlines()
    
    # Read last 10000 lines for recent activity
    recent_lines = lines[-10000:] if len(lines) > 10000 else lines
    
    # Extract market state info
    for line in reversed(recent_lines):
        # Market state line
        if 'Market state:' in line:
            match = re.search(r'IV=([\d.]+)%, DTE=(\d+), ADX=([\d.]+)', line)
            if match:
                state['iv_percentile'] = float(match.group(1))
                state['adx'] = float(match.group(3))  # ADX is group 3, not group 2 (DTE is group 2)
                # Extract timestamp
                ts_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                if ts_match:
                    state['timestamp'] = ts_match.group(1)
            break
    
    # Extract regime and additional indicators
    for line in reversed(recent_lines):
        if 'Regime detected:' in line or 'Regime:' in line:
            match = re.search(r'Regime[:\s]+(\w+)', line, re.IGNORECASE)
            if match:
                state['regime'] = match.group(1).upper()
            # Also try to get IV%, ADX, ATR% from regime line
            iv_match = re.search(r'IV=([\d.]+)%', line)
            adx_match = re.search(r'ADX=([\d.]+)', line)
            atr_match = re.search(r'ATR%=([\d.]+)', line)
            if iv_match and not state['iv_percentile']:
                state['iv_percentile'] = float(iv_match.group(1))
            if adx_match and not state['adx']:
                state['adx'] = float(adx_match.group(1))
            if atr_match and not state['atr_percentile']:
                state['atr_percentile'] = float(atr_match.group(1))
            break
    
    # Fallback: Try to extract ADX from "Calculated ADX" lines if still missing
    if not state['adx']:
        for line in reversed(recent_lines):
            if 'Calculated ADX' in line:
                match = re.search(r'Calculated ADX[^:]*:\s*([\d.]+)', line)
                if match:
                    state['adx'] = float(match.group(1))
                    break
    
    # Fallback: Try to extract IV% from IV percentile lines if still missing
    if not state['iv_percentile']:
        for line in reversed(recent_lines):
            if 'IV Percentile' in line or 'iv_percentile' in line.lower():
                match = re.search(r'IV[_\s]*Percentile[:\s]+([\d.]+)', line, re.IGNORECASE)
                if match:
                    state['iv_percentile'] = float(match.group(1))
                    break
    
    # Extract from strategy decisions JSON
    decisions_file = project_root / 'strategy_decisions.json'
    if decisions_file.exists():
        try:
            with open(decisions_file, 'r') as f:
                decisions = json.load(f)
                if decisions and isinstance(decisions, list):
                    latest = decisions[-1]
                    state['regime'] = latest.get('regime') or state['regime']
                    state['sub_state'] = latest.get('sub_state')
                    state['strategy_allowed'] = latest.get('strategy_allowed', [])
                    state['strategy_executed'] = latest.get('strategy_executed')
                    state['no_trade_reason'] = latest.get('no_trade_reason')
                    state['timestamp'] = latest.get('timestamp', state['timestamp'])
                    
                    # Extract indicators from regime_details if available
                    regime_details = latest.get('regime_details', {})
                    if regime_details:
                        if not state['iv_percentile'] and regime_details.get('iv_percentile'):
                            state['iv_percentile'] = regime_details.get('iv_percentile')
                        if not state['adx'] and regime_details.get('adx'):
                            state['adx'] = regime_details.get('adx')
                        if not state['atr_percentile'] and regime_details.get('atr_percentile'):
                            state['atr_percentile'] = regime_details.get('atr_percentile')
        except Exception as e:
            logger.debug(f"Could not read strategy_decisions.json: {e}")
    
    return state


def validate_indicator_ranges(state: dict) -> dict:
    """Validate if indicators are in expected ranges"""
    results = {
        'iv_check': None,
        'adx_check': None,
        'atr_check': None,
        'all_valid': False
    }
    
    iv = state.get('iv_percentile')
    adx = state.get('adx')
    atr = state.get('atr_percentile')
    
    if iv is not None:
        if 40 <= iv <= 60:
            results['iv_check'] = 'PASS (40-60: Calendar eligible)'
        elif iv > 70:
            results['iv_check'] = 'HIGH (>70: Income eligible)'
        elif iv < 30:
            results['iv_check'] = 'LOW (<30: Convex eligible)'
        else:
            results['iv_check'] = 'OUT_OF_RANGE'
    else:
        results['iv_check'] = 'MISSING'
    
    if adx is not None:
        if 18 <= adx <= 25:
            results['adx_check'] = 'PASS (18-25: Calendar eligible)'
        elif adx < 20:
            results['adx_check'] = 'LOW (<20: Weak trend)'
        elif adx > 25:
            results['adx_check'] = 'HIGH (>25: Strong trend, blocks calendar)'
        else:
            results['adx_check'] = 'OUT_OF_RANGE'
    else:
        results['adx_check'] = 'MISSING'
    
    if atr is not None:
        if atr < 25:
            results['atr_check'] = 'LOW (<25: Compressed, Convex eligible)'
        else:
            results['atr_check'] = 'NORMAL'
    else:
        results['atr_check'] = 'MISSING'
    
    return results


def explain_regime_logic(state: dict) -> str:
    """Explain why the detected regime makes sense"""
    iv = state.get('iv_percentile')
    adx = state.get('adx')
    atr = state.get('atr_percentile')
    regime = state.get('regime')
    
    explanation = []
    
    if regime == "INCOME":
        if iv and iv > 70:
            explanation.append(f"✓ IV% {iv:.1f}% > 70 (high IV environment)")
        if adx and adx < 20:
            explanation.append(f"✓ ADX {adx:.1f} < 20 (weak trend)")
        explanation.append("→ INCOME regime: Suitable for Iron Condor")
    
    elif regime == "CONVEX":
        if iv and iv < 30:
            explanation.append(f"✓ IV% {iv:.1f}% < 30 (low IV environment)")
        if atr and atr < 25:
            explanation.append(f"✓ ATR% {atr:.1f}% < 25 (compressed volatility)")
        explanation.append("→ CONVEX regime: Suitable for Convex Call Backspread")
    
    elif regime == "NEUTRAL":
        explanation.append("→ NEUTRAL regime: Transitional state")
        if iv and not (iv > 70 and adx and adx < 20):
            explanation.append(f"  - IV% {iv:.1f}% not in high-IV + low-ADX range for INCOME")
        if iv and not (iv < 30 and atr and atr < 25):
            explanation.append(f"  - IV% {iv:.1f}% not in low-IV + compressed-ATR range for CONVEX")
    
    return "\n".join(explanation) if explanation else "No explanation available"


def explain_sub_state(state: dict) -> str:
    """Explain sub-state determination"""
    sub_state = state.get('sub_state')
    iv = state.get('iv_percentile')
    adx = state.get('adx')
    
    if sub_state == "NEUTRAL_ACTIVE":
        explanation = ["NEUTRAL_ACTIVE: Calendar strategy allowed"]
        if iv and 40 <= iv <= 60:
            explanation.append(f"  ✓ IV% {iv:.1f}% in [40, 60]")
        else:
            explanation.append(f"  ✗ IV% {iv:.1f}% NOT in [40, 60]" if iv else "  ✗ IV% missing")
        
        if adx and 18 <= adx <= 25:
            explanation.append(f"  ✓ ADX {adx:.1f} in [18, 25]")
        else:
            explanation.append(f"  ✗ ADX {adx:.1f} NOT in [18, 25]" if adx else "  ✗ ADX missing")
    
    elif sub_state == "NEUTRAL_PASSIVE":
        explanation = ["NEUTRAL_PASSIVE: No strategies allowed (standing aside)"]
        if iv:
            if iv < 40:
                explanation.append(f"  - IV% {iv:.1f}% < 40 (too low)")
            elif iv > 60:
                explanation.append(f"  - IV% {iv:.1f}% > 60 (too high)")
        if adx:
            if adx < 18:
                explanation.append(f"  - ADX {adx:.1f} < 18 (too low)")
            elif adx > 25:
                explanation.append(f"  - ADX {adx:.1f} > 25 (too high)")
    else:
        explanation = [f"Sub-state: {sub_state or 'Unknown'}"]
    
    return "\n".join(explanation)


def explain_strategy_routing(state: dict) -> str:
    """Explain why strategies are/aren't being routed"""
    strategy_allowed = state.get('strategy_allowed', [])
    strategy_executed = state.get('strategy_executed')
    no_trade_reason = state.get('no_trade_reason')
    
    explanation = []
    
    if strategy_allowed:
        explanation.append(f"Strategies Allowed: {', '.join(strategy_allowed)}")
    else:
        explanation.append("No strategies allowed")
    
    if strategy_executed:
        explanation.append(f"✓ Strategy Executed: {strategy_executed}")
    else:
        explanation.append("✗ No strategy executed")
        if no_trade_reason:
            explanation.append(f"  Reason: {no_trade_reason}")
            
            # Detailed reason explanation
            if no_trade_reason == "NO_VALID_TRADE":
                explanation.append("    → Strategy conditions met, but no valid trade found")
                explanation.append("    → Check: Option chain availability, strike selection, risk limits")
            elif no_trade_reason == "MUTUAL_EXCLUSION":
                explanation.append("    → Another strategy is already active")
            elif no_trade_reason == "HIGH_IV_HIGH_ADX":
                explanation.append("    → High IV + High ADX: No-trade zone")
            elif no_trade_reason == "TRANSITION_PHASE":
                explanation.append("    → Market in transition: Standing aside")
    
    return "\n".join(explanation)


def main():
    """Run diagnostic check"""
    print("=" * 80)
    print("CURRENT MARKET STATE VALIDATION")
    print("=" * 80)
    print()
    
    # Find today's log file
    today = datetime.now().strftime('%Y%m%d')
    log_file = project_root / 'logs' / f'trading_system_{today}.log'
    
    if not log_file.exists():
        print(f"⚠️  Today's log file not found: {log_file}")
        print("   System may not be running today")
        return 1
    
    print(f"Reading from: {log_file}")
    print(f"Log size: {log_file.stat().st_size / 1024 / 1024:.1f} MB")
    print()
    
    # Extract current state
    state = extract_latest_market_state(str(log_file))
    
    print("=" * 80)
    print("CURRENT MARKET STATE")
    print("=" * 80)
    print(f"Timestamp: {state.get('timestamp', 'N/A')}")
    print(f"IV Percentile: {state.get('iv_percentile', 'N/A')}%")
    print(f"ADX: {state.get('adx', 'N/A')}")
    print(f"ATR Percentile: {state.get('atr_percentile', 'N/A')}%")
    print(f"Regime: {state.get('regime', 'N/A')}")
    print(f"Sub-state: {state.get('sub_state', 'N/A')}")
    print()
    
    # Validate indicator ranges
    print("=" * 80)
    print("INDICATOR VALIDATION")
    print("=" * 80)
    validation = validate_indicator_ranges(state)
    print(f"IV% Check: {validation['iv_check']}")
    print(f"ADX Check: {validation['adx_check']}")
    print(f"ATR% Check: {validation['atr_check']}")
    print()
    
    # Explain regime logic
    print("=" * 80)
    print("REGIME DETECTION EXPLANATION")
    print("=" * 80)
    print(explain_regime_logic(state))
    print()
    
    # Explain sub-state
    if state.get('regime') == "NEUTRAL":
        print("=" * 80)
        print("SUB-STATE EXPLANATION")
        print("=" * 80)
        print(explain_sub_state(state))
        print()
    
    # Explain strategy routing
    print("=" * 80)
    print("STRATEGY ROUTING EXPLANATION")
    print("=" * 80)
    print(explain_strategy_routing(state))
    print()
    
    # Check for calendar rejection reasons
    if "ATM_CALL_CALENDAR" in state.get('strategy_allowed', []):
        print("=" * 80)
        print("CALENDAR STRATEGY DIAGNOSIS")
        print("=" * 80)
        
        # Check log for calendar rejection reasons
        with open(log_file, 'r') as f:
            lines = f.readlines()
            recent = lines[-5000:] if len(lines) > 5000 else lines
            
            calendar_rejections = []
            for line in reversed(recent):
                if 'Calendar rejected' in line:
                    calendar_rejections.append(line.strip())
                    if len(calendar_rejections) >= 3:
                        break
            
            if calendar_rejections:
                print("Recent Calendar Rejection Reasons:")
                for i, reason in enumerate(calendar_rejections, 1):
                    print(f"  {i}. {reason[:150]}")
            else:
                print("No recent calendar rejections found in log")
        print()
    
    # Summary
    print("=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    
    issues = []
    if state.get('iv_percentile') is None:
        issues.append("IV% not available")
    if state.get('adx') is None:
        issues.append("ADX not available")
    if state.get('regime') is None:
        issues.append("Regime not detected")
    
    if issues:
        print("⚠️  Issues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✓ All key indicators available")
    
    if state.get('strategy_executed'):
        print(f"✓ Strategy executed: {state['strategy_executed']}")
    elif state.get('no_trade_reason'):
        print(f"ℹ️  No trade: {state['no_trade_reason']}")
    
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
