#!/usr/bin/env bash
# Run all backtests end-to-end. Requires market_data_YYYYMMDD dirs for the date range.
# Usage: ./run_all_backtests.sh [start_YYYYMMDD] [end_YYYYMMDD]
# Default: 20251222 20260116

set -e
START="${1:-20251222}"
END="${2:-20260116}"
echo "Running all backtests for $START to $END"
echo "=========================================="

echo "[1/6] Regime detection backtest..."
python3 backtest_regime_detection.py "$START" "$END"
echo ""

echo "[2/6] Trend following backtest..."
python3 backtest_trend_following.py
echo ""

echo "[3/6] Convex backspread backtest..."
python3 backtest_convex_backspread.py
echo ""

echo "[4/6] Iron Condor backtest..."
python3 backtest_iron_condor.py
echo ""

echo "[5/6] Neutral calendar backtest..."
python3 backtest_neutral_calendar.py
echo ""

echo "[6/6] Trailing stop comparison backtest..."
python3 backtest_trailing_stop_comparison.py
echo ""

echo "=========================================="
echo "All backtests completed."
