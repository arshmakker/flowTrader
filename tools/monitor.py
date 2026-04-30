#!/usr/bin/env python3
"""
Continuous monitor for RegimeTrader main.py
Shows: latest log entries, P&L snapshot, open positions, and issues
"""
import time
import os
import json
import sys
from datetime import datetime

LOG_DIR = "logs"
DATA_DIR = "data"
SNAPSHOT_FILE = os.path.join(DATA_DIR, "pnl_snapshot.json")
POSITION_FILE = os.path.join(DATA_DIR, "positions.json")

def get_latest_log():
    """Get the most recent log file."""
    try:
        files = os.listdir(LOG_DIR)
        log_files = [f for f in files if f.startswith("ic_system_") and f.endswith(".log")]
        if not log_files:
            return None
        latest = sorted(log_files)[-1]
        return os.path.join(LOG_DIR, latest)
    except:
        return None

def read_last_lines(filepath, n=20):
    """Read last n lines of a file efficiently."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(0, 2)
            file_size = f.tell()
            block_size = 1024
            data = b''
            while len(data) < file_size and len(data.split(b'\n')) < n + 1:
                seek_pos = max(0, file_size - len(data) - block_size)
                f.seek(seek_pos)
                data = f.read(file_size - seek_pos) + data
            lines = data.decode('utf-8', errors='ignore').split('\n')
            return [l for l in lines if l.strip()][-n:]
    except:
        return []

def read_snapshot():
    """Read P&L snapshot."""
    try:
        with open(SNAPSHOT_FILE) as f:
            return json.load(f)
    except:
        return None

def read_positions():
    """Read position file for open positions."""
    try:
        with open(POSITION_FILE) as f:
            return json.load(f)
    except:
        return None

def check_process():
    """Check if main.py is running."""
    try:
        import subprocess
        result = subprocess.run(['pgrep', '-f', 'python.*main.py'], capture_output=True, text=True)
        return result.stdout.strip() != ''
    except:
        return False

def extract_issues(log_lines):
    """Extract issues from recent log lines."""
    issues = {
        'rejections': [],
        'warnings': [],
        'errors': [],
        'trades': []
    }
    for line in log_lines:
        if 'WARNING' in line or 'refusing entry' in line.lower():
            issues['rejections'].append(line)
        elif 'ERROR' in line:
            issues['errors'].append(line)
        elif 'Trade closed' in line or 'harvest' in line.lower():
            issues['trades'].append(line)
    return issues

def format_pnl(snapshot):
    """Format P&L for display."""
    if not snapshot:
        return "No P&L data"
    total = snapshot.get('total_pnl', 0)
    realised = snapshot.get('realised_pnl', 0)
    unrealised = snapshot.get('unrealised_pnl', 0)
    trades = snapshot.get('total_trades', 0)
    wr = snapshot.get('win_rate_pct', 0)
    return f"Total: ₹{total:.2f} | Realised: ₹{realised:.2f} | Unrealised: ₹{unrealised:.2f} | Trades: {trades} | WR: {wr:.1f}%"

def main():
    print("RegimeTrader Monitor — Press Ctrl+C to stop\n")
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')

        print(f"{'='*80}")
        print(f"RegimeTrader Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}\n")

        # Process status
        running = check_process()
        status = "✓ RUNNING" if running else "✗ STOPPED"
        print(f"Process Status: {status}\n")

        # P&L Snapshot
        print("P&L SNAPSHOT:")
        snapshot = read_snapshot()
        if snapshot:
            print(f"  {format_pnl(snapshot)}")
            daily = snapshot.get('daily', {})
            if daily:
                print(f"  Daily: ₹{daily.get('realised_pnl', 0):.2f} | Trades: {daily.get('trades', 0)}")
        else:
            print("  No snapshot available")
        print()

        # Open Positions
        print("OPEN POSITIONS:")
        positions = read_positions()
        if positions and 'strategies' in positions:
            for inst, data in positions['strategies'].items():
                if data.get('active'):
                    pos = data.get('position', {})
                    print(f"  {inst}: Active | Expiry: {pos.get('expiry_date', 'N/A')}")
                else:
                    print(f"  {inst}: Flat")
        else:
            print("  No open positions")
        print()

        # Latest Log Entries
        print("LATEST LOG ENTRIES (last 15):")
        latest_log = get_latest_log()
        if latest_log:
            lines = read_last_lines(latest_log, 15)
            for line in lines:
                # Truncate long lines
                if len(line) > 100:
                    line = line[:97] + "..."
                print(f"  {line}")
        else:
            print("  No log file found")
        print()

        # Issues
        print("RECENT ISSUES:")
        if latest_log:
            lines = read_last_lines(latest_log, 50)
            issues = extract_issues(lines)
            if issues['errors']:
                print("  ERRORS:")
                for e in issues['errors'][-3:]:
                    print(f"    {e}")
            if issues['rejections']:
                print(f"  REJECTIONS (last 3 of {len(issues['rejections'])}):")
                for r in issues['rejections'][-3:]:
                    print(f"    {r}")
            if not issues['errors'] and not issues['rejections']:
                print("  No issues detected")

        print(f"\n{'='*80}")
        print("Refreshing in 30s... (Ctrl+C to stop)")

        try:
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
            break

if __name__ == "__main__":
    main()
