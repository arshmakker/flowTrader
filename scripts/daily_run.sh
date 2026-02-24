#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Running EOD finalization..."
python3 scripts/finalize_trades.py
echo "Generating simulated ledger..."
python3 scripts/generate_simulated_ledger.py
echo "Calculating P&L..."
python3 calculate_pnl.py
echo "EOD run complete."

