"""
Analyze Convex backtest report(s) to find winning vs losing patterns.

Uses only existing data: backtest_convex_report_*.json (no live market connection).
Groups trades by exit_reason, entry_hour, entry_debit, hold_minutes, IV/ADX at entry,
and prints count, total P&L, avg P&L, win rate per segment.

Usage:
  python3 analyze_convex_backtest.py [path_to_report.json]
  python3 analyze_convex_backtest.py   # uses latest backtest_convex_report_*.json in cwd
"""

import json
import os
import glob
import sys
from datetime import datetime

def load_reports(path=None, merge_all=False):
    """Load backtest report JSON(s). Returns list of trade dicts and report metadata.
    If path is None, uses latest backtest_convex_report_*.json by mtime.
    If merge_all=True and path is None, merges trades from all report files (use with care)."""
    if path and os.path.isfile(path):
        with open(path, 'r') as f:
            data = json.load(f)
        trades = data.get('trades', [])
        return trades, [data], path
    # Default: use single latest backtest_convex_report_*.json in cwd
    pattern = 'backtest_convex_report_*.json'
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return [], [], None
    if merge_all:
        all_trades = []
        reports = []
        for f in files:
            with open(f, 'r') as fp:
                data = json.load(fp)
            reports.append(data)
            all_trades.extend(data.get('trades', []))
        return all_trades, reports, files[0]
    with open(files[0], 'r') as f:
        data = json.load(f)
    return data.get('trades', []), [data], files[0]

def ensure_numeric(trades):
    """Ensure exit_pnl, entry_debit, etc. are float; entry_time/exit_time handled as-is."""
    for t in trades:
        if 'exit_pnl' in t and not isinstance(t['exit_pnl'], (int, float)):
            try:
                t['exit_pnl'] = float(t['exit_pnl'])
            except (TypeError, ValueError):
                pass
        if 'entry_debit' in t and not isinstance(t['entry_debit'], (int, float)):
            try:
                t['entry_debit'] = float(t['entry_debit'])
            except (TypeError, ValueError):
                pass
        if 'hold_minutes' in t and t['hold_minutes'] is not None and not isinstance(t['hold_minutes'], (int, float)):
            try:
                t['hold_minutes'] = float(t['hold_minutes'])
            except (TypeError, ValueError):
                t['hold_minutes'] = None
        if 'iv_percentile_at_entry' in t and t['iv_percentile_at_entry'] is not None and not isinstance(t['iv_percentile_at_entry'], (int, float)):
            try:
                t['iv_percentile_at_entry'] = float(t['iv_percentile_at_entry'])
            except (TypeError, ValueError):
                t['iv_percentile_at_entry'] = None
        if 'adx_at_entry' in t and t['adx_at_entry'] is not None and not isinstance(t['adx_at_entry'], (int, float)):
            try:
                t['adx_at_entry'] = float(t['adx_at_entry'])
            except (TypeError, ValueError):
                t['adx_at_entry'] = None
    return trades

