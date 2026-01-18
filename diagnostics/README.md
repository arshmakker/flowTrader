# Diagnostics Module

Synthetic data generation and regime validation tests for the trading system.

## Overview

This module provides:
- **Synthetic Market Data Generator**: Creates deterministic OHLC and IV data
- **Regime Validation Tests**: Tests regime detection logic on synthetic scenarios
- **Indicator Sanity Checks**: Validates indicator calculations

## Usage

### Standalone Execution

```bash
python3 diagnostics/synthetic_regime_tests.py
```

### Integration with Main System

Set environment variables to enable tests at startup:

```bash
export ENABLE_SYNTHETIC_TESTS=true
export ENABLE_REPLAY_MODE=true
python3 main.py
```

Or set both to enable tests:

```bash
ENABLE_SYNTHETIC_TESTS=true ENABLE_REPLAY_MODE=true python3 main.py
```

## Test Scenarios

### CASE 1: SIDEWAYS RANGE (Expected: INCOME)
- Flat price movement
- Low ADX (< 15)
- Stable ATR
- High IV (> 65)

### CASE 2: STRONG TREND (Expected: NEUTRAL)
- Monotonic price move
- ADX > 35
- Rising ATR
- High IV (> 60)

### CASE 3: VOL COMPRESSION (Expected: CONVEX)
- Tight range
- ATR percentile < 25
- IV < 35

### CASE 4: TRANSITION (Expected: NEUTRAL)
- ADX 20-25
- ATR ambiguous
- IV 45-55

## Understanding Test Results

### Pass/Fail Criteria

Tests pass when:
- Detected regime matches expected regime
- All indicator sanity checks pass

Tests fail when:
- Detected regime differs from expected
- Indicator values are outside expected ranges
- Calculation errors occur

### Interpreting Failures

Test failures are **diagnostic**, not necessarily bugs:

1. **Synthetic data may not perfectly match real market characteristics**
   - Real markets are complex; synthetic data is simplified
   - Failures help identify edge cases

2. **Regime detection is probabilistic**
   - Multiple factors determine regime (IV%, ADX, ATR%, range)
   - Small changes in synthetic data can change regime

3. **Indicator calculations depend on data quality**
   - ADX requires sufficient data points
   - ATR percentile needs historical ATR values

### Indicator Sanity Checks

All tests verify:
- No NaN values in indicators
- ADX in range [0, 100]
- IV percentile in range [0, 100]
- No negative values (except where allowed)

## Files

- `synthetic_data.py`: SyntheticMarketGenerator class
- `synthetic_regime_tests.py`: Test harness and execution
- `__init__.py`: Module exports

## Deterministic Output

All synthetic data uses fixed random seed (42) for reproducibility.
Same inputs always produce same outputs.

## Notes

- Tests do NOT modify production logic
- Tests do NOT change thresholds
- Tests are diagnostic tools, not CI tests
- System continues execution even if tests fail
