#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# RegimeTrader — Daily startup script
#
# Usage:
#   ./start.sh              (prompts for 2FA interactively)
#   ./start.sh 123456       (pass 2FA code as argument)
# ─────────────────────────────────────────────────────────

set -euo pipefail
cd "$(dirname "$0")"

# ── Virtual environment ─────────────────────────────────
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# ── Dependencies ────────────────────────────────────────
pip install -q -r requirements.txt
pip install -q flask rich 2>/dev/null || true

# ── Pre-flight checks ──────────────────────────────────
if [ ! -f "cred.yml" ]; then
    echo "ERROR: cred.yml not found. Copy from cred.yml.template and fill in your credentials."
    exit 1
fi

# Detect auth mode from cred.yml.
AUTH_MODE="$(python3 - <<'PY'
import yaml
from pathlib import Path
creds = yaml.safe_load(Path("cred.yml").read_text()) or {}
is_oauth = all(str(creds.get(k, "")).strip() for k in ("oauth_url", "client_id", "Secret_Code", "UID"))
print("oauth" if is_oauth else "legacy")
PY
)"

# ── 2FA code (legacy mode only) ─────────────────────────
if [ "${AUTH_MODE}" = "legacy" ]; then
    if [ -n "${1:-}" ]; then
        export TWOFA="$1"
    elif [ -z "${TWOFA:-}" ]; then
        read -rp "Enter your 2FA code: " TWOFA
        export TWOFA
    fi
fi

echo "────────────────────────────────────────"
echo " RegimeTrader — PAPER MODE"
echo " Dashboard: http://localhost:5050"
echo " Logs:      logs/trading_system_$(date +%Y%m%d).log"
echo "────────────────────────────────────────"
echo ""
echo " Timeline:"
echo "   09:15  Data collection starts"
echo "   10:00  Trade window opens"
echo "   10:30  Day classification locks"
echo "   14:15  Hard close all positions"
echo "   15:30  System shuts down"
echo ""
echo " Press Ctrl+C to stop early."
echo "────────────────────────────────────────"

# ── Run ─────────────────────────────────────────────────
python3 main.py