def run_analysis(trades, min_trades_for_reliable=3):
    """Group trades by key dimensions and compute P&L / win rate. Uses only built-in + optional pandas."""
    try:
        import pandas as pd
    except ImportError:
        pd = None

    if not trades:
        print("No trades to analyze.")
        return

    trades = ensure_numeric(trades)
    n = len(trades)
    total_pnl = sum(t.get('exit_pnl', 0) or 0 for t in trades)
    wins = sum(1 for t in trades if (t.get('exit_pnl') or 0) > 0)
    print("=" * 60)
    print("CONVEX BACKTEST PATTERN ANALYSIS")
    print("=" * 60)
    print(f"Total trades: {n}  |  Total P&L: ₹{total_pnl:.2f}  |  Win rate: {wins/n*100:.1f}%" if n else "No trades.")
    print()

    if pd is None:
        print("Install pandas for groupby analysis: pip install pandas")
        print("Per-trade list (first 20):")
        for i, t in enumerate(trades[:20]):
            print(f"  {i+1}. exit_reason={t.get('exit_reason')} entry_hour={t.get('entry_hour')} debit={t.get('entry_debit')} pnl={t.get('exit_pnl')}")
        return

    df = pd.DataFrame(trades)

    def pnl_summary(g):
        c = len(g)
        s = g['exit_pnl'].sum()
        m = g['exit_pnl'].mean() if c else 0
        w = (g['exit_pnl'] > 0).sum()
        wr = w / c * 100 if c else 0
        reliable = "(reliable)" if c >= min_trades_for_reliable else "(low n)"
        return pd.Series({'count': c, 'total_pnl': s, 'avg_pnl': m, 'win_rate_pct': wr, 'reliable': reliable})

    # 1. By exit_reason
    if 'exit_reason' in df.columns:
        print("--- By exit_reason ---")
        by_reason = df.groupby('exit_reason', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_reason.to_string(index=False))
        print()

    # 2. By entry_hour (if present)
    if 'entry_hour' in df.columns and df['entry_hour'].notna().any():
        print("--- By entry_hour ---")
        by_hour = df.groupby('entry_hour', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        by_hour = by_hour.sort_values('entry_hour')
        print(by_hour.to_string(index=False))
        print()

    # 3. By entry_debit bins
    if 'entry_debit' in df.columns:
        df_copy = df.copy()
        df_copy['debit_bin'] = pd.cut(
            df_copy['entry_debit'],
            bins=[0, 1000, 3000, 5000, 10000, float('inf')],
            labels=['<1k', '1k-3k', '3k-5k', '5k-10k', '>10k']
        )
        print("--- By entry_debit (bin) ---")
        by_debit = df_copy.groupby('debit_bin', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_debit.to_string(index=False))
        print()

    # 4. By hold_minutes bins (if present)
    if 'hold_minutes' in df.columns and df['hold_minutes'].notna().any():
        df_copy = df.copy()
        df_copy['hold_bin'] = pd.cut(
            df_copy['hold_minutes'],
            bins=[0, 60, 360, 1440, 4320, float('inf')],
            labels=['<1h', '1h-6h', '6h-1d', '1d-3d', '>3d']
        )
        print("--- By hold_minutes (bin) ---")
        by_hold = df_copy.groupby('hold_bin', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_hold.to_string(index=False))
        print()

    # 4b. By time-elapsed % (approx) — proxy for when exit happened vs 25%/40% time rules
    if 'hold_minutes' in df.columns and df['hold_minutes'].notna().any():
        # Assume weekly: 7 days to expiry at entry → 7*24*60 = 10080 min total life
        MINUTES_PER_WEEK = 7 * 24 * 60
        df_copy = df.copy()
        df_copy['time_elapsed_pct'] = df_copy['hold_minutes'] / MINUTES_PER_WEEK
        df_copy['time_elapsed_bin'] = pd.cut(
            df_copy['time_elapsed_pct'],
            bins=[0, 0.25, 0.40, 0.60, 1.01],
            labels=['<25% (pre-TSL time)', '25-40% (TSL, pre-hard)', '40-60%', '>60%']
        )
        print("--- By time_elapsed % (approx, weekly) ---")
        by_te = df_copy.groupby('time_elapsed_bin', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_te.to_string(index=False))
        print("  (25%% = TSL activation by time; 40%% = hard time exit when in loss.)")
        print()

    # 5. By iv_percentile_at_entry bins (if present)
    if 'iv_percentile_at_entry' in df.columns and df['iv_percentile_at_entry'].notna().any():
        df_copy = df.copy()
        df_copy['iv_bin'] = pd.cut(
            df_copy['iv_percentile_at_entry'],
            bins=[0, 50, 70, 90, 100.1],
            labels=['<50', '50-70', '70-90', '90-100']
        )
        print("--- By iv_percentile_at_entry (bin) ---")
        by_iv = df_copy.groupby('iv_bin', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_iv.to_string(index=False))
        print()

    # 6. By adx_at_entry bins (if present)
    if 'adx_at_entry' in df.columns and df['adx_at_entry'].notna().any():
        df_copy = df.copy()
        df_copy['adx_bin'] = pd.cut(
            df_copy['adx_at_entry'],
            bins=[0, 25, 35, 45, 100],
            labels=['<25', '25-35', '35-45', '>45']
        )
        print("--- By adx_at_entry (bin) ---")
        by_adx = df_copy.groupby('adx_bin', dropna=False, observed=False).apply(pnl_summary, include_groups=False).reset_index()
        print(by_adx.to_string(index=False))
        print()

    print("(Segments with count >= %d are more reliable; avoid overfitting on low-n buckets.)" % min_trades_for_reliable)
    print()

    # Winning pattern summary
    print("=" * 60)
    print("WINNING PATTERN SUMMARY")
    print("=" * 60)
    winners = [t for t in trades if (t.get('exit_pnl') or 0) > 0]
    losers = [t for t in trades if (t.get('exit_pnl') or 0) <= 0]
    if not winners:
        print("No winning trades in this report.")
        if losers:
            by_reason_l = pd.DataFrame(losers).groupby('exit_reason', dropna=False)['exit_pnl'].agg(['count', 'mean', 'sum'])
            least_bad_reason = by_reason_l['mean'].idxmax() if not by_reason_l.empty else None
            print("  Least bad exit_reason (highest avg P&L):", least_bad_reason or "N/A")
        print("  Run backtest over more dates or relax entry filters to get more trades and winners.")
    else:
        print("Winning trades: %d (avg P&L: ₹%.2f)" % (len(winners), sum(t.get('exit_pnl', 0) or 0 for t in winners) / len(winners)))
        win_reasons = {}
        win_hours = {}
        for t in winners:
            r = t.get('exit_reason') or '?'
            win_reasons[r] = win_reasons.get(r, 0) + 1
            h = t.get('entry_hour')
            if h is not None:
                win_hours[h] = win_hours.get(h, 0) + 1
        if win_reasons:
            print("  Exit reason(s) for winners:", ", ".join("%s (%d)" % (k, v) for k, v in sorted(win_reasons.items(), key=lambda x: -x[1])))
        if win_hours:
            print("  Entry hour(s) for winners:", ", ".join("%d:00 (%d)" % (h, c) for h, c in sorted(win_hours.items(), key=lambda x: -x[1])))
        debit_win = [t.get('entry_debit') for t in winners if t.get('entry_debit') is not None]
        if debit_win:
            print("  Entry debit range for winners: ₹%.0f – ₹%.0f" % (min(debit_win), max(debit_win)))
    print("=" * 60)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    trades, reports, used = load_reports(path)
    if used:
        print("Using report(s):", used if isinstance(used, str) else used[0], "\n")
    run_analysis(trades)

if __name__ == "__main__":
    main()
